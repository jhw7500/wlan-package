#!/bin/bash
# test_wifi_eth_peer.sh — standalone(비-bats) 단위/통합 테스트
#   대상: wifi_eth_peer_route.sh (등록기), wifi_eth_peer_find.sh (탐색기),
#         wifi.sh 의 `br route {find|set|auto}` 디스패치
#
# 방식: PATH 앞단에 ip/arping/logger/jq 스텁을 깔아 시스템 부작용 없이
#       인자 분기·exit code·발행 명령을 검증한다. 실행: bash test_wifi_eth_peer.sh
set -u

SCRIPTS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ROUTE_SH="$SCRIPTS_DIR/wifi_eth_peer_route.sh"
FIND_SH="$SCRIPTS_DIR/wifi_eth_peer_find.sh"
WIFI_SH="$SCRIPTS_DIR/wifi.sh"

PASS=0; FAIL=0
_pass() { PASS=$((PASS+1)); }
_fail() { FAIL=$((FAIL+1)); echo "  [FAIL] $1"; }
assert_eq()       { if [ "$1" = "$2" ]; then _pass; else _fail "$3 (want='$2' got='$1')"; fi; }
assert_contains() { case "$1" in *"$2"*) _pass;; *) _fail "$3 (missing '$2' in <<<$1>>>)";; esac; }
assert_absent()   { case "$1" in *"$2"*) _fail "$3 (unexpected '$2' in <<<$1>>>)";; *) _pass;; esac; }

# ── 스텁 환경 구성 ─────────────────────────────────────────────
# make_env → STUB(PATH 앞단), 임시 config/파일 경로 export. 각 테스트 격리.
_TMPROOT="$(mktemp -d)"
trap 'rm -rf "$_TMPROOT"' EXIT
_env_n=0
make_env() {
    _env_n=$((_env_n+1))
    STUB="$_TMPROOT/e$_env_n"; mkdir -p "$STUB/bin"
    CALLS="$STUB/calls.log"; : > "$CALLS"
    CONF="$STUB/conf.json"
    CLIENT_FILE="$STUB/eth0_client_ip"
    CARRIER="$STUB/carrier"; echo 1 > "$CARRIER"   # 기본 링크 up
    ARPING_RC="$STUB/arping_rc"; echo 0 > "$ARPING_RC"

    # stub: ip — 서브커맨드별 canned 출력, 모든 호출을 CALLS에 기록
    cat > "$STUB/bin/ip" <<STUBEOF
#!/bin/bash
echo "ip \$*" >> "$CALLS"
args="\$*"
case "\$args" in
  *"route replace"*|*"route add"*|*"route del"*|*"route flush"*) exit 0 ;;
  "-4 addr show "*) dev="\${args##* }"; cat "$STUB/ip_addr_\$dev" 2>/dev/null; exit 0 ;;
  "-4 route show default dev "*) dev="\${args##* }"; cat "$STUB/ip_route_default_\$dev" 2>/dev/null; exit 0 ;;
  "neigh show dev "*|"-4 neigh show dev "*) dev="\${args##* }"; cat "$STUB/ip_neigh_\$dev" 2>/dev/null; exit 0 ;;
esac
exit 0
STUBEOF

    # stub: arping — 기록 후 ARPING_RC 파일 코드로 종료
    cat > "$STUB/bin/arping" <<STUBEOF
#!/bin/bash
echo "arping \$*" >> "$CALLS"
exit \$(cat "$ARPING_RC" 2>/dev/null || echo 0)
STUBEOF

    # stub: logger/jq(그대로 진짜 jq 쓰되 없으면 통과)/sleep(즉시)
    cat > "$STUB/bin/logger" <<STUBEOF
#!/bin/bash
echo "logger \$*" >> "$CALLS"
exit 0
STUBEOF
    cat > "$STUB/bin/sleep" <<'STUBEOF'
#!/bin/bash
exit 0
STUBEOF
    # wifi.sh 통합 실행용 noop 스텁 (부작용 차단)
    for _c in systemctl networkctl sync; do
        printf '#!/bin/bash\nexit 0\n' > "$STUB/bin/$_c"
    done

    chmod +x "$STUB/bin/"*
    export WIFI_INIT_CONF_JSON="$CONF"
    export ETH_CLIENT_IP_FILE="$CLIENT_FILE"
    export ETH_CARRIER_PATH="$CARRIER"
    export WIFI_PEER_SCRIPT_DIR="$SCRIPTS_DIR"   # wifi.sh가 실제 peer 스크립트를 여기서 찾게
    export PATH="$STUB/bin:$_ORIG_PATH"
}
_ORIG_PATH="$PATH"

