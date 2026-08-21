import sys
import os
from unittest.mock import MagicMock, patch

sys.modules.setdefault("sUTILS", MagicMock())

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wifi_roam
from wifi_roam import select_network_for_ssid

import pytest

wifi_roam.logger = MagicMock()

class _Run:
    """subprocess.run stub: returncode + stdout."""
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""

# list_networks 표준 출력: 헤더 1줄 + "network id / ssid / bssid / flags" 탭 구분
_LIST_NETWORKS = (
    "network id / ssid / bssid / flags\n"
    "0\tHomeNet\tany\t[CURRENT]\n"
    "1\tOfficeNet\tany\t\n"
    "2\tGuest\tany\t[DISABLED]\n"
)

def _make_side_effect(list_out=_LIST_NETWORKS, select_rc=0, enable_rc=0,
                      states=("SCANNING", "COMPLETED")):
    """wpa_cli 호출 순서: list → bssid pin → select → status* → clear → enable."""
    state_iter = iter(states)

    def side_effect(cmd, *args, **kwargs):
        sub = cmd[3]  # ["wpa_cli","-i",IFACE,<sub>,...]
        if sub == "list_networks":
            return _Run(0, list_out)
        if sub == "bssid":
            return _Run(0, "OK\n")
        if sub == "select_network":
            return _Run(select_rc, "OK\n")
        if sub == "status":
            try:
                st = next(state_iter)
            except StopIteration:
                st = "COMPLETED"
            return _Run(0, f"bssid=00:11:22:33:44:55\nssid=OfficeNet\nid=1\nwpa_state={st}\n")
        if sub == "enable_network":
            return _Run(enable_rc, "OK\n")
        return _Run(1, "")

    return side_effect

def test_select_network_success_polls_then_enables(monkeypatch):
    calls = []
    _se = _make_side_effect()  # 이터레이터를 한 번만 생성해 호출 간 state 공유

    def side_effect(cmd, *a, **k):
        calls.append(cmd[3])
        return _se(cmd, *a, **k)

    with patch.object(wifi_roam.subprocess, "run", side_effect=side_effect):
        with patch.object(wifi_roam.time, "sleep", MagicMock()):
            ok = select_network_for_ssid("mlan0", "OfficeNet", "00:11:22:33:44:55")
    assert ok is True
    assert "select_network" in calls
    assert calls[-1] == "enable_network"  # fallback 후보 복원이 마지막

def test_select_network_ssid_not_found_returns_false_no_select(monkeypatch):
    # to_ssid가 list_networks에 없으면 select_network 자체를 호출하지 않고 False
    calls = []

    def side_effect(cmd, *a, **k):
        calls.append(cmd[3])
        return _make_side_effect()(cmd, *a, **k)

    with patch.object(wifi_roam.subprocess, "run", side_effect=side_effect):
        with patch.object(wifi_roam.time, "sleep", MagicMock()):
            ok = select_network_for_ssid("mlan0", "NoSuchSSID", "00:11:22:33:44:55")
    assert ok is False
    assert "select_network" not in calls
    assert "enable_network" not in calls

def test_select_network_timeout_polls_then_false_but_restores(monkeypatch):
    # COMPLETED에 끝내 도달 못하면 False, 단 enable_network로 fallback 후보는 복원
    calls = []

    def side_effect(cmd, *a, **k):
        calls.append(cmd[3])
        # status는 항상 SCANNING (COMPLETED 미도달)
        return _make_side_effect(states=("SCANNING",) * 50)(cmd, *a, **k)

    with patch.object(wifi_roam.subprocess, "run", side_effect=side_effect):
        with patch.object(wifi_roam.time, "sleep", MagicMock()):
            ok = select_network_for_ssid("mlan0", "OfficeNet", "00:11:22:33:44:55")
    assert ok is False
    assert "enable_network" in calls  # 실패해도 후보 복원

