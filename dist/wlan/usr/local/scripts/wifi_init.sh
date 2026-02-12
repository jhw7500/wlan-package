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

WIFI_INIT_CONF="/usr/local/etc/wifi_init.conf"
if [ -f "$WIFI_INIT_CONF" ]; then
    . "$WIFI_INIT_CONF"
fi

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

    if [ "$standard_lc" = "ax" ]; then
        logger -p local0.warn "[$tag:$LINENO] STANDARD=ax requested but mlan1 does not support ax; using ac"
        standard_lc="ac"
    fi

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

python3 /usr/local/logger/wired_mac_ip_get.py || true
WIRED_PY_RESULT=$?
if [ $WIRED_PY_RESULT -ne 0 ]; then
    logger -p local0.err "[$tag:$LINENO] wired_mac_ip_get.py failed with code $WIRED_PY_RESULT"
fi
logger -p local0.info "[$tag:$LINENO] wired_mac_ip_get.py completed"

logger -p local0.info "[$tag:$LINENO] Checking /tmp/eth0_client_mac..."
if [ -f /tmp/eth0_client_mac ]; then
    MLAN0_MAC=$(cat /tmp/eth0_client_mac)
    logger -p local0.info "[$tag:$LINENO] Read MAC from /tmp/eth0_client_mac: $MLAN0_MAC"
else
    logger -p local0.warn "[$tag:$LINENO] /tmp/eth0_client_mac not found, using static MAC"
    MLAN0_MAC=""
fi

if [[ "$MLAN0_MAC" =~ ^([a-fA-F0-9]{2}:){5}[a-fA-F0-9]{2}$ ]]; then
    logger -p local0.info "[$tag:$LINENO] [mlan0] vaild dynamic mac : $MLAN0_MAC"
    mkdir -p /opt/wlan/mac
    echo "$MLAN0_MAC" > /opt/wlan/mac/wired_client || logger -p local0.err "[$tag:$LINENO] Failed to write wired_client"
else
    logger -p local0.err "[$tag:$LINENO] [mlan0] invalid dynamic mac : $MLAN0_MAC"
    if [ -f /opt/wlan/mac/target0 ]; then
        MLAN0_MAC=$(cat /opt/wlan/mac/target0)
    else
        MLAN0_MAC=""
    fi
    if [[ "$MLAN0_MAC" =~ ^([a-fA-F0-9]{2}:){5}[a-fA-F0-9]{2}$ ]]; then
        logger -p local0.info "[$tag:$LINENO] [mlan0] vaild static mac : $MLAN0_MAC"
    else
        logger -p local0.err "[$tag:$LINENO] [mlan0] invaild static mac : $MLAN0_MAC"
        if [ -f /opt/wlan/mac/base0 ]; then
            MLAN0_MAC=$(cat /opt/wlan/mac/base0)
        else
            MLAN0_MAC=""
        fi
        if [[ "$MLAN0_MAC" =~ ^([a-fA-F0-9]{2}:){5}[a-fA-F0-9]{2}$ ]]; then
            logger -p local0.info "[$tag:$LINENO] [mlan0] valid base mac : $MLAN0_MAC"
        else
            logger -p local0.err "[$tag:$LINENO] [mlan0] invalid base mac : $MLAN0_MAC"
        fi
    fi
fi

/usr/local/scripts/update_mac.sh mlan0 "$MLAN0_MAC" || logger -p local0.err "[$tag:$LINENO] update_mac.sh mlan0 failed"

if [ -f /opt/wlan/mac/target1 ]; then
    MLAN1_MAC=$(cat /opt/wlan/mac/target1)
else
    MLAN1_MAC=""
fi
if [[ "$MLAN1_MAC" =~ ^([a-fA-F0-9]{2}:){5}[a-fA-F0-9]{2}$ ]]; then
    logger -p local0.info "[$tag:$LINENO] [mlan1] vaild static mac : $MLAN1_MAC"
else
    logger -p local0.err "[$tag:$LINENO] [mlan1] invaild static mac : $MLAN1_MAC"
    if [ -f /opt/wlan/mac/base1 ]; then
        MLAN1_MAC=$(cat /opt/wlan/mac/base1)
    else
        MLAN1_MAC=""
    fi
    if [[ "$MLAN1_MAC" =~ ^([a-fA-F0-9]{2}:){5}[a-fA-F0-9]{2}$ ]]; then
        logger -p local0.info "[$tag:$LINENO] [mlan1] valid base mac : $MLAN1_MAC"
    else
        logger -p local0.err "[$tag:$LINENO] [mlan1] invalid base mac : $MLAN1_MAC"
    fi
fi

/usr/local/scripts/update_mac.sh mlan1 "$MLAN1_MAC" || logger -p local0.err "[$tag:$LINENO] update_mac.sh mlan1 failed"

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
