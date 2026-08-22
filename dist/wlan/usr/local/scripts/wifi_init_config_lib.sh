#!/bin/bash

# 설정 JSON 가용성 판정 — 파일/jq/파싱 세 가지를 한 곳에서 본다.
# wifi_init.sh 와 wifi_apply_enabled.sh 가 같은 판정을 각자 구현하던 것을 통일한 것이다.
# 종료코드로 사유를 구분해 호출자가 자기 정책(warn/crit, skip/abort, 폴백값)을 정한다.
#   0 = 읽고 파싱 가능
#   1 = 파일 없음
#   2 = jq 없음
#   3 = 파싱 실패 (파일은 존재)
# 0 이 아니면 "키가 없다"와 "설정을 못 읽는다"를 구분할 수 없다 — 모든 키가 null 로
# 평가되므로 부재 키의 기본값을 적용하면 운영자 의도를 통째로 덮어쓴다. 특히 인터페이스
# 활성 기본값을 false 로 떨어뜨리면 설정이 깨진 기기가 무선까지 잃어 원격 복구가 끊긴다.
wifi_init_conf_status() {
    local file="${1:-${WIFI_INIT_CONF_JSON:-/usr/local/etc/wifi_init_conf.json}}"

    [ -f "$file" ] || return 1
    command -v jq >/dev/null 2>&1 || return 2
    jq empty "$file" >/dev/null 2>&1 || return 3
    return 0
}

wifi_init_json_key_exists() {
    local file="$1"
    local query="$2"

    [ -f "$file" ] || return 1
    command -v jq >/dev/null 2>&1 || return 1
    jq -e "$query != null" "$file" >/dev/null 2>&1
}

wifi_init_json_read_raw() {
    local file="$1"
    local query="$2"

    jq -r "$query" "$file" 2>/dev/null
}

wifi_init_normalize_bool() {
    local value="${1:-}"
    local default="${2:-true}"
    local value_lc

    value_lc=$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]')
    case "$value_lc" in
        true|1|yes|on|enabled)
            printf 'true\n'
            ;;
        false|0|no|off|disabled)
            printf 'false\n'
            ;;
        *)
            printf '%s\n' "$default"
            ;;
    esac
}

# === 부팅 roaming owner/topology snapshot ==================================
# /run은 reboot 때 비워지므로 최초 생성값이 이 boot의 owner/topology SoT다. JSON을
# 런타임에 편집하거나 daemon이 crash-restart되어도 이 파일은 덮어쓰지 않는다.
wifi_roam_policy_path() {
    printf '%s/%s.roam-policy.json\n' "${WIFI_RUN_DIR:-/run/wifi}" "$1"
}

# Snapshot 디렉터리와 독립적인 boot-latch. 기본 /run/wifi의
# 상위(/run)에 두어 /run/wifi만 삭제돼도 "이 boot에 이미 snapshot을
# 생성함"을 기억한다. /run 전체가 비워지는 재부팅에서만 둘 다 사라진다.
wifi_roam_policy_latch_path() {
    local iface="$1" run_dir="${WIFI_RUN_DIR:-/run/wifi}" latch_dir
    latch_dir="${WIFI_ROAM_POLICY_LATCH_DIR:-}"
    if [ -z "$latch_dir" ]; then
        latch_dir=$(dirname -- "$run_dir") || return 1
    fi
    printf '%s/.%s.roam-policy.latched\n' "$latch_dir" "$iface"
}

wifi_sync_path_or_global() {
    sync "$1" 2>/dev/null || sync 2>/dev/null
}

# Validate a JSON SSID array without translating any identity.  When a base
# supplicant configuration exists, reject an extra/base duplicate as well.
wifi_ssid_array_validate_json() {
    local extras_json="$1" base_conf="${2:-}"
    python3 - "$extras_json" "$base_conf" 2>/dev/null <<'PY'
import json
import os
import re
import sys

def valid(value):
    if not isinstance(value, str):
        raise ValueError("SSID must be a string")
    raw = value.encode("utf-8")
    if not 1 <= len(raw) <= 32:
        raise ValueError("SSID must be 1..32 UTF-8 bytes")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in value):
        raise ValueError("SSID contains a C0 control or DEL")
    return value

def parse_value(token):
    token = token.strip()
    if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
        return valid(token[1:-1])
    if not re.fullmatch(r"(?:[0-9A-Fa-f]{2})+", token):
        raise ValueError("unsupported base SSID representation")
    return valid(bytes.fromhex(token).decode("utf-8"))

values = json.loads(sys.argv[1])
if not isinstance(values, list):
    raise ValueError("extra_ssids must be an array")
checked = [valid(value) for value in values]
if len(set(checked)) != len(checked):
    raise ValueError("duplicate extra SSID identity")

