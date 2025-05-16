#!/bin/bash
#LOCKFILE="/tmp/logger.lock"
#exec 200>$LOCKFILE
#flock -n 200 || exit 1

#ip link set mlan0 address
getMac() {
    IFACE=$1

    if [ -e /sys/class/net/$IFACE/address ]; then
        mac_addr=$(cat /sys/class/net/$IFACE/address)
        logger -p local0.notice -t tshark "$IFACE MAC Address: $mac_addr"
        echo "$mac_addr"
    else
        logger -p local0.crit -t tshark "Interface $IFACE not found"
        echo ""
    fi
}


mac_mlan=$(getMac mlan0)
mac_rtap=$(getMac rtap)
mlanutl mlan0 netmon 1 0x41
ifconfig rtap up

# 내 MAC 주소
#my_mac=$(ip link show mlan0 | awk '/ether/ {print $2}')
#echo "mlan0 MAC Address: $MAC_ADDR"
logger -p local0.notice -t tshark "mlan0 MAC Address: $mac_mlan"
#my_mac="00:50:43:02:fe:01"
mac_bc="ff:ff:ff:ff:ff:ff"

# 무선 인터페이스 (예: rtap, mon0 등)
iface="rtap"

tshark -l -i "$iface" -T fields \
  -e wlan.sa \
  -e wlan.da \
  -e wlan.fc.type \
  -e wlan.fc.subtype \
  -e wlan.fc.retry \
  -e wlan.seq \
  -e radiotap.dbm_antsignal \
  -e radiotap.dbm_antnoise \
| awk -v mac_mlan="$mac_mlan" -v mac_bc="$mac_bc" 'BEGIN {
  mac_mlan = tolower(mac_mlan)
}
{
  sa = ($1 == "" ? "N/A" : $1)
  da = ($2 == "" ? "N/A" : $2)
  ftype = ($3 == "" ? "N/A" : $3)
  fsub = ($4 == "" ? "N/A" : $4)
  retry = ($5 == "" ? "N/A" : $5)
  seq = ($6 == "" ? "N/A" : $6)
  signal = ($7 == "" ? "N/A" : $7)
  noise = ($8 == "" ? "N/A" : $8)

  sa_lc = tolower(sa)
  da_lc = tolower(da)

  if (!(da_lc == mac_mlan || sa_lc == mac_mlan || da_lc == mac_bc)) #(da_lc == mac_bc)
  {
    next
  }

  # 송수신 방향 판별
  dir = (da_lc == mac_mlan || da_lc == mac_bc) ? "RX" :
        (sa_lc == mac_mlan ? "TX" : "??")

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

  printf("[WIFI] %s RSSI=%s NF=%s SNR=%s dB SA=%s DA=%s Retry=%s Seq=%s Frame=%s\n",
         dir, signal, noise, snr, sa, da, retry, seq, frame_str)
  fflush()
}' | while read line; do
  logger -p local0.notice -t tshark "$line"
done
