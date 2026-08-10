#!/bin/bash
tag=$(basename "$0")
WIFI_INIT_CONF_JSON="/usr/local/etc/wifi_init_conf.json"

# === 헬퍼 함수 (LED/print/cp 호출 전에 정의) ===

# 안전한 cp wrapper — 실패 시 logger.err. 모든 인자(와일드카드 포함)를 cp에 전달.
safe_cp() {
    if ! cp "$@" 2>/dev/null; then
        logger -p local0.err "[$tag:$LINENO] cp failed: $*"
        return 1
    fi
}

# 안전한 sysfs write wrapper — LED/brightness 등. 실패는 critical 아님 → logger.warn.
safe_sysfs_write() {
    local value="$1" path="$2"
    if ! echo "$value" > "$path" 2>/dev/null; then
        logger -p local0.warn "[$tag:$LINENO] sysfs write failed: $value > $path"
    fi
}

# 안전한 print.py wrapper — 콘솔 컬러 출력. 도구 호출 실패 시 logger.warn.
safe_print() {
    if ! /usr/local/logger/print.py "$@" 2>/dev/null; then
        logger -p local0.warn "[$tag:$LINENO] print.py failed: $*"
    fi
}

logger -p local0.info "[$tag:$LINENO] start"
safe_print cyan "[factory] reset start"
safe_sysfs_write none /sys/class/leds/status/trigger
safe_sysfs_write none /sys/class/leds/lan/trigger
safe_sysfs_write none /sys/class/leds/wlan/trigger

safe_sysfs_write heartbeat /sys/class/leds/status/trigger
safe_sysfs_write heartbeat /sys/class/leds/lan/trigger
safe_sysfs_write heartbeat /sys/class/leds/wlan/trigger

# === Deprecated 잔재 정리 ===
# 옛 버전 패키지에서 설치되어 현재는 사용 안 하는 파일들 제거.
# - 99-bd-arp.conf: peer_route sysctl 정책이 wifi_init.sh의 토글 분기로 통합되기 전(2026-05-27 이전)
#   /etc/sysctl.d/에 배포되던 파일. 현재는 wifi_init.sh가 wbridge.peer_route.enabled 토글에 따라
#   sysctl -w로 직접 적용/회수하므로 정적 파일이 남아있으면 토글 일관성을 깨뜨림.
rm -f /etc/sysctl.d/99-bd-arp.conf

