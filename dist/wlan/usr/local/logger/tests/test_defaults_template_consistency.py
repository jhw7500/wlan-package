"""코드 기본값(폴백)과 템플릿 wifi_init_conf.json(제품 의도)의 일치를 고정한다 — fail-same.

코드 기본값은 JSON 로드 실패/키 부재 시의 폴백이다. 템플릿과 다르면 JSON 손상 같은
실패 상태에서 기능 enable 이 뒤집히는 비대칭(fail-different)이 생긴다 — 실측 사례:
과거 실험 기능 4종(PREDICTIVE/LOAD/ADAPTIVE/POST_ROAM_ARP)은 템플릿이 false 로 끄는데
코드 기본이 True 라 JSON 이 깨지면 일제히 켜졌다 — 이 원칙의 기원. LOAD/ADAPTIVE/
POST_ROAM_ARP 는 감사 D1(2026-07-31)로 제거됐고 PREDICTIVE 만 남았다. 로밍 기본값은
mlan0/mlan1 템플릿이 정렬돼 있어 공통 코드 폴백을 사용한다.

전역이 다른 테스트의 load_roaming_config 호출로 오염되지 않도록, 모듈을 별도 이름으로
신선하게 로드해 '초기값'을 검사한다(순서 독립)."""
import importlib.util
import json
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("sUTILS", MagicMock())

_LOGGER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
# wifi_roam.py 가 `from roam_notify import ...` 를 하므로 logger 디렉터리를 명시적으로
# sys.path 에 넣는다 — `python -m pytest`(cwd 삽입)가 아닌 호출 방식에서도 단독 실행 가능.
sys.path.insert(0, _LOGGER_DIR)
_TMPL = os.path.join(
    _LOGGER_DIR, "..", "..", "..", "opt", "wlan", "config", "wifi_init_conf.json"
)
_GUIDE = os.path.join(
    _LOGGER_DIR, "..", "..", "..", "..", "..", "docs", "wifi_init_conf_guide.md"
)


