"""로밍 판정 스캔 정책 테스트.

정책:
  - 단일 설정 채널이 현재 채널과 같으면 home passive 우선, 필요할 때만 active 재확인
  - 다중 채널은 설정 채널 전체 directed active 1회
  - bgscan/ap.log cache RSSI는 최종 후보 판정에 사용하지 않음
"""
import json
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("sUTILS", MagicMock())
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wifi_roam


wifi_roam.logger = MagicMock()

CUR = "aa:aa:aa:aa:aa:aa"
STABLE = "stable"


def apln(idx, ch, rssi, bssid, ssid, freq=None):
    if freq is None:
        freq = wifi_roam.channel_to_freq(ch)
    return f"{idx:02d}|{ch}|{rssi}|0|{bssid}|{freq}|{ssid}"


def _station(rssi=-70, freq=5180, ssid="Net"):
    return {"bssid": CUR, "ssid": ssid, "freq": freq, "rssi": rssi, "load": 0}


def _fake_iw(passive_ret, active_ret, calls):
    def fake(ssids, freqs, passive=False, include_wildcard=True):
        calls.append(
            {
                "ssids": ssids,
                "freqs": freqs,
                "passive": passive,
                "wildcard": include_wildcard,
            }
        )
        return passive_ret if passive else active_ret

    return fake


@pytest.fixture(autouse=True)
def _globals(monkeypatch, tmp_path):
    monkeypatch.setattr(wifi_roam, "LAST_SCAN_TIME_FILE", str(tmp_path / "last_scan"))
    monkeypatch.setattr(wifi_roam, "WPA_TH_2G", -75)
    monkeypatch.setattr(wifi_roam, "WPA_TH_5G", -75)
    monkeypatch.setattr(wifi_roam, "WPA_FREQ", ["5180", "5200"])
    monkeypatch.setattr(wifi_roam, "DIFF_TH", 10)
    monkeypatch.setattr(wifi_roam, "ENABLE_PREDICTIVE_ROAM", False)
    monkeypatch.setattr(wifi_roam, "ENABLE_STAGED_SCAN", True)
    monkeypatch.setattr(wifi_roam, "SKIP_REDUNDANT_ACTIVE_SCAN", True)
    monkeypatch.setattr(wifi_roam, "HOME_PASSIVE", True)


def test_multifreq_runs_one_directed_active_scan(monkeypatch):
    calls = []
    active = [
        apln(0, 36, -70, CUR, "Net"),
        apln(1, 40, -45, "bb:bb:bb:bb:bb:bb", "Net"),
    ]
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", _fake_iw(None, active, calls))
    cache = MagicMock(side_effect=AssertionError("multi-freq roam must not read ap.log cache"))
    monkeypatch.setattr(wifi_roam, "get_latest_scan", cache)

    best, _, _, scanned = wifi_roam.staged_scan_best_candidate(
        _station(), ["Net"], "Net", STABLE, None
    )

    assert best is not None and best["bssid"] == "bb:bb:bb:bb:bb:bb"
    assert scanned is True
    assert calls == [
        {
            "ssids": ["Net"],
            "freqs": ["5180", "5200"],
            "passive": False,
            "wildcard": False,
        }
    ]
    cache.assert_not_called()


def test_multifreq_cache_candidate_is_never_used(monkeypatch):
    """강한 cache 후보가 있어도 최신 active 결과가 없으면 후보 없음."""
    calls = []
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", _fake_iw(None, None, calls))
    cache = MagicMock(
        return_value=(
            [{"bssid": "cc:cc:cc:cc:cc:cc", "ssid": "Net", "rssi": -30}],
            "2099-01-01 00:00:00",
        )
    )
    monkeypatch.setattr(wifi_roam, "get_latest_scan", cache)

    best, _, _, scanned = wifi_roam.staged_scan_best_candidate(
        _station(), ["Net"], "Net", STABLE, None
    )

    assert best is None and scanned is True
    cache.assert_not_called()


def test_multifreq_uses_same_active_scan_for_baseline(monkeypatch):
    """station=-70이어도 active에서 현재=-55, 후보=-48이면 diff=7로 기각."""
    calls = []
    active = [
        apln(0, 36, -55, CUR, "Net"),
        apln(1, 40, -48, "bb:bb:bb:bb:bb:bb", "Net"),
    ]
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", _fake_iw(None, active, calls))

    best, _, _, _ = wifi_roam.staged_scan_best_candidate(
        _station(rssi=-70), ["Net"], "Net", STABLE, None
    )
    assert best is None


def test_multifreq_deduplicates_frequency_list(monkeypatch):
    monkeypatch.setattr(wifi_roam, "WPA_FREQ", ["5180", 5180, "5200"])
    calls = []
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", _fake_iw(None, None, calls))
    wifi_roam.staged_scan_best_candidate(_station(), ["Net"], "Net", STABLE, None)
    assert calls[0]["freqs"] == ["5180", "5200"]


