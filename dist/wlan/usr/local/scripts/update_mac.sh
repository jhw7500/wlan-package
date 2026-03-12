#!/bin/bash
tag=$(basename "$0")
IFACE=$1
NEW_MAC=$2

case "$IFACE" in
  eth0)  LINK_FILE="/etc/systemd/network/22-eth0.link"  ;;
  mlan0) LINK_FILE="/etc/systemd/network/20-mlan0.link" ;;
  mlan1) LINK_FILE="/etc/systemd/network/21-mlan1.link" ;;
  *)
    logger -p local0.emerg "[$tag:$LINENO] [$IFACE] interface is wrong!!"
    exit 0
    ;;
esac

BACKUP_FILE="${LINK_FILE}.bak"

tmp=""
cleanup_tmp() {
  if [ -n "${tmp:-}" ]; then
    rm -f "$tmp"
  fi
}
trap cleanup_tmp EXIT

# MAC 유효성 검사
if [[ "$NEW_MAC" =~ ^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$ ]]; then
  # --- MAC 정상 ---

  # 백업 파일 없으면 생성
  if [ -f "$LINK_FILE" ] && [ ! -f "$BACKUP_FILE" ]; then
    cp "$LINK_FILE" "$BACKUP_FILE"
    logger -p local0.info "[$tag:$LINENO] [$IFACE] backup created: $BACKUP_FILE"
  fi

  # 현재 .link 파일에 동일 MAC이 있으면 무시
  if [ -f "$LINK_FILE" ]; then
    CURRENT_MAC=$(grep -oP '^MACAddress=\K.*' "$LINK_FILE" 2>/dev/null || true)
    if [ "$CURRENT_MAC" = "$NEW_MAC" ]; then
      logger -p local0.info "[$tag:$LINENO] [$IFACE] MAC unchanged ($NEW_MAC), skipping"
      exit 0
    fi
  fi

  # MAC 주소 업데이트
  tmp="$(mktemp "${LINK_FILE}.tmp.XXXXXX")"

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

  install -o root -g root -m 0644 "$tmp" "$LINK_FILE"
  tmp=""
  logger -p local0.info "[$tag:$LINENO] [$IFACE] Updated MACAddress to $NEW_MAC in $LINK_FILE"


else
  # --- MAC 비정상: 백업에서 복구 ---
  logger -p local0.err "[$tag:$LINENO] [$IFACE] invalid MAC: '$NEW_MAC'"
  if [ -f "$BACKUP_FILE" ]; then
    cp "$BACKUP_FILE" "$LINK_FILE"
    logger -p local0.crit "[$tag:$LINENO] [$IFACE] restored from backup: $BACKUP_FILE"
  else
    logger -p local0.emerg "[$tag:$LINENO] [$IFACE] no backup available: $BACKUP_FILE"
  fi
  exit 0
fi
