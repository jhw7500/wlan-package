"""Roam owner와 background-scan 서비스 정책의 부팅 계약."""

import json
import os
import subprocess
from pathlib import Path

import pytest


WLAN_ROOT = Path(__file__).resolve().parents[4]
APPLY = WLAN_ROOT / "usr/local/scripts/wifi_apply_enabled.sh"
LIB = WLAN_ROOT / "usr/local/scripts/wifi_init_config_lib.sh"
SYSTEMD = WLAN_ROOT / "etc/systemd/system"


def _write_exe(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(0o755)


def _snapshot_fault_bin(tmp_path: Path) -> Path:
    """Command shims that fail one named snapshot durability boundary."""
    fake_bin = tmp_path / "fault-bin"
    fake_bin.mkdir(exist_ok=True)
    _write_exe(
        fake_bin / "sync",
        r'''#!/bin/sh
if [ "$#" -eq 0 ] && [ -f "$FAULT_STATE/fallback-pending" ]; then
  rm -f "$FAULT_STATE/fallback-pending"
  exit 1
fi
arg=${1:-}
actual=
case "$arg" in
  "$LATCH_DIR"/.mlan0.roam-policy.latched.*) actual=latch-stage-sync ;;
  "$LATCH_DIR"/.mlan0.roam-policy.latched)   actual=latch-installed-sync ;;
  "$LATCH_DIR")                              actual=latch-dir-sync ;;
  "$RUN_DIR"/.mlan0.roam-policy.*)           actual=policy-stage-sync ;;
  "$RUN_DIR"/mlan0.roam-policy.json)         actual=policy-installed-sync ;;
  "$RUN_DIR")                                actual=policy-dir-sync ;;
esac
if [ -n "$actual" ] && [ "$FAULT_POINT" = "$actual" ]; then
  : > "$FAULT_STATE/fallback-pending"
  exit 1
fi
exit 0
''',
    )
    _write_exe(
        fake_bin / "mv",
        r'''#!/bin/sh
dst=
for arg in "$@"; do dst=$arg; done
actual=
case "$dst" in
  "$LATCH_DIR"/.mlan0.roam-policy.latched) actual=latch-rename ;;
  "$RUN_DIR"/mlan0.roam-policy.json)       actual=policy-rename ;;
esac
if [ -n "$actual" ] && [ "$FAULT_POINT" = "$actual" ]; then exit 1; fi
exec /bin/mv "$@"
''',
    )
    _write_exe(
        fake_bin / "chmod",
        r'''#!/bin/sh
dst=
for arg in "$@"; do dst=$arg; done
actual=
case "$dst" in
  "$LATCH_DIR"/.mlan0.roam-policy.latched.*) actual=latch-chmod ;;
  "$RUN_DIR"/.mlan0.roam-policy.*)           actual=policy-chmod ;;
esac
if [ -n "$actual" ] && [ "$FAULT_POINT" = "$actual" ]; then exit 1; fi
exec /bin/chmod "$@"
''',
    )
    _write_exe(
        fake_bin / "jq",
        r'''#!/bin/sh
actual=
last=
for arg in "$@"; do
  [ "$arg" != "-cn" ] || actual=policy-render
  last=$arg
done
case "$last" in
  "$RUN_DIR"/.mlan0.roam-policy.*)
    [ "$actual" = policy-render ] || actual=policy-stage-validate ;;
esac
if [ -n "$actual" ] && [ "$FAULT_POINT" = "$actual" ]; then exit 1; fi
exec /usr/bin/jq "$@"
''',
    )
    return fake_bin


def _run_snapshot_ensure(
    config: Path,
    run_dir: Path,
    latch_dir: Path,
    fake_bin: Path,
    fault_state: Path,
    fault_point: str,
) -> subprocess.CompletedProcess[str]:
    fault_state.mkdir(exist_ok=True)
    for child in fault_state.iterdir():
        child.unlink()
    wpa_dir = run_dir.parent / "wpa"
    wpa_dir.mkdir(exist_ok=True)
    base_conf = wpa_dir / "wpa_supplicant-mlan0.conf"
    if not base_conf.exists():
        base_conf.write_text('network={\n    ssid="Base"\n}\n')
    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "WIFI_RUN_DIR": str(run_dir),
        "WIFI_ROAM_POLICY_LATCH_DIR": str(latch_dir),
        "RUN_DIR": str(run_dir),
        "LATCH_DIR": str(latch_dir),
        "FAULT_STATE": str(fault_state),
        "FAULT_POINT": fault_point,
        "WPA_CONF_DIR": str(wpa_dir),
    }
    return subprocess.run(
        [
            "bash",
            "-c",
            '. "$1"; wifi_roam_policy_ensure_snapshot mlan0 "$2"',
            "_",
            str(LIB),
            str(config),
        ],
        env=env,
        text=True,
        capture_output=True,
        timeout=5,
    )


