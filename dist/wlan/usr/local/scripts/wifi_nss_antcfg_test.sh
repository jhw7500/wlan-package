#!/bin/bash
# wifi.sh nss/antcfg CLI 테스트 (#504) — mock mlanutl/install/logger, 보드는 WIFI_SOC_ID_PATH 로 고정
# 하드웨어 불필요. 실행: bash wifi_nss_antcfg_test.sh
# 설정은 패키지 템플릿(opt/wlan/config/wifi_init_conf.json) 사본에서 시작한다 — 실제 기본값·_comment·
# mlan1 의 빈 verify 가 그대로 들어간 상태에서 CLI 가 무엇을 쓰고 무엇을 거부하는지 본다.
set -u
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
WIFI_SH="$SCRIPT_DIR/wifi.sh"
TEMPLATE="$SCRIPT_DIR/../../../opt/wlan/config/wifi_init_conf.json"
TD=$(mktemp -d)
trap 'rm -rf "$TD"' EXIT
STUB="$TD/bin"
export STATE_DIR="$TD/state"
export CALL_LOG="$TD/calls.log"
export WIFI_RUN_DIR="$TD/run"
export WIFI_SOC_ID_PATH="$TD/soc_id"
export WIFI_INIT_CONF_JSON="$TD/conf.json"
mkdir -p "$STUB" "$STATE_DIR" "$WIFI_RUN_DIR"
for need in jq flock; do
    command -v "$need" >/dev/null 2>&1 || { echo "FAIL: $need is required"; exit 1; }
done
[ -f "$TEMPLATE" ] || { echo "FAIL: template missing: $TEMPLATE"; exit 1; }
# wifi.sh 는 wifi_fw_config_lib.sh 만은 설치본을 먼저 읽는다. 설치본이 저장소 사본과 다르면
# 이 시험은 저장소의 검증 함수를 보지 못하므로 결과를 내지 않고 멈춘다.
if [ -e /usr/local/scripts/wifi_fw_config_lib.sh ] \
   && ! cmp -s /usr/local/scripts/wifi_fw_config_lib.sh "$SCRIPT_DIR/wifi_fw_config_lib.sh"; then
    echo "FAIL: /usr/local/scripts/wifi_fw_config_lib.sh shadows $SCRIPT_DIR/wifi_fw_config_lib.sh"
    exit 1
fi

# ---- stubs ----
# mlanutl: 호출을 모두 기록한다. 인자 없는 antcfg GET 에만 실기 형식으로 답한다
# (docs/ant_1x1_positive_control_2026-08-25.md 의 원문: Mode of Tx/Rx path + NSS limit 줄).
cat > "$STUB/mlanutl" <<'EOF'
#!/bin/bash
echo "mlanutl $*" >> "$CALL_LOG"
if [ "${2:-}" = antcfg ] && [ $# -eq 2 ]; then
    path=$(cat "$STATE_DIR/live_path" 2>/dev/null || echo 0x303)
    printf 'Mode of Tx path is %s\n' "$path"
    printf 'Mode of Rx path is %s\n' "$path"
    if [ ! -f "$STATE_DIR/no_nss_line" ]; then
        v=$(cat "$STATE_DIR/live_htstream" 2>/dev/null || echo 0x1111)
        n=$((v))
        printf 'NSS limit (antcfg): 2G rx=%d tx=%d, 5G rx=%d tx=%d  [user_htstream=%s]\n' \
            $((n & 15)) $(((n >> 4) & 15)) $(((n >> 8) & 15)) $(((n >> 12) & 15)) "$v"
    fi
fi
exit 0
EOF
# safe_install_sync 의 install -o root -g root 를 비루트에서 돌리기 위한 대역 — 호출을 기록한다.
cat > "$STUB/install" <<'EOF'
#!/bin/bash
echo "install $*" >> "$CALL_LOG"
args=()
while [ $# -gt 0 ]; do
    case "$1" in
        -o|-g) shift ;;
        *) args+=("$1") ;;
    esac
    shift
done
exec /usr/bin/install "${args[@]}"
EOF
cat > "$STUB/logger" <<'EOF'
#!/bin/bash
echo "logger $*" >> "${STATE_DIR}/logger.log"
EOF
chmod +x "$STUB/mlanutl" "$STUB/install" "$STUB/logger"

