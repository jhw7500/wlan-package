#!/bin/bash

CONF_DIR="/etc/test"

case "$1" in
  0 | mlan0)
    IFACE="mlan0"
    CONF_FILE="wpa_supplicant.conf"
    ;;
  1 | mlan1)
    IFACE="mlan1"
    CONF_FILE="wpa_supplicant2.conf"
    ;;
  *)
    echo "Usage: $0 {0|1|mlan0|mlan1} {start|stop|status}"
    exit 1
    ;;
esac

case "$2" in
  "" | restart)
    pid=$(ps -ef |grep "wpa_supplicant -i $IFACE" |grep -v grep |awk '{print $2}')
    if [ -n "$pid" ]; then
        echo "Stopping WPA service $IFACE..."
        kill $pid
        sleep 1
    fi

    echo "Starting WPA service $IFACE..."
    wpa_supplicant -i $IFACE -c $CONF_DIR/$CONF_FILE -B
    ;;
  start | up)
    echo "Starting WPA service $IFACE..."
    wpa_supplicant -i $IFACE -c $CONF_DIR/$CONF_FILE -B
    ;;
  stop | down)
    pid=$(ps -ef |grep "wpa_supplicant -i $IFACE" |grep -v grep |awk '{print $2}')
    if [ -n "$pid" ]; then
        echo "Stopping WPA service $IFACE..."
        kill $pid
    fi
    ;;
  status)
    echo "Checking status..."
    ;;
  *)
    echo "Usage: $0 {0|1|mlan0|mlan1} {start|stop|status}"
    exit 1
    ;;
esac

