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
from typing import Any, Dict
from datetime import datetime
from collections import deque
from sUTILS import Logger, _EXTRA_

VERSION = "1.1"
IFACE = "mlan0"
LINK_LOG_FILE = f"/var/log/cantops/json/{IFACE}/link.json"
SCAN_LOG_FILE = f"/var/log/cantops/scan/{IFACE}/ap.log"
FREQ_LOG_FILE = f"/var/log/cantops/scan/{IFACE}/freq.log"
WPA_CONF_FILE = f"/etc/wpa_supplicant/wpa_supplicant-mlan0.conf"
ROAM_CONDITION_FLAG = "/tmp/roam_condition"
LAST_SCAN_TIME_FILE = "/tmp/last_roam_scan_time"
WIFI_INIT_CONF_JSON = "/usr/local/etc/wifi_init_conf.json"
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

# Sleep 설정
SCAN_NO_RESULT_SLEEP = DEFAULT_SCAN_NO_RESULT_SLEEP
ROAM_SUCCESS_SLEEP = DEFAULT_ROAM_SUCCESS_SLEEP

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
    current_default_th_2g = globals().get("DEFAULT_TH_2G", DEFAULT_TH_2G)
    current_default_th_5g = globals().get("DEFAULT_TH_5G", DEFAULT_TH_5G)
    current_check_interval = globals().get("CHECK_INTERVAL", CHECK_INTERVAL)

    try:
        check_interval = int(config["CHECK_INTERVAL"])
    except (TypeError, ValueError):
        check_interval = int(current_check_interval)

    globals().update(
        {
            "ENABLE_PREDICTIVE_ROAM": config["ENABLE_PREDICTIVE_ROAM"],
            "PREDICTIVE_THRESHOLD_BOOST": config["PREDICTIVE_THRESHOLD_BOOST"],
            "TREND_WINDOW_SIZE": config["TREND_WINDOW_SIZE"],
            "TREND_HISTORY_MAX_AGE": config["TREND_HISTORY_MAX_AGE"],
            "ENABLE_LOAD_BASED_ROAM": config["ENABLE_LOAD_BASED_ROAM"],
            "MAX_ROAM_LOAD": config["MAX_ROAM_LOAD"],
            "LOAD_DIFF_THRESHOLD": config["LOAD_DIFF_THRESHOLD"],
            "ENABLE_PING_PONG_PREVENTION": config["ENABLE_PING_PONG_PREVENTION"],
            "PING_PONG_WINDOW": config["PING_PONG_WINDOW"],
            "MAX_ROAMS_IN_WINDOW": config["MAX_ROAMS_IN_WINDOW"],
            "PING_PONG_DETECTION_TIME": config["PING_PONG_DETECTION_TIME"],
            "ENABLE_ADAPTIVE_INTERVAL": config["ENABLE_ADAPTIVE_INTERVAL"],
            "MIN_CHECK_INTERVAL": config["MIN_CHECK_INTERVAL"],
            "MAX_CHECK_INTERVAL": config["MAX_CHECK_INTERVAL"],
            "ADAPTIVE_RSSI_DROP_THRESHOLD": config["ADAPTIVE_RSSI_DROP_THRESHOLD"],
            "ADAPTIVE_RSSI_RISE_THRESHOLD": config["ADAPTIVE_RSSI_RISE_THRESHOLD"],
            "ADAPTIVE_NEAR_THRESHOLD_OFFSET": config["ADAPTIVE_NEAR_THRESHOLD_OFFSET"],
            "ADAPTIVE_NEAR_THRESHOLD_INTERVAL": config["ADAPTIVE_NEAR_THRESHOLD_INTERVAL"],
            "ADAPTIVE_GOOD_SIGNAL_OFFSET": config["ADAPTIVE_GOOD_SIGNAL_OFFSET"],
            "ADAPTIVE_CONSECUTIVE_DROP_COUNT": config["ADAPTIVE_CONSECUTIVE_DROP_COUNT"],
            "DEFAULT_TH_2G": config.get("DEFAULT_TH_2G", current_default_th_2g),
            "DEFAULT_TH_5G": config.get("DEFAULT_TH_5G", current_default_th_5g),
            "DIFF_TH": config["DIFF_TH"],
            "CHECK_INTERVAL": check_interval,
            "ENABLE_POST_ROAM_ARP_OPTIMIZATION": config[
                "ENABLE_POST_ROAM_ARP_OPTIMIZATION"
            ],
            "POST_ROAM_GARP_COUNT": config["POST_ROAM_GARP_COUNT"],
            "POST_ROAM_GARP_WAIT": config["POST_ROAM_GARP_WAIT"],
            "ENABLE_POST_ROAM_PEER_WARMUP": config["ENABLE_POST_ROAM_PEER_WARMUP"],
            "POST_ROAM_PEER_COUNT": config["POST_ROAM_PEER_COUNT"],
            "POST_ROAM_PEER_WAIT": config["POST_ROAM_PEER_WAIT"],
            "SCAN_NO_RESULT_SLEEP": int(config["SCAN_NO_RESULT_SLEEP"]),
            "ROAM_SUCCESS_SLEEP": int(config["ROAM_SUCCESS_SLEEP"]),
            "USE_SIGNAL_AVG": config["USE_SIGNAL_AVG"],
        }
    )


