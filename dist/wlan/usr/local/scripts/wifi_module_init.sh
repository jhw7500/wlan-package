#!/bin/bash

KERNEL_VERSION=$(uname -r)
LOGFILE="/var/log/cantops/module.log"
sleep 0.5

output=$(insmod /lib/modules/$KERNEL_VERSION/updates/mlan.ko 2>&1)
ret=$?

if [ $ret -eq 0 ]; then
    #echo "[SUCCESS] insmod mlan succeeded" >> "$LOGFILE"
    logger -p local0.notice "[$tag:$LINENO] insmod mlan success"
else
    #echo "[ERROR] insmod mlan failed (exit code: $ret)" >> "$LOGFILE"
    #echo "[insmod output]" >> "$LOGFILE"
    #echo "$output" >> "$LOGFILE"

    #echo "[dmesg tail]" >> "$LOGFILE"
    #dmesg | tail -n 10 >> "$LOGFILE"
    logger -p local0.err "[$tag:$LINENO] insmod mlan fail"
    logger -p local0.err "[$tag:$LINENO] $output"
fi

#output=$(insmod /lib/modules/$KERNEL_VERSION/updates/moal.ko mod_para=nxp/wifi_mod_para_.conf mfg_mode=0 mac_addr=90:2c:fb:00:f0:89 2>&1)
output=$(insmod /lib/modules/$KERNEL_VERSION/updates/moal.ko mod_para=nxp/wifi_mod_para_.conf mfg_mode=0 2>&1)
ret=$?

if [ $ret -eq 0 ]; then
    #echo "[SUCCESS] insmod moal succeeded" >> "$LOGFILE"
    logger -p local0.notice "[$tag:$LINENO] insmod moal success"
else
    #echo "[ERROR] insmod moal failed (exit code: $ret)" >> "$LOGFILE"
    #echo "[insmod output]" >> "$LOGFILE"
    #echo "$output" >> "$LOGFILE"

    #echo "[dmesg tail]" >> "$LOGFILE"
    #dmesg | tail -n 10 >> "$LOGFILE"
    logger -p local0.err "[$tag:$LINENO] insmod moal fail"
    logger -p local0.err "[$tag:$LINENO] $output"
fi

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

ifconfig mlan0 up
ifconfig mlan1 up
