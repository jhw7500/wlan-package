#!/bin/bash
tag=$(basename "$0")
WIFI_INIT_CONF_JSON="/usr/local/etc/wifi_init_conf.json"
logger -p local0.info "[$tag:$LINENO] start"
/usr/local/logger/print.py cyan "[factory] reset start"
echo none > /sys/class/leds/status/trigger
echo none > /sys/class/leds/lan/trigger
echo none > /sys/class/leds/wlan/trigger

echo heartbeat > /sys/class/leds/status/trigger
echo heartbeat > /sys/class/leds/lan/trigger
echo heartbeat > /sys/class/leds/wlan/trigger

cp /opt/wlan/config/logrotate.d/logrotate.rsyslog /etc/logrotate.d/
cp /opt/wlan/config/rc.local /etc/
cp /opt/wlan/config/rsyslog.conf /etc/
cp /opt/wlan/config/smb.conf /etc/samba/
#cp /opt/wlan/config/systemd/timesyncd.conf /etc/systemd/
cp /opt/wlan/config/wifi_mod_para__.conf /lib/firmware/nxp/
cp /opt/wlan/config/crontab /etc/
#cp /opt/wlan/firmware/* /lib/firmware/nxp/
#cp /opt/wlan/driver/* /lib/modules/$KERNEL_VERSION/updates/
cp /opt/wlan/config/wpa_supplicant/wpa_supplicant@.service /lib/systemd/system/
cp /opt/wlan/config/systemd/journald.conf /etc/systemd/

customctl() {
    target=$1
    daemon_name=$2

    status=$(systemctl is-enabled $daemon_name 2>/dev/null)

    if [[ $status != ${target}* ]]; then
        if [[ $target = enable* ]]; then
            systemctl enable $daemon_name
        elif [[ $target = disable* ]]; then
            if [[ $status != mask* ]]; then
                systemctl disable --now $daemon_name
            fi
        elif [[ $target = mask* ]]; then
            systemctl disable --now $daemon_name
            systemctl mask $daemon_name
        else
            echo "$target is wrong status"
            return 1
        fi
    fi

    status=$(systemctl is-enabled $daemon_name 2>/dev/null)
    echo "$daemon_name is $status"
    INSTALL_DOT_COUNT=$((INSTALL_DOT_COUNT + 1))
    dots=$(printf "%${INSTALL_DOT_COUNT}s" | tr ' ' '.')
    /usr/local/logger/print.py green "\r\n[factory] reset${dots}"
}

  customctl mask systemd-networkd-wait-online
  customctl mask nfs-server
  customctl mask proc-fs-nfsd.mount
  customctl mask ovsdb-server
  customctl mask avahi-daemon
  customctl mask avahi-autoipd
  customctl mask psplash
  customctl disable dhcpcd
  customctl disable psplash-quit
  customctl disable weston
  customctl disable openvswitch
  customctl disable openvswitch-switch
  customctl disable connman
  customctl disable smb
  customctl disable nmb
  customctl disable avahi-daemon.socket
  customctl disable containerd
  customctl disable lighttpd
  customctl disable nginx
  customctl disable monkey
  customctl disable netdata
  customctl disable collectd
  customctl disable thttpd
  customctl disable named
  customctl disable bind
  customctl disable fake-hwclock.timer
  customctl disable journald-snapshot.timer
  customctl disable ifplugd
  customctl disable systemd-resolved
  customctl disable hiawatha
  customctl disable netserver

  customctl enable watchdog
  customctl enable switchd enabled
  customctl enable fake-hwclock
  customctl enable wifi_init
  customctl enable wifi_logger

  customctl disable wifi_checker@eth0
  customctl enable wifi_led@eth0
  customctl enable wifi_logger@eth0

  customctl enable arping@mlan0
  customctl enable wifi_led@mlan0
  customctl enable wifi_logger@mlan0
  customctl enable wifi_checker@mlan0
  customctl enable wifi_bgscan@mlan0
  customctl enable wifi_roam@mlan0
  customctl enable wifi_capture@mlan0

  if [ -f "$WIFI_INIT_CONF_JSON" ] && command -v jq >/dev/null 2>&1; then
      jq '.mac.mlan0.target = "" | .mac.mlan1.target = ""' "$WIFI_INIT_CONF_JSON" > "${WIFI_INIT_CONF_JSON}.tmp"
      mv "${WIFI_INIT_CONF_JSON}.tmp" "$WIFI_INIT_CONF_JSON"
      logger -p local0.info "[$tag:$LINENO] MAC target reset in JSON"
  else
      logger -p local0.warn "[$tag:$LINENO] JSON MAC reset skipped (jq or config not found)"
  fi

  cp /opt/wlan/mfg/bridge_init.conf /usr/local/mfg/bridge_init.conf
  cp /opt/wlan/config/systemd/timesyncd.conf /etc/systemd/timesyncd.conf
  cp /opt/wlan/config/wpa_supplicant/wpa_supplicant-mlan0.conf /etc/wpa_supplicant/wpa_supplicant-mlan0.conf
  cp /opt/wlan/config/wpa_supplicant/wpa_supplicant-mlan1.conf /etc/wpa_supplicant/wpa_supplicant-mlan1.conf
  cp /opt/wlan/config/systemd/network/20-mlan0.network /etc/systemd/network/20-mlan0.network
  cp /opt/wlan/config/systemd/network/21-mlan1.network /etc/systemd/network/21-mlan1.network
  cp /opt/wlan/config/systemd/network/22-eth0.network /etc/systemd/network/22-eth0.network
  cp /opt/wlan/config/systemd/network/10-lo.network /etc/systemd/network/10-lo.network
  cp /opt/wlan/config/systemd/network/20-mlan0.link /etc/systemd/network/20-mlan0.link
  cp /opt/wlan/config/systemd/network/21-mlan1.link /etc/systemd/network/21-mlan1.link
  cp /opt/wlan/config/systemd/network/22-eth0.link /etc/systemd/network/22-eth0.link

