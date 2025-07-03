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
    else
        echo "Usage: $0 {0|1|mlan0|mlan1|2} {start|up|stop|down|restart|status|br|update}"
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
  config)
    case "$3" in
      mac)
        python3 /usr/local/logger/wifi_config.py $1 mac_addr $4
        ;;
      txpwr)
        cp $4 /lib/firmware/nxp/
        python3 /usr/local/logger/wifi_config.py $1 txpwrlimit_cfg nxp/$4
        ;;
      cal)
        cp $4 /lib/firmware/nxp/
        python3 /usr/local/logger/wifi_config.py $1 cal_data_cfg nxp/$4
        ;;
      mfg)
        if [ "$4" == "0" ]; then
            python3 /usr/local/logger/wifi_config.py $1 mfg_mode 0
            python3 /usr/local/logger/wifi_config.py $1 fw_name nxp/pcieuart9098_combo_v1.bin
        elif [ "$4" == "1" ]; then
            python3 /usr/local/logger/wifi_config.py $1 mfg_mode 1
            python3 /usr/local/logger/wifi_config.py $1 fw_name nxp/pcieuart9098_combo.bin
        fi
        ;;
      *)
        ;;
    esac
    ;;
  *)
    echo "Usage: $0 {0|1|mlan0|mlan1} {start|up|stop|down|restart|status}"
    exit 1
    ;;
esac

