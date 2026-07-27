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
import json
import time
import wifi_roam
from datetime import datetime, timedelta

import pytest

wifi_roam.logger = MagicMock()


def apln(idx, ch, rssi, bssid, ssid, freq=None):
    """pipe 포맷 스캔 라인 `NN|ch|rssi|ld|bssid|freq|ssid`.
    freq(field[5])는 scan_results 원본값 — filter_ap_lines_by_freq가 이걸로 홈채널을 거른다."""
    if freq is None:
        freq = wifi_roam.channel_to_freq(ch)
    return f"{idx:02d}|{ch}|{rssi}|0|{bssid}|{freq}|{ssid}"


@pytest.fixture(autouse=True)
def _globals(monkeypatch):
    monkeypatch.setattr(wifi_roam, "WPA_TH_2G", -75)
    monkeypatch.setattr(wifi_roam, "WPA_TH_5G", -75)
    monkeypatch.setattr(wifi_roam, "WPA_FREQ", ["5180", "5200"])  # ch36, ch40
    monkeypatch.setattr(wifi_roam, "DIFF_TH", 10)
    monkeypatch.setattr(wifi_roam, "ENABLE_LOAD_BASED_ROAM", False)
    monkeypatch.setattr(wifi_roam, "ENABLE_PREDICTIVE_ROAM", False)
    monkeypatch.setattr(wifi_roam, "CACHE_FRESH_SEC", 45)
    # 모듈 전역이라 테스트 간 누수 → 격리 위해 매 테스트 리셋(없으면 앞 테스트의 스캔 시각이
    # 남아 뒤 테스트의 캐시 블록이 self-induced로 오판된다).
    monkeypatch.setattr(wifi_roam, "_LAST_SELF_SCAN_TS", None)
    monkeypatch.setattr(wifi_roam, "_LAST_SELF_SCAN_END_TS", None)
    # 시계 스텝 감지 앵커도 전역 — 리셋 없으면 앞 테스트의 patch된 시각이 스텝으로 오판된다.
    monkeypatch.setattr(wifi_roam, "_LAST_WALL_TS", None, raising=False)
    monkeypatch.setattr(wifi_roam, "_LAST_MONO_TS", None, raising=False)
    # load_roaming_config 테스트가 전역을 덮으므로 복원 대상에 포함시킨다.
    monkeypatch.setattr(wifi_roam, "ENABLE_STAGED_SCAN", True)
    monkeypatch.setattr(wifi_roam, "SELF_INDUCED_TAIL_SEC", 10)
    monkeypatch.setattr(wifi_roam, "SKIP_REDUNDANT_ACTIVE_SCAN", True)
    monkeypatch.setattr(wifi_roam, "HOME_PASSIVE", True, raising=False)


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
    """홈채널 패시브에서 후보를 찾으면 액티브 폴백 없이 바로 반환."""
    calls = []
    home = [apln(0, 36, -68, CUR, "Net"), apln(1, 36, -45, "bb:bb:bb:bb:bb:bb", "Net")]
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", _fake_iw(home, None, calls))
    monkeypatch.setattr(wifi_roam, "get_latest_scan", lambda *a, **k: ([], None))

    best, reason, score, scanned = wifi_roam.staged_scan_best_candidate(
        _station(rssi=-70), None, ["Net"], "Net", STABLE, None
    )
    assert best is not None and best["bssid"] == "bb:bb:bb:bb:bb:bb"
    assert scanned is True
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
    c = cap["cmd"]
    assert "passive" in c
    assert "ssid" not in c                   # 패시브는 directed probe 없음
    assert "freq" in c and "5180" in c
    # [회귀] iw 5.19 문법 `scan [freq <freq>*] ... [ssid <ssid>*|passive]` —
    # passive 는 맨 뒤 그룹이라 freq 뒤에 와야 한다. 앞에 두면 iw가 rc=1로 즉시 실패해
    # 스캔이 아예 안 돈다(온타겟 실측). 멤버십만 보던 종전 단언은 이 버그를 못 잡았다.
    assert c.index("passive") > c.index("freq"), f"passive는 freq 뒤에 와야 함: {c}"
    assert c[-1] == "passive", f"passive는 마지막 토큰이어야 함: {c}"


# ---------- 회귀: Codex 리뷰 P2 2건 ----------

def test_stage1_ignores_offchannel_bss_from_full_table(monkeypatch):
    """[회귀] 홈채널 패시브 스캔이라도 wpa_cli scan_results는 BSS 테이블 전체를 준다.
    다른 채널(ch40)의 stale·강신호 BSS가 Stage 1 후보로 뽑혀 freshness 게이트와 액티브
    폴백을 우회하면 안 된다 → 홈 주파수(5180)로 필터되어 Stage 3까지 내려가야 한다."""
    calls = []
    # 홈 스캔이 반환한 테이블: 홈채널(ch36) 현재 AP + **오프채널(ch40) stale 강신호 AP**
    home_table = [
        apln(0, 36, -70, CUR, "Net"),
        apln(1, 40, -30, "ee:ee:ee:ee:ee:ee", "Net"),   # stale off-channel, 매우 강함
    ]
    active = [apln(0, 36, -45, "ff:ff:ff:ff:ff:ff", "Net")]
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", _fake_iw(home_table, active, calls))
    monkeypatch.setattr(wifi_roam, "get_latest_scan", lambda *a, **k: ([], None))

    best, _, _, _ = wifi_roam.staged_scan_best_candidate(
        _station(rssi=-70, freq=5180), None, ["Net"], "Net", STABLE, None
    )
    # 오프채널 stale AP가 선택되면 안 됨 — 액티브 폴백 결과가 나와야 한다.
    assert best is not None
    assert best["bssid"] != "ee:ee:ee:ee:ee:ee"
    assert best["bssid"] == "ff:ff:ff:ff:ff:ff"
    assert any(c["passive"] is False for c in calls)   # Stage 3까지 진행됨


