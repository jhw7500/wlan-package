# SD9098 upstream driver 외부 `iw` off-channel scan A/B 시험 기록

> 공개 검토본: SSID/BSSID/IP/hostname과 개인 절대 경로는 일관된
> `*_REDACTED` 토큰 또는 repo-relative 경로로 치환했다. 원시 target artifact는
> 로컬 보존본이 정본이다. p149.115 + `antcfg 0x0101` 재현과 p149.81
> matched-pair 미재현의 범위를 넘어서 active `iw` 또는 `wpa_cli TYPE=ONLY`를
> 일반적으로 안전하다고 해석하지 않는다.

## 1. 문서 상태

- 작성일: 2026-08-24 (KST)
- 대상 보드: `TARGET_HOST_REDACTED` (`WLAN_HOST_REDACTED`, aarch64)
- 대상 인터페이스: `mlan0`
- 비교 목적: `/opt/wlan/driver/mlan_imx93.ko`와
  `/opt/wlan/driver/moal_imx93.ko`를 업스트림 빌드로 교체했을 때, 기존
  505.p14에서 확인한 반복 외부 off-channel scan 후 데이터 경로 손상이 같은
  조건으로 재현되는지 확인한다.
- 현재 상태: **동일 505.p14에서 p149.115 실패/p149.81 미재현의 F/W-only A/B
  성립 / 제품 문법·주기 soak 대기 / ABA는 선택적 재확인**
- 543.p18 실행 범위: T1 두 번 재현. T2는 stop condition에 따라 9회에서
  중단했으며 판정에 사용하지 않는다.
- p149.81 matched-pair 실행 범위: T1 독립 boot 2회, T2 passive 1회,
  T3 두 off-channel 1회 완료.
- 결과 기록 원칙: 실행 전 예상값을 결과로 쓰지 않는다. 실제 로드된 바이너리와
  실행 로그를 확인한 뒤 §8을 갱신한다.
- 이번 단계에서는 드라이버/펌웨어 수정이나 우회 설정을 적용하지 않는다.

## 2. 비교 대상 정의와 과거 버전 표기 정정

### A — 기존 재현 기준선

이전 외부 `iw` scan 재현 시험에서 **실제로 로드된 드라이버는 505.p14**였다.

| 항목 | 기준선 값 |
|---|---|
| `/sys/module/mlan/version` | `505.p14` |
| `/sys/module/mlan/srcversion` | `41469260FFF85611C4D6D71` |
| `/sys/module/moal/version` | `505.p14` |
| `/sys/module/moal/srcversion` | `F15BF1363ED3552C63F31BB` |
| `/opt/wlan/driver/mlan_imx93.ko` SHA-256 | `ef6a8bd5fcc158d918e18da75638fb794d48931a1056e78e20284a40c3240eab` |
| `/opt/wlan/driver/moal_imx93.ko` SHA-256 | `c71d25e3fc9058f6d83ca10ffd95eb311705bc64f94691abe746dc6206b1f998` |
| FW/driver 문자열 | `SD9098----17.92.1.p149.115-MM6X17505.p14-GPL-(FP92)` |
| 펌웨어 | `/lib/firmware/cts/sd9098_wlan_v1.bin` |
| 펌웨어 SHA-256 | `7c3ef6e12d3cfc9bd638d1571ccf6ddd2e96e0ed179ec70664ccb1df0ba29e57` |
| calibration SHA-256 | `aa36ccf19f8a2e2ec03f7efafc1a25377674c7f3bf3567b2685483c6989506f5` |
| 커널 | `6.6.3-lts-next-gccf0a99701a7-dirty` |

기존 Task 10 기록의 `437.p3` 표기는 `modinfo -n mlan/moal`이 가리킨
`/lib/modules/.../updates/{mlan,moal}.ko`를 실제 로드 모듈로 오인한 것이다.
`wifi_init`은 그 파일이 아니라 `/opt/wlan/driver/{mlan,moal}_imx93.ko`를 직접
`insmod`했다. 따라서 이 A/B에서 다음 값은 **드라이버 식별 증거로 쓰지 않는다**.

```text
modinfo -n mlan
modinfo -n moal
```

실제 로드 증거는 `/sys/module/*/{version,srcversion}`, `/proc/mwlan`의 전체
버전 문자열, 부팅 로그, 그리고 `/opt/wlan/driver/*.ko`의 SHA-256을 함께 쓴다.
이 문서의 505.p14 식별이 기존 `437.p3` 표기를 대체한다.

### B — 이번 업스트림 후보

사용자가 `/opt/wlan/driver/mlan_imx93.ko`와
`/opt/wlan/driver/moal_imx93.ko`를 업스트림 빌드로 교체했다. 파일을 교체한
사실만으로 로드를 인정하지 않는다. 재부팅 후 §5의 식별 게이트를 통과한 실제
값을 §8에 기록한다. 실제 시험 대상은 **543.p18**로 식별됐다.

## 3. 고정 환경

다음 항목이 기준선과 다르면 드라이버만의 A/B가 아니므로 시험을 중단한다.

| 항목 | 고정값/조건 |
|---|---|
| boot loader | `wifi_init`이 `/opt/wlan/driver/*_imx93.ko`를 로드 |
| 연결 유지 주체 | `wpa_supplicant@mlan0.service`만 유지 |
| SSID | `LAB_SSID_REDACTED` |
| BSSID | `LAB_BSSID_REDACTED` |
| home frequency | `5180 MHz` |
| IPv4 | `WLAN_STATION_IP_REDACTED` |
| gateway | `WLAN_GATEWAY_IP_REDACTED` |
| JSON SHA-256 | `be46944c89e45f0b07a6a672f6a4a6fd23e8ef993a7d88a93ed5caab9219c2c9` |
| supplicant conf SHA-256 | `e1f9248ab43b159e69e7ffd5a7143ab797caeff7f3aa4216bcf706dc6708d758` |
| firmware/calibration | §2의 기준선 파일과 SHA-256이 동일해야 함 |
| module parameter | 부팅 로그상 `scan_chan_gap = 20` |
| runtime `scancfg` | `Scan channel Gap: 0 ms` — runtime override 미설정 상태 |
| 캡처 | 기본 시험에서는 netmon/`wifi_capture` 비활성 |
| 시험 중 WLAN L3 트래픽 | 초기 health ping 후 scan loop 동안 없음; 마지막에만 판정 ping |

`scancfg`가 0이어도 연결 중 cfg80211 scan은 드라이버 handle 값인 20 ms를
사용한다. 이번 최초 비교에서는 50/100/500 ms runtime override를 적용하지
않는다.

## 4. 서비스 격리 기준

연결을 보존하기 위해 `wpa_supplicant@mlan0.service`는 계속 실행한다.
`wifi_init`은 부팅 시 드라이버를 로드하는 경로로 사용하며, scan 요청자는 아니다.
다음 서비스는 시험 전에 중지하고 시험 중 inactive임을 기록한다.

```text
wifi_roam@mlan0.service
wifi_bgscan@mlan0.service
wifi_capture@mlan0.service
wifi_logger_scan@mlan0.service
wifi_logger_link@mlan0.service
wifi_logger_stat@mlan0.service
wifi_link_snapshot@mlan0.service
wifi_checker@mlan0.service
wifi_event@mlan0.service
wifi_bridge@mlan0.service
wlan_fw_watch.service
```

추가로 `iw event`, `getscantable`, `iw scan dump`, 별도 ping/ARP generator가
실행 중이지 않은지 process snapshot으로 확인한다. supplicant journal에서 각
명령은 `own=0 ext=1`이어야 하며 `Own scan request started a scan`은 0건이어야
한다. 즉, scan 요청자는 시험 shell 하나뿐이어야 한다.

## 5. 업스트림 실행 전 식별 게이트

아래 항목을 하나의 preflight artifact에 저장한다.

1. 새 boot ID와 실제 재부팅 시각.
2. `/opt/wlan/driver/{mlan,moal}_imx93.ko` SHA-256, 크기, mtime.
3. `/sys/module/{mlan,moal}/{version,srcversion}`.
4. `/proc/mwlan/adapter0/mlan0/info`의 전체 driver/FW 문자열.
5. 부팅 journal의 `fw_name`, `Request firmware`, `WLAN FW is active`,
   `wlan: version`, `scan_chan_gap` 행.
6. 실제 요청된 firmware/calibration/config 파일의 SHA-256.
7. JSON과 supplicant conf SHA-256.
8. `wpa_state=COMPLETED`, SSID/BSSID/freq/IP, station counter와 초기 gateway
   ping 3/3.
9. 격리 대상 서비스가 모두 inactive이고 supplicant만 active인지.

다음 중 하나라도 발생하면 scan을 시작하지 않는다.

- `/opt`의 업스트림 파일과 실제 `/sys/module`/`/proc/mwlan` 식별이 맞지 않음
- 기존 505.p14 두 바이너리 중 하나라도 그대로임
- firmware, calibration, JSON, supplicant conf가 기준선과 달라짐
- 연결 AP/BSSID/home frequency가 기준선과 다름
- 초기 ping 실패 또는 시작 전 `tx failed`가 비정상 증가
- 다른 scan requester/listener가 남아 있음

## 6. 동일 기준 시험 행렬

각 행은 원칙적으로 새 boot에서 시작한다. 장애가 재현되면 정상 reassociate나
서비스 재시작으로 복구됐다고 간주하지 않고 증거 수집 후 재부팅한다.

| ID | scan/대기 조건 | 505.p14 기준선 | 업스트림 결과 |
|---|---|---|---|
| N0 | scan 없음, 300초 idle | PASS, ping 3/3, `tx failed 0 -> 0` | 미실행 |
| N1 | `iw mlan0 scan freq 5180 ssid LAB_SSID_REDACTED`, 10초, 30회 | PASS, 30/30, `tx failed 0 -> 0` | 미실행 |
| T1 | `iw mlan0 scan freq 5200 ssid LAB_SSID_REDACTED`, 5초, 최대 30회 | **FAIL**, 10회/57초에 onset | **FAIL 2/2**, 27회/146초와 5회/33초 |
| T2 | `iw mlan0 scan freq 5200 passive`, 5초, 최대 30회 | **FAIL**, 14회/79초에 onset | 9회/54초에서 정책 중단 — 판정 없음 |
| T3 | `iw mlan0 scan freq 5200 5220 ssid LAB_SSID_REDACTED`, 10초, 최대 30회 | **FAIL**, 11회/117초에 onset | 미실행 |

T1이 30회에서 재현되지 않으면 새 boot에서 같은 T1을 한 번 더 수행한다. 두 번
모두 통과해도 결론은 `이 조건에서 미재현`이며 곧바로 `완전 수정`으로 확대하지
않는다. N0/N1은 AP와 기본 연결 자체가 정상임을 확인하는 negative control이다.

### 참고용 `scan_chan_gap` 민감도 기준선

이 표는 업스트림 최초 비교에 설정 변경으로 넣지 않는다. T1~T3 결과가 기준선과
다를 때 후속 원인 분리에만 사용한다.

| 조건 | 505.p14 결과 |
|---|---|
| T3, gap 20 ms | FAIL, scan 11 |
| T3, runtime gap 50 ms | FAIL, scan 13 |
| T3, runtime gap 100 ms | PASS, 30회 |
| T1, runtime gap 100 ms | FAIL, scan 29/157초 |
| T1, runtime gap 500 ms | PASS, 30회 + 즉시 반복 30회 |

따라서 gap은 고장 발생량을 줄이는 강한 완화 변수지만, 50 ms에서도 재현되므로
505.p14의 단일 원인으로 판정하지 않는다. 500 ms 역시 제품 권장값이 아니라
진단값이다.

## 7. 판정 및 중단 조건

### `REPRODUCED`

다음 조건이 모두 성립하면 기존 결함 서명이 재현된 것이다.

1. 마지막 gateway ping 실패.
2. `tx failed` delta가 1,000 이상.
3. supplicant는 계속 `wpa_state=COMPLETED`.
4. SSID/BSSID/home frequency가 시작값과 동일.
5. 각 scan이 external scan으로 관찰되고, supplicant own scan은 0건.
6. disconnect/authentication error 없이 데이터 경로만 실패하거나, 그보다 앞선
   동일한 RF/TX 실패 증거가 존재.

### `NOT_REPRODUCED`

정해진 횟수를 모두 완료하고 마지막 ping이 성공하며, stale `COMPLETED` + TX
failure spike 서명이 없을 때만 해당 실행을 `NOT_REPRODUCED`로 기록한다. 한 번의
미재현은 드라이버 수정 증명이 아니다.

### 즉시 중단

- scan 명령 자체 실패/timeout
- association, BSSID, channel이 시험 외 사유로 변경
- AP가 사라지거나 기준선 RF 조건을 유지할 수 없음
- FW recovery, kernel warning/oops, module reload 발생
- 설정/firmware hash 변경

## 8. 업스트림 실제 실행 결과

### 8.1 실제 로드 식별

| 항목 | 실제 값 |
|---|---|
| 시험 boot ID | `3f3f824f-00e0-4090-841f-a8dadc7001db` |
| `mlan_imx93.ko` SHA-256 | `c351a0d63f75d53f99ca0b74aba3911d79e9aaf1f4e269dac68cba9b06e4c46a` |
| `moal_imx93.ko` SHA-256 | `87b9d0dc5b86c4a40560050f4e9c5a2c8662acc97bea6b516f0f094fcfc9b6a0` |
| `/sys/module/mlan/version` / srcversion | `543.p18` / `69CD10BAA7F3A642C954443` |
| `/sys/module/moal/version` / srcversion | `543.p18` / `E14FF2EA56EE8DA9F44DC18` |
| `/proc/mwlan` 전체 버전 문자열 | `SD9098----17.92.1.p149.115--MM6X17543.p18-GPL-(FP92)` |
| firmware/calibration SHA-256 일치 | 일치 (`7c3ef6e...29e57` / `aa36ccf1...06f5`) |
| JSON/supplicant conf SHA-256 일치 | 일치 (`be46944c...c2c9` / `e1f9248a...d758`) |
| effective/runtime scan gap | boot `20 ms` / runtime override `0 ms` |

첫 preflight에서는 `/opt` 파일의 SHA-256만 신규 값이고 실제 `/sys/module`은
505.p14였다. 이 상태에서는 scan을 시작하지 않았으며, 재부팅 후 543.p18의
version/srcversion과 `/proc/mwlan` 문자열을 확인한 다음 시험했다. 즉 결과는
교체 전 파일이나 `modinfo` 대상이 아니라 실제 로드된 543.p18의 결과다.

새 버전은 `/proc/mwlan`에 `firmware_major_version=-17.92.1, -`로 출력하고 전체
driver 문자열에도 `p149.115--MM...`처럼 하이픈이 하나 더 보인다. 펌웨어 파일
자체와 부팅 시 요청 경로는 기준선과 일치했으므로, 이 표기 차이는 별도 관찰로
남기되 이번 데이터 경로 판정에는 사용하지 않았다.

### 8.2 실행별 결과

| ID | boot ID | 실행 시각 | 횟수/경과 | `tx failed` 전→후 | 마지막 ping | 상태/BSSID | 판정 | artifact |
|---|---|---|---|---|---|---|---|---|
| N0 | - | - | - | - | - | - | 미실행 | - |
| N1 | - | - | - | - | - | - | 미실행 | - |
| T1-1 | `3f3f824f...1db` | board clock `2026-08-20 00:36` | 27회/146초 onset, 150초 종료 | `0 -> 3683` onset; ping 후 `6344` | 0/3, rc 1 | `COMPLETED`, `LAB_BSSID_REDACTED` 유지 | **REPRODUCED** | `/tmp/iw-upstream-driver-ab-20260824/T1-active-5200-interval5-run1` |
| T1-2 | `cdbda041...117c` | board clock `2026-08-20 00:46` | 5회/33초 onset, 38초 종료 | `0 -> 4200` onset; ping 후 `6769` | 0/3, rc 1 | `COMPLETED`, `LAB_BSSID_REDACTED` 유지 | **REPRODUCED** | `/tmp/iw-upstream-driver-ab-20260824/T1-active-5200-interval5-run2` |
| T2 | `051f183a...a310` | board clock `2026-08-20 00:48` | 9회/54초에서 중단 | `0 -> 0` | 미실행 | `COMPLETED`, 같은 BSSID | **ABORTED / 판정 없음** | `/tmp/iw-upstream-driver-ab-20260824/T2-passive-5200-interval5-run1` |
| T3 | - | - | - | - | - | - | 미실행 | - |

