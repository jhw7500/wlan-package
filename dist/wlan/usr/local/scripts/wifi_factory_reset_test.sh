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

cat > "$BIN/topology" <<'EOF'
#!/bin/bash
[ "${TOPOLOGY_FAIL:-0}" = 1 ] && exit 1
printf '%s\n' "$*" > "${TOPOLOGY_ARGS_FILE:?}"
case "${1:-}" in
    read) printf '%s\n' "${TOPOLOGY_OUTPUT:-eth0-ip}" ;;
    snapshot)
        if [ -n "${TOPOLOGY_SNAPSHOT_OUTPUT:-}" ]; then
            printf '%s\n' "$TOPOLOGY_SNAPSHOT_OUTPUT"
        else
            printf '%s\n' '{"topology":"eth0-ip","generation":"absent"}'
        fi
        ;;
    *) exit 64 ;;
esac
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
export FACTORY_TOPOLOGY_HELPER="$BIN/topology"
export FACTORY_TOPOLOGY_FILE="$WORK/wifi_factory_topology.json"
FACTORY_TOPOLOGY_OWNER_UID="$(id -u)"
FACTORY_TOPOLOGY_OWNER_GID="$(id -g)"
export FACTORY_TOPOLOGY_OWNER_UID FACTORY_TOPOLOGY_OWNER_GID
export FACTORY_FILE_OWNER=""
export FACTORY_FILE_GROUP=""
export SYSTEMD_STATE="$STATE"
export TOPOLOGY_ARGS_FILE="$WORK/topology-args"

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
  "mac": {},
  "wbridge": {
    "ip_discovery": true,
    "peer_route":{"enabled":true},
    "arp_ignore_always":{"enabled":false},
    "moal":{"local_hairpin":"1"},
    "eth_fallback":{"enabled":true}
  }
}
EOF
printf '{"old":true}\n' > "$ACTIVE"
printf '{}\n' > "$SNAPSHOT"

expect_rc "valid preflight" 0 factory_preflight "$TEMPLATE" "$(dirname "$ACTIVE")"
TOPOLOGY_OUTPUT=eth0-ip
export TOPOLOGY_OUTPUT
expect_eq "legacy topology helper default is accepted" 'eth0-ip' "$(factory_read_topology)"
expect_eq "topology helper receives the strict selector contract" \
    "read --path $FACTORY_TOPOLOGY_FILE --owner-uid $FACTORY_TOPOLOGY_OWNER_UID --owner-gid $FACTORY_TOPOLOGY_OWNER_GID" \
    "$(cat "$TOPOLOGY_ARGS_FILE")"
TOPOLOGY_FAIL=1
export TOPOLOGY_FAIL
expect_rc "invalid or unsafe topology aborts before reset mutation" 1 factory_read_topology
unset TOPOLOGY_FAIL
TOPOLOGY_GENERATION_A="v1-$(printf 'a%.0s' {1..64})"
TOPOLOGY_GENERATION_B="v1-$(printf 'b%.0s' {1..64})"
TOPOLOGY_SNAPSHOT_OUTPUT="{\"topology\":\"peer-route\",\"generation\":\"$TOPOLOGY_GENERATION_A\"}"
export TOPOLOGY_SNAPSHOT_OUTPUT
expect_eq "topology snapshot freezes profile and generation" \
    "$TOPOLOGY_SNAPSHOT_OUTPUT" "$(factory_snapshot_topology)"
expect_rc "unchanged topology snapshot passes final generation check" 0 \
    factory_topology_snapshot_matches "$TOPOLOGY_SNAPSHOT_OUTPUT"
TOPOLOGY_SNAPSHOT_OUTPUT="{\"topology\":\"peer-route\",\"generation\":\"$TOPOLOGY_GENERATION_B\"}"
expect_rc "changed topology generation fails the final check" 1 \
    factory_topology_snapshot_matches \
        "{\"topology\":\"peer-route\",\"generation\":\"$TOPOLOGY_GENERATION_A\"}"
TOPOLOGY_SNAPSHOT_OUTPUT='{"topology":"peer-route","generation":"not-a-selector-generation"}'
expect_rc "malformed topology generation fails closed" 1 factory_snapshot_topology
TOPOLOGY_SNAPSHOT_OUTPUT='{"topology":"peer-route","generation":7}'
expect_rc "malformed topology snapshot fails closed" 1 factory_snapshot_topology
TOPOLOGY_SNAPSHOT_OUTPUT='{"topology":"eth0-ip","generation":"absent"}'
export TOPOLOGY_SNAPSHOT_OUTPUT
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

