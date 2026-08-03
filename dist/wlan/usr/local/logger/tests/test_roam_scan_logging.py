"""로밍 스캔 로깅 — 소스 라벨 / 캐시 지연 로깅 / 스캔 명령 기록.

배경(온타겟 실측 2026-07-29, cts-wlan):
  한 tick 에 AP 테이블이 1.15초 간격으로 두 벌 찍혀 운용자에게 중복으로 보였다. 실제로는
  Stage 0 배경 캐시(ap.log, 생산자=wifi_logger_scan 의 `mlanutl getscantable`)와 Stage 1
  홈채널 실측(`iw scan` → `wpa_cli scan_results`)이라는 **다른 두 소스**인데 포맷이 동일하고
  소스 표기가 없었다. 게다가 정상 경로(홈스캔 성공)에서 캐시 엔트리는 홈채널 필터로 전량
  제거돼 판정에 쓰이지도 않는다 — 즉 tick 당 11줄 중 4줄이 미사용 데이터였다.
  측정 비중: `:1506` 903줄(43%) + `:1544` 516줄(25%) = 전체 ROAM 로그의 68%.

또한 스캔 명령을 남기는 코드는 `mlanutl_scan` 안에만 있었는데 그 함수는 호출부 0건(사문,
setuserscan→iw scan 전환 때 호출부만 제거)이라, 실제로 도는 `_iw_scan_to_ap_lines` 는
성공은 물론 **실패 경로에서도 argv 를 남기지 않았다**.
"""
import sys
import os
from unittest.mock import MagicMock

sys.modules.setdefault("sUTILS", MagicMock())
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wifi_roam

import pytest

wifi_roam.logger = MagicMock()


def apln(idx, ch, rssi, bssid, ssid, freq=None):
    if freq is None:
        freq = wifi_roam.channel_to_freq(ch)
    return f"{idx:02d}|{ch}|{rssi}|0|{bssid}|{freq}|{ssid}"


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    wifi_roam.logger.reset_mock()
    monkeypatch.setattr(wifi_roam, "WPA_FREQ", ["5180"])


def _msgs():
    """logger.message 로 나간 (level, text) 목록."""
    out = []
    for call in wifi_roam.logger.message.call_args_list:
        args = call.args
        if len(args) >= 2:
            out.append((args[0], args[1]))
    return out


def _texts():
    return [t for _lv, t in _msgs()]


LINES = [
    apln(1, 36, -50, "bb:bb:bb:bb:bb:bb", "TEST"),
    apln(2, 36, -60, "cc:cc:cc:cc:cc:cc", "OTHER"),  # allowed_set 밖 → 후보 탈락
]


# ── parse_scan_entries: src 라벨 + log 스위치 ──


def test_default_logs_both_raw_and_candidates():
    """무회귀: 기본 호출은 종전대로 관측 행 + 후보 행을 모두 info 로 남긴다."""
    ent = wifi_roam.parse_scan_entries(LINES, "2026-07-29 13:00:00", {"TEST"})
    assert len(ent) == 1
    raw = [t for t in _texts() if "ssid:TEST" in t]
    cand = [t for t in _texts() if "roam candidate 0" in t]
    assert len(raw) == 1, "관측 행이 사라졌다"
    assert len(cand) == 1, "후보 행이 사라졌다"


def test_raw_row_kept_for_filtered_out_ssid():
    """필터 탈락 항목도 관측 행은 남는다 — '스캔엔 보였는데 왜 후보가 아닌가' 진단 근거."""
    wifi_roam.parse_scan_entries(LINES, "2026-07-29 13:00:00", {"TEST"})
    assert any("ssid:OTHER" in t for t in _texts()), "탈락 항목 관측 행이 사라지면 진단 불가"
    assert not any("roam candidate" in t and "OTHER" in t for t in _texts())


