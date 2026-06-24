import sys
import os
from unittest.mock import MagicMock

# sUTILS has heavy runtime deps; stub before importing wifi_roam.
sys.modules.setdefault("sUTILS", MagicMock())

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wifi_roam

import pytest

# logger is assigned at runtime; stub so config/error paths can call it.
wifi_roam.logger = MagicMock()

@pytest.fixture(autouse=True)
def _reset_globals(monkeypatch):
    # 각 테스트가 전역 상태에 독립이도록 기본값으로 리셋
    monkeypatch.setattr(wifi_roam, "EXTRA_SSIDS", [])
    monkeypatch.setattr(wifi_roam, "WPA_SSID", "BaseNet")
    monkeypatch.setattr(wifi_roam, "GENERATE_NETWORK_BLOCKS", False)
    yield

# --- get_allowed_ssids extra 게이트 ---

def test_mode_b_ignores_extra(monkeypatch):
    # 모드 B(generate=false): extra_ssids가 있어도 무시 → [live, WPA_SSID]
    monkeypatch.setattr(wifi_roam, "EXTRA_SSIDS", ["Office", "Guest"])
    monkeypatch.setattr(wifi_roam, "GENERATE_NETWORK_BLOCKS", False)
    assert wifi_roam.get_allowed_ssids("LiveNet") == ["LiveNet", "BaseNet"]

def test_mode_a_includes_extra(monkeypatch):
    # 모드 A(generate=true): extra 합집합
    monkeypatch.setattr(wifi_roam, "EXTRA_SSIDS", ["Office", "Guest"])
    monkeypatch.setattr(wifi_roam, "GENERATE_NETWORK_BLOCKS", True)
    assert wifi_roam.get_allowed_ssids("LiveNet") == ["LiveNet", "BaseNet", "Office", "Guest"]

def test_mode_a_dedups_extra(monkeypatch):
    # 모드 A: live/WPA_SSID와 중복되는 extra는 제거
    monkeypatch.setattr(wifi_roam, "EXTRA_SSIDS", ["BaseNet", "Office"])
    monkeypatch.setattr(wifi_roam, "GENERATE_NETWORK_BLOCKS", True)
    assert wifi_roam.get_allowed_ssids("LiveNet") == ["LiveNet", "BaseNet", "Office"]

def test_mode_b_no_extra_single_ssid(monkeypatch):
    # 모드 B + extra 없음 + live==WPA_SSID → [WPA_SSID] (기존 단일 SSID 무회귀)
    monkeypatch.setattr(wifi_roam, "EXTRA_SSIDS", [])
    monkeypatch.setattr(wifi_roam, "GENERATE_NETWORK_BLOCKS", False)
    assert wifi_roam.get_allowed_ssids("BaseNet") == ["BaseNet"]

# --- generate_network_blocks 파싱 ---

def test_parse_generate_true(tmp_path, monkeypatch):
    import json
    conf = {"mlan0": {"roaming": {"generate_network_blocks": True, "extra_ssids": ["X"]}}}
    p = tmp_path / "wifi_init_conf.json"
    p.write_text(json.dumps(conf))
    monkeypatch.setattr(wifi_roam, "WIFI_INIT_CONF_JSON", str(p))
    monkeypatch.setattr(wifi_roam, "GENERATE_NETWORK_BLOCKS", False)
    wifi_roam.load_roaming_config("mlan0")
    assert wifi_roam.GENERATE_NETWORK_BLOCKS is True

def test_parse_generate_default_false(tmp_path, monkeypatch):
    # 키 부재 시 False로 수렴 (모드 B 기본)
    import json
    conf = {"mlan0": {"roaming": {"extra_ssids": ["X"]}}}
    p = tmp_path / "wifi_init_conf.json"
    p.write_text(json.dumps(conf))
    monkeypatch.setattr(wifi_roam, "WIFI_INIT_CONF_JSON", str(p))
    monkeypatch.setattr(wifi_roam, "GENERATE_NETWORK_BLOCKS", True)  # 일부러 true로 두고 false 수렴 확인
    wifi_roam.load_roaming_config("mlan0")
    assert wifi_roam.GENERATE_NETWORK_BLOCKS is False
