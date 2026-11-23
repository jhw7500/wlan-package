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
prev_stat = {}  # AP별 카운터 기준(마지막으로 안 RX/TX·FAIL 드라이버 값)
last_stat = {}  # AP별 누적된 RX/TX 값 저장
# 직전 표본 대비 증가분으로 쌓는 드라이버 누적 카운터(#497)
STAT_COUNTERS = ("tx_fail", "tx_bytes", "tx_packets", "rx_bytes", "rx_packets")
# 대역폭을 모르는 표본으로 stat 줄을 연달아 건너뛴 횟수 — BW_SKIP_WARN_STREAK 번째에 한 번 경고한다(#497)
bw_skip_streak = 0
BW_SKIP_WARN_STREAK = 3
current_ap = ""  # 현재 연결된 AP
log_interval = 1  # 로그 주기 (초)
check_interval = 1  # 체크 주기 (초)
STAT_RESET_INTERVAL = 604800  # 통계 리셋 주기 (7일)
last_log_time = time.time()  # 마지막 로깅 시간
tx_retrys = {}
prev_retry_count = None
prev_tx_frame_count = None

WIFI_INIT_CONF_JSON = "/usr/local/etc/wifi_init_conf.json"
STAT_COMMAND_TIMEOUT_S = 5
PROC_NET_DEV = "/proc/net/dev"

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
        logger.message(
            "warn",
            f"[{iface}] config load failed, using defaults: {e}",
            _EXTRA_(),
        )

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

