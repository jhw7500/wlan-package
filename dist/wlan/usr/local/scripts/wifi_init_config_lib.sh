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

# wpa_supplicant conf의 freq_list/scan_freq에 등장하는 밴드 집합을 출력.
# 출력: ""(제한 없음/파일 없음), "2G", "5G", "2G 5G".
# wifi.sh(radio-apply exit 11 가드)와 wifi_init.sh(부팅 가드) 공용.
# Note: 6GHz(5925-7125MHz)는 미분류 — 현 칩(9098/IW612)이 6GHz 미지원.
#       6E 칩 도입 시 분류 추가 필요.
wifi_init_conf_freq_bands() {
    local conf="$1" f has2="" has5=""
    [ -f "$conf" ] || return 0
    for f in $(sed -n -e 's/^[[:space:]]*freq_list[[:space:]]*=//p' \
                      -e 's/^[[:space:]]*scan_freq[[:space:]]*=//p' "$conf"); do
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
# 모드 A(roaming.generate_network_blocks=true) + extra_ssids 비어있지 않을 때만,
# 센티넬 주석으로 감싼 자동 network 블록을 conf에 멱등 재생성한다.
# 모드 B/빈 배열/jq 부재 → 기존 자동 블록만 제거(무회귀). 사용자 수동 블록(센티넬 없음)은 보존.
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

    [ -f "$conf" ] || return 0
    command -v jq >/dev/null 2>&1 || return 0

    local gen
    gen=$(wifi_init_normalize_bool \
        "$(wifi_init_json_read_raw "$conf_json" ".${iface}.roaming.generate_network_blocks")" \
        false)

    local tmp
    tmp=$(mktemp "${conf}.extra.XXXXXX") || return 1
    local stripfile=""
    # 함수 종료(정상/비정상 모두) 시 임시파일 자동 정리
    trap 'rm -f "$tmp" "$stripfile"' RETURN

    # 모드 B 또는 generate=false → 자동 블록만 제거 후 설치(무회귀: extra 비면 byte 불변)
    if [ "$gen" != "true" ]; then
        _wifi_extra_ssid_strip "$conf" > "$tmp" || return 1
        if ! cmp -s "$tmp" "$conf"; then
            chmod --reference="$conf" "$tmp" 2>/dev/null || chmod 0600 "$tmp" 2>/dev/null || true
            mv -f "$tmp" "$conf" && sync "$conf" 2>/dev/null
        fi
        return 0
    fi

    # 모드 A: extra_ssids 읽기 (각 줄 1 SSID)
    local extras
    extras=$(wifi_init_json_read_raw "$conf_json" ".${iface}.roaming.extra_ssids[]?")

    # extra 없으면 자동 블록만 제거
    if [ -z "$extras" ]; then
        _wifi_extra_ssid_strip "$conf" > "$tmp" || return 1
        if ! cmp -s "$tmp" "$conf"; then
            chmod --reference="$conf" "$tmp" 2>/dev/null || chmod 0600 "$tmp" 2>/dev/null || true
            mv -f "$tmp" "$conf" && sync "$conf" 2>/dev/null
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
            chmod --reference="$conf" "$tmp" 2>/dev/null || chmod 0600 "$tmp" 2>/dev/null || true
            mv -f "$tmp" "$conf" && sync "$conf" 2>/dev/null
        fi
        return 0
    fi

    # 자동 블록 본문 생성 (센티넬 밖 conf + 센티넬 구간)
    {
        cat "$stripfile"
        printf '%s\n' "$WIFI_EXTRA_SSID_BEGIN"
        local ssid esc
        while IFS= read -r ssid; do
            [ -z "$ssid" ] && continue
            # ssid 내 \와 " 이스케이프 (conf C-style 문법)
            esc=$(printf '%s' "$ssid" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')
            printf 'network={\n'
            printf '    ssid="%s"\n' "$esc"
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
        chmod --reference="$conf" "$tmp" 2>/dev/null || chmod 0600 "$tmp" 2>/dev/null || true
        mv -f "$tmp" "$conf" && sync "$conf" 2>/dev/null
    fi
    return 0
}
