#!/bin/bash
# update_mac.sh 동작 검증 테스트.
# 실제 update_mac.sh를 SYSTEMD_NETWORK_DIR 오버라이드 + logger 셰임으로 비-root에서 실행한다.
# 사용: bash update_mac_test.sh   (성공 시 exit 0)
set -uo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
UPDATE_MAC="$SCRIPT_DIR/update_mac.sh"
WRITE_MAC="$SCRIPT_DIR/write_mac.sh"

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
run_wm() { # <iface> <mac>
  SYSTEMD_NETWORK_DIR="$NET" WIFI_INIT_CONF_JSON="$WORK/no-config.json" \
    PATH="$BINDIR:$PATH" bash "$WRITE_MAC" "$1" "$2"
}
macof() { grep -oP '^MACAddress=\K.*' "$1" 2>/dev/null || true; }
count_baks() { ls -1 "$NET"/21-mlan1.link.bak.* 2>/dev/null | wc -l | tr -d ' '; }
count_tmps() { find "$NET" -maxdepth 1 -type f -name '*.link.tmp.*' 2>/dev/null | wc -l | tr -d ' '; }
reset_net() { rm -rf "$NET"; mkdir -p "$NET"; }

assert_eq() { # desc expected actual
  if [ "$2" = "$3" ]; then log_pass "$1"; else log_fail "$1 (expected [$2] got [$3])"; fi
}
assert_has() { # desc file pattern
  if grep -q "$3" "$2" 2>/dev/null; then log_pass "$1"; else log_fail "$1 (missing /$3/ in $2)"; fi
}

if [ ! -x "$UPDATE_MAC" ]; then echo "missing/not-executable: $UPDATE_MAC" >&2; exit 1; fi
if [ ! -x "$WRITE_MAC" ]; then echo "missing/not-executable: $WRITE_MAC" >&2; exit 1; fi

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

# T12: 실제 MAC 변경을 반복해도 install 입력용 임시파일이 남지 않아야 함
reset_net
run_um mlan1 "de:ad:be:ef:00:11" >/dev/null 2>&1
run_um mlan1 "de:ad:be:ef:00:12" >/dev/null 2>&1
assert_eq "T12 no orphan .link.tmp after updates" "0" "$(count_tmps)"

# T13: 형식만 맞는 비할당 주소(all-zero/broadcast/multicast)는 거부
for bad_mac in \
  "00:00:00:00:00:00" \
  "ff:ff:ff:ff:ff:ff" \
  "01:00:5e:00:00:01"; do
  reset_net
  run_um mlan1 "$bad_mac" >/dev/null 2>&1
  rc=$?
  assert_eq "T13 reject non-unicast $bad_mac" "1" "$rc"
  if [ ! -e "$LF" ]; then
    log_pass "T13 rejected MAC created no .link ($bad_mac)"
  else
    log_fail "T13 rejected MAC unexpectedly created $LF ($bad_mac)"
  fi
done

# T14: 다른 활성 .link가 이미 사용하는 MAC은 대소문자와 무관하게 거부
reset_net
run_um mlan0 "DE:AD:BE:EF:00:21" >/dev/null 2>&1
run_um mlan1 "de:ad:be:ef:00:21" >/dev/null 2>&1
rc=$?
assert_eq "T14 reject MAC used by another .link" "1" "$rc"
if [ ! -e "$LF" ]; then
  log_pass "T14 duplicate rejection created no mlan1 .link"
else
  log_fail "T14 duplicate rejection unexpectedly created $LF"
fi

# T15: write_mac.sh도 같은 의미 검증/활성 .link 중복 검사를 사용
reset_net
run_wm mlan1 "01:00:5e:00:00:01" >/dev/null 2>&1
rc=$?
assert_eq "T15 write_mac rejects multicast" "1" "$rc"
run_um mlan0 "de:ad:be:ef:00:31" >/dev/null 2>&1
run_wm mlan1 "DE:AD:BE:EF:00:31" >/dev/null 2>&1
rc=$?
assert_eq "T15 write_mac rejects cross-link duplicate" "1" "$rc"

# T16: write_mac.sh 성공 경로는 active/fixed backup을 갱신하고 tmp를 남기지 않음
reset_net
run_um mlan1 "de:ad:be:ef:00:41" >/dev/null 2>&1
run_wm mlan1 "DE:AD:BE:EF:00:42" >/dev/null 2>&1
rc=$?
assert_eq "T16 write_mac succeeds" "0" "$rc"
assert_eq "T16 write_mac normalizes active MAC" "de:ad:be:ef:00:42" "$(macof "$LF")"
assert_eq "T16 write_mac fixed backup" "de:ad:be:ef:00:42" "$(macof "$LF.bak")"
assert_eq "T16 write_mac leaves no tmp" "0" "$(count_tmps)"

# T17: cleanup은 owned orphan/숫자 상한 초과분만 제거하고 정상 세대·사용자 .link는 보존
reset_net
touch "$LF.tmp.old1" "$LF.tmp.old2" "$LF.bak.5" "$LF.bak.6" "$LF.bak.99"
touch "$NET/99-operator.link"
run_um mlan1 --cleanup >/dev/null 2>&1
rc=$?
assert_eq "T17 cleanup command succeeds" "0" "$rc"
assert_eq "T17 cleanup removes orphan tmp" "0" "$(count_tmps)"
if [ -e "$LF.bak.5" ] && [ ! -e "$LF.bak.6" ] && [ ! -e "$LF.bak.99" ]; then
  log_pass "T17 cleanup enforces backup generation cap"
else
  log_fail "T17 cleanup did not enforce backup generation cap"
fi
if [ -e "$NET/99-operator.link" ]; then
  log_pass "T17 cleanup preserves operator .link"
else
  log_fail "T17 cleanup removed operator .link"
fi
if [ ! -e "$LF" ]; then
  log_pass "T17 cleanup-only created no active .link"
else
  log_fail "T17 cleanup-only unexpectedly created $LF"
fi

# T18: [Match]의 MACAddress는 선택 조건이지 할당 주소가 아니므로 충돌로 오인하지 않음
reset_net
printf '[Match]\nMACAddress=de:ad:be:ef:00:51\n\n[Link]\n' > "$NET/99-operator.link"
run_um mlan1 "de:ad:be:ef:00:51" >/dev/null 2>&1
rc=$?
assert_eq "T18 ignore match-only MACAddress for assignment conflict" "0" "$rc"
assert_eq "T18 requested MAC assigned to mlan1" "de:ad:be:ef:00:51" "$(macof "$LF")"

# T19: own .link의 [Match] MACAddress는 보존하고 [Link] 할당 주소만 추가/교체
reset_net
printf '[Match]\nMACAddress=02:00:00:00:00:01\nOriginalName=mlan1\n\n[Link]\n' > "$LF"
run_um mlan1 "de:ad:be:ef:00:61" >/dev/null 2>&1
rc=$?
assert_eq "T19 update with match-side MAC succeeds" "0" "$rc"
assert_has "T19 preserves [Match] MACAddress" "$LF" '^MACAddress=02:00:00:00:00:01$'
assert_has "T19 writes [Link] MACAddress" "$LF" '^MACAddress=de:ad:be:ef:00:61$'
assert_eq "T19 keeps exactly match+link MACAddress" "2" "$(grep -c '^MACAddress=' "$LF")"

echo ""
echo "PASS: $PASS_COUNT"
echo "FAIL: $FAIL_COUNT"
[ "$FAIL_COUNT" -eq 0 ]
