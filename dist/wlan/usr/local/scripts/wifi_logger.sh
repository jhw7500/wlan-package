#!/bin/bash

# Backward-compatible entry point. systemd owns the individual logger children.
exec "${WIFI_SH:-/usr/local/scripts/wifi.sh}" log system start
