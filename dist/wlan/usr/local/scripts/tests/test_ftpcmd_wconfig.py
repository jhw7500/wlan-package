"""wconfig validates the whole settings file before any change and keeps FTP replies on one line (#464)."""

import json
import os
import subprocess
import types
from pathlib import Path

import pytest


HANDLERS = Path(__file__).resolve().parents[4] / "opt/ftpcmd/bin"
NTP_UNIT = "systemd-timesyncd.service"
SNMP_UNIT = "snmpd.service"


def load_handler(name):
    path = HANDLERS / name
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(path.read_text(), str(path), "exec"), module.__dict__)
    return module


class System:
    """Fake systemd: enable state follows the JSON once wifi_apply_enabled.sh has run."""

    def __init__(self, json_path):
        self.json_path = json_path
        self.calls = []
        self.apply_rc = 0
        self.queue_rc = 0
        self.enabled = {NTP_UNIT: True, SNMP_UNIT: False}

    def run(self, argv, **kwargs):
        argv = list(argv)
        self.calls.append((argv, kwargs))
        assert 0 < kwargs["timeout"] < 30
        if argv[0].endswith("wifi_apply_enabled.sh"):
            if self.apply_rc == 0:
                data = json.loads(self.json_path.read_text())
                self.enabled[NTP_UNIT] = data["global"]["ntp"]["enabled"] is True
                self.enabled[SNMP_UNIT] = data["snmp"]["enabled"] is True
            return subprocess.CompletedProcess(argv, self.apply_rc, "child noise\n", "")
        if "is-enabled" in argv:
            return subprocess.CompletedProcess(argv, 0 if self.enabled[argv[-1]] else 1, "", "")
        return subprocess.CompletedProcess(argv, self.queue_rc, "", "")

    def apply_calls(self):
        return [kwargs for argv, kwargs in self.calls if argv[0].endswith("wifi_apply_enabled.sh")]

    def queued(self):
        return [(argv[-2], argv[-1]) for argv, _ in self.calls if "--no-block" in argv]


@pytest.fixture
def env(tmp_path, monkeypatch):
    module = load_handler("wconfig")
    home = tmp_path / "home"
    home.mkdir()
    conf = tmp_path / "wifi_init_conf.json"
    conf.write_text(json.dumps({
        "global": {"ntp": {"enabled": True}, "country": "KR"},
        "snmp": {"enabled": False, "trap": {"enabled": False, "dest": "", "community": "public"}},
    }, indent=2) + "\n")
    timesyncd = tmp_path / "timesyncd.conf"
    timesyncd.write_text(
        "# vendor header\n[Time]\nNTP=192.168.0.3\n"
        "FallbackNTP=time1.google.com time2.google.com\nPollIntervalMinSec=32\n"
    )
    system = System(conf)
    monkeypatch.setattr(subprocess, "run", system.run)
    monkeypatch.setattr(module, "HOME", home)
    monkeypatch.setattr(module, "JSON_PATH", conf)
    monkeypatch.setattr(module, "TIMESYNCD_CONF", timesyncd)
    monkeypatch.setattr(module.syslog, "syslog", lambda *args: None)
    return types.SimpleNamespace(module=module, home=home, conf=conf, timesyncd=timesyncd, system=system)


def write_settings(env, text):
    (env.home / "wconfig.txt").write_text(text)


def result_lines(env):
    return (env.home / "wconfig.result").read_text().splitlines()


def one_line(capsys):
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines, "a successful command must print the FTP reply line"
    first = lines[0]
    assert len(first.encode()) <= 256 and first.isascii() and first.isprintable()
    return first


def test_apply_rejects_an_invalid_value_and_changes_nothing(env):
    write_settings(env, "SNMP_FUNC=yes\nNTP_SERVER=192.0.2.10\n")
    conf_before, ntp_before = env.conf.read_bytes(), env.timesyncd.read_bytes()

    assert env.module.main(["apply"]) == 3

    assert env.conf.read_bytes() == conf_before
    assert env.timesyncd.read_bytes() == ntp_before
    assert env.system.calls == []
    assert any(line.startswith("SNMP_FUNC invalid") for line in result_lines(env))


@pytest.mark.parametrize("line", ["NTP_SERVER=999.1.1.1", "NTP_SERVER=224.0.0.1", "SNMP_TRAP_IPADDR=host.example",
                                  "NTP_FUNC=on", "garbage-without-equals"])
def test_each_bad_line_alone_blocks_the_apply(env, line):
    write_settings(env, f"SNMP_FUNC=enabled\n{line}\n")
    conf_before = env.conf.read_bytes()

    assert env.module.main(["apply"]) == 3

    assert env.conf.read_bytes() == conf_before
    assert env.system.calls == []


