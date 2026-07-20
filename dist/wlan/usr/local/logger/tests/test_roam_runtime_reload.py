"""SIGHUP 트리거 런타임 config reload 테스트 — 폴링 없음(프로덕션 비용 0).

계약: SIGHUP 수신 시 wifi_init_conf.json 을 1회 재읽어 적용(mtime 디바운스 없음 —
신호 시점이 곧 '쓰기 완료'). invalid/구조무효 저장은 현행 유지(기본값 회귀 금지)+경고.
generate_network_blocks/모드 A extra_ssids 는 재시작 전용(경고 후 유지). 인스턴스는
필드 갱신으로 이력 보존, enable off→on 은 생성. 적용 시 WPA_CONF_MTIME 리셋(wpa conf
재파싱 → TH 전파). interruptible_sleep 은 신호 시 즉시 깨는 커널 블록(폴링 아님).
"""
import json
import os
import sys
import time
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("sUTILS", MagicMock())
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wifi_roam
from wifi_roam import reload_roaming_config, interruptible_sleep, handle_sighup

wifi_roam.logger = MagicMock()

IFACE = "mlan0"


def _conf(check_interval=3, gen=False, pp_enable=True, pp_window=30, extra=None):
    roaming = {
        "CHECK_INTERVAL": check_interval,
        "generate_network_blocks": gen,
        "PING_PONG_PREVENTION": {
            "enable": pp_enable,
            "window": pp_window,
            "max_roams_in_window": 3,
            "detection_time": 10,
        },
    }
    if extra is not None:
        roaming["extra_ssids"] = extra
    return {IFACE: {"roaming": roaming}}


def _write(path, obj_or_text):
    text = obj_or_text if isinstance(obj_or_text, str) else json.dumps(obj_or_text)
    path.write_text(text)


def _warn_calls(substr):
    return [
        c for c in wifi_roam.logger.message.call_args_list
        if substr in str(c.args[1] if len(c.args) > 1 else "")
    ]


@pytest.fixture(autouse=True)
def _reset_signal_state():
    """self-pipe/pending 을 각 테스트 전후로 비운다(모듈 전역 공유)."""
    def drain():
        wifi_roam._RELOAD_STATE["pending"] = False
        if wifi_roam._SIG_PIPE_R is not None:
            try:
                while os.read(wifi_roam._SIG_PIPE_R, 4096):
                    pass
            except OSError:
                pass
    drain()
    yield
    drain()


@pytest.fixture
def env(tmp_path, monkeypatch):
    """전역 오염 방지: 관련 전역 전부 monkeypatch(teardown 자동 복원)."""
    p = tmp_path / "wifi_init_conf.json"
    monkeypatch.setattr(wifi_roam, "WIFI_INIT_CONF_JSON", str(p))
    for name, val in [
        ("CHECK_INTERVAL", 7),                 # sentinel — reload로 바뀌는지/유지되는지 관찰
        ("GENERATE_NETWORK_BLOCKS", False),
        ("ENABLE_PING_PONG_PREVENTION", True),
        ("ENABLE_PREDICTIVE_ROAM", False),
        ("ENABLE_ADAPTIVE_INTERVAL", False),
        ("PING_PONG_WINDOW", 30),
        ("MAX_ROAMS_IN_WINDOW", 3),
        ("ping_pong_preventer", None),
        ("trend_tracker", None),
        ("adaptive_interval", None),
        ("cross_ssid_cooldown", None),
        ("WPA_CONF_MTIME", 123.0),             # sentinel — 적용 시 None 리셋 확인
        ("EXTRA_SSIDS", []),
    ]:
        monkeypatch.setattr(wifi_roam, name, val)
    wifi_roam.logger.reset_mock()
    return p


# --- reload_roaming_config: 신호 수신 시 1회 재읽어 적용 ---

