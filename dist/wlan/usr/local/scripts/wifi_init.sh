#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
# shellcheck source=./wifi_init_config_lib.sh
. "$SCRIPT_DIR/wifi_init_config_lib.sh"

tag=$(basename "$0")
JSON_FILE="${JSON_FILE:-/usr/local/etc/config.json}"
FW_NAME="cts/pcieuart9098_combo_v1.bin"
MOD_PARA="cts/wifi_mod_para.conf"
CAL_DATA_CFG="cts/WlanCalData_ext_RD.conf"
TXPWRLIMIT_PATH="/lib/firmware/cts/txpwrlimit_cfg_9098.conf"
MFG_MODE=0
DEV_CAP_MASK=""

WIFI_INIT_CONF_JSON="${WIFI_INIT_CONF_JSON:-/usr/local/etc/wifi_init_conf.json}"

logger -p local0.info "[$tag:$LINENO] wifi initializing"

# Load JSON config
if [ -f "$WIFI_INIT_CONF_JSON" ] && command -v jq >/dev/null 2>&1; then
    FW_NAME=$(jq -r '.global.FW_NAME // "cts/pcieuart9098_combo_v1.bin"' "$WIFI_INIT_CONF_JSON")
    MOD_PARA=$(jq -r '.global.MOD_PARA // "cts/wifi_mod_para.conf"' "$WIFI_INIT_CONF_JSON")
    CAL_DATA_CFG=$(jq -r '.global.CAL_DATA_CFG // "cts/WlanCalData_ext_RD.conf"' "$WIFI_INIT_CONF_JSON")
    TXPWRLIMIT_PATH=$(jq -r '.global.TXPWRLIMIT_PATH // "/lib/firmware/cts/txpwrlimit_cfg_9098.conf"' "$WIFI_INIT_CONF_JSON")
    MFG_MODE=$(jq -r '.global.MFG_MODE // "0"' "$WIFI_INIT_CONF_JSON")
    STANDARD=$(jq -r '.global.STANDARD // ""' "$WIFI_INIT_CONF_JSON")
    DEV_CAP_MASK=$(jq -r '.global.DEV_CAP_MASK // ""' "$WIFI_INIT_CONF_JSON")
    PRIMARY_IFACE=$(jq -r '.global.PRIMARY_IFACE // "mlan0"' "$WIFI_INIT_CONF_JSON")
    MAC_MODE=$(jq -r '.global.MAC_MODE // "default"' "$WIFI_INIT_CONF_JSON")
fi

MLAN0_ENABLED=$(wifi_init_get_iface_enabled "mlan0" "true")
MLAN1_ENABLED=$(wifi_init_get_iface_enabled "mlan1" "true")
MLAN0_FREQ=$(wifi_init_get_iface_frequency "mlan0" "auto")
MLAN1_FREQ=$(wifi_init_get_iface_frequency "mlan1" "auto")

PRIMARY_IFACE="${PRIMARY_IFACE:-mlan0}"
if [ "$PRIMARY_IFACE" != "mlan0" ] && [ "$PRIMARY_IFACE" != "mlan1" ]; then
    logger -p local0.err "[$tag:$LINENO] PRIMARY_IFACE invalid: $PRIMARY_IFACE, fallback to mlan0"
    PRIMARY_IFACE="mlan0"
fi

MAC_MODE="${MAC_MODE:-default}"
if [ "$MAC_MODE" != "default" ] && [ "$MAC_MODE" != "dynamic" ] && [ "$MAC_MODE" != "static" ]; then
    logger -p local0.err "[$tag:$LINENO] MAC_MODE invalid: $MAC_MODE, fallback to default"
    MAC_MODE="default"
fi
logger -p local0.info "[$tag:$LINENO] PRIMARY_IFACE=$PRIMARY_IFACE MAC_MODE=$MAC_MODE"
logger -p local0.info "[$tag:$LINENO] mlan0 enabled=$MLAN0_ENABLED freq=$MLAN0_FREQ"
logger -p local0.info "[$tag:$LINENO] mlan1 enabled=$MLAN1_ENABLED freq=$MLAN1_FREQ"

