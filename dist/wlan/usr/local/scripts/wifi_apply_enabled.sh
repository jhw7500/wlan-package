#!/bin/bash
# wifi_apply_enabled.sh
#
# JSON wifi_init_conf.json의 데몬 *.enabled 키를 읽어 systemctl enable/disable
# 상태를 동기화한다. 실제 start/stop은 wifi_services.sh가 처리한다.
#
# wifi_init.service의 ExecStartPre로 호출되어 wifi_init.sh 실행 전에 systemd
# enable 상태를 JSON과 일치시킨 다음, ExecStartPost의 wifi_services.sh가
# enable된 unit만 일괄 start하는 흐름을 구성한다.
#
# 매핑 (JSON 키 → systemd unit):
#   .global.ping_monitor.enabled              → wifi_ping_monitor.service
#   .mlan0.net_rx + .mlan1.net_rx > 0         → wifi_mgmt_log.timer
#   .wbridge.thermal.enabled                  → wifi_thermal_state.timer
#   .mlanN.enabled (인터페이스 자체)          → wpa_supplicant@mlanN.service
#   .mlanN.logger.enabled                     → wifi_logger@mlanN.service
#   .mlanN.checker.enabled                    → wifi_checker@mlanN.service
#   .mlanN.bgscan.enabled                     → wifi_bgscan@mlanN.service
#   .mlanN.roaming.enabled                    → wifi_roam@mlanN.service
#   .mlanN.periodic_roam.enabled              → wifi_periodic_roam@mlanN.service
#   .mlanN.arping.enabled                     → wifi_arping@mlanN.service
#   (.mlanN.on_connect.enabled OR .mcs_tier.enabled) → wifi_event@mlanN.service
#   (.wbridge.enabled AND .wbridge.bridge_iface == mlanN) → wifi_bridge@mlanN.service
#
# 관리 외 (운영자가 systemctl로 직접): wifi_arping@*, wifi_capture@*,
# wifi_led@*, wifi_ping@*, wifi_logger.service(글로벌)
#
set -u

JSON="${WIFI_INIT_CONF_JSON:-/usr/local/etc/wifi_init_conf.json}"
tag="wifi_apply_enabled"

# 처리 결과 누적
ENABLED_UNITS=()
DISABLED_UNITS=()
FAILED_UNITS=()

[ -f "$JSON" ] || { logger -p local0.warn "[$tag:$LINENO] $JSON not found, skip"; exit 0; }
command -v jq >/dev/null 2>&1 || { logger -p local0.warn "[$tag:$LINENO] jq not available, skip"; exit 0; }

# JSON parse 검증 — 실패 시 즉시 중단. 그렇지 않으면 모든 키가 null로 평가되어
# default 값(logger/checker는 true)이 운영자 의도를 덮어쓸 수 있다.
if ! jq empty "$JSON" 2>/dev/null; then
    logger -p local0.crit "[$tag:$LINENO] CRITICAL: $JSON parse failed — refusing to apply (would overwrite operator intent with defaults)"
    printf '[%s:%s] CRITICAL: %s parse failed — aborted\n' "$tag" "$LINENO" "$JSON" >&2
    exit 1
fi

normalize_bool() {
    case "${1:-}" in
        true|TRUE|True|1|yes|YES|Yes) printf 'true\n' ;;
        *) printf 'false\n' ;;
    esac
}

# get_bool <jq_path> <default>
# jq의 // 연산자는 false도 falsey로 취급하여 default를 덮어쓰는 문제가 있어,
# null일 때만 default를 적용하는 안전 버전. false 값은 그대로 보존.
get_bool() {
    local path="$1" def="$2" v
    v=$(jq -r "if ${path} == null then \"${def}\" else (${path} | tostring) end" "$JSON" 2>/dev/null)
    [ -z "$v" ] && v="$def"
    normalize_bool "$v"
}

# apply <unit> <want_true_or_false>
# 현재 systemctl enable 상태와 want가 다를 때만 enable/disable 호출.
apply() {
    local unit="$1" want="$2"
    local cur="false"
    systemctl is-enabled --quiet "$unit" 2>/dev/null && cur="true"
    if [ "$want" = "true" ] && [ "$cur" = "false" ]; then
        logger -p local0.info "[$tag:$LINENO] enable $unit"
        if systemctl enable "$unit" 2>/dev/null; then
            ENABLED_UNITS+=("$unit")
        else
            FAILED_UNITS+=("enable:$unit")
            logger -p local0.err "[$tag:$LINENO] enable $unit failed"
        fi
    elif [ "$want" = "false" ] && [ "$cur" = "true" ]; then
        logger -p local0.info "[$tag:$LINENO] disable $unit"
        if systemctl disable "$unit" 2>/dev/null; then
            DISABLED_UNITS+=("$unit")
        else
            FAILED_UNITS+=("disable:$unit")
            logger -p local0.err "[$tag:$LINENO] disable $unit failed"
        fi
    fi
}

