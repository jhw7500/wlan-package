"""wpa.log lines go to a remote syslog server only through the file quote wconfig writes (#482).

rsyslog.conf reads /etc/rsyslog.d/*.conf after the wpa_supplicant blocks have stopped those
messages, so the forwarding action is included from inside the wpa.log block instead.  No file
means no forwarding: the package ships none, and factory reset deletes the one wconfig wrote.
"""

from pathlib import Path


DIST = Path(__file__).resolve().parents[4]
RSYSLOG_CONF = DIST / "opt/wlan/config/rsyslog.conf"
FACTORY_RESET = DIST / "usr/local/scripts/factory_reset.sh"
REPO = DIST.parents[1]
REMOTE = "/etc/rsyslog.d/wpa-remote.inc"
INCLUDE = f'include(file="{REMOTE}" mode="optional")'
FILTER_HEAD = "if $programname == 'wpa_supplicant' and ("
GUARD = "if $msg contains 'mlan0:' or $msg contains 'mlan1:' then {"


def index_of(lines, predicate, what):
    found = [number for number, line in enumerate(lines) if predicate(line)]
    assert len(found) == 1, f"{what}: lines {found}"
    return found[0]


def test_the_wpa_log_block_includes_the_remote_file_for_interface_lines_before_it_stops():
    lines = [line.strip() for line in RSYSLOG_CONF.read_text(encoding="utf-8").splitlines()]
    head = index_of(lines, lambda line: line == FILTER_HEAD, "wpa.log filter block")
    mlan1 = index_of(lines, lambda line: line.startswith("else if $msg contains 'mlan1:' then action(type=\"omfile\""),
                     "mlan1 wpa.log action")
    guard = index_of(lines, lambda line: line == GUARD, "interface guard")
    include = index_of(lines, lambda line: line == INCLUDE, "remote include")
    stop = next(number for number in range(include, len(lines)) if lines[number] == "stop")
    assert head < mlan1 < guard < include < stop
    assert lines[include + 1] == "}"


def test_the_remote_file_is_not_picked_up_by_the_rsyslog_d_glob():
    lines = RSYSLOG_CONF.read_text(encoding="utf-8").splitlines()
    assert "$IncludeConfig /etc/rsyslog.d/*.conf" in lines
    assert not REMOTE.endswith(".conf")


def test_the_package_no_longer_ships_an_rsyslog_d_forwarding_file():
    # A packaged file is rewritten on every install; the device file is wconfig's alone.
    assert not (DIST / "etc/rsyslog.d/10-udp-out.conf").exists()
    for manifest, entry in ((DIST / "DEBIAN/payload-manifest.txt", "etc/rsyslog.d/10-udp-out.conf"),
                            (REPO / "scripts/source_archive_manifest.txt", "dist/wlan/etc/rsyslog.d/10-udp-out.conf")):
        assert entry not in manifest.read_text(encoding="utf-8").splitlines()


def test_factory_reset_turns_remote_syslog_off_before_it_reboots():
    lines = [line.strip() for line in FACTORY_RESET.read_text(encoding="utf-8").splitlines()]
    remove = index_of(lines, lambda line: line == f"rm -f {REMOTE}", "remote file removal")
    reboot = index_of(lines, lambda line: line == "if ! systemctl reboot; then", "reboot")
    assert remove < reboot
