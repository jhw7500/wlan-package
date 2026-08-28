# `antcfg 0x0101` 조건의 scan-return TX wedge driver/F/W 분석 요청

> 공개 검토본: SSID/BSSID/IP/hostname과 개인 절대 경로는 일관된
> `*_REDACTED` 토큰 또는 repo/project-relative 경로로 치환했다. 원시 target artifact는
> 로컬 보존본이 정본이다. p149.115 + `antcfg 0x0101`의 재현과 p149.81
> matched-pair의 미재현만 bounded evidence이며, active scan 강제는 보편적 안전
> 보장이 아닌 제한된 완화로 취급한다.
>
> 2026-08-25 갱신: 계측 A/B에서 장애 counter가 `GET_LOG` QoS array index 7에
> 집중되고, 정상 off-channel scan마다 index 7 frame이 규칙적으로 2개씩 증가하는
> 경계를 확인했다. 아래에서는 이 관측 사실과, 이를 scan power-management frame으로
> 해석하는 추론을 구분한다.

## 1. 세션 역할

이 문서의 역할은 **드라이버 소스 분석과 F/W trace 요청의 공통 입력**이다.

- 보드 재현시험, SSH 접속, 서비스 제어, 모듈/F/W 교체는 다른 세션에서 수행한다.
- 이 세션에서는 보드를 조작하지 않는다.
- 기능 수정안을 먼저 적용하지 않는다.
- 기존 증거와 현재 드라이버 소스를 읽고, 최초 불일치 지점과 원인 판별용
  instrumentation을 설계한다.
- instrumentation 또는 수정안의 실제 적용·보드 검증은 시험 세션에서 수행한다.

## 2. 분석 목표

543.p18 driver와 17.92.1.p149.115 F/W 조합에서 association 전에
`antcfg 0x0101`을 적용하면, 반복 `iw` scan이 정상 완료된 뒤 association은 유지되지만
TX data-path만 정지한다.

다음을 소스 근거로 규명해야 한다.

1. `antcfg` 설정의 driver/FW 전달 경로
2. scan 시작·완료·operating-channel 복귀 경로
3. scan 중 정지된 TX queue가 완료 후 재개되는 경로
4. `0x303`과 `0x101` 조건에서 달라질 수 있는 상태
5. silent wedge의 최초 발생 경계를 찾기 위한 최소 instrumentation 지점

`antcfg`는 확인된 재현 trigger/cofactor지만, 아직 `antcfg` 명령 구현 자체가 결함이라고
확정한 것은 아니다. 실제 결함은 해당 antenna path에서 노출되는 FW/driver의 off-channel
scan 복귀 또는 TX 처리 상호작용일 수 있다.

계측 후 최우선 목표는 host queue wake 여부가 아니라, **off-channel 진입/복귀 때 F/W가
자체 생성하는 QoS index 7 frame의 종류·PM bit·Tx status와 physical 1x1에서의 ACK 실패
원인**을 확인하는 것이다.

## 3. 실행 환경

| 항목 | 값 |
|---|---|
| driver | 543.p18 |
| F/W | 17.92.1.p149.115 |
| mlan srcversion | `69CD10BAA7F3A642C954443` |
| moal srcversion | `E14FF2EA56EE8DA9F44DC18` |
| interface | `mlan0` |
| SSID | `LAB_SSID_REDACTED` |
| BSSID | `LAB_BSSID_REDACTED` |
| 연결 주파수 | 5180 MHz |
| station IP | `WLAN_STATION_IP_REDACTED` |
| gateway | `WLAN_GATEWAY_IP_REDACTED` |
| 관리 경로 | `eth0`, `MGMT_HOST_IP_REDACTED` |

### 3.1 실행 바이너리 hash

```text
mlan_imx93.ko
c351a0d63f75d53f99ca0b74aba3911d79e9aaf1f4e269dac68cba9b06e4c46a

moal_imx93.ko
87b9d0dc5b86c4a40560050f4e9c5a2c8662acc97bea6b516f0f094fcfc9b6a0

sd9098_wlan_v1.bin
7c3ef6e12d3cfc9bd638d1571ccf6ddd2e96e0ed179ec70664ccb1df0ba29e57

wifi_mod_para.conf
9586de01f4775113595a80e625820132308a92f42cfa1235907e50c6792abe0c

wifi_init_conf.json
be46944c89e45f0b07a6a672f6a4a6fd23e8ef993a7d88a93ed5caab9219c2c9
```

