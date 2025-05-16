#!/bin/bash

KERNEL_VERSION=$(uname -r)
LOGFILE="/var/log/cantops/module.log"
sleep 0.5

output=$(insmod /lib/modules/$KERNEL_VERSION/updates/mlan.ko 2>&1)
ret=$?

if [ $ret -eq 0 ]; then
    echo "[SUCCESS] insmod mlan succeeded" >> "$LOGFILE"
else
    echo "[ERROR] insmod mlan failed (exit code: $ret)" >> "$LOGFILE"
    echo "[insmod output]" >> "$LOGFILE"
    echo "$output" >> "$LOGFILE"

    echo "[dmesg tail]" >> "$LOGFILE"
    dmesg | tail -n 10 >> "$LOGFILE"
fi

output=$(insmod /lib/modules/$KERNEL_VERSION/updates/moal.ko mod_para=nxp/wifi_mod_para_.conf mfg_mode=0 2>&1)
ret=$?

if [ $ret -eq 0 ]; then
    echo "[SUCCESS] insmod moal succeeded" >> "$LOGFILE"
else
    echo "[ERROR] insmod moal failed (exit code: $ret)" >> "$LOGFILE"
    echo "[insmod output]" >> "$LOGFILE"
    echo "$output" >> "$LOGFILE"

    echo "[dmesg tail]" >> "$LOGFILE"
    dmesg | tail -n 10 >> "$LOGFILE"
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
