"""LAST_SCAN_TIME(bgscan 타이머 리셋) 기록 게이트 테스트.

bgscan 은 /tmp/last_roam_scan_time 이후 interval 전체를 다시 대기한다. 종전엔 staged
경로가 '스캔 시도(scanned)'만으로 무조건 기록해, 홈채널 패시브(부분 스캔 — bgscan 의
교차채널 캐시 공급을 정보로도 라디오로도 대체 못 함)까지 bgscan 을 밀어냈다 → RSSI 가
임계 주변에서 진동하면 bgscan 이 무한 연기되어 Stage 2 캐시가 고사(passive-first 가
자기 캐시 공급원을 굶기는 역설). 수정: **bgscan 동등 커버리지**일 때만 기록 —
① Stage 3 액티브가 실제 **성공**(실패 시 미기록: bgscan 조기 재개가 오히려 이득)
② scan_freq ⊆ {홈채널}(단일채널 — 홈 스캔이 곧 전체 커버리지, 결과 무관)."""
import os
import sys
from datetime import datetime
from unittest.mock import MagicMock

sys.modules.setdefault("sUTILS", MagicMock())
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wifi_roam

import pytest

wifi_roam.logger = MagicMock()

CUR = "aa:aa:aa:aa:aa:aa"
STABLE = "stable"


def apln(idx, ch, rssi, bssid, ssid, freq=None):
    if freq is None:
        freq = wifi_roam.channel_to_freq(ch)
    return f"{idx:02d}|{ch}|{rssi}|0|{bssid}|{freq}|{ssid}"


def _fake_iw(passive_ret, active_ret, calls):
    def fake(ssids, freqs, passive=False, include_wildcard=True):
        calls.append({"ssids": ssids, "freqs": freqs, "passive": passive})
        return passive_ret if passive else active_ret
    return fake


def _station(rssi=-70, freq=5180, ssid="Net"):
    return {"bssid": CUR, "ssid": ssid, "freq": freq, "rssi": rssi, "load": 0}


@pytest.fixture(autouse=True)
def _globals(tmp_path, monkeypatch):
    monkeypatch.setattr(wifi_roam, "WPA_TH_2G", -75)
    monkeypatch.setattr(wifi_roam, "WPA_TH_5G", -75)
    monkeypatch.setattr(wifi_roam, "WPA_FREQ", ["5180", "5200"])
    monkeypatch.setattr(wifi_roam, "DIFF_TH", 10)
    monkeypatch.setattr(wifi_roam, "ENABLE_LOAD_BASED_ROAM", False)
    monkeypatch.setattr(wifi_roam, "ENABLE_PREDICTIVE_ROAM", False)
    monkeypatch.setattr(wifi_roam, "CACHE_FRESH_SEC", 70)
    monkeypatch.setattr(wifi_roam, "_LAST_SELF_SCAN_TS", None)
    monkeypatch.setattr(wifi_roam, "_LAST_SELF_SCAN_END_TS", None)
    monkeypatch.setattr(wifi_roam, "_LAST_WALL_TS", None)
    monkeypatch.setattr(wifi_roam, "_LAST_MONO_TS", None)
    monkeypatch.setattr(wifi_roam, "ENABLE_STAGED_SCAN", True)
    monkeypatch.setattr(wifi_roam, "SELF_INDUCED_TAIL_SEC", 10)
    monkeypatch.setattr(wifi_roam, "SKIP_REDUNDANT_ACTIVE_SCAN", True)
    monkeypatch.setattr(wifi_roam, "HOME_PASSIVE", True)
    scan_time_file = tmp_path / "last_roam_scan_time"
    monkeypatch.setattr(wifi_roam, "LAST_SCAN_TIME_FILE", str(scan_time_file))
    yield scan_time_file


def _staged(station=None, allowed=("Net",)):
    return wifi_roam.staged_scan_best_candidate(
        station or _station(), None, list(allowed), "Net", STABLE, None
    )


