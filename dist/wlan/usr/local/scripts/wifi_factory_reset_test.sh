#!/bin/bash
set -uo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
LIB="$SCRIPT_DIR/wifi_factory_reset_lib.sh"
APPLY_SCRIPT="$SCRIPT_DIR/wifi_apply_enabled.sh"
FACTORY_SCRIPT="$SCRIPT_DIR/factory_reset.sh"
WIFI_INIT_SCRIPT="$SCRIPT_DIR/wifi_init.sh"

PASS=0
FAIL=0
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

pass() { PASS=$((PASS + 1)); printf 'PASS: %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf 'FAIL: %s\n' "$1" >&2; }
expect_rc() {
    local name="$1" expected="$2"
    shift 2
    "$@" >/dev/null 2>&1
    local actual=$?
    [ "$actual" -eq "$expected" ] && pass "$name" || fail "$name (expected rc=$expected actual=$actual)"
}
expect_eq() {
    local name="$1" expected="$2" actual="$3"
    [ "$actual" = "$expected" ] && pass "$name" || fail "$name (expected=[$expected] actual=[$actual])"
}

if [ ! -r "$LIB" ]; then
    fail "factory reset library exists"
    printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
    exit 1
fi

BIN="$WORK/bin"
STATE="$WORK/systemd-state"
mkdir -p "$BIN" "$STATE" "$WORK/active"

cat > "$BIN/logger" <<'EOF'
#!/bin/bash
exit 0
EOF

cat > "$BIN/board-ok" <<'EOF'
#!/bin/bash
tmp="${1}.board.$$"
jq '.global.BOARD_TYPE="imx93" | .global.BUS_TYPE="sdio" | .mcp.iio_device="/sys/bus/iio/devices/iio:device1"' "$1" > "$tmp" && mv "$tmp" "$1"
EOF

cat > "$BIN/board-fail" <<'EOF'
#!/bin/bash
exit 1
EOF

cat > "$BIN/preserve" <<'EOF'
#!/bin/bash
[ "${1:-}" = apply ] || exit 64
[ "${PRESERVE_FAIL:-0}" = 1 ] && exit 1
tmp="${WIFI_INIT_CONF_JSON}.preserve.$$"
jq '.global.CAL_DATA_CFG="preserved-cal.bin"' "$WIFI_INIT_CONF_JSON" > "$tmp" && mv "$tmp" "$WIFI_INIT_CONF_JSON"
EOF

cat > "$BIN/link-reset" <<'EOF'
#!/bin/bash
[ "${LINK_RESET_FAIL:-0}" = 1 ] && exit 1
exit 0
EOF

cat > "$BIN/config-backup" <<'EOF'
#!/bin/bash
exit 0
EOF

cat > "$BIN/apply-enabled" <<'EOF'
#!/bin/bash
[ "${APPLY_FAIL:-0}" = 1 ] && exit 1
[ "${WIFI_APPLY_STRICT:-0}" = 1 ] || exit 2
exit 0
EOF

cat > "$BIN/systemctl" <<'EOF'
#!/bin/bash
cmd="$1"; shift
case "$cmd" in
    cat)
        [ "${MISSING_UNIT:-}" = "${1:-}" ] && exit 1
        exit 0
        ;;
    enable)
        [ "${MISSING_UNIT:-}" = "${1:-}" ] && exit 1
        [ "${ENABLE_FAIL:-}" = "${1:-}" ] && exit 1
        : > "$SYSTEMD_STATE/${1:-}"
        ;;
    is-enabled)
        [ "${1:-}" = --quiet ] && shift
        [ -e "$SYSTEMD_STATE/${1:-}" ]
        ;;
    daemon-reload) exit 0 ;;
    *) exit 0 ;;
