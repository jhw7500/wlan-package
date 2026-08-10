#!/bin/bash
# wifi_conf_preserve.sh standalone tests (hardware/root 불필요)
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
SCRIPT="$SCRIPT_DIR/wifi_conf_preserve.sh"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

ACTIVE="$WORK/wifi_init_conf.json"
SNAPSHOT="$WORK/preserve.json"
FW="$WORK/firmware"
LOG="$WORK/logger.log"
FAKE_LOGGER="$WORK/logger.sh"
PASS=0
FAIL=0

mkdir -p "$FW/cts"
cat > "$FAKE_LOGGER" <<'EOF'
#!/bin/bash
printf '%s\n' "$*" >> "$LOG_CAPTURE"
EOF
chmod +x "$FAKE_LOGGER"

run_preserve() {
    WIFI_INIT_CONF_JSON="$ACTIVE" \
    WIFI_FIRMWARE_ROOT="$FW" \
    WIFI_PRESERVE_LOGGER="$FAKE_LOGGER" \
    LOG_CAPTURE="$LOG" \
        "$SCRIPT" "$@"
}

expect_eq() {
    local name="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then
        printf 'PASS: %s\n' "$name"
        PASS=$((PASS + 1))
    else
        printf 'FAIL: %s (expected=%s actual=%s)\n' "$name" "$expected" "$actual" >&2
        FAIL=$((FAIL + 1))
    fi
}

value_of() {
    jq -r --arg s "$1" --arg k "$2" '.[$s][$k] // "<absent>"' "$ACTIVE"
}

mac_of() {
    jq -r --arg i "$1" --arg k "$2" '.mac[$i][$k] // "<absent>"' "$ACTIVE"
}

# 템플릿(.deb의 /opt/wlan/config/wifi_init_conf.json)과 같은 모양의 최소 픽스처.
template_json() {
    jq -n '{
        global: {
            BOARD_TYPE: "imx93",
            MOD_PARA: "cts/wifi_mod_para.conf",
            CAL_DATA_CFG: "cts/WlanCalData_ext_RD.conf",
            TXPWRLIMIT_PATH: "/lib/firmware/cts/txpwrlimit_cfg_9098.conf",
            STANDARD: ""
        },
        mac: {
            mlan0: {base: "", target: ""},
            mlan1: {base: "", target: ""},
            eth0: {base: ""}
        },
        mlan0: {CAL_DATA_CFG: "", TXPWRLIMIT_PATH: "", enabled: true},
        mlan1: {CAL_DATA_CFG: "", TXPWRLIMIT_PATH: "", enabled: false}
    }'
}

# factory_reset.sh의 실제 순서를 그대로 흉내낸다:
#   save -> 템플릿 덮어쓰기 -> wifi_board_config.sh(.global.MOD_PARA 상수 재기록) -> apply
reset_cycle() {
    run_preserve save "$SNAPSHOT"
    template_json > "$ACTIVE"
    jq '.global.BOARD_TYPE = "imx8mm" | .global.MOD_PARA = "cts/wifi_mod_para.conf"' \
        "$ACTIVE" > "$ACTIVE.tmp" && mv "$ACTIVE.tmp" "$ACTIVE"
    run_preserve apply "$SNAPSHOT"
}

: > "$LOG"
touch "$FW/cts/wifi_mod_para_unit.conf" \
      "$FW/cts/WlanCalData_unit.conf" \
      "$FW/cts/WlanCalData_mlan0.conf" \
      "$FW/cts/txpwrlimit_unit.conf"

# --- 정상 경로: 생산 단계 값이 초기화를 살아남는다 ---
jq -n --arg fw "$FW" '{
    global: {
        BOARD_TYPE: "imx93",
        MOD_PARA: "cts/wifi_mod_para_unit.conf",
        CAL_DATA_CFG: "cts/WlanCalData_unit.conf",
        TXPWRLIMIT_PATH: ($fw + "/cts/txpwrlimit_unit.conf"),
        STANDARD: "ac"
    },
    mac: {
        mlan0: {base: "00:11:22:33:44:55", target: "02:aa:bb:cc:dd:ee"},
        mlan1: {base: "01:11:22:33:44:55", target: ""},
        eth0: {base: "00:11:22:33:44:66"}
    },
    mlan0: {CAL_DATA_CFG: "cts/WlanCalData_mlan0.conf", TXPWRLIMIT_PATH: "none", enabled: false},
    mlan1: {CAL_DATA_CFG: "", TXPWRLIMIT_PATH: "cts/missing.conf", enabled: true}
}' > "$ACTIVE"

