"""#361: postinst turns on systemd-timesyncd and the lab default-route script is gone.

The timesyncd checks execute the marked postinst fragment with a ``systemctl``
boundary double that records every call, so they assert what the package asks
systemd to do (and that a refusal does not abort the install), not merely that the
source text contains a command.  The lab-gateway checks assert the package
payload, the source archive manifest and the source tree agree that
``etc/networkd-dispatcher/routable.d/10-set-gateway.sh`` is no longer shipped.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile

import pytest


REPO = Path(__file__).resolve().parents[2]
POSTINST = REPO / "dist/wlan/DEBIAN/postinst"
PAYLOAD_MANIFEST = REPO / "dist/wlan/DEBIAN/payload-manifest.txt"
SOURCE_ARCHIVE_MANIFEST = REPO / "scripts/source_archive_manifest.txt"
LAB_GATEWAY_SCRIPT = "etc/networkd-dispatcher/routable.d/10-set-gateway.sh"
TIMESYNCD_UNIT = "systemd-timesyncd.service"


def _block(name: str) -> str:
    source = POSTINST.read_text()
    start = f"  # >>> {name}\n"
    end = f"  # <<< {name}\n"
    assert start in source and end in source, f"postinst block {name!r} is missing"
    return source.split(start, 1)[1].split(end, 1)[0]


def _run_timesyncd_block(*, enable_rc: int, restart_rc: int = 0) -> tuple[subprocess.CompletedProcess[str], list[str], list[str]]:
    with tempfile.TemporaryDirectory(prefix="timesyncd-enable-") as tmp:
        root = Path(tmp)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        calls = root / "calls"
        logs = root / "logs"
        calls.write_text("")
        logs.write_text("")

        fake_systemctl = bin_dir / "systemctl"
        fake_systemctl.write_text(
            """#!/bin/sh
printf '%s\\n' "$*" >> "$CALLS"
case "$1" in
  enable) exit "$ENABLE_RC" ;;
  restart) exit "$RESTART_RC" ;;
  *) exit 65 ;;
esac
"""
        )
        fake_systemctl.chmod(0o755)

        script = "\n".join(
            (
                "#!/bin/bash",
                "set -e",
                "tag=postinst-test",
                "KEY=PKG",
                'logger() { printf \'%s\\n\' "$*" >> "$LOGS"; }',
                _block("timesyncd-enable"),
                "printf 'completed\\n'",
            )
        )
        env = os.environ | {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "CALLS": str(calls),
            "LOGS": str(logs),
            "ENABLE_RC": str(enable_rc),
            "RESTART_RC": str(restart_rc),
        }
        result = subprocess.run(
            ["bash"], input=script, text=True, capture_output=True, env=env, timeout=5
        )
        return result, calls.read_text().splitlines(), logs.read_text().splitlines()


def test_timesyncd_is_enabled_and_restarted_to_read_the_installed_conf() -> None:
    result, calls, logs = _run_timesyncd_block(enable_rc=0)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "completed"
    assert calls == [
        f"enable {TIMESYNCD_UNIT}",
        f"restart --no-block {TIMESYNCD_UNIT}",
    ]
    assert any(TIMESYNCD_UNIT in line and "enabled" in line for line in logs), logs


@pytest.mark.parametrize("enable_rc", (1, 5))
def test_timesyncd_enable_failure_is_logged_and_does_not_abort_the_install(enable_rc: int) -> None:
    result, calls, logs = _run_timesyncd_block(enable_rc=enable_rc)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "completed"
    assert calls == [f"enable {TIMESYNCD_UNIT}"], calls
    assert any("local0.warn" in line and TIMESYNCD_UNIT in line for line in logs), logs


def test_timesyncd_restart_failure_is_logged_and_does_not_abort_the_install() -> None:
    result, calls, logs = _run_timesyncd_block(enable_rc=0, restart_rc=1)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "completed"
    assert calls == [
        f"enable {TIMESYNCD_UNIT}",
        f"restart --no-block {TIMESYNCD_UNIT}",
    ]
    assert any("local0.warn" in line and "restart" in line for line in logs), logs


def test_lab_gateway_script_is_not_in_the_package_payload() -> None:
    payload = PAYLOAD_MANIFEST.read_text().splitlines()

    assert LAB_GATEWAY_SCRIPT not in payload
    assert not any(line.startswith("etc/networkd-dispatcher/") for line in payload), payload


def test_lab_gateway_script_is_not_in_the_source_archive() -> None:
    archive = SOURCE_ARCHIVE_MANIFEST.read_text().splitlines()

    assert f"dist/wlan/{LAB_GATEWAY_SCRIPT}" not in archive


def test_lab_gateway_script_is_not_in_the_source_tree() -> None:
    assert not (REPO / "dist/wlan" / LAB_GATEWAY_SCRIPT).exists()
    assert not (REPO / "dist/wlan/etc/networkd-dispatcher").exists()
