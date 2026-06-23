import subprocess
import json
import time
import re
import sys
import os
import signal
import tempfile
import shutil
import argparse
from datetime import datetime
import logging
from sUTILS import Logger, _EXTRA_

VERSION = "0.3"
IFACE = ""
LOG_DIR = "/var/log/cantops/json"
LINK_PATH = "/var/log/cantops/json"
TARGET_PATH = "/dev/shm/json"
MWLAN_LOG_PATH = "/proc/mwlan/adapter0/mlan0/log"
LOOP_INTERVAL = 0.95	#0.965
SPIKE_THRESHOLD_FAIL = 1
SPIKE_THRESHOLD_RETRY = 10
# reconfigure/select_network 직후 100~200ms 순간 끊김(station dump 일시적 공백)을
# "끊김"으로 표시하지 않기 위한 빠른 재시도. count*delay 가 끊김 무시 윈도우(기본 ~200ms).
LINK_RETRY_COUNT = 4
LINK_RETRY_DELAY = 0.05

def handle_sigterm(signum, frame):
    logger.message('crit', f"[{IFACE}] SIGTERM {signum} received! Cleaning up...", _EXTRA_())
    cleanup()
    sys.exit(0)

def cleanup():
    pass

def remove_any(path):
    if not os.path.lexists(path):
        return  #     ^~  ^u^x  ^`  ^u^j ^|      ^u^d     ^c ^o^d  ^u^h  ^u

    logger.message('info', f"[{IFACE}] remove {path}", _EXTRA_())

    if os.path.islink(path) or os.path.isfile(path):
        os.remove(path)          #  ^l^l ^}   ^x^p ^j^t  ^k          ^a ^a   ^b   ^|
    elif os.path.isdir(path):
        shutil.rmtree(path)      #  ^t^t  ^i ^d       ^d     ^b   ^|
    else:
        os.unlink(path)          #     ^c^`  ^j  ^h^x  ^l^l ^}

def compact_lists(text):
    pattern = re.compile(r'\[\s*\n\s*((?:.+,\s*\n)+)\s*(.+?)\s*\]')
    return pattern.sub(lambda m: '[' + re.sub(r'\s+', ' ', m.group(1) + m.group(2)).strip() + ']', text)

def save_db(db, dir=LOG_DIR):
    raw_json = json.dumps(db, indent=2)
    compacted_json = compact_lists(raw_json)

    tmp_path = os.path.join(dir, "link.json.tmp")
    final_path = os.path.join(dir, "link.json")

    with open(tmp_path, 'w') as f:
        f.write(compacted_json)
        f.flush()
        os.fsync(f.fileno())  # 디스크에 flush 보장

    os.rename(tmp_path, final_path)  # atomic한 rename

'''
def save_db(db):
    def compact_lists(text):
        pattern = re.compile(r'\[\s*\n\s*((?:.+,\s*\n)+)\s*(.+?)\s*\]')
        return pattern.sub(lambda m: '[' + re.sub(r'\s+', ' ', m.group(1) + m.group(2)).strip() + ']', text)

    raw_json = json.dumps(db, indent=2)
    compacted_json = compact_lists(raw_json)
    
    with open(f"{LOG_DIR}/link.json", "w") as f:
        f.write(compacted_json)
'''

def validate_station(output):
    return "Station" in output or "signal" in output

def validate_info(output):
    return "ssid" in output or "type" in output

def validate_survey(output):
    return "channel" in output or "frequency" in output

def run_command_with_retry(cmd, retries=2, delay=0.1, validate_fn=None):
    for attempt in range(1, retries + 1):
        output = run_command(cmd)
        
        if output is None or output.strip() == "":
            #logger.message("warn", f"{cmd} -> empty result (attempt {attempt})", _EXTRA_())
            pass
        elif validate_fn is not None and not validate_fn(output):
            logger.message("warn", f"[{IFACE}] {cmd} -> failed validation (attempt {attempt})", _EXTRA_())
            #pass
        else:
            return output

        if attempt < retries:
            time.sleep(delay)

    #logger.message("err", f"[{IFACE}] {cmd} -> all {retries} attempts failed", _EXTRA_())
    return None

def run_command(cmd):
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(f"Command failed: {e}")
        return ""

def fast_retry_station_dump(count, delay):
    """station dump가 비었을 때(reconfigure/select_network 직후 순간 끊김 의심)
    짧은 간격(delay)으로 최대 count회 빠르게 재조회한다.

    윈도우(count*delay, 기본 ~200ms) 안에서 station이 회복되면 그 출력을 반환하고,
    끝까지 비어 있으면 None을 반환한다(진짜 끊김으로 판정). 정상 연결 중에는 호출되지
    않으므로 주기 오버헤드가 없다."""
    for _ in range(count):
        time.sleep(delay)
        out = run_command(["iw", IFACE, "station", "dump"])
        if out and validate_station(out):
            return out
    return None

