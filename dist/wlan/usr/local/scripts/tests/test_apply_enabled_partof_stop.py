"""#458: units that wifi_init restarts through PartOf= must also be stopped when the
configuration switches them off.

``systemctl restart wifi_init.service`` propagates to every unit with
``PartOf=wifi_init.service``: the unit is restarted in the same transaction (a timer
without ``After=wifi_init`` is back at once, the others wait in the job queue).
``disable`` only removes the install symlinks and cannot cancel that job, so a unit
switched off in the JSON kept running after the restart (board: wifi_mgmt_log.timer stayed
``disabled``/``active`` after net_rx 6 -> 0).  wifi_apply_enabled.sh runs as ExecStartPre
and is executed here against a recording ``systemctl`` double.  The affected class is read
from the packaged unit files, so a new PartOf unit wired into apply() is covered without
editing this test.
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
APPLY = WLAN_ROOT / "usr/local/scripts/wifi_apply_enabled.sh"
SYSTEMD = WLAN_ROOT / "etc/systemd/system"
TIMER = "wifi_mgmt_log.timer"
_PARTOF_WIFI_INIT = re.compile(r"^PartOf=.*\bwifi_init\.service\b")


def _run_apply(
    run_dir: Path,
    data: dict,
    *,
    enabled_units: set[str],
    strict: bool = True,
    stop_fail: str = "",
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    run_dir.mkdir()
    config = run_dir / "wifi_init_conf.json"
    config.write_text(json.dumps(data))
    calls = run_dir / "systemctl.calls"
    calls.write_text("")
    enabled = run_dir / "enabled.units"
    enabled.write_text("".join(f"{unit}\n" for unit in sorted(enabled_units)))
    fake_bin = run_dir / "bin"
    fake_bin.mkdir()
    # The double keeps the enable state, so the owner post-disable check sees the
    # disable it just issued instead of a unit that still looks enabled.
    _write_exe(
        fake_bin / "systemctl",
        """#!/bin/sh
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
""",
    )
    _write_exe(fake_bin / "logger", "#!/bin/sh\nexit 0\n")
    result = subprocess.run(
        ["bash", str(APPLY)],
        env=os.environ
        | {
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "WIFI_INIT_CONF_JSON": str(config),
            "WIFI_APPLY_STRICT": "1" if strict else "0",
            "WIFI_RUN_DIR": str(run_dir / "run"),
            "FAKE_SYSTEMCTL_CALLS": str(calls),
            "FAKE_ENABLED_UNITS": str(enabled),
            "FAKE_STOP_FAIL": stop_fail,
        },
        text=True,
        capture_output=True,
        timeout=10,
    )
    return result, calls.read_text().splitlines()


def _partof_wifi_init(unit: str) -> bool:
    """PartOf= as packaged; a template instance reads its ``name@.type`` file.

    A unit without a packaged file (snmpd, wpa_supplicant@, systemd-timesyncd) is a
    distribution unit and is not part of wifi_init."""
    path = SYSTEMD / re.sub(r"@[^.]*\.", "@.", unit)
    if not path.is_file():
        return False
    return any(_PARTOF_WIFI_INIT.match(line.strip()) for line in path.read_text().splitlines())


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
    _, probe_calls = _run_apply(tmp_path / "probe", data, enabled_units=set())
    applied = {line.split()[-1] for line in probe_calls if line.startswith("is-enabled --quiet ")}

    result, calls = _run_apply(tmp_path / "run2", data, enabled_units=applied)

    assert result.returncode == 0, result.stderr
    disabled = {line.split()[1] for line in calls if line.startswith("disable ")}
    stopped = {line.split()[1] for line in calls if line.startswith("stop ")}
    partof = {unit for unit in disabled if _partof_wifi_init(unit)}
    other = {unit for unit in disabled if not _partof_wifi_init(unit)}

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
    # wlan_fw_watch deliberately has no PartOf= (its recovery action restarts wifi_init),
    # and only a comment line in its unit file mentions PartOf=wifi_init.service.
    assert {
        "wpa_supplicant@mlan0.service",
        "wpa_supplicant@mlan1.service",
        "wifi_bridge@mlan0.service",
        "wifi_bridge@mlan1.service",
        "snmpd.service",
        "opcd.service",
        "wifi_logger.service",
        "wlan_fw_watch.service",
    } <= other

    assert partof - stopped == set()
    # Units wifi_init does not restart keep the disable-only behaviour (a wifi_manager may
    # own the supplicant, the firmware watcher must outlive a wifi_init restart).
    # systemd-timesyncd has its own stop rule (#361).
    assert (other - {"systemd-timesyncd.service"}) & stopped == set()


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
