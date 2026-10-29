#!/bin/bash
# Shared fast runtime SSID switch for
# wifi <iface> connectraw [--save|--no-save] [--] <SSID...>
# and the FTP wconnectraw adapter.
#
# The radio commands stay on the short path. In the default save mode, their
# exact OK replies publish a request for an independent worker to confirm the
# target association and save only its SSID. SUCCESS means the radio commands
# and any requested persistence handoff were submitted, not that association
# or save has finished.
set -u
tag=${0##*/}

WPA_CLI=/usr/sbin/wpa_cli
WPA_CONF_DIR=${WPA_CONF_DIR:-/etc/wpa_supplicant}
RAW_COMMIT_HELPER=${WIFI_RAW_COMMIT_HELPER:-/usr/local/scripts/wifi_raw_commit.py}
RAW_ORIGIN=${WIFI_CONNECT_RAW_ORIGIN:-cli}
case "$RAW_ORIGIN" in
    ftp) RAW_COMMAND=wconnectraw ;;
    *) RAW_ORIGIN=cli; RAW_COMMAND=connectraw ;;
esac
LC_ALL=C
export LC_ALL

monotonic_ms() {
    IFS=' .' read -r _sec _centi _rest < /proc/uptime || return 1
    case "$_sec:$_centi" in *[!0-9:]*|:*) return 1 ;; esac
    _centi=${_centi#0}
    [ -n "$_centi" ] || _centi=0
    printf '%s\n' "$((_sec * 1000 + _centi * 10))"
}

START_MS=$(monotonic_ms 2>/dev/null || true)
TRACE_ID="${START_MS:-0}-$$"
if [[ "${WIFI_RAW_TRACE_START_MS:-}" =~ ^(0|[1-9][0-9]{0,13})$ \
      && "${WIFI_RAW_TRACE_ID:-}" =~ ^${WIFI_RAW_TRACE_START_MS:-none}-[0-9]{1,10}$ \
      && "${WIFI_RAW_TRACE_START_MS:-0}" -le "${START_MS:-0}" ]]; then
    START_MS=$WIFI_RAW_TRACE_START_MS
    TRACE_ID=$WIFI_RAW_TRACE_ID
fi

IFACE=unknown
raw_log_failure() {
    _code=$1
    shift
    logger -p local0.err \
        "[$tag:$LINENO] ftpcmd_raw_result trace=$TRACE_ID command=$RAW_COMMAND status=fail code=$_code iface=${IFACE:-unknown} detail=$*" \
        >/dev/null 2>&1 || true
}

raw_reply() {
    _rc=$?
    trap - EXIT
    if [ "$_rc" -eq 0 ]; then
        printf 'SUCCESS\n'
    else
        printf 'FAIL code=%s\n' "$_rc"
    fi
    exit "$_rc"
}
trap raw_reply EXIT

case "${1:-}" in
    mlan0|0) IFACE=mlan0 ;;
    mlan1|1) IFACE=mlan1 ;;
    *)
        raw_log_failure 2 "expected interface mlan0, mlan1, 0 or 1"
        exit 2
        ;;
esac
shift

SAVE_PROFILE=1
SAVE_POLICY=save
SAVE_POLICY_SEEN=0
while [ "$#" -gt 0 ]; do
    case "$1" in
        --save|--no-save)
            if [ "$SAVE_POLICY_SEEN" -ne 0 ]; then
                raw_log_failure 2 "duplicate or conflicting save policy"
                exit 2
            fi
            SAVE_POLICY_SEEN=1
            if [ "$1" = --no-save ]; then
                SAVE_PROFILE=0
                SAVE_POLICY=no-save
            fi
            shift
            ;;
        --)
            shift
            break
            ;;
        --*)
            raw_log_failure 2 "unknown option $1"
            exit 2
            ;;
        *)
            break
            ;;
    esac
done

if [ "$#" -eq 0 ]; then
    raw_log_failure 2 "missing ssid"
    exit 2
fi
SSID=$*
case "$SSID" in
    ''|*[!A-Za-z0-9_.:,=+@/\ -]*)
        raw_log_failure 2 "invalid SSID characters"
        exit 2
        ;;
esac
if [ "${#SSID}" -gt 32 ]; then
    raw_log_failure 2 "SSID exceeds 32 bytes"
    exit 2
fi
PARSED_MS=$(monotonic_ms 2>/dev/null || printf '%s\n' "${START_MS:-0}")
logger -p local0.info \
    "[$tag:$LINENO] ftpcmd_raw_timing trace=$TRACE_ID phase=dispatch_received elapsed_ms=$((PARSED_MS - ${START_MS:-0})) iface=$IFACE command=$RAW_COMMAND origin=$RAW_ORIGIN save_policy=$SAVE_POLICY ssid=\"$SSID\"" \
    >/dev/null 2>&1 || true

