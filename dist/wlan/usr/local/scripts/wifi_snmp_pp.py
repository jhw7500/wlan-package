#!/usr/bin/env python3
"""wifi_snmp_pp.py — net-snmp ``pass_persist`` 백엔드 (CONTEC FXE3000 Private MIB).

B안 Phase1 구현. snmpd.conf 의
``pass_persist .1.3.6.1.4.1.672.65 /usr/bin/python3 -u /usr/local/scripts/wifi_snmp_pp.py``
디렉티브가 이 스크립트를 상주 프로세스로 띄우고 stdin/stdout 라인 프로토콜로
``.1.3.6.1.4.1.672.65``(CONTEC FXE3000) 서브트리 전체를 위임한다.

A안(``wifi_snmp.py``, NET-SNMP-EXTEND-MIB ``.8072`` 아래 9지표)과 달리, 같은
WiFi 지표를 **CONTEC 벤더 OID 트리**로 노출한다(매핑 문서 §10.4 전환 방향).
A안과 독립적으로 공존하며 데이터 출처는 동일하게 cantops 로거의 link.json 이다.

Phase1 객체 (mapping.md §9.2 🟢, opcd 게터 재사용 — 글루만):
  환경(7)     : FirmwareVersion .2.2 / HardwareVersion .2.3 / EthernetAddress .2.4 /
                WLMacAddress .2.5 / IPAddress .2.6 / SubnetMask .2.7 / DefaultGateway .2.8
  인터페이스(3): IfIndex .3.2.1.1 / IfLinkStatus .3.2.1.7 (유선=인스턴스 .1, 무선=.2)
  무선정보(8) : UnitType .3.3.1.3 / WIFInfoWLMacAddress .3.3.1.4 / BandWidth .3.3.1.5 /
                StaLoginState .3.3.1.11.1 / StaApMacAddress .3.3.1.11.2 /
                StaEssId .3.3.1.11.3 / StaChannel .3.3.1.11.4 / StaRssi .3.3.1.11.7

구현 규약 / 한계 (MIB 미정의 → 본 구현에서 확정, 사양 확정 시 정정 필요):
  * IfTable(.3.2.1)은 INDEX 절 없는 degenerate pseudo-table → 유선=인스턴스 .1,
    무선=.2 로 노출. 그 외 그룹은 모두 스칼라 → GET 시 ``.0`` 인스턴스 접미사.
  * MacAddress 는 MIB 상 OCTET STRING(SIZE 6) binary 이나, pass_persist 표준 타입에
    binary octet 토큰이 없어 ``string`` + 콜론 hex("aa:bb:cc:dd:ee:ff")로 노출한다.
    정확한 6옥텟 binary 가 필요하면 AgentX 서브에이전트 전환이 필요하다.
  * RSSI 는 dBm 음수값 그대로(INTEGER). 미연결/결측은 0.
  * Sta* (.11.x) 는 FXE3000(.672.65) 전용 트리. 본 제품은 STA 라 정합.

읽기 전용(SET → not-writable). 데이터 결측/미연결 시에도 프로토콜을 깨지 않고
안전한 기본값(빈 문자열 / 0 / 00:00:00:00:00:00 / 0.0.0.0)을 반환한다.

오버라이드 환경변수(테스트/다중 IF 용):
    WIFI_SNMP_ETH_JSON   eth0 link.json 경로 직접 지정
    WIFI_SNMP_MLAN_JSON  mlan0 link.json 경로 직접 지정
    WIFI_SNMP_DEVINFO    device_info.json 경로 직접 지정
"""

import json
import os
import re
import subprocess
import sys

# 등록 서브트리 루트 = CONTEC(672) → fxe3000(65)
ENTERPRISES = ".1.3.6.1.4.1"
CONTEC = ENTERPRISES + ".672"
FXE3000 = CONTEC + ".65"

DEFAULT_ETH_JSON = "/var/log/cantops/json/eth0/link.json"
DEFAULT_MLAN_JSON = "/var/log/cantops/json/mlan0/link.json"
DEFAULT_DEVINFO = "/usr/local/opc/etc/device_info.json"

NULL_MAC = "00:00:00:00:00:00"
NULL_IP = "0.0.0.0"


