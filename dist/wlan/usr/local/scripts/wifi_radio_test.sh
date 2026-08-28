#!/bin/bash
# wifi.sh mode/bw/radio-apply/radio-discard/ip CLI 테스트 — mock mlanutl/wpa_cli/iw/systemctl
# 하드웨어 불필요. 실행: bash wifi_radio_test.sh
# (lib 순수 함수 테스트는 wifi_init_config_test.sh, 이 파일은 CLI 경로 커버)
set -u
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
WIFI_SH="$SCRIPT_DIR/wifi.sh"
TD=$(mktemp -d)
trap 'rm -rf "$TD" /tmp/.radio_pending_mlan0.* /tmp/.radio_pending_mlan1.*' EXIT
STUB="$TD/bin"
export STATE_DIR="$TD/state"
export CALL_LOG="$TD/calls.log"
export WIFI_RUN_DIR="$TD/run"
mkdir -p "$STUB" "$STATE_DIR"

# ---- stubs ----
cat > "$STUB/mlanutl" <<'EOF'
#!/bin/bash
echo "mlanutl $*" >> "$CALL_LOG"
case "${2:-}" in
  bandcfg)
    if [ -n "${3:-}" ]; then
      n=$(cat "$STATE_DIR/bandcfg_fail" 2>/dev/null || echo 0)
      if [ "$n" -gt 0 ]; then echo $((n-1)) > "$STATE_DIR/bandcfg_fail"; exit 1; fi
      exit 0
    fi
    echo "Band Configuration:"; echo "  Infra Band: 0x35f ( B G A GN AN AAC GAX AAX )"; exit 0 ;;
  htcapinfo)
    if [ $# -eq 2 ]; then
      echo "HT cap info: "
      echo "    BG band:  0x05c20000"
      echo "     A band:  0x05c20000"
      exit 0
    fi
    [ -f "$STATE_DIR/htcap_fail" ] && exit 1
    exit 0 ;;
  vhtcfg)
    if [ $# -eq 4 ]; then
      [ -f "$STATE_DIR/vht_get_empty" ] && exit 0
      echo "11AC VHT Configuration: "
      echo "Band: 5G"
      echo "    BW config: Follow BW in VHT Capabilities"
      echo "    VHT Capabilities Info: 0x339b79f2"
      exit 0
    fi
    [ -f "$STATE_DIR/vht_fail" ] && exit 1
    exit 0 ;;
  htstreamcfg)
    if [ -f "$STATE_DIR/htstream_1x1" ]; then echo "HT stream is in 1x1 mode"; else echo "HT stream is in 2x2 mode"; fi
    exit 0 ;;
  11axcmd)
    [ -f "$STATE_DIR/omi_fail" ] && exit 1
    exit 0 ;;
  getdatarate)
    echo "Data Rate:"
    echo "  TX: "
    echo "    Type: $(cat "$STATE_DIR/datarate_type" 2>/dev/null || echo HE)"
    exit 0 ;;
  *) exit 0 ;;
esac
EOF
cat > "$STUB/wpa_cli" <<'EOF'
#!/bin/bash
echo "wpa_cli $*" >> "$CALL_LOG"
case "${3:-}" in
  abort_scan)
    n=$(cat "$STATE_DIR/abort_ok_remaining" 2>/dev/null || echo 0)
    if [ "$n" -gt 0 ]; then
      echo $((n-1)) > "$STATE_DIR/abort_ok_remaining"
      echo OK
    else
      echo FAIL
    fi ;;
  disconnect)  if [ -f "$STATE_DIR/disc_fail" ]; then echo FAIL; else echo OK; fi ;;
  reconfigure) if [ -f "$STATE_DIR/reconf_fail" ]; then echo FAIL; else echo OK; fi ;;
  reconnect)   if [ -f "$STATE_DIR/reconn_fail" ]; then echo FAIL; else echo OK; fi ;;
  reassociate) if [ -f "$STATE_DIR/reassoc_fail" ]; then echo FAIL; else echo OK; fi ;;
  status)      echo "wpa_state=$(cat "$STATE_DIR/wpa_state" 2>/dev/null || echo COMPLETED)" ;;
