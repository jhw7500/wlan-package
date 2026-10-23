#!/bin/bash
tag=$(basename "$0")
WIFI_INIT_CONF_JSON="/usr/local/etc/wifi_init_conf.json"
WIFI_INIT_CONF_TEMPLATE="/opt/wlan/config/wifi_init_conf.json"
FACTORY_RESET_LIB="/usr/local/scripts/wifi_factory_reset_lib.sh"
FACTORY_STAGED_CONFIG="/usr/local/etc/.wifi_init_conf.factory.$$"
PRESERVE_SNAPSHOT=""
critical_failures=0
FACTORY_TOPOLOGY=""
FACTORY_TOPOLOGY_SNAPSHOT=""

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

# 공장 초기화 오류: status 점등, LAN/WLAN 소등. LED 실패는 reset 실패를 가리지 않는다.
factory_reset_led_error() {
    safe_sysfs_write none /sys/class/leds/status/trigger
    safe_sysfs_write none /sys/class/leds/lan/trigger
    safe_sysfs_write none /sys/class/leds/wlan/trigger

    safe_sysfs_write 1 /sys/class/leds/status/brightness
    safe_sysfs_write 0 /sys/class/leds/lan/brightness
    safe_sysfs_write 0 /sys/class/leds/wlan/brightness
}

# 안전한 print.py wrapper — 콘솔 컬러 출력. 도구 호출 실패 시 logger.warn.
safe_print() {
    if ! /usr/local/logger/print.py "$@" 2>/dev/null; then
        logger -p local0.warn "[$tag:$LINENO] print.py failed: $*"
    fi
}

secure_wpa_conf() {
    local conf="$1"
    [ -f "$conf" ] || return 1
    chown root:root "$conf" 2>/dev/null || return 1
    chmod 0600 "$conf" || return 1
    sync "$conf" 2>/dev/null || sync
}

# 파괴적 변경 전에 package template, 필수 helper, systemd unit, 같은 파일시스템 stage
# 가능 여부를 모두 확인한다. 실패 시 기존 active 설정과 서비스 상태를 건드리지 않는다.
if [ ! -r "$FACTORY_RESET_LIB" ]; then
    logger -p local0.emerg "[$tag:$LINENO] missing factory reset library: $FACTORY_RESET_LIB"
    factory_reset_led_error
    exit 1
fi
# shellcheck source=./wifi_factory_reset_lib.sh
. "$FACTORY_RESET_LIB"

factory_reset_cleanup() {
    local status="$1"
    trap - EXIT
    if [ "$FACTORY_TRANSACTION_ACTIVE" -eq 1 ]; then
        status=1
        if ! factory_rollback_required_payloads; then
            logger -p local0.emerg "[$tag:$LINENO] CRITICAL: interrupted factory transaction rollback incomplete"
        fi
    fi
    rm -f -- "$FACTORY_STAGED_CONFIG" "$PRESERVE_SNAPSHOT"
    exit "$status"
}

# selector는 payload preflight가 destination 디렉터리를 정규화하기 전에 한 번 snapshot한다.
# 손상·권한 오류·symlink는 어떤 active 파일이나 서비스도 바꾸기 전에 reset을 중단한다.
if ! FACTORY_TOPOLOGY_SNAPSHOT=$(factory_snapshot_topology) \
   || ! FACTORY_TOPOLOGY=$(jq -er '.topology' <<< "$FACTORY_TOPOLOGY_SNAPSHOT"); then
    logger -p local0.emerg "[$tag:$LINENO] factory topology selection failed; active state unchanged"
    safe_print red "[factory] topology selection failed; reset aborted"
    factory_reset_led_error
    exit 1
fi

