# APEX 프레임워크 활용 가이드

## 프로젝트 워크플로우 (권장 순서)

```
1. 세션 시작 → 훅이 자동으로 컨텍스트 주입 (BASE)
2. 작업 계획 → /paul:init → /paul:plan → /paul:apply
3. 작업 중    → BASE가 진행 상태 추적
4. 작업 완료 → /paul:unify → /paul:verify
5. 주간 정리 → /base:groom
6. 보안 점검 → /aegis:audit (필요시)
```

---

## 프레임워크별 명령어 정리

### 1. BASE — 워크스페이스 관리 (매일 사용)

| 명령어 | 용도 | 사용 시점 |
|--------|------|-----------|
| `/base:pulse` | 워크스페이스 건강 상태 요약 | 세션 시작할 때 |
| `/base:status` | 빠른 상태 체크 | 현재 상태 궁금할 때 |
| `/base:groom` | 주간 정리 (stale 항목 정리, 백로그 리뷰) | 매주 금요일 |
| `/base:audit` | 심층 점검 (데드코드, 드리프트 감지) | 월간 또는 필요시 |
| `/base:audit-claude-md` | CLAUDE.md 파일 점검/개선 | CLAUDE.md 관리할 때 |
| `/base:surface-create` | 새 데이터 서페이스 생성 | 새로운 트래킹 영역 추가시 |
| `/base:surface-list` | 등록된 서페이스 목록 | 서페이스 확인할 때 |
| `/base:history` | 워크스페이스 변화 타임라인 | 히스토리 확인할 때 |
| `/base:carl-hygiene` | CARL 규칙 정리/리뷰 | CARL 규칙이 많아졌을 때 |

### 2. PAUL — 프로젝트 오케스트레이션 (기능 개발시 사용)

| 명령어 | 용도 | 사용 시점 |
|--------|------|-----------|
| `/paul:init` | 프로젝트에 PAUL 초기화 | 새 기능/프로젝트 시작 |
| `/paul:progress` | 현재 진행 상태 + 다음 액션 제안 | 작업 중 방향 확인 |
| `/paul:discuss` | 페이즈 비전 탐구 | 계획 전 아이디어 논의 |
| `/paul:plan` | 구현 계획 작성 (PLAN 단계) | 구체적 구현 계획 수립 |
| `/paul:apply` | 계획 실행 (APPLY 단계) | 승인된 계획 실행 |
| `/paul:unify` | 계획 vs 실제 비교 (UNIFY 단계) | 구현 완료 후 검증 |
| `/paul:verify` | UAT (사용자 수락 테스트) | 기능 완성 후 수동 테스트 |
| `/paul:pause` | 세션 중단 핸드오프 문서 생성 | 작업 중단할 때 |
| `/paul:resume` | 핸드오프에서 컨텍스트 복원 | 다음 세션에서 재개 |
| `/paul:discover` | 기술 옵션 리서치 | 기술 선택 고민할 때 |
| `/paul:research` | 서브에이전트로 조사 | 깊은 리서치 필요할 때 |
| `/paul:milestone` | 새 마일스톤 생성 | 큰 목표 단위 설정 |
| `/paul:handoff` | 세션 핸드오프 문서 생성 | 세션 종료시 |
| `/paul:audit` | 현재 계획 아키텍처 감사 | 계획 품질 검증 |

**PAUL 핵심 루프: Plan → Apply → Unify (반복)**

### 3. CARL — 컨텍스트 규칙 엔진 (자동 동작)

CARL은 주로 자동으로 동작합니다. `~/.carl/carl.json`에 규칙을 정의하면 매 프롬프트마다 관련 규칙이 주입됩니다.

현재 활성 규칙 (GLOBAL 도메인):
- 코드에서 절대경로 사용 / 사용자에게는 상대경로로 참조
- 독립적 tool call은 병렬 실행
- 검증 없이 완료 처리 금지

### 4. SEED — 프로젝트 인큐베이션