def test_cache_snapshot_taken_before_any_scan(monkeypatch):
    """[회귀] Stage 1 스캔은 nl80211 이벤트로 wifi_logger_scan이 ap.log에 새 블록을 쓰게
    한다. 캐시를 스캔 '후'에 읽으면 그 블록을 신선한 배경 캐시로 오인한다 → 캐시 스냅샷은
    반드시 첫 스캔보다 먼저 일어나야 한다."""
    order = []

    def fake_iw(ssids, freqs, passive=False, include_wildcard=True):
        order.append("scan")
        return None

    def fake_cache(*a, **k):
        order.append("cache")
        return ([], None)

    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", fake_iw)
    monkeypatch.setattr(wifi_roam, "get_latest_scan", fake_cache)

    wifi_roam.staged_scan_best_candidate(
        _station(rssi=-70), None, ["Net"], "Net", STABLE, None
    )
    assert order[0] == "cache", f"캐시 스냅샷이 스캔보다 먼저여야 함: {order}"


def test_self_induced_cache_block_rejected_across_iterations(monkeypatch):
    """[회귀] 반복 N의 로밍 스캔이 유발한 ap.log 블록을 반복 N+1의 Stage 2가 '신선한 배경
    캐시'로 오인하면 안 된다. wifi_logger_scan은 스캔 주체를 구분하지 않고 전 채널
    getscantable을 덤프하므로, 그 블록의 교차채널 항목은 타임스탬프만 새롭고 값은 stale이다."""
    monkeypatch.setattr(wifi_roam, "_LAST_SELF_SCAN_TS", None)

    # ap.log를 흉내: 스캔이 일어날 때마다 '지금' 타임스탬프로 stale 교차채널 AP 블록이 생김
    log = {"ts": None}
    stale_cross = [{"bssid": "ee:ee:ee:ee:ee:ee", "ssid": "Net", "channel": 40,
                    "freq": 5200, "rssi": -30, "rssi_th": -75, "ld": 0,
                    "load": 0, "noise": -95, "timestamp": ""}]

    def fake_iw(ssids, freqs, passive=False, include_wildcard=True):
        wifi_roam._LAST_SELF_SCAN_TS = time.time()          # 실제 코드와 동일한 경계 기록
        wifi_roam._LAST_SELF_SCAN_END_TS = time.time()
        log["ts"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # logger가 블록 append
        return None                                          # 후보 없음 → 다음 단계로

    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", fake_iw)
    monkeypatch.setattr(
        wifi_roam, "get_latest_scan",
        lambda *a, **k: ((stale_cross, log["ts"]) if log["ts"] else ([], None)),
    )

    st = _station(rssi=-70)
    # 반복 1: 캐시 없음 → 스캔들이 돌며 self-induced 블록 생성
    b1, _, _, _ = wifi_roam.staged_scan_best_candidate(st, None, ["Net"], "Net", STABLE, None)
    assert b1 is None
    assert log["ts"] is not None                    # 블록이 생겼음
    # 반복 2: 그 블록은 '내 스캔이 유발한 것' → 배경 캐시로 신뢰하면 안 됨
    b2, _, _, _ = wifi_roam.staged_scan_best_candidate(st, None, ["Net"], "Net", STABLE, None)
    assert b2 is None, f"self-induced 블록의 stale off-channel AP가 채택됨: {b2}"


def test_genuine_background_cache_still_used(monkeypatch):
    """대조군: 자기 스캔 이력이 없는(=진짜 bgscan) 신선한 블록은 정상적으로 채택된다.
    self-induced 판정이 과하게 걸려 Stage 2를 통째로 죽이지 않음을 고정."""
    fresh_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cache = [{"bssid": "cc:cc:cc:cc:cc:cc", "ssid": "Net", "channel": 40, "freq": 5200,
              "rssi": -50, "rssi_th": -75, "ld": 0, "load": 0, "noise": -95,
              "timestamp": fresh_ts}]
    calls = []
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", _fake_iw(None, None, calls))
    monkeypatch.setattr(wifi_roam, "get_latest_scan", lambda *a, **k: (cache, fresh_ts))

    best, _, _, _ = wifi_roam.staged_scan_best_candidate(
        _station(rssi=-70), None, ["Net"], "Net", STABLE, None
    )
    assert best is not None and best["bssid"] == "cc:cc:cc:cc:cc:cc"


def test_scan_block_self_induced_helper():
    now = time.time()
    ts_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    older = (datetime.now() - timedelta(seconds=30)).strftime("%Y-%m-%d %H:%M:%S")
    # 자기 스캔 기록이 없으면 판정 불가 → False(기존 동작 유지)
    assert wifi_roam.scan_block_self_induced(ts_now, None) is False
    # 블록이 자기 스캔 윈도우 안 → self-induced
    assert wifi_roam.scan_block_self_induced(ts_now, now, now) is True
    # 블록이 자기 스캔보다 훨씬 이전 → 배경 캐시
    assert wifi_roam.scan_block_self_induced(older, now, now) is False
    # 형식 불량은 보수적으로 False
    assert wifi_roam.scan_block_self_induced("garbage", now, now) is False


def test_self_induced_window_has_upper_bound():
    """[회귀] self-induced 판정에 상한이 없으면, ap.log가 append-only이고 get_latest_scan이
    항상 최신 블록만 주므로 **첫 로밍 스캔 이후 Stage 2가 영구히 죽는다**(교차채널 캐시
    기능의 사실상 제거). 자기 스캔보다 충분히 나중에 기록된 블록은 진짜 배경 bgscan이다."""
    ts_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tail = wifi_roam.SELF_INDUCED_TAIL_SEC
    # 자기 스캔이 방금 끝났다 → 지금 블록은 유발된 것
    now = time.time()
    assert wifi_roam.scan_block_self_induced(ts_now, now, now) is True
    # 자기 스캔이 (tail + 여유)만큼 전에 끝났다 → 지금 블록은 배경 bgscan
    old_scan = now - (tail + 20)
    assert wifi_roam.scan_block_self_induced(ts_now, old_scan, old_scan) is False, \
        "상한 없는 게이트 — 오래전 자기 스캔 때문에 진짜 배경 블록까지 거부됨"
    # 한 시간 전 자기 스캔도 마찬가지(영구 사망 방지)
    hour_ago = now - 3600
    assert wifi_roam.scan_block_self_induced(ts_now, hour_ago, hour_ago) is False


def test_stage2_recovers_after_earlier_self_scan(monkeypatch):
    """[회귀] 이전 반복에서 자기 스캔이 있었더라도, 그 뒤 충분히 지나 기록된 진짜 bgscan
    블록은 Stage 2에서 정상 사용돼야 한다. 상한 없는 게이트면 여기서 best=None이 된다."""
    now = time.time()
    old = now - (wifi_roam.SELF_INDUCED_TAIL_SEC + 30)
    monkeypatch.setattr(wifi_roam, "_LAST_SELF_SCAN_TS", old)
    monkeypatch.setattr(wifi_roam, "_LAST_SELF_SCAN_END_TS", old)

    fresh_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cache = [{"bssid": "cc:cc:cc:cc:cc:cc", "ssid": "Net", "channel": 40, "freq": 5200,
              "rssi": -50, "rssi_th": -75, "ld": 0, "load": 0, "noise": -95,
              "timestamp": fresh_ts}]
    calls = []
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", _fake_iw(None, None, calls))
    monkeypatch.setattr(wifi_roam, "get_latest_scan", lambda *a, **k: (cache, fresh_ts))

    best, _, _, _ = wifi_roam.staged_scan_best_candidate(
        _station(rssi=-70), None, ["Net"], "Net", STABLE, None
    )
    assert best is not None and best["bssid"] == "cc:cc:cc:cc:cc:cc", \
        "이전 자기 스캔 때문에 진짜 배경 캐시가 영구 거부됨 (Stage 2 사망)"


def test_filter_ap_lines_by_freq():
    lines = [
        apln(0, 36, -50, "aa:aa:aa:aa:aa:aa", "Net"),          # 5180
        apln(1, 40, -40, "bb:bb:bb:bb:bb:bb", "Net"),          # 5200
        apln(2, 36, -60, "cc:cc:cc:cc:cc:cc", "Net"),          # 5180
        "malformed-line",
    ]
    out = wifi_roam.filter_ap_lines_by_freq(lines, 5180)
    assert len(out) == 2
    assert all(l.split("|")[5] == "5180" for l in out)
    assert wifi_roam.filter_ap_lines_by_freq(lines, None) == []
    assert wifi_roam.filter_ap_lines_by_freq(None, 5180) == []


def test_scan_records_start_and_end_timestamps(monkeypatch):
    """[회귀] 래퍼가 스캔 시작/종료 시각을 **실제로** 남기는지 직접 고정한다.

    `scan_block_self_induced`의 `(self_scan_end_ts or self_scan_ts)` 폴백 때문에, finally의
    종료 기록을 통째로 지워도 나머지 테스트가 전부 통과한다(= 방어적 폴백이 실패를 가림).
    상한이 시작 시각 기준으로 좁아져 '위험한 방향'으로 조용히 degrade하므로, 이 성질만
    따로 고정한다 — 이번 리뷰에서 두 번 반복된 '테스트 전부 통과인데 의미는 깨짐' 형태."""
    monkeypatch.setattr(wifi_roam.time, "sleep", lambda *_: None)

    def se(cmd, *a, **k):
        if cmd[0] == "iw":
            return _Run(0)
        if "scan_results" in cmd:
            return _Run(0, _SR)
        return _Run(0)

    monkeypatch.setattr(wifi_roam.subprocess, "run", se)
    wifi_roam.iw_scan_to_ap_lines(None, [5180], passive=True)

    assert wifi_roam._LAST_SELF_SCAN_TS is not None, "스캔 시작 시각 미기록"
    assert wifi_roam._LAST_SELF_SCAN_END_TS is not None, "스캔 종료 시각 미기록(finally 유실)"
    assert wifi_roam._LAST_SELF_SCAN_END_TS >= wifi_roam._LAST_SELF_SCAN_TS


def test_scan_records_end_even_when_scan_fails(monkeypatch):
    """조기 return(예외/타임아웃) 경로에서도 finally가 종료 시각을 남겨 윈도우 상한이
    유실되지 않아야 한다."""
    def boom(*a, **k):
        raise RuntimeError("driver gone")

    monkeypatch.setattr(wifi_roam.subprocess, "run", boom)
    try:
        wifi_roam.iw_scan_to_ap_lines(None, [5180], passive=True)
    except Exception:
        pass
    assert wifi_roam._LAST_SELF_SCAN_END_TS is not None, "실패 경로에서 종료 시각 미기록"


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


# ---------- 런타임 설정 노출 (roaming.STAGED_SCAN) ----------

def test_staged_scan_config_applies_to_globals():
    """`.<iface>.roaming.STAGED_SCAN` 이 전역에 반영돼야 한다 — 현장에서 재배포 없이
    단계형 스캔을 끄거나(무회귀 폴백) 임계값을 튜닝할 수 있어야 하기 때문."""
    data = {"mlan0": {"roaming": {"STAGED_SCAN": {
        "enable": False, "cache_fresh_sec": 90, "self_induced_tail_sec": 25,
    }}}}
    wifi_roam.load_roaming_config("mlan0", data)
    assert wifi_roam.ENABLE_STAGED_SCAN is False
    assert wifi_roam.CACHE_FRESH_SEC == 90
    assert wifi_roam.SELF_INDUCED_TAIL_SEC == 25


def test_staged_scan_config_absent_keeps_defaults():
    """STAGED_SCAN 섹션이 없으면 기본값(단계형 활성)이 유지된다 — 무회귀."""
    wifi_roam.load_roaming_config("mlan0", {"mlan0": {"roaming": {}}})
    assert wifi_roam.ENABLE_STAGED_SCAN is wifi_roam.DEFAULT_ENABLE_STAGED_SCAN
    assert wifi_roam.CACHE_FRESH_SEC == wifi_roam.DEFAULT_CACHE_FRESH_SEC
    assert wifi_roam.SELF_INDUCED_TAIL_SEC == wifi_roam.DEFAULT_SELF_INDUCED_TAIL_SEC


def test_staged_scan_config_invalid_values_keep_defaults():
    """형식 오류(문자열/None)는 기본값 유지 — 한 줄 오타로 데몬이 죽지 않아야 한다."""
    data = {"mlan0": {"roaming": {"STAGED_SCAN": {
        "enable": "yes-ish", "cache_fresh_sec": "bad", "self_induced_tail_sec": None,
    }}}}
    wifi_roam.load_roaming_config("mlan0", data)
    assert wifi_roam.CACHE_FRESH_SEC == wifi_roam.DEFAULT_CACHE_FRESH_SEC
    assert wifi_roam.SELF_INDUCED_TAIL_SEC == wifi_roam.DEFAULT_SELF_INDUCED_TAIL_SEC
    assert isinstance(wifi_roam.ENABLE_STAGED_SCAN, bool)


def test_staged_scan_config_rejects_zero_and_negative():
    """[회귀] 0/음수는 기본값 유지. 스키마의 minimum:1 을 데몬도 강제해야 한다 —
    self_induced_tail_sec=0 이면 자기 스캔이 유발한 블록을 못 걸러 stale 교차채널
    데이터로 로밍하고(위험), cache_fresh_sec=0 이면 Stage 2가 조용히 영구 비활성된다."""
    for bad in (0, -1):
        data = {"mlan0": {"roaming": {"STAGED_SCAN": {
            "cache_fresh_sec": bad, "self_induced_tail_sec": bad,
        }}}}
        wifi_roam.load_roaming_config("mlan0", data)
        assert wifi_roam.CACHE_FRESH_SEC == wifi_roam.DEFAULT_CACHE_FRESH_SEC, bad
        assert wifi_roam.SELF_INDUCED_TAIL_SEC == wifi_roam.DEFAULT_SELF_INDUCED_TAIL_SEC, bad


def test_positive_int_caster():
    assert wifi_roam._positive_int(1) == 1
    assert wifi_roam._positive_int("45") == 45
    for bad in (0, -1, "0", "-3"):
        with pytest.raises(ValueError):
            wifi_roam._positive_int(bad)


# ---------- 단일채널 액티브 폴백 스킵 (scan_freq ⊆ 홈채널) ----------

def test_skip_active_fallback_when_single_channel_home_covers(monkeypatch):
    """scan_freq ⊆ 홈채널 + Stage1 패시브 성공 + 후보 미달 → Stage3 액티브 폴백 스킵.
    단일채널 배포에서 매 주기 불필요한 액티브 스캔(probe)을 없앤다."""
    monkeypatch.setattr(wifi_roam, "WPA_FREQ", ["5240"])          # 단일 채널(ch48)
    calls = []
    # 홈 패시브: 현재 AP(-50) + 동급 후보(-49, diff=1<10 이라 미달)
    home = [apln(0, 48, -50, CUR, "Net"), apln(1, 48, -49, "bb:bb:bb:bb:bb:bb", "Net")]
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", _fake_iw(home, None, calls))
    monkeypatch.setattr(wifi_roam, "get_latest_scan", lambda *a, **k: ([], None))

    best, _, _, scanned = wifi_roam.staged_scan_best_candidate(
        _station(rssi=-70, freq=5240), None, ["Net"], "Net", STABLE, None
    )
    assert best is None
    assert any(c["passive"] is True for c in calls)                       # 홈 패시브는 돎
    assert not any(c["passive"] is False for c in calls), f"active fired: {calls}"  # 액티브 스킵


def test_active_fallback_when_multichannel(monkeypatch):
    """scan_freq 가 홈채널보다 넓으면(다채널) 액티브 폴백을 스킵하지 않는다 —
    다른 채널 후보는 패시브 홈스캔이 못 보므로 액티브가 필요하다."""
    monkeypatch.setattr(wifi_roam, "WPA_FREQ", ["5180", "5240"])  # 홈(5240) + 추가(5180)
    calls = []
    home = [apln(0, 48, -50, CUR, "Net")]                          # 홈엔 현재 AP만
    active = [apln(0, 36, -35, "dd:dd:dd:dd:dd:dd", "Net")]        # 다른 채널의 강한 후보
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", _fake_iw(home, active, calls))
    monkeypatch.setattr(wifi_roam, "get_latest_scan", lambda *a, **k: ([], None))

    best, _, _, _ = wifi_roam.staged_scan_best_candidate(
        _station(rssi=-70, freq=5240), None, ["Net"], "Net", STABLE, None
    )
    assert any(c["passive"] is False for c in calls), "다채널이면 액티브 폴백 실행돼야"
    assert best is not None and best["bssid"] == "dd:dd:dd:dd:dd:dd"  # 다른채널 후보로 로밍


def test_no_skip_when_home_sees_only_current_ap(monkeypatch):
    """[회귀] 단일채널 + 홈 패시브 결과가 현재 AP 자신뿐이면 스킵하지 **않는다** — 액티브
    directed probe 로 재발견을 시도해야 한다.

    현재 AP 의 BSS 테이블 엔트리는 사용 중(in-use)이라 age/scan-miss 만료에서 면제된다:
    이번 dwell 에서 beacon 을 하나도 못 받아도 scan_results 에 항상 남는다. 즉 '현재 AP 만
    보임'은 '같은 채널에 다른 AP 가 없음'의 증거가 아니라, 이웃 AP beacon 이 간섭으로
    유실된 상태와 구분 불가다. 로밍컨디션 중에는 bgscan 도 정지하므로 여기서 스킵하면
    probe 기반 재발견 경로가 전무해져 beacon 수신이 회복될 때까지 약한 AP 에 고착된다.
    이웃 후보를 실제로 본 경우의 스킵(#122 본래 목적)은 위 테스트가 그대로 고정한다."""
    monkeypatch.setattr(wifi_roam, "WPA_FREQ", ["5240"])
    calls = []
    home = [apln(0, 48, -50, CUR, "Net")]  # BSS 테이블 상주 현재 AP 엔트리만
    active = [apln(0, 48, -35, "bb:bb:bb:bb:bb:bb", "Net")]  # directed probe 가 재발견한 이웃
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", _fake_iw(home, active, calls))
    monkeypatch.setattr(wifi_roam, "get_latest_scan", lambda *a, **k: ([], None))

    best, _, _, _ = wifi_roam.staged_scan_best_candidate(
        _station(rssi=-70, freq=5240), None, ["Net"], "Net", STABLE, None
    )
    assert any(c["passive"] is True for c in calls)                  # 홈 패시브는 돎
    assert any(c["passive"] is False for c in calls), \
        "현재 AP 상주 엔트리만으로 '커버됨' 판정 금지 — 액티브 재발견 실행돼야"
    assert best is not None and best["bssid"] == "bb:bb:bb:bb:bb:bb"


def test_no_skip_when_home_sees_only_other_ssid(monkeypatch):
    """[회귀] 홈 패시브가 결과는 냈지만 우리 SSID 후보가 하나도 없으면(타 SSID만) 스킵하지
    않는다 — RF 열악으로 우리 AP beacon 을 놓쳤을 때 액티브 directed probe 로 재시도해야."""
    monkeypatch.setattr(wifi_roam, "WPA_FREQ", ["5240"])
    calls = []
    # 홈 패시브 결과는 있으나 전부 다른 SSID → allowed("Net") 후보 0개 → home_scan_ok=False
    home = [apln(0, 48, -55, "ee:ee:ee:ee:ee:ee", "OtherNet")]
    active = [apln(0, 48, -40, "dd:dd:dd:dd:dd:dd", "Net")]
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", _fake_iw(home, active, calls))
    monkeypatch.setattr(wifi_roam, "get_latest_scan", lambda *a, **k: ([], None))

    wifi_roam.staged_scan_best_candidate(
        _station(rssi=-70, freq=5240), None, ["Net"], "Net", STABLE, None
    )
    assert any(c["passive"] is False for c in calls), \
        "우리 SSID 를 패시브로 못 봤으면 액티브 폴백으로 재시도해야(스킵 금지)"


def test_no_skip_when_home_scan_failed(monkeypatch):
    """단일채널이어도 Stage1 패시브 스캔 실패(결과 없음)면 액티브 폴백을 재시도로 실행."""
    monkeypatch.setattr(wifi_roam, "WPA_FREQ", ["5240"])
    calls = []
    active = [apln(0, 48, -40, "dd:dd:dd:dd:dd:dd", "Net")]
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", _fake_iw(None, active, calls))  # 홈 패시브=None
    monkeypatch.setattr(wifi_roam, "get_latest_scan", lambda *a, **k: ([], None))

    wifi_roam.staged_scan_best_candidate(
        _station(rssi=-70, freq=5240), None, ["Net"], "Net", STABLE, None
    )
    assert any(c["passive"] is False for c in calls), "홈 스캔 실패 시 액티브 폴백은 재시도로 실행돼야"


def test_skip_disabled_by_config(monkeypatch):
    """SKIP_REDUNDANT_ACTIVE_SCAN=False → 단일채널이어도 액티브 폴백 실행(무회귀/hidden 대비)."""
    monkeypatch.setattr(wifi_roam, "WPA_FREQ", ["5240"])
    monkeypatch.setattr(wifi_roam, "SKIP_REDUNDANT_ACTIVE_SCAN", False)
    calls = []
    home = [apln(0, 48, -50, CUR, "Net")]
    active = [apln(0, 48, -49, "bb:bb:bb:bb:bb:bb", "Net")]
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", _fake_iw(home, active, calls))
    monkeypatch.setattr(wifi_roam, "get_latest_scan", lambda *a, **k: ([], None))

    wifi_roam.staged_scan_best_candidate(
        _station(rssi=-70, freq=5240), None, ["Net"], "Net", STABLE, None
    )
    assert any(c["passive"] is False for c in calls), "옵션 off면 액티브 폴백 실행돼야"


def test_skip_redundant_active_config_applies():
    """`.roaming.STAGED_SCAN.skip_redundant_active` 가 전역에 반영."""
    wifi_roam.load_roaming_config(
        "mlan0", {"mlan0": {"roaming": {"STAGED_SCAN": {"skip_redundant_active": False}}}}
    )
    assert wifi_roam.SKIP_REDUNDANT_ACTIVE_SCAN is False
    wifi_roam.load_roaming_config(
        "mlan0", {"mlan0": {"roaming": {"STAGED_SCAN": {"skip_redundant_active": True}}}}
    )
    assert wifi_roam.SKIP_REDUNDANT_ACTIVE_SCAN is True


def test_skip_redundant_active_default_true():
    """STAGED_SCAN 섹션 부재 시 기본값(True) 유지."""
    wifi_roam.load_roaming_config("mlan0", {"mlan0": {"roaming": {}}})
    assert wifi_roam.SKIP_REDUNDANT_ACTIVE_SCAN is wifi_roam.DEFAULT_SKIP_REDUNDANT_ACTIVE_SCAN


# ---------- Stage 2 는 교차채널 전용 (홈채널 캐시 역전 방지) ----------

def test_stage2_ignores_home_channel_cache_entries(monkeypatch):
    """[회귀] Stage 2 캐시는 '교차채널 보완'용 — Stage 1 이 방금 실측한 홈채널 BSS 를 최대
    CACHE_FRESH_SEC 전의 묵은 RSSI 로 재평가해, 방금의 기각(DIFF_TH 미달)을 뒤집고 정책
    미달 로밍을 실행하면 안 된다(묵은 측정이 신선한 측정을 이기는 역전)."""
    calls = []
    # Stage 1 실측: baseline=-78(현재 AP), 이웃 bb=-72 → diff 6 < 10 이라 기각
    home = [apln(0, 36, -78, CUR, "Net"), apln(1, 36, -72, "bb:bb:bb:bb:bb:bb", "Net")]
    active = [apln(0, 40, -47, "dd:dd:dd:dd:dd:dd", "Net")]
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", _fake_iw(home, active, calls))
    fresh_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 40초 전 배경 캐시에는 같은 bb 가 -60(diff 18)으로 기록돼 있다 — 묵은 값
    cache = [{"bssid": "bb:bb:bb:bb:bb:bb", "ssid": "Net", "channel": 36, "freq": 5180,
              "rssi": -60, "rssi_th": -75, "ld": 0, "load": 0, "noise": -95,
              "timestamp": fresh_ts}]
    monkeypatch.setattr(wifi_roam, "get_latest_scan", lambda *a, **k: (cache, fresh_ts))

    best, _, _, _ = wifi_roam.staged_scan_best_candidate(
        _station(rssi=-70, freq=5180), None, ["Net"], "Net", STABLE, None
    )
    # 묵은 홈채널 캐시(bb, -60)로 로밍 금지 — Stage 3 액티브의 교차채널 결과(dd)여야 한다
    assert best is not None and best["bssid"] == "dd:dd:dd:dd:dd:dd", \
        f"Stage 1 이 방금 기각한 홈채널 BSS 가 묵은 캐시 값으로 재채택됨: {best}"
    assert any(c["passive"] is False for c in calls)


def test_stage2_mixed_cache_evaluates_cross_channel_only(monkeypatch):
    """[회귀] 혼합(홈+교차) 캐시에서 Stage 2 의 **평가 대상**은 필터된 stage2_entries 여야
    한다 — 필터만 만들고 평가를 원본 cache_entries 로 하는 부분 회귀 뮤턴트를 킬한다.
    홈채널의 강한 묵은 엔트리(bb, -40)가 아니라 교차채널 엔트리(cc, -55)가 채택돼야 한다."""
    calls = []
    home = [apln(0, 36, -70, CUR, "Net")]
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", _fake_iw(home, None, calls))
    fresh_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cache = [
        {"bssid": "bb:bb:bb:bb:bb:bb", "ssid": "Net", "channel": 36, "freq": 5180,
         "rssi": -40, "rssi_th": -75, "ld": 0, "load": 0, "noise": -95, "timestamp": fresh_ts},
        {"bssid": "cc:cc:cc:cc:cc:cc", "ssid": "Net", "channel": 40, "freq": 5200,
         "rssi": -55, "rssi_th": -75, "ld": 0, "load": 0, "noise": -95, "timestamp": fresh_ts},
    ]
    monkeypatch.setattr(wifi_roam, "get_latest_scan", lambda *a, **k: (cache, fresh_ts))

    best, _, _, _ = wifi_roam.staged_scan_best_candidate(
        _station(rssi=-70, freq=5180), None, ["Net"], "Net", STABLE, None
    )
    assert best is not None and best["bssid"] == "cc:cc:cc:cc:cc:cc", \
        f"홈채널 묵은 엔트리가 평가에 남음(소비 지점 미필터): {best}"
    assert not any(c["passive"] is False for c in calls)  # 교차채널 캐시로 충족 — 액티브 불필요


def test_stage2_uses_home_cache_when_home_scan_failed(monkeypatch):
    """대조군: Stage 1 홈 패시브가 실패하면 신선한 홈채널 실측이 없으므로, 홈채널 캐시도
    후보로 허용한다(없는 것보단 낫다) — 필터가 과하게 걸리지 않음을 고정."""
    calls = []
    active = [apln(0, 40, -47, "dd:dd:dd:dd:dd:dd", "Net")]
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", _fake_iw(None, active, calls))
    fresh_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cache = [{"bssid": "cc:cc:cc:cc:cc:cc", "ssid": "Net", "channel": 36, "freq": 5180,
              "rssi": -50, "rssi_th": -75, "ld": 0, "load": 0, "noise": -95,
              "timestamp": fresh_ts}]
    monkeypatch.setattr(wifi_roam, "get_latest_scan", lambda *a, **k: (cache, fresh_ts))

    best, _, _, _ = wifi_roam.staged_scan_best_candidate(
        _station(rssi=-70, freq=5180), None, ["Net"], "Net", STABLE, None
    )
    assert best is not None and best["bssid"] == "cc:cc:cc:cc:cc:cc", \
        "홈 스캔 실패 시엔 홈채널 캐시가 유일한 정보 — 과잉 필터로 버려짐"


# ---------- 시계 스텝(wall vs monotonic) 감지 ----------

def test_clock_step_detected_helper(monkeypatch):
    """전진/후진 스텝 감지 + 앵커 갱신. NTP slew(점진 보정) 수준의 미세 drift 는 무시."""
    monkeypatch.setattr(wifi_roam, "_LAST_WALL_TS", None, raising=False)
    monkeypatch.setattr(wifi_roam, "_LAST_MONO_TS", None, raising=False)
    t = {"wall": 1000.0, "mono": 500.0}
    monkeypatch.setattr(wifi_roam.time, "time", lambda: t["wall"])
    monkeypatch.setattr(wifi_roam.time, "monotonic", lambda: t["mono"])
    assert wifi_roam.clock_step_detected() is False      # 앵커 없음 → 판정 불가(보수적 False)
    t["wall"], t["mono"] = 1010.0, 510.0                  # 두 시계 동일 진행
    assert wifi_roam.clock_step_detected() is False
    t["wall"], t["mono"] = 1045.0, 513.0                  # wall +35s vs mono +3s → 전진 스텝
    assert wifi_roam.clock_step_detected() is True
    t["wall"], t["mono"] = 1040.0, 516.0                  # wall -5s vs mono +3s → 후진 스텝
    assert wifi_roam.clock_step_detected() is True
    t["wall"], t["mono"] = 1043.0, 519.0                  # 스텝 후 정상 진행 재개 → 자가 치유
    assert wifi_roam.clock_step_detected() is False


def test_stage2_skipped_after_clock_step(monkeypatch):
    """[회귀] self-induced 윈도우/신선도의 시간 앵커는 전부 wall-clock 이다. 직전 tick 이후
    시각 스텝(NTP step — fake-hwclock 기기는 부팅 후 첫 동기화가 큰 전진 스텝)이 감지되면
    그 tick 의 Stage 2 를 건너뛴다 — 자기 유발 블록이 윈도우를 이탈하면서 기록 시각만
    '신선'해 보여 stale 교차채널 값으로 로밍하는 오판 창을 닫는다."""
    monkeypatch.setattr(wifi_roam, "_LAST_WALL_TS", 1000.0, raising=False)
    monkeypatch.setattr(wifi_roam, "_LAST_MONO_TS", 500.0, raising=False)
    monkeypatch.setattr(wifi_roam.time, "time", lambda: 1033.0)      # wall +33s
    monkeypatch.setattr(wifi_roam.time, "monotonic", lambda: 503.0)  # mono +3s → 스텝
    calls = []
    home = [apln(0, 36, -70, CUR, "Net")]
    active = [apln(0, 40, -47, "dd:dd:dd:dd:dd:dd", "Net")]
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", _fake_iw(home, active, calls))
    fresh_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cache = [{"bssid": "cc:cc:cc:cc:cc:cc", "ssid": "Net", "channel": 40, "freq": 5200,
              "rssi": -50, "rssi_th": -75, "ld": 0, "load": 0, "noise": -95,
              "timestamp": fresh_ts}]
    monkeypatch.setattr(wifi_roam, "get_latest_scan", lambda *a, **k: (cache, fresh_ts))

    best, _, _, _ = wifi_roam.staged_scan_best_candidate(
        _station(rssi=-70, freq=5180), None, ["Net"], "Net", STABLE, None
    )
    assert best is not None and best["bssid"] == "dd:dd:dd:dd:dd:dd", \
        f"시계 스텝 tick 에 신뢰 불가한 캐시가 사용됨: {best}"
    assert any(c["passive"] is False for c in calls)     # Stage 3 액티브로 degrade


def test_stage2_used_when_clocks_advance_together(monkeypatch):
    """대조군: 두 시계가 같이 진행(스텝 없음)하면 캐시는 정상 사용된다 — 감지기가
    과하게 걸려 Stage 2 를 상시 죽이지 않음을 고정."""
    monkeypatch.setattr(wifi_roam, "_LAST_WALL_TS", 1000.0, raising=False)
    monkeypatch.setattr(wifi_roam, "_LAST_MONO_TS", 500.0, raising=False)
    monkeypatch.setattr(wifi_roam.time, "time", lambda: 1030.0)
    monkeypatch.setattr(wifi_roam.time, "monotonic", lambda: 530.0)
    calls = []
    home = [apln(0, 36, -70, CUR, "Net")]
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", _fake_iw(home, None, calls))
    fresh_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cache = [{"bssid": "cc:cc:cc:cc:cc:cc", "ssid": "Net", "channel": 40, "freq": 5200,
              "rssi": -50, "rssi_th": -75, "ld": 0, "load": 0, "noise": -95,
              "timestamp": fresh_ts}]
    monkeypatch.setattr(wifi_roam, "get_latest_scan", lambda *a, **k: (cache, fresh_ts))

    best, _, _, _ = wifi_roam.staged_scan_best_candidate(
        _station(rssi=-70, freq=5180), None, ["Net"], "Net", STABLE, None
    )
    assert best is not None and best["bssid"] == "cc:cc:cc:cc:cc:cc"
    assert not any(c["passive"] is False for c in calls)


# ---------- 배포 계층 일관성: cache_fresh_sec vs bgscan.interval ----------

def test_cache_fresh_default_covers_deployed_bgscan_interval():
    """[일관성] 코드 기본값과 템플릿 cache_fresh_sec 이 배포 계층의 bgscan.interval 을
    여유 포함으로 커버해야 한다 — 45s 기본이 배포 interval 60s 를 못 덮어 매 주기 마지막
    15초(25%) 구간에서 '존재하는 가장 신선한 배경 블록'조차 stale 판정되던 결함의 회귀 방지."""
    tmpl = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "..", "..", "opt", "wlan", "config", "wifi_init_conf.json",
    )
    with open(tmpl) as f:
        data = json.load(f)
    checked = 0
    for iface, cfg in data.items():
        if not isinstance(cfg, dict):
            continue
        bg = cfg.get("bgscan")
        staged = (cfg.get("roaming") or {}).get("STAGED_SCAN")
        if not isinstance(bg, dict) or not isinstance(staged, dict):
            continue
        interval = bg.get("interval")
        cfs = staged.get("cache_fresh_sec")
        if not isinstance(interval, int) or not isinstance(cfs, int):
            continue
        assert cfs == wifi_roam.DEFAULT_CACHE_FRESH_SEC, \
            f"{iface}: 템플릿({cfs}) ≠ 코드 기본값({wifi_roam.DEFAULT_CACHE_FRESH_SEC})"
        assert cfs >= interval + 5, \
            f"{iface}: cache_fresh_sec({cfs}) 가 bgscan.interval({interval})+지터 여유를 못 덮음"
        checked += 1
    assert checked >= 2, f"템플릿에서 검사된 iface 수 부족: {checked}"


