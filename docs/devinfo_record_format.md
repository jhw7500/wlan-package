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

`wifi_devinfo.service`(oneshot, `dist/wlan/usr/local/scripts/wifi_devinfo.py`)가 부팅 때 opcd·snmpd 보다 먼저 판정한다. `device_info.json` 은 이 스크립트의 판정(`run()`)을 거쳐서만, `/run/wifi_devinfo.lock` 을 쥔 채 쓴다. 이 판정을 부르는 곳은 부팅 서비스, 설치·업그레이드 때의 postinst, 기록 도구(`wifi_devinfo_write.py write`)다. 판정이 확정됐을 때만 파일을 바꾼다.

| 판정 | `device_info.json` | 종료 코드 |
|---|---|---:|
| 유효 | 레코드 값으로 쓴다(같은 디렉터리 임시 파일 → rename, 모드 0644). 디렉터리가 없으면 0755 로 만들고, 내용이 같으면 쓰지 않는다 | 0 |
| 없음 | `source` 표식이 있는 파일만 지운다. 표식 없는 수동 파일은 그대로 두고 "unsigned 수동 파일 사용" 로그를 남긴다 | 0 |
| 무효·모르는 키 | 없음과 같이 처리하고, 로그에 판정과 이유(키 번호, 양쪽 UID 등)를 남긴다 | 1 |
| 판정 불가 | 바꾸지 않는다(읽기 오류, UID 를 읽지 못함, 검증 라이브러리 없음, 그 밖의 예외) | 1 |

서비스는 기록 도구와 같은 잠금(`/run/wifi_devinfo.lock`)을 쥔 채 판정한다. UID 는 판정 7단계(UID 비교)에 이르렀을 때만 읽는다. 그래서 UID 를 읽지 못하는 장치도 없음·무효·모르는 키는 그대로 판정하고, 서명과 구조가 맞는 레코드만 판정 불가가 된다. 설치·업그레이드 때는 postinst 가 opcd 를 띄우거나 재시작하기 전에 같은 스크립트를 실행한다.

## 기록 도구

`dist/wlan/usr/local/scripts/wifi_devinfo_write.py`(root)는 양산 PC 도구가 SSH 로 부른다.

- `uid`: `uid_source=<N> uid=<32자리 소문자 hex>` 한 줄을 출력한다. 부팅 서비스와 같은 UID 출처를 읽는다.
- `write <레코드 파일> [--replace]`: 부팅 서비스와 같은 잠금을 잡고 아래 순서로 진행한다. 1~4 단계에서 멈추면 boot1 과 `force_ro` 를 건드리지 않는다.
  1. 레코드가 이 장치의 공개키·UID 로 유효한지 본다.
  2. 루트FS 디스크가 `mmcblk0` 인지 본다.
  3. `mmc extcsd read /dev/mmcblk0` 의 `PARTITION_CONFIG` 가 정확히 한 번 나오고, 부트 파티션 값(bits 5:3)이 1 인지 본다. 1 은 Boot Partition 1, 곧 `mmcblk0boot0` 이다.
  4. 블록 캐시를 비운 뒤 boot1 의 앞 512 B 를 읽는다. 앞 실행의 실패한 쓰기가 캐시에만 남아 있어도 매체를 기준으로 판단하기 위해서다.
     - 전부 0x00 이거나 전부 0xFF 면 쓴다.
     - 같은 레코드면 쓰지 않는다.
     - magic 이 일치하는 다른 레코드면(판정 결과와 무관) `--replace` 가 있어야 쓰고, 이전 512 B 를 `previous=<hex>` 로 출력한다. 키를 바꾼 뒤 다시 기록하는 경우가 여기에 해당한다.
     - 그 밖이면 거부한다.
  5. `force_ro` 를 0 으로 바꾼다 → O_SYNC 로 쓴다 → 블록 캐시를 비운다(BLKFLSBUF) → 되읽어 비교한다 → `force_ro` 를 1 로 되돌린다. 이 구간에는 SIGHUP·SIGTERM 을 막아 두었다가 되돌린 뒤에 처리하고, 중간에 실패해도 1 로 되돌린다. 쓰기를 건너뛴 경우에도, 앞 실행이 남긴 `force_ro=0` 이 있으면 1 로 되돌린다.
  6. 쓰기를 건너뛰었어도 서비스와 같은 판정으로 `device_info.json` 에 반영하고, 파일이 레코드 값과 같은지 확인한다. 잠금을 푼 뒤 opcd 가 active 면 try-restart 한다(잠금을 쥔 채 재시작하면 `Before=opcd.service` 순서 때문에 서비스 시작 작업과 서로 기다릴 수 있다). 끝에 `result=written` 또는 `result=unchanged` 를 출력한다. 출력이 실패해도(SSH 단절) 기록과 반영은 끝까지 한다.

