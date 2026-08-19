#!/bin/bash
# wbridge peer 채널 네트워크 산출물 재적용 (멱등)
#   - eth0 대표주소 = 무선 IP 미러(/32, 첫 주소) + 관리 IP(22-eth0.network) 후순위 재배치
#   - peer host route(<peer>/32 dev eth0 src <무선IP>) + permanent neigh
#     (발견 결과 /tmp/eth0_client_ip·_client_mac — tmpfs, 부팅마다 초기화)
#
# 게이트: wbridge.peer_route.enabled=true 또는 wbridge.moal.local_hairpin=1
#   (peer_route는 key invalid/missing 시 factory default true — wifi_init.sh와 동일 규칙)
#
# 호출 경로 (모두 동일 스크립트 — 단일 소스):
#   1) wifi_init.sh — 부팅 시 주소 부여 직후
#   2) wlan-peer-net.service — PartOf=systemd-networkd.service 로 networkd restart 추종.
#      networkd는 restart/reconfigure 시 foreign 주소·라우트를 flush하므로(실측 2026-07-18,
#      systemd 254: /32 미러·host route·permanent neigh 모두 소멸) 재적용이 필수다.
#      restart는 코드 fallback(wifi_init.sh/wifi_config.sh)과 수동 명령(wifi net restart)
#      양쪽에서 실사용된다. networkctl reload는 무해(실측), reconfigure eth0은 코드
#      미사용(수동 시 이 스크립트를 직접 실행해 복구).
#
# src 고정/neigh 고정 이유: docs/wifi_init_conf_guide.md 및 wired_mac_ip_get.py 주석 참조
# (커널 첫-주소 src 선택, arp_announce=2 sender 광고, off-subnet sender ARP 무시 peer).

tag="wifi_peer_net_reapply"
WIFI_INIT_CONF_JSON="${WIFI_INIT_CONF_JSON:-/usr/local/etc/wifi_init_conf.json}"

_pr=false
_lhp=""
if command -v jq >/dev/null 2>&1 && [ -f "$WIFI_INIT_CONF_JSON" ]; then
    _v=$(jq -r '.wbridge.peer_route.enabled' "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    case "$_v" in
        true|false) _pr="$_v" ;;
        *)          _pr=true ;;  # config 정상 + key invalid/missing → factory default
    esac
    _lhp=$(jq -r '.wbridge.moal.local_hairpin // empty' "$WIFI_INIT_CONF_JSON" 2>/dev/null)
fi
[ "$_pr" = "true" ] || [ "$_lhp" = "1" ] || exit 0
[ -r /etc/systemd/network/20-mlan0.network ] || exit 0
[ -d /sys/class/net/eth0 ] || exit 0

_m_addr=$(awk -F= '/^[[:space:]]*Address[[:space:]]*=/{gsub(/[[:space:]]/,"",$2); print $2; exit}' \
          /etc/systemd/network/20-mlan0.network)
_m_ip=${_m_addr%/*}
echo "$_m_ip" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' || exit 0

# 1) 미러 보장 + 관리 IP 후순위 재배치 (del→add 사이 networkd 선복구는 EEXIST로 무해)
ip addr replace "${_m_ip}/32" dev eth0 2>/dev/null
_e_addr=$(awk -F= '/^[[:space:]]*Address[[:space:]]*=/{gsub(/[[:space:]]/,"",$2); print $2; exit}' \
          /etc/systemd/network/22-eth0.network 2>/dev/null)
if [ -n "$_e_addr" ] && [ "${_e_addr%/*}" != "$_m_ip" ]; then
    ip addr del "$_e_addr" dev eth0 2>/dev/null
    ip addr add "$_e_addr" dev eth0 2>/dev/null
fi

# 2) peer host route + permanent neigh (발견 결과가 있을 때만)
_p_ip=$(cat /tmp/eth0_client_ip 2>/dev/null)
if echo "$_p_ip" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
    ip route replace "${_p_ip}/32" dev eth0 src "$_m_ip" 2>/dev/null \
        || logger -p local0.warn "[$tag:$LINENO] host route apply failed (${_p_ip}/32 src ${_m_ip})"
    _p_mac=$(cat /tmp/eth0_client_mac 2>/dev/null)
    if echo "$_p_mac" | grep -qiE '^([0-9a-f]{2}:){5}[0-9a-f]{2}$'; then
        ip neigh replace "$_p_ip" lladdr "$_p_mac" dev eth0 nud permanent 2>/dev/null
    fi
fi

logger -p local0.info "[$tag:$LINENO] applied: mirror=${_m_ip}/32 first, sub=${_e_addr:-<none>}, peer=${_p_ip:-<undiscovered>} (peer_route=$_pr local_hairpin=${_lhp:-0})"
exit 0
