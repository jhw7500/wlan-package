# ping-logger 사용 가이드

유무선 브릿지 환경에서 ICMP 패킷의 실시간 흐름을 추적하고,
인터페이스 간 미전달 패킷을 식별하는 도구입니다.

---

## 1. 개요

### 동작 원리

```
[외부 네트워크]
       |
    [eth0]  ← tcpdump #1 (pcap + 실시간 텍스트)
       |
   [브릿지]  ← 동작에 영향 없음 (패시브 캡처)
       |
   [mlan0]  ← tcpdump #2 (pcap + 실시간 텍스트)
       |
 [무선 네트워크]
```

- 클라이언트 모드에서 동작 (모니터 모드 아님)
- tcpdump의 `-l` (line-buffered) 모드로 패시브 캡처
- 브릿지 트래픽에 간섭하지 않음
- Python threading으로 두 인터페이스 스트림을 실시간 병합

### 파일 구성

| 파일 | 역할 |
|------|------|
| `ping_logger.py` | 메인 엔트리포인트 |
| `capture.py` | tcpdump 캡처 + 실시간 파싱 엔진 |
| `analyzer.py` | 종료 시 미전달 패킷 분석 |
| `models.py` | 데이터 모델 (PacketInfo, SessionConfig) |
| `ping-logger.conf.json` | JSON 설정 파일 |

경로: `/usr/local/tools/ping-logger/`

---

## 2. 빠른 시작

### 기본 실행 (듀얼 모드)

```bash
cd /usr/local/tools/ping-logger
python3 ping_logger.py
```

eth0 + mlan0에서 동시에 ICMP 패킷을 캡처합니다.
`Ctrl+C`로 종료하면 자동으로 미전달 패킷 분석을 수행합니다.

### 특정 IP만 필터링

```bash
python3 ping_logger.py -t 192.168.1.1
```

게이트웨이나 특정 호스트와의 ping만 추적할 때 유용합니다.

### 시간 제한 캡처

```bash
python3 ping_logger.py -t 192.168.1.1 -d 60
```

60초 후 자동 종료 + 분석 결과 출력.

---

## 3. 사용 시나리오

### 시나리오 1: 브릿지 패킷 손실 조사

브릿지를 통과하는 ping이 간헐적으로 실패할 때:

```bash
# 1. 듀얼 모드로 캡처 시작
python3 ping_logger.py -t 192.168.1.1

# 2. 다른 터미널에서 ping 실행
ping -i 0.5 192.168.1.1

# 3. 충분한 샘플 후 Ctrl+C

# 4. 종료 시 자동 분석 결과 확인:
#    - eth0에만 있고 mlan0에 없는 패킷 = 무선 구간에서 손실
#    - mlan0에만 있고 eth0에 없는 패킷 = 유선 구간에서 손실
```

**분석 결과 해석:**

```
=== 미전달 패킷 분석 ===
캡처: eth0=200, mlan0=195 패킷
매칭: 195, eth0에만=5, mlan0에만=0
손실률: 2.5%

[eth0에서 전달되지 않음 → mlan0]
  REQ id=1234 seq=45 192.168.1.100→192.168.1.1 t=1710654601.123
  REQ id=1234 seq=46 192.168.1.100→192.168.1.1 t=1710654601.623
```

- `eth0에만=5`: eth0에서 수신했지만 mlan0에서 나가지 않은 패킷 5개
- 연속된 seq(45, 46)이면 순간적 무선 끊김 가능성

### 시나리오 2: 브릿지 지연 측정

```bash
python3 ping_logger.py -t 192.168.1.1 -d 300
```

5분간 캡처 후 브릿지 통과 지연 통계:

```
브릿지 지연: 평균=1.234ms 최소=0.567ms 최대=15.678ms
```

- 평균 1-2ms: 정상
- 최대 10ms 이상: 무선 재전송 또는 버퍼링 의심

### 시나리오 3: 단일 인터페이스 모니터링

무선 구간만 확인할 때:

```bash
python3 ping_logger.py -s -1 mlan0
```

유선 구간만 확인할 때:

```bash
python3 ping_logger.py -s -1 eth0
```

단일 모드에서는 미전달 분석이 수행되지 않습니다.

### 시나리오 4: 원격 타겟에서 실행

개발 PC에서 타겟 보드의 ping-logger를 원격 실행:

```bash
# 타겟 IP: 10.0.0.100
python3 ping_logger.py -H 10.0.0.100 -t 192.168.1.1 -d 60
```

동작 과정:
1. 스크립트 파일을 타겟에 scp로 전송
2. 타겟에서 SSH로 실행
3. 종료 후 결과 파일(log, pcap)을 로컬로 복사
4. 타겟의 임시 파일 자동 정리

요구사항: 타겟에 SSH 키 기반 root 접근 가능해야 함.

### 시나리오 5: pcap 없이 가볍게 로깅

