import json
import sys
import os
from unittest.mock import MagicMock

# sUTILS pulls heavy runtime deps (paho, serial); stub before importing the module.
sys.modules.setdefault("sUTILS", MagicMock())

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wifi_logger_link  # noqa: E402

import pytest  # noqa: E402

# 2026-10-21 cts-wlan /sys/kernel/moal_bridge/stats 원문.
_BRIDGE_STATS_BOARD = (
    "w2p fwd=4138 bytes=599289 drop=0 err=0 oom=0 qlen=0 dwell_avg=0us dwell_max=0us n=0\n"
    "p2w fwd=6028 bytes=1889455 drop=32 err=0 oom=0 qlen=0 dwell_avg=0us dwell_max=0us n=0\n"
    "active=1 peer_released=0\n"
    "rx_deliver gap_avg=0us gap_max=0us n=0 dur_max=0us\n"
    "rx_pull avg=0us max=0us n=0\n"
    "tx_write avg=0us max=0us n=0\n"
    "hairpin on=0 tx_fwd=0 arp_tee=0 arp_inject=0\n"
    "announce on=0 tx=0\n"
    "iface=mlan0 peer=eth0 pending_iface=none pending_state=none\n"
    "switch_ok=0 switch_fail=0 rollback_ok=0 rollback_fail=0\n"
)

_BRIDGE_STATS_INACTIVE = (
    "bridge: inactive\n"
    "iface=none peer=none pending_iface=none pending_state=none\n"
    "switch_ok=0 switch_fail=0 rollback_ok=0 rollback_fail=0\n"
)

# 2026-10-21 cts-wlan 실측 10초 창 증가량 (AP 로 ping 5/s 동안).
# (dot11ReceivedFragmentCount, dot11GroupReceivedFrameCount, netdev rx, 브리지 w2p, beacon)
# 좀비(같은 MAC 의 2번 보드 연결 중): 호스트에는 브로드캐스트만 올라온다(netdev == w2p).
_ZOMBIE = [(28, 8, 10, 10, 92), (32, 1, 1, 1, 95)]
_NORMAL = [(51, 0, 51, 1, 97), (51, 1, 51, 1, 98), (53, 0, 52, 3, 98)]
_SYN_ZOMBIE = (66, 0, 0, 0, 99)

_START = (1000, 100, 500, 4000, 289000)


def _det():
    return wifi_logger_link.RxMismatchDetector(window_sec=10.0, min_fw=20, report_gap_sec=60.0)


@pytest.fixture
def det():
    return _det()


def run(det, deltas, start=_START, bridge=True):
    """기준 표본 뒤 10초마다 증가량을 누적해 넣고 각 창의 반환값을 돌려준다."""
    fw, grp, nd, br, bcn = start
    br = br if bridge else None
    det.update(0.0, fw, grp, nd, br, bcn)
    events = []
    t = 0.0
    for d_fw, d_grp, d_nd, d_br, d_bcn in deltas:
        t += 10.0
        fw, grp, nd, bcn = fw + d_fw, grp + d_grp, nd + d_nd, bcn + d_bcn
        br = br + d_br if br is not None else None
        events.append(det.update(t, fw, grp, nd, br, bcn))
    return events


# ---- 판정기: 실측 창 ---------------------------------------------------------

def test_real_zombie_window_reports_suspect(det):
    ev = run(det, _ZOMBIE[:1])[0]
    assert ev is not None
    level, text = ev
    assert level == "warn"
    assert "RX_MISMATCH suspect" in text
    assert "fw_unicast=+20" in text
    assert "host_unicast=+4" in text
    assert "netdev=+10" in text and "bridge=+10" in text and "group=+8" in text
    assert "beacon=+92" in text


def test_real_zombie_broadcasts_do_not_clear(det):
    """좀비 중에도 브로드캐스트는 호스트에 올라온다 — 그걸로 해소를 찍으면 안 된다(실측 회귀)."""
    events = run(det, _ZOMBIE)
    assert events[0] is not None and "suspect" in events[0][1]
    assert events[1] is None


def test_real_normal_windows_are_quiet(det):
    assert run(det, _NORMAL) == [None, None, None]