expect_rc "eth0-ip profile overrides mismatched runtime topology" 0 \
    factory_stage_config "$TEMPLATE" "$STAGED" "$SNAPSHOT" eth0-ip
expect_eq "preserved production value restored" 'preserved-cal.bin' "$(jq -r '.global.CAL_DATA_CFG' "$STAGED")"
expect_eq "eth0-ip profile writes the exact five-field mapping" \
    'false|false|true||false' \
    "$(jq -r '[.wbridge.peer_route.enabled,.wbridge.ip_discovery,.wbridge.arp_ignore_always.enabled,.wbridge.moal.local_hairpin,.wbridge.eth_fallback.enabled] | map(tostring) | join("|")' "$STAGED")"
expect_rc "peer-route profile stages successfully" 0 \
    factory_stage_config "$TEMPLATE" "$STAGED" "$SNAPSHOT" peer-route
expect_eq "peer-route profile writes the exact five-field mapping" \
    'true|true|false||false' \
    "$(jq -r '[.wbridge.peer_route.enabled,.wbridge.ip_discovery,.wbridge.arp_ignore_always.enabled,.wbridge.moal.local_hairpin,.wbridge.eth_fallback.enabled] | map(tostring) | join("|")' "$STAGED")"
expect_rc "unknown topology cannot stage factory JSON" 1 \
    factory_stage_config "$TEMPLATE" "$STAGED" "$SNAPSHOT" unknown
expect_rc "peer-route profile restages after invalid-input test" 0 \
    factory_stage_config "$TEMPLATE" "$STAGED" "$SNAPSHOT" peer-route
expect_rc "atomic commit succeeds" 0 factory_commit_config "$STAGED" "$ACTIVE"
expect_eq "committed active is valid" 'imx93' "$(jq -r '.global.BOARD_TYPE' "$ACTIVE")"
expect_eq "committed mode is 0644" '644' "$(stat -c %a "$ACTIVE")"