def parse_mwlan_log():
    parsed = {}
    try:
        with open(MWLAN_LOG_PATH, "r") as f:
            for line in f:
                if "=" not in line:
                    continue
                key, value = map(str.strip, line.split("=", 1))
                if " " in value:
                    values = value.strip().split()
                    values = [int(v) for v in values if v.isdigit()]
                    parsed[key] = values
                else:
                    parsed[key] = int(value.strip())
    except Exception as e:
        parsed["error"] = str(e)

    return parsed

address = ""

def parse_iw_info(output):
    result = {}
    for line in output.splitlines():
        if "addr" in line:
            result["address"] = line.split()[-1]
        elif line.strip().startswith("ssid "):
            parts = line.split(None, 1)
            if len(parts) == 2:
                result["ssid"] = parts[1].strip()
        elif "channel" in line:
            match = re.search(r"channel\s+(\d+)\s+\(([\d]+) MHz\),\s+width:\s+([\d]+ MHz)", line)
            if match:
                result["channel"] = int(match.group(1))
                result["freq"] = int(match.group(2))
                result["width"] = match.group(3)
        elif "txpower" in line:
            result["txpower"] = line.split()[-2]  # Remove dBm
            
    return result

def parse_station_dump(output):
    global address
    result = {}
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("Station "):
            result["address"] = stripped.split("Station")[1].split("(")[0].strip()
            address = result["address"]
            continue
        key_value = stripped.split(":", 1)
        if len(key_value) == 2:
            key = key_value[0].strip().replace(" ", "_")
            value = key_value[1].strip()
            result[key] = value
    return result

def parse_survey_dump(output):
    lines = output.strip().splitlines()
    survey_data = {}
    current_freq = None

    for line in lines:
        line = line.strip()
        if line.startswith("frequency:"):
            freq_mhz = int(re.findall(r'\d+', line)[0])
            current_freq = str(freq_mhz)
            survey_data[current_freq] = {}
        elif line.startswith("noise:"):
            noise = int(re.findall(r'-?\d+', line)[0])
            survey_data[current_freq]["noise"] = noise
        elif line.startswith("channel active time:"):
            active_time = int(re.findall(r'\d+', line)[0])
            survey_data[current_freq]["active_time_ms"] = active_time
        elif line.startswith("channel busy time:"):
            busy_time = int(re.findall(r'\d+', line)[0])
            survey_data[current_freq]["busy_time_ms"] = busy_time

    return survey_data

def get_ip_info(iface):
    info = {
        "ip_address": None,
        "netmask": None,
        "gateway": None,
    }

    ip_out = run_command(["ip", "addr", "show", iface])
    if ip_out:
        for line in ip_out.splitlines():
            line = line.strip()
            if line.startswith("inet "):
                m = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)/(\d+)", line)
                if m:
                    info["ip_address"] = m.group(1)
                    cidr = int(m.group(2))
                    netmask = [str((0xffffffff << (32 - cidr) >> i) & 0xff) for i in (24, 16, 8, 0)]
                    info["netmask"] = ".".join(netmask)
                    break

    route_out = run_command(["ip", "route", "show", "default", "dev", iface])
    if route_out:
        m = re.search(r"default\s+via\s+(\d+\.\d+\.\d+\.\d+)", route_out)
        if m:
            info["gateway"] = m.group(1)

    return info

def parse_eth_info(iface):
    stats = {
        "mac_address": None,
        "ip_address": None,
        "netmask": None,
        "gateway": None,
        "rx_packets": 0,
        "rx_bytes": 0,
        "rx_errors": 0,
        "rx_dropped": 0,
        "tx_packets": 0,
        "tx_bytes": 0,
        "tx_errors": 0,
        "tx_dropped": 0
    }

    ip_out = run_command(["ip", "addr", "show", iface])
    for line in ip_out.splitlines():
        line = line.strip()
        if line.startswith("link/ether"):
            m = re.search(r"link/ether\s+([0-9a-f:]+)", line)
            if m:
                stats["mac_address"] = m.group(1)

    stats.update(get_ip_info(iface))

    # traffic
    with open("/proc/net/dev") as f:
        for line in f:
            if iface + ":" in line:
                parts = line.split(f"{iface}:", 1)[1].split()
                stats.update({
                    "rx_bytes": int(parts[0]),
                    "rx_packets": int(parts[1]),
                    "rx_errors": int(parts[2]),
                    "rx_dropped": int(parts[3]),
                    "tx_bytes": int(parts[8]),
                    "tx_packets": int(parts[9]),
                    "tx_errors": int(parts[10]),
                    "tx_dropped": int(parts[11])
                })
    return stats

