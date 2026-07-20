#!/bin/bash
# wifi_eth_peer_find.sh [<subnet>] [<iface>] — eth 유선 peer IP 탐색기 (순수 탐색)
#
# eth0에서 유선 peer의 IP를 찾아 stdout에 한 줄씩 출력한다. 라우트/설정/파일을
# 절대 변경하지 않는다(읽기 전용). wifi_eth_peer_route.sh 와 짝을 이룬다.
#
# 대상 결정 우선순위:
#   1) <subnet> 인자 → 그 대역 sweep
#   2) 없고 eth_client_ip 설정 → 그 IP에 quick arping. 응답하면 그것이 peer.
#   3) 응답 없음/미설정 → eth_sweep_subnet > 선택 iface(mlanN) CIDR 대역 sweep
# sweep 결과에서 self(mlanN IP)·게이트웨이는 제외한다.
#
# exit: 0=1건 이상 발견, 1=0건, 2=usage/대상없음, 3=eth 링크 down
set -u
tag=$(basename "$0")

ETH_IFACE="${ETH_IFACE:-eth0}"
WIFI_INIT_CONF_JSON="${WIFI_INIT_CONF_JSON:-/usr/local/etc/wifi_init_conf.json}"
ETH_CARRIER_PATH="${ETH_CARRIER_PATH:-/sys/class/net/${ETH_IFACE}/carrier}"
SWEEP_PARALLEL_LIMIT="${SWEEP_PARALLEL_LIMIT:-50}"
SWEEP_TIMEOUT="${SWEEP_TIMEOUT:-1}"
SWEEP_MAX_HOSTS="${SWEEP_MAX_HOSTS:-2048}"   # /8 폭주 방지
# 오버라이드 방어: 0/음수/비숫자면 division-by-zero 방지 위해 기본값 복원
{ [ "$SWEEP_PARALLEL_LIMIT" -ge 1 ]; } 2>/dev/null || SWEEP_PARALLEL_LIMIT=50

SUBNET="${1:-}"
IFACE="${2:-mlan0}"

# 함수 내부의 $LINENO는 함수 정의줄로 고정되므로, 호출자 라인은 ${BASH_LINENO[0]}로 남긴다.
_err() { echo "$tag: $1" >&2; logger -p local0.err "[$tag:${BASH_LINENO[0]}] $1" 2>/dev/null || true; }

# ── config 조회 (null/"" → 미설정) ──
_cfg() { # $1=jq path
    command -v jq >/dev/null 2>&1 && [ -f "$WIFI_INIT_CONF_JSON" ] || return 1
    local v; v=$(jq -r "$1" "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    [ -n "$v" ] && [ "$v" != "null" ] && { printf '%s' "$v"; return 0; }
    return 1
}

# ── IPv4 헬퍼 ──
ip_to_int() { local IFS=.; read -r a b c d <<< "$1"; echo $(( (a<<24)+(b<<16)+(c<<8)+d )); }
int_to_ip() { local n=$1; printf '%d.%d.%d.%d' $(((n>>24)&255)) $(((n>>16)&255)) $(((n>>8)&255)) $((n&255)); }
# IPv4 검증 (wifi.sh is_valid_ipv4 준용: leading-zero·옥텟 범위 거부)
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

# ── 1) 링크 확인 (arping 전에) ──
if [ "$(cat "$ETH_CARRIER_PATH" 2>/dev/null)" != "1" ]; then
    _err "$ETH_IFACE link down (carrier != 1) — cannot probe"
    exit 3
fi

# ── 2) self(mlanN IP) / GW / mlanN CIDR 확보 (제외·폴백용) ──
_mlan_cidr=$(ip -4 addr show "$IFACE" 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}/\d+' | head -1)
_mlan_ip=${_mlan_cidr%/*}
_gw=$(ip -4 route show default dev "$IFACE" 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="via"){print $(i+1); exit}}')

# ── 3) quick path: subnet 미지정 + eth_client_ip 설정 시 그 IP에 arping ──
if [ -z "$SUBNET" ]; then
    if _cip=$(_cfg '.wbridge.eth_client_ip') || _cip=$(_cfg '.global.ETH_CLIENT_IP'); then
        if arping -I "$ETH_IFACE" -c 1 -w "$SWEEP_TIMEOUT" "$_cip" >/dev/null 2>&1; then
            logger -p local0.info "[$tag:$LINENO] quick path: eth_client_ip $_cip responded" 2>/dev/null || true
            printf '%s\n' "$_cip"
            exit 0
        fi
        logger -p local0.info "[$tag:$LINENO] quick path: $_cip no reply → fall back to sweep" 2>/dev/null || true
    fi
    # 폴백 대역: eth_sweep_subnet(wbridge→global) > mlanN CIDR (boot 경로 wired_mac_ip_get.py와 동일 소스)
    SUBNET=$(_cfg '.wbridge.eth_sweep_subnet') || SUBNET=$(_cfg '.global.eth_sweep_subnet') || SUBNET="$_mlan_cidr"
fi

[ -n "$SUBNET" ] || { _err "no sweep target (subnet 인자·eth_client_ip·eth_sweep_subnet·mlanN CIDR 모두 없음)"; exit 2; }

# ── 4) sweep: CIDR → host range 병렬 arping ──
case "$SUBNET" in */*) : ;; *) _err "invalid subnet (CIDR 필요): '$SUBNET'"; exit 2 ;; esac
_net=${SUBNET%/*}; _pfx=${SUBNET#*/}
is_valid_ipv4 "$_net" || { _err "invalid subnet addr: '$SUBNET'"; exit 2; }
{ [ "$_pfx" -ge 1 ] && [ "$_pfx" -le 32 ]; } 2>/dev/null || { _err "invalid prefix: '/$_pfx'"; exit 2; }