esac
exit 0
EOF
cat > "$STUB/iw" <<'EOF'
#!/bin/bash
echo "iw $*" >> "$CALL_LOG"
echo "Connected to aa:bb:cc:dd:ee:ff (on mlan0)"
EOF
cat > "$STUB/systemctl" <<'EOF'
#!/bin/bash
echo "systemctl $*" >> "$STATE_DIR/ip_calls.log"
[ -f "$STATE_DIR/networkd_fail" ] && exit 1
exit 0
EOF
chmod +x "$STUB/mlanutl" "$STUB/wpa_cli" "$STUB/iw" "$STUB/systemctl"
export PATH="$STUB:$PATH"

PASS=0; FAIL=0
check() { # desc expected_rc actual_rc [extra_cond]
  local desc="$1" exp="$2" act="$3" cond="${4:-0}"
  if [ "$act" = "$exp" ] && [ "$cond" = "0" ]; then
    echo "PASS: $desc"; PASS=$((PASS+1))
  else
    echo "FAIL: $desc (exit=$act expected=$exp cond=$cond)"; FAIL=$((FAIL+1))
  fi
}
fresh_json() {
  export WIFI_INIT_CONF_JSON="$TD/conf.json"
  echo '{"mlan0":{},"mlan1":{},"global":{}}' > "$WIFI_INIT_CONF_JSON"
  rm -f "$STATE_DIR"/* "$CALL_LOG" /tmp/.radio_pending_mlan0.* /tmp/.radio_pending_mlan1.*
}

# T1: mode SET 정상
fresh_json
bash "$WIFI_SH" 0 mode ac >/dev/null 2>&1; rc=$?
v=$(cat /tmp/.radio_pending_mlan0.mode 2>/dev/null)
j=$(jq -r '.mlan0.radio.mode // "none"' "$WIFI_INIT_CONF_JSON")
[ "$v" = "ac" ] && [ "$j" = "none" ]; check "T1 mode ac staged (not committed)" 0 "$rc" $?

# T2: mode 잘못된 값 → 2
fresh_json
bash "$WIFI_SH" 0 mode zz >/dev/null 2>&1; check "T2 mode invalid → 2" 2 $?

# T3: mlan1 + ax → 2
fresh_json
bash "$WIFI_SH" 1 mode ax >/dev/null 2>&1; check "T3 mlan1 ax → 2" 2 $?

# T4: bw SET 정상
fresh_json
bash "$WIFI_SH" 1 bw 40 >/dev/null 2>&1; rc=$?
v=$(cat /tmp/.radio_pending_mlan1.bw 2>/dev/null)
[ "$v" = "40" ]; check "T4 bw 40 staged" 0 "$rc" $?

# T5: bw 잘못된 값 → 2
fresh_json
bash "$WIFI_SH" 0 bw 160 >/dev/null 2>&1; check "T5 bw invalid → 2" 2 $?

# T6: radio-apply 설정 없음 → 0
fresh_json
bash "$WIFI_SH" 0 radio-apply >/dev/null 2>&1; check "T6 nothing to apply → 0" 0 $?

# T7: bw 40 stage → bw-only 경로 (reassociate + cap 적용, OMI 없음)
fresh_json
bash "$WIFI_SH" 0 bw 40 >/dev/null 2>&1
: > "$CALL_LOG"
bash "$WIFI_SH" 0 radio-apply 5 >/dev/null 2>&1; rc=$?
grep -q "htcapinfo 0x05c20000" "$CALL_LOG" && \
  grep -q "vhtcfg 2 2 0 0x339b79f2" "$CALL_LOG" && \
  grep -q "reassociate" "$CALL_LOG" && \
  ! grep -q "tx_omi" "$CALL_LOG" && \
  ! grep -q "disconnect" "$CALL_LOG" && \
  [ "$(jq -r '.mlan0.radio.bw' "$WIFI_INIT_CONF_JSON")" = "40" ] && \
  [ ! -f /tmp/.radio_pending_mlan0.bw ]
check "T7 bw40 bw-only → reassoc+cap, no omi/disconnect" 0 "$rc" $?

# T8: 성공 경로 (mode ac + bw 40, bandcfg 2회 실패 후 성공)
fresh_json
bash "$WIFI_SH" 0 mode ac >/dev/null 2>&1
bash "$WIFI_SH" 0 bw 40 >/dev/null 2>&1
echo 2 > "$STATE_DIR/bandcfg_fail"
: > "$CALL_LOG"
out=$(bash "$WIFI_SH" 0 radio-apply 5 2>&1); rc=$?
grep -q "mlanutl mlan0 bandcfg 0x5F" "$CALL_LOG" && \
  grep -q "mlanutl mlan0 htcapinfo 0x05c20000" "$CALL_LOG" && \
  grep -q "mlanutl mlan0 vhtcfg 2 2 0 0x339b79f2" "$CALL_LOG" && \
  grep -q "wpa_cli -i mlan0 disconnect" "$CALL_LOG" && \
  grep -q "wpa_cli -i mlan0 reconnect" "$CALL_LOG"
check "T8 mode path (disconnect+bandcfg+cap+reconnect)" 0 "$rc" $?
n_bandcfg=$(grep -c "bandcfg 0x5F" "$CALL_LOG")
[ "$n_bandcfg" = "3" ]; check "T8b bandcfg retried 3 calls" 0 0 $?

# T9: bandcfg 영구 실패 → 4
fresh_json
bash "$WIFI_SH" 0 mode n >/dev/null 2>&1
echo 99 > "$STATE_DIR/bandcfg_fail"
bash "$WIFI_SH" 0 radio-apply 3 >/dev/null 2>&1; check "T9 bandcfg fail → 4" 4 $?

# T10: assoc 미완료 → 8
fresh_json
bash "$WIFI_SH" 0 mode ac >/dev/null 2>&1
echo SCANNING > "$STATE_DIR/wpa_state"
: > "$CALL_LOG"
bash "$WIFI_SH" 0 radio-apply 2 >/dev/null 2>&1; rc=$?
grep -q "bandcfg 0x35f" "$CALL_LOG" && \
  [ "$(jq -r '.mlan0.radio.mode // "none"' "$WIFI_INIT_CONF_JSON")" = "none" ] && \
  [ -f /tmp/.radio_pending_mlan0.mode ]
check "T10 assoc timeout → 8 + rollback + no-commit" 8 "$rc" $?

# T11: wpa_cli disconnect 실패 → 7
fresh_json
bash "$WIFI_SH" 0 mode ac >/dev/null 2>&1
touch "$STATE_DIR/disc_fail"
bash "$WIFI_SH" 0 radio-apply >/dev/null 2>&1; check "T11 disconnect fail → 7" 7 $?

# T12: bw 20 → htcapinfo 0x05c00000 + bwcfg 0
fresh_json
bash "$WIFI_SH" 0 mode ac >/dev/null 2>&1
bash "$WIFI_SH" 0 bw 20 >/dev/null 2>&1
: > "$CALL_LOG"
bash "$WIFI_SH" 0 radio-apply 5 >/dev/null 2>&1; rc=$?
grep -q "htcapinfo 0x05c00000" "$CALL_LOG" && grep -q "vhtcfg 2 2 0 " "$CALL_LOG"
check "T12 bw20 → htcap bit17 clear (0x05c00000)" 0 "$rc" $?

# T13: bw 80 + mode ax → bwcfg 1, exit 0
fresh_json
bash "$WIFI_SH" 0 mode ax >/dev/null 2>&1
bash "$WIFI_SH" 0 bw 80 >/dev/null 2>&1
: > "$CALL_LOG"
bash "$WIFI_SH" 0 radio-apply 5 >/dev/null 2>&1; rc=$?
grep -q "bandcfg 0x35F" "$CALL_LOG" && grep -q "vhtcfg 2 2 1 " "$CALL_LOG"
check "T13 ax+bw80 mode path ok" 0 "$rc" $?

# T14: timeout 인자 검증 → 2
fresh_json
bash "$WIFI_SH" 0 radio-apply abc >/dev/null 2>&1; check "T14 bad timeout → 2" 2 $?

# T15: vhtcfg SET 실패 → 6
fresh_json
bash "$WIFI_SH" 0 mode ac >/dev/null 2>&1
bash "$WIFI_SH" 0 bw 80 >/dev/null 2>&1
touch "$STATE_DIR/vht_fail"
bash "$WIFI_SH" 0 radio-apply 3 >/dev/null 2>&1; check "T15 vhtcfg fail → 6" 6 $?

echo ""
echo "RESULT: PASS=$PASS FAIL=$FAIL"

# ===== 리뷰 수정 검증 추가 테스트 =====
# T16: JSON 파손 → radio-apply exit 9
fresh_json
echo '{"mlan0":' > "$WIFI_INIT_CONF_JSON"
bash "$WIFI_SH" 0 radio-apply >/dev/null 2>&1; check "T16 invalid JSON → 9" 9 $?

# T17: mlan1 + bw 20 (mode 미설정) → 게이트 제외, exit 0 + htcap bit17 clear
fresh_json
bash "$WIFI_SH" 1 bw 20 >/dev/null 2>&1
: > "$CALL_LOG"
bash "$WIFI_SH" 1 radio-apply 5 >/dev/null 2>&1; rc=$?
grep -q "mlanutl mlan1 htcapinfo 0x05c00000" "$CALL_LOG"
check "T17 mlan1 bw20 no-gate → 0" 0 "$rc" $?

# T18: reconfigure FAIL → exit 7 + rollback reconnect 시도
fresh_json
bash "$WIFI_SH" 0 mode ac >/dev/null 2>&1
touch "$STATE_DIR/reconf_fail"
: > "$CALL_LOG"
bash "$WIFI_SH" 0 radio-apply 5 >/dev/null 2>&1; rc=$?
grep -q "bandcfg 0x35f" "$CALL_LOG" && grep -q "reassociate" "$CALL_LOG"
check "T18 reconfigure FAIL → 7 + rollback(reassoc)" 7 "$rc" $?

# T19: STANDARD=n + mode ac → 경고 출력하되 persist 성공(exit 0)
fresh_json
jq '.mlan0.STANDARD = "n"' "$WIFI_INIT_CONF_JSON" > "$WIFI_INIT_CONF_JSON.t" && mv "$WIFI_INIT_CONF_JSON.t" "$WIFI_INIT_CONF_JSON"
out=$(bash "$WIFI_SH" 0 mode ac 2>&1); rc=$?
echo "$out" | grep -q "Warning: STANDARD=n"
check "T19 STANDARD conflict warning" 0 "$rc" $?

# T20: 대문자 입력 정규화 (bw AUTO)
fresh_json
bash "$WIFI_SH" 0 bw AUTO >/dev/null 2>&1; rc=$?
v=$(cat /tmp/.radio_pending_mlan0.bw 2>/dev/null)
[ "$v" = "auto" ]; check "T20 bw AUTO → auto staged" 0 "$rc" $?

# T21: mode GET 대문자 → GET 동작 (exit 0, 저장 없음)
fresh_json
bash "$WIFI_SH" 0 mode GET >/dev/null 2>&1; rc=$?
[ ! -f /tmp/.radio_pending_mlan0.mode ]; check "T21 mode GET uppercase" 0 "$rc" $?

# T22: standard 하향 시 radio.mode 초과 경고
fresh_json
bash "$WIFI_SH" 0 mode ac >/dev/null 2>&1
out=$(bash "$WIFI_SH" 0 standard n 2>&1); rc=$?
echo "$out" | grep -q "Warning: persisted radio.mode=ac"
check "T22 standard downgrade warning" 0 "$rc" $?

echo ""
echo "FINAL: PASS=$PASS FAIL=$FAIL"

# T23: STANDARD=ac(11ax 비활성) + bw 40 (mode 미설정) → 게이트 제외, exit 0
fresh_json
jq '.mlan0.STANDARD = "ac"' "$WIFI_INIT_CONF_JSON" > "$WIFI_INIT_CONF_JSON.t" && mv "$WIFI_INIT_CONF_JSON.t" "$WIFI_INIT_CONF_JSON"
bash "$WIFI_SH" 0 bw 40 >/dev/null 2>&1
: > "$CALL_LOG"
bash "$WIFI_SH" 0 radio-apply 5 >/dev/null 2>&1; rc=$?
grep -q "mlanutl mlan0 htcapinfo 0x05c20000" "$CALL_LOG" && grep -q "reassociate" "$CALL_LOG"
check "T23 STANDARD=ac + bw40 → 0 (cap+reassoc)" 0 "$rc" $?

# T24: STANDARD 없음 + bw 40 → OMI 경로 exit 0
fresh_json
bash "$WIFI_SH" 0 bw 40 >/dev/null 2>&1
: > "$CALL_LOG"
bash "$WIFI_SH" 0 radio-apply 5 >/dev/null 2>&1; rc=$?
grep -q "htcapinfo 0x05c20000" "$CALL_LOG" && grep -q "vhtcfg 2 2 0 " "$CALL_LOG" && grep -q "reassociate" "$CALL_LOG"
check "T24 no-STANDARD + bw40 → 0 (cap+reassoc)" 0 "$rc" $?

echo ""
echo "FINAL2: PASS=$PASS FAIL=$FAIL"

# T25: vhtcfg GET 빈값 + bw 40 (VHT 불필요) → skip + exit 0
fresh_json
bash "$WIFI_SH" 0 mode n >/dev/null 2>&1
bash "$WIFI_SH" 0 bw 40 >/dev/null 2>&1
touch "$STATE_DIR/vht_get_empty"
: > "$CALL_LOG"
out=$(bash "$WIFI_SH" 0 radio-apply 5 2>&1); rc=$?
! grep -q "vhtcfg 2 2 0 0x" "$CALL_LOG" && echo "$out" | grep -q "vhtcfg skipped"
check "T25 vht GET empty + bw40 → 0 (skip)" 0 "$rc" $?

# T26: vhtcfg GET 빈값 + bw 80 (VHT 필수) → exit 6
fresh_json
bash "$WIFI_SH" 0 mode ac >/dev/null 2>&1
bash "$WIFI_SH" 0 bw 80 >/dev/null 2>&1
touch "$STATE_DIR/vht_get_empty"
bash "$WIFI_SH" 0 radio-apply 3 >/dev/null 2>&1; check "T26 vht GET empty + bw80 → 6" 6 $?

echo ""
echo "FINAL3: PASS=$PASS FAIL=$FAIL"

# ===== freq↔mode 교차 검증 가드 테스트 =====
export WPA_CONF_DIR=$TD/wpa
mkdir -p "$WPA_CONF_DIR"
mkconf() {
  cat > "$WPA_CONF_DIR/wpa_supplicant-mlan0.conf" <<EOC
network={
    ssid="test"
    freq_list=$1
}
EOC
}

# T27: mode g + 5G 전용 freq → exit 11 (disconnect 호출 전 거부)
fresh_json
bash "$WIFI_SH" 0 mode g >/dev/null 2>&1
mkconf "5180 5200"
: > "$CALL_LOG"
bash "$WIFI_SH" 0 radio-apply 5 >/dev/null 2>&1; rc=$?
! grep -q "disconnect" "$CALL_LOG"
check "T27 g+5G-only freq → 11 (no disconnect)" 11 "$rc" $?

# T28: mode g + 2G/5G 혼합 → Notice + exit 0
fresh_json
bash "$WIFI_SH" 0 mode g >/dev/null 2>&1
mkconf "2412 5180"
out=$(bash "$WIFI_SH" 0 radio-apply 5 2>&1); rc=$?
echo "$out" | grep -q "Notice: mode=g"
check "T28 g+mixed freq → 0 + notice" 0 "$rc" $?

# T29: mode g + 2.4G 전용 → 정상 적용 (bandcfg 0x3)
fresh_json
bash "$WIFI_SH" 0 mode g >/dev/null 2>&1
mkconf "2412 2437"
: > "$CALL_LOG"
bash "$WIFI_SH" 0 radio-apply 5 >/dev/null 2>&1; rc=$?
grep -q "bandcfg 0x3" "$CALL_LOG"
check "T29 g+2.4G freq → 0 (bandcfg 0x3)" 0 "$rc" $?

# T30: mode b persist 시 5G 전용 conf 경고 (exit 0 유지)
fresh_json
mkconf "5180"
out=$(bash "$WIFI_SH" 0 mode b 2>&1); rc=$?
echo "$out" | grep -q "radio-apply will be rejected"
check "T30 mode b persist warning" 0 "$rc" $?

echo ""
echo "FINAL4: PASS=$PASS FAIL=$FAIL"

# ===== staged-commit 트랜잭션 테스트 =====
# T35: radio-discard — staged 제거
fresh_json
bash "$WIFI_SH" 0 mode ac >/dev/null 2>&1
bash "$WIFI_SH" 0 bw 40 >/dev/null 2>&1
bash "$WIFI_SH" 0 radio-discard >/dev/null 2>&1; rc=$?
[ ! -f /tmp/.radio_pending_mlan0.mode ] && [ ! -f /tmp/.radio_pending_mlan0.bw ]
check "T35 radio-discard clears staged" 0 "$rc" $?

# T36: staged가 committed를 override + 성공 시 commit 갱신
fresh_json
jq '.mlan0.radio.mode = "n"' "$WIFI_INIT_CONF_JSON" > "$WIFI_INIT_CONF_JSON.t" && mv "$WIFI_INIT_CONF_JSON.t" "$WIFI_INIT_CONF_JSON"
bash "$WIFI_SH" 0 mode ac >/dev/null 2>&1
: > "$CALL_LOG"
bash "$WIFI_SH" 0 radio-apply 5 >/dev/null 2>&1; rc=$?
grep -q "bandcfg 0x5F" "$CALL_LOG" && \
  [ "$(jq -r '.mlan0.radio.mode' "$WIFI_INIT_CONF_JSON")" = "ac" ] && \
  [ ! -f /tmp/.radio_pending_mlan0.mode ]
check "T36 staged overrides committed + commit" 0 "$rc" $?

# T38: committed만 있고 staged 없음 → 재적용 동작 (commit 변화 없음)
fresh_json
jq '.mlan0.radio.bw = "80"' "$WIFI_INIT_CONF_JSON" > "$WIFI_INIT_CONF_JSON.t" && mv "$WIFI_INIT_CONF_JSON.t" "$WIFI_INIT_CONF_JSON"
: > "$CALL_LOG"
bash "$WIFI_SH" 0 radio-apply 5 >/dev/null 2>&1; rc=$?
grep -q "vhtcfg 2 2 1 " "$CALL_LOG"
check "T38 committed-only reapply" 0 "$rc" $?

echo ""
echo "FINAL6: PASS=$PASS FAIL=$FAIL"

# T39: reconnect 실패 → exit 7 + 롤백 (스냅샷 복원 호출 확인)
fresh_json
bash "$WIFI_SH" 0 mode ac >/dev/null 2>&1
touch "$STATE_DIR/reconn_fail"
: > "$CALL_LOG"
bash "$WIFI_SH" 0 radio-apply 5 >/dev/null 2>&1; rc=$?
grep -q "bandcfg 0x35f" "$CALL_LOG" && [ -f /tmp/.radio_pending_mlan0.mode ]
check "T39 reconnect fail → 7 + rollback" 7 "$rc" $?

# ===== 재설계(reassoc/default) 테스트 =====
# T40: bw-only reassociate 실패 → exit 7 + rollback
fresh_json
bash "$WIFI_SH" 0 bw 40 >/dev/null 2>&1
touch "$STATE_DIR/reassoc_fail"
: > "$CALL_LOG"
bash "$WIFI_SH" 0 radio-apply 5 >/dev/null 2>&1; rc=$?
! grep -q "disconnect" "$CALL_LOG" && [ -f /tmp/.radio_pending_mlan0.bw ]
check "T40 bw-only reassoc fail → 7 + staged kept" 7 "$rc" $?

# T41: bw default → cap 80 적용 + reassoc + radio.bw 삭제(commit 아님)
fresh_json
jq '.mlan0.radio.bw = "40"' "$WIFI_INIT_CONF_JSON" > "$WIFI_INIT_CONF_JSON.t" && mv "$WIFI_INIT_CONF_JSON.t" "$WIFI_INIT_CONF_JSON"
bash "$WIFI_SH" 0 bw default >/dev/null 2>&1
: > "$CALL_LOG"
bash "$WIFI_SH" 0 radio-apply 5 >/dev/null 2>&1; rc=$?
grep -q "htcapinfo 0x05c20000" "$CALL_LOG" && grep -q "vhtcfg 2 2 1 " "$CALL_LOG" && \
  grep -q "reassociate" "$CALL_LOG" && \
  [ "$(jq -r '.mlan0.radio.bw // "DELETED"' "$WIFI_INIT_CONF_JSON")" = "DELETED" ]
check "T41 bw default → cap80+reassoc+radio.bw deleted" 0 "$rc" $?

# T42: bw default 입력 검증 (stage 성공)
fresh_json
bash "$WIFI_SH" 0 bw default >/dev/null 2>&1; rc=$?
[ "$(cat /tmp/.radio_pending_mlan0.bw 2>/dev/null)" = "default" ]
check "T42 bw default staged" 0 "$rc" $?

# T43: mode+bw 동시 stage → mode 경로(disconnect), bw도 cap 적용
fresh_json
bash "$WIFI_SH" 0 mode ax >/dev/null 2>&1
bash "$WIFI_SH" 0 bw 20 >/dev/null 2>&1
: > "$CALL_LOG"
bash "$WIFI_SH" 0 radio-apply 5 >/dev/null 2>&1; rc=$?
grep -q "disconnect" "$CALL_LOG" && grep -q "bandcfg 0x35F" "$CALL_LOG" && \
  grep -q "htcapinfo 0x05c00000" "$CALL_LOG" && \
  [ "$(jq -r '.mlan0.radio.mode' "$WIFI_INIT_CONF_JSON")" = "ax" ] && \
  [ "$(jq -r '.mlan0.radio.bw' "$WIFI_INIT_CONF_JSON")" = "20" ]
check "T43 mode+bw → mode path, both committed" 0 "$rc" $?

# T44: committed mode가 라이브와 다름(부팅 skip 복구) → bandcfg 재적용 (mode 경로)
# stub bandcfg GET은 0x35f(=ax) 반환. committed mode=ac(0x5F)면 라이브와 달라 APPLY_MODE 승격.
fresh_json
jq '.mlan0.radio.mode = "ac"' "$WIFI_INIT_CONF_JSON" > "$WIFI_INIT_CONF_JSON.t" && mv "$WIFI_INIT_CONF_JSON.t" "$WIFI_INIT_CONF_JSON"
: > "$CALL_LOG"
bash "$WIFI_SH" 0 radio-apply 5 >/dev/null 2>&1; rc=$?
grep -q "disconnect" "$CALL_LOG" && grep -q "bandcfg 0x5F" "$CALL_LOG"
check "T44 committed mode != live → bandcfg reapply" 0 "$rc" $?

# T45: committed mode가 라이브와 같음 → bandcfg 생략 (no disconnect)
# stub bandcfg GET=0x35f(ax). committed mode=ax(0x35F) → normalize 일치 → bw-only.
fresh_json
jq '.mlan0.radio = {"mode":"ax","bw":"40"}' "$WIFI_INIT_CONF_JSON" > "$WIFI_INIT_CONF_JSON.t" && mv "$WIFI_INIT_CONF_JSON.t" "$WIFI_INIT_CONF_JSON"
: > "$CALL_LOG"
bash "$WIFI_SH" 0 radio-apply 5 >/dev/null 2>&1; rc=$?
! grep -q "disconnect" "$CALL_LOG" && grep -q "reassociate" "$CALL_LOG" && grep -q "htcapinfo 0x05c20000" "$CALL_LOG"
check "T45 committed mode == live → bw-only reassoc" 0 "$rc" $?

# T46: committed mode 라이브 일치 + bw 없음 → nothing-to-apply, reassoc 없음
fresh_json
jq '.mlan0.radio = {"mode":"ax"}' "$WIFI_INIT_CONF_JSON" > "$WIFI_INIT_CONF_JSON.t" && mv "$WIFI_INIT_CONF_JSON.t" "$WIFI_INIT_CONF_JSON"
: > "$CALL_LOG"
out=$(bash "$WIFI_SH" 0 radio-apply 5 2>&1); rc=$?
echo "$out" | grep -q "nothing to apply" && ! grep -q "reassociate" "$CALL_LOG" && ! grep -q "disconnect" "$CALL_LOG"
check "T46 mode live + no bw → nothing-to-apply (no reassoc)" 0 "$rc" $?

# T47: wifi ip get (apply 아님) → usage exit, systemctl 미호출
fresh_json
rm -f "$STATE_DIR/ip_calls.log"
bash "$WIFI_SH" ip get >/dev/null 2>&1; rc=$?
[ ! -f "$STATE_DIR/ip_calls.log" ] || ! grep -q "systemctl" "$STATE_DIR/ip_calls.log"
[ "$rc" != "0" ] && check "T47 ip get → non-zero (usage), no systemctl" "$rc" "$rc" $? || check "T47 ip get → non-zero" 1 "$rc" 1

# T48: wifi ip apply → systemctl restart systemd-networkd 호출 + exit 0
fresh_json
rm -f "$STATE_DIR/ip_calls.log" "$STATE_DIR/networkd_fail"
bash "$WIFI_SH" ip apply >/dev/null 2>&1; rc=$?
grep -q "systemctl restart systemd-networkd" "$STATE_DIR/ip_calls.log" 2>/dev/null
check "T48 ip apply → systemctl restart + exit 0" 0 "$rc" $?

# T49: ip apply + networkd restart 실패 → exit 1
fresh_json
rm -f "$STATE_DIR/ip_calls.log"
touch "$STATE_DIR/networkd_fail"
bash "$WIFI_SH" ip apply >/dev/null 2>&1; check "T49 ip apply networkd fail → 1" 1 $?

# T50: 외부 scan/connect가 transition lock을 소유하면 radio-apply는 live FW/WPA를
# 건드리기 전에 fail-closed 해야 한다.
fresh_json
bash "$WIFI_SH" 0 bw 40 >/dev/null 2>&1
export WIFI_RUN_DIR="$TD/run"
export WIFI_SCAN_TRANSITION_LOCK_TIMEOUT=0
mkdir -p "$WIFI_RUN_DIR"
flock "$WIFI_RUN_DIR/mlan0.scan-transition.lock" -c 'sleep 10' &
_holder=$!
sleep 0.1
: > "$CALL_LOG"
bash "$WIFI_SH" 0 radio-apply 5 >/dev/null 2>&1; rc=$?
kill "$_holder" 2>/dev/null || true
wait "$_holder" 2>/dev/null || true
[ "$rc" -ne 0 ] && [ ! -s "$CALL_LOG" ]
check "T50 held transition lock blocks radio-apply before live calls" "$rc" "$rc" $?
unset WIFI_SCAN_TRANSITION_LOCK_TIMEOUT

# T51: FD7 직전에 시작돼 비동기로 남은 native scan은 OK→FAIL quiesce 뒤에만
# radio live transaction을 시작한다.
fresh_json
bash "$WIFI_SH" 0 bw 40 >/dev/null 2>&1
echo 1 > "$STATE_DIR/abort_ok_remaining"
: > "$CALL_LOG"
bash "$WIFI_SH" 0 radio-apply 5 >/dev/null 2>&1; rc=$?
[ "$(grep -c 'abort_scan' "$CALL_LOG")" -eq 2 ]
_abort_line=$(grep -n 'abort_scan' "$CALL_LOG" | tail -1 | cut -d: -f1)
_live_line=$(grep -nE 'mlanutl mlan0 (bandcfg|htcapinfo)|wpa_cli -i mlan0 (disconnect|reassociate)' "$CALL_LOG" | head -1 | cut -d: -f1)
[ -n "$_abort_line" ] && [ -n "$_live_line" ] && [ "$_abort_line" -lt "$_live_line" ]
check "T51 radio-apply quiesces native scan before live calls" 0 "$rc" $?

echo ""
echo "RESULT: PASS=$PASS FAIL=$FAIL"
[ "$FAIL" = "0" ]
