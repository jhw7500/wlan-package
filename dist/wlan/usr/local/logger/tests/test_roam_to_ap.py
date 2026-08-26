"""passive_roam.roam_to_ap 성공 판정 회귀 테스트 (수동 `wifi roam` 경로).

same-SSID(wpa_cli roam)는 wpa_cli가 "FAIL"에도 exit 0을 주므로 응답 텍스트 "OK" +
wpa_cli status 폴링(COMPLETED@target, roam_notify.confirm_roam 공용) 확인 시에만
성공(exit 0, notify)이어야 한다. cross-SSID(wifi connect 래퍼) 경로는 종전 계약 유지.
"""
import sys
import os
import json
import subprocess
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
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

    @contextmanager
    def acquired_lock(_iface):
        yield True

    monkeypatch.setattr(passive_roam, "notify_roam", notify)
    monkeypatch.setattr(passive_roam, "read_current_bssid", lambda *_a, **_k: FROM)
    monkeypatch.setattr(passive_roam, "scan_transition_lock", acquired_lock)
    monkeypatch.setattr(passive_roam, "abort_scan_quiesce", lambda _iface: True, raising=False)
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


def test_same_ssid_roam_fails_closed_when_transition_lock_is_busy(monkeypatch):
    """수동 roam도 bgscan/connect와 같은 transition namespace를 사용한다."""
    notify = _setup(monkeypatch)

    @contextmanager
    def denied_lock(_iface):
        yield False

    monkeypatch.setattr(passive_roam, "scan_transition_lock", denied_lock, raising=False)
    with patch.object(passive_roam.subprocess, "run") as run:
        assert roam_to_ap(IFACE, _ap(), current_ssid="Net") == 1
    run.assert_not_called()
    notify.assert_not_called()


def test_same_ssid_roam_fails_closed_when_native_scan_cannot_quiesce(monkeypatch):
    """FD7 획득 전에 시작된 wpa scan이 끝나지 않으면 roam을 발행하지 않는다."""
    notify = _setup(monkeypatch)
    monkeypatch.setattr(passive_roam, "abort_scan_quiesce", lambda _iface: False)
    with patch.object(passive_roam.subprocess, "run") as run:
        assert roam_to_ap(IFACE, _ap(), current_ssid="Net") == 1
    run.assert_not_called()
    notify.assert_not_called()


def test_same_ssid_confirm_waits_for_target(monkeypatch):
    notify = _setup(monkeypatch)
    with patch.object(passive_roam.subprocess, "run", return_value=_Run(0, "OK\n")), \
         patch.object(roam_notify, "get_associated_bssid", side_effect=[FROM, TARGET]) as gab, \
         patch.object(roam_notify.time, "monotonic", return_value=0.0):
        assert roam_to_ap(IFACE, _ap(), current_ssid="Net") == 0
    assert gab.call_count == 2
    notify.assert_called_once()


def test_cross_ssid_path_unchanged(monkeypatch):
    """Mode B cross-SSID keeps the supported wifi-connect contract."""
    notify = _setup(monkeypatch)
    with patch.object(passive_roam.subprocess, "run", return_value=_Run(0, "")) as run, \
         patch.object(passive_roam, "get_associated_bssid", return_value=TARGET):
        # current_ssid != ap.ssid → cross-SSID 분기
        assert roam_to_ap(
            IFACE, _ap(ssid="Net"), current_ssid="Other", mode_a=False
        ) == 0
    # 래퍼 명령이 호출됐는지(wifi connect)
    assert run.call_args[0][0][:3] == ["/usr/local/bin/wifi", IFACE, "connect"]
    notify.assert_called_once_with(IFACE, FROM, TARGET)


def test_mode_a_cross_ssid_is_rejected_before_any_child(monkeypatch):
    """Mode A manual cross-SSID selection is unsupported, not a doomed connect."""
    notify = _setup(monkeypatch)
    with patch.object(passive_roam.subprocess, "run") as run:
        assert (
            roam_to_ap(
                IFACE,
                _ap(ssid="Office"),
                current_ssid="Base",
                mode_a=True,
            )
            == 1
        )
    run.assert_not_called()
    notify.assert_not_called()