base_conf = sys.argv[2]
base = None
if base_conf and os.path.isfile(base_conf):
    in_network = False
    with open(base_conf, encoding="utf-8") as stream:
        for raw_line in stream:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if re.match(r"^network\s*=\s*\{", line):
                in_network = True
                continue
            if in_network and line.startswith("}"):
                break
            if in_network and re.match(r"^ssid\s*=", line):
                base = parse_value(line.split("=", 1)[1])
                break
if base is not None and base in checked:
    raise ValueError("extra SSID duplicates base SSID identity")
PY
}

wifi_ssid_to_hex() {
    python3 - "$1" 2>/dev/null <<'PY'
import sys
value = sys.argv[1]
raw = value.encode("utf-8")
if not 1 <= len(raw) <= 32:
    raise SystemExit(1)
if any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in value):
    raise SystemExit(1)
print(raw.hex())
PY
}

# Match upstream wpa_ssid_txt()/printf_encode output used by CTRL_IFACE
# status/list_networks/scan_results.  Writers compare this form to status while
# retaining the original identity for config serialization and operator text.
wifi_ssid_to_wpa_text() {
    python3 - "$1" 2>/dev/null <<'PY'
import sys
value = sys.argv[1]
raw = value.encode("utf-8")
if not 1 <= len(raw) <= 32:
    raise SystemExit(1)
if any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in value):
    raise SystemExit(1)
out = []
for byte in raw:
    if byte == 0x22:
        out.append(r'\"')
    elif byte == 0x5c:
        out.append(r'\\')
    elif 0x20 <= byte <= 0x7e:
        out.append(chr(byte))
    else:
        out.append(f"\\x{byte:02x}")
print("".join(out))
PY
}

