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

VERSION = "0.0"
LOG_DIR = "/var/log/cantops/capture"
INTERFACE = "rtap"
PCAP_DIR = f"{LOG_DIR}/tmp"
PCAP_FILENAME = f"{INTERFACE}.pcap"
CAP_FILENAME = "mgmt.log"
DURATION = 10
BROADCAST_MAC = "ff:ff:ff:ff:ff:ff"
IFACE = ""

parse_thread = 0

def handle_sigterm(signum, frame):
    logger.message('crit', f"{IFACE} IGTERM?{signum} received! Doing cleanup...", _EXTRA_())
    cleanup()
    sys.exit(0)

def cleanup():
    parse_thread.join()
    subprocess.run(["mlanutl", IFACE, "netmon", "0"])
    #logger.message('warn', "Cleanup done.", _EXTRA_())

def is_ap_connected(interface):
    try:
        result = subprocess.run(["iwconfig", interface], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        output = result.stdout + result.stderr

        # "No such device" 또는 "Not-Associated" 등의 키워드로 판단
        if "No such device" in output or "Access Point: Not-Associated" in output:
            return False

        # 연결되었는지 확인 (Access Point MAC이 존재하는 경우)
        for line in output.splitlines():
            if "Access Point" in line and "Not-Associated" not in line:
                return True

        return False
    except Exception as e:
        logger.message('err', f"{IFACE} Failed to check AP connection: {e}", _EXTRA_())
        return False
    
def get_mac_address(iface):
    path = f"/sys/class/net/{iface}/address"
    if os.path.exists(path):
        with open(path) as f:
            return f.read().strip().lower()
    else:
        #logging.critical(f"Interface {iface} not found")
        return ""

def capture_tshark():
    subprocess.run([
        "tshark", "-i", INTERFACE
        #"tshark", "-i", INTERFACE, "-w", CAPTURE_FILE
    ])

def parse_capture(mac_mlan):
    #print("??")
    #ether_host = f"ether host {mac_mlan}"
    ether_host = f"ether host {mac_mlan} or ether host {BROADCAST_MAC}"
    display_filter = f"wlan.sa == {mac_mlan} || wlan.da == {mac_mlan} || wlan.bssid == {mac_mlan}"
    logger.message('info', f"{IFACE} ether host : {ether_host}", _EXTRA_())
    
    tshark_proc = subprocess.Popen([
        "tshark", "-i", INTERFACE,
        "-b", "filesize:20480", #"-b", "files:20",
        "-w", f"{PCAP_DIR}/{PCAP_FILENAME}",
        #"-f", f"{ether_host}",
        #"-Y", f"{display_filter}",
        #"-Y", "!(wlan.fc.subtype == 14)",
        "-T", "fields",
        "-e", "frame.time_epoch",
        "-e", "wlan.sa", "-e", "wlan.da", "-e", "wlan.fc.type",
        "-e", "wlan.fc.subtype", "-e", "wlan.fc.retry",
        "-e", "wlan.seq", "-e", "radiotap.dbm_antsignal", "-e", "radiotap.dbm_antnoise",
        #"-e", "wlan.ssid",
        "-E", "separator=,"
    ], stdout=subprocess.PIPE, bufsize=1, text=True)
    #logger.message('warn', f"{tshark_proc.stdout}",_EXTRA_())
                       
    for line in tshark_proc.stdout:
        fields = line.strip().split(",")
        expected_fields = 9
        if len(fields) < expected_fields:
            logger.message('err', f"{IFACE} Skipping incomplete line: {fields}", _EXTRA_())
            continue
        
        #logger.message('info', f"sa:{fields[0]}, da:{fields[1]}", _EXTRA_())
        #logger.message('info', f"sa:{wlan.sa}, da:{wlan.da}", _EXTRA_())
        timestamp, sa, da = fields[0].strip(), fields[1].lower().strip(), fields[2].lower().strip()
        ftype, fsub, retry, seq, rssi, nf = fields[3:9]

        mac_mlan = mac_mlan.lower().strip()

        #if da != mac_mlan and sa != mac_mlan and ftype != "0" and fsub != "9":
            #logger.message('info', f"Skipping frame: sa={sa}, da={da}", _EXTRA_())
        #    continue

        #if da == BROADCAST_MAC or sa == BROADCAST_MAC:
        #    #logger.message('info', f"Skipping frame: sa={sa}, da={da}", _EXTRA_())
        #    continue

        if ftype == "0" and fsub == "14":
            continue

        #direction = "RX" if da == mac_mlan or da == BROADCAST_MAC else ("TX" if sa == mac_mlan else "??")
        
        snr = "N/A"
        try:
            snr = str(int(rssi) - int(nf))
        except:
            pass

        #readable = datetime.utcfromtimestamp(float(timestamp)).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        ts = float(timestamp)
        kst = datetime.fromtimestamp(ts, timezone(timedelta(hours=9)))
        readable = kst.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        frame_str = parse_frame_type(ftype, fsub)
        #msg = f"{direction} RSSI={rssi} NF={nf} SNR={snr} SA={sa} DA={da} Retry={retry} Seq={seq} Frame={frame_str}"
        #msg = f"{frame_str} : SA={sa} DA={da} RSSI={rssi} NF={nf} SNR={snr} Retry={retry} Seq={seq}"
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

        #logging.info(msg)
        #logger.message('info', msg, _EXTRA_())
        log_filename = f"{LOG_DIR}/{CAP_FILENAME}"
        with open(log_filename, "a") as log_file:
            log_file.write(msg+"\n")
        #print(f"{msg}")
        '''
        log_filename = f"{CAP_DIR}/test.log"
        timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(log_filename, "a") as log_file:
            log_file.write(f"{timestamp_str} "+msg+"\n")
        '''

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

def main():
    #print("start"
    
    while not os.path.exists(f"/sys/class/net/{IFACE}"):
        logger.message('err', f"interface {IFACE} is invalid", _EXTRA_())
        time.sleep(5)

    mac_mlan = get_mac_address(IFACE)
    logger.message('info', f"{IFACE} MAC: {mac_mlan}", _EXTRA_())
    subprocess.run(["mlanutl", IFACE, "netmon", "0"])
    
    time.sleep(1)

    #if not is_ap_connected(IFACE):
    #    logger.message('warn', f"{IFACE} not connected to any AP. Exiting.", _EXTRA_())
    #    sys.exit(1)
    
    subprocess.run(["mlanutl", IFACE, "netmon", "1", "0x41"])

    time.sleep(1)

    mac_rtap = get_mac_address(INTERFACE)
    logger.message('info', f"{INTERFACE} MAC: {mac_rtap}", _EXTRA_())
    subprocess.run(["ifconfig", INTERFACE, "up"])
    
    #print("start2")
    #cap_thread = threading.Thread(target=capture_tshark)
    #cap_thread.start()
    #cap_thread.join()
    #print("start3")
    global parse_thread
    parse_thread = threading.Thread(target=parse_capture, args=(mac_mlan,))
    parse_thread.start()
    #parse_capture(mac_mlan)
    #print("start")

    try:
        while True:
            time.sleep(1)
            #parse_capture(mac_mlan)
                    
    except (KeyboardInterrupt, SystemExit):
        logger.message('warn', f"{IFACE} Sys received KeyboardInterrupt or exit signal.", _EXTRA_())
        #cap_thread.join()
        parse_thread.join()
    
    logger.message('warn', f"{IFACE} main loop close", _EXTRA_())


if __name__ == "__main__":
    
    #logger = Logger(app_name='logger_cap', facility=logging.handlers.SysLogHandler.LOG_LOCAL0)
    signal.signal(signal.SIGTERM, handle_sigterm)
    
    if len(sys.argv) < 2:
        IFACE = "mlan0"
    else:
        IFACE = sys.argv[1]
    
    '''
    if IFACE == "mlan0" :
        logger = Logger(app_name='logger_cap', facility=logging.handlers.SysLogHandler.LOG_LOCAL0)
    elif IFACE == "mlan1" :
        logger = Logger(app_name='logger_cap', facility=logging.handlers.SysLogHandler.LOG_LOCAL1)
    else:
        logger = Logger(app_name='logger_cap', facility=logging.handlers.SysLogHandler.LOG_SYSLOG)
        logger.message('err', f"interface {IFACE} is invalid", _EXTRA_())

    LOG_DIR = f"/var/log/cantops/{IFACE}"
    CAP_DIR = f"{LOG_DIR}/capture"
    CAPTURE_FILE = f"{CAP_DIR}/pcap.pcap"
    PCAP_DIR = f"{CAP_DIR}/tmp"
    '''
    LOG_DIR = f"/var/log/cantops/capture/{IFACE}"
    logger = Logger(app_name='logger_cap', facility=logging.handlers.SysLogHandler.LOG_LOCAL0)
    logger.message("info", f"IFACE : {IFACE}, version : {VERSION}, LOG_DIR : {LOG_DIR}", _EXTRA_())

    if IFACE != "mlan0" and IFACE != "mlan1" :
        logger.message("err", f"{IFACE} is not vaild interface", _EXTRA_())
        sys.exit(1)
        
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR, exist_ok=True)
        
    if not os.path.exists(PCAP_DIR):
        os.makedirs(PCAP_DIR, exist_ok=True)

    main()