def test_select_network_status_timeout_after_select_still_restores(monkeypatch):
    # #2 회귀: select_network 성공 후 status 폴링이 TimeoutExpired를 raise해도
    # enable_network all 로 fallback 후보를 복원해야 한다(다른 블록 영구 disabled 방지).
    calls = []

    def side_effect(cmd, *a, **k):
        sub = cmd[3]
        calls.append(sub)
        if sub == "list_networks":
            return _Run(0, _LIST_NETWORKS)
        if sub == "bssid":
            return _Run(0, "OK\n")
        if sub == "select_network":
            return _Run(0, "OK\n")
        if sub == "status":
            raise wifi_roam.subprocess.TimeoutExpired(cmd, 10)
        if sub == "enable_network":
            return _Run(0, "OK\n")
        return _Run(1, "")

    with patch.object(wifi_roam.subprocess, "run", side_effect=side_effect):
        with patch.object(wifi_roam.time, "sleep", MagicMock()):
            ok = select_network_for_ssid("mlan0", "OfficeNet", "00:11:22:33:44:55")
    assert ok is False
    assert "select_network" in calls
    assert "enable_network" in calls  # 폴링 timeout이어도 복원 호출


def test_select_network_status_exception_after_select_still_restores(monkeypatch):
    # #2 회귀: select_network 성공 후 status 폴링이 일반 Exception을 raise해도 복원
    calls = []

    def side_effect(cmd, *a, **k):
        sub = cmd[3]
        calls.append(sub)
        if sub == "list_networks":
            return _Run(0, _LIST_NETWORKS)
        if sub == "bssid":
            return _Run(0, "OK\n")
        if sub == "select_network":
            return _Run(0, "OK\n")
        if sub == "status":
            raise RuntimeError("boom")
        if sub == "enable_network":
            return _Run(0, "OK\n")
        return _Run(1, "")

    with patch.object(wifi_roam.subprocess, "run", side_effect=side_effect):
        with patch.object(wifi_roam.time, "sleep", MagicMock()):
            ok = select_network_for_ssid("mlan0", "OfficeNet", "00:11:22:33:44:55")
    assert ok is False
    assert "enable_network" in calls


def test_select_network_list_networks_timeout_does_not_restore(monkeypatch):
    # select_network 전(list_networks)에서 timeout이면 disable된 블록이 없으므로
    # enable_network 를 호출하지 않아야 한다(selected=False 경로).
    calls = []

    def side_effect(cmd, *a, **k):
        sub = cmd[3]
        calls.append(sub)
        if sub == "list_networks":
            raise wifi_roam.subprocess.TimeoutExpired(cmd, 10)
        if sub == "enable_network":
            return _Run(0, "OK\n")
        return _Run(1, "")

    with patch.object(wifi_roam.subprocess, "run", side_effect=side_effect):
        with patch.object(wifi_roam.time, "sleep", MagicMock()):
            ok = select_network_for_ssid("mlan0", "OfficeNet", "00:11:22:33:44:55")
    assert ok is False
    assert "enable_network" not in calls


def test_select_network_list_networks_fails_returns_false(monkeypatch):
    with patch.object(wifi_roam.subprocess, "run",
                      side_effect=lambda cmd, *a, **k: _Run(1, "")):
        with patch.object(wifi_roam.time, "sleep", MagicMock()):
            ok = select_network_for_ssid("mlan0", "OfficeNet", "00:11:22:33:44:55")
    assert ok is False

def test_select_network_exact_ssid_match_not_substring(monkeypatch):
    # "Office"는 "OfficeNet"의 부분문자열이지만 정확히 일치하는 블록이 없으면 False
    calls = []

    def side_effect(cmd, *a, **k):
        calls.append(cmd[3])
        return _make_side_effect()(cmd, *a, **k)

    with patch.object(wifi_roam.subprocess, "run", side_effect=side_effect):
        with patch.object(wifi_roam.time, "sleep", MagicMock()):
            ok = select_network_for_ssid("mlan0", "Office", "00:11:22:33:44:55")
    assert ok is False
    assert "select_network" not in calls


