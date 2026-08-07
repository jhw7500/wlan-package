import sys
import os
from unittest.mock import MagicMock

# sUTILS has heavy runtime deps (paho, serial, numpy); stub it before importing wifi_roam.
sys.modules.setdefault("sUTILS", MagicMock())

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wifi_roam
from wifi_roam import compute_no_result_backoff, advance_no_candidate_backoff

import pytest

# `logger` is only assigned at runtime inside main(); stub so any error path is safe.
wifi_roam.logger = MagicMock()

def _set_sleep(monkeypatch, start, cap, fast=1):
    monkeypatch.setattr(wifi_roam, "SCAN_NO_RESULT_SLEEP", start)
    monkeypatch.setattr(wifi_roam, "ROAM_NO_RESULT_MAX_SLEEP", cap)
    # fast=1 → 기존 동작(streak1=start, streak2=×2). advance는 이 전역을 읽는다.
    monkeypatch.setattr(wifi_roam, "ROAM_NO_RESULT_FAST_COUNT", fast)

def test_backoff_streak_zero_is_start(monkeypatch):
    _set_sleep(monkeypatch, 3, 30)
    assert compute_no_result_backoff(0) == 3

def test_backoff_streak_negative_is_start(monkeypatch):
    # defensive: a negative streak must not produce a fractional/tiny sleep
    _set_sleep(monkeypatch, 3, 30)
    assert compute_no_result_backoff(-1) == 3

def test_backoff_doubles_each_streak(monkeypatch):
    _set_sleep(monkeypatch, 3, 30)
    # 3 -> 6 -> 12 -> 24 -> 30(cap) -> 30(cap)
    assert [compute_no_result_backoff(s) for s in range(1, 7)] == [3, 6, 12, 24, 30, 30]

def test_backoff_caps_at_max(monkeypatch):
    _set_sleep(monkeypatch, 3, 30)
    assert compute_no_result_backoff(20) == 30

def test_backoff_respects_custom_start_and_cap(monkeypatch):
    _set_sleep(monkeypatch, 5, 40)
    # 5 -> 10 -> 20 -> 40(cap) -> 40(cap)
    assert [compute_no_result_backoff(s) for s in range(1, 6)] == [5, 10, 20, 40, 40]

def test_backoff_returns_int(monkeypatch):
    _set_sleep(monkeypatch, 3, 30)
    for s in range(0, 8):
        assert isinstance(compute_no_result_backoff(s), int)

# --- #5: streak clamp (거대 정수 2**streak 방지, 상한 동작 보존) ---

def test_backoff_huge_streak_clamps_to_cap_no_giant_int(monkeypatch):
    # streak가 매우 커도 2**(streak-1) 거대 정수 연산 없이 상한값을 반환해야 한다.
    _set_sleep(monkeypatch, 3, 30)
    # 매우 큰 streak: clamp 없으면 2**99999 연산(매우 느림/거대) → clamp로 즉시 cap
    assert compute_no_result_backoff(100000) == 30

def test_max_level_reaches_cap(monkeypatch):
    # 3 -> 6 -> 12 -> 24 -> 30(cap): level 5에서 cap 도달
    _set_sleep(monkeypatch, 3, 30)
    lvl = wifi_roam._no_result_max_level()
    assert compute_no_result_backoff(lvl) == 30
    # max_level 이상은 모두 cap
    assert compute_no_result_backoff(lvl + 50) == 30

def test_max_level_defensive_on_bad_start(monkeypatch):
    # SCAN_NO_RESULT_SLEEP<=0 비정상 입력에도 무한 루프 없이 방어값 반환
    _set_sleep(monkeypatch, 0, 30)
    assert wifi_roam._no_result_max_level() == 1

# --- #9: advance_no_candidate_backoff helper (DRY) ---

def test_advance_increments_streak_and_returns_backoff(monkeypatch):
    _set_sleep(monkeypatch, 3, 30)
    backoff, streak = advance_no_candidate_backoff(0)
    assert streak == 1
    assert backoff == 3

def test_advance_streak_caps_at_max_level(monkeypatch):
    # 매 tick 호출해도 streak가 max_level 위로 무한 증가하지 않아야 한다(#5).
    _set_sleep(monkeypatch, 3, 30)
    max_level = wifi_roam._no_result_max_level()
    streak = 0
    for _ in range(100):
        backoff, streak = advance_no_candidate_backoff(streak)
    assert streak == max_level
    assert backoff == 30  # 상한 유지