rm -f "$STATE"/*
expect_rc "service state restore succeeds" 0 factory_restore_service_state "$ACTIVE"
for unit in wifi-stack.target wifi_apply_enabled.service wifi_init.service; do
    [ -e "$STATE/$unit" ] && pass "$unit enabled" || fail "$unit not enabled"
done
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
expect_eq "post-rename sync failure retains recovery transaction" 1 \
    "$FACTORY_TRANSACTION_ACTIVE"
FACTORY_SYNC_CMD=sync
expect_rc "post-rename sync rollback retries after storage recovers" 0 \
    factory_rollback_required_payloads
expect_eq "post-rename sync rollback restores destination" \
    'runtime-sync-rollback' "$(cat "$ROLLBACK1_DST")"
unset FACTORY_SYNC_COUNT_FILE

cat > "$BIN/sync-path-eio-global-ok" <<'EOF'
#!/bin/bash
[ "$#" -eq 0 ] && exit 0
exit 5
EOF
chmod +x "$BIN/sync-path-eio-global-ok"
printf 'runtime-path-eio\n' > "$ROLLBACK1_DST"
FACTORY_SYNC_CMD="$BIN/sync-path-eio-global-ok"
expect_rc "path-specific sync EIO is not masked by successful global sync" 1 \
    factory_install_required_payloads \
    "$ROLLBACK1_SRC|$ROLLBACK1_DST|0644"
expect_eq "path-specific sync EIO preserves destination" \
    'runtime-path-eio' "$(cat "$ROLLBACK1_DST")"
FACTORY_SYNC_CMD=sync
printf 'runtime-path-eio\n' > "$ROLLBACK1_DST"

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
expect_eq "failed required payload rename retains recovery transaction" 1 \
    "$FACTORY_TRANSACTION_ACTIVE"
FACTORY_MOVE_CMD=mv
expect_rc "failed required payload rename rollback retries after mover recovers" 0 \
    factory_rollback_required_payloads

expect_rc "required payload atomic install succeeds" 0 \
    factory_install_required_payloads "$REQUIRED_PAYLOAD"
expect_eq "required payload replaces stale destination" \
    'factory-default' "$(cat "$PAYLOAD_DST")"
expect_eq "required payload mode is normalized" '600' "$(stat -c %a "$PAYLOAD_DST")"
expect_rc "required payload postcondition accepts exact copy" 0 \
    factory_verify_required_payloads "$REQUIRED_PAYLOAD"

# JSON active와 그 정상본 세대, network/WPA/FW payload는 후조건 완료 전까지 하나의
# rollback 단위여야 한다. 아래 세 파일은 작은 통합 fixture로 실제 rename/복원을 검증한다.
TX_JSON_SRC="$WORK/tx-json-source"
TX_BAK_SRC="$WORK/tx-bak-source"
TX_PAYLOAD_SRC="$WORK/tx-payload-source"
TX_JSON_DST="$WORK/active/tx.json"
TX_BAK_DST="$WORK/active/tx.json.bak"
TX_PAYLOAD_DST="$PAYLOAD_DST_DIR/tx-payload"
printf 'factory-json\n' > "$TX_JSON_SRC"
printf 'factory-bak\n' > "$TX_BAK_SRC"
printf 'factory-payload\n' > "$TX_PAYLOAD_SRC"
printf 'runtime-json\n' > "$TX_JSON_DST"
printf 'runtime-bak\n' > "$TX_BAK_DST"
printf 'runtime-payload\n' > "$TX_PAYLOAD_DST"
TX_RECORDS=(
    "$TX_JSON_SRC|$TX_JSON_DST|0644"
    "$TX_BAK_SRC|$TX_BAK_DST|0644"
    "$TX_PAYLOAD_SRC|$TX_PAYLOAD_DST|0600"
)

# 첫 rename 전에 transaction 상태와 해당 rollback record가 공개되어야 EXIT trap이
# commit loop 중단에도 이미 바뀐 파일을 복원할 수 있다.
OBSERVED_PUBLISHES=0
move_require_published_transaction() {
    local src="${@: -2:1}"
    if [[ "$src" == *.factory.* ]] && [[ "$src" != *.factory-rollback.* ]]; then
        OBSERVED_PUBLISHES=$((OBSERVED_PUBLISHES + 1))
        [ "$FACTORY_TRANSACTION_ACTIVE" -eq 1 ] || return 91
        [ "${#FACTORY_TRANSACTION_ENTRIES[@]}" -eq "$OBSERVED_PUBLISHES" ] || return 92
    fi
    mv "$@"
}
FACTORY_MOVE_CMD=move_require_published_transaction
expect_rc "transaction is recoverable before every publish rename" 0 \
    factory_begin_required_payloads "${TX_RECORDS[@]}"
FACTORY_MOVE_CMD=mv
expect_eq "all publish renames observed registered rollback state" 3 "$OBSERVED_PUBLISHES"
if [ "$FACTORY_TRANSACTION_ACTIVE" -eq 1 ]; then
    expect_rc "observed publish transaction rolls back" 0 factory_rollback_required_payloads
else
    FACTORY_TRANSACTION_RECORDS=()
    FACTORY_TRANSACTION_ENTRIES=()
fi

expect_rc "combined JSON and payload transaction begins" 0 \
    factory_begin_required_payloads "${TX_RECORDS[@]}"
expect_eq "combined transaction publishes JSON" 'factory-json' "$(cat "$TX_JSON_DST")"
expect_eq "combined transaction publishes JSON backup" 'factory-bak' "$(cat "$TX_BAK_DST")"
expect_eq "combined transaction publishes payload" 'factory-payload' "$(cat "$TX_PAYLOAD_DST")"
expect_rc "later postcondition failure can roll back the whole transaction" 0 \
    factory_rollback_required_payloads
expect_eq "rollback restores prior JSON" 'runtime-json' "$(cat "$TX_JSON_DST")"
expect_eq "rollback restores prior JSON backup" 'runtime-bak' "$(cat "$TX_BAK_DST")"
expect_eq "rollback restores prior payload" 'runtime-payload' "$(cat "$TX_PAYLOAD_DST")"

expect_rc "combined transaction can begin again" 0 \
    factory_begin_required_payloads "${TX_RECORDS[@]}"
FACTORY_MOVE_CMD=false
expect_rc "rollback failure is explicit" 1 factory_rollback_required_payloads
FACTORY_MOVE_CMD=mv
expect_rc "rollback can be retried after transient failure" 0 factory_rollback_required_payloads

# 역순 rollback 일부가 성공하고 중간 항목이 실패해도 backup을 소비하지 않아야 한다.
expect_rc "mixed rollback fixture transaction begins" 0 \
    factory_begin_required_payloads "${TX_RECORDS[@]}"
ROLLBACK_MOVE_COUNT=0
ROLLBACK_FAIL_ONCE=1
move_fail_second_rollback_once() {
    local src="${@: -2:1}"
    if [[ "$src" == *.factory-rollback.* || "$src" == *.factory-restore.* ]]; then
        ROLLBACK_MOVE_COUNT=$((ROLLBACK_MOVE_COUNT + 1))
        if [ "$ROLLBACK_FAIL_ONCE" -eq 1 ] && [ "$ROLLBACK_MOVE_COUNT" -eq 2 ]; then
            ROLLBACK_FAIL_ONCE=0
            return 1
        fi
    fi
    mv "$@"
}
FACTORY_MOVE_CMD=move_fail_second_rollback_once
expect_rc "mixed reverse rollback reports the one failed restore" 1 \
    factory_rollback_required_payloads
expect_eq "mixed rollback keeps transaction active for retry" 1 "$FACTORY_TRANSACTION_ACTIVE"
FACTORY_MOVE_CMD=mv
expect_rc "mixed reverse rollback retry restores every destination" 0 \
    factory_rollback_required_payloads
expect_eq "mixed rollback retry restores JSON bytes" 'runtime-json' "$(cat "$TX_JSON_DST")"
expect_eq "mixed rollback retry restores backup bytes" 'runtime-bak' "$(cat "$TX_BAK_DST")"
expect_eq "mixed rollback retry restores payload bytes" 'runtime-payload' "$(cat "$TX_PAYLOAD_DST")"
if [ "$FACTORY_TRANSACTION_ACTIVE" -eq 1 ]; then
    printf 'runtime-json\n' > "$TX_JSON_DST"
    printf 'runtime-bak\n' > "$TX_BAK_DST"
    printf 'runtime-payload\n' > "$TX_PAYLOAD_DST"
    FACTORY_TRANSACTION_ACTIVE=0
    FACTORY_TRANSACTION_RECORDS=()
    FACTORY_TRANSACTION_ENTRIES=()
fi

# publish 실패와 동시에 rollback도 실패하면 ACTIVE/ENTRIES를 보존해 EXIT cleanup이나
# 운영자가 같은 backup으로 복구를 재시도할 수 있어야 한다.
COMMIT_MOVE_COUNT=0
move_fail_second_commit_and_rollback() {
    local src="${@: -2:1}"
    if [[ "$src" == *.factory-rollback.* || "$src" == *.factory-restore.* ]]; then
        return 1
    fi
    if [[ "$src" == *.factory.* ]]; then
        COMMIT_MOVE_COUNT=$((COMMIT_MOVE_COUNT + 1))
        [ "$COMMIT_MOVE_COUNT" -eq 2 ] && return 1
    fi
    mv "$@"
}
FACTORY_MOVE_CMD=move_fail_second_commit_and_rollback
expect_rc "commit error plus rollback error fails transaction begin" 1 \
    factory_begin_required_payloads "${TX_RECORDS[@]}"
expect_eq "failed rollback preserves active transaction state" 1 "$FACTORY_TRANSACTION_ACTIVE"
[ "${#FACTORY_TRANSACTION_ENTRIES[@]}" -ge 1 ] \
    && pass "failed rollback preserves pending recovery entries" \
    || fail "failed rollback discarded pending recovery entries"
FACTORY_MOVE_CMD=mv
if [ "$FACTORY_TRANSACTION_ACTIVE" -eq 1 ]; then
    expect_rc "preserved commit-error rollback can be retried" 0 \
        factory_rollback_required_payloads
else
    printf 'runtime-json\n' > "$TX_JSON_DST"
    printf 'runtime-bak\n' > "$TX_BAK_DST"
    printf 'runtime-payload\n' > "$TX_PAYLOAD_DST"
    FACTORY_TRANSACTION_RECORDS=()
    FACTORY_TRANSACTION_ENTRIES=()
fi

TOPOLOGY_SNAPSHOT_OUTPUT="{\"topology\":\"peer-route\",\"generation\":\"$TOPOLOGY_GENERATION_A\"}"
expect_rc "transaction begins for selector generation race" 0 \
    factory_begin_required_payloads "${TX_RECORDS[@]}"
TOPOLOGY_SNAPSHOT_OUTPUT="{\"topology\":\"peer-route\",\"generation\":\"$TOPOLOGY_GENERATION_B\"}"
expect_rc "changed selector blocks transaction finalization" 1 \
    factory_commit_required_payloads \
        "{\"topology\":\"peer-route\",\"generation\":\"$TOPOLOGY_GENERATION_A\"}"
expect_eq "selector race keeps transaction recoverable" 1 \
    "$FACTORY_TRANSACTION_ACTIVE"
FACTORY_MOVE_CMD=mv
expect_rc "selector race rolls back owned files" 0 factory_rollback_required_payloads

expect_rc "successful combined transaction begins" 0 \
    factory_begin_required_payloads "${TX_RECORDS[@]}"
expect_rc "successful combined transaction discards rollback generations" 0 \
    factory_commit_required_payloads
expect_eq "successful transaction retains factory JSON" 'factory-json' "$(cat "$TX_JSON_DST")"
expect_eq "successful transaction retains factory payload" 'factory-payload' "$(cat "$TX_PAYLOAD_DST")"

expect_rc "transaction begins for finalization cleanup failure" 0 \
    factory_begin_required_payloads "${TX_RECORDS[@]}"
CLEANUP_OBSERVED_ACTIVE=-1
sync_fail_observe_finalized_transaction() {
    CLEANUP_OBSERVED_ACTIVE="$FACTORY_TRANSACTION_ACTIVE"
    return 1
}
FACTORY_SYNC_CMD=sync_fail_observe_finalized_transaction
expect_rc "verified finalization cleanup sync failure is nonfatal" 0 factory_commit_required_payloads
FACTORY_SYNC_CMD=sync
expect_eq "failed finalization cleanup cannot trigger partial rollback" 0 \
    "$FACTORY_TRANSACTION_ACTIVE"
expect_eq "cleanup starts only after transaction finalization" 0 \
    "$CLEANUP_OBSERVED_ACTIVE"
if [ "${#FACTORY_TRANSACTION_CLEANUP_DEBT[@]}" -gt 0 ]; then
    pass "finalization cleanup records private-backup debt"
else
    fail "finalization cleanup lost private-backup debt"
fi
# RED 실행에서도 뒤쪽 독립 fixture가 활성 transaction에 묶이지 않게 테스트 상태만 정리한다.
FACTORY_TRANSACTION_ACTIVE=0
FACTORY_TRANSACTION_RECORDS=()
FACTORY_TRANSACTION_ENTRIES=()

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
# 성공 LED는 critical failure 판정 뒤에만 켜져야 한다.
if awk '
    /^if \[ "\$critical_failures" -ne 0 \]; then$/ { gate = NR }
    /^safe_sysfs_write 1 \/sys\/class\/leds\/(status|lan|wlan)\/brightness$/ {
        success_leds++
        if (!gate) early_success_led++
    }
    END { exit !(gate && success_leds == 3 && !early_success_led) }
' "$FACTORY_SCRIPT"; then
    pass "factory reset success LEDs follow the failure gate"
else
    fail "factory reset success LEDs precede the failure gate"
fi
if grep -Fq 'factory_commit_required_payloads "$FACTORY_TOPOLOGY_SNAPSHOT"' \
        "$FACTORY_SCRIPT" \
   && awk '
       /factory_verify_required_payloads .*FACTORY_TRANSACTION_RECORDS/ && !payloads { payloads=NR }
       /factory_topology_snapshot_matches .*expected_topology_snapshot/ && !generation_check { generation_check=NR }
       /FACTORY_TRANSACTION_ACTIVE=0/ && generation_check && !linearized { linearized=NR }
       END { exit !(payloads && generation_check && linearized && payloads < generation_check && generation_check < linearized) }
   ' "$LIB"; then
    pass "factory selector generation is rechecked at the finalization point"
else
    fail "factory selector generation is not checked immediately before finalization"
fi
if awk '
    /^factory_reset_cleanup\(\)/ { in_cleanup=1 }
    in_cleanup && /factory_rollback_required_payloads/ { rollback=1 }
    in_cleanup && /^}/ { exit !rollback }
    END { if (!in_cleanup) exit 1 }
' "$FACTORY_SCRIPT"; then
    pass "factory entrypoint EXIT cleanup rolls back an interrupted active transaction"
else
    fail "factory entrypoint interruption can leave a transaction partially applied"
fi
# 오류 패턴은 helper만 분리 실행해 sysfs 없이 실제 write 순서를 검증한다.
LED_ERROR_FUNCTION=$(sed -n '/^factory_reset_led_error() {/,/^}/p' "$FACTORY_SCRIPT")
LED_ERROR_ACTUAL=$(bash -c '
    safe_sysfs_write() { printf "%s %s\n" "$1" "$2"; }
    eval "$1"
    factory_reset_led_error
' _ "$LED_ERROR_FUNCTION")
LED_ERROR_EXPECTED=$(printf '%s\n' \
    'none /sys/class/leds/status/trigger' \
    'none /sys/class/leds/lan/trigger' \
    'none /sys/class/leds/wlan/trigger' \
    '1 /sys/class/leds/status/brightness' \
    '0 /sys/class/leds/lan/brightness' \
    '0 /sys/class/leds/wlan/brightness')
expect_eq "factory reset error LED pattern is status on, LAN/WLAN off" \
    "$LED_ERROR_EXPECTED" "$LED_ERROR_ACTUAL"
if awk '
    $1 == "factory_reset_led_error" { last_error_led = NR; calls++ }
    $1 == "exit" && $2 == "1" {
        exits++
        if (last_error_led != NR - 1) missing++
    }
    END { exit !(exits >= 6 && calls == exits && !missing) }
' "$FACTORY_SCRIPT"; then
    pass "factory reset errors show the error LED pattern before exiting"
else
    fail "factory reset error path exits without the error LED pattern"
fi
# #304: 공장 초기화는 정책 스크립트를 거치지 않는 유일한 재부팅이라, 직접
# Reset Cause 0x40을 /run/opc/reset_cause에 남긴 뒤 reboot 해야 opcd가 통지한다.
# 순서 계약: 기록이 `systemctl reboot` 보다 앞에 있고, 억제(reboot inhibited) 분기 뒤에 있다.
if awk '/reboot inhibited/{inh=NR} /^[[:space:]]*(if )?mkdir -p \/run\/opc/{m=NR} /^[[:space:]]*(&& )?printf .0x40.*reset_cause/{c=NR} /^(if ! )?systemctl reboot/{r=NR} END{exit !(inh && m && c && r && inh<m && m<=c && c<r)}' "$FACTORY_SCRIPT"; then
    pass "factory reset writes Reset Cause 0x40 after the inhibit gate and before reboot"
else
    fail "factory reset does not write Reset Cause 0x40 before systemctl reboot"
fi
# #362: 공장값으로 되돌린 mlan0 conf 에 대한 OPC ChangeIp ESSID 복원 표식은 payload commit 뒤,
# 재부팅 전에 지운다(순서 계약: commit < rm < systemctl reboot).
if awk '/factory_commit_required_payloads "\$FACTORY_TOPOLOGY_SNAPSHOT"/{c=NR} /rm -f \/usr\/local\/opc\/etc\/changeip_essid.restore/{m=NR} /^(if ! )?systemctl reboot/{r=NR} END{exit !(c && m && r && c<m && m<r)}' "$FACTORY_SCRIPT"; then
    pass "factory reset removes the ChangeIp ESSID restore marker after the payload commit and before reboot"
else
    fail "factory reset does not remove the ChangeIp ESSID restore marker between commit and reboot"
fi
# #383: 공장 초기화는 OPC 저장값(비밀번호·IP 주소 리스트·무선 설정)을 지워 opcd 가 다음 기동에
# 초기값으로 시작하게 한다. 생산 정보(device_info.json)와 데몬 설정(opc.conf)은 남긴다.
# wlan-opc #194: IP 주소 리스트의 장치 사본·기록·변환 흔적과 고객 홈의 iplist.txt(·.rejected)도 지운다.
OPC_ETC="$WORK/opc-etc"
OPC_HOME="$WORK/opc-home"
mkdir -p "$OPC_ETC" "$OPC_HOME"
OPC_CLEARED="password iplist.cfg iplist.cfg.migrated iplist.txt iplist.user.last radio.conf"
for opc_file in $OPC_CLEARED device_info.json opc.conf; do
    printf 'x\n' > "$OPC_ETC/$opc_file"
done
for home_file in iplist.txt iplist.txt.rejected .profile; do
    printf 'x\n' > "$OPC_HOME/$home_file"
done
cat > "$BIN/logger-capture" <<'EOF'
#!/bin/bash
printf '%s\n' "$*" >> "$OPC_LOG"
EOF
chmod +x "$BIN/logger-capture"
export OPC_LOG="$WORK/opc-clear.log"
if declare -F factory_reset_clear_opc_state >/dev/null; then
    FACTORY_LOGGER="$BIN/logger-capture" factory_reset_clear_opc_state "$OPC_ETC" "$OPC_HOME"
    expect_eq "factory OPC state clear returns success" 0 "$?"
    for opc_file in $OPC_CLEARED; do
        [ ! -e "$OPC_ETC/$opc_file" ] && pass "factory OPC state clear removes $opc_file" \
            || fail "factory OPC state clear leaves $opc_file"
    done
    for opc_file in device_info.json opc.conf; do
        [ -e "$OPC_ETC/$opc_file" ] && pass "factory OPC state clear keeps $opc_file" \
            || fail "factory OPC state clear removed $opc_file"
    done
    for home_file in iplist.txt iplist.txt.rejected; do
        [ ! -e "$OPC_HOME/$home_file" ] && pass "factory OPC state clear removes the customer $home_file (#194)" \
            || fail "factory OPC state clear leaves the customer $home_file (#194)"
    done
    [ -e "$OPC_HOME/.profile" ] && pass "factory OPC state clear keeps other files in the customer home" \
        || fail "factory OPC state clear removed another file in the customer home"
    : > "$OPC_LOG"
    FACTORY_LOGGER="$BIN/logger-capture" factory_reset_clear_opc_state "$OPC_ETC" "$OPC_HOME"
    expect_eq "factory OPC state clear tolerates already-missing files" 0 "$?"
    if grep -q 'removed' "$OPC_LOG"; then
        fail "factory OPC state clear logs a removal for a missing file"
    else
        pass "factory OPC state clear logs nothing for missing files"
    fi
    if [ "$(id -u)" -ne 0 ]; then
        printf 'x\n' > "$OPC_ETC/password"
        : > "$OPC_LOG"
        chmod 0555 "$OPC_ETC"
        FACTORY_LOGGER="$BIN/logger-capture" factory_reset_clear_opc_state "$OPC_ETC"
        opc_clear_rc=$?
        chmod 0755 "$OPC_ETC"
        expect_eq "factory OPC state clear continues when a file cannot be removed" 0 "$opc_clear_rc"
        if grep -q 'local0.warn' "$OPC_LOG" && grep -q 'password' "$OPC_LOG"; then
            pass "factory OPC state clear warns about the file it could not remove"
        else
            fail "factory OPC state clear does not warn about an undeletable file"
        fi
    fi
else
    fail "factory_reset_clear_opc_state is defined"
fi
if grep -Eq '^[[:space:]]+factory_reset_clear_opc_state /usr/local/opc/etc /home/admin$' "$FACTORY_SCRIPT"; then
    pass "factory reset passes the customer home /home/admin to the OPC state clear (#194)"
else
    fail "factory reset does not pass /home/admin to the OPC state clear (#194)"
fi
# 호출 자리 계약: payload commit 뒤, 성공 게이트(critical_failures == 0) 안, systemctl reboot 앞.
# opcd 는 멈추지 않는다 — 재부팅 종료 절차에서 Reset Cause 0x40 을 통지해야 한다.
if awk '/factory_commit_required_payloads "\$FACTORY_TOPOLOGY_SNAPSHOT"/{c=NR} /^[[:space:]]+factory_reset_clear_opc_state \/usr\/local\/opc\/etc/{o=NR; g=prev} {prev=$0} /^(if ! )?systemctl reboot/{r=NR} END{exit !(c && o && r && c<o && o<r && g ~ /critical_failures" -eq 0/)}' "$FACTORY_SCRIPT"; then
    pass "factory reset clears OPC stored values after the payload commit, inside the success gate, before reboot"
else
    fail "factory reset does not clear OPC stored values between commit and reboot inside the success gate"
fi
if grep -Eq 'systemctl (stop|kill)[^#]*opcd' "$FACTORY_SCRIPT"; then
    fail "factory reset stops opcd (the reboot would lose the 0x40 ResetNotice)"
else
    pass "factory reset leaves opcd running so the reboot announces Reset Cause 0x40"
fi
# #380: systemctl reboot 이 실패하면 0x40 원인 파일을 지워, 뒤의 다른 재부팅이 0x40 으로 통지되지 않게 한다.
if awk '/^if ! systemctl reboot; then/{inb=1; next} inb && /rm -f -- \/run\/opc\/reset_cause/{rm=NR} inb && /^[[:space:]]*exit 1/{ex=NR} inb && /^fi/{inb=0} END{exit !(rm && ex && rm<ex)}' "$FACTORY_SCRIPT"; then
    pass "factory reset removes the Reset Cause 0x40 file when systemctl reboot fails"
else
    fail "factory reset leaves the Reset Cause 0x40 file when systemctl reboot fails"
fi

# #380 성공 경로: 0x40 을 기록한 뒤 `if ! systemctl reboot; then` 까지(실패 분기 밖) 원인 파일을 지우거나
# 다시 쓰는 줄이 없어야, 재부팅이 시작될 때 opcd 가 0x40 을 읽는다.
if awk '/^[[:space:]]*(&& )?printf .0x40.*reset_cause/{c=NR; next} c && !r && /^if ! systemctl reboot; then/{r=NR} c && !r && /reset_cause/{bad=NR} END{exit !(c && r && !bad)}' "$FACTORY_SCRIPT"; then
    pass "factory reset keeps the Reset Cause 0x40 file until systemctl reboot starts"
else
    fail "factory reset touches the Reset Cause 0x40 file between recording it and systemctl reboot"
fi
if grep -Eq "trap .*exit 0.*(INT TERM EXIT|EXIT)" "$SWITCHD"; then
    fail "switchd EXIT trap masks factory reset failure status"
else
    pass "switchd cleanup preserves factory reset failure status"
fi

# Factory Reset은 enable/disable 일부 실패를 성공으로 삼으면 안 된다. 일반 boot 경로는
# 기존 best-effort 동작을 유지하고 strict 호출만 non-zero를 반환한다.
APPLY_JSON="$WORK/apply.json"
APPLY_RUN="$WORK/apply-run"
mkdir -p "$APPLY_RUN"
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
        WIFI_RUN_DIR="$APPLY_RUN" \
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
        WIFI_RUN_DIR="$APPLY_RUN" \
        ENABLE_FAIL='wpa_supplicant@mlan0.service' SYSTEMD_STATE="$STATE" \
        PATH="$PATH" bash "$APPLY_SCRIPT"

# 실제 entrypoint가 검증된 상태기계를 우회하지 않는지 정적으로 고정한다.
for call in factory_snapshot_topology \
            factory_preflight factory_preflight_required_payloads \
            factory_stage_config factory_begin_required_payloads \
            factory_commit_required_payloads factory_rollback_required_payloads \
            factory_restore_service_state \
            factory_verify_required_payloads factory_verify_postconditions; do
    grep -q "$call" "$FACTORY_SCRIPT" \
        && pass "factory entrypoint calls $call" \
        || fail "factory entrypoint missing $call"
done
if awk '
    /factory_snapshot_topology/ && !read_topology { read_topology=NR }
    /factory_preflight_required_payloads/ && !payload_preflight { payload_preflight=NR }
    END { exit !(read_topology && payload_preflight && read_topology < payload_preflight) }
' "$FACTORY_SCRIPT"; then
    pass "factory selector is read before payload preflight can normalize directories"
else
    fail "factory selector is not read before payload preflight mutation"
fi
for transactional_record in \
    '$FACTORY_STAGED_CONFIG|$WIFI_INIT_CONF_JSON|0644' \
    '$FACTORY_STAGED_CONFIG|${WIFI_INIT_CONF_JSON}.bak|0644' \
    '$FACTORY_STAGED_CONFIG|${WIFI_INIT_CONF_JSON}.bak.1|0644'; do
    grep -Fq "$transactional_record" "$FACTORY_SCRIPT" \
        && pass "factory transaction includes JSON generation: $transactional_record" \
        || fail "factory transaction misses JSON generation: $transactional_record"
done
if awk '
    /factory_begin_required_payloads/ && !begin_tx { begin_tx=NR }
    /factory_restore_service_state/ && !services { services=NR }
    /factory_verify_postconditions/ && !postconditions { postconditions=NR }
    /factory_commit_required_payloads/ && !commit_tx { commit_tx=NR }
    END { exit !(begin_tx && services && postconditions && commit_tx && begin_tx < services && services < postconditions && postconditions < commit_tx) }
' "$FACTORY_SCRIPT"; then
    pass "factory transaction retains rollback copies through required postconditions"
else
    fail "factory transaction finalizes before required postconditions"
fi
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
