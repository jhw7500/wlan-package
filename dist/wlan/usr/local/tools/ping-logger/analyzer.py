#!/usr/bin/env python3
"""미전달 패킷 분석 — tshark로 pcap 비교"""

import subprocess
import sys
from typing import Dict, List, Tuple


def _run_tshark(pcap_path: str) -> List[Dict]:
    """tshark로 pcap에서 ICMP 패킷 추출. 각 패킷을 dict로 반환."""
    cmd = [
        "tshark", "-r", pcap_path, "-n", "-T", "fields",
        "-e", "frame.time_epoch",
        "-e", "ip.src",
        "-e", "ip.dst",
        "-e", "icmp.type",
        "-e", "icmp.ident",
        "-e", "icmp.seq",
        "-E", "separator=,",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        print(f"[ERROR] tshark 실행 실패: {result.stderr[:200]}", file=sys.stderr)
        return []

    packets = []
    for line in result.stdout.splitlines():
        fields = line.strip().split(",")
        if len(fields) < 6:
            continue
        packets.append({
            "epoch": fields[0],
            "src": fields[1],
            "dst": fields[2],
            "type": fields[3],
            "id": fields[4],
            "seq": fields[5],
        })
    return packets


def _make_key(pkt: Dict) -> Tuple[str, str, str]:
    """매칭 키: (type, id, seq)"""
    return (pkt["type"], pkt["id"], pkt["seq"])


def _type_label(icmp_type: str) -> str:
    return {"8": "REQ", "0": "REP"}.get(icmp_type, icmp_type)


def analyze_undelivered(pcap1: str, pcap2: str,
                        if1: str, if2: str) -> str:
    """
    두 pcap을 비교하여 미전달 패킷 분석.
    주의: 키(type_id_seq) 중복 시 마지막 값만 유지됨 (ping-monitor.sh와 동일 제한).
    """
    pkts1 = _run_tshark(pcap1)
    pkts2 = _run_tshark(pcap2)

    if not pkts1 and not pkts2:
        return "캡처된 ICMP 패킷 없음"

    # dict 매칭
    map1 = {_make_key(p): p for p in pkts1}
    map2 = {_make_key(p): p for p in pkts2}

    keys1 = set(map1.keys())
    keys2 = set(map2.keys())

    matched_keys = keys1 & keys2
    only1_keys = keys1 - keys2
    only2_keys = keys2 - keys1

    matched = len(matched_keys)
    only1 = len(only1_keys)
    only2 = len(only2_keys)

    # 지연 계산
    delays = []
    for key in matched_keys:
        try:
            t1 = float(map1[key]["epoch"])
            t2 = float(map2[key]["epoch"])
            delays.append(abs(t2 - t1) * 1000)  # ms
        except (ValueError, KeyError):
            pass

    lines = []
    lines.append("")
    lines.append("=== 미전달 패킷 분석 ===")
    lines.append(f"캡처: {if1}={len(pkts1)}, {if2}={len(pkts2)} 패킷")
    lines.append(f"매칭: {matched}, {if1}에만={only1}, {if2}에만={only2}")

    total_uniq = matched + only1 + only2
    if total_uniq > 0:
        loss_pct = ((only1 + only2) / total_uniq) * 100
        lines.append(f"손실률: {loss_pct:.1f}%")

    if delays:
        avg_d = sum(delays) / len(delays)
        lines.append("")
        lines.append(f"브릿지 지연: 평균={avg_d:.3f}ms 최소={min(delays):.3f}ms 최대={max(delays):.3f}ms")

    if only1_keys:
        lines.append("")
        lines.append(f"[{if1}에서 전달되지 않음 → {if2}]")
        for key in sorted(only1_keys):
            p = map1[key]
            lines.append(f"  {_type_label(p['type'])} id={p['id']} seq={p['seq']} "
                         f"{p['src']}→{p['dst']} t={p['epoch']}")

    if only2_keys:
        lines.append("")
        lines.append(f"[{if2}에서 전달되지 않음 → {if1}]")
        for key in sorted(only2_keys):
            p = map2[key]
            lines.append(f"  {_type_label(p['type'])} id={p['id']} seq={p['seq']} "
                         f"{p['src']}→{p['dst']} t={p['epoch']}")

    if not only1_keys and not only2_keys:
        lines.append("")
        lines.append("모든 패킷이 정상 전달되었습니다.")

    return "\n".join(lines)
