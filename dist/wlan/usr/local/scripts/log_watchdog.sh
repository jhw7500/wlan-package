#!/bin/bash
# log_watchdog.sh — 로그 파일시스템 사용률 감시 + 긴급 정리
# systemd timer로 5분마다 실행

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

logger -p local0.warn "[$tag:$LINENO] Cleanup finished: deleted=${deleted} files, usage=${usage}%"
