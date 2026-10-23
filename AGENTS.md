# wlan-package 작업 지침

Claude Code와 Codex가 공용으로 읽는 저장소 지침이다. 루트 `CLAUDE.md`는
`@AGENTS.md`를 가져오며 저장소 지침은 이 파일에서 함께 관리한다.
상위 지침에 더해 아래 저장소 구조와 문서 관리 기준을 적용한다.

## 저장소 개요

- 산출물은 Debian 패키지 `wlan-proc`(`release/wlan.deb`)이다. 타겟은 ARM64 임베디드
  (i.MX8MM / i.MX93 + NXP 88W9098), systemd 기반.
- 패키지 버전의 정본은 `dist/wlan/DEBIAN/control`의 `Version` 한 곳이다.
- 레이아웃
  - `dist/wlan/` — 패키지에 실리는 파일 트리 그대로. `DEBIAN/`(maintainer 스크립트),
    `usr/local/scripts/`(WLAN 관리 셸·파이썬), `usr/local/logger/`(로거·로밍 데몬),
    `usr/local/tools/`(pcap-analyzer 등), `opt/wlan/`(드라이버·설정 템플릿).
  - `scripts/` — 빌드·릴리스 게이트·QA 하네스. 패키지에 실리지 않는다.
  - `wlan-bridge/` — git 서브모듈(L2 브리지 `wbridge`). `wlan-opc/` — OPC 데몬.
  - `docs/` — 지속적으로 관리하는 가이드·스키마·설계 계약.
  - `local-docs/` — 아래 기준에 따른 현황판·세션 메모·일회성 검증 자료.

## 로컬 작업 문서

- Git 관리가 필요 없는 현황판, 이슈 정리, 세션 메모, 개인 작업 계획,
  일회성 검증 보고서와 캡처는 저장소 루트의 `local-docs/`에 둔다.
- 필요하면 `local-docs/<작업명>/`으로 구분한다. 새 파일마다 ignore 규칙을
  추가하지 않고 `.gitignore`의 `/local-docs/`로 전체 디렉터리를 제외한다.
- 이 자료는 로컬 조회용이다. 일반 커밋·PR·릴리즈 산출물에 포함하지 않고,
  `git add -f`로 강제 추가하지 않는다. 사용자가 특정 자료의 정식 문서화를
  명시적으로 요청한 경우에는 필요한 내용만 정식 문서로 옮긴다.
- 추적되는 README나 정식 문서에 `local-docs/`, `.omx/`, 개인 절대 경로를
  가리키는 링크를 추가하지 않는다. 로컬 자료 사이의 상대 링크는 유지한다.
- `.omx/` 등 도구가 관리하는 기존 로컬 기록은 원래 위치와 ignore 규칙을
  유지한다. 원시·동결 검증 자료를 단순 정리를 위해 이동하거나 재작성하지 않는다.

## Git에 남길 문서와 검증

- README, CHANGELOG, 제품 사양, 사용자·운영 가이드와 지속적으로 유지할
  설계 계약은 정식 문서로 관리한다. 기존 추적 문서를 일괄 이동하거나
  문서 확장자 전체를 ignore하지 않는다.
- 재현에 필요한 제품 수정과 자동 회귀 테스트는 코드와 함께 추적한다.
  로컬 보고서 보관 규칙으로 테스트나 제품 소스를 제외하지 않는다.
- 커밋·PR에는 변경 이유와 실제 검증 결과를 간결하게 기록한다.
  일회성 보고서 전체를 추가해 코드와의 정합성 리뷰 범위를 늘리지 않는다.
- 문서를 정리한 뒤 `git status`와 `git check-ignore -v`로 제외 상태를
  확인하고, 이동한 문서의 상대 링크가 유효한지 점검한다.

## 빌드

```bash
git submodule update --init --recursive   # 새 체크아웃에서 빌드 입력 준비
./build.sh                                # 개발 빌드 (pre-build 게이트 생략)
./build.sh --release                      # 정규 빌드 (게이트 포함, CI와 동일)
```

- 서브모듈이 없으면 **에러 없이** bridge 없는 패키지가 나온다(`opt/wlan/config/no-wbridge`
  표식). 정규 빌드 전에 `wlan-bridge/wbridge/`가 있는지 확인한다.
- `build.sh`는 임시 staging에서 테스트·캐시를 제거하고 권한을 정리한 뒤 패키징한다.
  JSON·WPA 템플릿의 권한을 지정하지만 실행 파일의 실행 비트는 별도 관리한다.
  릴리스 빌드 전 `git status`가 클린한지 확인한다.
