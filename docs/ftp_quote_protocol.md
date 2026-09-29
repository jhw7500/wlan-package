# FTP QUOTE 커스텀 명령 프로토콜

이 문서는 장비의 vsftpd가 제공하는 FTP `QUOTE` 커스텀 명령의 호출 형식과
응답 계약을 정의한다. 구현 기준 경로는 `/opt/ftpcmd/bin`이며, 이 저장소에서는
`dist/wlan/opt/ftpcmd/bin`에 원본을 둔다.

## 1. 기본 호출과 응답

FTP 클라이언트의 명령 프롬프트에서 다음 형식으로 호출한다.

```text
ftp> quote <command> [arguments...]
```

vsftpd는 핸들러의 종료 코드와 첫 번째 표준 출력 줄을 FTP 응답으로 변환한다.

| 핸들러 결과 | FTP 응답 코드 | 응답 본문 |
| --- | ---: | --- |
| 종료 코드 `0` | `200` | 핸들러의 첫 번째 stdout 줄 |
| 종료 코드 `0` 이외 | `550` | 핸들러의 첫 번째 stdout 줄 |

예를 들어 `wconnect`의 성공과 실패는 각각 다음과 같다.

```text
ftp> quote wconnect jhw_wlan
200 SUCCESS

ftp> quote wconnect jhw_wlan 9999
550 FAIL code=1
```

`wconnect`와 `wconnectraw`는 FTP 응답을 단순화하여 성공 시 `SUCCESS`, 실패 시
`FAIL code=N`만 반환한다. 상세 실패 원인은 장비의 rsyslog에 기록된다.

```text
FTP client        vsftpd           handler             Wi-Fi stack
    |                |                |                     |
    |-- QUOTE ------>|                |                     |
    |                |-- argv ------->|                     |
    |                |                |-- apply/request --->|
    |                |                |<-- result/event -----|
    |                |<-- exit + stdout                     |
    |<-- 200 or 550 -|                |                     |
```

응답은 핸들러가 종료된 뒤 전송된다. 따라서 현재 FTP 제어 연결이 사용하는
인터페이스를 끊는 명령은 응답 유실과 원격 접근 단절을 막기 위해 거부될 수 있다.

## 2. 지원 명령 요약

| 명령 | 형식 | 기본 인터페이스 | 용도 |
| --- | --- | --- | --- |
| `getifstate` | `quote getifstate [interface]` | `mlan0` | 인터페이스 관리 상태 조회 |
| `ifcup` | `quote ifcup [interface]` | `mlan0` | 인터페이스를 UP으로 변경 |
| `ifcdown` | `quote ifcdown [interface]` | `mlan0` | 인터페이스를 DOWN으로 변경 |
| `rst` | `quote rst` | 해당 없음 | 장비 재부팅 요청 |
| `wconnect` | `quote wconnect [mlan0\|mlan1] [ssid [freq_or_channel...]]` | `mlan0` | 설정을 반영하고 연결 결과까지 확인 |
| `wconnectraw` | `quote wconnectraw [mlan0\|mlan1] <ssid>` | `mlan0` | 시험용 런타임 SSID 전환 요청 |

명령 이름으로 `connect`를 사용하면 안 된다. vsftpd가 `GET`, `POST`, `HEAD`,
`OPTIONS`, `CONNECT`를 HTTP 교차 프로토콜 공격으로 판단하여 커스텀 핸들러에
전달하기 전에 FTP 제어 연결을 종료한다.

## 3. `wconnect`

### 3.1 형식

```text
quote wconnect [mlan0|mlan1] [ssid [freq_or_channel...]]
```

인자 판정 순서는 다음과 같다.

1. 첫 토큰이 `mlan0` 또는 `mlan1`이면 인터페이스로 소비한다.
2. 인터페이스가 생략되면 `mlan0`을 사용한다.
3. 그다음 첫 토큰 하나를 SSID로 사용한다.
4. SSID 뒤의 모든 토큰을 각각 채널 또는 중심 주파수로 처리한다.

따라서 다음 두 명령은 같은 인터페이스를 사용한다.

```text
quote wconnect jhw_wlan
quote wconnect mlan0 jhw_wlan
```

SSID는 공백 없는 단일 FTP 인자만 지원한다. 또한 SSID가 정확히 `mlan0` 또는
`mlan1`이면 인터페이스 토큰으로 해석되므로 이 명령으로 해당 이름의 SSID를
지정할 수 없다.

### 3.2 SSID와 주파수 적용

