#!/bin/bash
set -uo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
LIB="$SCRIPT_DIR/wifi_fw_config_lib.sh"
WIFI_INIT="$SCRIPT_DIR/wifi_init.sh"
WIFI_CLI="$SCRIPT_DIR/wifi.sh"
WIFI_EVENT="$SCRIPT_DIR/wifi_event.sh"
POSTINST="$SCRIPT_DIR/../../../DEBIAN/postinst"
WIFI_INIT_UNIT="$SCRIPT_DIR/../../../etc/systemd/system/wifi_init.service"
EMERGENCY_UNIT="$SCRIPT_DIR/../../../etc/systemd/system/wlan_emergency_reboot.service"
TEMPLATE="$SCRIPT_DIR/../../../opt/wlan/config/wifi_init_conf.json"
PASS=0
FAIL=0
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

pass() { PASS=$((PASS + 1)); printf 'PASS: %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf 'FAIL: %s\n' "$1" >&2; }
expect_rc() {
    local name="$1" expected="$2"
    shift 2
    "$@" >/dev/null 2>&1
    local actual=$?
    [ "$actual" -eq "$expected" ] && pass "$name" || fail "$name (expected rc=$expected actual=$actual)"
}
expect_eq() {
    local name="$1" expected="$2" actual="$3"
    [ "$expected" = "$actual" ] && pass "$name" || fail "$name (expected=[$expected] actual=[$actual])"
}

if [ ! -r "$LIB" ]; then
    fail "FW config library exists"
    printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
    exit 1
fi

LOG="$WORK/log"
STATE="$WORK/state"
FAKE="$WORK/mlanutl"
: > "$LOG"
mkdir -p "$STATE"

cat > "$FAKE" <<'EOF'
#!/bin/bash
iface="$1" cmd="$2"; shift 2
case "$cmd" in
    rate_adapt_cfg)
        if [ $# -gt 0 ]; then printf '%s %s %s %s\n' "$@" > "$STATE/$iface.rate"; exit 0; fi
        read -r mode low high interval < "$STATE/$iface.rate"
        if [ "$mode" = 1 ]; then
            printf 'Rate Adapt Cfg:\n SR RateAdapt Enabled\n    Low   : %s\n    High  : %s\nEval Timer interval  : %s  i.e. %sms\n' \
                "$low" "$high" "$interval" "$((interval * 10))"
        else
            printf 'Rate Adapt Cfg:\n Legacy RateAdapt Enabled\n'
        fi
        ;;
    antcfg)
        if [ $# -gt 0 ]; then printf '%s\n' "$*" > "$STATE/$iface.ant"; exit 0; fi
        [ -e "$STATE/$iface.ant" ] || { printf 'Mode of Tx/Rx path is : 0x3\n'; exit 0; }
        read -r m n < "$STATE/$iface.ant"
        printf 'Mode of Tx path is : %s\n' "$m"
        printf 'Mode of Rx path is : %s\n' "${n:-$m}"
        ;;
    mcstiercfg)
        if [ $# -gt 0 ]; then
            count_file="$STATE/$iface.mcs_set_count"
            count=$(cat "$count_file" 2>/dev/null || echo 0); count=$((count + 1)); echo "$count" > "$count_file"
            # 첫 SET은 FW success처럼 0을 반환하지만 실제 map은 바꾸지 않는 냉부팅 race 재현.
            if [ "$count" -ge "${MCS_ACCEPT_ON_ATTEMPT:-1}" ]; then
                printf '%s\n' "$*" > "$STATE/$iface.mcs"
            fi
            [ "${MCS_CONNECTED_SET_RECOVERS:-0}" = 1 ] && rm -f "$STATE/$iface.he_default"
            exit 0
        fi
        args=$(cat "$STATE/$iface.mcs" 2>/dev/null || echo 'ht 15 vht 9 he both 11')
        ht=$(printf '%s\n' "$args" | sed -n 's/.*ht \([0-9]*\).*/\1/p')
        vht=$(printf '%s\n' "$args" | sed -n 's/.*vht \([0-9]*\).*/\1/p')
        he=$(printf '%s\n' "$args" | sed -n 's/.*he both \([0-9]*\).*/\1/p')
        map() { case "$1" in 7) echo FFF0;; 8|9) [ "$1" = 8 ] && echo FFF5 || echo FFFA;; 11) echo FFFA;; esac; }
        vm=$(map "$vht"); hm=$(map "${he:-11}")
        [ "${MCS_HE_UNAVAILABLE:-0}" = 1 ] && hm=0000
        [ "${MCS_HE_WRONG:-0}" = 1 ] && hm=FFFA
        [ -e "$STATE/$iface.he_default" ] && hm=FFFA
        printf 'MCS Tier Capability Configuration (association)\n'
        printf '  HT  (11n)  : 1x1 (MCS 0~%s)\n' "$ht"
        printf '  VHT Tx: 0x%s\n  VHT Rx: 0x%s\n' "$vm" "$vm"
        printf '  HE Tx: 0x%s\n  HE Rx: 0x%s\n' "$hm" "$hm"
        ;;
    11axcfg)
        args=$(cat "$STATE/$iface.mcs" 2>/dev/null || echo 'he both 11')
        he=$(printf '%s\n' "$args" | sed -n 's/.*he both \([0-9]*\).*/\1/p')
        case "${he:-11}" in 7) bytes='f0 ff f0 ff';; 9|11) bytes='fa ff fa ff';; esac
        [ "${MCS_HE_UNAVAILABLE:-0}" = 1 ] && bytes='00 00 00 00'
        [ "${MCS_HE_WRONG:-0}" = 1 ] && bytes='fa ff fa ff'
        [ -e "$STATE/$iface.he_default" ] && bytes='fa ff fa ff'
        printf '11axcfg: len=55\n02 ff 00 19 00 23 03 08 00 02 00 00 04 30 72 c9 fd 01 a0 0a 00 3d 00 %s a1\n' "$bytes"
        ;;
