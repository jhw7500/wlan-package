#!/bin/bash

tag=$(basename "$0")

# 입력 인자: 파일 경로와 패턴
file_path=$1
pattern=$2

# 백업 파일 경로 (원본 파일 경로 뒤에 .bak 확장자 추가)
backup_file="${file_path}.bak"

# 파일이 존재하는지 확인
if [ -f "$file_path" ]; then
    # 파일에 패턴이 존재하는지 확인
    if grep -q "$pattern" "$file_path"; then
        # 패턴이 존재하면 백업 파일을 생�
        logger -p local0.info "[$tag:$LINENO] backup to file : $backup_file"
        cp "$file_path" "$backup_file"
    else
        # 패턴이 존재하지 않으면 기존 백업 파일로 복구
        if [ -f "$backup_file" ]; then
            logger -p local0.crit "[$tag:$LINENO] recovery to file : $file_path"
            cp "$backup_file" "$file_path"
        else
            logger -p local0.emerg "[$tag:$LINENO] cannot recovery : $backup_file is not exist"
        fi
    fi
else
    logger -p local0.emerg "[$tag:$LINENO] $file_path is not exist"
    if [ -f "$backup_file" ]; then
        logger -p local0.crit "[$tag:$LINENO] recovery to file : $file_path"
        cp "$backup_file" "$file_path"
    else
        logger -p local0.emerg "[$tag:$LINENO] cannot recovery : $backup_file is not exist"
    fi
fi