`wconnect`는 보드에 저장된 기존 wpa_supplicant 프로필에서 SSID와 선택적인
주파수 정책을 변경한다. PSK와 `key_mgmt`는 FTP로 전달하지 않고 기존 프로필 값을
사용한다.

| 호출 형태 | 동작 |
| --- | --- |
| 인자 없음 | 현재 설정을 변경하지 않고 `mlan0` 재연결 |
| 인터페이스만 지정 | 해당 인터페이스의 현재 설정으로 재연결 |
| SSID 지정 | SSID를 영속 설정과 현재 network에 적용한 뒤 재연결 |
| SSID + 채널/주파수 지정 | SSID와 `freq_list`를 함께 적용한 뒤 재연결 |
| SSID만 지정 | 기존 공통 주파수 정책을 보존하여 재연결 |

주파수 토큰은 십진수 채널 번호 또는 MHz 중심 주파수를 사용한다. 각 토큰은
개별 인자로 전달해야 한다.

```text
# 채널 번호
quote wconnect jhw_wlan 36 40 44 48

# MHz
quote wconnect jhw_wlan 5180 5200 5220 5240

# 명시적 인터페이스
quote wconnect mlan1 jhw_wlan 2412 2437 2462
```

채널은 내부에서 MHz로 정규화된다. 2.4 GHz는 채널 1~14, 5 GHz는 변환 결과가
5180~5825 MHz 범위인 숫자를 허용한다. 실제 사용 가능 여부는 장비의 regulatory
domain과 드라이버가 최종 결정한다. `2G`, `5G` 같은 밴드 단축어는 `wconnect`의
주파수 인자로 지원하지 않는다.

주파수만 단독 변경할 수는 없다. 인터페이스 다음 첫 토큰은 항상 SSID이므로,
주파수를 변경하려면 SSID도 함께 지정해야 한다.

### 3.3 처리 흐름

SSID가 지정된 일반 경로는 다음 작업을 수행한다.

1. SSID를 UTF-8 1~32바이트 규칙으로 검사하고 wpa_supplicant 표현으로 인코딩한다.
2. 스캔 전환과 wpa_supplicant 설정 파일 writer lock을 획득한다.
3. 진행 중인 스캔을 정리하고 현재 network ID와 topology를 확인한다.
4. 설정 파일을 canonical 형식으로 렌더링하여 SSID와 선택적인 `freq_list`를
   영속 저장하고 동기화한다.
5. 현재 network ID가 있으면 `set_network`와 `reassociate`로 빠른 런타임 전환을
   요청한다.
6. 빠른 전환이 불가능하면 저장된 설정을 `reconfigure`하는 경로로 복구한다.
7. fresh `CONNECTED` 이벤트와 `wpa_state=COMPLETED` 상태에서 목표 SSID, ID,
   주파수의 착지를 확인한다.
8. 최대 15초 안에 연결을 확인하면 성공하고, 아니면 종료 코드 `8`로 실패한다.

다중 network topology에서는 SSID를 일괄 변경하지 않도록 SSID 지정 호출을
거부한다. 인자 없는 재연결은 허용하며, enabled network 중 wpa_supplicant가 고른
network에 연결되었는지 확인한다.

### 3.4 응답과 오류 코드

성공 응답은 항상 다음 한 줄이다.

```text
200 SUCCESS
```

실패 응답은 다음 형식이다.

```text
550 FAIL code=N
```

| 코드 | 의미 |
| ---: | --- |
| `1` | 인자·SSID·주파수·설정 환경 검증 실패, 다중 topology의 SSID 변경 거부, 또는 `wifi` 실행 환경 오류 |
| `2` | 핸들러 수준의 인터페이스 인자 오류 |
| `3` | 대상 인터페이스가 현재 FTP 제어 연결을 운반하여 안전상 거부 |
| `7` | wpa_supplicant 제어 명령 또는 연결 이벤트 monitor 처리 실패 |
| `8` | 15초 안에 목표 association을 확인하지 못함 |

`wconnect`는 하위 `wifi connect`의 종료 코드를 그대로 반환하므로, 위 표 이외의
종료 코드가 발생하면 해당 코드가 `FAIL code=N`의 `N`에 표시된다. 상세 오류는
`ftpcmd_result` 로그에서 확인한다.

## 4. `wconnectraw`

### 4.1 형식과 용도

```text
quote wconnectraw [mlan0|mlan1] <ssid>
```