# ---------- Stage 1 스캔 모드 (STAGED_SCAN.home_passive) ----------

def test_home_passive_false_stage1_directed_active(monkeypatch):
    """home_passive=false 면 Stage 1 홈채널 스캔이 패시브 대신 **directed 액티브**
    (allowed SSID probe, wildcard 없음 — Stage 3와 동일한 축소 원칙)로 실행된다.
    홈채널에 hidden 로밍 타깃이 있는 배포에서 스킵 최적화를 유지한 채 hidden 을 발견."""
    monkeypatch.setattr(wifi_roam, "HOME_PASSIVE", False, raising=False)
    calls = []
    home = [apln(0, 36, -70, CUR, "Net"), apln(1, 36, -45, "bb:bb:bb:bb:bb:bb", "Net")]
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", _fake_iw(None, home, calls))
    monkeypatch.setattr(wifi_roam, "get_latest_scan", lambda *a, **k: ([], None))

    best, _, _, scanned = wifi_roam.staged_scan_best_candidate(
        _station(rssi=-70), None, ["Net"], "Net", STABLE, None
    )
    assert len(calls) == 1, f"홈 스캔 1회만 실행돼야: {calls}"
    c = calls[0]
    assert c["passive"] is False                 # 액티브
    assert c["ssids"] == ["Net"]                 # allowed directed probe
    assert c["freqs"] == [5180]                  # 홈채널 스코프
    assert c["wildcard"] is False                # wildcard 제거(Stage 3와 동일)
    assert best is not None and best["bssid"] == "bb:bb:bb:bb:bb:bb"
    assert scanned is True


