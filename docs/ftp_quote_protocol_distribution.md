# WLAN 장비 FTP QUOTE 명령 규격

이 문서는 FTP 클라이언트에서 WLAN 장비를 제어할 때 사용하는 `quote` 명령과
응답을 정의한다. 명령은 FTP 제어 연결에서 실행한다.
인터페이스 필수 규칙은 `wlan-proc` 0.7.0부터 적용된다.

## 공통 규칙

```text
ftp> quote <명령> [인자...]
```

| 결과 | FTP 응답 |
| --- | --- |
| 성공 | `200 <결과>` |
| 입력 문자·바이트·토큰 제한 위반 | `501 Invalid command arguments.` |
| 명령 실행 실패 | `550 Failed: <명령> exited <오류 코드>` |
| 명령 실행 30초 초과 | `550 Command timed out: <명령>` |

`501`은 장비의 FTP 디스패처가 핸들러 실행 전에 입력을 거부한 응답이다.
`550 Failed: ... exited ...`의 숫자는 실행된 핸들러의 종료 코드다.
아래 입력 제한은 장비 이미지의 FTP 디스패처 기준이다.

`wrst`를 제외한 `w`로 시작하는 모든 명령은 첫 인자로 인터페이스를 필수로 받는다.
`mlan0` 또는 `0`은 `mlan0`, `mlan1` 또는 `1`은 `mlan1`을 선택한다.
`wrst`는 인터페이스를 생략할 수 있으며, 지정하면 같은 네 값만 허용한다.
나머지 `w` 명령의 인터페이스 누락이나 잘못된 값은 오류 코드 `2`다. 성공한 설정 명령과
`wconnect`의 결과는 `200 SUCCESS`이며,
`wconnect` 응답에는 SSID와 주파수를 포함하지 않는다.

## 무선 명령

| 명령 | 형식 | 인터페이스만 지정 | 인터페이스 뒤 값 지정 |
| --- | --- | --- | --- |
| `wssid` | `quote wssid <mlan0\|mlan1\|0\|1> [SSID...]` | 설정 파일의 SSID 조회 | 설정 파일에 SSID 저장 |
| `wfreq` | `quote wfreq <mlan0\|mlan1\|0\|1> [주파수/채널...]` | 설정 파일의 주파수 조회 | 설정 파일에 주파수 저장 |
| `wscan` | `quote wscan <mlan0\|mlan1\|0\|1> <SSID...>` | SSID 누락 오류 | 최신 스캔에서 SSID·RSSI·주파수 조회 |
| `wpsk` | `quote wpsk <mlan0\|mlan1\|0\|1> [--stage] <비밀번호...>` | 비밀번호 누락 오류 | PSK 저장 또는 휘발 저장 |
| `wconnect` | `quote wconnect <mlan0\|mlan1\|0\|1> [--save\|--no-save] [--] [SSID...]` | 저장된 설정으로 연결 | SSID를 저장한 뒤 연결 |
| `wstatus` | `quote wstatus <mlan0\|mlan1\|0\|1>` | 현재 연결 조회 | 추가 인자 오류 |
| `wconnectraw` | `quote wconnectraw <mlan0\|mlan1\|0\|1> [--save\|--no-save] [--] <SSID...>` | SSID 누락 오류 | 빠른 런타임 전환과 성공 시 비동기 SSID 저장 요청 |

`wssid`와 `wfreq`의 설정은 파일에만 저장된다. 저장한 값을 연결에 적용하려면
`wconnect`를 호출한다. `wstatus`는 저장된 값이 아니라 현재 연결된 SSID와
주파수(MHz)를 반환한다.

