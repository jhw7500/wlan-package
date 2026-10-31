#!/bin/bash
# wifi_services_lib.sh
#
# wifi_init.service가 통제하는 자식 unit 목록을 단일 정의.
# 실제 가동 여부는 운영자의 systemctl enable/disable이 결정한다.
#
# 비무선 로거는 MFG 프로파일에서도 JSON 정책에 따라 시작한다. 나머지 목록은
# MFG 모드에서 기동하지 않는 STA/FW 연계 서비스다.
#
# 제외 정책 (목록에서 빠진 것들):
#   - switchd, journald-snapshot.timer, fake-hwclock.timer, log-watchdog.timer  : wifi 외 도메인
#   - wifi_led@mlan0/mlan1                                                       : LED는 별도 정책
#   - wifi_logger@eth0 외 eth0 관련 wifi_* 인스턴스                              : 무선과 무관

wifi_services_non_wireless_list() {
    # systemd-timesyncd: 배포판 유닛, .global.ntp.enabled 토글(wifi_apply_enabled.sh)로 조건부 기동.
    # 부팅 중 wifi_apply_enabled 가 enable 한 직후에는 sysinit 이 이미 지나 스스로 뜨지 않으므로
    # 여기서 start 한다. 무선과 무관하므로 MFG 프로파일에서도 기동한다.
    printf '%s\n' wifi_logger.service wifi_logger@eth0.service systemd-timesyncd.service
}

wifi_services_wireless_list() {
    local svcs=()
    svcs+=(wifi_ping_monitor.service)
    svcs+=(wlan_fw_watch.service)   # 드라이버 wedge 감시 — .global.fw_watch.enabled 토글
    svcs+=(snmpd.service)   # 글로벌 SNMP 데몬 — .snmp.enabled 토글(wifi_apply_enabled.sh)로 조건부 기동
    svcs+=(opcd.service)    # OPC 제어 데몬 — .opc.enabled 토글(wifi_apply_enabled.sh)로 조건부 기동
    svcs+=(wifi_mgmt_log.timer wifi_thermal_state.timer)
    local iface
    for iface in mlan0 mlan1; do
        svcs+=("wpa_supplicant@${iface}.service")
        svcs+=("wifi_logger@${iface}.service")
        svcs+=("wifi_checker@${iface}.service")
        svcs+=("wifi_event@${iface}.service")
        svcs+=("wifi_raw_commit@${iface}.path")
        svcs+=("wifi_bridge@${iface}.service")
        svcs+=("wifi_arping@${iface}.service")
        svcs+=("wifi_bgscan@${iface}.service")
        svcs+=("wifi_roam@${iface}.service")
        svcs+=("wifi_periodic_roam@${iface}.service")
    done
    printf '%s\n' "${svcs[@]}"
}

wifi_services_list() {
    wifi_services_non_wireless_list
    wifi_services_wireless_list
}
