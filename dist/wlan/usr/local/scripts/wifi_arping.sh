#!/bin/bash
IFACE=$1
CONF_FILE=""
tag=$(basename "$0")
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
    THRESHOLD=$(jq -r '.arping.threshold // 10' "$WIFI_INIT_CONF_JSON")
    COOLDOWN=$(jq -r '.arping.cooldown_sec // 10' "$WIFI_INIT_CONF_JSON")
    LOOPDELAY=$(jq -r '.arping.loop_delay_sec // 10' "$WIFI_INIT_CONF_JSON")
    ARPING_TIMEOUT=$(jq -r '.arping.timeout_sec // 3' "$WIFI_INIT_CONF_JSON")
fi

# Environment variables override JSON (하위 호환)
THRESHOLD=${THRESHOLD:-10}
COOLDOWN=${COOLDOWN:-10}
LOOPDELAY=${LOOPDELAY:-10}

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
    while true; do
        STATE=$(jq -r '.eth_stats.phy.link' "/var/log/cantops/json/eth0/link.json" 2>/dev/null || echo "down")
        if [[ "$STATE" == "up" ]]; then
            break
        fi
        logger -p $F.info "[$tag:$LINENO] [$IFACE] not ready(link down), waiting..."
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