if [ "${CAL_DATA_CFG:-}" = "none" ]; then
    CAL_DATA_CFG=""
fi
if [ "${TXPWRLIMIT_PATH:-}" = "none" ]; then
    TXPWRLIMIT_PATH=""
fi

STANDARD="${STANDARD:-}"
if [ -n "$STANDARD" ]; then
    standard_lc=$(printf '%s' "$STANDARD" | tr '[:upper:]' '[:lower:]')
    if [ "$standard_lc" = "4" ]; then
        standard_lc="n"
    elif [ "$standard_lc" = "5" ]; then
        standard_lc="ac"
    elif [ "$standard_lc" = "6" ]; then
        standard_lc="ax"
    fi

    #if [ "$standard_lc" = "ax" ]; then
    #    logger -p local0.warn "[$tag:$LINENO] STANDARD=ax requested but mlan1 does not support ax; using ac"
    #    standard_lc="ac"
    #fi

    if [ "$standard_lc" = "n" ]; then
        DEV_CAP_MASK="0xfffcdfff"
    elif [ "$standard_lc" = "ac" ]; then
        DEV_CAP_MASK="0xfffcffff"
    elif [ "$standard_lc" = "ax" ]; then
        DEV_CAP_MASK="0xffffffff"
    else
        logger -p local0.err "[$tag:$LINENO] STANDARD invalid: $STANDARD"
    fi
fi

# Backup files with error logging
logger -p local0.info "[$tag:$LINENO] Starting backup..."
/usr/local/scripts/backup_file.sh /lib/firmware/$MOD_PARA PCIE9098_0 || logger -p local0.err "[$tag:$LINENO] backup failed: $MOD_PARA"
/usr/local/scripts/backup_file.sh $TXPWRLIMIT_PATH txpwrlimit_2g_cfg_set || logger -p local0.err "[$tag:$LINENO] backup failed: TXPWRLIMIT"
/usr/local/scripts/backup_file.sh /etc/systemd/network/20-mlan0.network mlan0 || logger -p local0.err "[$tag:$LINENO] backup failed: 20-mlan0.network"
/usr/local/scripts/backup_file.sh /etc/systemd/network/21-mlan1.network mlan1 || logger -p local0.err "[$tag:$LINENO] backup failed: 21-mlan1.network"
/usr/local/scripts/backup_file.sh /etc/systemd/network/22-eth0.network eth0 || logger -p local0.err "[$tag:$LINENO] backup failed: 22-eth0.network"
/usr/local/scripts/backup_file.sh /etc/wpa_supplicant/wpa_supplicant-mlan0.conf network= || logger -p local0.err "[$tag:$LINENO] backup failed: wpa_supplicant-mlan0"
/usr/local/scripts/backup_file.sh /etc/wpa_supplicant/wpa_supplicant-mlan1.conf network= || logger -p local0.err "[$tag:$LINENO] backup failed: wpa_supplicant-mlan1"

# Apply net_rx from wifi_init_conf.json to wifi_mod_para.conf
# PCIE9098_0 ← mlan0.net_rx, PCIE9098_1 ← mlan1.net_rx
apply_net_rx_to_mod_para() {
    local conf="/lib/firmware/$MOD_PARA"
    [ -f "$conf" ] || return 0

    local mlan0_net_rx mlan1_net_rx
    mlan0_net_rx=$(jq -r '.mlan0.net_rx // 0' "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    mlan1_net_rx=$(jq -r '.mlan1.net_rx // 0' "$WIFI_INIT_CONF_JSON" 2>/dev/null)

    _set_net_rx_in_block() {
        local block="$1" value="$2"
        # Remove existing net_rx line in block, then add if value > 0
        sed -i "/^[[:space:]]*${block}[[:space:]]*=/{
            :loop
            n
            /^[[:space:]]*net_rx=/d
            /^[[:space:]]*}/!b loop
        }" "$conf"

        if [[ "$value" =~ ^[1-9][0-9]*$ ]]; then
            sed -i "/^[[:space:]]*${block}[[:space:]]*=/{
                :loop
                n
                /^[[:space:]]*}/{
                    i\\	net_rx=${value}
                    b done
                }
                b loop
                :done
            }" "$conf"
            logger -p local0.info "[$tag:$LINENO] ${block}: net_rx=${value}"
        else
            logger -p local0.info "[$tag:$LINENO] ${block}: net_rx removed (disabled)"
        fi
    }

    _set_net_rx_in_block "PCIE9098_0" "$mlan0_net_rx"
    _set_net_rx_in_block "PCIE9098_1" "$mlan1_net_rx"
}
apply_net_rx_to_mod_para

