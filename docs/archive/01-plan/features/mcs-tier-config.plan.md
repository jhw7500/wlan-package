# Plan: wifi_init_conf.json MCS Tier 설정

**Feature**: mcs-tier-config
**Created**: 2026-04-06
**Status**: Implemented (wifi_init.sh `apply_mcs_tier` + wifi_event.sh 연결 이벤트 재적용 — 참조: `docs/wifi_init_conf_guide.md` §11.6)

---

## Executive Summary

| 관점 | 내용 |
|------|------|
| **Problem** | mcstiercfg MCS 제한이 수동 명령어 의존. 부팅/재부팅 시 자동 적용 안 됨 |
| **Solution** | wifi_init_conf.json에 인터페이스별 mcs_tier 섹션 추가, wifi_init.sh에서 부팅 시 자동 적용 |
| **Function UX Effect** | JSON 수정만으로 MCS 제한 변경 가능. 재부팅해도 설정 유지 |
| **Core Value** | MCS 상한 제한을 중앙 설정 파일에서 관리하여 현장 배포/튜닝 용이 |

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

## 1. 배경

### 1.1 현재 상태

- `mcstiercfg`는 직접 구현한 mlanutl 커맨드로, VHT/HE/HT의 Capability tier를 설정
- Association 전에 적용해야 하며, 로밍/재연결해도 유지됨
- 현재는 수동으로 `mlanutl mlan0 mcstiercfg vht 7 he 7 ht 7` 실행 필요
- `wifi_init_conf.json`은 이미 중앙 설정 파일로 rate_adapt, net_rx 등 관리 중

### 1.2 mcstiercfg 특성 요약

| 항목 | 값 |
|------|-----|
| 적용 시점 | Association 전 (reconnect 필요) |
| 로밍 시 리셋 | **유지됨** (Capability IE는 드라이버 구조체 저장) |
| HE tier | 7, 9, 11 |
| VHT tier | 7, 8, 9 |
| HT tier | 7 (1x1) 또는 15 (2x2) |
| 영향 범위 | TX + RX (AP의 rate 선택에도 영향) |

→ **부팅 시 한 번만 설정하면 로밍해도 유지**되므로, wifi_init.sh에서 적용하기에 적합

---

## 2. 요구사항

### 2.1 기능 요구사항

| ID | 요구사항 | 우선순위 |
|----|----------|----------|
| FR-01 | `wifi_init_conf.json`에 인터페이스별 `mcs_tier` 섹션 추가 | 필수 |
| FR-02 | `wifi_init.sh`에서 부팅 시 mcstiercfg 자동 적용 | 필수 |
| FR-03 | `mcs_tier.enabled: false`이면 설정 건너뜀 (기본값) | 필수 |
| FR-04 | 설정값 유효성 검증 (지원하는 tier만 허용) | 필수 |
| FR-05 | 적용 결과 syslog 로깅 | 필수 |
| FR-06 | `wifi_init_conf_guide.md` 문서 업데이트 | 필수 |
| FR-07 | `wifi_init_config_test.sh` 테스트 케이스 추가 | 필수 |

### 2.2 비기능 요구사항

| ID | 요구사항 |
|----|----------|
| NF-01 | JSON 없거나 jq 없으면 기본값으로 동작 (기존 fallback 패턴 유지) |
| NF-02 | mcstiercfg 미설치 환경에서 에러 없이 skip |
| NF-03 | rate_adapt_cfg와 마찬가지로 association 전에 적용 |

---

## 3. JSON 설정 구조

### 3.1 wifi_init_conf.json 추가 섹션

```json
"mlan0": {
    "mcs_tier": {
        "_comment": "MCS tier capability limit (mcstiercfg). Applied at boot before association. Survives roaming.",
        "enabled": false,
        "ht": 7,
        "vht": 7,
        "he": 7
    }
}
```

### 3.2 필드 정의

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `enabled` | bool | `false` | mcs_tier 설정 활성화 여부 |
| `ht` | int | (미설정) | HT(11n) 최대 MCS. 허용값: `7` (1x1), `15` (2x2) |
| `vht` | int | (미설정) | VHT(11ac) 최대 MCS. 허용값: `7`, `8`, `9` |
| `he` | int | (미설정) | HE(11ax) 최대 MCS. 허용값: `7`, `9`, `11` |

- `enabled: false`이면 mcstiercfg를 실행하지 않음 (FW 기본값 사용)
- 개별 키(ht/vht/he)가 없으면 해당 표준은 건너뜀
- 인터페이스별 독립 설정 (mlan0과 mlan1에 다른 tier 가능)

### 3.3 설정 예시

```json
// 모든 표준을 MCS 7로 제한 (보수적)
"mcs_tier": { "enabled": true, "ht": 7, "vht": 7, "he": 7 }

// HE만 MCS 9로 제한, VHT/HT는 기본
"mcs_tier": { "enabled": true, "he": 9 }

// 비활성 (기본값 — mcstiercfg 실행 안 함)
"mcs_tier": { "enabled": false }
```

---

## 4. 구현 범위

### 4.1 수정 파일

| 파일 | 변경 내용 | 규모 |
|------|----------|------|
| `dist/wlan/opt/wlan/config/wifi_init_conf.json` | mlan0/mlan1에 `mcs_tier` 섹션 추가 | ~10줄 |
| `dist/wlan/usr/local/scripts/wifi_init.sh` | `apply_mcs_tier()` 함수 추가, `apply_iface_radio_defaults()`에서 호출 | ~40줄 |
| `dist/wlan/usr/local/scripts/wifi_init_config_lib.sh` | `wifi_init_get_mcs_tier()` 헬퍼 추가 (선택) | ~15줄 |
| `dist/wlan/usr/local/scripts/wifi_init_config_test.sh` | mcs_tier 읽기/유효성 테스트 | ~20줄 |
| `docs/wifi_init_conf_guide.md` | mcs_tier 섹션 문서 추가 | ~30줄 |

