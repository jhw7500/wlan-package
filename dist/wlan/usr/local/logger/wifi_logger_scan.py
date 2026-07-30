import subprocess
import re
import os
import time
import random
import logging
import sys
import json
import signal
import fcntl
import threading
from datetime import datetime
from sUTILS import Logger, _EXTRA_

LOG_DIR = "/var/log/cantops/scan"
JSON_DIR = "var/log/cantops/json"
LINK_PATH = "/var/log/cantops/json"
TARGET_PATH = "/dev/shm/json"
WPA_CONF_FILE = f"/etc/wpa_supplicant/wpa_supplicant-mlan0.conf"
WIFI_INIT_CONF_JSON = "/usr/local/etc/wifi_init_conf.json"
LOG_INTERVAL = 30
# beacon.json stale 엔트리 프루닝 임계(초, 10분). 템플릿 logger.bgscan_stale_threshold_sec
# 와 fail-same. 종전엔 wifi_bgscan 이 이 키를 로드만 하고 미사용(dead knob)이었고 실소비처인
# 이 파일은 600 하드코딩이라 설정이 무효였다 — 로드를 실소비처로 이관(반영은 데몬 재시작).
DEFAULT_STALE_THRESHOLD_SEC = 600
# import 시점엔 logger 가 없어(__main__ 에서 생성) 로드 실패를 즉시 로깅할 수 없다 —
# 사유를 캡처해 두고 __main__ 의 logger 초기화 직후 1회 warn 으로 발행한다(운영 가시성).
_STALE_THRESHOLD_LOAD_WARNING = None


def load_stale_threshold(path=WIFI_INIT_CONF_JSON, default=DEFAULT_STALE_THRESHOLD_SEC):
    """`logger.bgscan_stale_threshold_sec` 로드 — 양의 int 만 수용(bool 제외), 파일
    부재/파싱 실패/불량 값은 default 유지(fail-same). import 시 1회 호출.
    파일 부재는 정상 폴백(신규/개발 환경)이라 무경고, 그 외 실패는 경고 캡처."""
    global _STALE_THRESHOLD_LOAD_WARNING
    try:
        with open(path) as f:
            v = json.load(f).get("logger", {}).get("bgscan_stale_threshold_sec", default)
        if isinstance(v, int) and not isinstance(v, bool) and v > 0:
            return v
        _STALE_THRESHOLD_LOAD_WARNING = (
            f"invalid logger.bgscan_stale_threshold_sec {v!r} — using default {default}"
        )
    except FileNotFoundError:
        pass
    except Exception as e:
        _STALE_THRESHOLD_LOAD_WARNING = (
            f"config load failed ({e}) — using default {default}"
        )
    return default


STALE_THRESHOLD_SEC = load_stale_threshold()
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
    header_section = []
    data_lines = []
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
                header_section.append(line)
            continue

        # 헤더 라인 (컬럼 설명)
        if sep_count == 1 and re.match(r'^#', clean):
            header_section.append(line)
            continue

        # 본문 이전은 무시
        if sep_count < 2:
            continue

        # SSID 필터링: 비어있거나 null 바이트(\00)인 항목 제거
        parts = clean.split('|')
        if len(parts) >= 7:
            ssid = parts[6].strip()
            if not ssid or '\\00' in ssid:
                continue

        data_lines.append(line)

    # 필터링 후 번호를 연속으로 재부여
    renumbered = []
    for idx, line in enumerate(data_lines):
        renumbered.append(re.sub(r'^\s*\d+\|', f'{idx:02d}|', line.strip()))

    return header_section + renumbered

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
    header = timestamp_str
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
            logger.message("info", f"[{IFACE}] BSSID {bssid} removed (last seen {info['date']})", _EXTRA_())
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
    filename = os.path.join(JSON_DIR, "beacon.json")
    
    if os.path.exists(filename):
        try:
            with open(filename, "r") as f:
                return json.load(f)
        except json.JSONDecodeError:
            logger.message("err", f"[{IFACE}] {filename} is empty or malformed. Using empty", _EXTRA_())
        except Exception as e:
            logger.message("err", f"[{IFACE}] Failed to read {filename}: {e}", _EXTRA_())
    
    return {}