esac
EOF
chmod +x "$FAKE"

export WIFI_FW_LOGGER="$WORK/logger"
cat > "$WIFI_FW_LOGGER" <<'EOF'
#!/bin/bash
printf '%s\n' "$*" >> "$LOG_CAPTURE"
EOF
chmod +x "$WIFI_FW_LOGGER"
export LOG_CAPTURE="$LOG" STATE
export WIFI_MLANUTL="$FAKE"
export WIFI_WPA_CLI="$WORK/wpa_cli"
cat > "$WIFI_WPA_CLI" <<'EOF'
#!/bin/bash
printf '%s\n' "$*" >> "$STATE/wpa_calls"
exit 0
EOF
chmod +x "$WIFI_WPA_CLI"
export WIFI_MCS_VERIFY_DELAY_SEC=0
export WIFI_MCS_VERIFY_ATTEMPTS=3
export WIFI_MCS_PENDING_DIR="$WORK/pending"

# shellcheck source=./wifi_fw_config_lib.sh
. "$LIB"

CONF="$WORK/conf.json"
cat > "$CONF" <<'EOF'
{
  "mlan0":{"STANDARD":"ax","rate_adapt":{"mode":1,"low_thresh":70,"high_thresh":90,"interval_ms":100},"mcs_tier":{"enabled":true,"ht":"7","vht":"7","he":"both 7"}},
  "mlan1":{"STANDARD":"ac","rate_adapt":{"mode":1,"low_thresh":255,"high_thresh":255,"interval_ms":200},"mcs_tier":{"enabled":true,"ht":"7","vht":"7","he":""}}
}
EOF

LEGACY_MCS="$WORK/legacy-mcs.json"
NORMALIZED_MCS="$WORK/normalized-mcs.json"
jq '
  .mlan0.mcs_tier = {enabled:true,ht:7,vht:9,he:11}
  | .mlan1.mcs_tier = {enabled:true,ht:15,vht:8,he:"both 7"}
' "$CONF" > "$LEGACY_MCS"
if declare -F wifi_fw_normalize_legacy_mcs_json >/dev/null 2>&1 \
   && wifi_fw_normalize_legacy_mcs_json "$LEGACY_MCS" > "$NORMALIZED_MCS"; then
    pass "legacy numeric MCS normalization succeeds"
else
    : > "$NORMALIZED_MCS"
    fail "legacy numeric MCS normalization succeeds"
fi
expect_eq "legacy mlan0 HT becomes canonical string" string \
    "$(jq -r '.mlan0.mcs_tier.ht | type' "$NORMALIZED_MCS" 2>/dev/null)"
