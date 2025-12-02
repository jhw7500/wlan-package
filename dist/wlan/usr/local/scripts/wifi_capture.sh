#!/bin/bash
tag=$(basename "$0")
LOCKFILE="/tmp/capture.lock"
exec 200>"$LOCKFILE"
flock -n 200 || exit 1

IFACE=$1
COUNT=10000

logger -p local0.info "[$tag:$LINENO] [$IFACE] start"

if [[ "$IFACE" != "mlan0" && "$IFACE" != "mlan1" ]]; then
    logger -p local0.emerg "[$tag:$LINENO] [$IFACE] interface is wrong!!"
    exit 0
fi

cleanup() {
    logger -p local0.info "[$tag:$LINENO] [$IFACE] stop"
    logger -p local2.info "[$tag:$LINENO] [$IFACE] stop"
    ifconfig rtap down 2>/dev/null
    mlanutl "$IFACE" netmon 0 2>/dev/null
    exit 0
}
trap cleanup INT TERM

getMac() {
    local iface="$1"
    if [ -e "/sys/class/net/$iface/address" ]; then
        cat "/sys/class/net/$iface/address"
    else
        logger -p local0.crit -t tshark "Interface $iface not found"
        logger -p local2.crit -t tshark "Interface $iface not found"
        echo ""
        return 1
    fi
}

SUBTYPE_MASK="$2"
[ -z "$SUBTYPE_MASK" ] && SUBTYPE_MASK=0

MAC_BC="ff:ff:ff:ff:ff:ff"
mac_mlan=$(getMac "$IFACE")

mlanutl "$IFACE" netmon 1 0x49
ifconfig rtap up

while :; do
    logger -p local0.info -t tshark "$IFACE MAC Address: $mac_mlan, subtype_mask: $SUBTYPE_MASK, count: $COUNT"
    logger -p local2.info -t tshark "$IFACE MAC Address: $mac_mlan, subtype_mask: $SUBTYPE_MASK, count: $COUNT"

    tshark -l -i rtap -n -c "$COUNT" -T fields \
        -e wlan.sa \
        -e wlan.da \
        -e wlan.fc.type \
        -e wlan.fc.subtype \
        -e wlan.fc.retry \
        -e wlan.seq \
        -e radiotap.dbm_antsignal \
        -e radiotap.dbm_antnoise 2>/dev/null \
    | gawk -v mac_mlan="$mac_mlan" -v mac_bc="$MAC_BC" -v subtype_mask="$SUBTYPE_MASK" '
        BEGIN {
            mac_mlan     = tolower(mac_mlan)
            mac_bc       = tolower(mac_bc)
            subtype_mask = strtonum(subtype_mask)   # "0x4000" → 숫자
        }
        {
            sa     = ($1 == "" ? "N/A" : $1)
            da     = ($2 == "" ? "N/A" : $2)
            ftype  = ($3 == "" ? -1    : $3) + 0
            fsub   = ($4 == "" ? -1    : $4) + 0
            retry  = ($5 == "" ? "N/A" : $5)
            seq    = ($6 == "" ? "N/A" : $6)
            signal = ($7 == "" ? "N/A" : $7)
            noise  = ($8 == "" ? "N/A" : $8)

            sa_lc = tolower(sa)
            da_lc = tolower(da)

            # 내 MAC 관련 프레임만
            if (da_lc != mac_mlan && sa_lc != mac_mlan)
                next

            # 관리 프레임(ftype==0)이고, subtype_mask가 설정된 경우만 마스크 적용
            if (ftype == 0 && subtype_mask != 0 && fsub >= 0) {
                if (and(subtype_mask, lshift(1, fsub)) != 0) {
                    next
                }
            }

            # 송수신 방향 판별
            if      (da_lc == mac_mlan || da_lc == mac_bc) dir = "RX"
            else if (sa_lc == mac_mlan)                    dir = "TX"
            else                                           dir = "??"

            # SNR 계산
            snr = (signal != "N/A" && noise != "N/A") ? (signal - noise) : "N/A"

            # 프레임 종류 매핑
            frame_str = "Unknown"
            if (ftype == 0) {
                if      (fsub == 0)  frame_str = "Assoc Request"
                else if (fsub == 1)  frame_str = "Assoc Response"
                else if (fsub == 2)  frame_str = "Reassoc Request"
                else if (fsub == 3)  frame_str = "Reassoc Response"
                else if (fsub == 4)  frame_str = "Probe Request"
                else if (fsub == 5)  frame_str = "Probe Response"
                else if (fsub == 8)  frame_str = "Beacon"
                else if (fsub == 9)  frame_str = "ATIM"
                else if (fsub == 10) frame_str = "Disassoc"
                else if (fsub == 11) frame_str = "Auth"
                else if (fsub == 12) frame_str = "Deauth"
                else if (fsub == 13) frame_str = "Action"
                else if (fsub == 14) frame_str = "Action No Ack"
            } else if (ftype == 1) {
                if      (fsub == 11) frame_str = "RTS"
                else if (fsub == 12) frame_str = "CTS"
                else if (fsub == 13) frame_str = "ACK"
            } else if (ftype == 2) {
                if      (fsub == 0)  frame_str = "Data"
                else if (fsub == 4)  frame_str = "Null"
                else if (fsub == 8)  frame_str = "QoS Data"
                else if (fsub == 12) frame_str = "QoS Null"
            }

            msg = sprintf("[%-2s] %-16s(%-2s) : SA=%-17s DA=%-17s RSSI=%-4s NF=%-4s SNR=%-4s Retry=%-5s Seq=%s",
                    dir, frame_str, fsub, sa, da, signal, noise, snr, retry, seq)
            cmd = "logger -p local2.debug -t tshark -- \"" msg "\""
            system(cmd)

            # 한 줄짜리 메시지 생성 (stdout으로 보냄)
            #printf("[%-2s] %-16s(%-2d) : SA=%-17s DA=%-17s RSSI=%-4s NF=%-4s SNR=%-4s Retry=%-5s Seq=%s\n",
            #       dir, frame_str, fsub, sa, da, signal, noise, snr, retry, seq)
        }
        ' \
    #| while IFS= read -r line; do
    #    logger -p local2.debug -t tshark -- "$line"
    #done

done
