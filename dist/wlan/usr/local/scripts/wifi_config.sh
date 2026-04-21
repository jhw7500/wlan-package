#!/bin/bash
tag=$(basename "$0")
key=LOG
IFACE=$1
logger -p local0.notice "[$tag:$LINENO] [$IFACE] wifi config"

if [[ "$IFACE" == "" ]]; then
    IFACE="mlan0"
fi

if [[ "$IFACE" != "mlan0" && "$IFACE" != "mlan1" ]]; then
    logger -p local0.err "[$tag:$LINENO] [$IFACE] interface $IFACE is wrong!!"
    exit 1
fi

# .network 파일 변경 적용 — networkctl reload 가 지원되면 사용 (eth0/시리얼 보호).
# 실패 시에만 systemd-networkd 전체 재시작으로 fallback (wifi_init.sh 와 동일 정책).
if command -v networkctl >/dev/null 2>&1 && networkctl reload 2>/dev/null; then
    logger -p local0.info "[$tag:$LINENO] [$IFACE] networkctl reload ok (eth0 not disrupted)"
    networkctl reconfigure "$IFACE" 2>/dev/null || true
else
    logger -p local0.warn "[$tag:$LINENO] [$IFACE] networkctl reload unavailable → restart systemd-networkd"
    systemctl restart systemd-networkd
fi
systemctl restart wifi_bridge@$IFACE
systemctl restart wifi_arping@eth0
