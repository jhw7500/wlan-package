#!/usr/bin/env python3
import json
import time
import subprocess
import re
import sys
import signal
import logging
import logging.handlers
import os
import select
from typing import Any, Dict
from datetime import datetime
from collections import deque
from sUTILS import Logger, _EXTRA_
from roam_notify import notify_roam, get_associated_bssid, confirm_roam

VERSION = "1.1"
IFACE = "mlan0"
LINK_LOG_FILE = f"/var/log/cantops/json/{IFACE}/link.json"
SCAN_LOG_FILE = f"/var/log/cantops/scan/{IFACE}/ap.log"
WPA_CONF_FILE = f"/etc/wpa_supplicant/wpa_supplicant-mlan0.conf"
ROAM_CONDITION_FLAG = "/tmp/roam_condition"
LAST_SCAN_TIME_FILE = "/tmp/last_roam_scan_time"
WIFI_INIT_CONF_JSON = "/usr/local/etc/wifi_init_conf.json"
ROAM_HINT_FILE = f"/tmp/wifi_roam_hint_{IFACE}"  # bgscan이 새 후보 AP 발견 시 touch (단방향 신호)
MAX_SCAN_SSIDS = 10  # nl80211 max # scan SSIDs (NXP mlan 실측). 초과 시 iw가 -EINVAL로 스캔 전체 실패.
WPA_SSID = None
WPA_FREQ = None
WPA_TH_2G = None
WPA_TH_5G = None
WPA_TH_CONNECT = None
WPA_CONF_MTIME = None  # 마지막으로 파싱한 wpa_supplicant conf 의 mtime (런타임 reconfigure 반영용)

# ==============================================================================
# 기본 설정값 (Default Configuration)
# ==============================================================================
DEFAULT_TH_2G = -75
DEFAULT_TH_5G = -75
DIFF_TH = 10
CHECK_INTERVAL = 1


def is_valid_rssi(rssi) -> bool:
    if not isinstance(rssi, int):
        return False
    return -120 <= rssi <= -1


# 개선 설정 기본값 (Default Improvement Configuration)
DEFAULT_ENABLE_PREDICTIVE_ROAM = True
DEFAULT_PREDICTIVE_THRESHOLD_BOOST = 5
DEFAULT_TREND_WINDOW_SIZE = 5
DEFAULT_TREND_HISTORY_MAX_AGE = 30
DEFAULT_ENABLE_LOAD_BASED_ROAM = True
DEFAULT_MAX_ROAM_LOAD = 80
DEFAULT_LOAD_DIFF_THRESHOLD = 20
DEFAULT_ENABLE_PING_PONG_PREVENTION = True
DEFAULT_PING_PONG_WINDOW = 60
DEFAULT_MAX_ROAMS_IN_WINDOW = 3
DEFAULT_PING_PONG_DETECTION_TIME = 30
DEFAULT_ENABLE_ADAPTIVE_INTERVAL = True
DEFAULT_MIN_CHECK_INTERVAL = 1
DEFAULT_MAX_CHECK_INTERVAL = 10
DEFAULT_ADAPTIVE_RSSI_DROP_THRESHOLD = -5    # Phase 1: 이 값 미만 하락 시 min_interval 전환
DEFAULT_ADAPTIVE_RSSI_RISE_THRESHOLD = 2     # Phase 1: 이 값 초과 상승 시 간격 증가
DEFAULT_ADAPTIVE_NEAR_THRESHOLD_OFFSET = 5   # Phase 2: threshold+offset 이하 → 빠른 체크 구간
DEFAULT_ADAPTIVE_NEAR_THRESHOLD_INTERVAL = 2 # Phase 2: 근접 구간 고정 인터벌 (s)
DEFAULT_ADAPTIVE_GOOD_SIGNAL_OFFSET = 15     # Phase 2: threshold+offset 초과 → 간격 증가 허용
DEFAULT_ADAPTIVE_CONSECUTIVE_DROP_COUNT = 2  # Phase 3: 연속 하락 이 횟수 이상 시 min_interval 강제
DEFAULT_USE_SIGNAL_AVG = False  # True: link 파일의 signal_avg 사용, False: signal 사용

# Sleep 기본값
DEFAULT_SCAN_NO_RESULT_SLEEP = 3  # AP 스캔 결과 없을 때 재시도 대기
DEFAULT_ROAM_SUCCESS_SLEEP = 5  # 로밍 성공 후 안정화 대기
DEFAULT_ROAM_NO_RESULT_MAX_SLEEP = 30  # 후보없음 backoff 상한(초)
DEFAULT_ROAM_NO_RESULT_BACKOFF_RECOVER_SEC = 60  # 상한 도달 후 streak 점감 시작(초)
DEFAULT_ROAM_CROSS_FAIL_RETRY_COUNT = 2  # cross-SSID 전환 실패 시 cooldown 없이 즉시 재시도 허용 횟수(초과 시 backoff)

# Post-Roam ARP 최적화 기본값
DEFAULT_ENABLE_POST_ROAM_ARP_OPTIMIZATION = True
DEFAULT_POST_ROAM_GARP_COUNT = 2
DEFAULT_POST_ROAM_GARP_WAIT = 1
DEFAULT_ENABLE_POST_ROAM_PEER_WARMUP = True
DEFAULT_POST_ROAM_PEER_COUNT = 5
DEFAULT_POST_ROAM_PEER_WAIT = 1

# 현재 설정값 (Current Configuration - will be loaded from JSON)
ENABLE_PREDICTIVE_ROAM = DEFAULT_ENABLE_PREDICTIVE_ROAM
PREDICTIVE_THRESHOLD_BOOST = DEFAULT_PREDICTIVE_THRESHOLD_BOOST
TREND_WINDOW_SIZE = DEFAULT_TREND_WINDOW_SIZE
TREND_HISTORY_MAX_AGE = DEFAULT_TREND_HISTORY_MAX_AGE
ENABLE_LOAD_BASED_ROAM = DEFAULT_ENABLE_LOAD_BASED_ROAM
MAX_ROAM_LOAD = DEFAULT_MAX_ROAM_LOAD
LOAD_DIFF_THRESHOLD = DEFAULT_LOAD_DIFF_THRESHOLD
ENABLE_PING_PONG_PREVENTION = DEFAULT_ENABLE_PING_PONG_PREVENTION
PING_PONG_WINDOW = DEFAULT_PING_PONG_WINDOW
MAX_ROAMS_IN_WINDOW = DEFAULT_MAX_ROAMS_IN_WINDOW
PING_PONG_DETECTION_TIME = DEFAULT_PING_PONG_DETECTION_TIME
ENABLE_ADAPTIVE_INTERVAL = DEFAULT_ENABLE_ADAPTIVE_INTERVAL
MIN_CHECK_INTERVAL = DEFAULT_MIN_CHECK_INTERVAL
MAX_CHECK_INTERVAL = DEFAULT_MAX_CHECK_INTERVAL
ADAPTIVE_RSSI_DROP_THRESHOLD = DEFAULT_ADAPTIVE_RSSI_DROP_THRESHOLD
ADAPTIVE_RSSI_RISE_THRESHOLD = DEFAULT_ADAPTIVE_RSSI_RISE_THRESHOLD
ADAPTIVE_NEAR_THRESHOLD_OFFSET = DEFAULT_ADAPTIVE_NEAR_THRESHOLD_OFFSET
ADAPTIVE_NEAR_THRESHOLD_INTERVAL = DEFAULT_ADAPTIVE_NEAR_THRESHOLD_INTERVAL
ADAPTIVE_GOOD_SIGNAL_OFFSET = DEFAULT_ADAPTIVE_GOOD_SIGNAL_OFFSET
ADAPTIVE_CONSECUTIVE_DROP_COUNT = DEFAULT_ADAPTIVE_CONSECUTIVE_DROP_COUNT
USE_SIGNAL_AVG = DEFAULT_USE_SIGNAL_AVG

# 다중 SSID 로밍: conf 기본 ssid 외 추가 로밍 후보 SSID (roaming.extra_ssids에서 로드)
EXTRA_SSIDS = []

# 모드 결정자: true=모드A(다중 network 블록 생성 + select_network cross-SSID),
# false=모드B(단일 블록, cross-SSID는 외부 wifi connect만). 기본 false(무회귀).
# extra_ssids는 generate=false면 무시(get_allowed_ssids/메인루프 cross/bgscan 3중 게이트).
GENERATE_NETWORK_BLOCKS = False

# Sleep 설정
SCAN_NO_RESULT_SLEEP = DEFAULT_SCAN_NO_RESULT_SLEEP
ROAM_SUCCESS_SLEEP = DEFAULT_ROAM_SUCCESS_SLEEP
ROAM_NO_RESULT_MAX_SLEEP = DEFAULT_ROAM_NO_RESULT_MAX_SLEEP
ROAM_NO_RESULT_BACKOFF_RECOVER_SEC = DEFAULT_ROAM_NO_RESULT_BACKOFF_RECOVER_SEC
ROAM_CROSS_FAIL_RETRY_COUNT = DEFAULT_ROAM_CROSS_FAIL_RETRY_COUNT

def _no_result_max_level():
    """backoff가 상한(ROAM_NO_RESULT_MAX_SLEEP)에 도달하는 최소 streak 레벨.

    2**(level-1) 거대 정수 연산을 막기 위해 streak를 이 레벨로 clamp한다.
    시작값*2**(L-1) >= cap 를 만족하는 최소 L. 도달 즉시 상한이므로 그 이상은 무의미.
    SCAN_NO_RESULT_SLEEP<=0 등 비정상 입력은 1로 방어(무한 루프/0배수 방지)."""
    start = SCAN_NO_RESULT_SLEEP
    cap = ROAM_NO_RESULT_MAX_SLEEP
    if start <= 0:
        return 1
    level = 1
    val = start
    while val < cap and level < 64:  # level 상한(64)으로 이론적 무한 루프 방어
        val *= 2
        level += 1
    return level


def compute_no_result_backoff(streak):
    """후보없음 streak에 대한 sleep 초(지수 backoff, 상한 clamp).

    streak<=0 → 시작값(SCAN_NO_RESULT_SLEEP). streak>=1 → 시작값 * 2**(eff-1),
    단 eff=min(streak, max_level)로 clamp(거대 정수 2**streak 방지)하고
    ROAM_NO_RESULT_MAX_SLEEP 상한. 상한 도달 동작은 보존(streak 큰 값 → MAX_SLEEP).
    끊김 복구는 wpa 네이티브가 담당하므로 이 backoff는 '연결 중 후보없음'
    airtime 잠식만 억제한다(spec §4)."""
    if streak <= 0:
        return int(SCAN_NO_RESULT_SLEEP)
    eff = min(streak, _no_result_max_level())
    backoff = SCAN_NO_RESULT_SLEEP * (2 ** (eff - 1))
    return int(min(backoff, ROAM_NO_RESULT_MAX_SLEEP))


def advance_no_candidate_backoff(streak, cap_ts):
    """후보없음 1 tick 진행: streak 증가(상한 clamp) → backoff 계산 →
    상한 도달 시 cap_ts(첫 도달 시각) 기록 및 RECOVER_SEC 경과마다 streak 점감.

    메인루프 3곳(scan 실패 / 결과 0건 / 적합후보 없음)의 동일 로직을 단일화(DRY).
    streak를 max_level로 cap해 매 tick 무한 증가를 막고(#5), 시간 기반 점감이
    유효하도록 한다. 반환: (backoff, streak, cap_ts)."""
    max_level = _no_result_max_level()
    streak = min(streak + 1, max_level)
    backoff = compute_no_result_backoff(streak)
    if backoff >= ROAM_NO_RESULT_MAX_SLEEP:
        if cap_ts is None:
            cap_ts = time.time()
        elif time.time() - cap_ts >= ROAM_NO_RESULT_BACKOFF_RECOVER_SEC:
            streak = max(1, streak - 1)
            cap_ts = time.time()
    return backoff, streak, cap_ts

def roam_hint_touched(state):
    """bgscan hint 파일 mtime이 직전 관측보다 새로우면 True(+state 갱신).

    파일 없음/stat 실패 → False(state 불변). 단방향(roam read / bgscan write)이라
    race-free. 호출자는 True일 때 no_candidate streak=0 으로 고속 복귀(spec §4 reset-b)."""
    try:
        mtime = os.path.getmtime(ROAM_HINT_FILE)
    except OSError:
        return False
    if state.get("hint_mtime") is None or mtime > state["hint_mtime"]:
        state["hint_mtime"] = mtime
        return True
    return False

# Post-Roam ARP 최적화 설정
ENABLE_POST_ROAM_ARP_OPTIMIZATION = DEFAULT_ENABLE_POST_ROAM_ARP_OPTIMIZATION
POST_ROAM_GARP_COUNT = DEFAULT_POST_ROAM_GARP_COUNT
POST_ROAM_GARP_WAIT = DEFAULT_POST_ROAM_GARP_WAIT
ENABLE_POST_ROAM_PEER_WARMUP = DEFAULT_ENABLE_POST_ROAM_PEER_WARMUP
POST_ROAM_PEER_COUNT = DEFAULT_POST_ROAM_PEER_COUNT
POST_ROAM_PEER_WAIT = DEFAULT_POST_ROAM_PEER_WAIT


