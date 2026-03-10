# Eco 모드 구현 계획

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** iMX8MM CPU 온도 저감을 위한 eco 모드를 기존 모드(latency/normal/thermal)를 변경하지 않고 신규 추가한다.

**Architecture:** `setup-irq-affinity.sh`에 eco 프로파일을 추가하고, cpufreq governor 제어 로직을 eco 모드 전용으로 삽입한다. `wifi_bridge.sh`와 `wifi_init_conf.json`의 모드 검증 목록에 eco를 추가한다. 스모크 테스트에 eco 케이스를 추가한다.

**Tech Stack:** Bash 스크립트, JSON 설정, systemd

**설계 문서:** `docs/specs/2026-03-10-imx8mm-thermal-optimization-design.md`

---

## 파일 구조

| 파일 | 변경 | 역할 |
|------|------|------|
| `wlan-bridge/scripts/setup-irq-affinity.sh` | 수정 | eco 프로파일 추가, cpufreq 로직 추가 |
| `wlan-bridge/scripts/restore-defaults.sh` | 수정 | cpufreq governor 원복 로직 추가 |
| `wlan-bridge/scripts/wbridge_smoke_test.sh` | 수정 | eco 모드 테스트 케이스 추가 |
| `dist/wlan/usr/local/scripts/wifi_bridge.sh` | 수정 | 모드 검증에 eco 추가 |
| `dist/wlan/opt/wlan/config/wifi_init_conf.json` | 수정 | mode 주석에 eco 언급 |

**변경하지 않는 파일:**
- `wbridge/` 하위 모든 C 소스 코드
- `wlan-driver/` 하위 모든 파일
- 기존 latency/normal/thermal 프로파일 값

---

## Chunk 1: setup-irq-affinity.sh에 eco 모드 추가

### Task 1: 모드 검증에 eco 추가

**Files:**
- Modify: `wlan-bridge/scripts/setup-irq-affinity.sh:81-87` (모드 검증 case)
- Modify: `wlan-bridge/scripts/setup-irq-affinity.sh:42-53` (help 텍스트)

- [ ] **Step 1: help 텍스트에 eco 모드 설명 추가**

`setup-irq-affinity.sh:42-53`의 help 섹션에서:

```bash
# 기존:
echo "MODE:"
echo "  latency  - 레이턴시 최소화 (인터럽트 즉시 처리, GRO OFF)"
echo "  thermal  - 발열 최소화 (인터럽트 병합, GRO ON)"
echo "  normal   - 균형 모드 (기본값)"

# 변경:
echo "MODE:"
echo "  latency  - 레이턴시 최소화 (인터럽트 즉시 처리, GRO OFF)"
echo "  eco      - 저전력 모드 (온도 저감 + 레이턴시 유지)"
echo "  thermal  - 발열 최소화 (인터럽트 병합, GRO ON)"
echo "  normal   - 균형 모드 (기본값)"
```

- [ ] **Step 2: 모드 검증 case에 eco 추가**

`setup-irq-affinity.sh:81-87`에서:

```bash
# 기존 (line 81-82):
case "$MODE" in
    latency|thermal|normal) ;;

# 변경:
case "$MODE" in
    latency|thermal|normal|eco) ;;
```

마찬가지로 `line 84`의 에러 메시지도 업데이트:

```bash
# 기존:
log_err "ERROR: 알 수 없는 모드 '$MODE' (latency|thermal|normal)"

# 변경:
log_err "ERROR: 알 수 없는 모드 '$MODE' (latency|normal|eco|thermal)"
```

- [ ] **Step 3: MODE_REQUESTED 검증에도 eco 추가**

`setup-irq-affinity.sh:92-98`에서:

```bash
# 기존 (line 93):
case "$MODE_REQUESTED" in
    latency|thermal|normal) ;;

# 변경:
case "$MODE_REQUESTED" in
    latency|thermal|normal|eco) ;;
```

- [ ] **Step 4: Commit**

```bash
git add wlan-bridge/scripts/setup-irq-affinity.sh
git commit -m "feat(setup-irq-affinity): add eco mode to validation and help text"
```

