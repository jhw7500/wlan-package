"""Handler start events must be private, single, and invisible to FTP replies."""

import datetime
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest


FTPCMD = Path(__file__).resolve().parents[4] / "opt/ftpcmd"
COMMANDS = (
    "getifstate", "ifcdown", "ifcup", "rst", "wconnect", "wconnectraw",
    "wfreq", "wgetifstate", "whelp", "wifcdown", "wifcup", "wntp", "wpsk",
    "wrst", "wscan", "wssid", "wstatus", "wtime",
)


@pytest.fixture
def installed(tmp_path):
    root = tmp_path / "ftpcmd"
    shutil.copytree(FTPCMD, root)
    # The real handlers see a private station interface and inert device tools.
    net = tmp_path / "net"
    (net / "mlan0").mkdir(parents=True)
    (net / "mlan0/flags").write_text("0x1\n")
    for name in ("getifstate", "ifcup", "ifcdown"):
        path = root / "bin" / name
        path.write_text(path.read_text().replace("/sys/class/net/", str(net) + "/"))
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name in ("ip", "systemctl", "logger"):
        path = stubs / name
        path.write_text("#!/bin/sh\nexit 0\n")
        path.chmod(0o755)
    # Replace only the external syslog transport, including the shell helper's
    # Python process. All handler entry/validation/delegation remains real.
    (stubs / "ftpcmd_test_syslog.py").write_text('''import json
import os
LOG_ERR = 3
LOG_INFO = 6
LOG_LOCAL0 = 128
def syslog(priority, message):
    if os.environ.get("FTPCMD_LOG_FAIL") == "1":
        raise OSError("syslog unavailable")
    with open(os.environ["FTPCMD_LOG_CAPTURE"], "a") as output:
        output.write(json.dumps([priority, message]) + "\\n")
''')
    # syslog is built into some supported interpreters; replace it explicitly.
    (stubs / "sitecustomize.py").write_text(
        'import sys\nimport ftpcmd_test_syslog\nsys.modules["syslog"] = ftpcmd_test_syslog\n'
    )
    capture = tmp_path / "events.jsonl"
    env = {key: value for key, value in os.environ.items()
           if key not in ("FTPCMD_LOCAL_ADDR", "FTPCMD_START_LOGGED_FOR")}
    env.update({
        "PATH": f"{stubs}:{os.environ['PATH']}", "PYTHONPATH": str(stubs),
        "PYTHONDONTWRITEBYTECODE": "1", "TZ": "UTC",
        "FTPCMD_LOG_CAPTURE": str(capture), "WIFI_RUN_DIR": str(tmp_path / "run"),
    })

    def run(name, *args, log_fail=False):
        capture.unlink(missing_ok=True)
        result = subprocess.run(
            [str(root / "bin" / name), *args], capture_output=True, text=True,
            timeout=5, cwd="/", env={**env, "FTPCMD_LOG_FAIL": str(int(log_fail))},
        )
        events = [json.loads(line) for line in capture.read_text().splitlines()] if capture.exists() else []
        return result, [event for event in events if event[1].startswith("ftpcmd_start ")]

    return root, capture, run


def assert_start(events, name):
    assert len(events) == 1, events
    priority, message = events[0]
    assert priority == 134  # local0.info, routed to cantops/logger.log
    match = re.fullmatch(
        rf"ftpcmd_start command={name} time=([0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}T"
        r"[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}\+00:00)", message,
    )
    assert match, message
    return datetime.datetime.fromisoformat(match[1]).timestamp()


@pytest.mark.parametrize("name", COMMANDS)
def test_every_entry_logs_start_before_rejecting_arguments(installed, name):
    # A slash makes even legacy interface names invalid before any device I/O.
    _, _, run = installed
    before = time.time()
    result, events = run(name, "invalid/interface")
    after = time.time()
    assert result.returncode == 2, result.stdout + result.stderr
    timestamp = assert_start(events, name)
    assert before - 0.001 <= timestamp <= after


@pytest.mark.parametrize("name,args,first_line", (
    ("wgetifstate", ("0",), "mlan0 UP"),
    ("wifcup", ("0",), "IFCUP command successful."),
    ("wifcdown", ("0",), "IFCDOWN command successful."),
    ("wrst", (), "reboot requested"),
))
def test_alias_logs_original_command_once_when_delegating(installed, name, args, first_line):
    _, _, run = installed
    result, events = run(name, *args)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    assert result.stdout.splitlines()[0] == first_line
    assert_start(events, name)


def test_start_event_never_contains_secret_arguments(installed):
    _, _, run = installed
    result, events = run("wpsk", "invalid/interface", "--stage", "PrivatePass123")
    assert result.returncode == 2
    assert_start(events, "wpsk")
    assert "PrivatePass123" not in json.dumps(events)
    assert "--stage" not in json.dumps(events)


@pytest.mark.parametrize("name,args,first_line", (
    ("whelp", (), "Commands:"),
    ("getifstate", ("mlan0",), "mlan0 UP"),
    ("wifcup", ("0",), "IFCUP command successful."),
))
def test_log_failure_keeps_success_reply_and_exit_status(installed, name, args, first_line):
    _, _, run = installed
    result, events = run(name, *args, log_fail=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    assert result.stdout.startswith(first_line)
    assert not events


def test_time_sync_logs_before_invoking_clock_change(installed, tmp_path):
    root, capture, run = installed
    marker = tmp_path / "clock-invoked"
    clock = tmp_path / "fake-hwclock"
    clock.write_text(f'#!/bin/sh\n[ -s "$FTPCMD_LOG_CAPTURE" ] || exit 81\ntouch "{marker}"\n')
    clock.chmod(0o755)
    handler = root / "bin/wtime"
    handler.write_text(handler.read_text().replace(
        'FAKE_HWCLOCK = "/usr/local/scripts/fake-hwclock.sh"',
        f'FAKE_HWCLOCK = "{clock}"',
    ))
    result, events = run("wtime", "sync", "2026-10-29", "16:30:00.123")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    assert result.stdout.startswith("SUCCESS TIME=")
    assert marker.exists() and capture.exists()
    assert assert_start(events, "wtime") <= marker.stat().st_mtime
