"""`wifi <if> log reset|cp|extract|compress` 의 대상·종료 코드와 `wifi <if> stat reset` 플래그(#494).

wifi.sh 를 실제로 실행한다. 로그 트리는 WIFI_LOG_BASE, 통계 초기화 플래그는 WIFI_STAT_FLAG_DIR,
변경 가드 잠금은 WIFI_RUN_DIR 로 옮기고 systemctl·logger·sync 는 PATH 스텁으로 고정한다.
성공(일부 파일 없음 포함)은 0, 실패는 0 이 아니어야 한다. 실패 시험은 파일 하나를 빼 둔 채
돌려, 마지막 `Skipped …` 출력의 성공 상태가 실패를 덮지 못하는지도 본다.
"""
import os
import subprocess
import tarfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
WIFI = SCRIPTS / "wifi.sh"
EXTRACT = SCRIPTS / "wifi_log_extract.py"
DAY = "2026-11-08"

# wifi.sh 의 iface `log` 분기가 고르는 목록과 같은 순서다(cp/extract/compress, reset).
COLLECT = ["cpu/cpu.log", "logger.log", "kern.log", "sys.log", "summary/summary.log",
           "scan/{i}/ap.log", "scan/{i}/freq.log", "stat/{i}/stat.log", "stat/{i}/snap.log",
           "wpa/{i}/wpa.log"]
RESET = ["scan/{i}/ap.log", "scan/{i}/freq.log", "stat/{i}/stat.log", "stat/{i}/snap.log",
         "wpa/{i}/wpa.log", "mgmt/{i}/mgmt.log", "mgmt/{i}/gmgmt.log", "ping/{i}/ping.log"]
OLD_AP_LOG = "stat/{i}/02_00_00_00_00_0a.log"

needs_non_root = pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")


def _exe(path, text):
    path.write_text(text)
    path.chmod(0o755)


