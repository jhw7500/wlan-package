#!/bin/bash
# wifi.sh/opc_wlan_apply.sh wpa conf writer integration tests (hardware/root free)
set -u

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
WIFI_SH="$SCRIPT_DIR/wifi.sh"
OPC_SH="$SCRIPT_DIR/opc_wlan_apply.sh"
TD=$(mktemp -d)
trap 'rm -rf "$TD"' EXIT
BIN="$TD/bin"
WPA_DIR="$TD/wpa"
STATE_DIR="$TD/state"
RUN_DIR="$TD/run"
CALL_LOG="$TD/calls.log"
mkdir -p "$BIN" "$WPA_DIR" "$STATE_DIR" "$RUN_DIR"
: > "$CALL_LOG"

cat > "$BIN/logger" <<'EOF'
#!/bin/sh
exit 0
EOF
cat > "$BIN/sync" <<'EOF'
#!/bin/sh
printf 'sync %s\n' "$*" >> "$CALL_LOG"
mode=$(cat "$STATE_DIR/sync-mode" 2>/dev/null || echo ok)

# Path sync 실패 직후 호출되는 global `sync` fallback도 같은 장애로 실패시킨다.
if [ "$#" -eq 0 ] && [ -f "$STATE_DIR/sync-fallback-pending" ]; then
    rm -f "$STATE_DIR/sync-fallback-pending"
    exit 1
fi

fail_once() {
    [ ! -f "$STATE_DIR/sync-mode-fired" ] || return 1
    : > "$STATE_DIR/sync-mode-fired"
    : > "$STATE_DIR/sync-fallback-pending"
    exit 1
}

case "$mode" in
  fail-stage)
    case "${1:-}" in
      "$WPA_DIR"/wpa_supplicant-mlan0.conf.*)
        case "${1:-}" in *.bak.*|*.rollback.*) ;; *) fail_once ;; esac
        ;;
    esac
    ;;
  fail-installed)
    [ "${1:-}" != "$WPA_DIR/wpa_supplicant-mlan0.conf" ] || fail_once
    ;;
  fail-dir)
    [ "${1:-}" != "$WPA_DIR" ] || fail_once
    ;;
  fail-rollback-file)
    if [ -f "$STATE_DIR/rollback-started" ] \
       && [ "${1:-}" = "$WPA_DIR/wpa_supplicant-mlan0.conf" ]; then
        fail_once
    fi
    ;;
  fail-rollback-dir)
    if [ -f "$STATE_DIR/rollback-started" ] && [ "${1:-}" = "$WPA_DIR" ]; then
        fail_once
    fi
    ;;
esac
exit 0
EOF
cat > "$BIN/install" <<'EOF'
#!/bin/sh
while [ "$#" -gt 2 ]; do shift; done
if [ "${INSTALL_MODE:-}" = "partial-fail" ]; then
    printf 'PARTIAL\n' > "$2"
    exit 1
fi
cp "$1" "$2"
EOF
cat > "$BIN/mv" <<'EOF'
#!/bin/sh
printf 'mv %s\n' "$*" >> "$CALL_LOG"
mode=$(cat "$STATE_DIR/mv-mode" 2>/dev/null || echo ok)
case "$mode:$*" in
  fail-rollback:*\.bak.*|fail-rollback:*\.rollback.*)
    exit 1
    ;;
  term-after-install:*\.bak.*|term-after-install:*\.rollback.*)
    exec /bin/mv "$@"
    ;;
  term-after-install:*)
    /bin/mv "$@"
    rc=$?
    if [ "$rc" -eq 0 ]; then
      kill -TERM "$PPID"
      sleep 0.1
    fi
    exit "$rc"
    ;;
esac
case "$*" in *\.rollback.*) : > "$STATE_DIR/rollback-started" ;; esac
exec /bin/mv "$@"
EOF
cat > "$BIN/systemctl" <<'EOF'
#!/bin/sh
exit 0
EOF
cat > "$BIN/wpa_cli" <<'EOF'
#!/bin/sh
printf 'wpa_cli %s\n' "$*" >> "$CALL_LOG"

# Model the deployed `wpa_cli -a ACTION -B -P PIDFILE` daemon interface.
# The private action monitor is a real background process so the harness can
# verify that wifi.sh bounds and removes it rather than merely deleting files.
case " $* " in
  *" -a "*)
    action=""; pidfile=""; background=0
    while [ "$#" -gt 0 ]; do
      case "$1" in
        -a) action="$2"; shift 2 ;;
        -P) pidfile="$2"; shift 2 ;;
        -B) background=1; shift ;;
        *) shift ;;
      esac
    done
    [ -n "$action" ] && [ -n "$pidfile" ] && [ "$background" = "1" ] || exit 2
    printf '%s\n' "$action" > "$STATE_DIR/monitor-action"
    printf '%s\n' "$pidfile" > "$STATE_DIR/last-monitor-pidfile"
    dirname "$pidfile" > "$STATE_DIR/last-monitor-dir"
    mode=$(cat "$STATE_DIR/monitor-mode" 2>/dev/null || echo matching)
    if [ "$mode" = "setup-fail" ]; then
      printf 'FAIL\n'
      exit 1
    fi
    # A native wpa_cli daemon has argv[0]="wpa_cli" even when PATH resolved
    # the executable.  Model that exact /proc identity for safe PID handling.
    bash -c 'exec -a wpa_cli sleep 60' >/dev/null 2>&1 &
    monitor_pid=$!
    printf '%s\n' "$monitor_pid" > "$pidfile"
    printf '%s\n' "$monitor_pid" > "$STATE_DIR/last-monitor-pid"
    printf 'OK\n'
    exit 0
    ;;