def test_home_passive_default_true_keeps_passive(monkeypatch):
    """기본값(true)은 현행 그대로 패시브 — 무회귀. 기본 상수도 함께 고정."""
    assert wifi_roam.DEFAULT_HOME_PASSIVE is True
    calls = []
    home = [apln(0, 36, -70, CUR, "Net")]
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", _fake_iw(home, None, calls))
    monkeypatch.setattr(wifi_roam, "get_latest_scan", lambda *a, **k: ([], None))
    wifi_roam.staged_scan_best_candidate(
        _station(rssi=-70), None, ["Net"], "Net", STABLE, None
    )
    assert calls and calls[0]["passive"] is True and calls[0]["ssids"] is None


def test_home_passive_config_applies():
    """`.roaming.STAGED_SCAN.home_passive` 가 전역에 반영(SIGHUP reload 동일 경로)."""
    wifi_roam.load_roaming_config(
        "mlan0", {"mlan0": {"roaming": {"STAGED_SCAN": {"home_passive": False}}}}
    )
    assert wifi_roam.HOME_PASSIVE is False
    wifi_roam.load_roaming_config(
        "mlan0", {"mlan0": {"roaming": {"STAGED_SCAN": {"home_passive": True}}}}
    )
    assert wifi_roam.HOME_PASSIVE is True


