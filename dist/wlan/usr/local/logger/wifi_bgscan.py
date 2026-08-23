
import subprocess
import re
import os
import time
import random
import logging
import sys
import json
import signal
import threading
from datetime import datetime
from sUTILS import Logger, _EXTRA_
from roam_state import lease_active, process_start_time, roam_state_paths, scan_transition_lock
from roam_policy import (
    RoamPolicyError,
    load_boot_roam_policy,
    parse_wpa_ssid_value,
    scan_backend_for_policy,
    validate_ssid,
    validate_ssid_list,
)

LOG_DIR = "/var/log/cantops/scan"

# 모듈 로드 시 mlan0 기본 경로로 평가 — __main__ 이 실제 IFACE 로 재대입한다
# (iface 미구분 전역 파일이면 DBDC 시 mlan0 roam 조건이 mlan1 bgscan 을 정지시킴).
ROAM_CONDITION_FLAG, LAST_SCAN_TIME_FILE = roam_state_paths("mlan0")
WIFI_INIT_CONF_JSON = "/usr/local/etc/wifi_init_conf.json"
ROAM_HINT_DIR = "/tmp"  # roam backoff hint 파일 디렉터리 (wifi_roam.roam_hint_touched 가 소비)
WPA_CONF_FILE = f"/etc/wpa_supplicant/wpa_supplicant-mlan0.conf"
# 이중 폴백(JSON 키 부재 + wpa conf `#!INTERVAL` 마커 부재) 시의 스캔 주기 — 템플릿
# bgscan.interval(60)과 fail-same 정렬(작으면 폴백 상태에서 30s 폭주 + cache_fresh 전제 붕괴).
DEFAULT_INTERVAL = 60
MAX_SCAN_SSIDS = 10  # nl80211 max # scan SSIDs (NXP mlan 실측). 초과 시 iw가 -EINVAL로 스캔 전체 실패.
#last_log_time = 0
VERSION = "0.0"
IFACE = ""
_WPA_CLI_WARNED = False   # wpa_cli 부재 로그 1회 제한 플래그
_WILDCARD_PROBE_WARNED = False   # ssid_filter=false+extra_ssids 와일드카드 probe 가정 경고 1회 제한
_FREQ_FILTER_DEPRECATED_WARNED = False  # common freq 정책과 충돌하는 false 경고 1회
_IW_PASSIVE_FORCED_ACTIVE_WARNED = False  # iw periodic passive safety override 경고 1회


class BgscanConfigError(RuntimeError):
    """스캔 backend 소유권을 안전하게 결정할 수 없는 시작 설정."""

def handle_sigterm(signum, frame):
    logger.message('crit', f"[{IFACE}] SIGTERM {signum} received! Cleaning up...", _EXTRA_())
    cleanup()
    sys.exit(0)

def cleanup():
    pass

# bgscan 은 roam_condition PID lease의 reader 전용이다. 죽은 writer/PID 재사용/구버전
# 정수 플래그는 reader가 자동 폐기해 같은 boot에서 bgscan이 영구 억제되지 않게 한다.
def get_flag(path=None) -> bool:
    # 기본 인자는 def 시점 바인딩이라 __main__ 의 iface별 재대입이 반영되지 않는다
    # → None 센티널로 호출 시점 전역을 읽는다(mlan1 인스턴스의 mlan0 플래그 오독 방지).
    if path is None:
        path = ROAM_CONDITION_FLAG
    return lease_active(path)

def is_wpa_running(interface="mlan0"):
    result = subprocess.run(
        ["systemctl", "is-active", f"wpa_supplicant@{interface}"],
        capture_output=True, text=True
    )
    return result.stdout.strip() == "active"