---

### Task 2: eco 프로파일 파라미터 정의

**Files:**
- Modify: `wlan-bridge/scripts/setup-irq-affinity.sh:117-157` (모드별 파라미터 case)

- [ ] **Step 1: eco 프로파일을 normal과 thermal 사이에 추가**

`setup-irq-affinity.sh`의 `case "$MODE" in` 블록(line 117)에서 `normal)` 앞에 `eco)` 케이스를 삽입:

```bash
    eco)
        MODE_DESC="저전력 (온도 저감)"
        RX_USECS=100; TX_USECS=100; RX_FRAMES=6
        GRO=on;       GSO=off;      TSO=off
        # wbridge-pcap 환경변수
        WB_DISPATCH_BUDGET=96
        WB_IMMEDIATE=0
        WB_TIMEOUT_MS=5
        WB_RT_PRIORITY=40
        WB_PCAP_BUFFER=4194304
        # wbridge-tpacket 환경변수
        WB_TPACKET_RETIRE_TOV=5
        ;;
```

위치: `thermal)` 블록의 `;;` 뒤, `normal)` 블록 앞에 삽입한다.

주의: 기존 latency/thermal/normal 값은 한 글자도 변경하지 않는다.

- [ ] **Step 2: 값 검증 — normal과 thermal 사이에 있는지 확인**

수동 검증 체크리스트:

| 파라미터 | latency | normal | **eco** | thermal | eco가 사이에 있는가? |
|---------|---------|--------|---------|---------|-------------------|
| RX_USECS | 0 | 50 | 100 | 150 | O |
| RX_FRAMES | 1 | 4 | 6 | 10 | O |
| DISPATCH_BUDGET | 64 | 64 | 96 | 128 | O |
| TIMEOUT_MS | 1 | 1 | 5 | 10 | O |
| RT_PRIORITY | 80 | 50 | 40 | 30 | O |
| IMMEDIATE | 1 | 1 | 0 | 0 | O |

- [ ] **Step 3: Commit**

```bash
git add wlan-bridge/scripts/setup-irq-affinity.sh
git commit -m "feat(setup-irq-affinity): define eco profile parameters between normal and thermal"
```

---

### Task 3: cpufreq governor 제어 로직 추가

**Files:**
- Modify: `wlan-bridge/scripts/setup-irq-affinity.sh` (오프로드 설정 뒤, 환경변수 생성 앞)

- [ ] **Step 1: cpufreq 제어 섹션 추가**

`setup-irq-affinity.sh`의 `[5/5] 오프로드` 블록(line 241-250) 뒤, `[ENV] wbridge 환경변수` 블록(line 252) 앞에 새 섹션을 삽입한다.

단계 번호를 조정: 기존 5단계 체계에 6단계 추가, 또는 기존 번호를 유지하면서 5.5로 삽입.
→ 기존 `[5/5]`를 `[5/6]`으로 변경하지 **않는다** (기존 모드 동작에 영향). 대신 eco 전용 로직을 조건부로 삽입:

```bash
# ─── eco 모드 전용: cpufreq governor ───
if [ "$MODE" = "eco" ]; then
    log_info "[cpufreq] eco 모드: conservative governor 설정"
    PREV_GOVERNOR=""
    if [ -f /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor ]; then
        PREV_GOVERNOR=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null || true)
        for cpu_gov in /sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_governor; do
            echo conservative > "$cpu_gov" 2>/dev/null || true
        done
        # conservative 파라미터 조정
        if [ -d /sys/devices/system/cpu/cpufreq/conservative ]; then
            echo 80 > /sys/devices/system/cpu/cpufreq/conservative/up_threshold 2>/dev/null || true
            echo 20 > /sys/devices/system/cpu/cpufreq/conservative/down_threshold 2>/dev/null || true
        fi
        log_info "  cpufreq: $PREV_GOVERNOR → conservative (up=80, down=20)"
    else
        log_warn "  cpufreq: scaling_governor 미지원"
    fi
fi
```

