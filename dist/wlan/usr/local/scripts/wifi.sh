#!/bin/bash
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
# shellcheck source=./wifi_init_config_lib.sh
. "/usr/local/scripts/wifi_init_config_lib.sh"
# 설치 전 환경(개발/테스트)에서는 스크립트 옆의 lib로 보충.
# 타깃은 /usr/local/bin/wifi 심볼릭 링크라 SCRIPT_DIR을 1차 경로로 못 쓴다.
if ! declare -f wifi_init_mode_to_bandcfg_mask >/dev/null 2>&1; then
    # 파일 존재 시에만 source — 없으면 조용히 skip(정상), 있으면 source의
    # syntax error는 그대로 노출(2>/dev/null로 삼키지 않음).
    [ -f "$SCRIPT_DIR/wifi_init_config_lib.sh" ] && . "$SCRIPT_DIR/wifi_init_config_lib.sh"
fi

tag=$(basename "$0")
IFACE=mlan
NUM=""

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
trap 'sync 2>/dev/null || true' EXIT

# ----- safe file update helpers -----
safe_install_0644_sync() {
    # $1: src(tmp), $2: dst(real)
    local src="$1" dst="$2"
    install -o root -g root -m 0644 "$src" "$dst"
    sync "$dst" 2>/dev/null || sync
}

safe_tmp_for() {
    # $1: target path
    mktemp "$1.tmp.XXXXXX"
}

# sed -i 대신에도 통일하고 싶으면 사용(권한 보장)
apply_sed_update() {
    local target="$1"
    shift
    local tmp
    tmp="$(safe_tmp_for "$target")"
    trap 'rm -f "$tmp"' RETURN
    sed "$@" "$target" > "$tmp"
    safe_install_0644_sync "$tmp" "$target"
    rm -f "$tmp"
    trap - RETURN
}
# ------------------------------------

WIFI_INIT_CONF_JSON="${WIFI_INIT_CONF_JSON:-/usr/local/etc/wifi_init_conf.json}"
JSON_FILE="${JSON_FILE:-/usr/local/etc/config.json}"

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