def is_wpa_connected(interface="mlan0"):
    """wpa_state==COMPLETED(연결 완료)인지. 미연결(스캔/인증/assoc 중)이면 False.
    wpa_cli 부재/오류는 로그로 가시화 — silent False면 bgscan이 영영 skip돼도 진단 불가."""
    try:
        result = subprocess.run(
            ["wpa_cli", "-i", interface, "status"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            logger.message("err", f"[{interface}] wpa_cli status exited {result.returncode} — treating as disconnected", _EXTRA_())
            return False
        for line in result.stdout.splitlines():
            if line.startswith("wpa_state="):
                return line.split("=", 1)[1].strip() == "COMPLETED"
        # exit 0인데 wpa_state= 라인 부재(소켓 오류 등) — silent False 방지 위해 로그
        logger.message("err", f"[{interface}] wpa_state not found in wpa_cli output — treating as disconnected", _EXTRA_())
    except FileNotFoundError:
        # wpa_cli 부재는 영구 상태 → 매 호출 로그 flood 방지 위해 1회만 남긴다.
        global _WPA_CLI_WARNED
        if not _WPA_CLI_WARNED:
            logger.message("err", f"[{interface}] wpa_cli not found — bgscan cannot verify connection (skipping scans)", _EXTRA_())
            _WPA_CLI_WARNED = True
    except Exception as e:
        logger.message("err", f"[{interface}] wpa_cli status error: {e}", _EXTRA_())
    return False

def parse_wpa_supplicant_conf(path):
    base_ssid = None
    network_ssids = []
    global_freqs = []
    base_freqs = []
    legacy_scan_freqs = []
    interval = DEFAULT_INTERVAL  # `#!INTERVAL=` 마커 부재 시 폴백(템플릿과 fail-same)
    in_network = False
    network_index = 0

    with open(path, "r") as f:
        for raw_line in f:
            line = raw_line.strip()
            if line.startswith("#!INTERVAL="):
                try:
                    interval = int(line.split("=")[1])
                except ValueError:
                    logger.message('err', f"[{IFACE}] INTERVAL : {interval} is invalid in {path}", _EXTRA_())
                continue
            if not line or line.startswith("#"):
                continue
            if re.match(r"^network\s*=\s*\{", line):
                in_network = True
                network_index += 1
                continue
            if in_network and line.startswith("}"):
                in_network = False
                continue

            if in_network and line.startswith("ssid="):
                try:
                    ssid = parse_wpa_ssid_value(line.split("=", 1)[1])
                except RoamPolicyError as exc:
                    raise ValueError(f"invalid SSID in {path}: {exc}") from exc
                if base_ssid is None:
                    base_ssid = ssid
                if ssid in network_ssids:
                    raise ValueError(f"duplicate SSID identity in {path}: {ssid!r}")
                network_ssids.append(ssid)
            elif not in_network and line.startswith("freq_list="):
                if not global_freqs:
                    value = line.split("=", 1)[1].split("#", 1)[0]
                    global_freqs = value.strip().split()
            elif in_network and network_index == 1 and line.startswith("freq_list="):
                if not base_freqs:
                    value = line.split("=", 1)[1].split("#", 1)[0]
                    base_freqs = value.strip().split()
            elif in_network and network_index == 1 and line.startswith("scan_freq="):
                # Boot-only compatibility fallback; canonical configs use freq_list.
                if not legacy_scan_freqs:
                    value = line.split("=", 1)[1].split("#", 1)[0]
                    legacy_scan_freqs = value.strip().split()

    freqs = global_freqs or base_freqs or legacy_scan_freqs
    return base_ssid, network_ssids, freqs, interval

def _parse_bool(value):
    """bool 해석을 roam parse_bool / lib normalize_bool과 통일(true/1/yes/on/enabled → True).
    JSON 정규 bool뿐 아니라 비정규 truthy 값도 동일 해석해 generate_network_blocks의
    3-way(roam/lib/bgscan) 모드 정합을 보장한다."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes", "on", "enabled")
    return bool(value)


def load_scan_policy(iface, run_dir=None):
    """이 boot에서 불변인 owner/topology snapshot을 fail-closed로 읽는다."""
    try:
        return load_boot_roam_policy(iface, run_dir=run_dir)
    except RoamPolicyError as e:
        raise BgscanConfigError(str(e)) from e


def load_scan_backend(iface, run_dir=None):
    """Boot snapshot에서 고정 scan requester를 결정한다."""
    try:
        return scan_backend_for_policy(load_scan_policy(iface, run_dir=run_dir))
    except RoamPolicyError as e:
        raise BgscanConfigError(str(e)) from e


def load_bgscan_json(iface, boot_policy=None):
    """`.iface.bgscan`에서 interval/ssid_filter/freq_filter/emit_roam_hint를,
    `.iface.roaming.extra_ssids`에서 추가 스캔 SSID를 한 번의 파일 읽기로 로드.
    interval은 양의 정수만, 필터/emit_roam_hint는 bool만, extra_ssids는 문자열 리스트만
    수용. 없음/형식오류면 (None, True, True, [], True).
    roaming.generate_network_blocks가 비-truthy(모드 B/부재)면 extra_ssids=[] 강제
    (spec §3.5 3차 게이트, 모드 B airtime 회귀 제거). bool 해석은 roam/lib와 통일(_parse_bool)."""
    interval, ssid_filter, freq_filter, extra_ssids = None, True, True, []
    emit_roam_hint = True
    # Native wpa_cli compatibility default. The iw constructor safety-overrides it to active.
    passive = True
    try:
        with open(WIFI_INIT_CONF_JSON, "r") as f:
            data = json.load(f)
        iface_cfg = data.get(iface, {})
        bg = iface_cfg.get("bgscan", {})
        iv = bg.get("interval")
        if isinstance(iv, int) and iv > 0:
            interval = iv
        if isinstance(bg.get("ssid_filter"), bool):
            ssid_filter = bg["ssid_filter"]
        if isinstance(bg.get("freq_filter"), bool):
            freq_filter = bg["freq_filter"]
        if isinstance(bg.get("emit_roam_hint"), bool):
            emit_roam_hint = bg["emit_roam_hint"]
        if isinstance(bg.get("passive"), bool):
            passive = bg["passive"]
        # owner/topology는 production main이 전달한 /run boot snapshot에서만 읽는다.
        # boot_policy=None은 단위테스트/구버전 직접호출 호환 경로일 뿐 daemon 경로가 아니다.
        roaming_cfg = iface_cfg.get("roaming", {})
        topology_cfg = boot_policy if boot_policy is not None else roaming_cfg
        if _parse_bool(topology_cfg.get("generate_network_blocks")):
            extra = topology_cfg.get("extra_ssids")
            extra_ssids = validate_ssid_list(extra)
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.message("err", f"[{iface}] bgscan json load error: {e}", _EXTRA_())
    return interval, ssid_filter, freq_filter, extra_ssids, emit_roam_hint, passive

def emit_roam_hint_touch(iface):
    """roam backoff 해제 신호: /tmp/wifi_roam_hint_<iface> 를 touch(mtime 갱신).

    단방향(bgscan write / roam read)이라 race-free. 실패는 조용히 무시(다음 스캔 재시도).
    호출부는 스캔 성공 직후 emit_roam_hint=True 일 때만 호출한다."""
    path = os.path.join(ROAM_HINT_DIR, f"wifi_roam_hint_{iface}")
    try:
        with open(path, "a"):
            pass
        os.utime(path, None)
    except OSError as e:
        logger.message("err", f"[{iface}] roam hint touch failed: {e}", _EXTRA_())

def construct_iw_scan_cmd(ssid, configured_freqs, ssid_filter=True, freq_filter=True, extra_ssids=None, passive=False):
    global _IW_PASSIVE_FORCED_ACTIVE_WARNED

    cmd = ["iw", IFACE, "scan"]

    # NXP moal 437.p3에서 반복 다채널 passive iw scan은 supplicant가 COMPLETED여도
    # data plane을 strand할 수 있다. JSON default는 native wpa_cli 호환 때문에 유지하되,
    # iw periodic backend는 언제나 기존 directed active grammar로 안전하게 구성한다.
    if passive:
        if not _IW_PASSIVE_FORCED_ACTIVE_WARNED:
            logger.message(
                "warn",
                f"[{IFACE}] bgscan.passive=true requested for iw; forcing active scanning "
                "because repeated multi-channel passive scans can strand the data plane "
                "on supported mlan hardware",
                _EXTRA_(),
            )
            _IW_PASSIVE_FORCED_ACTIVE_WARNED = True

    # freq_filter=false면 freq 필터를 빼고 전체 대역 스캔(기본 true).
    if freq_filter and configured_freqs:
        cmd += ["freq"] + configured_freqs

    # directed probe(ssid 토큰) 대상 수집:
    #  - 기본/현재 ssid: ssid_filter=true일 때만 probe. false면 광범위(undirected) 스캔.
    #  - roaming.extra_ssids: 명시적 로밍 후보이므로 ssid_filter와 무관하게 항상 probe.
    #    hidden extra SSID는 directed probe로만 발견되므로 ssid_filter=false에서도 누락되면 안 된다.
    # iw는 다중 ssid 토큰을 지원. 중복 제거하여 추가.
    #  - ssid_filter=false인데 extra_ssids가 있으면 directed probe만 남아 와일드카드
    #    probe가 사라진다(NXP mlan 포함 대부분의 드라이버는 ssid 지정 시 와일드카드를
    #    보내지 않음) → 광범위 스캔 의도가 깨져 extra 외 일반 AP가 누락될 수 있다.
    #    빈 문자열 ""(와일드카드 probe)을 함께 넣어 광범위 스캔을 보존한다.
    #    NOTE: `iw scan ssid ""`의 와일드카드 해석은 표준 API가 아니다. 새 플랫폼/드라이버
    #    도입 시 extra_ssids 사용 전 실제로 와일드카드 probe가 나가는지(빈 SSID 무시 여부) 검증할 것.
    # iw 문법은 `ssid <ssid>*` — 키워드는 1회만 쓰고 값을 나열한다(wifi_roam 과 동일 규칙).
    # 키워드를 반복하면 iw 5.19 파서(SSID 상태에서 키워드 복귀 없음)가 두 번째 'ssid' 를
    # 리터럴 SSID 로 소비해 probe 대상이 2N-1 개로 불어나고, 존재하지 않는 "ssid" 네트워크
    # directed probe 가 전파로 나간다.
    validated_extras = validate_ssid_list(
        list(extra_ssids or []), base_ssid=ssid if ssid is not None else None
    )
    probe = ([validate_ssid(ssid)] if (ssid_filter and ssid) else []) + validated_extras
    if not ssid_filter and validated_extras:
        probe.insert(0, "")
    probe = [s for s in probe if s is not None]
    # 드라이버 max-scan-SSID 초과 시 iw 가 -EINVAL 로 스캔 전체를 실패시킨다 → bgscan 이
    # 매 주기 전량 실패하면 ap.log 배경 캐시가 갱신되지 않아 로밍 Stage 2 까지 연쇄로 죽는다.
    # 현재 ssid/wildcard 가 리스트 앞이라 slice 가 우선순위를 보존한다.
    if len(probe) > MAX_SCAN_SSIDS:
        logger.message(
            "warn",
            f"[{IFACE}] scan SSIDs {len(probe)} > driver max {MAX_SCAN_SSIDS}; "
            f"capping directed probes (excess hidden SSIDs may be missed)",
            _EXTRA_(),
        )
        probe = probe[:MAX_SCAN_SSIDS]
    if probe:
        cmd += ["ssid"] + probe

    return cmd


def construct_wpa_scan_cmd(
    iface,
    ssid,
    configured_freqs,
    ssid_filter=True,
    extra_ssids=None,
    passive=False,
):
    """wpa_supplicant가 결과를 native selection에 쓰는 SCAN 요청을 만든다.

    `TYPE=ONLY`는 scan_only_handler를 설치해 selection을 막으므로 사용하지 않는다.
    전역 목록이 있으면 wpa의 조건부 fallback에 기대지 않고 명시적 comma-list로 넘긴다.
    ctrl_iface의 `ssid` 값은 raw 문자열이 아니라 UTF-8 hex 형식이다.
    """
    cmd = ["wpa_cli", "-i", iface, "scan"]
    if configured_freqs:
        cmd.append(f"freq={','.join(configured_freqs)}")
    if passive:
        cmd.append("passive=1")
        return cmd

    validated_extras = validate_ssid_list(
        list(extra_ssids or []), base_ssid=ssid if ssid is not None else None
    )
    probe = ([validate_ssid(ssid)] if (ssid_filter and ssid) else []) + validated_extras
    if len(probe) > MAX_SCAN_SSIDS:
        logger.message(
            "warn",
            f"[{iface}] scan SSIDs {len(probe)} > driver max {MAX_SCAN_SSIDS}; "
            f"capping directed probes (excess hidden SSIDs may be missed)",
            _EXTRA_(),
        )
        probe = probe[:MAX_SCAN_SSIDS]
    for name in probe:
        cmd.extend(["ssid", name.encode("utf-8").hex()])
    return cmd


def run_scan_command(cmd, backend):
    """고정 backend의 scan 요청을 한 번 실행하고 실제 수락 여부를 반환한다."""
    if backend not in ("iw", "wpa_cli"):
        raise ValueError(f"unsupported scan backend: {backend}")
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        logger.message(
            "err", f"[{IFACE}] {backend} scan timed out (30s)", _EXTRA_()
        )
        return False
    except OSError as e:
        logger.message(
            "err", f"[{IFACE}] {backend} scan execution failed: {e}", _EXTRA_()
        )
        return False

    if result.returncode != 0:
        detail = (result.stderr or "").strip() or (result.stdout or "").strip()
        logger.message(
            "err",
            f"[{IFACE}] {backend} scan exited {result.returncode}: {detail}",
            _EXTRA_(),
        )
        return False
    if backend == "wpa_cli":
        reply = next(
            (line.strip() for line in (result.stdout or "").splitlines() if line.strip()),
            "",
        )
        if reply != "OK":
            logger.message(
                "err",
                f"[{IFACE}] wpa_cli scan rejected: {reply or 'empty reply'}",
                _EXTRA_(),
            )
            return False
    return True


def build_scan_request(conf_path, backend, boot_policy=None):
    """reload 가능한 scan 파라미터로 고정 backend의 다음 요청을 구성한다."""
    global _WILDCARD_PROBE_WARNED, _FREQ_FILTER_DEPRECATED_WARNED

    ssid, network_ssids, freqs, wpa_interval = parse_wpa_supplicant_conf(conf_path)
    (
        json_interval,
        ssid_filter,
        freq_filter,
        extra_ssids,
        emit_roam_hint,
        passive,
    ) = load_bgscan_json(IFACE, boot_policy=boot_policy)
    interval = json_interval or wpa_interval or DEFAULT_INTERVAL

    # 전역 freq_list가 있으면 두 backend 모두 같은 목록을 반드시 사용한다. wpa는
    # 명시 freq를 빼도 전역 fallback이 적용돼 `freq_filter=false=전대역`을 구현할 수
    # 없으므로, backend별 의미가 갈라지는 legacy false를 더 이상 적용하지 않는다.
    if freqs and not freq_filter:
        if not _FREQ_FILTER_DEPRECATED_WARNED:
            logger.message(
                "warn",
                f"[{IFACE}] bgscan.freq_filter=false is deprecated; "
                "common global freq_list remains enforced",
                _EXTRA_(),
            )
            _FREQ_FILTER_DEPRECATED_WARNED = True
        freq_filter = True

    try:
        conf_extras = validate_ssid_list(network_ssids[1:], base_ssid=ssid)
        snapshot_source = extra_ssids
        if boot_policy is not None and _parse_bool(
            boot_policy.get("generate_network_blocks")
        ):
            snapshot_source = boot_policy.get("extra_ssids")
        snapshot_extras = validate_ssid_list(snapshot_source, base_ssid=ssid)
        conf_extra_identities = set(conf_extras)
        scan_extras = conf_extras + [
            extra
            for extra in snapshot_extras
            if extra not in conf_extra_identities
        ]
    except RoamPolicyError as exc:
        raise BgscanConfigError(f"invalid scan SSID topology: {exc}") from exc
    if backend == "iw":
        cmd = construct_iw_scan_cmd(
            ssid,
            freqs,
            ssid_filter,
            freq_filter,
            scan_extras,
            passive=passive,
        )
        if not ssid_filter and scan_extras and not _WILDCARD_PROBE_WARNED:
            logger.message(
                "warn",
                f"[{IFACE}] ssid_filter=false + extra SSIDs: wildcard(\"\") probe inserted "
                "— verify broad discovery on new drivers/platforms",
                _EXTRA_(),
            )
            _WILDCARD_PROBE_WARNED = True
        return cmd, interval, emit_roam_hint
    if backend == "wpa_cli":
        cmd = construct_wpa_scan_cmd(
            IFACE,
            ssid,
            freqs,
            ssid_filter=ssid_filter,
            extra_ssids=scan_extras,
            passive=passive,
        )
        # wifi_roam.py가 실행되지 않는 native owner에는 backoff hint 소비자가 없다.
        return cmd, interval, False
    raise ValueError(f"unsupported scan backend: {backend}")


def periodic_scan(conf_path, backend, boot_policy):

    # 스캔 명령/주기/필터는 매 스캔 직전 wpa_supplicant conf + JSON에서 재구성한다.
    # 초기 1회 구성 (실패해도 기동 — 다음 스캔 직전 재시도).
    cmd = None
    interval = DEFAULT_INTERVAL
    emit_roam_hint = backend == "iw"
    try:
        cmd, interval, emit_roam_hint = build_scan_request(
            conf_path, backend, boot_policy=boot_policy
        )
        logger.message(
            "info",
            f"[{IFACE}] bgscan start: backend={backend}, cmd={cmd}, interval={interval}",
            _EXTRA_(),
        )
    except Exception as e:
        logger.message("err", f"[{IFACE}] initial bgscan config load failed: {e}", _EXTRA_())

    last_time = time.time()

    while True:
        if not os.path.exists(f"/sys/class/net/{IFACE}"):
            logger.message("info", f"[{IFACE}] waiting for interface...", _EXTRA_())
            time.sleep(5)
            continue

        if not is_wpa_running(IFACE):
            time.sleep(5)
            continue

        if get_flag():
            #logger.message("info", f"[{IFACE}] roam condition on", _EXTRA_())
            time.sleep(5)
            continue

        # roam 스캔이 발생한 경우 bgscan 주기 초기화
        try:
            with open(LAST_SCAN_TIME_FILE, "r") as f:
                roam_scan_time = float(f.read().strip())
            if roam_scan_time > last_time:
                last_time = roam_scan_time
                logger.message("info", f"[{IFACE}] bgscan timer reset by roam scan", _EXTRA_())
        except (FileNotFoundError, ValueError):
            pass

        if time.time() - last_time >= interval:
            # 연결 상태 확인은 스캔 주기 도래 시에만 수행한다(매 tick wpa_cli 서브프로세스 호출 회피).
            # 미연결(스캔/인증/assoc 중)이면 wpa_supplicant의 재연결 스캔/association과 라디오 경합
            # (-EBUSY)·off-channel 교란을 피하려 skip하고 다음 주기로 back off한다.
            # (연결 상태에서 로밍 후보 탐색이 bgscan 본래 목적이라 미연결 스캔은 무의미)
            if not is_wpa_connected(IFACE):
                # last_time을 리셋하지 않는다 → 재연결 직후 (대기 없이) 곧바로 첫 스캔이 발생해
                # 로밍 후보를 빠르게 갱신. 미연결 동안은 5s 간격으로만 재확인하므로 매 tick
                # wpa_cli 호출은 없다(연결 상태에선 interval마다 1회만 호출됨).
                time.sleep(5)
                continue

            # 스캔 직전에 wpa conf + JSON을 다시 읽어 최신 ssid/freq/interval/필터로 스캔한다
            # (런타임 변경 반영). 재로드 실패 시 직전 cmd/interval 유지.
            try:
                cmd, interval, emit_roam_hint = build_scan_request(
                    conf_path, backend, boot_policy=boot_policy
                )
            except Exception as e:
                logger.message("err", f"[{IFACE}] bgscan config reload failed (keep last): {e}", _EXTRA_())

            if cmd:
                with scan_transition_lock(IFACE) as acquired:
                    if acquired:
                        logger.message("info", f"[{IFACE}] {cmd}", _EXTRA_())
                        if run_scan_command(cmd, backend):
                            # 스캔 성공(드라이버에 새 BSS 결과 적재) → roam backoff 해제 신호 touch.
                            # roam이 mtime 변화를 보면 후보없음 streak=0 으로 고속 복귀(spec §4 reset-b).
                            if emit_roam_hint:
                                emit_roam_hint_touch(IFACE)
                    else:
                        logger.message("info", f"[{IFACE}] scan-transition busy; defer bgscan", _EXTRA_())
            # 성공/실패 무관하게 다음 주기까지 back off (실패 시 1s 폭주 재시도 방지)
            last_time = time.time()

        time.sleep(1)

def main_loop(backend, boot_policy):
    #subprocess.run(["ifconfig", IFACE, "up"])
    #last_log_time = time.time()

    # 스캔 파라미터(ssid/freq/interval/필터)는 periodic_scan이 매 스캔 직전 재로드하며,
    # 초기값은 periodic_scan의 "bgscan start" 로그에 찍힌다(여기서 중복 read 안 함).
    logger.message("info", f"[{IFACE}] version: {VERSION} (스캔 파라미터는 매 스캔 직전 재로드)", _EXTRA_())
    periodic_scan(WPA_CONF_FILE, backend, boot_policy)

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, handle_sigterm)
    logger = Logger(app_name="SCAN", facility=logging.handlers.SysLogHandler.LOG_LOCAL0)

    if len(sys.argv) < 2:
        IFACE = "mlan0"
    else:
        IFACE = sys.argv[1]

    WPA_CONF_FILE = f"/etc/wpa_supplicant/wpa_supplicant-{IFACE}.conf"
    # roam 상태 파일 iface별 재대입 — 모듈 상수는 로드 시 mlan0 기본으로 평가됨
    # (wifi_roam ROAM_HINT_FILE 전례와 동일한 이유).
    ROAM_CONDITION_FLAG, LAST_SCAN_TIME_FILE = roam_state_paths(IFACE)
    #logger.message("info", f"[{IFACE}] version : {VERSION}", _EXTRA_())

    if IFACE != "mlan0" and IFACE != "mlan1" :
        logger.message("err", f"[{IFACE}] invalid interface", _EXTRA_())
        sys.exit(1)

    try:
        BOOT_POLICY = load_scan_policy(IFACE)
        SCAN_BACKEND = scan_backend_for_policy(BOOT_POLICY)
    except BgscanConfigError as e:
        logger.message("emerg", f"[{IFACE}] bgscan owner config invalid: {e}", _EXTRA_())
        sys.exit(2)
    if not BOOT_POLICY["bgscan_enabled"]:
        logger.message(
            "notice",
            f"[{IFACE}] boot policy disables package bgscan; refusing stale start",
            _EXTRA_(),
        )
        sys.exit(3)
    ROAM_OWNER = "wifi_roam" if SCAN_BACKEND == "iw" else "wpa_supplicant"
    logger.message(
        "notice",
        f"[{IFACE}] roam_owner={ROAM_OWNER} scan_backend={SCAN_BACKEND} (latched at start)",
        _EXTRA_(),
    )
    main_loop(SCAN_BACKEND, BOOT_POLICY)
