#!/bin/bash
tag=$(basename "$0")
BACKUP_DIR=$1
logger -p local0.info "[$tag:$LINENO] backup dir : $BACKUP_DIR"
mkdir $BACKUP_DIR
if [ ! -d "$BACKUP_DIR" ]; then
    logger -p local0.info "[$tag:$LINENO] $BACKUP_DIR is not dir"
    echo "$BACKUP_DIR is not dir"
    exit 0
fi

tar -cvzf /var/log/cantops/backup/logs.tar.gz /var/log/cantops
tar -cvzf /var/log/cantops/backup/journald.tar.gz /var/log/journald
tar -cvzf /var/log/cantops/backup/local.tar.gz /usr/local
tar -cvzf /var/log/cantops/backup/wlan.tar.gz /opt/wlan
tar -cvzf /var/log/cantops/backup/etc.tar.gz /etc

logger -p local0.info "[$tag:$LINENO] complete"
echo "complete"
