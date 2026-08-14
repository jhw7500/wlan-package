#!/bin/bash
# wifi_cal_backup.sh standalone tests (hardware/root 불필요)
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
SCRIPT="$SCRIPT_DIR/wifi_cal_backup.sh"
WIFI="$SCRIPT_DIR/wifi.sh"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

FW="$WORK/firmware"
CTS="$FW/cts"
BASELINE="$WORK/baseline"
JSON="$WORK/wifi_init_conf.json"
LOCK="$WORK/cal.lock"
mkdir -p "$CTS" "$BASELINE"
BINDIR="$WORK/bin"
mkdir -p "$BINDIR"
printf '#!/bin/sh\nexit 0\n' > "$BINDIR/logger"
chmod +x "$BINDIR/logger"

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
    WIFI_CAL_SYNC_CMD="${CAL_SYNC_CMD:-sync}" \
        "$SCRIPT" "$@"
}

run_wifi_cal() {
    WIFI_INIT_CONF_JSON="$JSON" \
    WIFI_CAL_BACKUP_SH="$SCRIPT" \
    WIFI_CTS_DIR="$CTS" \
    WIFI_FIRMWARE_ROOT="$FW" \
    WIFI_CTS_ROOT="$CTS" \
    WIFI_CAL_BASELINE_ROOT="$BASELINE" \
    WIFI_CAL_BACKUP_LOCK="$LOCK" \
    WIFI_CAL_LOGGER=/bin/true \
    PATH="$BINDIR:$PATH" \
        bash "$WIFI" cal "$1"
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

# marker 없는 과거 .bak은 package CAL 손상 시 되살아나면 안 된다. reset은 선택된
# package CAL active와 backup을 독립 baseline으로 함께 재시드한다.
cal_data BB > "$CTS/WlanCalData_ext.conf.bak"
run_cal reset
expect "reset seeds selected package calibration from baseline" \
    cmp -s "$CTS/WlanCalData_ext.conf" "$BASELINE/WlanCalData_ext.conf"
expect "reset replaces stale unmarked package calibration backup" \
    cmp -s "$CTS/WlanCalData_ext.conf.bak" "$BASELINE/WlanCalData_ext.conf"

cat > "$BINDIR/sync-fail" <<'EOF'
#!/bin/sh
exit 1
EOF
chmod +x "$BINDIR/sync-fail"
cal_data BB > "$CTS/WlanCalData_ext.conf.bak"
CAL_SYNC_CMD="$BINDIR/sync-fail"
if run_cal reset; then
    expect "reset propagates calibration sync failure" false
else
expect "reset propagates calibration sync failure" true
fi
unset CAL_SYNC_CMD
expect "failed calibration reset leaves no temp artifacts" \
    sh -c '! find "$1" -type f -name "*.tmp.*" -print -quit | grep -q .' _ "$CTS"

# legacy/MFG에서 marker 없이 선택된 custom CAL도 reset 단계에서 검증·보호해야 한다.
cal_data BC > "$CTS/unmarked-selected-valid.conf"
write_json cts/unmarked-selected-valid.conf
run_cal reset
expect "reset protects selected unmarked valid calibration" \
    test -s "$CTS/unmarked-selected-valid.conf.bak"
expect "reset marks selected unmarked valid calibration" \
    test -s "$CTS/unmarked-selected-valid.conf.user-cal"
printf 'broken unmarked selected calibration\n' > "$CTS/unmarked-selected-invalid.conf"
write_json cts/unmarked-selected-invalid.conf
if run_cal reset; then
    expect "reset rejects selected unmarked invalid calibration" false
else
    expect "reset rejects selected unmarked invalid calibration" true
fi

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

mkdir -p "$WORK/import"
cp "$CTS/WlanCalData_ext.conf" "$WORK/before-invalid-import.conf"
printf '01 00 0F 00 08 00\n00 20\n' > "$WORK/import/WlanCalData_ext.conf"
if run_wifi_cal "$WORK/import/WlanCalData_ext.conf" >/dev/null 2>&1; then
    expect "CLI rejects truncated calibration before rename" false
else
    expect "CLI rejects truncated calibration before rename" true
fi
expect "CLI invalid same-basename import preserves active file" \
    cmp -s "$CTS/WlanCalData_ext.conf" "$WORK/before-invalid-import.conf"
expect "CLI invalid same-basename import preserves JSON selection" \
    test "$(jq -r '.global.CAL_DATA_CFG' "$JSON")" = "cts/WlanCalData_ext.conf"

cal_data DD > "$WORK/import/custom-new.conf"
run_wifi_cal "$WORK/import/custom-new.conf" >/dev/null 2>&1
expect "CLI valid calibration stages active file" \
    cmp -s "$CTS/custom-new.conf" "$WORK/import/custom-new.conf"
expect "CLI valid calibration creates backup" test -s "$CTS/custom-new.conf.bak"
expect "CLI valid calibration creates marker" test -s "$CTS/custom-new.conf.user-cal"
expect "CLI valid calibration updates JSON after protection" \
    test "$(jq -r '.global.CAL_DATA_CFG' "$JSON")" = "cts/custom-new.conf"

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
expect "reset keeps selected custom backup" test -s "$CTS/custom-board.conf.bak"
expect "reset keeps selected custom marker" test -s "$CTS/custom-board.conf.user-cal"
expect "reset preserves selected active file" test -e "$CTS/custom-board.conf"

# 보존하기로 한 production CAL은 reset 도중 active가 깨져 있어도 정상 backup에서
# 먼저 복구되어야 한다. marker/backup을 지우고 다음 부팅을 실패시키면 안 된다.
cal_data EE > "$CTS/custom-reset-recovery.conf"
write_json cts/custom-reset-recovery.conf
run_cal mark "$CTS/custom-reset-recovery.conf"
printf 'broken during reset\n' > "$CTS/custom-reset-recovery.conf"
run_cal reset
expect "reset recovers selected custom calibration from backup" \
    cmp -s "$CTS/custom-reset-recovery.conf" "$CTS/custom-reset-recovery.conf.bak"
expect "reset retains recovered custom calibration marker" \
    test -s "$CTS/custom-reset-recovery.conf.user-cal"

# 패키지 이름과 같은 파일에 명시적으로 반입한 production CAL도 보존 계약은 같다.
cal_data AB > "$CTS/WlanCalData_ext.conf"
run_cal mark "$CTS/WlanCalData_ext.conf"
cp "$CTS/WlanCalData_ext.conf" "$WORK/selected-same-name.expected"
write_json cts/WlanCalData_ext.conf
run_cal reset
expect "reset keeps selected same-basename custom calibration" \
    cmp -s "$CTS/WlanCalData_ext.conf" "$WORK/selected-same-name.expected"
expect "reset keeps selected same-basename custom backup" \
    cmp -s "$CTS/WlanCalData_ext.conf.bak" "$WORK/selected-same-name.expected"
expect "reset keeps selected same-basename custom marker" \
    test -s "$CTS/WlanCalData_ext.conf.user-cal"

# 현재 선택되지 않은 과거 사용자 CAL만 reset 대상이다.
cal_data FF > "$CTS/obsolete-user-cal.conf"
run_cal mark "$CTS/obsolete-user-cal.conf"
cal_data A1 > "$CTS/current-selected.conf"
run_cal mark "$CTS/current-selected.conf"
write_json cts/current-selected.conf
run_cal reset
expect "reset removes unselected custom backup" test ! -e "$CTS/obsolete-user-cal.conf.bak"
expect "reset removes unselected custom marker" test ! -e "$CTS/obsolete-user-cal.conf.user-cal"
expect "reset removes unselected custom active file" test ! -e "$CTS/obsolete-user-cal.conf"

# package basename의 custom CAL이 현재 선택되지 않았다면 공장 baseline으로 완전히
# 되돌린다. marker만 지우고 active bytes를 남기면 나중 선택 시 pre-reset 값이 부활한다.
cal_data CD > "$CTS/WlanCalData_ext.conf"
run_cal mark "$CTS/WlanCalData_ext.conf"
write_json cts/current-selected.conf
run_cal reset
expect "reset restores unselected package-name CAL active to baseline" \
    cmp -s "$CTS/WlanCalData_ext.conf" "$BASELINE/WlanCalData_ext.conf"
expect "reset seeds unselected package-name CAL backup from baseline" \
    cmp -s "$CTS/WlanCalData_ext.conf.bak" "$BASELINE/WlanCalData_ext.conf"
expect "reset removes unselected package-name CAL marker" \
    test ! -e "$CTS/WlanCalData_ext.conf.user-cal"

printf '\nResult: %d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
