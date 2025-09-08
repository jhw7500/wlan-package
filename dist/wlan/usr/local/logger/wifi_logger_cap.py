import subprocess
import threading
import logging
import time
import os
import signal
from datetime import datetime, timedelta, timezone
from pathlib import Path
from sUTILS import Logger, _EXTRA_
import sys

VERSION = "0.1"
LOG_DIR = "/var/log/cantops/mgmt"
INTERFACE = "rtap"
#PCAP_FILE = f"{LOG_DIR}/tmp/{INTERFACE}.pcap"
PCAP_FILE = "/tmp/live.pcap"
CAP_FILENAME = "mgmt.log"
BROADCAST_MAC = "ff:ff:ff:ff:ff:ff"
IFACE = "mlan0"
SUBTYPE_MASK=0
parse_proc = None

def handle_sigterm(signum, frame):
    #logger.message('crit', f"[{IFACE}] SIGTERM {signum} received! Cleaning up...", _EXTRA_())
    logger.message('crit', "sigterm")
    #print("sigterm")
    cleanup()
    sys.exit(0)

def cleanup():
    if parse_proc:
        parse_proc.terminate()
    subprocess.run(["mlanutl_silent", IFACE, "netmon", "0"])

def get_mac_address(iface):
    path = f"/sys/class/net/{iface}/address"
    if os.path.exists(path):
        with open(path) as f:
            return f.read().strip().lower()
    return ""

def start_capture():
    return subprocess.Popen([
        "tcpdump", "-i", INTERFACE, "-U", "-n", "-w", PCAP_FILE
    ])

def start_parser(mac_mlan, subtype_mask):
    global parse_proc
    ether_filter = f"ether host {mac_mlan}"
    log_path = f"{LOG_DIR}/{CAP_FILENAME}"
    #logger.message('info', f"[{IFACE}] ether_filter : {ether_filter}", _EXTRA_())
    logger.no_extra()
    logger.message('info', "capture start")
    parse_proc = subprocess.Popen([
        "stdbuf", "-oL", "tshark",
        "-l", "-r", PCAP_FILE,
        "-n",
        "-Y", "wlan.fc.type == 0",
        #"-f", f"{ether_filter}",
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
    ], stdout=subprocess.PIPE, text=True, bufsize=1)

    for line in parse_proc.stdout:
        fields = line.strip().split(",")
        if len(fields) < 9:
            continue

        ts, sa, da = fields[0].strip(), fields[1].lower(), fields[2].lower()
        ftype, fsub, retry, seq, rssi, nf = fields[3:9]

        if ftype != "0":
            continue

        #action no ac
        #if fsub == "14":
        #    continue
            
        #beacon
        #if fsub == "8":
        #    continue

        #if sa != mac_mlan and (da != mac_mlan or da != BROADCAST_MAC):
        #if sa != mac_mlan and da != mac_mlan:
        #    continue

        try:
            subtype = int(fsub)
        except ValueError:
            continue

        if subtype_mask & (1 << subtype):
            continue

        snr = "N/A"
        try:
            snr = str(int(rssi) - int(nf))
        except:
            pass

        #ts_fmt = datetime.fromtimestamp(float(ts), timezone(timedelta(hours=9)))
        #ts_str = ts_fmt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        frame_str = parse_frame_type(ftype, fsub)

        logger.message('info', f"{frame_str:<16}({fsub:>2}) : SA={sa:<17} DA={da:<17} RSSI={rssi:>4} NF={nf:>4} SNR={snr:>3} Retry={retry:<3} Seq={seq}");

        #msg = (f"{ts_str} {frame_str:<16}({fsub:>2}) : SA={sa:<17} DA={da:<17} "
        #       f"RSSI={rssi:>4} NF={nf:>4} SNR={snr:>3} Retry={retry:<3} Seq={seq}")
        #log_file.write(msg + "\n")
        #log_file.flush()

def parse_frame_type(ftype, fsub):
    if ftype == "0":
        return {
            "0": "Assoc Request", "1": "Assoc Response", "2": "Reassoc Request",
            "3": "Reassoc Response", "4": "Probe Request", "5": "Probe Response",
            "8": "Beacon", "9": "ATIM", "10": "Disassoc", "11": "Auth",
            "12": "Deauth", "13": "Action", "14": "Action No Ack"
        }.get(fsub, "Mgmt")
    elif ftype == "1":
        return {"11": "RTS", "12": "CTS", "13": "ACK"}.get(fsub, "Ctrl")
    elif ftype == "2":
        return {"0": "Data", "4": "Null", "8": "QoS Data", "12": "QoS Null"}.get(fsub, "Data")
    return "Unknown"

cap_proc = None
parse_proc = None

