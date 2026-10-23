"""OPCd upgrade handoff behavior from the real postinst blocks.

The package replaces the executable and unit while an old opcd process may still be
running.  These tests execute the exact marked postinst fragments with a stateful
``systemctl`` boundary double; they assert the process state visible to the package,
not merely that source text contains a command.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile

import pytest


REPO = Path(__file__).resolve().parents[2]
POSTINST = REPO / "dist/wlan/DEBIAN/postinst"


def _block(name: str) -> str:
    source = POSTINST.read_text()
    start = f"  # >>> {name}\n"
    end = f"  # <<< {name}\n"
    assert start in source and end in source, f"postinst block {name!r} is missing"
    return source.split(start, 1)[1].split(end, 1)[0]


def _run(*, initially_active: bool, enabled: bool, no_wbridge: int = 0,
         restart_rc: int = 0, post_restart_active: bool = True) -> tuple[subprocess.CompletedProcess[str], list[str], str, str]:
    with tempfile.TemporaryDirectory(prefix="opcd-upgrade-") as tmp:
        root = Path(tmp)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        state = root / "state"
        pid = root / "pid"
        calls = root / "calls"
        state.write_text("active\n" if initially_active else "inactive\n")
        pid.write_text("100\n" if initially_active else "0\n")
        calls.write_text("")

        fake_systemctl = bin_dir / "systemctl"
        fake_systemctl.write_text(
            """#!/bin/sh
printf '%s\\n' "$*" >> "$CALLS"
case "$1" in
  is-active)
    [ "$(cat "$STATE")" = active ]
    ;;
  is-enabled)
    [ "$ENABLED" = 1 ]
    ;;
  try-restart)
    [ "$2" = opcd.service ] || exit 64
    [ "$(cat "$STATE")" = active ] || exit 0
    [ "$RESTART_RC" = 0 ] || exit "$RESTART_RC"
    if [ "$POST_RESTART_ACTIVE" = 1 ]; then
      printf 'active\\n' > "$STATE"
      printf '200\\n' > "$PID"
    else
      printf 'inactive\\n' > "$STATE"
      printf '0\\n' > "$PID"
    fi
    ;;
  *) exit 65 ;;
esac
"""
        )
        fake_systemctl.chmod(0o755)

        script = "\n".join(
            (
                "#!/bin/bash",
                "tag=postinst-test",
                "KEY=PKG",
                "logger() { :; }",
                _block("opcd-upgrade-state-capture"),
                "# Simulate wifi_apply_enabled disabling an active OPCd.",
                "if [ \"${SIMULATE_GATE_OFF:-0}\" = 1 ]; then printf 'inactive\\n' > \"$STATE\"; printf '0\\n' > \"$PID\"; fi",
                _block("opcd-upgrade-restart"),
                "printf 'completed\\n'",
            )
        )
        env = os.environ | {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "STATE": str(state),
            "PID": str(pid),
            "CALLS": str(calls),
            "ENABLED": "1" if enabled else "0",
            "RESTART_RC": str(restart_rc),
            "POST_RESTART_ACTIVE": "1" if post_restart_active else "0",
            "NO_WBRIDGE": str(no_wbridge),
            "SIMULATE_GATE_OFF": "1" if initially_active and not enabled else "0",
        }
        result = subprocess.run(
            ["bash"], input=script, text=True, capture_output=True, env=env, timeout=5
        )
        return result, calls.read_text().splitlines(), state.read_text().strip(), pid.read_text().strip()


def test_active_enabled_opcd_is_replaced_and_ready() -> None:
    result, calls, state, pid = _run(initially_active=True, enabled=True)

    assert result.returncode == 0, result.stderr
    assert calls == [
        "is-active --quiet opcd.service",
        "is-enabled --quiet opcd.service",
        "is-active --quiet opcd.service",
        "try-restart opcd.service",
        "is-active --quiet opcd.service",
    ]
    assert state == "active"
    assert pid == "200"


@pytest.mark.parametrize(
    ("initially_active", "enabled"),
    ((False, True), (True, False)),
    ids=("previously-inactive", "configuration-or-mfg-disabled"),
)
def test_restart_does_not_start_or_replace_a_gated_opcd(
    initially_active: bool, enabled: bool
) -> None:
    result, calls, state, pid = _run(
        initially_active=initially_active, enabled=enabled
    )

    assert result.returncode == 0, result.stderr
    assert not any(call.startswith("try-restart ") for call in calls)
    if initially_active and enabled:
        assert (state, pid) == ("active", "100")
    else:
        assert (state, pid) == ("inactive", "0")


def test_active_enabled_opcd_is_replaced_in_no_bridge_package() -> None:
    result, calls, state, pid = _run(
        initially_active=True, enabled=True, no_wbridge=1
    )

    assert result.returncode == 0, result.stderr
    assert "try-restart opcd.service" in calls
    assert (state, pid) == ("active", "200")


def test_restart_failure_fails_package_configuration() -> None:
    result, calls, state, pid = _run(
        initially_active=True, enabled=True, restart_rc=23
    )

    assert result.returncode == 23
    assert calls[-1] == "try-restart opcd.service"
    assert (state, pid) == ("active", "100")


def test_not_active_after_successful_restart_fails_configuration() -> None:
    result, calls, state, pid = _run(
        initially_active=True, enabled=True, post_restart_active=False
    )

    assert result.returncode != 0
    assert calls[-1] == "is-active --quiet opcd.service"
    assert (state, pid) == ("inactive", "0")
