#!/bin/bash
# Standalone associated-data-path reproducer for external nl80211 iw scans.
# The only scan requester is this shell process; no wifi_roam/wifi_bgscan is used.
#
# Validated target invocation (run over the wired management interface):
#   ISOLATION_PROFILE=requesters-only \
#     ./iw_external_scan_datapath_repro.sh --ack-disruptive /tmp/iw-repro \
#       mlan0 LAB_SSID_REDACTED WLAN_GATEWAY_IP_REDACTED 10 30
#
# Expected failure signature on the affected moal/mlan build:
#   result=REPRODUCED, final_ping_rc=1, tx_failed delta >= 1000,
#   wpa_state=COMPLETED, unchanged BSSID, and one own=0/ext=1 event per command.
#
# The script intentionally leaves isolated services stopped. Reboot the board
# after collecting artifacts; the affected data path did not recover reliably
# through reassociation/service restart in prior tests.
set -Eeuo pipefail

input_error() {
    echo "ERROR: $*" >&2
    exit 2
}

validate_new_artifact_dir() {
    local path="$1" parent base canonical_parent expected

    if [[ "$path" != /* ]] || [ "$path" = / ]; then
        input_error "artifact directory must be a new absolute artifact directory below an existing canonical parent"
    fi
    if [ -e "$path" ] || [ -L "$path" ]; then
        input_error "artifact path already exists: $path"
    fi
    parent="${path%/*}"
    base="${path##*/}"
    [ -n "$parent" ] || parent=/
    if [ -z "$base" ] || [ ! -d "$parent" ]; then
        input_error "artifact parent must already be a directory: $parent"
    fi
    canonical_parent="$(realpath -e -- "$parent")" \
        || input_error "cannot resolve artifact parent: $parent"
    if [ "$canonical_parent" = / ]; then
        expected="/$base"
    else
        expected="$canonical_parent/$base"
    fi
    [ "$path" = "$expected" ] \
        || input_error "artifact path must use its canonical parent: $path"
}

if [ "${1:-}" != --ack-disruptive ]; then
    echo "ERROR: --ack-disruptive is required; reserve the board and plan the reboot boundary" >&2
    exit 2
fi
shift

if [ "$#" -lt 4 ] || [ "$#" -gt 6 ]; then
    echo "usage: $0 --ack-disruptive <artifact-dir> <iface> <ssid> <gateway> [interval-sec] [max-scans]" >&2
    exit 2
fi
ART="$1"
IFACE="$2"
SSID="$3"
GW="$4"
INTERVAL="${5:-10}"
MAX_SCANS="${6:-30}"
FAIL_DELTA="${FAIL_DELTA:-1000}"
FREQS=(5180 5200 5220 5240)
SCAN_CMD=(iw "$IFACE" scan freq "${FREQS[@]}" ssid "$SSID")
WPA_UNIT="wpa_supplicant@${IFACE}.service"
CONF=/usr/local/etc/wifi_init_conf.json
WPA_CONF="/etc/wpa_supplicant/wpa_supplicant-${IFACE}.conf"

validate_new_artifact_dir "$ART"
[[ "$IFACE" =~ ^[-A-Za-z0-9_.:]+$ ]] \
    || input_error "unsupported interface name: $IFACE"
[ -n "$SSID" ] || input_error "SSID must not be empty"
[[ "$GW" =~ ^[-A-Za-z0-9_.:]+$ ]] \
    || input_error "unsupported gateway: $GW"
for limit in "$INTERVAL" "$MAX_SCANS" "$FAIL_DELTA"; do
    [[ "$limit" =~ ^[1-9][0-9]*$ ]] \
        || input_error "limits must be positive decimal integers"
done

finish_marker() {
    local rc=$?
    printf 'harness_exit=%s\nfinished_at=%s\n' "$rc" "$(date --iso-8601=seconds)" > "$ART/harness-exit.txt"
    touch "$ART/DONE"
    exit "$rc"
}

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run as root" >&2
    exit 2
fi
if [ ! -d "/sys/class/net/$IFACE" ]; then
    echo "ERROR: interface $IFACE missing" >&2
    exit 3
fi

umask 077
mkdir -m 0700 -- "$ART"
mkdir -m 0700 -- "$ART/scans"
cp -- "$0" "$ART/reproducer.sh"
exec > >(tee -a "$ART/harness.log") 2>&1
trap finish_marker EXIT INT TERM HUP

BOOT_ID="$(cat /proc/sys/kernel/random/boot_id)"
START_EPOCH="$(date +%s)"
JSON_HASH="$(sha256sum "$CONF" | awk '{print $1}')"
WPA_HASH="$(sha256sum "$WPA_CONF" | awk '{print $1}')"
WPA_CURSOR="$(journalctl -u "$WPA_UNIT" -n 0 --show-cursor --no-pager | sed -n 's/^-- cursor: //p')"
KERNEL_CURSOR="$(journalctl -k -n 0 --show-cursor --no-pager | sed -n 's/^-- cursor: //p')"

# requesters-only: production services stay up, but this shell is the only scan
# requester. wpa-only: stronger negative-control isolation for cofactor analysis.
ISOLATION_PROFILE="${ISOLATION_PROFILE:-requesters-only}"
case "$ISOLATION_PROFILE" in
    requesters-only)
        ISOLATION_UNITS=(
            "wifi_roam@${IFACE}.service"
            "wifi_bgscan@${IFACE}.service"
            "wifi_capture@${IFACE}.service"
        )
        ;;
    wpa-only)
        ISOLATION_UNITS=(
            "wifi_roam@${IFACE}.service"
            "wifi_bgscan@${IFACE}.service"
            "wifi_capture@${IFACE}.service"
            "wifi_logger_scan@${IFACE}.service"
            "wifi_logger_link@${IFACE}.service"
            "wifi_logger_stat@${IFACE}.service"
            "wifi_link_snapshot@${IFACE}.service"
            "wifi_checker@${IFACE}.service"
            "wifi_event@${IFACE}.service"
            "wifi_bridge@${IFACE}.service"
            "wlan_fw_watch.service"
        )
        ;;
    *)
        echo "ERROR: unsupported ISOLATION_PROFILE=$ISOLATION_PROFILE" >&2
        exit 4
        ;;
