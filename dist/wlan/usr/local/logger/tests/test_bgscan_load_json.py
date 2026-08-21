import sys
import os
import json
from unittest.mock import MagicMock

# sUTILS has heavy runtime deps (paho, serial, numpy); stub it before importing wifi_bgscan.
sys.modules.setdefault("sUTILS", MagicMock())

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wifi_bgscan
from wifi_bgscan import load_bgscan_json

import pytest

# `logger` is only assigned at runtime inside main_loop(), so it is undefined at import
# time. Stub it so load_bgscan_json's error path (malformed JSON) can call
# logger.message() without raising NameError under test.
wifi_bgscan.logger = MagicMock()


def _write_conf(tmp_path, monkeypatch, conf):
    """Write conf as the bgscan JSON and point load_bgscan_json at it (auto-restored)."""
    path = tmp_path / "wifi_init_conf.json"
    path.write_text(json.dumps(conf))
    monkeypatch.setattr(wifi_bgscan, "WIFI_INIT_CONF_JSON", str(path))
    return path


def _write_policy(tmp_path, *, roaming_enabled, generate=False, extras=None, bgscan=True):
    path = tmp_path / "mlan0.roam-policy.json"
    path.write_text(json.dumps({
        "version": 1,
        "iface": "mlan0",
        "roaming_enabled": roaming_enabled,
        "bgscan_enabled": bgscan,
        "generate_network_blocks": generate,
        "extra_ssids": extras or [],
    }))
    return path


# --- extra_ssids extraction / validation (the untested runtime path) ---

def test_extra_ssids_valid_list_stripped(tmp_path, monkeypatch):
    _write_conf(tmp_path, monkeypatch, {
        "mlan0": {"roaming": {"generate_network_blocks": True, "extra_ssids": ["  OfficeNet  ", "Guest"]}}
    })
    assert load_bgscan_json("mlan0")[3] == ["OfficeNet", "Guest"]


def test_extra_ssids_non_string_entries_filtered(tmp_path, monkeypatch):
    # int / None / dict / list entries are dropped by the isinstance(s, str) gate
    _write_conf(tmp_path, monkeypatch, {
        "mlan0": {"roaming": {"generate_network_blocks": True, "extra_ssids": ["Good", 123, None, {"x": 1}, ["nested"], "Net2"]}}
    })
    assert load_bgscan_json("mlan0")[3] == ["Good", "Net2"]


def test_extra_ssids_whitespace_only_dropped(tmp_path, monkeypatch):
    _write_conf(tmp_path, monkeypatch, {
        "mlan0": {"roaming": {"generate_network_blocks": True, "extra_ssids": ["", "   ", "\t", "Real"]}}
    })
    assert load_bgscan_json("mlan0")[3] == ["Real"]


def test_extra_ssids_missing_key_returns_empty(tmp_path, monkeypatch):
    _write_conf(tmp_path, monkeypatch, {"mlan0": {"roaming": {}}})
    assert load_bgscan_json("mlan0")[3] == []


def test_extra_ssids_missing_roaming_returns_empty(tmp_path, monkeypatch):
    _write_conf(tmp_path, monkeypatch, {"mlan0": {"bgscan": {"interval": 60}}})
    assert load_bgscan_json("mlan0")[3] == []


def test_extra_ssids_not_a_list_returns_empty(tmp_path, monkeypatch):
    # a bare string (common misconfig) is not a list → []
    _write_conf(tmp_path, monkeypatch, {
        "mlan0": {"roaming": {"extra_ssids": "OfficeNet"}}
    })
    assert load_bgscan_json("mlan0")[3] == []


def test_extra_ssids_scoped_per_iface(tmp_path, monkeypatch):
    # extra_ssids is read from the requested iface only
    _write_conf(tmp_path, monkeypatch, {
        "mlan0": {"roaming": {"generate_network_blocks": True, "extra_ssids": ["A"]}},
        "mlan1": {"roaming": {"generate_network_blocks": True, "extra_ssids": ["B"]}},
    })
    assert load_bgscan_json("mlan0")[3] == ["A"]
    assert load_bgscan_json("mlan1")[3] == ["B"]


# --- filters / interval parsing alongside extra_ssids (5-tuple contract) ---

