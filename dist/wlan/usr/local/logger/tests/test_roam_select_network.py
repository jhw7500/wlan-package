import sys
import os
import json
import subprocess
import fcntl
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.modules.setdefault("sUTILS", MagicMock())

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wifi_roam
from wifi_roam import select_network_for_ssid

import pytest

wifi_roam.logger = MagicMock()
_real_scan_transition_lock = wifi_roam.scan_transition_lock


@pytest.fixture(autouse=True)
def _private_scan_lock_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(
        wifi_roam, "scan_transition_lock",
        lambda iface: _real_scan_transition_lock(iface, run_dir=str(tmp_path / "wifi")),
    )


@pytest.fixture(autouse=True)
def _isolated_cleanup_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(wifi_roam, "WIFI_RUN_DIR", str(tmp_path / "wifi"), raising=False)
    monkeypatch.setattr(
        wifi_roam, "WIFI_SELECTION_STATE_DIR", str(tmp_path), raising=False
    )
    monkeypatch.setattr(wifi_roam, "_SELECTION_CLEANUP_PENDING", {}, raising=False)

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

_CLEAR_BSSID = "00:00:00:00:00:00"

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


def test_cleanup_marker_is_written_before_bssid_pin_mutation(monkeypatch):
    marker_seen_at_pin = False

    def side_effect(cmd, *a, **k):
        nonlocal marker_seen_at_pin
        if cmd[3] == "list_networks":
            return _Run(0, _LIST_NETWORKS)
        if cmd[3:] == ["bssid", "1", "00:11:22:33:44:55"]:
            marker = Path(wifi_roam.selection_cleanup_marker_path("mlan0"))
            record = json.loads(marker.read_text()) if marker.exists() else {}
            marker_seen_at_pin = (
                record.get("network_id") == "1"
                and record.get("phase") == "before-pin"
            )
            return _Run(0, "OK\n")
        if cmd[3:] in (["bssid", "1", _CLEAR_BSSID], ["enable_network", "all"]):
            return _Run(0, "OK\n")
        if cmd[3] == "select_network":
            return _Run(0, "FAIL\n")
        return _Run(1, "")

    with patch.object(wifi_roam.subprocess, "run", side_effect=side_effect):
        assert select_network_for_ssid(
            "mlan0", "OfficeNet", "00:11:22:33:44:55"
        ) is False

    assert marker_seen_at_pin is True


def test_cleanup_marker_write_failure_prevents_any_selection_mutation(monkeypatch):
    calls = []

    def side_effect(cmd, *a, **k):
        calls.append(cmd[3:])
        if cmd[3] == "list_networks":
            return _Run(0, _LIST_NETWORKS)
        return _Run(0, "OK\n")

    def fail_directory_sync(_path):
        raise OSError("injected directory fsync failure")

    monkeypatch.setattr(wifi_roam, "_sync_parent_directory", fail_directory_sync)
    with patch.object(wifi_roam.subprocess, "run", side_effect=side_effect):
        assert select_network_for_ssid(
            "mlan0", "OfficeNet", "00:11:22:33:44:55"
        ) is False

    assert calls == [["list_networks"]]
    assert wifi_roam._SELECTION_CLEANUP_PENDING == {"mlan0": "1"}