expect_eq "legacy mlan0 VHT value preserved" 9 \
    "$(jq -r '.mlan0.mcs_tier.vht' "$NORMALIZED_MCS" 2>/dev/null)"
expect_eq "legacy mlan0 HE becomes bidirectional string" 'both 11' \
    "$(jq -r '.mlan0.mcs_tier.he' "$NORMALIZED_MCS" 2>/dev/null)"
expect_eq "legacy mlan1 HT becomes canonical string" string \
    "$(jq -r '.mlan1.mcs_tier.ht | type' "$NORMALIZED_MCS" 2>/dev/null)"
expect_eq "legacy mlan1 VHT value preserved" 8 \
    "$(jq -r '.mlan1.mcs_tier.vht' "$NORMALIZED_MCS" 2>/dev/null)"
expect_eq "legacy mlan1 unsupported HE is cleared" '' \
    "$(jq -r '.mlan1.mcs_tier.he' "$NORMALIZED_MCS" 2>/dev/null)"
jq '.mlan1.mcs_tier.he = 9' "$LEGACY_MCS" > "$WORK/legacy-mcs-numeric-he.json"
wifi_fw_normalize_legacy_mcs_json "$WORK/legacy-mcs-numeric-he.json" \
    > "$WORK/normalized-mcs-numeric-he.json"
expect_eq "legacy mlan1 numeric HE is cleared" '' \
    "$(jq -r '.mlan1.mcs_tier.he' "$WORK/normalized-mcs-numeric-he.json" 2>/dev/null)"
expect_rc "normalized AX MCS config is valid" 0 wifi_fw_validate_mcs_config "$NORMALIZED_MCS" mlan0
expect_rc "normalized AC MCS config is valid" 0 wifi_fw_validate_mcs_config "$NORMALIZED_MCS" mlan1
wifi_fw_normalize_legacy_mcs_json "$NORMALIZED_MCS" > "$WORK/normalized-mcs-twice.json"
expect_eq "MCS normalization is idempotent" \
    "$(jq -S -c . "$NORMALIZED_MCS")" \
    "$(jq -S -c . "$WORK/normalized-mcs-twice.json")"
jq '.mlan0.mcs_tier.ht = 6' "$LEGACY_MCS" > "$WORK/invalid-legacy-mcs.json"
wifi_fw_normalize_legacy_mcs_json "$WORK/invalid-legacy-mcs.json" > "$WORK/invalid-normalized-mcs.json"
expect_eq "unknown legacy MCS value is not coerced" number \
    "$(jq -r '.mlan0.mcs_tier.ht | type' "$WORK/invalid-normalized-mcs.json")"
expect_rc "unknown legacy MCS remains invalid" 1 \
    wifi_fw_validate_mcs_config "$WORK/invalid-normalized-mcs.json" mlan0
grep -q 'normalize_legacy_mcs_tier /usr/local/etc/wifi_init_conf.json || exit 1' "$POSTINST" \
    && pass "postinst migrates legacy numeric MCS after merge" \
    || fail "postinst does not migrate legacy numeric MCS after merge"

expect_rc "static rate config valid" 0 wifi_fw_validate_rate_config "$CONF" mlan0
expect_rc "dynamic rate config valid" 0 wifi_fw_validate_rate_config "$CONF" mlan1
expect_rc "rate apply succeeds" 0 wifi_fw_apply_rate "$CONF" mlan0
expect_eq "rate SET uses configured values" '1 70 90 10' "$(cat "$STATE/mlan0.rate")"

jq '.mlan0.rate_adapt |= del(.high_thresh)' "$CONF" > "$WORK/partial.json"
expect_rc "partial rate section rejected" 1 wifi_fw_validate_rate_config "$WORK/partial.json" mlan0
rm -f "$STATE/mlan0.rate"
expect_rc "partial rate section is skipped without fallback" 0 wifi_fw_apply_rate "$WORK/partial.json" mlan0
expect_eq "partial rate emitted no SET" 'no' "$([ -e "$STATE/mlan0.rate" ] && echo yes || echo no)"