# ---- 값 파싱/포맷 헬퍼 -------------------------------------------------------

def _first_int(value):
    """문자열/숫자에서 첫 정수(부호 포함)를 뽑는다. 실패 시 None. (bool 배제)"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    m = re.search(r"-?\d+", str(value))
    return int(m.group()) if m else None


def _fmt_mac(value):
    """MAC 문자열을 콜론 hex 그대로. 결측이면 00:00:00:00:00:00."""
    if value is None:
        return NULL_MAC
    s = str(value).strip()
    return s if s else NULL_MAC


def _fmt_ip(value):
    """점표기 IPv4 문자열 그대로. 결측/null 이면 0.0.0.0."""
    if value is None:
        return NULL_IP
    s = str(value).strip()
    return s if s else NULL_IP


def _eth_field(eth, key):
    """eth0 link.json 필드 접근. 로거는 eth_stats.info.<key> 중첩 기록하나
    opcd 는 flat 검색으로 잡으므로 둘 다 시도(중첩 우선)."""
    eth = eth or {}
    info = (eth.get("eth_stats") or {}).get("info") or {}
    v = info.get(key)
    if v not in (None, ""):
        return v
    return eth.get(key)


# ---- I/O (데이터 소스 로드) -------------------------------------------------

def load_json(path):
    """JSON 파일을 dict 로 읽는다. 부재/파싱오류/연결끊김({})이면 빈 dict."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


_fw_cache = None


