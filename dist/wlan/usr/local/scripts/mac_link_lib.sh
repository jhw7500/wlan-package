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

mac_acquire_iface_lock() {
    local network_dir="$1" iface="$2" lock_dir lock_file
    if [ -n "${MAC_LINK_LOCK_DIR:-}" ]; then
        lock_dir="$MAC_LINK_LOCK_DIR"
    elif [ "$network_dir" = "/etc/systemd/network" ]; then
        lock_dir="/run/lock"
    else
        lock_dir="$network_dir"
    fi
    mkdir -p "$lock_dir" || return 1
    lock_file="$lock_dir/wlan-mac-${iface}.lock"
    exec 9>"$lock_file" || return 1
    flock 9
}

# 다른 활성 *.link가 같은 MACAddress를 설정하면 해당 파일 경로를 출력한다.
mac_find_link_conflict() {
    local network_dir="$1" own_link="$2" requested="$3"
    local link_file configured
    requested=$(mac_normalize "$requested")

    for link_file in "$network_dir"/*.link; do
        [ -f "$link_file" ] || continue
        [ "$link_file" = "$own_link" ] && continue
        configured=$(mac_read_link_address "$link_file")
        [ -n "$configured" ] || continue
        if [ "$configured" = "$requested" ]; then
            printf '%s\n' "$link_file"
            return 0
        fi
    done
    return 1
}