_wifi_roam_policy_mark_latched() {
    local iface="$1" latch latch_dir tmp
    latch=$(wifi_roam_policy_latch_path "$iface") || return 1
    [ -e "$latch" ] && return 0
    latch_dir=${latch%/*}
    mkdir -p "$latch_dir" || return 1
    tmp=$(mktemp "$latch_dir/.${iface}.roam-policy.latched.XXXXXX") || return 1
    if ! printf '1\n' > "$tmp" || ! chmod 0600 "$tmp" 2>/dev/null; then
        rm -f "$tmp"
        return 1
    fi
    if ! wifi_sync_path_or_global "$tmp"; then
        rm -f "$tmp"
        return 1
    fi
    if ! mv -f "$tmp" "$latch"; then
        rm -f "$tmp"
        return 1
    fi
    wifi_sync_path_or_global "$latch" || return 1
    wifi_sync_path_or_global "$latch_dir" || return 1
}

wifi_roam_policy_validate_file() {
    local file="$1" iface="$2" extras
    [ -f "$file" ] || return 1
    command -v jq >/dev/null 2>&1 || return 1
    jq -e --arg iface "$iface" '
        type == "object" and
        .version == 1 and
        .iface == $iface and
        (.roaming_enabled | type == "boolean") and
        (.bgscan_enabled | type == "boolean") and
        (.generate_network_blocks | type == "boolean") and
        (.extra_ssids | type == "array") and
        all(.extra_ssids[]; type == "string")
    ' "$file" >/dev/null 2>&1 || return 1
    extras=$(jq -c '.extra_ssids' "$file" 2>/dev/null) || return 1
    wifi_ssid_array_validate_json "$extras"
}

# 현재 JSON에서 snapshot을 **없을 때만** 원자 생성한다. 기존 파일이 valid면 그대로
# 유지하고, invalid면 추측/덮어쓰기 없이 실패한다(한 boot 안 owner hot-switch 차단).
wifi_roam_policy_ensure_snapshot() {
    local iface="$1"
    local conf_json="${2:-${WIFI_INIT_CONF_JSON:-/usr/local/etc/wifi_init_conf.json}}"
    local run_dir="${WIFI_RUN_DIR:-/run/wifi}" policy latch tmp base_conf
    local roaming_enabled bgscan_enabled generate extras

    policy=$(wifi_roam_policy_path "$iface") || return 1
    latch=$(wifi_roam_policy_latch_path "$iface") || return 1
    base_conf="${WPA_CONF_DIR:-/etc/wpa_supplicant}/wpa_supplicant-${iface}.conf"
    if [ ! -f "$base_conf" ]; then
        base_conf="${WIFI_WPA_DEFAULT_CONF_DIR:-${SCRIPT_DIR:-/usr/local/scripts}/../../../opt/wlan/config/wpa_supplicant}/wpa_supplicant-${iface}.conf"
    fi
    [ -f "$base_conf" ] || return 1
    command -v flock >/dev/null 2>&1 || return 1
    mkdir -p "$run_dir" || return 1
    # 두 apply 인스턴스가 동시에 'missing'을 보고 서로 다른 JSON으로 overwrite하지
    # 못하게 existence check부터 rename까지 직렬화한다.
    exec 8>"$run_dir/.roam-policy.lock" || return 1
    flock -x 8 || return 1
    if [ -e "$policy" ]; then
        # A policy without the tombstone is an untrusted policy-first crash
        # state.  Never promote it or reconstruct from mutable live JSON.
        [ -e "$latch" ] || return 1
        wifi_roam_policy_validate_file "$policy" "$iface" || return 1
        extras=$(jq -c '.extra_ssids' "$policy" 2>/dev/null) || return 1
        wifi_ssid_array_validate_json "$extras" "$base_conf" || return 1
        return 0
    fi
    # 같은 boot에 snapshot을 삭제한 후 live JSON으로 owner/topology를
    # 재구성하면 hot-switch가 된다. 재생성 대신 fail-closed.
    [ ! -e "$latch" ] || return 1

    wifi_init_conf_status "$conf_json" || return 1

    roaming_enabled=$(wifi_init_normalize_bool \
        "$(wifi_init_json_read_raw "$conf_json" ".${iface}.roaming.enabled")" false) || return 1
    bgscan_enabled=$(wifi_init_normalize_bool \
        "$(wifi_init_json_read_raw "$conf_json" ".${iface}.bgscan.enabled")" false) || return 1
    generate=$(wifi_init_normalize_bool \
        "$(wifi_init_json_read_raw "$conf_json" ".${iface}.roaming.generate_network_blocks")" false) || return 1
    # Preserve Mode A and Mode B manual identities alike.  Topology generation
    # is gated separately; the immutable snapshot remains the manual policy SoT.
    extras=$(jq -c --arg iface "$iface" \
        '(.[$iface].roaming.extra_ssids // []) as $raw
         | if ($raw | type) == "array" then $raw else error("extra_ssids") end' \
        "$conf_json" 2>/dev/null) || return 1
    wifi_ssid_array_validate_json "$extras" "$base_conf" || return 1

    tmp=$(mktemp "$run_dir/.${iface}.roam-policy.XXXXXX") || return 1
    if ! jq -cn \
        --arg iface "$iface" \
        --argjson roaming_enabled "$roaming_enabled" \
        --argjson bgscan_enabled "$bgscan_enabled" \
        --argjson generate "$generate" \
        --argjson extras "$extras" \
        '{version: 1, iface: $iface,
          roaming_enabled: $roaming_enabled,
          bgscan_enabled: $bgscan_enabled,
          generate_network_blocks: $generate,
          extra_ssids: $extras}' > "$tmp"; then
        rm -f "$tmp"
        return 1
    fi
    chmod 0644 "$tmp" 2>/dev/null || {
        rm -f "$tmp"
        return 1
    }
    if ! wifi_sync_path_or_global "$tmp"; then
        rm -f "$tmp"
        return 1
    fi
    wifi_roam_policy_validate_file "$tmp" "$iface" || {
        rm -f "$tmp"
        return 1
    }

    # Commit tombstone first.  Any failure after its rename is a same-boot
    # fail-closed state; policy is never reconstructed from changed JSON.
    if ! _wifi_roam_policy_mark_latched "$iface"; then
        rm -f "$tmp"
        return 1
    fi
    if ! mv -f "$tmp" "$policy"; then
        rm -f "$tmp"
        return 1
    fi
    wifi_sync_path_or_global "$policy" || return 1
    wifi_sync_path_or_global "$run_dir" || return 1
    wifi_roam_policy_validate_file "$policy" "$iface"
}

wifi_roam_policy_get_bool() {
    local iface="$1" key="$2" policy
    case "$key" in
        roaming_enabled|bgscan_enabled|generate_network_blocks) ;;
        *) return 1 ;;
    esac
    policy=$(wifi_roam_policy_path "$iface") || return 1
    wifi_roam_policy_validate_file "$policy" "$iface" || return 1
    jq -r ".${key}" "$policy" 2>/dev/null
}

# network block 수. 파싱이 깨져 닫히지 않은 block도 시작 토큰 기준으로 보수 집계한다.
wifi_wpa_conf_network_count() {
    local conf="$1"
    [ -f "$conf" ] || { printf '0\n'; return 0; }
    awk '/^[[:space:]]*network[[:space:]]*=[[:space:]]*\{/ { n++ }
         END { print n+0 }' "$conf"
}

# 단일 SSID를 일괄 치환하면 안 되는 topology인지 판정한다.
# 우선순위: 이 boot snapshot > (테스트/boot 전) live JSON > 실제 conf shape/sentinel.
# snapshot이 존재하지만 invalid면 안전하게 multi로 판정해 mutation을 차단한다.
wifi_wpa_conf_is_multi_topology() {
    local iface="$1" conf="$2" policy latch gen="" count
    local conf_json="${WIFI_INIT_CONF_JSON:-/usr/local/etc/wifi_init_conf.json}"
    policy=$(wifi_roam_policy_path "$iface") || return 0
    latch=$(wifi_roam_policy_latch_path "$iface") || return 0
    if [ -e "$policy" ]; then
        if ! wifi_roam_policy_validate_file "$policy" "$iface"; then
            return 0
        fi
        gen=$(jq -r '.generate_network_blocks' "$policy" 2>/dev/null) || return 0
    else
        # Snapshot이 이미 생성된 boot에 파일만 유실된 상태. live JSON으로
        # 단일 topology를 추측해 writer가 SSID를 일괄 치환하지 못하게 차단.
        [ ! -e "$latch" ] || return 0
        if wifi_init_conf_status "$conf_json"; then
            gen=$(wifi_init_normalize_bool \
                "$(wifi_init_json_read_raw "$conf_json" ".${iface}.roaming.generate_network_blocks")" \
                false)
        fi
    fi
    [ "$gen" = "true" ] && return 0
    grep -q '^# >>> wifi_extra_ssid' "$conf" 2>/dev/null && return 0
    count=$(wifi_wpa_conf_network_count "$conf") || return 0
    [ "${count:-0}" -gt 1 ] 2>/dev/null && return 0
    return 1
}

# wifi CLI와 OPC writer가 공유하는 per-interface advisory lock. FD 9는 process
# 종료까지 열린 채 유지되어 render/install/reconfigure/rollback 전체를 직렬화한다.
wifi_wpa_conf_lock_acquire() {
    local iface="$1" run_dir="${WIFI_RUN_DIR:-/run/wifi}" lock_file
    command -v flock >/dev/null 2>&1 || return 1
    mkdir -p "$run_dir" || return 1
    lock_file="$run_dir/${iface}.wpa-conf.lock"
    exec 9>"$lock_file" || return 1
    ( exec 7>&-; flock -x 9 )
}

# Close the connect transaction's private descriptors in the current child.
# The owning transaction shell never calls this directly and remains the sole
# lock owner until exit.
wifi_wpa_child_close() {
    exec 7>&- 9>&-
}

# Replace the current child shell with an external command.  Direct command
# substitutions use this form so no intermediate substitution shell retains
# the locks.
wifi_wpa_child_exec() {
    wifi_wpa_child_close
    exec "$@"
}

# A command substitution that must invoke a shell function closes immediately
# in that substitution shell; every transitive external child therefore
# inherits both descriptors closed while stdout and status remain unchanged.
wifi_wpa_child_call() {
    wifi_wpa_child_close
    "$@"
}

# Normal external calls get exactly one close-and-exec child.
wifi_wpa_run_child() (
    wifi_wpa_child_exec "$@"
)

# Normal calls into shared shell helpers get one close-first child.  The helper
# may spawn multiple external commands, all of which inherit closed FDs, while
# unrelated direct callers of those helpers retain their existing behavior.
wifi_wpa_run_child_call() (
    wifi_wpa_child_call "$@"
)

# Exact plain FAIL is the deployed "no active scan" result.  OK only accepts
# the abort request; the scan teardown can still be in progress, so reissue a
# small fixed number of times until FAIL proves quiescence.  Every other reply,
# transport failure, or bound exhaustion is fail-closed.  This is deliberately
# not a runtime knob and is independent of the association proof budget.
wifi_wpa_abort_scan_quiesce() {
    local iface="$1" reply attempt
    for attempt in 1 2 3 4 5; do
        if ! reply=$(wifi_wpa_child_exec wpa_cli -i "$iface" abort_scan 2>/dev/null); then
            return 1
        fi
        case "$reply" in
            FAIL)
                return 0
                ;;
            OK)
                [ "$attempt" -lt 5 ] || return 1
                wifi_wpa_run_child sleep 0.05 || return 1
                ;;
            *)
                return 1
                ;;
        esac
    done
    return 1
}

# FD 7 serializes live scans with association-changing operations.  Callers
# already hold FD 9, so the global order is always conf -> scan-transition.
# Tests may use a zero timeout; production defaults to a bounded 15 seconds.
wifi_scan_transition_lock_acquire() {
    local iface="$1" run_dir="${WIFI_RUN_DIR:-/run/wifi}" lock_file timeout
    timeout="${WIFI_SCAN_TRANSITION_LOCK_TIMEOUT:-15}"
    case "$timeout" in
        0) ;;
        ''|*[!0-9]*|*) timeout=15 ;;
    esac
    command -v flock >/dev/null 2>&1 || return 1
    wifi_wpa_run_child mkdir -p "$run_dir" || return 1
    lock_file="$run_dir/${iface}.scan-transition.lock"
    exec 7>"$lock_file" || return 1
    ( exec 9>&-; flock -w "$timeout" -x 7 )
}

wifi_init_get_iface_enabled() {
    local iface="$1"
    local default="${2:-true}"
    local value="$default"
    local conf_json="${WIFI_INIT_CONF_JSON:-/usr/local/etc/wifi_init_conf.json}"
    local query=".${iface}.enabled"

    if wifi_init_json_key_exists "$conf_json" "$query"; then
        value=$(wifi_init_json_read_raw "$conf_json" "$query")
    fi

    wifi_init_normalize_bool "$value" "$default"
}

wifi_init_get_iface_frequency() {
    local iface="$1"
    local default="${2:-auto}"
    local value="$default"
    local conf_json="${WIFI_INIT_CONF_JSON:-/usr/local/etc/wifi_init_conf.json}"
    local query=".${iface}.Frequency"

    if wifi_init_json_key_exists "$conf_json" "$query"; then
        value=$(wifi_init_json_read_raw "$conf_json" "$query")
    fi

    if [ -z "$value" ] || [ "$value" = "null" ]; then
        value="$default"
    fi

    printf '%s\n' "$value"
}

wifi_init_iface_is_enabled() {
    [ "$(wifi_init_get_iface_enabled "$1" "${2:-true}")" = "true" ]
}

# bandcfg 모드 상한 마스크 — wifi.sh(mode/radio-apply)와 wifi_init.sh(부팅 재적용)
# 공용 단일 정의. 모드는 "상한(cap)" 모델: 밴드 선택은 freq(freq_list)가 담당하므로
# 양 밴드 비트를 함께 켠다. GAC(2.4G 11ac, 비표준)은 FW fw_bands 검증 거부
# 가능성이 있어 제외.
wifi_init_mode_to_bandcfg_mask() {
    case "$1" in
        b)  echo 0x1 ;;    # B
        g)  echo 0x3 ;;    # B|G
        a)  echo 0x7 ;;    # B|G|A (legacy 상한: 5G=a, 2.4G=g)
        n)  echo 0x1F ;;   # +GN|AN
        ac) echo 0x5F ;;   # +AAC
        ax) echo 0x35F ;;  # +GAX|AAX
        *)  return 1 ;;    # unknown mode (출력 없음)
    esac
}

# 대역폭 → htcapinfo / vhtcfg bwcfg 매핑 — wifi.sh(radio-apply)와
# wifi_init.sh(부팅 재적용) 공용 단일 정의.
# 0x05c20000 = 부팅 기본 htcapinfo와 동일, 20은 bit17(20/40 enable)만 클리어.
wifi_init_bw_to_htcap() {
    case "$1" in
        20)         echo 0x05c00000 ;;  # bit17 clear → 20MHz 전용
        40|80|auto) echo 0x05c20000 ;;
        *)          return 1 ;;
    esac
}

wifi_init_bw_to_vhtbw() {
    case "$1" in
        20|40)   echo 0 ;;  # VHT BW는 11N CFG(20/40)를 따름
        80|auto) echo 1 ;;  # VHT cap BW(80)를 따름
        *)       return 1 ;;
    esac
}

# wpa_supplicant conf에서 패키지 공통 주파수 목록을 결정한다.
# 우선순위: 전역 freq_list > 첫 network 블록 freq_list > 첫 블록 legacy scan_freq fallback.
# 마지막 두 항목은 canonical 형식으로 이행하기 전 배포 conf를 위한 부팅 호환 경로다.
# 출력은 원래 순서를 보존한 공백 구분 목록이며, 제한이 없거나 파일이 없으면 무출력.
wifi_wpa_conf_common_freqs() {
    local conf="$1"
    [ -f "$conf" ] || return 0

    awk '
        function trim(s) {
            sub(/^[[:space:]]+/, "", s)
            sub(/[[:space:]]+$/, "", s)
            return s
        }
        function setting_value(line, key, value) {
            value = line
            sub(/^[[:space:]]*/, "", value)
            sub("^" key "[[:space:]]*=[[:space:]]*", "", value)
            sub(/[[:space:]]+#[^"]*$/, "", value)
            return trim(value)
        }
        /^[[:space:]]*#/ { next }
        /^[[:space:]]*network[[:space:]]*=[[:space:]]*\{/ {
            in_net = 1
            net_count++
            next
        }
        in_net && /^[[:space:]]*\}/ {
            in_net = 0
            next
        }
        !in_net && /^[[:space:]]*freq_list[[:space:]]*=/ {
            if (global_list == "")
                global_list = setting_value($0, "freq_list")
            next
        }
        in_net && net_count == 1 && /^[[:space:]]*freq_list[[:space:]]*=/ {
            if (base_list == "")
                base_list = setting_value($0, "freq_list")
            next
        }
        in_net && net_count == 1 && /^[[:space:]]*scan_freq[[:space:]]*=/ {
            if (base_scan == "")
                base_scan = setting_value($0, "scan_freq")
            next
        }
        END {
            if (global_list != "") print global_list
            else if (base_list != "") print base_list
            else if (base_scan != "") print base_scan
        }
    ' "$conf"
}

