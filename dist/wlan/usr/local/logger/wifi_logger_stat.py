import os
import json
import re
import subprocess
import time
import sys
import signal
import fcntl
import logging
from datetime import datetime
from sUTILS import Logger, _EXTRA_

#LOG_DIR = "/home/root/source/logs"
LOG_DIR = "/var/log/cantops/stat"
VERSION = "0.0"
IFACE = ""
KEY = "LOG"

# AP별 Signal Level 최소/최대값 및 RX/TX 저장
signal_levels = {}
prev_stat = {}  # AP별 마지막 RX/TX 값 저장
last_stat = {}  # AP별 누적된 RX/TX 값 저장
current_ap = ""  # 현재 연결된 AP
log_interval = 1  # 로그 주기 (초)
check_interval = 1  # 체크 주기 (초)
STAT_RESET_INTERVAL = 604800  # 통계 리셋 주기 (7일)
last_log_time = time.time()  # 마지막 로깅 시간
tx_retrys = {}
prev_retry_count = None
prev_tx_frame_count = None

WIFI_INIT_CONF_JSON = "/usr/local/etc/wifi_init_conf.json"

def load_logger_config(iface):
    """Load logger config: {iface}.logger.key → logger.key → default"""
    global log_interval, check_interval, STAT_RESET_INTERVAL
    try:
        with open(WIFI_INIT_CONF_JSON) as _f:
            _conf = json.load(_f)
        _global = _conf.get("logger", {})
        _iface = _conf.get(iface, {}).get("logger", {})
        log_interval = _iface.get("stat_log_interval_sec",
                       _global.get("stat_log_interval_sec", log_interval))
        check_interval = _iface.get("stat_check_interval_sec",
                         _global.get("stat_check_interval_sec", check_interval))
        STAT_RESET_INTERVAL = _iface.get("stat_reset_interval_sec",
                              _global.get("stat_reset_interval_sec", STAT_RESET_INTERVAL))
    except (OSError, json.JSONDecodeError) as e:
        print(f"WARN: [{iface}] config load failed, using defaults: {e}", file=sys.stderr)

LOG_LINE_RE = re.compile(r"""
    ^\[
        (?P<timestamp>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})
    \]\s+
    MAC:(?P<mac>[0-9A-Fa-f:]+),\s+
    BW:(?P<bw_val>\d+)\s*(?P<bw_unit>[A-Za-z/]+)?\s*,\s+ 
    RSSI:(?P<rssi>-?\d+)dBm\(
        min:(?P<rssi_min>-?\d+)/
        max:(?P<rssi_max>-?\d+)
    \),\s+
    RX:(?P<rx_bytes>\d+)bytes?/(?P<rx_packets>\d+)pkts?/
       (?P<rx_bps>\d+(?:\.\d+)?)Mb(?:ps|/s)/
       (?P<rx_avg_bps>\d+(?:\.\d+)?)Mb(?:ps|/s),\s+
    TX:(?P<tx_bytes>\d+)bytes?/(?P<tx_packets>\d+)pkts?/
       (?P<tx_bps>\d+(?:\.\d+)?)Mb(?:ps|/s)/
       (?P<tx_avg_bps>\d+(?:\.\d+)?)Mb(?:ps|/s),\s+
    FAIL:(?P<tx_fail>\d+),\s+
    (?:RETRY:(?P<retry_delta>\d+)\((?P<retry_pct>\d+(?:\.\d+)?)%\),\s+)?
    T:(?P<time>\d+)
    (?:\s*.*)?$            # 끝에 부가 정보가 더 있어도 허용
""", re.VERBOSE)

def handle_sigterm(signum, frame):
    logger.message('crit', f"[{IFACE}] SIGTERM {signum} received! Cleaning up...", _EXTRA_())
    cleanup()
    sys.exit(0)

def cleanup():
    pass

def update_avg(count, avg, new_value):
    #count += 1
    if count == 0:
        return new_value

    avg += (new_value - avg) / count
    return round(avg, 1)

