from pathlib import Path

import pytest


WLAN_ROOT = Path(__file__).resolve().parents[4]
REPO_ROOT = WLAN_ROOT.parents[1]
UNIT_ROOT = WLAN_ROOT / "etc/systemd/system"
LOGGER_SH = WLAN_ROOT / "usr/local/scripts/wifi_logger.sh"
PAYLOAD_MANIFEST = WLAN_ROOT / "DEBIAN/payload-manifest.txt"
SOURCE_MANIFEST = REPO_ROOT / "scripts/source_archive_manifest.txt"
CONTROL = WLAN_ROOT / "DEBIAN/control"
POSTINST = WLAN_ROOT / "DEBIAN/postinst"
FACTORY_RESET = WLAN_ROOT / "usr/local/scripts/factory_reset.sh"
FACTORY_RESET_LIB = WLAN_ROOT / "usr/local/scripts/wifi_factory_reset_lib.sh"

SYSTEM_CHILDREN = {
    "wifi_logger_cpu.service": (
        "ExecStart=/usr/local/scripts/wifi_logger_cpu.sh"
    ),
    "wifi_logger_mmc.service": (
        "ExecStart=/usr/local/scripts/wifi_logger_mmc.sh"
    ),
    "wifi_logger_temp.service": (
        "ExecStart=/usr/local/scripts/wifi_logger_temp.sh"
    ),
    "wifi_logger_mcp.service": (
        "ExecStart=/usr/local/scripts/wifi_logger_mcp.sh"
    ),
    "wifi_logger_summary.service": (
        "ExecStart=/bin/python3 /usr/local/logger/wifi_logger_summary.py"
    ),
}

INTERFACE_CHILDREN = (
    "wifi_logger_link@.service",
    "wifi_logger_scan@.service",
    "wifi_logger_stat@.service",
    "wifi_link_snapshot@.service",
)


def unit(name):
    return (UNIT_ROOT / name).read_text()


def test_system_controller_pulls_independent_children():
    controller = unit("wifi_logger.service")

    for name in SYSTEM_CHILDREN:
        assert name in controller
    assert "Type=oneshot" in controller
    assert "ExecStart=/bin/true" in controller
    assert "RemainAfterExit=yes" in controller
    assert "Requires=sys-subsystem-net-devices-mlan0.device" not in controller
    assert "/usr/local/scripts/wifi_logger.sh" not in controller


@pytest.mark.parametrize("name,exec_start", SYSTEM_CHILDREN.items())
def test_system_child_supervision_contract(name, exec_start):
    text = unit(name)

    assert "PartOf=wifi_logger.service" in text
    assert "After=wifi_init.service" in text
    assert "Type=simple" in text
    assert exec_start in text
    assert "Restart=always" in text
    assert "RestartSec=3" in text
    assert "StartLimitIntervalSec=300" in text
    assert "StartLimitBurst=10" in text
    assert "User=root" in text
    assert "StandardOutput=null" in text
    assert "StandardError=null" in text


@pytest.mark.parametrize("name", INTERFACE_CHILDREN)
def test_interface_child_restart_bursts_are_bounded(name):
    text = unit(name)

    assert "PartOf=wifi_init.service wifi_logger@%i.service" in text
    assert "Restart=always" in text
    assert "RestartSec=3" in text
    assert "StartLimitIntervalSec=300" in text
    assert "StartLimitBurst=10" in text
    assert "StartLimitIntervalSec=0" not in text


@pytest.mark.parametrize(
    "name",
    (
        "wifi_logger_scan@.service",
        "wifi_logger_stat@.service",
        "wifi_link_snapshot@.service",
    ),
)
def test_non_link_interface_children_remain_wireless_only(name):
    assert "ConditionPathIsDirectory=/sys/class/net/%i/wireless" in unit(name)


@pytest.mark.parametrize(
    "name", ("wifi_logger_link@.service", "wifi_logger_scan@.service", "wifi_logger_stat@.service")
)
def test_flock_exit_remains_restart_preventing(name):
    text = unit(name)

    assert "SuccessExitStatus=3" in text
    assert "RestartPreventExitStatus=3" in text


def test_legacy_system_logger_launcher_is_only_a_control_shim():
    text = LOGGER_SH.read_text()

    assert 'exec "${WIFI_SH:-/usr/local/scripts/wifi.sh}" log system start' in text
    assert "&" not in text


def test_logger_release_runtime_files_are_declared():
    payload = set(PAYLOAD_MANIFEST.read_text().splitlines())
    expected = {
        "etc/systemd/system/wifi_logger_cpu.service",
        "etc/systemd/system/wifi_logger_mcp.service",
        "etc/systemd/system/wifi_logger_mmc.service",
        "etc/systemd/system/wifi_logger_summary.service",
        "etc/systemd/system/wifi_logger_temp.service",
        "usr/local/scripts/wifi_logger_command_lib.sh",
        "usr/local/scripts/wifi_logger_control.sh",
    }

    assert expected <= payload


def test_logger_release_sources_and_tests_are_declared():
    source = set(SOURCE_MANIFEST.read_text().splitlines())
    runtime = {
        f"dist/wlan/{path}"
        for path in (
            "etc/systemd/system/wifi_logger_cpu.service",
            "etc/systemd/system/wifi_logger_mcp.service",
            "etc/systemd/system/wifi_logger_mmc.service",
            "etc/systemd/system/wifi_logger_summary.service",
            "etc/systemd/system/wifi_logger_temp.service",
            "usr/local/scripts/wifi_logger_command_lib.sh",
            "usr/local/scripts/wifi_logger_control.sh",
        )
    }
    tests = {
        "dist/wlan/usr/local/scripts/tests/test_logger_shell_timeouts.py",
        "dist/wlan/usr/local/scripts/tests/test_logger_supervision_units.py",
        "dist/wlan/usr/local/scripts/tests/test_wifi_logger_control.py",
    }

    assert runtime | tests <= source


def test_install_and_factory_reset_restore_logger_boot_policy():
    postinst = POSTINST.read_text()
    factory_reset = FACTORY_RESET.read_text()
    factory_lib = FACTORY_RESET_LIB.read_text()

    assert "systemctl enable" in postinst
    assert "wifi_logger" in postinst
    assert "wifi_logger@eth0" in postinst
    assert "customctl enable wifi_logger" in factory_reset
    assert "customctl enable wifi_logger@eth0" in factory_reset
    assert "factory_restore_service_state" in factory_reset
    assert '"$FACTORY_APPLY_ENABLED_SH"' in factory_lib


def test_logger_runtime_dependencies():
    fields = {}
    for line in CONTROL.read_text().splitlines():
        if ":" in line and not line.startswith(" "):
            key, value = line.split(":", 1)
            fields[key] = value.strip()

    dependencies = {item.strip().split()[0] for item in fields["Depends"].split(",")}
    assert {"bc", "sysstat"} <= dependencies
