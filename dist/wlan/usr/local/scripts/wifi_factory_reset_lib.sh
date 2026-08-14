#!/bin/bash
# Factory Reset의 검증·stage·원자 commit·서비스 후조건 공용 함수.
# 이 파일은 source해서 사용하며 자체적으로 시스템 상태를 변경하지 않는다.

: "${FACTORY_LOGGER:=logger}"
: "${FACTORY_SYSTEMCTL:=systemctl}"
: "${FACTORY_BOARD_CONFIG_SH:=/usr/local/scripts/wifi_board_config.sh}"
: "${FACTORY_PRESERVE_SH:=/usr/local/scripts/wifi_conf_preserve.sh}"
: "${FACTORY_LINK_RESET_SH:=/usr/local/scripts/wifi_link_reset.sh}"
: "${FACTORY_CAL_BACKUP_SH:=/usr/local/scripts/wifi_cal_backup.sh}"
: "${FACTORY_CONFIG_BACKUP_SH:=/usr/local/scripts/wifi_config_backup.sh}"
: "${FACTORY_APPLY_ENABLED_SH:=/usr/local/scripts/wifi_apply_enabled.sh}"
: "${FACTORY_INSTALL_CMD:=install}"
: "${FACTORY_MOVE_CMD:=mv}"
: "${FACTORY_SYNC_CMD:=sync}"
: "${FACTORY_REQUIRED_DIR_MODE:=0755}"
FACTORY_FILE_OWNER=${FACTORY_FILE_OWNER-root}
FACTORY_FILE_GROUP=${FACTORY_FILE_GROUP-root}

FACTORY_REQUIRED_UNITS=(
    wifi-stack.target
    wifi_apply_enabled.service
    wifi_init.service
    nginx.service
)
FACTORY_OPTIONAL_UNITS=()

factory_log() {
    local priority="$1"
    shift
    "$FACTORY_LOGGER" -p "$priority" "[wifi_factory_reset:$LINENO] $*" 2>/dev/null || true
}

_factory_template_valid() {
    local path="$1"
    [ -s "$path" ] || return 1
    jq -e '
        type == "object"
        and (.global | type) == "object"
        and (.mcp | type) == "object"
        and (.mlan0 | type) == "object"
        and (.mlan1 | type) == "object"
        and (.mac | type) == "object"
        and (.wbridge | type) == "object"
    ' "$path" >/dev/null 2>&1
}

_factory_active_valid() {
    local path="$1"
    _factory_template_valid "$path" || return 1
    jq -e '
        (.global.BOARD_TYPE | type) == "string"
        and (.global.BOARD_TYPE | length) > 0
        and (.mcp.iio_device | type) == "string"
        and (.mcp.iio_device | length) > 0
    ' "$path" >/dev/null 2>&1
}

_factory_install_0644() {
    local src="$1" dst="$2"
    if [ -n "$FACTORY_FILE_OWNER" ] && [ -n "$FACTORY_FILE_GROUP" ]; then
        install -o "$FACTORY_FILE_OWNER" -g "$FACTORY_FILE_GROUP" -m 0644 -- "$src" "$dst"
    else
        install -m 0644 -- "$src" "$dst"
    fi
}

_factory_payload_mode_valid() {
    case "$1" in
        0600|600|0644|644) return 0 ;;
        *) return 1 ;;
    esac
}

_factory_expected_mode() {
    local mode="$1"
    printf '%s\n' "${mode#0}"
}

_factory_normalize_required_dir() {
    local dir="$1"

    mkdir -p -- "$dir" || return 1
    [ -d "$dir" ] || return 1
    if [ -n "$FACTORY_FILE_OWNER" ] && [ -n "$FACTORY_FILE_GROUP" ]; then
        chown "$FACTORY_FILE_OWNER:$FACTORY_FILE_GROUP" "$dir" || return 1
    fi
    chmod "$FACTORY_REQUIRED_DIR_MODE" "$dir" || return 1
}