# Factory Reset 성공의 필수 복구 계약. wifi_init의 self-healing 대상은 active와 .bak을
# 같은 factory source로 함께 재시드해 reset 이전 SSID/IP/FW 설정이 부활하지 않게 한다.
# 그 밖의 OS 편의 설정과 .link는 아래 best-effort 복사 후 wifi_link_reset의 MAC 후조건으로
# 처리한다.
FACTORY_REQUIRED_PAYLOADS=(
    "/opt/wlan/config/wlan/txpwrlimit_cfg_9098.conf|/lib/firmware/cts/txpwrlimit_cfg_9098.conf|0644"
    "/opt/wlan/config/wlan/txpwrlimit_cfg_9098.conf|/lib/firmware/cts/txpwrlimit_cfg_9098.conf.bak|0644"
    "/opt/wlan/config/wlan/wifi_mod_para.conf|/lib/firmware/cts/wifi_mod_para.conf|0644"
    "/opt/wlan/config/wlan/wifi_mod_para.conf|/lib/firmware/cts/wifi_mod_para.conf.bak|0644"
    "/opt/wlan/config/wpa_supplicant/wpa_supplicant@.service|/lib/systemd/system/wpa_supplicant@.service|0644"
    "/opt/wlan/config/wpa_supplicant/wpa_supplicant-mlan0.conf|/etc/wpa_supplicant/wpa_supplicant-mlan0.conf|0600"
    "/opt/wlan/config/wpa_supplicant/wpa_supplicant-mlan0.conf|/etc/wpa_supplicant/wpa_supplicant-mlan0.conf.bak|0600"
    "/opt/wlan/config/wpa_supplicant/wpa_supplicant-mlan1.conf|/etc/wpa_supplicant/wpa_supplicant-mlan1.conf|0600"
    "/opt/wlan/config/wpa_supplicant/wpa_supplicant-mlan1.conf|/etc/wpa_supplicant/wpa_supplicant-mlan1.conf.bak|0600"
    "/opt/wlan/config/systemd/network/20-mlan0.network|/etc/systemd/network/20-mlan0.network|0644"
    "/opt/wlan/config/systemd/network/20-mlan0.network|/etc/systemd/network/20-mlan0.network.bak|0644"
    "/opt/wlan/config/systemd/network/21-mlan1.network|/etc/systemd/network/21-mlan1.network|0644"
    "/opt/wlan/config/systemd/network/21-mlan1.network|/etc/systemd/network/21-mlan1.network.bak|0644"
    "/opt/wlan/config/systemd/network/22-eth0.network|/etc/systemd/network/22-eth0.network|0644"
    "/opt/wlan/config/systemd/network/22-eth0.network|/etc/systemd/network/22-eth0.network.bak|0644"
)

if ! factory_preflight "$WIFI_INIT_CONF_TEMPLATE" "$(dirname "$WIFI_INIT_CONF_JSON")" \
   || ! factory_preflight_required_payloads "${FACTORY_REQUIRED_PAYLOADS[@]}"; then
    logger -p local0.emerg "[$tag:$LINENO] factory reset preflight failed; active state unchanged"
    safe_print red "[factory] preflight failed; reset aborted"
    factory_reset_led_error
    exit 1
fi

# 생산값 snapshot은 선택 사항이다. 실패하면 검증된 template+board 기본값으로 진행한다.
if [ -x /usr/local/scripts/wifi_conf_preserve.sh ]; then
    PRESERVE_SNAPSHOT=$(mktemp /tmp/wifi_conf_preserve.XXXXXX 2>/dev/null || true)
    if [ -z "$PRESERVE_SNAPSHOT" ] \
       || ! /usr/local/scripts/wifi_conf_preserve.sh save "$PRESERVE_SNAPSHOT"; then
        rm -f -- "$PRESERVE_SNAPSHOT"
        PRESERVE_SNAPSHOT=""
        logger -p local0.warn "[$tag:$LINENO] preserve snapshot failed; using template hardware defaults"
    fi
fi

if ! factory_stage_config "$WIFI_INIT_CONF_TEMPLATE" "$FACTORY_STAGED_CONFIG" \
    "$PRESERVE_SNAPSHOT" "$FACTORY_TOPOLOGY"; then
    rm -f -- "$FACTORY_STAGED_CONFIG" "$PRESERVE_SNAPSHOT"
    logger -p local0.emerg "[$tag:$LINENO] factory config staging failed; active state unchanged"
    safe_print red "[factory] config staging failed; reset aborted"
    factory_reset_led_error
    exit 1
fi
rm -f -- "$PRESERVE_SNAPSHOT"
PRESERVE_SNAPSHOT=""
trap 'factory_reset_cleanup $?' EXIT