esac

case "$*" in
  *" status"*)
    count=$(cat "$STATE_DIR/status-count" 2>/dev/null || echo 0)
    count=$((count + 1))
    printf '%s\n' "$count" > "$STATE_DIR/status-count"
    mode=$(cat "$STATE_DIR/status-mode" 2>/dev/null || echo base)
    ssid=Base; id=0; freq=5180; state=COMPLETED
    case "$mode" in
      stale-then-target)
        if [ "$count" -le 2 ]; then ssid=OldNet; freq=2412; else ssid=NewNet; freq=5180; fi ;;
      wrong-ssid) ssid=OldNet; freq=5180 ;;
      wrong-freq) ssid=NewNet; freq=2412 ;;
      mode-a-current)
        if [ "$count" -eq 1 ]; then
          ssid=Office; id=1
        elif [ "$count" -eq 2 ]; then
          ssid=Base; id=0
        else
          ssid=Office; id=1
        fi ;;
      mode-a-steady)
        ssid=Office; id=1 ;;
      mode-a-wait)
        ssid=Office; id=1
        [ "$count" -eq 1 ] || state=DISCONNECTED ;;
      no-current)
        if [ "$count" -eq 1 ]; then state=DISCONNECTED; ssid=; id=; freq=; fi ;;
    esac
    cat <<EOT
wpa_state=$state
ssid=$ssid
id=$id
freq=$freq
EOT
    ;;
  *" reconfigure"*)
    count=$(cat "$STATE_DIR/reconfigure-count" 2>/dev/null || echo 0)
    count=$((count + 1))
    printf '%s\n' "$count" > "$STATE_DIR/reconfigure-count"
    mode=$(cat "$STATE_DIR/reconfigure-mode" 2>/dev/null || echo ok)
    if [ "$mode" = "rc-ok" ]; then
      printf 'OK\n'
      exit 9
    elif [ "$mode" = "fail-first" ] && [ "$count" -eq 1 ]; then
      printf 'FAIL\n'
    else
      printf 'OK\n'
    fi
    ;;
  *" enable_network all"*)
    mode=$(cat "$STATE_DIR/enable-mode" 2>/dev/null || echo ok)
    if [ "$mode" = "rc-ok" ]; then printf 'OK\n'; exit 9
    elif [ "$mode" = "fail" ]; then printf 'FAIL\n'
    else printf 'OK\n'; fi
    ;;
  *" select_network "*)
    mode=$(cat "$STATE_DIR/select-mode" 2>/dev/null || echo ok)
    if [ "$mode" = "rc-ok" ]; then printf 'OK\n'; exit 9
    elif [ "$mode" = "fail" ]; then printf 'FAIL\n'
    else printf 'OK\n'; fi
    ;;
  *" reassociate"*|*" reconnect"*)
    action=$(cat "$STATE_DIR/monitor-action" 2>/dev/null || true)
    mode=$(cat "$STATE_DIR/monitor-mode" 2>/dev/null || echo off)
    if [ -n "$action" ] && [ -x "$action" ]; then
      case "$mode" in
        matching) WPA_ID=1 "$action" mlan0 CONNECTED ;;
        wrong-id) WPA_ID=0 "$action" mlan0 CONNECTED ;;
      esac
    fi
    mode=$(cat "$STATE_DIR/assoc-mode" 2>/dev/null || echo ok)
    if [ "$mode" = "rc-ok" ]; then printf 'OK\n'; exit 9
    elif [ "$mode" = "fail" ]; then printf 'FAIL\n'
    else printf 'OK\n'; fi
    ;;
  *) printf 'OK\n' ;;
