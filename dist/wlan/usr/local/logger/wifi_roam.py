
#!/usr/bin/env python3
import json
import time
import subprocess
import re
import sys
import signal
import logging
from datetime import datetime
from sUTILS import Logger, _EXTRA_

VERSION = "0.0"
IFACE = "mlan0"
LINK_LOG_FILE = f"/var/log/cantops/link/{IFACE}/link.json"
SCAN_LOG_FILE = f"/var/log/cantops/scan/{IFACE}/ap.log"
FREQ_LOG_FILE = f"/var/log/cantops/scan/{IFACE}/freq.log"
WPA_CONF_FILE = f"/etc/wpa_supplicant/wpa_supplicant-mlan0.conf"
DEFAULT_TH_2G = -75
DEFAULT_TH_5G = -75
WPA_SSID = None
WPA_FREQ = None
WPA_TH_2G = None
WPA_TH_5G = None
WPA_TH_CONNECT = None
DIFF_TH = 10
CHECK_INTERVAL = 1

def handle_sigterm(signum, frame):
    logger.message('crit', f"[{IFACE}] SIGTERM {signum} received! Cleaning up...", _EXTRA_())
    cleanup()
    sys.exit(0)

def cleanup():
    pass

def freq_to_channel(freq):
    freq = int(freq)
    if 2412 <= freq <= 2472:
        return (freq - 2407) // 5
    elif freq == 2484:
        return 14
    elif 5180 <= freq <= 5825:
        return (freq - 5000) // 5
    elif 5955 <= freq <= 7115:
        return (freq - 5950) // 5
    else:
        return None  # Unknown

def channel_to_freq(channel):
    channel = int(channel)
    if 1 <= channel <= 13:
        return 2407 + channel * 5
    elif channel == 14:
        return 2484
    elif 36 <= channel <= 165:
        return 5000 + channel * 5
    elif 1 <= channel <= 233:  # 6GHz band (e.g., channel 1 → 5955)
        freq = 5950 + channel * 5
        if 5955 <= freq <= 7115:
            return freq
        else:
            return None
    else:
        return None

def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")

FREQ_TO_CHAN = {
    # 2.4GHz
    "2412": "1g", "2417": "2g", "2422": "3g", "2427": "4g", "2432": "5g",
    "2437": "6g", "2442": "7g", "2447": "8g", "2452": "9g", "2457": "10g", "2462": "11g", 
    "2467": "12g", "2472": "13g", "2484": "14",
    # 5GHz
    "5180": "36a", "5200": "40a", "5220": "44a", "5240": "48a",
    "5260": "52a", "5280": "56a", "5300": "60a", "5320": "64a",
    "5500": "100a", "5520": "104a", "5540": "108a", "5560": "112a",
    "5580": "116a", "5600": "120a", "5620": "124a", "5640": "128a",
    "5660": "132a", "5680": "136a", "5700": "140a", "5720": "144a",
    "5745": "149a", "5765": "153a", "5785": "157a", "5805": "161a",
    "5825": "165a", "5845": "169a", "5865": "173a", "5885": "177a"
}

def mlanutl_scan(ssid, freqs):
    try:
        chan_str = ",".join(FREQ_TO_CHAN[f] for f in freqs)
    except KeyError as e:
        print(f"[ERROR] Unknown frequency: {e}")
        return
    '''
    cmd = f"mlanutl mlan0 setuserscan ssid={ssid} chan={chan_str}"
    #print(f"[CMD] {cmd}")
    freq_str = " ".join(str(f) for f in freqs)
    logger.message('info', f"[{IFACE}] active scan ssid={ssid}, freq={freq_str} cmd={cmd} for roaming", _EXTRA_())
    '''
    cmd = f"mlanutl mlan0 setuserscan chan={chan_str} ssid={ssid}"
    logger.message('info', f"[{IFACE}] scan : {cmd}", _EXTRA_())
    try:
        result = subprocess.run(cmd, shell=True, check=True, capture_output=True, text=True)
        output = result.stdout.strip()
        if not output:
            logger.message('err', f"[{IFACE}] scan command reutrned no output", _EXTRA_())
            return None

        #print(f"[OUTPUT]\n{output}")       
        return result.stdout.splitlines()
    except subprocess.CalledProcessError as e:
        logger.message('err', f"[{IFACE}] scan command failed:{e.stderr.strip()}", _EXTRA_())
        return None

