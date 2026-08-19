#!/bin/bash
#
# WLAN 드라이버 wedge 감시자.
#
# 잡는 장애: FW 자동복구(in-band reset)가 최종 실패해 드라이버가
# driver_status=MTRUE 로 latch 된 상태. netdev 는 그대로 살아 있고 operstate 도
# up 이라, 기존 경로 셋이 전부 빗나간다:
#   - wifi_checker.sh 의 "netdev 소멸"(fw_crash) 분기  → netdev 가 있으니 미발화
#   - wifi_checker.sh 의 station-dump 사다리            → is_wpa_completed 가 거짓이라 미발화
#   - wifi_init.service 의 OnFailure                    → 이미 성공한 유닛이라 재실행 계기 없음
# 그래서 /proc/mwlan/wifi_status 를 직접 본다. 이 값은 보드 전역 단일 신호라
# per-iface 인 wifi_checker@ 가 아니라 독립 유닛에서 감시한다.
#
# 이 유닛은 의도적으로 PartOf=wifi_init.service 가 아니다. 복구 액션이
# `systemctl restart wifi_init.service` 라서 PartOf 면 감시자가 리로드 도중 stop 되고,
# 그러면 90초 회복 검증창이 사라진다. (쿨다운은 STATE_FILE 에 있으므로 잃지 않는다.)
# 더 중요한 이유는 재부팅 예산 네임스페이스다: 정책은 --iface 유무로 state 파일을
# 가르는데, 링크 장애용 per-iface 예산과 보드 전역 하드웨어 wedge 예산이 섞이면
# AP 부재로 소진된 카운터가 wedge 재부팅을 조용히 거부한다.

tag=$(basename "$0")

LOGGER_COMMAND_LIB="${WIFI_LOGGER_COMMAND_LIB:-/usr/local/scripts/wifi_logger_command_lib.sh}"
if [ ! -r "$LOGGER_COMMAND_LIB" ]; then
    LOGGER_COMMAND_LIB="$(dirname "$0")/wifi_logger_command_lib.sh"
fi
# shellcheck source=wifi_logger_command_lib.sh
. "$LOGGER_COMMAND_LIB"

cleanup() {
    exit 0
}
trap cleanup INT TERM

WIFI_INIT_CONF_JSON="${WIFI_INIT_CONF_JSON:-/usr/local/etc/wifi_init_conf.json}"
# 테스트에서 가짜 proc 을 주입할 수 있게 경로를 override 가능하게 둔다.
WIFI_STATUS_PROC="${WIFI_STATUS_PROC:-/proc/mwlan/wifi_status}"
ADAPTER_CONFIG_GLOB="${ADAPTER_CONFIG_GLOB:-/proc/mwlan/adapter*/config}"
STATE_FILE="${STATE_FILE:-/var/log/cantops/fw_watch.state}"
MOD_PARA="${MOD_PARA:-cts/wifi_mod_para.conf}"
POLICY_BIN="${POLICY_BIN:-/usr/local/scripts/wlan_reboot_policy.sh}"
SNAPSHOT_BIN="${SNAPSHOT_BIN:-/usr/local/scripts/journald_snapshot.sh}"

# 기본값 (JSON 부재/jq 부재/비정상 값일 때)
CHECK_INTERVAL_SEC=5
INITIAL_DELAY_SEC=60
TERM_FAULT_CNT=3          # wifi_status=11 이 연속 3틱(=15s)
ABNORMAL_FAULT_CNT=36     # 그 외 비정상 값이 연속 36틱(=180s). 0 이면 이 계층 비활성
CONFIRM_TIMEOUT_SEC=3
RELOAD_ENABLED=1
RELOAD_COOLDOWN_SEC=900   # wifi_init.service 의 StartLimitIntervalSec=600 보다 길게
VERIFY_TIMEOUT_SEC=90
VERIFY_INTERVAL_SEC=3
MAX_REBOOT_COUNT=3
REBOOT_COOLDOWN_SEC=300
MIN_UPTIME_SEC=60

load_conf() {
    command -v jq >/dev/null 2>&1 || return 0
    [ -r "$WIFI_INIT_CONF_JSON" ] || return 0
    local v k
    for k in CHECK_INTERVAL_SEC INITIAL_DELAY_SEC TERM_FAULT_CNT ABNORMAL_FAULT_CNT \
             CONFIRM_TIMEOUT_SEC RELOAD_ENABLED RELOAD_COOLDOWN_SEC VERIFY_TIMEOUT_SEC \
             VERIFY_INTERVAL_SEC MAX_REBOOT_COUNT REBOOT_COOLDOWN_SEC MIN_UPTIME_SEC; do
        v=$(jq -r ".global.fw_watch.${k} // empty" "$WIFI_INIT_CONF_JSON" 2>/dev/null)
        # 숫자만 채택 — jq 실패/빈 JSON/문자열이 산술을 0 으로 만들지 않게 한다
        [[ "$v" =~ ^[0-9]+$ ]] && printf -v "$k" '%s' "$v"
    done
}