def test_advance_stays_at_cap_regardless_of_elapsed_time(monkeypatch):
    """상한 도달 후에는 시간이 아무리 흘러도 주기가 상한 아래로 내려가지 않는다.

    시간 기반 점감(ROAM_NO_RESULT_BACKOFF_RECOVER_SEC)을 제거하며 고정한 계약이다.
    제거 전에도 점감은 backoff 계산 뒤에 일어나고 다음 tick 의 streak+1 이 즉시
    되돌려 반환값이 상한 아래로 내려간 적이 없었다(실효 0). 누군가 점감을 되살리려
    한다면 이 테스트가 먼저 깨져 '동작이 바뀐다'는 사실을 드러낸다.
    """
    _set_sleep(monkeypatch, 3, 30, fast=3)
    clock = [1000.0]
    monkeypatch.setattr(wifi_roam.time, "time", lambda: clock[0])
    streak, seq = 0, []
    for _ in range(40):
        backoff, streak = advance_no_candidate_backoff(streak)
        seq.append(backoff)
        clock[0] += backoff  # 데몬이 backoff 만큼 자며 시간이 흐른다
    assert seq[:13] == [3, 3, 3, 6, 6, 6, 12, 12, 12, 24, 24, 24, 30]
    assert set(seq[12:]) == {30}  # 상한 도달 후 시간이 흘러도 전부 30
    # 상한 도달 지점(fast*(max_level-1)+1 = 13)에서 진동 없이 고정
    assert streak == 3 * (wifi_roam._no_result_max_level() - 1) + 1

# --- ROAM_NO_RESULT_FAST_COUNT: 처음 N회 빠른 주기 후 backoff ---

def test_fast_count_keeps_start_for_first_n(monkeypatch):
    """fast_count=3 → 레벨당 3회 반복하는 플래토 곡선."""
    _set_sleep(monkeypatch, 3, 30)
    assert [compute_no_result_backoff(s, 3) for s in range(1, 14)] == \
        [3, 3, 3, 6, 6, 6, 12, 12, 12, 24, 24, 24, 30]

def test_fast_count_one_matches_legacy(monkeypatch):
    """fast_count=1(기본) → 기존 지수 곡선과 완전 동일(무회귀·cross-SSID 보존)."""
    _set_sleep(monkeypatch, 3, 30)
    assert [compute_no_result_backoff(s, 1) for s in range(1, 7)] == [3, 6, 12, 24, 30, 30]
    # 인자 생략 시 기본 fast_count=1
    assert [compute_no_result_backoff(s) for s in range(1, 7)] == [3, 6, 12, 24, 30, 30]

def test_fast_count_custom_start_cap(monkeypatch):
    """시작값/상한 커스텀 + fast_count=2: 레벨당 2회 플래토."""
    _set_sleep(monkeypatch, 5, 40)
    assert [compute_no_result_backoff(s, 2) for s in range(1, 10)] == \
        [5, 5, 10, 10, 20, 20, 40, 40, 40]

def test_advance_uses_global_fast_count(monkeypatch):
    """advance는 전역 ROAM_NO_RESULT_FAST_COUNT(기본 3)를 사용 — 레벨당 3회 플래토."""
    _set_sleep(monkeypatch, 3, 30, fast=3)
    streak, seq = 0, []
    for _ in range(8):
        backoff, streak = advance_no_candidate_backoff(streak)
        seq.append(backoff)
    assert seq == [3, 3, 3, 6, 6, 6, 12, 12]
    assert streak == 8  # cap(13) 이전이라 tick 수 그대로

# --- roam hint mtime consumer ---

def test_hint_missing_file_returns_false(tmp_path, monkeypatch):
    monkeypatch.setattr(wifi_roam, "ROAM_HINT_FILE", str(tmp_path / "wifi_roam_hint_mlan0"))
    state = {"hint_mtime": None}
    assert wifi_roam.roam_hint_touched(state) is False
    assert state["hint_mtime"] is None

def test_hint_first_observation_is_touch(tmp_path, monkeypatch):
    hint = tmp_path / "wifi_roam_hint_mlan0"
    hint.write_text("")
    monkeypatch.setattr(wifi_roam, "ROAM_HINT_FILE", str(hint))
    state = {"hint_mtime": None}
    # first time the file exists → treated as a fresh touch (reset streak)
    assert wifi_roam.roam_hint_touched(state) is True
    assert state["hint_mtime"] == os.path.getmtime(str(hint))

