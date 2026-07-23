"""단계형 로밍 스캔(홈채널 패시브 → 교차채널 캐시 → 액티브 폴백) + baseline 통일 테스트.

설계:
  - 로밍 트리거(RSSI < 임계값) 시 먼저 홈채널 패시브 스캔으로 같은 채널 후보를 저부하로
    찾고, 현재 AP RSSI를 그 스캔(=후보와 동일 소스)에서 뽑아 baseline으로 통일한다.
  - 홈에서 후보를 못 찾으면 bgscan 배경 캐시(ap.log 마지막 블록)를 CACHE_FRESH_SEC 이내면
    재사용하고, 그것도 없으면 scan_freq+설정 SSID로 좁힌 액티브 폴백을 돈다(wildcard 제거).
"""
import sys
import os
from unittest.mock import MagicMock

sys.modules.setdefault("sUTILS", MagicMock())
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wifi_roam
from datetime import datetime, timedelta

import pytest

wifi_roam.logger = MagicMock()


def apln(idx, ch, rssi, bssid, ssid):
    """pipe 포맷 스캔 라인 `NN|ch|rssi|ld|bssid|freq|ssid` (freq 필드는 파서가 무시)."""
    return f"{idx:02d}|{ch}|{rssi}|0|{bssid}|0|{ssid}"


@pytest.fixture(autouse=True)
def _globals(monkeypatch):
    monkeypatch.setattr(wifi_roam, "WPA_TH_2G", -75)
    monkeypatch.setattr(wifi_roam, "WPA_TH_5G", -75)
    monkeypatch.setattr(wifi_roam, "WPA_FREQ", ["5180", "5200"])  # ch36, ch40
    monkeypatch.setattr(wifi_roam, "DIFF_TH", 10)
    monkeypatch.setattr(wifi_roam, "ENABLE_LOAD_BASED_ROAM", False)
    monkeypatch.setattr(wifi_roam, "ENABLE_PREDICTIVE_ROAM", False)
    monkeypatch.setattr(wifi_roam, "CACHE_FRESH_SEC", 45)


CUR = "aa:aa:aa:aa:aa:aa"
STABLE = "stable"  # trend sentinel (ENABLE_PREDICTIVE_ROAM=False라 값 무관)


def _station(rssi=-70, freq=5180, ssid="Net"):
    return {"bssid": CUR, "ssid": ssid, "freq": freq, "rssi": rssi, "load": 0}


def _fake_iw(passive_ret, active_ret, calls):
    def fake(ssids, freqs, passive=False, include_wildcard=True):
        calls.append(
            {"ssids": ssids, "freqs": freqs, "passive": passive, "wildcard": include_wildcard}
        )
        return passive_ret if passive else active_ret
    return fake


# ---------- Stage 1: 홈채널 패시브 스캔 ----------

def test_stage1_home_candidate_shortcircuits(monkeypatch):
    """홈채널 패시브에서 후보를 찾으면 캐시/액티브를 건드리지 않고 바로 반환."""
    calls = []
    home = [apln(0, 36, -68, CUR, "Net"), apln(1, 36, -45, "bb:bb:bb:bb:bb:bb", "Net")]
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", _fake_iw(home, None, calls))
    cache_called = {"n": 0}
    monkeypatch.setattr(
        wifi_roam, "get_latest_scan",
        lambda *a, **k: (cache_called.__setitem__("n", cache_called["n"] + 1), ([], None))[1],
    )

    best, reason, score, scanned = wifi_roam.staged_scan_best_candidate(
        _station(rssi=-70), None, ["Net"], "Net", STABLE, None
    )
    assert best is not None and best["bssid"] == "bb:bb:bb:bb:bb:bb"
    assert scanned is True
    assert cache_called["n"] == 0                 # 캐시 조회 안 함
    assert len(calls) == 1 and calls[0]["passive"] is True  # 액티브 폴백 안 함


