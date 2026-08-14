#!/bin/bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
LIB="$SCRIPT_DIR/wifi_init_config_lib.sh"
WIFI_INIT_SH="$SCRIPT_DIR/wifi_init.sh"
WIFI_SH="$SCRIPT_DIR/wifi.sh"
FACTORY_RESET_SH="$SCRIPT_DIR/factory_reset.sh"
POSTINST="$SCRIPT_DIR/../../../DEBIAN/postinst"
LEGACY_CONFIG_TEMPLATE="$SCRIPT_DIR/../../../opt/wlan/config/config.json"
GUIDE="$SCRIPT_DIR/../../../../../docs/wifi_init_conf_guide.md"
HANDOFF="$SCRIPT_DIR/../../../../../docs/wifi_init_conf_webui_handoff.md"

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

expect_file_not_contains() {
    local desc="$1"
    local file="$2"
    local pattern="$3"

    if grep -q "$pattern" "$file"; then
        log_fail "$desc (unexpected pattern: $pattern)"
    else
        log_pass "$desc"
    fi
}

expect_path_absent() {
    local desc="$1"
    local path="$2"

    if [ -e "$path" ] || [ -L "$path" ]; then
        log_fail "$desc (unexpected path: $path)"
    else
        log_pass "$desc"
    fi
}

expect_file_exists() {
    local desc="$1"
    local path="$2"

    if [ -f "$path" ]; then
        log_pass "$desc"
    else
        log_fail "$desc (missing file: $path)"
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
# 인터페이스별 MAC 쓰기는 한 지점이어야 한다 (base→override 이중 쓰기/불필요한 백업 회전 방지).
expect_equal \
    "wifi_init.sh has one update_mac write point" \
    "$(grep -c '^[[:space:]]*if /usr/local/scripts/update_mac.sh "\$iface" "\$mac" ' "$WIFI_INIT_SH")" \
    "1"
# 쓸 MAC이 없을 때의 클론 폐기(--clear)도 한 지점 — 쓰기 경로와 상호 배타적이어야 한다.
expect_equal \
    "wifi_init.sh has one update_mac clear point" \
    "$(grep -c '^[[:space:]]*if /usr/local/scripts/update_mac.sh "\$iface" --clear' "$WIFI_INIT_SH")" \
    "1"

expect_file_not_contains "wifi_init.sh has no legacy config path" "$WIFI_INIT_SH" '/usr/local/etc/config.json'
expect_file_not_contains "wifi.sh has no legacy config path" "$WIFI_SH" '/usr/local/etc/config.json'
expect_file_not_contains "config library has no overlay branch" "$LIB" 'overlay_json'
expect_file_not_contains "factory reset does not restore legacy config" "$FACTORY_RESET_SH" '/opt/wlan/config/config.json'
expect_path_absent "legacy config template is not packaged" "$LEGACY_CONFIG_TEMPLATE"
expect_file_contains "upgrade removes retired active config" "$POSTINST" 'rm -f -- /usr/local/etc/config.json'
expect_file_not_contains "postinst JSON merge never truncates active" "$POSTINST" 'echo "$merged" > "$active"'
expect_file_contains "postinst JSON merge uses atomic helper" "$POSTINST" 'atomic_json_install "$merged" "$active"'
expect_file_exists "operator guide is available to the source validation" "$GUIDE"
expect_file_exists "WebUI handoff is available to the source validation" "$HANDOFF"
expect_file_not_contains "guide has no legacy active config path" "$GUIDE" '/usr/local/etc/config.json'
expect_file_not_contains "guide has no legacy template path" "$GUIDE" '/opt/wlan/config/config.json'
expect_file_not_contains "handoff has no legacy active config path" "$HANDOFF" '/usr/local/etc/config.json'
expect_file_not_contains "handoff has no legacy template path" "$HANDOFF" '/opt/wlan/config/config.json'

run_case \
    "base values" \
    '{"mlan0":{"enabled":false,"Frequency":"5GHz"}}' \
    '' \
    "false" \
    "5GHz"

run_case \
    "legacy overlay is ignored" \
    '{"mlan0":{"enabled":false,"Frequency":"5GHz"}}' \
    '{"mlan0":{"enabled":true,"Frequency":"2.4GHz"}}' \
    "false" \
    "5GHz"

run_case \
    "legacy partial overlay is ignored" \
    '{"mlan0":{"enabled":false,"Frequency":"5GHz"}}' \
    '{"mlan0":{"enabled":true}}' \
    "false" \
    "5GHz"

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
