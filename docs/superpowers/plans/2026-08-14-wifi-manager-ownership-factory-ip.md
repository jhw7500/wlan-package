# wifi_manager Ownership and Factory IP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Release wlan-proc 0.5.4 without touching wifi_manager-owned config.json or nginx, and restore the Factory Reset eth0 address to 192.168.1.1/24.

**Architecture:** Enforce a strict package ownership boundary: installation and Factory Reset retain only WLAN-owned resources, while wifi_manager resources are absent from lifecycle and release-specific validation. Preserve the existing atomic Factory Reset payload transaction and change only its eth0 source contract. Keep historical 0.5.2 documentation intact while describing the corrected current behavior in 0.5.4.

**Tech Stack:** Bash, jq, systemd unit policy, Debian packaging (dpkg-deb), pytest, shell test harnesses, Markdown.

## Global Constraints

- wlan-proc must not read, write, delete, restore, or validate /usr/local/etc/config.json or /opt/wlan/config/config.json.
- wlan-proc must not preflight, enable, or post-verify nginx.
- Factory Reset eth0 must be exactly 192.168.1.1/24.
- Package version must be exactly 0.5.4.
- Historical 0.5.2 changelog text remains unchanged.
- No target deployment is part of this plan.
- Every production behavior change follows RED → GREEN → regression verification.

---

## File Map