esac
EOF
chmod +x "$BIN"/*
export PATH="$BIN:$PATH"
export CALL_LOG STATE_DIR WPA_DIR WIFI_RUN_DIR="$RUN_DIR"

PASS=0
FAIL=0
pass() { PASS=$((PASS + 1)); echo "PASS: $*"; }
fail() { FAIL=$((FAIL + 1)); echo "FAIL: $*"; }
check_equal() {
    local desc="$1" got="$2" expected="$3"
    if [ "$got" = "$expected" ]; then pass "$desc"; else fail "$desc (expected=$expected got=$got)"; fi
}

write_mode_a_legacy() {
    cat > "$WPA_DIR/wpa_supplicant-mlan0.conf" <<'EOF'
update_config=1
network={
    ssid="Base"
    key_mgmt=WPA-PSK
    psk="12345678"
    scan_freq=2412
    freq_list=2412
}
# >>> wifi_extra_ssid auto-generated (do not edit) >>>
network={
    ssid="Office"
    key_mgmt=WPA-PSK
    psk="12345678"
    scan_freq=5180
    freq_list=5180
}
# <<< wifi_extra_ssid auto-generated <<<
EOF
}

write_mode_b_legacy() {
    cat > "$WPA_DIR/wpa_supplicant-mlan0.conf" <<'EOF'
update_config=1
network={
    ssid="OldNet"
    key_mgmt=WPA-PSK
    psk="12345678"
    scan_freq=2412
    freq_list=2412
}
EOF
}

set_status_mode() {
    printf '%s\n' "$1" > "$STATE_DIR/status-mode"
    rm -f "$STATE_DIR/status-count"
}

set_reconfigure_mode() {
    printf '%s\n' "$1" > "$STATE_DIR/reconfigure-mode"
    rm -f "$STATE_DIR/reconfigure-count"
}

set_assoc_mode() {
    printf '%s\n' "$1" > "$STATE_DIR/assoc-mode"
}

set_monitor_mode() {
    printf '%s\n' "$1" > "$STATE_DIR/monitor-mode"
    rm -f "$STATE_DIR/monitor-action" "$STATE_DIR/last-monitor-pid" \
          "$STATE_DIR/last-monitor-pidfile" "$STATE_DIR/last-monitor-dir"
}

monitor_process_running() {
    local pid="$1" state
    kill -0 "$pid" 2>/dev/null || return 1
    state=$(awk '{print $3}' "/proc/$pid/stat" 2>/dev/null || true)
    [ "$state" != "Z" ]
}

check_monitor_cleaned() {
    local desc="$1" pid="" dir=""
    pid=$(cat "$STATE_DIR/last-monitor-pid" 2>/dev/null || true)
    dir=$(cat "$STATE_DIR/last-monitor-dir" 2>/dev/null || true)
    # Watchdog detection (0.1s) plus graceful TERM allowance (1s) can cross a
    # one-second assertion boundary; allow three seconds while still proving a
    # tight bound well below the fake daemon's unbounded lifetime.
    for _wait in $(seq 1 30); do
        [ -z "$pid" ] || ! monitor_process_running "$pid" || { sleep 0.1; continue; }
        break
    done
    if { [ -z "$pid" ] || ! monitor_process_running "$pid"; } \
       && { [ -z "$dir" ] || [ ! -e "$dir" ]; } \
       && ! find "$RUN_DIR" -maxdepth 1 -name 'mlan0.connect-monitor.*' -print -quit 2>/dev/null | grep -q .; then
        pass "$desc"
    else
        fail "$desc (pid=${pid:-missing} dir=${dir:-missing})"
    fi
}

check_monitor_attached_before_request() {
    local desc="$1" attach request
    attach=$(grep -n ' -a .* -B -P ' "$CALL_LOG" | head -1 | cut -d: -f1)
    request=$(grep -n 'reassociate$' "$CALL_LOG" | head -1 | cut -d: -f1)
    if [ -n "$attach" ] && [ -n "$request" ] && [ "$attach" -lt "$request" ]; then
        pass "$desc"
    else
        fail "$desc (attach=${attach:-missing} request=${request:-missing})"
    fi
}

set_mv_mode() {
    printf '%s\n' "$1" > "$STATE_DIR/mv-mode"
}

set_sync_mode() {
    printf '%s\n' "$1" > "$STATE_DIR/sync-mode"
    rm -f "$STATE_DIR/sync-mode-fired" "$STATE_DIR/sync-fallback-pending" \
          "$STATE_DIR/rollback-started"
}

set_boot_policy() {
    local roam_enabled="$1" generate="$2" extras_json="${3:-[]}" bgscan_enabled="${4:-true}"
    cat > "$RUN_DIR/mlan0.roam-policy.json" <<EOF
{"version":1,"iface":"mlan0","roaming_enabled":$roam_enabled,"bgscan_enabled":$bgscan_enabled,"generate_network_blocks":$generate,"extra_ssids":$extras_json}
EOF
}

count_global_freq() {
    awk '
      /^[[:space:]]*#/ { next }
      /^[[:space:]]*network[[:space:]]*=/ { in_net=1 }
      !in_net && /^[[:space:]]*freq_list[[:space:]]*=/ { n++ }
      in_net && /^[[:space:]]*}/ { in_net=0 }
      END { print n+0 }
    ' "$1"
}

count_block_freq() {
    awk -v expected="$2" '
      /^[[:space:]]*network[[:space:]]*=/ { in_net=1; next }
      in_net && $0 ~ "^[[:space:]]*freq_list=" expected "$" { n++ }
      in_net && /^[[:space:]]*}/ { in_net=0 }
      END { print n+0 }
    ' "$1"
}

echo "=== wifi freq canonical writer ==="
set_boot_policy true true '["Office"]'
write_mode_a_legacy
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" bash "$WIFI_SH" 0 freq 5180 5200 >/dev/null 2>&1
rc=$?
CONF="$WPA_DIR/wpa_supplicant-mlan0.conf"
check_equal "wifi freq exits zero" "$rc" "0"
check_equal "wifi freq writes one global list" "$(count_global_freq "$CONF")" "1"
check_equal "wifi freq writes same list to both blocks" \
    "$(count_block_freq "$CONF" '5180 5200')" "2"
check_equal "wifi freq removes scan_freq" \
    "$(grep -Ec '^[[:space:]]*scan_freq[[:space:]]*=' "$CONF" || true)" "0"
check_equal "wifi freq forces update_config=0" \
    "$(grep -Ec '^update_config=0$' "$CONF" || true)" "1"
check_equal "wifi freq remains persist-only" \
    "$(grep -c 'reconfigure' "$CALL_LOG" || true)" "0"
if [ -f "$RUN_DIR/mlan0.wpa-conf.lock" ]; then
    pass "wifi writer acquires shared per-interface lock"
else
    fail "wifi writer acquires shared per-interface lock"
fi

write_mode_b_legacy
set_boot_policy false false
cp "$CONF" "$TD/atomic-original.conf"
INSTALL_MODE=partial-fail WPA_CONF_DIR="$WPA_DIR" \
    bash "$WIFI_SH" 0 freq 5180 >/dev/null 2>&1
rc=$?
if [ "$rc" -ne 0 ]; then pass "wifi atomic writer propagates staging failure"; else fail "wifi atomic writer propagates staging failure"; fi
if cmp -s "$CONF" "$TD/atomic-original.conf"; then
    pass "wifi staging failure leaves original conf byte-exact"
else
    fail "wifi staging failure leaves original conf byte-exact"
fi

for _sync_failure in fail-stage fail-installed fail-dir; do
    write_mode_b_legacy
    cp "$CONF" "$TD/wifi-${_sync_failure}-original.conf"
    set_sync_mode "$_sync_failure"
    : > "$CALL_LOG"
    WPA_CONF_DIR="$WPA_DIR" bash "$WIFI_SH" 0 freq 5180 >/dev/null 2>&1
    rc=$?
    if [ "$rc" -ne 0 ]; then
        pass "wifi freq ${_sync_failure} is fatal"
    else
        fail "wifi freq ${_sync_failure} must be fatal"
    fi
    if [ "$_sync_failure" = "fail-stage" ]; then
        if cmp -s "$CONF" "$TD/wifi-${_sync_failure}-original.conf"; then
            pass "wifi freq staging sync failure leaves original byte-exact"
        else
            fail "wifi freq staging sync failure must leave original byte-exact"
        fi
        check_equal "wifi freq staging sync failure performs no install rename" \
            "$(grep -Ec "^mv -f .* $CONF$" "$CALL_LOG" || true)" "0"
        if ! find "$WPA_DIR" -maxdepth 1 -name 'wpa_supplicant-mlan0.conf.install.*' -print -quit | grep -q .; then
            pass "wifi freq staging sync failure removes stage"
        else
            fail "wifi freq staging sync failure must remove stage"
        fi
    fi
done
set_sync_mode ok

echo ""
echo "=== wifi connect target-aware writer ==="
set_boot_policy false false
write_mode_b_legacy
set_status_mode stale-then-target
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" ASSOC_TIMEOUT_DEFAULT=1 \
    bash "$WIFI_SH" 0 connect NewNet 5180 >/dev/null 2>&1
rc=$?
check_equal "wifi connect waits past stale COMPLETED" "$rc" "0"
if [ "$(cat "$STATE_DIR/status-count" 2>/dev/null || echo 0)" -ge 3 ]; then
    pass "wifi connect polls until requested SSID"
else
    fail "wifi connect polls until requested SSID"
fi
check_equal "wifi connect writes one global list" "$(count_global_freq "$CONF")" "1"
check_equal "wifi connect writes canonical block list" \
    "$(count_block_freq "$CONF" '5180')" "1"
check_equal "wifi connect removes scan_freq" \
    "$(grep -Ec '^[[:space:]]*scan_freq[[:space:]]*=' "$CONF" || true)" "0"

for _sync_failure in fail-installed fail-dir; do
    write_mode_b_legacy
    set_status_mode stale-then-target
    set_reconfigure_mode ok
    set_sync_mode "$_sync_failure"
    : > "$CALL_LOG"
    WPA_CONF_DIR="$WPA_DIR" ASSOC_TIMEOUT_DEFAULT=1 \
        bash "$WIFI_SH" 0 connect NewNet 5180 >/dev/null 2>&1
    rc=$?
    if [ "$rc" -ne 0 ]; then
        pass "wifi connect ${_sync_failure} is fatal before live apply"
    else
        fail "wifi connect ${_sync_failure} must be fatal before live apply"
    fi
    check_equal "wifi connect ${_sync_failure} does not reconfigure" \
        "$(grep -c 'reconfigure$' "$CALL_LOG" || true)" "0"
done
set_sync_mode ok

write_mode_b_legacy
set_reconfigure_mode rc-ok
set_status_mode stale-then-target
WPA_CONF_DIR="$WPA_DIR" ASSOC_TIMEOUT_DEFAULT=1 \
    bash "$WIFI_SH" 0 connect NewNet 5180 >/dev/null 2>&1
check_equal "wifi connect rejects reconfigure stdout OK with nonzero rc" "$?" "7"
set_reconfigure_mode ok

write_mode_b_legacy
set_status_mode wrong-ssid
WPA_CONF_DIR="$WPA_DIR" ASSOC_TIMEOUT_DEFAULT=1 \
    bash "$WIFI_SH" 0 connect NewNet 5180 >/dev/null 2>&1
check_equal "wifi connect rejects COMPLETED on wrong SSID" "$?" "8"

write_mode_b_legacy
set_status_mode wrong-freq
WPA_CONF_DIR="$WPA_DIR" ASSOC_TIMEOUT_DEFAULT=1 \
    bash "$WIFI_SH" 0 connect NewNet 5180 >/dev/null 2>&1
check_equal "wifi connect rejects requested SSID on wrong frequency" "$?" "8"

write_mode_a_legacy
set_boot_policy true true '["Office"]'
before=$(sha256sum "$CONF" | awk '{print $1}')
WPA_CONF_DIR="$WPA_DIR" bash "$WIFI_SH" 0 connect NewNet 5180 >/dev/null 2>&1
rc=$?
after=$(sha256sum "$CONF" | awk '{print $1}')
check_equal "wifi connect explicit SSID remains rejected in Mode A" "$rc" "1"
check_equal "Mode A rejection leaves conf unchanged" "$after" "$before"

write_mode_b_legacy
set_boot_policy true true '[]'
before=$(sha256sum "$CONF" | awk '{print $1}')
WPA_CONF_DIR="$WPA_DIR" bash "$WIFI_SH" 0 connect NewNet 5180 >/dev/null 2>&1
rc=$?
after=$(sha256sum "$CONF" | awk '{print $1}')
check_equal "Mode A empty-extra snapshot rejects explicit connect" "$rc" "1"
check_equal "Mode A empty-extra connect leaves conf unchanged" "$after" "$before"
WPA_CONF_DIR="$WPA_DIR" bash "$WIFI_SH" 0 ssid NewNet >/dev/null 2>&1
check_equal "Mode A empty-extra snapshot rejects wifi ssid" "$?" "1"

write_mode_a_legacy
set_boot_policy true true '["Office"]'
set_status_mode mode-a-steady
set_monitor_mode stale
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" ASSOC_TIMEOUT_DEFAULT=1 \
    bash "$WIFI_SH" 0 connect >/dev/null 2>&1
rc=$?
check_equal "Mode A no-arg reconnect rejects stale COMPLETED without fresh event" "$rc" "8"
check_monitor_attached_before_request "Mode A action monitor attaches before reassociate"
check_monitor_cleaned "Mode A stale-event timeout removes monitor resources"
check_equal "Mode A no-arg reconnect issues one owner-neutral reassociate" \
    "$(grep -c 'reassociate$' "$CALL_LOG" || true)" "1"
check_equal "Mode A no-arg reconnect never selects a network block" \
    "$(grep -c 'select_network' "$CALL_LOG" || true)" "0"
check_equal "Mode A no-arg reconnect never enables network blocks" \
    "$(grep -c 'enable_network' "$CALL_LOG" || true)" "0"

write_mode_a_legacy
set_status_mode mode-a-steady
set_monitor_mode wrong-id
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" ASSOC_TIMEOUT_DEFAULT=1 \
    bash "$WIFI_SH" 0 connect >/dev/null 2>&1
rc=$?
check_equal "Mode A no-arg reconnect rejects fresh event for another id" "$rc" "8"
check_monitor_cleaned "Mode A wrong-id timeout removes monitor resources"
check_equal "Mode A wrong-id reconnect never selects a network block" \
    "$(grep -c 'select_network' "$CALL_LOG" || true)" "0"
check_equal "Mode A wrong-id reconnect never enables network blocks" \
    "$(grep -c 'enable_network' "$CALL_LOG" || true)" "0"

write_mode_a_legacy
set_status_mode mode-a-steady
set_monitor_mode matching
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" ASSOC_TIMEOUT_DEFAULT=1 \
    bash "$WIFI_SH" 0 connect >/dev/null 2>&1
rc=$?
check_equal "Mode A no-arg reconnect accepts fresh matching event and status" "$rc" "0"
check_monitor_attached_before_request "Mode A matching monitor attaches before reassociate"
check_monitor_cleaned "Mode A success removes monitor resources"
check_equal "Mode A matching reconnect never selects a network block" \
    "$(grep -c 'select_network' "$CALL_LOG" || true)" "0"
check_equal "Mode A matching reconnect never enables network blocks" \
    "$(grep -c 'enable_network' "$CALL_LOG" || true)" "0"

write_mode_a_legacy
set_status_mode mode-a-steady
set_monitor_mode setup-fail
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" ASSOC_TIMEOUT_DEFAULT=1 \
    bash "$WIFI_SH" 0 connect >/dev/null 2>&1
rc=$?
check_equal "Mode A monitor setup failure is fail-closed" "$rc" "7"
check_equal "Mode A monitor setup failure sends no reconnect request" \
    "$(grep -Ec 'reassociate$|reconnect$' "$CALL_LOG" || true)" "0"
check_monitor_cleaned "Mode A setup failure removes monitor resources"

write_mode_a_legacy
set_status_mode mode-a-steady
set_monitor_mode matching
set_assoc_mode fail
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" ASSOC_TIMEOUT_DEFAULT=1 \
    bash "$WIFI_SH" 0 connect >/dev/null 2>&1
rc=$?
check_equal "Mode A rejected reconnect command exits 7" "$rc" "7"
check_monitor_cleaned "Mode A command rejection removes monitor resources"
set_assoc_mode ok

write_mode_a_legacy
set_status_mode mode-a-wait
set_monitor_mode stale
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" ASSOC_TIMEOUT_DEFAULT=10 \
    bash "$WIFI_SH" 0 connect >/dev/null 2>&1 &
_wifi_pid=$!
for _wait in $(seq 1 20); do
    [ -s "$STATE_DIR/last-monitor-pid" ] && break
    sleep 0.05
done
if [ -s "$STATE_DIR/last-monitor-pid" ]; then
    pass "Mode A signal test attaches monitor"
else
    fail "Mode A signal test must attach monitor"
fi
kill -TERM "$_wifi_pid" 2>/dev/null || true
wait "$_wifi_pid" 2>/dev/null
rc=$?
if [ "$rc" -ne 0 ]; then pass "Mode A TERM exits nonzero"; else fail "Mode A TERM must exit nonzero"; fi
check_monitor_cleaned "Mode A TERM removes monitor resources"

write_mode_a_legacy
set_status_mode mode-a-wait
set_monitor_mode stale
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" ASSOC_TIMEOUT_DEFAULT=10 \
    bash "$WIFI_SH" 0 connect >/dev/null 2>&1 &
_wifi_pid=$!
for _wait in $(seq 1 20); do
    [ -s "$STATE_DIR/last-monitor-pid" ] && break
    sleep 0.05
done
if [ -s "$STATE_DIR/last-monitor-pid" ]; then
    pass "Mode A SIGKILL test attaches monitor"
else
    fail "Mode A SIGKILL test must attach monitor"
fi
kill -KILL "$_wifi_pid" 2>/dev/null || true
wait "$_wifi_pid" 2>/dev/null || true
check_monitor_cleaned "Mode A watchdog bounds SIGKILL orphan"

write_mode_b_legacy
set_boot_policy false false
set_status_mode base
set_monitor_mode off
set_assoc_mode rc-ok
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" ASSOC_TIMEOUT_DEFAULT=1 \
    bash "$WIFI_SH" 0 connect >/dev/null 2>&1
rc=$?
check_equal "Mode B reconnect rejects reassociate/reconnect OK with nonzero rc" "$rc" "7"
check_equal "Mode B failed reassociate tries reconnect fallback" \
    "$(grep -c 'reconnect$' "$CALL_LOG" || true)" "1"
set_assoc_mode ok

write_mode_a_legacy
set_boot_policy true true '["Office"]'
set_status_mode no-current
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" ASSOC_TIMEOUT_DEFAULT=1 \
    bash "$WIFI_SH" 0 connect >/dev/null 2>&1
rc=$?
check_equal "Mode A without current id keeps broad recovery" "$rc" "0"
check_equal "Mode A broad recovery uses reassociate" \
    "$(grep -c 'reassociate$' "$CALL_LOG" || true)" "1"
check_equal "Mode A broad recovery does not invent a target id" \
    "$(grep -c 'select_network' "$CALL_LOG" || true)" "0"

echo ""
echo "=== supported Mode A and bgscan guidance ==="
if grep -Fq 'wpa_cli select_network' "$WIFI_SH"; then
    fail "wifi Mode A guidance must not recommend raw wpa_cli select_network"
else
    pass "wifi Mode A guidance avoids raw wpa_cli select_network"
fi
if grep -Fq 'wpa_cli select_network' "$OPC_SH"; then
    fail "OPC Mode A guidance must not recommend raw wpa_cli select_network"
else
    pass "OPC Mode A guidance avoids raw wpa_cli select_network"
fi
GUIDE="$SCRIPT_DIR/../../../../../docs/wifi_init_conf_guide.md"
if grep -Fq 'iw <iface> scan passive' "$GUIDE" \
   && grep -Fq 'wpa_cli -i <iface> SCAN passive=1' "$GUIDE"; then
    pass "bgscan guide documents iw and native passive request grammars"
else
    fail "bgscan guide documents iw and native passive request grammars"
fi
if grep -Fq 'UTF-8 hex' "$GUIDE"; then
    pass "bgscan guide documents hex-encoded native active SSID filters"
else
    fail "bgscan guide documents hex-encoded native active SSID filters"
fi
if grep -Fq 'both backends' "$GUIDE" \
   && grep -Fq 'wpa_state=COMPLETED' "$GUIDE"; then
    pass "bgscan guide applies COMPLETED safety gate to both backends"
else
    fail "bgscan guide applies COMPLETED safety gate to both backends"
fi

echo ""
echo "=== OPC canonical writer and rollback ==="
set_boot_policy false false
write_mode_b_legacy
set_reconfigure_mode ok
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$TD/run" \
    sh "$OPC_SH" mlan0 freq "5180 5200" >/dev/null 2>&1
rc=$?
check_equal "OPC freq exits zero" "$rc" "0"
check_equal "OPC freq writes one global list" "$(count_global_freq "$CONF")" "1"
check_equal "OPC freq writes canonical block list" \
    "$(count_block_freq "$CONF" '5180 5200')" "1"
check_equal "OPC freq removes scan_freq" \
    "$(grep -Ec '^[[:space:]]*scan_freq[[:space:]]*=' "$CONF" || true)" "0"
check_equal "OPC successful apply reconfigures once" \
    "$(grep -c 'reconfigure$' "$CALL_LOG" || true)" "1"
_opc_backup_sync=$(grep -nE '^sync .*/wpa_supplicant-mlan0\.conf\.bak\.[^/ ]+$' "$CALL_LOG" | head -1 | cut -d: -f1)
_opc_backup_dir_sync=$(grep -nFx "sync $WPA_DIR" "$CALL_LOG" | head -1 | cut -d: -f1)
_opc_install_mv=$(grep -nE "^mv -f .*/wpa_supplicant-mlan0\\.conf\\.[^/ ]+ $CONF$" "$CALL_LOG" | grep -v '\.bak\.' | head -1 | cut -d: -f1)
if [ -n "$_opc_backup_sync" ] && [ -n "$_opc_backup_dir_sync" ] \
   && [ -n "$_opc_install_mv" ] \
   && [ "$_opc_backup_sync" -lt "$_opc_backup_dir_sync" ] \
   && [ "$_opc_backup_dir_sync" -lt "$_opc_install_mv" ]; then
    pass "OPC durably syncs rollback backup entry before destructive rename"
