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
WPA_TEMPLATE0="$SCRIPT_DIR/../../../opt/wlan/config/wpa_supplicant/wpa_supplicant-mlan0.conf"
WPA_TEMPLATE1="$SCRIPT_DIR/../../../opt/wlan/config/wpa_supplicant/wpa_supplicant-mlan1.conf"

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

# conf 의 모든 network 블록 ssid= 를 **표기와 무관하게** 정체성으로 되돌려
# 한 줄씩 출력한다. 표기를 grep 으로 고정하면 직렬화 정책이 바뀔 때마다 깨지고,
# 정작 지켜야 할 것(바이트 정확한 SSID)은 검증되지 않는다.
conf_ssid_identities() {
    python3 - "$1" <<'PYCODE'
import re, sys
in_net = False
for raw in open(sys.argv[1], encoding="utf-8", errors="surrogateescape"):
    line = raw.strip()
    if re.match(r"^network\s*=\s*\{", line):
        in_net = True
        continue
    if in_net and line.startswith("}"):
        in_net = False
        continue
    if in_net and line.startswith("ssid="):
        token = line.split("=", 1)[1].strip()
        if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
            print(token[1:-1])
        else:
            print(bytes.fromhex(token).decode("utf-8"))
PYCODE
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
# 호출 앞의 환경변수 대입(예: MAC_SOURCE=...)은 허용한다 — 세는 대상은 "쓰기 지점의
# 개수"이지 호출 줄의 글자 모양이 아니다. 리터럴로 고정했더니 #268 이 MAC_SOURCE 를
# 붙이는 순간 0 이 되어 릴리스 게이트를 막았다.
expect_equal \
    "wifi_init.sh has one update_mac write point" \
    "$(grep -cE '^[[:space:]]*if ([A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*[[:space:]]+)*/usr/local/scripts/update_mac\.sh "\$iface" "\$mac" ' "$WIFI_INIT_SH")" \
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

_atomic_bin=$(mktemp -d)
cat > "$_atomic_bin/mv" <<'EOF'
#!/bin/sh
dst=
for arg in "$@"; do dst=$arg; done
if [ "${WIFI_ATOMIC_FAULT:-}" = mv ] && [ "$dst" = "${WIFI_ATOMIC_CONF:-}" ]; then
    exit 1
fi
exec /bin/mv "$@"
EOF
cat > "$_atomic_bin/sync" <<'EOF'
#!/bin/sh
state=${WIFI_ATOMIC_STATE:?}
if [ "$#" -eq 0 ] && [ -f "$state/fallback-pending" ]; then
    rm -f "$state/fallback-pending"
    exit 1
fi
arg=${1:-}
actual=
case "$arg" in
    "${WIFI_ATOMIC_CONF}".*) actual=stage ;;
    "${WIFI_ATOMIC_CONF}")   actual=installed ;;
    "${WIFI_ATOMIC_DIR}")    actual=dir ;;
esac
if [ "${WIFI_ATOMIC_FAULT:-}" = "$actual" ]; then
    : > "$state/fallback-pending"
    exit 1
fi
exit 0
EOF
chmod +x "$_atomic_bin/mv" "$_atomic_bin/sync"

run_boot_atomic_fault_case() {
    local operation="$1" fault="$2" case_dir conf json rc=0
    case_dir=$(mktemp -d)
    mkdir -p "$case_dir/run" "$case_dir/latches" "$case_dir/state"
    conf="$case_dir/wpa_supplicant-mlan0.conf"
    json="$case_dir/wifi_init_conf.json"
    cat > "$conf" <<'EOF'
update_config=1
network={
    ssid="base"
    key_mgmt=WPA-PSK
    psk="12345678"
    scan_freq=5180
}
EOF
    case "$operation" in
        normalize)
            printf '{"mlan0":{"roaming":{"generate_network_blocks":false,"extra_ssids":[]}}}\n' > "$json"
            ;;
        generate)
            printf '{"mlan0":{"roaming":{"generate_network_blocks":true,"extra_ssids":["office"]}}}\n' > "$json"
            ;;
        remove-empty|remove-mode-b|remove-missing-template)
            cat >> "$conf" <<'EOF'
