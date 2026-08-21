#!/bin/bash
# wifi.sh/opc_wlan_apply.sh wpa conf writer integration tests (hardware/root free)
set -u

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
WIFI_SH="$SCRIPT_DIR/wifi.sh"
OPC_SH="$SCRIPT_DIR/opc_wlan_apply.sh"
TD=$(mktemp -d)
trap 'rm -rf "$TD"' EXIT
BIN="$TD/bin"
WPA_DIR="$TD/wpa"
STATE_DIR="$TD/state"
CALL_LOG="$TD/calls.log"
mkdir -p "$BIN" "$WPA_DIR" "$STATE_DIR"
: > "$CALL_LOG"

cat > "$BIN/logger" <<'EOF'
#!/bin/sh
exit 0
EOF
cat > "$BIN/sync" <<'EOF'
#!/bin/sh
exit 0
EOF
cat > "$BIN/install" <<'EOF'
#!/bin/sh
while [ "$#" -gt 2 ]; do shift; done
cp "$1" "$2"
EOF
cat > "$BIN/systemctl" <<'EOF'
#!/bin/sh
exit 0
EOF
cat > "$BIN/wpa_cli" <<'EOF'
#!/bin/sh
printf 'wpa_cli %s\n' "$*" >> "$CALL_LOG"
case "$*" in
  *" status"*)
    count=$(cat "$STATE_DIR/status-count" 2>/dev/null || echo 0)
    count=$((count + 1))
    printf '%s\n' "$count" > "$STATE_DIR/status-count"
    mode=$(cat "$STATE_DIR/status-mode" 2>/dev/null || echo base)
    ssid=Base; id=0; freq=5180; state=COMPLETED
    case "$mode" in
      stale-then-target)
        if [ "$count" -le 2 ]; then ssid=OldNet; freq=2412; else ssid=NewNet; freq=5180; fi ;;
      wrong-ssid) ssid=OldNet; freq=5180 ;;
      wrong-freq) ssid=NewNet; freq=2412 ;;
      mode-a-current)
        if [ "$count" -eq 1 ]; then
          ssid=Office; id=1
        elif [ "$count" -eq 2 ]; then
          ssid=Base; id=0
        else
          ssid=Office; id=1
        fi ;;
      no-current)
        if [ "$count" -eq 1 ]; then state=DISCONNECTED; ssid=; id=; freq=; fi ;;
    esac
    cat <<EOT
wpa_state=$state
ssid=$ssid
id=$id
freq=$freq
EOT
    ;;
  *" reconfigure"*)
    count=$(cat "$STATE_DIR/reconfigure-count" 2>/dev/null || echo 0)
    count=$((count + 1))
    printf '%s\n' "$count" > "$STATE_DIR/reconfigure-count"
    mode=$(cat "$STATE_DIR/reconfigure-mode" 2>/dev/null || echo ok)
    if [ "$mode" = "fail-first" ] && [ "$count" -eq 1 ]; then
      printf 'FAIL\n'
    else
      printf 'OK\n'
    fi
    ;;
  *) printf 'OK\n' ;;
