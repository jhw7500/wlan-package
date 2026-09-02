#!/usr/bin/env python3
import json
import os
import re
import sys
import subprocess
import time
from datetime import datetime

from roam_notify import notify_roam, get_associated_bssid, confirm_roam
from roam_state import scan_transition_lock
from roam_policy import (
    RoamPolicyError,
    decode_wpa_ssid_text,
    parse_wpa_ssid_value,
    validate_ssid,
)

WIFI_IFACE = "mlan0"
SCAN_LOG = f"/var/log/cantops/scan/{WIFI_IFACE}/ap.log"
LINK_JSON = f"/var/log/cantops/json/{WIFI_IFACE}/link.json"
WPA_CONF_FILE = f"/etc/wpa_supplicant/wpa_supplicant-{WIFI_IFACE}.conf"
WIFI_INIT_CONF_JSON = "/usr/local/etc/wifi_init_conf.json"
SCAN_TIMESTAMP_RE = re.compile(
    r"^(?:\[(?P<bracketed>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]"
    r"|(?P<plain>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}))$"
)
# ap.log 마지막 블록의 허용 age 상한(bgscan 기본 주기 60s 의 2.5배). scan 로거가 죽으면
# 마지막 블록이 무기한 재사용되므로, 이보다 오래된 블록은 stale 로 거부한다(설정 노브 아님).
SCAN_BLOCK_MAX_AGE_SEC = 150


