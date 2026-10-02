# FTP QUOTE 명령 프로토콜

장비의 vsftpd가 제공하는 커스텀 QUOTE 명령의 호출 형식과 응답을 정의한다.
핸들러 원본은 `dist/wlan/opt/ftpcmd/bin/`, 타겟 설치 경로는 `/opt/ftpcmd/bin/`이다.
외부 배포용 명령·응답 요약은 [WLAN 장비 FTP QUOTE 명령 규격](ftp_quote_protocol_distribution.md)을 참조한다.

## 1. 공통 응답

~~~text
ftp> quote <command> [arguments...]
~~~

| 핸들러 종료 | FTP 응답 | 의미 |
| --- | --- | --- |
| `0` | `200 <첫 번째 stdout 줄>` | 명령별 성공 본문은 아래 표 참조 |
| `0` 이외 | `550 Failed: <command> exited <N>` | `N`은 핸들러 종료 코드 |
| vsftpd의 핸들러 제한 시간 초과 | `550 Command timed out: <command>` | 핸들러 강제 종료; 종료 코드 `N` 없음 |

실패 시 핸들러의 `FAIL code=N`이나 진단문은 FTP 응답 본문으로 전달되지 않는다.
자세한 원인은 장비의 `ftpcmd_result` 또는 `ftpcmd_raw_result` 로그에서 확인한다.
종료 코드의 의미는 명령마다 다르며, `8`이 모든 오류를 뜻하지 않는다.

무선 명령은 인터페이스를 생략하면 `mlan0`을 사용한다. `wconnect`,
`wconnectraw`, `wssid`, `wfreq`는 첫 번째 인자가 정확히 `mlan0` 또는
`mlan1`일 때만 인터페이스로 해석한다. 그 외의 첫 인자는 SSID 또는
주파수 인자다. `wstatus`는 선택적 인터페이스 인자를 엄격하게 검사한다.

## 2. 무선 명령 요약

| 명령 | 인자 없음 | 값 지정 | 성공 응답 |
| --- | --- | --- | --- |
| `wssid` | 설정 파일의 SSID 조회 | 설정 파일에 SSID 저장 | 조회: `200 <SSID>` / 저장: `200 SUCCESS` |
| `wfreq` | 설정 파일의 주파수 조회 | 설정 파일에 주파수 저장 | 조회: `200 <MHz 목록>` / 저장: `200 SUCCESS` |
| `wconnect` | 저장된 프로필로 연결 확인 | SSID 저장 후 연결 확인 | `200 SUCCESS` |
| `wstatus` | 현재 연결 조회 | 선택적 인터페이스만 허용 | `200 <SSID> <MHz>` |
| `wconnectraw` | SSID 누락 오류 | 런타임 전환 명령 제출 | `200 SUCCESS` |

~~~text
wssid / wfreq --> wpa_supplicant 설정 파일에 저장
                         |
                         v
                    wconnect --> 프로필 적용 및 연결 확인
                         |
                         v
                    wstatus --> 현재 연결 조회
~~~

`wssid`와 `wfreq`는 설정 파일만 변경한다. supplicant 재설정이나 연결 시도는
`wconnect`가 담당한다. `wstatus`는 설정 파일의 희망값이 아닌 현재 연결값을
반환한다.

~~~text
ftp> quote wssid
200 jhw_wlan_
ftp> quote wfreq
200 5180,5200,5220,5240
ftp> quote wstatus
200 jhw_wlan_ 5180

ftp> quote wfreq 5180 5200 5220 5240
200 SUCCESS
ftp> quote wconnect
200 SUCCESS
~~~

위 조회 예는 타겟에서 확인한 값이다. `wfreq` 조회는 허용 목록 전체를,
`wstatus` 조회는 실제 연결 주파수 한 개를 보여 준다.

## 3. 명령별 동작

### 3.1 wssid: 설정 SSID

~~~text
quote wssid [mlan0|mlan1] [ssid words...]
~~~

- 값이 없으면 첫 번째 `network` 블록의 SSID를 반환한다.
- 값이 있으면 남은 토큰을 공백으로 합쳐 설정 파일에 저장한다.
  예: `quote wssid My AP`는 `My AP`를 저장한다.
