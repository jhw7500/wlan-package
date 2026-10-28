"""Package supplicant startup when wifi_manager owns the wireless interface."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


WLAN_ROOT = Path(__file__).resolve().parents[4]
SERVICES = WLAN_ROOT / "usr/local/scripts/wifi_services.sh"
APPLY = WLAN_ROOT / "usr/local/scripts/wifi_apply_enabled.sh"
TEMPLATE = WLAN_ROOT / "opt/wlan/config/wifi_init_conf.json"
WPA_UNIT = WLAN_ROOT / "opt/wlan/config/wpa_supplicant/wpa_supplicant@.service"
MANUAL_TARGET = WLAN_ROOT / "etc/systemd/system/wifi-supplicant-manual.target"
POSTINST = WLAN_ROOT / "DEBIAN/postinst"
INIT = WLAN_ROOT / "usr/local/scripts/wifi_init.sh"

needs_jq = pytest.mark.skipif(shutil.which("jq") is None, reason="jq is required")
needs_systemctl = pytest.mark.skipif(
    shutil.which("systemctl") is None, reason="systemctl is required"
)


def _systemctl_in_root(root, *args):
    return subprocess.run(
        ["systemctl", f"--root={root}", *args],
        text=True,
        capture_output=True,
        timeout=10,
    )


@needs_systemctl
def test_enabled_supplicant_is_not_pulled_in_by_boot_target(tmp_path):
    unit_dir = tmp_path / "etc/systemd/system"
    unit_dir.mkdir(parents=True)
    shutil.copy2(WPA_UNIT, unit_dir)
    assert MANUAL_TARGET.is_file()
    shutil.copy2(MANUAL_TARGET, unit_dir)

    result = _systemctl_in_root(tmp_path, "enable", "wpa_supplicant@mlan0.service")
    assert result.returncode == 0, result.stderr
    state = _systemctl_in_root(tmp_path, "is-enabled", "wpa_supplicant@mlan0.service")
    assert state.returncode == 0, state.stderr
    assert state.stdout.strip() == "enabled"
    assert not (unit_dir / "multi-user.target.wants/wpa_supplicant@mlan0.service").is_symlink()
    assert (unit_dir / "wifi-supplicant-manual.target.wants/wpa_supplicant@mlan0.service").is_symlink()


def _run_postinst_migration(tmp_path, *, failing_rm=False):
    postinst = POSTINST.read_text()
    start = postinst.index("# >>> wpa-boot-link-migration")
    end = postinst.index("# <<< wpa-boot-link-migration", start)
    assert end < postinst.index("    /usr/local/scripts/wifi_apply_enabled.sh 2>/dev/null || true")
    boot_dir = "/etc/systemd/system/multi-user.target.wants/"
    migration = postinst[start:end]
    assert migration.count(boot_dir) == 1
    migration = migration.replace(boot_dir, f"{tmp_path}{boot_dir}", 1)
    failure_stub = "rm() { printf 'simulated rm failure\\n' >&2; return 13; }\n" if failing_rm else ""
    return subprocess.run(
        [
            "bash",
            "-c",
            "systemctl() { printf 'unexpected systemctl call\\n' >&2; return 99; }\n"
            "logger() { :; }\n" + failure_stub + migration,
        ],
        text=True,
        capture_output=True,
        timeout=10,
    )


@needs_systemctl
@pytest.mark.parametrize("iface", ["mlan0", "mlan1"])
def test_postinst_removes_legacy_boot_link_before_reapplying_policy(tmp_path, iface):
    unit_dir = tmp_path / "etc/systemd/system"
    unit_dir.mkdir(parents=True)
    unit = f"wpa_supplicant@{iface}.service"
    old_link = unit_dir / f"multi-user.target.wants/{unit}"
    manual_link = unit_dir / f"wifi-supplicant-manual.target.wants/{unit}"
    old_unit = WPA_UNIT.read_text().replace(
        "WantedBy=wifi-supplicant-manual.target", "WantedBy=multi-user.target"
    )
    (unit_dir / WPA_UNIT.name).write_text(old_unit)
    result = _systemctl_in_root(tmp_path, "enable", unit)
    assert result.returncode == 0, result.stderr
    assert old_link.is_symlink()

    shutil.copy2(WPA_UNIT, unit_dir)
    assert MANUAL_TARGET.is_file()
    shutil.copy2(MANUAL_TARGET, unit_dir)
    other_link = old_link.parent / "unrelated.service"
    other_link.symlink_to("/lib/systemd/system/unrelated.service")
    result = _run_postinst_migration(tmp_path)
    assert result.returncode == 0, result.stderr
    assert not old_link.is_symlink()
    assert other_link.is_symlink()
    result = _systemctl_in_root(tmp_path, "enable", unit)
    assert result.returncode == 0, result.stderr
    assert manual_link.is_symlink()


def test_postinst_fails_when_legacy_boot_link_cannot_be_removed(tmp_path):
    link = tmp_path / "etc/systemd/system/multi-user.target.wants/wpa_supplicant@mlan0.service"
    link.parent.mkdir(parents=True)
    link.symlink_to("/lib/systemd/system/wpa_supplicant@.service")

    result = _run_postinst_migration(tmp_path, failing_rm=True)

    assert result.returncode != 0
    assert link.is_symlink()
    assert "simulated rm failure" in result.stderr
    assert str(link) in result.stderr


def test_postinst_fails_when_legacy_boot_link_path_is_not_a_symlink(tmp_path):
    link = tmp_path / "etc/systemd/system/multi-user.target.wants/wpa_supplicant@mlan0.service"
    link.parent.mkdir(parents=True)
    link.write_text("operator data")

    result = _run_postinst_migration(tmp_path)

    assert result.returncode != 0
    assert link.read_text() == "operator data"
    assert str(link) in result.stderr


def _run_with_systemctl(tmp_path, script, *, bridge_iface, enabled_units):
    config = json.loads(TEMPLATE.read_text())
    config["global"]["MOD_PARA"] = "does-not-exist.conf"
    config["wbridge"]["bridge_iface"] = bridge_iface
    config["mlan1"]["enabled"] = True
    config_path = tmp_path / "wifi_init_conf.json"
    config_path.write_text(json.dumps(config))

    calls = tmp_path / "systemctl.calls"
    calls.write_text("")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    systemctl = fake_bin / "systemctl"
    systemctl.write_text(
        """#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_SYSTEMCTL_CALLS"
