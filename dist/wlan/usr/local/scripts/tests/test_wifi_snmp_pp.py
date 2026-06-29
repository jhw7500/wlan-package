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
        "rx_bitrate": "120.0 MBit/s HE-MCS 9 HE-NSS 1",
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

def test_map_has_27_instances():
    # Phase1 19 + Phase2a 8(LED3 + WirelessMode + WLM + StaTxRate + StaRxRate +
    # SupplicantState) = 27 인스턴스.
    om = _connected_map()
    assert len(om) == 27, (
        "기대 27 인스턴스, 실제 %d. Phase2 OID 추가 시 이 기대값 갱신 필요: %s"
        % (len(om), sorted(om, key=pp.oid_key))
    )


def test_collect_sources_ttl_cache():
    # snmpwalk 연속 GETNEXT 가 일관된 스냅샷을 보도록 _SOURCE_TTL 초 캐시.
    # 전역 _source_cache 는 finally 로 원복한다(다른 테스트 오염 방지 — Claude 리뷰).
    saved = dict(pp._source_cache)
    try:
        pp._source_cache["at"] = None
        pp._source_cache["data"] = None
        d1 = pp.collect_sources()
        d2 = pp.collect_sources()
        assert d1 is d2                      # TTL 내: 캐시 hit(동일 객체)
        # TTL 만료(캐시 미스) → 갱신 경로: 새 객체 반환
        pp._source_cache["at"] = pp._source_cache["at"] - pp._SOURCE_TTL - 1.0
        d3 = pp.collect_sources()
        assert d3 is not d1
    finally:
        pp._source_cache.clear()
        pp._source_cache.update(saved)


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
    assert len(walked) == 27


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


# ============================ Phase2a ============================
# LED 유도(3) + WLM(1) + WirelessMode(1) + StaTxRate/RxRate(2) +
# SupplicantState(1) = 8 객체. 모두 link.json/상수 재사용(신규 데이터소스는
# SupplicantState 의 별도 supplicant.json 한 건뿐, 미존재 시 associated 근사).

# --- WirelessMode 파생 (link.tx_bitrate rate_info 프리픽스) ------------------
# 프리픽스 우선순위: EHT-/HE-/VHT- 를 plain "MCS"(HT) 보다 먼저 판정해야 한다
# ("HE-MCS" 도 "MCS" 를 포함하므로). legacy(11a/b/g)는 bitrate 만으론 구분 불가.

def test_wireless_mode_he():
    assert pp._wireless_mode("143.3 MBit/s HE-MCS 11 HE-NSS 1") == "11ax"


def test_wireless_mode_vht():
    assert pp._wireless_mode("390.0 MBit/s VHT-MCS 9 80MHz VHT-NSS 2") == "11ac"


def test_wireless_mode_ht():
    assert pp._wireless_mode("144.4 MBit/s MCS 15 short GI") == "11n"


def test_wireless_mode_eht():
    assert pp._wireless_mode("1200 MBit/s EHT-MCS 13 EHT-NSS 2") == "11be"


def test_wireless_mode_legacy_or_missing():
    assert pp._wireless_mode("54.0 MBit/s") == ""   # legacy a/b/g 구분 불가
    assert pp._wireless_mode("") == ""
    assert pp._wireless_mode(None) == ""


# --- rate 문자열 → Mbps INTEGER (A안 m_txrate 와 동일 단위, 반올림) ---------

def test_rate_mbps_basic():
    assert pp._rate_mbps("143.3 MBit/s HE-MCS 11 HE-NSS 1") == 143


def test_rate_mbps_missing():
    assert pp._rate_mbps("") == 0
    assert pp._rate_mbps(None) == 0


def test_rate_mbps_rounds():
    # float 절삭이 아니라 반올림(143.7 → 144)
    assert pp._rate_mbps("143.7 MBit/s HE-MCS 11") == 144


def test_rate_mbps_high_rate_no_saturation():
    # Mbps 는 INT32 클램프 없이 고속(Wi-Fi6E 160MHz ~2402Mbps)도 그대로
    assert pp._rate_mbps("2402.0 MBit/s EHT-MCS 13 EHT-NSS 2") == 2402


# --- SupplicantState 매핑 (supplicant dict → invalid1/success2/failure3/auth4) -

def test_supplicant_state_success():
    assert pp._supplicant_state({"wpa_state": "COMPLETED", "temp_disabled": False}, True) == 2


def test_supplicant_state_failure_temp_disabled():
    # 인증 반복 실패 → wpa_supplicant 가 네트워크 temp-disable. 미접속 + temp_disabled → failure(3)
    assert pp._supplicant_state({"wpa_state": "SCANNING", "temp_disabled": True}, False) == 3