def test_src_label_present_on_both_rows():
    """[핵심] 소스 라벨이 관측 행·후보 행 양쪽에 붙는다 — 두 벌 출력을 구분하는 수단."""
    wifi_roam.parse_scan_entries(
        LINES, "2026-07-29 13:00:00", {"TEST"}, src="cache"
    )
    raw = [t for t in _texts() if "ssid:TEST" in t]
    cand = [t for t in _texts() if "roam candidate 0" in t]
    assert raw and "[cache]" in raw[0], f"관측 행에 src 라벨 없음: {raw}"
    assert cand and "[cache]" in cand[0], f"후보 행에 src 라벨 없음: {cand}"


def test_default_src_is_scan():
    """src 기본값은 scan — 실측 경로가 다수라 기본을 그쪽에 맞춘다."""
    wifi_roam.parse_scan_entries(LINES, "2026-07-29 13:00:00", {"TEST"})
    assert any("[scan]" in t for t in _texts())


def test_log_false_suppresses_all_rows_but_still_parses():
    """[핵심] log=False 는 로그만 끄고 파싱 결과는 그대로 — Stage 0 스냅샷용."""
    ent = wifi_roam.parse_scan_entries(
        LINES, "2026-07-29 13:00:00", {"TEST"}, src="cache", log=False
    )
    assert len(ent) == 1 and ent[0]["bssid"] == "bb:bb:bb:bb:bb:bb", "파싱까지 죽으면 회귀"
    assert not any("ssid:TEST" in t for t in _texts()), "log=False 인데 관측 행이 남았다"
    assert not any("roam candidate" in t for t in _texts()), "log=False 인데 후보 행이 남았다"


def test_log_scan_candidates_standalone():
    """호출자가 판정 시점에 직접 후보 행만 남길 수 있다(캐시 지연 로깅의 수단)."""
    ent = wifi_roam.parse_scan_entries(
        LINES, "2026-07-29 13:00:00", {"TEST"}, log=False
    )
    wifi_roam.logger.reset_mock()
    wifi_roam.log_scan_candidates(ent, "cache")
    cand = [t for t in _texts() if "roam candidate 0" in t]
    assert len(cand) == 1 and "[cache]" in cand[0]
    assert not any("ssid:TEST" in t and "roam candidate" not in t for t in _texts()), (
        "후보 행만 남겨야 하는데 관측 행까지 나왔다"
    )


# ── get_latest_scan: log 전달 ──


def test_get_latest_scan_passes_log_flag(monkeypatch, tmp_path):
    """get_latest_scan(log=False) 가 파서까지 전파된다 — Stage 0 이 조용해지는 근거."""
    ap = tmp_path / "ap.log"
    ap.write_text("[2026-07-29 13:00:00]\n" + "\n".join(LINES) + "\n")
    monkeypatch.setattr(wifi_roam, "SCAN_LOG_FILE", str(ap))
    ent, ts = wifi_roam.get_latest_scan({"ssid": "TEST"}, ["TEST"], log=False)
    assert len(ent) == 1 and ts == "2026-07-29 13:00:00", "파싱 결과가 바뀌면 회귀"
    assert not any("roam candidate" in t for t in _texts())


def test_get_latest_scan_defaults_to_logging(monkeypatch, tmp_path):
    """무회귀: 레거시 경로(비-staged)는 캐시가 곧 판정 입력이라 기본 로깅을 유지한다."""
    ap = tmp_path / "ap.log"
    ap.write_text("[2026-07-29 13:00:00]\n" + "\n".join(LINES) + "\n")
    monkeypatch.setattr(wifi_roam, "SCAN_LOG_FILE", str(ap))
    wifi_roam.get_latest_scan({"ssid": "TEST"}, ["TEST"])
    cand = [t for t in _texts() if "roam candidate 0" in t]
    assert len(cand) == 1 and "[cache]" in cand[0]


