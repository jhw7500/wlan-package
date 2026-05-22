#!/bin/bash
# backup_file.sh — 검증 기반 self-healing 백업/복구
#
# Usage: backup_file.sh <file_path> <pattern> [default_src]
#
# 검증 기준 (is_valid):
#   - 파일이 존재하고 size>0 (-s)
#   - 파일이 패턴(grep -E)을 포함
#
# 동작 요약:
#   1) 대상 정상         → .bak 갱신 + sync (백업)
#   2) 대상 손상 + .bak 정상   → .bak → 대상 (1차 복구)
#   3) 대상 손상 + .bak 손상 + default 정상 → default → 대상, default → .bak (2차 복구)
#   4) 모두 손상         → emergency 로그, exit 2 (복구 불가)
#
# 모든 cp 직후 per-file `sync`로 ext4 power-cut 가드.

tag=$(basename "$0")

file_path="${1:-}"
pattern="${2:-}"
default_src="${3:-}"

if [ -z "$file_path" ] || [ -z "$pattern" ]; then
    logger -p local0.err "[$tag:$LINENO] usage: $tag <file_path> <pattern> [default_src]"
    exit 64
fi

backup_file="${file_path}.bak"

# size>0 + pattern 검증
is_valid() {
    local p="$1"
    [ -s "$p" ] || return 1
    grep -qE "$pattern" "$p" 2>/dev/null
}

# cp + per-file sync (ext4 power-cut 가드)
safe_copy() {
    local src="$1" dst="$2"
    cp "$src" "$dst" || return 1
    sync "$dst" 2>/dev/null || sync
}

# 1) 대상 정상 → .bak 갱신
if is_valid "$file_path"; then
    #logger -p local0.info "[$tag:$LINENO] backup to file : $backup_file"
    safe_copy "$file_path" "$backup_file" \
        || logger -p local0.err "[$tag:$LINENO] backup write failed : $backup_file"
    exit 0
fi

logger -p local0.crit "[$tag:$LINENO] target invalid (empty or pattern missing) : $file_path"

# 2) .bak으로 복구
if is_valid "$backup_file"; then
    logger -p local0.crit "[$tag:$LINENO] recovery from .bak : $backup_file -> $file_path"
    if safe_copy "$backup_file" "$file_path"; then
        exit 0
    fi
    logger -p local0.emerg "[$tag:$LINENO] .bak recovery write failed : $file_path"
fi

# 3) default 원본으로 복구 (지정된 경우)
if [ -n "$default_src" ] && is_valid "$default_src"; then
    logger -p local0.emerg "[$tag:$LINENO] recovery from DEFAULT : $default_src -> $file_path"
    if safe_copy "$default_src" "$file_path"; then
        # .bak도 default로 재초기화하여 다음 부팅 self-healing 보장
        safe_copy "$default_src" "$backup_file" \
            || logger -p local0.err "[$tag:$LINENO] .bak reinit failed : $backup_file"
        exit 0
    fi
    logger -p local0.emerg "[$tag:$LINENO] default recovery write failed : $file_path"
fi

# 4) 복구 불가
logger -p local0.emerg "[$tag:$LINENO] cannot recovery : $file_path (.bak invalid, default unavailable or invalid)"
exit 2
