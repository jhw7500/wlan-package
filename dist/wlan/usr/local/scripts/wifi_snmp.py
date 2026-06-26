#!/usr/bin/env python3
"""wifi_snmp.py — net-snmp ``extend`` 백엔드.

snmpd.conf 의 ``extend <name> /usr/bin/python3 /usr/local/scripts/wifi_snmp.py <metric>``
디렉티브가 호출한다. WiFi 링크 지표 1개를 인자로 받아 그 값을 stdout 한 줄로 출력한다
(net-snmp 는 첫 줄을 NET-SNMP-EXTEND-MIB 의 nsExtendOutput1Line OID 로 노출한다).

데이터 출처는 cantops 링크 로거(wifi_logger_link.py)가 주기적으로 기록하는
``/var/log/cantops/json/<iface>/link.json`` 단일 파일이다. 추가 명령 실행 없이 이 파일만
파싱하므로 SNMP GET 응답이 가볍고 로거와 항상 같은 값을 노출한다.

지원 metric (snmpd.conf 의 extend 9종과 1:1):
    rssi bssid ssid channel txrate retry failed fcs_error noise

값을 구할 수 없으면(미연결·로거 미기동·필드 부재) 빈 줄을 출력하고 exit 0 한다.
알 수 없는 metric 만 stderr 경고 + exit 1.

오버라이드 환경변수(테스트/다중 IF 용):
    WIFI_SNMP_IFACE      대상 인터페이스 (기본 mlan0)
    WIFI_SNMP_LINK_JSON  link.json 경로 직접 지정 (지정 시 IFACE 무시)
"""

import json
import os
import re
import sys

DEFAULT_IFACE = "mlan0"
LINK_JSON_FMT = "/var/log/cantops/json/{iface}/link.json"


def _link_json_path():
    override = os.environ.get("WIFI_SNMP_LINK_JSON")
    if override:
        return override
    iface = os.environ.get("WIFI_SNMP_IFACE", DEFAULT_IFACE)
    return LINK_JSON_FMT.format(iface=iface)


def load_link(path=None):
    """link.json 을 dict 로 읽는다. 부재/파싱오류/연결끊김({})이면 빈 dict."""
    if path is None:
        path = _link_json_path()
    try:
        with open(path, "r") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _first_int(value):
    """문자열/숫자에서 첫 정수(부호 포함)를 뽑는다. 실패 시 None."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    m = re.search(r"-?\d+", str(value))
    return int(m.group()) if m else None


def _first_float(value):
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    m = re.search(r"-?\d+(?:\.\d+)?", str(value))
    return float(m.group()) if m else None


def _mwlan_counter(data, key):
    """mwlan_log 의 dot11* 카운터를 정수로 반환.

    parse_mwlan_log 는 공백 포함 값을 리스트(int[])로, 단일 값을 int 로 저장한다.
    리스트면 합을 사용한다. 없으면 None."""
    log = data.get("mwlan_log")
    if not isinstance(log, dict):
        return None
    val = log.get(key)
    if val is None:
        return None
    if isinstance(val, list):
        nums = [v for v in val if isinstance(v, int)]
        return sum(nums) if nums else None
    return _first_int(val)


def _fmt(value):
    """None → 빈 문자열, 그 외는 str(). float 은 정수면 정수로 축약."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


# ---- metric getters ---------------------------------------------------------
# 각 getter 는 link.json dict 를 받아 원시값(숫자/문자열) 또는 None 을 반환한다.

def m_rssi(d):
    link = d.get("link", {})
    # station dump 의 'signal avg' → signal_avg, 없으면 'signal'.
    return _first_int(link.get("signal_avg") or link.get("signal"))


def m_bssid(d):
    # 연결된 AP MAC = station dump 헤더의 주소(link.address). info.address 는 자기 MAC.
    return d.get("link", {}).get("address") or None


def m_ssid(d):
    return d.get("info", {}).get("ssid") or None


def m_channel(d):
    return _first_int(d.get("info", {}).get("channel"))


def m_txrate(d):
    # link.tx_bitrate 예: "65.0 MBit/s" → 65.0
    return _first_float(d.get("link", {}).get("tx_bitrate"))


def m_retry(d):
    # 주: 1차 소스 dot11RetryCount 는 어댑터 누적, 폴백 link.tx_retries 는 스테이션 단위라
    # 스코프가 다르다. mlan0 은 /proc log 가 상존해 폴백이 거의 발동하지 않는다.
    val = _mwlan_counter(d, "dot11RetryCount")
    if val is None:
        val = _first_int(d.get("link", {}).get("tx_retries"))
    return val


def m_failed(d):
    # 주: m_retry 와 동일 — dot11FailedCount(어댑터 누적) vs link.tx_failed(스테이션) 스코프 차.
    val = _mwlan_counter(d, "dot11FailedCount")
    if val is None:
        val = _first_int(d.get("link", {}).get("tx_failed"))
    return val


def m_fcs_error(d):
    # dot11FCSErrorCount 는 온타겟 NXP getlog 에 통상 존재하나 본 레포 샘플로 미검증.
    # 폴백 소스가 없어 mwlan_log 에 없으면 빈 값을 준다.
    return _mwlan_counter(d, "dot11FCSErrorCount")


def m_noise(d):
    info = d.get("info", {})
    chans = d.get("channel_info", {})
    if not isinstance(chans, dict):
        return None
    freq = _first_int(info.get("freq"))
    if freq is not None:
        entry = chans.get(str(freq))
        if isinstance(entry, dict) and "noise" in entry:
            return _first_int(entry.get("noise"))
    # info.freq 매칭 실패 시(iw info 일시 실패 등). survey 는 보통 모든 채널 항목을
    # 만들지만 noise 는 측정된 in-use 채널에만 있다 → noise 보유 항목이 정확히 1개면
    # 그것을 쓴다(wifi_link_monitor.py:520-525 와 동일 정책). 단순 len(chans)==1 은
    # 다채널 survey 에서 거의 성립 안 해 폴백이 죽으므로 쓰지 않는다.
    candidates = [v for v in chans.values()
                  if isinstance(v, dict) and "noise" in v]
    if len(candidates) == 1:
        return _first_int(candidates[0].get("noise"))
    return None


METRICS = {
    "rssi": m_rssi,
    "bssid": m_bssid,
    "ssid": m_ssid,
    "channel": m_channel,
    "txrate": m_txrate,
    "retry": m_retry,
    "failed": m_failed,
    "fcs_error": m_fcs_error,
    "noise": m_noise,
}


def get_metric(metric, data=None):
    getter = METRICS.get(metric)
    if getter is None:
        raise KeyError(metric)
    if data is None:
        data = load_link()
    return getter(data)


def main(argv):
    if len(argv) != 2:
        sys.stderr.write(
            "usage: wifi_snmp.py <%s>\n" % "|".join(METRICS)
        )
        return 1
    metric = argv[1].strip()
    if metric not in METRICS:
        sys.stderr.write("wifi_snmp.py: unknown metric %r\n" % metric)
        return 1
    try:
        value = get_metric(metric)
    except Exception as e:  # 견고성: 어떤 예외도 SNMP 응답을 막지 않게 빈 값 처리
        sys.stderr.write("wifi_snmp.py: %s\n" % e)
        print("")
        return 0
    print(_fmt(value))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