jq '.mlan0.rate_adapt.low_thresh=90 | .mlan0.rate_adapt.high_thresh=70' "$CONF" > "$WORK/bad-order.json"
expect_rc "rate low must be less than high" 1 wifi_fw_validate_rate_config "$WORK/bad-order.json" mlan0
jq '.mlan0.rate_adapt.low_thresh=255 | .mlan0.rate_adapt.high_thresh=90' "$CONF" > "$WORK/half-dynamic.json"
expect_rc "dynamic rate requires both thresholds 255" 1 wifi_fw_validate_rate_config "$WORK/half-dynamic.json" mlan0
jq '.mlan0.rate_adapt.interval_ms=105' "$CONF" > "$WORK/bad-interval.json"
expect_rc "rate interval must be 10ms multiple" 1 wifi_fw_validate_rate_config "$WORK/bad-interval.json" mlan0

# ── rate_adapt.enabled 게이트 ──────────────────────────────────────────────
# 기본 true(키 부재 = 종전 동작). false면 SET 자체를 하지 않고 FW 기본값을 유지한다.
# 값 검증은 enabled 와 독립이어야 한다 — 아니면 꺼둔 iface 에 대해 `wifi <iface> rate`가
# 유효한 값을 거부한다(CLI 가 같은 validate 를 공유하므로).
rm -f "$STATE/mlan0.rate"
expect_rc "absent enabled defaults to on" 0 wifi_fw_apply_rate "$CONF" mlan0
expect_eq "absent enabled emitted SET" '1 70 90 10' "$(cat "$STATE/mlan0.rate" 2>/dev/null)"

rm -f "$STATE/mlan0.rate"
jq '.mlan0.rate_adapt.enabled=true' "$CONF" > "$WORK/rate-on.json"
expect_rc "explicitly enabled rate applies" 0 wifi_fw_apply_rate "$WORK/rate-on.json" mlan0
expect_eq "enabled rate emitted SET" '1 70 90 10' "$(cat "$STATE/mlan0.rate" 2>/dev/null)"

rm -f "$STATE/mlan0.rate"
jq '.mlan0.rate_adapt.enabled=false' "$CONF" > "$WORK/rate-off.json"
expect_rc "disabled rate section applies cleanly" 0 wifi_fw_apply_rate "$WORK/rate-off.json" mlan0
expect_eq "disabled rate emitted no SET" 'no' "$([ -e "$STATE/mlan0.rate" ] && echo yes || echo no)"
expect_rc "disabled rate still validates values" 0 wifi_fw_validate_rate_config "$WORK/rate-off.json" mlan0

rm -f "$STATE/mlan0.rate"
jq '.mlan0.rate_adapt.enabled=0' "$CONF" > "$WORK/rate-zero.json"
expect_rc "non-true enabled treated as off" 0 wifi_fw_apply_rate "$WORK/rate-zero.json" mlan0
expect_eq "non-true enabled emitted no SET" 'no' "$([ -e "$STATE/mlan0.rate" ] && echo yes || echo no)"

expect_eq "template ships mlan0 rate_adapt.enabled" 'true' "$(jq -r '.mlan0.rate_adapt.enabled' "$TEMPLATE")"
expect_eq "template ships mlan1 rate_adapt.enabled" 'true' "$(jq -r '.mlan1.rate_adapt.enabled' "$TEMPLATE")"

# CLI 의 rate 쓰기는 통째 대입이면 동거 키(_comment/enabled)를 지운다 — 병합이어야 한다.
grep -qF '.[$iface].rate_adapt = ((.[$iface].rate_adapt // {}) + {' "$WIFI_CLI" \
    && pass "CLI merges rate_adapt instead of replacing" \
    || fail "CLI replaces rate_adapt object (동거 키 _comment/enabled 유실)"