try_insmod() {
    local module_path=$1
    local args=$2
    local output
    local ret

    output=$(insmod "$module_path" $args 2>&1)
    ret=$?

    if [ $ret -eq 0 ]; then
        logger -p local0.info "[$tag:$LINENO] insmod $(basename $module_path) success"
    else
        logger -p local0.emerg "[$tag:$LINENO] insmod $(basename $module_path) fail"
        logger -p local0.emerg "[$tag:$LINENO] $output"
    fi

    return $ret
}

MAC_REGEX='^([a-fA-F0-9]{2}:){5}[a-fA-F0-9]{2}$'

try_read_mac() {
    local label=$1
    local file=$2
    local iface=$3

    if [ ! -f "$file" ]; then
        return 1
    fi

    local val
    val=$(cat "$file")
    if [[ "$val" =~ $MAC_REGEX ]]; then
        logger -p local0.info "[$tag:$LINENO] [$iface] $label mac: $val"
        echo "$val"
        return 0
    fi

    logger -p local0.warn "[$tag:$LINENO] [$iface] invalid $label mac: $val"
    return 1
}

read_mac_from_json() {
    local label=$1
    local iface=$2
    local key=$3

    [ -f "$WIFI_INIT_CONF_JSON" ] && command -v jq >/dev/null 2>&1 || return 1

    local val
    val=$(jq -r ".mac.${iface}.${key} // empty" "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    [ -z "$val" ] && return 1

    if [[ "$val" =~ $MAC_REGEX ]]; then
        logger -p local0.info "[$tag:$LINENO] [$iface] $label mac (json): $val"
        echo "$val"
        return 0
    fi

    logger -p local0.warn "[$tag:$LINENO] [$iface] invalid $label mac in json: $val"
    return 1
}

try_dynamic_mac() {
    local iface=$1
    local mac

    mac=$(try_read_mac "dynamic" /tmp/eth0_client_mac "$iface") || return 1
    echo "$mac"
}

resolve_mac() {
    local iface=$1
    local mode=$2
    local mac=""
    local source="none"

    logger -p local0.info "[$tag:$LINENO] [$iface] MAC_MODE=$mode"

    if [ -z "$mac" ] && [ "$mode" = "dynamic" ]; then
        mac=$(try_dynamic_mac "$iface") || mac=""
        [ -n "$mac" ] && source="dynamic"
    fi

    if [ -z "$mac" ] && [ "$mode" = "static" ]; then
        mac=$(read_mac_from_json "target" "$iface" "target") || mac=""
        [ -n "$mac" ] && source="target"
    fi

    if [ -z "$mac" ]; then
        mac=$(read_mac_from_json "base" "$iface" "base") || mac=""
        [ -n "$mac" ] && source="base"
    fi

    logger -p local0.info "[$tag:$LINENO] [$iface] mac_source=$source"
    echo "$mac $source"
}

iface_enabled_value() {
    local iface="$1"

    case "$iface" in
        mlan0)
            printf '%s\n' "$MLAN0_ENABLED"
            ;;
        mlan1)
            printf '%s\n' "$MLAN1_ENABLED"
            ;;
        *)
            printf 'true\n'
            ;;
    esac
}

