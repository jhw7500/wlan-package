#!/usr/bin/env python3
"""로밍 완료 통지 헬퍼 — opcd Roaming Indication(0x04) 구동.

로밍 실행체(wifi_roam.py / passive_roam.py)가 로밍 성공 직후 호출한다.
post-roam link.json(/var/log/cantops/json/<iface>/link.json)을 재독해
페이로드를 구성하고, opcd에 로컬 UDP 데이터그램 1개를 보낸다.

WIRE CONTRACT (opcd 파서와 정확히 일치해야 함):
  transport : UDP/IPv4 → 127.0.0.1:50608 (opcd OPC_DEFAULT_ROAM_NOTIFY_PORT)
  format    : 1 datagram = JSON object 1개, UTF-8, <=512 bytes
  keys      : iface(str) ap_mac(str) 는 항상 포함. from/rssi/snr/channel/freq/band 는
              값이 있을 때만 포함(생략 가능). opcd 파서 기준 ap_mac/channel/freq 는
              필수(누락 시 datagram drop), rssi/snr 은 선택(부재→0), iface/from/band 는
              opcd에서 log 전용.

전 구간 try/except로 감싸 절대 raise 하지 않는다(로밍 return 경로 보호).
미연결(link.json == "{}" 또는 link.address 없음)이면 조용히 skip(전송 없음).
"""
import argparse
import json
import os
import socket
import sys
import time

DEFAULT_PORT = 50608
OPCD_HOST = "127.0.0.1"
LINK_JSON_FMT = "/var/log/cantops/json/{iface}/link.json"
MAX_DATAGRAM = 512  # opcd 와이어계약 상한과 일치(opcd가 >512B 드롭). MTU 제한 아님.
# link.json은 재결합 후 비동기로 갱신된다. roam 직후 바로 읽으면 이전 AP를 담고
# 있을 수 있어(리뷰 PR #83), 새 AP(to_bssid)로 정착할 때까지 잠깐 폴링한다.
SETTLE_TIMEOUT_S = 2.0
SETTLE_INTERVAL_S = 0.2


def _link_json_path(iface):
    return LINK_JSON_FMT.format(iface=iface)


def _read_link_json(iface):
    """link.json을 읽어 dict로 파싱. 없음/빈("{}" 포함)/파싱실패면 None."""
    try:
        with open(_link_json_path(iface), "r") as f:
            raw = f.read()
    except (FileNotFoundError, OSError):
        return None
    if not raw or raw.strip() in ("", "{}"):
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _read_link_json_settled(iface, to_bssid,
                            timeout=SETTLE_TIMEOUT_S, interval=SETTLE_INTERVAL_S):
    """link.json을 읽되, to_bssid가 주어지면 link.address가 그 BSSID로 정착(로밍
    완료)할 때까지 짧게 폴링한다. 타임아웃까지 미정착이면 마지막으로 읽은 값을
    반환한다(ap_mac은 build_payload의 to_bssid 폴백이 처리). to_bssid가 비면
    폴링 없이 1회 읽는다(예: cross-SSID select_network 경로)."""
    data = _read_link_json(iface)
    target = (to_bssid or "").strip().lower()
    if not target:
        return data
    deadline = time.monotonic() + timeout
    while True:
        link = data.get("link") if isinstance(data, dict) else None
        addr = link.get("address") if isinstance(link, dict) else None
        if isinstance(addr, str) and addr.strip().lower() == target:
            return data                      # 정착 완료
        if time.monotonic() >= deadline:
            return data                      # 타임아웃 — 마지막 값
        time.sleep(interval)
        data = _read_link_json(iface)


def _parse_rssi(raw):
    """signal_avg는 '-66 dBm' 같은 문자열. ' dBm' strip 후 int.

    안테나별 대괄호 형태('[-68,-70]' 또는 '[-68, -70] dBm')도 방어적으로
    처리: 첫 정수 토큰을 취한다. 파싱 불가 시 None.
    """
    try:
        if raw is None:
            return None
        if isinstance(raw, (int, float)):
            return int(raw)
        s = str(raw).replace("dBm", "").strip()
        if not s:
            return None
        # 대괄호 안테나 형태 방어: '[' ']' 제거 후 첫 콤마 토큰
        s = s.replace("[", " ").replace("]", " ")
        if "," in s:
            s = s.split(",")[0]
        s = s.strip()
        if not s:
            return None
        return int(float(s))
    except (ValueError, TypeError):
        return None