else
    fail "OPC must sync rollback backup file+directory before destructive rename"
fi
_opc_stage_sync=$(grep -nE '^sync .*/wpa_supplicant-mlan0\.conf\.[^/ ]+$' "$CALL_LOG" | grep -v '\.bak\.' | head -1 | cut -d: -f1)
_opc_reconfigure=$(grep -n 'reconfigure$' "$CALL_LOG" | head -1 | cut -d: -f1)
if [ -n "$_opc_stage_sync" ] && [ -n "$_opc_reconfigure" ] \
   && [ -n "$_opc_install_mv" ] \
   && [ "$_opc_stage_sync" -lt "$_opc_install_mv" ] \
   && [ "$_opc_install_mv" -lt "$_opc_reconfigure" ]; then
    pass "OPC syncs staged conf before reconfigure"
else
    fail "OPC must sync staged conf before rename/reconfigure"
fi
check_equal "OPC syncs destination directory" \
    "$(grep -Fxc "sync $WPA_DIR" "$CALL_LOG" || true)" "2"

write_mode_b_legacy
cp "$CONF" "$TD/opc-stage-sync-original.conf"
set_sync_mode fail-stage
set_reconfigure_mode ok
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$TD/run" \
    sh "$OPC_SH" mlan0 freq "5180 5200" >/dev/null 2>&1
