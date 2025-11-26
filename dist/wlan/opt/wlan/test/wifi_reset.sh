#!/bin/bash
echo 0 > /sys/class/leds/RFREG_nRST/brightness
sleep 1
echo 1 > /sys/bus/pci/devices/0000\:01\:00.0/remove
echo 1 > /sys/bus/pci/devices/0000\:01\:00.1/remove
sleep 1
echo 1 > /sys/class/leds/RFREG_nRST/brightness
#sleep 1
#echo 1 > /sys/bus/pci/rescan