디스크 공간이 부족하거나 텍스트 로그만 필요할 때:

```bash
python3 ping_logger.py -P -t 192.168.1.1
```

`-P` 옵션으로 pcap 저장을 비활성화합니다.
텍스트 로그 파일은 항상 생성됩니다.

---

## 4. JSON 설정 가이드

### 설정 파일 위치

기본: 스크립트 디렉토리의 `ping-logger.conf.json`
커스텀: `-c` 옵션으로 지정

### 설정 우선순위

```
기본값 → JSON 설정 → CLI 옵션 (최우선)
```

JSON에서 `"duration": 60`으로 설정해도 `-d 30`을 주면 30초가 적용됩니다.

### 설정 항목 상세

#### interfaces

```json
"interfaces": {
    "mode": "dual",        // "dual" 또는 "single"
    "primary": "eth0",     // 첫 번째 인터페이스
    "secondary": "mlan0"   // 두 번째 (dual 모드에서만 사용)
}
```

- `mode`: 캡처 모드. `"dual"`이면 두 인터페이스 동시 캡처
- `primary`: 유선 인터페이스 (브릿지의 유선 쪽)
- `secondary`: 무선 인터페이스 (브릿지의 무선 쪽)

#### filter

```json
"filter": {
    "target_ip": ""        // 빈 문자열 = 전체 ICMP
}
```

- 빈 문자열: 모든 ICMP 패킷 캡처
- IP 지정: 해당 호스트와의 ICMP만 캡처 (`tcpdump icmp and host <IP>`)

#### capture

```json
"capture": {
    "duration": 0          // 0 = Ctrl+C까지
}
```

- `0`: 무제한 (수동 종료)
- 양수: 해당 초 후 자동 종료

#### output

```json
"output": {
    "dir": "/tmp/ping-logger",  // 출력 디렉토리
    "save_pcap": true            // pcap 파일 저장 여부
}
```

- `dir`: 로그와 pcap 파일이 저장되는 디렉토리 (자동 생성)
- `save_pcap`: `false`이면 텍스트 로그만 생성 (디스크 절약)

#### display

```json
"display": {
    "show_timestamp": true,  // 타임스탬프 (14:30:01.123456)
    "show_seq": true,        // ICMP seq 번호 (seq=1)
    "show_size": false,      // 패킷 크기 (len=64)
    "color": true            // ANSI 컬러 출력
}
```

- `show_size`: 기본 false. 패킷 크기가 중요한 분석에서 활성화
- `color`: 파이프로 출력을 리다이렉트할 때 자동으로 비활성화됨

#### analysis

```json
"analysis": {
    "on_exit": true          // 종료 시 미전달 분석 수행
}
```

- `true`: 듀얼 모드 + pcap 저장 시 종료할 때 자동 분석
- `false`: 분석 건너뜀 (빠른 종료)

### 용도별 설정 예시

**장시간 모니터링 (디스크 절약):**
```json
{
    "interfaces": { "mode": "dual", "primary": "eth0", "secondary": "mlan0" },
    "filter": { "target_ip": "192.168.1.1" },
    "capture": { "duration": 3600 },
    "output": { "dir": "/tmp/ping-logger", "save_pcap": false },
    "display": { "show_timestamp": true, "show_seq": true, "show_size": false, "color": true },
    "analysis": { "on_exit": false }
}
```

**상세 분석 (모든 정보 수집):**
```json
{
    "interfaces": { "mode": "dual", "primary": "eth0", "secondary": "mlan0" },
    "filter": { "target_ip": "" },
    "capture": { "duration": 0 },
    "output": { "dir": "/var/log/ping-logger", "save_pcap": true },
    "display": { "show_timestamp": true, "show_seq": true, "show_size": true, "color": true },
    "analysis": { "on_exit": true }
}
```

**mlan0 단독 모니터링:**
```json
{
    "interfaces": { "mode": "single", "primary": "mlan0" },
    "filter": { "target_ip": "" },
    "capture": { "duration": 0 },
    "output": { "dir": "/tmp/ping-logger", "save_pcap": true },
    "display": { "show_timestamp": true, "show_seq": true, "show_size": false, "color": true },
    "analysis": { "on_exit": false }
}
```

---

## 5. 출력 파일

### 저장 위치

기본: `/tmp/ping-logger/`

```
/tmp/ping-logger/
├── ping_20260317_143000.log                # 텍스트 로그
├── icmp_eth0_20260317_143000.pcap          # eth0 pcap
└── icmp_mlan0_20260317_143000.pcap         # mlan0 pcap
```

### 텍스트 로그 형식

