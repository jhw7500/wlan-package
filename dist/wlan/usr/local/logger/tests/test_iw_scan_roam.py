"""로밍 판정 스캔의 iw 전환(wpa_cli scan_results 후보) 회귀 테스트.

근본원인: mlanutl setuserscan은 wpa_supplicant BSS 테이블을 채우지 않아 wpa_cli roam이
대상을 못 찾고 FAIL했다. iw scan은 테이블을 채우고, 후보를 scan_results(=테이블)에서
뽑으므로 roam 대상이 항상 테이블에 존재한다. 이 파일은 그 파이프라인을 고정한다.
"""
import sys
import os
import re
from unittest.mock import MagicMock, patch

sys.modules.setdefault("sUTILS", MagicMock())
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wifi_roam
import wifi_logger_scan
import passive_roam
from wifi_roam import (
    fresh_bssids_from_iw_scan,
    scan_results_to_ap_lines,
    iw_scan_to_ap_lines,
)

import pytest

wifi_roam.logger = MagicMock()

SCAN_RESULTS = (
    "bssid / frequency / signal level / flags / ssid\n"
    "00:80:4c:c7:7d:dd\t5180\t-50\t[WPA2-PSK-CCMP][ESS]\tjhw_wlan_\n"
    "04:ba:d6:ec:0b:08\t5200\t-48\t[WPA2-PSK-CCMP][ESS]\tjhw_wlan\n"       # 다른 SSID
    "58:86:94:d2:73:e8\t5200\t-47.5\t[WPA2-PSK-CCMP][ESS]\tiptime5G\n"     # float signal
    "aa:bb:cc:dd:ee:ff\t2412\t-60\t[ESS]\tjhw_wlan_\n"                      # freq 밖(2.4G)
    "garbage line no tabs\n"
)

IW_SCAN_DUMP = (
    "BSS 00:80:4c:c7:7d:dd(on mlan0) -- associated\n"
    "\tlast seen: 0 ms ago\n"
    "BSS 04:ba:d6:ec:0b:08(on mlan0)\n"
    "\tlast seen: 1 ms ago\n"
    "BSS 58:86:94:d2:73:e8(on mlan0)\n"
    "\tlast seen: 2 ms ago\n"
    "BSS aa:bb:cc:dd:ee:ff(on mlan0)\n"
    "\tlast seen: 3 ms ago\n"
)


@pytest.mark.parametrize("prefix", ["ap", "freq"])
def test_scan_logger_timestamp_has_no_brackets(prefix, tmp_path, monkeypatch):
    monkeypatch.setattr(wifi_logger_scan, "LOG_DIR", str(tmp_path))
    wifi_logger_scan.save_with_timestamp(prefix, ["sample"])

    header = (tmp_path / f"{prefix}.log").read_text().splitlines()[0]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", header)


@pytest.mark.parametrize(
    "header",
    ["2026-07-30 12:34:56", "[2026-07-30 12:34:56]"],
)
def test_passive_roam_accepts_plain_and_legacy_timestamp(header, tmp_path):
    scan_log = tmp_path / "ap.log"
    scan_log.write_text(
        f"{header}\n"
        "01|36|-50|0|00:11:22:33:44:55|cap|test-ssid\n"
    )

    aps = passive_roam.parse_last_scan_block(str(scan_log))
    assert len(aps) == 1
    assert aps[0]["bssid"] == "00:11:22:33:44:55"


class _Run:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# ---------- scan_results_to_ap_lines (순수 파서) ----------

def test_parser_header_and_malformed_skipped():
    out = scan_results_to_ap_lines(SCAN_RESULTS)
    # 헤더 + "garbage" 라인은 제외, 4개 유효 BSS만 변환
    assert len(out) == 4
    # 각 라인은 get_latest_scan 정규식(^\d{2}\|)과 7필드 만족
    for ln in out:
        parts = ln.split("|")
        assert len(parts) == 7
        assert parts[0].isdigit() and len(parts[0]) == 2