def test_stage1_baseline_unification_blocks_spurious_roam(monkeypatch):
    """현재 AP가 스캔 스케일에서 강하면(baseline 통일) 작은 diff는 로밍 안 함 —
    station dump(-70) 기준이면 diff=22로 오로밍했을 상황을 방지."""
    calls = []
    # station dump=-70이지만 홈 스캔에서 현재 AP는 -55(강함), 후보는 -48 → diff=7 <10.
    home = [apln(0, 36, -55, CUR, "Net"), apln(1, 36, -48, "bb:bb:bb:bb:bb:bb", "Net")]
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", _fake_iw(home, None, calls))
    monkeypatch.setattr(wifi_roam, "get_latest_scan", lambda *a, **k: ([], None))

    best, _, _, _ = wifi_roam.staged_scan_best_candidate(
        _station(rssi=-70), None, ["Net"], "Net", STABLE, None
    )
    assert best is None    # baseline=-55 통일 → 7dB diff는 임계값 미만


# ---------- Stage 2: 교차채널 캐시 ----------

def test_stage2_fresh_cache_used(monkeypatch):
    """홈에서 후보 없음 + 캐시 신선(≤45s) → 캐시 후보 채택, 액티브 폴백 미실행."""
    calls = []
    home = [apln(0, 36, -70, CUR, "Net")]  # 현재 AP만(후보 없음)
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", _fake_iw(home, None, calls))
    fresh_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cache = [{"bssid": "cc:cc:cc:cc:cc:cc", "ssid": "Net", "channel": 40, "freq": 5200,
              "rssi": -50, "rssi_th": -75, "ld": 0, "load": 0, "noise": -95, "timestamp": fresh_ts}]
    monkeypatch.setattr(wifi_roam, "get_latest_scan", lambda *a, **k: (cache, fresh_ts))

    best, _, _, _ = wifi_roam.staged_scan_best_candidate(
        _station(rssi=-70), None, ["Net"], "Net", STABLE, None
    )
    assert best is not None and best["bssid"] == "cc:cc:cc:cc:cc:cc"
    # 액티브 폴백(passive=False) 호출 없음
    assert not any(c["passive"] is False for c in calls)


def test_stage3_active_fallback_when_cache_stale(monkeypatch):
    """홈 후보 없음 + 캐시 stale(>45s) → 액티브 폴백 실행. 폴백은 scan_freq+설정 SSID,
    wildcard 제거로 호출된다."""
    calls = []
    home = [apln(0, 36, -70, CUR, "Net")]
    active = [apln(0, 40, -47, "dd:dd:dd:dd:dd:dd", "Net")]
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", _fake_iw(home, active, calls))
    stale_ts = (datetime.now() - timedelta(seconds=120)).strftime("%Y-%m-%d %H:%M:%S")
    monkeypatch.setattr(wifi_roam, "get_latest_scan", lambda *a, **k: (
        [{"bssid": "cc:cc:cc:cc:cc:cc", "ssid": "Net", "channel": 40, "freq": 5200,
          "rssi": -50, "rssi_th": -75, "ld": 0, "load": 0, "noise": -95, "timestamp": stale_ts}],
        stale_ts,
    ))

    best, _, _, scanned = wifi_roam.staged_scan_best_candidate(
        _station(rssi=-70), None, ["Net"], "Net", STABLE, None
    )
    assert best is not None and best["bssid"] == "dd:dd:dd:dd:dd:dd"
    active_calls = [c for c in calls if c["passive"] is False]
    assert len(active_calls) == 1
    assert active_calls[0]["wildcard"] is False           # wildcard 제거
    assert active_calls[0]["freqs"] == ["5180", "5200"]   # scan_freq 스코프
    assert active_calls[0]["ssids"] == ["Net"]            # 설정 SSID directed


