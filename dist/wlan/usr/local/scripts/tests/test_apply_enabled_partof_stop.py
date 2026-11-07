"""#458: units that wifi_init restarts through PartOf= must also be stopped when the
configuration switches them off, but only on wifi_init's own start path.

``systemctl restart wifi_init.service`` propagates to every unit with
``PartOf=wifi_init.service``: the unit is restarted in the same transaction (a timer
without ``After=wifi_init`` is back at once, the others wait in the job queue).
``disable`` only removes the install symlinks and cannot cancel that job, so a unit
switched off in the JSON kept running after the restart (board: wifi_mgmt_log.timer stayed
``disabled``/``active`` after net_rx 6 -> 0).  wifi_init.service opts in to the stop with
``Environment=WIFI_APPLY_STOP_PARTOF=1``.  The other callers of the same script -- the
logger CLI, postinst, factory reset and the shutdown sync -- keep the disable-only
behaviour, because ``wifi <scope> log enable|disable`` promises not to start or stop
anything.  The script is executed here against a recording ``systemctl`` double.  The
affected class is read from the packaged unit files, so a new PartOf unit wired into
apply() is covered without editing this test.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess

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
REPO_ROOT = WLAN_ROOT.parents[1]
APPLY = WLAN_ROOT / "usr/local/scripts/wifi_apply_enabled.sh"
CONTROL = WLAN_ROOT / "usr/local/scripts/wifi_logger_control.sh"
WIFI_INIT_UNIT = WLAN_ROOT / "etc/systemd/system/wifi_init.service"
TIMER = "wifi_mgmt_log.timer"
OPT_IN = "WIFI_APPLY_STOP_PARTOF"
# Where the package's unit files come from: etc/systemd/system is installed as is, the
# supplicant template ships from opt/wlan/config (postinst copies it to
# /lib/systemd/system), and wifi_bridge@ ships from the wlan-bridge submodule.
UNIT_DIRS = (
    WLAN_ROOT / "etc/systemd/system",
    WLAN_ROOT / "opt/wlan/config/wpa_supplicant",
    REPO_ROOT / "wlan-bridge/wbridge",
)
# PartOf=wifi_init.service units that stay disable-only: wifi_init.sh stops the supplicant
# itself before it reloads the module, and the bridge is BindsTo= its netdevs, so both go
# down with the reload without this script.
DISABLE_ONLY_PARTOF = ("wpa_supplicant@", "wifi_bridge@")
# Owners the script already stops on its own (disallowed or deprecated roam owners).
OWNER_PREFIXES = ("wifi_bgscan@", "wifi_roam@", "wifi_periodic_roam@")
_PARTOF_WIFI_INIT = re.compile(r"^PartOf=.*\bwifi_init\.service\b")

_FAKE_SYSTEMCTL = """#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_SYSTEMCTL_CALLS"
case "$1" in
  is-enabled)
    grep -qxF -- "$3" "$FAKE_ENABLED_UNITS"
    exit $?
    ;;
  enable)
    printf '%s\\n' "$2" >> "$FAKE_ENABLED_UNITS"
    ;;
  disable)
    grep -vxF -- "$2" "$FAKE_ENABLED_UNITS" > "$FAKE_ENABLED_UNITS.new"
    mv "$FAKE_ENABLED_UNITS.new" "$FAKE_ENABLED_UNITS"
    ;;
  stop)
    if [ -n "$FAKE_STOP_FAIL" ] && [ "$2" = "$FAKE_STOP_FAIL" ]; then
      exit 1
    fi
    ;;