# peer_route on/off + 선택적 eth_client_ip/eth_sweep_subnet 를 담은 최소 conf 작성
write_conf() { # $1=enabled(true/false) $2=eth_client_ip $3=eth_sweep_subnet
    cat > "$CONF" <<JSON
{ "wbridge": { "peer_route": { "enabled": $1 },
  "eth_client_ip": "${2:-}", "eth_sweep_subnet": "${3:-}" } }
JSON
}

echo "== wifi_eth_peer_route.sh (등록기) =="

# T1: 유효 IP + peer_route=on → ip route replace <ip>/32 dev eth0 발행, /tmp 저장, exit 0
make_env; write_conf true "" ""
out="$("$ROUTE_SH" 192.168.0.20 mlan0 2>&1)"; rc=$?
assert_eq "$rc" "0" "T1 exit0"
assert_contains "$(cat "$CALLS")" "ip route replace 192.168.0.20/32 dev eth0" "T1 route 발행"
assert_eq "$(cat "$CLIENT_FILE" 2>/dev/null)" "192.168.0.20" "T1 /tmp 저장"

# T2: 잘못된 IP → exit 2, route 미발행
make_env; write_conf true "" ""
out="$("$ROUTE_SH" 999.1.1.1 mlan0 2>&1)"; rc=$?
assert_eq "$rc" "2" "T2 invalid ip exit2"
assert_absent "$(cat "$CALLS")" "route replace" "T2 route 미발행"

# T3: 인자 없음 → usage exit 2
make_env; write_conf true "" ""
out="$("$ROUTE_SH" 2>&1)"; rc=$?
assert_eq "$rc" "2" "T3 no-arg usage exit2"

# T4: peer_route=off → [WARN] 출력하되 route는 발행, exit 0
make_env; write_conf false "" ""
out="$("$ROUTE_SH" 192.168.0.20 mlan0 2>&1)"; rc=$?
assert_eq "$rc" "0" "T4 off exit0"
if command -v jq >/dev/null 2>&1; then assert_contains "$out" "WARN" "T4 경고 출력(jq)"; else _pass; fi
assert_contains "$(cat "$CALLS")" "ip route replace 192.168.0.20/32 dev eth0" "T4 route 여전히 발행"

# T5: implausible IP(127.x) → exit 2
make_env; write_conf true "" ""
out="$("$ROUTE_SH" 127.0.0.1 mlan0 2>&1)"; rc=$?
assert_eq "$rc" "2" "T5 loopback 거부 exit2"

echo
echo "== wifi_eth_peer_find.sh (탐색기) =="

# canned: mlan0 IP=192.168.0.100/24, GW=192.168.0.1 (self/gw 제외 검증용)
_seed_iface() {
    printf '    inet 192.168.0.100/24 scope global mlan0\n' > "$STUB/ip_addr_mlan0"
    printf 'default via 192.168.0.1 dev mlan0\n' > "$STUB/ip_route_default_mlan0"
}

# F1: carrier down → exit 3, arping 미수행
make_env; write_conf true "" ""; _seed_iface; echo 0 > "$CARRIER"
out="$("$FIND_SH" 192.168.0.0/24 mlan0 2>&1)"; rc=$?
assert_eq "$rc" "3" "F1 carrier down exit3"
assert_absent "$(cat "$CALLS")" "arping" "F1 no arping"

# F2: 명시 subnet + neigh(self/gw/peer) → peer만 출력, self·gw 제외
make_env; write_conf true "" ""; _seed_iface
printf '192.168.0.1 lladdr aa:aa:aa:aa:aa:aa REACHABLE\n192.168.0.20 lladdr bb:bb:bb:bb:bb:bb REACHABLE\n192.168.0.100 lladdr cc:cc:cc:cc:cc:cc REACHABLE\n' > "$STUB/ip_neigh_eth0"
out="$("$FIND_SH" 192.168.0.0/24 mlan0 2>/dev/null)"; rc=$?
assert_eq "$rc" "0" "F2 exit0"
assert_contains "$out" "192.168.0.20" "F2 peer 출력"
assert_absent "$out" "192.168.0.100" "F2 self 제외"
assert_absent "$out" "192.168.0.1"   "F2 gw 제외"
assert_contains "$(cat "$CALLS")" "arping" "F2 sweep 수행"
assert_contains "$(cat "$CALLS")" "neigh flush dev eth0" "F2 sweep 전 neigh flush(STALE 오등록 방지)"

