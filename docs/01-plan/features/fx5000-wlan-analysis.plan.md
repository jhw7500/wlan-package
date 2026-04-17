# FX5000 WLAN 패킷 종합 분석 Plan

> 작성일: 2026-04-06
> Feature: fx5000-wlan-analysis
> Phase: Plan

## Executive Summary

| 관점 | 내용 |
|------|------|
| **Problem** | FX5000 환경에서 AP1/AP2 간 패킷 불균형(60:40)과 간헐적 ping timeout(0.5~0.8%)이 발생하며, 근본 원인이 불명확함 |
| **Solution** | 4종 로그(AP LOG, pcap, ExPing, 클라이언트 로그)를 종합 분석하여 체류시간·패킷비율·재전송률·timeout 상관관계를 규명 |
| **Function UX Effect** | AP별 클라이언트 체류시간 편중, 재전송률 차이, timeout 발생 패턴을 시각적 데이터로 제시 |
| **Core Value** | 로밍 정책 및 AP 배치 최적화를 위한 데이터 근거 확보 |

## Context Anchor

| 항목 | 내용 |
|------|------|
| **WHY** | AP 2대 환경에서 패킷 불균형과 ping timeout의 근본 원인을 파악하여 WLAN 안정성 개선 근거 마련 |
| **WHO** | WLAN 엔지니어, QA 팀 |
| **RISK** | pcap 파일 50MB×17개로 처리 시간이 길 수 있음. 클라이언트 로그 시간대가 다를 수 있음 |
| **SUCCESS** | 5개 분석 항목 모두 수치 기반 결과 도출, 원인 가설 제시 |
| **SCOPE** | 2026-02-26 09:14~11:16 구간의 FX5000 테스트 데이터 한정 |

---

## 1. 테스트 환경

### 1.1 네트워크 구성

| 항목 | 값 |
|------|-----|
| SSID | CANTOPS_TEST |
| 인증 | WPA2-PSK |
| Passphrase | 123456789...063 (63자) |
| 채널 | 48 (5240 MHz) |
| AP 펌웨어 | FXA5020-KR ver.1.06.04 (2025-12-18) |
| 테스트 시간 | 2026-02-26 09:14 ~ 11:16 (약 2시간) |

### 1.2 장비 매핑

| 장비 | BSSID / MAC | IP | 비고 |
|------|-------------|-----|------|
| AP1 | `02:80:4c:e7:06:24` (WLAN) / `00:80:4c:e7:06:22` (LAN) | 192.168.0.11 | |
| AP2 | `02:80:4c:e7:06:38` (WLAN) / `00:80:4c:e7:06:36` (LAN) | 192.168.0.12 | |
| STA1 (VHL102) | `00:50:43:18:fe:01` | 192.168.0.21 | DFK 로그: VHL102_21 |
| STA2 (VHL201) | `00:50:43:1a:fe:01` | 192.168.0.22 | DFK 로그: VHL201_22 |
| STA3 (VHL318) | `00:50:43:19:fe:01` | 192.168.0.23 | DFK 로그: VHL318_23 |
| Ping 소스 | `24:f5:aa:ce:93:03` | 192.168.0.30 | ExPing 실행 PC |

### 1.3 데이터 소스

| # | 소스 | 경로 | 설명 |
|---|------|------|------|
| 1 | AP LOG | `AP LOG/AP{1,2}_*.txt` | AP1/AP2 각 7개 로그. Auth/Assoc/Roaming/Login/Logout 이벤트 |
| 2 | Capture | `Capture/mon1_*.pcap` | 17개 pcap (mon1_00001~00017), 총 2,662,909 패킷, 모니터 모드 |
| 3 | ExPing | `ExPing/ExPing結果.csv` | 3 STA 대상 ping (SHIFT_JIS 인코딩), 17,049회/STA |
| 4 | DFK 클라이언트 | `DFK_CFI-FXE5000_20260226/VHL{102_21,201_22,318_23}/` | STA별 7개 로그 (wpa.log, freq.log, ap.log 등) |

