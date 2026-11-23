"""#494: stat 로거는 stat.log 에만 쓰고, AP별 누적은 로거 프로세스 메모리에서 이어 센다.

실제 log_stats() 루프를 가짜 링크 정보·시계로 돌린다. 링크 정보는 연결(같은 BSSID 가 이어지는
구간)마다 드라이버 카운터가 다시 세는 것처럼 만든다 — 연결 k 번째 표본은 rx/tx 1000·k 바이트,
10·k 패킷, tx_failed k. None 은 끊김(링크 정보 없음)이다. 로거가 보는 /sys/class/net/<iface> 는
있다고 답하고, /tmp/wifi_stat_* 플래그는 시험 디렉터리로 돌려 호스트 /tmp 를 건드리지 않는다.
"""
import os
import re
import sys
import time as real_time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# sUTILS pulls heavy runtime deps (paho, serial); stub before importing the module.
sys.modules.setdefault("sUTILS", MagicMock())
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wifi_logger_stat as stat  # noqa: E402

A = "02:00:00:00:00:0a"
B = "02:00:00:00:00:0b"

STAT_LINE = re.compile(
    r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} MAC:(?P<mac>[0-9A-Fa-f:]+), BW:80 MHz, "
    r"RSSI:-50dBm\(min:-50/max:-50\), "
    r"RX:(?P<rx>\d+)byte/(?P<rx_pkt>\d+)pkt/100\.0Mbps/[\d.]+Mbps, "
    r"TX:(?P<tx>\d+)byte/(?P<tx_pkt>\d+)pkt/100\.0Mbps/[\d.]+Mbps, "
    r"FAIL:(?P<fail>\d+), RETRY:0\(0\.0%\), T:(?P<t>\d+)"
)
STAMP = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} "


class _Done(Exception):
    pass


def _os_proxy(iface, flags):
    sysfs = f"/sys/class/net/{iface}"

    def real(path):
        if os.path.dirname(path) == "/tmp" and os.path.basename(path).startswith("wifi_stat_"):
            return str(flags / os.path.basename(path))
        return path

    path = SimpleNamespace(exists=lambda p: p == sysfs or os.path.exists(real(p)))

    class _Os:
        def __getattr__(self, name):
            return getattr(os, name)

    proxy = _Os()
    proxy.path = path
    proxy.remove = lambda p: os.remove(real(p))
    return proxy


