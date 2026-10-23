#!/bin/bash
# WPA ExecCondition: MAC 초기화 실패/진행 중에는 시작하지 않는다.
set -eu
SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
# shellcheck source=./mac_link_lib.sh
. "$SCRIPT_DIR/mac_link_lib.sh"
tag=wlan_mac_ready
iface="${1:-}"
case "$iface" in
    mlan0) link=20-mlan0.link ;;
    mlan1) link=21-mlan1.link ;;
    *) exit 1 ;;
esac
if [ -e /run/wifi/mac-contract-blocked ]; then
    logger -p local0.warn "[$tag:$LINENO] [$iface] MAC initialization blocked; leave supplicant stopped" || true
    exit 1
fi
status=$(systemctl show wifi_init.service -p ExecMainStatus --value) || exit 1
[ "$status" != 78 ] || exit 1
link="${SYSTEMD_NETWORK_DIR:-/etc/systemd/network}/$link"
[ -e "$link" ] || exit 0
expected=$(mac_read_link_address "$link") || exit 1
[ -n "$expected" ] || exit 0
mac_verify_live_address "$iface" "$expected"