def _fresh_roam_module():
    spec = importlib.util.spec_from_file_location(
        "wifi_roam_fresh_defaults", os.path.join(_LOGGER_DIR, "wifi_roam.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tmpl_roaming():
    with open(_TMPL) as f:
        return json.load(f)["mlan0"]["roaming"]


# (모듈 초기값 속성, 템플릿 mlan0.roaming 경로)
CASES = [
    ("DEFAULT_TH_2G", ["DEFAULT_TH_2G"]),
    ("DEFAULT_TH_5G", ["DEFAULT_TH_5G"]),
    ("DIFF_TH", ["DIFF_TH"]),
    ("CHECK_INTERVAL", ["CHECK_INTERVAL"]),
    ("DEFAULT_USE_SIGNAL_AVG", ["use_signal_avg"]),
    ("DEFAULT_SCAN_NO_RESULT_SLEEP", ["SCAN_NO_RESULT_SLEEP"]),
    ("DEFAULT_ROAM_SUCCESS_SLEEP", ["ROAM_SUCCESS_SLEEP"]),
    ("DEFAULT_ROAM_CROSS_FAIL_RETRY_COUNT", ["ROAM_CROSS_FAIL_RETRY_COUNT"]),
    ("DEFAULT_ROAM_NO_RESULT_FAST_COUNT", ["ROAM_NO_RESULT_FAST_COUNT"]),
    ("DEFAULT_ENABLE_PREDICTIVE_ROAM", ["PREDICTIVE_ROAM", "enable"]),
    ("DEFAULT_PREDICTIVE_THRESHOLD_BOOST", ["PREDICTIVE_ROAM", "threshold_boost"]),
    ("DEFAULT_TREND_WINDOW_SIZE", ["PREDICTIVE_ROAM", "trend_window_size"]),
    ("DEFAULT_TREND_HISTORY_MAX_AGE", ["PREDICTIVE_ROAM", "trend_history_max_age"]),
    ("DEFAULT_ENABLE_PING_PONG_PREVENTION", ["PING_PONG_PREVENTION", "enable"]),
    ("DEFAULT_PING_PONG_WINDOW", ["PING_PONG_PREVENTION", "window"]),
    ("DEFAULT_MAX_ROAMS_IN_WINDOW", ["PING_PONG_PREVENTION", "max_roams_in_window"]),
    ("DEFAULT_PING_PONG_DETECTION_TIME", ["PING_PONG_PREVENTION", "detection_time"]),
    ("DEFAULT_ENABLE_STAGED_SCAN", ["STAGED_SCAN", "enable"]),
    ("DEFAULT_SKIP_REDUNDANT_ACTIVE_SCAN", ["STAGED_SCAN", "skip_redundant_active"]),
    ("DEFAULT_HOME_PASSIVE", ["STAGED_SCAN", "home_passive"]),
]


@pytest.fixture(scope="module")
def fresh():
    return _fresh_roam_module()


@pytest.fixture(scope="module")
def tmpl():
    return _tmpl_roaming()


@pytest.mark.parametrize("attr,path", CASES, ids=[c[0] for c in CASES])
def test_code_default_matches_template(fresh, tmpl, attr, path):
    node = tmpl
    for k in path:
        assert isinstance(node, dict) and k in node, \
            f"템플릿 mlan0.roaming 에 {'.'.join(path)} 키 없음"
        node = node[k]
    code_val = getattr(fresh, attr)
    assert code_val == node, \
        f"{attr}(코드 초기값 {code_val!r}) != 템플릿 {'.'.join(path)}({node!r}) — fail-same 위반"


# bgscan 의 코드 기본값은 모듈 상수가 아니라 load_bgscan_json 의 초기값이다 — config 파일
# 부재 상태로 호출해 얻은 기본 튜플을 템플릿 mlan0.bgscan 과 대조한다.
# interval 은 제외: 3중 폴백 체인(JSON > wpa `#!INTERVAL` > DEFAULT_INTERVAL)이라 단순
# 대조가 성립하지 않고, 코드 최후 폴백(30)의 템플릿(60) 정렬 여부는 별도 판단 사항.
def test_bgscan_bool_defaults_match_template(monkeypatch):
    import wifi_bgscan
    monkeypatch.setattr(wifi_bgscan, "WIFI_INIT_CONF_JSON", "/nonexistent/wic.json")
    _interval, ssid_filter, freq_filter, _extra, emit_roam_hint, passive = \
        wifi_bgscan.load_bgscan_json("mlan0")
    with open(_TMPL) as f:
        bg = json.load(f)["mlan0"]["bgscan"]
    assert ssid_filter == bg["ssid_filter"], "ssid_filter fail-same 위반"
    assert freq_filter == bg["freq_filter"], "freq_filter fail-same 위반"
    assert passive == bg["passive"], "passive fail-same 위반"
    assert emit_roam_hint == bg["emit_roam_hint"], "emit_roam_hint fail-same 위반"
    # interval 폴백: JSON > wpa `#!INTERVAL` > 코드 폴백. 이중 실패(JSON 키 부재 + 마커
    # 부재) 시 쓰이는 코드 폴백이 템플릿 주기와 일치해야 스캔 cadence 가 fail-same 이다
    # (불일치 시 30s 폭주).
    assert wifi_bgscan.DEFAULT_INTERVAL == bg["interval"], "interval 코드 폴백 fail-same 위반"


# --- 스키마 default ↔ 템플릿 (docs/wifi_init_conf.schema.json) ---
# 스키마의 default 는 "배포 기본값이 무엇인가"를 설명하는 문서이고 WebUI·가이드 생성의
# 근거가 된다. 템플릿과 어긋나면 실제와 다른 값을 안내하게 된다 — 실측 사례: mlan0/mlan1
# 각 4키(CHECK_INTERVAL, ROAM_SUCCESS_SLEEP, PING_PONG_PREVENTION.window/detection_time)
# 가 어긋나 있었고 ROAM_NO_RESULT_FAST_COUNT 는 스키마에 키 자체가 없었다. 위의
# 코드↔템플릿 축만으로는 스키마가 사각지대라 이 축을 따로 고정한다.
_SCHEMA = os.path.join(
    _LOGGER_DIR, "..", "..", "..", "..", "..", "docs", "wifi_init_conf.schema.json"
)

SCHEMA_CASES = [(iface, path) for iface in ("mlan0", "mlan1") for _a, path in CASES]


@pytest.fixture(scope="module")
def schema():
    with open(_SCHEMA) as f:
        return json.load(f)


# 위의 `tmpl` 은 mlan0 만 반환하므로 iface 별 대조에는 전체 트리가 필요하다.
# 케이스마다 다시 파싱하지 않도록 module-scope 로 한 번만 읽는다.
@pytest.fixture(scope="module")
def full_tmpl():
    with open(_TMPL) as f:
        return json.load(f)


@pytest.mark.parametrize(
    "iface,path", SCHEMA_CASES, ids=[f"{i}:{'.'.join(p)}" for i, p in SCHEMA_CASES]
)
def test_schema_default_matches_template(schema, full_tmpl, iface, path):
    node = full_tmpl[iface]["roaming"]
    for k in path:
        assert isinstance(node, dict) and k in node, \
            f"템플릿 {iface}.roaming 에 {'.'.join(path)} 키 없음"
        node = node[k]

    sch = schema["properties"][iface]["properties"]["roaming"]
    for k in path:
        props = sch.get("properties", {})
        assert k in props, f"스키마 {iface}.roaming 에 {'.'.join(path)} 키 없음"
        sch = props[k]
    assert "default" in sch, f"스키마 {iface}.{'.'.join(path)} 에 default 없음"
    assert sch["default"] == node, (
        f"{iface}.{'.'.join(path)} — 스키마 default({sch['default']!r}) != "
        f"템플릿({node!r})"
    )


def _iface_default_cell(mlan0_value, mlan1_value):
    if mlan0_value == mlan1_value:
        return f"`{mlan0_value}`"
    return f"`{mlan0_value}` / `{mlan1_value}`"


def test_guide_roaming_defaults_match_template(full_tmpl):
    with open(_GUIDE, encoding="utf-8") as f:
        guide = f.read()
    mlan0 = full_tmpl["mlan0"]["roaming"]
    mlan1 = full_tmpl["mlan1"]["roaming"]

    for key in ("DIFF_TH", "CHECK_INTERVAL", "ROAM_SUCCESS_SLEEP"):
        expected = _iface_default_cell(mlan0[key], mlan1[key])
        assert f"| `{key}` | int | {expected} |" in guide, (
            f"가이드 {key} 기본값이 템플릿과 다름: expected {expected}"
        )

    ping0 = mlan0["PING_PONG_PREVENTION"]
    ping1 = mlan1["PING_PONG_PREVENTION"]
    assert ping0["detection_time"] == ping1["detection_time"]
    assert f"| `detection_time` | int | `{ping0['detection_time']}` |" in guide
    assert (
        f"(window={ping0['window']}, detection_time={ping0['detection_time']})"
        in guide
    )


def test_link_logger_cli_default_uses_module_constant(monkeypatch):
    import wifi_logger_link

    monkeypatch.setattr(wifi_logger_link, "LOOP_INTERVAL", 1.234)
    monkeypatch.setattr(wifi_logger_link, "SPIKE_THRESHOLD_FAIL", 12)
    monkeypatch.setattr(wifi_logger_link, "SPIKE_THRESHOLD_RETRY", 34)
    parser = wifi_logger_link.build_arg_parser()
    args = parser.parse_args(["mlan0"])
    assert args.interval == 1.234
    assert args.spike_fail == 12
    assert args.spike_retry == 34
    assert "default: 1.234" in parser.format_help()


@pytest.mark.parametrize("iface", ["mlan0", "mlan1"])
def test_link_logger_default_matches_template(full_tmpl, iface):
    """이 키는 전역 logger 에서 인터페이스별로 이관됐다 — 두 인터페이스 모두 고정한다."""
    import wifi_logger_link

    assert wifi_logger_link.LOOP_INTERVAL == full_tmpl[iface]["logger"]["link_interval_sec"]
