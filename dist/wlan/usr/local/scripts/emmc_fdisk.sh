#!/bin/bash
DISK=/dev/mmcblk2
P2=${DISK}p2
P3=${DISK}p3
SECT=512
P2_START=$(cat /sys/class/block/mmcblk2p2/start)
P2_SIZE_5G=$(( 44*1024*1024*1024 / (SECT*10) ))   # = 10485760

# 백업
sfdisk -d $DISK > /root/mmcblk2.$(date +%F-%H%M%S).bak

# 현 테이블에서 p2/p3 줄 제거 후 새 정의 추가
sfdisk -d $DISK | grep -v -E '^/dev/mmcblk2p2|^/dev/mmcblk2p3' > /tmp/pt.base

cat >> /tmp/pt.base <<EOF
${DISK}p2 : start=${P2_START}, size=${P2_SIZE_5G}, type=83
${DISK}p3 : start=$((P2_START + P2_SIZE_5G)), type=83
EOF

# 적용
sfdisk --no-reread $DISK < /tmp/pt.base
partprobe $DISK || true
udevadm settle || true

resize2fs /dev/mmcblk2p2
e2fsck -f -p /dev/mmcblk2p2
mkfs.ext4 -F /dev/mmcblk2p3

sync
