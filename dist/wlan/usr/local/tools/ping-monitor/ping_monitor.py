#!/usr/bin/env python3
"""
ping-monitor — 유무선 브릿지 ICMP 실시간 로거

클라이언트 모드에서 tcpdump를 사용하여 ping 패킷을 실시간 로깅합니다.
1개 또는 2개 인터페이스 동시 캡처를 지원하며, 브릿지 동작에 영향이 없습니다.

사용법:
  ping_monitor.py                                # 듀얼 모드 (eth0 + mlan0)
  ping_monitor.py -s -1 mlan0                    # mlan0만 캡처
  ping_monitor.py -t 192.168.1.1 -d 60           # 특정 IP, 60초
  ping_monitor.py -c /path/to/config.json        # 커스텀 설정 사용
  ping_monitor.py -H 10.0.0.100                  # 원격 실행
"""

import argparse
import ipaddress
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime

from models import SessionConfig
from capture import CaptureEngine, COLORS, NO_COLORS
from analyzer import analyze_undelivered


def load_config(path: str) -> SessionConfig:
    """JSON 파일에서 SessionConfig 로드"""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        print(f"INFO: 설정 로드: {path}")
        return SessionConfig.from_json(data)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[ERROR] 설정 파일 로드 실패: {e}", file=sys.stderr)
        sys.exit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="유무선 브릿지 ICMP 실시간 로거",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="CLI 옵션이 JSON 설정보다 우선합니다.",
    )
    parser.add_argument("-c", "--config", metavar="FILE", help="JSON 설정 파일 경로")
    parser.add_argument("-1", "--primary", metavar="IFACE", help="첫 번째 인터페이스 (기본: eth0)")
    parser.add_argument("-2", "--secondary", metavar="IFACE", help="두 번째 인터페이스 (기본: mlan0)")
    parser.add_argument("-s", "--single", action="store_true", help="단일 인터페이스 모드")
    parser.add_argument("-t", "--target", metavar="IP", help="특정 IP만 필터")
    parser.add_argument("-d", "--duration", type=int, metavar="SEC", help="캡처 시간 (초)")
    parser.add_argument("-o", "--output-dir", metavar="DIR", help="출력 디렉토리")
    parser.add_argument("-l", "--log-file", metavar="FILE", help="고정 로그 파일 경로 (데몬 모드)")
    parser.add_argument("-P", "--no-pcap", action="store_true", help="pcap 저장 비활성화")
    parser.add_argument("-H", "--remote", metavar="HOST", help="원격 타겟 호스트 (SSH)")
    return parser.parse_args()


def build_config(args: argparse.Namespace) -> SessionConfig:
    """JSON 설정 + CLI 인자 병합 (CLI 우선)"""
    # 1. JSON 설정 로드
    if args.config:
        cfg = load_config(args.config)
    else:
        default_conf = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "ping-monitor.conf.json")
        if os.path.exists(default_conf):
            cfg = load_config(default_conf)
        else:
            cfg = SessionConfig()

    # 2. CLI 인자로 덮어쓰기
    if args.primary:
        cfg.primary = args.primary
    if args.secondary:
        cfg.secondary = args.secondary
    if args.single:
        cfg.mode = "single"
    if args.target:
        cfg.target_ip = args.target
    if args.duration is not None:
        cfg.duration = args.duration
    if args.output_dir:
        cfg.output_dir = args.output_dir
    if args.log_file:
        cfg.log_file = args.log_file
    if args.no_pcap:
        cfg.save_pcap = False
    if args.remote:
        cfg.remote_host = args.remote

    return cfg


def check_prerequisites(cfg: SessionConfig) -> None:
    """전제 조건 확인"""
    if os.geteuid() != 0:
        print("[ERROR] root 권한이 필요합니다", file=sys.stderr)
        sys.exit(1)

    if not shutil.which("tcpdump"):
        print("[ERROR] 'tcpdump'이(가) 설치되어 있지 않습니다", file=sys.stderr)
        sys.exit(1)

    # 인터페이스 존재 확인
    for iface in ([cfg.primary] if cfg.mode == "single" else [cfg.primary, cfg.secondary]):
        ret = subprocess.run(["ip", "link", "show", iface],
                             capture_output=True, timeout=5)
        if ret.returncode != 0:
            print(f"[ERROR] 인터페이스 없음: {iface}", file=sys.stderr)
            sys.exit(1)

    if cfg.target_ip:
        try:
            ipaddress.ip_address(cfg.target_ip)
        except ValueError:
            print(f"[ERROR] 올바른 IP 주소가 아닙니다: {cfg.target_ip}", file=sys.stderr)
            sys.exit(1)

    if cfg.mode == "dual" and cfg.analysis_on_exit and not shutil.which("tshark"):
        print("WARN: 'tshark' 없음, 종료 시 미전달 분석 불가", file=sys.stderr)


