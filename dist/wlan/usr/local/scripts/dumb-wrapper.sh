#!/bin/sh
set -eu
IF0=eth0
IF1=mlan0

both_up() {
    ip link show "$IF0" | grep -q "state UP" || return 1
    ip link show "$IF1" | grep -q "state UP" || return 1
    cat /sys/class/net/$IF0/carrier 2>/dev/null | grep -q 1 || return 1
    cat /sys/class/net/$IF1/carrier 2>/dev/null | grep -q 1 || return 1
}

# 링크 둘 다 살아날 때까지 대기
for _ in $(seq 1 200); do
    if both_up; then break; fi
    sleep 0.2
done

exec /usr/local/bin/dumb_test eth0 mlan0
