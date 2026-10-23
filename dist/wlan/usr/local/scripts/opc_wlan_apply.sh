#!/bin/sh
# opc_wlan_apply.sh — OPC 무선설정 적용 (wpa_supplicant conf 직접편집 + reconfigure)
#
# 사용: opc_wlan_apply.sh <iface> [--netid N] [freq "<mhz ...>"|none] [ssid <name>]
#                        [mode b|g|a|n|ac|ax] [bw 20|40|80]
#   --netid N : (하위호환 인자) 단일 network 블록 전제 — 현재는 모든 블록에 적용하므로 무시.
#   freq/ssid/mode/bw : 하나 이상 지정. mode/bw는 드라이버 상한을 제한한다.
#
# 책임: wpa_supplicant conf 파일을 직접 수정(ssid/전역+블록 freq_list)하고
#       `wpa_cli reconfigure` 로 프로세스 종료 없이 동적 적용 + 영속을 한 번에 처리.
#       mode/bw는 드라이버에 적용하고 wifi_init_conf.json에 부팅 재적용 값을 저장.
#       적용 실패 시 conf/JSON/드라이버 snapshot으로 best-effort 복구한다.
#       AP 재연결 완료는 기다리지 않음 (결과는 WlanStatusChange indication 이 통지).
#
# 왜 save_config 가 아니라 conf 직접편집인가:
#   wpa_supplicant v2.10 의 save_config 는 freq_list 를 직렬화하지 않아(verified
#   on-target) 영속이 깨지고, update_config=1 conf 를 통째로 재생성하며 주석/포맷도
#   손실된다. conf 를 우리가 직접 쓰면 공통 freq_list/ssid 가 그대로 영속되고,
#   reconfigure 가 conf 를 재로드하면서 freq_list(하드 밴드 락)까지 즉시 반영된다.
#   (set_network 런타임 변경은 reconfigure 시 conf 값으로 덮어써지므로 쓰지 않는다.)
#
# 적용 트리거: wpa_cli reconfigure — 전체 conf 재로드라 재연결(끊김)이 발생할 수 있다.
#   끊김이 문제가 되면 freq_list 를 런타임 전용(set_network)으로 관리하는 방식으로
#   후속 전환을 검토한다.
#
# exit: 0=ok / 2=usage / 3=ctrl_interface 부재 /
#       4=conf/JSON/드라이버 적용 실패 / 5=reconfigure 실패 /
#       6=transaction 취소 또는 rollback 실패 / mlan0 ssid 설치 시 ChangeIp ESSID
#         복원 표식 부재(conf 미변경, #362)
set -u

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# 설치 이미지에서는 같은 scripts 디렉터리, 개발/대체 진입점에서는 정규 설치 경로를 쓴다.
if [ -r "$SCRIPT_DIR/wifi_init_config_lib.sh" ]; then
    # shellcheck source=./wifi_init_config_lib.sh
    . "$SCRIPT_DIR/wifi_init_config_lib.sh"
elif [ -r /usr/local/scripts/wifi_init_config_lib.sh ]; then
    # shellcheck source=/usr/local/scripts/wifi_init_config_lib.sh
    . /usr/local/scripts/wifi_init_config_lib.sh
else
    echo "opc_wlan_apply: wifi_init_config_lib.sh not found" >&2
    exit 4
fi

IFACE="${1:-}"
[ -n "$IFACE" ] || { echo "usage: $0 <iface> [--netid N] [freq \"<mhz ...>\"|none] [ssid <name>]" >&2; exit 2; }
shift

FREQS=""
HAVE_FREQ=0
SSID=""
HAVE_SSID=0
MODE=""; BW=""; MODE_MASK=""; HT_CAP=""; VHT_BW=""
while [ $# -gt 0 ]; do
    case "$1" in
        --netid) [ $# -gt 1 ] || { echo "opc_wlan_apply: --netid requires an argument" >&2; exit 2; }
                 shift 2 ;;   # 단일 network 블록 전제 — netid 무시(모든 블록에 적용)
        freq)    [ $# -gt 1 ] || { echo "opc_wlan_apply: freq requires an argument (MHz list, or 'none' to clear the band lock)" >&2; exit 2; }
                 HAVE_FREQ=1
                 # 'none' 또는 빈 문자열 = 밴드락 제거(전역/블록 freq_list + legacy scan_freq 삭제).
                 case "$2" in none|"") FREQS="" ;; *) FREQS="$2" ;; esac
                 shift 2 ;;
        ssid)    [ $# -gt 1 ] || { echo "opc_wlan_apply: ssid requires an argument" >&2; exit 2; }
                 SSID="$2"; HAVE_SSID=1; shift 2 ;;
        mode)    [ $# -gt 1 ] && [ -n "$2" ] || { echo "opc_wlan_apply: mode requires an argument" >&2; exit 2; }
                 MODE="$2"; shift 2 ;;
        bw)      [ $# -gt 1 ] && [ -n "$2" ] || { echo "opc_wlan_apply: bw requires an argument" >&2; exit 2; }
                 BW="$2"; shift 2 ;;
        *)       echo "opc_wlan_apply: unknown arg '$1'" >&2; exit 2 ;;
    esac