# wpa_supplicant conf를 canonical 주파수 형식으로 순수 변환한다.
# 사용: wifi_wpa_conf_render_canonical <source> <destination> <common-freqs>
# - 전역 update_config=0 및 (목록이 있으면) 전역 freq_list 1개
# - 각 network 블록에 같은 freq_list 1개
# - 모든 network 블록의 legacy active scan_freq 제거
# 설치/권한/롤백은 호출자 책임이라 wifi.sh와 OPC가 같은 변환을 각자 트랜잭션에 넣을 수 있다.
wifi_wpa_conf_render_canonical() {
    local source="$1" destination="$2" freqs="${3-}"
    [ -f "$source" ] || return 1
    [ "$source" != "$destination" ] || return 1

    awk -v freqs="$freqs" '
        function emit_global_policy() {
            if (global_emitted) return
            print "update_config=0"
            if (freqs != "") print "freq_list=" freqs
            global_emitted = 1
        }
        /^[[:space:]]*#/ { print; next }
        !in_net && /^[[:space:]]*update_config[[:space:]]*=/ { next }
        !in_net && /^[[:space:]]*freq_list[[:space:]]*=/ { next }
        /^[[:space:]]*network[[:space:]]*=[[:space:]]*\{/ {
            emit_global_policy()
            in_net = 1
            blocks++
            print
            next
        }
        in_net && /^[[:space:]]*scan_freq[[:space:]]*=/ { next }
        in_net && /^[[:space:]]*freq_list[[:space:]]*=/ { next }
        in_net && /^[[:space:]]*\}/ {
            if (freqs != "") print "    freq_list=" freqs
            in_net = 0
            print
            next
        }
        { print }
        END {
            if (blocks == 0 || in_net) exit 1
        }
    ' "$source" > "$destination"
}

