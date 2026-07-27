"""`logger.bgscan_stale_threshold_sec` 실효화 테스트.

종전엔 wifi_bgscan 이 이 키를 로드만 하고 이후 미사용(dead knob)이었고, 실소비처인
wifi_logger_scan(beacon.json stale 엔트리 프루닝)은 600 을 하드코딩해 설정이 무효였다.
수정 후: wifi_logger_scan 이 키를 직접 로드(양의 int 만, 부재/불량은 기본 600 유지 —
fail-same)하고, wifi_bgscan 의 죽은 로드는 제거된다."""
import importlib.util
import json
import os
import sys
from datetime import datetime
from unittest.mock import MagicMock

sys.modules.setdefault("sUTILS", MagicMock())
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wifi_bgscan
import wifi_logger_scan

import pytest

wifi_logger_scan.logger = MagicMock()

_TMPL = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "..", "..", "opt", "wlan", "config", "wifi_init_conf.json",
)


def test_loader_reads_config_value(tmp_path):
    p = tmp_path / "wic.json"
    p.write_text(json.dumps({"logger": {"bgscan_stale_threshold_sec": 300}}))
    assert wifi_logger_scan.load_stale_threshold(str(p)) == 300


def test_loader_defaults_on_missing_file():
    assert wifi_logger_scan.load_stale_threshold("/nonexistent/wic.json") == \
        wifi_logger_scan.DEFAULT_STALE_THRESHOLD_SEC


@pytest.mark.parametrize("bad", ["abc", 0, -5, True, None, 1.5])
def test_loader_defaults_on_invalid_value(tmp_path, bad):
    """양의 int 만 수용 — 불량 값(문자열/0/음수/bool/None/float)은 기본값 유지."""
    p = tmp_path / "wic.json"
    p.write_text(json.dumps({"logger": {"bgscan_stale_threshold_sec": bad}}))
    assert wifi_logger_scan.load_stale_threshold(str(p)) == \
        wifi_logger_scan.DEFAULT_STALE_THRESHOLD_SEC


def test_code_default_matches_template():
    """fail-same: 코드 기본 600 == 템플릿 logger.bgscan_stale_threshold_sec."""
    with open(_TMPL) as f:
        tmpl_val = json.load(f)["logger"]["bgscan_stale_threshold_sec"]
    assert wifi_logger_scan.DEFAULT_STALE_THRESHOLD_SEC == tmpl_val


def test_threshold_drives_beacon_pruning(monkeypatch):
    """[소비처 계약] STALE_THRESHOLD_SEC 가 remove_stale_entries 프루닝을 실제로 결정 —
    임계 100s 에서 50s 전 엔트리는 유지, 150s 전 엔트리는 제거."""
    monkeypatch.setattr(wifi_logger_scan, "STALE_THRESHOLD_SEC", 100)
    now = 1_700_000_000
    fmt = lambda ts: datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    db = {
        "aa:aa:aa:aa:aa:aa": {"date": fmt(now - 50)},
        "bb:bb:bb:bb:bb:bb": {"date": fmt(now - 150)},
    }
    out = wifi_logger_scan.remove_stale_entries(db, now)
    assert "aa:aa:aa:aa:aa:aa" in out
    assert "bb:bb:bb:bb:bb:bb" not in out


def test_loader_captures_warning_on_invalid_value(tmp_path, monkeypatch):
    """[가시성] 불량 값 폴백은 조용히 삼키지 않고 경고를 캡처한다(로깅은 logger 초기화
    후 __main__ 에서 1회 발행 — import 시점엔 logger 부재)."""
    monkeypatch.setattr(wifi_logger_scan, "_STALE_THRESHOLD_LOAD_WARNING", None, raising=False)
    p = tmp_path / "wic.json"
    p.write_text(json.dumps({"logger": {"bgscan_stale_threshold_sec": "abc"}}))
    assert wifi_logger_scan.load_stale_threshold(str(p)) == \
        wifi_logger_scan.DEFAULT_STALE_THRESHOLD_SEC
    assert wifi_logger_scan._STALE_THRESHOLD_LOAD_WARNING is not None


def test_loader_captures_warning_on_broken_json(tmp_path, monkeypatch):
    monkeypatch.setattr(wifi_logger_scan, "_STALE_THRESHOLD_LOAD_WARNING", None, raising=False)
    p = tmp_path / "wic.json"
    p.write_text("{broken json")
    assert wifi_logger_scan.load_stale_threshold(str(p)) == \
        wifi_logger_scan.DEFAULT_STALE_THRESHOLD_SEC
    assert wifi_logger_scan._STALE_THRESHOLD_LOAD_WARNING is not None


def test_loader_silent_on_missing_file_and_valid_value(tmp_path, monkeypatch):
    """파일 부재(신규/개발 환경 정상 폴백)와 정상 값은 경고 없음 — 경고 과발행 방지."""
    monkeypatch.setattr(wifi_logger_scan, "_STALE_THRESHOLD_LOAD_WARNING", None, raising=False)
    wifi_logger_scan.load_stale_threshold("/nonexistent/wic.json")
    assert wifi_logger_scan._STALE_THRESHOLD_LOAD_WARNING is None
    p = tmp_path / "wic.json"
    p.write_text(json.dumps({"logger": {"bgscan_stale_threshold_sec": 300}}))
    assert wifi_logger_scan.load_stale_threshold(str(p)) == 300
    assert wifi_logger_scan._STALE_THRESHOLD_LOAD_WARNING is None


def test_stale_threshold_wired_to_loader(tmp_path):
    """[와이어링] 모듈 전역 STALE_THRESHOLD_SEC 가 import 시 loader 를 **실제로 경유**해
    config 값을 받는지 검증 — 'loader 는 있는데 전역은 하드코딩' 회귀(= 원래 dead knob
    버그의 재발 형태)를 킬한다. 기본 경로 상수만 tmp config 로 치환한 사본을 신선 로드."""
    cfg = tmp_path / "wic.json"
    cfg.write_text(json.dumps({"logger": {"bgscan_stale_threshold_sec": 123}}))
    src_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "wifi_logger_scan.py")
    with open(src_path) as f:
        src = f.read()
    patched = src.replace(
        'WIFI_INIT_CONF_JSON = "/usr/local/etc/wifi_init_conf.json"',
        f'WIFI_INIT_CONF_JSON = "{cfg}"',
        1,
    )
    assert patched != src, "WIFI_INIT_CONF_JSON 상수 라인을 찾지 못함(경로 상수 변경 시 테스트 갱신)"
    mod_path = tmp_path / "wifi_logger_scan_wiring.py"
    mod_path.write_text(patched)
    spec = importlib.util.spec_from_file_location("wifi_logger_scan_wiring", str(mod_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.STALE_THRESHOLD_SEC == 123, \
        f"import 시 config 미반영(하드코딩 회귀): {mod.STALE_THRESHOLD_SEC}"


def test_bgscan_dead_load_removed():
    """wifi_bgscan 의 죽은 로드(로드만 하고 미사용) 제거 확인 — 소비처 단일화."""
    assert not hasattr(wifi_bgscan, "STALE_THRESHOLD_SEC"), \
        "wifi_bgscan 에 dead knob 로드가 잔존"
