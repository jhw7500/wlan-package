# ping-monitor — ICMP 실시간 로거

유무선 브릿지 환경에서 클라이언트 모드로 ICMP(ping) 패킷을 실시간 모니터링하고
로그 파일로 기록하는 도구입니다. 브릿지 동작에 영향을 주지 않습니다.

## 주요 기능

- **실시간 출력**: 타임스탬프 포함 컬러 터미널 출력
- **듀얼 인터페이스**: eth0 + mlan0 동시 캡처 (threading)
- **JSON 설정**: 인터페이스, 필터, 출력 옵션을 설정 파일로 관리
- **로그 파일**: 모든 캡처 내용을 텍스트 로그로 저장
- **미전달 분석**: 종료 시 인터페이스 간 전달되지 않은 패킷 식별
- **원격 실행**: SSH 기반 원격 타겟 실행

## 의존성

- Python 3 (필수)
- tcpdump (필수)
- tshark (미전달 분석에 필요)

## 사용법

```bash
# 기본 (eth0 + mlan0 듀얼 캡처)
python3 ping_monitor.py

# 단일 인터페이스
python3 ping_monitor.py -s -1 mlan0

# 특정 IP, 60초
python3 ping_monitor.py -t 192.168.1.1 -d 60

# 커스텀 설정 파일
python3 ping_monitor.py -c /path/to/config.json

# 원격 실행
python3 ping_monitor.py -H 10.0.0.100
```

## 옵션

| 옵션 | 설명 | 기본값 |
|------|------|--------|
| `-c FILE` | JSON 설정 파일 경로 | 스크립트 디렉토리의 `ping-monitor.conf.json` |
| `-1 IFACE` | 첫 번째 인터페이스 | `eth0` |
| `-2 IFACE` | 두 번째 인터페이스 | `mlan0` |
| `-s` | 단일 인터페이스 모드 | 듀얼 |
| `-t IP` | 특정 IP 필터 | 전체 ICMP |
| `-d SEC` | 캡처 시간(초) | 0 (무제한) |
| `-o DIR` | 출력 디렉토리 | `/tmp/ping-monitor` |
| `-P` | pcap 저장 비활성화 | 활성화 |
| `-H HOST` | 원격 호스트 | - |

## JSON 설정

`ping-monitor.conf.json` 참조. CLI 옵션이 JSON 설정보다 우선합니다.

```json
{
    "interfaces": { "mode": "dual", "primary": "eth0", "secondary": "mlan0" },
    "filter":     { "target_ip": "" },
    "capture":    { "duration": 0 },
    "output":     { "dir": "/tmp/ping-monitor", "save_pcap": true },
    "display":    { "show_timestamp": true, "show_seq": true, "show_size": false, "color": true },
    "analysis":   { "on_exit": true }
}
```

## 출력 예시

### 실시간 터미널

```
=== ping-monitor 세션 시작 ===
시간: 2026-03-17 14:30:00
모드: dual
인터페이스: eth0
인터페이스2: mlan0
===

14:30:01.123456 [eth0]  REQ 192.168.1.100 > 192.168.1.1 seq=1
14:30:01.124890 [mlan0] REQ 192.168.1.100 > 192.168.1.1 seq=1
14:30:01.130234 [mlan0] REP 192.168.1.1 > 192.168.1.100 seq=1
14:30:01.131567 [eth0]  REP 192.168.1.1 > 192.168.1.100 seq=1
```

### 종료 시 분석

```
=== 미전달 패킷 분석 ===
캡처: eth0=200, mlan0=198 패킷
매칭: 198, eth0에만=2, mlan0에만=0
손실률: 1.0%

브릿지 지연: 평균=1.234ms 최소=0.567ms 최대=5.678ms

[eth0에서 전달되지 않음 → mlan0]
  REQ id=1234 seq=45 192.168.1.100→192.168.1.1 t=1710654601.123456
```

## 출력 파일

| 파일 | 설명 |
|------|------|
| `ping_YYYYMMDD_HHMMSS.log` | 텍스트 로그 (실시간 출력 + 분석) |
| `icmp_eth0_YYYYMMDD_HHMMSS.pcap` | eth0 pcap (tshark 추가 분석 가능) |
| `icmp_mlan0_YYYYMMDD_HHMMSS.pcap` | mlan0 pcap |
