#!/bin/bash
set -euo pipefail

tag=$(basename "$0")
KERNEL_VERSION=$(uname -r)
JSON_FILE="/usr/local/etc/config.json"
FW_NAME="cts/pcieuart9098_combo_v1.bin"
MOD_PARA="cts/wifi_mod_para.conf"
CAL_DATA_CFG="cts/WlanCalData_ext_RD.conf"
TXPWRLIMIT_PATH="/lib/firmware/cts/txpwrlimit_cfg_9098.conf"
MFG_MODE=0
DEV_CAP_MASK=""

WIFI_INIT_CONF_JSON="/usr/local/etc/wifi_init_conf.json"

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

logger -p local0.info "[$tag:$LINENO] Booting"

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

if ip link show mlan0 &>/dev/null; then
    ip link set mlan0 down
fi
if ip link show mlan1 &>/dev/null; then
    ip link set mlan1 down
fi
cmd=$(lsmod |grep moal || true)
if [ -n "$cmd" ]; then
    rmmod moal
fi
cmd=$(lsmod |grep mlan || true)
if [ -n "$cmd" ]; then
    rmmod mlan
fi

# --- MAC 주소 결정 ---
# MAC_MODE: default  = base만 사용
#           dynamic  = 동적 → base
#           static   = target → base

MAC_REGEX='^([a-fA-F0-9]{2}:){5}[a-fA-F0-9]{2}$'

# 런타임 파일(예: /tmp/eth0_client_mac)에서 MAC 읽기
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
    else
        logger -p local0.warn "[$tag:$LINENO] [$iface] invalid $label mac: $val"
        return 1
    fi
}

# wifi_init_conf.json에서 MAC 읽기 (.mac.<iface>.<key>)
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
    else
        logger -p local0.warn "[$tag:$LINENO] [$iface] invalid $label mac in json: $val"
        return 1
    fi
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

    # 동적 MAC (dynamic)
    if [ -z "$mac" ] && [ "$mode" = "dynamic" ]; then
        mac=$(try_dynamic_mac "$iface") || mac=""
        [ -n "$mac" ] && source="dynamic"
    fi

    # target MAC (static) → JSON
    if [ -z "$mac" ] && [ "$mode" = "static" ]; then
        mac=$(read_mac_from_json "target" "$iface" "target") || mac=""
        [ -n "$mac" ] && source="target"
    fi

    # base MAC (공통 최종 fallback) → JSON
    if [ -z "$mac" ]; then
        mac=$(read_mac_from_json "base" "$iface" "base") || mac=""
        [ -n "$mac" ] && source="base"
    fi

    logger -p local0.info "[$tag:$LINENO] [$iface] mac_source=$source"

    # 결과: "mac source" 형태로 반환
    echo "$mac $source"
}

# PRIMARY: MAC_MODE에 따라 resolve
# SECONDARY: base만
if [ "$PRIMARY_IFACE" = "mlan0" ]; then
    SECONDARY_IFACE="mlan1"
else
    SECONDARY_IFACE="mlan0"
fi

# 동적 MAC이 필요한 경우 wired_mac_ip_get.py 먼저 실행
if [ "$MAC_MODE" = "dynamic" ]; then
    logger -p local0.info "[$tag:$LINENO] [$PRIMARY_IFACE] running wired_mac_ip_get.py"
    python3 /usr/local/logger/wired_mac_ip_get.py || true
fi

# resolve_mac 결과: "mac source"
PRIMARY_RESULT=$(resolve_mac "$PRIMARY_IFACE" "$MAC_MODE")
PRIMARY_MAC="${PRIMARY_RESULT% *}"
PRIMARY_MAC_SOURCE="${PRIMARY_RESULT##* }"
/usr/local/scripts/update_mac.sh "$PRIMARY_IFACE" "$PRIMARY_MAC" || logger -p local0.err "[$tag:$LINENO] update_mac.sh $PRIMARY_IFACE failed"

