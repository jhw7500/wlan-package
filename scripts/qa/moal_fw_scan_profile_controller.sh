#!/bin/bash
set -Eeuo pipefail

usage() {
    cat >&2 <<EOF
usage:
  $0 --describe <run-config>
  $0 --validate-profile <run-config>
  $0 --ack-disruptive <artifact-dir> <run-config>

The run-config is a trusted Bash file. It freezes paths, module arguments,
required SHA-256 identities, and APPLY_* profile toggles. Destructive mode
requires a root-owned config that is not writable by group or others, and
confirms an exclusive board reservation plus a reboot/restore plan through
--ack-disruptive.
EOF
}

config_error() {
    echo "ERROR: $*" >&2
    exit 2
}

validate_new_artifact_dir() {
    local path="$1" parent base canonical_parent expected

    if [[ "$path" != /* ]] || [ "$path" = / ]; then
        config_error "artifact directory must be a new absolute artifact directory below an existing canonical parent"
    fi
    if [ -e "$path" ] || [ -L "$path" ]; then
        config_error "artifact path already exists: $path"
    fi
    parent="${path%/*}"
    base="${path##*/}"
    [ -n "$parent" ] || parent=/
    if [ -z "$base" ] || [ ! -d "$parent" ]; then
        config_error "artifact parent must already be a directory: $parent"
    fi
    canonical_parent="$(realpath -e -- "$parent")" \
        || config_error "cannot resolve artifact parent: $parent"
    if [ "$canonical_parent" = / ]; then
        expected="/$base"
    else
        expected="$canonical_parent/$base"
    fi
    [ "$path" = "$expected" ] \
        || config_error "artifact path must use its canonical parent: $path"
}

MODE=""
ART=""
case "${1:-}" in
    --describe)
        [ "$#" -eq 2 ] || { usage; exit 2; }
        MODE=describe
        RUN_CONFIG="$2"
        ;;
    --validate-profile)
        [ "$#" -eq 2 ] || { usage; exit 2; }
        MODE=validate-profile
        RUN_CONFIG="$2"
        ;;
    --ack-disruptive)
        [ "$#" -eq 3 ] || { usage; exit 2; }
        MODE=destructive
        ART="$2"
        RUN_CONFIG="$3"
        ;;
    *)
        usage
        exit 2
        ;;
esac

if [ "$MODE" = destructive ]; then
    validate_new_artifact_dir "$ART"
fi

EXPECTED_CONFIG_UID="$(id -u)"
if [ "$MODE" = destructive ]; then
    [ "$EXPECTED_CONFIG_UID" -eq 0 ] \
        || config_error "controller destructive mode must run as root"
    EXPECTED_CONFIG_UID=0
fi

