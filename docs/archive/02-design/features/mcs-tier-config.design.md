# Design: wifi_init_conf.json MCS Tier 설정

**Feature**: mcs-tier-config
**Created**: 2026-04-06
**Architecture**: Option C — Pragmatic (rate_adapt_cfg 동일 패턴)

---

## Context Anchor

| 항목 | 내용 |
|------|------|
| **WHY** | UL 재전송률 55%로 높음. MCS 하향 제한으로 재전송 성공률 향상 기대. 현장 배포 시 수동 커맨드 실행 불가 |
| **WHO** | 캔탑스 WiFi 팀 (현장 배포), DFK/세봉 (검증) |
| **RISK** | mcstiercfg 설정 오류 시 association 실패 가능. FW VHT MCS 7 floor 제약 |
| **SUCCESS** | 부팅 후 mlanutl mcstiercfg GET으로 설정값 확인 가능. JSON 변경만으로 MCS 제한 변경 가능 |
| **SCOPE** | mcstiercfg 부팅 시 적용만. ratemaxcfg/on_connect 연동은 범위 밖 |

---

## 1. Overview

wifi_init_conf.json에 인터페이스별 `mcs_tier` 섹션을 추가하고, wifi_init.sh의 `apply_iface_radio_defaults()` 함수 내에서 `rate_adapt_cfg`와 동일한 패턴으로 mcstiercfg를 적용한다.

```
wifi_init.sh 실행 흐름:

  JSON 로드 → insmod → apply_iface_radio_defaults()
                          ├── txpwrlimit hostcmd (기존)
                          ├── reassoctrl (기존)
                          ├── rate_adapt_cfg (기존)
                          └── ★ apply_mcs_tier (신규)
                       → wpa_supplicant 시작
```

---

## 2. 설계 결정

| 결정 | 선택 | 근거 |
|------|------|------|
| 설계 패턴 | rate_adapt_cfg와 동일 | wifi_init.sh 내 직접 jq 읽기 → mlanutl 실행. 기존 패턴 재사용 |
| JSON 위치 | 인터페이스별 (mlan0/mlan1) | mlanutl이 인터페이스별 실행. mlan0/mlan1 독립 설정 필요 |
| config_lib.sh | 변경 없음 | 별도 헬퍼 불필요. jq 직접 호출이 기존 패턴 |
| 기본값 | enabled: false | 명시적 활성화 필요. 기존 동작 영향 없음 보장 |
| 에러 처리 | 유효성 실패 시 return 1 + 로깅 | 부팅은 계속 진행 (set -e 하에서도 `||` 패턴으로 보호) |

---

## 3. JSON 스키마

### 3.1 추가 위치

`wifi_init_conf.json`의 `mlan0` / `mlan1` 섹션 내, `on_connect` 앞에 추가:

```json
"mlan0": {
    ...
    "roaming": { ... },
    "mcs_tier": {
        "_comment": "MCS tier capability limit (mcstiercfg). Applied at boot before association. Survives roaming.",
        "enabled": false,
        "ht": 7,
        "vht": 7,
        "he": 7
    },
    "on_connect": { ... }
}
```

### 3.2 필드 유효성

| 키 | 타입 | 허용값 | 기본값 | 미설정 시 |
|----|------|--------|--------|----------|
| `enabled` | bool | true/false | false | mcstiercfg 미실행 |
| `ht` | int | 7, 15 | — | HT 건너뜀 |
| `vht` | int | 7, 8, 9 | — | VHT 건너뜀 |
| `he` | int | 7, 9, 11 | — | HE 건너뜀 |

---

## 4. 구현 상세

### 4.1 apply_mcs_tier() — wifi_init.sh