# >>> wifi_extra_ssid auto-generated (do not edit) >>>
network={
    ssid="old-extra"
    key_mgmt=WPA-PSK
    psk="12345678"
}
# <<< wifi_extra_ssid auto-generated <<<
EOF
            case "$operation" in
                remove-empty)
                    printf '{"mlan0":{"roaming":{"generate_network_blocks":true,"extra_ssids":[]}}}\n' > "$json" ;;
                remove-mode-b)
                    printf '{"mlan0":{"roaming":{"generate_network_blocks":false,"extra_ssids":["ignored"]}}}\n' > "$json" ;;
                remove-missing-template)
                    # Remove only the base credential so the required-template branch strips generated blocks.
                    awk 'BEGIN{base=1} /^# >>> wifi_extra_ssid/{base=0} !(base && /psk="12345678"/)' "$conf" > "$conf.no-psk"
                    mv "$conf.no-psk" "$conf"
                    printf '{"mlan0":{"roaming":{"generate_network_blocks":true,"extra_ssids":["office"]}}}\n' > "$json" ;;
            esac
            ;;
    esac
    cp "$conf" "$case_dir/original.conf"
    rm -f "$case_dir/state/fallback-pending"
    if PATH="$_atomic_bin:$PATH" \
       WIFI_ATOMIC_FAULT="$fault" WIFI_ATOMIC_CONF="$conf" \
       WIFI_ATOMIC_DIR="$case_dir" WIFI_ATOMIC_STATE="$case_dir/state" \
       WIFI_INIT_CONF_JSON="$json" WIFI_RUN_DIR="$case_dir/run" \
       WIFI_ROAM_POLICY_LATCH_DIR="$case_dir/latches" \
       bash -c '
          set -u
          . "$1"
          case "$2" in
            normalize) wifi_wpa_conf_normalize_file "$3" ;;
            *) wifi_init_sync_extra_ssid_blocks mlan0 "$3" ;;
          esac
       ' _ "$LIB" "$operation" "$conf"; then
        rc=0
    else
        rc=$?
    fi
    if [ "$rc" -ne 0 ]; then
        log_pass "$operation propagates $fault install boundary failure"
    else
        log_fail "$operation must propagate $fault install boundary failure"
    fi
    if [ "$fault" = stage ] || [ "$fault" = mv ]; then
        if cmp -s "$case_dir/original.conf" "$conf"; then
            log_pass "$operation $fault failure leaves original byte-exact"
        else
            log_fail "$operation $fault failure must leave original byte-exact"
        fi
    fi
    rm -rf "$case_dir"
}

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

# --- wpa_supplicant common frequency resolver ---

echo ""
echo "=== wpa common frequency resolver ==="

_wpa_tmpd=$(mktemp -d)
cat > "$_wpa_tmpd/global.conf" <<'EOF'
freq_list=5180 5200
network={
    ssid="base"
    freq_list=2412
    scan_freq=2437
}
EOF
cat > "$_wpa_tmpd/base-list.conf" <<'EOF'
network={
    ssid="base"
    freq_list=2412 2437
    scan_freq=5180
}
network={
    ssid="extra"
    freq_list=5200
}
EOF
cat > "$_wpa_tmpd/base-scan.conf" <<'EOF'
network={
    ssid="base"
    scan_freq=5220 5240 # legacy base list
}
EOF
cat > "$_wpa_tmpd/unrestricted.conf" <<'EOF'
network={
    ssid="base"
}
EOF

if declare -F wifi_wpa_conf_common_freqs >/dev/null; then
    expect_equal "common freq global wins" \
        "$(wifi_wpa_conf_common_freqs "$_wpa_tmpd/global.conf")" "5180 5200"
    expect_equal "common freq base freq_list fallback" \
        "$(wifi_wpa_conf_common_freqs "$_wpa_tmpd/base-list.conf")" "2412 2437"
    expect_equal "common freq base scan_freq legacy fallback" \
        "$(wifi_wpa_conf_common_freqs "$_wpa_tmpd/base-scan.conf")" "5220 5240"
    expect_equal "common freq unrestricted" \
        "$(wifi_wpa_conf_common_freqs "$_wpa_tmpd/unrestricted.conf")" ""