esac
EOF
chmod +x "$BIN"/*

export PATH="$BIN:$PATH"
export FACTORY_LOGGER="$BIN/logger"
export FACTORY_SYSTEMCTL="$BIN/systemctl"
export FACTORY_BOARD_CONFIG_SH="$BIN/board-ok"
export FACTORY_PRESERVE_SH="$BIN/preserve"
export FACTORY_LINK_RESET_SH="$BIN/link-reset"
export FACTORY_CAL_BACKUP_SH="$SCRIPT_DIR/wifi_cal_backup.sh"
export FACTORY_CONFIG_BACKUP_SH="$BIN/config-backup"
export FACTORY_APPLY_ENABLED_SH="$BIN/apply-enabled"
export FACTORY_FILE_OWNER=""
export FACTORY_FILE_GROUP=""
export SYSTEMD_STATE="$STATE"

# shellcheck source=./wifi_factory_reset_lib.sh
. "$LIB"

TEMPLATE="$WORK/template.json"
ACTIVE="$WORK/active/wifi_init_conf.json"
STAGED="$WORK/active/.factory-stage.json"
SNAPSHOT="$WORK/preserve.json"

cat > "$TEMPLATE" <<'EOF'
{
  "global": {"BOARD_TYPE":"template", "CAL_DATA_CFG":"template-cal.bin"},
  "mcp": {}, "mlan0":{"enabled":true}, "mlan1":{"enabled":false},
  "mac": {}, "wbridge": {}
}
EOF
printf '{"old":true}\n' > "$ACTIVE"
printf '{}\n' > "$SNAPSHOT"

expect_rc "valid preflight" 0 factory_preflight "$TEMPLATE" "$(dirname "$ACTIVE")"
FACTORY_CAL_BACKUP_SH="$WORK/missing-cal-helper"
expect_rc "missing required calibration helper fails preflight" 1 \
    factory_preflight "$TEMPLATE" "$(dirname "$ACTIVE")"
FACTORY_CAL_BACKUP_SH="$SCRIPT_DIR/wifi_cal_backup.sh"

printf '{bad json\n' > "$WORK/invalid.json"
expect_rc "invalid template rejected" 1 factory_preflight "$WORK/invalid.json" "$(dirname "$ACTIVE")"
expect_eq "invalid template keeps active" '{"old":true}' "$(cat "$ACTIVE")"

FACTORY_BOARD_CONFIG_SH="$BIN/board-fail"
expect_rc "missing or failed board helper rejects stage" 1 factory_stage_config "$TEMPLATE" "$STAGED" "$SNAPSHOT"
expect_eq "board failure keeps active" '{"old":true}' "$(cat "$ACTIVE")"
FACTORY_BOARD_CONFIG_SH="$BIN/board-ok"

PRESERVE_FAIL=1
export PRESERVE_FAIL
expect_rc "preserve apply failure falls back to board template" 0 factory_stage_config "$TEMPLATE" "$STAGED" "$SNAPSHOT"
expect_eq "board fact exists after preserve fallback" 'imx93' "$(jq -r '.global.BOARD_TYPE' "$STAGED")"
expect_eq "template value kept after preserve failure" 'template-cal.bin' "$(jq -r '.global.CAL_DATA_CFG' "$STAGED")"
unset PRESERVE_FAIL

expect_rc "preserve apply success" 0 factory_stage_config "$TEMPLATE" "$STAGED" "$SNAPSHOT"
expect_eq "preserved production value restored" 'preserved-cal.bin' "$(jq -r '.global.CAL_DATA_CFG' "$STAGED")"
expect_rc "atomic commit succeeds" 0 factory_commit_config "$STAGED" "$ACTIVE"
expect_eq "committed active is valid" 'imx93' "$(jq -r '.global.BOARD_TYPE' "$ACTIVE")"
expect_eq "committed mode is 0644" '644' "$(stat -c %a "$ACTIVE")"

rm -f "$STATE"/*
expect_rc "service state restore succeeds" 0 factory_restore_service_state "$ACTIVE"
for unit in wifi-stack.target wifi_apply_enabled.service wifi_init.service; do
    [ -e "$STATE/$unit" ] && pass "$unit enabled" || fail "$unit not enabled"
done
if grep -q 'nginx' "$LIB" "$FACTORY_SCRIPT"; then
    fail "Factory Reset does not own wifi_manager nginx"
else
    pass "Factory Reset does not own wifi_manager nginx"
fi

APPLY_FAIL=1
export APPLY_FAIL
expect_rc "strict service sync failure is fatal" 1 factory_restore_service_state "$ACTIVE"
unset APPLY_FAIL

MISSING_UNIT=wifi_init.service
export MISSING_UNIT
expect_rc "missing required Wi-Fi unit fails preflight" 1 factory_preflight "$TEMPLATE" "$(dirname "$ACTIVE")"
unset MISSING_UNIT

expect_rc "postconditions accept valid committed state" 0 factory_verify_postconditions "$ACTIVE"
LINK_RESET_FAIL=1
export LINK_RESET_FAIL
expect_rc "link reset check failure rejects postconditions" 1 factory_verify_postconditions "$ACTIVE"
unset LINK_RESET_FAIL

# Factory Reset의 필수 payload는 단순 cp 성공 코드뿐 아니라 source/destination 내용,
# owner/mode까지 검증되어야 한다. 실패한 atomic install은 기존 destination을 훼손하면 안 된다.
PAYLOAD_SRC="$WORK/payload-source.conf"
PAYLOAD_DST_DIR="$WORK/payload-destination"
PAYLOAD_DST="$PAYLOAD_DST_DIR/payload.conf"
mkdir -p "$PAYLOAD_DST_DIR"
printf 'factory-default\n' > "$PAYLOAD_SRC"
printf 'stale-runtime-value\n' > "$PAYLOAD_DST"
REQUIRED_PAYLOAD="$PAYLOAD_SRC|$PAYLOAD_DST|0600"
MISSING_PAYLOAD="$WORK/missing-source.conf|$PAYLOAD_DST_DIR/missing.conf|0644"

expect_rc "required payload preflight accepts valid source/destination" 0 \
    factory_preflight_required_payloads "$REQUIRED_PAYLOAD"
expect_rc "required payload preflight rejects missing source" 1 \
    factory_preflight_required_payloads "$MISSING_PAYLOAD"
EMPTY_PAYLOAD_SRC="$WORK/empty-source.conf"
: > "$EMPTY_PAYLOAD_SRC"
expect_rc "required payload preflight rejects empty source" 1 \
    factory_preflight_required_payloads \
    "$EMPTY_PAYLOAD_SRC|$PAYLOAD_DST_DIR/empty.conf|0644"

# manifest 전체를 stage할 수 없는 경우 어떤 destination도 바뀌면 안 된다.
PAYLOAD2_SRC="$WORK/payload-source-2.conf"
PAYLOAD2_DST="$PAYLOAD_DST_DIR/payload-2.conf"
printf 'factory-default-2\n' > "$PAYLOAD2_SRC"
printf 'stale-runtime-value-2\n' > "$PAYLOAD2_DST"
FACTORY_INSTALL_CMD=false
expect_rc "multi-payload stage failure is fatal" 1 \
    factory_install_required_payloads \
    "$REQUIRED_PAYLOAD" "$PAYLOAD2_SRC|$PAYLOAD2_DST|0644"
expect_eq "multi-payload stage failure preserves first destination" \
    'stale-runtime-value' "$(cat "$PAYLOAD_DST")"
expect_eq "multi-payload stage failure preserves second destination" \
    'stale-runtime-value-2' "$(cat "$PAYLOAD2_DST")"
FACTORY_INSTALL_CMD=install

FACTORY_INSTALL_CMD=false
expect_rc "required payload install failure is fatal" 1 \
    factory_install_required_payloads "$REQUIRED_PAYLOAD"
expect_eq "failed required payload install preserves old destination" \
    'stale-runtime-value' "$(cat "$PAYLOAD_DST")"
FACTORY_INSTALL_CMD=install

ROLLBACK1_SRC="$WORK/rollback-source-1.conf"
ROLLBACK2_SRC="$WORK/rollback-source-2.conf"
ROLLBACK1_DST="$PAYLOAD_DST_DIR/rollback-destination-1.conf"
ROLLBACK2_DST="$PAYLOAD_DST_DIR/rollback-destination-2.conf"
printf 'factory-rollback-1\n' > "$ROLLBACK1_SRC"
printf 'factory-rollback-2\n' > "$ROLLBACK2_SRC"
printf 'runtime-rollback-1\n' > "$ROLLBACK1_DST"
printf 'runtime-rollback-2\n' > "$ROLLBACK2_DST"
chmod 0640 "$ROLLBACK1_DST"
cat > "$BIN/move-fail-commit-second" <<'EOF'
#!/bin/bash
count_file="${FACTORY_MOVE_COUNT_FILE:?}"
count=$(cat "$count_file" 2>/dev/null || echo 0)
src="${@: -2:1}"
if [[ "$src" == *.factory.* ]] && [[ "$src" != *.factory-rollback.* ]]; then
    count=$((count + 1))
    printf '%s\n' "$count" > "$count_file"
    [ "$count" -eq 2 ] && exit 1
fi
exec mv "$@"
EOF
chmod +x "$BIN/move-fail-commit-second"
: > "$WORK/move-count"
FACTORY_MOVE_COUNT_FILE="$WORK/move-count"
export FACTORY_MOVE_COUNT_FILE
FACTORY_MOVE_CMD="$BIN/move-fail-commit-second"
expect_rc "multi-payload commit failure rolls back manifest" 1 \
    factory_install_required_payloads \
    "$ROLLBACK1_SRC|$ROLLBACK1_DST|0644" \
    "$ROLLBACK2_SRC|$ROLLBACK2_DST|0644"
expect_eq "multi-payload rollback restores first destination" \
    'runtime-rollback-1' "$(cat "$ROLLBACK1_DST")"
expect_eq "multi-payload rollback preserves second destination" \
    'runtime-rollback-2' "$(cat "$ROLLBACK2_DST")"
expect_eq "multi-payload rollback restores original mode" '640' \
    "$(stat -c %a "$ROLLBACK1_DST")"
FACTORY_MOVE_CMD=mv
unset FACTORY_MOVE_COUNT_FILE

cat > "$BIN/sync-fail-after-commit" <<'EOF'
#!/bin/bash
count_file="${FACTORY_SYNC_COUNT_FILE:?}"
count=$(cat "$count_file" 2>/dev/null || echo 0)
count=$((count + 1))
printf '%s\n' "$count" > "$count_file"
[ "$count" -ge 3 ] && exit 1
exec sync "$@"
EOF
chmod +x "$BIN/sync-fail-after-commit"
printf 'runtime-sync-rollback\n' > "$ROLLBACK1_DST"
: > "$WORK/sync-count"
FACTORY_SYNC_COUNT_FILE="$WORK/sync-count"
export FACTORY_SYNC_COUNT_FILE
FACTORY_SYNC_CMD="$BIN/sync-fail-after-commit"
expect_rc "post-rename sync failure rolls back payload" 1 \
    factory_install_required_payloads \
    "$ROLLBACK1_SRC|$ROLLBACK1_DST|0644"
expect_eq "post-rename sync rollback restores destination" \
    'runtime-sync-rollback' "$(cat "$ROLLBACK1_DST")"
FACTORY_SYNC_CMD=sync
unset FACTORY_SYNC_COUNT_FILE

FACTORY_SYNC_CMD=false
expect_rc "required payload pre-rename sync failure is fatal" 1 \
    factory_install_required_payloads "$REQUIRED_PAYLOAD"
expect_eq "failed required payload sync preserves old destination" \
    'stale-runtime-value' "$(cat "$PAYLOAD_DST")"
FACTORY_SYNC_CMD=sync

FACTORY_MOVE_CMD=false
expect_rc "required payload rename failure is fatal" 1 \
    factory_install_required_payloads "$REQUIRED_PAYLOAD"
expect_eq "failed required payload rename preserves old destination" \
    'stale-runtime-value' "$(cat "$PAYLOAD_DST")"
FACTORY_MOVE_CMD=mv

expect_rc "required payload atomic install succeeds" 0 \
    factory_install_required_payloads "$REQUIRED_PAYLOAD"
expect_eq "required payload replaces stale destination" \
    'factory-default' "$(cat "$PAYLOAD_DST")"
expect_eq "required payload mode is normalized" '600' "$(stat -c %a "$PAYLOAD_DST")"
expect_rc "required payload postcondition accepts exact copy" 0 \
    factory_verify_required_payloads "$REQUIRED_PAYLOAD"

printf 'tampered-after-copy\n' > "$PAYLOAD_DST"
expect_rc "required payload postcondition rejects stale content" 1 \
    factory_verify_required_payloads "$REQUIRED_PAYLOAD"
printf 'factory-default\n' > "$PAYLOAD_DST"
chmod 0644 "$PAYLOAD_DST"
expect_rc "required payload postcondition rejects wrong mode" 1 \
    factory_verify_required_payloads "$REQUIRED_PAYLOAD"

# destination symlink를 따라가면 공격자가 가리킨 파일을 덮을 수 있다. atomic rename은
# symlink 자체를 일반 파일로 교체하고 링크 대상은 그대로 보존해야 한다.
SYMLINK_TARGET="$WORK/symlink-target.conf"
SYMLINK_DST="$PAYLOAD_DST_DIR/symlink-destination.conf"
printf 'must-not-change\n' > "$SYMLINK_TARGET"
rm -f -- "$SYMLINK_DST"
ln -s "$SYMLINK_TARGET" "$SYMLINK_DST"
expect_rc "required payload install replaces destination symlink itself" 0 \
    factory_install_required_payloads \
    "$PAYLOAD_SRC|$SYMLINK_DST|0600"
[ ! -L "$SYMLINK_DST" ] \
    && pass "required payload destination is a regular file after install" \
    || fail "required payload destination symlink survived install"
expect_eq "required payload install does not overwrite symlink target" \
    'must-not-change' "$(cat "$SYMLINK_TARGET")"

# root 실행에서는 known system directory도 root:root/0755로 정규화한다. 테스트는
# 현재 사용자 소유를 root 대용으로 주입해 동일한 계약을 비권한 환경에서 검증한다.
FACTORY_FILE_OWNER=$(id -un)
FACTORY_FILE_GROUP=$(id -gn)
chmod 0777 "$PAYLOAD_DST_DIR"
expect_rc "required payload install normalizes destination directory" 0 \
    factory_install_required_payloads "$REQUIRED_PAYLOAD"
expect_eq "required payload destination directory mode is 0755" '755' \
    "$(stat -c %a "$PAYLOAD_DST_DIR")"
expect_eq "required payload destination directory owner is normalized" \
    "$FACTORY_FILE_OWNER:$FACTORY_FILE_GROUP" \
    "$(stat -c '%U:%G' "$PAYLOAD_DST_DIR")"
FACTORY_FILE_OWNER=""
FACTORY_FILE_GROUP=""

# 실제 장시간 버튼 경로도 reset 실패를 무시하고 별도 강제 재부팅하면 안 된다.
# reboot의 단일 소유자는 factory_reset.sh이며 switchd는 성공/실패와 무관하게 즉시 종료한다.
SWITCHD="$SCRIPT_DIR/switchd.sh"
if grep -Eq 'if[[:space:]]+!?[[:space:]]*/usr/local/scripts/factory_reset\.sh|/usr/local/scripts/factory_reset\.sh[[:space:]]*&&' "$SWITCHD"; then
    pass "long-press caller gates on factory reset result"
