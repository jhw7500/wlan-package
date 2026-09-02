"""passive_roam.roam_to_ap 성공 판정 회귀 테스트 (수동 `wifi roam` 경로).

wpa_cli가 "FAIL"에도 exit 0을 주므로 응답 텍스트 "OK" + wpa_cli status 폴링
(COMPLETED@target, roam_notify.confirm_roam 공용) 확인 시에만 성공(exit 0, notify)
이어야 한다. 그리고 roam은 **같은 SSID 안의 BSS 전환 전용**이라 다른 SSID의 AP는
목록에도 오르지 않고 실행도 거부한다 — 망 전환은 `wifi <iface> connect`의 역할이다.
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


@pytest.mark.parametrize("mode_a", [False, True])
def test_foreign_ssid_selection_is_refused_and_spawns_nothing(monkeypatch, mode_a):
    """roam never switches networks — in either boot topology.

    `wpa_cli roam` only moves inside the selected network block, and switching
    networks costs a reconnect.  A foreign-SSID AP is therefore refused here
    instead of being silently turned into a `wifi connect`.  The boot topology
    (Mode A/B) is irrelevant to this CLI: both must refuse.
    """
    del mode_a  # the manual path no longer reads the boot topology at all
    notify = _setup(monkeypatch)
    with patch.object(passive_roam.subprocess, "run") as run:
        assert roam_to_ap(IFACE, _ap(ssid="Office"), current_ssid="Base") == 1
    run.assert_not_called()
    notify.assert_not_called()


def test_unknown_current_ssid_refuses_to_roam(monkeypatch):
    """Every SSID source exhausted: the target cannot be proven same-SSID.

    Issuing `wpa_cli roam` anyway would send a roam for a BSS that may belong to
    another network just because the operator picked its list position.
    """
    notify = _setup(monkeypatch)
    with patch.object(passive_roam.subprocess, "run") as run:
        assert roam_to_ap(IFACE, _ap(ssid="Office"), current_ssid="") == 1
    run.assert_not_called()
    notify.assert_not_called()


# --- 현재 SSID 소스 계단식: wpa_cli status(권위) → wpa conf(폴백) ---

_STATUS_OK = "bssid=aa:bb:cc:dd:ee:ff\nssid=Live\nwpa_state=COMPLETED\n"


def _conf(tmp_path: Path, *blocks: str) -> str:
    body = "\n".join("network={\n" + b + "\n}" for b in blocks)
    path = tmp_path / "wpa_supplicant-mlan0.conf"
    path.write_text("ctrl_interface=/var/run/wpa_supplicant\n" + body + "\n")
    return str(path)


def test_ssid_source_prefers_supplicant_over_conf(monkeypatch, tmp_path):
    monkeypatch.setattr(
        passive_roam.subprocess, "run", lambda *_a, **_k: _Run(0, _STATUS_OK)
    )
    conf = _conf(tmp_path, '    ssid="FromConf"')
    assert passive_roam.read_current_ssid(IFACE, conf) == ("Live", "supplicant")


def test_ssid_source_ignores_supplicant_until_association_completes(
    monkeypatch, tmp_path
):
    """ssid= 는 결합 전에도 찍힌다 — 그건 목표이지 현재가 아니다."""
    monkeypatch.setattr(
        passive_roam.subprocess,
        "run",
        lambda *_a, **_k: _Run(0, "ssid=Live\nwpa_state=SCANNING\n"),
    )
    conf = _conf(tmp_path, '    ssid="FromConf"')
    assert passive_roam.read_current_ssid(IFACE, conf) == ("FromConf", "wpa conf")


def test_ssid_source_falls_back_to_conf_when_wpa_cli_is_absent(monkeypatch, tmp_path):
    def _boom(*_a, **_k):
        raise FileNotFoundError("wpa_cli")

    monkeypatch.setattr(passive_roam.subprocess, "run", _boom)
    conf = _conf(tmp_path, '    ssid="FromConf"')
    assert passive_roam.read_current_ssid(IFACE, conf) == ("FromConf", "wpa conf")


def test_ssid_source_uses_the_only_conf_block(monkeypatch, tmp_path):
    monkeypatch.setattr(passive_roam.subprocess, "run", lambda *_a, **_k: _Run(1, ""))
    conf = _conf(tmp_path, '    ssid="Only"\n    key_mgmt=WPA-PSK')
    assert passive_roam.read_current_ssid(IFACE, conf) == ("Only", "wpa conf")


def test_ssid_source_refuses_an_ambiguous_multi_block_conf(monkeypatch, tmp_path):
    """Mode A 다중 블록: 첫 블록이 라이브라는 보장이 없으므로 추측하지 않는다.

    그럴듯한 오답을 돌려주면 후보 필터와 roam_to_ap 의 same-SSID 가드가 **같은
    오답을 공유해** 둘 다 통과하고, 결국 다른 망의 BSS 로 roam 이 나간다.
    """
    monkeypatch.setattr(passive_roam.subprocess, "run", lambda *_a, **_k: _Run(1, ""))
    conf = _conf(tmp_path, '    ssid="First"', '    ssid="Second"')
    assert passive_roam.read_current_ssid(IFACE, conf) == ("", "unknown")


def test_ssid_source_unknown_when_every_source_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(passive_roam.subprocess, "run", lambda *_a, **_k: _Run(1, ""))
    assert passive_roam.read_current_ssid(IFACE, str(tmp_path / "absent.conf")) == (
        "",
        "unknown",
    )


def test_ssid_source_decodes_ctrl_iface_escapes_to_exact_identity(
    monkeypatch, tmp_path
):
    """CTRL_IFACE 는 printf_encode 형식 — 바이트 그대로 복원해야 필터가 맞는다."""
    monkeypatch.setattr(
        passive_roam.subprocess,
        "run",
        lambda *_a, **_k: _Run(0, "ssid=\\xea\\xb2\\x8c\\xec\\x8a\\xa4\\xed\\x8a\\xb8\nwpa_state=COMPLETED\n"),
    )
    ssid, source = passive_roam.read_current_ssid(IFACE, str(tmp_path / "absent.conf"))
    assert (ssid, source) == ("게스트", "supplicant")


def test_candidate_list_keeps_connected_ssid_and_drops_foreign_ones(monkeypatch):
    monkeypatch.setattr(passive_roam, "read_current_bssid", lambda *_: FROM)
    monkeypatch.setattr(
        passive_roam, "read_current_ssid", lambda *_a, **_k: ("Office", "supplicant")
    )
    monkeypatch.setattr(
        passive_roam,
        "parse_last_scan_block",
        lambda *_a, **_k: [
            {"bssid": TARGET, "ch": 36, "ss": -45, "ssid": "Office"},
            {"bssid": "99:88:77:66:55:44", "ch": 40, "ss": -30, "ssid": "Base"},
        ],
    )

    _bssid, current, source, candidates = passive_roam.build_candidate_list()

    assert (current, source) == ("Office", "supplicant")
    # The stronger "Base" AP is dropped: it is a different network.
    assert [candidate["ssid"] for candidate in candidates] == ["Office"]


def _write_cli_fixture(
    tmp_path: Path, *, scan_rows: str, conf_ssid: str = "Base", wpa_status: str = ""
) -> dict:
    """CLI fixture.  The boot snapshot still declares an extra SSID on purpose:
    the contract is that roam ignores it, so a regression that re-reads it is
    visible as a foreign SSID appearing in the list.

    A stub `wpa_cli` is always installed so the cascade is deterministic
    regardless of what the build host happens to have on PATH.
    """
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "mlan0.roam-policy.json").write_text(
        json.dumps(
            {
                "version": 1,
                "iface": "mlan0",
                "roaming_enabled": True,
                "bgscan_enabled": True,
                "generate_network_blocks": False,
                "extra_ssids": ["Office"],
            }
        )
    )
    link = tmp_path / "link.json"
    link.write_text(json.dumps({"link": {"address": FROM}, "info": {"ssid": "Base"}}))
    scan = tmp_path / "ap.log"
    scan.write_text(f"{datetime.now():%Y-%m-%d %H:%M:%S}\n{scan_rows}")

    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "wpa_cli"
    stub.write_text(
        "#!/bin/sh\n"
        'if [ "$3" = "status" ] && [ -n "$STUB_WPA_STATUS" ]; then\n'
        '  printf \'%s\\n\' "$STUB_WPA_STATUS"\n'
        "  exit 0\n"
        "fi\n"
        "exit 1\n"
    )
    stub.chmod(0o755)

    env = os.environ | {
        "WIFI_RUN_DIR": str(run_dir),
        "PASSIVE_ROAM_LINK_JSON": str(link),
        "PASSIVE_ROAM_SCAN_LOG": str(scan),
        "PATH": f"{bindir}:{os.environ.get('PATH', '')}",
        "STUB_WPA_STATUS": wpa_status,
        "PYTHONPATH": str(Path(passive_roam.__file__).resolve().parent),
    }
    if conf_ssid:
        env["PASSIVE_ROAM_WPA_CONF"] = _conf(tmp_path, f'    ssid="{conf_ssid}"')
    else:
        env["PASSIVE_ROAM_WPA_CONF"] = str(tmp_path / "absent.conf")
    return env


def _run_cli(env, *args):
    return subprocess.run(
        [sys.executable, str(Path(passive_roam.__file__).resolve()), *args,
         "--iface", IFACE],
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
    )


_MIXED_SCAN = (
    "00|36|-70|0|aa:bb:cc:dd:ee:ff|x|Base\n"
    "01|44|-55|0|99:88:77:66:55:44|x|Base\n"
    "02|40|-40|0|11:22:33:44:55:66|x|Office\n"
)


def test_cli_lists_only_the_connected_ssid(tmp_path):
    """Positive control: same-SSID BSSIDs are listed, the extra SSID is not."""
    env = _write_cli_fixture(tmp_path, scan_rows=_MIXED_SCAN)
    result = _run_cli(env)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "source: wpa conf" in output
    assert "99:88:77:66:55:44" in output
    assert "Office" not in output


def test_cli_uses_supplicant_ssid_when_available(tmp_path):
    """권위 소스가 살아 있으면 conf 가 무엇이든 그쪽을 따른다."""
    env = _write_cli_fixture(
        tmp_path,
        scan_rows=_MIXED_SCAN,
        conf_ssid="Office",
        wpa_status="ssid=Base\nwpa_state=COMPLETED",
    )
    result = _run_cli(env)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "source: supplicant" in output
    assert "99:88:77:66:55:44" in output
    assert "Office" not in output


def test_cli_never_switches_networks_for_a_stronger_foreign_ssid(tmp_path):
    """A far stronger AP on another SSID must not trigger a reconnect."""
    env = _write_cli_fixture(
        tmp_path,
        scan_rows=(
            "00|36|-70|0|aa:bb:cc:dd:ee:ff|x|Base\n"
            "01|40|-40|0|11:22:33:44:55:66|x|Office\n"
        ),
    )
    result = _run_cli(env, "0")
    output = result.stdout + result.stderr
    assert result.returncode == 1
    assert "Office" not in output
    assert "No other APs available to roam." in output
    assert "Executing:" not in output


def test_cli_shows_the_scan_but_refuses_to_roam_when_ssid_is_unknown(tmp_path):
    """소스가 전부 실패: 진단용 목록은 남기고 실행만 막는다."""
    env = _write_cli_fixture(tmp_path, scan_rows=_MIXED_SCAN, conf_ssid="")
    listing = _run_cli(env)
    assert listing.returncode == 0, listing.stdout + listing.stderr
    assert "source: unknown" in listing.stdout
    assert "Office" in listing.stdout  # 필터 기준이 없으니 전부 보여준다

    roaming = _run_cli(env, "0")
    output = roaming.stdout + roaming.stderr
    assert roaming.returncode == 1
    assert "Not roaming." in output
    assert "Executing:" not in output


def test_cli_refuses_when_mode_a_conf_cannot_identify_the_live_block(tmp_path):
    """Mode A 다중 블록 + supplicant 조회 불가 → 첫 블록으로 추측하지 않는다.

    추측하면 그 SSID 로 필터한 목록이 그럴듯하게 나오고 roam 이 실제로 발행된다.
    """
    env = _write_cli_fixture(tmp_path, scan_rows=_MIXED_SCAN, conf_ssid="")
    env["PASSIVE_ROAM_WPA_CONF"] = _conf(
        tmp_path, '    ssid="Base"', '    ssid="Office"'
    )

    listing = _run_cli(env)
    assert listing.returncode == 0, listing.stdout + listing.stderr
    assert "source: unknown" in listing.stdout

    roaming = _run_cli(env, "0")
    output = roaming.stdout + roaming.stderr
    assert roaming.returncode == 1
    assert "Not roaming." in output
    assert "Executing:" not in output


# --- 실기 ap.log 포맷(정렬 패딩) 회귀 ---
#
# 기존 픽스처는 `00|36|-70|0|<bssid>|x|Base` 처럼 패딩이 없어, 실제 생산자
# (wifi_logger_scan.py)가 쓰는 정렬 포맷의 선행 공백을 한 번도 통과시키지 않았다.
# 그래서 파서가 ssid 컬럼만 strip 하지 않는 결함을 테스트가 놓쳤고, 실기에서는
# ' jhw_wlan_' != 'jhw_wlan_' 로 후보가 전부 탈락해 목록이 조용히 비었다.
# 아래 픽스처는 타깃 ap.log 에서 그대로 옮긴 모양이다.
_REAL_SCAN = (
    "00| 044 | -48 | 015 | aa:bb:cc:dd:ee:ff | I2DM   NAX | Base\n"
    "01| 036 | -50 | 012 | 99:88:77:66:55:44 | I2DM   N   | Base\n"
    "02| 040 | -40 | 015 | 11:22:33:44:55:66 | I2DM   NAX | Office\n"
)


def test_parse_strips_alignment_padding_from_the_ssid_column(tmp_path):
    log = tmp_path / "ap.log"
    log.write_text(f"{datetime.now():%Y-%m-%d %H:%M:%S}\n{_REAL_SCAN}")

    aps = passive_roam.parse_last_scan_block(str(log), max_age_sec=10_000)

    assert [ap["ssid"] for ap in aps] == ["Base", "Base", "Office"], (
        "정렬 패딩이 SSID identity 에 섞이면 supplicant 가 준 SSID 와 절대 같아지지 않는다."
    )


def test_cli_lists_candidates_from_a_real_shaped_scan_log(tmp_path):
    """실기 포맷 그대로의 로그에서 같은 SSID 후보가 실제로 나열되어야 한다."""
    env = _write_cli_fixture(
        tmp_path,
        scan_rows=_REAL_SCAN,
        wpa_status="ssid=Base\nwpa_state=COMPLETED",
    )
    result = _run_cli(env)
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert "source: supplicant" in output
    assert "99:88:77:66:55:44" in output   # 같은 SSID 의 다른 BSSID 가 후보로 보인다
    assert "Office" not in output


def test_cli_explains_why_the_candidate_list_is_empty(tmp_path):
    """스캔에는 AP 가 있는데 현재 SSID 것이 하나도 없으면 이유를 말해야 한다.

    종전에는 메시지 없이 exit 1 이라 운영자가 원인을 알 수 없었다.
    """
    env = _write_cli_fixture(
        tmp_path,
        scan_rows="00| 040 | -40 | 015 | 11:22:33:44:55:66 | I2DM   NAX | Office\n",
        wpa_status="ssid=Base\nwpa_state=COMPLETED",
    )
    result = _run_cli(env)
    output = result.stdout + result.stderr

    assert result.returncode == 1
    assert "none on 'Base'" in output
