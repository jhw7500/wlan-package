# AGENTS.md — wlan-package

Claude Code와 Codex CLI가 공용으로 읽는 저장소 지침이다. 일반 규칙(응답 언어, 검증 위생,
커밋 예절 등)은 각 도구의 전역 설정(`~/.claude`, `~/.codex`)에 있으므로 여기에는
**이 저장소에서만 성립하는 사실**만 적는다. 규칙이 바뀌는 PR에서 이 파일도 같이 고친다.
루트 `CLAUDE.md`는 이 파일을 `@AGENTS.md`로 import하는 셔임이며 내용은 여기에만 둔다.

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
  - `docs/` — 가이드·스키마. `docs/01-plan`, `02-design`, `04-report`, `05-runbook`,
    `docs/superpowers/`는 gitignore된 로컬 작업 산출물이다. 확정 계약은 README,
    CHANGELOG, 정식 가이드, 테스트에만 둔다.
- `HANDOFF.*.md`(루트)는 세션 스냅샷이며 추적하지 않는다.

## 빌드

```bash
git submodule update --init --recursive   # 반드시 먼저
./build.sh                                # 개발 빌드 (pre-build 게이트 생략)
./build.sh --release                      # 정규 빌드 (게이트 포함, CI와 동일)
```

- 서브모듈이 없으면 **에러 없이** bridge 없는 패키지가 나온다(`opt/wlan/config/no-wbridge`
  표식). 정규 빌드 전에 `wlan-bridge/wbridge/`가 있는지 확인한다.
- `build.sh`는 워킹트리의 파일 모드를 그대로 싣는다. 릴리스 빌드 전 `git status`가
  클린한지 확인한다.
- 산출물: `release/wlan.deb`, `release/wlan-proc-<version>.deb`, `release/wlan-package.tar`
  (허용목록 기반 소스 아카이브), `release/SHA256SUMS`. `release/`는 gitignore 대상이다.
- `.ko`, `mlanutl_*`, `opcd` 등 바이너리는 다른 저장소에서 빌드해 `dist/`에 staging하며
  git에 넣지 않는다. 드라이버 교체 시에는 검증한 불변 ref로
  `scripts/gen_driver_manifest.sh --write <wlan-driver-v2 repo> <ref>`를 실행해
  `dist/wlan/opt/wlan/driver/DRIVER_MANIFEST.md`를 갱신한다(수동 편집 금지).

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
- shellcheck CI는 `build.sh`와 `dist/wlan/DEBIAN/*`만 보고 error 수준만 막는다.
- **CI 초록불은 빌드 가능을 뜻하지 않는다.** `Build and Test`는 ARM64 러너와 서브모듈
  토큰이 없으면 실제 빌드를 건너뛰고 success로 끝난다. .deb가 나오는지는 로컬
  `./build.sh --release`로만 판정한다.

## 파일을 추가·변경할 때 같이 고칠 것

- **새 payload 파일·새 테스트** → `scripts/source_archive_manifest.txt`에 추가.
  허용목록에 없으면 소스 아카이브에서 빠지고 `test_source_archive_manifest.py`가 실패한다.
- **systemd `ExecStart=`·udev `RUN+=`가 직접 실행하는 파일** → git 인덱스 모드가 100755
  여야 한다. 판정 규칙은 `scripts/exec_bit_targets.py` 한 곳이고, pre-commit 훅
  (`git config core.hooksPath scripts/git-hooks`)이 +x를 보정하며 CI
  `test_unit_execstart_exec_bit.py`가 강제한다. 100644로 실리면 타겟에서 `203/EXEC`로
  조용히 죽는다.
- **`wifi_init_conf.json` 키·기본값** → 템플릿, `docs/wifi_init_conf.schema.json`,
  `docs/wifi_init_conf_guide.md`가 함께 움직인다. `gen_config_defaults.py --check`로 확인.
  기본값 변경은 postinst의 json_merge가 기존 값을 보존하므로 **신규 설치와 공장초기화에만**
  적용된다. 기존 기기에 반영하려면 postinst 마이그레이션이 필요하다.
