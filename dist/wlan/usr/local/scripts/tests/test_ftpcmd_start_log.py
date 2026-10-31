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
NATIVE_LOGGER_SOURCE = FTPCMD / "lib/ftpcmd_start_log.c"
COMMANDS = (
    "getifstate", "ifcdown", "ifcup", "rst", "wconnect", "wconnectraw",
    "wfreq", "wgetifstate", "whelp", "wifcdown", "wifcup", "wntp", "wpsk",
    "wrst", "wscan", "wssid", "wstatus", "wtime",
)


@pytest.fixture
def installed(tmp_path):
    root = tmp_path / "ftpcmd"
    shutil.copytree(FTPCMD, root)
    # Exercise the shipped raw CLI dispatch with its sibling engine, rather
    # than relying on a host-installed /usr/local/bin/wifi.
    scripts = FTPCMD.parents[1] / "usr/local/scripts"
    for name in ("wifi.sh", "wifi_connect_raw.sh"):
        shutil.copy2(scripts / name, root / name)
    raw = root / "bin/wconnectraw"
    source = raw.read_text()
    assert source.count("WIFI=/usr/local/bin/wifi\n") == 1
    raw.write_text(source.replace(
        "WIFI=/usr/local/bin/wifi\n", f"WIFI={root / 'wifi.sh'}\n", 1,
    ))
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
    native_transport = tmp_path / "native_transport.c"
    native_transport.write_text(r'''
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>

static void capture_syslog(int priority, const char *format, va_list arguments)
{
    const char *capture = getenv("FTPCMD_LOG_CAPTURE");
    const char *fail = getenv("FTPCMD_LOG_FAIL");
    char message[256];
    FILE *output;

    if (capture == NULL || (fail != NULL && fail[0] == '1')) {
        return;
    }
    if (vsnprintf(message, sizeof(message), format, arguments) < 0) {
        return;
    }
    output = fopen(capture, "a");
    if (output == NULL) {
        return;
    }
    (void)fprintf(output, "[%d, \"%s\"]\n", priority, message);
    (void)fclose(output);
}

void __wrap_syslog(int priority, const char *format, ...)
{
    va_list arguments;

    va_start(arguments, format);
    capture_syslog(priority, format, arguments);
    va_end(arguments);
}

void __wrap___syslog_chk(int priority, int flag, const char *format, ...)
{
    va_list arguments;

    (void)flag;
    va_start(arguments, format);
    capture_syslog(priority, format, arguments);
    va_end(arguments);
}
''')
    subprocess.run([
        "cc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
        "-Wl,--wrap=syslog", "-Wl,--wrap=__syslog_chk",
        str(NATIVE_LOGGER_SOURCE), str(native_transport),
        "-o", str(root / "lib/ftpcmd_start_log"),
    ], check=True, capture_output=True, text=True)
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


def test_shell_entry_logs_without_starting_python(installed):
    root, _, run = installed
    (root / "lib/ftpcmd_log.py").unlink()

    result, events = run("wscan", "invalid/interface")

    assert result.returncode == 2, result.stdout + result.stderr
    assert_start(events, "wscan")


def test_shell_entry_does_not_spawn_a_path_resolution_tool(installed):
    root, _, run = installed
    dirname = root.parent / "stubs/dirname"
    dirname.write_text("#!/bin/sh\nexit 91\n")
    dirname.chmod(0o755)

    result, events = run("wscan", "invalid/interface")

    assert result.returncode == 2, result.stdout + result.stderr
    assert_start(events, "wscan")


@pytest.mark.parametrize("timezone,offset", (
    ("UTC", "+00:00"),
    ("Asia/Seoul", "+09:00"),
    ("Asia/Kathmandu", "+05:45"),
    ("XXX-9:30:45", "+09:30:45"),
))
def test_native_start_event_uses_local_utc_offset(installed, timezone, offset):
    root, capture, _ = installed
    before = time.time()
    result = subprocess.run(
        [str(root / "lib/ftpcmd_start_log"), "wscan"],
        capture_output=True, text=True, timeout=5,
        env={**os.environ, "TZ": timezone, "FTPCMD_LOG_CAPTURE": str(capture)},
    )
    after = time.time()

    assert result.returncode == 0
    assert result.stdout == result.stderr == ""
    events = [json.loads(line) for line in capture.read_text().splitlines()]
    assert len(events) == 1
    priority, message = events[0]
    assert priority == 134
    match = re.fullmatch(
        r"ftpcmd_start command=wscan time=(.+" + re.escape(offset) + r")", message,
    )
    assert match, message
    timestamp = datetime.datetime.fromisoformat(match[1]).timestamp()
    assert before - 0.001 <= timestamp <= after


@pytest.mark.parametrize("arguments", (
    (),
    ("wscan", "unexpected"),
    ("",),
    ("bad/name",),
    ("PrivatePass123!",),
    ("명령",),
    ("a" * 65,),
))
def test_native_logger_rejects_invalid_command_quietly(installed, arguments):
    root, capture, _ = installed
    result = subprocess.run(
        [str(root / "lib/ftpcmd_start_log"), *arguments],
        capture_output=True, text=True, timeout=5,
        env={**os.environ, "FTPCMD_LOG_CAPTURE": str(capture)},
    )

    assert result.returncode == 2
    assert result.stdout == result.stderr == ""
    assert not capture.exists()


def test_native_logger_accepts_full_ascii_command_boundary(installed):
    root, capture, _ = installed
    command = "A_b-" + "9" * 60
    result = subprocess.run(
        [str(root / "lib/ftpcmd_start_log"), command],
        capture_output=True, text=True, timeout=5,
        env={**os.environ, "TZ": "UTC", "FTPCMD_LOG_CAPTURE": str(capture)},
    )

    assert result.returncode == 0
    assert result.stdout == result.stderr == ""
    events = [json.loads(line) for line in capture.read_text().splitlines()]
    assert len(events) == 1
    assert events[0][0] == 134
    assert events[0][1].startswith(f"ftpcmd_start command={command} time=")


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
