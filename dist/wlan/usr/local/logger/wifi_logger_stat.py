import os
import re
import subprocess
import time
import sys
import logging
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
log_interval = 5  # 로그 주기 (초)
check_interval = 1  # 체크 주기 (초)
last_log_time = time.time()  # 마지막 로깅 시간
tx_retrys = {}


'''
def get_last_ap_log_values(log_filename):
    """Read the last log entry from the AP-specific log file to extract previous retry, TX, RX values"""
    if not os.path.exists(log_filename):
        return 0, 0, 0, 0, 0, 0  # (retry, rx_bytes, rx_packets, tx_bytes, tx_packets)

    try:
        with open(log_filename, "r") as file:
            lines = file.readlines()
            
            for line in reversed(lines):  # Read from the last line
                line =  line.strip()
                if not line:
                    continue

                match = re.search(
                    r"RETRY:([\d.]+)%\((\d+)\), TX:(\d+)byte/(\d+)pkt/(\d+)bps, "
                    r"RX:(\d+)byte/(\d+)pkt/(\d+)bps, TOTAL:([\d.]+)bps, SEC:([\d.]+)", 
                    line
                )
                if match:
                    retry_percent = float(match.group(1))
                    retry_count = int(match.group(2))
                    tx_bytes = int(match.group(3))
                    tx_packets = int(match.group(4))
                    tx_bps = int(match.group(5))
                    rx_bytes = int(match.group(6))
                    rx_packets = int(match.group(7))
                    rx_bps = int(match.group(8))
                    total_bps = int(match.group(9))
                    sec = int(match.group(10))
                    return retry_count, tx_bytes, tx_packets, rx_bytes, rx_packets, sec
    except Exception as e:
        logger.message("err", f"{IFACE} error reading log file: {e}", _EXTRA_())
        #print(f"Error reading log file: {e}")

    return 0, 0, 0, 0, 0, 0  # Default values if no valid log entry found
'''
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
                r"SSID:(?P<ssid>[^,]+), "
                r"MAC:(?P<mac>[0-9A-Fa-f:]+), "
                r"FREQ:(?P<freq>[\d.]+)GHz, "
                r"RSSI:(?P<rssi>[-]?\d+)dBm\(Min:(?P<rssi_min>[-]?\d+), Max:(?P<rssi_max>[-]?\d+)\), "
                r"RETRY:(?P<retry_percent>[\d.]+)%\((?P<retry_count>\d+)\), "
                r"TX:(?P<tx_bytes>\d+)byte/(?P<tx_packets>\d+)pkt/(?P<tx_mbps>[\d.]+)Mbps, "
                r"RX:(?P<rx_bytes>\d+)byte/(?P<rx_packets>\d+)pkt/(?P<rx_mbps>[\d.]+)Mbps, "
                r"TOTAL:(?P<total_mbps>[\d.]+)Mbps, "
                r"SEC:(?P<sec>\d+)", 
                line
            )

            if match:
                log_data = {
                    "ssid": match.group("ssid").strip(),
                    "mac": match.group("mac"),
                    "freq": float(match.group("freq")),  # 주파수는 float 변환
                    "rssi": int(match.group("rssi")),
                    "rssi_min": int(match.group("rssi_min")),
                    "rssi_max": int(match.group("rssi_max")),
                    "retry_percent": float(match.group("retry_percent")),  # 수정: float 변환
                    "retry_count": int(match.group("retry_count")),
                    "tx_bytes": int(match.group("tx_bytes")),
                    "tx_packets": int(match.group("tx_packets")),
                    "tx_mbps": float(match.group("tx_mbps")),
                    "rx_bytes": int(match.group("rx_bytes")),
                    "rx_packets": int(match.group("rx_packets")),
                    "rx_mbps": float(match.group("rx_mbps")),
                    "total_mbps": float(match.group("total_mbps")),
                    "sec": int(match.group("sec"))
                }

                print(f"log parsing : {log_data}")
                return log_data

    except Exception as e:
        print(f"log file read error: {e}")

    print(f"No valid log found : {log_filename}")
    return None  # 유효한 로그를 찾지 못한 경우
