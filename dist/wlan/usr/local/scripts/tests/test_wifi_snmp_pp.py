"""wifi_snmp_pp.py (pass_persist / CONTEC .672.65 Phase1) 단위 테스트.

데이터 소스(eth0/mlan0 link.json, device_info, fw, 유선링크)를 build_oid_map 에
직접 주입해 OID→값 매핑, GET/GETNEXT 프로토콜, 결측/미연결 방어를 검증한다.
실기기 snmpd/snmpwalk 없이 호스트에서 순수 함수로 검증 가능한 범위.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import wifi_snmp_pp as pp  # noqa: E402

BASE = pp.FXE3000  # .1.3.6.1.4.1.672.65

# --- 픽스처 -----------------------------------------------------------------

ETH = {
    "eth_stats": {
        "info": {
            "mac_address": "de:ad:be:ef:00:11",
            "ip_address": "192.168.1.50",
            "netmask": "255.255.255.0",
            "gateway": "192.168.1.1",
        },
        "phy": {"link": "up"},   # 로거 parse_eth_phy 는 "up"/"down" 문자열로 기록
    }
}

MLAN_CONN = {
    "info": {
        "address": "aa:bb:cc:dd:ee:01",   # 자기(무선 IF) MAC
        "ssid": "Wlan_5g",
        "channel": 48,
        "freq": 5240,
        "width": "80 MHz",
    },
    "link": {
        "address": "00:00:91:08:61:4b",   # AP MAC = BSSID
        "signal_avg": "-43 dBm",
        "tx_bitrate": "143.3 MBit/s HE-MCS 11 HE-NSS 1",
    },
}

DEV = {"hardware_version": "HW-1.0.0"}


def _connected_map():
    return pp.build_oid_map(eth=ETH, mlan=MLAN_CONN, devinfo=DEV,
                            fw="0.4.0", eth_link_up=True)


def _disconnected_map():
    # mlan0 미연결(빈 dict) + 유선 down
    return pp.build_oid_map(eth=ETH, mlan={}, devinfo=DEV,
                            fw="", eth_link_up=False)


# --- OID 수치 정렬 ----------------------------------------------------------

def test_oid_key_numeric_order():
    # 문자열 정렬이면 ".1.10" < ".1.9" 로 깨짐 — 수치 정렬 확인.
    assert pp.oid_key(".1.9") < pp.oid_key(".1.10")
    assert pp.oid_key(BASE + ".3.2.1.1.2") < pp.oid_key(BASE + ".3.2.1.7.1")
    assert pp.oid_key(BASE + ".3.3.1.11.4.0") < pp.oid_key(BASE + ".3.3.1.11.7.0")


# --- 환경 7 지표 ------------------------------------------------------------

def test_env_values_connected():
    om = _connected_map()
    assert om[BASE + ".2.2.0"] == ("string", "0.4.0")              # FirmwareVersion
    assert om[BASE + ".2.3.0"] == ("string", "HW-1.0.0")          # HardwareVersion
    assert om[BASE + ".2.4.0"] == ("string", "de:ad:be:ef:00:11")  # EthernetAddress
    assert om[BASE + ".2.5.0"] == ("string", "aa:bb:cc:dd:ee:01")  # WLMacAddress
    assert om[BASE + ".2.6.0"] == ("ipaddress", "192.168.1.50")    # IPAddress
    assert om[BASE + ".2.7.0"] == ("ipaddress", "255.255.255.0")   # SubnetMask
    assert om[BASE + ".2.8.0"] == ("ipaddress", "192.168.1.1")     # DefaultGateway


def test_env_eth_flat_fallback():
    # 로거가 flat top-level 로 기록한 경우에도 잡혀야 함.
    eth_flat = {"mac_address": "11:22:33:44:55:66", "ip_address": "10.0.0.2",
                "netmask": "255.0.0.0", "gateway": "10.0.0.1"}
    om = pp.build_oid_map(eth=eth_flat, mlan=MLAN_CONN, devinfo=DEV,
                          fw="x", eth_link_up=True)
    assert om[BASE + ".2.4.0"] == ("string", "11:22:33:44:55:66")
    assert om[BASE + ".2.6.0"] == ("ipaddress", "10.0.0.2")


def test_env_missing_defaults():
    om = pp.build_oid_map(eth={}, mlan={}, devinfo={}, fw="", eth_link_up=False)
    assert om[BASE + ".2.2.0"] == ("string", "")                  # fw 없음
    assert om[BASE + ".2.3.0"] == ("string", "")                  # hw 없음
    assert om[BASE + ".2.4.0"] == ("string", pp.NULL_MAC)         # eth MAC 없음
    assert om[BASE + ".2.6.0"] == ("ipaddress", pp.NULL_IP)       # IP 없음


# --- 인터페이스 (pseudo-table 인스턴스) --------------------------------------

def test_interface_indices_and_status():
    om = _connected_map()
    assert om[BASE + ".3.2.1.1.1"] == ("integer", "1")   # IfIndex 유선
    assert om[BASE + ".3.2.1.1.2"] == ("integer", "2")   # IfIndex 무선
    assert om[BASE + ".3.2.1.7.1"] == ("integer", "1")   # 유선 up
    assert om[BASE + ".3.2.1.7.2"] == ("integer", "1")   # 무선 up (associated)


def test_interface_status_down():
    om = _disconnected_map()
    assert om[BASE + ".3.2.1.7.1"] == ("integer", "2")   # 유선 down
    assert om[BASE + ".3.2.1.7.2"] == ("integer", "2")   # 무선 down


def test_eth_link_fallback_from_json_phy():
    # eth_link_up=None → eth_stats.phy.link("up") 폴백. 로거 실제 출력 형식과 일치.
    om = pp.build_oid_map(eth=ETH, mlan=MLAN_CONN, devinfo=DEV,
                          fw="x", eth_link_up=None)
    assert om[BASE + ".3.2.1.7.1"] == ("integer", "1")   # phy.link="up" → up(1)


def test_eth_link_fallback_down_from_json_phy():
    # phy.link="down"(로거 실제 출력) → down(2). 정수 픽스처가 가렸던 회귀 방지.
    eth_down = {"eth_stats": {"info": ETH["eth_stats"]["info"],
                              "phy": {"link": "down"}}}
    om = pp.build_oid_map(eth=eth_down, mlan=MLAN_CONN, devinfo=DEV,
                          fw="x", eth_link_up=None)
    assert om[BASE + ".3.2.1.7.1"] == ("integer", "2")   # phy.link="down" → down(2)


# --- 무선정보 8 지표 (연결/미연결) -------------------------------------------

def test_wireless_info_connected():
    om = _connected_map()
    assert om[BASE + ".3.3.1.3.0"] == ("string", "Station")           # UnitType
    assert om[BASE + ".3.3.1.4.0"] == ("string", "aa:bb:cc:dd:ee:01")  # WIFInfoWLMacAddress
    assert om[BASE + ".3.3.1.5.0"] == ("string", "80 MHz")           # BandWidth
    assert om[BASE + ".3.3.1.11.1.0"] == ("integer", "2")            # StaLoginState connected
    assert om[BASE + ".3.3.1.11.2.0"] == ("string", "00:00:91:08:61:4b")  # BSSID
    assert om[BASE + ".3.3.1.11.3.0"] == ("string", "Wlan_5g")       # StaEssId
    assert om[BASE + ".3.3.1.11.4.0"] == ("integer", "48")           # StaChannel
    assert om[BASE + ".3.3.1.11.7.0"] == ("integer", "-43")          # StaRssi (dBm)


def test_wireless_info_disconnected():
    om = _disconnected_map()
    assert om[BASE + ".3.3.1.3.0"] == ("string", "Station")          # 고정
    assert om[BASE + ".3.3.1.11.1.0"] == ("integer", "1")           # notConnected
    assert om[BASE + ".3.3.1.11.2.0"] == ("string", pp.NULL_MAC)    # BSSID 0
    assert om[BASE + ".3.3.1.11.3.0"] == ("string", "")             # ESSID 빈값
    assert om[BASE + ".3.3.1.11.4.0"] == ("integer", "0")           # Channel 0
    assert om[BASE + ".3.3.1.11.7.0"] == ("integer", "0")           # RSSI 0


# --- 맵 크기 / GET / GETNEXT -----------------------------------------------

def test_map_has_19_instances():
    # 객체 18개 = 환경7 + 인터페이스3 + 무선8, 인스턴스로는 IfIndex/IfLinkStatus 각 2개
    # → 7 + (2+2) + 8 = 19 인스턴스.
    om = _connected_map()
    assert len(om) == 19


def test_do_get_hit_and_miss():
    om = _connected_map()
    assert pp.do_get(BASE + ".2.2.0", om) == [BASE + ".2.2.0", "string", "0.4.0"]
    # 인스턴스 접미사 없는 객체 OID 는 GET 미스
    assert pp.do_get(BASE + ".2.2", om) == ["NONE"]
    # 존재하지 않는 OID
    assert pp.do_get(BASE + ".99.0", om) == ["NONE"]


def test_getnext_full_walk_covers_all_in_order():
    om = _connected_map()
    sorted_oids = sorted(om, key=pp.oid_key)
    # 서브트리 루트부터 walk
    cur = BASE
    walked = []
    for _ in range(len(om) + 5):  # 무한루프 방지 상한
        res = pp.do_getnext(cur, sorted_oids, om)
        if res == ["NONE"]:
            break
        walked.append(res[0])
        cur = res[0]
    assert walked == sorted_oids          # 전부, 수치 오름차순으로
    assert len(walked) == 19


def test_getnext_last_oid_returns_none():
    om = _connected_map()
    sorted_oids = sorted(om, key=pp.oid_key)
    last = sorted_oids[-1]
    assert pp.do_getnext(last, sorted_oids, om) == ["NONE"]


def test_getnext_from_instance_returns_following():
    om = _connected_map()
    sorted_oids = sorted(om, key=pp.oid_key)
    res = pp.do_getnext(BASE + ".3.2.1.1.2", sorted_oids, om)
    assert res[0] == BASE + ".3.2.1.7.1"   # 다음 수치 OID


def test_all_getnext_responses_within_subtree():
    om = _connected_map()
    sorted_oids = sorted(om, key=pp.oid_key)
    for oid in sorted_oids:
        res = pp.do_getnext(oid, sorted_oids, om)
        if res != ["NONE"]:
            assert res[0].startswith(BASE + ".")
