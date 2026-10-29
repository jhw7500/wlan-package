"""MAC-contract failures must not race the independent health checker."""

import os
import signal
import subprocess
import time
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]


def executable(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o700)


def prepare_checker(
    tmp_path: Path, *, iface: str, scenario: str = "initial-block"
) -> tuple[subprocess.Popen, Path, Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    events = tmp_path / "events"
    calls = tmp_path / "calls"
    sleep_count = tmp_path / "sleep-count"
    marker = tmp_path / "run/mac-contract-blocked"
    parent_metadata = tmp_path / "parent-metadata"
    marker.parent.mkdir()
    if scenario in {
        "initial-block",
        "stale-marker-non78",
        "marker-active-transition",
        "marker-metadata-unavailable",
        "marker-malformed-metadata",
    }:
        marker.touch()
    if scenario == "parent-78-start":
        parent_metadata.write_text(
            "ActiveState=failed\nExecMainStatus=78\n", encoding="utf-8"
        )
    elif scenario == "stale-marker-non78":
        parent_metadata.write_text(
            "ActiveState=failed\nExecMainStatus=1\n", encoding="utf-8"
        )
    elif scenario == "marker-active-transition":
        parent_metadata.write_text(
            "ActiveState=activating\nExecMainStatus=1\n", encoding="utf-8"
        )
    elif scenario == "marker-malformed-metadata":
        parent_metadata.write_text(
            "ActiveState=failed\nExecMainStatus=not-a-number\n", encoding="utf-8"
        )

    executable(fake_bin / "logger", 'printf "logger %s\\n" "$*" >> "$EVENTS"\n')
    executable(fake_bin / "print", 'printf "print %s\\n" "$*" >> "$EVENTS"\n')
    executable(fake_bin / "lsmod", "printf 'moal 1 0\\n'\n")
    executable(fake_bin / "dmesg", "printf 'private diagnostic\\n'\n")
    executable(fake_bin / "sync", "exit 0\n")
    executable(
        fake_bin / "date",
        """if [ "$TEST_SCENARIO" = raw-switch-lock-race ] && [ "${1:-}" = +%s ]; then
    n=0
    [ ! -f "$DATE_COUNT" ] || n=$(cat "$DATE_COUNT")
    printf '%s\\n' "$((TEST_EPOCH + n * 5))"
    printf '%s\\n' "$((n + 1))" > "$DATE_COUNT"
    exit 0
fi
exec /bin/date "$@"
""",
    )
    executable(fake_bin / "iw", 'printf "iw %s\\n" "$*" >> "$CALLS"\nexit 1\n')
    executable(fake_bin / "wpa_cli", 'printf "wpa_cli %s\\n" "$*" >> "$CALLS"\nexit 1\n')
    executable(
        fake_bin / "systemctl",
        """printf "systemctl %s\\n" "$*" >> "$CALLS"
if [ "$*" = "show wifi_init.service -p ActiveState -p ExecMainStatus" ]; then
    [ "$TEST_SCENARIO" != marker-metadata-unavailable ] || exit 1
    if [ -f "$PARENT_METADATA" ]; then
        cat "$PARENT_METADATA"
    else
        printf 'ActiveState=active\nExecMainStatus=0\n'
    fi
    exit 0
fi
if [ "$TEST_SCENARIO" = raw-switch-lock-race ] \
   && [ "$1" = is-active ] && [ "$2" = --quiet ]; then
    exit 0
fi
exit 1
""",
    )
    executable(
        fake_bin / "wifi",
        """printf "wifi %s\\n" "$*" >> "$EVENTS"
if [ "$TEST_SCENARIO" = raw-switch-lock-race ] && [ "${2:-}" = connect ]; then
    exec 8>"$WIFI_RUN_DIR/${1}.change.lock" || exit 1
    flock -n -x 8 || exit 6
    : > "$CHECKER_LOCKED"
    while [ ! -e "$RELEASE_CHECKER" ]; do /bin/sleep 0.01; done
elif [ "$TEST_SCENARIO" = raw-switch-lock-race ] && [ "${2:-}" = connectraw ]; then
    exec /bin/bash "$REAL_WIFI" "$@"
fi
exit 0
""",
    )
    executable(
        fake_bin / "sleep",
        """n=0
[ ! -f "$SLEEP_COUNT" ] || n=$(cat "$SLEEP_COUNT")
n=$((n + 1))
printf '%s\\n' "$n" > "$SLEEP_COUNT"
if [ "$TEST_IFACE" = eth0 ]; then
    [ "$n" -lt 2 ] || kill -TERM "$PPID"
    exit 0
fi
if [ "$TEST_SCENARIO" = raw-switch-lock-race ]; then
    /bin/sleep 0.03
    exit 0
fi
if [ "$TEST_SCENARIO" = mid-loop ] && [ "$n" -eq 6 ]; then
    printf 'marker-created\\n' >> "$EVENTS"
    touch "$MAC_BLOCK_MARKER"
fi
if [ "$TEST_SCENARIO" = parent-78-mid-loop ] && [ "$n" -eq 6 ]; then
    printf 'parent-status-78\n' >> "$EVENTS"
    printf 'ActiveState=failed\nExecMainStatus=78\n' > "$PARENT_METADATA"
fi
if [ "$TEST_SCENARIO" = mid-loop ] && [ "$n" -ge 8 ]; then
    kill -TERM "$PPID"
fi
if [ "$TEST_SCENARIO" = parent-78-mid-loop ] && [ "$n" -ge 8 ]; then
    kill -TERM "$PPID"
fi
if [ "$TEST_SCENARIO" = parent-78-start ] && [ "$n" -ge 8 ]; then
    kill -TERM "$PPID"
fi
if [ "$TEST_SCENARIO" = marker-active-transition ] && [ "$n" -ge 8 ]; then
    kill -TERM "$PPID"
fi
if [ "$TEST_SCENARIO" = marker-metadata-unavailable ] && [ "$n" -ge 8 ]; then
    kill -TERM "$PPID"
fi
if [ "$TEST_SCENARIO" = marker-malformed-metadata ] && [ "$n" -ge 8 ]; then
    kill -TERM "$PPID"
fi
if [ "$TEST_SCENARIO" = stale-marker-non78 ] && [ "$n" -ge 15 ]; then
    kill -TERM "$PPID"
fi
if [ "$n" -eq 7 ]; then
    if [ "$TEST_SCENARIO" = initial-block ]; then
        printf 'marker-removed\\n' >> "$EVENTS"
        rm -f "$MAC_BLOCK_MARKER"
    fi
fi
if [ -f "$MAC_BLOCK_MARKER" ]; then
    printf 'blocked-tick\\n' >> "$EVENTS"
else
    printf 'unblocked-tick\\n' >> "$EVENTS"
fi
if [ -f "$POLICY_MARK" ]; then
    kill -TERM "$PPID"
fi
exit 0
""",
    )

    policy = tmp_path / "policy"
    executable(
        policy,
        'printf "policy %s\\n" "$*" >> "$EVENTS"\nprintf "%s\\n" "$*" > "$POLICY_MARK"\nexit 42\n',
    )
    snapshot = tmp_path / "snapshot"
    executable(snapshot, "exit 0\n")
    logger_lib = tmp_path / "logger-lib.sh"
    logger_lib.write_text(
        "logger_run_bounded() { shift; \"$@\"; }\n"
        "logger_read_bounded() { return 1; }\n",
        encoding="utf-8",
    )

    sysfs = tmp_path / "net"
    if iface == "eth0":
        (sysfs / "eth0").mkdir(parents=True)
        (sysfs / "eth0/operstate").write_text("up\n", encoding="utf-8")
    elif scenario == "raw-switch-lock-race":
        (sysfs / iface).mkdir(parents=True)
        (sysfs / iface / "operstate").write_text("up\n", encoding="utf-8")

    source = (SCRIPTS / "wifi_checker.sh").read_text(encoding="utf-8")
    replacements = {
        "/usr/local/etc/wifi_init_conf.json": str(tmp_path / "missing.json"),
        "/usr/local/scripts/wlan_reboot_policy.sh": str(policy),
        "/usr/local/scripts/journald_snapshot.sh": str(snapshot),
        "/lib/firmware/": str(tmp_path / "firmware") + "/",
        "/sys/class/net/": str(sysfs) + "/",
        "/var/log/cantops": str(tmp_path / "logs"),
        "/run/wifi/": str(tmp_path / "run") + "/",
    }
    for old, new in replacements.items():
        source = source.replace(old, new)
    for boundary in replacements:
        assert boundary not in source, f"unsafe test path: {boundary}"
    runnable = tmp_path / "wifi_checker.sh"
    runnable.write_text(source, encoding="utf-8")

    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "EVENTS": str(events),
        "CALLS": str(calls),
        "SLEEP_COUNT": str(sleep_count),
        "MAC_BLOCK_MARKER": str(marker),
        "PARENT_METADATA": str(parent_metadata),
        "POLICY_MARK": str(tmp_path / "policy.mark"),
        "TEST_IFACE": iface,
        "TEST_SCENARIO": scenario,
        "WIFI_LOGGER_COMMAND_LIB": str(logger_lib),
        "WIFI_RUN_DIR": str(tmp_path / "run"),
        "DATE_COUNT": str(tmp_path / "date-count"),
        "TEST_EPOCH": str(int(time.time())),
        "CHECKER_LOCKED": str(tmp_path / "checker-locked"),
        "RELEASE_CHECKER": str(tmp_path / "release-checker"),
    }
    proc = subprocess.Popen(
        ["/bin/bash", str(runnable), iface],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    return proc, events, calls, Path(env["POLICY_MARK"])


def run_raw_switch(tmp_path: Path, ssid: str) -> subprocess.CompletedProcess:
    fake_bin = tmp_path / "bin"
    sysfs = tmp_path / "net"
    conf_dir = tmp_path / "wpa"
    conf_dir.mkdir(exist_ok=True)
    conf = conf_dir / "wpa_supplicant-mlan0.conf"
    conf.write_text('network={\n    ssid="old-ap"\n}\n', encoding="utf-8")
    conf.chmod(0o600)

    executable(tmp_path / "raw-commit", "exit 0\n")
    product_scripts = tmp_path / "product-scripts"
    product_scripts.mkdir(exist_ok=True)
    real_wifi = product_scripts / "wifi.sh"
    real_wifi.write_text((SCRIPTS / "wifi.sh").read_text(encoding="utf-8"), encoding="utf-8")
    raw_helper = SCRIPTS / "wifi_connect_raw.sh"
    if raw_helper.exists():
        installed_helper = product_scripts / raw_helper.name
        installed_helper.write_text(
            raw_helper.read_text(encoding="utf-8").replace(
                "WPA_CLI=/usr/sbin/wpa_cli", f"WPA_CLI={fake_bin / 'wpa_cli'}"
            ),
            encoding="utf-8",
        )
        installed_helper.chmod(0o700)
    executable(
        fake_bin / "wpa_cli",
        """case "$*" in
    *" status")
        printf 'wpa_state=COMPLETED\\nid=7\\nssid=old-ap\\nbssid=00:11:22:33:44:55\\n'
        ;;
    *" set_network "*) printf 'OK\\n' ;;
    *" disconnect") printf 'down\\n' > "$RAW_OPERSTATE"; printf 'OK\\n' ;;
    *" reassociate") printf 'OK\\n' ;;
    *) printf 'FAIL\\n'; exit 1 ;;
esac
""",
    )
    handler_source = (SCRIPTS.parents[2] / "opt/ftpcmd/bin/wconnectraw").read_text(
        encoding="utf-8"
    )
    handler_source = handler_source.replace(
        "WPA_CLI=/usr/sbin/wpa_cli", f"WPA_CLI={fake_bin / 'wpa_cli'}"
    ).replace("WIFI=/usr/local/bin/wifi", f"WIFI={fake_bin / 'wifi'}")
    handler = tmp_path / "wconnectraw"
    handler.write_text(handler_source, encoding="utf-8")

    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "EVENTS": str(tmp_path / "events"),
        "TEST_SCENARIO": "raw-switch-lock-race",
        "WIFI_RUN_DIR": str(tmp_path / "run"),
        "WPA_CONF_DIR": str(conf_dir),
        "WIFI_RAW_COMMIT_HELPER": str(tmp_path / "raw-commit"),
        "RAW_OPERSTATE": str(sysfs / "mlan0/operstate"),
        "CHECKER_LOCKED": str(tmp_path / "checker-locked"),
        "RELEASE_CHECKER": str(tmp_path / "release-checker"),
        "REAL_WIFI": str(real_wifi),
    }
    return subprocess.run(
        ["/bin/sh", str(handler), "0", ssid],
        env=env,
        capture_output=True,
        text=True,
        timeout=3,
    )