- SSID는 UTF-8 기준 1~32바이트이며 제어 문자는 허용하지 않는다.
- SSID 자체가 `mlan0` 또는 `mlan1`이면 인터페이스를 먼저 명시한다.
  예: `quote wssid mlan0 mlan0`.
- 조회 또는 저장 실패는 주로 코드 `1`이다. 하위 `wifi ssid`의 다른
  종료 코드가 전달될 수도 있다.

### 3.2 wfreq: 설정 주파수

~~~text
quote wfreq [mlan0|mlan1] [freq_or_channel...]
~~~

- 값이 없으면 설정 파일의 주파수 목록을 MHz와 쉼표로 반환한다.
  제한이 없으면 `200 ANY`다.
- 값이 있으면 각 채널 번호 또는 MHz를 검증하여 전역 및 모든 `network`
  블록의 `freq_list`에 저장한다.
- 채널 번호는 MHz로 변환한다. 허용 범위는 2.4 GHz의 2412~2484 MHz와
  5 GHz의 5180~5825 MHz다. 실제 사용 가능 여부는 regulatory domain과
  드라이버가 결정한다. `2G`, `5G` 단축어는 지원하지 않는다.
- 검증 또는 설정 파일 처리 실패는 주로 코드 `1`이다.

~~~text
ftp> quote wfreq 36 40 44 48
200 SUCCESS
ftp> quote wfreq
200 5180,5200,5220,5240
~~~

### 3.3 wconnect: 프로필 적용과 연결 확인

~~~text
quote wconnect [mlan0|mlan1] [ssid words...]
~~~

- SSID가 없으면 저장된 프로필을 사용한다. `quote wconnect mlan1`은
  `mlan1`의 저장된 프로필을 사용한다.
- SSID가 있으면 남은 토큰을 공백으로 합쳐 설정 파일에 저장한 뒤
  연결을 시도한다. `quote wconnect My AP`는 `My AP`에 연결을 시도한다.
- 주파수는 인자로 받지 않는다. 주파수를 바꾸려면 먼저 `wfreq`로 저장한다.
  숫자 단어도 SSID의 일부다. `quote wconnect AP 2`는 `AP 2`에 연결을 시도한다.
- 기존 PSK와 인증 설정을 재사용한다. FTP 명령으로 PSK를 전달하지 않는다.

이미 연결된 단일 `network`에서 요청 SSID와 저장된 SSID 및 주파수 정책이
현재 supplicant 설정과 일치하고 현재 연결 주파수도 허용 목록에 있으면
연결을 유지하며 `200 SUCCESS`를 반환한다. 인증 설정 변경이 적용 대기
중이거나, 설정이 다르거나, 미연결이면 프로필 `reconfigure` 후 필요한
스캔과 재연결을 요청한다. 연결 확인에는 기본 15초에 해당하는 폴링
예산을 사용한다. 설정 처리와 `reconfigure` 소요 시간은 별도이므로 FTP
명령의 전체 실행 시간이 반드시 15초 이내라는 뜻은 아니다.

SSID 없이 호출할 때는 설정 파일을 수정하지 않는다. 다중 `network`
구성에서는 명시적 SSID 일괄 변경을 거부하지만 무인자 연결은 허용한다.
`200 SUCCESS`는 목표 association을 확인했다는 뜻이다.
`550 Failed: wconnect exited 8`은 제한 시간 안에 확인하지 못했다는 뜻이며,
이후 늦게 연결될 수 있으므로 `wstatus`로 다시 조회한다.

### 3.4 wstatus: 현재 연결

~~~text
quote wstatus [mlan0|mlan1]
~~~

`wpa_cli status`가 `wpa_state=COMPLETED`일 때 현재 SSID와 연결 주파수
한 개를 반환한다. SSID에 공백이 있으면 마지막 숫자 토큰이 MHz이고 그 앞의
전체 문자열이 SSID다. 미연결이지만 supplicant 상태 조회가 가능하면 코드
`8`, 상태 조회 자체가 실패하면 `7`을 반환한다.

### 3.5 wconnectraw: 시험용 런타임 전환

~~~text
quote wconnectraw [mlan0|mlan1] <ssid words...>
~~~

SSID는 필수다. 현재 선택된 `network` ID가 없으면 유일하게 활성화된
`network`를 찾는다. `wpa_cli set_network <id> ssid ...`와
`wpa_cli reassociate`를 제출하고 두 응답이 정확히 `OK`이면 즉시
`200 SUCCESS`를 반환한다.

