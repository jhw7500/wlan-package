import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest


WLAN_ROOT = Path(__file__).resolve().parents[4]
REPO_ROOT = WLAN_ROOT.parents[1]
TEMPLATE = WLAN_ROOT / "opt/wlan/config/wifi_init_conf.json"
SCHEMA = REPO_ROOT / "docs/wifi_init_conf.schema.json"
APPLY_ENABLED = WLAN_ROOT / "usr/local/scripts/wifi_apply_enabled.sh"
SERVICES = WLAN_ROOT / "usr/local/scripts/wifi_services.sh"
SERVICES_LIB = WLAN_ROOT / "usr/local/scripts/wifi_services_lib.sh"
CONTROL = WLAN_ROOT / "usr/local/scripts/wifi_logger_control.sh"
WIFI = WLAN_ROOT / "usr/local/scripts/wifi.sh"
LOGCTL = WLAN_ROOT / "usr/local/scripts/logctl.sh"


@dataclass
class ControlResult:
    returncode: int
    stdout: str
    stderr: str
    calls: list[list[str]]
    config: Path


def _write_exe(path, text):
    path.write_text(text)
    path.chmod(0o755)


@pytest.fixture
def run_control(tmp_path):
    config = tmp_path / "wifi_init_conf.json"
    config.write_bytes(TEMPLATE.read_bytes())
    calls = tmp_path / "systemctl.calls"
    calls.write_text("")

    fake_systemctl = tmp_path / "systemctl"
    _write_exe(
        fake_systemctl,
        """#!/bin/sh
printf '%s\n' "$*" >> "$FAKE_SYSTEMCTL_CALLS"
case "$1" in
  is-enabled)
    case ",${FAKE_ENABLED_UNITS:-}," in *",$3,"*) exit 0;; *) exit 1;; esac ;;
  is-active)
    case ",${FAKE_ACTIVE_UNITS:-}," in *",$3,"*) exit 0;; *) exit 3;; esac ;;
  show)
    printf 'ActiveState=%s\nSubState=%s\nNRestarts=%s\n' \
      "${FAKE_ACTIVE_STATE:-active}" "${FAKE_SUB_STATE:-running}" \
      "${FAKE_NRESTARTS:-0}"
    ;;
esac
exit "${FAKE_SYSTEMCTL_RC:-0}"
""",
    )
    fake_apply = tmp_path / "apply-enabled"
    _write_exe(fake_apply, '#!/bin/sh\nexit "${FAKE_APPLY_RC:-0}"\n')
    fake_sync = tmp_path / "sync"
    _write_exe(fake_sync, '#!/bin/sh\nexit "${FAKE_SYNC_RC:-0}"\n')

    def run(scope, action, **extra_env):
        calls.write_text("")
        env = os.environ | {
            "WIFI_INIT_CONF_JSON": str(config),
            "WIFI_LOGGER_SYSTEMCTL": str(fake_systemctl),
            "WIFI_LOGGER_APPLY_ENABLED_SH": str(fake_apply),
            "WIFI_LOGGER_SYNC": str(fake_sync),
            "FAKE_SYSTEMCTL_CALLS": str(calls),
        }
        env.update({key: str(value) for key, value in extra_env.items()})
        cp = subprocess.run(
            ["bash", str(CONTROL), scope, action],
            env=env,
            text=True,
            capture_output=True,
            timeout=5,
        )
        recorded = [line.split() for line in calls.read_text().splitlines()]
        return ControlResult(
            cp.returncode, cp.stdout, cp.stderr, recorded, config
        )

    return run


def test_logger_policy_defaults_are_explicit():
    template = json.loads(TEMPLATE.read_text())
    schema = json.loads(SCHEMA.read_text())

    assert template["logger"]["enabled"] is True
    assert template["mlan0"]["logger"]["enabled"] is True
    assert template["mlan1"]["logger"]["enabled"] is False
    assert template["eth0"]["logger"]["enabled"] is True

    logger_schema = schema["properties"]["logger"]["properties"]
    eth0_logger_schema = (
        schema["properties"]["eth0"]["properties"]["logger"]["properties"]
    )
    assert logger_schema["enabled"]["type"] == "boolean"
    assert logger_schema["enabled"]["default"] is True
    assert eth0_logger_schema["enabled"]["type"] == "boolean"
    assert eth0_logger_schema["enabled"]["default"] is True


