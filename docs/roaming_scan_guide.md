# 로밍 스캔 운영 가이드

> 설정 키의 **개별 의미**는 [`wifi_init_conf_guide.md`](wifi_init_conf_guide.md) §11.3(bgscan) / §11.4(roaming)를 본다.
> 이 문서는 **"어떤 구성에 어떤 값을 왜 쓰는가"** 를 실측 근거와 함께 정리한다.

작성 근거: cts-wlan 온타겟 실측 (2026-07-29 ~ 07-30). 모든 수치는 실측값이며 추정은 그렇게 표시했다.

---

## 개요 — 스캔은 두 주체가 돌린다

| 주체 | 언제 | 무엇을 |
|---|---|---|
| **bgscan** (`wifi_bgscan.py`) | **평시** (로밍 컨디션 무관) | `interval`(기본 60초)마다 `scan_freq` **전 채널** |
| **roam Stage 1/2/3** (`wifi_roam.py`) | **로밍 컨디션 진입 후에만** | 홈 채널 → 캐시 → 전 채널 |

`wifi_logger_scan.py` 는 **스스로 스캔하지 않는다.** nl80211 scan-completed 이벤트를 감지해 그 시점의 `mlanutl getscantable` 결과를 `ap.log` 에 덤프하는 **수동적 관찰자**다. 즉 누가 스캔했든 그 결과가 `ap.log`(= Stage 2 캐시)에 기록된다.

### 로밍 컨디션이란

메인루프가 `rssi >= 임계`(good-signal)면 `interruptible_sleep` 후 `continue` 로 빠진다. **그 경로에서는 Stage 스캔이 한 번도 돌지 않는다.** 임계 미달(`rssi < 임계`)일 때만 `staged_scan_best_candidate()` 가 호출된다.

> **실측 확인**: 임계를 −75로 낮춰 로밍 컨디션을 해제하자 200초 동안 `roaming condition` 0건, roam 스캔 0건, bgscan 3건이었다.

---

## 1. roam Stage 구조

### Stage 1 — 홈 채널 스캔

현재 결합 채널 **하나만** 훑는다(`home_freq = station["freq"]`). 모드는 `STAGED_SCAN.home_passive`:

| 값 | 명령 | 성격 |
|---|---|---|
| `true` (기본) | `iw scan freq <홈> passive` | probe 미송신, beacon 수신 |
| `false` | `iw scan freq <홈> ssid <allowed>` | directed probe (hidden 커버) |

후보를 찾으면 **즉시 반환**해 Stage 2·3을 건너뛴다. 이때 `baseline_from_entries()` 로 현재 AP RSSI 를 같은 스캔 스케일로 맞춘다(소스 이질성 제거).

### Stage 2 — 교차채널 캐시

`ap.log` 의 **마지막 `[시각]` 블록**을 읽는다. 게이트 4개를 모두 통과해야 판정에 쓰인다:

1. `stage2_entries` 가 비어 있지 않음 — **홈 채널 엔트리를 제외한 뒤**
2. `not self_induced` — 내 로밍 스캔이 유발한 블록이 아님
3. `not clock_stepped` — 시계 점프 없음
4. `scan_block_fresh(ts, cache_fresh_sec)` — 기본 70초 이내

**홈 채널 엔트리를 빼는 이유**: Stage 1이 방금 실측한 채널을 최대 70초 묵은 캐시 RSSI로 재평가하면, 방금 내린 기각(DIFF_TH 미달)을 묵은 값이 뒤집는 역전이 생긴다. 단 Stage 1 스캔이 **실패**하면 이 필터가 걸리지 않는다(그때는 캐시가 유일한 정보 — 의도된 degrade).

### Stage 3 — 전 채널 액티브 폴백

`iw scan freq <scan_freq 전체> ssid <allowed>` (wildcard 없음). **hidden SSID를 발견하는 유일한 경로**다.

#### Stage 3 스킵 조건 — AND 3개

```
SKIP_REDUNDANT_ACTIVE_SCAN  and  home_scan_ok  and  home_covers_all
```

