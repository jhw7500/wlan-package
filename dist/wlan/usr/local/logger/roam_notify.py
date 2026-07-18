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
import subprocess
import sys
import time

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


def _bssid_from_status(text):
    """`wpa_cli status` 출력에서 결합 BSSID를 추출하는 순수 함수.

    wpa_state=COMPLETED 이고 bssid 줄이 있을 때만 그 값을 반환, 아니면 None
    (결합 미완료 시점의 bssid 줄은 신뢰하지 않는다).
    """
    state = None
    bssid = None
    for ln in (text or "").splitlines():
        if ln.startswith("wpa_state="):
            state = ln.split("=", 1)[1].strip()
        elif ln.startswith("bssid="):
            bssid = ln.split("=", 1)[1].strip()
    if state == "COMPLETED" and bssid:
        return bssid
    return None


def get_associated_bssid(iface, wait_s=3.0, poll_s=0.5):
    """현재 결합 BSSID를 wpa_cli status(권위)로 조회하는 **호출자용** 헬퍼.

    cross-SSID 전환은 펌웨어가 BSS를 자율 선택해 호출자가 목표 BSSID를 모르고,
    link.json은 비동기(~1s 주기) 갱신이라 전환 직후엔 이전 AP가 남을 수 있다.
    → 전환 성공 직후 이 헬퍼로 실 결합 BSS를 얻어 notify_roam(to_bssid=..)에 넘긴다.

    wifi connect 가 결합 완료 전에 rc==0 을 반환할 수 있어 wpa_state=COMPLETED 까지
    wait_s 한도로 poll_s 간격 재시도한다(이미 결합 상태면 첫 호출에서 즉시 반환).
    실패/미결합/예외는 "" — 호출자가 그대로 to_bssid 로 넘기면 link.address 폴백
    (종전 동작)이라 무회귀. 절대 raise 하지 않는다.

    notify_roam 자체는 이 함수를 호출하지 않는다(전송 경로는 subprocess 없이 유지).
    """
    try:
        deadline = time.monotonic() + max(0.0, float(wait_s))
        while True:
            try:
                st = subprocess.run(
                    ["wpa_cli", "-i", iface, "status"],
                    capture_output=True, text=True, timeout=2,
                )
                bssid = _bssid_from_status(st.stdout)
                if bssid:
                    return bssid
            except Exception:
                pass  # 개별 시도 실패는 재시도가 흡수
            if time.monotonic() >= deadline:
                return ""
            time.sleep(poll_s)
    except Exception:
        return ""


CONFIRM_WAIT_S = 5.0  # roam 후 wpa_state=COMPLETED@target 확인 폴링 한도(초)


def confirm_roam(iface, target_bssid, wait_s=CONFIRM_WAIT_S, poll_s=0.5):
    """`wpa_cli roam`은 비동기라 명령 수락("OK")만으론 재결합 완료를 알 수 없다.
    get_associated_bssid(wait_s=0.0)=단발 wpa_cli status 조회(COMPLETED 아니면 "")로
    목표 BSSID 일치까지 폴링한다. roam 진행 전 첫 조회는 이전 AP(COMPLETED)일 수 있어
    목표 일치 또는 타임아웃까지 반복한다. link.json(비동기 갱신)은 쓰지 않는다.
    wifi_roam.roam_to_bssid / passive_roam.roam_to_ap 공용(중복 제거)."""
    target = (target_bssid or "").strip().lower()
    if not target:
        return False
    deadline = time.monotonic() + max(0.0, float(wait_s))
    while True:
        assoc = (get_associated_bssid(iface, wait_s=0.0) or "").strip().lower()
        if assoc == target:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll_s)


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
