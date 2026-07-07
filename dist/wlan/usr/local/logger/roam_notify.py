#!/usr/bin/env python3
"""로밍 완료 통지 헬퍼 — opcd Roaming Indication(0x04) 구동.

로밍 실행체(wifi_roam.py / passive_roam.py)가 로밍 성공 직후 호출한다.
post-roam link.json(/var/log/cantops/json/<iface>/link.json)을 재독해
페이로드를 구성하고, opcd에 로컬 UDP 데이터그램 1개를 보낸다.

WIRE CONTRACT (opcd 파서와 정확히 일치해야 함):
  transport : UDP/IPv4 → 127.0.0.1:50608 (opcd OPC_DEFAULT_ROAM_NOTIFY_PORT)
  format    : 1 datagram = JSON object 1개, UTF-8, <=512 bytes
  keys      : iface(str) ap_mac(str) from(str) rssi(int) snr(int)
              channel(int) freq(int) band(str "2.4G"|"5G")

전 구간 try/except로 감싸 절대 raise 하지 않는다(로밍 return 경로 보호).
미연결(link.json == "{}" 또는 link.address 없음)이면 조용히 skip(전송 없음).
"""
import argparse
import json
import os
import socket
import sys

DEFAULT_PORT = 50608
OPCD_HOST = "127.0.0.1"
LINK_JSON_FMT = "/var/log/cantops/json/{iface}/link.json"
MAX_DATAGRAM = 512


def _link_json_path(iface):
    return LINK_JSON_FMT.format(iface=iface)


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
        payload["band"] = "5G" if freq >= 2500 else "2.4G"

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
    # noise 부재/0이면 snr omit
    if rssi is not None and noise not in (None, 0):
        payload["snr"] = rssi - noise

    return payload


def notify_roam(iface, from_bssid, to_bssid, port=DEFAULT_PORT):
    """로밍 완료를 opcd에 통지한다. 절대 raise 하지 않는다.

    Returns True on datagram sent, False otherwise(미연결/에러/skip).
    """
    try:
        path = _link_json_path(iface)
        try:
            with open(path, "r") as f:
                raw = f.read()
        except (FileNotFoundError, OSError):
            return False

        if not raw or raw.strip() in ("", "{}"):
            # 미연결
            return False

        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return False

        payload = build_payload(data, iface, from_bssid, to_bssid)
        if not payload:
            return False

        msg = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if len(msg) > MAX_DATAGRAM:
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
        # 로밍 return 경로 보호 — 무슨 일이 있어도 raise 안 함
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
