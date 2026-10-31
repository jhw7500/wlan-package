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
    monkeypatch.setattr(wifi_roam, "EXTRA_SSIDS_IN_ROAM_SCAN", True)
    yield

# --- get_allowed_ssids extra 게이트 ---

def test_mode_b_ignores_extra(monkeypatch):
    # 모드 B의 로밍 후보는 extra_ssids를 포함하지 않는다.
    monkeypatch.setattr(wifi_roam, "EXTRA_SSIDS", ["Office", "Guest"])
    monkeypatch.setattr(wifi_roam, "GENERATE_NETWORK_BLOCKS", False)
    assert wifi_roam.get_allowed_ssids("LiveNet") == ["LiveNet", "BaseNet"]


def test_mode_b_probes_extra_without_admitting_it_as_a_candidate(monkeypatch):
    monkeypatch.setattr(wifi_roam, "EXTRA_SSIDS", ["Office", "Guest"])
    monkeypatch.setattr(wifi_roam, "GENERATE_NETWORK_BLOCKS", False)
    candidates = wifi_roam.get_allowed_ssids("LiveNet")

    assert wifi_roam.get_scan_probe_ssids(candidates, "LiveNet") == [
        "LiveNet", "BaseNet", "Office", "Guest"
    ]
    assert candidates == ["LiveNet", "BaseNet"]


def test_mode_b_can_exclude_extra_from_roam_scan_without_changing_candidates(monkeypatch):
    monkeypatch.setattr(wifi_roam, "EXTRA_SSIDS", ["Office", "Guest"])
    monkeypatch.setattr(wifi_roam, "EXTRA_SSIDS_IN_ROAM_SCAN", False)
    candidates = wifi_roam.get_allowed_ssids("LiveNet")

    assert wifi_roam.get_scan_probe_ssids(candidates, "LiveNet") == ["LiveNet", "BaseNet"]
    assert candidates == ["LiveNet", "BaseNet"]


def test_mode_b_keeps_current_extra_ssid_in_probe_list(monkeypatch):
    monkeypatch.setattr(wifi_roam, "EXTRA_SSIDS", ["Office", "Guest"])
    monkeypatch.setattr(wifi_roam, "EXTRA_SSIDS_IN_ROAM_SCAN", False)

    candidates = wifi_roam.get_allowed_ssids("Office")
    assert candidates == ["Office", "BaseNet"]
    assert wifi_roam.get_scan_probe_ssids(candidates, "Office") == [
        "Office", "BaseNet"
    ]

def test_mode_a_includes_extra(monkeypatch):
    # 모드 A(generate=true): extra 합집합
    monkeypatch.setattr(wifi_roam, "EXTRA_SSIDS", ["Office", "Guest"])
    monkeypatch.setattr(wifi_roam, "GENERATE_NETWORK_BLOCKS", True)
    assert wifi_roam.get_allowed_ssids("LiveNet") == ["LiveNet", "BaseNet", "Office", "Guest"]
    assert wifi_roam.get_scan_probe_ssids(wifi_roam.get_allowed_ssids("LiveNet"), "LiveNet") == [
        "LiveNet", "BaseNet", "Office", "Guest"
    ]


def test_mode_a_can_exclude_extra_probes_without_removing_candidates(monkeypatch):
    monkeypatch.setattr(wifi_roam, "GENERATE_NETWORK_BLOCKS", True)
    monkeypatch.setattr(wifi_roam, "EXTRA_SSIDS", ["Office"])
    monkeypatch.setattr(wifi_roam, "EXTRA_SSIDS_IN_ROAM_SCAN", False)

    candidates = wifi_roam.get_allowed_ssids("LiveNet")
    assert candidates == ["LiveNet", "BaseNet", "Office"]
    assert wifi_roam.get_scan_probe_ssids(candidates, "LiveNet") == [
        "LiveNet", "BaseNet"
    ]


def test_mode_a_keeps_current_extra_ssid_in_probe_list(monkeypatch):
    monkeypatch.setattr(wifi_roam, "GENERATE_NETWORK_BLOCKS", True)
    monkeypatch.setattr(wifi_roam, "EXTRA_SSIDS", ["Office", "Guest"])
    monkeypatch.setattr(wifi_roam, "EXTRA_SSIDS_IN_ROAM_SCAN", False)

    candidates = wifi_roam.get_allowed_ssids("Office")
    assert candidates == ["Office", "BaseNet", "Guest"]
    assert wifi_roam.get_scan_probe_ssids(candidates, "Office") == [
        "Office", "BaseNet"
    ]

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
    assert wifi_roam.get_scan_probe_ssids(["BaseNet"], "BaseNet") == ["BaseNet"]

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


