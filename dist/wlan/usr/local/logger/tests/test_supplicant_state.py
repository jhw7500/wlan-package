"""wifi_logger_link.py 의 supplicant 상태 수집(Phase2a) 단위 테스트.

로거가 매 주기 wpa_cli status(+list_networks)를 폴링해 supplicant.json 에
{wpa_state, temp_disabled} 를 기록한다. SNMP(wifi_snmp_pp.py)가 이를 읽어
WIFInfoStaSupplicantState(invalid/success/failure/authenticating)로 매핑한다.
상태→MIB enum 매핑은 SNMP 쪽 책임이고, 여기서는 '사실 추출'만 검증한다.
"""

import json
import os
import sys
from unittest.mock import MagicMock

# sUTILS(paho.mqtt 의존)·curses 는 호스트에 없으므로 import 전에 stub.
sys.modules.setdefault("sUTILS", MagicMock())
sys.modules.setdefault("curses", MagicMock())

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wifi_logger_link as wl  # noqa: E402


# --- extract_supplicant: wpa_state 추출 -------------------------------------

def test_extract_state_completed():
    status = ("bssid=00:11:22:33:44:55\nfreq=5240\nssid=Wlan_5g\n"
              "wpa_state=COMPLETED\nip_address=192.168.1.50\n")
    assert wl.extract_supplicant(status) == {"wpa_state": "COMPLETED",
                                             "temp_disabled": False}


def test_extract_state_handshake():
    assert wl.extract_supplicant("wpa_state=4WAY_HANDSHAKE\n")["wpa_state"] == "4WAY_HANDSHAKE"


def test_extract_state_missing():
    assert wl.extract_supplicant("")["wpa_state"] == ""
    assert wl.extract_supplicant(None)["wpa_state"] == ""
    assert wl.extract_supplicant("bssid=x\nfreq=5240\n")["wpa_state"] == ""


# --- extract_supplicant: temp_disabled (list_networks flags) ---------------

def test_extract_temp_disabled_true():
    nets = ("network id / ssid / bssid / flags\n"
            "0\tWlan_5g\tany\t[TEMP-DISABLED]\n")
    out = wl.extract_supplicant("wpa_state=SCANNING\n", nets)
    assert out == {"wpa_state": "SCANNING", "temp_disabled": True}


def test_extract_temp_disabled_false_when_current():
    nets = ("network id / ssid / bssid / flags\n"
            "0\tWlan_5g\tany\t[CURRENT]\n")
    assert wl.extract_supplicant("wpa_state=COMPLETED\n", nets)["temp_disabled"] is False


def test_extract_temp_disabled_default_no_networks():
    assert wl.extract_supplicant("wpa_state=SCANNING\n")["temp_disabled"] is False


# --- write_supplicant_json: atomic 기록 -------------------------------------

def test_write_supplicant_json_roundtrip(tmp_path):
    supp = {"wpa_state": "COMPLETED", "temp_disabled": False}
    wl.write_supplicant_json(str(tmp_path), supp)
    with open(os.path.join(str(tmp_path), "supplicant.json"), encoding="utf-8") as f:
        assert json.load(f) == supp


def test_write_supplicant_json_no_temp_file_left(tmp_path):
    wl.write_supplicant_json(str(tmp_path), {"wpa_state": "", "temp_disabled": False})
    # atomic(tmp+rename) → .tmp 잔재 없어야 함
    assert os.listdir(str(tmp_path)) == ["supplicant.json"]


def test_write_supplicant_json_swallows_io_error(monkeypatch, tmp_path):
    # 보조 파일 기록 실패(부모 디렉터리 없음 등)가 주 link 로깅 루프를 죽이면 안 된다 —
    # 예외를 삼키고 로그만 남긴다(Gemini 리뷰 반영).
    monkeypatch.setattr(wl, "logger", MagicMock(), raising=False)
    bad_dir = os.path.join(str(tmp_path), "no", "such", "dir")  # 부모 부재 → open 실패
    wl.write_supplicant_json(bad_dir, {"wpa_state": "", "temp_disabled": False})  # raise 금지
    assert wl.logger.message.called


# --- poll_supplicant: list_networks 조건부 호출 분기 -------------------------

_LIST_CMD = ["wpa_cli", "-i", "mlan0", "list_networks"]


def test_poll_supplicant_skips_list_networks_when_completed(monkeypatch):
    # COMPLETED 면 temp-disable 판정 불필요 → list_networks 생략
    calls = []

    def fake_run(cmd):
        calls.append(cmd)
        return "ssid=X\nwpa_state=COMPLETED\n" if cmd[-1] == "status" else "UNEXPECTED"

    monkeypatch.setattr(wl, "run_command", fake_run)
    assert wl.poll_supplicant("mlan0") == {"wpa_state": "COMPLETED", "temp_disabled": False}
    assert _LIST_CMD not in calls


def test_poll_supplicant_skips_list_networks_when_status_empty(monkeypatch):
    # wpa 무응답(빈 status) → list_networks 무의미하므로 생략(G2 가드)
    calls = []

    def fake_run(cmd):
        calls.append(cmd)
        return ""

    monkeypatch.setattr(wl, "run_command", fake_run)
    assert wl.poll_supplicant("mlan0") == {"wpa_state": "", "temp_disabled": False}
    assert _LIST_CMD not in calls


def test_poll_supplicant_calls_list_networks_when_not_completed(monkeypatch):
    # 미연결/인증중(비COMPLETED) → list_networks 로 temp-disable 확인
    def fake_run(cmd):
        if cmd[-1] == "status":
            return "wpa_state=SCANNING\n"
        if cmd[-1] == "list_networks":
            return "network id / ssid / bssid / flags\n0\tX\tany\t[TEMP-DISABLED]\n"
        return ""

    monkeypatch.setattr(wl, "run_command", fake_run)
    assert wl.poll_supplicant("mlan0") == {"wpa_state": "SCANNING", "temp_disabled": True}
