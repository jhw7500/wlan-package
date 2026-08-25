# SD9098 `antcfg 0x303 0x101` SET/GET 호환성 행렬

## 1. 목적과 범위

작성일은 2026-08-25 KST다. 다음 두 질문을 RF scan 없이 분리했다.

1. main/505 driver를 고정했을 때 p149.81과 p149.115 F/W가 비대칭 antenna
   설정을 다르게 처리하는가?
2. p149.115 F/W를 고정했을 때 main/505와 ported/543 사이에 유효 상태 또는
   조회 인터페이스 차이가 있는가?

각 행은 `wifi_init.service`가 disabled/inactive이고 WLAN module이 없는 새 boot에서
시작했다. supplicant, roam, bgscan, capture, logger, checker, event, bridge와 FW watch를
모두 정지·runtime mask했다. `mlan0`을 올리지 않고 다음 SET 한 번과 즉시 GET 한 번만
실행했다.

```text
mlanutl mlan0 antcfg 0x303 0x101
mlanutl mlan0 antcfg
```

모든 행에서 `mlan0`은 down, wpa/`iw`/`wpa_cli` process 0, scan command 0,
association marker 0, FW/kernel fault marker 0이었다.

## 2. 고정 입력

| 입력 | SHA-256 |
|---|---|
| main/505 `mlan_imx93.ko` | `ef6a8bd5fcc158d918e18da75638fb794d48931a1056e78e20284a40c3240eab` |
| main/505 `moal_imx93.ko` | `c71d25e3fc9058f6d83ca10ffd95eb311705bc64f94691abe746dc6206b1f998` |
| ported/543 `mlan_imx93.ko` | `c351a0d63f75d53f99ca0b74aba3911d79e9aaf1f4e269dac68cba9b06e4c46a` |
| ported/543 `moal_imx93.ko` | `87b9d0dc5b86c4a40560050f4e9c5a2c8662acc97bea6b516f0f094fcfc9b6a0` |
| p149.81 F/W | `4716066f0325d0f7d21fbb45f037c1f9ca642011f206ccdfd6fa2371d6c81077` |
| p149.115 F/W | `7c3ef6e12d3cfc9bd638d1571ccf6ddd2e96e0ed179ec70664ccb1df0ba29e57` |
| 제품 main-style `mlanutl` | `7a703d7e4454d8ec61bbbfbf9ee8e0eef66b4560298d56b050d8d2799eea936f` |
| ported/543 matching `mlanutl` | `86ea019edd766b2c426026a4ffd86538af1f6ce85060e68cb02bbd8cc81d6f95` |

두 driver 모두 다음 module argument를 동일하게 사용했다.

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

## 3. 결과

| 조합 | boot ID | 표준 antenna GET | host NSS intent | 판정 |
|---|---|---|---|---|
| main/505 + p149.81 | `666523e4-dd3b-4ac1-9773-a1493de92e2c` | Tx `0x303`, Rx `0x303` | `0x2121` = 2G/5G Rx 1, Tx 2 | SET/GET rc 0 |
| main/505 + p149.115 | `0e690d25-e621-4bcd-bc19-9079dfcf406a` | Tx `0x303`, Rx `0x303` | `0x2121` = 2G/5G Rx 1, Tx 2 | SET/GET rc 0 |
| ported/543 + p149.115, 제품 utility | `e607620b-b958-4250-8b41-bf5435325c51` | Tx `0x303`, Rx `0x303` | 표시 없음 | utility/driver 조회 ABI 불일치 |
| ported/543 + p149.115, matching utility | `ef475066-56f7-460c-9979-f322dd323410` | Tx `0x303`, Rx `0x303` | `0x2121` = 2G/5G Rx 1, Tx 2 | 교정된 유효 행 |

유효한 세 행의 원문은 모두 다음과 같다.

```text
Mode of Tx path is 0x303
Mode of Rx path is 0x303
NSS limit (antcfg): 2G rx=1 tx=2, 5G rx=1 tx=2  [user_htstream=0x2121]
```

따라서 두 상태를 하나로 취급하면 안 된다.

- `Mode of Tx/Rx path`: F/W가 반환한 physical antenna mode. 세 조합 모두 2x2다.
- `user_htstream`: host가 association capability에 반영하는 NSS intent. 세 조합 모두
  2G/5G에서 Rx 1SS, Tx 2SS다.

## 4. ABI 차이의 소스 근거

main tree는
`/home/jhw/ai/opencode/projects/wlan-driver-v3`의 commit
`1ba9fd42b40c8f76b207ec391eec77c171cdcc12`다. 다음 구현은 기존 `antcfg` GET
응답을 16바이트로 확장해 Tx, Rx, `user_htstream`, reserved word를 함께 반환한다.

- `/home/jhw/ai/opencode/projects/wlan-driver-v3/mlinux/moal_eth_ioctl.c:14034-14055`
- `/home/jhw/ai/opencode/projects/wlan-driver-v3/mapp/mlanutl/mlanutl.c:21029-21038`

ported tree는
`/home/jhw/ai/opencode/projects/wlan-driver-v2`의 clean commit
`26400d66cc56e9af0096273b5d25d31d3e001fa6`다. 이 tree는 기존 `antcfg` 응답 ABI를
유지하고 `user_htstream`을 별도 `antcfgnss` private command로 반환한다.

- `/home/jhw/ai/opencode/projects/wlan-driver-v2/mlinux/moal_eth_ioctl.c:15488-15544`
- `/home/jhw/ai/opencode/projects/wlan-driver-v2/mlinux/moal_eth_ioctl.c:23697-23703`
- `/home/jhw/ai/opencode/projects/wlan-driver-v2/mapp/mlanutl/mlanutl.c:25509-25545`