def get_last_ap_log_values(log_filename, num_lines=10):
    """AP별 로그 파일에서 최신 로그 데이터를 읽어 파싱"""

    if not os.path.exists(log_filename):
        logger.message("err", f"[{IFACE}] {log_filename} is not exist", _EXTRA_())
        return None

    try:
        with open(log_filename, "rb") as file:
            file.seek(0, os.SEEK_END)
            file_size = file.tell()

            # 마지막 4KB 만 읽기(부족하면 파일 크기만큼)
            read_size = min(4096, file_size)
            file.seek(file_size - read_size, os.SEEK_SET)
            chunk = file.read()

        # 바이너리 → 텍스트 라인
        lines = chunk.splitlines()[-num_lines:]
        if not lines:
            logger.message("err", f"[{IFACE}] {log_filename} is empty", _EXTRA_())
            return None

        # 최신 줄부터 역순 탐색
        for bline in reversed(lines):
            line = bline.decode("utf-8", errors="ignore").strip()
            if not line:
                continue

            m = LOG_LINE_RE.search(line)  # 라인 내 아무 위치든 매칭(안전)
            if not m:
                continue

            g = m.groupdict()

            bw_val = int(g["bw_val"])
            bw_unit = (g["bw_unit"] or "").lower()
            bw_mhz = bw_val if "mhz" in bw_unit else None

            # 타입 캐스팅
            log_data = {
                "timestamp": g["timestamp"],
                "mac": g["mac"].lower(),
                "bw": bw_val,
                "bw_unit": g["bw_unit"] or "",
                "bw_mhz": bw_mhz,
                "rssi": int(g["rssi"]),
                "rssi_min": int(g["rssi_min"]),
                "rssi_max": int(g["rssi_max"]),
                "rx_bytes": int(g["rx_bytes"]),
                "rx_packets": int(g["rx_packets"]),
                "rx_bps": float(g["rx_bps"]),
                "rx_avg_bps": float(g["rx_avg_bps"]),
                "tx_bytes": int(g["tx_bytes"]),
                "tx_packets": int(g["tx_packets"]),
                "tx_bps": float(g["tx_bps"]),
                "tx_avg_bps": float(g["tx_avg_bps"]),
                "tx_fail": int(g["tx_fail"]),
                "time": int(g["time"]),
            }

            d = log_data
            logger.message("info",
                  f"[{IFACE}] {d['timestamp']} | {d['mac']} | BW:{d['bw']} | "
                  f"RSSI:{d['rssi']}({d['rssi_min']}/{d['rssi_max']}) | "
                  f"RX:{d['rx_packets']}/{d['rx_bps']}/{d['rx_avg_bps']} | "
                  f"TX:{d['tx_packets']}/{d['tx_bps']}/{d['tx_avg_bps']} | "
                  f"FAIL:{d['tx_fail']} T:{d['time']}", _EXTRA_())
            return log_data

    except Exception as e:
        print(f"log file read error: {e}")

    print(f"No valid log found : {log_filename}")
    return None