def _config(
    roaming_enabled: bool,
    *,
    generate_network_blocks: bool = False,
    extra_ssids: list[str] | None = None,
    bgscan_enabled: bool = True,
) -> dict:
    iface = {
        "enabled": True,
        "net_rx": 0,
        "STANDARD": "ac",
        "wpa_supplicant": {"enabled": False},
        "logger": {"enabled": False},
        "checker": {"enabled": False},
        "bgscan": {"enabled": bgscan_enabled},
        "roaming": {
            "enabled": roaming_enabled,
            "generate_network_blocks": generate_network_blocks,
            "extra_ssids": extra_ssids or [],
        },
        # Legacy conflict input: policy must ignore true and force the unit off.
        "periodic_roam": {"enabled": True},
        "arping": {"enabled": False},
        "on_connect": {"enabled": False},
        "mcs_tier": {"enabled": False, "he": ""},
    }
    disabled_iface = json.loads(json.dumps(iface))
    disabled_iface["enabled"] = False
    disabled_iface["periodic_roam"]["enabled"] = False
    return {
        "global": {
            "MOD_PARA": "does-not-exist.conf",
            "ping_monitor": {"enabled": False},
            "fw_watch": {"enabled": False},
        },
        "logger": {"enabled": False},
        "eth0": {"logger": {"enabled": False}},
        "wbridge": {
            "enabled": False,
            "bridge_iface": "mlan0",
            "thermal": {"enabled": False},
        },
        "snmp": {"enabled": False, "trap": {"enabled": False}},
        "opc": {"enabled": False},
        "mlan0": iface,
        "mlan1": disabled_iface,
    }


