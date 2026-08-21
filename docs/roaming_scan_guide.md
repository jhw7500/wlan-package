# 로밍 스캔 운영 가이드

> 설정 키의 **개별 의미**는 [`wifi_init_conf_guide.md`](wifi_init_conf_guide.md) §11.3(bgscan) / §11.4(roaming)를 본다.
> 이 문서는 **"어떤 구성에 어떤 값을 왜 쓰는가"** 를 실측 근거와 함께 정리한다.

작성 근거: cts-wlan 온타겟 실측 (2026-07-29 ~ 07-30). 수치는 **온타겟 실측 / 로그 재생(시뮬레이션) / 가정 기반 유도값** 셋으로 나뉘며, 실측이 아닌 것은 해당 위치에 라벨을 붙였다.

---

## 개요 — proactive roam owner는 하나만 선택한다

| `roaming.enabled` | proactive owner | `wifi_bgscan.py` requester | 동작 |
|---|---|---|---|
| `true` | `wifi_roam.py` | `iw` | 외부 알고리즘이 후보를 평가하고 BSSID/ID를 명시적으로 전환 |
| `false` | wpa_supplicant native | `wpa_cli scan` | scan 결과를 wpa native selection에 맡김(`TYPE=ONLY` 미사용) |

`wifi_roam`과 wpa native owner는 동시에 쓰지 않는다. 별도 backend JSON 키 대신 부팅
최초 `/run/wifi/<iface>.roam-policy.json`에 owner/backend/bgscan/topology/extra를 원자
snapshot한다. daemon crash-restart나 wifi_init 재시작에도 snapshot이 우선하며 변경은
**재부팅**해야 한다. `bgscan.enabled=false`는 package 주기
스캔만 끄고 wpa_supplicant의 장애 복구·재연결 scan은 막지 않는다.

### topology(Mode A/B) × owner 매트릭스

| topology | wifi_roam owner (`enabled=true`) | wpa native owner (`enabled=false`) |
|---|---|---|
| **Mode B** `generate_network_blocks=false` | 단일 블록, 같은 SSID 외부 로밍. cross-SSID는 `wifi connect` 수동 전환 | 단일 블록 native 선택. cross-SSID는 `wifi connect` 수동 전환 |
| **Mode A** `generate_network_blocks=true` | 다중 블록, 목표 BSSID pin+ID/SSID/BSSID 확인으로 자동 cross-SSID | 다중 블록, wpa 기본 정책으로 자동 cross-SSID |

Mode A/B도 부팅 snapshot과 network block topology를 함께 만들므로 변경 후 재부팅이
필수다. `generate_network_blocks=true`이면 `extra_ssids=[]`여도 Mode A identity와
명시적 SSID writer 금지는 유지된다. 모든
블록은 전역과 동일한 **공통 `freq_list`**를 사용하고 active `scan_freq`는 쓰지 않는다.

wifi_roam owner에서는 스캔 경로가 둘이다.

| 경로 | 언제 | 무엇을 |
|---|---|---|
| **bgscan** (`wifi_bgscan.py`, iw backend) | 로밍 컨디션이 꺼져 있을 때 | `interval`마다 공통 `freq_list` 전 채널 |
| **roam 판정 scan** (`wifi_roam.py`) | 로밍 컨디션 진입 후에만 | 단일 freq=홈채널 우선, 다중 freq=전체 directed active |

`wifi_logger_scan.py` 는 **스스로 스캔하지 않는다.** 커널 scan 완료 이벤트를 관찰해 `mlanutl getscantable` 결과를 `ap.log`에 기록한다. `ap.log`는 진단 기록이며 자동 로밍 후보 RSSI 판정에는 사용하지 않는다.

### 로밍 컨디션이란 (wifi_roam owner 전용)

메인루프가 `rssi >= 임계`(good-signal)면 `interruptible_sleep` 후 `continue` 로 빠진다. **그 경로에서는 Stage 스캔이 한 번도 돌지 않는다.** 임계 미달(`rssi < 임계`)일 때만 `staged_scan_best_candidate()` 가 호출된다.

> **실측 확인**: 임계를 −75로 낮춰 로밍 컨디션을 해제하자 200초 동안 `roaming condition` 0건, roam 스캔 0건, bgscan 3건이었다.

---

## 1. roam scan 구조 (wifi_roam owner)

### 단일 freq — 홈 채널 우선

공통 `freq_list`가 하나이고 현재 결합 채널과 같을 때만 홈채널 스캔을 먼저 수행한다. 모드는 `STAGED_SCAN.home_passive`:

