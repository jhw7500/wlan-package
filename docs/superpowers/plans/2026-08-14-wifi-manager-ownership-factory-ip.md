# config.json Retirement, nginx Precondition Boundary, and Factory IP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

> **Superseded framing:** this plan originally assumed a separate `wifi_manager`
> package owned `config.json` and nginx, and set out to remove wlan-proc's
> `config.json` deletion, its release/CI `config.json` checks, and Factory Reset's
> nginx preflight/enable/postcondition. That assumption was dropped: no
> `wifi_manager` package/source exists in this workspace, and target inspection
> found neither `/usr/local/etc/config.json` nor a `wifi_manager` package. The
> tasks below describe the final, confirmed contract instead.

**Goal:** Release wlan-proc 0.5.4 that (a) keeps `config.json` a fully retired,
actively-removed artifact with release/CI enforcement against its reintroduction,
(b) treats nginx as a standard product-image precondition that Factory Reset no
longer requires but still re-enables to undo damage wlan-proc itself caused in
past releases, and (c) restores the Factory Reset eth0 address to
`192.168.1.1/24`.

**Architecture:** `config.json` retirement is enforced end-to-end: postinst
deletes the active leftover on upgrade, and package/CI/release validators reject
any build that reintroduces it. nginx is decoupled from Factory Reset's required-
unit gate (its absence or bad state must not fail Factory Reset) while the single
`customctl enable nginx` recovery line stays, because 0.4.4/0.5.0 Factory Reset
persistently disabled nginx via unit-file manipulation and affected devices do
not self-heal. Preserve the existing atomic Factory Reset payload transaction and
change only its eth0 source contract. Keep historical 0.5.2 documentation intact
while describing the corrected current behavior in 0.5.4.

**Tech Stack:** Bash, jq, systemd unit policy, Debian packaging (dpkg-deb),
pytest, shell test harnesses, Markdown.

## Global Constraints

- wlan-proc must actively remove `/usr/local/etc/config.json` on upgrade (`rm -f`
  in postinst) and must never reinstall, restore, or merge it. Package/CI/release
  validators must keep rejecting any build that packages it.
- wlan-proc must not preflight or postcondition-verify nginx as a required
  Factory Reset unit, but must keep the single `customctl enable nginx` recovery
  line in `factory_reset.sh`.
- Factory Reset eth0 must be exactly 192.168.1.1/24.
- Package version must be exactly 0.5.4.
- Historical 0.5.2 changelog text remains unchanged.
- No target deployment is part of this plan.
- Every production behavior change follows RED -> GREEN -> regression
  verification.

---

## File Map

- dist/wlan/DEBIAN/postinst: keep/restore the active `config.json` deletion
  (`rm -f -- /usr/local/etc/config.json`) on upgrade.
- dist/wlan/usr/local/scripts/factory_reset.sh: keep the `customctl enable nginx`
  recovery line; nginx is not added back to any required-unit list here.
- dist/wlan/usr/local/scripts/wifi_factory_reset_lib.sh: keep `nginx.service` out
  of `FACTORY_REQUIRED_UNITS`.
- dist/wlan/usr/local/scripts/wifi_init_config_test.sh: keep the full retirement
  assertion set (legacy path absence, no legacy references in
  wifi_init.sh/wifi.sh/config library/factory_reset.sh, postinst deletion
  present, and the overlay-ignored `run_case` pair).
- dist/wlan/usr/local/scripts/wifi_factory_reset_test.sh: assert both that nginx
  is not a required unit and that `factory_reset.sh` still contains
  `customctl enable nginx`; assert the 192.168.1.1/24 factory eth0 source.
- dist/wlan/opt/wlan/config/systemd/network/22-eth0.network: restore the product
  factory address.
- scripts/validate_release.sh: keep the `release gate: retired config.json
  packaged` check and the eth0 contract.
- scripts/validate_release_test.sh: keep the `config.json` negative fixtures
  (regular file, symlink) and the wrong-IP fixture.