def test_valid_change_applies(env):
    _write(env, _conf(check_interval=2))
    assert reload_roaming_config(IFACE) is True
    assert wifi_roam.CHECK_INTERVAL == 2
    assert wifi_roam.WPA_CONF_MTIME is None       # 같은 사이클 wpa conf 재파싱 유도
    assert len(_warn_calls("runtime roaming config reloaded")) == 1


def test_invalid_json_keeps_current_and_warns(env):
    _write(env, '{"mlan0": {broken')             # 에디터 중간 상태(파손) — 신호가 일찍 온 경우
    assert reload_roaming_config(IFACE) is False
    assert wifi_roam.CHECK_INTERVAL == 7          # 현행 유지(기본값 회귀 금지)
    assert wifi_roam.WPA_CONF_MTIME == 123.0      # 미적용 → 리셋 없음
    assert len(_warn_calls("invalid JSON")) == 1


def test_no_roaming_section_keeps_current(env):
    _write(env, {"other": 1})                     # valid JSON 이나 mlan0.roaming 없음
    assert reload_roaming_config(IFACE) is False
    assert wifi_roam.CHECK_INTERVAL == 7
    assert len(_warn_calls("no valid mlan0.roaming section")) == 1


def test_missing_file_keeps_current(env):
    # 파일 부재 그대로 호출 — 크래시 없이 현행 유지 + 경고
    assert reload_roaming_config(IFACE) is False
    assert wifi_roam.CHECK_INTERVAL == 7
    assert len(_warn_calls("invalid JSON")) == 1


def test_generate_change_blocked_at_runtime(env):
    _write(env, _conf(gen=True))
    assert reload_roaming_config(IFACE) is True    # 나머지 키는 적용되므로 True
    assert wifi_roam.GENERATE_NETWORK_BLOCKS is False   # 재시작 전용 — 유지
    assert len(_warn_calls("requires daemon restart")) == 1


def test_mode_a_extra_ssids_change_blocked(env):
    wifi_roam.GENERATE_NETWORK_BLOCKS = True        # 모드 A
    wifi_roam.EXTRA_SSIDS = ["a"]
    _write(env, _conf(gen=True, extra=["a", "b"]))  # gen 유지, extra만 변경
    assert reload_roaming_config(IFACE) is True
    assert wifi_roam.EXTRA_SSIDS == ["a"]           # 롤백(블록 부팅 생성 종속)
    assert len(_warn_calls("extra_ssids change ignored")) == 1


def test_mode_b_extra_ssids_applies(env):
    # 모드 B(gen=False)는 extra_ssids 변경 그대로 적용(데몬 무영향, 재생성 불필요)
    _write(env, _conf(gen=False, extra=["x"]))
    assert reload_roaming_config(IFACE) is True
    assert wifi_roam.EXTRA_SSIDS == ["x"]


def test_pingpong_history_preserved_on_param_change(env):
    prev = wifi_roam.PingPongPreventer(30, 3)
    prev.roam_history.append((time.time(), "aa", "bb"))
    wifi_roam.ping_pong_preventer = prev
    _write(env, _conf(pp_window=60))
    assert reload_roaming_config(IFACE) is True
    assert wifi_roam.ping_pong_preventer is prev    # 재생성 아님
    assert prev.window_seconds == 60                # 파라미터 갱신
    assert len(prev.roam_history) == 1              # 이력 보존