분석을 시작하기 전에 현재 소스가 위 module의 version/srcversion과 일치하는지 확인한다.
일치하지 않으면 이후 결론을 확정하지 말고 어떤 소스·빌드 차이가 있는지 먼저 보고한다.

### 3.2 재현 시 module 입력

```text
insmod /opt/wlan/driver/mlan_imx93.ko
insmod /opt/wlan/driver/moal_imx93.ko \
  mod_para=cts/wifi_mod_para.conf \
  tx_work=0 \
  bridge_mode=1 \
  bridge_debug=0 \
  bridge_wlan_idx=0 \
  bridge_keepalive_ms=1 \
  bridge_keepalive_idle_ms=20 \
  bridge_local_hairpin=0 \
  wq_sched_policy=1 \
  wq_sched_prio=45
```

## 4. 최소 재현 조건

시험 세션에서 다음 조건으로 재현했다.

1. `wifi_init`을 비활성화한 clean boot에서 시작
2. 위 module 인자로 수동 로드
3. association 전에 다음 설정 하나만 적용

   ```text
   mlanutl mlan0 antcfg 0x0101
   ```

4. rate-adapt SET 미실행
   - live GET은 FW 기본 noise 기반 dynamic mode
   - 평가 주기 100 ms
5. MCS SET/GET 및 reassociate 미실행
6. TX-power/thermal과 radio-default 설정 미실행
7. network/sysctl/peer-route 및 ExecStartPost 동작 미실행
8. 연결 중 `wpa_supplicant`만 active
9. roam/bgscan/capture/logger/checker/event/bridge/FW-watch 서비스 inactive
10. 다음 scan을 완료 후 5초 간격으로 반복

```text
iw mlan0 scan freq 5180 5200 5220 5240 ssid LAB_SSID_REDACTED
```

## 5. 최소 재현 결과

유효 시험 boot ID:

```text
eeb83302-67d6-4122-b1cf-050989d7d2c4
```

| 항목 | 값 |
|---|---|
| antenna live GET | Tx `0x101`, Rx `0x101` |
| rate-adapt | FW 기본 dynamic, 100 ms |
| MCS/reassociate | FW 기본값/미실행 |
| 시작 상태 | `COMPLETED`, `tx failed=0`, ping 3/3 |
| scan 1~4 | rc 0, `tx failed=0` |
| 장애 시작 | scan 5 / 33초 |
| `tx failed` | 0 → 2,525 |
| ping 전후 counter | 2,549 → 5,169 |
| 최종 ping | 0/3, rc 1 |
| scan start/result | 5/5 |
| own scan | 0 |
| disconnect/auth error | 0 |
| FW dump/reset/CMD timeout | 0 |

장애 뒤에도 다음 상태는 유지됐다.

- `wpa_state=COMPLETED`
- 동일 SSID/BSSID/5180 MHz
- `iw scan` 명령 자체는 정상 반환
- disconnect나 FW recovery marker 없음

따라서 association 손실이 아니라 **scan-return 이후 TX/ACK data-path만 silent
wedge되는 서명**이다.

## 6. 대조 시험

| profile | antenna | rate-adapt | 결과 |
|---|---|---|---|
| exact module 입력만 | FW 기본값 | FW 기본값 | 60/60 미재현 |
| MCS-only | Tx/Rx `0x303` | FW 기본 dynamic | 60/60 미재현 |
| AR-only | Tx/Rx `0x101` | static 70/90/100 ms | scan 6 재현 |
| antcfg-only | Tx/Rx `0x101` | FW 기본 dynamic | scan 5 재현 |

현재 확정 가능한 범위는 다음과 같다.

- `antcfg 0x0101`은 이 gate의 충분한 재현 trigger/cofactor다.
- static rate-adapt, MCS 조작 및 reassociate는 이번 재현의 필수조건이 아니다.
- rate-only는 실행하지 않았으므로 rate 설정도 독립 trigger인지 여부는 판정하지 않았다.
- `antcfg` 명령 자체와 FW/driver 내부 scan 복귀 결함은 아직 구분되지 않았다.

