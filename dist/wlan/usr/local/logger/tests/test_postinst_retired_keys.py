"""postinst 은퇴 키 목록 ↔ 배포 템플릿 대조 + `_comment` 권위 게이트.

`dist/wlan/DEBIAN/postinst` 는 은퇴한 설정 키를 **하드코딩 경로 목록**으로 지운다
(`cleanup_retired_roaming_keys`) 또는 인터페이스별로 승격 후 지운다
(`migrate_retired_global_logger_keys`). 이 목록과 배포 템플릿을 대조하는 장치가
없어서, 어긋나도 아무도 모른 채 배포되는 것이 구조적 약점이다.

이 테스트가 고정하는 두 가지:

1. **은퇴 선언과 템플릿의 충돌 금지** — `cleanup_retired_roaming_keys` 는
   `json_merge` **뒤에** 돌기 때문에, 은퇴 목록에 있는 키가 템플릿에 되살아나면
   방금 설치된 값을 매 설치마다 조용히 지운다. 기능 키는 하나도 겹치면 안 된다.

2. **이관 키의 왕복 금지** — `migrate_retired_global_logger_keys` 는 `json_merge`
   **앞에서** 전역값을 per-iface 로 승격시키고 전역 키를 지운다. 전역 키가 템플릿에
   되살아나면 merge 가 곧바로 되돌려놓아 이관이 무효가 된다. 반대로 승격 대상
   인터페이스에 그 키가 템플릿에 없으면 승격이 아무 데도 도달하지 않는다.

3. **설명(`_comment*`)의 출처는 템플릿뿐** — `json_merge` 의 `jq *` 는 배열에서
   설치본이 이기므로, 그냥 두면 최초 설치 당시의 설명이 업그레이드 기기에 영구
   잔존한다(실측). 키가 다른 블록으로 이관된 뒤에도 옛 설명이 남아 운영자에게 틀린
   문서를 보여주므로, `JSON_MERGE_JQ` 가 `_comment*` 만 템플릿 값으로 덮고 템플릿에
   없는 것은 지운다. 이 테스트는 postinst 에서 **실제 프로그램을 꺼내** 검증한다.

의도적으로 **범용화(템플릿에 없는 키 일괄 삭제)하지 않는다**: `.mcp.iio_device` 는
템플릿에 없고 postinst 가 merge 후 보드별로 주입한다(postinst 의 iMX93 Invalid
Voltage 회귀 경고 참조). 일괄 스윕은 그 키를 지워 원 버그를 되살린다. 예외를 둔 것은
소비 코드가 읽지 않는 `_comment*`(문서 전용) 뿐이다.
"""
import json
import os
import re
import shutil
import subprocess

import pytest

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), *[".."] * 6))
_POSTINST = os.path.join(_REPO, "dist", "wlan", "DEBIAN", "postinst")
_TMPL = os.path.join(
    _REPO, "dist", "wlan", "opt", "wlan", "config", "wifi_init_conf.json"
)


def _postinst_fn(name, must_contain=None):
    """postinst 에서 셸 함수 본문을 원문 그대로 꺼낸다.

    본문의 끝은 **열 0 의 단독 `}` 행**으로 판정한다. 이 저장소의 셸 함수는 본문을
    들여쓰고 내장 jq 프로그램도 `if/then/else/end` 만 쓰므로 열 0 에 `}` 가 나타나지
    않는다는 전제다. 그 전제가 깨지면(중첩 프로세스 치환, 함수 안 함수 정의 등) 본문이
    거기서 조용히 잘리므로, 호출자가 `must_contain` 으로 본문 말미의 표식을 넘겨
    잘림을 즉시 검출한다 — 잘린 본문으로 정규식을 돌리면 "은퇴 경로 0건" 같은
    거짓 통과가 난다.
    """
    src = open(_POSTINST, encoding="utf-8").read()
    head = f"{name}() {{\n"
    assert head in src, f"postinst 에 {name} 이 없다(함수명/형식 변경 시 테스트 갱신)"
    rest = src.split(head, 1)[1]
    end = rest.find("\n}\n")
    assert end != -1, f"{name} 의 종료 행(열 0 의 단독 `}}`)을 찾지 못했다"
    body = rest[:end]
    if must_contain is not None:
        assert must_contain in body, (
            f"{name} 본문 추출이 잘렸다 — 본문 안에 열 0 의 단독 `}}` 행이 생기면 "
            f"거기서 끊긴다(기대 표식 {must_contain!r} 없음)"
        )
    return body


