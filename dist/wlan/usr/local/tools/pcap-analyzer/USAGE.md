# WLAN Pcap Analyzer 사용 가이드

## 기본 사용법

```bash
cd /usr/local/tools/pcap-analyzer
python3 pcap_analyzer.py <pcap파일> [옵션]
```

---

## pcap 파일 병합

여러 pcap 파일을 시간순으로 하나로 합칠 때 `mergecap`을 사용한다. Wireshark/tshark 패키지에 포함되어 있다.

```bash
# 기본 사용법 (시간순 병합이 기본 동작)
mergecap -w merged.pcap file1.pcap file2.pcap file3.pcap

# 파일명 정렬 후 병합
mergecap -w merged.pcap $(ls -1 *.pcap | sort)

# 병합 결과 확인
tshark -r merged.pcap -c 1 -T fields -e frame.time   # 첫 패킷 시간
tshark -r merged.pcap -T fields -e frame.time | tail -1  # 마지막 패킷 시간
```

분할 캡처된 pcap 파일들을 병합한 후 분석하면 전체 시간대를 한 번에 볼 수 있다.

---

## 옵션 목록

### 네트워크 설정 (JSON)

SSID, 비밀번호, 인증 방식을 JSON 파일에 저장하여 반복 입력을 줄인다.

| 옵션 | 설명 |
|------|------|
| `--config FILE` | 네트워크 설정 JSON 파일 경로 |

```json
{
    "ssid": "CANTOPS_TEST",
    "passphrase": "your_wpa_passphrase",
    "auth": "WPA2-PSK"
}
```

**로드 순서**: JSON 먼저 읽고, CLI 인자(`--ssid`, `--pass`)가 있으면 덮어쓴다.

**자동 탐색**: `--config`를 생략하면 아래 경로에서 `network.json`을 자동으로 찾는다.
1. pcap 파일과 같은 디렉토리
2. 현재 작업 디렉토리

```bash
# 명시적 지정
python3 pcap_analyzer.py capture.pcap --config /path/to/network.json

# 자동 탐색 (pcap 디렉토리 또는 현재 디렉토리에 network.json이 있으면 자동 로드)
python3 pcap_analyzer.py capture.pcap

# CLI 인자로 덮어쓰기 (JSON의 ssid/pass 대신 CLI 값 사용)
python3 pcap_analyzer.py capture.pcap --config network.json --ssid OTHER_SSID
```

예시 파일: `network.json.example`

### WPA 복호화

모니터 모드 pcap에서 ARP/ICMP/TCP 등 상위 프로토콜을 분석하려면 WPA 복호화가 필요하다. `network.json` 또는 CLI 인자로 SSID/비밀번호를 지정한다.

| 옵션 | 설명 |
|------|------|
| `--ssid SSID` | AP의 SSID (config보다 우선) |
| `--pass PASSPHRASE` | WPA 비밀번호 (config보다 우선) |

```bash
python3 pcap_analyzer.py capture.pcap --ssid CANTOPS_TEST --pass "mypassword"
```

복호화 없이 실행하면 802.11 레벨 분석만 수행된다 (Retry, MCS, RSSI, 로밍 이벤트 등).

### 시간 필터

대용량 pcap에서 특정 시간대만 추출하여 분석한다. tshark 디스플레이 필터로 동작하므로 추출 속도가 빠르다.

| 옵션 | 설명 | 형식 |
|------|------|------|
| `--start` | 시작 시간 | `"YYYY-MM-DD HH:MM:SS"` |
| `--end` | 종료 시간 | `"YYYY-MM-DD HH:MM:SS"` |

```bash
python3 pcap_analyzer.py capture.pcap --start "2026-02-26 13:49:20" --end "2026-02-26 13:49:40"
```

둘 중 하나만 지정해도 동작한다. 생략하면 pcap 전체를 분석한다.

### MAC 주소 필터

특정 MAC 주소가 관여한 프레임만 추출한다. TA(송신) 또는 RA(수신) 중 하나라도 일치하면 포함된다.

