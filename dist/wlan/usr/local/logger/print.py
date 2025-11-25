#!/usr/bin/env python3
import os
import sys

TTY_PATH = "/dev/ttyGS0"
CONSOLE_PATH = "/dev/console"

COLOR_MAP = {
    "black":   "30",
    "red":     "31",
    "green":   "32",
    "yellow":  "33",
    "blue":    "34",
    "magenta": "35",
    "cyan":    "36",
    "white":   "37",
    "bold":    "1",
    "none":    "",
}


def unescape_basic(s: str) -> str:
    s = s.replace(r"\r", "\r")
    s = s.replace(r"\n", "\n")
    s = s.replace(r"\t", "\t")
    s = s.replace(r"\e", "\x1b")
    return s


def build_payload(color_arg: str, text: str) -> str:
    color_arg = color_arg.lower()

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

    text = unescape_basic(text)

    msg = f"{prefix}{text}{reset}"
    if not text.endswith("\n"):
        msg += "\r\n"

    return msg


def write_to_console(msg: str):
    """ /dev/console 로 직접 출력 """
    try:
        fd = os.open(CONSOLE_PATH, os.O_WRONLY | os.O_NONBLOCK)
    except OSError:
        return

    try:
        os.write(fd, msg.encode("utf-8", "replace"))
    except OSError:
        pass
    finally:
        os.close(fd)


def write_to_usb(msg: str):
    try:
        fd = os.open(TTY_PATH, os.O_WRONLY | os.O_NONBLOCK)
    except OSError:
        return

    try:
        os.write(fd, msg.encode("utf-8", "replace"))
    except OSError:
        pass
    finally:
        os.close(fd)


def main():
    if len(sys.argv) < 2:
        sys.exit(1)

    color_arg = sys.argv[1]
    if len(sys.argv) >= 3:
        text = " ".join(sys.argv[2:])
    else:
        text = "test"

    msg = build_payload(color_arg, text)

    # ------------------------------------------
    # 1) /dev/console로 echo
    # ------------------------------------------
    ### ADDED: /dev/console 직접 write
    write_to_console(msg)

    # ------------------------------------------
    # 2) USB(ttyGS0)로 출력
    # ------------------------------------------
    write_to_usb(msg)


if __name__ == "__main__":
    main()
