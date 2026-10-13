"""wlan_reboot_policy의 최종 실패 신호 보존 계약 + Reset Cause 파일 기록(#304)."""

import os
import re
import shutil
import signal
import subprocess
import time
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


def _execute_isolated_policy(args, env, timeout):
    proc = subprocess.Popen(
        args, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate(timeout=3)
        raise
    return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)


def test_final_reboot_failure_preserves_do_reboot_status(tmp_path):
    """모든 재부팅 경로의 최종 127이 성공(0)으로 뒤집히면 안 된다."""
    policy = _replace_function(
        SCRIPT.read_text(encoding="utf-8"),
        "do_reboot",
        '  return "$TEST_REBOOT_RC"',
    )
    policy = _replace_function(policy, "log_kmsg", "  :")
    policy = _replace_function(policy, "systemd_is_running", "  return 1")

    runnable = tmp_path / "wlan_reboot_policy.sh"
    _write_executable(runnable, policy)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    capture = tmp_path / "policy.log"
    for name in ("sync",):
        _write_executable(fake_bin / name, "#!/bin/sh\nexit 0\n")
    snapshot = tmp_path / "snapshot.sh"
    _write_executable(snapshot, "#!/bin/sh\nexit 0\n")
    policy = policy.replace("_snap=/usr/local/scripts/journald_snapshot.sh", f"_snap={snapshot}")
    _write_executable(runnable, policy)
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


def _run_policy(tmp_path, args, reboot_rc="0", uptime=None, io_fault=None):
    """do_reboot/log_kmsg를 더블로 바꾸고 정책을 실제로 실행, (rc, cause 파일 경로)를 준다."""
    # do_reboot 더블: 호출 시점에 cause 파일이 이미 있는지 기록한다 — 실제 HW에서는
    # graceful reboot가 돌아오지 않으므로 do_reboot "뒤"의 기록은 영원히 안 쓰인다.
    policy = _replace_function(
        SCRIPT.read_text(encoding="utf-8"),
        "do_reboot",
        '  printf attempted > "$REBOOT_MARK"\n'
        '  [ -f "$RESET_CAUSE_PATH" ] && printf seen > "$ORDER_MARK"\n'
        '  return "$TEST_REBOOT_RC"',
    )
    policy = _replace_function(policy, "log_kmsg", "  return 14" if io_fault == "kmsg_error" else "  :")
    policy = _replace_function(policy, "systemd_is_running", "  return 1")
    if uptime is not None:
        policy = _replace_function(policy, "get_uptime_sec", f"  echo {uptime}")
    if io_fault == "state_fifo":
        # A private FIFO reproduces a stalled regular-file read. Override only
        # its stat result; the actual open/read and deadline still run.
        policy = policy.replace("set -euo pipefail", """set -euo pipefail
[() {
  if builtin [ "${1:-}" = "-f" ] && builtin [ "${2:-}" = "$FAULT_STATE_PATH" ]; then
    return 0
  fi
  builtin [ "$@"
}
export -f [
""", 1)
    runnable = tmp_path / "wlan_reboot_policy.sh"
    _write_executable(runnable, policy)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    for name in ("sync", "logger"):
        _write_executable(fake_bin / name, "#!/bin/sh\nexit 0\n")
    if io_fault in ("logger_error", "sync_error"):
        name = io_fault.removesuffix("_error")
        _write_executable(fake_bin / name, "#!/bin/sh\nexit 23\n")
    elif io_fault in ("logger_hang", "sync_hang"):
        name = io_fault.removesuffix("_hang")
        _write_executable(
            fake_bin / name,
            "#!/bin/sh\ntrap '' TERM\nwhile :; do /bin/sleep 1; done\n",
        )
    elif io_fault == "cleanup_hang":
        _write_executable(fake_bin / "rm", "#!/bin/sh\ntrap '' TERM\nwhile :; do /bin/sleep 1; done\n")
    if io_fault and io_fault.startswith("mfg_"):
        wifi = tmp_path / "wifi.json"
        wifi.write_text('{"global":{"MOD_PARA":"test.conf"}}\n')
        firmware = tmp_path / "firmware"
        firmware.mkdir()
        mode = {"mfg_one": "1", "mfg_invalid": "invalid"}.get(io_fault, "0")
        (firmware / "test.conf").write_text(f"mfg_mode={mode}\n")
        hang = "#!/bin/sh\ntrap '' TERM\nwhile :; do /bin/sleep 1; done\n"
        if io_fault == "mfg_orphan":
            hang = "#!/bin/sh\ntrap '' TERM\nprintf '%s\\n' \"$$\" > \"$FAULT_PID_FILE\"\nwhile :; do /bin/sleep 1; done\n"
        _write_executable(fake_bin / "jq", hang if io_fault in ("mfg_hang", "mfg_orphan") else
                          "#!/bin/sh\nprintf 'test.conf\\n'\n")
        policy = policy.replace("/usr/local/etc/wifi_init_conf.json", str(wifi))
        policy = policy.replace("/lib/firmware/", str(firmware) + "/")
    # Use the actual deadline utility. Only the snapshot path, terminal
    # reboot and host /dev/kmsg boundary are replaced.
    snapshot = tmp_path / "snapshot.sh"
    snapshot_body = "#!/bin/sh\nexit 0\n"
    if io_fault == "snapshot_hang":
        snapshot_body = "#!/bin/sh\ntrap '' TERM\nwhile :; do /bin/sleep 1; done\n"
    _write_executable(snapshot, snapshot_body)
    policy = policy.replace("_snap=/usr/local/scripts/journald_snapshot.sh", f"_snap={snapshot}")
    _write_executable(runnable, policy)
    cause = tmp_path / "run" / "opc" / "reset_cause"
    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "TEST_REBOOT_RC": reboot_rc,
        "STATE_DIR": str(tmp_path / "state"),
        "RUN_DIR": str(tmp_path / "run" / "policy"),
        "RESET_CAUSE_PATH": str(cause),
        "ORDER_MARK": str(tmp_path / "order.mark"),
        "REBOOT_MARK": str(tmp_path / "reboot.mark"),
        "FAULT_STATE_PATH": str(tmp_path / "state/reboot_policy_global.state"),
        "FAULT_PID_FILE": str(tmp_path / "leaf.pid"),
    }
    result = _execute_isolated_policy(
        ["bash", str(runnable), *args],
        env, 15 if io_fault else 5,
    )
    return result, cause


