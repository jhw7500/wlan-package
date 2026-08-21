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

_CANONICAL_MULTI_BLOCK = """\
update_config=0
freq_list=5180 5200
network={
    ssid="cantops"
    freq_list=2412
    key_mgmt=WPA-PSK
    psk="secret"
}
#!TH_CONNECT=-80
""" + _SENTINEL_BEGIN + """
network={
    ssid="OfficeA"
    freq_list=2437
    key_mgmt=WPA-PSK
    psk="secret"
}
""" + _SENTINEL_END + "\n"


def _write(tmp_path, text):
    p = tmp_path / "wpa.conf"
    p.write_text(text)
    return str(p)


def test_single_block_parses_base_ssid(tmp_path):
    # 센티넬 없음(단일블록) → 기존 동작 그대로.
    # th2g/th5g 는 conf 마커(-70/-72)가 아니라 모듈 기본값(-75) — 인자 미지정이므로.
    path = _write(tmp_path, _SINGLE_BLOCK)
    ssid, freqs, th2g, th5g, th_connect = parse_supplicant_conf(path)
    assert ssid == "cantops"
    assert freqs == ["2412", "5180"]
    assert th2g == wifi_roam.DEFAULT_TH_2G
    assert th5g == wifi_roam.DEFAULT_TH_5G
    assert th_connect == -80


def test_multi_block_returns_base_ssid_not_last_extra(tmp_path):
    # #1 회귀: 센티넬 이전(기본) 블록만 파싱 → 마지막 extra(OfficeB)가 아니라 cantops
    path = _write(tmp_path, _MULTI_BLOCK)
    ssid, freqs, th2g, th5g, th_connect = parse_supplicant_conf(path)
    assert ssid == "cantops"
    # scan_freq 는 센티넬 전(기본) 값이어야 함 (extra 값으로 오염되지 않음)
    assert freqs == ["2412", "5180"]
    assert th2g == wifi_roam.DEFAULT_TH_2G
    assert th5g == wifi_roam.DEFAULT_TH_5G
    # TH_CONNECT 는 여전히 conf 에서 읽는다 — extra 블록 값(-65)이 아니라 기본 블록 값
    assert th_connect == -80


def test_multi_block_ignores_extra_th_connect(tmp_path):
    # extra 블록의 TH_CONNECT(-65)가 기본 블록 값(-80)을 덮어쓰지 않음
    path = _write(tmp_path, _MULTI_BLOCK)
    _, _, _, _, th_connect = parse_supplicant_conf(path)
    assert th_connect == -80


def test_canonical_global_freq_list_is_roam_scan_source(tmp_path):
    """전역 목록이 block별 값과 충돌해도 common global 값만 사용한다."""
    path = _write(tmp_path, _CANONICAL_MULTI_BLOCK)
    ssid, freqs, _, _, th_connect = parse_supplicant_conf(path)
    assert ssid == "cantops"
    assert freqs == ["5180", "5200"]
    assert th_connect == -80


def test_legacy_base_freq_list_precedes_scan_freq_until_boot_migration(tmp_path):
    path = _write(
        tmp_path,
        """\
network={
    ssid="cantops"
    freq_list=2412 2437
    scan_freq=5180
}
""",
    )
    assert parse_supplicant_conf(path)[1] == ["2412", "2437"]


# ── 로밍 임계 소스 단일화 (conf `#!TH_2G=`/`#!TH_5G=` 마커 경로 제거) ──
# 종전에는 conf 마커가 JSON 을 덮어써, `wifi <if> roam th` 로 값을 바꿔도 마커가 남아 있으면
# 조용히 무시됐다. 마커를 생성하는 코드는 dist 에 없고 출하 conf 에도 없어 레거시·수동 편집
# 파일에서만 발현하는 함정이었다.


def test_conf_th_markers_do_not_override_json(tmp_path):
    """[핵심] conf 에 마커가 있어도 JSON 인자가 이긴다."""
    path = _write(tmp_path, _SINGLE_BLOCK)  # 마커 -70/-72 포함
    _, _, th2g, th5g, _ = parse_supplicant_conf(path, def_th2g=-66, def_th5g=-68)
    assert (th2g, th5g) == (-66, -68), "conf 마커가 JSON 값을 덮어썼다(회귀)"


def test_conf_th_markers_ignored_without_json(tmp_path):
    """JSON 인자가 없으면 마커가 아니라 모듈 기본값으로 떨어진다."""
    path = _write(tmp_path, _SINGLE_BLOCK)
    _, _, th2g, th5g, _ = parse_supplicant_conf(path)
    assert (th2g, th5g) == (wifi_roam.DEFAULT_TH_2G, wifi_roam.DEFAULT_TH_5G)


def test_parse_thresholds_removed():
    """dead code 였던 parse_thresholds 가 제거된 상태를 고정한다(호출부 0건이었음)."""
    assert not hasattr(wifi_roam, "parse_thresholds")
