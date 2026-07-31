#!/bin/bash
# wifi_config_backup.sh standalone tests (hardware/root 불필요)
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
SCRIPT="$SCRIPT_DIR/wifi_config_backup.sh"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

ACTIVE="$WORK/wifi_init_conf.json"
DEFAULT="$WORK/default.json"
BACKUP="$ACTIVE.bak"
PREVIOUS="$ACTIVE.bak.1"
LOCK="$WORK/backup.lock"
PASS=0
FAIL=0

valid_json() {
    local marker="$1"
    jq -n --arg marker "$marker" '{
        global: {marker: $marker},
        mlan0: {},
        mlan1: {},
        mac: {},
        wbridge: {}
    }'
}

run_backup() {
    WIFI_INIT_CONF_JSON="$ACTIVE" \
    WIFI_INIT_CONF_DEFAULT="$DEFAULT" \
    WIFI_INIT_CONF_BACKUP="$BACKUP" \
    WIFI_INIT_CONF_PREVIOUS="$PREVIOUS" \
    WIFI_INIT_CONF_LOCK="$LOCK" \
    WIFI_BOARD_CONFIG_SH=/bin/true \
    WIFI_CONFIG_LOGGER=/bin/true \
        "$SCRIPT" "$@"
}

marker_of() {
    jq -r '.global.marker' "$1"
}

expect_eq() {
    local name="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then
        printf 'PASS: %s\n' "$name"
        PASS=$((PASS + 1))
    else
        printf 'FAIL: %s (expected=%s actual=%s)\n' "$name" "$expected" "$actual" >&2
        FAIL=$((FAIL + 1))
    fi
}

valid_json default > "$DEFAULT"
valid_json first > "$ACTIVE"
run_backup commit
expect_eq "commit creates newest backup" first "$(marker_of "$BACKUP")"

run_backup commit
if [ ! -e "$PREVIOUS" ]; then
    expect_eq "identical commit does not rotate" absent absent
else
    expect_eq "identical commit does not rotate" absent present
fi

valid_json second > "$ACTIVE"
run_backup commit
expect_eq "changed commit updates newest" second "$(marker_of "$BACKUP")"
expect_eq "changed commit rotates prior newest" first "$(marker_of "$PREVIOUS")"

printf '{"global":' > "$ACTIVE"
run_backup restore
expect_eq "malformed active restores newest" second "$(marker_of "$ACTIVE")"

jq -n '{global:{},mlan0:{},mlan1:{}}' > "$ACTIVE"
run_backup restore
expect_eq "schema-incomplete active restores newest" second "$(marker_of "$ACTIVE")"

printf 'broken\n' > "$ACTIVE"
printf 'broken\n' > "$BACKUP"
run_backup restore
expect_eq "invalid newest falls back to previous" first "$(marker_of "$ACTIVE")"

printf 'broken\n' > "$ACTIVE"
printf 'broken\n' > "$BACKUP"
printf 'broken\n' > "$PREVIOUS"
run_backup restore
expect_eq "invalid backups fall back to default" default "$(marker_of "$ACTIVE")"

valid_json reset > "$ACTIVE"
valid_json stale-newest > "$BACKUP"
valid_json stale-previous > "$PREVIOUS"
run_backup reset
expect_eq "reset seeds factory JSON" reset "$(marker_of "$BACKUP")"
if [ ! -e "$PREVIOUS" ]; then
    expect_eq "reset removes pre-reset generation" absent absent
else
    expect_eq "reset removes pre-reset generation" absent present
fi

printf 'broken\n' > "$ACTIVE"
printf 'broken\n' > "$BACKUP"
printf 'broken\n' > "$PREVIOUS"
printf 'broken\n' > "$DEFAULT"
if run_backup restore; then
    expect_eq "unrecoverable JSON returns failure" failure success
else
    expect_eq "unrecoverable JSON returns failure" failure failure
fi

printf '\nResult: %d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
