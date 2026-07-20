"""reload_roaming_config_if_changed 회귀 테스트 — 런타임 config reload 4중 방어.

계약: mtime 변화 + 1사이클 디바운스 후, json.loads 선검증 성공 시에만 적용.
invalid 저장은 현행 유지(기본값 회귀 금지)+mtime당 경고 1회. generate_network_blocks
는 재시작 전용(경고 후 유지). 인스턴스는 필드 갱신으로 이력 보존, enable off→on은
생성. 적용 성공 시 WPA_CONF_MTIME 리셋(같은 사이클 wpa conf 재파싱 → TH 전파).
"""
import json
import os
import sys
import time
from unittest.mock import MagicMock

sys.modules.setdefault("sUTILS", MagicMock())
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wifi_roam
from wifi_roam import reload_roaming_config_if_changed

import pytest

wifi_roam.logger = MagicMock()

IFACE = "mlan0"


def _conf(check_interval=3, gen=False, pp_enable=True, pp_window=30):
    return {
        IFACE: {
            "roaming": {
                "CHECK_INTERVAL": check_interval,
                "generate_network_blocks": gen,
                "PING_PONG_PREVENTION": {
                    "enable": pp_enable,
                    "window": pp_window,
                    "max_roams_in_window": 3,
                    "detection_time": 10,
                },
            }
        }
    }


def _write(path, obj_or_text, mtime_s):
    """파일 쓰기 + mtime 명시 설정(디바운스/변경 감지를 결정적으로)."""
    text = obj_or_text if isinstance(obj_or_text, str) else json.dumps(obj_or_text)
    path.write_text(text)
    ns = int(mtime_s * 1e9)
    os.utime(str(path), ns=(ns, ns))


@pytest.fixture
def env(tmp_path, monkeypatch):
    """전역 오염 방지: 관련 전역 전부 monkeypatch(teardown 자동 복원) + 상태 리셋."""
    p = tmp_path / "wifi_init_conf.json"
    monkeypatch.setattr(wifi_roam, "WIFI_INIT_CONF_JSON", str(p))
    monkeypatch.setattr(
        wifi_roam, "ROAM_JSON_RELOAD_STATE", {"applied": None, "seen": None, "warned": None}
    )
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


def _warn_calls(substr):
    return [
        c for c in wifi_roam.logger.message.call_args_list
        if substr in str(c.args[1] if len(c.args) > 1 else "")
    ]


def test_first_call_records_baseline_only(env):
    _write(env, _conf(), 1000.0)
    assert reload_roaming_config_if_changed(IFACE) is False
    assert wifi_roam.CHECK_INTERVAL == 7            # 적용 안 함(기준점 기록만)
    assert wifi_roam.ROAM_JSON_RELOAD_STATE["applied"] is not None


def test_debounce_then_apply(env):
    _write(env, _conf(), 1000.0)
    reload_roaming_config_if_changed(IFACE)          # baseline
    _write(env, _conf(check_interval=2), 1010.0)     # 값 변경 + 새 mtime
    assert reload_roaming_config_if_changed(IFACE) is False   # 디바운스(관측만)
    assert wifi_roam.CHECK_INTERVAL == 7
    assert reload_roaming_config_if_changed(IFACE) is True    # 안정화 → 적용
    assert wifi_roam.CHECK_INTERVAL == 2
    assert wifi_roam.WPA_CONF_MTIME is None          # 같은 사이클 wpa conf 재파싱 유도
    assert len(_warn_calls("runtime roaming config reloaded")) == 1


def test_invalid_json_keeps_current_and_warns_once(env):
    _write(env, _conf(), 1000.0)
    reload_roaming_config_if_changed(IFACE)          # baseline
    _write(env, '{"mlan0": {broken', 1010.0)         # invalid 저장(에디터 중간 상태)
    reload_roaming_config_if_changed(IFACE)          # 디바운스
    assert reload_roaming_config_if_changed(IFACE) is False
    assert wifi_roam.CHECK_INTERVAL == 7             # 현행 유지(기본값 회귀 금지)
    assert wifi_roam.WPA_CONF_MTIME == 123.0         # 미적용이므로 리셋도 없음
    reload_roaming_config_if_changed(IFACE)          # 같은 mtime 재시도
    assert len(_warn_calls("invalid JSON")) == 1     # 경고는 mtime당 1회
    # 이후 정상 저장 → 자동 회복
    _write(env, _conf(check_interval=2), 1020.0)
    reload_roaming_config_if_changed(IFACE)
    assert reload_roaming_config_if_changed(IFACE) is True
    assert wifi_roam.CHECK_INTERVAL == 2


def test_generate_change_blocked_at_runtime(env):
    _write(env, _conf(), 1000.0)
    reload_roaming_config_if_changed(IFACE)
    _write(env, _conf(gen=True), 1010.0)
    reload_roaming_config_if_changed(IFACE)
    assert reload_roaming_config_if_changed(IFACE) is True
    assert wifi_roam.GENERATE_NETWORK_BLOCKS is False        # 재시작 전용 — 유지
    assert len(_warn_calls("requires daemon restart")) == 1