```
=== ping-logger 세션 시작 ===
시간: 2026-03-17 14:30:00
모드: dual
인터페이스: eth0
인터페이스2: mlan0
===

14:30:01.123456 [eth0] REQ 192.168.1.100 > 192.168.1.1 seq=1
14:30:01.124890 [mlan0] REQ 192.168.1.100 > 192.168.1.1 seq=1
14:30:01.130234 [mlan0] REP 192.168.1.1 > 192.168.1.100 seq=1
14:30:01.131567 [eth0] REP 192.168.1.1 > 192.168.1.100 seq=1
...

=== 미전달 패킷 분석 ===
캡처: eth0=200, mlan0=198 패킷
매칭: 198, eth0에만=2, mlan0에만=0
손실률: 1.0%

브릿지 지연: 평균=1.234ms 최소=0.567ms 최대=5.678ms

=== 세션 종료: 2026-03-17 14:35:00 ===
```

### pcap 파일 활용

pcap 파일은 tshark나 Wireshark로 추가 분석이 가능합니다.

```bash
# 패킷 목록 확인
tshark -r /tmp/ping-logger/icmp_eth0_20260317_143000.pcap

# 특정 seq만 필터
tshark -r /tmp/ping-logger/icmp_eth0_20260317_143000.pcap -Y "icmp.seq == 45"

# RTT 분석 (pcap-analyzer 연동)
cd /usr/local/tools/pcap-analyzer
python3 pcap_analyzer.py /tmp/ping-logger/icmp_mlan0_20260317_143000.pcap
```

---

## 6. 실시간 출력 읽는 법

### 컬러 코드

| 색상 | 의미 |
|------|------|
| 시안 `[eth0]` | 유선 인터페이스 |
| 초록 `[mlan0]` | 무선 인터페이스 |
| 노란 `REQ` | Echo Request (ping 요청) |
| 초록 `REP` | Echo Reply (ping 응답) |
| 빨강 `UNREACH` | Destination Unreachable |
| 빨강 `TIMEXC` | Time Exceeded |

### 정상 패턴

```
14:30:01.123 [eth0]  REQ ... seq=1    ← 유선에서 요청 수신
14:30:01.124 [mlan0] REQ ... seq=1    ← 무선으로 전달됨 (1ms 지연)
14:30:01.130 [mlan0] REP ... seq=1    ← 무선에서 응답 수신
14:30:01.131 [eth0]  REP ... seq=1    ← 유선으로 전달됨
```

- eth0 REQ → mlan0 REQ: 브릿지 유선→무선 전달 (정상 1-2ms)
- mlan0 REP → eth0 REP: 브릿지 무선→유선 전달

### 이상 패턴

**패킷 손실:**
```
14:30:01.123 [eth0]  REQ ... seq=5    ← eth0에서 수신
                                       ← mlan0에서 안 나옴 = 손실!
14:30:02.123 [eth0]  REQ ... seq=6    ← 다음 패킷은 정상
14:30:02.124 [mlan0] REQ ... seq=6
```

**높은 지연:**
```
14:30:01.123 [eth0]  REQ ... seq=7
14:30:01.145 [mlan0] REQ ... seq=7    ← 22ms 지연 = 무선 재전송 의심
```

---

## 7. 문제 해결

### "root 권한이 필요합니다"

tcpdump는 raw 소켓을 사용하므로 root 권한이 필수입니다.

```bash
sudo python3 ping_logger.py
```

### "인터페이스 없음: mlan0"

인터페이스가 올라와 있지 않습니다.

```bash
ip link show           # 인터페이스 목록 확인
ip link set mlan0 up   # 인터페이스 활성화
```

### "'tcpdump'이(가) 설치되어 있지 않습니다"

```bash
apt install tcpdump     # Debian/Ubuntu
opkg install tcpdump    # OpenWrt
```

### "'tshark' 없음, 종료 시 미전달 분석 불가"

경고만 표시되며 캡처는 정상 동작합니다.
미전달 분석이 필요하면 tshark를 설치하세요.

```bash
apt install tshark
```

### pcap 파일이 비어있음

- `target_ip` 필터가 올바른지 확인
- 실제로 ICMP 트래픽이 해당 인터페이스를 통과하는지 확인

```bash
# 직접 tcpdump로 확인
tcpdump -i eth0 icmp -c 5
```

---

## 8. 기존 도구와의 비교

| 항목 | ping-logger | ping-monitor.sh |
|------|-------------|-----------------|
| 언어 | Python 3 | Bash |
| 실시간 출력 | O (컬러, 파싱) | X (캡처 후 분석) |
| 로그 파일 | O (텍스트) | X (pcap만) |
| JSON 설정 | O | X (CLI만) |
| 미전달 분석 | O (종료 시) | O (종료 시) |
| 단일 인터페이스 | O | X (항상 듀얼) |
| 원격 실행 | O | O |
| 지연 통계 | O (종료 시) | O (종료 시) |

**ping-monitor.sh**: 캡처 후 일괄 분석에 적합
**ping-logger**: 실시간 모니터링 + 로깅에 적합
