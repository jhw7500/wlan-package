#!/bin/bash
# Factory Reset의 검증·stage·원자 commit·서비스 후조건 공용 함수.
# 이 파일은 source해서 사용하며 자체적으로 시스템 상태를 변경하지 않는다.

: "${FACTORY_LOGGER:=logger}"
: "${FACTORY_SYSTEMCTL:=systemctl}"
: "${FACTORY_BOARD_CONFIG_SH:=/usr/local/scripts/wifi_board_config.sh}"
: "${FACTORY_PRESERVE_SH:=/usr/local/scripts/wifi_conf_preserve.sh}"
: "${FACTORY_LINK_RESET_SH:=/usr/local/scripts/wifi_link_reset.sh}"
: "${FACTORY_CAL_BACKUP_SH:=/usr/local/scripts/wifi_cal_backup.sh}"
: "${FACTORY_APPLY_ENABLED_SH:=/usr/local/scripts/wifi_apply_enabled.sh}"
: "${FACTORY_TOPOLOGY_HELPER:=/usr/local/scripts/wifi_factory_topology.py}"
: "${FACTORY_TOPOLOGY_FILE:=/usr/local/etc/wifi_factory_topology.json}"
: "${FACTORY_TOPOLOGY_OWNER_UID:=0}"
: "${FACTORY_TOPOLOGY_OWNER_GID:=0}"
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
)
FACTORY_OPTIONAL_UNITS=()
FACTORY_TRANSACTION_ACTIVE=0
FACTORY_TRANSACTION_RECORDS=()
FACTORY_TRANSACTION_ENTRIES=()
FACTORY_TRANSACTION_CLEANUP_DEBT=()

factory_log() {
    local priority="$1"
    shift
    "$FACTORY_LOGGER" -p "$priority" "[wifi_factory_reset:$LINENO] $*" 2>/dev/null || true
}

factory_read_topology() {
    local topology

    [ -x "$FACTORY_TOPOLOGY_HELPER" ] || {
        factory_log local0.emerg "factory topology helper unavailable: $FACTORY_TOPOLOGY_HELPER"
        return 1
    }
    topology=$("$FACTORY_TOPOLOGY_HELPER" read \
        --path "$FACTORY_TOPOLOGY_FILE" \
        --owner-uid "$FACTORY_TOPOLOGY_OWNER_UID" \
        --owner-gid "$FACTORY_TOPOLOGY_OWNER_GID") || {
        factory_log local0.emerg "factory topology selector invalid or unsafe: $FACTORY_TOPOLOGY_FILE"
        return 1
    }
    case "$topology" in
        eth0-ip|peer-route) printf '%s\n' "$topology" ;;
        *)
            factory_log local0.emerg "factory topology helper returned invalid profile: $topology"
            return 1
            ;;
    esac
}

factory_snapshot_topology() {
    local snapshot normalized

    [ -x "$FACTORY_TOPOLOGY_HELPER" ] || {
        factory_log local0.emerg "factory topology helper unavailable: $FACTORY_TOPOLOGY_HELPER"
        return 1
    }
    snapshot=$("$FACTORY_TOPOLOGY_HELPER" snapshot \
        --path "$FACTORY_TOPOLOGY_FILE" \
        --owner-uid "$FACTORY_TOPOLOGY_OWNER_UID" \
        --owner-gid "$FACTORY_TOPOLOGY_OWNER_GID") || {
        factory_log local0.emerg "factory topology snapshot failed: $FACTORY_TOPOLOGY_FILE"
        return 1
    }
    normalized=$(jq -ce '
        if type == "object"
           and (keys | sort) == ["generation", "topology"]
           and (.topology | type) == "string"
           and (.generation | type) == "string"
           and (.generation == "absent"
                or (.generation | test("^v1-[0-9a-f]{64}$")))
        then {topology: .topology, generation: .generation}
        else error("invalid factory topology snapshot")
        end
    ' <<< "$snapshot" 2>/dev/null) || {
        factory_log local0.emerg "factory topology helper returned invalid snapshot"
        return 1
    }
    case "$(jq -r '.topology' <<< "$normalized")" in
        eth0-ip|peer-route) printf '%s\n' "$normalized" ;;
        *)
            factory_log local0.emerg "factory topology snapshot has invalid profile"
            return 1
            ;;
    esac
}

