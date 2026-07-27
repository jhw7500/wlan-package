#!/usr/bin/env python3
import subprocess
import logging
import time
import os
import signal
from datetime import datetime, timedelta, timezone
from sUTILS import Logger, _EXTRA_
import sys

VERSION = "0.2"
LOG_DIR = "/var/log/cantops/mgmt"
INTERFACE = "rtap"             # 모니터 인터페이스
CAP_FILENAME = "mgmt.log"
BROADCAST_MAC = "ff:ff:ff:ff:ff:ff"
IFACE = "mlan0"
# SUBTYPE_MASK: 관리 프레임(wlan.fc.type==0) subtype 제외(exclude) 마스크. argv[2]로 덮어씀.
# bit N = subtype N. 비트가 1이면 해당 프레임을 로그에서 제외한다.
#   bit  0 (0x0001) Assoc Request      bit  8 (0x0100) Beacon
#   bit  1 (0x0002) Assoc Response     bit  9 (0x0200) ATIM
#   bit  2 (0x0004) Reassoc Request    bit 10 (0x0400) Disassoc
#   bit  3 (0x0008) Reassoc Response   bit 11 (0x0800) Auth
#   bit  4 (0x0010) Probe Request      bit 12 (0x1000) Deauth
#   bit  5 (0x0020) Probe Response     bit 13 (0x2000) Action
#   bit  6 (0x0040) Timing Advertisement  bit 14 (0x4000) Action No Ack
#   bit  7 (0x0080) (Reserved)
# 제외 처리 계층: tcpdump BPF(TCPDUMP_SUPPORTED_SUBTYPES에 있는 subtype)
#   → tshark 디스플레이 필터(나머지) → 파이썬 루프 최종 가드.
# 예) 서비스 기본값 0x4100 = Beacon + Action No Ack 제외.
SUBTYPE_MASK = 0
TSHARK_COUNT = 100000          # int 로 두는 게 깔끔

logger = None
parse_proc = None
cap_proc = None
stop_flag = False


def handle_sigterm(signum, frame):
    global stop_flag
    stop_flag = True
    logger.message('crit', f"[{IFACE}] SIGTERM {signum} received", _EXTRA_())
    cleanup()
    sys.exit(0)


def cleanup():
    global parse_proc, cap_proc
    # tshark 정리
    logger.message('crit', f"[{IFACE}] cleanup", _EXTRA_())
    if parse_proc and parse_proc.poll() is None:
        try:
            parse_proc.terminate()
        except Exception:
            pass
    # tcpdump 정리
    if cap_proc and cap_proc.poll() is None:
        try:
            cap_proc.terminate()
        except Exception:
            pass

    # 잠깐 기다렸다가 살아있으면 kill
    time.sleep(0.2)
    for p in (parse_proc, cap_proc):
        if p and p.poll() is None:
            try:
                p.kill()
            except Exception:
                pass

    # netmon off
    subprocess.run(["mlanutl_silent", IFACE, "netmon", "0"], check=False)

def get_mac_address(iface):
    path = f"/sys/class/net/{iface}/address"
    if os.path.exists(path):
        with open(path) as f:
            return f.read().strip().lower()
    return ""

TCPDUMP_SUPPORTED_SUBTYPES = {
    0: "assoc-req",
    1: "assoc-resp",
    2: "reassoc-req",
    3: "reassoc-resp",
    4: "probe-req",
    5: "probe-resp",
    8: "beacon",
    10: "disassoc",
    11: "auth",
    12: "deauth",
    # 13: "action"  # NG면 빼고
    # 14: "action-no-ack" # NG면 없음
}