rc=$?
check_equal "OPC staging sync failure exits 4" "$rc" "4"
if cmp -s "$CONF" "$TD/opc-stage-sync-original.conf"; then
    pass "OPC staging sync failure leaves original conf byte-exact"
else
    fail "OPC staging sync failure must leave original conf byte-exact"
fi
check_equal "OPC staging sync failure does not reconfigure" \
    "$(grep -c 'reconfigure$' "$CALL_LOG" || true)" "0"
set_sync_mode ok

write_mode_b_legacy
cp "$CONF" "$TD/opc-installed-sync-original.conf"
set_sync_mode fail-installed
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$TD/run" \
    sh "$OPC_SH" mlan0 freq "5180 5200" >/dev/null 2>&1
rc=$?
check_equal "OPC installed-conf sync failure exits 4" "$rc" "4"
if cmp -s "$CONF" "$TD/opc-installed-sync-original.conf"; then
    pass "OPC installed-conf sync failure rolls back exact original"
else
    fail "OPC installed-conf sync failure must roll back exact original"
fi
set_sync_mode ok

write_mode_b_legacy
cp "$CONF" "$TD/opc-original.conf"
set_reconfigure_mode fail-first
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$TD/run" \
    sh "$OPC_SH" mlan0 freq "5180 5200" >/dev/null 2>&1
