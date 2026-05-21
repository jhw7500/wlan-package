#!/bin/bash
tag=$(basename "$0")
BACKUP_DIR="${1:-/var/log/cantops/backup}"
logger -p local0.info "[$tag:$LINENO] backup dir : $BACKUP_DIR"
mkdir -p "$BACKUP_DIR"
if [ ! -d "$BACKUP_DIR" ]; then
    logger -p local0.info "[$tag:$LINENO] $BACKUP_DIR is not dir"
    echo "$BACKUP_DIR is not dir"
    exit 0
fi

# /var/log/cantops 백업 시 출력 디렉토리($BACKUP_DIR)를 제외해 재귀 백업을 막는다.
tar -cvzf "$BACKUP_DIR/logs.tar.gz" --exclude="$BACKUP_DIR" /var/log/cantops
tar -cvzf "$BACKUP_DIR/journald.tar.gz" /var/log/cantops/journald
tar -cvzf "$BACKUP_DIR/local.tar.gz" /usr/local
tar -cvzf "$BACKUP_DIR/wlan.tar.gz" /opt/wlan
tar -cvzf "$BACKUP_DIR/etc.tar.gz" /etc

logger -p local0.info "[$tag:$LINENO] complete"
echo "complete"
