#!/usr/bin/env python3
"""배포 템플릿(wifi_init_conf.json)을 단일 소스로 스키마 default 와 handoff 표
기본값 컬럼을 동기화한다 (로드맵 ② 설정 생성화 — 최소안).

같은 기본값이 4곳(코드 DEFAULT_/템플릿/스키마/handoff 표)에 손으로 중복 기재되어
drift 가 반복 실측됐다(스키마 9건 PR #146, wbridge 2건 PR #148, handoff 3건).
이 도구는 그중 스키마·handoff 두 축을 템플릿에서 기계 생성으로 전환한다.
코드 DEFAULT_ 상수는 대상이 아니다 — tests/test_defaults_template_consistency.py
의 fail-same 축이 별도로 고정한다.

사용:
    scripts/gen_config_defaults.py --check   # 불일치 보고, 있으면 exit 1 (CI 게이트)
    scripts/gen_config_defaults.py --write   # 스키마·handoff 를 템플릿 값으로 패치

동기화 규칙:
  - 스키마: 구조·설명·x-* 는 손으로 관리하고 **default 값만** 템플릿에서 패치.
    템플릿에 있는데 스키마에 키가 없으면 오류로 보고(수동 추가 필요 — 설명을
    기계가 지어낼 수 없다).
  - handoff: §3(필드 스펙) 표의 **기본값 컬럼(4번째 셀)만** 패치. §2.3(기본값
    변동 이력)은 역사 기록이라 건드리지 않는다. mlan0/mlan1 값이 다르면
    `mlan0=A / mlan1=B` 형식.
"""
import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "dist/wlan/opt/wlan/config/wifi_init_conf.json"
SCHEMA = REPO / "docs/wifi_init_conf.schema.json"
HANDOFF = REPO / "docs/wifi_init_conf_webui_handoff.md"

IFACES = ("mlan0", "mlan1")


def template_leaves(tmpl):
    """(path 튜플, 값) 전수. _comment* 제외."""
    def walk(node, path):
        for k, v in node.items():
            if k.startswith("_comment"):
                continue
            if isinstance(v, dict):
                yield from walk(v, path + (k,))
            else:
                yield path + (k,), v
    yield from walk(tmpl, ())


# ── 스키마 축 ────────────────────────────────────────────────────────────────

def schema_node(schema, path):
    node = schema.get("properties", {})
    for i, k in enumerate(path):
        if k not in node:
            return None
        node = node[k]
        if i < len(path) - 1:
            node = node.get("properties", {})
    return node


def schema_report(tmpl, schema):
    """(drift, 키 누락, default 줄 누락). drift = (path, 템플릿값, 스키마값).
    default 필드 자체가 없는 노드는 drift 로 취급하면 --write 의 라인 패처가
    "default" 줄을 못 찾아 비정상 종료하므로 수동 추가 대상으로 분리한다."""
    drift, missing, missing_default = [], [], []
    for path, tv in template_leaves(tmpl):
        n = schema_node(schema, path)
        if n is None:
            missing.append(path)
        elif "default" not in n:
            missing_default.append(path)
        elif n["default"] != tv:
            drift.append((path, tv, n["default"]))
    return drift, missing, missing_default


def _find_block(lines, name, start, end, indent_gt):
    """[start, end) 범위에서 indent 가 indent_gt 초과인 '"name": {' 줄을 찾아
    (시작행, 끝행, indent) 반환. 끝행 = 같은 indent 의 '}' 또는 '},'.

    범위 한정 + **최소 들여쓰기 매치 선택** = 직계 자식. 첫 매치를 쓰면 더 깊은
    동명 블록(예: mac.mlan0 이 최상위 mlan0 보다 파일상 앞에 있음)을 오인한다 —
    실사용 첫 mlan0 패치에서 발견된 버그."""
    pat = re.compile(r'^(\s*)"' + re.escape(name) + r'":\s*\{')
    cands = []
    for i in range(start, end):
        m = pat.match(lines[i])
        if m and len(m.group(1)) > indent_gt:
            cands.append((len(m.group(1)), i))
    if not cands:
        return None
    indent, i = min(cands)  # 최소 들여쓰기(동률이면 앞선 행) = 스코프의 직계 자식
    for j in range(i + 1, end):
        s = lines[j]
        if s.strip() in ("}", "},") and len(s) - len(s.lstrip()) == indent:
            return i, j, indent
    raise RuntimeError(f"블록 끝 미발견: {name} @ {i + 1}")