_factory_sync_required_path() {
    local path="$1"
    "$FACTORY_SYNC_CMD" "$path" 2>/dev/null || "$FACTORY_SYNC_CMD"
}

_factory_verify_required_file() {
    local src="$1" dst="$2" mode="$3" dst_dir actual expected owner_group

    [ -f "$dst" ] && [ ! -L "$dst" ] && [ -s "$dst" ] || return 1
    cmp -s -- "$src" "$dst" || return 1

    actual=$(stat -c '%a' "$dst" 2>/dev/null) || return 1
    expected=$(_factory_expected_mode "$mode") || return 1
    [ "$actual" = "$expected" ] || return 1

    dst_dir=$(dirname -- "$dst")
    [ -d "$dst_dir" ] || return 1
    actual=$(stat -c '%a' "$dst_dir" 2>/dev/null) || return 1
    expected=$(_factory_expected_mode "$FACTORY_REQUIRED_DIR_MODE") || return 1
    [ "$actual" = "$expected" ] || return 1

    if [ -n "$FACTORY_FILE_OWNER" ] && [ -n "$FACTORY_FILE_GROUP" ]; then
        owner_group=$(stat -c '%U:%G' "$dst" 2>/dev/null) || return 1
        [ "$owner_group" = "$FACTORY_FILE_OWNER:$FACTORY_FILE_GROUP" ] || return 1
        owner_group=$(stat -c '%U:%G' "$dst_dir" 2>/dev/null) || return 1
        [ "$owner_group" = "$FACTORY_FILE_OWNER:$FACTORY_FILE_GROUP" ] || return 1
    fi
}

factory_preflight_required_payloads() {
    local record src dst mode extra dst_dir base probe failed=0

    [ "$#" -gt 0 ] || {
        factory_log local0.emerg "required payload manifest is empty"
        return 1
    }
    local command_var
    for command_var in FACTORY_INSTALL_CMD FACTORY_MOVE_CMD FACTORY_SYNC_CMD; do
        command -v "${!command_var}" >/dev/null 2>&1 || {
            factory_log local0.emerg "required payload command unavailable: ${!command_var}"
            return 1
        }
    done

    for record in "$@"; do
        src=""; dst=""; mode=""; extra=""
        IFS='|' read -r src dst mode extra <<< "$record"
        if [ -z "$src" ] || [ -z "$dst" ] || [ -z "$mode" ] || [ -n "$extra" ] \
           || ! _factory_payload_mode_valid "$mode"; then
            factory_log local0.emerg "invalid required payload manifest entry: $record"
            failed=1
            continue
        fi
        if [ ! -f "$src" ] || [ -L "$src" ] || [ ! -r "$src" ] || [ ! -s "$src" ]; then
            factory_log local0.emerg "required payload source missing, unreadable, or empty: $src"
            failed=1
            continue
        fi

        dst_dir=$(dirname -- "$dst")
        if ! mkdir -p -- "$dst_dir"; then
            factory_log local0.emerg "required payload directory unavailable: $dst_dir"
            failed=1
            continue
        fi
        if [ -e "$dst" ] && [ ! -f "$dst" ] && [ ! -L "$dst" ]; then
            factory_log local0.emerg "required payload destination has unsupported type: $dst"
            failed=1
            continue
        fi

        base=$(basename -- "$dst")
        probe=$(mktemp "$dst_dir/.${base}.factory-preflight.XXXXXX") || {
            factory_log local0.emerg "cannot stage required payload beside destination: $dst"
            failed=1
            continue
        }
        rm -f -- "$probe" || failed=1
    done

    [ "$failed" -eq 0 ]
}

