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
#   .global.fw_watch.enabled                  → wlan_fw_watch.service
#   .mlan0.net_rx + .mlan1.net_rx > 0         → wifi_mgmt_log.timer
#   .wbridge.thermal.enabled                  → wifi_thermal_state.timer
#   .snmp.enabled                             → snmpd.service (배포판 net-snmp 유닛)
#   .opc.enabled                              → opcd.service (wlan-opc OPC 제어 데몬)
#   .logger.enabled                           → wifi_logger.service (시스템 로거 그룹)
#   .eth0.logger.enabled                      → wifi_logger@eth0.service
#   .mlanN.wpa_supplicant.enabled             → wpa_supplicant@mlanN.service
#   .mlanN.logger.enabled                     → wifi_logger@mlanN.service
#   .mlanN.checker.enabled                    → wifi_checker@mlanN.service
#   .mlanN.bgscan.enabled                     → wifi_bgscan@mlanN.service
#   .mlanN.roaming.enabled                    → wifi_roam@mlanN.service
#   .mlanN.periodic_roam.enabled              → deprecated; wifi_periodic_roam@mlanN.service forced disabled
#   .mlanN.arping.enabled                     → wifi_arping@mlanN.service
#   (.mlanN.on_connect.enabled OR .snmp.trap.enabled OR
#    (AX iface AND .mlanN.mcs_tier.enabled AND HE configured))
#                                               → wifi_event@mlanN.service
#   (.wbridge.enabled AND .wbridge.bridge_iface == mlanN) → wifi_bridge@mlanN.service
#
# .mlanN.enabled=false 이면 위 mlanN 자식 유닛은 개별 키와 무관하게 전부 disable된다.
#
# 관리 외 (운영자가 systemctl로 직접): wifi_arping@*, wifi_capture@*,
# wifi_led@*, wifi_ping@*
#
set -u

SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
# 설정 가용성 판정(wifi_init_conf_status)을 wifi_init.sh 와 공유한다 — 각자 구현하면
# 파싱 실패 같은 사유가 한쪽에만 반영돼 두 스크립트의 판단이 갈린다.
# shellcheck source=/dev/null
. "$SCRIPT_DIR/wifi_init_config_lib.sh"

JSON="${WIFI_INIT_CONF_JSON:-/usr/local/etc/wifi_init_conf.json}"
tag="wifi_apply_enabled"
STRICT=0
case "${WIFI_APPLY_STRICT:-0}" in
    1|true|TRUE|yes|YES) STRICT=1 ;;
esac

# 처리 결과 누적
ENABLED_UNITS=()
DISABLED_UNITS=()
FAILED_UNITS=()

# 설정 가용성 — 판정은 공유(wifi_init_conf_status), 사유별 정책만 여기서 정한다.
# parse 실패는 skip이 아니라 중단이다: 모든 키가 null로 평가되어 default 값(logger/
# checker는 true)이 운영자 의도를 덮어쓴다.
wifi_init_conf_status "$JSON" && _conf_status=0 || _conf_status=$?
case "$_conf_status" in
    0) ;;
    1) logger -p local0.warn "[$tag:$LINENO] $JSON not found, skip"; exit "$STRICT" ;;
    2) logger -p local0.warn "[$tag:$LINENO] jq not available, skip"; exit "$STRICT" ;;
    *)
        logger -p local0.crit "[$tag:$LINENO] CRITICAL: $JSON parse failed — refusing to apply (would overwrite operator intent with defaults)"
        printf '[%s:%s] CRITICAL: %s parse failed — aborted\n' "$tag" "$LINENO" "$JSON" >&2
        exit 1
        ;;
esac

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

# owner/topology/bgscan enablement는 reboot 경계인 /run snapshot으로 고정한다.
# 같은 boot의 수동 재실행/daemon restart는 persisted JSON 변경을 반영하지 않는다.
WIFI_RUN_DIR="${WIFI_RUN_DIR:-/run/wifi}"
for _policy_iface in mlan0 mlan1; do
    if ! wifi_roam_policy_ensure_snapshot "$_policy_iface" "$JSON"; then
        logger -p local0.emerg "[$tag:$LINENO] cannot create/validate boot roam policy for $_policy_iface"
        printf '[%s] CRITICAL: invalid boot roam policy for %s\n' "$tag" "$_policy_iface" >&2
        exit 1
    fi
done

boot_policy_bool() {
    wifi_roam_policy_get_bool "$1" "$2"
}

# wifi_periodic_roam은 wifi_roam/wpa native와 겹치는 제3의 proactive owner라 더 이상
# 활성화하지 않는다. 호환을 위해 JSON 키는 읽되 true 요청은 경고하고 unit은 항상 false.
for _owner_iface in mlan0 mlan1; do
    if [ "$(get_bool ".${_owner_iface}.periodic_roam.enabled" "false")" = "true" ]; then
        _owner_msg="[${_owner_iface}] periodic_roam.enabled=true is deprecated and ignored; wifi_periodic_roam is forced disabled"
        logger -p local0.warning "[$tag:$LINENO] $_owner_msg"
        printf '[%s] WARNING: %s\n' "$tag" "$_owner_msg" >&2
    fi
