#!/bin/sh
tag=$(basename "$0")
logger -p local0.info "[$tag:$LINENO] $1"
STATE=/etc/fake-hwclock.data
#echo "hwclock $1"
case "$1" in
  save)
    date +"%Y-%m-%d %H:%M:%S" > "$STATE"
    ;;
  load)
    [ -f "$STATE" ] || exit 0
    DATE_STR="$(cat "$STATE")"
    date -s "$DATE_STR"
    ;;
  *)
    echo "Usage: fake-hwclock {save|load}"
    exit 1
    ;;
esac