def test_single_freq_home_candidate_shortcircuits(monkeypatch):
    monkeypatch.setattr(wifi_roam, "WPA_FREQ", ["5180"])
    calls = []
    home = [
        apln(0, 36, -68, CUR, "Net"),
        apln(1, 36, -45, "bb:bb:bb:bb:bb:bb", "Net"),
    ]
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", _fake_iw(home, None, calls))

    best, _, _, _ = wifi_roam.staged_scan_best_candidate(
        _station(), ["Net"], "Net", STABLE, None
    )
    assert best is not None and best["bssid"] == "bb:bb:bb:bb:bb:bb"
    assert len(calls) == 1 and calls[0]["passive"] is True


def test_single_freq_passive_baseline_blocks_spurious_roam(monkeypatch):
    monkeypatch.setattr(wifi_roam, "WPA_FREQ", ["5180"])
    calls = []
    home = [
        apln(0, 36, -55, CUR, "Net"),
        apln(1, 36, -48, "bb:bb:bb:bb:bb:bb", "Net"),
    ]
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", _fake_iw(home, None, calls))
    best, _, _, _ = wifi_roam.staged_scan_best_candidate(
        _station(rssi=-70), ["Net"], "Net", STABLE, None
    )
    assert best is None
    assert len(calls) == 1  # 이웃 beacon을 봤으므로 중복 active 생략


def test_single_freq_only_current_ap_falls_back_to_active(monkeypatch):
    monkeypatch.setattr(wifi_roam, "WPA_FREQ", ["5180"])
    calls = []
    home = [apln(0, 36, -70, CUR, "Net")]
    active = [
        apln(0, 36, -70, CUR, "Net"),
        apln(1, 36, -45, "bb:bb:bb:bb:bb:bb", "Net"),
    ]
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", _fake_iw(home, active, calls))

    best, _, _, _ = wifi_roam.staged_scan_best_candidate(
        _station(), ["Net"], "Net", STABLE, None
    )
    assert best is not None and best["bssid"] == "bb:bb:bb:bb:bb:bb"
    assert [c["passive"] for c in calls] == [True, False]
    assert calls[1]["wildcard"] is False


def test_single_freq_cache_is_ignored_even_when_passive_fails(monkeypatch):
    monkeypatch.setattr(wifi_roam, "WPA_FREQ", ["5180"])
    calls = []
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", _fake_iw(None, None, calls))
    cache = MagicMock(return_value=([{"bssid": "cc:cc:cc:cc:cc:cc", "rssi": -30}], "now"))
    monkeypatch.setattr(wifi_roam, "get_latest_scan", cache)

    best, _, _, _ = wifi_roam.staged_scan_best_candidate(
        _station(), ["Net"], "Net", STABLE, None
    )
    assert best is None
    assert [c["passive"] for c in calls] == [True, False]
    cache.assert_not_called()


def test_single_freq_home_passive_false_uses_one_directed_active(monkeypatch):
    monkeypatch.setattr(wifi_roam, "WPA_FREQ", ["5180"])
    monkeypatch.setattr(wifi_roam, "HOME_PASSIVE", False)
    calls = []
    active = [
        apln(0, 36, -70, CUR, "Net"),
        apln(1, 36, -45, "bb:bb:bb:bb:bb:bb", "Net"),
    ]
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", _fake_iw(None, active, calls))

    best, _, _, _ = wifi_roam.staged_scan_best_candidate(
        _station(), ["Net"], "Net", STABLE, None
    )
    assert best is not None
    assert calls == [
        {"ssids": ["Net"], "freqs": [5180], "passive": False, "wildcard": False}
    ]


@pytest.mark.parametrize("skip_redundant", [True, False])
def test_single_freq_home_active_no_candidate_is_not_repeated(
    monkeypatch, skip_redundant
):
    """home_passive=false의 첫 scan이 이미 active이므로 후보가 없어도 중복하지 않는다."""
    monkeypatch.setattr(wifi_roam, "WPA_FREQ", ["5180"])
    monkeypatch.setattr(wifi_roam, "HOME_PASSIVE", False)
    monkeypatch.setattr(wifi_roam, "SKIP_REDUNDANT_ACTIVE_SCAN", skip_redundant)
    calls = []
    only_current = [apln(0, 36, -70, CUR, "Net")]
    monkeypatch.setattr(
        wifi_roam, "iw_scan_to_ap_lines", _fake_iw(None, only_current, calls)
    )

    best, _, _, scanned = wifi_roam.staged_scan_best_candidate(
        _station(), ["Net"], "Net", STABLE, None
    )
    assert best is None
    assert scanned is True
    assert calls == [
        {"ssids": ["Net"], "freqs": [5180], "passive": False, "wildcard": False}
    ]


