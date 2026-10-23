#!/bin/bash
# Manual single-profile connect rollback. Runtime state contains credentials and
# deliberately lives under /run: a factory reset/reboot must not revive old APs.

wifi_connect_fallback_load() {
    local iface="$1" values json="${WIFI_INIT_CONF_JSON:-/usr/local/etc/wifi_init_conf.json}"
    CONNECT_FALLBACK_ENABLED=0
    CONNECT_ATTEMPT_TIMEOUT=15
    CONNECT_ROLLBACK_TIMEOUT=5
    CONNECT_FALLBACK_ARMED=0
    CONNECT_ROLLBACK_SOURCE=""
    CONNECT_LAST_GOOD="${WIFI_RUN_DIR:-/run/wifi}/${iface}.last-good.json"
    [ "${WIFI_CONNECT_ORIGIN:-manual}" = manual ] || return 0
    [ -f "$json" ] || return 0
    command -v jq >/dev/null 2>&1 || return 0
    values=$(jq -er --arg iface "$iface" '
        def option($key; $default): if has($key) then .[$key] else $default end;
        (.[$iface] // {}) |
        if has("connect_fallback") then .connect_fallback else {} end |
        if type != "object" then error("connect_fallback must be an object") else . end |
        [option("enabled"; false), option("attempt_timeout_sec"; 15), option("rollback_timeout_sec"; 5)] |
        if (.[0] | type) != "boolean" or (.[1] | type) != "number" or (.[2] | type) != "number"
        then error("invalid connect_fallback types")
        elif .[1] != (.[1] | floor) or .[1] < 1 or .[1] > 15
          or .[2] != (.[2] | floor) or .[2] < 1 or .[2] > 5
        then error("invalid connect_fallback timeout")
        else @tsv end
    ' "$json" 2>/dev/null) || {
        echo "Error: invalid ${iface}.connect_fallback configuration" >&2
        return 1
    }
    local enabled
    read -r enabled CONNECT_ATTEMPT_TIMEOUT CONNECT_ROLLBACK_TIMEOUT <<< "$values"
    [ "$enabled" != true ] || CONNECT_FALLBACK_ENABLED=1
}

# Capture only a profile whose identity/frequency policy is currently loaded
# and associated. Staged ssid/freq/credentials can never replace a verified copy.
wifi_connect_fallback_capture() {
    local iface="$1" conf="$2" status id="" state="" ssid="" freq="" key value
    local conf_ssid live_ssid expected_ssid freqs global_freq network_freq tmp marker_args=()
    local final_status final_id="" final_state="" final_ssid="" final_freq=""
    CONNECT_PROFILE_CAPTURED=0
    [ "${CONNECT_FALLBACK_ENABLED:-0}" = 1 ] || return 0
    [ -f "$conf" ] && [ ! -e "$WIFI_RUN_DIR/${iface}.credential-pending" ] || return 0
    [ "$(wifi_wpa_child_call wifi_wpa_conf_network_count "$conf")" = 1 ] || return 0
    status=$(wifi_wpa_child_exec timeout -k 1 1 wpa_cli -i "$iface" status 2>/dev/null) || return 0
    while IFS='=' read -r key value; do
        case "$key" in
            id) id="$value" ;; wpa_state) state="$value" ;;
            ssid) ssid="$value" ;; freq) freq="$value" ;;
        esac
    done <<< "$status"
    [ "$state" = COMPLETED ] && [[ "$id" =~ ^[0-9]+$ && "$freq" =~ ^[0-9]+$ ]] || return 0
    conf_ssid=$(wifi_wpa_run_child awk '
        /^[[:space:]]*#/ { next }
        /^[[:space:]]*ssid[[:space:]]*=/ {
            sub(/^[[:space:]]*ssid[[:space:]]*=[[:space:]]*/, ""); sub(/[[:space:]]+$/, ""); print; exit
        }' "$conf") || return 1
    live_ssid=$(wifi_wpa_child_exec timeout -k 1 1 wpa_cli -i "$iface" get_network "$id" ssid 2>/dev/null) || return 0
    expected_ssid=$(wifi_wpa_child_call wifi_ssid_conf_to_wpa_text "$conf_ssid") || return 0
    live_ssid=$(wifi_wpa_child_call wifi_ssid_conf_to_wpa_text "$live_ssid") || return 0
    [ "$expected_ssid" = "$live_ssid" ] && [ "$ssid" = "$expected_ssid" ] || return 0
    freqs=$(wifi_wpa_child_call wifi_wpa_conf_common_freqs "$conf") || return 1
    global_freq=$(wifi_wpa_child_exec timeout -k 1 1 wpa_cli -i "$iface" get freq_list 2>/dev/null) || return 0
    network_freq=$(wifi_wpa_child_exec timeout -k 1 1 wpa_cli -i "$iface" get_network "$id" freq_list 2>/dev/null) || return 0
    [ "$global_freq" != FAIL ] || global_freq=""
    [ "$network_freq" != FAIL ] || network_freq=""
    [ "$freqs" = "$global_freq" ] && [ "$freqs" = "$network_freq" ] || return 0
    if [ -n "$freqs" ]; then
        case " $freqs " in *" $freq "*) ;; *) return 0 ;; esac
    fi
    final_status=$(wifi_wpa_child_exec timeout -k 1 1 wpa_cli -i "$iface" status 2>/dev/null) || return 0
    while IFS='=' read -r key value; do
        case "$key" in
            id) final_id="$value" ;; wpa_state) final_state="$value" ;;
            ssid) final_ssid="$value" ;; freq) final_freq="$value" ;;
        esac
    done <<< "$final_status"
    [ "$final_state" = COMPLETED ] && [ "$final_id" = "$id" ] \
        && [ "$final_ssid" = "$ssid" ] && [ "$final_freq" = "$freq" ] || return 0
    wifi_wpa_run_child mkdir -p "$WIFI_RUN_DIR" || return 1
    tmp=$(wifi_wpa_child_exec mktemp "$CONNECT_LAST_GOOD.tmp.XXXXXX") || return 1
    if [ "$iface" = mlan0 ] && [ -f "$OPC_ESSID_MARKER" ]; then
        marker_args=(--rawfile marker "$OPC_ESSID_MARKER")
    else
        marker_args=(--argjson marker null)
    fi
    if ! wifi_wpa_run_child jq -n --arg iface "$iface" --arg ssid "$ssid" --arg freqs "$freqs" \
        --rawfile profile "$conf" "${marker_args[@]}" \
        '{version:1, iface:$iface, ssid:$ssid, freqs:$freqs, profile:$profile, marker:$marker}' > "$tmp" \
       || ! wifi_wpa_run_child chmod 0600 "$tmp" \
       || ! wifi_wpa_run_child_call sync_path_or_global "$tmp" \
       || ! wifi_wpa_run_child mv -f "$tmp" "$CONNECT_LAST_GOOD"; then
        wifi_wpa_run_child rm -f "$tmp"
        echo "Error: failed to save verified profile for $iface" >&2
        return 1
    fi
    wifi_wpa_run_child_call sync_path_or_global "$WIFI_RUN_DIR" || return 1
    CONNECT_PROFILE_CAPTURED=1
}