esac
{
    echo "boot_id=$BOOT_ID"
    echo "start_epoch=$START_EPOCH"
    echo "json_sha256=$JSON_HASH"
    echo "wpa_conf_sha256=$WPA_HASH"
    printf 'scan_command='; printf '%q ' "${SCAN_CMD[@]}"; echo
    echo "interval_seconds=$INTERVAL"
    echo "max_scans=$MAX_SCANS"
    echo "fail_delta=$FAIL_DELTA"
    echo "isolation_profile=$ISOLATION_PROFILE"
    echo "--- units before isolation ---"
    for u in "${ISOLATION_UNITS[@]}" "$WPA_UNIT"; do
        printf '%s=' "$u"; systemctl is-active "$u" 2>/dev/null || true
    done
} > "$ART/preflight.txt"

for u in "${ISOLATION_UNITS[@]}"; do
    systemctl stop "$u" 2>/dev/null || true
done
sleep 3

if [ "$(systemctl is-active "$WPA_UNIT" 2>/dev/null || true)" != active ]; then
    echo "ERROR: $WPA_UNIT is not active after isolation"
    exit 10
fi
ACTIVE_ISOLATED=0
for u in "${ISOLATION_UNITS[@]}"; do
    state="$(systemctl is-active "$u" 2>/dev/null || true)"
    printf '%s=%s\n' "$u" "$state" >> "$ART/isolated-units.txt"
    if [ "$state" = active ]; then ACTIVE_ISOLATED=$((ACTIVE_ISOLATED + 1)); fi
