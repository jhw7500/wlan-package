#!/bin/bash
# wifi_services_lib.sh
#
# wifi_init.service가 통제하는 자식 unit 목록을 단일 정의.
# 실제 가동 여부는 운영자의 systemctl enable/disable이 결정한다.
#
# 제외 정책 (목록에서 빠진 것들):
#   - switchd, journald-snapshot.timer, fake-hwclock.timer, log-watchdog.timer  : wifi 외 도메인
#   - wifi_led@mlan0/mlan1                                                       : LED는 별도 정책
#   - wifi_logger.service (글로벌)                                               : per-iface logger와 별개로 운영
#   - eth0 관련 wifi_* 인스턴스                                                  : 무선과 무관

wifi_services_list() {
    local svcs=()
    svcs+=(wifi_ping_monitor.service)
    svcs+=(snmpd.service)   # 글로벌 SNMP 데몬 — .snmp.enabled 토글(wifi_apply_enabled.sh)로 조건부 기동
    svcs+=(opcd.service)    # OPC 제어 데몬 — .opc.enabled 토글(wifi_apply_enabled.sh)로 조건부 기동
    svcs+=(wifi_mgmt_log.timer wifi_thermal_state.timer)
    local iface
    for iface in mlan0 mlan1; do
        svcs+=("wpa_supplicant@${iface}.service")
        svcs+=("wifi_logger@${iface}.service")
        svcs+=("wifi_checker@${iface}.service")
        svcs+=("wifi_event@${iface}.service")
        svcs+=("wifi_bridge@${iface}.service")
        svcs+=("wifi_arping@${iface}.service")
        svcs+=("wifi_bgscan@${iface}.service")
        svcs+=("wifi_roam@${iface}.service")
        svcs+=("wifi_periodic_roam@${iface}.service")
    done
    printf '%s\n' "${svcs[@]}"
}
