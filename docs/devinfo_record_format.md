# 출하 고정값 서명 레코드 형식 (v1)

장치마다 출하 때 한 번 기록하는 고정값(Vendor·Product Code, 시리얼, 제조·출하일, IEEE 802.11r/ai/k/v 지원 여부)을 eMMC boot1 에 서명 레코드로 둔다(#481). 장치는 부팅 때 레코드를 검증해 opcd 와 SNMP 가 읽는 `/usr/local/opc/etc/device_info.json` 을 만든다. 이 문서는 레코드 바이트 배치와 판정 규칙의 정본이고, 구현은 `dist/wlan/usr/local/scripts/devinfo_record.py`, 시험은 `dist/wlan/usr/local/scripts/tests/test_devinfo_record.py` 다.

## 위치

- eMMC boot1 하드웨어 파티션(i.MX93 보드는 `/dev/mmcblk0boot1`, 4 MiB)의 오프셋 0 부터 512 바이트.
- boot0(부트로더)은 쓰지 않는다. boot1 은 출하 이미지가 쓰지 않는 영역이고 `uuu` 재기록 뒤에도 남는다(2026-11-10 보드 실측).

## 바이트 배치 (v1, 512 B, 리틀엔디언)

| 오프셋 | 크기 | 필드 | 값 |
|---:|---:|---|---|
| 0 | 8 | magic | ASCII `CTSDINFO` |
| 8 | 2 | format_version | `1` |
| 10 | 1 | key_id | 서명 공개키 번호 `1`~`255` (`0` 은 쓰지 않는다) |
| 11 | 1 | uid_source | UID 를 읽는 곳 (아래 표) |
| 12 | 1 | uid_len | UID 바이트 수 `1`~`16` |
| 13 | 3 | reserved | 모두 `0` |
| 16 | 16 | uid | UID 원시 바이트, `uid_len` 뒤는 `0` |
| 32 | 4 | vendor_code | u32 |
| 36 | 2 | product_code | u16 |
| 38 | 2 | product_subcode | u16 |
| 40 | 1 | ieee_11r | `0` 또는 `1` |
| 41 | 1 | ieee_11ai | `0` 또는 `1` |
| 42 | 1 | ieee_11k | `0` 또는 `1` |
| 43 | 1 | ieee_11v | `0` 또는 `1` |
| 44 | 4 | manufacture_date | 연 u16, 월 u8, 일 u8 (1970~9999 년의 실제 날짜) |
| 48 | 4 | shipment_date | 연 u16, 월 u8, 일 u8 (1970~9999 년의 실제 날짜) |
| 52 | 32 | hardware_version | 출력 가능한 ASCII 에서 `"`·`\` 를 뺀 문자 최대 31 자, 나머지 `0` |
| 84 | 32 | serial_number | 출력 가능한 ASCII 에서 `"`·`\` 를 뺀 문자 최대 31 자, 나머지 `0` |
| 116 | 332 | reserved | 모두 `0` |
| 448 | 64 | signature | 오프셋 0~447 에 대한 Ed25519 서명 |

- 예약 바이트와 패딩도 서명 범위에 들어간다. 나중 버전이 예약 영역에 필드를 더할 수 있도록, v1 은 예약 바이트가 0 이 아니면 무효로 본다.
- 문자열 필드는 첫 `0` 뒤가 모두 `0` 이어야 한다. 31 자 제한은 opcd 의 32 바이트 C 문자열(`OPC_VERSION_FIELD_LEN`, `OPC_SERIAL_FIELD_LEN`)에 맞춘 것이다.
- `"`·`\` 와 1970 년 이전 날짜를 받지 않는 것은 opcd 가 읽는 범위에 맞춘 것이다. opcd 의 JSON 문자열 읽기(`wlan-opc/opcd/json_util.c` `copy_quoted_value`)는 첫 `"` 에서 멈추고 이스케이프를 풀지 않으며, 날짜 읽기(`inventory.c` `parse_date`)는 1970~9999 년만 받는다. 시험은 opcd 로더를 실제로 컴파일해, v1 이 허용하는 모든 문자와 날짜 경계가 그대로 읽히는지 확인한다.
- 날짜와 필드 폭은 opcd `inventory.h` 와 같다(`vendor_code` 32비트, `product_code`·`product_subcode` 16비트, IEEE 플래그 8비트, `opc_date_t`).

### uid_source

| 값 | SoC | 읽는 곳 |
|---:|---|---|
| `1` | i.MX93 | nvmem `fsb_s400_fuse0` 의 오프셋 192 부터 16 바이트(퓨즈 워드 48~51) |

sysfs `/sys/devices/soc0/serial_number` 는 이 가운데 두 워드만 이은 표현이라(i.MX93 보드 실측) 정본으로 쓰지 않는다. 다른 SoC 는 새 값을 더해 지원한다. 레코드를 만드는 쪽은 이 표에 있는 uid_source 와 그 길이(i.MX93 은 16 바이트)만 쓴다. 장치는 등록된 출처에서 정해진 길이만 읽으므로 다른 조합은 어느 장치에서도 UID 불일치가 된다.

## 판정 순서

장치는 boot1 의 앞 512 바이트를 읽어 다음 순서로 판정한다. 앞 단계에서 결론이 나면 멈춘다.

1. 512 바이트를 읽지 못하면(읽기 오류, 짧게 읽힘) **판정 불가**다. 기존 `device_info.json` 을 바꾸지 않는다.
2. magic 이 `CTSDINFO` 가 아니면 **없음**이다(빈 영역 `0x00`·`0xFF` 포함).
3. format_version 이 `1` 이 아니면 **무효**다.
4. key_id 가 장치의 공개키 목록에 없으면 **모르는 키**다. 무효와 로그를 구분한다(키 교체 뒤 패키지가 늦은 경우를 가려내기 위해서다).
5. 서명이 맞지 않으면 **무효**다.
6. 예약 바이트, uid_len·uid 패딩, IEEE 플래그, 날짜(1970~9999 년), 문자열 구조(`"`·`\` 금지 포함)가 규칙에 어긋나면 **무효**다.
7. uid_source 와 uid 가 장치에서 읽은 값과 다르면 **무효**다. 로그에 양쪽 값을 남긴다.
8. 여기까지 통과하면 **유효**다.

검증 라이브러리(`cryptography`)를 불러오지 못하는 등 예외가 나면 **판정 불가**로 보고 파일을 바꾸지 않는다.

## 값 규칙 (사양서 R2 6.2·6.3절)

장치는 위 구조만 검사한다. 아래 값 규칙은 레코드를 만드는 양산 도구가 강제한다. 그래야 새 기종 코드가 생겨도 장치 패키지를 바꾸지 않아도 된다.

- vendor_code: `0x00902CFB`(IEEE 등록 OUI 90-2C-FB).
- product_code: 상위 바이트는 제품 분류(`0x01` = Bridge A type), 하위 바이트는 무선 규격(`0x01` = 802.11n, `0x02` = 802.11ac, `0x03` = 802.11ax).
- product_subcode: 대상 국가 코드 두 글자의 ASCII 값. KR `0x4B52`, JP `0x4A50`, CN `0x434E`, 전 세계 범용(WW) `0x5757`.
- ieee_11r/ai/k/v: `0x00` 없음, `0x01` 있음. 현행 기종은 `1`/`0`/`1`/`1`.

## device_info.json 으로 옮기는 규칙

| JSON 키 | 형식 |
|---|---|
| `vendor_code` | `"0x%08X"` |
| `product_code`, `product_subcode` | `"0x%04X"` |
| `hardware_version`, `serial_number` | 문자열 |
| `manufacture_date`, `shipment_date` | `"YYYY-MM-DD"` |
| `ieee_11r`, `ieee_11ai`, `ieee_11k`, `ieee_11v` | 정수 `0`/`1` |
| `source` | `"boot1-record"`(레코드에서 만든 파일이라는 표식) |

opcd(`wlan-opc/opcd/inventory.c`)와 SNMP(`wifi_snmp_pp.py`)는 v1 이 유효로 보는 값 범위에서 이 형식을 그대로 읽는다. `source` 키는 둘 다 무시한다.

## 부팅 검증 서비스

`wifi_devinfo.service`(oneshot, `dist/wlan/usr/local/scripts/wifi_devinfo.py`)가 부팅 때 opcd·snmpd 보다 먼저 판정하고, `device_info.json` 을 쓰는 곳은 이 서비스 하나다. 판정이 확정됐을 때만 파일을 바꾼다.

| 판정 | `device_info.json` | 종료 코드 |
|---|---|---:|
| 유효 | 레코드 값으로 쓴다(같은 디렉터리 임시 파일 → rename, 모드 0644). 디렉터리가 없으면 0755 로 만들고, 내용이 같으면 쓰지 않는다 | 0 |
| 없음 | `source` 표식이 있는 파일만 지운다. 표식 없는 수동 파일은 그대로 두고 "unsigned 수동 파일 사용" 로그를 남긴다 | 0 |
| 무효·모르는 키 | 없음과 같이 처리하고, 로그에 판정과 이유(키 번호, 양쪽 UID 등)를 남긴다 | 1 |
| 판정 불가 | 바꾸지 않는다(읽기 오류, UID 를 읽지 못함, 검증 라이브러리 없음, 그 밖의 예외) | 1 |

UID 는 판정 7단계(UID 비교)에 이르렀을 때만 읽는다. 그래서 UID 를 읽지 못하는 장치도 없음·무효·모르는 키는 그대로 판정하고, 서명과 구조가 맞는 레코드만 판정 불가가 된다. 설치·업그레이드 때는 postinst 가 opcd 를 띄우거나 재시작하기 전에 같은 스크립트를 실행한다.

## 키 관리

- 서명 개인키는 사내에서만 보관하고 저장소와 장치에 두지 않는다. 장치 패키지에는 key_id 별 공개키만 둔다(`wifi_devinfo.py` 의 `SHIPPING_PUBLIC_KEYS`, 현재 key_id `1`).
- 공개키 목록은 추가만 한다. 이미 쓴 key_id 를 다른 키에 다시 쓰지 않는다.
- 개인키가 유출되면 해당 key_id 를 패키지에서 빼야 하고, 그 키로 서명한 장치는 모두 새 키로 다시 기록해야 한다.
- 시험은 시험 전용 키(고정 시드)를 쓴다. 이 키는 장치 패키지의 공개키 목록에 넣지 않는다.
