# FTP QUOTE 명령 프로토콜

장비의 vsftpd가 제공하는 커스텀 QUOTE 명령의 호출 형식과 응답을 정의한다.
핸들러 원본은 `dist/wlan/opt/ftpcmd/bin/`, 타겟 설치 경로는 `/opt/ftpcmd/bin/`이다.
외부 배포용 명령·응답 요약은 [WLAN 장비 FTP QUOTE 명령 규격](ftp_quote_protocol_distribution.md)을 참조한다.
아래 인터페이스 필수 규칙은 `wlan-proc` 0.7.0부터 적용된다.
`whelp`, `wntp`, `wtime`은 `wlan-proc` 0.7.13부터 제공한다.

## 1. 공통 응답

~~~text
ftp> quote <command> [arguments...]
~~~

| 처리 결과 | FTP 응답 | 의미 |
| --- | --- | --- |
| `0` | `200 <첫 번째 출력 줄>` | 명령별 성공 본문은 아래 표 참조 |
| 디스패처 입력 검증 실패 | `501 Invalid command arguments.` | 핸들러 실행 전 거부; 핸들러 종료 코드 없음 |
| `0` 이외 | `550 Failed: <command> exited <N>` | `N`은 핸들러 종료 코드 |
| vsftpd의 핸들러 제한 시간 초과 | `550 Command timed out: <command>` | 핸들러 강제 종료; 종료 코드 `N` 없음 |

실패 시 핸들러의 `FAIL code=N`이나 진단문은 FTP 응답 본문으로 전달되지 않는다.
자세한 원인은 장비의 `ftpcmd_result` 또는 `ftpcmd_raw_result` 로그에서 확인한다.
종료 코드의 의미는 명령마다 다르며, `8`이 모든 오류를 뜻하지 않는다.
실기 디스패처는 stderr도 수집한다. 성공 종료하는 조회 핸들러의 진단문은
응답 첫 줄을 덮지 않도록 stdout·stderr 대신 syslog에만 기록한다.

`wrst`, `whelp`, `wntp`, `wtime`을 제외한 `w`로 시작하는 모든 명령은 첫 번째 인자로 인터페이스를 **필수로** 받는다.
`mlan0`과 `0`은 `mlan0`, `mlan1`과 `1`은 `mlan1`을 선택한다. 인자가
없거나 이 네 값 이외의 값을 주면 하위 명령 실행 전에 코드 `2`로 거부한다.
인터페이스 뒤에 오는 SSID·주파수의 숫자 토큰은 별칭으로 변환하지 않는다.
`wrst`는 예외로 인터페이스를 생략할 수 있으며, 지정한 경우에는 같은 네 값만 허용한다.
`whelp`는 인터페이스 없이 명령 목록 또는 지정한 명령의 도움말을 조회한다.
`wntp`와 `wtime`도 인터페이스 없이 장치 전체의 NTP와 시각을 다룬다.
기존 `getifstate`, `ifcdown`, `ifcup`, `rst`의 인자 규칙은 유지한다.

### 1.1 FTP 인자 입력 제한

아래 제한은 장비 이미지에 포함된 vsftpd 커스텀 디스패처가 핸들러 실행 전에
검사한다. 패키지의 SSID 검증과 별도의 단계다.

- 인자에 허용하는 문자는 ASCII 영문자(`A-Z`, `a-z`), 숫자(`0-9`),
  일반 공백(`U+0020`), `_ - . : , = + @ /`뿐이다.
  한글 등 비 ASCII 문자, 따옴표, 역슬래시, 세미콜론은 거부한다.
- 명령 이름을 제외한 인자 문자열은 공백을 포함해 최대 **255바이트**,
  공백으로 구분한 인자는 최대 **8개 토큰**이다. 인터페이스 인자도 두 한도에
  포함되므로 `wssid 0` 뒤에 전달할 수 있는 SSID 단어는 최대 7개다.
- 공백이 있는 SSID는 `quote wssid 0 Office AP`처럼 보낸다. 인터페이스 뒤
  단어를 하나의 SSID로 합치며 연속 공백은 하나로 합쳐진다.
  실제 FTP 요청이 `wssid 0 "Office AP"`처럼 따옴표를 포함하면 거부한다.
  클라이언트의 입력 구문과 실제 요청 문자를 구분한다. 콘솔 `tnftp`는
  입력 구문의 따옴표를 제거할 수 있으므로 화면에 쓴 따옴표만으로 `501`을
  예상하지 않는다.
- 명령 이름의 영문 대소문자는 구분하지 않는다. 인터페이스와 SSID 등
  인자 값은 소문자로 변환하지 않는다.

허용 문자·바이트·토큰 한도를 벗어나면 `501 Invalid command arguments.`를
반환한다. 입력이 디스패처 검증을 통과해도 각 핸들러의 인자 규칙을 만족해야
한다. 예를 들어 `wstatus`의 추가 인자는 `550 Failed: wstatus exited 2`다.

이 제한은 FTP로 전달하는 입력에 적용된다. 다른 경로로 저장한 SSID의 문자
지원 여부나 무선 연결 가능 여부를 뜻하지 않는다. 입력 문자나 한도를 바꾸려면
이미지의 vsftpd 패치 변경과 재빌드·검증이 필요하다.

### 1.2 whelp: 커스텀 명령 도움말

~~~text
quote whelp [command]
~~~

- 인자가 없으면 패키지가 제공하는 핸들러 중 같은 디렉터리에 설치된 실행 가능한
  명령을 이름순으로 나열한다. `whelp` 자체도 포함한다.
- 명령 이름을 지정하면 역할, 호출 형식, 예제를 반환한다. 도움말 조회 시 실제
  명령을 실행하거나 무선 설정·인증 정보를 읽지 않는다. 명령 이름의 대소문자는
  구분하지 않는다.
- 성공 출력은 기존 디스패처가 전달하는 **인쇄 가능한 ASCII 한 줄·256바이트 이내**다.
  전체 상세 도움말을 여러 줄로 보내지 않고 필요한 명령을 각각 조회한다.
- 알 수 없거나 설치되지 않은 명령, 실행 권한이 없는 명령, 두 개 이상의 인자는
  코드 `2`로 실패한다(`550 Failed: whelp exited 2`).

~~~text
ftp> quote whelp
200 Commands: getifstate ifcdown ifcup rst wconnect wconnectraw wfreq wgetifstate whelp wifcdown wifcup wntp wpsk wrst wscan wssid wstatus wtime; Details: quote whelp <command>
ftp> quote whelp wstatus
200 Read current connected SSID/frequency, not saved settings. Usage: quote wstatus <iface>. Example: quote wstatus 0; iface=0|1|mlan0|mlan1
ftp> quote whelp wrst
200 Reboot the whole board; optional iface is only validated. Usage: quote wrst [iface]. Example: quote wrst; iface=0|1|mlan0|mlan1
~~~

