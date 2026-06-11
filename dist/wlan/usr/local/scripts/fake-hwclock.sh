#!/bin/sh
tag=$(basename "$0")
#logger -p local0.info "[$tag:$LINENO] $1"
# rootfs 재플래시 후에도 시각이 이어지도록 영속 파티션에 저장.
# /etc(rootfs)에 두면 이미지를 다시 구울 때마다 시계가 기본값으로 리셋되어
# /var/log/cantops 날짜별 로그가 세션 간에 뒤섞인다.
# dotfile인 이유: logctl clean / factory_reset의 `rm /var/log/cantops/*`
# glob에 걸리지 않아 로그 정리 후에도 시계 상태가 살아남는다.
STATE=/var/log/cantops/.fake-hwclock.data
LEGACY_STATE=/etc/fake-hwclock.data
#echo "hwclock $1"
case "$1" in
  save)
    mkdir -p "${STATE%/*}"
    # tmp+mv 원자적 교체 — 쓰기 도중 전원 차단 시 상태 파일 손상 방지
    date +"%Y-%m-%d %H:%M:%S" > "${STATE}.tmp" && mv -f "${STATE}.tmp" "$STATE"
    ;;
  load)
    [ -f "$STATE" ] || STATE="$LEGACY_STATE"
    [ -f "$STATE" ] || exit 0
    DATE_STR="$(cat "$STATE")"
    # 저장 시각이 현재 시계보다 미래일 때만 적용 (시간 역행 방지)
    SAVED=$(date -d "$DATE_STR" +%s 2>/dev/null) || {
        logger -p local0.warn "[$tag] invalid saved time, skip restore: '$DATE_STR'"
        exit 0
    }
    NOW=$(date +%s)
    if [ "$SAVED" -gt "$NOW" ]; then
        date -s "$DATE_STR"
    fi
    ;;
  *)
    echo "Usage: fake-hwclock {save|load}"
    exit 1
    ;;
esac