wifi_connect_fallback_start() {
    [ "${CONNECT_FALLBACK_ENABLED:-0}" = 1 ] || return 0
    CONNECT_OPERATION_DEADLINE_MS=$(($(wifi_wpa_monotonic_ms) + 25000))
    WIFI_WPA_DEADLINE_MS="$CONNECT_OPERATION_DEADLINE_MS"
}

wifi_connect_fallback_attempt_start() {
    [ "${CONNECT_FALLBACK_ENABLED:-0}" = 1 ] || return 0
    local latest=$((CONNECT_OPERATION_DEADLINE_MS - CONNECT_ROLLBACK_TIMEOUT * 1000))
    WIFI_WPA_DEADLINE_MS=$(($(wifi_wpa_monotonic_ms) + CONNECT_ATTEMPT_TIMEOUT * 1000))
    [ "$WIFI_WPA_DEADLINE_MS" -le "$latest" ] || WIFI_WPA_DEADLINE_MS="$latest"
}

wifi_connect_fallback_arm() {
    [ "${CONNECT_FALLBACK_ENABLED:-0}" = 1 ] || return 0
    # Publication may replace last-good before its directory sync fails. Keep
    # the pre-attempt bytes independent until the target commit is complete.
    if [ -f "$CONNECT_LAST_GOOD" ]; then
        CONNECT_ROLLBACK_SOURCE=$(wifi_wpa_child_exec mktemp "$CONNECT_LAST_GOOD.rollback.XXXXXX") || return 1
        if ! wifi_wpa_run_child cp "$CONNECT_LAST_GOOD" "$CONNECT_ROLLBACK_SOURCE" \
            || ! wifi_wpa_run_child chmod 0600 "$CONNECT_ROLLBACK_SOURCE"; then
            echo "Error: could not preserve previous checkpoint for $IFACE" >&2
            return 1
        fi
    fi
    CONNECT_FALLBACK_ARMED=1
}

