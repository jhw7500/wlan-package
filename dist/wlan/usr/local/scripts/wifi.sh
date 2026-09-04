#!/bin/bash
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
# Prefer the sibling library for package/development invocation; the installed
# `/usr/local/bin/wifi` has no sibling and therefore uses the installed copy.
if [ -r "$SCRIPT_DIR/wifi_init_config_lib.sh" ]; then
    . "$SCRIPT_DIR/wifi_init_config_lib.sh"
else
    # shellcheck source=./wifi_init_config_lib.sh
    . "/usr/local/scripts/wifi_init_config_lib.sh"
fi
# 설치 전 환경(개발/테스트)에서는 스크립트 옆의 lib로 보충.
# 타깃은 /usr/local/bin/wifi 심볼릭 링크라 SCRIPT_DIR을 1차 경로로 못 쓴다.
if ! declare -f wifi_init_mode_to_bandcfg_mask >/dev/null 2>&1; then
    # 파일 존재 시에만 source — 없으면 조용히 skip(정상), 있으면 source의
    # syntax error는 그대로 노출(2>/dev/null로 삼키지 않음).
    [ -f "$SCRIPT_DIR/wifi_init_config_lib.sh" ] && . "$SCRIPT_DIR/wifi_init_config_lib.sh"
fi
# Development/test entry points can have an older installed library even though
# this sibling script is the package under test.  Load the sibling only when
# the new live-operation primitive is absent; installed images already carry it.
if ! declare -f wifi_scan_transition_lock_acquire >/dev/null 2>&1; then
    [ -f "$SCRIPT_DIR/wifi_init_config_lib.sh" ] && . "$SCRIPT_DIR/wifi_init_config_lib.sh"
fi
# shellcheck source=./wifi_fw_config_lib.sh
if [ -r /usr/local/scripts/wifi_fw_config_lib.sh ]; then
    . /usr/local/scripts/wifi_fw_config_lib.sh
elif [ -r "$SCRIPT_DIR/wifi_fw_config_lib.sh" ]; then
    . "$SCRIPT_DIR/wifi_fw_config_lib.sh"
fi

tag=$(basename "$0")
IFACE=mlan
NUM=""
WPA_CONF_DIR="${WPA_CONF_DIR:-/etc/wpa_supplicant}"
WIFI_RUN_DIR="${WIFI_RUN_DIR:-/run/wifi}"

if [ "${1:-}" == "0" ] || [ "${1:-}" == "mlan0" ]; then
    IFACE=mlan0
    NUM=0
elif [ "${1:-}" == "1" ] || [ "${1:-}" == "mlan1" ]; then
    IFACE=mlan1
    NUM=1
elif [ "${1:-}" == "2" ] || [ "${1:-}" == "eth0" ]; then
    IFACE=eth0
    NUM=2
fi

logger -p local0.info "[$tag:$LINENO] [$IFACE] cmd : wifi $1 $2 $3 $4"
trap 'wifi_wpa_run_child sync 2>/dev/null || true' EXIT

# ----- safe file update helpers -----
sync_path_or_global() {
    # BusyBox sync builds without -f may reject a path operand.  A successful
    # global sync is an acceptable durability fallback; failure of both is not.
    sync "$1" 2>/dev/null || sync 2>/dev/null
}

safe_install_sync() {
    # $1: src(tmp), $2: dst(real)
    local src="$1" dst="$2" mode=0644 stage dst_dir
    case "$dst" in
        */wpa_supplicant-*.conf) mode=0600 ;;
    esac
    # install(1)을 dst에 직접 호출하면 unlink+copy라 power loss/동시 reader가 partial
    # 파일을 볼 수 있다. 같은 directory stage에 metadata까지 완성한 뒤 rename한다.
    stage=$(mktemp "${dst}.install.XXXXXX") || return 1
    if ! install -o root -g root -m "$mode" "$src" "$stage"; then
        rm -f "$stage"
        return 1
    fi
    if ! sync_path_or_global "$stage"; then
        rm -f "$stage"
        return 1
    fi
    if ! mv -f "$stage" "$dst"; then
        rm -f "$stage"
        return 1
    fi
    sync_path_or_global "$dst" || return 1
    dst_dir=${dst%/*}
    if [ "$dst_dir" != "$dst" ]; then
        sync_path_or_global "$dst_dir" || return 1
    fi
    return 0
}

safe_tmp_for() {
    # $1: target path
    mktemp "$1.tmp.XXXXXX"
}

stage_custom_calibration() {
    # 사용자 파일을 /lib/firmware/cts에 원자적으로 반입하고 즉시 정상본을 만든다.
    local src="$1" basename dst tmp
    if [ ! -f "$src" ]; then
        echo "Error: cal conf not found: '$src'" >&2
        return 1
    fi
    basename=$(basename "$src")
    dst="$WIFI_CTS_DIR/$basename"

    if [ ! -x "$WIFI_CAL_BACKUP_SH" ]; then
        echo "Error: calibration backup helper not found: $WIFI_CAL_BACKUP_SH" >&2
        return 1
    fi

    if [ ! "$src" -ef "$dst" ]; then
        tmp=$(safe_tmp_for "$dst") || return 1
        if [ "$(id -u)" -eq 0 ]; then
            if ! install -o root -g root -m 0644 "$src" "$tmp"; then
                rm -f "$tmp"
                echo "Error: failed to stage '$src' into $WIFI_CTS_DIR" >&2
                return 1
            fi
        else
            if ! install -m 0644 "$src" "$tmp"; then
                rm -f "$tmp"
                echo "Error: failed to stage '$src' into $WIFI_CTS_DIR" >&2
                return 1
            fi
        fi
        if ! "$WIFI_CAL_BACKUP_SH" check "$tmp"; then
            rm -f "$tmp"
            echo "Error: invalid calibration: '$src'" >&2
            return 1
        fi
        if ! mv -f "$tmp" "$dst"; then
            rm -f "$tmp"
            echo "Error: failed to stage '$src' into $WIFI_CTS_DIR" >&2
            return 1
        fi
        sync "$dst" 2>/dev/null || sync
    fi

    if ! "$WIFI_CAL_BACKUP_SH" mark "$dst"; then
        echo "Error: failed to protect custom calibration: '$dst'" >&2
        return 1
    fi
    printf 'cts/%s\n' "$basename"
}

# sed -i 대신에도 통일하고 싶으면 사용(권한 보장)
apply_sed_update() {
    local target="$1"
    shift
    local tmp
    tmp="$(safe_tmp_for "$target")"
    trap 'rm -f "$tmp"' RETURN
    sed "$@" "$target" > "$tmp"
    safe_install_sync "$tmp" "$target"
    rm -f "$tmp"
    trap - RETURN
}

# ------------------------------------

WIFI_INIT_CONF_JSON="${WIFI_INIT_CONF_JSON:-/usr/local/etc/wifi_init_conf.json}"
WIFI_CAL_BACKUP_SH="${WIFI_CAL_BACKUP_SH:-/usr/local/scripts/wifi_cal_backup.sh}"
WIFI_CTS_DIR="${WIFI_CTS_DIR:-/lib/firmware/cts}"
WIFI_LOGGER_CONTROL_SH="${WIFI_LOGGER_CONTROL_SH:-/usr/local/scripts/wifi_logger_control.sh}"

# wpa assoc 완료(wpa_state=COMPLETED) 대기 상한(초) — connect / radio-apply 공통 기본값.
# radio-apply는 인자($3)로 케이스별 override 가능하며, 미지정 시 이 값을 따른다.
ASSOC_TIMEOUT_DEFAULT="${ASSOC_TIMEOUT_DEFAULT:-15}"
# env override가 비정수/빈값/0/음수면 connect·radio-apply 폴링 산술이 깨지므로 안전 기본값으로 보정.
case "$ASSOC_TIMEOUT_DEFAULT" in ''|*[!0-9]*) ASSOC_TIMEOUT_DEFAULT=15 ;; esac
ASSOC_TIMEOUT_DEFAULT=$((10#$ASSOC_TIMEOUT_DEFAULT))   # 선행 0(00/08 등) 10진수 정규화 → 8진수 산술에러·zero 방지
[ "$ASSOC_TIMEOUT_DEFAULT" -ge 1 ] || ASSOC_TIMEOUT_DEFAULT=15

# JSON mac 설정 수정 함수 (.mac.<iface>.<key>)
update_json_mac() {
    local iface="$1"
    local key="$2"
    local value="$3"

    if [ ! -f "$WIFI_INIT_CONF_JSON" ]; then
        echo "Error: $WIFI_INIT_CONF_JSON not found"
        return 1
    fi
    if ! command -v jq >/dev/null 2>&1; then
        echo "Error: jq not installed"
        return 1
    fi

    if jq --arg v "$value" ".mac.${iface}.${key} = \$v" "$WIFI_INIT_CONF_JSON" > "${WIFI_INIT_CONF_JSON}.tmp"; then
        mv "${WIFI_INIT_CONF_JSON}.tmp" "$WIFI_INIT_CONF_JSON"
    else
        rm -f "${WIFI_INIT_CONF_JSON}.tmp"
        echo "Error: JSON update failed for ${iface}.${key}" >&2
        return 1
    fi
}

# JSON global 설정 수정 함수
update_json_global() {
    local key="$1"
    local value="$2"

    if [ ! -f "$WIFI_INIT_CONF_JSON" ]; then
        echo "Error: $WIFI_INIT_CONF_JSON not found"
        return 1
    fi

    if ! command -v jq >/dev/null 2>&1; then
        echo "Error: jq not installed"
        return 1
    fi

    if jq --arg k "$key" --arg v "$value" '.global[$k] = $v' "$WIFI_INIT_CONF_JSON" > "${WIFI_INIT_CONF_JSON}.tmp"; then
        mv "${WIFI_INIT_CONF_JSON}.tmp" "$WIFI_INIT_CONF_JSON"
    else
        rm -f "${WIFI_INIT_CONF_JSON}.tmp"
        echo "Error: JSON global update failed for ${key}" >&2
        return 1
    fi
}

update_json_iface() {
    local iface="$1"
    local key="$2"
    local value="$3"

    if [ ! -f "$WIFI_INIT_CONF_JSON" ]; then
        echo "Error: $WIFI_INIT_CONF_JSON not found"
        return 1
    fi

    if ! command -v jq >/dev/null 2>&1; then
        echo "Error: jq not installed"
        return 1
    fi

    if jq --arg i "$iface" --arg k "$key" --arg v "$value" '.[$i][$k] = $v' "$WIFI_INIT_CONF_JSON" > "${WIFI_INIT_CONF_JSON}.tmp"; then
        mv "${WIFI_INIT_CONF_JSON}.tmp" "$WIFI_INIT_CONF_JSON"
    else
        rm -f "${WIFI_INIT_CONF_JSON}.tmp"
        echo "Error: JSON iface update failed for ${iface}.${key}" >&2
        return 1
    fi
}

# JSON roaming.* 정수 설정 수정 (중첩 키 + --argjson 숫자 타입 — schema integer 유지)
update_json_roaming_int() {
    local iface="$1"
    local key="$2"
    local value="$3"

    if [ ! -f "$WIFI_INIT_CONF_JSON" ]; then
        echo "Error: $WIFI_INIT_CONF_JSON not found"
        return 1
    fi

    if ! command -v jq >/dev/null 2>&1; then
        echo "Error: jq not installed"
        return 1
    fi

    if jq --arg i "$iface" --arg k "$key" --argjson v "$value" '.[$i].roaming[$k] = $v' "$WIFI_INIT_CONF_JSON" > "${WIFI_INIT_CONF_JSON}.tmp"; then
        mv "${WIFI_INIT_CONF_JSON}.tmp" "$WIFI_INIT_CONF_JSON"
    else
        rm -f "${WIFI_INIT_CONF_JSON}.tmp"
        echo "Error: JSON roaming update failed for ${iface}.roaming.${key}" >&2
        return 1
    fi
}

# GOOD_SIGNAL_RESET_GATE 의 코드 정책 사본. enable만 JSON 설정이고 delta/grace는 고정값이다.
# tests/test_roam_goodsignal_gate.py가 Python 상수와의 drift를 차단한다.
GATE_DEF_ENABLE=true
GATE_FIXED_DELTA_DB=2
GATE_FIXED_GRACE_SEC=40

# GOOD_SIGNAL_RESET_GATE 키의 **적용 중인 값**을 출처와 함께 출력.
# JSON 에 있으면 그 값(false 포함), 없으면 `<기본값>(default)`. jq 실패는 비0 으로 전파해 호출부가
# "읽기 실패"와 "키 없음"을 구분할 수 있게 한다.
gate_effective() {
    local iface="$1"
    local key="$2"
    local fallback="$3"
    local v

    # iface/key 를 필터 문자열에 보간하지 않고 --arg 로 넘긴다 —
    # update_json_roaming_section 과 같은 방식(같은 파일 안에서 패턴이 갈리면 복사·재사용 시
    # 취약해진다). 현재 IFACE 는 mlan0/mlan1 로 제한돼 실제 인젝션 위험은 없다.
    # jq의 `a // b`는 null뿐 아니라 false도 b로 대체한다. enable=false를 "키 없음"으로
    # 오인하지 않도록 has()로 존재 여부를 먼저 판정한다.
    v=$(jq -r --arg i "$iface" --arg k "$key" \
          '(.[$i].roaming.GOOD_SIGNAL_RESET_GATE // {}) as $g |
           if ($g | has($k)) then ($g[$k] | tostring) else empty end' \
          "$WIFI_INIT_CONF_JSON") || return 1
    if [ -z "$v" ]; then
        echo "${fallback}(default)"
    else
        echo "$v"
    fi
}

# roaming 하위 **섹션 블록**의 키를 갱신(예: GOOD_SIGNAL_RESET_GATE.enable).
# update_json_roaming_int 는 `.roaming[key]` 1단계만 다루므로 중첩 블록용으로 분리했다.
# value 는 --argjson 이라 bool(true/false)·정수 모두 그대로 넣을 수 있다.
update_json_roaming_section() {
    local iface="$1"
    local section="$2"
    local key="$3"
    local value="$4"

    if [ ! -f "$WIFI_INIT_CONF_JSON" ]; then
        echo "Error: $WIFI_INIT_CONF_JSON not found" >&2
        return 1
    fi

    if ! command -v jq >/dev/null 2>&1; then
        echo "Error: jq not installed" >&2
        return 1
    fi

    # 섹션이 없을 수도 있다(구버전 config) → `// {}` 로 만들어 넣는다.
    if jq --arg i "$iface" --arg s "$section" --arg k "$key" --argjson v "$value" \
         '.[$i].roaming[$s] = ((.[$i].roaming[$s] // {}) | .[$k] = $v)' \
         "$WIFI_INIT_CONF_JSON" > "${WIFI_INIT_CONF_JSON}.tmp"; then
        mv "${WIFI_INIT_CONF_JSON}.tmp" "$WIFI_INIT_CONF_JSON"
    else
        rm -f "${WIFI_INIT_CONF_JSON}.tmp"
        echo "Error: JSON roaming update failed for ${iface}.roaming.${section}.${key}" >&2
        return 1
    fi
}

# roaming.* JSON 변경을 wifi_roam 데몬에 무재시작 반영.
# SIGHUP으로 데몬이 즉시 재읽어 반영 — 재시작 없이 데몬 상태(핑퐁 이력·backoff·cooldown)
# 보존. 폴링이 아니라 신호 트리거라 평시 비용 0. roam th / roam diff 가 공유한다.
reload_roam_daemon() {
    local iface="$1"

    if systemctl is-active --quiet "wifi_roam@${iface}" 2>/dev/null; then
        if systemctl kill --kill-who=main -s SIGHUP "wifi_roam@${iface}"; then
            echo "wifi_roam@${iface} reloaded via SIGHUP (applied, no restart)"
        else
            # SIGHUP 전달 실패(권한/구버전 systemd 등, 에러는 위에 노출) → restart 폴백으로
            # 반드시 반영(무음 미적용 방지). 재시작은 연결 무영향.
            echo "Warning: SIGHUP failed; falling back to restart" >&2
            if systemctl restart "wifi_roam@${iface}"; then
                echo "wifi_roam@${iface} restarted (applied)"
            else
                # 여기까지 오면 JSON 은 persist 됐지만 데몬은 옛 값을 계속 쓴다.
                # 조용히 성공(exit 0)으로 보고하면 자동화가 부분 적용을 감지하지 못한다.
                echo "Error: wifi_roam@${iface} restart failed — JSON persisted but the running daemon still uses the old value" >&2
                return 1
            fi
        fi
    else
        echo "wifi_roam@${iface} inactive (applies on next start)"
    fi
}

ensure_wifi_init_conf() {
    # JSON config is managed by postinst, no action needed here
    :
}

# ----- radio mode/bw helpers -----
# bandcfg 마스크 테이블은 wifi_init.sh와 공용 —
# wifi_init_config_lib.sh:wifi_init_mode_to_bandcfg_mask() 사용 (단일 정의).

# 모드 세대 비교용 랭크 (STANDARD n/ac/ax와 radio.mode 모순 감지)
mode_rank() {
    case "$1" in
        b) echo 1 ;; g) echo 2 ;; a) echo 3 ;; n) echo 4 ;; ac) echo 5 ;; ax) echo 6 ;;
        *) echo 0 ;;
    esac
}

# 5G association용 VHT cap GET ("0x........" 또는 빈 문자열)
get_vht_assoc_cap() {
    mlanutl "$1" vhtcfg 2 2 2>/dev/null | awk '/VHT Capabilities Info/ {print $NF; exit}'
}

# bw에 맞춰 htcapinfo(HT40 enable 비트) + vhtcfg(bwcfg)를 적용.
# 실기 검증(2026-06-15, 9098): 20=0x05c00000/bwcfg0, 40=0x05c20000/bwcfg0,
# 80=0x05c20000/bwcfg1 조합이 HE 링크에서 reassociate로 양방향 전환됨.
# stdout=결과 메시지, return: 0 ok / 2 invalid bw / 5 htcapinfo / 6 vhtcfg
# (6은 VHT cap GET 실패와 vhtcfg SET 실패 둘 다 포함 — 호출자 로그로 구분)
apply_bw_caps() { # $1 iface, $2 bw
    local iface="$1" bw="$2" htcap vhtbw vhtcap
    htcap=$(wifi_init_bw_to_htcap "$bw")
    vhtbw=$(wifi_init_bw_to_vhtbw "$bw")
    { [ -z "$htcap" ] || [ -z "$vhtbw" ]; } && return 2
    mlanutl "$iface" htcapinfo "$htcap" >/dev/null 2>&1 || return 5
    vhtcap=$(get_vht_assoc_cap "$iface")
    if [ -z "$vhtcap" ]; then
        # VHT cap GET 불가 + 20/40(bwcfg=0)이면 11N htcapinfo만으로 충분
        [ "$vhtbw" = "0" ] && {
            echo "htcapinfo $htcap applied (bw=$bw; VHT cap unavailable, vhtcfg skipped)"
            return 0
        }
        # bwcfg=1(80/auto/default)은 VHT cap이 필수인데 GET 실패 — 원인 명시
        echo "vhtcfg skipped: VHT cap GET failed (bw=$bw needs bwcfg=1)"
        return 6
    fi
    mlanutl "$iface" vhtcfg 2 2 "$vhtbw" "$vhtcap" >/dev/null 2>&1 || return 6
    echo "htcapinfo $htcap / vhtcfg bwcfg=$vhtbw applied (bw=$bw)"
    return 0
}

# apply_bw_caps 호출 + 결과 메시지 + 실패 시 rollback/exit를 일원화.
# mode 경로와 bw-only 경로의 중복 case 블록을 단일 정의로 통합한다.
# 성공 시 반환, 실패 시 적절한 exit 코드로 스크립트 종료(같은 셸, subshell 아님).
apply_bw_or_exit() { # $1 iface, $2 bw_cap
    local iface="$1" bw_cap="$2" msg rc
    msg=$(apply_bw_caps "$iface" "$bw_cap"); rc=$?
    [ -n "$msg" ] && echo "$msg"
    case "$rc" in
        0) return 0 ;;
        2) echo "Error: invalid radio.bw '$R_BW'" >&2; rollback_radio_live "$iface"; exit 2 ;;
        5) echo "Error: htcapinfo failed for $iface" >&2; logger -p local0.err "[$tag:$LINENO] [$iface] htcapinfo failed"; rollback_radio_live "$iface"; exit 5 ;;
        6) echo "Error: vhtcfg failed for $iface" >&2; logger -p local0.err "[$tag:$LINENO] [$iface] vhtcfg failed"; rollback_radio_live "$iface"; exit 6 ;;
        *) echo "Error: apply_bw_caps unexpected rc=$rc for $iface" >&2; rollback_radio_live "$iface"; exit 6 ;;
    esac
}

# wpa_cli는 데몬 응답이 FAIL이어도 exit 0일 수 있다. 반대로 transport/wrapper가
# stdout에 OK를 남기고 nonzero로 끝나는 경우도 실패이므로 rc와 reply를 모두 확인한다.
wpa_cli_ok() {
    local iface="$1" reply
    shift
    if ! reply=$(wifi_wpa_child_exec wpa_cli -i "$iface" "$@" 2>/dev/null); then
        return 1
    fi
    [ "$reply" = "OK" ]
}

# service start/restart 뒤 process active만으로 association 완료를 주장하지 않는다.
# 새 supplicant 인스턴스의 COMPLETED를 bounded polling하고 그동안 parent가 FD7을 유지한다.
wifi_wpa_wait_completed_under_transition() {
    local iface="$1" polls reply line state="" i
    polls=$((ASSOC_TIMEOUT_DEFAULT * 10))
    for ((i = 1; i <= polls; i++)); do
        reply=$(wifi_wpa_child_exec wpa_cli -i "$iface" status 2>/dev/null) || reply=""
        state=""
        while IFS= read -r line; do
            case "$line" in wpa_state=*) state=${line#wpa_state=} ;; esac
        done <<< "$reply"
        [ "$state" = COMPLETED ] && return 0
        [ "$i" -lt "$polls" ] && wifi_wpa_run_child sleep 0.1
    done
    return 1
}

# Store a live process's /proc start-time token in the named caller variable.
# This liveness path is shell-builtin only: the SIGKILL watchdog must never
# wait behind a schedulable `cat`/`tr` child before it can enforce its cleanup
# bound.  Field parsing starts after the final ") " to tolerate spaces in the
# kernel comm field.
connect_monitor_proc_start_into() {
    local output_name="$1" pid="$2" stat rest
    printf -v "$output_name" '%s' ""
    case "$pid" in ''|*[!0-9]*) return 1 ;; esac
    [ "$pid" -gt 1 ] || return 1
    if [ "${CONNECT_MONITOR_PROC_LOCAL:-1}" != "1" ]; then
        # Foreign /proc (see connect_monitor_proc_is_local): a start token read
        # here would describe an unrelated host process, so the reuse guard it
        # normally provides is unobtainable and must not be faked.  Fall back to
        # liveness.  `kill` resolves in this namespace, so both the probe and any
        # later signal stay confined to processes this namespace owns.
        kill -0 "$pid" 2>/dev/null || return 1
        printf -v "$output_name" '%s' "ns"
        return 0
    fi
    [ -r "/proc/$pid/stat" ] || return 1
    IFS= read -r stat < "/proc/$pid/stat" 2>/dev/null || return 1
    rest=${stat##*) }
    set -- $rest
    [ $# -ge 20 ] && [ "${1:-}" != "Z" ] || return 1
    printf -v "$output_name" '%s' "${20}"
}

# vsftpd runs ftpcmd handlers inside a private PID namespace while /proc stays
# the host's, so `$$` and the PID /proc reports for this very shell disagree.
# Read it with a redirection on a builtin: a command substitution would report
# its own child and make every run look foreign.
connect_monitor_proc_is_local() {
    local stat
    [ -r /proc/self/stat ] || return 1
    IFS= read -r stat < /proc/self/stat 2>/dev/null || return 1
    [ "${stat%% *}" = "$$" ]
}

connect_monitor_pid_matches() {
    local pid="$1" expected="$2" current
    [ -n "$expected" ] || return 1
    connect_monitor_proc_start_into current "$pid" || return 1
    [ "$current" = "$expected" ]
}

connect_monitor_pid_is_wpa_cli() {
    local pid="$1" arg
    case "$pid" in ''|*[!0-9]*) return 1 ;; esac
    [ "$pid" -gt 1 ] || return 1
    if [ "${CONNECT_MONITOR_PROC_LOCAL:-1}" != "1" ]; then
        # Foreign /proc: cmdline would belong to a host process.  Recovering the
        # PID from our own mode-0700 directory plus liveness is all that remains.
        kill -0 "$pid" 2>/dev/null
        return
    fi
    [ -r "/proc/$pid/cmdline" ] || return 1
    while IFS= read -r -d '' arg; do
        case "$arg" in wpa_cli|*/wpa_cli) return 0 ;; esac
    done < "/proc/$pid/cmdline" 2>/dev/null
    return 1
}

connect_event_monitor_cleanup() {
    local pid start watchdog_pid watchdog_start _i
    watchdog_pid="${CONNECT_MONITOR_WATCHDOG_PID:-}"
    watchdog_start="${CONNECT_MONITOR_WATCHDOG_START:-}"
    if connect_monitor_pid_matches "$watchdog_pid" "$watchdog_start"; then
        kill -TERM "$watchdog_pid" 2>/dev/null || true
    fi
    if [ -n "$watchdog_pid" ]; then
        wait "$watchdog_pid" 2>/dev/null || true
    fi

    pid="${CONNECT_MONITOR_PID:-}"
    start="${CONNECT_MONITOR_START:-}"
    # A signal can arrive after wpa_cli writes its private pidfile but before
    # start() records the identity.  Recover only a live wpa_cli identity from
    # our mode-0700 directory; arbitrary/stale numeric PIDs remain unsignalled.
    if [ -z "$start" ] && [ -n "${CONNECT_MONITOR_DIR:-}" ]; then
        pid=""
        if [ -r "$CONNECT_MONITOR_DIR/wpa_cli.pid" ]; then
            IFS= read -r pid < "$CONNECT_MONITOR_DIR/wpa_cli.pid" || pid=""
        fi
        if connect_monitor_pid_is_wpa_cli "$pid"; then
            connect_monitor_proc_start_into start "$pid" 2>/dev/null || start=""
        fi
    fi
    if connect_monitor_pid_matches "$pid" "$start"; then
        kill -TERM "$pid" 2>/dev/null || true
        for _i in 1 2 3 4 5 6 7 8 9 10; do
            connect_monitor_pid_matches "$pid" "$start" || break
            wifi_wpa_run_child sleep 0.1
        done
        if connect_monitor_pid_matches "$pid" "$start"; then
            kill -KILL "$pid" 2>/dev/null || true
        fi
    fi
    [ -z "${CONNECT_MONITOR_DIR:-}" ] \
        || wifi_wpa_run_child rm -rf -- "$CONNECT_MONITOR_DIR"
    CONNECT_MONITOR_DIR=""
    CONNECT_MONITOR_PID=""
    CONNECT_MONITOR_START=""
    CONNECT_MONITOR_WATCHDOG_PID=""
    CONNECT_MONITOR_WATCHDOG_START=""
}

