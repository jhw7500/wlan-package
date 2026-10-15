"""MAC-contract failures must not race the independent health checker."""

import os
import signal
import subprocess
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]


def executable(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o700)


def prepare_checker(
    tmp_path: Path, *, iface: str, scenario: str = "initial-block"
) -> tuple[subprocess.Popen, Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    events = tmp_path / "events"
    calls = tmp_path / "calls"
    sleep_count = tmp_path / "sleep-count"
    marker = tmp_path / "run/mac-contract-blocked"
    parent_status = tmp_path / "parent-status"
    marker.parent.mkdir()
    if scenario == "initial-block":
        marker.touch()
    if scenario == "parent-78-start":
        parent_status.write_text("78\n", encoding="utf-8")

    executable(fake_bin / "logger", 'printf "logger %s\\n" "$*" >> "$EVENTS"\n')
    executable(fake_bin / "print", 'printf "print %s\\n" "$*" >> "$EVENTS"\n')
    executable(fake_bin / "lsmod", "printf 'moal 1 0\\n'\n")
    executable(fake_bin / "dmesg", "printf 'private diagnostic\\n'\n")
    executable(fake_bin / "sync", "exit 0\n")
    executable(fake_bin / "iw", 'printf "iw %s\\n" "$*" >> "$CALLS"\nexit 1\n')
    executable(fake_bin / "wpa_cli", 'printf "wpa_cli %s\\n" "$*" >> "$CALLS"\nexit 1\n')
    executable(
        fake_bin / "systemctl",
        """printf "systemctl %s\\n" "$*" >> "$CALLS"
if [ "$*" = "show wifi_init.service -p ExecMainStatus --value" ]; then
    [ ! -f "$PARENT_STATUS" ] || cat "$PARENT_STATUS"
    exit 0
fi
exit 1
""",
    )
    executable(fake_bin / "wifi", 'printf "wifi %s\\n" "$*" >> "$EVENTS"\nexit 0\n')
    executable(
        fake_bin / "sleep",
        """n=0
[ ! -f "$SLEEP_COUNT" ] || n=$(cat "$SLEEP_COUNT")
n=$((n + 1))
printf '%s\\n' "$n" > "$SLEEP_COUNT"
if [ "$TEST_IFACE" = eth0 ]; then
    [ "$n" -lt 2 ] || kill -TERM "$PPID"
    exit 0
fi
if [ "$TEST_SCENARIO" = mid-loop ] && [ "$n" -eq 6 ]; then
    printf 'marker-created\\n' >> "$EVENTS"
    touch "$MAC_BLOCK_MARKER"
fi
if [ "$TEST_SCENARIO" = parent-78-mid-loop ] && [ "$n" -eq 6 ]; then
    printf 'parent-status-78\n' >> "$EVENTS"
    printf '78\n' > "$PARENT_STATUS"
fi
if [ "$TEST_SCENARIO" = mid-loop ] && [ "$n" -ge 8 ]; then
    kill -TERM "$PPID"
fi
if [ "$TEST_SCENARIO" = parent-78-mid-loop ] && [ "$n" -ge 8 ]; then
    kill -TERM "$PPID"
fi
if [ "$TEST_SCENARIO" = parent-78-start ] && [ "$n" -ge 8 ]; then
    kill -TERM "$PPID"
fi
if [ "$n" -eq 7 ]; then
    if [ "$TEST_SCENARIO" = initial-block ]; then
        printf 'marker-removed\\n' >> "$EVENTS"
        rm -f "$MAC_BLOCK_MARKER"
    fi
fi
if [ -f "$MAC_BLOCK_MARKER" ]; then
    printf 'blocked-tick\\n' >> "$EVENTS"
else
    printf 'unblocked-tick\\n' >> "$EVENTS"
fi
if [ -f "$POLICY_MARK" ]; then
    kill -TERM "$PPID"
fi
exit 0
""",
    )

    policy = tmp_path / "policy"
    executable(
        policy,
        'printf "policy %s\\n" "$*" >> "$EVENTS"\nprintf "%s\\n" "$*" > "$POLICY_MARK"\nexit 42\n',
    )
    snapshot = tmp_path / "snapshot"
    executable(snapshot, "exit 0\n")
    logger_lib = tmp_path / "logger-lib.sh"
    logger_lib.write_text(
        "logger_run_bounded() { shift; \"$@\"; }\n"
        "logger_read_bounded() { return 1; }\n",
        encoding="utf-8",
    )

    sysfs = tmp_path / "net"
    if iface == "eth0":
        (sysfs / "eth0").mkdir(parents=True)
        (sysfs / "eth0/operstate").write_text("up\n", encoding="utf-8")

    source = (SCRIPTS / "wifi_checker.sh").read_text(encoding="utf-8")
    replacements = {
        "/usr/local/etc/wifi_init_conf.json": str(tmp_path / "missing.json"),
        "/usr/local/scripts/wlan_reboot_policy.sh": str(policy),
        "/usr/local/scripts/journald_snapshot.sh": str(snapshot),
        "/lib/firmware/": str(tmp_path / "firmware") + "/",
        "/sys/class/net/": str(sysfs) + "/",
        "/var/log/cantops": str(tmp_path / "logs"),
        "/run/wifi/": str(tmp_path / "run") + "/",
    }
    for old, new in replacements.items():
        source = source.replace(old, new)
    for boundary in replacements:
        assert boundary not in source, f"unsafe test path: {boundary}"
    runnable = tmp_path / "wifi_checker.sh"
    runnable.write_text(source, encoding="utf-8")

    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "EVENTS": str(events),
        "CALLS": str(calls),
        "SLEEP_COUNT": str(sleep_count),
        "MAC_BLOCK_MARKER": str(marker),
        "PARENT_STATUS": str(parent_status),
        "POLICY_MARK": str(tmp_path / "policy.mark"),
        "TEST_IFACE": iface,
        "TEST_SCENARIO": scenario,
        "WIFI_LOGGER_COMMAND_LIB": str(logger_lib),
    }
    proc = subprocess.Popen(
        ["/bin/bash", str(runnable), iface],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    return proc, events, Path(env["POLICY_MARK"])


def finish(proc: subprocess.Popen, timeout: int = 8) -> tuple[str, str]:
    try:
        return proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        stdout, stderr = proc.communicate(timeout=3)
        raise AssertionError(f"checker did not reach bounded stop\n{stdout}\n{stderr}")


def test_wireless_checker_defers_recovery_until_mac_contract_unblocks(tmp_path):
    proc, events_path, policy_mark = prepare_checker(tmp_path, iface="mlan0")
    stdout, stderr = finish(proc)
    assert proc.returncode == 0, (stdout, stderr)

    events = events_path.read_text(encoding="utf-8").splitlines()
    removed_at = events.index("marker-removed")
    policy_at = next(i for i, line in enumerate(events) if line.startswith("policy "))
    assert removed_at < policy_at, events
    assert not any(line.startswith(("policy ", "wifi ")) for line in events[:removed_at])
    assert events[removed_at:policy_at].count("unblocked-tick") >= 6, events
    assert policy_mark.exists(), "wireless monitoring did not resume after marker removal"


def test_eth0_monitoring_ignores_wireless_mac_contract_block(tmp_path):
    proc, events_path, policy_mark = prepare_checker(tmp_path, iface="eth0")
    stdout, stderr = finish(proc)
    assert proc.returncode == 0, (stdout, stderr)

    events = events_path.read_text(encoding="utf-8")
    assert "[eth0] link change down -> up" in events
    assert not policy_mark.exists()


def test_marker_created_after_fatal_detection_still_blocks_policy(tmp_path):
    proc, events_path, policy_mark = prepare_checker(
        tmp_path, iface="mlan0", scenario="mid-loop"
    )
    stdout, stderr = finish(proc)
    assert proc.returncode == 0, (stdout, stderr)

    events = events_path.read_text(encoding="utf-8").splitlines()
    created_at = events.index("marker-created")
    assert "blocked-tick" in events[created_at:]
    assert not any(line.startswith(("policy ", "wifi ")) for line in events[created_at:])
    assert not policy_mark.exists()


def test_parent_status_78_at_start_suppresses_wireless_recovery(tmp_path):
    proc, events_path, policy_mark = prepare_checker(
        tmp_path, iface="mlan0", scenario="parent-78-start"
    )
    stdout, stderr = finish(proc)
    assert proc.returncode == 0, (stdout, stderr)

    events = events_path.read_text(encoding="utf-8").splitlines()
    assert not any(line.startswith(("policy ", "wifi ")) for line in events), events
    assert not policy_mark.exists()


def test_parent_status_becoming_78_before_policy_suppresses_recovery(tmp_path):
    proc, events_path, policy_mark = prepare_checker(
        tmp_path, iface="mlan0", scenario="parent-78-mid-loop"
    )
    stdout, stderr = finish(proc)
    assert proc.returncode == 0, (stdout, stderr)

    events = events_path.read_text(encoding="utf-8").splitlines()
    changed_at = events.index("parent-status-78")
    assert not any(line.startswith(("policy ", "wifi ")) for line in events[changed_at:]), events
    assert not policy_mark.exists()