esac
EOF
chmod +x "$BIN"/*
export PATH="$BIN:$PATH"
export CALL_LOG STATE_DIR

PASS=0
FAIL=0
pass() { PASS=$((PASS + 1)); echo "PASS: $*"; }
fail() { FAIL=$((FAIL + 1)); echo "FAIL: $*"; }
check_equal() {
    local desc="$1" got="$2" expected="$3"
    if [ "$got" = "$expected" ]; then pass "$desc"; else fail "$desc (expected=$expected got=$got)"; fi
}

write_mode_a_legacy() {
    cat > "$WPA_DIR/wpa_supplicant-mlan0.conf" <<'EOF'
update_config=1
network={
    ssid="Base"
    key_mgmt=WPA-PSK
    psk="12345678"
    scan_freq=2412
    freq_list=2412
}
# >>> wifi_extra_ssid auto-generated (do not edit) >>>
network={
    ssid="Office"
    key_mgmt=WPA-PSK
    psk="12345678"
    scan_freq=5180
    freq_list=5180
}
# <<< wifi_extra_ssid auto-generated <<<
EOF
}

write_mode_b_legacy() {
    cat > "$WPA_DIR/wpa_supplicant-mlan0.conf" <<'EOF'
update_config=1
network={
    ssid="OldNet"
    key_mgmt=WPA-PSK
    psk="12345678"
    scan_freq=2412
    freq_list=2412
}
EOF
}

set_status_mode() {
    printf '%s\n' "$1" > "$STATE_DIR/status-mode"
    rm -f "$STATE_DIR/status-count"
}

set_reconfigure_mode() {
    printf '%s\n' "$1" > "$STATE_DIR/reconfigure-mode"
    rm -f "$STATE_DIR/reconfigure-count"
}

count_global_freq() {
    awk '
      /^[[:space:]]*#/ { next }
      /^[[:space:]]*network[[:space:]]*=/ { in_net=1 }
      !in_net && /^[[:space:]]*freq_list[[:space:]]*=/ { n++ }
      in_net && /^[[:space:]]*}/ { in_net=0 }
      END { print n+0 }
    ' "$1"
}

count_block_freq() {
    awk -v expected="$2" '
      /^[[:space:]]*network[[:space:]]*=/ { in_net=1; next }
      in_net && $0 ~ "^[[:space:]]*freq_list=" expected "$" { n++ }
      in_net && /^[[:space:]]*}/ { in_net=0 }
      END { print n+0 }
    ' "$1"
}

echo "=== wifi freq canonical writer ==="
write_mode_a_legacy
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" bash "$WIFI_SH" 0 freq 5180 5200 >/dev/null 2>&1
rc=$?
CONF="$WPA_DIR/wpa_supplicant-mlan0.conf"
check_equal "wifi freq exits zero" "$rc" "0"
check_equal "wifi freq writes one global list" "$(count_global_freq "$CONF")" "1"
check_equal "wifi freq writes same list to both blocks" \
    "$(count_block_freq "$CONF" '5180 5200')" "2"
check_equal "wifi freq removes scan_freq" \
    "$(grep -Ec '^[[:space:]]*scan_freq[[:space:]]*=' "$CONF" || true)" "0"
check_equal "wifi freq forces update_config=0" \
    "$(grep -Ec '^update_config=0$' "$CONF" || true)" "1"
check_equal "wifi freq remains persist-only" \
    "$(grep -c 'reconfigure' "$CALL_LOG" || true)" "0"

echo ""
echo "=== wifi connect target-aware writer ==="
write_mode_b_legacy
set_status_mode stale-then-target
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" ASSOC_TIMEOUT_DEFAULT=1 \
    bash "$WIFI_SH" 0 connect NewNet 5180 >/dev/null 2>&1
rc=$?
check_equal "wifi connect waits past stale COMPLETED" "$rc" "0"
if [ "$(cat "$STATE_DIR/status-count" 2>/dev/null || echo 0)" -ge 3 ]; then
    pass "wifi connect polls until requested SSID"
else
    fail "wifi connect polls until requested SSID"
fi
check_equal "wifi connect writes one global list" "$(count_global_freq "$CONF")" "1"
check_equal "wifi connect writes canonical block list" \
    "$(count_block_freq "$CONF" '5180')" "1"
check_equal "wifi connect removes scan_freq" \
    "$(grep -Ec '^[[:space:]]*scan_freq[[:space:]]*=' "$CONF" || true)" "0"

write_mode_b_legacy
set_status_mode wrong-ssid
WPA_CONF_DIR="$WPA_DIR" ASSOC_TIMEOUT_DEFAULT=1 \
    bash "$WIFI_SH" 0 connect NewNet 5180 >/dev/null 2>&1
check_equal "wifi connect rejects COMPLETED on wrong SSID" "$?" "8"

write_mode_b_legacy
set_status_mode wrong-freq
WPA_CONF_DIR="$WPA_DIR" ASSOC_TIMEOUT_DEFAULT=1 \
    bash "$WIFI_SH" 0 connect NewNet 5180 >/dev/null 2>&1
check_equal "wifi connect rejects requested SSID on wrong frequency" "$?" "8"

write_mode_a_legacy
before=$(sha256sum "$CONF" | awk '{print $1}')
WPA_CONF_DIR="$WPA_DIR" bash "$WIFI_SH" 0 connect NewNet 5180 >/dev/null 2>&1
rc=$?
after=$(sha256sum "$CONF" | awk '{print $1}')
check_equal "wifi connect explicit SSID remains rejected in Mode A" "$rc" "1"
check_equal "Mode A rejection leaves conf unchanged" "$after" "$before"

write_mode_a_legacy
set_status_mode mode-a-current
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" ASSOC_TIMEOUT_DEFAULT=1 \
    bash "$WIFI_SH" 0 connect >/dev/null 2>&1
rc=$?
check_equal "Mode A no-arg reconnect confirms original network id" "$rc" "0"
check_equal "Mode A no-arg reconnect reselects current id" \
    "$(grep -c 'select_network 1$' "$CALL_LOG" || true)" "1"
if [ "$(cat "$STATE_DIR/status-count" 2>/dev/null || echo 0)" -ge 3 ]; then
    pass "Mode A no-arg reconnect ignores COMPLETED on another id"
else
    fail "Mode A no-arg reconnect ignores COMPLETED on another id"
fi
check_equal "Mode A no-arg reconnect restores all network blocks" \
    "$(grep -c 'enable_network all$' "$CALL_LOG" || true)" "1"

write_mode_a_legacy
set_status_mode no-current
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" ASSOC_TIMEOUT_DEFAULT=1 \
    bash "$WIFI_SH" 0 connect >/dev/null 2>&1
rc=$?
check_equal "Mode A without current id keeps broad recovery" "$rc" "0"
check_equal "Mode A broad recovery uses reassociate" \
    "$(grep -c 'reassociate$' "$CALL_LOG" || true)" "1"
check_equal "Mode A broad recovery does not invent a target id" \
    "$(grep -c 'select_network' "$CALL_LOG" || true)" "0"

echo ""
echo "=== OPC canonical writer and rollback ==="
write_mode_b_legacy
set_reconfigure_mode ok
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$TD/run" \
    sh "$OPC_SH" mlan0 freq "5180 5200" >/dev/null 2>&1
rc=$?
check_equal "OPC freq exits zero" "$rc" "0"
check_equal "OPC freq writes one global list" "$(count_global_freq "$CONF")" "1"
check_equal "OPC freq writes canonical block list" \
    "$(count_block_freq "$CONF" '5180 5200')" "1"
check_equal "OPC freq removes scan_freq" \
    "$(grep -Ec '^[[:space:]]*scan_freq[[:space:]]*=' "$CONF" || true)" "0"
check_equal "OPC successful apply reconfigures once" \
    "$(grep -c 'reconfigure$' "$CALL_LOG" || true)" "1"

write_mode_b_legacy
cp "$CONF" "$TD/opc-original.conf"
set_reconfigure_mode fail-first
: > "$CALL_LOG"
WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$TD/run" \
    sh "$OPC_SH" mlan0 freq "5180 5200" >/dev/null 2>&1
rc=$?
check_equal "OPC reconfigure failure exits 5" "$rc" "5"
if cmp -s "$CONF" "$TD/opc-original.conf"; then
    pass "OPC reconfigure failure restores exact original conf"
else
    fail "OPC reconfigure failure restores exact original conf"
fi
check_equal "OPC rollback reconfigures restored conf" \
    "$(grep -c 'reconfigure$' "$CALL_LOG" || true)" "2"

write_mode_a_legacy
before=$(sha256sum "$CONF" | awk '{print $1}')
WPA_CONF_DIR="$WPA_DIR" WIFI_RUN_DIR="$TD/run" \
    sh "$OPC_SH" mlan0 ssid NewNet >/dev/null 2>&1
rc=$?
after=$(sha256sum "$CONF" | awk '{print $1}')
check_equal "OPC explicit SSID remains rejected in Mode A" "$rc" "2"
check_equal "OPC Mode A rejection leaves conf unchanged" "$after" "$before"

echo ""
echo "RESULT: PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