| 값 | 명령 | 성격 |
|---|---|---|
| `true` (기본) | `iw scan freq <홈> passive` | probe 미송신, beacon 수신 |
| `false` | `iw scan freq <홈> ssid <allowed>` | directed probe (hidden 커버) |

후보를 찾으면 즉시 반환한다. 현재 AP RSSI도 같은 scan 결과에서 찾아 `DIFF_TH` 비교 source를 통일한다. 현재 AP 외 같은 SSID BSS를 관측했지만 이득이 부족하면 중복 active scan을 생략한다. 후보 beacon을 받지 못했거나 scan이 실패하면 동일 채널 directed active로 한 번 재확인한다.

### 다중 freq — 전 채널 active 1회

공통 `freq_list`가 2개 이상이면 홈 passive와 `ap.log` cache를 건너뛰고 즉시 다음을 수행한다.

```sh
iw scan freq <공통 freq_list 전체> ssid <allowed>
```

현재 AP와 후보 AP를 같은 시점/scan source로 비교한다. `ap.log`/bgscan cache RSSI는 최대 bgscan interval만큼 과거 값일 수 있어 최종 결정에는 사용하지 않는다.

`iw scan`과 `wpa_cli scan_results` 자체도 누적 BSS cache를 포함할 수 있으므로, 단순히 scan 명령 성공 후 모든 행에 현재 시각을 붙이지 않는다. `iw` 결과의 `last seen`이 **이번 scan 실행시간+1초 이내**인 BSSID만 추출하고, supplicant `scan_results`를 그 집합으로 교차 필터한다. age 정보가 없으면 fail-closed로 후보 판정을 건너뛴다.

#### 단일 freq passive 이후 active 재확인 스킵 조건 — AND 3개

```
SKIP_REDUNDANT_ACTIVE_SCAN  and  home_scan_ok  and  home_covers_all
```

| 조건 | JSON 키 | 의미 |
|---|---|---|
| `SKIP_REDUNDANT_ACTIVE_SCAN` | `STAGED_SCAN.skip_redundant_active` | 설정값 (기본 `true`) |
| `home_covers_all` | — (런타임 판정) | `freq_list ⊆ {홈채널}` — **단일 채널**이면 성립 |
| **`home_scan_ok`** | — (런타임 판정) | **홈 채널에서 현재 AP 외 같은 SSID 후보를 실제로 봤음** |

`home_scan_ok` 가 자주 간과된다. `home_passive=true`인 단일 채널에서 **같은 SSID AP가 자기뿐이면** 스킵이 걸리지 않고 directed active fallback이 한 번 실행된다.

> 기존 실측에서 AP 둘이 모두 ch48일 때 `skip redundant active fallback`이 동작했다. ch36/ch48 다중 구성은 이제 home passive 없이 전 채널 active 1회로 처리한다.

현재 결합 AP의 BSS 테이블 엔트리는 사용 중(in-use)이라 만료되지 않으므로, **"현재 AP만 보임"은 "같은 채널에 다른 AP가 없음"의 증거가 아니다.** 이웃 beacon 유실 시 directed probe가 유일한 재발견 경로라 액티브를 유지한다.

`home_passive=false`이면 첫 홈 스캔 자체가 동일 채널 directed active이므로 후보 유무나 `skip_redundant_active` 값과 무관하게 **1회로 종료**한다. 결과가 없을 때 같은 명령을 즉시 반복하지 않고 다음 backoff tick에서 재시도한다.

---

## 2. 구성별 권장 설정

### 2.1 단일 채널 — 패시브 전용 (공유 매체 probe airtime 0)

#### 전제 — 둘 다 필요

- 공통 `freq_list`가 홈 채널 **1개**
- **그 채널에 같은 SSID AP가 2대 이상** (자기 + 후보 ≥1) → `home_scan_ok` 성립

#### 설정

```json
"mlan0": {
  "bgscan":  { "passive": true, "interval": 60 },
  "roaming": {
    "STAGED_SCAN": {
      "enable": true,
      "home_passive": true,
      "skip_redundant_active": true
    }
  }
}
```

`wpa_supplicant-mlan0.conf` → 전역 및 블록 `freq_list=5240` (1개)

#### 결과

tick당 `['iw','mlan0','scan','freq','5240','passive']` **1회뿐**. Stage 3 스킵으로 **probe 0 → 공유 매체 probe airtime 0**. 단 자기 링크의 off-channel 영향까지 0 은 아니다(§3.2).

#### 한계