else
    fail "long-press caller ignores factory reset result"
fi
if sed -n '/factory_reset\.sh/,+12p' "$SWITCHD" | grep -q 'wlan_reboot_policy\.sh'; then
    fail "long-press caller still forces a second reboot"
else
    pass "factory reset remains the single reboot owner"
fi
if grep -Eq "trap .*exit 0.*(INT TERM EXIT|EXIT)" "$SWITCHD"; then
    fail "switchd EXIT trap masks factory reset failure status"
else
    pass "switchd cleanup preserves factory reset failure status"
fi

# Factory Reset은 enable/disable 일부 실패를 성공으로 삼으면 안 된다. 일반 boot 경로는
# 기존 best-effort 동작을 유지하고 strict 호출만 non-zero를 반환한다.
APPLY_JSON="$WORK/apply.json"
cat > "$APPLY_JSON" <<'EOF'
{
  "global":{"ping_monitor":{"enabled":false}},
  "mlan0":{"enabled":true,"STANDARD":"ax","logger":{"enabled":false},"checker":{"enabled":false},"mcs_tier":{"enabled":true,"he":"both 7"}},
  "mlan1":{"enabled":true,"STANDARD":"ac","logger":{"enabled":false},"checker":{"enabled":false},"mcs_tier":{"enabled":true,"he":""}},
  "wbridge":{"enabled":false,"thermal":{"enabled":false}},
  "snmp":{"enabled":false,"trap":{"enabled":false}}, "opc":{"enabled":false}
}
EOF
rm -f "$STATE"/*
expect_rc "normal service sync remains best effort" 0 \
    env WIFI_INIT_CONF_JSON="$APPLY_JSON" WIFI_APPLY_STRICT=0 \
        ENABLE_FAIL='wpa_supplicant@mlan0.service' SYSTEMD_STATE="$STATE" \
        PATH="$PATH" bash "$APPLY_SCRIPT"
[ -e "$STATE/wifi_event@mlan0.service" ] \
    && pass "MCS enabled keeps wifi_event enabled for deferred verification" \
    || fail "MCS enabled did not enable wifi_event"
[ ! -e "$STATE/wifi_event@mlan1.service" ] \
    && pass "AC-only MCS does not force deferred verification service" \
    || fail "AC-only MCS unexpectedly enabled wifi_event"
rm -f "$STATE"/*
expect_rc "strict service sync returns non-zero on unit failure" 1 \
    env WIFI_INIT_CONF_JSON="$APPLY_JSON" WIFI_APPLY_STRICT=1 \
        ENABLE_FAIL='wpa_supplicant@mlan0.service' SYSTEMD_STATE="$STATE" \
        PATH="$PATH" bash "$APPLY_SCRIPT"

# 실제 entrypoint가 검증된 상태기계를 우회하지 않는지 정적으로 고정한다.
for call in factory_preflight factory_preflight_required_payloads \
            factory_stage_config factory_install_required_payloads \
            factory_commit_config factory_restore_service_state \
            factory_verify_required_payloads factory_verify_postconditions; do
    grep -q "$call" "$FACTORY_SCRIPT" \
        && pass "factory entrypoint calls $call" \
        || fail "factory entrypoint missing $call"
done
if grep -Eq 'if[[:space:]]+![[:space:]]+"\$FACTORY_CAL_BACKUP_SH"[[:space:]]+reset' "$FACTORY_SCRIPT"; then
    pass "factory entrypoint treats production calibration reset failure as fatal"
else
    fail "factory entrypoint ignores production calibration reset failure"
fi
if grep -q 'safe_cp /opt/wlan/config/wifi_init_conf.json' "$FACTORY_SCRIPT"; then
    fail "factory entrypoint still directly overwrites active JSON"
else
    pass "factory entrypoint has no direct active JSON overwrite"
fi
if grep -q 'customctl enable wifi_init\|customctl enable wifi_\(logger\|checker\|bgscan\|roam\|event\)@mlan' "$FACTORY_SCRIPT"; then
    fail "factory entrypoint still duplicates JSON-managed Wi-Fi service policy"
else
    pass "factory entrypoint delegates JSON-managed Wi-Fi service policy"
fi
grep -Fq '"$_DEFAULT_DIR/wlan/wifi_mod_para.conf"' "$WIFI_INIT_SCRIPT" \
    && pass "wifi_init self-heals MOD_PARA from canonical factory source" \
    || fail "wifi_init MOD_PARA fallback still uses legacy source"
if grep -Fq '"$_DEFAULT_DIR/wifi_mod_para__.conf"' "$WIFI_INIT_SCRIPT"; then
    fail "wifi_init still references legacy MOD_PARA fallback"
else
    pass "wifi_init has no legacy MOD_PARA fallback"
fi
for required_source in \
    /opt/wlan/config/wlan/txpwrlimit_cfg_9098.conf \
    /opt/wlan/config/wlan/wifi_mod_para.conf \
    /opt/wlan/config/wpa_supplicant/wpa_supplicant@.service \
    /opt/wlan/config/wpa_supplicant/wpa_supplicant-mlan0.conf \
    /opt/wlan/config/wpa_supplicant/wpa_supplicant-mlan1.conf \
    /opt/wlan/config/systemd/network/20-mlan0.network \
    /opt/wlan/config/systemd/network/21-mlan1.network \
    /opt/wlan/config/systemd/network/22-eth0.network; do
    if grep -Fq "safe_cp $required_source" "$FACTORY_SCRIPT"; then
        fail "required payload still uses ignored safe_cp: $required_source"
    else
        pass "required payload no longer uses ignored safe_cp: $required_source"
    fi
done

for required_record in \
    '/opt/wlan/config/wlan/txpwrlimit_cfg_9098.conf|/lib/firmware/cts/txpwrlimit_cfg_9098.conf|0644' \
    '/opt/wlan/config/wlan/txpwrlimit_cfg_9098.conf|/lib/firmware/cts/txpwrlimit_cfg_9098.conf.bak|0644' \
    '/opt/wlan/config/wlan/wifi_mod_para.conf|/lib/firmware/cts/wifi_mod_para.conf|0644' \
    '/opt/wlan/config/wlan/wifi_mod_para.conf|/lib/firmware/cts/wifi_mod_para.conf.bak|0644' \
    '/opt/wlan/config/wpa_supplicant/wpa_supplicant@.service|/lib/systemd/system/wpa_supplicant@.service|0644' \
    '/opt/wlan/config/wpa_supplicant/wpa_supplicant-mlan0.conf|/etc/wpa_supplicant/wpa_supplicant-mlan0.conf|0600' \
    '/opt/wlan/config/wpa_supplicant/wpa_supplicant-mlan0.conf|/etc/wpa_supplicant/wpa_supplicant-mlan0.conf.bak|0600' \
    '/opt/wlan/config/wpa_supplicant/wpa_supplicant-mlan1.conf|/etc/wpa_supplicant/wpa_supplicant-mlan1.conf|0600' \
    '/opt/wlan/config/wpa_supplicant/wpa_supplicant-mlan1.conf|/etc/wpa_supplicant/wpa_supplicant-mlan1.conf.bak|0600' \
    '/opt/wlan/config/systemd/network/20-mlan0.network|/etc/systemd/network/20-mlan0.network|0644' \
    '/opt/wlan/config/systemd/network/20-mlan0.network|/etc/systemd/network/20-mlan0.network.bak|0644' \
    '/opt/wlan/config/systemd/network/21-mlan1.network|/etc/systemd/network/21-mlan1.network|0644' \
    '/opt/wlan/config/systemd/network/21-mlan1.network|/etc/systemd/network/21-mlan1.network.bak|0644' \
    '/opt/wlan/config/systemd/network/22-eth0.network|/etc/systemd/network/22-eth0.network|0644' \
    '/opt/wlan/config/systemd/network/22-eth0.network|/etc/systemd/network/22-eth0.network.bak|0644'; do
    grep -Fq "\"$required_record\"" "$FACTORY_SCRIPT" \
        && pass "factory manifest has exact required record: $required_record" \
        || fail "factory manifest missing exact required record: $required_record"
done

# wifi_init의 self-healing이 reset 이전 WPA/IP/FW 값을 되살리지 못하도록 관리되는
# .bak 세대도 동일 factory source로 원자적으로 재시드되어야 한다.
for required_backup in \
    /lib/firmware/cts/wifi_mod_para.conf.bak \
    /lib/firmware/cts/txpwrlimit_cfg_9098.conf.bak \
    /etc/wpa_supplicant/wpa_supplicant-mlan0.conf.bak \
    /etc/wpa_supplicant/wpa_supplicant-mlan1.conf.bak \
    /etc/systemd/network/20-mlan0.network.bak \
    /etc/systemd/network/21-mlan1.network.bak \
    /etc/systemd/network/22-eth0.network.bak; do
    grep -Fq "|$required_backup|" "$FACTORY_SCRIPT" \
        && pass "factory manifest reseeds backup: $required_backup" \
        || fail "factory manifest misses managed backup: $required_backup"
done

# Factory Reset 뒤에도 유선 관리 경로가 유지되어야 한다. 이 주소는 실제 양산/시험
# 제품 Factory Reset 유선 기본값은 192.168.1.1/24다.
FACTORY_ETH0_TEMPLATE="$SCRIPT_DIR/../../../opt/wlan/config/systemd/network/22-eth0.network"
if [ "$(awk -F= '$1 == "Address" { print $2 }' "$FACTORY_ETH0_TEMPLATE")" = "192.168.1.1/24" ]; then
    pass "factory eth0 address is 192.168.1.1/24"
else
    fail "factory eth0 address is not 192.168.1.1/24"
fi

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