def test_sigkill_after_pin_leaves_wal_for_restart_recovery(tmp_path, monkeypatch):
    logger_dir = Path(wifi_roam.__file__).resolve().parent
    state_dir = tmp_path / "selection-state"
    run_dir = tmp_path / "wifi"
    child = f"""
import os, sys, types
sys.modules['sUTILS'] = types.SimpleNamespace(Logger=object, _EXTRA_=lambda: {{}})
sys.path.insert(0, {str(logger_dir)!r})
import wifi_roam
wifi_roam.logger = types.SimpleNamespace(message=lambda *a, **k: None)
class R:
    def __init__(self, rc=0, out=''):
        self.returncode=rc; self.stdout=out; self.stderr=''
def run(cmd, *a, **k):
    if cmd[3] == 'list_networks':
        return R(0, {_LIST_NETWORKS!r})
    if cmd[3:] == ['bssid', '1', '00:11:22:33:44:55']:
        os._exit(91)
    return R(0, 'OK\\n')
wifi_roam.subprocess.run = run
wifi_roam.select_network_for_ssid('mlan0', 'OfficeNet', '00:11:22:33:44:55')
"""
    env = os.environ | {
        "WIFI_RUN_DIR": str(run_dir),
        "WIFI_SELECTION_STATE_DIR": str(state_dir),
    }
    crashed = subprocess.run([sys.executable, "-c", child], env=env, timeout=10)
    assert crashed.returncode == 91

    marker = state_dir / ".mlan0.selection-cleanup-pending"
    record = json.loads(marker.read_text())
    assert record == {"version": 1, "network_id": "1", "phase": "before-pin"}

    monkeypatch.setattr(wifi_roam, "WIFI_SELECTION_STATE_DIR", str(state_dir))
    recovery_calls = []

    def recover(cmd, *a, **k):
        recovery_calls.append(cmd[3:])
        return _Run(0, "OK\n")

    with patch.object(wifi_roam.subprocess, "run", side_effect=recover):
        assert wifi_roam.retry_pending_selection_cleanup("mlan0") is True
    assert recovery_calls == [["bssid", "1", _CLEAR_BSSID], ["enable_network", "all"]]
    assert not marker.exists()


def test_startup_consumes_surviving_wal_before_missing_policy_refusal(tmp_path):
    """Deleted /run/wifi must not strand a surviving selection WAL on restart."""
    logger_dir = Path(wifi_roam.__file__).resolve().parent
    script = Path(wifi_roam.__file__).resolve()
    state_dir = tmp_path / "selection-state"
    state_dir.mkdir()
    marker = state_dir / ".mlan0.selection-cleanup-pending"
    marker.write_text(
        json.dumps({"version": 1, "network_id": "1", "phase": "before-pin"})
        + "\n"
    )
    missing_run_dir = tmp_path / "deleted-wifi-run"
    call_log = tmp_path / "wpa.calls"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_wpa = fake_bin / "wpa_cli"
    fake_wpa.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$WPA_CALL_LOG\"\n"
        "printf 'OK\\n'\n"
    )
    fake_wpa.chmod(0o755)

    child = f"""
import contextlib, runpy, sys, types
@contextlib.contextmanager
def transition_lock(*args, **kwargs):
    yield True
class StubLogger:
    def __init__(self, *a, **k): pass
    def message(self, *a, **k): pass
sys.modules['sUTILS'] = types.SimpleNamespace(Logger=StubLogger, _EXTRA_=lambda: {{}})
sys.modules['roam_state'] = types.SimpleNamespace(
    clear_own_lease=lambda *a, **k: True,
    clear_stale_lease=lambda *a, **k: False,
    lease_active=lambda *a, **k: False,
    process_start_time=lambda *a, **k: '1',
    roam_state_paths=lambda iface: ({str(tmp_path / 'condition')!r}, {str(tmp_path / 'scan-time')!r}),
    scan_transition_lock=transition_lock,
    write_flag=lambda *a, **k: None,
)
sys.path.insert(0, {str(logger_dir)!r})
sys.argv = [{str(script)!r}, 'mlan0']
runpy.run_path({str(script)!r}, run_name='__main__')
"""
    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "WPA_CALL_LOG": str(call_log),
        "WIFI_RUN_DIR": str(missing_run_dir),
        "WIFI_SELECTION_STATE_DIR": str(state_dir),
    }
    result = subprocess.run(
        [sys.executable, "-c", child], env=env, text=True, capture_output=True, timeout=10
    )

    # Cleanup-only preflight succeeds, then owner/topology still fails closed on the
    # deliberately missing boot snapshot.
    assert result.returncode == 2, result.stderr
    assert call_log.read_text().splitlines() == [
        "-i mlan0 bssid 1 00:00:00:00:00:00",
        "-i mlan0 enable_network all",
    ]
    assert not marker.exists()


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


