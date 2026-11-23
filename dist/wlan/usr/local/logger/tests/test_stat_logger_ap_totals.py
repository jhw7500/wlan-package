"""#494: stat 로거는 stat.log 에만 쓰고, AP별 누적은 로거 프로세스 메모리에서 이어 센다.

실제 log_stats() 루프를 가짜 링크 정보·시계로 돌린다. 링크 정보는 연결(같은 BSSID 가 이어지는
구간)마다 드라이버 카운터가 다시 세는 것처럼 만든다 — 연결 k 번째 표본은 rx/tx 1000·k 바이트,
10·k 패킷, tx_failed k. None 은 끊김(링크 정보 없음)이다. sample() 로 만든 표본은 드라이버 카운터를
그대로 준다(#497). 로거가 보는 /sys/class/net/<iface> 는 있다고 답하고, /tmp/wifi_stat_* 플래그는
시험 디렉터리로 돌려 호스트 /tmp 를 건드리지 않는다.
"""
import json
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
COUNTERS = ("tx_fail", "tx_bytes", "tx_packets", "rx_bytes", "rx_packets")


def sample(ap, nbytes, pkts, fail, bw="80 MHz"):
    """드라이버 카운터를 그대로 주는 표본: rx·tx 바이트 nbytes, rx·tx 패킷 pkts, tx_failed fail.
    값이 None 인 카운터는 링크 정보에 없는(모르는) 값이다."""
    return {"ap": ap, "bw": bw, "tx_fail": fail, "tx_bytes": nbytes, "tx_packets": pkts,
            "rx_bytes": nbytes, "rx_packets": pkts}


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
        if isinstance(ap, dict):  # sample(): 드라이버 카운터를 그대로 준다
            item, ap = ap, ap["ap"]
            counters = {key: item[key] for key in COUNTERS}
            bw = item["bw"]
        else:
            if conn["ap"] != ap:
                conn.update(ap=ap, k=0)
            conn["k"] += 1
            k = conn["k"]
            counters = {"tx_fail": k, "tx_bytes": 1000 * k, "tx_packets": 10 * k,
                        "rx_bytes": 1000 * k, "rx_packets": 10 * k}
            bw = "80 MHz"
        levels = stat.signal_levels.setdefault(ap, {"min": float("inf"), "max": float("-inf")})
        levels["min"] = min(levels["min"], -50)
        levels["max"] = max(levels["max"], -50)
        return {
            "ap_mac": ap, "bw": bw, "rssi": -50,
            "rssi_min": levels["min"], "rssi_max": levels["max"],
            "tx_bitrate": 100.0, "rx_bitrate": 100.0, **counters,
        }

    log = MagicMock()
    for name in ("signal_levels", "prev_stat", "last_stat", "tx_retrys"):
        monkeypatch.setattr(stat, name, {})
    monkeypatch.setattr(stat, "bw_skip_streak", 0, raising=False)
    monkeypatch.setattr(stat, "current_ap", "")
    monkeypatch.setattr(stat, "last_log_time", 0.0)
    monkeypatch.setattr(stat, "log_interval", 1)
    monkeypatch.setattr(stat, "check_interval", 1)
    monkeypatch.setattr(stat, "prev_retry_count", None)
    monkeypatch.setattr(stat, "prev_tx_frame_count", None)
    monkeypatch.setattr(stat, "LOG_DIR", str(logdir))
    monkeypatch.setattr(stat, "IFACE", iface)
    monkeypatch.setattr(stat, "logger", log, raising=False)
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
    return SimpleNamespace(totals=stat.last_stat, logdir=logdir, flags=flags, lines=lines, log=log)


def _stat_lines(lines, mac):
    found = [STAT_LINE.fullmatch(line) for line in lines]
    return [m.groupdict() for m in found if m and m["mac"] == mac]


def _touch(path):
    path.write_text("")


