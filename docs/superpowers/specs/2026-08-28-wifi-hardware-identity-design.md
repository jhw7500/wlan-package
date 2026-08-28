# #197 실제 SoC 기반 제품 scan profile 검증 설계

## 배경

현재 `wifi_fw_validate_product_scan_profile`은 호출자가 전달한 persisted
`.global.BOARD_TYPE`으로 i.MX93 strict profile 적용 여부를 정한다. 설치와 Factory
Reset은 실제 SoC를 감지해 이 값을 고치지만, 정상 부팅은 유효한 JSON을 그대로 믿는다.
따라서 JSON drift 또는 변조로 i.MX93 strict profile을 건너뛸 수 있는 신뢰 경계가 남는다.

## 목표

실제 SoC를 보드 판정의 유일한 정답으로 사용한다. persisted `BOARD_TYPE`은 운영 설정이
아니라 감지 결과의 저장본으로 취급한다. 설치, Factory Reset, 정상 부팅은 기존
`wifi_board_config.sh`의 동일한 감지 계약을 사용하며, 별도 identity subsystem이나 새
library는 만들지 않는다.

## 비목표

- 지원 보드를 i.MX93과 i.MX8MM 외로 확장하지 않는다.
- driver component lock 형식이나 release provenance 생성기를 변경하지 않는다.
- i.MX8 custom antcfg/MCS 값을 i.MX93 제품 기본값으로 바꾸지 않는다.
- 일반적인 secure boot 또는 root 공격 방어 체계를 새로 만들지 않는다.

## 최소 identity 계약

`wifi_board_config.sh --detect`가 canonical identity source다.

- `${WIFI_SOC_ID_PATH:-/sys/devices/soc0/soc_id}`를 읽고 안전한 문자만 남긴다.
- 대소문자를 무시한 `i.MX93` family token은 `BOARD_TYPE=imx93`,
  `BUS_TYPE=sdio`로 정규화한다.
- 대소문자를 무시한 `i.MX8MM` family token은 `BOARD_TYPE=imx8mm`,
  `BUS_TYPE=pcie`로 정규화한다.
- 빈 값, 읽기 실패, 미지원 값은 non-zero로 종료한다. 기존의 "그 밖에는 imx8mm"
  fallback은 제거한다.
- 성공 시 기존과 같이 `SOC_ID`, `BOARD_TYPE`, `BUS_TYPE`, `IIO_DEV`를 안전하게 출력한다.

테스트 전용 경로 override는 기본 제품 동작을 바꾸지 않는다. 운영 기본값은 항상 실제
sysfs다.

## 실행 경로

### 설치

`postinst`는 `wifi_board_config.sh --detect` 성공값만 사용한다. helper 부재 또는 감지
실패 시 설치를 중단하며, 별도의 permissive inline SoC fallback은 제거한다. JSON merge 후
기존 helper 적용 경로로 board-owned 값을 기록한다.

### Factory Reset

기존 `factory_stage_config`가 template stage에 `wifi_board_config.sh`를 실행하는 흐름을
유지한다. 감지 실패는 stage 실패가 되어 active JSON을 바꾸지 않고 reset을 중단한다.

### 정상 부팅

`wifi_init.sh`는 JSON의 `BOARD_TYPE`을 소비하기 전에 `--detect`를 한 번 실행한다.

1. 실제 SoC identity를 확보하지 못하면 module load 전에 실패한다.
2. persisted `BOARD_TYPE`, `BUS_TYPE`, `IIO_DEV` 중 하나라도 감지값과 다르면
   `local0.crit`로 양쪽 값을 남기고 기존 helper로 JSON을 원자 정규화한다.
3. module 파일 선택과 product scan profile validator에는 persisted 값이 아니라 감지된
   `BOARD_TYPE`을 사용한다.
4. 실제 i.MX93에서는 기존 mlan0 antcfg/MCS와 mlan1 antcfg-off 불변식을 그대로
   fail-closed 검증한다.
5. 실제 i.MX8MM에서는 i.MX93 strict profile을 적용하지 않는다. 기존 helper가 정확히
   알려진 injected product profile만 중화하며, 그 밖의 custom 설정은 보존한다.