def test_supplicant_state_authenticating():
    for s in ("AUTHENTICATING", "ASSOCIATING", "ASSOCIATED", "4WAY_HANDSHAKE", "GROUP_HANDSHAKE"):
        assert pp._supplicant_state({"wpa_state": s, "temp_disabled": False}, False) == 4


def test_supplicant_state_invalid():
    assert pp._supplicant_state({"wpa_state": "DISCONNECTED", "temp_disabled": False}, False) == 1
    assert pp._supplicant_state({"wpa_state": "INACTIVE", "temp_disabled": False}, False) == 1


def test_supplicant_state_completed_wins_over_temp_disabled():
    # COMPLETED 면 temp_disabled 플래그와 무관하게 success(2) (현재 연결 우선)
    assert pp._supplicant_state({"wpa_state": "COMPLETED", "temp_disabled": True}, True) == 2


def test_supplicant_state_fallback_to_associated():
    # supplicant.json 부재(데이터 없음) → associated 근사(2/1)
    assert pp._supplicant_state(None, True) == 2
    assert pp._supplicant_state({}, False) == 1


# --- LED OID (build_oid_map) -----------------------------------------------

def test_led_connected():
    om = _connected_map()
    assert om[BASE + ".3.1.1.0"] == ("integer", "1")   # Power 상수 on
    assert om[BASE + ".3.1.2.0"] == ("integer", "1")   # LAN: eth up → on
    assert om[BASE + ".3.1.3.0"] == ("integer", "1")   # WLAN: associated → on


def test_led_disconnected():
    om = _disconnected_map()
    assert om[BASE + ".3.1.1.0"] == ("integer", "1")   # Power 여전히 on(응답=전원ON)
    assert om[BASE + ".3.1.2.0"] == ("integer", "2")   # LAN down → off
    assert om[BASE + ".3.1.3.0"] == ("integer", "2")   # WLAN not assoc → off


# --- WLM / WirelessMode / rates / supplicant OID (build_oid_map) ------------

def test_wlm_constant():
    om = _connected_map()
    assert om[BASE + ".3.3.1.2.0"] == ("string", "managed")   # STA 고정(사양 확정 대기)


def test_wireless_mode_oid_connected():
    om = _connected_map()
    assert om[BASE + ".3.3.1.1.0"] == ("string", "11ax")   # HE bitrate → 11ax


def test_wireless_mode_oid_disconnected():
    om = _disconnected_map()
    assert om[BASE + ".3.3.1.1.0"] == ("string", "")        # 미연결 → 빈값


def test_sta_rates_connected():
    om = _connected_map()
    assert om[BASE + ".3.3.1.11.5.0"] == ("integer", "143")   # TxRate Mbps
    assert om[BASE + ".3.3.1.11.6.0"] == ("integer", "120")   # RxRate Mbps


def test_sta_rates_disconnected():
    om = _disconnected_map()
    assert om[BASE + ".3.3.1.11.5.0"] == ("integer", "0")
    assert om[BASE + ".3.3.1.11.6.0"] == ("integer", "0")


def test_supplicant_oid_from_source():
    om = pp.build_oid_map(eth=ETH, mlan=MLAN_CONN, devinfo=DEV, fw="x",
                          eth_link_up=True,
                          supplicant={"wpa_state": "4WAY_HANDSHAKE", "temp_disabled": False})
    assert om[BASE + ".3.3.1.11.8.0"] == ("integer", "4")   # 인증중


def test_supplicant_oid_fallback():
    # supplicant 미주입 → associated 근사
    assert _connected_map()[BASE + ".3.3.1.11.8.0"] == ("integer", "2")
    assert _disconnected_map()[BASE + ".3.3.1.11.8.0"] == ("integer", "1")


# --- main() 라인 프로토콜 (Phase1 미커버 갭 보강) ---------------------------

def _run_main(stdin_lines):
    import io
    pp._source_cache.clear()
    pp._source_cache.update({"at": None, "data": None})
    stdin, stdout = io.StringIO("".join(stdin_lines)), io.StringIO()
    old_in, old_out = sys.stdin, sys.stdout
    sys.stdin, sys.stdout = stdin, stdout
    try:
        pp.main()
    finally:
        sys.stdin, sys.stdout = old_in, old_out
    return stdout.getvalue().splitlines()


def test_main_ping_pong():
    assert _run_main(["PING\n", "\n"])[0] == "PONG"


