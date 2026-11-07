"""wpa.log 필터와 그 줄을 읽는 rlog 별칭을 실제 소비자로 확인한다(#459).

운영 `rsyslog.conf`의 wpa_supplicant 블록 두 개와 `$template myformat` 줄을 그대로
잘라 실제 rsyslogd에 먹이고, 출하 wpa_supplicant(imx8·imx93) 문자열로 만든 줄을
imuxsock 소켓으로 보낸다. 생긴 `wpa.log`는 `profile.d/00-cantops.sh`의 실제 rlog
별칭(bash)으로 읽는다. rsyslogd가 없거나 이 환경에서 소켓을 열지 못하면 사유를 남기고
건너뛴다. `rlogf` 시험은 rsyslogd 없이 돈다.
"""

from __future__ import annotations

import os
import shlex
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

WLAN_ROOT = Path(__file__).resolve().parents[4]
RSYSLOG_CONF = WLAN_ROOT / "opt/wlan/config/rsyslog.conf"
PROFILE = WLAN_ROOT / "etc/profile.d/00-cantops.sh"

FILTER_HEAD = "if $programname == 'wpa_supplicant' and ("
STOP_HEAD = "if $programname == 'wpa_supplicant' then {"
TEMPLATE_HEAD = "$template myformat,"
WPA_LOG_ROOT = "/var/log/cantops/wpa/"

# MAC·SSID는 자리표시 값이다(공개 저장소).
PEER = "02:00:00:00:00:01"
# 출하 바이너리의 연결 단계 문구. 종전 필터 키워드에는 하나도 걸리지 않는다.
NEW_MLAN0 = (
    f"mlan0: SME: Trying to authenticate with {PEER} (SSID='test-ssid' freq=5180 MHz)",
    f"mlan0: SME: Authentication response: peer={PEER} auth_type=0 "
    "auth_transaction=2 status_code=0",
    f"mlan0: Associated with {PEER}",
    "mlan0: SME: Authentication timed out",
    "mlan0: SME: Association timed out",
    f"mlan0: Authentication with {PEER} timed out.",
)
NEW_MLAN1 = ("mlan1: SME: Association timed out",)
# 종전 키워드로도 남던 줄.
KEPT_MLAN0 = (
    f"mlan0: CTRL-EVENT-CONNECTED - Connection to {PEER} completed [id=0 id_str=]",
)
# 남지 않아야 하는 줄: 인터페이스 접두가 없는 SME hexdump, 키워드 없는 디버그 줄.
DROPPED = (
    "SME: Association Request IEs - hexdump(len=3): 00 01 02",
    f"mlan0: BSS: Remove id 3 BSSID {PEER} SSID 'test-ssid' due to wpa_bss_flush_by_age",
)
SENTINEL = "end-of-wpa-filter-batch"
LINE_HEAD = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} wpa_supplicant\[info\] "


def _rsyslogd() -> str | None:
    found = shutil.which("rsyslogd")
    if found:
        return found
    for candidate in ("/usr/sbin/rsyslogd", "/sbin/rsyslogd"):
        if os.access(candidate, os.X_OK):
            return candidate
    return None


def _block(lines: list[str], head: str) -> list[str]:
    starts = [index for index, line in enumerate(lines) if line == head]
    assert len(starts) == 1, f"{head!r} 블록이 {len(starts)}개다"
    for end in range(starts[0], len(lines)):
        if lines[end] == "}":
            return lines[starts[0] : end + 1]
    raise AssertionError(f"{head!r} 블록의 닫는 중괄호가 없다")


def _harness_conf(work: Path) -> str:
    lines = RSYSLOG_CONF.read_text(encoding="utf-8").splitlines()
    templates = [line for line in lines if line.startswith(TEMPLATE_HEAD)]
    assert len(templates) == 1, templates
    body = "\n".join(_block(lines, FILTER_HEAD) + _block(lines, STOP_HEAD))
    assert WPA_LOG_ROOT in body
    body = body.replace(WPA_LOG_ROOT, f"{work}/wpa/")
    # 시험이 실제 로그 경로에 쓰지 않게 한다.
    assert "/var/log" not in body, body
    return "\n".join(
        [
            f'global(workDirectory="{work}")',
            'module(load="imuxsock" SysSock.Use="off")',
            f'input(type="imuxsock" Socket="{work}/log.sock")',
            templates[0],
            body,
            f'*.* action(type="omfile" file="{work}/rest.log" template="myformat")',
            "",
        ]
    )


