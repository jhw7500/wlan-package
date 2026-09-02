#!/bin/bash
# Product-path soak for wifi_bgscan@IFACE.service.
#
# This does not invoke iw directly.  It temporarily satisfies the test-only
# ConditionPathExists gate, starts the installed wifi_bgscan service, and
# observes its actual command through journald.  All control/observation is
# expected to arrive over the wired management interface.  WLAN L3 traffic is
# generated only by the initial and final gateway pings.
set -Eeuo pipefail

restore_unit_active_state() {
    local unit="$1" was_active="$2"

    if [ "$was_active" = 1 ]; then
        systemctl start "$unit" && systemctl is-active --quiet "$unit"
    else
        systemctl stop "$unit" && ! systemctl is-active --quiet "$unit"
    fi
}

# The bgscan drop-in is a test-only gate left by the requester-isolation runs:
# the gate itself lives in /etc (persistent) while the file that satisfies it
# lives in /run (tmpfs).  Left behind, it makes the product service fail closed
# on every subsequent boot, and systemd records that as a condition skip rather
# than a failure, so neither `systemctl --failed` nor a journal error search
# surfaces it.  Preflight already asserts the product policy is
# bgscan.enabled=true, so the only correct end state for this harness is no gate.
remove_test_only_bgscan_dropin() {
    local dropin="$1" archive="${2:-}"

    [ -e "$dropin" ] || return 0
    if [ -n "$archive" ]; then
        cp -a -- "$dropin" "$archive" || return 1
    fi
    rm -f -- "$dropin" || return 1
    # Only succeeds while the directory holds nothing else, so unrelated
    # drop-ins on the same unit are preserved.
    rmdir -- "$(dirname -- "$dropin")" 2>/dev/null || true
    # Deleting the file is not enough — systemd serves the parsed unit until
    # it is told to re-read the configuration.
    systemctl daemon-reload || return 1
}

capture_safe_config_evidence() {
    local art="$1" mod_conf="$2" json_conf="$3" wpa_conf="$4" iface="$5"

    {
        printf '%s  wifi_mod_para.conf\n' "$(sha256sum "$mod_conf" | awk '{print $1}')"
        printf '%s  wifi_init_conf.json\n' "$(sha256sum "$json_conf" | awk '{print $1}')"
        printf '%s  wpa_supplicant.conf\n' "$(sha256sum "$wpa_conf" | awk '{print $1}')"
    } > "$art/config-identities.sha256"

    jq --arg iface "$iface" '{
        global: {
            BOARD_TYPE: .global.BOARD_TYPE,
            BUS_TYPE: .global.BUS_TYPE,
            MOD_PARA: .global.MOD_PARA,
            tx_work: .global.tx_work
        },
        wbridge: {
            enabled: .wbridge.enabled,
            engine: .wbridge.engine,
            moal: .wbridge.moal
        },
        interface: {
            name: $iface,
            enabled: .[$iface].enabled,
            bgscan: .[$iface].bgscan,
            roaming: {
                enabled: .[$iface].roaming.enabled,
                generate_network_blocks: .[$iface].roaming.generate_network_blocks,
                extra_ssids: .[$iface].roaming.extra_ssids
            }
        }
    }' "$json_conf" > "$art/module-input-json.txt"

    {
        sed -n '/^SD9098_0[[:space:]]*=/,/^}/p' "$mod_conf"
        sed -n '/^SD9098_1[[:space:]]*=/,/^}/p' "$mod_conf"
    } > "$art/wifi_mod_para-active-sd9098-blocks.txt"

    grep -nE '^(freq_list|scan_freq|#!INTERVAL|[[:space:]]+(ssid|freq_list|scan_freq)=)' \
        "$wpa_conf" > "$art/wpa-scan-policy.txt" || true

    cat > "$art/ARTIFACT_REVIEW_REQUIRED.txt" <<'EOF'
Raw WPA, JSON, and module configuration files are intentionally excluded.
Artifacts still contain test SSID/BSSID, network counters, and target identity.
Review and sanitize the complete directory before external publication.
EOF
}