def test_get_latest_scan_src_override_for_foreground_read(monkeypatch, tmp_path):
    """[핵심] 레거시 비-staged 경로는 이번 tick 에 자기가 스캔해 ap.log 에 방금 쓴 블록을
    되읽는다(`:2989` save_with_timestamp → `:3005` get_latest_scan). src 를 넘기지 못하면
    **전경 실측이 [cache] 로 오라벨**돼, 소스 구분이 가장 필요한 폴백 모드에서 라벨이
    거짓이 된다. 파일 경로가 ap.log 라는 사실이 곧 '배경 캐시'를 뜻하지 않는다."""
    ap = tmp_path / "ap.log"
    ap.write_text("[2026-07-29 13:00:00]\n" + "\n".join(LINES) + "\n")
    monkeypatch.setattr(wifi_roam, "SCAN_LOG_FILE", str(ap))
    wifi_roam.get_latest_scan({"ssid": "TEST"}, ["TEST"], src="scan")
    cand = [t for t in _texts() if "roam candidate 0" in t]
    assert cand and "[scan]" in cand[0], f"전경 실측이 scan 으로 안 찍힌다: {cand}"
    assert not any("[cache]" in t for t in _texts()), "전경 실측이 cache 로 오라벨됐다"
    raw = [t for t in _texts() if "ssid:TEST" in t]
    assert raw and "[scan]" in raw[0], "관측 행에도 src 가 전파돼야 한다"


# ── _iw_scan_to_ap_lines: 스캔 명령 기록 ──


def _fake_run(recorder, rc=0, results=""):
    class R:
        def __init__(self, code, out):
            self.returncode = code
            self.stdout = out
            self.stderr = ""

    def run(cmd, **kw):
        recorder.append(cmd)
        if cmd[0] == "iw":
            return R(rc, "")
        return R(0, results)  # wpa_cli scan_results

    return run


def test_passive_scan_command_is_logged(monkeypatch):
    """[핵심] 패시브 스캔 argv 가 로그에 남는다 — 종전엔 성공 시 흔적 0."""
    monkeypatch.setattr(wifi_roam.time, "sleep", lambda *_a: None)
    monkeypatch.setattr(wifi_roam.subprocess, "run", _fake_run([]))
    wifi_roam._iw_scan_to_ap_lines(None, [5180], passive=True)
    hits = [t for t in _texts() if "'scan'" in t and "'passive'" in t]
    assert hits, f"패시브 스캔 명령이 로그에 없다: {_texts()}"
    assert "'freq'" in hits[0] and "'5180'" in hits[0], f"freq 가 빠졌다: {hits[0]}"


def test_directed_scan_command_shows_wildcard(monkeypatch):
    """와일드카드 probe(빈 문자열)가 보이게 list repr 로 남긴다 — join 이면 사라진다."""
    monkeypatch.setattr(wifi_roam.time, "sleep", lambda *_a: None)
    monkeypatch.setattr(wifi_roam.subprocess, "run", _fake_run([]))
    wifi_roam._iw_scan_to_ap_lines(["TEST"], [5180], include_wildcard=True)
    hits = [t for t in _texts() if "'scan'" in t and "'ssid'" in t]
    assert hits, "directed 스캔 명령이 로그에 없다"
    assert "'TEST'" in hits[0]
    assert "''" in hits[0], f"와일드카드 probe 가 안 보인다(join 회귀): {hits[0]}"


def test_scan_command_logged_once_per_call(monkeypatch):
    """재시도 루프 밖에 있어야 한다 — EBUSY 재시도마다 찍히면 볼륨 문제가 재발."""
    calls = []

    class R:
        returncode = 16
        stdout = ""
        stderr = "resource busy"

    monkeypatch.setattr(wifi_roam.time, "sleep", lambda *_a: None)
    monkeypatch.setattr(
        wifi_roam.subprocess, "run", lambda cmd, **kw: (calls.append(cmd), R())[1]
    )
    wifi_roam._iw_scan_to_ap_lines(None, [5180], passive=True)
    assert len(calls) == 3, "EBUSY 재시도 3회 전제가 깨졌다"
    cmd_logs = [t for t in _texts() if "'scan'" in t and "'passive'" in t]
    assert len(cmd_logs) == 1, f"호출 1회당 1줄이어야 하는데 {len(cmd_logs)}줄"