# 글로벌 데몬
apply wifi_ping_monitor.service     "$(get_bool ".global.ping_monitor.enabled" "false")"
apply wifi_thermal_state.timer      "$(get_bool ".wbridge.thermal.enabled"     "false")"

# wifi_mgmt_log.timer: net_rx 값이 어느 한 쪽이라도 양수면 enable
_m0_rx=$(jq -r '.mlan0.net_rx // 0' "$JSON")
_m1_rx=$(jq -r '.mlan1.net_rx // 0' "$JSON")
_mgmt="false"
if [ "${_m0_rx:-0}" -gt 0 ] 2>/dev/null || [ "${_m1_rx:-0}" -gt 0 ] 2>/dev/null; then
    _mgmt="true"
fi
apply wifi_mgmt_log.timer "$_mgmt"

# wbridge 정책
WBRIDGE_ENABLED=$(get_bool ".wbridge.enabled" "false")
BRIDGE_IFACE=$(jq -r '.wbridge.bridge_iface // "mlan0"' "$JSON")

# per-iface 데몬
for iface in mlan0 mlan1; do
    iface_en=$(get_bool ".${iface}.enabled" "false")
    if [ "$iface_en" = "false" ]; then
        logger -p local0.info "[$tag:$LINENO] [$iface] disabled → all child units disable"
        for u in wpa_supplicant wifi_logger wifi_checker wifi_event \
                 wifi_bridge wifi_bgscan wifi_roam wifi_periodic_roam wifi_arping; do
            apply "${u}@${iface}.service" "false"
        done
        continue
    fi

    apply "wpa_supplicant@${iface}.service" "true"

    apply "wifi_logger@${iface}.service"        "$(get_bool ".${iface}.logger.enabled"        "true")"
    apply "wifi_checker@${iface}.service"       "$(get_bool ".${iface}.checker.enabled"       "true")"
    apply "wifi_bgscan@${iface}.service"        "$(get_bool ".${iface}.bgscan.enabled"        "false")"
    apply "wifi_roam@${iface}.service"          "$(get_bool ".${iface}.roaming.enabled"       "false")"
    apply "wifi_arping@${iface}.service"        "$(get_bool ".${iface}.arping.enabled"        "false")"
    apply "wifi_periodic_roam@${iface}.service" "$(get_bool ".${iface}.periodic_roam.enabled" "false")"

    # wifi_event: on_connect 또는 mcs_tier 둘 중 하나라도 enable이면 데몬 enable
    _oc=$(get_bool ".${iface}.on_connect.enabled" "false")
    _mc=$(get_bool ".${iface}.mcs_tier.enabled"   "false")
    if [ "$_oc" = "true" ] || [ "$_mc" = "true" ]; then
        apply "wifi_event@${iface}.service" "true"
    else
        apply "wifi_event@${iface}.service" "false"
    fi

    # wifi_bridge: wbridge.enabled이고 bridge_iface가 이 iface일 때만 enable
    if [ "$WBRIDGE_ENABLED" = "true" ] && [ "$BRIDGE_IFACE" = "$iface" ]; then
        apply "wifi_bridge@${iface}.service" "true"
    else
        apply "wifi_bridge@${iface}.service" "false"
    fi
done

# enable/disable 결과를 systemd가 즉시 인식하도록 reload
systemctl daemon-reload 2>/dev/null || true

# 처리 요약 — logger (local0 → /var/log/cantops)
_log_summary() {
    local msg="$1"
    # BASH_LINENO[0] = 헬퍼 호출자의 라인 (헬퍼 내부 라인이 아닌, summary가 어디서 났는지)
    local ln=${BASH_LINENO[0]}
    logger -p local0.info "[$tag:$ln] $msg"
}

_log_summary "summary: enabled=${#ENABLED_UNITS[@]} disabled=${#DISABLED_UNITS[@]} failed=${#FAILED_UNITS[@]}"
if [ "${#ENABLED_UNITS[@]}" -gt 0 ]; then
    _log_summary "  ENABLED  : ${ENABLED_UNITS[*]}"
fi
if [ "${#DISABLED_UNITS[@]}" -gt 0 ]; then
    _log_summary "  DISABLED : ${DISABLED_UNITS[*]}"
fi
if [ "${#FAILED_UNITS[@]}" -gt 0 ]; then
    _log_summary "  FAILED   : ${FAILED_UNITS[*]}"
fi
if [ "${#ENABLED_UNITS[@]}" -eq 0 ] && [ "${#DISABLED_UNITS[@]}" -eq 0 ] && [ "${#FAILED_UNITS[@]}" -eq 0 ]; then
    _log_summary "  no change (all units already in desired state)"
fi
