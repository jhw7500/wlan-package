# Compact Monitor Mode 구현 계획

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** wifi_link_monitor.py에 `--compact` 모드를 추가하여 최소 라인수로 핵심 WiFi 상태를 실시간 표시

**Architecture:** 기존 `draw_screen()` 함수는 유지하고, `--compact` 인자 추가 시 `draw_compact_screen()` 호출로 분기. 시스템 정보(온도, CPU, MEM)는 `/proc` 및 `mlanutl` 직접 읽기. wpa.log 파싱으로 스캔/로밍/핸드쉐이크 이벤트 실시간 추적.

**Tech Stack:** Python 3, curses, subprocess, re, collections.deque

---

## 화면 레이아웃 (약 20줄)

```
 Compact WiFi Monitor [mlan0]              2026-03-10 14:23:45
─────────────────────────────────────────────────────────────
 SSID: MyNetwork   AP: de:ad:be:ef:00:02   CH: 40(5200)
 RSSI: -42/-45 dBm  TX: 650.0M  RX: 350.0M  BW: 80MHz
 Temp: 65 / 55 / 48    Usage: CPU 23% / MEM 45%
─────────────────────────────────────────────────────────────
 Scan: 14:23:40         4-Way: 125ms
 Roam: ★ de:ad:be:ef:00:01 → 00:03  (3s ago)
─────────────────────────────────────────────────────────────
 [Summary Log]
 14:23:45 || mlan0 | 5180 | de:ad:.. | -42/-45 dBm || ...
 14:23:44 || mlan0 | 5180 | de:ad:.. | -41/-44 dBm || ...
 14:23:43 || mlan0 | 5180 | de:ad:.. | -43/-46 dBm || ...
 14:23:42 || mlan0 | 5180 | de:ad:.. | -42/-44 dBm || ...
 14:23:41 || mlan0 | 5180 | de:ad:.. | -40/-43 dBm || ...
```

## 데이터 소스 정리

| 항목 | 소스 | 방법 |
|------|------|------|
| info (address, ssid, channel, freq) | link.json → `info` | JSON 읽기 |
| temperature (cpu/mlan0/mlan1) | `/sys/devices/virtual/thermal/thermal_zone0/temp`, `mlanutl mlanX get_sensor_temp` | 파일 읽기 + subprocess |
| CPU/MEM usage | `/proc/stat` (2회 차분), `/proc/meminfo` | 파일 읽기 |
| 스캔 발생 | wpa.log `CTRL-EVENT-SCAN-STARTED` | 로그 tail 파싱 |
| 로밍 감지 | link.json `link.address` 변경 | 이전값 비교 |
| summary.log 최근 5줄 | `/var/log/cantops/summary/summary.log` | tail 읽기 |
| 4-Way Handshake 시간 | wpa.log `WPA: RX message 1 of 4-Way` ~ `CTRL-EVENT-CONNECTED` | 로그 파싱 시간차 |

## 파일 구조

- **수정 파일:** `dist/wlan/usr/local/logger/wifi_link_monitor.py` (유일한 수정 대상)

---

## Chunk 1: 시스템 정보 수집 함수들

### Task 1: 온도 읽기 함수 추가

**Files:**
- Modify: `dist/wlan/usr/local/logger/wifi_link_monitor.py:26` (load_json 위에)

- [ ] **Step 1: `get_temperatures()` 함수 작성**

`wifi_logger_temp.sh`와 동일한 소스에서 온도를 읽는다.

```python
import subprocess
import re

def get_temperatures():
    """CPU / mlan0 / mlan1 온도 읽기 (°C 정수)"""
    temps = {"cpu": "-", "mlan0": "-", "mlan1": "-"}

    # CPU 온도: /sys/devices/virtual/thermal/thermal_zone0/temp (밀리도)
    try:
        with open("/sys/devices/virtual/thermal/thermal_zone0/temp", "r") as f:
            temps["cpu"] = str(int(f.read().strip()) // 1000)
    except Exception:
        pass

    # MLAN 온도: mlanutl mlanX get_sensor_temp → "Sensor temp = XX"
    for iface in ("mlan0", "mlan1"):
        try:
            out = subprocess.check_output(
                ["mlanutl", iface, "get_sensor_temp"],
                timeout=2, stderr=subprocess.DEVNULL
            ).decode()
            m = re.search(r"=\s*(\d+)", out)
            if m:
                temps[iface] = m.group(1)
        except Exception:
            pass

    return temps
```

