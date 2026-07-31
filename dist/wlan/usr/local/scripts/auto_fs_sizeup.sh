#!/bin/bash
tag=$(basename "$0")
key=DSK

DEVICE="mmcblk$1"  #  ^}   ^d  ^l  ^b  ^z  (NOT /dev/mmcblk1)
SECTOR_SIZE=512

#   ^d     ^t^t  ^t ^}  ^j   ^a
TOTAL_BYTES=$(($(cat /sys/block/$DEVICE/size) * SECTOR_SIZE))

#  ^l^l ^k  ^e^x  ^u   ^d   ^d ^b
PART_BYTES=$(lsblk -b -n -o NAME,SIZE /dev/$DEVICE | grep "${DEVICE}p" | awk '{sum+=$2} END {print sum + 0}')

#  ^m  ^d  ^j    ^d ^b
PERCENT=$(awk "BEGIN { printf \"%.2f\", ($PART_BYTES / $TOTAL_BYTES) * 100 }")

#   ^|
echo "Total Device Size: $TOTAL_BYTES bytes, Partition Size Sum: $PART_BYTES bytes"
echo "Used for Partitions: $PERCENT %"

logger -p local0.notice "[$tag:$LINENO] [$key] Total Device Size: $TOTAL_BYTES bytes, Partition Size Sum: $PART_BYTES bytes, Percent: $PERCENT %"

LIMIT=90.00
exceeded=$(awk -v p="$PERCENT" -v limit="$LIMIT" 'BEGIN { if (p > limit) print 1; else print 0 }')

if [ "$exceeded" -eq 1 ]; then
    logger -p local0.notice "[$tag:$LINENO] [$key] no size up beacuse $PERCENT % >= $LIMIT"
    exit 0
fi

start=$(fdisk -l /dev/mmcblk$1 |grep mmcblk$1p$2|awk '{print $2}')
if [ "$start" == "*" ]; then
    echo start sector fail
    logger -p local0.error "[$tag:$LINENO] [$key] start sector fail"
    start=$(fdisk -l /dev/mmcblk$1 |grep mmcblk$1p$2|awk '{print $3}')
    end=$(fdisk -l /dev/mmcblk$1 |grep mmcblk$1p$2|awk '{print $4}')
else
    end=$(fdisk -l /dev/mmcblk$1 |grep mmcblk$1p$2|awk '{print $3}')
fi

echo mmcblk$1p$2 start:$start end:$end
logger -p notice "[$key][$tag:$LINENO] mmcblk$1p$2 start:$start end:$end"

#: <<'END'
if [ "$end" -lt 60000000 ]; then
echo fdisk mmcblk$1p$2
logger -p local0.alert "[$tag:$LINENO] [$key] fdisk mmcblk$1p$2 size up"
fdisk -u -c /dev/mmcblk$1 <<EOF
d
$2
n
p
$2
$start

wq
EOF
logger -p notice "[$key][$tag:$LINENO] resizefe /dev/mmcblk$1p$2"
resize2fs /dev/mmcblk$1p$2
fi
#END
