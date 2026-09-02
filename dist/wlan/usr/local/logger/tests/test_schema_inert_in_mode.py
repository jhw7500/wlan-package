"""`x-inert-in-mode` 어휘 게이트 (스키마 ↔ WebUI handoff).

`x-ui-editable*` 계열 커스텀 어휘에는 동기화 게이트가 없어 drift 가 실제로 발생했다
(handoff 는 `mlan0.antcfgnss.{enabled,value}` 에 by-board 가 적용된다고 적었지만
스키마에는 0건). 새 어휘를 도입하면서 같은 무감시 면을 하나 더 만들지 않도록,
이 어휘만큼은 스키마와 handoff 를 대조해 고정한다.

핵심 계약 두 가지:
1. 표기 대상 — 모드B 에서 읽는 소비자가 없는 roaming 키.
2. **무효성은 편집성과 별개다.** 잘못된 `extra_ssids` 는 모드와 무관하게 부팅
   스냅샷 생성을 실패시키므로(roam_policy 가 모드 게이트 없이 검증), 무효 표기를
   이유로 편집을 잠그면 UI 로 고칠 길이 사라진다.
"""
import json
import os

import pytest

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), *[".."] * 6))
_SCHEMA = os.path.join(_REPO, "docs/wifi_init_conf.schema.json")
_HANDOFF = os.path.join(_REPO, "docs/wifi_init_conf_webui_handoff.md")

_MARKER = "x-inert-in-mode"
_MODE_B_ONLY_ROAMING_KEYS = ("extra_ssids", "ROAM_CROSS_FAIL_RETRY_COUNT")


def _schema():
    with open(_SCHEMA, encoding="utf-8") as f:
        return json.load(f)


def _roaming_props(schema, iface):
    return schema["properties"][iface]["properties"]["roaming"]["properties"]


def _walk(node, path=""):
    if isinstance(node, dict):
        yield path, node
        for k, v in node.items():
            yield from _walk(v, f"{path}/{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}[{i}]")


@pytest.mark.parametrize("iface", ["mlan0", "mlan1"])
@pytest.mark.parametrize("key", _MODE_B_ONLY_ROAMING_KEYS)
def test_mode_b_only_keys_are_marked_inert(iface, key):
    prop = _roaming_props(_schema(), iface)[key]
    assert prop.get(_MARKER) == "B", (
        f"{iface}.roaming.{key} 는 모드B 에서 읽는 소비자가 없다 — "
        f"'{_MARKER}': 'B' 표기가 있어야 한다."
    )


@pytest.mark.parametrize("iface", ["mlan0", "mlan1"])
@pytest.mark.parametrize("key", _MODE_B_ONLY_ROAMING_KEYS)
def test_inert_marker_never_locks_editing(iface, key):
    """무효 표기가 편집성을 잠그면 안 된다 — 유일한 in-band 복구 경로가 닫힌다."""
    prop = _roaming_props(_schema(), iface)[key]
    assert prop["x-ui-editable"] != "no", (
        f"{iface}.roaming.{key}: 모드B 에서 무효라는 이유로 편집을 잠그면, "
        "부팅 스냅샷을 깨뜨리는 값을 UI 로 고칠 수 없게 된다."
    )


def test_marker_value_is_from_the_defined_vocabulary():
    """어휘가 퍼질 때 오타/임의값을 막는다."""
    bad = [
        (path, node[_MARKER])
        for path, node in _walk(_schema())
        if isinstance(node, dict) and _MARKER in node and node[_MARKER] not in ("A", "B")
    ]
    assert not bad, f"{_MARKER} 값은 'A' 또는 'B' 여야 한다: {bad}"


def test_handoff_defines_the_vocabulary_and_names_every_marked_key():
    """스키마에 표기된 키는 handoff 에도 반드시 나타나야 한다(by-board drift 재발 방지)."""
    with open(_HANDOFF, encoding="utf-8") as f:
        handoff = f.read()

    assert _MARKER in handoff, f"handoff 가 {_MARKER} 어휘를 정의하지 않았다."
    # 편집성과 무관하다는 규칙이 문서에 남아 있어야 한다 — 이게 이 어휘의 핵심이다.
    assert "편집 가능 여부와 무관" in handoff, (
        "handoff 가 '무효 표기는 편집성과 무관' 규칙을 잃었다 — "
        "UI 가 잠금으로 구현하면 복구 경로가 닫힌다."
    )

    marked = {
        path.rsplit("/", 1)[-1]
        for path, node in _walk(_schema())
        if isinstance(node, dict) and _MARKER in node
    }
    missing = sorted(k for k in marked if k not in handoff)
    assert not missing, f"스키마에만 표기되고 handoff 에 없는 키: {missing}"
