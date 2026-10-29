"""Time commands validate before mutation and keep FTP replies on one ASCII line."""

import os
import re
import subprocess
import types
from pathlib import Path

import pytest


HANDLERS = Path(__file__).resolve().parents[4] / "opt/ftpcmd/bin"


def load_handler(name):
    path = HANDLERS / name
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(path.read_text(), str(path), "exec"), module.__dict__)
    return module


@pytest.fixture
def commands(monkeypatch):
    class Commands:
        calls = None
        state = "active"
        enabled = "enabled"
        synchronized = "yes"
        server = "192.168.0.3"
        fail = None

        def run(self, argv, **kwargs):
            self.calls.append((argv, kwargs))
            if self.fail:
                return self.fail(argv, **kwargs)
            assert kwargs["capture_output"] and kwargs["text"]
            assert 0 < kwargs["timeout"] < 30
            if argv[0].endswith("systemctl") and "show" in argv:
                output = f"ActiveState={self.state}\nUnitFileState={self.enabled}\n"
            elif "show-timesync" in argv:
                output = f"ServerName=ntp.example.com\nServerAddress={self.server}\n"
            elif argv[0].endswith("timedatectl"):
                assert "show" in argv
                output = f"NTPSynchronized={self.synchronized}\n"
            else:
                # A child may be noisy; its stdout must not become the FTP result.
                output = "child output\nsecond child line\n"
            return subprocess.CompletedProcess(argv, 0, output, "")

    result = Commands()
    result.calls = []
    monkeypatch.setattr(subprocess, "run", result.run)
    return result


@pytest.fixture
def wntp(tmp_path, monkeypatch, commands):
    module = load_handler("wntp")
    config = tmp_path / "timesyncd.conf"
    config.write_text("[Time]\nNTP=192.168.0.3\nFallbackNTP=time.example.com\n")
    config.chmod(0o640)
    monkeypatch.setattr(module, "CONF", config)
    monkeypatch.setattr(module.syslog, "syslog", lambda *args: None)
    return module


@pytest.fixture
def wtime(monkeypatch, commands):
    module = load_handler("wtime")
    monkeypatch.setattr(module.syslog, "syslog", lambda *args: None)
    return module


def reply(capsys):
    captured = capsys.readouterr()
    assert captured.err == "", "the dispatcher can merge stderr into the success reply"
    output = captured.out
    assert output.endswith("\n")
    assert len(output.splitlines()) == 1
    text = output[:-1]
    assert all(0x20 <= ord(char) <= 0x7E for char in text)
    assert len(text.encode("ascii")) <= 256
    return text


def test_ntp_query_distinguishes_stored_and_current_server(wntp, commands, capsys):
    commands.server = "192.168.0.9"
    assert wntp.main([]) == 0
    assert reply(capsys) == (
        "SERVICE=active ENABLED=enabled SYNC=yes CONFIG=192.168.0.3 SERVER=192.168.0.9"
    )
    assert all("show" in args or "show-timesync" in args for args, _ in commands.calls)


def test_ntp_inactive_query_does_not_activate_timesyncd(wntp, commands, capsys):
    commands.state = "inactive"
    commands.enabled = "disabled"
    commands.synchronized = "no"
    assert wntp.main([]) == 0
    text = reply(capsys)
    assert "SERVICE=inactive ENABLED=disabled SYNC=no" in text
    assert "SERVER=none" in text
    assert not any("show-timesync" in args for args, _ in commands.calls)


def test_ntp_unavailable_queries_are_explicit_unknown(wntp, commands, capsys):
    def failed(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, "", "unavailable")

    commands.fail = failed
    assert wntp.main([]) == 0
    text = reply(capsys)
    assert "SERVICE=unknown ENABLED=unknown SYNC=unknown" in text
    assert "SERVER=unknown" in text
    assert not any("show-timesync" in args for args, _ in commands.calls)


def test_ntp_long_config_is_marked_truncated_within_reply_limit(wntp, capsys):
    wntp.CONF.write_text("[Time]\nNTP=" + " ".join(f"server{n}.example.com" for n in range(30)) + "\n")
    assert wntp.main([]) == 0
    assert "... SERVER=" in reply(capsys)