| 옵션 | 설명 |
|------|------|
| `--mac MAC` | MAC 주소 필터 (콤마로 복수 지정, OR 조건) |

```bash
# 단일 MAC
python3 pcap_analyzer.py capture.pcap --mac "00:50:43:18:fe:01"

# 복수 MAC (OR 조건)
python3 pcap_analyzer.py capture.pcap --mac "00:50:43:18:fe:01,00:50:43:19:fe:01"
```

### IP 주소 필터

특정 IP 주소가 관여한 프레임만 추출한다. WPA 복호화와 함께 사용해야 의미가 있다.

| 옵션 | 설명 |
|------|------|
| `--ip IP` | IP 주소 필터 (콤마로 복수 지정, OR 조건) |

```bash
python3 pcap_analyzer.py capture.pcap --ssid ... --pass ... --ip "192.168.0.21"
```

### STA 집중 분석

`--mac`의 alias로, 특정 STA 하나에 대해 집중 분석할 때 사용한다. 해당 STA가 관여한 프레임만 추출하여 전체 분석을 수행한다.

| 옵션 | 설명 |
|------|------|
| `--sta MAC` | 특정 STA MAC 주소 |

```bash
python3 pcap_analyzer.py capture.pcap --ssid ... --pass ... --sta "00:50:43:18:fe:01"
```

### AP 비교

AP 간 성능 비교와 프레임 분포 분석을 추가한다. 2대 이상의 AP가 감지될 때 유효하다.

| 옵션 | 설명 |
|------|------|
| `--compare-ap` | AP 비교 섹션 추가 (2대 이상일 때 유효) |

```bash
python3 pcap_analyzer.py capture.pcap --ssid ... --pass ... --compare-ap
```

출력 내용:

**1) 성능 비교 (TA/RA 기반)** — 기존 항목

```
             AP |   프레임 |  Retry% |  RSSI avg |  RSSI min |  로밍 수신
----------------------------------------------------------------------
      AP1(09cb) |    3070 |   19.1% |       -49 |       -82 |        0
      AP2(09cc) |    2446 |   18.4% |       -46 |       -93 |        3
```

**2) 프레임 분포 (BSSID 기반)** — AP별 패킷 수/비율 분석

```
             AP |        프레임 |      비율
--------------------------------------
      AP1(0624) |      1,771 |   60.7%
      AP2(0638) |      1,147 |   39.3%
```

- **프레임 타입별 분해**: Management / Control / Data 비율을 AP별로 비교
- **서브타입별 상세**: QoS Data, Beacon, Probe Response 등 주요 서브타입의 AP별 개수와 차이
- **불균형 기여도**: AP간 프레임 차이가 5% 이상일 때, 어떤 서브타입이 불균형에 기여하는지 비율로 표시
- **Beacon RSSI 비교**: AP별 Beacon 프레임의 평균 RSSI

### 간결 모드 (현장용)

진단, Ping Loss, 로밍 영향, AP 비교 섹션만 출력한다. 현장에서 핵심 결과를 빠르게 확인할 때 사용한다.

| 옵션 | 설명 |
|------|------|
| `--brief` | 진단 관련 섹션만 출력 |

```bash
python3 pcap_analyzer.py capture.pcap --ssid ... --pass ... --brief
```

요약 섹션에는 전체 12개 모듈의 summary가 모두 표시되지만, 상세 내용은 진단 관련 섹션만 포함된다.

### 외부 로그 병합

wpa_supplicant, kernel, syslog 등 외부 로그 파일을 pcap 분석 리포트에 병합한다. 로밍/연결 관련 키워드(ROAM, AUTH, ASSOC, EAPOL, DISCONNECT, CONNECT, carrier, signal 등)가 포함된 라인만 필터하여 타임라인으로 출력한다.

| 옵션 | 설명 |
|------|------|
| `--with-log FILE [FILE ...]` | 로그 파일 경로 (복수 가능) |

