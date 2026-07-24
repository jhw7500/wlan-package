#!/bin/sh
# fake-hwclock.sh 단조성(save forward-guard) 회귀 테스트.
# 핵심 계약: save 는 '지금껏 본 최댓값'만 유지해, 부팅 직후 시계가 뒤처진 순간에
# 좋은 저장값을 덮어써 시간이 뒤로 가는 것(→ 로그 중복)을 막는다. 단, NTP 동기화된
# 시각은 권위라 뒤로 스텝(미래 오설정 교정)도 허용한다.
#
# 실제 시계를 바꾸는 경로(load 성공 / set 성공)는 root+시계변경이 필요해 제외하고,
# 클럭을 mutate 하지 않는 결정 로직(save 채택 여부, 인자 검증)만 검증한다.
set -u
SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
SUT="$SCRIPT_DIR/fake-hwclock.sh"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
PASS=0; FAIL=0
ok()   { PASS=$((PASS+1)); echo "ok   - $1"; }
ng()   { FAIL=$((FAIL+1)); echo "FAIL - $1"; }

# timedatectl 스텁: 인자로 준 값(yes/no)을 NTPSynchronized 로 보고한다.
make_timedatectl() {
    mkdir -p "$TMP/bin"
    cat > "$TMP/bin/timedatectl" <<EOF
#!/bin/sh
echo "$1"
EOF
    chmod +x "$TMP/bin/timedatectl"
}

run_save() {  # $1=NTPSynchronized 스텁값
    make_timedatectl "$1"
    PATH="$TMP/bin:$PATH" FAKE_HWCLOCK_STATE="$STATE_F" \
        FAKE_HWCLOCK_LEGACY_STATE="$TMP/none" sh "$SUT" save
}

STATE_F="$TMP/state"

# --- 1) 저장값 없음(부트스트랩) → 기록 ---
rm -f "$STATE_F"
run_save no
if [ -f "$STATE_F" ]; then ok "no state -> writes (bootstrap)"; else ng "no state -> writes"; fi

# --- 2) 저장값이 과거 → 현재가 더 크므로 갱신 ---
echo "2000-01-01 00:00:00" > "$STATE_F"
run_save no
NEW=$(cat "$STATE_F")
if [ "$NEW" != "2000-01-01 00:00:00" ]; then ok "past saved -> overwritten forward"; else ng "past saved -> overwritten forward (got $NEW)"; fi

# --- 3) 저장값이 미래 + NTP 없음 → 역행 방지로 SKIP (핵심: 로그중복 방지) ---
echo "2035-01-01 00:00:00" > "$STATE_F"
run_save no
KEEP=$(cat "$STATE_F")
if [ "$KEEP" = "2035-01-01 00:00:00" ]; then ok "future saved, no NTP -> skip (monotonic guard)"; else ng "future saved, no NTP -> skip (got $KEEP)"; fi

# --- 4) 저장값이 미래 + NTP 동기화 → 권위라 뒤로 스텝(교정) 허용 ---
echo "2035-01-01 00:00:00" > "$STATE_F"
run_save yes
FIXED=$(cat "$STATE_F")
if [ "$FIXED" != "2035-01-01 00:00:00" ]; then ok "future saved, NTP synced -> overwrite (heal)"; else ng "future saved, NTP synced -> overwrite (got $FIXED)"; fi

# --- 5) 손상된 저장값 + NTP 없음 → 파싱 실패는 '미래 아님'으로 취급, 갱신되어 자가복구 ---
echo "garbage-not-a-date" > "$STATE_F"
run_save no
REPAIR=$(cat "$STATE_F")
case "$REPAIR" in
    garbage*) ng "corrupt saved -> should be repaired (still $REPAIR)";;
    *) ok "corrupt saved -> repaired by write";;
esac

# --- 6) set: 인자 없음 → usage + 비정상 종료 ---
if PATH="$TMP/bin:$PATH" FAKE_HWCLOCK_STATE="$STATE_F" sh "$SUT" set >/dev/null 2>&1; then
    ng "set without arg -> should fail"
else
    ok "set without arg -> exits non-zero"
fi

# --- 7) set: 잘못된 날짜 → 시계 변경 없이 실패 ---
if PATH="$TMP/bin:$PATH" FAKE_HWCLOCK_STATE="$STATE_F" sh "$SUT" set "not-a-date" >/dev/null 2>&1; then
    ng "set invalid date -> should fail"
else
    ok "set invalid date -> exits non-zero"
fi

# --- 8) 알 수 없는 서브커맨드 → usage + 비정상 종료 ---
if sh "$SUT" bogus >/dev/null 2>&1; then
    ng "unknown subcommand -> should fail"
else
    ok "unknown subcommand -> exits non-zero"
fi

echo "-----"
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
