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
from roam_state import (
    clear_own_lease,
    clear_stale_lease,
    lease_active,
    process_start_time,
    roam_state_paths,
    scan_transition_lock,
    write_flag,
)
from roam_policy import RoamPolicyError, load_boot_roam_policy

VERSION = "1.1"
IFACE = "mlan0"
BSSID_CLEAR_ADDR = "00:00:00:00:00:00"
SCAN_TRANSITION_BUSY = object()
# wpa_supplicant CTRL_IFACE BSSID accepts a MAC address, not "any";
# the all-zero address clears ssid->bssid_set and is displayed as "any".
LINK_LOG_FILE = f"/var/log/cantops/json/{IFACE}/link.json"
SCAN_LOG_FILE = f"/var/log/cantops/scan/{IFACE}/ap.log"
WPA_CONF_FILE = f"/etc/wpa_supplicant/wpa_supplicant-mlan0.conf"

# 모듈 로드 시 mlan0 기본 경로 — __main__ 이 실제 IFACE 로 재대입(ROAM_HINT_FILE 전례).
ROAM_CONDITION_FLAG, LAST_SCAN_TIME_FILE = roam_state_paths(IFACE)
WIFI_INIT_CONF_JSON = "/usr/local/etc/wifi_init_conf.json"
WIFI_RUN_DIR = os.environ.get("WIFI_RUN_DIR", "/run/wifi")
# Mode A selection 복구 WAL은 /run/wifi 바깥에 둔다. wifi 서비스 재시작 과정에서
# /run/wifi 자체가 정리돼도, supplicant 메모리에 남은 BSSID pin 복구 의무까지 함께
# 사라지면 안 된다. 테스트/제품별 경로 주입은 별도 환경변수로만 허용한다.
WIFI_SELECTION_STATE_DIR = os.environ.get(
    "WIFI_SELECTION_STATE_DIR",
    os.path.dirname(WIFI_RUN_DIR.rstrip("/")) or "/",
)
# WAL directory fsync 실패처럼 디스크 지속성을 증명하지 못한 경우에도 살아 있는
# daemon은 새 선택을 허용하지 않는다. 값은 복구해야 할 network id 문자열이다.
_SELECTION_CLEANUP_PENDING = {}
ROAM_HINT_FILE = f"/tmp/wifi_roam_hint_{IFACE}"  # bgscan이 새 후보 AP 발견 시 touch (단방향 신호)
SCAN_TIMESTAMP_RE = re.compile(
    r"^(?:\[(?P<bracketed>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]"
    r"|(?P<plain>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}))$"
)
MAX_SCAN_SSIDS = 10  # nl80211 max # scan SSIDs (NXP mlan 실측). 초과 시 iw가 -EINVAL로 스캔 전체 실패.
IW_SCAN_FRESH_SLACK_MS = 1000  # iw scan 경과시간에 더할 last-seen 오차/동시스캔 여유
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
# 코드 기본값은 JSON 로드 실패/키 부재 시의 폴백 — 템플릿 wifi_init_conf.json(제품 의도)과
# 일치시킨다(fail-same, tests/test_defaults_template_consistency.py 가 고정). 로밍 키는
# mlan0/mlan1 템플릿 값이 정렬돼 있어(튜닝 승격, 2026-08) 폴백도 단일 값으로 충분하다.
DIFF_TH = 7
CHECK_INTERVAL = 1  # 로밍 판정 tick 주기(초, 양 iface 템플릿 동일). 판정 입력 link.json 이 0.9s 주기 갱신(logger.link_interval_sec)이라 1 미만은 실익 없음

# 단계형 로밍 스캔: RSSI가 임계값 이하로 떨어지면
#   - 단일채널: 홈채널 패시브 스캔(같은 채널 후보 + 현재 AP RSSI로 baseline 통일),
#     후보 비콘을 못 받았을 때만 동일 채널 directed active 폴백
#   - 다중채널: 설정된 전 채널 directed active 스캔 1회
# bgscan/ap.log 캐시는 최대 interval 만큼 과거 RSSI이므로 최종 로밍 판정에 사용하지 않는다.
# 평상시 BSS 테이블 충전과 roam backoff 해제 hint 용도로만 유지한다.
# ENABLE_STAGED_SCAN=False면 종전 단일 액티브 스캔 경로로 회귀(무회귀 안전장치).
DEFAULT_ENABLE_STAGED_SCAN = True
# scan_freq 가 홈채널의 부분집합(단일 채널 등)이면 Stage 1 홈 패시브 스캔이 이미 모든 후보를
# 커버하므로 Stage 3 액티브 폴백은 같은 채널을 probe로 다시 훑는 것뿐 — 스킵해 매 로밍컨디션
# 주기의 불필요한 액티브 스캔(probe 송신)을 없앤다. hidden SSID 는 액티브 probe로만 발견되므로
# 홈채널에 hidden 로밍 타깃이 있는 배포는 home_passive=false(홈 directed 액티브)로 hidden 을
# 커버하는 것을 권장(스킵 유지+주기당 1회). 대안: 이 값을 false 로 두거나 다채널 운용.
DEFAULT_SKIP_REDUNDANT_ACTIVE_SCAN = True
# Stage 1 홈채널 스캔 모드. 기본 패시브(probe 미송신, 저부하·비콘 스케일 RSSI). false 면
# directed 액티브(allowed SSID probe, wildcard 없음) — 홈채널에 hidden 로밍 타깃이 있는
# 배포에서 skip_redundant_active 최적화를 유지한 채 hidden 을 발견하기 위한 스위치.
# (패시브는 비콘만 수신하므로 hidden SSID 를 구조적으로 못 본다.)
DEFAULT_HOME_PASSIVE = True
ENABLE_STAGED_SCAN = DEFAULT_ENABLE_STAGED_SCAN
SKIP_REDUNDANT_ACTIVE_SCAN = DEFAULT_SKIP_REDUNDANT_ACTIVE_SCAN
HOME_PASSIVE = DEFAULT_HOME_PASSIVE

# ── good-signal 리셋 게이트 ─────────────────────────────────────────────────
# 메인루프의 good-signal 분기(rssi >= threshold)는 후보없음 backoff streak 를 **무조건**
# 리셋한다. 실측(정체 18.85h 재생)에서 오탐 리셋 662건 중 624건(94%)이 이 분기였고 그중
# 623건이 Δ0dB — 임계 바로 위에서 진동할 뿐 위치가 안 변한 경우다. 리셋되면 다음 악화에
# backoff 가 시작값(3초)부터 다시 올라가 스캔이 폭증한다(스캔 3377→1417, -58.0%;
# airtime duty 5.44%→2.28%). 이동 로그(71개 90.1h)에서는 스캔 -0.0%·추가지연 0건으로
# 이동 시 재탐색성은 보존됐다.
#
# 판정은 "직전 리셋 시점 대비 |Δrssi| >= delta_db" 다. 재생 실측상 delta 값 1/2/3/5 가
# 전부 -58.0~-58.1% 로 동일해(리셋 지점의 Δ가 0dB 에 몰림) 1dB 양자화 여유를 둔 2 를 기본으로
# 한다. 설계의 2층(60초 peak-to-peak >= 5dB)은 이 파일의 RSSI 이력이 ENABLE_PREDICTIVE_ROAM
# 게이트 안에서만 쌓이고(그래서 출하 기본에서 비어 있음) 샘플 간격도 2~30초로 흔들려
# 후속 범위로 미뤘다 — 1층만으로 위 효과의 거의 전부를 얻는다.
DEFAULT_ENABLE_GOOD_SIGNAL_GATE = True  # 기본 on (2026-08-03 전환) — 실기 3-way 검증 완료(#138), 정체 스캔 −58%·이동 영향 0
GOOD_SIGNAL_GATE_DELTA_DB = 2
# 결합 직후 RSSI 는 25초에 걸쳐 12~14dB 하강한다(attach ramp — TX rate 불변이라 실제 링크
# 열화가 아닌 측정 램프). 그 구간의 큰 Δ 를 "이동"으로 읽으면 게이트가 무력화되므로, 결합
# 후 이 시간 동안은 종전처럼 무조건 리셋한다(보수적).
GOOD_SIGNAL_GATE_GRACE_SEC = 40
ENABLE_GOOD_SIGNAL_GATE = DEFAULT_ENABLE_GOOD_SIGNAL_GATE
# GOOD_SIGNAL_RESET_GATE.enable만 JSON/SIGHUP 설정으로 유지한다. delta/grace는 실측으로
# 확정된 정책 상수다. delta 1/2/3/5dB의 재생 결과가 사실상 동일했고 attach ramp는 약
# 25초로 고정 관측돼, 숫자 knob는 조정 이득보다 설정 drift 비용이 컸다.


def is_valid_rssi(rssi) -> bool:
    if not isinstance(rssi, int):
        return False
    return -120 <= rssi <= -1


# 개선 설정 기본값 (Default Improvement Configuration)
# 실험 기능 enable 류는 템플릿이 전부 false(plain-mode) — 폴백도 동일하게 꺼 둔다.
# (종전 코드 기본 True 는 JSON 손상 시 실험 기능 4종이 일제히 켜지는 fail-different 였다.)
DEFAULT_ENABLE_PREDICTIVE_ROAM = False
DEFAULT_PREDICTIVE_THRESHOLD_BOOST = 5
DEFAULT_TREND_WINDOW_SIZE = 5
DEFAULT_TREND_HISTORY_MAX_AGE = 30
DEFAULT_ENABLE_PING_PONG_PREVENTION = True
DEFAULT_PING_PONG_WINDOW = 20
DEFAULT_MAX_ROAMS_IN_WINDOW = 3
DEFAULT_PING_PONG_DETECTION_TIME = 10
DEFAULT_USE_SIGNAL_AVG = True  # True: link 파일의 signal_avg(평활) 사용, False: signal(순간값)

# Sleep 기본값
DEFAULT_SCAN_NO_RESULT_SLEEP = 3  # AP 스캔 결과 없을 때 재시도 대기
DEFAULT_ROAM_SUCCESS_SLEEP = 3  # 로밍 성공 후 안정화 대기(양 iface 템플릿 동일)
DEFAULT_ROAM_NO_RESULT_MAX_SLEEP = 30  # 후보없음 backoff 상한(초). 의도적으로 JSON 미노출 —
# 과거엔 로더가 .get() 으로 읽어 JSON 에 손으로 넣으면 몰래 실효되는 뒷문이었다(감사 D2 로 봉쇄).
# 운영에서 조정할 근거가 없고, 실험이 필요하면 이 상수를 직접 바꾼다.
DEFAULT_ROAM_CROSS_FAIL_RETRY_COUNT = 2  # cross-SSID 전환 실패 시 cooldown 없이 즉시 재시도 허용 횟수(초과 시 backoff)
DEFAULT_ROAM_NO_RESULT_FAST_COUNT = 3  # 후보 미발견 backoff 의 레벨당 반복 횟수 — 각 주기를 N tick 유지 후 2배(플래토 곡선)


# 현재 설정값 (Current Configuration - will be loaded from JSON)
ENABLE_PREDICTIVE_ROAM = DEFAULT_ENABLE_PREDICTIVE_ROAM
PREDICTIVE_THRESHOLD_BOOST = DEFAULT_PREDICTIVE_THRESHOLD_BOOST
TREND_WINDOW_SIZE = DEFAULT_TREND_WINDOW_SIZE
TREND_HISTORY_MAX_AGE = DEFAULT_TREND_HISTORY_MAX_AGE
ENABLE_PING_PONG_PREVENTION = DEFAULT_ENABLE_PING_PONG_PREVENTION
PING_PONG_WINDOW = DEFAULT_PING_PONG_WINDOW
MAX_ROAMS_IN_WINDOW = DEFAULT_MAX_ROAMS_IN_WINDOW
PING_PONG_DETECTION_TIME = DEFAULT_PING_PONG_DETECTION_TIME
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
ROAM_CROSS_FAIL_RETRY_COUNT = DEFAULT_ROAM_CROSS_FAIL_RETRY_COUNT
ROAM_NO_RESULT_FAST_COUNT = DEFAULT_ROAM_NO_RESULT_FAST_COUNT

