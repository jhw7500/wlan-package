import sys
import os
from unittest.mock import MagicMock

# sUTILS / curses have heavy or env-specific deps; stub before importing the module.
sys.modules.setdefault("sUTILS", MagicMock())
sys.modules.setdefault("curses", MagicMock())

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from wifi_link_monitor import WpaEventTracker  # noqa: E402


def _line(frac, rest, base="2026-01-09 07:06:43."):
    """wpa_supplicant -d 로그 한 줄 (line[:23] = ISO 타임스탬프)."""
    return f"{base}{frac} wpa_supplicant[info] mlan0: {rest}"


def _feed(tracker, lines):
    for ln in lines:
        tracker._parse_line(ln)


def _segments(t):
    return (t.last_down_ms, t.last_assoc_ms, t.last_handshake_ms)


def test_real_switch_log_three_segments():
    """실측 switch wpa.log → Down=243, Assoc=58, 4-Way=9 (합 = 전체 outage 310ms)."""
    t = WpaEventTracker("/nonexistent")
    _feed(t, [
        _line("678", "CTRL-EVENT-DISCONNECTED bssid=00:80:4c:c7:7d:dd reason=3 locally_generated=1"),
        _line("707", "State: DISCONNECTED -> SCANNING"),
        _line("921", "Trying to associate with 04:ba:d6:ec:0b:08 (SSID='jhw_wlan' freq=5200 MHz)"),
        _line("979", "WPA: RX message 1 of 4-Way Handshake from 04:ba:d6:ec:0b:08 (ver=2)"),
        _line("988", "CTRL-EVENT-CONNECTED - Connection to 04:ba:d6:ec:0b:08 completed [id=0 id_str=]"),
    ])
    assert _segments(t) == (243, 58, 9)
    assert sum(_segments(t)) == 310


def test_seamless_roam_down_zero():
    """끊김 이벤트 없는 seamless 로밍 → Down=0."""
    t = WpaEventTracker("/x")
    _feed(t, [
        _line("100", "Trying to associate with a:a (SSID='n' freq=5180 MHz)"),
        _line("140", "WPA: RX message 1 of 4-Way Handshake from a:a (ver=2)"),
        _line("150", "CTRL-EVENT-CONNECTED - Connection to a completed"),
    ])
    assert _segments(t) == (0, 40, 10)


def test_abandoned_cycle_then_fresh_outage_no_stale_leak():
    """connect 없이 중단된 cycle의 stale anchor가 다음 outage Down으로 누수되지 않음."""
    t = WpaEventTracker("/x")
    # cycleA: 정상 연결 (상태 리셋)
    _feed(t, [
        _line("000", "CTRL-EVENT-DISCONNECTED bssid=x reason=3", base="2026-01-09 09:00:00."),
        _line("050", "Trying to associate with a:a (SSID='n' freq=5200 MHz)", base="2026-01-09 09:00:00."),
        _line("090", "CTRL-EVENT-CONNECTED - Connection to a completed", base="2026-01-09 09:00:00."),
    ])
    # cycleB: DISC + ASSOC 했으나 CONNECTED 없음 (중단)
    _feed(t, [
        _line("000", "CTRL-EVENT-DISCONNECTED bssid=x reason=3", base="2026-01-09 09:01:00."),
        _line("400", "Trying to associate with b:b (SSID='n' freq=5200 MHz)", base="2026-01-09 09:01:00."),
    ])
    # cycleC: 5분 뒤 새 outage → 재연결
    _feed(t, [
        _line("000", "CTRL-EVENT-DISCONNECTED bssid=x reason=3", base="2026-01-09 09:06:00."),
        _line("300", "Trying to associate with c:c (SSID='n' freq=5200 MHz)", base="2026-01-09 09:06:00."),
        _line("340", "WPA: RX message 1 of 4-Way Handshake from c:c (ver=2)", base="2026-01-09 09:06:00."),
        _line("400", "CTRL-EVENT-CONNECTED - Connection to c completed", base="2026-01-09 09:06:00."),
    ])
    assert t.last_down_ms == 300  # 누수 시 300300 이었음


def test_multidrop_outage_bounded_to_last_leg():
    """associate 실패 후 재드롭(연속 outage) → 마지막 레그 기준 bounded 측정."""
    t = WpaEventTracker("/x")
    _feed(t, [
        _line("000", "CTRL-EVENT-DISCONNECTED bssid=x reason=3", base="2026-01-09 13:00:00."),
        _line("100", "Trying to associate with a:a (SSID='n' freq=5200 MHz)", base="2026-01-09 13:00:00."),
        _line("200", "CTRL-EVENT-DISCONNECTED bssid=x reason=15", base="2026-01-09 13:00:00."),
        _line("300", "Trying to associate with a:a (SSID='n' freq=5200 MHz)", base="2026-01-09 13:00:00."),
        _line("340", "WPA: RX message 1 of 4-Way Handshake from a:a (ver=2)", base="2026-01-09 13:00:00."),
        _line("350", "CTRL-EVENT-CONNECTED - Connection to a completed", base="2026-01-09 13:00:00."),
    ])
    assert _segments(t) == (100, 40, 10)


def test_open_network_connect_resets_4way_to_none():
    """4-Way 없는 Open망 연결 시 last_handshake_ms가 이전 사이클 값으로 잔류하지 않음."""
    t = WpaEventTracker("/x")
    # 직전 cycle: 4-Way 있어 last_handshake_ms 설정됨
    _feed(t, [
        _line("678", "CTRL-EVENT-DISCONNECTED bssid=x reason=3"),
        _line("921", "Trying to associate with z:z (SSID='wpa' freq=5200 MHz)"),
        _line("979", "WPA: RX message 1 of 4-Way Handshake from z:z (ver=2)"),
        _line("988", "CTRL-EVENT-CONNECTED - Connection to z completed"),
    ])
    assert t.last_handshake_ms == 9
    # Open망 연결: 4-Way 없음
    _feed(t, [
        _line("000", "CTRL-EVENT-DISCONNECTED bssid=x reason=3", base="2026-01-09 08:00:00."),
        _line("100", "Trying to associate with o:o (SSID='open' freq=2412 MHz)", base="2026-01-09 08:00:00."),
        _line("130", "CTRL-EVENT-CONNECTED - Connection to o completed", base="2026-01-09 08:00:00."),
    ])
    assert t.last_handshake_ms is None   # 잔류 방지
    assert t.last_assoc_ms == 30         # 4-Way 없으면 CONNECTED까지
    assert t.last_down_ms == 100


def test_connected_without_anchors_resets_all_to_none():
    """이벤트 누락으로 앵커가 전혀 없는 CONNECTED → 세 메트릭 모두 None(잔류 방지)."""
    t = WpaEventTracker("/x")
    t.last_down_ms, t.last_assoc_ms, t.last_handshake_ms = 999, 888, 777
    _feed(t, [_line("000", "CTRL-EVENT-CONNECTED - Connection to q completed", base="2026-01-09 09:00:00.")])
    assert _segments(t) == (None, None, None)


def test_disconnected_substring_not_treated_as_connected():
    """'CTRL-EVENT-DISCONNECTED'가 'CTRL-EVENT-CONNECTED'로 오인되지 않음."""
    t = WpaEventTracker("/x")
    t._parse_line(_line("000", "CTRL-EVENT-DISCONNECTED bssid=x reason=3 locally_generated=1"))
    assert t._disc_ts is not None
    assert t.last_handshake_ms is None
