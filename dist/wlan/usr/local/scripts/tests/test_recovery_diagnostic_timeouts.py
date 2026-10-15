"""Recovery callers must reach policy even when optional diagnostics stall."""
import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]


def executable(path, body):
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o700)


def systemctl_with_nonblocking_parent_status():
    return """if [ "$*" = "show wifi_init.service -p ExecMainStatus --value" ]; then
    printf 'malformed\\n'
    exit 0
fi
printf 'systemctl %s\\n' "$*" >> "$UNEXPECTED_CALLS"
exit 91
"""


@pytest.mark.parametrize("caller", ["wifi_checker.sh", "wlan_fw_watch.sh"])
@pytest.mark.parametrize("stalled", ["sync", "snapshot"])
def test_stalled_diagnostics_do_not_prevent_reboot_policy_request(tmp_path, caller, stalled):
    """Unbounded snapshot/sync must not hide a detected fatal driver condition."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    marker = tmp_path / "policy.mark"
    unexpected = tmp_path / "unexpected.calls"
    for name in ("logger", "print"):
        executable(fake_bin / name, "exit 0\n")
    executable(fake_bin / "lsmod", "printf 'moal 1 0\\n'\n")
    executable(fake_bin / "dmesg", "printf 'private kernel diagnostic\\n'\n")
    executable(fake_bin / "sleep", 'if [ -f "$POLICY_MARK" ]; then kill -TERM "$PPID"; fi\nexit 0\n')
    executable(fake_bin / "systemctl", systemctl_with_nonblocking_parent_status())
    for name in ("wifi", "reboot"):
        executable(fake_bin / name, f'printf "{name} %s\\n" "$*" >> "$UNEXPECTED_CALLS"\nexit 91\n')
    hang = "trap '' TERM\nwhile :; do /bin/sleep 1; done\n"
    executable(fake_bin / "sync", hang if stalled == "sync" else "exit 0\n")
    snapshot = tmp_path / "snapshot"
    executable(snapshot, hang if stalled == "snapshot" else "exit 0\n")
    policy = tmp_path / "policy"
    executable(policy, 'printf "%s\\n" "$*" > "$POLICY_MARK"\nexit 42\n')

    firmware = tmp_path / "firmware/cts"
    firmware.mkdir(parents=True)
    (firmware / "wifi_mod_para.conf").write_text("mfg_mode=0\n")
    wifi_status = tmp_path / "wifi_status"
    wifi_status.write_text("11\n")
    adapter = tmp_path / "adapter"
    adapter.write_text("hardware_status=5\n")
    state = tmp_path / "reload.state"
    state.write_text(f"{int(time.time())} reload\n")
    source = (SCRIPTS / caller).read_text()
    replacements = {
        "/usr/local/etc/wifi_init_conf.json": str(tmp_path / "missing.json"),
        "/usr/local/scripts/wlan_reboot_policy.sh": str(policy),
        "/usr/local/scripts/journald_snapshot.sh": str(snapshot),
        "/lib/firmware/": str(tmp_path / "firmware") + "/",
        "/sys/class/net/": str(tmp_path / "absent-net") + "/",
        "/var/log/cantops": str(tmp_path / "logs"),
        "/run/wifi/": str(tmp_path / "run") + "/",
        "/proc/mwlan/": str(tmp_path / "proc") + "/",
    }
    for old, new in replacements.items():
        source = source.replace(old, new)
    for boundary in replacements:
        assert boundary not in source, f"unsafe test path: {boundary}"
    runnable = tmp_path / caller
    runnable.write_text(source)
    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "POLICY_MARK": str(marker), "UNEXPECTED_CALLS": str(unexpected),
        "WIFI_LOGGER_COMMAND_LIB": str(SCRIPTS / "wifi_logger_command_lib.sh"),
        "WIFI_INIT_CONF_JSON": str(tmp_path / "missing.json"),
        "WIFI_STATUS_PROC": str(wifi_status), "ADAPTER_CONFIG_GLOB": str(adapter),
        "STATE_FILE": str(state), "POLICY_BIN": str(policy), "SNAPSHOT_BIN": str(snapshot),
    }
    proc = subprocess.Popen(
        ["/bin/bash", str(runnable), "mlan0"], env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
    )
    timed_out = False
    try:
        try:
            stdout, stderr = proc.communicate(timeout=14)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(proc.pid, signal.SIGKILL)
            stdout, stderr = proc.communicate(timeout=3)
        assert not timed_out, f"{caller}: stalled {stalled} prevented policy request\n{stderr}"
        assert proc.returncode == 0, (stdout, stderr)
        request = marker.read_text()
        expected_source = "wifi_checker" if caller == "wifi_checker.sh" else "wlan_fw_watch"
        assert f"--source {expected_source}" in request
        assert "--reason" in request
        assert not unexpected.exists(), unexpected.read_text() if unexpected.exists() else ""
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate(timeout=3)


@pytest.mark.parametrize("timeout_status", [124, 137])
def test_fw_watch_treats_timeout_status_as_confirmed_wedge(tmp_path, timeout_status):
    """GNU timeout variants must both keep a confirmed wedge on the recovery path."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    marker = tmp_path / "policy.mark"
    unexpected = tmp_path / "unexpected.calls"
    executable(fake_bin / "logger", "exit 0\n")
    executable(
        fake_bin / "sleep",
        'if [ -f "$POLICY_MARK" ]; then kill -TERM "$PPID"; fi\nexit 0\n',
    )
    executable(fake_bin / "systemctl", systemctl_with_nonblocking_parent_status())
    for name in ("reboot",):
        executable(
            fake_bin / name,
            f'printf "{name} %s\\n" "$*" >> "$UNEXPECTED_CALLS"\nexit 91\n',
        )
    timeout = tmp_path / "timeout"
    executable(timeout, 'exit "$TIMEOUT_STATUS"\n')
    policy = tmp_path / "policy"
    executable(policy, 'printf "%s\\n" "$*" > "$POLICY_MARK"\nexit 42\n')

    firmware = tmp_path / "firmware/cts"
    firmware.mkdir(parents=True)
    (firmware / "wifi_mod_para.conf").write_text("mfg_mode=0\n")
    wifi_status = tmp_path / "wifi_status"
    wifi_status.write_text("11\n")
    adapter = tmp_path / "adapter"
    adapter.write_text("hardware_status=5\n")
    state = tmp_path / "reload.state"
    state.write_text(f"{int(time.time())} reload\n")
    source = (SCRIPTS / "wlan_fw_watch.sh").read_text()
    replacements = {
        "/usr/local/etc/wifi_init_conf.json": str(tmp_path / "missing.json"),
        "/usr/local/scripts/wlan_reboot_policy.sh": str(policy),
        "/usr/local/scripts/journald_snapshot.sh": str(tmp_path / "snapshot"),
        "/lib/firmware/": str(tmp_path / "firmware") + "/",
        "/sys/class/net/": str(tmp_path / "absent-net") + "/",
        "/var/log/cantops": str(tmp_path / "logs"),
        "/proc/mwlan/": str(tmp_path / "proc") + "/",
    }
    for old, new in replacements.items():
        source = source.replace(old, new)
    for boundary in replacements:
        assert boundary not in source, f"unsafe test path: {boundary}"
    runnable = tmp_path / "wlan_fw_watch.sh"
    runnable.write_text(source)
    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "POLICY_MARK": str(marker),
        "UNEXPECTED_CALLS": str(unexpected),
        "TIMEOUT_STATUS": str(timeout_status),
        "LOGGER_TIMEOUT_BIN": str(timeout),
        "WIFI_LOGGER_COMMAND_LIB": str(SCRIPTS / "wifi_logger_command_lib.sh"),
        "WIFI_INIT_CONF_JSON": str(tmp_path / "missing.json"),
        "WIFI_STATUS_PROC": str(wifi_status),
        "ADAPTER_CONFIG_GLOB": str(adapter),
        "STATE_FILE": str(state),
        "POLICY_BIN": str(policy),
    }
    proc = subprocess.Popen(
        ["/bin/bash", str(runnable)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    timed_out = False
    try:
        try:
            stdout, stderr = proc.communicate(timeout=8)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(proc.pid, signal.SIGKILL)
            stdout, stderr = proc.communicate(timeout=3)
        assert not timed_out, f"timeout rc={timeout_status} lost recovery path\n{stderr}"
        assert proc.returncode == 0, (stdout, stderr)
        assert "--source wlan_fw_watch" in marker.read_text()
        assert not unexpected.exists(), unexpected.read_text() if unexpected.exists() else ""
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate(timeout=3)
