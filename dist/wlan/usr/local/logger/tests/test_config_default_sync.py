"""설정 기본값 생성 동기화 게이트 (로드맵 ② — scripts/gen_config_defaults.py).

같은 기본값이 템플릿/스키마/handoff 표에 손으로 중복 기재되어 drift 가 반복
실측됐다(스키마 9건 PR #146 + 2건, handoff 8건). 생성기 도입 후에는 템플릿만
고치고 --write 를 돌리는 것이 규약이며, 이 테스트가 "돌리는 것을 잊은" 커밋을
CI 에서 차단한다.

기존 test_defaults_template_consistency 의 스키마 축(구조적 대조)과 겹치지만
역할이 다르다 — 저쪽은 코드 폴백↔템플릿의 fail-same 원칙, 이쪽은 생성 산출물
(스키마 default·handoff 기본값 셀)의 동기화 규약을 고정한다.
"""
import os
import subprocess
import sys

_REPO = os.path.abspath(
    os.path.join(os.path.dirname(__file__), *[".."] * 6)
)
_TOOL = os.path.join(_REPO, "scripts", "gen_config_defaults.py")


def test_generated_defaults_are_in_sync():
    r = subprocess.run(
        [sys.executable, _TOOL, "--check"],
        capture_output=True, text=True, timeout=60
    )
    assert r.returncode == 0, (
        "템플릿과 스키마/handoff 기본값이 어긋났다.\n"
        "템플릿을 고쳤다면 scripts/gen_config_defaults.py --write 를 실행해\n"
        "생성 산출물을 함께 커밋할 것. 상세:\n" + r.stdout + r.stderr
    )


def test_schema_patch_resolves_shadowed_toplevel_block():
    """schema_patch_default 가 파일상 먼저 나오는 더 깊은 동명 블록(mac.mlan0)에
    속지 않고 최상위 mlan0 의 직계 경로를 패치한다 — 첫 mlan0 실패치에서 발견된
    스코핑 버그의 회귀 고정."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "gen_cfg", os.path.join(_REPO, "scripts", "gen_config_defaults.py")
    )
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)

    lines = gen.SCHEMA.read_text(encoding="utf-8").split("\n")
    gen.schema_patch_default(lines, ("mlan0", "roaming", "CHECK_INTERVAL"), 77)
    import json as _json
    patched = _json.loads("\n".join(lines))
    assert patched["properties"]["mlan0"]["properties"]["roaming"][
        "properties"]["CHECK_INTERVAL"]["default"] == 77
    # 그림자 블록(mac.mlan0)은 무손상
    assert "default" not in str(
        patched["properties"]["mac"]["properties"]["mlan0"]["properties"]["base"]
    ) or patched["properties"]["mac"]["properties"]["mlan0"][
        "properties"]["base"].get("default") != 77