| 종료 코드 | 이름 | 뜻 |
|---:|---|---|
| 0 | `EXIT_OK` | 기록했거나 같은 레코드라 그대로 두었고, 반영까지 끝났다 |
| 2 | `EXIT_USAGE` | 인자가 틀렸거나 레코드 파일을 읽지 못했다 |
| 3 | `EXIT_RECORD_INVALID` | 레코드가 512 B 가 아니거나 이 장치에서 유효하지 않다(키·서명·UID·구조) |
| 4 | `EXIT_TARGET_REFUSED` | 대상이 맞지 않다(루트FS 디스크, PARTITION_CONFIG, boot1 의 알 수 없는 내용) |
| 5 | `EXIT_REPLACE_REQUIRED` | boot1 에 다른 레코드가 있다 |
| 6 | `EXIT_WRITE_FAILED` | 쓰기, 되읽기, `force_ro` 복원 가운데 하나가 실패했다 |
| 7 | `EXIT_APPLY_FAILED` | boot1 기록은 끝났지만 `device_info.json` 반영이나 opcd 재시작이 실패했다 |
| 8 | `EXIT_BUSY` | 다른 기록이나 판정이 잠금을 쥐고 있다 |
| 9 | `EXIT_UNDETERMINED` | 확인할 수 없다(UID·EXT_CSD·boot1·루트FS 장치를 읽지 못함, 잠금 파일을 못 씀, `mmc`·`cryptography` 없음) |

SIGHUP 이나 SIGTERM 으로 멈추면 128 + 시그널 번호로 끝나고, 이때도 `force_ro` 를 1 로 되돌린다. `python3-cryptography` 와 `mmc-utils` 는 패키지 Recommends 다. 없으면 서비스는 "판정 불가"로 두고, 기록 도구는 9 로 끝난다.

레코드가 아닌 내용이 boot1 앞에 남으면(찢어진 쓰기 등) 도구는 거부한다. 이때는 앞 512 B 를 백업한 뒤 손으로 비우고 다시 기록한다. 비우는 순서는 `echo 0 > /sys/block/mmcblk0boot1/force_ro`, `dd if=/dev/zero of=/dev/mmcblk0boot1 bs=512 count=1 conv=fsync`, `echo 1 > /sys/block/mmcblk0boot1/force_ro` 다.

## 양산 발급 도구

`scripts/factory/devinfo_issue.py` 는 양산 PC(Windows·Linux)에서 장치 하나에 레코드를 발급한다. 패키지에는 들어가지 않는다. Python 3, `cryptography`, OpenSSH 클라이언트(`ssh`)가 필요하다. 도구는 같은 저장소 트리의 `dist/wlan/usr/local/scripts/devinfo_record.py` 를 상대 경로로 읽으므로, 저장소 체크아웃이나 소스 아카이브를 풀어 둔 상태로 쓴다.

```
python3 scripts/factory/devinfo_issue.py --host <장치 주소> --key <개인키 PEM> --ledger <원장 CSV> \
    --serial <시리얼> --hardware-version <HW 버전> --product-code 0x0103 --subcode KR \
    --manufacture-date <YYYY-MM-DD> --shipment-date <YYYY-MM-DD>
```

