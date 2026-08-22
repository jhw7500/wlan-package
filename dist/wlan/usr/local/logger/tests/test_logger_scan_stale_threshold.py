"""`logger.bgscan_stale_threshold_sec` 실효화 테스트.

종전엔 wifi_bgscan 이 이 키를 로드만 하고 이후 미사용(dead knob)이었고, 실소비처인
wifi_logger_scan(beacon.json stale 엔트리 프루닝)은 600 을 하드코딩해 설정이 무효였다.
수정 후: wifi_logger_scan 이 키를 직접 로드(양의 int 만, 부재/불량은 기본 600 유지 —
fail-same)하고, wifi_bgscan 의 죽은 로드는 제거된다.

이후 이 키는 전역 logger 에서 인터페이스별(`<iface>.logger.*`)로 이관됐다 — 해석 순서는
`<iface>.logger` → `logger`(업그레이드 기기 잔존분) → 코드 기본값."""
import ast
import importlib.util
import json
import os
import sys
from datetime import datetime
from unittest.mock import MagicMock

sys.modules.setdefault("sUTILS", MagicMock())
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wifi_bgscan
import wifi_logger_scan

import pytest

wifi_logger_scan.logger = MagicMock()

_TMPL = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "..", "..", "opt", "wlan", "config", "wifi_init_conf.json",
)


def test_logger_scan_decodes_hex_ssid_identity_byte_exact(tmp_path):
    ssid = '  게스트 \\ " exact  '
    conf = tmp_path / "wpa.conf"
    conf.write_text(
        "freq_list=5180\n"
        "network={\n"
        f"    ssid={ssid.encode('utf-8').hex()}\n"
        "}\n"
    )
    parsed, _, _ = wifi_logger_scan.parse_wpa_supplicant_conf(str(conf))
    assert parsed == ssid


def test_loader_reads_config_value(tmp_path):
    p = tmp_path / "wic.json"
    p.write_text(json.dumps({"logger": {"bgscan_stale_threshold_sec": 300}}))
    assert wifi_logger_scan.load_stale_threshold(str(p)) == 300


def test_loader_defaults_on_missing_file():
    assert wifi_logger_scan.load_stale_threshold("/nonexistent/wic.json") == \
        wifi_logger_scan.DEFAULT_STALE_THRESHOLD_SEC


@pytest.mark.parametrize("bad", ["abc", 0, -5, True, None, 1.5])
def test_loader_defaults_on_invalid_value(tmp_path, bad):
    """양의 int 만 수용 — 불량 값(문자열/0/음수/bool/None/float)은 기본값 유지."""
    p = tmp_path / "wic.json"
    p.write_text(json.dumps({"logger": {"bgscan_stale_threshold_sec": bad}}))
    assert wifi_logger_scan.load_stale_threshold(str(p)) == \
        wifi_logger_scan.DEFAULT_STALE_THRESHOLD_SEC


@pytest.mark.parametrize("iface", ["mlan0", "mlan1"])
def test_code_default_matches_template(iface):
    """fail-same: 코드 기본 600 == 템플릿 <iface>.logger.bgscan_stale_threshold_sec.
    이 키는 전역 logger 에서 인터페이스별로 이관됐다 — 두 인터페이스 모두 고정한다."""
    with open(_TMPL) as f:
        tmpl_val = json.load(f)[iface]["logger"]["bgscan_stale_threshold_sec"]
    assert wifi_logger_scan.DEFAULT_STALE_THRESHOLD_SEC == tmpl_val


def test_template_has_no_global_key():
    """전역 logger 에 이 키가 되살아나면 '전역/인터페이스 두 곳에 값' 상태로 회귀한다 —
    인터페이스 스코프가 우선이라 전역 값은 조용히 무시되는 유령 노브가 된다."""
    with open(_TMPL) as f:
        assert "bgscan_stale_threshold_sec" not in json.load(f)["logger"]


def test_loader_prefers_iface_scope(tmp_path):
    """<iface>.logger 값이 전역 logger 값을 이긴다."""
    p = tmp_path / "wic.json"
    p.write_text(json.dumps({
        "logger": {"bgscan_stale_threshold_sec": 300},
        "mlan1": {"logger": {"bgscan_stale_threshold_sec": 900}},
    }))
    assert wifi_logger_scan.load_stale_threshold(str(p), iface="mlan1") == 900
    # 다른 iface 는 자기 블록이 없으므로 전역으로 폴백
    assert wifi_logger_scan.load_stale_threshold(str(p), iface="mlan0") == 300


def test_loader_falls_back_to_global_when_iface_key_absent(tmp_path):
    """[업그레이드 경로] 구 전역 키만 남은 기기에서도 값이 살아있어야 한다.

    정상 업그레이드에서는 postinst 의 migrate_retired_global_logger_keys 가 전역 키를
    per-iface 로 승격시킨 뒤 지우므로 전역 키는 남지 않는다. 이 폴백은 그 마이그레이션이
    돌지 못한 기기(jq 부재/마이그레이션 실패/구버전 postinst)용 안전망이다."""
    p = tmp_path / "wic.json"
    p.write_text(json.dumps({
        "logger": {"bgscan_stale_threshold_sec": 300},
        "mlan0": {"logger": {"link_interval_sec": 0.9}},
    }))
    assert wifi_logger_scan.load_stale_threshold(str(p), iface="mlan0") == 300