class Wifi:
    def __init__(self, tmp_path):
        self.base = tmp_path / "cantops"
        self.flags = tmp_path / "flags"
        self.out = tmp_path / "out"
        self.calls = tmp_path / "calls"
        for d in (self.base, self.flags, self.out):
            d.mkdir()
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _exe(bin_dir / "logger", "#!/bin/sh\nexit 0\n")
        _exe(bin_dir / "sync", "#!/bin/sh\nexit 0\n")
        _exe(bin_dir / "systemctl", f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{self.calls}"\nexit 0\n')
        self.env = os.environ | {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "WIFI_RUN_DIR": str(tmp_path / "run"),
            "WIFI_LOG_BASE": str(self.base),
            "WIFI_STAT_FLAG_DIR": str(self.flags),
            "WIFI_LOG_EXTRACT_SCRIPT": str(EXTRACT),
            "TZ": "UTC",
        }

    def populate(self, rels, iface="mlan0"):
        for rel in rels:
            path = self.base / rel.format(i=iface)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{DAY} 09:00:00 {rel.format(i=iface)}\n{DAY} 11:00:00 later\n")

    def path(self, rel, iface="mlan0"):
        return self.base / rel.format(i=iface)

    def run(self, *argv):
        return subprocess.run(["bash", str(WIFI), *argv], env=self.env, cwd=self.out,
                              text=True, capture_output=True, timeout=60)


@pytest.fixture
def wifi(tmp_path):
    w = Wifi(tmp_path)
    w.populate(sorted(set(COLLECT + RESET + [OLD_AP_LOG])))
    w.populate(RESET, iface="mlan1")
    return w


def test_log_reset_empties_only_the_iface_logs(wifi):
    cp = wifi.run("mlan0", "log", "reset")

    assert cp.returncode == 0, cp.stderr
    assert all(wifi.path(rel).read_text() == "" for rel in RESET)
    assert wifi.path(OLD_AP_LOG).read_text() != ""
    assert all(wifi.path(rel, "mlan1").read_text() != "" for rel in RESET)
    assert wifi.path("kern.log").read_text() != ""
    assert f"Reset {len(RESET)} log(s) for mlan0" in cp.stdout
    assert "kill -s HUP rsyslog" in wifi.calls.read_text()


def test_log_reset_mac_target_is_rejected(wifi):
    cp = wifi.run("mlan0", "log", "reset", "mac")

    assert cp.returncode == 2
    assert "invalid log reset target 'mac'" in cp.stderr
    assert wifi.path(OLD_AP_LOG).read_text() != ""
    assert all(wifi.path(rel).read_text() != "" for rel in RESET)


def test_log_reset_with_missing_files_still_succeeds(wifi):
    wifi.path("mgmt/{i}/gmgmt.log").unlink()
    wifi.path("ping/{i}/ping.log").unlink()

    cp = wifi.run("mlan0", "log", "reset")

    assert cp.returncode == 0, cp.stderr
    assert "Skipped 2 missing: gmgmt.log ping.log" in cp.stdout


@needs_non_root
def test_log_reset_fails_when_a_truncate_fails(wifi):
    wifi.path("ping/{i}/ping.log").unlink()
    wifi.path("wpa/{i}/wpa.log").chmod(0o444)

    cp = wifi.run("mlan0", "log", "reset")

    assert cp.returncode != 0
    assert "Warning: truncate failed" in cp.stderr
    assert wifi.path("stat/{i}/stat.log").read_text() == ""
    assert "Skipped 1 missing: ping.log" in cp.stdout


def test_log_cp_exit_codes(wifi):
    full = wifi.run("mlan0", "log", "cp", "all")
    assert full.returncode == 0, full.stderr
    assert f"Copied {len(COLLECT)} log(s) for mlan0 to" in full.stdout
    assert sorted(p.name for p in (wifi.out / "all").iterdir()) == sorted(Path(r).name for r in COLLECT)

    wifi.path("kern.log").unlink()
    some = wifi.run("mlan0", "log", "cp", "some")
    assert some.returncode == 0, some.stderr
    assert "Skipped 1 missing: kern.log" in some.stdout


@needs_non_root
def test_log_cp_fails_when_a_copy_fails(wifi):
    wifi.path("kern.log").unlink()
    wifi.path("sys.log").chmod(0)

    cp = wifi.run("mlan0", "log", "cp", "broken")

    assert cp.returncode != 0
    assert "Warning: copy failed" in cp.stderr
    assert "Error: 1 log(s) failed to copy for mlan0" in cp.stderr
    assert f"Copied {len(COLLECT) - 2} log(s) for mlan0 to" in cp.stdout
    assert "Skipped 1 missing: kern.log" in cp.stdout


def test_log_extract_exit_codes(wifi):
    full = wifi.run("mlan0", "log", "extract", f"{DAY} 09:00", f"{DAY} 10:00", "x1")
    assert full.returncode == 0, full.stderr
    assert (wifi.out / "x1" / "kern.log").read_text() == f"{DAY} 09:00:00 kern.log\n"

    wifi.path("kern.log").unlink()
    some = wifi.run("mlan0", "log", "extract", f"{DAY} 09:00", f"{DAY} 10:00", "x2")
    assert some.returncode == 0, some.stderr
    assert "Skipped 1 missing: kern.log" in some.stdout

    bad = wifi.run("mlan0", "log", "extract", "25:99", "26:00", "x3")
    assert bad.returncode != 0


def test_log_compress_exit_codes(wifi):
    full = wifi.run("mlan0", "log", "compress")
    assert full.returncode == 0, full.stderr
    (archive,) = wifi.out.glob("mlan0_log_*.tar.gz")
    with tarfile.open(archive) as tar:
        assert sorted(tar.getnames()) == sorted(rel.format(i="mlan0") for rel in COLLECT)
    archive.unlink()

    wifi.path("kern.log").unlink()
    some = wifi.run("mlan0", "log", "compress")
    assert some.returncode == 0, some.stderr
    assert "Skipped 1 missing: kern.log" in some.stdout


@needs_non_root
def test_log_compress_fails_when_tar_fails(wifi):
    wifi.path("kern.log").unlink()
    wifi.path("sys.log").chmod(0)

    cp = wifi.run("mlan0", "log", "compress")

    assert cp.returncode != 0
    assert "Error: tar failed" in cp.stderr
    assert list(wifi.out.glob("*.tar.gz")) == []


@pytest.mark.parametrize("iface", ["mlan0", "mlan1"])
def test_stat_reset_without_mac_flags_every_ap_of_that_iface(tmp_path, iface):
    wifi = Wifi(tmp_path)

    cp = wifi.run(iface, "stat", "reset")

    assert cp.returncode == 0, cp.stderr
    assert [p.name for p in wifi.flags.iterdir()] == [f"wifi_stat_init_f_{iface}"]
    assert cp.stdout.strip() == f"stat reset requested for all APs on {iface}"


def test_stat_reset_mac_flag_is_lower_case(tmp_path):
    wifi = Wifi(tmp_path)

    cp = wifi.run("mlan0", "stat", "reset", "02:00:00:00:00:0A")

    assert cp.returncode == 0, cp.stderr
    assert [p.name for p in wifi.flags.iterdir()] == ["wifi_stat_reset_02:00:00:00:00:0a"]


def test_stat_reset_rejects_a_bad_mac(tmp_path):
    wifi = Wifi(tmp_path)

    cp = wifi.run("mlan0", "stat", "reset", "02:00:00:00:00")

    assert cp.returncode == 1
    assert list(wifi.flags.iterdir()) == []


@pytest.mark.parametrize("argv", [["stat", "reset"], ["stat", "reset", "02:00:00:00:00:0a"],
                                  ["stat", "interval", "60"]])
def test_stat_commands_reject_an_iface_without_a_stat_logger(tmp_path, argv):
    wifi = Wifi(tmp_path)
    conf = tmp_path / "wifi_init_conf.json"
    conf.write_text('{"eth0": {}}\n')
    wifi.env["WIFI_INIT_CONF_JSON"] = str(conf)

    cp = wifi.run("eth0", *argv)

    assert cp.returncode == 1
    assert "stat reset/interval supports mlan0/mlan1 only" in cp.stderr
    assert list(wifi.flags.iterdir()) == []
    assert conf.read_text() == '{"eth0": {}}\n'