삽입 위치: line 250(`done` 뒤)과 line 252(`# ─── 6. wbridge 환경변수 파일 생성 ───`) 사이.

- [ ] **Step 2: 환경변수 파일에 cpufreq 메타데이터 추가**

`setup-irq-affinity.sh`의 환경변수 파일 생성 부분(line 255-273)에서, EOF 앞에 추가:

```bash
# cpufreq (eco 모드 전용)
WBRIDGE_CPUFREQ_GOVERNOR=${PREV_GOVERNOR:-unchanged}
```

주의: `PREV_GOVERNOR`는 eco 모드에서만 설정되므로, 다른 모드에서는 `unchanged`가 기록된다.

- [ ] **Step 3: Commit**

```bash
git add wlan-bridge/scripts/setup-irq-affinity.sh
git commit -m "feat(setup-irq-affinity): add cpufreq governor control for eco mode"
```

---

### Task 4: restore-defaults.sh에 cpufreq 원복 추가

**Files:**
- Modify: `wlan-bridge/scripts/restore-defaults.sh:63-73` (IRQ Affinity 초기화 뒤)

- [ ] **Step 1: cpufreq governor 원복 섹션 추가**

`restore-defaults.sh`의 `4. IRQ Affinity 초기화` 블록(line 64-73) 뒤, `5. 영구 설정 안내` 블록(line 75) 앞에 삽입:

```bash
# 5. cpufreq Governor 원복 (eco 모드에서 변경된 경우)
echo ""
echo -e "${BLUE}5. cpufreq Governor 원복 (→ performance)${NC}"
for cpu_gov in /sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_governor; do
    if [ -f "$cpu_gov" ]; then
        echo performance > "$cpu_gov" 2>/dev/null || true
    fi
done
CURRENT_GOV=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null || echo "N/A")
echo "  ✓ cpufreq governor: $CURRENT_GOV"
```

기존 `5. 영구 설정 안내` 번호를 `6.`으로 변경.

- [ ] **Step 2: Commit**

```bash
git add wlan-bridge/scripts/restore-defaults.sh
git commit -m "feat(restore-defaults): add cpufreq governor restore to performance"
```

---

## Chunk 2: wifi_bridge.sh 및 wifi_init_conf.json 업데이트

### Task 5: wifi_bridge.sh 모드 검증에 eco 추가

**Files:**
- Modify: `dist/wlan/usr/local/scripts/wifi_bridge.sh:78-84` (REQUESTED_MODE 검증)
- Modify: `dist/wlan/usr/local/scripts/wifi_bridge.sh:134-141` (thermal clamp 로직)

- [ ] **Step 1: REQUESTED_MODE 검증에 eco 추가**

`wifi_bridge.sh:78-84`에서:

```bash
# 기존 (line 78-79):
case "$REQUESTED_MODE" in
    latency|normal|thermal) ;;

# 변경:
case "$REQUESTED_MODE" in
    latency|normal|eco|thermal) ;;
```

- [ ] **Step 2: thermal clamp 로직에 eco 동작 정의**

`wifi_bridge.sh:134-141`의 thermal clamp 로직 확인:

```bash
EFFECTIVE_MODE="$REQUESTED_MODE"
if [ "$MODE_FORCE" -ne 1 ]; then
    if [ "$THERMAL_STATE" = "hot" ]; then
        EFFECTIVE_MODE="thermal"
    elif [ "$THERMAL_STATE" = "warm" ] && [ "$REQUESTED_MODE" = "latency" ]; then
        EFFECTIVE_MODE="normal"
    fi
fi
```

eco 모드의 thermal clamp 동작:
- `hot` → `thermal`로 클램프 (기존 로직이 이미 처리: hot이면 무조건 thermal)
- `warm` → eco는 이미 normal보다 온도 저감이므로 유지 (변경 없음)
- `ok` → eco 그대로 유지 (변경 없음)

**결론: thermal clamp 로직은 변경 불필요.** 기존 `hot → thermal` 로직이 eco 포함 모든 모드에 적용됨.

- [ ] **Step 3: Commit**