### 6.1 2026-08-25 corrected 방향 분리와 positive-control

matching utility로 physical antenna mode와 host `user_htstream`을 따로 검증한 후 동일한
gate를 다시 실행했다.

| 상태 | 결과 |
|---|---|
| physical Tx1/Rx2 | 60/60 미재현 |
| physical Tx2/Rx2 + host Tx2/Rx1 (`user_htstream=0x2121`) | 60/60 미재현 |
| physical Tx1/Rx1 + host Tx1/Rx1 (`user_htstream=0x1111`) | scan 32 재현 |

positive-control은 scan 1~30에서 `tx failed=0`, scan 31에서 1, scan 32에서 2,810으로
증가했다. 최종 ping은 0/3이었지만 `COMPLETED`, 동일 BSSID/5180 MHz를 유지했고
external request/result는 32/32, own scan과 disconnect는 0이었다. 앞선 scan 5
재현과 서명은 같지만 onset이 scan 32로 달라 발생 시점은 비결정적이다.

장애 상태의 `/proc/mwlan/adapter0/mlan0/{info,debug,log}`는 carrier on, netdev queue
started, qdisc backlog 0, `tx_pending=0`, `tx_pkts_queued=0`, `wmm_tx_pending=0`,
`scan_processing=0`, command/Tx timeout 0을 보였다. 반면 `dot11FailedCount=68654`,
`dot11ACKFailureCount=686553`으로 증가했고 beacon receive는 계속되며 missed는 0이었다.

따라서 후속 분석은 host queue wake 누락만이 아니라 **scan 복귀 뒤 FW/MAC/RF Tx 상태와
Tx ACK failure 원인**에 우선순위를 둬야 한다. 첫 실패 packet의 Tx descriptor/rate/NSS,
FW Tx-status reason, RF/BB Tx enable과 channel/antenna context를 working Rx-only와
failing 1x1에서 비교한다.

상세 증거:

```text
docs/ant_1x1_positive_control_2026-08-25.md
```

### 6.2 고정 driver의 F/W-only A/B와 driver-side 배제

동일한 main/505.p14 `mlan`/`moal` 바이너리를 고정한 matched-pair 결과는 다음과 같다.

| F/W | 시험 | 결과 |
|---|---|---|
| p149.115 | 단일 off-channel active | scan 10/57초 재현 |
| p149.115 | 단일 off-channel passive | scan 14/79초 재현 |
| p149.115 | 두 off-channel active | scan 11/117초 재현 |
| p149.81 | 단일 off-channel active, 독립 boot 2회 | 30/30 + 30/30 미재현 |
| p149.81 | 단일 off-channel passive | 30/30 미재현 |
| p149.81 | 두 off-channel active | 30/30 미재현 |

따라서 main/505와 ported/543의 차이는 장애 발생의 필요조건이 아니다. 현재 증거는
**p149.115 F/W 자체 또는 p149.115와 driver/AP의 상호작용**을 주요 차이로 강하게
지지한다.

별도의 source/ABI 비교에서 확인한 driver-side 범위는 다음과 같다.

- main에서 추가한 per-direction, SET-only `user_htstream` 갱신 semantics는 ported에도
  반영돼 있다.
- `HostCmd_CMD_RF_ANTENNA`의 기존 2G/5G byte prefix와 field offset은 동일하고,
  ported는 끝에 6G Tx/Rx 2 byte를 추가해 command size가 16에서 18로 늘어난다.
- ioctl capture와 계측 marker에서 두 `antcfg` 인자가 command까지 정확히 전달됐다.
- main 방식 `mlanutl`을 ported driver에 사용하는 ABI 불일치는 NSS 표시가 조용히
  누락될 수 있는 별도 호환성 문제다. 그러나 이번 유효 run의 HostCmd/physical GET과
  `user_htstream`은 직접 확인됐으므로 현재 scan wedge의 설명으로는 부족하다.

### 6.3 계측 driver A/B