done

# apply <unit> <want_true_or_false> [owner_disable_critical]
# 현재 systemctl enable 상태와 want가 다를 때만 enable/disable 호출.
apply() {
    local unit="$1" want="$2" owner_critical="${3:-false}"
    local cur="false" disable_failed=0
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
            disable_failed=1
            if [ "$owner_critical" = "true" ]; then
                OWNER_POLICY_FAILED=1
                logger -p local0.crit "[$tag:$LINENO] cannot disable disallowed owner $unit"
            fi
        fi
    fi
    # disable exit 0만으로 stale enable symlink 제거를 단정하지 않는다.
    if [ "$want" = "false" ] && [ "$owner_critical" = "true" ] \
       && [ "$disable_failed" -eq 0 ] \
       && systemctl is-enabled --quiet "$unit" 2>/dev/null; then
        OWNER_POLICY_FAILED=1
        FAILED_UNITS+=("postcheck-enabled:$unit")
        logger -p local0.crit "[$tag:$LINENO] disallowed owner remains enabled: $unit"
    fi
}

# disable은 target dependency로 이미 queued된 start job을 취소하지 않는다. owner로
# 허용되지 않은 unit은 enable 상태와 무관하게 stop하여 queued/active 양쪽을 닫는다.
OWNER_POLICY_FAILED=0
ensure_stopped_owner_unit() {
    local unit="$1"
    if systemctl stop "$unit" 2>/dev/null; then
        return 0
    fi
    OWNER_POLICY_FAILED=1
    FAILED_UNITS+=("stop:$unit")
    logger -p local0.crit "[$tag:$LINENO] stop/cancel disallowed owner $unit failed"
    return 1
}

# MFG 프로파일: mfg_mode=1(SoT: mod_para.conf)이면 STA/FW 접촉 유닛을 일괄
# disable+stop 하고 종료한다. MFG FW에서는 scan/connect가 동작하지 않아
# checker/roam 등이 오판 → 복구 사다리/재부팅 요청이 오발되므로 systemd 레벨에서
# 차단한다 (wifi_init.sh의 MFG 가드와 세트). disable ≠ stop이라 이미 떠 있는
# 유닛은 명시적으로 stop한다. mfg_mode=0 복귀 후 재실행되면 아래 정상 경로가
# JSON 기준으로 재-enable한다(자기치유).
_MOD_PARA=$(jq -r '.global.MOD_PARA // "cts/wifi_mod_para.conf"' "$JSON" 2>/dev/null) || _MOD_PARA="cts/wifi_mod_para.conf"
[ -n "$_MOD_PARA" ] || _MOD_PARA="cts/wifi_mod_para.conf"
MFG_MODE=$(grep -m1 '^[[:space:]]*mfg_mode=' "/lib/firmware/$_MOD_PARA" 2>/dev/null | sed 's/.*mfg_mode=//' | tr -d ' ' || echo "0")

# 시스템/유선 로거는 MFG STA 프로파일과 독립이다. MFG early-return보다 먼저
# 적용해야 wifi log system|eth0 enable/disable이 모든 모드에서 같은 의미를 가진다.
apply wifi_logger.service     "$(get_bool ".logger.enabled" "true")"
apply wifi_logger@eth0.service "$(get_bool ".eth0.logger.enabled" "true")"

if [ "${MFG_MODE:-0}" = "1" ]; then
    logger -p local0.info "[$tag:$LINENO] mfg_mode=1 → MFG profile: disable+stop STA/FW-touching units"
    MFG_UNITS=(wifi_ping_monitor.service wifi_thermal_state.timer wifi_mgmt_log.timer
               snmpd.service opcd.service wlan_fw_watch.service)
    for iface in mlan0 mlan1; do
        for u in wpa_supplicant wifi_logger wifi_checker wifi_event \
                 wifi_bridge wifi_bgscan wifi_roam wifi_periodic_roam wifi_arping; do
            MFG_UNITS+=("${u}@${iface}.service")
        done
    done
    for u in "${MFG_UNITS[@]}"; do
        case "$u" in
            wifi_bgscan@*.service|wifi_roam@*.service|wifi_periodic_roam@*.service)
                apply "$u" "false" "true"
                ;;
            *)
                apply "$u" "false"
                ;;
        esac
        # 무조건 stop — is-active 게이트는 After=wifi_init로 큐에 대기 중인 start/restart
        # job을 놓친다(그 시점 유닛은 inactive, disable은 큐된 job을 취소하지 못함).
        # stop은 job-mode=replace로 대기 job을 취소하며 inactive 유닛에는 무비용 no-op.
        # 유닛 파일 자체가 없는 선택 유닛(snmpd/opcd 미설치 이미지)은 큐 job도 존재할 수
        # 없으므로 skip — 존재하지 않는 유닛 stop의 err 오탐 로그 방지.
        if systemctl cat "$u" >/dev/null 2>&1; then
            case "$u" in
                wifi_bgscan@*.service|wifi_roam@*.service|wifi_periodic_roam@*.service)
                    ensure_stopped_owner_unit "$u" || true
                    ;;
                *)
                    systemctl stop "$u" 2>/dev/null || logger -p local0.err "[$tag:$LINENO] stop $u failed"
                    ;;
            esac
        fi
    done
    systemctl daemon-reload 2>/dev/null || true
    logger -p local0.info "[$tag:$LINENO] MFG profile applied: disabled=${#DISABLED_UNITS[@]} failed=${#FAILED_UNITS[@]}"
    if [ "$OWNER_POLICY_FAILED" -ne 0 ] \
       || { [ "$STRICT" -eq 1 ] && [ "${#FAILED_UNITS[@]}" -gt 0 ]; }; then
        exit 1
    fi
    exit 0