# ---- helpers ----
PASS=0
FAIL=0
check() {
    local desc="$1"
    shift
    if "$@"; then
        PASS=$((PASS + 1)); echo "PASS: $desc"
    else
        FAIL=$((FAIL + 1)); echo "FAIL: $desc"
        printf '  rc=%s output:\n%s\n' "${RC:-}" "$(printf '%s\n' "${OUT:-}" | head -12 | sed 's/^/    /')"
    fi
}
# $1 = SoC id (기본 i.MX93). 설정 파일은 0600 으로 두어 safe_install_sync(0644) 경유 기록을 구분한다.
fresh() {
    cp "$TEMPLATE" "$WIFI_INIT_CONF_JSON"
    chmod 600 "$WIFI_INIT_CONF_JSON"
    printf '%s\n' "${1:-i.MX93}" > "$WIFI_SOC_ID_PATH"
    rm -f "$STATE_DIR"/*
    : > "$CALL_LOG"
}
edit() {
    local t
    t=$(mktemp "$TD/edit.XXXXXX")
    jq "$1" "$WIFI_INIT_CONF_JSON" > "$t" && cat "$t" > "$WIFI_INIT_CONF_JSON"
    rm -f "$t"
}
snap() { cp -p "$WIFI_INIT_CONF_JSON" "$TD/before.json"; : > "$CALL_LOG"; }
run() { OUT=$(PATH="$STUB:$PATH" bash "$WIFI_SH" "$@" 2>&1); RC=$?; }
rc_is() { [ "$RC" -eq "$1" ]; }
unchanged() { cmp -s "$TD/before.json" "$WIFI_INIT_CONF_JSON"; }
refused() { [ "$RC" -ne 0 ] && unchanged; }
# 지정한 경로 말고는 문서 전체가 그대로인가 (jq 경로식을 del 에 넘긴다)
same_except() { [ "$(jq -S "del($1)" "$TD/before.json")" = "$(jq -S "del($1)" "$WIFI_INIT_CONF_JSON")" ]; }
sec_is() { [ "$(jq -c --arg i "$1" --arg s "$2" '.[$i][$s] | del(._comment?)' "$WIFI_INIT_CONF_JSON")" = "$3" ]; }
has_line() { printf '%s\n' "$OUT" | grep -qxF -- "$1"; }
lacks_text() { ! printf '%s\n' "$OUT" | grep -qF -- "$1"; }
calls_are() { [ "$(cat "$CALL_LOG")" = "$1" ]; }
no_mlanutl() { ! grep -q '^mlanutl' "$CALL_LOG"; }
installed_via_stage() {
    grep '^install ' "$CALL_LOG" | grep -qF -- "${WIFI_INIT_CONF_JSON}.install." \
        && [ "$(stat -c %a "$WIFI_INIT_CONF_JSON")" = 644 ]
}

W_HT7_MLAN0="Warning: mcs_tier.ht=7 keeps the effective TX NSS of mlan0 at 1 (Tx nibble >= 2 has no effect); use 'wifi mlan0 mcs ht 15' to allow TX NSS 2"
W_WEDGE="Warning: the gate guards against physical 1-path antenna settings, a p149.115 scan wedge cofactor (docs/wifi_init_conf_guide.md §11.4b)"
w_gate() { echo "Warning: --force: $1 is checked by the imx93 boot scan-profile gate; every wifi_init start will log local0.err and record product_scan_profile in /run/wifi/fwcfg_unapplied_mlan0 (shown by 'wifi 0 info')"; }

# ===== A. 조회: JSON 불변, 읽기 전용 GET 한 번, 밴드별 실효 TX NSS 상한 =====
fresh; snap; run 0 nss
check "A1 nss GET rc=0" rc_is 0
check "A1 nss GET leaves JSON unchanged" unchanged
check "A1 nss GET shows the JSON section" \
    has_line "antcfgnss (JSON $WIFI_INIT_CONF_JSON, mlan0): {\"enabled\":true,\"value\":\"0x1111\",\"verify\":{\"user_htstream\":\"0x1111\"}}"
check "A1 nss GET shows the mcs_tier ht tier" has_line "mcs_tier (JSON): enabled=true ht=15 -> ht tier NSS 2"
check "A1 nss GET shows live user_htstream" has_line "user_htstream (live, mlanutl mlan0 antcfg): 0x1111"
check "A1 nss GET configured cap" has_line "  configured 0x1111 (next wifi_init start): 5G 1, 2.4G 1"
check "A1 nss GET live cap" has_line "  live 0x1111: 5G 1, 2.4G 1"
check "A1 nss GET issues only the read-only antcfg GET" calls_are "mlanutl mlan0 antcfg"

fresh
edit '.mlan0.antcfgnss.value = "0x2211" | .mlan0.antcfgnss.verify.user_htstream = "0x2211"'
echo 0x2121 > "$STATE_DIR/live_htstream"
snap; run 0 nss get
check "A2 nss get rc=0" rc_is 0
check "A2 nss get leaves JSON unchanged" unchanged
check "A2 cap is per band (0x2211 + ht 15 -> 5G 2, 2.4G 1)" \
    has_line "  configured 0x2211 (next wifi_init start): 5G 2, 2.4G 1"
check "A2 cap includes the Rx nibble (live 0x2121 -> 1/1)" has_line "  live 0x2121: 5G 1, 2.4G 1"

fresh
edit '.mlan0.mcs_tier.ht = "7" | .mlan0.antcfgnss.value = "0x2222" | .mlan0.antcfgnss.verify.user_htstream = "0x2222"'
snap; run 0 nss
check "A3 ht 7 tier line" has_line "mcs_tier (JSON): enabled=true ht=7 -> ht tier NSS 1"
check "A3 ht 7 caps configured 0x2222 at 1" has_line "  configured 0x2222 (next wifi_init start): 5G 1, 2.4G 1"

fresh
edit '.mlan0.mcs_tier.enabled = false | .mlan0.antcfgnss.enabled = false'
touch "$STATE_DIR/no_nss_line"
snap; run 0 nss
check "A4 rc=0" rc_is 0
check "A4 disabled mcs_tier does not cap" has_line "mcs_tier (JSON): enabled=false ht=15 -> ht tier not applied"
check "A4 disabled antcfgnss" has_line "  configured: antcfgnss disabled (driver default host NSS intent)"
check "A4 live value missing" has_line "user_htstream (live, mlanutl mlan0 antcfg): (not available)"
check "A4 live cap missing" has_line "  live: user_htstream not available"

fresh; snap; run 1 antcfg
check "A5 antcfg GET rc=0" rc_is 0
check "A5 antcfg GET leaves JSON unchanged" unchanged
check "A5 antcfg GET shows the JSON section" \
    has_line "antcfg (JSON $WIFI_INIT_CONF_JSON, mlan1): {\"enabled\":false,\"tx\":\"\",\"rx\":\"\",\"verify\":{\"physical_tx\":\"\",\"physical_rx\":\"\",\"user_htstream\":\"\"}}"
check "A5 antcfg GET shows the raw live GET" has_line "Mode of Tx path is 0x303"
check "A5 antcfg GET issues only the read-only antcfg GET" calls_are "mlanutl mlan1 antcfg"

# ===== B. nss 값 SET: value·verify 같이 기록, 다른 키 불변, mlanutl 미호출, 원자 기록 =====
fresh; snap; run 0 nss 2
check "B1 nss 2 rc=0" rc_is 0
check "B1 nss 2 records 0x2222 in value and verify" \
    sec_is mlan0 antcfgnss '{"enabled":true,"value":"0x2222","verify":{"user_htstream":"0x2222"}}'
check "B1 nothing else changed" \
    same_except '.mlan0.antcfgnss.enabled, .mlan0.antcfgnss.value, .mlan0.antcfgnss.verify.user_htstream'
check "B1 SET does not call mlanutl" no_mlanutl
check "B1 written through safe_install_sync (install stage, 0644)" installed_via_stage

fresh; edit '.mlan0.antcfgnss.value = "0x2222" | .mlan0.antcfgnss.verify.user_htstream = "0x2222"'
snap; run 0 nss 1
check "B2 nss 1 rc=0" rc_is 0
check "B2 nss 1 records 0x1111" \
    sec_is mlan0 antcfgnss '{"enabled":true,"value":"0x1111","verify":{"user_htstream":"0x1111"}}'

fresh; snap; run 0 nss 0x2121
check "B3 nss 0x2121 rc=0" rc_is 0
check "B3 nss 0x2121 records the value as given" \
    sec_is mlan0 antcfgnss '{"enabled":true,"value":"0x2121","verify":{"user_htstream":"0x2121"}}'

for bad in 2121 3 0x 0x0 0x00000 0x10000 0xZZ1; do
    fresh; snap; run 0 nss "$bad"
    check "B4 nss '$bad' refused, JSON unchanged" refused
done
fresh; snap; run 0 nss 2 3
check "B5 nss with two values refused" refused
fresh; snap; run 0 nss --force
check "B5 nss --force without a value refused" refused
fresh; snap; run 2 nss 2
check "B6 eth0 is not a radio interface (nss)" refused
fresh i.MX8MM; snap; run 2 antcfg 0x303
check "B6 eth0 is not a radio interface (antcfg)" refused

# ===== C. on/off 는 enabled 만 바꾼다 =====
fresh; snap; run 0 nss off
check "C1 nss off rc=0" rc_is 0
check "C1 nss off sets enabled=false" sec_is mlan0 antcfgnss '{"enabled":false,"value":"0x1111","verify":{"user_htstream":"0x1111"}}'
check "C1 nss off changes only enabled" same_except '.mlan0.antcfgnss.enabled'
fresh; edit '.mlan0.antcfgnss.enabled = false'
snap; run 0 nss on
check "C2 nss on rc=0" rc_is 0
check "C2 nss on sets enabled=true" sec_is mlan0 antcfgnss '{"enabled":true,"value":"0x1111","verify":{"user_htstream":"0x1111"}}'
check "C2 nss on changes only enabled" same_except '.mlan0.antcfgnss.enabled'

fresh; edit '.mlan0.antcfgnss.enabled = false | .mlan0.antcfgnss.value = ""'
snap; run 0 nss on
check "C3 nss on refuses an empty stored value" refused

fresh i.MX8MM; edit '.mlan0.antcfg.tx = "0x303"'
snap; run 0 antcfg on
check "C4 antcfg on rc=0 (imx8)" rc_is 0
check "C4 antcfg on changes only enabled" same_except '.mlan0.antcfg.enabled'
check "C4 antcfg on sets enabled=true" sec_is mlan0 antcfg '{"enabled":true,"tx":"0x303","rx":""}'
fresh i.MX8MM; edit '.mlan0.antcfg.tx = "0x303" | .mlan0.antcfg.enabled = true'
snap; run 0 antcfg off
check "C5 antcfg off rc=0" rc_is 0
check "C5 antcfg off changes only enabled" same_except '.mlan0.antcfg.enabled'
check "C5 antcfg off sets enabled=false" sec_is mlan0 antcfg '{"enabled":false,"tx":"0x303","rx":""}'

fresh; edit '.mlan0.antcfg.tx = ""'
snap; run 0 antcfg on --force
check "C6 antcfg on refuses an empty tx even with --force" refused

# ===== D. mcs_tier ht 7 + Tx 니블 2 이상이면 경고 =====
fresh; edit '.mlan0.mcs_tier.ht = "7"'; run 0 nss 2
check "D1 ht 7 + nss 2 warns" has_line "$W_HT7_MLAN0"
fresh; edit '.mlan0.mcs_tier.ht = "7"'; run 0 nss 0x1121
check "D2 ht 7 + 2.4G Tx nibble 2 warns" has_line "$W_HT7_MLAN0"
fresh; edit '.mlan0.mcs_tier.ht = "7"'; run 0 nss 0x1211
check "D3 nss 0x1211 rc=0" rc_is 0
check "D3 ht 7 + only a 5G Rx nibble 2 does not warn" lacks_text "Warning: mcs_tier.ht=7"
fresh; edit '.mlan0.mcs_tier.ht = "7"'; run 0 nss 1
check "D4 nss 1 rc=0" rc_is 0
check "D4 ht 7 + nss 1 does not warn" lacks_text "Warning: mcs_tier.ht=7"
fresh; run 0 nss 2
check "D5 nss 2 rc=0" rc_is 0
check "D5 ht 15 + nss 2 does not warn" lacks_text "Warning: mcs_tier.ht=7"
fresh; edit '.mlan0.mcs_tier.ht = "7" | .mlan0.mcs_tier.enabled = false'; run 0 nss 2
check "D6 nss 2 rc=0" rc_is 0
check "D6 disabled mcs_tier does not warn" lacks_text "Warning: mcs_tier.ht=7"
# wifi_init 이 거부하는 mcs_tier(vht 10)는 적용되지 않으므로 ht 티어로 쓰지 않는다
fresh; edit '.mlan0.mcs_tier.ht = "7" | .mlan0.mcs_tier.vht = "10"'; run 0 nss 2
check "D8 nss 2 rc=0" rc_is 0
check "D8 invalid mcs_tier gives no ht 7 warning" lacks_text "Warning: mcs_tier.ht=7"
snap; run 0 nss
check "D9 invalid mcs_tier is not used as the ht tier" has_line "mcs_tier (JSON): enabled=true ht=7 -> ht tier not applied"
check "D9 cap ignores the invalid tier" has_line "  configured 0x2222 (next wifi_init start): 5G 2, 2.4G 2"
fresh
edit '.mlan0.mcs_tier.ht = "7" | .mlan0.antcfgnss.enabled = false | .mlan0.antcfgnss.value = "0x2222" | .mlan0.antcfgnss.verify.user_htstream = "0x2222"'
run 0 nss on
check "D7 nss on with a Tx2 value under ht 7 warns" has_line "$W_HT7_MLAN0"

# ===== E. imx93 부팅 게이트 대상 키: --force 없이는 거부, off 는 항상 허용 =====
fresh; snap; run 0 antcfg 0x303
check "E1 imx93 refuses enabling mlan0.antcfg without --force" refused
fresh; snap; run 0 antcfg 0x303 --force
check "E2 --force writes mlan0.antcfg" sec_is mlan0 antcfg '{"enabled":true,"tx":"0x303","rx":""}'
check "E2 --force reports the gate log and marker" has_line "$(w_gate mlan0.antcfg)"
check "E2 --force reports the scan wedge risk" has_line "$W_WEDGE"
fresh; edit '.mlan0.antcfg.tx = "0x303"'; snap; run 0 antcfg on
check "E3 imx93 refuses antcfg on for mlan0 without --force" refused
fresh; snap; run 1 antcfg 3 3
check "E4 imx93 refuses enabling mlan1.antcfg without --force" refused
fresh; snap; run 1 antcfg 3 3 --force
check "E5 --force writes mlan1.antcfg (stale empty verify removed)" sec_is mlan1 antcfg '{"enabled":true,"tx":"3","rx":"3"}'
check "E5 --force reports the gate log and marker" has_line "$(w_gate mlan1.antcfg)"
fresh; snap; run 1 nss 0x1111
check "E6 imx93 refuses enabling mlan1.antcfgnss without --force" refused
fresh; snap; run 1 nss 0x1111 --force
check "E7 --force writes mlan1.antcfgnss" \
    sec_is mlan1 antcfgnss '{"enabled":true,"value":"0x1111","verify":{"user_htstream":"0x1111"}}'
check "E7 --force reports the gate log and marker" has_line "$(w_gate mlan1.antcfgnss)"
fresh; edit '.mlan1.antcfgnss.value = "0x1111"'; snap; run 1 nss on
check "E8 imx93 refuses nss on for mlan1 without --force" refused
fresh; edit '.mlan1.antcfg.enabled = true | .mlan1.antcfg.tx = "3" | del(.mlan1.antcfg.verify)'
snap; run 1 antcfg off
check "E9 antcfg off on a gate key is allowed" rc_is 0
check "E9 antcfg off sets enabled=false" sec_is mlan1 antcfg '{"enabled":false,"tx":"3","rx":""}'
fresh; edit '.mlan1.antcfgnss.enabled = true | .mlan1.antcfgnss.value = "0x1111"'
snap; run 1 nss off
check "E10 nss off on a gate key is allowed" rc_is 0
check "E10 nss off sets enabled=false" sec_is mlan1 antcfgnss '{"enabled":false,"value":"0x1111"}'
check "E10 nss off changes only enabled" same_except '.mlan1.antcfgnss.enabled'
fresh; edit '.mlan1.antcfg.enabled = true | .mlan1.antcfg.tx = "3" | del(.mlan1.antcfg.verify)'
snap; run 0 nss 2
check "E11 an existing violation in another section does not block mlan0 nss" rc_is 0
check "E11 mlan0 nss written" \
    sec_is mlan0 antcfgnss '{"enabled":true,"value":"0x2222","verify":{"user_htstream":"0x2222"}}'
check "E11 no --force warning for a non-gate key" lacks_text "Warning: --force"

# ===== F. imx8 은 antcfgnss ABI 가 없다, 판정 불가 보드는 켜는 SET 을 거부 =====
fresh i.MX8MM; snap; run 0 nss 2
check "F1 imx8 refuses nss 2" refused
fresh i.MX8MM; snap; run 0 nss on
check "F2 imx8 refuses nss on" refused
fresh i.MX8MM; snap; run 0 nss off
check "F3 imx8 allows nss off" rc_is 0
check "F3 nss off sets enabled=false" \
    sec_is mlan0 antcfgnss '{"enabled":false,"value":"0x1111","verify":{"user_htstream":"0x1111"}}'
check "F3 nss off changes only enabled" same_except '.mlan0.antcfgnss.enabled'
fresh i.MX8MM; snap; run 0 antcfg 0x303
check "F4 imx8 antcfg needs no --force (no imx93 gate)" sec_is mlan0 antcfg '{"enabled":true,"tx":"0x303","rx":""}'
check "F4 no --force warning on imx8" lacks_text "Warning: --force"
fresh i.MX6; snap; run 0 antcfg 0x303 --force
check "F5 unknown SoC refuses antcfg" refused
fresh i.MX6; snap; run 0 nss 2
check "F6 unknown SoC refuses nss" refused
fresh; rm -f "$WIFI_SOC_ID_PATH"; snap; run 0 nss 2
check "F7 unreadable SoC id refuses nss" refused

# ===== G. antcfg 값 검사, verify 처리, 적용 순서 안내 =====
for bad in "0" "010" "0x10000" "0x" "abc" "3 0" "3 010" "0x303 0x0"; do
    fresh i.MX8MM; snap
    # shellcheck disable=SC2086 # "tx rx" 쌍을 인자 두 개로 나눈다
    run 0 antcfg $bad
    check "G1 antcfg '$bad' refused" refused
done
fresh i.MX8MM; snap; run 0 antcfg 1 2 3
check "G2 antcfg with three values refused" refused

fresh i.MX8MM
edit '.mlan0.antcfg = {"enabled":false,"tx":"3","rx":"3","verify":{"physical_tx":"3","physical_rx":"3","user_htstream":"0x2222"}}'
snap; run 0 antcfg 3 3
check "G3 same tx/rx keeps verify" \
    sec_is mlan0 antcfg '{"enabled":true,"tx":"3","rx":"3","verify":{"physical_tx":"3","physical_rx":"3","user_htstream":"0x2222"}}'
check "G3 no removal note when verify is kept" lacks_text "antcfg.verify removed"
snap; run 0 antcfg 1 1
check "G4 changed tx/rx drops verify (log-only)" sec_is mlan0 antcfg '{"enabled":true,"tx":"1","rx":"1"}'
check "G4 says verify was removed" \
    has_line "Note: mlan0.antcfg.verify removed because tx/rx changed (log-only: SET + GET are logged, not compared)"

fresh i.MX8MM; snap; run 0 antcfg 0x303
check "G5 order note when antcfgnss is also enabled" \
    has_line "Note: mlan0.antcfgnss is also enabled; wifi_init applies antcfg first and then antcfgnss, so user_htstream ends at the antcfgnss value 0x1111"
check "G5 antcfg SET does not call mlanutl" no_mlanutl
check "G5 written through safe_install_sync (install stage, 0644)" installed_via_stage
fresh i.MX8MM; edit '.mlan0.antcfgnss.enabled = false'; run 0 antcfg 0x303
check "G6 antcfg 0x303 rc=0" rc_is 0
check "G6 no order note when antcfgnss is disabled" lacks_text "Note: mlan0.antcfgnss"

# ===== H. CHANGE_GUARD: 조회는 면제, 변경은 잠금 대상 =====
fresh
exec 7>"$WIFI_RUN_DIR/mlan0.change.lock"
flock -x 7
snap; run 0 nss
check "H1 nss GET runs while a change holds the lock" rc_is 0
snap; run 0 nss get
check "H2 nss get runs while a change holds the lock" rc_is 0
snap; run 0 antcfg
check "H3 antcfg GET runs while a change holds the lock" rc_is 0
snap; run 0 nss 2
check "H4 nss SET is guarded (rc 6)" rc_is 6
check "H4 guarded SET leaves JSON unchanged" unchanged
snap; run 0 antcfg off
check "H5 antcfg off is guarded (rc 6)" rc_is 6
exec 7>&-

# ===== I. 패키지 설치·업그레이드(postinst migrate_product_antcfg)를 거쳐도 CLI 값이 남는다 =====
# postinst:965 가 부르는 함수 그대로 활성 설정을 다시 쓴다.
migrate() {
    local t rc
    t=$(mktemp "$TD/mig.XXXXXX")
    bash -c '. "$1" && wifi_fw_migrate_product_antcfg_json "$2" imx93' _ \
        "$SCRIPT_DIR/wifi_fw_config_lib.sh" "$WIFI_INIT_CONF_JSON" > "$t" \
        && cat "$t" > "$WIFI_INIT_CONF_JSON"
    rc=$?
    rm -f "$t"
    return "$rc"
}
fresh; run 0 nss 2
check "I1 nss 2 rc=0" rc_is 0
check "I1 upgrade migration runs" migrate
check "I1 nss value survives the upgrade migration" \
    sec_is mlan0 antcfgnss '{"enabled":true,"value":"0x2222","verify":{"user_htstream":"0x2222"}}'
fresh; run 0 nss off
check "I2 nss off rc=0" rc_is 0
check "I2 upgrade migration runs" migrate
check "I2 nss off survives the upgrade migration" \
    sec_is mlan0 antcfgnss '{"enabled":false,"value":"0x1111","verify":{"user_htstream":"0x1111"}}'

# ===== J. radio JSON 잠금: OPC·라디오 CLI 의 JSON 쓰기와 겹치지 않는다 =====
fresh
exec 5>"$WIFI_RUN_DIR/radio-json.lock"
flock -x 5
snap; run 0 nss 2
check "J1 nss SET is refused while the radio JSON lock is held" refused
snap; run 0 antcfg off
check "J2 antcfg off is refused while the radio JSON lock is held" refused
snap; run 0 nss
check "J3 nss GET does not need the radio JSON lock" rc_is 0
exec 5>&-
snap; run 0 nss 2
check "J4 nss SET works again once the lock is released" rc_is 0

# ===== K. 꺼진 인터페이스는 wifi_init 이 라디오 기본값을 건너뛴다 — 적용되지 않는다고 알린다 =====
N_MLAN1_DISABLED="Note: mlan1 is disabled (.mlan1.enabled is not true); wifi_init skips its radio defaults, so this setting is applied only after the interface is enabled"
fresh; run 1 nss 0x1111 --force
check "K1 nss on the disabled mlan1 rc=0" rc_is 0
check "K1 says the disabled interface does not apply it" has_line "$N_MLAN1_DISABLED"
fresh; run 1 nss
check "K2 GET of the disabled mlan1 says so too" has_line "$N_MLAN1_DISABLED"
fresh; run 0 nss 2
check "K3 nss 2 rc=0" rc_is 0
check "K3 no disabled note for the enabled mlan0" lacks_text "Note: mlan0 is disabled"
# 설정이 유효하면 wifi_init 은 키가 없는 인터페이스를 꺼진 것으로 본다(IFACE_ENABLED_DEFAULT=false)
fresh; edit 'del(.mlan1.enabled)'; run 1 nss
check "K4 a missing .mlan1.enabled counts as disabled, as in wifi_init" has_line "$N_MLAN1_DISABLED"

echo "RESULT: PASS=$PASS FAIL=$FAIL"
[ "$FAIL" = 0 ]