def parse_frame_type(ftype, fsub):
    if ftype == "0":
        return {
            "0":  "Assoc Request",
            "1":  "Assoc Response",
            "2":  "Reassoc Request",
            "3":  "Reassoc Response",
            "4":  "Probe Request",
            "5":  "Probe Response",
            "8":  "Beacon",
            "9":  "ATIM",
            "10": "Disassoc",
            "11": "Auth",
            "12": "Deauth",
            "13": "Action",
            "14": "Action No Ack",
        }.get(fsub, "Mgmt")
    elif ftype == "1":
        return {"11": "RTS", "12": "CTS", "13": "ACK"}.get(fsub, "Ctrl")
    elif ftype == "2":
        return {"0": "Data", "4": "Null", "8": "QoS Data", "12": "QoS Null"}.get(fsub, "Data")
    return "Unknown"

def build_tcpdump_bpf(mask: int) -> list:
    expr = ["type", "mgt"]  # 기본은 관리 프레임만

    first = True
    for num, name in TCPDUMP_SUPPORTED_SUBTYPES.items():
        if mask & (1 << num):
            # 첫 번째 제외 조건 앞에만 "and" 붙이고, 이후는 계속 "and not..."
            if first:
                expr += ["and"]
                first = False
            else:
                expr += ["and"]
            expr += ["not", "subtype", name]

    return expr

def build_tshark_dpf(mask: int) -> str:
    # 기본: 관리 프레임만
    conds = ["wlan.fc.type == 0"]

    excluded_nums = []
    for subtype in range(0, 16):
        # mask에 포함되어 있지만, tcpdump에서 이미 처리한 건 제외
        if mask & (1 << subtype) and subtype not in TCPDUMP_SUPPORTED_SUBTYPES:
            excluded_nums.append(subtype)

    if excluded_nums:
        conds.append("!(" + " || ".join(
            f"wlan.fc.subtype == {n}" for n in excluded_nums
        ) + ")")

    return " && ".join(conds)

