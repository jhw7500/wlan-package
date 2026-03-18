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

# awk 결과를 안전하게 반영 (권한 0644 고정 + sync)
apply_awk_update() {
    local target="$1"
    local tmp
    tmp="$(safe_tmp_for "$target")"
    trap 'rm -f "$tmp"' RETURN
    safe_install_0644_sync "$tmp" "$target"
    rm -f "$tmp"
    trap - RETURN
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

    jq --arg v "$value" ".mac.${iface}.${key} = \$v" "$WIFI_INIT_CONF_JSON" > "${WIFI_INIT_CONF_JSON}.tmp"
    mv "${WIFI_INIT_CONF_JSON}.tmp" "$WIFI_INIT_CONF_JSON"
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

    jq --arg k "$key" --arg v "$value" '.global[$k] = $v' "$WIFI_INIT_CONF_JSON" > "${WIFI_INIT_CONF_JSON}.tmp"
    mv "${WIFI_INIT_CONF_JSON}.tmp" "$WIFI_INIT_CONF_JSON"
}

ensure_wifi_init_conf() {
    # JSON config is managed by postinst, no action needed here
    :
}

usage() {
    echo "Usage: wifi {0|1|2|mlan0|mlan1|eth0} {start|up|stop|down|restart|status} : runtime"
    echo "       wifi {0|1|2|mlan0|mlan1|eth0} br {up|down|start|stop|restart} : runtime"
    echo "       wifi {0|1|2|mlan0|mlan1|eth0} txpwr {0|1|2|3|no|default|low|org|custom_file_name} : runtime"
    echo "       wifi {0|1|2|mlan0|mlan1|eth0} config {conf} {value} : persist"
    echo "       wifi {0|1|2|mlan0|mlan1|eth0} info : show current configuration and status"
    echo "       wifi {0|1|2|mlan0|mlan1|eth0} ip {address/netmask} : persist"
    echo "       wifi {0|1|2|mlan0|mlan1|eth0} gt {address} : persist"
    echo "       wifi {0|1|2|mlan0|mlan1|eth0} mac {0|1|base|target} {mac_address} : persist"
    echo "       wifi {0|1|2|mlan0|mlan1|eth0} spoof {0|1|dynamic|static} : persist"
    #echo "       wifi {0|1|mlan0|mlan1} standard {n|ac|ax|4|5|6} : persist"
    echo "       wifi {0|1|mlan0|mlan1} ssid {id} : persist"
    echo "       wifi {0|1|mlan0|mlan1} psk {password} : persist"
    echo "       wifi {0|1|mlan0|mlan1} key {0|1|NONE|WPA-PSK|*} : persist"
    echo "       wifi {0|1|mlan0|mlan1} freq {freq_list|channel_list} : persist"
    echo "       wifi {0|1|mlan0|mlan1} scan {freq_list|channel_list} : runtime"
    echo "       wifi {0|1|mlan0|mlan1} roam [0|1..N] : 0=auto best, N=Nth AP (RSSI order)"
    echo "       wifi {0|1|mlan0|mlan1} mon [c|compact] [interval] [--summary-lines N] [--roam-display N]"
    echo "       wifi txpwr {0|1|2|3|no|default|low|org|conf_file_name} : persist"
    echo "       wifi cal {0|1|2|None|WlanCalData_ext.conf|WlanCalData_ext_RD.conf|*} : persist"
    echo "       wifi mfg {0|1|off|on} : persist"
    echo "       wifi ant {0|1|internal|external} : runtime"
    echo "       wifi set {fem|azure} : apply preset configuration profile"
    echo "       wifi stand {n|ac|ax|4|5|6} : persist"
    echo "       wifi backup : persist"
    exit 1
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
            printf "  %-6s: %-18s [%s] MAC:%s Carrier:%s GW:%s Conf:%s\n" \
                "$dev" "${cidr:-N/A}" "$state" "$mac" "$carrier" "${gw:-N/A}" "${cfg_ip:-N/A}"
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
                local enabled freq net_rx bgscan_interval
                local roam_th_2g roam_th_5g roam_diff roam_check
                local pred_en load_en pingpong_en adaptive_en

                enabled=$(jq -r ".${iface}.enabled // true" "$WIFI_INIT_CONF_JSON")
                freq=$(jq -r ".${iface}.Frequency // \"auto\"" "$WIFI_INIT_CONF_JSON")
                net_rx=$(jq -r ".${iface}.net_rx // 0" "$WIFI_INIT_CONF_JSON")
                bgscan_interval=$(jq -r ".${iface}.bgscan.interval // 60" "$WIFI_INIT_CONF_JSON")
                roam_th_2g=$(jq -r ".${iface}.roaming.DEFAULT_TH_2G // -75" "$WIFI_INIT_CONF_JSON")
                roam_th_5g=$(jq -r ".${iface}.roaming.DEFAULT_TH_5G // -75" "$WIFI_INIT_CONF_JSON")
                roam_diff=$(jq -r ".${iface}.roaming.DIFF_TH // 10" "$WIFI_INIT_CONF_JSON")
                roam_check=$(jq -r ".${iface}.roaming.CHECK_INTERVAL // 5" "$WIFI_INIT_CONF_JSON")
                pred_en=$(jq -r ".${iface}.roaming.PREDICTIVE_ROAM.enable // true" "$WIFI_INIT_CONF_JSON")
                load_en=$(jq -r ".${iface}.roaming.LOAD_BASED_ROAM.enable // false" "$WIFI_INIT_CONF_JSON")
                pingpong_en=$(jq -r ".${iface}.roaming.PING_PONG_PREVENTION.enable // true" "$WIFI_INIT_CONF_JSON")
                adaptive_en=$(jq -r ".${iface}.roaming.ADAPTIVE_INTERVAL.enable // true" "$WIFI_INIT_CONF_JSON")

                echo "  [${iface}]"
                echo "    enabled=$enabled  Frequency=$freq  net_rx=$net_rx"
                echo "    bgscan_interval=${bgscan_interval}s"
                echo "    roaming: TH_2G=${roam_th_2g} TH_5G=${roam_th_5g} DIFF=${roam_diff} CHECK=${roam_check}s"
                echo "    features: predictive=$pred_en load_based=$load_en pingpong=$pingpong_en adaptive=$adaptive_en"
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
            FW_NAME=$(jq -r '.global.FW_NAME // "cts/pcieuart9098_combo_v1.bin"' "$WIFI_INIT_CONF_JSON")
            MOD_PARA=$(jq -r '.global.MOD_PARA // "cts/wifi_mod_para.conf"' "$WIFI_INIT_CONF_JSON")
            CAL_DATA_CFG=$(jq -r '.global.CAL_DATA_CFG // "cts/WlanCalData_ext_RD.conf"' "$WIFI_INIT_CONF_JSON")
            TXPWRLIMIT_PATH=$(jq -r '.global.TXPWRLIMIT_PATH // "/lib/firmware/cts/txpwrlimit_cfg_9098.conf"' "$WIFI_INIT_CONF_JSON")
            echo "  FW_NAME       : $FW_NAME"
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
        local pad
        pad=$(printf '%*s' $((${#dev} + 4)) "")
        echo "  ${dev}: country=${country:-N/A} ssid=${ssid:-N/A} psk=${psk:-N/A} key_mgmt=${key_mgmt:-N/A}"
        if [ -n "${freq_list:-}" ]; then
            echo "${pad}freq_list=${freq_list}"
        fi
        if [ -n "${scan_freq:-}" ]; then
            echo "${pad}scan_freq=${scan_freq}"
        fi
    done
    echo ""
    fi

    echo "[Services]"
    if command -v systemctl >/dev/null 2>&1; then
        local svc_list=()
        svc_list+=(wifi_init wifi_logger switchd)

        add_iface_svcs() {
            local iface="$1"
            svc_list+=("wifi_logger@${iface}" "wifi_led@${iface}")
            svc_list+=("wifi_checker@${iface}")
            svc_list+=("wifi_bridge@${iface}" "wifi_arping@${iface}" "wifi_bgscan@${iface}" "wifi_roam@${iface}")
            if [ "$iface" = "mlan0" ] || [ "$iface" = "mlan1" ]; then
                svc_list+=("wpa_supplicant@${iface}")
            fi
        }

        if [ "$only_iface" = "all" ]; then
            add_iface_svcs eth0
            add_iface_svcs mlan0
            add_iface_svcs mlan1
        else
            add_iface_svcs "$only_iface"
        fi

        # Timers (global)
        svc_list+=("mgmt-log-flush.timer" "wbridge-thermal-state.timer" "journald-snapshot.timer" "fake-hwclock.timer")

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
    if [ "$2" == "0" ]; then
        FW_NAME="cts/pcieuart9098_combo_v1.bin"
        MFG_MODE="0"
    elif [ "$2" == "1" ]; then
        FW_NAME="cts/pcieuart9098_combo.bin"
        MFG_MODE="1"
    else
        usage
    fi
    echo "Updated:"
    echo "  FW_NAME=${FW_NAME}"
    echo "  MFG_MODE=${MFG_MODE}"
    update_json_global "FW_NAME" "$FW_NAME"
    update_json_global "MFG_MODE" "$MFG_MODE"
    exit 1
    ;;
  cal)
    CAL_DATA_CFG=$2
    if [[ "$CAL_DATA_CFG" == *.conf ]]; then
        cp $CAL_DATA_CFG /lib/firmware/cts/$CAL_DATA_CFG
        CAL_DATA_CFG="cts/$CAL_DATA_CFG"
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
        cp $2 /lib/firmware/cts/
        TXPWRLIMIT_PATH="/lib/firmware/cts/$2"
    else
        usage
    fi
    echo "Updated:"
    echo "  TXPWRLIMIT_PATH=$TXPWRLIMIT_PATH"
    update_json_global "TXPWRLIMIT_PATH" "$TXPWRLIMIT_PATH"
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
        wifi 1 down
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
    if [[ "$2" == "4" ]] || [[ "$2" == "n" ]] || [[ "$2" == "N" ]]; then
        VAL="n"
    elif [[ "$2" == "5" ]] || [[ "$2" == "ac" ]] || [[ "$2" == "AC" ]]; then
        VAL="ac"
    elif [[ "$2" == "6" ]] || [[ "$2" == "ax" ]] || [[ "$2" == "AX" ]]; then
        VAL="ax"
    else
        usage
    fi

    update_json_global "STANDARD" "$VAL"
    echo "STANDARD updated to $VAL in $WIFI_INIT_CONF_JSON"
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
    systemctl restart wpa_supplicant@$IFACE
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
        echo "not change txpwrlimit for $IFACE"
        CONF=""
    elif [ "$3" == "default" ] || [ "$3" == "1" ]; then
        echo "default txpwrlimit for $IFACE"
        CONF=/lib/firmware/cts/txpwrlimit_cfg_9098.conf
    elif [ "$3" == "low" ] || [ "$3" == "2" ]; then
        echo "low txpwrlimit for $IFACE"
        CONF=/lib/firmware/cts/txpwrlimit_cfg_9098_low.conf
    elif [ "$3" == "test" ] || [ "$3" == "3" ]; then
        echo "test txpwrlimit for $IFACE"
        CONF=/lib/firmware/cts/txpwrlimit_cfg_9098_org.conf
    elif [[ "$3" == *.conf ]]; then
        cp $3 /lib/firmware/cts/
        CONF=/lib/firmware/cts/$3
    else
        usage
    fi
    echo "txpwrlimit set to $CONF for $IFACE"
    mlanutl $IFACE hostcmd $CONF txpwrlimit_2g_cfg_set > /dev/null 2>&1
    mlanutl $IFACE hostcmd $CONF txpwrlimit_5g_cfg_set_sub0 > /dev/null 2>&1
    mlanutl $IFACE hostcmd $CONF txpwrlimit_5g_cfg_set_sub1 > /dev/null 2>&1
    mlanutl $IFACE hostcmd $CONF txpwrlimit_5g_cfg_set_sub2 > /dev/null 2>&1
    mlanutl $IFACE hostcmd $CONF txpwrlimit_5g_cfg_set_sub3 > /dev/null 2>&1
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
    echo "cal_data_cfg file set to $3 for $IFACE"
    python3 /usr/local/logger/wifi_config.py $1 cal_data_cfg cts/$3
    ;;
  mfg)
    if [ "$3" == "off" ] || [ "$3" == "0" ]; then
        echo "mfg_mode set to off for $IFACE" 
        python3 /usr/local/logger/wifi_config.py $1 mfg_mode 0
        python3 /usr/local/logger/wifi_config.py $1 fw_name cts/pcieuart9098_combo_v1.bin
    elif [ "$3" == "on" ] || [ "$3" == "1" ]; then
        echo "mfg_mode set to on for $IFACE"
        python3 /usr/local/logger/wifi_config.py $1 mfg_mode 1
        python3 /usr/local/logger/wifi_config.py $1 fw_name cts/pcieuart9098_combo.bin
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
    awk -v freqs="$FREQ_STR" '
    BEGIN { found_scan = 0; found_list = 0 }
    /^[[:space:]]*#/ { print; next }
    /^[[:space:]]*scan_freq[[:space:]]*=/ { print "    scan_freq=" freqs; found_scan = 1; next }
    /^[[:space:]]*freq_list[[:space:]]*=/ { print "freq_list=" freqs; found_list = 1; next }
    { print }
    END { if (!found_scan) print "scan_freq=" freqs; if (!found_list) print "freq_list=" freqs }
    ' "$CONF" > "$TMP_FILE"
    safe_install_0644_sync "$TMP_FILE" "$CONF"
    rm -f "$TMP_FILE"
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
    FREQS=()
    for arg in "$@"; do FREQS+=( "$(to_freq_mhz "$arg")" ); done
    [ ${#FREQS[@]} -eq 0 ] && { echo "configure freq not exist" >&2; exit 1; }
    FREQ_STR="${FREQS[*]}"
    echo "scanning freq_list $FREQ_STR for $IFACE"
    iw $IFACE scan freq $FREQ_STR
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

    update_json_global "STANDARD" "$VAL"
    echo "STANDARD updated to $VAL in $WIFI_INIT_CONF_JSON"
    ;;
  ip)
    set -euo pipefail
    shift 2
    CONF=$(ls -ptr /etc/systemd/network/*${IFACE}*.network | grep -v '/$'| tail -1 | tr -d '\r\n')
    if [ ! -f "$CONF" ]; then echo "not found: $CONF" >&2; exit 1; fi
    if [ $# -lt 1 ]; then echo "usage: wifi <iface> ip <address/netmask>" >&2; exit 1; fi
    NEW_IP="$1"
    # subnet mask가 없으면 /24 기본 적용
    if [[ "$NEW_IP" != */* ]]; then
        NEW_IP="${NEW_IP}/24"
        echo "No subnet mask specified, using default /24"
    fi
    TMP_FILE="$(mktemp)"
    if awk -v new_ip="$NEW_IP" '
        BEGIN { changed = 0 }
        /^[[:space:]]*#/ { print; next }
        /^[[:space:]]*Address[[:space:]]*=/ { print "Address=" new_ip; changed = 1; next }
        { print }
        END { if (!changed) exit 1 }
    ' "$CONF" > "$TMP_FILE"; then
        safe_install_0644_sync "$TMP_FILE" "$CONF"
        rm -f "$TMP_FILE"
        echo "Address changed to \"$NEW_IP\" in $CONF"
    else echo "no Address= line found, nothing changed in $CONF" >&2; rm -f "$TMP_FILE"; exit 1; fi
    ;;
  gt)
    set -euo pipefail
    shift 2
    CONF=$(ls -ptr /etc/systemd/network/*${IFACE}*.network | grep -v '/$'| tail -1 | tr -d '\r\n')
    if [ ! -f "$CONF" ]; then echo "not found: $CONF" >&2; exit 1; fi
    if [ $# -lt 1 ]; then echo "usage: wifi <iface> gt <address>" >&2; exit 1; fi
    NEW_GT="$1"
    TMP_FILE="$(mktemp)"
    if awk -v new_gt="$NEW_GT" '
        BEGIN { changed = 0 }
        /^[[:space:]]*#/ { print; next }
        /^[[:space:]]*Gateway[[:space:]]*=/ { print "Gateway=" new_gt; changed = 1; next }
        { print }
        END { if (!changed) exit 1 }
    ' "$CONF" > "$TMP_FILE"; then
        safe_install_0644_sync "$TMP_FILE" "$CONF"
        rm -f "$TMP_FILE"
        echo "Gateway changed to \"$NEW_GT\" in $CONF"
    else echo "no Gateway= line found, nothing changed in $CONF" >&2; rm -f "$TMP_FILE"; exit 1; fi
    ;;
  *)
    usage
    ;;
esac
