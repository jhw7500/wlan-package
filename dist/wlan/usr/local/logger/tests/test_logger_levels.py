"""sUTILS.Logger.message() 레벨 이름 검사(#470).

Logger.message()는 levels 에 없는 이름이면 원문을 버리고 "Unknown logging level" 한 줄만 err 로
남긴다. 그래서 소스의 .message(<레벨>, ...) 호출이 넘기는 문자열 레벨은 모두 levels 에 있어야 한다.

다른 시험이 수집 때 sys.modules["sUTILS"] 를 MagicMock 으로 채우므로, 실제 sUTILS.py 를 파일 경로로
따로 읽는다. 호스트에 없는 런타임 의존성(paho·serial 등)만 이 시험 동안 가짜로 두고, SysLogHandler 는
메모리 handler 로 바꾼다(/dev/log 불필요).
"""

import ast
import importlib
import importlib.util
import logging
import logging.handlers
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

LOGGER_DIR = Path(__file__).resolve().parents[1]
WLAN_ROOT = Path(__file__).resolve().parents[4]


class _Capture(logging.Handler):
    def __init__(self, *args, **kwargs):  # SysLogHandler(address=..., facility=...) signature
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def real_logger(monkeypatch):
    for name in ("paho", "paho.mqtt", "paho.mqtt.client", "serial", "numpy"):
        try:
            importlib.import_module(name)
        except ImportError:
            monkeypatch.setitem(sys.modules, name, MagicMock())
    spec = importlib.util.spec_from_file_location("_sutils_t470", LOGGER_DIR / "sUTILS.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(logging.handlers, "SysLogHandler", _Capture)
    root = logging.getLogger()
    saved = root.handlers[:]
    try:
        yield module.Logger("t470")
    finally:
        for handler in root.handlers[:]:
            root.removeHandler(handler)
        for handler in saved:
            root.addHandler(handler)


def _literal_levels():
    """Every .message(<str>, <msg>, ...) call in the package's Python sources, tests excluded."""
    found = []
    for path in sorted(WLAN_ROOT.rglob("*.py")):
        if "tests" in path.relative_to(WLAN_ROOT).parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "message" and len(node.args) >= 2
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                found.append((f"{path.relative_to(WLAN_ROOT)}:{node.lineno}", node.args[0].value))
    return found


def test_every_literal_message_level_is_a_logger_level(real_logger):
    levels = set(real_logger.levels)
    calls = _literal_levels()
    # The scan must see the package's calls; an empty scan would pass for nothing.
    assert len(calls) > 100 and any(level == "warn" for _, level in calls)
    unknown = [f"{where} {level!r}" for where, level in calls if level not in levels]
    assert unknown == [], "levels unknown to sUTILS.Logger (message is dropped): " + ", ".join(unknown)


def test_a_warn_message_is_recorded_with_its_text_at_warning_level(real_logger):
    real_logger.message("warn", "[mlan0] radio log off after error: boom", ("x.py", 1))

    assert [(r.levelno, r.getMessage()) for r in real_logger.syslog_handler.records] == [
        (logging.WARNING, "[mlan0] radio log off after error: boom")]
