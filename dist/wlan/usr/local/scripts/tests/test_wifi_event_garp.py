"""`wifi_event.sh` roam GARP behavior regression tests (#234).

The production script is executed end to end with helper paths redirected to a
private copy. PATH stubs model the external `iw`, `ip`, `jq`, and `arping`
boundaries; an owned process group contains background handlers without needing
user/mount namespaces. This proves local dispatch behavior only; it does not
prove that GARP removes the measured on-target roaming gap.
"""

import json
import os
import shlex
import signal
import subprocess
import textwrap
import time
from pathlib import Path

import pytest


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
WIFI_EVENT = SCRIPTS_DIR / "wifi_event.sh"


IW_STUB = r"""#!/bin/sh
if [ "${1:-}" = "event" ] && [ "${2:-}" = "-t" ]; then
    while IFS= read -r line; do
        printf '%s\n' "$line"
        sleep "${WIFI_EVENT_LINE_DELAY:-0}"
    done < "$WIFI_EVENT_EVENTS"
    # The real `iw event` is long lived.  Keep this producer alive briefly so
    # background handlers can cross their external-command boundary before the
    # test reaps the owned process group.
    sleep "${WIFI_EVENT_TAIL_DELAY:-0.1}"
    exit 0
fi
exit 0
"""

IP_STUB = r"""#!/bin/sh
printf 'ip %s\n' "$*" >> "$WIFI_EVENT_CALLS"
if [ "$*" = "-4 -o addr show $WIFI_EVENT_IFACE" ] && [ -s "$WIFI_EVENT_IPV4" ]; then
    ip=$(cat "$WIFI_EVENT_IPV4")
    printf '7: %s    inet %s/24 brd 192.0.2.255 scope global %s\n' \
        "$WIFI_EVENT_IFACE" "$ip" "$WIFI_EVENT_IFACE"
fi
"""

JQ_STUB = r"""#!/bin/sh
printf 'jq\n' >> "$WIFI_EVENT_CALLS"
exec /usr/bin/jq "$@"
"""

ARPING_STUB = r"""#!/bin/sh
printf 'arping:start %s\n' "$*" >> "$WIFI_EVENT_CALLS"
sleep "${WIFI_EVENT_ARPING_DELAY:-0}"
printf 'arping:done %s\n' "$*" >> "$WIFI_EVENT_CALLS"
exit "${WIFI_EVENT_ARPING_RC:-0}"
"""

LOGGER_STUB = r"""#!/bin/sh
printf 'logger %s\n' "$*" >> "$WIFI_EVENT_CALLS"
"""

TRAP_STUB = r"""#!/bin/sh
printf 'trap %s\n' "$*" >> "$WIFI_EVENT_CALLS"
"""

LINK_LIB_STUB = "wlan_bssid() { :; }\n"
FW_LIB_STUB = "wifi_fw_verify_mcs_connected() { return 0; }\n"

