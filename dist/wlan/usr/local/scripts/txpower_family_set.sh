#!/bin/bash
#
# Set Marvell/NXP mlan txpowercfg entries by rate family.
#
# Examples:
#   ./scripts/txpower_family_set.sh -i mlan0 -p 5 legacy
#   ./scripts/txpower_family_set.sh -i mlan0 -p 10 ht vht he
#   ./scripts/txpower_family_set.sh -i mlan0 -p 15 all
#   ./scripts/txpower_family_set.sh -i mlan0 -p 20 --clamp all
#   ./scripts/txpower_family_set.sh -i mlan0 -p 10 --current
#   ./scripts/txpower_family_set.sh -i mlan0 --status
#   ./scripts/txpower_family_set.sh -i mlan0 --auto
#
# Notes:
#   - mlanutl txpowercfg SET is per group/rate, so this script loops over the
#     groups and rates used by each family.
#   - Use txratecfg separately when measuring one exact rate/MCS.

set -euo pipefail

IFACE="mlan0"
MLANUTL="${MLANUTL:-mlanutl}"
IW="${IW:-iw}"
POWER_DBM=""
DRY_RUN=0
AUTO=0
STATUS=0
RESET_BEFORE_SET=1
CLAMP=0
CURRENT=0
CURRENT_DETECTED=0
CLAMPED_COUNT=0
MISSING_LIMIT_COUNT=0
FAILED_COUNT=0
EFFECTIVE_POWER=""
CURRENT_LABEL=""
CURRENT_GROUP=""
CURRENT_RATE_MAX=""
TXPOWER_MODE=""
TXPOWER_MODE_TEXT=""
STATE_DIR="${TXPOWER_STATE_DIR:-/run}"
STATE_FILE=""
STATE_MODE=""
STATE_UPDATED=""
STATE_DIRTY=0

declare -A LIMIT_MAX
declare -A PARSED_GROUP_COUNT
declare -A PARSED_GROUP_FIRST_RATE
declare -A PARSED_GROUP_LAST_RATE
declare -A PARSED_GROUP_MIN_POWER
declare -A PARSED_GROUP_MAX_POWER
declare -A STATE_POWER
PARSED_GROUP_IDS=()
QUEUE_ENTRIES=()

usage() {
    cat <<EOF
Usage:
  $0 [-i iface] [-u mlanutl] -p power_dbm [legacy|ht|vht|he|all ...]
  $0 [-i iface] [-u mlanutl] -p power_dbm --current
  $0 [-i iface] [-u mlanutl] --status
  $0 [-i iface] [-u mlanutl] --auto

Options:
  -i iface      Interface name. Default: mlan0
  -u path       mlanutl path. Default: mlanutl or \$MLANUTL
  --iw path     iw path. Default: iw or \$IW
  -p dBm        Target TX power in dBm
  -n            Dry-run. Print commands without executing them
  --clamp       Clamp each group/rate to its current maximum power and mark it.
                Entries missing from the firmware GET table are skipped
  --current     Detect the current PHY format and bandwidth from iw link/info
  --status      Show txpower mode and current-link group power without setting
  --no-reset    Do not run "txpowercfg 0xFF" before applying a new power
  --auto        Restore firmware automatic TX power config: txpowercfg 0xFF
  -h, --help    Show this help

State tracking:
  Successful settings made by this script are stored per interface under
  \${TXPOWER_STATE_DIR:-/run}. Changes made by calling mlanutl directly cannot
  be reconstructed from the firmware GET table on every firmware version.

Families and txpowercfg groups:
  legacy        group 0, rate index 0..11
  ht            groups 1..2, MCS 0..15
  vht           groups 3..8, MCS 0..9
  he            groups 9..14, MCS 0..11
  all           legacy ht vht he

Examples:
  $0 -i mlan0 -p 5 legacy
  $0 -i mlan0 -p 10 all
  $0 -i mlan0 -p 20 --clamp all
  $0 -i mlan0 -p 10 --current
  $0 -i mlan0 --status
  $0 -i mlan0 -u /opt/wlan/bin/mlanutl_imx93 -p 15 he
EOF
}