def test_select_network_status_nonzero_rc_cannot_prove_target_completion(monkeypatch):
    """Target-looking stdout must not override a failed status process result."""
    calls = []

    def side_effect(cmd, *a, **k):
        calls.append(cmd[3:])
        if cmd[3] == "list_networks":
            return _Run(0, _LIST_NETWORKS)
        if cmd[3] in ("bssid", "select_network", "enable_network"):
            return _Run(0, "OK\n")
        if cmd[3] == "status":
            return _Run(
                9,
                "bssid=00:11:22:33:44:55\n"
                "ssid=OfficeNet\nid=1\nwpa_state=COMPLETED\n",
            )
        return _Run(1, "")

    with patch.object(wifi_roam.subprocess, "run", side_effect=side_effect):
        with patch.object(wifi_roam.time, "sleep", MagicMock()):
            ok = select_network_for_ssid(
                "mlan0", "OfficeNet", "00:11:22:33:44:55"
            )

    assert ok is False
    assert sum(call == ["status"] for call in calls) == 6
    assert ["bssid", "1", _CLEAR_BSSID] in calls
    assert ["enable_network", "all"] in calls


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
        ["bssid", "1", _CLEAR_BSSID],
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
    assert sum(c[3:] == ["bssid", "1", _CLEAR_BSSID] for c in calls) == 1
    assert sum(c[3:] == ["enable_network", "all"] for c in calls) == 1


def test_cross_ssid_pin_rejection_stops_before_select_and_cleans_ambiguous_pin(monkeypatch):
    calls = []

    def side_effect(cmd, *a, **k):
        calls.append(cmd)
        if cmd[3] == "list_networks":
            return _Run(0, _LIST_NETWORKS)
        if cmd[3:] == ["bssid", "1", _TARGET_BSSID]:
            return _Run(0, "FAIL\n")
        if cmd[3:] == ["bssid", "1", _CLEAR_BSSID]:
            return _Run(0, "OK\n")
        if cmd[3:] == ["enable_network", "all"]:
            return _Run(0, "OK\n")
        return _Run(1, "")

    with patch.object(wifi_roam.subprocess, "run", side_effect=side_effect):
        ok = select_network_for_ssid("mlan0", "OfficeNet", _TARGET_BSSID)

    assert ok is False
    assert not any(c[3] == "select_network" for c in calls)
    assert sum(c[3:] == ["bssid", "1", _CLEAR_BSSID] for c in calls) == 1
    assert sum(c[3:] == ["enable_network", "all"] for c in calls) == 1


@pytest.mark.parametrize("failure", ["timeout", "exception"])
def test_cross_ssid_pin_ambiguous_failure_always_clears_and_restores(monkeypatch, failure):
    calls = []

    def side_effect(cmd, *a, **k):
        calls.append(cmd)
        if cmd[3] == "list_networks":
            return _Run(0, _LIST_NETWORKS)
        if cmd[3:] == ["bssid", "1", _TARGET_BSSID]:
            if failure == "timeout":
                raise wifi_roam.subprocess.TimeoutExpired(cmd, 10)
            raise RuntimeError("pin transport failed after delivery")
        if cmd[3:] == ["bssid", "1", _CLEAR_BSSID]:
            return _Run(0, "OK\n")
        if cmd[3:] == ["enable_network", "all"]:
            return _Run(0, "OK\n")
        return _Run(1, "")

    with patch.object(wifi_roam.subprocess, "run", side_effect=side_effect):
        with patch.object(wifi_roam.time, "sleep", MagicMock()):
            ok = select_network_for_ssid("mlan0", "OfficeNet", _TARGET_BSSID)

    assert ok is False
    assert not any(c[3] == "select_network" for c in calls)
    assert sum(c[3:] == ["bssid", "1", _CLEAR_BSSID] for c in calls) == 1
    assert sum(c[3:] == ["enable_network", "all"] for c in calls) == 1


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
    assert sum(c[3:] == ["bssid", "1", _CLEAR_BSSID] for c in calls) == 1
    assert sum(c[3:] == ["enable_network", "all"] for c in calls) == 1