이 명령은 처리 시간 비교를 위한 시험용 경로다. 현재 선택된 network ID에 대해
아래 두 명령만 제출하고 즉시 반환한다.

```text
wpa_cli -i <iface> set_network <id> ssid "<ssid>"
wpa_cli -i <iface> reassociate
```

`wconnectraw`는 다음 작업을 하지 않는다.

- SSID 유효성 검사
- topology 검사
- 설정 파일 수정 또는 동기화
- 주파수/채널 적용
- 실제 연결 완료 대기 및 결과 판정

따라서 `200 SUCCESS`는 두 wpa_cli 명령이 종료 코드 `0`과 정확한 `OK` 응답을
반환했다는 뜻일 뿐, AP 연결 성공을 뜻하지 않는다. 이후 `reconfigure` 또는 서비스
재시작 시 디스크의 설정으로 되돌아갈 수 있다.

인터페이스 뒤의 남은 토큰은 공백으로 합쳐 SSID로 전달하지만, escaping과 제어문자
검증을 하지 않는다. 운영 기능이 아니라 제한된 시험 환경에서만 사용한다.

### 4.2 오류 코드

| 코드 | 의미 |
| ---: | --- |
| `1` | `wpa_cli status` 실패 또는 현재 network ID 없음 |
| `2` | SSID 누락 |
| `7` | `set_network` 또는 `reassociate`가 실패하거나 응답이 정확히 `OK`가 아님 |

## 5. 인터페이스 명령

### 5.1 `getifstate`

```text
quote getifstate [interface]
```

인자를 생략하면 `mlan0`을 조회한다. `/sys/class/net/<interface>/flags`의 `IFF_UP`
비트를 기준으로 관리 상태를 반환한다. 무선 association 상태나 `operstate`를
의미하지 않는다.

```text
200 mlan0 UP
200 mlan0 DOWN
```

| 코드 | 의미 |
| ---: | --- |
| `1` | 인터페이스 flags를 읽을 수 없음 |
| `2` | 인터페이스 이름이 잘못되었거나 존재하지 않음 |

### 5.2 `ifcup`

```text
quote ifcup [interface]
```

인자를 생략하면 `mlan0`에 `ip link set <interface> up`을 실행한다.

```text
200 IFCUP command successful.
```

| 코드 | 의미 |
| ---: | --- |
| `1` | `ip link set ... up` 실패 |
| `2` | 인터페이스 이름이 잘못되었거나 존재하지 않음 |

### 5.3 `ifcdown`

```text
quote ifcdown [interface]
```

인자를 생략하면 `mlan0`에 `ip link set <interface> down`을 실행한다.

```text
200 IFCDOWN command successful.
```

| 코드 | 의미 |
| ---: | --- |
| `1` | `ip link set ... down` 실패 |
| `2` | 인터페이스 이름이 잘못되었거나 존재하지 않음 |
| `3` | 대상 인터페이스가 현재 FTP 제어 연결을 운반하여 안전상 거부 |

## 6. 재부팅 명령

```text
quote rst
```

인자를 받지 않는다. systemd에 non-blocking 재부팅 작업을 전달한 뒤 응답한다.

```text
200 reboot requested
```

| 코드 | 의미 |
| ---: | --- |
| `1` | systemd에 재부팅을 요청하지 못함 |
| `2` | 허용되지 않은 인자가 전달됨 |

성공 응답 직후 장비가 재부팅되므로 FTP 연결이 끊기는 것은 정상이다.

## 7. 타이밍 및 실패 로그

### 7.1 `wconnect` 누적 타이밍

`wconnect`는 한 요청에 동일한 `trace` 값을 사용하여 rsyslog에 누적 경과 시간을
기록한다.

```text
ftpcmd_timing trace=<id> phase=<phase> elapsed_ms=<ms> ...
```

`elapsed_ms`는 `dispatch_received` 직전의 monotonic clock을 기준으로 한 누적값이다.
특정 구간의 소요 시간은 연속 phase의 `elapsed_ms` 차이로 계산한다.

