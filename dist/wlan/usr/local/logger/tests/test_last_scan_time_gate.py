"""LAST_SCAN_TIME(bgscan 타이머 리셋) 기록 게이트 테스트.

bgscan 은 /run/wifi/last_roam_scan_<iface> 이후 interval 전체를 다시 대기한다.
다중채널 directed active 또는 단일채널 home scan처럼 설정 채널 전체를 성공적으로
실측했을 때만 기록한다. 스캔 실패는 기록하지 않아 bgscan이 조기에 재개되게 한다."""
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
    monkeypatch.setattr(wifi_roam, "ENABLE_PREDICTIVE_ROAM", False)
    monkeypatch.setattr(wifi_roam, "ENABLE_STAGED_SCAN", True)
    monkeypatch.setattr(wifi_roam, "SKIP_REDUNDANT_ACTIVE_SCAN", True)
    monkeypatch.setattr(wifi_roam, "HOME_PASSIVE", True)
    scan_time_file = tmp_path / "last_roam_scan_time"
    monkeypatch.setattr(wifi_roam, "LAST_SCAN_TIME_FILE", str(scan_time_file))
    yield scan_time_file


def test_record_helper_writes_parseable_epoch(_globals):
    """[헬퍼 계약] 기록 값은 bgscan 이 float() 로 파싱하는 epoch — 공유 헬퍼 회귀망
    (staged/legacy 양 경로가 이 헬퍼를 쓴다; legacy 호출부는 메인루프라 유닛 관례상
    헬퍼 계약으로 커버)."""
    wifi_roam._record_roam_scan_time()
    assert _globals.exists()
    assert float(_globals.read_text()) > 0


def test_record_helper_failure_warns_once(tmp_path, monkeypatch):
    """쓰기 실패(디렉터리 생성 불가 등 중대 상태)는 침묵하지 않고 프로세스당 1회 warn —
    단, 신호 파일이라 동작은 계속(예외 전파 금지). 부모 경로가 파일인 지점으로 실패를
    주입한다 — makedirs 도입 후 절대경로 권한 의존(root 실행 CI에서 성공해버림) 제거."""
    blocker = tmp_path / "blocker"
    blocker.write_text("")
    monkeypatch.setattr(wifi_roam, "LAST_SCAN_TIME_FILE", str(blocker / "x"))
    monkeypatch.setattr(wifi_roam, "_SCAN_TIME_WRITE_WARNED", False, raising=False)
    wifi_roam.logger.reset_mock()
    wifi_roam._record_roam_scan_time()          # 예외 전파 없이 통과해야 함
    warns = [c for c in wifi_roam.logger.message.call_args_list if c.args[0] == "warn"]
    assert len(warns) == 1, "쓰기 실패가 침묵함(경고 미발행)"
    wifi_roam._record_roam_scan_time()
    warns = [c for c in wifi_roam.logger.message.call_args_list if c.args[0] == "warn"]
    assert len(warns) == 1, "경고가 매 호출 반복 발행됨(플러드)"


def _staged(station=None, allowed=("Net",)):
    return wifi_roam.staged_scan_best_candidate(
        station or _station(), list(allowed), "Net", STABLE, None
    )


def test_multichannel_active_success_records(_globals, monkeypatch):
    """다중채널은 전체 directed active 성공이므로 bgscan 타이머를 기록."""
    calls = []
    active = [apln(0, 36, -70, CUR, "Net"), apln(1, 40, -45, "bb:bb:bb:bb:bb:bb", "Net")]
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", _fake_iw(None, active, calls))
    best, _, _, scanned = _staged()
    assert best is not None and scanned is True
    assert len(calls) == 1 and calls[0]["passive"] is False
    assert _globals.exists(), "다중채널 전체 active 성공이 기록되지 않음"


def test_cache_is_ignored_and_active_failure_not_recorded(_globals, monkeypatch):
    """강한 cache 후보가 있어도 active 실패면 후보/타이머 기록 모두 없음."""
    calls = []
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", _fake_iw(None, None, calls))
    fresh_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cache = [{"bssid": "cc:cc:cc:cc:cc:cc", "ssid": "Net", "channel": 40, "freq": 5200,
              "rssi": -50, "rssi_th": -75, "ld": 0, "load": 0, "noise": -95,
              "timestamp": fresh_ts}]
    cache_reader = MagicMock(return_value=(cache, fresh_ts))
    monkeypatch.setattr(wifi_roam, "get_latest_scan", cache_reader)
    best, _, _, _ = _staged()
    assert best is None
    assert not _globals.exists()
    cache_reader.assert_not_called()


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


def test_single_channel_iw_failure_not_recorded(_globals, monkeypatch):
    """단일채널이라도 iw 스캔 자체가 실패(None)하면 기록 금지 — '결과 무관'은 AP 발견
    여부에 한하며, 스캔 실패는 커버리지가 아니다(`if home_lines:` 가드 회귀망)."""
    monkeypatch.setattr(wifi_roam, "WPA_FREQ", ["5240"])
    calls = []
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", _fake_iw(None, None, calls))
    monkeypatch.setattr(wifi_roam, "get_latest_scan", lambda *a, **k: ([], None))
    best, _, _, scanned = _staged(_station(freq=5240))
    assert best is None and scanned is True
    assert any(c["passive"] is False for c in calls), "스캔 실패 시 액티브 재시도는 실행돼야"
    assert not _globals.exists(), "스캔 실패가 커버리지로 기록됨"


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