한 번 실행하면 장치 하나를 아래 순서로 처리한다. 장치에는 세 번 연결한다.

1. 입력값을 "값 규칙"과 레코드 형식(문자열 길이·문자, 날짜)으로 검사한다. 시리얼, 하드웨어 버전, 제품 코드, 국가 코드, 두 날짜에는 기본값이 없다. 시리얼과 하드웨어 버전은 비어 있거나 앞뒤에 공백이 있으면 거부한다(배치 파일의 빈 변수 등). `--ieee` 의 기본값은 현행 기종의 `1,0,1,1` 이다. `--subcode` 는 `KR`·`JP`·`CN`·`WW` 또는 16진 값을 받는다.
2. 개인키(PKCS8 PEM)를 읽는다. 키에 암호가 걸려 있으면 묻는다. 이 키의 공개키로 장치가 믿는 공개키 목록(`devinfo_record.py` 의 `SHIPPING_PUBLIC_KEYS`)에서 key_id 를 찾는다.
3. 입력값을 보여 주고 확인을 받는다. `--yes` 를 주면 건너뛴다.
4. 원장 옆에 `<원장>.lock` 을 만든다. 파일이 이미 있으면 멈추므로, 두 발급이 한 원장에 동시에 쓰지 않는다. 원장을 덧붙이기로 열 수 있는지도 이때 확인한다.
5. [SSH 1] `wifi_devinfo_write.py uid` 로 UID 를 받는다. 레코드에 서명하고, 장치와 같은 판정으로 유효한지 스스로 확인한다. 원장의 중복을 검사한 뒤 `signed` 행을 적는다.
6. [SSH 2] 레코드를 표준입력으로 보내 `wifi_devinfo_write.py write /dev/stdin` 을 부른다(`--replace` 를 주면 그대로 전한다). 결과는 `result` 행에 적는다.
7. [SSH 3] 고정 대조 스크립트를 `python3 -` 로 보낸다.
   - `device_info.json` 이 레코드 값과 같은지 본다.
   - `vhlctl login`(기본 비밀번호) → `device-info` 로 opcd 의 GetDeviceInfo 응답(스냅샷 `/dev/shm/opcd/device_info.json`)을 받아 11 필드와 result 0 을 본다.
   - opcd 가 `inactive`·`failed` 였으면 잠시 띄웠다가 멈춘다. `activating` 처럼 바뀌는 중이면 건드리지 않고 대조 실패(`opcd_state`)로 둔다.
   - 띄웠던 opcd 는 예외나 SIGHUP·SIGTERM(SSH 단절) 때도 멈춘다. 멈추지 못했으면 아무것도 지우지 않고 대조 실패(`opcd_restore`)로 둔다.
   - 멈춘 뒤에는 opcd 가 시작하며 만드는 `/usr/local/opc/etc/temp`·`/home/admin/iplist.txt`·`/usr/local/opc/etc/iplist.user.last`(고객 파일과 한 쌍으로 쓰는 기록) 가운데 전에 없던 것만 지운다. 그 밖에 새로 생긴 이름은 지우지 않고 결과(`left`)에 남기며, 도구가 로그로 보여 준다. 예를 들어 opcd 가 받아들이지 않은 `iplist.txt` 를 보관하는 `iplist.txt.rejected` 가 그렇다.
   - `/usr/local/opc/etc/password` 가 있으면 기본 비밀번호로 로그인할 수 없으므로 대조 실패로 본다.
   - 결과는 `verify` 행에 적는다.

SSH 사용 방식:

