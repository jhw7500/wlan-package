#!/bin/bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
LIB="$SCRIPT_DIR/wifi_init_config_lib.sh"
WIFI_INIT_SH="$SCRIPT_DIR/wifi_init.sh"

PASS_COUNT=0
FAIL_COUNT=0

log_pass() {
    PASS_COUNT=$((PASS_COUNT + 1))
    echo "[PASS] $*"
}

log_fail() {
    FAIL_COUNT=$((FAIL_COUNT + 1))
    echo "[FAIL] $*"
}

expect_equal() {
    local desc="$1"
    local got="$2"
    local expected="$3"

    if [ "$got" = "$expected" ]; then
        log_pass "$desc"
    else
        log_fail "$desc (expected=$expected got=$got)"
    fi
}

expect_file_contains() {
    local desc="$1"
    local file="$2"
    local pattern="$3"

    if grep -q "$pattern" "$file"; then
        log_pass "$desc"
    else
        log_fail "$desc (missing pattern: $pattern)"
    fi
}

make_json_files() {
    local conf_json="$1"
    local overlay_json="$2"
    local conf_body="$3"
    local overlay_body="$4"

    printf '%s\n' "$conf_body" > "$conf_json"
    if [ -n "$overlay_body" ]; then
        printf '%s\n' "$overlay_body" > "$overlay_json"
    fi
}

run_case() {
    local name="$1"
    local conf_body="$2"
    local overlay_body="$3"
    local expected_enabled="$4"
    local expected_frequency="$5"

    local tmpdir
    tmpdir=$(mktemp -d)
    local conf_json="$tmpdir/wifi_init_conf.json"
    local overlay_json="$tmpdir/config.json"

    make_json_files "$conf_json" "$overlay_json" "$conf_body" "$overlay_body"

    WIFI_INIT_CONF_JSON="$conf_json" \
    JSON_FILE="$overlay_json" \
    bash -c '
        set -euo pipefail
        source "$1"
        enabled=$(wifi_init_get_iface_enabled mlan0)
        frequency=$(wifi_init_get_iface_frequency mlan0)
        printf "%s\n%s\n" "$enabled" "$frequency"
    ' _ "$LIB" > "$tmpdir/output.txt"

    local enabled
    local frequency
    enabled=$(sed -n '1p' "$tmpdir/output.txt")
    frequency=$(sed -n '2p' "$tmpdir/output.txt")

    expect_equal "$name enabled" "$enabled" "$expected_enabled"
    expect_equal "$name frequency" "$frequency" "$expected_frequency"

    rm -rf "$tmpdir"
}

if [ ! -f "$LIB" ]; then
    echo "missing library: $LIB" >&2
    exit 1
fi

expect_file_contains "wifi_init.sh defines read_mac_from_json" "$WIFI_INIT_SH" '^read_mac_from_json() {'
expect_file_contains "wifi_init.sh defines resolve_mac" "$WIFI_INIT_SH" '^resolve_mac() {'

run_case \
    "base values" \
    '{"mlan0":{"enabled":false,"Frequency":"5GHz"}}' \
    '' \
    "false" \
    "5GHz"

run_case \
    "overlay overrides" \
    '{"mlan0":{"enabled":false,"Frequency":"5GHz"}}' \
    '{"mlan0":{"enabled":true,"Frequency":"2.4GHz"}}' \
    "true" \
    "2.4GHz"

run_case \
    "partial overlay" \
    '{"mlan0":{"enabled":false,"Frequency":"5GHz"}}' \
    '{"mlan0":{"enabled":true}}' \
    "true" \
    "5GHz"

# --- mcs_tier tests ---

echo ""
echo "=== mcs_tier tests ==="

# T-mcs-01: mcs_tier disabled (default)
_json='{"mlan0":{"mcs_tier":{"enabled":false,"ht":7,"vht":7,"he":7}}}'
expect_equal "mcs_tier disabled" \
    "$(echo "$_json" | jq -r '.mlan0.mcs_tier.enabled // false')" \
    "false"

# T-mcs-02: mcs_tier enabled, read all tiers
_json='{"mlan0":{"mcs_tier":{"enabled":true,"ht":7,"vht":8,"he":9}}}'
expect_equal "mcs_tier enabled" \
    "$(echo "$_json" | jq -r '.mlan0.mcs_tier.enabled')" \
    "true"
expect_equal "mcs_tier ht=7" \
    "$(echo "$_json" | jq -r '.mlan0.mcs_tier.ht')" \
    "7"
expect_equal "mcs_tier vht=8" \
    "$(echo "$_json" | jq -r '.mlan0.mcs_tier.vht')" \
    "8"
expect_equal "mcs_tier he=9" \
    "$(echo "$_json" | jq -r '.mlan0.mcs_tier.he')" \
    "9"

