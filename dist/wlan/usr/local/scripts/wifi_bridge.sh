#!/bin/bash
set -euo pipefail

tag=$(basename "$0")
IFACE=$1
CONF_FILE=""
# TODO: Read wired interface from config.json instead of hardcoding eth0
WIRED_IF="eth0"

logger -p local0.info "[$tag:$LINENO] [$IFACE] wbridge start"

if [[ "$IFACE" != "mlan0" && "$IFACE" != "mlan1" ]]; then
    logger -p local0.emerg "[$tag:$LINENO] [$IFACE] interface is wrong!!"
    exit 0
fi

both_up() {
    ip link show "$WIRED_IF" | grep -q "state UP" || return 1
    ip link show "$IFACE" | grep -q "state UP" || return 1
    cat /sys/class/net/"$WIRED_IF"/carrier 2>/dev/null | grep -q 1 || return 1
    cat /sys/class/net/"$IFACE"/carrier 2>/dev/null | grep -q 1 || return 1
}

getMac() {
    if [ -e /sys/class/net/$IFACE/address ]; then
        mac_addr=$(cat /sys/class/net/$IFACE/address)
        logger -p local0.info "[$tag:$LINENO] [$IFACE] MAC Address: $mac_addr"
        echo "$mac_addr"
    else
        logger -p local0.crit "[$tag:$LINENO] [$IFACE] Interface is not found"
        echo ""
    fi
}

for _ in $(seq 1 200); do
    if both_up; then break; fi
    sleep 0.2
done

if [[ "$IFACE" == "mlan0" ]]; then
    CONF_FILE=/etc/systemd/network/20-mlan0.network
fi
if [[ "$IFACE" == "mlan1" ]]; then
    CONF_FILE=/etc/systemd/network/21-mlan1.network
fi

if [ ! -f "$CONF_FILE" ]; then
    echo "Config file not found: $CONF_FILE"
    exit 1
fi

# Use the new wbridge binary via the wifi-wbridge symlink
# --ip-filter: Skip re-injection for bridge's local IPs
# --no-debug: Reduce log noise in production
exec /usr/local/bin/wifi-wbridge --ip-filter --no-debug "$WIRED_IF" "$IFACE"