SECONDARY_MAC=$(read_mac_from_json "base" "$SECONDARY_IFACE" "base") || SECONDARY_MAC=""
SECONDARY_MAC_SOURCE="base"
/usr/local/scripts/update_mac.sh "$SECONDARY_IFACE" "$SECONDARY_MAC" || logger -p local0.err "[$tag:$LINENO] update_mac.sh $SECONDARY_IFACE failed"

# --- wifi_bridge 제어: base MAC 사용 시 bridge 비활성 ---
control_bridge_service() {
    local iface=$1
    local mac_source=$2

    if [ "$mac_source" = "base" ] || [ "$mac_source" = "none" ]; then
        logger -p local0.info "[$tag:$LINENO] [$iface] mac_source=$mac_source → disable wifi_bridge@$iface"
        systemctl disable "wifi_bridge@${iface}.service" 2>/dev/null || true
    else
        logger -p local0.info "[$tag:$LINENO] [$iface] mac_source=$mac_source → enable wifi_bridge@$iface"
        systemctl enable "wifi_bridge@${iface}.service" 2>/dev/null || true
    fi
}

if command -v systemctl >/dev/null 2>&1; then
    control_bridge_service "$PRIMARY_IFACE" "$PRIMARY_MAC_SOURCE"
    control_bridge_service "$SECONDARY_IFACE" "$SECONDARY_MAC_SOURCE"
fi

# --- eth0: base ---
ETH0_MAC=$(read_mac_from_json "base" "eth0" "base") || ETH0_MAC=""
/usr/local/scripts/update_mac.sh eth0 "$ETH0_MAC" || logger -p local0.err "[$tag:$LINENO] update_mac.sh eth0 failed"

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

sleep 0.5
if [ -n "${TXPWRLIMIT_PATH:-}" ]; then
    logger -p local0.info "[$tag:$LINENO] [mlan0] TXPWRLIMIT_PATH : $TXPWRLIMIT_PATH"
    mlanutl mlan0 hostcmd "$TXPWRLIMIT_PATH" txpwrlimit_2g_cfg_set > /dev/null 2>&1
    mlanutl mlan0 hostcmd "$TXPWRLIMIT_PATH" txpwrlimit_5g_cfg_set_sub0 > /dev/null 2>&1
    mlanutl mlan0 hostcmd "$TXPWRLIMIT_PATH" txpwrlimit_5g_cfg_set_sub1 > /dev/null 2>&1
    mlanutl mlan0 hostcmd "$TXPWRLIMIT_PATH" txpwrlimit_5g_cfg_set_sub2 > /dev/null 2>&1
    mlanutl mlan0 hostcmd "$TXPWRLIMIT_PATH" txpwrlimit_5g_cfg_set_sub3 > /dev/null 2>&1

    sleep 0.2
    logger -p local0.info "[$tag:$LINENO] [mlan1] txpwrlimit set"
    mlanutl mlan1 hostcmd "$TXPWRLIMIT_PATH" txpwrlimit_2g_cfg_set > /dev/null 2>&1
    mlanutl mlan1 hostcmd "$TXPWRLIMIT_PATH" txpwrlimit_5g_cfg_set_sub0 > /dev/null 2>&1
    mlanutl mlan1 hostcmd "$TXPWRLIMIT_PATH" txpwrlimit_5g_cfg_set_sub1 > /dev/null 2>&1
    mlanutl mlan1 hostcmd "$TXPWRLIMIT_PATH" txpwrlimit_5g_cfg_set_sub2 > /dev/null 2>&1
    mlanutl mlan1 hostcmd "$TXPWRLIMIT_PATH" txpwrlimit_5g_cfg_set_sub3 > /dev/null 2>&1
else
    logger -p local0.warn "[$tag:$LINENO] TXPWRLIMIT_PATH empty; skip txpwrlimit hostcmd"
fi

