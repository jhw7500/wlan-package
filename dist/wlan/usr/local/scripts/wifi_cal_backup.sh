#!/bin/bash
# wifi_cal_backup.sh — 사용자 지정 calibration 파일 백업/복구
#
# Usage:
#   wifi_cal_backup.sh protect       JSON이 선택한 사용자 calibration을 백업/복구
#   wifi_cal_backup.sh mark <file>   `wifi ... cal <file>`로 반입한 파일을 사용자 파일로 표시·백업
#   wifi_cal_backup.sh check <file>  반입 임시파일을 변경 없이 검증
#   wifi_cal_backup.sh reset         선택된 생산 calibration은 복구·보존하고 미선택 표식 제거
set -u

tag=$(basename "$0")
JSON="${WIFI_INIT_CONF_JSON:-/usr/local/etc/wifi_init_conf.json}"
FIRMWARE_ROOT="${WIFI_FIRMWARE_ROOT:-/lib/firmware}"
CTS_ROOT="${WIFI_CTS_ROOT:-${FIRMWARE_ROOT}/cts}"
BASELINE_ROOT="${WIFI_CAL_BASELINE_ROOT:-/opt/wlan/config/wlan}"
LOCK_FILE="${WIFI_CAL_BACKUP_LOCK:-/run/wifi/wifi_cal.backup.lock}"
LOGGER_BIN="${WIFI_CAL_LOGGER:-logger}"
SYNC_BIN="${WIFI_CAL_SYNC_CMD:-sync}"

CTS_ROOT=$(readlink -m -- "$CTS_ROOT")

log_msg() {
    local priority="$1" line="$2"
    shift 2
    "$LOGGER_BIN" -p "$priority" "[$tag:$line] $*" || true
}

# calibration 원본은 공백으로 구분된 1-byte hex 스트림이며, 5~6번째 byte가
# little-endian payload 길이다. 모든 토큰과 선언 길이를 함께 확인해 완전한 hex prefix가
# 정상본을 덮지 못하게 한다.
is_valid_cal() {
    local path="$1"
    [ -s "$path" ] || return 1
    awk '
        function hex_nibble(c) {
            return index("0123456789ABCDEF", toupper(c)) - 1
        }
        function hex_byte(s) {
            return hex_nibble(substr(s, 1, 1)) * 16 + hex_nibble(substr(s, 2, 1))
        }
        {
            gsub(/\r/, "")
            for (i = 1; i <= NF; i++) {
                if (length($i) != 2 || $i !~ /^[0-9A-Fa-f][0-9A-Fa-f]$/) {
                    exit 1
                }
                count++
                if (count == 5) payload_lo = $i
                if (count == 6) payload_hi = $i
            }
        }
        END {
            if (count < 6) exit 1
            declared = hex_byte(payload_lo) + hex_byte(payload_hi) * 256
            if (count != declared + 6) exit 1
        }
    ' "$path" >/dev/null 2>&1
}

atomic_copy() {
    local src="$1" dst="$2" dst_dir tmp
    dst_dir=$(dirname "$dst")
    mkdir -p "$dst_dir" || return 1
    tmp=$(mktemp "${dst}.tmp.XXXXXX") || return 1
    if ! cp -p -- "$src" "$tmp"; then
        rm -f -- "$tmp"
        return 1
    fi
    if ! "$SYNC_BIN" "$tmp" 2>/dev/null && ! "$SYNC_BIN"; then
        rm -f -- "$tmp"
        return 1
    fi
    if ! mv -f -- "$tmp" "$dst"; then
        rm -f -- "$tmp"
        return 1
    fi
    "$SYNC_BIN" "$dst" 2>/dev/null || "$SYNC_BIN" || return 1
    "$SYNC_BIN" "$dst_dir" 2>/dev/null || "$SYNC_BIN" || return 1
}

write_marker() {
    local path="$1" marker="${path}.user-cal" tmp
    tmp=$(mktemp "${marker}.tmp.XXXXXX") || return 1
    printf 'managed-by=wifi_cal_backup.sh\n' > "$tmp"
    if ! "$SYNC_BIN" "$tmp" 2>/dev/null && ! "$SYNC_BIN"; then
        rm -f -- "$tmp"
        return 1
    fi
    if ! mv -f -- "$tmp" "$marker"; then
        rm -f -- "$tmp"
        return 1
    fi
    "$SYNC_BIN" "$marker" 2>/dev/null || "$SYNC_BIN"
}

