#!/bin/bash
tag=$(basename "$0")
set -euo pipefail

DIR="/var/log/cantops/journald"
CURSOR_FILE="$DIR/.cursor"
DST="$DIR/$(date +%Y%m%d)"
MAX_CNT=14
MAX_SIZE=4294967296

mkdir -p "$DST"

journalctl --sync 2>/dev/null || true

# 스냅샷 전 크기 기록
before_size=$(stat -c%s "$DST/journal.log" 2>/dev/null || echo 0)

# 디스크 여유 공간 체크: 10MB 미만이면 cursor를 진행시키지 않고 건너뜀
# (append 실패 후 cursor가 진행되면 해당 구간 로그가 영구 유실됨)
avail_kb=$(df -k "$DST" 2>/dev/null | awk 'NR==2{print $4}')
if [ "${avail_kb:-0}" -lt 10240 ]; then
    logger -p local0.warn "[$tag:$LINENO] disk space too low (${avail_kb}KB available), skipping journal snapshot to prevent cursor loss"
    exit 0
fi

# 커서 이후의 새 로그만 날짜별 파일에 append (중복 없음)
journalctl --cursor-file="$CURSOR_FILE" \
           --no-pager \
           -o short-iso \
           >> "$DST/journal.log" 2>/dev/null || true

after_size=$(stat -c%s "$DST/journal.log" 2>/dev/null || echo 0)
added=$((after_size - before_size))
logger -p local0.info "[$tag:$LINENO] snapshot -> $DST/journal.log (+${added}B, total=${after_size}B)"

# 날짜별 디렉토리 수 제한
cnt=$(find "$DIR" -mindepth 1 -maxdepth 1 -type d | wc -l)
if (( cnt > MAX_CNT )); then
    logger -p local0.info "[$tag:$LINENO] journald dir cnt : $cnt > $MAX_CNT"
    del=$((cnt - MAX_CNT))
    find "$DIR" -mindepth 1 -maxdepth 1 -type d -printf '%T+ %p\n' | sort | head -n "$del" | cut -d' ' -f2- | xargs -r rm -rf --
fi

# 전체 크기 제한
size=$(du -sb "$DIR" | awk '{print $1}')
if (( size > MAX_SIZE )); then
    logger -p local0.info "[$tag:$LINENO] journald dir size : $size > $MAX_SIZE"
    find "$DIR" -mindepth 1 -maxdepth 1 -type d -printf '%T+ %p\n' | sort | head -n 1 | cut -d' ' -f2- | xargs -r rm -rf --
fi

sync
