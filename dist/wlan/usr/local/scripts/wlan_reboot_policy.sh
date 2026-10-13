#!/bin/bash

set -euo pipefail

tag=$(basename "$0")

SOURCE=""
IFACE=""
REASON=""
FORCE=0
EXECUTOR=0

# Internal entry point for the independent systemd executor. The whole policy
# (including MFG, uptime/count gates and the lock) runs there exactly once.
if [ "${1:-}" = "--executor" ]; then
  EXECUTOR=1
  shift
fi

usage() {
  echo "Usage: $tag --reason <text> [--source <name>] [--iface <iface>] [--force]" >&2
}

run_bounded_io() {
  local seconds="$1"
  shift
  if ! command -v timeout >/dev/null 2>&1; then
    return 125
  fi
  # These best-effort I/O helpers must end as a group at the deadline. A
  # TERM-first wrapper can exit before -k sends KILL, leaving resistant
  # grandchildren alive. Keep timeout's separate group and kill it directly.
  timeout -s KILL "$seconds" "$@"
}

io_warning() {
  # A failed diagnostic sink must not cancel the recovery or change a refusal
  # code. The fallback can also block (for example, on a full journal pipe).
  # Save the original sink on fd 3 before suppressing timeout's diagnostics.
  {
    run_bounded_io 1 /bin/bash -c 'printf "%s: %s\n" "$1" "$2" >&3' \
      _ "$tag" "$*"
  } 3>&2 2>/dev/null || true
}

log_kmsg() {
  local msg="$1"
  if [ -w /dev/kmsg ]; then
    run_bounded_io 1 /bin/bash -c 'printf "wlan-policy: %s\n" "$1" > /dev/kmsg' \
      _ "$msg" 2>/dev/null || io_warning "kernel log write failed/timed out: $msg"
  fi
  return 0
}

log_syslog() {
  local msg="$1"
  if command -v logger >/dev/null 2>&1; then
    run_bounded_io 1 logger -p local0.warning "[$tag:$LINENO] $msg" \
      2>/dev/null || io_warning "syslog write failed/timed out: $msg"
  fi
  return 0
}

log_all() {
  local msg="$1"
  log_syslog "$msg" || true
  log_kmsg "$msg" || true
}

systemd_is_running() {
  [ -d /run/systemd/system ]
}

handoff_to_systemd() {
  local script
  script=$(readlink -f "${BASH_SOURCE[0]}")
  local -a args=(--executor --source "$SOURCE" --reason "$REASON")
  [ -z "$IFACE" ] || args+=(--iface "$IFACE")
  [ "$FORCE" -ne 1 ] || args+=(--force)

  # wifi_checker/fw_watch/emergency units stop at shutdown and kill their
  # entire cgroup. A background child or nohup cannot survive that stop.
  # This separate transient service deliberately has no shutdown conflict.
  # --wait preserves refusal/failure status while the caller is still alive;
  # its death does not terminate the independently managed executor.
  # A launch failure must not run the reboot inside the caller's cgroup.
  exec systemd-run --quiet --wait --collect --service-type=exec \
    --unit="wlan-reboot-policy-$$.service" \
    --property=DefaultDependencies=no --property=TimeoutStopSec=5s \
    --expand-environment=no \
    --setenv="MIN_UPTIME_SEC=$MIN_UPTIME_SEC" \
    --setenv="REBOOT_COOLDOWN_SEC=$REBOOT_COOLDOWN_SEC" \
    --setenv="MAX_REBOOT_COUNT=$MAX_REBOOT_COUNT" \
    --setenv="STATE_DIR=$STATE_DIR" --setenv="RUN_DIR=$RUN_DIR" \
    --setenv="RESET_CAUSE_PATH=$RESET_CAUSE_PATH" \
    -- /bin/bash "$script" "${args[@]}"
}

