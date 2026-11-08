"""#459(b): radio.log(syslog local4) 한 줄 — 직전 줄 이후 비콘 수신·미스 증가분과 운용 채널 사용률.

RadioLog 는 링크 로거가 매 주기(0.9초) 읽는 값으로 radio_log_interval_sec 마다 한 줄을 만든다.
재연결·로밍(bssid·채널 변경, connected time·survey 누적값 감소)과 카운터 감소는 음수나 과대값
대신 '-'로 쓰고 기준을 다시 잡는다. 사용률은 opcd(#192)와 같은 규칙으로만 낸다.
"""
import json
import os
import socket
import sys
import tempfile
from unittest.mock import MagicMock

# sUTILS pulls heavy runtime deps (paho, serial); stub before importing the module.
sys.modules.setdefault("sUTILS", MagicMock())

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wifi_logger_link as wl  # noqa: E402

import pytest  # noqa: E402

BSS = "02:00:00:00:00:01"
BSS2 = "02:00:00:00:00:02"


class Link:
    """한 연결의 표본을 만든다: 초당 비콘 10개, active 1000 ms, busy 100 ms(사용률 10%)."""

    def __init__(self):
        self.v = dict(bssid=BSS, ch=36, freq=5180, conn=100, bcn=1000, miss=10,
                      busy=1000, active=10000)
        self.t = 0.0

    def at(self, rl, t, **change):
        dt = t - self.t
        self.t = t
        v = self.v
        v["conn"] += int(dt)
        v["bcn"] += int(10 * dt)
        v["busy"] += int(100 * dt)
        v["active"] += int(1000 * dt)
        v.update(change)
        return rl.sample(
            t,
            {"address": v["bssid"], "connected_time": f"{v['conn']} seconds"},
            {"channel": v["ch"], "freq": v["freq"], "width": "80 MHz"},
            {"beaconReceivedCount": v["bcn"], "beaconMissedCount": v["miss"]},
            {"freq": v["freq"], "active_time_ms": v["active"], "busy_time_ms": v["busy"],
             "mono_ms": int(t * 1000)},
        )


def line(bcn, miss, cu, bssid=BSS, ch=36):
    return f"mlan0 bssid={bssid} ch={ch} bw=80 bcn={bcn} miss={miss} cu={cu}"


def test_line_reports_increases_since_previous_line():
    rl, link = wl.RadioLog("mlan0", 5), Link()
    assert link.at(rl, 0.0) is None
    assert [link.at(rl, t) for t in (1.0, 2.0, 3.0, 4.0)] == [None] * 4
    assert link.at(rl, 5.0, miss=12) == line("+50", "+2", "10%")


def test_next_line_waits_a_full_interval():
    rl, link = wl.RadioLog("mlan0", 5), Link()
    link.at(rl, 0.0)
    assert link.at(rl, 5.0) is not None
    assert link.at(rl, 9.0) is None
    assert link.at(rl, 10.0) == line("+50", "+0", "10%")


def test_zero_interval_turns_the_log_off():
    rl, link = wl.RadioLog("mlan0", 0), Link()
    assert [link.at(rl, t) for t in (0.0, 5.0, 10.0, 60.0)] == [None] * 4


def test_counter_restart_marks_only_that_counter():
    rl, link = wl.RadioLog("mlan0", 5), Link()
    link.at(rl, 0.0)
    link.at(rl, 2.0, bcn=3)  # beaconReceivedCount 만 0 근처에서 다시 센다
    # 다시 센 값이 줄 시각에 기준(1000)을 넘어도 증가분으로 쓰지 않는다(+100 이 아니라 '-').
    assert link.at(rl, 5.0, bcn=1100) == line("-", "+0", "10%")
    assert link.at(rl, 10.0) == line("+50", "+0", "10%")


RECONNECT = {
    "roam": {"bssid": BSS2},
    "channel": {"ch": 40, "freq": 5200},
    "connected-time": {"conn": 1},
    "survey-restart": {"busy": 20, "active": 200},
}