def test_main_get_constant_oid():
    # LedPower 는 데이터소스와 무관한 상수라 호스트(파일 부재)에서도 항상 존재
    out = _run_main(["get\n", BASE + ".3.1.1.0\n", "\n"])
    assert out == [BASE + ".3.1.1.0", "integer", "1"]


def test_main_get_miss_returns_none():
    out = _run_main(["get\n", BASE + ".99.0\n", "\n"])
    assert out == ["NONE"]


def test_main_getnext_returns_triple():
    out = _run_main(["getnext\n", BASE + "\n", "\n"])
    assert len(out) == 3 and out[0].startswith(BASE + ".")


def test_main_set_not_writable():
    out = _run_main(["set\n", BASE + ".3.1.1.0\n", "integer 5\n", "\n"])
    assert out == ["not-writable"]


def test_main_eof_exits_clean():
    assert _run_main([]) == []


# ============================ Phase2b ============================
# mlan0 무선통계 9개(.3.3.2.x, Counter32). 데이터소스 = link.json 의 mwlan_log
# (로거 parse_mwlan_log 가 /proc getlog 파싱). 측정 불가 시 noSuchInstance(omap 누락).
# 0고정 4개(.4/.7/.11/.12)는 소스 영구 부재 → 미노출.

# mwlan_log 채워진 mlan 픽스처(연결+통계). getlog dot11* 카운터 + link bytes.
MLAN_STATS = {
    "info": dict(MLAN_CONN["info"]),
    "link": dict(MLAN_CONN["link"], tx_bytes="5000000", rx_bytes="9000000"),
    "mwlan_log": {
        "dot11TransmittedFrameCount": 1000,
        "dot11MulticastTransmittedFrameCount": 40,  # 실제 getlog 키(README_MLAN). TxUni=960, TxMcast=40
        "dot11RetryCount": 50,                      # TxShortRetries
        "dot11MultipleRetryCount": 12,             # TxLongRetries
        "dot11ReceivedFragmentCount": 800,
        "dot11MulticastReceivedFrameCount": 30,    # RxUnicast=770, RxMulticast=30
        "dot11FCSErrorCount": 2621,                # RxHwFCSErrors
    },
}


def _stats_map():
    return pp.build_oid_map(eth=ETH, mlan=MLAN_STATS, devinfo=DEV, fw="x", eth_link_up=True)


# --- _mwlan_counter (A안에서 이식한 dot11 카운터 추출) -----------------------

def test_mwlan_counter_int_list_missing():
    mw = {"a": 7, "b": [1, 2, 3], "c": True}
    assert pp._mwlan_counter(mw, "a") == 7
    assert pp._mwlan_counter(mw, "b") == 6        # 리스트(QoS AC)는 합산
    assert pp._mwlan_counter(mw, "c") is None     # bool 배제
    assert pp._mwlan_counter(mw, "z") is None     # 부재
    assert pp._mwlan_counter({}, "a") is None


# --- _counter_out (32bit wrap + 음수 클램프) --------------------------------

def test_counter_out_normal_negative_wrap():
    assert pp._counter_out(960) == "960"
    assert pp._counter_out(-3) == "0"                       # 음수 → 0
    assert pp._counter_out(4294967296 + 5) == "5"          # 2^32 wrap → 하위32bit


# --- 통계 OID (mwlan_log 있을 때) -------------------------------------------

def test_stats_present_with_mwlan_log():
    om = _stats_map()
    assert om[BASE + ".3.3.2.1.0"] == ("counter", "960")    # TxUnicast = Trans-Group
    assert om[BASE + ".3.3.2.2.0"] == ("counter", "40")     # TxMulticast = GroupTrans
    assert om[BASE + ".3.3.2.3.0"] == ("counter", "5000000")  # TxUniOctets ~ tx_bytes
    assert om[BASE + ".3.3.2.5.0"] == ("counter", "50")     # TxShortRetries = RetryCount
    assert om[BASE + ".3.3.2.6.0"] == ("counter", "12")     # TxLongRetries = MultipleRetry
    assert om[BASE + ".3.3.2.8.0"] == ("counter", "770")    # RxUnicast = RecvFrag-GroupRecv
    assert om[BASE + ".3.3.2.9.0"] == ("counter", "30")     # RxMulticast = GroupRecv
    assert om[BASE + ".3.3.2.10.0"] == ("counter", "9000000")  # RxUniOctets ~ rx_bytes
    assert om[BASE + ".3.3.2.13.0"] == ("counter", "2621")  # RxHwFCSErrors = FCSError


