# config.json 완전 제거 · nginx 선행조건 분리 · Factory Reset eth0 공장주소 복원 설계

## 배경

이 설계는 애초 "`/usr/local/etc/config.json`과 nginx는 `wlan-proc`가 아니라 별도
`wifi_manager` 패키지가 설치·관리한다"는 전제로 출발했다. 그 전제 아래 `wlan-proc`의
`config.json` 삭제 로직, release/CI의 `config.json` 전용 검사, Factory Reset의 nginx
preflight·enable·후조건을 모두 제거하는 방향으로 작성됐었다.

이 전제는 근거가 확인되지 않아 **폐기**됐다. 이 저장소 워크스페이스에는 `wifi_manager`
패키지/소스가 존재하지 않고, 타겟 실측에서도 `/usr/local/etc/config.json`과
`wifi_manager` 패키지가 확인되지 않는다.

최종 확정된 계약은 다음과 같다.

1. **`config.json`은 완전 제거 대상이다.** 호환 유지 대상이 아니다. 런타임 설정원은
   `/usr/local/etc/wifi_init_conf.json` 하나뿐이다. `wlan-proc`은 업그레이드 시
   active 잔재를 계속 삭제하고, package/CI/release gate는 `config.json` 재유입을
   계속 차단한다.
2. **nginx는 표준 제품 이미지가 제공하는 선행조건이다.** Factory Reset의 필수 유닛에서
   제외해 nginx 부재·비정상이 Factory Reset을 실패시키지 않게 하되, 과거 출하된
   0.4.4/0.5.0의 Factory Reset이 `customctl disable nginx`로 영속 disable 시킨
   기기를 되살리는 `customctl enable nginx`는 그대로 유지한다. `wlan-proc`이 만든
   피해만 `wlan-proc`이 되돌리는 범위다.
3. `wlan-proc`과 (있다면) `wifi_manager`/`wifi-manager`류 유닛의 실제 접점은 파일이
   아니라 systemd 유닛이다. `wifi_init.sh`는 `wifi_manager`/`wifi-manager` 유닛이
   enabled면 wpa_supplicant를 직접 start하지 않는다. 이 접점은 이번 변경과 무관하며
   그대로 둔다.

또한 Factory Reset의 `eth0` 공장 주소가 타겟 시험용
`192.168.214.5/24`로 승격되어 있던 문제는 그대로 유효하다. 제품 공장 기본값은
`192.168.1.1/24`이다.

## 목표

1. `wlan-proc`이 업그레이드 시 `/usr/local/etc/config.json`의 active 잔재를 계속
   제거한다(완전 제거 계약 유지).
2. package/CI/release validator가 `config.json` 재유입을 계속 차단한다.
3. `wlan-proc` Factory Reset이 nginx를 필수 유닛에서 제외해 nginx 부재·비정상이
   Factory Reset을 실패시키지 않게 하되, 과거 Factory Reset이 영속 disable 시킨
   nginx를 복구하는 `customctl enable nginx`는 유지한다.
4. Factory Reset의 `eth0` 공장 주소를 `192.168.1.1/24`로 복원한다.
5. 변경 버전을 `0.5.4`로 올리고 패키지·문서·검증 계약을 동기화한다.

## 비목표

- 별도 `wifi_manager` 패키지의 존재나 버전을 전제하거나 감지하지 않는다(근거가
  확인되지 않아 폐기된 전제다).
- nginx의 설치나 최초 활성화를 `wlan-proc`이 새로 떠맡지 않는다. `wlan-proc`은
  표준 이미지가 이미 제공하는 nginx를 전제로 하며, 과거 자신이 만든 disable 피해만
  복구한다.
- `wifi_init.sh`가 `wifi_manager`/`wifi-manager` 유닛의 enabled 여부로
  wpa_supplicant 직접 기동을 건너뛰는 기존 접점을 변경하지 않는다.
- 0.5.2 당시 `192.168.214.5/24`를 사용했던 역사 기록을 소급 삭제하지 않는다.
- Factory Reset의 wpa_supplicant 공장 템플릿(SSID) 값은 변경하지 않는다.

## 소유권 경계

### config.json

`config.json`은 0.5.1 개발 중 임시 도입된 overlay 설정원이며 현재 소비 코드가 없다.
`wlan-proc`은 이 파일을 호환 유지 대상으로 취급하지 않고 완전 제거 대상으로 취급한다.

- `postinst`는 업그레이드 시 `rm -f -- /usr/local/etc/config.json`으로 active
  잔재를 계속 제거해, 장비에 `wifi_init_conf.json` 하나만 설정원으로 남긴다.
- `scripts/validate_release.sh`의 `release gate: retired config.json packaged`
  검사를 유지한다. 패키지에 `config.json`이 다시 포함되면 release가 실패한다.
- `.github/workflows/build-test.yml`의
  `dpkg-deb -c | grep usr/local/etc/config.json` 검사를 유지한다.
- `scripts/validate_release_test.sh`의 `config.json` negative fixture 2건(일반
  파일, symlink)을 유지해 게이트가 실제로 거부하는지 검증한다.
- `wifi_init_config_test.sh`의 retirement assertion 전부 유지한다. legacy 경로가
  패키지에 없는지, `wifi_init.sh`/`wifi.sh`/config library/factory_reset.sh에 legacy
  경로 참조가 없는지, postinst가 삭제 로직을 갖는지, 그리고 overlay가 존재해도 무시되는지
  검증하는 `run_case`(legacy overlay is ignored / legacy partial overlay is ignored)를
  포함한다.

