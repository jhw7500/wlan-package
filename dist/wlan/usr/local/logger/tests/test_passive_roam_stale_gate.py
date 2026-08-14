"""passive_roam stale 스캔 블록 거부 게이트 테스트.

scan 로거가 죽으면 ap.log 마지막 블록이 무기한 재사용되던 문제(수동 wifi roam /
periodic_roam 이 옛 후보로 로밍 시도)를 SCAN_BLOCK_MAX_AGE_SEC 로 차단한다 —
블록 timestamp 가 상한보다 오래되면 파싱을 거부하고 빈 목록을 반환한다.
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import passive_roam

AP_LINE = "01|36|-50|0|00:11:22:33:44:55|cap|test-ssid\n"


def _write_log(path, age_sec, bracketed=False):
    ts = (datetime.now() - timedelta(seconds=age_sec)).strftime("%Y-%m-%d %H:%M:%S")
    header = f"[{ts}]" if bracketed else ts
    path.write_text(f"{header}\n{AP_LINE}")


def test_fresh_block_accepted(tmp_path):
    log = tmp_path / "ap.log"
    _write_log(log, 0)
    aps = passive_roam.parse_last_scan_block(str(log))
    assert len(aps) == 1
    assert aps[0]["bssid"] == "00:11:22:33:44:55"


def test_block_within_max_age_accepted(tmp_path):
    log = tmp_path / "ap.log"
    _write_log(log, passive_roam.SCAN_BLOCK_MAX_AGE_SEC - 60)
    assert len(passive_roam.parse_last_scan_block(str(log))) == 1


def test_stale_block_rejected_with_message(tmp_path, capsys):
    log = tmp_path / "ap.log"
    _write_log(log, passive_roam.SCAN_BLOCK_MAX_AGE_SEC + 60)
    assert passive_roam.parse_last_scan_block(str(log)) == []
    out = capsys.readouterr().out
    assert "stale" in out
    assert "run a scan first" in out


def test_stale_legacy_bracketed_block_rejected(tmp_path):
    log = tmp_path / "ap.log"
    _write_log(log, passive_roam.SCAN_BLOCK_MAX_AGE_SEC + 60, bracketed=True)
    assert passive_roam.parse_last_scan_block(str(log)) == []


def test_unparseable_timestamp_fails_open(tmp_path):
    """정규식은 통과하나 strptime 불가(월 13) — age 판정 불가 시 종전 동작(수용) 유지."""
    log = tmp_path / "ap.log"
    log.write_text(f"2026-13-01 12:00:00\n{AP_LINE}")
    assert len(passive_roam.parse_last_scan_block(str(log))) == 1


def test_max_age_override_rejects(tmp_path):
    log = tmp_path / "ap.log"
    _write_log(log, 30)
    assert passive_roam.parse_last_scan_block(str(log), max_age_sec=10) == []


def test_max_age_scales_with_bgscan_interval(tmp_path):
    conf = tmp_path / "wifi_init_conf.json"
    conf.write_text('{"mlan0":{"bgscan":{"interval":300}}}')
    assert passive_roam.scan_block_max_age_sec("mlan0", str(conf)) == 750


def test_valid_long_interval_cache_is_not_rejected(tmp_path, monkeypatch):
    conf = tmp_path / "wifi_init_conf.json"
    conf.write_text('{"mlan0":{"bgscan":{"interval":300}}}')
    log = tmp_path / "ap.log"
    _write_log(log, 400)
    monkeypatch.setattr(passive_roam, "WIFI_INIT_CONF_JSON", str(conf))
    monkeypatch.setattr(passive_roam, "WIFI_IFACE", "mlan0")
    assert len(passive_roam.parse_last_scan_block(str(log))) == 1
