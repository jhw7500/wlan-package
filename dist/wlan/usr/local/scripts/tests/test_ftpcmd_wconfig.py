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
    system = System(conf)
    monkeypatch.setattr(subprocess, "run", system.run)
    monkeypatch.setattr(module, "HOME", home)
    monkeypatch.setattr(module, "JSON_PATH", conf)
    monkeypatch.setattr(module, "TIMESYNCD_CONF", timesyncd)
    monkeypatch.setattr(module, "ETH0_NETWORK", eth0, raising=False)
    monkeypatch.setattr(module, "MLAN0_NETWORK", mlan0, raising=False)
    monkeypatch.setattr(module.syslog, "syslog", lambda *args: None)
    return types.SimpleNamespace(module=module, home=home, conf=conf, timesyncd=timesyncd, eth0=eth0,
                                 mlan0=mlan0, system=system)


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
                     "LAN_MODE=static", "LAN_IPADDR=192.0.2.1", "LAN_NETMASK=255.255.255.0", "LAN_GATEWAY=0.0.0.0"):
        assert expected in written
    conf_before, ntp_before, eth0_before = env.conf.read_bytes(), env.timesyncd.read_bytes(), env.eth0.read_bytes()

    assert env.module.main(["apply"]) == 0

    assert env.conf.read_bytes() == conf_before
    assert env.timesyncd.read_bytes() == ntp_before
    assert env.eth0.read_bytes() == eth0_before
    assert one_line(capsys).startswith("SUCCESS applied=0 unchanged=8 ")
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


# --- wired LAN (eth0) ---------------------------------------------------------------

def network_lines(env, key):
    return [line for line in env.eth0.read_text().splitlines() if line.partition("=")[0].strip() == key]


def result_line(env, key):
    [line] = [line for line in result_lines(env) if line.split(" ", 1)[0] == key]
    return line


def test_get_reports_the_lan_values_of_the_eth0_network_file(env, capsys):
    assert env.module.main(["get"]) == 0
    assert one_line(capsys) == "SUCCESS wrote 8 keys to wconfig.txt"
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
