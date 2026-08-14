#!/bin/bash
tag=$(basename "$0")

#logger -p local0.info "[$tag:$LINENO] start"

WIFI_INIT_CONF_JSON="/usr/local/etc/wifi_init_conf.json"
BOARD_TYPE="imx8mm"
if [ -f "$WIFI_INIT_CONF_JSON" ] && command -v jq >/dev/null 2>&1; then
    BOARD_TYPE=$(jq -r '.global.BOARD_TYPE // "imx8mm"' "$WIFI_INIT_CONF_JSON")
fi

if [ "$BOARD_TYPE" == "imx93" ]; then
    CHIP=1; OFF=13
    PMIC_REGMAP="/sys/kernel/debug/regmap/*-0025/registers"
    PMIC_REG="04:"; PMIC_RUN="04"
else
    CHIP=1; OFF=9
    PMIC_REGMAP="/sys/kernel/debug/regmap/0-004b/registers"
    PMIC_REG="2d:"; PMIC_RUN="80"
fi

ACTIVE_LOW=1
SHORT_MIN=0
SHORT_MAX=3
LONG_MIN=10
LED="/sys/class/leds/5VREG_nEN/brightness"
LOGKMSG="/dev/kmsg"

state=$(cat $PMIC_REGMAP 2>/dev/null | grep "$PMIC_REG" | awk '{print $2}')
#sleep 5
logger -p local0.info "[$tag:$LINENO] switchd ready : 0x$state"
/usr/local/logger/print.py cyan "switchd ready : 0x$state"

fifo=/run/switchd.gpio
[ -p "$fifo" ] || { rm -f "$fifo"; mkfifo -m 600 "$fifo"; }

log() { echo "<6>[switchd] $*" > "$LOGKMSG"; }

# gpiomon을 백그라운드로 띄워 FIFO에 씁니다.
# BusyBox에 stdbuf가 없을 수 있으니 포맷 자체를 짧게 고정(-F)해서 버퍼링 이슈 최소화
gpiomon -c "$CHIP" -e both -b pull-up -p 50ms -F '%E %c %o' "$OFF" >"$fifo" &
GMON_PID=$!

cleanup() {
    local rc=$?
    trap - INT TERM EXIT
    kill "$GMON_PID" 2>/dev/null || true
    rm -f -- "$fifo"
    exit "$rc"
}
trap cleanup INT TERM EXIT

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
                /usr/local/logger/print.py green "press"
                #echo "press" > /dev/console
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
                        state=$(cat $PMIC_REGMAP 2>/dev/null | grep "$PMIC_REG" | awk '{print $2}')
                        if [ "$state" == "$PMIC_RUN" ]; then
                            logger -p local0.emerg "[$tag:$LINENO] short press: power off : 0x$state"
                            #echo "short press: power off : 0x$state" > /dev/console
                            #echo "short press: power off : 0x$state" > /dev/kmsg
                            /usr/local/logger/print.py red "short press: power off : 0x$state"
                            /usr/local/scripts/journald_snapshot.sh
                            sleep 0.2
                            [ -e "$LED" ] && echo 0 > "$LED"
                            [ -e "$LED" ] && echo 1 > "$LED"
                        else
                            logger -p local0.emerg "[$tag:$LINENO] state is not RUN : 0x$state"
                            echo "state is not RUN : 0x$state" > /dev/console
                        fi
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
        #[ $elapsed -gt 0 ] && logger -p local0.info "[$tag:$LINENO] press (${elapsed}s)"
        #[ $elapsed -gt 0 ] && /usr/local/logger/print.py green "press (${elapsed}s)"        
        # 10초 도달 즉시
        if [ $elapsed -ge $LONG_MIN ] && [ $fired_long -eq 0 ]; then
            #logger -p local0.info "[$tag:$LINENO] long-press action fired (>=${LONG_MIN}s)"
            logger -p local0.emerg "[$tag:$LINENO] long press triggered"
            fired_long=1
            # Factory Reset이 검증과 reboot를 단독 소유한다. 실패 rc를 무시하고 여기서
            # 강제 reboot하면 불완전한 WPA/network/FW 상태로 부팅될 수 있다.
            if ! /usr/local/scripts/factory_reset.sh; then
                logger -p local0.emerg "[$tag:$LINENO] factory reset failed; reboot inhibited"
                /usr/local/logger/print.py red "factory reset failed; reboot inhibited"
                exit 1
            fi
            exit 0
        fi
    fi
done
