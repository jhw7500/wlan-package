#!/bin/bash
INPUT_PATH=$1
LIMIT=$2
SIZE=$3
KEY=$4
FILE_PATH=""
tag=$(basename "$0")
cnt=0

if [ -z "$INPUT_PATH" ];then
    logger -p local0.crit "[$tag:$LINENO] failed : you should input path"
    exit 1
fi

#echo "========================="
#echo "input path : " $INPUT_PATH
logger -p local0.info "[$tag:$LINENO] path : $INPUT_PATH, limit cnt : $LIMIT, limit size : $SIZE MB"

if [ ! -d "$INPUT_PATH" ]; then
    logger -p local0.crit "[$tag:$LINENO] failed : $INPUT_PATH is not directory"
    exit 1
fi

if [ $LIMIT -le 1 ]; then
    logger -p local0.crit "[$tag:$LINENO] failed : LIMIT:$LIMIT greater than 1"
    exit 1
fi

if [ -n "$KEY" ]; then
    FILE_PATH=$INPUT_PATH/$KEY
else
    FILE_PATH=$INPUT_PATH
fi

cnt=$(ls -lt $FILE_PATH* | grep ^- | wc -l)

#echo "file_path:$FILE_PATH, file_cnt:$cnt"
if [ $cnt -gt $LIMIT ]; then
    logger -p local0.info "[$tag:$LINENO] file cnt $cnt > $LIMIT ($tailcnt)"
    find $FILE_PATH* -maxdepth 1 -type f -printf '%T+ %p\n' | sort | head -n -$LIMIT | cut -d' ' -f2- | xargs -r rm -f
fi

MAX_SIZE=$(($SIZE * 1024 * 1024))
current_size=$(du -sb $INPUT_PATH | awk '{print $1}')
if [ $current_size -gt $MAX_SIZE ]; then
    logger -p local0.err "[$tag:$LINENO] $INPUT_PATH dir $current_size byte over $MAX_SIZE byte!"
    oldest_file=$(ls $INPUT_PATH | grep -E '\log-[0-9]{8}\.gz$' | sort | head -n 1)
    #echo "Deleting oldest log file: $oldest_file"
    logger -p local0.err "[$tag:$LINENO] deleting oldest log file: $oldest_file"
    rm -f "$INPUT_PATH/$oldest_file"
    current_size=$(du -sb $INPUT_PATH | awk '{print $1}')
    logger -p local0.err "[$tag:$LINENO] $INPUT_PATH dir $current_size byte"
fi