def parse_eth_phy(iface):
    out = run_command(["ethtool", iface])
    result = {}

    for line in out.splitlines():
        line = line.strip()
        if line.startswith("Speed:"):
            result["speed"] = line.split(":", 1)[1].strip()
        elif line.startswith("Duplex:"):
            result["duplex"] = line.split(":", 1)[1].strip()
        elif line.startswith("Port:"):
            result["port"] = line.split(":", 1)[1].strip()
        elif line.startswith("Link detected:"):
            val = line.split(":", 1)[1].strip()
            result["link"] = "up" if val == "yes" else "down"

    return result

def is_wpa_running(interface="mlan0"):
    return os.path.exists(f"/run/wpa_supplicant/{interface}")

def is_wifi_connected_wpa(interface="mlan0") -> bool:
    try:
        result = subprocess.check_output(["wpa_cli", "-i", interface, "status"], encoding="utf-8")
        for line in result.splitlines():
            if line.startswith("wpa_state="):
                state = line.split("=")[1]
                return state == "COMPLETED"
        return False
    except subprocess.CalledProcessError:
        return False

def is_wifi_connected_iw(interface="mlan0") -> bool:
    try:
        # iw <iface> link 실행
        output = subprocess.check_output(
            ["iw", interface, "link"],
            stderr=subprocess.DEVNULL,
            encoding="utf-8"
        )
        
        return "Connected to" in output

    except (subprocess.CalledProcessError, FileNotFoundError):
        return False

_prev_tx_failed = None
_prev_tx_retries = None

def check_tx_spike(link_data):
    """tx_failed / tx_retries 급증 감지 → syslog 기록"""
    global _prev_tx_failed, _prev_tx_retries

    try:
        cur_fail = int(link_data.get("tx_failed", 0))
        cur_retry = int(link_data.get("tx_retries", 0))
    except (ValueError, TypeError):
        return

    if _prev_tx_failed is not None:
        d_fail = cur_fail - _prev_tx_failed
        d_retry = cur_retry - _prev_tx_retries
        if d_fail >= SPIKE_THRESHOLD_FAIL:
            logger.message("warn",
                f"[{IFACE}] TX_FAIL spike: +{d_fail} (total {cur_fail})", _EXTRA_())
        if d_retry >= SPIKE_THRESHOLD_RETRY:
            logger.message("warn",
                f"[{IFACE}] TX_RETRY spike: +{d_retry} (total {cur_retry})", _EXTRA_())

    _prev_tx_failed = cur_fail
    _prev_tx_retries = cur_retry


_empty_json = b'{}\n'

def _write_empty_link():
    path = os.path.join(LOG_DIR, "link.json")
    with open(path, 'wb') as f:
        f.write(_empty_json)