def finish(proc: subprocess.Popen, timeout: int = 8) -> tuple[str, str]:
    try:
        return proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        stdout, stderr = proc.communicate(timeout=3)
        raise AssertionError(f"checker did not reach bounded stop\n{stdout}\n{stderr}")


def recovery_actions(calls_path: Path) -> list[str]:
    if not calls_path.exists():
        return []
    return [
        line
        for line in calls_path.read_text(encoding="utf-8").splitlines()
        if line.startswith("systemctl ")
        and " show wifi_init.service " not in f" {line} "
    ]


def test_raw_switch_grace_keeps_checker_from_blocking_immediate_retry(tmp_path):
    proc, _, _, _ = prepare_checker(
        tmp_path, iface="mlan0", scenario="raw-switch-lock-race"
    )
    try:
        submitted = run_raw_switch(tmp_path, "field-ap")
        assert submitted.returncode == 0, submitted.stdout + submitted.stderr

        deadline = time.monotonic() + 2
        checker_locked = tmp_path / "checker-locked"
        sleep_count = tmp_path / "sleep-count"
        while time.monotonic() < deadline:
            ticks = int(sleep_count.read_text()) if sleep_count.exists() else 0
            if checker_locked.exists() or ticks >= 3:
                break
            time.sleep(0.01)
        else:
            raise AssertionError("checker did not reach the reconnect decision window")

        retried = run_raw_switch(tmp_path, "field-ap")
    finally:
        (tmp_path / "release-checker").touch()
        os.killpg(proc.pid, signal.SIGTERM)
        finish(proc)

    assert retried.returncode == 0, (
        "checker occupied the raw switch change gate during its retry window: "
        f"stdout={retried.stdout!r} stderr={retried.stderr!r}"
    )