emit_management_transport_evidence() {
    local connection="${1:-}" peer route_line route_device

    if [ -z "$connection" ]; then
        echo "management_transport=systemd-transient-unit"
        return 0
    fi

    peer="${connection%% *}"
    route_line="$(ip route get "$peer" 2>/dev/null || true)"
    route_device="$(
        awk '{
            for (i = 1; i <= NF; i++) {
                if ($i == "dev" && i < NF) {
                    print $(i + 1)
                    exit
                }
            }
        }' <<< "$route_line"
    )"
    echo "management_transport=ssh"
    echo "management_route_device=${route_device:-unknown}"
}

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

product_soak_cleanup() {
    local rc=$? restore_rc=0 dropin_removal_rc=0 bg_state_after_restore
    trap - EXIT INT TERM HUP
    if [ "$BG_STATE_TOUCHED" -eq 1 ]; then
        set +e
        restore_unit_active_state "$BG_UNIT" "$ORIGINAL_BG_WAS_ACTIVE"
        restore_rc=$?
        set -e
        if [ "$restore_rc" -ne 0 ]; then
            echo "ERROR: failed to restore $BG_UNIT to original activity state" >&2
            [ "$rc" -ne 0 ] || rc=70
        fi
    fi
    if [ "$ALLOW_CREATED" -eq 1 ]; then
        rm -f -- "$ALLOW_FILE"
    fi
    # Runs after the activity restore so "restore to as-found" keeps its
    # meaning; taking the gate away only changes what the NEXT boot does.
    set +e
    remove_test_only_bgscan_dropin "$DROPIN" "$ART/removed-test-dropin.conf"
    dropin_removal_rc=$?
    set -e
    if [ "$dropin_removal_rc" -ne 0 ]; then
        echo "ERROR: failed to remove test-only drop-in $DROPIN" >&2
        [ "$rc" -ne 0 ] || rc=71
    fi
    bg_state_after_restore="$(systemctl is-active "$BG_UNIT" 2>/dev/null || true)"
    {
        if [ "$FINALIZED" -eq 0 ]; then
            echo "result=ABORTED"
        fi
        echo "harness_exit=$rc"
        echo "finished_at=$(date --iso-8601=seconds)"
        echo "bgscan_original_state=$ORIGINAL_BG_STATE"
        echo "bgscan_restore_rc=$restore_rc"
        echo "bgscan_state_after_restore=$bg_state_after_restore"
        echo "allow_file_preexisted=$ALLOW_EXISTED"
        echo "allow_file_present_after=$([ -e "$ALLOW_FILE" ] && echo 1 || echo 0)"
        echo "dropin_removal_rc=$dropin_removal_rc"
        echo "dropin_present_after=$([ -e "$DROPIN" ] && echo 1 || echo 0)"
    } > "$ART/harness-exit.txt"
    touch "$ART/DONE"
    exit "$rc"
}

if [ "${BASH_SOURCE[0]}" != "$0" ]; then
    return 0
fi

if [ "${1:-}" != --ack-disruptive ]; then
    echo "ERROR: --ack-disruptive is required; reserve the board and plan service restoration" >&2
    exit 2
fi
shift

if [ "$#" -lt 4 ] || [ "$#" -gt 6 ]; then
    echo "usage: $0 --ack-disruptive <artifact-dir> <iface> <ssid> <gateway> [max-scans] [max-elapsed-sec]" >&2
    exit 2
