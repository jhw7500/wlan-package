# Link-Incomplete Resource Guard Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 링크가 불완전(eth/wlan 중 하나 down)한 동안 브리지의 불필요한 패킷 처리/전송 시도를 줄이되, 로밍 중 일시적 단절에서는 서비스 안정성을 유지한다.

**Architecture:** 하드 fail-fast(즉시 종료) 대신 하이브리드 정책을 적용한다. `wifi_bridge.sh`에 debounce/hysteresis 기반 링크 상태 판정을 추가하고, 링크 불완전 시에는 브리지를 시작하지 않거나(초기 구간) 실행 중에는 엔진에서 TX short-circuit로 syscall/큐 오버헤드를 줄인다. systemd StartLimit과 roaming watcher(`wifi_checker.sh`)가 충돌하지 않도록 종료 코드/재시작 조건을 분리한다.

**Tech Stack:** bash (`wifi_bridge.sh`, `wbridge_smoke_test.sh`), C (`wbridge-pcap.c`, `wbridge-tpacket.c`), systemd unit (`wifi_bridge@.service`), Linux netdev/sysfs (`/sys/class/net/*/carrier`).

### Task 1: 현재 동작을 재현 가능한 형태로 고정

**Files:**
- Create: `docs/plans/evidence/2026-02-24-link-guard-baseline.md`
- Test: `wlan-bridge/scripts/wbridge_smoke_test.sh`

**Step 1: Baseline 증거 문서 틀 작성**

```markdown
# Link Guard Baseline Evidence
- Case A: eth0 up, mlan0 down
- Case B: eth0 down, mlan0 up
- Case C: both up
```

**Step 2: 현재 서비스/프로세스 상태 캡처**

Run: `systemctl status wifi_bridge@mlan0 --no-pager`
Expected: `active (running)` 또는 재시작 로그 확인 가능

**Step 3: 링크 비정상 시 리소스 지표 수집 커맨드 실행**

Run: `journalctl -u wifi_bridge@mlan0 -n 120 --no-pager`
Expected: 링크 대기/시작/재시작 관련 로그 확보

**Step 4: 결과를 evidence 문서에 기록**

```markdown
- command: ...
- observed: ...
- interpretation: ...
```

**Step 5: Commit**

```bash
git add docs/plans/evidence/2026-02-24-link-guard-baseline.md
git commit -m "docs: capture baseline evidence for link guard plan"
```

### Task 2: 링크 정책 환경변수 설계 및 문서화

**Files:**
- Modify: `dist/wlan/etc/default/wbridge`
- Modify: `wlan-bridge/docs/optimization-modes.md`
- Test: `wlan-bridge/docs/wbridge-smoke-test-checklist.md`

**Step 1: 실패 테스트(문서 기준) 추가**

```markdown
- 신규 항목: LINK_GUARD 정책 변수 미정의 시 기본값 동작 명시 필요
```

**Step 2: 변수 스펙 추가**

```bash
WBRIDGE_LINK_GUARD=1
WBRIDGE_LINK_DOWN_DEBOUNCE_SEC=5
WBRIDGE_LINK_UP_STABLE_SEC=2
WBRIDGE_LINK_DOWN_IDLE_SEC=60
```

**Step 3: smoke checklist에 검증 항목 추가**

```markdown
| C7 | Link guard debounce | down<debounce 구간에서 브리지 재기동 폭주 없음 |
| C8 | Long down idle | down>=idle 시 CPU 사용 저감/전송 시도 감소 |
```

**Step 4: 문서 lint/형식 확인**

Run: `grep -n "WBRIDGE_LINK_GUARD\|C7\|C8" dist/wlan/etc/default/wbridge wlan-bridge/docs/optimization-modes.md wlan-bridge/docs/wbridge-smoke-test-checklist.md`
Expected: 신규 키/체크 항목 라인 확인

**Step 5: Commit**

```bash
git add dist/wlan/etc/default/wbridge wlan-bridge/docs/optimization-modes.md wlan-bridge/docs/wbridge-smoke-test-checklist.md
git commit -m "docs: define link guard policy and smoke criteria"
```