@pytest.mark.parametrize("original", (
    "[Time]\nNTP=192.168.0.3\nFallbackNTP=time.example.com\nPollIntervalMinSec=32\n",
    "# device config\n[Time]\n NTP = 192.168.0.3\nNTP=\nNTP=192.168.0.4\n[Other]\nNTP=keep\n",
    "[Time]\nNTP=192.168.0.3 \\\n# continued server list\n 192.168.0.4\nFallbackNTP=time.example.com\n",
    "# no time section\n[Other]\nKey=value",
    "[Time]\nFallbackNTP=time.example.com",
))
@pytest.mark.parametrize("address", ("192.168.0.8", "2001:db8::123"))
def test_ntp_ip_atomically_replaces_device_list_without_apply(
    wntp, commands, capsys, original, address,
):
    wntp.CONF.write_text(original)
    before = wntp.CONF.stat()
    assert wntp.main(["ip", address]) == 0
    assert reply(capsys) == f"SUCCESS NTP={address}; run wntp apply"
    assert not commands.calls
    assert wntp.configured_servers(wntp.CONF.read_text()) == [address]
    assert "192.168.0.3" not in wntp.CONF.read_text()
    assert "192.168.0.4" not in wntp.CONF.read_text()
    for preserved in ("FallbackNTP=time.example.com", "PollIntervalMinSec=32", "NTP=keep", "Key=value"):
        if preserved in original:
            assert preserved in wntp.CONF.read_text()
    after = wntp.CONF.stat()
    assert (after.st_uid, after.st_gid, after.st_mode & 0o777) == (
        before.st_uid, before.st_gid, 0o640,
    )
    assert after.st_ino != before.st_ino
    assert sorted(path.name for path in wntp.CONF.parent.iterdir()) == ["timesyncd.conf"]


def test_ntp_ip_creates_missing_device_config(wntp, capsys):
    wntp.CONF.unlink()
    assert wntp.main(["ip", "192.168.0.8"]) == 0
    assert "SUCCESS" in reply(capsys)
    assert wntp.configured_servers(wntp.CONF.read_text()) == ["192.168.0.8"]
    assert wntp.CONF.stat().st_mode & 0o777 == 0o644


def test_ntp_write_failure_keeps_original_and_cleans_temp(wntp, monkeypatch, capsys):
    original = wntp.CONF.read_bytes()

    def fail_replace(*args):
        raise OSError("replace refused")

    monkeypatch.setattr(wntp.os, "replace", fail_replace)
    assert wntp.main(["ip", "192.168.0.8"]) == 1
    assert capsys.readouterr().out == ""
    assert wntp.CONF.read_bytes() == original
    assert sorted(path.name for path in wntp.CONF.parent.iterdir()) == ["timesyncd.conf"]


def test_ntp_refuses_symlink_config_without_replacing_it(wntp, capsys):
    target = wntp.CONF.with_name("target")
    target.write_text("[Time]\nNTP=192.168.0.3\n")
    wntp.CONF.unlink()
    wntp.CONF.symlink_to(target)
    assert wntp.main(["ip", "192.168.0.8"]) == 1
    assert capsys.readouterr().out == ""
    assert wntp.CONF.is_symlink()
    assert target.read_text() == "[Time]\nNTP=192.168.0.3\n"


def test_ntp_apply_only_restarts_existing_unit(wntp, commands, capsys):
    original = wntp.CONF.read_bytes()
    assert wntp.main(["apply"]) == 0
    assert "restarted systemd-timesyncd; synchronization not confirmed" in reply(capsys)
    assert len(commands.calls) == 1
    argv, _ = commands.calls[0]
    assert argv[0].endswith("systemctl")
    assert "restart" in argv and argv[-1] == "systemd-timesyncd.service"
    assert "enable" not in argv and "set-ntp" not in argv
    assert wntp.CONF.read_bytes() == original


