"""#361: postinst starts systemd-timesyncd when the .global.ntp.enabled gate left it enabled,
and the lab default-route script is gone.

The timesyncd checks execute the marked postinst fragment with a ``systemctl``
boundary double that records every call, so they assert what the package asks
systemd to do (restart only after the gate enabled the unit; a refusal does not
abort the install), not merely that the source text contains a command.  The
enable/disable decision itself lives in wifi_apply_enabled.sh and is covered by
dist/wlan/usr/local/scripts/tests/test_apply_enabled_ntp.py.  The lab-gateway
checks assert the package payload, the source archive manifest and the source
tree agree that ``etc/networkd-dispatcher/routable.d/10-set-gateway.sh`` is no
longer shipped.
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


def _run_timesyncd_block(*, enabled: bool, restart_rc: int = 0) -> tuple[subprocess.CompletedProcess[str], list[str], list[str]]:
    with tempfile.TemporaryDirectory(prefix="timesyncd-start-") as tmp:
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
  is-enabled) [ "$ENABLED" = 1 ] ;;
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
                _block("timesyncd-start"),
                "printf 'completed\\n'",
            )
        )
        env = os.environ | {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "CALLS": str(calls),
            "LOGS": str(logs),
            "ENABLED": "1" if enabled else "0",
            "RESTART_RC": str(restart_rc),
        }
        result = subprocess.run(
            ["bash"], input=script, text=True, capture_output=True, env=env, timeout=5
        )
        return result, calls.read_text().splitlines(), logs.read_text().splitlines()


def test_enabled_timesyncd_is_restarted_to_read_the_installed_conf() -> None:
    result, calls, logs = _run_timesyncd_block(enabled=True)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "completed"
    assert calls == [
        f"is-enabled --quiet {TIMESYNCD_UNIT}",
        f"restart --no-block {TIMESYNCD_UNIT}",
    ]
    assert any(TIMESYNCD_UNIT in line and "restarted" in line for line in logs), logs


def test_disabled_timesyncd_is_left_alone() -> None:
    result, calls, logs = _run_timesyncd_block(enabled=False)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "completed"
    assert calls == [f"is-enabled --quiet {TIMESYNCD_UNIT}"], calls
    assert not any(line.startswith("restart") or line.startswith("enable") for line in calls)
    assert any(TIMESYNCD_UNIT in line and "left disabled" in line for line in logs), logs


@pytest.mark.parametrize("restart_rc", (1, 5))
def test_restart_failure_is_logged_and_does_not_abort_the_install(restart_rc: int) -> None:
    result, calls, logs = _run_timesyncd_block(enabled=True, restart_rc=restart_rc)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "completed"
    assert calls == [
        f"is-enabled --quiet {TIMESYNCD_UNIT}",
        f"restart --no-block {TIMESYNCD_UNIT}",
    ]
    assert any("local0.warn" in line and "restart failed" in line for line in logs), logs


def test_block_runs_after_the_gate_has_synchronised_the_merged_json() -> None:
    source = POSTINST.read_text()
    gate_call = "/usr/local/scripts/wifi_apply_enabled.sh 2>/dev/null || true"
    gate = source.index(gate_call)
    merge = source.index("json_merge /opt/wlan/config/wifi_init_conf.json /usr/local/etc/wifi_init_conf.json")
    block = source.index("  # >>> timesyncd-start\n")
    between = source[merge:block]

    assert gate < merge < block, "the restart block must follow json_merge and the first gate call"
    # On a first install the pre-merge gate call skips (no JSON yet), so the merged JSON must be
    # applied again on BOTH package flavours before the block decides: once inside the
    # NO_WBRIDGE=1 branch and once for the ordinary NO_WBRIDGE=0 flavour.
    assert between.count(gate_call) == 2, between
    assert 'if [ "$NO_WBRIDGE" -eq 0 ]; then\n    ' + gate_call in between
    assert "systemctl enable systemd-timesyncd" not in source, "enable belongs to wifi_apply_enabled.sh"


def test_lab_gateway_script_is_not_in_the_package_payload() -> None:
    payload = PAYLOAD_MANIFEST.read_text().splitlines()

    assert LAB_GATEWAY_SCRIPT not in payload
    assert not any(line.startswith("etc/networkd-dispatcher/") for line in payload), payload


def test_lab_gateway_script_is_not_in_the_source_archive() -> None:
    archive = SOURCE_ARCHIVE_MANIFEST.read_text().splitlines()

    assert f"dist/wlan/{LAB_GATEWAY_SCRIPT}" not in archive
    assert "scripts/qa/test_timesyncd_lab_gateway.py" in archive


def test_lab_gateway_script_is_not_in_the_source_tree() -> None:
    assert not (REPO / "dist/wlan" / LAB_GATEWAY_SCRIPT).exists()
    assert not (REPO / "dist/wlan/etc/networkd-dispatcher").exists()