# JSON active/정상본 세대와 필수 network/WPA/FW payload를 하나의 rollback 단위로 묶는다.
# rollback copy는 서비스·링크·파일 후조건이 모두 끝날 때까지 유지한다.
# 이 트랜잭션 밖의 systemd enable/mask, calibration helper, 로그 정리는 원복하지 않는다.
# 그런 후속 단계가 실패하면 파일은 복원하고 reboot을 막아 수동 진단 상태로 남긴다.
FACTORY_TRANSACTION_PAYLOADS=(
    "${FACTORY_REQUIRED_PAYLOADS[@]}"
    "$FACTORY_STAGED_CONFIG|$WIFI_INIT_CONF_JSON|0644"
    "$FACTORY_STAGED_CONFIG|${WIFI_INIT_CONF_JSON}.bak|0644"
    "$FACTORY_STAGED_CONFIG|${WIFI_INIT_CONF_JSON}.bak.1|0644"
)
if ! factory_begin_required_payloads "${FACTORY_TRANSACTION_PAYLOADS[@]}"; then
    logger -p local0.emerg "[$tag:$LINENO] required factory payload restore failed; reset aborted"
    safe_print red "[factory] required payload restore failed; reset aborted"
    factory_reset_led_error
    exit 1
fi

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
safe_cp /opt/wlan/config/crontab /etc/
#cp /opt/wlan/firmware/* /lib/firmware/nxp/
#cp /opt/wlan/driver/* /lib/modules/$KERNEL_VERSION/updates/
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
    install_pct=$((INSTALL_DOT_COUNT * 100 / INSTALL_UNIT_TOTAL))
    [ "$install_pct" -le 100 ] || install_pct=100
    dots=$(printf "%${INSTALL_DOT_COUNT}s" | tr ' ' '.')
    # 진행 표시는 유닛마다 줄을 쌓을 게 아니라 같은 줄을 덮어써야 읽힌다.
    # 끝의 \r 이 print.py 에 "줄을 닫지 말고 컬럼 0 으로 돌아가라"는 표시다.
    # 덮어쓴 자리에 이전 글자가 남지 않으려면 줄 길이가 단조 증가해야 하므로
    # 숫자는 고정폭으로 찍고, 점은 어차피 매번 하나씩 늘어난다.
    safe_print green "$(printf '[factory] reset %3d%% (%2d/%2d) %s' \
        "$install_pct" "$INSTALL_DOT_COUNT" "$INSTALL_UNIT_TOTAL" "$dots")\r"
}

# 진행률의 분모. 아래 customctl 호출 줄을 세어 구하므로, 유닛을 늘리거나 주석
# 처리해도 따로 고칠 곳이 없다. 세지 못하면 0 나눗셈이 되므로 1 로 폴백하되,
# 진행률만 무의미해질 뿐 factory reset 자체는 계속 진행해야 하므로 경고만 남긴다.
INSTALL_UNIT_TOTAL=$(grep -cE '^[[:space:]]*customctl[[:space:]]' "$0" 2>/dev/null)
case "$INSTALL_UNIT_TOTAL" in
    ''|*[!0-9]*|0)
        logger -p local0.warn "[$tag:$LINENO] cannot count customctl units in $0; progress percent unreliable"
        INSTALL_UNIT_TOTAL=1
        ;;