- [ ] **Step 2: 커밋**

```bash
git add dist/wlan/usr/local/logger/wifi_link_monitor.py
git commit -m "feat(monitor): add get_temperatures() for compact mode"
```

---

### Task 2: CPU/MEM 사용량 함수 추가

**Files:**
- Modify: `dist/wlan/usr/local/logger/wifi_link_monitor.py`

- [ ] **Step 1: `get_cpu_mem_usage()` 함수 작성**

`/proc/stat` 차분 방식은 sleep이 필요하므로, 간단하게 `/proc/loadavg` + `/proc/meminfo` 사용.
단, 실시간 CPU%가 필요하므로 이전 값을 저장하고 매 루프에서 차분 계산.

```python
_prev_cpu = None

def get_cpu_usage():
    """CPU 사용률 (%) - /proc/stat 기반 차분 계산"""
    global _prev_cpu
    try:
        with open("/proc/stat", "r") as f:
            line = f.readline()  # cpu  user nice system idle ...
        parts = line.split()
        vals = [int(x) for x in parts[1:]]
        idle = vals[3] + vals[4]  # idle + iowait
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
    """메모리 사용률 (%) - /proc/meminfo 기반"""
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
```

- [ ] **Step 2: 커밋**

```bash
git add dist/wlan/usr/local/logger/wifi_link_monitor.py
git commit -m "feat(monitor): add CPU/MEM usage functions"
```

---

### Task 3: wpa.log 이벤트 파서 추가

**Files:**
- Modify: `dist/wlan/usr/local/logger/wifi_link_monitor.py`

- [ ] **Step 1: wpa.log 이벤트 추적 클래스 작성**

wpa.log 형식 (rsyslog myformat):
```
2026-03-10 14:23:45.123 wpa_supplicant[info] mlan0: WPA: RX message 1 of 4-Way Handshake
2026-03-10 14:23:45.248 wpa_supplicant[info] mlan0: CTRL-EVENT-CONNECTED ...
2026-03-10 14:23:40.100 wpa_supplicant[info] mlan0: CTRL-EVENT-SCAN-STARTED
```

```python
from collections import deque
from datetime import datetime

class WpaEventTracker:
    """wpa.log를 tail 방식으로 파싱하여 이벤트 추적"""

    def __init__(self, wpa_log_path):
        self.path = wpa_log_path
        self.last_pos = 0
        self.last_scan_time = None       # 마지막 스캔 시작 시간 (str)
        self.last_handshake_ms = None    # 마지막 4-Way 소요시간 (ms)
        self._hs_start = None            # 핸드쉐이크 시작 datetime

    def update(self):
        """새로운 로그 라인 읽어서 이벤트 갱신"""
        try:
            with open(self.path, "r") as f:
                f.seek(0, 2)  # EOF
                size = f.tell()
                if size < self.last_pos:
                    # 로그 로테이션 발생
                    self.last_pos = 0
                f.seek(self.last_pos)
                new_lines = f.readlines()
                self.last_pos = f.tell()
        except Exception:
            return

        for line in new_lines:
            self._parse_line(line)

    def _parse_timestamp(self, line):
        """로그 라인에서 타임스탬프 추출: '2026-03-10 14:23:45.123 ...'"""
        try:
            ts_str = line[:23]  # YYYY-MM-DD HH:MM:SS.mmm
            return datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S.%f")
        except Exception:
            return None

    def _parse_line(self, line):
        # 스캔 시작 감지
        if "CTRL-EVENT-SCAN-STARTED" in line:
            ts = self._parse_timestamp(line)
            if ts:
                self.last_scan_time = ts.strftime("%H:%M:%S")

        # 4-Way Handshake 시작 (message 1)
        if "WPA: RX message 1 of 4-Way" in line:
            self._hs_start = self._parse_timestamp(line)

        # 4-Way Handshake 완료 (CONNECTED)
        if "CTRL-EVENT-CONNECTED" in line and self._hs_start:
            ts = self._parse_timestamp(line)
            if ts:
                delta = (ts - self._hs_start).total_seconds() * 1000
                self.last_handshake_ms = int(delta)
                self._hs_start = None
```