done
if [ "$ACTIVE_ISOLATED" -ne 0 ]; then
    echo "ERROR: $ACTIVE_ISOLATED isolation units remain active"
    exit 11
fi

status_value() {
    local key="$1"
    wpa_cli -i "$IFACE" status 2>/dev/null | sed -n "s/^${key}=//p" | head -1
}
tx_failed() {
    iw dev "$IFACE" station dump 2>/dev/null | awk '/tx failed:/ {print $3; exit}'
}

INITIAL_STATE="$(status_value wpa_state)"
INITIAL_BSSID="$(status_value bssid)"
INITIAL_SSID="$(status_value ssid)"
INITIAL_FREQ="$(status_value freq)"
INITIAL_TX_FAILED="$(tx_failed)"
INITIAL_TX_FAILED="${INITIAL_TX_FAILED:-0}"

set +e
ping -I "$IFACE" -c 3 -W 2 "$GW" > "$ART/initial-ping.txt" 2>&1
INITIAL_PING_RC=$?
set -e
if [ "$INITIAL_STATE" != COMPLETED ] || [ "$INITIAL_PING_RC" -ne 0 ]; then
    echo "ERROR: unhealthy baseline state=$INITIAL_STATE ping_rc=$INITIAL_PING_RC"
    exit 12
fi

{
    echo "initial_state=$INITIAL_STATE"
    echo "initial_bssid=$INITIAL_BSSID"
    echo "initial_ssid=$INITIAL_SSID"
    echo "initial_freq=$INITIAL_FREQ"
    echo "initial_tx_failed=$INITIAL_TX_FAILED"
    echo "initial_ping_rc=$INITIAL_PING_RC"
    echo "--- status ---"
    wpa_cli -i "$IFACE" status
    echo "--- station ---"
    iw dev "$IFACE" station dump
    echo "--- addresses ---"
    ip address show dev "$IFACE"
    echo "--- routes ---"
    ip route show dev "$IFACE"
    echo "--- neighbors ---"
    ip neigh show dev "$IFACE"
} > "$ART/baseline.txt"

{
    uname -a
    echo "--- module identity ---"
    for m in moal mlan; do
        echo "[$m]"
        modinfo "$m" 2>/dev/null | grep -E '^(filename|version|srcversion|vermagic):' || true
        path="$(modinfo -n "$m" 2>/dev/null || true)"
        if [ -f "$path" ]; then
            sha256sum "$path"
        fi
    done
    echo "--- interface driver ---"
    readlink -f "/sys/class/net/$IFACE/device/driver" || true
    readlink -f "/sys/class/net/$IFACE/device" || true
    ethtool -i "$IFACE" 2>/dev/null || true
    echo "--- loaded modules ---"
    lsmod | grep -E '^(moal|mlan|cfg80211|rfkill)' || true
    echo "--- firmware candidates ---"
    find /lib/firmware -maxdepth 3 -type f \( -iname '*9098*' -o -iname '*nxp*' -o -iname '*wlan*' \) -print 2>/dev/null | sort | head -200
    echo "--- firmware/config requested by this boot ---"
    dmesg | grep -E 'fw_name=|Request firmware:|WLAN FW is active|wlan: version =' || true
    while IFS= read -r rel; do
        file="/lib/firmware/$rel"
        if [ -f "$file" ]; then
            sha256sum "$file"
        fi
    done < <(
        dmesg | sed -n -E \
            -e 's/.*fw_name=([^[:space:]]+).*/\1/p' \
            -e 's/.*Request firmware: ([^[:space:]]+).*/\1/p' | sort -u
    )
    for file in /lib/firmware/cts/wifi_mod_para.conf \
                /lib/firmware/cts/config/wifi_mod_para.conf \
                /lib/firmware/nxp/wifi_mod_para.conf; do
        if [ -f "$file" ]; then
            sha256sum "$file"
        fi
    done
} > "$ART/driver-environment.txt" 2>&1

