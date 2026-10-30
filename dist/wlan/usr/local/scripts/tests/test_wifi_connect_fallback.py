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
                ssid)
                    if [ -f "$D/delay-first-ssid-query" ]; then
                        read -r delay < "$D/delay-first-ssid-query" || exit 1
                        rm -f "$D/delay-first-ssid-query"
                        sleep "$delay"
                    fi
                    awk '/^[[:space:]]*ssid=/{sub(/^[[:space:]]*ssid=/, ""); print; exit}' "$runtime" ;;
                freq_list) awk '/^[[:space:]]*freq_list=/{sub(/^[[:space:]]*freq_list=/, ""); print; exit}' "$runtime" ;;
                key_mgmt) echo WPA-PSK ;;
                psk)
                    if [ -f "$D/mask-psk" ]; then
                        echo '*'
                    else
                        awk '/^[[:space:]]*psk=/{sub(/^[[:space:]]*psk=/, ""); print; exit}' "$runtime"
                    fi ;;
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
        list_networks)
            if [ -f "$D/network-list" ]; then cat "$D/network-list"; exit 0; fi
            ssid=$(awk '/^[[:space:]]*ssid=/{sub(/^[[:space:]]*ssid="/, ""); sub(/"$/, ""); print; exit}' "$runtime")
            printf 'network id / ssid / bssid / flags\n0\t%s\tany\t\n' "$ssid"
            exit 0 ;;
        set)
            echo "wpa_cli $*" >> "$D/calls.log"
            if [ "${4:-}" = freq_list ]; then
                NEW_FREQ="${5:-}" awk '
                    /^[[:space:]]*freq_list=/{
                        if (!done) { sub(/freq_list=.*/, "freq_list=" ENVIRON["NEW_FREQ"]); done=1 }
                    }
                    { print }
                ' "$runtime" > "$runtime.new" && mv -f "$runtime.new" "$runtime"
            fi
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
            elif [ "${5:-}" = psk ]; then
                if [ -f "$D/fail-set-psk" ]; then echo FAIL; exit 0; fi
                NEW_PSK="${6:-}" awk '/^[[:space:]]*psk=/{sub(/psk=.*/, "psk=" ENVIRON["NEW_PSK"])} {print}' \
                    "$runtime" > "$runtime.new" && mv -f "$runtime.new" "$runtime"
            elif [ "${5:-}" = freq_list ]; then
                NEW_FREQ="${6:-}" awk '
                    BEGIN { in_net=0 }
                    /^[[:space:]]*network[[:space:]]*=/ { in_net=1 }
                    in_net && /^[[:space:]]*freq_list=/{
                        if (!done) { sub(/freq_list=.*/, "freq_list=" ENVIRON["NEW_FREQ"]); done=1 }
                    }
                    { print }
                ' "$runtime" > "$runtime.new" && mv -f "$runtime.new" "$runtime"
            fi
            echo OK
            exit 0 ;;
        status)
            if [ -f "$D/delay-reconnect-status" ] && [ -f "$D/reconnect-requested" ]; then
                read -r delay < "$D/delay-reconnect-status" || exit 1
                : > "$D/slow-reconnect-status-started"
                sleep "$delay"
                : > "$D/slow-reconnect-status-finished"
            fi
            if [ -f "$D/delay-target" ] && grep -q 'ssid="unreachable"' "$runtime"; then
                delay=3
                read -r delay < "$D/delay-target" || delay=3
                sleep "$delay"
            fi ;;
        reconfigure|reassociate|reconnect|scan)
            case "$command" in reassociate|reconnect) : > "$D/reconnect-requested" ;; esac
            if [ "$command" = reconfigure ]; then
                cp "$WPA_CONF_DIR/wpa_supplicant-${2}.conf" "$runtime"
                if [ -f "$D/delay-first-source-reconfigure" ] \
                   && ! grep -q 'ssid="unreachable"' "$runtime"; then
                    delay=6.5
                    read -r delay < "$D/delay-first-source-reconfigure" || delay=6.5
                    rm -f "$D/delay-first-source-reconfigure"
                    sleep "$delay"
                fi
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
            if grep -Fxq -- "$ssid" "$D/available"; then
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
        self.run_dir.mkdir(exist_ok=True)
        self.run_dir.chmod(0o755)
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
                    WIFI_RUNTIME_STAGE_EXPECT_UID=str(os.getuid()),
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

    def stage_runtime_psk(self, secret="runtime-secret"):
        self.run_dir.mkdir(exist_ok=True)
        stage = self.run_dir / "mlan0.runtime-psk"
        stage.write_text(secret)
        stage.chmod(0o600)
        return stage

    def run_runtime_connect(self, ssid, secret="runtime-secret", **kwargs):
        self.stage_runtime_psk(secret)
        extra_env = dict(kwargs.pop("extra_env", {}) or {}, FTPCMD_RUNTIME_PSK="1")
        return self.run_connect(ssid, apply_stored_conf=True, extra_env=extra_env, **kwargs)

    def persistent_state(self):
        conf_stat = self.conf.stat()
        marker_stat = self.marker.stat() if self.marker.exists() else None
        return {
            "conf": self.conf.read_bytes(),
            "conf_inode": conf_stat.st_ino,
            "conf_mtime_ns": conf_stat.st_mtime_ns,
            "marker": self.marker.read_bytes() if marker_stat else None,
            "marker_inode": marker_stat.st_ino if marker_stat else None,
            "marker_mtime_ns": marker_stat.st_mtime_ns if marker_stat else None,
        }

    def prepare_mlan1(self):
        self.conf = self.conf_dir / "wpa_supplicant-mlan1.conf"
        self.write_conf(CONF_MODE_B)
        (self.state / "runtime.conf").write_text(CONF_MODE_B)
        self.config.write_text(json.dumps({"mlan1": {"connect_fallback": {
            "enabled": False, "attempt_timeout_sec": 1,
            "rollback_timeout_sec": 1,
        }}}))

    def run_mlan1_connect(self, *args, runtime=False):
        env = self.environment()
        env["FTPCMD_APPLY_CONF"] = "1"
        if runtime:
            env["FTPCMD_RUNTIME_PSK"] = "1"
        return subprocess.run(
            ["bash", str(WIFI_SH), "mlan1", "connect", *args],
            env=env, capture_output=True, text=True, timeout=60)

    def test_mlan1_runtime_rollback_keeps_new_mlan0_marker(self):
        self.prepare_mlan1()
        stage = self.run_dir / "mlan1.runtime-psk"
        stage.write_text("runtime-secret")
        stage.chmod(0o600)
        (self.state / "fail-set-psk").touch()
        marker_text = "essid=new-mlan0-value\nslot=9\n"
        (self.state / "new-opc-marker").write_text(marker_text)
        stub = self.stub / "wpa_cli"
        source = stub.read_text()
        before = 'if [ -f "$D/fail-set-psk" ]; then echo FAIL; exit 0; fi'
        after = 'cat "$D/new-opc-marker" > "$OPC_ESSID_MARKER"\n                ' + before
        self.assertEqual(source.count(before), 1)
        stub.write_text(source.replace(before, after, 1))

        result = self.run_mlan1_connect("good-B", runtime=True)
        self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
        self.assertEqual(self.conf.read_text(), CONF_MODE_B)
        self.assertEqual(self.marker.read_text(), marker_text)

    def test_mlan1_stale_recovery_keeps_changed_mlan0_marker(self):
        self.prepare_mlan1()
        target = CONF_MODE_B.replace('ssid="jhw_wlan_"', 'ssid="good-B"')
        self.conf.write_text(target)
        (self.state / "runtime.conf").write_text(target)
        marker = self.run_dir / "mlan1.runtime-switch-in-progress"
        marker.write_text("marker=present:600\ncheckpoint=absent\n")
        marker.chmod(0o600)
        original = self.run_dir / "mlan1.runtime-original.conf"
        original.write_text(CONF_MODE_B)
        original.chmod(0o600)
        original_marker = self.run_dir / "mlan1.runtime-original.marker"
        original_marker.write_text("essid=old-mlan0-value\n")
        original_marker.chmod(0o600)
        marker_text = "essid=new-mlan0-value\nslot=9\n"
        self.marker.write_text(marker_text)

        result = self.run_mlan1_connect()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.conf.read_text(), CONF_MODE_B)
        self.assertEqual(self.marker.read_text(), marker_text)
        self.assertFalse(marker.exists())

    def test_mlan1_stale_unmanaged_marker_recovers_without_touching_mlan0(self):
        self.prepare_mlan1()
        target = CONF_MODE_B.replace('ssid="jhw_wlan_"', 'ssid="good-B"')
        self.conf.write_text(target)
        (self.state / "runtime.conf").write_text(target)
        marker = self.run_dir / "mlan1.runtime-switch-in-progress"
        marker.write_text("marker=unmanaged\ncheckpoint=absent\n")
        marker.chmod(0o600)
        original = self.run_dir / "mlan1.runtime-original.conf"
        original.write_text(CONF_MODE_B)
        original.chmod(0o600)
        marker_text = "essid=concurrent-mlan0-value\n"
        self.marker.write_text(marker_text)

        result = self.run_mlan1_connect()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.conf.read_text(), CONF_MODE_B)
        self.assertEqual(self.marker.read_text(), marker_text)
        self.assertFalse(marker.exists())

    def test_mlan1_runtime_switch_does_not_snapshot_mlan0_marker(self):
        self.prepare_mlan1()
        stage = self.run_dir / "mlan1.runtime-psk"
        stage.write_text("runtime-secret")
        stage.chmod(0o600)
        target = self.root / "outside-opc-marker"
        target.write_text("essid=other\n")
        self.marker.symlink_to(target)

        result = self.run_mlan1_connect("good-B", runtime=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.marker.resolve(), target)
        self.assertEqual(target.read_text(), "essid=other\n")
        self.assertFalse((self.run_dir / "mlan1.runtime-original.marker").exists())

    def test_runtime_first_switch_persists_both_fields_only_after_verified_landing(self):
        result = self.run_runtime_connect("good-B")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        stored = self.conf.read_text()
        self.assertIn('ssid="good-B"', stored)
        self.assertIn('psk="runtime-secret"', stored)
        calls = (self.state / "calls.log").read_text().splitlines()
        switch = [line for line in calls if any(command in line for command in (
            " disconnect", " set_network", " reassociate", " reconfigure"))]
        self.assertEqual(switch[:4], [
            "wpa_cli -i mlan0 disconnect",
            'wpa_cli -i mlan0 set_network 0 ssid "good-B"',
            'wpa_cli -i mlan0 set_network 0 psk "runtime-secret"',
            "wpa_cli -i mlan0 reassociate",
        ])
        self.assertFalse((self.run_dir / "mlan0.runtime-psk").exists())
        self.assertFalse((self.run_dir / "mlan0.runtime-psk.active").exists())
        self.assertFalse((self.run_dir / "mlan0.runtime-switch-in-progress").exists())
        self.assertNotIn("runtime-secret", result.stdout + result.stderr)

    def test_runtime_first_timeout_restores_original_file_marker_and_association(self):
        marker = "essid=original\nslot=7\n"
        self.marker.write_text(marker)
        result = self.run_runtime_connect("unreachable")
        self.assertEqual(result.returncode, 8, result.stdout + result.stderr)
        self.assertEqual(self.conf.read_text(), CONF_MODE_B)
        self.assertEqual((self.state / "runtime.conf").read_text(), CONF_MODE_B)
        self.assertEqual(self.marker.read_text(), marker)
        self.assertIn("ssid=jhw_wlan_\n", (self.state / "status").read_text())
        self.assertFalse((self.run_dir / "mlan0.runtime-psk.active").exists())
        self.assertFalse((self.run_dir / "mlan0.runtime-switch-in-progress").exists())
        self.assertNotIn("runtime-secret", result.stdout + result.stderr)

    def test_runtime_slow_status_preserves_time_to_restore_original_association(self):
        self.set_policy(attempt=15, rollback=5)
        (self.state / "delay-target").write_text("0.3\n")

        result = self.run_runtime_connect("unreachable")

        self.assertEqual(result.returncode, 8, result.stdout + result.stderr)
        self.assertEqual(self.conf.read_text(), CONF_MODE_B)
        self.assertEqual((self.state / "runtime.conf").read_text(), CONF_MODE_B)
        self.assertIn("ssid=jhw_wlan_\n", (self.state / "status").read_text())
        self.assertFalse((self.run_dir / "mlan0.runtime-switch-in-progress").exists())

    def test_runtime_masked_psk_preflight_keeps_monitor_for_switch_and_restore(self):
        self.set_policy(attempt=1, rollback=1)
        (self.state / "mask-psk").touch()
        (self.state / "delay-first-source-reconfigure").write_text("6.5\n")

        result = self.run_runtime_connect("unreachable")

        self.assertEqual(result.returncode, 8, result.stdout + result.stderr)
        self.assertEqual(self.conf.read_text(), CONF_MODE_B)
        self.assertEqual((self.state / "runtime.conf").read_text(), CONF_MODE_B)
        self.assertIn("ssid=jhw_wlan_\n", (self.state / "status").read_text())
        self.assertFalse((self.run_dir / "mlan0.runtime-switch-in-progress").exists())

    def test_runtime_first_rejected_psk_is_rolled_back_without_persisting(self):
        (self.state / "fail-set-psk").touch()
        result = self.run_runtime_connect("good-B")
        self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
        self.assertEqual(self.conf.read_text(), CONF_MODE_B)
        self.assertEqual((self.state / "runtime.conf").read_text(), CONF_MODE_B)

    def test_runtime_first_rejects_a_live_credential_that_differs_from_storage(self):
        runtime = CONF_MODE_B.replace(
            'psk="stub-psk-not-a-secret"', 'psk="runtime-only-credential"')
        (self.state / "runtime.conf").write_text(runtime)
        result = self.run_runtime_connect("good-B")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("stored and live credentials do not match", result.stderr)
        self.assertEqual(self.conf.read_text(), CONF_MODE_B)
        calls = self.state / "calls.log"
        self.assertNotIn(" disconnect", calls.read_text() if calls.exists() else "")

    def test_runtime_first_masked_live_psk_is_proved_by_stored_profile_reassociation(self):
        (self.state / "mask-psk").touch()
        result = self.run_runtime_connect("good-B")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = (self.state / "calls.log").read_text().splitlines()
        reconfigure = calls.index("wpa_cli -i mlan0 reconfigure")
        disconnect = calls.index("wpa_cli -i mlan0 disconnect")
        self.assertLess(reconfigure, disconnect)
        self.assertIn('ssid="good-B"', self.conf.read_text())
        self.assertIn('psk="runtime-secret"', self.conf.read_text())

    def test_runtime_first_rejects_a_symlink_stage_without_touching_the_link(self):
        target = self.root / "outside-secret"
        target.write_text("runtime-secret")
        self.run_dir.mkdir(exist_ok=True)
        (self.run_dir / "mlan0.runtime-psk").symlink_to(target)
        result = self.run_connect("good-B", apply_stored_conf=True,
                                  extra_env={"FTPCMD_RUNTIME_PSK": "1"})
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(target.read_text(), "runtime-secret")
        self.assertEqual(self.conf.read_text(), CONF_MODE_B)

    def test_stage_psk_creates_a_private_runtime_file_without_logging_the_secret(self):
        secret = "private-runtime-secret"
        result = self.run_wifi("stage-psk", secret)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        stage = self.run_dir / "mlan0.runtime-psk"
        self.assertEqual(stage.read_text(), secret)
        self.assertEqual(stat.S_IMODE(stage.stat().st_mode), 0o600)
        log = (self.state / "logger.log").read_text()
        self.assertIn("stage-psk [redacted]", log)
        self.assertNotIn(secret, log + result.stdout + result.stderr)

    def test_stage_psk_rejects_characters_outside_the_ftp_command_set(self):
        for secret in ('bad"credential', r"bad\credential", "bad$credential"):
            with self.subTest(secret=secret):
                result = self.run_wifi("stage-psk", secret)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertFalse((self.run_dir / "mlan0.runtime-psk").exists())

    def test_stage_psk_refuses_to_replace_state_from_an_active_transaction(self):
        self.run_dir.mkdir(exist_ok=True)
        active = self.run_dir / "mlan0.runtime-psk.active"
        active.write_text("active-secret")
        active.chmod(0o600)
        result = self.run_wifi("stage-psk", "next-secret")
        self.assertEqual(result.returncode, 6, result.stdout + result.stderr)
        self.assertEqual(active.read_text(), "active-secret")
        self.assertFalse((self.run_dir / "mlan0.runtime-psk").exists())

    def test_persistent_psk_then_stage_rejects_conflicting_credential_mode(self):
        persistent = self.run_wifi("psk", "persistent-secret")
        self.assertEqual(persistent.returncode, 0, persistent.stdout + persistent.stderr)
        stored = self.conf.read_text()
        self.assertIn('psk="persistent-secret"', stored)
        self.assertTrue((self.run_dir / "mlan0.credential-pending").exists())

        staged = self.run_wifi("stage-psk", "staged-secret")
        self.assertEqual(staged.returncode, 6, staged.stdout + staged.stderr)
        self.assertEqual(self.conf.read_text(), stored)
        self.assertFalse((self.run_dir / "mlan0.runtime-psk").exists())

        applied = self.run_connect("jhw_wlan_", apply_stored_conf=True)
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        self.assertFalse((self.run_dir / "mlan0.credential-pending").exists())

    def test_staged_psk_then_persistent_psk_rejects_conflicting_credential_mode(self):
        staged = self.run_wifi("stage-psk", "staged-secret")
        self.assertEqual(staged.returncode, 0, staged.stdout + staged.stderr)

        persistent = self.run_wifi("psk", "persistent-secret")
        self.assertEqual(persistent.returncode, 6, persistent.stdout + persistent.stderr)
        self.assertEqual(self.conf.read_text(), CONF_MODE_B)
        self.assertEqual((self.run_dir / "mlan0.runtime-psk").read_text(), "staged-secret")
        self.assertFalse((self.run_dir / "mlan0.credential-pending").exists())

        applied = self.run_connect("good-B", apply_stored_conf=True,
                                   extra_env={"FTPCMD_RUNTIME_PSK": "1"})
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        self.assertIn('psk="staged-secret"', self.conf.read_text())
        self.assertFalse((self.run_dir / "mlan0.runtime-psk").exists())

    def test_staged_psk_blocks_persistent_key_change_too(self):
        staged = self.run_wifi("stage-psk", "staged-secret")
        self.assertEqual(staged.returncode, 0, staged.stdout + staged.stderr)

        changed = self.run_wifi("key", "NONE")
        self.assertEqual(changed.returncode, 6, changed.stdout + changed.stderr)
        self.assertEqual(self.conf.read_text(), CONF_MODE_B)
        self.assertEqual((self.run_dir / "mlan0.runtime-psk").read_text(), "staged-secret")

    def test_concurrent_stage_psk_publish_is_rejected_by_the_exclusive_guard(self):
        mv = self.stub / "mv"
        mv.write_text('''#!/bin/bash
last="${!#}"
case "$last" in
    *.runtime-psk)
        if [ ! -e "$WPA_STUB_DIR/stage-entered" ]; then
            : > "$WPA_STUB_DIR/stage-entered"
            while [ ! -e "$WPA_STUB_DIR/stage-release" ]; do sleep 0.01; done
        fi ;;
esac
exec /bin/mv "$@"
''')
        mv.chmod(0o755)
        first = subprocess.Popen(
            ["bash", str(WIFI_SH), "mlan0", "stage-psk", "first-secret"],
            env=self.environment(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 5
            while not (self.state / "stage-entered").exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue((self.state / "stage-entered").exists())
            second = self.run_wifi("stage-psk", "second-secret")
            self.assertEqual(second.returncode, 6, second.stdout + second.stderr)
            (self.state / "stage-release").touch()
            stdout, stderr = first.communicate(timeout=5)
            self.assertEqual(first.returncode, 0, stdout + stderr)
            self.assertEqual((self.run_dir / "mlan0.runtime-psk").read_text(), "first-secret")
        finally:
            (self.state / "stage-release").touch()
            if first.poll() is None:
                first.kill()
                first.communicate(timeout=3)

    def test_stage_psk_rejects_a_group_or_world_writable_runtime_directory(self):
        self.run_dir.mkdir(exist_ok=True)
        for mode in (0o775, 0o757):
            with self.subTest(mode=oct(mode)):
                self.run_dir.chmod(mode)
                result = self.run_wifi("stage-psk", "next-secret")
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertFalse((self.run_dir / "mlan0.runtime-psk").exists())
        self.run_dir.chmod(0o755)

    def test_runtime_first_late_sync_failure_restores_original_persistence_and_link(self):
        sync = self.stub / "sync"
        sync.write_text('''#!/bin/sh
conf="$WPA_CONF_DIR/wpa_supplicant-mlan0.conf"
if grep -q 'ssid="good-B"' "$conf"; then
    count=$(cat "$WPA_STUB_DIR/sync-fail-count" 2>/dev/null || echo 0)
    count=$((count + 1))
    echo "$count" > "$WPA_STUB_DIR/sync-fail-count"
    [ "$count" -gt 2 ] || exit 1
fi
exec /bin/sync "$@"
''')
        sync.chmod(0o755)
        result = self.run_runtime_connect("good-B")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.conf.read_text(), CONF_MODE_B)
        self.assertEqual((self.state / "runtime.conf").read_text(), CONF_MODE_B)
        self.assertIn("ssid=jhw_wlan_\n", (self.state / "status").read_text())
        self.assertFalse((self.run_dir / "mlan0.runtime-switch-in-progress").exists())

    def test_runtime_first_checkpoint_publication_failure_restores_previous_checkpoint(self):
        sync = self.stub / "sync"
        sync.write_text('''#!/bin/sh
if [ "${1:-}" = "$WIFI_RUN_DIR" ] || [ "$#" -eq 0 ]; then
    checkpoint="$WIFI_RUN_DIR/mlan0.last-good.json"
    if [ -f "$checkpoint" ] \
       && jq -e '.ssid == "good-B"' "$checkpoint" >/dev/null 2>&1; then
        exit 1
    fi
fi
exec /bin/sync "$@"
''')
        sync.chmod(0o755)
        result = self.run_runtime_connect("good-B")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.conf.read_text(), CONF_MODE_B)
        self.assertEqual((self.state / "runtime.conf").read_text(), CONF_MODE_B)
        checkpoint = json.loads((self.run_dir / "mlan0.last-good.json").read_text())
        self.assertEqual(checkpoint["ssid"], "jhw_wlan_")
        self.assertEqual(checkpoint["profile"], CONF_MODE_B)
        self.assertEqual(list(self.run_dir.glob("*.rollback.*")), [])

    def test_runtime_first_cleanup_failure_after_commit_restores_previous_checkpoint(self):
        rm = self.stub / "rm"
        rm.write_text('''#!/bin/sh
for arg in "$@"; do
    case "$arg" in
        *.runtime-switch-in-progress)
            if grep -q 'ssid="good-B"' "$WPA_CONF_DIR/wpa_supplicant-mlan0.conf" \
               && [ ! -e "$WPA_STUB_DIR/failed-marker-remove" ]; then
                : > "$WPA_STUB_DIR/failed-marker-remove"
                exit 1
            fi ;;
    esac
done
exec /bin/rm "$@"
''')
        rm.chmod(0o755)
        result = self.run_runtime_connect("good-B")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.conf.read_text(), CONF_MODE_B)
        self.assertEqual((self.state / "runtime.conf").read_text(), CONF_MODE_B)
        checkpoint = json.loads((self.run_dir / "mlan0.last-good.json").read_text())
        self.assertEqual(checkpoint["ssid"], "jhw_wlan_")
        self.assertEqual(checkpoint["profile"], CONF_MODE_B)
        self.assertFalse((self.run_dir / "mlan0.runtime-switch-in-progress").exists())

    def test_term_after_commit_marker_removal_preserves_the_target_checkpoint(self):
        rm = self.stub / "rm"
        rm.write_text('''#!/bin/sh
for arg in "$@"; do
    case "$arg" in
        *.runtime-switch-in-progress)
            if grep -q 'ssid="good-B"' "$WPA_CONF_DIR/wpa_supplicant-mlan0.conf"; then
                /bin/rm "$@" || exit $?
                kill -TERM "$PPID"
                exit 0
            fi ;;
    esac
done
exec /bin/rm "$@"
''')
        rm.chmod(0o755)
        result = self.run_runtime_connect("good-B")
        self.assertEqual(result.returncode, 143, result.stdout + result.stderr)
        self.assertIn('ssid="good-B"', self.conf.read_text())
        self.assertIn('psk="runtime-secret"', self.conf.read_text())
        self.assertIn('ssid="good-B"', (self.state / "runtime.conf").read_text())
        checkpoint = json.loads((self.run_dir / "mlan0.last-good.json").read_text())
        self.assertEqual(checkpoint["ssid"], "good-B")
        self.assertIn('ssid="good-B"', checkpoint["profile"])
        self.assertFalse((self.run_dir / "mlan0.runtime-switch-in-progress").exists())

    def test_failed_rollback_keeps_original_snapshot_for_the_next_connect(self):
        self.set_policy(enabled=False)
        sync = self.stub / "sync"
        sync.write_text('''#!/bin/sh
conf="$WPA_CONF_DIR/wpa_supplicant-mlan0.conf"
if grep -q 'ssid="good-B"' "$conf"; then
    count=$(cat "$WPA_STUB_DIR/recovery-sync-count" 2>/dev/null || echo 0)
    count=$((count + 1))
    echo "$count" > "$WPA_STUB_DIR/recovery-sync-count"
    [ "$count" -gt 4 ] || exit 1
fi
exec /bin/sync "$@"
''')
        sync.chmod(0o755)
        result = self.run_runtime_connect("good-B")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('ssid="good-B"', self.conf.read_text())
        marker = self.run_dir / "mlan0.runtime-switch-in-progress"
        original = self.run_dir / "mlan0.runtime-original.conf"
        self.assertTrue(marker.is_file())
        self.assertTrue(original.is_file())
        self.assertEqual(original.read_text(), CONF_MODE_B)
        self.assertEqual(stat.S_IMODE(marker.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(original.stat().st_mode), 0o600)

        sync.write_text("#!/bin/sh\nexec /bin/sync \"$@\"\n")
        recovered = self.run_connect(apply_stored_conf=True)
        self.assertEqual(recovered.returncode, 0, recovered.stdout + recovered.stderr)
        self.assertEqual(self.conf.read_text(), CONF_MODE_B)
        self.assertEqual((self.state / "runtime.conf").read_text(), CONF_MODE_B)
        self.assertFalse(marker.exists())
        self.assertFalse(original.exists())

    def test_runtime_first_term_restores_before_returning_the_signal_status(self):
        self.stage_runtime_psk()
        (self.state / "block-target").touch()
        env = dict(self.environment(), FTPCMD_APPLY_CONF="1", FTPCMD_RUNTIME_PSK="1")
        process = subprocess.Popen(
            ["bash", str(WIFI_SH), "mlan0", "connect", "unreachable"],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                log = self.state / "calls.log"
                calls = log.read_text() if log.exists() else ""
                if "set_network 0 psk" in calls:
                    break
                if process.poll() is not None:
                    break
                time.sleep(0.02)
            self.assertIn("set_network 0 psk", (self.state / "calls.log").read_text())
            process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=8)
            self.assertEqual(process.returncode, 143, stdout + stderr)
            self.assertEqual(self.conf.read_text(), CONF_MODE_B)
            self.assertEqual((self.state / "runtime.conf").read_text(), CONF_MODE_B)
            self.assertIn("ssid=jhw_wlan_\n", (self.state / "status").read_text())
            self.assertFalse((self.run_dir / "mlan0.runtime-switch-in-progress").exists())
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=3)

    def test_no_ssid_connect_restores_stale_profile_and_checkpoint_before_capture(self):
        # Model SIGKILL after target conf/link/checkpoint publication but before
        # the in-progress marker is removed. Recovery must consume the fixed
        # original snapshots before fallback capture can overwrite last-good.
        runtime = CONF_MODE_B.replace('ssid="jhw_wlan_"', 'ssid="good-B"').replace(
            'psk="stub-psk-not-a-secret"', 'psk="runtime-secret"')
        (self.state / "runtime.conf").write_text(runtime)
        (self.state / "status").write_text(
            _cli.STATUS_INITIAL.replace("ssid=jhw_wlan_", "ssid=good-B"))
        self.run_dir.mkdir(exist_ok=True)
        marker = self.run_dir / "mlan0.runtime-switch-in-progress"
        marker.write_text("marker=absent\ncheckpoint=present\n")
        marker.chmod(0o600)
        original = self.run_dir / "mlan0.runtime-original.conf"
        original.write_text(CONF_MODE_B)
        original.chmod(0o600)
        original_checkpoint = {
            "version": 1, "iface": "mlan0", "ssid": "jhw_wlan_",
            "freqs": "5180 5200 5220 5240", "profile": CONF_MODE_B, "marker": None,
        }
        checkpoint_snapshot = self.run_dir / "mlan0.runtime-original.last-good"
        checkpoint_snapshot.write_text(json.dumps(original_checkpoint))
        checkpoint_snapshot.chmod(0o600)
        target_checkpoint = dict(original_checkpoint, ssid="good-B", profile=runtime)
        live_checkpoint = self.run_dir / "mlan0.last-good.json"
        live_checkpoint.write_text(json.dumps(target_checkpoint))
        live_checkpoint.chmod(0o600)
        active = self.run_dir / "mlan0.runtime-psk.active"
        active.write_text("runtime-secret")
        active.chmod(0o600)
        result = self.run_connect(apply_stored_conf=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = (self.state / "calls.log").read_text()
        self.assertIn("wpa_cli -i mlan0 reconfigure", calls)
        self.assertNotIn("already associated", result.stdout)
        checkpoint = json.loads(live_checkpoint.read_text())
        self.assertEqual(checkpoint["ssid"], "jhw_wlan_")
        self.assertEqual(checkpoint["profile"], CONF_MODE_B)
        self.assertFalse((self.run_dir / "mlan0.runtime-switch-in-progress").exists())
        self.assertFalse(active.exists())

    def test_no_ssid_connect_recovers_marker_published_before_stage_claim(self):
        self.run_dir.mkdir(exist_ok=True)
        marker = self.run_dir / "mlan0.runtime-switch-in-progress"
        marker.write_text("marker=absent\ncheckpoint=present\n")
        marker.chmod(0o600)
        original = self.run_dir / "mlan0.runtime-original.conf"
        original.write_text(CONF_MODE_B)
        original.chmod(0o600)
        original_checkpoint = {
            "version": 1, "iface": "mlan0", "ssid": "jhw_wlan_",
            "freqs": "5180 5200 5220 5240", "profile": CONF_MODE_B,
            "marker": None,
        }
        checkpoint_snapshot = self.run_dir / "mlan0.runtime-original.last-good"
        checkpoint_snapshot.write_text(json.dumps(original_checkpoint))
        checkpoint_snapshot.chmod(0o600)
        live_checkpoint = self.run_dir / "mlan0.last-good.json"
        live_checkpoint.write_text(json.dumps(original_checkpoint))
        live_checkpoint.chmod(0o600)
        stage = self.run_dir / "mlan0.runtime-psk"
        stage.write_text("runtime-secret")
        stage.chmod(0o600)

        # Model a stale wrapper environment too: wifi's marker validation must
        # override the new-switch flag before enforcing explicit-SSID syntax.
        result = self.run_connect(
            apply_stored_conf=True, extra_env={"FTPCMD_RUNTIME_PSK": "1"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("runtime credential switch requires", result.stderr)
        self.assertEqual(self.conf.read_text(), CONF_MODE_B)
        self.assertEqual((self.state / "runtime.conf").read_text(), CONF_MODE_B)
        checkpoint = json.loads(live_checkpoint.read_text())
        self.assertEqual(checkpoint["ssid"], "jhw_wlan_")
        self.assertFalse(marker.exists())
        self.assertFalse(stage.exists())

    def test_explicit_ssid_stops_after_recovering_marker_before_stage_claim(self):
        self.run_dir.mkdir(exist_ok=True)
        marker = self.run_dir / "mlan0.runtime-switch-in-progress"
        marker.write_text("marker=absent\ncheckpoint=present\n")
        marker.chmod(0o600)
        original = self.run_dir / "mlan0.runtime-original.conf"
        original.write_text(CONF_MODE_B)
        original.chmod(0o600)
        original_checkpoint = {
            "version": 1, "iface": "mlan0", "ssid": "jhw_wlan_",
            "freqs": "5180 5200 5220 5240", "profile": CONF_MODE_B,
            "marker": None,
        }
        checkpoint_snapshot = self.run_dir / "mlan0.runtime-original.last-good"
        checkpoint_snapshot.write_text(json.dumps(original_checkpoint))
        checkpoint_snapshot.chmod(0o600)
        live_checkpoint = self.run_dir / "mlan0.last-good.json"
        live_checkpoint.write_text(json.dumps(original_checkpoint))
        live_checkpoint.chmod(0o600)
        stage = self.run_dir / "mlan0.runtime-psk"
        stage.write_text("runtime-secret")
        stage.chmod(0o600)

        result = self.run_connect("good-B", apply_stored_conf=True)
        self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
        self.assertIn("stage the PSK again", result.stderr)
        self.assertEqual(self.conf.read_text(), CONF_MODE_B)
        self.assertEqual((self.state / "runtime.conf").read_text(), CONF_MODE_B)
        checkpoint = json.loads(live_checkpoint.read_text())
        self.assertEqual(checkpoint["ssid"], "jhw_wlan_")
        self.assertEqual(checkpoint["profile"], CONF_MODE_B)
        calls = (self.state / "calls.log").read_text()
        self.assertNotIn('set_network 0 ssid "good-B"', calls)
        self.assertFalse(marker.exists())
        self.assertFalse(stage.exists())

    def test_plain_no_ssid_connect_uses_the_network_id_assigned_by_stale_recovery(self):
        stub = self.stub / "wpa_cli"
        text = stub.read_text()
        old = "printf '0 %s 5220\\n' \"$ssid\" > \"$D/landing\""
        new = "printf '1 %s 5220\\n' \"$ssid\" > \"$D/landing\""
        self.assertEqual(text.count(old), 1)
        stub.write_text(text.replace(old, new, 1))

        marker = self.run_dir / "mlan0.runtime-switch-in-progress"
        marker.write_text("marker=absent\ncheckpoint=absent\n")
        marker.chmod(0o600)
        original = self.run_dir / "mlan0.runtime-original.conf"
        original.write_text(CONF_MODE_B)
        original.chmod(0o600)
        active = self.run_dir / "mlan0.runtime-psk.active"
        active.write_text("runtime-secret")
        active.chmod(0o600)

        result = self.run_connect()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        status = (self.state / "status").read_text()
        self.assertIn("id=1\n", status)
        calls = (self.state / "calls.log").read_text()
        self.assertIn("wpa_cli -i mlan0 reconfigure", calls)
        self.assertIn("wpa_cli -i mlan0 reassociate", calls)
        self.assertFalse(marker.exists())
        self.assertFalse(active.exists())

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

    def test_no_save_success_changes_only_the_live_profile(self):
        self.marker.write_text("essid=opc-owned\nslot=4\n")
        raw_commit = self.run_dir / "mlan0.raw-commit"
        raw_commit.write_text("obsolete request\n")
        before = self.persistent_state()

        result = self.run_connect("--no-save", "good-B")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.persistent_state(), before)
        self.assertIn('ssid="good-B"', (self.state / "runtime.conf").read_text())
        self.assertFalse(raw_commit.exists())

    def test_no_save_timeout_rolls_back_live_profile_without_rewriting_persistence(self):
        self.marker.write_text("essid=opc-owned\nslot=5\n")
        before = self.persistent_state()

        result = self.run_connect("--no-save", "unreachable")

        self.assertEqual(result.returncode, 8, result.stdout + result.stderr)
        self.assertEqual(self.persistent_state(), before)
        self.assertEqual((self.state / "runtime.conf").read_text(), CONF_MODE_B)
        self.assertIn("ssid=jhw_wlan_\n", (self.state / "status").read_text())

    def _run_no_save_with_slow_snapshot(self, target):
        self.set_policy(attempt=1, rollback=1)
        self.marker.write_text("essid=opc-owned\nslot=5\n")
        (self.state / "delay-first-ssid-query").write_text("8\n")
        before = self.persistent_state()

        result = self.run_connect("--no-save", target)

        self.assertEqual(self.persistent_state(), before)
        self.assertFalse((self.state / "delay-first-ssid-query").exists())
        self.assertEqual(list(self.run_dir.glob("mlan0.connect-monitor.*")), [])
        return result

    def test_no_save_slow_snapshot_keeps_monitor_through_success(self):
        result = self._run_no_save_with_slow_snapshot("good-B")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('ssid="good-B"', (self.state / "runtime.conf").read_text())
        status = (self.state / "status").read_text()
        self.assertIn("wpa_state=COMPLETED\n", status)
        self.assertIn("ssid=good-B\n", status)

    def test_no_save_slow_snapshot_keeps_monitor_through_rollback(self):
        result = self._run_no_save_with_slow_snapshot("unreachable")

        self.assertEqual(result.returncode, 8, result.stdout + result.stderr)
        self.assertEqual((self.state / "runtime.conf").read_text(), CONF_MODE_B)
        status = (self.state / "status").read_text()
        self.assertIn("wpa_state=COMPLETED\n", status)
        self.assertIn("ssid=jhw_wlan_\n", status)
        self.assertNotIn("runtime-only rollback could not verify", result.stderr)

    def test_no_save_staged_psk_success_does_not_commit_credentials(self):
        self.marker.write_text("essid=opc-owned\nslot=6\n")
        stage = self.stage_runtime_psk()
        before = self.persistent_state()

        result = self.run_connect("--no-save", "good-B")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.persistent_state(), before)
        runtime = (self.state / "runtime.conf").read_text()
        self.assertIn('ssid="good-B"', runtime)
        self.assertIn('psk="runtime-secret"', runtime)
        self.assertFalse(stage.exists())

    def test_no_save_staged_psk_activation_failure_allows_next_connect(self):
        self.marker.write_text("essid=opc-owned\nslot=6\n")
        self.stage_runtime_psk()
        before = self.persistent_state()
        mv = self.stub / "mv"
        mv.write_text('''#!/bin/sh
case "$*" in
    *runtime-psk*runtime-psk.active*)
        /bin/mv "$@"
        exit 1 ;;
esac
exec /bin/mv "$@"
''')
        mv.chmod(0o755)

        result = self.run_connect("--no-save", "good-B")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.persistent_state(), before)
        self.assertEqual((self.state / "runtime.conf").read_text(), CONF_MODE_B)
        self.assertFalse((self.run_dir / "mlan0.runtime-psk.active").exists())
        calls = (self.state / "calls.log").read_text()
        for command in (" set_network", " disconnect", " reassociate", " reconfigure"):
            self.assertNotIn(command, calls)
        retry = self.run_connect()
        self.assertEqual(retry.returncode, 0, retry.stdout + retry.stderr)
        runtime_retry = self.run_connect("--no-save", "good-B")
        self.assertEqual(runtime_retry.returncode, 0, runtime_retry.stdout + runtime_retry.stderr)

    def assert_no_save_staged_psk_activation_signal(self, signal_name, expected_rc):
        self.marker.write_text("essid=opc-owned\nslot=6\n")
        self.stage_runtime_psk()
        before = self.persistent_state()
        mv = self.stub / "mv"
        mv.write_text('''#!/bin/sh
case "$*" in
    *runtime-psk*runtime-psk.active*)
        /bin/mv "$@" || exit 1
        kill -SIGNAL "$PPID"
        exit 0 ;;
esac
exec /bin/mv "$@"
'''.replace("SIGNAL", signal_name))
        mv.chmod(0o755)

        result = self.run_connect("--no-save", "good-B")

        self.assertEqual(result.returncode, expected_rc, result.stdout + result.stderr)
        self.assertEqual(self.persistent_state(), before)
        self.assertEqual((self.state / "runtime.conf").read_text(), CONF_MODE_B)
        self.assertFalse((self.run_dir / "mlan0.runtime-psk.active").exists())
        retry = self.run_connect()
        self.assertEqual(retry.returncode, 0, retry.stdout + retry.stderr)
        runtime_retry = self.run_connect("--no-save", "good-B")
        self.assertEqual(runtime_retry.returncode, 0, runtime_retry.stdout + runtime_retry.stderr)

    def test_no_save_staged_psk_activation_hup_allows_next_connect(self):
        self.assert_no_save_staged_psk_activation_signal("HUP", 129)

    def test_no_save_staged_psk_activation_int_allows_next_connect(self):
        self.assert_no_save_staged_psk_activation_signal("INT", 130)

    def test_no_save_staged_psk_activation_term_allows_next_connect(self):
        self.assert_no_save_staged_psk_activation_signal("TERM", 143)

    def test_no_save_unsafe_staged_psk_preserves_pending_raw_commit(self):
        raw_commit = self.run_dir / "mlan0.raw-commit"
        raw_bytes = b"prior-raw-request\nsecond-line\n"
        raw_commit.write_bytes(raw_bytes)
        stage = self.stage_runtime_psk()
        stage.chmod(0o644)
        runtime_before = (self.state / "runtime.conf").read_bytes()
        status_before = (self.state / "status").read_bytes()

        result = self.run_connect("--no-save", "good-B")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(raw_commit.read_bytes(), raw_bytes)
        self.assertEqual((self.state / "runtime.conf").read_bytes(), runtime_before)
        self.assertEqual((self.state / "status").read_bytes(), status_before)
        calls = (self.state / "calls.log").read_text()
        for command in (" set_network", " disconnect", " reassociate", " reconfigure"):
            self.assertNotIn(command, calls)

    def test_no_save_raw_commit_cancel_failure_does_not_consume_staged_psk(self):
        raw_commit = self.run_dir / "mlan0.raw-commit"
        raw_commit.write_text("prior-raw-request\n")
        stage = self.stage_runtime_psk()
        rm = self.stub / "rm"
        rm.write_text('''#!/bin/sh
for arg in "$@"; do
    case "$arg" in *.raw-commit) exit 1 ;; esac
done
exec /bin/rm "$@"
''')
        rm.chmod(0o755)
        runtime_before = (self.state / "runtime.conf").read_bytes()

        result = self.run_connect("--no-save", "good-B")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(stage.read_text(), "runtime-secret")
        self.assertEqual((self.state / "runtime.conf").read_bytes(), runtime_before)
        self.assertTrue(raw_commit.exists())
        calls = (self.state / "calls.log").read_text()
        for command in (" set_network", " disconnect", " reassociate", " reconfigure"):
            self.assertNotIn(command, calls)

    def test_no_save_staged_psk_timeout_restores_live_credentials_without_file_writes(self):
        self.marker.write_text("essid=opc-owned\nslot=7\n")
        self.stage_runtime_psk()
        before = self.persistent_state()

        result = self.run_connect("--no-save", "unreachable")

        self.assertEqual(result.returncode, 8, result.stdout + result.stderr)
        self.assertEqual(self.persistent_state(), before)
        self.assertEqual((self.state / "runtime.conf").read_text(), CONF_MODE_B)

    def test_no_save_without_ssid_reassociates_runtime_without_reconfigure(self):
        raw_commit = self.run_dir / "mlan0.raw-commit"
        raw_commit.write_text("obsolete request\n")
        before = self.persistent_state()

        result = self.run_connect("--no-save", apply_stored_conf=True)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.persistent_state(), before)
        calls = (self.state / "calls.log").read_text()
        self.assertIn("wpa_cli -i mlan0 reassociate", calls)
        self.assertNotIn("wpa_cli -i mlan0 reconfigure", calls)
        self.assertFalse(raw_commit.exists())

    def test_no_save_without_ssid_monitor_failure_preserves_pending_raw_commit(self):
        raw_commit = self.run_dir / "mlan0.raw-commit"
        raw_bytes = b"prior-raw-request\nsecond-line\n"
        raw_commit.write_bytes(raw_bytes)
        stub = self.stub / "wpa_cli"
        text = stub.read_text()
        needle = 'if [ -n "$action" ]; then\n'
        self.assertEqual(text.count(needle), 1)
        stub.write_text(text.replace(needle, needle + "    exit 1\n", 1))

        result = self.run_connect("--no-save")

        self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
        self.assertEqual(raw_commit.read_bytes(), raw_bytes)
        calls = (self.state / "calls.log").read_text()
        self.assertNotIn("wpa_cli -i mlan0 reassociate", calls)

    def test_no_save_without_ssid_timeout_uses_attempt_budget(self):
        (self.state / "available").write_text("")
        (self.state / "status").write_text("wpa_state=DISCONNECTED\nid=0\n")
        before = self.persistent_state()
        started = time.monotonic()

        result = self.run_connect("--no-save", extra_env={"ASSOC_TIMEOUT_DEFAULT": "4"})

        elapsed = time.monotonic() - started
        self.assertEqual(result.returncode, 8, result.stdout + result.stderr)
        self.assertIn("not completed within 1s", result.stderr)
        self.assertLess(elapsed, 3, f"1s attempt took {elapsed:.3f}s")
        self.assertEqual(self.persistent_state(), before)
        self.assertEqual((self.state / "runtime.conf").read_text(), CONF_MODE_B)
        self.assertEqual(list(self.run_dir.glob("mlan0.connect-monitor.*")), [])

    def test_no_save_without_ssid_slow_status_respects_attempt_deadline(self):
        (self.state / "available").write_text("")
        (self.state / "delay-reconnect-status").write_text("4\n")
        before = self.persistent_state()
        started = time.monotonic()

        result = self.run_connect("--no-save", extra_env={"ASSOC_TIMEOUT_DEFAULT": "4"})

        elapsed = time.monotonic() - started
        self.assertEqual(result.returncode, 8, result.stdout + result.stderr)
        self.assertTrue((self.state / "slow-reconnect-status-started").exists())
        self.assertFalse((self.state / "slow-reconnect-status-finished").exists())
        self.assertLess(elapsed, 3, f"slow status exceeded 1s attempt: {elapsed:.3f}s")
        self.assertEqual(self.persistent_state(), before)
        self.assertEqual((self.state / "runtime.conf").read_text(), CONF_MODE_B)
        self.assertEqual(list(self.run_dir.glob("mlan0.connect-monitor.*")), [])

    def test_no_save_without_ssid_uses_full_configured_poll_budget(self):
        self.set_policy(attempt=3)
        # The event must arrive after reassociate returns, not keep the
        # command's stdout open until the delayed association is available.
        stub = self.stub / "wpa_cli"
        text = stub.read_text()
        delayed = '( sleep "$delay"; echo "$lid" > "$D/fire.tmp" && mv -f "$D/fire.tmp" "$D/fire" ) &'
        self.assertEqual(text.count(delayed), 1)
        stub.write_text(text.replace(delayed, delayed[:-1] + '>/dev/null 2>&1 &', 1))
        (self.state / "fire-delay").write_text("1.5\n")
        before = self.persistent_state()

        result = self.run_connect("--no-save", extra_env={"ASSOC_TIMEOUT_DEFAULT": "1"})

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("wpa_state=COMPLETED", result.stdout)
        self.assertEqual(self.persistent_state(), before)
        self.assertEqual((self.state / "runtime.conf").read_text(), CONF_MODE_B)

    def test_no_save_without_ssid_rejects_proof_returned_after_deadline(self):
        # A second slow status can complete beyond the deadline because the
        # BusyBox-compatible command timeout rounds up to whole seconds.
        (self.state / "delay-reconnect-status").write_text("0.7\n")
        stub = self.stub / "wpa_cli"
        text = stub.read_text()
        event = '            WPA_ID="$id" "$action" "$iface" CONNECTED\n'
        self.assertEqual(text.count(event), 1)
        wait_for_poll = '            while [ ! -f "$D/slow-reconnect-status-started" ]; do sleep 0.01; done\n'
        stub.write_text(text.replace(event, wait_for_poll + event, 1))
        before = self.persistent_state()

        result = self.run_connect("--no-save")

        self.assertEqual(result.returncode, 8, result.stdout + result.stderr)
        self.assertIn("not completed within 1s", result.stderr)
        self.assertEqual(self.persistent_state(), before)
        self.assertEqual((self.state / "runtime.conf").read_text(), CONF_MODE_B)

    def test_no_save_multi_network_reconnect_obeys_attempt_timeout(self):
        self.write_conf(_cli.CONF_MODE_A)
        (self.state / "runtime.conf").write_text(_cli.CONF_MODE_A)
        (self.state / "available").write_text("")
        (self.state / "status").write_text("wpa_state=DISCONNECTED\nid=0\n")
        before = self.persistent_state()
        started = time.monotonic()

        result = self.run_connect("--no-save", extra_env={"ASSOC_TIMEOUT_DEFAULT": "4"})

        elapsed = time.monotonic() - started
        self.assertEqual(result.returncode, 8, result.stdout + result.stderr)
        self.assertIn("multi-network topology", result.stdout)
        self.assertLess(elapsed, 3, f"1s multi-network attempt took {elapsed:.3f}s")
        self.assertEqual(self.persistent_state(), before)
        self.assertEqual((self.state / "runtime.conf").read_text(), _cli.CONF_MODE_A)

    def test_no_save_rejects_pending_or_stale_transactions_without_repair(self):
        for state_name in ("mlan0.credential-pending", "mlan0.runtime-switch-in-progress"):
            with self.subTest(state_name=state_name):
                state = self.run_dir / state_name
                state.write_text("marker=absent\ncheckpoint=absent\n")
                state.chmod(0o600)
                before = self.persistent_state()
                runtime_before = (self.state / "runtime.conf").read_bytes()

                result = self.run_connect("--no-save", "good-B")

                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(self.persistent_state(), before)
                self.assertEqual((self.state / "runtime.conf").read_bytes(), runtime_before)
                self.assertTrue(state.exists())
                state.unlink()

    def test_no_save_frequency_policy_is_runtime_only(self):
        stub = self.stub / "wpa_cli"
        text = stub.read_text()
        self.assertEqual(text.count("printf '0 %s 5220\\n'"), 1)
        stub.write_text(text.replace("printf '0 %s 5220\\n'", "printf '0 %s 5200\\n'", 1))
        before = self.persistent_state()

        result = self.run_connect("--no-save", "good-B", "36", "40")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.persistent_state(), before)
        runtime = (self.state / "runtime.conf").read_text()
        self.assertEqual(runtime.count("freq_list=5180 5200\n"), 2)

    def test_explicit_save_keeps_the_existing_persistent_connect_contract(self):
        result = self.run_connect("--save", "good-B")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('ssid="good-B"', self.conf.read_text())

    def test_option_delimiter_allows_a_literal_save_named_ssid(self):
        (self.state / "available").write_text("jhw_wlan_\n--save\n")

        result = self.run_connect("--", "--save")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('ssid="--save"', self.conf.read_text())

    def test_no_save_then_delimiter_allows_a_literal_save_named_ssid(self):
        (self.state / "available").write_text("jhw_wlan_\n--save\n")
        before = self.persistent_state()

        result = self.run_connect("--no-save", "--", "--save")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.persistent_state(), before)
        self.assertIn('ssid="--save"', (self.state / "runtime.conf").read_text())

    def test_duplicate_or_conflicting_save_policy_is_rejected(self):
        for args in (("--save", "--save", "good-B"),
                     ("--no-save", "--no-save", "good-B"),
                     ("--save", "--no-save", "good-B"),
                     ("--no-save", "--save", "good-B")):
            with self.subTest(args=args):
                before = self.persistent_state()
                result = self.run_connect(*args)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertEqual(self.persistent_state(), before)
                self.assertEqual((self.state / "runtime.conf").read_text(), CONF_MODE_B)

    def test_no_save_selects_the_only_enabled_network_while_disconnected(self):
        (self.state / "status").write_text("wpa_state=DISCONNECTED\n")
        before = self.persistent_state()

        result = self.run_connect("--no-save", "good-B")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.persistent_state(), before)
        self.assertIn('ssid="good-B"', (self.state / "runtime.conf").read_text())

    def test_no_save_prefers_one_current_network_while_disconnected(self):
        (self.state / "status").write_text("wpa_state=DISCONNECTED\n")
        (self.state / "network-list").write_text(
            "network id / ssid / bssid / flags\n"
            "0\tjhw_wlan_\tany\t[CURRENT]\n"
            "1\tother\tany\t\n")
        before = self.persistent_state()

        result = self.run_connect("--no-save", "good-B")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.persistent_state(), before)
        self.assertIn('ssid="good-B"', (self.state / "runtime.conf").read_text())

    def test_no_save_rejects_ambiguous_enabled_networks_while_disconnected(self):
        (self.state / "status").write_text("wpa_state=DISCONNECTED\n")
        (self.state / "network-list").write_text(
            "network id / ssid / bssid / flags\n"
            "0\tjhw_wlan_\tany\t\n"
            "1\tother\tany\t\n")
        before = self.persistent_state()

        result = self.run_connect("--no-save", "good-B")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.persistent_state(), before)
        self.assertEqual((self.state / "runtime.conf").read_text(), CONF_MODE_B)
        self.assertIn("cannot select one live network", result.stderr)

    def test_no_save_disconnected_timeout_restores_live_fields_without_source_reconnect(self):
        (self.state / "status").write_text("wpa_state=DISCONNECTED\n")
        before = self.persistent_state()

        result = self.run_connect("--no-save", "unreachable")

        self.assertEqual(result.returncode, 8, result.stdout + result.stderr)
        self.assertEqual(self.persistent_state(), before)
        self.assertEqual((self.state / "runtime.conf").read_text(), CONF_MODE_B)
        self.assertEqual((self.state / "status").read_text(), "wpa_state=DISCONNECTED\n")

    def test_unknown_leading_connect_option_is_rejected_before_mutation(self):
        before = self.persistent_state()

        result = self.run_connect("--temporary", "good-B")

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(self.persistent_state(), before)
        self.assertEqual((self.state / "runtime.conf").read_text(), CONF_MODE_B)
        self.assertIn("unknown connect option", result.stderr)

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

    def test_psk_setter_does_not_log_the_passphrase(self):
        secret = "replacement-secret"
        result = self.run_wifi("psk", secret)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f'psk="{secret}"', self.conf.read_text())
        log = (self.state / "logger.log").read_text()
        self.assertIn("psk [redacted]", log)
        self.assertNotIn(secret, log)

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

    def miss_target_until_scanned(self, target="good-B"):
        # The last scan probed only the current SSID; a scan for the target sees it.
        (self.state / "bss").write_text(f"id=1\nage=5\nssid=jhw_wlan_\nid=2\nage=86\nssid={target}\n")
        (self.state / "iw-bss").write_text(f"id=1\nage=0\nssid=jhw_wlan_\nid=2\nage=0\nssid={target}\n")

    def iw_calls(self):
        log = self.state / "calls.log"
        return [line for line in (log.read_text().splitlines() if log.exists() else [])
                if line.startswith("iw ")]

    def stub_processes_left(self, *patterns):
        return [p for p in patterns
                if subprocess.run(["pgrep", "-f", p], capture_output=True).returncode == 0]

    def test_ftp_ssid_switch_scans_for_a_missed_target_and_reassociates_from_cache(self):
        # Scanning while still connected replaces RECONFIGURE's scan with the
        # link down (#399): the switch disconnects only after the scan ended.
        self.set_policy(enabled=False)
        self.miss_target_until_scanned()
        (self.state / "iw-delay").write_text("0.3\n")
        result = self.run_connect("good-B", apply_stored_conf=True, trace=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.iw_calls(), [
            "iw [mlan0] [scan] [freq] [5180] [5200] [5220] [5240] [ssid] [good-B]"])
        self.assert_cache_reassociate()
        calls = (self.state / "calls.log").read_text().splitlines()
        self.assertLess(calls.index("iw-exit"), calls.index("wpa_cli -i mlan0 disconnect"))
        self.assertIn("prescan=0", (self.state / "logger.log").read_text())
        # A scan left running by a killed connect must not keep its locks.
        fds = (self.state / "iw-fds").read_text()
        self.assertIn(str(self.state), fds)
        self.assertNotIn(".lock", fds)

    def test_ftp_ssid_switch_passes_the_target_ssid_as_one_scan_argument(self):
        # The stub's landing record splits on spaces, so only the commands are
        # checked here, not the association that follows.
        self.set_policy(enabled=False)
        (self.state / "available").write_text("jhw_wlan_\ngood B\n")
        self.miss_target_until_scanned("good B")
        self.run_connect("good B", apply_stored_conf=True)
        self.assertEqual(self.iw_calls(), [
            "iw [mlan0] [scan] [freq] [5180] [5200] [5220] [5240] [ssid] [good B]"])
        self.assertIn('wpa_cli -i mlan0 set_network 0 ssid "good B"', self.ftp_switch_commands())

    def test_ftp_ssid_switch_does_not_scan_when_the_last_scan_saw_the_target(self):
        self.set_policy(enabled=False)
        result = self.run_connect("good-B", apply_stored_conf=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.iw_calls(), [])
        self.assert_cache_reassociate()

    def test_busy_or_slow_scan_leaves_the_profile_reload(self):
        # EBUSY from another scanner, or a scan past its 1 s cap for 4
        # channels: reload as before, with no scan process left behind.
        for name, setup, rc in (("busy", ("iw-rc", "240\n"), "240"),
                                ("slow", ("iw-delay", "4.71\n"), "124")):
            with self.subTest(name):
                self.write_conf(CONF_MODE_B)
                (self.state / "runtime.conf").write_text(CONF_MODE_B)
                (self.state / "status").write_text(_cli.STATUS_INITIAL)
                for name_ in ("calls.log", "logger.log"):
                    (self.state / name_).write_text("")
                for name_ in ("iw-rc", "iw-delay", "iw-bss"):
                    (self.state / name_).unlink(missing_ok=True)
                self.set_policy(enabled=False)
                (self.state / "bss").write_text("id=1\nage=5\nssid=jhw_wlan_\nid=2\nage=86\nssid=good-B\n")
                (self.state / setup[0]).write_text(setup[1])
                started = time.monotonic()
                result = self.run_connect("good-B", apply_stored_conf=True, trace=True)
                self.assert_profile_reload(result)
                self.assertLess(time.monotonic() - started, 4)
                self.assertEqual(len(self.iw_calls()), 1)
                self.assertIn(f"prescan={rc}", (self.state / "logger.log").read_text())
                self.assertEqual(self.stub_processes_left(f"{self.stub}/iw", "sleep 4.71"), [])

    def test_signal_during_the_scan_stops_it_before_any_link_change(self):
        self.set_policy(enabled=False)
        self.miss_target_until_scanned()
        (self.state / "iw-delay").write_text("4.72\n")
        env = dict(self.environment(), FTPCMD_APPLY_CONF="1")
        process = subprocess.Popen(["bash", str(WIFI_SH), "mlan0", "connect", "good-B"],
                                   env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True)
        try:
            deadline = time.monotonic() + 5
            while not self.iw_calls() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertEqual(len(self.iw_calls()), 1)
            process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=8)
            self.assertEqual(process.returncode, 143, stdout + stderr)
            self.assertEqual(self.stub_processes_left(f"{self.stub}/iw", "sleep 4.72"), [])
            self.assertEqual(self.ftp_switch_commands(), [])
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=3)

    def test_scan_is_started_only_for_ftp_switches_that_can_reassociate(self):
        cases = (
            ("ordinary connect", False, True, ("good-B",)),
            ("fallback enabled", True, False, ("good-B",)),
            ("staged credentials", True, True, ("good-B",)),
            ("frequency argument", True, True, ("good-B", "5220")),
        )
        for name, ftp, disabled, args in cases:
            with self.subTest(name):
                self.write_conf(CONF_MODE_B)
                (self.state / "runtime.conf").write_text(CONF_MODE_B)
                (self.state / "status").write_text(_cli.STATUS_INITIAL)
                (self.state / "calls.log").write_text("")
                self.set_policy(enabled=not disabled)
                self.miss_target_until_scanned()
                self.run_dir.mkdir(exist_ok=True)
                pending = self.run_dir / "mlan0.credential-pending"
                if name == "staged credentials":
                    pending.touch()
                else:
                    pending.unlink(missing_ok=True)
                result = self.run_connect(*args, apply_stored_conf=ftp)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(self.iw_calls(), [])

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
        ip.write_text("#!/bin/sh\nprintf '7: eth0 inet 192.0.2.15/24 scope global eth0\\n'\n")
        ip.chmod(0o755)
        handlers = SCRIPTS_DIR.parents[2] / "opt/ftpcmd/bin"
        raw_scripts = self.root / "raw-scripts"
        raw_scripts.mkdir()
        raw_wifi = raw_scripts / "wifi.sh"
        raw_wifi.write_text(WIFI_SH.read_text())
        raw_wifi.chmod(0o755)
        raw_helper = raw_scripts / "wifi_connect_raw.sh"
        raw_helper.write_text((SCRIPTS_DIR / raw_helper.name).read_text().replace(
            "WPA_CLI=/usr/sbin/wpa_cli", "WPA_CLI=" + shlex.quote(str(self.stub / "wpa_cli"))))
        raw_helper.chmod(0o755)
        raw = self.root / "wconnectraw"
        raw.write_text((handlers / "wconnectraw").read_text().replace(
            "WIFI=/usr/local/bin/wifi", "WIFI=" + shlex.quote(str(raw_wifi))))
        process = self.start_connect()
        try:
            self.wait_for_target(process)
            for handler, args in ((raw, ["0", "another-net"]),
                                  (handlers / "ifcup", ["mlan0"]),
                                  (handlers / "ifcdown", ["mlan0"])):
                with self.subTest(handler=handler.name):
                    result = subprocess.run(["sh", str(handler), *args],
                                            env=dict(self.environment(), FTPCMD_LOCAL_ADDR="192.0.2.15"),
                                            capture_output=True,
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