지원하는 타임스탬프 형식:
- epoch: `1772081370.615432: ...`
- syslog: `Feb 26 13:49:30 hostname ...`
- ISO: `2026-02-26 13:49:30.123 ...`
- 시간: `13:49:30.123 ...`

```bash
# 절대 경로
python3 pcap_analyzer.py capture.pcap --with-log /path/to/wpa.log /path/to/kerl.log

# 상대 경로 (현재 디렉토리 기준)
python3 pcap_analyzer.py capture.pcap --with-log ./wpa.log ./sys.log
```

### 출력 파일

| 옵션 | 설명 | 기본값 |
|------|------|--------|
| `-o FILE` | 출력 파일 경로 | `<pcap파일명>_analysis.txt` |

```bash
python3 pcap_analyzer.py capture.pcap -o /tmp/result.txt
```

---

## 분석 리포트 구성 (12개 섹션)

| # | 섹션 | 내용 | 항상 실행 |
|---|------|------|-----------|
| 1 | 개요 | 프레임수, 시간, 프로토콜 분포, 디바이스 목록 | O |
| 2 | Retry MCS 분포 | MCS별 retry rate, Rate Fallback 패턴 | O |
| 3 | Retry Burst | 연속 retry burst + 제어트래픽 지연 | O |
| 4 | 로밍 이벤트 | Auth/Assoc/EAPOL 시퀀스 타임라인 | O |
| 5 | Ping RTT | ICMP Request→Reply 매칭 + 이상치 | O |
| 6 | 제어 트래픽 | ARP/ICMP/TCP ACK 타임라인 | O |
| 7 | 신호 품질 | STA별 RSSI/MCS 분포, 최저 RSSI 근거 | O |
| 8 | 초당 통계 | 초별 retry/제어트래픽 히트맵, 핫스팟 | O |
| 9 | 로밍 영향 분석 | 로밍 전후 retry/RSSI/ping 변화 | O |
| 10 | Ping Loss | 응답 없는 Request + 원인 역추적 | O |
| 11 | 종합 진단 | STA별 WARNING/INFO + 현장 제안 | O |
| 12 | AP 비교 | AP 간 성능 비교 + BSSID 기반 프레임 분포/불균형 분석 | `--compare-ap` |
| - | 외부 로그 | 로밍 키워드 필터 타임라인 | `--with-log` |

---

## 옵션 조합 예시

### 1. 현장 빠른 진단

문제가 발생한 시간대에 대해 핵심 진단만 확인한다.

```bash
python3 pcap_analyzer.py capture.pcap \
  --ssid CANTOPS_TEST --pass "비밀번호" \
  --start "2026-02-26 13:49:20" --end "2026-02-26 13:49:40" \
  --brief
```

출력: 로밍 영향(9), Ping Loss(10), 종합 진단(11) 섹션만 표시

### 2. 특정 STA 문제 추적

특정 STA에서만 ping이 끊기는 경우, 해당 STA만 집중 분석한다.

```bash
python3 pcap_analyzer.py capture.pcap \
  --ssid CANTOPS_TEST --pass "비밀번호" \
  --sta "00:50:43:18:fe:01" \
  --brief
```

출력: STA1 관련 프레임만 추출 → 진단 1대만 표시

### 3. AP 간 품질 비교

두 AP 사이에서 로밍할 때 어느 AP 방향이 문제인지 비교한다.

```bash
python3 pcap_analyzer.py capture.pcap \
  --ssid CANTOPS_TEST --pass "비밀번호" \
  --compare-ap \
  --brief
```

출력: AP 성능 비교 + 프레임 분포 + 불균형 기여도 + 진단 요약

### 3-1. AP 간 패킷 불균형 분석

AP별 패킷 수 차이가 발생하는 원인을 파악할 때 사용한다. 분할 캡처 파일은 먼저 병합한다.

