"""Run manual connect transactions against a controllable supplicant boundary.

The real scripts own profile writes, locks, deadlines, and rollback. Only device
commands are replaced; the fake AP is unavailable unless listed in `available`.
"""

import fcntl
import importlib.util
import json
import os
import shlex
import signal
import stat
import subprocess
import time
from pathlib import Path

_spec = importlib.util.spec_from_file_location("connect_fixture", Path(__file__).with_name("test_wifi_connect_cli.py"))
_cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cli)
CONF_MODE_B, SCRIPTS_DIR, WIFI_SH = _cli.CONF_MODE_B, _cli.SCRIPTS_DIR, _cli.WIFI_SH


# Model both the persisted profile and the separately loaded supplicant profile.
# This catches a rollback which restores only the file, or backs up a profile
# already changed by wssid/wfreq instead of the previous verified connection.
PROFILE_BOUNDARY = r'''
if [ "${1:-}" != --daemon ]; then
    command=${3:-}
    runtime="$D/runtime.conf"
    case "$command" in
        get_network)
            case "${5:-}" in
                ssid) awk '/^[[:space:]]*ssid=/{sub(/^[[:space:]]*ssid=/, ""); print; exit}' "$runtime" ;;
                freq_list) awk '/^[[:space:]]*freq_list=/{sub(/^[[:space:]]*freq_list=/, ""); print; exit}' "$runtime" ;;
                *) echo FAIL ;;
            esac
            exit 0 ;;
        get)
            awk '/^[[:space:]]*freq_list=/{sub(/^[[:space:]]*freq_list=/, ""); print; exit}' "$runtime"
            exit 0 ;;
        status)
            if [ -f "$D/delay-target" ] && grep -q 'ssid="unreachable"' "$runtime"; then
                sleep 3
            fi ;;
        reconfigure|reassociate|reconnect|scan)
            if [ "$command" = reconfigure ]; then
                cp "$WPA_CONF_DIR/wpa_supplicant-${2}.conf" "$runtime"
                if grep -q 'ssid="unreachable"' "$runtime"; then
                    : > "$D/target-entered"
                    while [ -f "$D/block-target" ]; do sleep 0.02; done
                    if [ -f "$D/fail-reconfigure" ]; then
                        rm -f "$D/fail-reconfigure"
                        echo FAIL
                        exit 0
                    fi
                fi
            fi
            ssid=$(awk '/^[[:space:]]*ssid=/{sub(/^[[:space:]]*ssid="/, ""); sub(/"$/, ""); print; exit}' "$runtime")
            if grep -Fxq "$ssid" "$D/available"; then
                printf '0 %s 5220\n' "$ssid" > "$D/landing"
                cp "$D/landing" "$D/scan-landing"
            else
                rm -f "$D/landing" "$D/scan-landing"
                printf 'wpa_state=SCANNING\n' > "$D/status"
            fi ;;
    esac
fi
'''