def test_pingpong_history_preserved_on_param_change(env):
    prev = wifi_roam.PingPongPreventer(30, 3)
    prev.roam_history.append((time.time(), "aa", "bb"))      # 기존 이력
    wifi_roam.ping_pong_preventer = prev
    _write(env, _conf(), 1000.0)
    reload_roaming_config_if_changed(IFACE)
    _write(env, _conf(pp_window=60), 1010.0)
    reload_roaming_config_if_changed(IFACE)
    assert reload_roaming_config_if_changed(IFACE) is True
    assert wifi_roam.ping_pong_preventer is prev             # 재생성 아님
    assert prev.window_seconds == 60                         # 파라미터 갱신
    assert len(prev.roam_history) == 1                       # 이력 보존


def test_symmetric_instances_param_update_preserves_state(env):
    """trend/adaptive/cooldown도 재생성 아닌 필드 갱신(이력·상태 보존) — 대칭 경로 커버."""
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
    _write(env, conf, 1000.0)
    reload_roaming_config_if_changed(IFACE)          # baseline
    conf[IFACE]["roaming"]["CHECK_INTERVAL"] = 2     # 변경 유발
    _write(env, conf, 1010.0)
    reload_roaming_config_if_changed(IFACE)
    assert reload_roaming_config_if_changed(IFACE) is True
    assert wifi_roam.trend_tracker is tt and tt.window_size == 9 and tt.max_age == 99
    assert len(tt.rssi_history) == 1                 # 이력 보존
    assert wifi_roam.adaptive_interval is ai and ai.min_interval == 2 and ai.max_interval == 20
    assert wifi_roam.cross_ssid_cooldown is cd and cd.retry_count == 5
    assert cd.entries["Net"]["fails"] == 1           # cooldown 상태 보존


def test_enable_off_to_on_creates_instance(env):
    wifi_roam.ENABLE_PING_PONG_PREVENTION = False
    wifi_roam.ping_pong_preventer = None
    _write(env, _conf(pp_enable=True), 1000.0)
    reload_roaming_config_if_changed(IFACE)                  # baseline
    _write(env, _conf(pp_enable=True, pp_window=60), 1010.0)
    reload_roaming_config_if_changed(IFACE)
    assert reload_roaming_config_if_changed(IFACE) is True
    assert wifi_roam.ENABLE_PING_PONG_PREVENTION is True
    assert wifi_roam.ping_pong_preventer is not None         # off→on 생성


def test_valid_json_but_not_dict_keeps_current(env):
    """구문은 valid지만 dict가 아님(예: 123) → 구조 검증에서 차단, 현행 유지."""
    _write(env, _conf(), 1000.0)
    reload_roaming_config_if_changed(IFACE)          # baseline
    _write(env, "123", 1010.0)
    reload_roaming_config_if_changed(IFACE)          # 디바운스
    assert reload_roaming_config_if_changed(IFACE) is False
    assert wifi_roam.CHECK_INTERVAL == 7             # 기본값 회귀 없음
    assert len(_warn_calls("no valid")) == 1


def test_missing_iface_section_keeps_current(env):
    """iface 키 부재(다른 iface만 있는 valid JSON) → 조용한 기본값 회귀 차단."""
    _write(env, _conf(), 1000.0)
    reload_roaming_config_if_changed(IFACE)          # baseline
    _write(env, {"mlan1": {"roaming": {"CHECK_INTERVAL": 2}}}, 1010.0)
    reload_roaming_config_if_changed(IFACE)
    assert reload_roaming_config_if_changed(IFACE) is False
    assert wifi_roam.CHECK_INTERVAL == 7
    assert len(_warn_calls("no valid")) == 1


def test_reload_passes_validated_data_no_reparse(env, monkeypatch):
    """★TOCTOU 차단: 검증한 파싱 결과가 그대로 load에 전달(파일 재파싱 없음)."""
    captured = {}
    real = wifi_roam.load_roaming_config

    def spy(iface, data=None):
        captured["data"] = data
        return real(iface, data=data)

    monkeypatch.setattr(wifi_roam, "load_roaming_config", spy)
    _write(env, _conf(), 1000.0)
    reload_roaming_config_if_changed(IFACE)          # baseline
    _write(env, _conf(check_interval=2), 1010.0)
    reload_roaming_config_if_changed(IFACE)
    assert reload_roaming_config_if_changed(IFACE) is True
    assert captured["data"] is not None              # 검증본 전달(재파싱 아님)
    assert captured["data"][IFACE]["roaming"]["CHECK_INTERVAL"] == 2
    assert wifi_roam.CHECK_INTERVAL == 2


def test_unchanged_mtime_noop(env):
    _write(env, _conf(), 1000.0)
    reload_roaming_config_if_changed(IFACE)
    assert reload_roaming_config_if_changed(IFACE) is False  # 동일 mtime — no-op
    assert wifi_roam.CHECK_INTERVAL == 7


def test_missing_file_noop(env):
    # env 픽스처는 경로만 지정 — 파일을 만들지 않은 상태(부재) 그대로 호출
    assert reload_roaming_config_if_changed(IFACE) is False
    assert wifi_roam.CHECK_INTERVAL == 7