wifi_connect_fallback_time_left() {
    [ -z "${WIFI_WPA_DEADLINE_MS:-}" ] \
        || [ "$(wifi_wpa_monotonic_ms)" -lt "$WIFI_WPA_DEADLINE_MS" ]
}

wifi_connect_fallback_log() {
    local outcome="$1"
    case "$outcome" in
        restored) echo "fallback restored: previous association verified ($IFACE)" >&2 ;;
        restore-failed) echo "fallback failed: previous association not restored ($IFACE)" >&2 ;;
        fallback-unavailable) echo "fallback unavailable: no verified previous profile ($IFACE)" >&2 ;;
    esac
    wifi_wpa_run_child logger -p local0.warning \
        "[$tag:$LINENO] connect_fallback iface=$IFACE outcome=$outcome" 2>/dev/null || true
}

wifi_connect_fallback_restore() {
    local tmp marker_tmp ssid freqs polls snapshot="$CONNECT_ROLLBACK_SOURCE"
    if [ ! -f "$snapshot" ] || ! wifi_wpa_run_child jq -e --arg iface "$IFACE" '
        .version == 1 and .iface == $iface and
        (.profile | type == "string" and length > 0) and
        (.ssid | type == "string" and length > 0) and (.freqs | type == "string") and
        (.marker == null or (.marker | type == "string"))
    ' "$snapshot" >/dev/null 2>&1; then
        return 2
    fi
    WIFI_WPA_DEADLINE_MS=$(($(wifi_wpa_monotonic_ms) + CONNECT_ROLLBACK_TIMEOUT * 1000))
    if [ "$WIFI_WPA_DEADLINE_MS" -gt "$CONNECT_OPERATION_DEADLINE_MS" ]; then
        WIFI_WPA_DEADLINE_MS="$CONNECT_OPERATION_DEADLINE_MS"
    fi
    tmp=$(wifi_wpa_child_exec mktemp "$CONF.fallback.XXXXXX") || return 1
    if ! wifi_wpa_run_child jq -j '.profile' "$snapshot" > "$tmp" \
        || ! wifi_wpa_run_child_call safe_install_sync "$tmp" "$CONF"; then
        wifi_wpa_run_child rm -f "$tmp"
        return 1
    fi
    wifi_wpa_run_child rm -f "$tmp"
    if [ "$IFACE" = mlan0 ]; then
        if wifi_wpa_run_child jq -e '.marker != null' "$snapshot" >/dev/null; then
            marker_tmp=$(wifi_wpa_child_exec mktemp "$OPC_ESSID_MARKER.fallback.XXXXXX") || return 1
            if ! wifi_wpa_run_child jq -j '.marker' "$snapshot" > "$marker_tmp" \
                || ! wifi_wpa_run_child_call safe_install_sync "$marker_tmp" "$OPC_ESSID_MARKER" 0600; then
                wifi_wpa_run_child rm -f "$marker_tmp"
                return 1
            fi
            wifi_wpa_run_child rm -f "$marker_tmp"
        else
            wifi_wpa_run_child rm -f "$OPC_ESSID_MARKER" || return 1
        fi
    fi
    ssid=$(wifi_wpa_child_exec jq -r '.ssid' "$snapshot") || return 1
    freqs=$(wifi_wpa_child_exec jq -r '.freqs' "$snapshot") || return 1
    HAS_TARGET=1; HAS_TARGET_ID=0; TARGET_SSID_WPA_TEXT="$ssid"
    FREQ_STR="$freqs"; SET_FREQ=0
    [ -z "$freqs" ] || SET_FREQ=1
    wifi_wpa_run_child rm -f "$WIFI_RUN_DIR/${IFACE}.credential-pending" || return 1
    : > "$WIFI_RUN_DIR/${IFACE}.reconfigure-grace" 2>/dev/null || true
    connect_event_monitor_arm && wpa_cli_ok "$IFACE" reconfigure || return 1
    # Give the reload a brief chance to land before requesting reassociation.
    for ((polls = 0; polls < 3; polls++)); do
        wifi_connect_fallback_time_left || return 1
        if connect_association_poll_matches; then return 0; fi
        wifi_wpa_run_child sleep 0.1
    done
    wpa_cli_ok "$IFACE" reassociate || wpa_cli_ok "$IFACE" reconnect || return 1
    while wifi_connect_fallback_time_left; do
        if connect_association_poll_matches; then return 0; fi
        wifi_wpa_run_child sleep 0.1
    done
    return 1
}

