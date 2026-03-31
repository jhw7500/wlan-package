#!/usr/bin/env python3
"""
WLAN Pcap 종합 분석 도구

사용법:
    python3 pcap_analyzer.py <pcap_file> [옵션]

옵션:
    --ssid SSID          WPA 복호화용 SSID
    --pass PASSPHRASE    WPA 복호화용 비밀번호
    --start "YYYY-MM-DD HH:MM:SS"  시작 시간 필터
    --end "YYYY-MM-DD HH:MM:SS"    종료 시간 필터
    --mac MAC1[,MAC2]    MAC 주소 필터 (TA/RA 둘 다 매칭, 콤마 구분)
    --ip  IP1[,IP2]      IP 주소 필터 (src/dst 둘 다 매칭, 콤마 구분)
    --sta MAC            특정 STA만 집중 분석 (--mac의 alias)
    --compare-ap         AP 간 로밍 성능 비교 포함
    --brief              진단/Loss/로밍영향만 출력 (현장용 간결 모드)
    --with-log FILE...   외부 로그 병합 (wpa.log, kerl.log 등)
    -o OUTPUT_FILE       출력 파일 (기본: <pcap이름>_analysis.txt)
"""
import argparse
import json
import os
import sys
from datetime import datetime

from extractor import extract_frames
from detector import detect_roles
from indexer import FrameIndex
from reporter import format_report
from analyzers import overview, retry_mcs, retry_burst, roaming, ping_rtt
from analyzers import control_traffic, signal_quality, per_second
from analyzers import roaming_impact, ping_loss, diagnosis, compare_ap
from log_merger import merge_logs


def main():
    parser = argparse.ArgumentParser(description="WLAN Pcap 종합 분석 도구")
    parser.add_argument("pcap", help="분석할 pcap 파일 경로")
    parser.add_argument("--config", default="", help="네트워크 설정 JSON 파일 경로")
    parser.add_argument("--ssid", default="", help="WPA 복호화용 SSID (config보다 우선)")
    parser.add_argument("--pass", dest="passphrase", default="", help="WPA 복호화용 비밀번호 (config보다 우선)")
    parser.add_argument("--start", default="", help="시작 시간 필터")
    parser.add_argument("--end", default="", help="종료 시간 필터")
    parser.add_argument("--mac", default="", help="MAC 필터 (콤마 구분)")
    parser.add_argument("--ip", default="", help="IP 필터 (콤마 구분)")
    parser.add_argument("--sta", default="", help="특정 STA MAC만 집중 분석")
    parser.add_argument("--compare-ap", action="store_true", help="AP 간 로밍 성능 비교")
    parser.add_argument("--brief", action="store_true", help="진단 요약만 출력 (현장용)")
    parser.add_argument("--with-log", nargs="+", default=[], metavar="FILE",
                       help="외부 로그 병합 (wpa.log 등)")
    parser.add_argument("-o", "--output", default="", help="출력 파일 경로")
    args = parser.parse_args()

    if not os.path.exists(args.pcap):
        print(f"[ERROR] 파일이 존재하지 않습니다: {args.pcap}", file=sys.stderr)
        sys.exit(1)

    # JSON 설정 로드 → CLI 인자로 덮어쓰기
    config_path = args.config
    if not config_path:
        # pcap과 같은 디렉토리 또는 현재 디렉토리에서 자동 탐색
        for candidate in [
            os.path.join(os.path.dirname(args.pcap), "network.json"),
            "network.json",
        ]:
            if os.path.exists(candidate):
                config_path = candidate
                break

    if config_path and os.path.exists(config_path):
        with open(config_path) as cf:
            config = json.load(cf)
        print(f"  설정 파일 로드: {config_path}")
        if not args.ssid:
            args.ssid = config.get("ssid", "")
        if not args.passphrase:
            args.passphrase = config.get("passphrase", config.get("pass", ""))

    # --sta는 --mac의 alias
    if args.sta:
        args.mac = args.sta

    if not args.output:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        tmp_dir = os.path.join(script_dir, "tmp")
        os.makedirs(tmp_dir, exist_ok=True)
        base = os.path.splitext(os.path.basename(args.pcap))[0]
        date_str = datetime.now().strftime("%Y%m%d")
        args.output = os.path.join(tmp_dir, f"{base}_analysis_{date_str}.txt")

    wpa_used = bool(args.ssid and args.passphrase)

    # 1. 프레임 추출
    print(f"[1/4] tshark로 프레임 추출 중... ({os.path.basename(args.pcap)})")
    if args.mac:
        print(f"  MAC 필터: {args.mac}")
    if args.ip:
        print(f"  IP 필터: {args.ip}")
    frames = extract_frames(
        args.pcap,
        wpa_passphrase=args.passphrase,
        ssid=args.ssid,
        time_start=args.start,
        time_end=args.end,
        mac_filter=args.mac,
        ip_filter=args.ip,
    )
    if not frames:
        print("[ERROR] 프레임을 추출하지 못했습니다.", file=sys.stderr)
        sys.exit(1)
    print(f"  -> {len(frames):,}프레임 추출 완료")

    # 2. MAC 역할 감지
    print("[2/4] MAC 역할 감지 중...")
    roles = detect_roles(frames)
    aps = [m for m, r in roles.items() if r["role"] == "AP"]
    stas = [m for m, r in roles.items() if r["role"] == "STA"]
    print(f"  -> AP {len(aps)}대, STA {len(stas)}대 감지")

    # 3. 인덱스 구축 + 분석 모듈 실행
    print("[3/4] 프레임 인덱싱 + 분석 모듈 실행 중...")
    index = FrameIndex(frames, roles)
    analyzer_list = [
        ("개요", overview),
        ("Retry MCS", retry_mcs),
        ("Retry Burst", retry_burst),
        ("로밍", roaming),
        ("Ping RTT", ping_rtt),
        ("제어 트래픽", control_traffic),
        ("신호 품질", signal_quality),
        ("초당 통계", per_second),
        ("로밍 영향", roaming_impact),
        ("Ping Loss", ping_loss),
        ("종합 진단", diagnosis),
    ]

    if args.compare_ap:
        analyzer_list.append(("AP 비교", compare_ap))

    sections = []
    for name, mod in analyzer_list:
        print(f"  -> {name} 분석...")
        sections.append(mod.analyze(frames, roles, index))

    # 외부 로그 병합
    if args.with_log:
        print(f"  -> 외부 로그 병합... ({len(args.with_log)}파일)")
        sections.append(merge_logs(args.with_log))

    # 4. 리포트 생성
    print(f"[4/4] 리포트 생성 중... -> {args.output}")
    report = format_report(sections, args.pcap, wpa_used, brief=args.brief)

    with open(args.output, "w") as f:
        f.write(report)

    print(f"\n완료! 리포트 저장: {args.output}")
    print(f"파일 크기: {os.path.getsize(args.output):,} bytes")

    print("\n--- 요약 ---")
    for sec in sections:
        print(f"  [{sec.title}] {sec.summary}")


if __name__ == "__main__":
    main()
