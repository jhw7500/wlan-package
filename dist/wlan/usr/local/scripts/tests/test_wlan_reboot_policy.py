"""wlan_reboot_policy의 최종 실패 신호 보존 계약."""

import os
import re
import subprocess
from pathlib import Path


WLAN_ROOT = Path(__file__).resolve().parents[4]
SCRIPT = WLAN_ROOT / "usr/local/scripts/wlan_reboot_policy.sh"


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def _replace_function(script: str, name: str, body: str) -> str:
    """위험한 외부 경계만 테스트 더블로 바꾸고 실제 정책 흐름은 유지한다."""
    pattern = rf"(?ms)^{re.escape(name)}\(\) \{{\n.*?^\}}\n"
    replacement = f"{name}() {{\n{body}\n}}\n"
    patched, count = re.subn(pattern, replacement, script, count=1)
    assert count == 1, f"{name} 함수를 찾지 못했다"
    return patched


def test_final_reboot_failure_preserves_do_reboot_status(tmp_path):
    """모든 재부팅 경로의 최종 127이 성공(0)으로 뒤집히면 안 된다."""
    policy = _replace_function(
        SCRIPT.read_text(encoding="utf-8"),
        "do_reboot",
        '  return "$TEST_REBOOT_RC"',
    )
    policy = _replace_function(policy, "log_kmsg", "  :")

    runnable = tmp_path / "wlan_reboot_policy.sh"
    _write_executable(runnable, policy)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    capture = tmp_path / "policy.log"
    for name in ("sync", "timeout"):
        _write_executable(fake_bin / name, "#!/bin/sh\nexit 0\n")
    _write_executable(
        fake_bin / "logger",
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$LOGGER_CAPTURE"\n',
    )

    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "LOGGER_CAPTURE": str(capture),
        "TEST_REBOOT_RC": "127",
        "STATE_DIR": str(tmp_path / "state"),
        "RUN_DIR": str(tmp_path / "run"),
    }
    result = subprocess.run(
        [
            "bash",
            str(runnable),
            "--force",
            "--source",
            "pytest",
            "--reason",
            "controlled reboot failure",
        ],
        env=env,
        text=True,
        capture_output=True,
        timeout=5,
    )

    assert result.returncode == 127, (
        "do_reboot의 최종 실패 상태를 상위 호출자에게 그대로 반환해야 한다: "
        f"rc={result.returncode}, stderr={result.stderr!r}"
    )
    assert "reboot: failed (rc=127)" in capture.read_text(encoding="utf-8")
