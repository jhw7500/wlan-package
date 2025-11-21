#!/bin/bash
tag=$(basename "$0")
IFACE=$1
logger -p local0.info "[$tag:$LINENO] [$IFACE] wifi $1 $2 $3 $4"
CONF_DIR="/etc/test"
NUM=""

case "$1" in
  0 | mlan0)
    IFACE="mlan0"
    NFACE="mlan1"
    NUM=0
    ;;
  1 | mlan1)
    IFACE="mlan1"
    NUM=1
    NFACE="mlan0"
    ;;
  2 )
    echo "autoselect mlan0, mlan1"
    #systemctl restart wpa_supplicant@mlan0
    #systemctl restart wpa_supplicant@mlan1
    ;;
  mfg)
    ifconfig mlan0 down
    ifconfig mlan1 down
    rmmod moal
    rmmod mlan
    insmod /opt/wlan/driver/mlan.ko
    insmod /opt/wlan/driver/moal.ko mfg_mode=1 drv_mode=1 fw_name=cts/pcieuart9098_combo.bin
    cd /usr/local/mfg/
    ./mfgbridge
    exit 1
    ;;
  txpwrlimit)
    cp $2 /lib/firmware/cts/
    python3 /usr/local/logger/wifi_config.py mlan0 txpwrlimit_cfg cts/$2
    python3 /usr/local/logger/wifi_config.py mlan1 txpwrlimit_cfg cts/$2
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
        sleep 1
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
  txpwrlimit)
    if [ "$3" == "low" ]; then
        TXPWRLIMIT_PATH=/lib/firmware/cts/config/txpwrlimit_cfg_9098_low.conf
    elif [ "$3" == "test" ]; then
        TXPWRLIMIT_PATH=/lib/firmware/cts/config/txpwrlimit_cfg_9098_test.conf
    else
        TXPWRLIMIT_PATH=/lib/firmware/cts/config/txpwrlimit_cfg_9098.conf
    fi
    echo "$IFACE txpwrlimit set to $TXPWRLIMIT_PATH"
    mlanutl $IFACE hostcmd $TXPWRLIMIT_PATH txpwrlimit_2g_cfg_set > /dev/null 2>&1
    mlanutl $IFACE hostcmd $TXPWRLIMIT_PATH txpwrlimit_5g_cfg_set_sub0 > /dev/null 2>&1
    mlanutl $IFACE hostcmd $TXPWRLIMIT_PATH txpwrlimit_5g_cfg_set_sub1 > /dev/null 2>&1
    mlanutl $IFACE hostcmd $TXPWRLIMIT_PATH txpwrlimit_5g_cfg_set_sub2 > /dev/null 2>&1
    mlanutl $IFACE hostcmd $TXPWRLIMIT_PATH txpwrlimit_5g_cfg_set_sub3 > /dev/null 2>&1
    ;;
  config)
    echo "python3 /usr/local/logger/wifi_config.py $1 $3 $4"
    python3 /usr/local/logger/wifi_config.py $1 $3 $4
    ;;
  mac)
    echo "python3 /usr/local/logger/wifi_config.py $1 mac_addr $3"
    python3 /usr/local/logger/wifi_config.py $1 mac_addr $3
    ;;
  cal)
    cp $4 /lib/firmware/cts/
    python3 /usr/local/logger/wifi_config.py $1 cal_data_cfg cts/$3
    ;;
  mfg)
    if [ "$3" == "0" ]; then
        python3 /usr/local/logger/wifi_config.py $1 mfg_mode 0
        python3 /usr/local/logger/wifi_config.py $1 fw_name cts/pcieuart9098_combo_v1.bin
    elif [ "$3" == "1" ]; then
        python3 /usr/local/logger/wifi_config.py $1 mfg_mode 1
        python3 /usr/local/logger/wifi_config.py $1 fw_name cts/pcieuart9098_combo.bin
    fi
    ;;
  spoof)
    if [ "$3" == "dynamic" ]; then
        cat /dev/null > "/opt/wlan/mac/target$NUM"
    elif [ "$3" == "static" ]; then
        cp /tmp/eth0_client_mac "/opt/wlan/mac/target$NUM"
    fi
    ;;
  *)
    echo "Usage: $0 {0|1|mlan0|mlan1} {start|up|stop|down|restart|status}"
    exit 1
    ;;
esac