# Hold the change gate exclusively across the live mutation and any persistence
# handoff. Other writers cannot alter the profile before a commit is queued.
CHANGE_RUN_DIR=${WIFI_RUN_DIR:-/run/wifi}
mkdir -p "$CHANGE_RUN_DIR" || exit 1
exec 6>"$CHANGE_RUN_DIR/${IFACE}.change.lock" || exit 1
if ! flock -n -x 6; then
    raw_log_failure 6 "configuration/connection change busy for $IFACE"
    echo "configuration/connection change busy for $IFACE" >&2
    exit 6
fi
if [ -e "$CHANGE_RUN_DIR/${IFACE}.credential-pending" ] \
   || [ -L "$CHANGE_RUN_DIR/${IFACE}.credential-pending" ]; then
    raw_log_failure 6 "credential edit pending for $IFACE"
    echo "credential edit pending for $IFACE" >&2
    exit 6
fi

RAW_COMMIT_REQUEST="$CHANGE_RUN_DIR/${IFACE}.raw-commit"
if [ "$SAVE_PROFILE" -eq 1 ]; then
    CONF="$WPA_CONF_DIR/wpa_supplicant-$IFACE.conf"
    if [ ! -f "$CONF" ] || [ -L "$CONF" ] || [ ! -x "$RAW_COMMIT_HELPER" ]; then
        raw_log_failure 1 "profile or persistence worker unavailable"
        exit 1
    fi
    CONF_SHA=$(sha256sum "$CONF" 2>/dev/null)
    CONF_SHA=${CONF_SHA%% *}
    if [ "${#CONF_SHA}" -ne 64 ]; then
        raw_log_failure 1 "profile digest unavailable"
        exit 1
    fi
fi

STATUS=$($WPA_CLI -i "$IFACE" status 2>/dev/null)
STATUS_RC=$?
if [ "$STATUS_RC" -ne 0 ]; then
    raw_log_failure 1 "status command failed rc=$STATUS_RC"
    exit 1
fi

NETWORK_ID=""
WPA_STATE=""
ORIGINAL_SSID=""
ORIGINAL_BSSID="-"
while IFS='=' read -r _key _value; do
    case "$_key" in
        id) NETWORK_ID=$_value ;;
        wpa_state) WPA_STATE=$_value ;;
        ssid) ORIGINAL_SSID=$_value ;;
        bssid) ORIGINAL_BSSID=$_value ;;
    esac
done <<EOF
$STATUS
EOF
if [ "$WPA_STATE" = "INTERFACE_DISABLED" ]; then
    # wpa_supplicant refuses reassociate here, but disconnect would still keep it
    # down after the interface comes back up, so change nothing.
    raw_log_failure 1 "interface disabled (wpa_state=INTERFACE_DISABLED)"
    exit 1
fi
REQUIRE_NEW_BSSID=0
if [ "$ORIGINAL_SSID" != "$SSID" ]; then
    if [ "$ORIGINAL_BSSID" != - ]; then
        REQUIRE_NEW_BSSID=1
    elif [ "$WPA_STATE" = COMPLETED ]; then
        raw_log_failure 1 "connected status has no original BSSID"
        exit 1
    fi
fi
if [ -z "$NETWORK_ID" ]; then
    # Disconnected status has no id. Use the selected network if one is marked
    # CURRENT, or the only enabled network when there is no current selection.
    NETWORKS=$($WPA_CLI -i "$IFACE" list_networks 2>/dev/null)
    LIST_RC=$?
    if [ "$LIST_RC" -eq 0 ]; then
        NETWORK_ID=$(printf '%s\n' "$NETWORKS" | awk -F '\t' '
            $1 ~ /^[0-9]+$/ {
                if ($4 ~ /\[CURRENT\]/) { current = $1; current_count++ }
                if ($4 !~ /\[DISABLED\]/) { enabled = $1; enabled_count++ }
            }
            END {
                if (current_count == 1) print current
                else if (current_count == 0 && enabled_count == 1) print enabled
            }
        ')
    fi
    case "$NETWORK_ID" in
        ''|*[!0-9]*)
            raw_log_failure 1 "cannot select one configured network while disconnected"
            exit 1
            ;;
    esac
fi

STATUS_DONE_MS=$(monotonic_ms 2>/dev/null || printf '%s\n' "$PARSED_MS")

# Suppress the checker while this explicit switch is being verified (15s).
# Its normal 10s disconnected recovery would hold change.lock and reject a
# manual retry with code 6. Reuse the existing bounded reconfigure grace.
if ! : > "$CHANGE_RUN_DIR/${IFACE}.reconfigure-grace"; then
    raw_log_failure 1 "cannot publish reconnect grace for $IFACE"
    exit 1
