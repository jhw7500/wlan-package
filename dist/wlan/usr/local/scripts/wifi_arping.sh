#!/bin/bash
IFACE="${1:-}"
CONF_FILE=""
tag=$(basename "$0")
if [ -z "$IFACE" ]; then
    echo "usage: $0 <iface>" >&2
    logger -p local0.err "[$tag:$LINENO] usage: $0 <iface>"
    exit 2
fi
FAILS=0
F="local0"

# Defaults
THRESHOLD=10
COOLDOWN=10
LOOPDELAY=10
ARPING_TIMEOUT=3

# Load from JSON config
WIFI_INIT_CONF_JSON="/usr/local/etc/wifi_init_conf.json"
if [ -f "$WIFI_INIT_CONF_JSON" ] && command -v jq >/dev/null 2>&1; then
    THRESHOLD=$(jq -r ".${IFACE}.arping.threshold // 10" "$WIFI_INIT_CONF_JSON")
    COOLDOWN=$(jq -r ".${IFACE}.arping.cooldown_sec // 10" "$WIFI_INIT_CONF_JSON")
    LOOPDELAY=$(jq -r ".${IFACE}.arping.loop_delay_sec // 10" "$WIFI_INIT_CONF_JSON")
    ARPING_TIMEOUT=$(jq -r ".${IFACE}.arping.timeout_sec // 3" "$WIFI_INIT_CONF_JSON")
fi

# Environment variables override JSON (하위 호환)
THRESHOLD=${THRESHOLD:-10}
COOLDOWN=${COOLDOWN:-10}
LOOPDELAY=${LOOPDELAY:-10}

# 유선 링크 판정. `link.json` 은 wifi_logger_link@<iface> 이 만드는 파생물이라 부팅 직후엔
# 아직 없고, 그 로거가 꺼진 구성에서는 영영 없다. 파일 부재를 "링크 다운" 으로 뭉뚱그리면
# 케이블이 꽂혀 있어도 기다리게 된다(실측 2026-08-31: arping 과 로거가 같은 초에 떠서 부재를
# down 으로 읽음, 그때 carrier 는 1). 그래서 유효한 link.json 값이 있으면 그것을 쓰고, 없거나
# 못 읽으면 커널 carrier 로 내려간다. 둘 다 없으면 down 이 아니라 unknown 이다 — 모르는 것을
# 아는 것처럼 기록하지 않는다.
eth_link_state() {   # $1 iface, $2 link.json 경로 -> up | down | unknown
    local iface="$1" json="$2" value=""
    if [ -r "$json" ] && command -v jq >/dev/null 2>&1; then
        value=$(jq -r '.eth_stats.phy.link // empty' "$json" 2>/dev/null) || value=""
        case "$value" in
            up)   printf 'up\n';   return 0 ;;
            down) printf 'down\n'; return 0 ;;
        esac
    fi
    value=""
    if [ -r "${NET_SYSFS_ROOT:-/sys/class/net}/$iface/carrier" ]; then
        # admin-down 인터페이스의 carrier 는 EINVAL 이라 읽기 자체가 실패한다.
        IFS= read -r value < "${NET_SYSFS_ROOT:-/sys/class/net}/$iface/carrier" 2>/dev/null \
            || value=""
    fi
    case "$value" in
        1) printf 'up\n' ;;
        0) printf 'down\n' ;;
        *) printf 'unknown\n' ;;
    esac
    return 0
}

get_target_ip() {
    case "$IFACE" in
        eth0)
            cat "/tmp/${IFACE}_client_ip" 2>/dev/null || true
            ;;
        mlan0|mlan1)
            ip route | awk -v dev="$IFACE" '$1=="default" && $0 ~ dev {print $3; exit}'
            ;;
        *)
            echo ""
            ;;
    esac
}

get_state() {
    wpa_cli -i "$IFACE" status | grep "^wpa_state=" | cut -d= -f2
}

is_connected() {
    local state
    state=$(get_state)
    [[ "$state" == "COMPLETED" ]]
}

is_wpa_active() {
    systemctl is-active --quiet "wpa_supplicant@${IFACE}.service"
}

