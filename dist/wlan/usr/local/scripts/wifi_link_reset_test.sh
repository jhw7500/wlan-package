#!/bin/bash
# wifi_link_reset.sh standalone tests (하드웨어/root 불필요)
set -uo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
SCRIPT="$SCRIPT_DIR/wifi_link_reset.sh"
LIB="$SCRIPT_DIR/mac_link_lib.sh"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
NET="$WORK/net"
LOG="$WORK/logger.log"
FAKE_LOGGER="$WORK/logger.sh"
PASS=0
FAIL=0

cat > "$FAKE_LOGGER" <<'EOF'
#!/bin/bash
printf '%s\n' "$*" >> "$LOG_CAPTURE"
EOF
chmod +x "$FAKE_LOGGER"

run_reset() {
    SYSTEMD_NETWORK_DIR="$NET" \
    MAC_LINK_LOCK_DIR="$NET" \
    WIFI_LINK_RESET_LOGGER="$FAKE_LOGGER" \
    LOG_CAPTURE="$LOG" \
        bash "$SCRIPT" "$@"
}

expect_eq() {
    local name="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then
        printf 'PASS: %s\n' "$name"
        PASS=$((PASS + 1))
    else
        printf 'FAIL: %s (expected=[%s] actual=[%s])\n' "$name" "$expected" "$actual" >&2
        FAIL=$((FAIL + 1))
    fi
}

linkmac() { bash -c '. "$1"; mac_read_link_address "$2"' _ "$LIB" "$1" 2>/dev/null || true; }
exists()  { [ -e "$1" ] && echo yes || echo no; }

reset_net() { rm -rf "$NET"; mkdir -p "$NET"; : > "$LOG"; }

# 템플릿 상태(패키지가 배포하는 .link — MACAddress 없음)
tmpl_link() { printf '[Match]\nOriginalName=%s\n\n[Link]\n' "$1"; }

# --- 정상 경로: 템플릿 복원이 끝난 뒤라 지울 것이 없다 ---
reset_net
tmpl_link mlan0 > "$NET/20-mlan0.link"
tmpl_link mlan1 > "$NET/21-mlan1.link"
tmpl_link eth0  > "$NET/22-eth0.link"
run_reset
expect_eq "clean tree exits 0" "0" "$?"
expect_eq "clean tree logs verification" "1" "$(grep -c 'link reset verified' "$LOG")"

# --- 후조건 정정: 템플릿 복사가 실패해 클론 MAC이 남은 경우 ---
reset_net
printf '[Match]\nOriginalName=mlan0\n\n[Link]\nMACAddress=aa:bb:cc:00:00:01\nMTUBytes=1500\n' > "$NET/20-mlan0.link"
tmpl_link mlan1 > "$NET/21-mlan1.link"
tmpl_link eth0  > "$NET/22-eth0.link"
run_reset
expect_eq "package link with stale MAC exits 0" "0" "$?"
expect_eq "package link MACAddress stripped" "" "$(linkmac "$NET/20-mlan0.link")"
expect_eq "package link keeps other [Link] keys" "1" "$(grep -c '^MTUBytes=1500' "$NET/20-mlan0.link")"
expect_eq "package link file kept" "yes" "$(exists "$NET/20-mlan0.link")"
expect_eq "stale package MAC logged as err" "1" "$(grep -c 'still had MACAddress after template restore' "$LOG")"

# --- 외부 .link 삭제: 우리 인터페이스를 지목하며 MAC을 강제 ---
reset_net
tmpl_link mlan0 > "$NET/20-mlan0.link"
tmpl_link mlan1 > "$NET/21-mlan1.link"
tmpl_link eth0  > "$NET/22-eth0.link"
printf '[Match]\nOriginalName=mlan0\n\n[Link]\nMACAddress=aa:bb:cc:00:00:02\n' > "$NET/10-custom.link"
run_reset
expect_eq "foreign link forcing MAC exits 0" "0" "$?"
expect_eq "foreign link removed" "no" "$(exists "$NET/10-custom.link")"
expect_eq "foreign removal logged as crit" "1" "$(grep -c 'removing foreign link that forces MAC' "$LOG")"
expect_eq "package links untouched" "yes" "$(exists "$NET/20-mlan0.link")"

