#!/usr/bin/env python3
"""wifi_snmp_pp.py — net-snmp ``pass_persist`` 백엔드 (CONTEC FXE3000 Private MIB).

B안 Phase1 구현. snmpd.conf 의
``pass_persist .1.3.6.1.4.1.672.65 /usr/bin/python3 -u /usr/local/logger/wifi_snmp_pp.py``
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

Phase2a 객체 (mapping.md §9.3 🟡 일부, 파싱/유도 — link.json 재사용):
  LED(3)      : LedPower .3.1.1(상수 on) / LedLan .3.1.2(유선 carrier) /
                LedWlan .3.1.3(무선 연결) — on(1)/off(2), 물리 LED 판독 아닌 유도값
  무선정보(5) : WirelessMode .3.3.1.1(tx_bitrate 프리픽스→11n/ac/ax/be) /
                WLM .3.3.1.2(managed 고정) / StaTxRate .3.3.1.11.5(Mbps) /
                StaRxRate .3.3.1.11.6(Mbps) / SupplicantState .3.3.1.11.8
  SupplicantState(.11.8) 는 wifi_logger_link.py 가 매 주기 wpa_cli 폴링해 기록하는
  별도 파일 supplicant.json({wpa_state, temp_disabled})에서 invalid(1)/success(2)/
  failure(3)/authenticating(4) 로 매핑. 부재 시 associated 근사(2/1).

Phase2b 객체 (mapping.md §6, mlan0 무선통계 — Counter32):
  통계(9)     : TxUnicast .3.3.2.1(=dot11Transmitted-Group) / TxMulticast .2(=GroupTrans) /
                TxUniOctets .3(~tx_bytes 근사) / TxShortRetries .5(=dot11RetryCount) /
                TxLongRetries .6(=dot11MultipleRetryCount) / RxUnicast .8(=RecvFrag-GroupRecv) /
                RxMulticast .9(=GroupRecv) / RxUniOctets .10(~rx_bytes 근사) /
                RxHwFCSErrors .13(=dot11FCSErrorCount)
  소스 = mlan link.json 의 mwlan_log(로거 parse_mwlan_log 가 /proc getlog 를 양포맷 파싱).
  측정 불가 시 noSuchInstance(omap 누락) — Counter 0 은 '리셋' 오인이라 미노출(Phase2a
  always-present 의 의도적 예외). 0고정 4개(.4/.7/.11/.12)는 소스 영구 부재라 미노출.
  multicast 카운터 키는 dot11Multicast*(README_MLAN) 우선, dot11Group* fallback(펌웨어차).
  octet(.3/.10)은 mwlan_log 가 아니라 link.tx/rx_bytes 라 /proc 실패 시에도 노출(비대칭).
  caveat: short/long retry 는 802.11 정의와 1:1 불일치 / octet 은 mcast 포함 근사 +
  재연결 시 0 리셋되어 Counter32 가짜 wrap 가능(NMS 델타 필터 필요) / TxUnicast(.1)·
  RxUnicast(.8)은 frame·fragment 기반(관리프레임·단편 포함)이라 순수 유니캐스트 대비 과대계상.

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
    WIFI_SNMP_SUPP_JSON  supplicant.json 경로 직접 지정 (Phase2a)
"""

import json
import os
import re
import subprocess
import sys
import time
import traceback

# 등록 서브트리 루트 = CONTEC(672) → fxe3000(65)
ENTERPRISES = ".1.3.6.1.4.1"
CONTEC = ENTERPRISES + ".672"
FXE3000 = CONTEC + ".65"

DEFAULT_ETH_JSON = "/var/log/cantops/json/eth0/link.json"
DEFAULT_MLAN_JSON = "/var/log/cantops/json/mlan0/link.json"
DEFAULT_DEVINFO = "/usr/local/opc/etc/device_info.json"
# Phase2a: wifi_logger_link.py 가 매 주기 기록하는 supplicant 상태({wpa_state,
# temp_disabled}). link.json 의 미연결 '{}' 계약(opcd/passive_roam 의존)을
# 건드리지 않으려 별도 파일로 둔다.
DEFAULT_SUPP_JSON = "/var/log/cantops/json/mlan0/supplicant.json"

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


# ---- Phase2a 파생 헬퍼 (순수 함수) ------------------------------------------

