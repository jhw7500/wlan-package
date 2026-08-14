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

# MFG 프로파일 판정 (SoT: mod_para.conf의 mfg_mode=)
_mfg_mode() {
    local mp="cts/wifi_mod_para.conf" j="${WIFI_INIT_CONF_JSON:-/usr/local/etc/wifi_init_conf.json}" m
    if command -v jq >/dev/null 2>&1 && [ -f "$j" ]; then
        mp=$(jq -r '.global.MOD_PARA // "cts/wifi_mod_para.conf"' "$j" 2>/dev/null) || mp="cts/wifi_mod_para.conf"
        [ -n "$mp" ] || mp="cts/wifi_mod_para.conf"
    fi
    m=$(grep -m1 '^[[:space:]]*mfg_mode=' "/lib/firmware/$mp" 2>/dev/null | sed 's/.*mfg_mode=//' | tr -d ' ')
    printf '%s\n' "${m:-0}"
}

_wifi_start_if_enabled() {
    local u="$1"
    if systemctl is-enabled --quiet "$u" 2>/dev/null; then
        logger -p local0.info "[$tag:$LINENO] start $u"
        systemctl start --no-block "$u" 2>/dev/null || \
            logger -p local0.err "[$tag:$LINENO] start $u failed"
    fi
}

wifi_services_start_non_wireless() {
    local u
    while IFS= read -r u; do
        _wifi_start_if_enabled "$u"
    done < <(wifi_services_non_wireless_list)
}

wifi_services_start_wireless() {
    local u
    while IFS= read -r u; do
        _wifi_start_if_enabled "$u"
    done < <(wifi_services_wireless_list)
}

cmd_start() {
    # wifi-stack.target가 이미 활성화된 뒤 ExecStartPre에서 enable 상태가 복구돼도
    # 이번 부팅에 즉시 기동되도록 system/eth0 로거를 먼저 시작한다.
    wifi_services_start_non_wireless

    # MFG 이중 안전장치: wifi_apply_enabled.sh의 MFG disable이 누락/실패해 enable이
    # 남아 있어도 MFG FW 위에서 STA 데몬이 기동되지 않도록 무선 목록을 건너뛴다.
    if [ "$(_mfg_mode)" = "1" ]; then
        logger -p local0.info "[$tag:$LINENO] mfg_mode=1 → skip wireless child unit start (MFG profile)"
        return 0
    fi
    wifi_services_start_wireless
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