@pytest.mark.parametrize("io_fault", ["logger_error", "kmsg_error", "sync_error"])
def test_diagnostic_io_failure_does_not_cancel_approved_reboot(tmp_path, io_fault):
    """An I/O error must not turn approved recovery into an early script exit."""
    result, _ = _run_policy(
        tmp_path, ["--force", "--source", "wlan_fw_watch", "--reason", "driver unusable"],
        io_fault=io_fault,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "reboot.mark").read_text() == "attempted"


@pytest.mark.parametrize("io_fault", ["logger_hang", "sync_hang"])
def test_stalled_diagnostic_io_does_not_wait_forever(tmp_path, io_fault):
    """A TERM-resistant logging/sync child cannot stall the recovery policy."""
    result, _ = _run_policy(
        tmp_path, ["--force", "--source", "pytest", "--reason", "controlled I/O stall"],
        io_fault=io_fault,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "reboot.mark").exists()


def test_full_state_and_cause_devices_do_not_cancel_recovery(tmp_path):
    """Real ENOSPC from /dev/full affects only private test symlinks."""
    state = tmp_path / "state"
    state.mkdir()
    (state / "reboot_policy_global.state").symlink_to("/dev/full")
    cause = tmp_path / "run/opc/reset_cause"
    cause.parent.mkdir(parents=True)
    cause.symlink_to("/dev/full")
    result, _ = _run_policy(
        tmp_path, ["--force", "--source", "wlan_fw_watch", "--reason", "driver unusable"],
        io_fault="storage_full",
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "reboot.mark").exists()


def test_blocked_cause_fifo_cannot_stall_recovery(tmp_path):
    """An unexpected cause-file node must not leave the policy blocked on open."""
    cause = tmp_path / "run/opc/reset_cause"
    cause.parent.mkdir(parents=True)
    os.mkfifo(cause)
    result, _ = _run_policy(
        tmp_path, ["--force", "--source", "wlan_fw_watch", "--reason", "driver unusable"],
        io_fault="cause_fifo",
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "reboot.mark").exists()


def test_stalled_state_read_cannot_block_approved_recovery(tmp_path):
    """The stat/open/read of the persistent budget belongs inside its deadline."""
    state = tmp_path / "state"
    state.mkdir()
    os.mkfifo(state / "reboot_policy_global.state")
    result, _ = _run_policy(
        tmp_path, ["--force", "--source", "pytest", "--reason", "controlled state read stall"],
        io_fault="state_fifo",
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "reboot.mark").exists()