`<iface>`는 `mlan0|mlan1|0|1` 중 하나다. 꺾쇠·대괄호는 형식 설명이므로
실제 요청에 넣지 않는다. 목록은 설치 상태에 따라 달라질 수 있다.

### 1.3 wntp: NTP 조회·서버 저장·적용

| 호출 | 동작 | 성공 본문 |
| --- | --- | --- |
| `quote wntp` | 현재 상태와 장치의 저장 서버 조회 | `SERVICE=<상태> ENABLED=<유닛 enable 상태> SYNC=<yes/no> CONFIG=<저장 목록> SERVER=<선택 서버>` |
| `quote wntp ip <IP>` | 장치의 기본 NTP 서버 저장 | `SUCCESS NTP=<IP>; run wntp apply` |
| `quote wntp apply` | `systemd-timesyncd.service` 재시작 | `SUCCESS restarted systemd-timesyncd; synchronization not confirmed` |

- `CONFIG`는 `/etc/systemd/timesyncd.conf`의 `[Time]` 안에 저장된 `NTP=` 목록이다.
  여러 주소는 쉼표로 구분하고 지정값이 없으면 `unset`이다. `SERVER`는 실행 중
  서비스가 선택한 주소이며, 저장 후 `apply` 전에는 두 값이 다를 수 있다.
- `SERVICE`와 `ENABLED`는 systemd 유닛 상태다. `SYNC`는 커널의 NTP 동기화
  플래그로 서비스가 active라는 뜻과 다르다. 조회할 수 없는 값은 `unknown`,
  서비스가 inactive/failed이면 선택 서버는 `none`이다. 중지된 timesyncd를
  조회 과정에서 기동하지 않는다. 긴 필드는 `...`로 생략해 256바이트 한도를 지킨다.
- `ip`는 호스트명·CIDR·zone ID가 없는 유니캐스트 IPv4/IPv6 리터럴을 받는다.
  `[Time]`의 기존 `NTP=` 목록만 교체하고 다른 설정·파일 소유자·권한은 유지한다.
  설정은 원자적으로 교체하며 재시작은 하지 않는다. 일반 파일이 아닌 설정 경로는 거부한다.