def test_writes_only_stat_log_and_continues_ap_totals_across_a_b_a(monkeypatch, tmp_path):
    run = run_logger(monkeypatch, tmp_path, [A] * 5 + [B] * 3 + [A] * 2)

    assert sorted(os.listdir(run.logdir)) == ["stat.log"]
    # A 로 돌아온 연결은 드라이버 카운터가 1 부터 다시 세지만 A 의 누적은 앞 연결에서 이어진다:
    # 바이트·패킷은 4000+1000, 시간은 5+2 초. FAIL 도 같은 식으로 증가분만 더한다(#497) — 각 연결의
    # 첫 표본은 기준이라 A 는 4+1, B 는 2 다.
    a = run.totals[A]
    assert (a["rx_bytes"], a["rx_packets"], a["tx_bytes"], a["tx_packets"]) == (5000, 50, 5000, 50)
    assert (a["tx_fail"], a["time"]) == (5, 7)
    assert (run.totals[B]["rx_bytes"], run.totals[B]["tx_fail"], run.totals[B]["time"]) == (2000, 2, 3)
    # stat.log 줄 형식은 그대로다: 통계 줄 10개와 AP changed 줄 3개뿐이다.
    changes = [line for line in run.lines if re.fullmatch(STAMP + r"AP changed: .*", line)]
    assert [c.split("AP changed: ")[1] for c in changes] == [f"None -> {A}", f"{A} -> {B}", f"{B} -> {A}"]
    assert len(run.lines) == 13 and len(_stat_lines(run.lines, A)) + len(_stat_lines(run.lines, B)) == 10
    last = _stat_lines(run.lines, A)[-1]
    assert (last["rx"], last["rx_pkt"], last["fail"], last["t"]) == ("5000", "50", "5", "6")


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
    # 끊겨 있던 A 도 0 부터 다시 센다: 돌아온 연결의 두 표본만 남는다(첫 표본은 기준이라 FAIL 은 1).
    a = run.totals[A]
    assert (a["rx_bytes"], a["rx_packets"], a["tx_fail"], a["time"]) == (1000, 10, 1, 2)
    # B 의 FAIL 도 0 에서 다시 시작해 초기화 뒤 표본 하나의 새 실패만 센다(#497).
    b = run.totals[B]
    assert (b["rx_bytes"], b["tx_fail"], b["time"]) == (1000, 1, 1)
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


# --- #497: 카운터마다 직전 표본 대비 증가분을 더하고, 줄면 드라이버가 다시 센 것으로 본다 ---

def test_fail_adds_only_new_failures_when_tx_failed_survives_a_roam(monkeypatch, tmp_path):
    # 보드 1: 다른 AP 로 로밍하면 드라이버 tx failed·tx 패킷이 0 으로 돌아가지 않고 이어졌다(이 시험은 바이트도 잇는다).
    # A 에서 5 번 실패하고 B 에서 1 번, A 로 돌아와 2 번 실패했다 — A 의 FAIL 은 5+2 다
    # (종전에는 A 로 돌아올 때 A 의 누적 5 에 드라이버 값 전체를 더해 13 이 됐다).
    seq = [sample(A, 1000, 10, 0), sample(A, 2000, 20, 2), sample(A, 3000, 30, 5),
           sample(B, 3100, 31, 5), sample(B, 3200, 32, 6),
           sample(A, 3300, 33, 6), sample(A, 3400, 34, 8)]
    run = run_logger(monkeypatch, tmp_path, seq)

    assert [m["fail"] for m in _stat_lines(run.lines, A)] == ["0", "2", "5", "5", "7"]
    a, b = run.totals[A], run.totals[B]
    assert (a["tx_fail"], b["tx_fail"]) == (7, 1)
    assert (a["rx_bytes"], a["tx_packets"], b["rx_bytes"], b["tx_packets"]) == (2100, 21, 100, 1)


def test_same_ap_reconnect_that_restarts_every_counter_never_lowers_totals(monkeypatch, tmp_path):
    # 같은 AP 재연결(wpa_cli reassociate)은 AP 변경이 아니라 기준을 다시 잡지 않는다. 드라이버가
    # 카운터를 0 부터 다시 세면 줄어든 카운터는 현재값을 증가분으로 보고, 그 뒤로는 실제 증가분만 더한다.
    seq = [sample(A, 1000, 10, 3), sample(A, 2000, 20, 5), sample(A, 0, 0, 0),
           sample(A, 500, 5, 1), sample(A, 1500, 15, 2)]
    run = run_logger(monkeypatch, tmp_path, seq)

    assert [(m["rx"], m["tx"], m["tx_pkt"], m["fail"]) for m in _stat_lines(run.lines, A)] == [
        ("0", "0", "0", "0"), ("1000", "1000", "10", "2"), ("1000", "1000", "10", "2"),
        ("1500", "1500", "15", "3"), ("2500", "2500", "25", "4")]