```bash
git add dist/wlan/usr/local/scripts/wifi_bridge.sh
git commit -m "feat(wifi_bridge): add eco to mode validation"
```

---

### Task 6: wifi_init_conf.json 업데이트

**Files:**
- Modify: `dist/wlan/opt/wlan/config/wifi_init_conf.json:31-33` (wbridge 섹션)

- [ ] **Step 1: wbridge 섹션의 _comment에 eco 모드 언급**

`wifi_init_conf.json:31-33`에서:

```json
// 기존:
"_comment": "wbridge bridge process configuration (WBRIDGE_* env vars). /etc/default/wbridge takes priority if set.",

// 변경:
"_comment": "wbridge bridge process configuration (WBRIDGE_* env vars). mode: latency|normal|eco|thermal. /etc/default/wbridge takes priority if set.",
```

mode 기본값은 `"normal"` 그대로 유지. eco를 기본값으로 변경하지 않는다.

- [ ] **Step 2: Commit**

```bash
git add dist/wlan/opt/wlan/config/wifi_init_conf.json
git commit -m "docs(wifi_init_conf): document eco as valid mode option"
```

---

## Chunk 3: 스모크 테스트 추가

### Task 7: eco 모드 스모크 테스트 케이스 추가

**Files:**
- Modify: `wlan-bridge/scripts/wbridge_smoke_test.sh` (run_thermal_clamp_case 뒤)

- [ ] **Step 1: run_eco_case 함수 추가**

`wbridge_smoke_test.sh`의 `run_thermal_clamp_case()` 함수(line 169-185) 뒤, `run_force_override_case()` 함수(line 187) 앞에 삽입:

```bash
run_eco_case() {
    log_info "Running case: eco_mode"
    set_kv WBRIDGE_ENGINE pcap
    set_kv WBRIDGE_OPTIMIZE 1
    set_kv WBRIDGE_MODE eco
    set_kv WBRIDGE_THERMAL_STATE ok
    set_kv WBRIDGE_MODE_FORCE 0

    service_restart || return

    local effective
    effective=$(json_get /run/wbridge.effective.json profile_effective)

    expect_equal "eco mode effective" "$effective" "eco"
}
```

- [ ] **Step 2: eco thermal clamp 테스트 추가**

eco 모드에서 hot 상태일 때 thermal로 클램프되는지 확인:

```bash
run_eco_thermal_clamp_case() {
    log_info "Running case: eco_thermal_clamp"
    set_kv WBRIDGE_ENGINE pcap
    set_kv WBRIDGE_OPTIMIZE 1
    set_kv WBRIDGE_MODE eco
    set_kv WBRIDGE_THERMAL_STATE hot
    set_kv WBRIDGE_MODE_FORCE 0

    service_restart || return

    local effective
    effective=$(json_get /run/wbridge.effective.json profile_effective)

    expect_equal "eco thermal clamp to thermal" "$effective" "thermal"
}
```

- [ ] **Step 3: main 함수에 새 테스트 케이스 등록**

`wbridge_smoke_test.sh:296-301`의 main 함수에서:

```bash
# 기존:
    run_pcap_case
    run_tpacket_case
    run_thermal_clamp_case
    run_force_override_case
    run_thermal_timer_case
    run_sysctl_case

# 변경:
    run_pcap_case
    run_tpacket_case
    run_thermal_clamp_case
    run_eco_case
    run_eco_thermal_clamp_case
    run_force_override_case
    run_thermal_timer_case
    run_sysctl_case
```

- [ ] **Step 4: Commit**

```bash
git add wlan-bridge/scripts/wbridge_smoke_test.sh
git commit -m "test(smoke_test): add eco mode and eco thermal clamp test cases"
```

---

## Chunk 4: 문서 및 최종 검증

### Task 8: 설계 문서 상태 업데이트

**Files:**
- Modify: `docs/specs/2026-03-10-imx8mm-thermal-optimization-design.md:6` (상태)

- [ ] **Step 1: 상태를 "구현 완료 / 검증 대기"로 변경**

```markdown
# 기존:
**상태:** 설계 완료 / 구현 대기

# 변경:
**상태:** 구현 완료 / 타겟 검증 대기
```