_factory_install_required_file() {
    local src="$1" dst="$2" mode="$3" dst_dir base tmp

    dst_dir=$(dirname -- "$dst")
    base=$(basename -- "$dst")
    _factory_normalize_required_dir "$dst_dir" || return 1
    tmp=$(mktemp "$dst_dir/.${base}.factory.XXXXXX") || return 1

    if [ -n "$FACTORY_FILE_OWNER" ] && [ -n "$FACTORY_FILE_GROUP" ]; then
        if ! "$FACTORY_INSTALL_CMD" -o "$FACTORY_FILE_OWNER" -g "$FACTORY_FILE_GROUP" \
             -m "$mode" -- "$src" "$tmp"; then
            rm -f -- "$tmp"
            return 1
        fi
    elif ! "$FACTORY_INSTALL_CMD" -m "$mode" -- "$src" "$tmp"; then
        rm -f -- "$tmp"
        return 1
    fi

    if ! _factory_verify_required_file "$src" "$tmp" "$mode"; then
        rm -f -- "$tmp"
        return 1
    fi
    if ! _factory_sync_required_path "$tmp"; then
        rm -f -- "$tmp"
        return 1
    fi
    if ! "$FACTORY_MOVE_CMD" -fT -- "$tmp" "$dst"; then
        rm -f -- "$tmp"
        return 1
    fi
    _factory_sync_required_path "$dst" || return 1
    _factory_sync_required_path "$dst_dir" || return 1
    _factory_verify_required_file "$src" "$dst" "$mode"
}

_factory_stage_required_file() {
    local src="$1" dst="$2" mode="$3" staged="$4" dst_dir

    dst_dir=$(dirname -- "$dst")
    _factory_normalize_required_dir "$dst_dir" || return 1
    if [ -n "$FACTORY_FILE_OWNER" ] && [ -n "$FACTORY_FILE_GROUP" ]; then
        "$FACTORY_INSTALL_CMD" -o "$FACTORY_FILE_OWNER" -g "$FACTORY_FILE_GROUP" \
            -m "$mode" -- "$src" "$staged" || return 1
    else
        "$FACTORY_INSTALL_CMD" -m "$mode" -- "$src" "$staged" || return 1
    fi
    _factory_verify_required_file "$src" "$staged" "$mode" || return 1
    _factory_sync_required_path "$staged"
}

