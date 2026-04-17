import json
import time
import curses
import os
import argparse
import sys
import signal
import subprocess
import re
from datetime import datetime

INTERVAL = 1.0
FILE_PATH = None
RUNNING = True
COMPACT_MODE = False
SUMMARY_PATH = "/var/log/cantops/summary/summary.log"
SUMMARY_LINES = 5
PING_LINES = 5
ROAM_DISPLAY_SEC = 5
CONF_PATH = "/usr/local/etc/wifi_init_conf.json"

def signal_handler(signum, frame):
    global RUNNING
    RUNNING = False

def safe_end_curses():
    """curses 화면을 정상 종료"""
    try:
        curses.nocbreak()
        curses.echo()
        curses.endwin()
    except Exception:
        pass

def load_json(filepath):
    try:
        with open(filepath, "r") as f:
            return json.load(f)
    except:
        return {}


# ── Compact 모드 유틸 함수/클래스 ──────────────────────────────

def is_service_active(name):
    """systemd 서비스가 active 상태인지 확인"""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", name],
            capture_output=True, text=True, timeout=2
        )
        return result.stdout.strip() == "active"
    except Exception:
        return False


def get_bridge_status():
    """wbridge 모드, thermal state, cpufreq governor 조회"""
    status = {"effective": "-", "requested": "-", "thermal": "-", "governor": "-"}
    try:
        with open("/run/wbridge.effective.json", "r") as f:
            ej = json.load(f)
        status["effective"] = ej.get("profile_effective", "-")
        status["requested"] = ej.get("mode_requested", "-")
        status["thermal"] = ej.get("thermal_state", "-")
    except Exception:
        pass
    try:
        with open("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor", "r") as f:
            status["governor"] = f.read().strip()
    except Exception:
        pass
    return status


def get_temperatures():
    """CPU / mlan0 / mlan1 온도 (°C 정수)"""
    temps = {"cpu": "-", "mlan0": "-", "mlan1": "-"}
    try:
        with open("/sys/devices/virtual/thermal/thermal_zone0/temp", "r") as f:
            temps["cpu"] = str(int(f.read().strip()) // 1000)
    except Exception:
        pass
    for iface in ("mlan0", "mlan1"):
        try:
            out = subprocess.check_output(
                ["mlanutl", iface, "get_sensor_temp"],
                timeout=2, stderr=subprocess.DEVNULL
            ).decode()
            m = re.search(r"[\s=]+(\d+(?:\.\d+)?)\s*C", out)
            if m:
                temps[iface] = str(int(float(m.group(1))))
        except Exception:
            pass
    return temps


def get_ip_and_gw(iface):
    """인터페이스의 IP 주소와 기본 게이트웨이 조회"""
    ip_addr, gw = "-", "-"
    try:
        out = subprocess.check_output(
            ["ip", "-4", "addr", "show", iface],
            timeout=2, stderr=subprocess.DEVNULL
        ).decode()
        m = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)", out)
        if m:
            ip_addr = m.group(1)
    except Exception:
        pass
    try:
        out = subprocess.check_output(
            ["ip", "-4", "route", "show", "default", "dev", iface],
            timeout=2, stderr=subprocess.DEVNULL
        ).decode()
        m = re.search(r"via\s+(\d+\.\d+\.\d+\.\d+)", out)
        if m:
            gw = m.group(1)
    except Exception:
        pass
    return ip_addr, gw


_prev_cpu = None

def get_cpu_usage():
    """/proc/stat 기반 CPU 사용률 (%)"""
    global _prev_cpu
    try:
        with open("/proc/stat", "r") as f:
            line = f.readline()
        vals = [int(x) for x in line.split()[1:]]
        idle = vals[3] + vals[4]
        total = sum(vals)
        if _prev_cpu is None:
            _prev_cpu = (idle, total)
            return "-"
        prev_idle, prev_total = _prev_cpu
        _prev_cpu = (idle, total)
        d_idle = idle - prev_idle
        d_total = total - prev_total
        if d_total == 0:
            return "0"
        return str(round((1 - d_idle / d_total) * 100))
    except Exception:
        return "-"