@pytest.mark.parametrize("roaming_enabled", [True, False])
def test_roam_owner_drives_services_and_periodic_owner_is_forced_off(
    tmp_path: Path, roaming_enabled: bool
) -> None:
    config = tmp_path / "wifi_init_conf.json"
    config.write_text(json.dumps(_config(roaming_enabled)))
    calls = tmp_path / "systemctl.calls"
    logs = tmp_path / "logger.calls"
    calls.write_text("")
    logs.write_text("")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_exe(
        fake_bin / "systemctl",
        """#!/bin/sh
printf '%s\n' "$*" >> "$FAKE_SYSTEMCTL_CALLS"
if [ "$1" = "is-enabled" ]; then
  if grep -Fxq "disable $3" "$FAKE_SYSTEMCTL_CALLS"; then exit 1; fi
  case ",${FAKE_ENABLED_UNITS:-}," in
    *",$3,"*) exit 0 ;;
    *) exit 1 ;;
  esac
fi
exit 0
""",
    )
    _write_exe(
        fake_bin / "logger",
        """#!/bin/sh
printf '%s\n' "$*" >> "$FAKE_LOGGER_CALLS"
exit 0
""",
    )

    initially_enabled = ["wifi_periodic_roam@mlan0.service"]
    if not roaming_enabled:
        initially_enabled.append("wifi_roam@mlan0.service")
    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "WIFI_INIT_CONF_JSON": str(config),
        "WIFI_APPLY_STRICT": "1",
        "FAKE_SYSTEMCTL_CALLS": str(calls),
        "FAKE_LOGGER_CALLS": str(logs),
        "FAKE_ENABLED_UNITS": ",".join(initially_enabled),
        "WIFI_RUN_DIR": str(tmp_path / "run"),
    }
    result = subprocess.run(
        ["bash", str(APPLY)],
        env=env,
        text=True,
        capture_output=True,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr
    recorded = calls.read_text().splitlines()
    assert "enable wifi_bgscan@mlan0.service" in recorded
    if roaming_enabled:
        assert "enable wifi_roam@mlan0.service" in recorded
        assert "disable wifi_roam@mlan0.service" not in recorded
    else:
        assert "disable wifi_roam@mlan0.service" in recorded
        assert "enable wifi_roam@mlan0.service" not in recorded

    assert "disable wifi_periodic_roam@mlan0.service" in recorded
    assert "stop wifi_periodic_roam@mlan0.service" in recorded
    if roaming_enabled:
        assert "stop wifi_roam@mlan0.service" not in recorded
    else:
        assert "stop wifi_roam@mlan0.service" in recorded

    snapshot = json.loads(
        (tmp_path / "run" / "mlan0.roam-policy.json").read_text()
    )
    assert snapshot == {
        "version": 1,
        "iface": "mlan0",
        "roaming_enabled": roaming_enabled,
        "bgscan_enabled": True,
        "generate_network_blocks": False,
        "extra_ssids": [],
    }
    warning = logs.read_text() + result.stderr
    assert "periodic_roam" in warning
    assert "deprecated" in warning.lower()


def test_boot_policy_snapshot_is_immutable_until_reboot(tmp_path: Path) -> None:
    """같은 /run 안에서는 JSON을 바꾸고 apply를 재실행해도 owner/topology가 바뀌지 않는다."""
    config = tmp_path / "wifi_init_conf.json"
    config.write_text(
        json.dumps(
            _config(
                True,
                generate_network_blocks=True,
                extra_ssids=["Office", "Guest"],
            )
        )
    )
    calls = tmp_path / "systemctl.calls"
    logs = tmp_path / "logger.calls"
    calls.write_text("")
    logs.write_text("")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_exe(
        fake_bin / "systemctl",
        """#!/bin/sh
printf '%s\n' "$*" >> "$FAKE_SYSTEMCTL_CALLS"
if [ "$1" = "is-enabled" ]; then exit 1; fi
exit 0
""",
    )
    _write_exe(fake_bin / "logger", "#!/bin/sh\nexit 0\n")
    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "WIFI_INIT_CONF_JSON": str(config),
        "WIFI_RUN_DIR": str(tmp_path / "run"),
        "FAKE_SYSTEMCTL_CALLS": str(calls),
        "FAKE_LOGGER_CALLS": str(logs),
    }

    first = subprocess.run(
        ["bash", str(APPLY)], env=env, text=True, capture_output=True, timeout=5
    )
    assert first.returncode == 0, first.stderr
    snap_path = tmp_path / "run" / "mlan0.roam-policy.json"
    first_snapshot = snap_path.read_text()

    changed = _config(False, bgscan_enabled=False)
    config.write_text(json.dumps(changed))
    calls.write_text("")
    second = subprocess.run(
        ["bash", str(APPLY)], env=env, text=True, capture_output=True, timeout=5
    )
    assert second.returncode == 0, second.stderr
    assert snap_path.read_text() == first_snapshot
    snapshot = json.loads(first_snapshot)
    assert snapshot["roaming_enabled"] is True
    assert snapshot["bgscan_enabled"] is True
    assert snapshot["generate_network_blocks"] is True
    assert snapshot["extra_ssids"] == ["Office", "Guest"]


