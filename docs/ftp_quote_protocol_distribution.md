# WLAN 장비 FTP QUOTE 명령 규격

이 문서는 FTP 클라이언트에서 WLAN 장비를 제어할 때 사용하는 `quote` 명령과
응답을 정의한다. 명령은 FTP 제어 연결에서 실행한다.

## 공통 규칙

```text
ftp> quote <명령> [인자...]
```

| 결과 | FTP 응답 |
| --- | --- |
| 성공 | `200 <결과>` |
| 명령 실행 실패 | `550 Failed: <명령> exited <오류 코드>` |
| 명령 실행 30초 초과 | `550 Command timed out: <명령>` |

무선 명령의 기본 인터페이스는 `mlan0`이다. `mlan1`을 사용하려면 첫 인자로
명시한다. 성공한 설정 명령과 `wconnect`의 결과는 `200 SUCCESS`이며,
`wconnect` 응답에는 SSID와 주파수를 포함하지 않는다.

## 무선 명령

| 명령 | 형식 | 인자 없음 | 값 지정 |
| --- | --- | --- | --- |
| `wssid` | `quote wssid [mlan0\|mlan1] [SSID...]` | 설정 파일의 SSID 조회 | 설정 파일에 SSID 저장 |
| `wfreq` | `quote wfreq [mlan0\|mlan1] [주파수/채널...]` | 설정 파일의 주파수 조회 | 설정 파일에 주파수 저장 |
| `wconnect` | `quote wconnect [mlan0\|mlan1] [SSID...]` | 저장된 설정으로 연결 | SSID를 저장한 뒤 연결 |
| `wstatus` | `quote wstatus [mlan0\|mlan1]` | 현재 연결 조회 | 인터페이스만 지정 가능 |
| `wconnectraw` | `quote wconnectraw [mlan0\|mlan1] <SSID...>` | 오류 | 시험용 런타임 전환 요청 |

`wssid`와 `wfreq`의 설정은 파일에만 저장된다. 저장한 값을 연결에 적용하려면
`wconnect`를 호출한다. `wstatus`는 저장된 값이 아니라 현재 연결된 SSID와
주파수(MHz)를 반환한다.

```text
ftp> quote wssid Office AP
200 SUCCESS
ftp> quote wfreq 5180 5200 5220 5240
200 SUCCESS
ftp> quote wssid
200 Office AP
ftp> quote wfreq
200 5180,5200,5220,5240
ftp> quote wconnect
200 SUCCESS
ftp> quote wstatus
200 Office AP 5200
```

위 연결 주파수 `5200`은 응답 형식의 예시다. 실제 값은 장비가 연결한 AP에
따라 달라진다. `wstatus`의 SSID에 공백이 있으면 마지막 숫자가 주파수이고
그 앞의 전체 문자열이 SSID다.

### SSID와 주파수 인자

- 공백이 있는 SSID는 `quote wssid Office AP` 또는
  `quote wconnect Office AP`처럼 입력한다. 남은 단어는 하나의 SSID로 합쳐진다.
- 첫 단어가 `mlan0` 또는 `mlan1`이면 인터페이스로 해석한다. SSID 자체가
  `mlan0`이면 `quote wconnect mlan0 mlan0`처럼 입력한다.
- FTP 디스패처는 인자를 최대 8개 토큰, 전체 255바이트로 제한한다.
  허용 문자는 영문자, 숫자, 공백, `_ - . : , = + @ /`이다. 연속 공백은
  하나로 합쳐진다. 한글 및 그 밖의 문자는 핸들러 실행 전에 거부된다.
- `wfreq`는 MHz 또는 채널 번호를 받는다. 예를 들어 채널 `36 40 44 48`은
  `5180 5200 5220 5240` MHz로 저장된다. 조회 결과가 `200 ANY`면
  주파수 제한이 없다. `2G`, `5G` 단축어는 지원하지 않는다.
- `wconnect`는 주파수를 인자로 받지 않는다. 주파수 변경은 `wfreq`로
  저장한 뒤 `wconnect`로 적용한다.

### 연결 결과

`wconnect`는 저장된 인증 정보로 연결을 시도한다. 단일 네트워크 프로필에서
저장 설정과 현재 런타임 설정이 일치하면 연결을 유지하고 성공을 반환한다.
연결 확인에 실패하면 `550 Failed: wconnect exited 8`을 반환할 수 있다.
이후 연결될 수도 있으므로 `quote wstatus`로 실제 상태를 확인한다.

`wconnectraw`는 시험용 명령이다. `200 SUCCESS`는 무선 제어 명령의 제출
성공만 뜻하며 AP 연결 성공을 보장하지 않는다. 설정 파일을 바꾸지 않고
연결 완료도 기다리지 않는다.

## 오류 코드

| 명령 | 코드 | 의미 |
| --- | ---: | --- |
| `wssid`, `wfreq` | `1` | 설정 조회, 입력 검증 또는 저장 실패 |
| `wconnect` | `1` | SSID 또는 설정 검증 실패 |
| `wconnect` | `3` | 대상 인터페이스가 현재 FTP 연결을 운반하여 요청 거부 |
| `wconnect` | `7` | 무선 제어 명령 또는 연결 이벤트 확인 실패 |
| `wconnect` | `8` | 제한 시간 내 목표 연결 확인 실패 |
| `wstatus` | `2` | 잘못된 인터페이스 인자 또는 인자 초과 |
| `wstatus` | `7` | 무선 상태 조회 실패 또는 필수 값 누락 |
| `wstatus` | `8` | 상태 조회는 가능하지만 현재 연결되지 않음 |
| `wconnectraw` | `1` | 상태 조회 또는 대상 네트워크 선택 실패 |
| `wconnectraw` | `2` | SSID 누락 |
| `wconnectraw` | `7` | 런타임 명령 실패 |

입력 제한으로 핸들러 실행 전에 거부된 요청이나 30초 초과 요청은 위 표의
종료 코드를 갖지 않을 수 있다. `wconnect`의 연결 확인 폴링 기본값은 15초지만
설정 처리 시간까지 포함한 전체 명령 시간의 상한은 아니다. 두 시간은
FTP 명령 인자로 변경할 수 없다.

## 인터페이스 및 재부팅 명령

| 명령 | 형식 | 성공 응답 | 주요 오류 코드 |
| --- | --- | --- | --- |
| `getifstate` | `quote getifstate [인터페이스]` | `200 <인터페이스> UP` 또는 `200 <인터페이스> DOWN` | `1`: 상태 읽기 실패, `2`: 인터페이스 오류 |
| `ifcup` | `quote ifcup [인터페이스]` | `200 IFCUP command successful.` | `1`: UP 실패, `2`: 인터페이스 오류 |
| `ifcdown` | `quote ifcdown [인터페이스]` | `200 IFCDOWN command successful.` | `1`: DOWN 실패, `2`: 인터페이스 오류, `3`: FTP 연결 보호 |
| `rst` | `quote rst` | `200 reboot requested` | `1`: 재부팅 요청 실패, `2`: 인자 오류 |

인터페이스 명령도 인자를 생략하면 `mlan0`을 사용한다. `getifstate`의
`UP`/`DOWN`은 인터페이스의 관리 상태로, AP 연결 여부와 다르다.
`ifcdown`은 현재 FTP 제어 연결을 운반하는 인터페이스를 내리는 요청을
거부한다. `rst` 성공 후에는 장비가 재부팅되므로 FTP 연결이 끊길 수 있다.