### Task 3: `wifi_bridge.sh`를 테스트 가능한 구조로 분리

**Files:**
- Modify: `dist/wlan/usr/local/scripts/wifi_bridge.sh`
- Create: `dist/wlan/usr/local/scripts/tests/test_wifi_bridge_link_policy.sh`

**Step 1: 실패 테스트 작성 (pure bash)**

```bash
#!/usr/bin/env bash
set -euo pipefail
# expect: classify_link_state returns down when carrier=0
```

**Step 2: 테스트 실패 확인**

Run: `bash dist/wlan/usr/local/scripts/tests/test_wifi_bridge_link_policy.sh`
Expected: FAIL (`classify_link_state not found`)

**Step 3: 최소 구현 (함수 분리)**

```bash
classify_link_state() { ... }
wait_both_up_with_debounce() { ... }
```

**Step 4: 테스트 통과 확인**

Run: `bash dist/wlan/usr/local/scripts/tests/test_wifi_bridge_link_policy.sh`
Expected: PASS

**Step 5: Commit**

```bash
git add dist/wlan/usr/local/scripts/wifi_bridge.sh dist/wlan/usr/local/scripts/tests/test_wifi_bridge_link_policy.sh
git commit -m "refactor: extract link policy functions from wifi_bridge startup"
```

### Task 4: 초기 구간 링크 불완전 처리 정책 적용

**Files:**
- Modify: `dist/wlan/usr/local/scripts/wifi_bridge.sh`
- Modify: `dist/wlan/etc/systemd/system/wifi_bridge@.service`

**Step 1: 실패 테스트 작성**

```bash
# expect: when down duration >= LINK_DOWN_IDLE_SEC, script exits with non-failure code for intentional idle
```

**Step 2: 테스트 실패 확인**

Run: `bash dist/wlan/usr/local/scripts/tests/test_wifi_bridge_link_policy.sh`
Expected: FAIL (exit code policy mismatch)

**Step 3: 최소 구현**

```bash
# wifi_bridge.sh
if link_incomplete_long_enough; then
  exit 0
fi
```

```ini
# wifi_bridge@.service
RestartPreventExitStatus=0
```

**Step 4: 테스트 통과 확인**

Run: `bash dist/wlan/usr/local/scripts/tests/test_wifi_bridge_link_policy.sh`
Expected: PASS (의도적 idle 종료는 failure로 간주되지 않음)

**Step 5: Commit**

```bash
git add dist/wlan/usr/local/scripts/wifi_bridge.sh dist/wlan/etc/systemd/system/wifi_bridge@.service
git commit -m "feat: add intentional idle-exit policy for prolonged link incomplete state"
```

### Task 5: pcap 엔진 TX short-circuit 추가

**Files:**
- Modify: `wlan-bridge/wbridge/wbridge-pcap.c`
- Test: `wlan-bridge/wbridge/wbridge-pcap.c` (build + runtime log)

**Step 1: 실패 테스트(빌드 전제 + 계측 기대) 작성**

```markdown
- link_down 플래그 있을 때 pcap_inject 호출 건수가 증가하지 않아야 함
```

**Step 2: 계측/실패 확인**

Run: `make -C wlan-bridge/wbridge`
Expected: 빌드 성공, 아직 short-circuit 미적용 상태

**Step 3: 최소 구현**

```c
if (peer_link_down) {
    atomic_fetch_add(&stats.per_thread[i].dropped, 1);
    return;
}
```

**Step 4: 빌드 + 로그 검증**

Run: `make -C wlan-bridge/wbridge && journalctl -u wifi_bridge@mlan0 -n 80 --no-pager`
Expected: link-down 구간에서 inject 실패 로그 폭 감소

**Step 5: Commit**

```bash
git add wlan-bridge/wbridge/wbridge-pcap.c
git commit -m "perf: skip pcap inject when peer link is down"
```

### Task 6: tpacket 엔진 TX short-circuit 추가

**Files:**
- Modify: `wlan-bridge/wbridge/wbridge-tpacket.c`
- Test: `wlan-bridge/wbridge/wbridge-tpacket.c` (build + runtime log)