def test_symmetric_instances_param_update_preserves_state(env):
    tt = wifi_roam.RSSITrendTracker(5, 30)
    tt.rssi_history.append((time.time(), -50))
    ai = wifi_roam.AdaptiveInterval(1, 10)
    cd = wifi_roam.CrossSsidCooldown(2)
    cd.entries["Net"] = {"fails": 1, "until": 0.0}
    wifi_roam.ENABLE_PREDICTIVE_ROAM = True
    wifi_roam.ENABLE_ADAPTIVE_INTERVAL = True
    wifi_roam.trend_tracker = tt
    wifi_roam.adaptive_interval = ai
    wifi_roam.cross_ssid_cooldown = cd
    conf = _conf()
    conf[IFACE]["roaming"]["PREDICTIVE_ROAM"] = {
        "enable": True, "trend_window_size": 9, "trend_history_max_age": 99}
    conf[IFACE]["roaming"]["ADAPTIVE_INTERVAL"] = {
        "enable": True, "min_check_interval": 2, "max_check_interval": 20}
    conf[IFACE]["roaming"]["ROAM_CROSS_FAIL_RETRY_COUNT"] = 5
    _write(env, conf)
    assert reload_roaming_config(IFACE) is True
    assert wifi_roam.trend_tracker is tt and tt.window_size == 9 and tt.max_age == 99
    assert len(tt.rssi_history) == 1
    assert wifi_roam.adaptive_interval is ai and ai.min_interval == 2 and ai.max_interval == 20
    assert wifi_roam.cross_ssid_cooldown is cd and cd.retry_count == 5
    assert cd.entries["Net"]["fails"] == 1


def test_enable_off_to_on_creates_instance(env):
    wifi_roam.ENABLE_PING_PONG_PREVENTION = False
    wifi_roam.ping_pong_preventer = None
    _write(env, _conf(pp_enable=True, pp_window=60))
    assert reload_roaming_config(IFACE) is True
    assert wifi_roam.ENABLE_PING_PONG_PREVENTION is True
    assert wifi_roam.ping_pong_preventer is not None     # off→on 생성


def test_enable_on_to_off_keeps_instance_gate_off(env):
    prev = wifi_roam.PingPongPreventer(30, 3)
    wifi_roam.ENABLE_PING_PONG_PREVENTION = True
    wifi_roam.ping_pong_preventer = prev
    _write(env, _conf(pp_enable=False))
    assert reload_roaming_config(IFACE) is True
    assert wifi_roam.ENABLE_PING_PONG_PREVENTION is False
    assert wifi_roam.ping_pong_preventer is prev         # 인스턴스 잔존(게이트가 미사용 보장)


# --- handle_sighup / interruptible_sleep: 폴링 없는 신호 기반 대기 ---

@pytest.mark.skipif(wifi_roam._SIG_PIPE_R is None, reason="self-pipe unavailable")
def test_handle_sighup_sets_pending_and_wakes():
    wifi_roam._RELOAD_STATE["pending"] = False
    handle_sighup(1, None)
    assert wifi_roam._RELOAD_STATE["pending"] is True
    # self-pipe 에 바이트가 들어와 select 가 즉시 반환(대기 중 데몬 깨우기)
    ready, _, _ = wifi_roam.select.select([wifi_roam._SIG_PIPE_R], [], [], 0)
    assert ready == [wifi_roam._SIG_PIPE_R]


@pytest.mark.skipif(wifi_roam._SIG_PIPE_R is None, reason="self-pipe unavailable")
def test_interruptible_sleep_returns_and_drains_on_signal():
    os.write(wifi_roam._SIG_PIPE_W, b"x")           # 신호 대기 상태
    interruptible_sleep(5)                           # 5초 안 기다리고 즉시 반환해야 함
    with pytest.raises(BlockingIOError):             # drain 됐으므로 파이프 비어있음
        os.read(wifi_roam._SIG_PIPE_R, 1)


def test_interruptible_sleep_no_signal_uses_full_timeout(monkeypatch):
    calls = {"n": 0, "timeout": None}
    def fake_select(r, w, x, timeout):
        calls["n"] += 1
        calls["timeout"] = timeout
        return ([], [], [])                          # 타임아웃(신호 없음)
    monkeypatch.setattr(wifi_roam.select, "select", fake_select)
    interruptible_sleep(0.05)
    assert calls["n"] == 1 and calls["timeout"] == 0.05