def test_mode_b_snapshot_preserves_manual_extra_ssid_identities_byte_exact(
    tmp_path: Path,
) -> None:
    config = tmp_path / "wifi_init_conf.json"
    special = '  게스트 \\ " exact  '
    config.write_text(json.dumps(_config(False, extra_ssids=[special])))
    run_dir = tmp_path / "run"
    latch_dir = tmp_path / "latches"
    latch_dir.mkdir()
    fake_bin = _snapshot_fault_bin(tmp_path)
    result = _run_snapshot_ensure(
        config,
        run_dir,
        latch_dir,
        fake_bin,
        tmp_path / "fault-state",
        "",
    )
    assert result.returncode == 0, result.stderr
    policy = json.loads((run_dir / "mlan0.roam-policy.json").read_text())
    assert policy["generate_network_blocks"] is False
    assert policy["extra_ssids"] == [special]


def test_mode_b_current_manual_candidate_survives_restart_and_reboot(
    tmp_path: Path,
) -> None:
    config = tmp_path / "wifi_init_conf.json"
    config.write_text(json.dumps(_config(True, extra_ssids=["Base"])))
    run_dir = tmp_path / "run"
    latch_dir = tmp_path / "latches"
    latch_dir.mkdir()
    fake_bin = _snapshot_fault_bin(tmp_path)
    fault_state = tmp_path / "fault-state"

    first = _run_snapshot_ensure(
        config, run_dir, latch_dir, fake_bin, fault_state, ""
    )
    assert first.returncode == 0, first.stderr
    existing = _run_snapshot_ensure(
        config, run_dir, latch_dir, fake_bin, fault_state, ""
    )
    assert existing.returncode == 0, existing.stderr

    (run_dir / "mlan0.roam-policy.json").unlink()
    (latch_dir / ".mlan0.roam-policy.latched").unlink()
    reboot = _run_snapshot_ensure(
        config, run_dir, latch_dir, fake_bin, fault_state, ""
    )
    assert reboot.returncode == 0, reboot.stderr


@pytest.mark.parametrize("policy_source", ["snapshot", "live-json"])
def test_mode_b_extra_block_sync_accepts_current_manual_candidate(
    tmp_path: Path, policy_source: str
) -> None:
    config = tmp_path / "wifi_init_conf.json"
    config.write_text(json.dumps(_config(True, extra_ssids=["Base"])))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    latch_dir = tmp_path / "latches"
    latch_dir.mkdir()
    wpa_dir = tmp_path / "wpa"
    wpa_dir.mkdir()
    conf = wpa_dir / "wpa_supplicant-mlan0.conf"
    original = 'freq_list=5180\nnetwork={\n    ssid="Base"\n}\n'
    conf.write_text(original)

    if policy_source == "snapshot":
        (run_dir / "mlan0.roam-policy.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "iface": "mlan0",
                    "roaming_enabled": True,
                    "bgscan_enabled": True,
                    "generate_network_blocks": False,
                    "extra_ssids": ["Base"],
                }
            )
        )
        (latch_dir / ".mlan0.roam-policy.latched").write_text("1\n")

    result = subprocess.run(
        [
            "bash",
            "-c",
            '. "$1"; wifi_init_sync_extra_ssid_blocks mlan0 "$2"',
            "_",
            str(LIB),
            str(conf),
        ],
        env=os.environ
        | {
            "WIFI_INIT_CONF_JSON": str(config),
            "WIFI_RUN_DIR": str(run_dir),
            "WIFI_ROAM_POLICY_LATCH_DIR": str(latch_dir),
            "WPA_CONF_DIR": str(wpa_dir),
        },
        text=True,
        capture_output=True,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr
    assert conf.read_text() == original


@pytest.mark.parametrize(
    "extras",
    [
        [""],
        ["bad\nname"],
        ["bad\x7fname"],
        ["dup", "dup"],
        ["Base"],
        ["가" * 11],
        [7],
    ],
)
def test_snapshot_rejects_invalid_or_duplicate_ssid_list_before_latching(
    tmp_path: Path, extras
) -> None:
    config = tmp_path / "wifi_init_conf.json"
    config.write_text(
        json.dumps(_config(True, generate_network_blocks=True, extra_ssids=extras))
    )
    run_dir = tmp_path / "run"
    latch_dir = tmp_path / "latches"
    latch_dir.mkdir()
    fake_bin = _snapshot_fault_bin(tmp_path)
    result = _run_snapshot_ensure(
        config,
        run_dir,
        latch_dir,
        fake_bin,
        tmp_path / "fault-state",
        "",
    )
    assert result.returncode != 0
    assert not (run_dir / "mlan0.roam-policy.json").exists()
    assert not (latch_dir / ".mlan0.roam-policy.latched").exists()


def test_periodic_owner_is_stopped_even_when_already_disabled(tmp_path: Path) -> None:
    config = tmp_path / "wifi_init_conf.json"
    config.write_text(json.dumps(_config(False)))
    calls = tmp_path / "systemctl.calls"
    calls.write_text("")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_exe(
        fake_bin / "systemctl",
        """#!/bin/sh
printf '%s\n' "$*" >> "$FAKE_SYSTEMCTL_CALLS"
if [ "$1" = "is-enabled" ]; then exit 1; fi
exit 0
""",
    )
    _write_exe(fake_bin / "logger", "#!/bin/sh\nexit 0\n")
    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "WIFI_INIT_CONF_JSON": str(config),
        "WIFI_RUN_DIR": str(tmp_path / "run"),
        "FAKE_SYSTEMCTL_CALLS": str(calls),
    }
    result = subprocess.run(
        ["bash", str(APPLY)], env=env, text=True, capture_output=True, timeout=5
    )
    assert result.returncode == 0, result.stderr
    assert "stop wifi_periodic_roam@mlan0.service" in calls.read_text().splitlines()


