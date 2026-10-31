import os
import sys
import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


sys.modules.setdefault("sUTILS", MagicMock())
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import wifi_bgscan


wifi_bgscan.logger = MagicMock()
wifi_bgscan.IFACE = "mlan0"


class _Run:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_wpa_scan_requires_ok_reply_even_when_process_exit_is_zero():
    with patch.object(
        wifi_bgscan.subprocess, "run", return_value=_Run(0, "FAIL-BUSY\n")
    ) as run:
        assert wifi_bgscan.run_scan_command(
            ["wpa_cli", "-i", "mlan0", "scan"], "wpa_cli"
        ) is False
    run.assert_called_once()


def test_wpa_scan_accepts_only_zero_exit_and_ok_first_line():
    with patch.object(
        wifi_bgscan.subprocess, "run", return_value=_Run(0, "OK\n")
    ):
        assert wifi_bgscan.run_scan_command(
            ["wpa_cli", "-i", "mlan0", "scan"], "wpa_cli"
        ) is True

    with patch.object(
        wifi_bgscan.subprocess, "run", return_value=_Run(1, "OK\n", "socket error")
    ):
        assert wifi_bgscan.run_scan_command(
            ["wpa_cli", "-i", "mlan0", "scan"], "wpa_cli"
        ) is False


def test_iw_scan_uses_process_exit_status():
    with patch.object(wifi_bgscan.subprocess, "run", return_value=_Run(0)):
        assert wifi_bgscan.run_scan_command(
            ["iw", "mlan0", "scan", "passive"], "iw"
        ) is True
    with patch.object(
        wifi_bgscan.subprocess, "run", return_value=_Run(1, stderr="busy")
    ):
        assert wifi_bgscan.run_scan_command(
            ["iw", "mlan0", "scan", "passive"], "iw"
        ) is False


def test_timeout_fails_without_invoking_an_alternate_backend():
    run = MagicMock(
        side_effect=wifi_bgscan.subprocess.TimeoutExpired(
            ["wpa_cli", "-i", "mlan0", "scan"], 30
        )
    )
    with patch.object(wifi_bgscan.subprocess, "run", run):
        assert wifi_bgscan.run_scan_command(
            ["wpa_cli", "-i", "mlan0", "scan"], "wpa_cli"
        ) is False
    run.assert_called_once()


def _write_runtime_config(tmp_path, monkeypatch, *, freq_filter=True, passive=False):
    json_path = tmp_path / "wifi_init_conf.json"
    json_path.write_text(
        json.dumps(
            {
                "mlan0": {
                    "bgscan": {
                        "interval": 45,
                        "ssid_filter": True,
                        "freq_filter": freq_filter,
                        "passive": passive,
                        "emit_roam_hint": True,
                    },
                    "roaming": {
                        "enabled": True,
                        "generate_network_blocks": True,
                        "extra_ssids": ["JsonOnly"],
                    },
                }
            }
        )
    )
    monkeypatch.setattr(wifi_bgscan, "WIFI_INIT_CONF_JSON", str(json_path))
    conf = tmp_path / "wpa.conf"
    conf.write_text(
        """\
freq_list=5180 5200
network={
    ssid="Base"
    freq_list=5180 5200
}
network={
    ssid="Office"
    freq_list=5180 5200
}
"""
    )
    return str(conf)


def _write_mode_b_config(tmp_path, monkeypatch, *, ssid_filter=True, extras=None,
                         passive=False, extra_in_roam_scan=None):
    json_path = tmp_path / "wifi_init_conf.json"
    json_path.write_text(json.dumps({
        "mlan0": {
            "bgscan": {"ssid_filter": ssid_filter, "passive": passive},
            "roaming": {
                "generate_network_blocks": False,
                "extra_ssids": ["LiveJsonMustNotWin"],
                **({"extra_ssids_in_roam_scan": extra_in_roam_scan}
                   if extra_in_roam_scan is not None else {}),
            },
        }
    }))
    monkeypatch.setattr(wifi_bgscan, "WIFI_INIT_CONF_JSON", str(json_path))
    conf = tmp_path / "wpa.conf"
    conf.write_text('freq_list=5180 5200\nnetwork={\n    ssid="Base"\n}\n')
    policy = {"generate_network_blocks": False, "extra_ssids": extras or []}
    return str(conf), policy