보드 RTC가 세션 날짜보다 뒤진 `2026-08-20`을 표시하므로 표에는 board clock임을
명시했다. 한 실행 내부의 순서는 boot ID, epoch, journal cursor와 elapsed time으로
결속했다.

T1의 세부 증거:

- exact command: `iw mlan0 scan freq 5200 ssid LAB_SSID_REDACTED`
- scan 간 대기: 5초, scan 자체 소요 85~93 ms
- scan 24/130초까지 `tx failed=0`
- scan 25/136초 `1`, scan 26/141초 `352`, scan 27/146초 `3683`
- 종료 직전/판정 ping 후 `tx failed=3706/6344`
- external scan/result 27/27, supplicant own scan 0, disconnect/auth error 0
- JSON과 supplicant conf의 전/후 SHA-256 동일
- `wifi_roam`, `wifi_bgscan`, `wifi_capture`, scan/link/stat logger,
  checker/event/bridge/fw-watch가 모두 inactive인 `wpa-only` 격리
- 장애 후 추가 진단 시에도 `COMPLETED`, 같은 BSSID/5180, carrier on, TX queue
  started, beacon received 2549/missed 0이었지만 `dot11FailedCount=27650`,
  `dot11ACKFailureCount=276674`, station `tx failed=27648`로 계속 증가
- FW recovery, kernel oops, deauth/disassoc는 관찰되지 않음

T1 독립 반복의 세부 증거:

- 같은 543.p18, firmware/config/AP/gap, `wpa-only` 격리와 명령을 사용
- scan 1~2 `tx failed=0`, scan 3 `3`, scan 4 `982`, scan 5 `4200`
- 종료 직전/판정 ping 후 `tx failed=4230/6769`
- external scan/result 5/5, supplicant own scan 0, disconnect/auth error 0
- 추가 진단 시 `dot11FailedCount=23544`, `dot11ACKFailureCount=235512`,
  beacon received 3681/missed 0, carrier on과 TX queues started
- JSON과 supplicant conf의 전/후 SHA-256 동일

T2는 사용자가 제시한 stop condition을 적용해 중단했다. 정확한 passive 명령을
9회 실행한 시점에는 `tx failed=0`이었지만 505.p14 기준선의 onset은 14회였으므로,
9회 결과는 PASS나 개선 증거가 아니다. harness의 TERM trap은 신호 처리 당시 마지막
명령 상태 때문에 `harness_exit=0`을 기록했으나 controller record가 명시적으로
`result=ABORTED_BY_CONTROLLER`, `verdict=NONE_INCOMPLETE`로 이를 대체한다.

### 8.3 최종 A/B 결론

**실제로 로드된 업스트림 543.p18에서도 기존 결함 서명이 재현됐다. 따라서 이
버전은 T1 조건의 문제를 제거하지 못했다.**

505.p14의 T1은 scan 10/57초였다. 543.p18은 첫 boot에서 scan 27/146초였지만,
독립 반복에서는 scan 5/33초로 더 빠르게 재현됐다. 따라서 첫 실행의 지연은
543.p18 개선 증거가 아니라 발생 시점 변동성이다. 관찰 범위가 5~27 scan으로
기준선 10 scan을 양쪽으로 포함하며, 같은 stale `COMPLETED` + ACK/TX failure
서명이 2/2 재현됐다. **543.p18에 문제 제거 또는 일관된 내성 개선은 없다.**

firmware, calibration, JSON, supplicant conf, AP/BSSID/home frequency,
`scan_chan_gap`, 격리 서비스와 scan grammar는 기준선과 동일했다. 바뀐 핵심 변수는
505.p14에서 543.p18로의 실제 로드 드라이버다. T2 passive는 불완전 중단됐고 T3는
실행하지 않았으므로 active/passive 간 상대 변화나 다채널 내성은 결론내리지
않는다. 현재 목적에는 T1 2/2 재현으로 충분하여 추가 543.p18 시험을 중단한다.

### 8.4 실패 후 복구 확인

각 T1 실패와 중단된 T2 뒤에 보드를 재부팅했다. 최종 복구 상태는 다음과 같다.

- final recovery boot ID: `c8143d38-c5cd-433a-884e-07ae5363932e`
- 543.p18 version/srcversion과 두 `/opt` SHA-256 유지
- firmware/calibration/JSON/supplicant conf SHA-256 기준선과 일치
- runtime scan gap override `0 ms`
- `wifi_capture` inactive, 별도 netmon interface 없음
- `mlan0`은 같은 SSID/BSSID/5180/IP에서 `COMPLETED`
- station `tx failed=0`, gateway ping 5/5
- 원래 서비스 상태 복원: supplicant/roam/logger/checker/event/bridge/fw-watch
  active, `wifi_bgscan`/capture inactive

복구 boot에서 station MAC이 시험 boot의 `00:e0:4c:68:2b:1f`에서
`00:50:43:02:fe:01`로 달라진 것이 관찰됐다. BSSID/SSID/frequency/IP와 ping은
정상이었고 T1 실패 판정은 그 전 boot 안에서 완료됐으므로 판정을 바꾸지는 않는다.
MAC 선택 변화의 원인은 이번 scan 재현 범위 밖의 별도 확인 항목으로 남긴다.

artifact manifests:

```text
/tmp/iw-upstream-driver-ab-20260824/T1-active-5200-interval5-run1/SHA256SUMS.final
SHA-256 f6349bb0d212d558be5ba32b313d8237b11c8c41a86472eee4316d2557105bfa

/tmp/iw-upstream-driver-ab-20260824/T1-active-5200-interval5-run2/SHA256SUMS.final
SHA-256 d2a1529dd0c97286003dbbbc0337f255de81de92f4b7c0d561aa643825a058a3

/tmp/iw-upstream-driver-ab-20260824/T2-passive-5200-interval5-run1/SHA256SUMS.final
SHA-256 c743873121931f17f708de655af8f0c0395e96e5ce13ecaefc40c81d87e3abdd
```

## 9. 기존 505.p14 증거 요약

원본 artifact root:

```text
/tmp/iw-service-bisect-20260824
```

주요 결과:

- scan 없는 5분 idle: 정상, `tx failed 0`, ping 정상.
- home channel 5180 active 30회: 정상.
- 단일 off-channel 5200 active/5초: scan 10, 57초에 `tx failed=2694`, 최종
  ping 실패, supplicant/BSSID 유지.
- 단일 off-channel 5200 passive/5초: scan 14, 79초에
  `tx failed=3347`, 최종 ping 실패, supplicant/BSSID 유지.
- 두 off-channel 5200+5220/10초: scan 11, 117초에 실패.
- 연속 ping을 보내도 T1은 scan 9/52초에 실패하여 keepalive가 보호하지 못함.
- netmon은 원인을 제거하지 않고 발생을 지연함: 단일 5200/5초가 netmon 없이
  scan 9~10, netmon 사용 시 scan 53 부근에서 실패.

RF capture artifact:

```text
/tmp/iw-service-bisect-20260824/iw-rf-capture-noping-gap20/rtap-all.pcap
SHA-256 736296e425c18d0858d31b23722668a1c86b2a5081706e3c1087701518222859
```

관찰된 경계는 다음과 같다.

- client에 대한 마지막 AP ACK: frame 3025,
  `1787152305.082088`, 5180 MHz, -51 dBm.
- 그 이후 AP ACK 0건.
- 같은 시각 이후 AP beacon 165건; 마지막 beacon은 frame 3205,
  `1787152325.595489`, 5180 MHz, -52 dBm.
- 최종 ping 시 client TX data 4건은 보이지만 AP ACK가 없음.
- deauth/disassoc 없음; supplicant는 같은 BSSID에서 `COMPLETED` 유지.

이는 AP beacon/RX와 논리적 association은 살아 있지만 uplink TX가 AP ACK를 받지
못하는 서명이다. 같은-radio netmon metadata는 독립 RF 계측기가 아니므로 정확한
물리 carrier/register 상태까지 증명하지는 않는다.

## 10. 증거 보존과 복구

- 업스트림 artifact root 예정:
  `/tmp/iw-upstream-driver-ab-20260824`.
- 각 실행은 preflight, service state, exact command, progress, initial/final
  status, station counters, supplicant/kernel journal, binary/config hashes,
  `result.txt`, `SHA256SUMS`를 남긴다.
- `SHA256SUMS` 생성 후 변하는 `harness.log`는 manifest 검증에서 제외하거나,
  harness 종료 뒤 최종 manifest를 다시 생성한다.
- 장애 재현 직후 추가 scan/reassociate로 상태를 덮지 않는다. 먼저 journal,
  `/proc/mwlan`, station/driver counters를 수집한다.
- 각 destructive run 뒤 재부팅하고 다음을 확인한다: 실제 로드 드라이버 유지,
  runtime gap override 0, 원본 JSON/conf hash 일치, 요구 서비스 복구,
  `wifi_capture`/netmon 비활성, association 및 gateway ping 정상.
- 기존 505.p14 두 바이너리 백업은 아래 SHA-256으로 식별하며 업스트림 결과 수집이
  끝날 때까지 삭제하지 않는다.

```text
mlan_imx93.ko ef6a8bd5fcc158d918e18da75638fb794d48931a1056e78e20284a40c3240eab
moal_imx93.ko c71d25e3fc9058f6d83ca10ffd95eb311705bc64f94691abe746dc6206b1f998
```

## 11. 이전 릴리즈 F/W + driver matched-pair 결과

### 11.1 실제 로드 조합

두 독립 boot 모두 다음 조합을 확인한 뒤 T1을 실행했다.

| 항목 | 실제 값 |
|---|---|
| driver | `505.p14` |
| mlan SHA-256 | `ef6a8bd5fcc158d918e18da75638fb794d48931a1056e78e20284a40c3240eab` |
| moal SHA-256 | `c71d25e3fc9058f6d83ca10ffd95eb311705bc64f94691abe746dc6206b1f998` |
| F/W | `17.92.1.p149.81` |
| F/W SHA-256 | `4716066f0325d0f7d21fbb45f037c1f9ca642011f206ccdfd6fa2371d6c81077` |
| 전체 문자열 | `SD9098----17.92.1.p149.81-MM6X17505.p14-GPL-(FP92)` |
| calibration SHA-256 | `aa36ccf19f8a2e2ec03f7efafc1a25377674c7f3bf3567b2685483c6989506f5` |
| module/runtime gap | boot `20 ms` / runtime override `0 ms` |

JSON, supplicant conf, AP `LAB_BSSID_REDACTED`, home 5180 MHz, gateway와
`wpa-only` 격리는 앞선 T1과 동일했다.

### 11.2 T1 독립 boot 결과

정확한 명령은 두 번 모두 다음과 같다.

```text
iw mlan0 scan freq 5200 ssid LAB_SSID_REDACTED
```

| 실행 | boot ID | scan/경과 | `tx failed` | 마지막 ping | external/own | 판정 |
|---|---|---|---|---|---|---|
| run 1 | `a93c0ff7-9c79-4ca7-a13d-d9288a9d135f` | 30회/163초 | `0 -> 1` | rc 0 | 30/0 | `NOT_REPRODUCED` |
| run 2 | `5d3238a7-c3ff-4f3e-9a46-8b36b4350e00` | 30회/163초 | `0 -> 0` | rc 0 | 30/0 | `NOT_REPRODUCED` |

두 실행 모두 같은 SSID/BSSID/5180에서 `COMPLETED`를 유지했고,
disconnect/auth error는 0, JSON과 supplicant conf의 전/후 SHA-256은
동일했다. 합계 60회의 5초 간격 단일 off-channel active scan에서 기존
stale-`COMPLETED` + TX/ACK failure 서명은 재현되지 않았다.

artifact manifests:

```text
/tmp/iw-previous-release-matched-20260824/T1-active-5200-interval5-run1/SHA256SUMS.final
SHA-256 6933c71d6c6f9c21a64826847594888dbd4e88fe98f002f3f25508c6cc638aa6

/tmp/iw-previous-release-matched-20260824/T1-active-5200-interval5-run2/SHA256SUMS.final
SHA-256 39bc859684cd1954fa1c77a6386943d7d80a601e250ed9021fff6cdea61ae45a
```

### 11.3 T2 passive와 T3 다채널 결과

| 시험 | exact scan grammar | boot ID | scan/경과 | `tx failed` | 마지막 ping | 판정 |
|---|---|---|---|---|---|---|
| T2 | `iw mlan0 scan freq 5200 passive` | `816014c3-ea20-4f4f-bfea-88c32f82df95` | 30회/166초 | `0 -> 0` | rc 0 | `NOT_REPRODUCED` |
| T3 | `iw mlan0 scan freq 5200 5220 ssid LAB_SSID_REDACTED` | `61694f62-a7d4-4c96-8bd2-b378304e8d50` | 30회/315초 | `0 -> 0` | rc 0 | `NOT_REPRODUCED` |

T2의 p149.115 기준선은 scan 14/79초, T3 기준선은 scan 11/117초에 실패했다.
p149.81에서는 두 시험 모두 그 경계를 지나 30회를 완료했다. external scan/result는
각각 30/30, supplicant own scan과 disconnect/auth error는 0이었다. 같은
SSID/BSSID/home frequency를 유지했고 JSON과 supplicant conf도 전/후 byte-identical했다.

artifact manifests:

```text
/tmp/iw-previous-release-matched-20260824/T2-passive-5200-interval5-run1/SHA256SUMS.final
SHA-256 e80f4d8a47483f069690945042cf5a9c19dbe064612b0bc87456d007bea0e49b

/tmp/iw-previous-release-matched-20260824/T3-active-5200-5220-interval10-run1/SHA256SUMS.final
SHA-256 6811cad348aebead9d61210f259530e20fb0b581d96bc60a0dda0ad73b9979eb
```

### 11.4 현재 해석

505.p14 driver 바이너리는 p149.115 실패 기준선과 byte-identical하다. 설정/AP/gap도
같고 의도적으로 바뀐 핵심 변수는 F/W `p149.115 -> p149.81`이다. p149.115에서는
같은 T1이 505.p14에서 scan 10/57초, 543.p18에서는 독립 boot 2/2로 scan
27/146초와 5/33초에 실패했다. 반면 p149.81+505.p14는 T1 독립 boot 2/2,
T2 passive와 T3 다채널까지 모든 실행에서 미재현됐다.

따라서 현재 증거는 **p149.115 F/W 또는 그 F/W와 host driver/AP의 상호작용이
주요 차이**임을 강하게 지지한다. 그러나 아직 다음 제한이 있다.

초기 p149.115 시험과 현재 p149.81 시험의 mlan/moal SHA-256이 각각 완전히
동일하므로, 이것은 이미 505.p14 driver를 고정한 **F/W-only A/B**다. 아래 ABA는
최초 A를 같은 시점에 다시 재현해 시간/RF 환경 변수를 더 줄이는 선택적 강화
시험이지, 누락된 최초 p149.115 조합을 보충하기 위한 필수 시험이 아니다.

- T2/T3는 각각 한 boot만 검증했다. 다만 p149.115의 과거 onset 경계를 30회까지
  충분히 초과했다.
- 이 시점에는 실제 제품 4채널/60초 cadence와 장시간 idle postcondition이
  미검증이었으나, 후속 §12의 30회 product-path soak에서 통과했다.
- 505.p14를 고정하고 F/W만 `p149.81 -> p149.115 -> p149.81`로 바꾸는 같은
  시점의 ABA 시험은 아직 하지 않았다.
- 최초 505.p14+p149.115 기준선과 현재 시험의 station MAC이 달랐지만,
  `00:50:43:02:fe:01`을 쓴 543.p18+p149.115 run도 scan 5에 실패했으므로 MAC만으로
  차이를 설명하기는 어렵다.

따라서 현 단계의 표현은 **“p149.81 matched pair가 기존 실패 경계를 T1/T2/T3에서
모두 통과했다”**이지, “p149.81에서 완전 해결”은 아니다.