die() {
    echo "error: $*" >&2
    exit 1
}

init_state_file() {
    local state_iface

    state_iface="$(printf '%s' "$IFACE" | tr -c 'A-Za-z0-9_.-' '_')"
    STATE_FILE="${STATE_DIR}/txpower_family_set.${state_iface}.state"
}

load_state() {
    local kind
    local a
    local b
    local c

    STATE_MODE=""
    STATE_UPDATED=""
    STATE_POWER=()
    [ -r "$STATE_FILE" ] || return 0

    while read -r kind a b c; do
        case "$kind" in
        mode)
            STATE_MODE="$a"
            ;;
        updated)
            STATE_UPDATED="$a"
            ;;
        power)
            if [[ "$a" =~ ^[0-9]+$ && "$b" =~ ^[0-9]+$ && "$c" =~ ^[0-9]+$ ]]; then
                STATE_POWER["${a}:${b}"]="$c"
            fi
            ;;
        esac
    done < "$STATE_FILE"
}

save_state() {
    local key
    local tmp

    [ "$STATE_DIRTY" -eq 1 ] || return 0
    [ "$DRY_RUN" -eq 0 ] || return 0
    [ -d "$STATE_DIR" ] || {
        echo "error: state directory does not exist: $STATE_DIR" >&2
        return 1
    }

    # mktemp 로 예측 불가한 이름 + 0600 파일을 원자적으로 생성한다($$ 기반 고정 이름은
    # /run 에서 symlink 선점 공격에 노출된다). 실패 경로마다 tmp 를 정리한다.
    tmp="$(mktemp "${STATE_FILE}.XXXXXX")" || {
        echo "error: cannot create temporary state file in $STATE_DIR" >&2
        return 1
    }
    if ! {
        echo "version 1"
        echo "mode ${STATE_MODE:-unknown}"
        echo "updated $(date +%s)"
        if [ "${#STATE_POWER[@]}" -gt 0 ]; then
            for key in "${!STATE_POWER[@]}"; do
                echo "power ${key%%:*} ${key#*:} ${STATE_POWER[$key]}"
            done | sort -k2,2n -k3,3n
        fi
    } > "$tmp"; then
        rm -f -- "$tmp"
        echo "error: failed to write state file" >&2
        return 1
    fi
    if ! mv -f -- "$tmp" "$STATE_FILE"; then
        rm -f -- "$tmp"
        echo "error: failed to install state file" >&2
        return 1
    fi
    STATE_DIRTY=0
}

persist_state_on_exit() {
    local status=$?

    trap - EXIT
    if ! save_state; then
        [ "$status" -ne 0 ] || status=1
    fi
    exit "$status"
}

record_auto_state() {
    [ "$DRY_RUN" -eq 0 ] || return 0
    STATE_MODE="auto-requested"
    STATE_POWER=()
    STATE_DIRTY=1
}

record_manual_power() {
    local group="$1"
    local rate="$2"
    local power="$3"

    [ "$DRY_RUN" -eq 0 ] || return 0
    if [ "$STATE_MODE" != "manual" ]; then
        STATE_POWER=()
    fi
    STATE_MODE="manual"
    STATE_POWER["${group}:${rate}"]="$power"
    STATE_DIRTY=1
}

run_cmd() {
    printf '+'
    printf ' %q' "$@"
    printf '\n'

    if [ "$DRY_RUN" -eq 0 ]; then
        "$@"
    fi
}

read_txpower_mode() {
    "$MLANUTL" "$IFACE" txpowercfg | awk '
    /Mode:/ {
        mode = $NF
        gsub(/[()]/, "", mode)
        text = ""
        for (i = 2; i < NF; i++) {
            if (text != "")
                text = text " "
            text = text $i
        }
        gsub(/[[:space:]]+$/, "", text)
        print mode, text
        exit
    }'
}