apply_iface_txpwrlimit() {
    local iface="$1"
    local enabled="$2"

    if [ "$enabled" != "true" ]; then
        logger -p local0.info "[$tag:$LINENO] [$iface] disabled; skip txpwrlimit"
        return 0
    fi

    if [ -z "${TXPWRLIMIT_PATH:-}" ]; then
        logger -p local0.warn "[$tag:$LINENO] [$iface] TXPWRLIMIT_PATH empty; skip txpwrlimit hostcmd"
        return 0
    fi

    logger -p local0.info "[$tag:$LINENO] [$iface] TXPWRLIMIT_PATH : $TXPWRLIMIT_PATH"
    mlanutl "$iface" hostcmd "$TXPWRLIMIT_PATH" txpwrlimit_2g_cfg_set > /dev/null 2>&1 || logger -p local0.err "[$tag:$LINENO] [$iface] txpwrlimit_2g_cfg_set failed"
    mlanutl "$iface" hostcmd "$TXPWRLIMIT_PATH" txpwrlimit_5g_cfg_set_sub0 > /dev/null 2>&1 || logger -p local0.err "[$tag:$LINENO] [$iface] txpwrlimit_5g_cfg_set_sub0 failed"
    mlanutl "$iface" hostcmd "$TXPWRLIMIT_PATH" txpwrlimit_5g_cfg_set_sub1 > /dev/null 2>&1 || logger -p local0.err "[$tag:$LINENO] [$iface] txpwrlimit_5g_cfg_set_sub1 failed"
    mlanutl "$iface" hostcmd "$TXPWRLIMIT_PATH" txpwrlimit_5g_cfg_set_sub2 > /dev/null 2>&1 || logger -p local0.err "[$tag:$LINENO] [$iface] txpwrlimit_5g_cfg_set_sub2 failed"
    mlanutl "$iface" hostcmd "$TXPWRLIMIT_PATH" txpwrlimit_5g_cfg_set_sub3 > /dev/null 2>&1 || logger -p local0.err "[$tag:$LINENO] [$iface] txpwrlimit_5g_cfg_set_sub3 failed"
}

