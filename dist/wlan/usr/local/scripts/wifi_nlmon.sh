#!/bin/bash
KERNEL_VERSION=$(uname -r)
#insmod /lib/modules/$KERNEL_VERSION/kernel/drivers/net/nlmon.ko
#insmod /lib/modules/$KERNEL_VERSION/updates/nlmon.ko

#ip link add nlmon0 type nlmon
#ip link set nlmon0 up

mlanutl mlan0 mgmtframectrl 0x363f