reset_cycle

expect_eq "global.MOD_PARA survives board config" "cts/wifi_mod_para_unit.conf" "$(value_of global MOD_PARA)"
expect_eq "global.CAL_DATA_CFG preserved" "cts/WlanCalData_unit.conf" "$(value_of global CAL_DATA_CFG)"
expect_eq "global.TXPWRLIMIT_PATH preserved (absolute)" "$FW/cts/txpwrlimit_unit.conf" "$(value_of global TXPWRLIMIT_PATH)"
expect_eq "mlan0.CAL_DATA_CFG preserved" "cts/WlanCalData_mlan0.conf" "$(value_of mlan0 CAL_DATA_CFG)"
expect_eq "mlan0.TXPWRLIMIT_PATH none preserved" "none" "$(value_of mlan0 TXPWRLIMIT_PATH)"
expect_eq "mlan1.CAL_DATA_CFG empty preserved" "" "$(value_of mlan1 CAL_DATA_CFG)"
expect_eq "mlan1.TXPWRLIMIT_PATH dangling falls back to template" "" "$(value_of mlan1 TXPWRLIMIT_PATH)"
expect_eq "dangling path logged" "1" "$(grep -c 'path missing on disk' "$LOG")"

# --- .mac.<iface>.base 보존 / target은 초기화 ---
expect_eq "mac.mlan0.base preserved" "00:11:22:33:44:55" "$(mac_of mlan0 base)"
expect_eq "mac.eth0.base preserved" "00:11:22:33:44:66" "$(mac_of eth0 base)"
expect_eq "mac.mlan1.base multicast falls back to template" "" "$(mac_of mlan1 base)"
expect_eq "unassignable MAC logged" "1" "$(grep -c 'not an assignable unicast MAC' "$LOG")"
expect_eq "mac.mlan0.target is reset" "" "$(mac_of mlan0 target)"
expect_eq "mac.eth0 has no target key" "<absent>" "$(mac_of eth0 target)"

# 보존 대상이 아닌 키는 초기화된다 — 이게 공장 초기화의 본래 동작이다.
expect_eq "non-preserved key is reset" "" "$(value_of global STANDARD)"
expect_eq "non-preserved key is reset (mlan0.enabled)" "true" "$(value_of mlan0 enabled)"
expect_eq "board config result kept" "imx8mm" "$(value_of global BOARD_TYPE)"

# --- MOD_PARA는 빈값/none을 "사용 안 함"으로 받아주지 않는다 ---
# 드라이버가 항상 필요로 하는 값이라 빈값을 보존하면 moal_args="mod_para=" 로 insmod된다.
# 바로 앞 wifi_board_config.sh가 상수로 고쳐놓은 값을 되돌리는 셈이라 거부해야 한다.
for _bad in "" none; do
    : > "$LOG"
    jq -n --arg m "$_bad" '{global: {MOD_PARA: $m, CAL_DATA_CFG: "", TXPWRLIMIT_PATH: ""},
                            mac: {}, mlan0: {}, mlan1: {}}' > "$ACTIVE"
    run_preserve save "$SNAPSHOT"
    template_json > "$ACTIVE"
    jq '.global.MOD_PARA = "cts/wifi_mod_para.conf"' "$ACTIVE" > "$ACTIVE.t" && mv "$ACTIVE.t" "$ACTIVE"
    run_preserve apply "$SNAPSHOT"
    expect_eq "MOD_PARA='$_bad' rejected; board config value kept" \
        "cts/wifi_mod_para.conf" "$(value_of global MOD_PARA)"
    expect_eq "MOD_PARA='$_bad' rejection logged" "1" \
        "$(grep -c 'empty/none not allowed for this key' "$LOG")"
done

# --- 빈값 예외는 나머지 키에서는 그대로 유효하다 (회귀 방지) ---
: > "$LOG"
jq -n '{global: {MOD_PARA: "cts/wifi_mod_para_unit.conf", CAL_DATA_CFG: "", TXPWRLIMIT_PATH: "none"},
        mac: {}, mlan0: {}, mlan1: {}}' > "$ACTIVE"