rc=$?
check_equal "OPC reconfigure failure exits 5" "$rc" "5"
if cmp -s "$CONF" "$TD/opc-original.conf"; then
    pass "OPC reconfigure failure restores exact original conf"
else
    fail "OPC reconfigure failure restores exact original conf"
fi
check_equal "OPC rollback reconfigures restored conf" \
    "$(grep -c 'reconfigure$' "$CALL_LOG" || true)" "2"

write_mode_b_legacy
cp "$CONF" "$TD/opc-rc-original.conf"
set_reconfigure_mode rc-ok
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$TD/run" \
    sh "$OPC_SH" mlan0 freq "5180 5200" >/dev/null 2>&1
rc=$?
check_equal "OPC rejects reconfigure stdout OK with nonzero rc" "$rc" "5"
if cmp -s "$CONF" "$TD/opc-rc-original.conf"; then
    pass "OPC rc failure restores exact original conf"
else
    fail "OPC rc failure restores exact original conf"
fi
set_reconfigure_mode ok

write_mode_b_legacy
cp "$CONF" "$TD/opc-rollback-original.conf"
set_reconfigure_mode fail-first
set_mv_mode fail-rollback
: > "$CALL_LOG"
OPC_ERR="$TD/opc-rollback.err"
WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$TD/run" \
    sh "$OPC_SH" mlan0 freq "5180 5200" >/dev/null 2>"$OPC_ERR"