def test_apply_enabled_maps_system_and_eth0_before_mfg_gate():
    text = APPLY_ENABLED.read_text()
    mfg_gate = text.index("if [ \"${MFG_MODE:-0}\" = \"1\" ]")

    system_mapping = 'apply wifi_logger.service     "$(get_bool ".logger.enabled" "true")"'
    eth0_mapping = 'apply wifi_logger@eth0.service "$(get_bool ".eth0.logger.enabled" "true")"'
    assert system_mapping in text
    assert eth0_mapping in text
    assert text.index(system_mapping) < mfg_gate
    assert text.index(eth0_mapping) < mfg_gate


def test_boot_starter_includes_non_wireless_logger_groups_before_mfg_gate():
    services = SERVICES_LIB.read_text()
    starter = SERVICES.read_text()

    assert "wifi_logger.service" in services
    assert "wifi_logger@eth0.service" in services
    assert "wifi_services_start_non_wireless" in starter
    assert starter.index("wifi_services_start_non_wireless") < starter.index(
        'if [ "$(_mfg_mode)" = "1" ]'
    )


@pytest.mark.parametrize(
    "scope,unit",
    [
        ("system", "wifi_logger.service"),
        ("mlan0", "wifi_logger@mlan0.service"),
        ("mlan1", "wifi_logger@mlan1.service"),
        ("eth0", "wifi_logger@eth0.service"),
    ],
)
def test_runtime_start_targets_only_controller(run_control, scope, unit):
    result = run_control(scope, "start")

    assert result.returncode == 0
    assert result.calls == [["start", unit]]


def test_system_stop_warns_about_thermal_protection(run_control):
    result = run_control("system", "stop")

    assert result.returncode == 0
    assert "overtemperature protection" in result.stderr
    assert result.calls == [["stop", "wifi_logger.service"]]


def test_restart_resets_expected_children_before_controller(run_control):
    result = run_control("eth0", "restart")

    assert result.returncode == 0
    assert result.calls == [
        ["reset-failed", "wifi_logger_link@eth0.service"],
        ["restart", "wifi_logger@eth0.service"],
    ]


def test_system_disable_updates_only_system_logger(run_control):
    before = json.loads(run_control("system", "status").config.read_text())

    result = run_control("system", "disable")
    after = json.loads(result.config.read_text())

    assert result.returncode == 0
    assert "overtemperature protection" in result.stderr
    assert after["logger"]["enabled"] is False
    assert after["mlan0"] == before["mlan0"]
    assert after["mlan1"] == before["mlan1"]
    assert after["eth0"] == before["eth0"]


def test_eth0_enable_updates_only_eth0_logger(run_control):
    before = json.loads(run_control("system", "status").config.read_text())

    result = run_control("eth0", "disable")
    after = json.loads(result.config.read_text())

    assert result.returncode == 0
    assert after["eth0"]["logger"]["enabled"] is False
    assert after["logger"] == before["logger"]
    assert after["mlan0"] == before["mlan0"]


def test_malformed_json_changes_neither_file_nor_systemd(run_control):
    probe = run_control("system", "status")
    probe.config.write_text("{")

    result = run_control("mlan0", "enable")

    assert result.returncode == 2
    assert result.config.read_text() == "{"
    assert result.calls == []


def test_sync_failure_does_not_replace_config(run_control):
    probe = run_control("system", "status")
    before = probe.config.read_bytes()

    result = run_control("mlan0", "disable", FAKE_SYNC_RC=1)

    assert result.returncode == 1
    assert result.config.read_bytes() == before


