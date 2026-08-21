"""Roam owner와 background-scan 서비스 정책의 부팅 계약."""

import json
import os
import subprocess
from pathlib import Path

import pytest


WLAN_ROOT = Path(__file__).resolve().parents[4]
APPLY = WLAN_ROOT / "usr/local/scripts/wifi_apply_enabled.sh"
SYSTEMD = WLAN_ROOT / "etc/systemd/system"


def _write_exe(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(0o755)


def _config(
    roaming_enabled: bool,
    *,
    generate_network_blocks: bool = False,
    extra_ssids: list[str] | None = None,
    bgscan_enabled: bool = True,
) -> dict:
    iface = {
        "enabled": True,
        "net_rx": 0,
        "STANDARD": "ac",
        "wpa_supplicant": {"enabled": False},
        "logger": {"enabled": False},
        "checker": {"enabled": False},
        "bgscan": {"enabled": bgscan_enabled},
        "roaming": {
            "enabled": roaming_enabled,
            "generate_network_blocks": generate_network_blocks,
            "extra_ssids": extra_ssids or [],
        },
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
  if grep -Fxq "disable $3" "$FAKE_SYSTEMCTL_CALLS"; then exit 1; fi
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
        "WIFI_RUN_DIR": str(tmp_path / "run"),
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
    assert "stop wifi_periodic_roam@mlan0.service" in recorded
    if roaming_enabled:
        assert "stop wifi_roam@mlan0.service" not in recorded
    else:
        assert "stop wifi_roam@mlan0.service" in recorded

    snapshot = json.loads(
        (tmp_path / "run" / "mlan0.roam-policy.json").read_text()
    )
    assert snapshot == {
        "version": 1,
        "iface": "mlan0",
        "roaming_enabled": roaming_enabled,
        "bgscan_enabled": True,
        "generate_network_blocks": False,
        "extra_ssids": [],
    }
    warning = logs.read_text() + result.stderr
    assert "periodic_roam" in warning
    assert "deprecated" in warning.lower()


def test_boot_policy_snapshot_is_immutable_until_reboot(tmp_path: Path) -> None:
    """같은 /run 안에서는 JSON을 바꾸고 apply를 재실행해도 owner/topology가 바뀌지 않는다."""
    config = tmp_path / "wifi_init_conf.json"
    config.write_text(
        json.dumps(
            _config(
                True,
                generate_network_blocks=True,
                extra_ssids=["Office", "Guest"],
            )
        )
    )
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
if [ "$1" = "is-enabled" ]; then exit 1; fi
exit 0
""",
    )
    _write_exe(fake_bin / "logger", "#!/bin/sh\nexit 0\n")
    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "WIFI_INIT_CONF_JSON": str(config),
        "WIFI_RUN_DIR": str(tmp_path / "run"),
        "FAKE_SYSTEMCTL_CALLS": str(calls),
        "FAKE_LOGGER_CALLS": str(logs),
    }

    first = subprocess.run(
        ["bash", str(APPLY)], env=env, text=True, capture_output=True, timeout=5
    )
    assert first.returncode == 0, first.stderr
    snap_path = tmp_path / "run" / "mlan0.roam-policy.json"
    first_snapshot = snap_path.read_text()

    changed = _config(False, bgscan_enabled=False)
    config.write_text(json.dumps(changed))
    calls.write_text("")
    second = subprocess.run(
        ["bash", str(APPLY)], env=env, text=True, capture_output=True, timeout=5
    )
    assert second.returncode == 0, second.stderr
    assert snap_path.read_text() == first_snapshot
    snapshot = json.loads(first_snapshot)
    assert snapshot["roaming_enabled"] is True
    assert snapshot["bgscan_enabled"] is True
    assert snapshot["generate_network_blocks"] is True
    assert snapshot["extra_ssids"] == ["Office", "Guest"]


def test_periodic_owner_is_stopped_even_when_already_disabled(tmp_path: Path) -> None:
    config = tmp_path / "wifi_init_conf.json"
    config.write_text(json.dumps(_config(False)))
    calls = tmp_path / "systemctl.calls"
    calls.write_text("")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_exe(
        fake_bin / "systemctl",
        """#!/bin/sh
printf '%s\n' "$*" >> "$FAKE_SYSTEMCTL_CALLS"
if [ "$1" = "is-enabled" ]; then exit 1; fi
exit 0
""",
    )
    _write_exe(fake_bin / "logger", "#!/bin/sh\nexit 0\n")
    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "WIFI_INIT_CONF_JSON": str(config),
        "WIFI_RUN_DIR": str(tmp_path / "run"),
        "FAKE_SYSTEMCTL_CALLS": str(calls),
    }
    result = subprocess.run(
        ["bash", str(APPLY)], env=env, text=True, capture_output=True, timeout=5
    )
    assert result.returncode == 0, result.stderr
    assert "stop wifi_periodic_roam@mlan0.service" in calls.read_text().splitlines()


def test_disallowed_owner_stop_failure_is_fatal_even_without_strict(tmp_path: Path) -> None:
    config = tmp_path / "wifi_init_conf.json"
    config.write_text(json.dumps(_config(False)))
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_exe(
        fake_bin / "systemctl",
        """#!/bin/sh
if [ "$1" = "is-enabled" ]; then exit 1; fi
if [ "$1" = "stop" ] && [ "$2" = "wifi_roam@mlan0.service" ]; then exit 1; fi
exit 0
""",
    )
    _write_exe(fake_bin / "logger", "#!/bin/sh\nexit 0\n")
    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "WIFI_INIT_CONF_JSON": str(config),
        "WIFI_RUN_DIR": str(tmp_path / "run"),
        "WIFI_APPLY_STRICT": "0",
    }
    result = subprocess.run(
        ["bash", str(APPLY)], env=env, text=True, capture_output=True, timeout=5
    )
    assert result.returncode != 0


@pytest.mark.parametrize("disable_reply", ["fail", "still-enabled"])
def test_disallowed_owner_disable_or_postcheck_failure_is_fatal_without_strict(
    tmp_path: Path, disable_reply: str
) -> None:
    config = tmp_path / "wifi_init_conf.json"
    config.write_text(json.dumps(_config(False)))
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_exe(
        fake_bin / "systemctl",
        """#!/bin/sh
if [ "$1" = "is-enabled" ] && [ "$3" = "wifi_periodic_roam@mlan0.service" ]; then
  exit 0
fi
if [ "$1" = "disable" ] && [ "$2" = "wifi_periodic_roam@mlan0.service" ]; then
  [ "$DISABLE_REPLY" = "fail" ] && exit 1
  exit 0
fi
if [ "$1" = "is-enabled" ]; then exit 1; fi
exit 0
""",
    )
    _write_exe(fake_bin / "logger", "#!/bin/sh\nexit 0\n")
    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "WIFI_INIT_CONF_JSON": str(config),
        "WIFI_RUN_DIR": str(tmp_path / "run"),
        "WIFI_APPLY_STRICT": "0",
        "DISABLE_REPLY": disable_reply,
    }
    result = subprocess.run(
        ["bash", str(APPLY)], env=env, text=True, capture_output=True, timeout=5
    )
    assert result.returncode != 0


def test_deleted_boot_snapshot_is_not_recreated_from_mutated_json(tmp_path: Path) -> None:
    config = tmp_path / "wifi_init_conf.json"
    config.write_text(json.dumps(_config(True, generate_network_blocks=True)))
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_exe(
        fake_bin / "systemctl",
        "#!/bin/sh\nif [ \"$1\" = is-enabled ]; then exit 1; fi\nexit 0\n",
    )
    _write_exe(fake_bin / "logger", "#!/bin/sh\nexit 0\n")
    run_dir = tmp_path / "run"
    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "WIFI_INIT_CONF_JSON": str(config),
        "WIFI_RUN_DIR": str(run_dir),
    }

    first = subprocess.run(
        ["bash", str(APPLY)], env=env, text=True, capture_output=True, timeout=5
    )
    assert first.returncode == 0, first.stderr
    snapshot = run_dir / "mlan0.roam-policy.json"
    latch = tmp_path / ".mlan0.roam-policy.latched"
    assert snapshot.exists()
    assert latch.exists()

    snapshot.unlink()
    config.write_text(json.dumps(_config(False, bgscan_enabled=False)))
    second = subprocess.run(
        ["bash", str(APPLY)], env=env, text=True, capture_output=True, timeout=5
    )
    assert second.returncode != 0
    assert not snapshot.exists()


def test_owner_units_fail_closed_on_stale_queued_start() -> None:
    periodic = (SYSTEMD / "wifi_periodic_roam@.service").read_text()
    roam = (SYSTEMD / "wifi_roam@.service").read_text()
    bgscan = (SYSTEMD / "wifi_bgscan@.service").read_text()
    assert "ExecCondition=/bin/false" in periodic
    assert "RestartPreventExitStatus=3" in roam
    assert "RestartPreventExitStatus=3" in bgscan
