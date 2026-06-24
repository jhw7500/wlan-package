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
    assert load_bgscan_json("mlan0") == (45, False, False, [], True)


def test_invalid_types_fall_back_to_defaults(tmp_path, monkeypatch):
    # interval <= 0, filters non-bool → defaults (None interval, True/True filters)
    _write_conf(tmp_path, monkeypatch, {
        "mlan0": {"bgscan": {"interval": 0, "ssid_filter": "yes", "freq_filter": 1}}
    })
    interval, ssid_filter, freq_filter, _, _ = load_bgscan_json("mlan0")
    assert interval is None and ssid_filter is True and freq_filter is True


def test_missing_file_returns_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(wifi_bgscan, "WIFI_INIT_CONF_JSON", str(tmp_path / "nope.json"))
    assert load_bgscan_json("mlan0") == (None, True, True, [], True)


def test_malformed_json_returns_defaults(tmp_path, monkeypatch):
    path = tmp_path / "wifi_init_conf.json"
    path.write_text("{ not valid json ]")
    monkeypatch.setattr(wifi_bgscan, "WIFI_INIT_CONF_JSON", str(path))
    # hits the generic except path → logger.message (stubbed) → defaults
    assert load_bgscan_json("mlan0") == (None, True, True, [], True)


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

def test_extra_ssids_gated_off_when_generate_non_bool(tmp_path, monkeypatch):
    # generate가 bool이 아니면(misconfig) 모드 B로 안전 수렴 → []
    _write_conf(tmp_path, monkeypatch, {
        "mlan0": {"roaming": {"generate_network_blocks": "true", "extra_ssids": ["Office"]}}
    })
    assert load_bgscan_json("mlan0")[3] == []


def test_emit_roam_hint_touch_creates_and_advances(tmp_path, monkeypatch):
    monkeypatch.setattr(wifi_bgscan, "ROAM_HINT_DIR", str(tmp_path))
    wifi_bgscan.emit_roam_hint_touch("mlan0")
    p = tmp_path / "wifi_roam_hint_mlan0"
    assert p.exists()
    first = os.path.getmtime(str(p))
    os.utime(str(p), (first - 5, first - 5))
    wifi_bgscan.emit_roam_hint_touch("mlan0")
    assert os.path.getmtime(str(p)) > first - 5