done
[ "$HAVE_FREQ" = 1 ] || [ "$HAVE_SSID" = 1 ] || [ -n "$MODE$BW" ] \
    || { echo "opc_wlan_apply: nothing to apply" >&2; exit 2; }
case "$IFACE" in mlan0|mlan1) ;; *) echo "opc_wlan_apply: unsupported interface '$IFACE'" >&2; exit 2 ;; esac
case "$FREQS" in *[!0-9\ ]*) echo "opc_wlan_apply: invalid freq '$FREQS' (digits/spaces only)" >&2; exit 2 ;; esac
if [ -n "$MODE" ]; then
    MODE_MASK=$(wifi_init_mode_to_bandcfg_mask "$MODE") \
        || { echo "opc_wlan_apply: unsupported mode '$MODE'" >&2; exit 2; }
    [ "$IFACE:$MODE" != "mlan1:ax" ] \
        || { echo "opc_wlan_apply: mlan1 does not support ax" >&2; exit 2; }
fi
if [ -n "$BW" ]; then
    case "$BW" in 20|40|80) ;; *) echo "opc_wlan_apply: unsupported bandwidth '$BW'" >&2; exit 2 ;; esac
    HT_CAP=$(wifi_init_bw_to_htcap "$BW") || exit 2
    VHT_BW=$(wifi_init_bw_to_vhtbw "$BW") || exit 2
fi