@pytest.mark.parametrize("change", list(RECONNECT.values()), ids=list(RECONNECT))
def test_reconnect_or_roam_marks_every_value_then_rebaselines(change):
    rl, link = wl.RadioLog("mlan0", 5), Link()
    link.at(rl, 0.0)
    link.at(rl, 2.0, **change)
    v = link.v
    assert link.at(rl, 5.0) == line("-", "-", "-", bssid=v["bssid"], ch=v["ch"])
    assert link.at(rl, 10.0) == line("+50", "+0", "10%", bssid=v["bssid"], ch=v["ch"])


def test_cu_is_dash_when_active_time_disagrees_with_monotonic_time():
    # moal 은 active time 을 벽시계로 센다. 구간 안에서 벽시계가 2초 뛰면 비율이 틀어지므로
    # opcd(#192)처럼 |Δactive − Δmono| > 500 ms 인 구간의 사용률은 '-'다.
    rl, link = wl.RadioLog("mlan0", 5), Link()
    link.at(rl, 0.0)
    link.v["active"] += 2000
    assert link.at(rl, 5.0) == line("+50", "+0", "-")


def test_cu_saturates_at_100_when_busy_outgrows_active():
    rl, link = wl.RadioLog("mlan0", 5), Link()
    link.at(rl, 0.0)
    link.v["busy"] += 5000
    assert link.at(rl, 5.0) == line("+50", "+0", "100%")


def test_missing_inputs_give_dash():
    rl = wl.RadioLog("mlan0", 5)
    assert rl.sample(0.0, {"address": BSS}, {}, {"error": "x"}, None) is None
    assert rl.sample(5.0, {"address": BSS}, {}, {"error": "x"}, None) == \
        f"mlan0 bssid={BSS} ch=- bw=- bcn=- miss=- cu=-"


def test_disconnect_reset_starts_a_new_baseline():
    rl, link = wl.RadioLog("mlan0", 5), Link()
    link.at(rl, 0.0)
    link.at(rl, 2.0)
    rl.reset()  # 끊김: 줄 없이 기준을 버린다
    assert link.at(rl, 3.0, conn=0, bcn=1500, busy=0, active=0) is None
    assert link.at(rl, 7.0) is None
    assert link.at(rl, 8.0) == line("+50", "+0", "10%")


@pytest.mark.parametrize("conf, expected", [
    ({}, 5),
    ({"mlan0": {"logger": {"radio_log_interval_sec": 10}}}, 10),
    ({"logger": {"radio_log_interval_sec": 7}}, 7),
    ({"logger": {"radio_log_interval_sec": 7},
      "mlan0": {"logger": {"radio_log_interval_sec": 0}}}, 0),
    ({"mlan0": {"logger": {"radio_log_interval_sec": -1}}}, None),
    ({"mlan0": {"logger": {"radio_log_interval_sec": 2.5}}}, None),
    ({"mlan0": {"logger": {"radio_log_interval_sec": "5"}}}, None),
    ({"mlan0": {"logger": {"radio_log_interval_sec": True}}}, None),
])
def test_interval_from_conf(conf, expected):
    assert wl.radio_log_interval_from_conf(conf, "mlan0") == expected


