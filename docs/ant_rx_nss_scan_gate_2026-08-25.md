# Ant Rx NSS 단독 `iw` scan gate 결과 (2026-08-25)

## 1. 질문과 판정 기준

543.p18 driver와 17.92.1.p149.115 F/W에서 `antcfg 0x303 0x101` 요청은
physical antenna mode를 Tx/Rx `0x303/0x303`으로 유지하면서 host
`user_htstream=0x2121`을 만든다. 이는 2G/5G association capability의
Tx 2SS/Rx 1SS intent다.

이번 시험은 이 **host Rx NSS 1SS 제한만으로** 외부 `iw` scan 뒤 TX data-path
wedge가 발생하는지 확인했다. 다음 중 하나가 발생하면 즉시 중단하도록 했다.

- `tx failed`가 기준값보다 1,000 이상 증가
- scan command 실패
- 최대 60회 완료

최종 `REPRODUCED` 판정에는 ping 실패, `tx failed` 증가, 계속
`wpa_state=COMPLETED`, 동일 BSSID가 함께 필요하다.

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
| clean test boot ID | `efa52942-da37-4adf-9d20-324dfc56348a` |

module argument는 다음으로 동결했다.

```text
mod_para=cts/wifi_mod_para.conf
tx_work=0
bridge_mode=1
bridge_debug=0
bridge_wlan_idx=0
bridge_keepalive_ms=1
bridge_keepalive_idle_ms=20
bridge_local_hairpin=0
wq_sched_policy=1
wq_sched_prio=45
```

clean boot에서 `wifi_init.service`가 disabled/inactive이고 WLAN module,
`mlan0`, scan process가 모두 없는 것을 확인한 뒤 controller를 실행했다.

## 3. 설정 검증

요청과 SET 직후 matching utility GET은 다음과 같았다.

```text
request: antcfg 0x303 0x101
Mode of Tx path is 0x303
Mode of Rx path is 0x303
NSS limit (antcfg): 2G rx=1 tx=2, 5G rx=1 tx=2  [user_htstream=0x2121]
```

controller는 이번 시험부터 다음 세 계층을 독립 검증한다.

- 요청값: `ANT_TX=0x303`, `ANT_RX=0x101`
- 예상 physical GET: `EXPECTED_ANT_TX=0x303`, `EXPECTED_ANT_RX=0x303`
- 예상 host NSS intent: `EXPECTED_USER_HTSTREAM=0x2121`

연결 기준 상태는 동일 BSSID, 5180 MHz, `COMPLETED`, `tx failed=0`, gateway
ping 5/5였다.

## 4. scan 결과

실행 command는 다음으로 고정했다.

```text
iw mlan0 scan freq 5180 5200 5220 5240 ssid <시험 SSID>
```

| 항목 | 결과 |
|---|---|
| 판정 | `NOT_REPRODUCED` |
| scan | 60/60 성공 |
| 간격 | scan 완료 뒤 5초 |
| 총 경과 | 328초 |
| scan 시간 | 최소 272 ms, 최대 320 ms, 평균 275.55 ms |
| 초기/최종 `tx failed` | 0 / 0 |
| 초기/최종 WPA state | `COMPLETED` / `COMPLETED` |
| 초기/최종 BSSID | 동일 |
| 초기/최종 주파수 | 5180 / 5180 MHz |
| 최종 ping | 3/3, loss 0% |
| external request/result | 60 / 60 |
| own scan | 0 |
| disconnect/auth error | 0 |
| FW/kernel fault marker | 0 |

## 5. 결론

이 조건에서는 **host advertised Rx NSS 1SS 제한 단독으로 wedge가 발생하지 않았다.**
앞선 방향 분리 결과와 함께 보면 다음까지 말할 수 있다.

- physical Tx 1-path / Rx 2-path 제한은 60/60에서 재현되지 않았다.
- physical Tx/Rx 1-path 조합은 앞선 시험에서 scan 5에 재현됐다.
- physical Tx/Rx 2-path + host Tx 2SS/Rx 1SS 조합은 이번 60/60에서
  재현되지 않았다.

따라서 **Tx 제한 단독과 host Rx NSS 제한 단독은 각각 충분조건이 아니다.** 남은
차이는 physical Rx 1-path와 Tx/Rx 동시 1-path 상호작용이다. F/W가 Rx-only 요청을
physical `0x303`으로 정상화하므로, 현재 user-space SET/GET만으로 physical Rx 1-path
단독을 구성해 두 후보를 더 분리할 수는 없다.

후속 corrected positive control은 Tx/Rx `0x101/0x101`에서 scan 32에 장애를 다시
재현했다. 상세 결과는
`/home/jhw/ai/opencode/projects/wlan-package/.worktrees/wpa-roam-scan-policy/docs/ant_1x1_positive_control_2026-08-25.md`에
기록했다. 다음 유효 단계는 driver/F/W 계측으로 working Rx-only와 failing 1x1의
scan 복귀 상태를 비교하는 것이다.

## 6. 증거와 복구

전체 회수본:

`/home/jhw/ai/opencode/projects/wlan-package/.worktrees/wpa-roam-scan-policy/artifacts/moal-ant-rx-nss-scan-20260825/runs/scan-qa-ant-rx-nss-20260825.tar.gz`

tarball SHA-256:

`4adf3a65ef14473dc6f4fd4fdb416dc257a11df3059279b71ab57be835dfe387`

post-exit manifest 148개 파일을 로컬에서 모두 재검증했다. 실행 당시 harness의
내부 manifest는 아직 열린 `harness.log`를 포함해 1개 mismatch가 발생했다. 결과
파일 손상이 아니라 manifest 생성 시점 문제이며, 회수 전 별도 post-run manifest로
전체 파일을 검증했다. 재사용 harness는 live envelope를 payload manifest에서
제외하도록 수정했다.

제품 복구 boot ID는 `4ae7d804-1f0f-4b67-92ba-76dd21b2caab`이다. driver/F/W와
제품 설정 hash, `antcfg 0x101/0x101`, 서비스 상태를 복구했다. 첫 10회 ping은
7/10이었지만 같은 BSSID와 `COMPLETED`, `tx failed=0`, fault 0을 유지했다. 이어진
20/20과 cleanup 후 5/5는 loss 0%였으므로 기존 wedge 서명은 아니었다. 원격 시험
directory는 모두 삭제했다.