```bash
# 분할 pcap 병합
mergecap -w merged.pcap $(ls -1 *.pcap | sort)

# AP 비교 분석
python3 pcap_analyzer.py merged.pcap --compare-ap -o ap_compare.txt
```

BSSID 기반으로 Management/Control/Data 타입별 분해, 서브타입별 차이, 불균형 기여도가 자동 산출된다.

### 4. 외부 로그와 교차 분석

pcap 분석 결과와 wpa_supplicant/kernel 로그를 하나의 리포트에서 확인한다.

```bash
python3 pcap_analyzer.py capture.pcap \
  --ssid CANTOPS_TEST --pass "비밀번호" \
  --with-log /path/to/wpa.log /path/to/kerl.log /path/to/sys.log
```

출력: 12개 분석 섹션 + 외부 로그 병합 섹션

### 5. 특정 STA + AP 비교 + 로그 (전체 조합)

가장 상세한 분석. 특정 STA에 집중하면서 AP 비교와 외부 로그도 포함한다.

```bash
python3 pcap_analyzer.py capture.pcap \
  --ssid CANTOPS_TEST --pass "비밀번호" \
  --start "2026-02-26 13:49:20" --end "2026-02-26 13:49:40" \
  --sta "00:50:43:18:fe:01" \
  --compare-ap \
  --with-log wpa.log kerl.log \
  -o sta1_report.txt
```

### 6. 복수 STA 비교

두 STA의 차이를 비교할 때, --mac으로 둘 다 포함시킨다.

```bash
python3 pcap_analyzer.py capture.pcap \
  --ssid CANTOPS_TEST --pass "비밀번호" \
  --mac "00:50:43:18:fe:01,00:50:43:19:fe:01" \
  --brief
```

출력: STA1과 STA2만 포함된 진단 비교

### 7. IP 기반 필터 + 전체 분석

MAC 주소를 모를 때 IP로 필터링한다. WPA 복호화 필수.

```bash
python3 pcap_analyzer.py capture.pcap \
  --ssid CANTOPS_TEST --pass "비밀번호" \
  --ip "192.168.0.21" \
  --brief
```

### 8. WPA 복호화 없이 (802.11 레벨만)

비밀번호를 모르거나 open 네트워크인 경우. Retry, MCS, RSSI, 로밍 이벤트는 분석 가능하지만 Ping RTT, Ping Loss, ARP, TCP ACK 분석은 불가.

```bash
python3 pcap_analyzer.py capture.pcap --brief
```

### 9. 시간 필터 없이 전체 pcap 분석

시간대를 모를 때 전체 pcap을 분석한다. 대용량 파일은 시간이 걸릴 수 있다.

```bash
python3 pcap_analyzer.py capture.pcap \
  --ssid CANTOPS_TEST --pass "비밀번호" \
  --brief \
  -o full_analysis.txt
```

### 10. 현장 원샷 명령어

pcap과 로그 파일이 같은 디렉토리에 있을 때, 한 번에 전체 분석을 수행한다.

```bash
cd /path/to/capture_dir
python3 /path/to/pcap_analyzer.py mon1_00016.pcap \
  --ssid CANTOPS_TEST --pass "비밀번호" \
  --brief --compare-ap \
  --with-log wpa.log kerl.log sys.log \
  -o analysis.txt && cat analysis.txt
```

---

## 출력 크기 비교 (옵션별)

FX3000 pcap (6,587프레임, 20초) 기준:

| 옵션 조합 | 줄 수 | 비고 |
|-----------|-------|------|
| 기본 (전체) | 863 | 12개 섹션 상세 |
| `--compare-ap` | 874 | +AP 비교 |
| `--with-log` | 975 | +외부 로그 |
| `--sta` | 487 | STA1만 |
| `--brief` | 196 | 진단 관련만 |
| `--brief --compare-ap` | 207 | +AP 비교 |
| `--sta --brief` | 61 | STA1 진단만 |
| `--sta --brief --compare-ap` | 72 | 최소+AP |
| 전체 조합 | 185 | STA1+brief+AP+log |