def test_apply_failure_keeps_committed_policy_for_boot_retry(run_control):
    result = run_control("mlan1", "enable", FAKE_APPLY_RC=1)

    assert result.returncode == 1
    assert json.loads(result.config.read_text())["mlan1"]["logger"]["enabled"] is True


def test_status_reports_healthy_controller_and_children(run_control):
    result = run_control(
        "system",
        "status",
        FAKE_ENABLED_UNITS="wifi_logger.service",
        FAKE_ACTIVE_STATE="active",
        FAKE_SUB_STATE="running",
    )

    assert result.returncode == 0
    assert "wifi_logger.service" in result.stdout
    assert "wifi_logger_temp.service" in result.stdout
    assert "active" in result.stdout


def test_status_returns_three_for_intentionally_disabled_inactive_group(run_control):
    result = run_control(
        "mlan1",
        "status",
        FAKE_ACTIVE_STATE="inactive",
        FAKE_SUB_STATE="dead",
    )

    assert result.returncode == 3
    assert "disabled" in result.stdout


def test_status_returns_zero_for_healthy_runtime_override(run_control):
    result = run_control(
        "mlan1",
        "status",
        FAKE_ACTIVE_STATE="active",
        FAKE_SUB_STATE="running",
    )

    assert result.returncode == 0
    assert "runtime-override" in result.stdout


@pytest.mark.parametrize(
    "scope,action",
    [("all", "start"), ("interfaces", "stop"), ("mlan0", "clean")],
)
def test_invalid_scope_or_action_returns_usage(run_control, scope, action):
    result = run_control(scope, action)

    assert result.returncode == 2
    assert result.calls == []


@pytest.mark.parametrize(
    "argv,expected",
    [
        (["mlan0", "log", "status"], ["mlan0", "status"]),
        (["1", "log", "restart"], ["mlan1", "restart"]),
        (["eth0", "log", "enable"], ["eth0", "enable"]),
        (["log", "system", "disable"], ["system", "disable"]),
    ],
)
def test_wifi_dispatches_logger_lifecycle_to_helper(tmp_path, argv, expected):
    calls = tmp_path / "control.calls"
    fake_control = tmp_path / "control"
    _write_exe(
        fake_control,
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$FAKE_CONTROL_CALLS"\nexit 17\n',
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_exe(fake_bin / "logger", "#!/bin/sh\nexit 0\n")
    _write_exe(fake_bin / "sync", "#!/bin/sh\nexit 0\n")

    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "WIFI_LOGGER_CONTROL_SH": str(fake_control),
        "FAKE_CONTROL_CALLS": str(calls),
    }
    cp = subprocess.run(
        ["bash", str(WIFI), *argv],
        env=env,
        text=True,
        capture_output=True,
        timeout=5,
    )

    assert cp.returncode == 17
    assert calls.read_text().split() == expected


@pytest.mark.parametrize(
    "action,expected",
    [
        ("start", ["log", "system", "start"]),
        ("stop", ["log", "system", "stop"]),
        ("restart", ["log", "system", "restart"]),
        ("status", ["log", "system", "status"]),
        ("enable", ["log", "system", "enable"]),
        ("disable", ["log", "system", "disable"]),
        ("clean", ["log", "reset"]),
    ],
)
def test_logctl_is_only_a_wifi_sh_compatibility_wrapper(tmp_path, action, expected):
    calls = tmp_path / "wifi.calls"
    fake_wifi = tmp_path / "wifi"
    _write_exe(
        fake_wifi,
        '#!/bin/sh\nprintf "%s\\n" "$*" > "$FAKE_WIFI_CALLS"\nexit 23\n',
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_exe(fake_bin / "systemctl", "#!/bin/sh\nexit 0\n")
    _write_exe(fake_bin / "rm", "#!/bin/sh\nexit 0\n")
    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "WIFI_SH": str(fake_wifi),
        "FAKE_WIFI_CALLS": str(calls),
    }

    cp = subprocess.run(
        ["bash", str(LOGCTL), action],
        env=env,
        text=True,
        capture_output=True,
        timeout=5,
    )

    assert cp.returncode == 23
    assert calls.read_text().split() == expected
