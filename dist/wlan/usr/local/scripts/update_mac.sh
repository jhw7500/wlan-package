#!/bin/bash
tag=$(basename "$0")
IFACE=$1
NEW_MAC=$2

if [ -z "$NEW_MAC" ]; then
    logger -p local0.info "[$tag:$LINENO] [$IFACE] no MAC provided, skipping"
    exit 0
fi

# systemd network 디렉토리. 테스트에서 SYSTEMD_NETWORK_DIR로 오버라이드 가능(기본은 실제 경로).
NETWORK_DIR="${SYSTEMD_NETWORK_DIR:-/etc/systemd/network}"
case "$IFACE" in
  eth0)  LINK_FILE="$NETWORK_DIR/22-eth0.link"  ;;
  mlan0) LINK_FILE="$NETWORK_DIR/20-mlan0.link" ;;
  mlan1) LINK_FILE="$NETWORK_DIR/21-mlan1.link" ;;
  *)
    logger -p local0.emerg "[$tag:$LINENO] [$IFACE] interface is wrong!!"
    exit 0
    ;;
esac

# 회전 백업: ${BACKUP_PREFIX}.1(최신) ~ .${MAX_BAK}(가장 오래됨). 인터페이스당 최대 ${MAX_BAK}개.
BACKUP_PREFIX="${LINK_FILE}.bak"
MAX_BAK=5

tmp=""
cleanup_tmp() {
  if [ -n "${tmp:-}" ]; then
    rm -f "$tmp"
  fi
}
trap cleanup_tmp EXIT

# 현재 .link를 회전 백업한다 (인터페이스당 최대 $MAX_BAK개, 최신=.bak.1).
# 최신 백업(.bak.1)과 MAC이 동일하면 중복 백업을 만들지 않는다.
backup_link() {
  [ -f "$LINK_FILE" ] || return 0

  local cur_mac newest_mac i
  cur_mac=$(grep -oP '^MACAddress=\K.*' "$LINK_FILE" 2>/dev/null || true)

  # 중복 방지: 최신 백업과 MAC이 같으면 새 백업 생성 안 함
  if [ -f "${BACKUP_PREFIX}.1" ]; then
    newest_mac=$(grep -oP '^MACAddress=\K.*' "${BACKUP_PREFIX}.1" 2>/dev/null || true)
    if [ "$cur_mac" = "$newest_mac" ]; then
      logger -p local0.info "[$tag:$LINENO] [$IFACE] backup skipped (same MAC: ${cur_mac:-none})"
      return 0
    fi
  fi

  # 회전: 가장 오래된(.bak.$MAX_BAK) 삭제 → 한 칸씩 밀기 → 현재 .link를 .bak.1로 저장
  rm -f "${BACKUP_PREFIX}.${MAX_BAK}"
  for (( i=MAX_BAK-1; i>=1; i-- )); do
    [ -f "${BACKUP_PREFIX}.${i}" ] && mv -f "${BACKUP_PREFIX}.${i}" "${BACKUP_PREFIX}.$((i+1))"
  done
  cp "$LINK_FILE" "${BACKUP_PREFIX}.1"
  logger -p local0.info "[$tag:$LINENO] [$IFACE] backup created: ${BACKUP_PREFIX}.1 (rotate, max ${MAX_BAK})"
}

