#!/bin/bash
# wifi.sh/opc_wlan_apply.sh wpa conf writer integration tests (hardware/root free)
set -u

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
WIFI_SH="$SCRIPT_DIR/wifi.sh"
OPC_SH="$SCRIPT_DIR/opc_wlan_apply.sh"
ROAM_GUIDE="$SCRIPT_DIR/../../../../../docs/roaming_scan_guide.md"
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
if { [ "${INSTALL_MODE:-}" = "hold-after-monitor" ] \
     && [ -s "$STATE_DIR/monitor-action" ]; \
   } || [ "${INSTALL_MODE:-}" = "hold-fd9-writer" ]; then
  if mkdir "$STATE_DIR/held-child-claim" 2>/dev/null; then
    printf '%s\n' "$$" > "$STATE_DIR/held-child-pid"
    : > "$STATE_DIR/held-child-ready"
    IFS= read -r _release < "$STATE_DIR/held-child-release"
  fi
fi
if [ "${INSTALL_MODE:-}" = "partial-fail" ]; then
    printf 'PARTIAL\n' > "$2"
    exit 1
fi
cp "$1" "$2"
EOF
cat > "$BIN/cat" <<'EOF'
#!/bin/sh
mode=$(/bin/cat "$STATE_DIR/held-child-mode" 2>/dev/null || true)
case "$mode:${1:-}" in
  monitor-pid-cat:*/wpa_cli.pid)
    if mkdir "$STATE_DIR/held-child-claim" 2>/dev/null; then
      printf '%s\n' "$$" > "$STATE_DIR/held-child-pid"
      : > "$STATE_DIR/held-child-ready"
      IFS= read -r _release < "$STATE_DIR/held-child-release"
    fi
    ;;
