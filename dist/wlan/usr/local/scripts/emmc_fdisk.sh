#!/bin/bash
# Detect eMMC block device number
# iMX8MM: mmcblk2, iMX93: mmcblk0
if [ -d /sys/block/mmcblk2 ]; then
    EMMC_NUM=2
else
    EMMC_NUM=0
fi

DISK=/dev/mmcblk${EMMC_NUM}
P2=${DISK}p2
P3=${DISK}p3
SECT=512
P2_START=$(cat /sys/class/block/mmcblk${EMMC_NUM}p2/start)
P2_SIZE_5G=$(( 44*1024*1024*1024 / (SECT*10) ))   # = 10485760

# 백업
sfdisk -d $DISK > /root/mmcblk${EMMC_NUM}.$(date +%F-%H%M%S).bak

# 현 테이블에서 p2/p3 줄 제거 후 새 정의 추가
sfdisk -d $DISK | grep -v -E "^/dev/mmcblk${EMMC_NUM}p2|^/dev/mmcblk${EMMC_NUM}p3" > /tmp/pt.base

cat >> /tmp/pt.base <<EOF
${DISK}p2 : start=${P2_START}, size=${P2_SIZE_5G}, type=83
${DISK}p3 : start=$((P2_START + P2_SIZE_5G)), type=83
EOF

# 적용
sfdisk --no-reread $DISK < /tmp/pt.base
partprobe $DISK || true
udevadm settle || true

resize2fs ${P2}
e2fsck -f -p ${P2}
mkfs.ext4 -F ${P3}

sync