# --- glob/목록 OriginalName도 대상으로 인식 ---
reset_net
tmpl_link mlan0 > "$NET/20-mlan0.link"
printf '[Match]\nOriginalName=mlan*\n\n[Link]\nMACAddress=aa:bb:cc:00:00:03\n' > "$NET/05-glob.link"
printf '[Match]\nOriginalName=eth1 eth0\n\n[Link]\nMACAddress=aa:bb:cc:00:00:04\n' > "$NET/06-list.link"
run_reset
expect_eq "glob OriginalName removed" "no" "$(exists "$NET/05-glob.link")"
expect_eq "space-separated OriginalName removed" "no" "$(exists "$NET/06-list.link")"

# --- 무관한 인터페이스의 .link는 보존 ---
reset_net
tmpl_link mlan0 > "$NET/20-mlan0.link"
printf '[Match]\nOriginalName=enp0s1\n\n[Link]\nMACAddress=aa:bb:cc:00:00:05\n' > "$NET/30-other.link"
run_reset
expect_eq "unrelated iface link exits 0" "0" "$?"
expect_eq "unrelated iface link preserved" "yes" "$(exists "$NET/30-other.link")"

# --- MAC을 설정하지 않는 외부 .link는 보존 (운영자 MTU/WoL 설정) ---
reset_net
tmpl_link mlan0 > "$NET/20-mlan0.link"
printf '[Match]\nOriginalName=mlan0\n\n[Link]\nMTUBytes=9000\nWakeOnLan=magic\n' > "$NET/15-tuning.link"
run_reset
expect_eq "non-MAC foreign link exits 0" "0" "$?"
expect_eq "non-MAC foreign link preserved" "yes" "$(exists "$NET/15-tuning.link")"

# --- OriginalName 없이 MAC만 설정: 삭제하지 않고 실패로 보고 (사람 판단) ---
reset_net
tmpl_link mlan0 > "$NET/20-mlan0.link"
printf '[Match]\nPath=platform-*\n\n[Link]\nMACAddress=aa:bb:cc:00:00:06\n' > "$NET/12-bypath.link"
run_reset
expect_eq "undecidable foreign link exits 1" "1" "$?"
expect_eq "undecidable foreign link preserved" "yes" "$(exists "$NET/12-bypath.link")"
expect_eq "undecidable link logged for review" "1" "$(grep -c 'left in place for manual review' "$LOG")"

# --- 부정(!) OriginalName은 판정 불가로 다뤄 삭제하지 않고 보고 ---
# systemd는 '!' 접두 패턴을 "이것만 제외"로 해석하므로 단순 glob 매칭이 의미상 뒤집힌다.
# (OriginalName=!mlan0 은 mlan0을 뺀 전부와 매칭 — glob으로 보면 "매칭 안 함"으로 오판)
reset_net
tmpl_link mlan0 > "$NET/20-mlan0.link"
printf '[Match]\nOriginalName=!mlan0\n\n[Link]\nMACAddress=aa:bb:cc:00:00:10\n' > "$NET/11-negated.link"
run_reset
expect_eq "negated OriginalName exits 1" "1" "$?"
expect_eq "negated OriginalName link preserved" "yes" "$(exists "$NET/11-negated.link")"
expect_eq "negated OriginalName logged for review" "1" \
    "$(grep -c 'cannot decide the target' "$LOG")"

# --- OriginalName 줄이 여러 개면 판정 불가 (병합/덮어쓰기 의미를 추측하지 않는다) ---
reset_net
tmpl_link mlan0 > "$NET/20-mlan0.link"
printf '[Match]\nOriginalName=eth9\nOriginalName=mlan0\n\n[Link]\nMACAddress=aa:bb:cc:00:00:20\n' > "$NET/12-two.link"
run_reset
expect_eq "multiple OriginalName exits 1" "1" "$?"
expect_eq "multiple OriginalName link preserved" "yes" "$(exists "$NET/12-two.link")"
expect_eq "multiple OriginalName logged for review" "1" "$(grep -c 'cannot decide the target' "$LOG")"

