import ast
import re
from pathlib import Path


WLAN_ROOT = Path(__file__).resolve().parents[4]
LOGGER_STAT = WLAN_ROOT / "usr/local/logger/wifi_logger_stat.py"
SYSTEMD_ROOT = WLAN_ROOT / "etc/systemd/system"
STRUCTURED_LOG_UNITS = (
    "wifi_event@.service",
    "wifi_checker@.service",
    "wifi_logger.service",
    "wifi_logger@.service",
    "wlan_fw_watch.service",
)
SHELL_ROOTS = (
    WLAN_ROOT / "usr/local/scripts",
    WLAN_ROOT / "etc/networkd-dispatcher",
    WLAN_ROOT / "DEBIAN",
)

LOGGER_CALL_RE = re.compile(r"(^|[;&|()\s])logger(?:\s|$)")
LOGGER_LOG_PRIORITY_RE = re.compile(
    r"(?:^|\s)-p\s+[\"']?local0\.(?!debug\b)"
)
DYNAMIC_LINE_RE = (
    r"\$(?:\{)?(?:(?:BASH_)?LINENO|ln)(?:\[\d+\])?(?:\})?"
)
DYNAMIC_SOURCE_RE = re.compile(rf"\[[^]]+:{DYNAMIC_LINE_RE}\]")
CATEGORY_BEFORE_SOURCE_RE = re.compile(
    rf"\[(?![^]]*{DYNAMIC_LINE_RE})[^]]+\]\s*"
    rf"\[[^]]*{DYNAMIC_LINE_RE}[^]]*\]"
)
SHELL_LINENO_RE = re.compile(r"\$(?:\{)?(?:BASH_)?LINENO")
POSIX_SH_SHEBANGS = {
    "#!/bin/sh",
    "#!/usr/bin/sh",
    "#!/usr/bin/env sh",
}
SCRIPTS_REQUIRING_SHELL_LINENO = (
    "fake-hwclock.sh",
    "wifi_snmp_trap.sh",
)


def _logical_shell_lines(path):
    lines = path.read_text(errors="replace").splitlines()
    index = 0
    while index < len(lines):
        start_line = index + 1
        logical = lines[index]
        while logical.rstrip().endswith("\\") and index + 1 < len(lines):
            logical = logical.rstrip()[:-1] + " " + lines[index + 1].lstrip()
            index += 1
        yield start_line, logical
        index += 1


def _shell_files():
    for root in SHELL_ROOTS:
        for path in root.rglob("*"):
            if path.is_file():
                yield path


def _declares_posix_sh(path):
    first_line = path.read_text(errors="replace").splitlines()[:1]
    return bool(first_line and first_line[0].strip() in POSIX_SH_SHEBANGS)


def test_logger_log_shell_calls_include_dynamic_source_first():
    violations = []
    for path in _shell_files():
        for line_number, command in _logical_shell_lines(path):
            if command.lstrip().startswith("#"):
                continue
            if not LOGGER_CALL_RE.search(command):
                continue
            # rsyslog.conf의 local0.info selector에는 debug가 들어가지 않는다.
            # local1/2/3/7 등 개별 로그 포맷은 이 테스트 범위가 아니다.
            if not LOGGER_LOG_PRIORITY_RE.search(command):
                continue
            if not DYNAMIC_SOURCE_RE.search(command):
                violations.append(
                    f"{path.relative_to(WLAN_ROOT)}:{line_number}: "
                    "missing dynamic [file:line]"
                )
            elif CATEGORY_BEFORE_SOURCE_RE.search(command):
                violations.append(
                    f"{path.relative_to(WLAN_ROOT)}:{line_number}: "
                    "[file:line] must be the first message field"
                )

    assert violations == []


def test_changed_lineno_scripts_use_a_compatible_shell():
    violations = []
    scripts_root = WLAN_ROOT / "usr/local/scripts"
    for script_name in SCRIPTS_REQUIRING_SHELL_LINENO:
        path = scripts_root / script_name
        if _declares_posix_sh(path) and SHELL_LINENO_RE.search(path.read_text()):
            violations.append(
                f"{script_name}: LINENO is unavailable with a POSIX sh shebang"
            )

    assert violations == []


def test_wifi_logger_stat_does_not_write_unstructured_stdout():
    tree = ast.parse(LOGGER_STAT.read_text(), filename=str(LOGGER_STAT))
    print_lines = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "print"
    ]

    assert print_lines == []


def test_shell_logger_services_suppress_unstructured_stdio():
    violations = []
    for unit_name in STRUCTURED_LOG_UNITS:
        unit = (SYSTEMD_ROOT / unit_name).read_text()
        if "StandardOutput=null" not in unit:
            violations.append(f"{unit_name}: StandardOutput must be null")
        if "StandardError=null" not in unit:
            violations.append(f"{unit_name}: StandardError must be null")

    assert violations == []