def test_stalled_cause_cleanup_preserves_final_failure(tmp_path):
    """A failed reboot cannot be hidden by a hung best-effort cleanup command."""
    result, cause = _run_policy(
        tmp_path, ["--force", "--source", "wlan_fw_watch", "--reason", "driver unusable"],
        reboot_rc="127", io_fault="cleanup_hang",
    )
    assert result.returncode == 127, result.stderr
    assert (tmp_path / "reboot.mark").exists()
    assert cause.read_text() == "0x12\n", "a failed cleanup may leave the original cause"


@pytest.mark.parametrize("stored_count, expected_rc", [("bad-count", 14), ("0002", 11)])
def test_budget_parser_rejects_corruption_and_accepts_decimal_zero_padding(tmp_path, stored_count, expected_rc):
    import time
    state = tmp_path / "state"
    state.mkdir()
    (state / "reboot_policy_global.state").write_text(f"{int(time.time())} {stored_count}\n")
    result, _ = _run_policy(
        tmp_path, ["--source", "wifi_checker", "--reason", "link lost"], uptime="9999",
    )
    assert result.returncode == expected_rc, result.stderr
    assert (tmp_path / "reboot.mark").exists() == (expected_rc == 0)


@pytest.mark.parametrize("record", ["", "123\n", "123 1 extra\n", "123 bad\n"])
def test_invalid_budget_refuses_automatic_reboot(tmp_path, record):
    state = tmp_path / "state"
    state.mkdir()
    (state / "reboot_policy_global.state").write_text(record)
    result, cause = _run_policy(
        tmp_path, ["--source", "wifi_checker", "--reason", "link lost"], uptime="9999",
    )
    assert result.returncode == 14, result.stderr
    assert not (tmp_path / "reboot.mark").exists()
    assert not cause.exists()