@pytest.mark.parametrize("backend", ["iw", "wpa_cli"])
@pytest.mark.parametrize("extra_in_roam_scan", [None, False])
def test_mode_b_uses_boot_extras_without_generating_network_blocks(
    tmp_path, monkeypatch, backend, extra_in_roam_scan
):
    conf, policy = _write_mode_b_config(
        tmp_path, monkeypatch, extras=["Office", "Guest"],
        extra_in_roam_scan=extra_in_roam_scan,
    )

    cmd, _, _ = wifi_bgscan.build_scan_request(conf, backend, boot_policy=policy)

    if backend == "iw":
        assert cmd[cmd.index("ssid") + 1:] == ["Base", "Office", "Guest"]
    else:
        assert [cmd[i + 1] for i, part in enumerate(cmd[:-1]) if part == "ssid"] == [
            ssid.encode().hex() for ssid in ("Base", "Office", "Guest")
        ]
    assert Path(conf).read_text().count("network={") == 1


def test_mode_b_boot_extras_survive_missing_live_json(tmp_path, monkeypatch):
    conf, policy = _write_mode_b_config(tmp_path, monkeypatch, extras=["Office"])
    monkeypatch.setattr(
        wifi_bgscan, "WIFI_INIT_CONF_JSON", str(tmp_path / "missing.json")
    )

    cmd, _, _ = wifi_bgscan.build_scan_request(conf, "iw", boot_policy=policy)

    assert cmd[cmd.index("ssid") + 1:] == ["Base", "Office"]


@pytest.mark.parametrize("backend", ["iw", "wpa_cli"])
def test_mode_b_unfiltered_scan_keeps_broad_discovery_and_probes_extras(
    tmp_path, monkeypatch, backend
):
    conf, policy = _write_mode_b_config(
        tmp_path, monkeypatch, ssid_filter=False, extras=["Office"]
    )

    cmd, _, _ = wifi_bgscan.build_scan_request(conf, backend, boot_policy=policy)

    if backend == "iw":
        assert cmd[cmd.index("ssid") + 1:] == ["", "Base", "Office"]
    else:
        assert [cmd[i + 1] for i, token in enumerate(cmd[:-1]) if token == "ssid"] == [
            "",
            "Base".encode().hex(),
            "Office".encode().hex()
        ]


@pytest.mark.parametrize("backend", ["iw", "wpa_cli"])
def test_mode_a_unfiltered_extra_probe_command_is_unchanged(
    tmp_path, monkeypatch, backend
):
    conf, policy = _write_mode_b_config(
        tmp_path, monkeypatch, ssid_filter=False, extras=["Office"]
    )
    policy["generate_network_blocks"] = True

    cmd, _, _ = wifi_bgscan.build_scan_request(conf, backend, boot_policy=policy)

    if backend == "iw":
        assert cmd[cmd.index("ssid") + 1:] == ["", "Office"]
    else:
        assert [cmd[i + 1] for i, token in enumerate(cmd[:-1]) if token == "ssid"] == [
            "Office".encode().hex()
        ]