else
    log_fail "wifi_wpa_conf_common_freqs is defined"
fi

cat > "$_wpa_tmpd/legacy-render.conf" <<'EOF'
# preserve this comment
update_config=1
country=KR
freq_list=2412
network={
    ssid="base"
    scan_freq=2412 2437
    freq_list=2412 2437
}
network={
    ssid="extra"
    scan_freq=5180
    freq_list=5180
}
EOF

if declare -F wifi_wpa_conf_render_canonical >/dev/null; then
    if wifi_wpa_conf_render_canonical \
        "$_wpa_tmpd/legacy-render.conf" "$_wpa_tmpd/rendered.conf" "5180 5200"; then
        expect_equal "canonical has one global freq_list" \
            "$(awk '
                /^[[:space:]]*#/ { next }
                /^[[:space:]]*network[[:space:]]*=/ { in_net=1 }
                !in_net && /^[[:space:]]*freq_list[[:space:]]*=/ { n++ }
                in_net && /^[[:space:]]*}/ { in_net=0 }
                END { print n+0 }
            ' "$_wpa_tmpd/rendered.conf")" "1"
        expect_equal "canonical copies freq_list to every block" \
            "$(awk '
                /^[[:space:]]*network[[:space:]]*=/ { in_net=1; next }
                in_net && /^[[:space:]]*freq_list=5180 5200$/ { n++ }
                in_net && /^[[:space:]]*}/ { in_net=0 }
                END { print n+0 }
            ' "$_wpa_tmpd/rendered.conf")" "2"
        expect_equal "canonical removes active scan_freq" \
            "$(grep -Ec '^[[:space:]]*scan_freq[[:space:]]*=' "$_wpa_tmpd/rendered.conf" || true)" "0"
        expect_equal "canonical forces one update_config=0" \
            "$(grep -Ec '^update_config=0$' "$_wpa_tmpd/rendered.conf" || true)" "1"
        expect_file_contains "canonical preserves unrelated comments" \
            "$_wpa_tmpd/rendered.conf" '^# preserve this comment$'

        wifi_wpa_conf_render_canonical \
            "$_wpa_tmpd/rendered.conf" "$_wpa_tmpd/rendered-again.conf" "5180 5200"
        if cmp -s "$_wpa_tmpd/rendered.conf" "$_wpa_tmpd/rendered-again.conf"; then
            log_pass "canonical render is idempotent"
        else
            log_fail "canonical render is idempotent"
        fi

        wifi_wpa_conf_render_canonical \
            "$_wpa_tmpd/legacy-render.conf" "$_wpa_tmpd/unrestricted-render.conf" ""
        expect_equal "unrestricted removes all active freq_list" \
            "$(grep -Ec '^[[:space:]]*freq_list[[:space:]]*=' "$_wpa_tmpd/unrestricted-render.conf" || true)" "0"
        expect_equal "unrestricted removes all active scan_freq" \
            "$(grep -Ec '^[[:space:]]*scan_freq[[:space:]]*=' "$_wpa_tmpd/unrestricted-render.conf" || true)" "0"
    else
        log_fail "canonical renderer accepts network config"
    fi
else
    log_fail "wifi_wpa_conf_render_canonical is defined"
fi

cp "$_wpa_tmpd/legacy-render.conf" "$_wpa_tmpd/in-place.conf"
chmod 0640 "$_wpa_tmpd/in-place.conf"
_metadata_uid=$(id -u)
_metadata_gid=$(id -g)
if [ "$_metadata_uid" -eq 0 ]; then
    _metadata_uid=12345
    _metadata_gid=12346
    chown "$_metadata_uid:$_metadata_gid" "$_wpa_tmpd/in-place.conf"
