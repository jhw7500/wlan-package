# VHL Protocol Testing Guide

VHL(이더넷 연결 장치)에서 무선기판 제어 데몬(vhld)을 테스트하는 방법입니다.

## 1. 타겟 보드에서 vhld 데몬 시작

### 방법 A: systemd 서비스 사용 (권장)
```bash
# 패키지 설치 후
systemctl start vhld.service
systemctl status vhld.service

# 로그 확인
journalctl -u vhld -f
```

### 방법 B: 수동 시작
```bash
cd /usr/local/vhl_daemon
./vhld vhld.conf &

# 또는 포어그라운드에서 실행
./vhld vhld.conf
```

### 방법 C: 호스트 개발 환경에서 테스트
```bash
cd /home/jhw/ai/opencode/projects/wlan-package/dist/wlan/usr/local/vhl_daemon
make clean && make
./vhld vhld.conf &
```

## 2. vhld 포트 확인

기본 포트: **50000** (vhld.conf에서 변경 가능)

```bash
# UDP 리스닝 확인
ss -ulnp | grep 50000
# 또는
netstat -ulnp | grep 50000
```

## 3. VHL 장치에서 테스트

### Python 테스트 클라이언트 사용 (권장)

VHL 장치에서 다음을 실행:

```bash
# 타겟 보드의 IP 주소를 지정
python3 vhl_test_client.py 192.168.0.100 50000
```

또는 로컬 테스트:
```bash
python3 vhl_test_client.py 127.0.0.1 50000
```

**테스트 항목:**
- Sequence Number Echo
- Get Device Info (장치 정보 취득)
- Get Device Status (상태 취득)
- Get WLAN Config (무선 설정 취득)
- Set Indication (Indication 설정)

### socat 테스트 스크립트 사용

```bash
./tests/test_vhld.sh 192.168.0.100 50000
```

## 4. 네트워크 연결 확인

```bash
# 핑 테스트
ping -c 3 192.168.0.100

# UDP 포트 접속 확인 (nc)
nc -uzv 192.168.0.100 50000
```

## 5. 방화벽 확인

타겟 보드에서 UDP 50000 포트가 열려 있는지 확인:

```bash
# iptables 확인
iptables -L -n | grep 50000

# 필요시 방화벽 규칙 추가
iptables -I INPUT -p udp --dport 50000 -j ACCEPT
```

## 6. 테스트 예시

### 로컬 테스트 (개발 호스트에서)
```bash
# 터미널 1: 데몬 시작
cd /home/jhw/ai/opencode/projects/wlan-package/dist/wlan/usr/local/vhl_daemon
./vhld vhld.conf

# 터미널 2: 테스트
./tests/test_vhld.sh 127.0.0.1 50000
# 또는
python3 tests/vhl_test_client.py 127.0.0.1 50000
```

### 원격 테스트 (VHL 장치에서)
```bash
# VHL 장치의 IP가 192.168.0.100인 경우
python3 vhl_test_client.py 192.168.0.100 50000
```

## 7. 문제 해결

### "Response timeout"
- 데몬이 실행 중인지 확인: `ps aux | grep vhld`
- 포트가 올바른지 확인: `ss -ulnp | grep vhld`
- 방화벽 규칙 확인

### "Connection refused"
- IP 주소가 올바른지 확인
- vhld가 해당 IP로 바인드되어 있는지 확인

### "Parse error"
- vhld 로그 확인: `journalctl -u vhld -n 50`
- 프로토콜 버전 확인 (현재: 1)

## 8. 설정 파일 수정

포트나 인터페이스를 변경하려면:

```bash
vi /usr/local/vhl_daemon/vhld.conf
```

```conf
# 포트 변경
PORT=50001

# 바인드 인터페이스 변경
BIND_IFACE=eth1

# 무선 인터페이스 변경
WLAN_IFACE=mlan1
```

변경 후 데몬 재시작:
```bash
systemctl restart vhld.service
```
