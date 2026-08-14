import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import pytest


WLAN_ROOT = Path(__file__).resolve().parents[4]
SCRIPTS = WLAN_ROOT / "usr/local/scripts"


@dataclass
class TimedResult:
    returncode: int
    stdout: str
    stderr: str
    elapsed: float
    log: str = ""


def _write_exe(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


@pytest.fixture
def run_shell():
    def run(command: str, **extra_env) -> TimedResult:
        env = os.environ | {key: str(value) for key, value in extra_env.items()}
        start = time.monotonic()
        cp = subprocess.run(
            ["bash", "-c", command],
            cwd=SCRIPTS,
            env=env,
            text=True,
            capture_output=True,
            timeout=15,
        )
        return TimedResult(
            cp.returncode,
            cp.stdout,
            cp.stderr,
            time.monotonic() - start,
        )

    return run


@pytest.fixture
def snapshot_once(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for name in ("wpa_cli", "iw"):
        _write_exe(fake_bin / name, "#!/bin/sh\nsleep 30\n")
    _write_exe(fake_bin / "logger", "#!/bin/sh\nexit 0\n")
    log_path = tmp_path / "snap.log"

    def run(iface: str) -> TimedResult:
        env = os.environ | {
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "WIFI_LOGGER_ONESHOT": "1",
            "WIFI_LOGGER_INITIAL_DELAY_SEC": "0",
            "WIFI_LOGGER_COMMAND_TIMEOUT_SEC": "0.2",
            "WIFI_LOGGER_SNAPSHOT_PATH": str(log_path),
        }
        start = time.monotonic()
        cp = subprocess.run(
            ["bash", str(SCRIPTS / "wifi_link_snapshot.sh"), iface],
            env=env,
            text=True,
            capture_output=True,
            timeout=5,
        )
        return TimedResult(
            cp.returncode,
            cp.stdout,
            cp.stderr,
            time.monotonic() - start,
            log_path.read_text() if log_path.exists() else "",
        )

    return run


def test_bounded_runner_kills_hung_command(run_shell):
    result = run_shell(
        '. ./wifi_logger_command_lib.sh; '
        'logger_run_bounded 0.2 sh -c "sleep 30"'
    )

    assert result.returncode == 124
    assert result.elapsed < 2.0


def test_bounded_reader_preserves_successful_contents(run_shell, tmp_path):
    sample = tmp_path / "sample"
    sample.write_text("bounded-read\n")

    result = run_shell(
        f'. ./wifi_logger_command_lib.sh; logger_read_bounded 1 "{sample}"'
    )

    assert result.returncode == 0
    assert result.stdout == "bounded-read\n"


def test_snapshot_times_out_each_command(snapshot_once):
    result = snapshot_once("mlan0")

    assert result.returncode == 0
    assert result.elapsed < 3.0
    assert "wpa_cli timeout" in result.log
    assert "iw timeout" in result.log


def test_system_samplers_use_the_shared_bounded_command_library():
    for name in (
        "wifi_logger_cpu.sh",
        "wifi_logger_mmc.sh",
        "wifi_logger_temp.sh",
        "wifi_logger_mcp.sh",
    ):
        text = (SCRIPTS / name).read_text()
        assert "wifi_logger_command_lib.sh" in text, name
        assert "logger_run_bounded" in text or "logger_read_bounded" in text, name


@pytest.fixture
def run_system_sampler_once(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    capture = tmp_path / "commands.log"
    capture.write_text("")

    _write_exe(
        fake_bin / "logger",
        '#!/bin/sh\nprintf "logger %s\\n" "$*" >> "$LOGGER_CAPTURE"\n',
    )
    for name in ("mpstat", "sar", "cat"):
        _write_exe(fake_bin / name, "#!/bin/sh\nexec sleep 30\n")

    ext_csd = tmp_path / "ext_csd"
    ext_csd.write_text("00" * 512)
    iio = tmp_path / "iio:device0"
    iio.mkdir()
    cpu_root = tmp_path / "cpu-root"
    cpu_root.mkdir()

    def run(script_name: str) -> TimedResult:
        env = os.environ | {
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "LOGGER_CAPTURE": str(capture),
            "WIFI_INIT_CONF_JSON": str(tmp_path / "missing.json"),
            "WIFI_LOGGER_ONESHOT": "1",
            "WIFI_LOGGER_COMMAND_TIMEOUT_SEC": "0.2",
            "WIFI_CPU_SYSFS_ROOT": str(cpu_root),
            "WIFI_MMC_EXT_CSD_PATH": str(ext_csd),
            "WIFI_MCP_IIO_DEVICE": str(iio),
            "WIFI_LOGGER_MCP_MAX_PROBE_FAIL": "1",
            "WIFI_LOGGER_MCP_CHECK_INTERVAL": "0.01",
        }
        start = time.monotonic()
        cp = subprocess.run(
            ["bash", str(SCRIPTS / script_name)],
            env=env,
            text=True,
            capture_output=True,
            timeout=3,
        )
        return TimedResult(
            cp.returncode,
            cp.stdout,
            cp.stderr,
            time.monotonic() - start,
            capture.read_text(),
        )

    return run


def test_cpu_commands_time_out_without_freezing_logger(run_system_sampler_once):
    result = run_system_sampler_once("wifi_logger_cpu.sh")

    assert result.returncode == 0
    assert result.elapsed < 2.0
    assert "CPU:unknown%, MEM:unknown%" in result.log


def test_mmc_sysfs_read_times_out_without_freezing_logger(run_system_sampler_once):
    result = run_system_sampler_once("wifi_logger_mmc.sh")

    assert result.returncode == 0
    assert result.elapsed < 2.0
    assert "MMC read failed" in result.log


def test_mcp_sysfs_read_timeout_is_a_visible_failure(run_system_sampler_once):
    result = run_system_sampler_once("wifi_logger_mcp.sh")

    assert result.returncode != 0
    assert result.elapsed < 2.0
    assert "ADC read failed" in result.log
    assert "monitoring disabled" in result.log


@pytest.fixture
def run_temp_once(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    capture = tmp_path / "temperature.log"
    capture.write_text("")
    cpu_sequence_file = tmp_path / "cpu-sequence"
    cpu_counter = tmp_path / "cpu-counter"

    _write_exe(
        fake_bin / "logger",
        '#!/bin/sh\nprintf "logger %s\\n" "$*" >> "$LOGGER_CAPTURE"\n',
    )
    _write_exe(
        fake_bin / "systemctl",
        '#!/bin/sh\nprintf "systemctl %s\\n" "$*" >> "$LOGGER_CAPTURE"\n',
    )
    _write_exe(
        fake_bin / "mlanutl",
        """#!/bin/sh
case "$FAKE_MLANUTL_BEHAVIOR" in
  timeout) exec sleep 30 ;;
  malformed) printf 'sensor temperature unavailable\n' ;;
  *) printf 'sensor temp value 50\n' ;;
esac
""",
    )
    _write_exe(
        fake_bin / "cat",
        """#!/bin/sh
[ "$1" = "--" ] && shift
if [ "$1" = "$FAKE_CPU_TEMP_PATH" ]; then
  n=$(/bin/cat "$FAKE_CPU_COUNTER" 2>/dev/null || printf '0')
  n=$((n + 1))
  printf '%s\n' "$n" > "$FAKE_CPU_COUNTER"
  value=$(/bin/sed -n "${n}p" "$FAKE_CPU_SEQUENCE")
  [ -n "$value" ] || value=$(/usr/bin/tail -n 1 "$FAKE_CPU_SEQUENCE")
  printf '%s\n' "$value"
  exit 0
fi
exec /bin/cat "$@"
""",
    )
    snapshot = tmp_path / "journald_snapshot"
    _write_exe(
        snapshot,
        '#!/bin/sh\nprintf "snapshot %s\\n" "$*" >> "$LOGGER_CAPTURE"\n',
    )
    reboot = tmp_path / "wlan_reboot_policy"
    _write_exe(
        reboot,
        '#!/bin/sh\nprintf "wlan_reboot_policy %s\\n" "$*" >> "$LOGGER_CAPTURE"\n',
    )

    cpu_temp_path = tmp_path / "thermal_zone0_temp"
    cpu_temp_path.write_text("unused\n")
    max_temp_path = tmp_path / "max_temp"
    net_root = tmp_path / "net"
    (net_root / "mlan0").mkdir(parents=True)
    (net_root / "mlan1").mkdir()
    config = tmp_path / "wifi_init_conf.json"

    def run(
        *,
        mlanutl_behavior="valid",
        cpu_sequence=(50000,),
        emerg_threshold=93,
    ) -> TimedResult:
        capture.write_text("")
        cpu_counter.unlink(missing_ok=True)
        cpu_sequence_file.write_text(
            "".join(f"{value}\n" for value in cpu_sequence)
        )
        config.write_text(
            """{
  "temperature": {
    "emerg_cpu": %d,
    "crit_cpu": 90,
    "error_cpu": 85,
    "warn_cpu": 80,
    "emerg_mlan": 85,
    "crit_mlan": 80,
    "error_mlan": 75,
    "warn_mlan": 70,
    "recover_cpu": 90,
    "recover_mlan": 80,
    "cooldown_sec": 0,
    "emerg_count_threshold": 0,
    "check_interval_sec": 0
  }
}
"""
            % emerg_threshold
        )
        env = os.environ | {
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "LOGGER_CAPTURE": str(capture),
            "FAKE_MLANUTL_BEHAVIOR": mlanutl_behavior,
            "FAKE_CPU_TEMP_PATH": str(cpu_temp_path),
            "FAKE_CPU_COUNTER": str(cpu_counter),
            "FAKE_CPU_SEQUENCE": str(cpu_sequence_file),
            "WIFI_INIT_CONF_JSON": str(config),
            "WIFI_CPU_TEMP_PATH": str(cpu_temp_path),
            "WIFI_MAX_TEMP_PATH": str(max_temp_path),
            "WIFI_NET_CLASS_ROOT": str(net_root),
            "WIFI_LOGGER_ONESHOT": "1",
            "WIFI_LOGGER_INITIAL_DELAY_SEC": "0",
            "WIFI_LOGGER_TEMP_TIMEOUT_SEC": "0.2",
            "WIFI_LOGGER_COMMAND_TIMEOUT_SEC": "0.2",
            "WIFI_LOGGER_COOLDOWN_RETRY_SEC": "0",
            "WIFI_LOGGER_REBOOT_DELAY_SEC": "0",
            "WIFI_STOP_UNITS": "wifi_logger@mlan0",
            "WIFI_JOURNALD_SNAPSHOT_SH": str(snapshot),
            "WIFI_REBOOT_POLICY_SH": str(reboot),
        }
        start = time.monotonic()
        cp = subprocess.run(
            ["bash", str(SCRIPTS / "wifi_logger_temp.sh")],
            env=env,
            text=True,
            capture_output=True,
            timeout=5,
        )
        return TimedResult(
            cp.returncode,
            cp.stdout,
            cp.stderr,
            time.monotonic() - start,
            capture.read_text(),
        )

    return run


@pytest.mark.parametrize("behavior", ("timeout", "malformed"))
def test_temperature_invalid_read_is_unknown_not_zero(run_temp_once, behavior):
    result = run_temp_once(mlanutl_behavior=behavior)

    assert result.returncode == 0
    assert result.elapsed < 2.0
    assert "mlan0 : unknown" in result.log
    assert "mlan0 : 0" not in result.log


def test_temperature_emergency_still_calls_existing_safety_path(run_temp_once):
    result = run_temp_once(cpu_sequence=(94000, 80000), emerg_threshold=93)

    assert result.returncode == 0
    assert "systemctl stop wifi_logger@mlan0" in result.log
    assert "snapshot" in result.log
    assert "wlan_reboot_policy --source wifi_logger_temp" in result.log