### 11.5 다음 단계와 stop condition

1. **제품 문법/주기 soak**: 현재 p149.81 조합에서 실제 4채널 grammar와 60초
   cadence를 observation traffic 없이 수행하고 마지막에만 데이터 경로를
   판정한다.
2. **선택적 F/W-only ABA**: 더 강한 회귀 귀속이 필요할 때만 505.p14 driver 두
   SHA-256을 고정하고 F/W를 p149.81(A) -> p149.115(B) -> p149.81(A')로 바꿔
   T1을 반복한다. B에서 실패하고 A'에서 다시 통과하면 시간/RF 환경 가능성까지
   더 강하게 배제한다.
3. 모든 단계에서 stale `COMPLETED` + TX/ACK failure가 한 번이라도 나오면 즉시
   중단하고 장애 상태를 보존한다.

현재 보드는 p149.81+505.p14로 복구됐고 원래 서비스 상태, `tx failed=0`, 같은
association과 gateway ping 5/5을 확인했다. final recovery boot ID는
`a6d01fc4-565b-45a9-a24b-641462b5933c`이다.

## 12. 제품 4채널/60초 soak와 실제 모듈 로드 설정

### 12.1 시험 경로

2026-08-24 host 시각(보드 시계 2026-08-20)에 다음 제품 경로 시험을 시작했다.
수동 shell이 `iw`를 호출하는 T1~T3와 달리, 설치된
`wifi_bgscan@mlan0.service`가 실제 JSON, supplicant conf, boot policy snapshot을
읽어 요청한다.

```text
owner/backend: wifi_roam / iw
JSON interval: 60
global/common freq_list: 5180 5200 5220 5240
actual command:
  iw mlan0 scan freq 5180 5200 5220 5240 ssid LAB_SSID_REDACTED
target count: 30
```

`wifi_bgscan`이 시작 전에 inactive였던 직접 원인은 `bgscan.enabled=false`가 아니다.
JSON, boot snapshot, systemd enable 상태는 모두 true/enabled였지만, 앞선 requester
격리시험에서 설치한 다음 test-only drop-in의 condition 파일이 없었다.

```ini
[Unit]
ConditionPathExists=/run/task10-bgscan-control-allow
```

시험은 이 파일만 임시 생성해 실제 서비스를 시작하며 JSON, supplicant conf와 unit
본문은 편집하지 않는다. 관리 SSH는 `MGMT_HOST_IP_REDACTED dev eth0`인 유선 경로다. WLAN
L3 observation traffic은 시작/종료 gateway ping에만 사용하고, 중간에는 local
supplicant 상태와 driver counter만 읽는다. 완료 또는 이상 감지 후 bgscan을 정지하고
condition 파일을 제거해 직전 inactive 상태로 복구한다.

> **drop-in 자체도 반드시 제거해야 한다.** condition 파일만 지우고 drop-in을 남기면,
> gate는 `/etc`(영속)에 있고 그것을 만족시키는 파일은 `/run`(tmpfs)에 있으므로 **다음
> 부팅부터 `wifi_bgscan`이 영구히 fail-closed** 된다. systemd는 이를 실패가 아닌
> condition skip으로 기록해 `systemctl --failed`에도, 저널 오류 검색에도 걸리지 않는다.
> 게다가 `wifi_logger_scan`은 스캔을 유발하지 않는 순수 소비자라 서비스는 `active
> (running)`인 채 `ap.log`/`freq.log`만 조용히 비고, 원인이 로그로테이션으로 오귀속되기
> 쉽다. (2026-09-01 실제 발생 — 2026-08-19 설치분이 배포 검증 재부팅 때 드러남.)
>
> `scripts/qa/product_wifi_bgscan_soak.sh`는 이제 cleanup에서 drop-in을 아티팩트로
> 백업한 뒤 제거하고 `daemon-reload`까지 수행하며, 결과를 `harness-exit.txt`의
> `dropin_removal_rc` / `dropin_present_after`에 남긴다. 수동으로 정리할 때는:
>
> ```bash
> rm -f /etc/systemd/system/wifi_bgscan@mlan0.service.d/task10-bgscan-off-control.conf
> rmdir /etc/systemd/system/wifi_bgscan@mlan0.service.d 2>/dev/null || true
> systemctl daemon-reload && systemctl start wifi_bgscan@mlan0.service
> ```

artifact:

```text
/tmp/iw-product-soak-20260824/product-4ch-interval60-run1
```

### 12.2 실제 로드 모듈과 직접 `insmod` 인자

`modinfo -n moal`의 설치 검색 결과가 아니라, `wifi_init.sh`가 직접 선택한
`/opt/wlan/driver/*_imx93.ko`를 기준으로 기록한다. boot journal에 남은
`moal engine: bridge params added` 원문은 다음과 같다.

```text
insmod /opt/wlan/driver/mlan_imx93.ko
insmod /opt/wlan/driver/moal_imx93.ko \
  mod_para=cts/wifi_mod_para.conf \
  tx_work=0 \
  bridge_mode=1 \
  bridge_debug=0 \
  bridge_wlan_idx=0 \
  bridge_keepalive_ms=1 \
  bridge_keepalive_idle_ms=20 \
  bridge_local_hairpin=0 \
  wq_sched_policy=1 \
  wq_sched_prio=45
```

이 인자열은 추정 문자열이 아니라 해당 boot에서 `wifi_init.sh`가 최종 조립해 journal에
출력한 값이다. `bridge_peer`와 `bridge_consume_link_local`은 JSON 값이 빈 문자열이라
전달되지 않았다. `tx_work`, `bridge_keepalive_idle_ms`, `bridge_local_hairpin`,
`wq_sched_*`는 로드 대상 `.ko`의 `parmtype=` capability gate를 통과했기 때문에
포함됐다.

| 파일 | version/srcversion | SHA-256 |
|---|---|---|
| `mlan_imx93.ko` | `505.p14` / `41469260FFF85611C4D6D71` | `ef6a8bd5fcc158d918e18da75638fb794d48931a1056e78e20284a40c3240eab` |
| `moal_imx93.ko` | `505.p14` / `F15BF1363ED3552C63F31BB` | `c71d25e3fc9058f6d83ca10ffd95eb311705bc64f94691abe746dc6206b1f998` |
| `sd9098_wlan_v1.bin` | `17.92.1.p149.81` | `4716066f0325d0f7d21fbb45f037c1f9ca642011f206ccdfd6fa2371d6c81077` |

### 12.3 실제 `mod_para` 파일과 SD9098 블록

직접 인자 `mod_para=cts/wifi_mod_para.conf`는 firmware search root 기준이므로 실제
파일은 `/lib/firmware/cts/wifi_mod_para.conf`다. 이 boot의 SHA-256은
`9586de01f4775113595a80e625820132308a92f42cfa1235907e50c6792abe0c`다.
`BUS_TYPE=sdio`이므로 `SD9098_0`은 mlan0, `SD9098_1`은 mlan1에 적용된다.

```text
SD9098_0 = {
    cfg80211_wext=0xf
    max_vir_bss=1
    drv_mode=1
    ps_mode=2
    auto_ds=2
    host_mlme=1
    sta_name=mlan
    napi=1
    ext_scan=2
    sched_scan=1
    scan_chan_gap=20
    keep_previous_scan=1
    fw_name=cts/sd9098_wlan_v1.bin
    net_rx=7
    mgmt_hex_dump=0
    cal_data_cfg=cts/WlanCalData_ext_RD.conf
}
SD9098_1 = {
    cfg80211_wext=0xf
    max_vir_bss=1
    drv_mode=1
    ps_mode=2
    auto_ds=2
    host_mlme=1
    sta_name=mlan
    napi=1
    ext_scan=2
    sched_scan=1
    scan_chan_gap=20
    keep_previous_scan=1
    fw_name=cts/sd9098_wlan_v1.bin
    net_rx=0
    mgmt_hex_dump=0
    cal_data_cfg=cts/WlanCalData_ext_RD.conf
}
```

후반 네 항목은 `wifi_init.sh`가 로드 직전에 JSON을 반영한 결과다.

- mlan0: `net_rx=7`, `mgmt_hex_dump_enable=false`, `STANDARD=ax`
- mlan1: `net_rx=0`, `mgmt_hex_dump_enable=false`, `STANDARD=ac`
- 양쪽 `CAL_DATA_CFG`가 비어 있어 global
  `cts/WlanCalData_ext_RD.conf`를 상속
- mlan0의 ax와 mlan1의 ac는 각 인터페이스 native maximum이므로
  `dev_cap_mask` 제한 행을 제거
- `bridge_mode`는 블록에서 제거하고 위 직접 `insmod` 인자 하나로 전달

주의할 점은 `/sys/module/moal/parameters/scan_chan_gap=0`, `ext_scan=0`,
`drv_mode=7`, `ps_mode=0`처럼 보이는 값이 SD9098 card block의 미적용을 뜻하지
않는다는 것이다. 이것들은 moal의 전역 module-param 저장값/기본값이고,
`mod_para` parser가 adapter별 구조체에 적용한 값은 이 sysfs 노드로 역반영되지 않는다.
같은 boot dmesg가 두 adapter에 대해 `card_type: SD9098, config block: 0/1`과
`scan_chan_gap = 20`을 각각 출력했고 최종 F/W 문자열도
`SD9098----17.92.1.p149.81-MM6X17505.p14-GPL-(FP92)`로 확인됐다.

반대로 직접 module parameter 중 sysfs로 export된 값은 `tx_work=0`,
`bridge_iface=mlan0`, `bridge_debug=0`, `bridge_keepalive_ms=1`,
`bridge_keepalive_idle_ms=20`, `bridge_local_hairpin=0`으로 journal 인자와 일치했다.
load-only 또는 permission 0인 `bridge_mode`, `bridge_wlan_idx`, `wq_sched_*`,
`mod_para`는 sysfs 파일이 없으므로 boot journal 원문을 적용 증거로 사용한다.

### 12.4 soak 결과

동일 boot `a6d01fc4-565b-45a9-a24b-641462b5933c`에서 목표 30회가 완료됐다.

| 항목 | 결과 |
|---|---|
| 총 경과 | 1,836초(30분 36초, preflight/마지막 판정 포함) |
| stop reason | `max_scans` |
| exact product command | 30/30 |
| unexpected grammar | 0 |
| wpa external start/result | 30/30 |
| wpa own scan | 0 |
| disconnect/auth error | 0 |
| `tx failed` | baseline 2 → pre-final-ping 2 → post-ping 2 |
| association | `COMPLETED`, `LAB_BSSID_REDACTED`, `LAB_SSID_REDACTED`, 5180 MHz 유지 |
| 마지막 gateway ping | 5/5, loss 0%, avg 1.926 ms |
| 판정 | `NOT_REPRODUCED` |

실제 command start 29개 간격은 다음과 같았다.

```text
minimum: 60.578 s
maximum: 60.882 s
average: 60.665 s
```

따라서 `bgscan.interval=60`은 wall-clock 기준으로 정확히 매 60.000초에 fire하는
고정 rate가 아니다. 직전 scan 처리 완료 후 timer를 재설정하고 1초 main-loop polling과
명령 실행 overhead가 더해지는 **최소 대기 간격**에 가깝다. 이번 보드에서는 실제
start-to-start가 약 60.7초였다.

설정 후조건도 byte hash로 확인했다.

| 파일 | before/after SHA-256 |
|---|---|
| `wifi_init_conf.json` | `be46944c89e45f0b07a6a672f6a4a6fd23e8ef993a7d88a93ed5caab9219c2c9` |
| `wpa_supplicant-mlan0.conf` | `e1f9248ab43b159e69e7ffd5a7143ab797caeff7f3aa4216bcf706dc6708d758` |
| `wifi_mod_para.conf` | `9586de01f4775113595a80e625820132308a92f42cfa1235907e50c6792abe0c` |

시험 후 condition allow 파일은 제거됐고 서비스는 직전 상태로 복구됐다.
`wifi_init`, `wpa_supplicant@mlan0`, `wifi_roam@mlan0`은 active,
`wifi_bgscan@mlan0`, `wifi_capture@mlan0`은 inactive다. association은 계속
`COMPLETED`, BSSID/freq/IP 동일, `tx failed=2`였다.

### 12.5 scan runtime 조회값

시험 종료 뒤 실제 보드의 `/usr/local/bin/mlanutl` symlink가 가리키는
`/opt/wlan/bin/mlanutl_imx93`으로 `scancfg`를 조회했다. mlanutl SHA-256은
`7a703d7e4454d8ec61bbbfbf9ee8e0eef66b4560298d56b050d8d2799eea936f`다.

| 값 | mlan0 | mlan1 |
|---|---:|---:|
| Scan Type | 1 (Active) | 1 (Active) |
| Scan Mode | 3 (Any) | 3 (Any) |
| Scan Probes | 4 | 4 |
| Specific Scan Time | 40 ms | 80 ms |
| Active Scan Time | 110 ms | 80 ms |
| Passive Scan Time | 110 ms | 80 ms |
| Passive to Active | 1 (Enable) | 1 (Enable) |
| Extended Scan Support | 3 (Enhanced) | 3 (Enhanced) |
| Scan channel Gap | 0 ms | 0 ms |

여기서 `mlanutl scancfg`의 gap 0은 runtime scan-config 조회값이고, §12.3의
`mod_para` card block `scan_chan_gap=20`과 서로 다른 설정 계층이다. 기록상 둘을
하나의 값처럼 덮어쓰지 않는다. 이 boot에서 card block 적용 증거는 dmesg의 adapter별
`scan_chan_gap = 20`, runtime command 응답은 위 `0 ms`로 각각 보존했다.

### 12.6 판정과 artifact 무결성

p149.81+505.p14는 가속 T1/T2/T3뿐 아니라 실제 제품 서비스의 4채널 directed
active/60초 경로에서도 30회 동안 기존 stale `COMPLETED` + uplink TX/ACK wedge를
재현하지 않았다. 이로써 §11의 “제품 cadence 미검증” 제한은 30분 gate 범위에서
해소됐다. 다만 이는 1시간/수시간/일 단위 신뢰성 보증은 아니며, F/W-only ABA는
회귀 귀속을 더 강화하고 싶을 때의 선택 시험으로 남는다.

최종 manifest는 28개 파일을 포함하며 `sha256sum -c` 전부 OK였다.

```text
/tmp/iw-product-soak-20260824/product-4ch-interval60-run1/SHA256SUMS.final
SHA-256 783638d1da777050f237ca66cb720730a880cb16327e786828ff088c17e11467
```

## 13. p149.115에서 iw와 wpa_cli TYPE=ONLY requester 비교

### 13.1 목적과 고정 변수

p149.81 product soak 통과 뒤 active F/W를 p149.115로 다시 바꾸고, 같은 RF scan을
shell `iw`와 supplicant own request로 각각 실행해 requester 변경이 TX/ACK wedge를
피하는지 확인했다. 최초 교체 시 p149.81 파일을 잘못 넣은 사실은 preflight에서
발견해 시험을 시작하지 않았고, 다음 값이 확인된 뒤에만 실행했다.

```text
driver: 505.p14
mlan SHA-256: ef6a8bd5fcc158d918e18da75638fb794d48931a1056e78e20284a40c3240eab
moal SHA-256: c71d25e3fc9058f6d83ca10ffd95eb311705bc64f94691abe746dc6206b1f998
F/W: 17.92.1.p149.115
F/W SHA-256: 7c3ef6e12d3cfc9bd638d1571ccf6ddd2e96e0ed179ec70664ccb1df0ba29e57
SSID/BSSID/home: LAB_SSID_REDACTED / LAB_BSSID_REDACTED / 5180 MHz
isolation: wpa-only
observation traffic: initial/final gateway ping only
```

`wifi_roam`, `wifi_bgscan`, capture, scan/link/stat logger, snapshot, checker,
event, bridge와 fw_watch를 정지하고 `wpa_supplicant@mlan0`만 유지했다. 각 requester
시험은 독립 boot에서 수행했고 장애가 나면 artifact 확정 전에는 복구 조작을 하지
않았다.

### 13.2 iw 재현

먼저 과거 T1과 byte-identical한 단일 off-channel reproducer를 실행했다.

```text
iw mlan0 scan freq 5200 ssid LAB_SSID_REDACTED
5초 간격, 최대 30회
reproducer SHA-256: 3147916052655f279bb1d3632bfaea3825d02b5f5d80372f6773a72a181a9b6c
```

이 boot에서는 30회, 164초 동안 `tx failed=0`, 마지막 ping 정상으로
`NOT_REPRODUCED`였다. 과거 onset 변동과 사용자 요청의 악조건을 고려해 이 한 번을
정상 판정으로 쓰지 않고 fresh boot에서 4채널/5초로 강화했다.

```text
iw mlan0 scan freq 5180 5200 5220 5240 ssid LAB_SSID_REDACTED
5초 간격, 최대 60회
```

강화시험은 scan 11, 66초에 동일 장애를 재현했다.

| 항목 | 값 |
|---|---|
| stop reason | `tx_failed_spike` |
| `tx failed` | 0 → scan 11에서 1,013 → final 3,853 |
| final ping | 실패, loss 100% |
| supplicant | `COMPLETED` 유지 |
| BSSID | `LAB_BSSID_REDACTED` 유지 |
| external start/result | 11/11 |
| own scan | 0 |
| disconnect/auth error | 0 |

artifact와 manifest:

```text
/tmp/iw-p149115-requester-compare-20260824/505p14/iw-active-5200-interval5-run1
  78 files, manifest SHA-256
  0dcd2a4a70ab9c57e97315d02bec4ef254abd46d34927c0a7ea280c262f28147

/tmp/iw-p149115-requester-compare-20260824/505p14/iw-active-4ch-interval5-run1
  40 files, manifest SHA-256
  b03737ef6ac7a75f4330a6d4a5631e3e6adbb69b05409e0a214656aec4f1a6a1
```

### 13.3 wpa_cli TYPE=ONLY 재현

iw 장애 artifact를 확정하고 재부팅한 다음 동일 4채널 directed active 요청을
supplicant own scan으로 바꿨다.

```text
wpa_cli -i mlan0 scan TYPE=ONLY \
  freq=5180,5200,5220,5240 \
  ssid 6a68775f776c616e5f
```

`TYPE=ONLY`는 반드시 SCAN parameter 문자열의 첫 토큰이어야 한다. upstream
`wpa_supplicant/ctrl_iface.c`의 `wpas_ctrl_scan()`은 시작 9바이트가
`TYPE=ONLY`일 때만 `scan_only=1`로 만들고 `scan_only_handler`를 설치한다. SSID는
ctrl_iface 규약에 맞춰 `LAB_SSID_REDACTED`의 UTF-8 hex를 전달했다.

모든 요청 reply는 `OK`였고 journal에서 own scan과 `Scan-only results received`가
각각 12회였다. external scan, network selection/association 시도와 disconnect/auth
error는 모두 0이므로 TYPE=ONLY가 의도대로 wpa native selection을 억제한 상태다.
그럼에도 scan 12, 73초에 iw와 같은 장애가 재현됐다.

| 항목 | 값 |
|---|---|
| stop reason | `tx_failed_spike` |
| `tx failed` | 0 → scan 12에서 3,220 → final 5,989 |
| final ping | 실패, loss 100% |
| supplicant | `COMPLETED` 유지 |
| BSSID | `LAB_BSSID_REDACTED` 유지 |
| own scan / scan-only result | 12/12 |
| external scan | 0 |
| network selection | 0 |
| disconnect/auth error | 0 |

artifact와 manifest:

```text
/tmp/iw-p149115-requester-compare-20260824/505p14/wpa-cli-type-only-4ch-interval5-run1
41 files, manifest SHA-256
5212c6bdcba2e9016d46554e9e646c6e67be6a3611e492bc864765828ae9bb54
```

위 세 artifact는 각 시험 종료 시 `SHA256SUMS.final` 전 항목이 OK임을 확인하고
manifest 자체 SHA-256을 기록했다. 다만 저장 위치가 보드의 비영속 `/tmp`였으므로
마지막 복구 재부팅 뒤 원시 파일은 남아 있지 않다. 위 hash는 시험 당시 무결성
기록이며, 원시 로그 재분석이 필요하면 시험을 다시 실행해 영속 저장소로 복사해야 한다.

### 13.4 결론과 stop condition

이 결과로 **wpa_cli TYPE=ONLY는 p149.115의 scan-return TX/ACK wedge에 대한
우회책이 아니다.** iw external request와 supplicant own scan-only request 모두
같은 driver/F/W RF scan 경로에서 장애를 만들었다. TYPE=ONLY 시험에서 selection이
0회였으므로 이 장애는 wpa native roaming과 `wifi_roam.py`의 정책 충돌로 설명되지
않는다.

단일 5200 MHz T1이 한 boot에서 통과한 것은 p149.115가 정상이라는 증거가 아니라
trigger의 확률성과 scan workload 민감도를 다시 보여준다. 4채널/5초에서는 iw와
wpa_cli가 각각 scan 11/12에 인접하게 실패했다. 두 실패 boot의 station MAC도
각각 `00:e0:4c:68:2b:1f`, `00:50:43:02:fe:01`로 달랐으므로 특정 station MAC만으로
설명할 수 없다.

사용자가 정한 다음 단계는 “wpa_cli가 문제없으면 driver까지 변경”이었다. wpa_cli도
동일 장애를 재현해 그 조건이 성립하지 않았으므로 이번 실행에서는 543.p18 driver로
교체하지 않았다. 장애 artifact 확정 후 재부팅했으며 최종 보드는 505.p14+p149.115,
`COMPLETED`, `tx failed=0`, gateway ping 3/3, 원래 서비스 상태로 복구됐다.

## 14. 543.p18+p149.115에서 다채널 requester 재검증

### 14.1 목적과 실제 로드 식별

사용자가 직접 driver를 업데이트하고 재부팅한 뒤, §13과 같은 4채널 directed active
scan을 `iw` external request와 `wpa_cli TYPE=ONLY` own request로 각각 독립 boot에서
반복했다. `/opt` 파일 교체만으로 판단하지 않고 다음 실제 로드값을 확인한 뒤 시험했다.

```text
driver: 543.p18
mlan srcversion: 69CD10BAA7F3A642C954443
moal srcversion: E14FF2EA56EE8DA9F44DC18
mlan SHA-256: c351a0d63f75d53f99ca0b74aba3911d79e9aaf1f4e269dac68cba9b06e4c46a
moal SHA-256: 87b9d0dc5b86c4a40560050f4e9c5a2c8662acc97bea6b516f0f094fcfc9b6a0
F/W: 17.92.1.p149.115
F/W SHA-256: 7c3ef6e12d3cfc9bd638d1571ccf6ddd2e96e0ed179ec70664ccb1df0ba29e57
JSON SHA-256: be46944c89e45f0b07a6a672f6a4a6fd23e8ef993a7d88a93ed5caab9219c2c9
wpa conf SHA-256: e1f9248ab43b159e69e7ffd5a7143ab797caeff7f3aa4216bcf706dc6708d758
mod_para SHA-256: 9586de01f4775113595a80e625820132308a92f42cfa1235907e50c6792abe0c
SSID/BSSID/home: LAB_SSID_REDACTED / LAB_BSSID_REDACTED / 5180 MHz
```

두 시험 모두 `wpa-only` 격리를 사용했다. `wifi_roam`, `wifi_bgscan`, capture,
scan/link/stat logger, snapshot, checker, event, bridge와 fw_watch를 중지하고
`wpa_supplicant@mlan0`만 연결 유지 주체로 남겼다. 관리 SSH는 eth0 경로였으며,
scan loop 중에는 WLAN L3 트래픽을 만들지 않고 시작/종료 판정 ping만 실행했다.

### 14.2 `iw` 4채널 시험

```text
boot ID: 4c060dbc-c645-4de2-94fa-b30baa2b4f8b
iw mlan0 scan freq 5180 5200 5220 5240 ssid LAB_SSID_REDACTED
scan 완료 뒤 5초 간격, 최대 60회
```

scan 12에서 `tx failed=3`이 처음 보였고 scan 13, 76초에 2,504로 증가해
`tx_failed_spike`로 중단했다.

| 항목 | 값 |
|---|---|
| 판정 | **REPRODUCED** |
| scan/onset | 13회 / 76초 |
| `tx failed` | 0 → onset 2,504 → pre-ping 2,527 → post-ping 5,175 |
| final ping | 0/3, loss 100%, rc 1 |
| supplicant/BSSID | `COMPLETED`, `LAB_BSSID_REDACTED` 유지 |
| external start/result | 13/13 |
| own scan | 0 |
| disconnect/auth error | 0 |
| kernel fault/recovery marker | 0 |

### 14.3 `wpa_cli TYPE=ONLY` 4채널 시험

iw artifact를 영속 저장소로 복사하고 재부팅한 뒤 실제 로드값과 기준선 hash를 다시
확인했다.

```text
boot ID: f853486a-7889-4b5c-9669-6d895d55ddb4
wpa_cli -i mlan0 scan TYPE=ONLY \
  freq=5180,5200,5220,5240 \
  ssid 6a68775f776c616e5f
scan-only result 완료 뒤 5초 간격, 최대 60회
```

18개 요청 reply가 모두 `OK`였고 stderr는 비어 있었다. own request와
`Scan-only results received`도 각각 18회였다. scan 15~17에서 `tx failed`가
2/8/14로 증가한 다음 scan 18, 105초에 1,074가 되어 같은 장애로 중단했다.

| 항목 | 값 |
|---|---|
| 판정 | **REPRODUCED** |
| scan/onset | 18회 / 105초 |
| `tx failed` | 0 → onset 1,074 → pre-ping 1,096 → post-ping 3,725 |
| final ping | 0/3, loss 100%, rc 1 |
| supplicant/BSSID | `COMPLETED`, `LAB_BSSID_REDACTED` 유지 |
| own scan / scan-only result | 18/18 |
| external scan | 0 |
| network selection | 0 |
| disconnect/auth error | 0 |
| kernel fault/recovery marker | 0 |

### 14.4 비교 판정

| driver/F/W | requester | onset |
|---|---|---|
| 505.p14+p149.115 | `iw` 4채널 | scan 11 / 66초 |
| 543.p18+p149.115 | `iw` 4채널 | scan 13 / 76초 |
| 505.p14+p149.115 | `wpa_cli TYPE=ONLY` 4채널 | scan 12 / 73초 |
| 543.p18+p149.115 | `wpa_cli TYPE=ONLY` 4채널 | scan 18 / 105초 |

이번 두 실행에서는 543.p18 onset이 늦었지만 이를 개선으로 판정할 수 없다. §8의
543.p18+p149.115 단일 off-channel 반복도 동일 조건에서 scan 5~27회로 크게
흔들렸다. 새 다채널 시험은 두 requester 모두 최대 60회 gate 전에 실패했으며 장애
서명도 505.p14와 같다. 따라서 **543.p18은 p149.115의 scan-return TX/ACK wedge를
해결하지 못했고, requester를 `wpa_cli TYPE=ONLY`로 바꾸는 것도 우회책이 아니다.**

TYPE=ONLY에서 network selection이 0회였으므로 이번 결과도 wpa native roaming과
`wifi_roam.py`의 정책 충돌로 설명되지 않는다. driver crash/FW recovery가 아니라
association은 stale `COMPLETED`로 남고 uplink ACK/TX 경로만 손상되는 기존 서명이다.

### 14.5 영속 artifact와 최종 복구

이번에는 각 실패 boot에서 재부팅하기 전에 보드 `/tmp` 원본을 호스트 worktree로
복사하고, 호스트에서 `sha256sum -c`를 다시 실행했다.

```text
artifacts/driver543-requester-compare-20260824/543p18/
  iw-active-4ch-interval5-run1/
    41 manifest entries, bad 0
    manifest SHA-256 561024cad6bf38b453e6ed55b77f8de3faa42635c35efd905867038eef17a8b3
  wpa-cli-type-only-4ch-interval5-run1/
    50 manifest entries, bad 0
    manifest SHA-256 195e5c91df60172032d549933b9c1b7a468884a9a07eee0c5db81b732de0a809
  final-recovery.txt
    SHA-256 b0fb077b65835c3e9420208051bef2ccab2c77fed34456006e00a551441a786a
```

최종 복구 boot ID는 `059d06d6-300a-4396-aaa6-aca40dd946b5`다. 실제 로드는 계속
543.p18+p149.115이고, 설정 hash도 기준선과 같다. mlan0은 같은 SSID/BSSID/5180/IP에서
`COMPLETED`, `tx failed=0`, gateway ping 5/5다. supplicant/roam/logger/checker/event/
bridge/fw-watch는 원래대로 active이고 test-only condition이 있는 bgscan과 capture는
inactive다.

## 15. wifi_init 없이 manual-default profile 시험

### 15.1 목적과 정확한 로드 경로

제품 `wifi_init` 경로의 direct moal 인자와 확장된 card block이 장애에 관여하는지
확인하기 위해 `wifi_init.service`를 disable하고 다음 두 명령만 수동 실행했다.

```text
insmod /opt/wlan/driver/mlan_imx93.ko
insmod /opt/wlan/driver/moal_imx93.ko mod_para=cts/wifi_mod_para__.conf
```

고정한 바이너리와 설정 식별은 다음과 같다.

```text
driver: 543.p18
mlan SHA-256: c351a0d63f75d53f99ca0b74aba3911d79e9aaf1f4e269dac68cba9b06e4c46a
moal SHA-256: 87b9d0dc5b86c4a40560050f4e9c5a2c8662acc97bea6b516f0f094fcfc9b6a0
F/W: 17.92.1.p149.115
F/W SHA-256: 7c3ef6e12d3cfc9bd638d1571ccf6ddd2e96e0ed179ec70664ccb1df0ba29e57
wifi_mod_para__.conf SHA-256: 8030e6d6f58da8d5b2f391a6e0a963ab685a173a31230b980079645abd2de922
JSON SHA-256: be46944c89e45f0b07a6a672f6a4a6fd23e8ef993a7d88a93ed5caab9219c2c9
wpa conf SHA-256: e1f9248ab43b159e69e7ffd5a7143ab797caeff7f3aa4216bcf706dc6708d758
```

두 requester 시험은 독립 cold boot에서 수행했다. 부팅 시 `wifi_init`과 WLAN module이
inactive/미로드임을 확인한 뒤 manual load를 실행했다. module 초기화 중 mlan0 device
생성에 반응해 enabled wpa unit이 조기 기동하지 않도록 wpa unit을 runtime mask하고,
`insmod moal` 반환과 두 SDIO adapter 등록 완료 뒤에만 unmask/start했다.

첫 preflight 한 번은 이 mask가 없어서 mlan0 생성과 동시에 wpa가 시작됐다. 첫 adapter
등록 중 association scan이 실행되고 두 번째 adapter 초기화와 겹쳐 FW dump/in-band
reset이 발생했으므로 `ABORTED_PRETEST_WPA_STARTED_DURING_INSMOD`로 처리했다. 이 실행은
scan 결과에 포함하지 않았다. 유효 boot에서는 `Driver loaded successfully` 뒤 약 4초
후에만 첫 association scan이 시작됐고 FW fault/recovery marker는 0이었다.

### 15.2 제품 profile과 달라진 실효값

사용자가 지정한 `wifi_mod_para__.conf`는 direct 인자뿐 아니라 SD9098 card block도
제품 파일보다 단순하다. 따라서 이 시험은 “direct 인자만의 A/B”가 아니라 아래 값을
함께 바꾼 **manual-default profile 전체 비교**다.

| 항목 | 제품 `wifi_init` profile | manual-default profile |
|---|---|---|
| direct moal arg | `mod_para` 외 9개 전달 | `mod_para`만 전달 |
| `tx_work` | 0 | 1 (driver default) |
| `bridge_mode` | 1 | 0 (미전달/default) |
| `bridge_keepalive_idle_ms` | 20 | 0 (default) |
| workqueue scheduling | FIFO/45 | NORMAL/0 (미전달/default) |
| card `drv_mode` | 1 | 미지정, exported global 7 |
| card `ps_mode` / `auto_ds` | 2 / 2 | 1 / 1 |
| card `ext_scan` | 2 | 미지정, exported global 0 |
| card `sched_scan` | 1 | 미지정 |
| card `scan_chan_gap` | 20 ms | 미지정, exported global 0 |
| card `keep_previous_scan` | 1 | 미지정 |
| card `net_rx` (mlan0) | 7 | 미지정 |
| 생성 netdev | adapter별 STA 1개 | adapter별 STA/UAP/P2P |
| mlan0 TX power | 20 dBm | 8 dBm (`wifi_init` 후처리 없음) |

F/W, calibration, supplicant conf, AP/BSSID/home frequency와 WLAN L3 observation 규칙은
동일하다. scan loop 중에는 WLAN L3 트래픽이 없고 시작/종료 gateway ping만 사용했다.
supplicant 외 roam/bgscan/capture/logger/checker/event/bridge/fw-watch는 모두 inactive였다.

### 15.3 `iw` 4채널/5초 결과

```text
boot ID: 069d86c1-f977-4e60-97da-8695e6b3dd0c
iw mlan0 scan freq 5180 5200 5220 5240 ssid LAB_SSID_REDACTED
scan 완료 뒤 5초 간격, 60회
```

| 항목 | 값 |
|---|---|
| 판정 | **NOT_REPRODUCED** |
| 완료 | 60/60, loop 333초(result 종료 335초) |
| `tx failed` | 0 → pre/post-ping 1/1 |
| final ping | 3/3, loss 0%, rc 0 |
| supplicant/BSSID | `COMPLETED`, `LAB_BSSID_REDACTED` 유지 |
| external start/result | 60/60 |
| own scan | 0 |
| disconnect/auth error | 0 |
| kernel fault/recovery marker | 0 |
| scan command 소요 | 395~449 ms, 평균 409.6 ms |

### 15.4 `wpa_cli TYPE=ONLY` 4채널/5초 결과

```text
boot ID: 76551d68-b85e-42c2-ba61-58a84424b157
wpa_cli -i mlan0 scan TYPE=ONLY \
  freq=5180,5200,5220,5240 \
  ssid 6a68775f776c616e5f
scan-only result 완료 뒤 5초 간격, 60회
```

| 항목 | 값 |
|---|---|
| 판정 | **NOT_REPRODUCED** |
| 완료 | 60/60, loop 347초(result 종료 349초) |
| `tx failed` | 0 → pre/post-ping 2/2 |
| final ping | 3/3, loss 0%, rc 0 |
| supplicant/BSSID | `COMPLETED`, `LAB_BSSID_REDACTED` 유지 |
| own scan / scan-only result | 60/60 |
| external scan | 0 |
| network selection | 0 |
| disconnect/auth error | 0 |
| kernel fault/recovery marker | 0 |
| request-to-result | 493~836 ms, 평균 628.7 ms |

### 15.5 판정과 다음 분리시험

같은 543.p18+p149.115에서 제품 profile은 `iw` scan 13/76초와 wpa scan 18/105초에
실패했지만 manual-default profile은 두 requester 모두 60회 gate를 통과했다. 따라서
**제품 profile에만 있는 direct 인자, card block 또는 `wifi_init` 후처리 중 하나 이상이
p149.115 scan-return TX/ACK wedge의 재현 조건에 관여한다는 강한 증거**가 추가됐다.

manual-default의 scan 자체도 제품 profile보다 길었다. 같은 543 시험에서 제품 `iw`는
평균 277.5 ms, 제품 wpa는 평균 362.1 ms였지만 manual-default는 각각 409.6/628.7 ms였다.
특히 `ext_scan=2 → 0`과 함께 requester 두 종류 모두 처리시간과 장애 결과가 바뀌어
enhanced scan 경로가 우선 후보지만, 이번 데이터만으로 특정 옵션을 원인으로 확정할 수는
없다. `tx_work`, bridge/workqueue 인자, power-save, scan gap, net_rx와 TX power도 동시에
달라졌기 때문이다.

다음 최소 분리시험은 2x2의 나머지 두 칸이다.

1. 제품 `wifi_mod_para.conf` + direct arg는 `mod_para`만: card block만 제품값
2. `wifi_mod_para__.conf` + 제품 direct arg 전체: direct arg만 제품값

첫 조합이 실패하면 card block(`ext_scan` 포함) 쪽, 두 번째가 실패하면 direct arg 쪽으로
원인군을 우선 분리할 수 있다. 이후 해당 그룹 안에서 한 옵션씩 또는 이분법으로 좁힌다.
이번 60회 미재현은 이 다음 분리를 진행할 근거이지 장시간 안정성 보증은 아니다.

### 15.6 artifact와 제품 경로 복구

```text
artifacts/moal-manual-default-20260824/543p18-p149115/
  iw-active-4ch-interval5-run1/
    141 manifest entries, bad 0
    manifest SHA-256 97fc7ce5268d103130003ffc3e77ea4cdf09417010c1189663086079713b0897
  wpa-cli-type-only-4ch-interval5-run1/
    140 manifest entries, bad 0
    manifest SHA-256 d658fec9c02bf303ca942c52568d81f19fefde64fa6678f2f2612f9bfde06f24
  post-recovery-health-followup.txt
    SHA-256 5e336d80813ab0140fc84c6382dd9a7d1bef7e26f1a8693fe22a791839303c84
  EVIDENCE_SHA256SUMS
    SHA-256 d42706d73c5b847566aa88f2cf70e2086bae310c0a0e25636bba6b0a72cd0007
```

시험 뒤 `wifi_init.service`를 다시 enable하고 재부팅했다. 최종 recovery boot ID는
`b296ede8-339a-4277-a0bb-f8f15c6d0550`이며 제품 `wifi_mod_para.conf`와 원래 direct
인자(`tx_work=0`, `bridge_mode=1`, idle 20, FIFO/45)가 다시 적용됐다. 실제 로드는
543.p18+p149.115, association은 같은 SSID/BSSID/5180/IP에서 `COMPLETED`,
`tx failed=0`, gateway ping 5/5, boot fault marker 0이다. 원래 서비스 상태도 복구됐다.
후속 fresh ping에서는 4/5와 19/20으로 각각 한 패킷 유실이 있었지만 전후
`tx failed=0`, `COMPLETED`와 BSSID/frequency가 유지됐다. 따라서 기존 wedge 서명은
아니며 제품 profile 복구 후 링크에 간헐 ICMP loss가 관찰된 사실은 별도로 남긴다.
마지막 영속 follow-up은 20/20, `tx failed=0`, 상태/BSSID/frequency 동일이었다.

## 16. `ext_scan=2` 단일변수 분리시험

### 16.1 가설과 설정 차이

manual-default가 통과하고 제품 profile이 실패한 차이 중 enhanced scan 경로를 먼저
분리하기 위해, `wifi_mod_para__.conf`에서 mlan0에 해당하는 `SD9098_0`에 다음 한 줄만
추가했다. 원본 파일과 `SD9098_1`은 변경하지 않았다.

```diff
 SD9098_0 = {
     ...
+    ext_scan=2
 }
```

| 파일 | SHA-256 |
|---|---|
| 기준 `wifi_mod_para__.conf` | `8030e6d6f58da8d5b2f391a6e0a963ab685a173a31230b980079645abd2de922` |
| 시험 `wifi_mod_para_extscan2.conf` | `46c29aa2edc74a4553a5d924f6084d7dfadef6c19298af1d45b9ae3451bc76eb` |

line 단위 검증 결과는 삭제 0줄, 추가 1줄이며 추가 내용은 정확히 `ext_scan=2`다. 시험
로드 명령은 다음과 같아 direct moal 인자도 manual-default와 동일하게 유지했다.

```text
insmod /opt/wlan/driver/mlan_imx93.ko
insmod /opt/wlan/driver/moal_imx93.ko \
  mod_para=cts/wifi_mod_para_extscan2.conf
```

두 유효 boot 모두 543.p18 driver와 p149.115 F/W, 같은 calibration/WPA conf/AP를
사용했다. dmesg는 `card_type: SD9098, config block: 0` 다음에 `ext_scan = 2`를
출력했고 block 1에는 `ext_scan` 출력이 없었다. 따라서 sysfs의 전역 기본값 표시와
무관하게 mlan0 card block 적용이 확인됐다. supplicant 외 roam/bgscan/capture/logger/
checker/event/bridge/fw-watch는 모두 inactive였고 baseline fault marker는 0이었다.

### 16.2 `iw` 결과

```text
boot ID: e264905c-6291-4123-96b8-fa59f1290b91
iw mlan0 scan freq 5180 5200 5220 5240 ssid LAB_SSID_REDACTED
scan 완료 뒤 5초 간격, 최대 60회
```

| 항목 | 값 |
|---|---|
| 판정 | **NOT_REPRODUCED** |
| 완료 | 60/60, 338초 |
| `tx failed` | 0 → pre/post-ping 0/0 |
| final ping | 3/3, rc 0 |
| 상태 | `COMPLETED`, BSSID/5180 MHz 유지 |
| external start/result | 60/60 |
| own scan / disconnect / FW fault | 0 / 0 / 0 |
| scan 소요 | 398~458 ms, 평균 419.9 ms, p95 452 ms |

제품 profile은 같은 543.p18+p149.115에서 scan 13/76초에 실패했지만 이 조건은 60회
gate를 통과했다.

### 16.3 `wpa_cli TYPE=ONLY` 결과

`iw` 시험 뒤 재부팅하여 별도 cold boot에서 같은 전용 설정을 다시 로드했다.

```text
boot ID: 51a3a94a-9da1-4471-825f-ad502de729f4
wpa_cli -i mlan0 scan TYPE=ONLY \
  freq=5180,5200,5220,5240 \
  ssid 6a68775f776c616e5f
scan-only result 완료 뒤 5초 간격, 최대 60회
```

| 항목 | 값 |
|---|---|
| 판정 | **NOT_REPRODUCED** |
| 완료 | 60/60, 350초 |
| `tx failed` | 0 → pre/post-ping 0/0 |
| final ping | 3/3, rc 0 |
| 상태 | `COMPLETED`, BSSID/5180 MHz 유지 |
| own scan / scan-only result | 60/60 |
| external scan / network selection | 0 / 0 |
| disconnect / FW fault | 0 / 0 |
| request-to-result | 484~795 ms, 평균 624.1 ms, p95 751 ms |

제품 profile의 wpa requester는 scan 18/105초에 실패했지만 이 조건은 60회 gate를
통과했다. 부팅 과정에서 수동 로드 preflight에 들어가기 전에 사라진 짧은 boot 두 개가
관찰됐지만, 그 boot들에서는 scan을 실행하지 않았다. 위 결과는 설정/driver/F/W/연결/
fault 사전조건을 모두 통과한 `51a3a94a-...` boot만을 대상으로 한다.

### 16.4 판정

**`ext_scan=2` 단독은 이번 60회 gate에서 장애의 충분조건이 아니다.** 옵션 적용 자체는
dmesg로 확인됐지만 requester 두 종류 모두 실패하지 않았다. 또한 scan 처리시간도 제품
profile(`iw` 277.5 ms, wpa 362.1 ms) 쪽으로 이동하지 않고 manual-default(`iw`
409.6 ms, wpa 628.7 ms)에 가까운 419.9/624.1 ms였다.

이 결과는 `ext_scan`이 원인과 무관하다는 뜻은 아니다. 제품 block의 `sched_scan=1`,
`scan_chan_gap=20`, `keep_previous_scan=1` 또는 direct 인자/power-save/net_rx/wifi_init
후처리와의 **상호작용 조건**일 수 있다. 다음 최소 분리는 두 가지 중 하나다.

1. 현재 파일에 나머지 scan card 옵션 3개를 함께 추가해 scan-option interaction을 확인
2. 제품 card block + `mod_para`만, manual-default block + 제품 direct 인자의 2x2를 먼저
   완성한 뒤 실패한 그룹 안에서 이분법 수행

단일변수 결과가 이미 음성이므로 원인군을 넓게 분리할 수 있는 2번이 더 체계적이다.

### 16.5 artifact와 제품 복구

```text
artifacts/moal-extscan2-singlevar-20260824/543p18-p149115/
  config/
    원본, 시험 설정, one-line diff, manual-load controller
  iw-active-4ch-interval5-run1/
    144 manifest entries, bad 0
    manifest SHA-256 d57b1e0c418bc2bd5b76b63bbfc995bb05b1c909f5ad6e8826866b122b16ba52
  wpa-cli-type-only-4ch-interval5-run1/
    143 manifest entries, bad 0
    manifest SHA-256 8b3a0b6df35d092e478d99ff72f3836f4eba23f9d4637b3ff6f18a598afe6a5d
  EVIDENCE_SHA256SUMS
    297 entries, bad 0
    SHA-256 4a72d381a9b4c2358e37ec7d4089476d56f577dc15d2738a435336164fb0aa97
```

시험 후 `wifi_init.service`를 enable하고 제품 경로로 재부팅했다. recovery boot ID는
`2764505c-de05-4cc7-aa3f-df0ea1ab74e5`이며 제품 `wifi_mod_para.conf`, direct 인자
`tx_work=0`, `bridge_mode=1`, idle 20, FIFO/45가 복구됐다. 동일 AP/BSSID/5180/IP에서
`COMPLETED`, boot fault marker 0, `tx failed=0`이다. 첫 recovery ping은 18/20이었지만
즉시 반복한 20회는 20/20이었고 전후 `tx failed=0`이므로 기존 wedge 서명은 없었다.
시험 전용 board 파일과 원격 임시 artifact는 삭제했으며 원본 설정/driver/F/W hash가
그대로임을 확인했다.

## 17. `wifi_init` 없이 제품 module 입력 그대로 수동 로드

### 17.1 목적과 고정한 입력

§16에서 `ext_scan=2` 단독이 충분조건이 아니었으므로, 이번에는 제품 module 입력은
전부 유지하면서 `wifi_init.sh`의 런타임/FW/network 후처리만 제외했다. 설치된
`/usr/local/scripts/wifi_init.sh`와 저장소 파일의 SHA-256은 모두
`e7a18aacb437008472c0e7c6846bc7ac44db79e039070b4f605dfa02e391de7d`로 일치한다.
현재 제품 boot journal의 최종 인자열과 수동 controller 배열을 토큰 순서까지 비교해
동일함을 확인했다.

```text
insmod /opt/wlan/driver/mlan_imx93.ko
insmod /opt/wlan/driver/moal_imx93.ko \
  mod_para=cts/wifi_mod_para.conf \
  tx_work=0 \
  bridge_mode=1 \
  bridge_debug=0 \
  bridge_wlan_idx=0 \
  bridge_keepalive_ms=1 \
  bridge_keepalive_idle_ms=20 \
  bridge_local_hairpin=0 \
  wq_sched_policy=1 \
  wq_sched_prio=45
```

제품 `wifi_mod_para.conf` SHA-256은
`9586de01f4775113595a80e625820132308a92f42cfa1235907e50c6792abe0c`다. 각 유효
boot의 dmesg에서 다음 실효값도 다시 확인했다.

```text
card block: drv_mode=1, ps_mode=2, auto_ds=2,
            ext_scan=2, sched_scan=1, scan_chan_gap=20, net_rx=7
direct arg: tx_work=0, bridge mode=1, keepalive=1/idle 20,
            main workqueue SCHED_FIFO/45
```

따라서 manual-default나 §16과 달리 module 입력은 제품과 같다. 현재 boot에는
`wifi_init` journal이 없으며 controller에는 `mlanutl`과 `networkctl` 호출이 없다.
supplicant만 active로 두고 roam/bgscan/capture/logger/checker/event/bridge/fw-watch는
모두 inactive로 유지했다. 이는 §14의 실패 시험과 같은 `wpa-only` 서비스 격리다.

### 17.2 제외된 `wifi_init` 런타임 동작

제품 boot journal과 일치하는 소스를 기준으로, 수동 boot에서 실행하지 않은 주요 동작은
다음과 같다.

1. `TXPWRLIMIT_PATH`의 2.4 GHz/5 GHz sub0~3 hostcmd
2. `enable_thermal_mgmt` hostcmd
3. `macctrl 0x00010e13`, `httxcfg 0x00000063`,
   `htcapinfo 0x05c20000`, `reassoctrl 1`
4. `antcfg 0x0101`, rate-adapt SR(70/90/100 ms), HT/VHT/HE MCS tier 7
   및 연결 후 MCS SET + 1회 reassociate + GET 검증
5. `networkctl` reload/reconfigure, peer-route/sysctl 처리와 ExecStartPost 서비스 기동

눈에 보이는 차이는 수동 boot의 TX power가 8 dBm, 복구한 제품 boot가 20 dBm인 것이다.
두 조건 모두 association 폭은 20 MHz였다. 기존 `.network`, `.link`, udev 파일은 설치된
영속 상태이므로 `wifi_init` 없이도 적용될 수 있다. 또한 module load 뒤 supplicant 시작
시점도 완전히 같지는 않다. 따라서 이번 시험은 위 런타임 호출과 sequencing을 하나의
원인군으로 제외한 것이며, 개별 명령을 분리한 시험은 아니다.

제품 recovery journal에서는 association이 MCS tier를 초기화한 뒤 connected SET을
수행하고, 실제로 1회 reassociate한 다음 GET 검증까지 완료한 것이 확인됐다. 수동
no-init boot에는 이 sequence가 없으므로 다음 FW replay 안에서 우선 관찰할 하위 후보다.

### 17.3 `iw` 결과

```text
boot ID: 229ce911-57b3-47d3-818c-c773be10ec2f
iw mlan0 scan freq 5180 5200 5220 5240 ssid LAB_SSID_REDACTED
scan 완료 뒤 5초 간격, 최대 60회
```

| 항목 | 값 |
|---|---|
| 판정 | **NOT_REPRODUCED** |
| 완료 | 60/60, 328초 |
| `tx failed` | 0 → pre/post-ping 1/1 |
| final ping | 3/3, rc 0 |
| 상태 | `COMPLETED`, BSSID/5180 MHz 유지 |
| external start/result | 60/60 |
| own scan / disconnect / FW fault | 0 / 0 / 0 |
| scan 소요 | 273~281 ms, 평균 274.4 ms, p95 276 ms |

§14의 전체 제품 경로는 같은 driver/F/W와 서비스 격리에서 scan 13/76초에 실패했다.
반면 이번에는 60회를 완료했다. 주목할 점은 장애는 사라졌지만 scan 시간은 §14 제품값
277.5 ms와 거의 같다는 것이다. 즉 제품 module 입력만으로 빠른 enhanced scan 경로는
활성화됐지만 wedge까지 만들지는 못했다.

### 17.4 `wpa_cli TYPE=ONLY` 결과

`iw` artifact를 호스트로 복사한 뒤 재부팅하고, 동일 module 입력을 다시 수동 로드했다.

```text
boot ID: 0533897f-f2e7-4a1e-85b4-d71d54505460
wpa_cli -i mlan0 scan TYPE=ONLY \
  freq=5180,5200,5220,5240 \
  ssid 6a68775f776c616e5f
scan-only result 완료 뒤 5초 간격, 최대 60회
```

| 항목 | 값 |
|---|---|
| 판정 | **NOT_REPRODUCED** |
| 완료 | 60/60, 341초 |
| `tx failed` | 0 → pre/post-ping 4/4 |
| final ping | 3/3, rc 0 |
| 상태 | `COMPLETED`, BSSID/5180 MHz 유지 |
| own scan / scan-only result | 60/60 |
| external scan / network selection | 0 / 0 |
| disconnect / FW fault | 0 / 0 |
| request-to-result | 306~704 ms, 평균 490.9 ms, p95 641 ms |

§14의 전체 제품 경로는 scan 18/105초에 `tx failed=1,074`와 100% ping loss로
실패했다. 이번 `tx failed=4`는 ping이 유지되고 대량 증가가 없으므로 기존 wedge 서명이
아니다.

### 17.5 판정과 다음 분리

**제품 `mod_para`와 direct arg 전체만으로는 이번 60회 gate에서 장애가 재현되지
않았다.** 두 requester가 모두 통과했으므로, §14와 이번 시험 사이에 남은 차이인
`wifi_init`의 일회성 FW/network 설정 또는 실행 순서 중 하나 이상이 재현 cofactor다.
다만 확률 결함에 대한 두 번의 음성 gate이므로 장시간 안정성을 보증하거나 단일 명령을
원인으로 확정하지는 않는다.

다음 이분법은 수동 module load를 유지한 채 **제품 순서 그대로 `mlanutl`/FW 설정만
replay**하는 것이다. pre-association 명령을 적용한 뒤 supplicant를 시작하고, 연결 후에는
관찰된 MCS SET/reassociate/GET 검증 sequence까지 재현한다. network/sysctl/ExecStartPost는
계속 제외한다.

- replay에서 장애가 돌아오면 FW 설정 내부를
  `TX-power/thermal` → `radio defaults` → `antenna/rate/MCS(+1회 reassociate)` 그룹으로 분할
- replay도 통과하면 non-mlanutl network/MAC/udev 상태 또는 association timing을 분리

이 순서가 개별 명령을 바로 하나씩 추가하는 것보다 원인군을 먼저 절반으로 줄인다.

### 17.6 artifact와 제품 복구

```text
artifacts/moal-product-args-no-init-20260824/543p18-p149115/
  config/
    제품 mod_para, exact-arg 대조, 제외 소스 구간, runtime FW 설정 snapshot
  iw-active-4ch-interval5-run1/
    144 manifest entries, bad 0
    manifest SHA-256 310b96cec6afbd47a50af8c6894336a23d1b14d9982141f410735998918b6093
  wpa-cli-type-only-4ch-interval5-run1/
    143 manifest entries, bad 0
    manifest SHA-256 538d2c18a061d9e1c500936b0d01f38ea7ac21531a63e8ab806d7c05c46b994d
  EVIDENCE_SHA256SUMS
    298 entries, bad 0
    SHA-256 8b8a337bb7285ed0b457470107143de7da4edf6b56293c8931d264127be5cd52
```

시험 뒤 제품 경로로 복구한 boot ID는
`4ff8714d-c212-47a2-b16b-49b62933aaf1`이다. `wifi_init`은 enabled/active이고,
같은 SSID/BSSID/5180/IP에서 `COMPLETED`, TX power 20 dBm, `tx failed=0`, fault marker
0이다. recovery ping 20/20과 최종 cleanup 확인 5/5가 모두 성공했다. 시험 controller,
harness 및 원격 임시 artifact는 삭제했고 원본 module/config hash가 유지됨을 확인했다.

## 18. 제품 module 입력 + `mlanutl`/FW-only replay

### 18.1 판별 목적과 실행 범위

§17에서 제품 module 입력 전체만으로 `iw` 60회가 통과했으므로, 다음 원인군인
`wifi_init`의 일회성 FW 설정을 분리했다. 유효 시험 boot
`9eb143f5-ccbd-4f5e-8817-2f6411afd3d6`은 `wifi_init.service`가 disabled/inactive이고
자동 로드된 `mlan`/`moal` 및 `mlan0`이 없는 것을 확인한 뒤 시작했다.

수동 module 입력은 §17과 토큰 순서까지 같다.

```text
insmod /opt/wlan/driver/mlan_imx93.ko
insmod /opt/wlan/driver/moal_imx93.ko \
  mod_para=cts/wifi_mod_para.conf \
  tx_work=0 \
  bridge_mode=1 \
  bridge_debug=0 \
  bridge_wlan_idx=0 \
  bridge_keepalive_ms=1 \
  bridge_keepalive_idle_ms=20 \
  bridge_local_hairpin=0 \
  wq_sched_policy=1 \
  wq_sched_prio=45
```

그 위에 제품 소스와 동일한 순서로 다음 FW 동작만 replay했다.

1. TX power limit: 2.4 GHz와 5 GHz sub0~3 hostcmd
2. `enable_thermal_mgmt`
3. `macctrl 0x00010e13`, `httxcfg 0x00000063`,
   `htcapinfo 0x05c20000`, `reassoctrl 1`
4. `antcfg 0x0101`, rate-adapt SR 70/90/100 ms
5. `mcstiercfg ht 7 vht 7 he both 7`의 association 전 SET/GET
6. 첫 연결 뒤 connected SET, `wpa_cli reassociate` 1회, 두 번째 CONNECTED 뒤 GET 검증

association 전 HE map이 보이지 않아 제품과 같이 5회 SET 뒤 deferred marker가 생성됐고,
첫 association이 MCS tier를 되돌린 뒤 connected SET과 정확히 한 번의 reassociate가
실행됐다. 두 번째 CONNECTED에서 GET 검증이 성공하여 pending/once marker가 모두
제거됐다. controller의 직접 명령은 전부 rc 0이며 연결 이벤트 수는 2였다.

이번에도 `networkctl reload/reconfigure`, peer-route/sysctl 처리 및 ExecStartPost 자식
서비스는 실행하지 않았다. 설치된 영속 `.network`/udev 파일은 그대로이므로, 이 시험은
해당 파일의 존재가 아니라 **이번 boot에서 `wifi_init`이 실행하는 동작**을 분리한다.
`wpa_supplicant`만 active였고 roam/bgscan/capture/logger/checker/event/bridge/fw-watch는
모두 inactive였다. 사용자의 범위 결정에 따라 `wpa_cli TYPE=ONLY` 시험은 생략했다.

### 18.2 replay 완료 후 사전조건

| 항목 | 실효값 |
|---|---|
| driver / F/W | 543.p18 / 17.92.1.p149.115 |
| 제품 config hash | `wifi_mod_para.conf` `9586de01...`, JSON `be46944c...` |
| bridge | mode 1, `wlan_bss=0 (mlan0)`, peer eth0, keepalive 1/idle 20, Activated |
| workqueue | `SCHED_FIFO`, priority 45 |
| TX power | **20 dBm** (§17 no-replay는 8 dBm) |
| association | `LAB_SSID_REDACTED`, `LAB_BSSID_REDACTED`, 5180 MHz, `COMPLETED` |
| baseline | `tx failed=0`, ping 3/3, FW fault marker 0 |

따라서 단순히 명령을 호출했다는 사실뿐 아니라 제품에서 관찰된 TX power와 MCS
SET/reassociate/GET 상태까지 replay됐음을 확인하고 scan을 시작했다.

### 18.3 `iw` 결과

```text
iw mlan0 scan freq 5180 5200 5220 5240 ssid LAB_SSID_REDACTED
scan 완료 뒤 5초 간격, 최대 60회
```

| 항목 | 값 |
|---|---|
| 판정 | **REPRODUCED** |
| 중단 | scan 16 / 91초, `tx_failed_spike` |
| `tx failed` 진행 | 0 → scan 13: 1 → 14: 7 → 15: 16 → 16: **2,540** |
| ping 전후 counter | 2,575 → 5,207 |
| final ping | **0/3, rc 1** |
| 상태 | 전후 `COMPLETED`, 같은 BSSID/SSID/5180 MHz |
| external start/result | 16/16 |
| own scan / disconnect / FW fault | 0 / 0 / 0 |
| scan 소요 | 274~287 ms, 평균 277.9 ms, median 276.5 ms, p95 287 ms |

scan 15까지 counter가 16이었으나 scan 16 완료 뒤 2,540으로 급증했고, 이어진 ping은
100% 손실과 추가 counter 증가를 만들었다. supplicant 상태와 BSSID는 유지됐고
disconnect/FW recovery marker는 없으므로 기존 scan-return TX/ACK wedge 서명과 일치한다.
장애 확인 즉시 추가 scan/reassociate 없이 artifact를 보존하고 재부팅했다.

### 18.4 판정과 다음 분리

§17의 정확한 제품 module 입력만 사용한 `iw`는 60/60을 통과했지만, 이번에는 같은
module 입력에 FW replay를 더하자 scan 16에서 실패했다. 따라서 이번 gate에서는
**replay한 `mlanutl`/FW 설정 중 하나 이상 또는 그 association 전후 순서가 재현에 필요한
cofactor**다. 반대로 `wifi_init`의 network/sysctl/ExecStartPost 동작은 이번 재현의
필수조건이 아니다.

아직 TX power, thermal, radio default, antenna/rate/MCS를 함께 replay했으므로 어느 한
명령을 원인으로 확정할 수 없고, 모든 명령이 필요하다는 뜻도 아니다. 다음 시험은 같은
module 입력과 `iw` gate를 유지하고 독립 boot마다 한 그룹만 바꾸는 FW 내부 분리다.

1. A: TX-power + thermal
2. B: `macctrl`/`httxcfg`/`htcapinfo`/`reassoctrl`
3. C: antenna + rate + MCS SET/1회 reassociate/GET

우선 C 단독과 A+B를 이분한 뒤, 실패한 쪽만 하위 그룹으로 나누는 것이 최소 run 수다.
두 쪽이 모두 단독 통과하면 pair 조합으로 상호작용을 확인한다.

### 18.5 artifact와 제품 복구

```text
artifacts/moal-fw-replay-no-init-20260824/543p18-p149115/
  summary.txt
  config/
    replay controller, frozen JSON/mod_para/FW command files, iw harness
  fw-only-iw-run1/
    controller/
      각 replay 명령과 rc, module/dmesg/journal, FW live preflight
    iw-active-4ch-interval5-run1/
      scan 16회 원문, progress/result, wpa/kernel journal
    EVIDENCE_SHA256SUMS
      72 entries, 72/72 OK
      SHA-256 0b30ae286bab6a93fab4090f27b08845b4b95dfd66cce867662cf0e76060cb12
  fw-only-iw-run1.tar.gz
    SHA-256 c6a18d4be04bc8505e74efb77bc9781f6987cff5db0f2c732e0293cb536637bd
```

제품 복구 boot ID는 `f3b5ff59-d196-45ab-9e26-b30b41cf7f3b`이다. `wifi_init`은
enabled/active, 원래 자식 서비스 상태도 복구됐고 같은 SSID/BSSID/5180/IP에서
`COMPLETED`, TX power 20 dBm이다. recovery ping은 20/20, 전후 `tx failed=0`, fault
marker 0이다. 보드의 시험 controller/harness/원격 artifact를 삭제했고 module/FW/config
hash가 시험 전과 같은 것도 확인했다.

## 19. FW replay 1차 그룹 분리 — C-only

### 19.1 그룹 정의와 고정 조건

§18의 전체 FW replay가 재현됐으므로 다음 세 그룹 중 C만 남겼다.

| 그룹 | 내용 | 이번 실행 |
|---|---|---|
| A | TX power limit 2G/5G hostcmd + thermal enable | **미실행** |
| B | `macctrl`/`httxcfg`/`htcapinfo`/`reassoctrl` | **미실행** |
| C | antcfg + rate-adapt + MCS SET/1회 reassociate/GET | **실행** |

유효 boot ID는 `5ad25872-4ed8-4171-a89c-7bd14a48fd1f`이다. `wifi_init` 없이 시작해
자동 로드된 WLAN module과 `mlan0`이 없음을 확인한 뒤, §17~18과 동일한 제품
`mod_para`와 direct moal 인자로 수동 로드했다. driver/F/W/config hash도 앞선 시험과
같다.

C의 실제 순서는 다음과 같다.

1. `antcfg 0x0101`
2. rate-adapt SR 70/90/100 ms
3. association 전 `mcstiercfg ht 7 vht 7 he both 7` SET/GET 5회와 deferred marker
4. 첫 연결 뒤 connected MCS SET
5. `wpa_cli reassociate` 정확히 1회
6. 두 번째 CONNECTED 뒤 GET 검증과 marker 정리

controller의 실행 command directory에는 module load와 C의 GET 증거만 있으며,
TX-power/thermal/radio-default 명령 파일은 0개다. 제품/full replay의 20 dBm과 달리
사전조건 TX power도 **8 dBm**이어서 A가 적용되지 않았음을 별도로 확인했다.

| 사전조건 | 값 |
|---|---|
| association | `COMPLETED`, `LAB_SSID_REDACTED`, `LAB_BSSID_REDACTED`, 5180 MHz |
| 연결 이벤트 | 2회 — 초기 연결 + C의 1회 reassociate |
| 시작 상태 | `tx failed=0`, ping 3/3, FW fault 0 |
| 서비스 | supplicant만 active, 나머지 scan/roam/logger/checker/event/fw-watch inactive |

`networkctl`, network/sysctl/peer-route, ExecStartPost는 계속 제외했고 `wpa_cli` scan 시험도
사용자 결정에 따라 실행하지 않았다.

### 19.2 `iw` 결과

```text
iw mlan0 scan freq 5180 5200 5220 5240 ssid LAB_SSID_REDACTED
scan 완료 뒤 5초 간격, 최대 60회
```

| 항목 | 값 |
|---|---|
| 판정 | **REPRODUCED** |
| 중단 | scan 29 / 161초 |
| `tx failed` 진행 | scan 27까지 0 → scan 28: 983 → scan 29: **4,297** |
| ping 전후 counter | 4,321 → 6,963 |
| final ping | **0/3, rc 1** |
| 상태 | 전후 `COMPLETED`, BSSID/SSID/5180 MHz 유지 |
| external start/result | 29/29 |
| own scan / disconnect / FW fault | 0 / 0 / 0 |
| scan 소요 | 274~319 ms, 평균 279.9 ms, median 276 ms, p95 313 ms |

scan 28부터 counter가 증가했고 다음 scan에서 fail threshold를 넘었다. 최종 ping은 100%
손실과 counter 추가 증가를 만들었지만 supplicant 상태와 BSSID는 유지됐다. 따라서 §18과
같은 scan-return TX/ACK wedge 서명이다.

### 19.3 판정과 다음 분리

**C-only가 이번 gate에서 장애의 충분조건이다.** A와 B가 전혀 실행되지 않은 상태에서도
재현됐으므로, 적어도 이번 재현에는 TX-power/thermal/radio-default가 필요하지 않았다.
다만 이는 A+B가 무관하거나 독립적으로 재현할 수 없다는 증명은 아니며, 발생 scan이
§18의 16에서 이번 29로 달라진 것도 확률적 trigger 범위 안에서만 해석한다.

C 내부의 다음 최소 분리는 다음과 같다.

1. MCS-only: association 전 SET + connected SET + 1회 reassociate + GET
2. MCS-only가 음성이면 antcfg+rate를 함께 실행
3. 두 반쪽이 모두 음성이면 C 내부 상호작용을 확인

C-only가 positive stop condition을 충족했으므로 A+B 독립 run은 실행하지 않았다.

### 19.4 artifact와 제품 복구

```text
artifacts/moal-fw-group-bisect-20260824/543p18-p149115/
  summary.txt
  config/
    c-only/ab-only 선택형 controller, frozen 설정, iw harness
  c-only-iw-run1/
    controller/
    iw-active-4ch-interval5-run1/
    EVIDENCE_SHA256SUMS
      88 entries, 88/88 OK
      SHA-256 7e82c18a50bcddef633f4b0f91f612f47ba25588e25fde9e4924f92fab162653
  c-only-iw-run1.tar.gz
    SHA-256 d4a44b03187e1c67ef9225ecfdf6a4b4159b332398ce30d8d2a1c1a994a46b3c
```

제품 복구 boot ID는 `1b15528d-8fff-4b4f-b839-a3a4cc4711a0`이다. `wifi_init`과
`wlan_fw_watch`는 enabled/active이고 원래 자식 서비스 상태도 복구됐다. 같은
SSID/BSSID/5180/IP에서 `COMPLETED`, TX power 20 dBm, recovery ping 20/20,
전후 `tx failed=0`, fault marker 0이다. 시험용 board 파일은 모두 삭제했다.

## 20. C 내부 분리 — MCS-only 대 antcfg+rate

### 20.1 목적과 공통 고정 조건

§19의 C-only가 재현됐으므로 C를 다음 두 반쪽으로 분리했다. 각 profile은 서로 다른
clean boot에서 실행했으며, 유효 boot를 확정하기 전에 boot ID가 10초 이상 유지되고
uptime이 80초 이상인지 확인했다. 각 시험 시작 전 `wifi_init`은 disabled/inactive,
현재 boot journal은 비어 있고 `mlan`/`moal`과 `mlan0`이 없는 것도 확인했다.

| profile | 실행 | 명시적으로 미실행 |
|---|---|---|
| MCS-only | association 전 MCS SET/GET, connected SET, reassociate 1회, 두 번째 CONNECTED GET | antcfg, rate-adapt, A, B |
| AR-only | `antcfg 0x0101`, rate-adapt SR 70/90/100 ms | MCS SET/GET, reassociate, A, B |

두 profile 모두 driver/F/W는 543.p18/17.92.1.p149.115이고 제품 module 입력을 그대로
사용했다.

```text
insmod /opt/wlan/driver/mlan_imx93.ko
insmod /opt/wlan/driver/moal_imx93.ko \
  mod_para=cts/wifi_mod_para.conf \
  tx_work=0 \
  bridge_mode=1 \
  bridge_debug=0 \
  bridge_wlan_idx=0 \
  bridge_keepalive_ms=1 \
  bridge_keepalive_idle_ms=20 \
  bridge_local_hairpin=0 \
  wq_sched_policy=1 \
  wq_sched_prio=45
```

두 경우 모두 A가 빠져 TX power는 8 dBm이었고, `networkctl`, network/sysctl/peer-route,
ExecStartPost 자식 서비스도 계속 제외했다. 시험 중에는 `wpa_supplicant`만 active였으며
사용자 결정에 따라 `wpa_cli` scan은 실행하지 않았다. scan gate도 이전과 같다.

```text
iw mlan0 scan freq 5180 5200 5220 5240 ssid LAB_SSID_REDACTED
scan 완료 뒤 5초 대기, 최대 60회
tx failed 증가량이 1,000 이상이면 즉시 중단
```

보드 로그의 로컬 시각은 2026-08-20이고 artifact directory는 host session 날짜를
따른다. 실행 식별에는 시각 대신 아래 boot ID를 사용했다.

### 20.2 MCS-only 결과

유효 boot ID는 `f8a870c6-66aa-4737-bf3d-e1afbd6e4fba`다. controller command
evidence에는 두 module load와 MCS GET인 `042`/`043`만 존재한다. antcfg/rate 명령은
없고 live GET도 antenna Tx/Rx `0x303`, noise 기반 dynamic rate-adapt 100 ms로 FW
기본 상태가 유지됨을 보였다. 연결 이벤트는 초기 연결과 의도한 1회 reassociate를 합쳐
정확히 2회였다.

| 항목 | 값 |
|---|---|
| 판정 | **NOT_REPRODUCED** |
| 완료 | **60/60**, 총 327초 |
| `tx failed` | 0 → final-ping 전 5 → 후 5 |
| ping | 시작 3/3, 종료 **3/3** |
| 상태 | 전후 `COMPLETED`, BSSID/SSID/5180 MHz 유지 |
| external start/result | 60/60 |
| own scan / disconnect / FW fault | 0 / 0 / 0 |
| scan 소요 | 272~304 ms, 평균 275.1 ms, median 274 ms, p95 278 ms |

bounded 60-scan gate에서는 데이터 경로 wedge가 발생하지 않았다. 이는 MCS/reassociate가
확률에 전혀 영향을 주지 않는다는 증명은 아니지만, 적어도 뒤의 AR-only 재현에 필요한
조건은 아니었다.

### 20.3 AR-only 결과

유효 boot ID는 `9e783418-a12e-458c-948a-aafaf7216f33`이다. controller evidence에는
두 module load와 `040-antcfg-get`, `041-rate-adapt-get`만 존재했다. live 값은 antenna
Tx/Rx `0x101`, static success-rate threshold 70/90, 평가 주기 100 ms였다. MCS 설정과
reassociate는 없었고 연결 이벤트도 초기 1회뿐이었다. 시작 상태는 `tx failed=0`, ping
3/3, FW fault marker 0이었다.

| 항목 | 값 |
|---|---|
| 판정 | **REPRODUCED** |
| 장애 시작 | **scan 6 / 38초**, harness 종료 43초 |
| `tx failed` | 0 → 5 → 11 → 17 → 73 → **2,417** |
| ping 전후 counter | 2,441 → 5,058 |
| final ping | **0/3, rc 1** |
| 상태 | 전후 `COMPLETED`, 같은 BSSID/SSID/5180 MHz |
| external start/result | 6/6 |
| own scan / disconnect / FW fault | 0 / 0 / 0 |
| scan 소요 | 275~282 ms, 평균 278.5 ms, median 278 ms, p95 282 ms |

scan 5까지 counter는 73이었으나 scan 6 완료 직후 2,417로 급증해 positive stop이
작동했다. 이어진 ping은 100% 손실과 추가 counter 증가를 만들었다. 그런데 supplicant는
계속 `COMPLETED`, BSSID도 동일했고 disconnect/recovery marker가 없었다. 따라서 앞선
시험과 같은 scan-return TX/ACK wedge 서명이다.

### 20.4 판정과 다음 분리

**이번 gate에서 antcfg+rate만으로 재현에 충분했다.** 따라서 A(TX-power/thermal),
B(radio defaults), MCS 조작, reassociate, network/sysctl/ExecStartPost 동작은 이번
AR-only 재현의 필수조건이 아니다. 특히 MCS-only는 같은 최대 60회 gate를 통과했다.

다만 이번 시험은 `antcfg 0x0101`과 static rate-adapt 70/90/100 ms를 한 profile로 묶었기
때문에 다음 셋 중 어느 것인지는 아직 구분하지 못한다.

1. antcfg 단독
2. rate-adapt 단독
3. 두 설정의 상호작용

다음 최소 판별은 독립 clean boot의 antcfg-only와 rate-only다. 둘 다 음성이면 결합 AR을
반복해 상호작용과 재현성을 확인한다. 이번 단계에서는 AR-only가 계획된 positive stop
condition을 충족했으므로 추가 board run은 하지 않았다.

### 20.5 artifact와 제품 복구

```text
artifacts/moal-fw-c-internal-bisect-20260824/543p18-p149115/
  summary.txt
  config/
    MCS-only/AR-only 선택형 controller, frozen 설정, iw harness
  mcs-only-iw-run1/
    EVIDENCE_SHA256SUMS: 148 entries, 148/148 OK
    manifest SHA-256 d87d2bb81bc9d01c36cc587677217a232a7cf5744ed079274bd2de8f4a09a08e
  mcs-only-iw-run1.tar.gz
    SHA-256 2fd39a85567cdf1212498dd5b0df8b0228cee7439302fe054c39d754d773ca99
  ar-only-iw-run1/
    EVIDENCE_SHA256SUMS: 41 entries, 41/41 OK
    manifest SHA-256 7b6f40ccbe46fc544f3baf66def52da23e722b345cf313a2550fba8c24e49bef
  ar-only-iw-run1.tar.gz
    SHA-256 537e4c170282761b59bcfaedf4978176cadcff6806cd8fbbeec3f3b694912f8d
  EVIDENCE_SHA256SUMS
    211 entries, 211/211 OK
    SHA-256 89266be2ff5f7978437b5aaed81a7e75963a177d78f7d59366b98d9b54719aa1
```

제품 복구 boot ID는 `0fa36385-29dd-4db1-b13e-ed92717c03f6`이다. `wifi_init`,
`wlan_fw_watch`, supplicant와 제품 정책상 선택된 `wifi_roam`이 enabled/active이고,
`wifi_bgscan`은 상호배타 정책에 따라 enabled/inactive다. 같은 SSID/BSSID/5180/IP에서
`COMPLETED`, recovery ping 20/20과 cleanup 후 5/5가 성공했다. 두 확인 모두 전후
`tx failed=0`, fault marker 0이다. module/FW/mod_para/JSON/wpa-conf hash가 유지됐고,
보드의 controller/harness/run directory/tarball은 모두 삭제했다.

## 21. AR 내부 분리 — antcfg-only

### 21.1 목적과 profile 검증

§20의 AR-only에서 장애가 재현됐으므로 `antcfg`와 rate-adapt를 한 번에 하나씩 분리하기로
했다. 승인된 stop condition은 antcfg-only가 positive면 즉시 중단하고, negative일 때만
별도 clean boot에서 rate-only를 실행하는 것이다.

기존 controller를 직접 덮어쓰지 않고 새 artifact에 복사한 뒤 `ant-only`와 `rate-only`
profile contract를 먼저 작성했다. 기존 controller에 대한 contract test는 해당 profile이
없어 exit 1로 실패했고, 최소 분기를 추가한 새 controller는 contract와 `bash -n`을 모두
통과했다. 실제 board preflight가 최종 integration 검증이다.

유효 boot ID는 `eeb83302-67d6-4122-b1cf-050989d7d2c4`다. 안정 boot 확정 뒤 다음을
확인했다.

- `wifi_init`: disabled/inactive, 현재 boot journal 없음
- 자동 적재된 `mlan`/`moal` 없음, `mlan0` 없음
- controller/harness board hash와 local frozen hash 일치
- 제품과 동일한 543.p18 / 17.92.1.p149.115 및 direct moal 인자 사용

```text
insmod /opt/wlan/driver/mlan_imx93.ko
insmod /opt/wlan/driver/moal_imx93.ko \
  mod_para=cts/wifi_mod_para.conf \
  tx_work=0 \
  bridge_mode=1 \
  bridge_debug=0 \
  bridge_wlan_idx=0 \
  bridge_keepalive_ms=1 \
  bridge_keepalive_idle_ms=20 \
  bridge_local_hairpin=0 \
  wq_sched_policy=1 \
  wq_sched_prio=45
```

### 21.2 antcfg-only 사전조건

controller command directory에는 module load `001`/`002`와 `040-antcfg-get`만 있다.
`041-rate-adapt-get`, MCS 및 A/B 명령 파일은 없다.

| 항목 | 값 |
|---|---|
| 유일한 적용 설정 | `antcfg 0x0101` |
| antenna live GET | Tx `0x101`, Rx `0x101` |
| rate-adapt | **FW 기본값 유지**: noise 기반 dynamic, 100 ms |
| MCS / reassociate | FW 기본 capability / 미실행 |
| 연결 이벤트 | 초기 1회 |
| TX power | 8 dBm — A 미적용 확인 |
| 시작 상태 | `COMPLETED`, `tx failed=0`, ping 3/3, FW fault 0 |
| 서비스 | supplicant만 active, 나머지 scan/roam/logger/checker/event/fw-watch inactive |

따라서 §20 AR-only와 달리 static rate threshold 70/90은 적용되지 않았고, 제품
`antcfg`만 남은 one-variable 시험이다. network/sysctl/peer-route 및 ExecStartPost도
계속 제외했다.

### 21.3 `iw` 결과

```text
iw mlan0 scan freq 5180 5200 5220 5240 ssid LAB_SSID_REDACTED
scan 완료 뒤 5초 대기, 최대 60회
```

| 항목 | 값 |
|---|---|
| 판정 | **REPRODUCED** |
| 장애 시작 | **scan 5 / 33초**, harness 종료 37초 |
| `tx failed` | scan 4까지 0 → scan 5: **2,525** |
| ping 전후 counter | 2,549 → 5,169 |
| final ping | **0/3, rc 1** |
| 상태 | 전후 `COMPLETED`, 같은 BSSID/SSID/5180 MHz |
| external start/result | 5/5 |
| own scan / disconnect / FW fault | 0 / 0 / 0 |
| scan 소요 | 275~280 ms, 평균 276.8 ms, median 276 ms, p95 280 ms |

scan 4까지 counter가 0이었으나 scan 5 반환 직후 2,525로 급증했다. 이어진 ping은 100%
손실과 추가 counter 증가를 만들었지만 supplicant와 BSSID는 그대로였고 FW recovery나
disconnect marker는 없었다. 기존과 동일한 scan-return TX/ACK wedge 서명이다.

### 21.4 판정 범위

**이번 gate에서는 `antcfg 0x0101` 단독 적용으로 재현에 충분했다.** rate-adapt가 FW
기본 dynamic 상태였으므로 static 70/90/100 ms 설정은 이번 장애의 필수조건이 아니다.
MCS 조작, reassociate, A/B, wifi_init의 나머지 동작도 마찬가지다.

이 결과는 앞선 profile들과도 일관된다.

| profile | antenna | rate-adapt | 결과 |
|---|---|---|---|
| MCS-only (§20) | FW 기본 `0x303` | FW 기본 dynamic | 60/60 미재현 |
| AR-only (§20) | `0x101` | static 70/90/100 ms | scan 6 재현 |
| **antcfg-only (§21)** | **`0x101`** | **FW 기본 dynamic** | **scan 5 재현** |

따라서 `0x303`에서 `0x101`로 바꾸는 antenna 설정은 확인된 trigger/cofactor다. 다만
이것이 곧 `antcfg` 명령 구현 자체가 결함이라는 뜻은 아니다. 실제 결함 층은 이 antenna
path에서 노출되는 FW/driver의 off-channel scan 복귀와 TX data-path 상호작용일 수 있다.
또한 rate-only를 실행하지 않았으므로 rate 설정도 독립 trigger인지 여부는 판정하지
않았다. antcfg-only가 승인된 positive stop을 만족해 rate-only는 계획대로 생략했다.

### 21.5 artifact와 제품 복구

```text
artifacts/moal-fw-ar-internal-bisect-20260825/543p18-p149115/
  summary.txt
  config/
    ant-only/rate-only controller, profile contract test, frozen 설정, iw harness
    SHA256SUMS: 9 entries, 9/9 OK
  ant-only-iw-run1/
    EVIDENCE_SHA256SUMS: 39 entries, 39/39 OK
    manifest SHA-256 4d3e7db214c1b91a5a9dbb142951f99f152485a08262e9fc358b827a2729002a
  ant-only-iw-run1.tar.gz
    SHA-256 c0054f0beebbfff62fb959c3aa5f74a13127baf1aedb23dc55984f9495f214f5
  EVIDENCE_SHA256SUMS
    61 entries, 61/61 OK
    SHA-256 36509f7cbef6ddcbc7c95f57a341407103471db7a464cce71e167a8819361837
```

제품 복구 boot ID는 `89f39e00-214e-45ff-8192-caee3a42ae07`이다. `wifi_init`,
`wlan_fw_watch`, supplicant와 제품 정책상 선택된 `wifi_roam`은 enabled/active이고,
`wifi_bgscan`은 enabled/inactive다. TX power 20 dBm, 같은 SSID/BSSID/5180/IP에서
`COMPLETED`이며 recovery ping 20/20과 cleanup 후 5/5가 성공했다. 전후
`tx failed=0`, fault marker 0이고 원래 module/FW/config hash도 유지됐다. 보드 시험
파일은 모두 삭제했다.

## 22. antcfg Tx/Rx 방향 분리와 재사용 controller

### 22.1 목적과 공통 조건

§21에서 `antcfg 0x0101` 단독으로 재현됐으므로 물리 path 제한의 Tx와 Rx 방향을
분리했다. 최초 가설은 MCS tier로 Tx NSS를 제한할 수 있으므로 `antcfg`에는 Rx만 1SS를
적용하면 scan wedge를 피할 수 있는지 확인하는 것이었다.

시험마다 별도 clean boot를 사용했다. `wifi_init`은 disabled/inactive, 현재 boot의
서비스 journal은 비어 있고 `mlan`/`moal`과 `mlan0`이 없는 것을 확인했다. driver/FW와
module 인자는 §20~21과 같고, A/B·rate·MCS 설정은 모두 실행하지 않았다. 연결 뒤에는
supplicant만 active였고 TX power는 8 dBm이었다.

이번부터 artifact 전용 controller를 다음 재사용 가능한 repository QA 도구로 정리했다.

```text
scripts/qa/moal_fw_scan_profile_controller.sh
scripts/qa/moal_fw_scan_profile.example.conf
scripts/qa/test_moal_fw_scan_profile_controller.py
scripts/qa/iw_external_scan_datapath_repro.sh
scripts/qa/wpa_cli_scan_only_datapath_repro.sh
scripts/qa/product_wifi_bgscan_soak.sh
scripts/qa/test_scan_qa_safety.py
```

revision별 trusted run-config가 module/FW/config 경로, 정확한 module argument array,
expected SHA-256과 `APPLY_*` toggle을 가진다. `ANT_TX`와 `ANT_RX`는 독립 입력이며 SET
직후 GET을 수치 비교한다. 따라서 명령이 exit 0이어도 FW의 유효값이 다르면 scan 전에
중단한다. 보드 시험에 동결된 controller/harness는 각각
`ac093537...`/`77266b57...`이고 당시 contract test 5/5, `bash -n`, `shellcheck -x`,
config manifest 검증을 통과했다.

보드 시험 뒤 safety review에서 repository entrypoint에는 `--ack-disruptive`, config
owner/type/mode 검사, 필수 hash pin 검사와 `--validate-profile`을 추가했다. 최종 host
contract는 controller 12/12와 공통 safety 14/14, `bash -n`/`shellcheck`를 통과했다.
현재 controller/`iw`/`wpa_cli`/제품 soak SHA-256은 각각
`df63dd2a...`/`39e828fc...`/`449de8e6...`/`70afc3cb...`다. 제품 연결 상태에서
비파괴 board smoke로 `--describe`, `--validate-profile`, 승인 없는 harness의 artifact
생성 전 exit 2를 확인했고, 이후에도 `COMPLETED`, ping 3/3, `tx failed=0`이었다. core
destructive 동작의 board evidence는 위 동결본 hash에 귀속한다. 자세한 재사용 절차는
`docs/moal_fw_scan_profile_qa.md`에 기록했다.

### 22.2 Rx-only 요청의 표준 antenna GET mismatch

첫 유효 boot ID는 `e2a9be62-8405-47bd-afa0-3e647c482164`이다. 요청과 즉시 GET은
다음과 같았다.

```text
command=/usr/local/bin/mlanutl mlan0 antcfg 0x303 0x101
exit=0

Mode of Tx path is 0x303
Mode of Rx path is 0x303
```

당시 controller는 표준 antenna GET만 검증했으므로 Rx mismatch로 exit 1을 반환했고,
이 profile에서는 **`iw` scan을 한 번도 실행하지 않았다**. module load와
`040-antcfg-set`, `041-antcfg-get` 네 command file만 존재한다. 후속 §23의 호환성
행렬에서 이 GET은 F/W physical antenna mode만 나타내며 host NSS intent와는 다른
상태라는 점이 확인됐다.

CLI 인자 유실 여부는 ioctl capture shim으로 별도 확인했다. 제품 utility와 matching
source tree의 utility가 모두 같은 문자열을 만들었다.

```text
board-split capture=MRVL_CMDantcfg0x303 0x101
source-split capture=MRVL_CMDantcfg0x303 0x101
```

matching driver source는 commit
`26400d66cc56e9af0096273b5d25d31d3e001fa6`이며 tree는 clean이었다. 다음 경로도 두
인자를 분리 처리한다.

- `mapp/mlanutl/mlanutl.c:20991-21006`: 두 인자 form 허용 및 직렬화
- `mlinux/moal_eth_ioctl.c:15377-15405`: 두 번째 값을 `rx_antenna`에 저장
- `mlan/mlan_cmdevt.c:8517-8525`: Tx/Rx를 별도 HostCmd field로 생성

따라서 단순한 SET 문자열 parser 문제는 배제됐다. 다만 §23 결과에 따라 이 시점의
판정 범위는 다음처럼 정정한다. F/W physical antenna mode는 Tx/Rx `0x303/0x303`으로
반환됐지만 host `user_htstream`은 Rx 1SS/Tx 2SS인 `0x2121`이었다. 즉 “전체 유효 상태가
2Tx/2Rx로 정상화됐다”가 아니라 **physical antenna mode와 host advertised NSS가
분리됐다**가 정확하다.

### 22.3 반대 비대칭 Tx 1SS / Rx 2SS

지원 여부가 문서 예제에도 명시된 반대 방향 `antcfg 0x101 0x303`을 별도 clean boot에서
한 단계씩 확인했다. 유효 boot ID는
`00447841-32ca-4fee-9a82-6e24d7664ba9`이다.

controller command directory에는 다음 네 파일만 있었다.

```text
001-insmod-mlan.txt
002-insmod-moal.txt
040-antcfg-set.txt
041-antcfg-get.txt
```

사전조건은 다음과 같다.

| 항목 | 값 |
|---|---|
| 요청 / live GET | Tx `0x101`, Rx `0x303` / 동일 |
| rate-adapt | FW 기본 noise 기반 dynamic, 100 ms |
| MCS / reassociate | FW 기본 capability / 미실행 |
| 연결 이벤트 | 초기 1회 |
| TX power | 8 dBm |
| 시작 상태 | `COMPLETED`, `tx failed=0`, ping 3/3, FW fault 0 |

동일한 `iw` gate 결과는 다음과 같다. 마지막 인자 `60`은 60초가 아니라 최대 scan
60회이며 실제 전체 경과는 330초였다.

| 항목 | 값 |
|---|---|
| 판정 | **NOT_REPRODUCED** |
| 완료 | **60/60**, 총 330초 |
| `tx failed` | 0 → final-ping 전 2 → 후 2 |
| ping | 시작 3/3, 종료 **3/3** |
| 상태 | 전후 `COMPLETED`, 같은 BSSID/SSID/5180 MHz |
| external start/result | 60/60 |
| own scan / disconnect / FW fault | 0 / 0 / 0 |
| scan 소요 | 272~303 ms, 평균 275.1 ms, median 274 ms, p95 279 ms |

시험 뒤에도 live GET은 Tx `0x101`, Rx `0x303`이었고 rate는 dynamic으로 유지됐다.

### 22.4 판정 범위

현재 방향 분리 결과는 다음과 같다.

| profile | Tx | Rx | 결과 |
|---|---:|---:|---|
| FW 기본 / MCS-only | `0x303` | `0x303` | 60/60 미재현 |
| Rx-only 요청 | physical `0x303` | physical `0x303`, host NSS Rx 1SS | scan 미실행 |
| Tx 제한 | `0x101` | `0x303` | 60/60 미재현 |
| antcfg-only (§21) | `0x101` | `0x101` | scan 5 재현 |

따라서 **Tx path의 1SS 제한 단독은 이번 bounded gate의 충분조건이 아니다.** 반면
Tx/Rx 동시 1SS에서는 재현된다. §23에서 `0x303/0x101` 요청이 host NSS intent
`0x2121`을 만든다는 점은 확인했지만 이 상태에서는 scan을 실행하지 않았다. 그러므로
Rx NSS 제한 단독 또는 Tx/Rx 동시 제한의 상호작용은 여전히 구분되지 않았고, 이
결과만으로 Rx가 원인이라고 확정해서는 안 된다.

### 22.5 artifact와 제품 복구

```text
artifacts/moal-ant-direction-bisect-20260825/543p18-p149115/
  config/
    reusable controller, two frozen run-configs, contract test, iw harness
    SHA256SUMS: 13 entries, 13/13 OK
    manifest SHA-256 e795eea8902436336776d14d74c8bd723551c56d455411e8dec6c580e657e343
  runs/ant-rx-only-iw-run1/
    SHA256SUMS: 12 entries, 12/12 OK
    manifest SHA-256 b24f69ed97620981f4cf16f97c9bea2859306c7a8a94939c2ba0a820ced34d45
  runs/ant-rx-only-iw-run1-controller-abort.tar.gz
    SHA-256 b7487aad53ed20da37bf60814f064d916acceae5ded3f26340d5dbfa9ee49ba8
  runs/ant-tx-restricted-iw-run1/
    SHA256SUMS: 150 entries, 150/150 OK
    manifest SHA-256 3d07ffd0b915d42e5e0dcd59796cfd083e49790b69c8871e2de9355aca679c0e
  runs/ant-tx-restricted-iw-run1.tar.gz
    SHA-256 5fd4348740553e7c92d27f1f2f52b0e09f3eb6586e1d0535e62ef383dc606c3d
  EVIDENCE_SHA256SUMS
    254 entries, 254/254 OK
    SHA-256 1e15428c07401e4349c6f7caf93bd59cb34096a6fcb5b4bc7231851635eee9e6
```

최종 제품 복구 boot ID는 `1b32cf21-1e0c-4e5e-933f-76bfc8219715`다. 원래
module/FW/config/utility hash를 유지했고 `wifi_init`, `wlan_fw_watch`, supplicant와 제품
정책의 `wifi_roam`이 active다. 제품값 Tx/Rx `0x101`, TX power 20 dBm, 같은
SSID/BSSID/5180/IP에서 `COMPLETED`, `tx failed=0`, fault marker 0을 확인했다. 복구 직후
20/20 ping은 성공했다. 원격 파일 삭제 직후의 짧은 5회 확인에서는 2/5 응답의 일시 손실이
있었으나, 즉시 이어진 20회 재검증은 20/20이고 전후 `tx failed=0`, FW fault 0이었다.
uptime 486초의 최종 gate도 10/10, `tx failed=0`, fault 0이었다. 원격 시험 directory,
tarball, probe 파일은 모두 삭제했다.

## 23. `antcfg 0x303 0x101` driver/F/W SET/GET 호환성 행렬

### 23.1 무스캔 비교 결과

main/505+p149.81, main/505+p149.115, ported/543+p149.115를 각각 clean boot에서
비교했다. supplicant와 모든 scan 관련 서비스를 중지하고 `mlan0`을 down으로 유지한
채 SET 한 번과 즉시 GET 한 번만 실행했다. 세 유효 행 모두 scan command, association,
FW fault가 0이었다.

| 조합 | physical antenna GET | host NSS intent |
|---|---|---|
| main/505 + p149.81 | Tx/Rx `0x303/0x303` | `0x2121`, 2G/5G Rx 1SS/Tx 2SS |
| main/505 + p149.115 | Tx/Rx `0x303/0x303` | `0x2121`, 2G/5G Rx 1SS/Tx 2SS |
| ported/543 + p149.115 | Tx/Rx `0x303/0x303` | `0x2121`, 2G/5G Rx 1SS/Tx 2SS |

505 driver를 고정한 두 F/W가 동일하므로 이 SET/GET 범위에서 F/W 세대별 비대칭
정책 차이는 관찰되지 않았다. p149.115를 고정한 505/543도 matching utility 기준 유효
상태가 동일하다.

### 23.2 확인된 driver 차이는 조회 ABI

main/505는 기존 `antcfg` GET을 16바이트로 확장해 Tx, Rx, `user_htstream`, reserved를
함께 반환한다. ported/543은 기존 `antcfg` ABI를 유지하고 host NSS를 별도
`antcfgnss` private command로 반환한다. 제품 utility는 main 방식만 알고 있어
ported/543 최초 행에서는 NSS 진단이 보이지 않았다. ported commit
`26400d66cc56e9af0096273b5d25d31d3e001fa6`의 matching utility로 교정 실행하자
동일한 `0x2121`이 확인됐다.

따라서 §22의 “2Tx/1Rx 상태를 만들 수 없다”는 표현은 정정한다. F/W physical Rx
antenna mode는 2-path로 남지만, host가 association capability에 사용하는 Rx NSS는
1SS로 제한된다. 이전 controller는 첫 값만 검증했기 때문에 안전하게 scan 전에
중단했지만 host NSS 상태를 보지 못했다. 이 상태에서 scan을 실행하지 않았으므로 Rx NSS
제한 단독의 wedge 영향은 아직 미검증이다.

상세 보고서는
`/home/jhw/ai/opencode/projects/wlan-package/.worktrees/wpa-roam-scan-policy/docs/antcfg_set_get_compat_matrix_2026-08-25.md`,
원시 증거는
`/home/jhw/ai/opencode/projects/wlan-package/.worktrees/wpa-roam-scan-policy/artifacts/moal-antcfg-compat-matrix-20260825`에
보관한다.

### 23.3 최종 제품 복구

최종 제품 boot ID는 `024e324c-5bb8-4869-9d17-ed8b3dd6ff5b`다. active driver/F/W는
원래 ported/543+p149.115 hash와 일치하고 제품 `antcfg`는 `0x101/0x101`이다.
supplicant는 `COMPLETED`, station `tx failed=0`, cleanup 전 ping 10/10과 cleanup 후
5/5, FW/kernel fault 0을 확인했다. 이후 uptime 약 508초의 추가 live gate는 4/5,
연속 재검증은 19/20과 29/30으로 각 한 패킷 손실이 있었다. 전후 같은 BSSID와
`COMPLETED`, `tx failed=0`, fault 0을 유지해 기존 wedge 서명은 아니지만 최종 무손실
ping gate는 반복 만족하지 못했다. 원격 시험 controller, run, tarball과 임시 backup은
모두 삭제했다.
