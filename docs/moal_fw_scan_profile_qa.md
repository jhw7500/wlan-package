# MOAL/FW scan profile 재현 시험 가이드

> 공개 예시의 SSID/BSSID/IP/hostname은 `*_REDACTED` 토큰으로 치환했다.
> 원시 artifact와 실제 frozen run-config는 로컬 보존본이 정본이며, 외부 공유 전에
> 별도 비밀정보·네트워크 식별자 검토를 수행한다.

## 1. 목적

driver, firmware 또는 모듈 인자를 바꿀 때마다 동일한 조건으로 다음 항목을 반복 검증한다.

- `wifi_init`을 실행하지 않은 clean boot에서 제품과 동일한 module 인자를 재생한다.
- `antcfg`, rate-adapt, MCS, 제품 A/B 설정을 profile별로 독립 적용한다.
- SET 직후 GET으로 실제 적용값을 검증한다.
- `wpa_supplicant`만 남긴 상태에서 외부 `iw` 4채널 scan을 반복한다.
- 데이터 경로 wedge, `tx failed`, 연결 상태, BSSID, scan 주체와 FW fault를 함께 기록한다.

이 도구는 제품 초기화 코드를 대신하지 않는다. 실험 변수를 최소화하기 위한 QA 전용
controller이며, reboot·제품 복구·원격 artifact 회수는 호출자가 수행한다.

## 2. 파일 구성

| 파일 | 역할 |
|---|---|
| `scripts/qa/moal_fw_scan_profile_controller.sh` | clean-boot 검증, module load, profile 적용, GET 검증, 연결 기준 상태 생성 |
| `scripts/qa/moal_fw_scan_profile.example.conf` | revision별로 복사할 trusted Bash run-config 예시 |
| `scripts/qa/test_moal_fw_scan_profile_controller.py` | config/`--describe` contract test |
| `scripts/qa/iw_external_scan_datapath_repro.sh` | 연결 상태에서 `iw` 4채널 scan과 데이터 경로 판정 |
| `scripts/qa/wpa_cli_scan_only_datapath_repro.sh` | 동일 조건의 `wpa_cli SCAN TYPE=ONLY` requester 비교 |
| `scripts/qa/product_wifi_bgscan_soak.sh` | 실제 `wifi_bgscan` 경로의 idle-postcondition soak와 상태 복원 |
| `scripts/qa/test_scan_qa_safety.py` | destructive ack, artifact redaction, service 복원 contract test |

run-config는 shell에서 source되는 **신뢰된 Bash 파일**이다. 외부 입력을 그대로 저장해
사용하면 안 된다. destructive 실행에서는 root 소유 regular file이어야 하며 symlink 또는
group/other writable 파일은 거부한다.

## 3. profile 입력

### 3.1 revision identity

각 driver/FW 조합마다 다음 경로와 SHA-256을 새 run-config에 고정한다.

- `MLAN_KO`, `MOAL_KO`, `FW`
- `CONF`, `JSON`, `WPA_CONF`
- `MLANUTL`
- 사용하는 profile에 따라 `FW_LIB`, `TXPWR_CONF`, `THERMAL_CONF`
- `EXPECTED_*_SHA`

`MOAL_ARGS`는 문자열이 아니라 Bash indexed array로 작성한다. 제품 load 순서와 각 인자를
그대로 기록해야 하며, revision을 바꿀 때 기존 artifact의 config를 덮어쓰지 않는다.

### 3.2 동작 toggle

| 항목 | 의미 |
|---|---|
| `APPLY_AB=1` | TX-power/thermal 및 radio-default 명령 재생 |
| `APPLY_ANTCFG=1` | `ANT_TX`, `ANT_RX` 적용 |
| `EXPECTED_ANT_TX`, `EXPECTED_ANT_RX` | SET 뒤 예상 physical antenna GET. 생략하면 요청값 사용 |
| `EXPECTED_USER_HTSTREAM` | 선택적 host NSS intent 예상값. matching utility 출력으로 별도 검증 |
| `APPLY_RATE=1` | 동결 JSON을 통해 rate-adapt 적용 |
| `APPLY_MCS=1` | 동결 JSON을 통해 MCS 적용·검증 및 필요한 1회 reassociate |

모든 toggle은 정확히 `0` 또는 `1`이어야 한다. `ANT_RX=""`이면 한 인자 형식을 사용해
Rx=Tx를 요청한다. 두 값을 주면 Tx/Rx 분리 형식을 사용한다.

중요한 점은 **명령 성공, physical antenna mode, host NSS intent를 동일시하지 않는
것**이다. controller는 SET 뒤 `mlanutl ... antcfg` GET을 실행하고 요청값과 별도로
`EXPECTED_ANT_TX`, `EXPECTED_ANT_RX`를 검증한다. `EXPECTED_USER_HTSTREAM`을 지정하면
같은 출력의 `user_htstream`도 별도로 검증한다. ported/543에서는 이 값을 출력하는
matching utility가 필요하다. 예상하지 않은 정상화나 누락이 있으면 scan 전에 실패한다.