read_limits() {
    "$MLANUTL" "$IFACE" txpowercfg | awk '
    function flush(    group, start, end, r) {
        if (fmt == "" || max == "")
            return

        group = ""
        start = first
        end = last

        if (fmt == "LG" && bw == 20) {
            group = 0
        } else if (fmt == "HT" && bw == 20) {
            group = 1
            if (start >= 12) {
                start -= 12
                end -= 12
            }
        } else if (fmt == "HT" && bw == 40) {
            group = 2
            if (start >= 140) {
                start -= 140
                end -= 140
            }
        } else if (fmt == "VHT" && bw == 20 && nss == 1) {
            group = 3
        } else if (fmt == "VHT" && bw == 20 && nss == 2) {
            group = 4
        } else if (fmt == "VHT" && bw == 40 && nss == 1) {
            group = 5
        } else if (fmt == "VHT" && bw == 40 && nss == 2) {
            group = 6
        } else if (fmt == "VHT" && bw == 80 && nss == 1) {
            group = 7
        } else if (fmt == "VHT" && bw == 80 && nss == 2) {
            group = 8
        } else if (fmt == "HE" && bw == 20 && nss == 1) {
            group = 9
        } else if (fmt == "HE" && bw == 20 && nss == 2) {
            group = 10
        } else if (fmt == "HE" && bw == 40 && nss == 1) {
            group = 11
        } else if (fmt == "HE" && bw == 40 && nss == 2) {
            group = 12
        } else if (fmt == "HE" && bw == 80 && nss == 1) {
            group = 13
        } else if (fmt == "HE" && bw == 80 && nss == 2) {
            group = 14
        }

        if (group == "")
            return

        for (r = start; r <= end; r++)
            print group, r, max
    }

    /^    Power Group / {
        flush()
        fmt = ""
        bw = ""
        nss = 1
        first = ""
        last = ""
        max = ""
        next
    }
    /Bandwidth:/ {
        fmt = $2
        bw = $3 + 0
        next
    }
    /NSS:/ {
        nss = $2 + 0
        next
    }
    /first rate index:/ {
        first = $4 + 0
        next
    }
    /last rate index:/ {
        last = $4 + 0
        next
    }
    /maximum power:/ {
        max = $3 + 0
        next
    }
    END {
        flush()
    }'
}

load_limits() {
    local group
    local rate
    local max
    local count=0

    echo "== read current txpower limits =="
    while read -r group rate max; do
        LIMIT_MAX["${group}:${rate}"]="$max"
        if [ -z "${PARSED_GROUP_COUNT[$group]:-}" ]; then
            PARSED_GROUP_IDS+=("$group")
            PARSED_GROUP_COUNT["$group"]=0
            PARSED_GROUP_FIRST_RATE["$group"]="$rate"
            PARSED_GROUP_LAST_RATE["$group"]="$rate"
            PARSED_GROUP_MIN_POWER["$group"]="$max"
            PARSED_GROUP_MAX_POWER["$group"]="$max"
        fi
        PARSED_GROUP_COUNT["$group"]=$((PARSED_GROUP_COUNT["$group"] + 1))
        if [ "$rate" -lt "${PARSED_GROUP_FIRST_RATE[$group]}" ]; then
            PARSED_GROUP_FIRST_RATE["$group"]="$rate"
        fi
        if [ "$rate" -gt "${PARSED_GROUP_LAST_RATE[$group]}" ]; then
            PARSED_GROUP_LAST_RATE["$group"]="$rate"
        fi
        if [ "$max" -lt "${PARSED_GROUP_MIN_POWER[$group]}" ]; then
            PARSED_GROUP_MIN_POWER["$group"]="$max"
        fi
        if [ "$max" -gt "${PARSED_GROUP_MAX_POWER[$group]}" ]; then
            PARSED_GROUP_MAX_POWER["$group"]="$max"
        fi
        count=$((count + 1))
    done < <(read_limits)

    [ "$count" -gt 0 ] || die "failed to read txpower limits"
    echo "loaded ${count} group/rate limits"
}