esac
exit 0
"""


def _fake_env(run_dir: Path, *, enabled_units: set[str], stop_fail: str = "") -> tuple[dict[str, str], Path]:
    """A recording systemctl that keeps the enable state, so the owner post-disable check
    sees the disable it just issued instead of a unit that still looks enabled."""
    calls = run_dir / "systemctl.calls"
    calls.write_text("")
    enabled = run_dir / "enabled.units"
    enabled.write_text("".join(f"{unit}\n" for unit in sorted(enabled_units)))
    fake_bin = run_dir / "bin"
    fake_bin.mkdir()
    _write_exe(fake_bin / "systemctl", _FAKE_SYSTEMCTL)
    _write_exe(fake_bin / "logger", "#!/bin/sh\nexit 0\n")
    env = {key: value for key, value in os.environ.items() if key != OPT_IN}
    env |= {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "WIFI_RUN_DIR": str(run_dir / "run"),
        "FAKE_SYSTEMCTL_CALLS": str(calls),
        "FAKE_ENABLED_UNITS": str(enabled),
        "FAKE_STOP_FAIL": stop_fail,
    }
    return env, calls


def _run_apply(
    run_dir: Path,
    data: dict,
    *,
    enabled_units: set[str],
    strict: bool = True,
    stop_fail: str = "",
    stop_partof: bool = True,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    run_dir.mkdir()
    config = run_dir / "wifi_init_conf.json"
    config.write_text(json.dumps(data))
    env, calls = _fake_env(run_dir, enabled_units=enabled_units, stop_fail=stop_fail)
    env |= {
        "WIFI_INIT_CONF_JSON": str(config),
        "WIFI_APPLY_STRICT": "1" if strict else "0",
    }
    if stop_partof:
        env[OPT_IN] = "1"
    result = subprocess.run(
        ["bash", str(APPLY)],
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
    )
    return result, calls.read_text().splitlines()


def _unit_file(unit: str) -> Path | None:
    name = re.sub(r"@[^.]*\.", "@.", unit)
    for directory in UNIT_DIRS:
        path = directory / name
        if path.is_file():
            return path
    return None


def _partof_wifi_init(unit: str) -> bool:
    """PartOf= as packaged; a template instance reads its ``name@.type`` file.

    A unit without a packaged file (snmpd, systemd-timesyncd) is a distribution unit and
    is not part of wifi_init."""
    path = _unit_file(unit)
    if path is None:
        return False
    return any(_PARTOF_WIFI_INIT.match(line.strip()) for line in path.read_text().splitlines())


def _applied_units(tmp_path: Path, data: dict, *, stop_partof: bool) -> set[str]:
    _, probe_calls = _run_apply(tmp_path / "probe", data, enabled_units=set(), stop_partof=stop_partof)
    return {line.split()[-1] for line in probe_calls if line.startswith("is-enabled --quiet ")}


def test_net_rx_zero_stops_the_mgmt_log_timer_it_disables(tmp_path: Path) -> None:
    result, calls = _run_apply(tmp_path / "run1", _config(False), enabled_units={TIMER})

    assert result.returncode == 0, result.stderr
    assert f"disable {TIMER}" in calls
    assert f"stop {TIMER}" in calls


def test_net_rx_zero_stops_the_mgmt_log_timer_even_when_already_disabled(tmp_path: Path) -> None:
    # A previous restart disabled it without stopping it; the next restart must still
    # close the running (or queued) unit.
    result, calls = _run_apply(tmp_path / "run1", _config(False), enabled_units=set())

    assert result.returncode == 0, result.stderr
    assert f"disable {TIMER}" not in calls
    assert f"stop {TIMER}" in calls


def test_positive_net_rx_enables_and_does_not_stop_the_mgmt_log_timer(tmp_path: Path) -> None:
    data = _config(False)
    data["mlan0"]["net_rx"] = 6

    result, calls = _run_apply(tmp_path / "run1", data, enabled_units=set())

    assert result.returncode == 0, result.stderr
    assert f"enable {TIMER}" in calls
    assert f"stop {TIMER}" not in calls


def test_every_partof_unit_switched_off_is_stopped_and_other_units_are_not(tmp_path: Path) -> None:
    # _config(False): mlan0 on with its daemons off, mlan1 off (child loop), global
    # daemons off, net_rx 0.  The probe run lists every unit apply() evaluates; the second
    # run starts with all of them enabled so each switched-off unit shows up as a disable.
    data = _config(False)
    applied = _applied_units(tmp_path, data, stop_partof=True)

    result, calls = _run_apply(tmp_path / "run2", data, enabled_units=applied)

    assert result.returncode == 0, result.stderr
    disabled = {line.split()[1] for line in calls if line.startswith("disable ")}
    stopped = {line.split()[1] for line in calls if line.startswith("stop ")}
    exceptions = {unit for unit in disabled if unit.startswith(DISABLE_ONLY_PARTOF)}
    partof = {unit for unit in disabled if _partof_wifi_init(unit)} - exceptions
    other = {unit for unit in disabled if not _partof_wifi_init(unit)} - exceptions

    # Positive controls: the unit-file reader and the config both reach the units the
    # issue enumerates, so an empty set cannot pass the assertions below vacuously.
    assert {
        TIMER,
        "wifi_thermal_state.timer",
        "wifi_ping_monitor.service",
        "wifi_logger@eth0.service",
        "wifi_logger@mlan0.service",
        "wifi_checker@mlan0.service",
        "wifi_event@mlan0.service",
        "wifi_arping@mlan0.service",
        "wifi_logger@mlan1.service",
        "wifi_checker@mlan1.service",
        "wifi_event@mlan1.service",
        "wifi_arping@mlan1.service",
    } <= partof
    # The supplicant template is PartOf=wifi_init.service where it ships (opt/wlan/config).
    assert _partof_wifi_init("wpa_supplicant@mlan0.service")
    assert {
        "wpa_supplicant@mlan0.service",
        "wpa_supplicant@mlan1.service",
        "wifi_bridge@mlan0.service",
        "wifi_bridge@mlan1.service",
    } <= exceptions
    # wlan_fw_watch deliberately has no PartOf= (its recovery action restarts wifi_init),
    # and only a comment line in its unit file mentions PartOf=wifi_init.service.
    assert {"snmpd.service", "opcd.service", "wifi_logger.service", "wlan_fw_watch.service"} <= other

    assert partof - stopped == set()
    assert exceptions & stopped == set()
    # Units wifi_init does not restart keep the disable-only behaviour.  systemd-timesyncd
    # has its own stop rule (#361).
    assert (other - {"systemd-timesyncd.service"}) & stopped == set()


def test_callers_without_the_wifi_init_opt_in_stop_no_switched_off_unit(tmp_path: Path) -> None:
    # postinst, factory reset, the shutdown sync (wifi_apply_enabled.service ExecStop) and
    # the logger CLI run the script without the opt-in: they only change enable state.
    data = _config(False)
    applied = _applied_units(tmp_path, data, stop_partof=False)

    result, calls = _run_apply(tmp_path / "run2", data, enabled_units=applied, stop_partof=False)

    assert result.returncode == 0, result.stderr
    disabled = {line.split()[1] for line in calls if line.startswith("disable ")}
    stopped = {line.split()[1] for line in calls if line.startswith("stop ")}
    assert {TIMER, "wifi_logger@mlan0.service", "wifi_logger@eth0.service"} <= disabled
    # Only the roam owners keep their own unconditional stop, and timesyncd its #361 stop.
    assert {unit for unit in stopped if not unit.startswith(OWNER_PREFIXES)} - {"systemd-timesyncd.service"} == set()


def test_logger_cli_policy_change_with_the_real_apply_script_stops_no_logger(tmp_path: Path) -> None:
    # README.md and the guide promise that `wifi <scope> log enable|disable` only updates
    # the boot policy.  The CLI runs this script, so neither the scope it changes nor any
    # other scope's runtime-started logger may be stopped.
    data = _config(False)
    data["logger"]["enabled"] = True
    data["mlan0"]["logger"]["enabled"] = True
    config = tmp_path / "wifi_init_conf.json"
    config.write_text(json.dumps(data))
    for step, scope, action in ((1, "mlan0", "disable"), (2, "eth0", "enable")):
        run_dir = tmp_path / f"step{step}"
        run_dir.mkdir()
        env, calls = _fake_env(run_dir, enabled_units={"wifi_logger@mlan0.service"})
        fake_sync = run_dir / "bin" / "sync"
        _write_exe(fake_sync, "#!/bin/sh\nexit 0\n")
        env |= {
            "WIFI_INIT_CONF_JSON": str(config),
            "WIFI_LOGGER_SYSTEMCTL": str(run_dir / "bin" / "systemctl"),
            "WIFI_LOGGER_APPLY_ENABLED_SH": str(APPLY),
            "WIFI_LOGGER_SYNC": str(fake_sync),
        }

        result = subprocess.run(
            ["bash", str(CONTROL), scope, action],
            env=env,
            text=True,
            capture_output=True,
            timeout=20,
        )

        assert result.returncode == 0, result.stderr
        recorded = calls.read_text().splitlines()
        # Positive control: the real apply script ran and synced the changed policy.
        expected = "disable wifi_logger@mlan0.service" if step == 1 else "enable wifi_logger@eth0.service"
        assert expected in recorded
        assert [line for line in recorded if line.startswith("stop wifi_logger")] == []


def test_only_the_wifi_init_unit_opts_in_to_the_partof_stop() -> None:
    unit_lines = WIFI_INIT_UNIT.read_text().splitlines()
    assert f"Environment={OPT_IN}=1" in unit_lines
    # The exec line stays a plain path, so the release gate and exec_bit_targets.py keep
    # checking the script itself (an `env VAR=1 script` prefix would hide it behind
    # /usr/bin/env).
    assert "ExecStartPre=/usr/local/scripts/wifi_apply_enabled.sh" in unit_lines
    setters = []
    for path in WLAN_ROOT.rglob("*"):
        if not path.is_file() or "tests" in path.relative_to(WLAN_ROOT).parts:
            continue
        if path in (WIFI_INIT_UNIT, APPLY) or path.stat().st_size > 2_000_000:
            continue
        if OPT_IN in path.read_text(errors="ignore"):
            setters.append(str(path.relative_to(WLAN_ROOT)))
    assert setters == []


def test_stop_failure_of_a_switched_off_partof_unit_is_reported(tmp_path: Path) -> None:
    result, calls = _run_apply(
        tmp_path / "strict", _config(False), enabled_units={TIMER}, stop_fail=TIMER
    )

    assert f"stop {TIMER}" in calls
    assert result.returncode == 1

    lenient, _ = _run_apply(
        tmp_path / "lenient", _config(False), enabled_units={TIMER}, strict=False, stop_fail=TIMER
    )

    assert lenient.returncode == 0, lenient.stderr