fi

# 글로벌 데몬
apply wifi_ping_monitor.service     "$(get_bool ".global.ping_monitor.enabled" "false")"
apply wlan_fw_watch.service         "$(get_bool ".global.fw_watch.enabled"     "true")"
apply wifi_thermal_state.timer      "$(get_bool ".wbridge.thermal.enabled"     "false")"
apply snmpd.service                 "$(get_bool ".snmp.enabled"                "false")"
apply opcd.service                  "$(get_bool ".opc.enabled"                 "false")"

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
SNMP_TRAP_ENABLED=$(get_bool ".snmp.trap.enabled" "false")

# per-iface 데몬
for iface in mlan0 mlan1; do
    iface_en=$(get_bool ".${iface}.enabled" "false")
    if [ "$iface_en" = "false" ]; then
        logger -p local0.info "[$tag:$LINENO] [$iface] disabled → all child units disable"
        for u in wpa_supplicant wifi_logger wifi_checker wifi_event \
                 wifi_bridge wifi_bgscan wifi_roam wifi_periodic_roam wifi_arping; do
            case "$u" in
                wifi_bgscan|wifi_roam|wifi_periodic_roam)
                    apply "${u}@${iface}.service" "false" "true"
                    ;;
                *)
                    apply "${u}@${iface}.service" "false"
                    ;;
            esac
        done
        ensure_stopped_owner_unit "wifi_bgscan@${iface}.service" || true
        ensure_stopped_owner_unit "wifi_roam@${iface}.service" || true
        ensure_stopped_owner_unit "wifi_periodic_roam@${iface}.service" || true
        continue
    fi

    apply "wpa_supplicant@${iface}.service"     "$(get_bool ".${iface}.wpa_supplicant.enabled" "true")"

    apply "wifi_logger@${iface}.service"        "$(get_bool ".${iface}.logger.enabled"        "true")"
    apply "wifi_checker@${iface}.service"       "$(get_bool ".${iface}.checker.enabled"       "true")"
    _bgscan_want=$(boot_policy_bool "$iface" bgscan_enabled) || exit 1
    _roam_want=$(boot_policy_bool "$iface" roaming_enabled) || exit 1
    if [ "$_bgscan_want" = "false" ]; then
        apply "wifi_bgscan@${iface}.service" "false" "true"
    else
        apply "wifi_bgscan@${iface}.service" "true"
    fi
    if [ "$_roam_want" = "false" ]; then
        apply "wifi_roam@${iface}.service" "false" "true"
    else
        apply "wifi_roam@${iface}.service" "true"
    fi
    apply "wifi_arping@${iface}.service"        "$(get_bool ".${iface}.arping.enabled"        "false")"
    apply "wifi_periodic_roam@${iface}.service" "false" "true"
    if [ "$_bgscan_want" = "false" ]; then
        ensure_stopped_owner_unit "wifi_bgscan@${iface}.service" || true
    fi
    if [ "$_roam_want" = "false" ]; then
        ensure_stopped_owner_unit "wifi_roam@${iface}.service" || true
    fi
    # deprecated third owner는 is-enabled=false/수동 active 상태까지 포함해 항상 중지.
    ensure_stopped_owner_unit "wifi_periodic_roam@${iface}.service" || true

    # wifi_event: on_connect 명령, SNMP 링크/채널 트랩, 또는 association 후 deferred
    # MCS 검증/1회 제한 복구가 필요하면 enable한다.
    _oc=$(get_bool ".${iface}.on_connect.enabled" "false")
    _mcs=$(get_bool ".${iface}.mcs_tier.enabled" "false")
    _standard=$(jq -r ".${iface}.STANDARD // \"\" | ascii_downcase" "$JSON" 2>/dev/null)
    _mcs_he=$(jq -r ".${iface}.mcs_tier.he // \"\"" "$JSON" 2>/dev/null)
    _mcs_verify="false"
    if [ "$_mcs" = "true" ] && { [ "$_standard" = "ax" ] || [ "$_standard" = "6" ]; } \
       && [ -n "$_mcs_he" ]; then
        _mcs_verify="true"
    fi
    if [ "$_oc" = "true" ] || [ "$SNMP_TRAP_ENABLED" = "true" ] || [ "$_mcs_verify" = "true" ]; then
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

if [ "$OWNER_POLICY_FAILED" -ne 0 ]; then
    exit 1
fi
if [ "$STRICT" -eq 1 ] && [ "${#FAILED_UNITS[@]}" -gt 0 ]; then
    exit 1
fi