_RATE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*MBit/s")
# authenticating(4) = 실제 인증/연결 핸드셰이크 단계만. SCANNING/DISCONNECTED/INACTIVE 는
# 인증 이전(AP 탐색·유휴)이라 의도적으로 제외 → invalid(1)(미접속). SCANNING 을 4 로 올리면
# 백그라운드 스캔마다 authenticating 으로 보여 NMS 노이즈가 된다.
_SUPP_IN_PROGRESS = ("AUTHENTICATING", "ASSOCIATING", "ASSOCIATED",
                     "4WAY_HANDSHAKE", "GROUP_HANDSHAKE")


def _wireless_mode(bitrate):
    """link.tx_bitrate 의 rate_info 프리픽스로 무선규격(11n/ac/ax/be)을 유도한다.
    EHT-/HE-/VHT- 를 plain "MCS"(HT) 보다 먼저 본다 — "HE-MCS" 도 "MCS" 를 포함하므로.
    legacy(11a/b/g)는 bitrate 만으론 구분 불가 → 빈 문자열(미연결도 빈 문자열).
    opcd C 의 parse_bitrate_to_mode 와 동치(프리픽스 우선순위 동일)."""
    if not bitrate:
        return ""
    s = str(bitrate)
    if "EHT-" in s:
        return "11be"
    if "HE-" in s:
        return "11ax"
    if "VHT-" in s:
        return "11ac"
    if "MCS" in s:
        return "11n"
    return ""


def _rate_mbps(bitrate):
    """'143.3 MBit/s ...' → Mbps INTEGER(반올림). 결측/미파싱은 0.
    단위 Mbps 는 A안 extend 백엔드(wifi_snmp.py m_txrate)와 동일 → 두 SNMP 트리
    (.8072 extend / .672.65 CONTEC)가 같은 값을 보고한다. MIB SYNTAX 는 단위 미명시
    bare INTEGER 이고 Mbps 면 INT32 saturate 가 없다(bps 환산은 160MHz/Wi-Fi6E
    ≥2.15Gbps 에서 넘침)."""
    if not bitrate:
        return 0
    m = _RATE_RE.search(str(bitrate))
    if not m:
        return 0
    return int(round(float(m.group(1))))


def _supplicant_state(supplicant, associated):
    """supplicant.json({wpa_state, temp_disabled}) → MIB enum
    invalid(1)/success(2)/failure(3)/authenticating(4).
    COMPLETED=성공(2, temp_disabled 보다 우선). 미접속+temp_disabled(인증 반복실패로
    네트워크 일시비활성)=실패(3). 핸드셰이크/assoc 진행중=인증중(4). 그 외=무효(1).
    supplicant 데이터 부재 시 associated 근사(2/1)."""
    supp = supplicant or {}
    state = supp.get("wpa_state")
    if not state:
        return 2 if associated else 1
    if state == "COMPLETED":
        return 2
    # temp_disabled 를 _SUPP_IN_PROGRESS 보다 먼저 본다. 로거의 두 wpa_cli 호출
    # (status→list_networks) 사이 TOCTOU 로 HANDSHAKE 중에도 temp_disabled=True 일 수
    # 있으나, 그건 인증 반복실패 직후 재시도 첫 사이클이라 failure(3) 로 보는 게 맞고
    # 다음 폴링 주기에 COMPLETED(2) 또는 authenticating(4) 로 갱신된다.
    if supp.get("temp_disabled"):
        return 3
    if state in _SUPP_IN_PROGRESS:
        return 4
    return 1


def _mwlan_counter(mwlan_log, key):
    """mlan link.json 의 mwlan_log dict 에서 dot11 카운터를 정수로 반환. 공백구분 다중값
    (QoS AC별 리스트)은 합산, 스칼라는 _first_int, 부재/비숫자는 None.
    (A안 wifi_snmp.py _mwlan_counter 이식 — mwlan_log 만 의존하는 순수 함수.)"""
    if not isinstance(mwlan_log, dict):
        return None
    v = mwlan_log.get(key)
    if isinstance(v, list):
        nums = [x for x in v if type(x) is int]   # bool 배제(type is int)
        return sum(nums) if nums else None
    return _first_int(v)