def test_scan_command_logged_even_on_failure(monkeypatch):
    """실패해도 명령이 남는다 — 종전엔 rc/stderr 만 남아 무엇을 실행했는지 복원 불가였다."""
    monkeypatch.setattr(wifi_roam.time, "sleep", lambda *_a: None)
    monkeypatch.setattr(wifi_roam.subprocess, "run", _fake_run([], rc=1))
    out = wifi_roam._iw_scan_to_ap_lines(None, [5180], passive=True)
    assert out is None, "실패 시 None 반환 계약이 깨졌다"
    assert any("'scan'" in t and "'passive'" in t for t in _texts())


# ── staged_scan 통합: Stage 0 침묵 / Stage 2 지연 로깅 ──
# 기존 test_staged_scan.py 는 get_latest_scan 을 통째로 mock 하므로 log 전달을 검증하지
# 못한다. 여기서는 **실제 ap.log 파일**을 두고 파서까지 돌려 tick 단위 로그 수를 센다.

CUR = "aa:aa:aa:aa:aa:aa"


@pytest.fixture
def staged(monkeypatch, tmp_path):
    monkeypatch.setattr(wifi_roam, "LAST_SCAN_TIME_FILE", str(tmp_path / "last_scan"))
    monkeypatch.setattr(wifi_roam, "WPA_TH_2G", -75)
    monkeypatch.setattr(wifi_roam, "WPA_TH_5G", -75)
    monkeypatch.setattr(wifi_roam, "WPA_FREQ", ["5180", "5200"])
    monkeypatch.setattr(wifi_roam, "DIFF_TH", 10)
    monkeypatch.setattr(wifi_roam, "ENABLE_PREDICTIVE_ROAM", False)
    monkeypatch.setattr(wifi_roam, "CACHE_FRESH_SEC", 45)
    monkeypatch.setattr(wifi_roam, "SELF_INDUCED_TAIL_SEC", 10)
    monkeypatch.setattr(wifi_roam, "SKIP_REDUNDANT_ACTIVE_SCAN", True)
    monkeypatch.setattr(wifi_roam, "HOME_PASSIVE", True, raising=False)
    monkeypatch.setattr(wifi_roam, "_LAST_SELF_SCAN_TS", None)
    monkeypatch.setattr(wifi_roam, "_LAST_SELF_SCAN_END_TS", None)
    monkeypatch.setattr(wifi_roam, "_LAST_WALL_TS", None, raising=False)
    monkeypatch.setattr(wifi_roam, "_LAST_MONO_TS", None, raising=False)
    return monkeypatch, tmp_path


def _write_cache(tmp_path, monkeypatch, lines, ts=None):
    from datetime import datetime

    ts = ts or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ap = tmp_path / "ap.log"
    ap.write_text(f"[{ts}]\n" + "\n".join(lines) + "\n")
    monkeypatch.setattr(wifi_roam, "SCAN_LOG_FILE", str(ap))
    return ts


def _station(rssi=-70, freq=5180, ssid="Net"):
    return {"bssid": CUR, "ssid": ssid, "freq": freq, "rssi": rssi, "load": 0}