@pytest.mark.parametrize("backend", ["iw", "wpa_cli"])
def test_mode_b_unfiltered_probe_cap_preserves_wildcard_and_base(
    tmp_path, monkeypatch, backend
):
    conf, policy = _write_mode_b_config(
        tmp_path, monkeypatch, ssid_filter=False,
        extras=[f"Office{i}" for i in range(10)],
    )

    cmd, _, _ = wifi_bgscan.build_scan_request(conf, backend, boot_policy=policy)

    if backend == "iw":
        probes = cmd[cmd.index("ssid") + 1:]
    else:
        probes = [cmd[i + 1] for i, token in enumerate(cmd[:-1]) if token == "ssid"]
        probes = [bytes.fromhex(probe).decode() for probe in probes]
    assert probes[:2] == ["", "Base"]
    assert len(probes) == wifi_bgscan.MAX_SCAN_SSIDS


def test_mode_b_native_passive_keeps_existing_no_probe_contract(
    tmp_path, monkeypatch
):
    conf, policy = _write_mode_b_config(
        tmp_path, monkeypatch, extras=["Office"], passive=True
    )

    cmd, _, _ = wifi_bgscan.build_scan_request(conf, "wpa_cli", boot_policy=policy)

    assert "passive=1" in cmd
    assert "ssid" not in cmd


@pytest.mark.parametrize("backend", ["iw", "wpa_cli"])
@pytest.mark.parametrize("ssid_filter", [True, False])
def test_mode_b_empty_extras_preserve_scan_command(
    tmp_path, monkeypatch, backend, ssid_filter
):
    conf, policy = _write_mode_b_config(
        tmp_path, monkeypatch, ssid_filter=ssid_filter
    )

    cmd, _, _ = wifi_bgscan.build_scan_request(conf, backend, boot_policy=policy)

    if backend == "iw":
        expected = ["iw", "mlan0", "scan", "freq", "5180", "5200"]
        if ssid_filter:
            expected += ["ssid", "Base"]
    else:
        expected = ["wpa_cli", "-i", "mlan0", "scan", "freq=5180,5200"]
        if ssid_filter:
            expected += ["ssid", "Base".encode().hex()]
    assert cmd == expected


def test_build_request_uses_passed_backend_not_reloadable_owner(tmp_path, monkeypatch):
    conf = _write_runtime_config(tmp_path, monkeypatch)

    cmd, interval, emit_hint = wifi_bgscan.build_scan_request(conf, "wpa_cli")

    assert cmd[:4] == ["wpa_cli", "-i", wifi_bgscan.IFACE, "scan"]
    assert interval == 45
    assert emit_hint is False
    assert "TYPE=ONLY" not in cmd


def test_build_request_uses_common_freq_even_when_legacy_filter_is_false(
    tmp_path, monkeypatch
):
    conf = _write_runtime_config(tmp_path, monkeypatch, freq_filter=False)

    cmd, _, _ = wifi_bgscan.build_scan_request(conf, "iw")

    assert cmd[:3] == ["iw", wifi_bgscan.IFACE, "scan"]
    assert cmd[cmd.index("freq") + 1 : cmd.index("ssid")] == ["5180", "5200"]


def test_build_request_passive_json_mode_a_is_forced_to_ordered_active_iw(
    tmp_path, monkeypatch
):
    conf = _write_runtime_config(tmp_path, monkeypatch, passive=True)

    cmd, _, _ = wifi_bgscan.build_scan_request(conf, "iw")

    assert cmd == [
        "iw", "mlan0", "scan", "freq", "5180", "5200",
        "ssid", "Base", "Office", "JsonOnly",
    ]
    assert "passive" not in cmd


def test_native_request_combines_conf_and_json_ssids_in_order(tmp_path, monkeypatch):
    conf = _write_runtime_config(tmp_path, monkeypatch)

    cmd, _, _ = wifi_bgscan.build_scan_request(conf, "wpa_cli")

    ssids = [cmd[i + 1] for i, token in enumerate(cmd[:-1]) if token == "ssid"]
    assert ssids == [name.encode().hex() for name in ("Base", "Office", "JsonOnly")]


