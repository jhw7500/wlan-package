#!/bin/bash
# update_mac.sh 동작 검증 테스트.
# 실제 update_mac.sh를 SYSTEMD_NETWORK_DIR 오버라이드 + logger 셰임으로 비-root에서 실행한다.
# 사용: bash update_mac_test.sh   (성공 시 exit 0)
set -uo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
UPDATE_MAC="$SCRIPT_DIR/update_mac.sh"

PASS_COUNT=0
FAIL_COUNT=0
log_pass() { PASS_COUNT=$((PASS_COUNT + 1)); echo "[PASS] $*"; }
log_fail() { FAIL_COUNT=$((FAIL_COUNT + 1)); echo "[FAIL] $*"; }

# --- 격리 환경 (임시 network 디렉토리 + logger 셰임) ---
WORK=$(mktemp -d)
BINDIR="$WORK/bin"; NET="$WORK/net"
mkdir -p "$BINDIR" "$NET"
printf '#!/bin/sh\nexit 0\n' > "$BINDIR/logger"; chmod +x "$BINDIR/logger"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

LF="$NET/21-mlan1.link"   # mlan1 대상

run_um() { # <iface> <mac>
  SYSTEMD_NETWORK_DIR="$NET" PATH="$BINDIR:$PATH" bash "$UPDATE_MAC" "$1" "$2"
}
macof() { grep -oP '^MACAddress=\K.*' "$1" 2>/dev/null || true; }
count_baks() { ls -1 "$NET"/21-mlan1.link.bak.* 2>/dev/null | wc -l | tr -d ' '; }
reset_net() { rm -rf "$NET"; mkdir -p "$NET"; }

assert_eq() { # desc expected actual
  if [ "$2" = "$3" ]; then log_pass "$1"; else log_fail "$1 (expected [$2] got [$3])"; fi
}
assert_has() { # desc file pattern
  if grep -q "$3" "$2" 2>/dev/null; then log_pass "$1"; else log_fail "$1 (missing /$3/ in $2)"; fi
}

if [ ! -x "$UPDATE_MAC" ]; then echo "missing/not-executable: $UPDATE_MAC" >&2; exit 1; fi

echo "=== update_mac.sh tests ==="

# T01: .link 없음 → [Match]/OriginalName/MACAddress 갖춘 유효 파일 생성
reset_net
run_um mlan1 "de:ad:be:ef:00:01" >/dev/null 2>&1
assert_has "T01 missing→create [Match]"      "$LF" '^\[Match\]'
assert_has "T01 missing→create OriginalName" "$LF" '^OriginalName=mlan1'
assert_eq  "T01 missing→create MACAddress"   "de:ad:be:ef:00:01" "$(macof "$LF")"

# T02: 0바이트 .link(구버전 버그 잔재) → 재생성 (Codex 지적 회귀 방지)
reset_net
: > "$LF"
run_um mlan1 "de:ad:be:ef:00:02" >/dev/null 2>&1
assert_eq  "T02 empty→regenerate MACAddress" "de:ad:be:ef:00:02" "$(macof "$LF")"
assert_has "T02 empty→regenerate [Match]"    "$LF" '^\[Match\]'

# T03: 섹션 없는 쓰레기 .link → 재생성
reset_net
printf 'garbage line\nno sections here\n' > "$LF"
run_um mlan1 "de:ad:be:ef:00:03" >/dev/null 2>&1
assert_eq  "T03 section-less→regenerate"     "de:ad:be:ef:00:03" "$(macof "$LF")"
assert_has "T03 section-less→[Link] present" "$LF" '^\[Link\]'

# T04: [Match]+[Link] 있고 MACAddress 없음(배포 기본형) → MACAddress 추가, [Match] 보존
reset_net
printf '[Match]\nOriginalName=mlan1\n\n[Link]\n' > "$LF"
run_um mlan1 "de:ad:be:ef:00:04" >/dev/null 2>&1
assert_eq  "T04 add MACAddress"              "de:ad:be:ef:00:04" "$(macof "$LF")"
assert_has "T04 preserve OriginalName"       "$LF" '^OriginalName=mlan1'