def _mwlan_counter_any(mwlan_log, *keys):
    """여러 후보 키를 순서대로 시도해 첫 non-None 카운터 반환. multicast frame count 의
    getlog 명칭이 펌웨어별로 dot11Multicast*(README_MLAN) / dot11Group* 로 갈려 흡수."""
    for k in keys:
        v = _mwlan_counter(mwlan_log, k)
        if v is not None:
            return v
    return None


def _counter_out(value):
    """SNMP Counter32 출력 문자열: 음수 유도값은 0 클램프, 64bit(byte 카운터)는 하위 32bit."""
    return str(max(0, int(value)) & 0xFFFFFFFF)


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


# snmpwalk 한 번은 OID 수만큼 연속 GETNEXT 를 보낸다(Phase1=19회). 매 요청마다
# 파일 3개 + carrier 를 다시 읽으면 부하가 누적되고, walk 도중 link.json 이 갱신되면
# OID 간 값이 불일치한다. 짧은 TTL 캐시로 한 walk 가 일관된 스냅샷을 보게 한다.
_SOURCE_TTL = 0.5  # 초
_source_cache = {"at": None, "data": None}


def collect_sources():
    """런타임 데이터 소스를 모아 build_oid_map 인자 dict 로 반환. _SOURCE_TTL 초 캐시."""
    now = time.monotonic()
    at = _source_cache["at"]
    if at is not None and _source_cache["data"] is not None and (now - at) < _SOURCE_TTL:
        return _source_cache["data"]
    data = dict(
        eth=load_json(os.environ.get("WIFI_SNMP_ETH_JSON", DEFAULT_ETH_JSON)),
        mlan=load_json(os.environ.get("WIFI_SNMP_MLAN_JSON", DEFAULT_MLAN_JSON)),
        devinfo=load_json(os.environ.get("WIFI_SNMP_DEVINFO", DEFAULT_DEVINFO)),
        supplicant=load_json(os.environ.get("WIFI_SNMP_SUPP_JSON", DEFAULT_SUPP_JSON)),
        fw=get_fw_version(),
        eth_link_up=get_eth_carrier(),
    )
    _source_cache["at"] = now
    _source_cache["data"] = data
    return data


# ---- OID 맵 구축 (순수 함수 — 테스트 주입 용이) ------------------------------