def load_roaming_config(iface):
    """
    JSON 형식의 conf 파일에서 인터페이스별 로밍 설정 로드

    Args:
        iface: 인터페이스 이름 (mlan0 또는 mlan1)

    Returns:
        dict: 로밍 설정 dictionary
    """
    global EXTRA_SSIDS
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
        "USE_SIGNAL_AVG": DEFAULT_USE_SIGNAL_AVG,
    }

    # 1. JSON 설정 파일 시도
    try:
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


def iw_scan(ssid, freqs):
    if ssid and freqs:
        cmd = ["iw", IFACE, "scan", "freq"] + freqs + ["ssid", ssid]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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
    extra_ssids가 비고 라이브==WPA_SSID면 [WPA_SSID] → 기존 단일 SSID 동작(무회귀)."""
    allowed = []
    for s in (live_ssid, WPA_SSID):
        if s and s not in allowed:
            allowed.append(s)
    for s in EXTRA_SSIDS:
        if s and s not in allowed:
            allowed.append(s)
    return allowed


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
def roam_to_bssid(from_bssid, to_bssid):
    """
    Ping-pong 확인 후 로밍 실행

    Args:
        from_bssid: 현재 연결된 BSSID
        to_bssid: 로밍할 BSSID

    Returns:
        bool: 로밍 성공 여부
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

        if result.returncode == 0:
            if ENABLE_PING_PONG_PREVENTION and ping_pong_preventer:
                ping_pong_preventer.add_roam(from_bssid, to_bssid)

            logger.message("info", f"[{IFACE}] Roam successful: {to_bssid}", _EXTRA_())

            optimize_post_roam_connectivity(IFACE)

            return True
        else:
            logger.message(
                "err", f"[{IFACE}] Roam failed: {result.stderr.strip()}", _EXTRA_()
            )
            return False

    except subprocess.TimeoutExpired:
        logger.message("err", f"[{IFACE}] Roam timeout: {to_bssid}", _EXTRA_())
        return False
    except Exception as e:
        logger.message("err", f"[{IFACE}] Roam error: {e}", _EXTRA_())
        return False