def test_stage0_cache_silent_when_home_scan_succeeds(staged):
    """[핵심] 홈스캔이 후보를 찾으면 캐시는 판정에 안 쓰이므로 로그도 안 남는다.

    종전엔 이 경로에서 캐시 관측·후보 행이 매 tick 찍혀(실측 tick당 4줄) 같은 테이블이
    두 번 나온 것처럼 보였다."""
    monkeypatch, tmp_path = staged
    # 캐시에는 교차채널(ch40) 후보가 들어 있다 — 그런데도 안 찍혀야 한다.
    _write_cache(tmp_path, monkeypatch, [apln(1, 40, -50, "cc:cc:cc:cc:cc:cc", "Net")])
    home = [apln(0, 36, -68, CUR, "Net"), apln(1, 36, -45, "bb:bb:bb:bb:bb:bb", "Net")]
    monkeypatch.setattr(
        wifi_roam, "iw_scan_to_ap_lines", lambda *a, **k: home if k.get("passive") else None
    )
    wifi_roam.logger.reset_mock()

    best, _r, _s, _sc = wifi_roam.staged_scan_best_candidate(
        _station(), ["Net"], "Net", "stable", None
    )
    assert best is not None and best["bssid"] == "bb:bb:bb:bb:bb:bb"
    assert not any("[cache]" in t for t in _texts()), (
        f"판정에 안 쓰인 캐시가 로그를 채웠다: {[t for t in _texts() if '[cache]' in t]}"
    )
    assert any("[scan]" in t for t in _texts()), "실측 스캔 로그까지 사라지면 회귀"


def test_stage2_cache_logged_when_actually_used(staged):
    """[핵심] 캐시가 실제 판정 입력이 되는 tick 에서는 [cache] 후보 행이 남는다 —
    지연 로깅이 '정보를 없애는' 변경이 아님을 고정."""
    monkeypatch, tmp_path = staged
    _write_cache(tmp_path, monkeypatch, [apln(1, 40, -50, "cc:cc:cc:cc:cc:cc", "Net")])
    home = [apln(0, 36, -70, CUR, "Net")]  # 현재 AP만 → 홈 후보 없음
    monkeypatch.setattr(
        wifi_roam, "iw_scan_to_ap_lines", lambda *a, **k: home if k.get("passive") else None
    )
    wifi_roam.logger.reset_mock()

    best, _r, _s, _sc = wifi_roam.staged_scan_best_candidate(
        _station(), ["Net"], "Net", "stable", None
    )
    assert best is not None and best["bssid"] == "cc:cc:cc:cc:cc:cc", "캐시 후보 채택 경로가 깨졌다"
    cache_cand = [t for t in _texts() if "[cache]" in t and "roam candidate" in t]
    assert cache_cand, f"캐시를 썼는데 근거 로그가 없다: {_texts()}"


def test_stage2_logs_only_entries_actually_evaluated(staged):
    """캐시 로그는 홈채널 필터를 거친 stage2_entries 와 일치해야 한다 — 판정 입력과
    로그가 어긋나면 진단이 틀어진다(홈채널 캐시 엔트리는 Stage 1 실측이 대체)."""
    monkeypatch, tmp_path = staged
    _write_cache(
        tmp_path,
        monkeypatch,
        [
            apln(1, 36, -40, "dd:dd:dd:dd:dd:dd", "Net"),  # 홈채널(5180) → 필터로 제외
            apln(2, 40, -50, "cc:cc:cc:cc:cc:cc", "Net"),  # 교차채널 → 평가 대상
        ],
    )
    home = [apln(0, 36, -70, CUR, "Net")]
    monkeypatch.setattr(
        wifi_roam, "iw_scan_to_ap_lines", lambda *a, **k: home if k.get("passive") else None
    )
    wifi_roam.logger.reset_mock()

    best, _r, _s, _sc = wifi_roam.staged_scan_best_candidate(
        _station(), ["Net"], "Net", "stable", None
    )
    cache_logs = [t for t in _texts() if "[cache]" in t]
    assert not any("dd:dd:dd:dd:dd:dd" in t for t in cache_logs), (
        "홈채널 캐시 엔트리는 평가에서 빠지는데 로그에는 남았다(로그≠판정입력)"
    )
    assert best is not None and best["bssid"] == "cc:cc:cc:cc:cc:cc"