def test_loader_iface_none_reads_global_only(tmp_path):
    """iface 미지정(import 시점) 은 인터페이스 블록을 보지 않는다."""
    p = tmp_path / "wic.json"
    p.write_text(json.dumps({
        "logger": {"bgscan_stale_threshold_sec": 300},
        "mlan0": {"logger": {"bgscan_stale_threshold_sec": 900}},
    }))
    assert wifi_logger_scan.load_stale_threshold(str(p)) == 300


@pytest.mark.parametrize("bad", ["abc", 0, -5, True, 1.5])
def test_loader_invalid_iface_value_uses_default_not_global(tmp_path, bad):
    """iface 값이 불량이면 전역으로 조용히 새지 않고 코드 기본값 + 경고 — 운영자가
    고친 인터페이스 값이 무효인 사실이 전역값에 가려지면 진단이 불가능해진다."""
    p = tmp_path / "wic.json"
    p.write_text(json.dumps({
        "logger": {"bgscan_stale_threshold_sec": 300},
        "mlan0": {"logger": {"bgscan_stale_threshold_sec": bad}},
    }))
    assert wifi_logger_scan.load_stale_threshold(str(p), iface="mlan0") == \
        wifi_logger_scan.DEFAULT_STALE_THRESHOLD_SEC


def test_apply_iface_stale_threshold_rebinds_global(tmp_path, monkeypatch):
    """[와이어링] __main__ 이 IFACE 확정 후 호출하는 재해석이 모듈 전역
    STALE_THRESHOLD_SEC 를 **실제로** 갱신하는지 — '로더는 iface 를 받는데 전역은
    여전히 import 시점 전역값' 회귀(원래 dead knob 버그의 재발 형태)를 킬한다."""
    monkeypatch.setattr(wifi_logger_scan, "STALE_THRESHOLD_SEC", -1, raising=False)
    p = tmp_path / "wic.json"
    p.write_text(json.dumps({
        "logger": {"bgscan_stale_threshold_sec": 300},
        "mlan1": {"logger": {"bgscan_stale_threshold_sec": 900}},
    }))
    assert wifi_logger_scan.apply_iface_stale_threshold("mlan1", path=str(p)) == 900
    assert wifi_logger_scan.STALE_THRESHOLD_SEC == 900


def _main_block_statements():
    """`if __name__ == "__main__":` 본문을 AST 로 돌려준다.

    문자열 검색은 주석 처리된 호출도 통과시키고 실행 순서도 볼 수 없다 — 재해석 호출이
    블로킹 루프(main_loop) 뒤로 밀리면 영영 실행되지 않는데 grep 은 이를 잡지 못한다.
    """
    src_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "wifi_logger_scan.py"
    )
    with open(src_path) as f:
        tree = ast.parse(f.read())
    for node in tree.body:
        if not isinstance(node, ast.If):
            continue
        t = node.test
        if (
            isinstance(t, ast.Compare)
            and isinstance(t.left, ast.Name)
            and t.left.id == "__name__"
            and len(t.comparators) == 1
            and isinstance(t.comparators[0], ast.Constant)
            and t.comparators[0].value == "__main__"
        ):
            return node.body
    raise AssertionError('`if __name__ == "__main__":` 블록을 찾지 못함')


def _index_of_top_level_call(stmts, func_name):
    """본문 최상위에서 `func_name(...)` 호출문의 위치. 없으면 None."""
    for i, st in enumerate(stmts):
        if (
            isinstance(st, ast.Expr)
            and isinstance(st.value, ast.Call)
            and isinstance(st.value.func, ast.Name)
            and st.value.func.id == func_name
        ):
            return i
    return None


def test_main_calls_apply_iface_stale_threshold():
    """[와이어링] __main__ 에서 재해석 호출이 빠지면 위 함수가 있어도 실효 0 이다."""
    stmts = _main_block_statements()
    idx = _index_of_top_level_call(stmts, "apply_iface_stale_threshold")
    assert idx is not None, (
        "__main__ 에 apply_iface_stale_threshold(...) 호출문이 없다 — 로더가 iface 를 "
        "받아도 모듈 전역은 import 시점의 전역 스코프 값에 머문다"
    )
    call = stmts[idx].value
    assert [a.id for a in call.args if isinstance(a, ast.Name)] == ["IFACE"], (
        "재해석 호출이 IFACE 를 넘기지 않는다"
    )


def test_apply_iface_stale_threshold_runs_before_blocking_loop():
    """재해석이 main_loop() 뒤에 있으면 영영 실행되지 않는다(문자열 검색은 못 잡는 형태)."""
    stmts = _main_block_statements()
    apply_idx = _index_of_top_level_call(stmts, "apply_iface_stale_threshold")
    loop_idx = _index_of_top_level_call(stmts, "main_loop")
    assert apply_idx is not None, "재해석 호출이 없다"
    assert loop_idx is not None, "main_loop() 호출을 찾지 못함(이름 변경 시 테스트 갱신)"
    assert apply_idx < loop_idx, (
        f"재해석 호출(#{apply_idx})이 블로킹 루프(#{loop_idx}) 뒤에 있다 — 실행되지 않는다"
    )


