"""Successful raw switches may persist only the confirmed single-network SSID."""

import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


WORKER = Path(__file__).resolve().parents[1] / "wifi_raw_commit.py"
UNITS = Path(__file__).resolve().parents[4] / "etc/systemd/system"
ORIGINAL = b'update_config=0\nfreq_list=5180 5200\nnetwork={\n    ssid="Old AP"\n    psk="password123"\n    freq_list=5180 5200\n}\n'
SPEC = importlib.util.spec_from_file_location("wifi_raw_commit", WORKER)
RAW_COMMIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RAW_COMMIT)


class RawCommit(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wifi-raw-commit-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run_dir = self.root / "run"
        self.run_dir.mkdir()
        self.policy = self.run_dir / "mlan0.roam-policy.json"
        self.policy.write_text(json.dumps({
            "version": 1, "iface": "mlan0", "roaming_enabled": False,
            "bgscan_enabled": False, "generate_network_blocks": False,
            "extra_ssids": [],
        }))
        self.latch = self.root / ".mlan0.roam-policy.latched"
        self.latch.write_text("1\n")
        self.latch.chmod(0o600)
        self.conf_dir = self.root / "etc"
        self.conf_dir.mkdir()
        self.conf = self.conf_dir / "wpa_supplicant-mlan0.conf"
        self.conf.write_bytes(ORIGINAL)
        self.conf.chmod(0o600)
        self.marker = self.root / "changeip_essid.restore"
        self.marker.write_text("old marker\n")
        self.status = self.root / "status"
        self.status_read = self.root / "status-read"
        self.set_status("Office AP B")
        self.wpa_cli = self.root / "wpa_cli"
        self.wpa_cli.write_text(
            '#!/bin/sh\n'
            'case "$3" in\n'
            '  status) if [ -n "$RAW_TEST_STATUS_SLEEP" ]; then sleep "$RAW_TEST_STATUS_SLEEP"; fi; '
            'if [ -n "$RAW_TEST_STATUS_RC" ]; then exit "$RAW_TEST_STATUS_RC"; fi; '
            'touch "$RAW_TEST_STATUS_READ"; cat "$RAW_TEST_STATUS" ;;\n'
            '  get_network) printf \'"%s"\\n\' "$RAW_TEST_NETWORK_SSID" ;;\n'
            '  *) exit 2 ;;\n'
            'esac\n'
        )
        self.wpa_cli.chmod(0o755)

    def set_status(self, ssid):
        self.status.write_text(
            f"bssid=02:11:22:33:44:55\nfreq=5180\nssid={ssid}\nid=7\nwpa_state=COMPLETED\n"
        )

    def run_worker(self, *, digest=None, ssid="Office AP B"):
        return self.run_command(
            "mlan0", "7", ssid, digest or hashlib.sha256(ORIGINAL).hexdigest(),
            "test-trace", "aa:bb:cc:dd:ee:ff", "1", target_ssid=ssid)

    def run_command(self, *args, target_ssid="Office AP B"):
        return subprocess.run(
            [sys.executable, str(WORKER), *args],
            capture_output=True, text=True, timeout=5,
            env=self.worker_env(target_ssid),
        )

    def worker_env(self, target_ssid="Office AP B", status_sleep="", status_rc=""):
        env = os.environ.copy()
        env.update({
            "WIFI_RUN_DIR": str(self.run_dir),
            "WPA_CONF_DIR": str(self.conf_dir),
            "OPC_ESSID_MARKER": str(self.marker),
            "WIFI_RAW_COMMIT_WPA_CLI": str(self.wpa_cli),
            "WIFI_RAW_COMMIT_TIMEOUT_SEC": "0.2",
            "WIFI_RAW_COMMIT_POLL_SEC": "0.02",
            "RAW_TEST_STATUS": str(self.status),
            "RAW_TEST_STATUS_READ": str(self.status_read),
            "RAW_TEST_STATUS_SLEEP": status_sleep,
            "RAW_TEST_STATUS_RC": status_rc,
            "RAW_TEST_NETWORK_SSID": target_ssid,
        })
        return env

    def write_request(self, *, ssid="Office AP B", digest=None, path=None):
        request = path or self.run_dir / "mlan0.raw-commit"
        request.write_text("\n".join((
            "wifi-raw-commit-v1", "7", ssid,
            digest or hashlib.sha256(ORIGINAL).hexdigest(), "test-trace",
            "aa:bb:cc:dd:ee:ff", "1", "")), encoding="ascii")
        request.chmod(0o600)
        return request

    def test_path_worker_consumes_request_after_confirmed_association(self):
        request = self.write_request()
        result = self.run_command("--from-request", "mlan0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(request.exists())
        self.assertEqual(self.conf.read_bytes(), ORIGINAL.replace(b'"Old AP"', b'"Office AP B"'))

    def test_path_worker_rejects_unsafe_request(self):
        request = self.write_request()
        request.chmod(0o644)
        result = self.run_command("--from-request", "mlan0")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.conf.read_bytes(), ORIGINAL)

    def test_path_worker_rejects_symlink_request(self):
        request = self.write_request()
        target = self.root / "request-target"
        request.rename(target)
        request.symlink_to(target)
        result = self.run_command("--from-request", "mlan0")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.conf.read_bytes(), ORIGINAL)

    def test_path_worker_reports_timeout_and_removes_request(self):
        request = self.write_request()
        self.set_status("Old AP")
        result = self.run_command("--from-request", "mlan0")
        self.assertEqual(result.returncode, 8, result.stderr)
        self.assertFalse(request.exists())
        self.assertEqual(self.conf.read_bytes(), ORIGINAL)

    def test_path_worker_cleans_request_after_missing_wpa_cli(self):
        request = self.write_request()
        env = self.worker_env()
        env["WIFI_RAW_COMMIT_WPA_CLI"] = str(self.root / "missing-wpa-cli")
        result = subprocess.run(
            [sys.executable, str(WORKER), "--from-request", "mlan0"],
            capture_output=True, text=True, timeout=5,
            env=env,
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(request.exists())
        self.assertEqual(self.conf.read_bytes(), ORIGINAL)

    def test_path_worker_times_out_slow_wpa_cli_without_stale_request(self):
        request = self.write_request()
        result = subprocess.run(
            [sys.executable, str(WORKER), "--from-request", "mlan0"],
            capture_output=True, text=True, timeout=5,
            env=self.worker_env(status_sleep="2"),
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(request.exists())

    def test_wpa_cli_timeout_is_reported_as_control_failure(self):
        with mock.patch.dict(os.environ, self.worker_env(status_sleep="2"), clear=True), \
             mock.patch.object(RAW_COMMIT.syslog, "syslog") as logged:
            result = RAW_COMMIT.commit(
                "mlan0", "7", "Office AP B", hashlib.sha256(ORIGINAL).hexdigest(),
                "test-trace", "aa:bb:cc:dd:ee:ff", True)
        self.assertEqual(result, 1)
        self.assertIn("reason=wpa_cli_timeout", logged.call_args.args[1])

    def test_wpa_cli_nonzero_is_reported_as_control_failure(self):
        with mock.patch.dict(os.environ, self.worker_env(status_rc="1"), clear=True), \
             mock.patch.object(RAW_COMMIT.syslog, "syslog") as logged:
            result = RAW_COMMIT.commit(
                "mlan0", "7", "Office AP B", hashlib.sha256(ORIGINAL).hexdigest(),
                "test-trace", "aa:bb:cc:dd:ee:ff", True)
        self.assertEqual(result, 1)
        self.assertIn("reason=wpa_cli_error", logged.call_args.args[1])

    def test_latest_request_supersedes_in_progress_wait(self):
        request = self.write_request(ssid="Absent AP")
        worker = subprocess.Popen(
            [sys.executable, str(WORKER), "--from-request", "mlan0"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=self.worker_env(),
        )
        self.addCleanup(lambda: worker.kill() if worker.poll() is None else None)
        deadline = time.monotonic() + 2
        while not self.status_read.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(self.status_read.exists(), "worker did not start polling")
        replacement = self.write_request(path=self.run_dir / "replacement")
        os.replace(replacement, request)
        _, stderr = worker.communicate(timeout=5)
        self.assertEqual(worker.returncode, 0, stderr)
        self.assertFalse(request.exists())
        self.assertEqual(self.conf.read_bytes(), ORIGINAL.replace(b'"Old AP"', b'"Office AP B"'))

    def test_systemd_path_starts_independent_worker(self):
        path_unit = (UNITS / "wifi_raw_commit@.path").read_text()
        service_unit = (UNITS / "wifi_raw_commit@.service").read_text()
        self.assertIn("PathExists=/run/wifi/%i.raw-commit", path_unit)
        self.assertIn("Unit=wifi_raw_commit@%i.service", path_unit)
        self.assertIn("ExecStart=/usr/local/scripts/wifi_raw_commit.py --from-request %i", service_unit)
        self.assertNotIn("ExecStartPre=", service_unit)
        self.assertIn("PartOf=wifi_init.service", service_unit)
        self.assertIn("StartLimitIntervalSec=30s", service_unit)
        self.assertIn("StartLimitBurst=30", service_unit)
        self.assertIn("TriggerLimitIntervalSec=30s", path_unit)
        self.assertIn("TriggerLimitBurst=30", path_unit)

    def test_path_does_not_hold_paths_target_until_wifi_init_finishes(self):
        # systemd gives .path units Before=paths.target unless defaults are off.
        # Holding paths.target also holds basic.target and networkd at cold boot.
        path_unit = (UNITS / "wifi_raw_commit@.path").read_text()
        directives = {}
        for line in path_unit.splitlines():
            if line.startswith(("#", "[")) or "=" not in line:
                continue
            key, value = line.split("=", 1)
            directives.setdefault(key, []).extend(value.split())

        before = set(directives.get("Before", []))
        if directives.get("DefaultDependencies", ["yes"]) != ["no"]:
            before.add("paths.target")
        self.assertNotIn("paths.target", before)
        self.assertIn("wifi_init.service", directives.get("After", []))
        self.assertIn("sysinit.target", directives.get("After", []))
        self.assertIn("sysinit.target", directives.get("Requires", []))
        self.assertIn("shutdown.target", directives.get("Before", []))
        self.assertIn("shutdown.target", directives.get("Conflicts", []))

    def test_confirmed_target_updates_only_ssid_and_clears_restore_marker(self):
        result = self.run_worker()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.conf.read_bytes(), ORIGINAL.replace(b'"Old AP"', b'"Office AP B"'))
        self.assertEqual(stat.S_IMODE(self.conf.stat().st_mode), 0o600)
        self.assertFalse(self.marker.exists())

    def test_old_association_does_not_persist_target(self):
        self.set_status("Old AP")
        result = self.run_worker()
        self.assertEqual(result.returncode, 8, result.stderr)
        self.assertEqual(self.conf.read_bytes(), ORIGINAL)
        self.assertTrue(self.marker.exists())

    def test_stale_old_bssid_with_relabelled_ssid_is_not_success(self):
        self.status.write_text(
            "bssid=aa:bb:cc:dd:ee:ff\nfreq=5200\nssid=Office AP B\nid=7\nwpa_state=COMPLETED\n"
        )
        result = self.run_worker()
        self.assertEqual(result.returncode, 8, result.stderr)
        self.assertEqual(self.conf.read_bytes(), ORIGINAL)

    def test_changed_profile_is_not_overwritten_after_connection(self):
        self.conf.write_bytes(ORIGINAL.replace(b'password123', b'newpassword'))
        result = self.run_worker()
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertIn(b'newpassword', self.conf.read_bytes())
        self.assertTrue(self.marker.exists())

    def test_pending_credential_is_not_persisted_with_new_profile_digest(self):
        updated = ORIGINAL.replace(b'password123', b'newpassword')
        self.conf.write_bytes(updated)
        (self.run_dir / "mlan0.credential-pending").touch()
        request = self.write_request(digest=hashlib.sha256(updated).hexdigest())
        result = self.run_command("--from-request", "mlan0")
        self.assertEqual(result.returncode, 6, result.stderr)
        self.assertFalse(request.exists())
        self.assertEqual(self.conf.read_bytes(), updated)
        self.assertTrue(self.marker.exists())

    def test_target_outside_stored_frequency_policy_is_not_persisted(self):
        profiles = {
            "global-and-network": ORIGINAL.replace(b'5180 5200', b'2412'),
            "network-only": ORIGINAL.replace(b'    freq_list=5180 5200',
                                             b'    freq_list=2412'),
            "legacy-scan": ORIGINAL.replace(b'freq_list=5180 5200\n', b'').replace(
                b'network={\n', b'network={\n    scan_freq=2412\n'),
        }
        for name, restricted in profiles.items():
            with self.subTest(name=name):
                self.conf.write_bytes(restricted)
                request = self.write_request(digest=hashlib.sha256(restricted).hexdigest())
                result = self.run_command("--from-request", "mlan0")
                self.assertEqual(result.returncode, 6, result.stderr)
                self.assertFalse(request.exists())
                self.assertEqual(self.conf.read_bytes(), restricted)
                self.assertTrue(self.marker.exists())

    def test_unrestricted_profile_can_persist_confirmed_target(self):
        unrestricted = ORIGINAL.replace(b'freq_list=5180 5200\n', b'')
        self.conf.write_bytes(unrestricted)
        result = self.run_worker(digest=hashlib.sha256(unrestricted).hexdigest())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.conf.read_bytes(), unrestricted.replace(
            b'"Old AP"', b'"Office AP B"'))

    def test_multi_network_profile_is_not_rewritten(self):
        multi = ORIGINAL + b'network={\n    ssid="Other AP"\n    psk="password123"\n}\n'
        self.conf.write_bytes(multi)
        result = self.run_worker(digest=hashlib.sha256(multi).hexdigest())
        self.assertEqual(result.returncode, 6, result.stderr)
        self.assertEqual(self.conf.read_bytes(), multi)
        self.assertTrue(self.marker.exists())

    def test_unsafe_restore_marker_blocks_persistence(self):
        victim = self.root / "victim"
        victim.write_text("keep\n")
        self.marker.unlink()
        self.marker.symlink_to(victim)
        result = self.run_worker()
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(self.conf.read_bytes(), ORIGINAL)
        self.assertEqual(victim.read_text(), "keep\n")

    def test_lost_boot_policy_refuses_to_guess_mode_b(self):
        self.policy.unlink()
        result = self.run_worker()
        self.assertEqual(result.returncode, 6, result.stderr)
        self.assertEqual(self.conf.read_bytes(), ORIGINAL)

    def test_invalid_boot_policy_refuses_to_guess_mode_b(self):
        self.policy.write_text('{"iface":"mlan1","generate_network_blocks":false}')
        result = self.run_worker()
        self.assertEqual(result.returncode, 6, result.stderr)
        self.assertEqual(self.conf.read_bytes(), ORIGINAL)


if __name__ == "__main__":
    unittest.main()