`wpsk <iface> <비밀번호>`는 8~63바이트 비밀번호를 설정 파일의 모든
`network` 블록에 저장한다. 다음 `wconnect`가 적용하며, 기본
`connect_fallback.enabled=false`에서는 연결 실패 뒤에도 새 PSK가 남는다.
`wpsk <iface> --stage <비밀번호>`는 `/run/wifi`에 mode `0600`으로만
저장한다. 단일 PSK network의 Mode B에서 `wconnect <iface> <SSID>`가
연결 성공을 확인하면 SSID와 PSK를 함께 파일에 저장한다. 성공한
전환은 재부팅 후에도 유지된다. `--stage`는 인터페이스 바로 뒤에 놓는다.
같은 인터페이스에 파일 저장 PSK가 적용 대기 중이면 `--stage`를, 휘발
PSK가 대기 중이면 일반 `wpsk`를 코드 `6`으로 거절한다. 먼저 선택한
방식의 `wconnect`를 마쳐야 다른 방식으로 바꿀 수 있다. 휘발 stage는
재부팅하면 사라지고, 파일 저장 PSK는 남는다.
FTP 제어 연결이 암호화되지 않았다면 비밀번호가 평문으로 전달된다.
이 명령은 비밀번호의 로컬 프로세스 인자 노출도 허용한다. `wpsk`와 하위
`wifi` 실행 인자, `--stage` 연결 시 `wpa_cli` 인자를 조회할 수 있는 계정은
비밀번호를 볼 수 있다. 핸들러는 비밀번호를 FTP 응답이나 자체 로그에는
기록하지 않는다.

`wscan`은 설정 파일의 공통 주파수 목록에서 대상 SSID를 지정해 스캔한다.
목록이 없으면 전대역 스캔을 한다. 이번 스캔에서 확인한 동일 SSID 중
가장 강한 BSS를 `200 SSID="<SSID>" RSSI=<dBm> FREQ=<MHz>`로 반환한다.
발견되지 않으면 오류 코드 `4`다. 이 명령은 설정을 저장하거나 연결을
전환하지 않는다. 예를 들어 전환 전에 다음처럼 확인할 수 있다.

```text
ftp> quote wscan 0 Office AP B
200 SSID="Office AP B" RSSI=-50.00 FREQ=5180
```

```text
ftp> quote wssid 0 Office AP
200 SUCCESS
ftp> quote wfreq 0 5180 5200 5220 5240
200 SUCCESS
ftp> quote wssid 0
200 Office AP
ftp> quote wfreq 0
200 5180,5200,5220,5240
ftp> quote wconnect 0
200 SUCCESS
ftp> quote wstatus 0
200 Office AP 5200
```

위 연결 주파수 `5200`은 응답 형식의 예시다. 실제 값은 장비가 연결한 AP에
따라 달라진다. `wstatus`의 SSID에 공백이 있으면 마지막 숫자가 주파수이고
그 앞의 전체 문자열이 SSID다.

### SSID와 주파수 인자

- 공백이 있는 SSID는 `quote wssid 0 Office AP` 또는
  `quote wconnect 0 Office AP`처럼 입력한다. 인터페이스 뒤 단어는 하나의 SSID로 합쳐진다.
  연속 공백은 하나로 합쳐진다. 실제 FTP 요청에 따옴표나 역슬래시를 넣지 않는다.
  요청에 `wssid 0 "Office AP"`처럼 따옴표가 포함되면 `501`로 거부된다.
  클라이언트가 입력 구문의 따옴표를 제거하는 경우에는 실제 전송 문자열을
  기준으로 판단한다.
- 첫 단어는 항상 인터페이스다. SSID 자체가 `mlan0`이면
  `quote wconnect 0 mlan0`, `1`이면 `quote wconnect 0 1`처럼 입력한다.
- 허용 문자는 ASCII 영문자(`A-Z`, `a-z`), 숫자(`0-9`),
  일반 공백(`U+0020`), `_ - . : , = + @ /`뿐이다. 한글 등 비 ASCII 문자,
  따옴표, 역슬래시, 세미콜론은 핸들러 실행 전에 `501`로 거부된다.
- 명령 이름을 제외한 인자 문자열은 공백을 포함해 최대 **255바이트**,
  공백으로 구분한 인자는 최대 **8개 토큰**이다. 인터페이스 인자도 한도에
  포함되므로 SSID 단어는 최대 7개다. 한도를 넘으면 `501`로 거부된다.
- `wssid` 또는 `wconnect`로 저장하는 SSID는 위 허용 문자로 구성한
  **1~32바이트**여야 한다. 33바이트 SSID는 핸들러 검증에서 실패하여 `550`을
  반환한다. FTP 입력 제한만으로 다른 경로로 저장한 SSID의 무선 연결
  가능 여부를 판단하지 않는다.