| 조건 | physical Tx/Rx | host `user_htstream` | 결과 |
|---|---|---|---|
| B: symmetric 1x1 positive control | `0x101/0x101` | `0x1111` | scan 4 재현 |
| A: Rx-NSS-only 요청 | 실측 `0x303/0x303` | `0x2121` | 60/60 미재현 |
| A marker 보충 | `0x303/0x303` | `0x2121` | 추가 10/10 미재현 |
| 기존 반대 비대칭 | `0x101/0x303` | Tx 1SS/Rx 2SS intent | 60/60 미재현 |

B에서 기존 장애가 재현됐으므로 instrumentation이 장애를 숨겼다고 볼 근거는 없다.
현재 positive cell은 physical Tx/Rx가 동시에 1-path인 상태뿐이다. Tx 1SS 단독과 host
advertised Rx 1SS 단독은 충분조건이 아니다. 다만 F/W가 physical Tx2/Rx1 비대칭을
허용하지 않아 physical Rx 1-path 단독과 symmetric 1x1 상호작용은 아직 분리되지 않았다.

### 6.4 최초 failure boundary: QoS array index 7

#### 계측으로 확인한 사실

1. 정상 4채널 scan은 home 5180 MHz와 off-channel 5200/5220/5240 MHz로 구성된다.
   A marker 보충 run의 각 정상 scan에서 `dot11TransmittedFrameCount`가 6 증가했고,
   본시험 60회 종료 시 QoS index 7 성공값은
   `370 = baseline 10 + 60 * 3 * 2`였다. 즉 off-channel excursion 하나마다
   index 7 성공 frame이 정확히 2개씩 증가했다.
2. 과거 단일 off-channel scan도 index 7 성공값이 active run에서
   `58 = baseline 4 + 27 * 2`, `18 = baseline 8 + 5 * 2`, passive run에서
   `25 = baseline 7 + 9 * 2`였다. passive scan도 p149.115에서 재현되므로 probe
   request는 필요조건이 아니며, active/passive가 공유하는 off-channel 제어 경로를
   우선해야 한다.
3. B 장애 최종값은 전체 `dot11FailedCount=45643` 중 QoS index 7 실패가 `45619`,
   index 7 ACK failure가 `456190`, discard가 `45619`였다. 전체 실패의 약 99.95%가
   index 7에 집중됐고, 실패 frame 하나당 ACK failure 10회 뒤 discard되는 비율이다.
4. marker 기준 첫 불일치는 harness scan 2에서 보인다. `CFG80211_DONE`까지 기대한 6개
   중 성공 5개만 증가했고, 1초 뒤 index 7 실패 1과 ACK failure 10이 나타났다.
   이 구간에서 host packet count, host/MLAN pending, SDIO write port는 움직이지 않았고
   queue는 started였다. FW channel=36, physical Tx/Rx=`0x101/0x101`,
   `user_htstream=0x1111`, HT MCS7/1SS도 유지됐다.
5. 다음 scan completion에서는 index 7 실패가 5개 더 늘었다. 그 다음 scan의
   completion 후 1초 구간에는 index 7 실패가 609, ACK failure가 6090 증가했다.
   새 실패 frame과 discard가 계속 생성되므로 하나의 host descriptor가 무한
   재시도되는 서명과도 다르다.
6. beacon 수신과 association은 계속 유지된다. 따라서 channel 복귀와 일반 RX 생존은
   확인되지만, 짧은 ACK의 수신 경로까지 정상이라는 증거는 아니다.

#### 현재 해석이며 아직 증명되지 않은 부분

- off-channel excursion마다 2개인 index 7 frame은 F/W가 생성하는 scan power-management
  제어 frame, 예를 들어 진입 시 PM=1과 복귀 시 PM=0인 QoS Null일 가능성이 높다.
- 첫 실패가 scan 완료 뒤 예상된 여섯 번째 위치에 나타나므로 최종 home-channel 복귀의
  PM=0/wake frame이 먼저 실패했을 가능성이 높다.
- host packet/SDIO 활동 없이 counter가 움직이는 점은 일반 host data/null 경로보다
  F/W 내부 scan state machine을 지지한다.

Driver의 `mlan/mlan_sta_tx.c:wlan_send_null_packet()`도 host null packet에
`WMM_HIGHEST_PRIORITY`(7)를 부여하므로 index 7만으로 frame origin을 확정할 수는 없다.
다만 장애 window의 `ps_mode=0`, `ps_state=0`, `pps_uapsd=0`, `tx_lock=0`, host packet
count와 SDIO write-port 무변화는 일반 host null loop의 우선순위를 낮춘다.