- 원격 명령은 위의 고정 문자열 셋뿐이다. 입력값은 명령에 넣지 않고 서명된 레코드로만 보낸다.
- 호스트 키는 실행마다 새로 만든 임시 known_hosts 에 받는다(`StrictHostKeyChecking=accept-new`). 양산에서는 같은 주소에 장치가 계속 바뀌어, 사용자 known_hosts 를 쓰면 두 번째 장치부터 막히기 때문이다. 시스템 known_hosts 도 보지 않는다(`GlobalKnownHostsFile` 을 같은 임시 디렉터리의 없는 파일로 둔다). 한 실행의 세 연결은 같은 호스트 키여야 한다.
- 그래서 도구는 각 실행의 첫 연결에 응답한 호스트를 장치로 믿는다. 발급은 PC 와 장치만 있는 격리된 유선 구간에서 한다. 같은 망에서 장치 주소로 응답하는 다른 호스트가 있으면, 그 호스트가 비밀번호를 받고, 자기가 고른 UID 의 유효 레코드도 받는다.
- 인증은 비밀번호나 `--identity <키 파일>` 이다. 비밀번호는 ssh 가 직접 묻는다. 양산에서는 비밀번호가 다른 호스트로 새지 않도록 `--identity` 를 권한다.
- 연결을 재사용하지 않으므로(Windows OpenSSH 에는 ControlMaster 가 없다) 비밀번호 인증이면 Windows·Linux 모두 장치마다 비밀번호를 세 번 묻는다. `--identity` 를 쓰면 묻지 않는다.

원장(CSV, UTF-8, 헤더 한 줄)은 덧붙이기만 하며, 행마다 fsync 한다.

- 중복 검사는 `--ledger` 로 준 원장 하나 안에서만 한다. 양산 PC 가 여럿이어도 원장은 하나를 함께 쓴다.
- 원장은 열어 보기만 하고 Excel 등에서 저장하지 않는다. 저장하면 시리얼의 앞자리 0 이 사라지거나 인코딩이 바뀔 수 있다. BOM 이 붙은 UTF-8 은 그대로 읽는다. 그 밖에 읽을 수 없거나, 열이 다르거나, `signed` 행의 시리얼·UID 열이 그 행 `record_hex` 의 내용과 다르면 아무 장치에도 연결하지 않고 3 으로 멈춘다.

- 행의 단계(`phase`)는 셋이다.
  - `signed`: 서명이 곧 발급이다.
  - `result`: 기록 도구의 종료 코드(`device_rc`), `result`, `previous_hex`. 장치가 돌려준 글자는 `result` 가 `written`·`unchanged` 이고 `previous_hex` 가 1024 자리 소문자 hex 일 때만 그대로 적는다. 그 밖은 `invalid` 로 적어, 응답한 호스트가 보낸 글자가 원장(Excel 로 여는 파일)에 들어가지 않게 한다.
  - `verify`: `ok`, `mismatch:<항목>` 또는 `error:rc <N>`.
- 열 순서: `time_utc, phase, serial, uid_source, uid, key_id, pubkey_sha256, vendor_code, product_code, product_subcode, ieee_11r, ieee_11ai, ieee_11k, ieee_11v, manufacture_date, shipment_date, hardware_version, record_hex, device_rc, result, previous_hex, verify, reissue_reason, tool_commit, operator, pc, host`.
- `signed` 행은 장치에 쓰기 전에 적는다. 서명한 레코드가 장치에 들어갔는데 원장에는 없는 상태를 만들지 않기 위해서다.
- Ed25519 서명은 같은 입력에 늘 같다. 그래서 같은 시리얼·UID·값으로 다시 실행하면 이어서 처리한다(`signed` 행을 다시 적지 않는다). 장치 기록은 끝났는데 PC 쪽이 멈췄을 때 이 방법으로 복구한다. 이어서 하기는 그 시리얼이나 UID 로 한 가장 최근 발급과 같을 때만이다. 재발급으로 대체된 옛 레코드로 되돌리는 것은 다시 `--reissue` 가 필요하다.
- 원장의 `signed` 행에 시리얼이나 UID 가운데 하나라도 이미 있는데 레코드가 다르면, `--reissue --reason <사유>` 가 있어야 발급한다. 장치 boot1 에 다른 레코드가 있으면 `--replace` 도 준다(없으면 기록 도구가 5 로 거부한다).