def test_select_network_fail_reply_returns_false_no_poll(monkeypatch):
    # wpa_cli는 supplicant "FAIL" 응답에도 exit 0 — 응답 텍스트 게이트로 즉시 실패해야
    # 하며 status 폴링에 진입하지 않는다. 복원(enable_network)은 방어적으로 호출한다
    # (upstream은 FAIL을 disable 전에 반환하지만 패치 supplicant 의미론을 가정하지 않음).
    calls = []

    def side_effect(cmd, *a, **k):
        sub = cmd[3]
        calls.append(sub)
        if sub == "list_networks":
            return _Run(0, _LIST_NETWORKS)
        if sub == "bssid":
            return _Run(0, "OK\n")
        if sub == "select_network":
            return _Run(0, "FAIL\n")
        if sub == "enable_network":
            return _Run(0, "OK\n")
        return _Run(1, "")

    with patch.object(wifi_roam.subprocess, "run", side_effect=side_effect):
        with patch.object(wifi_roam.time, "sleep", MagicMock()):
            ok = select_network_for_ssid("mlan0", "OfficeNet", "00:11:22:33:44:55")
    assert ok is False
    assert "status" not in calls
    assert "enable_network" in calls  # 방어적 복원(무-disable 상태면 no-op)


def test_select_network_old_ap_completed_not_success_until_target(monkeypatch):
    # 전환 직후 첫 폴은 구 AP 결합이 COMPLETED로 잔존 — ssid/id 대조로 성공 오판 금지,
    # 목표 블록 재결합(COMPLETED@target) 시점에만 성공.
    calls = []
    status_seq = iter([
        "bssid=aa:bb:cc:dd:ee:ff\nssid=HomeNet\nid=0\nwpa_state=COMPLETED\n",
        "wpa_state=SCANNING\n",
        "bssid=00:11:22:33:44:55\nssid=OfficeNet\nid=1\nwpa_state=COMPLETED\n",
    ])

    def side_effect(cmd, *a, **k):
        sub = cmd[3]
        calls.append(sub)
        if sub == "list_networks":
            return _Run(0, _LIST_NETWORKS)
        if sub == "bssid":
            return _Run(0, "OK\n")
        if sub == "select_network":
            return _Run(0, "OK\n")
        if sub == "status":
            return _Run(0, next(status_seq))
        if sub == "enable_network":
            return _Run(0, "OK\n")
        return _Run(1, "")

    with patch.object(wifi_roam.subprocess, "run", side_effect=side_effect):
        with patch.object(wifi_roam.time, "sleep", MagicMock()):
            ok = select_network_for_ssid("mlan0", "OfficeNet", "00:11:22:33:44:55")
    assert ok is True
    assert calls.count("status") == 3  # 구 AP COMPLETED·SCANNING을 지나 3번째에만 성공
    assert calls[-1] == "enable_network"


def test_select_network_wrong_ssid_completed_times_out(monkeypatch):
    # 끝까지 구 AP(COMPLETED) 잔존 — ssid/id 불일치로 성공 금지, 타임아웃 실패 + 후보 복원
    calls = []

    def side_effect(cmd, *a, **k):
        sub = cmd[3]
        calls.append(sub)
        if sub == "list_networks":
            return _Run(0, _LIST_NETWORKS)
        if sub == "bssid":
            return _Run(0, "OK\n")
        if sub == "select_network":
            return _Run(0, "OK\n")
        if sub == "status":
            return _Run(0, "bssid=aa:bb:cc:dd:ee:ff\nssid=HomeNet\nid=0\nwpa_state=COMPLETED\n")
        if sub == "enable_network":
            return _Run(0, "OK\n")
        return _Run(1, "")

    with patch.object(wifi_roam.subprocess, "run", side_effect=side_effect):
        with patch.object(wifi_roam.time, "sleep", MagicMock()):
            ok = select_network_for_ssid("mlan0", "OfficeNet", "00:11:22:33:44:55")
    assert ok is False
    assert calls.count("status") == 6  # 폴링 한도 소진
    assert "enable_network" in calls


