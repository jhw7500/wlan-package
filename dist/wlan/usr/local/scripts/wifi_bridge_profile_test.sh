#!/bin/bash
# wifi.sh br profile CLI regression tests. Hardware is not required.
set -u

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
WIFI_SH="$SCRIPT_DIR/wifi.sh"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

PASS=0
FAIL=0

pass() {
    PASS=$((PASS + 1))
    echo "PASS: $*"
}

fail() {
    FAIL=$((FAIL + 1))
    echo "FAIL: $*"
}

fresh_config() {
    export WIFI_INIT_CONF_JSON="$WORK/wifi_init_conf.json"
    jq -n '{
        wbridge: {
            engine: "pcap",
            peer_route: {enabled: true},
            ip_discovery: true,
            arp_ignore_always: {enabled: true},
            moal: {local_hairpin: 1},
            eth_fallback: {enabled: true},
            operator_note: "preserve"
        },
        mlan0: {operator_note: "ip-managed-separately"}
    }' > "$WIFI_INIT_CONF_JSON"
    rm -f "${WIFI_INIT_CONF_JSON}.bak-profile" "${WIFI_INIT_CONF_JSON}.tmp"
}

# The mlan0-ip dry-run must be recognized without changing the configuration.
fresh_config
before=$(sha256sum "$WIFI_INIT_CONF_JSON" | awk '{print $1}')
out=$(bash "$WIFI_SH" 0 br profile mlan0-ip 2>&1)
rc=$?
after=$(sha256sum "$WIFI_INIT_CONF_JSON" | awk '{print $1}')
if [ "$rc" -eq 0 ] && echo "$out" | grep -Fq '[Profile: mlan0-ip]' && \
   [ "$before" = "$after" ] && [ ! -e "${WIFI_INIT_CONF_JSON}.bak-profile" ]; then
    pass "mlan0-ip dry-run is recognized and does not write"
else
    fail "mlan0-ip dry-run contract (rc=$rc)"
fi

# A wrong value in any of the five linked settings must fail this assertion.
fresh_config
out=$(bash "$WIFI_SH" 0 br profile mlan0-ip apply 2>&1)
rc=$?
if [ "$rc" -eq 0 ] && jq -e '
    .wbridge.peer_route.enabled == false and
    .wbridge.ip_discovery == false and
    .wbridge.arp_ignore_always.enabled == false and
    .wbridge.moal.local_hairpin == "" and
    .wbridge.eth_fallback.enabled == false and
    .wbridge.operator_note == "preserve" and
    .mlan0.operator_note == "ip-managed-separately"
' "$WIFI_INIT_CONF_JSON" >/dev/null && \
   jq -e '.wbridge.peer_route.enabled == true' "${WIFI_INIT_CONF_JSON}.bak-profile" >/dev/null; then
    pass "mlan0-ip apply writes the five-setting bundle and preserves unrelated config"
else
    fail "mlan0-ip apply contract (rc=$rc output=$out)"
fi

# An empty local_hairpin value must be shown as the documented driver default,
# not as a visually blank current value.
out=$(bash "$WIFI_SH" 0 br profile mlan0-ip 2>&1)
rc=$?
lhp_line=$(printf '%s\n' "$out" | grep -F 'moal.local_hairpin:')
case "$lhp_line" in
    *"<empty>=driver default 0 ->"*)
        [ "$rc" -eq 0 ] && pass "profile view labels an empty local_hairpin value" ||
            fail "profile view returned rc=$rc"
        ;;
    *)
        fail "profile view left local_hairpin visually blank ($lhp_line)"
        ;;
esac

# The operator-facing profile list must expose the new command.
fresh_config
out=$(bash "$WIFI_SH" 0 br profile 2>&1)
rc=$?
if [ "$rc" -eq 0 ] && echo "$out" | grep -Fq 'mlan0-ip'; then
    pass "profile list advertises mlan0-ip"
else
    fail "profile list omits mlan0-ip (rc=$rc)"
fi

echo "RESULT: PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