def test_cross_ssid_cleanup_transient_failure_is_retried_before_success(monkeypatch):
    calls = []
    clear_count = 0
    enable_count = 0

    def side_effect(cmd, *a, **k):
        nonlocal clear_count, enable_count
        calls.append(cmd)
        sub = cmd[3]
        if sub == "list_networks":
            return _Run(0, _LIST_NETWORKS)
        if cmd[3:] == ["bssid", "1", _TARGET_BSSID]:
            return _Run(0, "OK\n")
        if cmd[3:] == ["bssid", "1", _CLEAR_BSSID]:
            clear_count += 1
            return _Run(0, "FAIL\n" if clear_count == 1 else "OK\n")
        if sub == "select_network":
            return _Run(0, "OK\n")
        if sub == "status":
            return _Run(
                0,
                f"bssid={_TARGET_BSSID}\nssid=OfficeNet\nid=1\nwpa_state=COMPLETED\n",
            )
        if sub == "enable_network":
            enable_count += 1
            return _Run(0, "FAIL\n" if enable_count == 1 else "OK\n")
        return _Run(1, "")

    with patch.object(wifi_roam.subprocess, "run", side_effect=side_effect):
        with patch.object(wifi_roam.time, "sleep", MagicMock()):
            ok = select_network_for_ssid("mlan0", "OfficeNet", _TARGET_BSSID)

    assert ok is True
    assert clear_count == 2
    assert enable_count == 2


def test_cross_ssid_cleanup_persistent_failure_never_reports_success(monkeypatch):
    calls = []

    def side_effect(cmd, *a, **k):
        calls.append(cmd)
        sub = cmd[3]
        if sub == "list_networks":
            return _Run(0, _LIST_NETWORKS)
        if cmd[3:] == ["bssid", "1", _TARGET_BSSID]:
            return _Run(0, "OK\n")
        if sub == "select_network":
            return _Run(0, "OK\n")
        if sub == "status":
            return _Run(
                0,
                f"bssid={_TARGET_BSSID}\nssid=OfficeNet\nid=1\nwpa_state=COMPLETED\n",
            )
        if cmd[3:] == ["bssid", "1", _CLEAR_BSSID] or sub == "enable_network":
            return _Run(0, "FAIL\n")
        if sub == "reconfigure":
            return _Run(0, "FAIL\n")
        return _Run(1, "")

    with patch.object(wifi_roam.subprocess, "run", side_effect=side_effect):
        with patch.object(wifi_roam.time, "sleep", MagicMock()):
            ok = select_network_for_ssid("mlan0", "OfficeNet", _TARGET_BSSID)

    assert ok is False
    assert any(c[3] == "reconfigure" for c in calls)
    marker = Path(wifi_roam.selection_cleanup_marker_path("mlan0"))
    assert json.loads(marker.read_text()) == {
        "version": 1,
        "network_id": "1",
        "phase": "before-pin",
    }
    assert any(
        "cleanup remains unresolved" in str(call)
        for call in wifi_roam.logger.message.call_args_list
    )


def test_pending_selection_cleanup_is_retried_and_marker_cleared(monkeypatch):
    marker = Path(wifi_roam.selection_cleanup_marker_path("mlan0"))
    marker.write_text(
        json.dumps({"version": 1, "network_id": "1", "phase": "before-pin"})
        + "\n"
    )
    calls = []

    def side_effect(cmd, *a, **k):
        calls.append(cmd[3:])
        if cmd[3:] in (["bssid", "1", _CLEAR_BSSID], ["enable_network", "all"]):
            return _Run(0, "OK\n")
        return _Run(1, "")

    with patch.object(wifi_roam.subprocess, "run", side_effect=side_effect):
        with patch.object(wifi_roam.time, "sleep", MagicMock()):
            assert wifi_roam.retry_pending_selection_cleanup("mlan0") is True

    assert calls == [["bssid", "1", _CLEAR_BSSID], ["enable_network", "all"]]
    assert not marker.exists()


