"""roam_notify.build_payload / _parse_rssi 단위 테스트.

build_payload는 파일 I/O와 분리된 순수 함수이므로 fixture 없이 직접 검증한다.
roam_notify는 stdlib만 import하므로 stub 불필요.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from roam_notify import build_payload, _parse_rssi, _bssid_from_status


def _data(address="04:ba:d6:ec:0b:08", signal_avg="-66 dBm", signal=None,
          freq=5200, channel=40, noise=-96):
    """link.json dict를 조립. None을 주면 해당 키를 생략."""
    link = {}
    if address is not None:
        link["address"] = address
    if signal_avg is not None:
        link["signal_avg"] = signal_avg
    if signal is not None:
        link["signal"] = signal
    info = {}
    if freq is not None:
        info["freq"] = freq
    if channel is not None:
        info["channel"] = channel
    data = {"link": link, "info": info}
    if noise is not None and freq is not None:
        data["channel_info"] = {str(freq): {"noise": noise}}
    return data


# ---- _parse_rssi ----
def test_parse_rssi_dbm_string():
    assert _parse_rssi("-66 dBm") == -66


def test_parse_rssi_bracket_antenna():
    assert _parse_rssi("-66 [-68,-70] dBm") == -66


def test_parse_rssi_int_passthrough():
    assert _parse_rssi(-55) == -55


def test_parse_rssi_invalid_none():
    assert _parse_rssi(None) is None
    assert _parse_rssi("") is None
    assert _parse_rssi("garbage") is None


# ---- ap_mac 우선순위 (option A 핵심) ----
def test_ap_mac_prefers_to_bssid():
    # same-SSID: to_bssid가 목표 BSS → link.address와 달라도 to_bssid 사용
    p = build_payload(_data(address="04:ba:d6:ec:0b:08"), "mlan0", "", "aa:bb:cc:dd:ee:ff")
    assert p["ap_mac"] == "aa:bb:cc:dd:ee:ff"


def test_ap_mac_falls_back_to_link_address_when_to_bssid_empty():
    # cross-SSID: to_bssid="" → link.address(실 결합 BSS)
    p = build_payload(_data(address="04:ba:d6:ec:0b:08"), "mlan0", "", "")
    assert p["ap_mac"] == "04:ba:d6:ec:0b:08"


def test_ap_mac_none_when_no_source():
    assert build_payload(_data(address=None), "mlan0", "", "") is None


# ---- 미연결/무효 입력 → None ----
def test_not_associated_returns_none():
    assert build_payload({}, "mlan0", "", "x") is None            # link 없음
    assert build_payload({"link": "x"}, "mlan0", "", "y") is None  # link가 dict 아님
    assert build_payload("nope", "mlan0", "", "z") is None         # data가 dict 아님


# ---- 필드 매핑 ----
def test_full_payload_fields():
    p = build_payload(_data(signal_avg="-66 dBm", freq=5200, channel=40, noise=-96),
                      "mlan0", "00:11:22:33:44:55", "04:ba:d6:ec:0b:08")
    assert p["iface"] == "mlan0"
    assert p["ap_mac"] == "04:ba:d6:ec:0b:08"
    assert p["from"] == "00:11:22:33:44:55"
    assert p["rssi"] == -66
    assert p["channel"] == 40
    assert p["freq"] == 5200
    assert p["band"] == "5G"
    assert p["snr"] == -66 - (-96)   # rssi - noise = 30


def test_band_24g_below_5000():
    p = build_payload(_data(freq=2412, channel=1, noise=-95), "mlan0", "", "x")
    assert p["band"] == "2.4G"


def test_band_5g_at_threshold():
    p = build_payload(_data(freq=5000, channel=100, noise=-95), "mlan0", "", "x")
    assert p["band"] == "5G"


def test_snr_omitted_when_noise_zero():
    # noise=0 은 survey 미초기화(측정불가) → snr 생략
    p = build_payload(_data(freq=5200, noise=0), "mlan0", "", "x")
    assert "snr" not in p


def test_snr_omitted_when_noise_absent():
    p = build_payload(_data(freq=5200, noise=None), "mlan0", "", "x")
    assert "snr" not in p


def test_rssi_falls_back_to_signal():
    p = build_payload(_data(signal_avg=None, signal="-70 dBm"), "mlan0", "", "x")
    assert p["rssi"] == -70


def test_from_omitted_when_empty():
    p = build_payload(_data(), "mlan0", "", "04:ba:d6:ec:0b:08")
    assert "from" not in p


# ---- option D: 호출자 권위값(스캔) 우선 ----
def test_authoritative_channel_freq_rssi_override():
    # link.json엔 이전 AP(ch40/5200/-66)가 남아 있어도 호출자 스캔값(ch36/5180/-49)이 우선
    d = _data(freq=5200, channel=40, signal_avg="-66 dBm", noise=-96)
    p = build_payload(d, "mlan0", "", "04:ba:d6:ec:0b:08",
                      channel=36, freq=5180, rssi=-49)
    assert p["channel"] == 36 and p["freq"] == 5180 and p["band"] == "5G"
    assert p["rssi"] == -49


def test_channel_only_derives_freq():
    # passive_roam은 channel만 넘김 → freq 파생(ch36→5180)
    d = _data(freq=None, channel=None, noise=None)
    p = build_payload(d, "mlan0", "", "00:80:4c:c7:7d:dd", channel=36, rssi=-50)
    assert p["channel"] == 36 and p["freq"] == 5180 and p["band"] == "5G"


def test_channel_to_freq_helper():
    from roam_notify import _channel_to_freq
    assert _channel_to_freq(1) == 2412
    assert _channel_to_freq(36) == 5180
    assert _channel_to_freq(40) == 5200
    assert _channel_to_freq(149) == 5745
    assert _channel_to_freq(0) is None
    assert _channel_to_freq("x") is None


# ---- cross-SSID 실 결합 BSS 조회 (wpa_cli status 권위) ----
def test_bssid_from_status_completed():
    out = ("bssid=04:ba:d6:ec:0b:08\nfreq=5180\nssid=jhw_wlan\n"
           "wpa_state=COMPLETED\naddress=00:e0:4c:68:2b:1f\n")
    assert _bssid_from_status(out) == "04:ba:d6:ec:0b:08"


def test_bssid_from_status_not_completed():
    # 결합 미완료 시점의 bssid 줄은 신뢰하지 않는다
    assert _bssid_from_status("bssid=04:ba:d6:ec:0b:08\nwpa_state=SCANNING\n") is None
    assert _bssid_from_status("wpa_state=COMPLETED\n") is None   # bssid 줄 없음
    assert _bssid_from_status("") is None
    assert _bssid_from_status(None) is None


def _stub_status_run(stdout_text):
    class _R:
        stdout = stdout_text
    return lambda *a, **k: _R()


def test_get_associated_bssid_success(monkeypatch):
    import roam_notify
    monkeypatch.setattr(roam_notify.subprocess, "run",
                        _stub_status_run("bssid=aa:bb:cc:dd:ee:ff\nwpa_state=COMPLETED\n"))
    assert roam_notify.get_associated_bssid("mlan0", wait_s=0.1, poll_s=0.01) \
        == "aa:bb:cc:dd:ee:ff"


def test_get_associated_bssid_timeout_empty(monkeypatch):
    # COMPLETED 미도달 → wait_s 소진 후 "" (호출자는 link.address 폴백 = 무회귀)
    import roam_notify
    monkeypatch.setattr(roam_notify.subprocess, "run",
                        _stub_status_run("wpa_state=SCANNING\n"))
    assert roam_notify.get_associated_bssid("mlan0", wait_s=0.05, poll_s=0.01) == ""


def test_get_associated_bssid_exception_empty(monkeypatch):
    # wpa_cli 부재 등 예외에도 절대 raise 하지 않고 ""
    import roam_notify

    def _boom(*a, **k):
        raise OSError("no wpa_cli")

    monkeypatch.setattr(roam_notify.subprocess, "run", _boom)
    assert roam_notify.get_associated_bssid("mlan0", wait_s=0.05, poll_s=0.01) == ""