# F3: subnet 생략 + eth_client_ip 설정 + arping 응답(rc0) → 그 IP 출력 (quick path)
make_env; write_conf true "192.168.0.20" ""; _seed_iface; echo 0 > "$ARPING_RC"
out="$("$FIND_SH" "" mlan0 2>/dev/null)"; rc=$?
assert_eq "$rc" "0" "F3 quick exit0"
assert_contains "$out" "192.168.0.20" "F3 eth_client_ip 출력"

# F4: sweep 결과 없음(neigh empty) → exit 1, 빈 출력
make_env; write_conf true "" ""; _seed_iface; : > "$STUB/ip_neigh_eth0"
out="$("$FIND_SH" 192.168.0.0/24 mlan0 2>/dev/null)"; rc=$?
assert_eq "$rc" "1" "F4 no peer exit1"
assert_eq "$out" "" "F4 빈 출력"

# F5: 복수 peer + FAILED 혼재 → REACHABLE/STALE 2줄만, FAILED 제외
make_env; write_conf true "" ""; _seed_iface
printf '192.168.0.20 lladdr bb:bb:bb:bb:bb:bb REACHABLE\n192.168.0.30 lladdr dd:dd:dd:dd:dd:dd STALE\n192.168.0.40 lladdr ee:ee:ee:ee:ee:ee FAILED\n' > "$STUB/ip_neigh_eth0"
out="$("$FIND_SH" 192.168.0.0/24 mlan0 2>/dev/null)"; rc=$?
assert_eq "$rc" "0" "F5 exit0"
assert_contains "$out" "192.168.0.20" "F5 peer1"
assert_contains "$out" "192.168.0.30" "F5 peer2(STALE 허용)"
assert_absent   "$out" "192.168.0.40" "F5 FAILED 제외"
assert_eq "$(printf '%s\n' "$out" | grep -c .)" "2" "F5 정확히 2줄"

# F6: 스윕 대역 밖 neigh 엔트리 제외 (MEDIUM: stale 이웃 누출 → auto 오등록 방지)
make_env; write_conf true "" ""; _seed_iface
printf '192.168.0.5 lladdr bb:bb:bb:bb:bb:bb REACHABLE\n10.0.0.77 lladdr dd:dd:dd:dd:dd:dd STALE\n192.168.9.9 lladdr ee:ee:ee:ee:ee:ee STALE\n' > "$STUB/ip_neigh_eth0"
out="$("$FIND_SH" 192.168.0.0/24 mlan0 2>/dev/null)"; rc=$?
assert_eq "$rc" "0" "F6 exit0"
assert_contains "$out" "192.168.0.5" "F6 대역내 peer 출력"
assert_absent "$out" "10.0.0.77"   "F6 대역밖 제외1"
assert_absent "$out" "192.168.9.9" "F6 대역밖 제외2"
assert_eq "$(printf '%s\n' "$out" | grep -c .)" "1" "F6 정확히 1줄(대역내만)"

# F7: 잘못된 subnet 인자 → exit 2 (garbage 스윕 대신 clean 에러), arping 미수행
make_env; write_conf true "" ""; _seed_iface
out="$("$FIND_SH" abc.def.ghi.jkl/24 mlan0 2>&1)"; rc=$?
assert_eq "$rc" "2" "F7 bad subnet exit2"
assert_absent "$(cat "$CALLS")" "arping" "F7 arping 미수행"

# F8: DELAY 등 REACHABLE/STALE 외 상태 제외 (스펙 정렬)
make_env; write_conf true "" ""; _seed_iface
printf '192.168.0.20 lladdr bb:bb:bb:bb:bb:bb DELAY\n192.168.0.30 lladdr dd:dd:dd:dd:dd:dd REACHABLE\n' > "$STUB/ip_neigh_eth0"
out="$("$FIND_SH" 192.168.0.0/24 mlan0 2>/dev/null)"; rc=$?
assert_absent   "$out" "192.168.0.20" "F8 DELAY 제외"
assert_contains "$out" "192.168.0.30" "F8 REACHABLE 출력"

# F9: SWEEP_PARALLEL_LIMIT=0 오버라이드 시 division-by-zero 없이 동작
make_env; write_conf true "" ""; _seed_iface
printf '192.168.0.20 lladdr bb:bb:bb:bb:bb:bb REACHABLE\n' > "$STUB/ip_neigh_eth0"
err="$(SWEEP_PARALLEL_LIMIT=0 "$FIND_SH" 192.168.0.0/29 mlan0 2>&1 >/dev/null)"
assert_absent "$err" "division by zero" "F9 div-by-zero 없음"

