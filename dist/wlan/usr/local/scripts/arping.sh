#!/bin/bash
IFACE=$1
CONF_FILE=""
tag=$(basename "$0")
FAILS=0
THRESHOLD=${THRESHOLD:-10}
COOLDOWN=${COOLDOWN:-10}
LOOPDELAY=${LOOPDELAY:-10}
F="local0"

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
    STATE=$(jq -r '.eth_stats.phy.link' "/var/log/cantops/json/eth0/link.json")
    if [[ "$STATE" != "up" ]]; then
        logger -p $F.info "[$tag:$LINENO] [$IFACE] not ready"
        exit 1
    fi
    TARGET_IP=$(cat /tmp/${IFACE}_client_ip)
elif [ "$IFACE" = "mlan0" ]; then
    if ! is_wpa_active || ! is_connected; then
        logger -p $F.info "[$tag:$LINENO] [$IFACE] not ready"
        exit 1
    fi
    #TARGET_IP=$(grep -E '^Gateway=' /etc/systemd/network/20-mlan0.network | head -n1 | cut -d= -f2)
    TARGET_IP=$(ip route | awk '/^default/ && /mlan0/ {print $3}')
elif [ "$IFACE" = "mlan1" ]; then
    if ! is_wpa_active || ! is_connected; then
        logger -p $F.info "[$tag:$LINENO] [$IFACE] not ready"
        exit 1
    fi
    #TARGET_IP=$(grep -E '^Gateway=' /etc/systemd/network/21-mlan1.network | head -n1 | cut -d= -f2)
    TARGET_IP=$(ip route | awk '/^default/ && /mlan1/ {print $3}')
else
    logger -p $F.info "[$tag:$LINENO] [$IFACE] interface is wrong : $IFACE"
    exit 1
fi

if ! is_ipv4 "$TARGET_IP"; then
  logger -p $F.err "[$tag:$LINENO] [$IFACE] invalid Target IP : '$TARGET_IP'"
  exit 1
fi

if ! is_plausible_host_ip "$TARGET_IP"; then
  logger -p $F.err "[$tag:$LINENO] [$IFACE] implausible Target IP : '$TARGET_IP'"
  exit 1
fi

#TARGET_IP="192.168.0.20"
logger -p $F.info "[$tag:$LINENO] [$IFACE] start : $TARGET_IP"

while true; do
  if arping -I "$IFACE" -c 1 -w 3 "$TARGET_IP" >/dev/null 2>&1; then
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
