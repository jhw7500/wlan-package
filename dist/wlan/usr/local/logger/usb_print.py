#!/usr/bin/env python3
import os
import sys

TTY_PATH = "/dev/ttyGS0"

COLOR_MAP = {
    "black":   "30",
    "red":     "31",
    "green":   "32",
    "yellow":  "33",
    "blue":    "34",
    "magenta": "35",
    "cyan":    "36",
    "white":   "37",
    "bold":    "1",    # 단독 사용 시 굵게
    "none":    "",     # 색 없음
}


def unescape_basic(s: str) -> str:
    # 문자열에 들어온 \r \n \t \e 를 실제 제어문자로 치환
    # 예: "\\r\\ntest" -> "\r\ntest"
    s = s.replace(r"\r", "\r")
    s = s.replace(r"\n", "\n")
    s = s.replace(r"\t", "\t")
    s = s.replace(r"\e", "\x1b")
    return s


def build_payload(color_arg: str, text: str) -> bytes:
    color_arg = color_arg.lower()

    # 숫자 직접 넣을 수도 있게 (예: 31, 32)
    if color_arg.isdigit():
        code = color_arg
    else:
        code = COLOR_MAP.get(color_arg, "")

    if code:
        prefix = f"\033[{code}m"
        reset = "\033[0m"
    else:
        prefix = ""
        reset = ""

    # 텍스트 안의 \r, \n 등을 실제 제어문자로 변환
    text = unescape_basic(text)

    msg = f"{prefix}{text}{reset}"
    # 마지막에 CRLF 하나 붙여줌
    if not text.endswith("\n"):
        msg += "\r\n"

    return msg.encode("utf-8", "replace")


def main():
    if len(sys.argv) < 2:
        sys.exit(1)

    color_arg = sys.argv[1]
    if len(sys.argv) >= 3:
        text = " ".join(sys.argv[2:])
    else:
        text = "test"

    payload = build_payload(color_arg, text)

    try:
        fd = os.open(TTY_PATH, os.O_WRONLY | os.O_NONBLOCK)
    except OSError:
        # USB 안 꽂혔거나 아직 준비 안 된 상태 → 그냥 종료 (hang 방지)
        sys.exit(1)

    try:
        try:
            os.write(fd, payload)
        except OSError:
            # EAGAIN 등은 무시
            pass
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
