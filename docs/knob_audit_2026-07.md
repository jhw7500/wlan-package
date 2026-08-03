# 노브 전수 감사 (로드맵 ①) — 결과

수행: 2026-07-31, master `7b1c625`. 방법: 템플릿 leaf 키 전수(197, mlan0/mlan1 dedup) →
소비처 역추적(인용 키 + 부모 섹션 동시 등장, jq 경로 포함) → 0건 키 개별 검증(바이너리
strings 포함) → 테스트 커버리지 grep → 시맨틱 판정(메모리·세션 실측 근거).

데이터: [`knob_audit_consumers_2026-07.tsv`](knob_audit_consumers_2026-07.tsv)(키→소비처 197행),
[`knob_audit_testcov_2026-07.tsv`](knob_audit_testcov_2026-07.tsv)(키→테스트 유무).

## 헤드라인

1. **안 읽히는 키 0건.** 소비처 0 후보 3개 전부 해소 — `connect_threshold` = 패치된
   wpa_supplicant 바이너리 소비(imx93/imx8 각 4건 + wifi_init_conf 참조 1건),
   `mcs_tier.ht/he` = jq 직접 읽기(`wifi_init.sh:781,783`, `wifi.sh:2600,2602` — 2글자
   키라 토크나이저가 놓친 것). config 위생은 우려보다 좋다.
2. **진짜 부피는 기본 off 실험 4종 = 22키 + 코드 경로.** PREDICTIVE_ROAM(4) ·
   LOAD_BASED_ROAM(3) · ADAPTIVE_INTERVAL(**9**) · POST_ROAM_ARP+PEER_WARMUP(6).
   전부 출하 off, 실기에서 켠 실적 없음(아는 한). wifi_roam.py 3,300줄의 상당 부분.
3. **로밍 엔진이 2개 공존한다.** `wifi_roam.py` 데몬 외에 `passive_roam.py` 가
   `wifi.sh` 수동 roam CLI(`:1927`)와 opt-in `wifi_periodic_roam@`(기본 off)의 백엔드.
   사문 아님 — 통합 검토 대상.
4. **테스트 커버리지 78/197(40%).** roaming 도메인 집중. 쉘 데몬 도메인
   (temperature/mcp/checker/arping/monitor)은 사실상 0 (cal/config/init_config 만 *_test.sh 존재).
5. **코드-전용 뒷문 노브 1개 잔존**: `ROAM_NO_RESULT_MAX_SLEEP` — 템플릿·스키마에 없는데
   JSON 에 손으로 넣으면 읽힘(RECOVER_SEC 은 제거됨).

## 섹션별 판정