fi
if declare -F wifi_wpa_conf_normalize_file >/dev/null; then
    if wifi_wpa_conf_normalize_file "$_wpa_tmpd/in-place.conf"; then
        expect_equal "in-place normalizer preserves mode" \
            "$(stat -c '%a' "$_wpa_tmpd/in-place.conf")" "640"
        expect_equal "in-place normalizer preserves uid/gid" \
            "$(stat -c '%u:%g' "$_wpa_tmpd/in-place.conf")" \
            "$_metadata_uid:$_metadata_gid"
        expect_equal "in-place normalizer uses resolved global list" \
            "$(wifi_wpa_conf_common_freqs "$_wpa_tmpd/in-place.conf")" "2412"
        cp "$_wpa_tmpd/in-place.conf" "$_wpa_tmpd/in-place.once"
        wifi_wpa_conf_normalize_file "$_wpa_tmpd/in-place.conf"
        if cmp -s "$_wpa_tmpd/in-place.once" "$_wpa_tmpd/in-place.conf"; then
            log_pass "in-place normalization is idempotent"
        else
            log_fail "in-place normalization is idempotent"
        fi
    else
        log_fail "in-place normalizer accepts valid conf"
    fi

    printf 'country=KR\n' > "$_wpa_tmpd/no-network.conf"
    cp "$_wpa_tmpd/no-network.conf" "$_wpa_tmpd/no-network.before"
    if wifi_wpa_conf_normalize_file "$_wpa_tmpd/no-network.conf"; then
        log_fail "in-place normalizer rejects missing network block"
    elif cmp -s "$_wpa_tmpd/no-network.before" "$_wpa_tmpd/no-network.conf"; then
        log_pass "in-place normalizer rejects missing network without damage"
    else
        log_fail "in-place normalizer rejects missing network without damage"
    fi
else
    log_fail "wifi_wpa_conf_normalize_file is defined"
fi

# Even without privilege, exercise the shared helper against files owned by the
# test user.  Root additionally uses a non-default numeric UID/GID fixture.
printf 'source\n' > "$_wpa_tmpd/metadata-source.conf"
printf 'target\n' > "$_wpa_tmpd/metadata-target.conf"
chmod 0640 "$_wpa_tmpd/metadata-source.conf"
chmod 0600 "$_wpa_tmpd/metadata-target.conf"
if [ "$(id -u)" -eq 0 ]; then
    chown "$_metadata_uid:$_metadata_gid" "$_wpa_tmpd/metadata-source.conf"
fi
if declare -F wifi_wpa_conf_preserve_metadata >/dev/null \
   && wifi_wpa_conf_preserve_metadata \
        "$_wpa_tmpd/metadata-source.conf" "$_wpa_tmpd/metadata-target.conf"; then
    expect_equal "shared metadata helper copies mode" \
        "$(stat -c '%a' "$_wpa_tmpd/metadata-target.conf")" "640"
    expect_equal "shared metadata helper copies uid/gid" \
        "$(stat -c '%u:%g' "$_wpa_tmpd/metadata-target.conf")" \
        "$_metadata_uid:$_metadata_gid"
else
    log_fail "shared metadata helper applies required mode and uid/gid"
fi

cat > "$_wpa_tmpd/generated.conf" <<'EOF'
update_config=0
freq_list=5180 5200
network={
    ssid="base"
    key_mgmt=WPA-PSK
    psk="12345678"
    freq_list=5180 5200
}
EOF
chmod 0640 "$_wpa_tmpd/generated.conf"
if [ "$(id -u)" -eq 0 ]; then
    chown "$_metadata_uid:$_metadata_gid" "$_wpa_tmpd/generated.conf"