sleep 0.2
logger -p local0.info "[$tag:$LINENO] [mlan0] macctrl 0x00010e13"
#MAC Control: 0x00010213
mlanutl mlan0 macctrl 0x00010e13 > /dev/null 2>&1
logger -p local0.info "[$tag:$LINENO] [mlan1] macctrl 0x00010e13"
mlanutl mlan1 macctrl 0x00010e13 > /dev/null 2>&1

sleep 0.2
logger -p local0.info "[$tag:$LINENO] [mlan0] httxcfg 0x00000063"
#    BG band:  0x00000061
#     A band:  0x00000063
mlanutl mlan0 httxcfg 0x00000063 > /dev/null 2>&1
logger -p local0.info "[$tag:$LINENO] [mlan1] httxcfg 0x00000063"
mlanutl mlan1 httxcfg 0x00000063 > /dev/null 2>&1

sleep 0.2
logger -p local0.info "[$tag:$LINENO] [mlan0] htcapinfo 0x05c20000"
#    BG band:  0x04c00000
#     A band:  0x05c20000
mlanutl mlan0 htcapinfo 0x05c20000 > /dev/null 2>&1
logger -p local0.info "[$tag:$LINENO] [mlan1] htcapinfo 0x05c20000"
mlanutl mlan1 htcapinfo 0x05c20000 > /dev/null 2>&1

sleep 0.2
logger -p local0.info "[$tag:$LINENO] [mlan0] reassoctrl enable"
#Re-association is Disabled
mlanutl mlan0 reassoctrl 1 > /dev/null 2>&1
logger -p local0.info "[$tag:$LINENO] [mlan1] reassoctrl enable"
mlanutl mlan1 reassoctrl 1 > /dev/null 2>&1

if command -v jq >/dev/null 2>&1; then
    FREQ=$(jq -r '.mlan0.Frequency' "$JSON_FILE")
    freq_lc=$(printf '%s' "$FREQ" | tr '[:upper:]' '[:lower:]')
    if [ "$freq_lc" = "5ghz" ]; then
        logger -p local0.info "[$tag:$LINENO] [mlan0] freq 5GHz : mlanutl mlan0 bandcfg 0x254"
        mlanutl mlan0 bandcfg 0x254
    elif [ "$freq_lc" = "2.4ghz" ]; then
        logger -p local0.info "[$tag:$LINENO] [mlan0] freq 2.4GHz : mlanutl mlan0 bandcfg 0x10b"
        mlanutl mlan0 bandcfg 0x10b
    elif [ "$freq_lc" = "auto" ]; then
        logger -p local0.info "[$tag:$LINENO] [mlan0] freq Auto : mlanutl mlan0 bandcfg 0x35f"
        mlanutl mlan0 bandcfg 0x35f
    else
        logger -p local0.err "[$tag:$LINENO] [mlan0] freq not available : $FREQ"
    fi

    FREQ=$(jq -r '.mlan1.Frequency' "$JSON_FILE")
    freq_lc=$(printf '%s' "$FREQ" | tr '[:upper:]' '[:lower:]')
    if [ "$freq_lc" = "5ghz" ]; then
        logger -p local0.info "[$tag:$LINENO] [mlan1] freq 5GHz : mlanutl mlan1 bandcfg 0x54"
        mlanutl mlan1 bandcfg 0x54
    elif [ "$freq_lc" = "2.4ghz" ]; then
        logger -p local0.info "[$tag:$LINENO] [mlan1] freq 2.4GHz : mlanutl mlan1 bandcfg 0x0b"
        mlanutl mlan1 bandcfg 0x0b
    elif [ "$freq_lc" = "auto" ]; then
        logger -p local0.info "[$tag:$LINENO] [mlan1] freq Auto : mlanutl mlan1 bandcfg 0x5f"
        mlanutl mlan1 bandcfg 0x5f
    else
        logger -p local0.err "[$tag:$LINENO] [mlan1] freq not available : $FREQ"
    fi
else
    logger -p local0.err "[$tag:$LINENO] jq not found; skip bandcfg"
fi

if command -v systemctl >/dev/null 2>&1; then
    systemctl restart systemd-networkd
fi
