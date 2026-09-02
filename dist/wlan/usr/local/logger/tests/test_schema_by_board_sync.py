"""`x-ui-editable-by-board` 문서↔스키마 동기화 게이트.

drift 실측: #220(`3a4a2ba`)이 antcfg 의 광고 NSS 역할을 antcfgnss 로 옮기면서,
스키마의 antcfgnss 전 필드에는 `x-ui-editable: "no"` 를 넣고(의도) handoff 의
by-board 목록 문장에만 `mlan0.antcfgnss.{enabled,value}` 를 추가했다. 스키마에는
antcfgnss by-board 가 **0건**인데 문서는 있다고 말하는 상태가 그대로 남았다.

`x-ui-editable*` 계열에는 기본값 컬럼과 달리 동기화 게이트가 없어 아무도 이를
발견하지 못했다. 이 테스트가 그 면을 덮는다 — 문서의 목록 문장과 스키마의 실제
표기가 정확히 일치해야 한다.
"""
import json
import os
import re

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), *[".."] * 6))
_SCHEMA = os.path.join(_REPO, "docs/wifi_init_conf.schema.json")
_HANDOFF = os.path.join(_REPO, "docs/wifi_init_conf_webui_handoff.md")

_MARKER = "x-ui-editable-by-board"
_SENTENCE_START = "현재 이 조건부 계약은"
# `mlan0.antcfg.{enabled,tx,rx}` 형태만 뽑는다.
_CLAIM_RE = re.compile(r"`(mlan\d)\.([A-Za-z0-9_]+)\.\{([^}]*)\}`")


def _schema_by_board_keys():
    with open(_SCHEMA, encoding="utf-8") as f:
        schema = json.load(f)
    found = set()
    for iface, iface_node in schema["properties"].items():
        if not re.fullmatch(r"mlan\d", iface):
            continue
        for group, group_node in iface_node.get("properties", {}).items():
            for key, key_node in (group_node.get("properties") or {}).items():
                if isinstance(key_node, dict) and _MARKER in key_node:
                    found.add(f"{iface}.{group}.{key}")
    return found


def _handoff_claimed_keys():
    with open(_HANDOFF, encoding="utf-8") as f:
        handoff = f.read()
    start = handoff.index(_SENTENCE_START)
    end = handoff.index("이다.", start) + len("이다.")
    sentence = handoff[start:end]
    claimed = set()
    for iface, group, keys in _CLAIM_RE.findall(sentence):
        for key in keys.split(","):
            key = key.strip()
            if key:
                claimed.add(f"{iface}.{group}.{key}")
    return claimed


def test_handoff_by_board_list_matches_the_schema_exactly():
    schema_keys = _schema_by_board_keys()
    claimed = _handoff_claimed_keys()

    assert schema_keys, "스키마에서 by-board 표기를 하나도 찾지 못했다 — 탐색 로직 점검 필요."
    assert claimed, "handoff 의 조건부 계약 문장에서 키를 하나도 뽑지 못했다 — 문장 형식이 바뀌었는지 확인."

    only_in_doc = sorted(claimed - schema_keys)
    only_in_schema = sorted(schema_keys - claimed)
    assert not only_in_doc, (
        "handoff 가 by-board 적용이라고 적었지만 스키마에 표기가 없는 키: "
        f"{only_in_doc}. 스키마에 넣든 문장에서 빼든 한쪽으로 맞출 것."
    )
    assert not only_in_schema, (
        "스키마에 by-board 표기가 있으나 handoff 문장이 누락한 키: "
        f"{only_in_schema}. WebUI 가 조건부 계약을 모르고 지나친다."
    )


def test_by_board_maps_share_one_board_vocabulary():
    """보드 키가 갈라지면 UI 조회가 조용히 fallback 으로 떨어진다."""
    with open(_SCHEMA, encoding="utf-8") as f:
        schema = json.load(f)
    shapes = set()
    for iface_node in schema["properties"].values():
        for group_node in (iface_node.get("properties") or {}).values():
            if not isinstance(group_node, dict):
                continue
            for key_node in (group_node.get("properties") or {}).values():
                if isinstance(key_node, dict) and _MARKER in key_node:
                    shapes.add(tuple(sorted(key_node[_MARKER])))
    assert len(shapes) == 1, f"by-board 맵의 보드 키 집합이 갈렸다: {sorted(shapes)}"