class WifiEventHarness:
    def __init__(self, tmp_path: Path):
        self.root = tmp_path
        self.bin = tmp_path / "bin"
        self.usr_local = tmp_path / "usr-local"
        self.helpers = self.usr_local / "scripts"
        self.script = tmp_path / "wifi_event.sh"
        self.calls = tmp_path / "calls.log"
        self.events = tmp_path / "events.log"
        self.ipv4 = tmp_path / "ipv4"
        self.config = tmp_path / "wifi_init_conf.json"
        self.bin.mkdir()
        self.usr_local.mkdir()
        self.helpers.mkdir()
        self.calls.write_text("")
        self.events.write_text("")
        self.ipv4.write_text("192.0.2.10\n")

        self._write_executable(self.bin / "iw", IW_STUB)
        self._write_executable(self.bin / "ip", IP_STUB)
        self._write_executable(self.bin / "jq", JQ_STUB)
        self._write_executable(self.bin / "arping", ARPING_STUB)
        self._write_executable(self.bin / "logger", LOGGER_STUB)
        self._write_executable(self.helpers / "wifi_snmp_trap.sh", TRAP_STUB)
        (self.helpers / "wlan_link_lib.sh").write_text(LINK_LIB_STUB)
        (self.helpers / "wifi_fw_config_lib.sh").write_text(FW_LIB_STUB)
        self._write_executable(
            self.script,
            WIFI_EVENT.read_text().replace(
                "/usr/local/scripts/", f"{shlex.quote(str(self.helpers))}/"
            ),
        )

    @staticmethod
    def _write_executable(path: Path, body: str) -> None:
        path.write_text(body)
        path.chmod(0o755)

    def run(
        self,
        *,
        config: object | str | None,
        events: list[str],
        ipv4: str | None = "192.0.2.10",
        arping_delay: float = 0,
        arping_rc: int = 0,
        line_delay: float = 0,
        tail_delay: float = 0.1,
    ) -> tuple[subprocess.CompletedProcess[str], float]:
        if isinstance(config, str):
            self.config.write_text(config)
        elif config is not None:
            self.config.write_text(json.dumps(config))
        elif self.config.exists():
            self.config.unlink()
        self.events.write_text("".join(f"{event}\n" for event in events))
        if ipv4 is None:
            self.ipv4.write_text("")
        else:
            self.ipv4.write_text(f"{ipv4}\n")

        env = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "WIFI_INIT_CONF_JSON": str(self.config),
            "WIFI_EVENT_ARPING_DELAY": str(arping_delay),
            "WIFI_EVENT_ARPING_RC": str(arping_rc),
            "WIFI_EVENT_CALLS": str(self.calls),
            "WIFI_EVENT_EVENTS": str(self.events),
            "WIFI_EVENT_IFACE": "mlan0",
            "WIFI_EVENT_IPV4": str(self.ipv4),
            "WIFI_EVENT_LINE_DELAY": str(line_delay),
            "WIFI_EVENT_TAIL_DELAY": str(tail_delay),
        }
        started = time.monotonic()
        proc = subprocess.Popen(
            ["bash", str(self.script), "mlan0"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=10)
            result = subprocess.CompletedProcess(
                proc.args, proc.returncode, stdout, stderr
            )
        finally:
            # The removed PID namespace used to reap these same descendants.
            # Only this subprocess's newly created process group is signalled.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
        return result, time.monotonic() - started

    def call_lines(self) -> list[str]:
        return self.calls.read_text().splitlines()

    def arping_starts(self) -> list[str]:
        return [line for line in self.call_lines() if line.startswith("arping:start ")]


@pytest.fixture
def harness(tmp_path: Path) -> WifiEventHarness:
    return WifiEventHarness(tmp_path)


@pytest.mark.parametrize(
    ("event", "expected_trap"),
    [
        ("1700000000.0: mlan0 connected to 02:00:00:00:00:01", "trap link up"),
        ("1700000000.0: mlan0 roamed to 02:00:00:00:00:02", None),
    ],
)
def test_enabled_sends_exact_garp_for_connected_and_roamed_paths(
    harness: WifiEventHarness, event: str, expected_trap: str | None
) -> None:
    result, _ = harness.run(
        config={"mlan0": {"roam_garp": {"enabled": True}}},
        events=[event],
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert harness.arping_starts() == [
        "arping:start -U -c 3 -I mlan0 192.0.2.10"
    ]
    traps = [line for line in harness.call_lines() if line.startswith("trap ")]
    assert traps == ([expected_trap] if expected_trap else [])


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"mlan0": {"roam_garp": {"enabled": False}}},
        {"mlan0": {"roam_garp": {"enabled": None}}},
        "{ malformed json",
    ],
    ids=["missing-default", "false", "null", "malformed"],
)
def test_non_true_configuration_never_sends_garp(
    harness: WifiEventHarness, config: object | str
) -> None:
    result, _ = harness.run(
        config=config,
        events=["1700000000.0: mlan0 connected to 02:00:00:00:00:01"],
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert harness.arping_starts() == []


def test_enabled_without_interface_ipv4_is_pure_bridge_noop(
    harness: WifiEventHarness,
) -> None:
    result, _ = harness.run(
        config={"mlan0": {"roam_garp": {"enabled": True}}},
        events=["1700000000.0: mlan0 roamed to 02:00:00:00:00:02"],
        ipv4=None,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert harness.arping_starts() == []


def test_other_interface_event_is_ignored(harness: WifiEventHarness) -> None:
    result, _ = harness.run(
        config={"mlan0": {"roam_garp": {"enabled": True}}},
        events=["1700000000.0: mlan1 connected to 02:00:00:00:00:03"],
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert harness.arping_starts() == []
    assert not any(line.startswith("trap ") for line in harness.call_lines())


def test_slow_arping_does_not_block_following_event(harness: WifiEventHarness) -> None:
    result, elapsed = harness.run(
        config={"mlan0": {"roam_garp": {"enabled": True}}},
        events=[
            "1700000000.0: mlan0 connected to 02:00:00:00:00:01",
            "1700000000.1: mlan0 roamed to 02:00:00:00:00:02",
        ],
        arping_delay=1,
        line_delay=0.05,
        tail_delay=1.2,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    lines = harness.call_lines()
    starts = [i for i, line in enumerate(lines) if line.startswith("arping:start ")]
    done = [i for i, line in enumerate(lines) if line.startswith("arping:done ")]
    assert len(starts) == 2
    assert len(done) == 2
    assert starts[1] < done[0], lines
    assert elapsed < 1.8


def test_failed_arping_does_not_stop_event_loop(harness: WifiEventHarness) -> None:
    result, _ = harness.run(
        config={"mlan0": {"roam_garp": {"enabled": True}}},
        events=[
            "1700000000.0: mlan0 connected to 02:00:00:00:00:01",
            "1700000000.1: mlan0 roamed to 02:00:00:00:00:02",
        ],
        arping_rc=1,
        line_delay=0.05,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert harness.arping_starts() == [
        "arping:start -U -c 3 -I mlan0 192.0.2.10",
        "arping:start -U -c 3 -I mlan0 192.0.2.10",
    ]