apply_iface_radio_defaults() {
    local iface="$1"
    local enabled="$2"

    if [ "$enabled" != "true" ]; then
        logger -p local0.info "[$tag:$LINENO] [$iface] disabled; skip radio defaults"
        return 0
    fi

    sleep 0.2
    logger -p local0.info "[$tag:$LINENO] [$iface] macctrl 0x00010e13"
    mlanutl "$iface" macctrl 0x00010e13 > /dev/null 2>&1 || logger -p local0.err "[$tag:$LINENO] [$iface] macctrl failed"

    sleep 0.2
    logger -p local0.info "[$tag:$LINENO] [$iface] httxcfg 0x00000063"
    mlanutl "$iface" httxcfg 0x00000063 > /dev/null 2>&1 || logger -p local0.err "[$tag:$LINENO] [$iface] httxcfg failed"

    sleep 0.2
    logger -p local0.info "[$tag:$LINENO] [$iface] htcapinfo 0x05c20000"
    mlanutl "$iface" htcapinfo 0x05c20000 > /dev/null 2>&1 || logger -p local0.err "[$tag:$LINENO] [$iface] htcapinfo failed"

    sleep 0.2
    logger -p local0.info "[$tag:$LINENO] [$iface] reassoctrl enable"
    mlanutl "$iface" reassoctrl 1 > /dev/null 2>&1 || logger -p local0.err "[$tag:$LINENO] [$iface] reassoctrl failed"

    # Apply rate_adapt_cfg from global config (must be set before association)
    local ra_mode ra_low ra_high ra_interval
    ra_mode=$(jq -r '.global.rate_adapt.mode // empty' "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    if [ -n "$ra_mode" ]; then
        ra_low=$(jq -r '.global.rate_adapt.low_thresh // 255' "$WIFI_INIT_CONF_JSON" 2>/dev/null)
        ra_high=$(jq -r '.global.rate_adapt.high_thresh // 255' "$WIFI_INIT_CONF_JSON" 2>/dev/null)
        local ra_interval_ms
        ra_interval_ms=$(jq -r '.global.rate_adapt.interval_ms // 100' "$WIFI_INIT_CONF_JSON" 2>/dev/null)
        ra_interval=$((ra_interval_ms / 10))
        sleep 0.2
        logger -p local0.info "[$tag:$LINENO] [$iface] rate_adapt_cfg $ra_mode $ra_low $ra_high $ra_interval (${ra_interval_ms}ms)"
        mlanutl "$iface" rate_adapt_cfg "$ra_mode" "$ra_low" "$ra_high" "$ra_interval" > /dev/null 2>&1 || \
            logger -p local0.err "[$tag:$LINENO] [$iface] rate_adapt_cfg failed"
    fi
}

apply_iface_bandcfg() {
    local iface="$1"
    local enabled="$2"
    local freq="$3"
    local freq_lc
    local bandcfg_5g
    local bandcfg_24g
    local bandcfg_auto

    if [ "$enabled" != "true" ]; then
        logger -p local0.info "[$tag:$LINENO] [$iface] disabled; skip bandcfg"
        return 0
    fi

    freq_lc=$(printf '%s' "$freq" | tr '[:upper:]' '[:lower:]')
    case "$iface" in
        mlan0)
            bandcfg_5g="0x254"
            bandcfg_24g="0x10b"
            bandcfg_auto="0x35f"
            ;;
        mlan1)
            bandcfg_5g="0x54"
            bandcfg_24g="0x0b"
            bandcfg_auto="0x5f"
            ;;
        *)
            logger -p local0.err "[$tag:$LINENO] [$iface] unsupported iface for bandcfg"
            return 1
            ;;
    esac

    if [ "$freq_lc" = "5ghz" ]; then
        logger -p local0.info "[$tag:$LINENO] [$iface] freq 5GHz"
        mlanutl "$iface" bandcfg "$bandcfg_5g" > /dev/null 2>&1 || logger -p local0.err "[$tag:$LINENO] [$iface] bandcfg 5GHz failed"
    elif [ "$freq_lc" = "2.4ghz" ]; then
        logger -p local0.info "[$tag:$LINENO] [$iface] freq 2.4GHz"
        mlanutl "$iface" bandcfg "$bandcfg_24g" > /dev/null 2>&1 || logger -p local0.err "[$tag:$LINENO] [$iface] bandcfg 2.4GHz failed"
    elif [ "$freq_lc" = "auto" ]; then
        logger -p local0.info "[$tag:$LINENO] [$iface] freq Auto"
        mlanutl "$iface" bandcfg "$bandcfg_auto" > /dev/null 2>&1 || logger -p local0.err "[$tag:$LINENO] [$iface] bandcfg auto failed"
    else
        logger -p local0.err "[$tag:$LINENO] [$iface] freq not available : $freq"
    fi
}

# PRIMARY: MAC_MODE에 따라 resolve
# SECONDARY: base만
if [ "$PRIMARY_IFACE" = "mlan0" ]; then
    SECONDARY_IFACE="mlan1"
else
    SECONDARY_IFACE="mlan0"
fi

# 동적 MAC이 필요한 경우 wired_mac_ip_get.py 먼저 실행
if [ "$MAC_MODE" = "dynamic" ] && wifi_init_iface_is_enabled "$PRIMARY_IFACE" "true"; then
    logger -p local0.info "[$tag:$LINENO] [$PRIMARY_IFACE] running wired_mac_ip_get.py"
    python3 /usr/local/logger/wired_mac_ip_get.py || true
fi

# resolve_mac 결과: "mac source"
PRIMARY_ENABLED=$(iface_enabled_value "$PRIMARY_IFACE")
if [ "$PRIMARY_ENABLED" = "true" ]; then
    PRIMARY_RESULT=$(resolve_mac "$PRIMARY_IFACE" "$MAC_MODE")
    PRIMARY_MAC="${PRIMARY_RESULT% *}"
    PRIMARY_MAC_SOURCE="${PRIMARY_RESULT##* }"
    if [ -n "$PRIMARY_MAC" ]; then
        /usr/local/scripts/update_mac.sh "$PRIMARY_IFACE" "$PRIMARY_MAC" || logger -p local0.err "[$tag:$LINENO] update_mac.sh $PRIMARY_IFACE failed"
    else
        logger -p local0.info "[$tag:$LINENO] [$PRIMARY_IFACE] no MAC resolved; skip update_mac"
        PRIMARY_MAC_SOURCE="none"
    fi