- `wfreq`는 MHz 또는 채널 번호를 받는다. 예를 들어 채널 `36 40 44 48`은
  `5180 5200 5220 5240` MHz로 저장된다. 조회 결과가 `200 ANY`면
  주파수 제한이 없다. `2G`, `5G` 단축어는 지원하지 않는다.
- 주파수를 변경할 때는 `wfreq`로 저장한 뒤 `wconnect`로 적용한다.
  `wconnect`는 주파수 인자를 받지 않으며, 숫자 단어도 SSID의 일부로
  처리한다(`quote wconnect 0 AP 2` → SSID `AP 2`).

### 연결 결과

일반 `wconnect`는 저장된 인증 정보로 연결을 시도한다. 단일 네트워크 프로필에서
저장 설정과 현재 런타임 설정이 일치하면 연결을 유지하고 성공을 반환한다.
다른 SSID로 전환할 때 SSID 외의 설정이 그대로이면 최근 스캔 결과로 바로 연결해
스캔 시간을 줄인다. 최근 스캔 결과에 대상 AP가 없으면 연결을 끊기 전에 대상 SSID를
먼저 스캔하므로 기본 설정에서도 같은 효과가 난다. 이 스캔 때문에 응답은 조금 늦어질 수
있지만 무선 연결이 끊겨 있는 시간은 줄어든다(랩 4채널 실측: 약 0.5초 → 약 0.15초).
연결 확인에 실패하면 `550 Failed: wconnect exited 8`을 반환할 수 있다.
이후 연결될 수도 있으므로 `quote wstatus 0`으로 실제 상태를 확인한다.

일반 저장 우선 경로의 출하 기본값(`mlanN.connect_fallback.enabled=false`)에서는
저장된 새 SSID·주파수·PSK를 연결 실패 후에도 유지한다.
`wpsk --stage` 경로는 실패 시 이전 설정과 연결로 복구를 시도하며, 성공한
연결의 SSID·PSK만 파일에 저장한다. 이 경로는 연결된 단일 PSK network의
Mode B와 명시적 목표 SSID가 필요하다. 이전 망으로 돌아가려면 일반 경로에서는
이전 설정을 다시 저장하고
`wconnect`로 적용한다. fallback을 opt-in으로 활성화하면 수동 CLI/FTP connect에 한해
직전 검증 완료 프로필(SSID, 주파수 정책, 인증 설정)을 실패 시 복원하고 reconfigure한
뒤 이전 association을 확인한다. 복원 성공 여부와 무관하게 새 연결의 원래 nonzero 결과를
반환한다(일반 association/control 오류는 7/8). 사용자 출력은 `fallback restored`/
`fallback failed`/`fallback unavailable`, syslog는 `outcome=restored`/
`outcome=restore-failed`/`outcome=fallback-unavailable`로 구분한다. known-good이 없어도
새 연결 시도는 허용한다.

`wpsk --stage` 전환 중 프로세스가 강제 종료돼 미완료 표식이 남으면
유선·시리얼 경로에서 SSID 없이 `wconnect <iface>`를 호출해 원본 복구를
재시도할 수 있다. 복구가 확인되지 않으면 표식이 남는다. 전원 차단 시
`/run`의 복구 스냅샷은 사라지며, 저장 직후 응답 전에 차단됐다면 새
프로필이 남을 수 있다. 이 경우 저장 SSID·PSK를 유선·시리얼 경로에서
확인하고 필요한 기존 값을 다시 설정한다.

known-good은 가능한 경우 `wssid`/`wfreq` 또는 CLI의 PSK/key 변경 전에 캡처하고 새 목표
연결 성공 뒤 갱신한다. 검증되지 않은 사본은 사용하지 않는다. 상태는 `/run/wifi` 아래
mode `0600` 파일이며 재부팅 후 유지되지 않는다. fallback 활성 시 새 연결 대기는 기본
15초(설정 1~15), 복원 확인은 기본 5초(설정 1~5)이고 실제 경과 시간을 차감해 FTP 30초
제한 안의 예산을 관리한다. 비활성 경로는 기존 연결 제한시간 동작을 유지한다. 커널 I/O
hang까지 제한하는 hard cap은 아니다.