def test_parser_field_mapping_and_float_signal():
    out = scan_results_to_ap_lines(SCAN_RESULTS)
    first = out[0].split("|")
    assert first[1] == "36"                       # 5180 → ch36
    assert first[2] == "-50"                      # rssi
    assert first[4] == "00:80:4c:c7:7d:dd"        # bssid (콜론 유지)
    assert first[6] == "jhw_wlan_"                # ssid
    # float signal(-47.5) → int
    row3 = out[2].split("|")
    assert row3[2] == "-47"


def test_parser_empty_and_none():
    assert scan_results_to_ap_lines("") == []
    assert scan_results_to_ap_lines(None) == []


def test_iw_fresh_bssid_parser_filters_stale_and_malformed():
    dump = (
        "BSS 00:80:4c:c7:7d:dd(on mlan0) -- associated\n"
        "\tlast seen: 25 ms ago\n"
        "BSS 04:ba:d6:ec:0b:08(on mlan0)\n"
        "\tlast seen: 30000 ms ago\n"
        "BSS broken\n"
        "\tlast seen: 0 ms ago\n"
        "BSS 58:86:94:d2:73:e8(on mlan0)\n"
        "\tSSID: no-age-is-not-fresh\n"
    )
    assert fresh_bssids_from_iw_scan(dump, 1000) == {"00:80:4c:c7:7d:dd"}
    assert fresh_bssids_from_iw_scan(None, 1000) == set()
    assert fresh_bssids_from_iw_scan(dump, "bad") == set()


def test_scan_results_filter_keeps_only_fresh_bssids():
    out = scan_results_to_ap_lines(
        SCAN_RESULTS,
        fresh_bssids={"00:80:4C:C7:7D:DD", "58:86:94:d2:73:e8"},
    )
    assert [line.split("|")[4] for line in out] == [
        "00:80:4c:c7:7d:dd",
        "58:86:94:d2:73:e8",
    ]


# ---------- 라운드트립: scan_results → ap_lines → get_latest_scan ----------

def test_roundtrip_candidate_bssid_is_table_entry(tmp_path, monkeypatch):
    """scan_results에서 만든 ap.log를 get_latest_scan이 파싱해, 후보 bssid가
    scan_results(=wpa_supplicant 테이블) 항목과 정확히 일치함을 보장."""
    ap_log = tmp_path / "ap.log"
    monkeypatch.setattr(wifi_roam, "SCAN_LOG_FILE", str(ap_log))
    monkeypatch.setattr(wifi_roam, "WPA_FREQ", ["5180", "5200", "5220", "5240"])

    ap_lines = scan_results_to_ap_lines(SCAN_RESULTS)
    wifi_roam.save_with_timestamp(str(ap_log), ap_lines)
    header = ap_log.read_text().splitlines()[0]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", header)

    entries, ts = wifi_roam.get_latest_scan({"ssid": "jhw_wlan_"}, ["jhw_wlan_"])
    # allowed=jhw_wlan_ + WPA_FREQ(5G 4채널) 필터 → jhw_wlan_(5180)만 후보.
    # 04:ba(다른 ssid), iptime5G(다른 ssid), aa:bb(2.4G freq밖)는 제외.
    assert [e["bssid"] for e in entries] == ["00:80:4c:c7:7d:dd"]
    e = entries[0]
    assert e["channel"] == 36 and e["freq"] == 5180 and e["rssi"] == -50
    assert e["ssid"] == "jhw_wlan_"


def test_empty_wpa_freq_accepts_any_channel_same_ssid(tmp_path, monkeypatch):
    """scan_freq 미설정(WPA_FREQ 빈값)이면 동일 SSID AP를 채널 무관 후보로 허용한다
    — 동작주파수 제한 없이 운용하는 배포에서도 로밍 가능해야 한다. 기존엔 필터가
    `WPA_FREQ and freq in WPA_FREQ`라 빈 WPA_FREQ에서 후보가 0이 되어 로밍이 죽었다."""
    ap_log = tmp_path / "ap.log"
    monkeypatch.setattr(wifi_roam, "SCAN_LOG_FILE", str(ap_log))
    monkeypatch.setattr(wifi_roam, "WPA_FREQ", [])          # scan_freq 미설정(제한 없음)
    ap_lines = scan_results_to_ap_lines(SCAN_RESULTS)
    wifi_roam.save_with_timestamp(str(ap_log), ap_lines)

    entries, ts = wifi_roam.get_latest_scan({"ssid": "jhw_wlan_"}, ["jhw_wlan_"])
    # 동일 SSID(jhw_wlan_) 2개 모두 후보: 00:80(5180/ch36) + aa:bb(2412/ch1) — 채널 무관.
    # 04:ba(다른 ssid), iptime5G(다른 ssid)는 SSID 불일치로 여전히 제외.
    assert sorted(e["bssid"] for e in entries) == [
        "00:80:4c:c7:7d:dd", "aa:bb:cc:dd:ee:ff"
    ]