---

## 2. 분석 요구사항

### R1. AP별 클라이언트 체류시간 비교

**목적**: 각 STA가 AP1/AP2에 각각 얼마나 오래 연결되어 있었는지 산출

**데이터 소스**:
- 클라이언트 `wpa.log`: `CTRL-EVENT-CONNECTED` → BSSID로 AP 식별, 다음 CONNECTED 또는 DISCONNECTED까지가 체류시간
- AP LOG: `Login` / `Logout` / `Roaming` 이벤트로 교차 검증

**산출물**:
- STA별 AP1/AP2 체류시간 합계 및 비율
- 로밍 횟수 (AP1→AP2, AP2→AP1)
- 시간대별 AP 연결 타임라인

### R2. AP별 패킷 비율 분석

**목적**: pcap에서 AP1/AP2 각각의 프레임 분포를 분석

**데이터 소스**:
- `Capture/mon1_*.pcap`: BSSID 필드 기준 필터링

**분석 항목**:
- 전체 프레임 비율 (AP1 vs AP2)
- 프레임 타입별 분해 (Management / Control / Data)
- STA별 AP 프레임 분포
- 시간대별(10분 단위) AP 프레임 추이
- HE Action No Ack 등 특이 프레임 분석

### R3. 재전송률 분석 비교

**목적**: AP별/STA별 재전송(Retry) 비율 비교

**데이터 소스**:
- `Capture/mon1_*.pcap`: `wlan.fc.retry == 1` 필터

**분석 항목**:
- AP별 전체 Retry율
- STA별 Retry율 (AP1에서의 Retry vs AP2에서의 Retry)
- 시간대별 Retry율 변화
- Retry 집중 구간과 로밍 이벤트 상관관계

### R4. Ping Timeout 구간 체크

**목적**: ExPing NG 발생 시점과 WLAN 이벤트 상관관계 분석

**데이터 소스**:
- `ExPing/ExPing結果.csv`: NG 레코드 (SHIFT_JIS → UTF-8 변환)
- `ExPing/ExPing統計.csv`: 통계 요약
- 클라이언트 `wpa.log`: 로밍/재연결 이벤트

**분석 항목**:
- STA별 NG 횟수 및 비율 (현재: .21=143건 0.8%, .22=85건 0.5%, .23=144건 0.8%)
- NG 발생 시간대 분포 (히트맵)
- NG 발생 시점 ±5초 내 로밍/Auth/Assoc 이벤트 존재 여부
- 연속 NG 구간 식별 (버스트 타임아웃)
- 고지연(>100ms) 구간과 NG 구간 상관관계

### R5. 종합 원인 분석

**목적**: R1~R4 결과를 종합하여 패킷 불균형 및 timeout 근본 원인 규명

**분석 관점**:
- 체류시간 편중 → 패킷 불균형 기여도
- 로밍 시점 → ping timeout 발생 상관관계
- AP별 Retry율 차이 → 채널 품질/간섭 영향
- HE Action No Ack 편중 → AP1 고유 동작 분석
- Beacon RSSI 차이 → 스니퍼 위치 vs 실제 클라이언트 RSSI 비교 (freq.log)

---

## 3. 분석 방법론

### 3.1 도구

| 도구 | 용도 |
|------|------|
| `tshark` | pcap 파싱, 필터링, 통계 추출 |
| `python3` (pandas) | 로그 파싱, 시간 상관관계 분석, 통계 계산 |
| `iconv` | SHIFT_JIS → UTF-8 변환 (ExPing) |

### 3.2 분석 순서

