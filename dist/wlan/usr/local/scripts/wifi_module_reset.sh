#!/bin/bash

KERNEL_VERSION=$(uname -r)

ifconfig mlan0 down
ifconfig mlan1 down
ifconfig rtap down

sleep 0.5

rmmod moal
rmmod mlan

/usr/local/scripts/wifi_module_init.sh