# ---------- iw_scan_to_ap_lines (오케스트레이션) ----------

def _dispatch(iw_rc=0, iw_err="", sr=SCAN_RESULTS, sr_rc=0):
    def side_effect(cmd, *a, **k):
        if cmd[0] == "iw":
            return _Run(iw_rc, IW_SCAN_DUMP if iw_rc == 0 else "", iw_err)
        if "scan_results" in cmd:
            return _Run(sr_rc, sr)
        return _Run(0, "")
    return side_effect


def test_iw_scan_to_ap_lines_happy(monkeypatch):
    monkeypatch.setattr(wifi_roam.time, "sleep", lambda *_: None)
    with patch.object(wifi_roam.subprocess, "run", side_effect=_dispatch()):
        out = iw_scan_to_ap_lines("jhw_wlan_", ["5180", "5200"])
    assert out and out[0].split("|")[4] == "00:80:4c:c7:7d:dd"


def test_iw_scan_drops_supplicant_bss_not_refreshed_by_this_scan(monkeypatch):
    """누적 scan_results의 강한 stale BSS가 이번 iw scan 결과로 승격되지 않는다."""
    monkeypatch.setattr(wifi_roam.time, "sleep", lambda *_: None)
    iw_dump = (
        "BSS 00:80:4c:c7:7d:dd(on mlan0) -- associated\n"
        "\tlast seen: 0 ms ago\n"
        "BSS 04:ba:d6:ec:0b:08(on mlan0)\n"
        "\tlast seen: 30000 ms ago\n"
    )

    def side_effect(cmd, *a, **k):
        if cmd[0] == "iw":
            return _Run(0, iw_dump)
        if "scan_results" in cmd:
            return _Run(0, SCAN_RESULTS)
        return _Run(0, "")

    with patch.object(wifi_roam.subprocess, "run", side_effect=side_effect):
        out = iw_scan_to_ap_lines("jhw_wlan_", ["5180", "5200"])

    assert [line.split("|")[4] for line in out] == ["00:80:4c:c7:7d:dd"]


def test_iw_scan_without_last_seen_metadata_fails_closed(monkeypatch):
    """freshness를 증명할 수 없으면 누적 supplicant table을 읽지 않고 판단을 건너뛴다."""
    monkeypatch.setattr(wifi_roam.time, "sleep", lambda *_: None)
    calls = {"sr": 0}

    def side_effect(cmd, *a, **k):
        if cmd[0] == "iw":
            return _Run(0, "BSS 00:80:4c:c7:7d:dd(on mlan0)\n\tSSID: jhw_wlan_\n")
        if "scan_results" in cmd:
            calls["sr"] += 1
            return _Run(0, SCAN_RESULTS)
        return _Run(0, "")

    with patch.object(wifi_roam.subprocess, "run", side_effect=side_effect):
        assert iw_scan_to_ap_lines("jhw_wlan_", ["5180"]) is None
    assert calls["sr"] == 0


def test_iw_scan_ebusy_retries_then_reads(monkeypatch):
    monkeypatch.setattr(wifi_roam.time, "sleep", lambda *_: None)
    calls = {"iw": 0}

    def side_effect(cmd, *a, **k):
        if cmd[0] == "iw":
            calls["iw"] += 1
            if calls["iw"] == 1:
                return _Run(240, "", "command failed: Device or resource busy (-16)")
            return _Run(0, IW_SCAN_DUMP)
        if "scan_results" in cmd:
            return _Run(0, SCAN_RESULTS)
        return _Run(0, "")

    with patch.object(wifi_roam.subprocess, "run", side_effect=side_effect):
        out = iw_scan_to_ap_lines(["jhw_wlan_"], ["5180"])
    assert calls["iw"] == 2          # -EBUSY 후 재시도
    assert out and len(out) == 4