def _station_counter(station, key):
    """link.json 의 누적 카운터. 없거나 정수로 읽을 수 없으면 None(모름) — 0 으로 채우면 다음 표본에서
    카운터 전체가 증가분으로 다시 더해진다(#497)."""
    try:
        return int(station[key])
    except (KeyError, TypeError, ValueError):
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

    # info·width 가 없으면 대역폭을 모른다(None) — 그런 표본의 stat 줄은 log_stats_write 가 건너뛴다(#497).
    ap_info = data.get("info")
    bandwidth = ap_info.get("width") if isinstance(ap_info, dict) else None

    info = {
        "ap_mac": mac,
        "bw" : bandwidth,
        "rssi": signal,
        "rssi_min": signal_levels[mac]["min"],
        "rssi_max": signal_levels[mac]["max"],
        "tx_fail": _station_counter(station, "tx_failed"),
        "tx_bytes": _station_counter(station, "tx_bytes"),
        "tx_packets": _station_counter(station, "tx_packets"),
        "tx_bitrate": tx_bitrate,
        "rx_bytes": _station_counter(station, "rx_bytes"),
        "rx_packets": _station_counter(station, "rx_packets"),
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
    empty_stats = {
        "rx_bytes": 0,
        "rx_packets": 0,
        "tx_bytes": 0,
        "tx_packets": 0,
    }
    try:
        with open(PROC_NET_DEV, encoding="utf-8") as proc_net_dev:
            result = proc_net_dev.read()
    except OSError as exc:
        logger.message(
            "err",
            f"[{IFACE}] failed to read {PROC_NET_DEV}: {exc}",
            _EXTRA_(),
        )
        return empty_stats
    
    mlan0_data = re.search(rf"{IFACE}:\s+(\d+)\s+(\d+)\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+(\d+)\s+(\d+)", result)

    if mlan0_data:
        return {
            "rx_bytes": int(mlan0_data.group(1)),
            "rx_packets": int(mlan0_data.group(2)),
            "tx_bytes": int(mlan0_data.group(3)),
            "tx_packets": int(mlan0_data.group(4)),
        }
    return empty_stats

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
    try:
        result = subprocess.run(
            ["mlanutl", interface, "getlog"],
            capture_output=True,
            text=True,
            timeout=STAT_COMMAND_TIMEOUT_S,
            check=True,
        )
        return result.stdout
    except subprocess.TimeoutExpired:
        logger.message(
            "err",
            f"[{interface}] getlog timed out after {STAT_COMMAND_TIMEOUT_S}s",
            _EXTRA_(),
        )
        return ""
    except (subprocess.CalledProcessError, OSError) as e:
        logger.message("err", f"[{interface}] getlog failed: {e}", _EXTRA_())
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
    global bw_skip_streak
    # 대역폭을 모르는 표본(info·width 없음, 0 MHz)은 연결 정보가 채워지기 전이라 stat 줄을 쓰지 않는다(#497).
    # 세 쓰기 경로가 모두 여기를 거친다. 누적·기준·시간은 호출자가 이미 갱신해 그 구간은 다음 줄에 들어간다.
    width = re.match(r"\s*(\d+)", str(wifi_info.get("bw") or ""))
    if not width or int(width.group(1)) == 0:
        bw_skip_streak += 1
        if bw_skip_streak == BW_SKIP_WARN_STREAK:
            logger.message(
                "warn",
                f"[{IFACE}] stat line skipped {bw_skip_streak} times in a row: "
                f"bandwidth unknown (bw={wifi_info.get('bw')!r})",
                _EXTRA_(),
            )
        return
    bw_skip_streak = 0

    #tx_retry = my_stat['tx_retry']

    #tx_retry_per = round((tx_retry/my_stat['tx_packets'])*100, 2) if my_stat['tx_packets'] else 0
    #tx_retry_per = round((tx_retry/my_stat['tx_frame_cnt'])*100, 2) if my_stat['tx_frame_cnt'] else 0
    #if tx_retry_per > 100:
    #    tx_retry_per = 100
        
    log_entry = (
        f"{time.strftime('%Y-%m-%d %H:%M:%S')} "
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
    
    # 줄에 MAC 이 있어 stat.log 만으로 AP 별 줄을 뽑을 수 있다. AP 별 파일은 쓰지 않는다(#494).
    all_log_filename = f"{LOG_DIR}/stat.log"

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
                        f"{time.strftime('%Y-%m-%d %H:%M:%S')} "
                        f"AP changed: {current_ap} -> {ap_mac}\n"
                    )
                    current_ap = ap_mac
                    #logger.message("info", f"[{IFACE}] AP change: {current_ap} -> {ap_mac}", _EXTRA_())
                    with open(all_log_filename, "a") as all_log_file:
                        all_log_file.write(log_entry)
                time.sleep(1)
                continue
            else:
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

        #mac_address = ap_mac.replace(":", "_")  # File-safe MAC format        
        all_log_filename = f"{LOG_DIR}/stat.log"

        # Detect AP change
        if current_ap != ap_mac:
            #print(f"🔄 AP changed: {current_ap} -> {ap_mac}")
            #old_mac_address = current_ap.replace(":", "_")
            #old_log_filename = f"{LOG_DIR}/{old_mac_address}.log"
            log_entry = (
                f"{time.strftime('%Y-%m-%d %H:%M:%S')} "
                f"AP changed: {current_ap} -> {ap_mac}\n"
            )
            #logger.message("info", f"[{IFACE}] AP change: {current_ap} -> {ap_mac}", _EXTRA_())
            current_ap = ap_mac
            with open(all_log_filename, "a") as all_log_file:
                all_log_file.write(log_entry)

            # AP 가 바뀌면 카운터 기준만 이 표본의 드라이버 값으로 다시 잡는다 — 로밍 때 드라이버는
            # 카운터를 이어 세기도(tx failed) 다시 세기도 한다(#497). 모르는 값(None)은 처음 보일 때 기준이 된다.
            prev_stat[ap_mac] = {key: wifi_info[key] for key in STAT_COUNTERS}

            # AP별 누적(바이트·패킷·FAIL·평균 속도·시간)과 RSSI 최소·최대는 이 프로세스 메모리
            # (last_stat·signal_levels)에서 이어 센다(#494). 처음 보는 AP 는 0 부터 세고,
            # 로거가 다시 시작하면 모든 AP 가 0 부터다.
            last_stat.setdefault(ap_mac, {"rx_bytes": 0, "rx_packets": 0, "tx_bytes": 0, "tx_packets": 0, "tx_fail": 0, "time": 0, "tx_avg_bps": 0, "rx_avg_bps": 0, "total_bps": 0})

        # 초기화 요청: wifi_stat_init_f_<iface>(logrotate 매일 회전·`wifi <if> stat reset`)는 이 로거의
        # AP 전체를, wifi_stat_reset_<소문자 MAC>(`wifi <if> stat reset <MAC>`)은 그 AP 만 비운다.
        init_flagfile = f"/tmp/wifi_stat_init_f_{IFACE}"
        mac_flagfile = f"/tmp/wifi_stat_reset_{ap_mac.lower()}"
        cond_time = last_stat[ap_mac]["time"] >= STAT_RESET_INTERVAL
        cond_flagfile = os.path.exists(init_flagfile)
        cond_mac_flagfile = os.path.exists(mac_flagfile)
        reset_flag = cond_time or cond_flagfile or cond_mac_flagfile

        #print(f"current_ap : {current_ap} , ap_mac : {ap_mac}")
        # Update RX/TX·FAIL stats: 카운터마다 직전 표본 대비 증가분을 더한다(#497). 드라이버 값이 기준보다
        # 작으면 그 카운터를 다시 센 것(재연결)으로 보고 현재값을 더한다. 드라이버가 카운터마다 다시 세는
        # 시점이 달라(보드 1 로밍: tx failed·tx 패킷은 이어지고 비콘 카운터는 0 부터) 카운터 하나가 줄었다고 전체를 초기화로 보지 않는다.
        # 값을 모르면(None) 더하지도 기준을 바꾸지도 않고, 다시 보이면 마지막으로 안 기준과 비교한다.
        base = prev_stat[ap_mac]
        for key in STAT_COUNTERS:
            cur = wifi_info[key]
            if cur is None:
                continue
            if base.get(key) is not None:
                last_stat[ap_mac][key] += cur - base[key] if cur >= base[key] else cur
            base[key] = cur
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
                f"{time.strftime('%Y-%m-%d %H:%M:%S')} "
                f"MAC:{ap_mac} stat init!!\n"
            )
            #logger.message("info", f"[{IFACE}] AP change: {current_ap} -> {ap_mac}", _EXTRA_())
            with open(all_log_filename, "a") as all_log_file:
                all_log_file.write(log_entry)

            # FAIL 도 0 부터 다시 센다(#497). 카운터 기준(prev_stat)은 이번 표본의 드라이버 값 그대로라
            # 다음 표본은 그 뒤의 증가분만 더한다.
            last_stat[ap_mac]["tx_fail"] = 0
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
                # iface 초기화 요청은 끊겨 있던 AP 의 누적도 버린다 — 다시 연결되면 0 부터 센다.
                for table in (last_stat, prev_stat, signal_levels):
                    for mac in [k for k in table if k != ap_mac]:
                        del table[mac]
                try:
                    os.remove(init_flagfile)
                except FileNotFoundError:
                    pass
            if cond_mac_flagfile:
                try:
                    os.remove(mac_flagfile)
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
        logger.message("warn", f"[{IFACE}] lock file open failed: {e} - exit", _EXTRA_())
        sys.exit(1)   # open 실패는 운영 에러(권한/mount) — 중복 회피 exit 0과 구분
    _locked = False
    for _ in range(5):
        try:
            fcntl.flock(_lock_fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _locked = True
            break
        except OSError:
            time.sleep(1)
    if not _locked:
        logger.message("warn", f"[{IFACE}] another wifi_logger_stat already running - exit", _EXTRA_())
        # exit 3 = 중복 실행(flock-loss). systemd 유닛의 RestartPreventExitStatus=3 이 이
        # 종료를 재시작하지 않게 해 재시작 폭주를 막는다(이미 다른 인스턴스가 생산 중).
        sys.exit(3)

    load_logger_config(IFACE)

    LOG_DIR = f"/var/log/cantops/stat/{IFACE}"
    logger.message("info", f"[{IFACE}] version : {VERSION}, log_file : {LOG_DIR}/stat.log", _EXTRA_())
        
    # 로그 디렉토리 생성
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR, exist_ok=True)
        
    log_stats()