**hidden SSID를 구조적으로 못 잡는다**(beacon에 SSID가 없다). 홈 채널에 hidden 로밍 타깃이 있으면 `home_passive: false` 로 바꿔야 하고, 그러면 probe airtime 이 생긴다. 대안인 `skip_redundant_active: false`(패시브+액티브 2회)는 **probe airtime 은 같고**(액티브 스캔 횟수가 둘 다 1회) 스캔 시간(off-channel dwell)이 한 번 더 늘어 자기 링크 tail 비용이 커진다.

### 2.2 다채널 — 직접 액티브

#### 별도 설정이 필요 없다

공통 `freq_list`가 2개 이상이면 홈 passive/cache 단계를 건너뛰고 **전 채널 directed active가 1회 실행**된다. 기본값 그대로 두면 된다.

```json
"mlan0": {
  "bgscan":  { "passive": true, "interval": 60 },
  "roaming": {
    "STAGED_SCAN": {
      "enable": true,
      "home_passive": true,
      "skip_redundant_active": true
    }
  }
}
```

전역 및 모든 블록 `freq_list=5180 5240` (2개 이상)

다채널에서는 `home_passive`와 `skip_redundant_active`가 scan 방식을 바꾸지 않는다. `skip_redundant_active`는 단일 freq passive 모드에서만 평가되며, 단일 freq `home_passive=false`도 directed active 1회로 완결된다.

### 2.3 `bgscan.passive` 판단

**hidden 로밍 타깃이 없다면 두 구성 모두 `true`(기본) 를 권하며, 이 결론은 단말 수와 무관하다.** hidden 타깃을 운용한다면 이득(고정)과 비용(단말 수 비례)의 교차점이 생겨 단말 밀도가 결정 변수가 된다(아래 각주).

액티브 bgscan의 **고유 이득은 하나뿐**이다 — hidden SSID를 **평시에 미리** 캐시·BSS 테이블에 넣어두는 것. 패시브는 beacon만 받으므로 hidden을 못 본다.

| 항목 | 패시브 | 액티브 |
|---|---|---|
| 다채널 일반 AP 커버 | **확보** (§3.4 실측) | 확보 |
| hidden 평시 선점 | 없음 | 있음 (Stage 3 대비 약 1.15초 선점, §3.3 주) |
| 공유 매체 probe airtime | **0** | 4.56ms/스캔 상시 |
| RSSI 스케일 | 차이 없음 (§3.3 — roam Stage 1 vs Stage 3 실측. **bgscan 모드 간·`getscantable` 캐시는 미측정**) | 좌동 |
| 로그 구분 | `passive` 토큰으로 구분 | **Stage 3와 명령 문자열 동일** |

- **다채널**: roam condition에서 direct active가 hidden을 잡는다. active bgscan의 이득은 평시 선점뿐이고 대가는 상시 airtime이다.
- **단일 채널**: cache는 판정에 쓰이지 않는다. hidden 대상은 `home_passive: false`로 발견한다.

단말 수는 **비용 쪽만** 키운다(50대·3초 주기 기준 7.67%). 이득은 단말 수와 무관하므로, 1대여도 `true` 가 낫고 많아지면 격차가 벌어질 뿐이다.

> hidden 로밍 타깃을 운용한다면 "약 1.15초 선점 vs 상시 airtime" 을 견주는 판단이 되고, 단말 밀도가 갈림길이 된다.

---

## 3. 실측 데이터

### 3.1 probe airtime — 공유 매체 비용

`wifi_capture@mlan0`(mlanutl netmon → `rtap`)으로 관리 프레임을 직접 계수. `SUBTYPE_MASK` 는 **제외** 마스크이고 기본값 `0x4100` 은 Beacon·ActionNoAck 만 빼므로 Probe Req/Resp 가 그대로 기록된다. 캡처 로그에는 자기 송신 프레임도 함께 들어오며, **방향은 `SA == 자기 MAC` 비교로 분류**했다(로그 형식은 §7.2 — `MGMT[TX]`/`[RX]` 구분이 없다).

스캔 18회 / 5분:

| 모드 | ProbeReq(자기 송신) | ProbeResp(수신) | airtime/스캔 |
|---|---|---|---|
| **passive** (n=9) | **0** (합 0) | **0** (합 0) | **0.00ms** |
| **active** (n=9) | 4.0 (합 36) | 7.9 (합 71) | **4.56ms** *(가정 기반 유도값)* |

패시브 9회 전부 probe 0건 — **음성 대조 성립**. 즉 "패시브 = 매체 비용 0" 은 추정이 아니라 실측이다.

단말 수를 곱하면:

