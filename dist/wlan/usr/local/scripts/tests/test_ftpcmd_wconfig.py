"""wconfig validates the whole settings file before any change and keeps FTP replies on one line (#464)."""

import json
import os
import stat
import subprocess
import types
from pathlib import Path

import pytest


HANDLERS = Path(__file__).resolve().parents[4] / "opt/ftpcmd/bin"
NTP_UNIT = "systemd-timesyncd.service"
SNMP_UNIT = "snmpd.service"
LAN_UNIT = "wifi_lan_apply.service"
UNIT_FILE = Path(__file__).resolve().parents[4] / "etc/systemd/system" / LAN_UNIT
ETH0_TEXT = ("[Match]\nName=eth0\n\n[Network]\nDHCP=no\n# management address, factory-reset recovery path\n"
             "Address=192.0.2.1/24\n#KeepConfiguration=static\nLinkLocalAddressing=no\nIPv6AcceptRA=no\n")
MLAN0_TEXT = "[Match]\nName=mlan0\n\n[Network]\nDHCP=no\nAddress=198.51.100.100/24\nLinkLocalAddressing=no\n"
SNMPD_LOCAL_TEXT = "# values applied by quote wconfig (#464)\nrocommunity public  default\n"
# Fake zone files and the POSIX string each real tzdata file ends with.
ZONES = {"Asia/Seoul": "KST-9", "Asia/Tokyo": "JST-9", "UTC": "UTC0", "Etc/GMT": "GMT0",
         "Etc/GMT-9": "<+09>-9", "Etc/GMT+5": "<-05>5", "Etc/GMT-14": "<+14>-14",
         "Etc/GMT+12": "<-12>12", "America/New_York": "EST5EDT,M3.2.0,M11.1.0"}
TIMEDATECTL = "/usr/bin/timedatectl"


def tzif(footer):
    """A version 2 zone file reduced to what wconfig reads: the magic and the POSIX footer line."""
    return b"TZif2" + bytes(39) + b"\n" + footer.encode() + b"\n"


def load_handler(name):
    path = HANDLERS / name
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(path.read_text(), str(path), "exec"), module.__dict__)
    return module


class System:
    """Fake systemd: enable state follows the JSON once wifi_apply_enabled.sh has run."""

    def __init__(self, json_path, localtime, zoneinfo):
        self.json_path = json_path
        self.localtime, self.zoneinfo, self.timezone_rc = localtime, zoneinfo, 0
        self.calls = []
        self.apply_rc = 0
        self.queue_rc = 0
        self.enabled = {NTP_UNIT: True, SNMP_UNIT: False}
        # Live IPv4 per existing link, as `ip -4 -o addr show dev X` lists it.
        self.addresses = {"mlan0": [], "mlan1": []}

    def run(self, argv, **kwargs):
        argv = list(argv)
        self.calls.append((argv, kwargs))
        assert 0 < kwargs["timeout"] < 30
        if Path(argv[0]).name == "ip":
            iface = argv[-1]
            if iface not in self.addresses:
                return subprocess.CompletedProcess(argv, 1, "", f'Device "{iface}" does not exist.\n')
            out = "".join(f"5: {iface}    inet {cidr} brd 0.0.0.0 scope global {iface}\\"
                          "       valid_lft forever preferred_lft forever\n" for cidr in self.addresses[iface])
            return subprocess.CompletedProcess(argv, 0, out, "")
        if Path(argv[0]).name == "timedatectl":
            # timedated replaces the /etc/localtime link with one to the new zone file.
            if self.timezone_rc == 0:
                self.localtime.unlink()
                self.localtime.symlink_to(self.zoneinfo / argv[-1])
            return subprocess.CompletedProcess(argv, self.timezone_rc, "", "")
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
    eth0 = tmp_path / "22-eth0.network"
    eth0.write_text(ETH0_TEXT)
    mlan0 = tmp_path / "20-mlan0.network"
    mlan0.write_text(MLAN0_TEXT)
    snmpd_local = tmp_path / "snmpd.local.conf"
    snmpd_local.write_text(SNMPD_LOCAL_TEXT)
    snmpd_local.chmod(0o644)
    zoneinfo = tmp_path / "zoneinfo"
    for zone, footer in ZONES.items():
        (zoneinfo / zone).parent.mkdir(parents=True, exist_ok=True)
        (zoneinfo / zone).write_bytes(tzif(footer))
    localtime = tmp_path / "localtime"
    localtime.symlink_to(zoneinfo / "Asia/Seoul")
    system = System(conf, localtime, zoneinfo)
    monkeypatch.setattr(subprocess, "run", system.run)
    monkeypatch.setattr(module, "LOCALTIME", localtime, raising=False)
    monkeypatch.setattr(module, "ZONEINFO", zoneinfo, raising=False)
    monkeypatch.setattr(module, "HOME", home)
    monkeypatch.setattr(module, "JSON_PATH", conf)
    monkeypatch.setattr(module, "TIMESYNCD_CONF", timesyncd)
    monkeypatch.setattr(module, "ETH0_NETWORK", eth0, raising=False)
    monkeypatch.setattr(module, "MLAN0_NETWORK", mlan0, raising=False)
    monkeypatch.setattr(module, "SNMPD_LOCAL_CONF", snmpd_local, raising=False)
    remote = tmp_path / "wpa-remote.inc"
    monkeypatch.setattr(module, "REMOTE_SYSLOG", remote, raising=False)
    monkeypatch.setattr(module.syslog, "syslog", lambda *args: None)
    return types.SimpleNamespace(module=module, home=home, conf=conf, timesyncd=timesyncd, eth0=eth0,
                                 mlan0=mlan0, snmpd_local=snmpd_local, system=system,
                                 localtime=localtime, zoneinfo=zoneinfo, remote=remote)


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
    for expected in ("NTP_FUNC=enabled", "NTP_SERVER=192.168.0.3", "SNMP_FUNC=disabled", "SNMP_TRAP_IPADDR=0.0.0.0",
                     "SNMP_TRAP_WLAN=disabled", "SNMP_TRAP_CHAN=disabled", "SNMP_COMMUNITY=public",
                     "SNMP_SYSCONTACT=", "SNMP_SYSLOCATION=", "SNMP_SYSNAME=",
                     "LAN_MODE=static", "LAN_IPADDR=192.0.2.1", "LAN_NETMASK=255.255.255.0", "LAN_GATEWAY=0.0.0.0"):
        assert expected in written
    conf_before, ntp_before, eth0_before = env.conf.read_bytes(), env.timesyncd.read_bytes(), env.eth0.read_bytes()
    local_before = env.snmpd_local.read_bytes()

    assert env.module.main(["apply"]) == 0

    assert env.conf.read_bytes() == conf_before
    assert env.timesyncd.read_bytes() == ntp_before
    assert env.eth0.read_bytes() == eth0_before
    assert env.snmpd_local.read_bytes() == local_before
    assert one_line(capsys).startswith("SUCCESS applied=0 unchanged=17 ")
    # A retry after a failed service step must still bring services in line, and
    # timesyncd only reads its servers when it starts, so a listed server restarts it.
    # Unchanged LAN values hand nothing to the LAN unit: eth0 is not reconfigured.
    assert len(env.system.apply_calls()) == 1
    assert sorted(env.system.queued()) == [("restart", NTP_UNIT), ("stop", SNMP_UNIT)]