def test_real_recovery_clears(det):
    events = run(det, _ZOMBIE + _NORMAL[:1])
    assert events[2] == ("info", "RX_MISMATCH cleared after 30s: fw_unicast=+51 host_unicast=+52")


def test_real_group_frames_not_delivered_is_not_suspect(det):
    """2026-10-21 11:34:47 실측(같은 MAC 두 보드로 트래픽 폭주 중): 펌웨어가 센 그룹 프레임이
    호스트에 다 오지 않는다(그룹 173 > 브리지 111). 호스트가 실제로 받은 브로드캐스트는
    min(그룹, netdev, 브리지) 를 넘을 수 없다 — 그룹 전체를 빼면 유니캐스트가 음수가 되어 오탐."""
    assert run(det, [(233, 173, 166, 111, 92)]) == [None]


# ---- 판정기: 집계 규칙 ------------------------------------------------------

def test_bridged_unicast_is_not_mismatch(det):
    """브리지가 eth0 으로 넘긴 유니캐스트는 netdev 에 안 잡힌다 — 브리지 카운터로 본다."""
    assert run(det, [(62, 2, 2, 62, 99)]) == [None]


def test_bridge_broadcast_counted_once(det):
    """브리지는 그룹 프레임을 netdev·w2p 양쪽에 센다 — 합이 아니라 max 로 한 번만."""
    ev = run(det, [(30, 10, 10, 10, 99)])[0]
    assert ev is not None and "host_unicast=+0" in ev[1]


def test_bridge_absent_uses_netdev_only(det):
    ev = run(det, [(30, 10, 10, 0, 99)], bridge=False)[0]
    assert ev is not None
    assert "host_unicast=+0" in ev[1] and "bridge=n/a" in ev[1]


def test_suspect_ratio_boundary():
    """호스트 유니캐스트가 펌웨어의 1/4 미만이어야 의심."""
    assert run(_det(), [(20, 0, 4, 0, 99)])[0] is not None
    assert run(_det(), [(20, 0, 5, 0, 99)])[0] is None


def test_clear_ratio_boundary():
    """해소는 호스트 유니캐스트가 펌웨어의 1/2 이상일 때."""
    assert run(_det(), [_SYN_ZOMBIE, (20, 0, 10, 0, 99)])[1] is not None
    assert run(_det(), [_SYN_ZOMBIE, (20, 0, 9, 0, 99)])[1] is None


def test_fw_unicast_threshold_boundary(det):
    """그룹을 뺀 펌웨어 유니캐스트가 임계값 미만이면 판정 보류."""
    events = run(det, [(29, 10, 0, 0, 99), (30, 10, 0, 0, 99)])
    assert events[0] is None
    assert events[1] is not None and "fw_unicast=+20" in events[1][1]


def test_low_unicast_window_keeps_episode(det):
    """트래픽이 적은 창은 해소도 의심도 아니다 — 사건은 유지된다."""
    events = run(det, [_SYN_ZOMBIE, (5, 0, 5, 0, 99), _NORMAL[0]])
    assert events[1] is None
    assert events[2] == ("info", "RX_MISMATCH cleared after 30s: fw_unicast=+51 host_unicast=+52")


def test_beacons_stopped_is_not_mismatch(det):
    assert run(det, [(66, 0, 0, 0, 0)]) == [None]


def test_missing_beacon_counter_still_detects(det):
    """비콘 카운터가 없는 펌웨어에서도 놓치지 않는다(사용자 선택: 놓치지 않는 쪽)."""
    det.update(0.0, 1000, 100, 500, 4000, None)
    ev = det.update(10.0, 1066, 100, 500, 4000, None)
    assert ev is not None and "beacon=n/a" in ev[1]


def test_missing_group_counter_treated_as_zero(det):
    det.update(0.0, 1000, None, 500, 4000, 289000)
    ev = det.update(10.0, 1066, None, 500, 4000, 289099)
    assert ev is not None and "group=+0" in ev[1]


def test_bridge_presence_change_skips_window(det):
    """브리지가 사라지거나 생긴 창은 집계 기준이 달라 판정하지 않는다."""
    det.update(0.0, 1000, 100, 500, 4000, 289000)
    assert det.update(10.0, 1066, 100, 500, None, 289099) is None


