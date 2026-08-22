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
wifi_bgscan.logger = MagicMock()   # logger 는 __main__ 에서만 생성 — cap 경고 경로용 mock


def _ssid_tokens(cmd):
    """iw 문법상 ssid 그룹은 **종단**(`[ssid <ssid>*|passive]`) — `ssid` 키워드 1회 뒤의
    모든 토큰이 SSID 값이다. 키워드를 반복하면 iw 5.19 파서(SSID 상태에서 키워드 복귀
    없음)가 두 번째 `ssid` 를 리터럴 SSID 로 소비해, probe 대상이 2N-1 개로 불어나고
    존재하지 않는 \"ssid\" 네트워크 directed probe 가 전파로 나간다."""
    assert cmd.count("ssid") <= 1, f"ssid 키워드 반복 금지(iw 가 리터럴 'ssid' 를 probe): {cmd}"
    if "ssid" not in cmd:
        return []
    return cmd[cmd.index("ssid") + 1:]


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
    # (5) ssid=None (conf missing / link.json absent), ssid_filter=True, with extras
    #     → only extras probed (no None token leaks in)
    (None, True, ["OfficeNet"], ["OfficeNet"]),
    # (6) ssid=None, ssid_filter=False, with extras → wildcard still inserted
    (None, False, ["OfficeNet"], ["", "OfficeNet"]),
    # (7) ssid_filter=False, extra_ssids=[] (empty, not None) → no spurious "" wildcard
    ("HomeNet", False, [], []),
])
def test_ssid_probe_tokens(ssid, ssid_filter, extra_ssids, expected):
    cmd = construct_iw_scan_cmd(ssid, [], ssid_filter=ssid_filter, freq_filter=False, extra_ssids=extra_ssids)
    assert _ssid_tokens(cmd) == expected


def test_freq_filter_true_adds_freq_tokens():
    cmd = construct_iw_scan_cmd("HomeNet", ["2412", "5180"], ssid_filter=True, freq_filter=True)
    assert "freq" in cmd
    assert "2412" in cmd and "5180" in cmd


@pytest.mark.parametrize("extras", [["HomeNet"], ["Office", "Office"]])
def test_iw_scan_rejects_base_or_duplicate_ssid_identity(extras):
    with pytest.raises(wifi_bgscan.RoamPolicyError):
        construct_iw_scan_cmd(
            "HomeNet", [], ssid_filter=True, freq_filter=False, extra_ssids=extras
        )


def test_freq_filter_false_omits_freq_tokens():
    cmd = construct_iw_scan_cmd("HomeNet", ["2412", "5180"], ssid_filter=True, freq_filter=False)
    assert "freq" not in cmd


def test_cmd_prefix():
    cmd = construct_iw_scan_cmd("HomeNet", [])
    assert cmd[:3] == ["iw", "mlan0", "scan"]


# --- passive scan mode ---

def test_passive_adds_keyword_and_drops_ssid_probes():
    # 패시브: probe를 안 쏘므로 ssid 토큰이 전부 빠지고 'passive' 키워드가 붙는다.
    cmd = construct_iw_scan_cmd(
        "HomeNet", ["2412", "5180"], ssid_filter=True,
        freq_filter=True, extra_ssids=["OfficeNet"], passive=True,
    )
    assert cmd[:3] == ["iw", "mlan0", "scan"]
    assert _ssid_tokens(cmd) == []          # directed probe 없음
    assert "freq" in cmd and "2412" in cmd and "5180" in cmd  # freq 스코프는 유지
    # [회귀] iw 5.19 문법 `scan [freq <freq>*] ... [ssid <ssid>*|passive]` — passive는
    # 맨 뒤 그룹이라 freq 뒤에 와야 한다. 앞에 두면 iw가 rc=1로 즉시 실패해 스캔이 아예
    # 안 돈다(온타겟 실측). freq_filter=true가 기본이라 이 순서가 곧 기능 여부를 가른다.
    assert cmd.index("passive") > cmd.index("freq"), f"passive는 freq 뒤여야 함: {cmd}"
    assert cmd[-1] == "passive", f"passive는 마지막 토큰이어야 함: {cmd}"


def test_passive_freq_filter_false_omits_freq():
    cmd = construct_iw_scan_cmd("HomeNet", ["2412"], freq_filter=False, passive=True)
    assert cmd == ["iw", "mlan0", "scan", "passive"]   # freq 없으면 passive만
    assert "freq" not in cmd


def test_active_default_has_no_passive_keyword():
    # 기본(passive=False)은 회귀 없이 종전 액티브 스캔.
    cmd = construct_iw_scan_cmd("HomeNet", ["2412"], ssid_filter=True)
    assert "passive" not in cmd
    assert _ssid_tokens(cmd) == ["HomeNet"]


# --- iw 인자 조립 규칙 (ssid 그룹 종단 + 드라이버 SSID 한도) ---