ported driver는 SET 응답의 Tx/Rx action을 독립 확인하고 각각의 bit count로
`user_htstream`을 갱신한다.

- `/home/jhw/ai/opencode/projects/wlan-driver-v2/mlan/mlan_cmdevt.c:8636-8665`
- `/home/jhw/ai/opencode/projects/wlan-driver-v2/mlan/mlan_cmdevt.c:8690-8698`

제품 `/usr/local/bin/mlanutl`은 `antcfg` 문자열만 가지며 `antcfgnss`를 포함하지 않는다.
따라서 ported/543에서 보조 행이 사라진 최초 실행은 상태 소실 증거가 아니라 조회 ABI
불일치였다. matching utility로 다시 실행하자 동일한 `0x2121`이 확인됐다.

## 5. 질문별 결론

### 5.1 F/W 세대 정책인가?

아니다. main/505를 byte-identical하게 고정한 p149.81과 p149.115가 physical mode와
host NSS intent를 모두 동일하게 반환했다. 이 bounded SET/GET 결과에서 F/W 세대에 따른
비대칭 설정 정책 차이는 관찰되지 않았다.

### 5.2 505와 543의 driver/FW 유효 동작 차이인가?

matching utility 기준 유효 상태는 동일하다. 확인된 차이는 private ioctl의 **조회 ABI**다.
main/505는 확장 `antcfg` 응답, ported/543은 기존 `antcfg`와 별도 `antcfgnss`를 사용한다.

### 5.3 `0x303/0x101` 요청은 무시됐는가?

완전히 무시된 것은 아니다. F/W physical antenna mode는 `0x303/0x303`으로 남지만 host
NSS intent는 요청대로 `0x2121`이 된다. 따라서 “2Tx/1Rx physical path가 만들어졌다”와
“host가 Rx 1SS/Tx 2SS capability를 광고한다”를 구분해야 한다.

## 6. 이전 방향 분리 판정에 미치는 영향

앞선 controller는 표준 antenna GET의 Rx가 `0x101`인지 여부만 검사했으므로
`0x303/0x303`을 mismatch로 보고 scan 전에 중단했다. 이 중단 자체는 안전했지만,
“2Tx/1Rx 상태를 만들 수 없다”는 결론은 너무 강했다. 정확한 표현은 다음과 같다.

- physical antenna mode의 Rx는 2-path로 반환된다.
- host advertised Rx NSS는 1SS로 제한된다.
- 이 host-side 2Tx/1Rx 상태로 scan gate를 실행하지 않았으므로 Rx NSS 제한 단독의
  wedge 영향은 아직 미검증이다.

향후 controller는 physical antenna mode와 `user_htstream`을 별도 필드로 검증해야 한다.
ported/543에서는 matching `antcfgnss` 조회를 사용해야 한다.

## 7. 제품 복구

최종 복구 boot ID는 `024e324c-5bb8-4869-9d17-ed8b3dd6ff5b`다.

- driver/F/W: ported/543 + p149.115, 원래 SHA-256 일치
- 제품 `antcfg`: Tx/Rx `0x101/0x101`
- `wifi_init`, FW watch, supplicant, `wifi_roam`: enabled/active
- `wifi_bgscan`: enabled/inactive, 제품 정책과 동일
- association: `COMPLETED`, 5180 MHz
- station `tx failed=0`
- gateway ping: cleanup 전 10/10, cleanup 직후 5/5
- FW/kernel fault marker: 0
- 원격 시험 controller, run, tarball, 임시 backup: 모두 삭제 확인

복구 약 8분 뒤의 추가 live gate에서는 4/5, 이어진 bounded 재검증은 19/20과 29/30으로
각각 한 패킷 손실이 있었다. 모든 측정 전후에 같은 BSSID와 `COMPLETED`를 유지했고
`tx failed=0`, FW/kernel fault 0이었다. 따라서 기존 stale-`COMPLETED` + TX failure
wedge 서명은 아니지만, **최종 무손실 ping gate는 반복해서 만족하지 못했다.** 이 손실의
원인은 이번 SET/GET 행렬에서 분리하지 않았으며 제품 설정 복구와 별도 관측으로 남긴다.

## 8. 증거 위치

원시 증거 root는
`/home/jhw/ai/opencode/projects/wlan-package/.worktrees/wpa-roam-scan-policy/artifacts/moal-antcfg-compat-matrix-20260825`다.

주요 파일은 다음과 같다.

- `/home/jhw/ai/opencode/projects/wlan-package/.worktrees/wpa-roam-scan-policy/artifacts/moal-antcfg-compat-matrix-20260825/runs/main505-p14981/commands/011-antcfg-get.txt`
- `/home/jhw/ai/opencode/projects/wlan-package/.worktrees/wpa-roam-scan-policy/artifacts/moal-antcfg-compat-matrix-20260825/runs/main505-p149115/commands/011-antcfg-get.txt`
- `/home/jhw/ai/opencode/projects/wlan-package/.worktrees/wpa-roam-scan-policy/artifacts/moal-antcfg-compat-matrix-20260825/runs/ported543-p149115/commands/011-antcfg-get.txt`
- `/home/jhw/ai/opencode/projects/wlan-package/.worktrees/wpa-roam-scan-policy/artifacts/moal-antcfg-compat-matrix-20260825/runs/ported543-p149115-matching-util/commands/011-antcfg-get.txt`
- `/home/jhw/ai/opencode/projects/wlan-package/.worktrees/wpa-roam-scan-policy/artifacts/moal-antcfg-compat-matrix-20260825/analysis/source-abi-evidence.txt`
- `/home/jhw/ai/opencode/projects/wlan-package/.worktrees/wpa-roam-scan-policy/artifacts/moal-antcfg-compat-matrix-20260825/recovery/product-postcleanup-state.txt`