def iw_scan(ssid, freqs):
    if ssid and freqs:
        cmd = ["iw", IFACE, "scan", "freq"] + freqs + ["ssid", ssid]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def get_station_info():
    try:
        with open(LINK_LOG_FILE, 'r') as f:
            data = json.load(f)
            result = {
                "bssid": data['station_info']['address'].strip().lower(),
                #"ssid": data['info']['ssid'].strip(),
                "freq": int(data['info']['freq']),
                "rssi": int(data['station_info']['signal'].replace(" dBm", ""))
            }
            return result
    except Exception as e:
        #logger.message('err', f"[{IFACE}] Failed to read station info from link log: {e}", _EXTRA_())
        logger.message('info', f"[{IFACE}] waiting for link : {e}", _EXTRA_())
        return None

def load_channel_info():
    try:
        with open(LINK_LOG_FILE, 'r') as f:
            data = json.load(f)
            return data.get("channel_info", {})
    except Exception as e:
        print(f"[ERROR] Failed to load channel info from link log: {e}")
        return {}

def get_current_ssid():
    try:
        with open(LINK_LOG_FILE, 'r') as f:
            data = json.load(f)
            return data['info']['ssid'].strip()
    except Exception as e:
        logger.message('err', f"[{IFACE}] Failed to get current SSID from link log: {e}", _EXTRA_())
        return None

def get_current_bssid():
    try:
        with open(LINK_LOG_FILE, 'r') as f:
            data = json.load(f)
            return data['station_info']['address'].strip().lower()
    except Exception as e:
        logger.message('err', f"[{IFACE}] Failed to get current BSSID from link log: {e}", _EXTRA_())
        return None

def get_latest_scan(st):
    try:
        with open(SCAN_LOG_FILE, 'r') as f:
            lines = f.readlines()
    except Exception as e:
        logger.message('err', f"[{IFACE}] Failed to read scan info from ap log: {e}", _EXTRA_())
        return [], None

    timestamp = None
    entries = []

    for i in reversed(range(len(lines))):
        line = lines[i]
        match = re.match(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]", line)
        if match:
            timestamp = match.group(1)
            start_idx = i + 1
            break

    if timestamp is None:
        logger.message('err', f"[{IFACE}] timestamp is not exist", _EXTRA_())
        return [], None

    #logger.message('info', f"[{IFACE}] wpa ssid:{st['ssid']}, freq:{WPA_FREQ}", _EXTRA_())
    for line in lines[start_idx:]:
        if re.match(r"^\d{2}\|", line):
            fields = line.strip().split("|")
            if len(fields) >= 7:
                try:
                    channel = int(fields[1].strip())
                    rssi = int(fields[2].strip())
                    ld = int(fields[3].strip())
                    bssid = fields[4].strip().lower()
                    ssid = fields[6].strip()
                    rssi_th = WPA_TH_2G if channel < 36 else WPA_TH_5G
                    freq = channel_to_freq(channel)
                    #logger.message('info', f"[{IFACE}] ssid:{ssid}, bssid:{bssid}, ch:{channel}, freq:{freq}, rssi:{rssi}, th:{rssi_th}, ld:{ld}", _EXTRA_()) 
                    #if st['bssid'] != bssid and st['ssid'] == ssid and channel in WPA_FREQ: #and rssi > rssi_th:
                    if st['ssid'] == ssid and str(freq) in WPA_FREQ:
                        #logger.message('info', f"[{IFACE}] {bssid} append to entry", _EXTRA_()) 
                        entries.append({
                            "timestamp": timestamp,
                            "channel": channel,
                            "rssi": rssi,
                            "rssi_th": rssi_th,
                            "ld": ld,
                            "bssid": bssid,
                            "ssid": ssid
                        })
                except Exception as e:
                    logger.message('warn', f"[{IFACE}] scan entry parsing failed: {e}", _EXTRA_())
                    continue

    candidates = sorted(entries, key=lambda x: x['rssi'], reverse=True)
    #logger.message('info', f"[{IFACE}] roam candidates: {candidates}", _EXTRA_())
    i = 0
    for entry in candidates:
        logger.message('info',
            f"[{IFACE}] roam candidate {i}: "
            f"ts={entry['timestamp']}, ssid={entry['ssid']}, bssid={entry['bssid']}, "
            f"ch={entry['channel']}, ld={entry['ld']}, rssi={entry['rssi']}(th={entry['rssi_th']})",
            _EXTRA_()
        )
        i+=1

    return candidates, timestamp

