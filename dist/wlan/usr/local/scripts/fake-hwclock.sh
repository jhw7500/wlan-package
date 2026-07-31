#!/bin/bash
tag=$(basename "$0")
#logger -p local0.info "[$tag:$LINENO] $1"
# rootfs 재플래시 후에도 시각이 이어지도록 영속 파티션에 저장.
# /etc(rootfs)에 두면 이미지를 다시 구울 때마다 시계가 기본값으로 리셋되어
# /var/log/cantops 날짜별 로그가 세션 간에 뒤섞인다.
# dotfile인 이유: logctl clean / factory_reset의 `rm /var/log/cantops/*`
# glob에 걸리지 않아 로그 정리 후에도 시계 상태가 살아남는다.
# 기본은 영속 파티션. FAKE_HWCLOCK_STATE 로 덮어쓸 수 있다(테스트 주입용, 기본 불변).
STATE=${FAKE_HWCLOCK_STATE:-/var/log/cantops/.fake-hwclock.data}
LEGACY_STATE=${FAKE_HWCLOCK_LEGACY_STATE:-/etc/fake-hwclock.data}

# RTC·NTP가 모두 없는 환경에서도 "시계가 부팅 시 뒤로 가지 않는다"(단조성)를
# 보장하는 것이 이 스크립트의 핵심 계약이다. 시각이 뒤로 가면 날짜별 로그가
# 이전 세션과 같은 타임스탬프로 겹쳐(중복) 순서가 무너진다.
#   load : 저장값이 현재보다 미래일 때만 전진 적용(역행 금지).
#   save : 저장값 = '지금껏 본 최댓값'만 유지(forward-only). 부팅 직후 restore가
#          늦어 시계가 잠시 뒤처진 순간(예: epoch) 좋은 저장값을 덮지 않게 한다.
#          단, NTP로 검증된 시각은 권위라 뒤로 스텝(미래 오설정 교정)도 허용한다.
#   set  : 위 forward-guard가 스스로 못 벗어나는 상태(예: 미래 오설정 박제)를
#          벗어나는 수동 탈출구. NTP 없는 환경의 절대시각 교정 경로.

ntp_synced() {
    # timedatectl 부재/미동기화면 false. 동기화면 true.
    [ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" = "yes" ]
}

resolve_state_file() {
    # 존재하는 상태 파일 경로 출력(STATE 우선, 없으면 LEGACY). 둘 다 없으면 빈 출력.
    # load 와 saved_epoch 가 공유(폴백 로직 단일화).
    if [ -f "$STATE" ]; then
        echo "$STATE"
    elif [ -f "$LEGACY_STATE" ]; then
        echo "$LEGACY_STATE"
    fi
}

saved_epoch() (
    # 저장 시각을 epoch로 출력. 없거나 파싱실패면 빈 출력.
    # 함수 본문을 서브셸 ( )로 둬 내부 변수가 호출 스코프로 새지 않게 한다(POSIX 청결).
    f=$(resolve_state_file)
    [ -n "$f" ] || exit 0
    date -d "$(cat "$f" 2>/dev/null)" +%s 2>/dev/null
)

write_state() {
    # 실패(읽기전용/full/미마운트 파티션 등)를 조용히 삼키지 않고 저널에 남긴 뒤
    # non-zero 로 전파한다 — save 실패는 다음 부팅 시각 역행의 직접 원인이고, set 은
    # 실패를 성공으로 위장하면 안 되기 때문(호출부가 exit code 로 판단).
    mkdir -p "${STATE%/*}" 2>/dev/null || {
        logger -p local0.err "[$tag:$LINENO] mkdir failed: ${STATE%/*}"
        return 1
    }
    # tmp+mv 원자적 교체 — 쓰기 도중 전원 차단 시 상태 파일 손상 방지
    if date +"%Y-%m-%d %H:%M:%S" > "${STATE}.tmp" 2>/dev/null && mv -f "${STATE}.tmp" "$STATE" 2>/dev/null; then
        return 0
    fi
    logger -p local0.err "[$tag:$LINENO] failed to write state: $STATE"
    rm -f "${STATE}.tmp" 2>/dev/null
    return 1
}

case "$1" in
  save)
    NOW=$(date +%s)
    SAVED=$(saved_epoch)
    # forward-only: 현재가 저장값보다 앞설 때만 갱신. 저장값 없음(부트스트랩)이거나
    # NTP 동기화됨(권위)이면 무조건 갱신 → 미래 박제도 NTP가 교정 가능.
    if ntp_synced || [ -z "$SAVED" ] || [ "$NOW" -gt "$SAVED" ]; then
        write_state || exit 1
    fi
    ;;
  load)
    STATE_FILE=$(resolve_state_file)
    [ -n "$STATE_FILE" ] || exit 0
    DATE_STR="$(cat "$STATE_FILE")"
    SAVED=$(date -d "$DATE_STR" +%s 2>/dev/null) || {
        logger -p local0.warn "[$tag:$LINENO] invalid saved time, skip restore: '$DATE_STR'"
        exit 0
    }
    NOW=$(date +%s)
    # 저장 시각이 현재 시계보다 미래일 때만 적용 (시간 역행 방지).
    # date -s 의 성공 stdout(=설정된 날짜)은 저널 소음이라 버리되, 성공/실패는 명시적
    # logger 로 남긴다(리다이렉트로 둘 다 삼키면 restore 실패가 저널에서 사라진다).
    # restore 는 best-effort: 실패해도 exit 0 을 유지한다 — 부팅 시각 보정 실패가
    # sysinit 순서의 이 유닛을 fail 시켜 부팅을 저해하면 안 되기 때문(시계가 틀린 채로도
    # 부팅은 되어야 한다). root+검증된 DATE_STR 이라 실무상 err 경로는 사실상 도달 불가.
    if [ "$SAVED" -gt "$NOW" ]; then
        if date -s "$DATE_STR" >/dev/null 2>&1; then
            logger -p local0.info "[$tag:$LINENO] clock restored to '$DATE_STR'"
        else
            logger -p local0.err "[$tag:$LINENO] failed to set clock to '$DATE_STR'"
        fi
    fi
    ;;
  set)
    # 수동 절대시각 교정. date -s + 강제 저장(forward-guard 우회) + RTC write.
    # 표준 `date -s`만 쓰면 다음 save의 forward-guard가 되돌리므로, 반드시 이
    # 서브커맨드로 저장까지 함께 해야 교정이 영속된다.
    [ -n "$2" ] || { echo "Usage: $tag set '<YYYY-MM-DD HH:MM:SS>'"; exit 1; }
    date -s "$2" >/dev/null 2>&1 || { echo "invalid date: $2" >&2; exit 1; }
    # 저장 실패면 시계는 바뀌었어도 다음 부팅에 옛 값으로 되돌아간다 → 성공으로
    # 위장하지 않고 명확히 실패로 종료(사용자가 파티션 상태를 고치고 재시도하도록).
    write_state || { echo "ERROR: clock changed but state NOT persisted ($STATE)" >&2; exit 1; }
    [ -e /dev/rtc0 ] && hwclock -w 2>/dev/null
    echo "clock set to $(date +'%Y-%m-%d %H:%M:%S') (saved + rtc write attempted)"
    ;;
  *)
    echo "Usage: $tag {save|load|set '<YYYY-MM-DD HH:MM:SS>'}"
    exit 1
    ;;
esac
