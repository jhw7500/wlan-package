#!/usr/bin/env python3
import json
import os
import re
import sys
import subprocess
import time
from datetime import datetime

from roam_notify import notify_roam, get_associated_bssid, confirm_roam
from roam_policy import (
    RoamPolicyError,
    load_boot_roam_policy,
    validate_ssid,
    validate_ssid_list,
)

WIFI_IFACE = "mlan0"
SCAN_LOG = f"/var/log/cantops/scan/{WIFI_IFACE}/ap.log"
LINK_JSON = f"/var/log/cantops/json/{WIFI_IFACE}/link.json"
WIFI_INIT_CONF_JSON = "/usr/local/etc/wifi_init_conf.json"
WIFI_COMMAND = os.environ.get("PASSIVE_ROAM_WIFI_COMMAND", "/usr/local/bin/wifi")
SCAN_TIMESTAMP_RE = re.compile(
    r"^(?:\[(?P<bracketed>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]"
    r"|(?P<plain>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}))$"
)
# ap.log 마지막 블록의 허용 age 상한(bgscan 기본 주기 60s 의 2.5배). scan 로거가 죽으면
# 마지막 블록이 무기한 재사용되므로, 이보다 오래된 블록은 stale 로 거부한다(설정 노브 아님).
SCAN_BLOCK_MAX_AGE_SEC = 150


def scan_block_max_age_sec(iface=WIFI_IFACE, conf_path=WIFI_INIT_CONF_JSON):
    """bgscan 주기가 길어도 정상 cache를 stale로 오판하지 않는 동적 상한."""
    try:
        with open(conf_path, "r") as f:
            interval = json.load(f).get(iface, {}).get("bgscan", {}).get("interval")
        if isinstance(interval, int) and not isinstance(interval, bool) and interval > 0:
            return max(SCAN_BLOCK_MAX_AGE_SEC, int(interval * 2.5))
    except (FileNotFoundError, json.JSONDecodeError, KeyError, AttributeError, TypeError):
        pass
    return SCAN_BLOCK_MAX_AGE_SEC


def read_current_bssid(link_json_path=LINK_JSON):
    """Read current connected BSSID from link.json"""
    try:
        with open(link_json_path, "r") as f:
            data = json.load(f)
        return data.get("link", {}).get("address", "").strip().lower()
    except (
        FileNotFoundError,
        json.JSONDecodeError,
        KeyError,
        AttributeError,
        TypeError,
        RoamPolicyError,
    ):
        return ""


def read_current_ssid(link_json_path=LINK_JSON):
    """Read current connected SSID from link.json"""
    try:
        with open(link_json_path, "r") as f:
            data = json.load(f)
        return validate_ssid(data.get("info", {}).get("ssid", ""))
    except (
        FileNotFoundError,
        json.JSONDecodeError,
        KeyError,
        AttributeError,
        TypeError,
        RoamPolicyError,
    ):
        return ""


def load_manual_roam_policy(iface, run_dir=None):
    """Read the immutable boot owner/topology contract for manual roaming."""
    return load_boot_roam_policy(iface, run_dir=run_dir)


def load_extra_ssids(iface, conf_path=WIFI_INIT_CONF_JSON):
    """Compatibility accessor backed only by the immutable boot snapshot."""
    del conf_path
    try:
        return list(load_manual_roam_policy(iface)["extra_ssids"])
    except RoamPolicyError:
        return []


