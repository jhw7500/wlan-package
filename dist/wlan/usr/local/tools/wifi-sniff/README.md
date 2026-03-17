# WiFi Channel Sniffer Toolkit

NXP 88Q9098 WLAN 드라이버의 **channel specified sniffer mode (netmon)**를 이용한
WiFi 패킷 캡처 및 복호화 도구입니다.

- **타겟 플랫폼:** iMX8MP + NXP 88Q9098
- **커널:** Linux 6.6.3
- **드라이버:** NXP WLAN (mlan/moal)

---

## 구성 파일

| 파일 | 설명 |
|------|------|
| `wifi-capture.sh` | 채널 스니퍼 모드 캡처 |
| `wifi-decrypt.sh` | pcap 복호화 + 요약 통계 |
| `wifi-sniffer.conf.example` | 복호화 설정 파일 예제 |

---

## 사전 준비

### 필수 도구

| 도구 | 용도 | 캡처 | 복호화 |
|------|------|:----:|:------:|
| mlanutl | netmon 제어 | 필수 | - |
| iw | 모니터 인터페이스 관리 | 필수 | - |
| wpa_cli | STA 연결 해제 | 필수 | - |
| tcpdump | pcap 저장 | 필수 | - |
| tshark | 복호화, 분석 | - | 필수 |

### PATH 설정

타겟 보드에서 `mlanutl`이 `/opt/wlan/bin/`에 있는 경우:

```bash
export PATH=$PATH:/opt/wlan/bin
```

영구 설정하려면 `/etc/profile` 또는 `~/.bashrc`에 추가하세요.

### 스크립트 설치

호스트 PC에서 타겟 보드로 복사:

```bash
scp wifi-capture.sh wifi-decrypt.sh root@<타겟IP>:/opt/wlan/bin/
ssh root@<타겟IP> "chmod +x /opt/wlan/bin/wifi-capture.sh /opt/wlan/bin/wifi-decrypt.sh"
```

---

## wifi-capture.sh — 채널 스니퍼 캡처

### 동작 원리

1. 현재 STA 연결 정보를 읽음 (자동감지 모드)
2. STA 연결 해제
3. FW를 channel specified sniffer mode로 전환 (netmon)
4. mon0 모니터 인터페이스 생성
5. tcpdump로 802.11 raw 프레임을 pcap 파일에 저장
6. Ctrl+C로 종료 시 자동 정리 (netmon 비활성화 + mon0 제거)

> **주의:** 캡처가 시작되면 **WiFi 연결이 끊깁니다.** 장치는 스니핑 전용 모드로 동작합니다.
> SSH가 WiFi를 통해 연결된 경우, 캡처 시작 시 SSH도 끊기므로 반드시 **시리얼 콘솔이나 이더넷** 등 별도 경로로 접속하세요.

### 사용법

```
wifi-capture.sh [OPTIONS]

Options:
  -c <channel>     모니터 채널 (미지정 시 자동감지)
  -b <band>        밴드 (미지정 시 자동감지 또는 채널에서 추론)
  -f <filter>      filter_flag 비트맵 (기본값: 7)
  -w <bandwidth>   채널 대역폭 (기본값: 0)
  -i <interface>   mlan 인터페이스 (기본값: mlan0)
  -H <host>        원격 타겟 IP
  -o <directory>   pcap 저장 경로 (기본값: 현재 디렉토리)
  -h               도움말
```

### 옵션 상세

#### -c (채널)

모니터링할 WiFi 채널 번호.

| 대역 | 주요 채널 |
|------|----------|
| 2.4GHz | 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13 |
| 5GHz | 36, 40, 44, 48, 52, 56, 60, 64, 100, 104, 108, 112, 116, 120, 124, 128, 132, 136, 140, 149, 153, 157, 161, 165 |

현재 AP의 채널을 모르는 경우:

```bash
# STA가 연결된 상태에서
iw dev mlan0 link | grep freq
# freq: 5200  → 채널 40

# 또는
wpa_cli -i mlan0 status | grep freq
# freq=5200
```

#### -b (밴드)

문자열 또는 숫자로 지정합니다.

| 문자열 | 숫자 | 설명 | 사용 시기 |
|--------|------|------|----------|
| B | 1 | 802.11b | 2.4GHz 레거시 |
| G | 2 | 802.11g | 2.4GHz 레거시 |
| A | 4 | 802.11a | 5GHz 레거시 |
| **GN** | **8** | **802.11n 2.4GHz** | **2.4GHz 일반** |
| **AN** | **16** | **802.11n 5GHz** | **5GHz 일반** |
| GAC | 32 | 802.11ac 2.4GHz | 2.4GHz VHT |
| **AAC** | **64** | **802.11ac 5GHz** | **5GHz VHT (고속)** |