fi
ART="$1"
IFACE="$2"
SSID="$3"
GW="$4"
MAX_SCANS="${5:-30}"
MAX_ELAPSED="${6:-2400}"
FAIL_DELTA="${FAIL_DELTA:-1000}"
EXPECTED_FREQS=(5180 5200 5220 5240)
EXPECTED_CMD="['iw', '$IFACE', 'scan', 'freq', '5180', '5200', '5220', '5240', 'ssid', '$SSID']"
BG_UNIT="wifi_bgscan@${IFACE}.service"
ROAM_UNIT="wifi_roam@${IFACE}.service"
WPA_UNIT="wpa_supplicant@${IFACE}.service"
CONF="/usr/local/etc/wifi_init_conf.json"
WPA_CONF="/etc/wpa_supplicant/wpa_supplicant-${IFACE}.conf"
POLICY="/run/wifi/${IFACE}.roam-policy.json"
MOD_CONF="/lib/firmware/cts/wifi_mod_para.conf"
ALLOW_FILE="/run/task10-bgscan-control-allow"
DROPIN="/etc/systemd/system/${BG_UNIT}.d/task10-bgscan-off-control.conf"

validate_new_artifact_dir "$ART"
[[ "$IFACE" =~ ^[-A-Za-z0-9_.:]+$ ]] \
    || input_error "unsupported interface name: $IFACE"
[ -n "$SSID" ] || input_error "SSID must not be empty"
[[ "$GW" =~ ^[-A-Za-z0-9_.:]+$ ]] \
    || input_error "unsupported gateway: $GW"
for limit in "$MAX_SCANS" "$MAX_ELAPSED" "$FAIL_DELTA"; do
    [[ "$limit" =~ ^[1-9][0-9]*$ ]] \
        || input_error "limits must be positive decimal integers"
done

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
cp -- "$0" "$ART/reproducer.sh"
exec > >(tee -a "$ART/controller.log") 2>&1

ORIGINAL_BG_STATE="$(systemctl is-active "$BG_UNIT" 2>/dev/null || true)"
ORIGINAL_BG_WAS_ACTIVE=0
[ "$ORIGINAL_BG_STATE" = active ] && ORIGINAL_BG_WAS_ACTIVE=1
ALLOW_EXISTED=0
[ -e "$ALLOW_FILE" ] && ALLOW_EXISTED=1
ALLOW_CREATED=0
BG_STATE_TOUCHED=0
FINALIZED=0

trap product_soak_cleanup EXIT INT TERM HUP

status_value() {
    local key="$1"
    wpa_cli -i "$IFACE" status 2>/dev/null | sed -n "s/^${key}=//p" | head -1
}

tx_failed() {
    iw dev "$IFACE" station dump 2>/dev/null | awk '/tx failed:/ {print $3; exit}'
}

capture_status() {
    local out="$1"
    {
        date --iso-8601=seconds
        echo "boot_id=$(cat /proc/sys/kernel/random/boot_id)"
        echo "--- wpa status ---"
        wpa_cli -i "$IFACE" status
        echo "--- iw link ---"
        iw dev "$IFACE" link
        echo "--- station ---"
        iw dev "$IFACE" station dump
        echo "--- interface counters ---"
        ip -s link show dev "$IFACE"
        echo "--- neighbors ---"
        ip neigh show dev "$IFACE"
        echo "--- roam condition ---"
        cat "/run/wifi/roam_condition_${IFACE}" 2>/dev/null || echo absent
        echo "--- services ---"
        systemctl is-active "$WPA_UNIT" "$ROAM_UNIT" "$BG_UNIT" || true
    } > "$out" 2>&1
}

