"""#192: link.json 의 survey_in_use 는 운용 채널(`[in use]`) 행의 누적 active·busy 만 담는다.

opcd 는 이 값의 증가분으로 Network 혼잡(채널 점유율)을 계산한다. 스캔 구간값인 다른 채널
행을 잘못 집거나, 필드가 빠진 행을 반쪽짜리로 내보내면 opcd 가 엉뚱한 점유율을 보고한다.
"""
import os
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("sUTILS", MagicMock())

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wifi_logger_link  # noqa: E402

# 랩 보드(i.MX93, SDIO) `iw dev mlan0 survey dump` 원문(2026-10-26).
SURVEY = (
    "Survey data from mlan0\n"
    "\tfrequency:\t\t\t5180 MHz\n"
    "\tnoise:\t\t\t\t-92 dBm\n"
    "\tchannel active time:\t\t39 ms\n"
    "\tchannel busy time:\t\t2 ms\n"
    "Survey data from mlan0\n"
    "\tfrequency:\t\t\t5200 MHz [in use]\n"
    "\tnoise:\t\t\t\t-92 dBm\n"
    "\tchannel active time:\t\t8331505 ms\n"
    "\tchannel busy time:\t\t368025 ms\n"
    "\tchannel receive time:\t\t70738 ms\n"
    "\tchannel transmit time:\t\t76992 ms\n"
    "Survey data from mlan0\n"
    "\tfrequency:\t\t\t5220 MHz\n"
    "\tnoise:\t\t\t\t-92 dBm\n"
    "\tchannel active time:\t\t39 ms\n"
    "\tchannel busy time:\t\t0 ms\n"
)


def test_in_use_row_is_returned():
    assert wifi_logger_link.parse_survey_in_use(SURVEY) == {
        "freq": 5200, "active_time_ms": 8331505, "busy_time_ms": 368025}


def test_scan_rows_are_never_reported():
    without = SURVEY.replace(" [in use]", "")
    assert wifi_logger_link.parse_survey_in_use(without) is None


def test_in_use_row_without_busy_time_is_dropped():
    broken = SURVEY.replace("\tchannel busy time:\t\t368025 ms\n", "")
    assert wifi_logger_link.parse_survey_in_use(broken) is None


def test_in_use_row_at_the_end_of_the_dump():
    last = SURVEY.replace(" [in use]", "") + (
        "Survey data from mlan0\n"
        "\tfrequency:\t\t\t5240 MHz [in use]\n"
        "\tchannel active time:\t\t1000 ms\n"
        "\tchannel busy time:\t\t500 ms\n")
    assert wifi_logger_link.parse_survey_in_use(last) == {
        "freq": 5240, "active_time_ms": 1000, "busy_time_ms": 500}


def test_record_carries_the_monotonic_read_time():
    # active time 은 벽시계 기준이라 opcd 가 이 단조 시각과 대조한다(#192 리뷰 A-R1-001).
    assert wifi_logger_link.survey_in_use_record(SURVEY, 123456) == {
        "freq": 5200, "active_time_ms": 8331505, "busy_time_ms": 368025, "mono_ms": 123456}
    assert wifi_logger_link.survey_in_use_record(SURVEY.replace(" [in use]", ""), 123456) is None
    assert wifi_logger_link.survey_in_use_record("", 123456) is None


def test_existing_channel_info_shape_is_unchanged():
    info = wifi_logger_link.parse_survey_dump(SURVEY)
    assert info["5200"] == {"noise": -92, "active_time_ms": 8331505, "busy_time_ms": 368025}
    assert set(info) == {"5180", "5200", "5220"}