현재 marker에는 실제 802.11 frame subtype, PM bit와 over-air ACK가 없다. 따라서 위
세 문장은 가설이며 `QoS Null`, PM=0/1 또는 정확한 TID를 확정하지 않는다. 또한
`ACKFailureCount`만으로 AP가 frame을 받지 못한 것인지, STA가 AP ACK를 수신하지 못한
것인지 구분할 수 없다.

## 7. 소스 분석 요구사항

### 7.1 실행 module과 소스 일치

1. module version/srcversion 생성 경로 확인
2. 현재 분석 소스가 실행 module과 일치하는지 확인
3. 불일치한다면 commit, patch 또는 build option 차이를 식별
4. 불일치 상태에서는 line-level 결론을 실행 바이너리 사실로 표현하지 않음

### 7.2 `antcfg` call graph

다음 전체 경로를 실제 파일과 함수명으로 작성한다.

```text
mlanutl/private ioctl 입력
→ moal ioctl/cfg 처리
→ mlan request 구조체
→ FW command 구성
→ command response 처리
→ driver 내부 상태 반영
```

추가 확인 항목:

- `0x303`과 `0x101`이 저장되는 구조체 및 필드
- Tx/Rx 값이 각각 어떻게 인코딩되는지
- 설정이 adapter/BSS/interface 중 어느 scope인지
- scan, channel restore, rate, power-save 또는 Tx descriptor 생성 경로에서 참조되는지
- command 성공 뒤 host와 FW 상태를 재검증하는 경로가 있는지

### 7.3 scan call graph

다음 경로를 실제 소스 기준으로 작성한다.

```text
cfg80211 scan 요청
→ moal scan entry
→ mlan scan request
→ FW scan command
→ scan response/event
→ operating-channel/BSS 복구
→ cfg80211 scan_done
```

다음 상태의 set/clear 위치를 모두 확인한다.

- scan processing/in-progress flag
- current/operating channel
- carrier와 netdev queue 상태
- WMM Tx queue pause/resume 상태
- power-save 및 wake 상태
- main-process/workqueue scheduling 상태
- pending command 및 pending Tx counter
- BSS/association 상태

특히 `cfg80211_scan_done()` 또는 동등 완료 보고가 실제 TX data-path 복구보다 먼저
실행될 수 있는지 확인한다.

### 7.4 TX call graph

다음 경로를 실제 파일과 함수명으로 추적한다.

```text
ndo_start_xmit/netdev 입력
→ moal enqueue
→ mlan/WMM queue
→ dequeue/main process
→ FW Tx command/descriptor
→ Tx completion
→ skb/counter 정리
```

확인 항목:

- scan 중 queue를 정지시키는 모든 경로
- scan 완료 후 queue를 깨우는 모든 경로
- `netif_queue_stopped()`와 driver 내부 queue 상태의 불일치 가능성
- pending counter 때문에 main process가 재실행되지 않는 경로
- Tx packet은 증가하지만 FW 전달이나 completion이 사라지는 경로
- 오류 반환 없이 skb가 보류·폐기·재큐잉될 수 있는 경로
- antenna/capability 값에 따라 달라지는 descriptor 또는 rate/path 처리

### 7.5 정적 결함 후보 검색

계측 결과를 반영한 우선순위는 다음과 같다.

1. F/W enhanced-scan의 off-channel 진입/복귀 PM frame 생성과 completion state machine
2. 마지막 home-channel 복귀 frame 실패 뒤 index 7 frame을 계속 재생성하는 retry loop
3. physical 1x1에서 scan 복귀 뒤 RF/BB Tx chain 또는 ACK Rx chain/context 복구 누락
4. scan 전후 Tx descriptor의 rate/NSS/BW/antenna context 불일치
5. p149.81 이후 p149.115까지 변경된 scan/PS/null-frame/channel-return 처리

다음 기존 host-side 후보도 확인하되, queue/pending/SDIO와 association/channel이 정상인
runtime evidence 때문에 위 F/W/MAC/PHY 경계보다 우선순위를 낮춘다.

