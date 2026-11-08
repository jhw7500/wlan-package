"""local4 → radio.log 라우팅을 실제 rsyslogd 로 확인한다(#459).

운영 `rsyslog.conf`에서 `$template myformat`, `^wifi_` 블록, sys.log 선택자, local0~7 파일
선택자를 그대로 잘라 rsyslogd 사본에 먹이고, 링크 로거가 보내는 것과 같은 데이터그램
(`<166>RADIO …`, local4.info)이 radio.log 에만 남는지 본다. rsyslogd 실행 방식(사본 실행,
부재 시 실패)과 공용 함수는 test_wpa_log_filter.py 를 따른다.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "_wpa_log_filter_harness", Path(__file__).resolve().parent / "test_wpa_log_filter.py")
_H = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_H)

LOG_ROOT = "/var/log/cantops/"
WIFI_HEAD = 'if re_match($programname, "^wifi_") then {'
RADIO_MSG = "mlan0 bssid=02:00:00:00:00:01 ch=36 bw=80 bcn=+50 miss=+0 cu=10%"
# (시설·등급 PRI, 태그와 본문). 마지막 user 줄은 모든 줄이 처리됐는지 기다리는 표식이다.
MESSAGES = (
    (166, "RADIO " + RADIO_MSG),                              # local4.info, 링크 로거 형식
    (134, "LINK [wifi_logger_link.py:1] sentinel-local0"),    # local0.info → logger.log
    (166, "wifi_radio sentinel-wifi-local4"),                 # local4 라도 ^wifi_ → logger.log
    (13, "other: sentinel-user"),                             # user.notice → sys.log
)


def _harness_conf(work: Path) -> str:
    lines = _H.RSYSLOG_CONF.read_text(encoding="utf-8").splitlines()
    templates = [line for line in lines if line.startswith(_H.TEMPLATE_HEAD)]
    assert len(templates) == 1, templates
    # 레거시 파일 선택자는 이 지시어로 myformat 을 쓴다(없으면 rsyslog 기본 파일 형식).
    defaults = [line for line in lines if line.startswith("$ActionFileDefaultTemplate ")]
    assert len(defaults) == 1, defaults
    starts = [i for i, line in enumerate(lines) if line.startswith("*.*;")]
    assert len(starts) == 1, starts
    end = starts[0]
    while lines[end].endswith("\\"):
        end += 1
    syslog_selector = lines[starts[0] : end + 1]
    assert "sys.log" in syslog_selector[-1], syslog_selector
    file_selectors = [line for line in lines if re.match(r"local[0-7]\.", line)]
    body = "\n".join(_H._block(lines, WIFI_HEAD) + syslog_selector + file_selectors)
    body = body.replace(LOG_ROOT, f"{work}/")
    # 시험이 실제 로그 경로에 쓰지 않게 한다.
    assert "/var/log" not in body, body
    return "\n".join(
        [
            f'global(workDirectory="{work}")',
            'module(load="imuxsock" SysSock.Use="off")',
            f'input(type="imuxsock" Socket="{work}/log.sock")',
            templates[0],
            defaults[0],
            body,
            "",
        ]
    )


@pytest.fixture(scope="module")
def routed():
    rsyslogd = _H._rsyslogd()
    if rsyslogd is None:
        pytest.fail("rsyslogd가 없다 — 이 시험은 실제 rsyslogd로 라우팅을 확인한다(rsyslog 설치 필요)")
    # Unix 소켓 경로 108바이트 제한 때문에 짧은 /tmp 아래에 둔다.
    work = Path(tempfile.mkdtemp(prefix="rsr", dir="/tmp"))
    proc = None
    try:
        # AppArmor 프로파일은 /usr/sbin/rsyslogd 경로에 붙으므로 사본을 실행한다(test_wpa_log_filter.py).
        binary = work / "rsyslogd"
        shutil.copy2(rsyslogd, binary)
        conf = work / "harness.conf"
        conf.write_text(_harness_conf(work), encoding="utf-8")
        check = subprocess.run(
            [str(binary), "-N1", "-f", str(conf)], capture_output=True, text=True, timeout=30)
        assert check.returncode == 0, check.stdout + check.stderr
        with open(work / "rsyslogd.out", "wb") as out:
            proc = subprocess.Popen(
                [str(binary), "-n", "-f", str(conf), "-i", str(work / "rsyslogd.pid")],
                stdout=out, stderr=subprocess.STDOUT)
        sock = work / "log.sock"
        _H._wait(lambda: sock.is_socket() or proc.poll() is not None, 10)
        if not sock.is_socket():
            pytest.fail("rsyslogd가 시험 소켓을 열지 못했다: "
                        + (work / "rsyslogd.out").read_text(errors="replace"))
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
            for pri, text in MESSAGES:
                client.sendto(f"<{pri}>{text}".encode(), str(sock))
        files = {name: work / name for name in ("radio.log", "logger.log", "sys.log")}
        _H._wait(lambda: _H._has(_H._lines(files["sys.log"]), "sentinel-user")
                 and _H._has(_H._lines(files["logger.log"]), "sentinel-wifi-local4"), 10)
        proc.terminate()
        proc.wait(timeout=10)
        yield {name: _H._lines(path) for name, path in files.items()}
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait()
        shutil.rmtree(work, ignore_errors=True)


def test_radio_line_lands_only_in_radio_log(routed):
    assert [line for line in routed["radio.log"] if line.endswith(" " + RADIO_MSG)], routed
    assert not [line for line in routed["sys.log"] if RADIO_MSG in line]
    assert not [line for line in routed["logger.log"] if RADIO_MSG in line]
    # 운영 myformat 형식: 날짜 시각 RADIO[info] 본문
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} RADIO\[info\] " + re.escape(RADIO_MSG),
                        routed["radio.log"][0]), routed["radio.log"]


def test_existing_routes_are_unchanged(routed):
    assert _H._has(routed["logger.log"], "sentinel-local0")
    assert not [line for line in routed["sys.log"] if "sentinel-local0" in line]
    assert _H._has(routed["sys.log"], "sentinel-user")
    # 태그가 wifi_ 로 시작하면 local4 라도 ^wifi_ 규칙이 logger.log 로 보내고 멈춘다.
    assert _H._has(routed["logger.log"], "sentinel-wifi-local4")
    assert not [line for line in routed["radio.log"] if "sentinel-wifi-local4" in line]