capture_module_evidence() {
    local out="$ART/module-load-evidence.txt"
    {
        echo "# Module files selected by wifi_init.sh for BOARD_TYPE=imx93"
        echo "insmod /opt/wlan/driver/mlan_imx93.ko"
        echo "# Exact moal_args emitted by the boot's wifi_init.sh:"
        journalctl -b --no-pager --all -o cat \
            | grep -E 'moal: tx_work|moal: deliver_rt_prio|moal engine: bridge params added' || true
        echo
        echo "# Module/file identity"
        sha256sum /opt/wlan/driver/mlan_imx93.ko \
            /opt/wlan/driver/moal_imx93.ko \
            /lib/firmware/cts/sd9098_wlan_v1.bin \
            "$MOD_CONF" "$CONF" "$WPA_CONF" \
            /usr/local/scripts/wifi_init.sh \
            /usr/local/logger/wifi_bgscan.py \
            /usr/local/logger/wifi_roam.py
        for ko in /opt/wlan/driver/mlan_imx93.ko /opt/wlan/driver/moal_imx93.ko; do
            echo "--- modinfo $ko ---"
            modinfo "$ko" | grep -E '^(filename|version|srcversion|vermagic|depends|parm):' || true
        done
        echo
        echo "# Loaded modules"
        grep -E '^(mlan|moal) ' /proc/modules || true
        echo
        echo "# Exported runtime module parameters"
        for module in mlan moal; do
            echo "--- $module ---"
            for param in "/sys/module/$module/parameters/"*; do
                [ -e "$param" ] || continue
                printf '%s=' "${param##*/}"
                cat "$param" 2>&1 || true
            done | sort
        done
        echo
        echo "# Boot-time driver evidence (mod_conf values are per-card and can differ from global sysfs defaults)"
        dmesg | grep -E 'SD9098: init module param|card_type: SD9098|drv_mode =|ps_mode =|scan_chan_gap =|WLAN FW is active|wlan: version =' || true
        echo
        echo "# Runtime scan configuration (separate layer from mod_conf card blocks)"
        if [ -x /usr/local/bin/mlanutl ]; then
            echo "--- mlanutl binary ---"
            readlink -f /usr/local/bin/mlanutl
            sha256sum "$(readlink -f /usr/local/bin/mlanutl)"
            echo "--- mlan0 scancfg ---"
            /usr/local/bin/mlanutl mlan0 scancfg 2>&1 || true
            echo "--- mlan1 scancfg ---"
            /usr/local/bin/mlanutl mlan1 scancfg 2>&1 || true
        else
            echo "/usr/local/bin/mlanutl unavailable"
        fi
    } > "$out" 2>&1

    cp -- /usr/local/scripts/wifi_init.sh "$ART/wifi_init.sh"
    capture_safe_config_evidence "$ART" "$MOD_CONF" "$CONF" "$WPA_CONF" "$IFACE"
}

BOOT_ID="$(cat /proc/sys/kernel/random/boot_id)"
START_EPOCH="$(date +%s)"
START_ISO="$(date --iso-8601=seconds)"
JSON_HASH="$(sha256sum "$CONF" | awk '{print $1}')"
WPA_HASH="$(sha256sum "$WPA_CONF" | awk '{print $1}')"
MOD_CONF_HASH="$(sha256sum "$MOD_CONF" | awk '{print $1}')"
GLOBAL_CURSOR="$(journalctl -n 0 --show-cursor --no-pager | sed -n 's/^-- cursor: //p')"
WPA_CURSOR="$(journalctl -u "$WPA_UNIT" -n 0 --show-cursor --no-pager | sed -n 's/^-- cursor: //p')"
KERNEL_CURSOR="$(journalctl -k -n 0 --show-cursor --no-pager | sed -n 's/^-- cursor: //p')"

capture_module_evidence