| 조건 | 의미 |
|---|---|
| `SKIP_REDUNDANT_ACTIVE_SCAN` | 설정값 (기본 `true`) |
| `home_covers_all` | `scan_freq ⊆ {홈채널}` — **단일 채널**이면 성립 |
| **`home_scan_ok`** | **홈 채널에서 현재 AP 외 같은 SSID 후보를 실제로 봤음** |

`home_scan_ok` 가 자주 간과된다. 단일 채널이어도 **홈 채널에 같은 SSID AP가 자기뿐이면** 스킵이 걸리지 않고 액티브가 돈다.

> **실측 확인**: AP 둘이 모두 ch48일 때는 `skip redundant active fallback` 이 439건 찍혔다. ch36/ch48로 분리해 홈 채널에 1대만 남기자 Stage 3가 매 tick 실행됐다.

현재 결합 AP의 BSS 테이블 엔트리는 사용 중(in-use)이라 만료되지 않으므로, **"현재 AP만 보임"은 "같은 채널에 다른 AP가 없음"의 증거가 아니다.** 이웃 beacon 유실 시 directed probe가 유일한 재발견 경로라 액티브를 유지한다.

---

## 2. 구성별 권장 설정

### 2.1 단일 채널 — 패시브 전용 (매체 비용 0)

#### 전제 — 둘 다 필요

- `scan_freq` 가 홈 채널 **1개**
- **그 채널에 같은 SSID AP가 2대 이상** (자기 + 후보 ≥1) → `home_scan_ok` 성립

#### 설정

```json
"mlan0": {
  "bgscan":  { "passive": true, "interval": 60 },
  "roaming": {
    "STAGED_SCAN": {
      "enable": true,
      "home_passive": true,
      "skip_redundant_active": true,
      "cache_fresh_sec": 70
    }
  }
}
```

`wpa_supplicant-mlan0.conf` → `scan_freq=5240` (1개)

#### 결과

tick당 `['iw','mlan0','scan','freq','5240','passive']` **1회뿐**. Stage 3 스킵으로 **probe 0 → 공유 매체 비용 0**.

#### 한계

**hidden SSID를 구조적으로 못 잡는다**(beacon에 SSID가 없다). 홈 채널에 hidden 로밍 타깃이 있으면 `home_passive: false` 로 바꿔야 하고, 그러면 매체 비용이 생긴다. 대안으로 `skip_redundant_active: false`(패시브+액티브 2회)도 가능하나 비용이 더 크다.

### 2.2 다채널 — 액티브 폴백

#### 별도 설정이 필요 없다

`scan_freq` 가 2개 이상이면 `home_covers_all=false` 가 되어 스킵 조건이 무너지고 **Stage 3 액티브가 매 tick 자동 실행**된다. **기본값 그대로 두면 된다.**

```json
"mlan0": {
  "bgscan":  { "passive": true, "interval": 60 },
  "roaming": {
    "STAGED_SCAN": {
      "enable": true,
      "home_passive": true,
      "skip_redundant_active": true,
      "cache_fresh_sec": 70
    }
  }
}
```

`scan_freq=5180 5240` (2개 이상)

#### 다채널에서 `home_passive: false` 는 쓰지 말 것

Stage 1이 `iw scan freq <홈> ssid <allowed>`, Stage 3가 `iw scan freq <전채널> ssid <allowed>` 가 되어 **홈 채널을 두 번 액티브로 probe** 한다. 순수 낭비다. 다채널에서 Stage 1의 역할은 "저부하 패시브로 홈 후보를 먼저 찾아 액티브를 회피"하는 것이므로 `true` 가 맞다.

### 2.3 `bgscan.passive` 판단

**두 구성 모두 `true`(기본) 를 권한다.** 단말 수와 무관하다.

액티브 bgscan의 **고유 이득은 하나뿐**이다 — hidden SSID를 **평시에 미리** 캐시·BSS 테이블에 넣어두는 것. 패시브는 beacon만 받으므로 hidden을 못 본다.

| 항목 | 패시브 | 액티브 |
|---|---|---|
| 다채널 일반 AP 커버 | **확보** (§3.4 실측) | 확보 |
| hidden 평시 선점 | 없음 | 있음 (Stage 3 대비 ~1초 선점) |
| 매체 비용 | **0** | 4.6ms/스캔 상시 |
| RSSI 스케일 | 차이 없음 (§3.3) | 차이 없음 |
| 로그 구분 | `passive` 토큰으로 구분 | **Stage 3와 명령 문자열 동일** |

