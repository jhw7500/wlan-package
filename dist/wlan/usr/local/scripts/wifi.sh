#!/bin/bash

CONF_DIR="/etc/test"

case "$1" in
  0 | mlan0)
    IFACE="mlan0"
    NFACE="mlan1"
    ;;
  1 | mlan1)
    IFACE="mlan1"
    NFACE="mlan0"
    ;;
  2 )
    echo "autoselect mlan0, mlan1"
    #systemctl restart wpa_supplicant@mlan0
    #systemctl restart wpa_supplicant@mlan1
    ;;
  *)
    echo "Usage: $0 {0|1|mlan0|mlan1} {start|up|stop|down|restart|status}"
    exit 1
    ;;
esac

case "$2" in
  "" | restart)
    if [ "$1" == 2 ]; then
        echo "Restarting WPA service..."
        systemctl restart wpa_supplicant@mlan0
        systemctl restart wpa_supplicant@mlan1
    else
        echo "Restarting WPA service $IFACE..."
        systemctl stop wpa_supplicant@$IFACE
        sleep 0.3
        systemctl start wpa_supplicant@$IFACE
    fi
    ;;
  start | up)
    if [ "$1" == 2 ]; then
        echo "Starting WPA service.."
        systemctl start wpa_supplicant@mlan0
        systemctl start wpa_supplicant@mlan1
    else
        echo "Starting WPA service $IFACE..."
        systemctl start wpa_supplicant@$IFACE
    fi
    #systemctl start wpa_supplicant@$IFACE
    ;;
  stop | down)
    if [ "$1" == 2 ]; then
        echo "Stopping WPA service..."
        systemctl stop wpa_supplicant@mlan0
        systemctl stop wpa_supplicant@mlan1
    else
        echo "Stopping WPA service $IFACE..."
        systemctl stop wpa_supplicant@$IFACE
    fi
    #systemctl stop wpa_supplicant@$IFACE
    ;;
  status)
    if [ "$1" != 2 ]; then
        echo "Checking status $IFACE..."
        systemctl status wpa_supplicant@$IFACE
    fi
    ;;
  br)
    if [ "$1" == 2 ]; then
        #systemctl stop wifi_bridge@$NFACE
        systemctl restart wifi_bridge@mlan0
        systemctl restart wifi_bridge@mlan1
    else
        systemctl restart wifi_bridge@$IFACE
    fi
    ;;
  *)
    echo "Usage: $0 {0|1|mlan0|mlan1} {start|up|stop|down|restart|status}"
    exit 1
    ;;
esac