## 선택·로드 module 확인

새 library를 만들지 않고 기존 `wifi_board_config.sh`에 작은 검증 명령을 추가한다.

```text
wifi_board_config.sh --verify-loaded <board> <mlan-ko> <moal-ko>
```

이 명령은 다음만 확인한다.

- `<board>`와 선택된 KO basename 조합이 `imx93 -> *_imx93.ko`,
  `imx8mm -> *_imx8.ko`로 일치한다.
- 선택 KO의 NUL-separated module metadata에서 유일한 `version`과 `srcversion`을
  읽는다. 파일 부재·빈 값·중복 field는 실패다.
- `${WIFI_SYS_MODULE_ROOT:-/sys/module}` 아래 `mlan`과 `moal`의 `version`,
  `srcversion`이 선택 KO와 같다. override는 focused test에서만 사용한다.

`wifi_init.sh`는 두 `insmod` 성공 직후 이 명령을 호출한다. metadata 부재, sysfs 부재,
선택/로드 불일치는 association 전에 실패한다. 비교는 현재 스크립트가 KO capability를
읽을 때 이미 사용하는 `tr '\0' '\n'` 방식만 재사용하며 새 runtime dependency를 추가하지
않는다.

## 로그와 실패 정책

- 감지 성공: 실제 `SOC_ID`, canonical board/bus, 선택 module을 `info`로 기록한다.
- persisted 불일치: persisted 값과 실제 값을 `crit`로 기록한 뒤 실제 값으로 정규화한다.
- SoC source 부재·미지원·읽기 오류: `emerg`, module load 전 실패한다.
- JSON 정규화 실패: `emerg`, 기존 JSON을 유지하고 module load 전 실패한다.
- 선택/로드 module identity 불일치: 기대·실제 metadata를 `emerg`로 기록하고 association
  전에 실패한다.

알려진 실제 SoC가 있는 경우 persisted 불일치 자체는 즉시 실패 사유가 아니다. 실제 값으로
정규화한 뒤 해당 보드 profile 검증 결과가 최종 기동 여부를 결정한다.

## 변경 범위

- `dist/wlan/usr/local/scripts/wifi_board_config.sh`
- `dist/wlan/usr/local/scripts/wifi_init.sh`
- `dist/wlan/DEBIAN/postinst`
- `dist/wlan/usr/local/scripts/wifi_fw_config_test.sh`
- `docs/wifi_init_conf_guide.md`

## 테스트 전략

1. fake `soc_id`로 i.MX93과 i.MX8MM 정규화 성공을 검증한다.
2. 빈 파일, 누락 파일, 미지원 SoC가 fail-closed인지 검증한다.
3. persisted board/bus/IIO 불일치가 실제 값으로 정규화되고 로그되는지 검증한다.
4. fake KO metadata와 fake `/sys/module`로 일치 성공 및 basename/version/srcversion
   불일치 실패를 검증한다.
5. 실제 i.MX93 identity에서 안전 profile만 통과하고, 실제 i.MX8MM에서는 strict
   profile을 건너뛰며 custom config가 보존되는지 검증한다.
6. postinst, Factory Reset, normal boot가 모두 기존 helper를 사용하는 source contract를
   검증한다.
7. `scripts/validate_release.sh pre`와 `build.sh`를 실행한다.

## 수용 기준

1. persisted `BOARD_TYPE`만 바꿔 i.MX93 strict profile을 우회할 수 없다.
2. 미지원 또는 확인 불가능한 SoC는 imx8mm로 오인되지 않고 fail-closed 된다.
3. 실제 SoC, persisted board facts, 선택 KO, 로드 module의 불일치가 로그와 테스트로
   관측된다.
4. i.MX93 안전 불변식과 i.MX8MM custom 설정 보존이 모두 유지된다.
5. 설치, Factory Reset, 정상 부팅이 `wifi_board_config.sh`의 같은 identity 계약을 쓴다.
6. focused tests, pre-release gate, 전체 build가 통과한다.