- **다채널**: hidden은 Stage 3가 어차피 잡는다. 액티브 bgscan이 얻는 것은 "1 tick 선점"뿐이고(Stage 1→3 간격 실측 1.15초), 대가는 상시 airtime이다.
- **단일 채널**: 캐시가 **판정에 아예 쓰이지 않는다**(§1 Stage 2 — 홈채널 필터로 공집합). 액티브 bgscan이 hidden을 캐시에 넣어도 읽히지 않는다. hidden을 잡으려면 `home_passive: false` 가 유일한 경로다.

단말 수는 **비용 쪽만** 키운다(50대 × 3초 = 7.67%). 이득은 단말 수와 무관하므로, 1대여도 `true` 가 낫고 많아지면 격차가 벌어질 뿐이다.

> hidden 로밍 타깃을 운용한다면 "1초 선점 vs 상시 airtime" 을 견주는 판단이 되고, 단말 밀도가 갈림길이 된다.

---

## 3. 실측 데이터

### 3.1 probe airtime — 공유 매체 비용

`wifi_capture@mlan0`(mlanutl netmon → `rtap`)으로 관리 프레임을 직접 계수. `SUBTYPE_MASK` 는 **제외** 마스크이고 기본값 `0x4100` 은 Beacon·ActionNoAck 만 빼므로 Probe Req/Resp 가 그대로 기록된다. `MGMT[TX]` 도 잡히므로 자기 송신 probe까지 센다.

스캔 18회 / 5분:

| 모드 | ProbeReq(TX) | ProbeResp(RX) | airtime/스캔 |
|---|---|---|---|
| **passive** (n=9) | **0** (합 0) | **0** (합 0) | **0.00ms** |
| **active** (n=9) | 4.0 (합 36) | 8.0 (합 71) | **4.60ms** |

패시브 9회 전부 probe 0건 — **음성 대조 성립**. 즉 "패시브 = 매체 비용 0" 은 추정이 아니라 실측이다.

단말 수를 곱하면:

| 스캔 주기 | 1대 | 10대 | **50대** |
|---|---|---|---|
| 3초 (최악 backoff) | 0.15% | 1.54% | **7.67%** |
| 30초 (상한) | 0.015% | 0.15% | 0.77% |

> **한계**: airtime 계산이 **관리 프레임 6Mbps 가정**에 의존한다. 캡처 필드에 `radiotap.datarate` 가 없어 실제 rate 는 확인하지 못했다. 24Mbps 면 값이 1/4 이 된다. **절대값보다 "패시브 0 : 액티브 유의미" 라는 대비와 단말 수 비례 관계**가 신뢰할 부분이다.

### 3.2 off-channel 비용 — 자기 링크

무선 경로(`-I mlan0`) ping 을 흘리며 스캔 시각과 대조해 **상대시간별 프로파일**을 만들었다.

#### 저부하 (1400B × 10/s ≈ 112kbps, n=2980)

| 구간 | median | p90 | 손실 |
|---|---|---|---|
| baseline (스캔 +4초 초과) | 3.54ms | 6.99 | 0% |
| passive 직후 0~2초 | 3.59ms (**+0.05**) | — | 0% |
| active 직후 0~2초 | 3.59ms (**+0.05**) | — | 0% |

상대시간 프로파일에서 영향 위치가 드러난다:

```
passive  +0.0~0.5s  med 5.03  (+1.49)   ← 여기만
         +0.5~1.0s  med 2.00  (−1.54)   ← 오히려 낮음(버퍼 플러시 정황)
         +1.0~1.5s  med 3.48  (baseline 복귀)

active   +0.0~0.5s  med 4.95  (+1.41)   ← 여기만
         +1.0s~     med 3.4~3.8 (baseline 수준)
```

#### 고부하 (실제 4.1Mbps, n=2931)

