#!/bin/bash

tag=$(basename "$0")
KERNEL_VERSION=$(uname -r)
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
#python3 /usr/local/logger/mac_config.py

if ! try_insmod "/lib/modules/$KERNEL_VERSION/updates/mlan_6.12.ko" ""; then
    echo "mlan module load failed"  
    #exit 1
fi

if ! try_insmod "/lib/modules/$KERNEL_VERSION/updates/moal_6.12.ko" "mod_para=nxp/wifi_mod_para.conf mfg_mode=0"; then
    echo "moal module load failed"
    #exit 1
fi

#try_insmod "/lib/modules/$KERNEL_VERSION/updates/moal_6.12.ko" "fw_name=nxp/pcieuart9098_combo.bin mfg_mode=1"
#try_insmod "/lib/modules/$KERNEL_VERSION/kernel/drivers/net/nlmon.ko"

sleep 0.2
logger -p local0.info "[$tag:$LINENO] [mlan0] txpwrlimit set"
mlanutl mlan0 hostcmd /lib/firmware/nxp/config/txpwrlimit_cfg_9098.conf txpwrlimit_2g_cfg_set
mlanutl mlan0 hostcmd /lib/firmware/nxp/config/txpwrlimit_cfg_9098.conf txpwrlimit_5g_cfg_set_sub0
mlanutl mlan0 hostcmd /lib/firmware/nxp/config/txpwrlimit_cfg_9098.conf txpwrlimit_5g_cfg_set_sub1
mlanutl mlan0 hostcmd /lib/firmware/nxp/config/txpwrlimit_cfg_9098.conf txpwrlimit_5g_cfg_set_sub2
mlanutl mlan0 hostcmd /lib/firmware/nxp/config/txpwrlimit_cfg_9098.conf txpwrlimit_5g_cfg_set_sub3

sleep 0.2
logger -p local0.info "[$tag:$LINENO] [mlan1] txpwrlimit set"
mlanutl mlan1 hostcmd /lib/firmware/nxp/config/txpwrlimit_cfg_9098.conf txpwrlimit_2g_cfg_set
mlanutl mlan1 hostcmd /lib/firmware/nxp/config/txpwrlimit_cfg_9098.conf txpwrlimit_5g_cfg_set_sub0
mlanutl mlan1 hostcmd /lib/firmware/nxp/config/txpwrlimit_cfg_9098.conf txpwrlimit_5g_cfg_set_sub1
mlanutl mlan1 hostcmd /lib/firmware/nxp/config/txpwrlimit_cfg_9098.conf txpwrlimit_5g_cfg_set_sub2
mlanutl mlan1 hostcmd /lib/firmware/nxp/config/txpwrlimit_cfg_9098.conf txpwrlimit_5g_cfg_set_sub3

sleep 0.2
logger -p local0.info "[$tag:$LINENO] [mlan0] macctrl 0x00010e13"
mlanutl mlan0 macctrl 0x00010e13
logger -p local0.info "[$tag:$LINENO] [mlan1] macctrl 0x00010e13"
mlanutl mlan1 macctrl 0x00010e13

sleep 0.2
logger -p local0.info "[$tag:$LINENO] [mlan0] httxcfg 0x00000063"
mlanutl mlan0 httxcfg 0x00000063
logger -p local0.info "[$tag:$LINENO] [mlan1] httxcfg 0x00000063"
mlanutl mlan1 httxcfg 0x00000063

sleep 0.2
logger -p local0.info "[$tag:$LINENO] [mlan0] htcapinfo 0x05c20000"
mlanutl mlan0 htcapinfo 0x05c20000
logger -p local0.info "[$tag:$LINENO] [mlan1] htcapinfo 0x05c20000"
mlanutl mlan1 htcapinfo 0x05c20000

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

sleep 0.1
logger -p local0.info "[$tag:$LINENO] [mlan0] link up"
ip link set mlan0 up
sleep 0.1
logger -p local0.info "[$tag:$LINENO] [mlan1] link donw" 
ip link set mlan1 down

#ip link add nlmon0 type nlmon
#ip link set nlmon0 up

sleep 0.2
#python3 /usr/local/logger/getmac.py
logger -p local0.info "[$tag:$LINENO] [mlan0] wpa_supplicant start"
systemctl start wpa_supplicant@mlan0
#sleep 0.2
systemctl start wifi_bridge@mlan0

#echo 1 > /proc/sys/kernel/printk