safe_cp /opt/wlan/config/logrotate.d/logrotate.rsyslog /etc/logrotate.d/
safe_cp /opt/wlan/config/rc.local /etc/
safe_cp /opt/wlan/config/rsyslog.conf /etc/
safe_cp /opt/wlan/config/smb.conf /etc/samba/
#cp /opt/wlan/config/systemd/timesyncd.conf /etc/systemd/
safe_cp /opt/wlan/config/wlan/* /lib/firmware/cts/
safe_cp /opt/wlan/config/crontab /etc/
#cp /opt/wlan/firmware/* /lib/firmware/nxp/
#cp /opt/wlan/driver/* /lib/modules/$KERNEL_VERSION/updates/
safe_cp /opt/wlan/config/wpa_supplicant/wpa_supplicant@.service /lib/systemd/system/
safe_cp /opt/wlan/config/systemd/journald.conf /etc/systemd/

customctl() {
    target=$1
    daemon_name=$2

    status=$(systemctl is-enabled $daemon_name 2>/dev/null)

    if [[ $status != ${target}* ]]; then
        if [[ $target = enable* ]]; then
            if ! systemctl enable $daemon_name 2>/dev/null; then
                logger -p local0.err "[$tag:$LINENO] systemctl enable failed: $daemon_name"
            fi
        elif [[ $target = disable* ]]; then
            if [[ $status != mask* ]]; then
                if ! systemctl disable --now $daemon_name 2>/dev/null; then
                    logger -p local0.err "[$tag:$LINENO] systemctl disable failed: $daemon_name"
                fi
            fi
        elif [[ $target = mask* ]]; then
            systemctl disable --now $daemon_name 2>/dev/null || true
            if ! systemctl mask $daemon_name 2>/dev/null; then
                logger -p local0.err "[$tag:$LINENO] systemctl mask failed: $daemon_name"
            fi
        else
            # invalid target — typo/오타 즉시 syslog로 발견 (예: 'eanble' 같은 미래 함정)
            # 기존엔 echo만 남기고 silent return 1이라 발견이 어려웠음.
            logger -p local0.err "[$tag:$LINENO] INVALID target='$target' for daemon='$daemon_name' (must match enable*|disable*|mask*)"
            echo "$target is wrong status"
            return 1
        fi
    fi

    status=$(systemctl is-enabled $daemon_name 2>/dev/null)
    echo "$daemon_name is $status"
    INSTALL_DOT_COUNT=$((INSTALL_DOT_COUNT + 1))
    dots=$(printf "%${INSTALL_DOT_COUNT}s" | tr ' ' '.')
    safe_print green "\r\n[factory] reset${dots}"
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
  customctl enable switchd
  customctl enable fake-hwclock
  customctl enable wifi_init
  customctl enable wifi_logger
  customctl enable log-watchdog.timer
  customctl enable journald-snapshot.timer

  customctl disable wifi_checker@eth0
  customctl enable wifi_led@eth0
  customctl enable wifi_logger@eth0

  #customctl enable arping@mlan0
  customctl enable wifi_led@mlan0
  customctl enable wifi_logger@mlan0
  customctl enable wifi_checker@mlan0
  customctl enable wifi_bgscan@mlan0
  customctl enable wifi_roam@mlan0
  customctl enable wifi_event@mlan0
  #customctl enable wifi_capture@mlan0

  safe_cp /opt/wlan/mfg/bridge_init.conf /usr/local/mfg/bridge_init.conf
  safe_cp /opt/wlan/config/systemd/timesyncd.conf /etc/systemd/timesyncd.conf
  safe_cp /opt/wlan/config/wpa_supplicant/wpa_supplicant-mlan0.conf /etc/wpa_supplicant/wpa_supplicant-mlan0.conf
  safe_cp /opt/wlan/config/wpa_supplicant/wpa_supplicant-mlan1.conf /etc/wpa_supplicant/wpa_supplicant-mlan1.conf
  safe_cp /opt/wlan/config/systemd/network/20-mlan0.network /etc/systemd/network/20-mlan0.network
  safe_cp /opt/wlan/config/systemd/network/21-mlan1.network /etc/systemd/network/21-mlan1.network
  safe_cp /opt/wlan/config/systemd/network/22-eth0.network /etc/systemd/network/22-eth0.network
  safe_cp /opt/wlan/config/systemd/network/10-lo.network /etc/systemd/network/10-lo.network
  safe_cp /opt/wlan/config/systemd/network/20-mlan0.link /etc/systemd/network/20-mlan0.link
  safe_cp /opt/wlan/config/systemd/network/21-mlan1.link /etc/systemd/network/21-mlan1.link
  safe_cp /opt/wlan/config/systemd/network/22-eth0.link /etc/systemd/network/22-eth0.link
  # active .link는 JSON MAC에서 다시 생성되는 파생 상태다. 위 복사만으로는 MAC 오염이 다
  # 지워지지 않는다 — 백업(*.link.bak*)에서 되살아날 수 있고, safe_cp는 실패해도 로그만 남기고
  # 진행하며, 패키지 소유가 아닌 .link가 같은 인터페이스를 지목하면(파일명이 20-보다 앞서면
  # udev가 그쪽을 먼저 적용) 템플릿 복원이 통째로 가려진다. 파생 잔재 제거·외부 .link 삭제·
  # 후조건 검증을 wifi_link_reset.sh가 한 번에 처리한다.
  if [ -x /usr/local/scripts/wifi_link_reset.sh ]; then
      if ! /usr/local/scripts/wifi_link_reset.sh; then
          logger -p local0.err "[$tag:$LINENO] link reset incomplete; a .link may still force a MAC"
          safe_print red "\r\n[factory] WARNING: link reset incomplete (see syslog)"
      fi
  else
      logger -p local0.err "[$tag:$LINENO] missing wifi_link_reset.sh; stale .link MAC not cleaned"
  fi
  # 덮어쓰기 전, 지워선 안 되는 하드웨어/생산 설정(MOD_PARA·CAL_DATA_CFG·TXPWRLIMIT_PATH·
  # .mac.<iface>.base)을 떠 둔다. 되쓰기는 보드 감지 이후에 한다 — wifi_board_config.sh가
  # .global.MOD_PARA를 상수로 다시 쓰므로 그 전에 되쓰면 곧바로 덮인다.
  # 보존 목록과 판정 규칙은 wifi_conf_preserve.sh 참고(`wifi_conf_preserve.sh keys`).
  PRESERVE_SNAPSHOT=""
  if [ -x /usr/local/scripts/wifi_conf_preserve.sh ]; then
      PRESERVE_SNAPSHOT=$(mktemp /tmp/wifi_conf_preserve.XXXXXX 2>/dev/null)
      if [ -z "$PRESERVE_SNAPSHOT" ] \
         || ! /usr/local/scripts/wifi_conf_preserve.sh save "$PRESERVE_SNAPSHOT"; then
          rm -f -- "$PRESERVE_SNAPSHOT"
          PRESERVE_SNAPSHOT=""
          logger -p local0.err "[$tag:$LINENO] preserve snapshot failed; hardware keys fall back to template"
      fi
  else
      logger -p local0.err "[$tag:$LINENO] missing wifi_conf_preserve.sh; hardware keys fall back to template"
  fi

  # 활성 설정을 템플릿으로 되돌린다. postinst는 json_merge(기존 값 우선)로 쓰지만 여기서는
  # 통째로 덮어쓴다 — 사용자 런타임 설정(.global/.mlanN/.wbridge 등)을 지우는 것이 초기화의 목적.
  #
  # .mac.<iface>.target은 템플릿의 빈 문자열로 리셋된다. 의도된 동작이다 — target은
  # `wifi mac <iface> target <MAC>`으로 정하는 런타임 설정이고, 리셋 후에는 base(=유닛의
  # 기준 MAC)로 폴백한다(wifi_init.sh resolve_mac: dynamic → target → base).
  # base는 아래 preserve 단계에서 되살린다. 위에서 비워진 .link의 MACAddress는 다음 부팅에
  # resolve_mac → update_mac.sh 경로로 base에서 다시 채워진다(드라이버 로드 전에 수행).
  safe_cp /opt/wlan/config/wifi_init_conf.json /usr/local/etc/

  # 단, 보드 감지 결과는 사용자 설정이 아니라 하드웨어 사실이므로 반드시 되살린다.
  # 템플릿에는 .mcp.iio_device 키가 없고 .global.BOARD_TYPE은 고정값("imx93")이라, 위 복사만으로
  # 끝내면 postinst가 주입해 둔 값이 사라진다. 그러면 wifi_logger_mcp.sh가 iio:device0
  # fallback으로 떨어져(iMX93은 device1) ADC를 못 읽고 Invalid Voltage를 무한 로깅하며,
  # iMX8MM에서는 BOARD_TYPE이 imx93으로 뒤바뀌어 드라이버 선택까지 어긋난다.
  # 이 호출로 factory_reset 결과 == 신규 설치 결과(템플릿 + 보드 감지)가 된다.
  if [ -x /usr/local/scripts/wifi_board_config.sh ]; then
      /usr/local/scripts/wifi_board_config.sh /usr/local/etc/wifi_init_conf.json \
          || logger -p local0.err "[$tag:$LINENO] board config apply failed"
  else
      logger -p local0.err "[$tag:$LINENO] missing wifi_board_config.sh; board settings not restored"
  fi

  # 보드 감지가 끝난 뒤에 하드웨어/생산 설정을 되쓴다(위 snapshot 주석 참고).
  # 실패해도 초기화는 계속한다 — 템플릿 기본값으로 부팅은 되고, 원인은 로그에 남는다.
  if [ -n "$PRESERVE_SNAPSHOT" ]; then
      /usr/local/scripts/wifi_conf_preserve.sh apply "$PRESERVE_SNAPSHOT" \
          || logger -p local0.err "[$tag:$LINENO] preserved key restore failed; template values remain"
      rm -f -- "$PRESERVE_SNAPSHOT"
  fi

  # 후조건 검증. 헬퍼가 없든, 실패했든, 조용히 잘못 썼든 결과는 하나다 — .mcp.iio_device가
  # 없는 채로 reboot하면 이 버그가 그대로 재현된다. 여기서 확인해 최소한 원인이 드러나게 한다.
  # (인라인 jq 폴백을 두지 않는 이유: 헬퍼는 이 스크립트와 같은 .deb로 원자적으로 배포되고,
  #  헬퍼의 실패 원인(jq 부재·JSON 손상)은 인라인 jq도 똑같이 겪으므로 실익이 없다.
  #  감지 로직을 세 번째로 복제하면 드리프트만 늘어난다.)
  if command -v jq >/dev/null 2>&1; then
      _iio=$(jq -r '.mcp.iio_device // empty' /usr/local/etc/wifi_init_conf.json 2>/dev/null)
      if [ -z "$_iio" ]; then
          logger -p local0.err "[$tag:$LINENO] .mcp.iio_device missing after board config; wifi_logger_mcp will fall back and fail to read the ADC"
          safe_print red "\r\n[factory] WARNING: board config not applied (.mcp.iio_device missing)"
      else
          logger -p local0.info "[$tag:$LINENO] board config verified: iio_device=$_iio"
      fi
  fi

  # 공장 초기화 전 운영 설정이 다음 부팅의 복구 후보로 남지 않게 JSON 정상본 세대도
  # 초기화된 active로 교체한다. 실패하더라도 예전 정상본은 직접 제거해 부활을 막는다.
  if [ -x /usr/local/scripts/wifi_config_backup.sh ]; then
      if ! /usr/local/scripts/wifi_config_backup.sh reset; then
          rm -f -- "${WIFI_INIT_CONF_JSON}.bak" "${WIFI_INIT_CONF_JSON}.bak.1"
          sync "$(dirname "$WIFI_INIT_CONF_JSON")" 2>/dev/null || sync
          logger -p local0.emerg "[$tag:$LINENO] JSON backup reset failed; removed stale backup generations"
      fi
  else
      rm -f -- "${WIFI_INIT_CONF_JSON}.bak" "${WIFI_INIT_CONF_JSON}.bak.1"
      sync "$(dirname "$WIFI_INIT_CONF_JSON")" 2>/dev/null || sync
      logger -p local0.emerg "[$tag:$LINENO] missing wifi_config_backup.sh; removed stale JSON backups without reseed"
  fi
  if [ -x /usr/local/scripts/wifi_cal_backup.sh ]; then
      /usr/local/scripts/wifi_cal_backup.sh reset \
        || logger -p local0.err "[$tag:$LINENO] custom calibration backup reset failed"
  else
      logger -p local0.err "[$tag:$LINENO] missing wifi_cal_backup.sh; custom calibration backup artifacts may remain"
  fi

#find /var/log/cantops -mindepth 1 -maxdepth 1 ! -name journald -exec rm -rf {} +
:<<'END'
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
END

rm -rf /var/log/cantops/* 2>/dev/null \
    || logger -p local0.warn "[$tag:$LINENO] log cleanup failed: /var/log/cantops/*"

safe_sysfs_write none /sys/class/leds/status/trigger
safe_sysfs_write none /sys/class/leds/lan/trigger
safe_sysfs_write none /sys/class/leds/wlan/trigger

safe_sysfs_write 1 /sys/class/leds/status/brightness
safe_sysfs_write 1 /sys/class/leds/lan/brightness
safe_sysfs_write 1 /sys/class/leds/wlan/brightness


echo "factory reset finish"
safe_print cyan "[factory] reset finish"

# === 자동 reboot ===
# 변경된 .network / wpa_supplicant / wifi_init_conf.json / sysctl 등을 cold start로
# 일관되게 반영. 각 service의 restart를 일일이 호출하는 대신 reboot으로 일원화.
# 3초 sleep은 logger/print 출력이 콘솔/저널에 flush될 시간 확보.
logger -p local0.info "[$tag:$LINENO] rebooting in 3s to apply all changes"
safe_print cyan "[factory] rebooting in 3s..."
sleep 3
systemctl reboot