esac

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
  customctl enable wifi_logger
  customctl enable log-watchdog.timer
  customctl enable journald-snapshot.timer

  customctl disable wifi_checker@eth0
  customctl enable wifi_led@eth0
  customctl enable wifi_logger@eth0

  #customctl enable arping@mlan0
  customctl enable wifi_led@mlan0
  #customctl enable wifi_capture@mlan0

  # 위 진행 표시는 CR 로 같은 줄을 덮어써 왔다. 여기서 한 번 줄을 닫아, 이후
  # 콘솔 출력이 진행 줄 뒤에 이어붙지 않게 한다. 직전 갱신보다 짧으면 그 꼬리가
  # 화면에 남으므로, 같은 점 문자열을 유지한 채 " done" 만 덧붙인다.
  safe_print green "$(printf '[factory] reset %3d%% (%2d/%2d) %s done' \
      "${install_pct:-0}" "${INSTALL_DOT_COUNT:-0}" "${INSTALL_UNIT_TOTAL:-0}" "${dots:-}")"

  safe_cp /opt/wlan/mfg/bridge_init.conf /usr/local/mfg/bridge_init.conf
  safe_cp /opt/wlan/config/systemd/timesyncd.conf /etc/systemd/timesyncd.conf
  secure_wpa_conf /etc/wpa_supplicant/wpa_supplicant-mlan0.conf || critical_failures=$((critical_failures + 1))
  secure_wpa_conf /etc/wpa_supplicant/wpa_supplicant-mlan1.conf || critical_failures=$((critical_failures + 1))
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
          critical_failures=$((critical_failures + 1))
          logger -p local0.err "[$tag:$LINENO] link reset incomplete; a .link may still force a MAC"
          safe_print red "\r\n[factory] WARNING: link reset incomplete (see syslog)"
      fi
  else
      critical_failures=$((critical_failures + 1))
      logger -p local0.err "[$tag:$LINENO] missing wifi_link_reset.sh; stale .link MAC not cleaned"
  fi
  # JSON은 위 트랜잭션에서 이미 active와 .bak 두 세대에 같은 factory stage로 승격됐다.
  # 아래 교정 작업과 후조건이 실패하면 필수 payload와 함께 이전 세대로 복원한다.
  if [ -x "$FACTORY_CAL_BACKUP_SH" ]; then
      if ! "$FACTORY_CAL_BACKUP_SH" reset; then
          critical_failures=$((critical_failures + 1))
          logger -p local0.emerg "[$tag:$LINENO] selected production calibration reset/protection failed"
      fi
  else
      critical_failures=$((critical_failures + 1))
      logger -p local0.emerg "[$tag:$LINENO] missing wifi_cal_backup.sh; cannot protect selected production calibration"
  fi

  if ! factory_verify_required_payloads "${FACTORY_TRANSACTION_PAYLOADS[@]}"; then
      critical_failures=$((critical_failures + 1))
      logger -p local0.emerg "[$tag:$LINENO] required factory payload postcondition failed"
  fi

  if ! factory_restore_service_state "$WIFI_INIT_CONF_JSON"; then
      critical_failures=$((critical_failures + 1))
      logger -p local0.emerg "[$tag:$LINENO] factory service state restore failed"
  fi
  if ! factory_verify_postconditions "$WIFI_INIT_CONF_JSON"; then
      critical_failures=$((critical_failures + 1))
      logger -p local0.emerg "[$tag:$LINENO] factory reset postcondition verification failed"
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

if [ "$critical_failures" -eq 0 ]; then
    if ! factory_commit_required_payloads "$FACTORY_TOPOLOGY_SNAPSHOT"; then
        critical_failures=$((critical_failures + 1))
        logger -p local0.emerg "[$tag:$LINENO] factory transaction finalization failed"
    fi
fi

# OPC ChangeIp 의 ESSID 복원 표식(wlan-opc#161)은 방금 공장값으로 되돌린 mlan0 conf 에 대한 것이라
# 함께 지운다. 복원은 커밋값이 일치할 때만 일어나므로 안전 문제는 아니고, 다음 부팅의 opcd -R 이
# "marker dropped" 로그를 남기지 않게 하는 위생이다(#362). 실패는 경고만 남기고 진행한다.
if [ "$critical_failures" -eq 0 ] && [ -e /usr/local/opc/etc/changeip_essid.restore ]; then
    if rm -f /usr/local/opc/etc/changeip_essid.restore 2>/dev/null; then
        logger -p local0.info "[$tag:$LINENO] removed the ChangeIp ESSID restore marker"
    else
        logger -p local0.warn "[$tag:$LINENO] could not remove /usr/local/opc/etc/changeip_essid.restore"
    fi
fi

