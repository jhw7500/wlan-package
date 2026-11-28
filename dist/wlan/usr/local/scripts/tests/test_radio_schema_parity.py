"""WebUI 계약(스키마·핸드오프 문서)을 무선 모드·대역폭과 on_connect 의 실제 동작에 맞출 것(#505).

`wifi <iface> mode|bw` 가 radio-apply 에 성공하면 `mlanN.radio.mode`·`radio.bw` 를 저장하고,
부팅 때 `wifi_init.sh` 의 `apply_radio_mode_bw()` 가 연결 전에 다시 적용한다. 값 해석은
`wifi_init_config_lib.sh` 의 매핑 함수 한 곳이다. WebUI 는 스키마의 enum 으로 입력을 고르므로,
스키마가 코드가 받지 않는 값을 허용하면 UI 설정이 부팅 때 조용히 건너뛰어지고, 받는 값을
빠뜨리면 UI 가 쓸 수 없는 값이 생긴다. 그래서 enum 을 매핑 함수의 실제 결과와 비교한다.

radio 는 템플릿에 넣지 않는다(키 없음 = 칩 기본·AP 따름). 넣으면 업그레이드의 json_merge 가
기존 설정에 값을 주입한다.

on_connect 는 `wifi_event.sh` 가 시작할 때 한 번 읽는다. 스키마·문서의 적용 시점이 `runtime`
(매번 재로드)이면 UI 가 서비스를 재시작하지 않아 바꾼 명령이 반영되지 않는다.
"""
import json
import re
import subprocess
from pathlib import Path

import pytest

WLAN_ROOT = Path(__file__).resolve().parents[4]
REPO_ROOT = WLAN_ROOT.parents[1]
SCHEMA = REPO_ROOT / "docs/wifi_init_conf.schema.json"
HANDOFF = REPO_ROOT / "docs/wifi_init_conf_webui_handoff.md"
CONFIG_LIB = WLAN_ROOT / "usr/local/scripts/wifi_init_config_lib.sh"
EVENT_SH = WLAN_ROOT / "usr/local/scripts/wifi_event.sh"
TEMPLATE = WLAN_ROOT / "opt/wlan/config/wifi_init_conf.json"

IFACES = ("mlan0", "mlan1")
# 스키마 enum 보다 넓게 잡아 양방향(허용 과다·누락)을 본다.
MODE_CANDIDATES = ["b", "g", "a", "n", "ac", "ax", "gac", "AX", "auto", "default", ""]
BW_CANDIDATES = ["20", "40", "80", "160", "auto", "default", "AUTO", "40+", ""]


def _schema():
    return json.loads(SCHEMA.read_text(encoding="utf-8"))