def test_active_freq_before_ssid_and_ssid_group_terminal():
    """[회귀] iw 5.19 문법상 ssid 도 맨 뒤 그룹 — freq 가 ssid 뒤에 오면 iw 가
    'freq','5180' 을 SSID 값으로 소비해 rc=0 인 채 전대역 오스캔이 된다(#120 과 동종
    클래스, 액티브 경로). freq → ssid 순서와 ssid 그룹 종단을 함께 고정한다."""
    cmd = construct_iw_scan_cmd("HomeNet", ["5180"], ssid_filter=True, freq_filter=True)
    assert cmd.index("freq") < cmd.index("ssid"), f"freq 는 ssid 앞이어야 함: {cmd}"
    assert cmd[cmd.index("ssid") + 1:] == ["HomeNet"], f"ssid 그룹은 종단이어야 함: {cmd}"


def test_ssid_probe_cap_at_driver_max():
    """[회귀] nl80211 max scan SSIDs(10, NXP mlan 실측) 초과 시 iw 가 -EINVAL 로 스캔
    **전체**를 실패시킨다 — bgscan 이 매 주기 전량 실패하면 ap.log 배경 캐시가 영영
    갱신되지 않아 로밍 Stage 2 까지 연쇄로 죽는다. cap 으로 전량 실패를 막는다."""
    extras = [f"Net{i}" for i in range(12)]
    cmd = construct_iw_scan_cmd(
        "HomeNet", [], ssid_filter=True, freq_filter=False, extra_ssids=extras
    )
    toks = _ssid_tokens(cmd)
    assert len(toks) == wifi_bgscan.MAX_SCAN_SSIDS
    assert toks[0] == "HomeNet"            # 현재 네트워크 우선 보존


def test_no_dangling_ssid_keyword_without_probe_targets():
    """[회귀] probe 대상이 없으면 `ssid` 키워드 자체가 붙지 않아야 한다 — 값 없는 dangling
    `ssid` 는 iw 인자 파싱 실패로 스캔 전체를 죽인다. _ssid_tokens([])==[] 는 키워드 부재와
    dangling 을 구분하지 못하므로 키워드 부재를 직접 고정한다."""
    cmd = construct_iw_scan_cmd("HomeNet", ["2412"], ssid_filter=False, freq_filter=True)
    assert "ssid" not in cmd


def test_ssid_probe_cap_preserves_wildcard():
    """ssid_filter=False 광범위 스캔 의도의 wildcard("") 는 cap 후에도 보존돼야 한다."""
    extras = [f"Net{i}" for i in range(12)]
    cmd = construct_iw_scan_cmd(
        "HomeNet", [], ssid_filter=False, freq_filter=False, extra_ssids=extras
    )
    toks = _ssid_tokens(cmd)
    assert len(toks) == wifi_bgscan.MAX_SCAN_SSIDS
    assert toks[0] == ""                   # wildcard 슬롯 보존


# --- wpa_cli SCAN backend ---

def _wpa_ssid_hex_tokens(cmd):
    return [cmd[i + 1] for i, token in enumerate(cmd[:-1]) if token == "ssid"]


def test_wpa_passive_scan_uses_exact_common_frequency_list():
    cmd = wifi_bgscan.construct_wpa_scan_cmd(
        "mlan0", "Base", ["5180", "5200"], passive=True
    )
    assert cmd == [
        "wpa_cli", "-i", "mlan0", "scan", "freq=5180,5200", "passive=1"
    ]
    assert "TYPE=ONLY" not in cmd


def test_wpa_active_scan_hex_encodes_each_configured_ssid():
    cmd = wifi_bgscan.construct_wpa_scan_cmd(
        "mlan0",
        "Base",
        ["5180"],
        ssid_filter=True,
        extra_ssids=["Office", "게스트"],
        passive=False,
    )
    assert cmd[:5] == ["wpa_cli", "-i", "mlan0", "scan", "freq=5180"]
    assert _wpa_ssid_hex_tokens(cmd) == [
        "Base".encode("utf-8").hex(),
        "Office".encode("utf-8").hex(),
        "게스트".encode("utf-8").hex(),
    ]
    assert "TYPE=ONLY" not in cmd


def test_wpa_scan_without_common_list_is_unrestricted():
    cmd = wifi_bgscan.construct_wpa_scan_cmd(
        "mlan0", "Base", [], ssid_filter=False, extra_ssids=[], passive=False
    )
    assert cmd == ["wpa_cli", "-i", "mlan0", "scan"]


def test_wpa_scan_caps_unique_directed_ssids():
    extras = [f"Net{i}" for i in range(20)]
    cmd = wifi_bgscan.construct_wpa_scan_cmd(
        "mlan0", "Base", ["2412"], ssid_filter=True, extra_ssids=extras
    )
    ssids = _wpa_ssid_hex_tokens(cmd)
    assert len(ssids) == wifi_bgscan.MAX_SCAN_SSIDS
    assert ssids[0] == "Base".encode().hex()
    assert len(set(ssids)) == len(ssids)


@pytest.mark.parametrize("extras", [["Base"], ["Office", "Office"]])
def test_wpa_scan_rejects_base_or_duplicate_ssid_identity(extras):
    with pytest.raises(wifi_bgscan.RoamPolicyError):
        wifi_bgscan.construct_wpa_scan_cmd(
            "mlan0", "Base", ["2412"], ssid_filter=True, extra_ssids=extras
        )