fallback 진행 중 같은 인터페이스의 변경은 코드 6(busy)으로 거부하지만 조회는 허용한다.
`wconnectraw`, `ifcup`/`ifcdown`, `wifcup`/`wifcdown`도 이 change guard를 사용한다.
OPC의 경쟁 wireless profile/radio helper write도 busy로 거부되지만 ChangeIp의 IP/route
처리는 기존의 별도 계약을 유지한다. OPC helper와 checker/background reconnect/roam은
fallback을 시작하지 않는다. `wconnectraw`, Mode A와 다중 `network` 구성도 대상이 아니며, 다중 프로필의
선택·복구는 supplicant가 소유한다.

`wconnect`·`wconnectraw`는 인터페이스 뒤, SSID 앞에 `--save` 또는 `--no-save`를
받는다. 기본값은 기존 저장 동작이다. `--no-save`는 conf·OPC 복원 표식을 변경하지
않고 런타임 연결에만 적용하며, 이전의 미완료 raw 저장 요청도 취소한다. 옵션처럼
생긴 SSID 앞에는 `--`를 넣는다. 설정 재적재·무선 재시작·재부팅 시 저장 설정이
다시 적용된다. 보드 셸의 `wifi <iface> connect`·`connectraw`도 같은 옵션을 받는다.

기본 저장 경로에서 `wconnectraw`의 `200 SUCCESS`는 무선 제어 명령 제출과 `/run/wifi` 저장 요청
게시의 성공이다. 보드 셸의 `wifi <iface> connectraw <SSID...>`와 같은 경로를
사용한다. 유효한 명령의 수신은 `ftpcmd_raw_timing phase=dispatch_received`로 기록하며,
무선 변경 전 checker의 자동 재연결 보호 구간(기본 20초)을 갱신한다. 이 구간은
고정 대기가 아니며, checker가 전환 확인 중 락을 점유해 수동 재시도를 막는 것을 방지한다.
FTP 프로세스 밖의 systemd path/service가 요청을 처리하며,
AP 연결 및 저장 완료를 기다리지 않는다. 연결이 확인되면
단일 `network`의 Mode B에서 변경되지 않은 설정 파일의 SSID만 저장한다.
PSK·주파수는 유지한다. `wstatus`로 연결을, `wssid`로 저장 결과를 확인한다.
저장 완료 뒤에는 재부팅해도 새 SSID가 유지된다. 저장 전 재부팅하면 휘발성
요청이 사라질 수 있고, 연속 명령의 최신 요청은 앞선 미완료 요청을 대체한다.
저장 작업 로그의 `association_timeout`은 연결 미완료, `wpa_cli_timeout`과
`wpa_cli_error`는 supplicant 제어 실패를 뜻한다.
연결 실패·시간 초과·
다중망·경쟁 설정 변경에서는 이전 저장 설정이 남는다. 다른 PSK를 쓰는
망에는 `wpsk --stage`와 `wconnect`를 사용한다. 코드 7 또는 9로 실패해도
링크가 끊기거나 전환 중일 수 있으므로 `wstatus`로 확인하고 필요하면
`quote wconnect <iface>`로 저장된 프로필을 다시 적용한다.

`wconnectraw --no-save` 경로는 비동기 저장 요청을 만들지 않는다. `200 SUCCESS`는
무선 명령 제출만 뜻하므로 실제 연결은 `wstatus`로 확인한다.

## 오류 코드