def get_mem_usage():
    """/proc/meminfo 기반 메모리 사용률 (%)"""
    try:
        info = {}
        with open("/proc/meminfo", "r") as f:
            for line in f:
                parts = line.split()
                if parts[0] in ("MemTotal:", "MemAvailable:"):
                    info[parts[0]] = int(parts[1])
                if len(info) == 2:
                    break
        total = info["MemTotal:"]
        avail = info["MemAvailable:"]
        return str(round((1 - avail / total) * 100))
    except Exception:
        return "-"


def read_last_lines(filepath, n=5):
    """파일 마지막 N줄 반환"""
    try:
        with open(filepath, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            read_size = min(size, 4096)
            f.seek(size - read_size)
            data = f.read().decode("utf-8", errors="replace")
        lines = data.strip().split("\n")
        return lines[-n:]
    except Exception:
        return []


class WpaEventTracker:
    """wpa.log tail 파싱: 스캔/로밍/4-Way 핸드쉐이크 이벤트 추적"""

    def __init__(self, wpa_log_path):
        self.path = wpa_log_path
        self.last_pos = 0
        self.last_scan_time = None
        self.last_handshake_ms = None  # 4-Way만 (RX msg 1 ~ COMPLETED/CONNECTED)
        self.last_roam_ms = None       # 전체 로밍 (Trying to associate ~ CONNECTED)
        self._hs_start = None
        self._roam_start = None

    def update(self):
        try:
            with open(self.path, "r") as f:
                f.seek(0, 2)
                size = f.tell()
                if size < self.last_pos:
                    self.last_pos = 0
                f.seek(self.last_pos)
                new_lines = f.readlines()
                self.last_pos = f.tell()
        except Exception:
            return
        for line in new_lines:
            self._parse_line(line)

    def _parse_timestamp(self, line):
        try:
            return datetime.strptime(line[:23], "%Y-%m-%d %H:%M:%S.%f")
        except Exception:
            return None

    def _parse_line(self, line):
        if "CTRL-EVENT-SCAN-STARTED" in line:
            ts = self._parse_timestamp(line)
            if ts:
                self.last_scan_time = ts.strftime("%H:%M:%S")
        # 로밍 시작: "Trying to associate with XX:XX:XX"
        if "Trying to associate with" in line:
            self._roam_start = self._parse_timestamp(line)
        # 4-Way 시작
        if "WPA: RX message 1 of 4-Way" in line:
            self._hs_start = self._parse_timestamp(line)
        # 로밍+핸드쉐이크 완료
        if "CTRL-EVENT-CONNECTED" in line:
            ts = self._parse_timestamp(line)
            if ts and self._hs_start:
                self.last_handshake_ms = int(
                    (ts - self._hs_start).total_seconds() * 1000
                )
                self._hs_start = None
            if ts and self._roam_start:
                self.last_roam_ms = int(
                    (ts - self._roam_start).total_seconds() * 1000
                )
                self._roam_start = None


class RoamTracker:
    """AP MAC 변경 기반 로밍 감지 (최소 display_sec초 표시)"""

    def __init__(self, display_sec=3):
        self.display_sec = display_sec
        self.prev_ap = None
        self.roam_from = None
        self.roam_to = None
        self.roam_time = None

    def check(self, current_ap):
        if current_ap and self.prev_ap and current_ap != self.prev_ap:
            self.roam_from = self.prev_ap
            self.roam_to = current_ap
            self.roam_time = time.time()
        if current_ap:
            self.prev_ap = current_ap

    def get_display(self):
        if self.roam_time is None:
            return None
        elapsed = time.time() - self.roam_time
        if elapsed > self.display_sec:
            return None
        return f"{self.roam_from} -> {self.roam_to}  ({int(elapsed)}s ago)"


class InterruptTracker:
    """플랫폼별 WLAN/ETH 인터럽트 추적 (delta/s + avg/s)"""

    # BUS_TYPE → WLAN IRQ 패턴, BOARD_TYPE → ETH IRQ 패턴
    WLAN_MAP = {"sdio": "mmc2", "pcie": "mrvl_pcie_msi"}
    ETH_MAP = {"imx93": "eth0", "imx8mm": "30be0000.ethernet"}

    def __init__(self, bus_type, board_type, interval=1.0):
        self.wlan_name = self.WLAN_MAP.get(bus_type, "mmc2")
        self.eth_name = self.ETH_MAP.get(board_type, "eth0")
        self._num_cpus = self._get_num_cpus()
        self._interval = interval
        self._eth_lines = self._count_irq_lines(self.eth_name)
        # WLAN
        self.start_wlan = self._read_irq_all(self.wlan_name)
        self.prev_wlan = self.start_wlan
        self.delta_wlan = 0
        self.avg_wlan = 0
        # ETH data (1st line)
        self.start_eth_data = self._read_irq_nth(self.eth_name, 0)
        self.prev_eth_data = self.start_eth_data
        self.delta_eth_data = 0
        self.avg_eth_data = 0
        # ETH err (2nd line, if exists)
        self.start_eth_err = self._read_irq_nth(self.eth_name, 1) if self._eth_lines >= 2 else 0
        self.prev_eth_err = self.start_eth_err
        self.delta_eth_err = 0
        self.start_time = time.time()

    @staticmethod
    def _get_num_cpus():
        try:
            with open("/proc/interrupts", "r") as f:
                return len(f.readline().split())
        except Exception:
            return 4

    def _get_matching_lines(self, name):
        """name에 매칭되는 모든 라인 반환"""
        lines = []
        try:
            with open("/proc/interrupts", "r") as f:
                for line in f:
                    if name in line:
                        lines.append(line)
        except Exception:
            pass
        return lines

    def _sum_line(self, line):
        """한 줄의 CPU 카운트 합산"""
        total = 0
        parts = line.split()
        for i in range(1, self._num_cpus + 1):
            if i < len(parts) and parts[i].isdigit():
                total += int(parts[i])
        return total

    def _read_irq_all(self, name):
        """매칭되는 모든 라인의 CPU 카운트 합산"""
        return sum(self._sum_line(l) for l in self._get_matching_lines(name))

    def _read_irq_nth(self, name, n):
        """매칭되는 N번째(0-based) 라인의 CPU 카운트"""
        lines = self._get_matching_lines(name)
        if n < len(lines):
            return self._sum_line(lines[n])
        return 0

    def _count_irq_lines(self, name):
        return len(self._get_matching_lines(name))

    def update(self):
        cur_wlan = self._read_irq_all(self.wlan_name)
        cur_eth_data = self._read_irq_nth(self.eth_name, 0)
        cur_eth_err = self._read_irq_nth(self.eth_name, 1) if self._eth_lines >= 2 else 0
        # 카운터 리셋 감지 (드라이버 재로드 등)
        if cur_wlan < self.prev_wlan or cur_eth_data < self.prev_eth_data:
            self.start_wlan = cur_wlan
            self.start_eth_data = cur_eth_data
            self.start_eth_err = cur_eth_err
            self.start_time = time.time()
            self.delta_wlan = 0
            self.delta_eth_data = 0
            self.delta_eth_err = 0
            self.avg_wlan = 0
            self.avg_eth_data = 0
        else:
            self.delta_wlan = int((cur_wlan - self.prev_wlan) / self._interval)
            self.delta_eth_data = int((cur_eth_data - self.prev_eth_data) / self._interval)
            self.delta_eth_err = int((cur_eth_err - self.prev_eth_err) / self._interval)
            elapsed = int(time.time() - self.start_time)
            if elapsed > 0:
                self.avg_wlan = (cur_wlan - self.start_wlan) // elapsed
                self.avg_eth_data = (cur_eth_data - self.start_eth_data) // elapsed
        self.prev_wlan = cur_wlan
        self.prev_eth_data = cur_eth_data
        self.prev_eth_err = cur_eth_err


# ── 화면 그리기 ────────────────────────────────────────────────

def draw_compact_screen(stdscr, data, wpa_tracker, roam_tracker, summary_path, ping_log_path=None, ip_gw=None, irq_tracker=None, start_time=None):
    """Compact 모드: 최소 라인으로 핵심 WiFi 상태 표시"""
    stdscr.clear()
    max_y, max_x = stdscr.getmaxyx()
    sep = "-" * min(max_x - 2, 60)

    def safe_addstr(y, x, text, attr=0):
        if y < 0 or y >= max_y or x < 0 or x >= max_x:
            return
        stdscr.addstr(y, x, text[: max_x - x], attr)

    y = 0
    info = data.get("info", {}) if isinstance(data, dict) else {}
    link = data.get("link", {}) if isinstance(data, dict) else {}
    date_str = data.get("date", "") if isinstance(data, dict) else ""

    ssid = info.get("ssid", "-")
    ch = info.get("channel", "-")
    freq = info.get("freq", "-")
    width = info.get("width", "-")
    ap_addr = link.get("address", "-")

    # 헤더
    runtime_str = ""
    if start_time:
        elapsed = int(time.time() - start_time)
        h, m, s = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
        runtime_str = f"  RT:{h:02d}:{m:02d}:{s:02d}"
    safe_addstr(y, 1, "Compact WiFi Monitor", curses.A_BOLD)
    safe_addstr(y, 23, date_str + runtime_str)
    y += 1
    safe_addstr(y, 1, sep)
    y += 1

    # info: MAC, IP, GW / AP, BW, SSID, CH
    my_mac = info.get("address", "-")
    ch_freq = f"{ch}({freq})" if ch != "-" and freq != "-" else str(ch)
    ip_addr, gw = ip_gw if ip_gw else ("-", "-")
    safe_addstr(y, 1, f"MAC: {my_mac}  IP: {ip_addr}  GW: {gw}")
    y += 1
    safe_addstr(y, 1, f"AP: {ap_addr}  BW: {width}  SSID: {ssid}  CH: {ch_freq}")
    y += 1

    # 전송률 + MCS 정보
    tx_rate = link.get("tx_bitrate", "-")
    rx_rate = link.get("rx_bitrate", "-")
    tx_s = tx_rate.split(" ")[0] if isinstance(tx_rate, str) else str(tx_rate)
    rx_s = rx_rate.split(" ")[0] if isinstance(rx_rate, str) else str(rx_rate)
    def _extract_mcs(rate_str):
        if not isinstance(rate_str, str):
            return ""
        parts = rate_str.split("MBit/s", 1)
        if len(parts) < 2 or not parts[1].strip():
            return ""
        mcs_info = parts[1].strip()
        mcs_info = mcs_info.replace("VHT-NSS", "NSS").replace("HE-NSS", "NSS").replace("HT-NSS", "NSS")
        return mcs_info
    tx_mcs = _extract_mcs(tx_rate)
    rx_mcs = _extract_mcs(rx_rate)
    safe_addstr(y, 1, f"TX: {tx_s}M {tx_mcs}")
    y += 1
    safe_addstr(y, 1, f"RX: {rx_s}M {rx_mcs}")
    y += 1

    # RSSI + tx_failed / tx_retries
    sig = link.get("signal", "-")
    sig_avg = link.get("signal_avg", "-")
    sig_s = sig.replace(" dBm", "") if isinstance(sig, str) else str(sig)
    sig_a = sig_avg.replace(" dBm", "") if isinstance(sig_avg, str) else str(sig_avg)
    tx_fail = link.get("tx_failed", "-")
    mwlan_log = data.get("mwlan_log", {}) if isinstance(data, dict) else {}
    tx_retry = mwlan_log.get("dot11RetryCount", link.get("tx_retries", "-"))
    safe_addstr(y, 1, f"RSSI: {sig_s}/{sig_a} dBm  TX_FAIL: {tx_fail}  TX_RETRY: {tx_retry}")
    y += 1

    # 온도 + CPU/MEM
    temps = get_temperatures()
    cpu_u = get_cpu_usage()
    mem_u = get_mem_usage()
    safe_addstr(y, 1, f"Temp: {temps['cpu']} / {temps['mlan0']} / {temps['mlan1']}")
    safe_addstr(y, 35, f"Usage: CPU {cpu_u}% / MEM {mem_u}%")
    y += 1

    # 브릿지 모드 / thermal state / cpufreq
    bs = get_bridge_status()
    mode_str = bs["effective"]
    if bs["effective"] != bs["requested"] and bs["requested"] != "-":
        mode_str = f"{bs['effective']} (req:{bs['requested']})"
    safe_addstr(y, 1, f"Bridge: {mode_str}  thermal:{bs['thermal']}  gov:{bs['governor']}")
    y += 1

    # 인터럽트 (IRQ)
    if irq_tracker:
        t = irq_tracker
        safe_addstr(y, 1,
            f"IRQ: {t.wlan_name} {t.prev_wlan} +{t.delta_wlan}/s (avg:{t.avg_wlan})"
            f"  {t.eth_name} {t.prev_eth_data} +{t.delta_eth_data}/s (avg:{t.avg_eth_data})"
            f"  err:{t.prev_eth_err} +{t.delta_eth_err}/s")
        y += 1

    safe_addstr(y, 1, sep)
    y += 1

    # 스캔 / 4-Way / 로밍소요
    scan_str = wpa_tracker.last_scan_time or "idle"
    hs_str = f"{wpa_tracker.last_handshake_ms}ms" if wpa_tracker.last_handshake_ms else "-"
    roam_ms_str = f"{wpa_tracker.last_roam_ms}ms" if wpa_tracker.last_roam_ms else "-"
    safe_addstr(y, 1, f"Scan: {scan_str}")
    safe_addstr(y, 22, f"4-Way: {hs_str}")
    safe_addstr(y, 38, f"RoamT: {roam_ms_str}")
    y += 1

    # 로밍 이벤트 (AP 변경 감지)
    roam_disp = roam_tracker.get_display()
    if roam_disp:
        safe_addstr(y, 1, f"RoamAP: * {roam_disp}", curses.A_BOLD)
    else:
        safe_addstr(y, 1, "RoamAP: -")
    y += 1

    safe_addstr(y, 1, sep)
    y += 1

    # summary.log 최근 5줄
    safe_addstr(y, 1, "[Summary Log]", curses.A_BOLD)
    y += 1
    lines = read_last_lines(summary_path, SUMMARY_LINES)
    for line in lines:
        if y >= max_y - 1:
            break
        safe_addstr(y, 1, line.strip())
        y += 1

    # ping.log 최근 N줄 (ping_monitor 서비스 활성 시)
    if ping_log_path and y < max_y - 2:
        safe_addstr(y, 1, sep)
        y += 1
        safe_addstr(y, 1, "[Ping Log]", curses.A_BOLD)
        y += 1
        plines = read_last_lines(ping_log_path, PING_LINES)
        for line in plines:
            if y >= max_y - 1:
                break
            safe_addstr(y, 1, line.strip())
            y += 1

    stdscr.refresh()


def draw_screen(stdscr, data):
    stdscr.clear()
    max_y, max_x = stdscr.getmaxyx()

    def safe_addstr(y, x, text, attr=0):
        # 화면 밖이면 그리기 스킵
        if y < 0 or y >= max_y:
            return
        if x < 0 or x >= max_x:
            return
        # 오른쪽으로 넘치는 부분은 잘라냄
        stdscr.addstr(y, x, text[: max_x - x], attr)

    y = 0
    safe_addstr(y, 2, "Realtime JSON Monitor", curses.A_BOLD | curses.A_UNDERLINE)
    y += 2

    def print_section(title, content, keys=None):
        nonlocal y
        if y >= max_y - 2:
            return  # 더 이상 쓸 공간 없음

        safe_addstr(y, 2, f"[{title}]", curses.A_BOLD)
        y += 1

        for k, v in content.items():
            if keys and k not in keys:
                continue
            if y >= max_y - 1:
                return

            v_str = "-" if v is None else str(v)
            line = f"{k:<30}: {v_str}"
            safe_addstr(y, 4, line)
            y += 1

        y += 1

    if isinstance(data, dict):
        if "info" in data and isinstance(data["info"], dict):
            print_section("info", data["info"])
        if "link" in data and isinstance(data["link"], dict):
            print_section("link", data["link"])
        if "channel_info" in data and isinstance(data["channel_info"], dict):
            print_section("channel_info", data["channel_info"])
        if "mwlan_log" in data and isinstance(data["mwlan_log"], dict):
            print_section(
                "mwlan_log",
                data["mwlan_log"],
                keys=["dot11FailedCount", "dot11RetryCount"],
            )

        if "eth_stats" in data and isinstance(data["eth_stats"], dict):
            eth = data["eth_stats"]
            if "info" in eth and isinstance(eth["info"], dict):
                print_section(
                    "Eth Interface Info",
                    eth["info"],
                    keys=[
                        "mac_address", "rx_packets", "rx_bytes", "rx_errors",
                        "rx_dropped", "tx_packets", "tx_bytes",
                        "tx_errors", "tx_dropped",
                    ],
                )
            if "phy" in eth and isinstance(eth["phy"], dict):
                print_section(
                    "Eth PHY Info",
                    eth["phy"],
                    keys=["link", "speed", "duplex"],
                )

        if "date" in data and y < max_y:
            safe_addstr(y, 2, f"[Last Updated] {data['date']}")

    stdscr.refresh()

def main(stdscr, file_path):
    global RUNNING
    global INTERVAL

    # compact 모드 객체 초기화
    wpa_tracker = None
    roam_tracker = None
    ping_log_path = None
    _cached_ip_gw = None
    irq_tracker = None
    _start_time = time.time()
    if COMPACT_MODE:
        iface = "mlan0"
        parts = file_path.split("/")
        for i, p in enumerate(parts):
            if p == "json" and i + 1 < len(parts):
                iface = parts[i + 1]
                break
        wpa_log = f"/var/log/cantops/wpa/{iface}/wpa.log"
        wpa_tracker = WpaEventTracker(wpa_log)
        roam_tracker = RoamTracker(display_sec=ROAM_DISPLAY_SEC)
        if is_service_active("wifi_ping_monitor"):
            ping_log_path = f"/var/log/cantops/ping/{iface}/ping.log"
        _cached_ip_gw = get_ip_and_gw(iface)
        conf = load_json(CONF_PATH)
        bus_type = conf.get("global", {}).get("BUS_TYPE", "sdio")
        board_type = conf.get("global", {}).get("BOARD_TYPE", "imx93")
        irq_tracker = InterruptTracker(bus_type, board_type, interval=INTERVAL)

    while RUNNING:
        try:
            data = load_json(file_path)

            if COMPACT_MODE:
                wpa_tracker.update()
                if irq_tracker:
                    irq_tracker.update()
                ap = None
                if isinstance(data, dict):
                    ap = data.get("link", {}).get("address")
                roam_tracker.check(ap)
                draw_compact_screen(stdscr, data, wpa_tracker, roam_tracker, SUMMARY_PATH, ping_log_path, _cached_ip_gw, irq_tracker, _start_time)
            else:
                draw_screen(stdscr, data)

            time.sleep(INTERVAL)

        except KeyboardInterrupt:
            RUNNING = False
            break

        except Exception as e:
            stdscr.clear()
            max_y, max_x = stdscr.getmaxyx()
            msg = f"Error: {e}"
            stdscr.addstr(0, 0, msg[:max_x - 1])
            stdscr.refresh()
            time.sleep(1)

    # curses 정상 종료
    safe_end_curses()


if __name__ == "__main__":
    # Ctrl-C / kill 에 안전하게 반응하도록 signal 등록
    #signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    parser = argparse.ArgumentParser()
    parser.add_argument("iface", nargs="?", default="mlan0",
                        choices=["mlan0", "mlan1", "eth0"],
                        help="Interface name")
    parser.add_argument("--path", help="Optional JSON path override")
    parser.add_argument("--interval", type=float, default=None,
                        help="Screen refresh interval (default: from conf or 1.0)")
    parser.add_argument("--compact", action="store_true",
                        help="Compact mode: minimal display with essential info")
    parser.add_argument("--summary-lines", type=int, default=None,
                        help="Number of summary.log lines to show (default: from conf or 5)")
    parser.add_argument("--ping-lines", type=int, default=None,
                        help="Number of ping.log lines to show (default: from conf or 5)")
    parser.add_argument("--roam-display", type=int, default=None,
                        help="Roam event display duration in seconds (default: from conf or 5)")
    args = parser.parse_args()

    # 우선순위: CLI args > wifi_init_conf.json > 하드코딩 기본값
    conf = load_json(CONF_PATH)
    mon = conf.get("monitor", {})

    INTERVAL = args.interval if args.interval is not None else mon.get("interval_sec", INTERVAL)
    COMPACT_MODE = args.compact
    SUMMARY_LINES = args.summary_lines if args.summary_lines is not None else mon.get("summary_lines", SUMMARY_LINES)
    PING_LINES = args.ping_lines if args.ping_lines is not None else mon.get("ping_lines", PING_LINES)
    ROAM_DISPLAY_SEC = args.roam_display if args.roam_display is not None else mon.get("roam_display_sec", ROAM_DISPLAY_SEC)

    if args.path:
        FILE_PATH = args.path
    else:
        FILE_PATH = f"/var/log/cantops/json/{args.iface}/link.json"

    try:
        curses.wrapper(main, FILE_PATH)
    finally:
        safe_end_curses()
