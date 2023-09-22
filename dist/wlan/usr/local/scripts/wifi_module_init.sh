#!/bin/bash

tag=$(basename "$0")
KERNEL_VERSION=$(uname -r)
JSON_FILE="/usr/local/etc/config.json"
#LOGFILE="/var/log/cantops/module.log"
#sleep 0.5

logger -p local0.info "[$tag:$LINENO] wifi module init (Booting)"
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
wifi 0 down
wifi 1 down
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

#if ! try_insmod "/lib/modules/$KERNEL_VERSION/updates/mlan_6.12.ko" ""; then
if ! try_insmod "/opt/wlan/driver/debug/mlan.ko" ""; then
    echo "mlan module load failed"  
    #exit 1
fi

#python3 /usr/local/logger/wifi_mac_save.py mlan0 wifi_mod_para__.conf

MLAN0_MAC=$(cat /opt/wlan/mac/base0)
if [[ "$MLAN0_MAC" =~ ^([a-fA-F0-9]{2}:){5}[a-fA-F0-9]{2}$ ]]; then
    logger -p local0.info "[$tag:$LINENO] [mlan0] vaild base mac address : $MLAN0_MAC"
    python3 /usr/local/logger/wifi_config.py mlan0 mac_addr $MLAN0_MAC
else
    logger -p local0.err "[$tag:$LINENO] [mlan0] invalid mac address : $MLAN0_MAC"
fi

MLAN1_MAC=$(cat /opt/wlan/mac/base1)
if [[ "$MLAN1_MAC" =~ ^([a-fA-F0-9]{2}:){5}[a-fA-F0-9]{2}$ ]]; then
    logger -p local0.info "[$tag:$LINENO] [mlan1] vaild base mac address : $MLAN1_MAC"
    python3 /usr/local/logger/wifi_config.py mlan1 mac_addr $MLAN1_MAC
else
    logger -p local0.err "[$tag:$LINENO] [mlan1] invalid mac address : $MLAN1_MAC"
fi

#if ! try_insmod "/opt/wlan/driver/debug/moal.ko" "fw_name=nxp/pcieuart9098_combo.bin mfg_mode=1"; then
if ! try_insmod "/opt/wlan/driver/moal.ko" "mod_para=nxp/wifi_mod_para__.conf"; then
    echo "moal module load failed"
    #exit 1
fi

#try_insmod "/lib/modules/$KERNEL_VERSION/updates/moal_6.12.ko" "fw_name=nxp/pcieuart9098_combo.bin mfg_mode=1"
#try_insmod "/lib/modules/$KERNEL_VERSION/kernel/drivers/net/nlmon.ko"

sleep 0.5
logger -p local0.info "[$tag:$LINENO] [mlan0] txpwrlimit set"
mlanutl mlan0 hostcmd /lib/firmware/nxp/config/txpwrlimit_cfg_9098.conf txpwrlimit_2g_cfg_set > /dev/null 2>&1
mlanutl mlan0 hostcmd /lib/firmware/nxp/config/txpwrlimit_cfg_9098.conf txpwrlimit_5g_cfg_set_sub0 > /dev/null 2>&1
mlanutl mlan0 hostcmd /lib/firmware/nxp/config/txpwrlimit_cfg_9098.conf txpwrlimit_5g_cfg_set_sub1 > /dev/null 2>&1
mlanutl mlan0 hostcmd /lib/firmware/nxp/config/txpwrlimit_cfg_9098.conf txpwrlimit_5g_cfg_set_sub2 > /dev/null 2>&1
mlanutl mlan0 hostcmd /lib/firmware/nxp/config/txpwrlimit_cfg_9098.conf txpwrlimit_5g_cfg_set_sub3 > /dev/null 2>&1