def test_pending_selection_cleanup_defers_under_real_scan_transition_lock(
    tmp_path, monkeypatch
):
    """Recovery must not mutate supplicant while another live operation owns the lock."""
    marker = Path(wifi_roam.selection_cleanup_marker_path("mlan0"))
    marker.write_text(
        json.dumps({"version": 1, "network_id": "1", "phase": "before-pin"})
        + "\n"
    )
    calls = []

    def record_mutation(cmd, *args, **kwargs):
        calls.append(cmd)
        return _Run(0, "OK\n")

    lock_dir = tmp_path / "wifi"
    lock_dir.mkdir(exist_ok=True)
    lock_path = lock_dir / "mlan0.scan-transition.lock"
    with lock_path.open("a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        with patch.object(wifi_roam.subprocess, "run", side_effect=record_mutation):
            result = wifi_roam.retry_pending_selection_cleanup("mlan0")

    assert result is wifi_roam.SCAN_TRANSITION_BUSY
    assert calls == []
    assert marker.exists()


def test_main_loop_pending_cleanup_contention_holds_real_lock_and_mutates_nothing(
    tmp_path, monkeypatch
):
    marker = Path(wifi_roam.selection_cleanup_marker_path("mlan0"))
    marker.write_text(
        json.dumps({"version": 1, "network_id": "1", "phase": "before-pin"})
        + "\n"
    )
    monkeypatch.setattr(wifi_roam, "ENABLE_PREDICTIVE_ROAM", False)
    monkeypatch.setattr(wifi_roam, "ENABLE_PING_PONG_PREVENTION", False)
    monkeypatch.setattr(wifi_roam, "GENERATE_NETWORK_BLOCKS", False)
    monkeypatch.setattr(wifi_roam, "CHECK_INTERVAL", 7)
    monkeypatch.setattr(wifi_roam, "_RELOAD_STATE", {"pending": False})
    later = MagicMock(side_effect=AssertionError("busy recovery did not defer"))
    monkeypatch.setattr(wifi_roam, "reload_supplicant_conf_if_changed", later)
    control_calls = []

    def record_control(cmd, *args, **kwargs):
        control_calls.append(cmd)
        return _Run(0, "OK\n")

    class StopCycle(Exception):
        pass

    sleeps = []
    def stop_cycle(seconds):
        sleeps.append(seconds)
        raise StopCycle

    monkeypatch.setattr(wifi_roam, "interruptible_sleep", stop_cycle)
    lock_path = tmp_path / "wifi" / "mlan0.scan-transition.lock"
    lock_path.parent.mkdir(exist_ok=True)
    with lock_path.open("a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        with patch.object(wifi_roam.subprocess, "run", side_effect=record_control):
            with pytest.raises(StopCycle):
                wifi_roam.main()

    assert sleeps == [7]
    assert control_calls == []
    assert marker.exists()
    later.assert_not_called()


def test_startup_pending_cleanup_contention_fails_without_control_mutation(tmp_path):
    """Startup contention is explicit and cannot issue even one cleanup command."""
    logger_dir = Path(wifi_roam.__file__).resolve().parent
    script = Path(wifi_roam.__file__).resolve()
    state_dir = tmp_path / "selection-state"
    state_dir.mkdir()
    marker = state_dir / ".mlan0.selection-cleanup-pending"
    marker.write_text(
        json.dumps({"version": 1, "network_id": "1", "phase": "before-pin"})
        + "\n"
    )
    run_dir = tmp_path / "wifi"
    run_dir.mkdir()
    lock_path = run_dir / "mlan0.scan-transition.lock"
    call_log = tmp_path / "wpa.calls"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_wpa = fake_bin / "wpa_cli"
    fake_wpa.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$WPA_CALL_LOG\"\n"
        "printf 'OK\\n'\n"
    )
    fake_wpa.chmod(0o755)

    child = f"""
import contextlib, fcntl, os, runpy, sys, types
@contextlib.contextmanager
def transition_lock(iface):
    path = os.path.join({str(run_dir)!r}, f"{{iface}}.scan-transition.lock")
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    acquired = False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError:
            pass
        yield acquired
    finally:
        if acquired:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
class StubLogger:
    def __init__(self, *a, **k): pass
    def message(self, *a, **k): pass
sys.modules['sUTILS'] = types.SimpleNamespace(Logger=StubLogger, _EXTRA_=lambda: {{}})
sys.modules['roam_state'] = types.SimpleNamespace(
    clear_own_lease=lambda *a, **k: True,
    clear_stale_lease=lambda *a, **k: False,
    lease_active=lambda *a, **k: False,
    process_start_time=lambda *a, **k: '1',
    roam_state_paths=lambda iface: ({str(tmp_path / 'condition')!r}, {str(tmp_path / 'scan-time')!r}),
    scan_transition_lock=transition_lock,
    write_flag=lambda *a, **k: None,
)
sys.path.insert(0, {str(logger_dir)!r})
sys.argv = [{str(script)!r}, 'mlan0']
runpy.run_path({str(script)!r}, run_name='__main__')
"""
    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "WPA_CALL_LOG": str(call_log),
        "WIFI_RUN_DIR": str(run_dir),
        "WIFI_SELECTION_STATE_DIR": str(state_dir),
    }
    with lock_path.open("a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        result = subprocess.run(
            [sys.executable, "-c", child],
            env=env,
            text=True,
            capture_output=True,
            timeout=10,
        )

    assert result.returncode == 4, result.stderr
    assert not call_log.exists() or call_log.read_text() == ""
    assert marker.exists()


