#!/bin/bash
# write_mac.sh <iface> <mac>
# base MAC을 기록:
#   1. wifi_init_conf.json (.mac.<iface>.base)
#   2. .link 파일 (새로 씀)
#   3. .bak 파일 (새로 씀)

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
# shellcheck source=./mac_link_lib.sh
. "$SCRIPT_DIR/mac_link_lib.sh"

tag=$(basename "$0")
IFACE="${1:-}"
NEW_MAC="${2:-}"
WIFI_INIT_CONF_JSON="${WIFI_INIT_CONF_JSON:-/usr/local/etc/wifi_init_conf.json}"
NETWORK_DIR="${SYSTEMD_NETWORK_DIR:-/etc/systemd/network}"

case "$IFACE" in
  eth0)  LINK_FILE="$NETWORK_DIR/22-eth0.link"  ;;
  mlan0) LINK_FILE="$NETWORK_DIR/20-mlan0.link" ;;
  mlan1) LINK_FILE="$NETWORK_DIR/21-mlan1.link" ;;
  *)
    logger -p local0.emerg "[$tag:$LINENO] [$IFACE] interface is wrong!!"
    exit 1
    ;;
esac

BACKUP_FILE="${LINK_FILE}.bak"
if ! mac_acquire_iface_lock "$NETWORK_DIR" "$IFACE"; then
  logger -p local0.err "[$tag:$LINENO] [$IFACE] failed to acquire MAC update lock"
  exit 1
fi

# 임시파일 정리는 EXIT trap 하나로 모은다. bash의 trap은 누적이 아니라 교체라,
# 구간마다 trap을 다시 걸면 앞의 것이 무효화되어 그 임시파일이 영영 남는다
# (실제로 타깃 /etc/systemd/network에 .link.tmp.* 고아가 쌓여 있었다).
tmp=""
tmp_bak=""
tmp_json=""
trap 'rm -f -- ${tmp:+"$tmp"} ${tmp_bak:+"$tmp_bak"} ${tmp_json:+"$tmp_json"}' EXIT

# MAC 유효성 검사
if ! mac_is_assignable "$NEW_MAC"; then
  logger -p local0.err "[$tag:$LINENO] [$IFACE] invalid MAC: '$NEW_MAC'"
  exit 1
fi
NEW_MAC=$(mac_normalize "$NEW_MAC")

CONFLICT_LINK=$(mac_find_link_conflict "$NETWORK_DIR" "$LINK_FILE" "$NEW_MAC" || true)
if [ -n "$CONFLICT_LINK" ]; then
  logger -p local0.err "[$tag:$LINENO] [$IFACE] MAC conflict: $NEW_MAC already used by $CONFLICT_LINK"
  exit 1
fi

# 1. wifi_init_conf.json에 MAC 기록
if [ -f "$WIFI_INIT_CONF_JSON" ] && command -v jq >/dev/null 2>&1; then
    tmp_json="$(mktemp "${WIFI_INIT_CONF_JSON}.tmp.XXXXXX")" || {
      logger -p local0.err "[$tag:$LINENO] [$IFACE] failed to create JSON temp file"
      exit 1
    }
    if ! jq --arg v "$NEW_MAC" ".mac.${IFACE}.base = \$v" \
        "$WIFI_INIT_CONF_JSON" > "$tmp_json" \
        || ! mv "$tmp_json" "$WIFI_INIT_CONF_JSON"; then
      logger -p local0.err "[$tag:$LINENO] [$IFACE] failed to update JSON"
      exit 1
    fi
    tmp_json=""
    logger -p local0.info "[$tag:$LINENO] [$IFACE] Written MAC to JSON: $NEW_MAC"
else
    logger -p local0.warn "[$tag:$LINENO] [$IFACE] JSON update skipped (jq or config not found)"
fi

# link 파일 내용 생성 함수
link_content() {
  printf '[Match]\nOriginalName=%s\n\n[Link]\nMACAddress=%s\n' "$IFACE" "$NEW_MAC"
}

# 2. .link 파일 새로 쓰기
if [ -f "$LINK_FILE" ]; then
  tmp="$(mktemp "${LINK_FILE}.tmp.XXXXXX")"
  link_content > "$tmp"
  if [ "$(id -u)" -eq 0 ]; then
    install -o root -g root -m 0644 "$tmp" "$LINK_FILE"
  else
    install -m 0644 "$tmp" "$LINK_FILE"
  fi || {
    logger -p local0.err "[$tag:$LINENO] [$IFACE] failed to write $LINK_FILE"
    exit 1
  }
  logger -p local0.info "[$tag:$LINENO] [$IFACE] Written $LINK_FILE → $NEW_MAC"
else
  logger -p local0.warn "[$tag:$LINENO] [$IFACE] link file not found: $LINK_FILE"
fi

# 3. .bak 파일 새로 쓰기
tmp_bak="$(mktemp "${BACKUP_FILE}.tmp.XXXXXX")"
link_content > "$tmp_bak"
if [ "$(id -u)" -eq 0 ]; then
  install -o root -g root -m 0644 "$tmp_bak" "$BACKUP_FILE"
else
  install -m 0644 "$tmp_bak" "$BACKUP_FILE"
fi || {
  logger -p local0.err "[$tag:$LINENO] [$IFACE] failed to write $BACKUP_FILE"
  exit 1
}
logger -p local0.info "[$tag:$LINENO] [$IFACE] Written $BACKUP_FILE → $NEW_MAC"