def test_stage3_empty_scan_freq_full_band_with_wildcard(monkeypatch):
    """scan_freq(WPA_FREQ) 미설정 → 전대역(freqs=None) + wildcard 포함 1회 폴백(안전장치)."""
    monkeypatch.setattr(wifi_roam, "WPA_FREQ", [])
    calls = []
    home = [apln(0, 36, -70, CUR, "Net")]
    active = [apln(0, 40, -47, "dd:dd:dd:dd:dd:dd", "Net")]
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", _fake_iw(home, active, calls))
    monkeypatch.setattr(wifi_roam, "get_latest_scan", lambda *a, **k: ([], None))

    best, _, _, _ = wifi_roam.staged_scan_best_candidate(
        _station(rssi=-70), None, ["Net"], "Net", STABLE, None
    )
    active_calls = [c for c in calls if c["passive"] is False]
    assert len(active_calls) == 1
    assert active_calls[0]["freqs"] is None
    assert active_calls[0]["wildcard"] is True


# ---------- 순수 헬퍼 ----------

def test_scan_block_fresh_boundary():
    now = datetime.now()
    fresh = now.strftime("%Y-%m-%d %H:%M:%S")
    stale = (now - timedelta(seconds=100)).strftime("%Y-%m-%d %H:%M:%S")
    assert wifi_roam.scan_block_fresh(fresh, 45) is True
    assert wifi_roam.scan_block_fresh(stale, 45) is False
    assert wifi_roam.scan_block_fresh(None, 45) is False
    assert wifi_roam.scan_block_fresh("garbage", 45) is False


def test_baseline_from_entries_found_and_fallback():
    entries = [
        {"bssid": CUR, "rssi": -55},
        {"bssid": "bb:bb:bb:bb:bb:bb", "rssi": -48},
    ]
    assert wifi_roam.baseline_from_entries(entries, CUR, -70) == -55       # 현재 AP 발견
    assert wifi_roam.baseline_from_entries(entries, "zz:zz:zz:zz:zz:zz", -70) == -70  # 폴백
    assert wifi_roam.baseline_from_entries(entries, None, -70) == -70


# ---------- iw_scan_to_ap_lines 옵션 ----------

class _Run:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


_SR = (
    "bssid / frequency / signal level / flags / ssid\n"
    "bb:bb:bb:bb:bb:bb\t5180\t-45\t[WPA2-PSK-CCMP][ESS]\tNet\n"
)


def test_iw_passive_cmd_has_passive_no_ssid(monkeypatch):
    monkeypatch.setattr(wifi_roam.time, "sleep", lambda *_: None)
    cap = {}

    def se(cmd, *a, **k):
        if cmd[0] == "iw":
            cap["cmd"] = list(cmd)
            return _Run(0)
        if "scan_results" in cmd:
            return _Run(0, _SR)
        return _Run(0)

    monkeypatch.setattr(wifi_roam.subprocess, "run", se)
    wifi_roam.iw_scan_to_ap_lines(None, [5180], passive=True)
    assert "passive" in cap["cmd"]
    assert "ssid" not in cap["cmd"]          # 패시브는 directed probe 없음
    assert "freq" in cap["cmd"] and "5180" in cap["cmd"]


def test_iw_active_no_wildcard_directed_only(monkeypatch):
    monkeypatch.setattr(wifi_roam.time, "sleep", lambda *_: None)
    cap = {}

    def se(cmd, *a, **k):
        if cmd[0] == "iw":
            cap["cmd"] = list(cmd)
            return _Run(0)
        if "scan_results" in cmd:
            return _Run(0, _SR)
        return _Run(0)

    monkeypatch.setattr(wifi_roam.subprocess, "run", se)
    wifi_roam.iw_scan_to_ap_lines(["Net"], ["5180"], include_wildcard=False)
    c = cap["cmd"]
    vals = c[c.index("ssid") + 1:]
    assert vals == ["Net"]                   # wildcard "" 없음, directed만
    assert "passive" not in c