def build_oid_map(eth=None, mlan=None, devinfo=None, fw=None, eth_link_up=None,
                  supplicant=None):
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
    tx_bitrate = link.get("tx_bitrate")   # 'NNN.N MBit/s <rate_info>' 또는 결측
    rx_bitrate = link.get("rx_bitrate")

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

    # LED (.3.1.x StatusInfo 스칼라 → .0) — 물리 LED 판독이 아니라 링크/연결상태 유도.
    # on(1)/off(2). Power 는 'SNMP 응답=전원ON' 이라 상수 on.
    put(".3.1.1.0", "integer", 1)                           # LedPower 상수 on
    put(".3.1.2.0", "integer", 1 if eth_up else 2)          # LedLan = 유선 carrier
    put(".3.1.3.0", "integer", 1 if associated else 2)      # LedWlan = 무선 연결

    # 인터페이스 (.3.2.1 pseudo-table: 유선=인스턴스 1, 무선=2)
    put(".3.2.1.1.1", "integer", 1)            # IfIndex 유선
    put(".3.2.1.1.2", "integer", 2)            # IfIndex 무선
    put(".3.2.1.7.1", "integer", 1 if eth_up else 2)        # 유선 up(1)/down(2)
    put(".3.2.1.7.2", "integer", 1 if associated else 2)    # 무선 up(1)/down(2)

    # 무선정보 (.3.3.1.x 스칼라 → .0)
    put(".3.3.1.1.0", "string", _wireless_mode(tx_bitrate))  # WirelessMode(규격)
    put(".3.3.1.2.0", "string", "managed")     # WLM(Link Mode) STA 고정(사양 확정 대기)
    put(".3.3.1.3.0", "string", "Station")     # UnitType 고정
    put(".3.3.1.4.0", "string", _fmt_mac(wl_mac))
    put(".3.3.1.5.0", "string", bandwidth)
    put(".3.3.1.11.1.0", "integer", 2 if associated else 1)  # StaLoginState
    put(".3.3.1.11.2.0", "string", _fmt_mac(bssid))
    put(".3.3.1.11.3.0", "string", ssid)
    put(".3.3.1.11.4.0", "integer", channel)
    put(".3.3.1.11.5.0", "integer", _rate_mbps(tx_bitrate))   # StaTxRate (Mbps)
    put(".3.3.1.11.6.0", "integer", _rate_mbps(rx_bitrate))   # StaRxRate (Mbps)
    put(".3.3.1.11.7.0", "integer", rssi)
    put(".3.3.1.11.8.0", "integer", _supplicant_state(supplicant, associated))  # SupplicantState

    # 무선통계 (.3.3.2.x Counter32 스칼라 → .0). 소스 = mlan link.json 의 mwlan_log
    # (로거 parse_mwlan_log 가 /proc getlog 파싱). 측정 가능할 때만 put → 결측은
    # noSuchInstance(omap 누락): Counter 의미상 0 반환은 매니저에 '리셋'으로 오인되므로.
    # 0고정 4개(.4 TxMultiOctets/.7 TxFifo/.11 RxMultiOctets/.12 RxFifo)는 소스 영구
    # 부재(§6 ❌불가)라 노출 안 함. 유도식 음수는 max(0,·), 출력은 32bit wrap(_counter_out).
    # caveat: .5/.6 retry 는 dot11Retry/MultipleRetryCount 로 802.11 short/long 정의와
    # 1:1 불일치, .3/.10 octet 은 tx/rx_bytes(멀티캐스트 포함) 근사, .1/.8 unicast 는
    # frame/fragment 차감(관리프레임·단편 포함)이라 순수 유니캐스트 패킷수 대비 과대계상 가능.
    mw = mlan.get("mwlan_log") or {}

    def put_counter(suboid, val):
        # None=소스 부재 → noSuchInstance(omit). _counter_out 가 음수 클램프+32bit wrap 단일 담당.
        if val is not None:
            put(suboid, "counter", _counter_out(val))

    # multicast frame count 는 getlog 명칭이 dot11Multicast*(README_MLAN) 또는 dot11Group* 라
    # 둘 다 시도. unicast(.1/.8)는 전체-멀티캐스트 차감, 음수는 _counter_out 가 0 클램프.
    mc_tx = _mwlan_counter_any(mw, "dot11MulticastTransmittedFrameCount", "dot11GroupTransmittedFrameCount")
    mc_rx = _mwlan_counter_any(mw, "dot11MulticastReceivedFrameCount", "dot11GroupReceivedFrameCount")
    tx_all = _mwlan_counter(mw, "dot11TransmittedFrameCount")
    rx_frag = _mwlan_counter(mw, "dot11ReceivedFragmentCount")

    def _diff(total, group):
        return None if (total is None or group is None) else total - group

    put_counter(".3.3.2.1.0", _diff(tx_all, mc_tx))                            # TxUnicast
    put_counter(".3.3.2.2.0", mc_tx)                                           # TxMulticast
    # .3/.10 octet 근사: iw station dump tx/rx_bytes 는 재연결 시 0 리셋 → Counter32 감소를
    # 매니저가 가짜 wrap 으로 오인할 수 있음(NMS last-seen/델타 이상값 필터 필요).
    put_counter(".3.3.2.3.0", _first_int(link.get("tx_bytes")))               # TxUniOctets(근사)
    put_counter(".3.3.2.5.0", _mwlan_counter(mw, "dot11RetryCount"))          # TxShortRetries(≈)
    put_counter(".3.3.2.6.0", _mwlan_counter(mw, "dot11MultipleRetryCount"))  # TxLongRetries(≈)
    put_counter(".3.3.2.8.0", _diff(rx_frag, mc_rx))                          # RxUnicast
    put_counter(".3.3.2.9.0", mc_rx)                                          # RxMulticast
    put_counter(".3.3.2.10.0", _first_int(link.get("rx_bytes")))             # RxUniOctets(근사)
    put_counter(".3.3.2.13.0", _mwlan_counter(mw, "dot11FCSErrorCount"))      # RxHwFCSErrors

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
            # 어떤 예외도 프로세스를 죽이지 않게 — snmpd 가 재시작/타임아웃 하지 않도록.
            # 단 진단을 위해 stderr 로 traceback 출력(snmpd 가 child stderr 를 syslog 로 보냄).
            traceback.print_exc(file=sys.stderr)
            _out("NONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