fi
cat > "$_wpa_tmpd/wifi_init_conf.json" <<'EOF'
{
  "mlan0": {
    "roaming": {
      "generate_network_blocks": true,
      "extra_ssids": ["office", "guest"]
    }
  }
}
EOF
if WIFI_INIT_CONF_JSON="$_wpa_tmpd/wifi_init_conf.json" \
    wifi_init_sync_extra_ssid_blocks mlan0 "$_wpa_tmpd/generated.conf"; then
    expect_equal "generated mode A has three network blocks" \
        "$(grep -Ec '^[[:space:]]*network[[:space:]]*=' "$_wpa_tmpd/generated.conf")" "3"
    expect_equal "generated mode A copies common block filter" \
        "$(grep -Ec '^[[:space:]]+freq_list=5180 5200$' "$_wpa_tmpd/generated.conf")" "3"
    expect_equal "generated mode A has no scan_freq" \
        "$(grep -Ec '^[[:space:]]*scan_freq[[:space:]]*=' "$_wpa_tmpd/generated.conf" || true)" "0"
    expect_equal "generated SSIDs keep their exact identity" \
        "$(conf_ssid_identities "$_wpa_tmpd/generated.conf" | grep -Ecx 'office|guest')" "2"
    expect_equal "generated mode A preserves mode" \
        "$(stat -c '%a' "$_wpa_tmpd/generated.conf")" "640"
    expect_equal "generated mode A preserves uid/gid" \
        "$(stat -c '%u:%g' "$_wpa_tmpd/generated.conf")" \
        "$_metadata_uid:$_metadata_gid"
else
    log_fail "generated mode A sync accepts canonical base"
fi

cat > "$_wpa_tmpd/remove-extra.json" <<'EOF'
{
  "mlan0": {
    "roaming": {
      "generate_network_blocks": true,
      "extra_ssids": []
    }
  }
}
EOF
if WIFI_INIT_CONF_JSON="$_wpa_tmpd/remove-extra.json" \
    wifi_init_sync_extra_ssid_blocks mlan0 "$_wpa_tmpd/generated.conf"; then
    expect_equal "Mode A extra-block removal leaves one network" \
        "$(grep -Ec '^[[:space:]]*network[[:space:]]*=' "$_wpa_tmpd/generated.conf")" "1"
    expect_equal "Mode A extra-block removal preserves mode" \
        "$(stat -c '%a' "$_wpa_tmpd/generated.conf")" "640"
    expect_equal "Mode A extra-block removal preserves uid/gid" \
        "$(stat -c '%u:%g' "$_wpa_tmpd/generated.conf")" \
        "$_metadata_uid:$_metadata_gid"
else
    log_fail "Mode A extra-block removal succeeds"
fi

cat > "$_wpa_tmpd/empty-extra.json" <<'EOF'
{
  "mlan0": {
    "roaming": {
      "enabled": true,
      "generate_network_blocks": true,
      "extra_ssids": []
    },
    "bgscan": {"enabled": true}
  }
}
EOF
cat > "$_wpa_tmpd/empty-extra.conf" <<'EOF'
update_config=0
network={
    ssid="base"
    key_mgmt=WPA-PSK
    psk="12345678"
}
EOF
if WIFI_INIT_CONF_JSON="$_wpa_tmpd/empty-extra.json" \
    wifi_wpa_conf_is_multi_topology mlan0 "$_wpa_tmpd/empty-extra.conf"; then
    log_pass "Mode A identity survives an empty extra_ssids list"
else
    log_fail "Mode A identity survives an empty extra_ssids list"
fi

mkdir -p "$_wpa_tmpd/run"
cat > "$_wpa_tmpd/run/mlan0.roam-policy.json" <<'EOF'
{"version":1,"iface":"mlan0","roaming_enabled":true,"bgscan_enabled":true,"generate_network_blocks":true,"extra_ssids":[]}
EOF
cat > "$_wpa_tmpd/live-mode-b.json" <<'EOF'
{"mlan0":{"roaming":{"generate_network_blocks":false},"bgscan":{"enabled":false}}}
EOF
if WIFI_RUN_DIR="$_wpa_tmpd/run" WIFI_INIT_CONF_JSON="$_wpa_tmpd/live-mode-b.json" \
    wifi_wpa_conf_is_multi_topology mlan0 "$_wpa_tmpd/empty-extra.conf"; then
    log_pass "boot snapshot wins over runtime topology JSON edits"
else
    log_fail "boot snapshot wins over runtime topology JSON edits"
fi