def test_hint_unchanged_mtime_returns_false(tmp_path, monkeypatch):
    hint = tmp_path / "wifi_roam_hint_mlan0"
    hint.write_text("")
    monkeypatch.setattr(wifi_roam, "ROAM_HINT_FILE", str(hint))
    state = {"hint_mtime": os.path.getmtime(str(hint))}
    assert wifi_roam.roam_hint_touched(state) is False

def test_hint_newer_mtime_returns_true(tmp_path, monkeypatch):
    hint = tmp_path / "wifi_roam_hint_mlan0"
    hint.write_text("")
    monkeypatch.setattr(wifi_roam, "ROAM_HINT_FILE", str(hint))
    old = os.path.getmtime(str(hint))
    # advance mtime explicitly (avoid filesystem timestamp granularity flakiness)
    os.utime(str(hint), (old + 10, old + 10))
    state = {"hint_mtime": old}
    assert wifi_roam.roam_hint_touched(state) is True
    assert state["hint_mtime"] == old + 10

# --- #3: ROAM_HINT_FILE 은 IFACE 갱신(__main__) 직후 재대입되어야 함 (mlan1 불일치 방지) ---

def test_roam_hint_file_module_default_uses_iface():
    # 모듈 로드 시 기본값은 기본 IFACE(mlan0) 기준 경로
    assert wifi_roam.ROAM_HINT_FILE == "/tmp/wifi_roam_hint_mlan0"

def test_main_reassigns_roam_hint_file_for_iface():
    # __main__ 블록에서 IFACE 갱신 직후 ROAM_HINT_FILE 을 재대입하는지 소스로 회귀 보장.
    # (재대입이 없으면 mlan1 으로 떠도 hint 경로가 mlan0 으로 남아 bgscan touch 와 불일치.)
    src = open(os.path.join(os.path.dirname(__file__), "..", "wifi_roam.py")).read()
    main_idx = src.index('if __name__ == "__main__"')
    main_block = src[main_idx:]
    assert 'ROAM_HINT_FILE = f"/tmp/wifi_roam_hint_{IFACE}"' in main_block

# --- same-SSID roam 실패 시 backoff 전진 (메인루프 wiring) ---

class _StopLoop(Exception):
    """interruptible_sleep 호출 횟수로 main() 무한 루프를 종료시키는 테스트 전용 신호."""