```bash
apply_mcs_tier() {
    local iface="$1"
    local enabled ht vht he args=""

    # JSON 없거나 jq 없으면 skip (NF-01)
    [ -f "$WIFI_INIT_CONF_JSON" ] && command -v jq >/dev/null 2>&1 || return 0

    enabled=$(jq -r ".${iface}.mcs_tier.enabled // false" "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    [ "$enabled" = "true" ] || return 0

    # 각 표준별 읽기 + 유효성 검증
    ht=$(jq -r ".${iface}.mcs_tier.ht // empty" "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    vht=$(jq -r ".${iface}.mcs_tier.vht // empty" "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    he=$(jq -r ".${iface}.mcs_tier.he // empty" "$WIFI_INIT_CONF_JSON" 2>/dev/null)

    if [ -n "$ht" ]; then
        case "$ht" in
            7|15) args="$args ht $ht" ;;
            *) logger -p local0.err "[$tag:$LINENO] [$iface] mcs_tier: invalid ht=$ht (must be 7 or 15)"; return 1 ;;
        esac
    fi

    if [ -n "$vht" ]; then
        case "$vht" in
            7|8|9) args="$args vht $vht" ;;
            *) logger -p local0.err "[$tag:$LINENO] [$iface] mcs_tier: invalid vht=$vht (must be 7/8/9)"; return 1 ;;
        esac
    fi

    if [ -n "$he" ]; then
        case "$he" in
            7|9|11) args="$args he $he" ;;
            *) logger -p local0.err "[$tag:$LINENO] [$iface] mcs_tier: invalid he=$he (must be 7/9/11)"; return 1 ;;
        esac
    fi

    if [ -z "$args" ]; then
        logger -p local0.warn "[$tag:$LINENO] [$iface] mcs_tier: enabled but no tier specified"
        return 0
    fi

    logger -p local0.info "[$tag:$LINENO] [$iface] mcstiercfg$args"
    mlanutl "$iface" mcstiercfg $args > /dev/null 2>&1 || \
        logger -p local0.err "[$tag:$LINENO] [$iface] mcstiercfg failed"
}
```

### 4.2 호출 위치 — apply_iface_radio_defaults()

기존 `rate_adapt_cfg` 블록 직후에 추가:

```bash
apply_iface_radio_defaults() {
    local iface="$1"
    ...
    # Apply rate_adapt_cfg (기존)
    ra_mode=$(jq -r '.global.rate_adapt.mode // empty' "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    if [ -n "$ra_mode" ]; then
        ...
    fi

    # Apply MCS tier capability limit (신규)
    apply_mcs_tier "$iface" || \
        logger -p local0.err "[$tag:$LINENO] [$iface] apply_mcs_tier failed (continuing)"
}
```

`|| logger` 패턴으로 apply_mcs_tier 실패해도 부팅 중단 없이 계속 진행.

### 4.3 wifi_init_conf.json 변경

mlan0과 mlan1 모두에 동일 구조 추가. 기본값 `enabled: false`:

```json
"mcs_tier": {
    "_comment": "MCS tier capability limit (mcstiercfg). Applied at boot before association. Survives roaming. ht: 7|15, vht: 7|8|9, he: 7|9|11",
    "enabled": false,
    "ht": 7,
    "vht": 7,
    "he": 7
}
```

---

## 5. 에러 처리

| 조건 | 동작 | syslog |
|------|------|--------|
| JSON 없음 | skip (return 0) | 없음 |
| jq 없음 | skip (return 0) | 없음 |
| enabled=false | skip (return 0) | 없음 |
| enabled=true, 키 미설정 | 해당 표준 건너뜀 | warn (no tier specified) |
| 유효하지 않은 값 | return 1 + 에러 로깅 | err (invalid ht/vht/he) |
| mlanutl 실행 실패 | 에러 로깅, 부팅 계속 | err (mcstiercfg failed) |
| mlanutl 없음 (NF-02) | mlanutl 실패 → 에러 로깅 | err (mcstiercfg failed) |

---

## 6. 수정 파일 목록

| # | 파일 (절대 경로) | 변경 내용 | 줄 수 |
|---|---|---|---|
| 1 | `/home/jhw/ai/opencode/projects/wlan-package/dist/wlan/opt/wlan/config/wifi_init_conf.json` | mlan0/mlan1에 mcs_tier 섹션 추가 | +14 |
| 2 | `/home/jhw/ai/opencode/projects/wlan-package/dist/wlan/usr/local/scripts/wifi_init.sh` | apply_mcs_tier() 함수 + 호출부 | +40 |
| 3 | `/home/jhw/ai/opencode/projects/wlan-package/dist/wlan/usr/local/scripts/wifi_init_config_test.sh` | mcs_tier JSON 읽기 테스트 | +25 |
| 4 | `/home/jhw/ai/opencode/projects/wlan-package/docs/wifi_init_conf_guide.md` | mcs_tier 섹션 문서 추가 | +35 |

**총 변경량**: ~114줄 (4개 파일)

---

## 7. 테스트 설계

### 7.1 wifi_init_config_test.sh 추가 케이스