**자동 추론:** `-b`를 생략하면 채널 번호에서 자동 추론합니다.
- 채널 1~14 → GN (2.4GHz)
- 채널 36 이상 → AN (5GHz)

STA가 연결된 상태에서 `-c`도 생략하면 HT/VHT 정보까지 읽어 더 정확하게 감지합니다.

**복합 밴드:** 숫자를 직접 지정하여 OR 조합 가능.
- `-b 11` = B(1) + G(2) + GN(8)
- `-b 20` = A(4) + AN(16)

#### -f (필터)

캡처할 프레임 유형을 비트맵으로 지정합니다.

| bit | 값 | 프레임 유형 | 포함하는 내용 |
|-----|---|-----------|-------------|
| 0 | 1 | 관리 프레임 | Beacon, Probe Req/Resp, Auth, Assoc, Deauth, Disassoc |
| 1 | 2 | 제어 프레임 | ACK, RTS, CTS, Block ACK |
| 2 | 4 | 데이터 프레임 | EAPOL, IP 패킷, TCP, UDP, ICMP (ping) 등 |

조합 예:

| 값 | 의미 | 사용 시기 |
|----|------|----------|
| **7** | 관리+제어+데이터 **(기본값)** | 전체 캡처 |
| 1 | 관리 프레임만 | Beacon/Probe 분석 |
| 5 | 관리+데이터 | 로밍 + 트래픽 분석 |
| 4 | 데이터 프레임만 | 트래픽만 캡처 |

#### -w (채널 대역폭)

| 값 | 대역폭 | 설명 |
|----|--------|------|
| **0** | **20MHz (기본값)** | 단일 채널 |
| 1 | 40MHz (상위) | HT40+ secondary channel above |
| 3 | 40MHz (하위) | HT40- secondary channel below |
| 4 | 80MHz | VHT 80MHz |

> **참고:** 값 2는 사용하지 않습니다.

### 사용 예제

```bash
# 예제 1: 자동감지 (STA 연결 상태에서)
wifi-capture.sh

# 예제 2: 채널만 지정 (밴드 자동 추론)
wifi-capture.sh -c 40

# 예제 3: 채널 + 밴드 직접 지정
wifi-capture.sh -c 40 -b AAC

# 예제 4: 관리 프레임만 캡처
wifi-capture.sh -c 40 -f 1

# 예제 5: 80MHz 대역폭으로 캡처
wifi-capture.sh -c 40 -b AAC -w 4

# 예제 6: 2.4GHz 채널 6 캡처
wifi-capture.sh -c 6 -b GN

# 예제 7: pcap 저장 경로 지정
wifi-capture.sh -c 40 -o /tmp

# 예제 8: 원격 타겟에서 캡처 (이더넷 SSH 필수)
wifi-capture.sh -H 192.168.1.100 -c 40 -b AN
```

### 출력 파일

파일명 형식: `capture_ch<채널>_<밴드>_<날짜>_<시간>.pcap`

```
capture_ch40_AAC_20260317_143022.pcap
capture_ch6_GN_20260317_150000.pcap
```

### 종료

**Ctrl+C**를 누르면 자동으로:
1. tcpdump 종료
2. netmon 비활성화
3. mon0 인터페이스 제거
4. 캡처 결과 요약 출력 (파일명, 크기, 패킷 수)

```
INFO: 캡처 종료 중...

INFO: === 캡처 결과 ===
INFO: 파일: capture_ch40_AN_20260317_143022.pcap
INFO: 크기: 48M
INFO: 패킷: 160129
```

### 캡처 후 WiFi 재연결

캡처 종료 후 WiFi를 다시 사용하려면 수동 재연결이 필요합니다:

```bash
wpa_cli -i mlan0 reconnect
```

---

## wifi-decrypt.sh — pcap 복호화

### 동작 원리

1. SSID와 PSK를 확인 (CLI 인자 또는 설정파일)
2. pcap에서 EAPOL 4-Way Handshake 존재 여부 확인
3. tshark로 WPA2-PSK 복호화하여 새 pcap 저장
4. 복호화 결과 요약 출력

### 복호화 전제 조건

WPA2-PSK 복호화가 작동하려면 **3가지가 모두 필요**합니다:

