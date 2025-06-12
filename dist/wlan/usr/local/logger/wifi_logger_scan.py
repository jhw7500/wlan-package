import subprocess
import re
import os
import time
import random
import logging
import sys
import json
import signal
import threading
from datetime import datetime
from sUTILS import Logger, _EXTRA_

LOG_DIR = "/var/log/cantops/scan"
WPA_CONF_FILE = f"/etc/wpa_supplicant/wpa_supplicant-mlan0.conf"
LOG_INTERVAL = 30
STALE_THRESHOLD_SEC = 600  #1hour
#last_log_time = 0
VERSION = "0.0"
IFACE = ""

def handle_sigterm(signum, frame):
    logger.message('crit', f"[{IFACE}] SIGTERM {signum} received! Cleaning up...", _EXTRA_())
    cleanup()
    sys.exit(0)

def cleanup():
    pass

def parse_wpa_supplicant_conf(path):
    ssid = None
    freqs = []
    scan_interval = 30  # 기본값

    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if line.startswith("ssid=") and not line.startswith("#"):
                try:
                    ssid = line.split("=", 1)[1].strip().strip('"')
                except ValueError:
                    logger.message('err', f"[{IFACE}] ssid : {ssid} is invalid in {path}", _EXTRA_())
                    pass
            elif line.startswith("scan_freq=") and not line.startswith("#"):
                try:
                    freqs = line.split("=", 1)[1].strip().split()
                except ValueError:
                    logger.message('err', f"[{IFACE}] scan_freq : {freqs} is invalid in {path}", _EXTRA_())
                    pass
            elif line.startswith("#!bgscan="): #and not line.startswith("#"):
                parts = line.split("=", 1)[1].strip().strip('"').split(":")
                if len(parts) == 4:  # bgscan="simple:X:Y:Z"
                    try:
                        scan_interval = int(parts[3])
                    except ValueError:
                        logger.message('err', f"[{IFACE}] scan_interval : {scan_interval} is invalid in {path}", _EXTRA_())
                        pass

    return ssid, freqs, scan_interval

def periodic_scan(ssid, freqs, interval):
    if not ssid or not freqs:
        logger.message("err", f"[{IFACE}] invalid config: ssid='{ssid}', freqs={freqs}", _EXTRA_())
        return

    if not isinstance(interval, int) or interval <= 0:
        logger.message("err", f"[{IFACE}] invalid scan interval: {interval}, using default 30s", _EXTRA_())
        interval = 30

    while True:
        try:
            cmd = ["iw", IFACE, "scan", "freq"] + freqs + ["ssid", ssid]
            result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        except subprocess.CalledProcessError as e:
            logger.message("err", f"[{IFACE}] scan command failed: {e}", _EXTRA_())
        except Exception as e:
            logger.message("err", f"[{IFACE}] unexpected scan error: {type(e).__name__}: {e}", _EXTRA_())

        time.sleep(interval)

'''
def periodic_scan(ssid, freqs, interval):
    if not ssid or not freqs:
        logger.message("err", f"[{IFACE}] invalid config: ssid='{ssid}', freqs={freqs}", _EXTRA_())
        return

    if not isinstance(interval, int) or interval <= 0:
        logger.message("err", f"[{IFACE}] invalid scan interval: {interval}, using default 30s", _EXTRA_())
        interval = 30

    while True:
        try:
            # mlan0일 때만 systemctl 중지
            if IFACE == "mlan0":
                subprocess.run(["systemctl", "stop", "wifi_capture"], check=True)
                #logger.message("info", f"[{IFACE}] systemctl stop wifi_capture before scan", _EXTRA_())

            # 스캔 실행
            cmd = ["iw", IFACE, "scan", "freq"] + freqs + ["ssid", ssid]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            logger.message("info", f"[{IFACE}] scan : {cmd}", _EXTRA_())

        except subprocess.CalledProcessError as e:
            logger.message("err", f"[{IFACE}] command failed: {e}", _EXTRA_())
        except Exception as e:
            logger.message("err", f"[{IFACE}] unexpected error: {type(e).__name__}: {e}", _EXTRA_())
        finally:
            # mlan0일 때만 systemctl 재시작
            if IFACE == "mlan0":
                try:
                    subprocess.run(["systemctl", "start", "wifi_capture"], check=True)
                    #logger.message("info", f"[{IFACE}] systemctl start wifi_capture after scan", _EXTRA_())
                except subprocess.CalledProcessError as e:
                    logger.message("err", f"[{IFACE}] failed to restart wifi_capture: {e}", _EXTRA_())

        time.sleep(interval)
'''