def test_manual_policy_reads_immutable_snapshot_and_preserves_ssid_bytes(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "mlan0.roam-policy.json").write_text(
        json.dumps(
            {
                "version": 1,
                "iface": "mlan0",
                "roaming_enabled": True,
                "bgscan_enabled": True,
                "generate_network_blocks": True,
                "extra_ssids": ['  office \\" exact  '],
            }
        )
    )
    policy = passive_roam.load_manual_roam_policy("mlan0", run_dir=str(run_dir))
    assert policy["generate_network_blocks"] is True
    assert policy["extra_ssids"] == ['  office \\" exact  ']


@pytest.mark.parametrize("mode_a", [False, True])
def test_current_manual_candidate_remains_available_for_same_ssid_roam(
    monkeypatch, mode_a
):
    """After a legitimate cross-SSID transition, current may equal a boot extra.

    That runtime state is not an invalid boot base/extra declaration.  Both
    modes must still expose same-SSID BSSID candidates; Mode A merely hides
    the *other* cross-SSID targets.
    """
    monkeypatch.setattr(passive_roam, "read_current_bssid", lambda *_: FROM)
    monkeypatch.setattr(passive_roam, "read_current_ssid", lambda *_: "Office")
    monkeypatch.setattr(
        passive_roam,
        "parse_last_scan_block",
        lambda *_a, **_k: [
            {"bssid": TARGET, "ch": 36, "ss": -45, "ssid": "Office"}
        ],
    )
    policy = {
        "generate_network_blocks": mode_a,
        "extra_ssids": ["Office"],
    }

    _bssid, current, candidates, extras = passive_roam.build_candidate_list(policy)

    assert current == "Office"
    assert [candidate["ssid"] for candidate in candidates] == ["Office"]
    assert extras == ([] if mode_a else ["Office"])


def _write_cli_fixture(tmp_path: Path, *, mode_a: bool) -> tuple[dict, Path]:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "mlan0.roam-policy.json").write_text(
        json.dumps(
            {
                "version": 1,
                "iface": "mlan0",
                "roaming_enabled": True,
                "bgscan_enabled": True,
                "generate_network_blocks": mode_a,
                "extra_ssids": ["Office"],
            }
        )
    )
    link = tmp_path / "link.json"
    link.write_text(
        json.dumps(
            {
                "link": {"address": FROM},
                "info": {"ssid": "Base"},
            }
        )
    )
    scan = tmp_path / "ap.log"
    scan.write_text(
        f"{datetime.now():%Y-%m-%d %H:%M:%S}\n"
        "00|36|-70|0|aa:bb:cc:dd:ee:ff|x|Base\n"
        "01|40|-40|0|11:22:33:44:55:66|x|Office\n"
    )
    command_log = tmp_path / "wifi.calls"
    wifi = tmp_path / "wifi"
    wifi.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$PASSIVE_WIFI_CALL_LOG\"\n"
        "exit 0\n"
    )
    wifi.chmod(0o755)
    env = os.environ | {
        "WIFI_RUN_DIR": str(run_dir),
        "PASSIVE_ROAM_LINK_JSON": str(link),
        "PASSIVE_ROAM_SCAN_LOG": str(scan),
        "PASSIVE_ROAM_WIFI_COMMAND": str(wifi),
        "PASSIVE_WIFI_CALL_LOG": str(command_log),
        "PYTHONPATH": str(Path(passive_roam.__file__).resolve().parent),
    }
    return env, command_log


def test_cli_mode_a_hides_cross_ssid_and_invokes_no_wifi_child(tmp_path):
    """Real CLI integration: a stronger extra SSID is neither listed nor executed."""
    env, command_log = _write_cli_fixture(tmp_path, mode_a=True)
    result = subprocess.run(
        [sys.executable, str(Path(passive_roam.__file__).resolve()), "0", "--iface", IFACE],
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 1
    assert "manual cross-SSID selection is unsupported in Mode A" in output
    assert not command_log.exists() or command_log.read_text() == ""
    assert "Executing:" not in output


def test_cli_mode_b_executes_real_wifi_child_for_cross_ssid(tmp_path):
    """Real CLI integration: Mode B still supports manual cross-SSID connect."""
    env, command_log = _write_cli_fixture(tmp_path, mode_a=False)
    result = subprocess.run(
        [sys.executable, str(Path(passive_roam.__file__).resolve()), "0", "--iface", IFACE],
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert command_log.read_text().splitlines() == ["mlan0 connect Office"]