SCAN_COUNT=0
STOP_REASON=max_scans
ONSET_SCAN_COUNT=""
ONSET_TX_FAILED=""
ONSET_ELAPSED=""

for i in $(seq 1 "$MAX_SCANS"); do
    SCAN_COUNT="$i"
    SCAN_START_MS="$(date +%s%3N)"
    SCAN_START_ISO="$(date --iso-8601=seconds)"
    set +e
    timeout 30 "${SCAN_CMD[@]}" > "$ART/scans/scan-$(printf '%03d' "$i").txt" \
        2> "$ART/scans/scan-$(printf '%03d' "$i").stderr"
    SCAN_RC=$?
    set -e
    SCAN_END_MS="$(date +%s%3N)"
    if [ "$SCAN_RC" -ne 0 ]; then
        STOP_REASON=scan_command_failure
        printf '%s scan=%d rc=%d elapsed_ms=%d\n' "$SCAN_START_ISO" "$i" "$SCAN_RC" "$((SCAN_END_MS-SCAN_START_MS))" | tee -a "$ART/progress.log"
        break
    fi

    # No WLAN L3 traffic is generated here. Wait, then read local driver counters only.
    sleep "$INTERVAL"
    TX="$(tx_failed)"
    TX="${TX:-$INITIAL_TX_FAILED}"
    DELTA=$((TX - INITIAL_TX_FAILED))
    ELAPSED=$(( $(date +%s) - START_EPOCH ))
    STATE="$(status_value wpa_state)"
    BSSID="$(status_value bssid)"
    printf '%s scan=%d rc=0 scan_ms=%d elapsed_s=%d tx_failed=%d delta=%d state=%s bssid=%s\n' \
        "$SCAN_START_ISO" "$i" "$((SCAN_END_MS-SCAN_START_MS))" "$ELAPSED" \
        "$TX" "$DELTA" "$STATE" "$BSSID" | tee -a "$ART/progress.log"

    if [ "$DELTA" -ge "$FAIL_DELTA" ]; then
        STOP_REASON=tx_failed_spike
        ONSET_SCAN_COUNT="$i"
        ONSET_TX_FAILED="$TX"
        ONSET_ELAPSED="$ELAPSED"
        break
    fi
done

PRE_PING_TX_FAILED="$(tx_failed)"
PRE_PING_TX_FAILED="${PRE_PING_TX_FAILED:-0}"
FINAL_STATE="$(status_value wpa_state)"
FINAL_BSSID="$(status_value bssid)"
FINAL_SSID="$(status_value ssid)"
FINAL_FREQ="$(status_value freq)"
ip neigh show dev "$IFACE" > "$ART/pre-final-ping-neighbor.txt" 2>&1 || true

set +e
ping -I "$IFACE" -c 3 -W 2 "$GW" > "$ART/final-ping.txt" 2>&1
FINAL_PING_RC=$?
set -e
POST_PING_TX_FAILED="$(tx_failed)"
POST_PING_TX_FAILED="${POST_PING_TX_FAILED:-0}"

journalctl -u "$WPA_UNIT" --after-cursor "$WPA_CURSOR" -o short-iso --no-pager > "$ART/wpa-journal.log" 2>&1 || true
journalctl -k --after-cursor "$KERNEL_CURSOR" -o short-iso --no-pager > "$ART/kernel-journal.log" 2>&1 || true
EXTERNAL_COUNT="$(grep -F -c 'External program started a scan' "$ART/wpa-journal.log" 2>/dev/null || true)"
EXT_RESULT_COUNT="$(grep -F -c 'own=0 ext=1' "$ART/wpa-journal.log" 2>/dev/null || true)"
OWN_SCAN_COUNT="$(grep -F -c 'Own scan request started a scan' "$ART/wpa-journal.log" 2>/dev/null || true)"
DISCONNECT_COUNT="$(grep -E -c 'CTRL-EVENT-DISCONNECTED|Authentication timed out|SSID-TEMP-DISABLED' "$ART/wpa-journal.log" 2>/dev/null || true)"
END_EPOCH="$(date +%s)"
RESULT=NOT_REPRODUCED
if [ "$FINAL_PING_RC" -ne 0 ] && [ $((PRE_PING_TX_FAILED - INITIAL_TX_FAILED)) -ge "$FAIL_DELTA" ] && \
   [ "$FINAL_STATE" = COMPLETED ] && [ "$FINAL_BSSID" = "$INITIAL_BSSID" ]; then
    RESULT=REPRODUCED
