#!/bin/bash
tag=$(basename "$0")
IFACE=$1

logger -p local0.info "[$tag:$LINENO] [$IFACE] arping_sweep start"

if [[ "$IFACE" != "mlan0" && "$IFACE" != "mlan1" && "$IFACE" != "eth0" ]]; then
    logger -p local0.err "[$tag:$LINENO] [$IFACE] interface is wrong!!"
    exit 1
fi

CIDR=$(ip -4 addr show "mlan0" | grep -oP '(?<=inet\s)\d+(\.\d+){3}/\d+')
if [[ -z "$CIDR" ]]; then
    logger -p local0.err "[$tag:$LINENO] [$IFACE] has no IP address"
    exit 1
fi

# ipcalc 결과 파싱
#eval $(ipcalc "$CIDR" | grep -E 'HostMin|HostMax' | sed 's/ //g' | tr ':' '=')
HostMin=$(ipcalc "$CIDR" | grep 'HostMin' | awk '{print $2}')
HostMax=$(ipcalc "$CIDR" | grep 'HostMax' | awk '{print $2}')

ip_to_int() {
    IFS=. read -r a b c d <<< "$1"
    echo $(( (a << 24) + (b << 16) + (c << 8) + d ))
}

int_to_ip() {
    local ip=$1
    printf "%d.%d.%d.%d\n" \
        $(( (ip >> 24) & 0xFF )) \
        $(( (ip >> 16) & 0xFF )) \
        $(( (ip >> 8) & 0xFF )) \
        $(( ip & 0xFF ))
}

START=$(ip_to_int "$HostMin")
END=$(ip_to_int "$HostMax")

# 병렬 arping 함수
arping_ping() {
    ip=$1
    arping -I "$IFACE" -c 1 -w 1 "$ip" > /dev/null 2>&1
}

# 병렬 처리 (xargs 없이)
PIDS=()
for (( ip = START; ip <= END; ip++ )); do
    TARGET=$(int_to_ip "$ip")
    arping_ping "$TARGET" &
    PIDS+=($!)
    # 50개 병렬 제한
    if (( ${#PIDS[@]} >= 50 )); then
        wait -n  # 하나 끝날 때까지 대기
        # 제거
        for i in "${!PIDS[@]}"; do
            if ! kill -0 "${PIDS[$i]}" 2>/dev/null; then
                unset 'PIDS[i]'
            fi
        done
    fi
done

# 남은 것들 기다리기
wait

# 결과 출력
NEW_ARP=$(ip neigh show dev "$IFACE" | grep -v FAILED)
logger -p local0.info "[$tag:$LINENO] [$IFACE] NEW_ARP : $NEW_ARP"