CONF="${WPA_CONF_DIR:-/etc/wpa_supplicant}/wpa_supplicant-${IFACE}.conf"
CONF_DIR=${CONF%/*}
WIFI_RUN_DIR="${WIFI_RUN_DIR:-/run/wifi}"
[ -f "$CONF" ] || { echo "opc_wlan_apply: conf not found: $CONF" >&2; exit 4; }
RADIO_JSON="${WIFI_INIT_CONF_JSON:-/usr/local/etc/wifi_init_conf.json}"
RADIO_DIR=${RADIO_JSON%/*}
if [ -n "$MODE$BW" ]; then
    command -v mlanutl >/dev/null 2>&1 \
        && wifi_init_conf_status "$RADIO_JSON" \
        && jq -e 'type == "object"' "$RADIO_JSON" >/dev/null 2>&1 \
        || { echo "opc_wlan_apply: invalid/unavailable radio tools or JSON" >&2; exit 4; }
fi
wifi_change_guard_acquire "$IFACE" shared \
    || { echo "opc_wlan_apply: configuration/connection change busy for $IFACE" >&2; exit 4; }
wifi_wpa_conf_lock_acquire "$IFACE" \
    || { echo "opc_wlan_apply: failed to lock $CONF" >&2; exit 4; }
# OPC ChangeIp 의 ESSID 는 opcd 가 복원 표식(essid_marker, "표식 없으면 conf 편집 없음")을 arm 한
# 뒤에만 mlan0 conf 에 설치할 수 있다 — 표식 없는 설치는 재부팅 때 되돌릴 수 없는 영구 변경이다.
# arm 과 이 lock 사이에 다른 작성자(wifi 0 ssid/connect, 공장초기화)가 표식을 지웠다면 그 작성자의
# SSID 가 최종값이므로 conf 를 건드리지 않고 거부한다(#362 Tribunal A-R1-002). opcd 는 비0 종료를
# settle 로 처리한다(conf 가 요청값이 아니면 커밋하지 않음).
OPC_ESSID_MARKER="${OPC_ESSID_MARKER:-/usr/local/opc/etc/changeip_essid.restore}"
if [ "$HAVE_SSID" = 1 ] && [ "$IFACE" = "mlan0" ] && [ ! -e "$OPC_ESSID_MARKER" ]; then
    echo "opc_wlan_apply: ChangeIp ESSID restore marker is missing; refusing to install ssid on mlan0" >&2
    exit 6
fi
# A mode-only request derives its effective frequencies from CONF. Resolve and
# validate them only while holding the writer lock, so a concurrent frequency
# update cannot replace the checked 2.4GHz list with a 5GHz-only list before
# this transaction applies a b/g radio cap.
if [ "$MODE" = b ] || [ "$MODE" = g ]; then
    CHECK_FREQS="$FREQS"
    if [ "$HAVE_FREQ" = 0 ]; then
        CHECK_FREQS=$(wifi_wpa_conf_common_freqs "$CONF") || exit 4
    fi
    HAS_2G=0; HAS_5G=0
    for mhz in $CHECK_FREQS; do
        [ "$mhz" -ge 2400 ] && [ "$mhz" -lt 2500 ] && HAS_2G=1
        [ "$mhz" -ge 5000 ] && HAS_5G=1
    done
    if [ "$HAS_2G" = 0 ] && [ "$HAS_5G" = 1 ]; then
        echo "opc_wlan_apply: mode=$MODE cannot use a 5GHz-only frequency list" >&2
        exit 2
    fi
fi
wifi_scan_transition_lock_acquire "$IFACE" \
    || { echo "opc_wlan_apply: failed to lock scan transition for $IFACE" >&2; exit 4; }
if [ -n "$MODE$BW" ]; then
    wifi_radio_json_lock_acquire \
        || { echo "opc_wlan_apply: radio JSON transaction busy" >&2; exit 4; }
fi

# boot snapshot Mode A 또는 실제 다중블록 conf는 SSID 일괄교체가 기본 SSID를
# 소실시키므로 거부한다(exit 2=usage). freq 변경은 물리 대역 공통이라 허용한다.
if [ "$HAVE_SSID" = 1 ] && wifi_wpa_run_child_call wifi_wpa_conf_is_multi_topology "$IFACE" "$CONF"; then
    echo "opc_wlan_apply: $CONF uses multiple network blocks - refusing bulk ssid change" >&2
    echo "                SSID selection is handled automatically by the boot-latched owner policy." >&2
    echo "                To reconnect the current network, use 'wifi <iface> connect' without an SSID." >&2
    exit 2
fi

# Validate and encode the exact SSID before abort_scan or any other live
# control mutation.  In Mode B an immutable extra is intentionally a manual
# candidate that may replace the sole base block.
if [ "$HAVE_SSID" = 1 ]; then
    SSID_CONF_VALUE=$(wifi_wpa_child_call wifi_ssid_to_conf_value "$SSID") \
        || { echo "opc_wlan_apply: SSID must be valid UTF-8, 1-32 bytes, without C0 controls or DEL" >&2; exit 2; }
fi

# ctrl interface 가용 확인 (wpa_supplicant 미동작이면 reconfigure 불가 → 3).
wifi_wpa_run_child wpa_cli -i "$IFACE" ping >/dev/null 2>&1 \
    || { echo "opc_wlan_apply: wpa_cli ctrl unavailable for $IFACE" >&2; exit 3; }
if ! wifi_wpa_run_child_call wifi_wpa_abort_scan_quiesce "$IFACE"; then
    echo "opc_wlan_apply: cannot quiesce scan for $IFACE" >&2
    exit 5
fi

# --- conf 직접 편집 (atomic: 같은 fs 임시파일 → chmod → rename, 원본은 롤백용 백업) -
# ssid → 중간파일에서 network 블록의 ssid= 치환(없으면 블록 끝에 추가).
# freq → 공용 renderer가 전역/모든 블록에 같은 freq_list를 쓰고 legacy scan_freq를 제거.
# SSID는 공통 UTF-8/길이/control 계약으로 검증한 뒤 wifi_ssid_to_conf_value가
# 고른 표기로 쓴다 — 안전하면 따옴표(사람이 읽는다), SSID에 `"`가 있으면 hex.
# 어느 쪽이든 공백·백슬래시·UTF-8이 byte-exact로 보존된다.
# FREQS 는 숫자/공백만 허용 — 직접 호출 시 awk -v 로 들어가는 값에 개행 등이 섞여
# conf 라인이 인젝션되는 것을 차단(데몬 경로는 정수만 전달하나 방어적으로 검증).
case "$FREQS" in *[!0-9\ ]*) echo "opc_wlan_apply: invalid freq '$FREQS' (digits/spaces only)" >&2; exit 2 ;; esac
# busybox awk 가 ENVIRON 을 미지원하면 아래 SSID 전달이 "" 로 조용히 덮어써져 conf 의
# ssid 가 빈 값으로 손상된다(awk 는 exit 0 → opcd 는 성공 오인하는 silent failure).
# SSID 적용 시에만 ENVIRON 지원을 사전 검증하고, 미지원이면 편집 전에 비-0(exit 4)로
# 실패를 알린다. (freq 경로는 -v 전달이라 ENVIRON 과 무관 — 밴드락은 영향받지 않는다.)
if [ "$HAVE_SSID" = 1 ]; then
    OPC_ENVIRON_PROBE=ok wifi_wpa_run_child awk 'BEGIN { exit(ENVIRON["OPC_ENVIRON_PROBE"] == "ok" ? 0 : 1) }' </dev/null \
        || { echo "opc_wlan_apply: awk lacks ENVIRON support - cannot apply ssid safely" >&2; exit 4; }
fi
# trap 을 mktemp 보다 먼저 등록 — 임시파일 생성과 trap 등록 사이에 시그널이 와도
# 파일이 남지 않도록(누출 창 제거). ROLLBACK_REQUIRED는 새 conf 설치 직전부터
# reconfigure 성공까지 1이며, 이 구간의 모든 exit/signal은 원본 복원을 거친다.
BAK=""; EDIT_TMP=""; TMP=""; ROLLBACK_REQUIRED=0
TRANSACTION_COMMITTED=0
RADIO_BAK=""; RADIO_TMP=""; RADIO_ROLLBACK_REQUIRED=0
LIVE_ROLLBACK_REQUIRED=0; MODE_CHANGED=0
SNAP_BAND=""; SNAP_HT_BG=""; SNAP_HT_A=""; SNAP_VHT_BW=""; SNAP_VHT_CAP=""
opc_sync_required() {
    wifi_wpa_run_child sync "$1" 2>/dev/null || wifi_wpa_run_child sync 2>/dev/null
}

opc_radio_snapshot_and_stage() {
    OPC_RADIO_OUTPUT=$(wifi_wpa_child_exec mlanutl "$IFACE" bandcfg 2>/dev/null) || return 1
    SNAP_BAND=$(printf '%s\n' "$OPC_RADIO_OUTPUT" | wifi_wpa_run_child awk '/Infra Band:/ { print tolower($3); exit }')
    OPC_RADIO_OUTPUT=$(wifi_wpa_child_exec mlanutl "$IFACE" htcapinfo 2>/dev/null) || return 1
    SNAP_HT_BG=$(printf '%s\n' "$OPC_RADIO_OUTPUT" | wifi_wpa_run_child awk '/BG band:/ { print $3; exit }')
    SNAP_HT_A=$(printf '%s\n' "$OPC_RADIO_OUTPUT" | wifi_wpa_run_child awk '/A band:/ { print $3; exit }')
    [ -n "$SNAP_BAND" ] && [ -n "$SNAP_HT_BG" ] && [ -n "$SNAP_HT_A" ] || return 1
    OPC_RADIO_OUTPUT=$(wifi_wpa_child_exec mlanutl "$IFACE" vhtcfg 2 2 2>/dev/null) || OPC_RADIO_OUTPUT=""
    SNAP_VHT_CAP=$(printf '%s\n' "$OPC_RADIO_OUTPUT" | wifi_wpa_run_child awk '/VHT Capabilities Info/ { print $NF; exit }')
    case "$OPC_RADIO_OUTPUT" in
        *"Follow BW in the 11N"*) SNAP_VHT_BW=0 ;;
        *"Follow BW in VHT"*) SNAP_VHT_BW=1 ;;
    esac
    # Do not begin a transaction that cannot apply/restore the VHT limit.
    if [ "$BW" = 80 ]; then
        [ -n "$SNAP_VHT_CAP" ] && [ -n "$SNAP_VHT_BW" ] || return 1
    fi
    [ -z "$SNAP_VHT_CAP" ] || [ -n "$SNAP_VHT_BW" ] || return 1
    RADIO_BAK=$(wifi_wpa_child_exec mktemp "${RADIO_JSON}.opc-bak.XXXXXX") || return 1
    wifi_wpa_run_child cp -p "$RADIO_JSON" "$RADIO_BAK" || return 1
    opc_sync_required "$RADIO_BAK" && opc_sync_required "$RADIO_DIR" || return 1
    RADIO_TMP=$(wifi_wpa_child_exec mktemp "${RADIO_JSON}.opc.XXXXXX") || return 1
    wifi_wpa_run_child cp -p "$RADIO_JSON" "$RADIO_TMP" || return 1
    wifi_wpa_run_child jq --arg i "$IFACE" --arg m "$MODE" --arg b "$BW" '
        if $m != "" then .[$i].radio.mode = $m else . end |
        if $b != "" then .[$i].radio.bw = $b else . end
    ' "$RADIO_JSON" > "$RADIO_TMP" || return 1
    opc_sync_required "$RADIO_TMP"
}

opc_radio_wpa_ok() {
    OPC_RADIO_REPLY=$(wifi_wpa_child_exec wpa_cli -i "$IFACE" "$1" 2>/dev/null) || return 1
    [ "$OPC_RADIO_REPLY" = OK ]
}

opc_radio_apply_live() {
    OPC_RADIO_APPLIED=0
    OPC_RADIO_WANT_BAND=$(printf '%s' "$MODE_MASK" | wifi_wpa_run_child tr 'ABCDEF' 'abcdef')
    LIVE_ROLLBACK_REQUIRED=1
    if [ -n "$MODE" ] && [ "$SNAP_BAND" != "$OPC_RADIO_WANT_BAND" ]; then
        MODE_CHANGED=1
        opc_radio_wpa_ok disconnect || return 1
        # The outer NXP call has a 900/450ms deadline. Keep this local retry
        # short; association completes later and is reported by indication.
        for OPC_RADIO_ATTEMPT in 1 2 3 4 5; do
            if wifi_wpa_run_child mlanutl "$IFACE" bandcfg "$MODE_MASK" >/dev/null 2>&1; then
                OPC_RADIO_APPLIED=1
                break
            fi
            [ "$OPC_RADIO_ATTEMPT" = 5 ] || wifi_wpa_run_child sleep 0.02
        done
        [ "$OPC_RADIO_APPLIED" = 1 ] || return 1
    fi
    if [ -n "$BW" ]; then
        wifi_wpa_run_child mlanutl "$IFACE" htcapinfo "$HT_CAP" >/dev/null 2>&1 || return 1
        if [ -n "$SNAP_VHT_CAP" ]; then
            wifi_wpa_run_child mlanutl "$IFACE" vhtcfg 2 2 "$VHT_BW" "$SNAP_VHT_CAP" >/dev/null 2>&1 || return 1
        fi
    fi
}

opc_radio_restore_live() {
    OPC_RADIO_RESTORE_OK=1
    if [ "$MODE_CHANGED" = 1 ]; then
        opc_radio_wpa_ok disconnect || OPC_RADIO_RESTORE_OK=0
        wifi_wpa_run_child mlanutl "$IFACE" bandcfg "$SNAP_BAND" >/dev/null 2>&1 || OPC_RADIO_RESTORE_OK=0
    fi
    if [ -n "$BW" ]; then
        wifi_wpa_run_child mlanutl "$IFACE" htcapinfo "$SNAP_HT_BG" 1 >/dev/null 2>&1 || OPC_RADIO_RESTORE_OK=0
        wifi_wpa_run_child mlanutl "$IFACE" htcapinfo "$SNAP_HT_A" 2 >/dev/null 2>&1 || OPC_RADIO_RESTORE_OK=0
        if [ -n "$SNAP_VHT_CAP" ]; then
            wifi_wpa_run_child mlanutl "$IFACE" vhtcfg 2 2 "$SNAP_VHT_BW" "$SNAP_VHT_CAP" >/dev/null 2>&1 || OPC_RADIO_RESTORE_OK=0
        fi
    fi
    [ "$OPC_RADIO_RESTORE_OK" = 1 ]
}

opc_transaction_cleanup() {
    cleanup_rc=$?
    trap - EXIT HUP INT TERM
    # One commit marker governs every resource. A signal after commit must
    # never restore only some of conf/JSON/driver state.
    if [ "$TRANSACTION_COMMITTED" = 1 ]; then
        ROLLBACK_REQUIRED=0
        RADIO_ROLLBACK_REQUIRED=0
        LIVE_ROLLBACK_REQUIRED=0
    fi
    [ -z "$EDIT_TMP" ] || wifi_wpa_run_child rm -f "$EDIT_TMP"
    [ -z "$TMP" ] || wifi_wpa_run_child rm -f "$TMP"
    EDIT_TMP=""
    TMP=""
    [ -z "$RADIO_TMP" ] || wifi_wpa_run_child rm -f "$RADIO_TMP"
    RADIO_TMP=""

    if [ "$LIVE_ROLLBACK_REQUIRED" = 1 ]; then
        if ! opc_radio_restore_live; then
            echo "opc_wlan_apply: CRITICAL: driver rollback failed" >&2
            cleanup_rc=6
        fi
    fi
    if [ "$RADIO_ROLLBACK_REQUIRED" = 1 ]; then
        RADIO_TMP=$(wifi_wpa_child_exec mktemp "${RADIO_JSON}.opc-rollback.XXXXXX") || RADIO_TMP=""
        if [ -n "$RADIO_TMP" ] \
            && wifi_wpa_run_child cp -p "$RADIO_BAK" "$RADIO_TMP" \
            && opc_sync_required "$RADIO_TMP" \
            && wifi_wpa_run_child mv -f "$RADIO_TMP" "$RADIO_JSON" \
            && opc_sync_required "$RADIO_JSON" && opc_sync_required "$RADIO_DIR"; then
            RADIO_ROLLBACK_REQUIRED=0
            RADIO_TMP=""
        else
            echo "opc_wlan_apply: CRITICAL: radio JSON rollback failed; backup $RADIO_BAK" >&2
            cleanup_rc=6
        fi
        [ -z "$RADIO_TMP" ] || wifi_wpa_run_child rm -f "$RADIO_TMP"
    fi

    if [ "$ROLLBACK_REQUIRED" = 1 ]; then
        rollback_ok=1
        if [ -z "$BAK" ] || [ ! -f "$BAK" ]; then
            rollback_ok=0
        else
            TMP=$(wifi_wpa_child_exec mktemp "${CONF}.rollback.XXXXXX") || rollback_ok=0
        fi
        if [ "$rollback_ok" -eq 1 ]; then
            wifi_wpa_run_child cp -p "$BAK" "$TMP" || rollback_ok=0
        fi
        if [ "$rollback_ok" -eq 1 ]; then
            opc_sync_required "$TMP" || rollback_ok=0
        fi
        if [ "$rollback_ok" -eq 1 ]; then
            if wifi_wpa_run_child mv -f "$TMP" "$CONF"; then
                TMP=""
            else
                rollback_ok=0
            fi
        fi
        if [ "$rollback_ok" -eq 1 ]; then
            opc_sync_required "$CONF" || rollback_ok=0
        fi
        if [ "$rollback_ok" -eq 1 ]; then
            opc_sync_required "$CONF_DIR" || rollback_ok=0
        fi

        if [ "$rollback_ok" -eq 1 ]; then
            ROLLBACK_REQUIRED=0
            # 복원 inode+directory가 durable해진 뒤에만 원본 backup을 소비한다.
            if wifi_wpa_run_child rm -f "$BAK"; then
                BAK=""
            fi
            # live daemon도 복원본을 읽게 한다. 원래 실패/cancel의 반환값은 보존한다.
            if [ "$LIVE_ROLLBACK_REQUIRED" = 0 ]; then
                wifi_wpa_run_child wpa_cli -i "$IFACE" reconfigure >/dev/null 2>&1 || true
            fi
            if [ "$cleanup_rc" -eq 5 ]; then
                echo "opc_wlan_apply: reconfigure failed for $IFACE - conf rolled back" >&2
            else
                echo "opc_wlan_apply: transaction interrupted - conf rolled back" >&2
            fi
        else
            [ -z "$TMP" ] || wifi_wpa_run_child rm -f "$TMP"
            TMP=""
            if [ -n "$BAK" ] && [ -f "$BAK" ]; then
                echo "opc_wlan_apply: CRITICAL: rollback failed; original backup retained at $BAK" >&2
            else
                echo "opc_wlan_apply: CRITICAL: rollback failed; original backup unavailable at ${BAK:-<unset>}" >&2
            fi
            cleanup_rc=6
        fi
    elif [ -n "$BAK" ]; then
        wifi_wpa_run_child rm -f "$BAK"
        BAK=""
    fi
    if [ "$LIVE_ROLLBACK_REQUIRED" = 1 ]; then
        if ! opc_radio_wpa_ok reconfigure || ! opc_radio_wpa_ok reconnect; then
            echo "opc_wlan_apply: CRITICAL: reconnect after radio rollback failed" >&2
            cleanup_rc=6
        fi
    fi
    if [ "$RADIO_ROLLBACK_REQUIRED" = 0 ] && [ -n "$RADIO_BAK" ]; then
        wifi_wpa_run_child rm -f "$RADIO_BAK"
    fi
    exit "$cleanup_rc"
}
trap 'opc_transaction_cleanup' EXIT
trap 'exit 6' HUP INT TERM
if [ -n "$MODE$BW" ]; then
    opc_radio_snapshot_and_stage \
        || { echo "opc_wlan_apply: radio snapshot/JSON staging failed" >&2; exit 4; }
fi
BAK="$(wifi_wpa_child_exec mktemp "${CONF}.bak.XXXXXX")" || { echo "opc_wlan_apply: mktemp(bak) failed" >&2; exit 4; }
wifi_wpa_run_child cp -p "$CONF" "$BAK" || { echo "opc_wlan_apply: backup failed" >&2; exit 4; }
# rollback inode가 rename 전에 durable해야 전원 장애 후에도 원본 복구를 보장한다.
opc_sync_required "$BAK" \
    || { echo "opc_wlan_apply: backup sync failed" >&2; exit 4; }
# 새 backup 이름도 directory에 영속된 뒤에만 CONF를 교체한다. 파일 inode sync만으로는
# 정전 후 backup directory entry의 존재를 보장하지 못한다.
opc_sync_required "$CONF_DIR" \
    || { echo "opc_wlan_apply: backup directory sync failed" >&2; exit 4; }
EDIT_TMP="$(wifi_wpa_child_exec mktemp "${CONF}.edit.XXXXXX")" || { echo "opc_wlan_apply: mktemp(edit) failed" >&2; exit 4; }
TMP="$(wifi_wpa_child_exec mktemp "${CONF}.XXXXXX")" || { echo "opc_wlan_apply: mktemp failed" >&2; exit 4; }

# 검증된 hex token을 ENVIRON으로 전달한다. ENVIRON 미지원 awk는 위 probe에서
# 걸러져 이 경로에 도달하지 않는다.
OPC_SSID_CONF="${SSID_CONF_VALUE:-}" wifi_wpa_run_child awk -v do_ssid="$HAVE_SSID" '
    BEGIN { in_net = 0; blocks = 0; ssid_done = 0; new_ssid = ENVIRON["OPC_SSID_CONF"] }
    /^[[:space:]]*#/ { print; next }
    /^[[:space:]]*network[[:space:]]*=[[:space:]]*\{/ {
        in_net = 1; blocks++; ssid_done = 0; print; next
    }
    in_net && /^[[:space:]]*\}/ {
        if (do_ssid && !ssid_done)  { print "    ssid=" new_ssid; ssid_done = 1 }
        in_net = 0; print; next
    }
    do_ssid && in_net && /^[[:space:]]*ssid[[:space:]]*=/ {
        if (!ssid_done) { print "    ssid=" new_ssid; ssid_done = 1 } next
    }
    { print }
    END {
        if (blocks == 0) { print "error: no network={ block in " FILENAME > "/dev/stderr"; exit 1 }
        if (blocks > 1) { print "warn: " blocks " network blocks present - all modified (single-block assumed)" > "/dev/stderr" }
    }
' "$CONF" > "$EDIT_TMP" || { echo "opc_wlan_apply: conf edit failed" >&2; exit 4; }

# SSID-only 요청도 기존 공통 목록을 해석해 canonical 형식을 보존/이행한다.
COMMON_FREQS="$FREQS"
if [ "$HAVE_FREQ" = 0 ]; then   # freq 인자 없음(SSID-only) → 기존 목록 보존. freq(none 포함) 있으면 아래 COMMON_FREQS 그대로(빈 목록=제거).
    COMMON_FREQS=$(wifi_wpa_child_call wifi_wpa_conf_common_freqs "$EDIT_TMP") \
        || { echo "opc_wlan_apply: common frequency resolve failed" >&2; exit 4; }
fi
wifi_wpa_run_child_call wifi_wpa_conf_render_canonical "$EDIT_TMP" "$TMP" "$COMMON_FREQS" \
    || { echo "opc_wlan_apply: canonical conf render failed" >&2; exit 4; }
wifi_wpa_run_child rm -f "$EDIT_TMP"
EDIT_TMP=""

# 원본 conf 권한을 보존한다 — psk= 평문이 0644 로 월드리더블 노출되지 않도록.
# --reference 미지원 환경(busybox 등)은 0600 으로 폴백(노출 최소).
wifi_wpa_run_child chmod --reference="$CONF" "$TMP" 2>/dev/null \
    || wifi_wpa_run_child chmod 0600 "$TMP" 2>/dev/null || true
# rename 전 staging 내용을 먼저 durable하게 한다. rename 후에는 대상
# inode와 directory entry를 각각 sync해 전원 장애에서 old/new 중 하나만 남게 한다.
opc_sync_required "$TMP" \
    || { echo "opc_wlan_apply: staged conf sync failed" >&2; exit 4; }
if [ -n "$MODE$BW" ]; then
    # Snapshot/config staging has succeeded before the first driver mutation.
    if wifi_wpa_run_child mkdir -p "$WIFI_RUN_DIR" 2>/dev/null; then
        wifi_wpa_run_child touch "$WIFI_RUN_DIR/${IFACE}.reconfigure-grace" 2>/dev/null || true
    fi
    opc_radio_apply_live \
        || { echo "opc_wlan_apply: driver radio apply failed" >&2; exit 4; }
fi
# 이 플래그를 rename보다 먼저 세워, 두 명령 사이 signal도 원본으로 복구한다.
ROLLBACK_REQUIRED=1
wifi_wpa_run_child mv -f "$TMP" "$CONF" || { echo "opc_wlan_apply: conf install failed" >&2; exit 4; }
TMP=""
opc_sync_required "$CONF" \
    || { echo "opc_wlan_apply: installed conf sync failed" >&2; exit 4; }
opc_sync_required "$CONF_DIR" \
    || { echo "opc_wlan_apply: installed directory sync failed" >&2; exit 4; }

# --- 적용 트리거: 전체 conf 재로드 → 공통 freq_list/ssid 모두 반영 ----------------
# reconfigure 실패 시 깨진 conf 가 영속되어 다음 reboot 기동을 막을 수 있으므로,
# 원본 백업으로 롤백한 뒤 재적용한다(무선 전체 다운 방지).
# 데몬 reply가 OK여도 wrapper/transport rc가 nonzero면 실패다. reply와 rc를 따로
# 보존해 둘 다 성공인 경우에만 commit한다(wifi.sh의 wpa_cli_ok와 동일 계약).
# reconfigure 는 재연결(끊김)을 유발 — wifi_checker 가 과도기를 '불안정'으로 오판해
# reassociate/restart 하지 않도록 grace flag 를 세운다(TTL 은 checker 의 RECONFIGURE_GRACE_SEC).
if wifi_wpa_run_child mkdir -p "$WIFI_RUN_DIR" 2>/dev/null; then
    wifi_wpa_run_child touch "$WIFI_RUN_DIR/${IFACE}.reconfigure-grace" 2>/dev/null || true
fi
RECONFIGURE_REPLY=$(wifi_wpa_child_exec wpa_cli -i "$IFACE" reconfigure 2>/dev/null)
RECONFIGURE_RC=$?
if [ "$RECONFIGURE_RC" -ne 0 ] || [ "$RECONFIGURE_REPLY" != "OK" ]; then
    # EXIT transaction trap이 rollback rename/result/sync를 일원화한다.
    exit 5
fi
if [ "$MODE_CHANGED" = 1 ]; then
    opc_radio_wpa_ok reconnect || exit 5
fi
if [ -n "$MODE$BW" ]; then
    RADIO_ROLLBACK_REQUIRED=1
    wifi_wpa_run_child mv -f "$RADIO_TMP" "$RADIO_JSON" || exit 4
    RADIO_TMP=""
    opc_sync_required "$RADIO_JSON" && opc_sync_required "$RADIO_DIR" || exit 4
fi
# All requested settings are accepted and durable. AP association is asynchronous.
TRANSACTION_COMMITTED=1
wifi_wpa_run_child rm -f "$BAK"
BAK=""
[ -z "$RADIO_BAK" ] || wifi_wpa_run_child rm -f "$RADIO_BAK"
RADIO_BAK=""
trap - EXIT HUP INT TERM

exit 0