def test_ntp_server_zero_clears_only_the_device_server(env, capsys):
    # systemd 254 refills an empty FallbackNTP= with its compiled default list, so
    # 0.0.0.0 cannot mean "no server at all"; it clears the device server only.
    write_settings(env, "NTP_SERVER=0.0.0.0\n")

    assert env.module.main(["apply"]) == 0

    lines = env.timesyncd.read_text().splitlines()
    assert "NTP=" in lines
    assert "FallbackNTP=time1.google.com time2.google.com" in lines
    capsys.readouterr()
    assert env.module.main(["get", "NTP_SERVER"]) == 0
    assert one_line(capsys) == "NTP_SERVER=0.0.0.0"


def test_an_empty_device_server_reads_as_zero_whatever_the_fallback(env, capsys):
    env.timesyncd.write_text("[Time]\nNTP=\nFallbackNTP=time1.google.com\n")

    assert env.module.main(["get", "NTP_SERVER"]) == 0

    assert one_line(capsys) == "NTP_SERVER=0.0.0.0"


def test_a_skipped_timesyncd_step_fails_the_saved_server(env):
    env.system.apply_rc = 1
    write_settings(env, "NTP_FUNC=enabled\nNTP_SERVER=192.0.2.10\n")

    assert env.module.main(["apply"]) == 4

    lines = result_lines(env)
    assert any(line.startswith("NTP_SERVER failed") for line in lines)
    assert "NTP=192.0.2.10\n" in env.timesyncd.read_text()


def test_reapplying_after_a_failed_restart_restarts_timesyncd(env):
    env.system.queue_rc = 1
    write_settings(env, "NTP_SERVER=192.0.2.10\n")
    assert env.module.main(["apply"]) == 4
    assert any(line.startswith("NTP_SERVER failed") for line in result_lines(env))

    env.system.queue_rc = 0
    env.system.calls.clear()
    assert env.module.main(["apply"]) == 0

    assert "NTP_SERVER unchanged" in result_lines(env)
    assert env.system.queued() == [("restart", NTP_UNIT)]


def test_another_sessions_temporary_file_is_left_alone(env):
    # Each FTP session runs in its own PID namespace, so PIDs repeat across sessions.
    foreign = env.home / f".wconfig.txt.{os.getpid()}.tmp"
    foreign.write_text("other session in flight\n")

    assert env.module.main(["put", "NTP_FUNC=enabled"]) == 0

    assert foreign.read_text() == "other session in flight\n"
    assert (env.home / "wconfig.txt").read_text() == "NTP_FUNC=enabled\n"
    assert sorted(path.name for path in env.home.iterdir()) == [foreign.name, "wconfig.txt"]


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