def exec_remote(cfg: SessionConfig, raw_args: list) -> None:
    """원격 호스트에서 실행"""
    host = cfg.remote_host
    print(f"INFO: 원격 실행: {host}")

    script_dir = os.path.dirname(os.path.abspath(__file__))
    files_to_copy = ["ping_monitor.py", "capture.py", "analyzer.py", "models.py",
                     "ping-monitor.conf.json"]

    # scp로 파일 전송
    remote_dir = "/tmp/ping-monitor"
    subprocess.run(["ssh", f"root@{host}", f"mkdir -p {remote_dir}"],
                   check=True, timeout=10)
    for fname in files_to_copy:
        local = os.path.join(script_dir, fname)
        if os.path.exists(local):
            subprocess.run(["scp", "-q", local, f"root@{host}:{remote_dir}/"],
                           check=True, timeout=30)

    # -H 제거한 인자 재구성
    remote_args = []
    skip_next = False
    for arg in raw_args:
        if skip_next:
            skip_next = False
            continue
        if arg in ("-H", "--remote"):
            skip_next = True
            continue
        remote_args.append(arg)

    cmd = f"cd {shlex.quote(remote_dir)} && python3 ping_monitor.py {' '.join(shlex.quote(a) for a in remote_args)}"
    ret = subprocess.run(["ssh", "-t", f"root@{host}", cmd])

    # 결과 복사
    print("INFO: 결과 파일 복사 중...")
    os.makedirs(cfg.output_dir, exist_ok=True)
    subprocess.run(
        ["scp", "-q", f"root@{host}:{cfg.output_dir}/ping_*.log", f"{cfg.output_dir}/"],
        check=False, timeout=30,
    )
    subprocess.run(
        ["scp", "-q", f"root@{host}:{cfg.output_dir}/icmp_*.pcap", f"{cfg.output_dir}/"],
        check=False, timeout=30,
    )
    subprocess.run(
        ["ssh", f"root@{host}", f"rm -rf {remote_dir} {cfg.output_dir}"],
        check=False, timeout=10,
    )

    sys.exit(ret.returncode)


def main() -> None:
    args = parse_args()
    cfg = build_config(args)

    # 원격 실행
    if cfg.remote_host:
        exec_remote(cfg, sys.argv[1:])
        return

    check_prerequisites(cfg)

    colors = COLORS if (cfg.color and sys.stdout.isatty()) else NO_COLORS

    # 출력 디렉토리 + 파일 준비
    os.makedirs(cfg.output_dir, exist_ok=True)
    session_ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    if cfg.log_file:
        # 고정 로그 경로 (데몬 모드) — append
        log_path = cfg.log_file
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        log_open_mode = "a"
    else:
        # 타임스탬프 로그 파일 (일회성 실행)
        log_path = os.path.join(cfg.output_dir, f"ping_{session_ts}.log")
        log_open_mode = "w"

    pcap1_path = os.path.join(cfg.output_dir, f"icmp_{cfg.primary}_{session_ts}.pcap")
    pcap2_path = ""
    if cfg.mode == "dual":
        pcap2_path = os.path.join(cfg.output_dir, f"icmp_{cfg.secondary}_{session_ts}.pcap")

    log_file = open(log_path, log_open_mode, encoding="utf-8")
    try:
        _run_session(cfg, log_file, log_path, pcap1_path, pcap2_path, colors)
    finally:
        if not log_file.closed:
            log_file.close()


def _run_session(cfg, log_file, log_path, pcap1_path, pcap2_path, colors) -> None:
    """실제 캡처 세션 실행 (log_file은 호출자가 닫음)"""
    # 세션 헤더
    header_lines = [
        "=== ping-monitor 세션 시작 ===",
        f"시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"모드: {cfg.mode}",
        f"인터페이스: {cfg.primary}",
    ]
    if cfg.mode == "dual":
        header_lines.append(f"인터페이스2: {cfg.secondary}")
    if cfg.target_ip:
        header_lines.append(f"필터: {cfg.target_ip}")
    header_lines.append("===")
    header_lines.append("")

    for line in header_lines:
        print(line)
        log_file.write(line + "\n")
    log_file.flush()

    print("INFO: 캡처 시작...")
    if cfg.duration > 0:
        print(f"INFO: 시간: {cfg.duration}초")
    print("INFO: 종료: Ctrl+C")
    print("")

    # 캡처 엔진 시작
    engine = CaptureEngine(cfg, log_file, colors)

    if cfg.mode == "dual":
        ifaces = [cfg.primary, cfg.secondary]
        pcap_paths = {cfg.primary: pcap1_path, cfg.secondary: pcap2_path}
    else:
        ifaces = [cfg.primary]
        pcap_paths = {cfg.primary: pcap1_path}

    engine.start(ifaces, pcap_paths if cfg.save_pcap else None)

    # 종료 처리 (재진입 방지)
    shutdown_called = False

    def shutdown(signum=None, frame=None):
        nonlocal shutdown_called
        if shutdown_called:
            return
        shutdown_called = True

        print("")
        print("INFO: 캡처 종료...")

        engine.stop()
        time.sleep(0.5)

        # 세션 종료 로그
        end_line = f"\n=== 세션 종료: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ==="
        log_file.write(end_line + "\n")

        # 미전달 분석
        if (cfg.mode == "dual" and cfg.analysis_on_exit and cfg.save_pcap
                and shutil.which("tshark")
                and os.path.exists(pcap1_path) and os.path.exists(pcap2_path)):
            analysis = analyze_undelivered(pcap1_path, pcap2_path,
                                           cfg.primary, cfg.secondary)
            print(analysis)
            log_file.write(analysis + "\n")

        # 결과 요약
        print("")
        print("INFO: === 결과 ===")
        if os.path.exists(log_path):
            with open(log_path, encoding="utf-8") as f:
                line_count = sum(1 for _ in f)
            print(f"INFO: 로그: {log_path} ({line_count} 줄)")
        if cfg.save_pcap:
            for p in [pcap1_path, pcap2_path]:
                if os.path.exists(p):
                    size = os.path.getsize(p)
                    print(f"INFO: pcap: {p} ({size // 1024}K)")

        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # 대기
    if cfg.duration > 0:
        time.sleep(cfg.duration)
        shutdown()
    else:
        signal.pause()


if __name__ == "__main__":
    main()