| phase | 기록 시점과 그 전까지 수행한 주요 작업 |
| --- | --- |
| `dispatch_received` | 핸들러가 인자를 전달받아 실행을 시작함 |
| `arguments_parsed` | 기본 인터페이스, SSID 유무, 주파수 인자 수 판정 완료 |
| `connect_invoked` | 실행 파일과 제어 연결 안전 검사 후 `wifi connect` 호출 직전 |
| `wifi_connect_entered` | `wifi` 스크립트 초기화 후 connect 분기 진입 |
| `runtime_switch_requested` | 설정 검증·파일 저장·monitor 준비 후 `set_network`/`reassociate` 제출 완료 |
| `reconfigure_requested` | 빠른 전환 실패 후 `reconfigure` 제출 완료 |
| `reconnect_requested` | 인자 없는 재연결 또는 fallback `reassociate`/`reconnect` 제출 완료 |
| `disconnected_event` | wpa_supplicant의 fresh `DISCONNECTED` 이벤트 수신 |
| `connected_event` | 유효한 network ID가 포함된 fresh `CONNECTED` 이벤트 수신 |
| `association_verified` | 상태 polling으로 목표 association의 `COMPLETED` 착지 확인 |
| `association_timeout` | 제한 시간 안에 목표 association을 확인하지 못함 |
| `reply_ready` | 하위 명령 종료 코드를 얻고 FTP 응답 본문을 출력하기 직전 |

이벤트 phase는 비동기다. 예를 들어 `DISCONNECTED` 이벤트는 `wpa_cli reconfigure`
호출 도중 발생할 수 있으므로 로그 줄의 rsyslog 시각이나 phase 출력 순서가
`reconfigure_requested`와 근접하거나 앞설 수 있다. 구간 분석에는 같은 `trace`의
monotonic `elapsed_ms`를 사용한다.

`reply_ready`는 핸들러 내부 처리가 끝난 시점이다. 이후 vsftpd가 응답을 조립하고
소켓으로 전송하는 시간과 클라이언트가 수신하는 시간은 포함하지 않는다.

실패 상세 로그 형식은 다음과 같다.

```text
ftpcmd_result trace=<id> command=wconnect status=fail code=<n> iface=<iface> detail=<first-detail-line>
```

### 7.2 `wconnectraw` 제출 시간

`wconnectraw`는 연결 완료를 기다리지 않고 명령 제출 구간만 기록한다.

```text
ftpcmd_raw_timing trace=<id> phase=commands_submitted elapsed_ms=<ms> \
  iface=<iface> parse_ms=<ms> id_lookup_ms=<ms> set_network_ms=<ms> \
  reassociate_ms=<ms> set_rc=<rc> reassociate_rc=<rc>
```

| 필드 | 측정 범위 |
| --- | --- |
| `parse_ms` | 프로세스 시작부터 인터페이스/SSID 파싱 완료까지 |
| `id_lookup_ms` | `wpa_cli status`로 현재 network ID를 읽는 시간 |
| `set_network_ms` | 런타임 SSID 변경 명령 시간 |
| `reassociate_ms` | 재연결 요청 명령 시간 |
| `elapsed_ms` | 위 구간 전체 누적 시간 |

실패 상세는 `ftpcmd_raw_result`로 기록한다. raw 명령 이후 실제 무선 연결 해제와
재연결 시간은 이 trace에 포함되지 않으므로, 같은 시간대의 `wifi_event.sh` 로그를
별도로 비교해야 한다.

### 7.3 로그 조회 예

장비의 rsyslog 저장 위치에 맞춰 다음 키워드로 필터링한다.

```sh
grep 'ftpcmd_timing trace=' <rsyslog-file>
grep 'ftpcmd_result trace=' <rsyslog-file>
grep 'ftpcmd_raw_timing trace=' <rsyslog-file>
grep 'ftpcmd_raw_result trace=' <rsyslog-file>
```

## 8. 운용 제약

- FTP 제어 채널이 암호화되지 않은 환경에서는 SSID와 명령 인자가 평문으로
  전달된다. PSK는 어떤 `wconnect` 명령에도 넣지 않는다.
- `wconnect`는 기존 프로필의 인증 설정을 재사용하므로 인증 방식이나 PSK가 다른
  AP로 전환하려면 사전에 보드 설정을 준비해야 한다.
- `wconnect`와 `ifcdown`은 `FTPCMD_LOCAL_ADDR`로 현재 FTP 제어 연결의 로컬
  인터페이스를 확인하고 같은 인터페이스를 끊는 요청을 종료 코드 `3`으로 거부한다.
- 핸들러는 root 권한과 제한된 고정 PATH에서 실행된다. 구현에서 `/usr/local/bin/wifi`
  같은 프로그램은 절대 경로를 사용한다.
- vsftpd의 핸들러 제한 시간은 30초이고 `wconnect`의 association 확인 제한은
  15초다. 장시간 연결 시험은 FTP 요청 밖에서 수행한다.