def start_pipeline():
    global cap_proc, parse_proc
    # 공통 옵션
    tcpdump_cmd = [
        "tcpdump", "-i", INTERFACE, "-U", "-n", "-w", "-"  # 표준출력으로 RAW pcap
    ]
    tshark_cmd = [
        "tshark",
        "-l",               # 라인 버퍼링
        "-n",               # 이름해석 금지
        "-r", "-",          # 표준입력에서 pcap 읽기
        "-Y", "wlan.fc.type == 0",  # 관리 프레임만
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
        "-E", "separator=,"
    ]
    # 불필요 재조립/버퍼링 끄기(안전한 기본 셋)
    tshark_cmd += [
        "-o", "tcp.desegment_tcp_streams:FALSE",
        "-o", "tls.desegment_ssl_records:FALSE",
        "-o", "http.reassemble_body:FALSE"
    ]

    # 프로세스 그룹으로 띄워 한 번에 종료하기 쉽게
    cap_proc = subprocess.Popen(
        tcpdump_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        preexec_fn=os.setsid,
        bufsize=0
    )
    parse_proc = subprocess.Popen(
        tshark_cmd,
        stdin=cap_proc.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
        preexec_fn=os.setsid
    )

'''
def cleanup():
    # tshark → tcpdump 순서로 종료 신호, 그리고 wait()
    for p in (parse_proc, cap_proc):
        if p and p.poll() is None:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGTERM)
            except Exception:
                pass
    time.sleep(0.3)
    for p in (parse_proc, cap_proc):
        if p and p.poll() is None:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            except Exception:
                pass
    for p in (parse_proc, cap_proc):
        if p:
            try:
                p.wait(timeout=1)
            except Exception:
                pass
    subprocess.run(["mlanutl_silent", IFACE, "netmon", "0"], check=False)
'''

def start_parser_loop(mac_mlan, subtype_mask):
    logger.no_extra()
    logger.message('info', "capture start")

    # parse_proc.stdout를 라인 단위로 즉시 처리
    for raw in iter(parse_proc.stdout.readline, ''):
        line = raw.strip()
        if not line:
            continue
        fields = line.split(",")
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
        if subtype_mask & (1 << subtype):
            continue

        try:
            snr = str(int(rssi) - int(nf))
        except Exception:
            snr = "N/A"

        frame_str = parse_frame_type(ftype, fsub)
        logger.message(
            'info',
            f"{frame_str:<16}({fsub:>2}) : SA={sa:<17} DA={da:<17} RSSI={rssi:>4} NF={nf:>4} SNR={snr:>3} Retry={retry:<3} Seq={seq}"
        )

def main():
    while not os.path.exists(f"/sys/class/net/{IFACE}"):
        logger.message('info', f"[{IFACE}] waiting for interface...", _EXTRA_())
        time.sleep(1)

    subprocess.run(["mkfifo", PCAP_FILE])
    mac_mlan = get_mac_address(IFACE)
    #logger.message('info', f"[{IFACE}] MAC: {mac_mlan}", _EXTRA_())
    subprocess.run(["mlanutl_silent", IFACE, "netmon", "0"])
    #time.sleep(0.5)
    subprocess.run(["mlanutl_silent", IFACE, "netmon", "1", "0x49"])
    subprocess.run(["ifconfig", INTERFACE, "up"])

    cap_proc = start_capture()
    #time.sleep(0.5)
    start_parser(mac_mlan, SUBTYPE_MASK)
    #start_pipeline()
    #start_parser_loop(mac_mlan, SUBTYPE_MASK)

    try:
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        logger.message('warn', "received interrupt")
        cleanup()
        cap_proc.terminate()

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, handle_sigterm)
    logger = Logger(app_name='MGMT', facility=logging.handlers.SysLogHandler.LOG_LOCAL0)
    IFACE = sys.argv[1] if len(sys.argv) > 1 else "mlan0"
    if IFACE not in ["mlan0", "mlan1"]:
        logger.message("emerg", f"Invalid interface {IFACE}", _EXTRA_())
        sys.exit(1)

    SUBTYPE_MASK = int(sys.argv[2], 0) if len(sys.argv) > 2 else 0x0000
    LOG_DIR = f"/var/log/cantops/mgmt/{IFACE}"
    os.makedirs(f"{LOG_DIR}/tmp", exist_ok=True)
    x = int(SUBTYPE_MASK)
    logger.message("info", f"[{IFACE}] version : {VERSION}, subtype_mask : {x:#04x}, log_file : {LOG_DIR}/{CAP_FILENAME}", _EXTRA_())
    logger = Logger(app_name='logger_cap', facility=logging.handlers.SysLogHandler.LOG_LOCAL2)

    main()