if [ "$1" = "is-enabled" ]; then
    shift
    [ "${1:-}" = "--quiet" ] && shift
    case ",${FAKE_ENABLED_UNITS}," in
        *",${1},"*) exit 0 ;;
    esac
    exit 1
fi
exit 0
"""
    )
    systemctl.chmod(0o755)
    logger = fake_bin / "logger"
    logger.write_text("#!/bin/sh\nexit 0\n")
    logger.chmod(0o755)

    result = subprocess.run(
        ["bash", str(script), "start"] if script == SERVICES else ["bash", str(script)],
        env=os.environ
        | {
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "WIFI_INIT_CONF_JSON": str(config_path),
            "WIFI_RUN_DIR": str(tmp_path / "run"),
            "WIFI_APPLY_STRICT": "1",
            "FAKE_SYSTEMCTL_CALLS": str(calls),
            "FAKE_ENABLED_UNITS": ",".join(enabled_units),
        },
        text=True,
        capture_output=True,
        timeout=10,
    )
    return result, calls.read_text().splitlines()


def test_services_list_uses_its_adjacent_library():
    result = subprocess.run(
        ["bash", str(SERVICES), "list"],
        text=True,
        capture_output=True,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr
    assert "wpa_supplicant@mlan0.service" in result.stdout.splitlines()


@needs_jq
@pytest.mark.parametrize("manager", ["wifi_manager", "wifi-manager"])
@pytest.mark.parametrize(
    "bridge_iface, owner_iface",
    [("mlan0", "mlan0"), ("mlan1", "mlan1"), ("none", "mlan0"), ("invalid", "mlan0")],
)
def test_manager_enabled_disables_package_supplicant_autostart(tmp_path, manager, bridge_iface, owner_iface):
    result, calls = _run_with_systemctl(
        tmp_path,
        APPLY,
        bridge_iface=bridge_iface,
        enabled_units=[
            manager,
            "wpa_supplicant@mlan0.service",
            "wpa_supplicant@mlan1.service",
        ],
    )

    assert result.returncode == 0, result.stderr
    other_iface = "mlan1" if owner_iface == "mlan0" else "mlan0"
    assert f"disable wpa_supplicant@{owner_iface}.service" in calls
    assert f"disable wpa_supplicant@{other_iface}.service" not in calls
    assert f"stop wpa_supplicant@{owner_iface}.service" not in calls


@needs_jq
@pytest.mark.parametrize("manager", ["wifi_manager", "wifi-manager"])
@pytest.mark.parametrize(
    "bridge_iface, owner_iface",
    [("mlan0", "mlan0"), ("mlan1", "mlan1"), ("none", "mlan0"), ("invalid", "mlan0")],
)
def test_manager_enabled_skips_package_start_even_if_unit_is_still_enabled(tmp_path, manager, bridge_iface, owner_iface):
    result, calls = _run_with_systemctl(
        tmp_path,
        SERVICES,
        bridge_iface=bridge_iface,
        enabled_units=[
            manager,
            "wpa_supplicant@mlan0.service",
            "wpa_supplicant@mlan1.service",
        ],
    )

    assert result.returncode == 0, result.stderr
    other_iface = "mlan1" if owner_iface == "mlan0" else "mlan0"
    assert f"start --no-block wpa_supplicant@{owner_iface}.service" not in calls
    assert f"start --no-block wpa_supplicant@{other_iface}.service" in calls


@pytest.mark.parametrize(
    "bridge_iface, owner_iface",
    [("mlan0", "mlan0"), ("mlan1", "mlan1"), ("none", "mlan0"), ("invalid", "mlan0")],
)
def test_wifi_init_normalizes_owner_interface(bridge_iface, owner_iface):
    init = INIT.read_text()
    start = init.index('BRIDGE_IFACE="${BRIDGE_IFACE:-mlan0}"')
    end = init.index('MAC_MODE="${MAC_MODE:-default}"', start)
    script = (
        'logger() { :; }\n'
        f'BRIDGE_IFACE="{bridge_iface}"\n'
        'WBRIDGE_ENABLED=true\n'
        + init[start:end]
        + 'printf "%s %s\\n" "$BRIDGE_IFACE" "$BRIDGE_NONE"\n'
    )

    result = subprocess.run(["bash", "-c", script], text=True, capture_output=True, timeout=5)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"{owner_iface} {'true' if bridge_iface == 'none' else 'false'}"


@needs_jq
def test_manager_only_suppresses_selected_bridge_interface(tmp_path):
    enabled_units = [
        "wifi_manager",
        "wpa_supplicant@mlan0.service",
        "wpa_supplicant@mlan1.service",
    ]
    result, calls = _run_with_systemctl(
        tmp_path, APPLY, bridge_iface="mlan1", enabled_units=enabled_units
    )

    assert result.returncode == 0, result.stderr
    assert "disable wpa_supplicant@mlan1.service" in calls
    assert "disable wpa_supplicant@mlan0.service" not in calls


@needs_jq
def test_package_autostart_is_restored_when_manager_is_disabled(tmp_path):
    result, calls = _run_with_systemctl(
        tmp_path,
        APPLY,
        bridge_iface="mlan0",
        enabled_units=["wpa_supplicant@mlan1.service"],
    )

    assert result.returncode == 0, result.stderr
    assert "enable wpa_supplicant@mlan0.service" in calls


@needs_jq
def test_package_starts_supplicant_when_manager_is_not_enabled(tmp_path):
    result, calls = _run_with_systemctl(
        tmp_path,
        SERVICES,
        bridge_iface="mlan0",
        enabled_units=["wpa_supplicant@mlan0.service"],
    )

    assert result.returncode == 0, result.stderr
    assert "start --no-block wpa_supplicant@mlan0.service" in calls