# MFG 프로파일 판정 (SoT: mod_para.conf의 mfg_mode=). wifi_checker.sh 와 동일 규칙.
is_mfg_mode() {
    local m
    m=$(grep -m1 '^[[:space:]]*mfg_mode=' "/lib/firmware/$MOD_PARA" 2>/dev/null | sed 's/.*mfg_mode=//' | tr -d ' ')
    [ "${m:-0}" = "1" ]
}

# fork 없이 읽는다. 파일 부재/빈 값은 빈 문자열 — 0 으로 취급하지 않는 것이 중요하다
# (rmmod 중에는 MODULE_GET 실패로 exit 0 + 0바이트가 나온다).
read_wifi_status() {
    local ws=""
    [ -r "$WIFI_STATUS_PROC" ] || { printf ''; return; }
    read -r ws < "$WIFI_STATUS_PROC" 2>/dev/null || ws=""
    printf '%s' "$ws"
}

# 0 = 모든 adapter 가 Ready(오탐) / 1 = 하나 이상 non-zero 또는 timeout(wedge 확정)
# / 2 = 판정 불가(파일 없음)
#
# adapter config 는 seq_file show() 가 타임아웃 없는 FW 커맨드를 발행하므로
# 폴링 루프에서 읽지 않는다. 디바운스가 성립한 틱에서만, 반드시 bounded 로 읽는다.
# 3초 안에 못 돌아오면 그것 자체가 wedge 신호라 CONFIRMED 로 친다.
HW_STATUS_SEEN=""
confirm_hw_not_ready() {
    local out rc
    # shellcheck disable=SC2086
    out=$(logger_run_bounded "$CONFIRM_TIMEOUT_SEC" grep -h '^hardware_status=' $ADAPTER_CONFIG_GLOB 2>/dev/null)
    rc=$?
    # 값만 남긴다 — 접두사를 그대로 두면 로그가 hardware_status=hardware_status=5 가 된다
    HW_STATUS_SEEN=$(printf '%s' "$out" | sed 's/^hardware_status=//' | tr '\n' ',' | sed 's/,$//')
    if [ "$rc" -eq 124 ]; then
        HW_STATUS_SEEN="timeout"
        return 1
    fi
    [ -n "$out" ] || return 2
    printf '%s' "$out" | grep -qv '^hardware_status=0$' && return 1
    return 0
}

# mlan* 중 하나라도 존재하면 참. 특정 이름(mlan0)에 의존하지 않는다.
have_wlan_netdev() {
    local d
    for d in /sys/class/net/mlan*; do
        [ -d "$d" ] && return 0
    done
    return 1
}

reload_allowed() {
    local last now
    now=$(date +%s)
    last=$(head -n1 "$STATE_FILE" 2>/dev/null | cut -d' ' -f1)
    [[ "$last" =~ ^[0-9]+$ ]] || return 0
    [ $((now - last)) -ge "$RELOAD_COOLDOWN_SEC" ]
}

mark_reload() {
    mkdir -p "$(dirname "$STATE_FILE")" 2>/dev/null || true
    echo "$(date +%s) reload" > "$STATE_FILE" 2>/dev/null || true
}

# 리로드 후 회복 확인. /proc/mwlan 부재는 실패가 아니라 "진행 중"이다.
# ifindex 는 리로드마다 바뀌므로 절대 기준으로 쓰지 않는다.
verify_recovered() {
    local deadline ws
    deadline=$(( $(date +%s) + VERIFY_TIMEOUT_SEC ))
    while [ "$(date +%s)" -lt "$deadline" ]; do
        ws=$(read_wifi_status)
        if [ "$ws" = "0" ]; then
            # confirm_hw_not_ready 가 0 을 주면 전 adapter 가 Ready 라는 뜻이다.
            # netdev 는 이름을 박지 않고 mlan* 중 하나라도 돌아왔는지만 본다 —
            # 어느 어댑터가 먼저 뜨는지는 구성에 따라 다르다.
            if confirm_hw_not_ready && have_wlan_netdev; then
                return 0
            fi
        fi
        sleep "$VERIFY_INTERVAL_SEC"
    done
    return 1
}

request_reboot() {
    local reason="$1" rc
    MAX_REBOOT_COUNT="$MAX_REBOOT_COUNT" REBOOT_COOLDOWN_SEC="$REBOOT_COOLDOWN_SEC" MIN_UPTIME_SEC="$MIN_UPTIME_SEC" \
      "$POLICY_BIN" \
        --source wlan_fw_watch \
        --reason "wlan_fw_watch fatal: $reason"
    rc=$?
    # `if ! cmd; then rc=$?` 는 부정된 상태(항상 0)를 잡으므로 직접 $? 를 받는다.
    if [ "$rc" -ne 0 ]; then
        logger -p local0.warning "[$tag:$LINENO] reboot refused by policy (rc=$rc)"
        # rc=11(loop): 정책이 거부하면서도 state 를 먼저 쓰므로, 쿨다운보다 짧게
        # 재요청하면 count 가 영구히 래칫된다.
        if [ "$rc" -eq 11 ]; then sleep $((REBOOT_COOLDOWN_SEC + 30)); else sleep 60; fi
    fi
}

