"""wlan_reboot_policy의 최종 실패 신호 보존 계약 + Reset Cause 파일 기록(#304)."""

import os
import re
import subprocess
from pathlib import Path

import pytest


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


def _run_policy(tmp_path, args, reboot_rc="0", uptime=None):
    """do_reboot/log_kmsg를 더블로 바꾸고 정책을 실제로 실행, (rc, cause 파일 경로)를 준다."""
    # do_reboot 더블: 호출 시점에 cause 파일이 이미 있는지 기록한다 — 실제 HW에서는
    # graceful reboot가 돌아오지 않으므로 do_reboot "뒤"의 기록은 영원히 안 쓰인다.
    policy = _replace_function(
        SCRIPT.read_text(encoding="utf-8"),
        "do_reboot",
        '  [ -f "$RESET_CAUSE_PATH" ] && printf seen > "$ORDER_MARK"\n'
        '  return "$TEST_REBOOT_RC"',
    )
    policy = _replace_function(policy, "log_kmsg", "  :")
    if uptime is not None:
        policy = _replace_function(policy, "get_uptime_sec", f"  echo {uptime}")
    runnable = tmp_path / "wlan_reboot_policy.sh"
    _write_executable(runnable, policy)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    for name in ("sync", "timeout", "logger"):
        _write_executable(fake_bin / name, "#!/bin/sh\nexit 0\n")
    cause = tmp_path / "run" / "opc" / "reset_cause"
    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "TEST_REBOOT_RC": reboot_rc,
        "STATE_DIR": str(tmp_path / "state"),
        "RUN_DIR": str(tmp_path / "run" / "policy"),
        "RESET_CAUSE_PATH": str(cause),
        "ORDER_MARK": str(tmp_path / "order.mark"),
    }
    result = subprocess.run(
        ["bash", str(runnable), *args],
        env=env, text=True, capture_output=True, timeout=5,
    )
    return result, cause


@pytest.mark.parametrize(
    "args, expected",
    [
        # wifi_checker: link / fw_crash → 0x10, station_dump_fault → 0x11
        (["--source", "wifi_checker", "--iface", "mlan0", "--reason", "wifi_checker fatal: link"], "0x10"),
        (["--source", "wifi_checker", "--iface", "mlan0", "--reason", "wifi_checker fatal: fw_crash"], "0x10"),
        (["--source", "wifi_checker", "--iface", "mlan0", "--reason", "wifi_checker fatal: station_dump_fault"], "0x11"),
        # wlan_fw_watch 드라이버 wedge → 0x12
        (["--source", "wlan_fw_watch", "--reason", "wlan_fw_watch fatal: wifi_status=11 terminal"], "0x12"),
        # 과열 복구 재부팅 → 0x20 (--force 경로)
        (["--force", "--source", "wifi_logger_temp", "--reason", "overtemp recovered -> reboot cpu=70"], "0x20"),
        # wlan_emergency_reboot.service → 0x30
        (["--source", "wifi_init", "--reason", "wifi_init persistent failure (excluding one cold MCS lifecycle retry)"], "0x30"),
    ],
)
def test_reset_cause_written_before_reboot(tmp_path, args, expected):
    """#304: 승인된 재부팅은 do_reboot 전에 opcd가 읽을 Reset Cause를 한 줄로 남긴다."""
    result, cause = _run_policy(tmp_path, ["--force", *args] if "--force" not in args else args)
    assert result.returncode == 0, result.stderr
    assert cause.exists(), "승인된 재부팅인데 /run/opc/reset_cause가 없다"
    text = cause.read_text(encoding="utf-8")
    assert text == f"{expected}\n", f"한 줄 '{expected}\\n' 이어야 한다: {text!r}"
    assert (tmp_path / "order.mark").exists(), "cause 파일은 do_reboot 호출 전에 존재해야 한다"


def test_reset_cause_absent_for_unknown_source(tmp_path):
    """매핑에 없는 source는 파일을 남기지 않는다 — opcd가 0x0002(SYSTEM)로 처리."""
    result, cause = _run_policy(
        tmp_path, ["--force", "--source", "pytest", "--reason", "controlled reboot"])
    assert result.returncode == 0, result.stderr
    assert not cause.exists()


def test_reset_cause_not_written_when_refused(tmp_path):
    """거부된 요청(uptime 게이트)은 stale cause를 남기면 안 된다."""
    result, cause = _run_policy(
        tmp_path,
        ["--source", "wifi_checker", "--iface", "mlan0", "--reason", "wifi_checker fatal: link"],
        uptime="5",
    )
    assert result.returncode == 10, result.stderr
    assert not cause.exists()


def test_reset_cause_write_failure_does_not_block_reboot(tmp_path):
    """cause 파일을 못 써도(경로가 파일이라 mkdir 불가) 재부팅 자체는 진행된다."""
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "opc").write_text("not a dir", encoding="utf-8")
    result, _ = _run_policy(
        tmp_path, ["--force", "--source", "wlan_fw_watch", "--reason", "wlan_fw_watch fatal: wedge"])
    assert result.returncode == 0, result.stderr


def test_reset_cause_not_written_when_loop_refused(tmp_path):
    """loop 게이트(exit 11)로 거부돼도 stale cause를 남기지 않는다."""
    state = tmp_path / "state"
    state.mkdir()
    import time
    (state / "reboot_policy_mlan0.state").write_text(f"{int(time.time())} 3\n", encoding="utf-8")
    result, cause = _run_policy(
        tmp_path,
        ["--source", "wifi_checker", "--iface", "mlan0", "--reason", "wifi_checker fatal: link"],
        uptime="9999",
    )
    assert result.returncode == 11, result.stderr
    assert not cause.exists()


def test_reset_cause_removed_when_all_reboot_stages_fail(tmp_path):
    """do_reboot가 3단계 모두 실패(127)하면 cause 파일을 지운다 — 나중의 운용자
    재부팅이 옛 WLAN 원인을 통지하면 안 된다(opcd는 파일 없음 → 0x0002)."""
    result, cause = _run_policy(
        tmp_path,
        ["--force", "--source", "wlan_fw_watch", "--reason", "wlan_fw_watch fatal: wedge"],
        reboot_rc="127",
    )
    assert result.returncode == 127, result.stderr
    assert (tmp_path / "order.mark").exists(), "실패 전에는 기록돼 있었어야 한다"
    assert not cause.exists(), "재부팅 실패 후 stale cause가 남아 있다"