_rate_merged=$(jq --arg iface mlan0 --argjson mode 0 --argjson low 10 \
        --argjson high 20 --argjson interval 50 '
        .[$iface].rate_adapt = ((.[$iface].rate_adapt // {}) + {
            mode: $mode, low_thresh: $low, high_thresh: $high, interval_ms: $interval
        })' "$TEMPLATE")
expect_eq "rate merge keeps enabled" 'true' \
    "$(printf '%s' "$_rate_merged" | jq -r '.mlan0.rate_adapt.enabled')"
expect_eq "rate merge keeps _comment" 'true' \
    "$(printf '%s' "$_rate_merged" | jq -r '(.mlan0.rate_adapt._comment | type) == "array"')"
expect_eq "rate merge updates values" '0 10 20 50' \
    "$(printf '%s' "$_rate_merged" | jq -r '.mlan0.rate_adapt | "\(.mode) \(.low_thresh) \(.high_thresh) \(.interval_ms)"')"

# ── antcfg (FW Tx/Rx 안테나 경로) ────────────────────────────────────────────
# mcs_tier 와 같은 opt-in — 지금까지 적용하지 않던 설정이라 기본으로 켜면 출하 기기의
# RF 경로가 통째로 바뀐다. 꺼져 있으면 SET 자체를 하지 않는다.
_ant() { jq --arg t "$2" --arg r "$3" ".mlan0.antcfg={enabled:$1, tx:\$t, rx:\$r}" "$CONF"; }

rm -f "$STATE/mlan0.ant"
expect_rc "antcfg absent section skips" 0 wifi_fw_apply_antcfg "$CONF" mlan0
expect_eq "antcfg absent emitted no SET" 'no' "$([ -e "$STATE/mlan0.ant" ] && echo yes || echo no)"

_ant false 0x303 '' > "$WORK/ant-off.json"
expect_rc "antcfg disabled is not an error" 0 wifi_fw_apply_antcfg "$WORK/ant-off.json" mlan0
expect_eq "antcfg disabled emitted no SET" 'no' "$([ -e "$STATE/mlan0.ant" ] && echo yes || echo no)"
expect_rc "antcfg disabled reports rc=2 to validator" 2 wifi_fw_validate_antcfg_config "$WORK/ant-off.json" mlan0

_ant true 0x303 '' > "$WORK/ant-tx.json"
expect_rc "antcfg tx-only valid" 0 wifi_fw_validate_antcfg_config "$WORK/ant-tx.json" mlan0
rm -f "$STATE/mlan0.ant"
expect_rc "antcfg tx-only applies" 0 wifi_fw_apply_antcfg "$WORK/ant-tx.json" mlan0
expect_eq "antcfg tx-only omits rx arg" '0x303' "$(cat "$STATE/mlan0.ant" 2>/dev/null)"

_ant true 0x103 0x303 > "$WORK/ant-txrx.json"
rm -f "$STATE/mlan0.ant"
expect_rc "antcfg tx+rx applies" 0 wifi_fw_apply_antcfg "$WORK/ant-txrx.json" mlan0
expect_eq "antcfg tx+rx passes both args" '0x103 0x303' "$(cat "$STATE/mlan0.ant" 2>/dev/null)"

_ant true 3 '' > "$WORK/ant-dec.json"
expect_rc "antcfg accepts decimal" 0 wifi_fw_validate_antcfg_config "$WORK/ant-dec.json" mlan0
_ant true 0xFFFF 0x1770 > "$WORK/ant-sad.json"
expect_rc "antcfg accepts SAD diversity form" 0 wifi_fw_validate_antcfg_config "$WORK/ant-sad.json" mlan0

# 0 은 어떤 경로도 선택하지 않아 RF 가 죽는다 — 무선이 유일한 접속 경로라 반드시 거부.
_ant true 0 '' > "$WORK/ant-zero.json"
expect_rc "antcfg rejects zero tx" 1 wifi_fw_validate_antcfg_config "$WORK/ant-zero.json" mlan0
_ant true 0x303 0 > "$WORK/ant-zero-rx.json"
expect_rc "antcfg rejects zero rx" 1 wifi_fw_validate_antcfg_config "$WORK/ant-zero-rx.json" mlan0
_ant true 0x10000 '' > "$WORK/ant-over.json"
expect_rc "antcfg rejects out-of-range tx" 1 wifi_fw_validate_antcfg_config "$WORK/ant-over.json" mlan0
_ant true 0xZZ '' > "$WORK/ant-hex.json"
expect_rc "antcfg rejects non-hex tx" 1 wifi_fw_validate_antcfg_config "$WORK/ant-hex.json" mlan0
_ant true '' '' > "$WORK/ant-empty.json"
expect_rc "antcfg rejects empty tx when enabled" 1 wifi_fw_validate_antcfg_config "$WORK/ant-empty.json" mlan0

rm -f "$STATE/mlan0.ant"
expect_rc "invalid antcfg is skipped without fallback" 0 wifi_fw_apply_antcfg "$WORK/ant-zero.json" mlan0
expect_eq "invalid antcfg emitted no SET" 'no' "$([ -e "$STATE/mlan0.ant" ] && echo yes || echo no)"

# 어댑터 단위 설정이므로 두 iface 값이 다르면 경고를 남긴다
jq '.mlan0.antcfg={enabled:true,tx:"0x303",rx:""} | .mlan1.antcfg={enabled:true,tx:"0x202",rx:""}' \
    "$CONF" > "$WORK/ant-conflict.json"
: > "$LOG"
rm -f "$STATE/mlan0.ant"
wifi_fw_apply_antcfg "$WORK/ant-conflict.json" mlan0 >/dev/null 2>&1
grep -q 'adapter-level setting' "$LOG" \
    && pass "antcfg warns when two ifaces disagree" \
    || fail "antcfg does not warn on adapter-level conflict"

jq '.mlan0.antcfg={enabled:true,tx:"0x303",rx:""} | .mlan1.antcfg={enabled:true,tx:"0x303",rx:""}' \
    "$CONF" > "$WORK/ant-same.json"
: > "$LOG"
wifi_fw_apply_antcfg "$WORK/ant-same.json" mlan0 >/dev/null 2>&1
grep -q 'adapter-level setting' "$LOG" \
    && fail "antcfg warns even when values match (오탐)" \
    || pass "antcfg does not warn when values match"

expect_eq "template ships mlan0 antcfg disabled" 'false' "$(jq -r '.mlan0.antcfg.enabled' "$TEMPLATE")"
expect_eq "template ships mlan1 antcfg disabled" 'false' "$(jq -r '.mlan1.antcfg.enabled' "$TEMPLATE")"
grep -q 'wifi_fw_apply_antcfg "$WIFI_INIT_CONF_JSON" "$iface"' "$WIFI_INIT" \
    && pass "wifi_init delegates antcfg apply" \
    || fail "wifi_init does not delegate antcfg apply"

expect_rc "ax MCS config valid" 0 wifi_fw_validate_mcs_config "$CONF" mlan0
expect_rc "ac MCS config valid without HE" 0 wifi_fw_validate_mcs_config "$CONF" mlan1
jq '.mlan0.mcs_tier |= del(.vht)' "$CONF" > "$WORK/partial-mcs.json"
expect_rc "partial enabled MCS rejected" 1 wifi_fw_validate_mcs_config "$WORK/partial-mcs.json" mlan0

rm -f "$STATE/mlan0.mcs" "$STATE/mlan0.mcs_set_count"
export MCS_ACCEPT_ON_ATTEMPT=2
expect_rc "MCS retries until GET matches" 0 wifi_fw_apply_mcs_verified "$CONF" mlan0
expect_eq "MCS required two SET attempts" 2 "$(cat "$STATE/mlan0.mcs_set_count")"

rm -f "$STATE/mlan0.mcs" "$STATE/mlan0.mcs_set_count"
export MCS_ACCEPT_ON_ATTEMPT=99
expect_rc "MCS fails after bounded verification attempts" 1 wifi_fw_apply_mcs_verified "$CONF" mlan0
expect_eq "MCS attempts are bounded" 3 "$(cat "$STATE/mlan0.mcs_set_count")"

# 88W9098은 association 전 mcstiercfg GET에서 HT/VHT는 적용값을 반환하면서 HE만
# 0x0000으로 보일 수 있다. 이 상태를 persistent SET 실패로 오인하면 supplicant 시작 전
# wifi_init 재시작/비상 reboot 순환이 생긴다. HE만 미확정이면 연결 후 검증으로 defer한다.
rm -rf "$WIFI_MCS_PENDING_DIR"
rm -f "$STATE/mlan0.mcs" "$STATE/mlan0.mcs_set_count"
export MCS_ACCEPT_ON_ATTEMPT=1 MCS_HE_UNAVAILABLE=1
expect_rc "pre-association HE-only mismatch is deferred" 0 wifi_fw_apply_mcs_verified "$CONF" mlan0
expect_eq "deferred HE verification creates per-iface pending marker" yes \
    "$([ -e "$WIFI_MCS_PENDING_DIR/mcs_verify_pending_mlan0" ] && echo yes || echo no)"
deferred_set_count=$(cat "$STATE/mlan0.mcs_set_count")

unset MCS_HE_UNAVAILABLE
expect_rc "connected verification accepts deferred HE map" 0 wifi_fw_verify_mcs_connected "$CONF" mlan0
expect_eq "connected verification never re-applies MCS" "$deferred_set_count" "$(cat "$STATE/mlan0.mcs_set_count")"
expect_eq "successful connected verification clears pending marker" no \
    "$([ -e "$WIFI_MCS_PENDING_DIR/mcs_verify_pending_mlan0" ] && echo yes || echo no)"

rm -rf "$WIFI_MCS_PENDING_DIR"
rm -f "$STATE/mlan0.mcs" "$STATE/mlan0.mcs_set_count"
export MCS_HE_UNAVAILABLE=1
expect_rc "second HE-only mismatch is still deferred without blocking boot" 0 wifi_fw_apply_mcs_verified "$CONF" mlan0
connected_failure_set_count=$(cat "$STATE/mlan0.mcs_set_count")
expect_rc "connected mismatch is observable but non-destructive to caller" 1 wifi_fw_verify_mcs_connected "$CONF" mlan0
expect_eq "failed connected recovery performs one bounded SET" "$((connected_failure_set_count + 1))" "$(cat "$STATE/mlan0.mcs_set_count")"
expect_eq "failed connected recovery does not reassociate" 0 \
    "$([ -f "$STATE/wpa_calls" ] && wc -l < "$STATE/wpa_calls" || echo 0)"
expect_eq "failed connected verification remains pending for next link event" yes \
    "$([ -e "$WIFI_MCS_PENDING_DIR/mcs_verify_pending_mlan0" ] && echo yes || echo no)"
unset MCS_HE_UNAVAILABLE

# 실제 로드된 SDIO p149.115 실기: 첫 association에서 HE가 FW 기본(0xFFFA)으로 되돌아간다. connected SET은
# 링크를 끊지 않고 다음 association 값을 저장하므로 1회 reassociate 후 GET으로 확정한다.
rm -rf "$WIFI_MCS_PENDING_DIR"
rm -f "$STATE/mlan0.mcs" "$STATE/mlan0.mcs_set_count" "$STATE/wpa_calls"
export MCS_HE_UNAVAILABLE=1 MCS_ACCEPT_ON_ATTEMPT=1
expect_rc "pre-association HE visibility creates recovery pending" 0 wifi_fw_apply_mcs_verified "$CONF" mlan0
unset MCS_HE_UNAVAILABLE
touch "$STATE/mlan0.he_default"
export MCS_CONNECTED_SET_RECOVERS=1
connected_recovery_set_count=$(cat "$STATE/mlan0.mcs_set_count")
expect_rc "connected default HE schedules bounded recovery" 0 wifi_fw_verify_mcs_connected "$CONF" mlan0
expect_eq "connected recovery SET runs exactly once" "$((connected_recovery_set_count + 1))" "$(cat "$STATE/mlan0.mcs_set_count")"
expect_eq "connected recovery requests one reassociation" '-i mlan0 reassociate' "$(cat "$STATE/wpa_calls")"
expect_eq "reassociation keeps verification pending" yes \
    "$([ -e "$WIFI_MCS_PENDING_DIR/mcs_verify_pending_mlan0" ] && echo yes || echo no)"
expect_eq "reassociation attempt marker is present" yes \
    "$([ -e "$WIFI_MCS_PENDING_DIR/mcs_reassociate_once_mlan0" ] && echo yes || echo no)"
expect_rc "post-reassociation GET verifies stored MCS" 0 wifi_fw_verify_mcs_connected "$CONF" mlan0
expect_eq "post-reassociation verification performs no extra SET" "$((connected_recovery_set_count + 1))" "$(cat "$STATE/mlan0.mcs_set_count")"
expect_eq "post-reassociation verification performs no extra reassociate" 1 "$(wc -l < "$STATE/wpa_calls")"
expect_eq "post-reassociation verification clears pending" no \
    "$([ -e "$WIFI_MCS_PENDING_DIR/mcs_verify_pending_mlan0" ] && echo yes || echo no)"
expect_eq "post-reassociation verification clears attempt marker" no \
    "$([ -e "$WIFI_MCS_PENDING_DIR/mcs_reassociate_once_mlan0" ] && echo yes || echo no)"
unset MCS_CONNECTED_SET_RECOVERS

# 명확한 비영(非零) HE 오설정은 association-dependent visibility가 아니다.
# HT/VHT가 맞더라도 pending으로 완화하지 않고 기존 fatal 경로를 유지해야 한다.
rm -rf "$WIFI_MCS_PENDING_DIR"
rm -f "$STATE/mlan0.mcs" "$STATE/mlan0.mcs_set_count"
export MCS_HE_WRONG=1
expect_rc "definite non-zero HE mismatch remains fatal" 1 wifi_fw_apply_mcs_verified "$CONF" mlan0
expect_eq "definite HE mismatch creates no pending marker" no \
    "$([ -e "$WIFI_MCS_PENDING_DIR/mcs_verify_pending_mlan0" ] && echo yes || echo no)"
unset MCS_HE_WRONG

rm -f "$STATE/mlan1.mcs" "$STATE/mlan1.mcs_set_count"
export MCS_ACCEPT_ON_ATTEMPT=1
expect_rc "AC iface applies only HT/VHT" 0 wifi_fw_apply_mcs_verified "$CONF" mlan1
expect_eq "AC iface SET excludes HE" 'ht 7 vht 7' "$(cat "$STATE/mlan1.mcs")"

COLD_FLAG="$WORK/mcs-cold-retry"
first_cold_rc=$(wifi_fw_mcs_cold_failure_code "$COLD_FLAG" 2>/dev/null || true)
expect_eq "first cold MCS mismatch requests transitional status" 75 "$first_cold_rc"
expect_eq "first cold MCS mismatch creates retry marker" yes "$([ -e "$COLD_FLAG" ] && echo yes || echo no)"
second_cold_rc=$(wifi_fw_mcs_cold_failure_code "$COLD_FLAG" 2>/dev/null || true)
expect_eq "second MCS mismatch escalates to ordinary failure" 1 "$second_cold_rc"
wifi_fw_mcs_cold_success "$COLD_FLAG" 2>/dev/null || true
expect_eq "successful MCS verification clears cold marker" no "$([ -e "$COLD_FLAG" ] && echo yes || echo no)"

grep -q 'wifi_fw_config_lib.sh' "$WIFI_INIT" && pass "wifi_init sources FW config library" || fail "wifi_init does not source FW config library"
grep -q 'wifi_fw_apply_rate' "$WIFI_INIT" && pass "wifi_init delegates rate apply" || fail "wifi_init does not delegate rate apply"
grep -q 'wifi_fw_apply_mcs_verified' "$WIFI_INIT" && pass "wifi_init verifies MCS" || fail "wifi_init does not verify MCS"
grep -q 'wifi_fw_config_lib.sh' "$WIFI_EVENT" \
    && pass "wifi_event sources FW config library" \
    || fail "wifi_event does not source FW config library"
grep -q 'wifi_fw_verify_mcs_connected' "$WIFI_EVENT" \
    && pass "wifi_event verifies deferred MCS after connection" \
    || fail "wifi_event does not verify deferred MCS after connection"
if grep -q '^apply_mcs_tier()' "$WIFI_INIT"; then fail "legacy unverified MCS apply remains"; else pass "legacy unverified MCS apply removed"; fi
expect_eq "mlan1 HE template is empty" '' "$(jq -r '.mlan1.mcs_tier.he' "$TEMPLATE")"
if grep -q 'Applied live.*reconnect to take effect' "$WIFI_CLI"; then
    fail "CLI still claims connected live MCS changes association capability"
else
    pass "CLI does not claim connected live MCS association change"
fi
grep -q '^  rate)' "$WIFI_CLI" && pass "CLI exposes rate command" || fail "CLI rate command missing"
grep -q 'wifi_fw_validate_rate_config' "$WIFI_CLI" && pass "CLI shares rate validation" || fail "CLI does not share rate validation"
if grep -q 'mlanutl "$IFACE" rate_adapt_cfg "$' "$WIFI_CLI"; then
    fail "CLI performs ineffective connected rate SET"
else
    pass "CLI rate SET is persist-only"
fi
grep -q '^ExecStart=/usr/local/scripts/wifi_init.sh$' "$WIFI_INIT_UNIT" \
    && pass "wifi_init uses systemd lifecycle retry boundary" \
    || fail "wifi_init unit bypasses direct lifecycle retry boundary"
grep -q 'ExecCondition=.*ExecMainStatus.*75' "$EMERGENCY_UNIT" \
    && pass "emergency reboot skips transitional MCS status" \
    || fail "emergency reboot does not skip transitional MCS status"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
