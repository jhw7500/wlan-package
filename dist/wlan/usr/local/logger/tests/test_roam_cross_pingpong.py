"""cross-SSID 경로(route_cross_ssid_transition) ping-pong 방지 회귀 테스트.

계약: 데몬 자동 cross 전환은 진입 전 is_ping_pong 차단(None 반환, cooldown 미등록)
+ 성공 시 add_roam(실 결합 BSS, 폴백 to_bssid). 수동 로밍(passive_roam)·망전환
(wifi connect)은 이 함수를 거치지 않으므로 비대상 — 여기서는 데몬 경로만 고정한다.
"""
import sys
import os
from unittest.mock import MagicMock, patch

sys.modules.setdefault("sUTILS", MagicMock())
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wifi_roam
from wifi_roam import route_cross_ssid_transition, record_cross_ssid_result

import pytest

wifi_roam.logger = MagicMock()
_real_scan_transition_lock = wifi_roam.scan_transition_lock


@pytest.fixture(autouse=True)
def _private_scan_lock_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(
        wifi_roam,
        "scan_transition_lock",
        lambda iface: _real_scan_transition_lock(
            iface, run_dir=str(tmp_path / "wifi")
        ),
    )

IFACE = "mlan0"
FROM = "aa:bb:cc:dd:ee:ff"
TO = "11:22:33:44:55:66"
ACTUAL = "77:88:99:aa:bb:cc"  # 펌웨어가 실제 고른 BSS(목표와 다를 수 있음)


def _setup(monkeypatch, pingpong=True, is_pp=False, mode_a=True):
    prev = MagicMock()
    prev.is_ping_pong.return_value = is_pp
    monkeypatch.setattr(wifi_roam, "ENABLE_PING_PONG_PREVENTION", pingpong)
    monkeypatch.setattr(wifi_roam, "ping_pong_preventer", prev if pingpong else None)
    monkeypatch.setattr(wifi_roam, "GENERATE_NETWORK_BLOCKS", mode_a)
    notify = MagicMock()
    monkeypatch.setattr(wifi_roam, "notify_roam", notify)
    return prev, notify


def test_blocked_returns_none_without_attempt(monkeypatch):
    """★핵심: ping-pong이면 전환 시도 자체를 안 하고 None(차단, 실패 아님)."""
    prev, notify = _setup(monkeypatch, is_pp=True)
    with patch.object(wifi_roam, "select_network_for_ssid") as sel, \
         patch.object(wifi_roam, "connect_to_ssid") as con:
        assert route_cross_ssid_transition(IFACE, "Net", FROM, TO) is None
    sel.assert_not_called()
    con.assert_not_called()
    notify.assert_not_called()
    prev.add_roam.assert_not_called()


def test_mode_a_success_counts_actual_bssid(monkeypatch):
    """모드 A 성공 → add_roam(from, 실 결합 BSS) + notify."""
    prev, notify = _setup(monkeypatch)
    with patch.object(wifi_roam, "select_network_for_ssid", return_value=True), \
         patch.object(wifi_roam, "get_associated_bssid", return_value=ACTUAL):
        assert route_cross_ssid_transition(IFACE, "Net", FROM, TO) is True
    prev.add_roam.assert_called_once_with(FROM, ACTUAL)
    notify.assert_called_once_with(IFACE, FROM, ACTUAL)


def test_mode_a_success_fallback_to_target_bssid(monkeypatch):
    """실 결합 BSS 조회 실패("") → 목표 to_bssid로 폴백 카운트."""
    prev, notify = _setup(monkeypatch)
    with patch.object(wifi_roam, "select_network_for_ssid", return_value=True), \
         patch.object(wifi_roam, "get_associated_bssid", return_value=""):
        assert route_cross_ssid_transition(IFACE, "Net", FROM, TO) is True
    prev.add_roam.assert_called_once_with(FROM, TO)


def test_mode_a_failure_no_count(monkeypatch):
    """전환 실패 → 카운트/통지 없음, False."""
    prev, notify = _setup(monkeypatch)
    with patch.object(wifi_roam, "select_network_for_ssid", return_value=False):
        assert route_cross_ssid_transition(IFACE, "Net", FROM, TO) is False
    prev.add_roam.assert_not_called()
    notify.assert_not_called()


def test_mode_b_no_route_level_count(monkeypatch):
    """모드 B: connect_to_ssid가 내부에서 자체 add_roam — route 레벨 이중 카운트 금지."""
    prev, notify = _setup(monkeypatch, mode_a=False)
    with patch.object(wifi_roam, "connect_to_ssid", return_value=True), \
         patch.object(wifi_roam, "get_associated_bssid", return_value=ACTUAL):
        assert route_cross_ssid_transition(IFACE, "Net", FROM, TO) is True
    prev.add_roam.assert_not_called()   # route 레벨에선 안 함(내부 몫)
    notify.assert_called_once()


def test_mode_b_pingpong_blocked_returns_none(monkeypatch):
    """모드 B에서도 ping-pong 차단은 route 레벨 None(connect_to_ssid 미호출)."""
    prev, notify = _setup(monkeypatch, pingpong=True, is_pp=True, mode_a=False)
    with patch.object(wifi_roam, "connect_to_ssid") as con:
        assert route_cross_ssid_transition(IFACE, "Net", FROM, TO) is None
    con.assert_not_called()
    notify.assert_not_called()
    prev.add_roam.assert_not_called()


def test_connect_to_ssid_internal_block_returns_none(monkeypatch):
    """connect_to_ssid 직접 호출 시에도 차단=None(False 아님) — record 오염 방지 계약."""
    prev, _ = _setup(monkeypatch, is_pp=True, mode_a=False)
    with patch.object(wifi_roam.subprocess, "run") as run:
        assert wifi_roam.connect_to_ssid(IFACE, "Net", FROM, TO) is None
    run.assert_not_called()
    prev.add_roam.assert_not_called()


def test_pingpong_disabled_normal_flow(monkeypatch):
    """기능 off → 차단/카운트 없이 정상 전환."""
    prev, notify = _setup(monkeypatch, pingpong=False)
    with patch.object(wifi_roam, "select_network_for_ssid", return_value=True), \
         patch.object(wifi_roam, "get_associated_bssid", return_value=ACTUAL):
        assert route_cross_ssid_transition(IFACE, "Net", FROM, TO) is True
    notify.assert_called_once()


def test_record_none_is_noop():
    """★record: ok=None(차단)은 clear/register 모두 미호출 — cooldown 오염 방지."""
    cd = MagicMock()
    record_cross_ssid_result(cd, "Net", None, 8.0)
    cd.clear.assert_not_called()
    cd.register_failure.assert_not_called()
    # 기존 계약 무회귀
    record_cross_ssid_result(cd, "Net", True, 8.0)
    cd.clear.assert_called_once_with("Net")
    record_cross_ssid_result(cd, "Net", False, 8.0)
    cd.register_failure.assert_called_once_with("Net", 8.0)
