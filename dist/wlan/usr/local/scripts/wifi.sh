#!/bin/bash
tag=$(basename "$0")
IFACE=$1
CONF_DIR="/etc/test"
NUM=""

logger -p local0.info "[$tag:$LINENO] [$IFACE] cmd : wifi $1 $2 $3 $4"

usage() {
    echo "Usage: wifi {0|1|mlan0|mlan1} {start|up|stop|down|restart|status|br}"
    echo "       wifi {0|1|mlan0|mlan1} txpwrlimit {0|1|2|default|low|test}"
    echo "       wifi {0|1|mlan0|mlan1} config {conf} {value} : file"
    echo "       wifi {0|1|mlan0|mlan1} cal {conf_file_name} : file"
    echo "       wifi {0|1|mlan0|mlan1} mfg {0|1|off|on} : file"
    echo "       wifi {0|1|mlan0|mlan1} mac {0|1|base|target} {mac_address} : file"
    echo "       wifi {0|1|mlan0|mlan1} spoof {0|1|dynamic|static} : file"
    echo "       wifi {0|1|mlan0|mlan1} limit {4|5|6} : file"
    echo "       wifi {0|1|mlan0|mlan1} freq {freq_list|channel_list} : file"
    echo "       wifi {0|1|mlan0|mlan1} scan {freq_list|channel_list}"
    echo "       wifi txpwrlimit {conf_file_name} :file"
    echo "       wifi mfg {0|1|off|on}"
    echo "       wifi ant {0|1|internal|external}"
    exit 1
}

to_freq_mhz() {
    local v="$1"
    if ! [[ "$v" =~ ^[0-9]+$ ]]; then
        echo "$v"
        return
    fi

    if (( v < 1000 )); then
        if (( v >= 1 && v <= 13 )); then
            echo $((2407 + 5 * v))     # ch1=2412, ch6=2437, ...
            return
        elif (( v == 14 )); then
            echo 2484
            return
        else
            echo $((5000 + 5 * v))
            return
        fi
    else
        echo "$v"
        return
    fi
}

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
  mfg)
    if [ "$2" == "0" ]; then
        systemctl restart wifi_init
    elif [ "$2" == "1" ]; then
        ifconfig mlan0 down
        ifconfig mlan1 down
        rmmod moal
        rmmod mlan
        insmod /opt/wlan/driver/mlan.ko
        insmod /opt/wlan/driver/moal.ko mfg_mode=1 drv_mode=1 fw_name=cts/pcieuart9098_combo.bin
        cd /usr/local/mfg/
        ./mfgbridge
    else
        usage
    fi
    exit 1
    ;;
  txpwrlimit)
    echo "config file is $2 for txpwrlimit"
    cp $2 /lib/firmware/cts/
    python3 /usr/local/logger/wifi_config.py mlan0 txpwrlimit_cfg cts/$2
    python3 /usr/local/logger/wifi_config.py mlan1 txpwrlimit_cfg cts/$2
    exit 1
    ;;
  ant)
    if [ "$2" == "internal" ] || [ "$2" == "0" ]; then
        wifi 0 down
        wifi 1 down
        sleep 1
        echo "set to internal antenna mode"
        echo 0 > /sys/class/leds/SW_SEL1/brightness
        echo 1 > /sys/class/leds/SW_SEL2/brightness
    elif [ "$2" == "external" ] || [ "$2" == "1" ]; then
        wifi 1 down
        wifi 1 down
        sleep 1
        echo "set to external antenna mode"
        echo 1 > /sys/class/leds/SW_SEL1/brightness
        echo 0 > /sys/class/leds/SW_SEL2/brightness
    else
        usage
    fi
    exit 1
    ;;
  *)
    usage
    ;;
esac

if [ "$IFACE" != "mlan0" ] && [ "$IFACE" != "mlan1" ]; then
    usage
fi