| 스캔 주기 | 1대 | 10대 | **50대** |
|---|---|---|---|
| 3초 (최악 backoff) | 0.15% | 1.54% | **7.67%** |
| 30초 (상한) | 0.015% | 0.15% | 0.77% |

> **한계**: airtime 은 실측 프레임 수에 **관리 프레임 6Mbps 가정**(ProbeReq 242us / ProbeResp 455us)을 곱해 유도한 값이다 — `(36×242 + 71×455)/9 = 4.56ms`. 캡처 필드에 `radiotap.datarate` 가 없어 실제 rate 는 확인하지 못했다. 24Mbps 면 OFDM 프리앰블 고정분(20us) 때문에 정확히 1/4 이 아니라 **약 1/3.5** 로 줄어든다. **절대값보다 "패시브 0 : 액티브 유의미" 라는 대비와 단말 수 비례 관계**가 신뢰할 부분이다.

### 3.2 off-channel 비용 — 자기 링크

무선 경로(`-I mlan0`) ping 을 흘리며 스캔 시각과 대조해 **상대시간별 프로파일**을 만들었다.

#### 저부하 (1400B × 10/s ≈ 112kbps, n=2980)

| 구간 | median(ms) | p90(ms) | 손실 |
|---|---|---|---|
| baseline (스캔 +4초 초과) | 3.54 | 6.99 | 0% |
| passive 직후 0~2초 | 3.59 (**+0.05**) | — | 0% |
| active 직후 0~2초 | 3.59 (**+0.05**) | — | 0% |

> 저부하 구간에 쓴 측정 ping 의 `-w` 값이 기록되지 않아 `n=2980` 을 §7.3 예시(`-i 0.1 -w 295` → 최대 2950)로 재현할 수 없다. 재측정 시 명령을 함께 남길 것.

상대시간 프로파일에서 영향 위치가 드러난다:

```
passive  +0.0~0.5s  med 5.03  (+1.49)   ← 여기만
         +0.5~1.0s  med 2.00  (−1.54)   ← 오히려 낮음(버퍼 플러시 정황)
         +1.0~1.5s  med 3.48  (baseline 복귀)

active   +0.0~0.5s  med 4.95  (+1.41)   ← 여기만
         +1.0s~     med 3.4~3.8 (baseline 수준)
```

#### 고부하 (실제 4.1Mbps, n=2931)

| 구간 | median(ms) | p90(ms) | **max(ms)** | 손실 |
|---|---|---|---|---|
| baseline | 1.80 | 4.01 | 34.2 | 0% |
| **passive** 0~0.5s | 1.76 | **3.05** | **4.25** | 0% |
| **active** 0~0.5s | 2.13 | **4.72** | **46.30** | 0% |

고부하에서 active 는 median 이 baseline 대비 **+0.33ms(+18%)** 오르고, tail 이 더 크게 벌어진다(active max 46.3 vs baseline 34.2). passive 와의 max 비 10.9배는 0~0.5초라는 **좁은 구간의 표본 최댓값**이라 그대로 일반화하지 말 것. 손실은 전 구간 0%로 드라이버 버퍼가 넘치지 않았다. 실시간 제어 용도라면 tail 이 의미를 가진다.

> **한계**: 고부하가 4.1Mbps(링크 용량 ~10%)다. 더 높은 부하에서 버퍼가 넘쳐 손실로 전환되는지는 미측정.

### 3.3 패시브/액티브 RSSI 스케일 — 차이 없음

같은 tick 안에서 Stage 1(패시브)과 Stage 3(액티브)의 동일 BSSID RSSI 를 짝지어 비교 (n=281). 같은 tick 의 **Stage 1→Stage 3 간격은 실측 1.15초**이며, 문서에서 "선점"으로 부르는 값이 이것이다.

| 지표 | 값 |
|---|---|
| median | **+0.0 dB** |
| mean | +0.06 |
| sd | 0.44 |
| 분포 | −2:2, −1:6, **0:251**, +1:17, +2:4, +3:1 |

**251/281(89.3%)이 정확히 0dB.** 즉 `construct_iw_scan_cmd` 주석의 *"패시브는 beacon 기반이라 `signal_avg` 와 스케일이 가깝다"* 는 논거가 이 하드웨어(NXP moal)에서는 **액티브와 구분되지 않는다.** 모드 선택 기준에서 스케일은 빼도 되고, 남는 것은 **airtime vs hidden 커버**뿐이다.

> **주의**: 전체 로그로 재면 −1.0dB 로 보이는데 그건 시간대가 다른 데이터가 섞인 아티팩트다. **같은 tick 으로 짝지어야** 한다.

### 3.4 다채널 bgscan 커버리지 — 비홈 채널도 남는다