def main():
    os.makedirs(LOG_DIR, exist_ok=True)
    while True:
        if not os.path.exists(f"/sys/class/net/{IFACE}"):
            logger.message("info", f"[{IFACE}] waiting for interface...", _EXTRA_())
            time.sleep(1)
            continue

        if IFACE == "eth0":
            eth_stats = {
                "info": parse_eth_info("eth0"),
                "phy": parse_eth_phy("eth0")
            }
            data = {
                "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "eth_stats": eth_stats
            }
            save_db(data, LOG_DIR)
            time.sleep(LOOP_INTERVAL)
            continue

        if not is_wpa_running(IFACE):
            _write_empty_link()
            time.sleep(1)
            continue

        # station dump로 연결 판별 (iw link 대체)
        link_out = run_command_with_retry(["iw", IFACE, "station", "dump"], validate_fn=validate_station)
        if not link_out:
            # reconfigure/select_network로 인한 100~200ms 순간 끊김을 끊김으로 표시하지
            # 않도록 빠르게 재시도. 윈도우 안에서 회복되면 정상 처리로 진행한다.
            link_out = fast_retry_station_dump(LINK_RETRY_COUNT, LINK_RETRY_DELAY)
        if not link_out:
            _write_empty_link()
            time.sleep(1)
            continue

        link_data = parse_station_dump(link_out)

        # iw info는 매 주기 호출 (ssid·channel·width·txpower가 같은 AP에서도 변할 수 있어 캐시 시 stale)
        info_out = run_command_with_retry(["iw", IFACE, "info"], validate_fn=validate_info)

        channel_out = run_command(["iw", IFACE, "survey", "dump"])
        channel_data = parse_survey_dump(channel_out) if channel_out else {}

        check_tx_spike(link_data)

        info_data = parse_iw_info(info_out) if info_out else {}
        info_data.update(get_ip_info(IFACE))

        data = {
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "info": info_data,
            "link": link_data,
            "channel_info": channel_data,
            "mwlan_log": parse_mwlan_log()
        }

        save_db(data, LOG_DIR)
        time.sleep(LOOP_INTERVAL)

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, handle_sigterm)
    logger = Logger(app_name="LINK", facility=logging.handlers.SysLogHandler.LOG_LOCAL0)

    parser = argparse.ArgumentParser()
    parser.add_argument("iface", nargs="?", default="mlan0",
                        choices=["mlan0", "mlan1", "eth0"],
                        help="Interface name")
    parser.add_argument("--interval", type=float, default=0.965,
                        help="Main loop interval in seconds (default: 0.965)")
    parser.add_argument("--spike-fail", type=int, default=1,
                        help="TX fail spike threshold per cycle (default: 1)")
    parser.add_argument("--spike-retry", type=int, default=10,
                        help="TX retry spike threshold per cycle (default: 10)")
    parser.add_argument("--link-retry-count", type=int, default=LINK_RETRY_COUNT,
                        help=f"Fast-retry count when station dump is momentarily empty "
                             f"(reconfigure/select_network blip suppression, default: {LINK_RETRY_COUNT})")
    parser.add_argument("--link-retry-delay", type=float, default=LINK_RETRY_DELAY,
                        help=f"Fast-retry delay between attempts in seconds (default: {LINK_RETRY_DELAY})")
    args = parser.parse_args()

    IFACE = args.iface

    # Load tunables from JSON: {iface}.logger.<key> → logger.<key> → arg default
    # link_retry_* 키는 아직 기본 JSON에 없어도 무방하다(없으면 arg/모듈 기본값 사용).
    _link_interval = args.interval
    _retry_count = args.link_retry_count
    _retry_delay = args.link_retry_delay
    try:
        with open("/usr/local/etc/wifi_init_conf.json") as _f:
            _conf = json.load(_f)
        _global = _conf.get("logger", {}).get("link_interval_sec", _link_interval)
        _link_interval = _conf.get(IFACE, {}).get("logger", {}).get("link_interval_sec", _global)
        _g_cnt = _conf.get("logger", {}).get("link_retry_count", _retry_count)
        _retry_count = _conf.get(IFACE, {}).get("logger", {}).get("link_retry_count", _g_cnt)
        _g_dly = _conf.get("logger", {}).get("link_retry_delay_sec", _retry_delay)
        _retry_delay = _conf.get(IFACE, {}).get("logger", {}).get("link_retry_delay_sec", _g_dly)
    except (OSError, json.JSONDecodeError) as e:
        print(f"WARN: [{IFACE}] config load failed, using defaults: {e}", file=sys.stderr)

    LOOP_INTERVAL = _link_interval
    SPIKE_THRESHOLD_FAIL = args.spike_fail
    SPIKE_THRESHOLD_RETRY = args.spike_retry
    try:
        LINK_RETRY_COUNT = max(0, int(_retry_count))
        LINK_RETRY_DELAY = max(0.0, float(_retry_delay))
    except (ValueError, TypeError):
        print(f"WARN: [{IFACE}] invalid link_retry_* config, using defaults "
              f"({args.link_retry_count}/{args.link_retry_delay})", file=sys.stderr)
        LINK_RETRY_COUNT = args.link_retry_count
        LINK_RETRY_DELAY = args.link_retry_delay

    LOG_DIR = f"/var/log/cantops/json/{IFACE}"
    logger.message("info",
        f"[{IFACE}] version: {VERSION}, interval: {LOOP_INTERVAL}s, "
        f"spike_fail: {SPIKE_THRESHOLD_FAIL}, spike_retry: {SPIKE_THRESHOLD_RETRY}, "
        f"link_retry: {LINK_RETRY_COUNT}x{LINK_RETRY_DELAY}s, "
        f"log: {LOG_DIR}/link.json", _EXTRA_())

    if IFACE == "mlan0":
        MWLAN_LOG_PATH = "/proc/mwlan/adapter0/mlan0/log"
    elif IFACE == "mlan1":
        MWLAN_LOG_PATH = "/proc/mwlan/adapter1/mlan1/log"
    elif IFACE == "eth0":
        MWLAN_LOG_PATH = ""
    else:
        logger.message("emerg", f"[{IFACE}] is not valid interface", _EXTRA_())
        sys.exit(1)

    if not os.path.exists(TARGET_PATH):
        os.makedirs(TARGET_PATH, exist_ok=True)

    if not os.path.islink(LINK_PATH):
        remove_any(LINK_PATH)
        logger.message("err", f"[{IFACE}] symbolic link {LINK_PATH} with {TARGET_PATH}", _EXTRA_())
        os.symlink(TARGET_PATH, LINK_PATH)

    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR, exist_ok=True)

    main()
