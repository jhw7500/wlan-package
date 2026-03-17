"""tshark를 실행하여 pcap에서 프레임 데이터를 추출한다."""
import subprocess
import sys
from typing import List, Optional
from models import Frame

TSHARK_FIELDS = [
    "frame.number", "frame.time_epoch", "frame.time",
    "wlan.fc.retry", "wlan.fc.type_subtype",
    "_ws.col.Protocol", "frame.len",
    "radiotap.mcs.index", "radiotap.dbm_antsignal",
    "wlan.ta", "wlan.ra",
    "ip.src", "ip.dst",
    "icmp.type", "arp.opcode", "tcp.len", "tcp.flags",
    "wlan.seq",
]


def build_tshark_cmd(
    pcap_path: str,
    wpa_passphrase: str = "",
    ssid: str = "",
    time_start: str = "",
    time_end: str = "",
    mac_filter: str = "",
    ip_filter: str = "",
) -> List[str]:
    cmd = ["tshark", "-r", pcap_path, "-T", "fields"]
    for field in TSHARK_FIELDS:
        cmd.extend(["-e", field])

    if wpa_passphrase and ssid:
        wpa_key = f"{wpa_passphrase}:{ssid}"
        cmd.extend([
            "-o", "wlan.enable_decryption:TRUE",
            "-o", f'uat:80211_keys:"wpa-pwd","{wpa_key}"',
        ])

    filters = []
    if time_start:
        filters.append(f'frame.time >= "{time_start}"')
    if time_end:
        filters.append(f'frame.time < "{time_end}"')
    if mac_filter:
        # wlan.addr는 TA/RA 모두 매칭
        mac_parts = [f'wlan.addr == {m.strip()}' for m in mac_filter.split(",")]
        filters.append(f'({" || ".join(mac_parts)})')
    if ip_filter:
        ip_parts = [f'ip.addr == {ip.strip()}' for ip in ip_filter.split(",")]
        filters.append(f'({" || ".join(ip_parts)})')
    if filters:
        cmd.extend(["-Y", " && ".join(filters)])

    return cmd


def parse_tsv_line(line: str) -> Optional[Frame]:
    cols = line.strip().split("\t")
    expected = len(TSHARK_FIELDS)
    if len(cols) < 7:
        return None
    while len(cols) < expected:
        cols.append("")

    try:
        return Frame(
            number=int(cols[0]),
            epoch=float(cols[1]),
            timestamp=cols[2],
            retry=cols[3] == "1",
            subtype=cols[4],
            protocol=cols[5],
            length=int(cols[6]) if cols[6] else 0,
            mcs=cols[7],
            rssi=cols[8],
            ta=cols[9],
            ra=cols[10],
            ip_src=cols[11],
            ip_dst=cols[12],
            icmp_type=cols[13],
            arp_opcode=cols[14],
            tcp_len=cols[15],
            tcp_flags=cols[16],
            seq=cols[17] if len(cols) > 17 else "",
        )
    except (ValueError, IndexError):
        return None


def extract_frames(
    pcap_path: str,
    wpa_passphrase: str = "",
    ssid: str = "",
    time_start: str = "",
    time_end: str = "",
    mac_filter: str = "",
    ip_filter: str = "",
) -> List[Frame]:
    cmd = build_tshark_cmd(pcap_path, wpa_passphrase, ssid, time_start, time_end,
                           mac_filter, ip_filter)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    if result.returncode != 0:
        print(f"[ERROR] tshark 실행 실패: {result.stderr[:500]}", file=sys.stderr)
        return []

    frames = []
    for line in result.stdout.splitlines():
        frame = parse_tsv_line(line)
        if frame is not None:
            frames.append(frame)

    return frames
