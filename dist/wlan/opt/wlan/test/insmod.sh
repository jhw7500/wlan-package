#!/bin/bash
insmod /opt/wlan/driver/mlan.ko
insmod /opt/wlan/driver/moal.ko mod_para=cts/wifi_mod_para.conf