이 저장소 워크스페이스에는 `wifi_manager` 패키지/소스가 존재하지 않으며, 타겟 실측에서도
`/usr/local/etc/config.json`과 `wifi_manager` 패키지가 확인되지 않는다. "wifi_manager
소유"라는 전제는 근거가 확인되지 않아 폐기했다.

`wlan-proc`과 wifi_manager의 실제 접점은 파일이 아니라 systemd 유닛이다:
`wifi_init.sh`가 `wifi_manager`/`wifi-manager` 유닛이 enabled면 wpa_supplicant를
직접 start하지 않는다. 이 접점은 그대로 둔다.

### nginx

nginx는 표준 제품 이미지가 제공하는 선행조건이다. Factory Reset은 다음과 같이
동작한다.

- `wifi_factory_reset_lib.sh`의 `FACTORY_REQUIRED_UNITS`에서 `nginx.service`를
  제외한다. nginx가 없거나 비정상이어도 Factory Reset은 실패하지 않는다(preflight·
  후조건 없음).
- `factory_reset.sh`의 `customctl enable nginx`는 유지한다. 출하된 0.4.4/0.5.0의
  factory_reset이 `customctl disable nginx`로 nginx를 영속 disable 시켰고
  (enable/disable은 유닛 파일 조작이라 영속), 그 기기는 스스로 복구되지 않는다.
  `wlan-proc`이 만든 피해만 `wlan-proc`이 되돌리는 범위다.

WLAN 핵심 서비스(`wifi-stack.target`, `wifi_apply_enabled.service`,
`wifi_init.service`)의 기존 검증은 유지한다.

## Factory Reset eth0 계약

공장 템플릿 `22-eth0.network`의 주소는 `192.168.1.1/24`이다.
Factory Reset은 기존 필수 payload 트랜잭션을 통해 active와 `.bak`에 같은 값을
원자적으로 복원한다. 일반 패키지 업그레이드는 기존 active 네트워크 설정을
보존한다(postinst의 `cpchk`가 대상이 비었거나 없을 때만 복사하므로).

**이 주소는 Factory Reset 후의 복구 경로다.** Factory Reset은 WPA supplicant 설정도
공장 기본값으로 되돌리므로, 기본 SSID가 현장에 없으면 무선이 붙지 않는 것이 정상이고
의도된 동작이다. 재설정은 유선으로 한다 — `192.168.1.0/24` 호스트를 `eth0`에 연결해
`192.168.1.1`로 접속한다. 따라서 이 값이 사이트 시험 주소로 바뀌어 있으면
공장초기화된 유닛의 복구 경로가 사라진다. 0.5.2가 정확히 그 상태였고
(`192.168.214.5/24`로 승격), 이 설계가 복원하는 대상이다.

release gate와 Factory Reset source-contract 테스트는 packaged template의 주소가
정확히 `192.168.1.1/24`인지 확인한다. `192.168.214.5/24`는 제품 기본값으로
허용하지 않는다.

## 버전·문서

- `dist/wlan/DEBIAN/control`: `0.5.4`, 버전 SSoT
- `README.md`: 현재 버전 및 Factory Reset 접속 주소 갱신
- `CHANGELOG.md`: 0.5.4 항목 추가
- `docs/wifi_init_conf_guide.md`: 현재 Factory Reset 주소와 재접속 절차 갱신

0.5.2 항목은 당시 출시 동작을 설명하는 역사 기록이므로 유지한다.

## 오류 처리

- `config.json`이 패키지에 재포함되면 release gate가 fail-closed로 실패한다.
- nginx가 없거나 비정상이어도 `wlan-proc` 설치와 Factory Reset의 성공 여부에
  영향을 주지 않는다(nginx는 필수 유닛이 아니다).
- WLAN 자체 필수 payload·CAL·service·sync 실패는 기존처럼 fail-closed 처리한다.
- eth0 factory template이 `192.168.1.1/24`가 아니면 build/release gate가 실패한다.

## 테스트 전략

### RED

1. postinst에 `rm -f -- /usr/local/etc/config.json`이 없으면 실패하는 retirement
   테스트
2. release validator가 `config.json` 재유입을 막지 않으면 실패하는 테스트
   (일반 파일/symlink fixture 각 1건)
3. `wifi_factory_reset_lib.sh`의 `FACTORY_REQUIRED_UNITS`에 nginx가 포함되면
   실패하는 소유권 테스트
4. `factory_reset.sh`에 `customctl enable nginx`가 없으면 실패하는 복구 테스트
5. Factory Reset eth0 source contract가 `192.168.1.1/24`가 아니면 실패하는 테스트

### GREEN

- 관련 focused shell tests (`wifi_init_config_test.sh`, `wifi_factory_reset_test.sh`)
- `scripts/validate_release_test.sh`
- `scripts/package_tar_test.sh`
- 전체 `./build.sh`
- generic/versioned Debian package gate
- source tar gate
- `release/SHA256SUMS` 검증

## 수용 기준

1. `postinst` 업그레이드 경로에 `/usr/local/etc/config.json` 삭제(`rm -f`)가 있다.
2. package/CI validator에 `config.json` 재유입 차단 검사가 있다(release gate +
   CI `dpkg-deb` 검사 + negative fixture 2건).
3. `wifi_factory_reset_lib.sh`의 `FACTORY_REQUIRED_UNITS`에 `nginx.service`가
   없다.
4. `factory_reset.sh`에 `customctl enable nginx`가 있다(과거 영속 disable 복구).
5. packaged `22-eth0.network`는 `Address=192.168.1.1/24`이다.
6. 버전·문서·테스트·release artifact가 `0.5.4` 계약과 일치한다.
7. 전체 빌드 및 release gates가 통과한다.
