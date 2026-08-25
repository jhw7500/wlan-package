# Ant 1x1 positive-control 재현 결과 (2026-08-25)

## 1. 목적

host Rx NSS-only 조건이 60회 `iw` scan을 통과한 직후, 동일한 controller와 환경에서
physical/host Tx/Rx를 모두 1SS로 만든 positive control이 여전히 장애를 재현하는지
확인했다. 이 시험이 재현돼야 앞선 Rx-only 음성 결과를 유효한 방향 분리로 해석할 수 있다.

## 2. 고정 환경

| 항목 | 값 |
|---|---|
| driver | ported 543.p18 |
| `mlan_imx93.ko` SHA-256 | `c351a0d63f75d53f99ca0b74aba3911d79e9aaf1f4e269dac68cba9b06e4c46a` |
| `moal_imx93.ko` SHA-256 | `87b9d0dc5b86c4a40560050f4e9c5a2c8662acc97bea6b516f0f094fcfc9b6a0` |
| F/W | `17.92.1.p149.115` |
| F/W SHA-256 | `7c3ef6e12d3cfc9bd638d1571ccf6ddd2e96e0ed179ec70664ccb1df0ba29e57` |
| matching `mlanutl` SHA-256 | `86ea019edd766b2c426026a4ffd86538af1f6ce85060e68cb02bbd8cc81d6f95` |
| module arguments | 제품과 동일한 10개 argument |
| A/B, rate, MCS replay | 모두 미적용 |
| isolation | `wpa_supplicant@mlan0.service`만 active |
| clean test boot ID | `a375f095-fedf-4757-bec8-075eb029ba36` |

driver 분석 세션에서 source tree와 build output이 변경되는 중이었으므로, 이번 시험은
Rx-only 시험에 사용했던 matching utility binary를 그대로 동결해 재사용했다. driver,
F/W, module argument, AP/BSSID, supplicant config, scan harness도 동일하게 유지했다.

## 3. 설정과 기준 상태

```text
request: antcfg 0x101 0x101
Mode of Tx path is 0x101
Mode of Rx path is 0x101
NSS limit (antcfg): 2G rx=1 tx=1, 5G rx=1 tx=1  [user_htstream=0x1111]
```

controller는 요청값, physical GET, host NSS intent를 모두 독립 검증했다. scan 직전에는
동일 BSSID와 5180 MHz에서 `COMPLETED`, `tx failed=0`, gateway ping 5/5였고,
FW/kernel fault marker는 없었다.

## 4. 재현 결과

```text
iw mlan0 scan freq 5180 5200 5220 5240 ssid <시험 SSID>
```

| 항목 | 결과 |
|---|---|
| 판정 | `REPRODUCED` |
| 정상 구간 | scan 1~30, `tx failed=0` |
| 전조 | scan 31, `tx failed=1` |
| 장애 onset | scan 32 / 177초 |
| onset `tx failed` | 2,810 |
| 최종 ping 전/후 `tx failed` | 2,833 / 5,482 |
| 초기/최종 ping | 3/3 / 0/3 |
| 초기/최종 WPA state | `COMPLETED` / `COMPLETED` |
| 초기/최종 BSSID 및 주파수 | 동일 BSSID / 5180 MHz |
| external request/result | 32 / 32 |
| own scan | 0 |
| disconnect/auth error | 0 |
| FW reset/dump/CMD timeout | 0 |
| scan 시간 | 최소 274 ms, 최대 317 ms, 평균 279.53 ms |

앞선 positive run은 scan 5에서 발생했고 이번에는 scan 32에서 발생했다. 따라서 장애
서명은 반복되지만 **발생 횟수는 비결정적**이다. 짧은 5~10회 통과만으로 미재현을
판정하면 안 되며, 현재 60회 gate와 조기 `tx failed` 중단이 필요하다.

## 5. 장애 상태의 host/FW 관측

장애 뒤에도 `/proc/mwlan/adapter0/mlan0/info`와
`/proc/mwlan/adapter0/mlan0/debug`는 다음을 보였다.

