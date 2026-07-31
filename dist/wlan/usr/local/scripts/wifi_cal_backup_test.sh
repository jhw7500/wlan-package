#!/bin/bash
# wifi_cal_backup.sh standalone tests (hardware/root 불필요)
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
SCRIPT="$SCRIPT_DIR/wifi_cal_backup.sh"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

FW="$WORK/firmware"
CTS="$FW/cts"
BASELINE="$WORK/baseline"
JSON="$WORK/wifi_init_conf.json"
LOCK="$WORK/cal.lock"
mkdir -p "$CTS" "$BASELINE"

PASS=0
FAIL=0

cal_data() {
    local byte="$1"
    printf '01 00 0F 00 08 00\n00 20 59 0F 00 00 00 %s\n' "$byte"
}

write_json() {
    local global="$1" mlan0="${2:-}" mlan1="${3:-}"
    jq -n --arg g "$global" --arg m0 "$mlan0" --arg m1 "$mlan1" '{
        global: {CAL_DATA_CFG: $g},
        mlan0: {CAL_DATA_CFG: $m0},
        mlan1: {CAL_DATA_CFG: $m1}
    }' > "$JSON"
}

run_cal() {
    WIFI_INIT_CONF_JSON="$JSON" \
    WIFI_FIRMWARE_ROOT="$FW" \
    WIFI_CTS_ROOT="$CTS" \
    WIFI_CAL_BASELINE_ROOT="$BASELINE" \
    WIFI_CAL_BACKUP_LOCK="$LOCK" \
    WIFI_CAL_LOGGER=/bin/true \
        "$SCRIPT" "$@"
}

expect() {
    local name="$1"
    shift
    if "$@"; then
        printf 'PASS: %s\n' "$name"
        PASS=$((PASS + 1))
    else
        printf 'FAIL: %s\n' "$name" >&2
        FAIL=$((FAIL + 1))
    fi
}

cal_data AA > "$CTS/WlanCalData_ext.conf"
cp "$CTS/WlanCalData_ext.conf" "$BASELINE/WlanCalData_ext.conf"
write_json cts/WlanCalData_ext.conf
run_cal protect
expect "unchanged package calibration is not backed up" test ! -e "$CTS/WlanCalData_ext.conf.bak"

cp "$SCRIPT_DIR/../../lib/firmware/cts/WlanCalData_ext.conf" "$CTS/vendor-ext.conf"
cp "$SCRIPT_DIR/../../lib/firmware/cts/azure/cal_data.conf" "$CTS/vendor-azure.conf"
write_json cts/vendor-ext.conf cts/vendor-azure.conf
run_cal protect
expect "bundled 1294-byte format passes declared-length validation" test -s "$CTS/vendor-ext.conf.bak"
expect "bundled 606-byte format passes declared-length validation" test -s "$CTS/vendor-azure.conf.bak"

cal_data BB > "$CTS/custom-board.conf"
write_json cts/custom-board.conf
run_cal protect
expect "unknown selected calibration gets backup" test -s "$CTS/custom-board.conf.bak"
expect "unknown selected calibration gets marker" test -s "$CTS/custom-board.conf.user-cal"

printf 'not calibration\n' > "$CTS/custom-board.conf"
run_cal protect
expect "invalid custom calibration restores backup" cmp -s "$CTS/custom-board.conf" "$CTS/custom-board.conf.bak"

printf '01 00 0F 00 08 00\n00 20\n' > "$CTS/custom-board.conf"
run_cal protect
expect "truncated complete-hex prefix restores backup" cmp -s "$CTS/custom-board.conf" "$CTS/custom-board.conf.bak"

cal_data CC > "$CTS/WlanCalData_ext.conf"
run_cal mark "$CTS/WlanCalData_ext.conf"
expect "explicit package-name import is marked custom" test -s "$CTS/WlanCalData_ext.conf.user-cal"
expect "explicit package-name import is backed up" test -s "$CTS/WlanCalData_ext.conf.bak"

cp "$CTS/WlanCalData_ext.conf.bak" "$WORK/user-package-name.conf"
cp "$BASELINE/WlanCalData_ext.conf" "$CTS/WlanCalData_ext.conf"
write_json cts/WlanCalData_ext.conf
run_cal protect
expect "upgrade restores marked user calibration over package bytes" \
    cmp -s "$CTS/WlanCalData_ext.conf" "$WORK/user-package-name.conf"
expect "upgrade preserves marked user backup" \
    cmp -s "$CTS/WlanCalData_ext.conf.bak" "$WORK/user-package-name.conf"

printf 'broken\n' > "$CTS/no-backup.conf"
write_json cts/no-backup.conf
if run_cal protect; then
    expect "invalid custom without backup fails" false
else
    expect "invalid custom without backup fails" true
fi

cal_data DD > "$WORK/outside.conf"
write_json "$WORK/outside.conf"
run_cal protect
expect "outside-cts path is not backed up" test ! -e "$WORK/outside.conf.bak"

write_json cts/custom-board.conf
run_cal reset
expect "reset removes custom backup" test ! -e "$CTS/custom-board.conf.bak"
expect "reset removes custom marker" test ! -e "$CTS/custom-board.conf.user-cal"
expect "reset preserves staged active file" test -e "$CTS/custom-board.conf"

printf '\nResult: %d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