# --- target BSSID pin/confirm/cleanup contract ---

_TARGET_BSSID = "00:11:22:33:44:55"


def test_cross_ssid_select_pins_and_confirms_exact_target_bssid(monkeypatch):
    calls = []

    def side_effect(cmd, *a, **k):
        calls.append(cmd)
        sub = cmd[3]
        if sub == "list_networks":
            return _Run(0, _LIST_NETWORKS)
        if sub == "bssid":
            return _Run(0, "OK\n")
        if sub == "select_network":
            return _Run(0, "OK\n")
        if sub == "status":
            return _Run(
                0,
                f"bssid={_TARGET_BSSID}\nssid=OfficeNet\nid=1\nwpa_state=COMPLETED\n",
            )
        if sub == "enable_network":
            return _Run(0, "OK\n")
        return _Run(1, "")

    with patch.object(wifi_roam.subprocess, "run", side_effect=side_effect):
        with patch.object(wifi_roam.time, "sleep", MagicMock()):
            ok = select_network_for_ssid("mlan0", "OfficeNet", _TARGET_BSSID)

    assert ok is True
    assert [c[3:] for c in calls] == [
        ["list_networks"],
        ["bssid", "1", _TARGET_BSSID],
        ["select_network", "1"],
        ["status"],
        ["bssid", "1", "any"],
        ["enable_network", "all"],
    ]


def test_cross_ssid_select_rejects_completed_on_wrong_bssid_and_cleans_up(monkeypatch):
    calls = []

    def side_effect(cmd, *a, **k):
        calls.append(cmd)
        sub = cmd[3]
        if sub == "list_networks":
            return _Run(0, _LIST_NETWORKS)
        if sub in ("bssid", "select_network", "enable_network"):
            return _Run(0, "OK\n")
        if sub == "status":
            return _Run(
                0,
                "bssid=aa:bb:cc:dd:ee:ff\nssid=OfficeNet\nid=1\nwpa_state=COMPLETED\n",
            )
        return _Run(1, "")

    with patch.object(wifi_roam.subprocess, "run", side_effect=side_effect):
        with patch.object(wifi_roam.time, "sleep", MagicMock()):
            ok = select_network_for_ssid("mlan0", "OfficeNet", _TARGET_BSSID)

    assert ok is False
    assert sum(c[3:] == ["bssid", "1", "any"] for c in calls) == 1
    assert sum(c[3:] == ["enable_network", "all"] for c in calls) == 1


def test_cross_ssid_pin_rejection_stops_before_select_without_spurious_cleanup(monkeypatch):
    calls = []

    def side_effect(cmd, *a, **k):
        calls.append(cmd)
        if cmd[3] == "list_networks":
            return _Run(0, _LIST_NETWORKS)
        if cmd[3] == "bssid":
            return _Run(0, "FAIL\n")
        return _Run(1, "")

    with patch.object(wifi_roam.subprocess, "run", side_effect=side_effect):
        ok = select_network_for_ssid("mlan0", "OfficeNet", _TARGET_BSSID)

    assert ok is False
    assert not any(c[3] == "select_network" for c in calls)
    assert not any(c[3:] == ["bssid", "1", "any"] for c in calls)
    assert not any(c[3] == "enable_network" for c in calls)


