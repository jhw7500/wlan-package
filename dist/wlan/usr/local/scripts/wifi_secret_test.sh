#!/bin/bash
set -uo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
WIFI="$SCRIPT_DIR/wifi.sh"
POSTINST="$SCRIPT_DIR/../../../DEBIAN/postinst"
FACTORY="$SCRIPT_DIR/factory_reset.sh"
TEMPLATE_DIR="$SCRIPT_DIR/../../../opt/wlan/config/wpa_supplicant"
PASS=0 FAIL=0

ok() { PASS=$((PASS + 1)); echo "PASS: $1"; }
bad() { FAIL=$((FAIL + 1)); echo "FAIL: $1" >&2; }
contains() { grep -Fq -- "$2" "$1" && ok "$3" || bad "$3"; }
not_contains() { grep -Fq -- "$2" "$1" && bad "$3" || ok "$3"; }

for conf in "$TEMPLATE_DIR"/wpa_supplicant-mlan*.conf; do
    mode=$(stat -c %a "$conf")
    if (( (8#$mode & 07111) == 0 )); then
        ok "template source is non-executable: $(basename "$conf")"
    else
        bad "template source is executable or set-id: $conf (mode=$mode)"
    fi
done

contains "$WIFI" 'safe_install_sync()' "wifi has mode-aware atomic install"
not_contains "$WIFI" 'safe_install_0644_sync' "misleading 0644 helper removed"
contains "$WIFI" 'psk_display="********"' "wifi info masks PSK"
not_contains "$WIFI" 'psk=${psk:-N/A}' "wifi info never prints raw PSK"
contains "$POSTINST" 'secure_wpa_conf /etc/wpa_supplicant/wpa_supplicant-mlan0.conf' "postinst secures mlan0 active secret"
contains "$POSTINST" 'secure_wpa_conf /etc/wpa_supplicant/wpa_supplicant-mlan1.conf' "postinst secures mlan1 active secret"
contains "$FACTORY" 'secure_wpa_conf /etc/wpa_supplicant/wpa_supplicant-mlan0.conf' "factory reset secures mlan0 secret"
contains "$FACTORY" 'secure_wpa_conf /etc/wpa_supplicant/wpa_supplicant-mlan1.conf' "factory reset secures mlan1 secret"

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