@pytest.fixture(scope="module")
def tmpl():
    with open(_TMPL, encoding="utf-8") as f:
        return json.load(f)


def _has(node, path):
    for key in path.strip(".").split("."):
        if not isinstance(node, dict) or key not in node:
            return False
        node = node[key]
    return True


def test_retired_roaming_keys_absent_from_template(tmpl):
    """은퇴 선언된 기능 키가 템플릿에 되살아나면 매 설치마다 조용히 삭제된다."""
    paths = re.findall(
        r"^\s*(\.mlan[01]\.[A-Za-z0-9_.]+),?\s*$",
        _postinst_fn("cleanup_retired_roaming_keys",
                     must_contain="POST_ROAM_ARP_OPTIMIZATION"),
        re.M,
    )
    assert paths, "은퇴 경로 파싱 실패 — postinst del() 목록 형식이 바뀌었다"
    live = [p for p in paths if "_comment" not in p and _has(tmpl, p)]
    assert not live, (
        "은퇴 선언된 키가 배포 템플릿에 존재한다 — postinst 의 "
        "cleanup_retired_roaming_keys 가 json_merge 직후 이 값을 지워 설정이 "
        f"무효가 된다: {live}"
    )


def test_cleanup_does_not_touch_comments():
    """`_comment` 는 json_merge 가 템플릿 권위로 관리한다 — cleanup 이 다시 손대면
    merge 가 방금 넣은 최신 설명을 도로 지우는 옛 우회책이 부활한다."""
    body = _postinst_fn("cleanup_retired_roaming_keys",
                        must_contain="POST_ROAM_ARP_OPTIMIZATION")
    assert "_comment" not in body, (
        "cleanup_retired_roaming_keys 가 `_comment` 를 다시 다룬다. 설명 갱신은 "
        "json_merge 의 JSON_MERGE_JQ 가 담당하므로 여기서 지우면 안 된다"
    )


def _json_merge_program():
    """postinst 의 실제 병합 프로그램을 꺼내온다 — 테스트가 사본이 아닌 실물을 검증."""
    src = open(_POSTINST, encoding="utf-8").read()
    assert "JSON_MERGE_JQ='" in src, "JSON_MERGE_JQ 정의를 찾지 못함(형식 변경 시 갱신)"
    prog = src.split("JSON_MERGE_JQ='", 1)[1].split("'\n", 1)[0]
    assert "cpaths" in prog, f"병합 프로그램 추출 실패: {prog[:120]!r}"
    return prog


