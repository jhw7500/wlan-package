import json
import os
import sys

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from roam_policy import (  # noqa: E402
    RoamPolicyError,
    load_boot_roam_policy,
    scan_backend_for_policy,
)


def _write_policy(tmp_path, **overrides):
    policy = {
        "version": 1,
        "iface": "mlan0",
        "roaming_enabled": True,
        "bgscan_enabled": True,
        "generate_network_blocks": True,
        "extra_ssids": ["Office", "Guest"],
    }
    policy.update(overrides)
    path = tmp_path / "mlan0.roam-policy.json"
    path.write_text(json.dumps(policy))
    return path


def test_loads_strict_boot_policy_and_derives_backend(tmp_path):
    _write_policy(tmp_path)
    policy = load_boot_roam_policy("mlan0", run_dir=str(tmp_path))
    assert policy["generate_network_blocks"] is True
    assert policy["extra_ssids"] == ["Office", "Guest"]
    assert scan_backend_for_policy(policy) == "iw"


def test_disabled_external_owner_derives_wpa_backend(tmp_path):
    _write_policy(tmp_path, roaming_enabled=False)
    policy = load_boot_roam_policy("mlan0", run_dir=str(tmp_path))
    assert scan_backend_for_policy(policy) == "wpa_cli"


@pytest.mark.parametrize(
    "overrides",
    [
        {"version": 2},
        {"iface": "mlan1"},
        {"roaming_enabled": "true"},
        {"bgscan_enabled": 1},
        {"generate_network_blocks": None},
        {"extra_ssids": "Office"},
        {"extra_ssids": ["Office", 7]},
    ],
)
def test_rejects_invalid_or_cross_interface_snapshot(tmp_path, overrides):
    _write_policy(tmp_path, **overrides)
    with pytest.raises(RoamPolicyError):
        load_boot_roam_policy("mlan0", run_dir=str(tmp_path))


def test_missing_snapshot_is_fail_closed(tmp_path):
    with pytest.raises(RoamPolicyError):
        load_boot_roam_policy("mlan0", run_dir=str(tmp_path))