다채널(공통 `freq_list=5180 5240`) + `bgscan.passive=true` + **로밍 컨디션 미진입** 상태로 200초 관찰:

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

`construct_iw_scan_cmd`의 패시브 분기는 공통 목록이 있으면 `["freq"] + freqs`를
붙이므로, **패시브 bgscan도 `freq_list` 전 채널을 훑는다.** 즉 로밍 컨디션에
도달하지 않아도 비홈 채널 일반 AP가 `ap.log`·캐시에 기록된다.

> 이번 블록에 빈 SSID 항목은 없었다. 다만 **"hidden 이라 안 잡힌 것"인지 "그 시점에 없었던 것"인지는 이 데이터로 구분되지 않는다.**

### 3.5 good-signal 리셋 게이트 (PR #138)

`link.json` RSSI 주입으로 조건을 통제한 3-way 대조 (각 3분, backoff 상한을 5초로 낮춰 주입 주기와 정합).

> **Δ 의 정의**: 직전 tick 대비가 아니라 **직전 good-signal 리셋 시점의 RSSI 대비** 변화다(게이트가 `delta_db` 와 비교하는 값). 그래서 `-43 → -44` 는 good-signal 진입값이 늘 −43 이라 Δ0 이고, `-43 → -45 → -41 → -45` 는 진입값이 −43·−41 로 교대해 Δ2 다.

| 구간 | 주입 시퀀스 | 판정 tick | streak (중앙/최대) | `gate_suppressed` 최대 |
|---|---|---|---|---|
| **A** 정체 / off | `-43 → -44` (Δ0) | 18 | **1 / 1** | 0 |
| **B** 정체 / on | `-43 → -44` (Δ0) | 18 | **4 / 4** | **24** |
| **C** 이동 / on | `-43 → -45 → -41 → -45` (Δ2) | 18 | **1 / 1** | 0 |

**판정 tick 이 세 구간 전부 18** — 판정 입력 RSSI 가 결정론적으로 통제됐고 tick 노출 수가 맞아 비교 가능성이 확보됐다(주변 AP RSSI·후보 유무 같은 환경 변수까지 고정된 것은 아니다).

표의 `판정 tick` 은 로밍 컨디션에 진입해 후보를 따진 횟수다. `gate_suppressed` 는 메인루프의 good-signal 분기에서 억제할 때마다 오르는 **누적 카운터**라 good-signal 쪽 tick 까지 세므로 24 > 18 이 정상이다.

- **B vs C**: 같은 `gate=on` 인데 시나리오만 달라 갈린다 → **게이트의 정확성**(정체 억제 / 이동 통과)
- **C 가 off 기준선(A)과 같은 `1/1`**: 이동 시 게이트가 후보 탐색을 지연시키지 않는다는 정황이다. 다만 **이동/off(D) 구간은 측정하지 않았으므로** "게이트 on/off 가 이동에서 동일하다"를 관측으로 못박지는 않는다.

---

## 4. 다채널에서 airtime 을 좌우하는 것은 빈도다

같은 모드에서 **스캔 주기 3초 → 30초면 airtime 이 10배 줄어든다**(50대 기준 7.67% → 0.77%, −90%). 모드 변경 자체는 더 크지만(액티브 7.67% → 패시브 0%), **다채널에서는 Stage 3 액티브가 강제되어 모드가 선택 가능한 변수가 아니다**(§2.2). 그래서 그 구성에서 조절할 수 있는 인자 중에는 빈도가 지배적이다.

### backoff 곡선