def _merge(template, installed, tmp_path):
    t = tmp_path / "template.json"
    a = tmp_path / "installed.json"
    t.write_text(json.dumps(template))
    a.write_text(json.dumps(installed))
    r = subprocess.run(
        ["jq", "-s", _json_merge_program(), str(t), str(a)],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, f"병합 프로그램 실행 실패: {r.stderr}"
    return json.loads(r.stdout)


needs_jq = pytest.mark.skipif(shutil.which("jq") is None, reason="jq 미설치")


@needs_jq
def test_merge_refreshes_stale_comment_from_template(tmp_path):
    """[핵심] 업그레이드 기기의 옛 설명이 템플릿의 현재 설명으로 갱신되어야 한다.

    jq 의 `*` 는 배열에서 오른쪽(설치본)이 이기므로, 이 규칙이 없으면 최초 설치 당시의
    설명이 영구 잔존해 키가 이관된 뒤에도 틀린 문서를 보여준다.
    """
    merged = _merge(
        {"logger": {"_comment": ["새 설명"], "enabled": True}},
        {"logger": {"_comment": ["옛 설명"], "enabled": False}},
        tmp_path,
    )
    assert merged["logger"]["_comment"] == ["새 설명"]
    assert merged["logger"]["enabled"] is False, "운영자 값은 그대로 보존되어야 한다"


@needs_jq
def test_merge_drops_comment_absent_from_template(tmp_path):
    """은퇴한 블록의 설명 잔재는 제거된다(템플릿이 설명의 유일한 출처)."""
    merged = _merge(
        {"mlan1": {"roaming": {"STAGED_SCAN": {"enable": True}}}},
        {"mlan1": {"roaming": {"STAGED_SCAN": {"_comment": ["은퇴한 설명"], "enable": False}}}},
        tmp_path,
    )
    assert "_comment" not in merged["mlan1"]["roaming"]["STAGED_SCAN"]
    assert merged["mlan1"]["roaming"]["STAGED_SCAN"]["enable"] is False


@needs_jq
def test_merge_preserves_operator_values(tmp_path):
    """설명 규칙이 일반 키의 병합 의미(설치본 우선)를 바꾸지 않는다."""
    merged = _merge(
        {"mlan0": {"logger": {"link_interval_sec": 0.9, "enabled": True}}},
        {"mlan0": {"logger": {"link_interval_sec": 2.5}}},
        tmp_path,
    )
    assert merged["mlan0"]["logger"]["link_interval_sec"] == 2.5, "운영자 값 유실"
    assert merged["mlan0"]["logger"]["enabled"] is True, "신규 키가 주입되어야 한다"


@needs_jq
def test_merge_is_idempotent_on_fresh_install(tmp_path):
    """설치본 == 템플릿(신규 설치 직후)이면 병합이 아무것도 바꾸지 않는다."""
    with open(_TMPL, encoding="utf-8") as f:
        t = json.load(f)
    assert _merge(t, t, tmp_path) == t


@needs_jq
def test_shipped_template_comments_reach_installed_file(tmp_path):
    """실물 대조: 현재 템플릿의 모든 `_comment` 가 업그레이드 후에도 그대로 도달한다."""
    with open(_TMPL, encoding="utf-8") as f:
        t = json.load(f)
    stale = json.loads(json.dumps(t))

    def poison(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k.startswith("_comment"):
                    node[k] = ["[구버전 잔재]"]
                else:
                    poison(v)

    poison(stale)
    merged = _merge(t, stale, tmp_path)

    def collect(node, path=""):
        out = {}
        if isinstance(node, dict):
            for k, v in node.items():
                if k.startswith("_comment"):
                    out[f"{path}.{k}"] = v
                else:
                    out.update(collect(v, f"{path}.{k}"))
        return out

    assert collect(merged) == collect(t), "템플릿 설명이 설치본에 도달하지 않았다"


def _promotions():
    matches = re.findall(
        r'promote\("([A-Za-z0-9_]+)";\s*\[([^\]]+)\]\)',
        _postinst_fn("migrate_retired_global_logger_keys",
                          must_contain="bgscan_stale_threshold_sec"),
    )
    assert matches, "promote() 파싱 실패 — migrate 함수 형식이 바뀌었다"
    return [(k, re.findall(r'"([a-z0-9]+)"', ifs)) for k, ifs in matches]


@pytest.mark.parametrize("key,ifaces", _promotions())
def test_migrated_key_not_reintroduced_globally(key, ifaces, tmpl):
    """이관 키가 전역 logger 에 되살아나면 merge 가 되돌려놔 이관이 무효가 된다."""
    assert not _has(tmpl, f".logger.{key}"), (
        f"logger.{key} 는 인터페이스별로 이관됐는데 템플릿 전역에 되살아났다 — "
        "migrate 가 지운 직후 json_merge 가 다시 넣어 per-iface 이관이 무효가 된다"
    )


@pytest.mark.parametrize("key,ifaces", _promotions())
def test_migrated_key_exists_on_promotion_targets(key, ifaces, tmpl):
    """승격 대상 인터페이스에 키가 없으면 승격이 아무 소비처에도 도달하지 않는다."""
    missing = [i for i in ifaces if not _has(tmpl, f".{i}.logger.{key}")]
    assert not missing, (
        f"migrate 가 logger.{key} 를 {missing} 로 승격시키지만 템플릿의 해당 "
        "인터페이스에 그 키가 없다 — 승격 대상 목록과 템플릿이 어긋났다"
    )