| 명령어 | 용도 | 사용 시점 |
|--------|------|-----------|
| `/seed` | 새 프로젝트 아이디어 인큐베이션 시작 | 새 프로젝트 구상할 때 |
| `/seed:tasks:ideate` | 아이디어 브레인스토밍 | 아이디어 발산 |
| `/seed:tasks:launch` | 프로젝트 론칭 | 인큐베이션 → 실제 프로젝트 |
| `/seed:tasks:graduate` | 프로젝트 졸업 | PAUL로 넘길 때 |
| `/seed:tasks:status` | 인큐베이션 상태 확인 | 진행 상태 체크 |

### 5. SKILLSMITH — 스킬 빌더

| 명령어 | 용도 | 사용 시점 |
|--------|------|-----------|
| `/skillsmith` | 새 스킬 생성/관리 | 커스텀 스킬 만들 때 |
| `/skillsmith:tasks:scaffold` | 스킬 스캐폴드 | 스킬 구조 생성 |
| `/skillsmith:tasks:audit` | 스킬 품질 감사 | 스킬 검증 |
| `/skillsmith:tasks:discover` | 기존 스킬 탐색 | 사용 가능한 스킬 탐색 |

### 6. AEGIS — 보안 감사

| 명령어 | 용도 | 사용 시점 |
|--------|------|-----------|
| `/aegis:audit` | 전체/부분 보안 감사 실행 | 보안 점검할 때 |
| `/aegis:init` | 프로젝트에 AEGIS 초기화 | 첫 보안 감사 전 |
| `/aegis:report` | 감사 보고서 생성/조회 | 감사 결과 확인 |
| `/aegis:remediate` | 취약점 수정 계획 생성 | 발견된 문제 수정 |
| `/aegis:validate` | AEGIS 설치 검증 | 도구 정상 작동 확인 |
| `/aegis:status` | 감사 진행 상태 | 감사 중 진행률 확인 |

---

## 실전 시나리오 예시

### 새 기능 개발할 때

```
1. /paul:init          ← 프로젝트 초기화 (처음 한번)
2. /paul:discuss       ← "이런 기능을 만들고 싶은데" 논의
3. /paul:plan          ← 구현 계획 작성
4. /paul:apply         ← 계획 실행
5. /paul:unify         ← 계획 vs 실제 비교
6. /paul:verify        ← 수동 테스트
```

### 세션 관리

```
작업 중단: /paul:pause   ← 핸드오프 문서 생성
다음 세션: /paul:resume  ← 컨텍스트 복원 후 계속
```

### 주간 루틴

```
매주 금요일: /base:groom  ← stale 항목 정리, 백로그 리뷰
```

### 릴리스 전

```
/aegis:audit             ← 보안 점검
```

---

## 건너뛴 기능 (추후 활성화 가능)

### Enterprise Plan Audit

PLAN과 APPLY 사이에 아키텍처 감사 단계를 추가하는 기능.

**활성화 시 흐름 변경:**
```
기본:  PLAN → APPLY → UNIFY
감사:  PLAN → AUDIT → APPLY → UNIFY
```

**AUDIT 단계 검토 항목:**
- 아키텍처 결정 적절성 (모듈 분리, 의존성 방향)
- 보안 취약점 가능성
- 성능 영향
- 확장성/유지보수성
- 기존 코드베이스 일관성

**활성화 방법:** `/paul:config` → `enterprise_plan_audit.enabled: true`

**적합한 시점:** 팀 규모 확대, 납품 전 품질 강화 필요시

### Specialized Flows

프로젝트별 특화 스킬/커맨드를 PAUL 워크플로우에 연결하는 기능.

**활성화 방법:** `/paul:flows`

**활용 예시:**
- 반복되는 빌드/테스트 워크플로우 자동화
- 특정 도메인 전문가 스킬 연결 (예: `/embedded-expert`)

---

## 설치 정보

| 프레임워크 | 버전 | 설치일 |
|------------|------|--------|
| CARL | v2.0.0 | 설치됨 |
| BASE | v3.1.3 | 2026-03-30 |
| PAUL | 설치됨 | - |
| SEED | 설치됨 | - |
| SKILLSMITH | 설치됨 | - |
| AEGIS | 설치됨 | - |