resolve_cal_path() {
    local value="$1" candidate resolved
    case "$value" in
        ""|none|None) return 1 ;;
        cts/*) candidate="${FIRMWARE_ROOT}/${value}" ;;
        "$CTS_ROOT"/*) candidate="$value" ;;
        /lib/firmware/cts/*|/usr/lib/firmware/cts/*) candidate="$value" ;;
        *) return 1 ;;
    esac
    resolved=$(readlink -m -- "$candidate")
    case "$resolved" in
        "$CTS_ROOT"/*) printf '%s\n' "$resolved" ;;
        *) return 1 ;;
    esac
}

is_package_cal() {
    local rel="$1"
    case "$rel" in
        WlanCalData_ext.conf|WlanCalData_ext_.conf|WlanCalData_ext_RD.conf|\
        WlanCalData_ext_a0.conf|azure/cal_data.conf|config/cal_data.conf)
            return 0
            ;;
        *) return 1 ;;
    esac
}

is_user_cal() {
    local path="$1" rel="${1#"$CTS_ROOT"/}" baseline
    [ -f "${path}.user-cal" ] && return 0
    if ! is_package_cal "$rel"; then
        return 0
    fi

    # /opt에 독립 기본본이 있는 항목은 내용이 다를 때만 사용자 변경으로 승격한다.
    baseline="${BASELINE_ROOT}/${rel}"
    [ -f "$baseline" ] && ! cmp -s -- "$path" "$baseline"
}

protect_one() {
    local path="$1" mode="${2:-protect}" backup="${1}.bak"

    # marker가 있으면 backup이 사용자가 명시적으로 반입한 원본이다. 패키지 업그레이드가
    # 같은 basename의 active를 유효한 기본 파일로 덮어도 protect에서는 backup을 복원한다.
    # 새 사용자 파일로 backup을 교체할 권한은 명시적 mark 경로에만 있다.
    if [ "$mode" = "protect" ] && [ -f "${path}.user-cal" ] && is_valid_cal "$backup"; then
        if ! is_valid_cal "$path" || ! cmp -s -- "$path" "$backup"; then
            log_msg local0.crit "$LINENO" "restoring managed calibration: $backup -> $path"
            atomic_copy "$backup" "$path" || {
                log_msg local0.emerg "$LINENO" "managed calibration recovery failed: $backup -> $path"
                return 1
            }
        fi
        return 0
    fi

    if is_valid_cal "$path"; then
        if ! is_valid_cal "$backup" || ! cmp -s -- "$path" "$backup"; then
            if ! atomic_copy "$path" "$backup"; then
                log_msg local0.err "$LINENO" "calibration backup failed: $path -> $backup"
                return 1
            fi
            log_msg local0.info "$LINENO" "calibration backup committed: $backup"
        fi
        if [ ! -f "${path}.user-cal" ]; then
            write_marker "$path" || {
                log_msg local0.err "$LINENO" "calibration marker write failed: ${path}.user-cal"
                return 1
            }
        fi
        return 0
    fi

    if is_valid_cal "$backup"; then
        log_msg local0.crit "$LINENO" "recovering calibration from backup: $backup -> $path"
        atomic_copy "$backup" "$path" || {
            log_msg local0.emerg "$LINENO" "calibration recovery failed: $backup -> $path"
            return 1
        }
        return 0
    fi

    log_msg local0.emerg "$LINENO" "custom calibration invalid and no valid backup: $path"
    return 1
}

mark_cal() {
    local path
    path=$(resolve_cal_path "${1:-}") || {
        log_msg local0.err "$LINENO" "calibration path must be under $CTS_ROOT: ${1:-}"
        return 1
    }
    if ! is_valid_cal "$path"; then
        log_msg local0.err "$LINENO" "refusing to mark invalid calibration: $path"
        return 1
    fi
    protect_one "$path" mark
}

protect_selected() {
    local value path failed=0
    local -A seen=()

    if ! jq -e 'type == "object"' "$JSON" >/dev/null 2>&1; then
        log_msg local0.err "$LINENO" "cannot read calibration paths from invalid JSON: $JSON"
        return 1
    fi

    while IFS= read -r value; do
        path=$(resolve_cal_path "$value") || {
            case "$value" in
                ""|none|None) ;;
                *) log_msg local0.warn "$LINENO" "calibration path outside managed cts scope; skip backup: $value" ;;
            esac
            continue
        }
        [ -n "${seen[$path]:-}" ] && continue
        seen["$path"]=1

        if is_user_cal "$path"; then
            protect_one "$path" protect || failed=1
        fi
    done < <(jq -r '
        .global.CAL_DATA_CFG // "",
        .mlan0.CAL_DATA_CFG // "",
        .mlan1.CAL_DATA_CFG // ""
    ' "$JSON")

    [ "$failed" -eq 0 ]
}

reset_markers() {
    local marker path selected_path baseline backup failed=0
    local -A selected=()
    local -A handled=()

    [ -d "$CTS_ROOT" ] || return 0

    # Factory Reset이 보존하는 JSON의 CAL 선택과 일치시킨다. 선택된 production CAL은
    # marker+backup까지 유지하며, active가 깨졌다면 backup에서 먼저 복구한다.
    if ! jq -e 'type == "object"' "$JSON" >/dev/null 2>&1; then
        log_msg local0.emerg "$LINENO" "cannot read selected calibration paths during reset: $JSON"
        return 1
    fi
    while IFS= read -r value; do
        selected_path=$(resolve_cal_path "$value") || continue
        selected["$selected_path"]=1
    done < <(jq -r '
        .global.CAL_DATA_CFG // "",
        .mlan0.CAL_DATA_CFG // "",
        .mlan1.CAL_DATA_CFG // ""
    ' "$JSON")

    while IFS= read -r -d '' marker; do
        path="${marker%.user-cal}"
        if [ -n "${selected[$path]:-}" ]; then
            if ! protect_one "$path" protect; then
                log_msg local0.emerg "$LINENO" "selected calibration could not be protected during reset: $path"
                failed=1
            fi
            handled["$path"]=1
            continue
        fi
        if ! rm -f -- "$marker" "${path}.bak" "$path"; then
            log_msg local0.err "$LINENO" "failed to remove calibration backup artifacts: $path"
            failed=1
        fi
    done < <(find "$CTS_ROOT" -type f -name '*.user-cal' -print0)

    # marker 없는 legacy/MFG custom CAL도 선택된 순간 production state다. package
    # baseline 두 경로는 아래 전용 분기가 처리하고, 나머지는 여기서 검증·backup·mark한다.
    for path in "${!selected[@]}"; do
        [ -n "${handled[$path]:-}" ] && continue
        case "${path#"$CTS_ROOT"/}" in
            WlanCalData_ext.conf|WlanCalData_ext_RD.conf) continue ;;
        esac
        if ! protect_one "$path" protect; then
            log_msg local0.emerg "$LINENO" "selected unmarked calibration invalid during reset: $path"
            failed=1
        else
            handled["$path"]=1
        fi
    done

    # package-owned CAL은 marker가 없으면 공장 baseline이 단일 진실원이다. 예전
    # unmarked .bak을 남기면 다음 active 손상 때 그 값이 production CAL로 부활한다.
    for path in "$CTS_ROOT/WlanCalData_ext.conf" "$CTS_ROOT/WlanCalData_ext_RD.conf"; do
        if [ -n "${selected[$path]:-}" ] && [ -f "${path}.user-cal" ]; then
            continue
        fi
        baseline="${BASELINE_ROOT}/$(basename -- "$path")"
        backup="${path}.bak"
        if [ ! -e "$path" ] && [ ! -e "$baseline" ]; then
            continue
        fi
        if ! is_valid_cal "$baseline"; then
            log_msg local0.emerg "$LINENO" "selected package calibration baseline invalid: $baseline"
            failed=1
            continue
        fi
        if ! atomic_copy "$baseline" "$path" || ! atomic_copy "$baseline" "$backup"; then
            log_msg local0.emerg "$LINENO" "failed to seed package calibration and backup: $path"
            failed=1
            continue
        fi
        if ! is_valid_cal "$path" || ! is_valid_cal "$backup" \
           || ! cmp -s -- "$baseline" "$path" || ! cmp -s -- "$baseline" "$backup"; then
            log_msg local0.emerg "$LINENO" "package calibration postcondition failed: $path"
            failed=1
        fi
    done
    if ! "$SYNC_BIN" "$CTS_ROOT" 2>/dev/null && ! "$SYNC_BIN"; then
        failed=1
        log_msg local0.emerg "$LINENO" "calibration reset directory sync failed: $CTS_ROOT"
    fi
    [ "$failed" -eq 0 ]
}

if ! command -v flock >/dev/null 2>&1; then
    log_msg local0.emerg "$LINENO" "flock not available; cannot serialize calibration backup"
    exit 1
fi
mkdir -p "$(dirname "$LOCK_FILE")" || exit 1
exec 9>"$LOCK_FILE" || exit 1
flock -x 9 || exit 1

case "${1:-protect}" in
    protect)
        command -v jq >/dev/null 2>&1 || {
            log_msg local0.emerg "$LINENO" "jq not available; cannot read $JSON"
            exit 1
        }
        protect_selected
        ;;
    mark)
        mark_cal "${2:-}"
        ;;
    check)
        if ! is_valid_cal "${2:-}"; then
            log_msg local0.err "$LINENO" "invalid calibration stream: ${2:-}"
            exit 1
        fi
        ;;
    reset)
        reset_markers
        ;;
    *)
        log_msg local0.err "$LINENO" "usage: $tag <protect|mark <file>|check <file>|reset>"
        exit 64
        ;;
esac
