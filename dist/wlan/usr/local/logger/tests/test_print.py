"""print.py의 build_payload / unescape_basic 단위 테스트.

build_payload는 파일 I/O(/dev/console, /dev/ttyGS0 write)와 분리된 순수 함수라
fixture 없이 직접 검증한다. 모듈명이 내장 함수와 겹치는 `print`라 importlib로
명시 로드한다.

핵심 계약은 "줄을 언제 닫는가" 세 갈래다:

  평범한 텍스트   -> 끝에 CRLF 를 붙인다 (콘솔 한 줄 = 한 메시지)
  `\n` 으로 끝남  -> 이미 닫혔으므로 아무것도 붙이지 않는다
  `\r` 로 끝남    -> 줄을 닫지 않고 컬럼 0 으로만 돌아간다 (진행 표시 in-place)

마지막 갈래가 깨지면 factory_reset.sh 의 진행 표시가 갱신마다 새 줄을 쌓아,
유닛 40개에 81줄이 찍힌다. 그래서 대조군 테스트를 같이 둔다.
"""
import importlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
_print_mod = importlib.import_module("print")
build_payload = _print_mod.build_payload
unescape_basic = _print_mod.unescape_basic

GREEN = "\033[32m"
RESET = "\033[0m"


def test_plain_text_gets_crlf():
    assert build_payload("green", "hello") == f"{GREEN}hello{RESET}\r\n"


def test_text_ending_with_newline_is_not_terminated_again():
    assert build_payload("green", r"hello\n") == f"{GREEN}hello\n{RESET}"


def test_text_ending_with_cr_is_not_terminated():
    """진행 표시 계약 — 줄을 닫지 않고 컬럼 0 으로만 돌아간다."""
    assert build_payload("green", r"reset..\r") == f"{GREEN}reset..{RESET}\r"


def test_cr_comes_after_the_color_reset():
    """CR 이 reset escape 앞에 오면 다음 write 가 그 escape 위에 겹쳐 쓴다."""
    payload = build_payload("green", r"reset..\r")
    assert payload.endswith("\r")
    assert payload.index(RESET) < payload.rindex("\r")


def test_repeated_inplace_updates_stay_on_one_line():
    """factory_reset.sh 가 실제로 의존하는 성질 — 이어 붙여도 개행이 없다."""
    stream = "".join(
        build_payload("green", "reset" + "." * i + r"\r") for i in range(1, 6)
    )
    assert "\n" not in stream


def test_without_cr_the_same_updates_stack_into_lines():
    """대조군 — CR 이 없으면 갱신마다 줄이 쌓인다(회귀 시 위 테스트가 이 모양이 된다)."""
    stream = "".join(build_payload("green", "reset" + "." * i) for i in range(1, 6))
    assert stream.count("\n") == 5


def test_unknown_and_none_colors_emit_no_escape():
    assert build_payload("chartreuse", "hi") == "hi\r\n"
    assert build_payload("none", "hi") == "hi\r\n"


def test_numeric_color_is_passed_through():
    assert build_payload("35", "hi") == f"\033[35mhi{RESET}\r\n"


def test_color_name_is_case_insensitive():
    assert build_payload("GREEN", "hi") == build_payload("green", "hi")


def test_unescape_basic_handles_the_documented_escapes():
    assert unescape_basic(r"a\rb\nc\td\e") == "a\rb\nc\td\x1b"
