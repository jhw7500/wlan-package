import os
import sys
from types import SimpleNamespace
from unicodedata import east_asian_width
from unittest.mock import MagicMock

sys.modules.setdefault("curses", MagicMock())
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wifi_link_monitor as monitor  # noqa: E402


def test_beacon_line_uses_miss_receive_order_and_keeps_zero():
    line = monitor.format_roam_beacon_line(
        None, {"beaconMissedCount": 0, "beaconReceivedCount": 49779}, 78)
    assert line == "beacon count : 0/49779  RoamAP: -"


def test_beacon_line_marks_unavailable_values():
    assert monitor.format_roam_beacon_line(None, {}, 78) == (
        "beacon count : 없음/없음  RoamAP: -")
    assert monitor.format_roam_beacon_line(
        None, {"beaconMissedCount": True, "beaconReceivedCount": "10"}, 78
    ).startswith("beacon count : 없음/없음")


def test_roam_history_yields_space_to_beacon_count():
    roam = "00:11:22:33:44:55 -> 66:77:88:99:aa:bb  (1s ago)"
    line = monitor.format_roam_beacon_line(
        roam, {"beaconMissedCount": 12, "beaconReceivedCount": 123456}, 60)
    assert line.startswith("beacon count : 12/123456  RoamAP: * ")
    assert len(line) <= 60


def test_missing_value_text_fits_narrow_terminal_cells():
    line = monitor.format_roam_beacon_line(None, {}, 32)
    assert sum(2 if east_asian_width(ch) in ("F", "W") else 1
               for ch in line) <= 32
    assert "\n" not in line


def test_compact_screen_keeps_beacons_on_roam_row(monkeypatch):
    monkeypatch.setattr(monitor, "get_temperatures", lambda: {
        "cpu": "-", "mlan0": "-", "mlan1": "-"})
    monkeypatch.setattr(monitor, "get_cpu_usage", lambda: "-")
    monkeypatch.setattr(monitor, "get_mem_usage", lambda: "-")
    monkeypatch.setattr(monitor, "get_bridge_status", lambda: {
        "effective": "-", "requested": "-", "thermal": "-", "governor": "-"})
    monkeypatch.setattr(monitor, "get_moal_bridge_stats", lambda: {
        "w2p_fwd": "-"})
    monkeypatch.setattr(monitor, "read_last_lines", lambda *_args: [])

    class Screen:
        def __init__(self):
            self.lines = []

        def clear(self):
            pass

        def getmaxyx(self):
            return 40, 80

        def addstr(self, y, x, value, _attr=0):
            self.lines.append((y, x, value))

        def refresh(self):
            pass

    screen = Screen()
    wpa = SimpleNamespace(last_scan_time=None, last_down_ms=None,
                          last_assoc_ms=None, last_handshake_ms=None)
    roam = SimpleNamespace(get_display=lambda: None)
    monitor.draw_compact_screen(
        screen, {"mwlan_log": {"beaconMissedCount": 0,
                               "beaconReceivedCount": 49779}},
        wpa, roam, "/missing", ip_gw=("-", "-"))
    roam_rows = [(y, text) for y, _x, text in screen.lines
                 if "RoamAP:" in text]
    assert len(roam_rows) == 1
    assert roam_rows[0][1] == "beacon count : 0/49779  RoamAP: -"
    assert not any(y != roam_rows[0][0] and "beacon count" in text
                   for y, _x, text in screen.lines)