def parse_last_scan_block(scan_log_path=SCAN_LOG, max_age_sec=None):
    """Parse the last scan block from ap.log"""
    if max_age_sec is None:
        max_age_sec = scan_block_max_age_sec(WIFI_IFACE, WIFI_INIT_CONF_JSON)
    if not os.path.exists(scan_log_path):
        return []

    with open(scan_log_path, "r") as f:
        lines = f.read().splitlines()

    if not lines:
        return []

    # Find the last timestamp header (legacy [timestamp] and current timestamp).
    start_idx = None
    ts_match = None
    for i in range(len(lines) - 1, -1, -1):
        line = lines[i].strip()
        ts_match = SCAN_TIMESTAMP_RE.fullmatch(line)
        if ts_match:
            start_idx = i
            break

    if start_idx is None:
        return []

    # stale 가드: 블록 timestamp 가 상한보다 오래됐으면 거부 — 죽은 scan 로거의 옛 데이터로
    # 로밍하는 것을 막는다. strptime 불가(포맷 불명)면 age 판정 불가 — 종전 동작(수용) 유지.
    ts_str = ts_match.group("bracketed") or ts_match.group("plain")
    try:
        block_ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").timestamp()
    except ValueError:
        block_ts = None
    if block_ts is not None:
        age_sec = time.time() - block_ts
        if age_sec > max_age_sec:
            print(f"scan data stale ({int(age_sec)}s old > {max_age_sec}s limit) — run a scan first")
            return []

    block_lines = lines[start_idx + 1:]

    aps = []
    for raw in block_lines:
        line = raw.rstrip("\r\n")
        structural = line.lstrip()
        if not structural or "|" not in line:
            continue
        if structural.startswith("-") or structural.startswith("#") or structural.startswith("["):
            continue

        parts = line.split("|")
        if len(parts) < 7:
            continue

        try:
            aps.append({
                "idx": parts[0].strip(),
                "ch": int(parts[1].strip()),
                "ss": int(parts[2].strip()),
                "ld": int(parts[3].strip()),
                "bssid": parts[4].strip(),
                "cap": parts[5].strip(),
                "ssid": validate_ssid("|".join(parts[6:])),
            })
        except (ValueError, RoamPolicyError):
            continue

    return aps


def build_candidate_list(policy=None):
    """
    Return (current_bssid, current_ssid, candidates, extra_ssids)

    candidates: list of AP dicts with extra key "is_current"
    All APs are included, including the current one.
    Filtered by allowed SSIDs (current + roaming.extra_ssids).
    Sorted by RSSI (ss) descending (higher is better).
    """
    current_bssid = read_current_bssid(LINK_JSON)
    current_ssid = read_current_ssid(LINK_JSON)
    if policy is None:
        policy = load_manual_roam_policy(WIFI_IFACE)
    mode_a = policy["generate_network_blocks"]
    # The snapshot rejected base/extra duplication at boot.  At runtime the
    # current SSID may legitimately be one of those extras after an automatic
    # Mode A selection or a manual Mode B connect; treat it as the live base
    # for same-SSID roaming instead of misclassifying the boot policy.
    policy_extras = validate_ssid_list(list(policy["extra_ssids"]))
    # Mode A extras are autonomous owner topology, not manual cross-SSID
    # targets.  Do not advertise their identities to this CLI.
    extra_ssids = [] if mode_a else policy_extras
    if mode_a and policy_extras:
        print("manual cross-SSID selection is unsupported in Mode A")
    allowed = ([current_ssid] if current_ssid else []) + [
        s for s in extra_ssids if s and s != current_ssid
    ]
    aps = parse_last_scan_block(SCAN_LOG)

    if not aps:
        print("No scan block found in ap.log")
        return current_bssid, current_ssid, [], extra_ssids

    for ap in aps:
        bssid_low = ap["bssid"].strip().lower()
        ap["is_current"] = (bssid_low == current_bssid)

    # Filter by allowed SSIDs (current + roaming.extra_ssids)
    if allowed:
        aps = [ap for ap in aps if ap["ssid"] in allowed]

    # Sort by RSSI (higher is better; e.g. -40 > -50)
    aps.sort(key=lambda x: x["ss"], reverse=True)

    return current_bssid, current_ssid, aps, extra_ssids


def print_candidate_list(current_bssid, candidates):
    print(f"Current BSSID: {current_bssid or '<unknown>'}")
    print("-" * 80)
    print("{:<3} {:>3} {:>4} {:>4} {:17} {:10} {}".format(
        "No", "ch", "ss", "ld", "bssid", "cap", "ssid"))
    print("-" * 80)

    for i, ap in enumerate(candidates, start=1):
        tag = "<current>" if ap.get("is_current") else ""
        print("{:<3} {:>3} {:>4} {:>4} {:17} {:10} {} {}".format(
            i, ap["ch"], ap["ss"], ap["ld"], ap["bssid"], ap["cap"], ap["ssid"], tag
        ))