_base=$(ip_to_int "$_net")
_maskbits=$(( _pfx==0 ? 0 : (0xFFFFFFFF << (32-_pfx)) & 0xFFFFFFFF ))
_network=$(( _base & _maskbits ))
_bcast=$(( _network | (~_maskbits & 0xFFFFFFFF) ))
if [ "$_pfx" -ge 31 ]; then _start=$_network; _end=$_bcast; else _start=$((_network+1)); _end=$((_bcast-1)); fi
_count=$(( _end - _start + 1 ))
if [ "$_count" -le 0 ]; then _err "empty host range for $SUBNET"; exit 2; fi
if [ "$_count" -gt "$SWEEP_MAX_HOSTS" ]; then
    _err "subnet too large ($_count hosts > $SWEEP_MAX_HOSTS) — 좁은 대역을 지정하세요"
    exit 2
fi

logger -p local0.info "[$tag:$LINENO] sweep $SUBNET on $ETH_IFACE ($_count hosts)" 2>/dev/null || true
# arping(iputils)은 raw PF_PACKET 소켓이라 응답을 받아도 **커널 neigh 테이블을 채우지 않는다**
# (온타겟 실측 2026-07-20: arping exit=0인데 `ip neigh show`엔 없음, ping은 채움). 따라서
# `ip neigh show`를 읽지 않고 arping **exit code**(0=응답)로 응답자를 직접 수집한다.
# 스윕 범위 내 IP만 arping하므로 대역 필터가 불필요하고, live 응답만 잡으므로 STALE
# 잔존 오등록 문제도 원천적으로 없다(neigh flush 불필요).
_n=0
_responders=$(
    for (( ipi=_start; ipi<=_end; ipi++ )); do
        _t=$(int_to_ip "$ipi")
        ( arping -I "$ETH_IFACE" -c 1 -w "$SWEEP_TIMEOUT" "$_t" >/dev/null 2>&1 && printf '%s\n' "$_t" ) &
        _n=$((_n+1))
        if [ $(( _n % SWEEP_PARALLEL_LIMIT )) -eq 0 ]; then wait; fi
    done
    wait
)

# ── 5) 응답자에서 self·GW 제외 (스윕 범위 내만 arping했으므로 범위 필터 불필요) ──
_found=0
while IFS= read -r _pip; do
    [ -n "$_pip" ] || continue
    [ "$_pip" = "$_mlan_ip" ] && continue
    [ -n "$_gw" ] && [ "$_pip" = "$_gw" ] && continue
    printf '%s\n' "$_pip"
    _found=$((_found+1))
done < <(printf '%s\n' "$_responders" | awk 'NF && !seen[$0]++')

[ "$_found" -ge 1 ] && exit 0 || exit 1
