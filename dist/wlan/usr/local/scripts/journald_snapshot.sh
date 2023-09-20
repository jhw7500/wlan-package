#!/bin/bash
set -euo pipefail
SRC="/run/log/journal"
DST="/var/log/cantops/journald/$(date +%Y%m%d)"
mkdir -p "$DST"

# 저널 파일들만 증분 복사(덮어쓰기 in-place)
rsync -a --inplace --no-whole-file --chmod=Fu=rw,Fg=r,Fa=r "$SRC"/ "$DST"/

# 보관 정책(예: 30일)
find /var/log/cantops/journald -mindepth 1 -maxdepth 1 -type d -mtime +7 -print -exec rm -rf {} +