def connect_to_ssid(iface, to_ssid, from_bssid, to_bssid):
    """다른 SSID로 로밍: wifi <iface> connect (conf ssid 교체→reconfigure→reassociate).
    wpa_cli roam은 같은 network 블록(SSID) 내 BSS만 전환하므로, 다른 SSID 전환은 connect로 처리.
    - extra_ssids가 현재와 같은 psk/key_mgmt를 공유한다는 전제(아니면 connect 후 인증 실패).
    - freq 인자는 일부러 생략한다: wifi connect에 단일 freq를 주면 conf의 multi-freq
      scan_freq/freq_list가 그 한 채널로 collapse되어 이후 스캔 범위가 축소된다. ssid만
      교체하고 scan_freq는 유지(후보는 이미 WPA_FREQ 내 채널이라 연결 가능).
    - ping-pong 예산은 same-SSID 로밍과 공유(의도): cross-SSID도 BSSID 기반 카운트에 합산."""
    if ENABLE_PING_PONG_PREVENTION and ping_pong_preventer:
        if ping_pong_preventer.is_ping_pong(from_bssid, to_bssid):
            logger.message(
                "info",
                f"[{IFACE}] Cross-SSID roam blocked: ping-pong ({from_bssid} → {to_bssid})",
                _EXTRA_(),
            )
            return False

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
    global trend_tracker, ping_pong_preventer, adaptive_interval

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

    while True:
        # wpa_cli reconfigure 등으로 conf 가 런타임 변경됐으면 재파싱(mtime 변화 시에만).
        # ssid/scan_freq/TH 캐시를 최신화해 옛 SSID 로 스캔하는 stale 로밍을 방지한다.
        reload_supplicant_conf_if_changed(WPA_CONF_FILE)

        # Load 정보 포함하여 연결 상태 확인
        station = get_link_info_with_load()

        if not station:
            time.sleep(CHECK_INTERVAL)
            continue

        rssi = station.get("rssi")
        if not is_valid_rssi(rssi):
            time.sleep(CHECK_INTERVAL)
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
            time.sleep(CHECK_INTERVAL)
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

            if ENABLE_ADAPTIVE_INTERVAL and adaptive_interval:
                interval = adaptive_interval.update(rssi, base_threshold, trend)
            else:
                interval = CHECK_INTERVAL

            time.sleep(interval)
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

        # 주변 AP 스캔
        if WPA_SSID and WPA_FREQ:
            # station["ssid"]는 get_link_info_with_load가 link.json info.ssid(실제 연결 SSID)로 채움
            if not station.get("ssid"):
                station["ssid"] = WPA_SSID
            lines = mlanutl_scan(get_allowed_ssids(station.get("ssid")), WPA_FREQ)
            try:
                with open(LAST_SCAN_TIME_FILE, "w") as f:
                    f.write(str(time.time()))
            except Exception:
                pass

            if lines:
                ap_lines = extract_ap_table(lines)
                chan_lines = extract_channel_table(lines)
                save_with_timestamp(SCAN_LOG_FILE, ap_lines)
                save_with_timestamp(FREQ_LOG_FILE, chan_lines)
            else:
                logger.message("err", f"[{IFACE}] scan failed", _EXTRA_())
                time.sleep(interval)
                continue

        # Load 정보 가져오기
        channel_info_data = (
            station.get("channel_info") if ENABLE_LOAD_BASED_ROAM else None
        )

        # 스캔 결과 파싱
        entries, timestamp = get_latest_scan(station, channel_info_data, get_allowed_ssids(station.get("ssid")))

        if not entries:
            logger.message(
                "err", f"[{IFACE}] No Matching APs found in latest scan", _EXTRA_()
            )
            time.sleep(interval)
            continue

        # 로밍 후보 평가
        best_ap = None
        best_reason = ""
        best_score = 0

        for roam_ap in entries:
            if roam_ap["bssid"] == station["bssid"]:
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
            logger.message(
                "emerg",
                f"[{IFACE}] Roaming: {station['bssid']} → {best_ap['bssid']}, "
                f"reason={best_reason}, score={best_score:.1f}, "
                f"{best_ap['ssid']}, {best_ap['rssi']}dB (ch={best_ap['freq']})",
                _EXTRA_(),
            )

            # 라우팅 판단을 후보 필터와 동일 소스(라이브 SSID + conf WPA_SSID)로 통일.
            # best_ap가 base SSID 집합에 속하면 무중단 roam, extra SSID면 connect(재연결).
            # (info.ssid가 cross-SSID connect 직후 일시적으로 stale이면 분기가 한 tick
            #  어긋날 수 있으나 ROAM_SUCCESS_SLEEP로 bounded — 다음 tick 자가 교정.)
            base_ssids = {s for s in (station.get("ssid"), WPA_SSID) if s}
            if best_ap.get("ssid") and best_ap["ssid"] not in base_ssids:
                # 다른(extra) SSID → wifi connect. 성공/실패 무관 안정화 대기로 재시도 폭주 방지
                # (connect 실패해도 conf ssid는 이미 교체되어, 즉시 재시도하면 링크가 흔들림).
                connect_to_ssid(
                    IFACE, best_ap["ssid"], station["bssid"], best_ap["bssid"]
                )
                time.sleep(ROAM_SUCCESS_SLEEP)
            elif roam_to_bssid(station["bssid"], best_ap["bssid"]):
                time.sleep(ROAM_SUCCESS_SLEEP)
        else:
            logger.message(
                "info", f"[{IFACE}] No suitable roam candidate found", _EXTRA_()
            )

        time.sleep(interval)


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
    logger = Logger(app_name="ROAM", facility=logging.handlers.SysLogHandler.LOG_LOCAL0)

    IFACE = sys.argv[1] if len(sys.argv) > 1 else "mlan0"
    if IFACE not in ["mlan0", "mlan1"]:
        logger.message("emerg", f"[{IFACE}] interface is invalid", _EXTRA_())
        sys.exit(1)

    LINK_LOG_FILE = f"/var/log/cantops/json/{IFACE}/link.json"
    SCAN_LOG_FILE = f"/var/log/cantops/scan/{IFACE}/ap.log"
    FREQ_LOG_FILE = f"/var/log/cantops/scan/{IFACE}/freq.log"
    WPA_CONF_FILE = f"/etc/wpa_supplicant/wpa_supplicant-{IFACE}.conf"

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