wifi_connect_fallback_commit() {
    [ "${CONNECT_FALLBACK_ENABLED:-0}" = 1 ] || return 0
    if [ "${CONNECT_EXPLICIT_SSID:-0}" = 1 ] && [ "$IFACE" = mlan0 ]; then
        wifi_wpa_run_child rm -f "$OPC_ESSID_MARKER" || return 1
    fi
    wifi_connect_fallback_capture "$IFACE" "$CONF" || return 1
    [ "$CONNECT_PROFILE_CAPTURED" = 1 ] || {
        echo "Error: could not verify profile for fallback checkpoint" >&2
        return 1
    }
    CONNECT_FALLBACK_ARMED=0
}

wifi_connect_cleanup() {
    local rc=$?
    trap - EXIT HUP INT TERM
    if [ "${CONNECT_FALLBACK_ARMED:-0}" = 1 ] && [ "$rc" -ne 0 ]; then
        if wifi_connect_fallback_restore; then
            wifi_connect_fallback_log restored
        else
            case "$?" in
                2) wifi_connect_fallback_log fallback-unavailable ;;
                *) wifi_connect_fallback_log restore-failed ;;
            esac
        fi
        if [ -n "$CONNECT_ROLLBACK_SOURCE" ]; then
            if ! wifi_wpa_run_child mv -f "$CONNECT_ROLLBACK_SOURCE" "$CONNECT_LAST_GOOD"; then
                # Retain the private recovery copy if cache publication itself
                # fails; deleting it could discard the sole previous profile.
                echo "Error: could not restore previous checkpoint for $IFACE" >&2
            fi
            CONNECT_ROLLBACK_SOURCE=""
        fi
    fi
    WIFI_WPA_DEADLINE_MS=""
    local leftovers=()
    [ -z "${CONNECT_ROLLBACK_SOURCE:-}" ] || leftovers+=("$CONNECT_ROLLBACK_SOURCE")
    [ -z "${CANON_FILE:-}" ] || leftovers+=("$CANON_FILE")
    [ -z "${TMP_FILE:-}" ] || leftovers+=("$TMP_FILE")
    [ "${#leftovers[@]}" -eq 0 ] || wifi_wpa_run_child rm -f "${leftovers[@]}"
    connect_event_monitor_cleanup
    wifi_connect_sync_persistent
    exit "$rc"
}

# Make this connect's persistent changes durable before the reply without a
# system-wide sync. They are all in the CONF and OPC marker directories:
# installs there sync their own file (safe_install_sync), while deletions there
# (the marker, fallback temp files) become durable with the directory. Runtime
# state is under /run and temp files are under /tmp.
wifi_connect_sync_persistent() {
    local dirs=() dir
    for dir in "${CONF%/*}" "${OPC_ESSID_MARKER%/*}"; do
        [ -d "$dir" ] && dirs+=("$dir")
    done
    if [ "${#dirs[@]}" -gt 0 ] && wifi_wpa_run_child sync "${dirs[@]}" 2>/dev/null; then
        return 0
    fi
    wifi_wpa_run_child sync 2>/dev/null || true
}