def test_apply_writes_each_store_and_queues_one_action_per_service(env, capsys):
    write_settings(env, "NTP_FUNC=disabled\nNTP_SERVER=192.0.2.10\nSNMP_FUNC=enabled\nSNMP_TRAP_IPADDR=192.0.2.20\n")

    assert env.module.main(["apply"]) == 0

    data = json.loads(env.conf.read_text())
    assert data["global"]["ntp"]["enabled"] is False
    assert data["snmp"]["enabled"] is True
    assert data["snmp"]["trap"]["dest"] == "192.0.2.20"
    assert data["global"]["country"] == "KR"
    text = env.timesyncd.read_text()
    assert "NTP=192.0.2.10\n" in text
    assert "FallbackNTP=time1.google.com time2.google.com\n" in text
    assert text.startswith("# vendor header\n") and "PollIntervalMinSec=32\n" in text

    [apply_kwargs] = env.system.apply_calls()
    assert apply_kwargs["env"]["SYSTEMCTL_FORCE_BUS"] == "1"
    assert apply_kwargs["env"]["WIFI_APPLY_STRICT"] == "1"
    for argv, kwargs in env.system.calls:
        if argv[0].endswith("systemctl"):
            assert kwargs["env"]["SYSTEMCTL_FORCE_BUS"] == "1"
    # A disabled service is stopped, never restarted; each service gets exactly one action.
    assert sorted(env.system.queued()) == [("start", SNMP_UNIT), ("stop", NTP_UNIT)]
    assert one_line(capsys).startswith("SUCCESS applied=4 unchanged=0 ")
    assert "NTP_SERVER applied" in result_lines(env)


def test_changed_server_restarts_an_enabled_timesyncd(env):
    write_settings(env, "NTP_FUNC=enabled\nNTP_SERVER=192.0.2.10\n")

    assert env.module.main(["apply"]) == 0

    assert env.system.queued() == [("restart", NTP_UNIT)]


def test_get_then_apply_is_unchanged_but_still_resyncs_services(env, capsys):
    assert env.module.main(["get"]) == 0
    capsys.readouterr()
    written = (env.home / "wconfig.txt").read_text().splitlines()
    for expected in ("NTP_FUNC=enabled", "NTP_SERVER=192.168.0.3", "SNMP_FUNC=disabled", "SNMP_TRAP_IPADDR=0.0.0.0"):
        assert expected in written
    conf_before, ntp_before = env.conf.read_bytes(), env.timesyncd.read_bytes()

    assert env.module.main(["apply"]) == 0

    assert env.conf.read_bytes() == conf_before
    assert env.timesyncd.read_bytes() == ntp_before
    assert one_line(capsys).startswith("SUCCESS applied=0 unchanged=4 ")
    # A retry after a failed service step must still bring services in line.
    assert len(env.system.apply_calls()) == 1
    assert sorted(env.system.queued()) == [("start", NTP_UNIT), ("stop", SNMP_UNIT)]


def test_ntp_server_zero_means_no_server_at_all(env, capsys):
    write_settings(env, "NTP_SERVER=0.0.0.0\n")

    assert env.module.main(["apply"]) == 0

    lines = env.timesyncd.read_text().splitlines()
    assert "NTP=" in lines and "FallbackNTP=" in lines
    assert not any(line.startswith(("NTP=1", "FallbackNTP=t")) for line in lines)
    capsys.readouterr()
    assert env.module.main(["get", "NTP_SERVER"]) == 0
    assert one_line(capsys) == "NTP_SERVER=0.0.0.0"


def test_trap_destination_compares_the_host_and_keeps_the_port(env, capsys):
    data = json.loads(env.conf.read_text())
    data["snmp"]["trap"]["dest"] = "192.0.2.5:1162"
    env.conf.write_text(json.dumps(data))

    assert env.module.main(["get", "SNMP_TRAP_IPADDR"]) == 0
    assert one_line(capsys) == "SNMP_TRAP_IPADDR=192.0.2.5"

    write_settings(env, "SNMP_TRAP_IPADDR=192.0.2.5\n")
    assert env.module.main(["apply"]) == 0
    assert "SNMP_TRAP_IPADDR unchanged" in result_lines(env)

    write_settings(env, "SNMP_TRAP_IPADDR=192.0.2.7\n")
    assert env.module.main(["apply"]) == 0
    assert json.loads(env.conf.read_text())["snmp"]["trap"]["dest"] == "192.0.2.7:1162"

    write_settings(env, "SNMP_TRAP_IPADDR=0.0.0.0\n")
    assert env.module.main(["apply"]) == 0
    assert json.loads(env.conf.read_text())["snmp"]["trap"]["dest"] == ""