# snapshot이 이미 생성된 boot에서 파일만 삭제되면 live JSON으로
# topology를 재해석하지 않고 writer도 보수적 multi 판정으로 fail-closed한다.
rm -f "$_wpa_tmpd/run/mlan0.roam-policy.json"
: > "$_wpa_tmpd/.mlan0.roam-policy.latched"
if WIFI_RUN_DIR="$_wpa_tmpd/run" WIFI_INIT_CONF_JSON="$_wpa_tmpd/live-mode-b.json" \
    wifi_wpa_conf_is_multi_topology mlan0 "$_wpa_tmpd/empty-extra.conf"; then
    log_pass "deleted boot snapshot blocks live topology fallback"
else
    log_fail "deleted boot snapshot blocks live topology fallback"
fi
cp "$_wpa_tmpd/empty-extra.conf" "$_wpa_tmpd/deleted-snapshot-sync.conf"
if WIFI_RUN_DIR="$_wpa_tmpd/run" WIFI_INIT_CONF_JSON="$_wpa_tmpd/live-mode-b.json" \
    wifi_init_sync_extra_ssid_blocks mlan0 "$_wpa_tmpd/deleted-snapshot-sync.conf"; then
    log_fail "deleted boot snapshot blocks extra-block writer fallback"
elif cmp -s "$_wpa_tmpd/empty-extra.conf" "$_wpa_tmpd/deleted-snapshot-sync.conf"; then
    log_pass "deleted boot snapshot blocks extra-block writer fallback"
else
    log_fail "deleted boot snapshot failure leaves extra-block conf unchanged"
fi

cat > "$_wpa_tmpd/run/mlan0.roam-policy.json" <<'EOF'
{"version":1,"iface":"mlan0","roaming_enabled":true,"bgscan_enabled":true,"generate_network_blocks":true,"extra_ssids":["BootOffice"]}
EOF
cp "$_wpa_tmpd/empty-extra.conf" "$_wpa_tmpd/snapshot-sync.conf"
if WIFI_RUN_DIR="$_wpa_tmpd/run" WIFI_INIT_CONF_JSON="$_wpa_tmpd/live-mode-b.json" \
    wifi_init_sync_extra_ssid_blocks mlan0 "$_wpa_tmpd/snapshot-sync.conf"; then
    expect_equal "extra block sync uses boot snapshot after service restart" \
        "$(grep -Ec '^[[:space:]]*network[[:space:]]*=' "$_wpa_tmpd/snapshot-sync.conf")" "2"
    expect_equal "extra block sync ignores mutated live JSON" \
        "$(conf_ssid_identities "$_wpa_tmpd/snapshot-sync.conf" | grep -cx 'BootOffice')" "1"
else
    log_fail "extra block sync uses boot snapshot after service restart"
fi

# Shared SSID identity contract: valid leading/trailing spaces, UTF-8,
# backslash, and quote bytes survive the writer byte-exactly. 직렬화 표기는
# wifi_ssid_to_conf_value 가 고른다(안전하면 따옴표, `"` 포함 시 hex).
cat > "$_wpa_tmpd/special-ssid.json" <<'EOF'
{"mlan0":{"roaming":{"generate_network_blocks":true,"extra_ssids":["  게스트 \\ \" exact  "]}}}
EOF
cp "$_wpa_tmpd/empty-extra.conf" "$_wpa_tmpd/special-ssid.conf"
_special_ssid='  게스트 \ " exact  '
_special_hex=$(python3 -c 'import sys; print(sys.argv[1].encode("utf-8").hex())' "$_special_ssid")
if WIFI_INIT_CONF_JSON="$_wpa_tmpd/special-ssid.json" \
   WIFI_RUN_DIR="$_wpa_tmpd/special-run" \
   WIFI_ROAM_POLICY_LATCH_DIR="$_wpa_tmpd/special-latch" \
   wifi_init_sync_extra_ssid_blocks mlan0 "$_wpa_tmpd/special-ssid.conf"; then
    expect_equal "extra SSID special bytes serialize as exact hex" \
        "$(grep -Ec "^[[:space:]]+ssid=${_special_hex}$" "$_wpa_tmpd/special-ssid.conf")" "1"
else
    log_fail "valid special UTF-8 extra SSID is accepted"
fi

