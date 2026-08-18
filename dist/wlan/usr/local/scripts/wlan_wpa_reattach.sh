#!/bin/bash
# netdev 재생성 시 wpa_supplicant 재부착 — udev RUN 훅 (99-wlan-wpa-reattach.rules)
#
# 유닛 상태별로 처리가 달라 udev RUN 한 줄로는 표현할 수 없다. 세 경우 모두 다르게
# 다뤄야 하며, 하나라도 뭉뚱그리면 다른 결함이 된다:
#
#   active/activating — 좀비 라운드. 프로세스가 파괴된 구 ifindex 에 붙은 채 살아 있다.
#                       restart 로 재부착시킨다.
#   failed            — 죽는 라운드에서 Restart= 가 StartLimit 을 소진한 상태. 유닛의
#                       Requires=sys-subsystem-net-devices-%i.device 때문에 장치가 없는
#                       동안의 재기동은 매번 즉시 실패하고, RestartSec x StartLimitBurst
#                       (=30s) 안에 netdev 가 안 돌아오면 유닛이 failed 로 굳는다.
#                       failed 는 systemd 기준 "not running" 이라 try-restart/restart 가
#                       모두 no-op 이다(실측). reset-failed 로 카운터를 지운 뒤 start 해야
#                       살아난다.
#   inactive/그 외    — 부팅 첫 add(아직 wifi_init 이 기동 전)이거나, 운영자가 껐거나,
#                       wifi_init 의 모듈 재로드가 의도적으로 stop 한 상태(deactivating).
#                       건드리지 않는다.
#
# failed 분기만 start 를 쓰므로 여기서만 운영자 의도를 되살릴 위험이 있다 — is-enabled 로
# 게이트해 .mlanN.wpa_supplicant.enabled=false 로 disable 된 유닛은 부활시키지 않는다.
#
# udev 제약: man 7 udev 는 RUN 에서 파생된 프로세스가 "unconditionally killed after the
# event handling has finished" 라고 명시한다. 따라서 job 을 거는 호출은 반드시
# --no-block 으로 큐잉만 하고 즉시 반환한다(미지정 시 systemctl 은 기동 완료까지 대기하고,
# 이 유닛은 지금 생성 중인 device 유닛을 Requires 로 걸고 있어 워커가 멈춘다).
# reset-failed / is-* 는 job 을 만들지 않는 즉시 반환 질의라 --no-block 대상이 아니다.
#
# systemd-udevd 는 최소 PATH 로 실행하므로 절대 경로를 쓴다.
set -u

SYSTEMCTL=/usr/bin/systemctl
LOGGER=/usr/bin/logger
tag="wlan_wpa_reattach"

iface="${1:-}"
# udev 는 %k 로 실제 커널 이름만 넘기지만, 이 스크립트가 손으로 호출될 수도 있다.
# `mlan[0-9]*` 는 glob 이라 "mlan0; ..." 같은 값도 통과시킨다(변수는 인용돼 있어 인젝션은
# 아니지만 엉뚱한 유닛명을 만든다). 자릿수를 고정해 mlan0..mlan99 만 받는다.
case "$iface" in
    mlan[0-9]|mlan[0-9][0-9]) ;;
    *)
        "$LOGGER" -p local0.err "[$tag] invalid interface '${iface}'" 2>/dev/null || true
        exit 1
        ;;
esac

unit="wpa_supplicant@${iface}.service"
[ -x "$SYSTEMCTL" ] || exit 0

state=$("$SYSTEMCTL" is-active "$unit" 2>/dev/null) || true

case "$state" in
    active|activating)
        "$LOGGER" -p local0.info "[$tag] [$iface] netdev re-created while unit $state — restart to re-attach" 2>/dev/null || true
        "$SYSTEMCTL" --no-block restart "$unit" \
            || "$LOGGER" -p local0.err "[$tag] [$iface] restart $unit failed" 2>/dev/null || true
        ;;
    failed)
        # 운영자가 꺼둔 유닛은 되살리지 않는다 — 이 분기만 start 를 쓴다.
        if "$SYSTEMCTL" is-enabled --quiet "$unit" 2>/dev/null; then
            "$LOGGER" -p local0.warn "[$tag] [$iface] unit failed (start-limit exhausted while netdev was absent) — reset-failed + start" 2>/dev/null || true
            "$SYSTEMCTL" reset-failed "$unit" 2>/dev/null || true
            "$SYSTEMCTL" --no-block start "$unit" \
                || "$LOGGER" -p local0.err "[$tag] [$iface] start $unit failed" 2>/dev/null || true
        else
            "$LOGGER" -p local0.info "[$tag] [$iface] unit failed but disabled — leave stopped (operator intent)" 2>/dev/null || true
        fi
        ;;
    *)
        # inactive/deactivating/빈 값 — 부팅 전, 운영자 정지, 또는 재로드 중 의도적 stop.
        :
        ;;
esac

exit 0
