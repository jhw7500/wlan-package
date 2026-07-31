#!/bin/bash
# update_mac.sh <iface> <mac|--cleanup|--reset-backups> [<plan-iface>=<plan-mac> ...]
# 최종 계획 인자는 wifi_init.sh의 다중 인터페이스 교환 시에만 사용한다.
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
# shellcheck source=./mac_link_lib.sh
. "$SCRIPT_DIR/mac_link_lib.sh"

tag=$(basename "$0")
IFACE="${1:-}"
NEW_MAC="${2:-}"
FINAL_MAC_PLAN=("${@:3}")

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

if ! mac_acquire_global_lock "$NETWORK_DIR"; then
  logger -p local0.err "[$tag:$LINENO] [$IFACE] failed to acquire global MAC update lock"
  exit 1
fi

tmp=""
cleanup_tmp() {
  if [ -n "${tmp:-}" ]; then
    rm -f "$tmp"
  fi
}
trap cleanup_tmp EXIT

cleanup_owned_artifacts() {
  local artifact suffix cleaned=0

  # 구버전/비정상 종료가 남긴 install 입력용 임시파일.
  for artifact in "${LINK_FILE}".tmp.*; do
    [ -e "$artifact" ] || continue
    if rm -f -- "$artifact"; then
      cleaned=$((cleaned + 1))
    else
      logger -p local0.err "[$tag:$LINENO] [$IFACE] failed to remove orphan: $artifact"
      return 1
    fi
  done

  # 현재 정책이 소유하는 숫자 회전본 중 .1~.5만 유지한다.
  for artifact in "${BACKUP_PREFIX}".*; do
    [ -e "$artifact" ] || continue
    suffix="${artifact#"$BACKUP_PREFIX".}"
    case "$suffix" in
      1|2|3|4|5) continue ;;
      ''|*[!0-9]*) continue ;;
    esac
    if rm -f -- "$artifact"; then
      cleaned=$((cleaned + 1))
    else
      logger -p local0.err "[$tag:$LINENO] [$IFACE] failed to remove excess backup: $artifact"
      return 1
    fi
  done

    [ "$cleaned" -eq 0 ] \
    || logger -p local0.info "[$tag:$LINENO] [$IFACE] cleaned $cleaned stale link artifact(s)"
}

cleanup_owned_artifacts || exit 1
if [ "$NEW_MAC" = "--cleanup" ]; then
  exit 0
fi

# 공장 초기화 전용: 패키지가 소유하는 fixed/숫자 회전 백업만 모두 삭제한다.
# 알 수 없는 비숫자 suffix와 운영자 .link는 건드리지 않는다.
reset_owned_backups() {
  local artifact suffix cleaned=0
  for artifact in "$BACKUP_PREFIX" "${BACKUP_PREFIX}".*; do
    [ -e "$artifact" ] || continue
    if [ "$artifact" != "$BACKUP_PREFIX" ]; then
      suffix="${artifact#"$BACKUP_PREFIX".}"
      case "$suffix" in
        ''|*[!0-9]*) continue ;;
      esac
    fi
    if rm -f -- "$artifact"; then
      cleaned=$((cleaned + 1))
    else
      logger -p local0.err "[$tag:$LINENO] [$IFACE] failed to remove link backup: $artifact"
      return 1
    fi
  done
  sync "$NETWORK_DIR" 2>/dev/null || sync
  logger -p local0.info "[$tag:$LINENO] [$IFACE] reset $cleaned package-owned link backup(s)"
}

if [ "$NEW_MAC" = "--reset-backups" ]; then
  reset_owned_backups
  exit $?
fi

# 현재 .link를 회전 백업한다 (인터페이스당 최대 $MAX_BAK개, 최신=.bak.1).
# 최신 백업(.bak.1)과 MAC이 동일하면 중복 백업을 만들지 않는다.
backup_link() {
  [ -f "$LINK_FILE" ] || return 0

  local cur_mac newest_mac i
  cur_mac=$(mac_read_link_address "$LINK_FILE")

  # 중복 방지: 최신 백업과 MAC이 같으면 새 백업 생성 안 함
  if [ -f "${BACKUP_PREFIX}.1" ]; then
    newest_mac=$(mac_read_link_address "${BACKUP_PREFIX}.1")
    if [ "$cur_mac" = "$newest_mac" ]; then
      logger -p local0.info "[$tag:$LINENO] [$IFACE] backup skipped (same MAC: ${cur_mac:-none})"
      return 0
    fi
  fi

  # 회전: 가장 오래된(.bak.$MAX_BAK) 삭제 → 한 칸씩 밀기 → 현재 .link를 .bak.1로 저장
  rm -f "${BACKUP_PREFIX}.${MAX_BAK}" || return 1
  for (( i=MAX_BAK-1; i>=1; i-- )); do
    if [ -f "${BACKUP_PREFIX}.${i}" ]; then
      mv -f "${BACKUP_PREFIX}.${i}" "${BACKUP_PREFIX}.$((i+1))" || return 1
    fi
  done
  cp "$LINK_FILE" "${BACKUP_PREFIX}.1" || return 1
  logger -p local0.info "[$tag:$LINENO] [$IFACE] backup created: ${BACKUP_PREFIX}.1 (rotate, max ${MAX_BAK})"
}