def test_iw_scan_empty_results_returns_none(monkeypatch):
    monkeypatch.setattr(wifi_roam.time, "sleep", lambda *_: None)
    header_only = "bssid / frequency / signal level / flags / ssid\n"
    with patch.object(wifi_roam.subprocess, "run", side_effect=_dispatch(sr=header_only)):
        out = iw_scan_to_ap_lines("jhw_wlan_", ["5180"])
    assert out is None               # 후보 0 → None(호출측 backoff)


def test_iw_scan_directed_cmd_includes_freq_and_ssids(monkeypatch):
    monkeypatch.setattr(wifi_roam.time, "sleep", lambda *_: None)
    captured = {}

    def side_effect(cmd, *a, **k):
        if cmd[0] == "iw":
            captured["cmd"] = cmd
            return _Run(0, IW_SCAN_DUMP)
        if "scan_results" in cmd:
            return _Run(0, SCAN_RESULTS)
        return _Run(0, "")

    with patch.object(wifi_roam.subprocess, "run", side_effect=side_effect):
        iw_scan_to_ap_lines(["jhw_wlan_", "extra1"], ["5180", "5200"])
    c = captured["cmd"]
    assert c[:3] == ["iw", wifi_roam.IFACE, "scan"]
    assert "freq" in c and "5180" in c and "5200" in c
    # iw 문법 `ssid <값>*`: ssid 키워드는 1회, 뒤에 값(jhw_wlan_, extra1, "") 나열
    assert c.count("ssid") == 1
    i = c.index("ssid")
    assert c[i + 1:] == ["jhw_wlan_", "extra1", ""]


def test_iw_scan_all_ebusy_returns_none(monkeypatch):
    """iw scan 3회 모두 -EBUSY → 스테일 테이블 폴백 대신 None(호출측 backoff)."""
    monkeypatch.setattr(wifi_roam.time, "sleep", lambda *_: None)
    calls = {"iw": 0, "sr": 0}

    def side_effect(cmd, *a, **k):
        if cmd[0] == "iw":
            calls["iw"] += 1
            return _Run(240, "", "command failed: Device or resource busy (-16)")
        if "scan_results" in cmd:
            calls["sr"] += 1
            return _Run(0, SCAN_RESULTS)
        return _Run(0, "")

    with patch.object(wifi_roam.subprocess, "run", side_effect=side_effect):
        out = iw_scan_to_ap_lines("jhw_wlan_", ["5180"])
    assert out is None
    assert calls["iw"] == 3      # 3회 재시도 모두 실패
    assert calls["sr"] == 0      # 스캔 실패 → scan_results 조회조차 안 함


def test_iw_scan_caps_ssids_to_driver_max(monkeypatch):
    """directed SSID가 드라이버 max(10) 초과 시 cap + wildcard 보존(iw -EINVAL 스캔 전체 실패 방지)."""
    monkeypatch.setattr(wifi_roam.time, "sleep", lambda *_: None)
    captured = {}

    def side_effect(cmd, *a, **k):
        if cmd[0] == "iw":
            captured["cmd"] = cmd
            return _Run(0, IW_SCAN_DUMP)
        if "scan_results" in cmd:
            return _Run(0, SCAN_RESULTS)
        return _Run(0, "")

    many = [f"ssid{i}" for i in range(15)]  # 15 directed → +wildcard면 한계 초과
    with patch.object(wifi_roam.subprocess, "run", side_effect=side_effect):
        iw_scan_to_ap_lines(many, ["5180"])
    c = captured["cmd"]
    assert c.count("ssid") == 1
    values = c[c.index("ssid") + 1:]
    assert len(values) == wifi_roam.MAX_SCAN_SSIDS  # directed(max-1) + wildcard
    assert values[-1] == ""                          # wildcard 항상 보존
    n = wifi_roam.MAX_SCAN_SSIDS - 1
    assert values[:n] == many[:n]                    # live/base 우선(앞쪽) 유지