```
Step 1: 데이터 전처리
  ├── pcap 병합 (mergecap) 또는 개별 처리
  ├── ExPing CSV 인코딩 변환
  ├── 클라이언트 wpa.log 파싱 → 로밍 이벤트 테이블
  └── AP LOG 파싱 → Login/Logout/Roaming 이벤트 테이블

Step 2: R1 체류시간 분석
  ├── wpa.log CONNECTED 이벤트 → AP별 체류시간 산출
  ├── AP LOG Login/Logout → 교차 검증
  └── 타임라인 생성

Step 3: R2 패킷 비율 분석
  ├── tshark BSSID 기준 프레임 카운트
  ├── 프레임 타입별 분해
  ├── STA별 분포
  └── 시간대별 추이

Step 4: R3 재전송률 분석
  ├── tshark Retry 필터 → AP별/STA별 카운트
  ├── 시간대별 Retry율 변화
  └── Retry 집중 구간 식별

Step 5: R4 Ping Timeout 분석
  ├── ExPing NG 시점 추출
  ├── wpa.log 로밍 이벤트와 시간 매칭
  ├── AP LOG 이벤트와 시간 매칭
  └── 상관관계 테이블 생성

Step 6: R5 종합 원인 분석
  ├── R1~R4 결과 교차 분석
  ├── 가설 수립 및 검증
  └── 결론 및 개선 제안
```

### 3.3 pcap 처리 전략

17개 pcap 파일(각 ~50MB)을 개별 처리하되 결과를 합산:

```bash
# AP별 프레임 카운트 예시
for f in Capture/mon1_*.pcap; do
  tshark -r "$f" -Y "wlan.bssid == 02:80:4c:e7:06:24" -T fields -e frame.number | wc -l
done
```

대용량이므로 `-Y` 디스플레이 필터 대신 `-R` 읽기 필터 또는 `-2` 2-pass 사용 고려.

---

## 4. 성공 기준

| # | 기준 | 측정 방법 |
|---|------|-----------|
| SC-1 | 3개 STA 모두 AP1/AP2 체류시간(초) 산출 완료 | 체류시간 합계 = 테스트 총 시간의 95% 이상 커버 |
| SC-2 | AP별 프레임 비율이 프레임 타입별로 분해됨 | Management/Control/Data 각각 수치 존재 |
| SC-3 | Retry율이 AP별·STA별로 산출됨 | 6개 조합(2 AP × 3 STA) 모두 수치 존재 |
| SC-4 | Ping NG 건수의 80% 이상에 대해 원인 이벤트 매칭 | NG 시점 ±5초 내 로밍/재연결 이벤트 매칭률 |
| SC-5 | 종합 원인 가설이 데이터로 뒷받침됨 | 가설당 최소 2개 이상 데이터 포인트 제시 |

---

## 5. 리스크

| 리스크 | 영향 | 대응 |
|--------|------|------|
| pcap 대용량 처리 시간 | 17×50MB, tshark 처리 느림 | 개별 파일 병렬 처리, 필요시 merged pcap 사용 |
| 시간 동기화 불일치 | AP LOG와 클라이언트 로그 시간 차이 | NTP 설정 시점 확인, 오프셋 보정 |
| 스니퍼 위치 편향 | pcap RSSI가 실제 STA 수신과 다름 | freq.log의 실제 RSSI와 비교 |
| ExPing 인코딩 | SHIFT_JIS 파일 처리 | iconv로 UTF-8 변환 후 처리 |

---

## 6. 산출물

| 산출물 | 형식 | 설명 |
|--------|------|------|
| 체류시간 테이블 | Markdown 표 | STA×AP 체류시간 매트릭스 |
| 패킷 비율 테이블 | Markdown 표 | AP×프레임타입 매트릭스, STA별 분포 |
| 재전송률 테이블 | Markdown 표 | AP×STA Retry율 매트릭스 |
| Ping Timeout 상관 테이블 | Markdown 표 | NG 시점 ↔ WLAN 이벤트 매칭 |
| 종합 분석 보고서 | Markdown | R1~R5 결과 통합, 원인 가설, 개선 제안 |