def test_legacy_numeric_selection_cleanup_marker_is_still_recovered(monkeypatch):
    marker = Path(wifi_roam.selection_cleanup_marker_path("mlan0"))
    marker.write_text("1\n")
    calls = []

    def side_effect(cmd, *a, **k):
        calls.append(cmd[3:])
        if cmd[3:] in (["bssid", "1", _CLEAR_BSSID], ["enable_network", "all"]):
            return _Run(0, "OK\n")
        return _Run(1, "")

    with patch.object(wifi_roam.subprocess, "run", side_effect=side_effect):
        assert wifi_roam.retry_pending_selection_cleanup("mlan0") is True

    assert calls == [["bssid", "1", _CLEAR_BSSID], ["enable_network", "all"]]
    assert not marker.exists()


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


def _wpa_printf_encode(value):
    out = []
    for byte in value.encode("utf-8"):
        if byte == 0x22:
            out.append(r'\"')
        elif byte == 0x5C:
            out.append(r'\\')
        elif 0x20 <= byte <= 0x7E:
            out.append(chr(byte))
        else:
            out.append(f"\\x{byte:02x}")
    return "".join(out)


def test_select_network_decodes_wpa_ctrl_identity_in_list_and_status():
    ssid = '  게스트 \\ " exact  '
    encoded = _wpa_printf_encode(ssid)
    listing = (
        "network id / ssid / bssid / flags\n"
        f"1\t{encoded}\tany\t\n"
    )

    def side_effect(cmd, *args, **kwargs):
        sub = cmd[3]
        if sub == "list_networks":
            return _Run(0, listing)
        if sub in ("bssid", "select_network", "enable_network"):
            return _Run(0, "OK\n")
        if sub == "status":
            return _Run(
                0,
                "bssid=00:11:22:33:44:55\n"
                f"ssid={encoded}\nid=1\nwpa_state=COMPLETED\n",
            )
        return _Run(1, "")

    with patch.object(wifi_roam.subprocess, "run", side_effect=side_effect):
        with patch.object(wifi_roam.time, "sleep", MagicMock()):
            assert select_network_for_ssid(
                "mlan0", ssid, "00:11:22:33:44:55"
            ) is True