group_label() {
    case "$1" in
    0) echo "legacy 20 MHz" ;;
    1) echo "HT 20 MHz" ;;
    2) echo "HT 40 MHz" ;;
    3) echo "VHT 20 MHz NSS 1" ;;
    4) echo "VHT 20 MHz NSS 2" ;;
    5) echo "VHT 40 MHz NSS 1" ;;
    6) echo "VHT 40 MHz NSS 2" ;;
    7) echo "VHT 80 MHz NSS 1" ;;
    8) echo "VHT 80 MHz NSS 2" ;;
    9) echo "HE 20 MHz NSS 1" ;;
    10) echo "HE 20 MHz NSS 2" ;;
    11) echo "HE 40 MHz NSS 1" ;;
    12) echo "HE 40 MHz NSS 2" ;;
    13) echo "HE 80 MHz NSS 1" ;;
    14) echo "HE 80 MHz NSS 2" ;;
    *) echo "group $1" ;;
    esac
}

show_parsed_groups() {
    local group

    echo "parsed groups from txpowercfg:"
    for group in $(printf '%s\n' "${PARSED_GROUP_IDS[@]}" | sort -n); do
        echo "  group ${group} ($(group_label "$group")): rates ${PARSED_GROUP_FIRST_RATE[$group]}..${PARSED_GROUP_LAST_RATE[$group]}, power ${PARSED_GROUP_MIN_POWER[$group]}..${PARSED_GROUP_MAX_POWER[$group]} dBm, entries ${PARSED_GROUP_COUNT[$group]}"
    done
}

load_txpower_mode() {
    local mode_line

    mode_line="$(read_txpower_mode || true)"
    if [ -n "$mode_line" ]; then
        TXPOWER_MODE="${mode_line%% *}"
        TXPOWER_MODE_TEXT="${mode_line#* }"
    else
        TXPOWER_MODE="not-exposed"
        TXPOWER_MODE_TEXT="not exposed by current mlanutl/driver"
    fi
}

set_effective_power() {
    local group="$1"
    local rate="$2"
    local key="${group}:${rate}"
    local max="${LIMIT_MAX[$key]:-}"

    if [ "$CLAMP" -eq 0 ]; then
        EFFECTIVE_POWER="$POWER_DBM"
        return
    fi

    if [ -z "$max" ]; then
        echo "warn: no max limit for group ${group} rate ${rate}; skipping (set without --clamp to force)" >&2
        MISSING_LIMIT_COUNT=$((MISSING_LIMIT_COUNT + 1))
        EFFECTIVE_POWER=""
        return
    fi

    if [ "$POWER_DBM" -gt "$max" ]; then
        echo "clamp: group ${group} rate ${rate}: requested ${POWER_DBM} dBm > max ${max} dBm; using ${max} dBm"
        CLAMPED_COUNT=$((CLAMPED_COUNT + 1))
        EFFECTIVE_POWER="$max"
        return
    fi

    EFFECTIVE_POWER="$POWER_DBM"
}

set_range() {
    local family="$1"
    local group_start="$2"
    local group_end="$3"
    local rate_start="$4"
    local rate_end="$5"
    local group
    local rate

    echo "== ${family}: ${POWER_DBM} dBm =="
    for group in $(seq "$group_start" "$group_end"); do
        for rate in $(seq "$rate_start" "$rate_end"); do
            set_effective_power "$group" "$rate"
            [ -n "$EFFECTIVE_POWER" ] || continue
            if [ "$CLAMP" -eq 1 ]; then
                QUEUE_ENTRIES+=("${EFFECTIVE_POWER} ${group} ${rate}")
            elif run_cmd "$MLANUTL" "$IFACE" txpowercfg "$group" "$rate" "$EFFECTIVE_POWER"; then
                record_manual_power "$group" "$rate" "$EFFECTIVE_POWER"
            else
                echo "warn: set failed for group ${group} rate ${rate}" >&2
                FAILED_COUNT=$((FAILED_COUNT + 1))
            fi
        done
    done
}

set_group() {
    local label="$1"
    local group="$2"
    local rate_start="$3"
    local rate_end="$4"
    local rate

    echo "== ${label}: ${POWER_DBM} dBm =="
    for rate in $(seq "$rate_start" "$rate_end"); do
        set_effective_power "$group" "$rate"
        [ -n "$EFFECTIVE_POWER" ] || continue
        if [ "$CLAMP" -eq 1 ]; then
            QUEUE_ENTRIES+=("${EFFECTIVE_POWER} ${group} ${rate}")
        elif run_cmd "$MLANUTL" "$IFACE" txpowercfg "$group" "$rate" "$EFFECTIVE_POWER"; then
            record_manual_power "$group" "$rate" "$EFFECTIVE_POWER"
        else
            echo "warn: set failed for group ${group} rate ${rate}" >&2
            FAILED_COUNT=$((FAILED_COUNT + 1))
        fi
    done
}

