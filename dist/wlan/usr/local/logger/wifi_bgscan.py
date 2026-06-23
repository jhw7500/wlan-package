
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
ROAM_CONDITION_FLAG = "/tmp/roam_condition"
LAST_SCAN_TIME_FILE = "/tmp/last_roam_scan_time"
WIFI_INIT_CONF_JSON = "/usr/local/etc/wifi_init_conf.json"
ROAM_HINT_DIR = "/tmp"  # roam backoff hint 파일 디렉터리 (wifi_roam.roam_hint_touched 가 소비)
WPA_CONF_FILE = f"/etc/wpa_supplicant/wpa_supplicant-mlan0.conf"
DEFAULT_INTERVAL = 30
STALE_THRESHOLD_SEC = 600  #1hour

# Load STALE_THRESHOLD_SEC from JSON config
try:
    with open(WIFI_INIT_CONF_JSON) as _f:
        _conf = json.load(_f)
    STALE_THRESHOLD_SEC = _conf.get("logger", {}).get("bgscan_stale_threshold_sec", STALE_THRESHOLD_SEC)
except Exception:
    pass

#last_log_time = 0
VERSION = "0.0"
IFACE = ""
_WPA_CLI_WARNED = False   # wpa_cli 부재 로그 1회 제한 플래그
_WILDCARD_PROBE_WARNED = False   # ssid_filter=false+extra_ssids 와일드카드 probe 가정 경고 1회 제한

def handle_sigterm(signum, frame):
    logger.message('crit', f"[{IFACE}] SIGTERM {signum} received! Cleaning up...", _EXTRA_())
    cleanup()
    sys.exit(0)

def cleanup():
    pass

def set_flag(on: bool, path=ROAM_CONDITION_FLAG):
    with open(path, "w") as f:
        if on == 1:
            f.write("1")
        elif on == 0:
            f.write("0")
        else:
            f.write("")  # 빈 파일은 OFF 상태

def get_flag(path=ROAM_CONDITION_FLAG) -> bool:
    try:
        with open(path, "r") as f:
            content = f.read().strip()
            return content == "1"
    except FileNotFoundError:
        return False  # 파일이 없으면 OFF 취급

def is_wpa_running(interface="mlan0"):
    result = subprocess.run(
        ["systemctl", "is-active", f"wpa_supplicant@{interface}"],
        capture_output=True, text=True
    )
    return result.stdout.strip() == "active"

