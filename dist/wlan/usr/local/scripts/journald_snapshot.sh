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