# F10: eth_client_ip가 global.ETH_CLIENT_IP에만 있어도 quick path 동작 (boot 경로 consumer parity)
make_env
cat > "$CONF" <<'JSON'
{ "wbridge": { "peer_route": { "enabled": true } },
  "global": { "ETH_CLIENT_IP": "192.168.0.20" } }
JSON
_seed_iface; echo 0 > "$ARPING_RC"
out="$("$FIND_SH" "" mlan0 2>/dev/null)"; rc=$?
assert_eq "$rc" "0" "F10 global eth_client_ip quick exit0"
assert_contains "$out" "192.168.0.20" "F10 global.ETH_CLIENT_IP quick path"

echo
echo "== wifi.sh br route 디스패치 (통합) =="

# I1: br route set <ip> → 등록기 경유 route replace, exit 0
make_env; write_conf true "" ""; _seed_iface
out="$(bash "$WIFI_SH" 0 br route set 192.168.0.20 2>/dev/null)"; rc=$?
assert_eq "$rc" "0" "I1 set exit0"
assert_contains "$(cat "$CALLS")" "ip route replace 192.168.0.20/32 dev eth0" "I1 route 발행"

# I2: br route find <subnet> → 탐색기 경유 peer 출력, exit 0
make_env; write_conf true "" ""; _seed_iface
printf '192.168.0.20 lladdr bb:bb:bb:bb:bb:bb REACHABLE\n' > "$STUB/ip_neigh_eth0"
out="$(bash "$WIFI_SH" 0 br route find 192.168.0.0/24 2>/dev/null)"; rc=$?
assert_eq "$rc" "0" "I2 find exit0"
assert_contains "$out" "192.168.0.20" "I2 peer 출력"

# I3: br route auto — 정확히 1건 → route 발행, exit 0
make_env; write_conf true "" ""; _seed_iface
printf '192.168.0.20 lladdr bb:bb:bb:bb:bb:bb REACHABLE\n' > "$STUB/ip_neigh_eth0"
out="$(bash "$WIFI_SH" 0 br route auto 192.168.0.0/24 2>/dev/null)"; rc=$?
assert_eq "$rc" "0" "I3 auto-1 exit0"
assert_contains "$(cat "$CALLS")" "ip route replace 192.168.0.20/32 dev eth0" "I3 auto→route 발행"

# I4: br route auto — 0건 → 에러 exit(고유 메시지 '미발견'), route 미발행
make_env; write_conf true "" ""; _seed_iface; : > "$STUB/ip_neigh_eth0"
out="$(bash "$WIFI_SH" 0 br route auto 192.168.0.0/24 2>&1)"; rc=$?
[ "$rc" -ne 0 ] && _pass || _fail "I4 auto-0 nonzero exit (got $rc)"
assert_absent "$(cat "$CALLS")" "route replace" "I4 route 미발행"
assert_contains "$out" "미발견" "I4 미발견 메시지"

# I5: br route auto — 2건 → 에러 exit(고유 메시지 '모호'/'set <ip>'), route 미발행
make_env; write_conf true "" ""; _seed_iface
printf '192.168.0.20 lladdr bb:bb:bb:bb:bb:bb REACHABLE\n192.168.0.30 lladdr dd:dd:dd:dd:dd:dd REACHABLE\n' > "$STUB/ip_neigh_eth0"
out="$(bash "$WIFI_SH" 0 br route auto 192.168.0.0/24 2>&1)"; rc=$?
[ "$rc" -ne 0 ] && _pass || _fail "I5 auto-2 nonzero exit (got $rc)"
assert_absent "$(cat "$CALLS")" "route replace" "I5 route 미발행"
assert_contains "$out" "모호" "I5 모호 메시지"
assert_contains "$out" "192.168.0.20" "I5 후보 나열"

# I6: br route <unknown> → usage(nonzero)
make_env; write_conf true "" ""; _seed_iface
out="$(bash "$WIFI_SH" 0 br route bogus 2>&1)"; rc=$?
[ "$rc" -ne 0 ] && _pass || _fail "I6 badverb nonzero exit (got $rc)"

# I7: finder 비정상 종료(스크립트 부재=exit127) → auto가 '미발견' 아닌 finder 오류로 보고
make_env; write_conf true "" ""; _seed_iface
out="$(WIFI_PEER_SCRIPT_DIR=/nonexistent-dir bash "$WIFI_SH" 0 br route auto 192.168.0.0/24 2>&1)"; rc=$?
[ "$rc" -ne 0 ] && _pass || _fail "I7 abnormal finder nonzero exit (got $rc)"
assert_absent   "$out" "미발견"    "I7 '미발견' 오표시 아님"
assert_contains "$out" "finder 오류" "I7 finder 오류 메시지"

echo
echo "TOTAL: PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