factory_topology_snapshot_matches() {
    local expected="$1" current

    current=$(factory_snapshot_topology) || return 1
    if [ "$current" != "$expected" ]; then
        factory_log local0.emerg "factory topology selector changed during reset"
        return 1
    fi
}

_factory_apply_topology_profile() {
    local staged="$1" topology="$2" candidate

    case "$topology" in
        eth0-ip|peer-route) ;;
        *) return 1 ;;
    esac
    candidate="${staged}.topology"
    rm -f -- "$candidate"
    if ! jq --arg topology "$topology" '
        if $topology == "eth0-ip" then
            .wbridge.peer_route.enabled = false
            | .wbridge.ip_discovery = false
            | .wbridge.arp_ignore_always.enabled = true
            | .wbridge.moal.local_hairpin = ""
            | .wbridge.eth_fallback.enabled = false
        else
            .wbridge.peer_route.enabled = true
            | .wbridge.ip_discovery = true
            | .wbridge.arp_ignore_always.enabled = false
            | .wbridge.moal.local_hairpin = ""
            | .wbridge.eth_fallback.enabled = false
        end
    ' "$staged" > "$candidate"; then
        rm -f -- "$candidate"
        return 1
    fi
    _factory_install_0644 "$candidate" "$staged" || {
        rm -f -- "$candidate"
        return 1
    }
    rm -f -- "$candidate"
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
    "$FACTORY_SYNC_CMD" "$path" 2>/dev/null
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

_factory_restore_required_payload() {
    local dst="$1" backup="$2" original_mode="$3" original_uid="$4" original_gid="$5"
    local original_type="$6" dst_dir base restore

    dst_dir=$(dirname -- "$dst")
    if [ -z "$backup" ]; then
        rm -f -- "$dst" || return 1
        _factory_sync_required_path "$dst_dir"
        return
    fi
    [ -f "$backup" ] || [ -L "$backup" ] || return 1
    base=$(basename -- "$dst")
    restore=$(mktemp "$dst_dir/.${base}.factory-restore.XXXXXX") || return 1
    if [ "$original_type" = link ]; then
        rm -f -- "$restore" || return 1
        if ! cp -a -- "$backup" "$restore"; then
            rm -f -- "$restore"
            return 1
        fi
        _factory_sync_required_path "$dst_dir" || {
            rm -f -- "$restore"
            return 1
        }
    elif [ "$original_type" = file ]; then
        if ! "$FACTORY_INSTALL_CMD" -m "$original_mode" -- "$backup" "$restore" \
           || ! chown "$original_uid:$original_gid" "$restore" \
           || ! chmod "$original_mode" "$restore" \
           || ! _factory_sync_required_path "$restore"; then
            rm -f -- "$restore"
            return 1
        fi
    else
        rm -f -- "$restore"
        return 1
    fi
    if ! "$FACTORY_MOVE_CMD" -fT -- "$restore" "$dst"; then
        rm -f -- "$restore"
        return 1
    fi
    if [ "$original_type" = file ]; then
        _factory_sync_required_path "$dst" || return 1
    fi
    _factory_sync_required_path "$dst_dir"
}

_factory_cleanup_transaction_backups() {
    local entry dst backup debt
    local -a cleanup_entries=("$@")

    FACTORY_TRANSACTION_CLEANUP_DEBT=()
    for entry in "${cleanup_entries[@]}"; do
        IFS='|' read -r dst backup _ _ _ _ <<< "$entry"
        [ -n "$backup" ] || continue
        if [ -e "$backup" ] || [ -L "$backup" ]; then
            if ! rm -f -- "$backup"; then
                debt="backup:$backup"
                FACTORY_TRANSACTION_CLEANUP_DEBT+=("$debt")
                factory_log local0.warn "factory transaction cleanup debt retained: $backup"
                continue
            fi
        fi
        if ! _factory_sync_required_path "$(dirname -- "$dst")"; then
            debt="directory-sync:$(dirname -- "$dst")"
            FACTORY_TRANSACTION_CLEANUP_DEBT+=("$debt")
            factory_log local0.warn "factory transaction cleanup directory sync debt: $(dirname -- "$dst")"
        fi
    done
}

