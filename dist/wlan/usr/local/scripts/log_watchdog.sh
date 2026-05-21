#!/bin/bash
# log_watchdog.sh — 로그 파일시스템 사용률 감시 + 긴급 정리
# systemd timer로 10분마다 실행 (log-watchdog.timer: OnUnitActiveSec=10min)

set -uo pipefail

LOG_PART="/var/log/cantops"
THRESHOLD=90
tag="$(basename "$0")"

# 파일시스템 사용률 확인
get_usage() {
    df "$LOG_PART" 2>/dev/null | awk 'NR==2 {gsub(/%/,""); print $5}'
}

usage=$(get_usage)
if [ -z "$usage" ]; then
    logger -p local0.warn "[$tag:$LINENO] Cannot read filesystem usage for $LOG_PART"
    exit 0
fi

if [ "$usage" -lt "$THRESHOLD" ]; then
    exit 0
fi

logger -p local0.warn "[$tag:$LINENO] Log filesystem usage ${usage}% >= ${THRESHOLD}%, starting cleanup"

# 1단계: 강제 logrotate
logrotate -f /etc/logrotate.d/logrotate.rsyslog 2>/dev/null || true

usage=$(get_usage)
if [ "$usage" -lt "$THRESHOLD" ]; then
    logger -p local0.info "[$tag:$LINENO] Cleanup done after forced rotate, usage=${usage}%"
    exit 0
fi

# 2단계: 가장 오래된 압축 로그부터 삭제 (최대 20개씩)
deleted=0
while [ "$usage" -ge "$THRESHOLD" ] && [ "$deleted" -lt 100 ]; do
    mapfile -t oldest_files < <(find "$LOG_PART" -name "*.gz" -printf '%T+ %p\n' 2>/dev/null | sort | head -20 | cut -d' ' -f2-)
    if [ ${#oldest_files[@]} -eq 0 ]; then
        break
    fi
    for f in "${oldest_files[@]}"; do
        rm -f "$f"
        deleted=$((deleted + 1))
    done
    usage=$(get_usage)
done

# 3단계: .gz 정리 후에도 임계 이상이면 journald 스냅샷(비압축)을 오래된 날짜부터 삭제.
# journal.log 는 *.gz 가 아니라 2단계 대상이 아니고 logrotate 대상도 아니므로
# 디스크 풀 긴급 상황에서 여기서만 정리된다(정상 retention 은 journald_snapshot.sh).
JOURNALD_DIR="/var/log/cantops/journald"
while [ "$usage" -ge "$THRESHOLD" ]; do
    oldest_dir=$(find "$JOURNALD_DIR" -mindepth 1 -maxdepth 1 -type d -printf '%T+ %p\n' 2>/dev/null | sort | head -1 | cut -d' ' -f2-)
    [ -z "$oldest_dir" ] && break
    rm -rf -- "$oldest_dir"
    deleted=$((deleted + 1))
    usage=$(get_usage)
done

logger -p local0.warn "[$tag:$LINENO] Cleanup finished: deleted=${deleted} files, usage=${usage}%"
