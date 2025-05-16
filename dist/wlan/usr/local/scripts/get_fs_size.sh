#!/bin/sh

DEVICE="mmcblk$1"  # 이름만 사용 (NOT /dev/mmcblk1)
SECTOR_SIZE=512

# 전체 디바이스 크기
TOTAL_BYTES=$(($(cat /sys/block/$DEVICE/size) * SECTOR_SIZE))

# 파티션 합계 계산
PART_BYTES=$(lsblk -b -n -o NAME,SIZE /dev/$DEVICE | grep "${DEVICE}p" | awk '{sum+=$2} END {print sum + 0}')

# 퍼센트 계산
PERCENT=$(awk "BEGIN { printf \"%.2f\", ($PART_BYTES / $TOTAL_BYTES) * 100 }")

# 출력
echo "Total Device Size: $TOTAL_BYTES bytes, Partition Size Sum: $PART_BYTES bytes"
echo "Used for Partitions: $PERCENT %"
