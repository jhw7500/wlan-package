#!/bin/bash
systemctl stop wifi_logger
systemctl stop wifi_logger@mlan0
systemctl stop wifi_capture@mlan0
systemctl stop wifi_checker@mlan0
wifi 0 down
wifi 1 down
ifconfig mlan0 down
ifconfig mlan1 down
rmmod moal
rmmod mlan