def roam_to_ap(interface, ap, index_label=None, current_ssid=None, mode_a=False):
    """
    Roam to the given AP.
    - 같은 SSID: wpa_cli roam <bssid> (무중단)
    - 다른 SSID: wifi <iface> connect <ssid> <ch> (conf ssid 교체→reconfigure→reassociate, 재연결).
      wpa_cli roam은 같은 network 블록(SSID) 내 BSS만 전환하므로 다른 SSID는 connect로 처리.
    If ap["is_current"] is True, do not roam.
    """
    if ap.get("is_current"):
        print("\nSelected AP is the current AP. No roaming performed.")
        return 0

    bssid = ap["bssid"]
    cross_ssid = bool(current_ssid) and bool(ap.get("ssid")) and ap["ssid"] != current_ssid
    if cross_ssid and mode_a:
        print("manual cross-SSID selection is unsupported in Mode A")
        return 1
    if cross_ssid:
        # freq 생략: 단일 freq를 주면 전역/블록 공통 freq_list가 그 채널로 collapse됨
        cmd = [WIFI_COMMAND, interface, "connect", ap["ssid"]]
    else:
        cmd = ["wpa_cli", "-i", interface, "roam", bssid]

    print(f"\nSelected AP:")
    if index_label is not None:
        print(f"  No:   {index_label}")
    print(f"  BSSID: {bssid}")
    print(f"  SSID:  {ap['ssid']}")
    print(f"  CH:    {ap['ch']}")
    print(f"  RSSI:  {ap['ss']}")
    print(f"  MODE:  {'connect (cross-SSID)' if cross_ssid else 'roam (same-SSID)'}")
    print(f"\nExecuting: {' '.join(cmd)}")

    # from_bssid는 roam 실행 전에 캡처한다: read_current_bssid()가 읽는 link.json은
    # 재결합 후 비동기로 갱신되므로, roam 이후 호출하면 이미 새 AP를 반환해 from==to가
    # 된다. LINK_JSON을 명시 전달 — 기본인자는 def 시점 값(mlan0)으로 고정되어
    # --iface 변경(모듈변수 갱신)을 반영하지 못한다.
    from_bssid = read_current_bssid(LINK_JSON)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        stdout = result.stdout.strip() if result.stdout else ""
        stderr = result.stderr.strip() if result.stderr else ""
        print(f"\noutput: {stdout} {stderr}".rstrip())

        if cross_ssid:
            # cross-SSID(wifi connect 래퍼): 래퍼 exit code가 성공 계약. 펌웨어가 BSS를
            # 자율 선택하므로 wpa_cli status(권위)로 실 결합 BSS를 조회해 통지 — link.json은
            # 비동기 갱신이라 직후엔 이전 AP가 남을 수 있다. 실패 시 "" → link.address
            # 폴백(종전 동작), 무회귀.
            print(f"ROAM_RESULT: {bssid} ch:{ap['ch']} rssi:{ap['ss']} -> {stdout or stderr or 'unknown'}")
            if result.returncode == 0:
                notify_roam(interface, from_bssid, get_associated_bssid(interface))
            return result.returncode

        # same-SSID(wpa_cli roam): wpa_cli는 supplicant가 "FAIL"을 응답해도 exit 0을 주므로
        # returncode가 아니라 응답 텍스트로 '수락(OK)'을 판정하고, 이후 wpa_cli status를
        # 폴링해 재결합 완료(COMPLETED@target)를 확인한 경우에만 성공으로 본다.
        accepted = result.returncode == 0 and stdout.split("\n", 1)[0].strip() == "OK"
        confirmed = accepted and confirm_roam(interface, bssid)
        note = "confirmed" if confirmed else ("not confirmed" if accepted else "rejected")
        # 한줄 요약 (wifi_periodic_roam.sh에서 grep "ROAM_RESULT"로 추출)
        print(f"ROAM_RESULT: {bssid} ch:{ap['ch']} rssi:{ap['ss']} -> {stdout or stderr or 'unknown'} ({note})")
        if confirmed:
            # 재결합 확인된 경우에만 통지 — bssid=목표 BSS, ch/rssi=스캔 권위값.
            notify_roam(interface, from_bssid, bssid, channel=ap["ch"], rssi=ap["ss"])
        return 0 if confirmed else 1
    except subprocess.TimeoutExpired:
        print(f"ROAM_RESULT: {bssid} ch:{ap['ch']} rssi:{ap['ss']} -> timeout")
        # timeout이어도 로밍이 뒤늦게 성공했을 수 있다(설계 §8.1 미세갭): 실 결합 BSS가
        # from과 다르면 통지. 조회 실패("")나 미변경이면 통지 생략 — 오발행 없음.
        cur = get_associated_bssid(interface)
        if cur and cur.lower() != (from_bssid or "").lower():
            notify_roam(interface, from_bssid, cur)
        return 1
    except FileNotFoundError:
        print("roam command not found.")
        return 1


