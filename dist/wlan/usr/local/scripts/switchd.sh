#!/bin/sh
tag=$(basename "$0")

logger -p local0.info "[$tag:$LINENO] start"

CHIP=1
OFF=9
ACTIVE_LOW=1
SHORT_MIN=0
SHORT_MAX=3
LONG_MIN=10
LED="/sys/class/leds/5VREG_nEN/brightness"
LOGKMSG="/dev/kmsg"

sleep 5
logger -p local0.emerg "[$tag:$LINENO] switchd ready!!"

fifo=/run/switchd.gpio
[ -p "$fifo" ] || { rm -f "$fifo"; mkfifo -m 600 "$fifo"; }

log() { echo "<6>[switchd] $*" > "$LOGKMSG"; }

# gpiomon을 백그라운드로 띄워 FIFO에 씁니다.
# BusyBox에 stdbuf가 없을 수 있으니 포맷 자체를 짧게 고정(-F)해서 버퍼링 이슈 최소화
gpiomon -c "$CHIP" -e both -b pull-up -p 50ms -F '%E %c %o' "$OFF" >"$fifo" &
GMON_PID=$!

trap 'kill $GMON_PID 2>/dev/null; rm -f "$fifo"; exit 0' INT TERM EXIT

pressed=0; press_t=0; fired_long=0; elapsed=0

while :; do
    # 이벤트 한 줄을 1초 타임아웃으로 읽는다. (없으면 heartbeat만 찍고 계속)
    if read -r -t 1 line <"$fifo"; then
        # 예: "rising gpiochip1 9" 또는 "falling gpiochip1 9"
        set -- $line
        edge="$1"
        case "$edge" in
            rising)  is_press=$([ $ACTIVE_LOW -eq 0 ] && echo 1 || echo 0) ;;
            falling) is_press=$([ $ACTIVE_LOW -eq 1 ] && echo 1 || echo 0) ;;
            *)       continue ;;
        esac

        now=$(date +%s)

        if [ "$is_press" -eq 1 ]; then
            if [ $pressed -eq 0 ]; then
                pressed=1; press_t=$now; fired_long=0; elapsed=0
                #log "press"
                logger -p local0.info "[$tag:$LINENO] press"
            fi
        else
            if [ $pressed -eq 1 ]; then
                pressed=0
                dur=$((now - press_t))
                logger -p local0.info "[$tag:$LINENO] release (${dur}s)"
                if [ $fired_long -eq 1 ]; then
                    logger -p local0.info "[$tag:$LINENO] long already fired; ignore release"
                else
                    if [ $dur -ge $SHORT_MIN ] && [ $dur -le $SHORT_MAX ]; then
                        # short 동작: 전원 OFF 전 로그를 확실히 남김
                        logger -p local0.emerg "[$tag:$LINENO] short press: power off 5VREG"
                        sync
                        sleep 0.5
                        [ -e "$LED" ] && echo 0 > "$LED"
                    fi
                fi
            fi
        fi
    fi

    # 프레스 중 1초마다 경과시간 로그 + 10초 즉시 long
    if [ $pressed -eq 1 ]; then
        now=$(date +%s)
        elapsed=$((now - press_t))
        # 1초마다
        [ $elapsed -gt 0 ] && logger -p local0.info "[$tag:$LINENO] press (${elapsed}s)"
        # 10초 도달 즉시
        if [ $elapsed -ge $LONG_MIN ] && [ $fired_long -eq 0 ]; then
            logger -p local0.info "[$tag:$LINENO] long-press action fired (>=${LONG_MIN}s)"
            logger -p local0.emerg "[$tag:$LINENO] long press triggered"
            sync
            fired_long=1
            # 예: 재부팅/특정 스크립트 실행 등
            # /usr/local/scripts/longpress.sh &
        fi
    fi
done