다음 host-side 유형은 보조적으로 조사한다.

1. scan-complete에서 해제되지 않을 수 있는 flag/counter
2. queue stop과 wake가 대칭이 아닌 오류/경쟁 경로
3. scan-complete event와 Tx workqueue 사이 race
4. main process 또는 Tx worker reschedule 누락
5. operating-channel 또는 BSS 상태 복구 누락
6. power-save/wake 상태 불일치
7. FW 응답 성공을 data-path 복구보다 먼저 완료 처리하는 경로
8. `0x101` antenna capability에서만 달라지는 조건문 또는 FW command 필드
9. 1x1/2x2 path 전환과 scan 복귀 사이의 상태 불일치

각 후보는 다음 형식으로 기록한다.

```text
후보:
소스 파일/함수:
관련 상태 변수:
현재 증상과 일치하는 이유:
반대 증거 또는 불확실성:
신뢰도:
보드에서 판별할 최소 관측값:
```

## 8. Instrumentation 설계 요구사항

보드 시험 세션에서 적용할 수 있도록 정확한 파일·함수·변수 단위로 제안한다.
packet마다 무제한 출력하지 말고 상태 전환과 누적 counter 중심으로 설계한다.

### 8.1 필수 관측 시점

1. scan request 진입
2. FW scan command 전송 직전/직후
3. scan-complete response/event
4. channel/BSS 복구 직후
5. cfg80211 scan 완료 보고 직전
6. netdev/WMM queue stop과 wake
7. scan 완료 후 최초 Tx enqueue
8. scan 완료 후 최초 Tx dequeue/FW 전달
9. Tx completion
10. `tx failed` 급증 감지 시점

### 8.2 각 지점에서 검토할 값

실제 변수명은 소스 분석을 통해 확정한다.

- timestamp와 CPU/task context
- interface/BSS index
- scan flag와 scan request ID
- current/operating channel
- carrier 및 netdev queue stopped 여부
- WMM queue별 depth/paused 상태
- global/per-interface Tx pending counter
- command pending counter
- main-process/workqueue scheduled/running 상태
- PS/wake 상태
- association/BSS 상태
- antenna Tx/Rx 설정
- Tx enqueue/dequeue/completion 누적 counter
- 마지막 FW command/response/event ID

Instrumentation은 어느 경계까지 packet이 진행했는지 한 번의 장애 run으로 판별할 수 있어야
한다.

### 8.3 F/W 팀에 요청할 최소 trace

동일 scan ID에서 각 off-channel 진입과 home-channel 복귀를 연결할 수 있도록 다음을
한 timeline으로 제공한다.

1. F/W 내부 scan/PS state 전이와 channel 번호
2. F/W가 자체 생성한 각 Tx frame의 origin, 802.11 frame control/subtype, QoS
   array index 또는 TID/UP, PM bit, sequence number
3. 해당 frame의 Tx descriptor rate/MCS/NSS/BW, selected physical Tx chain과 RF/BB
   enable 상태
4. 각 attempt의 Tx status, retry count, ACK timeout/failure reason과 최종 discard reason
5. 복귀 직전/직후 selected physical Rx chain과 ACK detect/receive 상태
6. AP가 frame을 수신하지 못한 경우와 STA가 AP ACK를 놓친 경우를 구분할 수 있는
   over-air 또는 MAC/PHY status
7. 정상 p149.81과 실패 p149.115에서 같은 frame sequence를 byte/field 단위로 비교한 결과

특히 4채널 scan의 기대 index 7 frame 6개 중 어느 frame이 누락·실패하는지, 실패 뒤
초당 수백 개의 index 7 frame을 생성하는 state/timeout 조건이 무엇인지 확인한다.

F/W 변경 이력에서는 p149.81→p149.84→p149.88→p149.115 사이의 scan,
power-save protection, null/QoS frame, channel-return, RF chain 및 ACK 처리 변경 ID를
검토한다. 가능하면 p149.84와 p149.88을 동일 driver/조건으로 bisect할 수 있도록
공식 binary 또는 정확한 provenance도 확인한다.

## 9. 요청 산출물

다음 순서로 결과를 작성한다.