fi

{
    echo "result=$RESULT"
    echo "boot_id=$BOOT_ID"
    echo "start_epoch=$START_EPOCH"
    echo "end_epoch=$END_EPOCH"
    echo "elapsed_seconds=$((END_EPOCH-START_EPOCH))"
    echo "stop_reason=$STOP_REASON"
    echo "scan_command_count=$SCAN_COUNT"
    echo "failure_onset_scan_count=$ONSET_SCAN_COUNT"
    echo "failure_onset_elapsed=$ONSET_ELAPSED"
    echo "initial_tx_failed=$INITIAL_TX_FAILED"
    echo "failure_onset_tx_failed=$ONSET_TX_FAILED"
    echo "pre_ping_tx_failed=$PRE_PING_TX_FAILED"
    echo "post_ping_tx_failed=$POST_PING_TX_FAILED"
    echo "initial_ping_rc=$INITIAL_PING_RC"
    echo "final_ping_rc=$FINAL_PING_RC"
    echo "initial_state=$INITIAL_STATE"
    echo "final_state=$FINAL_STATE"
    echo "initial_bssid=$INITIAL_BSSID"
    echo "final_bssid=$FINAL_BSSID"
    echo "initial_ssid=$INITIAL_SSID"
    echo "final_ssid=$FINAL_SSID"
    echo "initial_freq=$INITIAL_FREQ"
    echo "final_freq=$FINAL_FREQ"
    echo "wpa_external_scan_count=$EXTERNAL_COUNT"
    echo "wpa_ext_result_count=$EXT_RESULT_COUNT"
    echo "wpa_own_scan_count=$OWN_SCAN_COUNT"
    echo "disconnect_auth_error_count=$DISCONNECT_COUNT"
    echo "isolation_profile=$ISOLATION_PROFILE"
    echo "json_sha256_before=$JSON_HASH"
    echo "json_sha256_after=$(sha256sum "$CONF" | awk '{print $1}')"
    echo "wpa_conf_sha256_before=$WPA_HASH"
    echo "wpa_conf_sha256_after=$(sha256sum "$WPA_CONF" | awk '{print $1}')"
    echo "--- final status ---"
    wpa_cli -i "$IFACE" status
    echo "--- final station ---"
    iw dev "$IFACE" station dump
    echo "--- final neighbors ---"
    ip neigh show dev "$IFACE"
    echo "--- isolation units ---"
    cat "$ART/isolated-units.txt"
} > "$ART/result.txt"

# harness.log remains open until the stdout tee exits, while harness-exit.txt
# and DONE are written by the EXIT trap.  Keep those live envelope files out
# of this stable payload manifest; the caller's post-exit archive manifest
# covers the complete artifact directory.
MANIFEST_TMP="${ART%/*}/.${ART##*/}.SHA256SUMS.$$"
if ! (
    cd "$ART"
    find . -type f \
        ! -name harness.log \
        ! -name harness-exit.txt \
        ! -name DONE \
        ! -name SHA256SUMS \
        -print0 | LC_ALL=C sort -z | xargs -0 sha256sum > "$MANIFEST_TMP"
); then
    rm -f -- "$MANIFEST_TMP"
    exit 1
fi
mv -- "$MANIFEST_TMP" "$ART/SHA256SUMS"
echo "RESULT=$RESULT artifact=$ART"
# Services intentionally remain isolated; reboot is the required cleanup boundary.