| 종료 코드 | 이름 | 뜻 |
|---:|---|---|
| 0 | `EXIT_OK` | 기록(또는 같은 레코드 확인)과 대조까지 끝났다 |
| 2 | `EXIT_INPUT` | 입력값이 값 규칙이나 레코드 형식에 맞지 않거나, 임시 디렉터리 경로에 공백이나 `%` 가 있다. 필수 인자가 빠진 경우와 `--reissue` 에 `--reason` 이 없는 경우도 argparse 가 2 로 끝낸다 |
| 3 | `EXIT_LEDGER` | 원장이나 잠금 파일을 쓸 수 없거나, 잠금 파일이 이미 있거나, 원장을 읽을 수 없거나 내용이 맞지 않거나, `--reissue` 없이 이미 쓴 시리얼·UID 로 다른 레코드를 만들려 했다 |
| 4 | `EXIT_KEY` | 개인키를 읽지 못했거나, 장치가 믿는 공개키의 짝이 아니거나, 만든 레코드가 스스로 판정에서 유효하지 않다 |
| 5 | `EXIT_DEVICE` | ssh 실행·연결, UID 읽기, 장치 기록 가운데 하나가 실패했다. 기록 도구의 종료 코드는 원장의 `device_rc` 에 남는다 |
| 6 | `EXIT_VERIFY` | `device_info.json` 이나 GetDeviceInfo 응답이 레코드 값과 다르거나, 대조하지 못했다 |
| 7 | `EXIT_ABORTED` | 확인 질문에 아니라고 답했다 |

Windows 에서 쓸 때:

- Windows 10·11 의 기본 OpenSSH 클라이언트(`ssh.exe`)를 쓴다. PATH 에 없으면 `--ssh` 로 경로를 준다.
- 임시 디렉터리 경로에 공백이나 `%` 가 있으면 멈춘다. ssh 의 `-o` 값이 공백에서 잘리고 `%` 가 토큰으로 펼쳐지기 때문이다. Windows 사용자 이름에 공백이 있으면 기본 임시 경로에도 공백이 생기므로, `TMP`·`TEMP` 를 공백 없는 경로로 바꾸고 실행한다.
- `--identity` 키 파일은 작업자 계정만 읽을 수 있게 ACL 을 둔다. 다른 사용자도 읽을 수 있으면 Windows OpenSSH 가 그 키를 쓰지 않는다.
- 원장 잠금은 파일 생성(`O_EXCL`) 방식이라 Windows 에서도 같게 동작한다. 비정상 종료로 `<원장>.lock` 이 남으면, 다른 발급이 진행 중이 아닌지 확인한 뒤 지운다.
- 발급 중에 원장을 Excel 로 열어 두면 Excel 이 파일을 잠가 행을 쓰지 못한다(종료 코드 3). 원장을 닫고 같은 값으로 다시 실행하면 이어서 처리한다.

## 키 관리

- 서명 개인키는 사내에서만 보관하고 저장소와 장치에 두지 않는다. 장치 패키지에는 key_id 별 공개키만 둔다(`devinfo_record.py` 의 `SHIPPING_PUBLIC_KEYS`, 현재 key_id `1`. 장치의 `wifi_devinfo.py` 와 양산 발급 도구가 이 목록을 함께 쓴다).
- 공개키 목록은 추가만 한다. 이미 쓴 key_id 를 다른 키에 다시 쓰지 않는다.
- 개인키가 유출되면 해당 key_id 를 패키지에서 빼야 하고, 그 키로 서명한 장치는 모두 새 키로 다시 기록해야 한다.
- 시험은 시험 전용 키(고정 시드)를 쓴다. 이 키는 장치 패키지의 공개키 목록에 넣지 않는다.
