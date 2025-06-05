#!/bin/bash

tag=$(basename "$0")
KERNEL_VERSION=$(uname -r)
#LOGFILE="/var/log/cantops/module.log"
#sleep 0.5

try_insmod() {
    local module_path=$1
    local args=$2
    local output
    local ret

    output=$(insmod "$module_path" $args 2>&1)
    ret=$?

    if [ $ret -eq 0 ]; then
        logger -p local0.notice "[$tag:$LINENO] insmod $(basename $module_path) success"
    else
        logger -p local0.err "[$tag:$LINENO] insmod $(basename $module_path) fail"
        logger -p local0.err "[$tag:$LINENO] $output"
    fi

    return $ret
}

try_insmod "/lib/modules/$KERNEL_VERSION/updates/mlan.ko" ""
try_insmod "/lib/modules/$KERNEL_VERSION/updates/moal.ko" "mod_para=nxp/wifi_mod_para_.conf mfg_mode=0"
#try_insmod "/lib/modules/$KERNEL_VERSION/kernel/drivers/net/nlmon.ko"

sleep 0.5
iw dev mlan0 set power_save off

sleep 0.2
mlanutl mlan0 macctrl 0x00010e13
mlanutl mlan1 macctrl 0x00010e13

sleep 0.2
mlanutl mlan0 httxcfg 0x00000063
mlanutl mlan1 httxcfg 0x00000063

sleep 0.2
mlanutl mlan0 htcapinfo 0x05c20000
mlanutl mlan1 htcapinfo 0x05c20000

echo 1 > /proc/sys/net/ipv4/ip_forward

#ip link add nlmon0 type nlmon
ip link set mlan0 up
ip link set mlan1 up
#ip link set nlmon0 up

echo 1 > /proc/sys/kernel/printk

systemctl start wpa_supplicant@mlan0
systemctl start wifi_bridge@mlan0
