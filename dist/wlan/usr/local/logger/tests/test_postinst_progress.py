"""postinst 설치 진행 표시의 실행 계약을 검증한다."""

import os
import subprocess
from pathlib import Path

import pytest


POSTINST = Path(__file__).resolve().parents[4] / "DEBIAN" / "postinst"


def test_installer_progress_rewrites_one_line_and_finishes_cleanly(tmp_path):
    """단계 갱신은 CR로 한 줄을 덮고, 마지막 출력은 그 줄을 닫아야 한다."""
    output = tmp_path / "print-calls.tsv"
    fake_print = tmp_path / "print.py"
    fake_print.write_text(
        "#!/bin/sh\n"
        "printf '%s\\t%s\\n' \"$1\" \"$2\" >> \"$INSTALL_PROGRESS_LOG\"\n",
        encoding="utf-8",
    )
    fake_print.chmod(0o755)

    command = r'''
source "$1" noop
INSTALL_PRINT_COMMAND="$2"
INSTALL_PROGRESS_TOTAL=3
INSTALL_PROGRESS_CURRENT=0
installer_progress_start
installer_progress_step
installer_progress_step
installer_progress_step
installer_progress_done
'''
    env = os.environ.copy()
    env.update(
        {
            "DEBIAN_HAS_FRONTEND": "1",
            "DPKG_MAINTSCRIPT_PACKAGE": "wlan-proc",
            "INSTALL_PROGRESS_LOG": str(output),
        }
    )
    result = subprocess.run(
        [
            "bash",
            "-c",
            command,
            "postinst-progress-test",
            str(POSTINST),
            str(fake_print),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    calls = [tuple(line.split("\t", 1)) for line in output.read_text().splitlines()]
    assert calls == [
        ("cyan", "[installer] install start"),
        ("green", r"[installer] install  33% (1/3) .\r"),
        ("green", r"[installer] install  66% (2/3) ..\r"),
        ("green", r"[installer] install 100% (3/3) ...\r"),
        ("green", "[installer] install 100% (3/3) ... done"),
        ("cyan", "[installer] install finish"),
    ]


def test_configure_flow_has_six_progress_stages():
    source = POSTINST.read_text(encoding="utf-8")
    assert "INSTALL_PROGRESS_TOTAL=6" in source
    assert source.count("\n  installer_progress_step\n") == 6


def test_configure_progress_follows_partition_and_service_enable_work():
    source = POSTINST.read_text(encoding="utf-8")
    configure = source.split("\nconfigure)\n", 1)[1].split("\nupgrade)\n", 1)[0]
    step_positions = []
    cursor = 0
    needle = "\n  installer_progress_step\n"
    while (position := configure.find(needle, cursor)) != -1:
        step_positions.append(position)
        cursor = position + len(needle)

    assert configure.index('/usr/local/scripts/auto_fs_sizeup.sh "$EMMC_NUM" 3') < step_positions[0]
    assert configure.index("systemctl enable watchdog switchd") < step_positions[1]


def test_installer_ignores_ambient_print_command():
    env = os.environ.copy()
    env.update(
        {
            "DEBIAN_HAS_FRONTEND": "1",
            "DPKG_MAINTSCRIPT_PACKAGE": "wlan-proc",
            "INSTALL_PRINT_COMMAND": "/tmp/untrusted-print-command",
        }
    )
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1" noop; printf "%s" "$INSTALL_PRINT_COMMAND" >&2',
            "postinst-command-test",
            str(POSTINST),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert result.stderr.splitlines()[-1] == "/usr/local/logger/print.py"


@pytest.mark.parametrize("helper_exists", [False, True], ids=["missing", "failing"])
@pytest.mark.parametrize("logger_status", [0, 1], ids=["logger-ok", "logger-fails"])
def test_installer_print_failure_warns_stderr_and_syslog_without_failing(
    tmp_path, helper_exists, logger_status
):
    helper = tmp_path / "print.py"
    if helper_exists:
        helper.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        helper.chmod(0o755)
    output = tmp_path / "logger-calls.tsv"
    logger = tmp_path / "logger"
    logger.write_text(
        "#!/bin/sh\n"
        "printf '%s\\t%s\\t%s\\n' \"$1\" \"$2\" \"$3\" >> \"$INSTALL_WARNING_LOG\"\n"
        f"exit {logger_status}\n",
        encoding="utf-8",
    )
    logger.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "PATH": str(tmp_path) + os.pathsep + env["PATH"],
            "DEBIAN_HAS_FRONTEND": "1",
            "DPKG_MAINTSCRIPT_PACKAGE": "wlan-proc",
            "INSTALL_WARNING_LOG": str(output),
        }
    )
    result = subprocess.run(
        [
            "bash",
            "-ec",
            'source "$1" noop 2>/dev/null; INSTALL_PRINT_COMMAND="$2"; installer_print cyan "install start"',
            "postinst-fallback-test",
            str(POSTINST),
            str(helper),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "print.py failed" in result.stderr
    assert result.stderr.isascii()
    assert output.read_text().splitlines() == [f"-p\tlocal0.warn\t{result.stderr.strip()}"]
