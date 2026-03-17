#!/usr/bin/env python3
"""
실제 pcap 파일을 사용한 통합 테스트

테스트 대상 pcap:
  FX3000: PacketCapture/mon1_00016_20260226134651.pcap (시간대: 13:46~13:52)
  FXE5000: mon1_00010_20260226102542.pcap (시간대: 10:25~10:32)

실행:
  cd tools/pcap-analyzer
  python3 tests/test_integration.py
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from extractor import extract_frames, build_tshark_cmd, parse_tsv_line
from detector import detect_roles, mac_name
from models import Frame, AnalysisSection
from reporter import format_report
from analyzers import overview, retry_mcs, retry_burst, roaming
from analyzers import ping_rtt, control_traffic, signal_quality, per_second

# ── 경로 설정 ──
BASE = "/home/jhw/ai/opencode/projects/wlan-package/tmp/pcap/FX3000"
FX3000_PCAP = os.path.join(BASE, "PacketCapture/mon1_00016_20260226134651.pcap")
FXE5000_PCAP = os.path.join(BASE, "mon1_00010_20260226102542.pcap")
SSID = "CANTOPS_TEST"
PASS = "123456789012345678901234567890123456789012345678901234567890123"

# FX3000 알려진 MAC
FX3000_AP3 = "00:80:4c:e1:09:cb"
FX3000_AP4 = "00:80:4c:e1:09:cc"
FX3000_STA1 = "00:50:43:18:fe:01"
FX3000_STA2 = "00:50:43:19:fe:01"
FX3000_STA3 = "00:50:43:1a:fe:01"

passed = 0
failed = 0
errors = []


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS: {name}")
    else:
        failed += 1
        msg = f"  FAIL: {name}"
        if detail:
            msg += f" — {detail}"
        print(msg)
        errors.append(name)


# ═══════════════════════════════════════════
# 1. Extractor 테스트
# ═══════════════════════════════════════════
print("\n=== 1. Extractor (tshark 추출) ===")

# 1-1. FX3000 시간 필터 추출
fx_frames = extract_frames(
    FX3000_PCAP, wpa_passphrase=PASS, ssid=SSID,
    time_start="2026-02-26 13:49:20", time_end="2026-02-26 13:49:40",
)
check("FX3000 프레임 추출 성공", len(fx_frames) > 0)
check("FX3000 프레임 수 6000~7000", 6000 <= len(fx_frames) <= 7000,
      f"actual={len(fx_frames)}")

# 1-2. 프레임 필드 유효성
f0 = fx_frames[0]
check("첫 프레임 number > 0", f0.number > 0, f"number={f0.number}")
check("첫 프레임 epoch > 0", f0.epoch > 1700000000, f"epoch={f0.epoch}")
check("첫 프레임 timestamp 비어있지 않음", len(f0.timestamp) > 10)

# 1-3. WPA 복호화 확인 (SSH/TCP/ICMP 프로토콜이 보여야 함)
protos = set(f.protocol for f in fx_frames)
check("WPA 복호화 성공 — TCP 존재", "TCP" in protos, f"protos={protos}")
check("WPA 복호화 성공 — ICMP 존재", "ICMP" in protos)
check("WPA 복호화 성공 — ARP 존재", "ARP" in protos)
check("WPA 복호화 성공 — EAPOL 존재", "EAPOL" in protos)

# 1-4. MAC 필터 테스트
mac_frames = extract_frames(
    FX3000_PCAP, wpa_passphrase=PASS, ssid=SSID,
    time_start="2026-02-26 13:49:20", time_end="2026-02-26 13:49:40",
    mac_filter=FX3000_STA1,
)
check("MAC 필터 — STA1만 추출", len(mac_frames) < len(fx_frames),
      f"filtered={len(mac_frames)}, total={len(fx_frames)}")
# 필터된 프레임의 대부분(99%+)이 STA1 관여 (브로드캐스트/멀티캐스트 소수 포함됨)
sta1_involved = sum(1 for f in mac_frames if f.ta == FX3000_STA1 or f.ra == FX3000_STA1)
check("MAC 필터 — 99%+ 프레임에 STA1 관여",
      sta1_involved / len(mac_frames) > 0.99,
      f"{sta1_involved}/{len(mac_frames)}={sta1_involved*100//len(mac_frames)}%")

# 1-5. IP 필터 테스트
ip_frames = extract_frames(
    FX3000_PCAP, wpa_passphrase=PASS, ssid=SSID,
    time_start="2026-02-26 13:49:20", time_end="2026-02-26 13:49:40",
    ip_filter="192.168.0.21",
)
check("IP 필터 — 추출 성공", len(ip_frames) > 0)
check("IP 필터 — 전체보다 적음", len(ip_frames) < len(fx_frames),
      f"filtered={len(ip_frames)}")
# 모든 프레임에 해당 IP 관여 (A-MPDU는 "ip,ip" 형태로 중복되므로 in 사용)
ip_ok = sum(1 for f in ip_frames if "192.168.0.21" in f.ip_src or "192.168.0.21" in f.ip_dst)
check("IP 필터 — 모든 프레임에 192.168.0.21 관여",
      ip_ok == len(ip_frames),
      f"{ip_ok}/{len(ip_frames)}")

# 1-6. 복수 MAC 필터
multi_mac_frames = extract_frames(
    FX3000_PCAP, wpa_passphrase=PASS, ssid=SSID,
    time_start="2026-02-26 13:49:20", time_end="2026-02-26 13:49:40",
    mac_filter=f"{FX3000_STA1},{FX3000_STA2}",
)
check("복수 MAC 필터 — 단일보다 많음", len(multi_mac_frames) > len(mac_frames),
      f"multi={len(multi_mac_frames)}, single={len(mac_frames)}")

# 1-7. parse_tsv_line 엣지 케이스
check("parse_tsv_line 빈 줄 → None", parse_tsv_line("") is None)
check("parse_tsv_line 짧은 줄 → None", parse_tsv_line("1\t2") is None)
check("parse_tsv_line 유효 줄", parse_tsv_line(
    "100\t1772081360.0\tTS\t1\t40\tTCP\t200\t15\t-36\taa\tbb\t1.1\t2.2\t\t\t0\t0x10\t99"
) is not None)


# ═══════════════════════════════════════════
# 2. Detector 테스트
# ═══════════════════════════════════════════
print("\n=== 2. Detector (MAC 역할 감지) ===")

roles = detect_roles(fx_frames)

check("AP3(cb) 감지됨", FX3000_AP3 in roles)
check("AP4(cc) 감지됨", FX3000_AP4 in roles)
check("AP3 역할 = AP", roles.get(FX3000_AP3, {}).get("role") == "AP")
check("AP4 역할 = AP", roles.get(FX3000_AP4, {}).get("role") == "AP")
check("STA1 감지됨", FX3000_STA1 in roles)
check("STA1 역할 = STA", roles.get(FX3000_STA1, {}).get("role") == "STA")
check("STA2 감지됨", FX3000_STA2 in roles)
check("STA3 감지됨", FX3000_STA3 in roles)
check("브로드캐스트 제외", "ff:ff:ff:ff:ff:ff" not in roles)

# mac_name 테스트
check("mac_name AP3", "AP" in mac_name(FX3000_AP3, roles))
check("mac_name STA1", "STA" in mac_name(FX3000_STA1, roles))
check("mac_name BCAST", mac_name("ff:ff:ff:ff:ff:ff", roles) == "BCAST")
check("mac_name 알 수 없는 MAC", len(mac_name("aa:bb:cc:dd:ee:ff", roles)) > 0)


# ═══════════════════════════════════════════
# 3. Overview 분석기 테스트
# ═══════════════════════════════════════════
print("\n=== 3. Overview 분석기 ===")

sec_overview = overview.analyze(fx_frames, roles)
check("overview 타이틀", "개요" in sec_overview.title)
check("overview 라인 수 > 10", len(sec_overview.lines) > 10)
check("overview summary에 프레임 수", "프레임" in sec_overview.summary)

# 프로토콜 분포 확인
overview_text = "\n".join(sec_overview.lines)
check("overview에 TCP 포함", "TCP" in overview_text)
check("overview에 ICMP 포함", "ICMP" in overview_text)
check("overview에 Retry 통계", "Retry" in overview_text)
check("overview에 디바이스 목록", FX3000_AP3 in overview_text or "AP" in overview_text)


# ═══════════════════════════════════════════
# 4. Retry MCS 분석기 테스트
# ═══════════════════════════════════════════
print("\n=== 4. Retry MCS 분석기 ===")

sec_mcs = retry_mcs.analyze(fx_frames, roles)
check("retry_mcs 타이틀", "MCS" in sec_mcs.title)
mcs_text = "\n".join(sec_mcs.lines)
check("MCS별 테이블 존재", "MCS" in mcs_text and "Total" in mcs_text)
check("고MCS 편중 분석 존재", "MCS14" in mcs_text or "고MCS" in mcs_text)
check("Rate Fallback 분석 존재", "Fallback" in mcs_text or "하강" in mcs_text)

# 이전 분석에서 확인된 값과 비교
check("retry rate ~39.6%", "39" in sec_mcs.summary or "40" in sec_mcs.summary,
      f"summary={sec_mcs.summary}")
check("fallback 6건", "6건" in mcs_text or "fallback 6" in sec_mcs.summary,
      f"summary={sec_mcs.summary}")


# ═══════════════════════════════════════════
# 5. Retry Burst 분석기 테스트
# ═══════════════════════════════════════════
print("\n=== 5. Retry Burst 분석기 ===")

sec_burst = retry_burst.analyze(fx_frames, roles)
check("retry_burst 타이틀", "Burst" in sec_burst.title)
burst_text = "\n".join(sec_burst.lines)
check("burst 이벤트 존재", "burst" in sec_burst.summary)
check("근거 Frame# 포함", "#" in burst_text)
check("지연 통계 존재", "min=" in burst_text and "max=" in burst_text)

# 이전 분석: 72건
check("burst 72건", "72건" in burst_text or "72" in sec_burst.summary,
      f"summary={sec_burst.summary}")


# ═══════════════════════════════════════════
# 6. 로밍 분석기 테스트
# ═══════════════════════════════════════════
print("\n=== 6. 로밍 분석기 ===")

sec_roam = roaming.analyze(fx_frames, roles)
check("roaming 타이틀", "로밍" in sec_roam.title)
roam_text = "\n".join(sec_roam.lines)

# 이 시간대에 로밍 이벤트가 있음 (Auth, ReassocReq, EAPOL)
check("로밍 프레임 존재", "로밍 관련 프레임" in roam_text)
check("Auth 프레임 포함", "Auth" in roam_text)

# 13:49:30 부근에 로밍 시퀀스가 있어야 함
check("로밍 시퀀스 감지", "시퀀스" in sec_roam.summary)


# ═══════════════════════════════════════════
# 7. Ping RTT 분석기 테스트
# ═══════════════════════════════════════════
print("\n=== 7. Ping RTT 분석기 ===")

sec_ping = ping_rtt.analyze(fx_frames, roles)
check("ping_rtt 타이틀", "Ping" in sec_ping.title)
ping_text = "\n".join(sec_ping.lines)

# 이전 분석: 104쌍
check("ping 쌍 존재", "ping" in sec_ping.summary)
check("RTT 통계 존재", "min=" in ping_text and "avg=" in ping_text)
check("정상/Retry 구분", "정상" in ping_text or "no retry" in ping_text)

# 이상치 탐지 (788ms 등)
check("이상치 탐지", "이상치" in ping_text,
      "이전 분석에서 788ms 이상치가 있었음")

# Req→Reply 프레임 번호 쌍 포함
check("Req→Reply 프레임 번호", "#" in ping_text)


# ═══════════════════════════════════════════
# 8. 제어 트래픽 분석기 테스트
# ═══════════════════════════════════════════
print("\n=== 8. 제어 트래픽 분석기 ===")

sec_ctrl = control_traffic.analyze(fx_frames, roles)
check("control_traffic 타이틀", "제어" in sec_ctrl.title)
ctrl_text = "\n".join(sec_ctrl.lines)
check("ARP 포함", "ARP" in ctrl_text)
check("ICMP 포함", "ICMP" in ctrl_text)
check("TCP ACK 포함", "TCP ACK" in ctrl_text)
check("프레임 번호 포함", any(line.strip().startswith("5") for line in sec_ctrl.lines if "|" in line))
check("RSSI 컬럼 존재", "RSSI" in ctrl_text)
check("MCS 컬럼 존재", "MCS" in ctrl_text)


# ═══════════════════════════════════════════
# 9. 신호 품질 분석기 테스트
# ═══════════════════════════════════════════
print("\n=== 9. 신호 품질 분석기 ===")

sec_sig = signal_quality.analyze(fx_frames, roles)
check("signal_quality 타이틀", "신호" in sec_sig.title)
sig_text = "\n".join(sec_sig.lines)
check("STA별 분석 존재", "STA" in sig_text)
check("RSSI min/max 존재", "min=" in sig_text and "max=" in sig_text)
check("최저 RSSI 근거 프레임#", "#" in sig_text)
check("TX MCS 분포 존재", "TX MCS" in sig_text)


# ═══════════════════════════════════════════
# 10. 초당 통계 분석기 테스트
# ═══════════════════════════════════════════
print("\n=== 10. 초당 통계 분석기 ===")

sec_ps = per_second.analyze(fx_frames, roles)
check("per_second 타이틀", "초당" in sec_ps.title)
ps_text = "\n".join(sec_ps.lines)
check("시간 컬럼 존재", "Time" in ps_text or "13:49" in ps_text)
check("Retry 컬럼 존재", "Retry" in ps_text)
check("Ctrl 컬럼 존재", "Ctrl" in ps_text)
check("핫스팟 탐지", "핫스팟" in ps_text)

# 이전 분석: 13:49:21에 222 retries → 핫스팟
check("13:49:21 핫스팟", "핫스팟" in sec_ps.summary and "2" in sec_ps.summary,
      f"summary={sec_ps.summary}")


# ═══════════════════════════════════════════
# 11. Reporter 테스트
# ═══════════════════════════════════════════
print("\n=== 11. Reporter (리포트 생성) ===")

all_sections = [sec_overview, sec_mcs, sec_burst, sec_roam,
                sec_ping, sec_ctrl, sec_sig, sec_ps]
report = format_report(all_sections, FX3000_PCAP, wpa_used=True)

check("리포트 헤더 존재", "WLAN Pcap 종합 분석 리포트" in report)
check("WPA 복호화 표시", "사용" in report)
check("요약 섹션 존재", "--- 요약 ---" in report)
check("8개 섹션 제목 존재",
      all(sec.title in report for sec in all_sections))
check("리포트 길이 > 10KB", len(report) > 10000, f"len={len(report)}")


# ═══════════════════════════════════════════
# 12. FXE5000 pcap 테스트 (다른 환경)
# ═══════════════════════════════════════════
print("\n=== 12. FXE5000 pcap (다른 환경) ===")

fxe_frames = extract_frames(FXE5000_PCAP, wpa_passphrase=PASS, ssid=SSID)
check("FXE5000 프레임 추출 성공", len(fxe_frames) > 0)
check("FXE5000 프레임 수 > 100000", len(fxe_frames) > 100000,
      f"actual={len(fxe_frames)}")

fxe_roles = detect_roles(fxe_frames)
fxe_aps = [m for m, r in fxe_roles.items() if r["role"] == "AP"]
fxe_stas = [m for m, r in fxe_roles.items() if r["role"] == "STA"]
check("FXE5000 AP 감지", len(fxe_aps) >= 1)
check("FXE5000 STA 감지", len(fxe_stas) >= 1)

# MCS 없는 환경에서도 분석 동작
fxe_mcs = retry_mcs.analyze(fxe_frames, fxe_roles)
check("FXE5000 MCS 없음 처리", "없음" in fxe_mcs.summary or "MCS" in fxe_mcs.summary)

# 로밍 이벤트가 많아야 함
fxe_roam = roaming.analyze(fxe_frames, fxe_roles)
check("FXE5000 로밍 프레임 존재", "로밍 프레임" in fxe_roam.summary)

# Retry burst 존재
fxe_burst = retry_burst.analyze(fxe_frames, fxe_roles)
check("FXE5000 burst 존재", "burst" in fxe_burst.summary)

# 초당 통계 핫스팟 다수
fxe_ps = per_second.analyze(fxe_frames, fxe_roles)
check("FXE5000 핫스팟 다수", "핫스팟" in "\n".join(fxe_ps.lines))


# ═══════════════════════════════════════════
# 13. 엣지 케이스 테스트
# ═══════════════════════════════════════════
print("\n=== 13. 엣지 케이스 ===")

# 빈 프레임 리스트
empty_overview = overview.analyze([], {})
check("빈 프레임 — overview 처리", "없음" in empty_overview.summary)

empty_mcs = retry_mcs.analyze([], {})
check("빈 프레임 — retry_mcs 처리", len(empty_mcs.lines) > 0)

empty_burst = retry_burst.analyze([], {})
check("빈 프레임 — retry_burst 처리", len(empty_burst.lines) > 0)

empty_roam = roaming.analyze([], {})
check("빈 프레임 — roaming 처리", "없음" in empty_roam.summary)

empty_ping = ping_rtt.analyze([], {})
check("빈 프레임 — ping_rtt 처리", "없음" in empty_ping.summary)

empty_ctrl = control_traffic.analyze([], {})
check("빈 프레임 — control_traffic 처리", "없음" in empty_ctrl.summary)

empty_sig = signal_quality.analyze([], {})
check("빈 프레임 — signal_quality 처리", "없음" in empty_sig.summary)

empty_ps = per_second.analyze([], {})
check("빈 프레임 — per_second 처리", "없음" in empty_ps.summary)

# detect_roles 빈 리스트
empty_roles = detect_roles([])
check("빈 프레임 — detect_roles 빈 dict", len(empty_roles) == 0)

# build_tshark_cmd 다양한 조합
cmd = build_tshark_cmd("/test.pcap")
check("cmd 기본 — tshark 포함", "tshark" in cmd[0])
check("cmd 기본 — -Y 없음 (필터 없음)", "-Y" not in cmd)

cmd_wpa = build_tshark_cmd("/test.pcap", wpa_passphrase="pw", ssid="net")
check("cmd WPA — 복호화 옵션 포함", "wlan.enable_decryption:TRUE" in " ".join(cmd_wpa))

cmd_time = build_tshark_cmd("/test.pcap", time_start="2026-01-01 00:00:00")
check("cmd 시간 — -Y 포함", "-Y" in cmd_time)

cmd_mac = build_tshark_cmd("/test.pcap", mac_filter="aa:bb:cc:dd:ee:ff")
check("cmd MAC — wlan.addr 포함", "wlan.addr" in " ".join(cmd_mac))

cmd_ip = build_tshark_cmd("/test.pcap", ip_filter="10.0.0.1")
check("cmd IP — ip.addr 포함", "ip.addr" in " ".join(cmd_ip))

cmd_multi = build_tshark_cmd("/test.pcap", mac_filter="aa:aa:aa:aa:aa:aa,bb:bb:bb:bb:bb:bb")
check("cmd 복수 MAC — || 포함", "||" in " ".join(cmd_multi))

cmd_all = build_tshark_cmd(
    "/test.pcap", wpa_passphrase="pw", ssid="net",
    time_start="2026-01-01 00:00:00", time_end="2026-01-02 00:00:00",
    mac_filter="aa:aa:aa:aa:aa:aa", ip_filter="10.0.0.1",
)
cmd_str = " ".join(cmd_all)
check("cmd 전체 조합 — 모든 필터 포함",
      "wlan.addr" in cmd_str and "ip.addr" in cmd_str and "frame.time" in cmd_str)


# ═══════════════════════════════════════════
# 14. 이전 분석 결과와 교차 검증
# ═══════════════════════════════════════════
print("\n=== 14. 이전 분석 결과 교차 검증 (FX3000 13:49:20~40) ===")

# 이전 수동 분석에서 확인된 값들과 비교
retry_frames = [f for f in fx_frames if f.retry]
check("Retry 수 ~1034", 900 <= len(retry_frames) <= 1200,
      f"actual={len(retry_frames)}")

# ICMP ping 쌍 수 ~104
icmp_req = [f for f in fx_frames if f.is_icmp_request and not f.retry]
check("ICMP Request(non-retry) 수 ~104", 90 <= len(icmp_req) <= 120,
      f"actual={len(icmp_req)}")

# ARP 프레임 ~46
arp_frames = [f for f in fx_frames if f.is_arp]
check("ARP 프레임 수 ~46", 40 <= len(arp_frames) <= 55,
      f"actual={len(arp_frames)}")

# EAPOL 프레임 ~12
eapol_frames = [f for f in fx_frames if f.protocol == "EAPOL"]
check("EAPOL 프레임 수 ~12", 10 <= len(eapol_frames) <= 15,
      f"actual={len(eapol_frames)}")

# MCS 15가 가장 많아야 함
from collections import Counter
mcs_dist = Counter(f.mcs_int for f in fx_frames if f.mcs_int is not None)
if mcs_dist:
    top_mcs = mcs_dist.most_common(1)[0][0]
    check("MCS 15가 최다", top_mcs == 15, f"top_mcs={top_mcs}")

# STA1의 RSSI (STA→AP): -30~-45 범위
sta1_tx = [f for f in fx_frames if f.ta == FX3000_STA1 and f.rssi_first is not None]
if sta1_tx:
    avg_rssi = sum(f.rssi_first for f in sta1_tx) / len(sta1_tx)
    check("STA1 TX RSSI 범위 (-30~-50)", -50 <= avg_rssi <= -30,
          f"avg={avg_rssi:.0f}")


# ═══════════════════════════════════════════
# 결과 요약
# ═══════════════════════════════════════════
print(f"\n{'='*60}")
total = passed + failed
print(f"결과: {passed}/{total} 통과 ({passed*100//total}%)")
if errors:
    print(f"실패 목록:")
    for e in errors:
        print(f"  - {e}")
print(f"{'='*60}")

sys.exit(0 if failed == 0 else 1)