else
    PRIMARY_MAC_SOURCE="disabled"
    logger -p local0.info "[$tag:$LINENO] [$PRIMARY_IFACE] disabled; skip primary MAC update"
fi

SECONDARY_ENABLED=$(iface_enabled_value "$SECONDARY_IFACE")
if [ "$SECONDARY_ENABLED" = "true" ]; then
    SECONDARY_MAC=$(read_mac_from_json "base" "$SECONDARY_IFACE" "base") || SECONDARY_MAC=""
    SECONDARY_MAC_SOURCE="base"
    if [ -n "$SECONDARY_MAC" ]; then
        /usr/local/scripts/update_mac.sh "$SECONDARY_IFACE" "$SECONDARY_MAC" || logger -p local0.err "[$tag:$LINENO] update_mac.sh $SECONDARY_IFACE failed"
    else
        logger -p local0.info "[$tag:$LINENO] [$SECONDARY_IFACE] no base MAC configured; skip update_mac"
        SECONDARY_MAC_SOURCE="none"
    fi
else
    SECONDARY_MAC_SOURCE="disabled"
    logger -p local0.info "[$tag:$LINENO] [$SECONDARY_IFACE] disabled; skip secondary MAC update"
fi

# --- wifi_bridge 제어: base MAC 사용 시 bridge 비활성 ---
control_bridge_service() {
    local iface=$1
    local mac_source=$2

    if ! wifi_init_iface_is_enabled "$iface" "true"; then
        logger -p local0.info "[$tag:$LINENO] [$iface] disabled → stop+disable wifi_bridge@$iface"
        systemctl stop "wifi_bridge@${iface}.service" 2>/dev/null || true
        systemctl disable "wifi_bridge@${iface}.service" 2>/dev/null || true
        return 0
    fi

    if [ "$mac_source" = "base" ] || [ "$mac_source" = "none" ]; then
        logger -p local0.info "[$tag:$LINENO] [$iface] mac_source=$mac_source → stop+disable wifi_bridge@$iface"
        systemctl stop "wifi_bridge@${iface}.service" 2>/dev/null || true
        systemctl disable "wifi_bridge@${iface}.service" 2>/dev/null || true
    else
        logger -p local0.info "[$tag:$LINENO] [$iface] mac_source=$mac_source → enable+start wifi_bridge@$iface"
        systemctl enable "wifi_bridge@${iface}.service" 2>/dev/null || true
        systemctl start "wifi_bridge@${iface}.service" 2>/dev/null || true
    fi
}

# --- eth0: base ---
ETH0_MAC=$(read_mac_from_json "base" "eth0" "base") || ETH0_MAC=""
if [ -n "$ETH0_MAC" ]; then
    /usr/local/scripts/update_mac.sh eth0 "$ETH0_MAC" || logger -p local0.err "[$tag:$LINENO] update_mac.sh eth0 failed"
else
    logger -p local0.info "[$tag:$LINENO] [eth0] no base MAC configured; skip update_mac"
fi

if ! try_insmod "/opt/wlan/driver/mlan.ko" ""; then
    echo "mlan module load failed"
    exit 1
fi

if [ -n "${DEV_CAP_MASK:-}" ]; then
    logger -p local0.info "[$tag:$LINENO] mod_para=$MOD_PARA fw_name=$FW_NAME mfg_mode=$MFG_MODE cal_data_cfg=$CAL_DATA_CFG dev_cap_mask=$DEV_CAP_MASK"
else
    logger -p local0.info "[$tag:$LINENO] mod_para=$MOD_PARA fw_name=$FW_NAME mfg_mode=$MFG_MODE cal_data_cfg=$CAL_DATA_CFG"
fi

moal_args="mod_para=$MOD_PARA fw_name=$FW_NAME mfg_mode=$MFG_MODE cal_data_cfg=$CAL_DATA_CFG"
if [ -n "${DEV_CAP_MASK:-}" ]; then
    moal_args="$moal_args dev_cap_mask=$DEV_CAP_MASK"
fi

