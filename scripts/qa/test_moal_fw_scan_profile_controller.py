#!/usr/bin/env python3
"""Contract tests for the reusable MOAL/FW scan profile controller."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest


SCRIPT = Path(__file__).with_name("moal_fw_scan_profile_controller.sh")
EXAMPLE_CONFIG = Path(__file__).with_name("moal_fw_scan_profile.example.conf")


def write_config(
    directory: Path, config_body: str, mode: int = 0o600
) -> Path:
    config = directory / "profile.conf"
    config.write_text(textwrap.dedent(config_body), encoding="utf-8")
    config.chmod(mode)
    return config


def run_profile_mode(
    profile_mode: str, config_body: str, file_mode: int = 0o600
) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as tmp:
        config = write_config(Path(tmp), config_body, file_mode)
        return subprocess.run(
            [str(SCRIPT), profile_mode, str(config)],
            text=True,
            capture_output=True,
            check=False,
        )


def run_describe(config_body: str) -> subprocess.CompletedProcess[str]:
    return run_profile_mode("--describe", config_body)


BASE = """
PROFILE_NAME=ant-rx-only
IFACE=mlan0
GW=192.0.2.1
STATION_IP=192.0.2.100
SSID=qa-scan-net
APPLY_AB=0
APPLY_ANTCFG=1
ANT_TX=0x303
ANT_RX=0x101
APPLY_RATE=0
APPLY_MCS=0
MOAL_ARGS=(
  mod_para=cts/wifi_mod_para.conf
  tx_work=0
  bridge_mode=1
)
"""

SHA256_A = "a" * 64
REQUIRED_HASH_PINS = f"""
EXPECTED_MLAN_SHA={SHA256_A}
EXPECTED_MOAL_SHA={SHA256_A}
EXPECTED_FW_SHA={SHA256_A}
EXPECTED_CONF_SHA={SHA256_A}
EXPECTED_JSON_SHA={SHA256_A}
EXPECTED_WPA_SHA={SHA256_A}
EXPECTED_MLANUTL_SHA={SHA256_A}
"""


class DescribeContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(SCRIPT.is_file(), "controller script not implemented")

    def test_describes_split_tx_rx_ant_profile(self) -> None:
        result = run_describe(BASE)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("profile_name=ant-rx-only", result.stdout)
        self.assertIn("apply_antcfg=1", result.stdout)
        self.assertIn("ant_tx=0x303", result.stdout)
        self.assertIn("ant_rx=0x101", result.stdout)
        self.assertIn(
            "ant_command=mlanutl mlan0 antcfg 0x303 0x101", result.stdout
        )
        self.assertIn("apply_rate=0", result.stdout)
        self.assertIn("apply_mcs=0", result.stdout)
        self.assertIn("moal_arg_count=3", result.stdout)

    def test_describes_physical_and_host_nss_expectations_separately(self) -> None:
        configured = BASE + """