def test_home_passive_absent_keeps_default_invalid_coerced_to_bool():
    """키 부재는 기본값(true) 유지. 문자열 값은 parse_bool 규칙으로 해석되며 미인식
    문자열은 **False 로 강제**된다(기본값 유지 아님 — JSON boolean 리터럴 사용 전제,
    enable 등 기존 bool 키들과 동일 거동). 실거동을 명시 고정해 vacuous 통과를 막는다."""
    wifi_roam.load_roaming_config("mlan0", {"mlan0": {"roaming": {}}})
    assert wifi_roam.HOME_PASSIVE is wifi_roam.DEFAULT_HOME_PASSIVE
    wifi_roam.load_roaming_config(
        "mlan0", {"mlan0": {"roaming": {"STAGED_SCAN": {"home_passive": "yes-ish"}}}}
    )
    assert wifi_roam.HOME_PASSIVE is False  # parse_bool 미인식 문자열 → False (실거동 고정)


def test_home_passive_false_skip_guard_still_applies(monkeypatch):
    """home_passive=false + 단일채널 + 이웃 실견 → Stage 3 폴백 스킵 유지(총 스캔 1회).
    홈 액티브는 hidden 까지 커버하므로 Stage 3 는 완전 중복 — 스킵 정당성이 더 강하다."""
    monkeypatch.setattr(wifi_roam, "HOME_PASSIVE", False, raising=False)
    monkeypatch.setattr(wifi_roam, "WPA_FREQ", ["5240"])
    calls = []
    home = [apln(0, 48, -50, CUR, "Net"), apln(1, 48, -49, "bb:bb:bb:bb:bb:bb", "Net")]
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", _fake_iw(None, home, calls))
    monkeypatch.setattr(wifi_roam, "get_latest_scan", lambda *a, **k: ([], None))

    best, _, _, _ = wifi_roam.staged_scan_best_candidate(
        _station(rssi=-70, freq=5240), None, ["Net"], "Net", STABLE, None
    )
    assert best is None                          # diff 1 < 10 미달
    assert len(calls) == 1, f"홈 액티브 1회 외 추가 스캔 금지: {calls}"


def test_home_passive_false_baseline_unification(monkeypatch):
    """home_passive=false 에서도 baseline 통일(현재 AP RSSI 를 같은 스캔에서) 유지 —
    station dump(-70) 기준이면 diff 22 로 오로밍했을 상황을 홈 액티브 실측(-55)이 방지.
    단일채널로 두어 스킵 가드가 Stage 3 를 막게 하고, 총 1회 스캔으로 판정 완결을 함께 고정."""
    monkeypatch.setattr(wifi_roam, "HOME_PASSIVE", False, raising=False)
    monkeypatch.setattr(wifi_roam, "WPA_FREQ", ["5180"])
    calls = []
    home = [apln(0, 36, -55, CUR, "Net"), apln(1, 36, -48, "bb:bb:bb:bb:bb:bb", "Net")]
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", _fake_iw(None, home, calls))
    monkeypatch.setattr(wifi_roam, "get_latest_scan", lambda *a, **k: ([], None))

    best, _, _, _ = wifi_roam.staged_scan_best_candidate(
        _station(rssi=-70), None, ["Net"], "Net", STABLE, None
    )
    assert best is None                          # baseline=-55 → diff 7 < 10
    assert len(calls) == 1