def is_wpa_connected(interface="mlan0"):
    """wpa_state==COMPLETED(연결 완료)인지. 미연결(스캔/인증/assoc 중)이면 False.
    wpa_cli 부재/오류는 로그로 가시화 — silent False면 bgscan이 영영 skip돼도 진단 불가."""
    try:
        result = subprocess.run(
            ["wpa_cli", "-i", interface, "status"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            logger.message("err", f"[{interface}] wpa_cli status exited {result.returncode} — treating as disconnected", _EXTRA_())
            return False
        for line in result.stdout.splitlines():
            if line.startswith("wpa_state="):
                return line.split("=", 1)[1].strip() == "COMPLETED"
        # exit 0인데 wpa_state= 라인 부재(소켓 오류 등) — silent False 방지 위해 로그
        logger.message("err", f"[{interface}] wpa_state not found in wpa_cli output — treating as disconnected", _EXTRA_())
    except FileNotFoundError:
        # wpa_cli 부재는 영구 상태 → 매 호출 로그 flood 방지 위해 1회만 남긴다.
        global _WPA_CLI_WARNED
        if not _WPA_CLI_WARNED:
            logger.message("err", f"[{interface}] wpa_cli not found — bgscan cannot verify connection (skipping scans)", _EXTRA_())
            _WPA_CLI_WARNED = True
    except Exception as e:
        logger.message("err", f"[{interface}] wpa_cli status error: {e}", _EXTRA_())
    return False

def parse_wpa_supplicant_conf(path):
    ssid = None
    freqs = []
    interval = 30  #         ^r

    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if line.startswith("ssid=") and not line.startswith("#"):
                ssid = line.split("=", 1)[1].strip().strip('"')
            elif line.startswith("scan_freq=") and not line.startswith("#"):
                freqs = line.split("=", 1)[1].strip().split()
            elif line.startswith("#!INTERVAL="):
                try:
                    interval = int(line.split("=")[1])
                except ValueError:
                    logger.message('err', f"[{IFACE}] INTERVAL : {interval} is invalid in {path}", _EXTRA_())
                    pass
            '''
            elif line.startswith("bgscan=") and not line.startswith("#"):
                parts = line.split("=", 1)[1].strip().strip('"').split(":")
                if len(parts) == 4:  # bgscan="simple:X:Y:Z"
                    try:
                        scan_interval = int(parts[3])
                    except ValueError:
                        pass
            '''

    return ssid, freqs, interval

def load_bgscan_json(iface):
    """`.iface.bgscan`에서 interval/ssid_filter/freq_filter/emit_roam_hint를,
    `.iface.roaming.extra_ssids`에서 추가 스캔 SSID를 한 번의 파일 읽기로 로드.
    interval은 양의 정수만, 필터/emit_roam_hint는 bool만, extra_ssids는 문자열 리스트만
    수용. 없음/형식오류면 (None, True, True, [], True)."""
    interval, ssid_filter, freq_filter, extra_ssids = None, True, True, []
    emit_roam_hint = True
    try:
        with open(WIFI_INIT_CONF_JSON, "r") as f:
            data = json.load(f)
        iface_cfg = data.get(iface, {})
        bg = iface_cfg.get("bgscan", {})
        iv = bg.get("interval")
        if isinstance(iv, int) and iv > 0:
            interval = iv
        if isinstance(bg.get("ssid_filter"), bool):
            ssid_filter = bg["ssid_filter"]
        if isinstance(bg.get("freq_filter"), bool):
            freq_filter = bg["freq_filter"]
        if isinstance(bg.get("emit_roam_hint"), bool):
            emit_roam_hint = bg["emit_roam_hint"]
        # 로밍 후보(roaming.extra_ssids)와 bgscan 스캔 대상을 일치시킨다.
        extra = iface_cfg.get("roaming", {}).get("extra_ssids")
        if isinstance(extra, list):
            extra_ssids = [s.strip() for s in extra if isinstance(s, str) and s.strip()]
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.message("err", f"[{iface}] bgscan json load error: {e}", _EXTRA_())
    return interval, ssid_filter, freq_filter, extra_ssids, emit_roam_hint

def emit_roam_hint_touch(iface):
    """roam backoff 해제 신호: /tmp/wifi_roam_hint_<iface> 를 touch(mtime 갱신).

    단방향(bgscan write / roam read)이라 race-free. 실패는 조용히 무시(다음 스캔 재시도).
    호출부는 스캔 성공 직후 emit_roam_hint=True 일 때만 호출한다."""
    path = os.path.join(ROAM_HINT_DIR, f"wifi_roam_hint_{iface}")
    try:
        with open(path, "a"):
            os.utime(path, None)
    except OSError as e:
        logger.message("err", f"[{iface}] roam hint touch failed: {e}", _EXTRA_())

def construct_iw_scan_cmd(ssid, scan_freqs, ssid_filter=True, freq_filter=True, extra_ssids=None):
    cmd = ["iw", IFACE, "scan"]

    # freq_filter=false면 freq 필터를 빼고 전체 대역 스캔(기본 true).
    if freq_filter and scan_freqs:
        cmd += ["freq"] + scan_freqs

    # directed probe(ssid 토큰) 대상 수집:
    #  - 기본/현재 ssid: ssid_filter=true일 때만 probe. false면 광범위(undirected) 스캔.
    #  - roaming.extra_ssids: 명시적 로밍 후보이므로 ssid_filter와 무관하게 항상 probe.
    #    hidden extra SSID는 directed probe로만 발견되므로 ssid_filter=false에서도 누락되면 안 된다.
    # iw는 다중 ssid 토큰을 지원. 중복 제거하여 추가.
    #  - ssid_filter=false인데 extra_ssids가 있으면 directed probe만 남아 와일드카드
    #    probe가 사라진다(NXP mlan 포함 대부분의 드라이버는 ssid 지정 시 와일드카드를
    #    보내지 않음) → 광범위 스캔 의도가 깨져 extra 외 일반 AP가 누락될 수 있다.
    #    빈 문자열 ""(와일드카드 probe)을 함께 넣어 광범위 스캔을 보존한다.
    #    NOTE: `iw scan ssid ""`의 와일드카드 해석은 표준 API가 아니다. 새 플랫폼/드라이버
    #    도입 시 extra_ssids 사용 전 실제로 와일드카드 probe가 나가는지(빈 SSID 무시 여부) 검증할 것.
    seen = set()
    probe = ([ssid] if (ssid_filter and ssid) else []) + (extra_ssids or [])
    if not ssid_filter and extra_ssids:
        probe.insert(0, "")
    for s in probe:
        if s is not None and s not in seen:
            seen.add(s)
            cmd += ["ssid", s]

    return cmd

def periodic_scan(conf_path):

    # 스캔 명령/주기/필터는 매 스캔 직전 wpa_supplicant conf + JSON에서 재구성한다.
    def build():
        global _WILDCARD_PROBE_WARNED
        ssid, freqs, wpa_interval = parse_wpa_supplicant_conf(conf_path)
        json_interval, ssid_filter, freq_filter, extra_ssids, emit_roam_hint = load_bgscan_json(IFACE)
        interval = json_interval or wpa_interval or DEFAULT_INTERVAL
        cmd = construct_iw_scan_cmd(ssid, freqs, ssid_filter, freq_filter, extra_ssids)
        # ssid_filter=false + extra_ssids면 construct_iw_scan_cmd가 와일드카드("") probe를
        # 삽입해 광범위 스캔을 보존한다. 빈 SSID를 broadcast probe로 보는 nl80211 동작에
        # 의존하므로(드라이버/커널 의존), 그 가정을 운영 로그에 1회 노출해 신규 플랫폼에서
        # 일반 AP 발견 여부를 검증할 수 있게 한다.
        if not ssid_filter and extra_ssids and not _WILDCARD_PROBE_WARNED:
            logger.message(
                "warn",
                f"[{IFACE}] ssid_filter=false + extra_ssids: 와일드카드(\"\") probe 삽입 "
                f"— 신규 드라이버/플랫폼에서 일반 AP 발견 동작 확인 필요",
                _EXTRA_(),
            )
            _WILDCARD_PROBE_WARNED = True
        return cmd, interval, emit_roam_hint

    # 초기 1회 구성 (실패해도 기동 — 다음 스캔 직전 재시도).
    cmd = None
    interval = DEFAULT_INTERVAL
    emit_roam_hint = True
    try:
        cmd, interval, emit_roam_hint = build()
        logger.message("info", f"[{IFACE}] bgscan start: cmd={cmd}, interval={interval}", _EXTRA_())
    except Exception as e:
        logger.message("err", f"[{IFACE}] initial bgscan config load failed: {e}", _EXTRA_())

    last_time = time.time()

    while True:
        if not os.path.exists(f"/sys/class/net/{IFACE}"):
            logger.message("info", f"[{IFACE}] waiting for interface...", _EXTRA_())
            time.sleep(5)
            continue

        if not is_wpa_running(IFACE):
            time.sleep(5)
            continue

        if get_flag():
            #logger.message("info", f"[{IFACE}] roam condition on", _EXTRA_())
            time.sleep(5)
            continue

        # roam 스캔이 발생한 경우 bgscan 주기 초기화
        try:
            with open(LAST_SCAN_TIME_FILE, "r") as f:
                roam_scan_time = float(f.read().strip())
            if roam_scan_time > last_time:
                last_time = roam_scan_time
                logger.message("info", f"[{IFACE}] bgscan timer reset by roam scan", _EXTRA_())
        except (FileNotFoundError, ValueError):
            pass

        if time.time() - last_time >= interval:
            # 연결 상태 확인은 스캔 주기 도래 시에만 수행한다(매 tick wpa_cli 서브프로세스 호출 회피).
            # 미연결(스캔/인증/assoc 중)이면 wpa_supplicant의 재연결 스캔/association과 라디오 경합
            # (-EBUSY)·off-channel 교란을 피하려 skip하고 다음 주기로 back off한다.
            # (연결 상태에서 로밍 후보 탐색이 bgscan 본래 목적이라 미연결 스캔은 무의미)
            if not is_wpa_connected(IFACE):
                # last_time을 리셋하지 않는다 → 재연결 직후 (대기 없이) 곧바로 첫 스캔이 발생해
                # 로밍 후보를 빠르게 갱신. 미연결 동안은 5s 간격으로만 재확인하므로 매 tick
                # wpa_cli 호출은 없다(연결 상태에선 interval마다 1회만 호출됨).
                time.sleep(5)
                continue

            # 스캔 직전에 wpa conf + JSON을 다시 읽어 최신 ssid/freq/interval/필터로 스캔한다
            # (런타임 변경 반영). 재로드 실패 시 직전 cmd/interval 유지.
            try:
                cmd, interval, emit_roam_hint = build()
            except Exception as e:
                logger.message("err", f"[{IFACE}] bgscan config reload failed (keep last): {e}", _EXTRA_())

            if cmd:
                try:
                    logger.message("info", f"[{IFACE}] {cmd}", _EXTRA_())
                    # stderr는 capture(저널 노이즈 방지)하되 실패 시 로그에 포함 → 진단성 유지.
                    # timeout으로 드라이버/FW stall 시 데몬이 영구 hang되는 것을 방지(다음 주기 재시도).
                    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, check=True, timeout=30)
                    # 스캔 성공(드라이버에 새 BSS 결과 적재) → roam backoff 해제 신호 touch.
                    # roam이 mtime 변화를 보면 후보없음 streak=0 으로 고속 복귀(spec §4 reset-b).
                    if emit_roam_hint:
                        emit_roam_hint_touch(IFACE)
                except subprocess.TimeoutExpired:
                    logger.message("err", f"[{IFACE}] iw scan timed out (30s) — driver/FW stall?", _EXTRA_())
                except subprocess.CalledProcessError as e:
                    logger.message("err", f"[{IFACE}] iw scan failed: {e} stderr={(e.stderr or '').strip()}", _EXTRA_())
            # 성공/실패 무관하게 다음 주기까지 back off (실패 시 1s 폭주 재시도 방지)
            last_time = time.time()

        time.sleep(1)

def main_loop():
    #subprocess.run(["ifconfig", IFACE, "up"])
    #last_log_time = time.time()

    # 스캔 파라미터(ssid/freq/interval/필터)는 periodic_scan이 매 스캔 직전 재로드하며,
    # 초기값은 periodic_scan의 "bgscan start" 로그에 찍힌다(여기서 중복 read 안 함).
    logger.message("info", f"[{IFACE}] version: {VERSION} (스캔 파라미터는 매 스캔 직전 재로드)", _EXTRA_())
    periodic_scan(WPA_CONF_FILE)

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, handle_sigterm)
    logger = Logger(app_name="SCAN", facility=logging.handlers.SysLogHandler.LOG_LOCAL0)

    if len(sys.argv) < 2:
        IFACE = "mlan0"
    else:
        IFACE = sys.argv[1]

    WPA_CONF_FILE = f"/etc/wpa_supplicant/wpa_supplicant-{IFACE}.conf"
    #logger.message("info", f"[{IFACE}] version : {VERSION}", _EXTRA_())

    if IFACE != "mlan0" and IFACE != "mlan1" :
        logger.message("err", f"[{IFACE}] invalid interface", _EXTRA_())
        sys.exit(1)

    main_loop()
