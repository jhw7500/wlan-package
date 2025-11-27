import json
import time
import curses
import os
import argparse
import sys
import signal

INTERVAL = 1.0
FILE_PATH = None
RUNNING = True

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

def draw_screen(stdscr, data):
    stdscr.clear()
    stdscr.addstr(0, 2, "Realtime JSON Monitor - 'ilog'", curses.A_BOLD | curses.A_UNDERLINE)

    y = 2
    def print_section(title, content, keys=None):
        nonlocal y
        stdscr.addstr(y, 2, f"[{title}]", curses.A_BOLD)
        y += 1
        for k, v in content.items():
            if keys and k not in keys:
                continue
            # null 값 '-'로 출력
            v_str = "-" if v is None else str(v)
            stdscr.addstr(y, 4, f"{k:<30}: {v_str}")
            y += 1
        y += 1

    if "info" in data: print_section("info", data["info"])
    if "link" in data: print_section("link", data["link"])
    if "channel_info" in data: print_section("channel_info", data["channel_info"])
    if "mwlan_log" in data: print_section("mwlan_log", data["mwlan_log"], keys=["dot11FailedCount", "dot11RetryCount"])

    # ? 추가: eth_stats 표시

    if "eth_stats" in data:
        if "info" in data["eth_stats"]:
            #print_section("Eth Interface Info", data["eth_stats"]["info"])
            print_section("Eth Interface Info", data["eth_stats"]["info"], keys=["mac_address", "rx_packets", "rx_bytes", "rx_errors", "rx_dropped", "tx_packets", "tx_bytes", "tx_errors", "tx_dropped"])
        if "phy" in data["eth_stats"]:
            #print_section("Eth PHY Info", data["eth_stats"]["phy"])
            print_section("Eth PHY Info", data["eth_stats"]["phy"], keys=["link", "speed", "duplex"])

    if "date" in data:
        stdscr.addstr(y, 2, f"[Last Updated] {data['date']}")
    stdscr.refresh()

def main(stdscr, file_path):
    global RUNNING
    global INTERVAL

    while RUNNING:
        try:
            data = load_json(file_path)
            draw_screen(stdscr, data)
            time.sleep(INTERVAL)

        except KeyboardInterrupt:
            RUNNING = False
            break

        except Exception as e:
            # 파일 읽기 실패 같은 예외도 화면 깔끔하게 처리
            stdscr.clear()
            stdscr.addstr(0, 0, f"Error: {e}")
            stdscr.refresh()
            time.sleep(1)

    # curses 정상 종료
    safe_end_curses()


if __name__ == "__main__":
    # Ctrl-C / kill 에 안전하게 반응하도록 signal 등록
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    parser = argparse.ArgumentParser()
    parser.add_argument("iface", nargs="?", default="mlan0",
                        choices=["mlan0", "mlan1", "eth0"],
                        help="Interface name")
    parser.add_argument("--path", help="Optional JSON path override")
    parser.add_argument("--interval", type=float, default=1.0,
                        help="Screen refresh interval")
    args = parser.parse_args()

    INTERVAL = args.interval

    if args.path:
        FILE_PATH = args.path
    else:
        FILE_PATH = f"/var/log/cantops/json/{args.iface}/link.json"

    try:
        curses.wrapper(main, FILE_PATH)
    finally:
        safe_end_curses()