def test_no_verdict_before_window_elapses(det):
    """창이 차기 전에는 판정하지 않고, 기준점도 옮기지 않는다."""
    det.update(0.0, 1000, 100, 500, 4000, 289000)
    assert det.update(5.0, 1033, 100, 500, 4000, 289050) is None
    ev = det.update(10.0, 1066, 100, 500, 4000, 289099)
    assert ev is not None and "fw_unicast=+66" in ev[1]


# ---- 판정기: 사건 단위·로그 양 ----------------------------------------------

def test_one_line_per_episode(det):
    assert run(det, [_SYN_ZOMBIE, _SYN_ZOMBIE])[1] is None


def test_reports_rate_limited(det):
    """의심 보고는 1분에 1번 — 그 안의 새 사건은 의심/해소 모두 남기지 않는다(로그 폭주 방지)."""
    z, n = _SYN_ZOMBIE, _NORMAL[0]
    events = run(det, [z, n, z, n, n, z, n, z])
    assert events[0][0] == "warn"                   # t=10 보고
    assert events[1][0] == "info"                   # t=20 해소
    assert events[2] is None and events[3] is None  # t=30 억제된 사건, t=40 그 해소도 침묵
    assert events[4] is None
    assert events[5] is None and events[6] is None  # t=60 은 마지막 보고(10)+50s → 억제
    assert events[7] is not None and events[7][0] == "warn"  # t=80: 10+60 이후 재보고


def test_long_episode_reported_once(det):
    """보고 간격(60s)보다 오래 이어지는 사건도 의심 줄은 처음 한 번뿐이다."""
    events = run(det, [_SYN_ZOMBIE] * 8)
    assert events[0][0] == "warn"
    assert all(e is None for e in events[1:])


def test_suppressed_episode_reported_once_gap_elapses(det):
    """억제된 사건이 계속되면 보고 간격이 지난 첫 의심 창에서 보고한다 — 좀비가 계속되는데
    logger.log 의 마지막 줄이 거짓 해소로 남지 않게(tribunal A-R1-001)."""
    z, n = _SYN_ZOMBIE, _NORMAL[0]
    events = run(det, [z, n] + [z] * 6)
    assert events[0][0] == "warn"                    # t=10 보고
    assert events[1][0] == "info"                    # t=20 해소
    assert all(e is None for e in events[2:6])       # t=30..60 억제(마지막 보고 t=10 + 60 전)
    assert events[6] is not None and events[6][0] == "warn"  # t=70 계속되는 사건 보고
    assert events[7] is None                         # t=80 같은 사건은 다시 보고하지 않음


def test_report_gap_boundary(det):
    z, n = _SYN_ZOMBIE, _NORMAL[0]
    events = run(det, [z, n, n, n, n, n, z])
    assert events[6] is not None and events[6][0] == "warn"  # t=70: 정확히 60s 경과


def test_long_gap_rebaselines(det):
    """연결이 끊겨 갱신이 멈췄던 구간을 한 창으로 판정하지 않는다."""
    det.update(0.0, 1000, 100, 500, 4000, 289000)
    assert det.update(35.0, 1066, 100, 500, 4000, 289099) is None
    ev = det.update(45.0, 1132, 100, 500, 4000, 289198)
    assert ev is not None and "fw_unicast=+66" in ev[1]


def test_gap_just_over_two_windows_rebaselines(det):
    det.update(0.0, 1000, 100, 500, 4000, 289000)
    assert det.update(21.0, 1066, 100, 500, 4000, 289099) is None
    ev = det.update(31.0, 1132, 100, 500, 4000, 289198)
    assert ev is not None and "fw_unicast=+66" in ev[1]


def test_netdev_counter_reset_skips_window(det):
    """fw_reload 로 netdev 가 다시 만들어지면 rx_packets 가 0 부터 — 무수신이 아니다."""
    det.update(0.0, 1000, 100, 3000, 4000, 289000)
    assert det.update(10.0, 1066, 100, 0, 4000, 289099) is None


def test_fw_counter_reset_skips_window(det):
    det.update(0.0, 5000, 400, 3000, 4000, 289000)
    assert det.update(10.0, 66, 0, 3000, 4000, 289099) is None