| 구간 | median | p90 | **max** | 손실 |
|---|---|---|---|---|
| baseline | 1.80ms | 4.01 | 34.2 | 0% |
| **passive** 0~0.5s | 1.76ms | **3.05** | **4.25** | 0% |
| **active** 0~0.5s | 2.13ms | **4.72** | **46.30** | 0% |

**median 은 같고 최악값만 10배 차이**다. 손실은 전 구간 0%로 드라이버 버퍼가 넘치지 않았다. 실시간 제어 용도라면 tail 이 의미를 가진다.

> **한계**: 고부하가 4.1Mbps(링크 용량 ~10%)다. 더 높은 부하에서 버퍼가 넘쳐 손실로 전환되는지는 미측정.

### 3.3 패시브/액티브 RSSI 스케일 — 차이 없음

같은 tick 안에서 Stage 1(패시브)과 Stage 3(액티브)의 동일 BSSID RSSI 를 짝지어 비교 (n=281):

| 지표 | 값 |
|---|---|
| median | **+0.0 dB** |
| mean | +0.06 |
| sd | 0.44 |
| 분포 | `-2:2, -1:6, **0:251**, +1:17, +2:4, +3:1` |

**251/281(89.3%)이 정확히 0dB.** 즉 `construct_iw_scan_cmd` 주석의 *"패시브는 beacon 기반이라 `signal_avg` 와 스케일이 가깝다"* 는 논거가 이 하드웨어(NXP moal)에서는 **액티브와 구분되지 않는다.** 모드 선택 기준에서 스케일은 빼도 되고, 남는 것은 **airtime vs hidden 커버**뿐이다.

> **주의**: 전체 로그로 재면 −1.0dB 로 보이는데 그건 시간대가 다른 데이터가 섞인 아티팩트다. **같은 tick 으로 짝지어야** 한다.

### 3.4 다채널 bgscan 커버리지 — 비홈 채널도 남는다

다채널(`scan_freq=5180 5240`) + `bgscan.passive=true` + **로밍 컨디션 미진입** 상태로 200초 관찰:

```
roaming condition: 0     roam 판정 tick: 0     ROAM 스캔 명령: 0
SCAN(bgscan) 명령: 3
['iw', 'mlan0', 'scan', 'freq', '5180', '5240', 'passive']   ← 전 채널 포함
```

`ap.log` 최신 블록 (홈 채널 = ch36):

| 채널 | 개수 | 비고 |
|---|---|---|
| ch036 (홈) | 7개 | |
| **ch048 (비홈)** | **2개** | `04:ba:d6`(`jhw_wlan_`, −44dBm) 포함 — 로밍 후보 |

`construct_iw_scan_cmd` 의 패시브 분기가 `freq_filter and scan_freqs` 면 `["freq"] + scan_freqs` 를 붙이므로, **패시브 bgscan 도 `scan_freq` 전 채널을 훑는다.** 즉 로밍 컨디션에 도달하지 않아도 비홈 채널 일반 AP 가 `ap.log`·캐시에 기록된다.

> 이번 블록에 빈 SSID 항목은 없었다. 다만 **"hidden 이라 안 잡힌 것"인지 "그 시점에 없었던 것"인지는 이 데이터로 구분되지 않는다.**

### 3.5 good-signal 리셋 게이트 (PR #138)

`link.json` RSSI 주입으로 조건을 통제한 3-way 대조 (각 3분, backoff 상한을 5초로 낮춰 주입 주기와 정합):

| 구간 | 주입 시퀀스 | tick | streak (중앙/최대) | `gate_suppressed` 최대 |
|---|---|---|---|---|
| **A** 정체 / off | `-43 → -44` (Δ0) | 18 | **1 / 1** | 0 |
| **B** 정체 / on | `-43 → -44` (Δ0) | 18 | **4 / 4** | **24** |
| **C** 이동 / on | `-43 → -45 → -41 → -45` (Δ2) | 18 | **1 / 1** | 0 |

**tick 이 세 구간 전부 18** — 조건이 완전히 통제됐다.

- **B vs C**: 같은 `gate=on` 인데 시나리오만 달라 갈린다 → **게이트의 정확성**(정체 억제 / 이동 통과)
- **A vs C 동일**: 이동 시 게이트 on 이 off 와 구분되지 않는다 → **재탐색성 완전 보존**

---