def get_link_info(json_file):
    if not os.path.exists(json_file):
        logger.message("err", f"[{IFACE}] {json_file} is not exist", _EXTRA_())
        return None

    try:
        with open(json_file, "r") as f:
            data = json.load(f)
    except Exception as e:
        #logger.message("err", f"[{IFACE}] json read error: {e}", _EXTRA_())
        return None

    station = data.get("link")
    if not station:
        #logger.message("err", f"[{IFACE}] no 'link' field in json", _EXTRA_())
        return None

    mac = station.get("address", "00:00:00:00:00:00")
    signal_raw = station.get("signal", "-99 dBm")
    signal = int(re.search(r"-?\d+", signal_raw).group())

    tx_bitrate_raw = station.get("tx_bitrate", "0.0 MBit/s")
    rx_bitrate_raw = station.get("rx_bitrate", "0.0 MBit/s")

    tx_bitrate_match = re.match(r"([\d.]+)", tx_bitrate_raw)
    rx_bitrate_match = re.match(r"([\d.]+)", rx_bitrate_raw)

    tx_bitrate = float(tx_bitrate_match.group(1)) if tx_bitrate_match else 0.0
    rx_bitrate = float(rx_bitrate_match.group(1)) if rx_bitrate_match else 0.0

    # RSSI 범위 초기화
    if mac not in signal_levels:
        signal_levels[mac] = {"min": float("inf"), "max": float("-inf")}

    signal_levels[mac]["min"] = min(signal_levels[mac]["min"], signal)
    signal_levels[mac]["max"] = max(signal_levels[mac]["max"], signal)

    ap_info = data.get("info")
    if not station:
        #logger.message("err", f"[{IFACE}] no 'info' field in json", _EXTRA_())
        return None

    bandwidth = ap_info.get("width", "0 MHz")

    info = {
        "ap_mac": mac,
        "bw" : bandwidth,
        "rssi": signal,
        "rssi_min": signal_levels[mac]["min"],
        "rssi_max": signal_levels[mac]["max"],
        "tx_fail": int(station.get("tx_failed", 0)),
        "tx_bytes": int(station.get("tx_bytes", 0)),
        "tx_packets": int(station.get("tx_packets", 0)),
        "tx_bitrate": tx_bitrate,
        "rx_bytes": int(station.get("rx_bytes", 0)),
        "rx_packets": int(station.get("rx_packets", 0)),
        "rx_bitrate": rx_bitrate
    }

    return info

