"""#418: package upgrades hand active wifi_event instances to the new script.

The wireless link must stay up during installation, so postinst may restart only
the active wifi_event@mlan0/mlan1 monitors.  wpa_supplicant and other boot-loaded
components keep running until the documented reboot.
"""

import os
import stat
import subprocess
from pathlib import Path


POSTINST = Path(__file__).resolve().parents[4] / "DEBIAN" / "postinst"


def _upgrade_restart_block() -> str:
    text = POSTINST.read_text(encoding="utf-8")
    start_marker = "# >>> wifi-event-upgrade-restart"
    end_marker = "# <<< wifi-event-upgrade-restart"
    assert start_marker in text, "postinst upgrade restart block is missing"
    assert end_marker in text, "postinst upgrade restart block end is missing"
    return text[text.index(start_marker) : text.index(end_marker)]


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _run_block(
    tmp_path: Path,
    *,
    old_version: str,
    active_units: tuple[str, ...],
    restart_rc: int = 0,
):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "systemctl.calls"
    logs = tmp_path / "logger.calls"
    calls.touch()
    logs.touch()

    _write_executable(
        bin_dir / "systemctl",
        """#!/bin/sh
printf '%s\\n' "$*" >> "$SYSTEMCTL_CALLS"
if [ "$1" = "is-active" ]; then
    case ",$ACTIVE_UNITS," in
        *,"$3",*) exit 0 ;;
        *) exit 3 ;;
    esac
fi
if [ "$1" = "restart" ]; then
    exit "${RESTART_RC:-0}"
fi
exit 0
""",
    )
    _write_executable(
        bin_dir / "logger",
        "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$LOGGER_CALLS\"\n",
    )

    script = """set -e
tag=postinst
KEY=PKG
set -- configure "$OLD_VERSION"
""" + _upgrade_restart_block()
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "OLD_VERSION": old_version,
        "ACTIVE_UNITS": ",".join(active_units),
        "RESTART_RC": str(restart_rc),
        "SYSTEMCTL_CALLS": str(calls),
        "LOGGER_CALLS": str(logs),
    }
    result = subprocess.run(
        ["bash", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    return result, calls.read_text().splitlines(), logs.read_text().splitlines()


def test_upgrade_restarts_only_active_wifi_event_instance(tmp_path: Path) -> None:
    result, calls, logs = _run_block(
        tmp_path,
        old_version="0.7.5",
        active_units=("wifi_event@mlan0.service",),
    )

    assert result.returncode == 0, result.stderr
    assert "restart wifi_event@mlan0.service" in calls
    assert "restart wifi_event@mlan1.service" not in calls
    assert all("wpa_supplicant" not in call for call in calls)
    assert any(
        "restarted active wifi_event@mlan0.service with upgraded script" in log
        for log in logs
    )


def test_upgrade_logs_that_reboot_is_still_required(tmp_path: Path) -> None:
    result, calls, logs = _run_block(
        tmp_path,
        old_version="0.7.5",
        active_units=(),
    )

    assert result.returncode == 0, result.stderr
    assert not any(call.startswith("restart ") for call in calls)
    reboot_logs = [log for log in logs if "reboot required" in log]
    assert len(reboot_logs) == 1
    assert reboot_logs[0].startswith("-p local0.notice")
    assert "wpa_supplicant" in reboot_logs[0]
    assert "other boot-loaded components" in reboot_logs[0]
    assert "wireless link preserved" in reboot_logs[0]


def test_restart_failure_warns_without_claiming_success(tmp_path: Path) -> None:
    result, _calls, logs = _run_block(
        tmp_path,
        old_version="0.7.5",
        active_units=("wifi_event@mlan0.service",),
        restart_rc=1,
    )

    assert result.returncode == 0, result.stderr
    assert any(
        log.startswith("-p local0.warn")
        and "failed to restart active wifi_event@mlan0.service" in log
        for log in logs
    )
    assert not any(
        "restarted active wifi_event@mlan0.service" in log for log in logs
    )
    assert any(
        "reboot required" in log and "wpa_supplicant" in log for log in logs
    )


def test_fresh_install_does_not_run_upgrade_restart_policy(tmp_path: Path) -> None:
    result, calls, logs = _run_block(
        tmp_path,
        old_version="",
        active_units=("wifi_event@mlan0.service", "wifi_event@mlan1.service"),
    )

    assert result.returncode == 0, result.stderr
    assert calls == []
    assert logs == []
