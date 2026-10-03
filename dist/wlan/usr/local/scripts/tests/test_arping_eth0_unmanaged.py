"""`wifi_arping@eth0` 는 설정 게이트가 없으므로 패키지가 살려 두지 않는다.

postinst 는 예전 프로비저닝이 남긴 wifi-stack.target.wants enable 을 걷어 내고,
`wifi config` 는 eth0 인스턴스가 이미 떠 있을 때만 재시작한다. 둘 다 실제 셸을
가짜 systemctl/logger 로 실행해 호출 내역을 본다.
"""

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

DIST = Path(__file__).resolve().parents[4]
POSTINST = DIST / "DEBIAN/postinst"
WIFI_CONFIG = DIST / "usr/local/scripts/wifi_config.sh"

STUB_SYSTEMCTL = """#!/bin/sh
printf '%s\\n' "$*" >> "$CALLS"
case "$1" in
    is-enabled) exit "${IS_ENABLED_RC:-1}" ;;
    disable) exit "${DISABLE_RC:-0}" ;;
esac
exit 0
"""


def make_stub_bin(root: Path) -> Path:
    bin_dir = root / "bin"
    bin_dir.mkdir()
    stubs = {
        "systemctl": STUB_SYSTEMCTL,
        "logger": "#!/bin/sh\n[ -n \"$LOGS\" ] && printf '%s\\n' \"$*\" >> \"$LOGS\"\nexit 0\n",
        "networkctl": "#!/bin/sh\nexit 0\n",
    }
    for name, body in stubs.items():
        path = bin_dir / name
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return bin_dir


def cleanup_block() -> str:
    text = POSTINST.read_text(encoding="utf-8")
    start = text.index("# >>> arping-eth0-legacy-cleanup")
    end = text.index("# <<< arping-eth0-legacy-cleanup")
    return text[start:end]


class PostinstLegacyCleanup(unittest.TestCase):
    def run_block(self, is_enabled_rc, disable_rc=0):
        root = Path(tempfile.mkdtemp(prefix="arping-postinst-"))
        bin_dir = make_stub_bin(root)
        calls = root / "calls"
        logs = root / "logs"
        calls.touch()
        logs.touch()
        script = "set -e\ntag=postinst\nKEY=PKG\n" + cleanup_block() + "\necho DONE\n"
        env = dict(os.environ, PATH=f"{bin_dir}:/usr/bin:/bin", CALLS=str(calls),
                   LOGS=str(logs), IS_ENABLED_RC=str(is_enabled_rc),
                   DISABLE_RC=str(disable_rc))
        result = subprocess.run(["bash", "-c", script], env=env,
                                capture_output=True, text=True, check=False)
        return result, calls.read_text().splitlines(), logs.read_text().splitlines()

    def test_enabled_legacy_unit_is_disabled_and_stopped(self):
        result, calls, logs = self.run_block(is_enabled_rc=0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("disable --now wifi_arping@eth0.service", calls)
        self.assertEqual(len(logs), 1, logs)
        self.assertTrue(logs[0].startswith("-p local0.info"), logs)
        self.assertIn("disabled legacy wifi_arping@eth0", logs[0])

    def test_not_enabled_unit_is_left_alone(self):
        result, calls, logs = self.run_block(is_enabled_rc=1)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, ["is-enabled --quiet wifi_arping@eth0.service"])
        self.assertEqual(logs, [])

    def test_disable_failure_warns_and_does_not_abort_postinst(self):
        result, _calls, logs = self.run_block(is_enabled_rc=0, disable_rc=1)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DONE", result.stdout)
        self.assertEqual(len(logs), 1, logs)
        self.assertTrue(logs[0].startswith("-p local0.warn"), logs)
        self.assertNotIn("disabled legacy", logs[0])


class WifiConfigDoesNotResurrectEth0Arping(unittest.TestCase):
    def test_eth0_arping_only_try_restarted(self):
        root = Path(tempfile.mkdtemp(prefix="arping-wifi-config-"))
        bin_dir = make_stub_bin(root)
        calls = root / "calls"
        calls.touch()
        env = dict(os.environ, PATH=f"{bin_dir}:/usr/bin:/bin", CALLS=str(calls))
        result = subprocess.run(["bash", str(WIFI_CONFIG), "mlan0"], env=env,
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        arping = [c for c in calls.read_text().splitlines() if "wifi_arping@eth0" in c]
        self.assertEqual(arping, ["try-restart wifi_arping@eth0"])


if __name__ == "__main__":
    unittest.main()
