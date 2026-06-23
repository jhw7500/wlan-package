import sys
import os
from unittest.mock import MagicMock

# sUTILS has heavy runtime deps (paho, serial, numpy); stub it before importing wifi_roam.
sys.modules.setdefault("sUTILS", MagicMock())

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wifi_roam
from wifi_roam import compute_no_result_backoff

import pytest

# `logger` is only assigned at runtime inside main(); stub so any error path is safe.
wifi_roam.logger = MagicMock()

def _set_sleep(monkeypatch, start, cap):
    monkeypatch.setattr(wifi_roam, "SCAN_NO_RESULT_SLEEP", start)
    monkeypatch.setattr(wifi_roam, "ROAM_NO_RESULT_MAX_SLEEP", cap)

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