| 명령 | 코드 | 의미 |
| --- | ---: | --- |
| `wssid`, `wfreq`, `wconnect`, `wconnectraw`, `wstatus` | `2` | 필수 인터페이스 누락 또는 잘못된 값 |
| `wscan` | `1` | 스캔 실행 환경 또는 설정 주파수 조회 실패 |
| `wscan` | `2` | 인터페이스 또는 대상 SSID 누락·오류 |
| `wscan` | `4` | 이번 스캔의 유효한 대상 BSS 없음 |
| `wscan` | `6` | 같은 인터페이스의 설정·스캔 전환이 진행 중(busy) |
| `wscan` | `124` | 내부 25초 스캔 제한 시간 초과 |
| `wpsk` | `1` | 비밀번호 검증 또는 저장 실패 |
| `wpsk` | `2` | 인터페이스 또는 비밀번호 누락·오류 |
| `wpsk` | `6` | 같은 인터페이스의 설정 변경 진행 중 또는 파일 저장·휘발 PSK 방식 충돌(busy) |
| `wssid`, `wfreq` | `1` | 설정 조회, 입력 검증 또는 저장 실패 |
| `wconnect` | `1` | SSID 또는 설정 검증 실패 |
| `wconnect` | `3` | 대상 인터페이스가 현재 FTP 연결을 운반하여 요청 거부 |
| `wssid`, `wfreq`, `wconnect`, `wconnectraw`, `ifcup`, `ifcdown`, `wifcup`, `wifcdown` | `6` | 같은 인터페이스에서 fallback 또는 경쟁 변경이 진행 중(busy) |
| `wconnect` | `7` | 무선 제어 명령 또는 연결 이벤트 확인 실패 |
| `wconnect` | `8` | 제한 시간 내 목표 연결 확인 실패 |
| `wstatus` | `2` | 잘못된 인터페이스 인자 또는 인자 초과 |
| `wstatus` | `7` | 무선 상태 조회 실패 또는 필수 값 누락 |
| `wstatus` | `8` | 상태 조회는 가능하지만 현재 연결되지 않음 |
| `wconnectraw` | `1` | 상태·프로필 조회 실패, 인터페이스 비활성, 저장 작업 실행 환경 오류 또는 대상 네트워크 선택 실패 |
| `wconnectraw` | `2` | 필수 인터페이스 누락·오류, 저장 옵션 오류 또는 SSID 누락·검증 실패 |
| `wconnectraw` | `7` | 런타임 명령 실패 |
| `wconnectraw` | `9` | 런타임 전환 요청 후 저장 요청 파일 게시 실패 |

`501 Invalid command arguments.`와 `550 Command timed out: <명령>`에는
위 표의 핸들러 종료 코드가 없다. `wconnect`의 연결 확인 폴링 기본값은 15초지만
설정 처리 시간까지 포함한 전체 명령 시간의 상한은 아니다. 두 시간은
FTP 명령 인자로 변경할 수 없다.
`wscan`은 하위 스캔 명령의 다른 종료 코드도 전달할 수 있다.

## 인터페이스 및 재부팅 명령

| 명령 | 형식 | 성공 응답 | 주요 오류 코드 |
| --- | --- | --- | --- |
| `getifstate` | `quote getifstate [인터페이스]` | `200 <인터페이스> UP` 또는 `200 <인터페이스> DOWN` | `1`: 상태 읽기 실패, `2`: 인터페이스 오류 |
| `ifcup` | `quote ifcup [인터페이스]` | `200 IFCUP command successful.` | `1`: UP 실패, `2`: 인터페이스 오류, `6`: fallback busy |
| `ifcdown` | `quote ifcdown [인터페이스]` | `200 IFCDOWN command successful.` | `1`: DOWN 실패, `2`: 인터페이스 오류, `3`: FTP 연결 보호, `6`: fallback busy |
| `rst` | `quote rst` | `200 reboot requested` | `1`: 재부팅 요청 실패, `2`: 인자 오류 |

인터페이스 명령도 인자를 생략하면 `mlan0`을 사용한다. `getifstate`의
`UP`/`DOWN`은 인터페이스의 관리 상태로, AP 연결 여부와 다르다.
`ifcdown`은 현재 FTP 제어 연결을 운반하는 인터페이스를 내리는 요청을
거부한다. `rst` 성공 후에는 장비가 재부팅되므로 FTP 연결이 끊길 수 있다.

같은 동작을 하는 `w` 명령은 다음과 같다. `wrst`의 인터페이스는 선택적이다.