### 4.2 수정하지 않는 파일

| 파일 | 사유 |
|------|------|
| `wifi_event.sh` / on_connect | 범위 밖 (ratemaxcfg 연동은 별도 feature) |
| `mlanutl.c` / 드라이버 소스 | 이미 구현 완료, 변경 불필요 |
| `wifi_bridge.sh` | MCS 설정과 무관 |

---

## 5. 적용 시점 및 순서

```
wifi_init.sh 실행 순서:
  1. JSON 로드 (global, per-interface)
  2. backup_file.sh (기존)
  3. apply_net_rx_to_mod_para (기존)
  4. insmod moal.ko (드라이버 로드)
  5. apply_iface_radio_defaults() (기존)
     5-1. txpwrlimit hostcmd (기존)
     5-2. reassoctrl enable (기존)
     5-3. rate_adapt_cfg (기존)
     5-4. ★ apply_mcs_tier (신규) ← association 전
  6. wpa_supplicant 시작 (association 시작)
```

→ `apply_mcs_tier()`는 `rate_adapt_cfg` 직후, `wpa_supplicant` 시작 전에 실행

---

## 6. 유효성 검증

```bash
apply_mcs_tier() {
    local iface="$1"
    local enabled ht vht he args=""

    enabled=$(jq -r ".${iface}.mcs_tier.enabled // false" "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    [ "$enabled" = "true" ] || return 0

    # 각 표준별 값 읽기 + 유효성 검증
    ht=$(jq -r ".${iface}.mcs_tier.ht // empty" "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    vht=$(jq -r ".${iface}.mcs_tier.vht // empty" "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    he=$(jq -r ".${iface}.mcs_tier.he // empty" "$WIFI_INIT_CONF_JSON" 2>/dev/null)

    # HT: 7 또는 15만 허용
    if [ -n "$ht" ]; then
        case "$ht" in 7|15) args="$args ht $ht" ;; *)
            logger -p local0.err "[$tag] [$iface] mcs_tier: invalid ht=$ht (must be 7 or 15)"
            return 1 ;;
        esac
    fi

    # VHT: 7, 8, 9만 허용
    if [ -n "$vht" ]; then
        case "$vht" in 7|8|9) args="$args vht $vht" ;; *)
            logger -p local0.err "[$tag] [$iface] mcs_tier: invalid vht=$vht (must be 7/8/9)"
            return 1 ;;
        esac
    fi

    # HE: 7, 9, 11만 허용
    if [ -n "$he" ]; then
        case "$he" in 7|9|11) args="$args he $he" ;; *)
            logger -p local0.err "[$tag] [$iface] mcs_tier: invalid he=$he (must be 7/9/11)"
            return 1 ;;
        esac
    fi

    if [ -z "$args" ]; then
        logger -p local0.warn "[$tag] [$iface] mcs_tier: enabled but no tier specified"
        return 0
    fi

    logger -p local0.info "[$tag] [$iface] mcstiercfg$args"
    mlanutl "$iface" mcstiercfg $args > /dev/null 2>&1 || \
        logger -p local0.err "[$tag] [$iface] mcstiercfg failed"
}
```

---

## 7. 테스트 계획

| # | 테스트 | 검증 방법 |
|---|--------|----------|
| T-01 | enabled=false → mcstiercfg 실행 안 됨 | syslog에 mcstiercfg 로그 없음 |
| T-02 | enabled=true, ht=7 vht=7 he=7 → 정상 적용 | `mlanutl mlan0 mcstiercfg` GET으로 확인 |
| T-03 | he=9만 설정 → HE만 적용, VHT/HT 기본 | GET으로 HE만 변경 확인 |
| T-04 | 잘못된 값 (vht=6) → 에러 로깅, 적용 안 됨 | syslog에 invalid 에러 |
| T-05 | JSON 없음 → skip (에러 없음) | 정상 부팅 |
| T-06 | mcstiercfg 바이너리 없음 → skip | 정상 부팅 |
| T-07 | 적용 후 로밍 → 설정 유지 확인 | 로밍 후 GET으로 tier 유지 확인 |
| T-08 | mlan0/mlan1 다른 설정 → 각각 독립 적용 | 인터페이스별 GET 확인 |

---

## 8. 리스크

| 리스크 | 영향 | 완화 |
|--------|------|------|
| mcstiercfg가 드라이버에 없는 환경 | 실행 실패 | `command -v mlanutl` 또는 실패 시 로깅만 (기존 패턴) |
| 잘못된 tier 값으로 association 실패 | WiFi 연결 불가 | 유효성 검증 + enabled 기본값 false |
| VHT MCS 7 floor (FW 제약) | 설정은 되지만 실제 동작 차이 | 문서에 제약 명시, tier 7이 사실상 최소 |

---

## 9. Success Criteria

| # | 기준 | 측정 방법 |
|---|------|----------|
| SC-01 | JSON에 mcs_tier 설정 후 부팅 시 자동 적용 | `mlanutl mlan0 mcstiercfg` GET 출력 |
| SC-02 | enabled=false일 때 mcstiercfg 미실행 | syslog 확인 |
| SC-03 | 잘못된 값 입력 시 에러 로깅 + 부팅 정상 | syslog 에러 + WiFi 정상 동작 |
| SC-04 | 기존 wifi_init.sh 동작에 영향 없음 | mcs_tier 없는 JSON으로 부팅 정상 |
| SC-05 | wifi_init_conf_guide.md에 사용법 문서화 | 문서 리뷰 |