- [ ] **Step 2: 커밋**

```bash
git add dist/wlan/usr/local/logger/wifi_link_monitor.py
git commit -m "feat(monitor): add WpaEventTracker for scan/handshake events"
```

---

### Task 4: summary.log 최근 N줄 읽기 함수

**Files:**
- Modify: `dist/wlan/usr/local/logger/wifi_link_monitor.py`

- [ ] **Step 1: `read_last_lines()` 함수 작성**

```python
def read_last_lines(filepath, n=5):
    """파일의 마지막 N줄 반환 (deque 활용, 효율적)"""
    try:
        with open(filepath, "rb") as f:
            # 뒤에서부터 읽기
            f.seek(0, 2)
            size = f.tell()
            # 최대 4KB만 읽음 (summary.log 한 줄 ~150바이트)
            read_size = min(size, 4096)
            f.seek(size - read_size)
            data = f.read().decode("utf-8", errors="replace")
        lines = data.strip().split("\n")
        return lines[-n:]
    except Exception:
        return []
```

- [ ] **Step 2: 커밋**

```bash
git add dist/wlan/usr/local/logger/wifi_link_monitor.py
git commit -m "feat(monitor): add read_last_lines() for summary log"
```

---

## Chunk 2: Compact 화면 그리기 + CLI 통합

### Task 5: 로밍 감지 로직

**Files:**
- Modify: `dist/wlan/usr/local/logger/wifi_link_monitor.py`

- [ ] **Step 1: 로밍 상태 추적 변수 및 감지 로직**

`main()` 루프 내에서 이전 AP MAC과 비교하여 로밍 감지. 최소 3초 동안 표시.

```python
class RoamTracker:
    """AP MAC 변경 기반 로밍 감지, 최소 display_sec 동안 표시"""

    def __init__(self, display_sec=3):
        self.display_sec = display_sec
        self.prev_ap = None
        self.roam_from = None
        self.roam_to = None
        self.roam_time = None  # time.time()

    def check(self, current_ap):
        """현재 AP MAC을 받아 로밍 발생 여부 갱신"""
        if current_ap and self.prev_ap and current_ap != self.prev_ap:
            self.roam_from = self.prev_ap
            self.roam_to = current_ap
            self.roam_time = time.time()
        if current_ap:
            self.prev_ap = current_ap

    def get_display(self):
        """표시할 로밍 문자열 반환. 없으면 None"""
        if self.roam_time is None:
            return None
        elapsed = time.time() - self.roam_time
        if elapsed > self.display_sec:
            return None
        ago = int(elapsed)
        return f"{self.roam_from} -> {self.roam_to}  ({ago}s ago)"
```

- [ ] **Step 2: 커밋**

```bash
git add dist/wlan/usr/local/logger/wifi_link_monitor.py
git commit -m "feat(monitor): add RoamTracker for AP change detection"
```

---

### Task 6: `draw_compact_screen()` 함수 작성

**Files:**
- Modify: `dist/wlan/usr/local/logger/wifi_link_monitor.py`

- [ ] **Step 1: compact 화면 그리기 함수 구현**

