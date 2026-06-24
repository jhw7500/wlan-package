import sys
import os
from unittest.mock import MagicMock

# sUTILS has heavy runtime deps; stub before importing wifi_roam.
sys.modules.setdefault("sUTILS", MagicMock())

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wifi_roam
from wifi_roam import parse_supplicant_conf

import pytest

# logger is assigned at runtime; stub so error paths can call it.
wifi_roam.logger = MagicMock()

# 자동생성 센티넬(wifi_init_config_lib.sh 와 동일 prefix)
_SENTINEL_BEGIN = "# >>> wifi_extra_ssid auto-generated (do not edit) >>>"
_SENTINEL_END = "# <<< wifi_extra_ssid auto-generated <<<"

_SINGLE_BLOCK = """\
network={
\tssid="cantops"
\tscan_freq=2412 5180
\tkey_mgmt=WPA-PSK
\tpsk="secret"
}
#!TH_2G=-70
#!TH_5G=-72
#!TH_CONNECT=-80
"""

# 다중블록: 기본(cantops) network 블록 + 센티넬 사이 extra(OfficeA/OfficeB) 블록.
# extra 블록의 ssid=/scan_freq=/TH 가 기본값을 덮어쓰면 안 됨.
_MULTI_BLOCK = """\
network={
\tssid="cantops"
\tscan_freq=2412 5180
\tkey_mgmt=WPA-PSK
\tpsk="secret"
}
#!TH_2G=-70
#!TH_5G=-72
#!TH_CONNECT=-80
""" + _SENTINEL_BEGIN + """
network={
\tssid="OfficeA"
\tscan_freq=2437
\tkey_mgmt=WPA-PSK
\tpsk="aaa"
}
#!TH_2G=-60
#!TH_5G=-61
#!TH_CONNECT=-65
network={
\tssid="OfficeB"
\tscan_freq=5200
\tkey_mgmt=WPA-PSK
\tpsk="bbb"
}
""" + _SENTINEL_END + "\n"


def _write(tmp_path, text):
    p = tmp_path / "wpa.conf"
    p.write_text(text)
    return str(p)


def test_single_block_parses_base_ssid(tmp_path):
    # 센티넬 없음(단일블록) → 기존 동작 그대로
    path = _write(tmp_path, _SINGLE_BLOCK)
    ssid, freqs, th2g, th5g, th_connect = parse_supplicant_conf(path)
    assert ssid == "cantops"
    assert freqs == ["2412", "5180"]
    assert th2g == -70
    assert th5g == -72
    assert th_connect == -80


def test_multi_block_returns_base_ssid_not_last_extra(tmp_path):
    # #1 회귀: 센티넬 이전(기본) 블록만 파싱 → 마지막 extra(OfficeB)가 아니라 cantops
    path = _write(tmp_path, _MULTI_BLOCK)
    ssid, freqs, th2g, th5g, th_connect = parse_supplicant_conf(path)
    assert ssid == "cantops"
    # scan_freq / TH 도 센티넬 전(기본) 값이어야 함 (extra 값으로 오염되지 않음)
    assert freqs == ["2412", "5180"]
    assert th2g == -70
    assert th5g == -72
    assert th_connect == -80


def test_multi_block_ignores_extra_th_values(tmp_path):
    # extra 블록의 TH(-60/-61/-65)가 기본 TH(-70/-72/-80)를 덮어쓰지 않음
    path = _write(tmp_path, _MULTI_BLOCK)
    _, _, th2g, th5g, th_connect = parse_supplicant_conf(path)
    assert (th2g, th5g, th_connect) == (-70, -72, -80)
