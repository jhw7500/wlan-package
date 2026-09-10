"""postinst 설치 진행 표시의 실행 계약을 검증한다."""

import os
import subprocess
from pathlib import Path


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
            "INSTALL_PRINT_COMMAND": str(fake_print),
            "INSTALL_PROGRESS_LOG": str(output),
        }
    )
    result = subprocess.run(
        ["bash", "-c", command, "postinst-progress-test", str(POSTINST)],
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