1. 실행 module과 분석 소스의 일치 여부
2. `antcfg` call graph
3. scan start/completion/restore call graph
4. TX enqueue/dequeue/completion call graph
5. 의심 지점 우선순위 목록
6. 각 후보를 구분하는 관측값
7. 최소 instrumentation 설계
8. 필요하면 원인 판별용 patch 제안
   - 아직 적용하거나 보드에서 실행하지 않음
   - 기능 수정이 아니라 instrumentation만 포함
9. 시험 세션에 요청할 다음 one-variable 실험과 예상 판별 결과
10. p149.81과 p149.115의 off-channel index 7 frame sequence 비교
11. 최초 실패 frame의 subtype/PM bit/rate/NSS/chain과 Tx-status/ACK failure reason
12. AP no-receive와 STA ACK-Rx miss의 판별 결과
13. p149.81→p149.84→p149.88→p149.115 관련 F/W 변경 ID와 regression 범위

소스에서 확인한 사실과 추론을 명확히 구분한다. F/W 소스가 없다면 F/W 내부 동작을
확정적으로 표현하지 말고 driver/FW 경계까지의 사실만 확정한다.

## 10. 기존 증거 위치

### 종합 시험 문서

```text
docs/iw_external_scan_driver_ab_2026-08-24.md
```

관련 절:

- §9: main/505 + p149.115 active/passive off-channel 기준선
- §11: 동일 main/505 driver의 p149.115/p149.81 F/W-only matched-pair
- §12: p149.81 제품 4채널/60초 soak
- §17: exact product module 입력
- §18: full FW replay
- §19: C-only
- §20: MCS-only 대 antcfg+rate
- §21: antcfg-only

### antcfg-only artifact

```text
artifacts/moal-fw-ar-internal-bisect-20260825/543p18-p149115/
```

주요 파일:

```text
summary.txt
ant-only-iw-run1/controller/replay-definition.txt
ant-only-iw-run1/controller/group-preflight.txt
ant-only-iw-run1/controller/module-and-replay-dmesg.txt
ant-only-iw-run1/iw-active-4ch-interval5-run1/result.txt
ant-only-iw-run1/iw-active-4ch-interval5-run1/progress.log
ant-only-iw-run1/iw-active-4ch-interval5-run1/kernel-journal.log
ant-only-iw-run1/iw-active-4ch-interval5-run1/wpa-journal.log
ant-only-iw-run1/post-run-live-validation.txt
```

Artifact root manifest:

```text
61 entries, 61/61 OK
SHA-256 36509f7cbef6ddcbc7c95f57a341407103471db7a464cce71e167a8819361837
```

### 계측 A/B artifact

```text
artifacts/moal-instrumented-antcfg-scan-ab-20260825/
```

주요 파일:

```text
runs/run1-extracted/scan-qa-instrumented-ant-1x1-run1/result.txt
runs/run1-extracted/scan-qa-instrumented-ant-1x1-run1/instrumented-markers.log
runs/run1-extracted/scan-qa-instrumented-ant-1x1-run1/live-wedge-core-raw.txt
runs/run2-extracted/scan-qa-instrumented-ant-rx-nss-run2/result.txt
runs/run2-extracted/scan-qa-instrumented-ant-rx-nss-run2/instrumented-markers.log
runs/run2-extracted/scan-qa-instrumented-ant-rx-nss-run2/final-control-core-raw.txt
runs/run2-extracted/scan-qa-instrumented-ant-rx-nss-run2-supplement/result.txt
```

독립 positive-control:

```text
artifacts/moal-ant-1x1-positive-control-20260825/live-wedge-core-raw.txt
```

### 분석 문서

```text
docs/iw_external_scan_driver_ab_2026-08-24.md
docs/ant_1x1_positive_control_2026-08-25.md
wlan-driver-v2/docs/antcfg-scan-wedge-instrumentation-handoff-2026-08-25.md
wlan-driver-v2/docs/antcfg-scan-wedge-instrumented-ab-results-2026-08-25.md
wlan-driver-v2/docs/main505-ported543-rf-hostcmd-scan-sequence-comparison-2026-08-25.md
wlan-driver-v2/docs/main-antcfg-customization-ported-coverage-audit-2026-08-25.md
```