def abort_scan_quiesce(iface):
    """ABORT_SCAN이 plain FAIL(진행 scan 없음)로 수렴할 때만 안전하다고 판정."""
    for attempt in range(1, 6):
        try:
            result = subprocess.run(
                ["wpa_cli", "-i", iface, "abort_scan"],
                capture_output=True,
                text=True,
                timeout=2,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False
        if result.returncode != 0:
            return False
        reply = (result.stdout or "").strip()
        if reply == "FAIL":
            return True
        if reply != "OK" or attempt == 5:
            return False
        time.sleep(0.05)
    return False


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


def ssid_from_supplicant(iface):
    """`wpa_cli status` 의 ssid= — 결합한 네트워크의 권위 소스.

    wpa_state=COMPLETED 일 때만 채택한다(결합 미완료 시점의 ssid 줄은 목표일 뿐
    현재가 아니다 — roam_notify._bssid_from_status 와 같은 규칙). CTRL_IFACE 는
    printf_encode 형식이라 decode_wpa_ssid_text 로 정확한 identity 를 복원한다.
    조회 실패는 "" — 호출자가 다음 소스로 넘어간다.
    """
    try:
        result = subprocess.run(
            ["wpa_cli", "-i", iface, "status"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""
    if result.returncode != 0:
        return ""
    state = None
    ssid = ""
    for line in (result.stdout or "").splitlines():
        if line.startswith("wpa_state="):
            state = line.split("=", 1)[1].strip()
        elif line.startswith("ssid="):
            try:
                ssid = decode_wpa_ssid_text(line.split("=", 1)[1])
            except RoamPolicyError:
                ssid = ""
    return ssid if state == "COMPLETED" else ""


def ssid_from_wpa_conf(conf_path):
    """단일 network 블록 conf 의 ssid= — supplicant 조회가 안 될 때의 폴백.

    **블록이 둘 이상이면 "" 를 돌려준다.** Mode A 다중 블록에서는 자동 owner 가
    select_network 로 두 번째 이후 블록에 붙어 있을 수 있어 첫 블록이 라이브라는
    보장이 없다. 그 값을 그대로 쓰면 필터도 roam_to_ap 의 same-SSID 가드도 같은
    오답을 공유해 통과하고, 결국 다른 망의 BSS 로 `wpa_cli roam` 이 나간다 —
    그럴듯한 오답보다 "모른다"가 안전하므로 호출자의 unknown 거부 경로로 보낸다.
    """
    first_ssid = ""
    blocks = 0
    try:
        with open(conf_path, "r") as f:
            in_network = False
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if re.match(r"^network\s*=\s*\{", line):
                    in_network = True
                    blocks += 1
                    if blocks > 1:
                        return ""  # 어느 블록이 라이브인지 여기서는 알 수 없다
                    continue
                if in_network and line.startswith("}"):
                    in_network = False
                    continue
                if in_network and not first_ssid and line.startswith("ssid="):
                    first_ssid = parse_wpa_ssid_value(line.split("=", 1)[1])
    except (OSError, RoamPolicyError):
        return ""
    return first_ssid


def read_current_ssid(iface=WIFI_IFACE, conf_path=None):
    """현재 SSID를 (권위 → 폴백) 계단식으로 판정하고 (ssid, source) 를 돌려준다.

    link.json 은 여기서 쓰지 않는다 — 그 info.ssid 는 `iw <iface> info` 를 로거가
    주기 기록한 비동기 캐시라, 대상 검증 기준으로는 supplicant/conf 보다 약하다.
    (link.json 은 BSSID 표시용으로 계속 쓴다.)
    """
    ssid = ssid_from_supplicant(iface)
    if ssid:
        return ssid, "supplicant"
    ssid = ssid_from_wpa_conf(conf_path or WPA_CONF_FILE)
    if ssid:
        return ssid, "wpa conf"
    return "", "unknown"


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


def build_candidate_list():
    """
    Return (current_bssid, current_ssid, ssid_source, candidates)

    candidates: list of AP dicts with extra key "is_current"
    All APs are included, including the current one.
    Restricted to the SSID we are associated with: manual roaming is a
    same-SSID BSS transition.  Switching networks is a different operation
    with a different cost (`wifi <iface> connect <ssid>`, a reconnect), so it
    is not reachable from this list.
    Sorted by RSSI (ss) descending (higher is better).
    """
    current_bssid = read_current_bssid(LINK_JSON)
    current_ssid, ssid_source = read_current_ssid(WIFI_IFACE)
    aps = parse_last_scan_block(SCAN_LOG)

    if not aps:
        print("No scan block found in ap.log")
        return current_bssid, current_ssid, ssid_source, []

    for ap in aps:
        bssid_low = ap["bssid"].strip().lower()
        ap["is_current"] = (bssid_low == current_bssid)

    # Keep only the connected SSID.  With every source exhausted the SSID is
    # unknown and there is nothing to filter against: still show the scan for
    # diagnosis, and let roam_to_ap refuse — an unverifiable target must not be
    # roamed to just because it holds a list position.
    if current_ssid:
        aps = [ap for ap in aps if ap["ssid"] == current_ssid]

    # Sort by RSSI (higher is better; e.g. -40 > -50)
    aps.sort(key=lambda x: x["ss"], reverse=True)

    return current_bssid, current_ssid, ssid_source, aps


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


def roam_to_ap(interface, ap, index_label=None, current_ssid=None):
    """
    Roam to the given AP with `wpa_cli roam <bssid>` (무중단).

    같은 SSID 안의 BSS 전환 전용이다. `wpa_cli roam`은 현재 선택된 network
    블록(= 현재 SSID) 안의 BSS로만 전환하므로 다른 SSID는 이 경로로 갈 수 없다.
    망 전환(재연결을 동반)은 `wifi <iface> connect <ssid>`의 역할이라 여기서
    대신 수행하지 않는다 — 무중단 로밍을 기대한 조작이 링크 단절로 바뀌지 않게 한다.
    If ap["is_current"] is True, do not roam.
    """
    if ap.get("is_current"):
        print("\nSelected AP is the current AP. No roaming performed.")
        return 0

    bssid = ap["bssid"]
    ap_ssid = ap.get("ssid") or ""
    if not current_ssid:
        # 대상이 같은 SSID 인지 증명할 기준이 없다. 목록은 진단용으로 보여주되
        # 여기서 멈춘다 — 번호를 골랐다는 이유만으로 검증 못 한 BSS 로 나가면
        # 다른 망의 AP 에 roam 을 발행하게 된다.
        print(
            "\nCannot determine the connected SSID "
            "(wpa_cli status and wpa conf both unavailable); "
            "roam needs it to prove the target is same-SSID. Not roaming."
        )
        return 1
    if ap_ssid and ap_ssid != current_ssid:
        print(
            f"\nSelected AP is on SSID '{ap_ssid}', not the connected "
            f"'{current_ssid}'. roam performs a same-SSID BSS transition only; "
            f"use 'wifi {interface} connect {ap_ssid}' to switch networks."
        )
        return 1

    cmd = ["wpa_cli", "-i", interface, "roam", bssid]

    print(f"\nSelected AP:")
    if index_label is not None:
        print(f"  No:   {index_label}")
    print(f"  BSSID: {bssid}")
    print(f"  SSID:  {ap['ssid']}")
    print(f"  CH:    {ap['ch']}")
    print(f"  RSSI:  {ap['ss']}")
    print(f"\nExecuting: {' '.join(cmd)}")

    # from_bssid는 roam 실행 전에 캡처한다: read_current_bssid()가 읽는 link.json은
    # 재결합 후 비동기로 갱신되므로, roam 이후 호출하면 이미 새 AP를 반환해 from==to가
    # 된다. LINK_JSON을 명시 전달 — 기본인자는 def 시점 값(mlan0)으로 고정되어
    # --iface 변경(모듈변수 갱신)을 반영하지 못한다.
    # 공용 transition 잠금을 획득해 bgscan/manual scan/connect와 association 변경을
    # 직렬화한다.
    with scan_transition_lock(interface) as acquired:
        if not acquired:
            print(f"scan/association transition busy for {interface}; roam not started")
            return 1
        if not abort_scan_quiesce(interface):
            print(f"native scan did not quiesce for {interface}; roam not started")
            return 1

        from_bssid = read_current_bssid(LINK_JSON)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            stdout = result.stdout.strip() if result.stdout else ""
            stderr = result.stderr.strip() if result.stderr else ""
            print(f"\noutput: {stdout} {stderr}".rstrip())

            # wpa_cli는 supplicant가 "FAIL"을 응답해도 exit 0을 주므로
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


def roam_to_best_non_current(interface, candidates, current_ssid=None):
    """
    Find the best AP excluding the current one and roam to it.
    Candidates are restricted to the connected SSID in build_candidate_list.
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
    )


def main():
    global WIFI_IFACE, SCAN_LOG, LINK_JSON, WPA_CONF_FILE

    import argparse
    parser = argparse.ArgumentParser(
        description="Manual same-SSID roaming tool (wpa_cli roam)"
    )
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
    WPA_CONF_FILE = os.environ.get(
        "PASSIVE_ROAM_WPA_CONF",
        f"/etc/wpa_supplicant/wpa_supplicant-{WIFI_IFACE}.conf",
    )

    current_bssid, current_ssid, ssid_source, candidates = build_candidate_list()
    print(
        f"Roaming within SSID: {current_ssid or '<unknown>'} "
        f"(source: {ssid_source}, same-SSID only)"
    )
    if not candidates:
        sys.exit(1)

    # Always print the list (including current AP)
    print_candidate_list(current_bssid, candidates)

    # No argument: just show list, no roaming
    if args.index is None:
        return

    # Argument 0: auto-roam to best AP excluding current
    if args.index == 0:
        ret = roam_to_best_non_current(WIFI_IFACE, candidates, current_ssid)
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
    )
    sys.exit(ret)


if __name__ == "__main__":
    main()