## 4. airtime 의 지배 인자는 모드가 아니라 빈도다

같은 모드에서 **스캔 주기 3초 → 30초면 airtime 이 10배 줄어든다**(50대 기준 7.67% → 0.77%). 모드 선택으로 얻는 차이보다 크다.

### backoff 곡선

후보 미발견 시 `SCAN_NO_RESULT_SLEEP × 2^(streak-1)`, 상한 `ROAM_NO_RESULT_MAX_SLEEP`(기본 30). `ROAM_NO_RESULT_FAST_COUNT`(기본 3)만큼은 빠른 주기를 유지한 뒤 지수 성장 → 실측 곡선 **3, 3, 3, 6, 12, 24, 30**.

### good-signal 리셋 게이트로 빈도를 낮춘다

backoff 가 상한에 머물지 못하는 주 원인은 곡선이 아니라 **good-signal 분기의 무조건 리셋**이었다. 정체 로그 18.85h 재생에서 오탐 리셋 662건 중 **624건(94%)이 이 분기**, 그중 **623건이 Δ0dB** — 임계 바로 위에서 진동할 뿐 위치가 안 변한 경우다.

게이트를 켜면 스캔이 **3377 → 1417 (−58.0%)**, airtime duty **5.44% → 2.28%** 로 줄고, 이동 로그(71개 90.1h)는 **−0.0% · 추가지연 0건**이다.

```sh
wifi 0 roam gate on          # 켜기 (SIGHUP 무재시작)
wifi 0 roam gate             # 현재값 확인
wifi 0 roam gate delta 3     # 판정 임계 조정
```

기본값은 `enable: false`(무회귀)이므로 **현장 A/B 후 전환을 판단**한다. 자세한 근거는 `wifi_init_conf_guide.md` §`GOOD_SIGNAL_RESET_GATE`.

---

## 5. 함정 모음

| # | 함정 | 내용 |
|---|---|---|
| 1 | **`cache_fresh_sec` vs `bgscan.interval`** | 두 값이 **독립 설정**이다. `interval` 을 90으로 올리면 캐시가 영구 stale 이 되어 Stage 2가 죽고 액티브 폴백만 남는다. `cache_fresh_sec = interval + 여유(≥10초)` 를 유지할 것 |
| 2 | **JSON 실경로** | 데몬이 읽는 것은 **`/usr/local/etc/wifi_init_conf.json`**. `/opt/wlan/config/` 는 **템플릿**이라 편집해도 반영되지 않는다 |
| 3 | **`bgscan.passive=false` 의 부작용** | Stage 3와 **명령 문자열이 완전히 동일**해져 로그에서 주체를 로거 태그(`SCAN[` vs `ROAM[`)로만 구분해야 한다. 패시브면 `passive` 토큰으로 구분된다 |
| 4 | **good-signal 분기는 조용하다** | 억제가 없으면 **로그를 남기지 않는다**(볼륨 억제 설계). 로그 부재가 "미진입"을 뜻하지 않으므로, 판정은 `streak` 와 `gate_suppressed=N`(no-candidate 줄에 병기) 으로 한다 |
| 5 | **`MAX_SLEEP`·`RECOVER_SEC` 은 JSON 키가 없다** | `ROAM_NO_RESULT_MAX_SLEEP`·`ROAM_NO_RESULT_BACKOFF_RECOVER_SEC` 은 코드 상수만 있고 배포 JSON·스키마에 키가 없다. **수동 추가해야 실효**한다 |
| 6 | **`RECOVER_SEC` 은 실효 0** | 상한 도달 후 streak 를 1 감소시키지만 같은 경로의 clamp 가 되돌려 순효과가 없다(latent bug). 실기에서 streak 가 `7↔6` 으로 진동하는 모습으로 관측된다. 따라서 후보 미발견 상태가 길어져도 스캔 주기는 상한에 계속 머물며 빠른 탐색 주기로 자동 복귀하지 않는다 |
| 7 | **bgscan 기아** | 로밍 컨디션이 지속되면 roam 스캔이 `_record_roam_scan_time()` 으로 bgscan 타이머를 계속 밀어내 **bgscan 자체 스캔이 0회**가 된다. 정상 환경(로밍 컨디션 미진입)에서는 정상 동작하므로 **구조적 결함이 아니다** — 임계를 비정상적으로 높인 시험 세팅에서 나타난다 |
| 8 | **`wifi` CLI 경로** | `/usr/local/bin/wifi` 인데 **ssh 비대화형 PATH(`/usr/bin:/bin:/usr/sbin:/sbin`)에 없다.** 스크립트에서는 절대 경로를 쓸 것. `>/dev/null 2>&1` 과 겹치면 `command not found` 가 조용히 묻힌다 |
| 9 | **`iw dev link` 는 신뢰 불가** | moal 이 cfg80211 `current_bss` 를 갱신하지 않아 연결 중에도 `Not connected.` 를 반환한다. 링크 판정은 `wlan_link_lib.sh`(wpa_cli → station dump 계단식)를 쓸 것 |

