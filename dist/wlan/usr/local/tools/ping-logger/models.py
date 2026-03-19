#!/usr/bin/env python3
"""ping-logger 데이터 모델"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class PacketInfo:
    """파싱된 ICMP 패킷 정보"""
    iface: str
    timestamp: str
    src: str
    dst: str
    icmp_type: str          # REQ, REP, UNREACH, TIMEXC, unknown
    seq: Optional[str] = None
    length: Optional[str] = None

    @property
    def type_label(self) -> str:
        labels = {"REQ": "Echo", "REP": "Reply", "UNREACH": "Unreach", "TIMEXC": "TimeExc"}
        return labels.get(self.icmp_type, self.icmp_type)


@dataclass
class SessionConfig:
    """세션 설정 (JSON + CLI 병합 결과)"""
    mode: str = "dual"              # single | dual
    primary: str = "eth0"
    secondary: str = "mlan0"
    target_ip: str = ""
    duration: int = 0               # 0 = Ctrl+C까지
    output_dir: str = "/tmp/ping-logger"
    save_pcap: bool = True
    show_timestamp: bool = True
    show_seq: bool = True
    show_size: bool = False
    color: bool = True
    analysis_on_exit: bool = True
    remote_host: str = ""

    @classmethod
    def from_json(cls, data: dict) -> "SessionConfig":
        """JSON dict에서 SessionConfig 생성 (없는 키는 기본값 유지)"""
        defaults = cls()  # 기본값 인스턴스
        ifaces = data.get("interfaces", {})
        filt = data.get("filter", {})
        cap = data.get("capture", {})
        out = data.get("output", {})
        disp = data.get("display", {})
        ana = data.get("analysis", {})

        return cls(
            mode=ifaces.get("mode", defaults.mode),
            primary=ifaces.get("primary", defaults.primary),
            secondary=ifaces.get("secondary", defaults.secondary),
            target_ip=filt.get("target_ip", defaults.target_ip),
            duration=int(cap.get("duration", defaults.duration)),
            output_dir=out.get("dir", defaults.output_dir),
            save_pcap=bool(out.get("save_pcap", defaults.save_pcap)),
            show_timestamp=bool(disp.get("show_timestamp", defaults.show_timestamp)),
            show_seq=bool(disp.get("show_seq", defaults.show_seq)),
            show_size=bool(disp.get("show_size", defaults.show_size)),
            color=bool(disp.get("color", defaults.color)),
            analysis_on_exit=bool(ana.get("on_exit", defaults.analysis_on_exit)),
        )
