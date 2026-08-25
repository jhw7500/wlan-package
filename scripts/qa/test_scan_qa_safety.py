#!/usr/bin/env python3
"""Host-side safety contract tests for destructive scan QA runners."""

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest


QA_DIR = Path(__file__).resolve().parent
CONTROLLER = QA_DIR / "moal_fw_scan_profile_controller.sh"
PRODUCT_SOAK = QA_DIR / "product_wifi_bgscan_soak.sh"
TARGET_RUNNERS = (
    "iw_external_scan_datapath_repro.sh",
    "wpa_cli_scan_only_datapath_repro.sh",
    "product_wifi_bgscan_soak.sh",
)
STANDALONE_SCAN_RUNNERS = (
    "iw_external_scan_datapath_repro.sh",
    "wpa_cli_scan_only_datapath_repro.sh",
)


def acknowledged_runner_command(
    name: str, artifact: Path | str, first_limit: str = "5"
) -> list[str]:
    command = [
        str(QA_DIR / name),
        "--ack-disruptive",
        str(artifact),
        "mlan0",
        "qa-scan-net",
        "192.0.2.1",
    ]
    if name == "product_wifi_bgscan_soak.sh":
        return command + [first_limit, "2400"]
    return command + [first_limit, "30"]


def run_sourced_product_helper(
    command: str, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [
            "bash",
            "-c",
            f"source {shlex.quote(str(PRODUCT_SOAK))}\n{command}",
        ],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )


