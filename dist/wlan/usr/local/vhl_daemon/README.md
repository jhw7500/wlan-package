# vhld - VHL Protocol Daemon

VHL 무선기판 제어 프로토콜 데몬. eth0(유선)으로 VHL과 UDP 통신을 수행하여
장치 정보/상태 조회, 무선 설정 변경, 이벤트 통지 등을 처리한다.

## Build

```bash
# 호스트(x86) 빌드
make

# 크로스 빌드 (aarch64)
make cross

# 디버그 빌드
make debug
```

## Usage

```bash
vhld [config_file]
# 기본: /usr/local/vhl_daemon/vhld.conf
```

## Protocol

- Transport: UDP/IP (Big Endian)
- 기본 포트: 50000 (vhld.conf에서 변경 가능)
- 프로토콜 사양: wlan-opc/docs/VHL_protocol_260219_Cantops_KR.docx (opc 문서는 wlan-opc 서브모듈에서 관리)

## Test

```bash
# socat 필요
./tests/test_vhld.sh [host] [port]
```
