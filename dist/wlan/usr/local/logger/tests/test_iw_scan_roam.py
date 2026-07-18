"""로밍 판정 스캔의 iw 전환(wpa_cli scan_results 후보) 회귀 테스트.

근본원인: mlanutl setuserscan은 wpa_supplicant BSS 테이블을 채우지 않아 wpa_cli roam이
대상을 못 찾고 FAIL했다. iw scan은 테이블을 채우고, 후보를 scan_results(=테이블)에서
뽑으므로 roam 대상이 항상 테이블에 존재한다. 이 파일은 그 파이프라인을 고정한다.
"""
import sys
import os
from unittest.mock import MagicMock, patch

sys.modules.setdefault("sUTILS", MagicMock())
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wifi_roam
from wifi_roam import scan_results_to_ap_lines, iw_scan_to_ap_lines

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


# ---------- 라운드트립: scan_results → ap_lines → get_latest_scan ----------

def test_roundtrip_candidate_bssid_is_table_entry(tmp_path, monkeypatch):
    """scan_results에서 만든 ap.log를 get_latest_scan이 파싱해, 후보 bssid가
    scan_results(=wpa_supplicant 테이블) 항목과 정확히 일치함을 보장."""
    ap_log = tmp_path / "ap.log"
    monkeypatch.setattr(wifi_roam, "SCAN_LOG_FILE", str(ap_log))
    monkeypatch.setattr(wifi_roam, "WPA_FREQ", ["5180", "5200", "5220", "5240"])

    ap_lines = scan_results_to_ap_lines(SCAN_RESULTS)
    wifi_roam.save_with_timestamp(str(ap_log), ap_lines)

    entries, ts = wifi_roam.get_latest_scan({"ssid": "jhw_wlan_"}, None, ["jhw_wlan_"])
    # allowed=jhw_wlan_ + WPA_FREQ(5G 4채널) 필터 → jhw_wlan_(5180)만 후보.
    # 04:ba(다른 ssid), iptime5G(다른 ssid), aa:bb(2.4G freq밖)는 제외.
    assert [e["bssid"] for e in entries] == ["00:80:4c:c7:7d:dd"]
    e = entries[0]
    assert e["channel"] == 36 and e["freq"] == 5180 and e["rssi"] == -50
    assert e["ssid"] == "jhw_wlan_"


# ---------- iw_scan_to_ap_lines (오케스트레이션) ----------

def _dispatch(iw_rc=0, iw_err="", sr=SCAN_RESULTS, sr_rc=0):
    def side_effect(cmd, *a, **k):
        if cmd[0] == "iw":
            return _Run(iw_rc, "", iw_err)
        if "scan_results" in cmd:
            return _Run(sr_rc, sr)
        return _Run(0, "")
    return side_effect


def test_iw_scan_to_ap_lines_happy(monkeypatch):
    monkeypatch.setattr(wifi_roam.time, "sleep", lambda *_: None)
    with patch.object(wifi_roam.subprocess, "run", side_effect=_dispatch()):
        out = iw_scan_to_ap_lines("jhw_wlan_", ["5180", "5200"])
    assert out and out[0].split("|")[4] == "00:80:4c:c7:7d:dd"


def test_iw_scan_ebusy_retries_then_reads(monkeypatch):
    monkeypatch.setattr(wifi_roam.time, "sleep", lambda *_: None)
    calls = {"iw": 0}

    def side_effect(cmd, *a, **k):
        if cmd[0] == "iw":
            calls["iw"] += 1
            if calls["iw"] == 1:
                return _Run(240, "", "command failed: Device or resource busy (-16)")
            return _Run(0, "")
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
            return _Run(0, "")
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


def test_scan_results_nonzero_rc_returns_none(monkeypatch):
    """iw scan 성공했으나 wpa_cli scan_results가 비정상 종료 → None."""
    monkeypatch.setattr(wifi_roam.time, "sleep", lambda *_: None)

    def side_effect(cmd, *a, **k):
        if cmd[0] == "iw":
            return _Run(0, "")
        if "scan_results" in cmd:
            return _Run(1, "", "some error")
        return _Run(0, "")

    with patch.object(wifi_roam.subprocess, "run", side_effect=side_effect):
        out = iw_scan_to_ap_lines("jhw_wlan_", ["5180"])
    assert out is None