def get_fw_version():
    """dpkg-query 로 wlan-proc 버전. 프로세스 생애 1회 캐시(자주 안 변함)."""
    global _fw_cache
    if _fw_cache is not None:
        return _fw_cache
    try:
        proc = subprocess.run(
            ["dpkg-query", "-W", "-f=${Version}", "wlan-proc"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=2, check=False,
        )
        _fw_cache = proc.stdout.decode("utf-8", "replace").strip() if proc.returncode == 0 else ""
    except Exception:
        _fw_cache = ""
    return _fw_cache


def get_eth_carrier():
    """유선 링크 up 여부 = /sys/class/net/eth0/carrier. 못 읽으면 None(폴백 유도)."""
    try:
        with open("/sys/class/net/eth0/carrier", "r") as f:
            return f.read().strip() == "1"
    except OSError:
        return None


def collect_sources():
    """런타임 데이터 소스를 모아 build_oid_map 인자 dict 로 반환."""
    return dict(
        eth=load_json(os.environ.get("WIFI_SNMP_ETH_JSON", DEFAULT_ETH_JSON)),
        mlan=load_json(os.environ.get("WIFI_SNMP_MLAN_JSON", DEFAULT_MLAN_JSON)),
        devinfo=load_json(os.environ.get("WIFI_SNMP_DEVINFO", DEFAULT_DEVINFO)),
        fw=get_fw_version(),
        eth_link_up=get_eth_carrier(),
    )


# ---- OID 맵 구축 (순수 함수 — 테스트 주입 용이) ------------------------------

def build_oid_map(eth=None, mlan=None, devinfo=None, fw=None, eth_link_up=None):
    """주입된 데이터 소스로 {full_oid_instance: (type_token, value_str)} 를 만든다.

    모든 값은 pass_persist stdout 한 줄로 출력되므로 문자열로 정규화한다.
    """
    eth = eth or {}
    mlan = mlan or {}
    devinfo = devinfo or {}

    info = mlan.get("info") or {}
    link = mlan.get("link") or {}
    # 연결 판정: link.address(=AP MAC) 존재 여부 (별도 associated 필드 없음).
    associated = bool(link.get("address"))

    # 유선 링크: sysfs carrier 우선, 없으면 eth0 link.json 의 phy.link 폴백.
    # 로거(wifi_logger_link.py parse_eth_phy)는 phy.link 를 "up"/"down" 문자열로
    # 기록하므로 정수 파싱이 아니라 문자열 비교로 판정한다(정수형도 호환 허용).
    if eth_link_up is None:
        phy = (eth.get("eth_stats") or {}).get("phy") or {}
        eth_up = str(phy.get("link")).strip().lower() in ("up", "1", "yes", "true")
    else:
        eth_up = bool(eth_link_up)

    wl_mac = info.get("address")          # 무선 IF 자기 MAC
    bssid = link.get("address")           # 소속 AP MAC
    ssid = info.get("ssid") or ""
    bandwidth = info.get("width") or ""
    ch = _first_int(info.get("channel"))
    channel = ch if ch is not None else 0
    sig = _first_int(link.get("signal_avg") or link.get("signal"))
    rssi = sig if sig is not None else 0

    om = {}

    def put(suboid, typ, val):
        om[FXE3000 + suboid] = (typ, str(val))

    # 환경 (.2.x 스칼라 → .0)
    put(".2.2.0", "string", fw or "")
    put(".2.3.0", "string", devinfo.get("hardware_version") or "")
    put(".2.4.0", "string", _fmt_mac(_eth_field(eth, "mac_address")))
    put(".2.5.0", "string", _fmt_mac(wl_mac))
    put(".2.6.0", "ipaddress", _fmt_ip(_eth_field(eth, "ip_address")))
    put(".2.7.0", "ipaddress", _fmt_ip(_eth_field(eth, "netmask")))
    put(".2.8.0", "ipaddress", _fmt_ip(_eth_field(eth, "gateway")))

    # 인터페이스 (.3.2.1 pseudo-table: 유선=인스턴스 1, 무선=2)
    put(".3.2.1.1.1", "integer", 1)            # IfIndex 유선
    put(".3.2.1.1.2", "integer", 2)            # IfIndex 무선
    put(".3.2.1.7.1", "integer", 1 if eth_up else 2)        # 유선 up(1)/down(2)
    put(".3.2.1.7.2", "integer", 1 if associated else 2)    # 무선 up(1)/down(2)

    # 무선정보 (.3.3.1.x 스칼라 → .0)
    put(".3.3.1.3.0", "string", "Station")     # UnitType 고정
    put(".3.3.1.4.0", "string", _fmt_mac(wl_mac))
    put(".3.3.1.5.0", "string", bandwidth)
    put(".3.3.1.11.1.0", "integer", 2 if associated else 1)  # StaLoginState
    put(".3.3.1.11.2.0", "string", _fmt_mac(bssid))
    put(".3.3.1.11.3.0", "string", ssid)
    put(".3.3.1.11.4.0", "integer", channel)
    put(".3.3.1.11.7.0", "integer", rssi)

    return om


# ---- pass_persist 프로토콜 ---------------------------------------------------

def oid_key(oid):
    """OID 를 수치 정렬 키(int 리스트)로. 문자열 정렬 금지(.1.10 > .1.9)."""
    return [int(x) for x in oid.strip(".").split(".") if x != ""]


def do_get(oid, omap):
    """GET → [oid, type, value] 또는 [\"NONE\"]."""
    if oid in omap:
        typ, val = omap[oid]
        return [oid, typ, val]
    return ["NONE"]


def do_getnext(oid, sorted_oids, omap):
    """GETNEXT → 요청 OID 보다 수치상 큰 첫 OID 의 [oid, type, value], 없으면 [\"NONE\"]."""
    req = oid_key(oid)
    for noid in sorted_oids:
        if oid_key(noid) > req:
            typ, val = omap[noid]
            return [noid, typ, val]
    return ["NONE"]


def _out(line):
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def main():
    while True:
        try:
            line = sys.stdin.readline()
            if not line:                       # EOF
                break
            cmd = line.strip()
            if cmd == "PING":                  # 매 요청 직전 발생
                _out("PONG")
                continue
            if cmd == "":                      # 빈 줄 = 종료 신호
                break
            if cmd in ("get", "getnext"):
                oid = sys.stdin.readline().strip()
                omap = build_oid_map(**collect_sources())
                if cmd == "get":
                    result = do_get(oid, omap)
                else:
                    result = do_getnext(oid, sorted(omap, key=oid_key), omap)
                for r in result:
                    _out(r)
            elif cmd == "set":
                sys.stdin.readline()           # OID 줄 소비
                sys.stdin.readline()           # TYPE VALUE 줄 소비
                _out("not-writable")           # 읽기 전용
            # 그 외(DUMP 등) 알 수 없는 명령은 무시
        except Exception:
            # 어떤 예외도 프로세스를 죽이지 않게 — snmpd 가 재시작/타임아웃 하지 않도록
            _out("NONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
