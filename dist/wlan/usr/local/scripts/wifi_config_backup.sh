#!/bin/bash
# wifi_config_backup.sh — wifi_init_conf.json 전용 정상본 백업/복구
#
# Usage:
#   wifi_config_backup.sh restore  부팅 전 active 검증, 손상 시 .bak → .bak.1 → default 순 복구
#   wifi_config_backup.sh commit   Wi-Fi 초기화 성공 후 active를 최신 정상본으로 확정
#   wifi_config_backup.sh reset    공장 초기화된 active로 기존 정상본을 교체
#
# 단순 문자열 패턴 대신 JSON 구문과 필수 최상위 객체를 함께 검증한다. commit은 동일한
# 내용이면 회전하지 않으며, 최신 .bak과 그 이전 .bak.1 두 세대를 유지한다.
set -u

tag=$(basename "$0")
ACTIVE="${WIFI_INIT_CONF_JSON:-/usr/local/etc/wifi_init_conf.json}"
DEFAULT="${WIFI_INIT_CONF_DEFAULT:-/opt/wlan/config/wifi_init_conf.json}"
BACKUP="${WIFI_INIT_CONF_BACKUP:-${ACTIVE}.bak}"
PREVIOUS="${WIFI_INIT_CONF_PREVIOUS:-${ACTIVE}.bak.1}"
LOCK_FILE="${WIFI_INIT_CONF_LOCK:-/run/wifi/wifi_init_conf.backup.lock}"
BOARD_CONFIG_SH="${WIFI_BOARD_CONFIG_SH:-/usr/local/scripts/wifi_board_config.sh}"
LOGGER_BIN="${WIFI_CONFIG_LOGGER:-logger}"

log_msg() {
    local priority="$1" line="$2"
    shift 2
    "$LOGGER_BIN" -p "$priority" "[$tag:$line] $*" || true
}

is_valid_json() {
    local path="$1"
    [ -s "$path" ] || return 1
    jq -e '
        type == "object"
        and (.global | type) == "object"
        and (.mlan0 | type) == "object"
        and (.mlan1 | type) == "object"
        and (.mac | type) == "object"
        and (.wbridge | type) == "object"
    ' "$path" >/dev/null 2>&1
}

# 같은 디렉터리에 임시 파일을 쓴 뒤 rename하여 active/.bak이 중간 상태로 보이지 않게 한다.
atomic_copy() {
    local src="$1" dst="$2" dst_dir tmp
    dst_dir=$(dirname "$dst")
    mkdir -p "$dst_dir" || return 1
    tmp=$(mktemp "${dst}.tmp.XXXXXX") || return 1

    if ! cp -p -- "$src" "$tmp"; then
        rm -f -- "$tmp"
        return 1
    fi
    sync "$tmp" 2>/dev/null || sync
    if ! mv -f -- "$tmp" "$dst"; then
        rm -f -- "$tmp"
        return 1
    fi
    sync "$dst" 2>/dev/null || sync
    sync "$dst_dir" 2>/dev/null || true
}

apply_board_config() {
    if [ ! -x "$BOARD_CONFIG_SH" ]; then
        log_msg local0.emerg "$LINENO" "missing board config helper after JSON recovery: $BOARD_CONFIG_SH"
        return 1
    fi
    if ! "$BOARD_CONFIG_SH" "$ACTIVE"; then
        log_msg local0.emerg "$LINENO" "board config apply failed after JSON recovery: $ACTIVE"
        return 1
    fi
    if ! is_valid_json "$ACTIVE"; then
        log_msg local0.emerg "$LINENO" "recovered JSON invalid after board config: $ACTIVE"
        return 1
    fi
}

restore_from() {
    local src="$1" label="$2"
    is_valid_json "$src" || return 1

    log_msg local0.crit "$LINENO" "recovering $ACTIVE from $label: $src"
    if ! atomic_copy "$src" "$ACTIVE"; then
        log_msg local0.emerg "$LINENO" "JSON recovery write failed: $src -> $ACTIVE"
        return 1
    fi
    apply_board_config
}

commit_active() {
    if ! is_valid_json "$ACTIVE"; then
        log_msg local0.err "$LINENO" "refusing to commit invalid JSON: $ACTIVE"
        return 1
    fi

    if is_valid_json "$BACKUP" && cmp -s -- "$ACTIVE" "$BACKUP"; then
        log_msg local0.info "$LINENO" "JSON backup unchanged: $BACKUP"
        return 0
    fi

    if is_valid_json "$BACKUP"; then
        if ! atomic_copy "$BACKUP" "$PREVIOUS"; then
            log_msg local0.err "$LINENO" "failed to rotate JSON backup: $BACKUP -> $PREVIOUS"
            return 1
        fi
    fi
    if ! atomic_copy "$ACTIVE" "$BACKUP"; then
        log_msg local0.err "$LINENO" "failed to commit JSON backup: $ACTIVE -> $BACKUP"
        return 1
    fi
    log_msg local0.info "$LINENO" "JSON backup committed: $BACKUP"
}

if ! command -v jq >/dev/null 2>&1; then
    log_msg local0.emerg "$LINENO" "jq not available; cannot validate $ACTIVE"
    exit 1
fi
if ! command -v flock >/dev/null 2>&1; then
    log_msg local0.emerg "$LINENO" "flock not available; cannot serialize JSON backup"
    exit 1
fi

mkdir -p "$(dirname "$LOCK_FILE")" || {
    log_msg local0.err "$LINENO" "failed to create lock directory: $(dirname "$LOCK_FILE")"
    exit 1
}
exec 9>"$LOCK_FILE" || {
    log_msg local0.err "$LINENO" "failed to open lock: $LOCK_FILE"
    exit 1
}
flock -x 9 || {
    log_msg local0.err "$LINENO" "failed to acquire lock: $LOCK_FILE"
    exit 1
}

case "${1:-restore}" in
    restore)
        if is_valid_json "$ACTIVE"; then
            exit 0
        fi
        log_msg local0.crit "$LINENO" "active JSON invalid; starting recovery: $ACTIVE"
        restore_from "$BACKUP" ".bak" && exit 0
        restore_from "$PREVIOUS" ".bak.1" && exit 0
        restore_from "$DEFAULT" "default" && exit 0
        log_msg local0.emerg "$LINENO" "cannot recover $ACTIVE (.bak/.bak.1/default invalid or unavailable)"
        exit 2
        ;;
    commit)
        commit_active
        ;;
    reset)
        if ! is_valid_json "$ACTIVE"; then
            log_msg local0.err "$LINENO" "refusing to reset backups from invalid JSON: $ACTIVE"
            exit 1
        fi
        rm -f -- "$BACKUP" "$PREVIOUS" "${BACKUP}.tmp."* "${PREVIOUS}.tmp."*
        commit_active
        ;;
    *)
        log_msg local0.err "$LINENO" "usage: $tag <restore|commit|reset>"
        exit 64
        ;;
esac