class ConnectFallbackTests(_cli.ConnectHarness):
    def setUp(self):
        super().setUp()
        stub = _cli.WPA_CLI_STUB.replace('D="${WPA_STUB_DIR:?}"', 'D="${WPA_STUB_DIR:?}"\n' + PROFILE_BOUNDARY, 1)
        (self.stub / "wpa_cli").write_text(stub)
        self.write_conf(CONF_MODE_B)
        (self.state / "runtime.conf").write_text(CONF_MODE_B)
        (self.state / "available").write_text("jhw_wlan_\ngood-B\n")
        self.config = self.root / "wifi.json"
        self.marker = self.root / "changeip_essid.restore"
        mlanutl = self.stub / "mlanutl"
        mlanutl.write_text("#!/bin/sh\nexit 0\n")
        mlanutl.chmod(0o755)
        peer_dir = self.root / "peer"
        peer_dir.mkdir()
        finder = peer_dir / "wifi_eth_peer_find.sh"
        finder.write_text("#!/bin/sh\necho 192.0.2.2\n")
        finder.chmod(0o755)
        self.set_policy()

    def set_policy(self, enabled=True, attempt=1, rollback=1):
        self.config.write_text(json.dumps({"mlan0": {"connect_fallback": {
            "enabled": enabled, "attempt_timeout_sec": attempt,
            "rollback_timeout_sec": rollback,
        }}}))

    def environment(self):
        return dict(os.environ, PATH=str(self.stub) + ":" + os.environ["PATH"],
                    WPA_STUB_DIR=str(self.state), WPA_CONF_DIR=str(self.conf_dir),
                    WIFI_RUN_DIR=str(self.run_dir), WIFI_INIT_CONF_JSON=str(self.config),
                    WIFI_PEER_SCRIPT_DIR=str(self.root / "peer"),
                    WIFI_SCAN_TRANSITION_LOCK_TIMEOUT="0", ASSOC_TIMEOUT_DEFAULT="2",
                    OPC_ESSID_MARKER=str(self.marker))

    def run_connect(self, *args, extra_env=None, **kwargs):
        env = self.environment()
        env.update(extra_env or {})
        return super().run_connect(*args, extra_env=env, **kwargs)

    def run_wifi(self, *args, timeout=5):
        return subprocess.run(
            ["bash", str(WIFI_SH), "mlan0", *args],
            env=self.environment(), capture_output=True, text=True, timeout=timeout)

    def start_connect(self):
        return subprocess.Popen(["bash", str(WIFI_SH), "mlan0", "connect", "unreachable"],
                                env=self.environment(), stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)

    def wait_for_target(self, process):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if (self.state / "target-entered").exists():
                return
            if process.poll() is not None:
                break
            time.sleep(0.02)
        self.fail("candidate was never submitted to supplicant")

    def test_target_timeout_restores_profile_and_live_connection_but_returns_failure(self):
        result = self.run_connect("unreachable")
        self.assertEqual(result.returncode, 8, result.stdout + result.stderr)
        self.assertEqual(self.conf.read_text(), CONF_MODE_B)
        self.assertEqual((self.state / "runtime.conf").read_text(), CONF_MODE_B)
        self.assertIn("ssid=jhw_wlan_\n", (self.state / "status").read_text())
        self.assertIn("fallback restored", result.stdout + result.stderr)

    def test_staged_ssid_and_frequency_restore_previous_verified_profile(self):
        for command, value in (("ssid", "unreachable"), ("freq", "5180")):
            result = self.run_wifi(command, value)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = self.run_connect(apply_stored_conf=True)
        self.assertEqual(result.returncode, 8, result.stdout + result.stderr)
        self.assertEqual(self.conf.read_text(), CONF_MODE_B)
        self.assertEqual((self.state / "runtime.conf").read_text(), CONF_MODE_B)

    def test_staged_credentials_are_restored_with_the_verified_profile(self):
        result = self.run_wifi("psk", "replacement-secret")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = self.run_connect("unreachable")
        self.assertEqual(result.returncode, 8, result.stdout + result.stderr)
        self.assertEqual(self.conf.read_text(), CONF_MODE_B)
        self.assertFalse((self.run_dir / "mlan0.credential-pending").exists())

    def test_corrupt_checkpoint_is_unavailable_and_never_installed(self):
        result = self.run_wifi("ssid", "unreachable")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        (self.run_dir / "mlan0.last-good.json").write_text('{"profile":"broken"}')
        result = self.run_connect(apply_stored_conf=True)
        self.assertEqual(result.returncode, 8, result.stdout + result.stderr)
        self.assertIn('ssid="unreachable"', self.conf.read_text())
        self.assertIn("fallback unavailable", result.stdout + result.stderr)
        self.assertNotIn("fallback failed", result.stdout + result.stderr)

    def test_background_connect_does_not_initiate_manual_fallback(self):
        for origin in ("checker", "roam"):
            with self.subTest(origin=origin):
                result = self.run_connect("unreachable", extra_env={"WIFI_CONNECT_ORIGIN": origin})
                self.assertEqual(result.returncode, 8, result.stdout + result.stderr)
                self.assertIn('ssid="unreachable"', self.conf.read_text())
                self.assertNotIn("fallback restored", result.stdout + result.stderr)

    def test_multi_network_reconnect_remains_supplicant_owned(self):
        self.write_conf(_cli.CONF_MODE_A)
        (self.state / "runtime.conf").write_text(_cli.CONF_MODE_A)
        result = self.run_connect()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.conf.read_text(), _cli.CONF_MODE_A)
        self.assertEqual(list(self.run_dir.glob("*.last-good.json")), [])

    def test_success_replaces_last_good_for_the_next_failed_switch(self):
        result = self.run_connect("good-B")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        verified = self.conf.read_text()
        result = self.run_connect("unreachable")
        self.assertEqual(result.returncode, 8, result.stdout + result.stderr)
        self.assertEqual(self.conf.read_text(), verified)
        self.assertIn("ssid=good-B\n", (self.state / "status").read_text())

    def test_checkpoint_directory_sync_failure_restores_pre_attempt_connection(self):
        original_marker = "essid=original\nslot=1\n"
        self.marker.write_text(original_marker)
        (self.stub / "sync").write_text('''#!/bin/sh
if [ "${1:-}" = "$WIFI_RUN_DIR" ] || [ "$#" -eq 0 ]; then
    if [ -f "$WIFI_RUN_DIR/mlan0.last-good.json" ] \\
       && jq -e '.ssid == "good-B"' "$WIFI_RUN_DIR/mlan0.last-good.json" >/dev/null; then
        exit 1
    fi
fi
exit 0
''')
        result = self.run_connect("good-B")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.conf.read_text(), CONF_MODE_B)
        self.assertEqual((self.state / "runtime.conf").read_text(), CONF_MODE_B)
        self.assertIn("ssid=jhw_wlan_\n", (self.state / "status").read_text())
        self.assertEqual(self.marker.read_text(), original_marker)
        checkpoint = self.run_dir / "mlan0.last-good.json"
        self.assertEqual(json.loads(checkpoint.read_text())["profile"], CONF_MODE_B)
        self.assertEqual(stat.S_IMODE(checkpoint.stat().st_mode), 0o600)
        self.assertEqual(list(self.run_dir.glob("*.rollback.*")), [])
        self.assertIn("fallback restored", result.stderr)

    def test_recovery_copy_failure_prevents_target_submission(self):
        original_marker = "essid=original\nslot=1\n"
        self.marker.write_text(original_marker)
        cp = self.stub / "cp"
        cp.write_text('''#!/bin/bash
case "${!#}" in
    *.rollback.*) printf 'incomplete' > "${!#}"; exit 1 ;;
esac
exec /bin/cp "$@"
''')
        cp.chmod(0o755)
        result = self.run_connect("good-B")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.conf.read_text(), CONF_MODE_B)
        self.assertEqual((self.state / "runtime.conf").read_text(), CONF_MODE_B)
        self.assertEqual(self.marker.read_text(), original_marker)
        self.assertNotIn("reconfigure", (self.state / "calls.log").read_text())
        self.assertEqual(list(self.run_dir.glob("*.rollback.*")), [])
        self.assertIn("could not preserve previous checkpoint", result.stderr)

    def test_checkpoint_queries_do_not_consume_one_second_connection_budget(self):
        self.set_policy(attempt=1)
        stub = self.stub / "wpa_cli"
        stub.write_text(stub.read_text().replace(
            '    case "$command" in\n', '''    if grep -q 'ssid="jhw_wlan_"' "$runtime"; then
        case "$command" in status|get_network|get) sleep 0.3 ;; esac
    fi
    case "$command" in
''', 1).replace(
            '                cp "$WPA_CONF_DIR/wpa_supplicant-${2}.conf" "$runtime"\n',
            '                cp "$WPA_CONF_DIR/wpa_supplicant-${2}.conf" "$runtime"\n'
            '                if grep -q \'ssid="good-B"\' "$runtime"; then sleep 0.5; fi\n', 1))
        start = time.monotonic()
        result = self.run_connect("good-B")
        elapsed = time.monotonic() - start
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('ssid="good-B"', self.conf.read_text())
        self.assertIn("ssid=good-B\n", (self.state / "status").read_text())
        self.assertGreater(elapsed, 1.5)
        self.assertLess(elapsed, 6)

    def test_disabled_policy_keeps_existing_persist_on_failure_behavior(self):
        self.set_policy(enabled=False)
        result = self.run_connect("unreachable")
        self.assertEqual(result.returncode, 8, result.stdout + result.stderr)
        self.assertIn('ssid="unreachable"', self.conf.read_text())
        self.assertNotIn("fallback restored", result.stdout + result.stderr)

    def test_unverified_profile_is_never_used_as_a_fallback(self):
        self.write_conf(CONF_MODE_B.replace('ssid="jhw_wlan_"', 'ssid="unverified"'))
        result = self.run_connect("unreachable")
        self.assertEqual(result.returncode, 8, result.stdout + result.stderr)
        self.assertIn('ssid="unreachable"', self.conf.read_text())
        self.assertIn("fallback unavailable", result.stdout + result.stderr)

    def test_associated_ssid_must_match_the_loaded_profile_before_checkpoint(self):
        (self.state / "status").write_text(_cli.STATUS_INITIAL.replace("ssid=jhw_wlan_", "ssid=other-associated"))
        result = self.run_wifi("ssid", "unreachable")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(list(self.run_dir.glob("*.last-good.json")), [])

    def test_association_changing_during_profile_queries_is_not_checkpointed(self):
        stub = self.stub / "wpa_cli"
        stub.write_text(stub.read_text().replace(
            'get_network)\n', 'get_network)\n'
            '            if [ -f "$D/swap-status" ]; then\n'
            '                rm -f "$D/swap-status"\n'
            '                printf "ssid=other-associated\\nid=1\\nfreq=5200\\nwpa_state=COMPLETED\\n" > "$D/status"\n'
            '            fi\n', 1))
        (self.state / "swap-status").touch()
        result = self.run_wifi("ssid", "unreachable")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(list(self.run_dir.glob("*.last-good.json")), [])

    def test_reconfigure_error_restores_previous_profile_and_preserves_error_code(self):
        (self.state / "fail-reconfigure").touch()
        result = self.run_connect("unreachable")
        self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
        self.assertEqual(self.conf.read_text(), CONF_MODE_B)
        self.assertIn("fallback restored", result.stdout + result.stderr)

    def test_failed_old_association_is_reported_as_failure(self):
        (self.state / "available").write_text("")
        result = self.run_connect("unreachable")
        self.assertEqual(result.returncode, 8, result.stdout + result.stderr)
        self.assertEqual(self.conf.read_text(), CONF_MODE_B)
        self.assertIn("fallback failed", result.stdout + result.stderr)

    def test_invalid_policy_is_rejected_before_profile_or_live_mutation(self):
        for enabled, attempt, rollback in (("true", 1, 1), (True, 0, 1),
                                            (True, 16, 1), (True, 1, 6), (True, True, 1)):
            with self.subTest(enabled=enabled, attempt=attempt, rollback=rollback):
                self.set_policy(enabled=enabled, attempt=attempt, rollback=rollback)
                result = self.run_connect("unreachable")
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertEqual(self.conf.read_text(), CONF_MODE_B)
                self.assertFalse((self.state / "target-entered").exists())

    def test_busy_changes_are_rejected_and_read_only_query_is_allowed(self):
        self.set_policy(attempt=3)
        (self.state / "block-target").touch()
        install = self.stub / "install"
        install.write_text(install.read_text().replace("args=()", '''case "${!#}" in
    "$WPA_CONF_DIR"/*) ;;
    *) echo "test refuses installation outside the private profile directory" >&2; exit 1 ;;
esac
args=()''', 1))
        process = self.start_connect()
        try:
            self.wait_for_target(process)
            before = self.conf.read_text()
            config_before = self.config.read_text()
            for command, value in (("ssid", "another-net"), ("freq", "5200"),
                                   ("psk", "changed-secret"), ("txpwr", "no"), ("gt", "192.0.2.1")):
                with self.subTest(command=command):
                    result = self.run_wifi(command, value, timeout=2)
                    self.assertEqual(result.returncode, 6, result.stdout + result.stderr)
                    self.assertIn("busy", result.stderr)
                    self.assertEqual(self.conf.read_text(), before)
                    self.assertEqual(self.config.read_text(), config_before)
            for args in (("mode",), ("bw",), ("roam", "diff"), ("roam", "th"),
                         ("roam", "gate"), ("mscan", "get"),
                         ("br", "profile", "mlan0-ip"), ("br", "factory-profile", "peer-route"),
                         ("br", "route", "find")):
                with self.subTest(query=args):
                    query = self.run_wifi(*args, timeout=2)
                    self.assertEqual(query.returncode, 0, query.stdout + query.stderr)
        finally:
            (self.state / "block-target").unlink(missing_ok=True)
            process.communicate(timeout=12)

    def test_busy_existing_writer_or_scan_is_refused_before_connection_budget(self):
        self.run_dir.mkdir(exist_ok=True)
        for name in ("mlan0.wpa-conf.lock", "mlan0.scan-transition.lock"):
            with self.subTest(lock=name), (self.run_dir / name).open("w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                env = self.environment()
                env["WIFI_SCAN_TRANSITION_LOCK_TIMEOUT"] = "15"
                try:
                    result = subprocess.run(["bash", str(WIFI_SH), "mlan0", "connect", "unreachable"],
                                            env=env, capture_output=True, text=True, timeout=2)
                    self.assertEqual(result.returncode, 6, result.stdout + result.stderr)
                    self.assertEqual(self.conf.read_text(), CONF_MODE_B)
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)

    def test_opc_changes_are_busy_during_manual_fallback(self):
        self.set_policy(attempt=3)
        (self.state / "block-target").touch()
        process = self.start_connect()
        try:
            self.wait_for_target(process)
            result = subprocess.run(["sh", str(SCRIPTS_DIR / "opc_wlan_apply.sh"),
                                     "mlan0", "freq", "5200"], env=self.environment(),
                                    capture_output=True, text=True, timeout=2)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("busy", result.stderr)
            self.assertIn('ssid="unreachable"', self.conf.read_text())
        finally:
            (self.state / "block-target").unlink(missing_ok=True)
            process.communicate(timeout=12)

    def test_direct_ftp_mutations_cannot_bypass_active_fallback(self):
        self.set_policy(attempt=3)
        (self.state / "block-target").touch()
        # Even on a test host with a real mlan0, never run its ip command.
        ip = self.stub / "ip"
        ip.write_text("#!/bin/sh\nexit 0\n")
        ip.chmod(0o755)
        handlers = SCRIPTS_DIR.parents[2] / "opt/ftpcmd/bin"
        raw = self.root / "wconnectraw"
        raw.write_text((handlers / "wconnectraw").read_text().replace(
            "WPA_CLI=/usr/sbin/wpa_cli", "WPA_CLI=" + shlex.quote(str(self.stub / "wpa_cli"))))
        process = self.start_connect()
        try:
            self.wait_for_target(process)
            for handler, args in ((raw, ["0", "another-net"]),
                                  (handlers / "ifcup", ["mlan0"]),
                                  (handlers / "ifcdown", ["mlan0"])):
                with self.subTest(handler=handler.name):
                    result = subprocess.run(["sh", str(handler), *args],
                                            env=self.environment(), capture_output=True,
                                            text=True, timeout=2)
                    self.assertEqual(result.returncode, 6, result.stdout + result.stderr)
                    self.assertIn("busy", result.stdout + result.stderr)
        finally:
            (self.state / "block-target").unlink(missing_ok=True)
            process.communicate(timeout=12)

    def test_opc_does_not_wait_or_fallback_when_policy_is_enabled(self):
        self.marker.write_text("slot=1\n")
        result = subprocess.run(["sh", str(SCRIPTS_DIR / "opc_wlan_apply.sh"),
                                 "mlan0", "ssid", "unreachable"], env=self.environment(),
                                capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('ssid="unreachable"', self.conf.read_text())
        self.assertNotIn("fallback", result.stdout + result.stderr)

    def test_snapshot_with_credentials_is_private_and_marker_is_restored(self):
        original_marker = "essid=original\nslot=1\n"
        self.marker.write_text(original_marker)
        result = self.run_connect("unreachable")
        self.assertEqual(result.returncode, 8, result.stdout + result.stderr)
        self.assertTrue(self.marker.exists(), "a failed switch must retain OPC boot restoration")
        self.assertEqual(self.marker.read_text(), original_marker)
        snapshots = list(self.run_dir.glob("*.last-good.json"))
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(stat.S_IMODE(snapshots[0].stat().st_mode), 0o600)
        self.assertNotIn("stub-psk-not-a-secret", result.stdout + result.stderr)

    def test_slow_status_calls_do_not_consume_the_rollback_budget(self):
        (self.state / "delay-target").touch()
        start = time.monotonic()
        result = self.run_connect("unreachable")
        elapsed = time.monotonic() - start
        self.assertEqual(result.returncode, 8, result.stdout + result.stderr)
        self.assertLess(elapsed, 6, result.stdout + result.stderr)
        self.assertEqual(self.conf.read_text(), CONF_MODE_B)
        self.assertIn("fallback restored", result.stdout + result.stderr)

    def test_signal_rolls_back_before_releasing_transaction_lock(self):
        self.set_policy(attempt=3)
        process = self.start_connect()
        try:
            self.wait_for_target(process)
            recovery = list(self.run_dir.glob("*.rollback.*"))
            self.assertEqual(len(recovery), 1)
            self.assertEqual(stat.S_IMODE(recovery[0].stat().st_mode), 0o600)
            self.assertEqual(json.loads(recovery[0].read_text())["profile"], CONF_MODE_B)
            process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=8)
            self.assertEqual(process.returncode, 143, stdout + stderr)
            self.assertEqual(self.conf.read_text(), CONF_MODE_B)
            self.assertIn("fallback restored", stdout + stderr)
            self.assertEqual(list(self.run_dir.glob("*.rollback.*")), [])
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=3)
