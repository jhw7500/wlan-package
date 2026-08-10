#!/bin/bash
# systemd .link MAC 검증/중복 확인 공용 함수.

mac_normalize() {
    printf '%s\n' "${1,,}"
}

# 형식뿐 아니라 실제 인터페이스에 할당 가능한 unicast MAC인지 확인한다.
# locally-administered 주소는 허용하고 zero/broadcast/multicast는 거부한다.
mac_is_assignable() {
    local mac first last_nibble
    mac=$(mac_normalize "${1:-}")
    [[ "$mac" =~ ^([0-9a-f]{2}:){5}[0-9a-f]{2}$ ]] || return 1
    [ "$mac" != "00:00:00:00:00:00" ] || return 1
    [ "$mac" != "ff:ff:ff:ff:ff:ff" ] || return 1

    first="${mac%%:*}"
    last_nibble="${first:1:1}"
    case "$last_nibble" in
      0|2|4|6|8|a|c|e) return 0 ;;
      *) return 1 ;;
    esac
}

mac_read_link_address() {
    local link_file="$1"
    [ -f "$link_file" ] || return 1
    awk '
        /^[[:space:]]*\[[^]]+\]/ {
            in_link = ($0 ~ /^[[:space:]]*\[Link\][[:space:]]*([#;].*)?$/)
            next
        }
        in_link && /^[[:space:]]*MACAddress[[:space:]]*=/ {
            value = $0
            sub(/^[[:space:]]*MACAddress[[:space:]]*=[[:space:]]*/, "", value)
            sub(/[[:space:]#;].*$/, "", value)
            value = tolower(value)
        }
        END {
            if (value != "") print value
        }
    ' "$link_file" 2>/dev/null
}

mac_render_link_with_address() {
    local link_file="$1" mac="$2"
    awk -v mac="$mac" '
        /^[[:space:]]*\[[^]]+\]/ {
            if (in_link && !written) {
                print "MACAddress=" mac
                written = 1
            }
            in_link = ($0 ~ /^[[:space:]]*\[Link\][[:space:]]*([#;].*)?$/)
            print
            next
        }
        in_link && /^[[:space:]]*MACAddress[[:space:]]*=/ {
            if (!written) {
                print "MACAddress=" mac
                written = 1
            }
            next
        }
        { print }
        END {
            if (in_link && !written) print "MACAddress=" mac
        }
    ' "$link_file"
}

# [Match] 섹션의 OriginalName 값을 출력한다. 없으면 빈 출력(=파일명만으로는 대상 판정 불가).
mac_read_link_original_name() {
    local link_file="$1"
    [ -f "$link_file" ] || return 1
    awk '
        /^[[:space:]]*\[[^]]+\]/ {
            in_match = ($0 ~ /^[[:space:]]*\[Match\][[:space:]]*([#;].*)?$/)
            next
        }
        in_match && /^[[:space:]]*OriginalName[[:space:]]*=/ {
            sub(/^[[:space:]]*OriginalName[[:space:]]*=[[:space:]]*/, "")
            sub(/[[:space:]]*$/, "")
            if (value == "") value = $0
        }
        END {
            if (value != "") print value
        }
    ' "$link_file" 2>/dev/null
}

# [Match] 섹션의 OriginalName 줄 수. systemd가 반복 지정을 병합하는지 덮어쓰는지에
# 기대지 않기 위해, 2줄 이상이면 호출자가 판정 불가로 다룬다.
mac_count_link_original_name() {
    local link_file="$1"
    [ -f "$link_file" ] || { printf '0\n'; return 0; }
    awk '
        /^[[:space:]]*\[[^]]+\]/ {
            in_match = ($0 ~ /^[[:space:]]*\[Match\][[:space:]]*([#;].*)?$/)
            next
        }
        in_match && /^[[:space:]]*OriginalName[[:space:]]*=/ { n++ }
        END { print n + 0 }
    ' "$link_file" 2>/dev/null
}

# 이 .link의 매칭 대상을 OriginalName만으로 판정할 수 없으면 0(참)을 반환한다.
# 세 경우다. ① OriginalName이 아예 없다 — Path/Driver/Type 등 다른 조건은 여기서 해석하지
# 않는다. ② 값에 부정(!) 패턴이 있다 — systemd는 '!' 접두 패턴을 "이것만 제외"로 해석하므로
# 단순 glob 매칭 결과가 의미상 뒤집힌다(OriginalName=!mlan0 은 mlan0을 뺀 전부와 매칭).
# ③ OriginalName 줄이 여러 개다 — 병합/덮어쓰기 중 무엇인지 추측하면 과소 탐지(거짓 안심)나
# 과잉 삭제 중 하나로 틀린다.
# 호출자는 판정 불가를 "매칭 안 함"으로 취급하지 말고 보수적으로 처리해야 한다.
mac_link_match_undecidable() {
    local link_file="$1" names token
    [ "$(mac_count_link_original_name "$link_file")" -le 1 ] || return 0
    names=$(mac_read_link_original_name "$link_file") || return 0
    [ -n "$names" ] || return 0
    for token in $names; do
        case "$token" in
            !*) return 0 ;;
        esac
    done
    return 1
}

# .link의 OriginalName이 해당 인터페이스를 지목하는지. systemd는 공백 구분 목록과 glob을
# 허용하므로(예: "mlan0 mlan1", "mlan*") 토큰별로 glob 매칭한다.
# 판정 불가(OriginalName 없음 / 부정 패턴)면 1을 반환한다 — 호출자는
# mac_link_match_undecidable로 두 경우를 구분해야 한다.
mac_link_matches_iface() {
    local link_file="$1" iface="$2" names token
    mac_link_match_undecidable "$link_file" && return 1
    names=$(mac_read_link_original_name "$link_file") || return 1
    for token in $names; do
        # shellcheck disable=SC2254  # glob 매칭이 목적이라 의도적으로 비인용
        case "$iface" in
            $token) return 0 ;;
        esac
    done
    return 1
}

# [Link] 섹션의 MACAddress 라인만 제거한다 (mac_render_link_with_address의 역방향).
# [Match]의 MACAddress는 인터페이스 선택 조건이므로 보존한다. 결과 .link는 패키지 템플릿과
# 같은 "MAC 미지정" 상태가 되어 드라이버 기본 MAC(permaddr)이 그대로 쓰인다.
mac_render_link_without_address() {
    local link_file="$1"
    awk '
        /^[[:space:]]*\[[^]]+\]/ {
            in_link = ($0 ~ /^[[:space:]]*\[Link\][[:space:]]*([#;].*)?$/)
            print
            next
        }
        in_link && /^[[:space:]]*MACAddress[[:space:]]*=/ { next }
        { print }
    ' "$link_file"
}

mac_acquire_global_lock() {
    local network_dir="$1" lock_dir lock_file

    if [ -n "${MAC_LINK_LOCK_DIR:-}" ]; then
        lock_dir="$MAC_LINK_LOCK_DIR"
    elif [ "$network_dir" = "/etc/systemd/network" ]; then
        lock_dir="/run/lock"
    else
        lock_dir="$network_dir"
    fi
    lock_file="$lock_dir/wlan-mac-global.lock"

    # wifi_init.sh가 전체 최종 계획에 대해 이미 획득한 같은 락은 자식
    # update_mac.sh가 fd 9와 함께 상속한다. 환경 변수만 위조했거나 다른
    # network_dir의 락이면 재사용하지 않는다.
    if [ "${MAC_LINK_LOCK_HELD:-}" = "1" ] \
        && [ "${MAC_LINK_LOCK_FILE:-}" = "$lock_file" ] \
        && { : >&9; } 2>/dev/null; then
        return 0
    fi

    mkdir -p "$lock_dir" || return 1
    exec 9>"$lock_file" || return 1
    flock 9 || return 1
    export MAC_LINK_LOCK_HELD=1
    export MAC_LINK_LOCK_FILE="$lock_file"
}

mac_release_global_lock() {
    if [ "${MAC_LINK_LOCK_HELD:-}" != "1" ] || ! { : >&9; } 2>/dev/null; then
        return 0
    fi
    flock -u 9 || return 1
    exec 9>&-
    unset MAC_LINK_LOCK_HELD MAC_LINK_LOCK_FILE
}

# 패키지가 관리하는 .link 파일만 정확한 인터페이스에 매핑한다.
# 임의 파일명의 인터페이스 문자열은 신뢰하지 않아 운영자 파일은 항상 충돌 검사에 남긴다.
mac_owned_link_iface() {
    local link_file="$1"
    case "${link_file##*/}" in
      20-mlan0.link) printf 'mlan0\n' ;;
      21-mlan1.link) printf 'mlan1\n' ;;
      22-eth0.link)  printf 'eth0\n' ;;
      *) return 1 ;;
    esac
}

mac_plan_value() {
    local wanted_iface="$1" entry iface value
    shift

    for entry in "$@"; do
        iface="${entry%%=*}"
        [ "$iface" != "$entry" ] || continue
        [ "$iface" = "$wanted_iface" ] || continue
        value="${entry#*=}"
        [ -n "$value" ] || return 1
        mac_normalize "$value"
        return 0
    done
    return 1
}

# 다른 활성 *.link가 같은 MACAddress를 설정하면 해당 파일 경로를 출력한다.
# 단, 패키지 소유 인터페이스가 전달된 최종 계획에서 다른 MAC으로 이동할 예정이면
# 현재 주소는 일시적인 점유이므로 충돌에서 제외한다.
mac_find_link_conflict() {
    local network_dir="$1" own_link="$2" requested="$3"
    local link_file configured owner_iface planned
    shift 3
    requested=$(mac_normalize "$requested")

    for link_file in "$network_dir"/*.link; do
        [ -f "$link_file" ] || continue
        [ "$link_file" = "$own_link" ] && continue
        configured=$(mac_read_link_address "$link_file")
        [ -n "$configured" ] || continue
        if [ "$configured" = "$requested" ]; then
            owner_iface=$(mac_owned_link_iface "$link_file" || true)
            planned=""
            if [ -n "$owner_iface" ]; then
                planned=$(mac_plan_value "$owner_iface" "$@" || true)
            fi
            if [ -n "$planned" ] \
                && mac_is_assignable "$planned" \
                && [ "$planned" != "$requested" ]; then
                continue
            fi
            printf '%s\n' "$link_file"
            return 0
        fi
    done
    return 1
}