def test_boot_policy_overrides_mutated_live_topology(monkeypatch):
    monkeypatch.setattr(wifi_roam, "GENERATE_NETWORK_BLOCKS", False)
    monkeypatch.setattr(wifi_roam, "EXTRA_SSIDS", ["LiveJson"])
    wifi_roam.apply_boot_roam_policy({
        "roaming_enabled": True,
        "generate_network_blocks": True,
        "extra_ssids": [" BootOffice ", "Guest"],
    })
    assert wifi_roam.GENERATE_NETWORK_BLOCKS is True
    assert wifi_roam.EXTRA_SSIDS == [" BootOffice ", "Guest"]


def test_mode_b_boot_policy_keeps_probe_identities(monkeypatch):
    monkeypatch.setattr(wifi_roam, "EXTRA_SSIDS", ["LiveJson"])
    wifi_roam.apply_boot_roam_policy({
        "roaming_enabled": True,
        "generate_network_blocks": False,
        "extra_ssids": ["BootOffice", "Guest"],
    })

    assert wifi_roam.EXTRA_SSIDS == ["BootOffice", "Guest"]
    assert wifi_roam.get_allowed_ssids("BaseNet") == ["BaseNet"]


@pytest.mark.parametrize("extra_count,include_extra,expects_warning", [
    (8, True, False),
    (9, True, True),
    (9, False, True),
])
def test_boot_probe_limit_warning_preserves_snapshot_extras(
    monkeypatch, extra_count, include_extra, expects_warning
):
    extras = [f"Network{i}" for i in range(extra_count)]
    monkeypatch.setattr(wifi_roam, "EXTRA_SSIDS", extras)
    monkeypatch.setattr(wifi_roam, "GENERATE_NETWORK_BLOCKS", False)
    monkeypatch.setattr(wifi_roam, "EXTRA_SSIDS_IN_ROAM_SCAN", include_extra)
    log = MagicMock()
    monkeypatch.setattr(wifi_roam, "logger", log)

    wifi_roam.warn_scan_probe_limit()

    warnings = [
        call.args[1] for call in log.message.call_args_list
        if call.args[0] == "warn"
    ]
    assert bool(warnings) is expects_warning
    if warnings:
        assert "driver max 10" in warnings[0]
        assert not any(ssid in warnings[0] for ssid in extras)
    assert wifi_roam.EXTRA_SSIDS == extras


def test_boot_policy_rejects_stale_wifi_roam_owner():
    with pytest.raises(wifi_roam.RoamPolicyError):
        wifi_roam.apply_boot_roam_policy({
            "roaming_enabled": False,
            "generate_network_blocks": False,
            "extra_ssids": [],
        })


# --- 메인루프 cross 분기 AND 게이트 (should_cross_connect 헬퍼, live SSID 기준) ---

def test_cross_blocked_in_mode_b(monkeypatch):
    # 모드 B: best_ap가 다른 SSID여도 cross connect 진입 안 함
    monkeypatch.setattr(wifi_roam, "GENERATE_NETWORK_BLOCKS", False)
    assert wifi_roam.should_cross_connect("Office", "LiveNet") is False

def test_cross_allowed_in_mode_a_for_extra(monkeypatch):
    # 모드 A: best_ap가 live와 다른 SSID면 cross connect 허용
    monkeypatch.setattr(wifi_roam, "GENERATE_NETWORK_BLOCKS", True)
    assert wifi_roam.should_cross_connect("Office", "LiveNet") is True

def test_cross_return_to_conf_default_is_cross(monkeypatch):
    # ★T5 회귀(2026-07-20): live=extra(Office)에서 conf 기본(BaseNet) 복귀도 cross여야 한다.
    # 구 base_ssids 판정은 WPA_SSID 포함으로 False(same 오라우팅)→wpa_cli roam FAIL 무한루프.
    monkeypatch.setattr(wifi_roam, "GENERATE_NETWORK_BLOCKS", True)
    monkeypatch.setattr(wifi_roam, "WPA_SSID", "BaseNet")
    assert wifi_roam.should_cross_connect("BaseNet", "Office") is True

def test_cross_not_for_live_ssid_mode_a(monkeypatch):
    # 모드 A라도 best_ap가 라이브 SSID와 같으면 cross 아님(same-SSID roam 경로)
    monkeypatch.setattr(wifi_roam, "GENERATE_NETWORK_BLOCKS", True)
    assert wifi_roam.should_cross_connect("LiveNet", "LiveNet") is False

def test_cross_none_ssid_false(monkeypatch):
    # best_ap.ssid 부재면 cross 아님(roam_to_bssid 경로로)
    monkeypatch.setattr(wifi_roam, "GENERATE_NETWORK_BLOCKS", True)
    assert wifi_roam.should_cross_connect(None, "LiveNet") is False

def test_cross_unknown_live_false(monkeypatch):
    # live 미상(None/"")이면 보수적으로 cross 아님
    monkeypatch.setattr(wifi_roam, "GENERATE_NETWORK_BLOCKS", True)
    assert wifi_roam.should_cross_connect("Office", None) is False
    assert wifi_roam.should_cross_connect("Office", "") is False