@pytest.mark.parametrize("args", ([], ["apply"]))
def test_ntp_uses_public_system_bus_in_ftp_pid_namespace(wntp, commands, capsys, args):
    assert wntp.main(args) == 0
    reply(capsys)
    systemctl_calls = [(argv, kwargs) for argv, kwargs in commands.calls
                       if argv[0].endswith("systemctl")]
    assert systemctl_calls
    for _, kwargs in systemctl_calls:
        assert kwargs.get("env", {}).get("SYSTEMCTL_FORCE_BUS") == "1"


@pytest.mark.parametrize("args", (
    ["ip"], ["ip", "ntp.example.com"], ["ip", "192.168.0.8", "extra"],
    ["ip", "192.168.0.999"], ["ip", "192.168.0.8/24"], ["ip", "0.0.0.0"],
    ["ip", "255.255.255.255"], ["ip", "224.0.0.1"], ["ip", "::"],
    ["ip", "ff02::101"], ["ip", "fe80::1%eth0"], ["apply", "extra"],
    ["restart"], ["0"],
))
def test_ntp_invalid_arguments_have_no_side_effects(wntp, commands, capsys, args):
    original = wntp.CONF.read_bytes()
    assert wntp.main(args) == 2
    assert capsys.readouterr().out == ""
    assert not commands.calls
    assert wntp.CONF.read_bytes() == original


def test_time_query_has_milliseconds_and_offset(wtime, commands, capsys):
    assert wtime.main([]) == 0
    assert re.fullmatch(r"TIME=[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3} OFFSET=[+-][0-9]{4}", reply(capsys))
    assert not commands.calls


@pytest.mark.parametrize("date,time", (
    ("2026-10-29", "16:30:00"), ("2026-10-29", "16:30:00.123"),
    ("2026-10-29", "16:30:00.001"), ("2026-10-29", "16:30:00.999"),
    ("2028-02-29", "00:00:00.1"),
))
def test_time_sync_preserves_fractional_input_and_existing_ntp_state(
    wtime, commands, capsys, date, time,
):
    assert wtime.main(["sync", date, time]) == 0
    assert reply(capsys).startswith("SUCCESS TIME=")
    assert len(commands.calls) == 1
    assert commands.calls[0][0] == [
        "/usr/local/scripts/fake-hwclock.sh", "set", f"{date} {time}",
    ]


@pytest.mark.parametrize("args", (
    ["sync"], ["sync", "2026-10-29"], ["sync", "2026-10-29", "12:00:00", "UTC"],
    ["sync", "2026-02-29", "12:00:00"], ["sync", "2026-10-29", "24:00:00"],
    ["sync", "2026-10-29", "12:00:60"], ["sync", "2026-1-2", "12:00:00"],
    ["sync", "2026-10-29", "12:00:00.1234"], ["sync", "2026-10-29", "12:00:00."],
    ["sync", "2026-10-29", "12:00:00Z"], ["sync", "now", "+1hour"],
    ["sync", "2026-10-29", "12:00:00;reboot"], ["set"], ["0"],
))
def test_time_invalid_arguments_never_set_clock(wtime, commands, capsys, args):
    assert wtime.main(args) == 2
    assert capsys.readouterr().out == ""
    assert not commands.calls


@pytest.mark.parametrize("name,args", (("wntp", ["apply"]), ("wtime", ["sync", "2026-10-29", "12:00:00.123"])))
@pytest.mark.parametrize("failure,expected", (("exit", 1), ("missing", 1), ("timeout", 124)))
def test_mutation_failure_is_not_reported_as_success(
    name, args, failure, expected, commands, capsys, monkeypatch,
):
    module = load_handler(name)
    monkeypatch.setattr(module.syslog, "syslog", lambda *args: None)

    def fail(argv, **kwargs):
        if failure == "missing":
            raise FileNotFoundError(argv[0])
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return subprocess.CompletedProcess(argv, 1, "partial output\n", "operation failed")

    commands.fail = fail
    assert module.main(args) == expected
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("name", ("wntp", "wtime"))
def test_time_handlers_are_executable(name):
    assert os.access(HANDLERS / name, os.X_OK)