run_preserve save "$SNAPSHOT"
template_json > "$ACTIVE"
run_preserve apply "$SNAPSHOT"
expect_eq "global.CAL_DATA_CFG empty still preserved" "" "$(value_of global CAL_DATA_CFG)"
expect_eq "global.TXPWRLIMIT_PATH none still preserved" "none" "$(value_of global TXPWRLIMIT_PATH)"

# --- 키가 없던 설정: 템플릿 값이 그대로 남는다 ---
: > "$LOG"
jq -n '{global: {BOARD_TYPE: "imx93"}, mlan0: {}, mlan1: {}}' > "$ACTIVE"
reset_cycle
expect_eq "absent key keeps template value" "cts/wifi_mod_para.conf" "$(value_of global MOD_PARA)"
expect_eq "absent key keeps template cal" "cts/WlanCalData_ext_RD.conf" "$(value_of global CAL_DATA_CFG)"

# --- 손상된 active: save가 실패하고 호출자가 템플릿으로 진행한다 ---
: > "$LOG"
printf '{"global":' > "$ACTIVE"
if run_preserve save "$SNAPSHOT" 2>/dev/null; then
    expect_eq "invalid active fails save" "nonzero" "zero"
else
    expect_eq "invalid active fails save" "nonzero" "nonzero"
fi
expect_eq "invalid active logged" "1" "$(grep -c 'cannot snapshot preserved keys' "$LOG")"

# --- 손상된 snapshot: apply가 거부하고 템플릿 값을 유지한다 ---
: > "$LOG"
template_json > "$ACTIVE"
printf 'broken\n' > "$SNAPSHOT"
if run_preserve apply "$SNAPSHOT" 2>/dev/null; then
    expect_eq "invalid snapshot fails apply" "nonzero" "zero"
else
    expect_eq "invalid snapshot fails apply" "nonzero" "nonzero"
fi
expect_eq "invalid snapshot keeps template" "cts/wifi_mod_para.conf" "$(value_of global MOD_PARA)"

# --- 보존 목록 밖의 키는 snapshot에 있어도 되쓰지 않는다 ---
: > "$LOG"
template_json > "$ACTIVE"
jq -n '{global: {STANDARD: "ax"}, mac: {mlan0: {target: "02:00:00:00:00:01"}}, wbridge: {enabled: true}}' > "$SNAPSHOT"
run_preserve apply "$SNAPSHOT"
expect_eq "off-list key not restored" "" "$(value_of global STANDARD)"
expect_eq "off-list mac.target not restored" "" "$(mac_of mlan0 target)"
expect_eq "off-list section not created" "<absent>" "$(jq -r '.wbridge // "<absent>"' "$ACTIVE")"

# --- MAC 판정 라이브러리가 없으면 MAC만 빠지고 경로 키는 그대로 보존된다 ---
: > "$LOG"
jq -n --arg fw "$FW" '{
    global: {MOD_PARA: "cts/wifi_mod_para_unit.conf"},
    mac: {mlan0: {base: "00:11:22:33:44:55"}}
}' > "$ACTIVE"
run_preserve save "$SNAPSHOT"
template_json > "$ACTIVE"
WIFI_MAC_LINK_LIB="$WORK/no-such-lib.sh" run_preserve apply "$SNAPSHOT"
expect_eq "path key preserved without mac lib" "cts/wifi_mod_para_unit.conf" "$(value_of global MOD_PARA)"
expect_eq "mac key skipped without mac lib" "" "$(mac_of mlan0 base)"
expect_eq "missing mac lib logged" "1" "$(grep -c 'cannot validate preserved MAC' "$LOG")"

# --- keys 서브커맨드는 문서/테스트가 참조하는 단일 출처다 ---
expect_eq "keys lists 10 preserved paths" "10" "$(run_preserve keys | wc -l)"
expect_eq "keys are listed in canonical order" \
    "global.MOD_PARA global.CAL_DATA_CFG global.TXPWRLIMIT_PATH mlan0.CAL_DATA_CFG mlan0.TXPWRLIMIT_PATH mlan1.CAL_DATA_CFG mlan1.TXPWRLIMIT_PATH mac.mlan0.base mac.mlan1.base mac.eth0.base" \
    "$(run_preserve keys | tr '\n' ' ' | sed 's/ $//')"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
