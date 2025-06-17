import json
import time
import curses
import os
import argparse

FILE_PATH = "/var/log/cantops/link/mlan0/link.json"
INTERVAL = 1

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
            stdscr.addstr(y, 4, f"{k:<30}: {v}")
            y += 1
        y += 1

    if "info" in data: print_section("info", data["info"])
    if "station_info" in data: print_section("station_info", data["station_info"])
    if "channel_info" in data: print_section("channel_info", data["channel_info"])
    #if "mwlan_log" in data : print_section("mwlan_log", data["mwlan_log"])
    if "mwlan_log" in data : print_section("mwlan_log", data["mwlan_log"], keys=["dot11FailedCount", "dot11RetryCount"])
    if "date" in data: stdscr.addstr(y, 2, f"[Last Updated] {data['date']}")
    stdscr.refresh()

def main(stdscr):
    #curses.curs_set(0)  # hide cursor
    #FILE_PATH = sys.argv[1] if len(sys.argv) > 1 

    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", default="/var/log/cantops/link/mlan0/link.json", help="Path to JSON file")
    args = parser.parse_args()

    FILE_PATH = args.path

    while True:
        data = load_json(FILE_PATH)
        draw_screen(stdscr, data)
        time.sleep(INTERVAL)

if __name__ == "__main__":
    curses.wrapper(main)
