"""Execute the OPC radio helper with only the external device tools replaced."""

import fcntl
import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
HELPER = SCRIPTS / "opc_wlan_apply.sh"
PROFILE = '''ctrl_interface=/var/run/wpa_supplicant
update_config=0
freq_list=2412
network={
    ssid="radio-test"
    key_mgmt=WPA-PSK
    psk="test-fixture-only"
    freq_list=2412
}
'''
INITIAL_DRIVER = {
    "band": "0x35f", "ht_bg": "0x05c20000", "ht_a": "0x05c20000",
    "vht_bw": "1", "vht_cap": "0x339b79f2",
}

# These fakes model the hardware/process boundary. The real helper does all
# validation, locks, config rendering, installation, and rollback.
TOOL = r'''#!/usr/bin/python3
import json, os, signal, sys, time
from pathlib import Path

root = Path(os.environ["OPC_TEST_STATE"])
tool = Path(sys.argv[0]).name
args = sys.argv[1:]
with (root / "calls.jsonl").open("a") as log:
    log.write(json.dumps([tool, *args]) + "\n")
failure = os.environ.get("OPC_TEST_FAILURE", "")
marker = root / "failure-fired"

def fail_once(condition):
    if condition and not marker.exists():
        marker.touch()
        return True
    return False

if tool == "sync":
    sys.exit(0)
if tool == "mv":
    destination = args[-1]
    if fail_once(failure == "json-install" and destination == os.environ["WIFI_INIT_CONF_JSON"]):
        sys.exit(1)
    os.replace(args[-2], destination)
    if fail_once(failure == "term-json-installed" and destination == os.environ["WIFI_INIT_CONF_JSON"]):
        os.kill(os.getppid(), signal.SIGTERM)
        time.sleep(0.01)
    sys.exit(0)
if tool == "rm":
    if fail_once(failure == "term-after-commit" and any("wpa_supplicant-mlan0.conf.bak." in arg for arg in args)):
        os.kill(os.getppid(), signal.SIGTERM)
        time.sleep(0.01)
    for arg in args:
        if not arg.startswith("-"):
            Path(arg).unlink(missing_ok=True)
    sys.exit(0)
if tool == "wpa_cli":
    command = args[2]
    if command == "ping":
        print("PONG")
    elif command == "abort_scan":
        print("FAIL")
    elif fail_once(failure == "reconfigure" and command == "reconfigure"):
        print("FAIL")
    elif command == "status":
        print("wpa_state=SCANNING")
    else:
        print("OK")
    sys.exit(0)

iface, command = args[:2]
file = root / (iface + ".json")
state = json.loads(file.read_text())
if command == "bandcfg":
    if len(args) == 2:
        print("Band Configuration:\n  Infra Band: " + state["band"])
    else:
        if fail_once(failure == "bandcfg"):
            sys.exit(1)
        state["band"] = args[2].lower()
elif command == "htcapinfo":
    if len(args) == 2:
        print("HT cap info:\n  BG band: " + state["ht_bg"] + "\n   A band: " + state["ht_a"])
    else:
        if fail_once(failure == "htcapinfo"):
            sys.exit(1)
        bands = ["ht_bg", "ht_a"] if len(args) == 3 else ["ht_bg" if args[3] == "1" else "ht_a"]
        for band in bands:
            state[band] = args[2].lower()
elif command == "vhtcfg":
    if len(args) == 4:
        if failure == "vht-get-empty":
            sys.exit(0)
        source = "Follow BW in VHT Capabilities" if state["vht_bw"] == "1" else "Follow BW in the 11N"
        print("11AC VHT Configuration:\nBand: 5G\n  BW config: " + source + "\n  VHT Capabilities Info: " + state["vht_cap"])
    else:
        if fail_once(failure == "vhtcfg"):
            sys.exit(1)
        state["vht_bw"], state["vht_cap"] = args[4:6]
else:
    sys.exit(2)
file.write_text(json.dumps(state))
'''