def test_disallowed_owner_stop_failure_is_fatal_even_without_strict(tmp_path: Path) -> None:
    config = tmp_path / "wifi_init_conf.json"
    config.write_text(json.dumps(_config(False)))
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_exe(
        fake_bin / "systemctl",
        """#!/bin/sh
if [ "$1" = "is-enabled" ]; then exit 1; fi
if [ "$1" = "stop" ] && [ "$2" = "wifi_roam@mlan0.service" ]; then exit 1; fi
exit 0
""",
    )
    _write_exe(fake_bin / "logger", "#!/bin/sh\nexit 0\n")
    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "WIFI_INIT_CONF_JSON": str(config),
        "WIFI_RUN_DIR": str(tmp_path / "run"),
        "WIFI_APPLY_STRICT": "0",
    }
    result = subprocess.run(
        ["bash", str(APPLY)], env=env, text=True, capture_output=True, timeout=5
    )
    assert result.returncode != 0


@pytest.mark.parametrize("disable_reply", ["fail", "still-enabled"])
def test_disallowed_owner_disable_or_postcheck_failure_is_fatal_without_strict(
    tmp_path: Path, disable_reply: str
) -> None:
    config = tmp_path / "wifi_init_conf.json"
    config.write_text(json.dumps(_config(False)))
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_exe(
        fake_bin / "systemctl",
        """#!/bin/sh
if [ "$1" = "is-enabled" ] && [ "$3" = "wifi_periodic_roam@mlan0.service" ]; then
  exit 0
fi
if [ "$1" = "disable" ] && [ "$2" = "wifi_periodic_roam@mlan0.service" ]; then
  [ "$DISABLE_REPLY" = "fail" ] && exit 1
  exit 0
fi
if [ "$1" = "is-enabled" ]; then exit 1; fi
exit 0
""",
    )
    _write_exe(fake_bin / "logger", "#!/bin/sh\nexit 0\n")
    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "WIFI_INIT_CONF_JSON": str(config),
        "WIFI_RUN_DIR": str(tmp_path / "run"),
        "WIFI_APPLY_STRICT": "0",
        "DISABLE_REPLY": disable_reply,
    }
    result = subprocess.run(
        ["bash", str(APPLY)], env=env, text=True, capture_output=True, timeout=5
    )
    assert result.returncode != 0


