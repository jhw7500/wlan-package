"""#362: the boot-time `opcd -R` unit that restores the ESSID OPC ChangeIpAddress replaced.

Covers the unit's ordering contract, its presence in both package manifests, the
`.opc.enabled` gate that enables/disables it together with opcd.service (executed
against a recording ``systemctl`` double), the prerm teardown, and the two
persistent-SSID writers in wifi.sh that delete the restore marker.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess

import pytest

_SIBLING = Path(__file__).with_name("test_roam_owner_service_policy.py")
_spec = importlib.util.spec_from_file_location("_roam_owner_service_policy_helpers", _SIBLING)
assert _spec is not None and _spec.loader is not None
_helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_helpers)
_config = _helpers._config
_write_exe = _helpers._write_exe

WLAN_ROOT = Path(__file__).resolve().parents[4]
REPO_ROOT = WLAN_ROOT.parents[1]
UNIT_PATH = "etc/systemd/system/opcd-essid-restore.service"
UNIT = WLAN_ROOT / UNIT_PATH
APPLY = WLAN_ROOT / "usr/local/scripts/wifi_apply_enabled.sh"
WIFI_SH = WLAN_ROOT / "usr/local/scripts/wifi.sh"
PRERM = WLAN_ROOT / "DEBIAN/prerm"
PAYLOAD_MANIFEST = WLAN_ROOT / "DEBIAN/payload-manifest.txt"
SOURCE_MANIFEST = REPO_ROOT / "scripts/source_archive_manifest.txt"
MARKER = "/usr/local/opc/etc/changeip_essid.restore"
RESTORE_UNIT = "opcd-essid-restore.service"


def _unit_directives() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for line in UNIT.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "[")):
            continue
        key, _, value = line.partition("=")
        out.setdefault(key, []).append(value)
    return out


def test_unit_runs_once_before_the_wifi_stack_reads_the_conf() -> None:
    d = _unit_directives()

    assert d["DefaultDependencies"] == ["no"]
    assert "local-fs.target" in d["After"][0] and "sysinit.target" in d["After"][0]
    before = d["Before"][0].split()
    assert "wifi_init.service" in before and "wpa_supplicant@mlan0.service" in before
    assert d["Type"] == ["oneshot"]
    assert d["ExecStart"] == ["/usr/local/opc/bin/opcd -R"]
    assert d["ConditionPathExists"] == [MARKER]
    assert d["WantedBy"] == ["wifi-stack.target"]
    assert "Requires" not in d and "RemainAfterExit" not in d


def test_unit_is_in_both_manifests() -> None:
    assert UNIT_PATH in PAYLOAD_MANIFEST.read_text().splitlines()
    assert f"dist/wlan/{UNIT_PATH}" in SOURCE_MANIFEST.read_text().splitlines()


def _run_apply(tmp_path: Path, data: dict, *, opc_initially_enabled: bool) -> tuple[subprocess.CompletedProcess[str], list[str]]:
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
  case "$3" in
    opcd.service|opcd-essid-restore.service) [ "$FAKE_OPC_ENABLED" = "1" ] && exit 0 ;;
  esac
  exit 1
fi
exit 0
""",
    )
    _write_exe(fake_bin / "logger", "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$FAKE_LOGGER_CALLS\"\nexit 0\n")
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
            "FAKE_OPC_ENABLED": "1" if opc_initially_enabled else "0",
        },
        text=True,
        capture_output=True,
        timeout=5,
    )
    return result, calls.read_text().splitlines()


@pytest.mark.parametrize("opc_enabled", [True, False])
def test_opc_gate_leaves_the_restore_unit_alone(tmp_path: Path, opc_enabled: bool) -> None:
    """The restore unit is NOT gated by .opc.enabled: turning OPC off must not disable it, or
    a ChangeIp ESSID left behind would survive the next (clean) reboot while a power-cut
    reboot would restore it (tribunal A-R1-001).  ConditionPathExists already makes a boot
    without a marker a no-op."""
    data = _config(False)
    data["opc"] = {"enabled": opc_enabled}

    result, calls = _run_apply(tmp_path, data, opc_initially_enabled=not opc_enabled)

    assert result.returncode == 0, result.stderr
    assert ("enable opcd.service" in calls) is opc_enabled
    assert ("disable opcd.service" in calls) is not opc_enabled
    assert not any(RESTORE_UNIT in c for c in calls), calls


def test_postinst_enables_the_restore_unit_unconditionally() -> None:
    text = (WLAN_ROOT / "DEBIAN/postinst").read_text()
    enable_line = next(l for l in text.splitlines() if l.strip().startswith("systemctl enable watchdog"))

    assert RESTORE_UNIT in enable_line.split()
    assert "apply opcd-essid-restore.service" not in (WLAN_ROOT / "usr/local/scripts/wifi_apply_enabled.sh").read_text()


def test_prerm_tears_the_restore_unit_down_with_opcd() -> None:
    text = PRERM.read_text()

    assert "stop_service_if_active opcd-essid-restore" in text
    assert "disable_service_if_enabled opcd-essid-restore" in text


def test_persistent_ssid_writers_delete_the_marker_through_the_lock_child() -> None:
    text = WIFI_SH.read_text()

    assert f'OPC_ESSID_MARKER="${{OPC_ESSID_MARKER:-{MARKER}}}"' in text
    ssid_branch = text.split("  ssid)", 1)[1].split("  stage-psk)", 1)[0]
    connect_branch = text.split("  connect)", 1)[1].split("  scan)", 1)[0]
    assert 'wifi_wpa_run_child rm -f "$OPC_ESSID_MARKER"' in ssid_branch
    # connect deletes the marker from the helpers it calls for the two save
    # orders (#435): install-first and the cache switch that saves after the
    # verified association.
    for helper in ("connect_install_rendered_profile", "connect_cache_save_execute"):
        assert helper in connect_branch
        body = text.split(f"{helper}() {{", 1)[1].split("\n}\n", 1)[0]
        assert 'wifi_wpa_run_child rm -f "$OPC_ESSID_MARKER"' in body, helper
    assert 'rm -f "$OPC_ESSID_MARKER"' not in text.replace('wifi_wpa_run_child rm -f "$OPC_ESSID_MARKER"', ""), (
        "a bare rm would run outside the lock-holder child"
    )
