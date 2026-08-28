import os
import sys
from unittest.mock import MagicMock


sys.modules.setdefault("sUTILS", MagicMock())
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import wifi_bgscan
from wifi_bgscan import parse_wpa_supplicant_conf
import pytest


wifi_bgscan.logger = MagicMock()


def _write(tmp_path, text):
    path = tmp_path / "wpa.conf"
    path.write_text(text)
    return str(path)


def test_parser_separates_global_frequency_and_all_network_ssids(tmp_path):
    path = _write(
        tmp_path,
        """\
update_config=0
freq_list=5180 5200
network={
    ssid="Base"
    freq_list=2412
}
network={
    ssid="Office"
    freq_list=2437
}
#!INTERVAL=45
""",
    )

    base, ssids, freqs, interval = parse_wpa_supplicant_conf(path)

    assert base == "Base"
    assert ssids == ["Base", "Office"]
    assert freqs == ["5180", "5200"]
    assert interval == 45


def test_parser_preserves_unique_ssids_and_ignores_commented_settings(tmp_path):
    path = _write(
        tmp_path,
        """\
# freq_list=2412
freq_list=5180 5200 # common list
network={
    # ssid="Ignored"
    ssid="Base"
}
network={
    ssid="Office"
}
network={
    ssid="Guest"
}
""",
    )

    base, ssids, freqs, interval = parse_wpa_supplicant_conf(path)

    assert base == "Base"
    assert ssids == ["Base", "Office", "Guest"]
    assert freqs == ["5180", "5200"]
    assert interval == wifi_bgscan.DEFAULT_INTERVAL


def test_parser_keeps_legacy_base_frequency_precedence_until_boot_migration(tmp_path):
    base_list = _write(
        tmp_path,
        """\
network={
    ssid="Base"
    freq_list=2412 2437
    scan_freq=5180
}
network={
    ssid="Office"
    freq_list=5200
}
""",
    )
    assert parse_wpa_supplicant_conf(base_list)[2] == ["2412", "2437"]

    scan_only = tmp_path / "scan-only.conf"
    scan_only.write_text(
        """\
network={
    ssid="Base"
    scan_freq=5220 5240
}
"""
    )
    assert parse_wpa_supplicant_conf(str(scan_only))[2] == ["5220", "5240"]


def test_parser_decodes_hex_ssids_and_preserves_spaces_quotes_backslashes(tmp_path):
    base = '  게스트 \\ " exact  '
    office = " Office "
    path = _write(
        tmp_path,
        f"""\
network={{
    ssid={base.encode('utf-8').hex()}
}}
network={{
    ssid={office.encode('utf-8').hex()}
}}
""",
    )
    parsed_base, ssids, _, _ = parse_wpa_supplicant_conf(path)
    assert parsed_base == base
    assert ssids == [base, office]


def test_parser_rejects_duplicate_network_ssid_identity(tmp_path):
    path = _write(
        tmp_path,
        """\
network={
    ssid=42617365
}
network={
    ssid="Base"
}
""",
    )
    with pytest.raises(ValueError, match="duplicate"):
        parse_wpa_supplicant_conf(path)