def test_filters_and_interval_parsed(tmp_path, monkeypatch):
    _write_conf(tmp_path, monkeypatch, {
        "mlan0": {"bgscan": {"interval": 45, "ssid_filter": False, "freq_filter": False}}
    })
    assert load_bgscan_json("mlan0") == (45, False, False, [], True, True)


def test_invalid_types_fall_back_to_defaults(tmp_path, monkeypatch):
    # interval <= 0, filters non-bool → defaults (None interval, True/True filters)
    _write_conf(tmp_path, monkeypatch, {
        "mlan0": {"bgscan": {"interval": 0, "ssid_filter": "yes", "freq_filter": 1}}
    })
    interval, ssid_filter, freq_filter, _, _, _ = load_bgscan_json("mlan0")
    assert interval is None and ssid_filter is True and freq_filter is True


def test_missing_file_returns_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(wifi_bgscan, "WIFI_INIT_CONF_JSON", str(tmp_path / "nope.json"))
    assert load_bgscan_json("mlan0") == (None, True, True, [], True, True)


def test_malformed_json_returns_defaults(tmp_path, monkeypatch):
    path = tmp_path / "wifi_init_conf.json"
    path.write_text("{ not valid json ]")
    monkeypatch.setattr(wifi_bgscan, "WIFI_INIT_CONF_JSON", str(path))
    # hits the generic except path → logger.message (stubbed) → defaults
    assert load_bgscan_json("mlan0") == (None, True, True, [], True, True)


# --- emit_roam_hint gate (5-tuple element 4) ---

def test_emit_roam_hint_default_true(tmp_path, monkeypatch):
    _write_conf(tmp_path, monkeypatch, {"mlan0": {"bgscan": {"interval": 60}}})
    assert load_bgscan_json("mlan0")[4] is True

def test_emit_roam_hint_explicit_false(tmp_path, monkeypatch):
    _write_conf(tmp_path, monkeypatch, {
        "mlan0": {"bgscan": {"interval": 60, "emit_roam_hint": False}}
    })
    assert load_bgscan_json("mlan0")[4] is False

def test_emit_roam_hint_non_bool_falls_back_true(tmp_path, monkeypatch):
    # non-bool (e.g. "no") is ignored → default True
    _write_conf(tmp_path, monkeypatch, {
        "mlan0": {"bgscan": {"interval": 60, "emit_roam_hint": "no"}}
    })
    assert load_bgscan_json("mlan0")[4] is True

# --- generate_network_blocks 게이트 (모드 B는 extra probe 제외) ---

def test_extra_ssids_gated_off_in_mode_b(tmp_path, monkeypatch):
    # generate_network_blocks=false(기본): extra_ssids가 있어도 [] 강제
    _write_conf(tmp_path, monkeypatch, {
        "mlan0": {"roaming": {"generate_network_blocks": False, "extra_ssids": ["Office", "Guest"]}}
    })
    assert load_bgscan_json("mlan0")[3] == []

def test_extra_ssids_passed_in_mode_a(tmp_path, monkeypatch):
    # generate_network_blocks=true(모드 A): 기존대로 extra 파싱
    _write_conf(tmp_path, monkeypatch, {
        "mlan0": {"roaming": {"generate_network_blocks": True, "extra_ssids": ["  Office  ", "Guest"]}}
    })
    assert load_bgscan_json("mlan0")[3] == ["Office", "Guest"]

def test_extra_ssids_gated_off_when_generate_absent(tmp_path, monkeypatch):
    # generate 키 부재(기본 모드 B): extra가 있어도 []
    _write_conf(tmp_path, monkeypatch, {
        "mlan0": {"roaming": {"extra_ssids": ["Office"]}}
    })
    assert load_bgscan_json("mlan0")[3] == []

def test_extra_ssids_included_when_generate_truthy_str(tmp_path, monkeypatch):
    # 비정규 truthy("true"/1)는 roam parse_bool / lib normalize_bool과 통일되어 모드 A → extra 포함.
    # (3-way bool 정합: roam/lib/bgscan이 동일 해석 — final review Important-1 해소)
    _write_conf(tmp_path, monkeypatch, {
        "mlan0": {"roaming": {"generate_network_blocks": "true", "extra_ssids": ["Office"]}}
    })
    assert load_bgscan_json("mlan0")[3] == ["Office"]


