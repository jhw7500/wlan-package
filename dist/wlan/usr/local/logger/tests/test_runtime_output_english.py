"""Guard shipped runtime output against non-ASCII text."""

import ast
import re
from pathlib import Path


WLAN_ROOT = Path(__file__).resolve().parents[4]
ASCII = re.compile(r"^[\x00-\x7f]*$")
ENGINEER_TOOLS = {
    "wifi.sh",
    "diag-9098-11ax.sh",
    "verify-he-mu-features.sh",
}
PYTHON_RUNTIME_ROOTS = [
    WLAN_ROOT / "usr/local/logger",
    WLAN_ROOT / "usr/local/tools/ping-monitor",
]


def strip_shell_comment(line: str) -> str:
    quote = None
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote != "'":
            escaped = True
            continue
        if char in ("'", '"'):
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            continue
        if char == "#" and quote is None:
            return line[:index]
    return line


def test_shell_runtime_code_is_ascii_english():
    paths = list((WLAN_ROOT / "DEBIAN").glob("*inst"))
    paths += list((WLAN_ROOT / "DEBIAN").glob("*rm"))
    paths += list((WLAN_ROOT / "usr/local/scripts").glob("*.sh"))
    failures = []
    for path in paths:
        if path.name in ENGINEER_TOOLS or "test" in path.stem:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = strip_shell_comment(line)
            if code and not ASCII.fullmatch(code):
                failures.append(f"{path.relative_to(WLAN_ROOT)}:{lineno}: {code.strip()}")
    assert failures == []


def test_python_runtime_literals_are_ascii_english():
    failures = []
    paths = sorted(path for root in PYTHON_RUNTIME_ROOTS for path in root.glob("*.py"))
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = {
            child: parent
            for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if isinstance(parents.get(node), ast.Expr):
                continue
            if not ASCII.fullmatch(node.value):
                failures.append(
                    f"{path.relative_to(WLAN_ROOT)}:{node.lineno}: {node.value!r}"
                )
    assert failures == []


def test_systemd_runtime_fields_are_ascii_english():
    failures = []
    unit_root = WLAN_ROOT / "etc/systemd/system"
    for path in sorted(path for path in unit_root.glob("*") if path.is_file()):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            field = line.strip()
            if not field or field.startswith(("#", ";")):
                continue
            if not ASCII.fullmatch(field):
                failures.append(
                    f"{path.relative_to(WLAN_ROOT)}:{lineno}: {field}"
                )
    assert failures == []
