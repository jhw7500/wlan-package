#!/bin/bash
tag=$(basename "$0")
IFACE=$1
NEW_MAC=$2

case "$IFACE" in
  eth0)  LINK_FILE="/etc/systemd/network/22-eth0.link" ;;
  mlan0) LINK_FILE="/etc/systemd/network/20-mlan0.link" ;;
  mlan1) LINK_FILE="/etc/systemd/network/21-mlan1.link" ;;
  *)
    logger -p local0.emerg "[$tag:$LINENO] [$IFACE] interface is wrong!!"
    exit 0
    ;;
esac

if ! [[ "$NEW_MAC" =~ ^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$ ]]; then
  logger -p local0.err "[$tag:$LINENO] [$IFACE] invalid MAC format: $NEW_MAC, keeping original"
  exit 0
fi

/usr/local/scripts/backup_file.sh "$LINK_FILE" MACAddress

tmp="$(mktemp "${LINK_FILE}.tmp.XXXXXX")"
cleanup() { rm -f "$tmp"; }
trap cleanup EXIT

if grep -q "^MACAddress=" "$LINK_FILE"; then
  # 기존 라인 교체
  sed "s/^MACAddress=.*/MACAddress=${NEW_MAC}/" "$LINK_FILE" > "$tmp"
else
  # 없으면 [Link] 섹션 내부에 추가
  awk -v mac="$NEW_MAC" '
    /^\[Link\]/ { print; inlink=1; next }
    inlink && /^\[/ { print "MACAddress="mac; inlink=0 }
    { print }
    END { if (inlink) print "MACAddress="mac }
  ' "$LINK_FILE" > "$tmp"
fi

# 여기서 모드/소유를 강제로 정상화 (umask 무관)
install -o root -g root -m 0644 "$tmp" "$LINK_FILE"

logger -p local0.info "[$tag:$LINENO] [$IFACE] Updated MACAddress to $NEW_MAC in $LINK_FILE"