- dist/wlan/DEBIAN/postinst: remove destructive active config.json deletion.
- dist/wlan/usr/local/scripts/factory_reset.sh: remove nginx enable ownership.
- dist/wlan/usr/local/scripts/wifi_factory_reset_lib.sh: remove nginx from required units.
- dist/wlan/usr/local/scripts/wifi_init_config_test.sh: replace retirement assertions with a narrow non-interference contract.
- dist/wlan/usr/local/scripts/wifi_factory_reset_test.sh: test WLAN-only service ownership and the 192.168.1.1/24 factory source.
- dist/wlan/opt/wlan/config/systemd/network/22-eth0.network: restore the product factory address.
- scripts/validate_release.sh: change the eth0 contract and remove dedicated config.json policy.
- scripts/validate_release_test.sh: remove dedicated config.json fixtures and invert the wrong-IP fixture.
- .github/workflows/build-test.yml: remove dedicated config.json package inspection.
- dist/wlan/DEBIAN/control, README.md, CHANGELOG.md, docs/wifi_init_conf_guide.md: publish 0.5.4 behavior.
- release/*: rebuild validated 0.5.4 artifacts.

---

### Task 1: Establish the wifi_manager Non-Interference Contract

**Files:**
- Modify: dist/wlan/usr/local/scripts/wifi_init_config_test.sh:4-165
- Modify: dist/wlan/usr/local/scripts/wifi_factory_reset_test.sh:155-185
- Modify: dist/wlan/DEBIAN/postinst:326
- Modify: dist/wlan/usr/local/scripts/factory_reset.sh:213-216
- Modify: dist/wlan/usr/local/scripts/wifi_factory_reset_lib.sh:20-26

**Interfaces:**
- Consumes: postinst and Factory Reset scripts as text fixtures; FACTORY_REQUIRED_UNITS.
- Produces: lifecycle code that leaves wifi_manager resources untouched.

- [ ] **Step 1: Write failing ownership tests**

In wifi_init_config_test.sh, remove LEGACY_CONFIG_TEMPLATE, expect_path_absent, legacy package/docs absence checks, and overlay-only cases. Retain the base wifi_init_conf helper case and add:

~~~bash
expect_file_not_contains     "postinst preserves wifi_manager active config"     "$POSTINST" '/usr/local/etc/config.json'
expect_file_not_contains     "factory reset does not own wifi_manager config"     "$FACTORY_RESET_SH" '/opt/wlan/config/config.json'
~~~

In wifi_factory_reset_test.sh, make the expected required-unit loop:

~~~bash
for unit in wifi-stack.target wifi_apply_enabled.service wifi_init.service; do
    [ -e "$STATE/$unit" ] && pass "$unit enabled" || fail "$unit not enabled"
done
~~~

Delete nginx-specific missing-unit/postcondition cases and add:

~~~bash
if grep -q 'nginx' "$LIB" "$FACTORY_SCRIPT"; then
    fail "Factory Reset does not own wifi_manager nginx"
else
    pass "Factory Reset does not own wifi_manager nginx"
fi
~~~

- [ ] **Step 2: Run focused tests and verify RED**

~~~bash
bash dist/wlan/usr/local/scripts/wifi_init_config_test.sh
bash dist/wlan/usr/local/scripts/wifi_factory_reset_test.sh
~~~

Expected: postinst deletion and Factory Reset nginx references cause the new tests to fail.

- [ ] **Step 3: Implement minimal lifecycle changes**

Delete the /usr/local/etc/config.json removal from postinst. Set required units to:

~~~bash
FACTORY_REQUIRED_UNITS=(
    wifi-stack.target
    wifi_apply_enabled.service
    wifi_init.service
)
~~~

Delete the nginx customctl enable block and its ownership comment from factory_reset.sh.

- [ ] **Step 4: Verify GREEN**

~~~bash
bash dist/wlan/usr/local/scripts/wifi_init_config_test.sh
bash dist/wlan/usr/local/scripts/wifi_factory_reset_test.sh
bash -n dist/wlan/DEBIAN/postinst   dist/wlan/usr/local/scripts/factory_reset.sh   dist/wlan/usr/local/scripts/wifi_factory_reset_lib.sh
~~~

Expected: focused tests and syntax pass.

- [ ] **Step 5: Commit**

~~~bash
git add dist/wlan/DEBIAN/postinst   dist/wlan/usr/local/scripts/factory_reset.sh   dist/wlan/usr/local/scripts/wifi_factory_reset_lib.sh   dist/wlan/usr/local/scripts/wifi_init_config_test.sh   dist/wlan/usr/local/scripts/wifi_factory_reset_test.sh
git commit -m "fix: defer wifi_manager resources to owner package"
~~~

---

### Task 2: Remove Dedicated config.json Package Inspection

**Files:**
- Modify: scripts/validate_release.sh:525-533
- Modify: scripts/validate_release_test.sh:137-149
- Modify: .github/workflows/build-test.yml:156-164
- Test: scripts/validate_release_test.sh

**Interfaces:**
- Consumes: exact Debian payload manifest as the general integrity contract.
- Produces: no filename-specific release policy for wifi_manager config.json.

- [ ] **Step 1: Remove dedicated test fixtures**

Delete regular-file and symlink config.json fixtures from validate_release_test.sh. Delete the dpkg-deb config.json check from build-test.yml.

- [ ] **Step 2: Confirm obsolete production policy remains**

~~~bash
grep -n 'retired config.json' scripts/validate_release.sh
~~~

Expected: one match.

- [ ] **Step 3: Remove dedicated validator policy**

Delete:

~~~bash
if grep -Eq '(usr/local/etc|opt/wlan/config)/config\.json/?$' "$names"; then
    echo "release gate: retired config.json packaged" >&2
    return 1
fi
~~~

Do not add config.json to payload-manifest.txt.

- [ ] **Step 4: Verify cleanup and self-test**

~~~bash
! grep -RIn -E 'retired config.json|config.json should not be packaged'   scripts/validate_release.sh scripts/validate_release_test.sh   .github/workflows/build-test.yml
bash -n scripts/validate_release.sh scripts/validate_release_test.sh
bash scripts/validate_release_test.sh
~~~

Expected: no dedicated policy matches and self-test passes.

- [ ] **Step 5: Commit**

~~~bash
git add scripts/validate_release.sh scripts/validate_release_test.sh   .github/workflows/build-test.yml
git commit -m "test: remove wifi_manager payload policy"
~~~

---

### Task 3: Restore the Factory eth0 Address

**Files:**
- Modify: dist/wlan/opt/wlan/config/systemd/network/22-eth0.network:11
- Modify: dist/wlan/usr/local/scripts/wifi_factory_reset_test.sh:490-496
- Modify: scripts/validate_release.sh:7-8
- Modify: scripts/validate_release_test.sh:332-338

**Interfaces:**
- Consumes: existing atomic Factory Reset required-payload transaction.
- Produces: packaged and restored eth0 factory address 192.168.1.1/24.

- [ ] **Step 1: Change tests to the product default**

Use this factory source assertion:

~~~bash
if [ "$(awk -F= '$1 == "Address" { print $2 }' "$FACTORY_ETH0_TEMPLATE")" = "192.168.1.1/24" ]; then
    pass "factory eth0 address is 192.168.1.1/24"
else
    fail "factory eth0 address is not 192.168.1.1/24"
fi
~~~

Change the negative package fixture to:

~~~bash
sed -i 's/Address=192\.168\.1\.1\/24/Address=192.168.214.5\/24/'     "$PKG/opt/wlan/config/systemd/network/22-eth0.network"
~~~

- [ ] **Step 2: Run tests and verify RED**

~~~bash
bash dist/wlan/usr/local/scripts/wifi_factory_reset_test.sh
bash scripts/validate_release_test.sh
~~~

Expected: source and validator still require 214.5, so tests fail.

- [ ] **Step 3: Change source and release contract**

Set Address=192.168.1.1/24 in 22-eth0.network and set:

~~~bash
FACTORY_ETH0_ADDRESS="192.168.1.1/24"
~~~

in validate_release.sh.

- [ ] **Step 4: Verify GREEN**

~~~bash
bash dist/wlan/usr/local/scripts/wifi_factory_reset_test.sh
bash scripts/validate_release_test.sh
~~~

Expected: both suites pass.

- [ ] **Step 5: Commit**

~~~bash
git add dist/wlan/opt/wlan/config/systemd/network/22-eth0.network   dist/wlan/usr/local/scripts/wifi_factory_reset_test.sh   scripts/validate_release.sh scripts/validate_release_test.sh
git commit -m "fix: restore factory eth0 address"
~~~

---

### Task 4: Publish the 0.5.4 Contract

**Files:**
- Modify: dist/wlan/DEBIAN/control:2,44
- Modify: README.md:9,75,137,144
- Modify: CHANGELOG.md:4
- Modify: docs/wifi_init_conf_guide.md:153,187

**Interfaces:**
- Consumes: Tasks 1-3 behavior.
- Produces: version 0.5.4 and current operator instructions.

- [ ] **Step 1: Update metadata**

Set Version: 0.5.4 and append after the 0.5.3 description:

~~~text
 0.5.4 : wifi_manager 소유 config.json/nginx 비간섭, Factory Reset eth0 공장주소 192.168.1.1 복원
~~~

- [ ] **Step 2: Update README**

Change only current release references from 0.5.3 to 0.5.4. Add:

~~~markdown
config.json and nginx are owned by the separate wifi_manager package; wlan-proc
does not install, remove, reset, or validate them. Factory Reset restores the eth0
factory address to 192.168.1.1/24; normal upgrades preserve the active network file.
~~~

- [ ] **Step 3: Add changelog entry**

Insert before 0.5.3:

~~~markdown
## 0.5.4 (2026-08-14)

> SemVer **patch** — wifi_manager 소유 리소스와 wlan-proc의 경계를 정정하고 Factory Reset 유선 공장값을 복원한다.

- 업그레이드 시 /usr/local/etc/config.json을 삭제하지 않으며, package/CI/Factory Reset에서 config.json을 설치·복원·검증하지 않는다.
- nginx preflight·enable·후조건을 제거해 nginx lifecycle을 wifi_manager에 위임한다.
- Factory Reset의 eth0 공장 주소를 시험용 192.168.214.5/24에서 제품 기본 192.168.1.1/24로 복원한다. 일반 업그레이드는 active network를 보존한다.
~~~

Do not edit historical 0.5.2 text.

- [ ] **Step 4: Update operator guide**

Change current Factory Reset reconnect instructions to root@192.168.1.1. Replace the line 153 lab example with eth0=192.168.1.1/24 so 214.5 is not shown as a current product default.

- [ ] **Step 5: Verify docs**

~~~bash
grep -n '^Version: 0.5.4$' dist/wlan/DEBIAN/control
grep -n 'Current Version:\*\* 0.5.4' README.md
grep -n '^## 0.5.4 ' CHANGELOG.md
grep -n 'root@192.168.1.1' docs/wifi_init_conf_guide.md
~~~

Expected: all current contract references are found.

- [ ] **Step 6: Commit**

~~~bash
git add dist/wlan/DEBIAN/control README.md CHANGELOG.md   docs/wifi_init_conf_guide.md
git commit -m "docs: publish wlan-proc 0.5.4 ownership contract"
~~~

---

### Task 5: Full Build and Release Validation

**Files:**
- Regenerate: release/wlan.deb
- Regenerate: release/wlan-proc-0.5.4.deb
- Regenerate: release/wlan-package.tar
- Regenerate: release/SHA256SUMS

**Interfaces:**
- Consumes: all source, tests, metadata, and exact manifests.
- Produces: coherent validated 0.5.4 release artifacts.

- [ ] **Step 1: Run focused regressions**

~~~bash
bash dist/wlan/usr/local/scripts/wifi_init_config_test.sh
bash dist/wlan/usr/local/scripts/wifi_factory_reset_test.sh
bash scripts/validate_release_test.sh
bash scripts/package_tar_test.sh
~~~

Expected: all pass.

- [ ] **Step 2: Run complete build**

~~~bash
./build.sh
~~~

Expected: all pytest/shell suites and candidate package/archive gates pass.

- [ ] **Step 3: Validate published artifacts**

~~~bash
bash scripts/validate_release.sh package release/wlan.deb
bash scripts/validate_release.sh package release/wlan-proc-0.5.4.deb
bash scripts/package_tar_test.sh release/wlan-package.tar
(cd release && sha256sum -c SHA256SUMS)
dpkg-deb -f release/wlan.deb Package Version Architecture
rm -rf /tmp/wlan-0.5.4-check
dpkg-deb -x release/wlan.deb /tmp/wlan-0.5.4-check
grep -Fx 'Address=192.168.1.1/24'   /tmp/wlan-0.5.4-check/opt/wlan/config/systemd/network/22-eth0.network
~~~

Expected: metadata is wlan-proc / 0.5.4 / arm64 and eth0 source is 1.1/24.

- [ ] **Step 4: Verify ownership boundary**

~~~bash
! grep -RIn -E '/usr/local/etc/config\.json|/opt/wlan/config/config\.json|nginx'   dist/wlan/DEBIAN/postinst   dist/wlan/usr/local/scripts/factory_reset.sh   dist/wlan/usr/local/scripts/wifi_factory_reset_lib.sh
! grep -RIn -E 'retired config.json|config.json should not be packaged'   scripts .github/workflows/build-test.yml
~~~

Expected: no lifecycle or dedicated package-policy matches.

- [ ] **Step 5: Review and commit artifacts**

~~~bash
git diff --check
git status --short
git diff --stat master...HEAD
git add release/wlan.deb release/wlan-proc-0.5.4.deb   release/wlan-package.tar release/SHA256SUMS
git commit -m "release: build wlan-proc 0.5.4"
~~~

- [ ] **Step 6: Record final evidence**

~~~bash
git status --short --branch
git log --oneline master..HEAD
sha256sum release/wlan.deb release/wlan-proc-0.5.4.deb   release/wlan-package.tar
~~~

Expected: clean branch and matching generic/versioned Debian package hashes.