| 조건 | 설명 |
|------|------|
| SSID | AP의 네트워크 이름 |
| PSK | WiFi 비밀번호 |
| **EAPOL 4-Way Handshake** | **캡처 중 장치가 AP에 (재)연결하는 과정이 포함되어야 함** |

EAPOL이 캡처에 없으면 PSK를 알아도 데이터 복호화가 불가능합니다.
로밍이 빈번한 환경에서는 캡처 중 자연스럽게 EAPOL이 포함됩니다.

### 사용법

```
wifi-decrypt.sh [OPTIONS] <pcap-file>

Options:
  -s <ssid>    WiFi SSID
  -p <psk>     WiFi PSK (비밀번호)
  -h           도움말
```

### 자격증명 지정 방법

**방법 1: CLI 인자 (우선)**

```bash
wifi-decrypt.sh -s "CANTOPS_TEST" -p "mypassword" capture.pcap
```

**방법 2: 설정파일 (~/.wifi-sniffer.conf)**

```bash
# 설정파일 생성
cp wifi-sniffer.conf.example ~/.wifi-sniffer.conf
vi ~/.wifi-sniffer.conf
```

설정파일 형식:
```
SSID=CANTOPS_TEST
PSK=mypassword
```

설정파일이 있으면 인자 없이 실행 가능:
```bash
wifi-decrypt.sh capture.pcap
```

> CLI 인자가 설정파일보다 우선합니다. 둘 다 없으면 에러가 발생합니다.

### 사용 예제

```bash
# 예제 1: 인자로 직접 지정
wifi-decrypt.sh -s "CANTOPS_TEST" -p "mypassword" capture_ch40_AN_20260317_143022.pcap

# 예제 2: 설정파일 사용
wifi-decrypt.sh capture_ch40_AN_20260317_143022.pcap

# 예제 3: 여러 파일 순차 복호화
for f in capture_*.pcap; do
    wifi-decrypt.sh -s "MyWiFi" -p "mypass" "$f"
done
```

### 출력

**복호화된 pcap 파일:**

원본 파일명에 `_decrypted`가 추가됩니다.

```
capture_ch40_AN_20260317_143022.pcap
  → capture_ch40_AN_20260317_143022_decrypted.pcap
```

> **중요:** 복호화된 pcap을 wireshark에서 열 때도 동일한 SSID/PSK 키 설정이 필요합니다.
> tshark의 `-w` 옵션은 raw 프레임을 저장하므로, 파일 자체가 복호화된 것이 아니라
> 읽을 때 키를 적용해야 합니다.

**텍스트 요약 (stdout):**

```
INFO: === 복호화 결과 요약 ===
INFO: 총 패킷: 160129
INFO: EAPOL: 203
INFO: 복호화된 패킷 (IP/ARP): 3275

INFO: === 프로토콜 분포 ===
radiotap                                 frames:160129
  wlan_radio                             frames:159723
    wlan                                 frames:159723
      data                               frames:38027
      wlan.mgt                           frames:22580
      llc                                frames:3244
        eapol                            frames:203
        arp                              frames:725
        ip                               frames:1975
          tcp                            frames:1333
            http                         frames:84
            ssh                          frames:280
          udp                            frames:151
            ntp                          frames:14
            mdns                         frames:45
          icmp                           frames:491
        ipv6                             frames:223
```

### 경고 메시지

| 메시지 | 의미 | 조치 |
|--------|------|------|
| `EAPOL handshake가 없습니다` | pcap에 4-Way Handshake 미포함 | 로밍/재연결이 포함된 캡처 필요 |
| `복호화된 패킷이 0개입니다` | PSK 불일치 또는 EAPOL 불완전 | PSK 확인, 또는 더 긴 캡처 시도 |

---

## 전체 워크플로우

### 기본 시나리오: AP 채널의 트래픽 캡처

```
[타겟 보드 — 시리얼 콘솔]

# 1. 현재 연결 채널 확인
iw dev mlan0 link | grep freq
# freq: 5200 → 채널 40

# 2. 캡처 시작 (WiFi 끊김)
export PATH=$PATH:/opt/wlan/bin
wifi-capture.sh -c 40 -b AAC

# 3. 원하는 만큼 캡처 후 Ctrl+C

# 4. WiFi 재연결 (필요시)
wpa_cli -i mlan0 reconnect
```

