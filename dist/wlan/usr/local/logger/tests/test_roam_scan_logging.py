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
import contextlib
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


# ── staged_scan 통합: ap.log cache가 로밍 판정/로그에 유입되지 않는지 검증 ──

CUR = "aa:aa:aa:aa:aa:aa"


@pytest.fixture
def staged(monkeypatch, tmp_path):
    monkeypatch.setattr(wifi_roam, "LAST_SCAN_TIME_FILE", str(tmp_path / "last_scan"))
    monkeypatch.setattr(wifi_roam, "WPA_TH_2G", -75)
    monkeypatch.setattr(wifi_roam, "WPA_TH_5G", -75)
    monkeypatch.setattr(wifi_roam, "WPA_FREQ", ["5180", "5200"])
    monkeypatch.setattr(wifi_roam, "DIFF_TH", 10)
    monkeypatch.setattr(wifi_roam, "ENABLE_PREDICTIVE_ROAM", False)
    monkeypatch.setattr(wifi_roam, "SKIP_REDUNDANT_ACTIVE_SCAN", True)
    monkeypatch.setattr(wifi_roam, "HOME_PASSIVE", True, raising=False)
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


def test_multifreq_active_scan_does_not_log_cache(staged):
    """다중채널에서는 cache를 읽지 않고 최신 active 결과만 [scan]으로 기록."""
    monkeypatch, tmp_path = staged
    _write_cache(tmp_path, monkeypatch, [apln(1, 40, -30, "cc:cc:cc:cc:cc:cc", "Net")])
    active = [apln(0, 36, -68, CUR, "Net"), apln(1, 40, -45, "bb:bb:bb:bb:bb:bb", "Net")]
    monkeypatch.setattr(
        wifi_roam, "iw_scan_to_ap_lines", lambda *a, **k: active if not k.get("passive") else None
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


def test_multifreq_cache_not_used_when_active_fails(staged):
    """active 실패 시 ap.log에 강한 후보가 있어도 선택하거나 로그하지 않는다."""
    monkeypatch, tmp_path = staged
    _write_cache(tmp_path, monkeypatch, [apln(1, 40, -30, "cc:cc:cc:cc:cc:cc", "Net")])
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines", lambda *a, **k: None)
    wifi_roam.logger.reset_mock()

    best, _r, _s, _sc = wifi_roam.staged_scan_best_candidate(
        _station(), ["Net"], "Net", "stable", None
    )
    assert best is None
    assert not any("[cache]" in t for t in _texts())


def test_multifreq_logs_only_active_entries(staged):
    """cache BSSID가 아니라 이번 active scan BSSID만 판정 로그에 남는다."""
    monkeypatch, tmp_path = staged
    _write_cache(
        tmp_path,
        monkeypatch,
        [
            apln(1, 36, -30, "dd:dd:dd:dd:dd:dd", "Net"),
            apln(2, 40, -30, "cc:cc:cc:cc:cc:cc", "Net"),
        ],
    )
    active = [apln(0, 36, -70, CUR, "Net"), apln(1, 40, -50, "ee:ee:ee:ee:ee:ee", "Net")]
    monkeypatch.setattr(
        wifi_roam, "iw_scan_to_ap_lines", lambda *a, **k: active
    )
    wifi_roam.logger.reset_mock()

    best, _r, _s, _sc = wifi_roam.staged_scan_best_candidate(
        _station(), ["Net"], "Net", "stable", None
    )
    texts = _texts()
    assert not any("[cache]" in t for t in texts)
    assert not any("dd:dd:dd:dd:dd:dd" in t or "cc:cc:cc:cc:cc:cc" in t for t in texts)
    assert best is not None and best["bssid"] == "ee:ee:ee:ee:ee:ee"


# --- ap.log 정렬 포맷 회귀 (생산자 = wifi_logger_scan 의 getscantable) ---
#
# 위 apln() 은 데몬 자신의 iw_scan_to_ap_lines 출력(패딩 없음)만 모델링한다. 그런데
# 이 파서는 get_latest_scan 을 통해 **ap.log** 도 먹고, 그 파일에는 wifi_logger_scan 이
# 쓴 정렬 포맷(구분자 뒤 패딩) 블록이 섞인다. 모듈 docstring 이 두 생산자를 이미
# 짚고 있는데도 픽스처가 한쪽만 만들어, ssid 컬럼만 strip 하지 않는 결함을 놓쳤다.
# (같은 결함이 passive_roam 에서 실기 발현 — `wifi 0 roam` 후보가 전부 탈락했다.)
def _padded_apln(idx, ch, rssi, bssid, cap, ssid):
    """타깃 ap.log 에서 그대로 옮긴 모양: `00| 044 | -48 | 015 | <bssid> | <cap> | <ssid>`"""
    return f"{idx:02d}| {ch:03d} | {rssi} | 015 | {bssid} | {cap} | {ssid}"


def test_padded_cache_lines_keep_multi_ssid_candidates(monkeypatch):
    """정렬 패딩이 남으면 extra_ssids 다중 망 후보가 통째로 탈락한다.

    allowed_set 은 `ssid in allowed_set` 으로 걸러지므로, ' Office' != 'Office' 가
    되는 순간 Mode A 의 cross-SSID 후보가 하나도 살아남지 못한다.
    """
    monkeypatch.setattr(wifi_roam, "WPA_FREQ", [])
    lines = [
        _padded_apln(0, 36, -50, "aa:bb:cc:dd:ee:ff", "I2DM   NAX", "Base"),
        _padded_apln(1, 36, -45, "11:22:33:44:55:66", "I2DM   N  ", "Office"),
    ]

    entries = wifi_roam.parse_scan_entries(
        lines, "2026-09-02 00:00:00", allowed_set={"Base", "Office"}, log=False
    )

    # RSSI 내림차순 — Office(-45) 가 앞
    assert [e["ssid"] for e in entries] == ["Office", "Base"]
    assert [e["bssid"] for e in entries] == [
        "11:22:33:44:55:66",
        "aa:bb:cc:dd:ee:ff",
    ]


# ── est_throughput 관측 로그 ─────────────────────────────────────────────
#
# 판정에는 쓰지 않는다. wpa_supplicant 의 est_throughput 은 측정값이 아니라 SNR + AP 가
# 광고한 능력으로 계산한 추정이고 AP 부하가 빠져 있으며(upstream scan.c 의
# "TODO: channel utilization and AP load"), 스캔 시점 값이라 age 가 수십 초다. 반면 이
# 데몬은 후보 RSSI 를 스캔 소요시간+1초(IW_SCAN_FRESH_SLACK_MS) 안으로 fail-closed 강제한다.
# 두 신선도가 양립하지 않아 가드로 쓰지 않고, 임계 근거를 만들기 위해 먼저 관측만 남긴다.

_BSS_OUT = (
    "bssid=aa:aa:aa:aa:aa:aa\nfreq=5220\nlevel=-52\nage=37\nssid=TEST\n"
    "snr=35\nest_throughput=432402\n"
    "bssid=bb:bb:bb:bb:bb:bb\nfreq=5180\nlevel=-50\nage=37\nssid=TEST\n"
    "snr=42\nest_throughput=65000\n"
)


def _run(stdout, rc=0):
    class R:
        returncode = rc
    R.stdout = stdout
    return R


def test_fetch_bss_metrics_parses_batch(monkeypatch):
    """RANGE=ALL 배치 응답을 BSSID -> {snr, est, age} 로 만든다 (호출 1회)."""
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return _run(_BSS_OUT)

    monkeypatch.setattr(wifi_roam.subprocess, "run", fake_run)
    m = wifi_roam.fetch_bss_metrics("mlan0")

    assert len(calls) == 1, "후보 수와 무관하게 1회만 호출한다"
    assert "RANGE=ALL" in calls[0] and "MASK=0x181286" in calls[0]
    assert m["aa:aa:aa:aa:aa:aa"] == {"snr": 35, "est": 432402, "age": 37}
    assert m["bb:bb:bb:bb:bb:bb"]["est"] == 65000


def test_fetch_bss_metrics_fail_open(monkeypatch):
    """wpa_cli 는 실패해도 exit 0 + 빈 출력이다. 예외/타임아웃 포함 전부 빈 dict."""
    monkeypatch.setattr(wifi_roam.subprocess, "run", lambda *a, **k: _run(""))
    assert wifi_roam.fetch_bss_metrics("mlan0") == {}

    def boom(*a, **k):
        raise OSError("no wpa_cli")

    monkeypatch.setattr(wifi_roam.subprocess, "run", boom)
    assert wifi_roam.fetch_bss_metrics("mlan0") == {}


def test_log_includes_current_row_and_metrics(monkeypatch):
    """현재 AP 행이 후보 행 앞에 오고, 양쪽 모두 snr/est 가 붙는다."""
    metrics = {
        "aa:aa:aa:aa:aa:aa": {"snr": 35, "est": 432402, "age": 37},
        "bb:bb:bb:bb:bb:bb": {"snr": 42, "est": 65000, "age": 37},
    }
    station = {
        "bssid": "aa:aa:aa:aa:aa:aa", "ssid": "TEST", "freq": 5220, "rssi": -52,
    }
    wifi_roam.parse_scan_entries(
        [apln(1, 36, -50, "bb:bb:bb:bb:bb:bb", "TEST")],
        "ts", {"TEST"}, src="scan", metrics=metrics, current=station,
    )
    texts = _texts()
    cur = [t for t in texts if "roam current:" in t]
    cand = [t for t in texts if "roam candidate" in t]

    assert len(cur) == 1 and len(cand) == 1
    assert texts.index(cur[0]) < texts.index(cand[0]), "현재 AP 행이 먼저 와야 비교가 쉽다"
    assert "snr=35" in cur[0] and "est=432402(age=37s)" in cur[0]
    assert "snr=42" in cand[0] and "est=65000(age=37s)" in cand[0]


def test_log_without_metrics_is_unchanged(monkeypatch):
    """metrics/current 가 없으면 기존 형식 그대로 — 기존 호출부가 깨지지 않는다."""
    wifi_roam.parse_scan_entries(
        [apln(1, 36, -50, "bb:bb:bb:bb:bb:bb", "TEST")], "ts", {"TEST"}, src="scan",
    )
    texts = _texts()
    assert not any("roam current:" in t for t in texts)
    cand = [t for t in texts if "roam candidate" in t]
    assert len(cand) == 1
    assert "est=" not in cand[0] and "snr=" not in cand[0]


def test_log_skips_missing_metric_entries(monkeypatch):
    """BSS 테이블에 없는 후보는 필드만 빠지고 행은 정상 출력된다(fail-open)."""
    station = {"bssid": "aa:aa:aa:aa:aa:aa", "ssid": "TEST", "freq": 5220, "rssi": -52}
    wifi_roam.parse_scan_entries(
        [apln(1, 36, -50, "bb:bb:bb:bb:bb:bb", "TEST")],
        "ts", {"TEST"}, src="scan", metrics={}, current=station,
    )
    cand = [t for t in _texts() if "roam candidate" in t]
    assert len(cand) == 1 and "est=" not in cand[0]


def test_mask_mismatch_warns_once(monkeypatch):
    """마스크 비트가 supplicant 와 어긋나 snr/est 가 안 오면 원인을 한 번 남긴다.

    비트 번호는 wpa_supplicant 버전에 따라 달라질 수 있고(정본은 타겟의
    src/common/wpa_ctrl.h), 관측 기능이라 fail-open 이라서 그냥 두면 값이 조용히
    빈다. 동작은 그대로 두되 로그로만 알린다."""
    monkeypatch.setattr(wifi_roam, "_MASK_WARNED", False)
    # age 만 오는 응답 = 마스크가 통하지 않은 모양
    monkeypatch.setattr(
        wifi_roam.subprocess, "run",
        lambda *a, **k: _run("bssid=aa:aa:aa:aa:aa:aa\nage=12\n"),
    )
    wifi_roam.fetch_bss_metrics("mlan0")
    warns = [t for lv, t in _msgs() if lv == "warn" and "MASK=" in t]
    assert len(warns) == 1

    wifi_roam.logger.reset_mock()
    wifi_roam.fetch_bss_metrics("mlan0")
    assert not [t for lv, t in _msgs() if lv == "warn" and "MASK=" in t], "반복하지 않는다"


def test_mask_ok_does_not_warn(monkeypatch):
    """정상 응답에는 경고가 없다."""
    monkeypatch.setattr(wifi_roam, "_MASK_WARNED", False)
    monkeypatch.setattr(wifi_roam.subprocess, "run", lambda *a, **k: _run(_BSS_OUT))
    wifi_roam.fetch_bss_metrics("mlan0")
    assert not [t for lv, t in _msgs() if lv == "warn" and "MASK=" in t]


# ── 채널폭·규격 관측 로그 (#285) ──────────────────────────────────────────
#
# est_throughput 은 SNR 과 폭이 곱해진 값이라 폭을 역산할 수 없다. #285 가 판정에 쓰자고
# 제안한 것은 폭·세대인데, 출하 wpa_supplicant 의 WPA_BSS_MASK_* 에는 그 비트가 없어
# (BIT0~27 확인: /opt/sda imx93 2.11 및 워크스페이스 devtool-patched 사본 양쪽) `wpa_cli bss`
# 로는 원시 IE 를 직접 파싱해야 한다. 대신 `iw scan` stdout 은 fresh_bssids_from_iw_scan 이
# 이미 쓰고 있어 추가 명령 0 으로 같은 버퍼에서 뽑는다.
#
# 아래 픽스처는 **실기 캡처**다(2026-09-08, cts-wlan mlan0, iw 6.9). 네 분기를 모두 담는다:
#   04:..:08  HT(above) + VHT width=1 + HE      → bw=80  gen=he
#   5a:..:ea  HT(above), VHT 없음               → bw=40  gen=ht
#   bc:..:5f  HT(no secondary)                  → bw=20  gen=ht
#   04:..:00  VHT width=0(모호) → HT above 로 분해 → bw=40  gen=he
# 판정에 관계없는 장문 구간만 줄였고, 파서를 헷갈릴 수 있는 잡음(탭이 든 `Country:`,
# 같은 줄에 내용이 붙는 `RSN:`, `\t\tCapabilities:`)은 원문 그대로 남겼다.

_IW_SCAN_REAL = (
    'BSS 04:ba:d6:ec:0b:08(on mlan0) -- associated\n'
    '\tTSF: 238799231582 usec (2d, 18:19:59)\n'
    '\tfreq: 5220\n'
    '\tsignal: -51.00 dBm\n'
    '\tlast seen: 1 ms ago\n'
    '\tSSID: jhw_wlan_\n'
    '\tCountry: KR\tEnvironment: Indoor/Outdoor\n'
    '\tRSN:\t * Version: 1\n'
    '\tHT capabilities:\n'
    '\t\tCapabilities: 0x98f\n'
    '\tHT operation:\n'
    '\t\t * secondary channel offset: above\n'
    '\t\t * STA channel width: any\n'
    '\tVHT capabilities:\n'
    '\tVHT operation:\n'
    '\t\t * channel width: 1 (80 MHz)\n'
    '\t\t * center freq segment 1: 42\n'
    '\t\t * center freq segment 2: 0\n'
    '\tHE capabilities:\n'
    'BSS 5a:86:94:d3:73:ea(on mlan0)\n'
    '\tTSF: 238799231488 usec (2d, 18:19:59)\n'
    '\tfreq: 2412\n'
    '\tsignal: -32.00 dBm\n'
    '\tlast seen: 1 ms ago\n'
    '\tSSID: iptime_setup\n'
    '\tHT capabilities:\n'
    '\t\tCapabilities: 0x11ee\n'
    '\tHT operation:\n'
    '\t\t * secondary channel offset: above\n'
    '\t\t * STA channel width: any\n'
    'BSS bc:10:2f:9c:6b:5f(on mlan0)\n'
    '\tTSF: 238799231500 usec (2d, 18:19:59)\n'
    '\tfreq: 2412\n'
    '\tsignal: -56.00 dBm\n'
    '\tlast seen: 1 ms ago\n'
    '\tSSID: [system a/c]_E30AJT0233098J\n'
    '\tHT capabilities:\n'
    '\t\tCapabilities: 0x3c\n'
    '\tHT operation:\n'
    '\t\t * secondary channel offset: no secondary\n'
    '\t\t * STA channel width: 20 MHz\n'
    '\tRSN:\t * Version: 1\n'
    'BSS 04:ba:d6:ec:0b:00(on mlan0)\n'
    '\tTSF: 238799231503 usec (2d, 18:19:59)\n'
    '\tfreq: 2412\n'
    '\tsignal: -41.00 dBm\n'
    '\tlast seen: 1 ms ago\n'
    '\tSSID: jhw_wlan_2G\n'
    '\tRSN:\t * Version: 1\n'
    '\tHT capabilities:\n'
    '\t\tCapabilities: 0x198f\n'
    '\tHT operation:\n'
    '\t\t * secondary channel offset: above\n'
    '\t\t * STA channel width: any\n'
    '\tVHT capabilities:\n'
    '\tVHT operation:\n'
    '\t\t * channel width: 0 (20 or 40 MHz)\n'
    '\t\t * center freq segment 1: 3\n'
    '\t\t * center freq segment 2: 0\n'
    '\tHE capabilities:\n'
)


# 신선도 게이트 대조용. iw 는 이번 스캔이 들은 것뿐 아니라 커널 BSS 캐시 전체를 뱉는데,
# `last seen` 이 그 나이를 알려준다. 아래는 902 초 전 블록과 3 ms 전 블록을 함께 담는다.
_IW_SCAN_STALE = (
    "BSS 04:ba:d6:ec:0b:08(on mlan0)\n"
    "\tlast seen: 902345 ms ago\n"
    "\tfreq: 5220\n"
    "\tHT capabilities:\n"
    "\tHT operation:\n"
    "\t\t * secondary channel offset: above\n"
    "\tVHT capabilities:\n"
    "\tVHT operation:\n"
    "\t\t * channel width: 1 (80 MHz)\n"
    "\tHE capabilities:\n"
    "BSS bb:bb:bb:bb:bb:bb(on mlan0)\n"
    "\tlast seen: 3 ms ago\n"
    "\tfreq: 5180\n"
    "\tHT capabilities:\n"
    "\tHT operation:\n"
    "\t\t * secondary channel offset: no secondary\n"
)


def test_phy_caps_parses_real_iw_scan():
    """실기 캡처에서 네 분기(80/40/20/모호분해)를 모두 정확히 뽑는다.

    04:..:00 이 VHT width=0("20 or 40 MHz") 모호 케이스다 — HT secondary offset 으로
    분해해 40 이 되어야 한다. 그 분해를 지우면 bw 키가 **빠진다**(80 이 되지 않는다).
    아래 전체 dict 동일성 단언이 그 값까지 고정하므로 별도 테스트를 두지 않는다."""
    got = wifi_roam.phy_caps_from_iw_scan(_IW_SCAN_REAL)
    assert got == {
        "04:ba:d6:ec:0b:08": {"bw": "80", "gen": "he"},
        "5a:86:94:d3:73:ea": {"bw": "40", "gen": "ht"},
        "bc:10:2f:9c:6b:5f": {"bw": "20", "gen": "ht"},
        "04:ba:d6:ec:0b:00": {"bw": "40", "gen": "he"},
    }


def test_phy_caps_ignores_width_outside_vht_operation():
    """`* channel width:` 가 VHT operation 밖에 있으면 폭으로 쓰지 않는다.

    주의 — 이건 **현재 재현되는 충돌에 대한 방어가 아니다.** 설치된 iw 의 `channel width`
    템플릿 4종(`supported channel width` / `channel width trigger scan interval` /
    `STA channel width` / `channel width: %d (%s)`) 중 이 정규식에 매치되는 것은
    마지막 하나뿐이다 — 정규식이 `width` 바로 뒤 콜론을 요구하기 때문이다(실측,
    커밋 이력의 `scan.c:1622` 인용은 틀렸다). 섹션 추적은 **다른 버전·미래 출력**에 대한
    방어이고, 아래 입력은 그 가드를 직접 겨냥한 합성 입력이다. 가드를 지우면 빨개진다."""
    stray = (
        "BSS aa:bb:cc:dd:ee:ff(on mlan0)\n"
        "\tExtended capabilities:\n"
        "\t\t * channel width: 2 (160 MHz)\n"
        "\t\t * secondary channel offset: above\n"   # 섹션 밖 offset 도 무시돼야 한다
        "\tHT operation:\n"
        "\t\t * secondary channel offset: no secondary\n"
    )
    # capabilities IE 가 없으므로 gen 키 자체가 없어야 한다(추정하지 않는다).
    assert wifi_roam.phy_caps_from_iw_scan(stray) == {
        "aa:bb:cc:dd:ee:ff": {"bw": "20"}
    }, "VHT operation 밖의 channel width 는 무시하고 HT 로만 판정해야 한다"


def test_phy_caps_fail_open():
    """빈 입력·형식 붕괴는 빈 dict — 관측 실패가 로밍 판정을 막지 않는다."""
    assert wifi_roam.phy_caps_from_iw_scan("") == {}
    assert wifi_roam.phy_caps_from_iw_scan(None) == {}
    assert wifi_roam.phy_caps_from_iw_scan("쓰레기\n입력\n") == {}


def test_phy_caps_omits_unknown_rather_than_guessing():
    """근거가 없으면 키를 넣지 않는다 — 20MHz 로 가정하지 않는다."""
    bare = "BSS aa:bb:cc:dd:ee:ff(on mlan0)\n\tfreq: 2412\n\tsignal: -40.00 dBm\n"
    assert wifi_roam.phy_caps_from_iw_scan(bare) == {}


def test_metrics_suffix_appends_bw_and_gen():
    """로그 조각에 bw/gen 이 snr/est 뒤에 붙는다. est 와 달리 age 가 없다(정적 속성)."""
    metrics = {"aa:bb:cc:dd:ee:ff": {"snr": 35, "est": 432402, "age": 37}}
    phy = {"aa:bb:cc:dd:ee:ff": {"bw": "80", "gen": "he"}}
    s = wifi_roam._metrics_suffix(metrics, "AA:BB:CC:DD:EE:FF", phy=phy)
    assert s == ", snr=35, est=432402(age=37s), bw=80, gen=he"


def test_log_rows_carry_bw_and_gen(monkeypatch):
    """현재/후보 행 양쪽에 bw/gen 이 실린다 — #285 가 필요로 하는 최종 산출물."""
    monkeypatch.setattr(wifi_roam, "_LAST_PHY_CAPS", {
        "aa:aa:aa:aa:aa:aa": {"bw": "80", "gen": "he"},
        "bb:bb:bb:bb:bb:bb": {"bw": "20", "gen": "ht"},
    })
    station = {"bssid": "aa:aa:aa:aa:aa:aa", "ssid": "TEST", "freq": 5220, "rssi": -52}
    wifi_roam.parse_scan_entries(
        [apln(1, 36, -50, "bb:bb:bb:bb:bb:bb", "TEST")],
        "ts", {"TEST"}, src="scan", current=station,
    )
    texts = _texts()
    cur = [t for t in texts if "roam current:" in t][0]
    cand = [t for t in texts if "roam candidate" in t][0]
    assert "bw=80" in cur and "gen=he" in cur
    assert "bw=20" in cand and "gen=ht" in cand


def test_cache_rows_carry_no_bw_gen(monkeypatch):
    """phy 를 안 넘기는 경로(get_latest_scan 의 cache 행)엔 붙지 않는다.

    leaf 에서 전역을 암묵적으로 읽으면 snr/est 는 없는데 bw/gen 만 실리는 비대칭이
    생긴다. 전역을 **채워 둔 상태**로 확인해야 판별력이 있다 — 비어 있으면 앰비언트
    기본값을 되살려도 결과가 같아 뮤테이션이 안 잡힌다."""
    monkeypatch.setattr(wifi_roam, "_LAST_PHY_CAPS", {
        "aa:aa:aa:aa:aa:aa": {"bw": "160", "gen": "he"},
        "bb:bb:bb:bb:bb:bb": {"bw": "80", "gen": "vht"},
    })
    station = {"bssid": "aa:aa:aa:aa:aa:aa", "ssid": "TEST", "freq": 5220, "rssi": -52}
    wifi_roam.parse_scan_entries(
        [apln(1, 36, -50, "bb:bb:bb:bb:bb:bb", "TEST")],
        "ts", {"TEST"}, src="cache", current=station,
    )
    for t in _texts():
        if "roam current:" in t or "roam candidate" in t:
            assert "bw=" not in t and "gen=" not in t, f"cache 행에 폭이 샜다: {t}"


def test_wire_fills_last_phy_caps_from_the_scan_it_already_ran(monkeypatch):
    """프로덕션 배선 자체를 고정한다.

    이 테스트가 없으면 `_LAST_PHY_CAPS = phy_caps_from_iw_scan(...)` 두 줄을 지워도
    스위트가 전부 초록이었다 — 기능을 통째로 없애는 뮤테이션이 무검출이었다.
    아울러 **추가 subprocess 호출이 없다**(iw scan + wpa_cli scan_results 2회뿐)는
    것도 같이 고정한다."""
    calls = []
    scan_out = _IW_SCAN_REAL.replace("last seen: 1 ms ago", "last seen: 0 ms ago")

    class R:
        def __init__(self, code, out):
            self.returncode, self.stdout, self.stderr = code, out, ""

    def run(cmd, **kw):
        calls.append(cmd)
        if cmd[0] == "iw":
            return R(0, scan_out)
        return R(0, "bssid / frequency / signal / flags / ssid\n")

    monkeypatch.setattr(wifi_roam.time, "sleep", lambda *_a: None)
    monkeypatch.setattr(wifi_roam.subprocess, "run", run)
    monkeypatch.setattr(wifi_roam, "_LAST_PHY_CAPS", {})
    wifi_roam._iw_scan_to_ap_lines(None, [5220], passive=True)

    assert wifi_roam._LAST_PHY_CAPS.get("04:ba:d6:ec:0b:08") == {"bw": "80", "gen": "he"}, \
        f"배선이 캐시를 채우지 못했다: {wifi_roam._LAST_PHY_CAPS}"
    assert [c[0] for c in calls] == ["iw", "wpa_cli"], f"추가 명령이 생겼다: {calls}"
    # 배선이 신선 집합을 **실제로 넘기는지**까지 고정한다. 파서를 직접 호출하는 테스트는
    # allowed_bssids 를 스스로 주므로 이 결선을 검증하지 못한다(함수만 테스트하고 배선을 빼먹는 함정).
    calls.clear()
    scan_out2 = _IW_SCAN_STALE.replace("last seen: 3 ms ago", "last seen: 0 ms ago")
    monkeypatch.setattr(wifi_roam.subprocess, "run",
                        lambda cmd, **kw: (calls.append(cmd), R(0, scan_out2))[1]
                        if cmd[0] == "iw" else R(0, "bssid / frequency\n"))
    monkeypatch.setattr(wifi_roam, "_LAST_PHY_CAPS", {})
    wifi_roam._iw_scan_to_ap_lines(None, [5220], passive=True)
    assert "04:ba:d6:ec:0b:08" not in wifi_roam._LAST_PHY_CAPS, \
        f"902 초 전 블록이 배선을 통과했다: {wifi_roam._LAST_PHY_CAPS}"
    assert "bb:bb:bb:bb:bb:bb" in wifi_roam._LAST_PHY_CAPS


def test_scan_failure_clears_last_phy_caps(monkeypatch):
    """스캔 실패 시 직전 값이 남아 다음 행에 실리면 안 된다."""
    monkeypatch.setattr(wifi_roam.time, "sleep", lambda *_a: None)
    monkeypatch.setattr(wifi_roam, "_LAST_PHY_CAPS",
                        {"aa:aa:aa:aa:aa:aa": {"bw": "160", "gen": "he"}})

    def boom(cmd, **kw):
        raise wifi_roam.subprocess.TimeoutExpired(cmd, 15)

    monkeypatch.setattr(wifi_roam.subprocess, "run", boom)
    # 래퍼는 /run/wifi 락을 잡는다(테스트 환경엔 권한 없음). 락은 이 테스트의 대상이
    # 아니므로 획득된 것으로 대체한다 — 검증 대상은 실패 시 캐시 초기화다.
    monkeypatch.setattr(wifi_roam, "scan_transition_lock",
                        lambda _i: contextlib.nullcontext(True))
    assert wifi_roam.iw_scan_to_ap_lines(None, [5220], passive=True) is None
    assert wifi_roam._LAST_PHY_CAPS == {}, "실패했는데 직전 스캔 값이 남았다"


def test_unparsable_bss_header_does_not_attribute_to_previous_ap():
    """주소가 안 읽히는 BSS 헤더는 앞 AP 로 되돌아가지 않는다(형제와 동일).

    되돌아가면 그 AP 가 광고한 적 없는 폭을 지어낸다 — 이 변경이 만들려는 데이터셋에
    허위 값이 들어간다."""
    dump = (
        "BSS 00:11:22:33:44:55(on mlan0)\n"
        "\tHT capabilities:\n"
        "BSS garbage-not-a-mac\n"
        "\tVHT operation:\n"
        "\t\t * channel width: 2 (160 MHz)\n"
    )
    caps = wifi_roam.phy_caps_from_iw_scan(dump)
    assert caps == {"00:11:22:33:44:55": {"gen": "ht"}}, \
        f"파싱 불가 헤더 뒤의 행이 앞 AP 에 붙었다: {caps}"
    # 형제 파서와 같은 판정인지도 함께 고정한다.
    assert wifi_roam.fresh_bssids_from_iw_scan(dump, 5000) == set()


def test_phy_caps_covers_the_whole_advertised_width_table():
    """160 / 80+80 / HT below 는 실기 캡처에 없어 미검증 전사였다.

    폭 코드 표는 iw 6.9 scan.c:1519-1523 의 chandwidths[] 가 정본이다
    ({0:"20 or 40 MHz", 1:"80 MHz", 2:"160 MHz", 3:"80+80 MHz"}). 아래 스탠자는
    실기 캡처와 같은 형식으로 만든 합성 입력이고, 표를 잘못 옮기면 빨개진다."""
    def stanza(bssid, vht=None, ht_off=None, seg0=None, seg1=None):
        out = f"BSS {bssid}(on mlan0)\n\tHT capabilities:\n"
        if ht_off:
            out += f"\tHT operation:\n\t\t * secondary channel offset: {ht_off}\n"
        if vht is not None:
            name = {0: "20 or 40 MHz", 1: "80 MHz", 2: "160 MHz", 3: "80+80 MHz"}[vht]
            out += ("\tVHT capabilities:\n\tVHT operation:\n"
                    f"\t\t * channel width: {vht} ({name})\n")
            if seg0 is not None:
                out += f"\t\t * center freq segment 1: {seg0}\n"
            if seg1 is not None:
                out += f"\t\t * center freq segment 2: {seg1}\n"
        return out

    dump = (stanza("11:11:11:11:11:11", vht=2, ht_off="above")
            + stanza("22:22:22:22:22:22", vht=3, ht_off="above")
            + stanza("33:33:33:33:33:33", ht_off="below"))
    caps = wifi_roam.phy_caps_from_iw_scan(dump)
    assert caps["11:11:11:11:11:11"]["bw"] == "160"
    assert caps["22:22:22:22:22:22"]["bw"] == "80+80"
    assert caps["33:33:33:33:33:33"]["bw"] == "40", "HT secondary offset below 도 40 이다"
    # gen 사다리의 중간 단(11ac-only)은 실기 캡처에 없어 미검증이었다. #285 의 판정이
    # 곧 VHT80 대 HT20 구별이라, 이 단이 잘못 붙으면 대상 집단이 조용히 오염된다.
    assert caps["11:11:11:11:11:11"]["gen"] == "vht", "VHT capabilities 만 있으면 vht"
    assert caps["33:33:33:33:33:33"]["gen"] == "ht", "HT capabilities 만 있으면 ht"

    # revised signaling — 코드 1 은 80MHz 를 뜻하지 않는다. 802.11ac 에서 코드 2/3 은
    # 폐기되고 160·80+80 을 코드 1 + 두 center-frequency segment 간격으로 표현한다.
    # 정본: 출하 wpa_supplicant 의 get_vht_operation_channel_width
    # (src/common/ieee802_11_common.c) — seg1 이 있고 간격이 8 이면 160, 있으면 80+80.
    # 이 분해를 빼면 revised signaling 을 쓰는 넓은 AP 가 전부 80 으로 오분류된다.
    rev = (stanza("44:44:44:44:44:44", vht=1, ht_off="above", seg0=50, seg1=58)
           + stanza("55:55:55:55:55:55", vht=1, ht_off="above", seg0=42, seg1=106)
           + stanza("66:66:66:66:66:66", vht=1, ht_off="above", seg0=42, seg1=0))
    rc = wifi_roam.phy_caps_from_iw_scan(rev)
    assert rc["44:44:44:44:44:44"]["bw"] == "160", "간격 8 은 160 (구식 코드 2 와 동치)"
    assert rc["55:55:55:55:55:55"]["bw"] == "80+80", "간격 8 이 아니면 80+80"
    assert rc["66:66:66:66:66:66"]["bw"] == "80", "seg1 이 0 이면 80 (실기 캡처가 이 형태)"


def test_staged_scan_rows_carry_bw_and_gen(monkeypatch):
    """staged 호출자가 src="scan" 으로 넘겨 그 행에 폭이 실리는지 단언한다.

    로그 행 문자열 자체는 다른 테스트도 본다(호출지점은 하나다). 이 테스트가 고정하는
    것은 staged 경로가 그 호출지점에 도달한다는 것이다."""
    monkeypatch.setattr(wifi_roam, "_LAST_PHY_CAPS", {
        "aa:aa:aa:aa:aa:aa": {"bw": "80", "gen": "he"},
        "bb:bb:bb:bb:bb:bb": {"bw": "20", "gen": "ht"},
    })
    monkeypatch.setattr(wifi_roam, "WPA_FREQ", ["5180", "5220"])
    monkeypatch.setattr(wifi_roam, "iw_scan_to_ap_lines",
                        lambda *a, **k: [apln(0, 44, -52, "aa:aa:aa:aa:aa:aa", "TEST", 5220),
                                         apln(1, 36, -50, "bb:bb:bb:bb:bb:bb", "TEST", 5180)])
    monkeypatch.setattr(wifi_roam, "_record_roam_scan_time", lambda *a, **k: None)
    station = {"bssid": "aa:aa:aa:aa:aa:aa", "ssid": "TEST", "freq": 5220, "rssi": -52}
    wifi_roam.staged_scan_best_candidate(station, ["TEST"], "TEST", "stable", None)

    rows = [t for t in _texts() if "roam current:" in t or "roam candidate" in t]
    assert rows, f"로그 행이 없다: {_texts()}"
    assert any("bw=80" in t and "gen=he" in t for t in rows), \
        f"현재 AP 행에 폭이 없다 — 소비자 배선이 끊겼다: {rows}"
    assert any("bw=20" in t and "gen=ht" in t for t in rows), \
        f"후보 행에 폭이 없다: {rows}"


def test_legacy_scan_rows_carry_bw_and_gen(tmp_path, monkeypatch):
    """staged scan 을 끈 배포의 레거시 경로도 [scan] 행에 폭을 싣는다.

    이 경로는 자기 tick 이 돌린 스캔의 전경 실측인데도 phy 를 안 넘겨, 그 배포에서는
    데이터셋에 폭 컬럼이 통째로 비어 있었다. 반대로 [cache] 행은 계속 비어야 한다."""
    ap = tmp_path / "ap.log"
    ap.write_text("2026-09-08 13:00:00\n"
                  + apln(0, 36, -55, "bb:bb:bb:bb:bb:bb", "TEST", 5180) + "\n")
    monkeypatch.setattr(wifi_roam, "SCAN_LOG_FILE", str(ap))
    monkeypatch.setattr(wifi_roam, "_LAST_PHY_CAPS",
                        {"bb:bb:bb:bb:bb:bb": {"bw": "40", "gen": "vht"}})

    wifi_roam.get_latest_scan({"ssid": "TEST"}, ["TEST"], src="scan")
    scan_rows = [t for t in _texts() if "roam candidate" in t]
    assert scan_rows and "bw=40" in scan_rows[0] and "gen=vht" in scan_rows[0], \
        f"레거시 [scan] 행에 폭이 없다: {scan_rows}"

    wifi_roam.logger.reset_mock()
    wifi_roam.get_latest_scan({"ssid": "TEST"}, ["TEST"], src="cache")
    cache_rows = [t for t in _texts() if "roam candidate" in t]
    assert cache_rows and "bw=" not in cache_rows[0], \
        f"[cache] 행에 폭이 샜다: {cache_rows}"


def test_any_duplicate_bssid_block_drops_the_row():
    """한 덤프에 같은 BSSID 블록이 두 번 나오면 순서·내용과 무관하게 버린다.

    더 정교한 규칙을 두 번 시도했고 둘 다 뚫렸다 — 필드별 값 비교는 위조가 피해자와
    **다른 필드**를 세우면 통과했고, "두 번째 이후 블록의 새 증거만 불신"은 위조 블록이
    **먼저** 오면 통과했다. 아래 케이스가 그 우회들을 전부 덮는다.

    대가는 소거다(케이스 4·5) — 증거 없는 헤더 한 줄로 남의 행이 사라진다. 주입할 수
    있는 공격자는 언제든 중복을 만들 수 있어 못 막고, 임계 결정용 데이터셋에는 없는 값이
    틀린 값보다 낫다고 보아 감수한다."""
    real_ht20 = ("\tHT capabilities:\n"
                 "\tHT operation:\n"
                 "\t\t * secondary channel offset: no secondary\n")
    forged_vht160 = ("\tVHT capabilities:\n\tVHT operation:\n"
                     "\t\t * channel width: 2 (160 MHz)\n")
    B = "BSS 22:22:22:22:22:22(on mlan0)\n"
    other = "BSS 11:11:11:11:11:11(on mlan0)\n" + real_ht20

    # (1) 진짜 먼저 → 위조 나중. (2) 위조 먼저 → 진짜 나중(앞 규칙이 뚫렸던 순서).
    for name, dump in (
        ("genuine-first", other + B + real_ht20 + B + forged_vht160),
        ("forged-first",  other + B + forged_vht160 + B + real_ht20),
    ):
        caps = wifi_roam.phy_caps_from_iw_scan(dump)
        assert "22:22:22:22:22:22" not in caps, f"{name}: 위조가 살아남았다: {caps}"
        assert caps["11:11:11:11:11:11"] == {"bw": "20", "gen": "ht"}, \
            f"{name}: 중복 아닌 AP 까지 버려졌다"

    # (3) 교차필드 — 위조는 _vht_bw 만, 진짜는 _ht_off 만. capabilities 가 없어 gen 경로도
    #     관여하지 않으므로, 값 비교나 키 비교로는 잡히지 않는 형태다.
    cross = (B + "\tVHT operation:\n\t\t * channel width: 2 (160 MHz)\n"
             + B + "\tHT operation:\n\t\t * secondary channel offset: no secondary\n")
    assert wifi_roam.phy_caps_from_iw_scan(cross) == {}, "교차필드 위조가 통과했다"

    # (4) 값이 같은 정상 중복도 함께 버려진다 — 이게 감수한 대가다.
    assert wifi_roam.phy_caps_from_iw_scan(B + real_ht20 + B + real_ht20) == {}, \
        "정책상 정상 중복도 버려야 한다(위조와 구별할 수단이 없다)"

    # (5) 증거 없는 헤더 한 줄로도 지워진다 — 문서화된 소거 비용.
    assert wifi_roam.phy_caps_from_iw_scan(B + real_ht20 + B) == {}, \
        "증거 없는 중복 헤더도 같은 규칙을 받는다"


def test_oversized_width_value_does_not_raise():
    """자릿수 제한을 넘는 폭 값에 int() 가 던지면 로밍 데몬이 통째로 죽는다.

    관측 기능이 판정 경로를 멈춰선 안 된다 — 값만 버리고 계속한다."""
    huge = (
        "BSS 33:33:33:33:33:33(on mlan0)\n"
        "\tHT capabilities:\n"
        "\tHT operation:\n"
        "\t\t * secondary channel offset: above\n"
        "\tVHT operation:\n"
        "\t\t * channel width: " + "9" * 5000 + " (bogus)\n"
    )
    caps = wifi_roam.phy_caps_from_iw_scan(huge)
    assert caps == {"33:33:33:33:33:33": {"bw": "40", "gen": "ht"}}, \
        f"과대 값이 예외가 되거나 폭으로 채택됐다: {caps}"



def test_oversized_last_seen_does_not_kill_the_scan(monkeypatch):
    """형제 파서의 int() 도 같은 버퍼를 읽는다 — 한쪽만 막으면 아무것도 사지 못한다.

    이 함수는 로밍 판정 경로에서 불리고 위로 핸들러가 없어, 여기서 ValueError 가 나면
    데몬이 통째로 죽는다(systemd 가 되살려도 같은 비콘이 잡히는 동안 계속 재크래시)."""
    huge = ("BSS 44:44:44:44:44:44(on mlan0)\n"
            "\tlast seen: " + "9" * 5000 + " ms ago\n"
            "\tHT capabilities:\n")
    assert wifi_roam.fresh_bssids_from_iw_scan(huge, 2000) == set(), \
        "형식이 깨진 age 는 fail-closed 로 제외되어야 한다"

    calls = []

    class R:
        def __init__(self, code, out):
            self.returncode, self.stdout, self.stderr = code, out, ""

    def run(cmd, **kw):
        calls.append(cmd)
        return R(0, huge) if cmd[0] == "iw" else R(0, "bssid / frequency\n")

    monkeypatch.setattr(wifi_roam.time, "sleep", lambda *_a: None)
    monkeypatch.setattr(wifi_roam.subprocess, "run", run)
    # 예외가 나면 여기서 터진다. 신선한 BSS 가 없으니 None 반환이 정상이다.
    assert wifi_roam._iw_scan_to_ap_lines(None, [5220], passive=True) is None