- OPC가 만든 `/run/systemd/timesyncd.conf.d/opc-ntp.conf`와 다른 drop-in,
  networkd/DHCP가 제공한 서버는 기존 소유자가 관리한다. 이 명령은 해당 값을
  삭제하지 않는다. 이 값들이 장치 기본 서버를 덮어쓰거나 추가할 수 있으므로
  `apply` 뒤에도 `SERVER`를 확인한다. 설정 우선순위는
  [systemd timesyncd.conf 규격](https://www.freedesktop.org/software/systemd/man/latest/timesyncd.conf.html)을 따른다.
- `apply`는 inactive인 유닛도 기동할 수 있다. 유닛 enable 여부와
  `wifi_init_conf.json`의 `global.ntp.enabled`는 바꾸지 않는다. 이후 서비스 조정은
  기존 설정을 따른다. 재시작 성공은 NTP 서버 응답이나 동기화 완료를 보장하지 않는다.
- FTP 실행 환경에서 PID 1의 private bus 연결이 실패하는 경우를 위해
  systemctl 자식에 `SYSTEMCTL_FORCE_BUS=1`을 지정해 인증되는 public system bus를
  사용한다. systemd 254에서 검증했으며 이 변수의 지원 범위는
  [upstream 환경변수 문서](https://github.com/systemd/systemd/blob/994c7978608a0bd9b317f4f74ff266dd50a3e74e/docs/ENVIRONMENT.md)를 따른다.
- 잘못된 인자는 코드 `2`, 파일·재시작 오류는 `1`, 내부 20초 재시작 제한 시간
  초과는 `124`다. 제한 시간이 지나도 systemd 작업은 완료될 수 있으므로 상태를 다시 조회한다.

~~~text
ftp> quote wntp ip 192.168.0.3
200 SUCCESS NTP=192.168.0.3; run wntp apply
ftp> quote wntp apply
200 SUCCESS restarted systemd-timesyncd; synchronization not confirmed
ftp> quote wntp
200 SERVICE=active ENABLED=enabled SYNC=yes CONFIG=192.168.0.3 SERVER=192.168.0.3
~~~

### 1.4 wtime: 밀리초 시각 조회·수동 설정

| 호출 | 동작 | 성공 본문 |
| --- | --- | --- |
| `quote wtime` | 장치 현지 시각 조회 | `TIME=YYYY-MM-DD HH:MM:SS.mmm OFFSET=+0900` |
| `quote wtime sync YYYY-MM-DD HH:MM:SS[.mmm]` | 현지 시각을 수동 설정·저장 | `SUCCESS TIME=<현재 시각> OFFSET=<UTC 오프셋>; NTP unchanged` |

- 날짜와 시각을 공백으로 구분하며 따옴표를 보내지 않는다. 소수부는 생략하거나
  1~3자리로 지정한다. `.1`은 100ms, `.123`은 123ms다. 조회는 항상 소수 3자리다.
  별도 시간대 인자는 없으며 장치의 현지 시간대를 사용한다.
- 달력 날짜·시각·인자 개수를 검증한 뒤 기존
  `/usr/local/scripts/fake-hwclock.sh set`에 소수부를 포함한 시각을 한 인자로
  전달한다. 이 경로가 시스템 시각 설정, 영속 상태 저장, RTC 쓰기 시도를 담당한다.
  영속 상태 파일과 RTC는 기존의 초 단위 정밀도를 유지한다.
- NTP 설정과 서비스 상태는 바꾸지 않는다. NTP가 활성화되어 있으면 이후
  서버 기준으로 다시 보정될 수 있다. FTP 전송·명령 실행 지연은 자동 보상하지
  않으므로 입력의 밀리초 정밀도가 원격 기준 시계와 1ms 이내의 동기화를 보장하지 않는다.
- 인자·날짜 오류는 코드 `2`, 시간 설정·저장 오류는 `1`, 내부 20초 제한 시간
  초과는 `124`다. 설정 후 저장 실패나 제한 시간 초과가 발생하면 시각은 이미
  변경되었을 수 있으므로 다시 조회한다. RTC 쓰기는 기존 helper와 같이 best-effort다.

~~~text
ftp> quote wtime
200 TIME=2026-10-29 16:30:00.123 OFFSET=+0900
ftp> quote wtime sync 2026-10-29 16:30:00.123
200 SUCCESS TIME=2026-10-29 16:30:00.125 OFFSET=+0900; NTP unchanged
~~~

위 시각과 offset은 응답 형식 예시다. 설정 응답의 `TIME`은 명령 완료 뒤 실제 조회한 시각이다.

## 2. 무선 명령 요약

| 명령 | 인터페이스만 지정 | 인터페이스 뒤 값 지정 | 성공 응답 |
| --- | --- | --- | --- |
| `wssid` | 설정 파일의 SSID 조회 | 설정 파일에 SSID 저장 | 조회: `200 <SSID>` / 저장: `200 SUCCESS` |
| `wfreq` | 설정 파일의 주파수 조회 | 설정 파일에 주파수 저장 | 조회: `200 <MHz 목록>` / 저장: `200 SUCCESS` |
| `wscan` | 대상 SSID 누락 오류 | 대상 SSID의 최신 스캔 결과 조회 | `200 SSID="<SSID>" RSSI=<dBm> FREQ=<MHz>` |
| `wpsk` | 비밀번호 누락 오류 | PSK 저장 또는 `--stage`로 휘발 저장 | `200 SUCCESS` |
| `wconnect` | 저장된 프로필로 연결 확인 | SSID 저장 후 연결 확인 | `200 SUCCESS` |
| `wstatus` | 현재 연결 조회 | 추가 인자 오류 | `200 <SSID> <MHz>` |
| `wconnectraw` | SSID 누락 오류 | 빠른 런타임 전환과 성공 시 비동기 SSID 저장 요청 | `200 SUCCESS` |

~~~text
wssid / wfreq --> wpa_supplicant 설정 파일에 저장
                         |
                         v
                    wconnect --> 프로필 적용 및 연결 확인
                         |
                         v
                    wstatus --> 현재 연결 조회
~~~

`wssid`·`wfreq`와 기본 `wpsk`는 설정 파일만 변경한다. `wpsk --stage`는
PSK를 휘발 저장한다. `wscan`은 연결 설정을 바꾸지 않고 대상 SSID를
스캔한다. supplicant 재설정이나 연결 시도는
`wconnect`가 담당한다. `wstatus`는 설정 파일의 희망값이 아닌 현재 연결값을
반환한다. `mlanN.connect_fallback.enabled=true`이면 가능한 경우 이 저장 변경 전에
현재 검증 완료 프로필을 fallback 후보로 캡처하고, 다음 `wconnect` 실패 시 복구에 쓴다.

~~~text
ftp> quote wssid 0
200 jhw_wlan_
ftp> quote wfreq 0
200 5180,5200,5220,5240
ftp> quote wstatus 0
200 jhw_wlan_ 5180

ftp> quote wfreq 0 5180 5200 5220 5240
200 SUCCESS
ftp> quote wconnect 0
200 SUCCESS
~~~

위 조회 값은 이전 버전의 타겟에서 확인한 값이며 호출 형식은 0.7.0 기준이다.
`wfreq` 조회는 허용 목록 전체를,
`wstatus` 조회는 실제 연결 주파수 한 개를 보여 준다.

## 3. 명령별 동작

### 3.1 wssid: 설정 SSID

~~~text
quote wssid <mlan0|mlan1|0|1> [ssid words...]
~~~

- 값이 없으면 첫 번째 `network` 블록의 SSID를 반환한다.
- 값이 있으면 남은 토큰을 공백으로 합쳐 설정 파일에 저장한다.
  예: `quote wssid 0 My AP`는 `mlan0`에 `My AP`를 저장한다.
- 하위 `wifi ssid`의 SSID 검증은 UTF-8 기준 1~32바이트이며 제어 문자는
  허용하지 않는다. FTP 입력은 먼저 1.1절의 ASCII 제한을 통과해야 하므로
  이 경로에서 저장하는 SSID는 허용 문자로 구성한 1~32바이트다.
- SSID는 인터페이스 다음부터 시작한다. SSID 자체가 `mlan0`이면
  `quote wssid 0 mlan0`, `1`이면 `quote wssid 0 1`로 입력한다.
- 조회 또는 저장 실패는 주로 코드 `1`이다. 하위 `wifi ssid`의 다른
  종료 코드가 전달될 수도 있다.

### 3.2 wfreq: 설정 주파수

~~~text
quote wfreq <mlan0|mlan1|0|1> [freq_or_channel...]
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
ftp> quote wfreq 0 36 40 44 48
200 SUCCESS
ftp> quote wfreq 0
200 5180,5200,5220,5240
~~~

### 3.2.1 wscan: 대상 SSID 스캔

~~~text
quote wscan <mlan0|mlan1|0|1> <ssid words...>
~~~

- 지정한 SSID에 대한 `iw` directed scan을 실행한다. 스캔 주파수는 현재
  supplicant 설정 파일의 공통 `freq_list`를 사용하며, 제한 목록이 없으면
  전대역에서 찾는다. 설정 파일이나 연결 상태를 변경하지 않는다.
- 이번 스캔에서 새로 본 동일 SSID의 BSS 중 RSSI가 가장 강한 항목을
  `SSID="<SSID>" RSSI=<dBm> FREQ=<MHz>`로 반환한다. `FREQ`는 발견된
  AP의 주파수이며, 연결 성공이나 그 주파수로의 연결을 보장하지 않는다.
- 대상 SSID가 없거나 결과가 오래되었거나 설정 주파수 범위 밖이면 코드
  `4`를 반환한다. 스캔 실행 실패는 하위 `wifi scan`의 종료 코드를 전달한다.

~~~text
ftp> quote wscan 0 Office AP
200 SSID="Office AP" RSSI=-50.00 FREQ=5180
~~~

### 3.2.2 wpsk: 연결 비밀번호

~~~text
quote wpsk <mlan0|mlan1|0|1> [--stage] <passphrase words...>
~~~

- `--stage` 없이 호출하면 하위 `wifi psk`를 실행한다. 8~63바이트
  passphrase를 WPA 설정 파일의 모든 `network` 블록에 저장하며, 현재 연결은
  바꾸지 않는다. 다음 `wconnect`가 저장값을 적용한다. 연결 실패 시
  `connect_fallback`이 꺼져 있으면 새 PSK가 파일에 남는다.
- 인터페이스 바로 뒤에 `--stage`를 넣으면 비밀번호를 `/run/wifi`에
  mode `0600`으로 휘발 저장한다. 이때 설정 파일은 바뀌지 않는다.
  다음 `wconnect <iface> <SSID>`가 단일 PSK network를 런타임에서 먼저
  연결하고 성공을 확인한 뒤 SSID와 PSK를 설정 파일에 함께 저장한다.
  `--stage` 경로는 기존 연결·설정과 런타임 프로필이 일치하는 Mode B
  단일 network에 한정한다. 성공한 연결은 재부팅 후에도 사용할 수 있다.
- 같은 인터페이스에 파일 저장 PSK의 적용 대기 상태가 있으면 `--stage`는
  코드 `6`으로 거절한다. 휘발 PSK가 대기 중이면 일반 `wpsk`도 코드 `6`으로
  거절한다. 먼저 선택한 방식의 `wconnect`를 마쳐야 다른 방식을 사용할 수 있다.
  휘발 stage는 재부팅하면 사라지지만, 파일에 저장한 PSK는 재부팅 후에도 남는다.
- 비밀번호 단어는 공백 하나로 합쳐진다. FTP 디스패처의 허용 문자와
  8토큰/255바이트 제한이 먼저 적용되며, `--stage`는 예약 토큰이다.
  핸들러는 비밀번호를 응답이나 자체 로그에 쓰지 않는다. FTP 제어 연결이
  암호화되지 않았다면 전송 중 비밀번호가 평문이다. 운영 정책상 이 명령은
  비밀번호의 프로세스 인자 노출을 허용한다. `wpsk` 핸들러와 하위 `wifi`의
  인자에 비밀번호가 전달되고, `--stage` 연결 시에는 `wpa_cli` 인자에도
  전달된다. 로컬 프로세스 인자를 조회할 수 있는 계정도 비밀번호를 볼 수
  있으므로, 응답·로그 미기록을 비밀번호 비공개 보장으로 해석하지 않는다.

~~~text
ftp> quote wscan 0 Office AP B
200 SSID="Office AP B" RSSI=-50.00 FREQ=5180
ftp> quote wpsk 0 --stage newpass123
200 SUCCESS
ftp> quote wconnect 0 Office AP B
200 SUCCESS
~~~

### 3.3 wconnect: 프로필 적용과 연결 확인

~~~text
quote wconnect <mlan0|mlan1|0|1> [ssid words...]
~~~

- SSID가 없으면 저장된 프로필을 사용한다. `quote wconnect mlan1`은
  `mlan1`의 저장된 프로필을 사용한다.
- SSID가 있으면 남은 토큰을 공백으로 합쳐 설정 파일에 저장한 뒤
  연결을 시도한다. `quote wconnect 0 My AP`는 `mlan0`에서 `My AP`에 연결을 시도한다.
- 주파수는 인자로 받지 않는다. 주파수를 바꾸려면 먼저 `wfreq`로 저장한다.
  숫자 단어도 SSID의 일부다. `quote wconnect 0 AP 2`는 `AP 2`에 연결을 시도한다.
- 별도 `wpsk`를 사용하지 않았다면 기존 PSK와 인증 설정을 재사용한다.
  `wconnect` 자체는 PSK를 인자로 받지 않는다.

`wpsk <iface> --stage`를 먼저 호출했다면 명시적 SSID가 있는 다음
`wconnect`는 단일 PSK network의 Mode B에서 런타임 SSID·PSK 변경으로
새 연결을 확인한다. 확인 전에는 WPA 설정 파일을 바꾸지 않고, 확인 뒤
SSID와 PSK를 함께 저장한다. 같은 SSID의 새 PSK도 실제 재연결을 확인하며,
이미 연결됐다는 이유로 성공을 반환하지 않는다. 연결 또는 저장에 실패하면
이 경로는 `connect_fallback.enabled`와 관계없이 이전 설정·연결 복구를
시도하고 실패 코드를 유지한다. 저장 전 전원 차단 시 휘발 stage는 사라진다.
`wscan` 성공은 발견 사실이며 `wconnect` 연결 성공의 증거가 아니다.

전환 실패·TERM에는 원래 설정, OPC 복원 표식, fallback 체크포인트와
연결을 복구하려고 시도하며 원래 실패 코드를 반환한다. 프로세스가 강제
종료돼 `/run/wifi`에 미완료 표식이 남으면 유선·시리얼 관리 경로에서
`wconnect <iface>`를 다시 호출해 원본 프로필 복구를 요청할 수 있다.
복구 확인에 실패하면 표식을 보존하고 오류를 반환하므로, 저장 설정과
현재 연결을 점검한 뒤 재시도해야 한다. 전원 차단은 `/run`의 복구
스냅샷을 지운다. 저장 직후 응답 전 차단된 경우에는 새 설정이 남을 수
있으므로, 이 경우 유선·시리얼 경로에서 저장 프로필을 확인하고 필요한
기존 SSID·PSK를 다시 설정해야 한다.

이미 연결된 단일 `network`에서 요청 SSID와 저장된 SSID 및 주파수 정책이
현재 supplicant 설정과 일치하고 현재 연결 주파수도 허용 목록에 있으면
연결을 유지하며 `200 SUCCESS`를 반환한다. 인증 설정 변경이 적용 대기
중이거나, 설정이 다르거나, 미연결이면 프로필 `reconfigure` 후 필요한
스캔과 재연결을 요청한다.

다른 SSID로 전환할 때 다음을 모두 만족하면 `reconfigure`(항상 새로 스캔한다)
대신 supplicant 메모리의 SSID만 바꾸고 `disconnect`와 `reassociate`를 요청한다:
단일 `network`에 연결된 상태, supplicant의 BSS 목록에서 요청 SSID를 마지막 스캔에서
봤고(가장 최근에 본 AP와 5초 이내) 그 시점이 supplicant의 `scan_res_valid_for_connect`
(설정 `mlanN.wpa_supplicant.scan_res_valid_for_connect`, 기본 180초)보다 최근,
현재 supplicant의 SSID와 전역·네트워크 `freq_list`가
저장된 설정과 같음, 새 설정 파일이 SSID 줄만 다름, 인증 설정 변경 대기와
fallback 없음. supplicant는 마지막 스캔이 이 유효시간 안이고 그 결과에 대상 AP가
있으면 새 스캔 없이 연결한다. 없으면 supplicant가 곧바로 스캔한다.

백그라운드 스캔이 현재 SSID만 지정하는 기본 설정(`bgscan.ssid_filter=true`)에서는
대개 마지막 스캔에 요청 SSID가 없다. 이때 다음 조건이면 연결을 끊기 전에 요청 SSID를
먼저 스캔한다(#399).
- SSID 인자 하나인 전환
- fallback 없음
- 연결된 상태
- 저장 설정에 `freq_list`가 있음
- 인증 설정 변경 대기 없음

스캔 명령은 `iw <iface> scan freq <freq_list> ssid <SSID>`이다. 연결된 상태에서 4채널에
약 270ms가 걸리고, 핸들러는 그 끝을 기다린다. 상한은 채널당 0.15초에 0.3초를 더해 정수 초로
올린 값이다. 스캔으로 요청 SSID를 보면 위 경로로 연결한다. 스캔이 실패하거나(EBUSY 등),
상한을 넘거나, 요청 SSID가 보이지 않으면 `reconfigure` 경로를 쓴다. 이 경로는
`connect_threshold`처럼 supplicant가 설정을 읽을 때 함께 읽는 `wifi_init_conf.json`
값을 다시 읽지 않으므로, 그런 값을 바꾼 뒤에는 supplicant를 재시작해 적용한다.

fallback이 비활성인 기존 경로는
`ASSOC_TIMEOUT_DEFAULT`를 그대로 사용한다. `mlanN.connect_fallback.enabled=true`일 때만
`attempt_timeout_sec`(기본 15초)을 1~15초 범위에서 새 연결 예산으로 사용한다.

SSID 없이 호출할 때 정상 적용 경로는 설정 파일을 수정하지 않는다. 단, fallback을
활성화했고 연결이 실패하면 rollback이 저장된 이전 설정 파일을 복원할 수 있다. 다중 `network`
구성에서는 명시적 SSID 일괄 변경을 거부하지만 인터페이스만 지정한 연결은 허용한다.
`200 SUCCESS`는 목표 association을 확인했다는 뜻이다.
`550 Failed: wconnect exited 8`은 제한 시간 안에 확인하지 못했다는 뜻이며,
이후 늦게 연결될 수 있으므로 `wstatus`로 다시 조회한다.

일반 저장 우선 경로에서 `mlanN.connect_fallback.enabled=false`(기본)이면
실패한 새 SSID·주파수·인증 설정을 그대로 유지하며 자동 복구하지 않는다.
활성화했고 직전 검증 완료 프로필이 있으면
실패 시 SSID, 주파수 정책, 인증 설정을 함께 복원하고 reconfigure한 뒤 이전 association을
`rollback_timeout_sec`(기본 5초, 1~5) 안에서 확인한다. 복원에 성공해도 새 연결 실패가
성공으로 바뀌지는 않으며 원래 nonzero 결과를 반환한다. 일반 association/control 오류는
7/8이고, 검증·설치 오류 1이나 signal 결과 129/130/143도 보존될 수 있다. 사용자 출력은
`fallback restored`/`fallback failed`/`fallback unavailable`, syslog는
`outcome=restored`/`outcome=restore-failed`/`outcome=fallback-unavailable`로 구분한다.
known-good이 없는 경우에도 새 연결은
시도하지만 실패 시 복구할 설정이 없음을 명시한다. active fallback 중 같은 인터페이스의
변경 명령은 코드 6(busy)으로 거부하며 `wssid`/`wfreq`/`wstatus` 조회는 허용한다.
`wconnectraw`, `ifcup`/`ifcdown`, `wifcup`/`wifcdown`도 이 change guard를 사용한다.

마지막 검증 완료 프로필은 `/run/wifi` 아래 mode `0600` 런타임 상태로만 보관되어
재부팅 뒤에는 남지 않는다. Mode A와 다중 `network` 구성은 supplicant가 소유하므로
fallback에서 제외된다. `wconnectraw`, OPC helper, checker/background reconnect/roam도
fallback을 시작하지 않는다. OPC의 경쟁 wireless profile/radio helper write는 active
fallback 동안 busy로 거부되지만 ChangeIp의 IP/route 처리는 기존의 별도 계약을 유지한다.
새 연결과 복원의 시간값은 FTP 30초 제한 안에서 실제
경과 시간을 차감하는 userspace 예산이며 커널 I/O hang의 hard cap을 보장하지 않는다.

### 3.4 wstatus: 현재 연결

~~~text
quote wstatus <mlan0|mlan1|0|1>
~~~

`wpa_cli status`가 `wpa_state=COMPLETED`일 때 현재 SSID와 연결 주파수
한 개를 반환한다. SSID에 공백이 있으면 마지막 숫자 토큰이 MHz이고 그 앞의
전체 문자열이 SSID다. 미연결이지만 supplicant 상태 조회가 가능하면 코드
`8`, 상태 조회 자체가 실패하면 `7`을 반환한다.

### 3.5 wconnectraw: 빠른 런타임 전환과 성공 시 저장

~~~text
quote wconnectraw <mlan0|mlan1|0|1> <ssid words...>
~~~

SSID는 필수이며 1~32바이트, FTP 디스패처의 허용 문자여야 한다.
현재 선택된 `network` ID가 없으면 유일하게 활성화된
`network`를 찾는다. `wpa_cli set_network <id> ssid ...`, `wpa_cli disconnect`,
`wpa_cli reassociate`를 차례로 제출한다. 세 응답이 모두 정확히 `OK`이고
`/run/wifi`에 저장 요청을 원자적으로 게시하면 `200 SUCCESS`를 반환한다.
이 응답은 목표 AP 연결이나 파일 저장의 완료를 뜻하지 않는다.

별도 systemd path/service가 FTP 프로세스 밖에서 요청을 받고, 최대 15초 동안
`wpa_state=COMPLETED`, 목표 SSID와 동일한
`network` ID·BSSID 변경을 확인한다. 단일 `network`의 Mode B이고 그 사이
저장 프로필이 바뀌지 않았을 때만 SSID 행을 원자적으로 저장한다.
저장 파일의 전역·`network` `freq_list` 또는 기존 `scan_freq`가 현재 연결
주파수를 제외하면 저장하지 않고 `frequency_outside_profile`을 기록한다.
PSK와 주파수 설정은 바꾸지 않는다. `mlan0`의 영구 SSID 변경이면 OPC의
이전 ESSID 복원 표식도 제거한다. 완료 여부는 `quote wstatus <iface>`로
현재 연결을, `quote wssid <iface>`로 저장된 SSID를 각각 조회한다.
설정이 저장되면 재부팅 뒤에도 새 SSID를 사용한다. 연결 실패, 15초 초과,
다중 `network`/Mode A, 경쟁 설정 변경이면 파일은 이전 상태로 남는다.
저장 작업의 `ftpcmd_raw_commit` 로그는 연결 미완료
`association_timeout`과 supplicant 제어 실패 `wpa_cli_timeout`/
`wpa_cli_error`를 구분한다.
저장 확인 전에 재부팅하면 요청이 사라질 수 있다(`/run/wifi`는 휘발성이다).
연속 명령은 최신 저장 요청이 앞선 미완료 요청을 대체한다.
이 경우 `reconfigure` 또는 supplicant 재시작은 저장된 이전 프로필을 적용한다.
다른 PSK가 필요한 전환에는 `wpsk --stage`와 일반 `wconnect`를 사용한다.
`wconnectraw`는 staged PSK를 적용하지 않는다.
일반 `wpsk` 또는 `wifi psk`/`wifi key`로 저장한 인증 정보가 아직 연결
검증되지 않아 `credential-pending` 상태이면 `wconnectraw`는 코드 `6`으로
무선 명령 전에 거절한다. 저장 요청 이후 이 상태가 생겨도 비동기 작업은
SSID를 저장하지 않는다. 일반 `wconnect`로 새 인증 정보를 적용하고 연결을
검증한 뒤 다시 시도한다.

`disconnect`를 먼저 보내는 이유는 기존 연결을 유지한 채 다른 AP로 연결을
요청하면 연결 요청이 `EALREADY`로 거부되고, wpa_supplicant가 목표 BSSID를
ignore list에 올린 뒤 스캔을 한 번 더 하고 나서야 연결하기 때문이다. 같은
SSID를 지정해도 연결을 한 번 끊고 다시 연결한다.

`wpa_cli status`가 `wpa_state=INTERFACE_DISABLED`(예: `quote ifcdown` 뒤)이면
아무것도 바꾸지 않고 코드 `1`로 끝난다. 이 상태에서는 `reassociate`가
거부되는데, `disconnect`만 적용되면 인터페이스를 다시 올린 뒤에도
wpa_supplicant가 스스로 연결하지 않기 때문이다.
`set_network`가 실패하면 그 뒤 명령을 보내지 않으므로 현재 연결은 그대로다.
`disconnect` 뒤의 wpa_supplicant는 `reassociate`나 `reconnect`가 올 때까지
다시 연결하지 않는다. 그래서 `disconnect`가 실패해도 `reassociate`는 보낸다.
`disconnect` 이후 단계가 실패해 코드 `7`이 나오면 연결이 끊긴 채 남을 수
있다. `wifi_checker`가 동작 중이면 미연결이 `MAX_UNSTABLE_DURATION`(기본
10초) 이상 이어질 때 재연결을 한 번 요청한다. 바로 복구하려면
`quote wconnect <iface>`로 저장된 프로필을 다시 적용한다.

독립 저장 작업 시작이 실패하면 코드 `9`를 반환하지만 재연결 명령은 이미
제출된 상태라 링크가 새 AP로 전환될 수 있다. 이때 저장 파일은 이전 상태이며,
연결과 저장 결과를 각각 조회해야 한다.

## 4. 오류 코드

입력 문자·바이트·토큰 제한 위반은 핸들러 실행 전에
`501 Invalid command arguments.`로 거부되며 핸들러 종료 코드 `N`이 없다.
핸들러가 실패 코드로 종료하면 `550 Failed: <command> exited <N>`이다.
vsftpd가 핸들러를 30초에 강제 종료하면 `550 Command timed out: <command>`를
반환하며 `N`은 없다. 다음 표는 핸들러가 반환한 명령별 `N`의 의미다.

| 명령 | 코드 | 의미 |
| --- | ---: | --- |
| `wssid`, `wfreq` | `2` | 필수 인터페이스 누락 또는 잘못된 값 |
| `wssid` | `1` | 설정 조회, SSID 검증 또는 저장 실패 |
| `wfreq` | `1` | 설정 조회, 주파수 검증 또는 저장 실패 |
| `wscan` | `1` | 스캔 실행 환경 또는 설정 주파수 조회 실패 |
| `wscan` | `2` | 인터페이스 또는 대상 SSID 누락·오류 |
| `wscan` | `4` | 이번 스캔의 유효한 대상 BSS 없음 |
| `wscan` | `6` | 같은 인터페이스의 설정·스캔 전환이 진행 중(busy) |
| `wscan` | `124` | 내부 25초 스캔 제한 시간 초과 |
| `wpsk` | `1` | 비밀번호 검증 또는 저장 실패 |
| `wpsk` | `2` | 인터페이스 또는 비밀번호 누락·오류 |
| `wpsk` | `6` | 같은 인터페이스의 설정 변경 진행 중 또는 파일 저장·휘발 PSK 방식 충돌(busy) |
| `wconnect` | `1` | SSID 또는 설정 검증 실패, 실행 환경 오류 |
| `wconnect` | `2` | 필수 인터페이스 누락 또는 잘못된 값 |
| `wconnect` | `3` | 대상 인터페이스가 FTP 제어 연결을 운반하여 거부 |
| `wssid`, `wfreq`, `wconnect`, `wconnectraw`, `ifcup`, `ifcdown`, `wifcup`, `wifcdown` | `6` | 같은 인터페이스에서 fallback 또는 경쟁 변경이 진행 중(busy). `wconnectraw`는 미검증 인증 정보가 대기 중이어도 거절 |
| `wconnect` | `7` | supplicant 제어 명령 또는 연결 이벤트 감시 실패 |
| `wconnect` | `8` | 제한 시간 내 목표 연결 확인 실패 |
| `wstatus` | `2` | 인터페이스 인자 오류 또는 인자 초과 |
| `wstatus` | `7` | supplicant 상태 조회 실패 또는 필수 상태값 누락 |
| `wstatus` | `8` | 상태 조회는 성공했으나 현재 연결되지 않음 |
| `wconnectraw` | `1` | 상태·프로필 조회 실패, 인터페이스 비활성(`INTERFACE_DISABLED`), 저장 작업 실행 환경 오류, 또는 사용할 `network`를 결정할 수 없음 |
| `wconnectraw` | `2` | 필수 인터페이스 누락·오류 또는 SSID 누락·검증 실패 |
| `wconnectraw` | `7` | `set_network`, `disconnect` 또는 `reassociate` 명령 실패. `set_network` 실패면 현재 연결은 그대로이고, 그 뒤 단계 실패면 연결이 끊긴 채 남을 수 있다 |
| `wconnectraw` | `9` | 무선 명령 제출 후 저장 요청 파일 게시 실패. 연결은 전환될 수 있지만 파일은 이전 상태 |

`wssid`, `wfreq`, `wscan`, `wconnect`는 하위 명령의 종료 코드를 전달할 수 있으므로
표에 없는 `N`이 나올 수도 있다. 핸들러 로그의 `code`와 `detail`을 함께
확인한다. 첫 토큰이 `mlan0`, `mlan1`, `0`, `1` 이외인 `wconnect`
요청은 SSID 요청으로 해석하지 않고 코드 `2`로 거부한다.

다음은 0.7.0의 실패 응답 형식이다. `wstatus mlan1`의 코드 `7`은
해당 인터페이스에서 supplicant 상태 조회가 실패하는 경우의 예다.

~~~text
ftp> quote wstatus 0 ;
501 Invalid command arguments.
ftp> quote wfreq 0 invalid-frequency
550 Failed: wfreq exited 1
ftp> quote wstatus invalid-interface
550 Failed: wstatus exited 2
ftp> quote wconnectraw
550 Failed: wconnectraw exited 2
ftp> quote wstatus mlan1
550 Failed: wstatus exited 7
~~~

`mlan1`이 연결되어 있다면 응답은 달라진다. 유효하지 않은 33바이트
SSID의 `wconnect` 요청은 하위 SSID 검증에서 코드 `1`로 실패할 수 있다.

## 5. 인터페이스와 재부팅 명령

| 명령 | 호출 형식 | 성공 응답 | 실패 코드 |
| --- | --- | --- | --- |
| `getifstate` | `quote getifstate [interface]` | `200 <interface> UP` 또는 `200 <interface> DOWN` | `1`: 상태 읽기 실패, `2`: 이름 오류/인터페이스 없음 |
| `ifcup` | `quote ifcup [interface]` | `200 IFCUP command successful.` | `1`: UP 실패, `2`: 이름 오류/인터페이스 없음, `6`: fallback busy |
| `ifcdown` | `quote ifcdown [interface]` | `200 IFCDOWN command successful.` | `1`: DOWN 실패, `2`: 이름 오류/인터페이스 없음, `3`: FTP 제어 연결 보호, `6`: fallback busy |
| `rst` | `quote rst` | `200 reboot requested` | `1`: 재부팅 요청 실패, `2`: 인자 오류 |

인터페이스 명령은 인자를 생략하면 `mlan0`을 사용한다. `getifstate`의
`UP`/`DOWN`은 무선 연결 상태가 아니라 `/sys/class/net/<interface>/flags`의
관리 상태다. `ifcdown`은 FTP 제어 연결을 운반하는 인터페이스를 내리는
요청을 거부한다. `rst`는 systemd에 재부팅을 요청하므로 성공 응답 직후
FTP 연결이 끊길 수 있다.

같은 동작을 하는 `w` 명령도 제공한다. `wrst`의 인터페이스는 선택적이다.

| 신규 명령 | 형식 | 기존 동작·성공 응답 | 주요 오류 코드 |
| --- | --- | --- | --- |
| `wgetifstate` | `quote wgetifstate <mlan0\|mlan1\|0\|1>` | `getifstate`: `200 <interface> UP` 또는 `DOWN` | `1`: 상태 읽기 실패, `2`: 인자 오류/인터페이스 없음 |
| `wifcup` | `quote wifcup <mlan0\|mlan1\|0\|1>` | `ifcup`: `200 IFCUP command successful.` | `1`: UP 실패, `2`: 인자 오류/인터페이스 없음, `6`: fallback busy |
| `wifcdown` | `quote wifcdown <mlan0\|mlan1\|0\|1>` | `ifcdown`: `200 IFCDOWN command successful.` | `1`: DOWN 실패, `2`: 인자 오류/인터페이스 없음, `3`: FTP 연결 보호, `6`: fallback busy |
| `wrst` | `quote wrst [mlan0\|mlan1\|0\|1]` | `rst`: `200 reboot requested` | `1`: 재부팅 요청 실패, `2`: 인자 오류 |

`wgetifstate`, `wifcup`, `wifcdown`은 인터페이스 한 개를 필수로 받는다.
`wrst`는 인자 없이 호출하거나 유효한 인터페이스 한 개를 지정할 수 있다.
네 명령 모두 잘못된 인터페이스나 추가 인자는 코드 `2`로 거부한다.
`wgetifstate 1`의 응답에는 별칭 `1`이 아니라 `mlan1`이 표시된다.
`quote wrst`와 `quote wrst 0` 모두 **장비 전체**를 재부팅한다.
인터페이스만 재시작하는 명령이 아니다. 기존 세 인터페이스 명령의
숫자 인자는 계속 그대로 인터페이스 이름으로 처리하며 별칭 변환은 하지 않는다.

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
| `cache_connect_requested` | SSID만 메모리에서 바꾸고 최근 스캔 결과로 `reassociate` 요청 |
| `reconnect_requested` | `reassociate` 또는 `reconnect` 요청 |
| `connected_event`, `disconnected_event` | supplicant 비동기 이벤트 수신 |
| `association_verified`, `association_timeout` | 목표 연결 확인 또는 시간 초과 |
| `reply_ready` | 핸들러 종료 및 FTP 응답 준비 |

`cache_connect_requested`와 `reconfigure_requested`의 `prescan=` 값은 §3.3의 사전 스캔
결과다. 값의 뜻은 다음과 같다.
- `none`: 스캔하지 않음(요청 SSID가 이미 마지막 스캔에 있거나 조건이 맞지 않음)
- `0`: 스캔 완료
- 그 밖의 값: 스캔 명령의 종료 코드(예: `124` 상한 초과, `240` 다른 스캔과 겹침)

`elapsed_ms`는 핸들러 시작 이후 누적 시간이다. 연속 단계의 차이가 해당
구간 시간이다. 비동기 이벤트는 다른 단계와 출력 순서가 바뀔 수 있다.
`reply_ready` 이후 FTP 전송 시간은 포함되지 않는다. `wifi.sh` 자체의
로그는 `[wifi.sh:<line>]` 태그를 사용하며 `freq` 명령의 모든 주파수
인자를 기록한다.

`wconnectraw`는 연결 완료가 아닌 명령 제출 시간만 기록한다. `set_network`가
실패하면 타이밍 줄 없이 실패 줄만 남긴다. 비동기 저장 작업은 같은 trace로
`status=saved`, `status=skipped` 또는 `status=failed`를 별도 기록한다.

~~~text
ftpcmd_raw_timing trace=<id> phase=commands_submitted elapsed_ms=<ms> iface=<iface> parse_ms=<ms> id_lookup_ms=<ms> set_network_ms=<ms> disconnect_ms=<ms> reassociate_ms=<ms> set_rc=<rc> disconnect_rc=<rc> reassociate_rc=<rc>
ftpcmd_raw_result trace=<id> command=wconnectraw status=fail code=<N> iface=<iface> detail=<detail>
ftpcmd_raw_commit trace=<id> iface=<iface> status=<saved|skipped|failed> reason=<reason>
~~~

`wssid`, `wfreq`, `wscan`, `wpsk`, `wstatus` 실패는
`ftpcmd_result command=<command> status=fail code=<N> ...`로 기록한다.
설치된 rsyslog 파일에서
`ftpcmd_` 또는 `[wifi.sh:`를 검색하면 된다.

## 7. 운용 제약

- FTP 제어 연결이 암호화되지 않았다면 `wpsk`의 PSK를 포함한 명령 인자가
  평문으로 전달된다. PSK는 `wpsk`로만 전달하고 `wconnect`나
  `wconnectraw` 인자로 넣지 않는다.
- `wconnect`, `ifcdown`, `wifcdown`은 FTP 제어 연결을 운반하는 인터페이스를
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

## 8. 자동 검증 프로그램

호스트에서 Python 3로 `scripts/qa/ftp_quote_verify.py`를 실행한다. 추가 Python
패키지는 필요하지 않다. 비밀번호는 숨김 입력으로 받으며, 명령줄 인자나
보고서에 기록하지 않는다.

~~~sh
python3 scripts/qa/ftp_quote_verify.py \
  --host <HOST> --user <USER> --iface 0 \
  --repeat 3 --timeout 40 --report /tmp/ftp-quote-report.json
~~~

`wstatus`의 인터페이스 필수 규칙을 먼저 확인하고 다른 계약이면 중단한다.
프로필은 `wssid mlan0`·`wfreq mlan0`처럼 명시적 이름으로만 조회하며, 숫자
별칭은 상태 조회 명령으로 검사한다. 기본 `--mode read-only`에서는 설정
저장·링크 변경·연결 전환·재부팅을 하지 않는다. `--iface 1`은 `mlan1`을
선택하며 실제 연결 상태를 확인할 수
없는 인터페이스의 검사는 실패로 기록한다.

검사는 조회 응답, 상태 조회의 숫자 별칭, 필수·추가 인자, 허용 문자,
8개/9개 토큰과 255/256바이트 경계, 거부 응답 이후 같은 세션의 조회를 포함한다.
응답 대기 중 전송 오류가 나면 소켓을 닫고 새 연결로 조회를 한 번 확인한 뒤
중단한다. 복구 조회가 성공해도 최초 오류를 통과로 바꾸지 않는다.

`--timeout`은 클라이언트 소켓의 응답 대기 시간이며 서버의 30초 제한을 바꾸지
않는다. `--repeat`은 조회 검사 반복 횟수다. 자동 실행은 호출자 소유의 일반
파일(모드 `0600` 또는 `0400`)을 `--password-file`로 지정한다.

JSON 보고서는 검사별 성공 여부, FTP 응답 코드와 소요 시간을 담는다. SSID는
원문 대신 SHA-256으로 남긴다. 보고서는 모드 `0600`으로 새로 생성하며 기존
파일은 덮어쓰지 않으므로 재실행할 때 새 경로를 지정한다. 종료 코드는 전체
통과 `0`, 검증 실패 `1`, 입력·보고서 처리 오류 `2`다. 이 프로그램의 통과는
현재 명령 계약에 대한 증거이며 장시간 안정성·로밍·설치·외부 배포 완료를
뜻하지 않는다.

### 망 전환 성공 검증

`--mode switch`를 선택하면 조회 검사를 통과한 뒤 지정한 AP로 전환한다.
타겟, 계정, 인터페이스와 목표 AP는 실행 인자로 설정하며 고정값이 아니다.

~~~sh
python3 scripts/qa/ftp_quote_verify.py \
  --host <HOST> --user <USER> --iface 0 \
  --mode switch --switch-ssid "Office AP B" --switch-freq 40 \
  --repeat 3 --timeout 40 --report /tmp/ftp-switch-report.json
~~~

`--switch-ssid`는 목표 SSID, `--switch-freq`는 목표 AP의 MHz 또는 채널 번호다.
채널 `36`은 `5180`, 채널 `40`은 `5200` MHz로 정규화한다. 목표 주파수는
한 개를 지정한다. Wi-Fi 인증은 타겟에 저장된 PSK·보안 설정을 재사용하므로
목표 AP도 그 설정으로 연결할 수 있어야 한다. FTP 로그인 비밀번호와
Wi-Fi 인증 정보는 별개다.

각 전환은 `wssid`·`wfreq`로 저장하고 조회한 뒤 `wconnect`로 적용한다.
`200 SUCCESS`와 함께 `wstatus`가 목표 SSID와 주파수 모두에 일치해야 통과한다.
시작 시 이미 같은 SSID·주파수에 연결되어 있으면 전환 증거가 되지 않으므로
설정을 바꾸기 전에 중단한다. `--repeat` 횟수만큼 목표 망과 원래 망 사이를
왕복하며, 첫 실패 이후 추가 전환은 수행하지 않는다.

시작 시 저장 SSID·공통 주파수와 실제 연결 상태를 보관한다. 매번 성공·실패
후에는 새 FTP 연결로 원래 설정을 저장하고 조회한 뒤 재연결하여 시작 시의
SSID·연결 주파수를 확인한다. 이 복구는 검증 프로그램의 테스트 정리 절차다.
일반 `wconnect`는 연결 실패 시 이전 SSID·주파수로 자동 롤백하지 않는다.
실패 후 복구되어도 최초 실패를 통과로 바꾸지 않으며 보고서의 `restoration`
필드에 복구 시도와 결과를 별도로 남긴다.

FTP로 원래 설정을 다시 저장할 수 있어야 하므로 초기 SSID는 FTP 허용 문자와
공백 규칙을 만족하고, 초기 주파수 목록은 최대 7개여야 한다. `ANY`는 FTP 설정
명령으로 복원할 수 없어 변경 전에 중단한다. 저장 설정과 시작 시 연결의
SSID·주파수가 다르거나 연결을 확인할 수 없는 경우도 중단한다. 복원 범위는
조회 가능한 SSID·공통 주파수와 연결 상태이며 전체 프로필 파일의 바이트
복원은 검사하지 않는다.

전환 중에도 접근할 수 있도록 FTP 제어는 유선 등 별도 관리 경로로 연결한다.
전환 모드의 클라이언트 타임아웃은 최소 35초다. 변경 명령 도중 전송 오류가
나면 서버 핸들러의 30초 제한이 끝날 때까지 기다린 뒤 복구를 시도한다.
전환 모드에서 `SIGINT`(Ctrl-C)·`SIGTERM`을 받으면 중단 요청으로 기록한다.
현재 FTP 응답과 복구를 마친 뒤 보고서의 `interrupted`에 신호 이름을 남기고
종료 코드 `1`로 끝낸다. 중단 요청 이후 새 목표 설정이나 다음 전환 회차를
시작하지 않으며, 복구 명령은 계속 수행한다. FTP 접근 자체가 끊기거나
`SIGKILL`·전원 차단으로 종료되면 복구 완료를 보장할 수 없으므로
`restoration.passed`를 확인한다.
종료 결과는 최종 보고서 기록 시점에 확정하며 표준 출력과 종료 코드도
그 파일에 맞춘다. 보고서 작성 중 받은 신호는 중단 실패로 다시 기록한다.
복구 도중 전송 오류가 발생하면 같은 대기 규칙을 적용하고 새 연결로 한 번
재시도한다. 이미 원래 값인 필드는 다시 저장하지 않는다. 조회한 설정이
원래 값과 이번 테스트의 목표 값 어느 쪽도 아니면 다른 변경으로 판단해
덮어쓰지 않고 `restoration_error=conflict`로 기록한다. 조회와 저장 전체를
잠그는 기능은 없으므로 테스트 동안 다른 프로그램에서 프로필을 변경하지 않는다.