def parse_supplicant_conf(path):
    ssid = None
    freqs = []
    th2g = None
    th5g = None
    th_connect = None

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
            elif line.startswith("#!TH_2G="):
                try:
                    th2g = int(line.split("=")[1])
                except ValueError:
                    logger.message('err', f"[{IFACE}] TH_2G : {th2g} is invalid in {path}", _EXTRA_())
                    pass
            elif line.startswith("#!TH_5G="):
                try:
                    th5g = int(line.split("=")[1])
                except ValueError:
                    logger.message('err', f"[{IFACE}] TG_5G : {tg5g} is invalid in {path}", _EXTRA_())
                    pass
            elif line.startswith("#!TH_CONNECT="):
                try:
                    th_connect = int(line.split("=")[1])
                except ValueError:
                    logger.message('err', f"[{IFACE}] TH_CONNECT : {th_connect} is invalid in {path}", _EXTRA_())
                    pass

    th2g = th2g if th2g is not None else DEFAULT_TH_2G
    th5g = th5g if th5g is not None else DEFAULT_TH_5G

    return ssid, freqs, th2g, th5g, th_connect

def roam_to_bssid(bssid):
    #log(f"Roaming to BSSID {bssid}")
    #logger.message('info', f"[{IFACE}] Roaming to BSSID {bssid}", _EXTRA_())
    subprocess.run(["wpa_cli", "-i", IFACE, "roam", bssid])

def score_ap(ap, rssi_weight=1.0, ld_weight=1.0):
    normalized_rssi = ap['rssi'] + 100  # -40 → 60
    normalized_ld = ap['ld']            # 0~100
    score = rssi_weight * normalized_rssi - ld_weight * normalized_ld
    return score

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

def save_with_timestamp(filename, content_lines):
    # 날짜 기반 파일명 생성
    #date_str = datetime.now().strftime("%Y%m%d")
    #filename = os.path.join(LOG_DIR, f"{prefix}_{date_str}.log")
    #filename = os.path.join(LOG_DIR, f"{prefix}.log"

    # 타임스탬프 헤더
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
        f.write('\n')

    #print(f"✔ {filename} 로그에 추가됨")
    return filename

def parse_thresholds(conf_path):
    th2g = None
    th5g = None

    with open(conf_path, "r") as f:
        for line in f:
            line = line.strip()
            if line.startswith("#!TH_2G="):
                try:
                    th2g = int(line.split("=")[1])
                except ValueError:
                    pass
            elif line.startswith("#!TH_5G="):
                try:
                    th5g = int(line.split("=")[1])
                except ValueError:
                    pass

    th2g = th2g if th2g is not None else DEFAULT_TH_2G
    th5g = th5g if th5g is not None else DEFAULT_TH_5G

    return th2g, th5g