def test_interruptible_sleep_zero_or_negative_is_noop(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("select must not be called for <=0/None")
    monkeypatch.setattr(wifi_roam.select, "select", boom)
    interruptible_sleep(0)
    interruptible_sleep(-1)
    interruptible_sleep(None)


def test_interruptible_sleep_falls_back_to_time_sleep_without_pipe(monkeypatch):
    monkeypatch.setattr(wifi_roam, "_SIG_PIPE_R", None)
    slept = {"s": None}
    monkeypatch.setattr(wifi_roam.time, "sleep", lambda s: slept.__setitem__("s", s))
    interruptible_sleep(3)
    assert slept["s"] == 3


@pytest.mark.skipif(wifi_roam._SIG_PIPE_R is None, reason="self-pipe unavailable")
def test_interruptible_sleep_select_exception_falls_back(monkeypatch):
    """select 가 OSError(예: fd 무효화) 를 던져도 데몬이 죽지 않고 time.sleep 폴백."""
    def raise_oserror(*a, **k):
        raise OSError(9, "Bad file descriptor")
    monkeypatch.setattr(wifi_roam.select, "select", raise_oserror)
    slept = {"s": None}
    monkeypatch.setattr(wifi_roam.time, "sleep", lambda s: slept.__setitem__("s", s))
    interruptible_sleep(3)
    assert slept["s"] == 3


@pytest.mark.skipif(wifi_roam._SIG_PIPE_R is None, reason="self-pipe unavailable")
def test_interruptible_sleep_woken_mid_block_by_write():
    """블록된 select 가 '뒤늦게' 도착한 pipe write 로 조기 깨어난다 — 이 변경의 핵심
    기전(긴 backoff sleep 중 SIGHUP 이 대기를 즉시 깨움)을 실제 블로킹으로 검증."""
    import threading
    def waker():
        time.sleep(0.2)
        os.write(wifi_roam._SIG_PIPE_W, b"x")
    t = threading.Thread(target=waker)
    start = time.monotonic()
    t.start()
    interruptible_sleep(5.0)                 # 5초 예정이나 ~0.2s write 로 조기 반환해야
    elapsed = time.monotonic() - start
    t.join()
    assert elapsed < 2.0                     # 5초 훨씬 전에 반환(블록 중 깨어남)
    with pytest.raises(BlockingIOError):     # drain 됐으므로 파이프 비어있음
        os.read(wifi_roam._SIG_PIPE_R, 1)


def test_bad_numeric_value_no_crash_keeps_current(env, monkeypatch):
    """비수치 값(operator 오타)이 와도 데몬 크래시 없이 현행 유지 + 유효 키는 적용
    (Codex P1 가드 — SIGHUP reload 는 hand-edit 직후 발동해 오타 최빈 트리거)."""
    monkeypatch.setattr(wifi_roam, "SCAN_NO_RESULT_SLEEP", 3)   # 방어 시 유지되어야
    conf = _conf(check_interval=5)
    conf[IFACE]["roaming"]["SCAN_NO_RESULT_SLEEP"] = "bad"      # 비수치 오타
    _write(env, conf)
    assert reload_roaming_config(IFACE) is True                 # 크래시 없이 적용 수행
    assert wifi_roam.CHECK_INTERVAL == 5                        # 유효 키는 적용
    assert wifi_roam.SCAN_NO_RESULT_SLEEP == 3                  # bad 값은 _num 방어로 현행 유지


def test_gen_key_absent_no_spurious_warning(env):
    """모드 A 데몬에서 generate_network_blocks 키 부재 config → 조용히 복원(오탐 경고 없음)."""
    wifi_roam.GENERATE_NETWORK_BLOCKS = True
    wifi_roam.EXTRA_SSIDS = ["a"]
    conf = _conf()
    del conf[IFACE]["roaming"]["generate_network_blocks"]       # 키 부재(parse_bool False 수렴)
    _write(env, conf)
    assert reload_roaming_config(IFACE) is True
    assert wifi_roam.GENERATE_NETWORK_BLOCKS is True            # 재시작 전용 — 복원
    assert wifi_roam.EXTRA_SSIDS == ["a"]                       # 연동 복원
    assert len(_warn_calls("requires daemon restart")) == 0     # 키 부재는 오탐 경고 없음