def test_wireless_checker_defers_recovery_until_mac_contract_unblocks(tmp_path):
    proc, events_path, calls_path, policy_mark = prepare_checker(tmp_path, iface="mlan0")
    stdout, stderr = finish(proc)
    assert proc.returncode == 0, (stdout, stderr)

    events = events_path.read_text(encoding="utf-8").splitlines()
    removed_at = events.index("marker-removed")
    policy_at = next(i for i, line in enumerate(events) if line.startswith("policy "))
    assert removed_at < policy_at, events
    assert not any(line.startswith(("policy ", "wifi ")) for line in events[:removed_at])
    assert not recovery_actions(calls_path)
    assert events[removed_at:policy_at].count("unblocked-tick") >= 6, events
    assert policy_mark.exists(), "wireless monitoring did not resume after marker removal"


def test_eth0_monitoring_ignores_wireless_mac_contract_block(tmp_path):
    proc, events_path, _, policy_mark = prepare_checker(tmp_path, iface="eth0")
    stdout, stderr = finish(proc)
    assert proc.returncode == 0, (stdout, stderr)

    events = events_path.read_text(encoding="utf-8")
    assert "[eth0] link change down -> up" in events
    assert not policy_mark.exists()


def test_marker_created_after_fatal_detection_still_blocks_policy(tmp_path):
    proc, events_path, calls_path, policy_mark = prepare_checker(
        tmp_path, iface="mlan0", scenario="mid-loop"
    )
    stdout, stderr = finish(proc)
    assert proc.returncode == 0, (stdout, stderr)

    events = events_path.read_text(encoding="utf-8").splitlines()
    created_at = events.index("marker-created")
    assert "blocked-tick" in events[created_at:]
    assert not any(line.startswith(("policy ", "wifi ")) for line in events[created_at:])
    assert not recovery_actions(calls_path)
    assert not policy_mark.exists()