'''
def get_last_ap_log_values(log_filename, num_lines=10):
    """Read the last log entry from the AP-specific log file to extract relevant values"""
    if not os.path.exists(log_filename):
        return None  # 파일이 존재하지 않으면 None 반환

    try:
        with open(log_filename, "rb") as file:
            file.seek(0, os.SEEK_END)  # 파일 끝으로 이동
            file_size = file.tell()
            file.seek(max(file_size - 1024, 0), 0)  # 마지막 1KB만 읽어 최적화

            lines = file.readlines()[-num_lines:]  # 마지막 num_lines 줄만 읽기

        for line in reversed(lines):
            line = line.decode("utf-8", errors="ignore").rstrip()
            if not line:
                continue

            match = re.search(
                r"SSID:(.*?), MAC:([0-9A-Fa-f:]+), FREQ:([\d.]+)GHz, "
                r"RSSI:([-]?\d+)dBm\(Min:([-]?\d+), Max:([-]?\d+)\), "
                r"RETRY:(\d+)%\((\d+)\), "
                r"TX:(\d+)byte/(\d+)pkt/(\d+\.\d+|\d+)Mbps, "
                r"RX:(\d+)byte/(\d+)pkt/(\d+\.\d+|\d+)Mbps, "
                r"TOTAL:(\d+\.\d+|\d+)Mbps, SEC:(\d+)", 
                line
            )

            if match:
                ssid = match.group(1).strip()
                mac = match.group(2)
                freq = float(match.group(3))  # 주파수는 float 변환
                rssi = int(match.group(4))
                min_rssi = int(match.group(5))
                max_rssi = int(match.group(6))
                retry_percent = int(match.group(7))
                retry_count = int(match.group(8))
                tx_bytes = int(match.group(9))
                tx_packets = int(match.group(10))
                tx_bps = float(match.group(11))
                rx_bytes = int(match.group(12))
                rx_packets = int(match.group(13))
                rx_bps = float(match.group(14))  # 소수 가능
                total_bps = float(match.group(15))  # 소수 가능
                sec = int(match.group(16))

                print(f"✅ SSID={ssid}, MAC={mac}, FREQ={freq}GHz, RSSI={rssi}dBm, SEC={sec}, TOTAL BPS={total_bps}")
                
                return {
                    "ssid": ssid,
                    "mac": mac,
                    "freq": freq,
                    "rssi": rssi,
                    "min_rssi": min_rssi,
                    "max_rssi": max_rssi,
                    "retry_percent": retry_percent,
                    "retry_count": retry_count,
                    "tx_bytes": tx_bytes,
                    "tx_packets": tx_packets,
                    "tx_bps": tx_bps,
                    "rx_bytes": rx_bytes,
                    "rx_packets": rx_packets,
                    "rx_bps": rx_bps,
                    "total_bps": total_bps,
                    "sec": sec
                }

    except Exception as e:
        print(f"🚨 Error reading log file: {e}")

    return None  # 유효한 로그를 찾지 못한 경우
'''


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
    tx_retry = my_stat['tx_retry']

    #tx_retry_per = round((tx_retry/my_stat['tx_packets'])*100, 2) if my_stat['tx_packets'] else 0
    tx_retry_per = round((tx_retry/my_stat['tx_frame_cnt'])*100, 2) if my_stat['tx_frame_cnt'] else 0
    #if tx_retry_per > 100:
    #    tx_retry_per = 100
        
    log_entry = (
        f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
        f"SSID:{wifi_info['essid']}, MAC:{wifi_info['ap_mac']}, FREQ:{wifi_info['frequency']}GHz, "
        f"RSSI:{wifi_info['rssi']}dBm(Min:{wifi_info['rssi_min']}, Max:{wifi_info['rssi_max']}), "
        f"RETRY:{tx_retry_per}%({tx_retry}), "
        f"TX:{my_stat['tx_bytes']}byte/{my_stat['tx_packets']}pkt/{my_stat['tx_th_interval']}Mbps, "
        f"RX:{my_stat['rx_bytes']}byte/{my_stat['rx_packets']}pkt/{my_stat['rx_th_interval']}Mbps, "
        f"TOTAL:{my_stat['total_bps']}Mbps, SEC:{my_stat['sec']}\n"
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
            wifi_info = get_wifi_info()
            ap_mac = wifi_info['ap_mac']
            if ap_mac == "Not" and current_ap != ap_mac:
                #logger.message("info", f"prev_ap : {current_ap}, cur_ap : {ap_mac}", _EXTRA_())
                time.sleep(1)
                continue
            net_stats = get_network_stats()
            log = get_mlanutl_log(IFACE)
        else:
            time.sleep(5)
            continue
        
        
        #print(f"dot11RetryCount: {retry_count}")
        #print(f"dot11TransmittedFragmentCount: {tx_frag_count}")
        #if ap_mac == "Not":
        #    mac_address = current_ap.replace(":", "_")
        #else:
        #    mac_address = ap_mac.replace(":", "_")  # File-safe MAC format
        mac_address = ap_mac.replace(":", "_")  # File-safe MAC format
        log_filename = f"{LOG_DIR}/{mac_address}.log"
        all_log_filename = f"{LOG_DIR}/stat.log"
	

        # If it's a new AP, initialize RX/TX values
        '''
        if ap_mac not in prev_stat:
            log_stats_write(last_stat[ap_mac], wifi_info)
            prev_stat[ap_mac] = net_stats
            last_stat[ap_mac] = {"rx_bytes": 0, "rx_packets": 0, "tx_bytes": 0, "tx_packets": 0, "tx_retry": 0}
            prev_retry, prev_rx_bytes, prev_rx_packets, prev_tx_bytes, prev_tx_packets = get_last_ap_log_values(log_filename)
            #print("prev_tx_bytes=", prev_tx_bytes)
            #print("prev_rx_bytes=", prev_rx_bytes)
            #print("prev_retry=", prev_retry)
            last_stat[ap_mac]["rx_bytes"] = prev_rx_bytes
            last_stat[ap_mac]["rx_packets"] = prev_rx_packets
            last_stat[ap_mac]["tx_bytes"] = prev_tx_bytes
            last_stat[ap_mac]["tx_packets"] = prev_tx_packets
            last_stat[ap_mac]["tx_retry"] = prev_retry
        '''
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

            #subprocess.run(['systemctl', 'restart', 'logger_cap'],
            #       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            '''
            # Read last log entry for the previous AP
            #prev_retry, prev_rx_bytes, prev_rx_packets, prev_tx_bytes, prev_tx_packets = get_last_ap_log_values(log_filename)
            last_stat.setdefault(current_ap, {"rx_bytes": 0, "rx_packets": 0, "tx_bytes": 0, "tx_packets": 0, "tx_retry": 0})
            
            # Apply previous values before updating stats
            last_stat[current_ap]["rx_bytes"] += (net_stats["rx_bytes"] - prev_stat[ap_mac]["rx_bytes"])
            last_stat[current_ap]["rx_packets"] += (net_stats["rx_packets"] - prev_stat[ap_mac]["rx_packets"])
            last_stat[current_ap]["tx_bytes"] += (net_stats["tx_bytes"] - prev_stat[ap_mac]["tx_bytes"])
            last_stat[current_ap]["tx_packets"] += (net_stats["tx_packets"] - prev_stat[ap_mac]["tx_packets"])
            last_stat[current_ap]["tx_retry"] = wifi_info["tx_retry"]
            #print("prev_tx_bytes=", prev_tx_bytes)
            #print("prev_rx_bytes=", prev_rx_bytes)
            #print("prev_retry=", prev_retry)
            
            log_stats_write(last_stat[current_ap], wifi_info)
            '''
            # Reset previous AP RX/TX tracking
            prev_stat[ap_mac] = net_stats
            
            if os.path.exists(log_filename):
                '''
                print("file exist")
                print(get_last_ap_log_values(log_filename))
                prev_retry, prev_tx_bytes, prev_tx_packets, prev_rx_bytes, prev_rx_packets, sec = get_last_ap_log_values(log_filename)
                last_stat.setdefault(ap_mac, {"rx_bytes": 0, "rx_packets": 0, "tx_bytes": 0, "tx_packets": 0, "tx_retry": 0, "sec": 0, "tx_throughput": 0, "rx_throughput": 0, "total_bps": 0})
                last_stat[ap_mac]["rx_bytes"] = prev_rx_bytes
                last_stat[ap_mac]["rx_packets"] = prev_rx_packets
                last_stat[ap_mac]["tx_bytes"] = prev_tx_bytes
                last_stat[ap_mac]["tx_packets"] = prev_tx_packets
                last_stat[ap_mac]["sec"] = sec
                '''
                #last_stat[ap_mac]["tx_retry"] += prev_retry
                #print("prev_tx_bytes=", prev_tx_bytes)
                #print("prev_rx_bytes=", prev_rx_bytes)
                #print("prev_retry=", prev_retry)
                prev_log = get_last_ap_log_values(log_filename)
                last_stat.setdefault(ap_mac, {"rx_bytes": 0, "rx_packets": 0, "tx_bytes": 0, "tx_packets": 0, "tx_retry": 0, "sec": 0, "tx_th": 0, "rx_th": 0, "tx_th_interval": 0, "rx_th_interval": 0, "total_bps": 0})

                if prev_log is None:  # 🔥 None이면 기본값 설정
                    prev_log = {
                        "retry_count": 0,
                        "rx_bytes": 0,
                        "rx_packets": 0,
                        "tx_bytes": 0,
                        "tx_packets": 0,
                        "rssi_min": float("inf"),
                        "rssi_max": float("-inf"),
                        "sec": 0  # sec 값이 0이면 나눗셈 오류 가능하므로 1로 설정
                    }
                
                #prev_retry = prev_log["retry_count"]
                last_stat[ap_mac]["rx_bytes"] = prev_log["rx_bytes"]
                last_stat[ap_mac]["rx_packets"] = prev_log["rx_packets"]
                last_stat[ap_mac]["tx_bytes"] = prev_log["tx_bytes"]
                last_stat[ap_mac]["tx_packets"] = prev_log["tx_packets"]
                last_stat[ap_mac]["sec"] = prev_log["sec"]
                
                signal_levels[ap_mac]["min"] = min(signal_levels[ap_mac]["min"], prev_log["rssi_min"])
                signal_levels[ap_mac]["max"] = max(signal_levels[ap_mac]["max"], prev_log["rssi_max"])
                
                wifi_info["rssi_min"] = signal_levels[ap_mac]["min"]
                wifi_info["rssi_max"] = signal_levels[ap_mac]["max"]

        #print(f"current_ap : {current_ap} , ap_mac : {ap_mac}")
        # Update RX/TX stats
        last_stat[ap_mac]["tx_th"] = net_stats["tx_bytes"] - prev_stat[ap_mac]["tx_bytes"]
        last_stat[ap_mac]["rx_th"] = net_stats["rx_bytes"] - prev_stat[ap_mac]["rx_bytes"]
        #print("tx_throughput:", last_stat[ap_mac]["tx_throughput"])
        #print("rx_throughput:", last_stat[ap_mac]["rx_throughput"])
        retry_count, tx_frag_count = parse_log(log)
        #last_stat[ap_mac]["tx_retry"] = wifi_info["tx_retry"] + prev_retry
        last_stat[ap_mac]["tx_retry"] = retry_count
        last_stat[ap_mac]["tx_frame_cnt"] = tx_frag_count
        last_stat[ap_mac]["tx_bytes"] += last_stat[ap_mac]["tx_th"]
        last_stat[ap_mac]["rx_bytes"] += last_stat[ap_mac]["rx_th"]
        last_stat[ap_mac]["tx_packets"] += (net_stats["tx_packets"] - prev_stat[ap_mac]["tx_packets"])
        last_stat[ap_mac]["rx_packets"] += (net_stats["rx_packets"] - prev_stat[ap_mac]["rx_packets"])
        
        #last_stat[ap_mac]["tx_throughput_min"] = min(last_stat[ap_mac]["tx_throughput"], last_stat[ap_mac]["tx_throughput_min"])
        last_stat[ap_mac]["sec"] += check_interval
        
        last_stat[ap_mac]["tx_th_interval"] += last_stat[ap_mac]["tx_th"]
        last_stat[ap_mac]["rx_th_interval"] += last_stat[ap_mac]["rx_th"]
        # Update previous RX/TX values
        prev_stat[ap_mac] = net_stats
	
        # Log every 5 seconds
        if time.time() - last_log_time >= log_interval:
            #print(f"dot11RetryCount: {retry_count}")
            #print(f"dot11TransmittedFragmentCount: {tx_frag_count}")

            last_stat[ap_mac]["tx_th_interval"] = round(last_stat[ap_mac]["tx_th"]*8/1024/1024, 2)
            last_stat[ap_mac]["rx_th_interval"] = round(last_stat[ap_mac]["rx_th"]*8/1024/1024, 2)
            last_stat[ap_mac]["total_bps"] = round((last_stat[ap_mac]["tx_bytes"] + last_stat[ap_mac]["rx_bytes"])*8/last_stat[ap_mac]["sec"]/1024/1024, 2)
            log_stats_write(last_stat[ap_mac], wifi_info)
            '''
            tx_retry_per = round((tx_retrys[ap_mac]/last_stat[ap_mac]['tx_packets'])*100, 2) if last_stat[ap_mac]['tx_packets'] else 0
            log_entry = (
                f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
                f"SSID: {wifi_info['essid']}, MAC: {ap_mac}, FREQ: {wifi_info['frequency']} GHz, "
                f"RSSI: {wifi_info['signal_level']} dBm (Min: {wifi_info['signal_min']}, Max: {wifi_info['signal_max']}), "
                f"RETRY: {tx_retry_per}%({tx_retrys[ap_mac]}), "
                f"TX: {last_stat[ap_mac]['tx_bytes']} bytes / {last_stat[ap_mac]['tx_packets']} packets, "
                f"RX: {last_stat[ap_mac]['rx_bytes']} bytes / {last_stat[ap_mac]['rx_packets']} packets\n"
            )

            #print(log_entry.strip())

            with open(log_filename, "a") as log_file:
                log_file.write(log_entry)
            with open(all_log_filename, "a") as all_log_file:
                all_log_file.write(log_entry)
            '''
            
            last_log_time = time.time()

        time.sleep(check_interval)  # Check every second

if __name__ == "__main__":
    program_name = os.path.splitext(os.path.basename(sys.argv[0]))[0]
    logger = Logger(app_name="logger_stat", facility=logging.handlers.SysLogHandler.LOG_LOCAL0)
    
    if len(sys.argv) < 2:
        IFACE = "mlan0"
    else:
        IFACE = sys.argv[1]
        
    LOG_DIR = f"/var/log/cantops/stat/{IFACE}"
    logger.message("notice", f"IFACE : {IFACE}, version : {VERSION}, LOG_DIR : {LOG_DIR}", _EXTRA_())
    
    if IFACE != "mlan0" and IFACE != "mlan1" :
        logger.message("err", f"{IFACE} is not vaild interface", _EXTRA_())
        sys.exit(1)
        
    # 로그 디렉토리 생성
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR, exist_ok=True)
        
    log_stats()
