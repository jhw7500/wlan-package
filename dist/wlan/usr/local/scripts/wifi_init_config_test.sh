#!/bin/bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
LIB="$SCRIPT_DIR/wifi_init_config_lib.sh"

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

echo "PASS: $PASS_COUNT"
echo "FAIL: $FAIL_COUNT"

if [ "$FAIL_COUNT" -gt 0 ]; then
    exit 1
fi