open_validated_source_file() {
    local file="$1" expected_uid="$2" actual_uid mode file_type

    if [ ! -f "$file" ] || [ -L "$file" ]; then
        config_error "run config must be a regular non-symlink file: $file"
    fi
    exec {JHW_RUN_CONFIG_FD}< "$file" \
        || config_error "cannot open run config: $file"
    JHW_RUN_CONFIG_FD_PATH="/proc/$$/fd/$JHW_RUN_CONFIG_FD"
    actual_uid="$(stat -Lc '%u' -- "$JHW_RUN_CONFIG_FD_PATH")"
    mode="$(stat -Lc '%a' -- "$JHW_RUN_CONFIG_FD_PATH")"
    file_type="$(LC_ALL=C stat -Lc '%F' -- "$JHW_RUN_CONFIG_FD_PATH")"
    [ "$file_type" = "regular file" ] \
        || config_error "run config descriptor is not a regular file: $file"
    [ "$actual_uid" = "$expected_uid" ] \
        || config_error "run config owner uid must be $expected_uid: $file"
    (( (8#$mode & 8#022) == 0 )) \
        || config_error "run config must not be group/other writable: $file"
    JHW_RUN_CONFIG_SHA="$(sha256sum "$JHW_RUN_CONFIG_FD_PATH" | awk '{print $1}')"
    readonly JHW_RUN_CONFIG_FD JHW_RUN_CONFIG_FD_PATH JHW_RUN_CONFIG_SHA
}

open_validated_source_file "$RUN_CONFIG" "$EXPECTED_CONFIG_UID"

# Defaults are intentionally non-runnable. A version-specific run config must
# pin every applicable EXPECTED_* hash before a destructive board run.
PROFILE_NAME=""
IFACE=""
GW=""
STATION_IP=""
SSID=""
CONF_REL="cts/wifi_mod_para.conf"
CONF=""
JSON="/usr/local/etc/wifi_init_conf.json"
WPA_CONF=""
MLAN_KO="/opt/wlan/driver/mlan_imx93.ko"
MOAL_KO="/opt/wlan/driver/moal_imx93.ko"
FW="/lib/firmware/cts/sd9098_wlan_v1.bin"
MLANUTL="/usr/local/bin/mlanutl"
TXPWR_CONF="/lib/firmware/cts/txpwrlimit_cfg_9098.conf"
THERMAL_CONF="/lib/firmware/cts/config/debug.conf"
FW_LIB="/usr/local/scripts/wifi_fw_config_lib.sh"
MOAL_ARGS=()
APPLY_AB=0
APPLY_ANTCFG=0
ANT_TX=""
ANT_RX=""
EXPECTED_ANT_TX=""
EXPECTED_ANT_RX=""
EXPECTED_USER_HTSTREAM=""
DRVDBG_OR=""
APPLY_RATE=0
APPLY_MCS=0
EXPECTED_MLAN_SHA=""
EXPECTED_MOAL_SHA=""
EXPECTED_FW_SHA=""
EXPECTED_CONF_SHA=""
EXPECTED_JSON_SHA=""
EXPECTED_WPA_SHA=""
EXPECTED_MLANUTL_SHA=""
EXPECTED_FW_LIB_SHA=""
EXPECTED_TXPWR_SHA=""
EXPECTED_THERMAL_SHA=""

# Source the exact inode validated above. Reopening RUN_CONFIG here would leave
# a path-replacement race between the ownership check and root execution.
# shellcheck source=/dev/null
. "$JHW_RUN_CONFIG_FD_PATH"
exec {JHW_RUN_CONFIG_FD}<&-

[ -n "$PROFILE_NAME" ] || config_error "PROFILE_NAME is required"
[[ "$PROFILE_NAME" =~ ^[A-Za-z0-9._-]+$ ]] \
    || config_error "PROFILE_NAME contains unsupported characters: $PROFILE_NAME"
for required in IFACE GW STATION_IP SSID; do
    [ -n "${!required}" ] || config_error "$required is required"
done
for toggle in APPLY_AB APPLY_ANTCFG APPLY_RATE APPLY_MCS; do
    value="${!toggle}"
    [ "$value" = 0 ] || [ "$value" = 1 ] \
        || config_error "$toggle must be 0 or 1"
done
if [ -n "$DRVDBG_OR" ]; then
    [[ "$DRVDBG_OR" =~ ^(0[xX][0-9A-Fa-f]+|[0-9]+)$ ]] \
        || config_error "DRVDBG_OR must be a decimal or hexadecimal integer"
    if [[ "$DRVDBG_OR" == 0[xX]* ]]; then
        DRVDBG_OR_DEC=$((16#${DRVDBG_OR:2}))
    else
        DRVDBG_OR_DEC=$((10#$DRVDBG_OR))
    fi
    [ "$DRVDBG_OR_DEC" -le $((0xffffffff)) ] \
        || config_error "DRVDBG_OR must fit in 32 bits"
fi
if [ "$APPLY_ANTCFG" = 1 ]; then
    [ -n "$ANT_TX" ] || config_error "ANT_TX is required when APPLY_ANTCFG=1"
    [[ "$ANT_TX" =~ ^(0[xX][0-9A-Fa-f]+|[0-9]+)$ ]] \
        || config_error "ANT_TX must be a decimal or hexadecimal integer"
    if [ -n "$ANT_RX" ]; then
        [[ "$ANT_RX" =~ ^(0[xX][0-9A-Fa-f]+|[0-9]+)$ ]] \
            || config_error "ANT_RX must be a decimal or hexadecimal integer"
    fi
    EXPECTED_ANT_TX="${EXPECTED_ANT_TX:-$ANT_TX}"
    EXPECTED_ANT_RX="${EXPECTED_ANT_RX:-${ANT_RX:-$ANT_TX}}"
    for expected in EXPECTED_ANT_TX EXPECTED_ANT_RX; do
        [[ "${!expected}" =~ ^(0[xX][0-9A-Fa-f]+|[0-9]+)$ ]] \
            || config_error "$expected must be a decimal or hexadecimal integer"
    done
    if [ -n "$EXPECTED_USER_HTSTREAM" ]; then
        [[ "$EXPECTED_USER_HTSTREAM" =~ ^(0[xX][0-9A-Fa-f]+|[0-9]+)$ ]] \
            || config_error "EXPECTED_USER_HTSTREAM must be a decimal or hexadecimal integer"
    fi
fi
declare -p MOAL_ARGS 2>/dev/null | grep -q '^declare -a ' \
    || config_error "MOAL_ARGS must be a Bash indexed array"
[ "${#MOAL_ARGS[@]}" -gt 0 ] || config_error "MOAL_ARGS must not be empty"

CONF="${CONF:-/lib/firmware/$CONF_REL}"
WPA_CONF="${WPA_CONF:-/etc/wpa_supplicant/wpa_supplicant-${IFACE}.conf}"
WPA_UNIT="wpa_supplicant@${IFACE}.service"

if [ "$MODE" = describe ]; then
    echo "profile_name=$PROFILE_NAME"
    echo "iface=$IFACE"
    echo "apply_ab=$APPLY_AB"
    echo "apply_antcfg=$APPLY_ANTCFG"
    echo "drvdbg_or=${DRVDBG_OR:-<not-applied>}"
    if [ "$APPLY_ANTCFG" = 1 ]; then
        echo "ant_tx=$ANT_TX"
        if [ -n "$ANT_RX" ]; then
            echo "ant_rx=$ANT_RX"
            echo "ant_command=mlanutl $IFACE antcfg $ANT_TX $ANT_RX"
        else
            echo "ant_rx=<same-as-tx>"
            echo "ant_command=mlanutl $IFACE antcfg $ANT_TX"
        fi
        echo "expected_ant_tx=$EXPECTED_ANT_TX"
        echo "expected_ant_rx=$EXPECTED_ANT_RX"
        echo "expected_user_htstream=${EXPECTED_USER_HTSTREAM:-<not-verified>}"
    else
        echo "ant_tx=<not-applied>"
        echo "ant_rx=<not-applied>"
    fi
    echo "apply_rate=$APPLY_RATE"
    echo "apply_mcs=$APPLY_MCS"
    echo "moal_arg_count=${#MOAL_ARGS[@]}"
    printf 'moal_command=insmod %s' "$MOAL_KO"; printf ' %s' "${MOAL_ARGS[@]}"; echo
    exit 0
fi

require_sha_pin() {
    local name="$1" value="$2"
    [[ "$value" =~ ^[0-9A-Fa-f]{64}$ ]] \
        || config_error "$name must be a 64-hex SHA-256"
}

require_destructive_hash_pins() {
    require_sha_pin EXPECTED_MLAN_SHA "$EXPECTED_MLAN_SHA"
    require_sha_pin EXPECTED_MOAL_SHA "$EXPECTED_MOAL_SHA"
    require_sha_pin EXPECTED_FW_SHA "$EXPECTED_FW_SHA"
    require_sha_pin EXPECTED_CONF_SHA "$EXPECTED_CONF_SHA"
    require_sha_pin EXPECTED_JSON_SHA "$EXPECTED_JSON_SHA"
    require_sha_pin EXPECTED_WPA_SHA "$EXPECTED_WPA_SHA"
    require_sha_pin EXPECTED_MLANUTL_SHA "$EXPECTED_MLANUTL_SHA"
    if [ "$APPLY_RATE" = 1 ] || [ "$APPLY_MCS" = 1 ]; then
        require_sha_pin EXPECTED_FW_LIB_SHA "$EXPECTED_FW_LIB_SHA"
    fi
    if [ "$APPLY_AB" = 1 ]; then
        require_sha_pin EXPECTED_TXPWR_SHA "$EXPECTED_TXPWR_SHA"
        require_sha_pin EXPECTED_THERMAL_SHA "$EXPECTED_THERMAL_SHA"
    fi
}

require_destructive_hash_pins
if [ "$MODE" = validate-profile ]; then
    echo "profile_valid=$PROFILE_NAME"
    echo "run_config_sha256=$JHW_RUN_CONFIG_SHA"
    exit 0
fi

ISOLATION_UNITS=(
    "$WPA_UNIT"
    "wpa_supplicant@mlan1.service"
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

umask 077
mkdir -m 0700 -- "$ART"
mkdir -m 0700 -- "$ART/commands"
cp -- "$0" "$ART/moal_fw_scan_profile_controller.sh"
exec > >(tee -a "$ART/controller.log") 2>&1

on_exit() {
    local rc=$?
    printf 'controller_exit=%s\nfinished_at=%s\n' "$rc" "$(date --iso-8601=seconds)" \
        > "$ART/controller-exit.txt"
    if [ "$rc" -ne 0 ]; then
        dmesg > "$ART/dmesg-on-controller-error.txt" 2>&1 || true
        journalctl -b -a --no-pager > "$ART/journal-on-controller-error.txt" 2>&1 || true
    fi
    return "$rc"
}
trap on_exit EXIT

die() {
    echo "ERROR: $*" >&2
    exit 1
}

sha_of() {
    sha256sum "$1" | awk '{print $1}'
}

assert_sha() {
    local file="$1" expected="$2" actual
    test -f "$file" || die "missing required file: $file"
    actual=$(sha_of "$file")
    [ "$actual" = "$expected" ] || die "hash mismatch: $file expected=$expected actual=$actual"
}

run_logged() {
    local label="$1"
    shift
    local out="$ART/commands/${label}.txt" rc
    {
        printf 'started_at=%s\n' "$(date --iso-8601=ns)"
        printf 'command='; printf '%q ' "$@"; printf '\n'
    } > "$out"
    set +e
    "$@" >> "$out" 2>&1
    rc=$?
    set -e
    {
        printf 'exit=%s\n' "$rc"
        printf 'finished_at=%s\n' "$(date --iso-8601=ns)"
    } >> "$out"
    echo "REPLAY label=$label rc=$rc"
    [ "$rc" -eq 0 ] || die "replay command failed: $label rc=$rc"
}

status_value() {
    local key="$1"
    wpa_cli -i "$IFACE" status 2>/dev/null | sed -n "s/^${key}=//p" | head -1
}

fault_count_since_load() {
    dmesg | tail -n "+$((START_DMESG_LINES + 1))" \
        | grep -Ec 'FW trigger fw dump|START IN-BAND RESET|Start to process hanging|cmd_timeout_func' \
        || true
}

REQUIRED_FILES=("$MLAN_KO" "$MOAL_KO" "$FW" "$CONF" "$JSON" "$WPA_CONF" "$MLANUTL")
if [ "$APPLY_AB" = 1 ]; then
    REQUIRED_FILES+=("$TXPWR_CONF" "$THERMAL_CONF")
fi
if [ "$APPLY_RATE" = 1 ] || [ "$APPLY_MCS" = 1 ]; then
    REQUIRED_FILES+=("$FW_LIB")
fi
for file in "${REQUIRED_FILES[@]}"; do
    test -f "$file" || die "missing required file: $file"
done

assert_sha "$MLAN_KO" "$EXPECTED_MLAN_SHA"
assert_sha "$MOAL_KO" "$EXPECTED_MOAL_SHA"
assert_sha "$FW" "$EXPECTED_FW_SHA"
assert_sha "$CONF" "$EXPECTED_CONF_SHA"
assert_sha "$JSON" "$EXPECTED_JSON_SHA"
assert_sha "$WPA_CONF" "$EXPECTED_WPA_SHA"
assert_sha "$MLANUTL" "$EXPECTED_MLANUTL_SHA"
if [ "$APPLY_RATE" = 1 ] || [ "$APPLY_MCS" = 1 ]; then
    assert_sha "$FW_LIB" "$EXPECTED_FW_LIB_SHA"
fi
if [ "$APPLY_AB" = 1 ]; then
    assert_sha "$TXPWR_CONF" "$EXPECTED_TXPWR_SHA"
    assert_sha "$THERMAL_CONF" "$EXPECTED_THERMAL_SHA"
fi

[ "$(systemctl is-enabled wifi_init.service 2>/dev/null || true)" = disabled ] \
    || die "wifi_init.service must be disabled before this cold boot"
[ "$(systemctl is-active wifi_init.service 2>/dev/null || true)" != active ] \
    || die "wifi_init.service is active"
if lsmod | grep -Eq '^(moal|mlan)[[:space:]]'; then
    die "WLAN modules already loaded; clean wifi_init-disabled boot required"
fi

START_DMESG_LINES=$(dmesg | wc -l)
WPA_CURSOR=$(journalctl -u "$WPA_UNIT" -n 0 --show-cursor --no-pager 2>/dev/null \
    | sed -n 's/^-- cursor: //p')

{
    echo "profile_name=$PROFILE_NAME"
    echo "boot_id=$(cat /proc/sys/kernel/random/boot_id)"
    echo "started_at=$(date --iso-8601=seconds)"
    echo "run_config_name=${RUN_CONFIG##*/}"
    echo "run_config_sha256=$JHW_RUN_CONFIG_SHA"
    echo "wifi_init_enabled=$(systemctl is-enabled wifi_init.service 2>/dev/null || true)"
    echo "wifi_init_active=$(systemctl is-active wifi_init.service 2>/dev/null || true)"
    echo "excluded=networkctl,network/sysctl/peer-route processing,ExecStartPost child services"
    echo "mlan_command=insmod $MLAN_KO"
    printf 'moal_command=insmod %s' "$MOAL_KO"; printf ' %s' "${MOAL_ARGS[@]}"; echo
    echo "apply_ab=$APPLY_AB"
    echo "apply_antcfg=$APPLY_ANTCFG"
    echo "drvdbg_or=${DRVDBG_OR:-<not-applied>}"
    echo "ant_tx=${ANT_TX:-<not-applied>}"
    echo "ant_rx=${ANT_RX:-${ANT_TX:+<same-as-tx>}}"
    echo "expected_ant_tx=${EXPECTED_ANT_TX:-<not-verified>}"
    echo "expected_ant_rx=${EXPECTED_ANT_RX:-<not-verified>}"
    echo "expected_user_htstream=${EXPECTED_USER_HTSTREAM:-<not-verified>}"
    echo "apply_rate=$APPLY_RATE"
    echo "apply_mcs=$APPLY_MCS"
    sha256sum "${REQUIRED_FILES[@]}"
    echo "--- effective replay JSON ---"
    jq '{global:(.global|{BOARD_TYPE,BUS_TYPE,MOD_PARA,TXPWRLIMIT_PATH,tx_work}),mlan0:(.mlan0|{enabled,STANDARD,TXPWRLIMIT_PATH,thermal_mgmt,radio,antcfg,rate_adapt,mcs_tier,wpa_supplicant}),mlan1:(.mlan1|{enabled,STANDARD,TXPWRLIMIT_PATH,thermal_mgmt,radio,antcfg,rate_adapt,mcs_tier,wpa_supplicant}),wbridge:(.wbridge|{engine,moal})}' "$JSON"
    echo "--- current boot wifi_init journal ---"
    journalctl -b -u wifi_init.service -a --no-pager || true
} > "$ART/replay-definition.txt"

systemctl stop wifi-stack.target 2>/dev/null || true
for unit in "${ISOLATION_UNITS[@]}"; do
    systemctl stop "$unit" 2>/dev/null || true
done
sleep 2
pgrep -x wpa_supplicant >/dev/null 2>&1 && die "wpa_supplicant remains running"
systemctl mask --runtime "$WPA_UNIT" >/dev/null

run_logged 001-insmod-mlan insmod "$MLAN_KO"
run_logged 002-insmod-moal insmod "$MOAL_KO" "${MOAL_ARGS[@]}"
sleep 2
[ "$(fault_count_since_load)" -eq 0 ] || die "fault marker during module load"

for _ in $(seq 1 60); do
    [ -d "/sys/class/net/$IFACE" ] && break
    sleep 0.5
done
[ -d "/sys/class/net/$IFACE" ] || die "$IFACE not created"

# Diagnostic builds gate their marker stream behind drvdbg bits. Preserve all
# live bits and OR only the explicitly requested mask before any FW settings or
# association are applied, so ANTCFG/ASSOC markers are not missed.
if [ -n "$DRVDBG_OR" ]; then
    run_logged 005-drvdbg-get-before "$MLANUTL" "$IFACE" drvdbg
    DRVDBG_BEFORE=$(sed -nE \
        's/^[[:space:]]*drvdbg:[[:space:]]*(0[xX][0-9A-Fa-f]+).*$/\1/p' \
        "$ART/commands/005-drvdbg-get-before.txt" | tail -1)
    [[ "$DRVDBG_BEFORE" =~ ^0[xX][0-9A-Fa-f]+$ ]] \
        || die "unable to parse drvdbg before value: $DRVDBG_BEFORE"
    DRVDBG_BEFORE_DEC=$((16#${DRVDBG_BEFORE:2}))
    DRVDBG_EFFECTIVE_DEC=$((DRVDBG_BEFORE_DEC | DRVDBG_OR_DEC))
    printf -v DRVDBG_EFFECTIVE '0x%08x' "$DRVDBG_EFFECTIVE_DEC"
    run_logged 006-drvdbg-set "$MLANUTL" "$IFACE" drvdbg "$DRVDBG_EFFECTIVE"
    run_logged 007-drvdbg-get-after "$MLANUTL" "$IFACE" drvdbg
    DRVDBG_AFTER=$(sed -nE \
        's/^[[:space:]]*drvdbg:[[:space:]]*(0[xX][0-9A-Fa-f]+).*$/\1/p' \
        "$ART/commands/007-drvdbg-get-after.txt" | tail -1)
    [[ "$DRVDBG_AFTER" =~ ^0[xX][0-9A-Fa-f]+$ ]] \
        || die "unable to parse drvdbg after value: $DRVDBG_AFTER"
    [ "$((16#${DRVDBG_AFTER:2}))" -eq "$DRVDBG_EFFECTIVE_DEC" ] \
        || die "drvdbg mismatch expected=$DRVDBG_EFFECTIVE actual=$DRVDBG_AFTER"
fi

# Production order immediately after module insertion. mlan1 is disabled in
# the frozen JSON, so product wifi_init skips every per-interface FW command for it.
if [ "$APPLY_AB" = 1 ]; then
    run_logged 010-txpwr-2g "$MLANUTL" "$IFACE" hostcmd "$TXPWR_CONF" txpwrlimit_2g_cfg_set
    run_logged 011-txpwr-5g-sub0 "$MLANUTL" "$IFACE" hostcmd "$TXPWR_CONF" txpwrlimit_5g_cfg_set_sub0
    run_logged 012-txpwr-5g-sub1 "$MLANUTL" "$IFACE" hostcmd "$TXPWR_CONF" txpwrlimit_5g_cfg_set_sub1
    run_logged 013-txpwr-5g-sub2 "$MLANUTL" "$IFACE" hostcmd "$TXPWR_CONF" txpwrlimit_5g_cfg_set_sub2
    run_logged 014-txpwr-5g-sub3 "$MLANUTL" "$IFACE" hostcmd "$TXPWR_CONF" txpwrlimit_5g_cfg_set_sub3
    run_logged 020-thermal-enable "$MLANUTL" "$IFACE" hostcmd "$THERMAL_CONF" enable_thermal_mgmt

    sleep 0.2
    run_logged 030-macctrl "$MLANUTL" "$IFACE" macctrl 0x00010e13
    sleep 0.2
    run_logged 031-httxcfg "$MLANUTL" "$IFACE" httxcfg 0x00000063
    sleep 0.2
    run_logged 032-htcapinfo "$MLANUTL" "$IFACE" htcapinfo 0x05c20000
    sleep 0.2
    run_logged 033-reassoctrl "$MLANUTL" "$IFACE" reassoctrl 1
fi

if [ "$APPLY_RATE" = 1 ] || [ "$APPLY_MCS" = 1 ]; then
    export WIFI_MLANUTL="$MLANUTL"
    export WIFI_WPA_CLI="/usr/sbin/wpa_cli"
    export WIFI_MCS_PENDING_DIR="/run/wifi"
    # shellcheck source=/dev/null
    . "$FW_LIB"
fi

if [ "$APPLY_ANTCFG" = 1 ]; then
    ANT_ARGS=("$ANT_TX")
    [ -z "$ANT_RX" ] || ANT_ARGS+=("$ANT_RX")
    run_logged 040-antcfg-set "$MLANUTL" "$IFACE" antcfg "${ANT_ARGS[@]}"
    run_logged 041-antcfg-get "$MLANUTL" "$IFACE" antcfg
    ANT_GET="$ART/commands/041-antcfg-get.txt"
    ACTUAL_ANT_TX=$(sed -n 's/^Mode of Tx path is //p' "$ANT_GET" | tail -1)
    ACTUAL_ANT_RX=$(sed -n 's/^Mode of Rx path is //p' "$ANT_GET" | tail -1)
    ACTUAL_USER_HTSTREAM=$(sed -n \
        's/.*\[user_htstream=\(0[xX][0-9A-Fa-f][0-9A-Fa-f]*\)\].*/\1/p' \
        "$ANT_GET" | tail -1)
    [[ "$ACTUAL_ANT_TX" =~ ^(0[xX][0-9A-Fa-f]+|[0-9]+)$ ]] \
        || die "unable to parse antcfg Tx GET: $ACTUAL_ANT_TX"
    [[ "$ACTUAL_ANT_RX" =~ ^(0[xX][0-9A-Fa-f]+|[0-9]+)$ ]] \
        || die "unable to parse antcfg Rx GET: $ACTUAL_ANT_RX"
    [ "$((ACTUAL_ANT_TX))" -eq "$((EXPECTED_ANT_TX))" ] \
        || die "antcfg physical Tx mismatch expected=$EXPECTED_ANT_TX actual=$ACTUAL_ANT_TX"
    [ "$((ACTUAL_ANT_RX))" -eq "$((EXPECTED_ANT_RX))" ] \
        || die "antcfg physical Rx mismatch expected=$EXPECTED_ANT_RX actual=$ACTUAL_ANT_RX"
    if [ -n "$EXPECTED_USER_HTSTREAM" ]; then
        [[ "$ACTUAL_USER_HTSTREAM" =~ ^(0[xX][0-9A-Fa-f]+|[0-9]+)$ ]] \
            || die "unable to parse antcfg user_htstream GET: $ACTUAL_USER_HTSTREAM"
        [ "$((ACTUAL_USER_HTSTREAM))" -eq "$((EXPECTED_USER_HTSTREAM))" ] \
            || die "antcfg user_htstream mismatch expected=$EXPECTED_USER_HTSTREAM actual=$ACTUAL_USER_HTSTREAM"
    fi
fi

if [ "$APPLY_RATE" = 1 ]; then
    echo "REPLAY library=wifi_fw_apply_rate"
    wifi_fw_apply_rate "$JSON" "$IFACE"
    run_logged 050-rate-adapt-get "$MLANUTL" "$IFACE" rate_adapt_cfg
fi

if [ "$APPLY_MCS" = 1 ]; then
    echo "REPLAY library=wifi_fw_apply_mcs_verified"
    wifi_fw_apply_mcs_verified "$JSON" "$IFACE" \
        || die "pre-association MCS verification/defer failed"
    test -e "/run/wifi/mcs_verify_pending_${IFACE}" \
        || die "MCS did not enter the same pre-association deferred state as product boot"
    run_logged 060-mcstier-preassoc-get "$MLANUTL" "$IFACE" mcstiercfg
    run_logged 061-11ax-preassoc-get "$MLANUTL" "$IFACE" 11axcfg
else
    test ! -e "/run/wifi/mcs_verify_pending_${IFACE}" \
        || die "unexpected MCS pending marker in $PROFILE_NAME"
fi

ip link set dev "$IFACE" up
systemctl unmask --runtime "$WPA_UNIT" >/dev/null
systemctl reset-failed "$WPA_UNIT" 2>/dev/null || true
systemctl start --no-block "$WPA_UNIT" 2>/dev/null || true

READY=0
for _ in $(seq 1 90); do
    if [ "$(status_value wpa_state)" = COMPLETED ]; then
        READY=1
        break
    fi
    sleep 1
done
[ "$READY" -eq 1 ] || die "initial association did not complete"

CONNECTED_COUNT=0
if [ "$APPLY_MCS" = 1 ]; then
    echo "REPLAY library=wifi_fw_verify_mcs_connected phase=stage-and-reassociate"
    wifi_fw_verify_mcs_connected "$JSON" "$IFACE" \
        || die "connected MCS SET/reassociate stage failed"
    test -e "/run/wifi/mcs_reassociate_once_${IFACE}" \
        || die "product-observed one-time MCS reassociation was not requested"

    # Do not clear pending on the pre-reassociation link. Wait until wpa_supplicant
    # has emitted its second connected event, then perform the product GET verify.
    for _ in $(seq 1 120); do
        if [ -n "$WPA_CURSOR" ]; then
            CONNECTED_COUNT=$(journalctl -u "$WPA_UNIT" --after-cursor "$WPA_CURSOR" -a --no-pager 2>/dev/null \
                | grep -c 'CTRL-EVENT-CONNECTED' || true)
        else
            CONNECTED_COUNT=$(journalctl -b -u "$WPA_UNIT" -a --no-pager 2>/dev/null \
                | grep -c 'CTRL-EVENT-CONNECTED' || true)
        fi
        if [ "$CONNECTED_COUNT" -ge 2 ] && [ "$(status_value wpa_state)" = COMPLETED ]; then
            break
        fi
        sleep 0.25
    done
    [ "$CONNECTED_COUNT" -ge 2 ] || die "second association event not observed"

    echo "REPLAY library=wifi_fw_verify_mcs_connected phase=post-reassociate-get"
    wifi_fw_verify_mcs_connected "$JSON" "$IFACE" \
        || die "post-reassociation MCS GET verification failed"
    test ! -e "/run/wifi/mcs_verify_pending_${IFACE}" \
        || die "MCS pending marker remains after verification"
    test ! -e "/run/wifi/mcs_reassociate_once_${IFACE}" \
        || die "MCS reassociation marker remains after verification"
else
    if [ -n "$WPA_CURSOR" ]; then
        CONNECTED_COUNT=$(journalctl -u "$WPA_UNIT" --after-cursor "$WPA_CURSOR" -a --no-pager 2>/dev/null \
            | grep -c 'CTRL-EVENT-CONNECTED' || true)
    fi
fi

HEALTHY=0
for _ in $(seq 1 60); do
    if [ "$(status_value wpa_state)" = COMPLETED ] \
       && [ "$(status_value ssid)" = "$SSID" ] \
       && ip -4 -o address show dev "$IFACE" | grep -qF " $STATION_IP/" \
       && ping -I "$IFACE" -c 3 -W 2 "$GW" > "$ART/baseline-ping.txt" 2>&1; then
        HEALTHY=1
        break
    fi
    sleep 1
done
[ "$HEALTHY" -eq 1 ] || die "healthy associated data path not reached"
[ "$(fault_count_since_load)" -eq 0 ] || die "fault marker before scan test"

{
    echo "verified_at=$(date --iso-8601=seconds)"
    echo "boot_id=$(cat /proc/sys/kernel/random/boot_id)"
    echo "second_connected_event_count=$CONNECTED_COUNT"
    echo "wifi_init_enabled=$(systemctl is-enabled wifi_init.service 2>/dev/null || true)"
    echo "wifi_init_active=$(systemctl is-active wifi_init.service 2>/dev/null || true)"
    echo "--- loaded identity ---"
    for module in mlan moal; do
        printf '%s_version=' "$module"; cat "/sys/module/$module/version"
        printf '%s_srcversion=' "$module"; cat "/sys/module/$module/srcversion"
    done
    dmesg | grep 'wlan: version =' | tail -n 1
    echo "--- direct module parameters ---"
    for p in tx_work bridge_mode bridge_debug bridge_wlan_idx bridge_keepalive_ms \
             bridge_keepalive_idle_ms bridge_local_hairpin wq_sched_policy wq_sched_prio; do
        printf '%s=' "$p"
        cat "/sys/module/moal/parameters/$p" 2>/dev/null || echo '<not-exported>'
    done
    echo "--- live FW settings ---"
    "$MLANUTL" "$IFACE" drvdbg 2>&1 || true
    "$MLANUTL" "$IFACE" antcfg 2>&1 || true
    "$MLANUTL" "$IFACE" rate_adapt_cfg 2>&1 || true
    "$MLANUTL" "$IFACE" mcstiercfg 2>&1 || true
    "$MLANUTL" "$IFACE" 11axcfg 2>&1 || true
    echo "--- status/station/info ---"
    wpa_cli -i "$IFACE" status
    iw dev "$IFACE" station dump
    iw dev "$IFACE" info
    echo "--- address/route ---"
    ip address show dev "$IFACE"
    ip route show
    echo "--- services ---"
    for unit in "${ISOLATION_UNITS[@]}" wifi_init.service wifi-stack.target; do
        printf '%s enabled=%s active=%s\n' "$unit" \
            "$(systemctl is-enabled "$unit" 2>/dev/null || true)" \
            "$(systemctl is-active "$unit" 2>/dev/null || true)"
    done
    echo "fault_count_since_load=$(fault_count_since_load)"
} > "$ART/group-preflight.txt"

dmesg | tail -n "+$((START_DMESG_LINES + 1))" > "$ART/module-and-replay-dmesg.txt"
journalctl -b -a --no-pager > "$ART/replay-boot-journal.txt"

for unit in "${ISOLATION_UNITS[@]}"; do
    [ "$unit" = "$WPA_UNIT" ] && continue
    [ "$(systemctl is-active "$unit" 2>/dev/null || true)" != active ] \
        || die "isolation unit active: $unit"
done
[ "$(systemctl is-active "$WPA_UNIT" 2>/dev/null || true)" = active ] \
    || die "$WPA_UNIT is not active"

echo "READY profile=$PROFILE_NAME boot=$(cat /proc/sys/kernel/random/boot_id)"
