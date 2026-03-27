#!/usr/bin/env python3
"""tcpdump 실시간 캡처 + 파싱 + 로깅 엔진"""

import re
import subprocess
import threading
from datetime import date
from typing import Optional, IO

from models import PacketInfo, SessionConfig

# tcpdump 출력 파싱용 정규식
# -i any 형식: 14:23:01.123456 IP 192.168.1.1 > 192.168.1.2: ICMP echo request, ...
# 단일 iface 형식: 14:23:01.123456 IP 192.168.1.1 > 192.168.1.2: ICMP echo request, ...
RE_ICMP = re.compile(r"ICMP", re.IGNORECASE)
RE_SEQ = re.compile(r"seq\s+(\d+)")
RE_LEN = re.compile(r"length\s+(\d+)")
# -i any 출력에서 인터페이스명 추출: "HH:MM:SS.us <iface> In/Out IP ..."
RE_ANY_IFACE = re.compile(r"^\S+\s+(\S+)\s+(?:In|Out|[BP])\s+")

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


def parse_tcpdump_line(line: str, default_iface: str = "",
                       use_any: bool = False) -> Optional[PacketInfo]:
    """tcpdump -l 출력 한 줄을 PacketInfo로 파싱. ICMP가 아니면 None.

    use_any=True: -i any 형식 (인터페이스명이 출력에 포함)
    use_any=False: 단일 인터페이스 형식 (default_iface 사용)
    """
    if not RE_ICMP.search(line):
        return None

    parts = line.split()

    if use_any:
        # -i any 형식: "HH:MM:SS.us <iface> In/Out IP src > dst: ..."
        m = RE_ANY_IFACE.match(line)
        if not m or len(parts) < 7:
            return None
        iface = m.group(1)
        ts = parts[0]
        # "IP" 다음부터 src > dst
        try:
            ip_idx = parts.index("IP")
        except ValueError:
            return None
        src = parts[ip_idx + 1]
        dst = parts[ip_idx + 3].rstrip(":")
    else:
        # 단일 인터페이스 형식: "HH:MM:SS.us IP src > dst: ..."
        if len(parts) < 5:
            return None
        iface = default_iface
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
    """PacketInfo를 플레인텍스트 로그 문자열로 변환 (날짜 포함)"""
    parts = []
    if cfg.show_timestamp:
        parts.append(f"{date.today()} {pkt.timestamp}")
    parts.append(f"[{pkt.iface}]")
    parts.append(pkt.icmp_type)
    parts.append(f"{pkt.src} > {pkt.dst}")
    if cfg.show_seq and pkt.seq:
        parts.append(f"seq={pkt.seq}")
    if cfg.show_size and pkt.length:
        parts.append(f"len={pkt.length}")

    return " ".join(parts)


class CaptureEngine:
    """tcpdump 캡처 엔진. dual 모드에서는 -i any로 단일 프로세스 캡처."""

    def __init__(self, cfg: SessionConfig, log_file: Optional[IO], colors: dict):
        self.cfg = cfg
        self.log_file = log_file
        self.colors = colors
        self.ifaces: list = []
        self.text_proc: Optional[subprocess.Popen] = None
        self.pcap_procs: list = []
        self.thread: Optional[threading.Thread] = None

    def start(self, ifaces: list, pcap_paths: Optional[dict] = None) -> None:
        """캡처 시작

        Args:
            ifaces: 캡처할 인터페이스 목록 (1~2개)
            pcap_paths: {iface: path} pcap 저장 경로 (없으면 저장 안함)
        """
        self.ifaces = ifaces
        bpf = "icmp"
        if self.cfg.target_ip:
            bpf = f"icmp and host {self.cfg.target_ip}"

        # pcap 저장용 tcpdump (인터페이스별 개별 프로세스)
        if pcap_paths and self.cfg.save_pcap:
            for iface, path in pcap_paths.items():
                if path:
                    proc = subprocess.Popen(
                        ["tcpdump", "-i", iface, "-n", "-l",
                         "--time-stamp-precision=micro", "-w", path, bpf],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                    self.pcap_procs.append(proc)

        # 실시간 텍스트용 tcpdump
        use_any = len(ifaces) > 1
        if use_any:
            # dual: -i any로 단일 프로세스, 커널이 타임스탬프 순서 보장
            cmd = ["tcpdump", "-i", "any", "-n", "-l", bpf]
        else:
            cmd = ["tcpdump", "-i", ifaces[0], "-n", "-l", bpf]

        self.text_proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1,
        )

        self.thread = threading.Thread(
            target=self._read_loop, args=(use_any,), daemon=True)
        self.thread.start()

    def _read_loop(self, use_any: bool) -> None:
        """tcpdump stdout을 한 줄씩 읽고 파싱/출력/로그"""
        if not self.text_proc or not self.text_proc.stdout:
            return
        iface_set = set(self.ifaces)
        default_iface = self.ifaces[0] if not use_any else ""

        for line in self.text_proc.stdout:
            line = line.rstrip("\n")
            pkt = parse_tcpdump_line(line, default_iface=default_iface,
                                     use_any=use_any)
            if pkt is None:
                continue
            # -i any는 모든 인터페이스를 캡처하므로 관심 인터페이스만 필터
            if use_any and pkt.iface not in iface_set:
                continue

            display = format_display(pkt, self.cfg, self.colors)
            log_line = format_log(pkt, self.cfg)
            print(display, flush=True)
            if self.log_file:
                self.log_file.write(log_line + "\n")
                self.log_file.flush()

    def stop(self) -> None:
        """모든 프로세스 종료"""
        for proc in [self.text_proc] + self.pcap_procs:
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