fi

# Keep an older save request alive until every read-only preflight passes. Once
# this runtime-only switch is ready to mutate supplicant state, remove it while
# still holding change.lock so its worker observes that it was superseded.
if [ "$SAVE_PROFILE" -eq 0 ] \
   && { [ -e "$RAW_COMMIT_REQUEST" ] || [ -L "$RAW_COMMIT_REQUEST" ]; } \
   && ! rm -f -- "$RAW_COMMIT_REQUEST"; then
    raw_log_failure 1 "cannot cancel pending persistence request"
    exit 1
fi

# The FTP-safe ASCII validation above makes this quoted ctrl value unambiguous.
SSID_VALUE=\"$SSID\"
SET_REPLY=$($WPA_CLI -i "$IFACE" set_network "$NETWORK_ID" ssid "$SSID_VALUE" 2>&1)
SET_RC=$?
SET_DONE_MS=$(monotonic_ms 2>/dev/null || printf '%s\n' "$STATUS_DONE_MS")
if [ "$SET_RC" -ne 0 ] || [ "$SET_REPLY" != "OK" ]; then
    # Nothing has touched the link yet, so leave it as it is.
    raw_log_failure 7 "set_rc=$SET_RC set_reply=${SET_REPLY:-empty}"
    exit 7
fi

# An association to another BSS while the old link is still up is refused with
# EALREADY; wpa_supplicant then puts the target BSSID on its ignore list and
# connects only after another scan. Drop the link first. After disconnect
# wpa_supplicant stays down until a reassociate or reconnect, so reassociate is
# sent even if disconnect failed.
DISC_REPLY=$($WPA_CLI -i "$IFACE" disconnect 2>&1)
DISC_RC=$?
DISC_DONE_MS=$(monotonic_ms 2>/dev/null || printf '%s\n' "$SET_DONE_MS")

REASSOC_REPLY=$($WPA_CLI -i "$IFACE" reassociate 2>&1)
REASSOC_RC=$?
REASSOC_DONE_MS=$(monotonic_ms 2>/dev/null || printf '%s\n' "$DISC_DONE_MS")

case "$START_MS:$PARSED_MS:$STATUS_DONE_MS:$SET_DONE_MS:$DISC_DONE_MS:$REASSOC_DONE_MS" in
    *[!0-9:]*) ;;
    *)
        logger -p local0.info \
            "[$tag:$LINENO] ftpcmd_raw_timing trace=$TRACE_ID phase=commands_submitted elapsed_ms=$((REASSOC_DONE_MS - START_MS)) iface=$IFACE save_policy=$SAVE_POLICY parse_ms=$((PARSED_MS - START_MS)) id_lookup_ms=$((STATUS_DONE_MS - PARSED_MS)) set_network_ms=$((SET_DONE_MS - STATUS_DONE_MS)) disconnect_ms=$((DISC_DONE_MS - SET_DONE_MS)) reassociate_ms=$((REASSOC_DONE_MS - DISC_DONE_MS)) set_rc=$SET_RC disconnect_rc=$DISC_RC reassociate_rc=$REASSOC_RC" \
            >/dev/null 2>&1 || true
        ;;
esac

if [ "$DISC_RC" -eq 0 ] && [ "$DISC_REPLY" = "OK" ] \
   && [ "$REASSOC_RC" -eq 0 ] && [ "$REASSOC_REPLY" = "OK" ]; then
    if [ "$SAVE_PROFILE" -eq 0 ]; then
        exit 0
    fi
    # A path unit outside vsftpd's private PID namespace observes this atomic
    # request. No D-Bus client or child process is needed in the FTP handler.
    REQUEST_TMP=$(mktemp "$CHANGE_RUN_DIR/.${IFACE}.raw-commit.XXXXXX") || {
        raw_log_failure 9 "association requested but request file could not be created"
        exit 9
    }
    if ! printf 'wifi-raw-commit-v1\n%s\n%s\n%s\n%s\n%s\n%s\n' \
        "$NETWORK_ID" "$SSID" "$CONF_SHA" "$TRACE_ID" \
        "$ORIGINAL_BSSID" "$REQUIRE_NEW_BSSID" > "$REQUEST_TMP" \
       || ! mv -fT -- "$REQUEST_TMP" "$RAW_COMMIT_REQUEST"; then
        rm -f -- "$REQUEST_TMP"
        raw_log_failure 9 "association requested but request file could not be published"
        exit 9
    fi
    exit 0
fi

raw_log_failure 7 \
    "disconnect_rc=$DISC_RC disconnect_reply=${DISC_REPLY:-empty} reassociate_rc=$REASSOC_RC reassociate_reply=${REASSOC_REPLY:-empty}"
exit 7