def main():
    while True:

        station = get_station_info()

        if not station:
            time.sleep(CHECK_INTERVAL)
            continue

        '''
        if WPA_TH_CONNECT:
            if station['rssi'] < WPA_TH_CONNECT:
                logger.message('info', f"[{IFACE}] disconnect condition : {station['rssi']} < {WPA_TH_CONNECT} ({station['bssid']})", _EXTRA_())
                subprocess.run(["wpa_cli", "-i", IFACE, "disconnect"])
                time.sleep(CHECK_INTERVAL)
                continue
        '''

        #bssid, ssid, frequency, signal = get_station_info()
        '''
        channel_info = load_channel_info()
        for freq, info in channel_info.items():
            noise = info.get('noise')
            busy = info.get("busy_time_ms", 0)
            active = info.get("active_time_ms", 1)
            load = (busy / active) * 100
            #print(f"frequency {frequency}, freq {freq} MHz: Load={load:.2f}%, Noise={noise} dBm")
            if station['freq'] == int(freq):
                station['noise'] = noise
                station['load'] = round(load, 2)
        '''

        if station['freq'] < 5000:
            station['rssi_th'] = WPA_TH_2G
        else:
            station['rssi_th'] = WPA_TH_5G

        #logger.message('info', f"[{IFACE}] rssi cur : {station['rssi']}, roam_th : {station['rssi_th']}", _EXTRA_())
        if station['rssi'] >= station['rssi_th']:
            #subprocess.run(["systemctl", "start", "wifi_capture"], check=True)
            time.sleep(CHECK_INTERVAL)
            continue

        logger.message('info', f"[{IFACE}] roaming condition : {station['rssi']} < {station['rssi_th']} ({station['bssid']})", _EXTRA_())
                
        #ssid, freqs = parse_supplicant_conf(WPA_CONF_FILE)
        if WPA_SSID and WPA_FREQ:
            #subprocess.run(["systemctl", "stop", "wifi_capture"], check=True)
            station['ssid'] = WPA_SSID
            #iw_scan(WPA_SSID, WPA_FREQ)
            lines = mlanutl_scan(WPA_SSID, WPA_FREQ)
            #logger.message('info', f"[{IFACE}] scan end", _EXTRA_())
            if lines:
                ap_lines = extract_ap_table(lines)
                chan_lines = extract_channel_table(lines)
                save_with_timestamp(SCAN_LOG_FILE, ap_lines)
                save_with_timestamp(FREQ_LOG_FILE, chan_lines)
            else:
                logger.message('err', f"[{IFACE}] scan failed: output : {lines}", _EXTRA_())
                time.sleep(CHECK_INTERVAL)
                continue

        #time.sleep(0.1)
        entries, timestamp = get_latest_scan(station)

        if not entries:
            #log("No APs found in latest scan.")
            logger.message('err', f"[{IFACE}] No Matching APs found in latest scan", _EXTRA_())
            time.sleep(3)
            continue

        top_ap = entries[0]
        rssi_diff = top_ap["rssi"] - station['rssi']
        #score = score_ap(top_ap)

        #log(f"Current signal={signal}dBm (BSSID: {current_bssid}), "
        #    f"Top RSSI={top_rssi}dBm at {top_bssid}, Δ={rssi_diff}dB")
        
        if top_ap['bssid'] != station['bssid']:
            #if top_ap['rssi'] > top_ap['rssi_th'] and rssi_diff >= DIFF_TH:
            if rssi_diff >= DIFF_TH:
                logger.message('emerg', f"[{IFACE}] Roaming from {station['bssid']}(ch:{station['freq']}) to {top_ap['bssid']}(ch:{channel_to_freq(top_ap['channel'])})"
                                       f" : {top_ap['ssid']}, {top_ap['rssi']}>{top_ap['rssi_th']}", _EXTRA_())
                roam_to_bssid(top_ap['bssid'])
                time.sleep(5)
                continue
            else:
                logger.message('info', f"[{IFACE}] Top AP is not qualified : bssid={top_ap['bssid']}, rssi={top_ap['rssi']}({top_ap['rssi_th']}), diff={rssi_diff}({DIFF_TH})", _EXTRA_())
        else:
            logger.message('info', f"[{IFACE}] Top AP is already connected", _EXTRA_()) 

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, handle_sigterm)
    logger = Logger(app_name='ROAM', facility=logging.handlers.SysLogHandler.LOG_LOCAL0)

    IFACE = sys.argv[1] if len(sys.argv) > 1 else "mlan0"
    if IFACE not in ["mlan0", "mlan1"]:
        logger.message("emerg", f"[{IFACE}] interface is invalid", _EXTRA_())
        sys.exit(1)

    LINK_LOG_FILE = f"/var/log/cantops/link/{IFACE}/link.json"
    SCAN_LOG_FILE = f"/var/log/cantops/scan/{IFACE}/ap.log"
    FREQ_LOG_FILE = f"/var/log/cantops/scan/{IFACE}/freq.log"
    WPA_CONF_FILE = f"/etc/wpa_supplicant/wpa_supplicant-{IFACE}.conf"

    WPA_SSID, WPA_FREQ, WPA_TH_2G, WPA_TH_5G, WPA_TH_CONNECT = parse_supplicant_conf(WPA_CONF_FILE)    

    logger.message("info", f"[{IFACE}] version : {VERSION}, ssid : {WPA_SSID}, scan_freq : {WPA_FREQ}, TH_CONNECT : {WPA_TH_CONNECT}, TH_2G : {WPA_TH_2G}, TH_5G : {WPA_TH_5G}, conf : {WPA_CONF_FILE}", _EXTRA_())

    main()
