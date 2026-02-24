#!/bin/bash
# write_mac.sh <iface> <mac>
# MAC을 강제로 기록:
#   1. /opt/wlan/mac/ 파일
#   2. .link 파일 (새로 씀)
#   3. .bak 파일 (새로 씀)

tag=$(basename "$0")
IFACE=$1
NEW_MAC=$2

case "$IFACE" in
  eth0)  LINK_FILE="/etc/systemd/network/22-eth0.link" ; MAC_FILE="/opt/wlan/mac/wired" ;;
  mlan0) LINK_FILE="/etc/systemd/network/20-mlan0.link"; MAC_FILE="/opt/wlan/mac/base0" ;;
  mlan1) LINK_FILE="/etc/systemd/network/21-mlan1.link"; MAC_FILE="/opt/wlan/mac/base1" ;;
  *)
    logger -p local0.emerg "[$tag:$LINENO] [$IFACE] interface is wrong!!"
    exit 1
    ;;
esac

BACKUP_FILE="${LINK_FILE}.bak"

# MAC 유효성 검사
if ! [[ "$NEW_MAC" =~ ^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$ ]]; then
  logger -p local0.err "[$tag:$LINENO] [$IFACE] invalid MAC: '$NEW_MAC'"
  exit 1
fi

# 1. /opt/wlan/mac/ 에 MAC 기록
mkdir -p "$(dirname "$MAC_FILE")"
echo "$NEW_MAC" > "$MAC_FILE"
logger -p local0.info "[$tag:$LINENO] [$IFACE] Written MAC to $MAC_FILE"

# link 파일 내용 생성 함수
link_content() {
  printf '[Match]\nOriginalName=%s\n\n[Link]\nMACAddress=%s\n' "$IFACE" "$NEW_MAC"
}

# 2. .link 파일 새로 쓰기
if [ -f "$LINK_FILE" ]; then
  tmp="$(mktemp "${LINK_FILE}.tmp.XXXXXX")"
  trap 'rm -f "$tmp"' EXIT
  link_content > "$tmp"
  install -o root -g root -m 0644 "$tmp" "$LINK_FILE"
  logger -p local0.info "[$tag:$LINENO] [$IFACE] Written $LINK_FILE → $NEW_MAC"
else
  logger -p local0.warn "[$tag:$LINENO] [$IFACE] link file not found: $LINK_FILE"
fi

# 3. .bak 파일 새로 쓰기
tmp_bak="$(mktemp "${BACKUP_FILE}.tmp.XXXXXX")"
trap 'rm -f "$tmp_bak"' EXIT
link_content > "$tmp_bak"
install -o root -g root -m 0644 "$tmp_bak" "$BACKUP_FILE"
logger -p local0.info "[$tag:$LINENO] [$IFACE] Written $BACKUP_FILE → $NEW_MAC"