def _no_result_max_level():
    """지수 backoff가 상한(ROAM_NO_RESULT_MAX_SLEEP)에 도달하는 데 필요한 2배수 증가 레벨.

    compute_no_result_backoff는 레벨(=(streak-1)//fast)을 (이 값 - 1)로 clamp해 거대
    지수 연산을 막고(도달 즉시 상한이라 그 이상 무의미), advance_no_candidate_backoff
    는 streak를 fast*(max_level-1)+1 로 cap한다(플래토 곡선에서 상한 레벨에 처음
    도달하는 지점). 시작값*2**(L-1) >= cap 를 만족하는 최소 L.
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


def compute_no_result_backoff(streak, fast_count=1):
    """후보없음 streak에 대한 sleep 초 — 플래토 지수 backoff(상한 clamp).

    fast_count 를 **레벨당 반복 횟수**로 쓴다: 각 주기를 fast_count tick 유지한 뒤
    2배로 올린다. 곡선 = SCAN_NO_RESULT_SLEEP × 2^⌊(streak-1)/fast_count⌋,
    상한 ROAM_NO_RESULT_MAX_SLEEP. 기본(3,3)이면 3,3,3,6,6,6,12,12,12,24,24,24,30.
    종전 의미(처음 N회만 빠른 주기, 이후 매 tick 2배)에서 확장한 것 — 시작 구간뿐
    아니라 모든 레벨을 같은 폭으로 유지해, 정당한 리셋 직후 탐색이 완만하게
    느려진다(good-signal 게이트와 세트: 게이트가 가짜 리셋을 막으므로 상승 구간은
    실제 이동 직후에만 나타난다). fast_count=1 이면 기존 레거시 곡선
    (streak1=시작값, streak2=×2 …)과 정확히 동일(무회귀 계약, 테스트 고정).
    streak<=0 → 시작값. 레벨은 max_level-1 로 clamp(거대 지수 방지).
    끊김 복구는 wpa 네이티브가 담당하므로 이 backoff는 '연결 중 후보없음'
    airtime 잠식만 억제한다(spec §4)."""
    if streak <= 0:
        return int(SCAN_NO_RESULT_SLEEP)
    level = (streak - 1) // max(1, fast_count)
    level = min(level, _no_result_max_level() - 1)
    backoff = SCAN_NO_RESULT_SLEEP * (2 ** level)
    return int(min(backoff, ROAM_NO_RESULT_MAX_SLEEP))


def new_gate_state():
    """good-signal 게이트의 tick 간 상태.

    bssid=결합 변화 감지용, assoc_ts=마지막 결합 시각(attach ramp grace),
    reset_rssi=마지막으로 streak 를 리셋한 시점의 RSSI(변화 누적 기준),
    suppressed=억제 누적(매 tick 로그는 볼륨 문제라 리셋 시 1회 요약)."""
    return {"bssid": None, "assoc_ts": None, "reset_rssi": None, "suppressed": 0}


def track_association(station, gs, now=None):
    """결합 변화(로밍·자율 재연결·연결 끊김)를 감지해 게이트 기준을 갱신. 반환=새 결합 여부.

    now 는 결합 시각 주입용(테스트 격리) — good_signal_reset_allowed 와 같은 규약.

    **station 이 None 이면 bssid 를 비운다.** 비우지 않으면 끊겼다 **같은 AP** 로 재결합할 때
    BSSID 비교가 같아 새 결합을 못 알아채고, attach ramp grace 가 적용되지 않은 채 끊김 전
    baseline·streak 으로 판정한다. station=None 은 실제 끊김뿐 아니라 link.json stale·부재도
    포함하지만, 재결합을 놓치는 것보다 grace 를 한 번 더 주는 편이 보수적이다.

    결합이 바뀌면 reset_rssi 를 비우고(옛 AP RSSI 와 비교는 무의미) suppressed 도 0 으로
    돌린다 — 구 AP 에서 쌓인 억제 카운트가 새 AP 문맥의 요약 로그에 섞이면 오해를 준다.

    로밍 경로에만 두면 supplicant 자율 재연결을 놓치므로 BSSID 관측으로 감지한다."""
    # `is None` 으로 명시 — get_link_info 는 None 또는 채워진 dict 만 반환하므로
    # falsy 검사와 결과가 같지만, "연결 정보 없음" 이라는 의도를 코드에 드러낸다.
    if station is None:
        gs["bssid"] = None
        return False
    cur = station.get("bssid")
    if not cur or cur == gs["bssid"]:
        return False
    gs["bssid"] = cur
    gs["assoc_ts"] = time.time() if now is None else now
    gs["reset_rssi"] = None
    gs["suppressed"] = 0
    return True


def on_streak_reset(gs):
    """good-signal 이 아닌 경로(bgscan hint / 후보 발견)로 streak 가 리셋될 때 호출.

    **기준 무효화** — reset_rssi 는 "마지막 리셋 시점의 RSSI" 여야 하는데 종전엔 good-signal
    분기에서만 갱신돼, 다른 경로로 리셋된 뒤의 판정이 **옛 기준**과 비교됐다. 그러면 실제
    이동 후에도 억제되거나(Δ가 옛 기준 대비 작게 나옴) 최신 리셋 이전의 변화로 리셋이
    허용될 수 있다. hint 경로는 station 조회 **전**이라 RSSI 를 모르므로 None 으로 무효화한다
    — 다음 good-signal 이 no-baseline 으로 허용되며 기준을 다시 잡는다(보수적: 허용 쪽).

    **억제 이력 정리** — streak 가 0 이 되면 그때까지의 억제는 이미 무효가 된다(억제의 목적이
    streak 유지인데 다른 경로가 그걸 0 으로 만들었다). 그 카운트를 남기면 다음 요약이
    "이전 N회 억제, streak=0 유지했었음" 처럼 찍혀 **억제가 실익 없었다는 오독**을 부른다."""
    gs["reset_rssi"] = None
    gs["suppressed"] = 0


def good_signal_reset_allowed(cur_rssi, last_reset_rssi, last_assoc_ts, now=None):
    """good-signal 분기(rssi >= threshold)에서 후보없음 backoff streak 를 리셋해도 되는지.

    종전은 무조건 리셋이었다. 실측(정체 18.85h 재생)에서 오탐 리셋 662건 중 624건(94%)이
    이 분기였고 그중 623건이 **Δ0dB** — 임계 바로 위에서 진동할 뿐 위치가 안 변한 경우다.
    리셋되면 다음 악화에 backoff 가 3초부터 다시 올라가 스캔이 폭증한다.

    판정 = 직전 리셋 시점 대비 |Δrssi| >= GOOD_SIGNAL_GATE_DELTA_DB.
    "리셋 시점"을 기준으로 삼는 이유: 매 tick 직전값과 비교하면 1dB 씩 천천히 이동하는
    구간에서 매번 Δ<delta 로 억제돼 이동을 놓친다. 리셋 이후 누적 변화를 봐야 한다.

    허용(=종전 동작)으로 떨어지는 예외 셋:
      - 게이트 비활성(무회귀 경로)
      - 기준값 없음(프로세스 시작 직후 첫 판정) — 보수적으로 허용
      - 결합 후 GRACE_SEC 이내 — attach ramp(25초에 12~14dB 하강)의 큰 Δ 를 이동으로
        오독하지 않도록 게이트를 우회한다
    """
    if not ENABLE_GOOD_SIGNAL_GATE:
        return True, "gate-disabled"
    if last_reset_rssi is None:
        return True, "no-baseline"
    if last_assoc_ts is not None:
        now = time.time() if now is None else now
        if now - last_assoc_ts < GOOD_SIGNAL_GATE_GRACE_SEC:
            return True, "post-assoc-grace"
    delta = abs(cur_rssi - last_reset_rssi)
    if delta >= GOOD_SIGNAL_GATE_DELTA_DB:
        return True, f"moved({delta}dB)"
    return False, f"stationary({delta}dB)"


def advance_no_candidate_backoff(streak):
    """후보없음 1 tick 진행: streak 증가(상한 clamp) → backoff 계산.

    메인루프 3곳(scan 실패 / 결과 0건 / 적합후보 없음)의 동일 로직을 단일화(DRY).
    주기는 레벨당 ROAM_NO_RESULT_FAST_COUNT tick 씩 유지하며 2배로 올라가는 플래토
    곡선이다(compute_no_result_backoff 참조). streak 를 상한 도달
    지점에서 cap 해 매 tick 무한 증가를 막는다(#5). 반환: (backoff, streak).

    상한 도달 후 시간이 지나면 streak를 1 감소시키던 ROAM_NO_RESULT_BACKOFF_RECOVER_SEC
    경로는 제거했다 — 점감이 backoff 계산 뒤에 일어나고 다음 tick의 streak+1이 즉시
    되돌려 반환값이 상한 아래로 내려간 적이 없다(실효 0, streak만 7↔6 진동). 후보없음이
    길어져도 상한 주기를 유지하는 현재 동작은 그대로다."""
    fast = max(1, ROAM_NO_RESULT_FAST_COUNT)
    # 상한 레벨(max_level-1)에 처음 도달하는 streak 에서 clamp — 플래토 곡선에서
    # 레벨은 (streak-1)//fast 이므로 그 지점은 fast*(max_level-1)+1. fast=1 이면
    # 종전 cap(max_level)과 동일하다.
    max_streak = fast * (_no_result_max_level() - 1) + 1
    streak = min(streak + 1, max_streak)
    return compute_no_result_backoff(streak, fast), streak

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

    def _num(key, cast=int, minimum=None):
        # flat 덮어쓰기 루프가 원본 문자열 등 잘못된 값을 넣어도(런타임 reload/부팅)
        # int() 크래시 없이 현행 전역값을 유지한다. 수치 전역은 모듈 로드 시 유효 기본값
        # 으로 정의되므로 g.get(key)는 항상 유효(최후 방어까지 삼중 가드). 이 방어가
        # 없으면 예: `SCAN_NO_RESULT_SLEEP: "bad"` 한 줄로 데몬이 ValueError로 죽는다.
        #
        # minimum: 지정 시 그 미만 값을 거부하고 현행 전역값으로 폴백한다. 타입 가드만으로는
        # **값 범위**를 못 막는데, sleep/interval 계열에 0·음수가 들어오면 크래시가 아니라
        # 대기 없는 바쁜 루프가 된다 — interruptible_sleep(:1700)이 seconds<=0 이면 즉시
        # 반환하므로 매 tick 스캔이 폭주해 CPU·airtime 을 잠식한다(감지도 어렵다).
        # ROAM_SUCCESS_SLEEP 은 time.sleep 직접 호출(:2946/:2953)이라 음수면 ValueError 로
        # 데몬이 죽는다. 스키마의 minimum 은 WebUI 힌트일 뿐 데몬이 강제하지 않으므로
        # 직접 편집·마이그레이션으로 들어오는 값을 여기서 막는다.
        def _guard(v):
            if minimum is not None and v < minimum:
                raise ValueError(f"must be >= {minimum}, got {v}")
            return v

        try:
            return _guard(cast(config[key]))
        except KeyError:
            pass
        except (TypeError, ValueError) as e:
            if "logger" in g:
                g["logger"].message(
                    "warn",
                    f"[roaming] {key}={config.get(key)!r} rejected ({e}); "
                    f"keeping current {g.get(key)!r}",
                    _EXTRA_(),
                )
        cur = g.get(key)
        try:
            return _guard(cast(cur))
        except (TypeError, ValueError) as e:
            # 현행 전역값마저 부적합한 경우(이전 재로드에서 오염됐거나 초기값이 없는 키).
            # 아래에서 조용히 클램프하면 최후 폴백이 발동했는지 운영자가 알 수 없다.
            if "logger" in g:
                # minimum 이 없는 키는 아래에서 0 으로 떨어지므로 "clamping to None" 같은
                # 사실과 다른 문구가 남지 않게 실제 귀착값을 그대로 적는다.
                target = minimum if minimum is not None else 0
                g["logger"].message(
                    "warn",
                    f"[roaming] {key} current global {cur!r} also invalid ({e}); "
                    f"falling back to {target!r}",
                    _EXTRA_(),
                )
        # 미래에 모듈 전역 초기값 없는 키가 추가돼 cur가 None이어도 None을 전역에 쓰지
        # 않는다(이후 int 사용처 TypeError 방지) — 최후 폴백. minimum 이 있으면 그 값을
        # 하한으로 삼아 0(바쁜 루프)으로 떨어지지 않게 한다.
        if isinstance(cur, (int, float)):
            return max(cur, minimum) if minimum is not None else cur
        return minimum if minimum is not None else 0

    g.update(
        {
            "ENABLE_PREDICTIVE_ROAM": config["ENABLE_PREDICTIVE_ROAM"],
            "PREDICTIVE_THRESHOLD_BOOST": _num("PREDICTIVE_THRESHOLD_BOOST"),
            "TREND_WINDOW_SIZE": _num("TREND_WINDOW_SIZE"),
            "TREND_HISTORY_MAX_AGE": _num("TREND_HISTORY_MAX_AGE"),
            "ENABLE_PING_PONG_PREVENTION": config["ENABLE_PING_PONG_PREVENTION"],
            "PING_PONG_WINDOW": _num("PING_PONG_WINDOW"),
            "MAX_ROAMS_IN_WINDOW": _num("MAX_ROAMS_IN_WINDOW"),
            "PING_PONG_DETECTION_TIME": _num("PING_PONG_DETECTION_TIME"),
            "DEFAULT_TH_2G": _num("DEFAULT_TH_2G"),
            "DEFAULT_TH_5G": _num("DEFAULT_TH_5G"),
            "DIFF_TH": _num("DIFF_TH"),
            "CHECK_INTERVAL": _num("CHECK_INTERVAL", minimum=1),
            # sleep/interval 계열은 minimum=1 — 0·음수는 바쁜 루프 또는 time.sleep 크래시.
            "SCAN_NO_RESULT_SLEEP": _num("SCAN_NO_RESULT_SLEEP", minimum=1),
            "ROAM_SUCCESS_SLEEP": _num("ROAM_SUCCESS_SLEEP", minimum=1),
            "ROAM_CROSS_FAIL_RETRY_COUNT": _num("ROAM_CROSS_FAIL_RETRY_COUNT"),
            "ROAM_NO_RESULT_FAST_COUNT": _num("ROAM_NO_RESULT_FAST_COUNT"),
            "ENABLE_STAGED_SCAN": config["ENABLE_STAGED_SCAN"],
            "SKIP_REDUNDANT_ACTIVE_SCAN": config["SKIP_REDUNDANT_ACTIVE_SCAN"],
            "HOME_PASSIVE": config["HOME_PASSIVE"],
            "ENABLE_GOOD_SIGNAL_GATE": config["ENABLE_GOOD_SIGNAL_GATE"],
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
        "ENABLE_PING_PONG_PREVENTION": DEFAULT_ENABLE_PING_PONG_PREVENTION,
        "PING_PONG_WINDOW": DEFAULT_PING_PONG_WINDOW,
        "MAX_ROAMS_IN_WINDOW": DEFAULT_MAX_ROAMS_IN_WINDOW,
        "PING_PONG_DETECTION_TIME": DEFAULT_PING_PONG_DETECTION_TIME,
        "DEFAULT_TH_2G": DEFAULT_TH_2G,
        "DEFAULT_TH_5G": DEFAULT_TH_5G,
        "DIFF_TH": DIFF_TH,
        "CHECK_INTERVAL": CHECK_INTERVAL,
        "SCAN_NO_RESULT_SLEEP": DEFAULT_SCAN_NO_RESULT_SLEEP,
        "ROAM_SUCCESS_SLEEP": DEFAULT_ROAM_SUCCESS_SLEEP,
        "ROAM_CROSS_FAIL_RETRY_COUNT": DEFAULT_ROAM_CROSS_FAIL_RETRY_COUNT,
        "ROAM_NO_RESULT_FAST_COUNT": DEFAULT_ROAM_NO_RESULT_FAST_COUNT,
        "ENABLE_GOOD_SIGNAL_GATE": DEFAULT_ENABLE_GOOD_SIGNAL_GATE,
        "ENABLE_STAGED_SCAN": DEFAULT_ENABLE_STAGED_SCAN,
        "SKIP_REDUNDANT_ACTIVE_SCAN": DEFAULT_SKIP_REDUNDANT_ACTIVE_SCAN,
        "HOME_PASSIVE": DEFAULT_HOME_PASSIVE,
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

            # 단일채널 home scan / 다중채널 direct active 파라미터.
            # enable=false면 종전 단일 액티브 스캔 경로로 회귀 — 현장에서 재배포 없이
            # 무회귀 폴백을 켤 수 있게 노출한다.
            staged = roam_config.get("STAGED_SCAN")
            if isinstance(staged, dict):
                _apply_section_values(
                    config,
                    staged,
                    [
                        ("enable", "ENABLE_STAGED_SCAN", parse_bool),
                        ("skip_redundant_active", "SKIP_REDUNDANT_ACTIVE_SCAN", parse_bool),
                        ("home_passive", "HOME_PASSIVE", parse_bool),
                    ],
                )

            # good-signal 분기의 backoff streak 리셋 게이트(모듈 상단 주석 참조).
            # enable=false면 종전대로 무조건 리셋하는 현장 kill-switch.
            gsg = roam_config.get("GOOD_SIGNAL_RESET_GATE")
            if isinstance(gsg, dict):
                _apply_section_values(
                    config,
                    gsg,
                    [
                        ("enable", "ENABLE_GOOD_SIGNAL_GATE", parse_bool),
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
            # ROAM_NO_RESULT_MAX_SLEEP 은 JSON 에서 읽지 않는다(상수 고정 — 감사 D2).
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
        f"predictive={ENABLE_PREDICTIVE_ROAM}, "
        f"ping_pong={ENABLE_PING_PONG_PREVENTION}, "
        f"rssi_source={'signal_avg' if USE_SIGNAL_AVG else 'signal'}, "
        f"extra_ssids={EXTRA_SSIDS}",
        _EXTRA_(),
    )

    logger.message(
        "info",
        f"[{IFACE}] Roaming effective values: "
        f"th_2g={DEFAULT_TH_2G}, th_5g={DEFAULT_TH_5G}, diff_th={DIFF_TH}, check_interval={CHECK_INTERVAL}, "
        f"predictive(boost={PREDICTIVE_THRESHOLD_BOOST}, window={TREND_WINDOW_SIZE}, max_age={TREND_HISTORY_MAX_AGE}), "
        f"ping_pong(window={PING_PONG_WINDOW}, max_roams={MAX_ROAMS_IN_WINDOW}, detect_time={PING_PONG_DETECTION_TIME}), "
        f"good_signal_gate(enable={ENABLE_GOOD_SIGNAL_GATE}, delta_db={GOOD_SIGNAL_GATE_DELTA_DB}, "
        f"grace_sec={GOOD_SIGNAL_GATE_GRACE_SEC})",
        _EXTRA_(),
    )

    return config


def apply_boot_roam_policy(policy):
    """Live JSON로 읽은 topology를 이 boot의 immutable snapshot으로 덮는다."""
    global GENERATE_NETWORK_BLOCKS, EXTRA_SSIDS
    if not isinstance(policy, dict):
        raise RoamPolicyError("boot roam policy must be an object")
    if policy.get("roaming_enabled") is not True:
        raise RoamPolicyError("wifi_roam owner is disabled by boot policy")
    generate = policy.get("generate_network_blocks")
    extras = policy.get("extra_ssids")
    if not isinstance(generate, bool):
        raise RoamPolicyError("generate_network_blocks must be boolean")
    if not isinstance(extras, list) or any(not isinstance(v, str) for v in extras):
        raise RoamPolicyError("extra_ssids must be a string array")
    GENERATE_NETWORK_BLOCKS = generate
    EXTRA_SSIDS = (
        [value.strip() for value in extras if value.strip()] if generate else []
    )


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

    def would_block(self, from_bssid, to_bssid):
        """로그 없는 차단 판정 — 후보 제외(evaluate_candidates)용.

        is_ping_pong 과 동일 규칙이되 경고를 남기지 않는다. 후보마다 매 tick
        호출되는 자리라 로그를 남기면 억제 구간 내내 warn 이 반복된다.
        반환: None(허용) / "too-many-roams" / "round-trip"."""
        now = time.time()

        # 최근 로밍 횟수 확인
        recent = [t for t, _f, _tb in self.roam_history
                  if now - t < self.window_seconds]
        if len(recent) >= self.max_roams:
            return "too-many-roams"

        # 반복 로밍 확인 (A→B→A)
        for t, f, t_b in reversed(list(self.roam_history)):
            if f == to_bssid and t_b == from_bssid:
                if now - t < PING_PONG_DETECTION_TIME:
                    return "round-trip"

        return None

    def is_ping_pong(self, from_bssid, to_bssid):
        """would_block + 경고 로그 — 로밍 실행 직전의 최종 방어선용."""
        reason = self.would_block(from_bssid, to_bssid)
        if reason == "too-many-roams":
            logger.message(
                "warn",
                f"[{IFACE}] Too many roams ({self.get_roam_count()}) in {self.window_seconds}s",
                _EXTRA_(),
            )
            return True
        if reason == "round-trip":
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
# 전역 인스턴스 (Global Instances)
# ==============================================================================
trend_tracker = None
ping_pong_preventer = None
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
    clear_own_lease(ROAM_CONDITION_FLAG)


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


def set_flag(on, path=None):
    # 기본 인자는 def 시점 바인딩 → None 센티널로 호출 시점 전역(iface별 재대입) 해석.
    if path is None:
        path = ROAM_CONDITION_FLAG
    write_flag(on, path)


def get_flag(path=None) -> bool:
    if path is None:
        path = ROAM_CONDITION_FLAG  # 호출 시점 전역 — __main__ iface별 재대입 반영
    return lease_active(path)


def clear_stale_roam_lease(path=None) -> bool:
    if path is None:
        path = ROAM_CONDITION_FLAG
    return clear_stale_lease(path)


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


def iw_scan_to_ap_lines(ssids, freqs, passive=False, include_wildcard=True):
    """로밍 판정 스캔: `iw scan`으로 실제 스캔(=wpa_supplicant BSS 테이블 충전)을 트리거하고,
    후보를 `wpa_cli scan_results`(=그 테이블)에서 뽑아 pipe 포맷 ap 라인으로 변환한다.
    후보가 곧 테이블이라 이후 wpa_cli roam이 대상 BSS를 항상 찾는다
    (mlanutl setuserscan은 테이블 미충전 → roam FAIL 근본원인이었다).
    ssids: 단일 str 또는 리스트(directed probe). freqs: MHz 리스트. 실패/결과없음 → None.

    passive=True: probe를 안 쏘는 패시브 스캔(`iw scan passive`). directed ssid 토큰을
      전부 생략하고 beacon만 수신 — 홈채널 후보 저부하 수집 및 baseline 통일용.
    include_wildcard=False: 와일드카드("") broadcast probe를 빼고 directed probe만 —
      액티브 폴백을 conf의 설정 SSID로만 좁힐 때(사용자 요구: scan_freq+ssid만) 사용."""
    with scan_transition_lock(IFACE) as acquired:
        if not acquired:
            logger.message("info", f"[{IFACE}] scan-transition busy; defer roam scan", _EXTRA_())
            return SCAN_TRANSITION_BUSY
        return _iw_scan_to_ap_lines(ssids, freqs, passive, include_wildcard)


def _iw_scan_to_ap_lines(ssids, freqs, passive=False, include_wildcard=True):
    """iw_scan_to_ap_lines 본체."""
    if isinstance(ssids, str):
        ssid_list = [ssids] if ssids else []
    else:
        ssid_list = [s for s in (ssids or []) if s]

    # 1) iw scan 트리거(동기, 테이블 충전). 다른 스캐너(logger 등)와 경합 시 -EBUSY 재시도.
    # iw 문법(5.19): `scan [freq <freq>*] ... [ssid <ssid>*|passive]` — `passive`는 ssid와
    # 같은 **맨 뒤** 그룹이라 freq 뒤에 와야 한다. freq 앞에 두면 iw가 인자를 파싱하지 못해
    # rc=1로 즉시 실패하고 스캔이 아예 실행되지 않는다(온타겟 실측: `scan passive freq 5180`
    # → rc=1/0.012s/0 BSS vs `scan freq 5180 passive` → rc=0/0.148s/13 BSS).
    cmd = ["iw", IFACE, "scan"]
    if freqs:
        cmd += ["freq"] + [str(f) for f in freqs]
    if passive:
        cmd.append("passive")
    else:
        # directed probe(allowed ssid) [+ 와일드카드("") probe]. iw 문법은 `ssid <ssid>*` —
        # ssid 키워드는 1회만 쓰고 값을 나열한다(키워드 반복은 iw 버전/드라이버에 따라 리터럴
        # 소비/파싱 붕괴 위험). NXP mlan 등은 ssid 지정 시 와일드카드를 안 보내므로
        # include_wildcard=True면 ""를 함께 넣어 beacon/broadcast 광범위 스캔을 보존한다.
        # 드라이버 max-scan-SSID 초과 방지: iw는 초과 시 -EINVAL로 스캔 전체를 실패시키므로
        # (→ 로밍 정지) directed SSID 수를 제한한다. wildcard 사용 시 슬롯 1개를 남겨 보존한다.
        # ssid_list=get_allowed_ssids라 live/base가 앞이므로 slice가 현재 네트워크를 우선 보존.
        probe = list(dict.fromkeys(ssid_list + ([""] if include_wildcard else [])))
        if len(probe) > MAX_SCAN_SSIDS:
            logger.message(
                "warn",
                f"[{IFACE}] scan SSIDs {len(probe)} > driver max {MAX_SCAN_SSIDS}; "
                f"capping directed probes (excess hidden SSIDs may be missed)",
                _EXTRA_(),
            )
            if include_wildcard:
                probe = probe[:MAX_SCAN_SSIDS - 1] + [""]
            else:
                probe = probe[:MAX_SCAN_SSIDS]
        if probe:
            cmd += ["ssid"] + probe

    # 어떤 명령으로 스캔했는지 남긴다 — 종전엔 실패 경로(timeout/rc≠0)에서도 rc·stderr만
    # 찍혀 passive/directed 여부·freq·probe SSID 를 사후에 복원할 수 없었다. 형식·레벨은
    # wifi_bgscan.py 의 스캔 로그와 동일(list repr — 와일드카드 probe 는 빈 문자열이라
    # ' '.join 하면 화면에서 사라져 directed-only 스캔과 구분이 불가능하다).
    # 위치: passive/directed 분기 합류 후 + 재시도 루프 밖 → 호출 1회당 정확히 1줄.
    logger.message("info", f"[{IFACE}] {cmd}", _EXTRA_())

    scanned_ok = False
    scan_elapsed_ms = 0
    for attempt in range(3):
        attempt_started = time.monotonic()
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
            scan_elapsed_ms = max(0, int((time.monotonic() - attempt_started) * 1000))
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

    # `iw scan` stdout도 커널 BSS cache 전체를 담지만 각 항목에 `last seen` age가 있다.
    # 이번 scan 실행시간(+짧은 여유) 이내에 관측된 BSSID만 membership으로 사용한다.
    # 이후 wpa_cli scan_results에서 이 집합만 남기면, supplicant에 오래 잔존한 BSS를
    # 방금 측정한 것처럼 현재 시각으로 덮어써 로밍하는 오류를 막으면서 roam 대상이
    # supplicant BSS table에 존재한다는 기존 계약도 유지된다.
    max_seen_age_ms = scan_elapsed_ms + IW_SCAN_FRESH_SLACK_MS
    fresh_bssids = fresh_bssids_from_iw_scan(r.stdout, max_seen_age_ms)
    if not fresh_bssids:
        logger.message(
            "warn",
            f"[{IFACE}] iw scan returned no BSS refreshed within "
            f"{max_seen_age_ms}ms — skip roam decision",
            _EXTRA_(),
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

    return scan_results_to_ap_lines(sr.stdout, fresh_bssids=fresh_bssids) or None


def fresh_bssids_from_iw_scan(iw_scan_stdout, max_age_ms):
    """`iw scan` dump에서 이번 scan 동안 갱신된 BSSID 집합을 추출한다.

    `iw`는 오래된 kernel BSS cache도 함께 출력하므로 BSS 블록의 `last seen: N ms ago`를
    scan 실행시간+여유와 비교한다. age가 없거나 형식이 깨진 블록은 fail-closed로 제외한다.
    NXP iw 출력은 associated BSS를 `aa:bb:...(on mlan0)`처럼 붙여 쓰므로 MAC 17자만 캡처한다.
    """
    try:
        max_age = max(0, int(max_age_ms))
    except (TypeError, ValueError):
        return set()

    fresh = set()
    current_bssid = None
    for line in (iw_scan_stdout or "").splitlines():
        if line.startswith("BSS "):
            bss = re.match(r"^BSS\s+(([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2})", line)
            current_bssid = bss.group(1).lower() if bss else None
            continue
        seen = re.match(r"^\s*last seen:\s*(\d+)\s*ms ago\s*$", line)
        if current_bssid and seen and int(seen.group(1)) <= max_age:
            fresh.add(current_bssid)
    return fresh


def scan_results_to_ap_lines(scan_results_stdout, fresh_bssids=None):
    """`wpa_cli scan_results`(탭 구분: bssid/freq/signal/flags/ssid, 첫 줄 헤더)를
    get_latest_scan이 파싱하는 pipe 포맷(`NN|channel|rssi|ld|bssid|freq|ssid`, 7필드)으로
    변환. fresh_bssids가 주어지면 이번 iw scan에서 갱신된 BSSID만 허용한다.
    헤더/형식불량/BSSID아님/미지 freq는 skip. ld=0 고정(iw 출력에 없는 필드)."""
    out = []
    idx = 0
    fresh = (
        {str(b).lower() for b in fresh_bssids}
        if fresh_bssids is not None else None
    )
    for line in (scan_results_stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        bssid = parts[0].strip().lower()
        if not re.match(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$", bssid):
            continue  # 헤더('bssid / frequency / ...') 등 skip
        if fresh is not None and bssid not in fresh:
            continue
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
# get_link_info — link.json 파싱
# ==============================================================================
_LINK_CACHE: Dict[str, Any] = {
    "mtime_ns": None,
    "value": None,
}
# link.json 갱신 정지(생산자 hang 등) 판정 임계(초). 생산자(wifi_logger_link)는 0.9s 주기라
# 30s 는 부하 스파이크 대비 넉넉한 마진. #118 감독화(Restart=always)는 프로세스 '사망'만
# 복구하므로, hang(살아있으나 갱신 정지) 시 mtime 캐시가 마지막 값을 무기한 반환하던
# 구멍을 나이 게이트로 막는다.
LINK_STALE_SEC = 30
# 에피소드당 1회 경고(매 tick 반복 발행 방지), 갱신 재개 시 리셋.
_LINK_STALE_WARNED = False


def get_link_info():
    """link.json 에서 연결 정보(bssid/freq/rssi/ssid)를 반환(mtime 캐시·stale 게이트)."""
    global _LINK_STALE_WARNED
    try:
        st = os.stat(LINK_LOG_FILE)
        mtime_ns = st.st_mtime_ns
        # stale 게이트는 캐시 히트 **이전** — 갱신이 멈추면 mtime 불변=캐시 히트 경로라,
        # 순서가 뒤면 마지막 값 무기한 판정이 그대로 남는다. stale 은 파일 부재와 동일하게
        # None(판정 보류, 메인루프 재시도). wall-clock 전진 스텝 시 일시 stale 로 보일 수
        # 있으나 생산자의 다음 기록(~1s)으로 자가 치유된다.
        age = time.time() - st.st_mtime
        if age > LINK_STALE_SEC:
            if not _LINK_STALE_WARNED:
                logger.message(
                    "warn",
                    f"[{IFACE}] {LINK_LOG_FILE} stale ({age:.0f}s > {LINK_STALE_SEC}s) — "
                    f"생산자(wifi_logger_link) 갱신 정지 의심, 로밍 판정 보류",
                    _EXTRA_(),
                )
                _LINK_STALE_WARNED = True
            return None
        _LINK_STALE_WARNED = False
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


            _LINK_CACHE["mtime_ns"] = mtime_ns
            _LINK_CACHE["value"] = result

            return result

    except Exception as e:
        return None




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
def log_scan_candidates(candidates, src):
    """후보 엔트리를 info 로 기록한다.

    **파싱 시점이 아니라 실제 판정에 쓰이는 시점에 호출하는 것이 원칙.**
    현재 자동 로밍은 최신 scan 결과만 넘긴다. cache src는 수동 진단/구버전 호출 호환용이다."""
    for i, entry in enumerate(candidates):
        logger.message(
            "info",
            f"[{IFACE}] [{src}] roam candidate {i}: "
            f"ts={entry['timestamp']}, ssid={entry['ssid']}, bssid={entry['bssid']}, "
            f"ch={entry['channel']}, freq={entry['freq']}, ld={entry['ld']}, "
            f"rssi={entry['rssi']}(th={entry['rssi_th']})",
            _EXTRA_(),
        )


def parse_scan_entries(
    scan_lines, timestamp, allowed_set=None, src="scan", log=True
):
    """pipe 포맷 스캔 라인(`NN|ch|rssi|ld|bssid|freq|ssid`) 리스트를 로밍 후보 엔트리로
    변환한다. 파일(get_latest_scan) 경로와 메모리(홈 패시브/액티브 폴백 스캔) 경로가
    동일 파서를 공유하도록 순수 함수로 분리. allowed_set에 든 SSID + (WPA_FREQ 설정 시)
    그 채널만 후보로 채택. RSSI 내림차순 정렬해 반환.

    src: 로그에 붙는 소스 라벨("scan"=이번 tick 실측, "cache"=ap.log 배경 블록).
         포맷이 같은 두 출력이 한 tick 에 연달아 나와 중복으로 보이던 것을 구분한다.
    log: False 면 관측 행·후보 행을 모두 억제하고 파싱만 한다. 호출자가 판정에 실제로
         쓰는 시점에 log_scan_candidates 로 따로 남기기 위한 스위치(위 독스트링 참조)."""
    allowed_set = allowed_set or set()
    entries = []


    for line in scan_lines:
        if re.match(r"^\d{2}\|", line):
            fields = line.strip().split("|")
            if len(fields) >= 7:
                try:
                    channel = int(fields[1].strip())
                    rssi = int(fields[2].strip())
                    ld = int(
                        fields[3].strip()
                    )  # 포맷상 자리만 유지(항상 0) — 소비처 없음
                    bssid = fields[4].strip().lower()
                    ssid = fields[6].strip()
                    rssi_th = WPA_TH_2G if channel < 36 else WPA_TH_5G
                    freq = channel_to_freq(channel)

                    if freq is None:
                        continue

                    freq_str = str(freq)

                    # 후보 필터(아래 if)보다 앞이라 **필터 탈락 항목까지** 남는다 —
                    # "스캔엔 보였는데 왜 후보가 아닌가"(allowed_set/WPA_FREQ 게이트)
                    # 진단의 유일한 근거라 유지한다.
                    if log:
                        logger.message(
                            "info",
                            f"[{IFACE}] [{src}] ssid:{ssid}, bssid:{bssid}, ch:{channel}, "
                            f"freq:{freq}, rssi:{rssi}, th:{rssi_th}, ld:{ld}",
                            _EXTRA_(),
                        )

                    # scan_freq(WPA_FREQ) 미설정이면 채널 제한 없이 동일 SSID를 후보로
                    # 허용한다(동작주파수 제한 없이 운용하는 배포 지원). 설정돼 있으면 그
                    # 채널로 스코프(기존 동작 유지). 빈 WPA_FREQ에서 후보 0이 되어 로밍이
                    # 죽던 근본원인 수정.
                    if ssid in allowed_set and (not WPA_FREQ or freq_str in WPA_FREQ):
                        entries.append(
                            {
                                "timestamp": timestamp,
                                "channel": channel,
                                "freq": freq,
                                "rssi": rssi,
                                "rssi_th": rssi_th,
                                "ld": ld,
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

    if log:
        log_scan_candidates(candidates, src)

    return candidates


def get_latest_scan(st, allowed_ssids=None, log=True, src="cache"):
    """ap.log(배경 스캔 캐시)의 마지막 시각 블록을 읽어 후보 엔트리 + 그 블록의
    타임스탬프를 반환. 파싱은 parse_scan_entries가 담당(메모리 경로와 공유).

    log=False 면 파싱만 하고 로그를 남기지 않는다 — 캐시를 **판정에 쓸지 모르는 시점**에
    선반영하는 staged_scan Stage 0 스냅샷용. 실제로 쓰는 시점에 호출자가
    log_scan_candidates 로 남긴다.

    src 는 **파일이 아니라 그 블록의 출처**를 뜻한다. 기본 "cache"(bgscan 등 배경 스캔이
    남긴 블록)이지만, 레거시 비-staged 경로는 이번 tick 에 자기가 스캔한 결과를 ap.log 에
    쓰고(save_with_timestamp) 곧바로 되읽으므로 그 호출은 "scan" 을 넘겨야 한다 — 안 그러면
    전경 실측이 배경 캐시로 오라벨돼, 소스 구분이 가장 필요한 폴백 모드에서 라벨이 거짓이
    된다."""
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
    start_idx = 0
    for i in reversed(range(len(lines))):
        match = SCAN_TIMESTAMP_RE.fullmatch(lines[i].strip())
        if match:
            timestamp = match.group("plain") or match.group("bracketed")
            start_idx = i + 1
            break

    if timestamp is None:
        logger.message("err", f"[{IFACE}] timestamp is not exist", _EXTRA_())
        return [], None

    candidates = parse_scan_entries(
        lines[start_idx:], timestamp, allowed_set, src=src, log=log
    )
    return candidates, timestamp


def parse_supplicant_conf(path, def_th2g=None, def_th5g=None):
    """
    wpa_supplicant.conf 에서 기본 ssid / 공통 freq_list / `#!TH_CONNECT=` 를 파싱한다.

    주파수 우선순위는 전역 freq_list > 첫 network 블록 freq_list > 첫 블록
    scan_freq다. 마지막 두 경로는 wifi_init 부팅 정규화 전 legacy conf 호환용이다.

    로밍 임계(th2g/th5g)는 **conf 에서 읽지 않는다** — 인자로 받은 JSON 값을 그대로
    돌려주며, 인자가 없을 때만 모듈 기본값(DEFAULT_TH_*)으로 떨어진다. 종전에는 conf 의
    `#!TH_2G=`/`#!TH_5G=` 마커가 JSON 을 덮어쓰는 2차 경로였으나 제거했다(함수 하단 주석).

    반면 `#!TH_CONNECT=` 는 **conf 에서 계속 읽는 유일한 임계값**이다. 로밍 임계가 아니라
    연결 시도 최소 RSSI 라 용도가 다르고, 실제 소비도 패치된 wpa_supplicant 쪽에서 이뤄진다
    (이 값은 여기서 파싱해 전역에 싣기만 한다).

    Args:
        path: conf 파일 경로
        def_th2g: 2.4GHz 로밍 임계값 (JSON에서 로드)
        def_th5g: 5GHz 로밍 임계값 (JSON에서 로드)

    Returns:
        tuple: (ssid, freqs, th2g, th5g, th_connect)
    """
    ssid = None
    global_freqs = []
    base_freqs = []
    base_scan_freqs = []
    th_connect = None
    in_network = False
    network_index = 0

    with open(path, "r") as f:
        for raw_line in f:
            line = raw_line.strip()
            # 다중블록 모드: 자동생성 센티넬 이전(첫=기본 network 블록)까지만 파싱.
            # 센티넬 이후 extra 블록의 ssid=/freq_list=/TH 가 기본값을 덮어쓰지 않게 break.
            # (단일블록=센티넬 없음 → 영향 없음. 센티넬은 wifi_init_config_lib.sh 와 동일 prefix.)
            if line.startswith("# >>> wifi_extra_ssid"):
                break
            if not line:
                continue
            if line.startswith("#!TH_CONNECT="):
                try:
                    th_connect = int(line.split("=")[1])
                except ValueError:
                    logger.message(
                        "err",
                        f"[{IFACE}] TH_CONNECT : {th_connect} is invalid in {path}",
                        _EXTRA_(),
                    )
                continue
            if line.startswith("#"):
                continue
            if re.match(r"^network\s*=\s*\{", line):
                in_network = True
                network_index += 1
                continue
            if in_network and line.startswith("}"):
                in_network = False
                continue

            if in_network and network_index == 1 and line.startswith("ssid="):
                if ssid is None:
                    ssid = line.split("=", 1)[1].strip().strip('"')
            elif not in_network and line.startswith("freq_list="):
                if not global_freqs:
                    value = line.split("=", 1)[1].split("#", 1)[0]
                    global_freqs = value.strip().split()
            elif in_network and network_index == 1 and line.startswith("freq_list="):
                if not base_freqs:
                    value = line.split("=", 1)[1].split("#", 1)[0]
                    base_freqs = value.strip().split()
            elif in_network and network_index == 1 and line.startswith("scan_freq="):
                if not base_scan_freqs:
                    value = line.split("=", 1)[1].split("#", 1)[0]
                    base_scan_freqs = value.strip().split()

    freqs = global_freqs or base_freqs or base_scan_freqs

    # 로밍 임계는 JSON(mlanN.roaming.DEFAULT_TH_*) 단일 소스다.
    # 종전에는 conf 의 `#!TH_2G=`/`#!TH_5G=` 마커가 JSON 을 덮어썼다. 그 마커를 생성하는
    # 코드가 dist 어디에도 없고 출하 conf 에도 없어 레거시·수동 편집 파일에서만 발현했는데,
    # 설정 경로가 둘로 갈리는 탓에 `wifi <if> roam th` 로 값을 바꿔도 조용히 무시되는 사고가
    # 가능했다(wifi.sh 가 경고를 내지만 이미 저장한 뒤의 사후 통보다). 마커 파싱을 제거해
    # 임계 설정 경로를 JSON 하나로 통일한다.
    th2g = def_th2g if def_th2g is not None else DEFAULT_TH_2G
    th5g = def_th5g if def_th5g is not None else DEFAULT_TH_5G

    return ssid, freqs, th2g, th5g, th_connect


def reload_supplicant_conf_if_changed(path):
    """wpa_cli reconfigure 등으로 wpa_supplicant conf 가 런타임 변경되면 재파싱해
    전역 WPA_SSID/WPA_FREQ/WPA_TH_2G/WPA_TH_5G/WPA_TH_CONNECT 를 갱신한다.

    - mtime 이 바뀐 경우에만 파싱한다(1초 고빈도 루프의 매 tick 파일 I/O 회피).
    - 재파싱 실패 시 직전 캐시 값을 유지한다(wifi_bgscan 의 build() 재로드와 동일 정책).
    conf 를 시작 시 1회만 읽던 기존 동작은 ssid/freq_list 변경을 동반한 reconfigure
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
    # th2g/th5g 는 이제 conf 가 아니라 인자로 넘긴 DEFAULT_TH_*(JSON 값) 그대로다. 따라서
    # conf 파일만 바뀐 경우 이 둘은 changed 에 기여하지 않는다(ssid/freq_list/TH_CONNECT 가 판정).
    # 반대로 SIGHUP 경로에서는 reload_roaming_config 가 DEFAULT_TH_* 를 갱신한 뒤
    # **WPA_CONF_MTIME 을 None 으로 리셋**하므로(reload_roaming_config 말미 참조) 다음 호출의
    # mtime 비교가 성립해 재파싱이 유도된다. 그때 새 DEFAULT_TH_* 가 def_th2g/def_th5g 로
    # 전달돼 직전 WPA_TH_* 와 달라지므로 changed=True — JSON 변경을 실제 판정값까지 전파하는
    # 의도된 흐름이다. conf 파일 자체의 mtime 이 바뀌어야 하는 것이 아니다.
    changed = (ssid, freqs) != (WPA_SSID, WPA_FREQ) or (th2g, th5g, th_connect) != (WPA_TH_2G, WPA_TH_5G, WPA_TH_CONNECT)
    WPA_SSID, WPA_FREQ, WPA_TH_2G, WPA_TH_5G, WPA_TH_CONNECT = ssid, freqs, th2g, th5g, th_connect
    WPA_CONF_MTIME = mtime
    if changed:
        logger.message(
            "info",
            f"[{IFACE}] wpa conf reloaded (runtime reconfigure): ssid={WPA_SSID}, "
            f"freq_list={WPA_FREQ}, TH_2G={WPA_TH_2G}, TH_5G={WPA_TH_5G}, TH_CONNECT={WPA_TH_CONNECT}",
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
      3) generate_network_blocks(모드 결정자) / extra_ssids 는 모드와 무관하게
         boot snapshot 필드이므로 재부팅 전용(경고 후 부팅값 유지).
    인스턴스 파라미터는 재생성이 아닌 필드 갱신 → 이력(roam_history 등) 보존.
    적용 시 WPA_CONF_MTIME 을 리셋해 같은 사이클 wpa conf 재파싱을 유도(JSON DEFAULT_TH_*
    변경을 실제 판정값 WPA_TH_* 까지 전파). 반환: 적용 수행 여부."""
    global GENERATE_NETWORK_BLOCKS, EXTRA_SSIDS, WPA_CONF_MTIME
    global ping_pong_preventer, cross_ssid_cooldown, trend_tracker
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
    # generate_network_blocks는 런타임 전환 금지(재부팅 전용). 키가 '명시적으로' 다른
    # 값으로 바뀐 경우에만 경고 — 키 부재로 인한 False 수렴은 조용히 복원(오탐 방지).
    # topology/extra는 모드와 무관하게 동일한 boot snapshot에 속한다. Mode B에서
    # extra가 현재 사용되지 않더라도 mutable JSON 값을 전역으로 누출하면 향후 consumer가
    # topology hot-switch를 만들 수 있으므로 두 필드를 각각 무조건 복원한다.
    if GENERATE_NETWORK_BLOCKS != old_gen:
        if "generate_network_blocks" in roam_cfg:
            logger.message(
                "warn",
                f"[{iface}] generate_network_blocks change ignored at runtime "
                f"(requires reboot; keeping boot snapshot {old_gen})",
                _EXTRA_(),
            )
    GENERATE_NETWORK_BLOCKS = old_gen
    if "extra_ssids" in roam_cfg and EXTRA_SSIDS != old_extra:
        logger.message(
            "warn",
            f"[{iface}] extra_ssids change ignored at runtime "
            f"(requires reboot; keeping boot snapshot)",
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
    # cross_ssid_cooldown은 의도적으로 갱신만(생성 없음): 별도 enable이 없고 존재가
    # GENERATE_NETWORK_BLOCKS(런타임 전환 금지, 재부팅 전용)에 연동되므로
    # 런타임에 None→생성이 필요한 상황 자체가 없다.
    if cross_ssid_cooldown is not None:
        cross_ssid_cooldown.retry_count = max(0, ROAM_CROSS_FAIL_RETRY_COUNT)
    WPA_CONF_MTIME = None  # 같은 사이클 wpa conf 재파싱 → 새 DEFAULT_TH_* 로 WPA_TH_* 재산출
    logger.message("notice", f"[{iface}] runtime roaming config reloaded (SIGHUP)", _EXTRA_())
    return True


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

    with scan_transition_lock(IFACE) as acquired:
        if not acquired:
            logger.message("info", f"[{IFACE}] scan-transition busy; defer roam", _EXTRA_())
            return SCAN_TRANSITION_BUSY
        return _roam_to_bssid_locked(from_bssid, to_bssid, channel, freq, rssi)


def _roam_to_bssid_locked(from_bssid, to_bssid, channel=None, freq=None, rssi=None):
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
        reply = (en.stdout or "").strip().split("\n", 1)[0].strip()
        if en.returncode != 0 or reply != "OK":
            logger.message(
                "err",
                f"[{iface}] enable_network all failed: "
                f"{(en.stdout or en.stderr or '').strip() or f'rc={en.returncode}'}",
                _EXTRA_(),
            )
            return False
        return True
    except Exception as e:
        logger.message("err", f"[{iface}] enable_network all error: {e}", _EXTRA_())
        return False


def _set_network_bssid(iface, network_id, bssid):
    """network의 in-memory BSSID constraint를 설정/해제하고 OK까지 확인한다."""
    try:
        result = subprocess.run(
            ["wpa_cli", "-i", iface, "bssid", network_id, bssid],
            capture_output=True,
            text=True,
            timeout=10,
        )
        reply = (result.stdout or "").strip().split("\n", 1)[0].strip()
        if result.returncode == 0 and reply == "OK":
            return True
        detail = (result.stdout or result.stderr or "").strip() or f"rc={result.returncode}"
        logger.message(
            "err",
            f"[{iface}] network bssid command rejected (id={network_id}, bssid={bssid}): {detail}",
            _EXTRA_(),
        )
        return False
    except Exception as e:
        logger.message(
            "err",
            f"[{iface}] network bssid command error (id={network_id}, bssid={bssid}): {e}",
            _EXTRA_(),
        )
        return False


def _wpa_reconfigure(iface):
    """Canonical conf reload를 요청하고 ctrl reply까지 엄격히 확인한다."""
    try:
        result = subprocess.run(
            ["wpa_cli", "-i", iface, "reconfigure"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        reply = (result.stdout or "").strip().split("\n", 1)[0].strip()
        if result.returncode == 0 and reply == "OK":
            return True
        logger.message(
            "err",
            f"[{iface}] reconfigure recovery rejected: "
            f"{(result.stdout or result.stderr or '').strip() or f'rc={result.returncode}'}",
            _EXTRA_(),
        )
    except Exception as e:
        logger.message("err", f"[{iface}] reconfigure recovery error: {e}", _EXTRA_())
    return False


def selection_cleanup_marker_path(iface):
    """Mode A 선택 상태가 아직 복구되지 않았음을 나타내는 per-iface WAL."""
    return os.path.join(
        WIFI_SELECTION_STATE_DIR, f".{iface}.selection-cleanup-pending"
    )


def _sync_parent_directory(path):
    """Atomic replace/unlink의 directory entry를 durable하게 한다."""
    directory = os.path.dirname(path) or "."
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(directory, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _mark_selection_cleanup_pending(iface, network_id, phase="before-pin"):
    """BSSID/network 변경 전에 복구 의무를 메모리+atomic JSON WAL로 기록."""
    network_id = str(network_id)
    # disk I/O보다 먼저 gate를 세운다. WAL 기록이 실패하면 caller는 상태를 바꾸지
    # 않지만, 결과가 모호한 replace/fsync 실패도 다음 loop에서 보수적으로 복구한다.
    _SELECTION_CLEANUP_PENDING[iface] = network_id
    marker = selection_cleanup_marker_path(iface)
    tmp = f"{marker}.tmp.{os.getpid()}"
    fd = None
    try:
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            fd = None  # fdopen이 소유
            json.dump(
                {
                    "version": 1,
                    "network_id": network_id,
                    "phase": phase,
                },
                f,
                separators=(",", ":"),
            )
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, marker)
        _sync_parent_directory(marker)
        return True
    except Exception as e:
        logger.message(
            "emerg",
            f"[{iface}] cannot persist selection cleanup marker: {e}",
            _EXTRA_(),
        )
        return False
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _clear_selection_cleanup_pending(iface):
    marker = selection_cleanup_marker_path(iface)
    try:
        os.unlink(marker)
    except FileNotFoundError:
        _SELECTION_CLEANUP_PENDING.pop(iface, None)
        return True
    except OSError as e:
        logger.message(
            "err", f"[{iface}] cannot clear selection cleanup marker: {e}", _EXTRA_()
        )
        return False
    try:
        _sync_parent_directory(marker)
    except OSError as e:
        # 현재 namespace에서는 제거됐다. power loss로 marker가 다시 보이면
        # 다음 기동에 보수적으로 cleanup을 한 번 더 실행한다.
        logger.message(
            "warn", f"[{iface}] cleanup marker directory sync failed: {e}", _EXTRA_()
        )
    _SELECTION_CLEANUP_PENDING.pop(iface, None)
    return True


def _selection_cleanup_is_pending(iface):
    """디스크 WAL 또는 process-local gate 중 하나라도 남았는지 확인."""
    return iface in _SELECTION_CLEANUP_PENDING or os.path.exists(
        selection_cleanup_marker_path(iface)
    )


def _restore_network_selection_state(iface, network_id):
    """BSSID pin/all-network state를 bounded retry 후 canonical reconfigure로 복구한다."""
    for _ in range(2):
        clear_ok = _set_network_bssid(iface, network_id, BSSID_CLEAR_ADDR)
        enable_ok = _enable_network_all(iface)
        if clear_ok and enable_ok:
            return _clear_selection_cleanup_pending(iface)
        time.sleep(0.1)

    logger.message(
        "warn",
        f"[{iface}] transient selection cleanup failed; reloading canonical conf",
        _EXTRA_(),
    )
    if _wpa_reconfigure(iface):
        time.sleep(0.2)
        clear_ok = _set_network_bssid(iface, network_id, BSSID_CLEAR_ADDR)
        enable_ok = _enable_network_all(iface)
        if clear_ok and enable_ok and _clear_selection_cleanup_pending(iface):
            logger.message(
                "notice",
                f"[{iface}] selection state recovered by canonical reconfigure; "
                "transition result will be retried",
                _EXTRA_(),
            )
            # reconfigure는 association을 다시 시작할 수 있어 이전 COMPLETED 증명이
            # 더는 유효하지 않다. cleanup은 끝났지만 이번 transition은 실패.
            return False

    _mark_selection_cleanup_pending(iface, network_id)
    logger.message(
        "err",
        f"[{iface}] selection cleanup remains unresolved (id={network_id}); "
        "blocking new roam decisions until recovery",
        _EXTRA_(),
    )
    return False


def retry_pending_selection_cleanup(iface):
    """Pending WAL/gate가 있으면 새 roam 판정 전에 cleanup을 재시도한다."""
    marker = selection_cleanup_marker_path(iface)
    if iface in _SELECTION_CLEANUP_PENDING:
        network_id = _SELECTION_CLEANUP_PENDING[iface]
    else:
        try:
            with open(marker, "r") as f:
                raw = f.read().strip()
        except FileNotFoundError:
            return True
        except OSError as e:
            logger.message(
                "err", f"[{iface}] cannot read selection cleanup marker: {e}", _EXTRA_()
            )
            return False

        network_id = ""
        try:
            record = json.loads(raw)
            if (
                isinstance(record, dict)
                and record.get("version") == 1
                and str(record.get("network_id", "")).isdigit()
                and record.get("phase") == "before-pin"
            ):
                network_id = str(record["network_id"])
        except (TypeError, ValueError):
            pass
        # json.loads("1")은 예외 없이 int를 반환하므로 except 안에 두면 legacy
        # marker가 invalid로 떨어진다. 구조화 record가 아니어도 숫자-only면 수용한다.
        if not network_id and raw.isdigit():
            network_id = raw

        # WAL을 읽은 직후부터는 파일이 외부에서 사라져도 현재 daemon이 gate를 유지한다.
        _SELECTION_CLEANUP_PENDING[iface] = network_id

    if not str(network_id).isdigit():
        logger.message(
            "err",
            f"[{iface}] invalid selection cleanup marker; trying canonical recovery",
            _EXTRA_(),
        )
        if (
            _wpa_reconfigure(iface)
            and _enable_network_all(iface)
            and _clear_selection_cleanup_pending(iface)
        ):
            return True
        logger.message(
            "err", f"[{iface}] selection cleanup remains unresolved", _EXTRA_()
        )
        return False

    logger.message(
        "warn",
        f"[{iface}] retrying pending selection cleanup (id={network_id})",
        _EXTRA_(),
    )
    _restore_network_selection_state(iface, network_id)
    if _selection_cleanup_is_pending(iface):
        logger.message(
            "err", f"[{iface}] selection cleanup remains unresolved", _EXTRA_()
        )
        return False
    logger.message(
        "notice", f"[{iface}] pending selection cleanup resolved", _EXTRA_()
    )
    return True


def select_network_for_ssid(iface, to_ssid, to_bssid):
    """Mode A cross-SSID: target block를 목표 BSSID로 pin한 뒤 정확히 확인한다.

    pin은 wpa 메모리에만 두며 save_config하지 않는다. pin이 수락된 뒤에는 모든 종료
    경로에서 `bssid <id> 00:00:00:00:00:00`를, select 시도 뒤에는
    `enable_network all`을 실행해
    장애 시 native fallback 후보를 복원한다.
    """
    target_bssid = (to_bssid or "").strip().lower()
    if not target_bssid:
        logger.message("err", f"[{iface}] select_network: empty target BSSID", _EXTRA_())
        return False

    try:
        lst = subprocess.run(
            ["wpa_cli", "-i", iface, "list_networks"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as e:
        logger.message("err", f"[{iface}] list_networks error: {e}", _EXTRA_())
        return False
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

    pin_attempted = False
    completed = False
    cleanup_ok = True
    try:
        # pin/select 변경 전 cleanup 의무를 write-ahead한다. 이 marker가 있어야
        # BSSID 명령 직후 SIGKILL/OOM으로 finally를 못 거쳐도 재시작 후 복구한다.
        if not _mark_selection_cleanup_pending(iface, nid):
            logger.message(
                "emerg",
                f"[{iface}] refusing select_network: cleanup obligation "
                f"could not be persisted (id={nid})",
                _EXTRA_(),
            )
            return False
        # ctrl timeout/exception은 요청이 supplicant에 전달된 뒤 reply만 유실된 상태일 수
        # 있다. 호출 *전* attempted를 세워 모든 ambiguous 경로가 finally clear를 거친다.
        pin_attempted = True
        if not _set_network_bssid(iface, nid, target_bssid):
            return False
        logger.message(
            "notice",
            f"[{iface}] Cross-SSID select_network: id={nid} ssid={to_ssid} "
            f"bssid={target_bssid}",
            _EXTRA_(),
        )
        sel = subprocess.run(
            ["wpa_cli", "-i", iface, "select_network", nid],
            capture_output=True,
            text=True,
            timeout=10,
        )
        reply = (sel.stdout or "").strip().split("\n", 1)[0].strip()
        if sel.returncode != 0 or reply != "OK":
            detail = (sel.stdout or sel.stderr or "").strip() or f"rc={sel.returncode}"
            logger.message(
                "err",
                f"[{iface}] select_network rejected (id={nid}): {detail}",
                _EXTRA_(),
            )
        else:
            for _ in range(6):
                stt = subprocess.run(
                    ["wpa_cli", "-i", iface, "status"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if stt.returncode != 0:
                    detail = (stt.stdout or stt.stderr or "").strip()
                    logger.message(
                        "err",
                        f"[{iface}] status proof failed (rc={stt.returncode}): "
                        f"{detail or 'no reply'}",
                        _EXTRA_(),
                    )
                    time.sleep(0.5)
                    continue
                state = cur_ssid = cur_id = cur_bssid = None
                for ln in stt.stdout.splitlines():
                    if ln.startswith("wpa_state="):
                        state = ln.split("=", 1)[1].strip()
                    elif ln.startswith("ssid="):
                        cur_ssid = ln.split("=", 1)[1].strip()
                    elif ln.startswith("id="):
                        cur_id = ln.split("=", 1)[1].strip()
                    elif ln.startswith("bssid="):
                        cur_bssid = ln.split("=", 1)[1].strip().lower()
                if (
                    state == "COMPLETED"
                    and cur_ssid == to_ssid
                    and cur_id == nid
                    and cur_bssid == target_bssid
                ):
                    completed = True
                    break
                time.sleep(0.5)
    except Exception as e:
        logger.message(
            "err", f"[{iface}] select_network error: {to_ssid}: {e}", _EXTRA_()
        )
    finally:
        # pin rejection도 transport 관점에서는 상태가 모호하므로 clear/all-enable을
        # 묶어 bounded retry하고, 필요하면 canonical conf reconfigure로 자동복구한다.
        if pin_attempted:
            cleanup_ok = _restore_network_selection_state(iface, nid)

    if completed and cleanup_ok:
        logger.message(
            "info",
            f"[{iface}] Cross-SSID select_network successful: {to_ssid} "
            f"(id={nid}, bssid={target_bssid})",
            _EXTRA_(),
        )
        return True

    cleanup_detail = (
        "cleanup remains unresolved"
        if _selection_cleanup_is_pending(iface)
        else "candidates restored"
    )
    logger.message(
        "err",
        f"[{iface}] select_network: not COMPLETED@target for {to_ssid} "
        f"(id={nid}, bssid={target_bssid}), {cleanup_detail}",
        _EXTRA_(),
    )
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
        with scan_transition_lock(iface) as acquired:
            if not acquired:
                logger.message("info", f"[{iface}] scan-transition busy; defer cross-SSID roam", _EXTRA_())
                return SCAN_TRANSITION_BUSY
            ok = select_network_for_ssid(iface, to_ssid, to_bssid)
    else:
        # 모드 B: connect_to_ssid가 내부에서 자체 check+add_roam 수행(기존 동작 유지)
        ok = connect_to_ssid(iface, to_ssid, from_bssid, to_bssid)
    # Mode A는 목표 BSSID pin+status exact match로 이미 보장했고, Mode B는 connect
    # 과정에서 펌웨어가 실제 BSS를 선택한다. 두 경우 모두 통지에는 전환 후 status의
    # 권위 BSSID를 사용한다(link.json은 ~1s 주기라 직후 stale일 수 있음).
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
def check_roam_conditions(station, roam_ap, trend, baseline_rssi=None):
    """
    개선된 로밍 조건 확인

    Args:
        station: 현재 연결 정보
        roam_ap: 로밍 후보 AP 정보
        trend: RSSI 추세
        baseline_rssi: 현재 AP RSSI 비교 기준. None이면 station["rssi"](station dump)
            사용. 단계형 스캔에서는 홈채널 패시브 스캔에서 뽑은 현재 AP RSSI를 넘겨,
            후보(scan 스케일)와 동일 소스로 diff를 계산한다(소스 이질성 제거).

    Returns:
        tuple: (should_roam, reason)
    """
    if baseline_rssi is None:
        baseline_rssi = station["rssi"]
    # 기본 조건: RSSI 차이. 하락 추세(PREDICTIVE 활성)면 임계를 3dB 완화해 조기 로밍 —
    # 완화는 이 게이트 **이전에** 임계 자체에 적용해야 실효한다(종전엔 게이트 뒤에 있어
    # 도달 시점에 diff ≥ DIFF_TH 가 이미 보장된 dead code — reason 문자열만 바꿨다).
    # 완화 구간 후보도 아래 load 게이트는 동일하게 통과해야 한다. diff < effective_diff_th
    # 후보는 이 게이트에서 즉시 차단되며, 완화 임계는 min(DIFF_TH, 1) 로 하한 클램프한다 —
    # DIFF_TH<3 극단 설정에서 임계가 음수로 내려가면 **더 나쁜 AP**(음수 diff)가 통과하고,
    # LOAD 활성 시 load 점수가 score(=diff×10 + load개선×2)를 양수로 반전시켜 채택될 수
    # 있기 때문. 하한이 상수 1 이 아니라 min(DIFF_TH, 1) 인 이유: 하한 1 은 음수 차단이라는
    # 목적에 필요한 것보다 과해서, DIFF_TH=0("이득 무관")을 설정해도 falling 추세에서만
    # max(1, -3)=1 로 되살아나 diff=0 후보가 차단됐다(설정 의미와 불일치). min(DIFF_TH, 1)
    # 은 DIFF_TH=0 일 때만 하한을 0 으로 낮추고, DIFF_TH>=1 에서는 종전과 동일하게 1 이다.
    # ⚠️ 아래 표는 **이 완화 분기 안에서만** 성립한다 — 즉 ENABLE_PREDICTIVE_ROAM=True
    # 이고 trend 가 TREND_FALLING 일 때. 그 밖의 모든 경우는 effective_diff_th = DIFF_TH
    # 로 **설정값이 그대로** 쓰인다. 출하 기본은 PREDICTIVE_ROAM.enable=false 라 이 분기
    # 자체가 비활성이다.
    #   DIFF_TH=0 → max(0, -3) = 0   (변경: 종전 1)
    #   DIFF_TH=1 → max(1, -2) = 1   (동일)
    #   DIFF_TH=2 → max(1, -1) = 1   (동일)
    #   DIFF_TH=3 → max(1,  0) = 1   (동일)
    #   DIFF_TH=4 → max(1,  1) = 1   (동일)
    #   DIFF_TH≥5 → max(1, TH-3) = TH-3   (동일)
    # 즉 DIFF_TH=0 외에는 결과가 바뀌지 않는다. 다만 완화 분기 안에서는 DIFF_TH 1~4 가
    # 모두 1 로 수렴해 설정 해상도가 사라지는데, 이는 하한 1 의 기존 동작을 그대로 둔
    # 결과다(무회귀 우선). 일관성을 택하려면 하한을 0 으로 낮춰 "3dB 완화, 단 음수 금지"
    # 로 통일할 수 있으나, 그 경우 DIFF_TH 1~3 의 falling 동작이 함께 바뀐다.
    rssi_diff = roam_ap["rssi"] - baseline_rssi

    effective_diff_th = DIFF_TH
    is_falling_trend = False  # '완화 구간 진입'이 아니라 'falling 완화 활성' 플래그
    if ENABLE_PREDICTIVE_ROAM and trend == RSSITrendTracker.TREND_FALLING:
        effective_diff_th = max(min(DIFF_TH, 1), DIFF_TH - 3)
        is_falling_trend = True

    if rssi_diff < effective_diff_th:
        return (False, f"RSSI diff too small: {rssi_diff}dB < {effective_diff_th}dB")

    # falling 이면 diff ≥ DIFF_TH(완화 불필요 구간)여도 'Falling trend' 사유를 붙인다 —
    # 종전 reason 체계 유지(사유='추세 활성' 표시이지 '완화 구간 통과' 표시가 아님).
    if is_falling_trend:
        return (True, f"Falling trend, RSSI diff: {rssi_diff}dB")

    return (True, f"RSSI diff: {rssi_diff}dB")


def baseline_from_entries(entries, cur_bssid, default_rssi):
    """스캔 엔트리에서 현재 연결 AP(cur_bssid)의 RSSI를 찾아 반환(baseline 통일용).
    없으면 default_rssi(=station dump RSSI) 폴백. 현재 AP는 홈채널 스캔에 항상 잡히므로
    이 값이 후보와 동일 소스(scan 스케일)라 diff 편향이 제거된다."""
    if not cur_bssid:
        return default_rssi
    for e in entries:
        if e.get("bssid") == cur_bssid:
            return e["rssi"]
    return default_rssi


def evaluate_candidates(entries, station, trend, cooldown, live_ssid, baseline_rssi):
    """후보 엔트리 중 최적 로밍 대상을 고른다(현재 AP 제외, cross-SSID cooldown 반영).
    baseline_rssi=현재 AP 비교 기준(홈 패시브 스캔 스케일로 통일). 점수=RSSI diff*10.
    반환: (best_ap, best_reason, best_score). 없으면 (None, "", 0).

    갱신 조건이 `score > best_score`(초기 0)가 아니라 `best_ap is None or ...`인 이유:
    DIFF_TH=0 설정에서 diff=0 후보는 check_roam_conditions 게이트(:2297 `diff < th`)를
    정상 통과하는데 score=diff*10=0 이라 `0 > 0`이 거짓이 되어, 게이트가 허용한 후보가
    선택 단계에서 조용히 탈락했다(로그에는 `Roam candidate ... score=0`으로 찍혀 채택된
    것처럼 보임). 그 결과 DIFF_TH=0이 DIFF_TH=1과 동일하게 동작했다.
    출하 기본(DIFF_TH=8)에서는 최소 score 가 80 이라 영향 없음."""
    # 핑퐁 억제의 범위 규칙(리뷰 P1): same-SSID는 대상 BSSID 쌍을 정확히
    # 차단한다. cross-SSID도 현재는 선택한 BSSID를 network id에 임시 pin하지만,
    # 같은 목표 SSID의 다른 BSSID로 우회해 즉시 재시도하지 않도록 기존의 보수적
    # SSID-wide 차단 정책을 유지한다.
    blocked_cross_ssids = set()
    if ENABLE_PING_PONG_PREVENTION and ping_pong_preventer:
        for e in entries:
            e_ssid = e.get("ssid", "")
            if (
                should_cross_connect(e_ssid, live_ssid)
                and ping_pong_preventer.would_block(station["bssid"], e["bssid"])
            ):
                blocked_cross_ssids.add(e_ssid)

    best_ap, best_reason, best_score = None, "", 0
    for roam_ap in entries:
        if roam_ap["bssid"] == station["bssid"]:
            continue
        # cross-SSID cooldown(모드 A): 전환 실패한 extra SSID는 일정 시간 후보에서 제외.
        ap_ssid = roam_ap.get("ssid", "")
        if (
            cooldown is not None
            and should_cross_connect(ap_ssid, live_ssid)
            and cooldown.is_cooling(ap_ssid)
        ):
            continue
        # 핑퐁 억제 중인 대상은 선정 단계에서 제외. 종전엔 선정 후
        # roam_to_bssid/route_cross 진입부에서야 차단돼 "스캔→선정→차단→interval
        # 대기"가 CHECK_INTERVAL 주기로 헛돌았다 — 제외하면 (다른 후보가 없는 한)
        # no-candidate backoff 가 스캔 주기를 자연히 압축하고, 무관한 AP 는
        # 여전히 즉시 선택된다. 실행 직전 검사들은 최종 방어선으로 유지.
        if ENABLE_PING_PONG_PREVENTION and ping_pong_preventer:
            if should_cross_connect(ap_ssid, live_ssid):
                blocked = ap_ssid in blocked_cross_ssids  # SSID 단위(위 주석)
            else:
                blocked = bool(
                    ping_pong_preventer.would_block(
                        station["bssid"], roam_ap["bssid"]
                    )
                )
            if blocked:
                logger.message(
                    "info",
                    f"[{IFACE}] Roam skipped: {roam_ap['bssid']}, "
                    f"ping-pong suppressed",
                    _EXTRA_(),
                )
                continue

        should_roam, reason = check_roam_conditions(
            station, roam_ap, trend, baseline_rssi=baseline_rssi
        )
        if should_roam:
            rssi_diff = roam_ap["rssi"] - baseline_rssi
            score = rssi_diff * 10
            if best_ap is None or score > best_score:
                best_ap = roam_ap
                best_reason = reason
                best_score = score
            logger.message(
                "info",
                f"[{IFACE}] Roam candidate: {roam_ap['bssid']}, "
                f"rssi={roam_ap['rssi']}dB (diff={rssi_diff}dB), "
                f"reason={reason}, score={score:.1f}",
                _EXTRA_(),
            )
        else:
            logger.message(
                "info",
                f"[{IFACE}] Roam skipped: {roam_ap['bssid']}, {reason}",
                _EXTRA_(),
            )
    return best_ap, best_reason, best_score


def filter_ap_lines_by_freq(ap_lines, freq):
    """**`scan_results_to_ap_lines` 산출 라인 전용** — 지정 주파수 항목만 남긴다.

    ⚠️ field[5]=freq 는 `scan_results_to_ap_lines`가 만든 포맷에서만 성립한다. ap.log의
    `mlanutl getscantable` 라인도 같은 7-field pipe 모양이지만 field[5] 의미가 달라
    (그래서 `parse_scan_entries`는 field[5]를 아예 무시한다), 그 경로에 이 함수를 쓰면
    **조용히 빈 리스트를 반환**한다. `get_latest_scan` 경로에는 사용 금지.

    홈채널 패시브 스캔은 그 채널 하나만 실제로 측정하지만, 이어 읽는 `wpa_cli scan_results`는
    **BSS 테이블 전체**(다른 채널 항목 = 과거 스캔의 stale 값 포함)를 반환한다. 필터 없이
    쓰면 Stage 1이 stale 오프채널 BSS를 후보로 골라 freshness 게이트와 액티브 폴백을 통째로
    우회한다. field[5]는 scan_results의 원본 freq라 channel 왕복 변환 손실이 없다."""
    out = []
    try:
        want = int(freq)
    except (TypeError, ValueError):
        return out
    for line in ap_lines or []:
        parts = line.split("|")
        if len(parts) < 7:
            continue
        try:
            if int(parts[5].strip()) == want:
                out.append(line)
        except (TypeError, ValueError):
            continue
    return out


_SCAN_TIME_WRITE_WARNED = False  # 기록 실패 warn 프로세스당 1회(플러드 방지)


def _record_roam_scan_time():
    """bgscan 타이머 리셋 신호(LAST_SCAN_TIME_FILE) 기록.

    bgscan 은 이 시각 이후 interval 전체를 다시 대기하므로, **bgscan 동등 커버리지**
    (scan_freq 전 채널을 실제로 훑은) 스캔에서만 기록해야 한다. 다중채널 direct active와
    단일채널 home scan이 이에 해당한다.
    실패해도 동작은 계속하되(신호 파일 — 다음 동등 스캔에서 재기록) 프로세스당 1회
    warn 을 남긴다 — /run/wifi 쓰기 불가는 중대 시스템 상태라 침묵이 부적절(플러드 방지 1회)."""
    global _SCAN_TIME_WRITE_WARNED
    try:
        # 같은 디렉터리 tmp + os.replace 원자 교체 — bgscan float() 파싱의 torn read 제거.
        # /run/wifi 는 tmpfs 라 부팅마다 사라짐 → 쓰기 시 디렉터리 보장.
        os.makedirs(os.path.dirname(LAST_SCAN_TIME_FILE) or ".", exist_ok=True)
        tmp_path = f"{LAST_SCAN_TIME_FILE}.tmp"
        with open(tmp_path, "w") as f:
            f.write(str(time.time()))
        os.replace(tmp_path, LAST_SCAN_TIME_FILE)
    except Exception as e:
        if not _SCAN_TIME_WRITE_WARNED:
            logger.message(
                "warn",
                f"[{IFACE}] {LAST_SCAN_TIME_FILE} write failed ({e}) — bgscan 타이머 "
                f"리셋 신호 유실(스케줄 영향은 조기 스캔 방향)",
                _EXTRA_(),
            )
            _SCAN_TIME_WRITE_WARNED = True


def staged_scan_best_candidate(station, allowed, live_ssid, trend, cooldown):
    """최신 실측만으로 최적 로밍 후보를 찾는다.

    단일 설정 채널이 현재 홈채널과 같을 때만 저부하 홈채널 스캔을 먼저 사용한다.
    다중채널(또는 설정/현재 채널 불일치)은 홈 패시브와 ap.log 캐시 판정을 건너뛰고
    설정 채널 전체를 directed active scan 한다. bgscan 캐시 RSSI는 최대 interval 만큼
    과거 값이라 현재 링크 RSSI와 비교해 바로 roam 하면 stale 오판이 생길 수 있기 때문이다.

    반환: (best_ap, best_reason, best_score, scanned). scanned=실제 스캔 시도 여부
    (LAST_SCAN_TIME 기록/backoff 판단용)."""
    allowed_set = {s for s in allowed if s}
    cur_bssid = station.get("bssid")
    baseline_rssi = station["rssi"]
    scanned = False

    home_freq = station.get("freq")
    configured_freqs = list(dict.fromkeys(str(f) for f in WPA_FREQ if f is not None))
    single_home_channel = bool(
        home_freq
        and len(configured_freqs) == 1
        and configured_freqs[0] == str(home_freq)
    )

    # ── 다중채널: 최신 directed active scan 1회 ──
    # 현재 AP와 후보 AP를 같은 시점/소스로 측정한다. ap.log 캐시는 읽지도 평가하지도 않는다.
    if not single_home_channel:
        if configured_freqs:
            active_lines = iw_scan_to_ap_lines(
                allowed, configured_freqs, include_wildcard=False
            )
        else:
            logger.message(
                "warn",
                f"[{IFACE}] scan_freq(WPA_FREQ) unset — full-band active scan (once)",
                _EXTRA_(),
            )
            active_lines = iw_scan_to_ap_lines(
                allowed, None, include_wildcard=True
            )
        scanned = True
        if active_lines is SCAN_TRANSITION_BUSY:
            return SCAN_TRANSITION_BUSY, "", 0, False
        if not active_lines:
            return None, "", 0, scanned

        _record_roam_scan_time()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        active_entries = parse_scan_entries(
            active_lines, now_str, allowed_set, src="scan"
        )
        baseline_rssi = baseline_from_entries(
            active_entries, cur_bssid, baseline_rssi
        )
        best_ap, reason, score = evaluate_candidates(
            active_entries, station, trend, cooldown, live_ssid, baseline_rssi
        )
        return best_ap, reason, score, scanned

    # ── 단일채널: 홈채널 스캔 (기본 passive) ──
    # 패시브 스캔이 **현재 AP 외의 우리 허용 SSID 후보를 실제로 봤나**(=홈채널을 로밍
    # 관점에서 커버했나). 현재 결합 AP 의 BSS 테이블 엔트리는 사용 중(in-use)이라
    # age/scan-miss 만료에서 면제된다 — 이번 dwell 에서 beacon 을 하나도 못 받아도
    # scan_results 에 항상 남으므로, 그 엔트리는 '이번 스캔이 뭔가를 수신했다'의 증거가
    # 못 된다. 현재 AP 만으로 '커버됨' 판정하면 가드가 'iw scan 성공 여부'로 퇴화해,
    # 이웃 beacon 유실 구간에서 Stage 3 directed probe(유니캐스트 재시도라 beacon 보다
    # 강건)의 재발견 경로까지 스킵된다 — 로밍컨디션 중에는 bgscan 도 정지라 대체 경로가
    # 없다. 타 SSID beacon 만 받은 경우도 같은 이유로 스킵하지 않는다(리뷰 반영).
    home_scan_ok = False
    home_covers_all = False
    if home_freq:
        # scan_results는 BSS 테이블 전체를 주므로 홈 주파수로 좁힌다(위 helper 주석 참조).
        if HOME_PASSIVE:
            home_scan_lines = iw_scan_to_ap_lines(None, [home_freq], passive=True)
        else:
            # 홈채널 directed 액티브(STAGED_SCAN.home_passive=false) — 홈채널 hidden 로밍
            # 타깃 배포용. Stage 3와 동일하게 wildcard 없이 allowed 만 probe 하고, 이후
            # 파이프라인(freq 필터·baseline 통일·home_scan_ok·스킵 가드)은 패시브와 동일.
            home_scan_lines = iw_scan_to_ap_lines(
                allowed, [home_freq], include_wildcard=False
            )
        if home_scan_lines is SCAN_TRANSITION_BUSY:
            return SCAN_TRANSITION_BUSY, "", 0, False
        home_lines = filter_ap_lines_by_freq(home_scan_lines, home_freq)
        scanned = True
        if home_lines:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            home_entries = parse_scan_entries(
                home_lines, now_str, allowed_set, src="scan"
            )
            home_scan_ok = any(
                e.get("bssid") != cur_bssid for e in home_entries
            )  # 현재 AP 외 후보를 봤을 때만 '커버됨'
            # scan_freq ⊆ {홈채널}이면 이번 홈 스캔이 곧 **전체 커버리지**(bgscan 동등) —
            # 결과·이후 단계와 무관하게 bgscan 타이머 리셋을 기록한다(_record 독스트링 참조).
            # {str(home_freq)} 는 원소 1개짜리 set 리터럴(⊆ 비교), str() 은 타입 정규화
            # (WPA_FREQ 원소는 str, home_freq 는 int).
            home_covers_all = True
            if home_covers_all:
                _record_roam_scan_time()
            baseline_rssi = baseline_from_entries(home_entries, cur_bssid, baseline_rssi)
            best_ap, reason, score = evaluate_candidates(
                home_entries, station, trend, cooldown, live_ssid, baseline_rssi
            )
            if best_ap:
                logger.message(
                    "info",
                    f"[{IFACE}] roam candidate from home-channel "
                    f"{'passive' if HOME_PASSIVE else 'active'} scan",
                    _EXTRA_(),
                )
                return best_ap, reason, score, scanned

    # home_passive=false이면 위 호출 자체가 단일 설정 채널 전체의 directed active scan이다.
    # 결과가 없거나 현재 AP만 보여도 같은 명령을 즉시 한 번 더 실행할 정보 이득이 없다.
    # 실패는 다음 backoff tick에서 다시 시도하고, 이 tick에서는 중복 active fallback을 막는다.
    if not HOME_PASSIVE:
        logger.message(
            "info",
            f"[{IFACE}] home directed active scan already covered configured "
            f"channel({home_freq}) — skip duplicate active fallback",
            _EXTRA_(),
        )
        return None, "", 0, scanned

    # ── 단일채널 passive 이후 active 재확인 생략 ──
    # scan_freq ⊆ {홈채널}이면 Stage 3 액티브는 같은 채널을 probe로 다시 훑는 것뿐이라 후보
    # 발견에 새로 기여하는 게 없다(단일채널 배포 등). 매 로밍컨디션 주기의 불필요한 액티브
    # 스캔(probe 송신)을 없앤다 = airtime·링크 방해 감소. 조건: 최적화 활성 + Stage 1 스캔이
    # **현재 AP 외 후보를 실제로 봄**(스캔 실패·현재 AP 상주 엔트리만·타 SSID 만이면 액티브가
    # 재시도/재발견 역할이라 유지) + scan_freq 가 홈채널의 부분집합.
    # hidden SSID 는 액티브 probe로만 잡히므로 홈채널에 hidden 로밍 타깃이 있으면
    # home_passive=false(홈 directed 액티브)로 커버하거나 이 스킵을 config로 끈다.
    if SKIP_REDUNDANT_ACTIVE_SCAN and home_scan_ok and home_covers_all:
        logger.message(
            "info",
            f"[{IFACE}] scan_freq ⊆ home channel({home_freq}) — home "
            f"passive scan covered all, "
            f"skip redundant active fallback (no roam candidate)",
            _EXTRA_(),
        )
        return None, "", 0, scanned

    # passive scan이 현재 AP 외 후보 비콘을 하나도 못 받았거나 스킵 옵션이 꺼졌으면
    # 동일 채널 directed active scan으로 한 번 재확인한다(hidden/beacon loss 보완).
    active_lines = iw_scan_to_ap_lines(
        allowed, configured_freqs, include_wildcard=False
    )
    scanned = True
    if active_lines is SCAN_TRANSITION_BUSY:
        return SCAN_TRANSITION_BUSY, "", 0, False
    if active_lines:
        # scan_freq 전 채널(미설정 시 전대역) 실측 **성공** = bgscan 동등 커버리지.
        # 실패(None) 시엔 기록하지 않는다 — 신선 데이터가 없으니 bgscan 조기 재개가 이득.
        _record_roam_scan_time()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        active_entries = parse_scan_entries(
            active_lines, now_str, allowed_set, src="scan"
        )
        baseline_rssi = baseline_from_entries(active_entries, cur_bssid, baseline_rssi)
        best_ap, reason, score = evaluate_candidates(
            active_entries, station, trend, cooldown, live_ssid, baseline_rssi
        )
        return best_ap, reason, score, scanned

    return None, "", 0, scanned


# ==============================================================================
# 개선된 main 함수
# ==============================================================================
def main():
    global trend_tracker, ping_pong_preventer, cross_ssid_cooldown

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


    # 후보없음 점증 backoff 상태(spec §4). streak=연속 후보없음 tick 수,
    # hint_state=bgscan hint mtime 추적.
    no_candidate_streak = 0
    hint_state = {"hint_mtime": None}
    # good-signal 게이트 상태(new_gate_state / track_association 참조).
    gs = new_gate_state()

    while True:
        # SIGHUP 수신 시에만 wifi_init_conf.json 재읽어 반영(폴링 없음 → 프로덕션 비용 0).
        # 대기(interruptible_sleep)가 신호로 즉시 깨어나므로 긴 backoff 중에도 즉시 반영.
        # 먼저 플래그를 내린 뒤 reload — 처리 중 새 신호는 다음 사이클에 latest 재읽어 커버.
        if _RELOAD_STATE["pending"]:
            _RELOAD_STATE["pending"] = False
            reload_roaming_config(IFACE)
        # 이전 Mode A 선택의 BSSID pin/all-network cleanup이 미해결이면
        # 새 scan/로밍 판정보다 먼저 복구한다. 실패 동안은 새 pin 금지.
        if not retry_pending_selection_cleanup(IFACE):
            interruptible_sleep(CHECK_INTERVAL)
            continue
        # wpa_cli reconfigure 등으로 conf 가 런타임 변경됐으면 재파싱(mtime 변화 시에만).
        # ssid/scan_freq/TH 캐시를 최신화해 옛 SSID 로 스캔하는 stale 로밍을 방지한다.
        reload_supplicant_conf_if_changed(WPA_CONF_FILE)
        # bgscan이 새 후보 AP를 발견(hint touch)하면 즉시 backoff 해제(고속 복귀).
        if roam_hint_touched(hint_state):
            no_candidate_streak = 0
            on_streak_reset(gs)

        # Load 정보 포함하여 연결 상태 확인
        station = get_link_info()

        # 결합 추적은 **station 유효성 판정 전에** 한다 — station=None(끊김·link.json stale)
        # 에서 bssid 를 비워야 같은 AP 로의 재결합도 새 결합으로 감지된다(track_association).
        track_association(station, gs)

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
            # 단 게이트가 켜져 있으면 **위치가 실제로 변했을 때만** 리셋한다 — 임계 바로
            # 위에서의 Δ0dB 진동이 backoff 를 3초로 되돌려 스캔을 폭증시키던 경로다
            # (good_signal_reset_allowed 참조).
            allowed, why = good_signal_reset_allowed(
                station["rssi"], gs["reset_rssi"], gs["assoc_ts"]
            )
            if allowed:
                if gs["suppressed"]:
                    logger.message(
                        "info",
                        f"[{IFACE}] good-signal streak reset ({why}) — "
                        f"이전 {gs['suppressed']}회 억제, streak={no_candidate_streak} 유지했었음",
                        _EXTRA_(),
                    )
                # 리셋 동반 정리는 on_streak_reset 에 위임 — suppressed 초기화가 이 경로와
                # hint·후보발견 경로에 나뉘어 있으면 향후 필드가 추가될 때 한쪽이 빠진다.
                on_streak_reset(gs)
                no_candidate_streak = 0
                # 허용 경로 전용: 다음 판정의 비교 기준을 현재 RSSI 로 갱신(on_streak_reset 이
                # None 으로 비운 것을 여기서 채운다 — 순서 의존이므로 위임 뒤에 와야 한다).
                gs["reset_rssi"] = station["rssi"]
            elif no_candidate_streak:
                # 억제 — 매 tick(2초) 로그는 볼륨 문제라 카운터만 누적하고 위에서 요약한다.
                # streak 가 이미 0 이면 억제할 대상이 없다(리셋해도 결과가 같다). 그때도
                # 카운트하면 요약이 "43회 억제, streak=0 유지했었음" 처럼 실익 없는 숫자를
                # 보고한다 — 실기 로그에서 실제로 관측됐다.
                gs["suppressed"] += 1

            interval = CHECK_INTERVAL

            interruptible_sleep(interval)
            continue

        # 로밍 조건 발생
        logger.message(
            "info",
            f"[{IFACE}] roaming condition: {station['rssi']} < {predictive_threshold} "
            f"(base={base_threshold}, trend={trend_str}) "
            f"bssid={station['bssid']}",
            _EXTRA_(),
        )
        set_flag(1, ROAM_CONDITION_FLAG)

        # 인터벌 계산 (루프 내 모든 경로에서 공유)
        interval = CHECK_INTERVAL

        # ── 단계형 스캔으로 로밍 후보 결정 ──
        # station["ssid"]는 get_link_info가 link.json info.ssid(실제 연결 SSID)로 채움.
        if not station.get("ssid"):
            station["ssid"] = WPA_SSID
        allowed = get_allowed_ssids(station.get("ssid"))
        # cross-SSID 판정 기준 = 라이브 연결 SSID 단일(T5: base에 WPA_SSID를 넣으면
        # conf 기본 SSID 복귀가 same으로 오판되어 FAIL 루프). 평가/로밍 분기에서 공유.
        live_ssid = station.get("ssid")

        if ENABLE_STAGED_SCAN and WPA_SSID:
            # 단일채널은 home passive 우선, 다중채널은 전 채널 directed active 1회.
            # ap.log cache RSSI는 stale 가능성이 있어 최종 후보 판정에 사용하지 않는다.
            # 후보 로그는 syslog(logger.message)로 남는다.
            # LAST_SCAN_TIME(bgscan 타이머 리셋)은 staged 함수가 **bgscan 동등 커버리지**
            # 스캔에서만 직접 기록한다(홈채널 부분 스캔의 무조건 기록이 bgscan 을 계속
            # 불필요하게 밀어내지 않도록 한다 — _record_roam_scan_time 참조).
            best_ap, best_reason, best_score, scanned = staged_scan_best_candidate(
                station, allowed, live_ssid, trend, cross_ssid_cooldown
            )
            if best_ap is SCAN_TRANSITION_BUSY:
                interruptible_sleep(CHECK_INTERVAL)
                continue
        else:
            # 종전 단일 액티브 스캔 경로(ENABLE_STAGED_SCAN=False 또는 WPA_SSID 부재 시 무회귀).
            if WPA_SSID:
                ap_lines = iw_scan_to_ap_lines(allowed, WPA_FREQ)
                if ap_lines is SCAN_TRANSITION_BUSY:
                    interruptible_sleep(CHECK_INTERVAL)
                    continue
                # 레거시 = scan_freq 전 채널 액티브(bgscan 동등) — 종전대로 시도 시 기록.
                _record_roam_scan_time()
                if ap_lines:
                    save_with_timestamp(SCAN_LOG_FILE, ap_lines)
                else:
                    backoff, no_candidate_streak = advance_no_candidate_backoff(
                        no_candidate_streak
                    )
                    logger.message(
                        "err",
                        f"[{IFACE}] scan failed (no-candidate backoff={backoff}s, "
                        f"streak={no_candidate_streak})",
                        _EXTRA_(),
                    )
                    interruptible_sleep(backoff)
                    continue

            # WPA_SSID 가 있으면 위에서 이번 tick 에 직접 스캔해 ap.log 에 방금 쓴 블록을
            # 되읽는 것이므로 전경 실측("scan")이다. WPA_SSID 부재로 스캔을 건너뛴 경우에만
            # 진짜 배경 캐시("cache")를 읽는다.
            entries, _ts = get_latest_scan(
                station, allowed, src="scan" if WPA_SSID else "cache"
            )
            if not entries:
                backoff, no_candidate_streak = advance_no_candidate_backoff(
                    no_candidate_streak
                )
                logger.message(
                    "err",
                    f"[{IFACE}] No Matching APs found in latest scan "
                    f"(no-candidate backoff={backoff}s, streak={no_candidate_streak})",
                    _EXTRA_(),
                )
                interruptible_sleep(backoff)
                continue

            best_ap, best_reason, best_score = evaluate_candidates(
                entries, station, trend, cross_ssid_cooldown, live_ssid, station["rssi"]
            )

        # 최적 AP로 로밍
        if best_ap:
            # Lock contention is scheduler-neutral.  Do not reset candidate/gate
            # state or claim an attempted roam until the transition helper has
            # actually acquired the shared live-operation lock.
            prev_streak = no_candidate_streak
            # 라우팅 판단은 위에서 선계산한 live_ssid(라이브 연결 SSID)를 공유.
            # should_cross_connect 게이트(모드 A AND live와 다른 SSID)면 cross(select_network),
            # 아니면 무중단 roam. 모드 B(generate=false)는 cross 항상 차단(spec §3.5 2차 게이트).
            is_cross_ssid = should_cross_connect(best_ap.get("ssid"), live_ssid)
            if is_cross_ssid:
                # 다른(extra) SSID → 모드 A select_network(conf 불변). 성공/실패를 cooldown에 등록:
                # 실패 누적 시 그 SSID를 후보에서 제외해 deauth 진동을 차단(spec §3.2).
                ok = route_cross_ssid_transition(
                    IFACE, best_ap["ssid"], station["bssid"], best_ap["bssid"]
                )
                if ok is SCAN_TRANSITION_BUSY:
                    interruptible_sleep(CHECK_INTERVAL)
                    continue
            else:
                ok = roam_to_bssid(
                    station["bssid"], best_ap["bssid"],
                    channel=best_ap.get("channel"),
                    freq=best_ap.get("freq"),
                    rssi=best_ap.get("rssi"),
                )
                if ok is SCAN_TRANSITION_BUSY:
                    interruptible_sleep(CHECK_INTERVAL)
                    continue

            # A non-busy result means the transition lock was held and a real
            # backend decision occurred.  Only now reset candidate/gate state
            # and emit the attempt log.  On ordinary same-SSID failure the
            # backoff below still advances from prev_streak.
            no_candidate_streak = 0
            on_streak_reset(gs)
            gs["reset_rssi"] = station["rssi"]
            logger.message(
                "emerg",
                f"[{IFACE}] Roaming: {station['bssid']} → {best_ap['bssid']}, "
                f"reason={best_reason}, score={best_score:.1f}, "
                f"{best_ap['ssid']}, {best_ap['rssi']}dB (ch={best_ap['freq']})",
                _EXTRA_(),
            )

            if is_cross_ssid:
                # 전환 결과를 cooldown에 반영(성공→clear / 실패→register). post_sleep=실패 후
                # 메인루프가 대기하는 시간(ROAM_SUCCESS_SLEEP+interval)을 반영해, 그 sleep 동안
                # cooldown이 만료돼 무효화되는 것을 방지. cooldown None(모드 B)이면 무동작.
                record_cross_ssid_result(
                    cross_ssid_cooldown, best_ap["ssid"], ok, ROAM_SUCCESS_SLEEP + interval
                )
                # 로밍 성공 정착 대기(의도적 비-중단): 이 짧은 settle 중 SIGHUP이 와도
                # 직후 interruptible_sleep(interval) 또는 다음 루프 top에서 반영된다.
                time.sleep(ROAM_SUCCESS_SLEEP)
            elif ok:
                # 로밍 성공 정착 대기(의도적 비-중단): 이 짧은 settle 중 SIGHUP이 와도
                # 직후 interruptible_sleep(interval) 또는 다음 루프 top에서 반영된다.
                time.sleep(ROAM_SUCCESS_SLEEP)
            else:
                # same-SSID 시도 실패(거부·미확정·ping-pong 차단) → 후보없음 tick 과
                # 동일하게 종전 streak 에서 backoff 전진. CHECK_INTERVAL 고정 재시도가
                # 실패 모드별 고정 주기로 무한 반복되는 것을 억제한다(성공·hint·
                # good-signal 리셋 경로는 기존과 동일).
                backoff, no_candidate_streak = advance_no_candidate_backoff(prev_streak)
                logger.message(
                    "err",
                    f"[{IFACE}] Roam attempt failed: {station['bssid']} → "
                    f"{best_ap['bssid']} (roam-fail backoff={backoff}s, "
                    f"streak={no_candidate_streak})",
                    _EXTRA_(),
                )
                interruptible_sleep(backoff)
                continue
            interruptible_sleep(interval)
            continue

        # 적합한 후보 없음 → 점증 backoff(연결 중 후보없음 airtime 잠식 억제).
        backoff, no_candidate_streak = advance_no_candidate_backoff(no_candidate_streak)
        # 게이트가 억제 중이면 그 횟수를 병기한다. 억제만 이어지는 동안에는 good-signal
        # 분기가 아무 로그도 남기지 않아(요약은 '억제→리셋' 전이에서만 찍힌다) 운용 중
        # 상태를 알 수 없었다 — 이미 매 tick 찍히는 이 줄에 얹어 볼륨 증가 없이 노출한다.
        gate_note = f", gate_suppressed={gs['suppressed']}" if gs["suppressed"] else ""
        logger.message(
            "info",
            f"[{IFACE}] No suitable roam candidate found "
            f"(no-candidate backoff={backoff}s, streak={no_candidate_streak}{gate_note})",
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
    header = timestamp_str
    with open(filename, "a") as f:
        f.write(header + "\n")
        for line in content_lines:
            f.write(line.rstrip() + "\n")
        f.write("\n")

    return filename


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
    # roam 상태 파일도 동일 이유로 iface별 재대입 — DBDC 시 두 roam 데몬의 플래그
    # 교차 기록(last-writer-wins)과 bgscan 교차 정지를 차단한다.
    ROAM_CONDITION_FLAG, LAST_SCAN_TIME_FILE = roam_state_paths(IFACE)
    if clear_stale_roam_lease():
        logger.message("warn", f"[{IFACE}] removed stale roam-condition lease on startup", _EXTRA_())

    # Selection WAL은 disposable /run/wifi boot snapshot보다 오래 살아남도록 /run 바로
    # 아래에 둔다. 따라서 snapshot 손상/삭제로 owner를 fail-closed 하기 *전에* cleanup만
    # 수행해야 SIGKILL 뒤의 BSSID pin/disabled-network 상태를 영구히 고립시키지 않는다.
    # WAL이 없으면 단순 open→ENOENT이며, 이 preflight는 scan/roam 결정을 절대 수행하지 않는다.
    if not retry_pending_selection_cleanup(IFACE):
        logger.message(
            "emerg",
            f"[{IFACE}] startup selection cleanup unresolved; refusing owner startup",
            _EXTRA_(),
        )
        sys.exit(4)

    # owner/topology는 /run boot snapshot이 단일 SoT다. daemon crash/restart 뒤에도
    # persisted JSON 변경을 재해석하지 않으며, snapshot 부재/불일치는 fail-closed한다.
    try:
        BOOT_POLICY = load_boot_roam_policy(IFACE)
    except RoamPolicyError as e:
        logger.message("emerg", f"[{IFACE}] boot roam policy invalid: {e}", _EXTRA_())
        sys.exit(2)
    if not BOOT_POLICY["roaming_enabled"]:
        logger.message(
            "notice",
            f"[{IFACE}] boot policy selects wpa native owner; refusing stale wifi_roam start",
            _EXTRA_(),
        )
        sys.exit(3)

    # 런타임 튜닝 값은 JSON에서 로드하되 topology/extra는 즉시 boot snapshot으로 복원.
    load_roaming_config(IFACE)
    apply_boot_roam_policy(BOOT_POLICY)
    logger.message(
        "notice",
        f"[{IFACE}] boot roam policy: owner=wifi_roam "
        f"topology={'A' if GENERATE_NETWORK_BLOCKS else 'B'} "
        f"extra_ssids={EXTRA_SSIDS}",
        _EXTRA_(),
    )

    # 로밍 임계(th2g/th5g)는 JSON 단일 소스 — conf `#!TH_*` 마커는 더 이상 읽지 않는다.
    # parse_supplicant_conf 는 여기서 넘긴 DEFAULT_TH_*(= load_roaming_config 가 JSON 으로
    # 갱신한 값)를 그대로 돌려주고, conf 에서는 ssid/freq_list/`#!TH_CONNECT=` 만 읽는다.
    WPA_SSID, WPA_FREQ, WPA_TH_2G, WPA_TH_5G, WPA_TH_CONNECT = parse_supplicant_conf(
        WPA_CONF_FILE, def_th2g=DEFAULT_TH_2G, def_th5g=DEFAULT_TH_5G
    )
    # 초기 파싱 시점의 mtime 기록 — 이후 main 루프는 mtime 변화(reconfigure) 시에만 재파싱.
    try:
        WPA_CONF_MTIME = os.path.getmtime(WPA_CONF_FILE)
    except OSError:
        WPA_CONF_MTIME = None

    # 최종 적용값 로깅. 임계 소스는 JSON 단일이므로 source 필드를 없앴다 —
    # conf `#!TH_*` 마커 경로를 제거한 뒤로는 항상 "json" 이라 판별 의미가 없다.
    logger.message(
        "info",
        f"[{IFACE}] TH values: 2G={WPA_TH_2G}, 5G={WPA_TH_5G} (source: json)",
        _EXTRA_(),
    )

    logger.message(
        "info",
        f"[{IFACE}] version:{VERSION}, ssid:{WPA_SSID}, freq_list:{WPA_FREQ}, "
        f"TH_2G:{WPA_TH_2G}, TH_5G:{WPA_TH_5G}, "
        f"predictive_roam:{ENABLE_PREDICTIVE_ROAM}, "
        f"ping_pong_prevention:{ENABLE_PING_PONG_PREVENTION}",
        _EXTRA_(),
    )

    main()