def _accepted(funcs, candidates):
    """lib 를 source 한 bash 에서 모든 함수가 0 으로 끝나며 값을 내는 후보만 돌려준다."""
    script = (
        '. "$1"; shift; fns="$1"; shift\n'
        'for c in "$@"; do\n'
        '  ok=1\n'
        '  for fn in $fns; do\n'
        '    out=$("$fn" "$c") && [ -n "$out" ] || { ok=0; break; }\n'
        '  done\n'
        '  [ "$ok" = 1 ] && printf "<%s>\\n" "$c"\n'
        'done\n'
        'exit 0\n'
    )
    r = subprocess.run(
        ["bash", "-c", script, "bash", str(CONFIG_LIB), " ".join(funcs), *candidates],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, r.stderr
    return {line[1:-1] for line in r.stdout.splitlines() if line.startswith("<")}


def _radio_prop(iface, key):
    props = _schema()["properties"][iface]["properties"]
    assert "radio" in props, f"스키마에 {iface}.radio 가 없다"
    radio = props["radio"]["properties"]
    assert key in radio, f"스키마에 {iface}.radio.{key} 가 없다"
    return radio[key]


def test_boot_mapping_helpers_reject_unknown_values():
    """매핑 함수가 후보를 실제로 거른다는 양성 대조 — 모두 통과하면 아래 비교가 무의미하다."""
    assert "160" not in _accepted(["wifi_init_bw_to_htcap", "wifi_init_bw_to_vhtbw", "wifi_init_bw_to_he_b2"], BW_CANDIDATES)
    assert "gac" not in _accepted(["wifi_init_mode_to_bandcfg_mask"], MODE_CANDIDATES)


@pytest.mark.parametrize("iface", IFACES)
def test_schema_bw_enum_matches_boot_mapping(iface):
    accepted = _accepted(["wifi_init_bw_to_htcap", "wifi_init_bw_to_vhtbw", "wifi_init_bw_to_he_b2"], BW_CANDIDATES)
    prop = _radio_prop(iface, "bw")
    assert prop["type"] == "string"
    assert set(prop["enum"]) == accepted, (iface, prop["enum"], sorted(accepted))
    assert prop["x-apply-timing"] == "boot"
    assert "default" not in prop, "템플릿에 없는 키라 스키마 default 를 두지 않는다"


@pytest.mark.parametrize("iface", IFACES)
def test_schema_mode_enum_matches_boot_mapping(iface):
    accepted = _accepted(["wifi_init_mode_to_bandcfg_mask"], MODE_CANDIDATES)
    if iface == "mlan1":
        # wifi_init.sh apply_radio_mode_bw 와 wifi.sh 모두 mlan1 의 ax 를 거부한다.
        accepted = accepted - {"ax"}
    prop = _radio_prop(iface, "mode")
    assert prop["type"] == "string"
    assert set(prop["enum"]) == accepted, (iface, prop["enum"], sorted(accepted))
    assert prop["x-apply-timing"] == "boot"
    assert "default" not in prop


def test_template_does_not_carry_radio():
    """보존 검사: 템플릿에 radio 를 넣으면 json_merge 가 기존 설정에 값을 주입한다."""
    tmpl = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    for iface in IFACES:
        assert "radio" not in tmpl[iface], f"템플릿 {iface}.radio 가 생겼다"


def test_wifi_event_reads_on_connect_once_before_event_loop():
    """daemon-restart 계약의 코드 근거: 루프 밖에서 한 번 읽고 run_on_connect 는 캐시만 쓴다."""
    src = EVENT_SH.read_text(encoding="utf-8")
    read_at = src.find("ON_CONNECT_CMDS=$(jq")
    loop_at = src.find("iw event -t")
    assert 0 <= read_at < loop_at, (read_at, loop_at)
    body = re.search(r"\nrun_on_connect\(\) \{\n(.*?)\n\}\n", src, re.S)
    assert body, "run_on_connect 정의를 찾지 못함"
    assert "jq" not in body.group(1), "run_on_connect 가 JSON 을 다시 읽는다 — 적용 시점 계약을 다시 볼 것"


@pytest.mark.parametrize("iface", IFACES)
@pytest.mark.parametrize("key", ["enabled", "commands"])
def test_schema_on_connect_apply_timing_is_daemon_restart(iface, key):
    prop = _schema()["properties"][iface]["properties"]["on_connect"]["properties"][key]
    assert prop["x-apply-timing"] == "daemon-restart", (iface, key, prop["x-apply-timing"])


def _cells(line):
    """표 한 줄을 칸으로 나눈다. 칸 안의 `\\|`(이스케이프된 파이프)는 나누지 않는다."""
    return [c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


def _handoff_row(path_cell):
    for line in HANDOFF.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"| `{path_cell}` |"):
            return _cells(line)
    return None


@pytest.mark.parametrize("key", ["enabled", "commands"])
def test_handoff_on_connect_apply_timing_matches_schema(key):
    # 같은 이름의 행이 앞 절에도 있으므로 on_connect 절 안에서만 찾는다.
    text = HANDOFF.read_text(encoding="utf-8")
    section = text.split("#### 3.10.6 mlanN.on_connect", 1)[1].split("\n#### ", 1)[0]
    rows = [l for l in section.splitlines() if l.startswith(f"| `{key}` |")]
    assert len(rows) == 1, rows
    cells = _cells(rows[0])
    assert cells[6] == "daemon-restart", cells
    assert "매 connected 이벤트마다 JSON 재읽기" not in section


@pytest.mark.parametrize("key", ["radio.mode", "radio.bw"])
def test_handoff_documents_radio_keys(key):
    row = _handoff_row(key)
    assert row is not None, f"핸드오프 §3.10.1 에 `{key}` 행이 없다"
    assert row[6] == "boot", row


def _section(start_heading):
    text = HANDOFF.read_text(encoding="utf-8")
    assert start_heading in text, f"절 {start_heading!r} 가 없다"
    return text.split(start_heading, 1)[1].split("\n#### ", 1)[0]


def test_handoff_radio_bw_warns_on_connect_fixed_rate_conflict():
    """현장 UI 가 대역폭을 좁힐 때 기존 on_connect 고정 전송률과 겹치면 송신이 무너진다(Tribunal A-R1-001)."""
    row = " | ".join(_handoff_row("radio.bw"))
    assert "on_connect" in row and "txratecfg" in row, row
    sec = _section("#### 3.10.5a ")
    assert "on_connect" in sec and "wifi_event@" in sec, "§3.10.5a 에 on_connect 정리 규칙이 없다"


@pytest.mark.parametrize("iface", IFACES)
def test_schema_radio_bw_description_warns_on_connect_conflict(iface):
    desc = _radio_prop(iface, "bw")["description"]
    assert "on_connect" in desc and "txratecfg" in desc, desc


def test_handoff_test_recipes_apply_staged_radio_and_clean_up():
    """mode·bw 는 올려 두기만 하므로 radio-apply 가 필요하고(Codex P2), 시험이 끝나면 on_connect 항목과
    wifi_event 까지 되돌려야 고정이 다시 걸리지 않는다(Tribunal A-R1-002)."""
    sec = _section("#### 3.10.6a ")
    n_row = [l for l in sec.splitlines() if l.startswith("| AP 보다 좁은 대역폭 + MCS, 표준 n |")]
    assert len(n_row) == 1, n_row
    # 명령 칸(둘째 칸)에 적용 단계가 있어야 한다 — 비고 칸의 언급만으로는 절차가 되지 않는다.
    assert "radio-apply" in _cells(n_row[0])[1], _cells(n_row[0])
    cleanup = [l for l in sec.splitlines() if "txratecfg 0xff" in l]
    assert any("on_connect" in l and "wifi_event@" in l for l in cleanup), cleanup


@pytest.mark.parametrize("iface", IFACES)
def test_schema_radio_bw_description_states_he_width_behaviour(iface):
    """#510: ax 의 bw 20 은 HE 능력 폭도 내린다 — "AP→보드 폭은 AP 판단" 서술은 남기지 않는다."""
    desc = _radio_prop(iface, "bw")["description"]
    assert "AP 판단" not in desc, desc
    if iface == "mlan0":
        assert "11axcfg" in desc and "운영 모드 통지" in desc, desc
    else:
        assert "HE 능력은 다루지 않는다" in desc, desc


def test_handoff_radio_bw_documents_he_width_and_its_limit():
    """#510: radio.bw 행과 §3.10.5a 가 HE 능력 폭 동작, bw 40 의 한계, 2026-11-23 관측을 적는다."""
    row = " | ".join(_handoff_row("radio.bw"))
    assert "11axcfg" in row and "AP 판단" not in row, row
    sec = _section("#### 3.10.5a ")
    assert "AP 판단" not in sec, "§3.10.5a 에 대체된 서술이 남아 있다"
    assert "운영 모드 통지" in sec and "11axcfg" in sec and "2026-11-23" in sec
