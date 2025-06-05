import os
import json
import re
import subprocess
import time
import sys
import signal
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
last_log_time = time.time()  # 마지막 로깅 시간
tx_retrys = {}

def handle_sigterm(signum, frame):
    logger.message('crit', f"{IFACE} SIGTERM {signum} received! Cleaning up...", _EXTRA_())
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
        logger.message("err", f"{IFACE} {log_filename} is not exist", _EXTRA_())
        #print(f"{log_filename} is not exist")
        return None  # 파일이 존재하지 않으면 None 반환

    try:
        with open(log_filename, "rb") as file:
            file.seek(0, os.SEEK_END)  # 파일 끝으로 이동
            file_size = file.tell()

            # 마지막 4KB를 읽음 (로그가 길어도 충분히 확보)
            read_size = min(4096, file_size)
            file.seek(file_size - read_size, 0)

            lines = file.readlines()[-num_lines:]  # 마지막 num_lines 줄만 읽기

        if not lines:
            #print(f"{log_filename} is empty")
            logger.message("err", f"{IFACE} {log_filename} is empty", _EXTRA_())
            return None

        for line in reversed(lines):
            line = line.decode("utf-8", errors="ignore").strip()
            if not line:
                continue

            match = re.match(
                r"\[(?P<timestamp>[\d-]+\s[\d:]+)\] "
                r"mac:(?P<mac>[0-9A-Fa-f:]+), "
                r"rssi:(?P<rssi>[-]?\d+)dBm\(min:(?P<rssi_min>[-]?\d+)/max:(?P<rssi_max>[-]?\d+)\), "
                r"tx_fail:(?P<tx_fail>\d+)\, "
                r"tx:(?P<tx_bytes>\d+)byte/(?P<tx_packets>\d+)pkt/(?P<tx_bps>[\d.]+)Mbps/(?P<tx_avg_bps>[\d.]+)Mbps, "
                r"rx:(?P<rx_bytes>\d+)byte/(?P<rx_packets>\d+)pkt/(?P<rx_bps>[\d.]+)Mbps/(?P<rx_avg_bps>[\d.]+)Mbps, "
                r"time:(?P<time>\d+)", 
                line
            )

            if match:
                log_data = {
                    "mac": match.group("mac"),
                    "rssi": int(match.group("rssi")),
                    "rssi_min": int(match.group("rssi_min")),
                    "rssi_max": int(match.group("rssi_max")),
                    "tx_fail": int(match.group("tx_fail")),
                    "tx_bytes": int(match.group("tx_bytes")),
                    "tx_packets": int(match.group("tx_packets")),
                    "tx_avg_bps": float(match.group("tx_avg_bps")),
                    "tx_bps": float(match.group("tx_bps")),
                    "rx_bytes": int(match.group("rx_bytes")),
                    "rx_packets": int(match.group("rx_packets")),
                    "rx_avg_bps": float(match.group("rx_avg_bps")),
                    "rx_bps": float(match.group("rx_bps")),
                    "time": int(match.group("time"))
                }

                print(f"log parsing : {log_data}")
                return log_data

    except Exception as e:
        print(f"log file read error: {e}")

    print(f"No valid log found : {log_filename}")
    return None  # 유효한 로그를 찾지 못한 경우


def get_station_info(json_file):
    if not os.path.exists(json_file):
        logger.message("err", f"{IFACE} {json_file} is not exist", _EXTRA_())
        return None

    try:
        with open(json_file, "r") as f:
            data = json.load(f)
    except Exception as e:
        #logger.message("err", f"{IFACE} json read error: {e}", _EXTRA_())
        return None

    station = data.get("station_info")
    if not station:
        #logger.message("err", f"{IFACE} no 'station_info' field in json", _EXTRA_())
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

    info = {
        "ap_mac": mac,
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
        logger.message("err", f"{IFACE} iw station dump failed: {e}", _EXTRA_())
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
        logger.message("err", f"{IFACE} getlog Failed: {e}", _EXTRA_())
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
        f"mac:{wifi_info['ap_mac']}, "
        f"rssi:{wifi_info['rssi']}dBm(min:{wifi_info['rssi_min']}/max:{wifi_info['rssi_max']}), "
        f"tx_fail:{my_stat['tx_fail']}, "
        f"tx:{my_stat['tx_bytes']}byte/{my_stat['tx_packets']}pkt/{wifi_info['tx_bitrate']}Mbps/{my_stat['tx_avg_bps']}Mbps, "
        f"rx:{my_stat['rx_bytes']}byte/{my_stat['rx_packets']}pkt/{wifi_info['rx_bitrate']}Mbps/{my_stat['rx_avg_bps']}Mbps, "
        f"time:{my_stat['time']}\n"
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

    while True:
        if os.path.exists(f"/sys/class/net/{IFACE}"):
            #wifi_info = get_wifi_info()
            #wifi_info = get_station_dump()
            wifi_info = get_station_info(f"/var/log/cantops/link/{IFACE}/link.json")
            if not wifi_info:
                #print("not wifi info")
                #logger.warning("No WiFi info available — possibly disconnected or station dump empty.")
                time.sleep(5)
                continue

            ap_mac = wifi_info['ap_mac']
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

        mac_address = ap_mac.replace(":", "_")  # File-safe MAC format        
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

                if prev_log is None:  # 🔥 None이면 기본값 설정
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
                        "time": 0  # sec 값이 0이면 나눗셈 오류 가능하므로 1로 설정
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
	
        # Log every 5 seconds
        if time.time() - last_log_time >= log_interval:
            #print(f"dot11RetryCount: {retry_count}")
            
            #print(f"dot11TransmittedFragmentCount: {tx_frag_count}")

            #last_stat[ap_mac]["tx_th_interval"] = round(last_stat[ap_mac]["tx_th"]*8/1024/1024, 2)
            #last_stat[ap_mac]["rx_th_interval"] = round(last_stat[ap_mac]["rx_th"]*8/1024/1024, 2)
            #last_stat[ap_mac]["total_bps"] = round((last_stat[ap_mac]["tx_bytes"] + last_stat[ap_mac]["rx_bytes"])*8/last_stat[ap_mac]["time"]/1024/1024, 2)
            log_stats_write(last_stat[ap_mac], wifi_info)
            last_log_time = time.time()

        time.sleep(check_interval)  # Check every second
        last_stat[ap_mac]["time"] += check_interval
        

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, handle_sigterm)
    program_name = os.path.splitext(os.path.basename(sys.argv[0]))[0]
    logger = Logger(app_name="logger_stat", facility=logging.handlers.SysLogHandler.LOG_LOCAL0)
    
    if len(sys.argv) < 2:
        IFACE = "mlan0"
    else:
        IFACE = sys.argv[1]
        
    LOG_DIR = f"/var/log/cantops/stat/{IFACE}"
    logger.message("info", f"version : {VERSION}, log_file : {LOG_DIR}/stat.log", _EXTRA_())
    
    if IFACE != "mlan0" and IFACE != "mlan1" :
        logger.message("err", f"{IFACE} is not vaild interface", _EXTRA_())
        sys.exit(1)
        
    # 로그 디렉토리 생성
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR, exist_ok=True)
        
    log_stats()