def test_cross_ssid_status_exception_clears_pin_and_restores_networks(monkeypatch):
    calls = []

    def side_effect(cmd, *a, **k):
        calls.append(cmd)
        sub = cmd[3]
        if sub == "list_networks":
            return _Run(0, _LIST_NETWORKS)
        if sub in ("bssid", "select_network", "enable_network"):
            return _Run(0, "OK\n")
        if sub == "status":
            raise RuntimeError("status boom")
        return _Run(1, "")

    with patch.object(wifi_roam.subprocess, "run", side_effect=side_effect):
        with patch.object(wifi_roam.time, "sleep", MagicMock()):
            ok = select_network_for_ssid("mlan0", "OfficeNet", _TARGET_BSSID)

    assert ok is False
    assert sum(c[3:] == ["bssid", "1", "any"] for c in calls) == 1
    assert sum(c[3:] == ["enable_network", "all"] for c in calls) == 1


# --- cross-SSID 라우팅 헬퍼(메인루프 분기) ---

def test_route_cross_mode_a_uses_select_network(monkeypatch):
    # GENERATE_NETWORK_BLOCKS=True → select_network_for_ssid 호출, connect_to_ssid 미호출
    monkeypatch.setattr(wifi_roam, "GENERATE_NETWORK_BLOCKS", True, raising=False)
    sel = MagicMock(return_value=True)
    con = MagicMock(return_value=True)
    monkeypatch.setattr(wifi_roam, "select_network_for_ssid", sel)
    monkeypatch.setattr(wifi_roam, "connect_to_ssid", con)
    wifi_roam.route_cross_ssid_transition(
        "mlan0", "OfficeNet", "aa:bb:cc:dd:ee:ff", "11:22:33:44:55:66"
    )
    sel.assert_called_once_with("mlan0", "OfficeNet", "11:22:33:44:55:66")
    con.assert_not_called()

def test_route_cross_mode_b_uses_connect(monkeypatch):
    # GENERATE_NETWORK_BLOCKS=False → connect_to_ssid 호출, select_network 미호출
    monkeypatch.setattr(wifi_roam, "GENERATE_NETWORK_BLOCKS", False, raising=False)
    sel = MagicMock(return_value=True)
    con = MagicMock(return_value=True)
    monkeypatch.setattr(wifi_roam, "select_network_for_ssid", sel)
    monkeypatch.setattr(wifi_roam, "connect_to_ssid", con)
    wifi_roam.route_cross_ssid_transition(
        "mlan0", "OfficeNet", "aa:bb:cc:dd:ee:ff", "11:22:33:44:55:66"
    )
    con.assert_called_once_with("mlan0", "OfficeNet", "aa:bb:cc:dd:ee:ff", "11:22:33:44:55:66")
    sel.assert_not_called()


def test_route_cross_mode_a_fail_reply_no_success_side_effects(monkeypatch):
    # select_network FAIL 수락 거부가 route까지 False로 전파 — notify_roam/add_roam
    # (핑퐁 카운터) 등 성공 부수효과가 없어야 한다.
    monkeypatch.setattr(wifi_roam, "GENERATE_NETWORK_BLOCKS", True, raising=False)
    notify = MagicMock()
    monkeypatch.setattr(wifi_roam, "notify_roam", notify)
    preventer = MagicMock()
    preventer.is_ping_pong.return_value = False
    monkeypatch.setattr(wifi_roam, "ping_pong_preventer", preventer, raising=False)

    def side_effect(cmd, *a, **k):
        sub = cmd[3]
        if sub == "list_networks":
            return _Run(0, _LIST_NETWORKS)
        if sub == "select_network":
            return _Run(0, "FAIL\n")
        return _Run(0, "OK\n")

    with patch.object(wifi_roam.subprocess, "run", side_effect=side_effect):
        with patch.object(wifi_roam.time, "sleep", MagicMock()):
            ok = wifi_roam.route_cross_ssid_transition(
                "mlan0", "OfficeNet", "aa:bb:cc:dd:ee:ff", "11:22:33:44:55:66"
            )
    assert ok is False
    notify.assert_not_called()
    preventer.add_roam.assert_not_called()
