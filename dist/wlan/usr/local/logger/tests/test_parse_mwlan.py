"""parse_mwlan_log 의 양포맷(= / 공백) 파싱(_parse_mwlan_text) 단위 테스트.

/proc/mwlan/adapter0/mlan0/log 의 실제 포맷이 'key = value'(diag-9098-11ax.sh:179)
인지 공백정렬(mlanutl getlog 출력)인지 레포 증거로 단정 불가 → 양쪽 모두 파싱해
link.json 의 mwlan_log 를 어느 포맷이든 채운다(SNMP 통계·A안 공통 데이터소스).
"""

import os
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("sUTILS", MagicMock())
sys.modules.setdefault("curses", MagicMock())

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wifi_logger_link as wl  # noqa: E402


# --- '=' 포맷(하위호환) -----------------------------------------------------

def test_eq_single_value():
    assert wl._parse_mwlan_text("dot11RetryCount = 5\n") == {"dot11RetryCount": 5}


def test_eq_list_value():
    assert wl._parse_mwlan_text("dot11QosTransmittedFrameCount = 1 2 3 4\n") == {
        "dot11QosTransmittedFrameCount": [1, 2, 3, 4]
    }


# --- 공백정렬 포맷(mlanutl getlog 스타일) -----------------------------------

def test_ws_single_value():
    assert wl._parse_mwlan_text("dot11RetryCount                    5\n") == {
        "dot11RetryCount": 5
    }


def test_ws_list_value():
    assert wl._parse_mwlan_text("dot11QosRetryCount   1 2 3 4 5 6 7 8\n") == {
        "dot11QosRetryCount": [1, 2, 3, 4, 5, 6, 7, 8]
    }


def test_ws_multiple_lines_real_keys():
    text = ("dot11TransmittedFrameCount        100\n"
            "dot11GroupTransmittedFrameCount   7\n"
            "dot11FCSErrorCount                2621\n")
    out = wl._parse_mwlan_text(text)
    assert out["dot11TransmittedFrameCount"] == 100
    assert out["dot11GroupTransmittedFrameCount"] == 7
    assert out["dot11FCSErrorCount"] == 2621


# --- 잡음/헤더 라인 무시 ----------------------------------------------------

def test_skips_non_numeric_and_headers():
    text = ("mlanutl mlan0 getlog\n"
            "--------- statistics ---------\n"
            "\n"
            "dot11RetryCount  0\n")
    assert wl._parse_mwlan_text(text) == {"dot11RetryCount": 0}


def test_empty_or_none():
    assert wl._parse_mwlan_text("") == {}
    assert wl._parse_mwlan_text(None) == {}


# --- 견고성: int() 실패 토큰·잡음 값에도 크래시 금지(F1/F4) ------------------

def test_robust_against_malformed_int_tokens():
    # lstrip('-').isdigit() 는 통과하나 int() 가 실패하는 토큰(다중 dash '--7',
    # 유니코드 digit '²')에도 ValueError 없이 해당 줄만 skip (로거 데몬 상주 안정성).
    out = wl._parse_mwlan_text("dot11Bad --7\ndot11Uni ²\ndot11Ok 5\n")
    assert out == {"dot11Ok": 5}


def test_skips_lines_with_nonnumeric_value_tokens():
    # 값 토큰에 비숫자가 섞인 헤더/요약 줄은 통째 skip → 잡키 오염 방지(F4)
    out = wl._parse_mwlan_text("Total Tx Packets 100\ndot11Ok 9\n")
    assert out == {"dot11Ok": 9}


def test_eq_branch_rejects_nonidentifier_key():
    # '=' 포함 요약줄(공백·콜론 키)은 잡키로 저장 안 됨 — key.isidentifier 가드(round3 MED#2)
    out = wl._parse_mwlan_text("IEEE 802.11 statistics: MCS=7\ndot11RetryCount = 3\n")
    assert out == {"dot11RetryCount": 3}


def test_eq_branch_mixed_tokens_skip_whole_line():
    # '=' 다중값에 비숫자가 섞이면 라인 전체 skip(구 코드의 partial 드롭과 다른 의도된 동작).
    out = wl._parse_mwlan_text("dot11QosCount = 1 2 N/A 4\ndot11Ok = 3\n")
    assert "dot11QosCount" not in out
    assert out["dot11Ok"] == 3