if ! try_insmod "/opt/wlan/driver/moal.ko" "$moal_args"; then
    echo "moal module load failed"
    exit 1
fi

if [ "$MFG_MODE" == "1" ]; then
    exit 1
fi

# insmod 후 wpa_supplicant가 자동 시작되었을 수 있음 → 라디오 설정 전에 중지
systemctl stop wpa_supplicant@mlan0 2>/dev/null || true
systemctl stop wpa_supplicant@mlan1 2>/dev/null || true

apply_iface_txpwrlimit "mlan0" "$MLAN0_ENABLED"
apply_iface_txpwrlimit "mlan1" "$MLAN1_ENABLED"
apply_iface_radio_defaults "mlan0" "$MLAN0_ENABLED"
apply_iface_radio_defaults "mlan1" "$MLAN1_ENABLED"
apply_iface_bandcfg "mlan0" "$MLAN0_ENABLED" "$MLAN0_FREQ"
apply_iface_bandcfg "mlan1" "$MLAN1_ENABLED" "$MLAN1_FREQ"

# .network 파일의 Address=에 서브넷이 없으면 /24 보정
for nf in /etc/systemd/network/*.network; do
    [ -f "$nf" ] || continue
    if grep -qE '^Address=[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' "$nf" 2>/dev/null; then
        sed -i 's|^\(Address=[0-9]\+\.[0-9]\+\.[0-9]\+\.[0-9]\+\)$|\1/24|' "$nf"
        logger -p local0.warn "[$tag:$LINENO] Added missing /24 subnet to Address in $nf"
    fi
done

if command -v systemctl >/dev/null 2>&1; then
    systemctl restart systemd-networkd

    # wifi_bridge 제어는 insmod + networkd restart 이후에 실행
    # (mlan0/mlan1 디바이스가 존재해야 BindsTo 조건 충족)
    control_bridge_service "$PRIMARY_IFACE" "$PRIMARY_MAC_SOURCE"
    control_bridge_service "$SECONDARY_IFACE" "$SECONDARY_MAC_SOURCE"

    # wifi_manager 서비스가 없거나 disabled이면 직접 wpa_supplicant 시작
    wifi_manager_active=false
    for svc in wifi_manager wifi-manager; do
        if systemctl is-enabled "$svc" >/dev/null 2>&1; then
            wifi_manager_active=true
            logger -p local0.info "[$tag:$LINENO] $svc is enabled; skip manual wpa_supplicant start"
            break
        fi
    done

    if [ "$wifi_manager_active" = "false" ]; then
        if [ "$PRIMARY_ENABLED" = "true" ]; then
            logger -p local0.info "[$tag:$LINENO] No wifi_manager service; starting wpa_supplicant@$PRIMARY_IFACE"
            systemctl start --no-block "wpa_supplicant@${PRIMARY_IFACE}" 2>/dev/null || \
                logger -p local0.err "[$tag:$LINENO] Failed to start wpa_supplicant@$PRIMARY_IFACE"
        fi
    fi

    # wifi_periodic_roam 서비스 제어 (JSON periodic_roam.enabled 기반)
    control_periodic_roam_service() {
        local iface=$1
        local enabled_val

        if ! wifi_init_iface_is_enabled "$iface" "true"; then
            logger -p local0.info "[$tag:$LINENO] [$iface] disabled → stop+disable wifi_periodic_roam@$iface"
            systemctl stop "wifi_periodic_roam@${iface}.service" 2>/dev/null || true
            systemctl disable "wifi_periodic_roam@${iface}.service" 2>/dev/null || true
            return 0
        fi

        enabled_val=$(jq -r ".${iface}.periodic_roam.enabled // false" "$WIFI_INIT_CONF_JSON" 2>/dev/null)
        enabled_val=$(wifi_init_normalize_bool "$enabled_val" "false")

        if [ "$enabled_val" = "true" ]; then
            logger -p local0.info "[$tag:$LINENO] [$iface] periodic_roam enabled → enable+start wifi_periodic_roam@$iface"
            systemctl enable "wifi_periodic_roam@${iface}.service" 2>/dev/null || true
            systemctl start "wifi_periodic_roam@${iface}.service" 2>/dev/null || true
        else
            logger -p local0.info "[$tag:$LINENO] [$iface] periodic_roam disabled → stop+disable wifi_periodic_roam@$iface"
            systemctl stop "wifi_periodic_roam@${iface}.service" 2>/dev/null || true
            systemctl disable "wifi_periodic_roam@${iface}.service" 2>/dev/null || true
        fi
    }

    control_periodic_roam_service "$PRIMARY_IFACE"
    control_periodic_roam_service "$SECONDARY_IFACE"

    # wifi_event 서비스 제어 (JSON on_connect.enabled 기반)
    control_wifi_event_service() {
        local iface=$1
        local enabled_val

        if ! wifi_init_iface_is_enabled "$iface" "true"; then
            logger -p local0.info "[$tag:$LINENO] [$iface] disabled → stop+disable wifi_event@$iface"
            systemctl stop "wifi_event@${iface}.service" 2>/dev/null || true
            systemctl disable "wifi_event@${iface}.service" 2>/dev/null || true
            return 0
        fi

        enabled_val=$(jq -r ".${iface}.on_connect.enabled // false" "$WIFI_INIT_CONF_JSON" 2>/dev/null)
        enabled_val=$(wifi_init_normalize_bool "$enabled_val" "false")

        if [ "$enabled_val" = "true" ]; then
            logger -p local0.info "[$tag:$LINENO] [$iface] wifi_event enabled → enable+start wifi_event@$iface"
            systemctl enable "wifi_event@${iface}.service" 2>/dev/null || true
            systemctl start "wifi_event@${iface}.service" 2>/dev/null || true
        else
            logger -p local0.info "[$tag:$LINENO] [$iface] wifi_event disabled → stop+disable wifi_event@$iface"
            systemctl stop "wifi_event@${iface}.service" 2>/dev/null || true
            systemctl disable "wifi_event@${iface}.service" 2>/dev/null || true
        fi
    }

    control_wifi_event_service "$PRIMARY_IFACE"
    control_wifi_event_service "$SECONDARY_IFACE"

    # ping_monitor 서비스 제어 (JSON global.ping_monitor.enabled 기반)
    ping_monitor_enabled=$(jq -r '.global.ping_monitor.enabled // false' "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    ping_monitor_enabled=$(wifi_init_normalize_bool "$ping_monitor_enabled" "false")

    if [ "$ping_monitor_enabled" = "true" ]; then
        logger -p local0.info "[$tag:$LINENO] ping_monitor enabled → enable+start"
        systemctl enable wifi_ping_monitor.service 2>/dev/null || true
        systemctl start wifi_ping_monitor.service 2>/dev/null || true
    else
        logger -p local0.info "[$tag:$LINENO] ping_monitor disabled → stop+disable"
        systemctl stop wifi_ping_monitor.service 2>/dev/null || true
        systemctl disable wifi_ping_monitor.service 2>/dev/null || true
    fi

    # wifi_mgmt_log.timer 제어 (net_rx > 0이면 자동 활성화)
    mlan0_net_rx=$(jq -r '.mlan0.net_rx // 0' "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    mlan1_net_rx=$(jq -r '.mlan1.net_rx // 0' "$WIFI_INIT_CONF_JSON" 2>/dev/null)

    if [ "${mlan0_net_rx:-0}" -gt 0 ] 2>/dev/null || [ "${mlan1_net_rx:-0}" -gt 0 ] 2>/dev/null; then
        logger -p local0.info "[$tag:$LINENO] net_rx active (mlan0=$mlan0_net_rx, mlan1=$mlan1_net_rx) → enable wifi_mgmt_log.timer"
        systemctl enable wifi_mgmt_log.timer 2>/dev/null || true
        systemctl start wifi_mgmt_log.timer 2>/dev/null || true
    else
        logger -p local0.info "[$tag:$LINENO] net_rx disabled → stop+disable wifi_mgmt_log.timer"
        systemctl stop wifi_mgmt_log.timer 2>/dev/null || true
        systemctl disable wifi_mgmt_log.timer 2>/dev/null || true
    fi
fi