#find /var/log/cantops -mindepth 1 -maxdepth 1 ! -name journald -exec rm -rf {} +
LOG_DIR=/var/log/cantops
rm -rf $LOG_DIR/local0.log-*
rm -rf $LOG_DIR/kern.log-*
rm -rf $LOG_DIR/sys.log-*
rm -rf $LOG_DIR/ui.log-*
rm -rf $LOG_DIR/cpu/*
rm -rf $LOG_DIR/summary/*
rm -rf $LOG_DIR/scan/mlan0/*
rm -rf $LOG_DIR/scan/mlan1/*
rm -rf $LOG_DIR/stat/mlan0/*
rm -rf $LOG_DIR/stat/mlan1/*
rm -rf $LOG_DIR/wpa/mlan0/*
rm -rf $LOG_DIR/wpa/mlan1/*
rm -rf $LOG_DIR/mgmt/mlan0/*
rm -rf $LOG_DIR/mgmt/mlan1/*
rm -rf $LOG_DIR/err/* -r
rm -rf $LOG_DIR/journald/* -r
rm $LOG_DIR/max_temp
rm -rf $LOG_DIR/cpu.log-*

cat /dev/null > $LOG_DIR/local0.log
cat /dev/null > kern.log
cat /dev/null > sys.log
cat /dev/null > ui.log

echo none > /sys/class/leds/status/trigger
echo none > /sys/class/leds/lan/trigger
echo none > /sys/class/leds/wlan/trigger

echo 1 > /sys/class/leds/status/brightness
echo 1 > /sys/class/leds/lan/brightness
echo 1 > /sys/class/leds/wlan/brightness


echo "factory reset finish"
/usr/local/logger/print.py cyan "[factory] reset finish"
