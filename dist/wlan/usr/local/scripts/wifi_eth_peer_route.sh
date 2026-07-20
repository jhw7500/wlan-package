#!/bin/bash
# wifi_eth_peer_route.sh <ip> [<iface>] — peer host route 등록기 (순수 등록)
#
# peer_route 토폴로지에서 eth 지연연결 후, 부팅 때 누락된 peer host route
# (<peer>/32 dev eth0)를 사후 등록한다. wired_mac_ip_get.py 의
# apply_peer_host_route() 와 동일한 라우트를 발행하되, 수동/자동 재실행이 가능하다.
#
# <iface>(기본 mlan0)는 무-src 기본 경로에선 미사용이며, 향후 src-핀 옵션(src <mlanN_ip>)
# 을 켤 때만 쓰이는 예약 인자다.
#
# exit: 0=성공, 1=route 실패, 2=usage/인자 오류
set -u
tag=$(basename "$0")

ETH_IFACE="${ETH_IFACE:-eth0}"
WIFI_INIT_CONF_JSON="${WIFI_INIT_CONF_JSON:-/usr/local/etc/wifi_init_conf.json}"
ETH_CLIENT_IP_FILE="${ETH_CLIENT_IP_FILE:-/tmp/${ETH_IFACE}_client_ip}"

IP="${1:-}"
# shellcheck disable=SC2034  # 예약 인자: src-핀 옵션(§설계 §7) 활성 시 mlanN IP 산출용. 현재 무-src라 미사용.
IFACE="${2:-mlan0}"

usage() {
    echo "usage: $tag <ip> [<iface>]" >&2
    echo "  <ip>/32 dev $ETH_IFACE peer host route를 등록한다." >&2
}

# ── IPv4 검증 (wifi.sh is_valid_ipv4 준용: leading-zero 거부) ──
is_valid_ipv4() {
    local ip="$1" o; local -a octets
    [[ "$ip" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || return 1
    IFS='.' read -r -a octets <<< "$ip"
    for o in "${octets[@]}"; do
        [[ "$o" =~ ^(0|[1-9][0-9]{0,2})$ ]] || return 1
        [ "$o" -le 255 ] || return 1
    done
    return 0
}
# 라우팅 대상으로 부적절한 대역 거부 (wifi_arping.sh is_plausible_host_ip 준용)
is_plausible_host_ip() {
    case "$1" in
        0.*|127.*|169.254.*|22[4-9].*|23[0-9].*|255.255.255.255) return 1 ;;
    esac
    return 0
}

[ -n "$IP" ] || { usage; exit 2; }
if ! is_valid_ipv4 "$IP" || ! is_plausible_host_ip "$IP"; then
    echo "$tag: invalid/implausible IPv4: '$IP'" >&2
    logger -p local0.err "[$tag:$LINENO] invalid IP '$IP'"
    exit 2
fi

# ── peer_route 가드 (off여도 진행, 경고만) ──
if command -v jq >/dev/null 2>&1 && [ -f "$WIFI_INIT_CONF_JSON" ]; then
    _pr=$(jq -r '.wbridge.peer_route.enabled' "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    if [ "$_pr" = "false" ]; then
        echo "[WARN] $tag: peer_route.enabled=false — eth0 /32 mirror/sysctl 부재로 반쪽짜리일 수 있음. route는 등록하고 진행." >&2
        logger -p local0.warn "[$tag:$LINENO] peer_route=false but registering route for $IP anyway"
    fi
fi

# ── 라우트 등록 (idempotent) ──
if ip route replace "${IP}/32" dev "$ETH_IFACE" 2>/dev/null; then
    # 심링크 공격 방지: 기존 항목(심링크 포함)을 먼저 제거 후 정규 파일로 새로 쓴다.
    # 경로는 wired_mac_ip_get.py·wifi_arping.sh와 공유하는 /tmp/<eth>_client_ip 유지.
    rm -f "$ETH_CLIENT_IP_FILE" 2>/dev/null || true
    printf '%s\n' "$IP" > "$ETH_CLIENT_IP_FILE" 2>/dev/null || true
    logger -p local0.info "[$tag:$LINENO] peer host route registered: ${IP}/32 dev $ETH_IFACE"
    echo "$tag: registered ${IP}/32 dev $ETH_IFACE"
    exit 0
else
    echo "$tag: FAILED to register ${IP}/32 dev $ETH_IFACE" >&2
    logger -p local0.err "[$tag:$LINENO] route replace FAILED for ${IP}/32 dev $ETH_IFACE"
    exit 1
fi