rc=$?
check_equal "OPC rollback failure exits 6" "$rc" "6"
_rollback_backup=""
for _candidate in "$CONF".bak.*; do
    [ -f "$_candidate" ] && _rollback_backup="$_candidate" && break
done
if [ -n "$_rollback_backup" ] && cmp -s "$_rollback_backup" "$TD/opc-rollback-original.conf"; then
    pass "OPC rollback failure retains byte-exact original backup"
else
    fail "OPC rollback failure must retain byte-exact original backup"
fi
if grep -q 'CRITICAL: rollback failed' "$OPC_ERR" \
   && [ -n "$_rollback_backup" ] \
   && grep -Fq "$_rollback_backup" "$OPC_ERR"; then
    pass "OPC rollback failure reports critical retained-backup path"
else
    fail "OPC rollback failure must report critical retained-backup path"
fi
rm -f "$_rollback_backup"
set_mv_mode ok
set_reconfigure_mode ok

for _sync_failure in fail-rollback-file fail-rollback-dir; do
    write_mode_b_legacy
    cp "$CONF" "$TD/opc-${_sync_failure}-original.conf"
    set_reconfigure_mode fail-first
    set_sync_mode "$_sync_failure"
    : > "$CALL_LOG"
    OPC_ERR="$TD/opc-${_sync_failure}.err"
    WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$TD/run" \
        sh "$OPC_SH" mlan0 freq "5180 5200" >/dev/null 2>"$OPC_ERR"
    rc=$?
    check_equal "OPC ${_sync_failure} exits 6" "$rc" "6"
    _rollback_backup=""
    for _candidate in "$CONF".bak.*; do
        [ -f "$_candidate" ] && _rollback_backup="$_candidate" && break
    done
    if [ -n "$_rollback_backup" ] \
       && cmp -s "$_rollback_backup" "$TD/opc-${_sync_failure}-original.conf"; then
        pass "OPC ${_sync_failure} retains byte-exact recovery backup"
    else
        fail "OPC ${_sync_failure} must retain byte-exact recovery backup"
    fi
    if grep -q 'CRITICAL: rollback failed' "$OPC_ERR" \
       && [ -n "$_rollback_backup" ] \
       && grep -Fq "$_rollback_backup" "$OPC_ERR"; then
        pass "OPC ${_sync_failure} reports critical retained-backup path"
    else
        fail "OPC ${_sync_failure} must report critical retained-backup path"
    fi
    rm -f "$_rollback_backup"
    set_sync_mode ok