run_queued_entries() {
    local power
    local group
    local rate

    if [ "${#QUEUE_ENTRIES[@]}" -eq 0 ]; then
        echo "== apply queued entries: nothing to apply =="
        return 0
    fi
    echo "== apply queued entries: high power first =="
    while read -r power group rate; do
        if run_cmd "$MLANUTL" "$IFACE" txpowercfg "$group" "$rate" "$power"; then
            record_manual_power "$group" "$rate" "$power"
        else
            echo "warn: set failed for group ${group} rate ${rate}" >&2
            FAILED_COUNT=$((FAILED_COUNT + 1))
        fi
    done < <(printf '%s\n' "${QUEUE_ENTRIES[@]}" | sort -k1,1nr -k2,2n -k3,3n)
}

apply_family() {
    local family

    family="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"

    case "$family" in
    legacy)
        set_range "legacy" 0 0 0 11
        ;;
    ht)
        set_range "ht" 1 2 0 15
        ;;
    vht)
        set_range "vht" 3 8 0 9
        ;;
    he)
        set_range "he" 9 14 0 11
        ;;
    all)
        apply_family legacy
        apply_family ht
        apply_family vht
        apply_family he
        ;;
    *)
        die "unknown family '$1'. Use legacy, ht, vht, he, or all"
        ;;
    esac
}

detect_current_group() {
    local link
    local info
    local txline
    local fmt
    local bw
    local nss
    local group
    local max_rate

    # 멱등: preflight 에서 먼저 감지하면 apply 단계는 재실행(iw 재호출) 없이 캐시 사용.
    [ "$CURRENT_DETECTED" -eq 0 ] || return 0

    # 실패는 die 하지 않고 return 1 — 호출자(preflight/apply 는 die, --status 는 graceful)가 결정.
    link="$("$IW" dev "$IFACE" link)"
    info="$("$IW" dev "$IFACE" info)"
    txline="$(printf '%s\n' "$link" | awk '/tx bitrate:/ { sub(/^[[:space:]]*/, ""); print; exit }')"
    [ -n "$txline" ] || { echo "warn: cannot read tx bitrate from iw (interface not associated?)" >&2; return 1; }

    if printf '%s\n' "$txline" | grep -q "HE-MCS"; then
        fmt="he"
        max_rate=11
    elif printf '%s\n' "$txline" | grep -q "VHT-MCS"; then
        fmt="vht"
        max_rate=9
    elif printf '%s\n' "$txline" | grep -q "MCS"; then
        fmt="ht"
        max_rate=15
    else
        fmt="legacy"
        bw=20
        nss=1
        CURRENT_GROUP=0
        CURRENT_RATE_MAX=11
        CURRENT_LABEL="legacy 20 MHz"
        CURRENT_DETECTED=1
        return
    fi

    bw="$(printf '%s\n' "$txline" | sed -n 's/.* \([0-9][0-9]*\)MHz.*/\1/p' | head -1)"
    if [ -z "$bw" ]; then
        bw="$(printf '%s\n' "$info" | sed -n 's/.*width:[[:space:]]*\([0-9][0-9]*\)[[:space:]]*MHz.*/\1/p' | head -1)"
    fi
    [ -n "$bw" ] || { echo "warn: cannot detect current channel bandwidth from iw" >&2; return 1; }

    nss=1
    if [ "$fmt" = "he" ]; then
        nss="$(printf '%s\n' "$txline" | sed -n 's/.*HE-NSS[[:space:]]*\([0-9][0-9]*\).*/\1/p' | head -1)"
        [ -n "$nss" ] || nss=1
    elif [ "$fmt" = "vht" ]; then
        nss="$(printf '%s\n' "$txline" | sed -n 's/.*VHT-NSS[[:space:]]*\([0-9][0-9]*\).*/\1/p' | head -1)"
        [ -n "$nss" ] || nss=1
    fi

    case "${fmt}:${bw}:${nss}" in
    ht:20:*) group=1 ;;
    ht:40:*) group=2 ;;
    vht:20:1) group=3 ;;
    vht:20:2) group=4 ;;
    vht:40:1) group=5 ;;
    vht:40:2) group=6 ;;
    vht:80:1) group=7 ;;
    vht:80:2) group=8 ;;
    he:20:1) group=9 ;;
    he:20:2) group=10 ;;
    he:40:1) group=11 ;;
    he:40:2) group=12 ;;
    he:80:1) group=13 ;;
    he:80:2) group=14 ;;
    *)
        echo "warn: unsupported current mode: format=${fmt}, bandwidth=${bw}MHz, nss=${nss}" >&2
        return 1
        ;;
    esac

    CURRENT_GROUP="$group"
    CURRENT_RATE_MAX="$max_rate"
    CURRENT_LABEL="${fmt} ${bw} MHz NSS ${nss}"
    CURRENT_DETECTED=1
}