def schema_patch_default(lines, path, value):
    """스키마 텍스트에서 path 블록의 "default": 줄을 value 로 교체.
    각 단계 탐색을 직전 부모 블록의 [시작+1, 끝) 으로 스코프한다."""
    assert path, "빈 경로"
    pos, end, indent, found = 0, len(lines), -1, None
    for comp in path:
        found = _find_block(lines, comp, pos, end, indent)
        if found is None:
            raise RuntimeError(f"스키마에서 경로 미발견: {'.'.join(path)}")
        pos, end, indent = found[0] + 1, found[1], found[2]
    blk_end = found[1]
    dpat = re.compile(r'^(\s*)"default":\s*(.+?)(,?)\s*$')
    for i in range(pos, blk_end):
        m = dpat.match(lines[i])
        if m:
            lines[i] = f'{m.group(1)}"default": ' \
                       f"{json.dumps(value, ensure_ascii=False)}{m.group(3)}"
            return
    raise RuntimeError(f'"default" 줄 미발견: {".".join(path)}')


# ── handoff 축 ───────────────────────────────────────────────────────────────

def fmt_cell(v, quote_str=False):
    """quote_str: mlan0/mlan1 병기처럼 빈 문자열(`\"\"`)과 나란히 놓일 때는
    문자열을 따옴표로 감싸야 형이 드러난다(기존 표 관례: `mlan0=\"7\" / mlan1=\"\"`).
    단독 셀은 §3.1 관례대로 raw(`imx93`, `moal`)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        if v == "":
            return '""'
        return f'"{v}"' if quote_str else v
    if isinstance(v, list):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def resolve(tmpl, candidates, key):
    """후보 prefix 들에 key 를 붙여 템플릿에서 값을 찾는다.
    반환: (vals, mlanN-정규화 경로 튜플) — vals 는 mlanN 확장 시 {iface: 값},
    일반 경로는 {"_": 값}. 미해결이면 (None, None)."""
    _MISSING = object()

    def get(path):
        node = tmpl
        for k in path:
            if not isinstance(node, dict) or k not in node:
                return _MISSING
            node = node[k]
        return _MISSING if isinstance(node, dict) else node

    for prefix in candidates:
        parts = (prefix.split(".") if prefix else []) + key.split(".")
        if "mlanN" in parts:
            vals = {}
            for iface in IFACES:
                v = get(tuple(iface if p == "mlanN" else p for p in parts))
                if v is _MISSING:
                    vals = None
                    break
                vals[iface] = v
            if vals is not None:
                return vals, tuple(parts)
        else:
            v = get(tuple(parts))
            if v is not _MISSING:
                return {"_": v}, tuple(parts)
    return None, None


def desired_cell(vals):
    if "_" in vals:
        return f"`{fmt_cell(vals['_'])}`"
    a, b = vals["mlan0"], vals["mlan1"]
    if a == b:
        return f"`{fmt_cell(a)}`"
    q = isinstance(a, str) or isinstance(b, str)
    return f"`mlan0={fmt_cell(a, q)} / mlan1={fmt_cell(b, q)}`"


def header_candidates(header_text, tops):
    """헤더 문장에서 섹션 prefix 후보를 뽑는다(자기 자신 + 부모들 + mlanN 폴백)."""
    cands = []
    for tok in re.findall(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*", header_text):
        root = tok.split(".")[0]
        if root in tops or root in ("mlan0", "mlan1", "mlanN"):
            tok = re.sub(r"^mlan[01]\b", "mlanN", tok)
            while tok:
                if tok not in cands:
                    cands.append(tok)
                tok = tok.rpartition(".")[0]
    if any(c.startswith("mlanN") for c in cands) and "mlanN" not in cands:
        cands.append("mlanN")
    cands.append("")  # 전체 경로형 행(global.X 등) 대응
    return cands


# handoff §3 에 행이 없어도 되는 템플릿 leaf (mlanN 정규화 경로 문자열).
# 항목을 늘릴 땐 왜 WebUI 인수인계에서 빼는지 사유를 함께 적을 것.
HANDOFF_COVERAGE_ALLOWLIST = {
    # antcfg/antcfgnss 의 검증·위임 계약 필드. §3 에 대표 요약 행이 이미 있고
    # (`antcfg.verify.*` / `antcfgnss.verify.user_htstream` /
    # `antcfgnss.fallback_antcfg.*`), 전부 UI편집=no 라 낱개 행을 더 실어도 WebUI
    # 인수인계에 보탬이 없다. 게다가 이 leaf 들은 mlan0/mlan1 한쪽에만 존재해서
    # (mlan0 = antcfgnss 의 verify·fallback_antcfg, mlan1 = antcfg.verify)
    # `mlan0=X / mlan1=(없음)` 을 적을 셀 포맷이 §3 에 없다.
    # 템플릿의 iface 비대칭이 해소되면 여기서 빼고 §3 에 정식 행으로 실을 것.
    "mlanN.antcfg.verify.physical_rx",
    "mlanN.antcfg.verify.physical_tx",
    "mlanN.antcfg.verify.user_htstream",
    "mlanN.antcfgnss.verify.user_htstream",
    "mlanN.antcfgnss.fallback_antcfg.tx",
    "mlanN.antcfgnss.fallback_antcfg.rx",
    "mlanN.antcfgnss.fallback_antcfg.verify.physical_rx",
    "mlanN.antcfgnss.fallback_antcfg.verify.physical_tx",
    "mlanN.antcfgnss.fallback_antcfg.verify.user_htstream",
}
# 템플릿에 의도적으로 없는 런타임 생성 필드. 이 목록 밖의 미해결 표 행은 typo/유령
# 후보이므로 --check를 실패시킨다.
HANDOFF_UNRESOLVED_ALLOWLIST = {
    "mcp.iio_device",
    # 아래 셋은 §3 의 대표 요약 행이다. `*` 를 쓴 두 행은 어떤 템플릿 leaf 와도
    # 매칭될 수 없고, `antcfgnss.verify.user_htstream` 은 실재하는 키지만 mlan0
    # 에만 있어(resolve 는 mlanN 경로에 두 iface 값을 모두 요구한다) 해결되지
    # 않는다. 유령/typo 가 아니라 의도된 요약 행이므로 면제한다.
    "antcfg.verify.*",
    "antcfgnss.verify.user_htstream",
    "antcfgnss.fallback_antcfg.*",
}


def handoff_sync(tmpl, lines, write):
    """§3 표의 기본값 셀 동기화.
    (변경행, 미해결행, 커버리지 누락 leaf) 반환 — 누락 = 템플릿에 있는데
    §3 표 어디에도 행이 없는 키(WebUI 구현자가 볼 수 없는 설정)."""
    tops = set(tmpl.keys())
    in_scope = False
    cands, h3_cands = [""], [""]
    changed, unresolved = [], []
    covered = set()
    row_re = re.compile(r"^\|\s*`([^`]+)`\s*\|")
    for i, line in enumerate(lines):
        if line.startswith("## "):
            in_scope = line.startswith("## 3.")
            continue
        if not in_scope:
            continue
        if line.startswith("###"):
            got = header_candidates(line, tops)
            if line.startswith("### "):
                h3_cands = got
                cands = got
            else:
                # #### 하위 헤더에 prefix 토큰이 없으면(예: "인터페이스 기본 + radio")
                # 상위 ### 의 후보(mlanN 등)를 상속한다.
                cands = got if got != [""] else h3_cands
            continue
        m = row_re.match(line)
        if not m:
            continue
        key = m.group(1)
        cells = line.split("|")
        if len(cells) < 6:  # | 키 | 라벨 | 타입 | 기본값 | ... 최소 형태
            continue
        vals, rpath = resolve(tmpl, cands, re.sub(r"\bmlan[01]\b", "mlanN", key))
        if vals is None:
            unresolved.append((i + 1, key))
            continue
        covered.add(".".join(rpath))
        want = desired_cell(vals)
        cur = cells[4].strip()
        if cur != want:
            changed.append((i + 1, key, cur, want))
            if write:
                cells[4] = f" {want} "
                lines[i] = "|".join(cells)

    # 역방향 커버리지: 템플릿 leaf(mlanN 정규화) 전수가 §3 에 행으로 존재해야 한다.
    # 없으면 WebUI 구현자가 그 설정의 존재 자체를 모른다(리뷰 P2 — 실측 12키 누락).
    all_paths = set()
    for p, _v in template_leaves(tmpl):
        norm = tuple("mlanN" if c in IFACES else c for c in p)
        all_paths.add(".".join(norm))
    uncovered = sorted(all_paths - covered - HANDOFF_COVERAGE_ALLOWLIST)
    return changed, unresolved, uncovered


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="불일치 보고(있으면 exit 1)")
    g.add_argument("--write", action="store_true", help="스키마·handoff 패치")
    args = ap.parse_args()

    tmpl = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))

    drift, missing, missing_default = schema_report(tmpl, schema)
    handoff_lines = HANDOFF.read_text(encoding="utf-8").split("\n")
    h_changed, h_unresolved, h_uncovered = handoff_sync(
        tmpl, handoff_lines, write=args.write
    )
    h_unknown_unresolved = [
        (ln, key) for ln, key in h_unresolved
        if key not in HANDOFF_UNRESOLVED_ALLOWLIST
    ]

    fail = False
    if missing:
        fail = True
        print(f"[스키마] 템플릿 키가 스키마에 없음 — 수동 추가 필요: {len(missing)}건")
        for p in missing:
            print(f"    {'.'.join(p)}")
    if missing_default:
        fail = True
        print(f"[스키마] default 필드 없음 — 수동 추가 필요: {len(missing_default)}건")
        for p in missing_default:
            print(f"    {'.'.join(p)}")
    if drift:
        print(f"[스키마] default 불일치: {len(drift)}건")
        for p, tv, sv in drift:
            print(f"    {'.'.join(p)}: 템플릿={tv!r}  스키마={sv!r}")
    if h_changed:
        print(f"[handoff] 기본값 셀 불일치: {len(h_changed)}건")
        for ln, key, cur, want in h_changed:
            print(f"    :{ln} `{key}`: {cur} → {want}")
    if h_unresolved:
        print(f"[handoff] 템플릿 미해결 행: {len(h_unresolved)}건 "
              f"(명시적 runtime allowlist={len(h_unresolved) - len(h_unknown_unresolved)})")
        for ln, key in h_unresolved:
            print(f"    :{ln} `{key}`")
    if h_unknown_unresolved:
        fail = True
        print(f"[handoff] allowlist 없는 미해결 행 — 제거/유령 후보: "
              f"{len(h_unknown_unresolved)}건")
        for ln, key in h_unknown_unresolved:
            print(f"    :{ln} `{key}`")
    if h_uncovered:
        fail = True
        print(f"[handoff] §3 에 행이 없는 템플릿 키 — 수동 추가 필요: "
              f"{len(h_uncovered)}건")
        for p in h_uncovered:
            print(f"    {p}")

    if args.write:
        if missing or missing_default or h_unknown_unresolved:
            print("스키마 누락 또는 allowlist 없는 handoff 행은 --write 로 자동 처리하지 않는다. 중단.")
            return 1
        slines = SCHEMA.read_text(encoding="utf-8").split("\n")
        for p, tv, _ in drift:
            schema_patch_default(slines, p, tv)
        out = "\n".join(slines)
        json.loads(out)  # 문법 검증
        SCHEMA.write_text(out, encoding="utf-8")
        HANDOFF.write_text("\n".join(handoff_lines), encoding="utf-8")
        print(f"패치 완료: 스키마 {len(drift)}건, handoff {len(h_changed)}건")
        if h_uncovered:
            print(f"주의: handoff 행 누락 {len(h_uncovered)}건은 자동 패치 대상이 "
                  f"아니다(설명을 사람이 써야 함) — 위 목록을 수동 추가해야 "
                  f"--check 가 통과한다.")
            return 1
        return 0

    if fail or drift or h_changed:
        print("불일치 있음 — scripts/gen_config_defaults.py --write 로 동기화하라.")
        return 1
    print(f"동기화 상태 정상 (runtime 생성 handoff 행 {len(h_unresolved)}건 allowlist).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