def test_emitter_sends_local4_info_with_radio_tag():
    # 태그가 wifi_ 로 시작하면 rsyslog.conf 의 ^wifi_ 규칙이 logger.log 로 보내고 멈춘다.
    # local4(20)·info(6) → PRI 166.
    work = tempfile.mkdtemp(prefix="rlg", dir="/tmp")
    path = os.path.join(work, "log.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        server.bind(path)
        server.settimeout(5)
        emit = wl._make_radio_emitter(path)
        emit(line("+50", "+0", "10%"))
        data = server.recv(4096)
    finally:
        server.close()
        os.unlink(path)
        os.rmdir(work)
    assert data.rstrip(b"\x00") == ("<166>RADIO " + line("+50", "+0", "10%")).encode()


class _Stop(Exception):
    pass


STATION = "Station 02:00:00:00:00:01 (on lo)\n\tinactive time:\t10 ms\n\tconnected time:\t{conn} seconds\n"
INFO = ("Interface lo\n\taddr 02:11:22:33:44:55\n\ttype managed\n"
        "\tchannel 36 (5180 MHz), width: 80 MHz, center1: 5210 MHz\n\ttxpower 20.00 dBm\n")
SURVEY = ("Survey data from lo\n\tfrequency:\t\t\t5180 MHz [in use]\n\tnoise:\t\t\t\t-92 dBm\n"
          "\tchannel active time:\t\t{active} ms\n\tchannel busy time:\t\t{busy} ms\n")


def test_main_loop_writes_radio_lines_and_restarts_after_disconnect(tmp_path, monkeypatch):
    """main() 을 1초 주기로 돌린다: 0~5초 연결(5초에 한 줄), 6초 끊김, 7~11초 재연결.
    끊김에서 기준을 버리므로 다음 줄은 12초다 — 기준을 이어 쓰면 10초에 한 줄이 더 나온다."""
    mod = wl
    sysfs = tmp_path / "net"
    (sysfs / "lo").mkdir(parents=True)
    (sysfs / "lo" / "address").write_text("02:11:22:33:44:55\n")
    logdir = tmp_path / "json"
    logdir.mkdir()
    clock = {"t": 0.0, "sleeps": 0}
    disconnected_at = {6}

    def sleep(_seconds):
        clock["sleeps"] += 1
        clock["t"] = float(clock["sleeps"])
        if clock["sleeps"] >= 12:
            raise _Stop

    def now_sec():
        return int(clock["t"])

    def run_command(cmd):
        if cmd[-1] == "dump" and cmd[-2] == "station":
            return "" if now_sec() in disconnected_at else STATION.format(conn=100 + now_sec())
        if cmd[-1] == "dump" and cmd[-2] == "survey":
            return SURVEY.format(active=10000 + 1000 * now_sec(), busy=1000 + 100 * now_sec())
        return ""

    emitted = []
    monkeypatch.setattr(mod, "IFACE", "lo")
    monkeypatch.setattr(mod, "SYSFS_NET", str(sysfs))
    monkeypatch.setattr(mod, "LOG_DIR", str(logdir))
    monkeypatch.setattr(mod, "logger", MagicMock(), raising=False)
    monkeypatch.setattr(mod, "time", type("T", (), {
        "monotonic": staticmethod(lambda: clock["t"]), "sleep": staticmethod(sleep)}))
    monkeypatch.setattr(mod, "check_fw_settings", lambda *_: None)
    monkeypatch.setattr(mod, "is_wpa_running", lambda _iface: True)
    monkeypatch.setattr(mod, "poll_supplicant",
                        lambda _iface: {"wpa_state": "COMPLETED", "temp_disabled": False})
    monkeypatch.setattr(mod, "run_command", run_command)
    monkeypatch.setattr(mod, "validate_station", lambda out: bool(out))
    monkeypatch.setattr(mod, "fast_retry_station_dump", lambda *_: None)
    monkeypatch.setattr(mod, "run_command_with_retry", lambda *_a, **_k: INFO)
    monkeypatch.setattr(mod, "get_ip_info", lambda _iface: {})
    monkeypatch.setattr(mod, "check_tx_spike", lambda *_: None)
    monkeypatch.setattr(mod, "check_rx_mismatch", lambda *_: None)
    monkeypatch.setattr(mod, "parse_mwlan_log", lambda: {
        "beaconReceivedCount": 1000 + 10 * now_sec(), "beaconMissedCount": 10})
    monkeypatch.setattr(mod, "RADIO", wl.RadioLog("lo", 5), raising=False)
    monkeypatch.setattr(mod, "RADIO_EMIT", emitted.append, raising=False)
    with pytest.raises(_Stop):
        mod.main()
    assert emitted == [f"lo bssid={BSS} ch=36 bw=80 bcn=+50 miss=+0 cu=10%"]
    assert json.loads((logdir / "link.json").read_text())["link"]["address"] == BSS