def _drive_main(monkeypatch, roam_results, ticks, rssi_seq=None, gate=False):
    """main() 루프를 interruptible_sleep 기준 ticks 회 돌리고 sleep 값 목록을 반환.

    roam_results: roam_to_bssid 반환값 시퀀스 — 앞에서부터 소비하고 마지막 값을 반복.
    rssi_seq: tick별 station rssi 시퀀스(마지막 값 반복) — 미지정 시 -70 고정(항상 조건).
    gate=True 면 good-signal 게이트를 켜서(Δ2dB, grace 0) 임계 위 tick 의 리셋 판정까지
    관측한다. 기본은 매 tick 로밍 조건(rssi<th)+후보 발견이 성립하도록 고정해,
    same-SSID 시도 결과(성공/실패)에 따른 sleep 경로만 관측한다.
    """
    _set_sleep(monkeypatch, 3, 30, fast=3)
    if gate:
        monkeypatch.setattr(wifi_roam, "ENABLE_GOOD_SIGNAL_GATE", True)
        monkeypatch.setattr(wifi_roam, "GOOD_SIGNAL_GATE_DELTA_DB", 2)
        monkeypatch.setattr(wifi_roam, "GOOD_SIGNAL_GATE_GRACE_SEC", 0)
    monkeypatch.setattr(wifi_roam, "ENABLE_PREDICTIVE_ROAM", False)
    monkeypatch.setattr(wifi_roam, "ENABLE_PING_PONG_PREVENTION", False)
    monkeypatch.setattr(wifi_roam, "GENERATE_NETWORK_BLOCKS", False)
    monkeypatch.setattr(wifi_roam, "ENABLE_STAGED_SCAN", True)
    monkeypatch.setattr(wifi_roam, "WPA_SSID", "net1")
    monkeypatch.setattr(wifi_roam, "WPA_TH_2G", -65)
    monkeypatch.setattr(wifi_roam, "CHECK_INTERVAL", 1)
    monkeypatch.setattr(wifi_roam, "_RELOAD_STATE", {"pending": False})
    monkeypatch.setattr(wifi_roam, "set_flag", lambda *a, **k: None)
    monkeypatch.setattr(
        wifi_roam, "reload_supplicant_conf_if_changed", lambda *a, **k: None
    )
    monkeypatch.setattr(wifi_roam, "roam_hint_touched", lambda state: False)
    station = {"bssid": "aa:aa:aa:aa:aa:01", "rssi": -70, "freq": 2412, "ssid": "net1"}
    rssi_iter = list(rssi_seq) if rssi_seq else None

    def fake_link():
        s = dict(station)
        if rssi_iter:
            s["rssi"] = rssi_iter.pop(0) if len(rssi_iter) > 1 else rssi_iter[0]
        return s

    monkeypatch.setattr(wifi_roam, "get_link_info", fake_link)
    best = {"bssid": "aa:aa:aa:aa:aa:02", "ssid": "net1", "rssi": -55, "freq": 2437}
    monkeypatch.setattr(
        wifi_roam,
        "staged_scan_best_candidate",
        lambda *a, **k: (dict(best), "rssi", 10.0, True),
    )
    results = list(roam_results)

    def fake_roam(*a, **k):
        return results.pop(0) if len(results) > 1 else results[0]

    monkeypatch.setattr(wifi_roam, "roam_to_bssid", fake_roam)
    monkeypatch.setattr(wifi_roam.time, "sleep", lambda s: None)
    sleeps = []

    def fake_isleep(sec):
        sleeps.append(sec)
        if len(sleeps) >= ticks:
            raise _StopLoop

    monkeypatch.setattr(wifi_roam, "interruptible_sleep", fake_isleep)
    with pytest.raises(_StopLoop):
        wifi_roam.main()
    return sleeps


def test_main_roam_failure_sleeps_escalate(monkeypatch):
    """반복 실패 시 후보없음과 동일한 플래토 곡선으로 sleep 에스컬레이션.

    종전엔 후보 발견 시점 리셋 탓에 실패해도 CHECK_INTERVAL(1s) 고정 재시도였다."""
    sleeps = _drive_main(monkeypatch, [False], ticks=13)
    assert sleeps == [3, 3, 3, 6, 6, 6, 12, 12, 12, 24, 24, 24, 30]


def test_main_roam_failure_backoff_caps(monkeypatch):
    # 상한(30s) 도달 후 계속 실패해도 상한 유지(무한 증가 없음).
    sleeps = _drive_main(monkeypatch, [False], ticks=20)
    assert set(sleeps[12:]) == {30}


def test_main_roam_success_resets_backoff(monkeypatch):
    # 실패 5회(3,3,3,6,6) → 성공 확정 tick 은 interval(1s) → 이후 실패는 시작값 3부터.
    sleeps = _drive_main(monkeypatch, [False] * 5 + [True, False], ticks=8)
    assert sleeps == [3, 3, 3, 6, 6, 1, 3, 3]


def test_main_roam_success_keeps_interval(monkeypatch):
    # 성공만 이어지면 종전과 동일하게 interval(CHECK_INTERVAL) sleep 유지(무회귀).
    sleeps = _drive_main(monkeypatch, [True], ticks=3)
    assert sleeps == [1, 1, 1]


def test_main_good_signal_oscillation_keeps_failure_backoff(monkeypatch):
    """실패 에스컬레이션 중 임계 위 Δ1dB 진동 1 tick 이 backoff 를 리셋하지 못한다.

    후보 발견 시 baseline 을 현재 RSSI 로 앵커하지 않으면(None 잔존) good-signal 이
    no-baseline 으로 무조건 리셋을 허용해 에스컬레이션이 3s 로 되돌아간다(#155 리뷰).
    TH=-65: -66 실패 3틱(3,3,3) → -65 good-signal(Δ1 억제, interval 1s) → -66 실패는
    종전 streak 에서 재전진(6s)."""
    sleeps = _drive_main(
        monkeypatch, [False], ticks=5, rssi_seq=[-66, -66, -66, -65, -66], gate=True
    )
    assert sleeps == [3, 3, 3, 1, 6]