---

## 6. 설정 적용 방법

| 대상 | 방법 | 반영 |
|---|---|---|
| good-signal 게이트 | `wifi <if> roam gate [on\|off\|delta <dB>\|grace <sec>]` | SIGHUP 무재시작 |
| 로밍 임계 | `wifi <if> roam th {2G\|5G} <rssi>` | SIGHUP 무재시작 |
| 후보 최소 이득 | `wifi <if> roam diff <dB>` | SIGHUP 무재시작 |
| `STAGED_SCAN` 계열 | JSON 편집 + `systemctl kill --kill-who=main -s SIGHUP wifi_roam@<if>` | SIGHUP |
| `bgscan.passive` / `interval` | JSON 편집만 | **재시작 불필요** — `wifi_bgscan.py` 가 매 스캔 직전 재로드 (실측 확인) |
| `scan_freq` | `wpa_supplicant-<if>.conf` 편집 | `wpa_cli reconfigure` (순단·자율선택 유발 주의) |

> 조회형 `wifi <if> roam th`(값 없이)는 **표시 전용**이라 SIGHUP 을 보내지 않는다.

---

## 7. 검증 방법 — 재현 절차

### 7.1 로밍 판정 조건을 통제하려면 `link.json` 을 주입한다

`wifi_roam` 은 현재 AP RSSI 를 `link.json` 의 `link.signal_avg` **한 곳에서만** 읽는다(`use_signal_avg=true`). 생산자를 멈추고 그 값을 직접 쓰면 시나리오를 100% 재현할 수 있다.

```sh
systemctl stop wifi_logger_link@mlan0     # 생산자 정지
# link.signal / link.signal_avg 만 "-43 dBm" 형식으로 교체(다른 필드 보존, os.replace 로 원자적)
systemctl start wifi_logger_link@mlan0    # 복구
```

**왜 필요한가**: 자연 조건 A/B 는 실패한다. 게이트처럼 "RSSI 가 임계를 넘나드는 좁은 창"에서만 발동하는 로직은 환경 RSSI 표류로 창을 유지할 수 없다. 실제로 두 번 실패했다 — 임계를 내리면 good-signal 에만 머물러 `tick 0`, 임계를 그대로 두면 RSSI 최댓값이 임계에 못 미쳐 good-signal 진입 0.

#### 주의 셋