후보 미발견 시 `SCAN_NO_RESULT_SLEEP`(기본 3) `× 2^⌊(streak−1)/ROAM_NO_RESULT_FAST_COUNT⌋`, 상한 `ROAM_NO_RESULT_MAX_SLEEP`(기본 30). `ROAM_NO_RESULT_FAST_COUNT`(기본 3)는 **레벨당 반복 횟수** — 각 주기를 3 tick 유지한 뒤 2배로 올라간다 → 곡선 **3,3,3, 6,6,6, 12,12,12, 24,24,24, 30**(상한 도달 135초). `FAST_COUNT=1` 이면 종전 레거시 곡선(3,6,12,24,30)과 동일하다. 상한에 닿은 뒤에는 시간이 지나도 내려오지 않는다(§5 #6).

### good-signal 리셋 게이트로 빈도를 낮춘다

backoff 가 상한에 머물지 못하는 주 원인은 곡선이 아니라 **good-signal 분기의 무조건 리셋**이었다. 정체 로그 18.85h 재생에서 오탐 리셋 662건 중 **624건(94%)이 이 분기**, 그중 **623건이 Δ0dB** — 임계 바로 위에서 진동할 뿐 위치가 안 변한 경우다.

게이트를 켜면 스캔이 **3377 → 1417 (−58.0%)**, 스캔 점유 시간 비율(off-channel dwell duty)이 **5.44% → 2.28%** 로 줄고, 이동 로그(71개 90.1h)는 **변화 없음(0.0%) · 추가지연 0건**이다. *(전부 **로그 재생** 결과이며 온타겟 실측이 아니다. 이 duty 는 스캔 1회의 소요 시간을 기준으로 한 값이라 §3.1 의 probe airtime(4.56ms/스캔)과는 다른 지표다 — 산출에 쓴 스캔 소요시간 가정이 기록돼 있지 않아 두 지표를 직접 환산하지 말 것.)*

```sh
/usr/local/bin/wifi mlan0 roam gate on          # 켜기 (SIGHUP 무재시작)
/usr/local/bin/wifi mlan0 roam gate             # 현재값 확인
```

기본값은 `enable: true` 다(2026-08-03 전환 — 로그 재생 −58% + 실기 3-way 검증 후). 판정 임계 2dB와 결합 후 유예 40초는 실측 기반 코드 고정 정책이며 JSON/CLI 옵션이 아니다. 회귀 의심 시 `wifi <n> roam gate off` 로 즉시 끌 수 있다. 자세한 근거는 `wifi_init_conf_guide.md` §`GOOD_SIGNAL_RESET_GATE`.

---

## 5. 함정 모음

| # | 함정 | 내용 |
|---|---|---|
| 1 | **JSON 실경로** | 데몬이 읽는 것은 **`/usr/local/etc/wifi_init_conf.json`**. `/opt/wlan/config/` 는 **템플릿**이라 편집해도 반영되지 않는다 |
| 2 | **`bgscan.passive=false` 로그 구분** | wifi_roam/iw owner에서는 Stage 3와 명령 문자열이 같아져 로거 태그(`SCAN[` vs `ROAM[`)로 구분한다. wpa native owner는 `wpa_cli scan`이라 명령 자체가 다르다 |
| 3 | **good-signal 분기는 조용하다** | 게이트가 **발동(suppress)하지 않은** tick 은 로그를 남기지 않는다(로그 볼륨 절약 설계). 로그 부재가 "미진입"을 뜻하지 않으므로, 판정은 `streak` 와 `gate_suppressed=N`(no-candidate 줄에 병기) 으로 한다 |
| 4 | **`MAX_SLEEP` 은 순수 코드 상수다** | `ROAM_NO_RESULT_MAX_SLEEP`(30) 은 JSON 으로 바꿀 수 없다 — 과거엔 로더가 `.get()` 으로 읽어 JSON 에 손으로 넣으면 몰래 실효되는 뒷문이 있었으나 감사 D2(2026-07-31)로 봉쇄됐다(`test_max_sleep_backdoor_closed` 가 고정). 실험에서 상한을 바꾸려면 `wifi_roam.py` 의 `DEFAULT_ROAM_NO_RESULT_MAX_SLEEP` 상수를 직접 수정해야 한다 |
| 5 | **후보 미발견이 길어져도 빠른 주기로 복귀하지 않는다** | wifi_roam owner에서 상한(기본 30초)에 도달하면 그 주기를 유지한다. 복귀는 **후보 발견·bgscan hint·good-signal 리셋** 같은 사건으로만 일어난다. wpa native owner에서는 이 backoff/hint가 적용되지 않는다 |
| 6 | **로밍 조건 중 bgscan 정지** | wifi_roam owner에서는 로밍 조건 동안 bgscan을 멈추고 최신 active/home scan으로 직접 판단한다. wpa native owner에는 외부 로밍 컨디션 자체가 없다 |
| 7 | **`wifi` CLI 경로** | `/usr/local/bin/wifi` 인데 **ssh 비대화형 PATH(`/usr/bin:/bin:/usr/sbin:/sbin`)에 없다.** 스크립트에서는 절대 경로를 쓸 것. `>/dev/null 2>&1` 과 겹치면 `command not found` 가 조용히 묻힌다 |
| 8 | **`iw dev link` 는 신뢰 불가** | moal 이 cfg80211 `current_bss` 를 갱신하지 않아 연결 중에도 `Not connected.` 를 반환한다. 링크 판정은 `wlan_link_lib.sh`(wpa_cli → station dump 계단식)를 쓸 것 |
| 9 | **`freq_filter=false`** | deprecated. 공통 global `freq_list`가 있으면 false도 무시하고 iw/wpa_cli 모두 같은 목록을 강제한다. 전대역이 필요하면 공통 목록 자체를 제거해야 한다 |
| 10 | **`periodic_roam.enabled=true`** | 제3 owner 방지를 위해 무시되고 unit은 강제 disable+stop된다. unit의 항상-false ExecCondition도 stale queued job 실행을 막는다 |
| 11 | **boot policy snapshot 손상/부재** | owner를 추측하지 않는다. `/run/.<iface>.roam-policy.latched`가 same-boot snapshot 삭제를 감지하고 wifi_apply_enabled/owner daemon/writer가 fail-closed하여 이중 owner보다 기동 실패를 선택한다 |
| 12 | **Mode A 선택 cleanup 미해결** | BSSID pin **전** `/run/.<iface>.selection-cleanup-pending` JSON WAL을 atomic write+fsync한다. 지속성을 증명하지 못하면 pin을 거부하고 메모리 gate를 유지한다. pin 해제·all-network 복원·canonical reconfigure가 모두 실패해도 WAL/gate가 남는다. daemon 재시작은 boot snapshot 검증보다 cleanup-only preflight를 먼저 수행하므로 `/run/wifi`가 함께 삭제돼도 pin을 복구한 뒤 owner 정책을 fail-closed한다 |

---

## 6. 설정 적용 방법

| 대상 | 방법 | 반영 |
|---|---|---|
| good-signal 게이트 | `wifi <if> roam gate [on\|off]` | SIGHUP 무재시작 |
| 로밍 임계 | `wifi <if> roam th {2G\|5G} <rssi>` | SIGHUP 무재시작 |
| 후보 최소 이득 | `wifi <if> roam diff <dB>` | SIGHUP 무재시작 |
| `STAGED_SCAN` 계열 | JSON 편집 + `systemctl kill --kill-who=main -s SIGHUP wifi_roam@<if>` | SIGHUP |
| `bgscan.passive` / `interval` | JSON 편집만 | **재시작 불필요** — `wifi_bgscan.py` 가 매 스캔 직전 재로드 (실측 확인) |
| `bgscan.enabled` | JSON 편집 | **재부팅** — package scan service boot snapshot; false여도 wpa 복구 scan 유지 |
| `roaming.enabled` | JSON 편집 | **재부팅** — owner/backend boot snapshot |
| `generate_network_blocks` / `extra_ssids` | JSON 편집 | **재부팅** — boot snapshot + network block topology 재생성 |
| 공통 `freq_list` | `wifi <if> freq <freq...>` | persist-only, 다음 부팅. 즉시 `reconfigure`는 순단·wpa 자율선택 가능성에 주의 |

> 조회형 `wifi <if> roam th`(값 없이)는 **표시 전용**이라 SIGHUP 을 보내지 않는다.
>
> `<if>` 는 `mlan0` 같은 실이름이다. 스크립트에서는 `/usr/local/bin/wifi` 절대 경로로 호출할 것(§5 #7).

---

## 7. 검증 방법 — 재현 절차

### 7.1 로밍 판정 조건을 통제하려면 `link.json` 을 주입한다

**로밍 컨디션 진입 판정**에 쓰이는 현재 AP RSSI 는 `link.json` 의 `link.signal_avg` **한 곳에서만** 읽는다(`use_signal_avg=true`). 생산자를 멈추고 그 값을 직접 쓰면 판정 입력을 결정론적으로 통제할 수 있다. 단 **후보 비교용 baseline 은 Stage 1 이 성공하면 `baseline_from_entries()` 로 스캔 스케일에서 재설정**되므로(§1) 그 경로까지 주입값이 지배하지는 않는다.

```sh
systemctl stop wifi_logger_link@mlan0     # 생산자 정지
# link.signal / link.signal_avg 만 "-43 dBm" 형식으로 교체(다른 필드 보존, os.replace 로 원자적)
systemctl start wifi_logger_link@mlan0    # 복구
```

**왜 필요한가**: 자연 조건 A/B 는 실패한다. 게이트처럼 "RSSI 가 임계를 넘나드는 좁은 창"에서만 발동하는 로직은 환경 RSSI 표류로 창을 유지할 수 없다. 실제로 두 번 실패했다 — 임계를 내리면 good-signal 에만 머물러 `tick 0`, 임계를 그대로 두면 RSSI 최댓값이 임계에 못 미쳐 good-signal 진입 0.

#### 주의 셋

1. **기존 파일을 읽어 `signal` 만 바꿔야 한다.** 새로 만들면 `info.freq` 가 없어 `base_threshold` 결정이 실패하고 데몬이 조용히 `continue` 한다.
2. **mtime 갱신이 필수**다 — `_LINK_CACHE` 재파싱과 `LINK_STALE_SEC` 게이트를 통과해야 한다.
3. **backoff 상한이 주입 주기를 삼킨다.** streak 가 상한(30초)이면 데몬이 그만큼 자므로 3초 주기 주입은 깨어나는 순간의 값만 관측된다. 상한을 5초로 낮추고 주입 주기를 맞추면 **tick 수가 주입에 종속돼 구간 간 조건이 자동 정규화**된다. 단 상한은 JSON 으로 바꿀 수 없으므로(§5 #4 — 뒷문 봉쇄) `wifi_roam.py` 의 `DEFAULT_ROAM_NO_RESULT_MAX_SLEEP` 상수를 고쳐 배포해야 하고, 실험 후 원복이 필수다.

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

아래 항목은 이 가이드 작성 과정에서 확인한 **후속 구현 후보**다. 현재 제품 설정을 변경하라는 권고가 아니며, 이 문서 추가만으로 코드·템플릿·스키마 동작은 바뀌지 않는다. 코드나 스키마 수정은 각각 별도 설계·테스트 범위로 추적한다.

| 항목 | 상태 |
|---|---|
| **2층 판정** (60초 peak-to-peak ≥ 5dB) | **미구현.** RSSI 이력이 `ENABLE_PREDICTIVE_ROAM` 게이트 안에서만 쌓이고(출하 기본 `false` → 비어 있음) 샘플 간격도 2~30초로 흔들려 1초 raw 로 검증한 **예비 지표**(AUC 0.9999 — 표본 수·라벨 정의·검증 분할을 기록하지 않아 과적합 여부를 판별할 수 없다)를 그대로 옮길 수 없다. 1층(Δ)만으로 효과 거의 전부 |
| **게이트 기본값 전환** (`enable: true`) | 현장 A/B 후 판단 |
| **87% 기각의 대안 원인** | 여기서 87% 는 **정체 로그 18.85h 재생에서 로밍 컨디션 3379회 중 `No suitable roam candidate` 로 끝난 2953회의 비율**이다. 주간 구간에서 비연결 AP 스캔 RSSI 가 연결 AP 보다 median 11dB 높게 읽힌다(n=2152 쌍 중 **97.3%** 에서 비연결 AP 가 더 높게 읽힘). "연결=평활값 vs 스캔=순간값" 측정 방식 차이 가설을 배제하지 못했다. 이 값은 서로 다른 AP 집단의 통계라 고정 보정값이 아니므로 `DIFF_TH`에서 11dB를 빼지 않는다. 단계형 스캔은 현재 AP가 같은 스캔 결과에 있으면 `baseline_from_entries()`로 비교 소스를 통일한다. 기본 `DIFF_TH=8`을 유지하고 `Roam candidate` 로그의 실제 `diff`와 로밍 성공률을 현장 A/B한 뒤에만 조정한다. 현재 AP가 스캔에서 누락되어 station RSSI로 폴백하는 구간이 반복되면 그 구간을 별도 계측한다 |
| **hidden SSID 실측** | 패시브 블록에 빈 SSID 가 없었으나 "hidden 이라 안 잡힘"인지 "그 시점에 없었음"인지 미구분 (§3.4) |
| **관리 프레임 rate** | airtime 계산의 6Mbps 가정 미검증. `radiotap.datarate` 캡처가 필요 |
| **고부하 한계** | 4.1Mbps 까지만 측정. 더 높은 부하에서 버퍼가 넘쳐 손실로 전환되는지 미확인 |
| **`PING_PONG_PREVENTION.detection_time`** | 시험 환경에서는 기본 5초가 실제 로밍 간격 median 26초보다 좁아 **관측 기간 중 차단 0건**이었다. 다만 이는 **핑퐁 이벤트 자체가 없었을 가능성과 구분되지 않으므로** 죽은 기능으로 일반화하지 않고, 현장 측정 후 window·횟수 조건과 함께 조정한다 |

---

## 관련 문서

- [`wifi_init_conf_guide.md`](wifi_init_conf_guide.md) — 설정 키 레퍼런스 (§11.3 bgscan, §11.4 roaming, §`GOOD_SIGNAL_RESET_GATE`, §`STAGED_SCAN`)
- [`wifi_init_conf.schema.json`](wifi_init_conf.schema.json) — 스키마 (`x-apply-timing`, `x-consumer` 포함)
- [`he_diag_guide.md`](he_diag_guide.md) — HE/링크 진단