def test_mfg_owner_stop_is_not_skipped_when_systemctl_cat_fails(
    tmp_path: Path,
) -> None:
    """Package-owned owner unit은 `systemctl cat` 진단 실패와 무관하게 stop한다."""
    data = _config(False)
    data["global"]["MOD_PARA"] = "test-mfg.conf"
    config = tmp_path / "wifi_init_conf.json"
    config.write_text(json.dumps(data))
    calls = tmp_path / "systemctl.calls"
    calls.write_text("")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_exe(
        fake_bin / "systemctl",
        """#!/bin/sh
printf '%s\n' "$*" >> "$FAKE_SYSTEMCTL_CALLS"
if [ "$1" = "is-enabled" ]; then exit 1; fi
if [ "$1" = "cat" ] && [ "$2" = "wifi_roam@mlan0.service" ]; then exit 1; fi
exit 0
""",
    )
    _write_exe(
        fake_bin / "grep",
        """#!/bin/sh
case "$*" in
  *"/lib/firmware/test-mfg.conf"*) printf 'mfg_mode=1\n'; exit 0 ;;
esac
exec /bin/grep "$@"
""",
    )
    _write_exe(fake_bin / "logger", "#!/bin/sh\nexit 0\n")
    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "WIFI_INIT_CONF_JSON": str(config),
        "WIFI_RUN_DIR": str(tmp_path / "run"),
        "WIFI_APPLY_STRICT": "0",
        "FAKE_SYSTEMCTL_CALLS": str(calls),
    }

    result = subprocess.run(
        ["bash", str(APPLY)], env=env, text=True, capture_output=True, timeout=5
    )

    assert result.returncode == 0, result.stderr
    assert "stop wifi_roam@mlan0.service" in calls.read_text().splitlines()


def test_deleted_boot_snapshot_is_not_recreated_from_mutated_json(tmp_path: Path) -> None:
    config = tmp_path / "wifi_init_conf.json"
    config.write_text(json.dumps(_config(True, generate_network_blocks=True)))
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_exe(
        fake_bin / "systemctl",
        "#!/bin/sh\nif [ \"$1\" = is-enabled ]; then exit 1; fi\nexit 0\n",
    )
    _write_exe(fake_bin / "logger", "#!/bin/sh\nexit 0\n")
    run_dir = tmp_path / "run"
    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "WIFI_INIT_CONF_JSON": str(config),
        "WIFI_RUN_DIR": str(run_dir),
    }

    first = subprocess.run(
        ["bash", str(APPLY)], env=env, text=True, capture_output=True, timeout=5
    )
    assert first.returncode == 0, first.stderr
    snapshot = run_dir / "mlan0.roam-policy.json"
    latch = tmp_path / ".mlan0.roam-policy.latched"
    assert snapshot.exists()
    assert latch.exists()

    snapshot.unlink()
    config.write_text(json.dumps(_config(False, bgscan_enabled=False)))
    second = subprocess.run(
        ["bash", str(APPLY)], env=env, text=True, capture_output=True, timeout=5
    )
    assert second.returncode != 0
    assert not snapshot.exists()


def test_valid_policy_without_tombstone_is_not_repaired(tmp_path: Path) -> None:
    """Policy-first crash state is untrusted; it must never be promoted to latched."""
    config = tmp_path / "wifi_init_conf.json"
    config.write_text(json.dumps(_config(False, bgscan_enabled=False)))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    policy = run_dir / "mlan0.roam-policy.json"
    policy.write_text(
        json.dumps(
            {
                "version": 1,
                "iface": "mlan0",
                "roaming_enabled": True,
                "bgscan_enabled": True,
                "generate_network_blocks": True,
                "extra_ssids": ["Old"],
            }
        )
    )
    fake_bin = _snapshot_fault_bin(tmp_path)
    result = _run_snapshot_ensure(
        config,
        run_dir,
        tmp_path,
        fake_bin,
        tmp_path / "fault-state",
        "",
    )

    assert result.returncode != 0
    assert not (tmp_path / ".mlan0.roam-policy.latched").exists()
    assert json.loads(policy.read_text())["roaming_enabled"] is True