do_reboot() {
  # 점진적 강도 순으로 reboot을 시도한다. 각 단계가 block 되어도 다음 단계로
  # 넘어갈 수 있도록 background 실행 후 짧은 대기만 한다. 최종 fallback 으로
  # sysrq-trigger 를 사용하여 kernel emergency_restart() 경로로 직접 reset.
  # (emergency_restart 는 device_shutdown() 을 건너뛰므로 moal 등 드라이버가
  #  D state 락을 잡고 있어도 HW reset 이 즉시 수행된다.)
  # 1차: graceful reboot (systemd shutdown.target → unit stop → reboot(2))
  if [ -x /sbin/reboot ]; then
    /sbin/reboot 2>/dev/null &
  elif command -v reboot >/dev/null 2>&1; then
    reboot 2>/dev/null &
  elif command -v systemctl >/dev/null 2>&1; then
    systemctl reboot 2>/dev/null &
  fi
  sleep 10

  # 2차: forced reboot (systemd/shutdown.target 우회, reboot(2) 직접 호출)
  log_all "graceful reboot did not complete in 10s, escalating to forced reboot"
  if [ -x /sbin/reboot ]; then
    /sbin/reboot -f 2>/dev/null &
  elif command -v reboot >/dev/null 2>&1; then
    reboot -f 2>/dev/null &
  elif command -v systemctl >/dev/null 2>&1; then
    systemctl reboot --force 2>/dev/null &
  fi
  sleep 5

  # 3차: sysrq emergency restart — device_shutdown() 을 건너뛰어 D state
  # 드라이버와 무관하게 HW reset 을 트리거한다. 커널 스케줄러가 살아있는
  # 한 확실한 최종 경로.
  log_all "forced reboot did not complete, escalating to sysrq-trigger"
  if [ -w /proc/sys/kernel/sysrq ]; then
    echo 1 > /proc/sys/kernel/sysrq 2>/dev/null || true
  fi
  if [ -w /proc/sysrq-trigger ]; then
    echo b > /proc/sysrq-trigger 2>/dev/null || true
  fi

  # 여기까지 도달했다면 유저스페이스 echo 도 실행 불가 상태
  # HW watchdog 이 최후의 수단 (30초 timeout).
  sleep 5
  log_all "all reboot paths exhausted; awaiting HW watchdog timeout"
  return 127
}

get_uptime_sec() {
  awk '{print int($1)}' /proc/uptime 2>/dev/null || echo 0
}

MIN_UPTIME_SEC=${MIN_UPTIME_SEC:-120}
REBOOT_COOLDOWN_SEC=${REBOOT_COOLDOWN_SEC:-300}
MAX_REBOOT_COUNT=${MAX_REBOOT_COUNT:-3}

STATE_DIR=${STATE_DIR:-/var/log/cantops}
RUN_DIR=${RUN_DIR:-/run/cantops/wlan-policy}
# opcd(wlan-opc)가 SIGTERM(시스템 종료 중)에서 읽어 장치 리셋 통지(0x0020)의
# Reset Cause로 보내는 파일. 한 줄 "0x.." (wlan-opc protocol/ids.h OPC_RESET_CAUSE_*).
# 없으면 opcd는 0x0002(SYSTEM, 원인 미상)로 통지한다. (#304)
RESET_CAUSE_PATH=${RESET_CAUSE_PATH:-/run/opc/reset_cause}

# --source/--reason → Reset Cause ID. 매핑에 없으면 빈 문자열(파일을 남기지 않음).
reset_cause_id() {
  case "$SOURCE" in
    wifi_checker)
      case "$REASON" in
        *station_dump_fault*) echo 0x11 ;;   # station dump 장애 지속
        *)                    echo 0x10 ;;   # link / fw_crash: 무선 IF 소실
                                             # (wifi_checker의 다른 새 CAUSE가 생기면 여기에 추가)
      esac ;;
    wlan_fw_watch)    echo 0x12 ;;           # 드라이버 wedge, 리로드 실패
    wifi_logger_temp) echo 0x20 ;;           # 과열 복구 재부팅
    wifi_init)        echo 0x30 ;;           # wlan_emergency_reboot: wifi_init 실패
    *)                echo "" ;;
  esac
}

while [ $# -gt 0 ]; do
  case "$1" in
    --source)
      SOURCE=${2:-}; shift 2 ;;
    --iface)
      IFACE=${2:-}; shift 2 ;;
    --reason)
      REASON=${2:-}; shift 2 ;;
    --force)
      FORCE=1; shift ;;
    -h|--help)
      usage; exit 2 ;;
    *)
      echo "Unknown arg: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if [ -z "$REASON" ]; then
  echo "Missing --reason" >&2
  usage
  exit 2
fi

if [ "$EXECUTOR" -ne 1 ] && systemd_is_running; then
  handoff_to_systemd
fi

if [[ "$REASON" == *overtemp* ]] || [[ "$SOURCE" == "wifi_logger_temp" ]]; then
  MIN_UPTIME_SEC=0
fi