1. **기존 파일을 읽어 `signal` 만 바꿔야 한다.** 새로 만들면 `info.freq` 가 없어 `base_threshold` 결정이 실패하고 데몬이 조용히 `continue` 한다.
2. **mtime 갱신이 필수**다 — `_LINK_CACHE` 재파싱과 `LINK_STALE_SEC` 게이트를 통과해야 한다.
3. **backoff 상한이 주입 주기를 삼킨다.** streak 가 상한(30초)이면 데몬이 그만큼 자므로 3초 주기 주입은 깨어나는 순간의 값만 관측된다. `ROAM_NO_RESULT_MAX_SLEEP` 을 5초로 낮추고(§5 #5 — 수동 추가 필요) 주입 주기를 맞추면 **tick 수가 주입에 종속돼 구간 간 조건이 자동 정규화**된다.

### 7.2 probe airtime 을 재려면 `wifi_capture` 를 쓴다

```sh
systemctl start wifi_capture@mlan0     # netmon → rtap 생성. 연결 유지됨(실측 확인)
# /var/log/cantops/mgmt/mlan0/mgmt.log 에 MGMT[debug] 라인이 쌓인다
systemctl stop wifi_capture@mlan0
```

로그 형식(버전 0.2): `MGMT[TX]`/`[RX]` 구분이 **없고** `MGMT[debug]` 로 통일돼 있다. 방향은 **`SA == 자기 MAC`** 으로 판정한다(자기 MAC 은 로그의 `MAC: xx` 라인에서 취득).

스캔 시각과 대조할 때 **창을 다음 스캔 시각까지 잘라야** 한다. Stage 1(패시브) 직후 ~1.1초 뒤에 Stage 3(액티브)가 오므로 고정 3초 창을 쓰면 패시브 창에 액티브의 probe 가 섞여 **두 모드가 같은 값**으로 나온다.

### 7.3 off-channel 비용은 부하와 측정을 분리한다

```sh
ping -I mlan0 -i 0.002 -s 1400 -w 300 <target> &    # 부하 (~5.6Mbps)
ping -D -O -I mlan0 -i 0.1 -s 64 -w 295 <target>    # 측정 (RTT/손실 관측)
```

`-D`(timestamp) + `-O`(무응답 보고)로 손실을 seq 누락 없이 잡는다. 스캔 시각 기준 **상대시간 빈**으로 집계해야 영향 위치가 드러난다(전체 평균으로는 묻힌다).

---

## 부록 — 미해결 · 후속

| 항목 | 상태 |
|---|---|
| **2층 판정** (60초 peak-to-peak ≥ 5dB) | **미구현.** RSSI 이력이 `ENABLE_PREDICTIVE_ROAM` 게이트 안에서만 쌓이고(출하 기본 `false` → 비어 있음) 샘플 간격도 2~30초로 흔들려 1초 raw 로 검증한 지표(AUC 0.9999)를 그대로 옮길 수 없다. 1층(Δ)만으로 효과 거의 전부 |
| **게이트 기본값 전환** (`enable: true`) | 현장 A/B 후 판단 |
| **87% 기각의 대안 원인** | 주간 구간에서 비연결 AP 스캔 RSSI 가 연결 AP 보다 median 11dB 높게 읽힌다(n=2152, 97.3%). "연결=평활값 vs 스캔=순간값" 측정 방식 차이 가설을 배제하지 못했다. 이 값은 서로 다른 AP 집단의 통계라 고정 보정값이 아니므로 `DIFF_TH`에서 11dB를 빼지 않는다. 단계형 스캔은 현재 AP가 같은 스캔 결과에 있으면 `baseline_from_entries()`로 비교 소스를 통일한다. 기본 `DIFF_TH=8`을 유지하고 `Roam candidate` 로그의 실제 `diff`와 로밍 성공률을 현장 A/B한 뒤에만 조정한다. 현재 AP가 스캔에서 누락되어 station RSSI로 폴백하는 구간이 반복되면 그 구간을 별도 계측한다 |
| **hidden SSID 실측** | 패시브 블록에 빈 SSID 가 없었으나 "hidden 이라 안 잡힘"인지 "그 시점에 없었음"인지 미구분 (§3.4) |
| **관리 프레임 rate** | airtime 계산의 6Mbps 가정 미검증. `radiotap.datarate` 캡처가 필요 |
| **고부하 한계** | 4.1Mbps 까지만 측정. 더 높은 부하에서 버퍼가 넘쳐 손실로 전환되는지 미확인 |
| **`PING_PONG.detection_time`** | 5초 → 실제 로밍 간격 median 26초보다 좁아 실측 차단 0건인 죽은 노브 |

---

## 관련 문서

- [`wifi_init_conf_guide.md`](wifi_init_conf_guide.md) — 설정 키 레퍼런스 (§11.3 bgscan, §11.4 roaming, §`GOOD_SIGNAL_RESET_GATE`, §`STAGED_SCAN`)
- [`wifi_init_conf.schema.json`](wifi_init_conf.schema.json) — 스키마 (`x-apply-timing`, `x-consumer` 포함)
- [`he_diag_guide.md`](he_diag_guide.md) — HE/링크 진단
