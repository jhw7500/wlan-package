"""whelp must remain useful through the first-line FTP reply boundary."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


HANDLERS = Path(__file__).resolve().parents[4] / "opt/ftpcmd/bin"
WHELP = HANDLERS / "whelp"
COMMANDS = (
    "getifstate", "ifcdown", "ifcup", "rst", "wconnect", "wconnectraw",
    "wfreq", "wgetifstate", "whelp", "wifcdown", "wifcup", "wntp", "wpsk",
    "wrst", "wscan", "wssid", "wstatus", "wtime",
)


def run_help(*args, handler=WHELP):
    return subprocess.run(
        [sys.executable, str(handler), *args], capture_output=True, text=True,
        timeout=5, cwd="/", env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )


def reply(result):
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert result.stdout.endswith("\n")
    lines = result.stdout.splitlines()
    assert len(lines) == 1
    line = lines[0]
    assert line and all(0x20 <= ord(char) <= 0x7E for char in line)
    assert len(line.encode("ascii")) <= 256
    return line


def test_index_lists_all_shipped_executable_handlers():
    text = reply(run_help())
    listed = text.removeprefix("Commands: ").split(";", 1)[0].split()
    installed = sorted(path.name for path in HANDLERS.iterdir()
                       if path.is_file() and os.access(path, os.X_OK))
    assert listed == installed == sorted(COMMANDS)
    assert "quote whelp <command>" in text


@pytest.mark.parametrize("command", COMMANDS)
def test_each_help_reply_survives_dispatcher(command):
    text = reply(run_help(command))
    assert "Usage: quote " + command in text
    assert "Example: quote " + command in text
    if command.startswith("w") and command not in ("whelp", "wntp", "wrst", "wtime"):
        assert "0|1|mlan0|mlan1" in text


@pytest.mark.parametrize("command", ("rst", "wrst"))
def test_reboot_help_explains_whole_board_effect(command):
    assert "whole board" in reply(run_help(command))


def test_raw_help_does_not_claim_connection_or_save_completion():
    text = reply(run_help("wconnectraw"))
    assert "accepted" in text
    assert "wstatus" in text and "wssid" in text


@pytest.mark.parametrize("command", ("wconnect", "wpsk"))
def test_staged_psk_help_requires_an_explicit_target_ssid(command):
    text = reply(run_help(command))
    assert "staged PSK requires explicit SSID" in text


@pytest.mark.parametrize("args", (("missing",), ("../rst",), ("rst;reboot",),
                                  ("wstatus", "0")))
def test_invalid_topics_or_extra_arguments_fail(args):
    result = run_help(*args)
    assert result.returncode == 2
    assert "quote whelp" in result.stderr


def test_help_uses_its_own_directory_and_never_executes_handlers(tmp_path):
    directory = tmp_path / "bin"
    directory.mkdir()
    shutil.copytree(HANDLERS.parent / "lib", tmp_path / "lib")
    helper = directory / "whelp"
    shutil.copyfile(WHELP, helper)
    helper.chmod(0o755)
    marker = tmp_path / "executed"
    command = directory / "wstatus"
    command.write_text("#!/bin/sh\nprintf executed > '" + str(marker) + "'\n")
    command.chmod(0o755)
    hidden = directory / "rst"
    hidden.write_text("#!/bin/sh\nexit 99\n")
    hidden.chmod(0o644)

    text = reply(run_help(handler=helper))
    assert text.removeprefix("Commands: ").split(";", 1)[0].split() == ["whelp", "wstatus"]
    reply(run_help("wstatus", handler=helper))
    assert run_help("rst", handler=helper).returncode == 2
    assert not marker.exists()