def run_logger(monkeypatch, tmp_path, seq, iface="mlan0", before=lambda i, flags: None):
    """seq 의 BSSID(None=끊김)를 차례로 링크 정보로 내며 log_stats() 를 돈다.

    before(i, flags) 는 i 번째 표본을 내기 직전에 불린다. 표본이 다 떨어지면 루프를 멈춘다.
    """
    logdir = tmp_path / "stat"
    logdir.mkdir()
    flags = tmp_path / "flags"
    flags.mkdir()
    clock = [100.0]
    calls = [0]
    conn = {"ap": None, "k": 0}

    def link(_path):
        i = calls[0]
        calls[0] += 1
        if i >= len(seq):
            raise _Done
        before(i, flags)
        ap = seq[i]
        if ap is None:
            conn["ap"] = None
            return None
        if conn["ap"] != ap:
            conn.update(ap=ap, k=0)
        conn["k"] += 1
        k = conn["k"]
        levels = stat.signal_levels.setdefault(ap, {"min": float("inf"), "max": float("-inf")})
        levels["min"] = min(levels["min"], -50)
        levels["max"] = max(levels["max"], -50)
        return {
            "ap_mac": ap, "bw": "80 MHz", "rssi": -50,
            "rssi_min": levels["min"], "rssi_max": levels["max"],
            "tx_fail": k, "tx_bytes": 1000 * k, "tx_packets": 10 * k, "tx_bitrate": 100.0,
            "rx_bytes": 1000 * k, "rx_packets": 10 * k, "rx_bitrate": 100.0,
        }

    for name in ("signal_levels", "prev_stat", "last_stat", "tx_retrys"):
        monkeypatch.setattr(stat, name, {})
    monkeypatch.setattr(stat, "current_ap", "")
    monkeypatch.setattr(stat, "last_log_time", 0.0)
    monkeypatch.setattr(stat, "log_interval", 1)
    monkeypatch.setattr(stat, "check_interval", 1)
    monkeypatch.setattr(stat, "prev_retry_count", None)
    monkeypatch.setattr(stat, "prev_tx_frame_count", None)
    monkeypatch.setattr(stat, "LOG_DIR", str(logdir))
    monkeypatch.setattr(stat, "IFACE", iface)
    monkeypatch.setattr(stat, "logger", MagicMock(), raising=False)
    monkeypatch.setattr(stat, "get_mlanutl_log", lambda _iface: "")
    monkeypatch.setattr(stat, "get_link_info", link)
    monkeypatch.setattr(stat, "os", _os_proxy(iface, flags))
    monkeypatch.setattr(stat, "time", SimpleNamespace(
        time=lambda: clock[0],
        sleep=lambda s: clock.__setitem__(0, clock[0] + s),
        strftime=real_time.strftime,
    ))
    with pytest.raises(_Done):
        stat.log_stats()
    lines = (logdir / "stat.log").read_text().splitlines()
    return SimpleNamespace(totals=stat.last_stat, logdir=logdir, flags=flags, lines=lines)


def _stat_lines(lines, mac):
    found = [STAT_LINE.fullmatch(line) for line in lines]
    return [m.groupdict() for m in found if m and m["mac"] == mac]


def _touch(path):
    path.write_text("")


def test_writes_only_stat_log_and_continues_ap_totals_across_a_b_a(monkeypatch, tmp_path):
    run = run_logger(monkeypatch, tmp_path, [A] * 5 + [B] * 3 + [A] * 2)

    assert sorted(os.listdir(run.logdir)) == ["stat.log"]
    # A 로 돌아온 연결은 드라이버 카운터가 1 부터 다시 세지만 A 의 누적은 앞 연결에서 이어진다:
    # 바이트·패킷은 4000+1000, 시간은 5+2 초, FAIL 은 앞 연결의 5 에 이번 연결의 2 를 더한다.
    a = run.totals[A]
    assert (a["rx_bytes"], a["rx_packets"], a["tx_bytes"], a["tx_packets"]) == (5000, 50, 5000, 50)
    assert (a["tx_fail"], a["time"]) == (7, 7)
    assert (run.totals[B]["rx_bytes"], run.totals[B]["tx_fail"], run.totals[B]["time"]) == (2000, 3, 3)
    # stat.log 줄 형식은 그대로다: 통계 줄 10개와 AP changed 줄 3개뿐이다.
    changes = [line for line in run.lines if re.fullmatch(STAMP + r"AP changed: .*", line)]
    assert [c.split("AP changed: ")[1] for c in changes] == [f"None -> {A}", f"{A} -> {B}", f"{B} -> {A}"]
    assert len(run.lines) == 13 and len(_stat_lines(run.lines, A)) + len(_stat_lines(run.lines, B)) == 10
    last = _stat_lines(run.lines, A)[-1]
    assert (last["rx"], last["rx_pkt"], last["fail"], last["t"]) == ("5000", "50", "7", "6")


