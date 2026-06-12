#!/bin/bash

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
    local overlay_json="${JSON_FILE:-/usr/local/etc/config.json}"
    local query=".${iface}.enabled"

    if wifi_init_json_key_exists "$conf_json" "$query"; then
        value=$(wifi_init_json_read_raw "$conf_json" "$query")
    fi

    if wifi_init_json_key_exists "$overlay_json" "$query"; then
        value=$(wifi_init_json_read_raw "$overlay_json" "$query")
    fi

    wifi_init_normalize_bool "$value" "$default"
}

wifi_init_get_iface_frequency() {
    local iface="$1"
    local default="${2:-auto}"
    local value="$default"
    local conf_json="${WIFI_INIT_CONF_JSON:-/usr/local/etc/wifi_init_conf.json}"
    local overlay_json="${JSON_FILE:-/usr/local/etc/config.json}"
    local query=".${iface}.Frequency"

    if wifi_init_json_key_exists "$conf_json" "$query"; then
        value=$(wifi_init_json_read_raw "$conf_json" "$query")
    fi

    if wifi_init_json_key_exists "$overlay_json" "$query"; then
        value=$(wifi_init_json_read_raw "$overlay_json" "$query")
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
wifi_init_conf_freq_bands() {
    local conf="$1" f has2="" has5=""
    [ -f "$conf" ] || return 0
    for f in $(sed -n -e 's/^[[:space:]]*freq_list[[:space:]]*=//p' \
                      -e 's/^[[:space:]]*scan_freq[[:space:]]*=//p' "$conf"); do
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