- 일반 SDK/make/dpkg 패키지 빌드와 Yocto/BitBake 실행을 구분한다. BitBake 태스크와
  공유 빌드 캐시 변경에는 별도 사용자 승인이 필요하다.
- 산출물: `release/wlan.deb`, `release/wlan-proc-<version>.deb`, `release/wlan-package.tar`
  (허용목록 기반 소스 아카이브), `release/SHA256SUMS`. `release/`는 gitignore 대상이다.
- `build.sh`가 wbridge·opcd·vhlctl·vhld를 빌드해 staging한다. 드라이버 `.ko`와
  `mlanutl_*`는 외부 빌드 산출물을 staging하며 Git에 넣지 않는다.
- 드라이버 교체 시 검증한 불변 ref와 산출물을 준비한 뒤
  `bash scripts/gen_driver_manifest.sh --write <wlan-driver-v2 repo> <ref>`로
  `dist/wlan/opt/wlan/driver/DRIVER_MANIFEST.md`와 `DRIVER_COMPONENTS.sha256`을 함께
  갱신한다. `bash scripts/gen_driver_manifest.sh --check`가 선언과 payload 해시를 검사한다.

## 테스트와 게이트

pytest는 루트에서 한 번에 돌리지 않는다. 게이트가 쓰는 단위 그대로 디렉터리별로 실행한다.

```bash
python3 -m pytest dist/wlan/usr/local/logger/tests -q
python3 -m pytest dist/wlan/usr/local/scripts/tests -q
python3 -m pytest dist/wlan/usr/local/tools/pcap-analyzer/tests/test_extractor.py \
    dist/wlan/usr/local/tools/pcap-analyzer/tests/test_models.py \
    dist/wlan/usr/local/tools/pcap-analyzer/tests/test_ping_matching.py -q
python3 -m pytest scripts/qa -q
```

- 셸 테스트는 `dist/wlan/usr/local/scripts/*_test.sh`처럼 대상 옆에 `_test.sh`로 둔다.
- 릴리스 게이트 `scripts/validate_release.sh <source|pre|package <deb>>`
  - `source` — 스키마·pytest·셸 테스트 (CI `source-gate`가 실행)
  - `pre` — 정규 빌드 직전 전체 게이트(`build.sh --release`가 호출)
  - `package <deb>` — 만들어진 .deb 검증
  - 게이트 자체의 회귀는 `scripts/validate_release_test.sh`, `scripts/package_tar_test.sh`.
- 설정 기본값 drift: `scripts/gen_config_defaults.py --check`(게이트) / `--write`(동기화).
- 빠른 정적 확인: `python3 scripts/gen_config_defaults.py --check`와
  `python3 scripts/exec_bit_targets.py`.
- ShellCheck CI는 `build.sh`와 지정한 DEBIAN maintainer 스크립트를 error 수준으로 검사한다.
- `Build and Test`의 실제 `.deb` 생성 단계는 서브모듈 준비 성공과 ARM64 러너 조건이 있다.
  job의 success만으로 패키지 생성을 판정하지 말고 실제 실행 단계와 산출물을 확인한다.

## 파일을 추가·변경할 때 같이 고칠 것

- **새 payload 파일** → `dist/wlan/DEBIAN/payload-manifest.txt`와
  `scripts/source_archive_manifest.txt`를 함께 갱신한다. 패키지 payload 검사와
  `test_source_archive_manifest.py`가 두 목록의 누락을 검사한다.
- **`dist/wlan/**/tests/`의 새 테스트** → 소스 아카이브 목록을 갱신한다.
  `scripts/qa/` 도구·테스트는 소스 목록과 `scripts/package_tar_test.sh`의 별도
  `EXACT_PATHS` 허용목록을 함께 검토한다.
- **systemd `ExecStart=`·udev `RUN+=`가 직접 실행하는 일반 파일** → git 인덱스 모드가
  100755여야 한다. 심볼릭 링크는 120000을 유지하고 실제 대상의 실행 권한을 확인한다.
  판정 규칙은 `scripts/exec_bit_targets.py` 한 곳이고, pre-commit 훅
  (`git config core.hooksPath scripts/git-hooks`)이 +x를 보정하며 CI
  `test_unit_execstart_exec_bit.py`가 강제한다. 실행 비트가 빠지면 systemd는
  `203/EXEC`로 실패하고 udev는 `RUN` 실행을 조용히 건너뛸 수 있다.
