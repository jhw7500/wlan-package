#!/bin/bash
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

WIFI_INIT_CONF="/usr/local/etc/wifi_init.conf"

ensure_wifi_init_conf() {
    mkdir -p "$(dirname "$WIFI_INIT_CONF")"

    if [ ! -f "$WIFI_INIT_CONF" ]; then
        cat > "$WIFI_INIT_CONF" <<'EOF'
FW_NAME="cts/pcieuart9098_combo_v1.bin"
MOD_PARA="cts/wifi_mod_para.conf"
CAL_DATA_CFG="cts/WlanCalData_ext_RD.conf"
TXPWRLIMIT_PATH="/lib/firmware/cts/txpwrlimit_cfg_9098.conf"
MFG_MODE="0"
STANDARD=""
DEV_CAP_MASK=""
EOF
        return 0
    fi

    if ! grep -qE '^[[:space:]]*FW_NAME=' "$WIFI_INIT_CONF"; then
        echo 'FW_NAME="cts/pcieuart9098_combo_v1.bin"' >> "$WIFI_INIT_CONF"
    fi
    if ! grep -qE '^[[:space:]]*MOD_PARA=' "$WIFI_INIT_CONF"; then
        echo 'MOD_PARA="cts/wifi_mod_para.conf"' >> "$WIFI_INIT_CONF"
    fi
    if ! grep -qE '^[[:space:]]*CAL_DATA_CFG=' "$WIFI_INIT_CONF"; then
        echo 'CAL_DATA_CFG="cts/WlanCalData_ext_RD.conf"' >> "$WIFI_INIT_CONF"
    fi
    if ! grep -qE '^[[:space:]]*TXPWRLIMIT_PATH=' "$WIFI_INIT_CONF"; then
        echo 'TXPWRLIMIT_PATH="/lib/firmware/cts/txpwrlimit_cfg_9098.conf"' >> "$WIFI_INIT_CONF"
    fi
    if ! grep -qE '^[[:space:]]*MFG_MODE=' "$WIFI_INIT_CONF"; then
        echo 'MFG_MODE="0"' >> "$WIFI_INIT_CONF"
    fi
    if ! grep -qE '^[[:space:]]*STANDARD=' "$WIFI_INIT_CONF"; then
        echo 'STANDARD=""' >> "$WIFI_INIT_CONF"
    fi
    if ! grep -qE '^[[:space:]]*DEV_CAP_MASK=' "$WIFI_INIT_CONF"; then
        echo 'DEV_CAP_MASK=""' >> "$WIFI_INIT_CONF"
    fi
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
    echo "       wifi {0|1|mlan0|mlan1} standard {n|ac|ax|4|5|6} : persist"
    echo "       wifi {0|1|mlan0|mlan1} ssid {id} : persist"
    echo "       wifi {0|1|mlan0|mlan1} psk {password} : persist"
    echo "       wifi {0|1|mlan0|mlan1} key {0|1|NONE|WPA-PSK|*} : persist"
    echo "       wifi {0|1|mlan0|mlan1} freq {freq_list|channel_list} : persist"
    echo "       wifi {0|1|mlan0|mlan1} scan {freq_list|channel_list} : runtime"
    echo "       wifi {0|1|mlan0|mlan1} mon : runtime"
    echo "       wifi txpwr {0|1|2|3|no|default|low|org|conf_file_name} : persist"
    echo "       wifi cal {0|1|2|None|WlanCalData_ext.conf|WlanCalData_ext_RD.conf|*} : persist"
    echo "       wifi mfg {0|1|off|on} : persist"
    echo "       wifi ant {0|1|internal|external} : runtime"
    echo "       wifi set {fem|azure} : apply preset configuration profile"
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
            addr=$(ip -4 addr show $dev 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}')
            mac=$(cat /sys/class/net/$dev/address 2>/dev/null)
            carrier=$(cat /sys/class/net/$dev/carrier 2>/dev/null || echo "0")
            state=$(ip link show $dev | grep -oP '(?<=state\s)\w+')
            gw=$(ip -4 route show default dev "$dev" 2>/dev/null | awk '{for (i=1;i<=NF;i++) if ($i=="via") {print $(i+1); exit}}')
            printf "  %-6s: %-15s [%s] MAC:%s Carrier:%s GW:%s\n" "$dev" "${addr:-N/A}" "$state" "$mac" "$carrier" "${gw:-N/A}"
        fi
    done
    echo ""

    if [ "$only_iface" = "eth0" ]; then
        :
    else
        echo "[Active Config Summary]"
    JSON_FILE="/usr/local/etc/config.json"
    json_get() {
        local q="$1" fallback="$2"
        if [ ! -f "$JSON_FILE" ]; then
            echo "$fallback"
            return 0
        fi
        if command -v jq >/dev/null 2>&1; then
            jq -r "$q" "$JSON_FILE" 2>/dev/null || echo "$fallback"
            return 0
        fi
        if command -v python3 >/dev/null 2>&1; then
            python3 - <<PY 2>/dev/null || echo "$fallback"
import json
try:
    with open("$JSON_FILE", "r", encoding="utf-8") as f:
        data = json.load(f)
    # Minimal jq-ish support for the queries we use below
    q = "$q".strip()
    if q == ".mlan0.Frequency":
        print(data.get("mlan0", {}).get("Frequency", "$fallback"))
    elif q == ".mlan1.Frequency":
        print(data.get("mlan1", {}).get("Frequency", "$fallback"))
    elif q == ".mlan0.enabled":
        print(str(data.get("mlan0", {}).get("enabled", "$fallback")).lower())
    elif q == ".mlan1.enabled":
        print(str(data.get("mlan1", {}).get("enabled", "$fallback")).lower())
    else:
        print("$fallback")
except Exception:
    print("$fallback")
PY
            return 0
        fi
        echo "$fallback"
    }

        if [ -f "$JSON_FILE" ]; then
            if [ "$only_iface" = "all" ] || [ "$only_iface" = "mlan0" ]; then
                mlan0_freq=$(json_get '.mlan0.Frequency' 'N/A')
                [ "$mlan0_freq" = "null" ] && mlan0_freq="N/A"
                echo "  mlan0: Frequency=${mlan0_freq}"
            fi
            if [ "$only_iface" = "all" ] || [ "$only_iface" = "mlan1" ]; then
                mlan1_freq=$(json_get '.mlan1.Frequency' 'N/A')
                [ "$mlan1_freq" = "null" ] && mlan1_freq="N/A"
                echo "  mlan1: Frequency=${mlan1_freq}"
            fi
        else
            echo "  Config file not found: $JSON_FILE"
        fi
        echo ""
    fi

    if [ "$only_iface" = "eth0" ]; then
        :
    else
        echo "[WiFi Init Summary]"
    FW_NAME="cts/pcieuart9098_combo_v1.bin"
    MOD_PARA="cts/wifi_mod_para.conf"
    CAL_DATA_CFG="cts/azure/cal_data.conf"
    TXPWRLIMIT_PATH="/lib/firmware/cts/azure/txpwrlimit_cfg_9098.conf"
    MFG_MODE=0
    STANDARD=""
    DEV_CAP_MASK="0xffffffff"
    
    WIFI_INIT_CONF="/usr/local/etc/wifi_init.conf"
    if [ -f "$WIFI_INIT_CONF" ]; then
        . "$WIFI_INIT_CONF"
        echo "  Source: $WIFI_INIT_CONF (Overridden)"
    else
        echo "  Source: Default (Internal)"
    fi
    echo "  Standard        : ${STANDARD:-}"
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
        echo "  $dev: country=${country:-N/A} ssid=${ssid:-N/A} psk=${psk:-N/A} key_mgmt=${key_mgmt:-N/A}"
        if [ -n "${freq_list:-}" ]; then
            echo "       freq_list=${freq_list}"
        fi
        if [ -n "${scan_freq:-}" ]; then
            echo "       scan_freq=${scan_freq}"
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

        for svc in "${svc_list[@]}"; do
            state=$(systemctl is-active "$svc" 2>/dev/null || true)
            [ -z "$state" ] && state="unknown"
            printf "  %-22s %s\n" "$svc:" "${state^^}"
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
    ensure_wifi_init_conf
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
    sed -i -E "/^[[:space:]]*#/! s|^CAL_DATA_CFG=.*\$|CAL_DATA_CFG=${CAL_VAL}|" "$WIFI_INIT_CONF"
    sed -i -E "/^[[:space:]]*#/! s|^TXPWRLIMIT_PATH=.*\$|TXPWRLIMIT_PATH=${PWR_VAL}|" "$WIFI_INIT_CONF"
    echo "Updated in $WIFI_INIT_CONF:"
    echo "  CAL_DATA_CFG    = $CAL_VAL"
    echo "  TXPWRLIMIT_PATH = $PWR_VAL"
    exit 0
    ;;
  mfg)
    ensure_wifi_init_conf
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
    sed -i -E "/^[[:space:]]*#/! s|^FW_NAME=.*\$|FW_NAME=${FW_NAME}|" "$WIFI_INIT_CONF"
    sed -i -E "/^[[:space:]]*#/! s|^MFG_MODE=.*\$|MFG_MODE=${MFG_MODE}|" "$WIFI_INIT_CONF"
    exit 1
    ;;
  cal)
    ensure_wifi_init_conf
    CAL_DATA_CFG=$2
    if [[ "$CAL_DATA_CFG" == *.conf ]]; then
        cp $CAL_DATA_CFG /lib/firmware/cts/$CAL_DATA_CFG
        CAL_DATA_CFG="cts/$CAL_DATA_CFG"
    elif [[ "$CAL_DATA_CFG" == "2" ]]; then
        CAL_DATA_CFG="cts/WlanCalData_ext_RD.conf"
    elif [[ "$CAL_DATA_CFG" == "1" ]]; then
        CAL_DATA_CFG="cts/WlanCalData_ext.conf"
    elif [[ "$CAL_DATA_CFG" == "0" ]]; then
        CAL_DATA_CFG=\"\"
    else
        usage
    fi
    echo "Updated:"
    echo "  CAL_DATA_CFG=$CAL_DATA_CFG"
    sed -i -E "/^[[:space:]]*#/! s|^CAL_DATA_CFG=.*\$|CAL_DATA_CFG=${CAL_DATA_CFG}|" "$WIFI_INIT_CONF"
    exit 1
    ;;
  txpwr | txpwrlimit)
    ensure_wifi_init_conf
    if [ "$2" == "no" ] || [ "$2" == "0" ]; then
        TXPWRLIMIT_PATH=\"\"
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
    sed -i -E "/^[[:space:]]*#/! s|^TXPWRLIMIT_PATH=.*\$|TXPWRLIMIT_PATH=${TXPWRLIMIT_PATH}|" "$WIFI_INIT_CONF"
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
  "" | restart)
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
  mon)
    if [ -z "$3" ]; then
        interval=1
    else
        interval=$3
    fi
    echo "monitor link for $IFACE with interval $interval sec"
    python3 /usr/local/logger/wifi_link_monitor.py $IFACE --interval $interval
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
        echo "$4" > /opt/wlan/mac/base$NUM
    elif [ "$3" == "target" ] || [ "$3" == "1" ]; then
        echo "target mac set to $4 for $IFACE"
        echo "$4" > /opt/wlan/mac/target$NUM
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
        cat /dev/null > "/opt/wlan/mac/target$NUM"
    elif [ "$3" == "static" ] || [ "$3" == "1" ]; then
        echo "spoofing mode set to static for $IFACE"
        cp /tmp/eth0_client_mac "/opt/wlan/mac/target$NUM"
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
    ensure_wifi_init_conf
    if [[ "$3" == "4" ]] || [[ "$3" == "n" ]] || [[ "$3" == "N" ]]; then
        VAL="n"
    elif [[ "$3" == "5" ]] || [[ "$3" == "ac" ]] || [[ "$3" == "AC" ]]; then
        VAL="ac"
    elif [[ "$3" == "6" ]] || [[ "$3" == "ax" ]] || [[ "$3" == "AX" ]]; then
        VAL="ax"
    else
        usage
    fi

    if grep -qE '^[[:space:]]*STANDARD=' "$WIFI_INIT_CONF"; then
        sed -i -E "/^[[:space:]]*#/! s|^STANDARD=.*\$|STANDARD=\"${VAL}\"|" "$WIFI_INIT_CONF"
    else
        echo "STANDARD=\"${VAL}\"" >> "$WIFI_INIT_CONF"
    fi

    echo "STANDARD updated to $VAL in $WIFI_INIT_CONF"
    ;;
  ip)
    set -euo pipefail
    shift 2
    CONF=$(ls -ptr /etc/systemd/network/*${IFACE}*.network | grep -v '/$'| tail -1 | tr -d '\r\n')
    if [ ! -f "$CONF" ]; then echo "not found: $CONF" >&2; exit 1; fi
    if [ $# -lt 1 ]; then echo "usage: wifi <iface> ip <address/netmask>" >&2; exit 1; fi
    NEW_IP="$1"
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
