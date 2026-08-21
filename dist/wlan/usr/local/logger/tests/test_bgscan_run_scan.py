import os
import sys
import json
from unittest.mock import MagicMock, patch


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


def _write_runtime_config(tmp_path, monkeypatch, *, freq_filter=True):
    json_path = tmp_path / "wifi_init_conf.json"
    json_path.write_text(
        json.dumps(
            {
                "mlan0": {
                    "bgscan": {
                        "interval": 45,
                        "ssid_filter": True,
                        "freq_filter": freq_filter,
                        "passive": False,
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


def test_native_request_combines_conf_and_json_ssids_in_order(tmp_path, monkeypatch):
    conf = _write_runtime_config(tmp_path, monkeypatch)

    cmd, _, _ = wifi_bgscan.build_scan_request(conf, "wpa_cli")

    ssids = [cmd[i + 1] for i, token in enumerate(cmd[:-1]) if token == "ssid"]
    assert ssids == [name.encode().hex() for name in ("Base", "Office", "JsonOnly")]
