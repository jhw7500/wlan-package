"""passive_roam.roam_to_ap 성공 판정 회귀 테스트 (수동 `wifi roam` 경로).

same-SSID(wpa_cli roam)는 wpa_cli가 "FAIL"에도 exit 0을 주므로 응답 텍스트 "OK" +
wpa_cli status 폴링(COMPLETED@target, roam_notify.confirm_roam 공용) 확인 시에만
성공(exit 0, notify)이어야 한다. cross-SSID(wifi connect 래퍼) 경로는 종전 계약 유지.
"""
import sys
import os
from unittest.mock import MagicMock, patch

sys.modules.setdefault("sUTILS", MagicMock())
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import passive_roam
import roam_notify
from passive_roam import roam_to_ap

import pytest

IFACE = "mlan0"
FROM = "aa:bb:cc:dd:ee:ff"
TARGET = "11:22:33:44:55:66"


class _Run:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _ap(ssid="Net", is_current=False):
    return {"bssid": TARGET, "ch": 36, "ss": -60, "ssid": ssid, "is_current": is_current}


def _setup(monkeypatch):
    notify = MagicMock()
    monkeypatch.setattr(passive_roam, "notify_roam", notify)
    monkeypatch.setattr(passive_roam, "read_current_bssid", lambda *_a, **_k: FROM)
    monkeypatch.setattr(roam_notify.time, "sleep", lambda *_: None)  # confirm 폴링 sleep
    return notify


def test_is_current_no_roam(monkeypatch):
    notify = _setup(monkeypatch)
    with patch.object(passive_roam.subprocess, "run") as run:
        assert roam_to_ap(IFACE, _ap(is_current=True), current_ssid="Net") == 0
    run.assert_not_called()
    notify.assert_not_called()


def test_same_ssid_fail_exit0_is_failure(monkeypatch):
    """★핵심: same-SSID FAIL(exit 0) → exit 1, 통지 없음."""
    notify = _setup(monkeypatch)
    with patch.object(passive_roam.subprocess, "run", return_value=_Run(0, "FAIL\n")), \
         patch.object(roam_notify, "get_associated_bssid") as gab:
        assert roam_to_ap(IFACE, _ap(), current_ssid="Net") == 1
    gab.assert_not_called()
    notify.assert_not_called()


def test_same_ssid_ok_not_confirmed_is_failure(monkeypatch):
    notify = _setup(monkeypatch)
    with patch.object(passive_roam.subprocess, "run", return_value=_Run(0, "OK\n")), \
         patch.object(roam_notify, "get_associated_bssid", return_value=FROM), \
         patch.object(roam_notify.time, "monotonic", side_effect=[0.0, 0.0, 10.0]):
        assert roam_to_ap(IFACE, _ap(), current_ssid="Net") == 1
    notify.assert_not_called()


def test_same_ssid_ok_and_confirmed_is_success(monkeypatch):
    notify = _setup(monkeypatch)
    with patch.object(passive_roam.subprocess, "run", return_value=_Run(0, "OK\n")), \
         patch.object(roam_notify, "get_associated_bssid", return_value=TARGET), \
         patch.object(roam_notify.time, "monotonic", return_value=0.0):
        assert roam_to_ap(IFACE, _ap(), current_ssid="Net") == 0
    notify.assert_called_once()
    args, kwargs = notify.call_args
    assert args[0] == IFACE and args[1] == FROM and args[2] == TARGET
    assert kwargs.get("channel") == 36 and kwargs.get("rssi") == -60


def test_same_ssid_confirm_waits_for_target(monkeypatch):
    notify = _setup(monkeypatch)
    with patch.object(passive_roam.subprocess, "run", return_value=_Run(0, "OK\n")), \
         patch.object(roam_notify, "get_associated_bssid", side_effect=[FROM, TARGET]) as gab, \
         patch.object(roam_notify.time, "monotonic", return_value=0.0):
        assert roam_to_ap(IFACE, _ap(), current_ssid="Net") == 0
    assert gab.call_count == 2
    notify.assert_called_once()


def test_cross_ssid_path_unchanged(monkeypatch):
    """cross-SSID(wifi connect 래퍼): returncode 계약 유지 + status로 실결합 BSS 통지."""
    notify = _setup(monkeypatch)
    with patch.object(passive_roam.subprocess, "run", return_value=_Run(0, "")) as run, \
         patch.object(passive_roam, "get_associated_bssid", return_value=TARGET):
        # current_ssid != ap.ssid → cross-SSID 분기
        assert roam_to_ap(IFACE, _ap(ssid="Net"), current_ssid="Other") == 0
    # 래퍼 명령이 호출됐는지(wifi connect)
    assert run.call_args[0][0][:3] == ["/usr/local/bin/wifi", IFACE, "connect"]
    notify.assert_called_once_with(IFACE, FROM, TARGET)