sleep 0.2
logger -p local0.info "[$tag:$LINENO] [mlan1] txpwrlimit set"
mlanutl mlan1 hostcmd /lib/firmware/nxp/config/txpwrlimit_cfg_9098.conf txpwrlimit_2g_cfg_set > /dev/null 2>&1
mlanutl mlan1 hostcmd /lib/firmware/nxp/config/txpwrlimit_cfg_9098.conf txpwrlimit_5g_cfg_set_sub0 > /dev/null 2>&1
mlanutl mlan1 hostcmd /lib/firmware/nxp/config/txpwrlimit_cfg_9098.conf txpwrlimit_5g_cfg_set_sub1 > /dev/null 2>&1
mlanutl mlan1 hostcmd /lib/firmware/nxp/config/txpwrlimit_cfg_9098.conf txpwrlimit_5g_cfg_set_sub2 > /dev/null 2>&1
mlanutl mlan1 hostcmd /lib/firmware/nxp/config/txpwrlimit_cfg_9098.conf txpwrlimit_5g_cfg_set_sub3 > /dev/null 2>&1

sleep 0.2
logger -p local0.info "[$tag:$LINENO] [mlan0] macctrl 0x00010e13"
mlanutl mlan0 macctrl 0x00010e13 > /dev/null 2>&1
logger -p local0.info "[$tag:$LINENO] [mlan1] macctrl 0x00010e13"
mlanutl mlan1 macctrl 0x00010e13 > /dev/null 2>&1

sleep 0.2
logger -p local0.info "[$tag:$LINENO] [mlan0] httxcfg 0x00000063"
mlanutl mlan0 httxcfg 0x00000063 > /dev/null 2>&1
logger -p local0.info "[$tag:$LINENO] [mlan1] httxcfg 0x00000063"
mlanutl mlan1 httxcfg 0x00000063 > /dev/null 2>&1

sleep 0.2
logger -p local0.info "[$tag:$LINENO] [mlan0] htcapinfo 0x05c20000"
mlanutl mlan0 htcapinfo 0x05c20000 > /dev/null 2>&1
logger -p local0.info "[$tag:$LINENO] [mlan1] htcapinfo 0x05c20000"
mlanutl mlan1 htcapinfo 0x05c20000 > /dev/null 2>&1

sleep 0.2
logger -p local0.info "[$tag:$LINENO] [mlan0] reassoctrl enable"
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

sleep 0.2
logger -p local0.info "[$tag:$LINENO] [mlan0] link up"
ip link set mlan0 up
ifconfig mlan0 up

#sleep 0.2
#logger -p local0.info "[$tag:$LINENO] [mlan1] link down" 
ip link set mlan1 up
ifconfig mlan1 up

for i in {1..3}; do
    sleep 0.5
    cmd="iw mlan0 scan freq"
    logger -p local0.info "[$tag:$LINENO] [mlan0] cmd : $cmd"
    result=$(eval "$cmd")
    if [ -n "$result" ]; then
        logger -p local0.info "[$tag:$LINENO] [mlan0] scan : success"
        break
    else
        logger -p local0.err "[$tag:$LINENO] [mlan0] scan : no result"
    fi
done

sleep 0.5

FREQ=$(jq -r '.mlan0.Frequency' "$JSON_FILE")
if [ "$FREQ" = "5GHz" ]; then
    mlanutl mlan0 bandcfg 0x254
elif [ "$FREQ" = "2.4GHz" ]; then
    mlanutl mlan0 bandcfg 0x10b
elif [ "$FREQ" = "auto" ]; then
    mlanutl mlan0 bandcfg 0x35f
else
    ip link set mlan0 down
    ifconfig mlan0 down
fi

FREQ=$(jq -r '.mlan1.Frequency' "$JSON_FILE")
if [ "$FREQ" = "5GHz" ]; then
    mlanutl mlan1 bandcfg 0x54
elif [ "$FREQ" = "2.4GHz" ]; then
    mlanutl mlan1 bandcfg 0x0b
elif [ "$FREQ" = "auto" ]; then
    mlanutl mlan1 bandcfg 0x5f
else
    ip link set mlan1 down
    ifconfig mlan1 down
fi

#ip link add nlmon0 type nlmon
#ip link set nlmon0 up

sleep 0.5
#python3 /usr/local/logger/getmac.py
logger -p local0.info "[$tag:$LINENO] [mlan0] wpa_supplicant start"
#systemctl start wpa_supplicant@mlan0
wifi mlan0 up

sleep 0.5
#logger -p local0.info "[$tag:$LINENO] [mlan0] start wifi_bridge@mlan0"
systemctl restart wifi_bridge@mlan0

#echo 1 > /proc/sys/kernel/printk