- .github/workflows/build-test.yml: keep the dedicated
  `dpkg-deb -c | grep usr/local/etc/config.json` package inspection.
- dist/wlan/DEBIAN/control, README.md, CHANGELOG.md, docs/wifi_init_conf_guide.md:
  publish 0.5.4 behavior with the corrected ownership framing.
- release/*: rebuild validated 0.5.4 artifacts.

---

### Task 1: Keep the config.json Retirement Contract Enforced

**Files:**
- Modify: dist/wlan/usr/local/scripts/wifi_init_config_test.sh:4-200
- Modify: dist/wlan/DEBIAN/postinst:324-327

**Interfaces:**
- Consumes: postinst as a text fixture; legacy config path constants.
- Produces: an upgrade path that actively removes `config.json` and a test suite
  that fails if that removal, or any legacy reference, regresses.

- [x] **Step 1: Write/keep the retirement tests**

In `wifi_init_config_test.sh`, keep `LEGACY_CONFIG_TEMPLATE`, `expect_path_absent`,
the legacy-reference-absence checks in `wifi_init.sh`/`wifi.sh`/the config
library/`factory_reset.sh`, and the overlay-ignored `run_case` pair:

~~~bash
expect_file_not_contains "wifi_init.sh has no legacy config path" "$WIFI_INIT_SH" '/usr/local/etc/config.json'
expect_file_not_contains "wifi.sh has no legacy config path" "$WIFI_SH" '/usr/local/etc/config.json'
expect_file_not_contains "config library has no overlay branch" "$LIB" 'overlay_json'
expect_file_not_contains "factory reset does not restore legacy config" "$FACTORY_RESET_SH" '/opt/wlan/config/config.json'
expect_path_absent "legacy config template is not packaged" "$LEGACY_CONFIG_TEMPLATE"
expect_file_contains "upgrade removes retired active config" "$POSTINST" 'rm -f -- /usr/local/etc/config.json'
~~~

~~~bash
run_case "legacy overlay is ignored" \
    '{"mlan0":{"enabled":false,"Frequency":"5GHz"}}' \
    '{"mlan0":{"enabled":true,"Frequency":"2.4GHz"}}' \
    "false" "5GHz"

run_case "legacy partial overlay is ignored" \
    '{"mlan0":{"enabled":false,"Frequency":"5GHz"}}' \
    '{"mlan0":{"enabled":true}}' \
    "false" "5GHz"
~~~

- [x] **Step 2: Run focused tests and verify GREEN**

~~~bash
bash dist/wlan/usr/local/scripts/wifi_init_config_test.sh
~~~

Verified: `46 passed, 0 failed` (includes the retirement assertions and the
radio mode/bw helper cases in the same file).

- [x] **Step 3: Confirm the lifecycle deletion is present**

`postinst` keeps:

~~~bash
# config.json 은 호환 유지 대상이 아니라 완전 제거 대상이다. 0.5.1 개발 중 임시
# 도입된 overlay 설정원이며 현재 소비 코드가 없다. 업그레이드 장비에서도
# wifi_init_conf.json 하나만 남도록 active 잔재를 제거한다.
rm -f -- /usr/local/etc/config.json
~~~

- [x] **Step 4: Verify GREEN**

~~~bash
bash dist/wlan/usr/local/scripts/wifi_init_config_test.sh
bash -n dist/wlan/DEBIAN/postinst
~~~

Verified: focused tests and syntax pass.

- [ ] **Step 5: Commit**

~~~bash
git add dist/wlan/DEBIAN/postinst \
  dist/wlan/usr/local/scripts/wifi_init_config_test.sh
git commit -m "fix: keep config.json retirement enforced on upgrade"
~~~

---

### Task 2: Separate nginx as a Factory Reset Precondition, Keep the Recovery Enable

**Files:**
- Modify: dist/wlan/usr/local/scripts/wifi_factory_reset_lib.sh:20-24
- Modify: dist/wlan/usr/local/scripts/factory_reset.sh:213-217
- Modify: dist/wlan/usr/local/scripts/wifi_factory_reset_test.sh:165-181

**Interfaces:**
- Consumes: `FACTORY_REQUIRED_UNITS`; `factory_reset.sh` as a text fixture.
- Produces: a Factory Reset that does not fail when nginx is absent or broken,
  while still repairing the specific persistent-disable damage caused by
  0.4.4/0.5.0 Factory Reset.

- [x] **Step 1: Write/keep the precondition-separation tests**

In `wifi_factory_reset_test.sh`, keep the required-unit loop scoped to WLAN-owned
units only:

~~~bash
for unit in wifi-stack.target wifi_apply_enabled.service wifi_init.service; do
    [ -e "$STATE/$unit" ] && pass "$unit enabled" || fail "$unit not enabled"
done
~~~

And assert both halves of the nginx contract:

~~~bash
if grep -q 'nginx' "$LIB"; then
    fail "nginx is not a required factory unit"
else
    pass "nginx is not a required factory unit"
fi
if grep -q '^[[:space:]]*customctl enable nginx$' "$FACTORY_SCRIPT"; then
    pass "factory reset re-enables nginx disabled by past resets"
else
    fail "factory reset re-enables nginx disabled by past resets"
fi
~~~

- [x] **Step 2: Run focused tests and verify GREEN**

~~~bash
bash dist/wlan/usr/local/scripts/wifi_factory_reset_test.sh
~~~

Verified: `105 passed, 0 failed`, including "nginx is not a required factory
unit" and "factory reset re-enables nginx disabled by past resets".

- [x] **Step 3: Confirm the lifecycle code matches**

`FACTORY_REQUIRED_UNITS` in `wifi_factory_reset_lib.sh` stays WLAN-only:

~~~bash
FACTORY_REQUIRED_UNITS=(
    wifi-stack.target
    wifi_apply_enabled.service
    wifi_init.service
)
~~~

`factory_reset.sh` keeps the recovery line, documented as scoped to undoing
wlan-proc's own past damage:

~~~bash
# nginx 자체는 이미지/wifi_manager 소유라 Factory Reset 의 필수 유닛이 아니다
# (FACTORY_REQUIRED_UNITS 에서 제외). 다만 0.5.0 이하 factory_reset 이
# `customctl disable nginx` 로 영속 disable 시킨 기기는 스스로 복구되지 않으므로
# (enable/disable 은 유닛 파일 조작이라 영속), wlan-proc 이 만든 피해만 여기서 되돌린다.
customctl enable nginx
~~~

- [x] **Step 4: Verify GREEN**

~~~bash
bash dist/wlan/usr/local/scripts/wifi_factory_reset_test.sh
bash -n dist/wlan/usr/local/scripts/factory_reset.sh \
  dist/wlan/usr/local/scripts/wifi_factory_reset_lib.sh
~~~

Verified: focused tests and syntax pass.

- [ ] **Step 5: Commit**

~~~bash
git add dist/wlan/usr/local/scripts/factory_reset.sh \
  dist/wlan/usr/local/scripts/wifi_factory_reset_lib.sh \
  dist/wlan/usr/local/scripts/wifi_factory_reset_test.sh
git commit -m "fix: drop nginx from Factory Reset required units, keep recovery enable"
~~~

---

### Task 3: Keep the config.json Package/CI/Release Rejection Gates

**Files:**
- Modify: scripts/validate_release.sh:525-533
- Modify: scripts/validate_release_test.sh:149-172
- Modify: .github/workflows/build-test.yml:156-164

**Interfaces:**
- Consumes: exact Debian payload manifest as the general integrity contract.
- Produces: a filename-specific release/CI policy that fails any build
  reintroducing `config.json`.

- [x] **Step 1: Keep dedicated validator policy**

`validate_release.sh` keeps:

~~~bash
if grep -Eq '(usr/local/etc|opt/wlan/config)/config\.json/?$' "$names"; then
    echo "release gate: retired config.json packaged" >&2
    return 1
fi
~~~

- [x] **Step 2: Keep dedicated negative fixtures**

`validate_release_test.sh` keeps a regular-file fixture and a symlink fixture,
each expected to be rejected:

~~~bash
make_tree
mkdir -p "$PKG/opt/wlan/config"
printf '{}\n' > "$PKG/opt/wlan/config/config.json"
build "$WORK/config.deb"
if bash "$VALIDATE" package "$WORK/config.deb" >/dev/null 2>&1; then
    echo "FAIL: retired config.json accepted" >&2; exit 1
fi

make_tree
ln -s /etc/passwd "$PKG/opt/wlan/config/config.json"
build "$WORK/config-symlink.deb"
if bash "$VALIDATE" package "$WORK/config-symlink.deb" >/dev/null 2>&1; then
    echo "FAIL: retired config.json symlink accepted" >&2; exit 1
fi
~~~

Each fixture rebuilds the tree first (`make_tree`) because the preceding
Architecture fixture leaves `DEBIAN/control` mutated; without the rebuild the
Architecture gate — not the config.json gate — would be the one rejecting the
package, which would hide a regression in the config.json check.

- [x] **Step 3: Keep the CI package inspection**

`.github/workflows/build-test.yml` keeps:

~~~bash
if dpkg-deb -c release/wlan.deb | grep -q "usr/local/etc/config.json"; then
  echo "Error: config.json should not be packaged by wlan-package"
  exit 1
fi
~~~

- [x] **Step 4: Verify GREEN**

~~~bash
bash -n scripts/validate_release.sh scripts/validate_release_test.sh
bash scripts/validate_release_test.sh
~~~

Verified: `release gate self-test: PASS`.

- [ ] **Step 5: Commit**

~~~bash
git add scripts/validate_release.sh scripts/validate_release_test.sh \
  .github/workflows/build-test.yml
git commit -m "test: keep config.json reintroduction gate enforced"
~~~

---

### Task 4: Restore the Factory eth0 Address

**Files:**
- Modify: dist/wlan/opt/wlan/config/systemd/network/22-eth0.network:11
- Modify: dist/wlan/usr/local/scripts/wifi_factory_reset_test.sh:490-496
- Modify: scripts/validate_release.sh:7-8
- Modify: scripts/validate_release_test.sh:332-338

**Interfaces:**
- Consumes: existing atomic Factory Reset required-payload transaction.
- Produces: packaged and restored eth0 factory address 192.168.1.1/24.

- [x] **Step 1: Assert the product default**

~~~bash
if [ "$(awk -F= '$1 == "Address" { print $2 }' "$FACTORY_ETH0_TEMPLATE")" = "192.168.1.1/24" ]; then
    pass "factory eth0 address is 192.168.1.1/24"
else
    fail "factory eth0 address is not 192.168.1.1/24"
fi
~~~

The negative package fixture in `validate_release_test.sh` mutates a packaged
`192.168.1.1/24` copy to `192.168.214.5/24` and expects the validator to reject
it.

- [x] **Step 2: Set the source and release contract**

`22-eth0.network` carries `Address=192.168.1.1/24`; `validate_release.sh` sets:

~~~bash
FACTORY_ETH0_ADDRESS="192.168.1.1/24"
~~~

- [x] **Step 3: Verify GREEN**

~~~bash
bash dist/wlan/usr/local/scripts/wifi_factory_reset_test.sh
bash scripts/validate_release_test.sh
~~~

Verified: both suites pass ("factory eth0 address is 192.168.1.1/24" passes in
`wifi_factory_reset_test.sh`; `validate_release_test.sh` self-test passes).

- [ ] **Step 4: Commit**

~~~bash
git add dist/wlan/opt/wlan/config/systemd/network/22-eth0.network \
  dist/wlan/usr/local/scripts/wifi_factory_reset_test.sh \
  scripts/validate_release.sh scripts/validate_release_test.sh
git commit -m "fix: restore factory eth0 address"
~~~

(Already committed as `f4593ad fix: restore factory eth0 address`; this task's
scope is otherwise unchanged from that commit.)

---

### Task 5: Publish the 0.5.4 Contract

**Files:**
- Modify: dist/wlan/DEBIAN/control:2,44
- Modify: README.md:151-163
- Modify: CHANGELOG.md:4-12
- Modify: docs/wifi_init_conf_guide.md

**Interfaces:**
- Consumes: Tasks 1-4 behavior.
- Produces: version 0.5.4 and current operator instructions with the corrected
  ownership framing (config.json retirement kept, nginx precondition separated,
  eth0 restored) instead of the superseded "wifi_manager owns config.json/nginx"
  framing.

- [x] **Step 1: Update metadata**

`dist/wlan/DEBIAN/control` sets `Version: 0.5.4` and describes the corrected
scope:

~~~text
 0.5.4 : Factory Reset eth0 공장주소 192.168.1.1 복원, nginx 필수유닛 제외(과거 리셋 disable 복구 enable은 유지), config.json 완전제거 계약 유지
~~~

- [x] **Step 2: Update README**

README's install section (current version references at lines 151-163)
describes the actual contract, not a `wifi_manager`-owned split:

~~~markdown
런타임 설정원은 `/usr/local/etc/wifi_init_conf.json` 하나다. `config.json`은
완전 제거 대상이며 업그레이드 시 active 잔재가 삭제되고, 패키지에 다시
포함되면 release gate와 CI가 실패한다.

nginx는 표준 제품 이미지가 제공하는 선행조건이라 Factory Reset의 필수 유닛이
아니다. 없거나 비정상이어도 Factory Reset은 실패하지 않는다.

Factory Reset의 `eth0` 공장 기본값은 `192.168.1.1/24`다. 일반 패키지
업그레이드에서는 현재 active 네트워크 설정을 보존하므로, 이 값은 신규 설치와
Factory Reset에만 적용된다. 사이트별 유선 관리 주소는 설치 후 변경해서 쓴다.
~~~

- [x] **Step 3: Add changelog entry**

`CHANGELOG.md`'s 0.5.4 entry describes the corrected scope, not the superseded
`wifi_manager`-ownership framing:

~~~markdown
## 0.5.4 (2026-08-14)

> SemVer **patch** — nginx를 Factory Reset 필수 유닛에서 분리하고, Factory Reset 유선 공장 주소를 제품 기본값으로 되돌린다. `config.json` 완전 제거 계약은 유지한다.

### Factory Reset 소유권·기본값 정정

- Factory Reset의 `eth0` 공장 기본 주소를 임시 타겟 검증값 `192.168.214.5/24`에서 제품값 `192.168.1.1/24`로 복원한다. 일반 패키지 업그레이드는 active 네트워크 설정을 계속 보존하므로, 이 값은 신규 설치와 Factory Reset에만 적용된다.
- nginx는 표준 제품 이미지가 제공하는 선행조건이므로 `FACTORY_REQUIRED_UNITS`에서 제외한다. nginx가 없거나 비정상이어도 Factory Reset은 실패하지 않는다.
- 다만 0.5.0 이하의 Factory Reset이 `customctl disable nginx`로 영속 disable 시킨 기기는 스스로 복구되지 않으므로, `customctl enable nginx`는 그대로 유지한다. `wlan-proc`이 만든 피해만 되돌리는 범위다.
- `/usr/local/etc/config.json`은 호환 유지 대상이 아니라 완전 제거 대상이라는 기존 계약을 유지한다. 업그레이드 시 active 잔재를 삭제하고, 패키지·CI·release gate의 재유입 금지 검사도 유지한다.
~~~

Do not edit historical 0.5.2 text.

- [x] **Step 4: Update operator guide**

`docs/wifi_init_conf_guide.md` reflects `root@192.168.1.1` as the current
Factory Reset reconnect address; `192.168.214.5` is not shown as a current
product default.

- [x] **Step 5: Verify docs**

~~~bash
grep -n '^Version: 0.5.4$' dist/wlan/DEBIAN/control
grep -n '^## 0.5.4 ' CHANGELOG.md
grep -n 'root@192.168.1.1' docs/wifi_init_conf_guide.md
grep -c 'wifi_manager' README.md CHANGELOG.md
~~~

Expected: current contract references are found, and the corrected README/
CHANGELOG text no longer frames `config.json`/nginx as `wifi_manager`-owned.

- [ ] **Step 6: Commit**

~~~bash
git add dist/wlan/DEBIAN/control README.md CHANGELOG.md \
  docs/wifi_init_conf_guide.md
git commit -m "docs: correct wlan-proc 0.5.4 config.json/nginx contract"
~~~

(README/CHANGELOG/control already carry these corrected contents as
uncommitted working-tree changes at the time this plan was rewritten; this step
still needs to be run to land them.)

---

### Task 6: Full Build and Release Validation

**Files:**
- Regenerate: release/wlan.deb
- Regenerate: release/wlan-proc-0.5.4.deb
- Regenerate: release/wlan-package.tar
- Regenerate: release/SHA256SUMS

**Interfaces:**
- Consumes: all source, tests, metadata, and exact manifests.
- Produces: coherent validated 0.5.4 release artifacts.

- [x] **Step 1: Run focused regressions**

~~~bash
bash dist/wlan/usr/local/scripts/wifi_init_config_test.sh
bash dist/wlan/usr/local/scripts/wifi_factory_reset_test.sh
bash scripts/validate_release_test.sh
~~~

Verified: `46 passed, 0 failed`; `105 passed, 0 failed`; `release gate
self-test: PASS`.

- [ ] **Step 2: Run complete build**

~~~bash
./build.sh
~~~

Expected: all pytest/shell suites and candidate package/archive gates pass.
Not yet re-run against the corrected working tree in this session.

- [ ] **Step 3: Validate published artifacts**

~~~bash
bash scripts/validate_release.sh package release/wlan.deb
bash scripts/validate_release.sh package release/wlan-proc-0.5.4.deb
bash scripts/package_tar_test.sh release/wlan-package.tar
(cd release && sha256sum -c SHA256SUMS)
dpkg-deb -f release/wlan.deb Package Version Architecture
rm -rf /tmp/wlan-0.5.4-check
dpkg-deb -x release/wlan.deb /tmp/wlan-0.5.4-check
grep -Fx 'Address=192.168.1.1/24' \
  /tmp/wlan-0.5.4-check/opt/wlan/config/systemd/network/22-eth0.network
! dpkg-deb -c release/wlan.deb | grep -q 'usr/local/etc/config.json'
~~~

Expected: metadata is wlan-proc / 0.5.4 / arm64, eth0 source is 1.1/24, and
`config.json` is absent from the packaged payload.

- [ ] **Step 4: Verify the corrected contract end-to-end**

~~~bash
grep -Fq 'rm -f -- /usr/local/etc/config.json' dist/wlan/DEBIAN/postinst
! grep -q 'nginx' dist/wlan/usr/local/scripts/wifi_factory_reset_lib.sh
grep -Eq '^[[:space:]]*customctl enable nginx$' dist/wlan/usr/local/scripts/factory_reset.sh
grep -Fq 'release gate: retired config.json packaged' scripts/validate_release.sh
~~~

Expected: postinst still deletes the active `config.json` leftover, nginx is
absent from `FACTORY_REQUIRED_UNITS`, `factory_reset.sh` still re-enables
nginx, and the release gate still rejects packaged `config.json`.

- [ ] **Step 5: Review and commit artifacts**

~~~bash
git diff --check
git status --short
git diff --stat master...HEAD
git add release/wlan.deb release/wlan-proc-0.5.4.deb \
  release/wlan-package.tar release/SHA256SUMS
git commit -m "release: build wlan-proc 0.5.4"
~~~

- [ ] **Step 6: Record final evidence**

~~~bash
git status --short --branch
git log --oneline master..HEAD
sha256sum release/wlan.deb release/wlan-proc-0.5.4.deb \
  release/wlan-package.tar
~~~

Expected: clean branch and matching generic/versioned Debian package hashes.