# 원본 conf의 소유권을 임시파일에 먼저 적용하고 mode를 마지막에 복원한다.
# chown은 setuid/setgid bit를 지울 수 있으므로 chmod 순서가 뒤여야 mode도 정확하다.
wifi_wpa_conf_preserve_metadata() {
    local source="$1" destination="$2"
    chown --reference="$source" "$destination" 2>/dev/null || return 1
    chmod --reference="$source" "$destination" 2>/dev/null || return 1
}

# Checked same-directory publication for every boot topology transform.
# Content must already be fully rendered in $1; metadata is copied from the
# installed destination before staged sync and atomic rename.
wifi_wpa_conf_atomic_install() {
    local staged="$1" destination="$2" destination_dir staged_dir
    [ -f "$staged" ] || return 1
    [ -f "$destination" ] || return 1
    destination_dir=${destination%/*}
    staged_dir=${staged%/*}
    [ "$destination_dir" = "$staged_dir" ] || return 1
    wifi_wpa_conf_preserve_metadata "$destination" "$staged" || return 1
    wifi_sync_path_or_global "$staged" || return 1
    mv -f "$staged" "$destination" || return 1
    wifi_sync_path_or_global "$destination" || return 1
    wifi_sync_path_or_global "$destination_dir" || return 1
}

# 배포/사용 중 conf를 같은 파일시스템에서 원자적으로 canonical 형식으로 정규화한다.
# 파일 부재는 부팅 복원 경로와의 호환을 위해 no-op 성공, 파싱/설치 실패는 nonzero다.
wifi_wpa_conf_normalize_file() {
    local conf="$1" freqs tmp
    [ -f "$conf" ] || return 0

    freqs=$(wifi_wpa_conf_common_freqs "$conf") || return 1
    tmp=$(mktemp "${conf}.normalize.XXXXXX") || return 1

    if ! wifi_wpa_conf_render_canonical "$conf" "$tmp" "$freqs"; then
        rm -f "$tmp"
        return 1
    fi

    if cmp -s "$tmp" "$conf"; then
        rm -f "$tmp"
        return 0
    fi
    if ! wifi_wpa_conf_atomic_install "$tmp" "$conf"; then
        rm -f "$tmp"
        return 1
    fi
    return 0
}

# wpa_supplicant conf의 canonical 공통 주파수에 등장하는 밴드 집합을 출력.
# 출력: ""(제한 없음/파일 없음), "2G", "5G", "2G 5G".
# wifi.sh(radio-apply exit 11 가드)와 wifi_init.sh(부팅 가드) 공용.
# 부팅 정규화 전 legacy conf도 wifi_wpa_conf_common_freqs의 우선순위로 해석한다.
# Note: 6GHz(5925-7125MHz)는 미분류 — 현 칩(9098/IW612)이 6GHz 미지원.
#       6E 칩 도입 시 분류 추가 필요.
wifi_init_conf_freq_bands() {
    local conf="$1" f freqs has2="" has5=""
    [ -f "$conf" ] || return 0
    freqs=$(wifi_wpa_conf_common_freqs "$conf") || return 1
    for f in $freqs; do
        # 비숫자 토큰 필터: trailing comment("# ...") 등 word-splitting으로
        # 들어온 잡토큰도 여기서 걸러진다 (의도된 가드)
        case "$f" in
            *[!0-9]*) continue ;;
        esac
        if [ "$f" -ge 2400 ] && [ "$f" -le 2500 ]; then
            has2=1
        elif [ "$f" -ge 4900 ] && [ "$f" -le 5925 ]; then
            has5=1
        fi
    done
    if [ -n "$has2" ] && [ -n "$has5" ]; then
        echo "2G 5G"
    elif [ -n "$has2" ]; then
        echo "2G"
    elif [ -n "$has5" ]; then
        echo "5G"
    fi
}

# === wifi_extra_ssid 자동 network 블록 멱등 생성 ===
# 모드 A(boot snapshot generate_network_blocks=true) + extra_ssids 비어있지 않을 때만,
# 센티넬 주석으로 감싼 자동 network 블록을 conf에 멱등 재생성한다.
# 모드 B/빈 배열 → 기존 자동 블록만 제거(무회귀). 사용자 수동 블록(센티넬 없음)은 보존하되
# runtime 단일-SSID writer는 실제 block 수를 검사해 일괄 mutation을 거부한다.
# 첫(센티넬 밖) network 블록을 템플릿으로 고정 필드(key_mgmt/psk/proto/pairwise/group/
# scan_ssid/freq_list)를 상속하고 ssid만 extra 값으로 바꿔 append. ssid 권위 소스는 json.
WIFI_EXTRA_SSID_BEGIN='# >>> wifi_extra_ssid auto-generated (do not edit) >>>'
WIFI_EXTRA_SSID_END='# <<< wifi_extra_ssid auto-generated <<<'

# 센티넬 구간을 제거한 conf를 stdout으로 출력 (자동 블록만 삭제, 수동 블록 보존)
_wifi_extra_ssid_strip() {
    awk -v b="$WIFI_EXTRA_SSID_BEGIN" -v e="$WIFI_EXTRA_SSID_END" '
        $0 == b { skip = 1; next }
        $0 == e { skip = 0; next }
        skip { next }
        { print }
    ' "$1"
}

# 첫 network 블록에서 키 1개의 값(= 뒤 전체)을 추출. 없으면 빈 문자열.
_wifi_extra_ssid_template_field() {
    awk -v key="$2" '
        /^[[:space:]]*network[[:space:]]*=[[:space:]]*\{/ { if (!seen) { in_net = 1; seen = 1 }; next }
        in_net && /^[[:space:]]*\}/ { in_net = 0 }
        in_net {
            line = $0
            sub(/^[[:space:]]*/, "", line)
            if (match(line, "^" key "[[:space:]]*=")) {
                rest = substr(line, length(key) + 1)
                sub(/^[[:space:]]*=[[:space:]]*/, "", rest)
                print rest
                exit
            }
        }
    ' "$1"
}