@pytest.mark.parametrize("backend", ["iw", "wpa_cli"])
def test_generated_extra_and_boot_snapshot_overlap_is_one_probe(
    tmp_path, monkeypatch, backend
):
    conf = Path(_write_runtime_config(tmp_path, monkeypatch))
    conf.write_text(
        """\
freq_list=5180 5200
network={
    ssid="Base"
}
# >>> wifi_extra_ssid auto-generated (do not edit) >>>
network={
    ssid=4f6666696365
}
# <<< wifi_extra_ssid auto-generated <<<
"""
    )
    boot_policy = {
        "generate_network_blocks": True,
        "extra_ssids": ["Office"],
    }

    cmd, _, _ = wifi_bgscan.build_scan_request(
        str(conf), backend, boot_policy=boot_policy
    )

    if backend == "iw":
        probes = cmd[cmd.index("ssid") + 1 :]
    else:
        probes = [cmd[i + 1] for i, token in enumerate(cmd[:-1]) if token == "ssid"]
        probes = [bytes.fromhex(probe).decode("utf-8") for probe in probes]
    assert probes == ["Base", "Office"]


@pytest.mark.parametrize("extra_ssids", [["Office", "Office"], ["Base"]])
def test_build_request_deduplicates_boot_snapshot_extras(
    tmp_path, monkeypatch, extra_ssids
):
    conf = _write_runtime_config(tmp_path, monkeypatch)
    boot_policy = {
        "generate_network_blocks": True,
        "extra_ssids": extra_ssids,
    }

    cmd, _, _ = wifi_bgscan.build_scan_request(
        conf, "iw", boot_policy=boot_policy
    )

    assert cmd[cmd.index("ssid") + 1 :] == ["Base", "Office"]


def test_periodic_scan_lock_contention_defers_full_interval_without_subprocess(
    tmp_path, monkeypatch
):
    """A busy association transition is a neutral skipped bgscan cycle.

    The scan scheduler must record the attempted time even though no scan was
    issued, so the next loop at +1s does not spin/retry before the configured
    normal interval.  This gives the daemon a deterministic no-wait seam while
    production still uses the real per-interface flock.
    """
    conf = _write_runtime_config(tmp_path, monkeypatch)
    calls = []

    @contextmanager
    def denied_lock(*_args, **_kwargs):
        yield False

    # ``raising=False`` deliberately lets this execute on the pre-Task-10
    # code: it then demonstrates the missing serialization as an iw/wpa scan.
    monkeypatch.setattr(wifi_bgscan, "scan_transition_lock", denied_lock, raising=False)
    monkeypatch.setattr(wifi_bgscan.os.path, "exists", lambda _path: True)
    monkeypatch.setattr(wifi_bgscan, "is_wpa_running", lambda _iface: True)
    monkeypatch.setattr(wifi_bgscan, "is_wpa_connected", lambda _iface: calls.append("connected") or True)
    monkeypatch.setattr(wifi_bgscan, "get_flag", lambda: False)
    monkeypatch.setattr(wifi_bgscan, "build_scan_request", lambda *_a, **_k: (["iw", "mlan0", "scan"], 60, False))
    run = MagicMock(return_value=True)
    monkeypatch.setattr(wifi_bgscan, "run_scan_command", run)

    now = iter((0, 61, 61, 62))
    last_now = 62

    def fake_time():
        nonlocal last_now
        try:
            last_now = next(now)
        except StopIteration:
            pass
        return last_now

    monkeypatch.setattr(wifi_bgscan.time, "time", fake_time)
    sleep_calls = 0

    def fake_sleep(_seconds):
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls == 3:
            raise RuntimeError("stop periodic loop")

    monkeypatch.setattr(wifi_bgscan.time, "sleep", fake_sleep)

    with pytest.raises(RuntimeError, match="stop periodic loop"):
        wifi_bgscan.periodic_scan(conf, "iw", {})

    assert run.call_count == 0, "contention must not issue a scan subprocess"
    assert calls == ["connected"], "the +1s loop must be deferred until interval"