for _invalid_extra_json in \
    '["base"]' \
    '["dup","dup"]' \
    '[""]' \
    '["bad\u007f"]' \
    '["가가가가가가가가가가가"]'; do
    printf '{"mlan0":{"roaming":{"generate_network_blocks":true,"extra_ssids":%s}}}\n' \
        "$_invalid_extra_json" > "$_wpa_tmpd/invalid-extra.json"
    cp "$_wpa_tmpd/empty-extra.conf" "$_wpa_tmpd/invalid-extra.conf"
    cp "$_wpa_tmpd/invalid-extra.conf" "$_wpa_tmpd/invalid-extra.before"
    if WIFI_INIT_CONF_JSON="$_wpa_tmpd/invalid-extra.json" \
       WIFI_RUN_DIR="$_wpa_tmpd/invalid-run" \
       WIFI_ROAM_POLICY_LATCH_DIR="$_wpa_tmpd/invalid-latch" \
       wifi_init_sync_extra_ssid_blocks mlan0 "$_wpa_tmpd/invalid-extra.conf"; then
        log_fail "invalid/duplicate/base SSID list must be rejected: $_invalid_extra_json"
    elif cmp -s "$_wpa_tmpd/invalid-extra.before" "$_wpa_tmpd/invalid-extra.conf"; then
        log_pass "invalid/duplicate/base SSID rejection is non-mutating: $_invalid_extra_json"
    else
        log_fail "invalid SSID rejection mutated conf: $_invalid_extra_json"
    fi
    rm -rf "$_wpa_tmpd/invalid-run" "$_wpa_tmpd/invalid-latch"
done

_wpa_parser=$(command -v wpa_supplicant || true)
if [ -n "$_wpa_parser" ]; then
    _parser_log="$_wpa_tmpd/wpa-parser.log"
    "$_wpa_parser" -D none -i wlanpkgtest0 -c "$_wpa_tmpd/special-ssid.conf" -dd \
        >"$_parser_log" 2>&1 || true
    if grep -q 'Successfully initialized wpa_supplicant' "$_parser_log" \
       && ! grep -q 'Failed to read or parse configuration' "$_parser_log"; then
        log_pass "real wpa_supplicant parser accepts generated hex SSID config"
    else
        log_fail "real wpa_supplicant parser must accept generated hex SSID config"
    fi
else
    log_fail "real wpa_supplicant parser is required for SSID serialization coverage"
fi

cat > "$_wpa_tmpd/manual-multi.conf" <<'EOF'
network={
    ssid="one"
}
network={
    ssid="two"
}
EOF
cat > "$_wpa_tmpd/run/mlan0.roam-policy.json" <<'EOF'
{"version":1,"iface":"mlan0","roaming_enabled":false,"bgscan_enabled":true,"generate_network_blocks":false,"extra_ssids":[]}
EOF
if WIFI_RUN_DIR="$_wpa_tmpd/run" \
    wifi_wpa_conf_is_multi_topology mlan0 "$_wpa_tmpd/manual-multi.conf"; then
    log_pass "manual multi-block conf is protected from single-SSID writers"
else
    log_fail "manual multi-block conf is protected from single-SSID writers"
fi
rm -rf "$_wpa_tmpd"

echo ""
echo "=== checked boot topology atomic install failures ==="
for _boot_operation in normalize generate remove-empty remove-mode-b remove-missing-template; do
    for _boot_fault in stage mv installed dir; do
        run_boot_atomic_fault_case "$_boot_operation" "$_boot_fault"
    done
done