def test_counter_reset_during_episode_does_not_clear(det):
    det.update(0.0, 1000, 100, 3000, 4000, 289000)
    det.update(10.0, 1066, 100, 3000, 4000, 289099)
    assert det.update(20.0, 1132, 100, 0, 4000, 289198) is None


def test_missing_fw_counter_is_ignored(det):
    assert det.update(0.0, None, 100, 500, 4000, 289000) is None
    assert det.update(10.0, None, 100, 500, 4000, 289099) is None


# ---- 브리지 통계 파싱 -------------------------------------------------------

def test_parse_bridge_board_sample_counts_w2p_only():
    """보드 원문: w2p 만 센다(p2w 는 반대 방향)."""
    assert wifi_logger_link._parse_bridge_w2p(_BRIDGE_STATS_BOARD, "mlan0") == 4138


def test_parse_bridge_counts_dropped_frames():
    """eth0 케이블이 빠져 브리지가 버리는 프레임도 호스트가 받은 것 — 오탐 방지."""
    text = _BRIDGE_STATS_BOARD.replace(
        "w2p fwd=4138 bytes=599289 drop=0 err=0 oom=0",
        "w2p fwd=10 bytes=999 drop=3 err=1 oom=2")
    assert wifi_logger_link._parse_bridge_w2p(text, "mlan0") == 16


def test_parse_bridge_other_iface_is_none():
    text = _BRIDGE_STATS_BOARD.replace("iface=mlan0 peer=eth0", "iface=mlan1 peer=eth0")
    assert wifi_logger_link._parse_bridge_w2p(text, "mlan0") is None


def test_parse_bridge_inactive_is_none():
    assert wifi_logger_link._parse_bridge_w2p(_BRIDGE_STATS_INACTIVE, "mlan0") is None


# ---- 설정 (wifi_init_conf.json) ----------------------------------------------

_TEMPLATE = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..",
                         "opt", "wlan", "config", "wifi_init_conf.json")


def test_enabled_by_default():
    assert wifi_logger_link.rx_mismatch_enabled_from_conf({}, "mlan0") is True


def test_iface_setting_disables_only_that_iface():
    conf = {"mlan0": {"logger": {"rx_mismatch_enabled": False}}}
    assert wifi_logger_link.rx_mismatch_enabled_from_conf(conf, "mlan0") is False
    assert wifi_logger_link.rx_mismatch_enabled_from_conf(conf, "mlan1") is True


def test_iface_setting_overrides_global():
    conf = {"logger": {"rx_mismatch_enabled": False},
            "mlan0": {"logger": {"rx_mismatch_enabled": True}}}
    assert wifi_logger_link.rx_mismatch_enabled_from_conf(conf, "mlan0") is True
    assert wifi_logger_link.rx_mismatch_enabled_from_conf(conf, "mlan1") is False


def test_non_boolean_setting_is_invalid():
    """문자열 "false" 를 켜짐으로 읽지 않는다 — JSON boolean 만 인정하고 나머지는 None."""
    conf = {"mlan0": {"logger": {"rx_mismatch_enabled": "false"}}}
    assert wifi_logger_link.rx_mismatch_enabled_from_conf(conf, "mlan0") is None


def test_template_default_matches_code_default():
    """템플릿과 코드 기본값이 같아야 설정 파일이 없거나 깨진 기기도 같은 동작을 한다(fail-same)."""
    with open(_TEMPLATE, encoding="utf-8") as f:
        template = json.load(f)
    for iface in ("mlan0", "mlan1"):
        assert template[iface]["logger"]["rx_mismatch_enabled"] is True
        assert wifi_logger_link.rx_mismatch_enabled_from_conf({}, iface) is True


# ---- 루프 연결부 -----------------------------------------------------------