connect_event_monitor_start() { # $1 iface, $2 association timeout seconds
    local iface="$1" timeout="$2" action pidfile pid start parent_pid parent_start
    local watchdog_ticks watchdog_pid watchdog_start _i

    # Decide once, before any identity is pinned, whether /proc describes this
    # process tree.  The watchdog subshell inherits the answer.
    if connect_monitor_proc_is_local; then
        CONNECT_MONITOR_PROC_LOCAL=1
    else
        CONNECT_MONITOR_PROC_LOCAL=0
    fi
    wifi_wpa_run_child mkdir -p "$WIFI_RUN_DIR" 2>/dev/null || return 1
    CONNECT_MONITOR_DIR=$(wifi_wpa_child_exec mktemp -d "$WIFI_RUN_DIR/${iface}.connect-monitor.XXXXXX") \
        || return 1
    wifi_wpa_run_child chmod 0700 "$CONNECT_MONITOR_DIR" 2>/dev/null || {
        connect_event_monitor_cleanup
        return 1
    }
    action="$CONNECT_MONITOR_DIR/action.sh"
    pidfile="$CONNECT_MONITOR_DIR/wpa_cli.pid"
    wifi_wpa_run_child cat > "$action" <<'EOF'
#!/bin/sh
exec 7>&- 9>&-
[ "${2:-}" = "CONNECTED" ] || exit 0
monitor_dir=${0%/*}
[ -f "$monitor_dir/armed" ] || exit 0
case "${WPA_ID:-}" in ''|*[!0-9]*) exit 0 ;; esac
tmp="$monitor_dir/connected-id.tmp.$$"
(umask 077; printf '%s\n' "$WPA_ID" > "$tmp") || exit 1
mv -f "$tmp" "$monitor_dir/connected-id"
EOF
    wifi_wpa_run_child chmod 0700 "$action" 2>/dev/null || {
        connect_event_monitor_cleanup
        return 1
    }

    # EXIT/signal traps cannot run after SIGKILL.  Start the watchdog before
    # daemon attachment so even the small pidfile/setup window is covered.
    # It recovers only a live wpa_cli from our private directory, then pins its
    # /proc start token before sending any signal.
    parent_pid=$$
    connect_monitor_proc_start_into parent_start "$parent_pid" || {
        connect_event_monitor_cleanup
        return 1
    }
    watchdog_ticks=$((timeout * 10 + 50))
    (
        exec 7>&- 9>&-
        # Survive shell/job-control hangup propagation if the owning command
        # is killed before its EXIT trap can stop this watchdog.
        trap '' HUP
        for ((_i = 1; _i <= watchdog_ticks; _i++)); do
            [ -d "$CONNECT_MONITOR_DIR" ] || exit 0
            connect_monitor_pid_matches "$parent_pid" "$parent_start" || break
            wifi_wpa_run_child sleep 0.1
        done
        [ -d "$CONNECT_MONITOR_DIR" ] || exit 0
        pid=""; start=""
        for _i in 1 2 3 4 5 6 7 8 9 10; do
            pid=""
            if [ -r "$pidfile" ]; then
                IFS= read -r pid < "$pidfile" || pid=""
            fi
            if connect_monitor_pid_is_wpa_cli "$pid"; then
                connect_monitor_proc_start_into start "$pid" 2>/dev/null || start=""
                [ -n "$start" ] && break
            fi
            wifi_wpa_run_child sleep 0.1
        done
        if connect_monitor_pid_matches "$pid" "$start"; then
            kill -TERM "$pid" 2>/dev/null || true
            # The parent is already gone (normally SIGKILL), so there is no
            # caller left to benefit from a long graceful-daemon wait.  Bound
            # orphan cleanup tightly, then force release of private resources.
            for _i in 1 2 3; do
                connect_monitor_pid_matches "$pid" "$start" || break
                wifi_wpa_run_child sleep 0.1
            done
            if connect_monitor_pid_matches "$pid" "$start"; then
                kill -KILL "$pid" 2>/dev/null || true
            fi
        fi
        wifi_wpa_run_child rm -rf -- "$CONNECT_MONITOR_DIR"
    ) >/dev/null 2>&1 &
    watchdog_pid=$!
    CONNECT_MONITOR_WATCHDOG_PID="$watchdog_pid"
    connect_monitor_proc_start_into watchdog_start "$watchdog_pid" || {
        kill -TERM "$watchdog_pid" 2>/dev/null || true
        wait "$watchdog_pid" 2>/dev/null || true
        connect_event_monitor_cleanup
        return 1
    }
    CONNECT_MONITOR_WATCHDOG_START="$watchdog_start"

    # Daemon mode's stdout is not part of the request/reply protocol; its rc
    # plus a live private pidfile prove successful attachment.
    if ! wifi_wpa_run_child wpa_cli -i "$iface" -a "$action" -B -P "$pidfile" \
        >/dev/null 2>&1; then
        connect_event_monitor_cleanup
        return 1
    fi
    pid=""
    for _i in 1 2 3 4 5 6 7 8 9 10; do
        pid=""
        if [ -r "$pidfile" ]; then
            IFS= read -r pid < "$pidfile" || pid=""
        fi
        connect_monitor_proc_start_into start "$pid" 2>/dev/null || start=""
        [ -n "$start" ] && break
        wifi_wpa_run_child sleep 0.1
    done
    if [ -z "${start:-}" ]; then
        connect_event_monitor_cleanup
        return 1
    fi
    CONNECT_MONITOR_PID="$pid"
    CONNECT_MONITOR_START="$start"
    return 0
}

connect_event_monitor_arm() {
    [ -n "${CONNECT_MONITOR_DIR:-}" ] || return 1
    wifi_wpa_run_child rm -f "$CONNECT_MONITOR_DIR/connected-id" \
        "$CONNECT_MONITOR_DIR"/connected-id.tmp.*
    : > "$CONNECT_MONITOR_DIR/armed"
}

# Read fresh event evidence before status so an accepted COMPLETED snapshot is
# necessarily subsequent to the CONNECTED epoch.  The connect command sets the
# target globals before invoking this helper.
connect_association_poll_matches() {
    if ! FRESH_EVENT_ID=$(wifi_wpa_child_exec cat "$CONNECT_MONITOR_DIR/connected-id" 2>/dev/null); then
        FRESH_EVENT_ID=""
    fi
    WPA_STATUS=$(wifi_wpa_child_exec wpa_cli -i "$IFACE" status 2>/dev/null) || WPA_STATUS=""
    WPA_STATE=""; CUR_SSID=""; CUR_FREQ=""; CUR_ID=""; ASSOC_MATCH=0
    while IFS='=' read -r _key _value; do
        case "$_key" in
            wpa_state) WPA_STATE="$_value" ;;
            ssid)      CUR_SSID="$_value" ;;
            freq)      CUR_FREQ="$_value" ;;
            id)        CUR_ID="$_value" ;;
        esac
    done <<< "$WPA_STATUS"

    if [ "$WPA_STATE" = "COMPLETED" ]; then
        if [ "$HAS_TARGET_ID" = "1" ]; then
            [ "$FRESH_EVENT_ID" = "$TARGET_ID" ] \
                && [ "$CUR_ID" = "$TARGET_ID" ] && ASSOC_MATCH=1
        elif [ "$HAS_TARGET" = "0" ]; then
            [[ "$FRESH_EVENT_ID" =~ ^[0-9]+$ ]] \
                && [ "$CUR_ID" = "$FRESH_EVENT_ID" ] && ASSOC_MATCH=1
        elif [ "$CUR_SSID" = "$TARGET_SSID_WPA_TEXT" ]; then
            if [[ "$FRESH_EVENT_ID" =~ ^[0-9]+$ ]] \
               && [ "$FRESH_EVENT_ID" = "$CUR_ID" ]; then
                if [ "$SET_FREQ" = "0" ]; then
                    ASSOC_MATCH=1
                else
                    case " $FREQ_STR " in
                        *" $CUR_FREQ "*) ASSOC_MATCH=1 ;;
                    esac
                fi
            fi
        fi
    fi
    [ "$ASSOC_MATCH" = "1" ]
}

# ----- radio staged-apply helpers -----
# mode/bw는 /tmp(휘발)에 stage하고 radio-apply 성공 시에만 JSON에 commit한다.
# 미커밋 stage는 재부팅 시 자동 소멸 (mcstier 마커와 동일한 /tmp 관례) —
# 검증 안 된 설정이 부팅 재적용 경로에 들어가 기기가 고립되는 것을 방지.
radio_pending_path() {
    echo "/tmp/.radio_pending_${1}.${2}"
}

radio_stage_set() { # $1 iface, $2 key, $3 value
    local f
    f="$(radio_pending_path "$1" "$2")"
    printf '%s\n' "$3" > "${f}.tmp" && mv "${f}.tmp" "$f"
}

radio_stage_get() { # $1 iface, $2 key → stdout (없으면 빈값)
    cat "$(radio_pending_path "$1" "$2")" 2>/dev/null
}

# radio-apply 실패 시 적용 전 스냅샷으로 라이브 설정 복원 + 재연결 (best-effort).
# SNAP_* 전역은 radio-apply가 disconnect 전에 GET으로 채운다.
# 스냅샷이 비었거나 복원 호출이 실패하면 부분 롤백 — logger로 추적 가능하게 남긴다.
# 이 경로는 HT/VHT cap(htcapinfo/vhtcfg)만 변경하므로 HE cap(user_he_cap) 스냅샷은
# 의도적으로 없음. 향후 11axcfg로 user_he_cap을 직접 SET하는 코드가 추가되면
# 여기에 HE cap 스냅샷/복원도 추가할 것.
rollback_radio_live() { # $1 iface
    logger -p local0.warning "[$tag:$LINENO] [$1] radio-apply: rolling back to pre-apply settings"
    echo "rolling back to pre-apply settings..." >&2
    if [ -n "${SNAP_BAND:-}" ]; then
        mlanutl "$1" bandcfg "$SNAP_BAND" >/dev/null 2>&1 || \
            logger -p local0.warning "[$tag:$LINENO] [$1] rollback: bandcfg $SNAP_BAND restore failed"
    else
        logger -p local0.warning "[$tag:$LINENO] [$1] rollback: SNAP_BAND empty — bandcfg not restored"
    fi
    if [ -n "${SNAP_HT_BG:-}" ] && [ -n "${SNAP_HT_A:-}" ]; then
        # A && B || C: BG 복원(A) 실패 시 B 건너뛰고 C(logger), 또는 A 성공+B 실패 시 C.
        # 즉 두 밴드 중 하나라도 복원 실패하면 경고 — 의도된 동작.
        mlanutl "$1" htcapinfo "$SNAP_HT_BG" 1 >/dev/null 2>&1 && \
        mlanutl "$1" htcapinfo "$SNAP_HT_A" 2 >/dev/null 2>&1 || \
            logger -p local0.warning "[$tag:$LINENO] [$1] rollback: htcapinfo restore failed"
    else
        logger -p local0.warning "[$tag:$LINENO] [$1] rollback: htcapinfo snapshot incomplete (BG=${SNAP_HT_BG:-none} A=${SNAP_HT_A:-none}) — not restored"
    fi
    if [ -n "${SNAP_VHTBW:-}" ] && [ -n "${SNAP_VHTCAP:-}" ]; then
        mlanutl "$1" vhtcfg 2 2 "$SNAP_VHTBW" "$SNAP_VHTCAP" >/dev/null 2>&1 || \
            logger -p local0.warning "[$tag:$LINENO] [$1] rollback: vhtcfg restore failed"
    else
        logger -p local0.warning "[$tag:$LINENO] [$1] rollback: vht snapshot incomplete — vhtcfg not restored"
    fi
    # reassociate가 reconnect보다 부드럽고 cap 변경 반영이 확실 (실기 검증)
    wpa_cli -i "$1" reassociate >/dev/null 2>&1 || wpa_cli -i "$1" reconnect >/dev/null 2>&1
}
# ------------------------------------

# .{iface}.radio.{mode,bw}를 단일 jq 호출로 원자 commit (빈 인자는 생략).
# 두 키를 별도 호출로 쓰면 첫 키만 기록되고 둘째가 실패하는 부분 커밋
# 윈도우가 생기므로 한 번에 기록한다.
update_json_radio() {
    local iface="$1" mode="$2" bw="$3" tmp filter
    [ -z "$mode" ] && [ -z "$bw" ] && return 0
    if [ ! -f "$WIFI_INIT_CONF_JSON" ]; then
        echo "Error: $WIFI_INIT_CONF_JSON not found" >&2
        return 1
    fi
    if ! command -v jq >/dev/null 2>&1; then
        echo "Error: jq not installed" >&2
        return 1
    fi
    filter="."
    [ -n "$mode" ] && filter="$filter | .[\$i].radio.mode = \$m"
    if [ "$bw" = "__del__" ]; then
        filter="$filter | del(.[\$i].radio.bw)"   # bw default → committed 제거(AP 따라)
    elif [ -n "$bw" ]; then
        filter="$filter | .[\$i].radio.bw = \$b"
    fi
    # 고정 .tmp 대신 고유 tmp — 동시 호출(wifi 0 ... + wifi 1 ...) 시 파손 방지.
    # EXIT trap은 22행의 글로벌 sync trap을 덮어쓰므로 RETURN trap 사용
    # (apply_sed_update와 동일 패턴).
    tmp="$(safe_tmp_for "$WIFI_INIT_CONF_JSON")"
    trap 'rm -f "$tmp"' RETURN
    if jq --arg i "$iface" --arg m "$mode" --arg b "$bw" "$filter" \
        "$WIFI_INIT_CONF_JSON" > "$tmp"; then
        chmod 0644 "$tmp" 2>/dev/null
        mv "$tmp" "$WIFI_INIT_CONF_JSON"
        sync "$WIFI_INIT_CONF_JSON" 2>/dev/null || sync
        trap - RETURN
    else
        rm -f "$tmp"
        trap - RETURN
        echo "Error: JSON radio commit failed for ${iface} (mode=${mode:-keep} bw=${bw:-keep})" >&2
        return 1
    fi
}
# ------------------------------------

usage() {
    echo "Usage: wifi {0|1|2|mlan0|mlan1|eth0} {start|up|stop|down|restart|status} : runtime"
    echo "         start/up: active=no-op (scan/association unchanged, exit 0); inactive=quiesce+start+COMPLETED (6=busy 7=start 8=assoc-timeout)"
    echo "       wifi {0|1|2|mlan0|mlan1|eth0} info : show current configuration and status"
    echo "       wifi {0|1|2|mlan0|mlan1|eth0} ip {address/netmask|0} : persist (0=Address 삭제, netmask 생략 시 /24)"
    echo "       wifi net apply [iface] : runtime — .network 반영. iface 지정 시 해당 링크만 reconfigure(나머지 안 끊김); systemd<244면 전체 재시작 폴백"
    echo "       wifi net restart : runtime (systemctl restart systemd-networkd — 전체 networkd 관리 인터페이스 일시 중단; 실패 시 exit 1)"
    echo "       wifi net status [iface] : persist(.network) vs runtime 주소/게이트웨이 대조 (읽기 전용)"
    echo "       wifi ip apply : deprecated → wifi net restart 와 동일 (별칭 유지)"
    echo "       wifi {0|1|2|mlan0|mlan1|eth0} gt {address|0} : persist (0=Gateway 삭제, netmask 없는 순수 주소)"
    echo "       wifi {0|1|2|mlan0|mlan1|eth0} mac {0|1|base|target} {mac_address} : persist"
    echo "       wifi {0|1|mlan0|mlan1} br {up|down|start|stop|restart} : runtime"
    echo "       wifi {0|1|mlan0|mlan1} br status : peer_route/hairpin/브릿지 설정·런타임·정합성 진단 (읽기 전용)"
    echo "       wifi {0|1|mlan0|mlan1} br profile {hairpin|dual|peer-route|eth0-ip} [apply] : 연계 설정 묶음 조회/적용 (dry-run 기본)"
    echo "       wifi {0|1|mlan0|mlan1} br route {find [<subnet>]|set <ip>|auto [<subnet>]} : eth 지연연결 후 peer host route 탐색/등록"
    echo "       wifi {0|1|mlan0|mlan1} br {moal|pcap|tpacket} : wbridge engine+bridge_iface persist"
    echo "       wifi {0|1|mlan0|mlan1} txpwr {0|1|2|3|no|default|low|org|custom_file_name} : persist+runtime"
    echo "       wifi {0|1|mlan0|mlan1} config {conf} {value} : persist"
    echo "       wifi {0|1|mlan0|mlan1} spoof {0|1|dynamic|static} : persist"
    echo "       wifi {0|1|mlan0|mlan1} standard {n|ac|ax|4|5|6} : persist (mlan1은 ax 불가)"
    echo "       wifi {0|1|mlan0|mlan1} cal {0|1|2|none|WlanCalData_ext.conf|*} : persist (인터페이스별)"
    echo "       wifi {0|1|mlan0|mlan1} log {cp [dir]|compress} : 로그 복사/압축(현재 디렉터리)"
    echo "       wifi {0|1|mlan0|mlan1} log extract <start> <end> [dir] : 시간 범위 로그 추출(HH:MM[:SS]는 당일, 전체 날짜·시간은 따옴표 사용)"
    echo "       wifi {0|1|mlan0|mlan1} log reset : iface 로그 + AP별 MAC 로그 truncate (rsyslog HUP)"
    echo "       wifi {0|1|mlan0|mlan1} log reset mac : AP별 MAC 로그만 truncate"
    echo "       wifi {0|1|mlan0|mlan1} ssid {id} : persist"
    echo "       wifi {0|1|mlan0|mlan1} psk {password} : persist"
    echo "       wifi {0|1|mlan0|mlan1} key {0|1|NONE|WPA-PSK|SAE|OWE|FT-PSK|WPA-EAP|...} : persist (wpa_supplicant 인식 토큰만; 공백구분 다중 지정 가능)"
    echo "       wifi {0|1|mlan0|mlan1} freq {freq_list|channel_list} : persist"
    echo "       wifi {0|1|mlan0|mlan1} connect [ssid] [freq_list|channel_list] : ssid+전역/블록 freq_list 변경 후 reconfigure 적용(재연결); 인자 없으면 현재 설정으로 재연결(reassociate; 모드 A/다중 블록은 enable된 network 중 supplicant 선택)"
    echo "       wifi {0|1|mlan0|mlan1} scan {freq_list|channel_list|2G|5G} : runtime"
    echo "       wifi {0|1|mlan0|mlan1} mscan {get|channel_list|2G|5G} : runtime (setuserscan/getscantable)"
    echo "       wifi {0|1|mlan0|mlan1} roam [0|1..N] : same-SSID BSS 전환 (0=auto best, N=Nth AP, RSSI order)"
    echo "       wifi {0|1|mlan0|mlan1} roam th [2G|5G] [rssi] : 로밍 RSSI 임계값 표시/설정 (persist, SIGHUP 무재시작 반영)"
    echo "       wifi {0|1|mlan0|mlan1} roam diff [dB] : 후보 AP 최소 RSSI 이득 표시/설정 (persist, SIGHUP 무재시작 반영)"
    echo "       wifi {0|1|mlan0|mlan1} roam gate [on|off] : good-signal 리셋 게이트 표시/설정 (delta=2dB, grace=40s 고정)"
    echo "       wifi {0|1|mlan0|mlan1} stat reset [mac] : reset stat records (all or specific MAC)"
    echo "       wifi {0|1|mlan0|mlan1} stat interval {seconds} : set stat reset interval (persist)"
    echo "       wifi {0|1|mlan0|mlan1} mon [c|compact] [interval] [--summary-lines N] [--roam-display N]"
    echo "       wifi {0|1|mlan0|mlan1} mcs [on|off|reset|ht <7|15> vht <7|8|9> he <7|9|11>] : persist+runtime"
    echo "       wifi {0|1|mlan0|mlan1} rate [<mode> <low> <high> <interval_ms>] : configured/live 조회 또는 다음 부팅값 persist"
    echo "       wifi {0|1|mlan0|mlan1} mode [b|g|a|n|ac|ax] : stage (radio-apply 성공 시 persist, mlan1은 ax 불가)"
    echo "       wifi {0|1|mlan0|mlan1} bw [20|40|80|auto|default] : stage (radio-apply 성공 시 persist; default=AP 따라)"
    echo "       wifi {0|1|mlan0|mlan1} radio-apply [timeout_s] : runtime (적용 성공 시 commit, 실패 시 라이브 롤백+재연결)"
    echo "         radio-apply exit: 0=ok 2=usage 3=env 4=bandcfg 5=htcapinfo 6=vhtcfg 7=wpa_cli 8=assoc-timeout 9=json/commit 11=b/g+5G-freq"
    echo "       wifi {0|1|mlan0|mlan1} radio-discard : staged mode/bw 변경 취소"
    echo "       wifi txpwr {0|1|2|3|no|default|low|org|conf_file_name} : persist"
    echo "       wifi cal {0|1|2|None|WlanCalData_ext.conf|WlanCalData_ext_RD.conf|*} : persist"
    echo "       wifi mfg {0|1|off|on} : persist"
    echo "       wifi ant {0|1|internal|external} : runtime"
    echo "       wifi set {fem|azure} : apply preset configuration profile"
    echo "       wifi stand {n|ac|ax|4|5|6} : persist"
    echo "       wifi log dump : /var/log/cantops 전체 압축(현재 디렉터리)"
    echo "       wifi log reset : 전체 로그 초기화(전역+모든 iface truncate + rsyslog HUP)"
    echo "       wifi log system {start|stop|restart|status|enable|disable} : 시스템 로거 그룹 제어"
    echo "       wifi {0|1|2|mlan0|mlan1|eth0} log {start|stop|restart|status|enable|disable} : iface 로거 그룹 제어"
    echo "       wifi backup : 로그·설정 백업(/var/log/cantops/backup)"
    exit 1
}

# === 입력 검증 헬퍼 ===
# persist 계열 커맨드가 오타/범위오류 값을 conf에 그대로 쓰는 것을 막는 공용 어휘.
# 새 가드는 정규식을 재정의하지 말고 여기를 호출한다. 반환 0=유효.

# wpa_supplicant는 SSID/passphrase를 바이트 길이로 검사한다(os_strlen).
# ${#var}는 UTF-8 로케일에서 문자 수를 세어 바이트 수와 어긋나므로(한글 1자=3바이트)
# LC_ALL=C로 고정해 바이트로 센다. ssh가 LANG을 forward하면 같은 명령이 로케일에
# 따라 다르게 동작하는 것을 막는다.
byte_len() {
    local LC_ALL=C
    printf '%s' "${#1}"
}

is_valid_ipv4() {
    local ip="$1" o
    local -a octets
    [[ "$ip" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || return 1
    IFS='.' read -r -a octets <<< "$ip"
    for o in "${octets[@]}"; do
        # leading zero(010.011.1.1)를 거부한다 — systemd는 inet_pton으로 파싱하고
        # inet_pton은 leading zero octet을 거부하므로, 통과시키면 networkd가 그
        # 줄을 조용히 버려서 가드가 막으려던 바로 그 상태가 된다.
        [[ "$o" =~ ^(0|[1-9][0-9]{0,2})$ ]] || return 1
        [ "$o" -le 255 ] || return 1
    done
    return 0
}

# A.B.C.D/0-32 형식. Address=는 prefix 필수(호출부에서 기본 /24를 붙인다).
is_valid_ipv4_cidr() {
    local v="$1" addr prefix
    case "$v" in
        */*/*) return 1 ;;
        */*)   addr="${v%/*}"; prefix="${v#*/}" ;;
        *)     return 1 ;;
    esac
    is_valid_ipv4 "$addr" || return 1
    # prefix도 동일하게 leading zero 거부(/08) — 주소부와 규칙을 맞춘다.
    [[ "$prefix" =~ ^(0|[1-9][0-9]?)$ ]] || return 1
    [ "$prefix" -le 32 ] || return 1
    return 0
}

is_valid_mac() {
    local mac="${1,,}" first last_nibble
    [[ "$mac" =~ ^([0-9a-f]{2}:){5}[0-9a-f]{2}$ ]] || return 1
    [ "$mac" != "00:00:00:00:00:00" ] || return 1
    [ "$mac" != "ff:ff:ff:ff:ff:ff" ] || return 1
    first="${mac%%:*}"
    last_nibble="${first:1:1}"
    case "$last_nibble" in
      0|2|4|6|8|a|c|e) return 0 ;;
      *) return 1 ;;
    esac
}

# iface 셀렉터(0|1|2|mlan0|mlan1|eth0) → 커널 iface 이름.
# 최상단 $1 해석(17-26행)과 같은 매핑이지만, net 커맨드는 iface가 $3에 오므로
# 위치에 묶이지 않은 해석기가 따로 필요하다. 알 수 없으면 출력 없이 rc=1.
resolve_iface() {
    case "$1" in
        0|mlan0) echo mlan0 ;;
        1|mlan1) echo mlan1 ;;
        2|eth0)  echo eth0 ;;
        *)       return 1 ;;
    esac
}

