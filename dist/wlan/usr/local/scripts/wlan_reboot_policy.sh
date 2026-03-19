#!/bin/bash

set -euo pipefail

tag=$(basename "$0")

SOURCE=""
IFACE=""
REASON=""
FORCE=0

usage() {
  echo "Usage: $tag --reason <text> [--source <name>] [--iface <iface>] [--force]" >&2
}

log_kmsg() {
  local msg="$1"
  if [ -w /dev/kmsg ]; then
    echo "wlan-policy: $msg" > /dev/kmsg
  fi
}

log_syslog() {
  local msg="$1"
  if command -v logger >/dev/null 2>&1; then
    logger -p local0.warning "[$tag] $msg"
  fi
}

log_all() {
  local msg="$1"
  log_syslog "$msg"
  log_kmsg "$msg"
}

do_reboot() {
  # Try graceful reboot first, fall back to forced reboot
  sync
  if command -v /sbin/reboot >/dev/null 2>&1; then
    /sbin/reboot || /sbin/reboot -f
    return $?
  fi
  if command -v reboot >/dev/null 2>&1; then
    reboot || reboot -f
    return $?
  fi
  if command -v systemctl >/dev/null 2>&1; then
    systemctl reboot || systemctl reboot --force
    return $?
  fi

  return 127
}

get_uptime_sec() {
  awk '{print int($1)}' /proc/uptime 2>/dev/null || echo 0
}

MIN_UPTIME_SEC=${MIN_UPTIME_SEC:-120}
REBOOT_COOLDOWN_SEC=${REBOOT_COOLDOWN_SEC:-300}
MAX_REBOOT_COUNT=${MAX_REBOOT_COUNT:-3}

STATE_DIR=${STATE_DIR:-/var/log/cantops}
RUN_DIR=${RUN_DIR:-/run/cantops/wlan-policy}

while [ $# -gt 0 ]; do
  case "$1" in
    --source)
      SOURCE=${2:-}; shift 2 ;;
    --iface)
      IFACE=${2:-}; shift 2 ;;
    --reason)
      REASON=${2:-}; shift 2 ;;
    --force)
      FORCE=1; shift ;;
    -h|--help)
      usage; exit 2 ;;
    *)
      echo "Unknown arg: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if [ -z "$REASON" ]; then
  echo "Missing --reason" >&2
  usage
  exit 2
fi

if [[ "$REASON" == *overtemp* ]] || [[ "$SOURCE" == "wifi_logger_temp" ]]; then
  MIN_UPTIME_SEC=0
fi

mkdir -p "$RUN_DIR" 2>/dev/null || true
mkdir -p "$STATE_DIR" 2>/dev/null || true

lockdir="$RUN_DIR/reboot.lockdir"
if ! mkdir "$lockdir" 2>/dev/null; then
  log_all "dedupe: reboot already in progress (source=${SOURCE:-n/a} iface=${IFACE:-n/a} reason=$REASON)"
  exit 0
fi
trap 'rmdir "$lockdir" 2>/dev/null || true' EXIT

now=$(date +%s)
uptime=$(get_uptime_sec)

state_suffix="${IFACE:-global}"
state_file="$STATE_DIR/reboot_policy_${state_suffix}.state"

last_ts=0
count=0
if [ -f "$state_file" ]; then
  read -r last_ts count < "$state_file" 2>/dev/null || true
  last_ts=${last_ts:-0}
  count=${count:-0}
fi

if [ "$FORCE" -ne 1 ] && [ "$uptime" -lt "$MIN_UPTIME_SEC" ]; then
  log_all "refuse: uptime ${uptime}s < ${MIN_UPTIME_SEC}s (source=${SOURCE:-n/a} iface=${IFACE:-n/a} reason=$REASON)"
  exit 10
fi

if [ "$FORCE" -ne 1 ] && [ "$last_ts" -gt 0 ] && [ $((now - last_ts)) -lt "$REBOOT_COOLDOWN_SEC" ]; then
  count=$((count + 1))
else
  count=1
fi

echo "$now $count" > "$state_file" 2>/dev/null || true

if [ "$FORCE" -ne 1 ] && [ "$count" -ge "$MAX_REBOOT_COUNT" ]; then
  log_all "refuse: reboot loop detected (count=${count}/${MAX_REBOOT_COUNT}, cooldown=${REBOOT_COOLDOWN_SEC}s) (source=${SOURCE:-n/a} iface=${IFACE:-n/a} reason=$REASON)"
  exit 11
fi

log_all "reboot: approved (attempt ${count}/${MAX_REBOOT_COUNT}, cooldown=${REBOOT_COOLDOWN_SEC}s, uptime=${uptime}s) (source=${SOURCE:-n/a} iface=${IFACE:-n/a} reason=$REASON)"
sync
if ! do_reboot; then
  rc=$?
  log_all "reboot: failed (rc=$rc) (source=${SOURCE:-n/a} iface=${IFACE:-n/a} reason=$REASON)"
  exit "$rc"
fi