def get_station_dump():
    """Parse iw mlan0 station dump for ESSID, AP MAC, Frequency, RSSI, Tx Retries"""

    try:
        result = subprocess.run(["iw", IFACE, "station", "dump"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        output = result.stdout.strip()
    except subprocess.CalledProcessError as e:
        logger.message("err", f"[{IFACE}] iw station dump failed: {e}", _EXTRA_())
        return None

    if not output or "Station" not in output:
        #print("No station info found.")
        return None

    lines = output.strip().splitlines()
    info = {}

    for line in lines:
        line = line.strip()

        if line.startswith("Station"):
            mac = line.split()[1]
            info["ap_mac"] = mac

            if mac not in signal_levels:
                signal_levels[mac] = {"min": float("inf"), "max": float("-inf")}
        elif "signal:" in line:
            match = re.search(r"signal:\s*(-?\d+)\s*dBm", line)
            if match:
                signal = int(match.group(1))
                info["rssi"] = signal
                mac = info.get("ap_mac", "unknown")

                signal_levels[mac]["min"] = min(signal_levels[mac]["min"], signal)
                signal_levels[mac]["max"] = max(signal_levels[mac]["max"], signal)

                info["rssi_min"] = signal_levels[mac]["min"]
                info["rssi_max"] = signal_levels[mac]["max"]
        elif "rx bytes:" in line:
            info["rx_bytes"] = int(line.split(":")[1].strip())
        elif "rx packets:" in line:
            info["rx_packets"] = int(line.split(":")[1].strip())
        elif "tx bytes:" in line:
            info["tx_bytes"] = int(line.split(":")[1].strip())
        elif "tx packets:" in line:
            info["tx_packets"] = int(line.split(":")[1].strip())
        elif "tx failed:" in line:
            info['tx_fail'] = int(line.split(":")[1].strip())
            #info["tx_fail"] = tx_failed
            #mac = info.get("ap_mac", "unknown")
            #tx_retrys[mac] = tx_failed
        elif "tx bitrate:" in line:
            try:
                info["tx_bitrate"] = float(line.split()[2])
            except (IndexError, ValueError):
                info["tx_bitrate"] = 0.0
        elif "rx bitrate:" in line:
            try:
                info["rx_bitrate"] = float(line.split()[2])
            except (IndexError, ValueError):
                info["rx_bitrate"] = 0.0

    #info["essid"] = "unknown" 
    #info["frequency"] = "unknown"

    return info

def get_wifi_info():
    """Extract ESSID, Access Point MAC, Frequency, Signal Level, and Tx Retries from iwconfig mlan0"""
    cmd = f"iwconfig {IFACE}"
    result = os.popen(cmd).read()

    essid = re.search(r'ESSID:"(.+?)"', result)
    access_point = re.search(r'Access Point: ([\w:]+)', result)
    frequency = re.search(r'Frequency=([\d.]+) GHz', result)
    signal_level = re.search(r'Signal level=(-?\d+) dBm', result)
    tx_retry_match = re.search(r'Tx excessive retries:\s*(\d+)', result)

    ap_mac = access_point.group(1) if access_point else "Not"

    # Manage min/max Signal Level per AP
    if ap_mac not in signal_levels:
        signal_levels[ap_mac] = {"min": float("inf"), "max": float("-inf")}
    
    # Store Tx excessive retries per AP
    tx_retrys[ap_mac] = int(tx_retry_match.group(1)) if tx_retry_match else 0

    signal_val = int(signal_level.group(1)) if signal_level else 0
    signal_levels[ap_mac]["min"] = min(signal_levels[ap_mac]["min"], signal_val)
    signal_levels[ap_mac]["max"] = max(signal_levels[ap_mac]["max"], signal_val)

    return {
        "essid": essid.group(1) if essid else "Unknown",
        "ap_mac": ap_mac,
        "frequency": frequency.group(1) if frequency else "Unknown",
        "rssi": signal_val,
        "rssi_min": signal_levels[ap_mac]["min"],
        "rssi_max": signal_levels[ap_mac]["max"],
        "tx_retry": tx_retrys[ap_mac]
    }

def get_network_stats():
    """Extract RX/TX bytes and packets for mlan0 from /proc/net/dev"""
    cmd = "cat /proc/net/dev"
    result = os.popen(cmd).read()
    
    mlan0_data = re.search(rf"{IFACE}:\s+(\d+)\s+(\d+)\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+(\d+)\s+(\d+)", result)

    if mlan0_data:
        return {
            "rx_bytes": int(mlan0_data.group(1)),
            "rx_packets": int(mlan0_data.group(2)),
            "tx_bytes": int(mlan0_data.group(3)),
            "tx_packets": int(mlan0_data.group(4)),
        }
    return {
        "rx_bytes": 0,
        "rx_packets": 0,
        "tx_bytes": 0,
        "tx_packets": 0,
    }

'''
def get_mlanutl_log(interface="mlan0"):
    try:
        output = subprocess.check_output(["mlanutl", interface, "getlog"], text=True)
        return output
    except subprocess.CalledProcessError as e:
        print("Failed to run mlanutl:", e)
        return ""
'''

#_last_log_error_time = 0  # 전역으로 선언
#ERROR_THROTTLE_SEC = 5    # 에러 로그는 5초에 한 번만 출력

def get_mlanutl_log(interface="mlan0"):
    global _last_log_error_time
    try:
        return subprocess.check_output(["mlanutl", interface, "getlog"], text=True)
    except subprocess.CalledProcessError as e:
        logger.message("err", f"[{IFACE}] getlog Failed: {e}", _EXTRA_())
        return ""


def parse_log(log):
    retry_count = None
    tx_frag_count = None

    for line in log.splitlines():
        if "dot11RetryCount" in line:
            match = re.search(r'dot11RetryCount\s+(\d+)', line)
            if match:
                retry_count = int(match.group(1))

        elif "dot11TransmittedFragmentCount" in line:
            match = re.search(r'dot11TransmittedFragmentCount\s+(\d+)', line)
            if match:
                tx_frag_count = int(match.group(1))

    return retry_count, tx_frag_count

def log_stats_write(my_stat, wifi_info):
    #tx_retry = my_stat['tx_retry']

    #tx_retry_per = round((tx_retry/my_stat['tx_packets'])*100, 2) if my_stat['tx_packets'] else 0
    #tx_retry_per = round((tx_retry/my_stat['tx_frame_cnt'])*100, 2) if my_stat['tx_frame_cnt'] else 0
    #if tx_retry_per > 100:
    #    tx_retry_per = 100
        
    log_entry = (
        f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
        f"MAC:{wifi_info['ap_mac']}, "
        f"BW:{wifi_info['bw']}, "
        f"RSSI:{wifi_info['rssi']}dBm(min:{wifi_info['rssi_min']}/max:{wifi_info['rssi_max']}), "
        f"RX:{my_stat['rx_bytes']}byte/{my_stat['rx_packets']}pkt/{wifi_info['rx_bitrate']}Mbps/{my_stat['rx_avg_bps']}Mbps, "
        f"TX:{my_stat['tx_bytes']}byte/{my_stat['tx_packets']}pkt/{wifi_info['tx_bitrate']}Mbps/{my_stat['tx_avg_bps']}Mbps, "
        f"FAIL:{my_stat['tx_fail']}, "
        f"RETRY:{my_stat.get('retry_delta', 0)}({my_stat.get('retry_pct', 0.0)}%), "
        f"T:{my_stat['time']}\n"
    )
    


    #print(log_entry.strip())
    
    mac_address = wifi_info['ap_mac'].replace(":", "_")  # File-safe MAC format
    log_filename = f"{LOG_DIR}/{mac_address}.log"
    all_log_filename = f"{LOG_DIR}/stat.log"
    
    #if wifi_info['essid'] != "Unknown":
    with open(log_filename, "a") as log_file:
        log_file.write(log_entry)
    #else:
    #    print({wifi_info['essid']})

    with open(all_log_filename, "a") as all_log_file:
        all_log_file.write(log_entry)
        
    my_stat["tx_th_interval"] = 0
    my_stat["rx_th_interval"] = 0
            
def log_stats():
    global current_ap, last_log_time
    #global retry_count, tx_frag_count
    #logger.message("err", "test", _EXTRA_())
    all_log_filename = f"{LOG_DIR}/stat.log"
    ap_mac = None
    current_ap = None

    while True:
        if os.path.exists(f"/sys/class/net/{IFACE}"):
            #wifi_info = get_wifi_info()
            #wifi_info = get_station_dump()
            wifi_info = get_link_info(f"/var/log/cantops/json/{IFACE}/link.json")
            if not wifi_info:
                #print("not wifi info")
                #logger.warning("No WiFi info available — possibly disconnected or station dump empty.")
                #time.sleep(5)
                #continue
                ap_mac = None
                if current_ap != ap_mac:
                    log_entry = (
                        f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
                        f"AP changed: {current_ap} -> {ap_mac}\n"
                    )
                    current_ap = ap_mac
                    logger.message("info", f"[{IFACE}] AP change: {current_ap} -> {ap_mac}", _EXTRA_())
                    with open(all_log_filename, "a") as all_log_file:
                        all_log_file.write(log_entry)
                time.sleep(1)
                continue
            else:
                ap_mac = wifi_info['ap_mac']
                mac_address = ap_mac.replace(":", "_")

            #if ap_mac == "Not" and current_ap != ap_mac:
                #logger.message("info", f"prev_ap : {current_ap}, cur_ap : {ap_mac}", _EXTRA_())
                #time.sleep(1)
                #continue
            #net_stats = get_network_stats()
            #log = get_mlanutl_log(IFACE)
            #time.sleep(5)
            #continue
        else:
            time.sleep(5)
            continue

        #mac_address = ap_mac.replace(":", "_")  # File-safe MAC format        
        log_filename = f"{LOG_DIR}/{mac_address}.log"
        all_log_filename = f"{LOG_DIR}/stat.log"

        # Detect AP change
        if current_ap != ap_mac:
            #print(f"🔄 AP changed: {current_ap} -> {ap_mac}")
            #old_mac_address = current_ap.replace(":", "_")
            #old_log_filename = f"{LOG_DIR}/{old_mac_address}.log"
            log_entry = (
                f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
                f"AP changed: {current_ap} -> {ap_mac}\n"
            )
            logger.message("info", f"[{IFACE}] AP change: {current_ap} -> {ap_mac}", _EXTRA_())
            current_ap = ap_mac
            #if wifi_info['essid'] != "Unknown":
            with open(log_filename, "a") as log_file:
                log_file.write(log_entry)

            with open(all_log_filename, "a") as all_log_file:
                all_log_file.write(log_entry)

            # Reset previous AP RX/TX tracking
            prev_stat[ap_mac] = wifi_info
            
            if os.path.exists(log_filename):
                prev_log = get_last_ap_log_values(log_filename)
                last_stat.setdefault(ap_mac, {"rx_bytes": 0, "rx_packets": 0, "tx_bytes": 0, "tx_packets": 0, "tx_fail": 0, "time": 0, "tx_avg_bps": 0, "rx_avg_bps": 0, "total_bps": 0})

                if prev_log is None:
                    prev_log = {
                        "tx_fail": 0,
                        "rx_bytes": 0,
                        "rx_packets": 0,
                        "rx_avg_bps": 0,
                        "tx_bytes": 0,
                        "tx_packets": 0,
                        "tx_avg_bps": 0,
                        "rssi_min": float("inf"),
                        "rssi_max": float("-inf"),
                        "time": 0
                    }

                #prev_retry = prev_log["retry_count"]
                last_stat[ap_mac]["rx_bytes"] = prev_log["rx_bytes"]
                last_stat[ap_mac]["rx_packets"] = prev_log["rx_packets"]
                last_stat[ap_mac]["rx_avg_bps"] = prev_log["rx_avg_bps"]
                last_stat[ap_mac]["tx_bytes"] = prev_log["tx_bytes"]
                last_stat[ap_mac]["tx_packets"] = prev_log["tx_packets"]
                last_stat[ap_mac]["tx_avg_bps"] = prev_log["tx_avg_bps"]
                last_stat[ap_mac]["time"] = prev_log["time"]

                signal_levels[ap_mac]["min"] = min(signal_levels[ap_mac]["min"], prev_log["rssi_min"])
                signal_levels[ap_mac]["max"] = max(signal_levels[ap_mac]["max"], prev_log["rssi_max"])

                wifi_info["rssi_min"] = signal_levels[ap_mac]["min"]
                wifi_info["rssi_max"] = signal_levels[ap_mac]["max"]
            else:
                prev_log = {
                    "tx_fail": 0,
                    "rx_bytes": 0,
                    "rx_packets": 0,
                    "rx_avg_bps": 0,
                    "tx_bytes": 0,
                    "tx_packets": 0,
                    "tx_avg_bps": 0,
                    "rssi_min": float("inf"),
                    "rssi_max": float("-inf"),
                    "time": 0
                }
                last_stat.setdefault(ap_mac, {"rx_bytes": 0, "rx_packets": 0, "tx_bytes": 0, "tx_packets": 0, "tx_fail": 0, "time": 0, "tx_avg_bps": 0, "rx_avg_bps": 0, "total_bps": 0})

        cond_time = last_stat[ap_mac]["time"] >= STAT_RESET_INTERVAL
        cond_flagfile = os.path.exists("/tmp/wifi_stat_init_f")
        cond_mac_flagfile = os.path.exists(f"/tmp/wifi_stat_reset_{ap_mac}")
        reset_flag = cond_time or cond_flagfile or cond_mac_flagfile

        #print(f"current_ap : {current_ap} , ap_mac : {ap_mac}")
        # Update RX/TX stats
        last_stat[ap_mac]["tx_th"] = wifi_info["tx_bytes"] - prev_stat[ap_mac]["tx_bytes"]
        last_stat[ap_mac]["rx_th"] = wifi_info["rx_bytes"] - prev_stat[ap_mac]["rx_bytes"]
        #print("tx_throughput:", last_stat[ap_mac]["tx_throughput"])
        #print("rx_throughput:", last_stat[ap_mac]["rx_throughput"])
        #retry_count, tx_frag_count = parse_log(log)
        last_stat[ap_mac]["tx_fail"] = wifi_info["tx_fail"] + prev_log["tx_fail"]
        #print(f"{last_stat[ap_mac]['tx_fail']}, {wifi_info['tx_fail']}, {prev_log['tx_fail']}")
        #last_stat[ap_mac]["tx_retry"] = wifi_info["tx_retry"] + prev_retry
        #last_stat[ap_mac]["tx_retry"] = retry_count
        #last_stat[ap_mac]["tx_frame_cnt"] = tx_frag_count
        last_stat[ap_mac]["tx_bytes"] += last_stat[ap_mac]["tx_th"]
        last_stat[ap_mac]["rx_bytes"] += last_stat[ap_mac]["rx_th"]
        last_stat[ap_mac]["tx_packets"] += (wifi_info["tx_packets"] - prev_stat[ap_mac]["tx_packets"])
        last_stat[ap_mac]["rx_packets"] += (wifi_info["rx_packets"] - prev_stat[ap_mac]["rx_packets"])
        
        #last_stat[ap_mac]["tx_throughput_min"] = min(last_stat[ap_mac]["tx_throughput"], last_stat[ap_mac]["tx_throughput_min"])
        #last_stat[ap_mac]["tx_th_interval"] += last_stat[ap_mac]["tx_th"]
        #last_stat[ap_mac]["rx_th_interval"] += last_stat[ap_mac]["rx_th"]
        # Update previous RX/TX values
        prev_stat[ap_mac] = wifi_info
        #print(f"{last_stat[ap_mac]['time']}, {last_stat[ap_mac]['tx_avg_bps']}, {wifi_info['tx_bitrate']}")
        last_stat[ap_mac]['tx_avg_bps'] = update_avg(last_stat[ap_mac]["time"], last_stat[ap_mac]['tx_avg_bps'], wifi_info['tx_bitrate'])
        last_stat[ap_mac]['rx_avg_bps'] = update_avg(last_stat[ap_mac]["time"], last_stat[ap_mac]['rx_avg_bps'], wifi_info['rx_bitrate'])
	    
        # Log every N seconds
        if time.time() - last_log_time >= log_interval:
            # Retry rate from mlanutl getlog (cumulative delta)
            global prev_retry_count, prev_tx_frame_count
            log = get_mlanutl_log(IFACE)
            retry_count, tx_frame_count = parse_log(log)
            if retry_count is not None and tx_frame_count is not None:
                # 첫 샘플: baseline 설정만 하고 델타 계산 스킵
                if prev_retry_count is None or prev_tx_frame_count is None:
                    prev_retry_count = retry_count
                    prev_tx_frame_count = tx_frame_count
                    last_stat[ap_mac]["retry_delta"] = 0
                    last_stat[ap_mac]["retry_pct"] = 0.0
                    log_stats_write(last_stat[ap_mac], wifi_info)
                    last_log_time = time.time()
                    continue
                delta_retry = retry_count - prev_retry_count
                delta_tx = tx_frame_count - prev_tx_frame_count
                # 카운터 리셋/wrap 감지 시 이번 구간은 스킵
                if delta_retry < 0 or delta_tx < 0:
                    prev_retry_count = retry_count
                    prev_tx_frame_count = tx_frame_count
                    last_stat[ap_mac]["retry_delta"] = 0
                    last_stat[ap_mac]["retry_pct"] = 0.0
                    log_stats_write(last_stat[ap_mac], wifi_info)
                    last_log_time = time.time()
                    continue
                retry_pct = round((delta_retry / delta_tx) * 100, 1) if delta_tx > 0 else 0.0
                last_stat[ap_mac]["retry_delta"] = delta_retry
                last_stat[ap_mac]["retry_pct"] = retry_pct
                prev_retry_count = retry_count
                prev_tx_frame_count = tx_frame_count
            else:
                last_stat[ap_mac]["retry_delta"] = 0
                last_stat[ap_mac]["retry_pct"] = 0.0

            log_stats_write(last_stat[ap_mac], wifi_info)
            last_log_time = time.time()

        time.sleep(check_interval)  # Check every second
        last_stat[ap_mac]["time"] += check_interval
        

        #'''
        if reset_flag:
            log_entry = (
                f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
                f"MAC:{ap_mac} stat init!!\n"
            )
            #logger.message("info", f"[{IFACE}] AP change: {current_ap} -> {ap_mac}", _EXTRA_())
            with open(log_filename, "a") as log_file:
                log_file.write(log_entry)

            with open(all_log_filename, "a") as all_log_file:
                all_log_file.write(log_entry)

            prev_log["tx_fail"] = 0
            prev_log["rssi_min"] = 0
            prev_log["rssi_max"] = 0
            last_stat[ap_mac]["tx_bytes"] = 0
            last_stat[ap_mac]["rx_bytes"] = 0
            last_stat[ap_mac]["tx_packets"] = 0
            last_stat[ap_mac]["rx_packets"] = 0
            last_stat[ap_mac]['tx_avg_bps'] = 0
            last_stat[ap_mac]['rx_avg_bps'] = 0
            last_stat[ap_mac]["time"] = 0
            signal_levels[ap_mac]["min"] = 0
            signal_levels[ap_mac]["max"] = -100

            if cond_flagfile:
                try:
                    os.remove("/tmp/wifi_stat_init_f")
                except FileNotFoundError:
                    pass
            if cond_mac_flagfile:
                try:
                    os.remove(f"/tmp/wifi_stat_reset_{ap_mac}")
                except FileNotFoundError:
                    pass
        #'''

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, handle_sigterm)
    program_name = os.path.splitext(os.path.basename(sys.argv[0]))[0]
    logger = Logger(app_name="STAT", facility=logging.handlers.SysLogHandler.LOG_LOCAL0)
    
    if len(sys.argv) < 2:
        IFACE = "mlan0"
    else:
        IFACE = sys.argv[1]

    # iface 검증을 먼저 (락 파일 경로에 IFACE를 쓰기 전 — path traversal 방지)
    if IFACE != "mlan0" and IFACE != "mlan1":
        logger.message("emerg", f"[{IFACE}] is not valid interface", _EXTRA_())
        sys.exit(1)

    # 단일 인스턴스 락(iface별): 재시작 중복 실행 시 stat.log 동시 write(라인 겹침) 방지.
    # 이전 인스턴스 종료 지연에 대비해 최대 5초 재시도, 그래도 못 얻으면 중복으로 보고 종료.
    # 락은 /run(root 전용, non-world-writable)에 둬 /tmp 심링크 truncate 공격을 차단한다.
    try:
        _lock_fp = open(f"/run/wifi_logger_stat_{IFACE}.lock", "w")
    except OSError as e:
        logger.message("warning", f"[{IFACE}] lock file open failed: {e} — exit", _EXTRA_())
        sys.exit(0)
    _locked = False
    for _ in range(5):
        try:
            fcntl.flock(_lock_fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _locked = True
            break
        except OSError:
            time.sleep(1)
    if not _locked:
        logger.message("warning", f"[{IFACE}] another wifi_logger_stat already running — exit", _EXTRA_())
        sys.exit(0)

    load_logger_config(IFACE)

    LOG_DIR = f"/var/log/cantops/stat/{IFACE}"
    logger.message("info", f"[{IFACE}] version : {VERSION}, log_file : {LOG_DIR}/stat.log", _EXTRA_())
        
    # 로그 디렉토리 생성
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR, exist_ok=True)
        
    log_stats()