def roam_to_best_non_current(interface, candidates, current_ssid=None, mode_a=False):
    """
    Find the best AP excluding the current one and roam to it.
    Candidates are filtered by allowed SSIDs (current + extra_ssids) in build_candidate_list.
    """
    others = [ap for ap in candidates if not ap.get("is_current")]
    if not others:
        print("No other APs available to roam.")
        return 1

    others.sort(key=lambda x: x["ss"], reverse=True)
    best_ap = others[0]
    return roam_to_ap(
        interface,
        best_ap,
        index_label="best_non_current",
        current_ssid=current_ssid,
        mode_a=mode_a,
    )


def main():
    global WIFI_IFACE, SCAN_LOG, LINK_JSON, WIFI_COMMAND

    import argparse
    parser = argparse.ArgumentParser(description="Passive roaming tool")
    parser.add_argument("index", nargs="?", type=int, default=None,
                        help="0=best auto, N=roam to Nth AP (RSSI order)")
    parser.add_argument("--iface", default="mlan0",
                        choices=["mlan0", "mlan1"],
                        help="Interface name (default: mlan0)")
    args = parser.parse_args()

    WIFI_IFACE = args.iface
    SCAN_LOG = os.environ.get(
        "PASSIVE_ROAM_SCAN_LOG", f"/var/log/cantops/scan/{WIFI_IFACE}/ap.log"
    )
    LINK_JSON = os.environ.get(
        "PASSIVE_ROAM_LINK_JSON", f"/var/log/cantops/json/{WIFI_IFACE}/link.json"
    )
    WIFI_COMMAND = os.environ.get("PASSIVE_ROAM_WIFI_COMMAND", "/usr/local/bin/wifi")

    try:
        policy = load_manual_roam_policy(WIFI_IFACE)
        current_bssid, current_ssid, candidates, extra_ssids = build_candidate_list(policy)
    except RoamPolicyError as exc:
        print(f"cannot load immutable boot roaming policy: {exc}")
        sys.exit(1)
    mode_a = policy["generate_network_blocks"]
    print(f"Allowed SSIDs: current={current_ssid} extra_ssids={extra_ssids}")
    if not candidates:
        sys.exit(1)

    # Always print the list (including current AP)
    print_candidate_list(current_bssid, candidates)

    # No argument: just show list, no roaming
    if args.index is None:
        return

    # Argument 0: auto-roam to best AP excluding current
    if args.index == 0:
        ret = roam_to_best_non_current(
            WIFI_IFACE, candidates, current_ssid, mode_a=mode_a
        )
        sys.exit(ret)

    # Argument > 0: roam to N-th AP in the printed list
    if args.index < 0 or args.index > len(candidates):
        print(f"Invalid index: {args.index} (valid 0~{len(candidates)})")
        sys.exit(1)

    ap = candidates[args.index - 1]
    ret = roam_to_ap(
        WIFI_IFACE,
        ap,
        index_label=args.index,
        current_ssid=current_ssid,
        mode_a=mode_a,
    )
    sys.exit(ret)


if __name__ == "__main__":
    main()