# iface에 해당하는 .network 파일 경로. 같은 iface에 여러 개면 가장 최근(mtime) 것 —
# ip/gt/net status가 같은 규약을 복제하고 있어 한 곳으로 모은다.
# glob+루프를 쓰는 이유: `ls -ptr ... | grep -v '/$' | tail -1`은 glob 미매치 시
# 빈 경로를 흘려 "not found: " 같은 메시지를 냈다. 없으면 출력 없이 rc=1.
find_network_conf() {
    local iface="$1" f newest=""
    for f in /etc/systemd/network/*"$iface"*.network; do
        [ -f "$f" ] || continue
        if [ -z "$newest" ] || [ "$f" -nt "$newest" ]; then
            newest="$f"
        fi
    done
    [ -n "$newest" ] || return 1
    echo "$newest"
}

# MHz 값이 9098이 지원하는 대역(2.4G/5G) 안에 있는지 검사.
# to_freq_mhz는 1000 미만 정수를 무조건 5000+5*v로 매핑하므로(예: 200→6000,
# 0→5000) 변환 후 이 검사가 없으면 존재하지 않는 채널이 conf에 박힌다.
# 대역 범위만 보는 이유: 채널별 허용 여부는 regdomain이 결정하므로 여기서
# 좁히면 정상 채널을 거부하게 된다.
is_valid_wifi_freq() {
    local f="$1"
    [[ "$f" =~ ^[0-9]+$ ]] || return 1
    (( f >= 2412 && f <= 2484 )) && return 0
    (( f >= 5180 && f <= 5825 )) && return 0
    return 1
}

# 채널/MHz 토큰 하나를 MHz로 정규화하며 검증. 유효하면 MHz를 stdout으로,
# 아니면 메시지를 stderr로 내고 1을 반환(mscan:1746의 convert-then-recheck 규약).
to_freq_mhz_checked() {
    local arg="$1" f
    f="$(to_freq_mhz "$arg")"
    if ! [[ "$f" =~ ^[0-9]+$ ]]; then
        echo "Error: invalid channel/freq '$arg'" >&2
        return 1
    fi
    if ! is_valid_wifi_freq "$f"; then
        echo "Error: channel/freq '$arg' resolves to ${f}MHz — outside 2.4G(2412-2484)/5G(5180-5825)" >&2
        return 1
    fi
    echo "$f"
}

freq_to_channel() {
    local f="$1"
    if ! [[ "$f" =~ ^[0-9]+$ ]]; then
        echo "$f"
        return
    fi
    if (( f == 2484 )); then
        echo 14
    elif (( f >= 2412 && f <= 2472 )); then
        echo $(( (f - 2407) / 5 ))
    elif (( f >= 5000 && f <= 5995 )); then
        echo $(( (f - 5000) / 5 ))
    else
        echo "$f"
    fi
}

freqs_with_channels() {
    local freqs="$1"
    local result=""
    for f in $freqs; do
        local ch
        ch=$(freq_to_channel "$f")
        if [ "$ch" != "$f" ]; then
            result="${result:+$result }${f}(ch${ch})"
        else
            result="${result:+$result }${f}"
        fi
    done
    echo "$result"
}

to_freq_mhz() {
    local v="$1"
    if ! [[ "$v" =~ ^[0-9]+$ ]]; then
        echo "$v"
        return
    fi
    if (( v < 1000 )); then
        if (( v >= 1 && v <= 13 )); then
            echo $((2407 + 5 * v))
            return
        elif (( v == 14 )); then
            echo 2484
            return
        else
            echo $((5000 + 5 * v))
            return
        fi
    else
        echo "$v"
        return
    fi
}

# Expand a band shortcut (2G/5G) to its channel center frequencies (MHz).
# Empty output means the token is not a band shortcut.
band_freqs() {
    case "$1" in
        2G|2g|2.4G|2.4g)
            echo "2412 2417 2422 2427 2432 2437 2442 2447 2452 2457 2462 2467 2472 2484" ;;
        5G|5g)
            echo "5180 5200 5220 5240 5260 5280 5300 5320 5500 5520 5540 5560 5580 5600 5620 5640 5660 5680 5700 5720 5745 5765 5785 5805 5825" ;;
    esac
}

# Expand a band shortcut (2G/5G) to a setuserscan whole-band chan token.
# chan=0g/0a tells the driver to scan all channels of that band (avoids the
# MAX_CHAN_SCRATCH=100 char limit that an explicit channel list would hit).
# Empty output means not a band shortcut.
band_chans() {
    case "$1" in
        2G|2g|2.4G|2.4g)
            echo "0g" ;;
        5G|5g)
            echo "0a" ;;
    esac
}

show_info() {
    local only_iface="${1:-all}"

    echo "=========================================================="
    echo "  WLAN System Information & Status"
    echo "=========================================================="
    
    # 1. Interface Status
    echo "[Network Interfaces]"
    local devs=()
    if [ "$only_iface" = "all" ]; then
        devs=(eth0 mlan0 mlan1)
    else
        devs=("$only_iface")
    fi

    for dev in "${devs[@]}"; do
        if [ -d "/sys/class/net/$dev" ]; then
            cidr=$(ip -4 addr show "$dev" 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}/\d+')
            mac=$(cat /sys/class/net/"$dev"/address 2>/dev/null)
            carrier=$(cat /sys/class/net/"$dev"/carrier 2>/dev/null || echo "0")
            state=$(ip link show "$dev" | grep -oP '(?<=state\s)\w+')
            gw=$(ip -4 route show default dev "$dev" 2>/dev/null | awk '{for (i=1;i<=NF;i++) if ($i=="via") {print $(i+1); exit}}')
            # Read configured IP from .network file
            cfg_ip=""
            for nf in /etc/systemd/network/*.network; do
                [ -f "$nf" ] || continue
                if grep -q "^Name=${dev}$" "$nf" 2>/dev/null; then
                    cfg_ip=$(grep -oP '(?<=^Address=)\S+' "$nf" 2>/dev/null)
                    break
                fi
            done
            if [ "$only_iface" = "all" ]; then
                printf "  %-6s: %-18s [%s] MAC:%s Carrier:%s GW:%s Conf:%s\n" \
                    "$dev" "${cidr:-N/A}" "$state" "$mac" "$carrier" "${gw:-N/A}" "${cfg_ip:-N/A}"
            else
                printf "  %-18s [%s] MAC:%s Carrier:%s GW:%s Conf:%s\n" \
                    "${cidr:-N/A}" "$state" "$mac" "$carrier" "${gw:-N/A}" "${cfg_ip:-N/A}"
            fi
        fi
    done
    echo ""

    if [ "$only_iface" = "eth0" ]; then
        :
    else
        # Per-interface config from wifi_init_conf.json
        show_iface_config() {
            local iface="$1"
            if [ -f "$WIFI_INIT_CONF_JSON" ] && command -v jq >/dev/null 2>&1; then
                local iface_json
                iface_json=$(jq --arg i "$iface" '.[$i]' "$WIFI_INIT_CONF_JSON")
                if [ -z "$iface_json" ] || [ "$iface_json" = "null" ]; then
                    echo "  [${iface}] (no JSON config)"
                    return
                fi

                local enabled freq net_rx bgscan_interval
                local roam_th_2g roam_th_5g roam_diff roam_check
                local pred_en load_en pingpong_en adaptive_en

                enabled=$(echo "$iface_json" | jq -r 'if .enabled == null then true else .enabled end')
                freq=$(echo "$iface_json" | jq -r '.Frequency // "auto"')
                net_rx=$(echo "$iface_json" | jq -r '.net_rx // 0')
                bgscan_interval=$(echo "$iface_json" | jq -r '.bgscan.interval // 60')
                roam_th_2g=$(echo "$iface_json" | jq -r '.roaming.DEFAULT_TH_2G // -75')
                roam_th_5g=$(echo "$iface_json" | jq -r '.roaming.DEFAULT_TH_5G // -75')
                roam_diff=$(echo "$iface_json" | jq -r '.roaming.DIFF_TH // 7')
                roam_check=$(echo "$iface_json" | jq -r '.roaming.CHECK_INTERVAL // 1')
                pred_en=$(echo "$iface_json" | jq -r 'if .roaming.PREDICTIVE_ROAM.enable == null then false else .roaming.PREDICTIVE_ROAM.enable end')
                pingpong_en=$(echo "$iface_json" | jq -r 'if .roaming.PING_PONG_PREVENTION.enable == null then true else .roaming.PING_PONG_PREVENTION.enable end')

                if [ "$only_iface" = "all" ]; then
                    echo "  [${iface}]"
                    echo "    enabled=$enabled  Frequency=$freq  net_rx=$net_rx"
                    echo "    bgscan_interval=${bgscan_interval}s"
                    echo "    roaming: TH_2G=${roam_th_2g} TH_5G=${roam_th_5g} DIFF=${roam_diff} CHECK=${roam_check}s"
                    echo "    features: predictive=$pred_en pingpong=$pingpong_en"
                else
                    echo "  enabled=$enabled  Frequency=$freq  net_rx=$net_rx"
                    echo "  bgscan_interval=${bgscan_interval}s"
                    echo "  roaming: TH_2G=${roam_th_2g} TH_5G=${roam_th_5g} DIFF=${roam_diff} CHECK=${roam_check}s"
                    echo "  features: predictive=$pred_en pingpong=$pingpong_en"
                fi
            else
                echo "  [${iface}] (no JSON config)"
            fi
        }

        echo "[Interface Config]"
        if [ "$only_iface" = "all" ]; then
            show_iface_config "mlan0"
            show_iface_config "mlan1"
        else
            show_iface_config "$only_iface"
        fi
        echo ""

        echo "[Driver Config]"
        if [ -f "$WIFI_INIT_CONF_JSON" ] && command -v jq >/dev/null 2>&1; then
            BUS_TYPE=$(jq -r '.global.BUS_TYPE // "pcie"' "$WIFI_INIT_CONF_JSON")
            MOD_PARA=$(jq -r '.global.MOD_PARA // "cts/wifi_mod_para.conf"' "$WIFI_INIT_CONF_JSON")
            CAL_DATA_CFG=$(jq -r '.global.CAL_DATA_CFG // "cts/WlanCalData_ext_RD.conf"' "$WIFI_INIT_CONF_JSON")
            TXPWRLIMIT_PATH=$(jq -r '.global.TXPWRLIMIT_PATH // "/lib/firmware/cts/txpwrlimit_cfg_9098.conf"' "$WIFI_INIT_CONF_JSON")
            echo "  BUS_TYPE      : $BUS_TYPE"
            echo "  MOD_PARA      : $MOD_PARA"
            echo "  CAL_DATA_CFG  : $CAL_DATA_CFG"
            echo "  TXPWRLIMIT    : $TXPWRLIMIT_PATH"
        else
            echo "  (no JSON config)"
        fi
        echo ""

        # MFG 상태 — SoT는 mod_para.conf의 mfg_mode= (wifi_init.sh 등 정책 스크립트와 동일
        # 라인앵커 grep). fw_name은 활성 버스 블록(_0)에서, mfg_loaded flag는 "현재 드라이버가
        # mfg 모드로 로드됨"(wifi_init.sh 멱등 가드 기준)을 뜻한다.
        echo "[MFG]"
        local _mfg_mp _mfg_mode _mfg_blk _mfg_fw _mfg_loaded _mfg_bus
        _mfg_mp="cts/wifi_mod_para.conf"
        _mfg_bus="pcie"
        if [ -f "$WIFI_INIT_CONF_JSON" ] && command -v jq >/dev/null 2>&1; then
            _mfg_mp=$(jq -r '.global.MOD_PARA // "cts/wifi_mod_para.conf"' "$WIFI_INIT_CONF_JSON" 2>/dev/null) || _mfg_mp="cts/wifi_mod_para.conf"
            [ -n "$_mfg_mp" ] || _mfg_mp="cts/wifi_mod_para.conf"
            _mfg_bus=$(jq -r '.global.BUS_TYPE // "pcie"' "$WIFI_INIT_CONF_JSON" 2>/dev/null) || _mfg_bus="pcie"
        fi
        _mfg_mode=$(grep -m1 '^[[:space:]]*mfg_mode=' "/lib/firmware/$_mfg_mp" 2>/dev/null | sed 's/.*mfg_mode=//' | tr -d ' ')
        _mfg_mode=${_mfg_mode:-0}
        if [ "$_mfg_bus" == "sdio" ]; then _mfg_blk="SD9098"; else _mfg_blk="PCIE9098"; fi
        _mfg_fw=$(grep -A20 "^${_mfg_blk}_0 " "/lib/firmware/$_mfg_mp" 2>/dev/null | grep -m1 'fw_name=' | sed 's/.*fw_name=//' | tr -d ' ')
        _mfg_loaded="no"
        [ -f /run/wifi/mfg_loaded ] && _mfg_loaded="yes"
        echo "  mfg_mode      : $_mfg_mode (SoT: /lib/firmware/$_mfg_mp)"
        echo "  fw_name       : ${_mfg_fw:-N/A} (${_mfg_blk}_0)"
        echo "  mfg_loaded    : $_mfg_loaded (/run/wifi/mfg_loaded)"
        if [ "$_mfg_mode" == "1" ]; then
            echo "  profile       : MFG — STA/FW 유닛 disable+stop, checker idle, 재부팅 정책 거부(비상/과열 제외)"
        else
            echo "  profile       : normal"
        fi
        echo ""
    fi

    if [ "$only_iface" = "eth0" ]; then
        :
    else
        echo "[wpa_supplicant Settings]"
    wpa_field() {
        local file="$1" key="$2"
        awk -v key="$key" '
            /^[[:space:]]*#/ { next }
            {
                k = key "="
                if (index($0, k) == 1 || match($0, "^[[:space:]]+" k)) {
                    line = $0
                    sub(/^[[:space:]]+/, "", line)
                    sub(k, "", line)
                    gsub(/^[[:space:]]+|[[:space:]]+$/, "", line)
                    gsub(/^\"|\"$/, "", line)
                    print line
                    exit 0
                }
            }
        ' "$file" 2>/dev/null
    }

        local wpa_devs=()
        if [ "$only_iface" = "all" ]; then
            wpa_devs=(mlan0 mlan1)
        else
            wpa_devs=("$only_iface")
        fi

    for dev in "${wpa_devs[@]}"; do
        conf="$WPA_CONF_DIR/wpa_supplicant-${dev}.conf"
        if [ ! -f "$conf" ]; then
            echo "  $dev: not found ($conf)"
            continue
        fi
        country=$(wpa_field "$conf" "country")
        ssid=$(wpa_field "$conf" "ssid")
        psk=$(wpa_field "$conf" "psk")
        psk_display="N/A"
        [ -n "${psk:-}" ] && psk_display="********"
        key_mgmt=$(wpa_field "$conf" "key_mgmt")
        freq_list=$(wpa_field "$conf" "freq_list")
        if [ "$only_iface" = "all" ]; then
            local prefix="  ${dev}: "
            local pad
            pad=$(printf '%*s' ${#prefix} "")
            echo "${prefix}country=${country:-N/A} ssid=${ssid:-N/A} psk=${psk_display} key_mgmt=${key_mgmt:-N/A}"
        else
            local pad="  "
            echo "  country=${country:-N/A} ssid=${ssid:-N/A} psk=${psk_display} key_mgmt=${key_mgmt:-N/A}"
        fi
        if [ -n "${freq_list:-}" ]; then
            echo "${pad}freq_list=$(freqs_with_channels "${freq_list// / }")"
        fi
    done
    echo ""
    fi

    # FW 설정 축은 미반영이어도 부팅을 막지 않는다(로그는 흘러간다). 지금 이 보드가
    # 의도한 RF 설정으로 도는지 물을 수 있어야 하므로 상태로 노출한다.
    echo "[FW Config Unapplied]"
    if declare -F wifi_fw_unapplied_read >/dev/null 2>&1; then
        local unapplied_ifaces=() unapplied_dev unapplied_lines unapplied_line
        local unapplied_any=0
        if [ "$only_iface" = "all" ]; then
            unapplied_ifaces=(mlan0 mlan1)
        else
            unapplied_ifaces=("$only_iface")
        fi
        for unapplied_dev in "${unapplied_ifaces[@]}"; do
            unapplied_lines=$(wifi_fw_unapplied_read "$unapplied_dev") || continue
            while IFS= read -r unapplied_line; do
                [ -n "$unapplied_line" ] || continue
                unapplied_any=1
                printf "  %-6s %s\n" "${unapplied_dev}:" "$unapplied_line"
            done <<< "$unapplied_lines"
        done
        [ "$unapplied_any" -eq 1 ] || echo "  none (all FW config axes applied)"
    else
        echo "  (reader unavailable)"
    fi
    echo ""

    echo "[Services]"
    if command -v systemctl >/dev/null 2>&1; then
        local svc_list=()

        # Non-wifi services
        svc_list+=(switchd)
        svc_list+=("journald-snapshot.timer" "fake-hwclock.timer" "log-watchdog.timer")

        # WiFi global services
        svc_list+=(wifi_init wifi_logger wifi_ping_monitor)
        svc_list+=("wifi_mgmt_log.timer" "wifi_thermal_state.timer")

        # 부가 데몬 (SNMP 관리 / OPC 제어 — MFG 프로파일 disable+stop 대상)
        svc_list+=(snmpd opcd)

        add_iface_svcs() {
            local iface="$1"
            if [ "$iface" = "mlan0" ] || [ "$iface" = "mlan1" ]; then
                svc_list+=("wpa_supplicant@${iface}")
            fi
            svc_list+=("wifi_logger@${iface}" "wifi_led@${iface}")
            svc_list+=("wifi_checker@${iface}" "wifi_event@${iface}")
            svc_list+=("wifi_bridge@${iface}" "wifi_arping@${iface}" "wifi_bgscan@${iface}" "wifi_roam@${iface}" "wifi_periodic_roam@${iface}")
        }

        if [ "$only_iface" = "all" ]; then
            add_iface_svcs eth0
            add_iface_svcs mlan0
            add_iface_svcs mlan1
        else
            add_iface_svcs "$only_iface"
        fi

        for svc in "${svc_list[@]}"; do
            state=$(systemctl is-active "$svc" 2>/dev/null || true)
            [ -z "$state" ] && state="unknown"
            printf "  %-34s %s\n" "$svc:" "${state^^}"
        done
    else
        echo "  systemctl not available"
    fi
    echo "=========================================================="
}

# wifi <0|1|mlan0|mlan1> br status : peer_route/브릿지 설정·런타임·정합성 진단 (읽기 전용, 어떤 값도 변경 안 함)
_bridge_status() {
    local iface="${1:-mlan0}"
    local eth="eth0"
    local J="$WIFI_INIT_CONF_JSON"
    local have_jq=0
    command -v jq >/dev/null 2>&1 && [ -f "$J" ] && have_jq=1

    echo "=========================================================="
    echo "  WiFi Bridge / peer_route Status"
    echo "=========================================================="

    local engine bridge_iface mac_mode ip_disc sweep client_ip enabled enabled_raw pr_raw pr aia_raw aia lhp_raw
    if [ "$have_jq" = "1" ]; then
        engine=$(jq -r '.wbridge.engine // "pcap"' "$J")
        bridge_iface=$(jq -r '.wbridge.bridge_iface // "mlan0"' "$J")
        mac_mode=$(jq -r '.wbridge.mac_mode // "dynamic"' "$J")
        ip_disc=$(jq -r '.wbridge.ip_discovery // false' "$J")
        sweep=$(jq -r '.wbridge.eth_sweep_subnet // ""' "$J")
        client_ip=$(jq -r '.wbridge.eth_client_ip // ""' "$J")
        enabled_raw=$(jq -r '.wbridge.enabled' "$J")
        pr_raw=$(jq -r '.wbridge.peer_route.enabled' "$J")
        aia_raw=$(jq -r '.wbridge.arp_ignore_always.enabled' "$J")
        lhp_raw=$(jq -r '.wbridge.moal.local_hairpin // ""' "$J")
        ra_raw=$(jq -r '.wbridge.moal.roam_announce // ""' "$J")
    else
        echo "  (no jq or $J — config fields unavailable, runtime only)"
        engine="?"; bridge_iface="mlan0"; mac_mode="?"; ip_disc="?"; sweep=""; client_ip=""; enabled_raw="?"
        pr_raw="?"; aia_raw="?"; lhp_raw="?"; ra_raw="?"
    fi
    # wbridge.enabled 실효값 (기본 true). jq의 //는 false도 흡수하므로(`false // true` → true)
    # raw로 읽어 case로 분기 — enabled=false 상태를 진단에서 정확히 표기.
    case "$enabled_raw" in
        false)   enabled=false ;;
        true)    enabled=true ;;
        null|"") enabled=true ;;
        *)       enabled="$enabled_raw" ;;
    esac
    # peer_route 실효값 (wifi_init.sh와 동일: true/false 외 invalid/missing → factory default true)
    case "$pr_raw" in
        true|false) pr="$pr_raw" ;;
        *)          pr="true" ;;
    esac
    # arp_ignore_always 실효값 (기본 false)
    case "$aia_raw" in
        true) aia="true" ;;
        *)    aia="false" ;;
    esac
    # br status는 무선 브릿지 iface 기준. eth0 등으로 불리면 JSON bridge_iface로 대체.
    if [ "$iface" != "mlan0" ] && [ "$iface" != "mlan1" ]; then
        iface="$bridge_iface"
    fi

    echo "[Config: wbridge]  (source: $J)"
    printf "  %-24s %s\n" "enabled:"            "$enabled"
    printf "  %-24s %s\n" "engine:"             "$engine"
    printf "  %-24s %s\n" "bridge_iface:"       "$bridge_iface"
    printf "  %-24s %s\n" "mac_mode:"           "$mac_mode"
    printf "  %-24s %s\n" "peer_route.enabled:" "$pr (json=$pr_raw)"
    printf "  %-24s %s\n" "ip_discovery:"       "$ip_disc"
    printf "  %-24s %s\n" "arp_ignore_always:"  "$aia (json=$aia_raw)"
    printf "  %-24s %s\n" "moal.local_hairpin:" "${lhp_raw:-<empty>=driver default 0}"
    printf "  %-24s %s\n" "moal.roam_announce:" "${ra_raw:-<empty>=driver default 0}"
    printf "  %-24s %s\n" "eth_fallback:"       "$([ "$have_jq" = "1" ] && jq -r '.wbridge.eth_fallback.enabled // false' "$J" || echo "?")"
    printf "  %-24s %s\n" "eth_sweep_subnet:"   "${sweep:-<empty>}"
    printf "  %-24s %s\n" "eth_client_ip:"      "${client_ip:-<empty>}"
    echo ""

    # --- Runtime (measured; peer_route on일 때 wifi_init.sh가 부여한 것) ---
    local mlan_inet eth_mirror rule_cnt arp_ignore host_route t100_cnt topo lhp_rt rpf_eth hp_stats ra_rt an_stats
    mlan_inet=$(ip -4 addr show "$iface" 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}/\d+' | head -1)
    eth_mirror=$(ip -4 addr show "$eth" 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}/32' | head -1)
    rule_cnt=$(ip rule show 2>/dev/null | grep -c "iif $eth")
    arp_ignore=$(sysctl -n net.ipv4.conf.all.arp_ignore 2>/dev/null || echo "?")
    host_route=$(ip -4 route show dev "$eth" 2>/dev/null | awk '$1 ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/{print $1; exit}')
    t100_cnt=$(ip route show table 100 2>/dev/null | grep -c .)
    # local hairpin runtime: param 파일 부재("-")는 moal 미로드 또는 구버전 드라이버
    lhp_rt=$(cat /sys/module/moal/parameters/bridge_local_hairpin 2>/dev/null || echo "-")
    rpf_eth=$(sysctl -n net.ipv4.conf.$eth.rp_filter 2>/dev/null || echo "?")
    arp_ignore_if=$(sysctl -n net.ipv4.conf.$iface.arp_ignore 2>/dev/null || echo "?")
    hp_stats=$(grep '^hairpin' /sys/kernel/moal_bridge/stats 2>/dev/null || echo "-")
    # roam announce runtime: param 파일 부재("-")는 moal 미로드 또는 구버전 드라이버
    ra_rt=$(cat /sys/module/moal/parameters/bridge_roam_announce 2>/dev/null || echo "-")
    an_stats=$(grep '^announce' /sys/kernel/moal_bridge/stats 2>/dev/null || echo "-")

    echo "[Runtime: measured]"
    printf "  %-24s %s\n" "$iface inet:"        "${mlan_inet:-<none>}"
    printf "  %-24s %s\n" "$eth /32 mirror:"    "${eth_mirror:-<none>}"
    printf "  %-24s %s\n" "ip rule iif $eth:"   "$([ "${rule_cnt:-0}" -gt 0 ] && echo present || echo absent)"
    printf "  %-24s %s\n" "arp_ignore(all):"    "$arp_ignore"
    printf "  %-24s %s\n" "arp_ignore($iface):" "$arp_ignore_if"
    printf "  %-24s %s\n" "rp_filter($eth):"    "$rpf_eth"
    printf "  %-24s %s\n" "peer host route:"    "${host_route:-<none>}"
    printf "  %-24s %s\n" "table 100 routes:"   "${t100_cnt:-0}"
    printf "  %-24s %s\n" "local_hairpin(rt):"  "$lhp_rt"
    printf "  %-24s %s\n" "hairpin counters:"   "$hp_stats"
    printf "  %-24s %s\n" "roam_announce(rt):"  "$ra_rt"
    printf "  %-24s %s\n" "announce counters:"  "$an_stats"
    printf "  %-24s %s\n" "fallback route(m200):" "$(ip route show dev "$eth" 2>/dev/null | grep -q "metric 200" && echo present || echo absent)"
    echo ""

    # 토폴로지 판정 — JSON 의도 우선. IP 배치만으로는 [mlan0-IP + eth0 관리IP]와
    # [eth0-IP + mlan0 관리IP(타서브넷)]가 대칭이라 원리적으로 구분 불가하므로
    # (docs/bridge-eth0-ip-topology.md §1: eth0-IP 토폴로지의 mlan0 = "무IP 또는
    # 타서브넷 IP"), 토폴로지 전용 옵트인 키를 확정 신호로 쓴다:
    #   mlan0-IP 계열 전용 = hairpin / eth_fallback / peer_route,
    #   eth0-IP 전용 = arp_ignore_always.
    # topo_det("mlan0-IP"|"eth0-IP"|"")는 아래 Consistency 규칙의 게이트 —
    # 구버전은 "mlan0 inet 존재"만으로 판정해 관리IP 변형에서 WARN 오탐을 냈다.
    local warn=0 hp_on=0 lhp_exp ef_json ef_rt topo_det="" eth_inet
    ef_json=$([ "$have_jq" = "1" ] && jq -r '.wbridge.eth_fallback.enabled // false' "$J" || echo "?")
    eth_inet=$(ip -4 addr show "$eth" 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}/\d+' | grep -v '/32$' | head -1)
    if [ "$lhp_raw" = "1" ] || [ "$ef_json" = "true" ] || [ "$pr" = "true" ]; then
        topo_det="mlan0-IP"; topo="mlan0-IP (json: hairpin/eth_fallback/peer_route)"
    elif [ "$aia" = "true" ]; then
        topo_det="eth0-IP"; topo="eth0-IP (json: arp_ignore_always)"
    elif [ -n "$mlan_inet" ] && [ -z "$eth_inet" ]; then
        topo_det="mlan0-IP"; topo="mlan0-IP (est: $iface has inet, $eth none)"
    elif [ -z "$mlan_inet" ] && [ -n "$eth_inet" ]; then
        topo_det="eth0-IP"; topo="eth0-IP (est: $eth has inet, $iface none)"
    elif [ -n "$mlan_inet" ]; then
        topo="ambiguous (both $iface/$eth have inet, no json signal)"
    else
        topo="unknown (no inet on $iface/$eth)"
    fi
    printf "  %-24s %s\n" "topology:" "$topo"
    echo ""

    # --- Consistency (진단만; 사람이 판단. 자동 교정 없음) ---
    echo "[Consistency]"
    # eth_fallback 런타임 상태 — /32 소유 판정 등이 참조 (ef_json은 토폴로지 판정에서 확보)
    ef_rt=$(ip route show dev "$eth" 2>/dev/null | grep -c "metric 200")
    # hairpin 실효값: runtime param 기준 (JSON은 부팅 시 전달 여부일 뿐)
    [ "$lhp_rt" = "1" ] && hp_on=1
    if [ "$topo_det" = "mlan0-IP" ] && [ "$aia" = "true" ] && [ "$pr" = "false" ] && [ -n "$mlan_inet" ]; then
        echo "  [WARN] arp_ignore_always=on + peer_route=off on mlan0-IP -> wired->BD ARP UNANSWERED."
        echo "         Fix: wifi {0|1} br profile {hairpin|peer-route} apply"
        warn=1
    fi
    # 동일 서브넷 병존 — 어느 토폴로지에서도 비정상 (라우팅 모호·weak-host 재개방).
    # 관리 IP 병존은 반드시 타서브넷이어야 함 (docs/bridge-eth0-ip-topology.md §1).
    if [ -n "$mlan_inet" ] && [ -n "$eth_inet" ] && command -v python3 >/dev/null 2>&1; then
        if [ "$(python3 -c "import ipaddress,sys; print(int(ipaddress.ip_interface(sys.argv[1]).network==ipaddress.ip_interface(sys.argv[2]).network))" "$mlan_inet" "$eth_inet" 2>/dev/null)" = "1" ]; then
            echo "  [WARN] $iface($mlan_inet) / $eth($eth_inet) 동일 서브넷 병존 -> 어느 토폴로지에서도 비정상 (라우팅 모호). 관리 IP는 타서브넷으로."
            warn=1
        fi
    fi
    # eth0-IP 의도인데 eth0 무IP — 유선측 BD 접근 IP 부재 (IP 이설 미완 상태)
    if [ "$topo_det" = "eth0-IP" ] && [ -z "$eth_inet" ]; then
        echo "  [WARN] eth0-IP intent (arp_ignore_always=on) but $eth has no inet -> 유선측 BD 접근 IP 부재."
        echo "         Fix: $eth 에 공유 서브넷 IP 부여 (IP 이설 미완 상태)"
        warn=1
    fi
    if [ "$pr" = "true" ] && [ "$ip_disc" != "true" ]; then
        if [ "$hp_on" = "1" ]; then
            echo "  [INFO] peer_route=on + ip_discovery=off: host route는 미등록이지만 local_hairpin이 BD->peer를 커버."
        else
            echo "  [WARN] peer_route=on but ip_discovery=off -> BD->peer host route not registered (one-way)."
            echo "         Fix: wbridge.ip_discovery=true  또는 wifi {0|1} br profile hairpin apply"
            warn=1
        fi
    fi
    # --- local hairpin 정합성 (moal 전용 기능) ---
    if [ "$lhp_raw" = "1" ] && [ "$engine" != "moal" ]; then
        echo "  [WARN] local_hairpin=1 but engine=$engine -> ineffective (hairpin은 moal 전용, pcap은 tap이라 TX 가로채기 불가)."
        echo "         Fix: wifi {0|1} br moal  후 재부팅, 또는 profile peer-route apply"
        warn=1
    fi
    if [ "$hp_on" = "1" ] && [ -z "$mlan_inet" ]; then
        echo "  [WARN] local_hairpin active but $iface has no inet -> ARP REPLY inject(tip==self) 판정 불발, BD->peer ARP 미해소."
        echo "         Fix: $iface 에 공유 서브넷 IP 부여 (wifi $iface ip <addr/mask>)"
        warn=1
    fi
    if [ "$topo_det" = "mlan0-IP" ] && [ "$pr" = "false" ] && [ "$ip_disc" != "true" ] && [ "$hp_on" = "0" ] && [ -n "$mlan_inet" ]; then
        echo "  [WARN] peer_route=off + ip_discovery=off + hairpin off on mlan0-IP -> BD<->wired peer IP 통신 경로 없음."
        echo "         Fix: wifi {0|1} br profile {hairpin|peer-route} apply"
        warn=1
    fi
    if [ "$engine" = "moal" ] && [ "$lhp_raw" != "?" ]; then
        if [ "$lhp_rt" = "-" ]; then
            # param 파일 부재 = 로드된 .ko 가 bridge_local_hairpin 미선언(구버전 드라이버).
            # wifi_init.sh parmtype 게이트가 인자를 skip 하므로 부팅은 무사하나 hairpin 미적용.
            if [ "$lhp_raw" = "1" ]; then
                echo "  [WARN] local_hairpin=1(JSON) but loaded driver lacks the param (구버전 .ko) -> hairpin 미적용."
                echo "         Fix: hairpin 지원 드라이버(.ko) 배포 후 재부팅 (parmtype 게이트가 현재 인자를 skip 중)"
                warn=1
            fi
        else
            lhp_exp="${lhp_raw:-0}"
            if [ "$lhp_exp" != "$lhp_rt" ]; then
                echo "  [WARN] local_hairpin JSON(${lhp_raw:-<empty>=0}) != runtime($lhp_rt) -> 재부팅 미반영 또는 runtime 수동 토글 상태."
                warn=1
            fi
        fi
    fi
    if [ "$engine" = "moal" ] && [ "$ra_raw" != "?" ]; then
        if [ "$ra_rt" = "-" ]; then
            # param 파일 부재 = 로드된 .ko 가 bridge_roam_announce 미선언(구버전 드라이버).
            # wifi_init.sh parmtype 게이트가 인자를 skip 하므로 부팅은 무사하나 announce 미적용.
            if [ "$ra_raw" = "1" ]; then
                echo "  [WARN] roam_announce=1(JSON) but loaded driver lacks the param (구버전 .ko) -> announce 미적용."
                echo "         Fix: announce 지원 드라이버(.ko) 배포 후 재부팅 (parmtype 게이트가 현재 인자를 skip 중)"
                warn=1
            fi
        else
            ra_exp="${ra_raw:-0}"
            if [ "$ra_exp" != "$ra_rt" ]; then
                echo "  [WARN] roam_announce JSON(${ra_raw:-<empty>=0}) != runtime($ra_rt) -> 재부팅 미반영 또는 runtime 수동 토글 상태."
                warn=1
            fi
        fi
    fi
    if [ "$topo_det" = "mlan0-IP" ] && [ "$pr" = "false" ] && [ -n "$mlan_inet" ] && [ "$rpf_eth" = "1" ]; then
        echo "  [WARN] $eth rp_filter=1(strict) + peer_route=off -> wired->BD($iface IP행) inbound martian drop 위험."
        echo "         Fix: sysctl net.ipv4.conf.$eth.rp_filter=2"
        warn=1
    fi
    # 무선 weak-host ARP 개방: 실효 arp_ignore(max(all,iface))가 0이면 무선발
    # who-has <eth0-IP>에 클론 MAC으로 응답 — 플릿(공통 관리IP+플랫 L2)에서
    # 중복 IP/DAI 위반 위험. per-interface 봉인(wifi_init.sh)이 적용되면 항상 ≥1.
    if [ "$arp_ignore" = "0" ] && [ "$arp_ignore_if" = "0" ]; then
        echo "  [WARN] $iface weak-host ARP 개방 (arp_ignore all=0, $iface=0) -> 무선발 eth0-IP ARP에 클론 MAC 응답 위험(플릿 DAI)."
        echo "         Fix: 구버전 wifi_init.sh 의심 — per-interface 봉인 포함 버전 배포 후 재부팅, 임시: sysctl net.ipv4.conf.$iface.arp_ignore=1"
        warn=1
    fi
    # eth_fallback(B-2) 정합성: JSON on인데 fallback route(metric 200) 부재면 미반영
    if [ "$ef_json" = "true" ] && [ "${ef_rt:-0}" -eq 0 ]; then
        echo "  [WARN] eth_fallback=on(JSON) but no metric-200 route on $eth -> 미반영 (재부팅 필요) — 무선 down 시 유선 절체 불가 상태."
        warn=1
    fi
    if [ "$hp_on" = "1" ] && [ "$ef_json" != "true" ] && [ -n "$mlan_inet" ]; then
        echo "  [INFO] hairpin on + eth_fallback off: 무선 down 중 유선 BD<->peer 통신 코너 열림 (G2). 필요 시 profile {hairpin|dual} apply."
    fi
    if [ "$pr" = "true" ] && [ "$aia" = "true" ]; then
        echo "  [INFO] arp_ignore_always redundant (peer_route=on already applies arp_ignore=1)."
    fi
    if [ "$mac_mode" = "dynamic" ] && [ "$ip_disc" = "true" ] && [ -z "$sweep" ]; then
        echo "  [INFO] eth_sweep_subnet empty -> last-resort sweep falls back to $iface net (${mlan_inet:-<none>}). Set it to pin the range."
    fi
    if [ "$pr" = "true" ] && [ -z "$eth_mirror" ]; then
        echo "  [WARN] peer_route=on but no /32 mirror on $eth (runtime). Reboot or 'wifi <0|1> br restart'."
        warn=1
    fi
    if [ "$pr" = "false" ] && [ -n "$eth_mirror" ] && [ "$ef_json" != "true" ] && [ "$lhp_raw" != "1" ]; then
        # eth_fallback=on 또는 local_hairpin=1 이면 /32 는 정당한 산출물 — stale 아님
        # (wifi_init.sh가 hairpin 구성에서도 eth0 대표주소=무선 IP 미러를 부여한다)
        echo "  [WARN] peer_route=off but /32 mirror present on $eth ($eth_mirror). Stale; reboot to revert."
        warn=1
    fi
    [ "$warn" = "0" ] && echo "  [OK] no blocking issues detected."
    echo "=========================================================="
}

# wifi <0|1> br profile [<name> [apply]] : 연계 설정 묶음(프로파일) 조회/적용
# peer_route/ip_discovery/arp_ignore_always/moal.local_hairpin 은 서로 함정 조합이
# 있어(예: aia=on+peer_route=off → 유선→BD ARP 무응답) 개별 수정 시 실수하기 쉽다.
# 검증된 조합만 원자적으로 기록한다. 기본 dry-run — 'apply'를 붙여야 실제 기록.
# 적용 후 정합성은 br status 로 확인.
_bridge_profile() {
    local name="${1:-}" do_apply="${2:-}"
    local J="$WIFI_INIT_CONF_JSON"
    local pr disc aia lhp ef engine lhp_disp
    if ! command -v jq >/dev/null 2>&1 || [ ! -f "$J" ]; then
        echo "Error: jq or $J not found" >&2; return 1
    fi
    # ef(eth_fallback/B-2): mlan0 IP를 eth0에 병행 부여 — 무선 down 시 eth0 직결 자동
    # 절체 (G2 무선단절 유선 VHL 해소). hairpin/dual에 기본 포함.
    case "$name" in
        hairpin)    pr=false; disc=false; aia=false; lhp=1;  ef=true ;;
        dual)       pr=true;  disc=true;  aia=false; lhp=1;  ef=true ;;
        peer-route) pr=true;  disc=true;  aia=false; lhp=""; ef=false ;;
        eth0-ip)    pr=false; disc=false; aia=true;  lhp=""; ef=false ;;
        ""|show|list)
            echo "Usage: wifi {0|1} br profile {hairpin|dual|peer-route|eth0-ip} [apply]"
            echo "  (apply 없으면 dry-run — 변경될 값만 표시. 적용은 다음 부팅부터)"
            echo ""
            echo "  hairpin    : peer_route=off disc=off aia=off hairpin=1 ethfb=on  — peer IP 인지 불요 + 무선down 유선 절체 (moal 전용)"
            echo "  dual       : peer_route=on  disc=on  aia=off hairpin=1 ethfb=on  — 기존 방식 + hairpin 보험 + 절체 (moal 전용, 권장)"
            echo "  peer-route : peer_route=on  disc=on  aia=off hairpin=-  ethfb=off — 기존 방식 (엔진 무관)"
            echo "  eth0-ip    : peer_route=off disc=off aia=on  hairpin=-  ethfb=off — eth0-IP 토폴로지 (docs/bridge-eth0-ip-topology.md §6)"
            echo ""
            echo "[Current]"
            jq -r '.wbridge | "  engine=\(.engine // "pcap") peer_route=\(.peer_route.enabled) ip_discovery=\(.ip_discovery) arp_ignore_always=\(.arp_ignore_always.enabled) local_hairpin=\(.moal.local_hairpin // "-") eth_fallback=\(.eth_fallback.enabled // "-")"' "$J"
            return 0 ;;
        *)  echo "Error: unknown profile '$name' (hairpin|dual|peer-route|eth0-ip)" >&2; return 1 ;;
    esac
    engine=$(jq -r '.wbridge.engine // "pcap"' "$J")
    # hairpin 계열은 moal 엔진 전제 — pcap 은 tap 이라 TX 가로채기 불가
    if [ -n "$lhp" ] && [ "$engine" != "moal" ]; then
        echo "Error: profile '$name' requires engine=moal (current: $engine)." >&2
        echo "       switch first: wifi {0|1} br moal" >&2
        return 1
    fi
    lhp_disp="${lhp:-\"\" (driver default 0)}"
    echo "[Profile: $name]  ($J)"
    printf "  %-24s %s -> %s\n" "peer_route.enabled:" "$(jq -r '.wbridge.peer_route.enabled' "$J")" "$pr"
    printf "  %-24s %s -> %s\n" "ip_discovery:"       "$(jq -r '.wbridge.ip_discovery' "$J")" "$disc"
    printf "  %-24s %s -> %s\n" "arp_ignore_always:"  "$(jq -r '.wbridge.arp_ignore_always.enabled' "$J")" "$aia"
    printf "  %-24s %s -> %s\n" "moal.local_hairpin:" "$(jq -r '.wbridge.moal.local_hairpin // "-"' "$J")" "$lhp_disp"
    printf "  %-24s %s -> %s\n" "eth_fallback:"       "$(jq -r '.wbridge.eth_fallback.enabled // "-"' "$J")" "$ef"
    if [ "$do_apply" != "apply" ]; then
        echo "  (dry-run — 적용: wifi {0|1} br profile $name apply)"
        return 0
    fi
    cp "$J" "${J}.bak-profile" 2>/dev/null || true
    if jq --argjson pr "$pr" --argjson disc "$disc" --argjson aia "$aia" --arg lhp "$lhp" \
        --argjson ef "$ef" \
        '.wbridge.peer_route.enabled=$pr | .wbridge.ip_discovery=$disc |
         .wbridge.arp_ignore_always.enabled=$aia |
         .wbridge.moal.local_hairpin=(if $lhp=="" then "" else ($lhp|tonumber) end) |
         .wbridge.eth_fallback.enabled=$ef' \
        "$J" > "${J}.tmp"; then
        mv "${J}.tmp" "$J"
        echo "  applied (backup: ${J}.bak-profile). 다음 부팅 시 적용."
        echo "  hairpin 은 runtime 즉시 반영 가능: echo ${lhp:-0} > /sys/module/moal/parameters/bridge_local_hairpin"
        echo "  적용 후 점검: wifi {0|1} br status"
    else
        rm -f "${J}.tmp"
        echo "Error: JSON update failed (config unchanged)" >&2
        return 1
    fi
}

# wifi <0|1|mlan0|mlan1> br route {find|set|auto} : eth 유선 peer host route 탐색/등록
# peer_route=on인데 eth 미연결로 부팅해 누락된 peer host route(<peer>/32 dev eth0)를
# 이후 eth 연결 시 사후 등록한다. 두 독립 스크립트(탐색기/등록기)에 위임한다.
#   find [<subnet>] : peer IP 탐색만 (출력, route 무변경)
#   set  <ip>       : 주어진 IP로 route 등록
#   auto [<subnet>] : 탐색 후 정확히 1건이면 등록 (0/2+는 에러)
_bridge_route() {
    local verb="$1" arg="$2" iface="$3"
    local sdir="${WIFI_PEER_SCRIPT_DIR:-/usr/local/scripts}"
    local finder="$sdir/wifi_eth_peer_find.sh"
    local setter="$sdir/wifi_eth_peer_route.sh"
    case "$verb" in
        find)
            "$finder" "$arg" "$iface"
            ;;
        set)
            if [ -z "$arg" ]; then
                echo "Usage: wifi ${NUM:-0} br route set <ip>" >&2
                return 2
            fi
            "$setter" "$arg" "$iface"
            ;;
        auto)
            local peers frc cnt
            peers=$("$finder" "$arg" "$iface"); frc=$?
            # 탐색기 종료코드 구분: 0/1=정상(발견/미발견), 2/3=usage·링크down(이미 stderr 출력),
            # 그 외(127 스크립트 부재·126 권한 등)=비정상 → '미발견'으로 감추지 말고 원인 노출.
            case "$frc" in
                0|1) : ;;
                2|3) return "$frc" ;;
                *)   echo "br route auto: finder 오류 (exit $frc) — 스크립트/권한 확인" >&2; return "$frc" ;;
            esac
            cnt=$(printf '%s\n' "$peers" | grep -c .)
            if [ "$cnt" -eq 0 ]; then
                echo "br route auto: peer 미발견 (sweep 결과 없음). eth 링크/서브넷 확인 후 재시도하거나 'wifi ${NUM:-0} br route set <ip>'로 직접 지정하세요." >&2
                return 1
            elif [ "$cnt" -gt 1 ]; then
                echo "br route auto: 모호함 — $cnt개 발견. 하나를 골라 'wifi ${NUM:-0} br route set <ip>'로 지정하세요:" >&2
                printf '%s\n' "$peers" | sed 's/^/  /' >&2
                return 1
            fi
            "$setter" "$peers" "$iface"
            ;;
        *)
            echo "Usage: wifi {0|1|mlan0|mlan1} br route {find [<subnet>]|set <ip>|auto [<subnet>]}" >&2
            return 2
            ;;
    esac
}

case "$1" in
  0 | mlan0)
    IFACE="mlan0"
    NFACE="mlan1"
    NUM=0
    ;;
  1 | mlan1)
    IFACE="mlan1"
    NFACE="mlan0"
    NUM=1
    ;;
  2 | eth0)
    IFACE="eth0"
    NFACE=""
    NUM=2
    ;;
  info)
    show_info all
    exit 0
    ;;
  set)
    case "$2" in
      fem)
        CAL_VAL="cts/WlanCalData_ext_a0.conf"
        PWR_VAL="/lib/firmware/cts/txpwrlimit_cfg_9098_a0.conf"
        ;;
      azure)
        CAL_VAL="cts/azure/cal_data.conf"
        PWR_VAL="/lib/firmware/cts/azure/txpwrlimit_cfg_9098.conf"
        ;;
      *)
        usage
        ;;
    esac
    echo "Updating configuration to $2 profile..."
    update_json_global "CAL_DATA_CFG" "$CAL_VAL"
    update_json_global "TXPWRLIMIT_PATH" "$PWR_VAL"
    echo "Updated in $WIFI_INIT_CONF_JSON:"
    echo "  CAL_DATA_CFG    = $CAL_VAL"
    echo "  TXPWRLIMIT_PATH = $PWR_VAL"
    exit 0
    ;;
  mfg)
    BUS_TYPE=$(jq -r '.global.BUS_TYPE // "pcie"' "$WIFI_INIT_CONF_JSON" 2>/dev/null || echo "pcie")
    BT=$(jq -r '.global.BLUETOOTH.enable // false' "$WIFI_INIT_CONF_JSON" 2>/dev/null || echo "false")
    if [ "$2" == "0" ] || [ "$2" == "off" ]; then
        if [ "$BUS_TYPE" == "sdio" ]; then
            if [ "$BT" == "true" ]; then FW_NAME="cts/sduart9098_combo_v1.bin"
            else FW_NAME="cts/sd9098_wlan_v1.bin"; fi
        else
            if [ "$BT" == "true" ]; then FW_NAME="cts/pcieuart9098_combo_v1.bin"
            else FW_NAME="cts/pcie9098_wlan_v1.bin"; fi
        fi
        python3 /usr/local/logger/wifi_config.py 2 mfg_mode 0
        python3 /usr/local/logger/wifi_config.py 2 fw_name "$FW_NAME"
    elif [ "$2" == "1" ] || [ "$2" == "on" ]; then
        if [ "$BUS_TYPE" == "sdio" ]; then
            if [ "$BT" == "true" ]; then FW_NAME="cts/sduart9098_combo.bin"
            else FW_NAME="cts/sd9098_wlan.bin"; fi
        else
            if [ "$BT" == "true" ]; then FW_NAME="cts/pcieuart9098_combo.bin"
            else FW_NAME="cts/pcie9098_wlan.bin"; fi
        fi
        python3 /usr/local/logger/wifi_config.py 2 mfg_mode 1
        python3 /usr/local/logger/wifi_config.py 2 fw_name "$FW_NAME"
    else
        usage
    fi
    echo "Updated (BUS_TYPE=$BUS_TYPE, BLUETOOTH=$BT):"
    echo "  fw_name=${FW_NAME}"
    echo "  mfg_mode=$2"
    exit 0
    ;;
  cal)
    CAL_DATA_CFG=$2
    if [[ "$CAL_DATA_CFG" == *.conf ]]; then
        CAL_DATA_CFG=$(stage_custom_calibration "$CAL_DATA_CFG") || exit 1
    elif [[ "$CAL_DATA_CFG" == "2" ]]; then
        CAL_DATA_CFG="cts/WlanCalData_ext_RD.conf"
    elif [[ "$CAL_DATA_CFG" == "1" ]]; then
        CAL_DATA_CFG="cts/WlanCalData_ext.conf"
    elif [[ "$CAL_DATA_CFG" == "0" ]]; then
        CAL_DATA_CFG=""
    else
        usage
    fi
    echo "Updated:"
    echo "  CAL_DATA_CFG=$CAL_DATA_CFG"
    # 종전엔 성공/실패 무관하게 exit 1이라 종료코드가 아무것도 구분하지 못했다 →
    # update_json_global의 실제 결과를 전파한다.
    update_json_global "CAL_DATA_CFG" "$CAL_DATA_CFG" || exit 1
    exit 0
    ;;
  txpwr | txpwrlimit)
    if [ "$2" == "no" ] || [ "$2" == "0" ]; then
        TXPWRLIMIT_PATH=""
    elif [ "$2" == "default" ] || [ "$2" == "1" ]; then
        TXPWRLIMIT_PATH="/lib/firmware/cts/txpwrlimit_cfg_9098.conf"
    elif [ "$2" == "low" ] || [ "$2" == "2" ]; then
        TXPWRLIMIT_PATH="/lib/firmware/cts/txpwrlimit_cfg_9098_low.conf"
    elif [ "$2" == "test" ] || [ "$2" == "3" ]; then
        TXPWRLIMIT_PATH="/lib/firmware/cts/txpwrlimit_cfg_9098_org.conf"
    elif [[ "$2" == *.conf ]]; then
        # cp 실패를 무시하면 없는 파일을 가리키는 송신출력 정책이 persist된다.
        if [ ! -f "$2" ]; then
            echo "Error: txpwrlimit conf not found: '$2'" >&2
            exit 1
        fi
        _txpwr_basename=$(basename "$2")
        # 이미 /lib/firmware/cts 안의 파일이면 cp가 "same file"로 1을 반환 → -ef로 제외.
        if [ ! "$2" -ef "/lib/firmware/cts/$_txpwr_basename" ] \
            && ! cp "$2" "/lib/firmware/cts/$_txpwr_basename"; then
            echo "Error: failed to stage '$2' into /lib/firmware/cts" >&2
            exit 1
        fi
        TXPWRLIMIT_PATH="/lib/firmware/cts/$_txpwr_basename"
    else
        usage
    fi
    echo "Updated:"
    echo "  TXPWRLIMIT_PATH=$TXPWRLIMIT_PATH"
    # 종전엔 성공/실패 무관하게 exit 1 → 실제 결과를 전파한다.
    update_json_global "TXPWRLIMIT_PATH" "$TXPWRLIMIT_PATH" || exit 1
    # 새 정책의 .bak을 즉시 동기화하여 다음 부팅의 self-healing 사각지대 제거
    if [ -n "$TXPWRLIMIT_PATH" ] && [ -s "$TXPWRLIMIT_PATH" ]; then
        cp "$TXPWRLIMIT_PATH" "${TXPWRLIMIT_PATH}.bak" 2>/dev/null \
            && sync "${TXPWRLIMIT_PATH}.bak" 2>/dev/null || sync
    fi
    exit 0
    ;;
  ant)
    if [ "$2" == "internal" ] || [ "$2" == "0" ]; then
        wifi 0 down
        wifi 1 down
        sleep 1
        echo "set to internal antenna mode"
        echo 0 > /sys/class/leds/SW_SEL1/brightness
        echo 1 > /sys/class/leds/SW_SEL2/brightness
    elif [ "$2" == "external" ] || [ "$2" == "1" ]; then
        wifi 0 down
        wifi 1 down
        sleep 1
        echo "set to external antenna mode"
        echo 1 > /sys/class/leds/SW_SEL1/brightness
        echo 0 > /sys/class/leds/SW_SEL2/brightness
    else
        usage
    fi
    exit 0
    ;;
  stand)
    if [[ "$2" == "4" ]] || [[ "$2" == "n" ]] || [[ "$2" == "N" ]] || [[ "$2" == "ht" ]] || [[ "$2" == "HT" ]]; then
        VAL="n"
    elif [[ "$2" == "5" ]] || [[ "$2" == "ac" ]] || [[ "$2" == "AC" ]] || [[ "$2" == "vht" ]] || [[ "$2" == "VHT" ]]; then
        VAL="ac"
    elif [[ "$2" == "6" ]] || [[ "$2" == "ax" ]] || [[ "$2" == "AX" ]] || [[ "$2" == "he" ]] || [[ "$2" == "HE" ]]; then
        VAL="ax"
    else
        usage
    fi

    # 종전엔 성공/실패 무관하게 exit 1 → 실제 결과를 전파한다.
    update_json_global "STANDARD" "$VAL" || exit 1
    echo "STANDARD updated to $VAL in $WIFI_INIT_CONF_JSON"
    exit 0
    ;;
  net)
    # 'ip'가 전역(ip apply)과 per-iface(ip <addr>)로 중복돼 'wifi 0 ip apply'가
    # 주소 슬롯으로 떨어지던 문제를 네임스페이스 분리로 해소한다.
    case "${2:-}" in
      apply)
        # 지정 iface만 반영 — networkctl reload로 .network를 다시 읽고 해당 링크만
        # reconfigure한다. 전체 재시작(net restart)과 달리 다른 인터페이스가 끊기지
        # 않는다(무선으로 붙어 작업 중일 때 스스로 끊기는 사고 방지).
        NET_IFACE=""
        if [ -n "${3:-}" ]; then
            NET_IFACE=$(resolve_iface "$3") || {
                echo "Error: unknown iface '$3' (0|1|2|mlan0|mlan1|eth0)" >&2
                exit 1
            }
        fi
        # networkctl reconfigure는 systemd v244+ — 구버전에서 무작정 부르면 실패하므로
        # capability-gate 후 전체 재시작으로 폴백한다.
        if command -v networkctl >/dev/null 2>&1 \
            && networkctl --help 2>&1 | grep -q reconfigure; then
            if ! networkctl reload; then
                echo "Error: networkctl reload failed" >&2
                exit 1
            fi
            if [ -n "$NET_IFACE" ]; then
                if networkctl reconfigure "$NET_IFACE"; then
                    echo "$NET_IFACE reconfigured (other interfaces untouched)"
                    exit 0
                fi
                echo "Error: networkctl reconfigure $NET_IFACE failed" >&2
                exit 1
            fi
            echo "networkd config reloaded (no iface given — use 'wifi net apply <iface>' to reconfigure a link)"
            exit 0
        fi
        echo "Notice: networkctl reconfigure unavailable (systemd < 244) — falling back to full restart" >&2
        echo "Notice: all networkd-managed interfaces will be briefly interrupted" >&2
        if systemctl restart systemd-networkd; then
            echo "systemd-networkd restarted"
            exit 0
        fi
        echo "Error: systemd-networkd restart failed" >&2
        exit 1
        ;;
      restart)
        echo "Notice: all networkd-managed interfaces will be briefly interrupted" >&2
        echo "restarting systemd-networkd to apply ip configuration..."
        if systemctl restart systemd-networkd; then
            echo "systemd-networkd restarted"
            exit 0
        fi
        echo "Error: systemd-networkd restart failed" >&2
        exit 1
        ;;
      status)
        # persist(.network) vs runtime 대조 — 이 둘이 어긋나는 것이 ip/gt 오타의
        # 증상이다(파일에는 써졌지만 networkd가 무효값이라 버린 상태).
        NET_IFACE=""
        if [ -n "${3:-}" ]; then
            NET_IFACE=$(resolve_iface "$3") || {
                echo "Error: unknown iface '$3' (0|1|2|mlan0|mlan1|eth0)" >&2
                exit 1
            }
        fi
        for _if in ${NET_IFACE:-mlan0 mlan1 eth0}; do
            _nconf=$(find_network_conf "$_if") || _nconf=""
            _p_addr=""; _p_gw=""
            if [ -n "$_nconf" ]; then
                _p_addr=$(grep -oP '(?<=^Address=).*' "$_nconf" 2>/dev/null | head -1)
                _p_gw=$(grep -oP '(?<=^Gateway=).*' "$_nconf" 2>/dev/null | head -1)
            fi
            _r_addr=$(ip -4 addr show "$_if" 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}/\d+' | head -1)
            _r_gw=$(ip -4 route show default dev "$_if" 2>/dev/null | awk '{for (i=1;i<=NF;i++) if ($i=="via") {print $(i+1); exit}}')
            echo "$_if:"
            printf "  %-8s persist=%-22s runtime=%s\n" "Address" "${_p_addr:--}" "${_r_addr:--}"
            printf "  %-8s persist=%-22s runtime=%s\n" "Gateway" "${_p_gw:--}" "${_r_gw:--}"
            if [ -n "$_p_addr" ] && ! is_valid_ipv4_cidr "$_p_addr"; then
                echo "  WARN     persist된 Address '$_p_addr'는 유효하지 않음 — networkd가 이 줄을 버린다"
            fi
            if [ -n "$_p_gw" ] && ! is_valid_ipv4 "$_p_gw"; then
                echo "  WARN     persist된 Gateway '$_p_gw'는 유효하지 않음 — networkd가 이 줄을 버린다"
            fi
        done
        exit 0
        ;;
      *)
        echo "Usage: wifi net apply [iface] : .network 반영 — iface 지정 시 해당 링크만 (systemd<244면 전체 재시작 폴백)" >&2
        echo "       wifi net restart        : systemd-networkd 전체 재시작 (전체 networkd 관리 인터페이스 일시 중단)" >&2
        echo "       wifi net status [iface] : persist(.network) vs runtime 주소/게이트웨이 대조" >&2
        exit 1
        ;;
    esac
    ;;
  ip)
    # wifi N ip {addr}로 persist한 .network 설정을 실제 반영
    if [ "$2" = "apply" ]; then
        # net 네임스페이스로 이관됨 — 기존 스크립트/손버릇이 깨지지 않도록 별칭 유지.
        # 동작은 종전과 동일한 전체 재시작(= net restart)이다.
        echo "Notice: 'wifi ip apply'는 deprecated — 'wifi net restart'(동일 동작) 또는" >&2
        echo "        'wifi net apply <iface>'(해당 iface만 반영, 나머지 안 끊김)를 사용하세요." >&2
        echo "Notice: all networkd-managed interfaces will be briefly interrupted" >&2
        echo "restarting systemd-networkd to apply ip configuration..."
        if systemctl restart systemd-networkd; then
            echo "systemd-networkd restarted"
            exit 0
        else
            echo "Error: systemd-networkd restart failed" >&2
            exit 1
        fi
    else
        # usage()는 자체 exit 1 하지만, 명시적 exit로 의도를 못박는다
        usage
        exit 2
    fi
    ;;
  log)
    LOG_BASE=/var/log/cantops
    if [ "${2:-}" == "system" ]; then
        exec "$WIFI_LOGGER_CONTROL_SH" system "${3:-}"
    elif [ "$2" == "dump" ]; then
        if [ ! -d "$LOG_BASE" ]; then
            echo "Error: $LOG_BASE not found" >&2
            exit 1
        fi
        if ! command -v tar >/dev/null 2>&1; then
            echo "Error: tar not installed" >&2
            exit 1
        fi
        ARCHIVE="$(pwd)/cantops_log_$(date +%Y%m%d_%H%M%S).tar.gz"
        # --exclude: pwd가 cantops 하위일 때 생성 중인 아카이브 자신만 제외(기존 덤프는 보존).
        # pwd가 cantops 하위면 디렉터리 변경으로 tar가 rc=1(경고) 반환 — 아카이브는 정상이므로 허용,
        # rc>=2(fatal)만 실패로 처리한다.
        tar -czf "$ARCHIVE" --exclude="$(basename "$ARCHIVE")" -C "$(dirname "$LOG_BASE")" "$(basename "$LOG_BASE")"
        rc=$?
        if [ "$rc" -le 1 ]; then
            echo "Compressed $LOG_BASE to $ARCHIVE"
            exit 0
        else
            rm -f "$ARCHIVE"
            echo "Error: tar failed (rc=$rc)" >&2
            exit 1
        fi
    elif [ "$2" == "reset" ]; then
        # 전체 로그 초기화: 전역 + 모든 iface 로그 truncate. 파일 유지(inode 보존), 서비스 재시작 없음.
        # rsyslog 소유(cpu/logger/kern/sys)는 HUP으로 재오픈해야 sparse hole 없이 비워짐.
        # 대상 = cantops 로그 전체(logrotate.rsyslog 관리분 + rsyslog.conf omfile).
        # 전역 ping.log는 wifi_ping local1 라우팅으로 실재하나 logrotate 미등록이라 별도 포함.
        RESET_ALL=(
            "$LOG_BASE/kern.log"
            "$LOG_BASE/sys.log"
            "$LOG_BASE/logger.log"
            "$LOG_BASE/ui.log"
            "$LOG_BASE/summary/summary.log"
            "$LOG_BASE/cpu/cpu.log"
            "$LOG_BASE/ping.log"
        )
        for _if in mlan0 mlan1; do
            RESET_ALL+=(
                "$LOG_BASE/scan/$_if/ap.log"
                "$LOG_BASE/scan/$_if/freq.log"
                "$LOG_BASE/stat/$_if/stat.log"
                "$LOG_BASE/stat/$_if/snap.log"
                "$LOG_BASE/wpa/$_if/wpa.log"
                "$LOG_BASE/mgmt/$_if/mgmt.log"
                "$LOG_BASE/mgmt/$_if/gmgmt.log"
                "$LOG_BASE/ping/$_if/ping.log"
            )
        done
        _rn=0; _rmiss=()
        for _lf in "${RESET_ALL[@]}"; do
            if [ -f "$_lf" ]; then
                if : > "$_lf"; then _rn=$((_rn+1)); else echo "Warning: truncate failed: $_lf" >&2; fi
            else
                _rmiss+=("${_lf#"$LOG_BASE"/}")
            fi
        done
        # rsyslog 소유 로그(cpu/logger/kern/sys/ui/wpa/mgmt/ping)를 재오픈시켜 truncate 반영
        if systemctl kill -s HUP rsyslog 2>/dev/null; then
            echo "Reset $_rn log(s) (rsyslog reopened, HUP)"
        else
            echo "Reset $_rn log(s)"
            echo "Warning: rsyslog HUP failed — rsyslog-owned logs may need manual reopen" >&2
        fi
        [ ${#_rmiss[@]} -gt 0 ] && echo "Skipped ${#_rmiss[@]} missing: ${_rmiss[*]}"
        exit 0
    else
        usage
    fi
    ;;
  backup)
    BACKUP_DIR=/var/log/cantops/backup
    echo "backup to $BACKUP_DIR..."
    /usr/local/scripts/backup.sh "$BACKUP_DIR"
    exit $?
    ;;
  *)
    usage
    ;;
esac

if [ "$IFACE" != "mlan0" ] && [ "$IFACE" != "mlan1" ] && [ "$IFACE" != "eth0" ]; then
    usage
fi

case "$2" in
  info)
    show_info "$IFACE"
    exit 0
    ;;
  "")
    show_info "$IFACE"
    exit 0
    ;;
  restart)
    echo "restart WPA service for $IFACE..."
    wifi_scan_transition_lock_acquire "$IFACE" \
        || { echo "Error: scan/association transition busy for $IFACE" >&2; exit 6; }
    # 이미 accepted된 native scan은 먼저 취소한다. supplicant wedge로 quiesce가
    # 불가능해도 heavy recovery 자체를 막지 않는다; FD7 아래 stop이 scan owner를 종료한다.
    wifi_wpa_abort_scan_quiesce "$IFACE" \
        || logger -p local0.warning "[$tag:$LINENO] [$IFACE] native scan did not quiesce before forced WPA restart"
    wifi_wpa_run_child systemctl stop "wpa_supplicant@$IFACE" \
        || { echo "Error: failed to stop WPA service for $IFACE" >&2; exit 7; }
    wifi_wpa_run_child sleep 1
    wifi_wpa_run_child systemctl start "wpa_supplicant@$IFACE" \
        || { echo "Error: failed to start WPA service for $IFACE" >&2; exit 7; }
    wifi_wpa_wait_completed_under_transition "$IFACE" \
        || { echo "Error: WPA association not completed after restart for $IFACE" >&2; exit 8; }
    exit 0
    ;;
  start | up)
    echo "Starting WPA service for $IFACE..."
    wifi_scan_transition_lock_acquire "$IFACE" \
        || { echo "Error: scan/association transition busy for $IFACE" >&2; exit 6; }
    # start/up is idempotent for an already-running supplicant.  The activity
    # decision is made under FD7, and this path deliberately leaves any native
    # scan and the current association epoch untouched.  Recovery callers use
    # `wifi connect` (lightweight) or `wifi restart` (heavyweight) instead.
    if wifi_wpa_run_child systemctl is-active --quiet "wpa_supplicant@$IFACE"; then
        echo "WPA service already active for $IFACE; scan/association unchanged"
        exit 0
    fi
    wifi_wpa_abort_scan_quiesce "$IFACE" \
        || logger -p local0.warning "[$tag:$LINENO] [$IFACE] native scan quiesce unavailable before WPA start"
    wifi_wpa_run_child systemctl start "wpa_supplicant@$IFACE" \
        || { echo "Error: failed to start WPA service for $IFACE" >&2; exit 7; }
    wifi_wpa_wait_completed_under_transition "$IFACE" \
        || { echo "Error: WPA association not completed after start for $IFACE" >&2; exit 8; }
    exit 0
    ;;
  stop | down)
    echo "Stopping WPA service for $IFACE..."
    wifi_scan_transition_lock_acquire "$IFACE" \
        || { echo "Error: scan/association transition busy for $IFACE" >&2; exit 6; }
    wifi_wpa_abort_scan_quiesce "$IFACE" \
        || logger -p local0.warning "[$tag:$LINENO] [$IFACE] native scan did not quiesce before forced WPA stop"
    wifi_wpa_run_child systemctl stop "wpa_supplicant@$IFACE" \
        || { echo "Error: failed to stop WPA service for $IFACE" >&2; exit 7; }
    exit 0
    ;;
  status)
    systemctl status wpa_supplicant@$IFACE
    ;;
  br)
    if [ "$3" == "0" ] || [ "$3" == "down" ] || [ "$3" == "stop" ]; then
        echo "stop bridge for $IFACE..."
        systemctl stop wifi_bridge@$IFACE
    elif [ "$3" == "1" ] || [ "$3" == "up" ] || [ "$3" == "start" ]; then
        echo "start bridge for $IFACE..."
        systemctl start wifi_bridge@$IFACE
    elif [ "$3" == "restart" ]; then
        echo "restart bridge for $IFACE..."
        systemctl restart wifi_bridge@$IFACE
    elif [ "$3" == "status" ]; then
        _bridge_status "$IFACE"
    elif [ "$3" == "profile" ]; then
        _bridge_profile "${4:-}" "${5:-}"
    elif [ "$3" == "route" ]; then
        _bridge_route "${4:-}" "${5:-}" "$IFACE"
    elif [ "$3" == "moal" ] || [ "$3" == "pcap" ] || [ "$3" == "tpacket" ]; then
        if [ "$IFACE" != "mlan0" ] && [ "$IFACE" != "mlan1" ]; then
            echo "Error: br {moal|pcap|tpacket} supports mlan0/mlan1 only" >&2
            exit 1
        fi
        if [ ! -f "$WIFI_INIT_CONF_JSON" ]; then
            echo "Error: $WIFI_INIT_CONF_JSON not found" >&2
            exit 1
        fi
        if ! command -v jq >/dev/null 2>&1; then
            echo "Error: jq not installed" >&2
            exit 1
        fi
        if jq --arg i "$IFACE" --arg e "$3" '.wbridge.bridge_iface = $i | .wbridge.engine = $e' "$WIFI_INIT_CONF_JSON" > "${WIFI_INIT_CONF_JSON}.tmp"; then
            mv "${WIFI_INIT_CONF_JSON}.tmp" "$WIFI_INIT_CONF_JSON"
            echo "wbridge updated: bridge_iface=$IFACE engine=$3 (다음 wifi up/부팅 시 적용)"
        else
            rm -f "${WIFI_INIT_CONF_JSON}.tmp"
            echo "Error: wbridge JSON update failed" >&2
            exit 1
        fi
    else
        usage
    fi
    ;;
  roam)
    # 같은 SSID 안의 BSS 전환 전용(wpa_cli roam, 무중단). 다른 SSID 로의 망 전환은
    # 재연결을 동반하므로 `wifi <iface> connect <ssid>` 의 역할이고 여기서 하지 않는다.
    # wifi 0 roam            → 현재 SSID의 AP 리스트만 표시
    # wifi 0 roam 0          → 현재 AP 제외 최고 RSSI로 자동 로밍
    # wifi 0 roam 1~N        → RSSI 순서 N번째 AP로 로밍
    # wifi 0 roam th         → 로밍 RSSI 임계값 표시 (2G/5G)
    # wifi 0 roam th 5G -70  → roaming.DEFAULT_TH_5G=-70 (persist) + SIGHUP 즉시 반영(무재시작)
    # wifi 0 roam th 2G -70  → roaming.DEFAULT_TH_2G=-70
    # wifi 0 roam diff       → 후보 AP 최소 RSSI 이득(DIFF_TH) 표시
    # wifi 0 roam diff 3     → roaming.DIFF_TH=3 (persist) + SIGHUP 즉시 반영(무재시작)
    # wifi 0 roam gate       → good-signal 리셋 게이트 현재값 표시
    # wifi 0 roam gate on    → GOOD_SIGNAL_RESET_GATE.enable=true (현장 kill-switch)
    ROAM_ARG="${3:-}"
    if [ "$ROAM_ARG" = "th" ]; then
        if [ "$IFACE" = "eth0" ]; then
            echo "Error: roam th is for wlan interfaces (mlan0/mlan1)" >&2
            exit 1
        fi
        # 표시 경로도 파일 부재를 "unset"으로 오인하지 않도록 선확인 (파손 시 jq 파스 에러는 가시화)
        if [ ! -f "$WIFI_INIT_CONF_JSON" ]; then
            echo "Error: $WIFI_INIT_CONF_JSON not found" >&2
            exit 1
        fi
        BAND=$(echo "${4:-}" | tr 'a-z' 'A-Z')
        case "$BAND" in
            2G|2) TH_KEY="DEFAULT_TH_2G" ;;
            5G|5) TH_KEY="DEFAULT_TH_5G" ;;
            "")
                # 밴드 미지정 → 현재값 모두 표시
                th2=$(jq -r ".${IFACE}.roaming.DEFAULT_TH_2G // \"unset\"" "$WIFI_INIT_CONF_JSON")
                th5=$(jq -r ".${IFACE}.roaming.DEFAULT_TH_5G // \"unset\"" "$WIFI_INIT_CONF_JSON")
                echo "$IFACE roam threshold: 2G=${th2} 5G=${th5} (dBm)"
                exit 0
                ;;
            *)
                echo "Usage: wifi {0|1|mlan0|mlan1} roam th {2G|5G} [rssi]" >&2
                exit 1
                ;;
        esac
        RSSI="${5:-}"
        if [ -z "$RSSI" ]; then
            cur=$(jq -r ".${IFACE}.roaming.${TH_KEY} // \"unset\"" "$WIFI_INIT_CONF_JSON")
            echo "$IFACE roaming.${TH_KEY} = ${cur} (dBm)"
            exit 0
        fi
        # 음수 정수 -100..-1 만 허용 (양수/비정수/범위밖 거부)
        if ! echo "$RSSI" | grep -qE '^-[0-9]+$' || [ "$RSSI" -lt -100 ] || [ "$RSSI" -gt -1 ]; then
            echo "Error: rssi must be a negative integer in -100..-1 (got '$RSSI')" >&2
            exit 1
        fi
        # 표시용 이전값도 조회 실패를 구분한다 — 파손 JSON 에서 빈 값이 되면 출력이
        # " -> -70 (persist)" 처럼 이전값 없이 찍혀 혼란스럽다(roam diff 와 동일 패턴).
        if ! OLD=$(jq -r ".${IFACE}.roaming.${TH_KEY} // \"unset\"" "$WIFI_INIT_CONF_JSON"); then
            echo "Error: failed to read ${IFACE}.roaming.${TH_KEY} from $WIFI_INIT_CONF_JSON" >&2
            exit 1
        fi
        update_json_roaming_int "$IFACE" "$TH_KEY" "$RSSI" || exit 1
        echo "$IFACE roaming.${TH_KEY}: ${OLD} -> ${RSSI} (persist)"
        # 종전엔 여기서 wpa_supplicant conf 의 `#!TH_` 마커가 JSON 을 덮어쓴다고 경고했다.
        # wifi_roam.py 가 그 마커를 더는 읽지 않으므로(임계 소스 = JSON 단일) 경고를 제거한다
        # — 남겨두면 이제는 사실이 아닌 안내가 된다.
        # 반영 실패(SIGHUP+restart 모두 실패)는 persist 성공과 별개로 비0 종료 —
        # 자동화가 "저장됐으나 미반영" 상태를 감지할 수 있어야 한다.
        reload_roam_daemon "$IFACE" || exit 1
        exit 0
    fi
    if [ "$ROAM_ARG" = "diff" ]; then
        if [ "$IFACE" = "eth0" ]; then
            echo "Error: roam diff is for wlan interfaces (mlan0/mlan1)" >&2
            exit 1
        fi
        # 표시 경로도 파일 부재를 "unset"으로 오인하지 않도록 선확인 (파손 시 jq 파스 에러는 가시화)
        if [ ! -f "$WIFI_INIT_CONF_JSON" ]; then
            echo "Error: $WIFI_INIT_CONF_JSON not found" >&2
            exit 1
        fi
        DIFF="${4:-}"
        if [ -z "$DIFF" ]; then
            # jq 실패(파손 JSON·읽기 불가)를 "unset" 과 구분한다 — 실패인데 빈 값을
            # 정상 출력하고 exit 0 이면 조회 실패를 호출자가 감지할 수 없다.
            if ! cur=$(jq -r ".${IFACE}.roaming.DIFF_TH // \"unset\"" "$WIFI_INIT_CONF_JSON"); then
                echo "Error: failed to read ${IFACE}.roaming.DIFF_TH from $WIFI_INIT_CONF_JSON" >&2
                exit 1
            fi
            echo "$IFACE roaming.DIFF_TH = ${cur} (dB)"
            # 조회 시에도 위험 설정이면 환기 — 시험 후 되돌리지 않은 상태를 알아채게 한다.
            if [ "$cur" -le 1 ] 2>/dev/null; then
                echo "Warning: DIFF_TH=${cur} is a test-only setting (roam thrashing); revert after testing" >&2
            fi
            exit 0
        fi
        # 0..30 정수만 허용 (음수/비정수/범위밖 거부). DIFF_TH 는 '현재 AP 대비 최소 RSSI
        # 이득'이라 음수는 의미가 없고(더 나쁜 AP로 로밍), 30 이상은 사실상 로밍 금지다.
        if ! echo "$DIFF" | grep -qE '^[0-9]+$' || [ "$DIFF" -gt 30 ]; then
            echo "Error: diff must be an integer in 0..30 (got '$DIFF')" >&2
            exit 1
        fi
        # 표시용 이전값도 조회 실패를 구분한다 — 파손 JSON 에서 빈 값이 되면 출력이
        # " -> 3 (persist)" 처럼 이전값 없이 찍혀 혼란스럽다(조회 경로와 동일 패턴).
        if ! OLD=$(jq -r ".${IFACE}.roaming.DIFF_TH // \"unset\"" "$WIFI_INIT_CONF_JSON"); then
            echo "Error: failed to read ${IFACE}.roaming.DIFF_TH from $WIFI_INIT_CONF_JSON" >&2
            exit 1
        fi
        update_json_roaming_int "$IFACE" "DIFF_TH" "$DIFF" || exit 1
        echo "$IFACE roaming.DIFF_TH: ${OLD} -> ${DIFF} (persist)"
        # 반영 실패(SIGHUP+restart 모두 실패)는 persist 성공과 별개로 비0 종료 —
        # 자동화가 "저장됐으나 미반영" 상태를 감지할 수 있어야 한다.
        reload_roam_daemon "$IFACE" || exit 1
        # 낮은 값은 측정 요동(동일 AP 연속 관측 |ΔRSSI| p90=1dB)과 구분되지 않는 이득으로도
        # 로밍해 진동이 급증한다 — 실측 재생 기준 DIFF_TH=1 은 8 대비 로밍 2.28배, 10초 미만
        # 간격 로밍 0→54건. 시험용 설정이므로 되돌리기를 잊지 않도록 경고한다.
        # 반영까지 성공한 뒤에 출력한다 — 미반영으로 종료하는 경우엔 이 경고가 오히려
        # "적용됐다"는 오해를 준다.
        if [ "$DIFF" -le 1 ]; then
            echo "Warning: DIFF_TH=${DIFF} is a test-only setting (roam thrashing); revert after testing" >&2
        fi
        exit 0
    fi
    if [ "$ROAM_ARG" = "gate" ]; then
        # good-signal 리셋 게이트 — 신호 양호 분기에서 backoff streak 리셋을
        # "위치가 실제로 변했을 때만" 허용한다(GOOD_SIGNAL_RESET_GATE).
        if [ "$IFACE" = "eth0" ]; then
            echo "Error: roam gate is for wlan interfaces (mlan0/mlan1)" >&2
            exit 1
        fi
        if [ ! -f "$WIFI_INIT_CONF_JSON" ]; then
            echo "Error: $WIFI_INIT_CONF_JSON not found" >&2
            exit 1
        fi
        GATE_SUB="${4:-}"
        if [ -z "$GATE_SUB" ]; then
            # JSON 에 키가 없으면 데몬은 wifi_roam.py 의 기본값을 쓴다. 종전엔 그 경우를
            # "unset" 으로만 찍어 **실제 적용값을 알 수 없었다**(gate on 은 enable 만 넣으므로
            # 흔한 상태다). 적용 중인 값과 그 출처를 함께 보여준다.
            if ! GEN=$(gate_effective "$IFACE" enable "$GATE_DEF_ENABLE"); then
                echo "Error: failed to read ${IFACE}.roaming.GOOD_SIGNAL_RESET_GATE from $WIFI_INIT_CONF_JSON" >&2
                exit 1
            fi
            echo "$IFACE roam gate: enable=${GEN} delta_db=${GATE_FIXED_DELTA_DB}(fixed) post_roam_grace_sec=${GATE_FIXED_GRACE_SEC}(fixed)"
            exit 0
        fi
        case "$GATE_SUB" in
            on|off)
                [ "$GATE_SUB" = "on" ] && GVAL=true || GVAL=false
                update_json_roaming_section "$IFACE" "GOOD_SIGNAL_RESET_GATE" "enable" "$GVAL" || exit 1
                echo "$IFACE roaming.GOOD_SIGNAL_RESET_GATE.enable = ${GVAL} (persist)"
                ;;
            *)
                echo "Usage: wifi {0|1|mlan0|mlan1} roam gate [on|off] (delta=2dB, grace=40s fixed)" >&2
                exit 1
                ;;
        esac
        reload_roam_daemon "$IFACE" || exit 1
        exit 0
    fi
    if [ -z "$ROAM_ARG" ]; then
        python3 /usr/local/logger/passive_roam.py --iface $IFACE
    else
        python3 /usr/local/logger/passive_roam.py $ROAM_ARG --iface $IFACE
    fi
    ;;
  stat)
    case "${3:-}" in
      reset)
        TARGET_MAC="${4:-}"
        if [ -n "$TARGET_MAC" ]; then
            if ! [[ "$TARGET_MAC" =~ ^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$ ]]; then
                echo "Error: invalid MAC address '$TARGET_MAC'" >&2; exit 1
            fi
            touch "/tmp/wifi_stat_reset_${TARGET_MAC}"
            echo "stat reset requested for MAC $TARGET_MAC on $IFACE"
        else
            touch /tmp/wifi_stat_init_f
            echo "stat reset requested for all records on $IFACE"
        fi
        ;;
      interval)
        if [ -z "${4:-}" ]; then
            echo "Usage: wifi <iface> stat interval <seconds>"
            exit 1
        fi
        INTERVAL_SEC="$4"
        # 0은 매 iteration마다 통계를 리셋시켜 stat.log가 영영 누적되지 않는다
        # ("positive"라고 안내하면서 0을 통과시키던 버그). 10#은 08 같은 8진수 오인 방지.
        if ! [[ "$INTERVAL_SEC" =~ ^[0-9]+$ ]] || [ "$((10#$INTERVAL_SEC))" -lt 1 ]; then
            echo "Error: interval must be a positive integer (seconds)" >&2
            exit 1
        fi
        INTERVAL_SEC=$((10#$INTERVAL_SEC))
        jq --argjson v "$INTERVAL_SEC" \
            --arg iface "$IFACE" \
            '.[$iface].logger.stat_reset_interval_sec = $v' \
            "$WIFI_INIT_CONF_JSON" > "${WIFI_INIT_CONF_JSON}.tmp"
        mv "${WIFI_INIT_CONF_JSON}.tmp" "$WIFI_INIT_CONF_JSON"
        echo "stat_reset_interval_sec set to ${INTERVAL_SEC}s for $IFACE in $WIFI_INIT_CONF_JSON"
        ;;
      *)
        echo "Usage: wifi <iface> stat reset [mac]"
        echo "       wifi <iface> stat interval <seconds>"
        exit 1
        ;;
    esac
    ;;
  mon)
    shift 2
    MON_ARGS=""
    MON_COMPACT=""
    MON_INTERVAL="1"
    while [ $# -gt 0 ]; do
        case "$1" in
            c|compact)   MON_COMPACT="--compact" ;;
            [0-9]*)      MON_INTERVAL="$1" ;;
            -*)          MON_ARGS="$MON_ARGS $1" ;;
            *)           MON_ARGS="$MON_ARGS $1" ;;
        esac
        shift
    done
    echo "monitor link for $IFACE with interval $MON_INTERVAL sec ${MON_COMPACT:+(compact)}"
    python3 /usr/local/logger/wifi_link_monitor.py $IFACE --interval $MON_INTERVAL $MON_COMPACT $MON_ARGS
    ;;
  txpwr | txpwrlimit)
    if [ "$3" == "no" ] || [ "$3" == "0" ]; then
        echo "no txpwrlimit for $IFACE"
        CONF=""; TXPWR_PERSIST="none"
    elif [ "$3" == "default" ] || [ "$3" == "1" ]; then
        echo "default txpwrlimit for $IFACE"
        CONF=/lib/firmware/cts/txpwrlimit_cfg_9098.conf; TXPWR_PERSIST="$CONF"
    elif [ "$3" == "low" ] || [ "$3" == "2" ]; then
        echo "low txpwrlimit for $IFACE"
        CONF=/lib/firmware/cts/txpwrlimit_cfg_9098_low.conf; TXPWR_PERSIST="$CONF"
    elif [ "$3" == "test" ] || [ "$3" == "3" ]; then
        echo "test txpwrlimit for $IFACE"
        CONF=/lib/firmware/cts/txpwrlimit_cfg_9098_org.conf; TXPWR_PERSIST="$CONF"
    elif [[ "$3" == *.conf ]]; then
        # cp 실패를 무시하면 없는 파일을 가리키는 송신출력 정책이 persist된다.
        if [ ! -f "$3" ]; then
            echo "Error: txpwrlimit conf not found: '$3'" >&2
            exit 1
        fi
        _txpwr_basename=$(basename "$3")
        # 이미 /lib/firmware/cts 안의 파일이면 cp가 "same file"로 1을 반환 → -ef로 제외.
        if [ ! "$3" -ef "/lib/firmware/cts/$_txpwr_basename" ] \
            && ! cp "$3" "/lib/firmware/cts/$_txpwr_basename"; then
            echo "Error: failed to stage '$3' into /lib/firmware/cts" >&2
            exit 1
        fi
        CONF="/lib/firmware/cts/$_txpwr_basename"; TXPWR_PERSIST="$CONF"
    else
        usage
    fi
    if [ -n "$CONF" ]; then
        echo "txpwrlimit set to $CONF for $IFACE"
        mlanutl $IFACE hostcmd $CONF txpwrlimit_2g_cfg_set > /dev/null 2>&1
        mlanutl $IFACE hostcmd $CONF txpwrlimit_5g_cfg_set_sub0 > /dev/null 2>&1
        mlanutl $IFACE hostcmd $CONF txpwrlimit_5g_cfg_set_sub1 > /dev/null 2>&1
        mlanutl $IFACE hostcmd $CONF txpwrlimit_5g_cfg_set_sub2 > /dev/null 2>&1
        mlanutl $IFACE hostcmd $CONF txpwrlimit_5g_cfg_set_sub3 > /dev/null 2>&1
    fi
    update_json_iface "$IFACE" "TXPWRLIMIT_PATH" "$TXPWR_PERSIST"
    echo "TXPWRLIMIT_PATH persisted as '$TXPWR_PERSIST' for $IFACE"
    # 새 정책의 .bak을 즉시 동기화하여 다음 부팅 self-healing 사각지대 제거
    if [ -n "$CONF" ] && [ -s "$CONF" ]; then
        cp "$CONF" "${CONF}.bak" 2>/dev/null && sync "${CONF}.bak" 2>/dev/null || sync
    fi
    ;;
  config)
    # 임의 moal 파라미터를 쓰는 탈출구라 키 allowlist는 두지 않는다(미지의 키는
    # 드라이버 conf 파서가 조용히 무시하므로 부팅을 깨지 않는다). 형식 오타만 잡는다.
    _CFG_KEY="${3:-}"; _CFG_VAL="${4:-}"
    if [ -z "$_CFG_KEY" ] || [ -z "$_CFG_VAL" ]; then
        echo "Usage: wifi <iface> config <key> <value>" >&2
        exit 1
    fi
    if ! [[ "$_CFG_KEY" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]]; then
        echo "Error: invalid config key '$_CFG_KEY' (expected identifier: [a-zA-Z_][a-zA-Z0-9_]*)" >&2
        exit 1
    fi
    # 값의 개행 거부 — wifi_config.py가 "{key}={val}\n"으로 그대로 삽입하므로,
    # 값에 개행이 있으면 임의의 moal 파라미터 줄이 추가로 박힌다(아래 mfg_mode
    # 차단을 값 쪽으로 우회하는 경로).
    case "$_CFG_VAL" in
        *[$'\n\r']*) echo "Error: config value에 개행 문자 불가" >&2; exit 1 ;;
    esac
    # mfg_mode는 fw_name과 반드시 짝을 맞춰야 한다. 단독으로 켜면 MFG 프로파일
    # 가드(wifi_checker/services/apply_enabled/reboot_policy)가 정상 펌웨어 상태에서
    # 발동해 STA가 조용히 죽는다 — 짝을 맞춰주는 전용 커맨드로 유도.
    if [ "$_CFG_KEY" == "mfg_mode" ]; then
        echo "Error: use 'wifi $1 mfg {on|off}' instead — setting mfg_mode alone desyncs fw_name" >&2
        exit 1
    fi
    echo "config $_CFG_KEY value set to $_CFG_VAL for $IFACE"
    python3 /usr/local/logger/wifi_config.py "$1" "$_CFG_KEY" "$_CFG_VAL"
    ;;
  mac)
    if [ "$3" == "base" ] || [ "$3" == "0" ]; then
        # write_mac.sh:26도 같은 검사를 하지만, 오타는 여기서 먼저 잡아야
        # 아래 성공 메시지가 거짓말을 하지 않는다.
        if ! is_valid_mac "${4:-}"; then
            echo "Error: invalid MAC address '${4:-}' (expected unicast XX:XX:XX:XX:XX:XX)" >&2
            exit 1
        fi
        # 성공 보고는 실제 기록 이후에 — write_mac.sh는 .link 기록 실패 등으로도
        # 1을 반환하므로 그 결과를 삼키지 않는다.
        if ! /usr/local/scripts/write_mac.sh "$IFACE" "$4"; then
            echo "Error: write_mac.sh failed for $IFACE (base mac not written)" >&2
            exit 1
        fi
        echo "base mac set to $4 for $IFACE"
    elif [ "$3" == "target" ] || [ "$3" == "1" ]; then
        if [ "$IFACE" == "eth0" ]; then
            echo "Error: eth0 does not support target mac"
            exit 1
        fi
        # 이 경로는 무검증이었다: 빈 값은 spoof dynamic과 같은 상태를 만들고,
        # 쓰레기 값은 wifi_mac_set.py(무검증)를 타고 wifi_mod_para__.conf의
        # mac_addr= 로 흘러들어 드라이버 설정을 오염시킨다.
        if ! is_valid_mac "${4:-}"; then
            echo "Error: invalid MAC address '${4:-}' (expected unicast XX:XX:XX:XX:XX:XX)" >&2
            echo "       동적 spoofing으로 되돌리려면: wifi $1 spoof dynamic" >&2
            exit 1
        fi
        echo "target mac set to $4 for $IFACE"
        update_json_mac "$IFACE" "target" "$4"
    else
        usage
    fi
    ;;
  cal)
    CAL_VAL="$3"
    if [[ "$CAL_VAL" == *.conf ]]; then
        CAL_VAL=$(stage_custom_calibration "$CAL_VAL") || exit 1
    elif [[ "$CAL_VAL" == "2" ]]; then
        CAL_VAL="cts/WlanCalData_ext_RD.conf"
    elif [[ "$CAL_VAL" == "1" ]]; then
        CAL_VAL="cts/WlanCalData_ext.conf"
    elif [[ "$CAL_VAL" == "0" ]] || [[ "$CAL_VAL" == "none" ]] || [[ "$CAL_VAL" == "None" ]]; then
        CAL_VAL="none"
    else
        usage
    fi
    update_json_iface "$IFACE" "CAL_DATA_CFG" "$CAL_VAL"
    echo "CAL_DATA_CFG updated to '$CAL_VAL' for $IFACE in $WIFI_INIT_CONF_JSON"
    ;;
  mfg)
    BUS_TYPE=$(jq -r '.global.BUS_TYPE // "pcie"' "$WIFI_INIT_CONF_JSON" 2>/dev/null || echo "pcie")
    BT=$(jq -r '.global.BLUETOOTH.enable // false' "$WIFI_INIT_CONF_JSON" 2>/dev/null || echo "false")
    if [ "$3" == "off" ] || [ "$3" == "0" ]; then
        if [ "$BUS_TYPE" == "sdio" ]; then
            if [ "$BT" == "true" ]; then FW_NAME="cts/sduart9098_combo_v1.bin"
            else FW_NAME="cts/sd9098_wlan_v1.bin"; fi
        else
            if [ "$BT" == "true" ]; then FW_NAME="cts/pcieuart9098_combo_v1.bin"
            else FW_NAME="cts/pcie9098_wlan_v1.bin"; fi
        fi
        echo "mfg_mode set to off for $IFACE (BUS_TYPE=$BUS_TYPE, BLUETOOTH=$BT)"
        python3 /usr/local/logger/wifi_config.py $1 mfg_mode 0
        python3 /usr/local/logger/wifi_config.py $1 fw_name "$FW_NAME"
    elif [ "$3" == "on" ] || [ "$3" == "1" ]; then
        if [ "$BUS_TYPE" == "sdio" ]; then
            if [ "$BT" == "true" ]; then FW_NAME="cts/sduart9098_combo.bin"
            else FW_NAME="cts/sd9098_wlan.bin"; fi
        else
            if [ "$BT" == "true" ]; then FW_NAME="cts/pcieuart9098_combo.bin"
            else FW_NAME="cts/pcie9098_wlan.bin"; fi
        fi
        echo "mfg_mode set to on for $IFACE (BUS_TYPE=$BUS_TYPE, BLUETOOTH=$BT)"
        python3 /usr/local/logger/wifi_config.py $1 mfg_mode 1
        python3 /usr/local/logger/wifi_config.py $1 fw_name "$FW_NAME"
    else
        usage
    fi
    ;;
  spoof)
    if [ "$3" == "dynamic" ] || [ "$3" == "0" ]; then
        echo "spoofing mode set to dynamic for $IFACE"
        update_json_mac "$IFACE" "target" ""
    elif [ "$3" == "static" ] || [ "$3" == "1" ]; then
        if [ ! -f /tmp/eth0_client_mac ]; then
            echo "Error: /tmp/eth0_client_mac not found"
            exit 1
        fi
        SPOOF_MAC=$(cat /tmp/eth0_client_mac)
        echo "spoofing mode set to static for $IFACE (mac=$SPOOF_MAC)"
        update_json_mac "$IFACE" "target" "$SPOOF_MAC"
    else
        usage
    fi
    ;;
  freq)
    set -euo pipefail
    shift 2
    CONF="$WPA_CONF_DIR/wpa_supplicant-${IFACE}.conf"
    if [ ! -f "$CONF" ]; then echo "not found: $CONF" >&2; exit 1; fi
    wifi_wpa_conf_lock_acquire "$IFACE" \
        || { echo "Error: failed to lock $CONF" >&2; exit 1; }
    FREQS=()
    # to_freq_mhz는 비숫자 토큰을 그대로 되돌려주고 1000 미만 정수를 5000+5*v로
    # 매핑한다 → 재검사가 없으면 freq_list=abc(파싱 실패로 주파수 핀 해제) 나
    # freq_list=6000(존재하지 않는 채널)이 conf에 박힌 채 부팅을 넘긴다.
    for arg in "$@"; do
        _f="$(wifi_wpa_child_call to_freq_mhz_checked "$arg")" || exit 1
        FREQS+=( "$_f" )
    done
    [ ${#FREQS[@]} -eq 0 ] && { echo "configure freq not exist" >&2; exit 1; }
    FREQ_STR="${FREQS[*]}"
    TMP_FILE="$(wifi_wpa_child_exec mktemp)"
    trap 'wifi_wpa_run_child rm -f "$TMP_FILE"; wifi_wpa_run_child sync 2>/dev/null || true' EXIT
    if ! wifi_wpa_run_child_call wifi_wpa_conf_render_canonical "$CONF" "$TMP_FILE" "$FREQ_STR"; then
        echo "Error: failed to render canonical frequency policy in $CONF" >&2
        exit 1
    fi
    wifi_wpa_run_child_call safe_install_sync "$TMP_FILE" "$CONF"
    echo "global/block freq_list configured $FREQ_STR in $CONF"
    ;;
  ssid)
    set -euo pipefail
    shift 2
    CONF="$WPA_CONF_DIR/wpa_supplicant-${IFACE}.conf"
    if [ ! -f "$CONF" ]; then echo "not found: $CONF" >&2; exit 1; fi
    wifi_wpa_conf_lock_acquire "$IFACE" \
        || { echo "Error: failed to lock $CONF" >&2; exit 1; }
    if [ $# -lt 1 ]; then echo "usage: wifi <iface> ssid <NEW_SSID>" >&2; exit 1; fi
    if wifi_wpa_run_child_call wifi_wpa_conf_is_multi_topology "$IFACE" "$CONF"; then
        echo "Error: $CONF 는 Mode A 또는 다중 network topology입니다." >&2
        echo "       SSID 전환은 boot-latched owner policy에 따라 자동 처리됩니다." >&2
        echo "       SSID 없이 'wifi <iface> connect'를 실행하면 현재 network 재연결을 요청합니다." >&2
        exit 1
    fi
    NEW_SSID="$1"
    SSID_CONF_VALUE=$(wifi_wpa_child_call wifi_ssid_to_conf_value "$NEW_SSID") || {
        echo "Error: SSID must be valid UTF-8, 1-32 bytes, without C0 controls or DEL" >&2
        exit 1
    }
    # busybox awk가 ENVIRON 미지원이면 SSID가 ""로 silent 손상(awk exit 0 → 성공 오인)
    # → 적용 전 ENVIRON 지원을 사전 검증(connect 경로와 동일 규약).
    SSID_ENVIRON_PROBE=ok wifi_wpa_run_child awk 'BEGIN { exit(ENVIRON["SSID_ENVIRON_PROBE"] == "ok" ? 0 : 1) }' </dev/null \
        || { echo "Error: awk lacks ENVIRON support — cannot apply ssid safely" >&2; exit 1; }
    TMP_FILE="$(wifi_wpa_child_exec mktemp)"
    # active ssid= → 치환 / #ssid=(주석)만 있으면 주석 해제 후 설정 / 둘 다 없으면 network 블록 끝에 append.
    # (블록 인지 방식은 connect 경로와 동일 — 주석처리된 설정도 확실히 반영. 블록당 done 1회.)
    # 직렬화는 wifi_ssid_to_conf_value 가 고른다 — 안전하면 따옴표(사람이 읽는다),
    # SSID 에 `"` 가 있으면 hex(그 문자가 인용 패리티를 깨 `#` 이 주석이 된다).
    if WIFI_NEW_SSID_CONF="$SSID_CONF_VALUE" wifi_wpa_run_child awk '
        BEGIN { in_net = 0; changed = 0; new_ssid = ENVIRON["WIFI_NEW_SSID_CONF"] }
        /^[[:space:]]*network[[:space:]]*=[[:space:]]*\{/ { in_net = 1; done = 0; print; next }
        in_net && /^[[:space:]]*\}/ {
            if (!done) { print "    ssid=" new_ssid; changed = 1; done = 1 }
            in_net = 0; print; next
        }
        in_net && /^[[:space:]]*ssid[[:space:]]*=/ {
            if (!done) { print "    ssid=" new_ssid; changed = 1; done = 1 } next
        }
        in_net && /^[[:space:]]*#[[:space:]]*ssid[[:space:]]*=/ {
            if (!done) { print "    ssid=" new_ssid; changed = 1; done = 1; next }
            print; next
        }
        /^[[:space:]]*#/ { print; next }
        { print }
        END { if (!changed) exit 1 }
    ' "$CONF" > "$TMP_FILE"; then
        wifi_wpa_run_child_call safe_install_sync "$TMP_FILE" "$CONF"
        wifi_wpa_run_child rm -f "$TMP_FILE"
        echo "ssid changed to \"$NEW_SSID\" in $CONF"
    else echo "no network={ block found, nothing changed in $CONF" >&2; wifi_wpa_run_child rm -f "$TMP_FILE"; exit 1; fi
    ;;
  psk)
    set -euo pipefail
    shift 2
    CONF="$WPA_CONF_DIR/wpa_supplicant-${IFACE}.conf"
    if [ ! -f "$CONF" ]; then echo "not found: $CONF" >&2; exit 1; fi
    wifi_wpa_conf_lock_acquire "$IFACE" \
        || { echo "Error: failed to lock $CONF" >&2; exit 1; }
    if [ $# -lt 1 ]; then echo "usage: wifi <iface> psk <NEW_PSK>" >&2; exit 1; fi
    NEW_PSK="$1"
    # wpa_supplicant는 따옴표 passphrase를 8..63자로 제한한다. 벗어나면 해당 network
    # 블록만 버리는 게 아니라 conf 전체 로드에 실패해 supplicant가 아예 뜨지 않는다.
    # (이 핸들러는 항상 psk="..."로 쓰므로 64자 hex PMK는 지원 대상이 아니다.)
    # 길이는 바이트로 센다 — wpa_supplicant가 os_strlen(바이트)로 검사하므로
    # ${#var}(UTF-8 로케일에서 문자 수)로 재면 한글 passphrase가 양방향으로 어긋난다.
    _PSK_LEN=$(wifi_wpa_child_call byte_len "$NEW_PSK")
    if [ "$_PSK_LEN" -lt 8 ] || [ "$_PSK_LEN" -gt 63 ]; then
        echo "Error: psk must be 8-63 bytes (got $_PSK_LEN)" >&2
        exit 1
    fi
    # 개행·탭은 awk 멀티라인 injection으로 key_mgmt=NONE 같은 임의 directive를 주입한다.
    case "$NEW_PSK" in
        *[$'\n\r\t']*) echo "Error: psk에 개행/탭 문자 불가" >&2; exit 1 ;;
    esac
    # busybox awk가 ENVIRON 미지원이면 psk가 ""로 silent 손상 → 사전 검증(connect 규약).
    PSK_ENVIRON_PROBE=ok wifi_wpa_run_child awk 'BEGIN { exit(ENVIRON["PSK_ENVIRON_PROBE"] == "ok" ? 0 : 1) }' </dev/null \
        || { echo "Error: awk lacks ENVIRON support — cannot apply psk safely" >&2; exit 1; }
    TMP_FILE="$(wifi_wpa_child_exec mktemp)"
    # active psk= → 치환 / #psk=(주석)만 있으면 주석 해제 후 설정 / 둘 다 없으면 network 블록 끝에 append.
    # (블록 인지 방식은 connect 경로와 동일 — 주석처리된 설정도 확실히 반영. 블록당 done 1회.)
    # psk는 ENVIRON으로 raw 전달 — awk -v는 값의 \X를 C-escape로 해석해 손상시킨다.
    # 값은 이스케이프하지 않고 그대로 쓴다: wpa_supplicant의 따옴표 형식 psk="..."는
    # raw 바이트다(wpa_config_parse_psk가 os_strrchr로 마지막 "까지를 그대로 취함).
    # \를 \\로 바꿔 쓰면 리터럴 백슬래시가 저장되고, 위 byte_len은 이스케이프 전
    # 길이를 재므로 63바이트 경계에서 conf 전체 로드가 깨진다.
    if WIFI_NEW_PSK="$NEW_PSK" wifi_wpa_run_child awk '
        BEGIN { in_net = 0; changed = 0; new_psk = ENVIRON["WIFI_NEW_PSK"] }
        /^[[:space:]]*network[[:space:]]*=[[:space:]]*\{/ { in_net = 1; done = 0; print; next }
        in_net && /^[[:space:]]*\}/ {
            if (!done) { print "    psk=\"" new_psk "\""; changed = 1; done = 1 }
            in_net = 0; print; next
        }
        in_net && /^[[:space:]]*psk[[:space:]]*=/ {
            if (!done) { print "    psk=\"" new_psk "\""; changed = 1; done = 1 } next
        }
        in_net && /^[[:space:]]*#[[:space:]]*psk[[:space:]]*=/ {
            if (!done) { print "    psk=\"" new_psk "\""; changed = 1; done = 1; next }
            print; next
        }
        /^[[:space:]]*#/ { print; next }
        { print }
        END { if (!changed) exit 1 }
    ' "$CONF" > "$TMP_FILE"; then
        wifi_wpa_run_child_call safe_install_sync "$TMP_FILE" "$CONF"
        wifi_wpa_run_child rm -f "$TMP_FILE"
        echo "psk changed in $CONF"
    else echo "no network={ block found, nothing changed in $CONF" >&2; wifi_wpa_run_child rm -f "$TMP_FILE"; exit 1; fi
    ;;
  key)
    set -euo pipefail
    shift 2
    CONF="$WPA_CONF_DIR/wpa_supplicant-${IFACE}.conf"
    if [ ! -f "$CONF" ]; then echo "not found: $CONF" >&2; exit 1; fi
    wifi_wpa_conf_lock_acquire "$IFACE" \
        || { echo "Error: failed to lock $CONF" >&2; exit 1; }
    if [ $# -lt 1 ]; then echo "usage: wifi <iface> key <0|1|NONE|WPA-PSK>" >&2; exit 1; fi
    NEW_KEY="$1"
    [ "$NEW_KEY" = "0" ] && NEW_KEY="NONE"
    [ "$NEW_KEY" = "1" ] && NEW_KEY="WPA-PSK"
    # 오타 검사 — wpa_supplicant는 미지의 key_mgmt 토큰을 만나면 그 줄만 버리는 게
    # 아니라 conf 전체 파싱에 실패해 supplicant가 뜨지 않는다(WPA-PSK를 WPAPSK로
    # 치는 오타가 대표적). key_mgmt는 공백구분 다중 지정이 유효하므로 토큰별로 검사.
    # usage의 '*'는 임의 문자열이 아니라 아래 wpa_supplicant 인식 토큰 집합을 뜻한다.
    # 개행 먼저 거부 — read -r -a는 here-string의 첫 줄만 읽으므로, 개행 뒤 토큰은
    # allowlist를 통째로 우회하면서 awk는 여러 줄 전체를 conf에 써버린다(directive 주입).
    case "$NEW_KEY" in
        *[$'\n\r\t']*) echo "Error: key_mgmt에 개행/탭 문자 불가" >&2; exit 1 ;;
    esac
    read -r -a _KEY_TOKENS <<< "$NEW_KEY"
    [ ${#_KEY_TOKENS[@]} -eq 0 ] && { echo "Error: key_mgmt must not be empty" >&2; exit 1; }
    for _k in "${_KEY_TOKENS[@]}"; do
        case "$_k" in
            NONE|WPA-NONE|WPA-PSK|WPA-PSK-SHA256|WPA-EAP|WPA-EAP-SHA256|WPA-EAP-SUITE-B|WPA-EAP-SUITE-B-192) ;;
            IEEE8021X|SAE|FT-PSK|FT-EAP|FT-EAP-SHA384|FT-SAE|OWE|DPP|OSEN) ;;
            FILS-SHA256|FILS-SHA384|FT-FILS-SHA256|FT-FILS-SHA384) ;;
            *)
                echo "Error: invalid key_mgmt '$_k' (NONE|WPA-PSK|WPA-EAP|SAE|OWE|FT-PSK|FT-EAP|IEEE8021X|...)" >&2
                exit 1
                ;;
        esac
    done
    TMP_FILE="$(wifi_wpa_child_exec mktemp)"
    # active key_mgmt= → 치환 / #key_mgmt=(주석)만 있으면 주석 해제 후 설정 / 둘 다 없으면 network 블록 끝에 append.
    # (블록 인지 방식은 connect 경로와 동일 — 주석처리된 설정도 확실히 반영. 블록당 done 1회.)
    if wifi_wpa_run_child awk -v new_key="$NEW_KEY" '
        BEGIN { in_net = 0; changed = 0 }
        /^[[:space:]]*network[[:space:]]*=[[:space:]]*\{/ { in_net = 1; done = 0; print; next }
        in_net && /^[[:space:]]*\}/ {
            if (!done) { print "    key_mgmt=" new_key; changed = 1; done = 1 }
            in_net = 0; print; next
        }
        in_net && /^[[:space:]]*key_mgmt[[:space:]]*=/ {
            if (!done) { print "    key_mgmt=" new_key; changed = 1; done = 1 } next
        }
        in_net && /^[[:space:]]*#[[:space:]]*key_mgmt[[:space:]]*=/ {
            if (!done) { print "    key_mgmt=" new_key; changed = 1; done = 1; next }
            print; next
        }
        /^[[:space:]]*#/ { print; next }
        { print }
        END { if (!changed) exit 1 }
    ' "$CONF" > "$TMP_FILE"; then
        wifi_wpa_run_child_call safe_install_sync "$TMP_FILE" "$CONF"
        wifi_wpa_run_child rm -f "$TMP_FILE"
        echo "key_mgmt changed to $NEW_KEY in $CONF"
    else echo "no network={ block found, nothing changed in $CONF" >&2; wifi_wpa_run_child rm -f "$TMP_FILE"; exit 1; fi
    ;;
  connect)
    # 인자 있음(Mode B): ssid(+공통 freq_list)를 canonical conf에 기록한 뒤
    #                     wpa_cli reconfigure로 재로드한다.
    #                     freq 생략 시 기존 공통 목록을 유지하되 legacy scan_freq는 제거.
    # 인자 없음: conf 편집/reconfigure 없이 현재 설정으로 강제 재연결만.
    # 공통: reassociate(연결/미연결 모두 강제 재연관 → ssid 변경 반영 확실) 우선,
    #       실패 시 reconnect fallback(rollback_radio_live와 동일 규약) → assoc 대기.
    # exit: 0=ok 1=usage/env 7=wpa_cli 8=assoc-timeout
    # known limitation: reconfigure 성공 후 reassociate/reconnect 실패(7)나 assoc
    #   타임아웃(8) 시 conf는 새 ssid로 이미 persist된다(라이브는 옛 AP일 수 있음).
    #   ssid 변경은 의도된 영속이라 rollback하지 않는다 — 다음 재시도/부팅 시 적용.
    set -euo pipefail
    shift 2
    HAS_TARGET=0
    TARGET_SSID=""
    TARGET_SSID_WPA_TEXT=""
    HAS_TARGET_ID=0
    TARGET_ID=""
    MULTI_TOPOLOGY=0
    MULTI_INITIAL_ID=""
    SET_FREQ=0
    FREQ_STR=""
    CONNECT_TIMEOUT="$ASSOC_TIMEOUT_DEFAULT"
    TOTAL_POLLS=$((CONNECT_TIMEOUT * 10))
    REMAINING_POLLS="$TOTAL_POLLS"
    if [ "$IFACE" != "mlan0" ] && [ "$IFACE" != "mlan1" ]; then
        echo "Error: connect supports mlan0/mlan1 only" >&2; exit 1
    fi
    if ! command -v wpa_cli >/dev/null 2>&1; then
        echo "Error: wpa_cli not found" >&2; exit 1
    fi
    CONF="$WPA_CONF_DIR/wpa_supplicant-${IFACE}.conf"
    wifi_wpa_conf_lock_acquire "$IFACE" \
        || { echo "Error: failed to lock $CONF" >&2; exit 1; }
    wifi_scan_transition_lock_acquire "$IFACE" \
        || { echo "Error: failed to lock scan transition for $IFACE" >&2; exit 1; }
    if [ "$#" -ge 1 ]; then
        NEW_SSID="$1"
        SSID_CONF_VALUE=$(wifi_wpa_child_call wifi_ssid_to_conf_value "$NEW_SSID") || {
            echo "Error: SSID must be valid UTF-8, 1-32 bytes, without C0 controls or DEL" >&2
            exit 1
        }
        TARGET_SSID_WPA_TEXT=$(wifi_wpa_child_call wifi_ssid_to_wpa_text "$NEW_SSID") || {
            echo "Error: cannot encode SSID for exact association proof" >&2
            exit 1
        }
    fi
    if ! wifi_wpa_abort_scan_quiesce "$IFACE"; then
        echo "Error: cannot quiesce scan for $IFACE" >&2; exit 7
    fi
    # Every no-argument reconnect captures its current id, including Mode B.
    # Broad recovery is permitted only when this initial status has no id.
    if [ "$#" -eq 0 ]; then
        WPA_STATUS=$(wifi_wpa_child_exec wpa_cli -i "$IFACE" status 2>/dev/null) || WPA_STATUS=""
        while IFS='=' read -r _key _value; do
            [ "$_key" = "id" ] && TARGET_ID="$_value"
        done <<< "$WPA_STATUS"
        if [[ "$TARGET_ID" =~ ^[0-9]+$ ]]; then HAS_TARGET_ID=1; else TARGET_ID=""; fi
    fi
    # Mode A/실제 다중블록 거부 가드: boot snapshot을 우선하고 sentinel/block 수를
    # fail-safe 보조로 써 ssid 일괄교체를 차단한다. ssid 인자가 있을 때만 거부 — 인자 없는
    # 강제 재연결(reassociate)은 conf를 건드리지 않으므로 허용.
    if [ -f "$CONF" ] \
       && wifi_wpa_run_child_call wifi_wpa_conf_is_multi_topology "$IFACE" "$CONF"; then
        if [ $# -ge 1 ]; then
            echo "Error: $CONF 는 Mode A 또는 다중 network topology입니다." >&2
            echo "       SSID 전환은 boot-latched owner policy에 따라 자동 처리됩니다." >&2
            echo "       SSID 없이 'wifi <iface> connect'를 실행하면 재연결(reassociate)만 요청하며," >&2
            echo "       enable된 network 블록 중 supplicant가 고른 곳에 붙습니다." >&2
            exit 1
        fi
        # 다중 블록에서는 REASSOCIATE 뒤 supplicant가 enable된 모든 블록 중 best BSS를
        # 고른다(로밍 데몬이 cross-SSID select_network 뒤 enable_network all로 복원하므로
        # 시작 시점 id에 머문다는 보장이 없다 — #293). 그 id를 target으로 못 박지 않고,
        # fresh CONNECTED event가 가리킨 id의 COMPLETED로 착지를 증명한다(broad 경로).
        # disconnected/no-id 복구도 같은 경로다.
        MULTI_TOPOLOGY=1
        MULTI_INITIAL_ID="$TARGET_ID"
        HAS_TARGET_ID=0
        TARGET_ID=""
    fi
    trap 'connect_event_monitor_cleanup; wifi_wpa_run_child sync 2>/dev/null || true' EXIT
    trap 'exit 129' HUP
    trap 'exit 130' INT
    trap 'exit 143' TERM
    if ! connect_event_monitor_start "$IFACE" "$CONNECT_TIMEOUT"; then
        echo "Error: failed to attach reconnect event monitor for $IFACE" >&2
        exit 7
    fi
    if [ $# -ge 1 ]; then
        # === conf 편집 경로: ssid(+freq) 기록 → reconfigure ===
        if [ ! -f "$CONF" ]; then echo "not found: $CONF" >&2; exit 1; fi
        # Identity validation and byte-exact encoding happened before any live
        # ctrl mutation, while both writer locks were held.
        shift
        HAS_TARGET=1
        TARGET_SSID="$NEW_SSID"
        # busybox awk가 ENVIRON 미지원이면 SSID가 ""로 silent 손상(awk exit 0 → 성공 오인)
        # → SSID 적용 전 ENVIRON 지원을 사전 검증(opc_wlan_apply.sh와 동일 규약).
        CONNECT_ENVIRON_PROBE=ok wifi_wpa_run_child awk \
            'BEGIN { exit(ENVIRON["CONNECT_ENVIRON_PROBE"] == "ok" ? 0 : 1) }' </dev/null \
            || { echo "Error: awk lacks ENVIRON support — cannot apply ssid safely" >&2; exit 1; }
        # freq 인자는 freq 명령과 동일하게 채널/MHz 모두 허용(to_freq_mhz로 MHz 정규화)
        # + 동일한 재검사 — SSID는 이 아래에서 두텁게 가드되는데 freq만 무검증이었다.
        FREQS=()
        for arg in "$@"; do
            _f="$(wifi_wpa_child_call to_freq_mhz_checked "$arg")" || exit 1
            FREQS+=( "$_f" )
        done
        if [ ${#FREQS[@]} -gt 0 ]; then SET_FREQ=1; FREQ_STR="${FREQS[*]}"; fi
        # freq 생략 시에도 legacy conf를 canonical 형식으로 이행하면서 기존 공통 목록을
        # 보존한다(전역 > 첫 블록 freq_list > 첫 블록 legacy scan_freq fallback 우선순위).
        if [ "$SET_FREQ" = "0" ]; then
            if ! FREQ_STR="$(wifi_wpa_child_call wifi_wpa_conf_common_freqs "$CONF")"; then
                echo "Error: failed to resolve common frequency policy in $CONF" >&2
                exit 1
            fi
        fi

        # canonical 변환과 SSID 치환을 두 임시파일에서 완료한 뒤 최종 결과만 한 번
        # install한다. 중간 형식이 실제 conf에 노출되지 않아 reconfigure와 경쟁하지 않는다.
        CANON_FILE="$(wifi_wpa_child_exec mktemp)"
        TMP_FILE="$(wifi_wpa_child_exec mktemp)"
        trap 'wifi_wpa_run_child rm -f "$CANON_FILE" "$TMP_FILE"; connect_event_monitor_cleanup; wifi_wpa_run_child sync 2>/dev/null || true' EXIT
        if ! wifi_wpa_run_child_call wifi_wpa_conf_render_canonical \
            "$CONF" "$CANON_FILE" "$FREQ_STR"; then
            echo "Error: failed to render canonical frequency policy in $CONF" >&2
            exit 1
        fi
        # 직렬화는 wifi_ssid_to_conf_value 가 고른다(따옴표 우선, `"` 포함 시 hex).
        if CONNECT_SSID_CONF="$SSID_CONF_VALUE" wifi_wpa_run_child awk '
            BEGIN { in_net = 0; blocks = 0; new_ssid = ENVIRON["CONNECT_SSID_CONF"] }
            /^[[:space:]]*#/ { print; next }
            /^[[:space:]]*network[[:space:]]*=[[:space:]]*\{/ {
                in_net = 1; blocks++; done_ssid = 0; print; next
            }
            in_net && /^[[:space:]]*\}/ {
                if (!done_ssid) { print "    ssid=" new_ssid; done_ssid = 1 }
                in_net = 0; print; next
            }
            in_net && /^[[:space:]]*ssid[[:space:]]*=/ {
                if (!done_ssid) { print "    ssid=" new_ssid; done_ssid = 1 } next
            }
            { print }
            END { if (blocks == 0) { print "error: no network={ block in config" > "/dev/stderr"; exit 1 } }
        ' "$CANON_FILE" > "$TMP_FILE"; then
            wifi_wpa_run_child_call safe_install_sync "$TMP_FILE" "$CONF"
            wifi_wpa_run_child rm -f "$CANON_FILE" "$TMP_FILE"
        else
            wifi_wpa_run_child rm -f "$CANON_FILE" "$TMP_FILE"
            echo "Error: failed to update $CONF (no network={ block?)" >&2
            exit 1
        fi
        if [ "$SET_FREQ" = "1" ]; then
            echo "conf updated: ssid=\"$NEW_SSID\" global/block freq_list=$FREQ_STR in $CONF"
        elif [ -n "$FREQ_STR" ]; then
            echo "conf updated: ssid=\"$NEW_SSID\" (global/block freq_list kept: $FREQ_STR) in $CONF"
        else
            echo "conf updated: ssid=\"$NEW_SSID\" (no frequency restriction) in $CONF"
        fi
        if ! connect_event_monitor_arm || ! wpa_cli_ok "$IFACE" reconfigure; then
            echo "Error: wpa_cli reconfigure failed for $IFACE (wpa_supplicant 미동작 또는 conf 문법 오류 확인)" >&2
            exit 7
        fi
        echo "wpa_cli reconfigure OK ($IFACE)"
        # Reconfigure gets a bounded grace share of the one association poll
        # budget.  A delayed fresh event plus a subsequent matching status can
        # complete here without a redundant reassociation.
        GRACE_POLLS=$((TOTAL_POLLS / 3))
        [ "$GRACE_POLLS" -ge 1 ] || GRACE_POLLS=1
        [ "$GRACE_POLLS" -le 20 ] || GRACE_POLLS=20
        if [ "$GRACE_POLLS" -ge "$TOTAL_POLLS" ]; then
            GRACE_POLLS=$((TOTAL_POLLS - 1))
        fi
        for ((_i = 1; _i <= GRACE_POLLS && REMAINING_POLLS > 0; _i++)); do
            if connect_association_poll_matches; then
                ASSOC_MATCH=1
            fi
            REMAINING_POLLS=$((REMAINING_POLLS - 1))
            if [ "$ASSOC_MATCH" = "1" ]; then
                echo "associated by reconfigure: ssid=\"$CUR_SSID\" freq=$CUR_FREQ id=$CUR_ID"
                exit 0
            fi
            if [ "$_i" -lt "$GRACE_POLLS" ] && [ "$REMAINING_POLLS" -gt 0 ]; then
                wifi_wpa_run_child sleep 0.1
            fi
        done
    else
        # === 인자 없음: conf 그대로 현재 설정으로 재연결만 ===
        # stdout 첫 줄은 ftpcmd `wconnect`의 200 응답 본문이 되므로 ASCII만 쓴다(#292).
        if [ "$MULTI_TOPOLOGY" = "1" ]; then
            echo "no ssid given - reassociating $IFACE (multi-network topology: current id=${MULTI_INITIAL_ID:-none}, supplicant selects among enabled networks)..."
        elif [ "$HAS_TARGET_ID" = "1" ]; then
            echo "no ssid given - reassociating current network id=$TARGET_ID on $IFACE..."
        else
            echo "no ssid/current id given - reassociating $IFACE with current conf..."
        fi
    fi
    # --- 공통: owner-neutral 강제 재연결(reassociate 우선, 실패 시 reconnect) → assoc 대기 ---
    # Mode A도 다른 network 블록의 enabled 상태를 바꾸지 않는다. 단일 블록은 최초에 캡처한
    # id를, 다중 블록은 fresh CONNECTED event가 가리킨 id를 그 이후 COMPLETED 폴링으로 증명한다.
    if ! connect_event_monitor_arm; then
        echo "Error: failed to arm reconnect event monitor for $IFACE" >&2
        exit 7
    fi
    if ! wpa_cli_ok "$IFACE" reassociate && ! wpa_cli_ok "$IFACE" reconnect; then
        echo "Error: wpa_cli reassociate/reconnect failed for $IFACE" >&2
        exit 7
    fi
    # 연결 완료 대기(best-effort, 최대 15s) — 0.1s grid 폴링으로 COMPLETED를 빨리 감지.
    # (실제 association 시간은 물리 과정이라 불변; 폴링 grid만 줄여 끝맺음 반응성 개선)
    WPA_STATE=""; CUR_SSID=""; CUR_FREQ=""; CUR_ID=""
    FRESH_EVENT_ID=""; ASSOC_MATCH=0
    # No-argument reconnects arrive with the full budget.  Explicit reconnects
    # use only the remainder after grace; neither phase resets the counter.
    while [ "$REMAINING_POLLS" -gt 0 ]; do
        if connect_association_poll_matches; then
            ASSOC_MATCH=1
        fi
        REMAINING_POLLS=$((REMAINING_POLLS - 1))
        [ "$ASSOC_MATCH" = "1" ] && break
        [ "$REMAINING_POLLS" -gt 0 ] && wifi_wpa_run_child sleep 0.1
    done
    if [ "$ASSOC_MATCH" = "1" ]; then
        echo "associated: ssid=\"${CUR_SSID:-N/A}\" freq=${CUR_FREQ:-N/A} id=${CUR_ID:-N/A} (wpa_state=COMPLETED)"
        exit 0
    else
        echo "Warning: requested association not completed within ${CONNECT_TIMEOUT}s" >&2
        echo "         target_id=${TARGET_ID:-any} event_id=${FRESH_EVENT_ID:-none} target_ssid=${TARGET_SSID:-any} target_freq=${FREQ_STR:-any} state=${WPA_STATE:-unknown} id=${CUR_ID:-N/A} ssid=${CUR_SSID:-N/A} freq=${CUR_FREQ:-N/A}" >&2
        echo "         'wifi $NUM scan' / 'wifi $NUM info'로 AP 가용성/대역 점검" >&2
        exit 8
    fi
    ;;
  scan)
    set -euo pipefail
    shift 2
    # Band shortcut: 2G/5G must be used alone (no mixing with channel/freq args)
    BAND_FREQS="$(band_freqs "${1:-}")"
    if [ -n "$BAND_FREQS" ]; then
        if [ $# -ne 1 ]; then
            echo "Error: 2G/5G must be used alone (no other channel/freq args)" >&2; exit 1
        fi
        echo "scanning band $1 for $IFACE: $BAND_FREQS"
        if ! wifi_scan_transition_lock_acquire "$IFACE"; then
            echo "Error: scan/association transition busy for $IFACE" >&2; exit 6
        fi
        if ! wifi_wpa_abort_scan_quiesce "$IFACE"; then
            echo "Error: native scan did not quiesce for $IFACE" >&2; exit 6
        fi
        iw $IFACE scan freq $BAND_FREQS
        exit 0
    fi
    for arg in "$@"; do
        [ -n "$(band_freqs "$arg")" ] && { echo "Error: 2G/5G must be used alone (no other channel/freq args)" >&2; exit 1; }
    done
    FREQS=()
    # persist 경로는 아니지만 동일한 재검사 — 오타는 iw에 넘기기 전에 잡는다.
    for arg in "$@"; do
        _f="$(to_freq_mhz_checked "$arg")" || exit 1
        FREQS+=( "$_f" )
    done
    [ ${#FREQS[@]} -eq 0 ] && { echo "configure freq not exist" >&2; exit 1; }
    FREQ_STR="${FREQS[*]}"
    echo "scanning freq_list $FREQ_STR for $IFACE"
    if ! wifi_scan_transition_lock_acquire "$IFACE"; then
        echo "Error: scan/association transition busy for $IFACE" >&2; exit 6
    fi
    if ! wifi_wpa_abort_scan_quiesce "$IFACE"; then
        echo "Error: native scan did not quiesce for $IFACE" >&2; exit 6
    fi
    iw $IFACE scan freq $FREQ_STR
    ;;
  mscan)
    set -euo pipefail
    shift 2
    # "get" => dump scan results (getscantable)
    if [ "${1:-}" == "get" ]; then
        mlanutl "$IFACE" getscantable
        exit 0
    fi
    # mlanutl setuserscan based scan. Same arg style as scan (channels / 2G / 5G),
    # converted to setuserscan chan tokens (channel#+band: 2.4G->'g', 5G->'a').
    BAND_CHANS="$(band_chans "${1:-}")"
    if [ -n "$BAND_CHANS" ]; then
        if [ $# -ne 1 ]; then
            echo "Error: 2G/5G must be used alone (no other channel args)" >&2; exit 1
        fi
        CHAN_STR="$BAND_CHANS"
    else
        for arg in "$@"; do
            [ -n "$(band_chans "$arg")" ] && { echo "Error: 2G/5G must be used alone (no other channel args)" >&2; exit 1; }
        done
        CHANS=()
        for arg in "$@"; do
            ch="$(freq_to_channel "$arg")"
            if ! [[ "$ch" =~ ^[0-9]+$ ]]; then
                echo "Error: invalid channel/freq '$arg'" >&2; exit 1
            fi
            if (( ch <= 14 )); then
                CHANS+=( "${ch}g" )
            else
                CHANS+=( "${ch}a" )
            fi
        done
        [ ${#CHANS[@]} -eq 0 ] && { echo "configure channel not exist" >&2; exit 1; }
        CHAN_STR="$(IFS=,; echo "${CHANS[*]}")"
    fi
    echo "mscan (setuserscan) chan=$CHAN_STR for $IFACE"
    if ! wifi_scan_transition_lock_acquire "$IFACE"; then
        echo "Error: scan/association transition busy for $IFACE" >&2; exit 6
    fi
    if ! wifi_wpa_abort_scan_quiesce "$IFACE"; then
        echo "Error: native scan did not quiesce for $IFACE" >&2; exit 6
    fi
    mlanutl "$IFACE" setuserscan chan="$CHAN_STR"
    echo "(results: wifi $NUM mscan get)"
    ;;
  rate)
    if [ -z "${3:-}" ]; then
        echo "--- Configured rate_adapt ($WIFI_INIT_CONF_JSON) ---"
        if [ -f "$WIFI_INIT_CONF_JSON" ] && command -v jq >/dev/null 2>&1; then
            jq -r ".${IFACE}.rate_adapt // \"(not configured)\"" "$WIFI_INIT_CONF_JSON"
        else
            echo "(JSON or jq not available)"
        fi
        echo ""
        echo "--- Live rate_adapt_cfg ($IFACE) ---"
        mlanutl "$IFACE" rate_adapt_cfg 2>/dev/null || echo "(rate_adapt_cfg not available)"
        echo ""
        echo "Note: Connected-state SET is unsupported. Configured values reach the FW only through the"
        echo "      boot path, before association, and several gates can skip it (interface enable,"
        echo "      rate_adapt.enabled; see guide 11.5). The configured block above is an intent, not a"
        echo "      promise - read the live block above for the current FW state."
        echo "      fwcfg_watch, when the logger runs for this interface with fwcfg_watch_sec > 0, logs"
        echo "      later changes of the live value to syslog; it never compares live against JSON."
        if [ "$IFACE" = "mlan0" ]; then
            echo "      The documented 30/50 restore on roam COMPLETED did not reproduce here (0.5.6, 2026-08-31),"
            echo "      but a same-ESS BSS roam was not exercised, so it is not fully ruled out. See guide 11.5."
        else
            echo "      The documented 30/50 restore on association COMPLETED is untested on this interface;"
            echo "      treat a reset as still possible. See guide 11.5."
        fi
    else
        if [ "$#" -ne 6 ]; then
            echo "Usage: wifi $NUM rate <mode:0|1> <low:0..100|255> <high:0..100|255> <interval_ms>" >&2
            exit 1
        fi
        _rate_mode=$3
        _rate_low=$4
        _rate_high=$5
        _rate_interval=$6
        for _rate_value in "$_rate_mode" "$_rate_low" "$_rate_high" "$_rate_interval"; do
            case "$_rate_value" in ''|*[!0-9]*) echo "Error: rate values must be non-negative integers" >&2; exit 1 ;; esac
        done
        _rate_tmp=$(safe_tmp_for "$WIFI_INIT_CONF_JSON") || exit 1
        if ! jq --arg iface "$IFACE" \
                --argjson mode "$_rate_mode" --argjson low "$_rate_low" \
                --argjson high "$_rate_high" --argjson interval "$_rate_interval" '
                # 통째 대입은 동거 키(_comment, enabled)를 지운다 — 값만 덮어쓴다.
                .[$iface].rate_adapt = ((.[$iface].rate_adapt // {}) + {
                    mode: $mode, low_thresh: $low, high_thresh: $high, interval_ms: $interval
                })
            ' "$WIFI_INIT_CONF_JSON" > "$_rate_tmp"; then
            rm -f -- "$_rate_tmp"
            echo "Error: failed to stage rate_adapt JSON" >&2
            exit 1
        fi
        if ! declare -f wifi_fw_validate_rate_config >/dev/null 2>&1 \
           || ! wifi_fw_validate_rate_config "$_rate_tmp" "$IFACE"; then
            rm -f -- "$_rate_tmp"
            echo "Error: invalid rate config (mode 0|1; static 0..100 with low<high, or 255/255; interval positive 10ms multiple)" >&2
            exit 1
        fi
        if ! safe_install_sync "$_rate_tmp" "$WIFI_INIT_CONF_JSON"; then
            rm -f -- "$_rate_tmp"
            echo "Error: failed to persist rate_adapt JSON" >&2
            exit 1
        fi
        rm -f -- "$_rate_tmp"
        echo "rate_adapt configured for $IFACE: mode=$_rate_mode low=$_rate_low high=$_rate_high interval_ms=$_rate_interval"
        if [ "$(jq -r --arg i "$IFACE" '
                if (.[$i].rate_adapt.enabled == null) then "true"
                else (.[$i].rate_adapt.enabled | tostring) end' \
                "$WIFI_INIT_CONF_JSON" 2>/dev/null)" != "true" ]; then
            echo "Note: ${IFACE}.rate_adapt.enabled=false — 값은 저장되지만 부팅 시 적용되지 않는다."
        fi
        echo "Apply: next boot before association. Current connection is unchanged."
    fi
    ;;
  mcs)
    if [ -z "${3:-}" ]; then
        # GET: show current mcs_tier from JSON + live mcstiercfg
        echo "--- JSON config ($WIFI_INIT_CONF_JSON) ---"
        if [ -f "$WIFI_INIT_CONF_JSON" ] && command -v jq >/dev/null 2>&1; then
            jq -r ".${IFACE}.mcs_tier // \"(not configured)\"" "$WIFI_INIT_CONF_JSON"
        else
            echo "(JSON or jq not available)"
        fi
        echo ""
        echo "--- Live mcstiercfg ($IFACE) ---"
        mlanutl "$IFACE" mcstiercfg 2>/dev/null || echo "(mcstiercfg not available)"
        if [ "$IFACE" = "mlan0" ]; then
            echo ""
            echo "--- Live 11axcfg ($IFACE, HE capability map) ---"
            mlanutl "$IFACE" 11axcfg 2>/dev/null || echo "(11axcfg not available)"
        fi
    elif [ "$3" == "off" ] || [ "$3" == "0" ]; then
        # Disable mcs_tier
        jq --arg iface "$IFACE" '.[$iface].mcs_tier.enabled = false' \
            "$WIFI_INIT_CONF_JSON" > "${WIFI_INIT_CONF_JSON}.tmp" && \
            mv "${WIFI_INIT_CONF_JSON}.tmp" "$WIFI_INIT_CONF_JSON"
        echo "mcs_tier disabled for $IFACE in $WIFI_INIT_CONF_JSON"
        echo "(applies before association on next boot; current association is unchanged)"
    elif [ "$3" == "on" ] || [ "$3" == "1" ]; then
        # Enable mcs_tier with stored JSON values (re-apply ht/vht/he)
        jq --arg iface "$IFACE" '.[$iface].mcs_tier.enabled = true' \
            "$WIFI_INIT_CONF_JSON" > "${WIFI_INIT_CONF_JSON}.tmp" && \
            mv "${WIFI_INIT_CONF_JSON}.tmp" "$WIFI_INIT_CONF_JSON"
        echo "mcs_tier enabled for $IFACE in $WIFI_INIT_CONF_JSON"
        echo "(applies and is GET-verified before association on next boot)"
    elif [ "$3" == "reset" ]; then
        # Reset: disable in JSON + restore live
        jq --arg iface "$IFACE" '.[$iface].mcs_tier.enabled = false' \
            "$WIFI_INIT_CONF_JSON" > "${WIFI_INIT_CONF_JSON}.tmp" && \
            mv "${WIFI_INIT_CONF_JSON}.tmp" "$WIFI_INIT_CONF_JSON"
        echo "mcs_tier disabled for $IFACE (FW defaults return on next boot)"
    else
        # SET: wifi 0 mcs ht 7 vht 7 he 7
        shift 2  # remove iface and "mcs"
        MCS_HT="" MCS_VHT="" MCS_HE=""
        MCS_ARGS=""
        while [ $# -gt 0 ]; do
            case "$1" in
                ht)
                    [ -z "${2:-}" ] && { echo "Error: ht requires a value (7 or 15)"; exit 1; }
                    case "$2" in
                        7|15) MCS_HT="$2"; MCS_ARGS="$MCS_ARGS ht $2" ;;
                        *) echo "Error: ht must be 7 or 15"; exit 1 ;;
                    esac
                    shift 2 ;;
                vht)
                    [ -z "${2:-}" ] && { echo "Error: vht requires a value (7/8/9)"; exit 1; }
                    case "$2" in
                        7|8|9) MCS_VHT="$2"; MCS_ARGS="$MCS_ARGS vht $2" ;;
                        *) echo "Error: vht must be 7, 8, or 9"; exit 1 ;;
                    esac
                    shift 2 ;;
                he)
                    [ -z "${2:-}" ] && { echo "Error: he requires a value (7/9/11)"; exit 1; }
                    if [ "$IFACE" != "mlan0" ]; then
                        echo "Error: $IFACE supports up to 802.11ac; HE is unsupported" >&2
                        exit 1
                    fi
                    case "$2" in
                        7|9|11) MCS_HE="both $2"; MCS_ARGS="$MCS_ARGS he both $2" ;;
                        *) echo "Error: he must be 7, 9, or 11"; exit 1 ;;
                    esac
                    shift 2 ;;
                *) echo "Error: unknown option '$1'"; usage ;;
            esac
        done
        if [ -z "$MCS_ARGS" ]; then
            echo "Error: specify at least one of: ht, vht, he"
            exit 1
        fi
        # 문자열 타입을 유지한 채 원자 갱신한다. FW live SET은 connected 상태의 현재
        # association capability를 바꾸지 못하므로 실행하지 않는다.
        _mcs_tmp=$(safe_tmp_for "$WIFI_INIT_CONF_JSON") || exit 1
        if ! jq --arg iface "$IFACE" --arg ht "$MCS_HT" --arg vht "$MCS_VHT" --arg he "$MCS_HE" '
                .[$iface].mcs_tier.enabled = true
                | if $ht != "" then .[$iface].mcs_tier.ht = $ht else . end
                | if $vht != "" then .[$iface].mcs_tier.vht = $vht else . end
                | if $he != "" then .[$iface].mcs_tier.he = $he else . end
            ' "$WIFI_INIT_CONF_JSON" > "$_mcs_tmp" \
           || ! safe_install_sync "$_mcs_tmp" "$WIFI_INIT_CONF_JSON"; then
            rm -f -- "$_mcs_tmp"
            echo "Error: failed to update mcs_tier JSON" >&2
            exit 1
        fi
        rm -f -- "$_mcs_tmp"
        echo "mcs_tier updated for $IFACE:$MCS_ARGS"
        echo "JSON: enabled=true$( [ -n "$MCS_HT" ] && echo " ht=$MCS_HT" )$( [ -n "$MCS_VHT" ] && echo " vht=$MCS_VHT" )$( [ -n "$MCS_HE" ] && echo " he=$MCS_HE" )"
        echo "Apply: next boot before association (SET + mcstiercfg/11axcfg GET verification)"
    fi
    ;;
  standard)
    if [[ "$3" == "4" ]] || [[ "$3" == "n" ]] || [[ "$3" == "N" ]]; then
        VAL="n"
    elif [[ "$3" == "5" ]] || [[ "$3" == "ac" ]] || [[ "$3" == "AC" ]]; then
        VAL="ac"
    elif [[ "$3" == "6" ]] || [[ "$3" == "ax" ]] || [[ "$3" == "AX" ]]; then
        VAL="ax"
    else
        usage
    fi

    if [ "$IFACE" = "mlan1" ] && [ "$VAL" = "ax" ]; then
        echo "Error: mlan1 does not support ax (11ax). Use n or ac." >&2
        exit 1
    fi

    # persist/staged radio.mode가 새 STANDARD를 초과하면 부팅 후 bandcfg가 거부됨 — 경고
    R_MODE_CUR=$(radio_stage_get "$IFACE" mode)
    [ -z "$R_MODE_CUR" ] && \
        R_MODE_CUR=$(jq -r ".${IFACE}.radio.mode // empty" "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    if [ -n "$R_MODE_CUR" ] && [ "$(mode_rank "$R_MODE_CUR")" -gt "$(mode_rank "$VAL")" ]; then
        echo "Warning: persisted radio.mode=$R_MODE_CUR exceeds new STANDARD=$VAL." >&2
        echo "         Lower it with 'wifi $NUM mode $VAL' or bandcfg will fail after reboot." >&2
    fi

    update_json_iface "$IFACE" "STANDARD" "$VAL"
    echo "STANDARD updated to $VAL for $IFACE in $WIFI_INIT_CONF_JSON"
    ;;
  mode)
    # 무선 모드(b/g/a/n/ac/ax) persist. 런타임 적용은 radio-apply.
    # exit: 0=ok 2=usage 9=json
    if [ "$IFACE" != "mlan0" ] && [ "$IFACE" != "mlan1" ]; then
        echo "Error: mode supports mlan0/mlan1 only" >&2
        exit 2
    fi
    MODE_VAL=$(echo "${3:-}" | tr '[:upper:]' '[:lower:]')
    if [ -z "$MODE_VAL" ] || [ "$MODE_VAL" = "get" ]; then
        echo "--- Committed ($WIFI_INIT_CONF_JSON) ---"
        jq -r ".${IFACE}.radio.mode // \"(not configured: chip default)\"" \
            "$WIFI_INIT_CONF_JSON" 2>/dev/null || echo "(JSON/jq not available)"
        MODE_PEND=$(radio_stage_get "$IFACE" mode)
        echo "--- Staged (radio-apply 대기) ---"
        echo "${MODE_PEND:-(none)}"
        echo "--- Effective STANDARD (dev_cap_mask, 부팅 적용) ---"
        jq -r ".${IFACE}.STANDARD // .global.STANDARD // \"(not set: chip max)\"" \
            "$WIFI_INIT_CONF_JSON" 2>/dev/null || echo "(JSON/jq not available)"
        echo "--- Live bandcfg ($IFACE) ---"
        mlanutl "$IFACE" bandcfg 2>/dev/null || echo "(bandcfg not available)"
        exit 0
    fi
    MODE_MASK=$(wifi_init_mode_to_bandcfg_mask "$MODE_VAL")
    if [ -z "$MODE_MASK" ]; then
        echo "Error: mode must be one of b|g|a|n|ac|ax" >&2
        exit 2
    fi
    if [ "$IFACE" = "mlan1" ] && [ "$MODE_VAL" = "ax" ]; then
        echo "Error: mlan1 does not support ax (11ax). Use n or ac." >&2
        exit 2
    fi
    # STANDARD(dev_cap_mask)가 더 낮으면 FW cap이 잘려 bandcfg가 거부됨 — 사전 경고
    EFF_STD=$(jq -r ".${IFACE}.STANDARD // .global.STANDARD // empty" "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    if [ -n "$EFF_STD" ] && [ "$(mode_rank "$MODE_VAL")" -gt "$(mode_rank "$EFF_STD")" ]; then
        echo "Warning: STANDARD=$EFF_STD caps FW capability below mode=$MODE_VAL." >&2
        echo "         radio-apply will fail (exit 4) until 'wifi $NUM standard $MODE_VAL' + reboot." >&2
    fi
    # b/g(2.4G 전용 마스크) + 5G 전용 freq_list는 연결 불가 조합 — 사전 경고
    # (persist는 허용: freq를 이후에 바꿀 수 있으므로. 강제 거부는 radio-apply exit 11)
    if [ "$MODE_VAL" = "b" ] || [ "$MODE_VAL" = "g" ]; then
        MODE_FREQ_BANDS=$(wifi_init_conf_freq_bands "${WPA_CONF_DIR:-/etc/wpa_supplicant}/wpa_supplicant-${IFACE}.conf")
        if [ "$MODE_FREQ_BANDS" = "5G" ]; then
            echo "Warning: freq_list is 5G-only but mode=$MODE_VAL is 2.4G-only — radio-apply will be rejected (exit 11)." >&2
            echo "         Change freq with 'wifi $NUM freq <2.4G ch>' before applying." >&2
        fi
    fi
    radio_stage_set "$IFACE" "mode" "$MODE_VAL" || { echo "Error: stage write failed" >&2; exit 9; }
    echo "radio.mode = $MODE_VAL (bandcfg $MODE_MASK) staged for $IFACE"
    echo "(apply: 'wifi $NUM radio-apply' — 성공 시에만 JSON persist, 취소: 'wifi $NUM radio-discard')"
    exit 0
    ;;
  bw)
    # 대역폭(20/40/80MHz) persist. 런타임 적용은 radio-apply.
    # exit: 0=ok 2=usage 9=json
    if [ "$IFACE" != "mlan0" ] && [ "$IFACE" != "mlan1" ]; then
        echo "Error: bw supports mlan0/mlan1 only" >&2
        exit 2
    fi
    BW_VAL=$(echo "${3:-}" | tr '[:upper:]' '[:lower:]')
    if [ -z "$BW_VAL" ] || [ "$BW_VAL" = "get" ]; then
        echo "--- Committed ($WIFI_INIT_CONF_JSON) ---"
        jq -r ".${IFACE}.radio.bw // \"(not configured: chip default)\"" \
            "$WIFI_INIT_CONF_JSON" 2>/dev/null || echo "(JSON/jq not available)"
        BW_PEND=$(radio_stage_get "$IFACE" bw)
        echo "--- Staged (radio-apply 대기) ---"
        echo "${BW_PEND:-(none)}"
        echo "--- Live htcapinfo/vhtcfg ($IFACE) ---"
        mlanutl "$IFACE" htcapinfo 2>/dev/null || echo "(htcapinfo not available)"
        mlanutl "$IFACE" vhtcfg 2 2 2>/dev/null || echo "(vhtcfg not available)"
        exit 0
    fi
    case "$BW_VAL" in
        20|40|80|auto|default) ;;  # default = AP 따라(committed 제거)
        *)
            echo "Error: bw must be one of 20|40|80|auto|default" >&2
            exit 2
            ;;
    esac
    radio_stage_set "$IFACE" "bw" "$BW_VAL" || { echo "Error: stage write failed" >&2; exit 9; }
    echo "radio.bw = $BW_VAL staged for $IFACE"
    echo "(apply: 'wifi $NUM radio-apply' — 성공 시에만 JSON persist, 취소: 'wifi $NUM radio-discard')"
    exit 0
    ;;
  radio-discard | radio_discard)
    if [ "$IFACE" != "mlan0" ] && [ "$IFACE" != "mlan1" ]; then
        echo "Error: radio-discard supports mlan0/mlan1 only" >&2
        exit 2
    fi
    rm -f "$(radio_pending_path "$IFACE" mode)" "$(radio_pending_path "$IFACE" bw)"
    echo "staged radio mode/bw changes discarded for $IFACE"
    exit 0
    ;;
  radio-apply | radio_apply)
    # staged(pending) radio.mode/radio.bw를 적용하고 성공 시에만 JSON에 commit하는
    # 트랜잭션 시퀀스. 실패 시 적용 전 스냅샷으로 라이브 롤백 + 재연결.
    # mode 변경: disconnect → bandcfg(재시도) → cap → reconfigure → reconnect
    # bw-only: cap(htcapinfo/vhtcfg) → reassociate  (HE 포함 양방향, 무중단)
    # → assoc 대기 → commit(또는 bw=default면 radio.bw 삭제)
    # exit: 0=ok 2=usage 3=env 4=bandcfg 5=htcapinfo 6=vhtcfg 7=wpa_cli 8=assoc-timeout 9=json/commit 11=b/g+5G-freq
    if [ "$IFACE" != "mlan0" ] && [ "$IFACE" != "mlan1" ]; then
        echo "Error: radio-apply supports mlan0/mlan1 only" >&2
        exit 2
    fi
    ASSOC_TIMEOUT="${3:-$ASSOC_TIMEOUT_DEFAULT}"
    if ! [[ "$ASSOC_TIMEOUT" =~ ^[0-9]+$ ]] || [ "$ASSOC_TIMEOUT" -lt 1 ]; then
        echo "Error: timeout_s must be a positive integer" >&2
        exit 2
    fi
    ASSOC_TIMEOUT=$((10#$ASSOC_TIMEOUT))   # 08/09 등 선행 0 → 10진수 정규화 (폴링 *10 산술의 8진수 에러 방지)
    for _tool in mlanutl wpa_cli jq; do
        if ! command -v "$_tool" >/dev/null 2>&1; then
            echo "Error: $_tool not found" >&2
            exit 3
        fi
    done
    if [ ! -f "$WIFI_INIT_CONF_JSON" ]; then
        echo "Error: $WIFI_INIT_CONF_JSON not found" >&2
        exit 3
    fi
    # JSON 파손이 '설정 없음(exit 0)'으로 위장하지 않도록 유효성 선검사
    if ! jq -e . "$WIFI_INIT_CONF_JSON" >/dev/null 2>&1; then
        echo "Error: invalid JSON in $WIFI_INIT_CONF_JSON" >&2
        exit 9
    fi
    R_MODE_JSON=$(jq -r ".${IFACE}.radio.mode // empty" "$WIFI_INIT_CONF_JSON" 2>/dev/null) || {
        echo "Error: cannot read .${IFACE}.radio.mode from $WIFI_INIT_CONF_JSON" >&2
        exit 9
    }
    R_BW_JSON=$(jq -r ".${IFACE}.radio.bw // empty" "$WIFI_INIT_CONF_JSON" 2>/dev/null) || {
        echo "Error: cannot read .${IFACE}.radio.bw from $WIFI_INIT_CONF_JSON" >&2
        exit 9
    }
    # 적용 대상 = staged(pending) 우선, 없으면 committed 재적용
    R_MODE_PEND=$(radio_stage_get "$IFACE" mode)
    R_BW_PEND=$(radio_stage_get "$IFACE" bw)
    R_MODE="${R_MODE_PEND:-$R_MODE_JSON}"
    R_BW="${R_BW_PEND:-$R_BW_JSON}"
    if [ -z "$R_MODE" ] && [ -z "$R_BW" ]; then
        echo "nothing to apply: radio.mode/radio.bw not staged or committed for $IFACE"
        exit 0
    fi
    # bw=default: cap은 AP 최대(80=0x05c20000/bwcfg1)로 열어 AP 따라가게 하고,
    # commit 시 radio.bw를 JSON에서 삭제(부팅 재적용 대상에서 제외 = 디폴트 복귀).
    R_BW_DEFAULT=""
    R_BW_CAP="$R_BW"
    if [ "$R_BW" = "default" ]; then
        R_BW_CAP=80
        # radio.bw 삭제(__del__)는 staged default일 때만 — committed 잔재가
        # 사용자 미요청 삭제를 유발하지 않도록 staged 입력에 한정.
        [ "$R_BW_PEND" = "default" ] && R_BW_DEFAULT=1
    fi
    # freq↔mode 교차 검증: b/g 마스크(0x1/0x3)는 5G 비트가 없어 freq_list가
    # 5G 전용이면 스캔 채널 0개("Scan: No channel configured")로 영구 SCANNING에
    # 빠진다 → 링크를 건드리기 전에 즉시 거부. 2G+5G 혼합이면 안내만 출력.
    if [ "$R_MODE" = "b" ] || [ "$R_MODE" = "g" ]; then
        R_CONF="${WPA_CONF_DIR:-/etc/wpa_supplicant}/wpa_supplicant-${IFACE}.conf"
        R_FREQ_BANDS=$(wifi_init_conf_freq_bands "$R_CONF")
        if [ "$R_FREQ_BANDS" = "5G" ]; then
            echo "Error: mode=$R_MODE is 2.4G-only but common freq_list in $R_CONF is 5G-only — STA can never associate." >&2
            echo "       Fix with 'wifi $NUM freq <2.4G ch>' or 'wifi $NUM mode a' (or higher), then retry." >&2
            exit 11
        elif [ "$R_FREQ_BANDS" = "2G 5G" ]; then
            echo "Notice: mode=$R_MODE is 2.4G-only — 5G entries in freq_list will be silently ignored."
        fi
    fi
    if [ -n "$R_MODE" ]; then
        MODE_MASK=$(wifi_init_mode_to_bandcfg_mask "$R_MODE")
        if [ -z "$MODE_MASK" ]; then
            echo "Error: invalid radio.mode '$R_MODE' in $WIFI_INIT_CONF_JSON" >&2
            exit 2
        fi
        if [ "$IFACE" = "mlan1" ] && [ "$R_MODE" = "ax" ]; then
            echo "Error: mlan1 does not support ax (11ax)" >&2
            exit 2
        fi
    fi
    # 이 지점부터 mlanutl snapshot/SET과 disconnect/reassociate를 수행한다. 외부
    # bgscan/manual scan/connect와 같은 per-iface transition namespace를 획득해
    # rollback·association 확인까지 프로세스 수명 동안 직렬화한다.
    if ! wifi_scan_transition_lock_acquire "$IFACE"; then
        echo "Error: scan/association transition busy for $IFACE" >&2
        exit 6
    fi
    if ! wifi_wpa_abort_scan_quiesce "$IFACE"; then
        echo "Error: native scan did not quiesce for $IFACE" >&2
        exit 6
    fi
    # 실패 시 롤백용 적용 전 스냅샷
    SNAP_BAND=$(mlanutl "$IFACE" bandcfg 2>/dev/null | sed -n 's/.*Infra Band: \(0x[0-9a-fA-F]*\).*/\1/p' | head -1)
    SNAP_HT_OUT=$(mlanutl "$IFACE" htcapinfo 2>/dev/null)
    SNAP_HT_BG=$(printf '%s\n' "$SNAP_HT_OUT" | sed -n 's/^[[:space:]]*BG band:[[:space:]]*\(0x[0-9a-fA-F]*\).*/\1/p' | head -1)
    SNAP_HT_A=$(printf '%s\n' "$SNAP_HT_OUT" | sed -n 's/^[[:space:]]*A band:[[:space:]]*\(0x[0-9a-fA-F]*\).*/\1/p' | head -1)
    SNAP_VHT_OUT=$(mlanutl "$IFACE" vhtcfg 2 2 2>/dev/null)
    SNAP_VHTCAP=$(printf '%s\n' "$SNAP_VHT_OUT" | awk '/VHT Capabilities Info/ {print $NF; exit}')
    case "$SNAP_VHT_OUT" in
        *"Follow BW in the 11N"*) SNAP_VHTBW=0 ;;
        *"Follow BW in VHT"*)     SNAP_VHTBW=1 ;;
        *)                        SNAP_VHTBW="" ;;
    esac

    # bandcfg(=mode 적용)는 disconnect가 필요하다. mode를 새로 stage했거나,
    # committed mode가 라이브 bandcfg와 달라 재적용이 필요할 때만 disconnect 경로.
    # bw만 바꾸는 경우는 cap 적용 후 reassociate — 실기에서 HE 80↔40↔20
    # 양방향 전환이 검증된 무중단 경로(disconnect/reconnect는 40에서 SCANNING).
    APPLY_MODE=""
    if [ -n "$R_MODE_PEND" ]; then
        APPLY_MODE=1
    elif [ -n "$R_MODE" ] && [ -n "$MODE_MASK" ]; then
        # committed mode가 라이브와 다르면 bandcfg 재적용 — 부팅 connected-skip
        # (wifi_init.sh) 후 'wifi N radio-apply' 복구 경로가 mode를 실제 적용하도록.
        _live_band=$(printf '%s' "${SNAP_BAND:-}" | tr 'ABCDEF' 'abcdef')
        _want_band=$(printf '%s' "$MODE_MASK" | tr 'ABCDEF' 'abcdef')
        if [ "$_live_band" != "$_want_band" ]; then
            APPLY_MODE=1
            logger -p local0.info "[$tag:$LINENO] [$IFACE] committed mode=$R_MODE not live (band ${_live_band:-none} != $_want_band) — applying via bandcfg"
        fi
    fi
    logger -p local0.info "[$tag:$LINENO] [$IFACE] radio-apply: mode=${R_MODE:-keep} bw=${R_BW:-keep} (pending: mode=${R_MODE_PEND:-no} bw=${R_BW_PEND:-no}, path=$([ -n "$APPLY_MODE" ] && echo mode || echo bw-only))"

    if [ -n "$APPLY_MODE" ]; then
        # === mode 변경 경로: disconnect → bandcfg(재시도) → cap → reconfigure → reconnect ===
        # disconnect 는 재연결(끊김)을 유발 — wifi_checker 가 과도기를 '불안정'으로 오판해
        # reassociate/restart 하지 않도록 grace flag 를 세운다(TTL 은 checker 의 RECONFIGURE_GRACE_SEC).
        mkdir -p /run/wifi 2>/dev/null || true
        : > "/run/wifi/${IFACE}.reconfigure-grace" 2>/dev/null || true
        if ! wpa_cli_ok "$IFACE" disconnect; then
            echo "Error: wpa_cli disconnect failed for $IFACE" >&2
            exit 7
        fi
        # disconnect 직후 media_connected 해제가 비동기라 bandcfg가 -EOPNOTSUPP로
        # 실패할 수 있음 → 최대 3초(0.1s×30) 재시도
        BANDCFG_OK=""
        for ((_i = 1; _i <= 30; _i++)); do
            if mlanutl "$IFACE" bandcfg "$MODE_MASK" >/dev/null 2>&1; then
                BANDCFG_OK=1
                break
            fi
            sleep 0.1
        done
        if [ -z "$BANDCFG_OK" ]; then
            echo "Error: bandcfg $MODE_MASK failed for $IFACE (still connected, or band unsupported by FW — check 'wifi $NUM mode get' STANDARD)" >&2
            logger -p local0.err "[$tag:$LINENO] [$IFACE] radio-apply: bandcfg $MODE_MASK failed"
            rollback_radio_live "$IFACE"
            exit 4
        fi
        echo "bandcfg $MODE_MASK applied (mode=$R_MODE)"
        [ -n "$R_BW" ] && apply_bw_or_exit "$IFACE" "$R_BW_CAP"
        # 2차 과도기(reconfigure→reconnect)도 grace 로 덮는다 — 1차(disconnect) set 으로부터
        # bandcfg 재시도+assoc 대기로 TTL 이 소모됐을 수 있어 여기서 flag mtime 을 다시 갱신한다.
        : > "/run/wifi/${IFACE}.reconfigure-grace" 2>/dev/null || true
        if ! wpa_cli_ok "$IFACE" reconfigure; then
            echo "Error: wpa_cli reconfigure failed for $IFACE (check wpa_supplicant conf syntax)" >&2
            rollback_radio_live "$IFACE"
            exit 7
        fi
        if ! wpa_cli_ok "$IFACE" reconnect; then
            echo "Error: wpa_cli reconnect failed for $IFACE" >&2
            rollback_radio_live "$IFACE"
            exit 7
        fi
    else
        # === bw-only 경로: cap 적용 → reassociate (무중단, 실기 검증) ===
        # mode가 이미 라이브 일치 + bw 변경 없음이면 할 일이 없다 — 불필요한
        # reassociate(링크 끊김)를 막는다. (early-exit 가드는 R_MODE 있으면 통과 못 함)
        if [ -z "$R_BW" ]; then
            echo "nothing to apply for $IFACE (mode already live, no bw change)"
            exit 0
        fi
        # 위 가드로 R_BW는 항상 non-empty — apply_bw_or_exit 무조건 호출
        apply_bw_or_exit "$IFACE" "$R_BW_CAP"
        if ! wpa_cli_ok "$IFACE" reassociate; then
            echo "Error: wpa_cli reassociate failed for $IFACE" >&2
            rollback_radio_live "$IFACE"
            exit 7
        fi
    fi

    WPA_STATE=""
    # 0.1s grid 폴링 — connect와 동일 (상한 ASSOC_TIMEOUT초 유지; ASSOC_TIMEOUT은 위에서 정수 검증됨)
    for ((_i = 1; _i <= ASSOC_TIMEOUT * 10; _i++)); do
        WPA_STATE=$(wpa_cli -i "$IFACE" status 2>/dev/null | sed -n 's/^wpa_state=//p')
        [ "$WPA_STATE" = "COMPLETED" ] && break
        sleep 0.1
    done
    if [ "$WPA_STATE" != "COMPLETED" ]; then
        echo "Error: association not completed within ${ASSOC_TIMEOUT}s (state=${WPA_STATE:-unknown})" >&2
        logger -p local0.err "[$tag:$LINENO] [$IFACE] radio-apply: assoc timeout (state=${WPA_STATE:-unknown})"
        rollback_radio_live "$IFACE"
        echo "(previous settings restored; staged changes kept — fix and retry radio-apply, or radio-discard)" >&2
        exit 8
    fi

    # 트랜잭션 커밋: 적용 성공 시에만 staged → JSON persist (부팅 재적용 대상).
    # bw=default면 commit 대신 radio.bw 삭제(__del__). pending 키만 단일 jq로 원자 기록.
    COMMIT_BW="${R_BW_PEND:+$R_BW}"
    [ -n "$R_BW_DEFAULT" ] && COMMIT_BW="__del__"
    if ! update_json_radio "$IFACE" "${R_MODE_PEND:+$R_MODE}" "$COMMIT_BW"; then
        echo "Error: applied live but JSON commit failed — staged kept, retry radio-apply (boot persistence not updated)" >&2
        exit 9
    fi
    if [ -n "$R_MODE_PEND" ] || [ -n "$R_BW_PEND" ]; then
        rm -f "$(radio_pending_path "$IFACE" mode)" "$(radio_pending_path "$IFACE" bw)"
        if [ -n "$R_BW_DEFAULT" ]; then
            echo "radio.bw reset to default (AP-driven) for $IFACE in $WIFI_INIT_CONF_JSON"
        else
            echo "committed to $WIFI_INIT_CONF_JSON (mode=${R_MODE:-keep} bw=${R_BW:-keep})"
        fi
    fi
    logger -p local0.info "[$tag:$LINENO] [$IFACE] radio-apply: done (mode=${R_MODE:-keep} bw=${R_BW:-keep})"
    echo "radio-apply done for $IFACE (mode=${R_MODE:-keep} bw=${R_BW:-keep})"
    echo "--- link ---"
    # `iw link` 는 moal 에서 연결 중에도 "Not connected." 를 낼 수 있어 표시가 오해를 준다.
    wpa_cli -i "$IFACE" status 2>/dev/null | grep -E '^(bssid|freq|ssid|wpa_state)='
    iw dev "$IFACE" station dump 2>/dev/null | head -8
    echo "--- datarate ---"
    mlanutl "$IFACE" getdatarate 2>/dev/null || true
    ;;
  log)
    case "${3:-}" in
      start|stop|restart|status|enable|disable)
        exec "$WIFI_LOGGER_CONTROL_SH" "$IFACE" "$3"
        ;;
    esac
    if [ "$IFACE" != "mlan0" ] && [ "$IFACE" != "mlan1" ]; then
        echo "Error: log cp/compress/extract/reset supports mlan0/mlan1 only" >&2
        exit 1
    fi
    LOG_BASE=/var/log/cantops
    # cp/compress/extract 대상: 진단 편의로 전역 로그(cpu/logger/kern/sys/summary)도 함께 수집.
    # (log reset은 iface별 초기화라 전역 제외 — 전역 초기화는 'wifi log reset'이 담당)
    LOG_FILES=(
        "$LOG_BASE/cpu/cpu.log"
        "$LOG_BASE/logger.log"
        "$LOG_BASE/kern.log"
        "$LOG_BASE/sys.log"
        "$LOG_BASE/summary/summary.log"
        "$LOG_BASE/scan/$IFACE/ap.log"
        "$LOG_BASE/scan/$IFACE/freq.log"
        "$LOG_BASE/stat/$IFACE/stat.log"
        "$LOG_BASE/stat/$IFACE/snap.log"
        "$LOG_BASE/wpa/$IFACE/wpa.log"
    )
    EXIST=(); MISSING=()
    for _lf in "${LOG_FILES[@]}"; do
        if [ -f "$_lf" ]; then EXIST+=("$_lf"); else MISSING+=("$(basename "$_lf")"); fi
    done
    case "$3" in
      cp)
        DEST="${4:-${IFACE}_log_$(date +%Y%m%d_%H%M%S)}"
        mkdir -p "$DEST" || { echo "Error: cannot create directory $DEST" >&2; exit 1; }
        for _lf in "${EXIST[@]}"; do
            cp -a "$_lf" "$DEST/" || echo "Warning: copy failed: $_lf" >&2
        done
        echo "Copied ${#EXIST[@]} log(s) for $IFACE to $(pwd)/$DEST"
        [ ${#MISSING[@]} -gt 0 ] && echo "Skipped ${#MISSING[@]} missing: ${MISSING[*]}"
        ;;
      extract)
        if [ "$#" -lt 5 ] || [ "$#" -gt 6 ]; then
            echo "Usage: wifi $1 log extract <start> <end> [dir]" >&2
            echo "  time only: HH:MM[:SS] (today's local date)" >&2
            echo "  full time: \"YYYY-MM-DD HH:MM[:SS]\"" >&2
            exit 2
        fi
        if [ ${#EXIST[@]} -eq 0 ]; then
            echo "Error: no log files found for $IFACE" >&2
            exit 1
        fi
        if ! command -v python3 >/dev/null 2>&1; then
            echo "Error: python3 not installed" >&2
            exit 1
        fi
        _extract_script="${WIFI_LOG_EXTRACT_SCRIPT:-/usr/local/scripts/wifi_log_extract.py}"
        if [ ! -f "$_extract_script" ]; then
            _extract_script="$SCRIPT_DIR/wifi_log_extract.py"
        fi
        if [ ! -f "$_extract_script" ]; then
            echo "Error: wifi_log_extract.py not found" >&2
            exit 1
        fi
        _extract_args=(
            --iface "$IFACE"
            --start "$4"
            --end "$5"
        )
        [ -n "${6:-}" ] && _extract_args+=(--output "$6")
        if python3 "$_extract_script" "${_extract_args[@]}" "${EXIST[@]}"; then
            [ ${#MISSING[@]} -gt 0 ] && echo "Skipped ${#MISSING[@]} missing: ${MISSING[*]}"
        else
            exit $?
        fi
        ;;
      compress)
        if [ ${#EXIST[@]} -eq 0 ]; then
            echo "Error: no log files found for $IFACE" >&2
            exit 1
        fi
        if ! command -v tar >/dev/null 2>&1; then
            echo "Error: tar not installed" >&2
            exit 1
        fi
        ARCHIVE="${IFACE}_log_$(date +%Y%m%d_%H%M%S).tar.gz"
        REL=()
        for _lf in "${EXIST[@]}"; do REL+=("${_lf#"$LOG_BASE"/}"); done
        if tar -czf "$ARCHIVE" -C "$LOG_BASE" "${REL[@]}"; then
            echo "Compressed ${#EXIST[@]} log(s) for $IFACE to $(pwd)/$ARCHIVE"
            [ ${#MISSING[@]} -gt 0 ] && echo "Skipped ${#MISSING[@]} missing: ${MISSING[*]}"
        else
            rm -f "$ARCHIVE"
            echo "Error: tar failed" >&2
            exit 1
        fi
        ;;
      reset)
        case "${4:-}" in
          ""|mac) ;;
          *)
            echo "Error: invalid log reset target '$4' (expected: mac)" >&2
            exit 2
            ;;
        esac

        # iface 전용 로그는 파일을 유지하고 내용만 비운다.
        # scan/stat은 파이썬 append(재오픈 불필요), wpa/mgmt/ping은 rsyslog omfile이라
        # truncate 후 HUP로 재오픈해야 sparse hole 없이 비워짐(HUP은 연결에 영향 없음).
        # 대상은 logrotate.rsyslog의 iface별 cantops 로그와 동기화.
        # wifi_logger_stat.py의 AP별 파일명 형식(xx_xx_xx_xx_xx_xx.log)만 별도로 수집한다.
        _mac_pair='[[:xdigit:]][[:xdigit:]]'
        _mac_log_pattern="$LOG_BASE/stat/$IFACE/${_mac_pair}_${_mac_pair}_${_mac_pair}_${_mac_pair}_${_mac_pair}_${_mac_pair}.log"
        MAC_LOG_FILES=()
        while IFS= read -r _lf; do
            [ -f "$_lf" ] && MAC_LOG_FILES+=("$_lf")
        done < <(compgen -G "$_mac_log_pattern" || true)

        if [ "${4:-}" = "mac" ]; then
            RESET_FILES=("${MAC_LOG_FILES[@]}")
        else
            RESET_FILES=(
                "$LOG_BASE/scan/$IFACE/ap.log"
                "$LOG_BASE/scan/$IFACE/freq.log"
                "$LOG_BASE/stat/$IFACE/stat.log"
                "$LOG_BASE/stat/$IFACE/snap.log"
                "$LOG_BASE/wpa/$IFACE/wpa.log"
                "$LOG_BASE/mgmt/$IFACE/mgmt.log"
                "$LOG_BASE/mgmt/$IFACE/gmgmt.log"
                "$LOG_BASE/ping/$IFACE/ping.log"
            )
            RESET_FILES+=("${MAC_LOG_FILES[@]}")
        fi
        _rn=0; _rmiss=()
        for _lf in "${RESET_FILES[@]}"; do
            if [ -f "$_lf" ]; then
                if : > "$_lf"; then _rn=$((_rn+1)); else echo "Warning: truncate failed: $_lf" >&2; fi
            else
                _rmiss+=("$(basename "$_lf")")
            fi
        done

        if [ "${4:-}" = "mac" ]; then
            # wifi_logger_stat.py는 매 기록마다 append-open하므로 재시작/HUP이 필요 없다.
            echo "Reset $_rn AP MAC log(s) for $IFACE"
        else
            # wpa/mgmt/ping은 rsyslog 소유 → HUP로 재오픈(WiFi 연결엔 영향 없음)
            if systemctl kill -s HUP rsyslog 2>/dev/null; then
                echo "Reset $_rn log(s) for $IFACE, rsyslog reopened (HUP)"
            else
                echo "Reset $_rn log(s) for $IFACE"
                echo "Warning: rsyslog HUP failed — wpa/mgmt/ping may need manual reopen" >&2
            fi
        fi
        [ ${#_rmiss[@]} -gt 0 ] && echo "Skipped ${#_rmiss[@]} missing: ${_rmiss[*]}"
        ;;
      *)
        usage
        ;;
    esac
    ;;
  ip)
    set -euo pipefail
    shift 2
    CONF=$(find_network_conf "$IFACE") \
        || { echo "not found: no .network for $IFACE in /etc/systemd/network" >&2; exit 1; }
    if [ $# -lt 1 ]; then echo "usage: wifi <iface> ip <address/netmask>" >&2; exit 1; fi
    NEW_IP="$1"
    TMP_FILE="$(mktemp)"
    trap 'rm -f "$TMP_FILE"; sync 2>/dev/null || true' EXIT
    if [ "$NEW_IP" = "0" ]; then
        # Address 줄 삭제
        if awk '
            BEGIN { found = 0 }
            /^[[:space:]]*#/ { print; next }
            /^[[:space:]]*Address[[:space:]]*=/ { found = 1; next }
            { print }
            END { if (!found) exit 1 }
        ' "$CONF" > "$TMP_FILE"; then
            safe_install_sync "$TMP_FILE" "$CONF"
            echo "Address removed from $CONF"
        else echo "no Address= line found in $CONF" >&2; exit 1; fi
    else
        # 오타/범위 검사 — 여기가 없으면 'apply' 같은 서브커맨드 워드가 그대로
        # Address=apply/24 로 persist되고(아래 /24 기본값이 오타를 삼킨다),
        # systemd-networkd가 그 줄만 버려서 다음 부팅에 정적 IP가 사라진다.
        # /24를 붙이기 전에 검사해야 오류 메시지에 사용자가 친 값이 그대로 나온다.
        if ! is_valid_ipv4 "$NEW_IP" && ! is_valid_ipv4_cidr "$NEW_IP"; then
            echo "Error: invalid IP address '$NEW_IP' (expected A.B.C.D or A.B.C.D/0-32)" >&2
            # 'wifi <iface> ip apply'는 iface 인자 탓에 전역 apply arm에 닿지 못하고
            # 여기로 떨어진다 — 흔한 오타라 올바른 형태를 짚어준다.
            if [ "$NEW_IP" = "apply" ]; then
                echo "       설정을 반영하려면 iface 없이: wifi ip apply" >&2
            fi
            exit 1
        fi
        # subnet mask가 없으면 /24 기본 적용
        if [[ "$NEW_IP" != */* ]]; then
            NEW_IP="${NEW_IP}/24"
            echo "No subnet mask specified, using default /24"
        fi
        awk -v new_ip="$NEW_IP" '
            BEGIN { in_net = 0; done = 0 }
            /^[[:space:]]*#/ { print; next }
            /^[[:space:]]*\[/ {
                if (in_net && !done) { print "Address=" new_ip; done = 1 }
                in_net = ($0 ~ /^[[:space:]]*\[[Nn]etwork\]/)
                print; next
            }
            in_net && /^[[:space:]]*Address[[:space:]]*=/ {
                if (!done) { print "Address=" new_ip; done = 1 }
                next
            }
            { print }
            END {
                if (!done) {
                    if (in_net) print "Address=" new_ip
                    else { print "[Network]"; print "Address=" new_ip }
                }
            }
        ' "$CONF" > "$TMP_FILE"
        safe_install_sync "$TMP_FILE" "$CONF"
        echo "Address set to \"$NEW_IP\" in $CONF"
    fi
    ;;
  gt)
    set -euo pipefail
    shift 2
    CONF=$(find_network_conf "$IFACE") \
        || { echo "not found: no .network for $IFACE in /etc/systemd/network" >&2; exit 1; }
    if [ $# -lt 1 ]; then echo "usage: wifi <iface> gt <address>" >&2; exit 1; fi
    NEW_GT="$1"
    TMP_FILE="$(mktemp)"
    trap 'rm -f "$TMP_FILE"; sync 2>/dev/null || true' EXIT
    if [ "$NEW_GT" = "0" ]; then
        # Gateway 줄 삭제 — sibling인 `ip 0`(Address 삭제)과 대칭.
        if awk '
            BEGIN { found = 0 }
            /^[[:space:]]*#/ { print; next }
            /^[[:space:]]*Gateway[[:space:]]*=/ { found = 1; next }
            { print }
            END { if (!found) exit 1 }
        ' "$CONF" > "$TMP_FILE"; then
            safe_install_sync "$TMP_FILE" "$CONF"
            echo "Gateway removed from $CONF"
            exit 0
        else echo "no Gateway= line found in $CONF" >&2; exit 1; fi
    fi
    # 오타/범위 검사 — Gateway=는 prefix 없는 순수 주소. 이 가드가 없으면 임의
    # 문자열이 기존의 유효한 Gateway= 줄을 덮어써 기본 경로가 조용히 사라진다.
    # ip 커맨드가 {address/netmask}를 받는 탓에 /24를 딸려 치는 오타가 흔해 별도 안내.
    case "$NEW_GT" in
        */*) echo "Error: gateway must not carry a prefix — use '${NEW_GT%%/*}' (wifi <iface> gt <address>)" >&2; exit 1 ;;
    esac
    if ! is_valid_ipv4 "$NEW_GT"; then
        echo "Error: invalid gateway address '$NEW_GT' (expected A.B.C.D, or 0 to remove)" >&2
        exit 1
    fi
    awk -v new_gt="$NEW_GT" '
        BEGIN { in_net = 0; done = 0 }
        /^[[:space:]]*#/ { print; next }
        /^[[:space:]]*\[/ {
            if (in_net && !done) { print "Gateway=" new_gt; done = 1 }
            in_net = ($0 ~ /^[[:space:]]*\[[Nn]etwork\]/)
            print; next
        }
        in_net && /^[[:space:]]*Gateway[[:space:]]*=/ {
            if (!done) { print "Gateway=" new_gt; done = 1 }
            next
        }
        { print }
        END {
            if (!done) {
                if (in_net) print "Gateway=" new_gt
                else { print "[Network]"; print "Gateway=" new_gt }
            }
        }
    ' "$CONF" > "$TMP_FILE"
    safe_install_sync "$TMP_FILE" "$CONF"
    rm -f "$TMP_FILE"
    echo "Gateway set to \"$NEW_GT\" in $CONF"
    ;;
  *)
    usage
    ;;
esac