def test_later_unsupported_and_unknown_keys_are_reported_separately(env, monkeypatch):
    # Every #464 key has an applier now; a key listed as pending still makes apply exit 5.
    monkeypatch.setattr(env.module, "PENDING", ("LATER_KEY",))
    write_settings(env, "LATER_KEY=x\nLANGUAGE=en\nWLAN_SSID=x\nNTP_FUNC=enabled\n")

    assert env.module.main(["apply"]) == 5

    lines = result_lines(env)
    assert "LATER_KEY pending" in lines
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
    real_fstat = os.fstat

    def fstat(fd):
        # The device home belongs to the FTP account.  Report that owner for the
        # directory even when the test itself runs as root and owns the home.
        info = real_fstat(fd)
        if not stat.S_ISDIR(info.st_mode):
            return info
        fields = list(info[:10])
        fields[4], fields[5] = 1000, 1000
        return os.stat_result(fields)

    monkeypatch.setattr(env.module.os, "fstat", fstat)
    monkeypatch.setattr(env.module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(env.module.os, "fchown", lambda fd, uid, gid: owners.append((uid, gid)))
    outside = env.home.parent / "outside.txt"
    outside.write_text("NTP_FUNC=disabled\n")
    (env.home / "wconfig.txt").symlink_to(outside)

    assert env.module.main(["apply"]) == 1
    assert env.system.calls == []

    assert env.module.main(["put", "NTP_FUNC=enabled"]) == 0
    assert owners == [(1000, 1000)]
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


@pytest.mark.parametrize("args", [[], ["invalid/interface"], ["get", "LANGUAGE"], ["get", "WLAN_SSID"],
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


# --- wired LAN (eth0) ---------------------------------------------------------------

def network_lines(env, key):
    return [line for line in env.eth0.read_text().splitlines() if line.partition("=")[0].strip() == key]


def result_line(env, key):
    [line] = [line for line in result_lines(env) if line.split(" ", 1)[0] == key]
    return line


def test_get_reports_the_lan_values_of_the_eth0_network_file(env, capsys):
    assert env.module.main(["get"]) == 0
    assert one_line(capsys) == "SUCCESS wrote 17 keys to wconfig.txt"
    written = (env.home / "wconfig.txt").read_text().splitlines()
    for expected in ("LAN_MODE=static", "LAN_IPADDR=192.0.2.1", "LAN_NETMASK=255.255.255.0", "LAN_GATEWAY=0.0.0.0"):
        assert expected in written

    env.eth0.write_text(ETH0_TEXT.replace("DHCP=no\n", "DHCP=ipv4\n")
                        .replace("Address=192.0.2.1/24\n", "Address=192.0.2.1/24\nGateway=192.0.2.254\n"))
    assert env.module.main(["get", "LAN_MODE"]) == 0
    assert capsys.readouterr().out == "LAN_MODE=ipv4\n"
    assert env.module.main(["get", "LAN_GATEWAY"]) == 0
    assert capsys.readouterr().out == "LAN_GATEWAY=192.0.2.254\n"


def test_reapplying_get_output_is_not_blocked_by_a_clash_that_already_exists(env):
    # A wireless address can appear next to eth0's subnet at any time (a DHCP lease, a
    # reconnect).  Values that do not change are never re-validated, so the rest applies.
    env.system.addresses["mlan0"] = ["192.0.2.77/24"]
    write_settings(env, "NTP_SERVER=203.0.113.5\nLAN_MODE=static\nLAN_IPADDR=192.0.2.1\n"
                        "LAN_NETMASK=255.255.255.0\nLAN_GATEWAY=0.0.0.0\n")
    eth0_before = env.eth0.read_bytes()

    assert env.module.main(["apply"]) == 0

    assert "NTP_SERVER applied" in result_lines(env)
    for key in ("LAN_MODE", "LAN_IPADDR", "LAN_NETMASK", "LAN_GATEWAY"):
        assert result_line(env, key) == f"{key} unchanged"
    assert env.eth0.read_bytes() == eth0_before
    assert ("restart", LAN_UNIT) not in env.system.queued()


def test_get_output_of_a_dhcp_eth0_applies_as_unchanged(env, capsys):
    # The LAN rules run only for values that change, so reading back an eth0 that
    # this command could not have written still applies.
    env.eth0.write_text(ETH0_TEXT.replace("DHCP=no\n", "DHCP=ipv4\n"))
    assert env.module.main(["get"]) == 0
    capsys.readouterr()
    eth0_before = env.eth0.read_bytes()

    assert env.module.main(["apply"]) == 0

    assert env.eth0.read_bytes() == eth0_before
    assert result_line(env, "LAN_MODE") == "LAN_MODE unchanged"


def test_the_lan_unit_is_queued_after_every_other_service_action(env):
    # The unit's wait starts once it is queued, so nothing slow may follow it.
    write_settings(env, "NTP_FUNC=enabled\nNTP_SERVER=203.0.113.5\nSNMP_FUNC=enabled\nLAN_IPADDR=192.0.2.50\n")

    assert env.module.main(["apply"]) == 0

    assert len(env.system.apply_calls()) == 1
    assert env.system.calls[-1][0][-2:] == ["restart", LAN_UNIT]


def test_a_changed_lan_address_is_saved_and_handed_to_the_lan_unit(env, capsys):
    write_settings(env, "LAN_IPADDR=192.0.2.50\n")

    assert env.module.main(["apply"]) == 0

    text = env.eth0.read_text()
    assert network_lines(env, "Address") == ["Address=192.0.2.50/24"]
    assert network_lines(env, "DHCP") == ["DHCP=no"]
    assert text.startswith("[Match]\nName=eth0\n\n[Network]\n")
    for kept in ("# management address, factory-reset recovery path\n", "#KeepConfiguration=static\n",
                 "LinkLocalAddressing=no\n", "IPv6AcceptRA=no\n"):
        assert kept in text
    assert env.system.queued() == [("restart", LAN_UNIT)]
    for argv, kwargs in env.system.calls:
        if "--no-block" in argv:
            assert argv[0].endswith("systemctl") and kwargs["env"]["SYSTEMCTL_FORCE_BUS"] == "1"
    assert result_line(env, "LAN_IPADDR").startswith("LAN_IPADDR scheduled")
    assert one_line(capsys).startswith("SUCCESS applied=0 unchanged=0 scheduled=1 failed=0 ")


def test_a_full_lan_block_writes_address_prefix_and_gateway_in_network(env):
    write_settings(env, "LAN_MODE=static\nLAN_IPADDR=203.0.113.10\nLAN_NETMASK=255.255.255.128\n"
                        "LAN_GATEWAY=203.0.113.1\n")

    assert env.module.main(["apply"]) == 0

    assert network_lines(env, "Address") == ["Address=203.0.113.10/25"]
    assert network_lines(env, "Gateway") == ["Gateway=203.0.113.1"]
    text = env.eth0.read_text()
    assert text.index("[Network]") < text.index("Gateway=203.0.113.1")
    assert result_line(env, "LAN_MODE") == "LAN_MODE unchanged"
    for key in ("LAN_IPADDR", "LAN_NETMASK", "LAN_GATEWAY"):
        assert result_line(env, key).startswith(f"{key} scheduled")
    assert env.system.queued() == [("restart", LAN_UNIT)]


def test_gateway_zero_removes_it_and_one_address_line_remains(env):
    env.eth0.write_text(ETH0_TEXT.replace("Address=192.0.2.1/24\n", "Address=192.0.2.1/24\nAddress=192.0.2.2/24\n"
                                          "Gateway=192.0.2.254\nGateway=192.0.2.253\n"))
    write_settings(env, "LAN_IPADDR=192.0.2.60\nLAN_GATEWAY=0.0.0.0\n")

    assert env.module.main(["apply"]) == 0

    assert network_lines(env, "Address") == ["Address=192.0.2.60/24"]
    assert network_lines(env, "Gateway") == []


def test_a_dhcp_eth0_needs_lan_mode_static_in_the_same_file(env):
    env.eth0.write_text(ETH0_TEXT.replace("DHCP=no\n", "DHCP=ipv4\n"))
    before = env.eth0.read_bytes()
    write_settings(env, "LAN_IPADDR=192.0.2.50\n")

    assert env.module.main(["apply"]) == 3
    assert env.eth0.read_bytes() == before
    assert "LAN_MODE" in result_line(env, "LAN_IPADDR")

    write_settings(env, "LAN_MODE=static\nLAN_IPADDR=192.0.2.50\n")
    assert env.module.main(["apply"]) == 0
    assert network_lines(env, "DHCP") == ["DHCP=no"]
    assert network_lines(env, "Address") == ["Address=192.0.2.50/24"]


@pytest.mark.parametrize("line", [
    "LAN_MODE=dhcp", "LAN_MODE=Static", "LAN_IPADDR=999.1.1.1", "LAN_IPADDR=127.0.0.1", "LAN_IPADDR=0.1.2.3",
    "LAN_IPADDR=224.0.0.1", "LAN_IPADDR=192.0.2.0", "LAN_IPADDR=192.0.2.255",
    "LAN_NETMASK=255.0.255.0", "LAN_NETMASK=255.255.255.254", "LAN_NETMASK=254.0.0.0", "LAN_NETMASK=24",
    "LAN_NETMASK=0.0.0.255",
    "LAN_GATEWAY=host.example", "LAN_GATEWAY=198.51.100.1", "LAN_GATEWAY=192.0.2.1", "LAN_GATEWAY=192.0.2.255"])
def test_each_bad_lan_value_blocks_the_whole_apply(env, line):
    write_settings(env, f"NTP_SERVER=203.0.113.5\n{line}\n")
    eth0_before, ntp_before = env.eth0.read_bytes(), env.timesyncd.read_bytes()

    assert env.module.main(["apply"]) == 3

    assert env.eth0.read_bytes() == eth0_before
    assert env.timesyncd.read_bytes() == ntp_before
    assert env.system.queued() == []
    key = line.partition("=")[0]
    assert result_line(env, key).startswith(f"{key} invalid")


@pytest.mark.parametrize("line,reason", [("LAN_MODE=dhcp", "DHCP is not supported"),
                                         ("LAN_NETMASK=255.255.255.254", "255.255.255.252")])
def test_a_refused_lan_mode_or_netmask_names_the_limit(env, line, reason):
    # The combined check would refuse these too, but with a reason about the address.
    write_settings(env, f"{line}\n")

    assert env.module.main(["apply"]) == 3

    key = line.partition("=")[0]
    assert reason in result_line(env, key)


def test_moving_eth0_to_another_subnet_does_not_keep_an_outside_gateway(env):
    env.eth0.write_text(ETH0_TEXT.replace("Address=192.0.2.1/24\n", "Address=192.0.2.1/24\nGateway=192.0.2.254\n"))
    before = env.eth0.read_bytes()
    write_settings(env, "LAN_IPADDR=203.0.113.10\n")

    assert env.module.main(["apply"]) == 3

    assert env.eth0.read_bytes() == before
    line = result_line(env, "LAN_IPADDR")
    assert line.startswith("LAN_IPADDR invalid") and "LAN_GATEWAY" in line


@pytest.mark.parametrize("settings,live", [
    ("LAN_IPADDR=198.51.100.5\n", []),                                      # mlan0's static Address= while it is down
    ("LAN_IPADDR=203.0.113.10\n", ["203.0.113.77/24"]),                     # mlan0's live address
    ("LAN_IPADDR=198.51.0.10\nLAN_NETMASK=255.255.0.0\n", []),              # a wider eth0 prefix holds mlan0's subnet
    ("LAN_IPADDR=203.0.113.10\nLAN_NETMASK=255.255.255.128\n", ["203.0.0.1/16"]),  # a wider mlan0 prefix
])
def test_an_eth0_subnet_that_overlaps_mlan0_is_invalid(env, settings, live):
    env.system.addresses["mlan0"] = live
    before = env.eth0.read_bytes()
    write_settings(env, settings)

    assert env.module.main(["apply"]) == 3

    assert env.eth0.read_bytes() == before
    assert env.system.queued() == []
    line = result_line(env, "LAN_IPADDR")
    assert line.startswith("LAN_IPADDR invalid") and "mlan0" in line


@pytest.mark.parametrize("iface,cidr", [("mlan0", "203.0.113.77/32"), ("mlan1", "203.0.113.77/24")])
def test_mlan0_host_addresses_and_mlan1_do_not_block_an_eth0_subnet(env, iface, cidr):
    # A /32 is the peer_route mirror, not a subnet; the rule covers mlan0 only (#464 decision).
    env.system.addresses[iface] = [cidr]
    write_settings(env, "LAN_IPADDR=203.0.113.10\n")

    assert env.module.main(["apply"]) == 0

    assert network_lines(env, "Address") == ["Address=203.0.113.10/24"]


def test_a_gateway_change_alone_does_not_recheck_the_subnet(env):
    env.system.addresses["mlan0"] = ["192.0.2.77/24"]  # already next to eth0 before this apply
    write_settings(env, "LAN_GATEWAY=192.0.2.254\n")

    assert env.module.main(["apply"]) == 0

    assert network_lines(env, "Gateway") == ["Gateway=192.0.2.254"]


def test_a_lan_unit_that_cannot_be_queued_restores_the_eth0_file(env):
    before = env.eth0.read_bytes()
    env.system.queue_rc = 1
    write_settings(env, "LAN_IPADDR=192.0.2.50\n")

    assert env.module.main(["apply"]) == 4

    assert env.eth0.read_bytes() == before
    line = result_line(env, "LAN_IPADDR")
    assert line.startswith("LAN_IPADDR failed") and "restored" in line
    # The restored value differs again, so applying the same file retries the change.
    env.system.queue_rc = 0
    assert env.module.main(["apply"]) == 0
    assert network_lines(env, "Address") == ["Address=192.0.2.50/24"]


def test_lan_values_are_never_written_into_a_missing_eth0_file(env):
    env.eth0.unlink()
    write_settings(env, "LAN_MODE=static\nLAN_IPADDR=192.0.2.50\nLAN_NETMASK=255.255.255.0\nLAN_GATEWAY=0.0.0.0\n")

    assert env.module.main(["apply"]) == 4

    assert not env.eth0.exists()
    assert env.system.queued() == []


def test_lan_unit_applies_eth0_after_the_reply_and_restores_the_peer_channel():
    sections, current = {}, []
    for raw in UNIT_FILE.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("["):
            current = sections.setdefault(line, [])
            continue
        key, _, value = line.partition("=")
        current.append((key.strip(), value.strip()))
    assert "[Install]" not in sections  # started only by wconfig, never at boot
    unit, service = dict(sections["[Unit]"]), sections["[Service]"]
    assert unit["StartLimitIntervalSec"] == "0"  # a refused start would still look queued
    assert "PartOf" not in unit
    assert ("Type", "oneshot") in service
    assert ("ExecStartPre", "/bin/sleep 2") in service  # the FTP reply leaves first
    assert [value for key, value in service if key == "ExecStart"] == [
        "/usr/local/scripts/wifi.sh net apply eth0", "/usr/local/scripts/wifi_peer_net_reapply.sh"]
    assert any(key == "TimeoutStartSec" for key, _ in service)
    assert load_handler("wconfig").LAN_UNIT == UNIT_FILE.name


# --- SNMP per-type traps (#464 PR-3) ---------------------------------------------------

def trap_json(env):
    return json.loads(env.conf.read_text())["snmp"]["trap"]


def set_trap(env, **values):
    data = json.loads(env.conf.read_text())
    data["snmp"]["trap"].update(values)
    env.conf.write_text(json.dumps(data, indent=2) + "\n")


def test_get_reports_a_trap_type_as_enabled_only_while_traps_are_on(env, capsys):
    assert env.module.main(["get", "SNMP_TRAP_WLAN"]) == 0
    assert capsys.readouterr().out == "SNMP_TRAP_WLAN=disabled\n"  # snmp.trap.enabled is off
    set_trap(env, enabled=True, link=False)
    assert env.module.main(["get", "SNMP_TRAP_WLAN"]) == 0
    assert capsys.readouterr().out == "SNMP_TRAP_WLAN=disabled\n"
    assert env.module.main(["get", "SNMP_TRAP_CHAN"]) == 0
    assert capsys.readouterr().out == "SNMP_TRAP_CHAN=enabled\n"  # a missing switch means on


def test_turning_on_link_traps_alone_leaves_channel_traps_off(env):
    write_settings(env, "SNMP_TRAP_WLAN=enabled\n")

    assert env.module.main(["apply"]) == 0

    trap = trap_json(env)
    assert (trap["enabled"], trap["link"], trap["channel"]) == (True, True, False)
    assert (trap["dest"], trap["community"]) == ("", "public")
    assert result_line(env, "SNMP_TRAP_WLAN") == "SNMP_TRAP_WLAN applied"
    # wifi_event@ follows snmp.trap.enabled through the enable sync; no unit action of its own.
    assert len(env.system.apply_calls()) == 1
    assert env.system.queued() == []


def test_turning_both_trap_types_off_only_turns_traps_off(env):
    set_trap(env, enabled=True, link=True, channel=True)
    write_settings(env, "SNMP_TRAP_WLAN=disabled\nSNMP_TRAP_CHAN=disabled\n")

    assert env.module.main(["apply"]) == 0

    # The per-type choice stays, so turning snmp.trap.enabled back on (web UI) sends traps again.
    trap = trap_json(env)
    assert (trap["enabled"], trap["link"], trap["channel"]) == (False, True, True)


def test_turning_one_type_back_on_keeps_the_other_as_it_was_reported(env):
    set_trap(env, enabled=False, link=True, channel=True)  # both reported disabled
    write_settings(env, "SNMP_TRAP_CHAN=enabled\n")

    assert env.module.main(["apply"]) == 0

    trap = trap_json(env)
    assert (trap["enabled"], trap["link"], trap["channel"]) == (True, False, True)


def test_get_output_with_trap_types_applies_as_unchanged(env, capsys):
    set_trap(env, enabled=True, link=True, channel=False, dest="192.0.2.20")
    assert env.module.main(["get"]) == 0
    capsys.readouterr()
    before = env.conf.read_bytes()

    assert env.module.main(["apply"]) == 0

    assert env.conf.read_bytes() == before
    assert result_line(env, "SNMP_TRAP_WLAN") == "SNMP_TRAP_WLAN unchanged"
    assert result_line(env, "SNMP_TRAP_CHAN") == "SNMP_TRAP_CHAN unchanged"


@pytest.mark.parametrize("line", ["SNMP_TRAP_WLAN=on", "SNMP_TRAP_CHAN=true"])
def test_a_bad_trap_switch_blocks_the_whole_apply(env, line):
    write_settings(env, f"{line}\n")
    before = env.conf.read_bytes()

    assert env.module.main(["apply"]) == 3

    assert env.conf.read_bytes() == before
    assert env.system.calls == []


def test_a_failed_enable_sync_fails_the_trap_keys(env):
    env.system.apply_rc = 1
    write_settings(env, "SNMP_TRAP_WLAN=enabled\n")

    assert env.module.main(["apply"]) == 4

    assert result_line(env, "SNMP_TRAP_WLAN").startswith("SNMP_TRAP_WLAN failed")


# --- SNMP strings: /etc/snmp/snmpd.local.conf, which snmpd reads after snmpd.conf ------------

def set_local(env, text):
    env.snmpd_local.write_bytes(text.encode("utf-8"))
    env.snmpd_local.chmod(0o644)


def write_settings_utf8(env, text):
    (env.home / "wconfig.txt").write_bytes(text.encode("utf-8"))


def snmpd_on(env):
    """snmpd enabled both in the JSON and in systemd, as SNMP_FUNC=enabled leaves it."""
    data = json.loads(env.conf.read_text())
    data["snmp"]["enabled"] = True
    env.conf.write_text(json.dumps(data, indent=2) + "\n")
    env.system.enabled[SNMP_UNIT] = True


def written_settings(env):
    return (env.home / "wconfig.txt").read_text(encoding="utf-8").splitlines()


def test_get_reports_the_snmp_strings_of_the_local_file(env, capsys):
    set_local(env, "# comment\nrocommunity mycomm 192.0.2.0/24\nrocommunity6 other default\n"
                   "SysContact Kim <kim@example.com>\nsyslocation Old place\n"
                   'syslocation 서울 3F #2 "quoted"\nsysname Box-1\n')
    set_trap(env, community="mycomm")

    assert env.module.main(["get"]) == 0

    assert one_line(capsys) == "SUCCESS wrote 17 keys to wconfig.txt"
    # The first rocommunity's community; a sys* directive takes its last line, as snmpd does.
    for expected in ("SNMP_COMMUNITY=mycomm", "SNMP_SYSCONTACT=Kim <kim@example.com>",
                     'SNMP_SYSLOCATION=서울 3F #2 "quoted"', "SNMP_SYSNAME=Box-1"):
        assert expected in written_settings(env)


def test_a_missing_local_file_reads_as_empty_and_its_get_output_still_applies(env, capsys):
    env.snmpd_local.unlink()
    assert env.module.main(["get"]) == 0
    capsys.readouterr()
    for key in ("SNMP_COMMUNITY", "SNMP_SYSCONTACT", "SNMP_SYSLOCATION", "SNMP_SYSNAME"):
        assert f"{key}=" in written_settings(env)

    assert env.module.main(["apply"]) == 0

    # An empty community is not one wconfig can write, so the trap community does not make it
    # differ: a file written by get still applies, and nothing is created.
    assert one_line(capsys).startswith("SUCCESS applied=0 unchanged=17 ")
    assert not env.snmpd_local.exists()


def test_a_changed_location_rewrites_only_its_line_and_restarts_a_running_snmpd(env):
    env.system.enabled[SNMP_UNIT] = True
    set_local(env, "# keep\nrocommunity public 192.0.2.0/24\nsyslocation Old\nsysname Box-1\n")
    conf_before = env.conf.read_bytes()
    write_settings_utf8(env, 'SNMP_SYSLOCATION=서울 3F #2 "quoted"\n')

    assert env.module.main(["apply"]) == 0

    assert env.snmpd_local.read_text(encoding="utf-8") == (
        '# keep\nrocommunity public 192.0.2.0/24\nsyslocation 서울 3F #2 "quoted"\nsysname Box-1\n')
    assert stat.S_IMODE(env.snmpd_local.stat().st_mode) == 0o644
    assert env.conf.read_bytes() == conf_before
    assert env.system.queued() == [("try-restart", SNMP_UNIT)]
    assert result_line(env, "SNMP_SYSLOCATION") == "SNMP_SYSLOCATION applied"


def test_an_empty_string_removes_every_line_of_the_directive(env):
    env.system.enabled[SNMP_UNIT] = True
    set_local(env, "rocommunity public  default\nsysname Box-1\nSYSNAME Box-2\nsyslocation Here\n")
    write_settings(env, "SNMP_SYSNAME=\n")

    assert env.module.main(["apply"]) == 0

    # snmpd then falls back to the host name; the other directives stay as they were.
    assert env.snmpd_local.read_text() == "rocommunity public  default\nsyslocation Here\n"
    assert env.system.queued() == [("try-restart", SNMP_UNIT)]


def test_a_disabled_snmpd_keeps_the_strings_for_its_next_start(env):
    write_settings(env, "SNMP_SYSCONTACT=ops@example.com\n")

    assert env.module.main(["apply"]) == 0

    assert env.snmpd_local.read_text() == SNMPD_LOCAL_TEXT + "syscontact ops@example.com\n"
    # try-restart leaves a stopped snmpd stopped; it reads the file when it next starts.
    assert env.system.queued() == [("try-restart", SNMP_UNIT)]
    assert result_line(env, "SNMP_SYSCONTACT") == "SNMP_SYSCONTACT applied"


def test_reapplying_unchanged_strings_does_not_restart_snmpd(env, capsys):
    # A restart resets sysUpTime and drops queries for a moment, so only a change restarts it.
    snmpd_on(env)
    set_local(env, "rocommunity public  default\nsyslocation Here\n")
    before = env.snmpd_local.read_bytes()
    assert env.module.main(["get"]) == 0
    capsys.readouterr()

    assert env.module.main(["apply"]) == 0

    assert env.snmpd_local.read_bytes() == before
    assert one_line(capsys).startswith("SUCCESS applied=0 unchanged=17 ")
    assert sorted(env.system.queued()) == [("restart", NTP_UNIT), ("start", SNMP_UNIT)]


def test_a_community_sets_the_trap_community_and_only_the_community_word(env):
    env.system.enabled[SNMP_UNIT] = True
    set_local(env, "rocommunity public 192.0.2.0/24 .1.3.6.1.2.1\nROCOMMUNITY  public default\n"
                   "rocommunity6 public default\n")
    write_settings(env, "SNMP_COMMUNITY=Secret-1\n")

    assert env.module.main(["apply"]) == 0

    assert json.loads(env.conf.read_text())["snmp"]["trap"]["community"] == "Secret-1"
    # A source or OID narrowed by hand is kept, and rocommunity6 is another directive.
    assert env.snmpd_local.read_text() == ("rocommunity Secret-1 192.0.2.0/24 .1.3.6.1.2.1\n"
                                           "ROCOMMUNITY  Secret-1 default\nrocommunity6 public default\n")
    assert env.system.queued() == [("try-restart", SNMP_UNIT)]
    assert result_line(env, "SNMP_COMMUNITY") == "SNMP_COMMUNITY applied"


def test_a_community_without_a_line_is_added_for_every_source(env):
    set_local(env, "syslocation Here")
    write_settings(env, "SNMP_COMMUNITY=abc\n")

    assert env.module.main(["apply"]) == 0

    assert env.snmpd_local.read_text() == "syslocation Here\nrocommunity abc default\n"


def test_a_trap_community_changed_elsewhere_makes_the_same_community_apply(env, capsys):
    # SNMP_COMMUNITY sets both communities (#464); the web UI edits only snmp.trap.community.
    env.system.enabled[SNMP_UNIT] = True
    set_trap(env, community="webui")
    before = env.snmpd_local.read_bytes()
    assert env.module.main(["get", "SNMP_COMMUNITY"]) == 0
    assert capsys.readouterr().out == "SNMP_COMMUNITY=public\n"
    write_settings(env, "SNMP_COMMUNITY=public\n")

    assert env.module.main(["apply"]) == 0

    assert json.loads(env.conf.read_text())["snmp"]["trap"]["community"] == "public"
    assert result_line(env, "SNMP_COMMUNITY") == "SNMP_COMMUNITY applied"
    # The query community was already right: the file stays and snmpd keeps running.
    assert env.snmpd_local.read_bytes() == before
    assert env.system.queued() == []


def test_a_failed_trap_community_save_leaves_the_query_community(env, monkeypatch):
    env.system.enabled[SNMP_UNIT] = True

    def broken(changes):
        raise OSError("disk full")

    monkeypatch.setattr(env.module, "save_json", broken)
    write_settings(env, "SNMP_COMMUNITY=Secret-1\nSNMP_SYSNAME=Box-9\n")

    assert env.module.main(["apply"]) == 4

    assert env.snmpd_local.read_text() == SNMPD_LOCAL_TEXT + "sysname Box-9\n"
    assert result_line(env, "SNMP_COMMUNITY").startswith("SNMP_COMMUNITY failed settings save failed")
    assert result_line(env, "SNMP_SYSNAME") == "SNMP_SYSNAME applied"


def test_a_restart_that_cannot_be_queued_restores_the_local_file(env):
    env.system.enabled[SNMP_UNIT] = True
    before = env.snmpd_local.read_bytes()
    env.system.queue_rc = 1
    write_settings(env, "SNMP_SYSLOCATION=New place\n")

    assert env.module.main(["apply"]) == 4

    # failed means snmpd still runs with the old values, so the file keeps them too and
    # applying the same file again retries instead of reading as unchanged.
    assert env.snmpd_local.read_bytes() == before
    assert result_line(env, "SNMP_SYSLOCATION").startswith(
        "SNMP_SYSLOCATION failed try-restart snmpd.service failed rc=1")
    env.system.queue_rc = 0

    assert env.module.main(["apply"]) == 0

    assert env.snmpd_local.read_text() == SNMPD_LOCAL_TEXT + "syslocation New place\n"
    assert env.system.queued()[-1] == ("try-restart", SNMP_UNIT)


def test_a_failed_restart_removes_a_local_file_it_created(env):
    env.system.enabled[SNMP_UNIT] = True
    env.snmpd_local.unlink()
    env.system.queue_rc = 1
    write_settings(env, "SNMP_SYSNAME=Box-9\n")

    assert env.module.main(["apply"]) == 4

    assert not env.snmpd_local.exists()


def test_an_unreachable_systemd_fails_the_strings_even_when_snmpd_looks_disabled(env):
    # A failed lookup is not a stopped snmpd (Codex P2, tribunal A-R1-002): with systemd
    # unreachable every systemctl call fails, and the same file must retry once it answers.
    before = env.snmpd_local.read_bytes()
    env.system.enabled[SNMP_UNIT] = False
    env.system.queue_rc = 1
    write_settings(env, "SNMP_SYSNAME=Box-9\n")

    assert env.module.main(["apply"]) == 4

    assert env.snmpd_local.read_bytes() == before
    assert result_line(env, "SNMP_SYSNAME").startswith("SNMP_SYSNAME failed try-restart snmpd.service failed rc=1")
    env.system.queue_rc = 0

    assert env.module.main(["apply"]) == 0

    assert env.snmpd_local.read_text() == SNMPD_LOCAL_TEXT + "sysname Box-9\n"
    assert env.system.queued()[-1] == ("try-restart", SNMP_UNIT)


@pytest.mark.parametrize("line", [
    "SNMP_COMMUNITY=", "SNMP_COMMUNITY=" + "a" * 33, "SNMP_COMMUNITY=pub lic", "SNMP_COMMUNITY=pub#lic",
    'SNMP_COMMUNITY=pub"lic', "SNMP_COMMUNITY=pub'lic", "SNMP_COMMUNITY=pub\\lic", "SNMP_COMMUNITY=공개",
    # snmpd reads a leading - as an option: "rocommunity -v default" is an illegal line (Codex P2).
    "SNMP_COMMUNITY=-v", "SNMP_COMMUNITY=-public",
    "SNMP_SYSLOCATION=#2F", "SNMP_SYSNAME=" + "a" * 256, "SNMP_SYSNAME=" + "가" * 86,
    "SNMP_SYSCONTACT=tab\there", "SNMP_SYSCONTACT=bell\x07",
])
def test_each_bad_snmp_string_blocks_the_whole_apply(env, line):
    env.system.enabled[SNMP_UNIT] = True
    local_before, conf_before = env.snmpd_local.read_bytes(), env.conf.read_bytes()
    write_settings_utf8(env, f"NTP_SERVER=192.0.2.10\n{line}\n")

    assert env.module.main(["apply"]) == 3

    assert env.snmpd_local.read_bytes() == local_before
    assert env.conf.read_bytes() == conf_before
    assert env.system.calls == []
    key = line.split("=", 1)[0]
    assert result_line(env, key).startswith(f"{key} invalid")


def test_a_snmp_string_that_is_not_utf8_is_invalid_rather_than_a_crash(env):
    (env.home / "wconfig.txt").write_bytes(b"SNMP_SYSLOCATION=\x93\x8c\x8b\x9e\n")

    assert env.module.main(["apply"]) == 3

    assert result_line(env, "SNMP_SYSLOCATION").startswith("SNMP_SYSLOCATION invalid")


@pytest.mark.parametrize("key,value", [
    ("SNMP_SYSLOCATION", "a" * 255), ("SNMP_SYSLOCATION", "가" * 85),
    ("SNMP_SYSCONTACT", 'Kim "ops" <k@example.com>'), ("SNMP_SYSNAME", "x\\y #2"),
    ("SNMP_COMMUNITY", "a!b$c%d&e(f)g*h"), ("SNMP_COMMUNITY", "Z" * 32),
])
def test_snmp_strings_take_utf8_quotes_and_a_hash_inside_up_to_their_limits(env, capsys, key, value):
    write_settings_utf8(env, f"{key}={value}\n")

    assert env.module.main(["apply"]) == 0

    assert result_line(env, key) == f"{key} applied"
    capsys.readouterr()
    assert env.module.main(["get"]) == 0
    assert f"{key}={value}" in written_settings(env)


def test_get_of_one_key_prints_a_value_that_fits_the_reply_line(env, capsys):
    set_local(env, "rocommunity public  default\nsyslocation " + "a" * 239 + "\n")

    assert env.module.main(["get", "SNMP_SYSLOCATION"]) == 0

    assert capsys.readouterr().out == "SNMP_SYSLOCATION=" + "a" * 239 + "\n"


@pytest.mark.parametrize("value", ["서울 3F", "a" * 240])
def test_get_of_one_key_refuses_a_value_the_reply_line_cannot_carry(env, capsys, value):
    # The dispatcher relays one printable ASCII line of at most 256 bytes; such a value is
    # read from wconfig.txt after a full get, as it can only be written with ftp put.
    set_local(env, f"rocommunity public  default\nsyslocation {value}\n")

    assert env.module.main(["get", "SNMP_SYSLOCATION"]) == 2

    assert capsys.readouterr().out == ""
    assert env.module.main(["get"]) == 0
    assert f"SNMP_SYSLOCATION={value}" in written_settings(env)


# --- time zone ------------------------------------------------------------------------

def set_zone(env, zone):
    env.localtime.unlink()
    env.localtime.symlink_to(env.zoneinfo / zone)


def timezone_calls(env):
    return [argv for argv, _ in env.system.calls if Path(argv[0]).name == "timedatectl"]


def test_get_reports_the_posix_string_the_zone_file_ends_with(env, capsys):
    assert env.module.main(["get", "TIMEZONE"]) == 0
    assert capsys.readouterr().out == "TIMEZONE=KST-9\n"

    set_zone(env, "Etc/GMT+5")
    assert env.module.main(["get"]) == 0
    assert one_line(capsys) == "SUCCESS wrote 17 keys to wconfig.txt"
    assert "TIMEZONE=<-05>5" in written_settings(env)
    assert timezone_calls(env) == []


def test_a_missing_localtime_reads_as_utc(env, capsys):
    # systemd and the C library treat a device without /etc/localtime as UTC.
    env.localtime.unlink()

    assert env.module.main(["get", "TIMEZONE"]) == 0

    assert capsys.readouterr().out == "TIMEZONE=UTC0\n"


def test_a_localtime_that_is_no_zone_file_reads_empty(env, capsys):
    env.localtime.unlink()
    env.localtime.write_text("not a zone\n")

    assert env.module.main(["get", "TIMEZONE"]) == 0

    assert capsys.readouterr().out == "TIMEZONE=\n"


def test_the_contec_value_of_the_default_zone_is_unchanged(env):
    write_settings(env, "TIMEZONE=KST-9\n")

    assert env.module.main(["apply"]) == 0

    assert result_line(env, "TIMEZONE") == "TIMEZONE unchanged"
    assert timezone_calls(env) == []
    assert env.system.queued() == []


@pytest.mark.parametrize("value, zone", [
    ("JST-9", "Asia/Tokyo"), ("UTC0", "UTC"), ("GMT0", "Etc/GMT"),
    # Any other whole-hour offset: the Etc/GMT zone keeps the POSIX sign (Etc/GMT-9 is UTC+9).
    ("EST5", "Etc/GMT+5"), ("<-05>5", "Etc/GMT+5"), ("XYZ+5", "Etc/GMT+5"), ("<+09>-9", "Etc/GMT-9"),
    ("ABC-14", "Etc/GMT-14"), ("ABC12", "Etc/GMT+12"), ("XYZ0", "Etc/GMT"),
])
def test_a_posix_zone_sets_the_zone_with_the_same_offset(env, capsys, value, zone):
    write_settings(env, f"TIMEZONE={value}\n")

    assert env.module.main(["apply"]) == 0

    assert result_line(env, "TIMEZONE") == "TIMEZONE applied"
    assert timezone_calls(env) == [[TIMEDATECTL, "--no-ask-password", "set-timezone", zone]]
    capsys.readouterr()
    assert env.module.main(["get", "TIMEZONE"]) == 0
    assert capsys.readouterr().out == f"TIMEZONE={ZONES[zone]}\n"


def test_kst_sets_seoul_from_another_zone(env):
    set_zone(env, "UTC")
    write_settings(env, "TIMEZONE=KST-9\n")

    assert env.module.main(["apply"]) == 0

    assert timezone_calls(env) == [[TIMEDATECTL, "--no-ask-password", "set-timezone", "Asia/Seoul"]]
    assert env.localtime.resolve() == (env.zoneinfo / "Asia/Seoul").resolve()


@pytest.mark.parametrize("value", [
    "", "KST", "-9", "KS-9", "Asia/Seoul", "IST-5:30", "EST5EDT", "EST5EDT,M3.2.0,M11.1.0",
    "XYZ-15", "XYZ13", "XYZ+13", "<+09-9", "KST-09x", "KST--9",
])
def test_a_zone_without_a_whole_hour_fixed_offset_blocks_the_apply(env, value):
    write_settings(env, f"NTP_SERVER=192.0.2.10\nTIMEZONE={value}\n")
    ntp_before = env.timesyncd.read_bytes()

    assert env.module.main(["apply"]) == 3

    assert result_line(env, "TIMEZONE").startswith("TIMEZONE invalid")
    assert env.timesyncd.read_bytes() == ntp_before
    assert env.system.calls == []


@pytest.mark.parametrize("value", ["XYZ-15", "XYZ13", "XYZ+13"])
def test_an_offset_outside_the_etc_gmt_range_is_invalid_even_with_a_zone_file(env, value):
    # tzdata ends at Etc/GMT-14 and Etc/GMT+12; the range check names the limit itself.
    for zone in ("Etc/GMT-15", "Etc/GMT+13"):
        (env.zoneinfo / zone).write_bytes(tzif("XYZ"))
    write_settings(env, f"TIMEZONE={value}\n")

    assert env.module.main(["apply"]) == 3

    assert result_line(env, "TIMEZONE") == "TIMEZONE invalid expected an offset from -14 to 12"


def test_a_zone_the_device_has_no_file_for_is_invalid(env):
    (env.zoneinfo / "Etc/GMT+5").unlink()
    write_settings(env, "TIMEZONE=EST5\n")

    assert env.module.main(["apply"]) == 3

    assert result_line(env, "TIMEZONE") == "TIMEZONE invalid zone Etc/GMT+5 is not installed"
    assert env.system.calls == []


def test_a_timedatectl_failure_is_reported_and_the_zone_stays(env):
    env.system.timezone_rc = 1
    write_settings(env, "TIMEZONE=JST-9\nSNMP_TRAP_IPADDR=192.0.2.20\n")

    assert env.module.main(["apply"]) == 4

    assert result_line(env, "TIMEZONE") == "TIMEZONE failed timedatectl set-timezone Asia/Tokyo failed rc=1"
    assert result_line(env, "SNMP_TRAP_IPADDR") == "SNMP_TRAP_IPADDR applied"
    assert env.localtime.resolve() == (env.zoneinfo / "Asia/Seoul").resolve()
    assert ("try-restart", "rsyslog.service") not in env.system.queued()


def test_a_zone_change_restarts_a_running_rsyslog_so_log_times_follow(env):
    # A running process keeps the offset it read at start: rsyslog, which stamps most
    # /var/log/cantops logs, wrote the old offset on the board until it restarted.
    write_settings(env, "TIMEZONE=JST-9\n")

    assert env.module.main(["apply"]) == 0

    names = [Path(argv[0]).name for argv, _ in env.system.calls]
    assert names == ["timedatectl", "systemctl"]
    assert env.system.queued() == [("try-restart", "rsyslog.service")]


def test_an_rsyslog_hand_off_failure_keeps_the_zone_and_says_so(env):
    # The zone has changed; only the log times lag, so the key stays applied with the reason.
    env.system.queue_rc = 1
    write_settings(env, "TIMEZONE=JST-9\n")

    assert env.module.main(["apply"]) == 0

    assert result_line(env, "TIMEZONE") == ("TIMEZONE applied try-restart rsyslog.service failed rc=1; "
                                            "log times keep the old offset until quote rst")
    assert env.localtime.resolve() == (env.zoneinfo / "Asia/Tokyo").resolve()


def test_get_output_of_a_zone_outside_the_table_applies_unchanged(env, capsys):
    # A zone set some other way (with daylight saving here) is read back as its POSIX string,
    # which apply would reject; the same value is never validated, so the file still applies.
    set_zone(env, "America/New_York")
    assert env.module.main(["get"]) == 0
    assert "TIMEZONE=EST5EDT,M3.2.0,M11.1.0" in written_settings(env)
    capsys.readouterr()

    assert env.module.main(["apply"]) == 0

    assert result_line(env, "TIMEZONE") == "TIMEZONE unchanged"
    assert timezone_calls(env) == []


# --- remote syslog of wpa.log lines (#482) ------------------------------------------------

# The forward has its own bounded queue that drops instead of waiting when full: run on the main
# queue, a path that holds packets blocked sendto and stopped every rsyslog log (Tribunal A-R1-001).
FORWARD = ('action(type="omfwd" target="192.0.2.30" port="514" protocol="udp" '
           'queue.type="LinkedList" queue.size="1000" queue.timeoutEnqueue="0")')


def rsyslog_restarts(env):
    return [entry for entry in env.system.queued() if entry == ("try-restart", "rsyslog.service")]


def remote_lines(env):
    return env.remote.read_text().splitlines() if env.remote.exists() else None


def test_without_the_file_remote_syslog_reads_as_off(env, capsys):
    assert env.module.main(["get"]) == 0

    assert one_line(capsys) == "SUCCESS wrote 17 keys to wconfig.txt"
    assert "LOG_FUNC=disabled" in written_settings(env)
    assert "LOG_SERVER=0.0.0.0" in written_settings(env)
    assert env.module.main(["get", "LOG_SERVER"]) == 0
    assert capsys.readouterr().out == "LOG_SERVER=0.0.0.0\n"


def test_enabling_writes_one_forward_action_and_restarts_rsyslog(env, capsys):
    write_settings(env, "LOG_FUNC=enabled\nLOG_SERVER=192.0.2.30\n")

    assert env.module.main(["apply"]) == 0

    assert result_line(env, "LOG_FUNC") == "LOG_FUNC applied"
    assert result_line(env, "LOG_SERVER") == "LOG_SERVER applied"
    lines = remote_lines(env)
    assert "# LOG_FUNC=enabled" in lines and "# LOG_SERVER=192.0.2.30" in lines
    assert [line for line in lines if not line.startswith("#")] == [FORWARD]
    assert stat.S_IMODE(env.remote.stat().st_mode) == 0o644
    assert rsyslog_restarts(env) == [("try-restart", "rsyslog.service")]
    capsys.readouterr()
    assert env.module.main(["get"]) == 0
    assert "LOG_FUNC=enabled" in written_settings(env)
    assert "LOG_SERVER=192.0.2.30" in written_settings(env)


def test_disabling_keeps_the_server_but_sends_nothing(env):
    write_settings(env, "LOG_FUNC=enabled\nLOG_SERVER=192.0.2.30\n")
    assert env.module.main(["apply"]) == 0
    write_settings(env, "LOG_FUNC=disabled\nLOG_SERVER=192.0.2.30\n")

    assert env.module.main(["apply"]) == 0

    assert result_line(env, "LOG_FUNC") == "LOG_FUNC applied"
    assert result_line(env, "LOG_SERVER") == "LOG_SERVER unchanged"
    lines = remote_lines(env)
    assert "# LOG_SERVER=192.0.2.30" in lines
    assert [line for line in lines if not line.startswith("#")] == []
    assert len(rsyslog_restarts(env)) == 2


def test_enabled_without_a_server_sends_nothing(env):
    write_settings(env, "LOG_FUNC=enabled\nLOG_SERVER=0.0.0.0\n")

    assert env.module.main(["apply"]) == 0

    assert result_line(env, "LOG_FUNC") == "LOG_FUNC applied"
    assert [line for line in remote_lines(env) if not line.startswith("#")] == []


def test_get_output_reapplies_without_rewriting_or_restarting(env, capsys):
    write_settings(env, "LOG_FUNC=enabled\nLOG_SERVER=192.0.2.30\n")
    assert env.module.main(["apply"]) == 0
    before = env.remote.read_bytes()
    capsys.readouterr()
    assert env.module.main(["get"]) == 0
    env.system.calls.clear()

    assert env.module.main(["apply"]) == 0

    assert result_line(env, "LOG_FUNC") == "LOG_FUNC unchanged"
    assert result_line(env, "LOG_SERVER") == "LOG_SERVER unchanged"
    assert env.remote.read_bytes() == before
    assert rsyslog_restarts(env) == []


@pytest.mark.parametrize("key, value", [("LOG_SERVER", "224.0.0.1"), ("LOG_SERVER", "999.1.1.1"),
                                        ("LOG_SERVER", "host.example"), ("LOG_SERVER", "192.0.2.30:514"),
                                        ("LOG_FUNC", "on")])
def test_a_bad_log_value_blocks_the_apply(env, key, value):
    write_settings(env, f"{key}={value}\n")

    assert env.module.main(["apply"]) == 3

    assert result_line(env, key).startswith(f"{key} invalid")
    assert not env.remote.exists()
    assert env.system.calls == []


def test_a_restart_failure_removes_a_new_file_and_fails_both_keys(env):
    env.system.queue_rc = 1
    write_settings(env, "LOG_FUNC=enabled\nLOG_SERVER=192.0.2.30\n")

    assert env.module.main(["apply"]) == 4

    assert result_line(env, "LOG_FUNC").startswith("LOG_FUNC failed try-restart rsyslog.service failed rc=1")
    assert result_line(env, "LOG_SERVER").startswith("LOG_SERVER failed")
    assert not env.remote.exists()


def test_a_restart_failure_puts_the_previous_file_back(env):
    write_settings(env, "LOG_FUNC=enabled\nLOG_SERVER=192.0.2.30\n")
    assert env.module.main(["apply"]) == 0
    before = env.remote.read_bytes()
    env.system.queue_rc = 1
    write_settings(env, "LOG_FUNC=enabled\nLOG_SERVER=192.0.2.31\n")

    assert env.module.main(["apply"]) == 4

    assert result_line(env, "LOG_SERVER").startswith("LOG_SERVER failed")
    assert env.remote.read_bytes() == before


def test_timezone_and_log_changes_restart_rsyslog_once(env):
    # Both follow rsyslog; one queued restart reads the new zone and the new file together.
    write_settings(env, "TIMEZONE=JST-9\nLOG_FUNC=enabled\nLOG_SERVER=192.0.2.30\n")

    assert env.module.main(["apply"]) == 0

    assert [Path(argv[0]).name for argv, _ in env.system.calls] == ["timedatectl", "systemctl"]
    assert rsyslog_restarts(env) == [("try-restart", "rsyslog.service")]


def test_a_shared_restart_failure_keeps_the_zone_and_fails_the_log_keys(env):
    env.system.queue_rc = 1
    write_settings(env, "TIMEZONE=JST-9\nLOG_FUNC=enabled\nLOG_SERVER=192.0.2.30\n")

    assert env.module.main(["apply"]) == 4

    assert result_line(env, "TIMEZONE").startswith("TIMEZONE applied try-restart rsyslog.service failed rc=1")
    assert result_line(env, "LOG_FUNC").startswith("LOG_FUNC failed")
    assert not env.remote.exists()


def test_a_file_without_the_markers_reads_as_off(env, capsys):
    env.remote.write_text('action(type="omfwd" target="192.0.2.99" port="514" protocol="udp")\n')

    assert env.module.main(["get", "LOG_FUNC"]) == 0

    assert capsys.readouterr().out == "LOG_FUNC=disabled\n"


def test_only_the_comment_markers_count(env, capsys):
    # rsyslog cannot parse a bare KEY=value line, so it never sends; get must not claim it does.
    env.remote.write_text("LOG_FUNC=enabled\nLOG_SERVER=192.0.2.99\n")

    assert env.module.main(["get", "LOG_FUNC"]) == 0

    assert capsys.readouterr().out == "LOG_FUNC=disabled\n"