```python
def draw_compact_screen(stdscr, data, wpa_tracker, roam_tracker, summary_path):
    """최소 라인으로 핵심 정보 표시"""
    stdscr.clear()
    max_y, max_x = stdscr.getmaxyx()
    sep = "─" * min(max_x - 1, 60)

    def safe_addstr(y, x, text, attr=0):
        if y < 0 or y >= max_y or x < 0 or x >= max_x:
            return
        stdscr.addstr(y, x, text[: max_x - x], attr)

    y = 0
    info = data.get("info", {}) if isinstance(data, dict) else {}
    link = data.get("link", {}) if isinstance(data, dict) else {}
    date_str = data.get("date", "") if isinstance(data, dict) else ""

    iface = info.get("address", "-")  # 모니터 대상 MAC은 표시 안 함
    ssid = info.get("ssid", "-")
    ch = info.get("channel", "-")
    freq = info.get("freq", "-")
    width = info.get("width", "-")
    ap_addr = link.get("address", "-")

    # 헤더
    title = "Compact WiFi Monitor"
    safe_addstr(y, 1, title, curses.A_BOLD)
    safe_addstr(y, len(title) + 2, date_str)
    y += 1
    safe_addstr(y, 1, sep)
    y += 1

    # 1) info: SSID, AP, CH(freq)
    ch_freq = f"{ch}({freq})" if ch != "-" and freq != "-" else f"{ch}"
    safe_addstr(y, 1, f"SSID: {ssid}   AP: {ap_addr}   CH: {ch_freq}")
    y += 1

    # 2) 신호 / 전송률 / 대역폭
    sig = link.get("signal", "-")
    sig_avg = link.get("signal_avg", "-")
    tx_rate = link.get("tx_bitrate", "-")
    rx_rate = link.get("rx_bitrate", "-")
    # 간결하게: -42 dBm → -42
    sig_short = sig.replace(" dBm", "") if isinstance(sig, str) else sig
    sig_avg_short = sig_avg.replace(" dBm", "") if isinstance(sig_avg, str) else sig_avg
    # 전송률도 간결하게
    tx_short = tx_rate.split(" ")[0] if isinstance(tx_rate, str) else tx_rate
    rx_short = rx_rate.split(" ")[0] if isinstance(rx_rate, str) else rx_rate
    safe_addstr(y, 1, f"RSSI: {sig_short}/{sig_avg_short} dBm  TX: {tx_short}M  RX: {rx_short}M  BW: {width}")
    y += 1

    # 3) 온도
    temps = get_temperatures()
    safe_addstr(y, 1,
        f"Temp: {temps['cpu']} / {temps['mlan0']} / {temps['mlan1']}",
    )
    # CPU/MEM
    cpu = get_cpu_usage()
    mem = get_mem_usage()
    temp_end = 38  # 대략 Temp 문자열 끝 위치
    safe_addstr(y, temp_end, f"Usage: CPU {cpu}% / MEM {mem}%")
    y += 1

    safe_addstr(y, 1, sep)
    y += 1

    # 4) 스캔 / 4-Way
    scan_str = wpa_tracker.last_scan_time or "idle"
    hs_str = f"{wpa_tracker.last_handshake_ms}ms" if wpa_tracker.last_handshake_ms else "-"
    safe_addstr(y, 1, f"Scan: {scan_str}")
    safe_addstr(y, 25, f"4-Way: {hs_str}")
    y += 1

    # 5) 로밍 표시
    roam_disp = roam_tracker.get_display()
    if roam_disp:
        safe_addstr(y, 1, f"Roam: * {roam_disp}", curses.A_BOLD)
    else:
        safe_addstr(y, 1, "Roam: -")
    y += 1

    safe_addstr(y, 1, sep)
    y += 1

    # 6) summary.log 최근 5줄
    safe_addstr(y, 1, "[Summary Log]", curses.A_BOLD)
    y += 1
    lines = read_last_lines(summary_path, 5)
    for line in lines:
        if y >= max_y - 1:
            break
        # 줄이 너무 길면 잘라냄
        safe_addstr(y, 1, line.strip())
        y += 1

    stdscr.refresh()
```

- [ ] **Step 2: 커밋**

```bash
git add dist/wlan/usr/local/logger/wifi_link_monitor.py
git commit -m "feat(monitor): implement draw_compact_screen()"
```

---

### Task 7: `main()` 함수에 compact 모드 분기 추가

**Files:**
- Modify: `dist/wlan/usr/local/logger/wifi_link_monitor.py:109-132`

- [ ] **Step 1: main() 수정 — compact 모드용 객체 초기화 및 루프 분기**