esac
case "${1:-}" in
  /proc/*/stat)
    if [ -f "$STATE_DIR/watchdog-probe-arm" ] \
       && mkdir "$STATE_DIR/watchdog-probe-claim" 2>/dev/null; then
      printf '%s\n' "$$" > "$STATE_DIR/watchdog-probe-pid"
      : > "$STATE_DIR/watchdog-probe-ready"
      IFS= read -r _release < "$STATE_DIR/watchdog-probe-release"
    fi
    ;;
esac
exec /bin/cat "$@"
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
printf 'systemctl %s\n' "$*" >> "$CALL_LOG"
case "$1" in
  stop) printf 'inactive\n' > "$STATE_DIR/wpa-service-state" ;;
  start|restart) printf 'active\n' > "$STATE_DIR/wpa-service-state" ;;
  is-active)
    [ "$(cat "$STATE_DIR/wpa-service-state" 2>/dev/null || echo active)" = active ]
    exit $? ;;
esac
exit 0
EOF
cat > "$BIN/flock" <<'EOF'
#!/bin/sh
printf 'flock %s\n' "$*" >> "$CALL_LOG"
mode=$(/bin/cat "$STATE_DIR/held-child-mode" 2>/dev/null || true)
case "$mode:$*" in
  fd7-acquire:*" -x 7")
    if mkdir "$STATE_DIR/held-child-claim" 2>/dev/null; then
      printf '%s\n' "$$" > "$STATE_DIR/held-child-pid"
      : > "$STATE_DIR/held-child-ready"
      IFS= read -r _release < "$STATE_DIR/held-child-release"
    fi
    ;;
esac
exec /usr/bin/flock "$@"
EOF
cat > "$BIN/wpa_cli" <<'EOF'
#!/bin/sh
printf 'wpa_cli %s\n' "$*" >> "$CALL_LOG"

held_mode=$(/bin/cat "$STATE_DIR/held-child-mode" 2>/dev/null || true)
case "$held_mode:$*" in
  opc-ping:*" ping")
    if mkdir "$STATE_DIR/held-child-claim" 2>/dev/null; then
      printf '%s\n' "$$" > "$STATE_DIR/held-child-pid"
      : > "$STATE_DIR/held-child-ready"
      IFS= read -r _release < "$STATE_DIR/held-child-release"
    fi
    ;;
esac

emit_connected_event() {
  action=$(cat "$STATE_DIR/monitor-action" 2>/dev/null || true)
  [ -n "$action" ] && [ -x "$action" ] || return 0
  WPA_ID="${1:-0}" "$action" mlan0 CONNECTED
}

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
    if [ "$mode" = "stubborn" ]; then
      bash -c 'trap "" TERM; exec -a wpa_cli sleep 60' >/dev/null 2>&1 &
    else
      bash -c 'exec -a wpa_cli sleep 60' >/dev/null 2>&1 &
    fi
    monitor_pid=$!
    printf '%s\n' "$monitor_pid" > "$pidfile"
    printf '%s\n' "$monitor_pid" > "$STATE_DIR/last-monitor-pid"
    printf 'OK\n'
    exit 0
    ;;
esac

case "$*" in
  *" abort_scan"*)
    count=$(cat "$STATE_DIR/abort-scan-count" 2>/dev/null || echo 0)
    count=$((count + 1))
    printf '%s\n' "$count" > "$STATE_DIR/abort-scan-count"
    mode=$(cat "$STATE_DIR/abort-scan-mode" 2>/dev/null || echo plain-fail)
    case "$mode" in
      ok) printf 'OK\n' ;;
      plain-fail) printf 'FAIL\n' ;;
      ok-then-fail)
        if [ "$count" -eq 1 ]; then printf 'OK\n'; else printf 'FAIL\n'; fi ;;
      always-ok) printf 'OK\n' ;;
      ok-then-empty)
        if [ "$count" -eq 1 ]; then printf 'OK\n'; else :; fi ;;
      ok-then-other)
        if [ "$count" -eq 1 ]; then printf 'OK\n'; else printf 'BUSY\n'; fi ;;
      ok-then-rc-ok)
        printf 'OK\n'
        [ "$count" -eq 1 ] || exit 9 ;;
      empty) : ;;
      other) printf 'BUSY\n' ;;
      rc-ok) printf 'OK\n'; exit 9 ;;
    esac
    ;;
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
      mode-a-slow-poll)
        ssid=Office; id=1
        if [ "$count" -gt 1 ]; then
          printf '%s\n' "$$" > "$STATE_DIR/slow-status-pid"
          : > "$STATE_DIR/slow-status-ready"
          IFS= read -r _release < "$STATE_DIR/slow-status-release"
          state=DISCONNECTED
        fi ;;
      disconnected)
        state=DISCONNECTED
        ssid=
        id=
        freq=
        ;;
      no-current)
        if [ "$count" -eq 1 ]; then state=DISCONNECTED; ssid=; id=; freq=; fi ;;
      no-current-recover)
        if [ "$count" -eq 1 ]; then state=DISCONNECTED; ssid=; id=; freq=;
        else ssid=Base; id=0; freq=5180; fi ;;
      mode-b-id-switch)
        ssid=Base; freq=5180
        if [ "$count" -eq 1 ]; then id=0; else id=1; fi ;;
      target) ssid=NewNet; id=0; freq=5180 ;;
      target-custom) ssid=$(/bin/cat "$STATE_DIR/target-ssid"); id=0; freq=5180 ;;
      target-no-id) ssid=NewNet; id=; freq=5180 ;;
      target-nonnumeric-id) ssid=NewNet; id=bad; freq=5180 ;;
      target-delayed-grace)
        ssid=NewNet; id=0; freq=5180
        [ "$count" -ne 2 ] || emit_connected_event 0 ;;
      target-delayed-fallback)
        ssid=NewNet; id=0; freq=5180
        [ "$count" -ne 6 ] || emit_connected_event 0 ;;
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
      action=$(cat "$STATE_DIR/monitor-action" 2>/dev/null || true)
      monitor_mode=$(cat "$STATE_DIR/monitor-mode" 2>/dev/null || echo off)
      if [ "$monitor_mode" = "reconfigure-event" ] \
         && [ -n "$action" ] && [ -x "$action" ]; then
        WPA_ID=$(cat "$STATE_DIR/event-id" 2>/dev/null || echo 0) "$action" mlan0 CONNECTED
      fi
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
        matching-zero) WPA_ID=0 "$action" mlan0 CONNECTED ;;
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
cat > "$BIN/iw" <<'EOF'
#!/bin/sh
printf 'iw %s\n' "$*" >> "$CALL_LOG"
exit 0
EOF
cat > "$BIN/mlanutl" <<'EOF'
#!/bin/sh
printf 'mlanutl %s\n' "$*" >> "$CALL_LOG"
exit 0
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

set_abort_scan_mode() {
    printf '%s\n' "$1" > "$STATE_DIR/abort-scan-mode"
    rm -f "$STATE_DIR/abort-scan-count"
}

set_wpa_service_state() {
    printf '%s\n' "$1" > "$STATE_DIR/wpa-service-state"
}

prepare_held_child() {
    local mode="$1"
    rm -rf "$STATE_DIR/held-child-claim"
    rm -f "$STATE_DIR/held-child-pid" "$STATE_DIR/held-child-ready" \
          "$STATE_DIR/held-child-release"
    printf '%s\n' "$mode" > "$STATE_DIR/held-child-mode"
    mkfifo "$STATE_DIR/held-child-release"
}

wait_for_held_child() {
    local desc="$1" pid=""
    for _wait in $(seq 1 100); do
        if [ -s "$STATE_DIR/held-child-pid" ] \
           && [ -f "$STATE_DIR/held-child-ready" ] \
           && [ -d "$(cat "$STATE_DIR/last-monitor-dir" 2>/dev/null || true)" ]; then
            break
        fi
        sleep 0.01
    done
    pid=$(cat "$STATE_DIR/held-child-pid" 2>/dev/null || true)
    if [ -n "$pid" ] && monitor_process_running "$pid"; then
        pass "$desc"
        return 0
    fi
    fail "$desc"
    return 1
}

wait_for_held_child_no_monitor() {
    local desc="$1" pid=""
    for _wait in $(seq 1 100); do
        [ -s "$STATE_DIR/held-child-pid" ] \
            && [ -f "$STATE_DIR/held-child-ready" ] && break
        sleep 0.01
    done
    pid=$(cat "$STATE_DIR/held-child-pid" 2>/dev/null || true)
    if [ -n "$pid" ] && monitor_process_running "$pid"; then
        pass "$desc"
        return 0
    fi
    fail "$desc"
    return 1
}

cleanup_held_child() {
    local desc="$1" pid=""
    pid=$(cat "$STATE_DIR/held-child-pid" 2>/dev/null || true)
    [ -z "$pid" ] || kill -TERM "$pid" 2>/dev/null || true
    for _wait in $(seq 1 50); do
        [ -z "$pid" ] || ! monitor_process_running "$pid" && break
        sleep 0.01
    done
    if [ -z "$pid" ] || ! monitor_process_running "$pid"; then
        pass "$desc"
    else
        fail "$desc"
        kill -KILL "$pid" 2>/dev/null || true
    fi
    rm -rf "$STATE_DIR/held-child-claim"
    rm -f "$STATE_DIR/held-child-mode" "$STATE_DIR/held-child-pid" \
          "$STATE_DIR/held-child-ready" "$STATE_DIR/held-child-release"
}

prepare_watchdog_probe() {
    rm -rf "$STATE_DIR/watchdog-probe-claim"
    rm -f "$STATE_DIR/watchdog-probe-arm" "$STATE_DIR/watchdog-probe-pid" \
          "$STATE_DIR/watchdog-probe-ready" "$STATE_DIR/watchdog-probe-release"
    mkfifo "$STATE_DIR/watchdog-probe-release"
}

arm_watchdog_probe() {
    : > "$STATE_DIR/watchdog-probe-arm"
    for _wait in $(seq 1 50); do
        [ -f "$STATE_DIR/watchdog-probe-ready" ] && return 0
        sleep 0.01
    done
    return 1
}

cleanup_watchdog_probe() {
    local pid=""
    pid=$(cat "$STATE_DIR/watchdog-probe-pid" 2>/dev/null || true)
    [ -z "$pid" ] || kill -TERM "$pid" 2>/dev/null || true
    for _wait in $(seq 1 50); do
        [ -z "$pid" ] || ! monitor_process_running "$pid" && break
        sleep 0.01
    done
    [ -z "$pid" ] || kill -KILL "$pid" 2>/dev/null || true
    rm -rf "$STATE_DIR/watchdog-probe-claim"
    rm -f "$STATE_DIR/watchdog-probe-arm" "$STATE_DIR/watchdog-probe-pid" \
          "$STATE_DIR/watchdog-probe-ready" "$STATE_DIR/watchdog-probe-release"
}

check_held_child_survives_owner_reap() {
    local desc="$1" pid=""
    pid=$(cat "$STATE_DIR/held-child-pid" 2>/dev/null || true)
    if [ -n "$pid" ] && monitor_process_running "$pid"; then
        pass "$desc"
    else
        fail "$desc (pid=${pid:-missing})"
    fi
}

check_child_substitution_normalization() {
    local shell="$1" result="" rc=0
    result=$("$shell" -s -- "$SCRIPT_DIR/wifi_init_config_lib.sh" "$TD" <<'EOF'
set -e
. "$1"
root="$2/child-substitution-$$"
mkdir -p "$root"
trap 'rm -rf "$root"' EXIT
exec 9>"$root/fd9"
exec 7>"$root/fd7"

missing_out=$(wifi_wpa_child_exec cat "$root/missing" 2>/dev/null) || true
[ "$?" -eq 0 ]
[ -z "$missing_out" ]

printf 'alpha\nbeta' > "$root/success"
success_out=$(wifi_wpa_child_exec cat "$root/success")
[ "$success_out" = "$(printf 'alpha\nbeta')" ]
printf 'normalized\n'
EOF
    ) || rc=$?
    if [ "$rc" -eq 0 ] && [ "$result" = "normalized" ]; then
        pass "$shell child substitution preserves failure-to-empty and successful stdout"
    else
        fail "$shell child substitution normalization (rc=$rc output=$result)"
    fi
}

check_missing_pid_cleanup_set_e() {
    local funcs="$TD/connect-cleanup-functions.sh"
    local dir="$TD/missing-pid-cleanup" result="" rc=0
    awk '
        /^connect_monitor_proc_start_into\(\) \{/ { copy = 1 }
        /^connect_event_monitor_start\(\)/ { copy = 0 }
        copy { print }
    ' "$WIFI_SH" > "$funcs"
    rm -rf "$dir"
    mkdir -p "$dir"
    result=$(bash -s -- "$SCRIPT_DIR/wifi_init_config_lib.sh" "$funcs" "$dir" <<'EOF'
set -e
. "$1"
. "$2"
CONNECT_MONITOR_WATCHDOG_PID=""
CONNECT_MONITOR_WATCHDOG_START=""
CONNECT_MONITOR_PID=""
CONNECT_MONITOR_START=""
CONNECT_MONITOR_DIR="$3"
exec 9>"$3/fd9"
exec 7>"$3/fd7"
connect_event_monitor_cleanup
[ -z "$CONNECT_MONITOR_DIR" ]
[ ! -e "$3" ]
printf 'cleanup-reached\n'
EOF
    ) || rc=$?
    if [ "$rc" -eq 0 ] && [ "$result" = "cleanup-reached" ] \
       && [ ! -e "$dir" ]; then
        pass "set -e cleanup removes private monitor directory when PID file is absent"
    else
        fail "set -e missing-PID cleanup must reach directory removal (rc=$rc output=$result)"
        rm -rf "$dir"
    fi
}

hold_scan_transition_lock() {
    local lock="$RUN_DIR/mlan0.scan-transition.lock" ready="$TD/scan-lock-ready"
    rm -f "$ready"
    python3 - "$lock" "$ready" <<'PY' &
import fcntl
import signal
import sys

fd = open(sys.argv[1], "a+")
fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
open(sys.argv[2], "w").close()
signal.pause()
PY
    SCAN_LOCK_HOLDER=$!
    for _wait in $(seq 1 50); do
        [ -f "$ready" ] && return 0
        sleep 0.02
    done
    return 1
}

release_scan_transition_lock() {
    [ -z "${SCAN_LOCK_HOLDER:-}" ] || kill -TERM "$SCAN_LOCK_HOLDER" 2>/dev/null || true
    [ -z "${SCAN_LOCK_HOLDER:-}" ] || wait "$SCAN_LOCK_HOLDER" 2>/dev/null || true
    SCAN_LOCK_HOLDER=""
}

check_scan_transition_lock_available() {
    local desc="$1" lock="$RUN_DIR/mlan0.scan-transition.lock" _try
    # The parent has already been reaped.  Permit only a tiny scheduler handoff
    # for a just-daemonized monitor child; this probe is still before watchdog
    # cleanup and catches inherited FD7 locks.
    for _try in $(seq 1 10); do
        if ( exec 7>"$lock" && flock -n 7 ); then
            pass "$desc"
            return
        fi
        sleep 0.01
    done
    fail "$desc"
}

check_connect_locks_available_before_monitor_cleanup() {
    local desc="$1" dir=""
    dir=$(cat "$STATE_DIR/last-monitor-dir" 2>/dev/null || true)
    if [ -n "$dir" ] && [ -d "$dir" ] && (
        exec 9>"$RUN_DIR/mlan0.wpa-conf.lock"
        flock -n 9 || exit 1
        exec 7>"$RUN_DIR/mlan0.scan-transition.lock"
        flock -n 7 || exit 1
        [ -d "$dir" ]
    ); then
        pass "$desc"
    else
        fail "$desc (dir=${dir:-missing})"
    fi
}

check_ordered_writer_locks_available() {
    local desc="$1"
    if (
        exec 9>"$RUN_DIR/mlan0.wpa-conf.lock"
        flock -n 9 || exit 1
        exec 7>"$RUN_DIR/mlan0.scan-transition.lock"
        flock -n 7 || exit 1
    ); then
        pass "$desc"
    else
        fail "$desc"
    fi
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
    # Poll the combined cleanup postcondition through the production three-
    # second bound.  PID exit can precede rm -rf by a few milliseconds, so
    # never break on PID exit alone.
    for _wait in $(seq 1 30); do
        if { [ -z "$pid" ] || ! monitor_process_running "$pid"; } \
           && { [ -z "$dir" ] || [ ! -e "$dir" ]; } \
           && ! find "$RUN_DIR" -maxdepth 1 -name 'mlan0.connect-monitor.*' -print -quit 2>/dev/null | grep -q .; then
            pass "$desc"
            return
        fi
        sleep 0.1
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

check_monitor_attached_before_reconfigure() {
    local desc="$1" attach request
    attach=$(grep -n ' -a .* -B -P ' "$CALL_LOG" | head -1 | cut -d: -f1)
    request=$(grep -n 'reconfigure$' "$CALL_LOG" | head -1 | cut -d: -f1)
    if [ -n "$attach" ] && [ -n "$request" ] && [ "$attach" -lt "$request" ]; then
        pass "$desc"
    else
        fail "$desc (attach=${attach:-missing} reconfigure=${request:-missing})"
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
    printf '1\n' > "${RUN_DIR%/*}/.mlan0.roam-policy.latched"
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

ssid_hex() {
    python3 -c 'import sys; print(sys.argv[1].encode("utf-8").hex())' "$1"
}

ssid_wpa_text() {
    python3 - "$1" <<'PYCODE'
import sys
out = []
for byte in sys.argv[1].encode("utf-8"):
    if byte == 0x22:
        out.append(r'\"')
    elif byte == 0x5c:
        out.append(r'\\')
    elif 0x20 <= byte <= 0x7e:
        out.append(chr(byte))
    else:
        out.append(f"\\x{byte:02x}")
print("".join(out))
PYCODE
}

check_real_wpa_parser() {
    local desc="$1" conf="$2" parser log
    parser=$(command -v wpa_supplicant 2>/dev/null || true)
    if [ -z "$parser" ]; then
        fail "$desc (wpa_supplicant unavailable)"
        return
    fi
    log="$TD/wpa-parser-$PASS-$FAIL.log"
    "$parser" -D none -i wlanpkgtest0 -c "$conf" -dd >"$log" 2>&1 || true
    if grep -q 'Successfully initialized wpa_supplicant' "$log" \
       && ! grep -q 'Failed to read or parse configuration' "$log"; then
        pass "$desc"
    else
        fail "$desc"
    fi
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

# Exact current supported-writer graph inventory beyond wifi connect: the FD7
# acquisition primitive, all four FD9-only cases, and OPC from lock acquisition
# through transaction cleanup.  This is deliberately a reviewed source-slice
# inventory, not a claim to parse arbitrary future shell grammar.
if python3 - "$WIFI_SH" "$OPC_SH" "$SCRIPT_DIR/wifi_init_config_lib.sh" <<'PY'
import re
import sys

wifi_path, opc_path, lib_path = sys.argv[1:]
wifi = open(wifi_path, encoding="utf-8").read()
opc = open(opc_path, encoding="utf-8").read()
lib = open(lib_path, encoding="utf-8").read()

case_names = ("freq", "ssid", "psk", "key")
regions = []
for index, name in enumerate(case_names):
    start = wifi.index(f"  {name})")
    next_name = case_names[index + 1] if index + 1 < len(case_names) else "connect"
    end = wifi.index(f"\n  {next_name})", start)
    lock = wifi.index('wifi_wpa_conf_lock_acquire "$IFACE"', start, end)
    regions.append((f"wifi-{name}", wifi[lock:end]))

opc_start = opc.index('wifi_wpa_conf_lock_acquire "$IFACE"')
regions.append(("opc", opc[opc_start:]))

acquire_start = lib.index("wifi_scan_transition_lock_acquire() {")
acquire_end = lib.index("\n}\n", acquire_start) + 2
acquire = lib[acquire_start:acquire_end]
if "exec 9>&-" not in acquire:
    raise SystemExit("FD7 acquisition child does not structurally close FD9")

external = (
    "awk", "cat", "chmod", "chown", "cp", "dirname", "flock", "grep",
    "install", "jq", "mkdir", "mktemp", "mv", "python3", "rm", "sed",
    "sleep", "sync", "touch", "tr", "wpa_cli",
)
command = "(" + "|".join(external) + r")\b"
wrappers = (
    "wifi_wpa_child_exec", "wifi_wpa_child_call",
    "wifi_wpa_run_child", "wifi_wpa_run_child_call",
)
start_re = re.compile(
    r"^(?:(?:if|elif|while|until)\s+)?(?:!\s+)?"
    r"(?:[A-Za-z_][A-Za-z0-9_]*=(?:\"[^\"]*\"|'[^']*'|\S+)\s+)*" + command
)
after_re = re.compile(r"(?:\$\(|<\(|[;&|])\s*" + command)
violations = []
for region_name, region in regions:
    heredoc = False
    for number, line in enumerate(region.splitlines(), 1):
        stripped = line.strip()
        if heredoc:
            if stripped in {"EOF", "EOT"}:
                heredoc = False
            continue
        if re.search(r"<<-?['\"]?(?:EOF|EOT)['\"]?", line):
            heredoc = True
            continue
        if not stripped or stripped.startswith("#"):
            continue
        raw = bool(start_re.search(stripped) or after_re.search(line))
        if stripped.startswith("trap ") and re.search(command, line):
            raw = True
        if raw and not any(wrapper in line for wrapper in wrappers):
            violations.append(f"{region_name}:{number}: {stripped}")

if violations:
    print("supported-writer private-FD isolation violations:", file=sys.stderr)
    print("\n".join(violations), file=sys.stderr)
    raise SystemExit(1)
PY
then
    pass "FD7 acquisition, OPC, and FD9-only writer inventories are complete and isolated"
else
    fail "FD7 acquisition, OPC, and FD9-only writer inventories must be complete and isolated"
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
echo "=== shared SSID validation and hex serialization ==="
_special_ssid='  게스트 \ " exact  '
_special_hex=$(ssid_hex "$_special_ssid")
write_mode_b_legacy
set_boot_policy false false '["Office"]'
WPA_CONF_DIR="$WPA_DIR" bash "$WIFI_SH" 0 ssid "$_special_ssid" >/dev/null 2>&1
check_equal "wifi ssid accepts printable UTF-8 special identity" "$?" "0"
check_equal "wifi ssid writes exact UTF-8 hex token" \
    "$(grep -Ec "^[[:space:]]+ssid=${_special_hex}$" "$CONF")" "1"
check_real_wpa_parser "real parser accepts wifi ssid hex output" "$CONF"

_ssid_32='가가가가가가가가가가ab'
_ssid_33='가가가가가가가가가가가'
write_mode_b_legacy
WPA_CONF_DIR="$WPA_DIR" bash "$WIFI_SH" 0 ssid "$_ssid_32" >/dev/null 2>&1
check_equal "wifi ssid accepts exact 32-byte UTF-8 boundary" "$?" "0"
check_equal "wifi ssid 32-byte boundary is exact hex" \
    "$(grep -Ec "^[[:space:]]+ssid=$(ssid_hex "$_ssid_32")$" "$CONF")" "1"

for _invalid_ssid in '' $'bad\nname' $'bad\tname' $'bad\x7fname' "$_ssid_33"; do
    write_mode_b_legacy
    cp "$CONF" "$TD/invalid-ssid.before"
    WPA_CONF_DIR="$WPA_DIR" bash "$WIFI_SH" 0 ssid "$_invalid_ssid" >/dev/null 2>&1
    rc=$?
    if [ "$rc" -ne 0 ] && cmp -s "$CONF" "$TD/invalid-ssid.before"; then
        pass "wifi ssid rejects invalid/control/33-byte identity without mutation"
    else
        fail "wifi ssid must reject invalid/control/33-byte identity without mutation"
    fi
done

# Mode B's immutable extra list is a manual candidate allowlist.  Selecting
# one of those candidates intentionally replaces the sole base block; this is
# not Mode A topology generation and must remain supported by every Mode B
# SSID writer.
write_mode_b_legacy
set_boot_policy false false '["Office"]'
WPA_CONF_DIR="$WPA_DIR" bash "$WIFI_SH" 0 ssid Office >/dev/null 2>&1
rc=$?
if [ "$rc" -eq 0 ] && grep -Eq '^[[:space:]]+ssid=4f6666696365$' "$CONF"; then
    pass "wifi ssid accepts an immutable Mode B manual candidate"
else
    fail "wifi ssid must accept an immutable Mode B manual candidate"
fi

write_mode_b_legacy
set_boot_policy false false '["Office"]'
set_status_mode target-custom
printf '%s' "$(ssid_wpa_text Office)" > "$STATE_DIR/target-ssid"
set_monitor_mode reconfigure-event
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" ASSOC_TIMEOUT_DEFAULT=1 \
    bash "$WIFI_SH" 0 connect Office 5180 >/dev/null 2>&1
rc=$?
if [ "$rc" -eq 0 ] && grep -Eq '^[[:space:]]+ssid=4f6666696365$' "$CONF"; then
    pass "wifi connect accepts an immutable Mode B manual candidate"
else
    fail "wifi connect must accept an immutable Mode B manual candidate"
fi
check_equal "wifi connect Mode B manual candidate reconfigures once" \
    "$(grep -c 'reconfigure$' "$CALL_LOG" || true)" "1"

write_mode_b_legacy
set_boot_policy false false '["Office"]'
WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$RUN_DIR" \
    sh "$OPC_SH" mlan0 ssid Office >/dev/null 2>&1
rc=$?
if [ "$rc" -eq 0 ] && grep -Eq '^[[:space:]]+ssid=4f6666696365$' "$CONF"; then
    pass "OPC accepts an immutable Mode B manual candidate"
else
    fail "OPC must accept an immutable Mode B manual candidate"
fi

write_mode_b_legacy
set_status_mode target-custom
printf '%s' "$(ssid_wpa_text "$_special_ssid")" > "$STATE_DIR/target-ssid"
set_monitor_mode reconfigure-event
WPA_CONF_DIR="$WPA_DIR" ASSOC_TIMEOUT_DEFAULT=1 \
    bash "$WIFI_SH" 0 connect "$_special_ssid" 5180 >/dev/null 2>&1
check_equal "wifi connect accepts exact special SSID" "$?" "0"
check_equal "wifi connect writes exact UTF-8 hex token" \
    "$(grep -Ec "^[[:space:]]+ssid=${_special_hex}$" "$CONF")" "1"
check_real_wpa_parser "real parser accepts wifi connect hex output" "$CONF"

write_mode_b_legacy
set_reconfigure_mode ok
WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$RUN_DIR" \
    sh "$OPC_SH" mlan0 ssid "$_special_ssid" >/dev/null 2>&1
check_equal "OPC accepts exact special SSID in Mode B" "$?" "0"
check_equal "OPC writes exact UTF-8 hex token" \
    "$(grep -Ec "^[[:space:]]+ssid=${_special_hex}$" "$CONF")" "1"
check_real_wpa_parser "real parser accepts OPC hex output" "$CONF"

for _invalid_ssid in $'bad\x1fname' $'bad\x7fname' "$_ssid_33"; do
    write_mode_b_legacy
    cp "$CONF" "$TD/invalid-connect.before"
    set_monitor_mode stale
    WPA_CONF_DIR="$WPA_DIR" ASSOC_TIMEOUT_DEFAULT=1 \
        bash "$WIFI_SH" 0 connect "$_invalid_ssid" >/dev/null 2>&1
    rc=$?
    if [ "$rc" -ne 0 ] && cmp -s "$CONF" "$TD/invalid-connect.before"; then
        pass "wifi connect rejects invalid/control/33-byte identity without mutation"
    else
        fail "wifi connect must reject invalid/control/33-byte identity without mutation"
    fi

    write_mode_b_legacy
    cp "$CONF" "$TD/invalid-opc.before"
    WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$RUN_DIR" \
        sh "$OPC_SH" mlan0 ssid "$_invalid_ssid" >/dev/null 2>&1
    rc=$?
    if [ "$rc" -ne 0 ] && cmp -s "$CONF" "$TD/invalid-opc.before"; then
        pass "OPC rejects invalid/control/33-byte identity without mutation"
    else
        fail "OPC must reject invalid/control/33-byte identity without mutation"
    fi
done

echo ""
echo "=== wifi connect target-aware writer ==="
set_boot_policy false false
write_mode_b_legacy
set_status_mode stale-then-target
set_monitor_mode matching-zero
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
set_monitor_mode reconfigure-event
WPA_CONF_DIR="$WPA_DIR" ASSOC_TIMEOUT_DEFAULT=1 \
    bash "$WIFI_SH" 0 connect NewNet 5180 >/dev/null 2>&1
check_equal "wifi connect rejects COMPLETED on wrong SSID" "$?" "8"

write_mode_b_legacy
set_status_mode wrong-freq
set_monitor_mode reconfigure-event
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
check_scan_transition_lock_available "Mode A TERM leaves scan-transition lock immediately acquirable"

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
check_scan_transition_lock_available "Mode A SIGKILL leaves scan-transition lock immediately acquirable"
check_monitor_cleaned "Mode A watchdog bounds SIGKILL orphan"

# Hold the first post-monitor association status child alive, kill only its
# owning shell, and probe both transaction locks before the watchdog removes
# the private monitor directory.  This deterministically exposes inherited
# open file descriptions instead of hoping SIGKILL lands in a short poll.
write_mode_a_legacy
set_status_mode mode-a-slow-poll
set_monitor_mode stubborn
rm -f "$STATE_DIR/slow-status-pid" "$STATE_DIR/slow-status-ready" \
      "$STATE_DIR/slow-status-release"
mkfifo "$STATE_DIR/slow-status-release"
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" ASSOC_TIMEOUT_DEFAULT=10 \
    bash "$WIFI_SH" 0 connect >/dev/null 2>&1 &
_wifi_pid=$!
for _wait in $(seq 1 100); do
    [ -s "$STATE_DIR/slow-status-pid" ] \
        && [ -f "$STATE_DIR/slow-status-ready" ] \
        && [ -d "$(cat "$STATE_DIR/last-monitor-dir" 2>/dev/null || true)" ] \
        && break
    sleep 0.01
done
_slow_status_pid=$(cat "$STATE_DIR/slow-status-pid" 2>/dev/null || true)
if [ -n "$_slow_status_pid" ] && kill -0 "$_slow_status_pid" 2>/dev/null; then
    pass "Mode A SIGKILL fixture holds post-monitor status child live"
else
    fail "Mode A SIGKILL fixture must hold post-monitor status child live"
fi
kill -KILL "$_wifi_pid" 2>/dev/null || true
wait "$_wifi_pid" 2>/dev/null || true
if [ -n "$_slow_status_pid" ] && monitor_process_running "$_slow_status_pid"; then
    pass "Mode A status child remains live after owner SIGKILL and reap"
else
    fail "Mode A status child must remain live after owner SIGKILL and reap"
fi
check_connect_locks_available_before_monitor_cleanup \
    "Mode A SIGKILL releases FD9 then FD7 before monitor cleanup"
[ -z "$_slow_status_pid" ] || kill -TERM "$_slow_status_pid" 2>/dev/null || true
for _wait in $(seq 1 50); do
    [ -z "$_slow_status_pid" ] || ! monitor_process_running "$_slow_status_pid" \
        && break
    sleep 0.01
done
if [ -z "$_slow_status_pid" ] || ! monitor_process_running "$_slow_status_pid"; then
    pass "Mode A SIGKILL fixture cleans slow status child"
else
    fail "Mode A SIGKILL fixture must clean slow status child"
    kill -KILL "$_slow_status_pid" 2>/dev/null || true
fi
rm -f "$STATE_DIR/slow-status-release"
check_monitor_cleaned "Mode A slow-child watchdog still bounds monitor orphan"

# Arm a deterministic trap for any external /proc parent-liveness `cat` only
# after the monitor is attached.  The watchdog's three-second SIGKILL bound
# must not depend on a schedulable/transitively held polling child.
write_mode_a_legacy
set_status_mode mode-a-wait
set_monitor_mode stubborn
prepare_watchdog_probe
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" ASSOC_TIMEOUT_DEFAULT=10 \
    bash "$WIFI_SH" 0 connect >/dev/null 2>&1 &
_wifi_pid=$!
for _wait in $(seq 1 100); do
    [ -s "$STATE_DIR/last-monitor-pid" ] \
        && [ -d "$(cat "$STATE_DIR/last-monitor-dir" 2>/dev/null || true)" ] \
        && [ "$(cat "$STATE_DIR/status-count" 2>/dev/null || echo 0)" -ge 2 ] \
        && break
    sleep 0.01
done
if [ "$(cat "$STATE_DIR/status-count" 2>/dev/null || echo 0)" -ge 2 ]; then
    pass "watchdog probe arms after monitor setup and association polling"
else
    fail "watchdog probe must arm after monitor setup and association polling"
fi
if arm_watchdog_probe; then
    fail "watchdog parent-liveness poll must not depend on external cat"
else
    pass "watchdog parent-liveness poll is shell-builtin and child-free"
fi
kill -KILL "$_wifi_pid" 2>/dev/null || true
wait "$_wifi_pid" 2>/dev/null || true
check_connect_locks_available_before_monitor_cleanup \
    "builtin-watchdog SIGKILL releases FD9 then FD7 before monitor cleanup"
check_monitor_cleaned "builtin-watchdog bounds monitor orphan within three seconds"
cleanup_watchdog_probe
# A RED run deliberately holds the old external poll through the assertion;
# drain its resumed watchdog before the next scenario so one causal failure
# cannot contaminate later monitor checks.
for _wait in $(seq 1 30); do
    _probe_dir=$(cat "$STATE_DIR/last-monitor-dir" 2>/dev/null || true)
    [ -z "$_probe_dir" ] || [ ! -e "$_probe_dir" ] && break
    sleep 0.1
done
if [ ! -e "${_probe_dir:-}" ]; then
    pass "watchdog probe cleanup leaves no private monitor directory"
else
    fail "watchdog probe cleanup must leave no private monitor directory"
fi

# Hold the canonical writer's install child only after the explicit-connect
# monitor is attached.  This covers the render/install transaction rather than
# another association poll and again probes while the stubborn monitor's
# private directory proves watchdog cleanup has not hidden lock retention.
write_mode_b_legacy
set_boot_policy false false
set_status_mode target
set_monitor_mode stubborn
prepare_held_child explicit-install
: > "$CALL_LOG"
INSTALL_MODE=hold-after-monitor WPA_CONF_DIR="$WPA_DIR" ASSOC_TIMEOUT_DEFAULT=10 \
    bash "$WIFI_SH" 0 connect NewNet 5180 >/dev/null 2>&1 &
_wifi_pid=$!
wait_for_held_child \
    "explicit connect SIGKILL fixture holds post-monitor install child live"
kill -KILL "$_wifi_pid" 2>/dev/null || true
wait "$_wifi_pid" 2>/dev/null || true
check_held_child_survives_owner_reap \
    "explicit install child remains live after owner SIGKILL and reap"
check_connect_locks_available_before_monitor_cleanup \
    "explicit install SIGKILL releases FD9 then FD7 before monitor cleanup"
cleanup_held_child "explicit install SIGKILL fixture cleans held child"
check_monitor_cleaned "explicit install watchdog still bounds monitor orphan"

# Kill the transaction while the real FD7-acquisition child is selected and
# alive.  It has not called the kernel flock yet, so both ordered probes must be
# immediately free even though the child survives its parent.
write_mode_b_legacy
set_boot_policy false false
prepare_held_child fd7-acquire
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" ASSOC_TIMEOUT_DEFAULT=10 \
    bash "$WIFI_SH" 0 connect >/dev/null 2>&1 &
_wifi_pid=$!
wait_for_held_child_no_monitor \
    "FD7 acquisition SIGKILL fixture holds real flock child live"
kill -KILL "$_wifi_pid" 2>/dev/null || true
wait "$_wifi_pid" 2>/dev/null || true
check_held_child_survives_owner_reap \
    "FD7 acquisition child remains live after owner SIGKILL and reap"
check_ordered_writer_locks_available \
    "FD7 acquisition orphan closes FD9 and owns neither ordered lock"
cleanup_held_child "FD7 acquisition SIGKILL fixture cleans held child"

# OPC's first post-lock ctrl child is transitive through wcli().  It must close
# both private descriptors before exec just like the connect graph.
write_mode_b_legacy
set_boot_policy false false
prepare_held_child opc-ping
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$RUN_DIR" \
    sh "$OPC_SH" mlan0 freq "5180 5200" >/dev/null 2>&1 &
_opc_pid=$!
wait_for_held_child_no_monitor "OPC SIGKILL fixture holds wpa_cli ping child live"
kill -KILL "$_opc_pid" 2>/dev/null || true
wait "$_opc_pid" 2>/dev/null || true
check_held_child_survives_owner_reap \
    "OPC wpa_cli child remains live after owner SIGKILL and reap"
check_ordered_writer_locks_available \
    "OPC held-child SIGKILL releases FD9 then FD7 immediately"
cleanup_held_child "OPC SIGKILL fixture cleans held child"

# wifi freq is representative of the FD9-only writer family.  Exact source
# inventory below covers freq/ssid/psk/key; this causal fixture proves the
# close-first boundary with a selected install child.
write_mode_b_legacy
set_boot_policy false false
prepare_held_child fd9-install
: > "$CALL_LOG"
INSTALL_MODE=hold-fd9-writer WPA_CONF_DIR="$WPA_DIR" \
    bash "$WIFI_SH" 0 freq 5180 5200 >/dev/null 2>&1 &
_wifi_pid=$!
wait_for_held_child_no_monitor "FD9-only writer holds install child live"
kill -KILL "$_wifi_pid" 2>/dev/null || true
wait "$_wifi_pid" 2>/dev/null || true
check_held_child_survives_owner_reap \
    "FD9-only install child remains live after owner SIGKILL and reap"
check_ordered_writer_locks_available \
    "FD9-only writer SIGKILL releases FD9 and leaves FD7 free"
cleanup_held_child "FD9-only writer SIGKILL fixture cleans held child"

# The close-and-exec primitive is shared with POSIX-sh callers.  Normalize a
# missing reader in the owning shell, not inside the substitution child, and
# prove successful capture stays byte-exact (modulo shell-mandated trailing
# newline removal) in both shells.  The extracted production cleanup function
# then exercises the missing private PID file under its real `set -e` context.
check_child_substitution_normalization bash
check_child_substitution_normalization sh
check_missing_pid_cleanup_set_e

# Keep a narrow static inventory over the helper cluster and the connect
# source slice that execute after FD9/FD7 acquisition.  Every raw external
# token must sit behind one of the close-first boundaries; the exact boundary
# inventory also forces future wrapped children to be reviewed deliberately.
if python3 - "$WIFI_SH" "$SCRIPT_DIR/wifi_init_config_lib.sh" <<'PY'
import re
import sys

wifi_path, lib_path = sys.argv[1:]
wifi = open(wifi_path, encoding="utf-8").read()
lib = open(lib_path, encoding="utf-8").read()

helper_start = wifi.index("wpa_cli_ok() {")
helper_end = wifi.index("# ----- radio staged-apply helpers -----", helper_start)
connect_case = wifi.index("  connect)")
connect_start = wifi.index('    wifi_wpa_conf_lock_acquire "$IFACE"', connect_case)
connect_end = wifi.index("\n    ;;\n  scan)", connect_start)
abort_start = lib.index("wifi_wpa_abort_scan_quiesce() {")
abort_end = lib.index("\n}\n\n# FD 7 serializes", abort_start) + 2
regions = wifi[helper_start:helper_end] + wifi[connect_start:connect_end] \
    + lib[abort_start:abort_end]

external = (
    "awk", "cat", "chmod", "dirname", "grep", "install", "jq", "mkdir",
    "mktemp", "mv", "rm", "sleep", "sync", "tr", "wpa_cli",
)
command = "(" + "|".join(external) + r")\b"
command_start = re.compile(
    r"^(?:(?:if|elif|while|until)\s+)?(?:!\s+)?"
    r"(?:[A-Za-z_][A-Za-z0-9_]*=(?:\"[^\"]*\"|'[^']*'|\S+)\s+)*"
    + command
)
command_after_operator = re.compile(r"(?:\$\(|<\(|[;&|])\s*" + command)
wrappers = (
    "wifi_wpa_child_exec", "wifi_wpa_child_call",
    "wifi_wpa_run_child", "wifi_wpa_run_child_call",
)

violations = []
in_heredoc = False
action_body = []
for number, line in enumerate(regions.splitlines(), 1):
    stripped = line.strip()
    if in_heredoc:
        if stripped == "EOF":
            in_heredoc = False
        else:
            action_body.append(stripped)
        continue
    if "<<'EOF'" in line:
        in_heredoc = True
        continue
    if not stripped or stripped.startswith("#"):
        continue
    has_raw_command = bool(command_start.search(stripped) \
        or command_after_operator.search(line))
    if stripped.startswith("trap ") and re.search(command, line):
        has_raw_command = True
    if has_raw_command and not any(wrapper in line for wrapper in wrappers):
        violations.append(f"line {number}: {stripped}")

if "exec 7>&- 9>&-" not in action_body:
    violations.append("monitor action heredoc lacks an explicit FD7/FD9 close")

inventory = {wrapper: set() for wrapper in wrappers}
wrapper_re = re.compile(
    r"\b(" + "|".join(sorted(wrappers, key=len, reverse=True))
    + r")\s+([A-Za-z_][A-Za-z0-9_]*)"
)
for wrapper, command in wrapper_re.findall(regions):
    inventory[wrapper].add(command)

expected = {
    "wifi_wpa_child_exec": {"cat", "mktemp", "wpa_cli"},
    "wifi_wpa_child_call": {
        "to_freq_mhz_checked", "wifi_ssid_to_hex", "wifi_ssid_to_wpa_text",
        "wifi_wpa_conf_common_freqs",
    },
    "wifi_wpa_run_child": {
        "awk", "cat", "chmod", "mkdir", "rm", "sleep", "sync", "wpa_cli",
    },
    "wifi_wpa_run_child_call": {
        "safe_install_sync", "wifi_wpa_conf_is_multi_topology",
        "wifi_wpa_conf_render_canonical",
    },
}
if inventory != expected:
    violations.append(f"inventory mismatch: expected={expected!r} actual={inventory!r}")

if violations:
    print("post-lock child isolation violations:", file=sys.stderr)
    print("\n".join(violations), file=sys.stderr)
    raise SystemExit(1)
PY
then
    pass "wifi connect post-lock external-command inventory is complete and isolated"
else
    fail "wifi connect post-lock external-command inventory must stay complete and isolated"
fi

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
set_monitor_mode matching-zero
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
if grep -Fq 'bgscan.passive=true' "$GUIDE" \
   && grep -Fq 'active scan으로 강제' "$GUIDE" \
   && grep -Fq 'wpa_cli -i <iface> SCAN passive=1' "$GUIDE" \
   && grep -Fq 'STAGED_SCAN.home_passive' "$GUIDE" \
   && grep -Fq '별개' "$GUIDE"; then
    pass "bgscan guide documents iw safety override and native passive grammar"
else
    fail "bgscan guide documents iw safety override and native passive grammar"
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
echo "=== scan-transition serialization and fresh association proof ==="

# Production owns a 15-second bounded wait; the explicit zero override below is
# test-only so held-lock cases remain deterministic and fast.
write_mode_a_legacy
set_boot_policy true true '["Office"]'
set_abort_scan_mode plain-fail
set_status_mode mode-a-steady
set_monitor_mode matching
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$RUN_DIR" ASSOC_TIMEOUT_DEFAULT=1 \
    bash "$WIFI_SH" 0 connect >/dev/null 2>&1
rc=$?
check_equal "wifi connect retains the production association budget" "$rc" "0"
_conf_lock_line=$(grep -nE '^flock .*(^| )9$' "$CALL_LOG" | head -1 | cut -d: -f1)
_transition_lock_line=$(grep -nE '^flock -w 15 .* 7$' "$CALL_LOG" | head -1 | cut -d: -f1)
if [ -n "$_conf_lock_line" ] && [ -n "$_transition_lock_line" ] \
   && [ "$_conf_lock_line" -lt "$_transition_lock_line" ]; then
    pass "wifi connect acquires conf lock before 15-second scan-transition lock"
else
    fail "wifi connect must acquire FD9 then bounded FD7 lock (conf=${_conf_lock_line:-missing} transition=${_transition_lock_line:-missing})"
fi
check_equal "wifi connect accepts deployed plain FAIL ABORT_SCAN reply" \
    "$(grep -c 'abort_scan$' "$CALL_LOG" || true)" "1"

for _abort_mode in ok-then-fail plain-fail; do
    case "$_abort_mode" in
        ok-then-fail) _expected_abort_calls=2 ;;
        plain-fail)   _expected_abort_calls=1 ;;
    esac
    write_mode_b_legacy
    set_boot_policy false false
    set_abort_scan_mode "$_abort_mode"
    set_status_mode target
    set_monitor_mode reconfigure-event
    : > "$CALL_LOG"
    WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$RUN_DIR" ASSOC_TIMEOUT_DEFAULT=2 \
        bash "$WIFI_SH" 0 connect NewNet 5180 >/dev/null 2>&1
    rc=$?
    check_equal "explicit Mode B accepts $_abort_mode ABORT_SCAN" "$rc" "0"
    check_equal "explicit Mode B $_abort_mode reaches quiescent FAIL" \
        "$(grep -c 'abort_scan$' "$CALL_LOG" || true)" "$_expected_abort_calls"
    check_monitor_attached_before_reconfigure \
        "explicit Mode B attaches fresh-event monitor before reconfigure ($_abort_mode)"
    check_equal "fresh reconfigure proof avoids redundant reassociate ($_abort_mode)" \
        "$(grep -Ec 'reassociate$|reconnect$' "$CALL_LOG" || true)" "0"
done

for _abort_mode in empty other rc-ok; do
    write_mode_b_legacy
    set_boot_policy false false
    set_abort_scan_mode "$_abort_mode"
    cp "$CONF" "$TD/wifi-abort-${_abort_mode}.conf"
    : > "$CALL_LOG"
    WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$RUN_DIR" ASSOC_TIMEOUT_DEFAULT=1 \
        bash "$WIFI_SH" 0 connect NewNet 5180 >/dev/null 2>&1
    rc=$?
    if [ "$rc" -ne 0 ]; then pass "wifi rejects $_abort_mode ABORT_SCAN before mutation"; else fail "wifi must reject $_abort_mode ABORT_SCAN before mutation"; fi
    if cmp -s "$CONF" "$TD/wifi-abort-${_abort_mode}.conf"; then
        pass "wifi $_abort_mode ABORT_SCAN rejection leaves conf byte-exact"
    else
        fail "wifi $_abort_mode ABORT_SCAN rejection must leave conf byte-exact"
    fi
    check_equal "wifi $_abort_mode rejection sends no association request" \
        "$(grep -Ec 'reconfigure$|reassociate$|reconnect$' "$CALL_LOG" || true)" "0"
done

write_mode_b_legacy
set_boot_policy false false
set_abort_scan_mode other
cp "$CONF" "$TD/opc-abort-other.conf"
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$RUN_DIR" \
    sh "$OPC_SH" mlan0 freq "5180 5200" >/dev/null 2>&1
rc=$?
if [ "$rc" -ne 0 ]; then pass "OPC rejects invalid ABORT_SCAN before transaction"; else fail "OPC must reject invalid ABORT_SCAN before transaction"; fi
if cmp -s "$CONF" "$TD/opc-abort-other.conf"; then
    pass "OPC abort rejection leaves conf byte-exact"
else
    fail "OPC abort rejection must leave conf byte-exact"
fi
check_equal "OPC abort rejection sends no reconfigure" \
    "$(grep -c 'reconfigure$' "$CALL_LOG" || true)" "0"

write_mode_b_legacy
set_boot_policy false false
set_abort_scan_mode ok-then-fail
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$RUN_DIR" \
    sh "$OPC_SH" mlan0 freq "5180 5200" >/dev/null 2>&1
check_equal "OPC accepts OK then quiescent FAIL ABORT_SCAN" "$?" "0"
check_equal "OPC polls ABORT_SCAN through quiescent FAIL" \
    "$(grep -c 'abort_scan$' "$CALL_LOG" || true)" "2"

# An accepted abort is not quiescence.  Exhaustion and any later malformed or
# transport reply must fail before conf mutation or association-changing calls.
for _late_abort_mode in always-ok ok-then-empty ok-then-other ok-then-rc-ok; do
    case "$_late_abort_mode" in
        always-ok) _expected_abort_calls=5 ;;
        *)         _expected_abort_calls=2 ;;
    esac

    write_mode_b_legacy
    set_boot_policy false false
    set_abort_scan_mode "$_late_abort_mode"
    cp "$CONF" "$TD/wifi-${_late_abort_mode}.conf"
    : > "$CALL_LOG"
    WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$RUN_DIR" ASSOC_TIMEOUT_DEFAULT=1 \
        bash "$WIFI_SH" 0 connect NewNet 5180 >/dev/null 2>&1
    rc=$?
    if [ "$rc" -ne 0 ]; then
        pass "wifi rejects $_late_abort_mode before mutation"
    else
        fail "wifi must reject $_late_abort_mode before mutation"
    fi
    if cmp -s "$CONF" "$TD/wifi-${_late_abort_mode}.conf"; then
        pass "wifi $_late_abort_mode rejection leaves conf byte-exact"
    else
        fail "wifi $_late_abort_mode rejection must leave conf byte-exact"
    fi
    check_equal "wifi $_late_abort_mode stops after bounded ABORT_SCAN calls" \
        "$(grep -c 'abort_scan$' "$CALL_LOG" || true)" "$_expected_abort_calls"
    check_equal "wifi $_late_abort_mode sends no association command" \
        "$(grep -Ec 'reconfigure$|reassociate$|reconnect$' "$CALL_LOG" || true)" "0"

    write_mode_b_legacy
    set_boot_policy false false
    set_abort_scan_mode "$_late_abort_mode"
    cp "$CONF" "$TD/opc-${_late_abort_mode}.conf"
    : > "$CALL_LOG"
    WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$RUN_DIR" \
        sh "$OPC_SH" mlan0 freq "5180 5200" >/dev/null 2>&1
    rc=$?
    if [ "$rc" -ne 0 ]; then
        pass "OPC rejects $_late_abort_mode before transaction"
    else
        fail "OPC must reject $_late_abort_mode before transaction"
    fi
    if cmp -s "$CONF" "$TD/opc-${_late_abort_mode}.conf"; then
        pass "OPC $_late_abort_mode rejection leaves conf byte-exact"
    else
        fail "OPC $_late_abort_mode rejection must leave conf byte-exact"
    fi
    check_equal "OPC $_late_abort_mode stops after bounded ABORT_SCAN calls" \
        "$(grep -c 'abort_scan$' "$CALL_LOG" || true)" "$_expected_abort_calls"
    check_equal "OPC $_late_abort_mode sends no reconfigure" \
        "$(grep -c 'reconfigure$' "$CALL_LOG" || true)" "0"
done

# Held scan-transition lock: both writers fail before touching the conf or
# issuing ABORT_SCAN/reconfigure/reassociate.  The explicit timeout override
# keeps this RED test independent of the production 15-second bound.
write_mode_b_legacy
set_boot_policy false false
set_abort_scan_mode plain-fail
cp "$CONF" "$TD/wifi-held-transition.conf"
hold_scan_transition_lock || fail "test fixture acquires held scan-transition lock"
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$RUN_DIR" WIFI_SCAN_TRANSITION_LOCK_TIMEOUT=0 \
    bash "$WIFI_SH" 0 connect NewNet 5180 >/dev/null 2>&1
rc=$?
release_scan_transition_lock
if [ "$rc" -ne 0 ]; then pass "wifi held scan-transition lock fails closed"; else fail "wifi must fail closed on held scan-transition lock"; fi
if cmp -s "$CONF" "$TD/wifi-held-transition.conf"; then pass "wifi held transition lock leaves conf byte-exact"; else fail "wifi held transition lock must preserve conf"; fi
check_equal "wifi held transition lock sends no live ctrl request" \
    "$(grep -Ec 'abort_scan$|reconfigure$|reassociate$|reconnect$' "$CALL_LOG" || true)" "0"

write_mode_b_legacy
set_boot_policy false false
cp "$CONF" "$TD/opc-held-transition.conf"
hold_scan_transition_lock || fail "OPC test fixture acquires held scan-transition lock"
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$RUN_DIR" WIFI_SCAN_TRANSITION_LOCK_TIMEOUT=0 \
    sh "$OPC_SH" mlan0 freq "5180 5200" >/dev/null 2>&1
rc=$?
release_scan_transition_lock
if [ "$rc" -ne 0 ]; then pass "OPC held scan-transition lock fails closed"; else fail "OPC must fail closed on held scan-transition lock"; fi
if cmp -s "$CONF" "$TD/opc-held-transition.conf"; then pass "OPC held transition lock leaves conf byte-exact"; else fail "OPC held transition lock must preserve conf"; fi
check_equal "OPC held transition lock sends no live ctrl request" \
    "$(grep -Ec 'abort_scan$|reconfigure$|reassociate$|reconnect$' "$CALL_LOG" || true)" "0"

for _manual_op in scan mscan; do
    hold_scan_transition_lock || fail "${_manual_op} test fixture acquires held scan-transition lock"
    : > "$CALL_LOG"
    WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$RUN_DIR" WIFI_SCAN_TRANSITION_LOCK_TIMEOUT=0 \
        bash "$WIFI_SH" 0 "$_manual_op" 36 >/dev/null 2>&1
    rc=$?
    release_scan_transition_lock
    if [ "$rc" -ne 0 ]; then
        pass "wifi ${_manual_op} fails closed on held scan-transition lock"
    else
        fail "wifi ${_manual_op} must fail closed on held scan-transition lock"
    fi
    check_equal "wifi ${_manual_op} held lock starts no driver scan" \
        "$(grep -Ec '^iw |^mlanutl ' "$CALL_LOG" || true)" "0"
done

# supplicant lifecycle도 scan/connect와 같은 transition gateway를 사용한다.
for _lifecycle_op in restart start stop; do
    hold_scan_transition_lock || fail "${_lifecycle_op} fixture acquires held transition lock"
    : > "$CALL_LOG"
    WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$RUN_DIR" WIFI_SCAN_TRANSITION_LOCK_TIMEOUT=0 \
        ASSOC_TIMEOUT_DEFAULT=1 bash "$WIFI_SH" 0 "$_lifecycle_op" >/dev/null 2>&1
    rc=$?
    release_scan_transition_lock
    if [ "$rc" -ne 0 ]; then
        pass "wifi ${_lifecycle_op} fails closed on held transition lock"
    else
        fail "wifi ${_lifecycle_op} must fail closed on held transition lock"
    fi
    check_equal "wifi ${_lifecycle_op} held lock changes no service state" \
        "$(grep -c '^systemctl ' "$CALL_LOG" || true)" "0"
done

for _lifecycle_op in restart stop; do
    set_wpa_service_state active
    set_abort_scan_mode ok-then-fail
    set_status_mode base
    : > "$CALL_LOG"
    WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$RUN_DIR" ASSOC_TIMEOUT_DEFAULT=1 \
        bash "$WIFI_SH" 0 "$_lifecycle_op" >/dev/null 2>&1
    rc=$?
    check_equal "wifi ${_lifecycle_op} serialized lifecycle succeeds" "$rc" "0"
    check_equal "wifi ${_lifecycle_op} quiesces native scan" \
        "$(grep -c 'abort_scan$' "$CALL_LOG" || true)" "2"
done

# already-active start is an idempotent service no-op under FD7.  It neither
# cancels an accepted scan nor probes/changes association, and always exits 0.
set_wpa_service_state active
set_abort_scan_mode always-ok
set_status_mode base
: > "$CALL_LOG"
start_output=$(WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$RUN_DIR" \
    ASSOC_TIMEOUT_DEFAULT=1 bash "$WIFI_SH" 0 start 2>&1)
rc=$?
check_equal "wifi start already-active COMPLETED exits zero" "$rc" "0"
check_equal "wifi start already-active ignores ABORT_SCAN non-convergence" \
    "$(grep -c 'abort_scan$' "$CALL_LOG" || true)" "0"
check_equal "wifi start already-active starts no service" \
    "$(grep -c '^systemctl start ' "$CALL_LOG" || true)" "0"
check_equal "wifi start already-active probes no association" \
    "$(grep -c ' status$' "$CALL_LOG" || true)" "0"
case "$start_output" in
    *"already active"*"scan/association unchanged"*)
        pass "wifi start reports already-active no-op contract" ;;
    *) fail "wifi start must report already-active no-op contract" ;;
esac
_lock_line=$(grep -nE '^flock -w 15 -x 7$' "$CALL_LOG" | head -1 | cut -d: -f1)
_active_line=$(grep -nE '^systemctl is-active --quiet wpa_supplicant@mlan0$' "$CALL_LOG" | head -1 | cut -d: -f1)
if [ -n "$_lock_line" ] && [ -n "$_active_line" ] \
   && [ "$_lock_line" -lt "$_active_line" ]; then
    pass "wifi start checks active state after transition lock"
else
    fail "wifi start must check active state after transition lock"
fi

set_wpa_service_state active
set_abort_scan_mode always-ok
set_status_mode disconnected
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$RUN_DIR" ASSOC_TIMEOUT_DEFAULT=1 \
    bash "$WIFI_SH" 0 up >/dev/null 2>&1
rc=$?
check_equal "wifi up already-active disconnected exits zero" "$rc" "0"
check_equal "wifi up already-active disconnected changes no scan state" \
    "$(grep -c 'abort_scan$' "$CALL_LOG" || true)" "0"
check_equal "wifi up already-active disconnected starts no service" \
    "$(grep -c '^systemctl start ' "$CALL_LOG" || true)" "0"
check_equal "wifi up already-active disconnected probes no association" \
    "$(grep -c ' status$' "$CALL_LOG" || true)" "0"

# Inactive start retains PR #195's quiesce -> start -> bounded COMPLETED path.
set_wpa_service_state inactive
set_abort_scan_mode ok-then-fail
set_status_mode base
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$RUN_DIR" ASSOC_TIMEOUT_DEFAULT=1 \
    bash "$WIFI_SH" 0 start >/dev/null 2>&1
rc=$?
check_equal "wifi start inactive associated exits zero" "$rc" "0"
check_equal "wifi start inactive quiesces accepted native scan" \
    "$(grep -c 'abort_scan$' "$CALL_LOG" || true)" "2"
check_equal "wifi start inactive starts service once" \
    "$(grep -c '^systemctl start wpa_supplicant@mlan0$' "$CALL_LOG" || true)" "1"
check_equal "wifi start inactive proves COMPLETED" \
    "$(grep -c ' status$' "$CALL_LOG" || true)" "1"

set_wpa_service_state inactive
set_abort_scan_mode always-ok
set_status_mode base
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$RUN_DIR" ASSOC_TIMEOUT_DEFAULT=1 \
    bash "$WIFI_SH" 0 start >/dev/null 2>&1
rc=$?
check_equal "wifi start inactive survives non-quiescent stale control path" "$rc" "0"
check_equal "wifi start inactive bounds ABORT_SCAN attempts" \
    "$(grep -c 'abort_scan$' "$CALL_LOG" || true)" "5"
check_equal "wifi start inactive still starts after quiesce warning" \
    "$(grep -c '^systemctl start wpa_supplicant@mlan0$' "$CALL_LOG" || true)" "1"

set_wpa_service_state inactive
set_abort_scan_mode plain-fail
set_status_mode disconnected
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$RUN_DIR" ASSOC_TIMEOUT_DEFAULT=1 \
    bash "$WIFI_SH" 0 start >/dev/null 2>&1
rc=$?
check_equal "wifi start inactive disconnected exits association-timeout" "$rc" "8"
check_equal "wifi start inactive disconnected still starts service once" \
    "$(grep -c '^systemctl start wpa_supplicant@mlan0$' "$CALL_LOG" || true)" "1"
check_equal "wifi start inactive disconnected uses bounded status polls" \
    "$(grep -c ' status$' "$CALL_LOG" || true)" "10"

usage_output=$(WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$RUN_DIR" \
    bash "$WIFI_SH" invalid start 2>&1 || true)
case "$usage_output" in
    *"start/up: active=no-op"*"inactive=quiesce+start+COMPLETED"*)
        pass "wifi usage documents active/inactive start contract" ;;
    *) fail "wifi usage must document active/inactive start contract" ;;
esac

# FD7을 wpa_cli scan 요청이 반환한 뒤 획득했더라도 native scan은 비동기로 계속될 수
# 있다. raw driver scan 전에 ABORT_SCAN이 OK→FAIL로 수렴할 때까지 quiesce해야 한다.
for _manual_op in scan mscan; do
    set_abort_scan_mode ok-then-fail
    : > "$CALL_LOG"
    WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$RUN_DIR" \
        bash "$WIFI_SH" 0 "$_manual_op" 36 >/dev/null 2>&1
    rc=$?
    check_equal "wifi ${_manual_op} quiesces accepted native scan" "$rc" "0"
    check_equal "wifi ${_manual_op} waits for ABORT_SCAN FAIL" \
        "$(grep -c 'abort_scan$' "$CALL_LOG" || true)" "2"
    _abort_line=$(grep -n 'abort_scan$' "$CALL_LOG" | tail -1 | cut -d: -f1)
    _driver_line=$(grep -nE '^iw |^mlanutl ' "$CALL_LOG" | head -1 | cut -d: -f1)
    if [ -n "$_abort_line" ] && [ -n "$_driver_line" ] && [ "$_abort_line" -lt "$_driver_line" ]; then
        pass "wifi ${_manual_op} starts driver scan only after native quiescence"
    else
        fail "wifi ${_manual_op} must quiesce native scan before driver scan"
    fi

    set_abort_scan_mode always-ok
    : > "$CALL_LOG"
    WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$RUN_DIR" \
        bash "$WIFI_SH" 0 "$_manual_op" 36 >/dev/null 2>&1
    rc=$?
    if [ "$rc" -ne 0 ]; then
        pass "wifi ${_manual_op} rejects non-quiescent native scan"
    else
        fail "wifi ${_manual_op} must reject non-quiescent native scan"
    fi
    check_equal "wifi ${_manual_op} non-quiescent path starts no driver scan" \
        "$(grep -Ec '^iw |^mlanutl ' "$CALL_LOG" || true)" "0"
done

# Mode B and disconnected Mode A now use the same fresh-event transaction as
# capture-id Mode A; a stale COMPLETED snapshot alone must never prove success.
write_mode_b_legacy
set_boot_policy false false
set_abort_scan_mode plain-fail
set_status_mode base
set_monitor_mode stale
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$RUN_DIR" ASSOC_TIMEOUT_DEFAULT=1 \
    bash "$WIFI_SH" 0 connect >/dev/null 2>&1
check_equal "Mode B no-arg reconnect rejects stale COMPLETED without fresh event" "$?" "8"

write_mode_b_legacy
set_status_mode base
set_monitor_mode matching-zero
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$RUN_DIR" ASSOC_TIMEOUT_DEFAULT=1 \
    bash "$WIFI_SH" 0 connect >/dev/null 2>&1
check_equal "Mode B no-arg reconnect accepts fresh matching event and status" "$?" "0"
check_monitor_attached_before_request "Mode B fresh proof attaches monitor before reassociate"
check_monitor_cleaned "Mode B fresh proof cleans private monitor"

write_mode_a_legacy
set_boot_policy true true '["Office"]'
set_status_mode no-current
set_monitor_mode stale
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$RUN_DIR" ASSOC_TIMEOUT_DEFAULT=1 \
    bash "$WIFI_SH" 0 connect >/dev/null 2>&1
check_equal "Mode A disconnected recovery rejects stale/no-event COMPLETED" "$?" "8"

write_mode_a_legacy
set_status_mode no-current-recover
set_monitor_mode matching-zero
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$RUN_DIR" ASSOC_TIMEOUT_DEFAULT=1 \
    bash "$WIFI_SH" 0 connect >/dev/null 2>&1
check_equal "Mode A disconnected recovery accepts fresh event id and later matching status" "$?" "0"
check_monitor_attached_before_request "Mode A disconnected recovery attaches monitor before reassociate"

# If RECONFIGURE has no fresh proof, the monitor must be re-armed and exactly
# one owner-neutral reassociation uses the remainder of the original budget.
write_mode_b_legacy
set_boot_policy false false
set_abort_scan_mode plain-fail
set_status_mode target
set_monitor_mode matching-zero
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$RUN_DIR" ASSOC_TIMEOUT_DEFAULT=2 \
    bash "$WIFI_SH" 0 connect NewNet 5180 >/dev/null 2>&1
check_equal "explicit Mode B fallback reassociation succeeds with fresh proof" "$?" "0"
check_equal "explicit Mode B fallback issues exactly one reassociate" \
    "$(grep -c 'reassociate$' "$CALL_LOG" || true)" "1"

# Delayed reconfigure proof must consume the shared budget but still avoid a
# redundant reassociation when the event and subsequent status arrive in grace.
write_mode_b_legacy
set_boot_policy false false
set_status_mode target-delayed-grace
set_monitor_mode delayed-grace
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$RUN_DIR" ASSOC_TIMEOUT_DEFAULT=1 \
    bash "$WIFI_SH" 0 connect NewNet 5180 >/dev/null 2>&1
check_equal "delayed fresh explicit target proof succeeds within grace" "$?" "0"
check_equal "delayed grace proof avoids reassociate/reconnect" \
    "$(grep -Ec 'reassociate$|reconnect$' "$CALL_LOG" || true)" "0"
check_equal "delayed grace proof uses three shared-budget status polls" \
    "$(grep -c ' status$' "$CALL_LOG" || true)" "3"

# With no event, grace plus fallback must share exactly TOTAL_POLLS.  An
# accepted reassociate is not followed by reconnect merely because proof times out.
write_mode_b_legacy
set_boot_policy false false
set_status_mode target
set_monitor_mode stale
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$RUN_DIR" ASSOC_TIMEOUT_DEFAULT=1 \
    bash "$WIFI_SH" 0 connect NewNet 5180 >/dev/null 2>&1
check_equal "absent event cannot prove explicit target before timeout" "$?" "8"
check_equal "explicit grace plus fallback is bounded by TOTAL_POLLS" \
    "$(grep -c ' status$' "$CALL_LOG" || true)" "10"
check_equal "explicit no-proof timeout issues exactly one accepted reassociate" \
    "$(grep -c 'reassociate$' "$CALL_LOG" || true)" "1"
check_equal "accepted reassociate timeout never issues reconnect" \
    "$(grep -c 'reconnect$' "$CALL_LOG" || true)" "0"

# Grace consumes three polls; the event appears during fallback poll six and
# therefore needs the subsequent seventh status snapshot for causal success.
write_mode_b_legacy
set_boot_policy false false
set_status_mode target-delayed-fallback
set_monitor_mode delayed-fallback
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$RUN_DIR" ASSOC_TIMEOUT_DEFAULT=1 \
    bash "$WIFI_SH" 0 connect NewNet 5180 >/dev/null 2>&1
check_equal "fresh proof succeeds in remaining fallback budget" "$?" "0"
check_equal "fallback proof does not reset total poll budget" \
    "$(grep -c ' status$' "$CALL_LOG" || true)" "7"
check_equal "fallback proof uses exactly one reassociate" \
    "$(grep -c 'reassociate$' "$CALL_LOG" || true)" "1"
check_equal "fallback proof after accepted reassociate uses no reconnect" \
    "$(grep -c 'reconnect$' "$CALL_LOG" || true)" "0"

write_mode_b_legacy
set_boot_policy false false
set_status_mode mode-b-id-switch
set_monitor_mode matching
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$RUN_DIR" ASSOC_TIMEOUT_DEFAULT=1 \
    bash "$WIFI_SH" 0 connect >/dev/null 2>&1
check_equal "Mode B no-arg current id 0 rejects fresh event/status id 1" "$?" "8"

for _status_id_mode in target-no-id target-nonnumeric-id; do
    write_mode_b_legacy
    set_boot_policy false false
    set_status_mode "$_status_id_mode"
    set_monitor_mode reconfigure-event
    : > "$CALL_LOG"
    WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$RUN_DIR" ASSOC_TIMEOUT_DEFAULT=1 \
        bash "$WIFI_SH" 0 connect NewNet 5180 >/dev/null 2>&1
    check_equal "explicit numeric event rejects ${_status_id_mode} status" "$?" "8"
done

# Only the zero override is honored.  All other values normalize to the
# production 15-second bound; fixtures are uncontended so these never wait.
for _lock_case in zero one large invalid empty default; do
    write_mode_b_legacy
    set_boot_policy false false
    set_status_mode base
    set_monitor_mode matching-zero
    : > "$CALL_LOG"
    case "$_lock_case" in
        zero)
            _lock_value=0; _expected_timeout=0
            WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$RUN_DIR" \
                WIFI_SCAN_TRANSITION_LOCK_TIMEOUT="$_lock_value" ASSOC_TIMEOUT_DEFAULT=1 \
                bash "$WIFI_SH" 0 connect >/dev/null 2>&1 ;;
        one)
            _lock_value=1; _expected_timeout=15
            WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$RUN_DIR" \
                WIFI_SCAN_TRANSITION_LOCK_TIMEOUT="$_lock_value" ASSOC_TIMEOUT_DEFAULT=1 \
                bash "$WIFI_SH" 0 connect >/dev/null 2>&1 ;;
        large)
            _lock_value=16; _expected_timeout=15
            WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$RUN_DIR" \
                WIFI_SCAN_TRANSITION_LOCK_TIMEOUT="$_lock_value" ASSOC_TIMEOUT_DEFAULT=1 \
                bash "$WIFI_SH" 0 connect >/dev/null 2>&1 ;;
        invalid)
            _lock_value=invalid; _expected_timeout=15
            WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$RUN_DIR" \
                WIFI_SCAN_TRANSITION_LOCK_TIMEOUT="$_lock_value" ASSOC_TIMEOUT_DEFAULT=1 \
                bash "$WIFI_SH" 0 connect >/dev/null 2>&1 ;;
        empty)
            _lock_value=empty; _expected_timeout=15
            WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$RUN_DIR" \
                WIFI_SCAN_TRANSITION_LOCK_TIMEOUT="" ASSOC_TIMEOUT_DEFAULT=1 \
                bash "$WIFI_SH" 0 connect >/dev/null 2>&1 ;;
        default)
            _lock_value=default; _expected_timeout=15
            env -u WIFI_SCAN_TRANSITION_LOCK_TIMEOUT \
                WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$RUN_DIR" ASSOC_TIMEOUT_DEFAULT=1 \
                bash "$WIFI_SH" 0 connect >/dev/null 2>&1 ;;
    esac
    check_equal "scan-transition timeout ${_lock_value} fixture succeeds" "$?" "0"
    check_equal "scan-transition timeout ${_lock_value} uses flock -w ${_expected_timeout}" \
        "$(grep -Ec "^flock -w ${_expected_timeout} -x 7$" "$CALL_LOG" || true)" "1"
done

check_scan_transition_lock_available "normal writer exit leaves scan-transition lock acquirable"

echo ""
echo "RESULT: PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