- **`wifi_init_conf.json` 키·기본값** → 템플릿, `docs/wifi_init_conf.schema.json`,
  `docs/wifi_init_conf_webui_handoff.md`의 생성기 관리 항목을
  `gen_config_defaults.py --check`로 확인한다. `docs/wifi_init_conf_guide.md`의 설명과
  운영 계약은 별도로 검토한다.
  postinst의 json_merge는 기존 값을 보존하고 누락된 기본 키를 보충한다. 기존 키의 값을
  바꾸려면 마이그레이션을 검토하고 신규 설치·업그레이드·공장초기화 경로를 구분해 확인한다.
- **설정을 쓰는 코드**는 파일에 찍힌 글자가 아니라 소비자(wpa_supplicant, systemd 등)에
  실제로 먹여 검증한다. 일부 키는 스크립트가 아니라 패치된 wpa_supplicant 바이너리가
  JSON을 직접 읽으므로, 소비처를 찾을 때 바이너리 `strings`도 본다.
- **CHANGELOG.md** — 버전별 절, 첫 줄에 SemVer 수준과 한 줄 요약, 이슈·PR 번호 병기.

## 버전·릴리스·PR

- 버전은 **패치 자리만** 올린다(0.6.9 → 0.6.10). 동작 계약이 바뀌어도 minor를 올리지
  않는다. minor는 여러 PR을 묶는 릴리스 단위이며, 필요해 보이면 사용자에게 먼저 묻는다.
- master 직접 커밋 금지. 브랜치 → PR → 리뷰 → 머지.
- Claude·Gemini·OpenCode의 자동 실행 여부는 `.github/workflow-config.yml`과 PR 이벤트에
  따른다. `review:request`는 명시적 리뷰 요청이며 `review:skip`과 함께 쓰지 않는다.
  Codex 앱의 명시적 리뷰 요청은 별도의 `@codex review` 코멘트로 보낸다.
- 커밋·PR 본문은 적용 중인 Change Evidence Contract v1과 `jhw-commit`·`jhw-pr` 절차를 따른다.
- 리뷰 결과는 현재 HEAD·run에 결합된 canonical 상태와 accepted·filtered·normalized 수로
  확인한다. `Workflow Skipped`나 모델 job의 success만으로 리뷰 통과를 판정하지 않는다.
  원시 로그나 필터링된 후보를 유효한 리뷰 지적으로 승격하지 않는다.
- 리뷰 의견은 실제 결함만 수용한다. 가설·과설계·스타일 지적은 근거를 적고 거절·보류한다.
- 서브모듈 포인터 변경도 적용 중인 리뷰·머지 정책에 따라 상류 변경과 검증 근거를 확인한다.

## 타겟 배포·검증

- 배포는 `scp release/wlan.deb` → `dpkg -i`. 타겟 주소·접속 정보는 이 파일에 적지 않는다.
- 업그레이드 검증은 `wifi_init` 하나가 아니라 **자식 유닛**(`wifi_logger`, `wifi_roam`,
  `wifi_checker`, `wifi_bridge` 등)이 active인지부터 본다. 통신은 되는데 관측·로밍만 죽는
  장애가 조용하다.
- 데몬 바이너리 교체는 설치 파일이 아니라 **구동 중 프로세스의 `/proc/<pid>/exe`**로 확인한다.
  postinst가 restart하지 않으면 옛 바이너리가 계속 돈다.
- 공장초기화의 `eth0`=`192.168.1.1/24`는 유선 복구 경로다. 기본 SSID AP 부재와
  장치 고장을 구분하고, 망 전환 시험에는 유선·시리얼 관리 경로를 확보한다.
- 링크 상태 판정은 `dist/wlan/usr/local/scripts/wlan_link_lib.sh`의 계약을 따른다.
- 타겟 상태를 바꾸는 QA 도구(`scripts/qa/`)는 복원 계약을 가지며, 그 계약은
  `scripts/qa/test_*.py`가 고정한다. 도구를 고치면 테스트도 같이 돈다.

## 작업 환경 주의

- 여러 에이전트 세션이 Git 저장소를 공유한다. 다른 세션과 겹치는 작업은 별도 worktree로
  격리하고 해당 환경의 관리 도구를 우선한다. 다른 세션의 변경·worktree·도구 상태를 보존한다.
- `.omc/`, `.omx/`, `.claude/`, `.base/` 등 도구 상태 디렉터리는 gitignore 대상이며
  패키지·아카이브에 들어가지 않는다. 이 파일(`AGENTS.md`)도 소스 아카이브 허용목록에
  넣지 않는다.
