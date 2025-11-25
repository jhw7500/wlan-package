#!/bin/bash
tag=$(basename "$0")
set -euo pipefail
SRC="/run/log/journal"
DIR="/var/log/cantops/journald"
DST="$DIR/$(date +%Y%m%d)"
MAX_CNT=7
MAX_SIZE=4294967296
#MAX_SIZE=838860800
mkdir -p "$DST"

# 저널 파일들만 증분 복사(덮어쓰기 in-place)
rsync -a --inplace --no-whole-file --chmod=Fu=rw,Fg=r,Fa=r "$SRC"/ "$DST"/
#rsync -a --inplace --no-whole-file --chmod=Fu=rw,Fg=r,Fa=r "$SRC"/ "$DIR"/total/

cnt=$(find "$DIR" -mindepth 1 -maxdepth 1 -type d | wc -l)
if (( cnt > MAX_CNT )); then
    logger -p local0.info "[$tag:$LINENO] journald file cnt : $cnt > $MAX_CNT"
    del=$((cnt - MAX_CNT))
    find "$DIR" -mindepth 1 -maxdepth 1 -type d -printf '%T+ %p\n' | sort | head -n $del | cut -d' ' -f2- | xargs -r rm -rf --
fi

size=$(du -sb "$DIR" | awk '{print $1}')
if (( size > MAX_SIZE )); then
    logger -p local0.info "[$tag:$LINENO] journald file size : $size > $MAX_SIZE"
    find "$DIR" -mindepth 1 -maxdepth 1 -type d -printf '%T+ %p\n' | sort | head -n 1 | cut -d' ' -f2- | xargs -r rm -rf --
fi

#find "$DIR" -mindepth 1 -maxdepth 1 -type d -mtime +7 -print -exec rm -rf {} +

sync