wifi_init_sync_extra_ssid_blocks() {
    local iface="$1" conf="$2"
    local conf_json="${WIFI_INIT_CONF_JSON:-/usr/local/etc/wifi_init_conf.json}"
    local policy latch extras_json extras

    [ -f "$conf" ] || return 0
    command -v jq >/dev/null 2>&1 || return 1

    local gen
    policy=$(wifi_roam_policy_path "$iface") || return 1
    latch=$(wifi_roam_policy_latch_path "$iface") || return 1
    if [ -e "$policy" ]; then
        wifi_roam_policy_validate_file "$policy" "$iface" || return 1
        gen=$(jq -r '.generate_network_blocks' "$policy" 2>/dev/null) || return 1
        extras_json=$(jq -c '.extra_ssids' "$policy" 2>/dev/null) || return 1
    else
        # 이 boot에 snapshot을 이미 생성했다면 유실 후 live JSON으로
        # extra block을 재해석하지 않는다(수동 wifi_init 재실행 hot-switch 차단).
        [ ! -e "$latch" ] || return 1
        gen=$(wifi_init_normalize_bool \
            "$(wifi_init_json_read_raw "$conf_json" ".${iface}.roaming.generate_network_blocks")" \
            false) || return 1
        extras_json=$(jq -c --arg iface "$iface" \
            '(.[$iface].roaming.extra_ssids // []) as $raw
             | if ($raw | type) == "array" then $raw else error("extra_ssids") end' \
            "$conf_json" 2>/dev/null) || return 1
    fi
    wifi_ssid_array_validate_json "$extras_json" "$conf" || return 1

    local tmp
    tmp=$(mktemp "${conf}.extra.XXXXXX") || return 1
    local stripfile=""
    # 함수 종료(정상/비정상 모두) 시 임시파일 자동 정리
    trap 'rm -f "$tmp" "$stripfile"' RETURN

    # 모드 B 또는 generate=false → 자동 블록만 제거 후 설치(무회귀: extra 비면 byte 불변)
    if [ "$gen" != "true" ]; then
        _wifi_extra_ssid_strip "$conf" > "$tmp" || return 1
        if ! cmp -s "$tmp" "$conf"; then
            wifi_wpa_conf_atomic_install "$tmp" "$conf" || return 1
        fi
        return 0
    fi

    # 모드 A: extra_ssids 읽기 (각 줄 1 SSID)
    extras=$(printf '%s' "$extras_json" | jq -r '.[]' 2>/dev/null) || return 1

    # extra 없으면 자동 블록만 제거
    if [ -z "$extras" ]; then
        _wifi_extra_ssid_strip "$conf" > "$tmp" || return 1
        if ! cmp -s "$tmp" "$conf"; then
            wifi_wpa_conf_atomic_install "$tmp" "$conf" || return 1
        fi
        return 0
    fi

    # 템플릿 필드 상속 (첫 블록 기준)
    local stripped t_keymgmt t_psk t_proto t_pair t_group t_scanssid t_freqlist
    stripped=$(_wifi_extra_ssid_strip "$conf")
    stripfile=$(mktemp "${conf}.strip.XXXXXX") || return 1
    printf '%s\n' "$stripped" > "$stripfile"

    t_keymgmt=$(_wifi_extra_ssid_template_field "$stripfile" "key_mgmt")
    t_psk=$(_wifi_extra_ssid_template_field "$stripfile" "psk")
    t_proto=$(_wifi_extra_ssid_template_field "$stripfile" "proto")
    t_pair=$(_wifi_extra_ssid_template_field "$stripfile" "pairwise")
    t_group=$(_wifi_extra_ssid_template_field "$stripfile" "group")
    t_scanssid=$(_wifi_extra_ssid_template_field "$stripfile" "scan_ssid")
    t_freqlist=$(_wifi_extra_ssid_template_field "$stripfile" "freq_list")

    # 템플릿 필수 필드(key_mgmt/psk) 부재 → 안전하게 자동 블록 미생성(제거만)
    if [ -z "$t_keymgmt" ] || [ -z "$t_psk" ]; then
        printf '%s\n' "$stripped" > "$tmp"
        if ! cmp -s "$tmp" "$conf"; then
            wifi_wpa_conf_atomic_install "$tmp" "$conf" || return 1
        fi
        return 0
    fi

    # 자동 블록 본문 생성 (센티넬 밖 conf + 센티넬 구간)
    {
        cat "$stripfile"
        printf '%s\n' "$WIFI_EXTRA_SSID_BEGIN"
        local ssid ssid_hex
        while IFS= read -r ssid; do
            ssid_hex=$(wifi_ssid_to_hex "$ssid") || return 1
            printf 'network={\n'
            printf '    ssid=%s\n' "$ssid_hex"
            printf '    key_mgmt=%s\n' "$t_keymgmt"
            printf '    psk=%s\n' "$t_psk"
            [ -n "$t_proto" ]    && printf '    proto=%s\n' "$t_proto"
            [ -n "$t_pair" ]     && printf '    pairwise=%s\n' "$t_pair"
            [ -n "$t_group" ]    && printf '    group=%s\n' "$t_group"
            [ -n "$t_scanssid" ] && printf '    scan_ssid=%s\n' "$t_scanssid"
            [ -n "$t_freqlist" ] && printf '    freq_list=%s\n' "$t_freqlist"
            printf '    priority=0\n'
            printf '}\n'
        done <<EOF
$extras
EOF
        printf '%s\n' "$WIFI_EXTRA_SSID_END"
    } > "$tmp"

    # 문법 sanity: 전체 { 와 } 균형 + 최소 1개 network 블록 존재
    # opens/closes는 network 블록 한정이 아닌 전체 중괄호 집계.
    # 이렇게 해야 cred={...} 등 다른 블록이 있어도 정상 conf를 오판하지 않는다.
    local opens closes net_blocks
    opens=$(grep -c '{' "$tmp" 2>/dev/null || echo 0)
    closes=$(grep -c '}' "$tmp" 2>/dev/null || echo 0)
    net_blocks=$(grep -c '^[[:space:]]*network[[:space:]]*=[[:space:]]*{' "$tmp" 2>/dev/null || echo 0)
    if [ "$net_blocks" -lt 1 ] || [ "$opens" != "$closes" ]; then
        return 1
    fi

    if ! cmp -s "$tmp" "$conf"; then
        wifi_wpa_conf_atomic_install "$tmp" "$conf" || return 1
    fi
    return 0
}