```python
COMPACT_MODE = False
SUMMARY_PATH = "/var/log/cantops/summary/summary.log"

def main(stdscr, file_path):
    global RUNNING
    global INTERVAL

    # compact 모드 객체 초기화
    wpa_tracker = None
    roam_tracker = None
    if COMPACT_MODE:
        iface = "mlan0"
        # file_path에서 인터페이스명 추출
        # 예: /var/log/cantops/json/mlan0/link.json → mlan0
        parts = file_path.split("/")
        for i, p in enumerate(parts):
            if p == "json" and i + 1 < len(parts):
                iface = parts[i + 1]
                break
        wpa_log = f"/var/log/cantops/wpa/{iface}/wpa.log"
        wpa_tracker = WpaEventTracker(wpa_log)
        roam_tracker = RoamTracker(display_sec=3)

    while RUNNING:
        try:
            data = load_json(file_path)

            if COMPACT_MODE:
                wpa_tracker.update()
                ap = data.get("link", {}).get("address") if isinstance(data, dict) else None
                roam_tracker.check(ap)
                draw_compact_screen(stdscr, data, wpa_tracker, roam_tracker, SUMMARY_PATH)
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

    safe_end_curses()
```

- [ ] **Step 2: 커밋**

```bash
git add dist/wlan/usr/local/logger/wifi_link_monitor.py
git commit -m "feat(monitor): integrate compact mode into main loop"
```

---

### Task 8: argparse에 `--compact` 인자 추가

**Files:**
- Modify: `dist/wlan/usr/local/logger/wifi_link_monitor.py:135-159`

- [ ] **Step 1: argparse 수정**

```python
if __name__ == "__main__":
    signal.signal(signal.SIGTERM, signal_handler)

    parser = argparse.ArgumentParser()
    parser.add_argument("iface", nargs="?", default="mlan0",
                        choices=["mlan0", "mlan1", "eth0"],
                        help="Interface name")
    parser.add_argument("--path", help="Optional JSON path override")
    parser.add_argument("--interval", type=float, default=1.0,
                        help="Screen refresh interval")
    parser.add_argument("--compact", action="store_true",
                        help="Compact mode: minimal display with essential info")
    args = parser.parse_args()

    INTERVAL = args.interval
    COMPACT_MODE = args.compact

    if args.path:
        FILE_PATH = args.path
    else:
        FILE_PATH = f"/var/log/cantops/json/{args.iface}/link.json"

    try:
        curses.wrapper(main, FILE_PATH)
    finally:
        safe_end_curses()
```

- [ ] **Step 2: 전체 동작 확인**

```bash
# 기본 모드 (기존과 동일)
python3 dist/wlan/usr/local/logger/wifi_link_monitor.py mlan0

# compact 모드
python3 dist/wlan/usr/local/logger/wifi_link_monitor.py mlan0 --compact

# compact 모드 + 빠른 갱신
python3 dist/wlan/usr/local/logger/wifi_link_monitor.py mlan0 --compact --interval 0.5
```

- [ ] **Step 3: 최종 커밋**

```bash
git add dist/wlan/usr/local/logger/wifi_link_monitor.py
git commit -m "feat(monitor): add --compact CLI argument for compact mode"
```

---

## 구현 순서 요약

| Task | 내용 | 의존성 |
|------|------|--------|
| 1 | `get_temperatures()` | 없음 |
| 2 | `get_cpu_usage()`, `get_mem_usage()` | 없음 |
| 3 | `WpaEventTracker` 클래스 | 없음 |
| 4 | `read_last_lines()` | 없음 |
| 5 | `RoamTracker` 클래스 | 없음 |
| 6 | `draw_compact_screen()` | Task 1~5 |
| 7 | `main()` 수정 | Task 3, 5, 6 |
| 8 | argparse `--compact` 추가 | Task 7 |

**Task 1~5는 독립적이므로 병렬 구현 가능.**

## 사용법

```bash
# alias 추가 권장 (00-cantops.sh에)
alias monitorc='python3 /usr/local/logger/wifi_link_monitor.py mlan0 --compact'
```