이 성공은 **명령 제출 성공**만 뜻한다. AP 연결 완료를 기다리지 않으며,
SSID 검증, 설정 파일 저장, 주파수 적용도 하지 않는다. 이후
`reconfigure` 또는 supplicant 재시작으로 런타임 변경이 사라질 수 있다.

## 4. 오류 코드

핸들러가 실패 코드로 종료하면 `550 Failed: <command> exited <N>`이다.
vsftpd가 핸들러를 30초에 강제 종료하면 `550 Command timed out: <command>`를
반환하며 `N`은 없다. 다음 표는 핸들러가 반환한 명령별 `N`의 의미다.

| 명령 | 코드 | 의미 |
| --- | ---: | --- |
| `wssid` | `1` | 설정 조회, SSID 검증 또는 저장 실패 |
| `wfreq` | `1` | 설정 조회, 주파수 검증 또는 저장 실패 |
| `wconnect` | `1` | SSID 또는 설정 검증 실패, 실행 환경 오류 |
| `wconnect` | `2` | 인터페이스 검증 실패; 일반적인 인자 파싱에서는 발생하지 않음 |
| `wconnect` | `3` | 대상 인터페이스가 FTP 제어 연결을 운반하여 거부 |
| `wconnect` | `7` | supplicant 제어 명령 또는 연결 이벤트 감시 실패 |
| `wconnect` | `8` | 제한 시간 내 목표 연결 확인 실패 |
| `wstatus` | `2` | 인터페이스 인자 오류 또는 인자 초과 |
| `wstatus` | `7` | supplicant 상태 조회 실패 또는 필수 상태값 누락 |
| `wstatus` | `8` | 상태 조회는 성공했으나 현재 연결되지 않음 |
| `wconnectraw` | `1` | 상태 조회 실패 또는 사용할 `network`를 결정할 수 없음 |
| `wconnectraw` | `2` | SSID 누락 |
| `wconnectraw` | `7` | `set_network` 또는 `reassociate` 명령 실패 |

`wssid`, `wfreq`, `wconnect`는 하위 명령의 종료 코드를 전달할 수 있으므로
표에 없는 `N`이 나올 수도 있다. 핸들러 로그의 `code`와 `detail`을 함께
확인한다. 첫 토큰이 `mlan0`/`mlan1`이 아닌 `wconnect` 요청은 인터페이스
오류가 아니라 SSID 요청으로 해석된다.

다음은 타겟에서 연결 설정을 바꾸지 않는 입력으로 확인한 실패 응답이다.

~~~text
ftp> quote wfreq invalid-frequency
550 Failed: wfreq exited 1
ftp> quote wstatus invalid-interface
550 Failed: wstatus exited 2
ftp> quote wconnectraw
550 Failed: wconnectraw exited 2
ftp> quote wstatus mlan1
550 Failed: wstatus exited 7
~~~

마지막 예의 `7`은 확인 당시 타겟의 `mlan1`에서 상태 조회가 실패한 결과다.
`mlan1`이 연결되어 있다면 응답은 달라진다. 유효하지 않은 33바이트 SSID로
`wconnect`를 호출했을 때도 `550 Failed: wconnect exited 1`을 확인했다.

## 5. 인터페이스와 재부팅 명령

| 명령 | 호출 형식 | 성공 응답 | 실패 코드 |
| --- | --- | --- | --- |
| `getifstate` | `quote getifstate [interface]` | `200 <interface> UP` 또는 `200 <interface> DOWN` | `1`: 상태 읽기 실패, `2`: 이름 오류/인터페이스 없음 |
| `ifcup` | `quote ifcup [interface]` | `200 IFCUP command successful.` | `1`: UP 실패, `2`: 이름 오류/인터페이스 없음 |
| `ifcdown` | `quote ifcdown [interface]` | `200 IFCDOWN command successful.` | `1`: DOWN 실패, `2`: 이름 오류/인터페이스 없음, `3`: FTP 제어 연결 보호 |
| `rst` | `quote rst` | `200 reboot requested` | `1`: 재부팅 요청 실패, `2`: 인자 오류 |