def test_threshold_drives_beacon_pruning(monkeypatch):
    """[소비처 계약] STALE_THRESHOLD_SEC 가 remove_stale_entries 프루닝을 실제로 결정 —
    임계 100s 에서 50s 전 엔트리는 유지, 150s 전 엔트리는 제거."""
    monkeypatch.setattr(wifi_logger_scan, "STALE_THRESHOLD_SEC", 100)
    now = 1_700_000_000
    fmt = lambda ts: datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    db = {
        "aa:aa:aa:aa:aa:aa": {"date": fmt(now - 50)},
        "bb:bb:bb:bb:bb:bb": {"date": fmt(now - 150)},
    }
    out = wifi_logger_scan.remove_stale_entries(db, now)
    assert "aa:aa:aa:aa:aa:aa" in out
    assert "bb:bb:bb:bb:bb:bb" not in out


def test_loader_captures_warning_on_invalid_value(tmp_path, monkeypatch):
    """[가시성] 불량 값 폴백은 조용히 삼키지 않고 경고를 캡처한다(로깅은 logger 초기화
    후 __main__ 에서 1회 발행 — import 시점엔 logger 부재)."""
    monkeypatch.setattr(wifi_logger_scan, "_STALE_THRESHOLD_LOAD_WARNING", None, raising=False)
    p = tmp_path / "wic.json"
    p.write_text(json.dumps({"logger": {"bgscan_stale_threshold_sec": "abc"}}))
    assert wifi_logger_scan.load_stale_threshold(str(p)) == \
        wifi_logger_scan.DEFAULT_STALE_THRESHOLD_SEC
    assert wifi_logger_scan._STALE_THRESHOLD_LOAD_WARNING is not None


def test_loader_captures_warning_on_broken_json(tmp_path, monkeypatch):
    monkeypatch.setattr(wifi_logger_scan, "_STALE_THRESHOLD_LOAD_WARNING", None, raising=False)
    p = tmp_path / "wic.json"
    p.write_text("{broken json")
    assert wifi_logger_scan.load_stale_threshold(str(p)) == \
        wifi_logger_scan.DEFAULT_STALE_THRESHOLD_SEC
    assert wifi_logger_scan._STALE_THRESHOLD_LOAD_WARNING is not None


def test_loader_silent_on_missing_file_and_valid_value(tmp_path, monkeypatch):
    """파일 부재(신규/개발 환경 정상 폴백)와 정상 값은 경고 없음 — 경고 과발행 방지."""
    monkeypatch.setattr(wifi_logger_scan, "_STALE_THRESHOLD_LOAD_WARNING", None, raising=False)
    wifi_logger_scan.load_stale_threshold("/nonexistent/wic.json")
    assert wifi_logger_scan._STALE_THRESHOLD_LOAD_WARNING is None
    p = tmp_path / "wic.json"
    p.write_text(json.dumps({"logger": {"bgscan_stale_threshold_sec": 300}}))
    assert wifi_logger_scan.load_stale_threshold(str(p)) == 300
    assert wifi_logger_scan._STALE_THRESHOLD_LOAD_WARNING is None


def test_stale_threshold_wired_to_loader(tmp_path):
    """[와이어링] 모듈 전역 STALE_THRESHOLD_SEC 가 import 시 loader 를 **실제로 경유**해
    config 값을 받는지 검증 — 'loader 는 있는데 전역은 하드코딩' 회귀(= 원래 dead knob
    버그의 재발 형태)를 킬한다. 기본 경로 상수만 tmp config 로 치환한 사본을 신선 로드."""
    cfg = tmp_path / "wic.json"
    cfg.write_text(json.dumps({"logger": {"bgscan_stale_threshold_sec": 123}}))
    src_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "wifi_logger_scan.py")
    with open(src_path) as f:
        src = f.read()
    patched = src.replace(
        'WIFI_INIT_CONF_JSON = "/usr/local/etc/wifi_init_conf.json"',
        f'WIFI_INIT_CONF_JSON = "{cfg}"',
        1,
    )
    assert patched != src, "WIFI_INIT_CONF_JSON 상수 라인을 찾지 못함(경로 상수 변경 시 테스트 갱신)"
    mod_path = tmp_path / "wifi_logger_scan_wiring.py"
    mod_path.write_text(patched)
    spec = importlib.util.spec_from_file_location("wifi_logger_scan_wiring", str(mod_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.STALE_THRESHOLD_SEC == 123, \
        f"import 시 config 미반영(하드코딩 회귀): {mod.STALE_THRESHOLD_SEC}"


def test_bgscan_dead_load_removed():
    """wifi_bgscan 의 죽은 로드(로드만 하고 미사용) 제거 확인 — 소비처 단일화."""
    assert not hasattr(wifi_bgscan, "STALE_THRESHOLD_SEC"), \
        "wifi_bgscan 에 dead knob 로드가 잔존"
