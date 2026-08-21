"""Roam owner와 background-scan 서비스 정책의 부팅 계약."""

import json
import os
import subprocess
from pathlib import Path

import pytest


WLAN_ROOT = Path(__file__).resolve().parents[4]
APPLY = WLAN_ROOT / "usr/local/scripts/wifi_apply_enabled.sh"


def _write_exe(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(0o755)


def _config(roaming_enabled: bool) -> dict:
    iface = {
        "enabled": True,
        "net_rx": 0,
        "STANDARD": "ac",
        "wpa_supplicant": {"enabled": False},
        "logger": {"enabled": False},
        "checker": {"enabled": False},
        "bgscan": {"enabled": True},
        "roaming": {"enabled": roaming_enabled},
        # Legacy conflict input: policy must ignore true and force the unit off.
        "periodic_roam": {"enabled": True},
        "arping": {"enabled": False},
        "on_connect": {"enabled": False},
        "mcs_tier": {"enabled": False, "he": ""},
    }
    disabled_iface = json.loads(json.dumps(iface))
    disabled_iface["enabled"] = False
    disabled_iface["periodic_roam"]["enabled"] = False
    return {
        "global": {
            "MOD_PARA": "does-not-exist.conf",
            "ping_monitor": {"enabled": False},
            "fw_watch": {"enabled": False},
        },
        "logger": {"enabled": False},
        "eth0": {"logger": {"enabled": False}},
        "wbridge": {
            "enabled": False,
            "bridge_iface": "mlan0",
            "thermal": {"enabled": False},
        },
        "snmp": {"enabled": False, "trap": {"enabled": False}},
        "opc": {"enabled": False},
        "mlan0": iface,
        "mlan1": disabled_iface,
    }


@pytest.mark.parametrize("roaming_enabled", [True, False])
def test_roam_owner_drives_services_and_periodic_owner_is_forced_off(
    tmp_path: Path, roaming_enabled: bool
) -> None:
    config = tmp_path / "wifi_init_conf.json"
    config.write_text(json.dumps(_config(roaming_enabled)))
    calls = tmp_path / "systemctl.calls"
    logs = tmp_path / "logger.calls"
    calls.write_text("")
    logs.write_text("")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_exe(
        fake_bin / "systemctl",
        """#!/bin/sh
printf '%s\n' "$*" >> "$FAKE_SYSTEMCTL_CALLS"
if [ "$1" = "is-enabled" ]; then
  case ",${FAKE_ENABLED_UNITS:-}," in
    *",$3,"*) exit 0 ;;
    *) exit 1 ;;
  esac
fi
exit 0
""",
    )
    _write_exe(
        fake_bin / "logger",
        """#!/bin/sh
printf '%s\n' "$*" >> "$FAKE_LOGGER_CALLS"
exit 0
""",
    )

    initially_enabled = ["wifi_periodic_roam@mlan0.service"]
    if not roaming_enabled:
        initially_enabled.append("wifi_roam@mlan0.service")
    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "WIFI_INIT_CONF_JSON": str(config),
        "WIFI_APPLY_STRICT": "1",
        "FAKE_SYSTEMCTL_CALLS": str(calls),
        "FAKE_LOGGER_CALLS": str(logs),
        "FAKE_ENABLED_UNITS": ",".join(initially_enabled),
    }
    result = subprocess.run(
        ["bash", str(APPLY)],
        env=env,
        text=True,
        capture_output=True,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr
    recorded = calls.read_text().splitlines()
    assert "enable wifi_bgscan@mlan0.service" in recorded
    if roaming_enabled:
        assert "enable wifi_roam@mlan0.service" in recorded
        assert "disable wifi_roam@mlan0.service" not in recorded
    else:
        assert "disable wifi_roam@mlan0.service" in recorded
        assert "enable wifi_roam@mlan0.service" not in recorded

    assert "disable wifi_periodic_roam@mlan0.service" in recorded
    warning = logs.read_text() + result.stderr
    assert "periodic_roam" in warning
    assert "deprecated" in warning.lower()