# Required topology transforms are bounded by their stable section comments, not
# source line numbers. Every fail guard in that section must terminate wifi_init
# before the later bgscan/supplicant continuation can run.
_boot_transform_region=$(awk '
    index($0, "# backup_file") && index($0, "scan_freq/freq_list") { in_topology=1 }
    in_topology && /^# bgscan/ { exit }
    in_topology { print }
' "$WIFI_INIT_SH")
if printf '%s\n' "$_boot_transform_region" | grep -Fq 'wifi_wpa_conf_normalize_file' \
   && printf '%s\n' "$_boot_transform_region" | grep -Fq 'wifi_init_sync_extra_ssid_blocks' \
   && printf '%s\n' "$_boot_transform_region" | awk '
        /^[[:space:]]*if ! / { guards++; waiting=1; next }
        waiting && /^[[:space:]]*exit 1$/ { exits++; waiting=0; next }
        waiting && /^[[:space:]]*fi$/ { missing++; waiting=0 }
        END { exit !(guards > 0 && exits == guards && missing == 0) }
    ';
then
    log_pass "wifi_init fails closed when required wpa topology preparation fails"
else
    log_fail "wifi_init must exit before supplicant continuation on topology failure"
fi
rm -rf "$_atomic_bin"

_fb_tmpd=$(mktemp -d)
printf 'network={\n    freq_list=5180 5200\n    scan_freq=5180 5200\n}\n' > "$_fb_tmpd/c1.conf"
expect_equal "freq_bands 5G-only" "$(wifi_init_conf_freq_bands "$_fb_tmpd/c1.conf")" "5G"
printf 'network={\n    freq_list=2412 5180\n}\n' > "$_fb_tmpd/c2.conf"
expect_equal "freq_bands mixed" "$(wifi_init_conf_freq_bands "$_fb_tmpd/c2.conf")" "2G 5G"
printf 'network={\n    scan_freq=2412 # home only\n}\n' > "$_fb_tmpd/c3.conf"
expect_equal "freq_bands 2G + comment tokens" "$(wifi_init_conf_freq_bands "$_fb_tmpd/c3.conf")" "2G"
printf 'network={\n    ssid="x"\n}\n' > "$_fb_tmpd/c4.conf"
expect_equal "freq_bands no restriction" "$(wifi_init_conf_freq_bands "$_fb_tmpd/c4.conf")" ""
printf 'freq_list=5180 5200\nnetwork={\n    freq_list=2412\n    scan_freq=2437\n}\n' > "$_fb_tmpd/c5.conf"
expect_equal "freq_bands uses canonical global precedence" \
    "$(wifi_init_conf_freq_bands "$_fb_tmpd/c5.conf")" "5G"
expect_equal "freq_bands missing file" "$(wifi_init_conf_freq_bands "$_fb_tmpd/none.conf")" ""
rm -rf "$_fb_tmpd"

# 부팅은 backup_file 복원 후 canonical 정규화, 그 다음 extra block 생성 순서다.
_normalize_line=$(grep -n 'wifi_wpa_conf_normalize_file.*wpa_supplicant-mlan0' "$WIFI_INIT_SH" | head -1 | cut -d: -f1 || true)
_generate_line=$(grep -n 'wifi_init_sync_extra_ssid_blocks mlan0' "$WIFI_INIT_SH" | head -1 | cut -d: -f1 || true)
if [ -n "$_normalize_line" ] && [ -n "$_generate_line" ] && [ "$_normalize_line" -lt "$_generate_line" ]; then
    log_pass "wifi_init normalizes mlan0 before generating blocks"
else
    log_fail "wifi_init normalizes mlan0 before generating blocks"
fi

for _template in "$WPA_TEMPLATE0" "$WPA_TEMPLATE1"; do
    expect_equal "$(basename "$_template") uses update_config=0" \
        "$(grep -Ec '^update_config=0$' "$_template" || true)" "1"
    expect_equal "$(basename "$_template") has one global freq_list" \
        "$(awk '
            /^[[:space:]]*#/ { next }
            /^[[:space:]]*network[[:space:]]*=/ { in_net=1 }
            !in_net && /^[[:space:]]*freq_list[[:space:]]*=/ { n++ }
            in_net && /^[[:space:]]*}/ { in_net=0 }
            END { print n+0 }
        ' "$_template")" "1"
    expect_equal "$(basename "$_template") has no scan_freq" \
        "$(grep -Ec '^[[:space:]]*scan_freq[[:space:]]*=' "$_template" || true)" "0"
done

echo "PASS: $PASS_COUNT"
echo "FAIL: $FAIL_COUNT"

if [ "$FAIL_COUNT" -gt 0 ]; then
    exit 1
fi