```text
carrier on
tx queue 0..3: started
Tx pending: 0
Tx stop queue cnt: 0
tx_pkts_queued=0
tx_pause=0
scan_processing=0
num_cmd_timeout=0
num_tx_h2c_fail=0
num_tx_timeout=0
tx_pending=0
wmm_tx_pending[0..3]=0
```

qdisc backlog도 0이고 netdev drop/error가 증가하지 않았다. 즉 host netdev/WMM queue가
scan 뒤 정지하거나 packet을 계속 쌓는 서명은 관찰되지 않았다.

반면 같은 fresh boot의 FW/MAC 통계는 장애 뒤 다음처럼 증가했다.

```text
dot11FailedCount = 68654
dot11ACKFailureCount = 686553
dot11QosFailedCount = ... 68638
dot11QosACKFailureCount = ... 686390
beaconReceivedCount = 2821
beaconMissedCount = 0
```

association과 RX beacon 수신은 유지되는 동안 TX ACK failure만 폭증했다. 따라서 현재
증거는 단순한 **host queue wake 누락**보다는 그 아래의 FW/MAC/RF Tx 복귀 상태 또는
peer ACK를 받을 수 없는 Tx parameter 상태를 가리킨다. 이 통계만으로 실제 RF energy
송출 여부까지 확정할 수는 없으므로, 그 경계는 driver/F/W instrumentation 또는
monitor capture로 확인해야 한다.

## 6. Rx-only 결과와 합친 결론

| 상태 | scan 결과 |
|---|---|
| physical Tx1/Rx2 | 60/60 `NOT_REPRODUCED` |
| physical Tx2/Rx2 + host Tx2/Rx1 | 60/60 `NOT_REPRODUCED` |
| physical Tx1/Rx1 + host Tx1/Rx1 | scan 32 `REPRODUCED` |

따라서 Tx 제한 단독과 host Rx NSS 제한 단독은 각각 충분조건이 아니다. 재현 상태에만
남는 차이는 physical Rx 1-path 또는 physical Tx/Rx 동시 1-path 상호작용이다.
Rx-only physical 1-path는 F/W가 `0x303`으로 정상화하므로 현재 user-space command로는
더 분리할 수 없다.

다음 단계는 working Rx-only와 failing 1x1을 driver instrumentation build에서 비교하는
것이다. 특히 scan 31의 마지막 정상 상태와 scan 32 직후에 다음을 수집해야 한다.

- scan channel 복귀 뒤 physical antenna/RF-chain/channel context
- 첫 실패 packet의 Tx descriptor, selected rate/NSS와 FW Tx-status reason
- ACK timeout/retry reason과 RF/BB Tx enable 상태
- association capability와 runtime Tx-chain 상태의 불일치
- scan complete event, operating-channel restore, Tx resume의 순서

host queue는 이미 started/pending 0이므로 `netif_wake_queue()` 여부만 확인하는 분석은
충분하지 않다.

## 7. 증거와 제품 복구

전체 회수본:

`/home/jhw/ai/opencode/projects/wlan-package/.worktrees/wpa-roam-scan-policy/artifacts/moal-ant-1x1-positive-control-20260825/runs/scan-qa-ant-1x1-positive-20260825.tar.gz`

SHA-256:

`3f82bae94598002651f4d894248121ae8e9ce9b7b0c81db1d0ce1961977c0aa3`

post-exit manifest 93개를 로컬에서 모두 재검증했다. 장애 상태 raw 진단은 다음 파일이다.

`/home/jhw/ai/opencode/projects/wlan-package/.worktrees/wpa-roam-scan-policy/artifacts/moal-ant-1x1-positive-control-20260825/live-wedge-core-raw.txt`

제품 복구 boot ID는 `1b1a7958-4d40-4da5-a7d9-5841cb1dcc97`이다. 제품 hash와
`antcfg 0x101/0x101`, 서비스 상태를 복구했고 `tx failed=0`, ping 10/10 및 정리 후
5/5, FW/kernel fault 0을 확인했다. 원격 시험 directory는 모두 삭제했다.