# OPC 저장값(비밀번호·IP 주소 리스트·무선 설정)도 초기값으로 되돌린다(#383). opcd 는 멈추지 않는다 —
# 재부팅 종료 절차에서 Reset Cause 0x40 을 통지해야 한다. 재부팅이 억제되는 실패(롤백) 때는 지우지 않는다.
# 받아들인 한계: 여기서부터 재부팅 종료 절차가 opcd 를 멈출 때까지(아래 3초 대기 포함) 들어온 저장 요청과
# 종료 때 마저 쓰는 대기 저장은 파일을 다시 만들 수 있다. SetIpConfigList 는 메모리의 초기화 전 목록에
# 덧붙여 쓰므로 이전 목록 전체가 되살아난다. RESET 버튼 10초 조작 직후 몇 초 안의 호스트 쓰기라 드물다.
if [ "$critical_failures" -eq 0 ]; then
    factory_reset_clear_opc_state /usr/local/opc/etc
fi

if [ "$critical_failures" -ne 0 ]; then
    if [ "$FACTORY_TRANSACTION_ACTIVE" -eq 1 ] \
       && ! factory_rollback_required_payloads; then
        critical_failures=$((critical_failures + 1))
        logger -p local0.emerg "[$tag:$LINENO] CRITICAL: factory topology rollback incomplete"
    fi
    rm -f -- "$FACTORY_STAGED_CONFIG"
    logger -p local0.emerg "[$tag:$LINENO] factory reset incomplete: critical_failures=$critical_failures; reboot inhibited"
    safe_print red "[factory] reset incomplete; reboot inhibited (see syslog)"
    factory_reset_led_error
    exit 1
fi
rm -f -- "$FACTORY_STAGED_CONFIG"

# 필수 검증이 모두 통과한 뒤에만 세 LED를 점등한다.
safe_sysfs_write none /sys/class/leds/status/trigger
safe_sysfs_write none /sys/class/leds/lan/trigger
safe_sysfs_write none /sys/class/leds/wlan/trigger

safe_sysfs_write 1 /sys/class/leds/status/brightness
safe_sysfs_write 1 /sys/class/leds/lan/brightness
safe_sysfs_write 1 /sys/class/leds/wlan/brightness

echo "factory reset finish"
safe_print cyan "[factory] reset finish"
trap - EXIT
rm -f -- "$FACTORY_STAGED_CONFIG" "$PRESERVE_SNAPSHOT"

# === 자동 reboot ===
# 변경된 .network / wpa_supplicant / wifi_init_conf.json / sysctl 등을 cold start로
# 일관되게 반영. 각 service의 restart를 일일이 호출하는 대신 reboot으로 일원화.
# 3초 sleep은 logger/print 출력이 콘솔/저널에 flush될 시간 확보.
logger -p local0.info "[$tag:$LINENO] rebooting in 3s to apply all changes"
safe_print cyan "[factory] rebooting in 3s..."
# 공장 초기화는 wlan_reboot_policy.sh 를 거치지 않는 유일한 재부팅이라 여기서 직접
# opcd 의 Reset Cause(0x40 FACTORY_RESET, /run/opc/reset_cause)를 남긴다. 실패해도
# 재부팅은 진행한다(opcd 는 0x0002 로 통지). (#304)
if mkdir -p /run/opc 2>/dev/null \
   && printf '0x40\n' 2>/dev/null > /run/opc/reset_cause; then
    :
else
    logger -p local0.warning "[$tag:$LINENO] reset cause 0x40 not recorded (/run/opc unwritable)"
fi
sleep 3
if ! systemctl reboot; then
    logger -p local0.emerg "[$tag:$LINENO] factory reset reboot command failed"
    # 재부팅이 시작되지 않았으므로 0x40 원인 파일을 지운다. 남기면 뒤의 다른 재부팅이 0x40 으로
    # 통지된다(#380). OPC 저장값은 이미 지웠으므로 opcd 는 다음에 재시작할 때 초기값으로 시작한다.
    rm -f -- /run/opc/reset_cause 2>/dev/null \
        || logger -p local0.warn "[$tag:$LINENO] could not remove /run/opc/reset_cause after the failed reboot"
    logger -p local0.warn "[$tag:$LINENO] OPC stored values were cleared; opcd keeps the previous values in memory until it restarts"
    safe_print red "[factory] reboot failed (see syslog)"
    factory_reset_led_error
    exit 1
fi