def test_stats_multicast_group_key_fallback():
    # 일부 펌웨어는 dot11Group* 명칭을 쓸 수 있어 fallback 지원(README 는 dot11Multicast*).
    mlan = {"link": {"address": "aa:bb:cc:dd:ee:01"},
            "mwlan_log": {
                "dot11TransmittedFrameCount": 500,
                "dot11GroupTransmittedFrameCount": 20,     # Group 명칭(fallback)
                "dot11ReceivedFragmentCount": 400,
                "dot11GroupReceivedFrameCount": 10,
            }}
    om = pp.build_oid_map(mlan=mlan)
    assert om[BASE + ".3.3.2.1.0"] == ("counter", "480")   # 500-20
    assert om[BASE + ".3.3.2.2.0"] == ("counter", "20")
    assert om[BASE + ".3.3.2.8.0"] == ("counter", "390")   # 400-10
    assert om[BASE + ".3.3.2.9.0"] == ("counter", "10")


def test_stats_bytes_present_when_mwlan_log_absent():
    # 가용성 비대칭(Claude): octet(.3/.10)은 link.bytes 소스라 mwlan_log 없어도 노출,
    # dot11 통계 7개는 noSuchInstance. 실운영 경계(/proc 접근 실패) 케이스.
    mlan = {"link": {"address": "aa:bb:cc:dd:ee:01", "tx_bytes": "1000", "rx_bytes": "2000"}}
    om = pp.build_oid_map(mlan=mlan)
    assert om[BASE + ".3.3.2.3.0"] == ("counter", "1000")
    assert om[BASE + ".3.3.2.10.0"] == ("counter", "2000")
    assert BASE + ".3.3.2.1.0" not in om
    assert BASE + ".3.3.2.13.0" not in om


def test_stats_zero_fixed_omitted():
    # 소스 영구 부재(.4 TxMultiOctets/.7 TxFifo/.11 RxMultiOctets/.12 RxFifo) → 미노출
    om = _stats_map()
    for sub in (".3.3.2.4.0", ".3.3.2.7.0", ".3.3.2.11.0", ".3.3.2.12.0"):
        assert BASE + sub not in om


def test_stats_absent_without_mwlan_log():
    # mwlan_log 없으면 9 통계 전부 noSuchInstance(omap 누락) — Counter 0-오염 방지.
    # mwlan_log 게이트 7개(dot11 기반)만 단언. octet(.3/.10)은 link.bytes 소스라 별개
    # (test_stats_bytes_present_when_mwlan_log_absent 가 비대칭을 따로 검증).
    om = _connected_map()
    for sub in (".1", ".2", ".5", ".6", ".8", ".9", ".13"):
        assert BASE + ".3.3.2" + sub + ".0" not in om


def test_stats_negative_derivation_clamped():
    mlan = dict(MLAN_STATS, mwlan_log={
        "dot11TransmittedFrameCount": 10,
        "dot11GroupTransmittedFrameCount": 40,    # 10-40 < 0 → clamp 0
    })
    om = pp.build_oid_map(mlan=mlan)
    assert om[BASE + ".3.3.2.1.0"] == ("counter", "0")


def test_stats_partial_keys_only_present():
    # FCS 만 있는 mwlan_log → .13 만 노출, 나머지 통계 omit
    mlan = {"link": {"address": "aa:bb:cc:dd:ee:01"},
            "mwlan_log": {"dot11FCSErrorCount": 7}}
    om = pp.build_oid_map(mlan=mlan)
    assert om[BASE + ".3.3.2.13.0"] == ("counter", "7")
    assert BASE + ".3.3.2.1.0" not in om
    assert BASE + ".3.3.2.2.0" not in om


def test_stats_counter_byte_wrap():
    # 64bit byte 카운터는 하위 32bit 만 노출
    mlan = {"link": {"address": "aa:bb:cc:dd:ee:01", "tx_bytes": str(4294967296 + 123)},
            "mwlan_log": {}}
    om = pp.build_oid_map(mlan=mlan)
    assert om[BASE + ".3.3.2.3.0"] == ("counter", "123")


def test_stats_getnext_walk_includes_stats():
    om = _stats_map()
    sorted_oids = sorted(om, key=pp.oid_key)
    # 통계가 walk 에 정상 포함되고 수치정렬 유지(.3.3.2.13 > .3.3.2.2)
    assert pp.oid_key(BASE + ".3.3.2.2.0") < pp.oid_key(BASE + ".3.3.2.13.0")
    assert (BASE + ".3.3.2.13.0") in sorted_oids
    # 전체 walk 무결성
    cur, walked = BASE, []
    for _ in range(len(om) + 5):
        res = pp.do_getnext(cur, sorted_oids, om)
        if res == ["NONE"]:
            break
        walked.append(res[0]); cur = res[0]
    assert walked == sorted_oids