is_ipv4() {
  # 공백/개행/CR 제거 (윈도우 개행 방지)
  ip="$(printf '%s' "$1" | tr -d $'\r' | tr -d '[:space:]')"
  # 점 3개 확인
  case "$ip" in *.*.*.*) : ;; *) return 1;; esac
  echo "$ip" | awk -F. '
    NF!=4 {exit 1}
    {for(i=1;i<=4;i++){
      if ($i !~ /^[0-9]+$/) exit 1
      if ($i < 0 || $i > 255) exit 1
    }}
  ' >/dev/null 2>&1
}

is_plausible_host_ip() {
  ip="$1"
  case "$ip" in
    0.*|127.*|224.*|225.*|226.*|227.*|228.*|229.*|230.*|231.*|232.*|233.*|234.*|235.*|236.*|237.*|238.*|239.*|255.255.255.255|169.254.*) return 1;;
  esac
  return 0
}

if [ "$IFACE" = "eth0" ]; then
    ETH_LINK_JSON="${ETH_LINK_JSON:-/var/log/cantops/json/${IFACE}/link.json}"
    link_logged=""
    while true; do
        STATE=$(eth_link_state "$IFACE" "$ETH_LINK_JSON")
        if [ "$STATE" = "up" ]; then
            break
        fi
        # 상태가 바뀔 때만 남긴다 — 매 주기 같은 줄을 쌓으면 왜 기다리는지가 묻힌다.
        if [ "$STATE" != "$link_logged" ]; then
            if [ "$STATE" = "unknown" ]; then
                logger -p $F.warning "[$tag:$LINENO] [$IFACE] link state unknown (no $ETH_LINK_JSON, no carrier), waiting..."
            else
                logger -p $F.info "[$tag:$LINENO] [$IFACE] not ready(link down), waiting..."
            fi
            link_logged="$STATE"
        fi
        sleep "$LOOPDELAY"
    done
    TARGET_IP=$(get_target_ip)
elif [ "$IFACE" = "mlan0" ] || [ "$IFACE" = "mlan1" ]; then
    wait_logged=0
    while true; do
        if is_wpa_active && is_connected; then
            break
        fi
        if [ "$wait_logged" -eq 0 ]; then
            logger -p $F.info "[$tag:$LINENO] [$IFACE] waiting for wpa_supplicant and connection..."
            wait_logged=1
        fi
        sleep "$LOOPDELAY"
    done
    TARGET_IP=$(get_target_ip)
else
    logger -p $F.info "[$tag:$LINENO] [$IFACE] interface is wrong : $IFACE"
    exit 0
fi

wait_logged=0

while true; do
  TARGET_IP=$(get_target_ip)
  if is_ipv4 "$TARGET_IP" && is_plausible_host_ip "$TARGET_IP"; then
    break
  fi
  if [ "$wait_logged" -eq 0 ]; then
    logger -p $F.warning "[$tag:$LINENO] [$IFACE] gateway/target not ready; waiting... (target='$TARGET_IP')"
    wait_logged=1
  fi
  sleep "$LOOPDELAY"
done

#TARGET_IP="192.168.0.20"
logger -p $F.info "[$tag:$LINENO] [$IFACE] start : $TARGET_IP"

while true; do
  if arping -I "$IFACE" -c 1 -w "$ARPING_TIMEOUT" "$TARGET_IP" >/dev/null 2>&1; then
    FAILS=0
    logger -p $F.info "[$tag:$LINENO] [$IFACE] ARP OK: $TARGET_IP"
    sleep "$LOOPDELAY"
  else
    FAILS=$((FAILS+1))
    
    logger -p $F.warning "[$tag:$LINENO] [$IFACE] ARP NO-REPLY ($FAILS/$THRESHOLD): $TARGET_IP"
    if [ "$FAILS" -ge "$THRESHOLD" ]; then
      if [ "$IFACE" = "eth0" ]; then
        logger -p $F.err "[$tag:$LINENO] [$IFACE] threshold reached  -> wired_get_mac_ip.py"
        python3 /usr/local/logger/wired_mac_ip_get.py
        TARGET_IP=$(cat /tmp/${IFACE}_client_ip)
        #exit 1
      else
        logger -p $F.err "[$tag:$LINENO] [$IFACE] threshold reached  -> restart systemd-networkd?"
        #systemctl restart systemd-networkd
      fi
      FAILS=0
      sleep "$COOLDOWN"
    else
      sleep "$LOOPDELAY"
    fi
  fi
done