# wpa_cli는 데몬 응답이 FAIL이어도 exit 0이므로 출력 문자열로 성공 판정
wpa_cli_ok() {
    [ "$(wpa_cli -i "$1" "$2" 2>/dev/null)" = "OK" ]
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
    echo "       wifi {0|1|2|mlan0|mlan1|eth0} info : show current configuration and status"
    echo "       wifi {0|1|2|mlan0|mlan1|eth0} ip {address/netmask} : persist"
    echo "       wifi ip apply : runtime (systemctl restart systemd-networkd — 전체 networkd 관리 인터페이스 일시 중단; 실패 시 exit 1)"
    echo "       wifi {0|1|2|mlan0|mlan1|eth0} gt {address} : persist"
    echo "       wifi {0|1|2|mlan0|mlan1|eth0} mac {0|1|base|target} {mac_address} : persist"
    echo "       wifi {0|1|mlan0|mlan1} br {up|down|start|stop|restart} : runtime"
    echo "       wifi {0|1|mlan0|mlan1} br {moal|pcap|tpacket} : wbridge engine+bridge_iface persist"
    echo "       wifi {0|1|mlan0|mlan1} txpwr {0|1|2|3|no|default|low|org|custom_file_name} : persist+runtime"
    echo "       wifi {0|1|mlan0|mlan1} config {conf} {value} : persist"
    echo "       wifi {0|1|mlan0|mlan1} spoof {0|1|dynamic|static} : persist"
    echo "       wifi {0|1|mlan0|mlan1} standard {n|ac|ax|4|5|6} : persist (mlan1은 ax 불가)"
    echo "       wifi {0|1|mlan0|mlan1} cal {0|1|2|none|WlanCalData_ext.conf|*} : persist (인터페이스별)"
    echo "       wifi {0|1|mlan0|mlan1} log {cp [dir]|compress} : 로그 복사/압축(현재 디렉터리)"
    echo "       wifi {0|1|mlan0|mlan1} ssid {id} : persist"
    echo "       wifi {0|1|mlan0|mlan1} psk {password} : persist"
    echo "       wifi {0|1|mlan0|mlan1} key {0|1|NONE|WPA-PSK|*} : persist"
    echo "       wifi {0|1|mlan0|mlan1} freq {freq_list|channel_list} : persist"
    echo "       wifi {0|1|mlan0|mlan1} connect [ssid] [freq_list|channel_list] : ssid+scan_freq+freq_list 변경 후 reconfigure 적용(재연결); 인자 없으면 현재 설정으로 재연결(reassociate)"
    echo "       wifi {0|1|mlan0|mlan1} scan {freq_list|channel_list|2G|5G} : runtime"
    echo "       wifi {0|1|mlan0|mlan1} mscan {get|channel_list|2G|5G} : runtime (setuserscan/getscantable)"
    echo "       wifi {0|1|mlan0|mlan1} roam [0|1..N] : 0=auto best, N=Nth AP (RSSI order)"
    echo "       wifi {0|1|mlan0|mlan1} stat reset [mac] : reset stat records (all or specific MAC)"
    echo "       wifi {0|1|mlan0|mlan1} stat interval {seconds} : set stat reset interval (persist)"
    echo "       wifi {0|1|mlan0|mlan1} mon [c|compact] [interval] [--summary-lines N] [--roam-display N]"
    echo "       wifi {0|1|mlan0|mlan1} mcs [on|off|reset|ht <7|15> vht <7|8|9> he <7|9|11>] : persist+runtime"
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
    echo "       wifi log all : /var/log/cantops 전체 압축(현재 디렉터리)"
    echo "       wifi backup : persist"
    exit 1
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
                roam_diff=$(echo "$iface_json" | jq -r '.roaming.DIFF_TH // 10')
                roam_check=$(echo "$iface_json" | jq -r '.roaming.CHECK_INTERVAL // 5')
                pred_en=$(echo "$iface_json" | jq -r 'if .roaming.PREDICTIVE_ROAM.enable == null then true else .roaming.PREDICTIVE_ROAM.enable end')
                load_en=$(echo "$iface_json" | jq -r '.roaming.LOAD_BASED_ROAM.enable // false')
                pingpong_en=$(echo "$iface_json" | jq -r 'if .roaming.PING_PONG_PREVENTION.enable == null then true else .roaming.PING_PONG_PREVENTION.enable end')
                adaptive_en=$(echo "$iface_json" | jq -r 'if .roaming.ADAPTIVE_INTERVAL.enable == null then true else .roaming.ADAPTIVE_INTERVAL.enable end')

                if [ "$only_iface" = "all" ]; then
                    echo "  [${iface}]"
                    echo "    enabled=$enabled  Frequency=$freq  net_rx=$net_rx"
                    echo "    bgscan_interval=${bgscan_interval}s"
                    echo "    roaming: TH_2G=${roam_th_2g} TH_5G=${roam_th_5g} DIFF=${roam_diff} CHECK=${roam_check}s"
                    echo "    features: predictive=$pred_en load_based=$load_en pingpong=$pingpong_en adaptive=$adaptive_en"
                else
                    echo "  enabled=$enabled  Frequency=$freq  net_rx=$net_rx"
                    echo "  bgscan_interval=${bgscan_interval}s"
                    echo "  roaming: TH_2G=${roam_th_2g} TH_5G=${roam_th_5g} DIFF=${roam_diff} CHECK=${roam_check}s"
                    echo "  features: predictive=$pred_en load_based=$load_en pingpong=$pingpong_en adaptive=$adaptive_en"
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
        conf="/etc/wpa_supplicant/wpa_supplicant-${dev}.conf"
        if [ ! -f "$conf" ]; then
            echo "  $dev: not found ($conf)"
            continue
        fi
        country=$(wpa_field "$conf" "country")
        ssid=$(wpa_field "$conf" "ssid")
        psk=$(wpa_field "$conf" "psk")
        key_mgmt=$(wpa_field "$conf" "key_mgmt")
        freq_list=$(wpa_field "$conf" "freq_list")
        scan_freq=$(wpa_field "$conf" "scan_freq")
        if [ "$only_iface" = "all" ]; then
            local prefix="  ${dev}: "
            local pad
            pad=$(printf '%*s' ${#prefix} "")
            echo "${prefix}country=${country:-N/A} ssid=${ssid:-N/A} psk=${psk:-N/A} key_mgmt=${key_mgmt:-N/A}"
        else
            local pad="  "
            echo "  country=${country:-N/A} ssid=${ssid:-N/A} psk=${psk:-N/A} key_mgmt=${key_mgmt:-N/A}"
        fi
        if [ -n "${freq_list:-}" ]; then
            echo "${pad}freq_list=$(freqs_with_channels "${freq_list// / }")"
        fi
        if [ -n "${scan_freq:-}" ]; then
            echo "${pad}scan_freq=$(freqs_with_channels "${scan_freq// / }")"
        fi
    done
    echo ""
    fi

    echo "[Services]"
    if command -v systemctl >/dev/null 2>&1; then
        local svc_list=()

        # Non-wifi services
        svc_list+=(switchd)
        svc_list+=("journald-snapshot.timer" "fake-hwclock.timer" "log-watchdog.timer")

        # WiFi global services
        svc_list+=(wifi_init wifi_logger wifi_ping_monitor)
        svc_list+=("wifi_mgmt_log.timer" "wifi_thermal_state.timer")

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
        _cal_basename=$(basename "$CAL_DATA_CFG")
        cp "$CAL_DATA_CFG" "/lib/firmware/cts/$_cal_basename"
        CAL_DATA_CFG="cts/$_cal_basename"
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
    update_json_global "CAL_DATA_CFG" "$CAL_DATA_CFG"
    exit 1
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
        _txpwr_basename=$(basename "$2")
        cp "$2" "/lib/firmware/cts/$_txpwr_basename"
        TXPWRLIMIT_PATH="/lib/firmware/cts/$_txpwr_basename"
    else
        usage
    fi
    echo "Updated:"
    echo "  TXPWRLIMIT_PATH=$TXPWRLIMIT_PATH"
    update_json_global "TXPWRLIMIT_PATH" "$TXPWRLIMIT_PATH"
    # 새 정책의 .bak을 즉시 동기화하여 다음 부팅의 self-healing 사각지대 제거
    if [ -n "$TXPWRLIMIT_PATH" ] && [ -s "$TXPWRLIMIT_PATH" ]; then
        cp "$TXPWRLIMIT_PATH" "${TXPWRLIMIT_PATH}.bak" 2>/dev/null \
            && sync "${TXPWRLIMIT_PATH}.bak" 2>/dev/null || sync
    fi
    exit 1
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
    exit 1
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

    update_json_global "STANDARD" "$VAL"
    echo "STANDARD updated to $VAL in $WIFI_INIT_CONF_JSON"
    exit 1
    ;;
  ip)
    # wifi N ip {addr}로 persist한 .network 설정을 실제 반영
    if [ "$2" = "apply" ]; then
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
    if [ "$2" == "all" ]; then
        LOG_BASE=/var/log/cantops
        if [ ! -d "$LOG_BASE" ]; then
            echo "Error: $LOG_BASE not found" >&2
            exit 1
        fi
        if ! command -v zip >/dev/null 2>&1; then
            echo "Error: zip not installed" >&2
            exit 1
        fi
        ARCHIVE="$(pwd)/cantops_log_$(date +%Y%m%d_%H%M%S).zip"
        if ( cd "$(dirname "$LOG_BASE")" && zip -r -q "$ARCHIVE" "$(basename "$LOG_BASE")" ); then
            echo "Compressed $LOG_BASE to $ARCHIVE"
            exit 0
        else
            echo "Error: zip failed" >&2
            exit 1
        fi
    else
        usage
    fi
    ;;
  backup)
    BACKUP_DIR=/var/log/cantops/backup
    echo "backup to $BACKUP_DIR..."
    /usr/local/scripts/backup.sh $BACKUP_DIR
    exit 1
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
    #systemctl restart wpa_supplicant@$IFACE
    systemctl stop wpa_supplicant@$IFACE
    sleep 1
    systemctl start wpa_supplicant@$IFACE
    ;;
  start | up)
    echo "Starting WPA service for $IFACE..."
    systemctl start wpa_supplicant@$IFACE
    ;;
  stop | down)
    echo "Stopping WPA service for $IFACE..."
    systemctl stop wpa_supplicant@$IFACE
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
    # wifi 0 roam       → AP 리스트만 표시
    # wifi 0 roam 0     → 현재 AP 제외 최고 RSSI로 자동 로밍
    # wifi 0 roam 1~N   → RSSI 순서 N번째 AP로 로밍
    ROAM_ARG="${3:-}"
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
        if ! [[ "$INTERVAL_SEC" =~ ^[0-9]+$ ]]; then
            echo "Error: interval must be a positive integer (seconds)"
            exit 1
        fi
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
        _txpwr_basename=$(basename "$3")
        cp "$3" "/lib/firmware/cts/$_txpwr_basename"
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
    echo "config $3 value set to $4 for $IFACE"
    python3 /usr/local/logger/wifi_config.py $1 $3 $4
    ;;
  mac)
    if [ "$3" == "base" ] || [ "$3" == "0" ]; then
        echo "base mac set to $4 for $IFACE"
        /usr/local/scripts/write_mac.sh $IFACE $4
    elif [ "$3" == "target" ] || [ "$3" == "1" ]; then
        if [ "$IFACE" == "eth0" ]; then
            echo "Error: eth0 does not support target mac"
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
        _cal_basename=$(basename "$CAL_VAL")
        cp "$CAL_VAL" "/lib/firmware/cts/$_cal_basename"
        CAL_VAL="cts/$_cal_basename"
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
    CONF="/etc/wpa_supplicant/wpa_supplicant-${IFACE}.conf"
    if [ ! -f "$CONF" ]; then echo "not found: $CONF" >&2; exit 1; fi
    FREQS=()
    for arg in "$@"; do FREQS+=( "$(to_freq_mhz "$arg")" ); done
    [ ${#FREQS[@]} -eq 0 ] && { echo "configure freq not exist" >&2; exit 1; }
    FREQ_STR="${FREQS[*]}"
    TMP_FILE="$(mktemp)"
    trap 'rm -f "$TMP_FILE"; sync 2>/dev/null || true' EXIT
    # 모든 network={} 블록에 적용 (블록마다 done 플래그 리셋). 블록이 없으면 에러.
    awk -v freqs="$FREQ_STR" '
    BEGIN { in_net = 0; blocks = 0 }
    /^[[:space:]]*#/ { print; next }
    /^[[:space:]]*network[[:space:]]*=[[:space:]]*\{/ { in_net = 1; blocks++; done_scan = 0; done_list = 0; print; next }
    in_net && /^[[:space:]]*\}/ {
        if (!done_scan) { print "    scan_freq=" freqs; done_scan = 1 }
        if (!done_list) { print "    freq_list=" freqs; done_list = 1 }
        in_net = 0; print; next
    }
    in_net && /^[[:space:]]*scan_freq[[:space:]]*=/ { if (!done_scan) { print "    scan_freq=" freqs; done_scan = 1 } next }
    in_net && /^[[:space:]]*freq_list[[:space:]]*=/ { if (!done_list) { print "    freq_list=" freqs; done_list = 1 } next }
    { print }
    END { if (blocks == 0) { print "error: no network={ block in config" > "/dev/stderr"; exit 1 } }
    ' "$CONF" > "$TMP_FILE"
    safe_install_0644_sync "$TMP_FILE" "$CONF"
    echo "scan_freq / freq_list configure $FREQ_STR in $CONF"
    ;;
  ssid)
    set -euo pipefail
    shift 2
    CONF="/etc/wpa_supplicant/wpa_supplicant-${IFACE}.conf"
    if [ ! -f "$CONF" ]; then echo "not found: $CONF" >&2; exit 1; fi
    if [ $# -lt 1 ]; then echo "usage: wifi <iface> ssid <NEW_SSID>" >&2; exit 1; fi
    NEW_SSID="$1"
    TMP_FILE="$(mktemp)"
    if awk -v new_ssid="$NEW_SSID" '
        BEGIN { changed = 0 }
        /^[[:space:]]*#/ { print; next }
        /^[[:space:]]*ssid[[:space:]]*=/ { print "    ssid=\"" new_ssid "\""; changed = 1; next }
        { print }
        END { if (!changed) exit 1 }
    ' "$CONF" > "$TMP_FILE"; then
        safe_install_0644_sync "$TMP_FILE" "$CONF"
        rm -f "$TMP_FILE"
        echo "ssid changed to \"$NEW_SSID\" in $CONF"
    else echo "no ssid= line found, nothing changed in $CONF" >&2; rm -f "$TMP_FILE"; exit 1; fi
    ;;
  psk)
    set -euo pipefail
    shift 2
    CONF="/etc/wpa_supplicant/wpa_supplicant-${IFACE}.conf"
    if [ ! -f "$CONF" ]; then echo "not found: $CONF" >&2; exit 1; fi
    if [ $# -lt 1 ]; then echo "usage: wifi <iface> psk <NEW_PSK>" >&2; exit 1; fi
    NEW_PSK="$1"
    TMP_FILE="$(mktemp)"
    if awk -v new_psk="$NEW_PSK" '
        BEGIN { changed = 0 }
        /^[[:space:]]*#/ { print; next }
        /^[[:space:]]*psk[[:space:]]*=/ { print "    psk=\"" new_psk "\""; changed = 1; next }
        { print }
        END { if (!changed) exit 1 }
    ' "$CONF" > "$TMP_FILE"; then
        safe_install_0644_sync "$TMP_FILE" "$CONF"
        rm -f "$TMP_FILE"
        echo "psk changed in $CONF"
    else echo "no psk= line found, nothing changed in $CONF" >&2; rm -f "$TMP_FILE"; exit 1; fi
    ;;
  key)
    set -euo pipefail
    shift 2
    CONF="/etc/wpa_supplicant/wpa_supplicant-${IFACE}.conf"
    if [ ! -f "$CONF" ]; then echo "not found: $CONF" >&2; exit 1; fi
    if [ $# -lt 1 ]; then echo "usage: wifi <iface> key <0|1|NONE|WPA-PSK>" >&2; exit 1; fi
    NEW_KEY="$1"
    [ "$NEW_KEY" = "0" ] && NEW_KEY="NONE"
    [ "$NEW_KEY" = "1" ] && NEW_KEY="WPA-PSK"
    TMP_FILE="$(mktemp)"
    if awk -v new_key="$NEW_KEY" '
        BEGIN { changed = 0 }
        /^[[:space:]]*#/ { print; next }
        /^[[:space:]]*key_mgmt[[:space:]]*=/ { print "    key_mgmt=" new_key; changed = 1; next }
        { print }
        END { if (!changed) exit 1 }
    ' "$CONF" > "$TMP_FILE"; then
        safe_install_0644_sync "$TMP_FILE" "$CONF"
        rm -f "$TMP_FILE"
        echo "key_mgmt changed to $NEW_KEY in $CONF"
    else echo "no key_mgmt= line found, nothing changed in $CONF" >&2; rm -f "$TMP_FILE"; exit 1; fi
    ;;
  connect)
    # 인자 있음: ssid(+scan_freq/freq_list)를 conf에 기록 → wpa_cli reconfigure로 재로드.
    #            freq 인자 생략 시 ssid만 바꾸고 scan_freq/freq_list는 유지.
    # 인자 없음: conf 편집/reconfigure 없이 현재 설정으로 강제 재연결만.
    # 공통: reassociate(연결/미연결 모두 강제 재연관 → ssid 변경 반영 확실) 우선,
    #       실패 시 reconnect fallback(rollback_radio_live와 동일 규약) → assoc 대기.
    # exit: 0=ok 1=usage/env 7=wpa_cli 8=assoc-timeout
    # known limitation: reconfigure 성공 후 reassociate/reconnect 실패(7)나 assoc
    #   타임아웃(8) 시 conf는 새 ssid로 이미 persist된다(라이브는 옛 AP일 수 있음).
    #   ssid 변경은 의도된 영속이라 rollback하지 않는다 — 다음 재시도/부팅 시 적용.
    set -euo pipefail
    shift 2
    if [ "$IFACE" != "mlan0" ] && [ "$IFACE" != "mlan1" ]; then
        echo "Error: connect supports mlan0/mlan1 only" >&2; exit 1
    fi
    if ! command -v wpa_cli >/dev/null 2>&1; then
        echo "Error: wpa_cli not found" >&2; exit 1
    fi
    CONF="/etc/wpa_supplicant/wpa_supplicant-${IFACE}.conf"
    if [ $# -ge 1 ]; then
        # === conf 편집 경로: ssid(+freq) 기록 → reconfigure ===
        if [ ! -f "$CONF" ]; then echo "not found: $CONF" >&2; exit 1; fi
        NEW_SSID="$1"; shift
        # 빈 SSID는 conf에 ssid=""를 써 association 불가(silent exit 8) → 즉시 거부.
        [ -z "$NEW_SSID" ] && { echo "Error: SSID must not be empty" >&2; exit 1; }
        # SSID 개행/탭 거부 — awk 멀티라인 injection(conf에 임의 directive 주입) 차단.
        # connect는 conf 직접편집 entry point라 여기서 가드한다.
        case "$NEW_SSID" in
            *[$'\n\r\t']*) echo "Error: SSID에 개행/탭 문자 불가" >&2; exit 1 ;;
        esac
        # busybox awk가 ENVIRON 미지원이면 SSID가 ""로 silent 손상(awk exit 0 → 성공 오인)
        # → SSID 적용 전 ENVIRON 지원을 사전 검증(opc_wlan_apply.sh와 동일 규약).
        CONNECT_ENVIRON_PROBE=ok awk 'BEGIN { exit(ENVIRON["CONNECT_ENVIRON_PROBE"] == "ok" ? 0 : 1) }' </dev/null \
            || { echo "Error: awk lacks ENVIRON support — cannot apply ssid safely" >&2; exit 1; }
        # freq 인자는 freq 명령과 동일하게 채널/MHz 모두 허용(to_freq_mhz로 MHz 정규화)
        FREQS=()
        for arg in "$@"; do FREQS+=( "$(to_freq_mhz "$arg")" ); done
        SET_FREQ=0
        FREQ_STR=""
        if [ ${#FREQS[@]} -gt 0 ]; then SET_FREQ=1; FREQ_STR="${FREQS[*]}"; fi
        # 모든 network={} 블록에 ssid(+freq)를 한 awk 패스로 적용(freq/ssid 명령 동일 규약).
        # 임시파일은 set -e 중 조기 exit 시에도 정리되도록 EXIT trap 설정(freq 명령 패턴).
        TMP_FILE="$(mktemp)"
        trap 'rm -f "$TMP_FILE"; sync 2>/dev/null || true' EXIT
        # SSID는 ENVIRON으로 raw 전달(awk -v는 값의 \X를 C-escape로 해석해 손상) + esc()로
        # wpa_supplicant conf 문법(C-style)에 맞춰 \와 "를 이스케이프.
        if CONNECT_SSID="$NEW_SSID" awk -v freqs="$FREQ_STR" -v set_freq="$SET_FREQ" '
            function esc(s) { gsub(/\\/, "\\\\", s); gsub(/"/, "\\\"", s); return s }
            BEGIN { in_net = 0; blocks = 0; new_ssid = ENVIRON["CONNECT_SSID"] }
            /^[[:space:]]*#/ { print; next }
            /^[[:space:]]*network[[:space:]]*=[[:space:]]*\{/ {
                in_net = 1; blocks++; done_ssid = 0; done_scan = 0; done_list = 0; print; next
            }
            in_net && /^[[:space:]]*\}/ {
                if (!done_ssid) { print "    ssid=\"" esc(new_ssid) "\""; done_ssid = 1 }
                if (set_freq == 1) {
                    if (!done_scan) { print "    scan_freq=" freqs; done_scan = 1 }
                    if (!done_list) { print "    freq_list=" freqs; done_list = 1 }
                }
                in_net = 0; print; next
            }
            in_net && /^[[:space:]]*ssid[[:space:]]*=/ {
                if (!done_ssid) { print "    ssid=\"" esc(new_ssid) "\""; done_ssid = 1 } next
            }
            in_net && /^[[:space:]]*scan_freq[[:space:]]*=/ {
                if (set_freq == 1) { if (!done_scan) { print "    scan_freq=" freqs; done_scan = 1 } } else print
                next
            }
            in_net && /^[[:space:]]*freq_list[[:space:]]*=/ {
                if (set_freq == 1) { if (!done_list) { print "    freq_list=" freqs; done_list = 1 } } else print
                next
            }
            { print }
            END { if (blocks == 0) { print "error: no network={ block in config" > "/dev/stderr"; exit 1 } }
        ' "$CONF" > "$TMP_FILE"; then
            safe_install_0644_sync "$TMP_FILE" "$CONF"
            rm -f "$TMP_FILE"
        else
            rm -f "$TMP_FILE"
            echo "Error: failed to update $CONF (no network={ block?)" >&2
            exit 1
        fi
        if [ "$SET_FREQ" = "1" ]; then
            echo "conf updated: ssid=\"$NEW_SSID\" scan_freq/freq_list=$FREQ_STR in $CONF"
        else
            echo "conf updated: ssid=\"$NEW_SSID\" (scan_freq/freq_list 유지) in $CONF"
        fi
        if ! wpa_cli_ok "$IFACE" reconfigure; then
            echo "Error: wpa_cli reconfigure failed for $IFACE (wpa_supplicant 미동작 또는 conf 문법 오류 확인)" >&2
            exit 7
        fi
        echo "wpa_cli reconfigure OK ($IFACE)"
    else
        # === 인자 없음: conf 그대로 현재 설정으로 재연결만 ===
        echo "no ssid given — reassociating $IFACE with current conf..."
    fi
    # --- 공통: 강제 재연결(reassociate 우선, 실패 시 reconnect) → assoc 대기 ---
    if ! wpa_cli_ok "$IFACE" reassociate && ! wpa_cli_ok "$IFACE" reconnect; then
        echo "Error: wpa_cli reassociate/reconnect failed for $IFACE" >&2
        exit 7
    fi
    # 연결 완료 대기(best-effort, 최대 15s) — radio-apply와 동일한 assoc 폴링 패턴
    CONNECT_TIMEOUT=15
    WPA_STATE=""
    for ((_i = 1; _i <= CONNECT_TIMEOUT; _i++)); do
        WPA_STATE=$(wpa_cli -i "$IFACE" status 2>/dev/null | sed -n 's/^wpa_state=//p') || true
        [ "$WPA_STATE" = "COMPLETED" ] && break
        sleep 1
    done
    if [ "$WPA_STATE" = "COMPLETED" ]; then
        CUR_SSID=$(wpa_cli -i "$IFACE" status 2>/dev/null | sed -n 's/^ssid=//p') || true
        echo "associated: ssid=\"${CUR_SSID:-N/A}\" (wpa_state=COMPLETED)"
        exit 0
    else
        echo "Warning: association not completed within ${CONNECT_TIMEOUT}s (state=${WPA_STATE:-unknown})" >&2
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
        iw $IFACE scan freq $BAND_FREQS
        exit 0
    fi
    for arg in "$@"; do
        [ -n "$(band_freqs "$arg")" ] && { echo "Error: 2G/5G must be used alone (no other channel/freq args)" >&2; exit 1; }
    done
    FREQS=()
    for arg in "$@"; do FREQS+=( "$(to_freq_mhz "$arg")" ); done
    [ ${#FREQS[@]} -eq 0 ] && { echo "configure freq not exist" >&2; exit 1; }
    FREQ_STR="${FREQS[*]}"
    echo "scanning freq_list $FREQ_STR for $IFACE"
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
    mlanutl "$IFACE" setuserscan chan="$CHAN_STR"
    echo "(results: wifi $NUM mscan get)"
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
    elif [ "$3" == "off" ] || [ "$3" == "0" ]; then
        # Disable mcs_tier
        jq --arg iface "$IFACE" '.[$iface].mcs_tier.enabled = false' \
            "$WIFI_INIT_CONF_JSON" > "${WIFI_INIT_CONF_JSON}.tmp" && \
            mv "${WIFI_INIT_CONF_JSON}.tmp" "$WIFI_INIT_CONF_JSON"
        echo "mcs_tier disabled for $IFACE in $WIFI_INIT_CONF_JSON"
        echo "(apply on next boot. To restore live: mlanutl $IFACE mcstiercfg reset)"
    elif [ "$3" == "on" ] || [ "$3" == "1" ]; then
        # Enable mcs_tier with stored JSON values (re-apply ht/vht/he)
        jq --arg iface "$IFACE" '.[$iface].mcs_tier.enabled = true' \
            "$WIFI_INIT_CONF_JSON" > "${WIFI_INIT_CONF_JSON}.tmp" && \
            mv "${WIFI_INIT_CONF_JSON}.tmp" "$WIFI_INIT_CONF_JSON"
        echo "mcs_tier enabled for $IFACE in $WIFI_INIT_CONF_JSON"
        # Build live args from stored ht/vht/he
        MCS_ARGS=""
        MCS_HT=$(jq -r ".${IFACE}.mcs_tier.ht // empty" "$WIFI_INIT_CONF_JSON")
        MCS_VHT=$(jq -r ".${IFACE}.mcs_tier.vht // empty" "$WIFI_INIT_CONF_JSON")
        MCS_HE=$(jq -r ".${IFACE}.mcs_tier.he // empty" "$WIFI_INIT_CONF_JSON")
        [ -n "$MCS_HT" ] && MCS_ARGS="$MCS_ARGS ht $MCS_HT"
        [ -n "$MCS_VHT" ] && MCS_ARGS="$MCS_ARGS vht $MCS_VHT"
        [ -n "$MCS_HE" ] && MCS_ARGS="$MCS_ARGS he $MCS_HE"
        if [ -n "$MCS_ARGS" ]; then
            mlanutl "$IFACE" mcstiercfg $MCS_ARGS > /dev/null 2>&1 && \
                echo "Applied live:$MCS_ARGS (reconnect to take effect)" || \
                echo "Warning: live apply failed (will apply on next boot)"
        else
            echo "(no ht/vht/he stored — set values with: wifi $NUM mcs ht <v> vht <v> he <v>)"
        fi
    elif [ "$3" == "reset" ]; then
        # Reset: disable in JSON + restore live
        jq --arg iface "$IFACE" '.[$iface].mcs_tier.enabled = false' \
            "$WIFI_INIT_CONF_JSON" > "${WIFI_INIT_CONF_JSON}.tmp" && \
            mv "${WIFI_INIT_CONF_JSON}.tmp" "$WIFI_INIT_CONF_JSON"
        mlanutl "$IFACE" mcstiercfg reset > /dev/null 2>&1 && \
            echo "mcs_tier reset for $IFACE (JSON disabled + live restored)" || \
            echo "mcs_tier JSON disabled (mcstiercfg reset failed — reconnect may be needed)"
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
                    case "$2" in
                        7|9|11) MCS_HE="$2"; MCS_ARGS="$MCS_ARGS he $2" ;;
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
        # Update JSON: enable + set values
        JQ_EXPR=".${IFACE}.mcs_tier.enabled = true"
        [ -n "$MCS_HT" ] && JQ_EXPR="$JQ_EXPR | .${IFACE}.mcs_tier.ht = $MCS_HT"
        [ -n "$MCS_VHT" ] && JQ_EXPR="$JQ_EXPR | .${IFACE}.mcs_tier.vht = $MCS_VHT"
        [ -n "$MCS_HE" ] && JQ_EXPR="$JQ_EXPR | .${IFACE}.mcs_tier.he = $MCS_HE"
        jq "$JQ_EXPR" "$WIFI_INIT_CONF_JSON" > "${WIFI_INIT_CONF_JSON}.tmp" && \
            mv "${WIFI_INIT_CONF_JSON}.tmp" "$WIFI_INIT_CONF_JSON"
        echo "mcs_tier updated for $IFACE:$MCS_ARGS"
        echo "JSON: enabled=true$( [ -n "$MCS_HT" ] && echo " ht=$MCS_HT" )$( [ -n "$MCS_VHT" ] && echo " vht=$MCS_VHT" )$( [ -n "$MCS_HE" ] && echo " he=$MCS_HE" )"
        # Apply live
        mlanutl "$IFACE" mcstiercfg $MCS_ARGS > /dev/null 2>&1 && \
            echo "Applied live (reconnect to take effect)" || \
            echo "Warning: live apply failed (will apply on next boot)"
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
    ASSOC_TIMEOUT="${3:-15}"
    if ! [[ "$ASSOC_TIMEOUT" =~ ^[0-9]+$ ]] || [ "$ASSOC_TIMEOUT" -lt 1 ]; then
        echo "Error: timeout_s must be a positive integer" >&2
        exit 2
    fi
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
            echo "Error: mode=$R_MODE is 2.4G-only but freq_list/scan_freq in $R_CONF is 5G-only — STA can never associate." >&2
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
    for ((_i = 1; _i <= ASSOC_TIMEOUT; _i++)); do
        WPA_STATE=$(wpa_cli -i "$IFACE" status 2>/dev/null | sed -n 's/^wpa_state=//p')
        [ "$WPA_STATE" = "COMPLETED" ] && break
        sleep 1
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
    iw dev "$IFACE" link 2>/dev/null | head -8
    echo "--- datarate ---"
    mlanutl "$IFACE" getdatarate 2>/dev/null || true
    ;;
  log)
    if [ "$IFACE" != "mlan0" ] && [ "$IFACE" != "mlan1" ]; then
        echo "Error: log cp/compress supports mlan0/mlan1 only" >&2
        exit 1
    fi
    LOG_BASE=/var/log/cantops
    LOG_FILES=(
        "$LOG_BASE/cpu/cpu.log"
        "$LOG_BASE/logger.log"
        "$LOG_BASE/kerl.log"
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
      compress)
        if [ ${#EXIST[@]} -eq 0 ]; then
            echo "Error: no log files found for $IFACE" >&2
            exit 1
        fi
        if ! command -v zip >/dev/null 2>&1; then
            echo "Error: zip not installed" >&2
            exit 1
        fi
        ARCHIVE="${IFACE}_log_$(date +%Y%m%d_%H%M%S).zip"
        if zip -j -q "$ARCHIVE" "${EXIST[@]}"; then
            echo "Compressed ${#EXIST[@]} log(s) for $IFACE to $(pwd)/$ARCHIVE"
            [ ${#MISSING[@]} -gt 0 ] && echo "Skipped ${#MISSING[@]} missing: ${MISSING[*]}"
        else
            echo "Error: zip failed" >&2
            exit 1
        fi
        ;;
      *)
        usage
        ;;
    esac
    ;;
  ip)
    set -euo pipefail
    shift 2
    CONF=$(ls -ptr /etc/systemd/network/*${IFACE}*.network | grep -v '/$'| tail -1 | tr -d '\r\n')
    if [ ! -f "$CONF" ]; then echo "not found: $CONF" >&2; exit 1; fi
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
            safe_install_0644_sync "$TMP_FILE" "$CONF"
            echo "Address removed from $CONF"
        else echo "no Address= line found in $CONF" >&2; exit 1; fi
    else
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
        safe_install_0644_sync "$TMP_FILE" "$CONF"
        echo "Address set to \"$NEW_IP\" in $CONF"
    fi
    ;;
  gt)
    set -euo pipefail
    shift 2
    CONF=$(ls -ptr /etc/systemd/network/*${IFACE}*.network | grep -v '/$'| tail -1 | tr -d '\r\n')
    if [ ! -f "$CONF" ]; then echo "not found: $CONF" >&2; exit 1; fi
    if [ $# -lt 1 ]; then echo "usage: wifi <iface> gt <address>" >&2; exit 1; fi
    NEW_GT="$1"
    TMP_FILE="$(mktemp)"
    trap 'rm -f "$TMP_FILE"; sync 2>/dev/null || true' EXIT
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
    safe_install_0644_sync "$TMP_FILE" "$CONF"
    rm -f "$TMP_FILE"
    echo "Gateway set to \"$NEW_GT\" in $CONF"
    ;;
  *)
    usage
    ;;
esac