def start_parser(mac_mlan, subtype_mask):
    """
    tcpdump -i rtap -U -n -s 128 type mgt -w - | tshark -l -r - -n -c N -T fields ...
    를 계속 반복해서 돌리는 루프.
    """
    global parse_proc, cap_proc, stop_flag

    logger.no_extra()
    logger.message('info', f"[{IFACE}] capture loop start (mask=0x{subtype_mask:04x})", _EXTRA_())
    while not stop_flag:
        # 1) tcpdump 시작 (관리 프레임만)
        bpf = build_tcpdump_bpf(SUBTYPE_MASK)
        logger.message('info', f"[{IFACE}] tcpdump bypass filter : {bpf}", _EXTRA_())
        cap_proc = subprocess.Popen(
            ["tcpdump", "-i", INTERFACE, "-U", "-n", "-s", "128", "-w", "-"] + bpf,
            #["tcpdump", "-i", INTERFACE, "-U", "-n", "-s", "128", "-w", "-", "type", "mgt", "and", "not", "subtype", "beacon"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

        #time.sleep(0.5)
        #if cap_proc.poll() is not None:
        #    err = cap_proc.stderr.read().strip()
        #    logger.message('err', f"[{IFACE}] exited {cap_proc.returncode}, stderr={err}", _EXTRA_())

        # 2) tshark 시작 (stdin 으로 pcap 스트림 받기)
        dpf = build_tshark_dpf(SUBTYPE_MASK)
        logger.message('info', f"[{IFACE}] tshark display filter : {dpf}", _EXTRA_())
        parse_proc = subprocess.Popen(
            [
                "tshark",
                "-l",
                "-r", "-",
                "-n",
                "-c", str(TSHARK_COUNT),
                "-T", "fields",
                "-e", "frame.time_epoch",
                "-e", "wlan.sa",
                "-e", "wlan.da",
                "-e", "wlan.fc.type",
                "-e", "wlan.fc.subtype",
                "-e", "wlan.fc.retry",
                "-e", "wlan.seq",
                "-e", "radiotap.dbm_antsignal",
                "-e", "radiotap.dbm_antnoise",
                "-E", "separator=,",
                "-o", "tcp.desegment_tcp_streams:FALSE",
                "-o", "tls.desegment_ssl_records:FALSE",
                "-Y", dpf,
            ],
            stdin=cap_proc.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        # 부모 쪽에선 더 이상 이 파이프 안 쓰므로 닫음
        cap_proc.stdout.close()

        # 3) tshark stdout 한 줄씩 읽어서 처리
        for line in parse_proc.stdout:
            if stop_flag:
                break

            fields = line.strip().split(",")
            if len(fields) < 9:
                continue

            ts, sa, da = fields[0].strip(), fields[1].lower(), fields[2].lower()
            ftype, fsub, retry, seq, rssi, nf = fields[3:9]

            if ftype != "0":
                continue

            try:
                subtype = int(fsub)
            except ValueError:
                continue

            # subtype_mask 비트가 1인 subtype은 스킵
            if subtype_mask & (1 << subtype):
                continue

            snr = "N/A"
            try:
                snr = str(int(rssi) - int(nf))
            except Exception:
                pass

            frame_str = parse_frame_type(ftype, fsub)
            logger.message(
                'debug',
                f"{frame_str:<16}({fsub:>2}) : SA={sa:<17} DA={da:<17} "
                f"RSSI={rssi:>4} NF={nf:>4} SNR={snr:>3} Retry={retry:<3} Seq={seq}",
            )

        # 4) 여기까지 왔다는 건 tshark가 -c N 다 채우고 끝났거나,
        #    에러/stop_flag 로 루프를 빠져나온 상황
        try:
            logger.message('crit', f"[{IFACE}] wait", _EXTRA_())
            parse_proc.wait(timeout=1)
        except Exception:
            pass

        if cap_proc and cap_proc.poll() is None:
            try:
                logger.message('crit', f"[{IFACE}] terminate", _EXTRA_())
                cap_proc.terminate()
            except Exception:
                pass

        if stop_flag:
            logger.message('crit', f"[{IFACE}] flag", _EXTRA_())
            break

        # 재시작 사이에 약간 쉬어주기
        #time.sleep(0.2)

    # 전체 루프 끝 → 정리
    cleanup()


def main():
    while not os.path.exists(f"/sys/class/net/{IFACE}"):
        logger.message('info', f"[{IFACE}] waiting for interface...", _EXTRA_())
        time.sleep(1)

    mac_mlan = get_mac_address(IFACE)
    logger.message('info', f"[{IFACE}] MAC: {mac_mlan}", _EXTRA_())

    # netmon 세팅
    subprocess.run(["mlanutl_silent", IFACE, "netmon", "0"], check=False)
    time.sleep(0.2)
    subprocess.run(["mlanutl_silent", IFACE, "netmon", "1", "0x49"], check=False)
    subprocess.run(["ifconfig", INTERFACE, "up"], check=False)

    try:
        start_parser(mac_mlan, SUBTYPE_MASK)
    except KeyboardInterrupt:
        logger.message('warn', f"[{IFACE}] KeyboardInterrupt", _EXTRA_())
        cleanup()


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, handle_sigterm)

    # 처음에는 local0에 한 번 찍고
    logger = Logger(app_name='MGMT', facility=logging.handlers.SysLogHandler.LOG_LOCAL2)

    IFACE = sys.argv[1] if len(sys.argv) > 1 else "mlan0"
    if IFACE not in ["mlan0", "mlan1"]:
        logger.message("emerg", f"Invalid interface {IFACE}", _EXTRA_())
        sys.exit(1)

    SUBTYPE_MASK = int(sys.argv[2], 0) if len(sys.argv) > 2 else 0x0000

    LOG_DIR = f"/var/log/cantops/mgmt/{IFACE}"
    os.makedirs(f"{LOG_DIR}/tmp", exist_ok=True)

    logger.message(
        "info",
        f"[{IFACE}] version : {VERSION}, subtype_mask : 0x{SUBTYPE_MASK:04x}, log_file : {LOG_DIR}/{CAP_FILENAME}",
        _EXTRA_(),
    )

    # 이후부턴 local2 로 태그 분리해서 쓰고 싶으면 이거 유지
    #logger = Logger(app_name='logger_cap', facility.logging.handlers.SysLogHandler.LOG_LOCAL2)

    main()