def test_stage1_candidate_multichannel_not_recorded(_globals, monkeypatch):
    """다채널에서 Stage 1(홈 패시브)만으로 끝난 tick — 부분 스캔이라 기록 금지."""
    calls = []
    home = [apln(0, 36, -70, CUR, "Net"), apln(1, 36, -45, "bb:bb:bb:bb:bb:bb", "Net")]
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", _fake_iw(home, None, calls))
    monkeypatch.setattr(wifi_roam, "get_latest_scan", lambda *a, **k: ([], None))
    best, _, _, scanned = _staged()
    assert best is not None and scanned is True
    assert not _globals.exists(), "홈채널 부분 스캔이 bgscan 타이머를 리셋함(기아 회귀)"


def test_stage2_cache_hit_not_recorded(_globals, monkeypatch):
    """Stage 2 캐시 히트로 끝난 tick — 추가 스캔 없음, 기록 금지."""
    calls = []
    home = [apln(0, 36, -70, CUR, "Net")]
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", _fake_iw(home, None, calls))
    fresh_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cache = [{"bssid": "cc:cc:cc:cc:cc:cc", "ssid": "Net", "channel": 40, "freq": 5200,
              "rssi": -50, "rssi_th": -75, "ld": 0, "load": 0, "noise": -95,
              "timestamp": fresh_ts}]
    monkeypatch.setattr(wifi_roam, "get_latest_scan", lambda *a, **k: (cache, fresh_ts))
    best, _, _, _ = _staged()
    assert best is not None and best["bssid"] == "cc:cc:cc:cc:cc:cc"
    assert not _globals.exists()


def test_stage3_success_records(_globals, monkeypatch):
    """Stage 3 액티브가 scan_freq 전 채널을 실측 성공 — bgscan 동등, 기록해야 한다."""
    calls = []
    home = [apln(0, 36, -70, CUR, "Net")]
    active = [apln(0, 40, -47, "dd:dd:dd:dd:dd:dd", "Net")]
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", _fake_iw(home, active, calls))
    monkeypatch.setattr(wifi_roam, "get_latest_scan", lambda *a, **k: ([], None))
    best, _, _, _ = _staged()
    assert best is not None and best["bssid"] == "dd:dd:dd:dd:dd:dd"
    assert _globals.exists(), "Stage 3 성공이 bgscan 타이머 리셋을 기록하지 않음"


def test_stage3_failure_not_recorded(_globals, monkeypatch):
    """Stage 3 액티브 실패(iw None) — 신선 데이터 미생산, bgscan 조기 재개가 이득이라
    기록 금지(종전 '시도만으로 기록'의 회귀 방지)."""
    calls = []
    home = [apln(0, 36, -70, CUR, "Net")]
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", _fake_iw(home, None, calls))
    monkeypatch.setattr(wifi_roam, "get_latest_scan", lambda *a, **k: ([], None))
    best, _, _, scanned = _staged()
    assert best is None and scanned is True
    assert not _globals.exists()


def test_single_channel_skip_records(_globals, monkeypatch):
    """단일채널 + 스킵 가드 발동 — 홈 스캔이 곧 전체 커버리지(bgscan 동등), 기록해야."""
    monkeypatch.setattr(wifi_roam, "WPA_FREQ", ["5240"])
    calls = []
    home = [apln(0, 48, -50, CUR, "Net"), apln(1, 48, -49, "bb:bb:bb:bb:bb:bb", "Net")]
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", _fake_iw(home, None, calls))
    monkeypatch.setattr(wifi_roam, "get_latest_scan", lambda *a, **k: ([], None))
    best, _, _, _ = _staged(_station(freq=5240))
    assert best is None
    assert not any(c["passive"] is False for c in calls)   # 스킵 확인
    assert _globals.exists(), "단일채널 전체 커버리지 스캔이 기록되지 않음"


def test_single_channel_stage1_candidate_records(_globals, monkeypatch):
    """단일채널 + Stage 1 에서 후보 발견 — 결과와 무관하게 전체 커버리지라 기록해야."""
    monkeypatch.setattr(wifi_roam, "WPA_FREQ", ["5240"])
    calls = []
    home = [apln(0, 48, -60, CUR, "Net"), apln(1, 48, -45, "bb:bb:bb:bb:bb:bb", "Net")]
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", _fake_iw(home, None, calls))
    monkeypatch.setattr(wifi_roam, "get_latest_scan", lambda *a, **k: ([], None))
    best, _, _, _ = _staged(_station(freq=5240))
    assert best is not None and best["bssid"] == "bb:bb:bb:bb:bb:bb"
    assert _globals.exists()