# T-mcs-03: partial config (he only)
_json='{"mlan0":{"mcs_tier":{"enabled":true,"he":11}}}'
expect_equal "mcs_tier partial ht=empty" \
    "$(echo "$_json" | jq -r '.mlan0.mcs_tier.ht // empty')" \
    ""
expect_equal "mcs_tier partial he=11" \
    "$(echo "$_json" | jq -r '.mlan0.mcs_tier.he')" \
    "11"

# T-mcs-04: mcs_tier section missing → default false
_json='{"mlan0":{"enabled":true}}'
expect_equal "mcs_tier missing → false" \
    "$(echo "$_json" | jq -r '.mlan0.mcs_tier.enabled // false')" \
    "false"

# T-mcs-05: per-interface independence
_json='{"mlan0":{"mcs_tier":{"enabled":true,"he":7}},"mlan1":{"mcs_tier":{"enabled":true,"he":11}}}'
expect_equal "mlan0 he=7" \
    "$(echo "$_json" | jq -r '.mlan0.mcs_tier.he')" \
    "7"
expect_equal "mlan1 he=11" \
    "$(echo "$_json" | jq -r '.mlan1.mcs_tier.he')" \
    "11"

# --- radio mode/bw helper tests ---

echo ""
echo "=== radio mode/bw helpers ==="

# shellcheck source=./wifi_init_config_lib.sh
source "$LIB"

expect_equal "mask b"  "$(wifi_init_mode_to_bandcfg_mask b)"  "0x1"
expect_equal "mask g"  "$(wifi_init_mode_to_bandcfg_mask g)"  "0x3"
expect_equal "mask a"  "$(wifi_init_mode_to_bandcfg_mask a)"  "0x7"
expect_equal "mask n"  "$(wifi_init_mode_to_bandcfg_mask n)"  "0x1F"
expect_equal "mask ac" "$(wifi_init_mode_to_bandcfg_mask ac)" "0x5F"
expect_equal "mask ax" "$(wifi_init_mode_to_bandcfg_mask ax)" "0x35F"
expect_equal "mask invalid → empty" "$(wifi_init_mode_to_bandcfg_mask zz || true)" ""

expect_equal "htcap 20"   "$(wifi_init_bw_to_htcap 20)"   "0x05c00000"
expect_equal "htcap 40"   "$(wifi_init_bw_to_htcap 40)"   "0x05c20000"
expect_equal "htcap 80"   "$(wifi_init_bw_to_htcap 80)"   "0x05c20000"
expect_equal "htcap auto" "$(wifi_init_bw_to_htcap auto)" "0x05c20000"
expect_equal "htcap invalid → empty" "$(wifi_init_bw_to_htcap 160 || true)" ""

expect_equal "vhtbw 20"   "$(wifi_init_bw_to_vhtbw 20)"   "0"
expect_equal "vhtbw 40"   "$(wifi_init_bw_to_vhtbw 40)"   "0"
expect_equal "vhtbw 80"   "$(wifi_init_bw_to_vhtbw 80)"   "1"
expect_equal "vhtbw auto" "$(wifi_init_bw_to_vhtbw auto)" "1"
expect_equal "vhtbw invalid → empty" "$(wifi_init_bw_to_vhtbw 160 || true)" ""

_fb_tmpd=$(mktemp -d)
printf 'network={\n    freq_list=5180 5200\n    scan_freq=5180 5200\n}\n' > "$_fb_tmpd/c1.conf"
expect_equal "freq_bands 5G-only" "$(wifi_init_conf_freq_bands "$_fb_tmpd/c1.conf")" "5G"
printf 'network={\n    freq_list=2412 5180\n}\n' > "$_fb_tmpd/c2.conf"
expect_equal "freq_bands mixed" "$(wifi_init_conf_freq_bands "$_fb_tmpd/c2.conf")" "2G 5G"
printf 'network={\n    scan_freq=2412 # home only\n}\n' > "$_fb_tmpd/c3.conf"
expect_equal "freq_bands 2G + comment tokens" "$(wifi_init_conf_freq_bands "$_fb_tmpd/c3.conf")" "2G"
printf 'network={\n    ssid="x"\n}\n' > "$_fb_tmpd/c4.conf"
expect_equal "freq_bands no restriction" "$(wifi_init_conf_freq_bands "$_fb_tmpd/c4.conf")" ""
expect_equal "freq_bands missing file" "$(wifi_init_conf_freq_bands "$_fb_tmpd/none.conf")" ""
rm -rf "$_fb_tmpd"

echo "PASS: $PASS_COUNT"
echo "FAIL: $FAIL_COUNT"

if [ "$FAIL_COUNT" -gt 0 ]; then
    exit 1
fi
