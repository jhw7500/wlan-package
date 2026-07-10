#!/bin/bash

set -euo pipefail

tag=$(basename "$0")

SOURCE=""
IFACE=""
REASON=""
FORCE=0

usage() {
  echo "Usage: $tag --reason <text> [--source <name>] [--iface <iface>] [--force]" >&2
}

log_kmsg() {
  local msg="$1"
  if [ -w /dev/kmsg ]; then
    echo "wlan-policy: $msg" > /dev/kmsg
  fi
}

log_syslog() {
  local msg="$1"
  if command -v logger >/dev/null 2>&1; then
    logger -p local0.warning "[$tag] $msg"
  fi
}

log_all() {
  local msg="$1"
  log_syslog "$msg"
  log_kmsg "$msg"
}

do_reboot() {
  # 점진적 강도 순으로 reboot을 시도한다. 각 단계가 block 되어도 다음 단계로
  # 넘어갈 수 있도록 background 실행 후 짧은 대기만 한다. 최종 fallback 으로
  # sysrq-trigger 를 사용하여 kernel emergency_restart() 경로로 직접 reset.
  # (emergency_restart 는 device_shutdown() 을 건너뛰므로 moal 등 드라이버가
  #  D state 락을 잡고 있어도 HW reset 이 즉시 수행된다.)
  sync

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
_mod_para="cts/wifi_mod_para.conf"
if command -v jq >/dev/null 2>&1 && [ -f /usr/local/etc/wifi_init_conf.json ]; then
  _mod_para=$(jq -r '.global.MOD_PARA // "cts/wifi_mod_para.conf"' /usr/local/etc/wifi_init_conf.json 2>/dev/null) || _mod_para="cts/wifi_mod_para.conf"
  [ -n "$_mod_para" ] || _mod_para="cts/wifi_mod_para.conf"
fi
_mfg_mode=$(grep -m1 '^[[:space:]]*mfg_mode=' "/lib/firmware/$_mod_para" 2>/dev/null | sed 's/.*mfg_mode=//' | tr -d ' ' || echo "0")
if [ "$FORCE" -ne 1 ] && [ "${_mfg_mode:-0}" = "1" ] \
   && [[ "$REASON" != *overtemp* ]] && [ "$SOURCE" != "wifi_logger_temp" ] \
   && [ "$SOURCE" != "wifi_init" ]; then
  log_all "refuse: mfg_mode=1 (MFG profile) (source=${SOURCE:-n/a} iface=${IFACE:-n/a} reason=$REASON)"
  exit 12
fi

mkdir -p "$RUN_DIR" 2>/dev/null || true
mkdir -p "$STATE_DIR" 2>/dev/null || true

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
if [ -f "$state_file" ]; then
  read -r last_ts count < "$state_file" 2>/dev/null || true
  last_ts=${last_ts:-0}
  count=${count:-0}
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

echo "$now $count" > "$state_file" 2>/dev/null || true

if [ "$FORCE" -ne 1 ] && [ "$count" -ge "$MAX_REBOOT_COUNT" ]; then
  log_all "refuse: reboot loop detected (count=${count}/${MAX_REBOOT_COUNT}, cooldown=${REBOOT_COOLDOWN_SEC}s) (source=${SOURCE:-n/a} iface=${IFACE:-n/a} reason=$REASON)"
  exit 11
fi

log_all "reboot: approved (attempt ${count}/${MAX_REBOOT_COUNT}, cooldown=${REBOOT_COOLDOWN_SEC}s, uptime=${uptime}s) (source=${SOURCE:-n/a} iface=${IFACE:-n/a} reason=$REASON)"

# reboot 직전 volatile journal 을 eMMC 로 동기 flush.
# do_reboot 가 2차(reboot -f)/3차(sysrq)로 escalation 되면 journald-snapshot-boundary
# 의 ExecStop 이 실행되지 않아 직전 로그가 유실되므로, 여기서 1회 스냅샷한다.
# snapshot.sh 의 flock 으로 타이머와 직렬화되며, reboot 지연 방지를 위해 timeout 을 둔다.
_snap=/usr/local/scripts/journald_snapshot.sh
if command -v timeout >/dev/null 2>&1; then
  timeout 10 "$_snap" 2>/dev/null || log_all "pre-reboot journal snapshot failed/timed out (source=${SOURCE:-n/a} reason=$REASON)"
else
  "$_snap" 2>/dev/null || log_all "pre-reboot journal snapshot failed (source=${SOURCE:-n/a} reason=$REASON)"
fi
sync
if ! do_reboot; then
  rc=$?
  log_all "reboot: failed (rc=$rc) (source=${SOURCE:-n/a} iface=${IFACE:-n/a} reason=$REASON)"
  exit "$rc"
fi