def merge_db(old_db, new_db):
    updated = old_db.copy()
    for bssid, data in new_db.items():
        if bssid not in old_db:
            ssid = data.get("ssid", "<unknown>")
            logger.message("info", f"[{IFACE}] New BSSID detected: {bssid} (SSID: {ssid})", _EXTRA_())
        updated[bssid] = data
    return updated

def save_db(db):
    def compact_lists(text):
        pattern = re.compile(r'\[\s*\n\s*((?:.+,\s*\n)+)\s*(.+?)\s*\]')
        return pattern.sub(lambda m: '[' + re.sub(r'\s+', ' ', m.group(1) + m.group(2)).strip() + ']', text)

    raw_json = json.dumps(db, indent=2)
    compacted_json = compact_lists(raw_json)
    filename = os.path.join(JSON_DIR, "beacon.json")
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
        time.sleep(0.2)
        get_scan_result()
        #time.sleep(3)
        
    scan_event(IFACE, on_scan_event)
    #monitor_nl80211_scan_event(on_scan_event)
    # NOTE: scan_event 는 dmesg follow 를 영원히 블로킹하므로 이 아래에는 코드를 두지
    # 않는다 — 종전의 주기 스캔 while 은 도달 불가 사문이었고, 도달했다면 미정의
    # last_log_time 으로 NameError 였다(제거). 주기적 ap.log/beacon.json 생산은 bgscan
    # 등 어떤 주체든 유발하는 스캔 이벤트(on_scan_event)로 충분하다.

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, handle_sigterm)
    logger = Logger(app_name="SCAN", facility=logging.handlers.SysLogHandler.LOG_LOCAL0)

    if len(sys.argv) < 2:
        IFACE = "mlan0"
    else:
        IFACE = sys.argv[1]

    # iface 검증을 먼저 (락 파일 경로에 IFACE를 쓰기 전 — path traversal 방지)
    if IFACE != "mlan0" and IFACE != "mlan1":
        logger.message("emerg", f"[{IFACE}] is not valid interface", _EXTRA_())
        sys.exit(1)

    # import 시 캡처된 stale_threshold 로드 실패/불량값 경고를 1회 발행(운영 가시성).
    if _STALE_THRESHOLD_LOAD_WARNING:
        logger.message("warn", f"[{IFACE}] stale_threshold: {_STALE_THRESHOLD_LOAD_WARNING}", _EXTRA_())

    # 단일 인스턴스 락(iface별): 재시작 중복 실행 시 로그 동시 write 방지.
    # 락은 /run(root 전용, non-world-writable)에 둬 /tmp 심링크 truncate 공격을 차단한다.
    try:
        _lock_fp = open(f"/run/wifi_logger_scan_{IFACE}.lock", "w")
    except OSError as e:
        logger.message("warning", f"[{IFACE}] lock file open failed: {e} — exit", _EXTRA_())
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
        logger.message("warning", f"[{IFACE}] another wifi_logger_scan already running — exit", _EXTRA_())
        sys.exit(0)

    LOG_DIR = f"/var/log/cantops/scan/{IFACE}"
    JSON_DIR = f"/var/log/cantops/json/{IFACE}"
    WPA_CONF_FILE = f"/etc/wpa_supplicant/wpa_supplicant-{IFACE}.conf"
    logger.message("info", f"[{IFACE}] version : {VERSION}, log_file : {LOG_DIR}/ap.log, {LOG_DIR}/freq.log, {JSON_DIR}/beacon.json", _EXTRA_())
        
    # 로그 디렉토리 생성
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR, exist_ok=True)
    
    if not os.path.exists(TARGET_PATH):
        os.makedirs(TARGET_PATH, exist_ok=True)

    if not os.path.islink(LINK_PATH):
        if os.path.lexists(LINK_PATH):
            raise RuntimeError(f"{LINK_PATH} exists and is not a symlink. Cannot safely overwrite.")
        os.symlink(TARGET_PATH, LINK_PATH)

    if not os.path.exists(JSON_DIR):
        os.makedirs(JSON_DIR, exist_ok=True)
  
    main_loop()
