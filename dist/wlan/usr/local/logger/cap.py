import subprocess
import threading
import time
import os
import sys
import shlex
import logging.handlers
from datetime import datetime
from sUTILS import Logger, _EXTRA_

INTERFACE = "rtap"
IFACE = "mlan0"
BROADCAST_MAC = "ff:ff:ff:ff:ff:ff"
LOG_DIR = f"/var/log/cantops/capture/{IFACE}"
CAP_FILENAME = "mgmt.log"

logger = Logger(app_name='logger_cap', facility=logging.handlers.SysLogHandler.LOG_LOCAL0)

def parse_frame_type(ftype, fsub):
    if ftype == "0":
        return {
            "0": "Assoc Request", "1": "Assoc Response", "2": "Reassoc Request", "3": "Reassoc Response",
            "4": "Probe Request", "5": "Probe Response", "8": "Beacon", "9": "ATIM",
            "10": "Disassoc", "11": "Auth", "12": "Deauth", "13": "Action", "14": "Action No Ack"
        }.get(fsub, "Mgmt")
    elif ftype == "1":
        return {
            "11": "RTS", "12": "CTS", "13": "ACK"
        }.get(fsub, "Ctrl")
    elif ftype == "2":
        return {
            "0": "Data", "4": "Null", "8": "QoS Data", "12": "QoS Null"
        }.get(fsub, "Data")
    return "Unknown"

def get_mac_address(iface):
    path = f"/sys/class/net/{iface}/address"
    if os.path.exists(path):
        with open(path) as f:
            return f.read().strip().lower()
    return ""

def parse_capture_from_stream(stream, mac_mlan):
    for line in stream:
        fields = line.strip().split(",")
        if len(fields) < 9:
            logger.message("warn", f"Skipping line: {fields}", _EXTRA_())
            continue

        timestamp, sa, da = fields[0].strip(), fields[1].lower().strip(), fields[2].lower().strip()
        ftype, fsub, retry, seq, rssi, nf = fields[3:9]
        snr = "N/A"
        try:
            snr = str(int(rssi) - int(nf))
        except:
            pass
        ts = float(timestamp)
        readable = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        frame_str = parse_frame_type(ftype, fsub)
        msg = (
            f"{readable} "
            f"{frame_str:<16}({fsub:>2}) : "
            f"SA={sa:<17} "
            f"DA={da:<17} "
            f"RSSI={rssi:>4} "
            f"NF={nf:>4} "
            f"SNR={snr:>3} "
            f"Retry={retry:<3} "
            f"Seq={seq}"
        )
        log_filename = f"{LOG_DIR}/{CAP_FILENAME}"
        with open(log_filename, "a") as log_file:
            log_file.write(msg + "\n")
        logger.message("info", msg, _EXTRA_())

def main():
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR, exist_ok=True)

    mac_mlan = get_mac_address(IFACE)
    if not mac_mlan:
        logger.message("err", f"Failed to get MAC address for {IFACE}", _EXTRA_())
        sys.exit(1)

    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    gz_path = f"{LOG_DIR}/raw_{timestamp}.pcap.gz"

    tcpdump_cmd = [
        "tcpdump", "-i", INTERFACE, "-s", "512", "-w", "-",
        f"wlan host {mac_mlan} and not subtype beacon and not subtype probe-req and not subtype probe-resp"
    ]

    tee_cmd = f"tee >(gzip > {shlex.quote(gz_path)})"

    tshark_cmd = [
        "tshark", "-r", "-", "-T", "fields",
        "-e", "frame.time_epoch", "-e", "wlan.sa", "-e", "wlan.da",
        "-e", "wlan.fc.type", "-e", "wlan.fc.subtype", "-e", "wlan.fc.retry",
        "-e", "wlan.seq", "-e", "radiotap.dbm_antsignal", "-e", "radiotap.dbm_antnoise",
        "-E", "separator=,"
    ]

    logger.message("info", f"Starting capture for {mac_mlan}, saving to {gz_path}", _EXTRA_())

    tcpdump = subprocess.Popen(tcpdump_cmd, stdout=subprocess.PIPE)
    tee = subprocess.Popen(tee_cmd, stdin=tcpdump.stdout, stdout=subprocess.PIPE, shell=True, executable="/bin/bash")
    tshark = subprocess.Popen(tshark_cmd, stdin=tee.stdout, stdout=subprocess.PIPE, text=True)

    try:
        parse_capture_from_stream(tshark.stdout, mac_mlan)
        tshark.wait()
        logger.message("warn", f"tshark exited", _EXTRA_())
    except Exception as e:
        logger.message("err", f"Exception: {e}", _EXTRA_())
        sys.exit(1)

if __name__ == "__main__":
    logger.message("info", f"Interface: {IFACE}, LogDir: {LOG_DIR}", _EXTRA_())
    main()