# MAC 유효성 검사
if mac_is_assignable "$NEW_MAC"; then
  # --- MAC 정상 ---
  NEW_MAC=$(mac_normalize "$NEW_MAC")

  # 다른 활성 .link에 같은 MAC을 적용하면 인터페이스 간 주소 충돌이 발생한다.
  CONFLICT_LINK=$(mac_find_link_conflict \
    "$NETWORK_DIR" "$LINK_FILE" "$NEW_MAC" "${FINAL_MAC_PLAN[@]}" || true)
  if [ -n "$CONFLICT_LINK" ]; then
    logger -p local0.err "[$tag:$LINENO] [$IFACE] MAC conflict: $NEW_MAC already used by $CONFLICT_LINK"
    exit 1
  fi

  # 현재 .link 파일에 동일 MAC이 있으면 변경 없음 → 백업/쓰기 모두 skip
  if [ -f "$LINK_FILE" ]; then
    CURRENT_MAC=$(mac_read_link_address "$LINK_FILE")
    if [ "$CURRENT_MAC" = "$NEW_MAC" ]; then
      logger -p local0.info "[$tag:$LINENO] [$IFACE] MAC unchanged ($NEW_MAC), skipping"
      exit 0
    fi
  fi

  # 실제 변경이 일어나므로 현재 .link를 회전 백업 (최신과 동일 MAC이면 중복 생성 안 함)
  if ! backup_link; then
    logger -p local0.err "[$tag:$LINENO] [$IFACE] failed to rotate backup for $LINK_FILE"
    exit 1
  fi

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
  else
    # [Match]의 MACAddress는 선택 조건이므로 보존하고 [Link] 섹션의 할당 주소만
    # 교체/추가한다. 중복된 [Link] MACAddress 라인은 하나로 정규화한다.
    mac_render_link_with_address "$LINK_FILE" "$NEW_MAC" > "$tmp"
  fi

  # root일 때만 소유권을 root:root로 강제(부팅 시 실제 경로). 비-root(테스트)에서는 모드만 지정.
  if [ "$(id -u)" -eq 0 ]; then
    if ! install -o root -g root -m 0644 "$tmp" "$LINK_FILE"; then
      logger -p local0.err "[$tag:$LINENO] [$IFACE] failed to install $LINK_FILE"
      exit 1
    fi
  else
    if ! install -m 0644 "$tmp" "$LINK_FILE"; then
      logger -p local0.err "[$tag:$LINENO] [$IFACE] failed to install $LINK_FILE"
      exit 1
    fi
  fi
  rm -f "$tmp" || {
    logger -p local0.err "[$tag:$LINENO] [$IFACE] failed to remove temp file: $tmp"
    exit 1
  }
  tmp=""
  logger -p local0.info "[$tag:$LINENO] [$IFACE] Updated MACAddress to $NEW_MAC in $LINK_FILE"


else
  # --- MAC 비정상: 백업에서 복구 ---
  logger -p local0.err "[$tag:$LINENO] [$IFACE] invalid MAC: '$NEW_MAC'"

  # 현재 .link가 이미 유효한 MAC을 갖고 있으면 오래된 백업으로 되돌리지 않고 그대로 유지
  if [ -f "$LINK_FILE" ]; then
    CURRENT_MAC=$(mac_read_link_address "$LINK_FILE")
    if mac_is_assignable "$CURRENT_MAC"; then
      logger -p local0.warn "[$tag:$LINENO] [$IFACE] current MAC already valid ($CURRENT_MAC); skip restore"
      exit 1
    fi
  fi

  # 최신 유효 회전 백업(.bak.1)에서 복구, 없으면 구버전 단일 백업(.bak)로 폴백
  RESTORE_FROM=""
  if [ -f "${BACKUP_PREFIX}.1" ] \
      && mac_is_assignable "$(mac_read_link_address "${BACKUP_PREFIX}.1")"; then
    RESTORE_FROM="${BACKUP_PREFIX}.1"
  elif [ -f "${BACKUP_PREFIX}" ] \
      && mac_is_assignable "$(mac_read_link_address "${BACKUP_PREFIX}")"; then
    RESTORE_FROM="${BACKUP_PREFIX}"
  fi
  if [ -n "$RESTORE_FROM" ]; then
    if cp "$RESTORE_FROM" "$LINK_FILE"; then
      logger -p local0.crit "[$tag:$LINENO] [$IFACE] restored from backup: $RESTORE_FROM"
    else
      logger -p local0.emerg "[$tag:$LINENO] [$IFACE] restore failed: $RESTORE_FROM -> $LINK_FILE"
    fi
  else
    logger -p local0.emerg "[$tag:$LINENO] [$IFACE] no valid backup available: ${BACKUP_PREFIX}.1"
  fi
  exit 1
fi