```
[호스트 PC]

# 5. pcap 파일 가져오기
scp root@<타겟IP>:/root/capture_ch40_*.pcap .

# 6. 복호화
wifi-decrypt.sh -s "SSID이름" -p "비밀번호" capture_ch40_AAC_20260317_143022.pcap

# 7. wireshark로 분석 (선택)
wireshark capture_ch40_AAC_20260317_143022_decrypted.pcap
# wireshark에서도 키 설정 필요:
#   Edit → Preferences → Protocols → IEEE 802.11
#   → Decryption keys → wpa-pwd → "비밀번호:SSID이름"
```

### 스니핑 시나리오별 가이드

#### 다른 장치 간 ping 캡처

```bash
# 데이터 프레임 포함 필수 (filter bit 2)
wifi-capture.sh -c 40 -f 4    # 데이터만
wifi-capture.sh -c 40 -f 7    # 전체 (기본값)
```

복호화 후 ICMP 필터링:
```bash
tshark -r capture_decrypted.pcap \
  -o "wlan.enable_decryption:TRUE" \
  -o 'uat:80211_keys:"wpa-pwd","비밀번호:SSID"' \
  -Y "icmp"
```

#### Beacon/Probe 스캔 (주변 AP 탐색)

```bash
# 관리 프레임만 캡처
wifi-capture.sh -c 40 -f 1
```

#### 로밍 디버깅 (Auth/Assoc/Deauth 추적)

```bash
# 관리+데이터 (EAPOL 포함)
wifi-capture.sh -c 40 -f 5
```

---

## 문제 해결

### mlanutl을 찾을 수 없음

```
ERROR: 'mlanutl'이(가) 설치되어 있지 않습니다
```

PATH에 `/opt/wlan/bin`을 추가하세요:
```bash
export PATH=$PATH:/opt/wlan/bin
```

### STA 미연결 상태에서 자동감지 실패

```
ERROR: STA가 연결되어 있지 않습니다. -c와 -b를 직접 지정하세요
```

채널과 밴드를 직접 지정하세요:
```bash
wifi-capture.sh -c 40 -b AN
```

### netmon 활성화 실패

```
ERROR: netmon 활성화 실패 (채널=40, 밴드=16)
```

- 채널/밴드 조합이 올바른지 확인
- 드라이버가 로드되어 있는지 확인: `lsmod | grep moal`
- 이전 세션이 남아있을 수 있음: `mlanutl mlan0 netmon 0`

### mon0 생성 실패

```
ERROR: mon0 생성 실패
```

- 이미 mon0이 존재할 수 있음: `iw dev mon0 del`
- 드라이버가 모니터 모드를 지원하는지 확인

### SSH로 원격 캡처 시 연결 끊김

WiFi를 통한 SSH 연결은 STA disconnect 시 끊깁니다.
**이더넷 또는 시리얼 콘솔**을 통해 접속해야 합니다.

### 복호화된 패킷이 0개

- PSK가 올바른지 확인
- EAPOL 4-Way Handshake가 캡처에 포함되어 있는지 확인
- 로밍이 발생하도록 충분히 긴 시간 캡처

---

## 기술 배경

### channel specified sniffer mode란?

NXP 88Q9098 FW의 네트워크 모니터 모드입니다. STA 연결을 해제하고 FW를 특정 채널에
고정시켜 해당 채널의 **모든 802.11 프레임을 무차별(promiscuous) 수신**합니다.

일반 STA 모드에서는 자기 MAC 주소로 향하는 패킷만 수신하지만,
channel specified sniffer mode에서는 **다른 장치 간의 통신도 캡처**할 수 있습니다.

### 캡처 가능한 프레임

| 프레임 종류 | 설명 | 예시 |
|------------|------|------|
| 관리 프레임 | AP/STA 간 제어 메시지 | Beacon, Probe, Auth, Assoc, Deauth |
| 제어 프레임 | MAC 레벨 제어 | ACK, RTS/CTS, Block ACK |
| 데이터 프레임 | 실제 사용자 데이터 | IP, TCP, UDP, ICMP, EAPOL |

### 복호화 원리

WPA2-PSK 네트워크에서 데이터 프레임은 세션 키(PTK)로 암호화됩니다.
PTK는 EAPOL 4-Way Handshake에서 PSK + 난수 교환으로 생성됩니다.

따라서 복호화에는 **PSK + EAPOL handshake**가 모두 필요합니다.
tshark/wireshark는 EAPOL에서 PTK를 재계산하여 데이터를 복호화합니다.

```
PSK + SSID → PMK (Pairwise Master Key)
PMK + EAPOL 4-Way Handshake 난수 → PTK (Pairwise Transient Key)
PTK → 데이터 프레임 복호화
```