def test_single_config_freq_mismatch_uses_directed_active(monkeypatch):
    monkeypatch.setattr(wifi_roam, "WPA_FREQ", ["5200"])
    calls = []
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", _fake_iw(None, None, calls))
    wifi_roam.staged_scan_best_candidate(_station(freq=5180), ["Net"], "Net", STABLE, None)
    assert len(calls) == 1
    assert calls[0]["passive"] is False
    assert calls[0]["freqs"] == ["5200"]


def test_empty_scan_freq_uses_full_band_active_with_wildcard(monkeypatch):
    monkeypatch.setattr(wifi_roam, "WPA_FREQ", [])
    calls = []
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", _fake_iw(None, None, calls))
    wifi_roam.staged_scan_best_candidate(_station(), ["Net"], "Net", STABLE, None)
    assert calls == [
        {"ssids": ["Net"], "freqs": None, "passive": False, "wildcard": True}
    ]


def test_baseline_from_entries_found_and_fallback():
    entries = [{"bssid": CUR, "rssi": -55}]
    assert wifi_roam.baseline_from_entries(entries, CUR, -70) == -55
    assert wifi_roam.baseline_from_entries(entries, "ff:ff:ff:ff:ff:ff", -70) == -70


def test_filter_ap_lines_by_freq_handles_malformed_and_mixed_types():
    ch36 = apln(0, 36, -55, CUR, "Net", freq=5180)
    ch40 = apln(1, 40, -50, "bb:bb:bb:bb:bb:bb", "Net", freq=5200)
    malformed = ["bad", "00|36|-50|0|aa:bb:cc:dd:ee:ff|not-a-freq|Net"]

    assert wifi_roam.filter_ap_lines_by_freq([ch36, ch40] + malformed, "5180") == [ch36]
    assert wifi_roam.filter_ap_lines_by_freq(None, 5180) == []
    assert wifi_roam.filter_ap_lines_by_freq([ch36], None) == []


class _Run:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


_SR = (
    "bssid / frequency / signal level / flags / ssid\n"
    "bb:bb:bb:bb:bb:bb\t5180\t-45\t[WPA2-PSK-CCMP][ESS]\tNet\n"
)


def test_iw_passive_command_has_no_ssid(monkeypatch):
    monkeypatch.setattr(wifi_roam.time, "sleep", lambda *_: None)
    captured = {}

    def run(cmd, *args, **kwargs):
        if cmd[0] == "iw":
            captured["cmd"] = list(cmd)
            return _Run(0)
        return _Run(0, _SR)

    monkeypatch.setattr(wifi_roam.subprocess, "run", run)
    wifi_roam.iw_scan_to_ap_lines(None, [5180], passive=True)
    cmd = captured["cmd"]
    assert "ssid" not in cmd
    assert cmd[-1] == "passive"
    assert cmd.index("passive") > cmd.index("freq")


def test_iw_active_directed_has_no_wildcard(monkeypatch):
    monkeypatch.setattr(wifi_roam.time, "sleep", lambda *_: None)
    captured = {}

    def run(cmd, *args, **kwargs):
        if cmd[0] == "iw":
            captured["cmd"] = list(cmd)
            return _Run(0)
        return _Run(0, _SR)

    monkeypatch.setattr(wifi_roam.subprocess, "run", run)
    wifi_roam.iw_scan_to_ap_lines(["Net"], [5180, 5200], include_wildcard=False)
    cmd = captured["cmd"]
    assert "passive" not in cmd
    assert cmd[cmd.index("ssid") + 1 :] == ["Net"]


def test_staged_config_ignores_removed_cache_keys():
    """구버전 JSON의 제거된 cache 키가 살아나지 않고 유효 키만 반영된다."""
    wifi_roam.load_roaming_config(
        "mlan0",
        {
            "mlan0": {
                "roaming": {
                    "STAGED_SCAN": {
                        "enable": False,
                        "home_passive": False,
                        "skip_redundant_active": False,
                        "cache_fresh_sec": 90,
                        "self_induced_tail_sec": 25,
                    }
                }
            }
        },
    )
    assert wifi_roam.ENABLE_STAGED_SCAN is False
    assert wifi_roam.HOME_PASSIVE is False
    assert wifi_roam.SKIP_REDUNDANT_ACTIVE_SCAN is False
    assert not hasattr(wifi_roam, "CACHE_FRESH_SEC")
    assert not hasattr(wifi_roam, "SELF_INDUCED_TAIL_SEC")


def test_template_defaults_match_code():
    path = os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "..", "opt", "wlan", "config",
        "wifi_init_conf.json",
    )
    with open(path) as f:
        data = json.load(f)
    for iface in ("mlan0", "mlan1"):
        staged = data[iface]["roaming"]["STAGED_SCAN"]
        assert staged["enable"] is wifi_roam.DEFAULT_ENABLE_STAGED_SCAN
        assert staged["home_passive"] is wifi_roam.DEFAULT_HOME_PASSIVE
        assert staged["skip_redundant_active"] is wifi_roam.DEFAULT_SKIP_REDUNDANT_ACTIVE_SCAN