- [ ] **Step 2: Commit**

```bash
git add docs/specs/2026-03-10-imx8mm-thermal-optimization-design.md
git commit -m "docs: update design doc status to implementation complete"
```

---

### Task 9: 타겟 검증 가이드

이 태스크는 코드 변경이 아니라 타겟 보드에서 수행할 검증 절차이다.

- [ ] **Step 1: eco 모드 기본 동작 확인**

```bash
# 타겟 보드에서 실행
sudo ./setup-irq-affinity.sh --mode eco eth0 mlan0

# 확인 항목:
# 1. 에러 없이 완료
# 2. /run/wbridge.env에 eco 파라미터가 기록됨
cat /run/wbridge.env | grep -E "DISPATCH_BUDGET|TIMEOUT_MS|RT_PRIORITY"
# 예상: DISPATCH_BUDGET=96, TIMEOUT_MS=5, RT_PRIORITY=40

# 3. cpufreq governor가 conservative로 변경됨
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor
# 예상: conservative
```

- [ ] **Step 2: 온도 측정 기준선 (normal 모드)**

```bash
# normal 모드로 브릿지 시작
sudo ./setup-irq-affinity.sh --mode normal eth0 mlan0
# wbridge 시작 후 10분 대기

# 온도 기록 (10분)
for i in $(seq 1 600); do
    echo "$(date +%H:%M:%S) $(cat /sys/devices/virtual/thermal/thermal_zone0/temp)"
    sleep 1
done > /tmp/temp_normal.log

# ping RTT 기록
ping -c 100 -i 0.1 <gateway_ip> > /tmp/ping_normal.log
```

- [ ] **Step 3: 온도 측정 (eco 모드)**

```bash
# eco 모드로 브릿지 재시작
sudo ./setup-irq-affinity.sh --mode eco eth0 mlan0
# wbridge 재시작 후 10분 대기

# 동일한 온도/RTT 기록
for i in $(seq 1 600); do
    echo "$(date +%H:%M:%S) $(cat /sys/devices/virtual/thermal/thermal_zone0/temp)"
    sleep 1
done > /tmp/temp_eco.log

ping -c 100 -i 0.1 <gateway_ip> > /tmp/ping_eco.log
```

- [ ] **Step 4: 결과 비교**

```bash
# 온도 비교 (마지막 3분 평균)
tail -180 /tmp/temp_normal.log | awk '{sum+=$2; n++} END{print "normal avg:", sum/n/1000, "°C"}'
tail -180 /tmp/temp_eco.log    | awk '{sum+=$2; n++} END{print "eco avg:",    sum/n/1000, "°C"}'

# RTT 비교
grep -oP 'time=\K[0-9.]+' /tmp/ping_normal.log | awk '{sum+=$1; n++} END{print "normal RTT avg:", sum/n, "ms"}'
grep -oP 'time=\K[0-9.]+' /tmp/ping_eco.log    | awk '{sum+=$1; n++} END{print "eco RTT avg:",    sum/n, "ms"}'
```

예상 결과:
- 온도: eco가 normal 대비 8~11°C 낮음
- RTT: eco가 normal 대비 +2~4ms

---

## 요약

| Task | 파일 | 변경 내용 |
|------|------|----------|
| 1 | setup-irq-affinity.sh | 모드 검증/help에 eco 추가 |
| 2 | setup-irq-affinity.sh | eco 프로파일 파라미터 정의 |
| 3 | setup-irq-affinity.sh | cpufreq governor 제어 (eco 전용) |
| 4 | restore-defaults.sh | cpufreq 원복 로직 추가 |
| 5 | wifi_bridge.sh | 모드 검증에 eco 추가 |
| 6 | wifi_init_conf.json | eco 모드 문서화 |
| 7 | wbridge_smoke_test.sh | eco 테스트 케이스 2개 추가 |
| 8 | 설계 문서 | 상태 업데이트 |
| 9 | (타겟 보드) | 실측 검증 가이드 |

총 커밋: 7개 (Task 1~8, Task 9는 타겟 검증)
