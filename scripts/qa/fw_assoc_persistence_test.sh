#!/bin/bash
set -euo pipefail

HOST="root@192.168.214.5"
IFACE="mlan0"
ACTION="snapshot"
TARGET_BSSID=""
DURATION=15
OUT=""

usage() {
    echo "usage: $0 [--host user@ip] [--iface mlan0|mlan1] [--duration sec] [--out file] snapshot|reconnect|roam [BSSID]" >&2
    exit 64
}

while [ $# -gt 0 ]; do
    case "$1" in
        --host) HOST=${2:-}; shift 2 ;;
        --iface) IFACE=${2:-}; shift 2 ;;
        --duration) DURATION=${2:-}; shift 2 ;;
        --out) OUT=${2:-}; shift 2 ;;
        snapshot|reconnect) ACTION=$1; shift; break ;;
        roam) ACTION=roam; TARGET_BSSID=${2:-}; shift 2; break ;;
        *) usage ;;
    esac
done
case "$IFACE" in mlan0|mlan1) ;; *) usage ;; esac
case "$DURATION" in ''|*[!0-9]*) usage ;; esac
[ "$DURATION" -ge 1 ] || usage
[ "$ACTION" != roam ] || [ -n "$TARGET_BSSID" ] || usage

if [ -z "$OUT" ]; then
    mkdir -p artifacts
    OUT="artifacts/fw-assoc-${IFACE}-${ACTION}-$(date +%Y%m%d-%H%M%S).tsv"
fi

echo "target=$HOST iface=$IFACE action=$ACTION duration=${DURATION}s" >&2
ssh -o BatchMode=yes -o ConnectTimeout=5 "$HOST" \
    "IFACE='$IFACE' DURATION='$DURATION' bash -s" > "$OUT" <<'REMOTE' &
set -u
U=/usr/local/bin/mlanutl
ticks=$((DURATION * 10))
printf 'epoch_ms\twpa_state\tbssid\trate_mode\trate_low\trate_high\trate_interval_10ms\tht\tvht_tx\tvht_rx\the_tx\the_rx\tax_map\n'
for ((i=0; i<ticks; i++)); do
    status=$(wpa_cli -i "$IFACE" status 2>/dev/null || true)
    state=$(printf '%s\n' "$status" | sed -n 's/^wpa_state=//p')
    bssid=$(printf '%s\n' "$status" | sed -n 's/^bssid=//p')
    rate=$($U "$IFACE" rate_adapt_cfg 2>/dev/null || true)
    mode=$(printf '%s\n' "$rate" | awk '/SR RateAdapt Enabled/{print "SR";exit}/Legacy RateAdapt Enabled/{print "legacy";exit}')
    low=$(printf '%s\n' "$rate" | awk '/Low/{print $NF;exit}')
    high=$(printf '%s\n' "$rate" | awk '/High/{print $NF;exit}')
    interval=$(printf '%s\n' "$rate" | awk '/Eval Timer interval/{print $5;exit}')
    mcs=$($U "$IFACE" mcstiercfg 2>/dev/null || true)
    ht=$(printf '%s\n' "$mcs" | sed -n 's/.*HT .*MCS 0~\([0-9]*\).*/\1/p' | head -1)
    vtx=$(printf '%s\n' "$mcs" | awk '/VHT Tx:/{print $3;exit}')
    vrx=$(printf '%s\n' "$mcs" | awk '/VHT Rx:/{print $3;exit}')
    hetx=$(printf '%s\n' "$mcs" | awk '/HE Tx:/{print $3;exit}')
    herx=$(printf '%s\n' "$mcs" | awk '/HE Rx:/{print $3;exit}')
    ax=""
    if [ "$IFACE" = mlan0 ]; then
        ax=$($U "$IFACE" 11axcfg 2>/dev/null | tail -n +2 | tr '\n' ' ' || true)
    fi
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$(date +%s%3N)" "$state" "$bssid" "$mode" "$low" "$high" "$interval" \
        "$ht" "$vtx" "$vrx" "$hetx" "$herx" "$ax"
    sleep 0.1
done
REMOTE
SAMPLER_PID=$!

sleep 1
case "$ACTION" in
    snapshot) ;;
    reconnect)
        ssh -o BatchMode=yes "$HOST" \
            "wpa_cli -i '$IFACE' disconnect >/dev/null; sleep 0.2; wpa_cli -i '$IFACE' reconnect >/dev/null"
        ;;
    roam)
        ssh -o BatchMode=yes "$HOST" "wpa_cli -i '$IFACE' roam '$TARGET_BSSID'"
        ;;
esac
wait "$SAMPLER_PID"
echo "artifact=$OUT" >&2