| 신규 명령 | 형식 | 기존 명령과 동일한 성공 응답 |
| --- | --- | --- |
| `wgetifstate` | `quote wgetifstate <mlan0\|mlan1\|0\|1>` | `200 <인터페이스> UP` 또는 `200 <인터페이스> DOWN` |
| `wifcup` | `quote wifcup <mlan0\|mlan1\|0\|1>` | `200 IFCUP command successful.` |
| `wifcdown` | `quote wifcdown <mlan0\|mlan1\|0\|1>` | `200 IFCDOWN command successful.` |
| `wrst` | `quote wrst [mlan0\|mlan1\|0\|1]` | `200 reboot requested` |

`wgetifstate`, `wifcup`, `wifcdown`은 인터페이스 한 개를 필수로 받는다.
`wrst`는 인자 없이 호출하거나 유효한 인터페이스 한 개를 지정할 수 있다.
잘못된 인터페이스나 추가 인자, 나머지 세 명령의 인터페이스 누락은 코드 `2`,
그 밖의 실패 코드는 위 기존 명령과 같다. `wifcup`/`wifcdown`도 active fallback 중에는
코드 `6`을 반환하며, `wifcdown`은 FTP 연결 보호도 유지한다.
`quote wrst`와 `quote wrst 0` 모두 `rst`와 동일하게 **장비 전체**를 재부팅한다.
기존 `getifstate`, `ifcup`, `ifcdown`의 기본값과 `rst`의 무인자 규칙은 유지하며,
`0`/`1` 별칭은 `w` 명령에 적용한다.

## 자동 망 전환 검증

소스 배포본의 `scripts/qa/ftp_quote_verify.py`는 Python 3로 실행하며 기본은
조회 검증이다. 망 전환 성공을 검사하려면 `--mode switch`, 목표 AP의
`--switch-ssid`, MHz 또는 채널 번호를 받는 `--switch-freq`를 함께 지정한다.

```sh
python3 scripts/qa/ftp_quote_verify.py \
  --host <HOST> --user <USER> --iface 0 \
  --mode switch --switch-ssid "Office AP B" --switch-freq 40 \
  --repeat 3 --timeout 40 --report /tmp/ftp-switch-report.json
```

목표 AP는 타겟에 저장된 Wi-Fi 인증 정보로 연결할 수 있어야 한다. 비밀번호는
숨김 입력으로 받으며 `--repeat`은 망 전환 왕복 횟수다. 채널 `40`은 `5200`
MHz로 설정한다. `wssid`·`wfreq`로 저장한 뒤 `wconnect`를 실행하고 `wstatus`의
SSID·주파수가 목표와 일치하는지 확인한다. 이미 목표 SSID·주파수에 연결된
경우에는 변경 전에 중단한다.

테스트 후 원래 SSID·주파수를 새 FTP 연결로 복원하고 재연결을 확인한다.
이것은 테스트 도구 자체의 정리 절차다. 출하 기본값처럼
`connect_fallback.enabled=false`인 일반 `wconnect`에는 자동 복구가 없다.
성공·실패와 복구 결과는 JSON 보고서에 별도로 기록하고 SSID 원문은 마스킹한다.
제어 연결은 유선 등 별도 관리 경로로 유지한다. 초기 주파수가 `ANY`이거나
목록이 7개를 넘고, 초기 SSID를 FTP로 다시 저장할 수 없거나 저장 설정과 현재
연결이 다르면 변경 전에 중단한다. 복원은 SSID·공통 주파수와 연결 상태를
검사하며 전체 프로필 파일의 바이트 동일성을 뜻하지 않는다.
복구 중 전송 오류에는 새 연결로 한 번 재시도하며, 원래 값인 필드는 다시
저장하지 않는다. 원래 값과 테스트 목표 값 이외의 설정을 발견하면 덮어쓰지
않고 충돌을 기록한다. 테스트 중에는 다른 프로그램에서 프로필을 변경하지 않는다.
전환 중 Ctrl-C 또는 `SIGTERM`을 받으면 현재 명령과 복구를 마친 뒤
`interrupted` 신호 이름을 보고서에 남기고 종료 코드 `1`로 끝낸다. 다음 전환은
시작하지 않는다. `SIGKILL`·전원 차단이나 FTP 경로 상실은 복구를 막을 수 있다.