@pytest.mark.parametrize("iface", ["mlan0", "mlan1"])
def test_iface_init_flag_resets_current_and_disconnected_aps_once(monkeypatch, tmp_path, iface):
    def flag_on_b(i, flags):
        if i == 6:  # B 연결 중, A 는 끊겨 있다
            _touch(flags / f"wifi_stat_init_f_{iface}")

    run = run_logger(monkeypatch, tmp_path, [A] * 5 + [B] * 3 + [A] * 2, iface=iface,
                     before=flag_on_b)

    assert list(run.flags.iterdir()) == []
    inits = [line for line in run.lines if "stat init!!" in line]
    assert len(inits) == 1 and re.fullmatch(STAMP + f"MAC:{B} stat init!!", inits[0])
    # 끊겨 있던 A 도 0 부터 다시 센다: 돌아온 연결의 두 표본만 남는다.
    a = run.totals[A]
    assert (a["rx_bytes"], a["rx_packets"], a["tx_fail"], a["time"]) == (1000, 10, 2, 2)
    b = run.totals[B]
    assert (b["rx_bytes"], b["time"]) == (1000, 1)
    assert sorted(os.listdir(run.logdir)) == ["stat.log"]


@pytest.mark.parametrize("iface, other", [("mlan0", "mlan1"), ("mlan1", "mlan0")])
def test_init_flag_of_the_other_iface_is_left_alone(monkeypatch, tmp_path, iface, other):
    def other_flag(i, flags):
        if i == 6:
            _touch(flags / f"wifi_stat_init_f_{other}")

    run = run_logger(monkeypatch, tmp_path, [A] * 5 + [B] * 3 + [A] * 2, iface=iface,
                     before=other_flag)

    assert [p.name for p in run.flags.iterdir()] == [f"wifi_stat_init_f_{other}"]
    assert not [line for line in run.lines if "stat init!!" in line]
    assert (run.totals[A]["rx_bytes"], run.totals[B]["rx_bytes"]) == (5000, 2000)


def test_init_flag_made_while_disconnected_applies_at_the_next_connection(monkeypatch, tmp_path):
    seen = []

    def flag_while_down(i, flags):
        if i == 3:
            _touch(flags / "wifi_stat_init_f_mlan0")
        if i == 5:  # 다시 연결되기 직전까지 플래그는 남아 있다
            seen.append((flags / "wifi_stat_init_f_mlan0").exists())

    run = run_logger(monkeypatch, tmp_path, [A] * 3 + [None] * 2 + [A] * 2, before=flag_while_down)

    assert seen == [True]
    assert list(run.flags.iterdir()) == []
    assert (run.totals[A]["rx_bytes"], run.totals[A]["time"]) == (1000, 1)


@pytest.mark.parametrize("link_mac", [A, A.upper()], ids=["link-lower", "link-upper"])
def test_mac_reset_flag_is_looked_up_in_lower_case(monkeypatch, tmp_path, link_mac):
    def mac_flag(i, flags):
        if i == 3:
            _touch(flags / f"wifi_stat_reset_{A}")

    run = run_logger(monkeypatch, tmp_path, [link_mac] * 5, before=mac_flag)

    assert list(run.flags.iterdir()) == []
    assert [line for line in run.lines if "stat init!!" in line][0].endswith(f"MAC:{link_mac} stat init!!")
    assert (run.totals[link_mac]["rx_bytes"], run.totals[link_mac]["time"]) == (1000, 1)


def test_daily_rotation_flags_both_iface_loggers():
    # 매일 회전(logrotate stat 블록 postrotate)이 로거가 찾는 iface 플래그를 mlan0·mlan1 모두 만든다.
    conf = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "opt", "wlan", "config",
                        "logrotate.d", "logrotate.rsyslog")
    with open(conf, encoding="utf-8") as f:
        text = f.read()
    block = re.search(r"/var/log/cantops/stat/mlan0/\*\.log\n.*?\n}", text, re.S).group(0)
    script = re.search(r"postrotate\n(.*?)\n\s*endscript", block, re.S).group(1)
    touched = [arg for line in script.splitlines() if line.split()[:1] == ["touch"]
               for arg in line.split()[1:]]
    assert touched == ["/tmp/wifi_stat_init_f_mlan0", "/tmp/wifi_stat_init_f_mlan1"]
