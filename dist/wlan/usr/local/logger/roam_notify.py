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

DEFAULT_PORT = 50608
OPCD_HOST = "127.0.0.1"
LINK_JSON_FMT = "/var/log/cantops/json/{iface}/link.json"
MAX_DATAGRAM = 512  # opcd 와이어계약 상한과 일치(opcd가 >512B 드롭). MTU 제한 아님.


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
        # 'dBm'/괄호/콤마를 공백으로 정규화하고 첫 파싱 가능한 정수 토큰을 취한다
        # (안테나별 '-66 [-68,-70]' 조합에서도 결합 평균 -66을 반환).
        s = str(raw).replace("dBm", "").replace("[", " ").replace("]", " ").replace(",", " ")
        for tok in s.split():
            try:
                return int(float(tok))
            except ValueError:
                continue
        return None
    except (ValueError, TypeError):
        return None


def _channel_to_freq(ch):
    """802.11 채널번호 → 중심주파수(MHz). 2.4G(1-14)/5G(36-177)만 지원, 불명 시 None.
    (passive_roam 스캔 데이터는 channel만 있고 freq가 없어 파생에 사용.)"""
    try:
        ch = int(ch)
    except (ValueError, TypeError):
        return None
    if 1 <= ch <= 13:
        return 2412 + (ch - 1) * 5
    if ch == 14:
        return 2484
    if 36 <= ch <= 177:
        return 5000 + ch * 5
    return None


def build_payload(data, iface, from_bssid, to_bssid,
                  channel=None, freq=None, rssi=None):
    """link.json dict → opcd wire 페이로드 dict. 미연결이면 None.

    channel/freq/rssi가 주어지면(호출자의 대상 AP 스캔값=권위) link.json보다 우선한다
    — 로밍 직후 비동기로 지연되는 link.json이 이전 AP 값을 담고 있어도 정확하게 발행.

    파일 read와 분리한 순수 함수(self-test 용이).
    """
    if not isinstance(data, dict):
        return None

    link = data.get("link")
    if not isinstance(link, dict):
        return None

    # ap_mac: same-SSID 로밍은 to_bssid가 목표 BSS(정확)이므로 우선 사용 — link.json이
    # 아직 이전 AP를 담고 있어도 정확하고, 블로킹 폴링도 불필요. cross-SSID는 펌웨어가
    # BSS를 자율 선택하므로 호출자가 to_bssid=""를 넘기며, 이때 link.address(실 결합
    # BSS)로 폴백한다. (rssi/snr/channel은 link.json 1회 읽기 — 소폭 staleness 허용)
    ap_mac = (to_bssid or "").strip()
    if not ap_mac:
        addr = link.get("address")
        ap_mac = addr.strip() if isinstance(addr, str) else ""
    if not ap_mac:
        return None

    info = data.get("info") if isinstance(data.get("info"), dict) else {}

    # channel/freq/rssi: 호출자가 대상 AP 스캔값(권위)을 주면 우선 — 로밍 직후 link.json이
    # 아직 이전 AP를 담고 있어도 정확. 없으면 link.json에서 읽는다.
    if rssi is None:
        rssi = _parse_rssi(link.get("signal_avg"))
        if rssi is None:
            rssi = _parse_rssi(link.get("signal"))
    if channel is None:
        channel = info.get("channel")
    if freq is None:
        freq = info.get("freq")
    # 채널만 있고 freq가 없으면(예: passive_roam 스캔은 ch만) 채널→freq 파생
    if freq is None and channel is not None:
        freq = _channel_to_freq(channel)
    try:
        rssi = int(rssi) if rssi is not None else None
    except (ValueError, TypeError):
        rssi = None
    try:
        freq = int(freq) if freq is not None else None
    except (ValueError, TypeError):
        freq = None
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


def notify_roam(iface, from_bssid, to_bssid, port=DEFAULT_PORT,
                channel=None, freq=None, rssi=None):
    """로밍 완료를 opcd에 통지한다. 절대 raise 하지 않는다.

    channel/freq/rssi: 호출자가 아는 대상 AP 스캔값(권위). 주면 link.json보다 우선.

    Returns True on datagram sent, False otherwise(미연결/에러/skip).
    """
    try:
        # link.json 1회 읽기(폴링 없음). ap_mac은 build_payload에서 to_bssid를 우선
        # 사용하므로 link.json 비동기 지연에도 정확하다(same-SSID). cross-SSID는
        # 호출자가 to_bssid=""를 넘겨 link.address(실 결합 BSS)를 쓴다.
        data = _read_link_json(iface)
        if data is None:
            # 미연결/파일없음
            return False

        payload = build_payload(data, iface, from_bssid, to_bssid,
                                channel=channel, freq=freq, rssi=rssi)
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
    parser.add_argument("--channel", type=int, default=None,
                        help="대상 AP 채널 (권위값; 생략 시 link.json)")
    parser.add_argument("--freq", type=int, default=None,
                        help="대상 AP 주파수 MHz (권위값; 생략 시 channel 파생/link.json)")
    parser.add_argument("--rssi", type=int, default=None,
                        help="대상 AP RSSI dBm (권위값; 생략 시 link.json)")
    args = parser.parse_args(argv)

    ok = notify_roam(args.iface, args.from_bssid, args.to_bssid, args.port,
                     channel=args.channel, freq=args.freq, rssi=args.rssi)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
