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
        "mlan0": {"roaming": {"extra_ssids": ["  OfficeNet  ", "Guest"]}}
    })
    assert load_bgscan_json("mlan0")[3] == ["OfficeNet", "Guest"]


def test_extra_ssids_non_string_entries_filtered(tmp_path, monkeypatch):
    # int / None / dict / list entries are dropped by the isinstance(s, str) gate
    _write_conf(tmp_path, monkeypatch, {
        "mlan0": {"roaming": {"extra_ssids": ["Good", 123, None, {"x": 1}, ["nested"], "Net2"]}}
    })
    assert load_bgscan_json("mlan0")[3] == ["Good", "Net2"]


def test_extra_ssids_whitespace_only_dropped(tmp_path, monkeypatch):
    _write_conf(tmp_path, monkeypatch, {
        "mlan0": {"roaming": {"extra_ssids": ["", "   ", "\t", "Real"]}}
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
        "mlan0": {"roaming": {"extra_ssids": ["A"]}},
        "mlan1": {"roaming": {"extra_ssids": ["B"]}},
    })
    assert load_bgscan_json("mlan0")[3] == ["A"]
    assert load_bgscan_json("mlan1")[3] == ["B"]


# --- filters / interval parsing alongside extra_ssids (4-tuple contract) ---

def test_filters_and_interval_parsed(tmp_path, monkeypatch):
    _write_conf(tmp_path, monkeypatch, {
        "mlan0": {"bgscan": {"interval": 45, "ssid_filter": False, "freq_filter": False}}
    })
    assert load_bgscan_json("mlan0") == (45, False, False, [])


def test_invalid_types_fall_back_to_defaults(tmp_path, monkeypatch):
    # interval <= 0, filters non-bool → defaults (None interval, True/True filters)
    _write_conf(tmp_path, monkeypatch, {
        "mlan0": {"bgscan": {"interval": 0, "ssid_filter": "yes", "freq_filter": 1}}
    })
    interval, ssid_filter, freq_filter, _ = load_bgscan_json("mlan0")
    assert interval is None and ssid_filter is True and freq_filter is True


def test_missing_file_returns_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(wifi_bgscan, "WIFI_INIT_CONF_JSON", str(tmp_path / "nope.json"))
    assert load_bgscan_json("mlan0") == (None, True, True, [])


def test_malformed_json_returns_defaults(tmp_path, monkeypatch):
    path = tmp_path / "wifi_init_conf.json"
    path.write_text("{ not valid json ]")
    monkeypatch.setattr(wifi_bgscan, "WIFI_INIT_CONF_JSON", str(path))
    # hits the generic except path → logger.message (stubbed) → defaults
    assert load_bgscan_json("mlan0") == (None, True, True, [])