# MFG 안전망: mfg_mode=1(SoT: mod_para.conf)이면 재부팅 요청을 거부한다 —
# MFG FW에서는 checker 등의 헬스체크가 오판해 재부팅을 요청할 수 있다.
# 통과 예외: 과열 보호(overtemp/wifi_logger_temp), --force, wifi_init emergency
# (--source wifi_init) — wifi_init 경로는 mfg 오탐(exit 1 루프)이 wifi_init.sh의
# mfg 성공 종료로 제거된 뒤에는 실제 드라이버 로드 실패에서만 발생하며, FW wedge
# ('get fw info failed' 류)는 재부팅(카드 파워사이클)이 유일한 자동 복구다.
# 남용은 아래 uptime/count 게이트가 계속 바운드한다.
if [ "$FORCE" -ne 1 ] && [[ "$REASON" != *overtemp* ]] && [ "$SOURCE" != "wifi_logger_temp" ] \
   && [ "$SOURCE" != "wifi_init" ]; then
  # Existing safety exceptions do not need this persistent-storage probe.
  # Other callers must not interpret a stalled/invalid guard as normal mode.
  if _mfg_mode=$(run_bounded_io 1 /bin/bash -c '
    set -o pipefail
    mod_para="cts/wifi_mod_para.conf"
    if command -v jq >/dev/null 2>&1 && [ -f /usr/local/etc/wifi_init_conf.json ]; then
      mod_para=$(jq -r '\''.global.MOD_PARA // "cts/wifi_mod_para.conf"'\'' /usr/local/etc/wifi_init_conf.json) || exit 65
      [ -n "$mod_para" ] || mod_para="cts/wifi_mod_para.conf"
    fi
    if [ ! -f "/lib/firmware/$mod_para" ]; then
      printf "0\n"
      exit 0
    fi
    if mode=$(grep -m1 '\''^[[:space:]]*mfg_mode='\'' "/lib/firmware/$mod_para" | sed '\''s/.*mfg_mode=//'\'' | tr -d " "); then
      :
    else
      status=$?
      [ "$status" -eq 1 ] || exit 65
      mode=0
    fi
    case "${mode:-0}" in
      0|1) printf "%s\n" "${mode:-0}" ;;
      *) exit 65 ;;
    esac
  ' 2>/dev/null); then
    if [ "$_mfg_mode" = "1" ]; then
      log_all "refuse: mfg_mode=1 (MFG profile) (source=${SOURCE:-n/a} iface=${IFACE:-n/a} reason=$REASON)"
      exit 12
    fi
  else
    log_all "refuse: MFG mode unavailable/invalid (source=${SOURCE:-n/a} iface=${IFACE:-n/a} reason=$REASON)"
    exit 13
  fi
fi

mkdir -p "$RUN_DIR" 2>/dev/null || true
if ! run_bounded_io 1 mkdir -p -- "$STATE_DIR" 2>/dev/null; then
  io_warning "reboot budget directory unavailable: $STATE_DIR"
  if [ "$FORCE" -ne 1 ]; then
    log_all "refuse: reboot budget directory unavailable (source=${SOURCE:-n/a} reason=$REASON)"
    exit 14
  fi
fi

lockdir="$RUN_DIR/reboot.lockdir"
if ! mkdir "$lockdir" 2>/dev/null; then
  log_all "dedupe: reboot already in progress (source=${SOURCE:-n/a} iface=${IFACE:-n/a} reason=$REASON)"
  exit 0
fi
trap 'rmdir "$lockdir" 2>/dev/null || true' EXIT

now=$(date +%s)
uptime=$(get_uptime_sec)

state_suffix="${IFACE:-global}"
state_file="$STATE_DIR/reboot_policy_${state_suffix}.state"

last_ts=0
count=0
if _budget=$(run_bounded_io 1 /bin/bash -c '
  last=0; count=0
  if [ -e "$1" ] || [ -L "$1" ]; then
    [ -f "$1" ] && [ ! -L "$1" ] || exit 65
    read -r last count extra < "$1" || exit 65
    [ -z "$extra" ] || exit 65
  fi
  if [[ "$last" =~ ^[0-9]{1,10}$ && "$count" =~ ^[0-9]{1,9}$ ]]; then
    printf "%d %d\n" "$((10#$last))" "$((10#$count))"
  else
    exit 65
  fi
' _ "$state_file" 2>/dev/null); then
  read -r last_ts count <<< "$_budget"
else
  io_warning "reboot budget unavailable/invalid: $state_file"
  if [ "$FORCE" -ne 1 ]; then
    log_all "refuse: reboot budget unavailable/invalid (source=${SOURCE:-n/a} reason=$REASON)"
    exit 14
  fi
fi

if [ "$FORCE" -ne 1 ] && [ "$uptime" -lt "$MIN_UPTIME_SEC" ]; then
  log_all "refuse: uptime ${uptime}s < ${MIN_UPTIME_SEC}s (source=${SOURCE:-n/a} iface=${IFACE:-n/a} reason=$REASON)"
  exit 10
fi

if [ "$FORCE" -ne 1 ] && [ "$last_ts" -gt 0 ] && [ $((now - last_ts)) -lt "$REBOOT_COOLDOWN_SEC" ]; then
  count=$((count + 1))
else
  count=1
fi

if ! run_bounded_io 1 /bin/bash -c '
  set -euo pipefail
  dir=$(dirname -- "$1")
  tmp=$(mktemp "$dir/.reboot-policy.XXXXXX")
  trap '\''rm -f -- "$tmp"'\'' EXIT
  printf "%s %s\n" "$2" "$3" > "$tmp"
  chmod 0600 "$tmp"
  sync "$tmp" 2>/dev/null || sync
  mv -f -- "$tmp" "$1"
  sync "$dir" 2>/dev/null || sync
' _ "$state_file" "$now" "$count" 2>/dev/null; then
  io_warning "reboot budget not persisted: $state_file"
  if [ "$FORCE" -ne 1 ]; then
    log_all "refuse: reboot budget not persisted (source=${SOURCE:-n/a} reason=$REASON)"
    exit 14
  fi
fi

if [ "$FORCE" -ne 1 ] && [ "$count" -ge "$MAX_REBOOT_COUNT" ]; then
  log_all "refuse: reboot loop detected (count=${count}/${MAX_REBOOT_COUNT}, cooldown=${REBOOT_COOLDOWN_SEC}s) (source=${SOURCE:-n/a} iface=${IFACE:-n/a} reason=$REASON)"
  exit 11
fi

log_all "reboot: approved (attempt ${count}/${MAX_REBOOT_COUNT}, cooldown=${REBOOT_COOLDOWN_SEC}s, uptime=${uptime}s) (source=${SOURCE:-n/a} iface=${IFACE:-n/a} reason=$REASON)"

# 승인된 뒤에만(거부 분기에서 stale 파일을 남기지 않도록) Reset Cause를 기록한다.
# do_reboot 가 systemd 종료를 시작하면 opcd 가 SIGTERM 에서 이 파일을 읽는다.
# 실패해도 재부팅은 진행한다 — 통지는 best-effort, 복구가 우선.
_cause=$(reset_cause_id)
if [ -n "$_cause" ]; then
  if run_bounded_io 1 /bin/bash -c \
    'mkdir -p -- "$(dirname -- "$1")" && printf "%s\n" "$2" > "$1"' \
    _ "$RESET_CAUSE_PATH" "$_cause" 2>/dev/null; then
    log_syslog "reset cause $_cause recorded for opcd ($RESET_CAUSE_PATH) (source=${SOURCE:-n/a} reason=$REASON)"
  else
    log_all "reset cause $_cause NOT recorded ($RESET_CAUSE_PATH unavailable) - opcd reports 0x0002 when cause is absent/invalid (source=${SOURCE:-n/a})"
  fi
fi

# reboot 직전 volatile journal 을 eMMC 로 동기 flush.
# do_reboot 가 2차(reboot -f)/3차(sysrq)로 escalation 되면 journald-snapshot-boundary
# 의 ExecStop 이 실행되지 않아 직전 로그가 유실되므로, 여기서 1회 스냅샷한다.
# snapshot.sh 의 flock 으로 타이머와 직렬화되며, reboot 지연 방지를 위해 timeout 을 둔다.
_snap=/usr/local/scripts/journald_snapshot.sh
run_bounded_io 10 "$_snap" 2>/dev/null \
  || log_all "pre-reboot journal snapshot failed/timed out/skipped (source=${SOURCE:-n/a} reason=$REASON)"
run_bounded_io 2 sync 2>/dev/null \
  || log_all "pre-reboot sync failed/timed out/skipped (source=${SOURCE:-n/a} reason=$REASON)"
if do_reboot; then
  exit 0
else
  rc=$?
  # 3단계 모두 실패: 시스템은 살아 있고 cause 파일만 남는다. 지워 두지 않으면 나중의
  # 운용자 재부팅이 이 옛 WLAN 원인을 통지한다(파일 없음 → opcd 0x0002).
  run_bounded_io 1 rm -f -- "$RESET_CAUSE_PATH" 2>/dev/null \
    || io_warning "stale reset cause cleanup failed/timed out: $RESET_CAUSE_PATH"
  log_all "reboot: failed (rc=$rc) (source=${SOURCE:-n/a} iface=${IFACE:-n/a} reason=$REASON)"
  exit "$rc"
fi
