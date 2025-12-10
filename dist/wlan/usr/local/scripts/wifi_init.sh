#!/bin/bash

tag=$(basename "$0")
KERNEL_VERSION=$(uname -r)
JSON_FILE="/usr/local/etc/config.json"
FW_NAME=cts/pcieuart9098_combo_v1.bin
MOD_PARA=cts/wifi_mod_para.conf
CAL_DATA_CFG=cts/WlanCalData_ext_RD.conf
TXPWRLIMIT_PATH=/lib/firmware/cts/txpwrlimit_cfg_9098.conf
MFG_MODE=0

#LOGFILE="/var/log/cantops/module.log"
#sleep 0.5

logger -p local0.info "[$tag:$LINENO] Booting"
#/usr/local/scripts/get_pmic_state.sh &
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

#python3 /usr/local/logger/mac_set.py
#python3 /usr/local/logger/mac_get.py
#python3 /usr/local/logger/mac_config.py mlan0 wifi_mod_para__.conf
#wifi 0 down
#wifi 1 down
cmd=$(ifconfig |grep mlan0)
if [ -n "$cmd" ]; then
    ifconfig mlan0 down
fi
cmd=$(ifconfig |grep mlan1)
if [ -n "$cmd" ]; then
    ifconfig mlan1 down
fi
cmd=$(lsmod |grep moal)
if [ -n "$cmd" ]; then
    rmmod moal
fi
cmd=$(lsmod |grep mlan)
if [ -n "$cmd" ]; then
    rmmod mlan
fi

python3 /usr/local/logger/wired_mac_ip_get.py
#python3 /usr/local/logger/wifi_mac_save.py mlan0 wifi_mod_para__.conf

#MLAN0_MAC=$(cat /opt/wlan/mac/base0)
MLAN0_MAC=$(cat /tmp/eth0_client_mac)
if [[ "$MLAN0_MAC" =~ ^([a-fA-F0-9]{2}:){5}[a-fA-F0-9]{2}$ ]]; then
    logger -p local0.info "[$tag:$LINENO] [mlan0] vaild dynamic mac : $MLAN0_MAC"
    #echo "$MLAN0_MAC" > /opt/wlan/mac/target0
    echo "$MLAN0_MAC" > /opt/wlan/mac/wired_client
else
    logger -p local0.err "[$tag:$LINENO] [mlan0] invalid dynamic mac : $MLAN0_MAC"
    MLAN0_MAC=$(cat /opt/wlan/mac/target0)
    if [[ "$MLAN0_MAC" =~ ^([a-fA-F0-9]{2}:){5}[a-fA-F0-9]{2}$ ]]; then
        logger -p local0.info "[$tag:$LINENO] [mlan0] vaild static mac : $MLAN0_MAC"
    else
        logger -p local0.err "[$tag:$LINENO] [mlan0] invaild static mac : $MLAN0_MAC"
        MLAN0_MAC=$(cat /opt/wlan/mac/base0)
        if [[ "$MLAN0_MAC" =~ ^([a-fA-F0-9]{2}:){5}[a-fA-F0-9]{2}$ ]]; then
            logger -p local0.info "[$tag:$LINENO] [mlan0] valid base mac : $MLAN0_MAC"
        else
            logger -p local0.err "[$tag:$LINENO] [mlan0] invalid base mac : $MLAN0_MAC"
        fi
    fi
fi

/usr/local/scripts/update_mac.sh mlan0 $MLAN0_MAC
#python3 /usr/local/logger/wifi_config.py mlan0 mac_addr $MLAN0_MAC

MLAN1_MAC=$(cat /opt/wlan/mac/target1)
if [[ "$MLAN1_MAC" =~ ^([a-fA-F0-9]{2}:){5}[a-fA-F0-9]{2}$ ]]; then
    logger -p local0.info "[$tag:$LINENO] [mlan1] vaild static mac : $MLAN1_MAC"
else
    logger -p local0.err "[$tag:$LINENO] [mlan1] invaild static mac : $MLAN1_MAC"
    MLAN1_MAC=$(cat /opt/wlan/mac/base1)
    if [[ "$MLAN1_MAC" =~ ^([a-fA-F0-9]{2}:){5}[a-fA-F0-9]{2}$ ]]; then
        logger -p local0.info "[$tag:$LINENO] [mlan1] valid base mac : $MLAN1_MAC"
    else
        logger -p local0.err "[$tag:$LINENO] [mlan1] invalid base mac : $MLAN1_MAC"
    fi
fi

/usr/local/scripts/update_mac.sh mlan1 $MLAN1_MAC
#python3 /usr/local/logger/wifi_config.py mlan1 mac_addr $MLAN1_MAC

if ! try_insmod "/opt/wlan/driver/mlan.ko" ""; then
    echo "mlan module load failed"
    exit 1
fi

logger -p local0.info "[$tag:$LINENO] mod_para=$MOD_PARA fw_name=$FW_NAME mfg_mode=$MFG_MODE cal_data_cfg=$CAL_DATA_CFG"

if ! try_insmod "/opt/wlan/driver/moal.ko" "mod_para=$MOD_PARA fw_name=$FW_NAME mfg_mode=$MFG_MODE cal_data_cfg=$CAL_DATA_CFG"; then
    echo "moal module load failed"
    exit 1
fi

if [ "$MFG_MODE" == "1" ]; then
    exit 1
fi