def install_fake_systemctl(directory: Path) -> Path:
    state_file = directory / "systemctl-state"
    state_file.write_text("inactive\n", encoding="utf-8")
    fake = directory / "systemctl"
    fake.write_text(
        """#!/bin/bash
set -eu
state_file=${FAKE_SYSTEMCTL_STATE:?}
action=${1:?}
if [ "${FAKE_SYSTEMCTL_FAIL:-}" = "$action" ]; then
    echo "forced systemctl failure: $action" >&2
    exit 1
fi
case "$action" in
    start)
        printf 'active\\n' > "$state_file"
        ;;
    stop)
        printf 'inactive\\n' > "$state_file"
        ;;
    is-active)
        state=$(cat "$state_file")
        if [ "${2:-}" != --quiet ]; then
            printf '%s\\n' "$state"
        fi
        [ "$state" = active ]
        ;;
    *)
        echo "unexpected systemctl action: $action" >&2
        exit 64
        ;;
esac
""",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return state_file


def helper_env(fake_bin: Path, **values: str) -> dict[str, str]:
    return {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        **values,
    }


class DestructiveEntryPointTest(unittest.TestCase):
    def test_payload_manifest_excludes_the_live_harness_envelope(self) -> None:
        for name in STANDALONE_SCAN_RUNNERS:
            with self.subTest(name=name):
                source = (QA_DIR / name).read_text(encoding="utf-8")
                self.assertIn('! -name harness.log', source)
                self.assertIn('! -name harness-exit.txt', source)
                self.assertIn('! -name DONE', source)
                self.assertNotIn(
                    'sha256sum "$ART"/*.txt "$ART"/*.log', source
                )

    def test_target_runners_require_ack_before_artifact_write(self) -> None:
        for name in TARGET_RUNNERS:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                artifact = Path(tmp) / "artifacts"
                result = subprocess.run(
                    [str(QA_DIR / name), str(artifact)],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("--ack-disruptive", result.stderr)
                self.assertFalse(
                    artifact.exists(),
                    f"{name} wrote artifacts before explicit acknowledgement",
                )

    def test_acknowledged_runners_require_explicit_network_identity(self) -> None:
        for name in TARGET_RUNNERS:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                artifact = Path(tmp) / "artifacts"
                result = subprocess.run(
                    [
                        str(QA_DIR / name),
                        "--ack-disruptive",
                        str(artifact),
                    ],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("<iface> <ssid> <gateway>", result.stderr)
                self.assertFalse(artifact.exists())

    def test_target_runners_reject_root_artifact_path_before_root_check(
        self,
    ) -> None:
        for name in TARGET_RUNNERS:
            with self.subTest(name=name):
                result = subprocess.run(
                    acknowledged_runner_command(name, "/"),
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("new absolute artifact directory", result.stderr)

    def test_target_runners_reject_existing_artifact_directory(self) -> None:
        for name in TARGET_RUNNERS:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                artifact = Path(tmp) / "already-exists"
                artifact.mkdir()
                result = subprocess.run(
                    acknowledged_runner_command(name, artifact),
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("artifact path already exists", result.stderr)
                self.assertEqual(tuple(artifact.iterdir()), ())

    def test_target_runners_reject_zero_limit_before_root_check(self) -> None:
        for name in TARGET_RUNNERS:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                artifact = Path(tmp) / "artifacts"
                result = subprocess.run(
                    acknowledged_runner_command(name, artifact, first_limit="0"),
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("limits must be positive decimal integers", result.stderr)
                self.assertFalse(artifact.exists())

    def test_controller_rejects_root_artifact_path_before_root_or_config(
        self,
    ) -> None:
        result = subprocess.run(
            [
                str(CONTROLLER),
                "--ack-disruptive",
                "/",
                "/does/not/exist.conf",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("new absolute artifact directory", result.stderr)

    def test_controller_missing_ack_does_not_source_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            artifact = directory / "artifacts"
            sentinel = directory / "profile-was-sourced"
            config = directory / "unsafe.conf"
            config.write_text(
                f'PROFILE_NAME=unsafe\ntouch "{sentinel}"\n',
                encoding="utf-8",
            )
            config.chmod(0o666)

            result = subprocess.run(
                [str(CONTROLLER), str(artifact), str(config)],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("--ack-disruptive", result.stderr)
            self.assertFalse(sentinel.exists(), "unacknowledged profile was sourced")
            self.assertFalse(artifact.exists(), "unacknowledged artifacts were written")


class ProductSoakHelperTest(unittest.TestCase):
    def test_restore_returns_originally_active_unit_to_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            state_file = install_fake_systemctl(directory)
            result = run_sourced_product_helper(
                "restore_unit_active_state wifi_bgscan@mlan0.service 1",
                helper_env(directory, FAKE_SYSTEMCTL_STATE=str(state_file)),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(state_file.read_text(encoding="utf-8"), "active\n")

    def test_restore_returns_originally_inactive_unit_to_inactive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            state_file = install_fake_systemctl(directory)
            state_file.write_text("active\n", encoding="utf-8")
            result = run_sourced_product_helper(
                "restore_unit_active_state wifi_bgscan@mlan0.service 0",
                helper_env(directory, FAKE_SYSTEMCTL_STATE=str(state_file)),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(state_file.read_text(encoding="utf-8"), "inactive\n")

    def test_restore_failure_is_returned_to_the_harness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            state_file = install_fake_systemctl(directory)
            result = run_sourced_product_helper(
                "restore_unit_active_state wifi_bgscan@mlan0.service 1",
                helper_env(
                    directory,
                    FAKE_SYSTEMCTL_STATE=str(state_file),
                    FAKE_SYSTEMCTL_FAIL="start",
                ),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("forced systemctl failure: start", result.stderr)
            self.assertEqual(state_file.read_text(encoding="utf-8"), "inactive\n")

    def test_cleanup_restores_state_and_records_the_postcondition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            artifact = directory / "artifacts"
            artifact.mkdir()
            state_file = install_fake_systemctl(directory)
            allow_file = directory / "bgscan-allow"
            command = "\n".join(
                (
                    f"ART={shlex.quote(str(artifact))}",
                    "BG_UNIT=wifi_bgscan@mlan0.service",
                    "ORIGINAL_BG_STATE=active",
                    "ORIGINAL_BG_WAS_ACTIVE=1",
                    "BG_STATE_TOUCHED=1",
                    f"ALLOW_FILE={shlex.quote(str(allow_file))}",
                    "ALLOW_EXISTED=0",
                    "ALLOW_CREATED=0",
                    "FINALIZED=1",
                    "product_soak_cleanup",
                )
            )
            result = run_sourced_product_helper(
                command,
                helper_env(directory, FAKE_SYSTEMCTL_STATE=str(state_file)),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(state_file.read_text(encoding="utf-8"), "active\n")
            exit_record = (artifact / "harness-exit.txt").read_text(
                encoding="utf-8"
            )
            self.assertIn("harness_exit=0", exit_record)
            self.assertIn("bgscan_restore_rc=0", exit_record)
            self.assertIn("bgscan_state_after_restore=active", exit_record)

    def test_cleanup_promotes_restore_failure_to_exit_70(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            artifact = directory / "artifacts"
            artifact.mkdir()
            state_file = install_fake_systemctl(directory)
            allow_file = directory / "bgscan-allow"
            command = "\n".join(
                (
                    f"ART={shlex.quote(str(artifact))}",
                    "BG_UNIT=wifi_bgscan@mlan0.service",
                    "ORIGINAL_BG_STATE=active",
                    "ORIGINAL_BG_WAS_ACTIVE=1",
                    "BG_STATE_TOUCHED=1",
                    f"ALLOW_FILE={shlex.quote(str(allow_file))}",
                    "ALLOW_EXISTED=0",
                    "ALLOW_CREATED=0",
                    "FINALIZED=1",
                    "product_soak_cleanup",
                )
            )
            result = run_sourced_product_helper(
                command,
                helper_env(
                    directory,
                    FAKE_SYSTEMCTL_STATE=str(state_file),
                    FAKE_SYSTEMCTL_FAIL="start",
                ),
            )

            self.assertEqual(result.returncode, 70, result.stderr)
            exit_record = (artifact / "harness-exit.txt").read_text(
                encoding="utf-8"
            )
            self.assertIn("harness_exit=70", exit_record)
            self.assertIn("bgscan_restore_rc=1", exit_record)

    @unittest.skipUnless(shutil.which("jq"), "jq is required")
    def test_safe_evidence_omits_credentials_and_raw_configs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            artifact = directory / "artifacts"
            artifact.mkdir()
            config = directory / "wifi_init_conf.json"
            config.write_text(
                json.dumps(
                    {
                        "global": {
                            "BOARD_TYPE": "imx93",
                            "BUS_TYPE": "sdio",
                            "MOD_PARA": "cts/wifi_mod_para.conf",
                            "tx_work": 0,
                            "on_connect": "token=private-command-token",
                        },
                        "wbridge": {
                            "enabled": True,
                            "engine": "bridge",
                            "moal": {"bridge_mode": 1},
                        },
                        "mlan0": {
                            "enabled": True,
                            "bgscan": {
                                "enabled": True,
                                "interval": 60,
                                "passive": True,
                            },
                            "roaming": {
                                "enabled": True,
                                "generate_network_blocks": False,
                                "extra_ssids": [],
                            },
                            "wpa_supplicant": {"psk": "super-secret-psk"},
                        },
                    }
                ),
                encoding="utf-8",
            )
            module_config = directory / "wifi_mod_para.conf"
            module_config.write_text(
                "password=module-secret\n"
                "SD9098_0 = {\n"
                "    scan_chan_gap=0;\n"
                "}\n",
                encoding="utf-8",
            )
            wpa_config = directory / "wpa_supplicant-mlan0.conf"
            wpa_config.write_text(
                "freq_list=5180 5200 5220 5240\n"
                "network={\n"
                '    ssid="qa-scan-net"\n'
                '    psk="super-secret-psk"\n'
                "    scan_freq=5180 5200 5220 5240\n"
                "}\n",
                encoding="utf-8",
            )
            command = "capture_safe_config_evidence {} {} {} {} mlan0".format(
                shlex.quote(str(artifact)),
                shlex.quote(str(module_config)),
                shlex.quote(str(config)),
                shlex.quote(str(wpa_config)),
            )

            result = run_sourced_product_helper(command)

            self.assertEqual(result.returncode, 0, result.stderr)
            combined = b"".join(
                path.read_bytes() for path in sorted(artifact.iterdir())
            )
            self.assertNotIn(b"super-secret-psk", combined)
            self.assertNotIn(b"private-command-token", combined)
            self.assertNotIn(b"module-secret", combined)
            self.assertIn(b'"interval": 60', combined)
            self.assertFalse((artifact / "wifi_init_conf.json").exists())
            self.assertFalse((artifact / "wpa_supplicant-mlan0.conf").exists())
            self.assertFalse((artifact / "wifi_mod_para.conf").exists())

    def test_management_evidence_omits_ssh_endpoints_and_ports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            fake_ip = directory / "ip"
            fake_ip.write_text(
                "#!/bin/bash\n"
                "echo '192.0.2.10 via 192.0.2.1 dev eth0 src 192.0.2.20 uid 0'\n",
                encoding="utf-8",
            )
            fake_ip.chmod(0o755)
            connection = "192.0.2.10 12345 192.0.2.20 22"
            result = run_sourced_product_helper(
                f"emit_management_transport_evidence {shlex.quote(connection)}",
                helper_env(directory),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("management_transport=ssh", result.stdout)
            self.assertIn("management_route_device=eth0", result.stdout)
            for endpoint_token in connection.split():
                self.assertNotIn(endpoint_token, result.stdout)


if __name__ == "__main__":
    unittest.main()
