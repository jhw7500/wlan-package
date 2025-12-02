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

    while RUNNING:
        try:
            data = load_json(file_path)
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
