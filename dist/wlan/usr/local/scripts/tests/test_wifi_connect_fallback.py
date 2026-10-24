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
            if [ "${4:-}" = scan_res_valid_for_connect ]; then
                cat "$D/scan-valid" 2>/dev/null || echo 5
            else
                awk '/^[[:space:]]*freq_list=/{sub(/^[[:space:]]*freq_list=/, ""); print; exit}' "$runtime"
            fi
            exit 0 ;;
        set)
            echo "wpa_cli $*" >> "$D/calls.log"
            echo OK
            exit 0 ;;
        bss)
            cat "$D/bss" 2>/dev/null
            exit 0 ;;
        disconnect)
            printf 'wpa_state=DISCONNECTED\n' > "$D/status" ;;
        set_network)
            echo "wpa_cli $*" >> "$D/calls.log"
            sed -n 's/^wpa_state=//p' "$D/status" >> "$D/state-at-set-network"
            if [ "${5:-}" = ssid ]; then
                if [ -f "$D/fail-set-network" ]; then echo FAIL; exit 0; fi
                NEW_SSID="${6:-}" awk '/^[[:space:]]*ssid=/{sub(/ssid=.*/, "ssid=" ENVIRON["NEW_SSID"])} {print}' \
                    "$runtime" > "$runtime.new" && mv -f "$runtime.new" "$runtime"
            fi
            echo OK
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
        # Both SSIDs were in a scan 5 s ago (BSS RANGE=ALL MASK=id|age|ssid),
        # and the supplicant loaded the conf line wifi_init.sh writes.
        (self.state / "bss").write_text("id=1\nage=5\nssid=jhw_wlan_\nid=2\nage=5\nssid=good-B\n")
        (self.state / "scan-valid").write_text("180\n")
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

    def test_restore_is_verified_soon_after_its_connected_record(self):
        # The restored profile's event lands 30 ms after reconfigure, just after
        # the first restore poll already saw COMPLETED. A 0.1 s poll grid checks
        # again about 70 ms after the event; the restore wait wakes on the record.
        # The delayed event must not hold the stub's stdout, or reconfigure
        # would return only after the event and the first poll would see it.
        stub = self.stub / "wpa_cli"
        text = stub.read_text()
        delayed = '( sleep "$delay"; echo "$lid" > "$D/fire.tmp" && mv -f "$D/fire.tmp" "$D/fire" ) &'
        for old, new in (
                (delayed, delayed[:-1] + '>/dev/null 2>&1 &'),
                ('            WPA_ID="$id" "$action" "$iface" CONNECTED\n',
                 '            echo "fire $EPOCHREALTIME" >> "$D/timeline"\n'
                 '            WPA_ID="$id" "$action" "$iface" CONNECTED\n'),
                ('    status) cat "$D/status" ;;\n',
                 '    status) echo "status $EPOCHREALTIME" >> "$D/timeline"; cat "$D/status" ;;\n')):
            self.assertEqual(text.count(old), 1, old)
            text = text.replace(old, new)
        stub.write_text(text)
        (self.state / "fire-delay").write_text("0.03\n")
        (self.state / "daemon-interval").write_text("0.005\n")
        gaps = []
        for _ in range(3):
            self.write_conf(CONF_MODE_B)
            (self.state / "timeline").write_text("")
            result = self.run_connect("unreachable")
            self.assertEqual(result.returncode, 8, result.stdout + result.stderr)
            self.assertIn("fallback restored", result.stdout + result.stderr)
            events = [line.split() for line in (self.state / "timeline").read_text().splitlines()]
            fires = [i for i, (kind, _) in enumerate(events) if kind == "fire"]
            fire = float(events[fires[-1]][1])
            check = next(float(t) for kind, t in events[fires[-1]:] if kind == "status")
            gaps.append(round((check - fire) * 1000))
        self.assertLessEqual(sorted(gaps)[1], 40, gaps)

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

    def record_profile_queries(self):
        stub = self.stub / "wpa_cli"
        stub.write_text(stub.read_text().replace(
            '    case "$command" in\n',
            '    case "$command" in get_network|get) echo "$*" >> "$D/profile-queries.log" ;; esac\n'
            '    case "$command" in\n', 1))

    def test_ftp_switch_to_another_ssid_skips_already_associated_queries(self):
        self.set_policy(enabled=False)
        self.record_profile_queries()
        result = self.run_connect("good-B", apply_stored_conf=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ssid=good-B\n", (self.state / "status").read_text())
        # Only the cache check reads the live profile, once per value.
        log = self.state / "profile-queries.log"
        queries = log.read_text().splitlines() if log.exists() else []
        self.assertEqual(sorted(queries), sorted(set(queries)), queries)

    def test_ftp_same_ssid_keeps_association_without_reload(self):
        self.set_policy(enabled=False)
        self.record_profile_queries()
        result = self.run_connect("jhw_wlan_", apply_stored_conf=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("already associated", result.stdout)
        self.assertTrue((self.state / "profile-queries.log").exists())
        self.assertNotIn("reconfigure", (self.state / "calls.log").read_text())

    def ftp_switch_commands(self):
        return [line for line in (self.state / "calls.log").read_text().splitlines()
                if any(f" {c}" in line for c in (
                    "set_network", "disconnect", "set scan_res", "reassociate", "reconfigure"))]

    def test_ftp_ssid_switch_reassociates_from_cache_when_only_ssid_differs(self):
        self.set_policy(enabled=False)
        result = self.run_connect("good-B", apply_stored_conf=True, trace=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_cache_reassociate()
        self.assertIn('ssid="good-B"', self.conf.read_text())
        self.assertIn('ssid="good-B"', (self.state / "runtime.conf").read_text())
        self.assertIn("phase=cache_connect_requested", (self.state / "logger.log").read_text())
        self.assertIn("path=cache", (self.state / "logger.log").read_text())
        # The SSID only changes once the old association is gone, so no stop
        # in between can leave COMPLETED reported under the new SSID.
        self.assertEqual((self.state / "state-at-set-network").read_text(), "DISCONNECTED\n")

    def assert_cache_reassociate(self):
        # The supplicant's own scan_res_valid_for_connect decides; the switch
        # no longer changes it.
        self.assertEqual(self.ftp_switch_commands(), [
            "wpa_cli -i mlan0 disconnect",
            'wpa_cli -i mlan0 set_network 0 ssid "good-B"',
            "wpa_cli -i mlan0 reassociate",
        ])

    def assert_profile_reload(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        commands = self.ftp_switch_commands()
        self.assertIn("wpa_cli -i mlan0 reconfigure", commands)
        self.assertFalse(any("set scan_res" in c for c in commands), commands)
        self.assertIn("ssid=good-B\n", (self.state / "status").read_text())

    def test_ftp_ssid_switch_reloads_profile_when_credentials_are_staged(self):
        self.set_policy(enabled=False)
        self.run_dir.mkdir(exist_ok=True)
        (self.run_dir / "mlan0.credential-pending").touch()
        result = self.run_connect("good-B", apply_stored_conf=True)
        self.assert_profile_reload(result)
        self.assertFalse(any("set_network" in c for c in self.ftp_switch_commands()))

    def test_ftp_ssid_switch_reloads_profile_when_frequency_is_staged(self):
        self.set_policy(enabled=False)
        self.write_conf(_cli.CONF_MODE_B.replace("5180 5200 5220 5240", "5180 5200"))
        result = self.run_connect("good-B", apply_stored_conf=True)
        self.assert_profile_reload(result)
        self.assertIn("freq_list=5180 5200\n", (self.state / "runtime.conf").read_text())

    def test_ftp_ssid_switch_reloads_profile_when_last_scan_missed_target(self):
        # The last scan (5 s ago) probed only the current SSID; the target is
        # still within the limit from an older scan, which the supplicant ignores.
        self.set_policy(enabled=False)
        (self.state / "bss").write_text("id=1\nage=5\nssid=jhw_wlan_\nid=2\nage=86\nssid=good-B\n")
        result = self.run_connect("good-B", apply_stored_conf=True)
        self.assert_profile_reload(result)
        self.assertFalse(any("set_network" in c for c in self.ftp_switch_commands()))

    def test_ftp_ssid_switch_uses_cache_for_target_seen_while_last_scan_covered_channels(self):
        # Entries of one scan differ by the seconds it spent on its channels.
        self.set_policy(enabled=False)
        (self.state / "bss").write_text("id=1\nage=10\nssid=jhw_wlan_\nid=2\nage=15\nssid=good-B\n")
        result = self.run_connect("good-B", apply_stored_conf=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_cache_reassociate()

    def test_ftp_ssid_switch_uses_cache_within_configured_scan_validity(self):
        # A 150 s old scan still counts when the supplicant keeps results 180 s.
        self.set_policy(enabled=False)
        (self.state / "bss").write_text("id=1\nage=150\nssid=jhw_wlan_\nid=2\nage=150\nssid=good-B\n")
        result = self.run_connect("good-B", apply_stored_conf=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_cache_reassociate()

    def test_ssid_switch_keeps_the_global_scan_validity_line(self):
        # wifi_init.sh writes this line once per boot; a connect rewrites the
        # file and must keep it for the supplicant's next reconfigure.
        line = "scan_res_valid_for_connect=180\n"
        conf = CONF_MODE_B.replace("update_config=0\n", line + "update_config=0\n")
        for name, ftp in (("ordinary", False), ("ftp-cache", True)):
            with self.subTest(name):
                self.write_conf(conf)
                (self.state / "runtime.conf").write_text(conf)
                (self.state / "status").write_text(_cli.STATUS_INITIAL)
                (self.state / "calls.log").write_text("")
                self.set_policy(enabled=False)
                result = self.run_connect("good-B", apply_stored_conf=ftp)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                stored = self.conf.read_text()
                self.assertIn('ssid="good-B"', stored)
                self.assertEqual(stored.count("scan_res_valid_for_connect="), 1, stored)
                self.assertIn(line + "update_config=0\n", stored)
                if ftp:
                    self.assert_cache_reassociate()

    def test_ftp_ssid_switch_reloads_profile_when_scan_is_older_than_supplicant_limit(self):
        # A supplicant started before the conf line exists keeps its 5 s default.
        self.set_policy(enabled=False)
        (self.state / "scan-valid").write_text("5\n")
        result = self.run_connect("good-B", apply_stored_conf=True)
        self.assert_profile_reload(result)
        self.assertFalse(any("set_network" in c for c in self.ftp_switch_commands()))

    def test_ftp_ssid_switch_reloads_profile_when_rendering_changes_more_than_ssid(self):
        self.set_policy(enabled=False)
        legacy = _cli.CONF_MODE_B.replace("    scan_ssid=1\n", "    scan_ssid=1\n    scan_freq=5180 5200 5220 5240\n")
        self.write_conf(legacy)
        (self.state / "runtime.conf").write_text(legacy)
        result = self.run_connect("good-B", apply_stored_conf=True)
        self.assert_profile_reload(result)
        self.assertNotIn("scan_freq", self.conf.read_text())

    def test_ftp_ssid_switch_with_frequency_arguments_reloads_profile(self):
        self.set_policy(enabled=False)
        result = self.run_connect("good-B", "5220", apply_stored_conf=True)
        self.assert_profile_reload(result)

    def test_ftp_ssid_switch_reloads_profile_when_not_associated(self):
        self.set_policy(enabled=False)
        (self.state / "status").write_text(
            _cli.STATUS_INITIAL.replace("wpa_state=COMPLETED", "wpa_state=ASSOCIATING"))
        result = self.run_connect("good-B", apply_stored_conf=True)
        self.assert_profile_reload(result)

    def test_ordinary_connect_keeps_the_full_profile_reload(self):
        self.set_policy(enabled=False)
        result = self.run_connect("good-B")
        self.assert_profile_reload(result)

    def test_rejected_ssid_change_falls_back_to_profile_reload(self):
        self.set_policy(enabled=False)
        (self.state / "fail-set-network").touch()
        result = self.run_connect("good-B", apply_stored_conf=True)
        self.assert_profile_reload(result)
        self.assertEqual((self.state / "state-at-set-network").read_text(), "DISCONNECTED\n")

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

    def record_sync_calls(self, fail_directory_sync=False):
        # One line per call: its arguments, or <global> for a system-wide sync.
        fail = 'if [ "$#" -ge 2 ]; then exit 1; fi\n' if fail_directory_sync else ""
        (self.stub / "sync").write_text(
            '#!/bin/sh\n'
            'if [ "$#" -eq 0 ]; then echo "<global>"; else echo "$*"; fi >> "$WPA_STUB_DIR/sync.log"\n'
            + fail + 'exit 0\n')

    def sync_calls(self):
        log = self.state / "sync.log"
        return log.read_text().splitlines() if log.exists() else []

    def persistent_directories(self):
        return f"{self.conf_dir} {self.marker.parent}"

    def test_switch_syncs_the_profile_and_marker_directories_not_everything(self):
        self.record_sync_calls()
        for name, ftp, fallback in (("ordinary", False, True), ("ftp-cache", True, False)):
            with self.subTest(name):
                self.write_conf(CONF_MODE_B)
                (self.state / "runtime.conf").write_text(CONF_MODE_B)
                (self.state / "status").write_text(_cli.STATUS_INITIAL)
                (self.state / "sync.log").write_text("")
                self.marker.write_text("essid=original\nslot=1\n")
                self.set_policy(enabled=fallback)
                result = self.run_connect("good-B", apply_stored_conf=ftp)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                # The marker removal is made durable by the directory sync.
                self.assertFalse(self.marker.exists())
                calls = self.sync_calls()
                self.assertNotIn("<global>", calls, calls)
                self.assertEqual(calls[-1], self.persistent_directories(), calls)

    def test_failed_switch_leaves_no_temp_file_next_to_the_profile_or_marker(self):
        self.record_sync_calls()
        self.marker.write_text("essid=original\nslot=1\n")
        result = self.run_connect("unreachable")
        self.assertEqual(result.returncode, 8, result.stdout + result.stderr)
        self.assertIn("fallback restored", result.stderr)
        leftovers = [p.name for p in (*self.conf_dir.iterdir(), *self.marker.parent.iterdir())
                     if ".fallback." in p.name or ".install." in p.name]
        self.assertEqual(leftovers, [])
        calls = self.sync_calls()
        self.assertNotIn("<global>", calls, calls)
        self.assertEqual(calls[-1], self.persistent_directories(), calls)

    def test_directory_sync_failure_falls_back_to_a_global_sync(self):
        self.record_sync_calls(fail_directory_sync=True)
        result = self.run_connect("good-B")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.sync_calls()[-2:], [self.persistent_directories(), "<global>"])

    def test_switch_removes_its_temp_files_and_rollback_copy(self):
        tmp = self.root / "tmp"
        tmp.mkdir()
        self.set_policy(enabled=True)
        result = self.run_connect("good-B")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # The first switch only checkpoints; the second one arms a rollback copy.
        result = self.run_connect("jhw_wlan_", extra_env={"TMPDIR": str(tmp)})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(sorted(p.name for p in tmp.iterdir()), [])
        self.assertEqual(list(self.run_dir.glob("*.rollback.*")), [])

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
