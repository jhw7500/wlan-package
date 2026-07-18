"""roam_to_bssid 성공 판정 회귀 테스트.

핵심: wpa_cli는 supplicant가 "FAIL"을 응답해도 exit 0을 준다. 따라서 성공 판정은
returncode가 아니라 (1) 응답 텍스트 "OK"(명령 수락) + (2) wpa_cli status 폴링으로
wpa_state=COMPLETED@target(재결합 확인)이어야 한다. 이 파일은 그 계약을 고정한다.
성공 확인(confirm_roam)은 roam_notify 공용 함수로 단일화되어 있어 폴링 관련 mock은
roam_notify를 대상으로 한다.
"""
import sys
import os
from unittest.mock import MagicMock, patch

sys.modules.setdefault("sUTILS", MagicMock())
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wifi_roam
import roam_notify
from wifi_roam import roam_to_bssid

import pytest

wifi_roam.logger = MagicMock()

FROM = "aa:bb:cc:dd:ee:ff"
TARGET = "11:22:33:44:55:66"


class _Run:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _setup(monkeypatch, pingpong=True):
    """ping-pong preventer(항상 통과)와 부수효과 스텁을 설치하고 mock을 돌려준다."""
    prev = MagicMock()
    prev.is_ping_pong.return_value = False
    monkeypatch.setattr(wifi_roam, "ENABLE_PING_PONG_PREVENTION", pingpong)
    monkeypatch.setattr(wifi_roam, "ping_pong_preventer", prev)
    notify = MagicMock()
    optimize = MagicMock()
    monkeypatch.setattr(wifi_roam, "notify_roam", notify)
    monkeypatch.setattr(wifi_roam, "optimize_post_roam_connectivity", optimize)
    monkeypatch.setattr(roam_notify.time, "sleep", lambda *_: None)  # confirm 폴링 sleep
    return prev, notify, optimize


def test_fail_reply_with_exit0_is_not_success(monkeypatch):
    """★핵심 결함: supplicant가 FAIL(exit 0)을 줘도 성공으로 오판하면 안 된다."""
    prev, notify, optimize = _setup(monkeypatch)
    with patch.object(wifi_roam.subprocess, "run", return_value=_Run(0, "FAIL\n")), \
         patch.object(roam_notify, "get_associated_bssid") as gab:
        assert roam_to_bssid(FROM, TARGET) is False
    gab.assert_not_called()          # 거부됐으니 status 폴링(confirm)까지 가지 않는다
    notify.assert_not_called()       # opcd 오통지 없음
    prev.add_roam.assert_not_called()  # ping-pong 카운터 오염 없음


def test_failbusy_reply_with_exit0_is_not_success(monkeypatch):
    prev, notify, _ = _setup(monkeypatch)
    with patch.object(wifi_roam.subprocess, "run", return_value=_Run(0, "FAIL-BUSY\n")), \
         patch.object(roam_notify, "get_associated_bssid") as gab:
        assert roam_to_bssid(FROM, TARGET) is False
    gab.assert_not_called()
    notify.assert_not_called()
    prev.add_roam.assert_not_called()


def test_ok_but_never_confirmed_is_failure(monkeypatch):
    """OK(명령 수락)만으로는 성공이 아니다 — status가 목표로 안 오면 실패."""
    prev, notify, _ = _setup(monkeypatch)
    with patch.object(wifi_roam.subprocess, "run", return_value=_Run(0, "OK\n")), \
         patch.object(roam_notify, "get_associated_bssid", return_value=FROM), \
         patch.object(roam_notify.time, "monotonic", side_effect=[0.0, 0.0, 10.0]):
        assert roam_to_bssid(FROM, TARGET) is False
    notify.assert_not_called()
    prev.add_roam.assert_not_called()


def test_ok_and_confirmed_is_success(monkeypatch):
    """OK + status가 목표 BSSID로 COMPLETED → 성공. 이때만 통지/카운트."""
    prev, notify, optimize = _setup(monkeypatch)
    with patch.object(wifi_roam.subprocess, "run", return_value=_Run(0, "OK\n")), \
         patch.object(roam_notify, "get_associated_bssid", return_value=TARGET), \
         patch.object(roam_notify.time, "monotonic", return_value=0.0):
        assert roam_to_bssid(FROM, TARGET, channel=36, freq=5180, rssi=-60) is True
    prev.add_roam.assert_called_once_with(FROM, TARGET)
    optimize.assert_called_once()
    notify.assert_called_once()
    args, kwargs = notify.call_args
    assert args[0] == wifi_roam.IFACE and args[1] == FROM and args[2] == TARGET


def test_confirm_waits_for_target_not_first_completed(monkeypatch):
    """roam 진행 전 첫 status는 이전 AP(COMPLETED)일 수 있다 → 목표 일치까지 폴링."""
    prev, notify, _ = _setup(monkeypatch)
    with patch.object(wifi_roam.subprocess, "run", return_value=_Run(0, "OK\n")), \
         patch.object(roam_notify, "get_associated_bssid", side_effect=[FROM, TARGET]) as gab, \
         patch.object(roam_notify.time, "monotonic", return_value=0.0):
        assert roam_to_bssid(FROM, TARGET) is True
    assert gab.call_count == 2       # 첫 조회(이전 AP) 무시, 둘째(목표)에서 확정
    notify.assert_called_once()


def test_transport_failure_nonzero_exit_is_failure(monkeypatch):
    """소켓 전송 실패(wpa_cli exit != 0)도 실패."""
    prev, notify, _ = _setup(monkeypatch)
    with patch.object(wifi_roam.subprocess, "run", return_value=_Run(255, "")), \
         patch.object(roam_notify, "get_associated_bssid") as gab:
        assert roam_to_bssid(FROM, TARGET) is False
    gab.assert_not_called()
    notify.assert_not_called()