# T05: 기존 MACAddress → 교체
reset_net
printf '[Match]\nOriginalName=mlan1\n\n[Link]\nMACAddress=00:00:00:00:00:00\n' > "$LF"
run_um mlan1 "de:ad:be:ef:00:05" >/dev/null 2>&1
assert_eq  "T05 replace MACAddress"          "de:ad:be:ef:00:05" "$(macof "$LF")"

# T06: 동일 타깃 MAC → 조기 종료(변경 없음, 백업 증가 없음)
reset_net
printf '[Match]\nOriginalName=mlan1\n\n[Link]\nMACAddress=de:ad:be:ef:00:06\n' > "$LF"
run_um mlan1 "de:ad:be:ef:00:06" >/dev/null 2>&1
assert_eq  "T06 same MAC → no backup"        "0" "$(count_baks)"
assert_eq  "T06 same MAC → unchanged"        "de:ad:be:ef:00:06" "$(macof "$LF")"

# T07: 7회 서로 다른 MAC → 백업 최대 5개, .bak.1=직전, .bak.5=가장 오래된 보존분
reset_net
for n in 01 02 03 04 05 06 07; do run_um mlan1 "de:ad:be:ef:00:$n" >/dev/null 2>&1; done
assert_eq  "T07 rotate cap = 5"              "5" "$(count_baks)"
assert_eq  "T07 .link = latest(07)"          "de:ad:be:ef:00:07" "$(macof "$LF")"
assert_eq  "T07 .bak.1 = prev(06)"           "de:ad:be:ef:00:06" "$(macof "$NET/21-mlan1.link.bak.1")"
assert_eq  "T07 .bak.5 = oldest kept(02)"    "de:ad:be:ef:00:02" "$(macof "$NET/21-mlan1.link.bak.5")"

# T08: 백업 대상이 .bak.1과 동일 MAC → 중복 백업 생성 안 함 (T07 상태 이어서)
printf '[Match]\nOriginalName=mlan1\n\n[Link]\nMACAddress=de:ad:be:ef:00:06\n' > "$LF"  # 현재를 .bak.1(06)과 동일하게
before=$(count_baks)
run_um mlan1 "de:ad:be:ef:00:08" >/dev/null 2>&1
assert_eq  "T08 dedup → backup count unchanged" "$before" "$(count_baks)"
assert_eq  "T08 dedup → .link updated(08)"   "de:ad:be:ef:00:08" "$(macof "$LF")"

# T09: 잘못된 MAC + 현재 유효 → 복구 skip(현재 유지)
reset_net
printf '[Match]\nOriginalName=mlan1\n\n[Link]\nMACAddress=aa:aa:aa:aa:aa:aa\n' > "$LF"
printf '[Match]\nOriginalName=mlan1\n\n[Link]\nMACAddress=bb:bb:bb:bb:bb:bb\n' > "$NET/21-mlan1.link.bak.1"
run_um mlan1 "INVALID-MAC" >/dev/null 2>&1
assert_eq  "T09 invalid+valid → keep current" "aa:aa:aa:aa:aa:aa" "$(macof "$LF")"

# T10: 잘못된 MAC + 현재 무효(빈 파일) → .bak.1에서 복구
reset_net
: > "$LF"
printf '[Match]\nOriginalName=mlan1\n\n[Link]\nMACAddress=cc:cc:cc:cc:cc:cc\n' > "$NET/21-mlan1.link.bak.1"
run_um mlan1 "INVALID-MAC" >/dev/null 2>&1
assert_eq  "T10 invalid+invalid-current → restore .bak.1" "cc:cc:cc:cc:cc:cc" "$(macof "$LF")"

# T11: MAC 인자 없음 → 아무것도 생성/변경 안 함
reset_net
run_um mlan1 "" >/dev/null 2>&1
if [ ! -e "$LF" ]; then log_pass "T11 no MAC arg → no file created"; else log_fail "T11 no MAC arg → file unexpectedly created"; fi

echo ""
echo "PASS: $PASS_COUNT"
echo "FAIL: $FAIL_COUNT"
[ "$FAIL_COUNT" -eq 0 ]