sleep 0.5
logger -p local0.info "[$tag:$LINENO] [mlan0] TXPWRLIMIT_PATH : $TXPWRLIMIT_PATH"
mlanutl mlan0 hostcmd $TXPWRLIMIT_PATH txpwrlimit_2g_cfg_set > /dev/null 2>&1
mlanutl mlan0 hostcmd $TXPWRLIMIT_PATH txpwrlimit_5g_cfg_set_sub0 > /dev/null 2>&1
mlanutl mlan0 hostcmd $TXPWRLIMIT_PATH txpwrlimit_5g_cfg_set_sub1 > /dev/null 2>&1
mlanutl mlan0 hostcmd $TXPWRLIMIT_PATH txpwrlimit_5g_cfg_set_sub2 > /dev/null 2>&1
mlanutl mlan0 hostcmd $TXPWRLIMIT_PATH txpwrlimit_5g_cfg_set_sub3 > /dev/null 2>&1

sleep 0.2
logger -p local0.info "[$tag:$LINENO] [mlan1] txpwrlimit set"
mlanutl mlan1 hostcmd $TXPWRLIMIT_PATH txpwrlimit_2g_cfg_set > /dev/null 2>&1
mlanutl mlan1 hostcmd $TXPWRLIMIT_PATH txpwrlimit_5g_cfg_set_sub0 > /dev/null 2>&1
mlanutl mlan1 hostcmd $TXPWRLIMIT_PATH txpwrlimit_5g_cfg_set_sub1 > /dev/null 2>&1
mlanutl mlan1 hostcmd $TXPWRLIMIT_PATH txpwrlimit_5g_cfg_set_sub2 > /dev/null 2>&1
mlanutl mlan1 hostcmd $TXPWRLIMIT_PATH txpwrlimit_5g_cfg_set_sub3 > /dev/null 2>&1

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

#mlanutl mlan0 auto_arp 1

#modprobe dummy
#ip link add dummy0 type dummy
#ip addr add 192.168.4.254/32 dev dummy0
#ip link set dummy0 up

#echo 1 > /proc/sys/net/ipv4/ip_forward
#echo 1 > /proc/sys/net/ipv4/conf/eth0/proxy_arp
#echo 1 > /proc/sys/net/ipv4/conf/mlan0/proxy_arp
#echo 1 > /proc/sys/net/ipv4/conf/dummy0/proxy_arp

#sleep 0.1
#logger -p local0.info "[$tag:$LINENO] [mlan0] power save off"
#iw dev mlan0 set power_save off
#logger -p local0.info "[$tag:$LINENO] [mlan1] power save off"
#iw dev mlan1 set power_save off

#sleep 0.2
#logger -p local0.info "[$tag:$LINENO] [mlan0] link up"
#ip link set mlan0 up
#ifconfig mlan0 up

#sleep 0.2
#logger -p local0.info "[$tag:$LINENO] [mlan1] link down" 
#ip link set mlan1 up
#ifconfig mlan1 up

:<<'END'
for i in {1..3}; do
    sleep 0.5
    cmd="iw mlan0 scan"
    logger -p local0.info "[$tag:$LINENO] [mlan0] cmd : $cmd"
    result=$(eval "$cmd")
    if [ -n "$result" ]; then
        logger -p local0.info "[$tag:$LINENO] [mlan0] scan : success"
        break
    else
        logger -p local0.err "[$tag:$LINENO] [mlan0] scan : no result"
    fi
done
END

FREQ=$(jq -r '.mlan0.Frequency' "$JSON_FILE")
if [ "$FREQ" = "5GHz" ]; then
    logger -p local0.info "[$tag:$LINENO] [mlan0] freq 5GHz : mlanutl mlan0 bandcfg 0x254"
    mlanutl mlan0 bandcfg 0x254
elif [ "$FREQ" = "2.4GHz" ]; then
    logger -p local0.info "[$tag:$LINENO] [mlan0] freq 2.4GHz : mlanutl mlan0 bandcfg 0x10b"
    mlanutl mlan0 bandcfg 0x10b
elif [ "$FREQ" = "auto" ]; then
    logger -p local0.info "[$tag:$LINENO] [mlan0] freq Auto : mlanutl mlan0 bandcfg 0x35f"
    mlanutl mlan0 bandcfg 0x35f
fi

FREQ=$(jq -r '.mlan1.Frequency' "$JSON_FILE")
if [ "$FREQ" = "5GHz" ]; then
    logger -p local0.info "[$tag:$LINENO] [mlan1] freq 5GHz : mlanutl mlan1 bandcfg 0x54"
    mlanutl mlan1 bandcfg 0x54
elif [ "$FREQ" = "2.4GHz" ]; then
    logger -p local0.info "[$tag:$LINENO] [mlan1] freq 2.4GHz : mlanutl mlan1 bandcfg 0x0b"
    mlanutl mlan1 bandcfg 0x0b
elif [ "$FREQ" = "auto" ]; then
    logger -p local0.info "[$tag:$LINENO] [mlan1] freq Auto : mlanutl mlan1 bandcfg 0x5f"
    mlanutl mlan1 bandcfg 0x5f
fi

#ip link add nlmon0 type nlmon
#ip link set nlmon0 up

sleep 0.5
#python3 /usr/local/logger/getmac.py
#logger -p local0.info "[$tag:$LINENO] [mlan0] wpa_supplicant start"
#systemctl restart wifi_capture@mlan0
sleep 0.5
systemctl restart systemd-networkd
#sleep 0.5
wifi mlan0 restart

#sleep 0.5
#logger -p local0.info "[$tag:$LINENO] [mlan0] start wifi_bridge@mlan0"
#systemctl restart wifi_bridge@mlan0

#echo 1 > /proc/sys/kernel/printk
