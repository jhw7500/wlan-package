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
LOG_DIR = "/var/log/cantops/capture"
INTERFACE = "rtap"
#PCAP_FILE = f"{LOG_DIR}/tmp/{INTERFACE}.pcap"
PCAP_FILE = "/tmp/live.pcap"
CAP_FILENAME = "mgmt.log"
BROADCAST_MAC = "ff:ff:ff:ff:ff:ff"
IFACE = ""

parse_proc = None

def handle_sigterm(signum, frame):
    #logger.message('crit', f"[{IFACE}] SIGTERM {signum} received! Cleaning up...", _EXTRA_())
    cleanup()
    sys.exit(0)

def cleanup():
    if parse_proc:
        parse_proc.terminate()
    subprocess.run(["mlanutl", IFACE, "netmon", "0"])

def get_mac_address(iface):
    path = f"/sys/class/net/{iface}/address"
    if os.path.exists(path):
        with open(path) as f:
            return f.read().strip().lower()
    return ""

def start_capture():
    return subprocess.Popen([
        "tcpdump", "-i", INTERFACE, "-U", "-w", PCAP_FILE
    ])

def start_parser(mac_mlan):
    global parse_proc
    ether_filter = f"ether host {mac_mlan}"
    log_path = f"{LOG_DIR}/{CAP_FILENAME}"
    logger.message('err', f"[{IFACE}] ether_filter : {ether_filter}", _EXTRA_())
    parse_proc = subprocess.Popen([
        "stdbuf", "-oL", "tshark",
        "-l", "-r", PCAP_FILE,
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
        "-E", "separator=,"
    ], stdout=subprocess.PIPE, text=True, bufsize=1)

    with open(log_path, "a") as log_file:
        for line in parse_proc.stdout:
            fields = line.strip().split(",")
            if len(fields) < 9:
                continue

            ts, sa, da = fields[0].strip(), fields[1].lower(), fields[2].lower()
            ftype, fsub, retry, seq, rssi, nf = fields[3:9]

            #action no ac
            #if ftype == "0" and fsub == "14":
            #    continue
            
            #beacon
            #if ftype == "0" and fsub == "8":
            #    continue

            #if sa != mac_mlan and (da != mac_mlan or da != BROADCAST_MAC):
            #if sa != mac_mlan and da != mac_mlan:
            #    continue

            snr = "N/A"
            try:
                snr = str(int(rssi) - int(nf))
            except:
                pass

            ts_fmt = datetime.fromtimestamp(float(ts), timezone(timedelta(hours=9)))
            ts_str = ts_fmt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            frame_str = parse_frame_type(ftype, fsub)

            msg = (f"{ts_str} {frame_str:<16}({fsub:>2}) : SA={sa:<17} DA={da:<17} "
                   f"RSSI={rssi:>4} NF={nf:>4} SNR={snr:>3} Retry={retry:<3} Seq={seq}")
            log_file.write(msg + "\n")
            log_file.flush()

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

def main():
    while not os.path.exists(f"/sys/class/net/{IFACE}"):
        logger.message('err', f"[{IFACE}] interface is invalid", _EXTRA_())
        time.sleep(5)

    subprocess.run(["mkfifo", PCAP_FILE])
    mac_mlan = get_mac_address(IFACE)
    #logger.message('info', f"[{IFACE}] MAC: {mac_mlan}", _EXTRA_())
    subprocess.run(["mlanutl", IFACE, "netmon", "0"])
    time.sleep(0.5)
    subprocess.run(["mlanutl", IFACE, "netmon", "1", "0x41"])
    subprocess.run(["ifconfig", INTERFACE, "up"])

    cap_proc = start_capture()
    time.sleep(0.5)
    start_parser(mac_mlan)

    try:
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        logger.message('warn', f"[{IFACE}] received interrupt", _EXTRA_())
        cleanup()
        cap_proc.terminate()

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, handle_sigterm)
    logger = Logger(app_name='logger_cap', facility=logging.handlers.SysLogHandler.LOG_LOCAL0)

    IFACE = sys.argv[1] if len(sys.argv) > 1 else "mlan0"
    if IFACE not in ["mlan0", "mlan1"]:
        logger.message("emerg", f"Invalid interface {IFACE}", _EXTRA_())
        sys.exit(1)

    LOG_DIR = f"/var/log/cantops/capture/{IFACE}"
    os.makedirs(f"{LOG_DIR}/tmp", exist_ok=True)

    #logger.message("info", f"[{IFACE}] version : {VERSION}, log_file : {LOG_DIR}/{CAP_FILENAME}", _EXTRA_())

    main()
