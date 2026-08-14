# wifi_manager 소유권 분리 및 Factory Reset eth0 기본값 복원 설계

## 배경

`/usr/local/etc/config.json`과 nginx는 `wlan-proc`가 아니라 별도
`wifi_manager` 패키지가 설치하고 관리한다. 현재 `wlan-proc` 0.5.3은 업그레이드 시
`config.json`을 삭제하고, Factory Reset에서 nginx를 필수 이미지 구성요소로
preflight·enable·후조건 검증한다. 이 동작은 두 패키지의 소유권을 충돌시키며
`wifi_manager`의 동작을 깨뜨릴 수 있다.

또한 Factory Reset의 `eth0` 공장 주소가 타겟 시험용
`192.168.214.5/24`로 승격되어 있다. 제품 공장 기본값은
`192.168.1.1/24`이다.

## 목표

1. `wlan-proc`가 `config.json`을 설치·삭제·복원·검증하지 않는다.
2. `wlan-proc`가 nginx의 설치·enable 상태·Factory Reset 후조건을 관리하지 않는다.
3. Factory Reset의 `eth0` 공장 주소를 `192.168.1.1/24`로 복원한다.
4. 변경 버전을 `0.5.4`로 올리고 패키지·문서·검증 계약을 동기화한다.

## 비목표

- `wifi_manager`의 설치 여부나 버전을 감지하지 않는다.
- `wifi_manager`가 생성한 `config.json`의 형식·권한·내용을 검증하지 않는다.
- nginx를 설치·삭제·start·stop하는 대체 로직을 추가하지 않는다.
- 0.5.2 당시 `192.168.214.5/24`를 사용했던 역사 기록을 소급 삭제하지 않는다.
- 이번 변경에서 타겟 장치에 패키지를 배포하지 않는다.

## 소유권 경계

### config.json

`wlan-proc`의 설치·업그레이드·Factory Reset 경로는
`/usr/local/etc/config.json`과 `/opt/wlan/config/config.json`을 읽거나 쓰거나
삭제하지 않는다. 기존 `wifi_manager` 파일은 그대로 보존한다.

전용 release/CI 검사는 제거한다. Debian exact payload manifest는
`wlan-proc` 자체 payload의 일반 무결성 계약으로 계속 동작하지만,
`config.json`이라는 이름을 별도 정책으로 취급하지 않는다.

회귀 테스트는 외부 파일의 존재나 내용이 아니라 **비간섭 계약**만 검증한다.
즉 postinst와 Factory Reset이 외부 경로를 참조하지 않는지를 확인한다.

### nginx

nginx는 `wifi_manager` 소유다. Factory Reset은 다음을 수행하지 않는다.

- `nginx.service` 존재 여부 preflight
- nginx enable
- nginx enabled 후조건 검증

WLAN 핵심 서비스(`wifi-stack.target`, `wifi_apply_enabled.service`,
`wifi_init.service`)의 기존 검증은 유지한다.

## Factory Reset eth0 계약

공장 템플릿 `22-eth0.network`의 주소는 `192.168.1.1/24`이다.
Factory Reset은 기존 필수 payload 트랜잭션을 통해 active와 `.bak`에 같은 값을
원자적으로 복원한다. 일반 패키지 업그레이드는 기존 active 네트워크 설정을
보존한다.

release gate와 Factory Reset source-contract 테스트는 packaged template의 주소가
정확히 `192.168.1.1/24`인지 확인한다. `192.168.214.5/24`는 제품 기본값으로
허용하지 않는다.

## 버전·문서

- `dist/wlan/DEBIAN/control`: `0.5.4`
- `README.md`: 현재 버전 및 Factory Reset 접속 주소 갱신
- `CHANGELOG.md`: 0.5.4 항목 추가
- `docs/wifi_init_conf_guide.md`: 현재 Factory Reset 주소와 재접속 절차 갱신

0.5.2 항목은 당시 출시 동작을 설명하는 역사 기록이므로 유지한다.

## 오류 처리

- `config.json` 또는 nginx가 없거나 비정상이어도 `wlan-proc` 설치와 Factory Reset의
  성공 여부에 영향을 주지 않는다.
- WLAN 자체 필수 payload·CAL·service·sync 실패는 기존처럼 fail-closed 처리한다.
- eth0 factory template이 `192.168.1.1/24`가 아니면 build/release gate가 실패한다.

## 테스트 전략

### RED

1. postinst가 `/usr/local/etc/config.json`을 참조하면 실패하는 비간섭 테스트
2. Factory Reset/lib가 nginx를 참조하면 실패하는 소유권 테스트
3. Factory Reset eth0 source contract가 `192.168.1.1/24`가 아니면 실패하는 테스트
4. release validator fixture가 `192.168.214.5/24`를 포함하면 실패하는 테스트

### GREEN

- 관련 focused shell tests
- `scripts/validate_release_test.sh`
- `scripts/package_tar_test.sh`
- 전체 `./build.sh`
- generic/versioned Debian package gate
- source tar gate
- `release/SHA256SUMS` 검증

## 수용 기준

1. 업그레이드 스크립트에 `/usr/local/etc/config.json` 삭제가 없다.
2. Factory Reset과 그 library에 nginx lifecycle/preflight/postcondition이 없다.
3. package/CI validator에 `config.json` 전용 금지 검사가 없다.
4. packaged `22-eth0.network`는 `Address=192.168.1.1/24`이다.
5. 버전·문서·테스트·release artifact가 `0.5.4` 계약과 일치한다.
6. 전체 빌드 및 release gates가 통과한다.