EXPECTED_ANT_TX=0x303
EXPECTED_ANT_RX=0x303
EXPECTED_USER_HTSTREAM=0x2121
"""
        result = run_describe(configured)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("expected_ant_tx=0x303", result.stdout)
        self.assertIn("expected_ant_rx=0x303", result.stdout)
        self.assertIn("expected_user_htstream=0x2121", result.stdout)

    def test_describes_opt_in_drvdbg_mask(self) -> None:
        result = run_describe(BASE + "\nDRVDBG_OR=0x10\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("drvdbg_or=0x10", result.stdout)

    def test_rejects_invalid_drvdbg_mask(self) -> None:
        result = run_describe(BASE + "\nDRVDBG_OR=MCMND\n")
        self.assertEqual(result.returncode, 2)
        self.assertIn(
            "DRVDBG_OR must be a decimal or hexadecimal integer",
            result.stderr,
        )

    def test_rejects_invalid_host_nss_expectation(self) -> None:
        result = run_describe(BASE + "\nEXPECTED_USER_HTSTREAM=rx-one\n")
        self.assertEqual(result.returncode, 2)
        self.assertIn(
            "EXPECTED_USER_HTSTREAM must be a decimal or hexadecimal integer",
            result.stderr,
        )

    def test_describes_single_ant_argument_as_tx_equals_rx(self) -> None:
        result = run_describe(BASE.replace("ANT_RX=0x101", 'ANT_RX=""'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ant_rx=<same-as-tx>", result.stdout)
        self.assertIn("ant_command=mlanutl mlan0 antcfg 0x303", result.stdout)

    def test_rejects_ant_profile_without_tx_value(self) -> None:
        result = run_describe(BASE.replace("ANT_TX=0x303", 'ANT_TX=""'))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ANT_TX is required", result.stderr)

    def test_rejects_non_boolean_action_toggle(self) -> None:
        result = run_describe(BASE.replace("APPLY_RATE=0", "APPLY_RATE=yes"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("APPLY_RATE must be 0 or 1", result.stderr)

    def test_rejects_missing_network_identity(self) -> None:
        values = {
            "GW": "192.0.2.1",
            "STATION_IP": "192.0.2.100",
            "SSID": "qa-scan-net",
        }
        for name, value in values.items():
            with self.subTest(name=name):
                result = run_describe(
                    BASE.replace(f"{name}={value}", f'{name}=""')
                )
                self.assertEqual(result.returncode, 2)
                self.assertIn(f"{name} is required", result.stderr)

    def test_rejects_group_writable_profile_before_sourcing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            sentinel = directory / "sourced"
            config = write_config(
                directory,
                BASE + f'\ntouch "{sentinel}"\n',
                0o620,
            )
            result = subprocess.run(
                [str(SCRIPT), "--describe", str(config)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("group/other writable", result.stderr)
            self.assertFalse(sentinel.exists(), "unsafe profile was sourced")

    def test_rejects_symlink_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            target = write_config(directory, BASE)
            link = directory / "profile-link.conf"
            link.symlink_to(target)
            result = subprocess.run(
                [str(SCRIPT), "--describe", str(link)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("non-symlink", result.stderr)

    def test_validate_profile_rejects_missing_required_hash_pin(self) -> None:
        result = run_profile_mode("--validate-profile", BASE)
        self.assertEqual(result.returncode, 2)
        self.assertIn(
            "EXPECTED_MLAN_SHA must be a 64-hex SHA-256", result.stderr
        )

    def test_validate_profile_rejects_invalid_hash_pin(self) -> None:
        invalid_pins = REQUIRED_HASH_PINS.replace(SHA256_A, "a" * 63, 1)
        result = run_profile_mode("--validate-profile", BASE + invalid_pins)
        self.assertEqual(result.returncode, 2)
        self.assertIn(
            "EXPECTED_MLAN_SHA must be a 64-hex SHA-256", result.stderr
        )

    def test_validate_profile_accepts_all_base_hash_pins(self) -> None:
        result = run_profile_mode(
            "--validate-profile", BASE + REQUIRED_HASH_PINS
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("profile_valid=ant-rx-only", result.stdout)

    def test_validate_profile_requires_conditional_hash_pins(self) -> None:
        enabled = BASE.replace("APPLY_AB=0", "APPLY_AB=1").replace(
            "APPLY_RATE=0", "APPLY_RATE=1"
        )
        result = run_profile_mode(
            "--validate-profile", enabled + REQUIRED_HASH_PINS
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn(
            "EXPECTED_FW_LIB_SHA must be a 64-hex SHA-256", result.stderr
        )

        conditional_pins = f"""
EXPECTED_FW_LIB_SHA={SHA256_A}
EXPECTED_TXPWR_SHA={SHA256_A}
EXPECTED_THERMAL_SHA={SHA256_A}
"""
        result = run_profile_mode(
            "--validate-profile",
            enabled + REQUIRED_HASH_PINS + conditional_pins,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("profile_valid=ant-rx-only", result.stdout)

    def test_repository_example_config_is_describable(self) -> None:
        # 컨트롤러는 group/other writable 설정을 거부한다(정상적인 보안 검사). 저장소
        # 파일의 git 모드는 100644 지만 체크아웃하는 쪽 umask 가 0002 면 워크트리에서
        # 664 가 되어 이 테스트가 **환경에 따라** 실패한다(실측). 내용은 저장소 예시를
        # 그대로 쓰되 모드는 테스트가 직접 정해, 통과/실패가 umask 로 갈리지 않게 한다.
        with tempfile.TemporaryDirectory() as tmp:
            conf = Path(tmp) / EXAMPLE_CONFIG.name
            shutil.copyfile(EXAMPLE_CONFIG, conf)
            conf.chmod(0o600)
            result = subprocess.run(
                [str(SCRIPT), "--describe", str(conf)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("profile_name=example-ant-split", result.stdout)
            self.assertIn("ant_command=mlanutl mlan0 antcfg 0x303 0x101", result.stdout)


if __name__ == "__main__":
    unittest.main()
