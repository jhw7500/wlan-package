#!/bin/bash
tag=$(basename "$0")
set -euo pipefail

# 동시 실행 직렬화: 타이머(5분 주기)/부팅완료/shutdown 트리거가 겹쳐
# cursor 파일이 깨지거나 로그가 중복/유실되는 것을 방지한다.
exec 9>/run/journald_snapshot.lock
flock 9

DIR="/var/log/cantops/journald"
CURSOR_FILE="$DIR/.cursor"
DST="$DIR/$(date +%Y%m%d)"
MAX_CNT=14
MAX_SIZE=4294967296

mkdir -p "$DST"

journalctl --sync 2>/dev/null || true

# --- 과거 날짜 스냅샷 압축 (당일 파일은 append 중이라 제외) ---
# logrotate.rsyslog 와 동일하게 gzip 한다. 조회는 zgrep/zcat 사용.
for f in "$DIR"/*/journal.log; do
    [ -e "$f" ] || continue
    if [ "$f" != "$DST/journal.log" ]; then
        gzip -f "$f" 2>/dev/null || true
    fi
done

# --- retention 정리 (디스크 부족 시에도 반드시 실행) ---

# 날짜별 디렉토리 수 제한
cnt=$(find "$DIR" -mindepth 1 -maxdepth 1 -type d | wc -l)
if (( cnt > MAX_CNT )); then
    logger -p local0.info "[$tag:$LINENO] journald dir cnt : $cnt > $MAX_CNT"
    del=$((cnt - MAX_CNT))
    find "$DIR" -mindepth 1 -maxdepth 1 -type d -printf '%T+ %p\n' | sort | head -n "$del" | cut -d' ' -f2- | xargs -r rm -rf --
fi

# 전체 크기 제한 — 한도 이하로 내려갈 때까지 오래된 날짜부터 반복 삭제
size=$(du -sb "$DIR" | awk '{print $1}')
while (( size > MAX_SIZE )); do
    oldest=$(find "$DIR" -mindepth 1 -maxdepth 1 -type d -printf '%T+ %p\n' | sort | head -n 1 | cut -d' ' -f2-)
    [ -z "$oldest" ] && break
    logger -p local0.info "[$tag:$LINENO] journald dir size : $size > $MAX_SIZE, rm $oldest"
    rm -rf -- "$oldest"
    size=$(du -sb "$DIR" | awk '{print $1}')
done

# --- 디스크 여유 공간 체크 후 스냅샷 ---

avail_kb=$(df -k "$DST" 2>/dev/null | awk 'NR==2{print $4}')
if [ "${avail_kb:-0}" -lt 10240 ]; then
    logger -p local0.warn "[$tag:$LINENO] disk space too low (${avail_kb}KB < 10240KB), skipping journal snapshot to prevent cursor loss"
    exit 0
fi

# cursor 유실/손상 시 직전 백업본으로 복구해 과거 로그 재덤프(중복 폭증)를 막는다.
# (.cursor 와 .cursor.bak 이 동시에 손상된 경우만 head 부터 재덤프되며, 그 1회
#  중복량은 RuntimeMaxUse 로 제한된다)
if [ ! -s "$CURSOR_FILE" ] && [ -s "$CURSOR_FILE.bak" ]; then
    cp -f "$CURSOR_FILE.bak" "$CURSOR_FILE" 2>/dev/null || true
    logger -p local0.warn "[$tag:$LINENO] cursor missing/empty, restored from backup"
fi

# 스냅샷 전 크기 기록
before_size=$(stat -c%s "$DST/journal.log" 2>/dev/null || echo 0)

# 커서 이후의 새 로그만 날짜별 파일에 append (중복 없음)
journalctl --cursor-file="$CURSOR_FILE" \
           --no-pager \
           -o short-iso \
           >> "$DST/journal.log" 2>/dev/null || true

# 갱신된 cursor 를 백업(다음 실행의 유실/손상 대비)
if [ -s "$CURSOR_FILE" ]; then
    cp -f "$CURSOR_FILE" "$CURSOR_FILE.bak" 2>/dev/null || true
fi

after_size=$(stat -c%s "$DST/journal.log" 2>/dev/null || echo 0)
added=$((after_size - before_size))
logger -p local0.info "[$tag:$LINENO] snapshot -> $DST/journal.log (+${added}B, total=${after_size}B)"

sync