def test_parent_status_78_at_start_suppresses_wireless_recovery(tmp_path):
    proc, events_path, calls_path, policy_mark = prepare_checker(
        tmp_path, iface="mlan0", scenario="parent-78-start"
    )
    stdout, stderr = finish(proc)
    assert proc.returncode == 0, (stdout, stderr)

    events = events_path.read_text(encoding="utf-8").splitlines()
    assert not any(line.startswith(("policy ", "wifi ")) for line in events), events
    assert not recovery_actions(calls_path)
    assert not policy_mark.exists()


def test_parent_status_becoming_78_before_policy_suppresses_recovery(tmp_path):
    proc, events_path, calls_path, policy_mark = prepare_checker(
        tmp_path, iface="mlan0", scenario="parent-78-mid-loop"
    )
    stdout, stderr = finish(proc)
    assert proc.returncode == 0, (stdout, stderr)

    events = events_path.read_text(encoding="utf-8").splitlines()
    changed_at = events.index("parent-status-78")
    assert not any(line.startswith(("policy ", "wifi ")) for line in events[changed_at:]), events
    assert not recovery_actions(calls_path)
    assert not policy_mark.exists()


def test_failed_non78_parent_with_stale_marker_resumes_recovery(tmp_path):
    proc, events_path, calls_path, policy_mark = prepare_checker(
        tmp_path, iface="mlan0", scenario="stale-marker-non78"
    )
    stdout, stderr = finish(proc)
    assert proc.returncode == 0, (stdout, stderr)

    events = events_path.read_text(encoding="utf-8").splitlines()
    assert any(line.startswith("policy ") for line in events), events
    assert policy_mark.exists(), "stale marker suppressed ordinary non-MAC recovery"
    assert not recovery_actions(calls_path)