def get_last_dmesg_line_count():
    result = subprocess.run(["dmesg"], stdout=subprocess.PIPE, text=True)
    return len(result.stdout.strip().splitlines())

def scan_event(interface, on_event_callback):
    last_line_count = get_last_dmesg_line_count()

    with subprocess.Popen(["dmesg", "--follow"], stdout=subprocess.PIPE, text=True, bufsize=1) as dmesg_proc:
        scan_started = False
        current_line = 0
        for line in dmesg_proc.stdout:
            current_line += 1
            if current_line <= last_line_count:
                continue

            if "wlan:" not in line:
                continue

            #print(line.strip())

            if f"wlan: {interface} START SCAN" in line:
                scan_started = True

            if scan_started and "wlan: SCAN COMPLETED" in line:
                #logger.message("info", f"{interface} SCAN COMPLETED", _EXTRA_())
                on_event_callback(interface)
                scan_started = False


def monitor_nl80211_scan_event(on_event_callback):
    cmd = ["tshark", "-i", "nlmon0", "-l", "-Y", "nl80211.cmd == 50"]
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True) as proc:
        for line in proc.stdout:
            if line.strip():
                on_event_callback()

def run_setuserscan():
    try:
        result = subprocess.run(
            ['mlanutl', IFACE, 'setuserscan', 'sort_by_ch'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True  # ← returncode != 0이면 예외 발생
        )
        return result.stdout.splitlines()
    except subprocess.CalledProcessError as e:
        logger.message("err", f"[{IFACE}] setuserscan failed: {e}", _EXTRA_())
        return []

def run_iwdevscandump(retries=3, delay=1):
    for attempt in range(1, retries + 1):
        try:
            result = subprocess.run(
                ['iw', 'dev', IFACE, 'scan', 'dump'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True
            )
            return result.stdout.splitlines()
        except subprocess.CalledProcessError as e:
            logger.message("err", f"[{IFACE}] {attempt}/{retries} scan dump failed: {e}", _EXTRA_())
            if attempt < retries:
                time.sleep(delay)
            else:
                logger.message("err", f"[{IFACE}] All retry attempts for scan dump failed", _EXTRA_())
                return "err"

def run_getscantable(retries=3, delay=1):
    time.sleep(delay)
    for attempt in range(1, retries + 1):
        try:
            result = subprocess.run(
                ['mlanutl', IFACE, 'getscantable'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True
            )
            return result.stdout.splitlines()
        except subprocess.CalledProcessError as e:
            logger.message("err", f"[{IFACE}] {attempt}/{retries} getscantable failed: {e}", _EXTRA_())
            if attempt < retries:
                time.sleep(delay)
            else:
                logger.message("err", f"[{IFACE}] All retry attempts for getscantable failed", _EXTRA_())
                return []

def extract_ap_table(lines):
    bss_section = []
    sep_count = 0
    for line in lines:
        clean = line.strip()

        # 채널 테이블 시작되면 종료
        if '# | Channel' in clean:
            break

        # 구분선
        if re.match(r'^-+$', clean):
            sep_count += 1
            if sep_count <= 2:
                bss_section.append(line)
            continue

        # 헤더 라인 (컬럼 설명)
        if sep_count == 1 and re.match(r'^#', clean):
            bss_section.append(line)
            continue

        # 본문 이전은 무시
        if sep_count < 2:
            continue

        # 본문 중 AC/AX 포함 라인 필터
        #if re.search(r'AC|AX', clean):
        #    bss_section.append(line)

        bss_section.append(line)

    return bss_section

def extract_channel_table(lines):
    chan_section = []
    sep_count = 0
    for line in lines:
        clean = line.strip()

        # 헤더 구분선 3~4번째 줄은 그대로 추가
        if re.match(r'^-+$', clean):
            sep_count += 1
            if 3 <= sep_count <= 4:
                chan_section.append(line)
            continue

        # 헤더 라인 (# | Channel ...)
        if sep_count == 3 and re.match(r'^#', clean):
            chan_section.append(line)
            continue

        # 데이터 본문
        if sep_count >= 4 and clean:
            chan_section.append(line)

    return chan_section

def save_with_timestamp(prefix, content_lines):
    filename = os.path.join(LOG_DIR, f"{prefix}.log")
    #logger.message("info", f"{filename}", _EXTRA_())
    timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    '''
    header = f"===== [{timestamp_str}] {'=' * (60 - len(timestamp_str) - 10)}"

    # 로그 파일 append
    with open(filename, 'a') as f:
        f.write(header + '\n')
        for line in content_lines:
            f.write(line.rstrip() + '\n')
        f.write('\n')  # 블럭 구분용 줄
    '''
    header = f"[{timestamp_str}]"
    with open(filename, 'a') as f:
        f.write(header + '\n')
        for line in content_lines:
            f.write(line.rstrip() + '\n')
            #logger.message("info", f"{line.rstrip()}", _EXTRA_()) 
        f.write('\n')
    
    #print(f"✔ {filename} 로그에 추가됨")
    return filename

def none_if_empty(d):
    return d if d else None

def parse_date_str(date_str):
    dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
    return int(dt.timestamp())

def remove_stale_entries(db, now_ts):
    cleaned = {}
    for bssid, info in db.items():
        last_seen_str = info.get("date")
        if not last_seen_str:
            continue
        last_seen_ts = parse_date_str(last_seen_str)
        #logging.info(f"now_ts : {now_ts}, last_seen_ts : {last_seen_ts}")
        if now_ts - last_seen_ts < STALE_THRESHOLD_SEC:
            cleaned[bssid] = info
        else:
            logger.message("notice", f"[{IFACE}] BSSID {bssid} removed (last seen {info['date']})", _EXTRA_())
    return cleaned


def extract_ht_capabilities_block(text):
    ht_block = re.search(r'HT capabilities:\n((?:\s+.+\n)+?)HT operation:', text, re.DOTALL)
    if not ht_block:
        return {}

    block = ht_block.group(1)
    result = {}

    # Capabilities: 0x9ef
    cap_match = re.search(r'Capabilities:\s+(0x[0-9a-fA-F]+)', block)
    if cap_match:
        result['capabilities'] = cap_match.group(1)

    # 플래그 목록
    flags = re.findall(r'\n\s{24}(.+)', block)
    result['flags'] = [flag.strip() for flag in flags if not flag.startswith('Max ') and not flag.startswith('No ')]

    # Max AMSDU, AMPDU, SGI 등
    max_amsdu = re.search(r'Max AMSDU length:\s+(\d+)', block)
    if max_amsdu:
        result['max_amsdu'] = int(max_amsdu.group(1))

    rx_ampdu = re.search(r'Maximum RX AMPDU length\s+(\d+)', block)
    if rx_ampdu:
        result['rx_ampdu_len'] = int(rx_ampdu.group(1))

    spacing = re.search(r'Minimum RX AMPDU time spacing:\s+(.+)', block)
    if spacing:
        result['rx_ampdu_spacing'] = spacing.group(1).strip()

    mcs = re.search(r'HT TX/RX MCS rate indexes supported:\s+(.+)', block)
    if mcs:
        result['mcs'] = mcs.group(1).strip()

    return result

def extract_ht_operation_block(text):
    ht_block = re.search(r'HT operation:\n((?:\s+\* .+\n)+)', text)
    if not ht_block:
        return {}
    block = ht_block.group(1)
    result = {}
    for line in block.strip().splitlines():
        line = line.strip().lstrip("*").strip()
        if ": " in line:
            key, value = line.split(": ", 1)
            key = key.strip().lower().replace(" ", "_")  # 키 정제
            result[key] = value.strip()
    return result

def extract_vht_operation_block(text):
    vht_op_match = re.search(r'VHT operation:\n((?:\s+\* .+\n)+)', text)
    if not vht_op_match:
        return {}

    block = vht_op_match.group(1)
    result = {}
    for line in block.strip().splitlines():
        line = line.strip().lstrip("*").strip()
        if ": " in line:
            key, value = line.split(": ", 1)
            key = key.strip().lower().replace(" ", "_")
            result[key] = value.strip()
    return result

def parse_scan_output(scan_output):
    if scan_output is None:
        logger.message("warn", f"[{IFACE}] scan_output is None", _EXTRA_())
        return {}

    if isinstance(scan_output, list):
        scan_output = "\n".join(scan_output)

    if not scan_output.strip():
        logger.message("warn", f"[{IFACE}] parse_scan_output received empty scan data", _EXTRA_())
        return {}

    if not scan_output or not isinstance(scan_output, str):
        raise ValueError("Invalid scan output: expected non-empty string")

    bss_blocks = re.split(r'\n(?=BSS )', scan_output)
    result = {}

    for block in bss_blocks:
        bssid_match = re.search(r'BSS ([0-9a-f:]{17})', block)
        if not bssid_match:
            continue
        bssid = bssid_match.group(1)
        info = {}

        def extract(pattern, cast=str, default=None):
            m = re.search(pattern, block)
            return cast(m.group(1)) if m else default

        info['date'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        info['tsf'] = extract(r'TSF: (\d+)', int)
        info['last_seen'] = extract(r'last seen: (\d+)', int)
        info['ssid'] = extract(r'SSID: (.+)')
        info['freq'] = extract(r'freq: (\d+)', int)
        info['signal'] = extract(r'signal: (-?\d+\.\d+)', lambda x: int(float(x)))
        info['channel'] = extract(r'primary channel: (\d+)', int)
        info['beacon_interval'] = extract(r'beacon interval: (\d+)', int)
        info['country'] = extract(r'Country: (.+)\t')

        # Supported rates
        rates = []
        match_supported = re.search(r'Supported rates:\s*(.*)', block)
        if match_supported:
            rates += re.findall(r'[\d.]+', match_supported.group(1))

        # Extended supported rates
        match_extended = re.search(r'Extended supported rates:\s*(.*)', block)
        if match_extended:
            rates += re.findall(r'[\d.]+', match_extended.group(1))

        # Convert to float, sort, then to int if it's an integer value
        sorted_rates = sorted(set(float(rate) for rate in rates))
        formatted_rates = [int(rate) if rate.is_integer() else rate for rate in sorted_rates]

        #logging.info(f"{formatted_rates}")
        info['supported_rates'] = formatted_rates
        # HT Info
        ht_cap = extract(r'HT capabilities:\s+Capabilities: (0x[0-9a-f]+)')
        info['ht_cap'] = { 'capabilities': ht_cap }
        #ht_op_channel = extract(r'HT operation:\s+.*?primary channel: (\d+)', int)
        #sta_channel_width = extract(r'HT operation:\s+.*?STA channel width: (.+)')

        ht_op = none_if_empty(extract_ht_operation_block(block))
        info['ht_op'] = ht_op

        # VHT Info
        vht_cap = extract(r'VHT Capabilities \((0x[0-9a-f]+)\)')
        info['vht_cap'] = { 'capabilities': vht_cap }

        vht_op = none_if_empty(extract_vht_operation_block(block))
        info['vht_op'] = vht_op
        #logging.info(f"{vht_op}")

        # HE Info (optional)
        he_cap = extract(r'HE MAC Capabilities \((0x[0-9a-f]+)\)')
        he_phy = extract(r'HE PHY Capabilities: \((0x[0-9a-f]+)\)')
        #if he_cap or he_phy:
        info['he_cap'] = {
            'mac_capabilities': he_cap,
            'phy_capabilities': he_phy
        }

        he_op = extract(r'HE Operation Parameters: \((0x[0-9a-f]+)\)')
        info['he_op'] = {
            'operation_parameters': he_op
        }

        result[bssid] = info

    return result

def load_existing():
    filename = os.path.join(LOG_DIR, "beacon.json")
    if os.path.exists(filename):
        with open(filename, "r") as f:
            return json.load(f)
    return {}

def merge_db(old_db, new_db):
    updated = old_db.copy()
    for bssid, data in new_db.items():
        if bssid not in old_db:
            ssid = data.get("ssid", "<unknown>")
            logger.message("notice", f"[{IFACE}] New BSSID detected: {bssid} (SSID: {ssid})", _EXTRA_())
        updated[bssid] = data
    return updated

def save_db(db):
    def compact_lists(text):
        pattern = re.compile(r'\[\s*\n\s*((?:.+,\s*\n)+)\s*(.+?)\s*\]')
        return pattern.sub(lambda m: '[' + re.sub(r'\s+', ' ', m.group(1) + m.group(2)).strip() + ']', text)

    raw_json = json.dumps(db, indent=2)
    compacted_json = compact_lists(raw_json)
    filename = os.path.join(LOG_DIR, "beacon.json")
    with open(filename, "w") as f:
        f.write(compacted_json)

def get_scan_result():
    #logger.message("info", f"[{IFACE}] get scan result", _EXTRA_())
    lines = run_getscantable()
    #print(f"{lines}")
    #logger.message("info", f"[{IFACE}] {lines}", _EXTRA_())
    ap_lines = extract_ap_table(lines)
    chan_lines = extract_channel_table(lines)
    save_with_timestamp("ap", ap_lines)
    save_with_timestamp("freq", chan_lines)

    result = run_iwdevscandump()
    if result == "err":
        logger.message("err", f"[{IFACE}] scan result failed!", _EXTRA_())
    else:
        new_entries = parse_scan_output(result)
        existing_db = load_existing()
        merged = merge_db(existing_db, new_entries)
        now_sec = int(time.time())
        cleaned = remove_stale_entries(merged, now_sec)
        save_db(cleaned)

        
def main_loop():
    #subprocess.run(["ifconfig", IFACE, "up"])
    #last_log_time = time.time()
    
    #periodic scan when bgscan off
    '''
    ssid, freqs, interval = parse_wpa_supplicant_conf(WPA_CONF_FILE)
    logger.message("info", f"[{IFACE}] wpa conf = ssid : {ssid}, freq : {freqs}, interval : {interval}", _EXTRA_())
    threading.Thread(target=periodic_scan, args=(ssid, freqs, interval), daemon=True).start()
    '''

    def on_scan_event(IFACE):
        time.sleep(0.1)
        get_scan_result()
        #time.sleep(3)
        
    scan_event(IFACE, on_scan_event)
    #monitor_nl80211_scan_event(on_scan_event)

    ...
    log_step = 0
    try:
        while True:
            if time.time() - last_log_time >= LOG_INTERVAL -10 and log_step == 0:
                #print(f"last_log_time")
                #lines = run_setuserscan()
                result = run_iwdevscandump()
                if result == "err":
                    logger.message("err", f"[{IFACE}] scan result failed!", _EXTRA_())
                    time.sleep(5)
                    #last_log_time = time.time()
                else:
                    log_step = 1
                    #time.sleep(1)  # getscanresults가 준비될 때까지 대기
            elif time.time() - last_log_time >= LOG_INTERVAL and log_step == 1:
                lines = run_getscantable()
                #print(f"{lines}")
                ap_lines = extract_ap_table(lines)
                chan_lines = extract_channel_table(lines)
                
                save_with_timestamp("ap", ap_lines)
                save_with_timestamp("freq", chan_lines)

                '''
                timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                header = f"===== [{timestamp_str}] {'=' * (60 - len(timestamp_str) - 10)}"
                timestamped_result = f"{header}\n{result}"
                filename = os.path.join(LOG_DIR, "beacon.log")
                with open(filename, 'w') as f:
                    f.write(timestamped_result)
                '''
                #logger.message("err", f"result : {result}", _EXTRA_())                
                new_entries = parse_scan_output(result)
                existing_db = load_existing()
                merged = merge_db(existing_db, new_entries)
                now_sec = int(time.time())
                cleaned = remove_stale_entries(merged, now_sec)
                save_db(cleaned)

                last_log_time = time.time()
                log_step = 0
            #else:
                #print(f"last_log_time")
                #time.sleep(1)
                
            time.sleep(5)

    except KeyboardInterrupt:
        logger.message("err", f"[{IFACE}] keyboardInterrupt occurs", _EXTRA_())
    ...

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, handle_sigterm)
    logger = Logger(app_name="logger_scan", facility=logging.handlers.SysLogHandler.LOG_LOCAL0)

    if len(sys.argv) < 2:
        IFACE = "mlan0"
    else:
        IFACE = sys.argv[1]

    LOG_DIR = f"/var/log/cantops/scan/{IFACE}"
    WPA_CONF_FILE = f"/etc/wpa_supplicant/wpa_supplicant-{IFACE}.conf"    
    logger.message("info", f"[{IFACE}] version : {VERSION}, log_file : {LOG_DIR}/ap.log, {LOG_DIR}/freq.log, {LOG_DIR}/beacon.json", _EXTRA_())
    
    if IFACE != "mlan0" and IFACE != "mlan1" :
        logger.message("err", f"[{IFACE}] is not vaild interface", _EXTRA_())
        sys.exit(1)
        
    # 로그 디렉토리 생성
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR, exist_ok=True)
        
    main_loop()