- **설정을 쓰는 코드**는 파일에 찍힌 글자가 아니라 소비자(wpa_supplicant, systemd 등)에
  실제로 먹여 검증한다. 일부 키는 스크립트가 아니라 패치된 wpa_supplicant 바이너리가
  JSON을 직접 읽으므로, 소비처를 찾을 때 바이너리 `strings`도 본다.
- **CHANGELOG.md** — 버전별 절, 첫 줄에 SemVer 수준과 한 줄 요약, 이슈·PR 번호 병기.

## 버전·릴리스·PR

- 버전은 **패치 자리만** 올린다(0.6.9 → 0.6.10). 동작 계약이 바뀌어도 minor를 올리지
  않는다. minor는 여러 PR을 묶는 릴리스 단위이며, 필요해 보이면 사용자에게 먼저 묻는다.
- master 직접 커밋 금지. 브랜치 → PR → 리뷰 → 머지.
- 자동 리뷰(Claude·Gemini·OpenCode)는 PR에 `review:request` 라벨을 붙여야 돈다. `review:skip`과
  같이 쓰지 않는다. **Codex 리뷰는 라벨로 안 돌고 `@codex review` 코멘트가 따로 필요하다.**
- 리뷰 결과 확인 시 리뷰된 커밋 SHA와 provider가 실제로 실행됐는지 본다. `Workflow Skipped`
  job의 success는 리뷰가 아니다. 리뷰 job이 success인데 코멘트가 없으면 `gh run view --log`로
  필터링된 지적을 복구한다.
- 리뷰 의견은 실제 결함만 수용한다. 가설·과설계·스타일 지적은 근거를 적고 거절·보류한다.
- 서브모듈 포인터만 올리는 PR은 코드 diff가 없으므로 리뷰 게이트 없이 머지한다.

## 타겟 배포·검증

- 배포는 `scp release/wlan.deb` → `dpkg -i`. 타겟 주소·접속 정보는 이 파일에 적지 않는다.
- 업그레이드 검증은 `wifi_init` 하나가 아니라 **자식 유닛**(`wifi_logger`, `wifi_roam`,
  `wifi_checker`, `wifi_bridge` 등)이 active인지부터 본다. 통신은 되는데 관측·로밍만 죽는
  장애가 조용하다.
- 데몬 바이너리 교체는 설치 파일이 아니라 **구동 중 프로세스의 `/proc/<pid>/exe`**로 확인한다.
  postinst가 restart하지 않으면 옛 바이너리가 계속 돈다.
- 공장초기화 후 `eth0`=`192.168.1.1/24`는 **복구 경로**다. 기본 SSID AP가 없어 무선이 안
  붙는 것은 정상이며, 이 값을 사이트 주소로 바꾸지 않는다.
- 링크 상태 판정에 `iw link`를 쓰지 않는다(연결 중에도 `Not connected.`를 낸다).
  `dist/wlan/usr/local/scripts/wlan_link_lib.sh`를 쓴다.
- 타겟 상태를 바꾸는 QA 도구(`scripts/qa/`)는 복원 계약을 가지며, 그 계약은
  `scripts/qa/test_*.py`가 고정한다. 도구를 고치면 테스트도 같이 돈다.

## 작업 환경 주의

- 이 체크아웃은 여러 에이전트 세션이 **같은 `.git`을 공유**한다. 다른 세션이 작업 중이면
  master에 커밋을 얹지 말고 `git worktree add`로 격리한다. master에 얹힌 커밋은 다른
  세션의 push에 실려 리뷰 없이 나갈 수 있다.
- `.omc/`, `.omx/`, `.claude/`, `.base/` 등 도구 상태 디렉터리는 gitignore 대상이며
  패키지·아카이브에 들어가지 않는다. 이 파일(`AGENTS.md`)도 소스 아카이브 허용목록에
  넣지 않는다.