def test_marker_during_parent_activation_remains_blocked(tmp_path):
    proc, events_path, calls_path, policy_mark = prepare_checker(
        tmp_path, iface="mlan0", scenario="marker-active-transition"
    )
    stdout, stderr = finish(proc)
    assert proc.returncode == 0, (stdout, stderr)

    events = events_path.read_text(encoding="utf-8").splitlines()
    assert not any(line.startswith(("policy ", "wifi ")) for line in events), events
    assert not recovery_actions(calls_path)
    assert not policy_mark.exists()


def test_marker_with_unavailable_parent_metadata_remains_blocked(tmp_path):
    proc, events_path, calls_path, policy_mark = prepare_checker(
        tmp_path, iface="mlan0", scenario="marker-metadata-unavailable"
    )
    stdout, stderr = finish(proc)
    assert proc.returncode == 0, (stdout, stderr)

    events = events_path.read_text(encoding="utf-8").splitlines()
    assert not any(line.startswith(("policy ", "wifi ")) for line in events), events
    assert not recovery_actions(calls_path)
    assert not policy_mark.exists()


def test_marker_with_malformed_parent_metadata_remains_blocked(tmp_path):
    proc, events_path, calls_path, policy_mark = prepare_checker(
        tmp_path, iface="mlan0", scenario="marker-malformed-metadata"
    )
    stdout, stderr = finish(proc)
    assert proc.returncode == 0, (stdout, stderr)

    events = events_path.read_text(encoding="utf-8").splitlines()
    assert not any(line.startswith(("policy ", "wifi ")) for line in events), events
    assert not recovery_actions(calls_path)
    assert not policy_mark.exists()
