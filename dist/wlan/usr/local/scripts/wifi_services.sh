#!/bin/bash
# wifi_services.sh
#
# wifi_init.service의 ExecStartPost로 호출되어 enable된 자식 unit을 start한다.
# 자식 unit은 PartOf=wifi_init.service로 stop 전파를 받으므로 이 스크립트는 start만
# 책임진다 (stop/restart 서브커맨드는 수동 운영용으로 제공).
#
# 정책:
#   - 가동 결정 (enable/disable)은 운영자의 systemctl 상태가 진실
#   - 이 스크립트는 enable된 unit만 start --no-block 호출
#
set -u

LIB="/usr/local/scripts/wifi_services_lib.sh"
# shellcheck source=./wifi_services_lib.sh
. "$LIB"

tag="wifi_services"

cmd_start() {
    local u
    while IFS= read -r u; do
        if systemctl is-enabled --quiet "$u" 2>/dev/null; then
            logger -p local0.info "[$tag] start $u"
            systemctl start --no-block "$u" 2>/dev/null || \
                logger -p local0.err "[$tag] start $u failed"
        fi
    done < <(wifi_services_list)
}

cmd_stop() {
    local u
    while IFS= read -r u; do
        if systemctl is-active --quiet "$u" 2>/dev/null; then
            systemctl stop "$u" 2>/dev/null || true
        fi
    done < <(wifi_services_list)
}

cmd_enabled() {
    local u
    while IFS= read -r u; do
        systemctl is-enabled --quiet "$u" 2>/dev/null && printf '%s\n' "$u"
    done < <(wifi_services_list)
}

case "${1:-}" in
    start)   cmd_start ;;
    stop)    cmd_stop ;;
    restart) cmd_stop; cmd_start ;;
    list)    wifi_services_list ;;
    enabled) cmd_enabled ;;
    *)
        echo "Usage: $0 {start|stop|restart|list|enabled}" >&2
        exit 1
        ;;
esac
