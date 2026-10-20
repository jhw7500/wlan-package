"""로밍 조건 진입 통지(#368) — 사양 §4.4.3 Roaming(0x0004).

계약: 메인 루프가 로밍 조건(rssi < 임계)에 들어서는 순간 notify_roam 을 1회 부른다.
값은 판정 시점의 현재 접속 값이다 — ap_mac = 현재 BSSID, rssi = 판정에 쓴 RSSI.
channel/freq 는 넘기지 않아 roam_notify 가 link.json 의 현재 접속 값(같은 읽기의 짝)을 쓴다.
조건이 이어지는 동안 같은 BSSID 로는 다시 보내지 않고, 임계 위로 회복하거나 연결이
끊기면 재무장한다. 조건 중에 BSSID 가 바뀌면 새 AP 로 1회 보낸다.
"""
import sys
import os
from unittest.mock import MagicMock, call

sys.modules.setdefault("sUTILS", MagicMock())
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wifi_roam

import pytest

wifi_roam.logger = MagicMock()

A = "aa:bb:cc:00:00:01"
B = "aa:bb:cc:00:00:02"
TH = -70


def _sta(bssid, rssi):
    return {"bssid": bssid, "freq": 5180, "rssi": rssi, "ssid": "Net"}


class StopLoop(Exception):
    pass


def _run_main(monkeypatch, stations):
    """stations 를 반복마다 하나씩 get_link_info 로 돌려준 뒤 루프를 멈추고 notify mock 을 반환."""
    seq = iter(stations)

    def next_station():
        try:
            return next(seq)
        except StopIteration:
            raise StopLoop

    monkeypatch.setattr(wifi_roam, "ENABLE_PREDICTIVE_ROAM", False)
    monkeypatch.setattr(wifi_roam, "ENABLE_PING_PONG_PREVENTION", False)
    monkeypatch.setattr(wifi_roam, "GENERATE_NETWORK_BLOCKS", False)
    monkeypatch.setattr(wifi_roam, "ENABLE_STAGED_SCAN", False)
    monkeypatch.setattr(wifi_roam, "WPA_SSID", "Net")
    monkeypatch.setattr(wifi_roam, "WPA_TH_2G", TH)
    monkeypatch.setattr(wifi_roam, "WPA_TH_5G", TH)
    monkeypatch.setattr(wifi_roam, "_RELOAD_STATE", {"pending": False})
    monkeypatch.setattr(wifi_roam, "retry_pending_selection_cleanup", lambda iface: True)
    monkeypatch.setattr(wifi_roam, "reload_supplicant_conf_if_changed", lambda path: None)
    monkeypatch.setattr(wifi_roam, "roam_hint_touched", lambda state: False)
    monkeypatch.setattr(wifi_roam, "track_association", lambda station, gs: None)
    monkeypatch.setattr(wifi_roam, "set_flag", lambda value, path: None)
    monkeypatch.setattr(wifi_roam, "get_allowed_ssids", lambda ssid: ["Net"])
    # 조건 분기는 스캔 전이 경합으로 곧바로 다음 반복에 넘긴다 — 스캔·로밍 실행 없음.
    monkeypatch.setattr(
        wifi_roam, "iw_scan_to_ap_lines",
        lambda allowed, freq: wifi_roam.SCAN_TRANSITION_BUSY,
    )
    monkeypatch.setattr(wifi_roam, "interruptible_sleep", lambda seconds: None)
    monkeypatch.setattr(wifi_roam, "get_link_info", next_station)
    notify = MagicMock()
    monkeypatch.setattr(wifi_roam, "notify_roam", notify)
    with pytest.raises(StopLoop):
        wifi_roam.main()
    return notify


def test_entering_condition_notifies_once_with_current_values(monkeypatch):
    notify = _run_main(monkeypatch, [_sta(A, -50), _sta(A, -80), _sta(A, -82)])
    assert notify.call_args_list == [call(wifi_roam.IFACE, "", A, rssi=-80)]


def test_rearms_after_signal_recovers(monkeypatch):
    notify = _run_main(
        monkeypatch, [_sta(A, -80), _sta(A, -81), _sta(A, -50), _sta(A, -79)]
    )
    assert notify.call_args_list == [
        call(wifi_roam.IFACE, "", A, rssi=-80),
        call(wifi_roam.IFACE, "", A, rssi=-79),
    ]


def test_rearms_after_disconnect(monkeypatch):
    notify = _run_main(monkeypatch, [_sta(A, -80), None, _sta(A, -81)])
    assert notify.call_args_list == [
        call(wifi_roam.IFACE, "", A, rssi=-80),
        call(wifi_roam.IFACE, "", A, rssi=-81),
    ]


def test_new_bssid_while_in_condition_notifies_for_that_ap(monkeypatch):
    notify = _run_main(monkeypatch, [_sta(A, -80), _sta(B, -83), _sta(B, -84)])
    assert notify.call_args_list == [
        call(wifi_roam.IFACE, "", A, rssi=-80),
        call(wifi_roam.IFACE, "", B, rssi=-83),
    ]


def test_good_signal_never_notifies(monkeypatch):
    """보존 검사: 임계 위에서는 통지가 없다(수정 전후 모두 통과)."""
    notify = _run_main(monkeypatch, [_sta(A, -50), _sta(A, -60), _sta(A, -70)])
    notify.assert_not_called()