인터페이스 명령은 인자를 생략하면 `mlan0`을 사용한다. `getifstate`의
`UP`/`DOWN`은 무선 연결 상태가 아니라 `/sys/class/net/<interface>/flags`의
관리 상태다. `ifcdown`은 FTP 제어 연결을 운반하는 인터페이스를 내리는
요청을 거부한다. `rst`는 systemd에 재부팅을 요청하므로 성공 응답 직후
FTP 연결이 끊길 수 있다.

## 6. 로그와 시간 측정

`wconnect`는 같은 `trace`로 누적 시간과 실패 상세를 기록한다.

~~~text
ftpcmd_timing trace=<id> phase=<phase> elapsed_ms=<ms> ...
ftpcmd_result trace=<id> command=wconnect status=fail code=<N> iface=<iface> detail=<first-detail-line>
~~~

| phase | 의미 |
| --- | --- |
| `dispatch_received`, `arguments_parsed`, `connect_invoked` | FTP 수신, 인자 분석, `wifi connect` 호출 |
| `wifi_connect_entered` | `wifi.sh`의 연결 분기 진입 |
| `reconfigure_requested`, `scan_requested`, `scan_unavailable` | 프로필 적용과 필요한 스캔 요청 |
| `reconnect_requested` | `reassociate` 또는 `reconnect` 요청 |
| `connected_event`, `disconnected_event` | supplicant 비동기 이벤트 수신 |
| `association_verified`, `association_timeout` | 목표 연결 확인 또는 시간 초과 |
| `reply_ready` | 핸들러 종료 및 FTP 응답 준비 |

`elapsed_ms`는 핸들러 시작 이후 누적 시간이다. 연속 단계의 차이가 해당
구간 시간이다. 비동기 이벤트는 다른 단계와 출력 순서가 바뀔 수 있다.
`reply_ready` 이후 FTP 전송 시간은 포함되지 않는다. `wifi.sh` 자체의
로그는 `[wifi.sh:<line>]` 태그를 사용하며 `freq` 명령의 모든 주파수
인자를 기록한다.

`wconnectraw`는 연결 완료가 아닌 명령 제출 시간만 기록한다.

~~~text
ftpcmd_raw_timing trace=<id> phase=commands_submitted elapsed_ms=<ms> iface=<iface> parse_ms=<ms> id_lookup_ms=<ms> set_network_ms=<ms> reassociate_ms=<ms> set_rc=<rc> reassociate_rc=<rc>
ftpcmd_raw_result trace=<id> command=wconnectraw status=fail code=<N> iface=<iface> detail=<detail>
~~~

`wssid`, `wfreq`, `wstatus` 실패는
`ftpcmd_result command=<command> status=fail code=<N> ...`로 기록한다.
설치된 rsyslog 파일에서
`ftpcmd_` 또는 `[wifi.sh:`를 검색하면 된다.

## 7. 운용 제약

- FTP 제어 연결이 암호화되지 않았다면 SSID 등 명령 인자가 평문으로
  전달된다. PSK를 `wconnect`나 `wconnectraw` 인자로 넣지 않는다.
- `wconnect`와 `ifcdown`은 FTP 제어 연결을 운반하는 인터페이스를
  변경하지 않도록 검사한다. 판정은 실제 제어 연결 주소와 인터페이스
  구성에 따른다.
- `wifi.sh`의 연결 확인 폴링 기본값은 `ASSOC_TIMEOUT_DEFAULT=15`초다.
  직접 `wifi <iface> connect`를 실행할 때는 양의 정수 환경변수로 바꿀 수
  있다. FTP 핸들러는 이 환경변수를 전달받지 않고 시간 인자도 받지 않으므로
  현재 `quote wconnect`에서는 설정할 수 없다.
- vsftpd의 핸들러 제한 시간 30초는 커스텀 패치의
  `VSF_EXTCMD_TIMEOUT_SECS` 컴파일 상수다. `vsftpd.conf`에서 변경할 수
  없으며 패치 변경과 vsftpd 재빌드가 필요하다. 전체 명령 시간에는 연결
  확인 외의 설정 처리도 포함된다. `wconnectraw`도 이 30초 제한을 받지만
  연결 완료는 확인하지 않는다.
- 명령 이름으로 `connect`를 사용하지 않는다. vsftpd가 이를 HTTP 메서드로
  처리해 커스텀 핸들러에 전달하기 전에 연결을 종료한다.
