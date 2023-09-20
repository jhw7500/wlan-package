#!/bin/sh
set -eu
tag=$(basename "$0")

IFACE="${1:-}"
[ -n "$IFACE" ] || { logger -p local0.err "[$tag:$LINENO] empty iface"; exit 2; }

case "$IFACE" in
  eth0)  LED_PATH="/sys/class/leds/lan"  ;;
  mlan0|mlan1) LED_PATH="/sys/class/leds/wlan" ;;
  *) logger -p local0.err "[$tag:$LINENO] unknown iface: $IFACE"; exit 1 ;;
esac

if [ ! -d "$LED_PATH" ]; then
  logger -p local0.warning "[$tag:$LINENO] [$IFACE] $LED_PATH not ready; retry..."
  for i in $(seq 1 10); do
    [ -d "$LED_PATH" ] && break
    sleep 0.2
  done
fi

if [ -d "$LED_PATH" ]; then
  # 트리거를 netdev로 강제 (있을 때만)
  if [ -w "$LED_PATH/trigger" ] && grep -q '\[none\]\|netdev' "$LED_PATH/trigger"; then
    echo netdev > "$LED_PATH/trigger" 2>/dev/null || true
  fi

  # netdev 트리거 속성 적용
  [ -w "$LED_PATH/device_name" ] && echo "$IFACE" > "$LED_PATH/device_name" || true
  [ -w "$LED_PATH/link" ]        && echo 1 > "$LED_PATH/link" || true
  [ -w "$LED_PATH/tx" ]          && echo 1 > "$LED_PATH/tx"   || true
  [ -w "$LED_PATH/rx" ]          && echo 1 > "$LED_PATH/rx"   || true
  logger -p local0.info "[$tag:$LINENO] [$IFACE] $LED_PATH initialized"
else
  logger -p local0.err "[$tag:$LINENO] [$IFACE] $LED_PATH not found"
  exit 3
fi