# ==============================================================================
# 설정 로드 함수 (Configuration Loader)
# ==============================================================================
def parse_bool(value):
    """문자열을 boolean으로 변환"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes", "on", "enabled")
    return bool(value)


def _set_config_value(config: Dict[str, Any], key: str, raw_value: Any, caster) -> None:
    if raw_value is None:
        return
    try:
        config[key] = caster(raw_value)
    except (TypeError, ValueError):
        if "logger" in globals():
            logger.message(
                "warn",
                f"[{IFACE}] invalid roaming config '{key}': {raw_value} (keep default)",
                _EXTRA_(),
            )


def _apply_section_values(
    config: Dict[str, Any], section: Dict[str, Any], mapping
) -> None:
    for source_key, config_key, caster in mapping:
        _set_config_value(config, config_key, section.get(source_key), caster)


def _apply_runtime_globals(config: Dict[str, Any]) -> None:
    g = globals()

    def _num(key, cast=int):
        # flat 덮어쓰기 루프가 원본 문자열 등 잘못된 값을 넣어도(런타임 reload/부팅)
        # int() 크래시 없이 현행 전역값을 유지한다. 수치 전역은 모듈 로드 시 유효 기본값
        # 으로 정의되므로 g.get(key)는 항상 유효(최후 방어까지 삼중 가드). 이 방어가
        # 없으면 예: `SCAN_NO_RESULT_SLEEP: "bad"` 한 줄로 데몬이 ValueError로 죽는다.
        try:
            return cast(config[key])
        except (TypeError, ValueError, KeyError):
            cur = g.get(key)
            try:
                return cast(cur)
            except (TypeError, ValueError):
                # 미래에 모듈 전역 초기값 없는 키가 추가돼 cur가 None이어도 None을
                # 전역에 쓰지 않는다(이후 int 사용처 TypeError 방지) — 최후 0 폴백.
                return cur if isinstance(cur, (int, float)) else 0

    g.update(
        {
            "ENABLE_PREDICTIVE_ROAM": config["ENABLE_PREDICTIVE_ROAM"],
            "PREDICTIVE_THRESHOLD_BOOST": _num("PREDICTIVE_THRESHOLD_BOOST"),
            "TREND_WINDOW_SIZE": _num("TREND_WINDOW_SIZE"),
            "TREND_HISTORY_MAX_AGE": _num("TREND_HISTORY_MAX_AGE"),
            "ENABLE_LOAD_BASED_ROAM": config["ENABLE_LOAD_BASED_ROAM"],
            "MAX_ROAM_LOAD": _num("MAX_ROAM_LOAD"),
            "LOAD_DIFF_THRESHOLD": _num("LOAD_DIFF_THRESHOLD"),
            "ENABLE_PING_PONG_PREVENTION": config["ENABLE_PING_PONG_PREVENTION"],
            "PING_PONG_WINDOW": _num("PING_PONG_WINDOW"),
            "MAX_ROAMS_IN_WINDOW": _num("MAX_ROAMS_IN_WINDOW"),
            "PING_PONG_DETECTION_TIME": _num("PING_PONG_DETECTION_TIME"),
            "ENABLE_ADAPTIVE_INTERVAL": config["ENABLE_ADAPTIVE_INTERVAL"],
            "MIN_CHECK_INTERVAL": _num("MIN_CHECK_INTERVAL"),
            "MAX_CHECK_INTERVAL": _num("MAX_CHECK_INTERVAL"),
            "ADAPTIVE_RSSI_DROP_THRESHOLD": _num("ADAPTIVE_RSSI_DROP_THRESHOLD"),
            "ADAPTIVE_RSSI_RISE_THRESHOLD": _num("ADAPTIVE_RSSI_RISE_THRESHOLD"),
            "ADAPTIVE_NEAR_THRESHOLD_OFFSET": _num("ADAPTIVE_NEAR_THRESHOLD_OFFSET"),
            "ADAPTIVE_NEAR_THRESHOLD_INTERVAL": _num("ADAPTIVE_NEAR_THRESHOLD_INTERVAL"),
            "ADAPTIVE_GOOD_SIGNAL_OFFSET": _num("ADAPTIVE_GOOD_SIGNAL_OFFSET"),
            "ADAPTIVE_CONSECUTIVE_DROP_COUNT": _num("ADAPTIVE_CONSECUTIVE_DROP_COUNT"),
            "DEFAULT_TH_2G": _num("DEFAULT_TH_2G"),
            "DEFAULT_TH_5G": _num("DEFAULT_TH_5G"),
            "DIFF_TH": _num("DIFF_TH"),
            "CHECK_INTERVAL": _num("CHECK_INTERVAL"),
            "ENABLE_POST_ROAM_ARP_OPTIMIZATION": config[
                "ENABLE_POST_ROAM_ARP_OPTIMIZATION"
            ],
            "POST_ROAM_GARP_COUNT": _num("POST_ROAM_GARP_COUNT"),
            "POST_ROAM_GARP_WAIT": _num("POST_ROAM_GARP_WAIT"),
            "ENABLE_POST_ROAM_PEER_WARMUP": config["ENABLE_POST_ROAM_PEER_WARMUP"],
            "POST_ROAM_PEER_COUNT": _num("POST_ROAM_PEER_COUNT"),
            "POST_ROAM_PEER_WAIT": _num("POST_ROAM_PEER_WAIT"),
            "SCAN_NO_RESULT_SLEEP": _num("SCAN_NO_RESULT_SLEEP"),
            "ROAM_SUCCESS_SLEEP": _num("ROAM_SUCCESS_SLEEP"),
            "ROAM_NO_RESULT_MAX_SLEEP": _num("ROAM_NO_RESULT_MAX_SLEEP"),
            "ROAM_NO_RESULT_BACKOFF_RECOVER_SEC": _num(
                "ROAM_NO_RESULT_BACKOFF_RECOVER_SEC"
            ),
            "ROAM_CROSS_FAIL_RETRY_COUNT": _num("ROAM_CROSS_FAIL_RETRY_COUNT"),
            "USE_SIGNAL_AVG": config["USE_SIGNAL_AVG"],
        }
    )


def load_roaming_config(iface, data=None):
    """
    JSON 형식의 conf 파일에서 인터페이스별 로밍 설정 로드

    Args:
        iface: 인터페이스 이름 (mlan0 또는 mlan1)
        data: 이미 파싱·검증된 설정 dict(런타임 reload용). 주어지면 파일을 다시
              읽지 않아 검증-적용 사이 파일 교체(TOCTOU)로 인한 기본값 회귀가 없다.

    Returns:
        dict: 로밍 설정 dictionary
    """
    global EXTRA_SSIDS, GENERATE_NETWORK_BLOCKS
    GENERATE_NETWORK_BLOCKS = False
    config = {
        "ENABLE_PREDICTIVE_ROAM": DEFAULT_ENABLE_PREDICTIVE_ROAM,
        "PREDICTIVE_THRESHOLD_BOOST": DEFAULT_PREDICTIVE_THRESHOLD_BOOST,
        "TREND_WINDOW_SIZE": DEFAULT_TREND_WINDOW_SIZE,
        "TREND_HISTORY_MAX_AGE": DEFAULT_TREND_HISTORY_MAX_AGE,
        "ENABLE_LOAD_BASED_ROAM": DEFAULT_ENABLE_LOAD_BASED_ROAM,
        "MAX_ROAM_LOAD": DEFAULT_MAX_ROAM_LOAD,
        "LOAD_DIFF_THRESHOLD": DEFAULT_LOAD_DIFF_THRESHOLD,
        "ENABLE_PING_PONG_PREVENTION": DEFAULT_ENABLE_PING_PONG_PREVENTION,
        "PING_PONG_WINDOW": DEFAULT_PING_PONG_WINDOW,
        "MAX_ROAMS_IN_WINDOW": DEFAULT_MAX_ROAMS_IN_WINDOW,
        "PING_PONG_DETECTION_TIME": DEFAULT_PING_PONG_DETECTION_TIME,
        "ENABLE_ADAPTIVE_INTERVAL": DEFAULT_ENABLE_ADAPTIVE_INTERVAL,
        "MIN_CHECK_INTERVAL": DEFAULT_MIN_CHECK_INTERVAL,
        "MAX_CHECK_INTERVAL": DEFAULT_MAX_CHECK_INTERVAL,
        "ADAPTIVE_RSSI_DROP_THRESHOLD": DEFAULT_ADAPTIVE_RSSI_DROP_THRESHOLD,
        "ADAPTIVE_RSSI_RISE_THRESHOLD": DEFAULT_ADAPTIVE_RSSI_RISE_THRESHOLD,
        "ADAPTIVE_NEAR_THRESHOLD_OFFSET": DEFAULT_ADAPTIVE_NEAR_THRESHOLD_OFFSET,
        "ADAPTIVE_NEAR_THRESHOLD_INTERVAL": DEFAULT_ADAPTIVE_NEAR_THRESHOLD_INTERVAL,
        "ADAPTIVE_GOOD_SIGNAL_OFFSET": DEFAULT_ADAPTIVE_GOOD_SIGNAL_OFFSET,
        "ADAPTIVE_CONSECUTIVE_DROP_COUNT": DEFAULT_ADAPTIVE_CONSECUTIVE_DROP_COUNT,
        "DEFAULT_TH_2G": DEFAULT_TH_2G,
        "DEFAULT_TH_5G": DEFAULT_TH_5G,
        "DIFF_TH": DIFF_TH,
        "CHECK_INTERVAL": CHECK_INTERVAL,
        "ENABLE_POST_ROAM_ARP_OPTIMIZATION": DEFAULT_ENABLE_POST_ROAM_ARP_OPTIMIZATION,
        "POST_ROAM_GARP_COUNT": DEFAULT_POST_ROAM_GARP_COUNT,
        "POST_ROAM_GARP_WAIT": DEFAULT_POST_ROAM_GARP_WAIT,
        "ENABLE_POST_ROAM_PEER_WARMUP": DEFAULT_ENABLE_POST_ROAM_PEER_WARMUP,
        "POST_ROAM_PEER_COUNT": DEFAULT_POST_ROAM_PEER_COUNT,
        "POST_ROAM_PEER_WAIT": DEFAULT_POST_ROAM_PEER_WAIT,
        "SCAN_NO_RESULT_SLEEP": DEFAULT_SCAN_NO_RESULT_SLEEP,
        "ROAM_SUCCESS_SLEEP": DEFAULT_ROAM_SUCCESS_SLEEP,
        "ROAM_NO_RESULT_MAX_SLEEP": DEFAULT_ROAM_NO_RESULT_MAX_SLEEP,
        "ROAM_NO_RESULT_BACKOFF_RECOVER_SEC": DEFAULT_ROAM_NO_RESULT_BACKOFF_RECOVER_SEC,
        "ROAM_CROSS_FAIL_RETRY_COUNT": DEFAULT_ROAM_CROSS_FAIL_RETRY_COUNT,
        "USE_SIGNAL_AVG": DEFAULT_USE_SIGNAL_AVG,
    }

    # 1. JSON 설정 파일 시도. data(검증된 dict)가 주어지면 파일을 다시 읽지 않고
    #    그대로 사용 — 재읽기 제거로 검증-적용 원자성(TOCTOU 차단) 보장.
    try:
        if data is None:
            with open(WIFI_INIT_CONF_JSON, "r") as f:
                data = json.load(f)

        if iface in data and "roaming" in data[iface]:
            roam_config = data[iface]["roaming"]

            predictive = roam_config.get("PREDICTIVE_ROAM")
            if isinstance(predictive, dict):
                _apply_section_values(
                    config,
                    predictive,
                    [
                        ("enable", "ENABLE_PREDICTIVE_ROAM", parse_bool),
                        ("threshold_boost", "PREDICTIVE_THRESHOLD_BOOST", int),
                        ("trend_window_size", "TREND_WINDOW_SIZE", int),
                        ("trend_history_max_age", "TREND_HISTORY_MAX_AGE", int),
                    ],
                )

            load_based = roam_config.get("LOAD_BASED_ROAM")
            if isinstance(load_based, dict):
                _apply_section_values(
                    config,
                    load_based,
                    [
                        ("enable", "ENABLE_LOAD_BASED_ROAM", parse_bool),
                        ("max_roam_load", "MAX_ROAM_LOAD", int),
                        ("load_diff_threshold", "LOAD_DIFF_THRESHOLD", int),
                    ],
                )

            ping_pong = roam_config.get("PING_PONG_PREVENTION")
            if isinstance(ping_pong, dict):
                _apply_section_values(
                    config,
                    ping_pong,
                    [
                        ("enable", "ENABLE_PING_PONG_PREVENTION", parse_bool),
                        ("window", "PING_PONG_WINDOW", int),
                        ("max_roams_in_window", "MAX_ROAMS_IN_WINDOW", int),
                        ("detection_time", "PING_PONG_DETECTION_TIME", int),
                    ],
                )

            adaptive = roam_config.get("ADAPTIVE_INTERVAL")
            if isinstance(adaptive, dict):
                _apply_section_values(
                    config,
                    adaptive,
                    [
                        ("enable", "ENABLE_ADAPTIVE_INTERVAL", parse_bool),
                        ("min_check_interval", "MIN_CHECK_INTERVAL", int),
                        ("max_check_interval", "MAX_CHECK_INTERVAL", int),
                        ("rssi_drop_threshold", "ADAPTIVE_RSSI_DROP_THRESHOLD", int),
                        ("rssi_rise_threshold", "ADAPTIVE_RSSI_RISE_THRESHOLD", int),
                        ("near_threshold_offset", "ADAPTIVE_NEAR_THRESHOLD_OFFSET", int),
                        ("near_threshold_interval", "ADAPTIVE_NEAR_THRESHOLD_INTERVAL", int),
                        ("good_signal_offset", "ADAPTIVE_GOOD_SIGNAL_OFFSET", int),
                        ("consecutive_drop_count", "ADAPTIVE_CONSECUTIVE_DROP_COUNT", int),
                    ],
                )

            post_roam = roam_config.get("POST_ROAM_ARP_OPTIMIZATION")
            if isinstance(post_roam, dict):
                _apply_section_values(
                    config,
                    post_roam,
                    [
                        ("enable", "ENABLE_POST_ROAM_ARP_OPTIMIZATION", parse_bool),
                        ("garp_count", "POST_ROAM_GARP_COUNT", int),
                        ("garp_wait", "POST_ROAM_GARP_WAIT", int),
                    ],
                )

                peer_warmup = post_roam.get("PEER_WARMUP")
                if isinstance(peer_warmup, dict):
                    _apply_section_values(
                        config,
                        peer_warmup,
                        [
                            ("enable", "ENABLE_POST_ROAM_PEER_WARMUP", parse_bool),
                            ("peer_count", "POST_ROAM_PEER_COUNT", int),
                            ("peer_wait", "POST_ROAM_PEER_WAIT", int),
                        ],
                    )

            # use_signal_avg 옵션 처리
            _set_config_value(
                config, "USE_SIGNAL_AVG",
                roam_config.get("use_signal_avg"), parse_bool
            )

            # 다중 SSID 로밍: extra_ssids 로드 (str 리스트만 수용, 공백 제거).
            # 키 제거/null 시 이전 값이 stale로 남지 않도록 무조건 재대입.
            extra = roam_config.get("extra_ssids")
            EXTRA_SSIDS = [
                str(s).strip() for s in extra if str(s).strip()
            ] if isinstance(extra, list) else []

            # 후보없음 backoff 파라미터(평탄 대문자 키). 양의 정수만 수용, 형식오류 시 기본값 유지.
            _set_config_value(
                config, "ROAM_NO_RESULT_MAX_SLEEP",
                roam_config.get("ROAM_NO_RESULT_MAX_SLEEP"), int
            )
            _set_config_value(
                config, "ROAM_NO_RESULT_BACKOFF_RECOVER_SEC",
                roam_config.get("ROAM_NO_RESULT_BACKOFF_RECOVER_SEC"), int
            )
            _set_config_value(
                config, "ROAM_CROSS_FAIL_RETRY_COUNT",
                roam_config.get("ROAM_CROSS_FAIL_RETRY_COUNT"), int
            )

            # 모드 결정자 generate_network_blocks 파싱 (bool만 수용, 기본 false).
            # 키 부재/형식오류 시 false로 수렴해 모드 B(단일 블록) 보장.
            GENERATE_NETWORK_BLOCKS = parse_bool(
                roam_config.get("generate_network_blocks", False)
            )

            # 설정 적용
            for key in config.keys():
                if key in roam_config:
                    config[key] = roam_config[key]

    except FileNotFoundError:
        if "logger" in globals():
            logger.message(
                "warn",
                f"[{iface}] roaming config not found: {WIFI_INIT_CONF_JSON}",
                _EXTRA_(),
            )
    except json.JSONDecodeError as e:
        if "logger" in globals():
            logger.message(
                "err", f"[{iface}] roaming config JSON decode error: {e}", _EXTRA_()
            )
    except Exception as e:
        if "logger" in globals():
            logger.message(
                "err", f"[{iface}] roaming config load error: {e}", _EXTRA_()
            )

    _apply_runtime_globals(config)

    logger.message(
        "info",
        f"[{IFACE}] Roaming config loaded: "
        f"predictive={ENABLE_PREDICTIVE_ROAM}, load_based={ENABLE_LOAD_BASED_ROAM}, "
        f"ping_pong={ENABLE_PING_PONG_PREVENTION}, adaptive={ENABLE_ADAPTIVE_INTERVAL}, "
        f"post_roam_arp={ENABLE_POST_ROAM_ARP_OPTIMIZATION}, "
        f"rssi_source={'signal_avg' if USE_SIGNAL_AVG else 'signal'}, "
        f"extra_ssids={EXTRA_SSIDS}",
        _EXTRA_(),
    )

    logger.message(
        "info",
        f"[{IFACE}] Roaming effective values: "
        f"th_2g={DEFAULT_TH_2G}, th_5g={DEFAULT_TH_5G}, diff_th={DIFF_TH}, check_interval={CHECK_INTERVAL}, "
        f"predictive(boost={PREDICTIVE_THRESHOLD_BOOST}, window={TREND_WINDOW_SIZE}, max_age={TREND_HISTORY_MAX_AGE}), "
        f"load(max_roam_load={MAX_ROAM_LOAD}, load_diff_th={LOAD_DIFF_THRESHOLD}), "
        f"ping_pong(window={PING_PONG_WINDOW}, max_roams={MAX_ROAMS_IN_WINDOW}, detect_time={PING_PONG_DETECTION_TIME}), "
        f"adaptive(min={MIN_CHECK_INTERVAL}, max={MAX_CHECK_INTERVAL}), "
        f"post_roam_arp(garp_count={POST_ROAM_GARP_COUNT}, garp_wait={POST_ROAM_GARP_WAIT}, "
        f"peer_warmup={ENABLE_POST_ROAM_PEER_WARMUP}, peer_count={POST_ROAM_PEER_COUNT}, peer_wait={POST_ROAM_PEER_WAIT})",
        _EXTRA_(),
    )

    return config


# ==============================================================================
# RSSI 추적기 (RSSI Trend Tracker)
# ==============================================================================
class RSSITrendTracker:
    """RSSI 추세를 분석하여 예측형 로밍을 지원"""

    TREND_FALLING = -1
    TREND_STABLE = 0
    TREND_RISING = 1

    def __init__(self, window_size=TREND_WINDOW_SIZE, max_age=TREND_HISTORY_MAX_AGE):
        self.window_size = window_size
        self.max_age = max_age
        self.rssi_history = []  # [(timestamp, rssi), ...]

    def add_sample(self, rssi):
        """RSSI 샘플 추가"""
        now = time.time()
        self.rssi_history.append((now, rssi))

        # 오래된 샘플 제거
        self.rssi_history = [
            (t, r) for t, r in self.rssi_history if now - t < self.max_age
        ]

    def get_trend(self):
        """
        신호 추세 반환
        -1: 하락 (신호 악화 중)
        0: 유지
        1: 상승 (신호 개선 중)
        """
        if len(self.rssi_history) < 3:
            return self.TREND_STABLE

        # 최근 샘플만 사용
        recent = self.rssi_history[-min(self.window_size, len(self.rssi_history)) :]
        n = len(recent)

        if n < 2:
            return self.TREND_STABLE

        # 선형 회귀로 기울기 계산
        t0 = recent[0][0]
        sum_x = sum((t - t0) for t, _ in recent)
        sum_y = sum(r for _, r in recent)
        sum_xy = sum((t - t0) * r for t, r in recent)
        sum_x2 = sum((t - t0) * (t - t0) for t, _ in recent)

        denominator = n * sum_x2 - sum_x * sum_x
        if denominator == 0:
            return self.TREND_STABLE

        slope = (n * sum_xy - sum_x * sum_y) / denominator

        if slope < -0.5:  # 초당 0.5dB 이상 하락
            return self.TREND_FALLING
        elif slope > 0.5:  # 초당 0.5dB 이상 상승
            return self.TREND_RISING
        else:
            return self.TREND_STABLE

    def get_avg_rssi(self):
        """평균 RSSI 반환"""
        if not self.rssi_history:
            return None
        recent = self.rssi_history[-min(self.window_size, len(self.rssi_history)) :]
        return sum(r for _, r in recent) / len(recent)

    def is_falling_rapidly(self, threshold=-2.0):
        """RSSI가 급격히 하락 중인지 확인"""
        if len(self.rssi_history) < 2:
            return False

        recent = self.rssi_history[-min(3, len(self.rssi_history)) :]
        if len(recent) < 2:
            return False

        # 최근 3샘플의 평균 기울기 확인
        for i in range(len(recent) - 1):
            t1, r1 = recent[i]
            t2, r2 = recent[i + 1]
            dt = t2 - t1
            if dt > 0:
                slope = (r2 - r1) / dt
                if slope < threshold:
                    return True
        return False


# ==============================================================================
# Ping-pong 방지 (Ping-pong Prevention)
# ==============================================================================
class PingPongPreventer:
    """불필요한 반복 로밍 방지"""

    def __init__(self, window_seconds=PING_PONG_WINDOW, max_roams=MAX_ROAMS_IN_WINDOW):
        self.window_seconds = window_seconds
        self.max_roams = max_roams
        self.roam_history = deque()  # [(timestamp, from_bssid, to_bssid), ...]

    def add_roam(self, from_bssid, to_bssid):
        """로밍 기록 추가"""
        now = time.time()
        self.roam_history.append((now, from_bssid, to_bssid))

        # 오래된 기록 제거
        self.roam_history = deque(
            [
                (t, f, t_b)
                for t, f, t_b in self.roam_history
                if now - t < self.window_seconds
            ]
        )

    def is_ping_pong(self, from_bssid, to_bssid):
        """
        Ping-pong 로밍 확인
        - 최근 로밍 횟수 초과 여부
        - 왕복 로밍 (A→B→A) 확인
        """
        now = time.time()

        # 최근 로밍 횟수 확인
        recent_roams = [
            (t, f, t_b)
            for t, f, t_b in self.roam_history
            if now - t < self.window_seconds
        ]

        if len(recent_roams) >= self.max_roams:
            logger.message(
                "warn",
                f"[{IFACE}] Too many roams ({len(recent_roams)}) in {self.window_seconds}s",
                _EXTRA_(),
            )
            return True

        # 반복 로밍 확인 (A→B→A)
        for t, f, t_b in reversed(list(self.roam_history)):
            if f == to_bssid and t_b == from_bssid:
                if now - t < PING_PONG_DETECTION_TIME:
                    logger.message(
                        "warn",
                        f"[{IFACE}] Ping-pong detected: {from_bssid} ↔ {to_bssid}",
                        _EXTRA_(),
                    )
                    return True

        return False

    def get_roam_count(self):
        """최근 로밍 횟수 반환"""
        now = time.time()
        recent = [t for t, _, _ in self.roam_history if now - t < self.window_seconds]
        return len(recent)


class CrossSsidCooldown:
    """모드 A cross-SSID 전환 실패 SSID의 retry 카운트 + 지수 backoff cooldown 추적.

    PingPongPreventer(성공 roam 빈도, BSSID)와 의미가 달라 별도 클래스로 둔다.
    backoff 산식은 compute_no_result_backoff를 공유. fails는 성공 clear까지 유지한다
    (만료 즉시 제거하면 backoff가 리셋되어 영구 실패가 짧은 재시도로 회귀하므로)."""

    def __init__(self, retry_count=DEFAULT_ROAM_CROSS_FAIL_RETRY_COUNT):
        self.retry_count = max(0, retry_count)  # 음수 config 방어(0이면 첫 실패부터 cooldown)
        self.entries = {}  # {ssid: {"fails": int, "until": float}}

    def register_failure(self, ssid, post_sleep=0.0):
        """cross-SSID 전환 실패 등록. retry_count 이하면 cooldown 없음(즉시 재시도),
        초과하면 over=fails-retry_count 로 지수 backoff cooldown 설정.

        post_sleep: 실패 후 메인루프가 추가로 대기하는 시간(ROAM_SUCCESS_SLEEP+interval).
        cooldown until에 이를 더해, 그 sleep 동안 cooldown이 만료돼 다음 평가에서 무효화되는
        것을 막는다(Codex P2 / Claude MEDIUM: 첫 backoff 3s < ROAM_SUCCESS_SLEEP 5s 문제)."""
        if not ssid:
            return
        e = self.entries.setdefault(ssid, {"fails": 0, "until": 0.0})
        e["fails"] += 1
        if e["fails"] <= self.retry_count:
            e["until"] = time.time()  # cooldown 없음 → 다음 평가 tick에 재시도 허용
        else:
            over = e["fails"] - self.retry_count
            e["until"] = time.time() + post_sleep + compute_no_result_backoff(over)

    def is_cooling(self, ssid):
        """ssid가 cooldown 중이면 True. fails는 유지(성공 clear 전까지)."""
        e = self.entries.get(ssid)
        if not e:
            return False
        return time.time() < e["until"]

    def clear(self, ssid):
        """cross-SSID 전환 성공 → 해당 ssid의 실패 카운트/ cooldown 해제."""
        self.entries.pop(ssid, None)


def record_cross_ssid_result(cooldown, ssid, ok, post_sleep):
    """cross-SSID 전환 결과를 cooldown에 반영(메인루프 분기를 함수로 추출 → 단위 테스트 가능).

    성공(ok=True) → clear, 실패(ok=False) → register_failure(ssid, post_sleep).
    ok=None(ping-pong 차단 — 전환 미시도)은 실패가 아니므로 무동작(cooldown 오염 방지).
    cooldown이 None(모드 B, cross 자동전환 비활성)이면 무동작."""
    if cooldown is None or ok is None:
        return
    if ok:
        cooldown.clear(ssid)
    else:
        cooldown.register_failure(ssid, post_sleep)


# ==============================================================================
# 적응형 간격 (Adaptive Interval)
# ==============================================================================
class AdaptiveInterval:
    """RSSI 상태에 따라 체크 간격 조정"""

    def __init__(
        self, min_interval=MIN_CHECK_INTERVAL, max_interval=MAX_CHECK_INTERVAL
    ):
        self.min_interval = min_interval
        self.max_interval = max_interval
        self.current_interval = CHECK_INTERVAL
        self.last_rssi = None
        self.consecutive_low_rssi = 0
        self.consecutive_high_rssi = 0

    def update(self, current_rssi, threshold, trend):
        """
        RSSI와 추세에 따라 간격 조정
        - 신호가 임계값 근처: 간격 단축
        - 신호가 안정적이고 좋음: 간격 증가
        - 하락 추세: 즉시 간격 단축
        """
        # RSSI 변화율 계산
        if self.last_rssi is not None:
            rssi_change = current_rssi - self.last_rssi

            # Phase 1: 신호가 급격히 악화되면 최소 간격
            if rssi_change < ADAPTIVE_RSSI_DROP_THRESHOLD:
                self.current_interval = self.min_interval
                self.consecutive_low_rssi += 1
                self.consecutive_high_rssi = 0
            # Phase 1: 신호가 개선되면 간격 증가
            elif rssi_change > ADAPTIVE_RSSI_RISE_THRESHOLD:
                self.current_interval = min(
                    self.max_interval, self.current_interval + 1
                )
                self.consecutive_high_rssi += 1
                self.consecutive_low_rssi = 0
            else:
                # 안정 상태
                self.consecutive_low_rssi = 0
                self.consecutive_high_rssi = 0

        self.last_rssi = current_rssi

        if threshold is None:
            return self.current_interval

        # Phase 2: 임계값 근처에서는 간격 단축
        if current_rssi < threshold + ADAPTIVE_NEAR_THRESHOLD_OFFSET:
            self.current_interval = max(self.min_interval, ADAPTIVE_NEAR_THRESHOLD_INTERVAL)
        # Phase 2: 신호가 안정적이고 좋으면 간격 증가
        elif current_rssi > threshold + ADAPTIVE_GOOD_SIGNAL_OFFSET and trend == RSSITrendTracker.TREND_STABLE:
            self.current_interval = min(self.max_interval, self.current_interval + 1)

        # Phase 3: 연속 낮은 RSSI 감지 시 더 빠른 체크
        if self.consecutive_low_rssi >= ADAPTIVE_CONSECUTIVE_DROP_COUNT:
            self.current_interval = self.min_interval

        return self.current_interval


# ==============================================================================
# 전역 인스턴스 (Global Instances)
# ==============================================================================
trend_tracker = None
ping_pong_preventer = None
adaptive_interval = None
cross_ssid_cooldown = None


# ==============================================================================
# 원본 함수들 (Original Functions)
# ==============================================================================
def handle_sigterm(signum, frame):
    logger.message(
        "crit", f"[{IFACE}] SIGTERM {signum} received! Cleaning up...", _EXTRA_()
    )
    cleanup()
    sys.exit(0)


def cleanup():
    pass


def freq_to_channel(freq):
    freq = int(freq)
    if 2412 <= freq <= 2472:
        return (freq - 2407) // 5
    elif freq == 2484:
        return 14
    elif 5180 <= freq <= 5825:
        return (freq - 5000) // 5
    elif 5955 <= freq <= 7115:
        return (freq - 5950) // 5
    else:
        return None  # Unknown


def channel_to_freq(channel):
    channel = int(channel)
    if 1 <= channel <= 13:
        return 2407 + channel * 5
    elif channel == 14:
        return 2484
    elif 36 <= channel <= 165:
        return 5000 + channel * 5
    elif 1 <= channel <= 233:  # 6GHz band (e.g., channel 1 → 5955)
        freq = 5950 + channel * 5
        if 5955 <= freq <= 7115:
            return freq
        else:
            return None
    else:
        return None


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


FREQ_TO_CHAN = {
    # 2.4GHz
    "2412": "1g",
    "2417": "2g",
    "2422": "3g",
    "2427": "4g",
    "2432": "5g",
    "2437": "6g",
    "2442": "7g",
    "2447": "8g",
    "2452": "9g",
    "2457": "10g",
    "2462": "11g",
    "2467": "12g",
    "2472": "13g",
    "2484": "14",
    # 5GHz
    "5180": "36a",
    "5200": "40a",
    "5220": "44a",
    "5240": "48a",
    "5260": "52a",
    "5280": "56a",
    "5300": "60a",
    "5320": "64a",
    "5500": "100a",
    "5520": "104a",
    "5540": "108a",
    "5560": "112a",
    "5580": "116a",
    "5600": "120a",
    "5620": "124a",
    "5640": "128a",
    "5660": "132a",
    "5680": "136a",
    "5700": "140a",
    "5720": "144a",
    "5745": "149a",
    "5765": "153a",
    "5785": "157a",
    "5805": "161a",
    "5825": "165a",
    "5845": "169a",
    "5865": "173a",
    "5885": "177a",
}


def set_flag(on, path=ROAM_CONDITION_FLAG):
    with open(path, "w") as f:
        if on is True or on == 1:
            f.write("1")
        elif on is False or on == 0:
            f.write("0")
        else:
            f.write("")


def get_flag(path=ROAM_CONDITION_FLAG) -> bool:
    try:
        with open(path, "r") as f:
            content = f.read().strip()
            return content == "1"
    except FileNotFoundError:
        return False


def mlanutl_scan(ssids, freqs):
    """ssids: 단일 str 또는 SSID 리스트.
    1개면 기존처럼 ssid= 필터(무회귀), 다중이면 ssid= 생략(전체 스캔) 후
    get_latest_scan이 allowed_ssids로 필터(mlanutl 다중 ssid 지원 불확실 대비)."""
    try:
        chan_str = ",".join(FREQ_TO_CHAN[f] for f in freqs)
    except KeyError as e:
        print(f"[ERROR] Unknown frequency: {e}")
        return

    if isinstance(ssids, str):
        ssid_list = [ssids] if ssids else []
    else:
        ssid_list = [s for s in (ssids or []) if s]
    # list 형태 + shell=False로 셸 인젝션 차단 (extra_ssids는 operator 입력값)
    cmd = ["mlanutl", IFACE, "setuserscan", f"chan={chan_str}"]
    if len(ssid_list) == 1:
        cmd.append(f"ssid={ssid_list[0]}")
    # else: 0개/다중 SSID → ssid= 필터 생략(전체 스캔), get_latest_scan이 allowed_ssids로 거름
    logger.message("info", f"[{IFACE}] scan : {' '.join(cmd)}", _EXTRA_())
    try:
        result = subprocess.run(
            cmd, check=True, capture_output=True, text=True
        )
        output = result.stdout.strip()
        if not output:
            logger.message(
                "err", f"[{IFACE}] scan command returned no output", _EXTRA_()
            )
            return None

        return result.stdout.splitlines()
    except subprocess.CalledProcessError as e:
        logger.message(
            "err", f"[{IFACE}] scan command failed:{e.stderr.strip()}", _EXTRA_()
        )
        return None


def iw_scan_to_ap_lines(ssids, freqs):
    """로밍 판정 스캔: `iw scan`으로 실제 스캔(=wpa_supplicant BSS 테이블 충전)을 트리거하고,
    후보를 `wpa_cli scan_results`(=그 테이블)에서 뽑아 get_latest_scan이 읽는 pipe 포맷
    ap 라인으로 변환한다. 후보가 곧 테이블이라 이후 wpa_cli roam이 대상 BSS를 항상 찾는다
    (mlanutl setuserscan은 테이블 미충전 → roam FAIL 근본원인이었다).
    ssids: 단일 str 또는 리스트(directed probe). freqs: MHz 리스트. 실패/결과없음 → None."""
    if isinstance(ssids, str):
        ssid_list = [ssids] if ssids else []
    else:
        ssid_list = [s for s in (ssids or []) if s]

    # 1) iw scan 트리거(동기, 테이블 충전). 다른 스캐너(logger 등)와 경합 시 -EBUSY 재시도.
    cmd = ["iw", IFACE, "scan"]
    if freqs:
        cmd += ["freq"] + [str(f) for f in freqs]
    # directed probe(allowed ssid) + 와일드카드("") probe. iw 문법은 `ssid <ssid>*` —
    # ssid 키워드는 1회만 쓰고 값을 나열한다(키워드 반복은 iw 버전/드라이버에 따라 리터럴
    # 소비/파싱 붕괴 위험). NXP mlan 등은 ssid 지정 시 와일드카드를 안 보내므로
    # ""를 함께 넣어 beacon/broadcast 광범위 스캔을 보존한다(중복 제거).
    # 드라이버 max-scan-SSID 초과 방지: iw는 초과 시 -EINVAL로 스캔 전체를 실패시키므로
    # (→ 로밍 정지) directed SSID 수를 제한하고 wildcard는 항상 보존한다. 초과분(주로
    # hidden extra)은 wildcard broadcast로 non-hidden만 발견(구 mlanutl 다중=undirected 동치).
    # ssid_list=get_allowed_ssids라 live/base가 앞이므로 slice가 현재 네트워크를 우선 보존.
    probe = list(dict.fromkeys(ssid_list + [""]))
    if len(probe) > MAX_SCAN_SSIDS:
        logger.message(
            "warn",
            f"[{IFACE}] scan SSIDs {len(probe)} > driver max {MAX_SCAN_SSIDS}; "
            f"capping directed probes (excess hidden SSIDs may be missed)",
            _EXTRA_(),
        )
        probe = probe[:MAX_SCAN_SSIDS - 1] + [""]
    cmd += ["ssid"] + probe
    scanned_ok = False
    for attempt in range(3):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        except subprocess.TimeoutExpired:
            logger.message("err", f"[{IFACE}] iw scan timeout", _EXTRA_())
            return None
        except Exception as e:
            logger.message("err", f"[{IFACE}] iw scan error: {e}", _EXTRA_())
            return None
        if r.returncode == 0:
            scanned_ok = True
            break
        # -EBUSY(다른 스캔 진행 중) → 잠깐 후 재시도. 그 외는 중단.
        if "busy" in (r.stderr or "").lower() and attempt < 2:
            time.sleep(1)
            continue
        logger.message(
            "warn",
            f"[{IFACE}] iw scan rc={r.returncode}: {(r.stderr or '').strip()}",
            _EXTRA_(),
        )
        break

    # iw scan이 끝내 실패 → 스테일 테이블로 로밍 판단하지 않고 None 반환(호출측 backoff).
    if not scanned_ok:
        logger.message(
            "err", f"[{IFACE}] iw scan failed (all attempts) — skip roam decision", _EXTRA_()
        )
        return None

    # 2) wpa_supplicant가 스캔 결과를 흡수할 짧은 여유 후 scan_results(=BSS 테이블) 조회.
    time.sleep(1)
    try:
        sr = subprocess.run(
            ["wpa_cli", "-i", IFACE, "scan_results"],
            capture_output=True, text=True, timeout=5,
        )
    except Exception as e:
        logger.message("err", f"[{IFACE}] scan_results read error: {e}", _EXTRA_())
        return None
    if sr.returncode != 0:
        logger.message(
            "err",
            f"[{IFACE}] scan_results rc={sr.returncode}, stderr={(sr.stderr or '').strip()}",
            _EXTRA_(),
        )
        return None

    return scan_results_to_ap_lines(sr.stdout) or None


def scan_results_to_ap_lines(scan_results_stdout):
    """`wpa_cli scan_results`(탭 구분: bssid/freq/signal/flags/ssid, 첫 줄 헤더)를
    get_latest_scan이 파싱하는 pipe 포맷(`NN|channel|rssi|ld|bssid|freq|ssid`, 7필드)으로
    변환. 헤더/형식불량/BSSID아님/미지 freq는 skip. ld=0(LOAD는 channel_info 사용)."""
    out = []
    idx = 0
    for line in (scan_results_stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        bssid = parts[0].strip().lower()
        if not re.match(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$", bssid):
            continue  # 헤더('bssid / frequency / ...') 등 skip
        try:
            freq = int(parts[1].strip())
            rssi = int(float(parts[2].strip()))
        except (ValueError, IndexError):
            continue
        ssid = parts[4].strip()
        ch = freq_to_channel(freq)
        if ch is None:
            continue
        out.append(f"{idx % 100:02d}|{ch}|{rssi}|0|{bssid}|{freq}|{ssid}")
        idx += 1
    return out


# ==============================================================================
# 개선된 get_link_info_with_load (Load 정보 포함)
# ==============================================================================
_LINK_CACHE: Dict[str, Any] = {
    "mtime_ns": None,
    "value": None,
}


def get_link_info_with_load():
    """Load 정보를 포함한 연결 정보 반환"""
    try:
        st = os.stat(LINK_LOG_FILE)
        mtime_ns = st.st_mtime_ns
        cached_mtime_ns = _LINK_CACHE.get("mtime_ns")
        if cached_mtime_ns is not None and cached_mtime_ns == mtime_ns:
            return _LINK_CACHE.get("value")

        with open(LINK_LOG_FILE, "r") as f:
            data = json.load(f)
            link = data["link"]
            if USE_SIGNAL_AVG and "signal_avg" in link:
                rssi_raw = link["signal_avg"]
            else:
                rssi_raw = link["signal"]
            result = {
                "bssid": link["address"].strip().lower(),
                "freq": int(data["info"]["freq"]),
                "rssi": int(rssi_raw.replace(" dBm", "")),
                "ssid": data["info"].get("ssid", "").strip(),
            }

            # Load 정보 추가 (활성화됨)
            if ENABLE_LOAD_BASED_ROAM:
                channel_info = data.get("channel_info", {})
                result["channel_info"] = channel_info
                freq_str = str(result["freq"])

                if freq_str in channel_info:
                    info = channel_info[freq_str]
                    noise = info.get("noise", -95)
                    busy = info.get("busy_time_ms", 0)
                    active = info.get("active_time_ms", 1)

                    if active > 0:
                        load = (busy / active) * 100
                    else:
                        load = 0

                    result["noise"] = noise
                    result["load"] = round(load, 2)

                    logger.message(
                        "debug",
                        f"[{IFACE}] channel info: freq={freq_str}, noise={noise}, load={load:.1f}%",
                        _EXTRA_(),
                    )
            else:
                # Load 비활성화 시 기본값 설정
                result["noise"] = -95
                result["load"] = 0

            _LINK_CACHE["mtime_ns"] = mtime_ns
            _LINK_CACHE["value"] = result

            return result

    except Exception as e:
        return None


def get_link_info():
    """기존 get_link_info (호환성 유지)"""
    try:
        with open(LINK_LOG_FILE, "r") as f:
            data = json.load(f)
            link = data["link"]
            if USE_SIGNAL_AVG and "signal_avg" in link:
                rssi_raw = link["signal_avg"]
            else:
                rssi_raw = link["signal"]
            result = {
                "bssid": link["address"].strip().lower(),
                "freq": int(data["info"]["freq"]),
                "rssi": int(rssi_raw.replace(" dBm", "")),
            }
            return result
    except Exception as e:
        return None


def load_channel_info():
    try:
        with open(LINK_LOG_FILE, "r") as f:
            data = json.load(f)
            return data.get("channel_info", {})
    except Exception as e:
        print(f"[ERROR] Failed to load channel info from link log: {e}")
        return {}


def get_current_ssid():
    try:
        with open(LINK_LOG_FILE, "r") as f:
            data = json.load(f)
            return data["info"]["ssid"].strip()
    except Exception as e:
        logger.message(
            "err", f"[{IFACE}] Failed to get current SSID from link log: {e}", _EXTRA_()
        )
        return None


def get_current_bssid():
    try:
        with open(LINK_LOG_FILE, "r") as f:
            data = json.load(f)
            return data["link"]["address"].strip().lower()
    except Exception as e:
        logger.message(
            "err",
            f"[{IFACE}] Failed to get current BSSID from link log: {e}",
            _EXTRA_(),
        )
        return None


def get_allowed_ssids(live_ssid=None):
    """로밍 허용 SSID 목록: 라이브 연결 SSID + conf 기본 ssid(WPA_SSID) + roaming.extra_ssids.
    라이브 SSID를 항상 1차로 포함해 cross-SSID connect 후 conf ssid가 교체되어도 현재
    네트워크 후보를 잃지 않는다(passive_roam.py와 동일 원칙).
    extra_ssids가 비고 라이브==WPA_SSID면 [WPA_SSID] → 기존 단일 SSID 동작(무회귀).
    GENERATE_NETWORK_BLOCKS=false(모드 B)면 extra_ssids를 무시(1차 게이트, spec §3.5)."""
    allowed = []
    for s in (live_ssid, WPA_SSID):
        if s and s not in allowed:
            allowed.append(s)
    if GENERATE_NETWORK_BLOCKS:
        for s in EXTRA_SSIDS:
            if s and s not in allowed:
                allowed.append(s)
    return allowed


def should_cross_connect(best_ssid, live_ssid):
    """메인루프 cross-SSID 분기 게이트(spec §3.5, 2차 방어).
    GENERATE_NETWORK_BLOCKS=false(모드 B)면 best_ap가 extra SSID여도 cross connect
    진입을 차단. 모드 A에서는 best_ap.ssid가 **라이브 연결 SSID와 다를 때만** True.

    기준을 base 집합(live+conf WPA_SSID)이 아닌 live 단일로 두는 이유(T5 실측, 2026-07-20):
    모드 A에서 extra SSID로 전환한 뒤(live=extra) conf 기본 SSID(WPA_SSID)로 되돌아가는
    후보가 base 포함 판정 탓에 same-SSID(wpa_cli roam) 경로로 오라우팅되어, supplicant가
    현재 SSID 불일치로 FAIL을 무한 반복했다(복귀 불가). cross 여부의 진실은 '현재 결합
    SSID와 다른가'뿐이다 — passive_roam의 cross 판정과 동일 원칙. live 미상이면 보수적으로
    False(same 경로 → 진짜 cross였다면 새 성공판정이 FAIL로 정확히 노출)."""
    return bool(
        GENERATE_NETWORK_BLOCKS and best_ssid and live_ssid and best_ssid != live_ssid
    )


# ==============================================================================
# 개선된 get_latest_scan (Load 정보 포함)
# ==============================================================================
def get_latest_scan(st, channel_info_data=None, allowed_ssids=None):
    """Load 정보를 포함한 스캔 결과 반환"""
    if allowed_ssids is None:
        allowed_ssids = [st.get("ssid")] if st.get("ssid") else []
    allowed_set = {s for s in allowed_ssids if s}
    try:
        with open(SCAN_LOG_FILE, "r") as f:
            lines = f.readlines()
    except Exception as e:
        logger.message(
            "err", f"[{IFACE}] Failed to read scan info from ap log: {e}", _EXTRA_()
        )
        return [], None

    timestamp = None
    entries = []
    start_idx = 0

    for i in reversed(range(len(lines))):
        line = lines[i]
        match = re.match(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]", line)
        if match:
            timestamp = match.group(1)
            start_idx = i + 1
            break

    if timestamp is None:
        logger.message("err", f"[{IFACE}] timestamp is not exist", _EXTRA_())
        return [], None

    # Load 정보 매핑을 위한 사전 준비
    load_map = {}
    noise_map = {}
    if ENABLE_LOAD_BASED_ROAM and channel_info_data:
        for freq_str, info in channel_info_data.items():
            noise = info.get("noise", -95)
            busy = info.get("busy_time_ms", 0)
            active = info.get("active_time_ms", 1)
            load = (busy / active) * 100 if active > 0 else 0

            # 채널을 주파수로 변환하여 매핑
            load_map[freq_str] = round(load, 2)
            noise_map[freq_str] = noise

    for line in lines[start_idx:]:
        if re.match(r"^\d{2}\|", line):
            fields = line.strip().split("|")
            if len(fields) >= 7:
                try:
                    channel = int(fields[1].strip())
                    rssi = int(fields[2].strip())
                    ld = int(
                        fields[3].strip()
                    )  # Load from scan (deprecated, using channel_info)
                    bssid = fields[4].strip().lower()
                    ssid = fields[6].strip()
                    rssi_th = WPA_TH_2G if channel < 36 else WPA_TH_5G
                    freq = channel_to_freq(channel)

                    if freq is None:
                        continue

                    freq_str = str(freq)

                    # Load 정보 추가
                    ap_load = load_map.get(freq_str, 0)
                    ap_noise = noise_map.get(freq_str, -95)

                    logger.message(
                        "info",
                        f"[{IFACE}] ssid:{ssid}, bssid:{bssid}, ch:{channel}, freq:{freq}, "
                        f"rssi:{rssi}, th:{rssi_th}, ld:{ld}, load:{ap_load:.1f}%, noise:{ap_noise}",
                        _EXTRA_(),
                    )

                    if ssid in allowed_set and WPA_FREQ and freq_str in WPA_FREQ:
                        entries.append(
                            {
                                "timestamp": timestamp,
                                "channel": channel,
                                "freq": freq,
                                "rssi": rssi,
                                "rssi_th": rssi_th,
                                "ld": ld,
                                "load": ap_load,
                                "noise": ap_noise,
                                "bssid": bssid,
                                "ssid": ssid,
                            }
                        )
                except Exception as e:
                    logger.message(
                        "warn", f"[{IFACE}] scan entry parsing failed: {e}", _EXTRA_()
                    )
                    continue

    candidates = sorted(entries, key=lambda x: x["rssi"], reverse=True)

    i = 0
    for entry in candidates:
        logger.message(
            "info",
            f"[{IFACE}] roam candidate {i}: "
            f"ts={entry['timestamp']}, ssid={entry['ssid']}, bssid={entry['bssid']}, "
            f"ch={entry['channel']}, freq={entry['freq']}, ld={entry['ld']}, "
            f"load={entry.get('load', 0):.1f}%, rssi={entry['rssi']}(th={entry['rssi_th']})",
            _EXTRA_(),
        )
        i += 1

    return candidates, timestamp


def parse_supplicant_conf(path, def_th2g=None, def_th5g=None):
    """
    wpa_supplicant.conf 파싱
    TH 값이 없으면 인자로 받은 기본값 사용 (JSON 우선)

    Args:
        path: conf 파일 경로
        def_th2g: 2.4GHz 기본 임계값 (JSON에서 로드)
        def_th5g: 5GHz 기본 임계값 (JSON에서 로드)

    Returns:
        tuple: (ssid, freqs, th2g, th5g, th_connect)
    """
    ssid = None
    freqs = []
    th2g = None
    th5g = None
    th_connect = None

    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            # 다중블록 모드: 자동생성 센티넬 이전(첫=기본 network 블록)까지만 파싱.
            # 센티넬 이후 extra 블록의 ssid=/scan_freq=/TH 가 기본값을 덮어쓰지 않게 break.
            # (단일블록=센티넬 없음 → 영향 없음. 센티넬은 wifi_init_config_lib.sh 와 동일 prefix.)
            if line.startswith("# >>> wifi_extra_ssid"):
                break
            if line.startswith("ssid=") and not line.startswith("#"):
                try:
                    ssid = line.split("=", 1)[1].strip().strip('"')
                except ValueError:
                    logger.message(
                        "err",
                        f"[{IFACE}] ssid : {ssid} is invalid in {path}",
                        _EXTRA_(),
                    )
                    pass
            elif line.startswith("scan_freq=") and not line.startswith("#"):
                try:
                    freqs = line.split("=", 1)[1].strip().split()
                except ValueError:
                    logger.message(
                        "err",
                        f"[{IFACE}] scan_freq : {freqs} is invalid in {path}",
                        _EXTRA_(),
                    )
                    pass
            elif line.startswith("#!TH_2G="):
                try:
                    th2g = int(line.split("=")[1])
                except ValueError:
                    logger.message(
                        "err",
                        f"[{IFACE}] TH_2G : {th2g} is invalid in {path}",
                        _EXTRA_(),
                    )
                    pass
            elif line.startswith("#!TH_5G="):
                try:
                    th5g = int(line.split("=")[1])
                except ValueError:
                    logger.message(
                        "err",
                        f"[{IFACE}] TH_5G : {th5g} is invalid in {path}",
                        _EXTRA_(),
                    )
                    pass
            elif line.startswith("#!TH_CONNECT="):
                try:
                    th_connect = int(line.split("=")[1])
                except ValueError:
                    logger.message(
                        "err",
                        f"[{IFACE}] TH_CONNECT : {th_connect} is invalid in {path}",
                        _EXTRA_(),
                    )
                    pass

    # wpa_supplicant.conf에 값이 없으면 JSON 기본값 사용, 없으면 코드 기본값
    th2g = (
        th2g
        if th2g is not None
        else (def_th2g if def_th2g is not None else DEFAULT_TH_2G)
    )
    th5g = (
        th5g
        if th5g is not None
        else (def_th5g if def_th5g is not None else DEFAULT_TH_5G)
    )

    return ssid, freqs, th2g, th5g, th_connect


def reload_supplicant_conf_if_changed(path):
    """wpa_cli reconfigure 등으로 wpa_supplicant conf 가 런타임 변경되면 재파싱해
    전역 WPA_SSID/WPA_FREQ/WPA_TH_2G/WPA_TH_5G/WPA_TH_CONNECT 를 갱신한다.

    - mtime 이 바뀐 경우에만 파싱한다(1초 고빈도 루프의 매 tick 파일 I/O 회피).
    - 재파싱 실패 시 직전 캐시 값을 유지한다(wifi_bgscan 의 build() 재로드와 동일 정책).
    conf 를 시작 시 1회만 읽던 기존 동작은 ssid/scan_freq 변경을 동반한 reconfigure
    후 옛 SSID 로 스캔(No Matching APs)하는 stale 로밍을 유발했다.
    """
    global WPA_SSID, WPA_FREQ, WPA_TH_2G, WPA_TH_5G, WPA_TH_CONNECT, WPA_CONF_MTIME
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return  # conf 접근 불가 — 직전 캐시 유지
    if mtime == WPA_CONF_MTIME:
        return  # 변경 없음 — 재파싱 skip
    try:
        ssid, freqs, th2g, th5g, th_connect = parse_supplicant_conf(
            path, def_th2g=DEFAULT_TH_2G, def_th5g=DEFAULT_TH_5G
        )
    except Exception as e:
        logger.message("err", f"[{IFACE}] wpa conf reload failed (keep last): {e}", _EXTRA_())
        return
    changed = (ssid, freqs) != (WPA_SSID, WPA_FREQ) or (th2g, th5g, th_connect) != (WPA_TH_2G, WPA_TH_5G, WPA_TH_CONNECT)
    WPA_SSID, WPA_FREQ, WPA_TH_2G, WPA_TH_5G, WPA_TH_CONNECT = ssid, freqs, th2g, th5g, th_connect
    WPA_CONF_MTIME = mtime
    if changed:
        logger.message(
            "info",
            f"[{IFACE}] wpa conf reloaded (runtime reconfigure): ssid={WPA_SSID}, "
            f"scan_freq={WPA_FREQ}, TH_2G={WPA_TH_2G}, TH_5G={WPA_TH_5G}, TH_CONNECT={WPA_TH_CONNECT}",
            _EXTRA_(),
        )


# 런타임 roaming config reload — SIGHUP 신호로만 트리거(폴링 없음 → 프로덕션 비용 0).
# 로밍 설정 변경은 검증/튜닝 때만 발생하므로 매 루프 파일을 stat 하는 상시 폴링은
# 일반 환경에 불필요한 비용이다. 대신 self-pipe 로 대기 중인 메인루프를 즉시 깨워
# 긴 backoff sleep 중에도 즉시 반영한다(async-signal-safe: 플래그 대입 + os.write 만).
_RELOAD_STATE = {"pending": False}
try:
    # O_CLOEXEC: 데몬이 spawn하는 자식(iw/wpa_cli/mlanutl)이 신호 파이프 fd를 상속하지
    # 않게 한다(subprocess close_fds 기본과 이중 방어). O_NONBLOCK로 핸들러 write/drain 무블록.
    _SIG_PIPE_R, _SIG_PIPE_W = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
except (OSError, AttributeError):  # pipe2 미지원 플랫폼 폴백
    try:
        _SIG_PIPE_R, _SIG_PIPE_W = os.pipe()
        os.set_blocking(_SIG_PIPE_W, False)
        os.set_blocking(_SIG_PIPE_R, False)
    except OSError:
        _SIG_PIPE_R = _SIG_PIPE_W = None


def handle_sighup(signum, frame):
    """SIGHUP: 런타임 reload 요청 플래그 세팅 + self-pipe 로 대기 즉시 해제.
    핸들러는 async-signal-safe 연산만 수행한다(플래그 대입 + non-blocking os.write)."""
    _RELOAD_STATE["pending"] = True
    if _SIG_PIPE_W is not None:
        try:
            os.write(_SIG_PIPE_W, b"x")
        except OSError:
            pass


def interruptible_sleep(seconds):
    """SIGHUP 수신 시 즉시 깨어나는 대기 — 폴링이 아니라 커널 블록(유휴 CPU 0)이며,
    신호가 없으면 seconds 만큼 대기한다. self-pipe 미가용 시 time.sleep 폴백."""
    if seconds is None or seconds <= 0:
        return
    if _SIG_PIPE_R is None:
        time.sleep(seconds)
        return
    try:
        ready, _, _ = select.select([_SIG_PIPE_R], [], [], seconds)
    except (OSError, ValueError):
        time.sleep(seconds)
        return
    if ready:
        try:
            os.read(_SIG_PIPE_R, 4096)  # drain — 다음 신호를 위해 비운다
        except OSError:
            pass


def reload_roaming_config(iface):
    """SIGHUP 수신 시 wifi_init_conf.json 을 1회 재읽어 로밍 설정을 재시작 없이 반영한다
    (검증 시 조건/임계값 라이브 튜닝 + 데몬 상태[핑퐁 이력·backoff·cooldown] 보존).
    폴링이 아니라 명시적 신호 트리거이므로 mtime 디바운스가 불필요하다 — 신호 시점이
    곧 '쓰기 완료'다. 3중 방어:
      1) json.load 선검증: invalid 면 현행 유지(기본값 회귀 금지)+경고 후 무시.
      2) 구조 검증(iface.roaming dict): 없으면 현행 유지+경고.
      3) generate_network_blocks(모드 결정자) / 모드 A 의 extra_ssids 는 부팅 시
         wpa 블록 생성 절차와 결합돼 재시작 전용(경고 후 이전 값 유지).
    인스턴스 파라미터는 재생성이 아닌 필드 갱신 → 이력(roam_history 등) 보존.
    적용 시 WPA_CONF_MTIME 을 리셋해 같은 사이클 wpa conf 재파싱을 유도(JSON DEFAULT_TH_*
    변경을 실제 판정값 WPA_TH_* 까지 전파). 반환: 적용 수행 여부."""
    global GENERATE_NETWORK_BLOCKS, EXTRA_SSIDS, WPA_CONF_MTIME
    global ping_pong_preventer, adaptive_interval, cross_ssid_cooldown, trend_tracker
    try:
        with open(WIFI_INIT_CONF_JSON, "r") as f:
            new_data = json.load(f)
    except (OSError, ValueError) as e:  # ValueError ⊇ JSONDecodeError
        logger.message(
            "warn",
            f"[{iface}] runtime config reload skipped (invalid JSON, keeping current): {e}",
            _EXTRA_(),
        )
        return False
    # 구조 검증: 구문이 valid여도 dict가 아니거나 iface.roaming 섹션이 없으면
    # load_roaming_config가 조용히 전부 기본값을 적용한다(시작 시엔 정상 fallback이나
    # 런타임엔 '현행 유지' 계약 위반) → 여기서 차단. 통과한 new_data를 그대로 전달해
    # 재파싱을 없앤다(검증-적용 사이 파일 교체 TOCTOU 차단).
    if not (
        isinstance(new_data, dict)
        and isinstance(new_data.get(iface), dict)
        and isinstance(new_data[iface].get("roaming"), dict)
    ):
        logger.message(
            "warn",
            f"[{iface}] runtime config reload skipped "
            f"(no valid {iface}.roaming section, keeping current)",
            _EXTRA_(),
        )
        return False
    roam_cfg = new_data[iface]["roaming"]  # 구조 검증 통과 → dict 보장
    old_gen = GENERATE_NETWORK_BLOCKS
    old_extra = list(EXTRA_SSIDS)
    load_roaming_config(iface, data=new_data)
    # generate_network_blocks는 런타임 전환 금지(재시작 전용). 키가 '명시적으로' 다른
    # 값으로 바뀐 경우에만 경고 — 키 부재로 인한 False 수렴은 조용히 복원(오탐 방지).
    # gen을 유지할 때는 그에 연동된 EXTRA_SSIDS(부팅 시 생성된 wpa 블록과 정합)도 함께
    # 원복한다 — 런타임에 extra만 바뀌면 없는 블록으로 select_network가 실패하기 때문.
    if GENERATE_NETWORK_BLOCKS != old_gen:
        if "generate_network_blocks" in roam_cfg:
            logger.message(
                "warn",
                f"[{iface}] generate_network_blocks change ignored at runtime "
                f"(requires daemon restart; keeping {old_gen})",
                _EXTRA_(),
            )
        GENERATE_NETWORK_BLOCKS = old_gen
        EXTRA_SSIDS = list(old_extra)
    elif GENERATE_NETWORK_BLOCKS and EXTRA_SSIDS != old_extra:
        # 모드 A에서 extra_ssids는 부팅 시 생성된 wpa 네트워크 블록에 종속(gen과 동일
        # 사유로 블록 생성이 boot 절차). 런타임에 배열만 바꾸면 블록 없는 SSID로
        # select_network가 실패하므로 재시작 전용으로 취급(현행 유지 + 경고).
        logger.message(
            "warn",
            f"[{iface}] extra_ssids change ignored at runtime in mode A "
            f"(requires daemon restart to regenerate network blocks)",
            _EXTRA_(),
        )
        EXTRA_SSIDS = list(old_extra)
    # 인스턴스 파라미터 갱신(이력 보존) + enable off→on 인스턴스 생성
    if ENABLE_PING_PONG_PREVENTION:
        if ping_pong_preventer is None:
            ping_pong_preventer = PingPongPreventer(PING_PONG_WINDOW, MAX_ROAMS_IN_WINDOW)
        else:
            ping_pong_preventer.window_seconds = PING_PONG_WINDOW
            ping_pong_preventer.max_roams = MAX_ROAMS_IN_WINDOW
    if ENABLE_PREDICTIVE_ROAM:
        if trend_tracker is None:
            trend_tracker = RSSITrendTracker(TREND_WINDOW_SIZE, TREND_HISTORY_MAX_AGE)
        else:
            trend_tracker.window_size = TREND_WINDOW_SIZE
            trend_tracker.max_age = TREND_HISTORY_MAX_AGE
    if ENABLE_ADAPTIVE_INTERVAL:
        if adaptive_interval is None:
            adaptive_interval = AdaptiveInterval(MIN_CHECK_INTERVAL, MAX_CHECK_INTERVAL)
        else:
            adaptive_interval.min_interval = MIN_CHECK_INTERVAL
            adaptive_interval.max_interval = MAX_CHECK_INTERVAL
    # cross_ssid_cooldown은 의도적으로 갱신만(생성 없음): 별도 enable이 없고 존재가
    # GENERATE_NETWORK_BLOCKS(런타임 전환 금지, 재시작 전용)에 연동되므로
    # 런타임에 None→생성이 필요한 상황 자체가 없다.
    if cross_ssid_cooldown is not None:
        cross_ssid_cooldown.retry_count = max(0, ROAM_CROSS_FAIL_RETRY_COUNT)
    WPA_CONF_MTIME = None  # 같은 사이클 wpa conf 재파싱 → 새 DEFAULT_TH_* 로 WPA_TH_* 재산출
    logger.message("notice", f"[{iface}] runtime roaming config reloaded (SIGHUP)", _EXTRA_())
    return True


def get_my_ip(iface):
    try:
        result = subprocess.run(
            ["ip", "-4", "addr", "show", iface],
            capture_output=True,
            text=True,
            timeout=2,
        )
        for line in result.stdout.splitlines():
            if "inet " in line:
                parts = line.strip().split()
                for part in parts:
                    if part.startswith("inet"):
                        ip = parts[parts.index(part) + 1].split("/")[0]
                        return ip
        return None
    except Exception:
        return None


def get_recent_peers(iface, count=5):
    peers = []
    try:
        result = subprocess.run(
            ["ip", "neigh", "show", "dev", iface],
            capture_output=True,
            text=True,
            timeout=2,
        )
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 1:
                ip = parts[0]
                if "." in ip:
                    peers.append(ip)
                    if len(peers) >= count:
                        break
    except Exception:
        pass
    return peers


def optimize_post_roam_connectivity(iface):
    if not ENABLE_POST_ROAM_ARP_OPTIMIZATION:
        return

    my_ip = get_my_ip(iface)
    if not my_ip:
        logger.message(
            "debug",
            f"[{iface}] Post-roam optimization skipped: no IP address",
            _EXTRA_(),
        )
        return

    try:
        logger.message(
            "info",
            f"[{iface}] Sending gratuitous ARP ({POST_ROAM_GARP_COUNT} times)",
            _EXTRA_(),
        )
        for i in range(POST_ROAM_GARP_COUNT):
            subprocess.run(
                [
                    "arping",
                    "-U",
                    "-c",
                    "1",
                    "-w",
                    str(POST_ROAM_GARP_WAIT),
                    "-I",
                    iface,
                    my_ip,
                ],
                capture_output=True,
                timeout=POST_ROAM_GARP_WAIT + 1,
            )
            if i < POST_ROAM_GARP_COUNT - 1:
                time.sleep(0.1)
    except Exception as e:
        logger.message(
            "debug",
            f"[{iface}] Gratuitous ARP failed: {e}",
            _EXTRA_(),
        )

    if ENABLE_POST_ROAM_PEER_WARMUP:
        peers = get_recent_peers(iface, POST_ROAM_PEER_COUNT)
        if peers:
            logger.message(
                "info",
                f"[{iface}] ARP warm-up for {len(peers)} peers",
                _EXTRA_(),
            )
            for peer_ip in peers:
                try:
                    subprocess.run(
                        [
                            "arping",
                            "-c",
                            "1",
                            "-w",
                            str(POST_ROAM_PEER_WAIT),
                            "-I",
                            iface,
                            peer_ip,
                        ],
                        capture_output=True,
                        timeout=POST_ROAM_PEER_WAIT + 1,
                    )
                except Exception:
                    pass


# ==============================================================================
# 개선된 roam_to_bssid (Ping-pong 방지 포함)
# ==============================================================================
def roam_to_bssid(from_bssid, to_bssid, channel=None, freq=None, rssi=None):
    """
    Ping-pong 확인 후 로밍 실행 (channel/freq/rssi=대상 AP 스캔 권위값)

    성공 판정은 `wpa_cli roam`의 종료코드가 아니라 (1) 응답 텍스트가 "OK"(명령 수락)이고
    (2) 이후 wpa_cli status 폴링으로 wpa_state=COMPLETED@target(재결합 완료)이 확인될 때만
    성공으로 본다. wpa_cli는 supplicant가 "FAIL"을 응답해도 exit 0을 주므로(_wpa_ctrl_command),
    returncode==0 판정은 실패를 성공으로 오인하고 add_roam/notify_roam까지 잘못 수행한다.

    Args:
        from_bssid: 현재 연결된 BSSID
        to_bssid: 로밍할 BSSID

    Returns:
        bool: 로밍 성공(재결합 확인) 여부
    """
    # Ping-pong 확인
    if ENABLE_PING_PONG_PREVENTION and ping_pong_preventer:
        if ping_pong_preventer.is_ping_pong(from_bssid, to_bssid):
            logger.message(
                "info",
                f"[{IFACE}] Roam blocked: ping-pong prevention ({from_bssid} → {to_bssid})",
                _EXTRA_(),
            )
            return False

    logger.message("emerg", f"[{IFACE}] Roaming: {from_bssid} → {to_bssid}", _EXTRA_())

    try:
        result = subprocess.run(
            ["wpa_cli", "-i", IFACE, "roam", to_bssid],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        logger.message("err", f"[{IFACE}] Roam timeout: {to_bssid}", _EXTRA_())
        return False
    except Exception as e:
        logger.message("err", f"[{IFACE}] Roam error: {e}", _EXTRA_())
        return False

    # 1차 게이트: wpa_cli는 "FAIL" 응답에도 exit 0을 주므로 returncode가 아니라 응답
    # 텍스트로 '명령 수락(OK)' 여부를 판정한다. FAIL/FAIL-BUSY면 즉시 실패로 본다.
    reply = (result.stdout or "").strip()
    if result.returncode != 0 or reply.split("\n", 1)[0].strip() != "OK":
        detail = reply or (result.stderr or "").strip() or f"rc={result.returncode}"
        logger.message(
            "err", f"[{IFACE}] Roam rejected by supplicant: {detail}", _EXTRA_()
        )
        return False

    # 2차 게이트(권위): "OK"는 명령 수락일 뿐 재결합 완료가 아니다. wpa_cli status를
    # 폴링해 wpa_state=COMPLETED 이고 결합 BSS가 목표와 일치할 때만 성공으로 확정한다.
    if not confirm_roam(IFACE, to_bssid):
        logger.message(
            "err",
            f"[{IFACE}] Roam not confirmed (BSS != target): {to_bssid}",
            _EXTRA_(),
        )
        return False

    # 재결합이 확인된 경우에만 카운터/통지/최적화 수행 — 가짜 성공에 의한
    # ping-pong 카운터 오염과 opcd 오통지를 제거한다.
    if ENABLE_PING_PONG_PREVENTION and ping_pong_preventer:
        ping_pong_preventer.add_roam(from_bssid, to_bssid)

    logger.message("info", f"[{IFACE}] Roam successful (confirmed): {to_bssid}", _EXTRA_())

    optimize_post_roam_connectivity(IFACE)

    notify_roam(IFACE, from_bssid, to_bssid,
                channel=channel, freq=freq, rssi=rssi)

    return True


def connect_to_ssid(iface, to_ssid, from_bssid, to_bssid):
    """다른 SSID로 로밍: wifi <iface> connect (conf ssid 교체→reconfigure→reassociate).
    wpa_cli roam은 같은 network 블록(SSID) 내 BSS만 전환하므로, 다른 SSID 전환은 connect로 처리.
    - extra_ssids가 현재와 같은 psk/key_mgmt를 공유한다는 전제(아니면 connect 후 인증 실패).
    - freq 인자는 일부러 생략한다: wifi connect에 단일 freq를 주면 conf의 multi-freq
      scan_freq/freq_list가 그 한 채널로 collapse되어 이후 스캔 범위가 축소된다. ssid만
      교체하고 scan_freq는 유지(후보는 이미 WPA_FREQ 내 채널이라 연결 가능).
    - ping-pong 예산은 same-SSID 로밍과 공유(의도): cross-SSID도 BSSID 기반 카운트에 합산.
    - ping-pong 차단 시 None 반환(전환 미시도 — 실패 아님): 호출자가 결과를
      record_cross_ssid_result에 넘겨도 cooldown에 실패로 등록되지 않게 route와 계약 통일."""
    if ENABLE_PING_PONG_PREVENTION and ping_pong_preventer:
        if ping_pong_preventer.is_ping_pong(from_bssid, to_bssid):
            logger.message(
                "info",
                f"[{IFACE}] Cross-SSID roam blocked: ping-pong ({from_bssid} → {to_bssid})",
                _EXTRA_(),
            )
            return None

    logger.message(
        "notice",
        f"[{IFACE}] Cross-SSID roam: connect ssid={to_ssid} ({from_bssid} → {to_bssid})",
        _EXTRA_(),
    )
    try:
        result = subprocess.run(
            ["/usr/local/bin/wifi", iface, "connect", to_ssid],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            if ENABLE_PING_PONG_PREVENTION and ping_pong_preventer:
                ping_pong_preventer.add_roam(from_bssid, to_bssid)
            logger.message(
                "info", f"[{IFACE}] Cross-SSID connect successful: {to_ssid}", _EXTRA_()
            )
            optimize_post_roam_connectivity(IFACE)
            return True
        else:
            logger.message(
                "err",
                f"[{IFACE}] Cross-SSID connect failed (conf ssid may diverge from live until next tick): {result.stderr.strip()}",
                _EXTRA_(),
            )
            return False
    except subprocess.TimeoutExpired:
        logger.message("err", f"[{IFACE}] Cross-SSID connect timeout: {to_ssid}", _EXTRA_())
        return False
    except Exception as e:
        logger.message("err", f"[{IFACE}] Cross-SSID connect error: {e}", _EXTRA_())
        return False


def _parse_network_id_for_ssid(list_networks_stdout, to_ssid):
    """`wpa_cli list_networks` 출력에서 to_ssid와 정확히 일치하는 network id 반환.
    출력 형식: 첫 줄은 헤더("network id / ssid / bssid / flags"), 이후 탭 구분
    "<id>\t<ssid>\t<bssid>\t<flags>". ssid 정확 일치만 매칭(부분문자열 금지).
    여러 블록이 같은 ssid면 첫 번째(파일 순서=우선순위) id 반환. 없으면 None."""
    for line in list_networks_stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        nid, ssid = parts[0].strip(), parts[1].strip()
        if not nid.isdigit():
            continue  # 헤더 줄 skip
        if ssid == to_ssid:
            return nid
    return None


def _enable_network_all(iface):
    """select_network 후 다른(fallback) 블록을 다시 enable해 끊김 시 네이티브 fallback 복원.
    연결 중 enable_network all은 재스캔을 트리거하지 않음(안전). 실패는 로깅만."""
    try:
        en = subprocess.run(
            ["wpa_cli", "-i", iface, "enable_network", "all"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if en.returncode != 0:
            logger.message(
                "err",
                f"[{iface}] enable_network all failed: {en.stderr.strip()}",
                _EXTRA_(),
            )
    except Exception as e:
        logger.message("err", f"[{iface}] enable_network all error: {e}", _EXTRA_())


def select_network_for_ssid(iface, to_ssid):
    """모드 A(다중 블록) cross-SSID 전환: conf ssid를 교체하지 않고 메모리 상태만 전환.
    list_networks로 to_ssid의 network id 조회 → select_network <id> → wpa_state=COMPLETED
    폴링(최대 ~3s) → enable_network all(fallback 후보 복원). conf 파일 불변(save_config 미호출).
    id 조회 실패/타임아웃/예외 시 False 반환(절대 ssid 교체 안 함, 다음 tick 재평가).
    enable_network all은 실패 경로에서도(폴링 중 timeout/예외 포함) 호출해 fallback 블록을 복원한다."""
    # selected=True 이후 경로(select_network 성공)에서 예외가 나면 다른 블록이 disabled로
    # 남으므로, except 핸들러에서 반드시 _enable_network_all 로 fallback 후보를 복원한다.
    selected = False
    try:
        lst = subprocess.run(
            ["wpa_cli", "-i", iface, "list_networks"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if lst.returncode != 0:
            logger.message(
                "err",
                f"[{iface}] select_network: list_networks failed: {lst.stderr.strip()}",
                _EXTRA_(),
            )
            return False

        nid = _parse_network_id_for_ssid(lst.stdout, to_ssid)
        if nid is None:
            logger.message(
                "err",
                f"[{iface}] select_network: no network block for ssid={to_ssid} "
                f"(conf unchanged, retry next tick)",
                _EXTRA_(),
            )
            return False

        logger.message(
            "notice",
            f"[{iface}] Cross-SSID select_network: id={nid} ssid={to_ssid}",
            _EXTRA_(),
        )
        sel = subprocess.run(
            ["wpa_cli", "-i", iface, "select_network", nid],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if sel.returncode != 0:
            logger.message(
                "err",
                f"[{iface}] select_network failed (id={nid}): {sel.stderr.strip()}",
                _EXTRA_(),
            )
            # select_network은 다른 블록을 disable시키므로 실패해도 후보 복원 필요
            _enable_network_all(iface)
            return False
        # 이 시점부터 다른 블록이 disabled 상태 → 어떤 경로로 나가든 복원 책임 발생
        selected = True

        # wpa_state=COMPLETED 폴링 (최대 ~3s: 0.5s × 6회)
        completed = False
        for _ in range(6):
            stt = subprocess.run(
                ["wpa_cli", "-i", iface, "status"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            state = None
            for ln in stt.stdout.splitlines():
                if ln.startswith("wpa_state="):
                    state = ln.split("=", 1)[1].strip()
                    break
            if state == "COMPLETED":
                completed = True
                break
            time.sleep(0.5)

        # 성공/실패 무관 fallback 후보(다른 블록) 복원
        _enable_network_all(iface)

        if completed:
            logger.message(
                "info",
                f"[{iface}] Cross-SSID select_network successful: {to_ssid} (id={nid})",
                _EXTRA_(),
            )
            optimize_post_roam_connectivity(iface)
            return True

        logger.message(
            "err",
            f"[{iface}] select_network: wpa_state not COMPLETED for {to_ssid} "
            f"(id={nid}), candidates restored, retry next tick",
            _EXTRA_(),
        )
        return False
    except subprocess.TimeoutExpired:
        logger.message(
            "err", f"[{iface}] select_network timeout: {to_ssid}", _EXTRA_()
        )
        # 폴링 중 timeout이면 select_network이 이미 다른 블록을 disable한 상태이므로 복원
        if selected:
            _enable_network_all(iface)
        return False
    except Exception as e:
        logger.message("err", f"[{iface}] select_network error: {e}", _EXTRA_())
        if selected:
            _enable_network_all(iface)
        return False


def route_cross_ssid_transition(iface, to_ssid, from_bssid, to_bssid):
    """cross-SSID 전환 수단 라우팅. 모드 A(GENERATE_NETWORK_BLOCKS=True)는 conf 불변
    select_network_for_ssid, 모드 B(False)는 기존 connect_to_ssid(외부 wifi connect).
    배타적 2-모드라 한 모드에서 다른 경로는 진입 불가.

    ping-pong 방지(데몬 자동 전환 전용, T6 관찰 반영): same-SSID roam_to_bssid와
    동일하게 진입 전 is_ping_pong 차단 + 성공 시 add_roam 카운트(예산은 same/cross
    공유 — BSSID 기반 왕복이면 SSID 불문 핑퐁). 수동 로밍(passive_roam)·망전환
    (wifi connect 명령)은 이 함수를 거치지 않으므로 비대상. 차단은 '전환 실패'가
    아니므로 None을 반환해 cross cooldown(record_cross_ssid_result)에 실패로
    등록되지 않게 한다(cooldown=실패 SSID 억제, ping-pong=왕복 빈도 억제 — 별개).

    Returns:
        True=전환 확인 / False=전환 실패 / None=ping-pong 차단(이번 tick 스킵)
    """
    if ENABLE_PING_PONG_PREVENTION and ping_pong_preventer:
        if ping_pong_preventer.is_ping_pong(from_bssid, to_bssid):
            logger.message(
                "info",
                f"[{iface}] Cross-SSID roam blocked: ping-pong prevention "
                f"({from_bssid} → {to_bssid})",
                _EXTRA_(),
            )
            return None

    if GENERATE_NETWORK_BLOCKS:
        ok = select_network_for_ssid(iface, to_ssid)
    else:
        # 모드 B: connect_to_ssid가 내부에서 자체 check+add_roam 수행(기존 동작 유지)
        ok = connect_to_ssid(iface, to_ssid, from_bssid, to_bssid)
    # cross-SSID는 두 모드 모두 펌웨어가 실제 결합 BSS를 자율 선택하므로 to_bssid를
    # 미리 알 수 없다. link.json은 ~1s 주기 비동기 갱신이라 전환 직후엔 이전 AP가
    # 남을 수 있어(stale ap_mac), 성공 직후 wpa_cli status(권위)로 실 결합 BSS를
    # 조회해 넘긴다. 조회 실패 시 "" → link.address 폴백(종전 동작), 무회귀.
    if ok:
        # 소문자 정규화: PingPongPreventer가 (from,to) 문자열 비교로 왕복을 감지하므로
        # 스캔 파서(.lower())·link.json(.lower())과 표기를 일치시킨다.
        assoc = (get_associated_bssid(iface) or "").strip().lower()
        if GENERATE_NETWORK_BLOCKS and ENABLE_PING_PONG_PREVENTION and ping_pong_preventer:
            # 실 결합 BSS(권위)로 카운트, 조회 실패 시 목표 to_bssid 폴백.
            ping_pong_preventer.add_roam(from_bssid, assoc or to_bssid)
        notify_roam(iface, from_bssid, assoc)
    return ok


def score_ap(ap, rssi_weight=1.0, ld_weight=1.0):
    normalized_rssi = ap["rssi"] + 100  # -40 → 60
    normalized_ld = ap["ld"]  # 0~100
    score = rssi_weight * normalized_rssi - ld_weight * normalized_ld
    return score


# ==============================================================================
# 개선된 로밍 조건 확인 함수
# ==============================================================================
def check_roam_conditions(station, roam_ap, trend):
    """
    개선된 로밍 조건 확인

    Args:
        station: 현재 연결 정보
        roam_ap: 로밍 후보 AP 정보
        trend: RSSI 추세

    Returns:
        tuple: (should_roam, reason)
    """
    # 기본 조건: RSSI 차이
    rssi_diff = roam_ap["rssi"] - station["rssi"]

    if rssi_diff < DIFF_TH:
        return (False, f"RSSI diff too small: {rssi_diff}dB < {DIFF_TH}dB")

    # Load 조건
    if ENABLE_LOAD_BASED_ROAM:
        current_load = station.get("load", 0)
        roam_load = roam_ap.get("load", 0)

        # 로밍 대상 AP의 Load가 너무 높으면 제외
        if roam_load > MAX_ROAM_LOAD:
            return (False, f"Target AP load too high: {roam_load}% > {MAX_ROAM_LOAD}%")

        # 현재 AP보다 Load가 너무 높으면 제외
        if roam_load > current_load + LOAD_DIFF_THRESHOLD:
            return (
                False,
                f"Target AP load higher: {roam_load}% > {current_load}% + {LOAD_DIFF_THRESHOLD}%",
            )

    # 추세 기반 조건 완화 (하락 추세면 더 쉽게 로밍)
    if ENABLE_PREDICTIVE_ROAM and trend == RSSITrendTracker.TREND_FALLING:
        # 하락 추세면 RSSI 차이 조건을 3dB 완화
        if rssi_diff >= (DIFF_TH - 3):
            return (True, f"Falling trend, RSSI diff: {rssi_diff}dB")

    return (True, f"RSSI diff: {rssi_diff}dB")


# ==============================================================================
# 개선된 main 함수
# ==============================================================================
def main():
    global trend_tracker, ping_pong_preventer, adaptive_interval, cross_ssid_cooldown

    # 초기화
    if ENABLE_PREDICTIVE_ROAM:
        trend_tracker = RSSITrendTracker(TREND_WINDOW_SIZE, TREND_HISTORY_MAX_AGE)
        logger.message(
            "info",
            f"[{IFACE}] Predictive roaming enabled (boost={PREDICTIVE_THRESHOLD_BOOST}dB)",
            _EXTRA_(),
        )

    if ENABLE_PING_PONG_PREVENTION:
        ping_pong_preventer = PingPongPreventer(PING_PONG_WINDOW, MAX_ROAMS_IN_WINDOW)
        logger.message(
            "info",
            f"[{IFACE}] Ping-pong prevention enabled (max={MAX_ROAMS_IN_WINDOW}/{PING_PONG_WINDOW}s)",
            _EXTRA_(),
        )

    if GENERATE_NETWORK_BLOCKS:
        cross_ssid_cooldown = CrossSsidCooldown(ROAM_CROSS_FAIL_RETRY_COUNT)
        logger.message(
            "info",
            f"[{IFACE}] Cross-SSID fail cooldown enabled (retry_count={ROAM_CROSS_FAIL_RETRY_COUNT})",
            _EXTRA_(),
        )
    else:
        # 모드 B(generate_network_blocks=false)는 cross-SSID 자동전환이 비활성(should_cross_connect
        # 항상 False)이라 cooldown 미사용. 인스턴스를 만들지 않아 'enabled' 오인 로그를 피한다.
        cross_ssid_cooldown = None

    if ENABLE_ADAPTIVE_INTERVAL:
        adaptive_interval = AdaptiveInterval(MIN_CHECK_INTERVAL, MAX_CHECK_INTERVAL)
        logger.message(
            "info",
            f"[{IFACE}] Adaptive interval enabled (min={MIN_CHECK_INTERVAL}s, max={MAX_CHECK_INTERVAL}s)",
            _EXTRA_(),
        )

    if ENABLE_LOAD_BASED_ROAM:
        logger.message(
            "info",
            f"[{IFACE}] Load-based roaming enabled (max_load={MAX_ROAM_LOAD}%)",
            _EXTRA_(),
        )

    # 후보없음 점증 backoff 상태(spec §4). streak=연속 후보없음 tick 수,
    # last_backoff_cap_ts=상한 첫 도달 시각(시간 기반 점감용), hint_state=bgscan hint mtime 추적.
    no_candidate_streak = 0
    last_backoff_cap_ts = None
    hint_state = {"hint_mtime": None}

    while True:
        # SIGHUP 수신 시에만 wifi_init_conf.json 재읽어 반영(폴링 없음 → 프로덕션 비용 0).
        # 대기(interruptible_sleep)가 신호로 즉시 깨어나므로 긴 backoff 중에도 즉시 반영.
        # 먼저 플래그를 내린 뒤 reload — 처리 중 새 신호는 다음 사이클에 latest 재읽어 커버.
        if _RELOAD_STATE["pending"]:
            _RELOAD_STATE["pending"] = False
            reload_roaming_config(IFACE)
        # wpa_cli reconfigure 등으로 conf 가 런타임 변경됐으면 재파싱(mtime 변화 시에만).
        # ssid/scan_freq/TH 캐시를 최신화해 옛 SSID 로 스캔하는 stale 로밍을 방지한다.
        reload_supplicant_conf_if_changed(WPA_CONF_FILE)
        # bgscan이 새 후보 AP를 발견(hint touch)하면 즉시 backoff 해제(고속 복귀).
        if roam_hint_touched(hint_state):
            no_candidate_streak = 0
            last_backoff_cap_ts = None

        # Load 정보 포함하여 연결 상태 확인
        station = get_link_info_with_load()

        if not station:
            interruptible_sleep(CHECK_INTERVAL)
            continue

        rssi = station.get("rssi")
        if not is_valid_rssi(rssi):
            interruptible_sleep(CHECK_INTERVAL)
            continue

        # RSSI 추적
        if ENABLE_PREDICTIVE_ROAM and trend_tracker:
            trend_tracker.add_sample(rssi)
            trend = trend_tracker.get_trend()
            avg_rssi = trend_tracker.get_avg_rssi()
        else:
            trend = RSSITrendTracker.TREND_STABLE
            avg_rssi = rssi

        # 대역별 임계값 설정
        if station["freq"] < 5000:
            base_threshold = WPA_TH_2G
        else:
            base_threshold = WPA_TH_5G

        if base_threshold is None:
            interruptible_sleep(CHECK_INTERVAL)
            continue

        # 예측형 로밍: 하락 추세 시 임계값 조정
        if ENABLE_PREDICTIVE_ROAM and trend == RSSITrendTracker.TREND_FALLING:
            predictive_threshold = base_threshold + PREDICTIVE_THRESHOLD_BOOST
            trend_str = "falling"
        elif ENABLE_PREDICTIVE_ROAM and trend == RSSITrendTracker.TREND_RISING:
            predictive_threshold = base_threshold
            trend_str = "rising"
        else:
            predictive_threshold = base_threshold
            trend_str = "stable"

        station["rssi_th"] = base_threshold

        # 로밍 조건 확인
        if station["rssi"] >= predictive_threshold:
            set_flag(0, ROAM_CONDITION_FLAG)
            # 신호 양호(로밍 불필요) → 후보없음 streak 해제. 다음 악화 시 시작값부터 backoff.
            no_candidate_streak = 0
            last_backoff_cap_ts = None

            if ENABLE_ADAPTIVE_INTERVAL and adaptive_interval:
                interval = adaptive_interval.update(rssi, base_threshold, trend)
            else:
                interval = CHECK_INTERVAL

            interruptible_sleep(interval)
            continue

        # 로밍 조건 발생
        logger.message(
            "info",
            f"[{IFACE}] roaming condition: {station['rssi']} < {predictive_threshold} "
            f"(base={base_threshold}, trend={trend_str}) "
            f"bssid={station['bssid']}, load={station.get('load', 0):.1f}%",
            _EXTRA_(),
        )
        set_flag(1, ROAM_CONDITION_FLAG)

        # 인터벌 계산 (루프 내 모든 경로에서 공유)
        if ENABLE_ADAPTIVE_INTERVAL and adaptive_interval:
            interval = adaptive_interval.update(rssi, base_threshold, trend)
        else:
            interval = CHECK_INTERVAL

        # 주변 AP 스캔 — iw scan(테이블 충전) + wpa_cli scan_results(=BSS 테이블) 후보.
        # mlanutl setuserscan은 wpa_supplicant BSS 테이블을 채우지 않아 이후 wpa_cli roam이
        # 대상 BSS를 못 찾고 FAIL했다(근본원인: 네이티브 bgscan 제거로 테이블이 자동 갱신되지
        # 않는데 판정 스캔마저 테이블을 안 채움). iw scan은 테이블을 채우고, 후보를 테이블
        # 그 자체(scan_results)에서 뽑으므로 roam 대상이 항상 테이블에 존재한다.
        if WPA_SSID and WPA_FREQ:
            # station["ssid"]는 get_link_info_with_load가 link.json info.ssid(실제 연결 SSID)로 채움
            if not station.get("ssid"):
                station["ssid"] = WPA_SSID
            ap_lines = iw_scan_to_ap_lines(get_allowed_ssids(station.get("ssid")), WPA_FREQ)
            try:
                with open(LAST_SCAN_TIME_FILE, "w") as f:
                    f.write(str(time.time()))
            except Exception:
                pass

            if ap_lines:
                save_with_timestamp(SCAN_LOG_FILE, ap_lines)
                # freq.log(채널 load)는 mlanutl 전용 출력이라 미생산. 로밍 LOAD 데이터는
                # link.json channel_info에서 오므로 판정 영향 없음(로그용 freq.log는
                # wifi_logger_scan 데몬이 별도 생산).
            else:
                backoff, no_candidate_streak, last_backoff_cap_ts = (
                    advance_no_candidate_backoff(
                        no_candidate_streak, last_backoff_cap_ts
                    )
                )
                logger.message(
                    "err",
                    f"[{IFACE}] scan failed (no-candidate backoff={backoff}s, "
                    f"streak={no_candidate_streak})",
                    _EXTRA_(),
                )
                interruptible_sleep(backoff)
                continue

        # Load 정보 가져오기
        channel_info_data = (
            station.get("channel_info") if ENABLE_LOAD_BASED_ROAM else None
        )

        # 스캔 결과 파싱
        entries, timestamp = get_latest_scan(station, channel_info_data, get_allowed_ssids(station.get("ssid")))

        if not entries:
            backoff, no_candidate_streak, last_backoff_cap_ts = (
                advance_no_candidate_backoff(no_candidate_streak, last_backoff_cap_ts)
            )
            logger.message(
                "err",
                f"[{IFACE}] No Matching APs found in latest scan "
                f"(no-candidate backoff={backoff}s, streak={no_candidate_streak})",
                _EXTRA_(),
            )
            interruptible_sleep(backoff)
            continue

        # 로밍 후보 평가
        best_ap = None
        best_reason = ""
        best_score = 0

        # cross-SSID 판정 기준 = 라이브 연결 SSID 단일(T5: base에 WPA_SSID를 넣으면
        # conf 기본 SSID 복귀가 same으로 오판되어 FAIL 루프). 루프와 아래 로밍 분기에서 공유.
        live_ssid = station.get("ssid")

        for roam_ap in entries:
            if roam_ap["bssid"] == station["bssid"]:
                continue

            # cross-SSID cooldown(모드 A): 전환 실패한 extra SSID는 일정 시간 후보에서 제외해
            # select_network 진동을 차단한다. same-SSID(현재 ESS) BSS roam은 should_cross_connect가
            # cross 대상만 True이므로 영향 없음. cooldown SSID가 유일 후보면 best_ap=None → 후보없음 backoff.
            ap_ssid = roam_ap.get("ssid", "")  # .get으로 일관 접근(조건 순서 변경 시 KeyError 방지)
            if (
                cross_ssid_cooldown is not None
                and should_cross_connect(ap_ssid, live_ssid)
                and cross_ssid_cooldown.is_cooling(ap_ssid)
            ):
                continue

            # 로밍 조건 확인
            should_roam, reason = check_roam_conditions(station, roam_ap, trend)

            if should_roam:
                rssi_diff = roam_ap["rssi"] - station["rssi"]

                # 점수 계산 (RSSI 차이 + Load 개선 정도)
                score = rssi_diff * 10

                if ENABLE_LOAD_BASED_ROAM:
                    current_load = station.get("load", 0)
                    roam_load = roam_ap.get("load", 0)
                    load_improvement = current_load - roam_load
                    score += load_improvement * 2

                if score > best_score:
                    best_ap = roam_ap
                    best_reason = reason
                    best_score = score

                logger.message(
                    "info",
                    f"[{IFACE}] Roam candidate: {roam_ap['bssid']}, "
                    f"rssi={roam_ap['rssi']}dB (diff={rssi_diff}dB), "
                    f"load={roam_ap.get('load', 0):.1f}%, "
                    f"reason={reason}, score={score:.1f}",
                    _EXTRA_(),
                )
            else:
                logger.message(
                    "info",
                    f"[{IFACE}] Roam skipped: {roam_ap['bssid']}, {reason}",
                    _EXTRA_(),
                )

        # 최적 AP로 로밍
        if best_ap:
            # 후보 발견 → backoff 리셋(spec §4 reset). 다음 후보없음은 시작값부터.
            no_candidate_streak = 0
            last_backoff_cap_ts = None
            logger.message(
                "emerg",
                f"[{IFACE}] Roaming: {station['bssid']} → {best_ap['bssid']}, "
                f"reason={best_reason}, score={best_score:.1f}, "
                f"{best_ap['ssid']}, {best_ap['rssi']}dB (ch={best_ap['freq']})",
                _EXTRA_(),
            )

            # 라우팅 판단은 위에서 선계산한 live_ssid(라이브 연결 SSID)를 공유.
            # should_cross_connect 게이트(모드 A AND live와 다른 SSID)면 cross(select_network),
            # 아니면 무중단 roam. 모드 B(generate=false)는 cross 항상 차단(spec §3.5 2차 게이트).
            if should_cross_connect(best_ap.get("ssid"), live_ssid):
                # 다른(extra) SSID → 모드 A select_network(conf 불변). 성공/실패를 cooldown에 등록:
                # 실패 누적 시 그 SSID를 후보에서 제외해 deauth 진동을 차단(spec §3.2).
                ok = route_cross_ssid_transition(
                    IFACE, best_ap["ssid"], station["bssid"], best_ap["bssid"]
                )
                # 전환 결과를 cooldown에 반영(성공→clear / 실패→register). post_sleep=실패 후
                # 메인루프가 대기하는 시간(ROAM_SUCCESS_SLEEP+interval)을 반영해, 그 sleep 동안
                # cooldown이 만료돼 무효화되는 것을 방지. cooldown None(모드 B)이면 무동작.
                record_cross_ssid_result(
                    cross_ssid_cooldown, best_ap["ssid"], ok, ROAM_SUCCESS_SLEEP + interval
                )
                # 로밍 성공 정착 대기(의도적 비-중단): 이 짧은 settle 중 SIGHUP이 와도
                # 직후 interruptible_sleep(interval) 또는 다음 루프 top에서 반영된다.
                time.sleep(ROAM_SUCCESS_SLEEP)
            elif roam_to_bssid(station["bssid"], best_ap["bssid"],
                               channel=best_ap.get("channel"),
                               freq=best_ap.get("freq"),
                               rssi=best_ap.get("rssi")):
                # 로밍 성공 정착 대기(의도적 비-중단): 이 짧은 settle 중 SIGHUP이 와도
                # 직후 interruptible_sleep(interval) 또는 다음 루프 top에서 반영된다.
                time.sleep(ROAM_SUCCESS_SLEEP)
            interruptible_sleep(interval)
            continue

        # 적합한 후보 없음 → 점증 backoff(연결 중 후보없음 airtime 잠식 억제).
        backoff, no_candidate_streak, last_backoff_cap_ts = (
            advance_no_candidate_backoff(no_candidate_streak, last_backoff_cap_ts)
        )
        logger.message(
            "info",
            f"[{IFACE}] No suitable roam candidate found "
            f"(no-candidate backoff={backoff}s, streak={no_candidate_streak})",
            _EXTRA_(),
        )
        interruptible_sleep(backoff)
        continue


def extract_ap_table(lines):
    bss_section = []
    sep_count = 0
    for line in lines:
        clean = line.strip()

        # 채널 테이블 시작되면 종료
        if "# | Channel" in clean:
            break

        # 구분선
        if re.match(r"^-+$", clean):
            sep_count += 1
            if sep_count <= 2:
                bss_section.append(line)
            continue

        # 헤더 라인 (컬럼 설명)
        if sep_count == 1 and re.match(r"^#", clean):
            bss_section.append(line)
            continue

        # 본문 이전은 무시
        if sep_count < 2:
            continue

        bss_section.append(line)

    return bss_section


def extract_channel_table(lines):
    chan_section = []
    sep_count = 0
    for line in lines:
        clean = line.strip()

        # 헤더 구분선 3~4번째 줄은 그대로 추가
        if re.match(r"^-+$", clean):
            sep_count += 1
            if 3 <= sep_count <= 4:
                chan_section.append(line)
            continue

        # 헤더 라인 (# | Channel ...)
        if sep_count == 3 and re.match(r"^#", clean):
            chan_section.append(line)
            continue

        # 데이터 본문
        if sep_count >= 4 and clean:
            chan_section.append(line)

    return chan_section


def save_with_timestamp(filename, content_lines):
    timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = f"[{timestamp_str}]"
    with open(filename, "a") as f:
        f.write(header + "\n")
        for line in content_lines:
            f.write(line.rstrip() + "\n")
        f.write("\n")

    return filename


def parse_thresholds(conf_path, def_th2g=None, def_th5g=None):
    """
    wpa_supplicant.conf에서 TH 값만 파싱
    TH 값이 없으면 인자로 받은 기본값 사용 (JSON 우선)

    Args:
        conf_path: conf 파일 경로
        def_th2g: 2.4GHz 기본 임계값 (JSON에서 로드)
        def_th5g: 5GHz 기본 임계값 (JSON에서 로드)

    Returns:
        tuple: (th2g, th5g)
    """
    th2g = None
    th5g = None

    with open(conf_path, "r") as f:
        for line in f:
            line = line.strip()
            if line.startswith("#!TH_2G="):
                try:
                    th2g = int(line.split("=")[1])
                except ValueError:
                    pass
            elif line.startswith("#!TH_5G="):
                try:
                    th5g = int(line.split("=")[1])
                except ValueError:
                    pass

    # wpa_supplicant.conf에 값이 없으면 JSON 기본값 사용, 없으면 코드 기본값
    th2g = (
        th2g
        if th2g is not None
        else (def_th2g if def_th2g is not None else DEFAULT_TH_2G)
    )
    th5g = (
        th5g
        if th5g is not None
        else (def_th5g if def_th5g is not None else DEFAULT_TH_5G)
    )

    return th2g, th5g


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, handle_sigterm)
    signal.signal(signal.SIGHUP, handle_sighup)  # 런타임 config reload(폴링 없이 신호 트리거)
    logger = Logger(app_name="ROAM", facility=logging.handlers.SysLogHandler.LOG_LOCAL0)

    IFACE = sys.argv[1] if len(sys.argv) > 1 else "mlan0"
    if IFACE not in ["mlan0", "mlan1"]:
        logger.message("emerg", f"[{IFACE}] interface is invalid", _EXTRA_())
        sys.exit(1)

    LINK_LOG_FILE = f"/var/log/cantops/json/{IFACE}/link.json"
    SCAN_LOG_FILE = f"/var/log/cantops/scan/{IFACE}/ap.log"
    WPA_CONF_FILE = f"/etc/wpa_supplicant/wpa_supplicant-{IFACE}.conf"
    # ROAM_HINT_FILE은 모듈 로드 시 기본 IFACE(mlan0)로 평가됨 → IFACE 갱신 직후 재대입해야
    # bgscan이 touch하는 /tmp/wifi_roam_hint_<iface> 와 경로가 일치(mlan1 불일치 방지).
    ROAM_HINT_FILE = f"/tmp/wifi_roam_hint_{IFACE}"

    # JSON 설정 로드 (IFACE별 설정)
    load_roaming_config(IFACE)

    # JSON에서 로드한 TH 값을 기본값으로 wpa_supplicant.conf 파싱
    # wpa_supplicant.conf에 값이 있으면 덮어쓰기, 없으면 JSON 값 사용
    json_th_2g = DEFAULT_TH_2G
    json_th_5g = DEFAULT_TH_5G
    WPA_SSID, WPA_FREQ, WPA_TH_2G, WPA_TH_5G, WPA_TH_CONNECT = parse_supplicant_conf(
        WPA_CONF_FILE, def_th2g=json_th_2g, def_th5g=json_th_5g
    )
    # 초기 파싱 시점의 mtime 기록 — 이후 main 루프는 mtime 변화(reconfigure) 시에만 재파싱.
    try:
        WPA_CONF_MTIME = os.path.getmtime(WPA_CONF_FILE)
    except OSError:
        WPA_CONF_MTIME = None

    # 최종 적용값 로깅 (JSON → wpa_supplicant.conf 우선순위)
    th_source = (
        "wpa_conf" if (WPA_TH_2G != json_th_2g or WPA_TH_5G != json_th_5g) else "json"
    )
    logger.message(
        "info",
        f"[{IFACE}] TH values: 2G={WPA_TH_2G}, 5G={WPA_TH_5G} (source: {th_source}, json_default: 2G={json_th_2g}, 5G={json_th_5g})",
        _EXTRA_(),
    )

    logger.message(
        "info",
        f"[{IFACE}] version:{VERSION}, ssid:{WPA_SSID}, scan_freq:{WPA_FREQ}, "
        f"TH_2G:{WPA_TH_2G}, TH_5G:{WPA_TH_5G}, "
        f"predictive_roam:{ENABLE_PREDICTIVE_ROAM}, "
        f"load_based_roam:{ENABLE_LOAD_BASED_ROAM}, "
        f"ping_pong_prevention:{ENABLE_PING_PONG_PREVENTION}, "
        f"adaptive_interval:{ENABLE_ADAPTIVE_INTERVAL}",
        _EXTRA_(),
    )

    main()