def build_payload(data, iface, from_bssid, to_bssid):
    """link.json dict → opcd wire 페이로드 dict. 미연결이면 None.

    파일 read와 분리한 순수 함수(self-test 용이).
    """
    if not isinstance(data, dict):
        return None

    link = data.get("link")
    if not isinstance(link, dict):
        return None

    ap_mac = link.get("address")
    if isinstance(ap_mac, str):
        ap_mac = ap_mac.strip()
    if not ap_mac:
        # 미연결: link는 있으나 address 없음 → to_bssid로 폴백
        ap_mac = (to_bssid or "").strip()
    if not ap_mac:
        return None

    info = data.get("info") if isinstance(data.get("info"), dict) else {}

    # RSSI: signal_avg 우선, 없으면 signal
    rssi = _parse_rssi(link.get("signal_avg"))
    if rssi is None:
        rssi = _parse_rssi(link.get("signal"))

    # freq / channel
    freq = info.get("freq")
    try:
        freq = int(freq) if freq is not None else None
    except (ValueError, TypeError):
        freq = None

    channel = info.get("channel")
    try:
        channel = int(channel) if channel is not None else None
    except (ValueError, TypeError):
        channel = None

    payload = {"iface": iface, "ap_mac": ap_mac}
    if from_bssid:
        payload["from"] = str(from_bssid).strip()
    if rssi is not None:
        payload["rssi"] = rssi
    if channel is not None:
        payload["channel"] = channel
    if freq is not None:
        payload["freq"] = freq
        payload["band"] = "5G" if freq >= 5000 else "2.4G"

    # SNR = rssi - noise (noise는 channel_info[str(freq)].noise)
    noise = None
    ch_info = data.get("channel_info")
    if isinstance(ch_info, dict) and freq is not None:
        entry = ch_info.get(str(freq))
        if isinstance(entry, dict):
            noise = entry.get("noise")
    try:
        noise = int(noise) if noise is not None else None
    except (ValueError, TypeError):
        noise = None
    # noise 부재/0이면 snr omit. 이 드라이버에서 noise=0은 survey 미초기화(측정불가)를
    # 의미하며 실측 0 dBm이 아니므로, 잘못된 snr(=rssi)을 보내지 않도록 생략한다.
    if rssi is not None and noise not in (None, 0):
        payload["snr"] = rssi - noise

    return payload


def notify_roam(iface, from_bssid, to_bssid, port=DEFAULT_PORT):
    """로밍 완료를 opcd에 통지한다. 절대 raise 하지 않는다.

    Returns True on datagram sent, False otherwise(미연결/에러/skip).
    """
    try:
        # link.address가 to_bssid(새 AP)로 정착할 때까지 짧게 폴링해, 비동기로
        # 갱신되는 link.json의 이전 AP 값을 그대로 싣는 것을 방지한다.
        data = _read_link_json_settled(iface, to_bssid)
        if data is None:
            # 미연결/파일없음
            return False

        payload = build_payload(data, iface, from_bssid, to_bssid)
        if not payload:
            return False

        msg = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if len(msg) > MAX_DATAGRAM:
            print(f"[roam_notify] datagram {len(msg)}B > {MAX_DATAGRAM}B cap — dropped",
                  file=sys.stderr)
            return False

        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.sendto(msg, (OPCD_HOST, int(port)))
            return True
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
    except Exception:
        # 로밍 return 경로 보호 — 무슨 일이 있어도 raise 안 함. 단, 무음 실패로 묻히지
        # 않게 traceback을 stderr에 남긴다(진단 로그 자체도 절대 raise 안 하도록 가드).
        try:
            import traceback
            print(f"[roam_notify] unexpected error: {traceback.format_exc()}",
                  file=sys.stderr)
        except Exception:
            pass
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="opcd로 로밍 완료 통지(UDP)를 보낸다.")
    parser.add_argument("--iface", default="mlan0", help="무선 인터페이스 (기본 mlan0)")
    parser.add_argument("--from", dest="from_bssid", default="",
                        help="이전 AP BSSID (로그용, 선택)")
    parser.add_argument("--to", dest="to_bssid", default="",
                        help="대상 AP BSSID (link.address 폴백)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"opcd UDP 포트 (기본 {DEFAULT_PORT})")
    args = parser.parse_args(argv)

    ok = notify_roam(args.iface, args.from_bssid, args.to_bssid, args.port)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