@pytest.mark.parametrize(
    "fault_point",
    [
        "policy-render",
        "policy-chmod",
        "policy-stage-sync",
        "policy-stage-validate",
        "latch-chmod",
        "latch-stage-sync",
        "latch-rename",
    ],
)
def test_snapshot_fault_before_tombstone_publishes_neither_file_and_can_retry(
    tmp_path: Path, fault_point: str
) -> None:
    """Every pre-commit staging boundary fails before publishing either name."""
    config = tmp_path / "wifi_init_conf.json"
    config.write_text(
        json.dumps(_config(True, generate_network_blocks=True, extra_ssids=["First"]))
    )
    run_dir = tmp_path / "run"
    latch_dir = tmp_path / "latches"
    latch_dir.mkdir()
    fake_bin = _snapshot_fault_bin(tmp_path)
    state = tmp_path / "fault-state"
    first = _run_snapshot_ensure(
        config, run_dir, latch_dir, fake_bin, state, fault_point
    )
    policy = run_dir / "mlan0.roam-policy.json"
    latch = latch_dir / ".mlan0.roam-policy.latched"

    assert first.returncode != 0, fault_point
    assert not policy.exists(), fault_point
    assert not latch.exists(), fault_point

    config.write_text(
        json.dumps(_config(False, bgscan_enabled=False, extra_ssids=["Changed"]))
    )
    retry = _run_snapshot_ensure(config, run_dir, latch_dir, fake_bin, state, "")
    assert retry.returncode == 0, retry.stderr
    assert latch.exists()
    assert json.loads(policy.read_text())["roaming_enabled"] is False


@pytest.mark.parametrize(
    "fault_point",
    [
        "latch-installed-sync",
        "latch-dir-sync",
        "policy-rename",
        "policy-installed-sync",
        "policy-dir-sync",
    ],
)
def test_snapshot_fault_after_tombstone_never_reconstructs_from_changed_json(
    tmp_path: Path, fault_point: str
) -> None:
    """Once the durable commit tombstone is published, missing policy is fail-closed."""
    config = tmp_path / "wifi_init_conf.json"
    config.write_text(
        json.dumps(_config(True, generate_network_blocks=True, extra_ssids=["First"]))
    )
    run_dir = tmp_path / "run"
    latch_dir = tmp_path / "latches"
    latch_dir.mkdir()
    fake_bin = _snapshot_fault_bin(tmp_path)
    state = tmp_path / "fault-state"
    first = _run_snapshot_ensure(
        config, run_dir, latch_dir, fake_bin, state, fault_point
    )
    policy = run_dir / "mlan0.roam-policy.json"
    latch = latch_dir / ".mlan0.roam-policy.latched"

    assert first.returncode != 0, fault_point
    assert latch.exists(), fault_point
    policy.unlink(missing_ok=True)
    config.write_text(
        json.dumps(_config(False, bgscan_enabled=False, extra_ssids=["Changed"]))
    )
    retry = _run_snapshot_ensure(config, run_dir, latch_dir, fake_bin, state, "")

    assert retry.returncode != 0
    assert not policy.exists()
    assert latch.exists()


def test_owner_units_fail_closed_on_stale_queued_start() -> None:
    periodic = (SYSTEMD / "wifi_periodic_roam@.service").read_text()
    roam = (SYSTEMD / "wifi_roam@.service").read_text()
    bgscan = (SYSTEMD / "wifi_bgscan@.service").read_text()
    assert "ExecCondition=/bin/false" in periodic
    assert "RestartPreventExitStatus=3" in roam
    assert "RestartPreventExitStatus=3" in bgscan
