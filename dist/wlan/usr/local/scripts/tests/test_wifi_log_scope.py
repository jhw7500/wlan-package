"""`wifi log dump [all|today]` · `wifi log clear [today|all]` · `wifi log reset` (#462).

wifi.sh 를 실제로 실행한다. 로그 트리는 WIFI_LOG_BASE, logrotate 설정은
WIFI_LOGROTATE_CONF, journald 스냅샷 잠금은 WIFI_JOURNALD_LOCK 으로 옮기고,
date·systemctl·logger·sync 는 PATH 스텁으로 고정한다. 대상 목록의 기대값은
이 파일의 독립 파서(_conf_patterns)가 출하 logrotate.rsyslog 에서 만든다.
"""
import calendar
import fcntl
import fnmatch
import gzip
import os
import re
import shutil
import subprocess
import tarfile
import threading
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
WIFI = SCRIPTS / "wifi.sh"
EXTRACT = SCRIPTS / "wifi_log_extract.py"
WLAN_ROOT = SCRIPTS.parents[2]
LOGROTATE_CONF = WLAN_ROOT / "opt/wlan/config/logrotate.d/logrotate.rsyslog"
RSYSLOG_CONF = WLAN_ROOT / "opt/wlan/config/rsyslog.conf"
PREFIX = "/var/log/cantops/"

TODAY = "2026-11-08"
YESTERDAY = "2026-11-07"
TODAY_YMD = "20261108"
YESTERDAY_YMD = "20261107"
OLDER_YMD = "20261106"
# TZ=UTC 로 실행하므로 오늘 0시는 UTC 기준이다.
MIDNIGHT = calendar.timegm((2026, 11, 8, 0, 0, 0))
OLD_MTIME = MIDNIGHT - 12 * 3600
STAMP = "20261108_093000"

MAC_A = "02_00_00_00_00_01"
MAC_B = "02_00_00_00_00_02"
MAC_ORPHAN = "02_00_00_00_00_99"
ROTATED_RE = re.compile(r"-\d{8}(\.gz)?$")
SCRIPT_KEYWORDS = ("postrotate", "prerotate", "firstaction", "lastaction", "preremove")


def _conf_patterns(text):
    """logrotate 설정에서 /var/log/cantops/ 아래 경로 패턴을 고른다(시험 쪽 독립 구현)."""
    patterns, depth, in_script = [], 0, False
    for raw in text.splitlines():
        words = raw.split("#", 1)[0].replace("{", " { ").replace("}", " } ").split()
        if in_script:
            if words[:1] == ["endscript"]:
                in_script = False
            continue
        if depth and words[:1] and words[0] in SCRIPT_KEYWORDS:
            in_script = True
            continue
        for word in words:
            if word == "{":
                depth += 1
            elif word == "}":
                depth = max(depth - 1, 0)
            elif depth == 0 and word.startswith(PREFIX):
                rel = word[len(PREFIX):]
                if rel not in patterns:
                    patterns.append(rel)
    return patterns


def _line(day, clock, text):
    return f"{day} {clock}.000 {text}\n"


