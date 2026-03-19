#!/usr/bin/env python3
"""tcpdump 실시간 캡처 + 파싱 + 로깅 엔진"""

import re
import subprocess
import threading
from typing import Optional, IO

from models import PacketInfo, SessionConfig

# tcpdump 출력 파싱용 정규식
# 예: 14:23:01.123456 IP 192.168.1.1 > 192.168.1.2: ICMP echo request, id 1234, seq 1, length 64
RE_ICMP = re.compile(r"ICMP", re.IGNORECASE)
RE_SEQ = re.compile(r"seq\s+(\d+)")
RE_LEN = re.compile(r"length\s+(\d+)")

# ANSI 컬러
COLORS = {
    "reset": "\033[0m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "red": "\033[31m",
    "cyan": "\033[36m",
    "dim": "\033[2m",
}
NO_COLORS = {k: "" for k in COLORS}


def parse_tcpdump_line(iface: str, line: str) -> Optional[PacketInfo]:
    """tcpdump -l 출력 한 줄을 PacketInfo로 파싱. ICMP가 아니면 None."""
    if not RE_ICMP.search(line):
        return None

    parts = line.split()
    if len(parts) < 5:
        return None

    ts = parts[0]
    src = parts[2]
    dst = parts[4].rstrip(":")

    # ICMP 타입 판별
    line_lower = line.lower()
    if "echo request" in line_lower:
        icmp_type = "REQ"
    elif "echo reply" in line_lower:
        icmp_type = "REP"
    elif "unreachable" in line_lower:
        icmp_type = "UNREACH"
    elif "time exceeded" in line_lower:
        icmp_type = "TIMEXC"
    else:
        icmp_type = "unknown"

    seq_m = RE_SEQ.search(line)
    len_m = RE_LEN.search(line)

    return PacketInfo(
        iface=iface,
        timestamp=ts,
        src=src,
        dst=dst,
        icmp_type=icmp_type,
        seq=seq_m.group(1) if seq_m else None,
        length=len_m.group(1) if len_m else None,
    )


def format_display(pkt: PacketInfo, cfg: SessionConfig, colors: dict) -> str:
    """PacketInfo를 컬러 터미널 출력 문자열로 변환"""
    c = colors

    # 인터페이스 컬러
    iface_c = c["cyan"] if pkt.iface == cfg.primary else c["green"]

    # 타입 컬러
    type_colors = {"REQ": c["yellow"], "REP": c["green"], "UNREACH": c["red"], "TIMEXC": c["red"]}
    type_c = type_colors.get(pkt.icmp_type, "")

    parts = []
    if cfg.show_timestamp:
        parts.append(f"{c['dim']}{pkt.timestamp}{c['reset']}")
    parts.append(f"{iface_c}[{pkt.iface}]{c['reset']}")
    parts.append(f"{type_c}{pkt.icmp_type}{c['reset']}")
    parts.append(f"{pkt.src} > {pkt.dst}")
    if cfg.show_seq and pkt.seq:
        parts.append(f"seq={pkt.seq}")
    if cfg.show_size and pkt.length:
        parts.append(f"len={pkt.length}")

    return " ".join(parts)


def format_log(pkt: PacketInfo, cfg: SessionConfig) -> str:
    """PacketInfo를 플레인텍스트 로그 문자열로 변환"""
    parts = []
    if cfg.show_timestamp:
        parts.append(pkt.timestamp)
    parts.append(f"[{pkt.iface}]")
    parts.append(pkt.icmp_type)
    parts.append(f"{pkt.src} > {pkt.dst}")
    if cfg.show_seq and pkt.seq:
        parts.append(f"seq={pkt.seq}")
    if cfg.show_size and pkt.length:
        parts.append(f"len={pkt.length}")

    return " ".join(parts)


class CaptureStream:
    """단일 인터페이스 tcpdump 캡처 스트림"""

    def __init__(self, iface: str, cfg: SessionConfig, log_file: Optional[IO],
                 colors: dict, lock: threading.Lock):
        self.iface = iface
        self.cfg = cfg
        self.log_file = log_file
        self.colors = colors
        self.lock = lock
        self.pcap_proc: Optional[subprocess.Popen] = None
        self.text_proc: Optional[subprocess.Popen] = None
        self.thread: Optional[threading.Thread] = None

    def start(self, pcap_path: Optional[str] = None) -> None:
        """tcpdump 프로세스 시작"""
        bpf = "icmp"
        if self.cfg.target_ip:
            bpf = f"icmp and host {self.cfg.target_ip}"

        # pcap 저장용 tcpdump
        if pcap_path and self.cfg.save_pcap:
            self.pcap_proc = subprocess.Popen(
                ["tcpdump", "-i", self.iface, "-n", "-l",
                 "--time-stamp-precision=micro", "-w", pcap_path, bpf],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )

        # 실시간 텍스트용 tcpdump
        self.text_proc = subprocess.Popen(
            ["tcpdump", "-i", self.iface, "-n", "-l", bpf],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1,
        )

        # 읽기 스레드
        self.thread = threading.Thread(target=self._read_loop, daemon=True)
        self.thread.start()

    def _read_loop(self) -> None:
        """tcpdump stdout을 한 줄씩 읽고 파싱/출력/로그"""
        if not self.text_proc or not self.text_proc.stdout:
            return
        for line in self.text_proc.stdout:
            line = line.rstrip("\n")
            pkt = parse_tcpdump_line(self.iface, line)
            if pkt is None:
                continue

            display = format_display(pkt, self.cfg, self.colors)
            log_line = format_log(pkt, self.cfg)

            with self.lock:
                print(display, flush=True)
                if self.log_file:
                    self.log_file.write(log_line + "\n")
                    self.log_file.flush()

    def stop(self) -> None:
        """프로세스 종료"""
        for proc in [self.text_proc, self.pcap_proc]:
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