def test_same_ap_reconnect_that_restarts_only_tx_failed_keeps_byte_and_packet_increases(
        monkeypatch, tmp_path):
    # 같은 AP 재연결에서 tx failed 만 0 부터 다시 세고 바이트·패킷은 이어지는 경우다. 카운터 하나가 줄었다고
    # 모든 카운터를 초기화로 보면 바이트·패킷 누적에 드라이버 카운터 전체가 다시 더해진다.
    # 줄어든 tx failed 는 현재값(다시 센 뒤의 실패 1)을 증가분으로 더한다.
    seq = [sample(A, 1000, 10, 3), sample(A, 2000, 20, 5), sample(A, 3000, 30, 1),
           sample(A, 4000, 40, 2)]
    run = run_logger(monkeypatch, tmp_path, seq)

    a = run.totals[A]
    assert (a["rx_bytes"], a["tx_bytes"], a["rx_packets"], a["tx_packets"]) == (3000, 3000, 30, 30)
    assert [m["fail"] for m in _stat_lines(run.lines, A)] == ["0", "2", "3", "4"]


def test_counter_missing_from_one_sample_is_neither_added_nor_rebaselined(monkeypatch, tmp_path):
    # 링크 정보에 tx bytes·tx failed 가 빠진 표본은 그 카운터를 모른다. 더하지도 기준을 바꾸지도 않고,
    # 다시 보이면 마지막으로 안 기준(2000·2)과 비교한다 — 0 으로 보면 다음 표본에서 카운터 전체가
    # 증가분으로 다시 더해진다.
    gap = dict(sample(A, 3000, 30, 3), tx_bytes=None, tx_fail=None)
    seq = [sample(A, 1000, 10, 1), sample(A, 2000, 20, 2), gap, sample(A, 4000, 40, 4)]
    run = run_logger(monkeypatch, tmp_path, seq)

    assert [(m["tx"], m["rx"], m["fail"]) for m in _stat_lines(run.lines, A)] == [
        ("0", "0", "0"), ("1000", "1000", "1"), ("1000", "2000", "1"), ("3000", "3000", "3")]


def _link_info(monkeypatch, tmp_path, data):
    """실제 get_link_info() 로 link.json 을 읽는다."""
    path = tmp_path / "link.json"
    path.write_text(json.dumps(data))
    monkeypatch.setattr(stat, "signal_levels", {})
    monkeypatch.setattr(stat, "IFACE", "mlan0")
    monkeypatch.setattr(stat, "logger", MagicMock(), raising=False)
    return stat.get_link_info(str(path))


LINK = {"address": A, "signal": "-50 dBm", "tx_bitrate": "100.0 MBit/s", "rx_bitrate": "100.0 MBit/s",
        "tx_failed": "7", "tx_bytes": "9000", "tx_packets": "90", "rx_bytes": "8000", "rx_packets": "80"}


@pytest.mark.parametrize("value", [None, "", "n/a"], ids=["missing", "empty", "text"])
def test_link_info_reports_a_missing_or_unparsable_counter_as_unknown(monkeypatch, tmp_path, value):
    link = {k: v for k, v in LINK.items() if k != "tx_bytes"}
    if value is not None:
        link["tx_bytes"] = value
    info = _link_info(monkeypatch, tmp_path, {"link": link, "info": {"width": "20 MHz"}})

    assert info["tx_bytes"] is None
    assert [info[k] for k in ("tx_fail", "tx_packets", "rx_bytes", "rx_packets")] == [7, 90, 8000, 80]


TOTALS = {"rx_bytes": 0, "rx_packets": 0, "tx_bytes": 0, "tx_packets": 0, "tx_fail": 0, "time": 0,
          "tx_avg_bps": 0, "rx_avg_bps": 0}