**Step 1: 실패 테스트(계측 기대) 작성**

```markdown
- link_down 상태에서 tx_ring_enqueue/sendto 시도 횟수 감소 확인
```

**Step 2: 빌드 기준선 확인**

Run: `make -C wlan-bridge/wbridge`
Expected: baseline build PASS

**Step 3: 최소 구현**

```c
if (peer_link_down) {
    local_drop_count++;
    goto next_pkt;
}
```

**Step 4: 빌드 + 로그 검증**

Run: `make -C wlan-bridge/wbridge && journalctl -u wifi_bridge@mlan0 -n 80 --no-pager`
Expected: down 구간 sendto/txring error 로그 감소

**Step 5: Commit**

```bash
git add wlan-bridge/wbridge/wbridge-tpacket.c
git commit -m "perf: short-circuit tpacket tx path when peer link is down"
```

### Task 7: 로밍 안정성 가드레일 적용

**Files:**
- Modify: `dist/wlan/usr/local/scripts/wifi_checker.sh`
- Modify: `dist/wlan/usr/local/scripts/wifi_bridge.sh`
- Test: `dist/wlan/opt/wlan/test/roam/wpa.sh`

**Step 1: 실패 테스트 시나리오 작성**

```markdown
- roaming 중 1~4초 단절에서는 bridge 재기동/종료가 없어야 함
```

**Step 2: 재현 스크립트 실행(실패/현재상태 기록)**

Run: `bash dist/wlan/opt/wlan/test/roam/wpa.sh`
Expected: 현재 동작 로그 확보

**Step 3: 최소 구현 (debounce/hysteresis 일치)**

```bash
# wifi_checker / wifi_bridge 공통 임계값 사용
LINK_DOWN_DEBOUNCE_SEC=5
LINK_UP_STABLE_SEC=2
```

**Step 4: 재검증**

Run: `bash dist/wlan/opt/wlan/test/roam/wpa.sh && journalctl -u wifi_bridge@mlan0 -n 120 --no-pager`
Expected: roaming 짧은 단절에서 restart 폭주 없음

**Step 5: Commit**

```bash
git add dist/wlan/usr/local/scripts/wifi_checker.sh dist/wlan/usr/local/scripts/wifi_bridge.sh
git commit -m "fix: align roaming debounce policy between checker and bridge startup"
```

### Task 8: 통합 스모크 및 릴리즈 검증

**Files:**
- Modify: `wlan-bridge/docs/wbridge-smoke-test-checklist.md`
- Test: `wlan-bridge/scripts/wbridge_smoke_test.sh`

**Step 1: 실패 테스트 추가 (C7/C8 검증 자동화 항목)**

```markdown
- smoke script에 link-incomplete long-down case 추가
```

**Step 2: 현재 스모크 실패 확인**

Run: `bash wlan-bridge/scripts/wbridge_smoke_test.sh mlan0`
Expected: 신규 C7/C8 없음으로 FAIL

**Step 3: 최소 구현**

```bash
# smoke script에 C7/C8 검사 함수 추가
```

**Step 4: 최종 검증**

Run: `bash wlan-bridge/scripts/wbridge_smoke_test.sh mlan0`
Expected: 전체 PASS

**Step 5: Commit**

```bash
git add wlan-bridge/scripts/wbridge_smoke_test.sh wlan-bridge/docs/wbridge-smoke-test-checklist.md
git commit -m "test: add link-incomplete guard checks to smoke suite"
```

## Cross-Checks (Every Task)

- Shell syntax: `bash -n dist/wlan/usr/local/scripts/wifi_bridge.sh dist/wlan/usr/local/scripts/wifi_checker.sh`
- C build: `make -C wlan-bridge/wbridge`
- Runtime evidence: `journalctl -u wifi_bridge@mlan0 -n 120 --no-pager`
- Service state: `systemctl status wifi_bridge@mlan0 --no-pager`

## Skills to Use During Execution

- `@superpowers:executing-plans`
- `@superpowers:systematic-debugging`
- `@superpowers:verification-before-completion`
