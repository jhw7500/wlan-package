import sys
import os
from unittest.mock import MagicMock

# sUTILS has heavy runtime deps (paho, serial, numpy); stub it before importing wifi_bgscan.
sys.modules.setdefault("sUTILS", MagicMock())

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wifi_bgscan
from wifi_bgscan import construct_iw_scan_cmd

import pytest

wifi_bgscan.IFACE = "mlan0"


def _ssid_tokens(cmd):
    return [cmd[i + 1] for i in range(len(cmd)) if cmd[i] == "ssid"]


@pytest.mark.parametrize("ssid,ssid_filter,extra_ssids,expected", [
    # (1) ssid_filter=True, no extras → current SSID only
    ("HomeNet", True, None, ["HomeNet"]),
    # (2) ssid_filter=True, with extras → current + extras
    ("HomeNet", True, ["OfficeNet"], ["HomeNet", "OfficeNet"]),
    # (3) ssid_filter=False, no extras → undirected scan (no ssid tokens)
    ("HomeNet", False, None, []),
    # (4) ssid_filter=False, with extras → wildcard "" first, then extras
    #     (preserves broad scan intent while directed-probing hidden extra SSIDs)
    ("HomeNet", False, ["OfficeNet"], ["", "OfficeNet"]),
    # (5) dedup: extra_ssids contains current SSID → no duplicate ssid token
    ("HomeNet", True, ["HomeNet", "OfficeNet"], ["HomeNet", "OfficeNet"]),
])
def test_ssid_probe_tokens(ssid, ssid_filter, extra_ssids, expected):
    cmd = construct_iw_scan_cmd(ssid, [], ssid_filter=ssid_filter, freq_filter=False, extra_ssids=extra_ssids)
    assert _ssid_tokens(cmd) == expected


def test_freq_filter_true_adds_freq_tokens():
    cmd = construct_iw_scan_cmd("HomeNet", ["2412", "5180"], ssid_filter=True, freq_filter=True)
    assert "freq" in cmd
    assert "2412" in cmd and "5180" in cmd


def test_freq_filter_false_omits_freq_tokens():
    cmd = construct_iw_scan_cmd("HomeNet", ["2412", "5180"], ssid_filter=True, freq_filter=False)
    assert "freq" not in cmd


def test_cmd_prefix():
    cmd = construct_iw_scan_cmd("HomeNet", [])
    assert cmd[:3] == ["iw", "mlan0", "scan"]