{
    echo "boot_id=$BOOT_ID"
    echo "start_iso=$START_ISO"
    echo "artifact_publication=review-required"
    echo "expected_backend=iw"
    echo "expected_command=$EXPECTED_CMD"
    echo "expected_interval_seconds=60"
    echo "max_scans=$MAX_SCANS"
    echo "max_elapsed_seconds=$MAX_ELAPSED"
    echo "gateway=$GW"
    echo "original_bgscan_state=$ORIGINAL_BG_STATE"
    echo "allow_file_preexisted=$ALLOW_EXISTED"
    echo "json_sha256=$JSON_HASH"
    echo "wpa_conf_sha256=$WPA_HASH"
    echo "mod_conf_sha256=$MOD_CONF_HASH"
    echo "--- policy ---"
    cat "$POLICY"
    echo
    echo "--- focused JSON ---"
    jq "{global:{BOARD_TYPE:.global.BOARD_TYPE,BUS_TYPE:.global.BUS_TYPE,BLUETOOTH:.global.BLUETOOTH,MOD_PARA:.global.MOD_PARA,CAL_DATA_CFG:.global.CAL_DATA_CFG,tx_work:.global.tx_work},wbridge:{enabled:.wbridge.enabled,bridge_iface:.wbridge.bridge_iface,engine:.wbridge.engine,moal:.wbridge.moal},iface:{enabled:.[\"$IFACE\"].enabled,bgscan:.[\"$IFACE\"].bgscan,roaming:{enabled:.[\"$IFACE\"].roaming.enabled,generate_network_blocks:.[\"$IFACE\"].roaming.generate_network_blocks,extra_ssids:.[\"$IFACE\"].roaming.extra_ssids}}}" "$CONF"
    echo "--- supplicant scan policy ---"
    grep -nE '^(freq_list|scan_freq|#!INTERVAL|[[:space:]]+(ssid|freq_list|scan_freq)=)' "$WPA_CONF"
    echo "--- product units before ---"
    systemctl is-active "$WPA_UNIT" "$ROAM_UNIT" "$BG_UNIT" || true
    systemctl is-enabled "$ROAM_UNIT" "$BG_UNIT" || true
    echo "--- bgscan drop-in ---"
    cat "$DROPIN" 2>/dev/null || echo absent
    echo "--- wired control route ---"
    emit_management_transport_evidence "${SSH_CONNECTION:-}"
} > "$ART/preflight.txt" 2>&1