def _wait(predicate, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def _send(sock: Path, tag: str, message: str) -> None:
    # journald가 서비스 stdout을 넘기는 것과 같은 daemon.info 로컬 syslog 형식.
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
        client.sendto(f"<30>{tag}[4242]: {message}".encode(), str(sock))


def _lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def _has(lines: list[str], message: str) -> bool:
    return any(line.endswith(" " + message) for line in lines)


@pytest.fixture(scope="module")
def filtered():
    rsyslogd = _rsyslogd()
    if rsyslogd is None:
        pytest.skip("rsyslogd가 없어 실제 필터 실행 시험을 건너뛴다")
    # Unix 소켓 경로는 108바이트 제한이 있어 TMPDIR이 길어도 짧은 /tmp 아래에 둔다.
    work = Path(tempfile.mkdtemp(prefix="rsy", dir="/tmp"))
    proc = None
    try:
        conf = work / "harness.conf"
        conf.write_text(_harness_conf(work), encoding="utf-8")
        check = subprocess.run(
            [rsyslogd, "-N1", "-f", str(conf)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert check.returncode == 0, check.stdout + check.stderr
        with open(work / "rsyslogd.out", "wb") as out:
            proc = subprocess.Popen(
                [rsyslogd, "-n", "-f", str(conf), "-i", str(work / "rsyslogd.pid")],
                stdout=out,
                stderr=subprocess.STDOUT,
            )
        sock = work / "log.sock"
        _wait(lambda: sock.is_socket() or proc.poll() is not None, 10)
        if not sock.is_socket():
            pytest.skip(
                "rsyslogd가 시험 소켓을 열지 못했다: "
                + (work / "rsyslogd.out").read_text(errors="replace")
            )
        for message in (*NEW_MLAN0, *NEW_MLAN1, *KEPT_MLAN0, *DROPPED):
            _send(sock, "wpa_supplicant", message)
        _send(sock, "sentinel", SENTINEL)
        rest = work / "rest.log"
        assert _wait(lambda: _has(_lines(rest), SENTINEL), 10), (
            "rsyslogd가 마지막 줄을 기록하지 않았다: "
            + (work / "rsyslogd.out").read_text(errors="replace")
        )
        proc.terminate()
        proc.wait(timeout=10)
        yield {
            "work": work,
            "mlan0": _lines(work / "wpa/mlan0/wpa.log"),
            "mlan1": _lines(work / "wpa/mlan1/wpa.log"),
            "rest": _lines(rest),
        }
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait()
        shutil.rmtree(work, ignore_errors=True)


def test_connection_phase_lines_reach_wpa_log(filtered):
    missing = [m for m in NEW_MLAN0 if not _has(filtered["mlan0"], m)]
    assert not missing, f"mlan0 wpa.log에 없는 줄: {missing}"
    assert not [m for m in NEW_MLAN0 if _has(filtered["mlan1"], m)]


def test_connection_phase_lines_route_by_interface(filtered):
    for message in NEW_MLAN1:
        assert _has(filtered["mlan1"], message), filtered["mlan1"]
        assert not _has(filtered["mlan0"], message)


def test_existing_and_unrelated_lines_keep_their_handling(filtered):
    import re

    for message in KEPT_MLAN0:
        assert _has(filtered["mlan0"], message), filtered["mlan0"]
    for message in DROPPED:
        for name in ("mlan0", "mlan1", "rest"):
            assert not _has(filtered[name], message), (name, message)
    # wpa_supplicant 줄은 모두 stop되어 다른 규칙으로 새지 않는다.
    assert not [line for line in filtered["rest"] if "wpa_supplicant" in line]
    # 운영 myformat 형식 그대로(WpaEventTracker가 앞 23자를 시각으로 읽는다).
    for line in filtered["mlan0"] + filtered["mlan1"]:
        assert re.match(LINE_HEAD, line), line


def _run_aliases(wpadir0: Path, command: str, path: str | None = None):
    if shutil.which("bash") is None:
        pytest.skip("bash가 없다")
    script = "\n".join(
        [
            "shopt -s expand_aliases",
            f". {shlex.quote(str(PROFILE))}",
            f"WPADIR0={shlex.quote(str(wpadir0))}",
            command,
        ]
    )
    env = dict(os.environ)
    if path is not None:
        env["PATH"] = path
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


def test_rlog_aliases_show_connection_phase_lines(filtered):
    wpadir0 = filtered["work"] / "wpa/mlan0"
    wanted = (NEW_MLAN0[0], NEW_MLAN0[2])  # Trying to authenticate with, Associated with
    for command in ("rlogt 100", "rlogh 100", f"rlogg {PEER}"):
        result = _run_aliases(wpadir0, command)
        lines = result.stdout.splitlines()
        missing = [m for m in wanted if not _has(lines, m)]
        assert not missing, (command, missing, result.stdout, result.stderr)


def test_rlogf_follows_wpa_log(tmp_path):
    wpadir0 = tmp_path / "wpa/mlan0"
    wpadir0.mkdir(parents=True)
    head = "2026-11-08 01:30:00.123 wpa_supplicant[info] "
    shown = (NEW_MLAN0[0], NEW_MLAN0[2])
    hidden = "mlan0: CTRL-EVENT-SCAN-STARTED "
    (wpadir0 / "wpa.log").write_text(
        "".join(f"{head}{m}\n" for m in (*shown, hidden)), encoding="utf-8"
    )
    # tail -f 를 파일을 한 번 읽고 끝나는 대역으로 바꾼다(따라가기는 시험하지 않는다).
    bindir = tmp_path / "bin"
    bindir.mkdir()
    shim = bindir / "tail"
    shim.write_text('#!/bin/sh\n[ "$1" = "-f" ] && shift\nexec cat -- "$@"\n')
    shim.chmod(0o755)
    result = _run_aliases(wpadir0, "rlogf", path=f"{bindir}:{os.environ.get('PATH', '')}")
    lines = result.stdout.splitlines()
    missing = [m for m in shown if not _has(lines, m)]
    assert not missing, (missing, result.stdout, result.stderr)
    assert not [line for line in lines if "CTRL-EVENT-SCAN-STARTED" in line]