@pytest.mark.parametrize("info, written", [
    (None, False), ({}, False), ({"width": ""}, False), ({"width": "0 MHz"}, False),
    ({"width": "20 MHz"}, True),
], ids=["no-info", "no-width", "empty-width", "zero-width", "20-MHz"])
def test_stat_line_needs_a_known_bandwidth(monkeypatch, tmp_path, info, written):
    # 연결 정보가 채워지기 전 표본(info·width 없음)은 종전에 기본값 `BW:0 MHz` 줄로 찍혔다(info 가 없으면
    # 예외). 세 쓰기 경로가 모두 부르는 log_stats_write() 가 그런 표본의 줄을 쓰지 않는다.
    data = {"link": dict(LINK)}
    if info is not None:
        data["info"] = info
    wifi_info = _link_info(monkeypatch, tmp_path, data)
    monkeypatch.setattr(stat, "LOG_DIR", str(tmp_path))
    monkeypatch.setattr(stat, "bw_skip_streak", 0, raising=False)

    stat.log_stats_write(dict(TOTALS), wifi_info)

    log = tmp_path / "stat.log"
    assert log.exists() == written
    if written:
        assert ", BW:20 MHz, " in log.read_text()


def test_samples_without_bandwidth_keep_counting_and_warn_once_per_streak(monkeypatch, tmp_path):
    # 대역폭을 모르는 표본은 줄을 건너뛰지만 누적·기준·시간은 갱신해, 다음 온전한 줄이 그 구간을 담는다.
    # 줄을 연달아 3 번 건너뛰면 그 연속에서 경고를 한 번 남기고, 줄을 쓰면 연속을 다시 센다.
    bws = [None, "80 MHz", "0 MHz", "", None, "0 MHz", "80 MHz", "0", "0 MHz", None, "80 MHz"]
    seq = [sample(A, 1000 * (i + 1), 10 * (i + 1), i, bw=bw) for i, bw in enumerate(bws)]
    run = run_logger(monkeypatch, tmp_path, seq)

    stat_lines = [line for line in run.lines if " MAC:" in line]
    assert len(stat_lines) == 3 and all(", BW:80 MHz, " in line for line in stat_lines)
    assert [(m["rx"], m["tx_pkt"], m["fail"], m["t"]) for m in _stat_lines(run.lines, A)] == [
        ("1000", "10", "1", "1"), ("6000", "60", "6", "6"), ("10000", "100", "10", "10")]
    warns = [c for c in run.log.message.call_args_list if c.args[0] == "warn"]
    assert len(warns) == 2 and all("[mlan0]" in c.args[1] for c in warns)


def test_fail_starts_from_zero_when_the_logger_starts(monkeypatch, tmp_path):
    # 로거가 뜰 때 드라이버 tx failed 가 이미 쌓여 있어도 FAIL 은 0 에서 시작해 새 실패만 센다.
    run = run_logger(monkeypatch, tmp_path, [sample(A, 1000, 10, 50), sample(A, 2000, 20, 52)])

    assert [m["fail"] for m in _stat_lines(run.lines, A)] == ["0", "2"]


@pytest.mark.parametrize("trigger", ["iface-flag", "mac-flag", "interval"])
def test_fail_restarts_from_zero_after_a_stat_reset(monkeypatch, tmp_path, trigger):
    # 매일 회전·`wifi <if> stat reset [MAC]`·STAT_RESET_INTERVAL 뒤에도 FAIL 은 0 이고, 다음 표본은
    # 그때의 드라이버 값(55)을 기준으로 새 실패만 더한다 — 기준을 0 으로 두면 56 이 통째로 더해진다.
    def reset_at_4th(i, flags):
        if i == 3 and trigger != "interval":
            name = "wifi_stat_init_f_mlan0" if trigger == "iface-flag" else f"wifi_stat_reset_{A}"
            _touch(flags / name)

    if trigger == "interval":
        monkeypatch.setattr(stat, "STAT_RESET_INTERVAL", 3)  # 4 번째 표본(T:3) 뒤에 비운다
    seq = [sample(A, 1000 * i, 10 * i, fail) for i, fail in enumerate([50, 52, 53, 55, 56], 1)]
    run = run_logger(monkeypatch, tmp_path, seq, before=reset_at_4th)

    assert len([line for line in run.lines if "stat init!!" in line]) == 1
    assert [m["fail"] for m in _stat_lines(run.lines, A)] == ["0", "2", "3", "5", "1"]
    assert run.totals[A]["tx_fail"] == 1
