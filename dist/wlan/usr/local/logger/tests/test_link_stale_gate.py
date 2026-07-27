"""link.json stale 게이트 테스트.

link.json 은 wifi_logger_link 가 ~1s 주기로 갱신하는 로밍 판정의 유일한 입력이다.
종전 get_link_info_with_load 는 mtime 불변이면 캐시를 무기한 반환해, 생산자가 hang
등으로 멈추면 **마지막 값으로 계속 판정**했다(#118 감독화는 사망만 복구, hang 은 잔존).
수정: mtime 나이 > LINK_STALE_SEC 면 캐시 히트 여부와 무관하게 None(부재와 동일 시맨틱,
메인루프는 판정 보류·재시도) + 에피소드당 1회 warn, 갱신 재개 시 자가 리셋."""
import json
import os
import sys
import time
from unittest.mock import MagicMock

sys.modules.setdefault("sUTILS", MagicMock())
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wifi_roam

import pytest

VALID = {
    "link": {"address": "AA:BB:CC:DD:EE:FF", "signal": "-48 dBm", "signal_avg": "-50 dBm"},
    "info": {"freq": "5240", "ssid": "Net"},
}


@pytest.fixture(autouse=True)
def _globals(tmp_path, monkeypatch):
    wifi_roam.logger = MagicMock()
    p = tmp_path / "link.json"
    p.write_text(json.dumps(VALID))
    monkeypatch.setattr(wifi_roam, "LINK_LOG_FILE", str(p))
    monkeypatch.setattr(wifi_roam, "_LINK_CACHE", {"mtime_ns": None, "value": None})
    monkeypatch.setattr(wifi_roam, "_LINK_STALE_WARNED", False)
    monkeypatch.setattr(wifi_roam, "USE_SIGNAL_AVG", False)
    monkeypatch.setattr(wifi_roam, "ENABLE_LOAD_BASED_ROAM", False)
    yield p


def _warn_calls():
    return [c for c in wifi_roam.logger.message.call_args_list if c.args[0] == "warn"]


def test_fresh_file_returns_station(_globals):
    st = wifi_roam.get_link_info_with_load()
    assert st is not None
    assert st["bssid"] == "aa:bb:cc:dd:ee:ff"
    assert st["rssi"] == -48
    assert st["freq"] == 5240


def test_stale_returns_none_and_warns_once(_globals):
    old = time.time() - 120
    os.utime(_globals, (old, old))
    assert wifi_roam.get_link_info_with_load() is None, "stale 파일이 판정에 사용됨"
    assert len(_warn_calls()) == 1
    # 같은 에피소드 반복 호출 — 경고 중복 발행 금지
    assert wifi_roam.get_link_info_with_load() is None
    assert len(_warn_calls()) == 1


def test_stale_bypasses_mtime_cache(_globals, monkeypatch):
    """[핵심 순서] 캐시가 프라임된 뒤 갱신이 멈추면(mtime 불변) 캐시 히트보다 나이
    게이트가 먼저 걸려 None — 종전 '마지막 값 무기한 판정'의 정확한 재현 케이스."""
    assert wifi_roam.get_link_info_with_load() is not None   # 캐시 프라임(fresh)
    real_time = time.time()
    monkeypatch.setattr(wifi_roam.time, "time", lambda: real_time + 120)  # 시간만 경과
    assert wifi_roam.get_link_info_with_load() is None, \
        "mtime 캐시 히트가 stale 게이트를 우회함"


def test_recovery_resets_warning(_globals):
    old = time.time() - 120
    os.utime(_globals, (old, old))
    assert wifi_roam.get_link_info_with_load() is None
    assert len(_warn_calls()) == 1
    # 생산자 재개(파일 재작성 = fresh mtime) → 정상 복귀 + 경고 플래그 리셋
    _globals.write_text(json.dumps(VALID))
    assert wifi_roam.get_link_info_with_load() is not None
    os.utime(_globals, (old, old))                            # 새 stale 에피소드
    assert wifi_roam.get_link_info_with_load() is None
    assert len(_warn_calls()) == 2