def test_stalled_budget_refuses_automatic_reboot(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    os.mkfifo(state / "reboot_policy_global.state")
    result, cause = _run_policy(
        tmp_path, ["--source", "wifi_checker", "--reason", "link lost"],
        uptime="9999", io_fault="state_fifo",
    )
    assert result.returncode == 14, result.stderr
    assert not (tmp_path / "reboot.mark").exists()
    assert not cause.exists()


@pytest.mark.skipif(os.getuid() == 0, reason="Root bypasses the real EACCES fixture")
def test_unpersisted_budget_refuses_automatic_reboot(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    budget = state / "reboot_policy_global.state"
    budget.write_text("0 0\n")
    budget.chmod(0o444)
    state.chmod(0o500)
    try:
        result, cause = _run_policy(
            tmp_path, ["--source", "wifi_checker", "--reason", "link lost"], uptime="9999",
        )
        assert result.returncode == 14, result.stderr
        assert not (tmp_path / "reboot.mark").exists()
        assert not cause.exists()
    finally:
        state.chmod(0o755)
        budget.chmod(0o644)


def test_stalled_budget_write_preserves_old_record_and_refuses_reboot(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    budget = state / "reboot_policy_global.state"
    budget.write_text("0 0\n")
    result, cause = _run_policy(
        tmp_path, ["--source", "wifi_checker", "--reason", "link lost"],
        uptime="9999", io_fault="sync_hang",
    )
    assert result.returncode == 14, result.stderr
    assert budget.read_text() == "0 0\n"
    assert not (tmp_path / "reboot.mark").exists()
    assert not cause.exists()


@pytest.mark.parametrize("io_fault", ["mfg_hang", "mfg_invalid"])
def test_unknown_mfg_mode_refuses_without_waiting_forever(tmp_path, io_fault):
    """An unreadable MFG guard must finish with a distinct safe refusal."""
    result, _ = _run_policy(
        tmp_path, ["--source", "wifi_checker", "--reason", "link lost"],
        uptime="9999", io_fault=io_fault,
    )
    assert result.returncode == 13, result.stderr
    assert not (tmp_path / "reboot.mark").exists()


@pytest.mark.parametrize("args", [
    ["--force", "--source", "wifi_checker", "--reason", "link lost"],
    ["--source", "wifi_init", "--reason", "driver load failed"],
    ["--source", "wifi_logger_temp", "--reason", "temperature recovered"],
    ["--source", "wifi_checker", "--reason", "overtemp recovered"],
])
def test_existing_mfg_exceptions_skip_stalled_config_reads(tmp_path, args):
    result, _ = _run_policy(tmp_path, args, uptime="9999", io_fault="mfg_hang")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "reboot.mark").exists()


@pytest.mark.parametrize("io_fault, expected_rc", [("mfg_zero", 0), ("mfg_one", 12)])
def test_known_mfg_mode_preserves_original_guard(tmp_path, io_fault, expected_rc):
    result, _ = _run_policy(
        tmp_path, ["--source", "wifi_checker", "--reason", "link lost"],
        uptime="9999", io_fault=io_fault,
    )
    assert result.returncode == expected_rc, result.stderr
    assert (tmp_path / "reboot.mark").exists() == (expected_rc == 0)


def test_timeout_cannot_leave_term_resistant_grandchild_after_wrapper_exits(tmp_path):
    """TERM must not end the timeout monitor before its resistant leaf is killed."""
    result, _ = _run_policy(
        tmp_path, ["--source", "wifi_checker", "--reason", "link lost"],
        uptime="9999", io_fault="mfg_orphan",
    )
    pid = int((tmp_path / "leaf.pid").read_text())
    program = os.fsencode(tmp_path / "bin/jq")

    def leaf_running():
        try:
            proc = Path(f"/proc/{pid}")
            args = (proc / "cmdline").read_bytes().split(b"\0")
            return program in args and "\nState:\tZ" not in (proc / "status").read_text()
        except FileNotFoundError:
            return False

    try:
        assert result.returncode == 13, result.stderr
        end = time.monotonic() + 0.5
        while leaf_running() and time.monotonic() < end:
            time.sleep(0.01)
        assert not leaf_running(), "timeout left the TERM-resistant leaf alive"
    finally:
        if leaf_running():
            pidfd = os.pidfd_open(pid)
            try:
                if leaf_running():
                    signal.pidfd_send_signal(pidfd, signal.SIGKILL)
            finally:
                os.close(pidfd)


def test_term_resistant_snapshot_does_not_block_sync_and_reboot(tmp_path):
    """A journal helper ignoring TERM must be killed, not waited for indefinitely."""
    result, _ = _run_policy(
        tmp_path, ["--force", "--source", "pytest", "--reason", "controlled snapshot stall"],
        io_fault="snapshot_hang",
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "reboot.mark").exists()


def test_bad_logger_preserves_refusal_and_final_reboot_failure(tmp_path):
    """Logging errors cannot replace the caller-visible policy decision/status."""
    refused = tmp_path / "refused"
    refused.mkdir()
    result, _ = _run_policy(
        refused, ["--source", "wifi_checker", "--reason", "link lost"],
        uptime="5", io_fault="logger_error",
    )
    assert result.returncode == 10
    assert not (refused / "reboot.mark").exists()
    failed = tmp_path / "failed"
    failed.mkdir()
    result, cause = _run_policy(
        failed, ["--force", "--source", "wlan_fw_watch", "--reason", "driver unusable"],
        reboot_rc="127", io_fault="logger_error",
    )
    assert result.returncode == 127
    assert (failed / "reboot.mark").exists()
    assert not cause.exists()


@pytest.mark.parametrize("sync_fault", ["error", "hang"])
def test_every_reboot_stage_runs_when_logging_and_sync_fail(tmp_path, sync_fault):
    """Exercise the real escalation flow, replacing only destructive commands."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls = tmp_path / "reboot.calls"
    fake_reboot = '#!/bin/sh\nprintf "%s\\n" "$*" >> "$REBOOT_CALLS"\n'
    _write_executable(fake_bin / "reboot", fake_reboot)
    _write_executable(fake_bin / "systemctl", fake_reboot)
    _write_executable(fake_bin / "sleep", "#!/bin/sh\nexit 0\n")
    _write_executable(fake_bin / "logger", "#!/bin/sh\nexit 23\n")
    _write_executable(fake_bin / "sync", "#!/bin/sh\nexit 23\n" if sync_fault == "error" else
                      "#!/bin/sh\ntrap '' TERM\nwhile :; do /bin/sleep 1; done\n")
    snapshot = tmp_path / "snapshot.sh"
    _write_executable(snapshot, "#!/bin/sh\nexit 0\n")
    sysrq = tmp_path / "sysrq"
    trigger = tmp_path / "sysrq-trigger"
    sysrq.write_text("")
    trigger.write_text("")
    policy = _replace_function(SCRIPT.read_text(), "systemd_is_running", "  return 1")
    policy = _replace_function(policy, "log_kmsg", "  :")
    policy = policy.replace("/sbin/reboot", str(fake_bin / "reboot"))
    policy = policy.replace("/proc/sys/kernel/sysrq", str(sysrq))
    policy = policy.replace("/proc/sysrq-trigger", str(trigger))
    policy = policy.replace("_snap=/usr/local/scripts/journald_snapshot.sh", f"_snap={snapshot}")
    for path in ("/sbin/reboot", "/proc/sys/kernel/sysrq", "/proc/sysrq-trigger", "/dev/kmsg"):
        assert path not in policy, f"unsafe test boundary: {path}"
    runnable = tmp_path / "policy.sh"
    _write_executable(runnable, policy)
    cause = tmp_path / "reset_cause"
    env = os.environ | {"PATH": f"{fake_bin}:{os.environ['PATH']}",
        "STATE_DIR": str(tmp_path / "state"), "RUN_DIR": str(tmp_path / "run"),
        "RESET_CAUSE_PATH": str(cause), "REBOOT_CALLS": str(calls)}
    result = _execute_isolated_policy(
        ["/bin/bash", str(runnable), "--force", "--source", "wlan_fw_watch", "--reason", "driver unusable"],
        env, 10,
    )
    assert result.returncode == 127, result.stderr
    assert calls.read_text().splitlines() == ["", "-f"]
    assert sysrq.read_text() == "1\n"
    assert trigger.read_text() == "b\n"
    assert not cause.exists()


def test_missing_deadline_tool_skips_optional_io_instead_of_blocking(tmp_path):
    """Without timeout, optional I/O is skipped rather than run without a limit."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for name in ("basename", "grep", "sed", "tr", "date", "awk", "mkdir", "rmdir", "rm"):
        (fake_bin / name).symlink_to(shutil.which(name))
    hang = "#!/bin/sh\ntrap '' TERM\nwhile :; do /bin/sleep 1; done\n"
    for name in ("logger", "sync", "snapshot"):
        _write_executable(fake_bin / name, hang)
    policy = _replace_function(SCRIPT.read_text(), "systemd_is_running", "  return 1")
    policy = _replace_function(policy, "log_kmsg", "  :")
    policy = _replace_function(policy, "do_reboot", '  printf attempted > "$REBOOT_MARK"\n  return 0')
    policy = policy.replace("_snap=/usr/local/scripts/journald_snapshot.sh", f"_snap={fake_bin / 'snapshot'}")
    runnable = tmp_path / "policy.sh"
    _write_executable(runnable, policy)
    mark = tmp_path / "reboot.mark"
    env = os.environ | {"PATH": str(fake_bin), "STATE_DIR": str(tmp_path / "state"),
        "RUN_DIR": str(tmp_path / "run"), "RESET_CAUSE_PATH": str(tmp_path / "cause"),
        "REBOOT_MARK": str(mark)}
    result = _execute_isolated_policy(
        ["/bin/bash", str(runnable), "--force", "--source", "pytest", "--reason", "controlled missing timeout"],
        env, 3,
    )
    assert result.returncode == 0, result.stderr
    assert mark.read_text() == "attempted"


def test_blocked_stderr_cannot_stall_failed_log_warning(tmp_path):
    """The warning fallback itself must not block on a full journal/pipe sink."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for name in ("sync", "snapshot"):
        _write_executable(fake_bin / name, "#!/bin/sh\nexit 0\n")
    _write_executable(fake_bin / "logger", "#!/bin/sh\nexit 23\n")
    policy = _replace_function(SCRIPT.read_text(), "systemd_is_running", "  return 1")
    policy = _replace_function(policy, "log_kmsg", "  :")
    policy = _replace_function(policy, "do_reboot", '  printf attempted > "$REBOOT_MARK"\n  return 0')
    policy = policy.replace("_snap=/usr/local/scripts/journald_snapshot.sh", f"_snap={fake_bin / 'snapshot'}")
    runnable = tmp_path / "policy.sh"
    _write_executable(runnable, policy)
    mark = tmp_path / "reboot.mark"
    env = os.environ | {"PATH": f"{fake_bin}:{os.environ['PATH']}",
        "STATE_DIR": str(tmp_path / "state"), "RUN_DIR": str(tmp_path / "run"),
        "RESET_CAUSE_PATH": str(tmp_path / "cause"), "REBOOT_MARK": str(mark)}
    read_fd, write_fd = os.pipe()
    proc = None
    try:
        os.set_blocking(write_fd, False)
        while True:
            try:
                os.write(write_fd, b"x" * 65536)
            except BlockingIOError:
                break
        os.set_blocking(write_fd, True)
        proc = subprocess.Popen(
            ["/bin/bash", str(runnable), "--force", "--source", "pytest", "--reason", "controlled full stderr"],
            env=env, stdout=subprocess.DEVNULL, stderr=write_fd, start_new_session=True,
        )
        try:
            status = proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=3)
            pytest.fail("failed-log warning blocked the approved reboot on stderr")
        assert status == 0
        assert mark.read_text() == "attempted"
    finally:
        if proc is not None and proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=3)
        os.close(read_fd)
        os.close(write_fd)


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


def _systemd_policy(tmp_path, launcher):
    """Actual policy with a harmless reboot and external systemd boundary."""
    policy = _replace_function(
        SCRIPT.read_text(encoding="utf-8"), "do_reboot",
        '  printf "%s\\n" "$REASON" >> "$STATE_DIR/reboot.calls"\n'
        '  return 127',
    )
    policy = _replace_function(policy, "log_kmsg", "  :")
    # Only the manager-presence probe is controlled. The production handoff,
    # argument parser, limits, lock, cause handling and exit status stay real.
    policy = _replace_function(policy, "systemd_is_running", "  return 0")
    runnable = tmp_path / "policy.sh"
    _write_executable(runnable, policy)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for name in ("sync", "logger"):
        _write_executable(fake_bin / name, "#!/bin/sh\nexit 0\n")
    snapshot = tmp_path / "snapshot.sh"
    _write_executable(snapshot, "#!/bin/sh\nexit 0\n")
    policy = policy.replace("_snap=/usr/local/scripts/journald_snapshot.sh", f"_snap={snapshot}")
    _write_executable(runnable, policy)
    _write_executable(fake_bin / "systemd-run", launcher)
    state = tmp_path / "state"
    cause = tmp_path / "reset_cause"
    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "STATE_DIR": str(state), "RUN_DIR": str(tmp_path / "run"),
        "RESET_CAUSE_PATH": str(cause), "MIN_UPTIME_SEC": "0",
        "REBOOT_COOLDOWN_SEC": "300", "MAX_REBOOT_COUNT": "9",
    }
    return runnable, env, state, cause


def test_failed_systemd_handoff_never_reboots_in_callers_cgroup(tmp_path):
    """A failed independent executor launch must not fall back into a dying caller."""
    runnable, env, state, cause = _systemd_policy(
        tmp_path, "#!/bin/sh\nexit 42\n",
    )
    result = subprocess.run(
        ["bash", str(runnable), "--source", "wlan_fw_watch", "--reason", "driver wedge"],
        env=env, text=True, capture_output=True, timeout=5,
    )
    assert result.returncode == 42, result.stderr
    assert not state.exists(), "handoff failure consumed reboot budget"
    assert not cause.exists(), "handoff failure left a stale cause"


def test_systemd_executor_keeps_arguments_limits_and_failure_status(tmp_path):
    """The executor alone runs the policy once and reports final failure unchanged."""
    launcher = '''#!/usr/bin/env python3
import os, sys
args=sys.argv[1:]
env={'PATH':os.environ['PATH']}
for arg in args:
    if arg.startswith('--setenv='):
        key,value=arg[len('--setenv='):].split('=',1)
        env[key]=value
required={'STATE_DIR','RUN_DIR','RESET_CAUSE_PATH','MIN_UPTIME_SEC','REBOOT_COOLDOWN_SEC','MAX_REBOOT_COUNT'}
if not required.issubset(env):
    sys.exit(91)
boundary=args.index('--')
os.execve(args[boundary+1],args[boundary+1:],env)
'''
    runnable, env, state, cause = _systemd_policy(tmp_path, launcher)
    import time
    state.mkdir()
    (state / "reboot_policy_global.state").write_text(f"{int(time.time())} 3\n")
    reason = "driver wedge $literal ${unchanged} space"
    result = subprocess.run(
        ["bash", str(runnable), "--source", "wlan_fw_watch", "--reason", reason],
        env=env, text=True, capture_output=True, timeout=5,
    )
    assert result.returncode == 127, result.stderr
    assert (state / "reboot.calls").read_text() == reason + "\n"
    assert (state / "reboot_policy_global.state").read_text().split()[1] == "4"
    assert not cause.exists(), "the executor failed but left a stale cause"
    assert not (tmp_path / "run/reboot.lockdir").exists()
