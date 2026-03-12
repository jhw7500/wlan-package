#!/bin/bash
tag=$(basename "$0")
IFACE="eth0"
WIFI_INIT_CONF_JSON="/usr/local/etc/wifi_init_conf.json"

mac_addr=$(cat /sys/class/net/$IFACE/address)
if [[ "$mac_addr" =~ ^([a-fA-F0-9]{2}:){5}[a-fA-F0-9]{2}$ ]]; then
    logger -p local0.info "[$tag:$LINENO] [eth0] valid mac address : $mac_addr"
else
    logger -p local0.err "[$tag:$LINENO] [eth0] invalid mac address : $mac_addr"
fi

if [ -f "$WIFI_INIT_CONF_JSON" ] && command -v jq >/dev/null 2>&1; then
    jq --arg v "$mac_addr" '.mac.eth0.base = $v' "$WIFI_INIT_CONF_JSON" > "${WIFI_INIT_CONF_JSON}.tmp"
    mv "${WIFI_INIT_CONF_JSON}.tmp" "$WIFI_INIT_CONF_JSON"
    logger -p local0.info "[$tag:$LINENO] [eth0] Written MAC to JSON: $mac_addr"
else
    logger -p local0.warn "[$tag:$LINENO] [eth0] JSON update skipped (jq or config not found)"
fi
