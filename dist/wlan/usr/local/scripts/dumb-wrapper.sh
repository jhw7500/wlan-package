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

# Wait for both links to come up (40 seconds total: 200 * 0.2)
for _ in $(seq 1 200); do
    if both_up; then break; fi
    sleep 0.2
done

# Verify both interfaces are ready after timeout
if ! both_up; then
    echo "ERROR: Interfaces $IF0 and $IF1 not ready after 40 seconds timeout" >&2
    logger -t dumb-wrapper -p daemon.error "Failed to start: Interfaces $IF0 and $IF1 not ready after 40 seconds"
    exit 1
fi

exec /usr/local/bin/wifi-dumb --ip-filter --no-debug eth0 mlan0
