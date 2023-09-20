#!/bin/bash
tag=$(basename "$0")
IFACE=$1
NEW_MAC=$2

if [[ "$IFACE" == "eth0" ]]; then
    LINK_FILE="/etc/systemd/network/22-eth0.link"
elif [[ "$IFACE" == "mlan0" ]]; then
    LINK_FILE="/etc/systemd/network/20-mlan0.link"
elif [[ "$IFACE" == "mlan1" ]]; then
    LINK_FILE="/etc/systemd/network/21-mlan1.link"
else
    logger -p local0.emerg "[$tag:$LINENO] [$IFACE] interface is wrong!!"
    exit 0
fi

if ! [[ "$NEW_MAC" =~ ^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$ ]]; then
    logger -p local0.err "[$tag:$LINENO] [$IFACE] invalid MAC format: $NEW_MAC, keeping original"
    exit 0
fi

# 백업 생성
cp "$LINK_FILE" "${LINK_FILE}.bak.$(date +%Y%m%d%H%M%S)"

# 기존 MACAddress 라인을 찾아서 교체
if grep -q "^MACAddress=" "$LINK_FILE"; then
    sed -i "s/^MACAddress=.*/MACAddress=${NEW_MAC}/" "$LINK_FILE"
else
    # MACAddress 항목이 없으면 [Link] 섹션 끝에 추가
    awk -v mac="$NEW_MAC" '
        /^\[Link\]/ { print; inlink=1; next }
        inlink && /^\[/ { print "MACAddress="mac; inlink=0 }
        { print }
        END { if (inlink) print "MACAddress="mac }
    ' "$LINK_FILE" > "${LINK_FILE}.tmp" && mv "${LINK_FILE}.tmp" "$LINK_FILE"
fi

logger -p local0.info "[$tag:$LINENO] [$IFACE] Updated MACAddress to $NEW_MAC in $LINK_FILE"