| 섹션 (키수) | 소비처 | 판정 |
|---|---|---|
| global(11)·mac(5)·wbridge(28)·temperature(13)·mmc/mcp(13)·monitor(4)·logger(6+iface6)·eth0(1) | wifi_init.sh, wifi_bridge.sh, wifi_logger_*.sh, wifi_link_monitor.py 등 실가동 | **유지** — 전부 실소비. 테스트 부재는 별건 |
| iface 하드웨어(STANDARD·CAL·TXPWR·Frequency·net_rx·tx_work·rate_adapt·thermal_mgmt·mgmt_hex_dump)(12) | wifi_init.sh 부팅 적용 | **유지** |
| bgscan(6) | wifi_bgscan.py, wifi_apply_enabled.sh | **유지** + `emit_roam_hint` 주의 표기(아래) |
| roaming 코어(TH·DIFF·CHECK_INTERVAL·sleep·cross·fast·staged·gate)(20) | wifi_roam.py, wifi.sh CLI | **유지+테스트 고정**(이미 양호) |
| **실험 4종(22)** | wifi_roam.py (게이트 안) + **wifi.sh status 표시**(감사 당시 누락 — PR #147 리뷰가 발견, jq fallback 이 true 라 키 제거 후 거짓 표시되던 것 함께 수정) | **결정 필요: 제거 vs experimental 표기** ↓ |
| PING_PONG_PREVENTION(4) | wifi_roam.py | 유지 — detection_time 은 환경 의존(시험환경 차단 0건), 현장 측정 후 조정 |
| GOOD_SIGNAL_RESET_GATE(3) | wifi_roam.py, wifi.sh gate CLI | 유지 — 기본 off 는 experimental 아닌 **현장 A/B 대기**(#138 실기 검증 완료) |
| periodic_roam(3) + passive_roam.py | wifi_periodic_roam.sh, wifi.sh roam CLI | **통합 검토** — 두 번째 로밍 엔진. 수동 CLI 의존이라 즉시 제거 불가 |
| checker(10)·arping(7)·mcs_tier(4)·on_connect(2) | 전용 데몬/CLI, 기본 off 셋 있음 | 유지 — opt-in 기능 스위치(유닛 연결 확인) |
| snmp(5)·opc(1) | postinst, wifi_apply_enabled.sh | 유지 — opt-in, 결정 이력 있음(SNMP B안) |
| connect_threshold(1) | **wpa_supplicant 바이너리** | 유지 — 스크립트 grep 만으론 사문으로 오판되는 함정. 문서에 소비자 명시 필요 |

## 결정 포인트 (사용자 판단 필요)

**D1. 실험 4종 처리 — 복잡도 감소의 최대 지렛대**
- (a) **코드째 제거**: wifi_roam.py 대폭 감량 + 22키 삭제. RECOVER_SEC 패턴(증명→테스트 고정→제거)으로
  각각 처리. **의존 주의**: 2층 판정(로드맵 미해결 항목)이 PREDICTIVE 의 RSSI 이력을 전제 —
  PREDICTIVE 를 지우면 2층 판정 계획도 함께 폐기하는 결정이 됨.
- (b) **experimental 표기 유지**: 스키마에 x-experimental, 가이드에 "출하 미사용" 명시.
  코드 부피는 그대로.
- 판단 기준: 현장/고객이 이 4종을 켠 적 있는가.

**D2. `ROAM_NO_RESULT_MAX_SLEEP` 뒷문** — (a) 상수화(로더에서 키 제거, 뒷문 봉쇄) vs
(b) 정식 노출(템플릿+스키마+테스트). 실험 때만 쓰였으므로 (a) 권장.

**D3. `emit_roam_hint`** — 단일 iface 에서 실효 0(roam_condition 게이트로 backoff 중 미발행,
소비 경로 사문). spec(7/28)은 "dual-iface 우발 발화 가능, 삭제 표면 12+" 근거로 코드 보존
결정. 유지한다면 스키마 description 에 "단일 iface 실효 없음" 명시 권장.

**D4. 로밍 엔진 통합(passive_roam)** — 별도 설계 필요, 이번 라운드 범위 밖 권장.

## 결정 결과 (2026-07-31 사용자 확정)

| 항목 | 결정 |
|---|---|
| **D1** 실험 4종 | **부분 제거** — ADAPTIVE_INTERVAL(9키)·LOAD_BASED_ROAM(3키)·POST_ROAM_ARP+PEER_WARMUP(6키) 제거. **PREDICTIVE_ROAM(4키)은 보류**(2층 판정의 RSSI 이력 소스 의존) |
| **D2** MAX_SLEEP 뒷문 | **상수화** — 로더의 JSON 키 읽기 제거, 코드 상수 30 고정 |
| **D3** emit_roam_hint | **유지+표기** — 스키마/가이드에 "단일 iface 실효 없음" 명시 (mlan1 dual-STA 계획 전제) |
| **D4** 엔진 통합 | **보류** — 로드맵 항목으로만 유지 |

실행: 브랜치 `refactor/roam-knob-kill-round1`, 각 항목 "증명→테스트 고정→제거" 순.
테스트 공백(쉘 데몬 도메인)은 로드맵 ③ 만질-때-추출 규칙에 위임(선제 작성은 과투자).