_factory_rollback_required_payloads() {
    local entry dst backup original_mode original_uid original_gid original_type failed=0 i
    local -a rollback_entries=("$@")

    for ((i=${#rollback_entries[@]} - 1; i >= 0; i--)); do
        IFS='|' read -r dst backup original_mode original_uid original_gid original_type \
            <<< "${rollback_entries[$i]}"
        if ! _factory_restore_required_payload "$dst" "$backup" "$original_mode" \
            "$original_uid" "$original_gid" "$original_type"; then
            factory_log local0.emerg "required payload rollback restore failed: $backup -> $dst"
            failed=1
        fi
    done
    [ "$failed" -eq 0 ]
}

factory_begin_required_payloads() {
    local record src dst mode extra dst_dir base staged backup entry failed=0
    local original_mode original_uid original_gid original_type
    local -a staged_entries=()

    [ "$FACTORY_TRANSACTION_ACTIVE" -eq 0 ] || {
        factory_log local0.emerg "required payload transaction already active"
        return 1
    }
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

    # 첫 destination rename 전에 복구 상태를 공개한다. EXIT cleanup은 commit loop 어느
    # 지점에서 중단돼도 이미 등록된 항목을 복원할 수 있다.
    FACTORY_TRANSACTION_RECORDS=("$@")
    FACTORY_TRANSACTION_ENTRIES=()
    FACTORY_TRANSACTION_CLEANUP_DEBT=()
    FACTORY_TRANSACTION_ACTIVE=1

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
        if [ "$failed" -eq 0 ]; then
            FACTORY_TRANSACTION_ENTRIES+=(
                "$dst|$backup|$original_mode|$original_uid|$original_gid|$original_type"
            )
        fi
        if [ "$failed" -ne 0 ] || ! "$FACTORY_MOVE_CMD" -fT -- "$staged" "$dst"; then
            factory_log local0.emerg "required payload commit failed: $src -> $dst"
            failed=1
            break
        fi
    done

    if [ "$failed" -ne 0 ]; then
        if ! factory_rollback_required_payloads; then
            factory_log local0.emerg "CRITICAL: required payload rollback failed during publish"
        elif [ -n "$backup" ] && [ "$FACTORY_TRANSACTION_ACTIVE" -eq 0 ] \
             && { [ -e "$backup" ] || [ -L "$backup" ]; } \
             && ! rm -f -- "$backup"; then
            factory_log local0.emerg "required payload rollback cleanup failed: $backup"
        fi
        for entry in "${staged_entries[@]}"; do
            IFS='|' read -r _ _ _ staged <<< "$entry"
            rm -f -- "$staged"
        done
        return 1
    fi

    for entry in "${FACTORY_TRANSACTION_ENTRIES[@]}"; do
        IFS='|' read -r dst backup _ _ _ _ <<< "$entry"
        dst_dir=$(dirname -- "$dst")
        _factory_sync_required_path "$dst" || failed=1
        _factory_sync_required_path "$dst_dir" || failed=1
    done
    factory_verify_required_payloads "$@" || failed=1
    if [ "$failed" -ne 0 ]; then
        if ! factory_rollback_required_payloads; then
            factory_log local0.emerg "CRITICAL: required payload rollback failed after post-commit verification"
        fi
        return 1
    fi
}

factory_rollback_required_payloads() {
    local -a restored_entries

    [ "$FACTORY_TRANSACTION_ACTIVE" -eq 1 ] || {
        factory_log local0.emerg "no required payload transaction to roll back"
        return 1
    }
    if ! _factory_rollback_required_payloads "${FACTORY_TRANSACTION_ENTRIES[@]}"; then
        factory_log local0.emerg "required payload transaction rollback incomplete"
        return 1
    fi
    restored_entries=("${FACTORY_TRANSACTION_ENTRIES[@]}")
    FACTORY_TRANSACTION_RECORDS=()
    FACTORY_TRANSACTION_ENTRIES=()
    FACTORY_TRANSACTION_ACTIVE=0
    _factory_cleanup_transaction_backups "${restored_entries[@]}"
}

factory_commit_required_payloads() {
    local expected_topology_snapshot="${1:-}"
    local -a committed_entries

    [ "$FACTORY_TRANSACTION_ACTIVE" -eq 1 ] || {
        factory_log local0.emerg "no required payload transaction to commit"
        return 1
    }
    factory_verify_required_payloads "${FACTORY_TRANSACTION_RECORDS[@]}" || return 1
    if [ -n "$expected_topology_snapshot" ] \
       && ! factory_topology_snapshot_matches "$expected_topology_snapshot"; then
        return 1
    fi
    # 후조건을 통과한 시점이 transaction의 선형화 지점이다. 이후 private rollback
    # backup 정리는 best-effort이며 실패해도 검증된 factory 상태를 다시 뒤집지 않는다.
    committed_entries=("${FACTORY_TRANSACTION_ENTRIES[@]}")
    FACTORY_TRANSACTION_RECORDS=()
    FACTORY_TRANSACTION_ENTRIES=()
    FACTORY_TRANSACTION_ACTIVE=0
    _factory_cleanup_transaction_backups "${committed_entries[@]}"
}

factory_install_required_payloads() {
    factory_begin_required_payloads "$@" || return 1
    factory_commit_required_payloads
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
                  "$FACTORY_APPLY_ENABLED_SH" \
                  "$FACTORY_TOPOLOGY_HELPER"; do
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
    local template="$1" staged="$2" snapshot="${3:-}" topology="${4:-eth0-ip}" candidate

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

    if ! _factory_apply_topology_profile "$staged" "$topology"; then
        factory_log local0.emerg "factory topology profile rejected: $topology"
        rm -f -- "$staged"
        return 1
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

# OPC 저장값(비밀번호·IP 주소 리스트·무선 설정)을 지워 opcd 가 다음 기동에 초기값(초기 비밀번호,
# 빈 IP 주소 리스트, 미설정 무선 설정)으로 시작하게 한다(#383). IP 주소 리스트는 장치 사본과 기록
# (iplist.txt·iplist.user.last), 0.7.4 이전 바이너리와 그 변환 흔적(iplist.cfg·iplist.cfg.migrated),
# 둘째 인자로 받은 고객 홈의 파일(iplist.txt·iplist.txt.rejected)까지 지운다 — 고객 파일이 남으면
# opcd 가 다음 기동에 그 리스트를 다시 적용한다(wlan-opc #194). 다음 기동 때 opcd 는 빈 리스트로
# 시작하고 빈 고객 파일(0 B)을 만든다. 생산 정보(device_info.json)와 데몬 설정(opc.conf), 고객 홈의
# 다른 파일은 남긴다. opcd 는 멈추지 않는다 — 재부팅 종료 절차에서 Reset Cause 0x40 을 통지해야
# 하고, 서비스만 멈추면 통지하지 않는다. 지우지 못해도 공장 초기화는 계속한다(경고만).
factory_reset_clear_opc_state() {
    local dir="$1" home="${2:-}" name path
    local -a paths=()
    for name in password iplist.cfg iplist.cfg.migrated iplist.txt iplist.user.last radio.conf; do
        paths+=("$dir/$name")
    done
    [ -z "$home" ] || paths+=("$home/iplist.txt" "$home/iplist.txt.rejected")
    for path in "${paths[@]}"; do
        [ -e "$path" ] || [ -L "$path" ] || continue
        if rm -f -- "$path" 2>/dev/null && [ ! -e "$path" ]; then
            factory_log local0.info "removed OPC stored value $path"
        else
            factory_log local0.warn "could not remove OPC stored value $path (previous value stays)"
        fi
    done
    return 0
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