load_conf
logger -p local0.info "[$tag:$LINENO] start (interval=${CHECK_INTERVAL_SEC}s term=${TERM_FAULT_CNT} abnormal=${ABNORMAL_FAULT_CNT} reload=${RELOAD_ENABLED})"
sleep "$INITIAL_DELAY_SEC"

FAULT_CNT=0
FAULT_CLASS=""

while true; do
    if is_mfg_mode; then
        FAULT_CNT=0; FAULT_CLASS=""
        sleep 10
        continue
    fi

    ws=$(read_wifi_status)
    case "$ws" in
        ""|"0") CLASS="OK"       ;;
        "11")   CLASS="TERMINAL" ;;   # WIFI_STATUS_FW_RECOVERY_FAIL
        *)      CLASS="ABNORMAL" ;;
    esac

    if [ "$CLASS" = "OK" ]; then
        FAULT_CNT=0; FAULT_CLASS=""
        sleep "$CHECK_INTERVAL_SEC"
        continue
    fi

    if [ "$CLASS" != "$FAULT_CLASS" ]; then
        FAULT_CNT=1; FAULT_CLASS="$CLASS"
    else
        FAULT_CNT=$((FAULT_CNT + 1))
    fi

    if [ "$CLASS" = "TERMINAL" ]; then THRESH="$TERM_FAULT_CNT"; else THRESH="$ABNORMAL_FAULT_CNT"; fi
    if [ "$THRESH" -eq 0 ] || [ "$FAULT_CNT" -lt "$THRESH" ]; then
        sleep "$CHECK_INTERVAL_SEC"
        continue
    fi

    confirm_hw_not_ready
    crc=$?
    if [ "$crc" -eq 0 ]; then
        # 전 adapter 가 Ready — wifi_status 만 이상한 오탐(FW 이벤트가 임의 값을
        # 실어 보내는 경우 등). 카운터를 접고 계속 감시한다.
        logger -p local0.warning "[$tag:$LINENO] wifi_status=$ws but all adapters ready ($HW_STATUS_SEEN) — treating as false positive"
        FAULT_CNT=0; FAULT_CLASS=""
        sleep "$CHECK_INTERVAL_SEC"
        continue
    fi
    if [ "$crc" -eq 2 ]; then
        FAULT_CNT=0; FAULT_CLASS=""
        sleep "$CHECK_INTERVAL_SEC"
        continue
    fi

    logger -p local0.emerg "[$tag:$LINENO] driver wedge confirmed (wifi_status=$ws hardware_status=$HW_STATUS_SEEN class=$CLASS ticks=$FAULT_CNT)"
    [ -x "$SNAPSHOT_BIN" ] && "$SNAPSHOT_BIN" >/dev/null 2>&1
    sync

    if [ "$RELOAD_ENABLED" != "1" ]; then
        # 리로드 기능이 꺼져 있으면 감지만 보고하고 끝낸다. 설정 이름이 약속하지 않은
        # 재부팅을 여기서 하지 않는다 — 재부팅까지 원한다면 RELOAD_ENABLED=1 로 두고
        # 리로드가 실제로 실패했을 때 에스컬레이션되게 하는 것이 맞다.
        logger -p local0.emerg "[$tag:$LINENO] reload disabled by config — reporting only, no action taken"
    elif reload_allowed; then
        logger -p local0.emerg "[$tag:$LINENO] reloading driver via wifi_init.service"
        mark_reload
        # --no-block 필수: TimeoutStartSec 동안 감시 루프가 눈이 멀면 안 된다.
        systemctl --no-block restart wifi_init.service
        if verify_recovered; then
            logger -p local0.info "[$tag:$LINENO] driver recovered after reload (wifi_status=0)"
            FAULT_CNT=0; FAULT_CLASS=""
            sleep "$CHECK_INTERVAL_SEC"
            continue
        fi
        logger -p local0.emerg "[$tag:$LINENO] reload did not recover within ${VERIFY_TIMEOUT_SEC}s — escalating"
        request_reboot "driver_wedge (wifi_status=$ws hardware_status=$HW_STATUS_SEEN, reload did not recover)"
    else
        # 쿨다운 중 = 직전 리로드로도 낫지 않고 다시 wedge 라는 뜻이라 에스컬레이션한다.
        logger -p local0.emerg "[$tag:$LINENO] wedge recurred within reload cooldown — escalating"
        request_reboot "driver_wedge (wifi_status=$ws hardware_status=$HW_STATUS_SEEN, recurred within reload cooldown)"
    fi

    FAULT_CNT=0; FAULT_CLASS=""
    sleep "$CHECK_INTERVAL_SEC"
done