def _write(path, text, mtime=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode() if isinstance(text, str) else text
    if path.name.endswith(".gz"):
        data = gzip.compress(data)
    path.write_bytes(data)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def _exe(path, text):
    path.write_text(text)
    path.chmod(0o755)


def _snapshot(base):
    return {
        p.relative_to(base).as_posix(): p.read_bytes()
        for p in base.rglob("*")
        if p.is_file() and not p.is_symlink()
    }


def _current_targets(base, patterns):
    found = set()
    for pattern in patterns:
        for path in base.glob(pattern):
            if path.is_file() and not ROTATED_RE.search(path.name):
                found.add(path.relative_to(base).as_posix())
    return found


def _rotated_targets(rels, patterns):
    found = set()
    for rel in rels:
        match = ROTATED_RE.search(rel)
        if match and any(fnmatch.fnmatchcase(rel[: match.start()], p) for p in patterns):
            found.add(rel)
    return found


class Tree:
    def __init__(self, tmp_path, conf_text=None, conf_path=None):
        self.tmp = tmp_path
        self.base = tmp_path / "cantops"
        self.base.mkdir()
        self.out = tmp_path / "out"
        self.out.mkdir()
        self.calls = tmp_path / "calls"
        self.lock = tmp_path / "journald_snapshot.lock"
        self.patterns = []
        if conf_path is None:
            conf_path = tmp_path / "logrotate.rsyslog"
            conf_path.write_text(conf_text if conf_text is not None else LOGROTATE_CONF.read_text())
        self.conf = conf_path
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        real_date = shutil.which("date")
        _exe(bin_dir / "logger", "#!/bin/sh\nexit 0\n")
        _exe(bin_dir / "sync", "#!/bin/sh\nexit 0\n")
        _exe(bin_dir / "systemctl", f'#!/bin/sh\nprintf "systemctl %s\\n" "$*" >> "{self.calls}"\nexit 0\n')
        _exe(
            bin_dir / "date",
            "#!/bin/sh\n"
            'case "$*" in\n'
            f'  "+%F %Y%m%d_%H%M%S") echo "{TODAY} {STAMP}" ;;\n'
            f'  "+%Y%m%d_%H%M%S") echo "{STAMP}" ;;\n'
            f'  "+%F") echo "{TODAY}" ;;\n'
            f'  *) exec "{real_date}" "$@" ;;\n'
            "esac\n",
        )
        self.env = os.environ | {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "WIFI_RUN_DIR": str(tmp_path / "run"),
            "WIFI_LOG_BASE": str(self.base),
            "WIFI_LOGROTATE_CONF": str(self.conf),
            "WIFI_JOURNALD_LOCK": str(self.lock),
            "WIFI_LOG_EXTRACT_SCRIPT": str(EXTRACT),
            "TZ": "UTC",
        }

    def run(self, *argv):
        return subprocess.run(
            ["bash", str(WIFI), *argv],
            env=self.env,
            cwd=self.out,
            text=True,
            capture_output=True,
            timeout=60,
        )

    def hup_calls(self):
        if not self.calls.exists():
            return []
        return [c for c in self.calls.read_text().splitlines() if "HUP" in c]


def _populate(base, patterns):
    """출하 설정의 대상마다 현재 파일을 만들고, 회전본·journald·err·상태 파일을 둔다."""
    for pattern in patterns + ["ping.log"]:
        names = [pattern]
        if "*" in pattern:
            stem = pattern.rsplit("/", 1)[0]
            names = [f"{stem}/stat.log", f"{stem}/snap.log", f"{stem}/{MAC_A}.log", f"{stem}/{MAC_B}.log"]
        for rel in names:
            _write(base / rel, _line(YESTERDAY, "23:59:58", f"old {rel}") + _line(TODAY, "09:00:00", f"new {rel}"))
    block = (
        _line(YESTERDAY, "23:59:00", "[scan] old block") + "  old-cont-1\n"
        + _line(TODAY, "09:00:00", "[scan] new block") + "  new-cont-1\n  new-cont-2\n"
    )
    _write(base / "scan/mlan0/ap.log", block)
    _write(base / "scan/mlan1/ap.log", block.replace("[scan]", "[scan1]"))
    # 오늘 레코드가 없는 대상(쓰는 곳 없는 gmgmt.log) — dump today 에서 빠져야 한다
    _write(base / "mgmt/mlan1/gmgmt.log", _line(YESTERDAY, "23:59:58", "old only"))
    # 회전본: 오늘 회전한 평문과 .gz(전날 줄 + 자정 직후 줄), 옛 회전본, HUP 실패로 오늘도 쓰인 옛 이름 회전본
    _write(base / f"kern.log-{TODAY_YMD}", _line(YESTERDAY, "12:00:00", "kern yesterday") + _line(TODAY, "00:00:01", "kern straggler"))
    _write(base / f"radio.log-{TODAY_YMD}.gz", _line(YESTERDAY, "12:00:00", "radio yesterday") + _line(TODAY, "00:00:02", "radio straggler"))
    _write(base / f"kern.log-{YESTERDAY_YMD}.gz", _line("2026-11-06", "12:00:00", "kern older"), OLD_MTIME)
    _write(base / f"sys.log-{YESTERDAY_YMD}.gz", _line(TODAY, "08:00:00", "misdated but old copy"), OLD_MTIME)
    _write(base / f"logger.log-{YESTERDAY_YMD}", _line(YESTERDAY, "23:00:00", "logger yesterday") + _line(TODAY, "10:00:00", "logger after failed HUP"), MIDNIGHT + 10 * 3600)
    _write(base / f"stat/mlan1/{MAC_ORPHAN}.log-{YESTERDAY_YMD}.gz", _line(YESTERDAY, "12:00:00", "orphan ap"), OLD_MTIME)
    _write(base / f"stat/mlan0/{MAC_A}.log-{OLDER_YMD}.gz", _line("2026-11-05", "12:00:00", "mac older"), OLD_MTIME)
    # journald 스냅샷과 커서
    _write(base / "journald/.cursor", "s=cursor\n")
    _write(base / "journald/.cursor.bak", "s=cursor-bak\n")
    _write(base / f"journald/{OLDER_YMD}/journal.log.gz", f"{'2026-11-06'}T12:00:00+0000 host kernel: older\n", OLD_MTIME)
    _write(base / f"journald/{YESTERDAY_YMD}/journal.log.gz", f"{YESTERDAY}T12:00:00+0000 host kernel: yesterday\n", OLD_MTIME)
    _write(
        base / f"journald/{TODAY_YMD}/journal.log",
        f"{YESTERDAY}T23:59:59+0000 host kernel: before midnight\n"
        f"{TODAY}T09:00:00+0000 host wpa_supplicant[1]: mlan0: SME: Trying to authenticate\n",
    )
    # err/ 과 상태 파일, 대상이 아닌 파일
    _write(base / f"err/module_{TODAY_YMD}_090000.log", "[  1.0] dmesg\n")
    _write(base / f"err/module_{YESTERDAY_YMD}_120000.log", "[  2.0] dmesg\n")
    _write(base / "json/mlan0/link.json", '{"state": 1}\n')
    _write(base / "base_mac", "02:00:00:00:00:00\n")
    _write(base / ".fake-hwclock.data", "2026-11-08 09:00:00\n")
    _write(base / f"backup/cfg-{YESTERDAY_YMD}.tar.gz", b"backup")
    _write(base / "fw_watch.state", "ok\n")
    _write(base / "capture/x.pcap", b"pcap")
    _write(base / "other.log", _line(TODAY, "09:00:00", "not a target"))


@pytest.fixture
def shipped(tmp_path):
    patterns = _conf_patterns(LOGROTATE_CONF.read_text())
    tree = Tree(tmp_path)
    _populate(tree.base, patterns)
    tree.patterns = patterns
    return tree


def test_shipped_conf_parser_sees_known_targets():
    patterns = _conf_patterns(LOGROTATE_CONF.read_text())
    assert {"kern.log", "radio.log", "wpa/mlan0/wpa.log", "stat/mlan0/*.log", "stat/mlan1/*.log"} <= set(patterns)
    assert not any(p.startswith("/") or ".." in p.split("/") for p in patterns)


@pytest.mark.parametrize("argv", [["log", "clear", "today"], ["log", "clear"]])
def test_clear_today_empties_exactly_the_logrotate_targets_and_global_ping(shipped, argv):
    before = _snapshot(shipped.base)
    expected = _current_targets(shipped.base, shipped.patterns + ["ping.log"])
    assert {"radio.log", "ping.log", f"stat/mlan0/{MAC_A}.log", "stat/mlan1/snap.log"} <= expected

    cp = shipped.run(*argv)

    assert cp.returncode == 0, cp.stderr
    after = _snapshot(shipped.base)
    assert set(after) == set(before)
    emptied = {rel for rel in after if after[rel] == b"" and before[rel] != b""}
    assert emptied == expected
    assert all(after[rel] == before[rel] for rel in before if rel not in expected)
    assert shipped.hup_calls() == ["systemctl kill -s HUP rsyslog"]
    assert f"Cleared {len(expected)} log(s)" in cp.stdout


def test_clear_all_also_removes_rotated_copies_of_targets_only(shipped):
    before = _snapshot(shipped.base)
    rotated = _rotated_targets(before, shipped.patterns + ["ping.log"])
    assert {f"kern.log-{TODAY_YMD}", f"stat/mlan1/{MAC_ORPHAN}.log-{YESTERDAY_YMD}.gz"} <= rotated

    cp = shipped.run("log", "clear", "all")

    assert cp.returncode == 0, cp.stderr
    after = _snapshot(shipped.base)
    assert set(before) - set(after) == rotated
    current = _current_targets(shipped.base, shipped.patterns + ["ping.log"])
    assert all(after[rel] == b"" for rel in current)
    kept = set(before) - rotated - current
    assert all(after[rel] == before[rel] for rel in kept)
    assert f"backup/cfg-{YESTERDAY_YMD}.tar.gz" in kept and f"journald/{TODAY_YMD}/journal.log" in kept
    assert f"removed {len(rotated)} rotated file(s)" in cp.stdout
    assert shipped.hup_calls() == ["systemctl kill -s HUP rsyslog"]


def test_reset_also_removes_journald_snapshots_and_err_but_keeps_state(shipped):
    before = _snapshot(shipped.base)
    rotated = _rotated_targets(before, shipped.patterns + ["ping.log"])
    journald = {rel for rel in before if re.match(r"journald/\d{8}/", rel)}
    errs = {rel for rel in before if rel.startswith("err/")}
    state = {"journald/.cursor", "journald/.cursor.bak", "json/mlan0/link.json", "base_mac",
             ".fake-hwclock.data", f"backup/cfg-{YESTERDAY_YMD}.tar.gz", "fw_watch.state",
             "capture/x.pcap", "other.log"}

    cp = shipped.run("log", "reset")

    assert cp.returncode == 0, cp.stderr
    after = _snapshot(shipped.base)
    assert set(before) - set(after) == rotated | journald | errs
    assert all(after[rel] == before[rel] for rel in state)
    assert not any((shipped.base / "journald" / d).exists() for d in (OLDER_YMD, YESTERDAY_YMD, TODAY_YMD))
    assert all(after[rel] == b"" for rel in _current_targets(shipped.base, shipped.patterns + ["ping.log"]))
    assert "3 journald snapshot(s)" in cp.stdout and "2 err file(s)" in cp.stdout


def test_reset_waits_for_the_journald_snapshot_lock(shipped):
    fd = os.open(shipped.lock, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)

    def release():
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    timer = threading.Timer(1.5, release)
    timer.start()
    started = time.monotonic()
    cp = shipped.run("log", "reset")
    elapsed = time.monotonic() - started
    timer.join()

    assert cp.returncode == 0, cp.stderr
    assert elapsed >= 1.4
    assert not (shipped.base / "journald" / TODAY_YMD).exists()
    assert (shipped.base / "journald/.cursor").exists()


def test_target_list_follows_the_logrotate_conf(tmp_path):
    conf = (
        "# /var/log/cantops/commented.log\n"
        "/var/log/cantops/a.log /var/log/cantops/b.log\n"
        "/var/log/cantops/sub/c.log {\n"
        "    daily\n"
        "    postrotate\n"
        "        echo /var/log/cantops/in_script.log {\n"
        "    endscript\n"
        "}\n"
        "/var/log/other.log\n"
        "{\n    weekly\n}\n"
        "/var/log/cantops/glob/*.log\n"
        "{\n}\n"
    )
    tree = Tree(tmp_path, conf_text=conf)
    assert _conf_patterns(conf) == ["a.log", "b.log", "sub/c.log", "glob/*.log"]
    for rel in ["a.log", "b.log", "sub/c.log", "glob/x.log", "glob/y.log", "glob/z.txt",
                "ping.log", "commented.log", "in_script.log", "other.log"]:
        _write(tree.base / rel, _line(TODAY, "09:00:00", rel))
    before = _snapshot(tree.base)

    cp = tree.run("log", "clear", "today")

    assert cp.returncode == 0, cp.stderr
    after = _snapshot(tree.base)
    emptied = {rel for rel in after if after[rel] == b""}
    assert emptied == {"a.log", "b.log", "sub/c.log", "glob/x.log", "glob/y.log", "ping.log"}
    assert all(after[rel] == before[rel] for rel in before if rel not in emptied)


@pytest.mark.parametrize("conf_state", ["missing", "no-cantops-entry"])
@pytest.mark.parametrize("argv", [["log", "clear", "today"], ["log", "clear", "all"], ["log", "reset"], ["log", "dump", "today"]])
def test_unusable_conf_fails_without_touching_logs(tmp_path, conf_state, argv):
    if conf_state == "missing":
        tree = Tree(tmp_path, conf_path=tmp_path / "absent.conf")
    else:
        tree = Tree(tmp_path, conf_text="/var/log/other.log\n{\n    daily\n}\n")
    _populate(tree.base, _conf_patterns(LOGROTATE_CONF.read_text()))
    before = _snapshot(tree.base)

    cp = tree.run(*argv)

    assert cp.returncode != 0
    assert "log targets unavailable" in cp.stderr
    assert _snapshot(tree.base) == before
    assert tree.hup_calls() == []
    assert list(tree.out.iterdir()) == []


@pytest.mark.parametrize("argv", [["log", "dump"], ["log", "dump", "all"]])
def test_dump_and_dump_all_archive_the_whole_tree(shipped, argv):
    expected = {f"cantops/{rel}" for rel in _snapshot(shipped.base)}

    cp = shipped.run(*argv)

    assert cp.returncode == 0, cp.stderr
    archive = shipped.out / f"cantops_log_{STAMP}.tar.gz"
    with tarfile.open(archive) as tar:
        members = {m.name for m in tar.getmembers() if m.isfile()}
    assert members == expected


def _today_members(tree):
    cp = tree.run("log", "dump", "today")
    assert cp.returncode == 0, cp.stderr
    archive = tree.out / f"cantops_log_today_{STAMP}.tar.gz"
    assert str(archive) in cp.stdout
    members = {}
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            if member.isfile():
                reader = tar.extractfile(member)
                assert reader is not None
                members[member.name] = reader.read()
    return members


def test_dump_today_keeps_only_todays_records_with_their_paths(shipped):
    members = _today_members(shipped)

    current = _current_targets(shipped.base, shipped.patterns + ["ping.log"])
    assert "mgmt/mlan1/gmgmt.log" in current
    current -= {"mgmt/mlan1/gmgmt.log"}
    for rel in current - {"scan/mlan0/ap.log", "scan/mlan1/ap.log"}:
        assert members[f"cantops/{rel}"] == _line(TODAY, "09:00:00", f"new {rel}").encode()
    block = _line(TODAY, "09:00:00", "[scan] new block") + "  new-cont-1\n  new-cont-2\n"
    assert members["cantops/scan/mlan0/ap.log"] == block.encode()
    assert members["cantops/scan/mlan1/ap.log"] == block.replace("[scan]", "[scan1]").encode()
    assert members[f"cantops/kern.log-{TODAY_YMD}"] == _line(TODAY, "00:00:01", "kern straggler").encode()
    assert members[f"cantops/radio.log-{TODAY_YMD}"] == _line(TODAY, "00:00:02", "radio straggler").encode()
    assert members[f"cantops/journald/{TODAY_YMD}/journal.log"] == (
        f"{TODAY}T09:00:00+0000 host wpa_supplicant[1]: mlan0: SME: Trying to authenticate\n".encode()
    )
    expected_names = (
        {f"cantops/{rel}" for rel in current}
        | {f"cantops/kern.log-{TODAY_YMD}", f"cantops/radio.log-{TODAY_YMD}",
           f"cantops/logger.log-{YESTERDAY_YMD}", f"cantops/journald/{TODAY_YMD}/journal.log"}
    )
    assert set(members) == expected_names


def test_dump_today_reads_an_old_named_copy_still_written_today(shipped):
    members = _today_members(shipped)

    assert members[f"cantops/logger.log-{YESTERDAY_YMD}"] == _line(TODAY, "10:00:00", "logger after failed HUP").encode()
    assert f"cantops/sys.log-{YESTERDAY_YMD}" not in members


def test_dump_rejects_an_unknown_scope(shipped):
    cp = shipped.run("log", "dump", "week")

    assert cp.returncode != 0
    assert list(shipped.out.iterdir()) == []


def test_every_rsyslog_cantops_file_is_a_logrotate_target_or_global_ping():
    patterns = _conf_patterns(LOGROTATE_CONF.read_text())
    destinations = set()
    for raw in RSYSLOG_CONF.read_text().splitlines():
        line = raw.strip()
        if line.startswith("#"):
            continue
        destinations.update(m.group(1) for m in re.finditer(r'file="/var/log/cantops/([^"]+)"', line))
        match = re.search(r"\s-?/var/log/cantops/(\S+)\s*$", line)
        if match:
            destinations.add(match.group(1))
    assert {"radio.log", "kern.log", "wpa/mlan0/wpa.log", "ping.log"} <= destinations

    uncovered = sorted(
        d for d in destinations
        if d != "ping.log" and not any(fnmatch.fnmatchcase(d, p) for p in patterns)
    )
    assert uncovered == []