case "$2" in
  "" | restart)
    echo "restart WPA service for $IFACE..."
    systemctl restart wpa_supplicant@$IFACE
    ;;
  start | up)
    echo "Starting WPA service for $IFACE..."
    systemctl start wpa_supplicant@$IFACE
    ;;
  stop | down)
    echo "Stopping WPA service for $IFACE..."
    systemctl stop wpa_supplicant@$IFACE
    ;;
  status)
    systemctl status wpa_supplicant@$IFACE
    ;;
  br)
    echo "restart bridge for $IFACE..."
    systemctl restart wifi_bridge@$IFACE
    ;;
  txpwrlimit)
    if [ "$3" == "default" ] || [ "$3" == "0" ]; then
        echo "default txpwrlimit for $IFACE"
        CONF=/lib/firmware/cts/config/txpwrlimit_cfg_9098.conf
    elif [ "$3" == "low" ] || [ "$3" == "1" ]; then
        echo "low txpwrlimit for $IFACE"
        CONF=/lib/firmware/cts/config/txpwrlimit_cfg_9098_low.conf
    elif [ "$3" == "test" ] || [ "$3" == "2" ]; then
        echo "test txpwrlimit for $IFACE"
        CONF=/lib/firmware/cts/config/txpwrlimit_cfg_9098_test.conf
    else
        usage
    fi
    echo "txpwrlimit set to $CONF for $IFACE"
    mlanutl $IFACE hostcmd $CONF txpwrlimit_2g_cfg_set > /dev/null 2>&1
    mlanutl $IFACE hostcmd $CONF txpwrlimit_5g_cfg_set_sub0 > /dev/null 2>&1
    mlanutl $IFACE hostcmd $CONF txpwrlimit_5g_cfg_set_sub1 > /dev/null 2>&1
    mlanutl $IFACE hostcmd $CONF txpwrlimit_5g_cfg_set_sub2 > /dev/null 2>&1
    mlanutl $IFACE hostcmd $CONF txpwrlimit_5g_cfg_set_sub3 > /dev/null 2>&1
    ;;
  config)
    echo "config $3 value set to $4 for $IFACE"
    python3 /usr/local/logger/wifi_config.py $1 $3 $4
    ;;
  mac)
    if [ "$3" == "base" ] || [ "$3" == "0" ]; then
        echo "base mac set to $4 for $IFACE"
        echo "$4" > /opt/wlan/mac/base$NUM
    elif [ "$3" == "target" ] || [ "$3" == "1" ]; then
        echo "target mac set to $4 for $IFACE"
        echo "$4" > /opt/wlan/mac/target$NUM
    else
        usage
    fi
    #echo "python3 /usr/local/logger/wifi_config.py $1 mac_addr $3"
    #python3 /usr/local/logger/wifi_config.py $1 mac_addr $3
    ;;
  cal)
    #cp $4 /lib/firmware/cts/
    echo "cal_data_cfg file set to $3 for $IFACE"
    python3 /usr/local/logger/wifi_config.py $1 cal_data_cfg cts/$3
    ;;
  mfg)
    if [ "$3" == "off" ] || [ "$3" == "0" ]; then
        echo "mfg_mode set to off for $IFACE" 
        python3 /usr/local/logger/wifi_config.py $1 mfg_mode 0
        python3 /usr/local/logger/wifi_config.py $1 fw_name cts/pcieuart9098_combo_v1.bin
    elif [ "$3" == "on" ] || [ "$3" == "1" ]; then
        echo "mfg_mode set to on for $IFACE"
        python3 /usr/local/logger/wifi_config.py $1 mfg_mode 1
        python3 /usr/local/logger/wifi_config.py $1 fw_name cts/pcieuart9098_combo.bin
    else
        usage
    fi
    ;;
  spoof)
    if [ "$3" == "dynamic" ] || [ "$3" == "0" ]; then
        echo "spoofing mode set to dynamic for $IFACE"
        cat /dev/null > "/opt/wlan/mac/target$NUM"
    elif [ "$3" == "static" ] || [ "$3" == "1" ]; then
        echo "spoofing mode set to static for $IFACE"
        cp /tmp/eth0_client_mac "/opt/wlan/mac/target$NUM"
    else
        usage
    fi
    ;;
  freq)
    set -euo pipefail
    shift 2
    CONF="/etc/wpa_supplicant/wpa_supplicant-${IFACE}.conf"
    if [ ! -f "$CONF" ]; then
        echo "not found�: $CONF" >&2
        exit 1
    fi
    FREQS=()
    for arg in "$@"; do
        FREQS+=( "$(to_freq_mhz "$arg")" )
    done

    if [ ${#FREQS[@]} -eq 0 ]; then
        echo "configure freq not exist" >&2
        exit 1
    fi

    FREQ_STR="${FREQS[*]}"   # "5200 5220 5240" 형태
    TMP_FILE="$(mktemp)"

    awk -v freqs="$FREQ_STR" '
    BEGIN {
        found_scan = 0
        found_list = 0
    }
    /^[[:space:]]*#/ {
        print
        next
    }
    /^[[:space:]]*scan_freq[[:space:]]*=/ {
        print "    scan_freq=" freqs
        found_scan = 1
        next
    }
    /^[[:space:]]*freq_list[[:space:]]*=/ {
        print "freq_list=" freqs
        found_list = 1
        next
    }
    {
        print
    }
    END {
        if (!found_scan)
            print "scan_freq=" freqs
        if (!found_list)
            print "freq_list=" freqs
    }
    ' "$CONF" > "$TMP_FILE"

    mv "$TMP_FILE" "$CONF"

    echo "scan_freq / freq_list configure $FREQ_STR in $CONF"
    ;;
  scan)
    set -euo pipefail
    shift 2
    FREQS=()
    for arg in "$@"; do
        FREQS+=( "$(to_freq_mhz "$arg")" )
    done

    if [ ${#FREQS[@]} -eq 0 ]; then
        echo "configure freq not exist" >&2
        exit 1
    fi

    FREQ_STR="${FREQS[*]}"   # "5200 5220 5240"  ^x^u ^c^|
    TMP_FILE="$(mktemp)"
    echo "scanning freq_list $FREQ_STR for $IFACE"
    iw $IFACE scan freq $FREQ_STR
    ;;
  limit)
    if [ "$3" == "4" ]; then
        echo "limit to wifi4 for $IFACE" 
        python3 /usr/local/logger/wifi_config.py $1 dev_cap_mask 0xfffc07ff
    elif [ "$3" == "5" ]; then
        echo "limit to wifi5 for $IFACE"
        python3 /usr/local/logger/wifi_config.py $1 dev_cap_mask 0xfffcffff
    elif [ "$3" == "6" ]; then
        echo "limit to wifi6 for $IFACE"
        python3 /usr/local/logger/wifi_config.py $1 dev_cap_mask 0xffffffff
    else
        usage
    fi
    ;;
  *)
    usage
    ;;
esac