apply_current_group() {
    detect_current_group || die "cannot apply --current: interface not associated or unsupported mode"
    echo "detected current mode: ${CURRENT_LABEL}, group ${CURRENT_GROUP}"
    set_group "current ${CURRENT_LABEL}" "$CURRENT_GROUP" 0 "$CURRENT_RATE_MAX"
}

show_status() {
    local rate
    local value
    local source
    local unknown_count=0
    local total_count=0

    load_txpower_mode
    load_limits
    # --status 는 진단 명령 — 미연결이면 die 대신 current-group 섹션만 생략한다.
    local have_current=1
    detect_current_group || have_current=0

    load_state
    if [ "$STATE_MODE" = "manual" ]; then
        source="script-tracked successful SET commands"
    elif [ "$STATE_MODE" = "auto-requested" ]; then
        source="firmware GET limit table after script-requested 0xFF"
    else
        source="firmware GET limit table; current override is not recoverable"
    fi

    echo "== txpower status =="
    echo "firmware mode field: ${TXPOWER_MODE_TEXT} (${TXPOWER_MODE}); informational only"
    echo "script state: ${STATE_MODE:-not recorded}${STATE_UPDATED:+, updated epoch ${STATE_UPDATED}}"
    if [ "$have_current" -eq 1 ]; then
        echo "current link: ${CURRENT_LABEL}, group ${CURRENT_GROUP}"
    else
        echo "current link: not associated (current-group section skipped)"
    fi
    echo "value source: ${source}"
    if [ "$TXPOWER_MODE" = "not-exposed" ]; then
        echo "hint: run \"$MLANUTL $IFACE txpowercfg\" and check whether it prints \"Mode:\""
    fi
    [ "$have_current" -eq 1 ] || return 0
    echo "group ${CURRENT_GROUP} values:"
    for rate in $(seq 0 "$CURRENT_RATE_MAX"); do
        if [ "$STATE_MODE" = "manual" ]; then
            value="${STATE_POWER["${CURRENT_GROUP}:${rate}"]:-unknown}"
        else
            value="${LIMIT_MAX["${CURRENT_GROUP}:${rate}"]:-unknown}"
        fi
        total_count=$((total_count + 1))
        if [ "$value" = "unknown" ]; then
            unknown_count=$((unknown_count + 1))
        fi
        echo "  rate ${rate}: ${value} dBm"
    done
    if [ "$unknown_count" -eq "$total_count" ]; then
        if [ "$STATE_MODE" = "manual" ]; then
            echo "warning: current link group ${CURRENT_GROUP} was not set by the tracked script state"
        else
            echo "warning: current link group ${CURRENT_GROUP} was not found in parsed txpowercfg output"
        fi
        show_parsed_groups
    fi
}