def test_extra_ssids_gated_off_when_generate_false(tmp_path, monkeypatch):
    # 정규 false(모드 B)는 extra 무시 → []
    _write_conf(tmp_path, monkeypatch, {
        "mlan0": {"roaming": {"generate_network_blocks": False, "extra_ssids": ["Office"]}}
    })
    assert load_bgscan_json("mlan0")[3] == []


def test_boot_topology_policy_does_not_hot_reload_from_json(tmp_path, monkeypatch):
    path = _write_conf(tmp_path, monkeypatch, {
        "mlan0": {
            "bgscan": {"interval": 60},
            "roaming": {
                "generate_network_blocks": False,
                "extra_ssids": ["LiveJsonMustNotWin"],
            },
        }
    })
    boot_policy = {
        "version": 1,
        "iface": "mlan0",
        "roaming_enabled": True,
        "bgscan_enabled": True,
        "generate_network_blocks": True,
        "extra_ssids": ["BootOffice"],
    }
    assert load_bgscan_json("mlan0", boot_policy=boot_policy)[3] == ["BootOffice"]

    data = json.loads(path.read_text())
    data["mlan0"]["roaming"] = {
        "generate_network_blocks": True,
        "extra_ssids": ["ChangedAtRuntime"],
    }
    path.write_text(json.dumps(data))
    assert load_bgscan_json("mlan0", boot_policy=boot_policy)[3] == ["BootOffice"]


def test_emit_roam_hint_touch_creates_and_advances(tmp_path, monkeypatch):
    monkeypatch.setattr(wifi_bgscan, "ROAM_HINT_DIR", str(tmp_path))
    wifi_bgscan.emit_roam_hint_touch("mlan0")
    p = tmp_path / "wifi_roam_hint_mlan0"
    assert p.exists()
    first = os.path.getmtime(str(p))
    os.utime(str(p), (first - 5, first - 5))
    wifi_bgscan.emit_roam_hint_touch("mlan0")
    assert os.path.getmtime(str(p)) > first - 5


def test_passive_explicit_false(tmp_path, monkeypatch):
    """`bgscan.passive: false` 명시 시 파싱 계층이 False를 반환해야 한다
    (기본값 True 경로만 검증돼 있어 명시 경로가 비어 있었다)."""
    _write_conf(tmp_path, monkeypatch, {
        "mlan0": {"bgscan": {"interval": 60, "passive": False}}
    })
    assert load_bgscan_json("mlan0")[5] is False


def test_passive_non_bool_falls_back_true(tmp_path, monkeypatch):
    """non-bool(예: 문자열)은 무시하고 기본 True 유지."""
    _write_conf(tmp_path, monkeypatch, {
        "mlan0": {"bgscan": {"interval": 60, "passive": "no"}}
    })
    assert load_bgscan_json("mlan0")[5] is True


# --- proactive roam owner -> boot-latched scan backend ---

def test_scan_backend_is_iw_when_wifi_roam_is_enabled(tmp_path, monkeypatch):
    _write_policy(tmp_path, roaming_enabled=True)
    assert wifi_bgscan.load_scan_backend("mlan0", run_dir=str(tmp_path)) == "iw"


def test_scan_backend_is_wpa_cli_when_wifi_roam_is_disabled(tmp_path, monkeypatch):
    _write_policy(tmp_path, roaming_enabled=False)
    assert wifi_bgscan.load_scan_backend("mlan0", run_dir=str(tmp_path)) == "wpa_cli"


@pytest.mark.parametrize("owner", [None, "false", 0])
def test_scan_backend_rejects_missing_or_non_boolean_owner(tmp_path, monkeypatch, owner):
    policy = {
        "version": 1,
        "iface": "mlan0",
        "bgscan_enabled": True,
        "generate_network_blocks": False,
        "extra_ssids": [],
    }
    if owner is not None:
        policy["roaming_enabled"] = owner
    (tmp_path / "mlan0.roam-policy.json").write_text(json.dumps(policy))
    with pytest.raises(wifi_bgscan.BgscanConfigError):
        wifi_bgscan.load_scan_backend("mlan0", run_dir=str(tmp_path))


def test_scan_backend_rejects_unreadable_json_instead_of_guessing(tmp_path, monkeypatch):
    with pytest.raises(wifi_bgscan.BgscanConfigError):
        wifi_bgscan.load_scan_backend("mlan0", run_dir=str(tmp_path))