def test_later_unsupported_and_unknown_keys_are_reported_separately(env):
    write_settings(env, "TIMEZONE=KST-9\nLANGUAGE=en\nWLAN_SSID=x\nNTP_FUNC=enabled\n")

    assert env.module.main(["apply"]) == 5

    lines = result_lines(env)
    assert "TIMEZONE pending" in lines
    assert "LANGUAGE unsupported" in lines
    assert "WLAN_SSID unknown" in lines
    assert "NTP_FUNC unchanged" in lines


def test_unsupported_and_unknown_keys_alone_do_not_fail(env, capsys):
    write_settings(env, "LANGUAGE=en\nLOG_SAVE=disabled\nWLAN_SSID=x\nSNMP_FUNC=disabled\n")

    assert env.module.main(["apply"]) == 0

    reply = one_line(capsys)
    assert "unsupported=2" in reply and "unknown=1" in reply and "pending=0" in reply


def test_file_format_accepts_bom_crlf_and_comments(env):
    write_settings(env, "\ufeffNTP_SERVER=192.0.2.11\r\n# comment\r\n\r\n  SNMP_FUNC = enabled  \r\n")

    assert env.module.main(["apply"]) == 0

    assert "NTP=192.0.2.11\n" in env.timesyncd.read_text()
    assert json.loads(env.conf.read_text())["snmp"]["enabled"] is True


def test_a_duplicated_known_key_is_invalid(env):
    write_settings(env, "NTP_FUNC=enabled\nNTP_FUNC=disabled\nWLAN_SSID=a\nWLAN_SSID=b\n")

    assert env.module.main(["apply"]) == 3

    lines = result_lines(env)
    assert any(line.startswith("NTP_FUNC invalid") for line in lines)
    assert not any(line.startswith("WLAN_SSID invalid") for line in lines)


def test_put_edits_one_key_without_applying(env, capsys):
    write_settings(env, "# keep me\nSNMP_FUNC=disabled\nLANGUAGE=en\nSNMP_FUNC=enabled\n")

    assert env.module.main(["put", "SNMP_FUNC=enabled"]) == 0
    assert env.module.main(["put", "SNMP_SYSLOCATION=Room", "1"]) == 0

    assert (env.home / "wconfig.txt").read_text().splitlines() == [
        "# keep me", "SNMP_FUNC=enabled", "LANGUAGE=en", "SNMP_SYSLOCATION=Room 1",
    ]
    assert env.system.calls == []
    assert json.loads(env.conf.read_text())["snmp"]["enabled"] is False
    assert one_line(capsys).startswith("SUCCESS ")


def test_files_are_handed_to_the_home_owner_and_links_are_not_followed(env, monkeypatch):
    owners = []
    monkeypatch.setattr(env.module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(env.module.os, "fchown", lambda fd, uid, gid: owners.append((uid, gid)))
    outside = env.home.parent / "outside.txt"
    outside.write_text("NTP_FUNC=disabled\n")
    (env.home / "wconfig.txt").symlink_to(outside)

    assert env.module.main(["apply"]) == 1
    assert env.system.calls == []

    assert env.module.main(["put", "NTP_FUNC=enabled"]) == 0
    home = env.home.stat()
    assert owners == [(home.st_uid, home.st_gid)]
    assert outside.read_text() == "NTP_FUNC=disabled\n"
    assert not (env.home / "wconfig.txt").is_symlink()


def test_a_failed_service_step_is_reported_without_rollback(env):
    env.system.apply_rc = 1
    write_settings(env, "NTP_FUNC=disabled\nSNMP_TRAP_IPADDR=192.0.2.20\n")

    assert env.module.main(["apply"]) == 4

    lines = result_lines(env)
    assert any(line.startswith("NTP_FUNC failed") for line in lines)
    assert "SNMP_TRAP_IPADDR applied" in lines
    assert json.loads(env.conf.read_text())["global"]["ntp"]["enabled"] is False


@pytest.mark.parametrize("args", [[], ["invalid/interface"], ["get", "TIMEZONE"], ["get", "WLAN_SSID"],
                                  ["put", "NO_EQUALS"], ["put", "lower=x"], ["apply", "extra"]])
def test_usage_errors_exit_2_without_changes(env, args):
    conf_before = env.conf.read_bytes()

    assert env.module.main(args) == 2

    assert env.conf.read_bytes() == conf_before
    assert env.system.calls == []


def test_missing_settings_file_is_an_io_error(env):
    assert env.module.main(["apply"]) == 1
    assert env.system.calls == []


def test_handler_is_executable():
    assert os.access(HANDLERS / "wconfig", os.X_OK)