# MAC 유효성 검사
if [[ "$NEW_MAC" =~ ^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$ ]]; then
  # --- MAC 정상 ---

  # 현재 .link 파일에 동일 MAC이 있으면 변경 없음 → 백업/쓰기 모두 skip
  if [ -f "$LINK_FILE" ]; then
    CURRENT_MAC=$(grep -oP '^MACAddress=\K.*' "$LINK_FILE" 2>/dev/null || true)
    if [ "$CURRENT_MAC" = "$NEW_MAC" ]; then
      logger -p local0.info "[$tag:$LINENO] [$IFACE] MAC unchanged ($NEW_MAC), skipping"
      exit 0
    fi
  fi

  # 실제 변경이 일어나므로 현재 .link를 회전 백업 (최신과 동일 MAC이면 중복 생성 안 함)
  backup_link

  # MAC 주소 업데이트
  mkdir -p "$(dirname "$LINK_FILE")"
  if ! tmp="$(mktemp "${LINK_FILE}.tmp.XXXXXX")"; then
    logger -p local0.err "[$tag:$LINENO] [$IFACE] failed to create temp file in $(dirname "$LINK_FILE")"
    exit 1
  fi

  # 파일이 없거나 / 비었거나(0바이트) / [Match]·[Link] 섹션이 없으면(구버전 버그로 생긴
  # 빈 .link 포함) [Match](OriginalName)/[Link](MACAddress)를 갖춘 파일을 통째로 (재)생성한다.
  if [ ! -s "$LINK_FILE" ] || ! grep -q '^\[Match\]' "$LINK_FILE" || ! grep -q '^\[Link\]' "$LINK_FILE"; then
    printf '[Match]\nOriginalName=%s\n\n[Link]\nMACAddress=%s\n' "$IFACE" "$NEW_MAC" > "$tmp"
    logger -p local0.info "[$tag:$LINENO] [$IFACE] $LINK_FILE missing/empty/section-less; (re)creating valid .link"
  elif grep -q "^MACAddress=" "$LINK_FILE"; then
    # 기존 MACAddress 라인 교체
    sed "s/^MACAddress=.*/MACAddress=${NEW_MAC}/" "$LINK_FILE" > "$tmp"
  else
    # [Link]는 있으나 MACAddress 없음 → [Link] 섹션 내부에 추가
    awk -v mac="$NEW_MAC" '
      /^\[Link\]/ { print; inlink=1; next }
      inlink && /^\[/ { print "MACAddress="mac; inlink=0 }
      { print }
      END { if (inlink) print "MACAddress="mac }
    ' "$LINK_FILE" > "$tmp"
  fi

  # root일 때만 소유권을 root:root로 강제(부팅 시 실제 경로). 비-root(테스트)에서는 모드만 지정.
  if [ "$(id -u)" -eq 0 ]; then
    install -o root -g root -m 0644 "$tmp" "$LINK_FILE"
  else
    install -m 0644 "$tmp" "$LINK_FILE"
  fi
  tmp=""
  logger -p local0.info "[$tag:$LINENO] [$IFACE] Updated MACAddress to $NEW_MAC in $LINK_FILE"


else
  # --- MAC 비정상: 백업에서 복구 ---
  logger -p local0.err "[$tag:$LINENO] [$IFACE] invalid MAC: '$NEW_MAC'"

  # 현재 .link가 이미 유효한 MAC을 갖고 있으면 오래된 백업으로 되돌리지 않고 그대로 유지
  if [ -f "$LINK_FILE" ]; then
    CURRENT_MAC=$(grep -oP '^MACAddress=\K.*' "$LINK_FILE" 2>/dev/null || true)
    if [[ "$CURRENT_MAC" =~ ^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$ ]]; then
      logger -p local0.warn "[$tag:$LINENO] [$IFACE] current MAC already valid ($CURRENT_MAC); skip restore"
      exit 0
    fi
  fi

  # 최신 회전 백업(.bak.1)에서 복구, 없으면 구버전 단일 백업(.bak)로 폴백
  RESTORE_FROM=""
  if [ -f "${BACKUP_PREFIX}.1" ]; then
    RESTORE_FROM="${BACKUP_PREFIX}.1"
  elif [ -f "${BACKUP_PREFIX}" ]; then
    RESTORE_FROM="${BACKUP_PREFIX}"
  fi
  if [ -n "$RESTORE_FROM" ]; then
    cp "$RESTORE_FROM" "$LINK_FILE"
    logger -p local0.crit "[$tag:$LINENO] [$IFACE] restored from backup: $RESTORE_FROM"
  else
    logger -p local0.emerg "[$tag:$LINENO] [$IFACE] no backup available: ${BACKUP_PREFIX}.1"
  fi
  exit 0
fi