# Strict product-policy preflight.  Do not silently turn this into a manual iw test.
[ "$(jq -r '.roaming_enabled' "$POLICY")" = true ] || { echo "ERROR: policy roaming_enabled != true"; exit 10; }
[ "$(jq -r '.bgscan_enabled' "$POLICY")" = true ] || { echo "ERROR: policy bgscan_enabled != true"; exit 11; }
[ "$(jq -r ".[\"$IFACE\"].bgscan.interval" "$CONF")" = 60 ] || { echo "ERROR: interval is not 60"; exit 12; }
[ "$(jq -r ".[\"$IFACE\"].bgscan.enabled" "$CONF")" = true ] || { echo "ERROR: bgscan disabled"; exit 13; }
[ "$(jq -r ".[\"$IFACE\"].roaming.enabled" "$CONF")" = true ] || { echo "ERROR: backend owner is not wifi_roam/iw"; exit 14; }
GLOBAL_FREQ_LINE="$(sed -n 's/^freq_list=//p' "$WPA_CONF" | head -1)"
[ "$GLOBAL_FREQ_LINE" = "${EXPECTED_FREQS[*]}" ] || { echo "ERROR: unexpected global freq_list=$GLOBAL_FREQ_LINE"; exit 15; }
[ "$(status_value wpa_state)" = COMPLETED ] || { echo "ERROR: wpa baseline not COMPLETED"; exit 16; }
[ "$(status_value ssid)" = "$SSID" ] || { echo "ERROR: baseline SSID mismatch"; exit 17; }
[ "$(systemctl is-active "$ROAM_UNIT" 2>/dev/null || true)" = active ] || { echo "ERROR: wifi_roam is not active"; exit 18; }

set +e
ping -I "$IFACE" -c 5 -W 2 "$GW" > "$ART/initial-ping.txt" 2>&1
INITIAL_PING_RC=$?
set -e
[ "$INITIAL_PING_RC" -eq 0 ] || { echo "ERROR: unhealthy initial WLAN data path"; exit 19; }

INITIAL_STATE="$(status_value wpa_state)"
INITIAL_BSSID="$(status_value bssid)"
INITIAL_SSID="$(status_value ssid)"
INITIAL_FREQ="$(status_value freq)"
INITIAL_TX_FAILED="$(tx_failed)"
INITIAL_TX_FAILED="${INITIAL_TX_FAILED:-0}"
capture_status "$ART/baseline-after-initial-ping.txt"

# The drop-in was installed by the earlier requester-isolation tests.  Satisfy
# it without changing the deployed unit or JSON, then run the real service.
# Cleanup removes the drop-in afterwards -- see remove_test_only_bgscan_dropin.
if [ "$ALLOW_EXISTED" -eq 0 ]; then
    : > "$ALLOW_FILE"
    ALLOW_CREATED=1
fi
BG_STATE_TOUCHED=1
systemctl start "$BG_UNIT"
sleep 3
[ "$(systemctl is-active "$BG_UNIT" 2>/dev/null || true)" = active ] || {
    echo "ERROR: $BG_UNIT did not become active"
    systemctl status "$BG_UNIT" --no-pager -l || true
    exit 20
}

echo "START product bgscan soak at $START_ISO; expected=$EXPECTED_CMD"
LAST_COUNT=0
STOP_REASON=max_scans
ONSET_COUNT=""
ONSET_TX_FAILED=""
ONSET_ELAPSED=""

while :; do
    NOW="$(date +%s)"
    ELAPSED=$((NOW - START_EPOCH))
    CURRENT_BOOT="$(cat /proc/sys/kernel/random/boot_id)"
    if [ "$CURRENT_BOOT" != "$BOOT_ID" ]; then
        STOP_REASON=boot_changed
        break
    fi

    BG_LOG="$(journalctl -u "$BG_UNIT" --since "@$START_EPOCH" --no-pager --all -o cat 2>/dev/null || true)"
    COUNT="$(printf '%s\n' "$BG_LOG" | grep -F -c "[$IFACE] ['iw'," || true)"
    BAD_COUNT="$(printf '%s\n' "$BG_LOG" | grep -F "[$IFACE] ['iw'," | grep -Fv -c "$EXPECTED_CMD" || true)"
    ERROR_COUNT="$(printf '%s\n' "$BG_LOG" | grep -E -c 'scan (exited|timed out|execution failed)|config .*failed' || true)"
    STATE="$(status_value wpa_state)"
    BSSID="$(status_value bssid)"
    TX="$(tx_failed)"
    TX="${TX:-$INITIAL_TX_FAILED}"
    DELTA=$((TX - INITIAL_TX_FAILED))
    ROAM_FLAG="$(cat "/run/wifi/roam_condition_${IFACE}" 2>/dev/null || echo absent)"
    BG_STATE="$(systemctl is-active "$BG_UNIT" 2>/dev/null || true)"

    if [ "$COUNT" -ne "$LAST_COUNT" ]; then
        printf '%s scans=%s elapsed_s=%s tx_failed=%s delta=%s state=%s bssid=%s roam_flag=%s service=%s\n' \
            "$(date --iso-8601=seconds)" "$COUNT" "$ELAPSED" "$TX" "$DELTA" \
            "$STATE" "$BSSID" "$ROAM_FLAG" "$BG_STATE" | tee -a "$ART/progress.log"
        LAST_COUNT="$COUNT"
    fi

    if [ "$BAD_COUNT" -ne 0 ]; then STOP_REASON=unexpected_scan_grammar; break; fi
    if [ "$ERROR_COUNT" -ne 0 ]; then STOP_REASON=bgscan_error; break; fi
    if [ "$BG_STATE" != active ]; then STOP_REASON=bgscan_service_inactive; break; fi
    if [ "$STATE" != COMPLETED ]; then STOP_REASON=wpa_state_changed; break; fi
    if [ "$BSSID" != "$INITIAL_BSSID" ]; then STOP_REASON=bssid_changed; break; fi
    if [ "$ROAM_FLAG" != 0 ]; then STOP_REASON=roam_condition_entered; break; fi
    if [ "$DELTA" -ge "$FAIL_DELTA" ]; then
        STOP_REASON=tx_failed_spike
        ONSET_COUNT="$COUNT"
        ONSET_TX_FAILED="$TX"
        ONSET_ELAPSED="$ELAPSED"
        break
    fi
    if [ "$COUNT" -ge "$MAX_SCANS" ]; then
        # The command is logged immediately before subprocess.run().  Wait for
        # the final iw child/result to finish before stopping the service.
        COMPLETE_DEADLINE=$((NOW + 45))
        while pgrep -f "^iw $IFACE scan " >/dev/null 2>&1 && [ "$(date +%s)" -lt "$COMPLETE_DEADLINE" ]; do
            sleep 1
        done
        if pgrep -f "^iw $IFACE scan " >/dev/null 2>&1; then
            STOP_REASON=final_scan_completion_timeout
        fi
        break
    fi
    if [ "$ELAPSED" -ge "$MAX_ELAPSED" ]; then STOP_REASON=max_elapsed_timeout; break; fi
    sleep 5
done

# Freeze the radio requester before the one final WLAN datapath probe.
systemctl stop "$BG_UNIT" >/dev/null 2>&1 || true
if [ "$ALLOW_CREATED" -eq 1 ]; then
    rm -f -- "$ALLOW_FILE"
    ALLOW_CREATED=0
fi
sleep 2

PRE_PING_TX_FAILED="$(tx_failed)"
PRE_PING_TX_FAILED="${PRE_PING_TX_FAILED:-0}"
FINAL_STATE="$(status_value wpa_state)"
FINAL_BSSID="$(status_value bssid)"
FINAL_SSID="$(status_value ssid)"
FINAL_FREQ="$(status_value freq)"
capture_status "$ART/pre-final-ping.txt"

set +e
ping -I "$IFACE" -c 5 -W 2 "$GW" > "$ART/final-ping.txt" 2>&1
FINAL_PING_RC=$?
set -e
POST_PING_TX_FAILED="$(tx_failed)"
POST_PING_TX_FAILED="${POST_PING_TX_FAILED:-0}"
capture_status "$ART/post-final-ping.txt"

journalctl --after-cursor "$GLOBAL_CURSOR" --no-pager --all -o short-unix > "$ART/journal-all.log" 2>&1 || true
journalctl -u "$WPA_UNIT" --after-cursor "$WPA_CURSOR" --no-pager --all -o short-unix > "$ART/wpa-journal.log" 2>&1 || true
journalctl -k --after-cursor "$KERNEL_CURSOR" --no-pager --all -o short-unix > "$ART/kernel-journal.log" 2>&1 || true
grep -E 'SCAN .*\[mlan0\]|wifi_bgscan|External program started a scan|own=0 ext=1' "$ART/journal-all.log" > "$ART/bgscan-journal.log" 2>/dev/null || true

grep -F "[$IFACE] ['iw'," "$ART/bgscan-journal.log" > "$ART/scan-request-lines.log" 2>/dev/null || true
awk '
    {
        t=$1+0
        n++
        printf "scan=%d epoch=%.6f", n, t
        if (n > 1) {
            gap=t-prev
            printf " start_gap_seconds=%.3f", gap
            sum+=gap
            if (n == 2 || gap < min) min=gap
            if (n == 2 || gap > max) max=gap
        }
        print ""
        prev=t
    }
    END {
        print "request_count=" n
        if (n > 1) {
            printf "start_gap_min_seconds=%.3f\n", min
            printf "start_gap_max_seconds=%.3f\n", max
            printf "start_gap_avg_seconds=%.3f\n", sum/(n-1)
        }
    }
' "$ART/scan-request-lines.log" > "$ART/cadence-summary.txt"

REQUEST_COUNT="$(grep -F -c "[$IFACE] ['iw'," "$ART/bgscan-journal.log" 2>/dev/null || true)"
BAD_REQUEST_COUNT="$(grep -F "[$IFACE] ['iw'," "$ART/bgscan-journal.log" 2>/dev/null | grep -Fv -c "$EXPECTED_CMD" || true)"
EXTERNAL_COUNT="$(grep -F -c 'External program started a scan' "$ART/wpa-journal.log" 2>/dev/null || true)"
EXT_RESULT_COUNT="$(grep -F -c 'own=0 ext=1' "$ART/wpa-journal.log" 2>/dev/null || true)"
OWN_SCAN_COUNT="$(grep -F -c 'Own scan request started a scan' "$ART/wpa-journal.log" 2>/dev/null || true)"
DISCONNECT_COUNT="$(grep -E -c 'CTRL-EVENT-DISCONNECTED|Authentication timed out|SSID-TEMP-DISABLED' "$ART/wpa-journal.log" 2>/dev/null || true)"
END_EPOCH="$(date +%s)"

RESULT=INCONCLUSIVE
POST_DELTA=$((POST_PING_TX_FAILED - INITIAL_TX_FAILED))
if [ "$FINAL_PING_RC" -ne 0 ] && [ "$POST_DELTA" -ge "$FAIL_DELTA" ] \
   && [ "$FINAL_STATE" = COMPLETED ] && [ "$FINAL_BSSID" = "$INITIAL_BSSID" ]; then
    RESULT=REPRODUCED
elif [ "$FINAL_PING_RC" -ne 0 ]; then
    RESULT=DATAPATH_FAIL_OTHER
elif [ "$STOP_REASON" = max_scans ] && [ "$REQUEST_COUNT" -eq "$MAX_SCANS" ] \
   && [ "$BAD_REQUEST_COUNT" -eq 0 ] && [ "$FINAL_STATE" = COMPLETED ] \
   && [ "$FINAL_BSSID" = "$INITIAL_BSSID" ] && [ "$DISCONNECT_COUNT" -eq 0 ] \
   && [ "$EXTERNAL_COUNT" -eq "$MAX_SCANS" ] && [ "$EXT_RESULT_COUNT" -eq "$MAX_SCANS" ] \
   && [ "$OWN_SCAN_COUNT" -eq 0 ]; then
    RESULT=NOT_REPRODUCED
fi

{
    echo "result=$RESULT"
    echo "boot_id=$BOOT_ID"
    echo "start_epoch=$START_EPOCH"
    echo "end_epoch=$END_EPOCH"
    echo "elapsed_seconds=$((END_EPOCH - START_EPOCH))"
    echo "stop_reason=$STOP_REASON"
    echo "expected_command=$EXPECTED_CMD"
    echo "expected_interval_seconds=60"
    echo "requested_scan_count=$REQUEST_COUNT"
    echo "unexpected_grammar_count=$BAD_REQUEST_COUNT"
    echo "wpa_external_scan_count=$EXTERNAL_COUNT"
    echo "wpa_ext_result_count=$EXT_RESULT_COUNT"
    echo "wpa_own_scan_count=$OWN_SCAN_COUNT"
    echo "disconnect_auth_error_count=$DISCONNECT_COUNT"
    echo "failure_onset_scan_count=$ONSET_COUNT"
    echo "failure_onset_elapsed=$ONSET_ELAPSED"
    echo "initial_tx_failed=$INITIAL_TX_FAILED"
    echo "failure_onset_tx_failed=$ONSET_TX_FAILED"
    echo "pre_ping_tx_failed=$PRE_PING_TX_FAILED"
    echo "post_ping_tx_failed=$POST_PING_TX_FAILED"
    echo "post_ping_tx_failed_delta=$POST_DELTA"
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
    echo "json_sha256_before=$JSON_HASH"
    echo "json_sha256_after=$(sha256sum "$CONF" | awk '{print $1}')"
    echo "wpa_conf_sha256_before=$WPA_HASH"
    echo "wpa_conf_sha256_after=$(sha256sum "$WPA_CONF" | awk '{print $1}')"
    echo "mod_conf_sha256_before=$MOD_CONF_HASH"
    echo "mod_conf_sha256_after=$(sha256sum "$MOD_CONF" | awk '{print $1}')"
    echo "bgscan_state_before_cleanup=$(systemctl is-active "$BG_UNIT" 2>/dev/null || true)"
    echo "allow_file_present_after=$([ -e "$ALLOW_FILE" ] && echo 1 || echo 0)"
} > "$ART/result.txt"

FINALIZED=1
echo "RESULT=$RESULT artifact=$ART scans=$REQUEST_COUNT elapsed=$((END_EPOCH - START_EPOCH))s"