done
set_reconfigure_mode ok

write_mode_b_legacy
cp "$CONF" "$TD/opc-signal-original.conf"
set_mv_mode term-after-install
OPC_ERR="$TD/opc-signal.err"
WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$TD/run" \
    sh "$OPC_SH" mlan0 freq "5180 5200" >/dev/null 2>"$OPC_ERR"
rc=$?
check_equal "OPC cancellation after install exits 6" "$rc" "6"
if cmp -s "$CONF" "$TD/opc-signal-original.conf"; then
    pass "OPC cancellation after install restores exact original conf"
else
    fail "OPC cancellation after install restores exact original conf"
fi
set_mv_mode ok

write_mode_a_legacy
set_boot_policy true true '["Office"]'
before=$(sha256sum "$CONF" | awk '{print $1}')
WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$TD/run" \
    sh "$OPC_SH" mlan0 ssid NewNet >/dev/null 2>&1
rc=$?
after=$(sha256sum "$CONF" | awk '{print $1}')
check_equal "OPC explicit SSID remains rejected in Mode A" "$rc" "2"
check_equal "OPC Mode A rejection leaves conf unchanged" "$after" "$before"

write_mode_b_legacy
set_boot_policy true true '[]'
before=$(sha256sum "$CONF" | awk '{print $1}')
WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$RUN_DIR" \
    sh "$OPC_SH" mlan0 ssid NewNet >/dev/null 2>&1
rc=$?
after=$(sha256sum "$CONF" | awk '{print $1}')
check_equal "OPC rejects Mode A with empty extra list" "$rc" "2"
check_equal "OPC empty-extra rejection leaves conf unchanged" "$after" "$before"

echo ""
echo "RESULT: PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