## 4. 호스트 사전 검증

```bash
python3 -m pytest -q \
  scripts/qa/test_moal_fw_scan_profile_controller.py \
  scripts/qa/test_scan_qa_safety.py
bash -n scripts/qa/*.sh
shellcheck -x scripts/qa/*.sh

scripts/qa/moal_fw_scan_profile_controller.sh \
  --describe path/to/frozen-run.conf

scripts/qa/moal_fw_scan_profile_controller.sh \
  --validate-profile path/to/frozen-run.conf
```

`--describe`에서 다음을 확인한다.

- profile 이름과 interface
- 실제 `antcfg` 명령
- 각 `APPLY_*` 값
- `MOAL_ARGS` 개수와 완성된 `insmod` 명령

`--validate-profile`은 실제 파일을 읽기 전에 owner/type/mode를 검사하고, 기본 identity
hash 및 활성화한 profile에 필요한 조건부 hash가 모두 64자리 SHA-256인지 확인한다.
예제 config의 빈 hash는 설명용이므로 validation과 destructive 실행에서는 의도적으로
실패한다.

배포 전 controller, harness, run-config와 필요한 config 파일을 한 directory에 복사하고
`SHA256SUMS`를 만든다. 보드에 복사한 뒤에도 같은 hash인지 비교한다. safety-hardened
controller는 run artifact에 config 이름과 SHA-256을 기록하지만 trusted Bash 원문을
자동 복제하지 않으므로, 이 동결 config directory가 재현의 기준 사본이다.

## 5. 보드 실행 절차

### 5.1 제품 기준 상태

유선 관리 경로를 사용하며 다음을 먼저 기록한다.

- boot ID와 uptime
- module/FW/config/utility hash 및 module `srcversion`
- `wifi_init`, FW watch, supplicant 상태
- `wpa_cli status`, `iw info/link/station dump`
- TX power, `tx failed`, gateway ping, 현재 boot의 FW reset/dump marker

### 5.2 clean boot gate

1. `wifi_init.service`를 disable한다.
2. reboot한다.
3. 보드가 연속 reboot할 수 있으므로 첫 SSH 성공을 사용하지 않는다.
4. 같은 boot ID가 10초 뒤에도 유지되고 uptime이 80초 이상인지 확인한다.
5. `wifi_init` disabled/inactive, 현재 boot journal 없음, `mlan`/`moal`과 `mlan0` 부재를
   확인한다.

이 조건 중 하나라도 다르면 controller를 실행하지 않는다.

### 5.3 controller

```bash
install -d -m 0700 /var/tmp/scan-qa-run
test ! -e /var/tmp/scan-qa-run/controller
scripts/qa/moal_fw_scan_profile_controller.sh \
  --ack-disruptive \
  /var/tmp/scan-qa-run/controller \
  /root/scan-qa/frozen-run.conf
```

`--ack-disruptive`는 보드 독점 사용, 유선 관리 경로 및 reboot/복구 계획을 호출자가 이미
확인했다는 명시적 승인이다. 자동 예약 기능은 아니다. 이 flag가 없으면 config를 source하거나
artifact를 만들기 전에 exit 2로 중단한다.

artifact leaf는 절대경로의 **새 경로**여야 하고 parent는 미리 존재해야 한다. 스크립트는
기존 directory, symlink, `/`, non-canonical parent를 거부하고 새 leaf를 `0700`으로
원자 생성한다.

성공 조건은 다음과 같다.

- 동결 hash와 실제 보드 파일 일치
- 정확한 module command만 실행
- 선택한 profile의 command file만 존재
- 적용한 설정의 live GET 일치
- 예상하지 않은 MCS pending/reassociate marker 없음
- `wpa_state=COMPLETED`, 지정 SSID/IP, baseline ping 성공
- `fault_count_since_load=0`
- `wpa_supplicant`만 active인 격리 상태

controller가 실패하면 `iw` gate를 실행하지 않는다. error dmesg/journal과 command
artifact를 회수하고 제품을 reboot 복구한다.

### 5.4 외부 `iw` gate

```bash
ISOLATION_PROFILE=wpa-only \
scripts/qa/iw_external_scan_datapath_repro.sh \
  --ack-disruptive \
  /var/tmp/scan-qa-run/iw-active-4ch-interval5-run1 \
  mlan0 LAB_SSID_REDACTED WLAN_GATEWAY_IP_REDACTED 5 60
```

인자 `5`는 각 scan 완료 뒤 대기 초, 마지막 `60`은 **시간이 아니라 최대 scan 횟수**다.
정상 scan이 약 0.27초 걸리므로 이 예의 전체 실행은 기준 ping과 수집 시간을 포함해 약
330초다.

scan 명령은 다음으로 고정된다.