```bash
# T-01: mcs_tier disabled (기본값)
test_mcs_tier_disabled() {
    local json='{"mlan0":{"mcs_tier":{"enabled":false,"ht":7,"vht":7,"he":7}}}'
    local result=$(echo "$json" | jq -r '.mlan0.mcs_tier.enabled // false')
    assert_eq "$result" "false" "mcs_tier disabled"
}

# T-02: mcs_tier enabled, 전체 tier 읽기
test_mcs_tier_all() {
    local json='{"mlan0":{"mcs_tier":{"enabled":true,"ht":7,"vht":8,"he":9}}}'
    assert_eq "$(echo "$json" | jq -r '.mlan0.mcs_tier.ht')" "7" "ht=7"
    assert_eq "$(echo "$json" | jq -r '.mlan0.mcs_tier.vht')" "8" "vht=8"
    assert_eq "$(echo "$json" | jq -r '.mlan0.mcs_tier.he')" "9" "he=9"
}

# T-03: 부분 설정 (he만)
test_mcs_tier_partial() {
    local json='{"mlan0":{"mcs_tier":{"enabled":true,"he":11}}}'
    assert_eq "$(echo "$json" | jq -r '.mlan0.mcs_tier.ht // empty')" "" "ht empty"
    assert_eq "$(echo "$json" | jq -r '.mlan0.mcs_tier.he')" "11" "he=11"
}

# T-04: mcs_tier 섹션 없음 → 기본값 false
test_mcs_tier_missing() {
    local json='{"mlan0":{"enabled":true}}'
    local result=$(echo "$json" | jq -r '.mlan0.mcs_tier.enabled // false')
    assert_eq "$result" "false" "mcs_tier missing → false"
}
```

### 7.2 타겟 보드 수동 검증

| # | 테스트 | 명령어 | 기대 결과 |
|---|--------|--------|----------|
| T-05 | 부팅 후 적용 확인 | `mlanutl mlan0 mcstiercfg` | 설정한 tier 값 출력 |
| T-06 | syslog 확인 | `journalctl -t wifi_init.sh \| grep mcstiercfg` | `mcstiercfg ht 7 vht 7 he 7` |
| T-07 | 로밍 후 유지 | 로밍 후 `mlanutl mlan0 mcstiercfg` | tier 값 동일 |
| T-08 | disabled → 미실행 | enabled=false 후 재부팅, syslog | mcstiercfg 로그 없음 |

---

## 8. 문서 업데이트 — wifi_init_conf_guide.md

`## 1. global` 섹션 아래, 인터페이스 섹션에 추가:

```markdown
### N.N mlanX.mcs_tier - MCS Tier 능력 제한

**사용 스크립트**: `wifi_init.sh`

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `enabled` | bool | `false` | mcstiercfg 적용 활성화 |
| `ht` | int | — | HT(11n) 최대 MCS. `7`(1x1) 또는 `15`(2x2) |
| `vht` | int | — | VHT(11ac) 최대 MCS. `7`, `8`, `9` |
| `he` | int | — | HE(11ax) 최대 MCS. `7`, `9`, `11` |

> **주의**: VHT는 FW 내부에 MCS 7 하한(floor)이 있어 tier 7이 사실상 최소값.
> 상세 비교: `wlan-driver/docs/mcs-rate-control-comparison.md` 참조.

**적용 시점**: 부팅 시 wpa_supplicant 시작 전 (association 전). 로밍해도 유지됨.

```json
"mcs_tier": {
    "enabled": true,
    "ht": 7,
    "vht": 7,
    "he": 7
}
```
```

---

## 9. Implementation Guide

### 9.1 구현 순서

| 순서 | 파일 | 작업 |
|------|------|------|
| 1 | wifi_init_conf.json | mlan0/mlan1에 mcs_tier 섹션 추가 |
| 2 | wifi_init.sh | apply_mcs_tier() 함수 작성 |
| 3 | wifi_init.sh | apply_iface_radio_defaults()에 호출 추가 |
| 4 | wifi_init_config_test.sh | 테스트 케이스 추가 |
| 5 | wifi_init_conf_guide.md | 문서 업데이트 |

### 9.2 의존성

- 기존 `mlanutl mcstiercfg` 커맨드가 드라이버에 포함되어 있어야 함
- `jq` 패키지 (이미 의존성에 포함)

### 9.3 Session Guide

단일 세션으로 완료 가능 (총 ~114줄, 4파일). 분할 불필요.

| Module | 파일 | 예상 |
|--------|------|------|
| module-1 | JSON + wifi_init.sh | ~55줄 |
| module-2 | 테스트 + 문서 | ~60줄 |