while [ "$#" -gt 0 ]; do
    case "$1" in
    -i)
        [ "$#" -ge 2 ] || die "-i requires an interface"
        IFACE="$2"
        shift 2
        ;;
    -u)
        [ "$#" -ge 2 ] || die "-u requires a mlanutl path"
        MLANUTL="$2"
        shift 2
        ;;
    --iw)
        [ "$#" -ge 2 ] || die "--iw requires an iw path"
        IW="$2"
        shift 2
        ;;
    -p)
        [ "$#" -ge 2 ] || die "-p requires a power value"
        POWER_DBM="$2"
        shift 2
        ;;
    -n)
        DRY_RUN=1
        shift
        ;;
    --clamp)
        CLAMP=1
        shift
        ;;
    --current)
        CURRENT=1
        shift
        ;;
    --status)
        STATUS=1
        shift
        ;;
    --no-reset)
        RESET_BEFORE_SET=0
        shift
        ;;
    --auto)
        AUTO=1
        shift
        ;;
    -h | --help)
        usage
        exit 0
        ;;
    --)
        shift
        break
        ;;
    -*)
        die "unknown option '$1'"
        ;;
    *)
        break
        ;;
    esac
done

init_state_file
load_state
trap persist_state_on_exit EXIT

if [ "$AUTO" -eq 1 ]; then
    [ "$#" -eq 0 ] || die "--auto cannot be used with family arguments"
    run_cmd "$MLANUTL" "$IFACE" txpowercfg 0xFF
    record_auto_state
    exit 0
fi

if [ "$STATUS" -eq 1 ]; then
    [ "$#" -eq 0 ] || die "--status cannot be used with family arguments"
    [ "$CURRENT" -eq 0 ] || die "--status already uses current link detection"
    [ "$CLAMP" -eq 0 ] || die "--status cannot be used with --clamp"
    [ "$DRY_RUN" -eq 0 ] || die "--status cannot be used with dry-run"
    show_status
    exit 0
fi

[ -n "$POWER_DBM" ] || die "-p power_dbm is required"

# 음수 dBm 도 유효(저출력 설정) — 선행 '-' 허용. 소수/빈값/비정수는 거부.
if ! [[ "$POWER_DBM" =~ ^-?[0-9]+$ ]]; then
    die "power must be an integer dBm value"
fi

if [ "$CURRENT" -eq 1 ] && [ "$#" -gt 0 ]; then
    die "--current cannot be used with family arguments"
fi

if [ "$CURRENT" -eq 0 ] && [ "$#" -eq 0 ]; then
    usage >&2
    die "family is required: legacy, ht, vht, he, all, or --current"
fi

# Preflight: reset(0xFF) 이전에 대상 유효성을 검증한다. 오타 family 나 미연결
# --current 는 종전엔 reset 이후에야 실패해, 기존 수동 TX 설정을 0xFF 로 지우고
# auto state 를 남긴 뒤 죽었다(진행 중 파워 테스트 무효화). 검증 실패 시 reset 전에 die.
# --current 의 링크 감지는 여기서 1회 수행되고 이후 apply 단계는 캐시를 재사용한다.
if [ "$CURRENT" -eq 1 ]; then
    detect_current_group || die "cannot use --current: interface not associated or unsupported mode"
else
    for family in "$@"; do
        case "$(printf '%s' "$family" | tr '[:upper:]' '[:lower:]')" in
        legacy | ht | vht | he | all) ;;
        *) die "unknown family '$family'. Use legacy, ht, vht, he, or all" ;;
        esac
    done
fi

if [ "$RESET_BEFORE_SET" -eq 1 ]; then
    echo "== reset: firmware automatic TX power control =="
    run_cmd "$MLANUTL" "$IFACE" txpowercfg 0xFF
    record_auto_state
fi

if [ "$CLAMP" -eq 1 ]; then
    load_limits
fi

if [ "$CURRENT" -eq 1 ]; then
    apply_current_group
else
    for family in "$@"; do
        apply_family "$family"
    done
fi

if [ "$CLAMP" -eq 1 ]; then
    run_queued_entries
    echo "== clamp summary =="
    echo "clamped entries: ${CLAMPED_COUNT}"
    echo "skipped entries without parsed limit: ${MISSING_LIMIT_COUNT}"
fi

if [ "$FAILED_COUNT" -gt 0 ]; then
    echo "warning: ${FAILED_COUNT} set command(s) failed" >&2
    exit 1
fi
