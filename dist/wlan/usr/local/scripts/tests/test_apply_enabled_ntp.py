"""#361: .global.ntp.enabled drives systemd-timesyncd.service through wifi_apply_enabled.sh.

The script is executed against a recording ``systemctl`` double, so these tests assert the
enable/disable calls the package would issue (default true when the key is absent, as on an
upgrade before json_merge adds it), not merely that the source text names the unit.  The
boot-time start list is checked through wifi_services_lib.sh's list function.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess

import pytest

# Reuse the config builder and the executable writer of the sibling apply-enabled test
# by file path, so collection works the same whether this directory is imported as a
# package or with the test file's directory on sys.path.
_SIBLING = Path(__file__).with_name("test_roam_owner_service_policy.py")
_spec = importlib.util.spec_from_file_location("_roam_owner_service_policy_helpers", _SIBLING)
assert _spec is not None and _spec.loader is not None
_helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_helpers)
_config = _helpers._config
_write_exe = _helpers._write_exe

WLAN_ROOT = Path(__file__).resolve().parents[4]
APPLY = WLAN_ROOT / "usr/local/scripts/wifi_apply_enabled.sh"
SERVICES_LIB = WLAN_ROOT / "usr/local/scripts/wifi_services_lib.sh"
UNIT = "systemd-timesyncd.service"


def _run_apply(tmp_path: Path, data: dict, *, ntp_initially_enabled: bool) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    config = tmp_path / "wifi_init_conf.json"
    config.write_text(json.dumps(data))
    calls = tmp_path / "systemctl.calls"
    logs = tmp_path / "logger.calls"
    calls.write_text("")
    logs.write_text("")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_exe(
        fake_bin / "systemctl",
        """#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_SYSTEMCTL_CALLS"
if [ "$1" = "is-enabled" ]; then
  if [ "$3" = "systemd-timesyncd.service" ] && [ "$FAKE_NTP_ENABLED" = "1" ]; then
    exit 0
  fi
  exit 1
fi
exit 0
""",
    )
    _write_exe(
        fake_bin / "logger",
        """#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_LOGGER_CALLS"
exit 0
""",
    )
    result = subprocess.run(
        ["bash", str(APPLY)],
        env=os.environ
        | {
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "WIFI_INIT_CONF_JSON": str(config),
            "WIFI_APPLY_STRICT": "1",
            "WIFI_RUN_DIR": str(tmp_path / "run"),
            "FAKE_SYSTEMCTL_CALLS": str(calls),
            "FAKE_LOGGER_CALLS": str(logs),
            "FAKE_NTP_ENABLED": "1" if ntp_initially_enabled else "0",
        },
        text=True,
        capture_output=True,
        timeout=5,
    )
    return result, calls.read_text().splitlines()


def test_absent_key_defaults_to_enabling_timesyncd(tmp_path: Path) -> None:
    data = _config(False)
    assert "ntp" not in data["global"]

    result, calls = _run_apply(tmp_path, data, ntp_initially_enabled=False)

    assert result.returncode == 0, result.stderr
    assert f"enable {UNIT}" in calls
    assert f"disable {UNIT}" not in calls


@pytest.mark.parametrize("value", [True, "true", 1])
def test_true_enables_timesyncd_when_it_is_disabled(tmp_path: Path, value) -> None:
    data = _config(False)
    data["global"]["ntp"] = {"enabled": value}

    result, calls = _run_apply(tmp_path, data, ntp_initially_enabled=False)

    assert result.returncode == 0, result.stderr
    assert f"enable {UNIT}" in calls
    assert f"disable {UNIT}" not in calls


@pytest.mark.parametrize("value", [False, "false", 0])
def test_false_disables_timesyncd_when_it_is_enabled(tmp_path: Path, value) -> None:
    data = _config(False)
    data["global"]["ntp"] = {"enabled": value}

    result, calls = _run_apply(tmp_path, data, ntp_initially_enabled=True)

    assert result.returncode == 0, result.stderr
    assert f"disable {UNIT}" in calls
    assert f"enable {UNIT}" not in calls
    # disable changes only the unit file; an instance sysinit already started (boot after an
    # unclean shutdown that skipped the ExecStop sync) must be brought down here as well.
    assert f"stop {UNIT}" in calls


def test_false_stops_timesyncd_even_when_already_disabled(tmp_path: Path) -> None:
    data = _config(False)
    data["global"]["ntp"] = {"enabled": False}

    result, calls = _run_apply(tmp_path, data, ntp_initially_enabled=False)

    assert result.returncode == 0, result.stderr
    assert f"disable {UNIT}" not in calls
    assert f"stop {UNIT}" in calls


def test_matching_state_issues_no_change(tmp_path: Path) -> None:
    data = _config(False)
    data["global"]["ntp"] = {"enabled": True}

    result, calls = _run_apply(tmp_path, data, ntp_initially_enabled=True)

    assert result.returncode == 0, result.stderr
    assert f"is-enabled --quiet {UNIT}" in calls
    assert f"enable {UNIT}" not in calls
    assert f"disable {UNIT}" not in calls
    assert f"stop {UNIT}" not in calls


def test_ntp_gate_is_applied_before_the_mfg_early_return() -> None:
    """MFG mode cannot be exercised here (the flag is read from /lib/firmware), so the
    ordering contract is checked on the script text: the toggle must sit with the other
    non-wireless units that precede the ``MFG_MODE`` branch, or an MFG board would ignore it."""
    source = APPLY.read_text()
    ntp = source.index('apply systemd-timesyncd.service "$_ntp_enabled"')
    mfg_branch = source.index('if [ "${MFG_MODE:-0}" = "1" ]; then')
    logger_line = source.index('apply wifi_logger.service')

    assert logger_line < ntp < mfg_branch
    assert source.count("apply systemd-timesyncd.service") == 1
    assert source.count('get_bool ".global.ntp.enabled" "true"') == 1


def test_package_removal_restores_the_image_default() -> None:
    """prerm mirrors the snmpd precedent: the package enabled a distribution-owned unit, so
    removing the policy owner (JSON + wifi_apply_enabled.sh) puts it back to disabled."""
    text = (WLAN_ROOT / "DEBIAN/prerm").read_text()

    assert "stop_service_if_active systemd-timesyncd" in text
    assert "disable_service_if_enabled systemd-timesyncd" in text


def test_boot_start_list_includes_timesyncd_outside_the_wireless_group() -> None:
    script = f'. "{SERVICES_LIB}"; wifi_services_non_wireless_list; echo ---; wifi_services_wireless_list'
    result = subprocess.run(["bash", "-c", script], text=True, capture_output=True, timeout=5)

    assert result.returncode == 0, result.stderr
    non_wireless, _, wireless = result.stdout.partition("---\n")
    assert UNIT in non_wireless.split()
    assert UNIT not in wireless.split()
