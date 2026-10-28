"""FTP wpsk passes one passphrase to wifi without exposing it in replies."""

import os
from pathlib import Path
import shlex
import subprocess


HANDLER = Path(__file__).resolve().parents[4] / "opt" / "ftpcmd" / "bin" / "wpsk"


def run_wpsk(tmp_path, *args, wifi_rc=0):
    wifi = tmp_path / "wifi"
    capture = tmp_path / "arguments"
    capture.unlink(missing_ok=True)
    wifi.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$@" > "$WPSK_CAPTURE"\n'
        'printf "sensitive-child-output\\n"\n'
        'printf "sensitive-child-error\\n" >&2\n'
        f"exit {wifi_rc}\n"
    )
    wifi.chmod(0o755)
    log = tmp_path / "logger.log"
    log.unlink(missing_ok=True)
    logger = tmp_path / "logger"
    logger.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$WPSK_LOG"\n')
    logger.chmod(0o755)
    source = HANDLER.read_text()
    original = "WIFI=/usr/local/bin/wifi\n"
    assert source.count(original) == 1
    copied = tmp_path / "wpsk"
    copied.write_text(source.replace(original, f"WIFI={shlex.quote(str(wifi))}\n", 1))
    copied.chmod(0o755)
    env = dict(os.environ, WPSK_CAPTURE=str(capture), WPSK_LOG=str(log),
               PATH=str(tmp_path) + ":" + os.environ["PATH"])
    result = subprocess.run([str(copied), *args], capture_output=True, text=True, env=env)
    calls = capture.read_text().splitlines() if capture.exists() else []
    logs = log.read_text().splitlines() if log.exists() else []
    return result, calls, logs


def test_wpsk_stages_passphrase_as_one_wifi_argument(tmp_path):
    result, calls, logs = run_wpsk(tmp_path, "0", "new", "secret")
    assert result.returncode == 0
    assert result.stdout == "SUCCESS\n"
    assert result.stderr == ""
    assert calls == ["mlan0", "psk", "new secret"]
    assert logs == []


def test_wpsk_explicit_runtime_stage_keeps_the_legacy_mode_distinct(tmp_path):
    result, calls, logs = run_wpsk(tmp_path, "1", "--stage", "new", "secret")
    assert result.returncode == 0
    assert result.stdout == "SUCCESS\n"
    assert result.stderr == ""
    assert calls == ["mlan1", "stage-psk", "new secret"]
    assert logs == []


def test_wpsk_rejects_missing_interface_or_secret_without_wifi_call(tmp_path):
    for args in ((), ("bad", "secret"), ("mlan1",), ("--stage", "0"), ("0", "--stage")):
        result, calls, logs = run_wpsk(tmp_path, *args)
        assert result.returncode == 2
        assert result.stdout == result.stderr == ""
        assert calls == []
        assert len(logs) == 1
        assert "command=wpsk status=fail code=2" in logs[0]


def test_wpsk_propagates_failure_without_leaking_child_output(tmp_path):
    result, calls, logs = run_wpsk(tmp_path, "mlan1", "wrong-secret", wifi_rc=7)
    assert result.returncode == 7
    assert result.stdout == result.stderr == ""
    assert calls == ["mlan1", "psk", "wrong-secret"]
    assert len(logs) == 1
    assert "command=wpsk status=fail code=7 iface=mlan1 reason=apply-failed" in logs[0]
    assert "wrong-secret" not in logs[0]
