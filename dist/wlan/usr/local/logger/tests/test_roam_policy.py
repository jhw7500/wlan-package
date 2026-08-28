import json
import os
import sys

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import roam_policy  # noqa: E402
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


@pytest.mark.parametrize(
    "ssid",
    ["", "bad\nname", "bad\rname", "bad\tname", "bad\x00name", "bad\x1fname", "bad\x7fname", "가" * 11],
)
def test_shared_ssid_contract_rejects_empty_controls_and_over_32_bytes(ssid):
    with pytest.raises(RoamPolicyError):
        roam_policy.validate_ssid(ssid)


@pytest.mark.parametrize(
    "ssid",
    ["a", " leading", "trailing ", 'a\\b"c', "게스트", "가" * 10 + "ab"],
)
def test_shared_ssid_contract_preserves_valid_utf8_through_32_bytes(ssid):
    assert roam_policy.validate_ssid(ssid) == ssid


def test_shared_ssid_list_rejects_duplicates_and_base_identity():
    with pytest.raises(RoamPolicyError):
        roam_policy.validate_ssid_list(["Office", "Office"])
    with pytest.raises(RoamPolicyError):
        roam_policy.validate_ssid_list(["Base", "Office"], base_ssid="Base")


def test_hex_and_existing_quoted_wpa_ssid_values_round_trip_exactly():
    ssid = '  게스트 \\ " exact  '
    encoded = ssid.encode("utf-8").hex()
    assert roam_policy.parse_wpa_ssid_value(encoded) == ssid
    assert roam_policy.parse_wpa_ssid_value(f'"{ssid}"') == ssid


@pytest.mark.parametrize(
    "extras",
    [
        ["Office", "Office"],
        [""],
        ["bad\x7f"],
        ["가" * 11],
    ],
)
def test_boot_policy_rejects_invalid_or_duplicate_ssid_identities(tmp_path, extras):
    _write_policy(tmp_path, extra_ssids=extras)
    with pytest.raises(RoamPolicyError):
        load_boot_roam_policy("mlan0", run_dir=str(tmp_path))


def _wpa_printf_encode(value):
    out = []
    for byte in value.encode("utf-8"):
        if byte == 0x22:
            out.append(r'\"')
        elif byte == 0x5C:
            out.append(r'\\')
        elif 0x20 <= byte <= 0x7E:
            out.append(chr(byte))
        else:
            out.append(f"\\x{byte:02x}")
    return "".join(out)


def test_wpa_ctrl_printf_encoded_ssid_round_trips_to_exact_utf8_identity():
    ssid = '  게스트 \\ " exact  '
    encoded = _wpa_printf_encode(ssid)
    assert encoded != ssid
    assert roam_policy.decode_wpa_ssid_text(encoded) == ssid