@pytest.fixture
def wired(monkeypatch, tmp_path):
    """실제 파일 읽기 경로를 임시 파일로 돌린다(실경로와 닮지 않은 이름)."""
    netdev = tmp_path / "rxcount_fixture"
    bridge = tmp_path / "brstat_fixture"
    monkeypatch.setattr(wifi_logger_link, "IFACE", "mlan0")
    # logger 는 __main__ 에서만 생성되므로 모듈 속성이 없을 수 있다.
    monkeypatch.setattr(wifi_logger_link, "logger", MagicMock(), raising=False)
    monkeypatch.setattr(wifi_logger_link, "NETDEV_RX_PATH_FMT", str(netdev) + ".{}")
    monkeypatch.setattr(wifi_logger_link, "BRIDGE_STATS_PATH", str(bridge))
    monkeypatch.setattr(wifi_logger_link, "_rx_mismatch", _det())
    monkeypatch.setattr(wifi_logger_link, "_rx_mismatch_disabled", False)

    def set_counters(netdev_rx, w2p_fwd):
        (tmp_path / "rxcount_fixture.mlan0").write_text(f"{netdev_rx}\n")
        bridge.write_text(_BRIDGE_STATS_BOARD.replace("w2p fwd=4138", f"w2p fwd={w2p_fwd}"))

    return set_counters


def _mw(fw, group, bcn):
    return {"dot11ReceivedFragmentCount": fw, "dot11GroupReceivedFrameCount": group,
            "beaconReceivedCount": bcn,
            "dot11QosReceivedFragmentCount": [1655, 0, 0, 0, 0, 0, 87, 2]}


def test_check_rx_mismatch_logs_suspect(wired):
    wired(3111, 4138)
    wifi_logger_link.check_rx_mismatch(_mw(4473, 3674, 289100), 0.0)
    wifi_logger_link.check_rx_mismatch(_mw(4539, 3674, 289199), 10.0)
    calls = wifi_logger_link.logger.message.call_args_list
    assert len(calls) == 1
    level, text = calls[0].args[0], calls[0].args[1]
    assert level == "warn"
    assert text.startswith("[mlan0] RX_MISMATCH suspect")
    assert "fw_unicast=+66" in text and "netdev=+0" in text and "bridge=+0" in text


def test_check_rx_mismatch_uses_group_counter(wired):
    """그룹 카운터를 넘기지 않으면 브로드캐스트만 받는 좀비를 놓친다."""
    wired(3111, 4138)
    wifi_logger_link.check_rx_mismatch(_mw(4473, 3674, 289100), 0.0)
    wired(3121, 4148)
    wifi_logger_link.check_rx_mismatch(_mw(4503, 3684, 289199), 10.0)
    calls = wifi_logger_link.logger.message.call_args_list
    assert len(calls) == 1 and "host_unicast=+0" in calls[0].args[1]


def test_check_rx_mismatch_reads_bridge_counter(wired):
    """브리지 카운터 파일이 실제로 판정에 들어간다."""
    wired(3111, 4138)
    wifi_logger_link.check_rx_mismatch(_mw(4473, 3674, 289100), 0.0)
    wired(3111, 4200)
    wifi_logger_link.check_rx_mismatch(_mw(4539, 3674, 289199), 10.0)
    assert not wifi_logger_link.logger.message.called


def test_check_rx_mismatch_reads_netdev_counter(wired):
    wired(3111, 4138)
    wifi_logger_link.check_rx_mismatch(_mw(4473, 3674, 289100), 0.0)
    wired(3171, 4138)
    wifi_logger_link.check_rx_mismatch(_mw(4539, 3674, 289199), 10.0)
    assert not wifi_logger_link.logger.message.called


def test_check_rx_mismatch_ignores_mwlan_error(wired):
    wired(3111, 4138)
    wifi_logger_link.check_rx_mismatch({"error": "No such file"}, 0.0)
    wifi_logger_link.check_rx_mismatch({"error": "No such file"}, 10.0)
    assert not wifi_logger_link.logger.message.called


def test_check_rx_mismatch_error_disables_once(wired, monkeypatch):
    """디버깅 보조 기능의 예외는 메인 루프로 나가지 않고, 1회 기록 후 꺼진다(운용 무영향)."""
    wired(3111, 4138)
    calls = {"n": 0}

    def boom(*args):
        calls["n"] += 1
        raise RuntimeError("synthetic")

    monkeypatch.setattr(wifi_logger_link._rx_mismatch, "update", boom)
    wifi_logger_link.check_rx_mismatch(_mw(4473, 3674, 289100), 0.0)
    wifi_logger_link.check_rx_mismatch(_mw(4539, 3674, 289199), 10.0)
    assert calls["n"] == 1
    msgs = wifi_logger_link.logger.message.call_args_list
    assert len(msgs) == 1 and "RX_MISMATCH check disabled" in msgs[0].args[1]