# --- 부모가 판정 불가이고 자신은 MAC이 없어도 드롭인 MAC은 보고된다 ---
# (부모는 remove_foreign_link가 조기 반환하므로 scan_dropins가 놓치면 아무도 보고하지 않는다)
reset_net
tmpl_link mlan0 > "$NET/20-mlan0.link"
printf '[Match]\nPath=platform-*\n\n[Link]\nMTUBytes=1500\n' > "$NET/20-x.link"
mkdir -p "$NET/20-x.link.d"
printf '[Link]\nMACAddress=aa:bb:cc:00:00:21\n' > "$NET/20-x.link.d/10-mac.conf"
run_reset
expect_eq "dropin under undecidable parent exits 1" "1" "$?"
expect_eq "dropin under undecidable parent reported" "1" "$(grep -c 'drop-in forces MAC' "$LOG")"
expect_eq "undecidable parent preserved" "yes" "$(exists "$NET/20-x.link")"

# --- --check는 변경 없이 진단만 ---
reset_net
tmpl_link mlan0 > "$NET/20-mlan0.link"
printf '[Match]\nOriginalName=mlan0\n\n[Link]\nMACAddress=aa:bb:cc:00:00:07\n' > "$NET/10-custom.link"
run_reset --check
expect_eq "--check reports failure" "1" "$?"
expect_eq "--check does not delete" "yes" "$(exists "$NET/10-custom.link")"
run_reset
expect_eq "reset after --check succeeds" "0" "$?"
expect_eq "reset after --check deleted foreign link" "no" "$(exists "$NET/10-custom.link")"
run_reset --check
expect_eq "--check passes once clean" "0" "$?"

# --- 파생 잔재(*.link.*) 일소: 백업 세대·비숫자 백업·orphan tmp ---
reset_net
tmpl_link mlan0 > "$NET/20-mlan0.link"
touch "$NET/20-mlan0.link.bak" "$NET/20-mlan0.link.bak.1" "$NET/20-mlan0.link.bak.5" \
      "$NET/20-mlan0.link.bak.99" "$NET/20-mlan0.link.bak.operator" "$NET/20-mlan0.link.tmp.aBcDeF"
run_reset
expect_eq "artifact purge exits 0" "0" "$?"
expect_eq "artifact purge removes all *.link.*" "0" \
    "$(find "$NET" -maxdepth 1 -type f -name '*.link.*' | wc -l | tr -d ' ')"
expect_eq "artifact purge keeps active link" "yes" "$(exists "$NET/20-mlan0.link")"
expect_eq "artifact purge logged" "1" "$(grep -c 'removed .* derived link artifact' "$LOG")"

# --- .link.d 드롭인: rm 대상이 아니며(디렉터리) MAC 설정 시 보고 ---
reset_net
tmpl_link mlan0 > "$NET/20-mlan0.link"
mkdir -p "$NET/20-mlan0.link.d"
printf '[Link]\nMACAddress=aa:bb:cc:00:00:08\n' > "$NET/20-mlan0.link.d/10-mac.conf"
run_reset
expect_eq "dropin forcing MAC exits 1" "1" "$?"
expect_eq "dropin dir preserved" "yes" "$(exists "$NET/20-mlan0.link.d")"
expect_eq "dropin conf preserved" "yes" "$(exists "$NET/20-mlan0.link.d/10-mac.conf")"
expect_eq "dropin logged for review" "1" "$(grep -c 'drop-in forces MAC' "$LOG")"

# --- MAC을 설정하지 않는 드롭인은 조용히 통과 ---
reset_net
tmpl_link mlan0 > "$NET/20-mlan0.link"
mkdir -p "$NET/20-mlan0.link.d"
printf '[Link]\nMTUBytes=9000\n' > "$NET/20-mlan0.link.d/10-mtu.conf"
run_reset
expect_eq "non-MAC dropin exits 0" "0" "$?"
expect_eq "non-MAC dropin preserved" "yes" "$(exists "$NET/20-mlan0.link.d/10-mtu.conf")"

# --- 부모 .link가 없는 고아 드롭인은 systemd가 무시하므로 대상 아님 ---
reset_net
tmpl_link mlan0 > "$NET/20-mlan0.link"
mkdir -p "$NET/40-orphan.link.d"
printf '[Link]\nMACAddress=aa:bb:cc:00:00:09\n' > "$NET/40-orphan.link.d/10-mac.conf"
run_reset
expect_eq "orphan dropin exits 0" "0" "$?"

# --- 잘못된 인자 / 디렉터리 부재 ---
reset_net
run_reset --bogus >/dev/null 2>&1
expect_eq "invalid argument exits 64" "64" "$?"
rm -rf "$NET"
run_reset >/dev/null 2>&1
expect_eq "missing network dir exits 1" "1" "$?"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
