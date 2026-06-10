#!/bin/bash
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
# shellcheck source=./wifi_init_config_lib.sh
. "/usr/local/scripts/wifi_init_config_lib.sh"

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

usage() {
    echo "Usage: wifi {0|1|2|mlan0|mlan1|eth0} {start|up|stop|down|restart|status} : runtime"
    echo "       wifi {0|1|2|mlan0|mlan1|eth0} info : show current configuration and status"
    echo "       wifi {0|1|2|mlan0|mlan1|eth0} ip {address/netmask} : persist"
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
    echo "       wifi {0|1|mlan0|mlan1} scan {freq_list|channel_list|2G|5G} : runtime"
    echo "       wifi {0|1|mlan0|mlan1} mscan {get|channel_list|2G|5G} : runtime (setuserscan/getscantable)"
    echo "       wifi {0|1|mlan0|mlan1} roam [0|1..N] : 0=auto best, N=Nth AP (RSSI order)"
    echo "       wifi {0|1|mlan0|mlan1} stat reset [mac] : reset stat records (all or specific MAC)"
    echo "       wifi {0|1|mlan0|mlan1} stat interval {seconds} : set stat reset interval (persist)"
    echo "       wifi {0|1|mlan0|mlan1} mon [c|compact] [interval] [--summary-lines N] [--roam-display N]"
    echo "       wifi {0|1|mlan0|mlan1} mcs [on|off|reset|ht <7|15> vht <7|8|9> he <7|9|11>] : persist+runtime"
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

    update_json_iface "$IFACE" "STANDARD" "$VAL"
    echo "STANDARD updated to $VAL for $IFACE in $WIFI_INIT_CONF_JSON"
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