def test_iw_scan_no_cap_under_limit(monkeypatch):
    """한계 이하면 cap 없이 그대로(무회귀)."""
    monkeypatch.setattr(wifi_roam.time, "sleep", lambda *_: None)
    captured = {}

    def side_effect(cmd, *a, **k):
        if cmd[0] == "iw":
            captured["cmd"] = cmd
            return _Run(0, IW_SCAN_DUMP)
        if "scan_results" in cmd:
            return _Run(0, SCAN_RESULTS)
        return _Run(0, "")

    with patch.object(wifi_roam.subprocess, "run", side_effect=side_effect):
        iw_scan_to_ap_lines(["a", "b", "c"], ["5180"])
    values = captured["cmd"][captured["cmd"].index("ssid") + 1:]
    assert values == ["a", "b", "c", ""]


def test_iw_scan_exactly_at_limit_no_cap(monkeypatch):
    """경계값: directed (MAX-1)개 + wildcard = 정확히 MAX면 cap 미발생(`> MAX` 엄격부등호).
    `>`를 `>=`로 잘못 바꾸는 off-by-one 회귀를 잡는다."""
    monkeypatch.setattr(wifi_roam.time, "sleep", lambda *_: None)
    captured = {}

    def side_effect(cmd, *a, **k):
        if cmd[0] == "iw":
            captured["cmd"] = cmd
            return _Run(0, IW_SCAN_DUMP)
        if "scan_results" in cmd:
            return _Run(0, SCAN_RESULTS)
        return _Run(0, "")

    exact = [f"s{i}" for i in range(wifi_roam.MAX_SCAN_SSIDS - 1)]  # (MAX-1) directed +wildcard=정확히 MAX
    with patch.object(wifi_roam.subprocess, "run", side_effect=side_effect):
        iw_scan_to_ap_lines(exact, ["5180"])
    values = captured["cmd"][captured["cmd"].index("ssid") + 1:]
    assert len(values) == wifi_roam.MAX_SCAN_SSIDS  # 경계=cap 미발생
    assert values == exact + [""]                    # 전 directed 유지 + wildcard


def test_scan_results_nonzero_rc_returns_none(monkeypatch):
    """iw scan 성공했으나 wpa_cli scan_results가 비정상 종료 → None."""
    monkeypatch.setattr(wifi_roam.time, "sleep", lambda *_: None)

    def side_effect(cmd, *a, **k):
        if cmd[0] == "iw":
            return _Run(0, IW_SCAN_DUMP)
        if "scan_results" in cmd:
            return _Run(1, "", "some error")
        return _Run(0, "")

    with patch.object(wifi_roam.subprocess, "run", side_effect=side_effect):
        out = iw_scan_to_ap_lines("jhw_wlan_", ["5180"])
    assert out is None


def test_iw_scan_empty_freqs_omits_freq_arg_full_band(monkeypatch):
    """WPA_FREQ=[] (scan_freq 미설정)이면 iw scan 명령에 'freq' 인자가 없어야 한다
    (전체 대역 스캔). 게이트 #3(if WPA_SSID:)이 빈 freqs로 iw_scan_to_ap_lines를
    호출하는 경로의 회귀 방지 — freqs 있으면 'freq'가 있어 대조로 무회귀도 고정."""
    monkeypatch.setattr(wifi_roam.time, "sleep", lambda *_: None)
    cap = {}

    def side_effect(cmd, *a, **k):
        if cmd[0] == "iw":
            cap["cmd"] = list(cmd)
            return _Run(0, IW_SCAN_DUMP)
        if "scan_results" in cmd:
            return _Run(0, SCAN_RESULTS)
        return _Run(0, "")

    # 빈 freqs → freq 제한 없음(전체 대역)
    with patch.object(wifi_roam.subprocess, "run", side_effect=side_effect):
        iw_scan_to_ap_lines(["jhw_wlan_"], [])
    assert "freq" not in cap["cmd"]

    # 대조: freqs 지정 시 freq 인자 존재(스코프 스캔 무회귀)
    with patch.object(wifi_roam.subprocess, "run", side_effect=side_effect):
        iw_scan_to_ap_lines(["jhw_wlan_"], ["5180", "5200"])
    assert "freq" in cap["cmd"] and "5180" in cap["cmd"]
