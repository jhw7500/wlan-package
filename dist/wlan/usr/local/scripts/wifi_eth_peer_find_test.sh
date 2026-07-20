#!/bin/bash
# wifi_eth_peer_find.sh 헬퍼 단위 테스트 (arping source 선택 로직 + IPv4 헬퍼).
# WIFI_EPF_SOURCE_ONLY=1 로 함수만 로드하고, ip 명령을 셰임해 비-root/네트워크 없이 실행.
# 사용: bash wifi_eth_peer_find_test.sh  (성공 시 exit 0)
set -uo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
FINDER="$SCRIPT_DIR/wifi_eth_peer_find.sh"

PASS=0; FAIL=0
ok(){ PASS=$((PASS+1)); echo "[PASS] $*"; }
no(){ FAIL=$((FAIL+1)); echo "[FAIL] $*"; }
eq(){ if [ "$2" = "$3" ]; then ok "$1"; else no "$1 (exp [$2] got [$3])"; fi; }

WORK=$(mktemp -d); BIN="$WORK/bin"; mkdir -p "$BIN"
# 모의 ip: "-4 addr show eth0"에 MOCK_ETH_INET 한 줄만 응답
cat > "$BIN/ip" <<'EOF'
#!/bin/sh
case "$*" in
  *"addr show eth0"*) echo "    inet ${MOCK_ETH_INET:-192.168.1.1/24} brd 0 scope global eth0" ;;
esac
exit 0
EOF
chmod +x "$BIN/ip"
trap 'rm -rf "$WORK"' EXIT
export PATH="$BIN:$PATH" ETH_IFACE=eth0 MOCK_ETH_INET=""   # MOCK_ETH_INET는 자식 ip 프로세스가 읽으므로 export 필수

[ -f "$FINDER" ] || { echo "missing: $FINDER" >&2; exit 1; }
# 함수만 로드 (링크체크/스윕은 실행 안 함)
WIFI_EPF_SOURCE_ONLY=1 . "$FINDER"

netmask(){ local ip=$1 pfx=$2 base m; base=$(ip_to_int "$ip"); m=$(( pfx==0?0:(0xFFFFFFFF<<(32-pfx))&0xFFFFFFFF )); echo "$(( base & m )) $m"; }

echo "=== IPv4 헬퍼 ==="
eq "ip_to_int/int_to_ip roundtrip" "192.168.0.220" "$(int_to_ip "$(ip_to_int 192.168.0.220)")"
if is_valid_ipv4 192.168.0.1;   then ok "is_valid_ipv4 accepts valid"; else no "is_valid_ipv4 accepts valid"; fi
if is_valid_ipv4 192.168.0.256; then no "is_valid_ipv4 rejects 256";   else ok "is_valid_ipv4 rejects 256"; fi
if is_valid_ipv4 192.168.01.1;  then no "is_valid_ipv4 rejects lead0"; else ok "is_valid_ipv4 rejects lead0"; fi

echo "=== _arp_src_opt (버그 수정 핵심) ==="
read -r NET MASK < <(netmask 192.168.0.0 24)

# C1: eth0가 대상 대역 내 IP 보유 → 기본(-s 없음)
MOCK_ETH_INET="192.168.0.5/24"; _mlan_ip="192.168.0.20"
eq "eth0 in-subnet → 기본(no -s)" "" "$(_arp_src_opt "$NET" "$MASK")"

# C2: eth0 대역 밖 + mlan_ip 대역 내 → -s mlan_ip  (실제 .20/.220 시나리오)
MOCK_ETH_INET="192.168.1.1/24"; _mlan_ip="192.168.0.20"
eq "eth0 out + mlan in → -s mlan_ip" "-s 192.168.0.20" "$(_arp_src_opt "$NET" "$MASK")"

# C3: eth0 대역 밖 + mlan_ip 대역 밖 → 기본(-s 없음)
MOCK_ETH_INET="192.168.1.1/24"; _mlan_ip="10.0.0.5"
eq "eth0 out + mlan out → 기본(no -s)" "" "$(_arp_src_opt "$NET" "$MASK")"

# C4: mlan_ip 미설정 → 기본
MOCK_ETH_INET="192.168.1.1/24"; _mlan_ip=""
eq "mlan_ip empty → 기본(no -s)" "" "$(_arp_src_opt "$NET" "$MASK")"

echo ""; echo "PASS: $PASS"; echo "FAIL: $FAIL"
[ "$FAIL" -eq 0 ]