_factory_rollback_required_payloads() {
    local entry dst backup original_mode original_uid original_gid original_type dst_dir failed=0 i
    local -a rollback_entries=("$@")

    for ((i=${#rollback_entries[@]} - 1; i >= 0; i--)); do
        IFS='|' read -r dst backup original_mode original_uid original_gid original_type \
            <<< "${rollback_entries[$i]}"
        dst_dir=$(dirname -- "$dst")
        if [ -n "$backup" ]; then
            if ! "$FACTORY_MOVE_CMD" -fT -- "$backup" "$dst"; then
                factory_log local0.emerg "required payload rollback restore failed: $backup -> $dst"
                failed=1
                continue
            fi
            if [ "$original_type" = file ]; then
                chown "$original_uid:$original_gid" "$dst" || failed=1
                chmod "$original_mode" "$dst" || failed=1
                _factory_sync_required_path "$dst" || failed=1
            fi
        elif ! rm -f -- "$dst"; then
            factory_log local0.emerg "required payload rollback remove failed: $dst"
            failed=1
            continue
        fi
        _factory_sync_required_path "$dst_dir" || {
            factory_log local0.emerg "required payload rollback directory sync failed: $dst_dir"
            failed=1
        }
    done
    [ "$failed" -eq 0 ]
}

factory_install_required_payloads() {
    local record src dst mode extra dst_dir base staged backup entry failed=0
    local original_mode original_uid original_gid original_type
    local -a staged_entries=() committed_entries=()

    factory_preflight_required_payloads "$@" || return 1

    # 전체 manifest를 먼저 준비한다. stage 하나라도 실패하면 destination은 하나도
    # 건드리지 않는다.
    for record in "$@"; do
        src=""; dst=""; mode=""; extra=""
        IFS='|' read -r src dst mode extra <<< "$record"
        dst_dir=$(dirname -- "$dst")
        base=$(basename -- "$dst")
        staged=$(mktemp "$dst_dir/.${base}.factory.XXXXXX") || failed=1
        if [ "$failed" -eq 0 ] \
           && _factory_stage_required_file "$src" "$dst" "$mode" "$staged"; then
            staged_entries+=("$src|$dst|$mode|$staged")
        else
            [ -n "${staged:-}" ] && rm -f -- "$staged"
            factory_log local0.emerg "required payload stage failed: $src -> $dst"
            failed=1
            break
        fi
    done
    if [ "$failed" -ne 0 ]; then
        for entry in "${staged_entries[@]}"; do
            IFS='|' read -r _ _ _ staged <<< "$entry"
            rm -f -- "$staged"
        done
        return 1
    fi

    # 기존 destination은 같은 디렉터리에 권한을 제한한 rollback copy로 보존한다.
    # active는 staged rename 시점까지 존재하므로 power-loss에도 missing-file window가 없다.
    for entry in "${staged_entries[@]}"; do
        IFS='|' read -r src dst mode staged <<< "$entry"
        dst_dir=$(dirname -- "$dst")
        base=$(basename -- "$dst")
        backup=""
        original_mode=""; original_uid=""; original_gid=""; original_type="none"
        if [ -e "$dst" ] || [ -L "$dst" ]; then
            backup=$(mktemp "$dst_dir/.${base}.factory-rollback.XXXXXX") || failed=1
            if [ "$failed" -eq 0 ]; then
                if [ -L "$dst" ]; then
                    original_type="link"
                    rm -f -- "$backup" || failed=1
                    [ "$failed" -eq 0 ] && cp -a -- "$dst" "$backup" || failed=1
                elif [ -f "$dst" ]; then
                    original_type="file"
                    original_mode=$(stat -c '%a' "$dst") || failed=1
                    original_uid=$(stat -c '%u' "$dst") || failed=1
                    original_gid=$(stat -c '%g' "$dst") || failed=1
                    if [ -n "$FACTORY_FILE_OWNER" ] && [ -n "$FACTORY_FILE_GROUP" ]; then
                        "$FACTORY_INSTALL_CMD" -o "$FACTORY_FILE_OWNER" -g "$FACTORY_FILE_GROUP" \
                            -m 0600 -- "$dst" "$backup" || failed=1
                    else
                        "$FACTORY_INSTALL_CMD" -m 0600 -- "$dst" "$backup" || failed=1
                    fi
                else
                    failed=1
                fi
                if [ "$failed" -eq 0 ]; then
                    if [ "$original_type" = link ]; then
                        _factory_sync_required_path "$dst_dir" || failed=1
                    else
                        _factory_sync_required_path "$backup" || failed=1
                    fi
                fi
            fi
        fi
        if [ "$failed" -eq 0 ] && "$FACTORY_MOVE_CMD" -fT -- "$staged" "$dst"; then
            committed_entries+=("$dst|$backup|$original_mode|$original_uid|$original_gid|$original_type")
        else
            factory_log local0.emerg "required payload commit failed: $src -> $dst"
            failed=1
            break
        fi
    done

    if [ "$failed" -ne 0 ]; then
        _factory_rollback_required_payloads "${committed_entries[@]}" || true
        if [ -n "$backup" ] && ! rm -f -- "$backup"; then
            factory_log local0.emerg "required payload rollback cleanup failed: $backup"
        fi
        for entry in "${staged_entries[@]}"; do
            IFS='|' read -r _ _ _ staged <<< "$entry"
            rm -f -- "$staged"
        done
        return 1
    fi

    for entry in "${committed_entries[@]}"; do
        IFS='|' read -r dst backup _ _ _ _ <<< "$entry"
        dst_dir=$(dirname -- "$dst")
        _factory_sync_required_path "$dst" || failed=1
        _factory_sync_required_path "$dst_dir" || failed=1
    done
    factory_verify_required_payloads "$@" || failed=1
    if [ "$failed" -ne 0 ]; then
        _factory_rollback_required_payloads "${committed_entries[@]}" || true
        return 1
    fi

    for entry in "${committed_entries[@]}"; do
        IFS='|' read -r dst backup _ _ _ _ <<< "$entry"
        if [ -n "$backup" ] && ! rm -f -- "$backup"; then
            factory_log local0.emerg "required payload rollback cleanup failed: $backup"
            return 1
        fi
        _factory_sync_required_path "$(dirname -- "$dst")" || return 1
    done
}

factory_verify_required_payloads() {
    local record src dst mode extra failed=0

    [ "$#" -gt 0 ] || return 1
    for record in "$@"; do
        src=""; dst=""; mode=""; extra=""
        IFS='|' read -r src dst mode extra <<< "$record"
        if [ -z "$src" ] || [ -z "$dst" ] || [ -z "$mode" ] || [ -n "$extra" ] \
           || ! _factory_payload_mode_valid "$mode" \
           || ! _factory_verify_required_file "$src" "$dst" "$mode"; then
            factory_log local0.emerg "required payload postcondition failed: $src -> $dst"
            failed=1
        fi
    done
    [ "$failed" -eq 0 ]
}

factory_preflight() {
    local template="$1" active_dir="$2" helper unit probe

    command -v jq >/dev/null 2>&1 || {
        factory_log local0.emerg "jq missing"
        return 1
    }
    _factory_template_valid "$template" || {
        factory_log local0.emerg "invalid factory template: $template"
        return 1
    }

    for helper in "$FACTORY_BOARD_CONFIG_SH" "$FACTORY_LINK_RESET_SH" \
                  "$FACTORY_CAL_BACKUP_SH" \
                  "$FACTORY_CONFIG_BACKUP_SH" "$FACTORY_APPLY_ENABLED_SH"; do
        [ -x "$helper" ] || {
            factory_log local0.emerg "required helper missing or not executable: $helper"
            return 1
        }
    done

    [ -x "$FACTORY_SYSTEMCTL" ] || command -v "$FACTORY_SYSTEMCTL" >/dev/null 2>&1 || {
        factory_log local0.emerg "systemctl unavailable: $FACTORY_SYSTEMCTL"
        return 1
    }
    for unit in "${FACTORY_REQUIRED_UNITS[@]}"; do
        "$FACTORY_SYSTEMCTL" cat "$unit" >/dev/null 2>&1 || {
            factory_log local0.emerg "required base-image/package unit missing: $unit"
            return 1
        }
    done
    for unit in "${FACTORY_OPTIONAL_UNITS[@]}"; do
        "$FACTORY_SYSTEMCTL" cat "$unit" >/dev/null 2>&1 \
            || factory_log local0.warn "optional service unavailable; reset continues: $unit"
    done

    mkdir -p -- "$active_dir" || return 1
    probe=$(mktemp "$active_dir/.factory-preflight.XXXXXX") || {
        factory_log local0.emerg "cannot create same-filesystem stage in $active_dir"
        return 1
    }
    rm -f -- "$probe"
}

factory_stage_config() {
    local template="$1" staged="$2" snapshot="${3:-}" candidate

    rm -f -- "$staged" "${staged}.preserve"
    _factory_install_0644 "$template" "$staged" || return 1
    if ! "$FACTORY_BOARD_CONFIG_SH" "$staged"; then
        factory_log local0.emerg "board config failed for staged config: $staged"
        rm -f -- "$staged"
        return 1
    fi
    _factory_active_valid "$staged" || {
        factory_log local0.emerg "staged config lacks required board facts: $staged"
        rm -f -- "$staged"
        return 1
    }

    # Preserve 복원은 별도 candidate에 적용한다. helper가 중간 파일을 훼손한 뒤 실패해도
    # 이미 검증된 template+board stage를 그대로 사용할 수 있어야 한다.
    if [ -n "$snapshot" ] && [ -s "$snapshot" ] && [ -x "$FACTORY_PRESERVE_SH" ]; then
        candidate="${staged}.preserve"
        if _factory_install_0644 "$staged" "$candidate" \
           && WIFI_INIT_CONF_JSON="$candidate" "$FACTORY_PRESERVE_SH" apply "$snapshot" \
           && _factory_active_valid "$candidate"; then
            mv -f -- "$candidate" "$staged" || return 1
        else
            rm -f -- "$candidate"
            factory_log local0.warn "preserved values not applied; using template+board stage"
        fi
    fi

    chmod 0644 "$staged" || return 1
    sync "$staged" 2>/dev/null || sync
    _factory_active_valid "$staged"
}

factory_commit_config() {
    local staged="$1" active="$2" active_dir tmp
    _factory_active_valid "$staged" || return 1
    active_dir=$(dirname "$active")
    tmp=$(mktemp "$active_dir/.wifi_init_conf.factory.XXXXXX") || return 1

    if ! _factory_install_0644 "$staged" "$tmp" || ! _factory_active_valid "$tmp"; then
        rm -f -- "$tmp"
        return 1
    fi
    sync "$tmp" 2>/dev/null || sync
    if ! mv -f -- "$tmp" "$active"; then
        rm -f -- "$tmp"
        return 1
    fi
    sync "$active" 2>/dev/null || sync
    sync "$active_dir" 2>/dev/null || sync
}

factory_restore_service_state() {
    local active="$1" unit failed=0
    _factory_active_valid "$active" || return 1

    "$FACTORY_SYSTEMCTL" daemon-reload >/dev/null 2>&1 || failed=1
    for unit in "${FACTORY_REQUIRED_UNITS[@]}"; do
        "$FACTORY_SYSTEMCTL" enable "$unit" >/dev/null 2>&1 || failed=1
    done
    for unit in "${FACTORY_OPTIONAL_UNITS[@]}"; do
        if "$FACTORY_SYSTEMCTL" cat "$unit" >/dev/null 2>&1; then
            "$FACTORY_SYSTEMCTL" enable "$unit" >/dev/null 2>&1 || failed=1
        else
            factory_log local0.warn "optional service unavailable; enable skipped: $unit"
        fi
    done
    if ! WIFI_INIT_CONF_JSON="$active" WIFI_APPLY_STRICT=1 \
         "$FACTORY_APPLY_ENABLED_SH"; then
        failed=1
    fi
    for unit in "${FACTORY_REQUIRED_UNITS[@]}"; do
        "$FACTORY_SYSTEMCTL" is-enabled --quiet "$unit" >/dev/null 2>&1 || failed=1
    done
    for unit in "${FACTORY_OPTIONAL_UNITS[@]}"; do
        if "$FACTORY_SYSTEMCTL" cat "$unit" >/dev/null 2>&1; then
            "$FACTORY_SYSTEMCTL" is-enabled --quiet "$unit" >/dev/null 2>&1 || failed=1
        fi
    done
    [ "$failed" -eq 0 ] || {
        factory_log local0.emerg "required service state restore failed"
        return 1
    }
}

factory_verify_postconditions() {
    local active="$1" unit owner_group mode
    _factory_active_valid "$active" || return 1

    mode=$(stat -c '%a' "$active" 2>/dev/null) || return 1
    [ "$mode" = 644 ] || return 1
    if [ -n "$FACTORY_FILE_OWNER" ] && [ -n "$FACTORY_FILE_GROUP" ]; then
        owner_group=$(stat -c '%U:%G' "$active" 2>/dev/null) || return 1
        [ "$owner_group" = "$FACTORY_FILE_OWNER:$FACTORY_FILE_GROUP" ] || return 1
    fi

    "$FACTORY_LINK_RESET_SH" --check >/dev/null 2>&1 || return 1
    for unit in "${FACTORY_REQUIRED_UNITS[@]}"; do
        "$FACTORY_SYSTEMCTL" is-enabled --quiet "$unit" >/dev/null 2>&1 || return 1
    done
    for unit in "${FACTORY_OPTIONAL_UNITS[@]}"; do
        if "$FACTORY_SYSTEMCTL" cat "$unit" >/dev/null 2>&1; then
            "$FACTORY_SYSTEMCTL" is-enabled --quiet "$unit" >/dev/null 2>&1 || return 1
        fi
    done
}
