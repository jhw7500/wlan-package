import os
import shlex
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
WIFI_SH = SCRIPTS_DIR / "wifi.sh"
FTPCMD_DIR = SCRIPTS_DIR.parents[2] / "opt" / "ftpcmd" / "bin"
WSCAN = FTPCMD_DIR / "wscan"
CONFIG_LIB = SCRIPTS_DIR / "wifi_init_config_lib.sh"


class WscanHarness(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="wscan-test-")
        self.root = Path(self.tempdir.name)
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        self.run_dir = self.root / "run"
        self.conf_dir = self.root / "conf"
        self.conf_dir.mkdir()
        self.calls = self.root / "calls"

        self._write_executable("logger", "exit 0\n")
        self._write_executable("sync", "exit 0\n")
        self._write_executable(
            "flock",
            '[ -n "${WSCAN_FLOCK_DELAY:-}" ] && sleep "$WSCAN_FLOCK_DELAY"\nexit 0\n',
        )
        self._write_executable(
            "wpa_cli",
            textwrap.dedent(
                """\
                case "$*" in
                    *" abort_scan") printf 'FAIL\\n' ;;
                    *) printf 'OK\\n' ;;
                esac
                """
            ),
        )
        self._write_executable(
            "iw",
            textwrap.dedent(
                """\
                printf '%s\\n' "$*" > "$WSCAN_CALLS"
                [ -n "${WSCAN_IW_DELAY:-}" ] && sleep "$WSCAN_IW_DELAY"
                printf '%s\\n' "$WSCAN_IW_OUTPUT"
                exit "${WSCAN_IW_RC:-0}"
                """
            ),
        )

        self.env = dict(os.environ)
        self.env.update(
            {
                "PATH": f"{self.bin_dir}:{os.environ['PATH']}",
                "WIFI_RUN_DIR": str(self.run_dir),
                "WSCAN_CALLS": str(self.calls),
                "WSCAN_IW_OUTPUT": "",
            }
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def _write_executable(self, name, body):
        path = self.bin_dir / name
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(0o755)
        return path

    def _write_conf(self, body):
        (self.conf_dir / "wpa_supplicant-mlan0.conf").write_text(body)

    def _patched_wscan(self):
        source = WSCAN.read_text()
        replacements = {
            "WIFI=/usr/local/bin/wifi\n": f"WIFI={shlex.quote(str(WIFI_SH))}\n",
            "CONFIG_LIB=/usr/local/scripts/wifi_init_config_lib.sh\n": (
                f"CONFIG_LIB={shlex.quote(str(CONFIG_LIB))}\n"
            ),
            "WPA_CONF_DIR=/etc/wpa_supplicant\n": (
                f"WPA_CONF_DIR={shlex.quote(str(self.conf_dir))}\n"
            ),
        }
        for old, new in replacements.items():
            self.assertEqual(source.count(old), 1)
            source = source.replace(old, new, 1)
        path = self.root / "wscan"
        path.write_text(source)
        path.chmod(0o755)
        return path

    def run_wscan(self, *args, **env):
        merged = dict(self.env)
        merged.update(env)
        return subprocess.run(
            [str(self._patched_wscan()), *args],
            capture_output=True,
            text=True,
            timeout=10,
            env=merged,
        )

    def test_wifi_scan_directs_probe_on_explicit_frequencies(self):
        result = subprocess.run(
            [str(WIFI_SH), "mlan0", "scan", "--ssid", "Field AP", "36", "5200"],
            capture_output=True,
            text=True,
            timeout=10,
            env=self.env,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("WIFI_SCAN_START_MS=", result.stdout)
        self.assertEqual(
            self.calls.read_text().strip(),
            "mlan0 scan freq 5180 5200 ssid Field AP",
        )

    def test_wifi_scan_allows_full_band_only_for_directed_scan(self):
        directed = subprocess.run(
            [str(WIFI_SH), "mlan0", "scan", "--ssid", "Field AP"],
            capture_output=True,
            text=True,
            timeout=10,
            env=self.env,
        )
        self.assertEqual(directed.returncode, 0, directed.stdout + directed.stderr)
        self.assertEqual(self.calls.read_text().strip(), "mlan0 scan ssid Field AP")

        ordinary = subprocess.run(
            [str(WIFI_SH), "mlan0", "scan"],
            capture_output=True,
            text=True,
            timeout=10,
            env=self.env,
        )
        self.assertNotEqual(ordinary.returncode, 0)
        self.assertIn("configure freq not exist", ordinary.stderr)

    def test_returns_strongest_fresh_exact_ssid_using_profile_frequencies(self):
        profile = "freq_list=5180 5200\nnetwork={\n    ssid=\"old\"\n}\n"
        self._write_conf(profile)
        scan = textwrap.dedent(
            """\
            BSS 00:00:00:00:00:01(on mlan0)
                freq: 5180
                signal: -48.00 dBm
                last seen: 0 ms ago
                SSID: Field AP
            BSS 00:00:00:00:00:02(on mlan0)
                freq: 5200
                signal: -35.25 dBm
                last seen: 0 ms ago
                SSID: Field AP
            BSS 00:00:00:00:00:03(on mlan0)
                freq: 5200
                signal: -20.00 dBm
                last seen: 0 ms ago
                SSID: Field AP Guest
            """
        )
        result = self.run_wscan("0", "Field", "AP", WSCAN_IW_OUTPUT=scan)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout, 'SSID="Field AP" RSSI=-35.25 FREQ=5200\n')
        self.assertEqual(
            self.calls.read_text().strip(),
            "mlan0 scan freq 5180 5200 ssid Field AP",
        )
        self.assertEqual(
            (self.conf_dir / "wpa_supplicant-mlan0.conf").read_text(), profile
        )

    def test_no_profile_frequency_list_runs_full_band_directed_scan(self):
        self._write_conf('network={\n    ssid="old"\n}\n')
        scan = textwrap.dedent(
            """\
            BSS 00:00:00:00:00:01(on mlan0)
                freq: 2412
                signal: -41.00 dBm
                last seen: 0 ms ago
                SSID: target
            """
        )
        result = self.run_wscan("mlan0", "target", WSCAN_IW_OUTPUT=scan)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.calls.read_text().strip(), "mlan0 scan ssid target")

    def test_rejects_stale_target_even_when_it_is_stronger(self):
        self._write_conf("freq_list=5180\n")
        scan = textwrap.dedent(
            """\
            BSS 00:00:00:00:00:01(on mlan0)
                freq: 5180
                signal: -10.00 dBm
                last seen: 5000 ms ago
                SSID: target
            BSS 00:00:00:00:00:02(on mlan0)
                freq: 5180
                signal: -50.00 dBm
                last seen: 0 ms ago
                SSID: target
            """
        )
        result = self.run_wscan("0", "target", WSCAN_IW_OUTPUT=scan)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout, 'SSID="target" RSSI=-50.00 FREQ=5180\n')

    def test_rejects_recent_bss_outside_profile_frequencies(self):
        self._write_conf("freq_list=5180\n")
        scan = textwrap.dedent(
            """\
            BSS 00:00:00:00:00:01(on mlan0)
                freq: 2412
                signal: -20.00 dBm
                last seen: 0 ms ago
                SSID: target
            BSS 00:00:00:00:00:02(on mlan0)
                freq: 5180
                signal: -50.00 dBm
                last seen: 0 ms ago
                SSID: target
            """
        )
        result = self.run_wscan("0", "target", WSCAN_IW_OUTPUT=scan)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout, 'SSID="target" RSSI=-50.00 FREQ=5180\n')

    def test_lock_wait_is_excluded_from_freshness_window(self):
        self._write_conf("freq_list=5180\n")
        scan = textwrap.dedent(
            """\
            BSS 00:00:00:00:00:01(on mlan0)
                freq: 5180
                signal: -30.00 dBm
                last seen: 1400 ms ago
                SSID: target
            """
        )
        result = self.run_wscan(
            "0",
            "target",
            WSCAN_FLOCK_DELAY="1.5",
            WSCAN_IW_OUTPUT=scan,
        )
        self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "")

    def test_missing_or_absent_target_fails_without_a_result_line(self):
        self._write_conf("freq_list=5180\n")
        missing = self.run_wscan("0")
        self.assertEqual(missing.returncode, 2)
        self.assertEqual(missing.stdout, "")

        scan = textwrap.dedent(
            """\
            BSS 00:00:00:00:00:01(on mlan0)
                freq: 5180
                signal: -40.00 dBm
                last seen: 0 ms ago
                SSID: another
            """
        )
        absent = self.run_wscan("0", "target", WSCAN_IW_OUTPUT=scan)
        self.assertEqual(absent.returncode, 4)
        self.assertEqual(absent.stdout, "")


if __name__ == "__main__":
    unittest.main()