```text
iw mlan0 scan freq 5180 5200 5220 5240 ssid LAB_SSID_REDACTED
```

기본 positive stop은 초기값 대비 `tx failed`가 1,000 이상 증가하는 경우다. 최종
`REPRODUCED` 판정에는 다음이 함께 필요하다.

- final ping 실패
- `tx failed` 증가량이 threshold 이상
- supplicant는 계속 `COMPLETED`
- BSSID가 시작과 동일

`wpa_external_scan_count`, `own=0 ext=1`, own-scan count와 disconnect count도 반드시
검토한다. 이 시험에서는 `wpa_cli scan`을 추가로 실행하지 않는다.

### 5.5 회수와 복구

1. controller와 harness를 포함한 run root에 SHA-256 manifest를 만든다.
2. tarball을 만든 뒤 유선 경로로 회수한다.
3. 로컬에서 manifest를 다시 검증한다.
4. `wifi_init`과 제품 서비스를 enable하고 reboot한다.
5. 안정 boot를 다시 확정한다.
6. 제품 hash, 서비스, Tx/Rx, TX power, 연결, `tx failed=0`, ping, FW fault=0을 확인한다.
7. 원격 controller/run/tarball을 삭제하고 다시 연결 상태를 확인한다.

## 6. 2026-08-25 방향 분리에서 확인한 주의점

543.p18 driver와 17.92.1.p149.115 FW에서 다음 결과를 얻었다.

| 요청 | 즉시 GET | scan gate |
|---|---|---|
| Tx `0x303`, Rx `0x101` | physical Tx/Rx `0x303/0x303`, host NSS `0x2121` | 교정된 gate 60/60 `NOT_REPRODUCED` |
| Tx `0x101`, Rx `0x303` | Tx `0x101`, Rx `0x303` | 60/60 `NOT_REPRODUCED` |
| Tx/Rx `0x101` | Tx/Rx `0x101` | 앞선 시험에서 scan 5에 `REPRODUCED` |

LD_PRELOAD ioctl capture에서 제품 `mlanutl`과 같은 source tree의 utility가 모두
`MRVL_CMDantcfg0x303 0x101`을 정확히 직렬화했다. matching driver source도 두 인자를
별도 Tx/Rx field로 만든다. 후속 driver/F/W 행렬에서는 표준 antenna GET과 host NSS
intent가 서로 다른 계층임을 확인했다. F/W physical mode는 Tx/Rx `0x303/0x303`으로
돌아오지만 host `user_htstream=0x2121`은 2G/5G Rx 1SS/Tx 2SS를 유지한다.

main/505는 확장된 16-byte `antcfg` 응답으로 `user_htstream`을 함께 반환하지만,
ported/543은 기존 `antcfg` ABI와 별도 `antcfgnss` 조회를 사용한다. 따라서 utility와
driver revision을 맞추지 않으면 ported/543의 NSS 진단이 조용히 생략된다. 상세 증거는
`/home/jhw/ai/opencode/projects/wlan-package/.worktrees/wpa-roam-scan-policy/docs/antcfg_set_get_compat_matrix_2026-08-25.md`에
기록했다.

현재 bounded evidence가 지지하는 범위는 다음과 같다.

- 1Tx/2Rx physical path 제한만으로는 동일 gate에서 wedge가 재현되지 않았다.
- 1Tx/1Rx는 재현된다.
- `0x303/0x101` 요청은 physical antenna mode를 2Tx/2Rx로 유지하면서 host NSS intent를
  2Tx/1Rx로 만든다.
- 교정된 controller로 host Rx NSS 제한 단독을 60회 직접 검증했으며 재현되지 않았다.
- 따라서 Tx 제한 단독과 host Rx NSS 제한 단독은 각각 충분조건이 아니다. 남은 차이는
  physical Rx 1-path와 Tx/Rx 동시 1-path 상호작용이다.

controller는 이제 요청값, 예상 physical antenna mode, 예상 `user_htstream`을 별도
필드로 검증한다. ported/543에서는 별도 NSS ABI를 처리해 `antcfg` 결과에
`user_htstream`을 표시하는 matching utility가 필요하다. 이번 결과와 원시 증거는
`/home/jhw/ai/opencode/projects/wlan-package/.worktrees/wpa-roam-scan-policy/docs/ant_rx_nss_scan_gate_2026-08-25.md`와
`/home/jhw/ai/opencode/projects/wlan-package/.worktrees/wpa-roam-scan-policy/artifacts/moal-ant-rx-nss-scan-20260825/`에
보관한다.

원시 증거는 다음에 보관한다.

```text
/home/jhw/ai/opencode/projects/wlan-package/.worktrees/wpa-roam-scan-policy/artifacts/moal-ant-direction-bisect-20260825/543p18-p149115/
/home/jhw/ai/opencode/projects/wlan-package/.worktrees/wpa-roam-scan-policy/artifacts/moal-antcfg-compat-matrix-20260825/
```