@unittest.skipUnless(shutil.which("jq") and shutil.which("flock"), "jq/flock required")
class OpcRadioApplyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="opc-radio-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bin = self.root / "bin"
        self.state = self.root / "state"
        self.wpa = self.root / "wpa"
        for directory in (self.bin, self.state, self.wpa):
            directory.mkdir()
        for name in ("wpa_cli", "mlanutl", "sync", "mv", "rm"):
            path = self.bin / name
            path.write_text(TOOL)
            path.chmod(0o755)
        for iface in ("mlan0", "mlan1"):
            (self.state / (iface + ".json")).write_text(json.dumps(INITIAL_DRIVER))
            profile = self.wpa / ("wpa_supplicant-" + iface + ".conf")
            profile.write_text(PROFILE)
            profile.chmod(0o640)
        self.config = self.root / "wifi.json"
        self.initial_config = {
            "mlan0": {"radio": {"mode": "ax", "bw": "80"}, "keep": "first"},
            "mlan1": {"radio": {"mode": "ac", "bw": "20"}, "keep": "second"},
            "global": {"keep": "global"},
        }
        self.config.write_text(json.dumps(self.initial_config, indent=2) + "\n")
        self.config.chmod(0o640)
        # #362: the helper installs an mlan0 ssid only while opcd's ChangeIp ESSID restore
        # marker exists (opcd arms it before calling the helper); model that armed state.
        self.marker = self.root / "changeip_essid.restore"
        self.marker.write_text("slot=1\n")
        self.env = dict(os.environ, PATH=str(self.bin) + ":" + os.environ["PATH"],
                        OPC_TEST_STATE=str(self.state), WPA_CONF_DIR=str(self.wpa),
                        WIFI_RUN_DIR=str(self.root / "run"),
                        WIFI_INIT_CONF_JSON=str(self.config),
                        WIFI_SCAN_TRANSITION_LOCK_TIMEOUT="0",
                        OPC_ESSID_MARKER=str(self.marker))

    def run_helper(self, *args, failure=""):
        return subprocess.run(["sh", str(HELPER), *args],
                              env=dict(self.env, OPC_TEST_FAILURE=failure),
                              text=True, capture_output=True, timeout=6)

    def calls(self):
        path = self.state / "calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def driver(self, iface="mlan0"):
        return json.loads((self.state / (iface + ".json")).read_text())

    def profile(self, iface="mlan0"):
        return self.wpa / ("wpa_supplicant-" + iface + ".conf")

    def test_mlan0_ssid_without_restore_marker_is_refused_before_any_mutation(self):
        """#362 (tribunal A-R1-002): if another writer removed the ChangeIp ESSID restore
        marker between opcd's arm and this install, that writer's SSID is final — the helper
        must leave the conf untouched and exit 6 so opcd's settle does not commit."""
        self.marker.unlink()
        result = self.run_helper("mlan0", "ssid", "late-changeip")
        self.assertEqual(result.returncode, 6, result.stdout + result.stderr)
        self.assertIn("restore marker is missing", result.stderr)
        self.assertEqual(self.profile().read_text(), PROFILE)
        self.assertEqual(self.calls(), [])

    def test_mlan1_ssid_does_not_require_the_restore_marker(self):
        self.marker.unlink()
        result = self.run_helper("mlan1", "ssid", "second-net")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('ssid="second-net"', self.profile("mlan1").read_text())

    def test_request_applies_driver_and_persists_settings_for_boot(self):
        result = self.run_helper("mlan0", "freq", "5180 5200", "mode", "ac", "bw", "40")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.driver(), dict(INITIAL_DRIVER, band="0x5f", vht_bw="0"))
        config = json.loads(self.config.read_text())
        self.assertEqual(config["mlan0"], {"radio": {"mode": "ac", "bw": "40"}, "keep": "first"})
        self.assertEqual(config["mlan1"], self.initial_config["mlan1"])
        self.assertEqual(config["global"], self.initial_config["global"])
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o640)
        self.assertEqual(stat.S_IMODE(self.profile().stat().st_mode), 0o640)
        self.assertIn("freq_list=5180 5200", self.profile().read_text())
        self.assertIn('ssid="radio-test"', self.profile().read_text())
        self.assertEqual(self.driver("mlan1"), INITIAL_DRIVER)
        self.assertNotIn(["wpa_cli", "-i", "mlan0", "status"], self.calls())

    def test_supported_mode_values_apply_independent_literal_masks(self):
        for mode, mask in (("b", "0x1"), ("g", "0x3"), ("a", "0x7"),
                           ("n", "0x1f"), ("ac", "0x5f"), ("ax", "0x35f")):
            with self.subTest(mode=mode):
                result = self.run_helper("mlan0", "freq", "2412", "mode", mode, "bw", "20")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.driver()["band"], mask)
                self.assertEqual(json.loads(self.config.read_text())["mlan0"]["radio"], {"mode": mode, "bw": "20"})

    def test_supported_widths_preserve_vht_capability_bits(self):
        for width, ht, vht in (("20", "0x05c00000", "0"),
                               ("40", "0x05c20000", "0"),
                               ("80", "0x05c20000", "1")):
            with self.subTest(width=width):
                result = self.run_helper("mlan0", "freq", "5180", "mode", "ax", "bw", width)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.driver(), dict(INITIAL_DRIVER, ht_bg=ht, ht_a=ht, vht_bw=vht))

    def test_second_interface_applies_only_its_own_settings(self):
        result = self.run_helper("mlan1", "freq", "5200", "mode", "n", "bw", "40")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.driver("mlan1"), dict(INITIAL_DRIVER, band="0x1f", vht_bw="0"))
        self.assertEqual(self.driver(), INITIAL_DRIVER)
        self.assertEqual(json.loads(self.config.read_text())["mlan0"], self.initial_config["mlan0"])

    def test_driver_and_config_restore_after_apply_or_persist_failure(self):
        for failure in ("htcapinfo", "vhtcfg", "reconfigure", "json-install"):
            with self.subTest(failure=failure):
                (self.state / "failure-fired").unlink(missing_ok=True)
                config_before = self.config.read_bytes()
                profile_before = self.profile().read_bytes()
                result = self.run_helper("mlan0", "freq", "5180", "mode", "ac", "bw", "20", failure=failure)
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue((self.state / "failure-fired").exists(), result.stderr)
                self.assertEqual(self.driver(), INITIAL_DRIVER)
                self.assertEqual(self.config.read_bytes(), config_before)
                self.assertEqual(self.profile().read_bytes(), profile_before)

    def test_invalid_radio_values_are_rejected_without_live_or_file_mutation(self):
        for iface, mode, width, freq in (("mlan1", "ax", "20", "5180"),
                                         ("mlan0", "bad", "20", "5180"),
                                         ("mlan0", "ac", "160", "5180"),
                                         ("mlan0", "ac", "80+80", "5180"),
                                         ("mlan0", "ac", "320", "5180"),
                                         ("mlan0", "b", "20", "5180"),
                                         ("mlan0", "g", "20", "5180")):
            with self.subTest(iface=iface, mode=mode, width=width):
                (self.state / "calls.jsonl").unlink(missing_ok=True)
                profile_before, config_before = self.profile(iface).read_bytes(), self.config.read_bytes()
                result = self.run_helper(iface, "freq", freq, "mode", mode, "bw", width)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(self.calls(), [])
                self.assertEqual(self.profile(iface).read_bytes(), profile_before)
                self.assertEqual(self.config.read_bytes(), config_before)

    def test_invalid_boot_json_is_rejected_before_driver_mutation(self):
        self.config.write_text("{invalid")
        result = self.run_helper("mlan0", "freq", "5180", "mode", "ac", "bw", "40")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.profile().read_text(), PROFILE)
        self.assertEqual(self.driver(), INITIAL_DRIVER)

    def test_missing_vht_readback_rejects_80_before_any_live_change(self):
        before = self.config.read_bytes()
        result = self.run_helper("mlan0", "freq", "5180", "mode", "ac", "bw", "80", failure="vht-get-empty")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(["mlanutl", "mlan0", "vhtcfg", "2", "2"], self.calls())
        self.assertEqual(self.driver(), INITIAL_DRIVER)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(self.profile().read_text(), PROFILE)
        self.assertNotIn(["wpa_cli", "-i", "mlan0", "disconnect"], self.calls())

    def test_missing_vht_readback_still_allows_ht20(self):
        result = self.run_helper("mlan0", "freq", "2412", "mode", "n", "bw", "20", failure="vht-get-empty")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.driver(), dict(INITIAL_DRIVER, band="0x1f", ht_bg="0x05c00000", ht_a="0x05c00000"))

    def test_shared_json_lock_rejects_another_radio_transaction_before_mutation(self):
        run_dir = Path(self.env["WIFI_RUN_DIR"])
        run_dir.mkdir()
        before = self.config.read_bytes()
        with (run_dir / "radio-json.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_helper("mlan1", "freq", "5180", "mode", "ac", "bw", "40")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.driver("mlan1"), INITIAL_DRIVER)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(self.calls(), [])

    def test_mode_only_revalidates_concurrent_frequency_change_under_conf_lock(self):
        run_dir = Path(self.env["WIFI_RUN_DIR"])
        run_dir.mkdir()
        flock_marker = self.state / "flock-entered"
        flock = self.bin / "flock"
        flock.write_text(
            "#!/bin/sh\n"
            ": > \"$OPC_TEST_STATE/flock-entered\"\n"
            f"exec {shutil.which('flock')} \"$@\"\n"
        )
        flock.chmod(0o755)
        lock_path = run_dir / "mlan0.wpa-conf.lock"
        with lock_path.open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            process = subprocess.Popen(
                ["sh", str(HELPER), "mlan0", "mode", "b"],
                env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            self.addCleanup(lambda: process.poll() is None and process.kill())
            deadline = time.monotonic() + 2
            while not flock_marker.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(flock_marker.exists(), "helper did not attempt the configuration lock")
            # The helper is now waiting for this exact configuration lock.
            # Change the protected profile while it waits, then let it acquire
            # the lock and validate the effective frequency list.
            self.assertIsNone(process.poll())
            concurrent_profile = PROFILE.replace("freq_list=2412", "freq_list=5180")
            self.profile().write_text(concurrent_profile)
            fcntl.flock(lock, fcntl.LOCK_UN)
            stdout, stderr = process.communicate(timeout=6)

        self.assertEqual(process.returncode, 2, stdout + stderr)
        self.assertIn("mode=b cannot use a 5GHz-only frequency list", stderr)
        self.assertEqual(self.driver(), INITIAL_DRIVER)
        self.assertEqual(json.loads(self.config.read_text()), self.initial_config)
        self.assertEqual(self.profile().read_text(), concurrent_profile)
        self.assertEqual(self.calls(), [])
        with lock_path.open("w") as released_lock:
            fcntl.flock(released_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_term_before_commit_restores_all_settings_and_releases_all_locks(self):
        before = self.config.read_bytes()
        result = self.run_helper("mlan0", "freq", "5180", "mode", "ac", "bw", "20", failure="term-json-installed")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.state / "failure-fired").exists())
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(self.profile().read_text(), PROFILE)
        self.assertEqual(self.driver(), INITIAL_DRIVER)
        for name in ("mlan0.wpa-conf.lock", "mlan0.scan-transition.lock", "radio-json.lock"):
            with (Path(self.env["WIFI_RUN_DIR"]) / name).open("w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_term_after_commit_keeps_all_new_settings_together(self):
        result = self.run_helper("mlan0", "freq", "5180", "mode", "ac", "bw", "20", failure="term-after-commit")
        self.assertTrue((self.state / "failure-fired").exists(), result.stderr)
        self.assertEqual(self.driver(), dict(INITIAL_DRIVER, band="0x5f", ht_bg="0x05c00000", ht_a="0x05c00000", vht_bw="0"))
        self.assertEqual(json.loads(self.config.read_text())["mlan0"]["radio"], {"mode": "ac", "bw": "20"})
        self.assertIn("freq_list=5180", self.profile().read_text())

    def test_wifi_radio_json_commit_cooperates_with_opc_transaction_lock(self):
        # Run the actual commit helper from wifi.sh without staging values in
        # its global /tmp CLI namespace or operating the host's interfaces.
        text = (SCRIPTS / "wifi.sh").read_text()
        def function(name):
            start = text.index(name + "() {")
            return text[start:text.index("\n}\n", start) + 3]
        harness = ". \"$1\"\n" + function("safe_tmp_for") + function("update_json_radio")
        harness += '\nupdate_json_radio mlan1 n 40\n'
        run_dir = Path(self.env["WIFI_RUN_DIR"])
        run_dir.mkdir()
        before = self.config.read_bytes()
        with (run_dir / "radio-json.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = subprocess.run(["bash", "-c", harness, "_", str(SCRIPTS / "wifi_init_config_lib.sh")],
                                    env=self.env, text=True, capture_output=True, timeout=3)
        self.assertNotEqual(result.returncode, 0, "wifi commit bypassed the OPC JSON lock")
        self.assertEqual(self.config.read_bytes(), before)

    def test_freq_and_ssid_only_calls_keep_existing_radio_state(self):
        result = self.run_helper("mlan0", "freq", "5180", "ssid", "other-test")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.driver(), INITIAL_DRIVER)
        self.assertEqual(json.loads(self.config.read_text()), self.initial_config)
        self.assertFalse(any(call[0] == "mlanutl" for call in self.calls()))


if __name__ == "__main__":
    unittest.main()
