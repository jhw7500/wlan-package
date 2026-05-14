# HE 4기능 진단 스크립트 가이드 (9098 STA)

## 개요

NXP/Marvell 9098 STA 보드(IMX93 SDIO / IMX8MM PCIe)에서 WiFi6(HE) 4기능
**DL-MU-MIMO, UL-MU-MIMO, DL-OFDMA, UL-OFDMA**의 광고/협상/2×2 동작을
한 번에 진단하는 read-only 스크립트.

- **설치 경로**: `/usr/local/scripts/diag-9098-11ax.sh`, `/usr/local/scripts/verify-he-mu-features.sh`
- **권한**: `+x` (보드 root에서 직접 실행)
- **의존성**: `mlanutl` (보통 `/usr/local/bin/mlanutl`), `iw`, `awk`, `sed`
- **부수 효과 없음**: 모든 명령이 GET/cat 기반. 설정 변경 안 함.

---

## 두 스크립트 차이

| 스크립트 | 섹션 수 | 출력 분량 | 용도 |
|---|---|---|---|
| `diag-9098-11ax.sh` | 11 섹션 + VERDICT | ~150 줄 | **종합 진단** (드라이버/버스/FW/AP/통계 모두) |
| `verify-he-mu-features.sh` | 5 단계 + FINAL VERDICT | ~80 줄 | **4기능 × 5단계 검증 매트릭스** (사용자 요구사항 포맷) |

두 스크립트는 같은 mlanutl 명령을 공유. 일상 점검은 `verify-` 권장, 깊은 분석은 `diag-` 사용.

---

## 사용법

```bash
# 1. 종합 진단
/usr/local/scripts/diag-9098-11ax.sh mlan0

# 2. 4기능 × 5단계 검증
/usr/local/scripts/verify-he-mu-features.sh mlan0

# 3. VERDICT만 빠르게 보기
/usr/local/scripts/diag-9098-11ax.sh mlan0 | sed -n '/\[11\]/,/\[DONE\]/p'
/usr/local/scripts/verify-he-mu-features.sh mlan0 | sed -n '/FINAL VERDICT/,/\[DONE\]/p'
```

---

## VERDICT 해석

### `diag-9098-11ax.sh` 출력 예시

```
=== [11] VERDICT (최종 결론) ===
─── 9098 STA SDIO (mlan0) ─────────────────────────────────────
연결       : <BSSID> (<signal> dBm), 현재 BW <NN> MHz, PHY rate <NNN> MBit/s
HT stream  : 2x2

광고 능력 (HE Cap byte 디코드):
  [1] DL-MU-MIMO 수신     : OK  (SU-BFEE=1, SoundDim=2 streams, RX MCS NSS2=2)
  [2] UL-MU-MIMO 송신     : OK  (+HTC=1, FullBW-UL-MU=1, TX MCS NSS2=2)
  [3] DL-OFDMA 수신       : OK  (+HTC=1, Rx-HE-MU-PPDU=1)
  [4] UL-OFDMA 응답       : OK  (+HTC=1, OM-Ctrl=1)

── 종합 판정 ──
  ✅ STA(9098) 측 4기능 광고/협상 모두 정상. 2×2 NSS 동작 확인.
  ✅ 실측 BW=80MHz — 광고대로 동작.
  ℹ  9098 SDIO 칩 의도된 제약: BSR=0, Trig-BF-FB=000 (모두 0이면 정상)
  ▶  결론: STA 정상. throughput/MU 효율 cap의 원인은 환경(AP 정책·STA 수).
```

### 자동 판정 기호

| 기호 | 의미 |
|---|---|
| ✅ | PASS — 광고/협상/동작 모두 정상 |
| ⚠ | 광고는 OK이지만 환경(AP/BW 등)이 cap. STA 책임 아님 |
| ℹ | 정보 — 9098 칩의 의도된 제약 (변경 불가) |
| ❌ | FAIL — 광고 자체가 결핍. HE Cap conf 또는 FW build 점검 필요 |
| ▶ | 종합 결론 한 줄 |

### PASS/FAIL 자동 판정 로직

| 기능 | OK 조건 |
|---|---|
| DL-MU-MIMO | `phy[4].0=SU-BFEE=1` && `phy[5][0:2]+1≥2` && `rx_mcs_80 NSS2≤2` |
| UL-MU-MIMO | `mac[0].0=+HTC=1` && `phy[2].6=FullBW-UL-MU=1` && `tx_mcs_80 NSS2≤2` |
| DL-OFDMA | `mac[0].0=+HTC=1` && `phy[3].6=Rx-HE-MU-PPDU=1` |
| UL-OFDMA | `mac[0].0=+HTC=1` && `mac[3].1=OM-Ctrl=1` |

---

## 알려진 한계 (FAQ)

| 관찰 | 정상 여부 | 설명 |
|---|---|---|
| `BSR=0` (HE MAC Cap byte2 bit4) | ℹ 정상 | 9098 SDIO 칩 의도된 제약 — 변경 불가 |
| `Trig-BF-FB=000` (PHY Cap byte6 bit2/3/4) | ℹ 정상 | 동일 — 칩 한계 |
| `MU-BFER=0` (PHY Cap byte4 bit1) | ✅ STA에서 정상 | STA는 MU-BFER가 아닌 BFEE 입장 |
| `STBC=0` (PHY Cap byte2 bit2/3) | ℹ 정상 | 9098 SDIO build에서 FW가 클리어 |
| `160MHz=0` (PHY Cap byte0 bit3) | ℹ 정상 | 9098 SDIO 칩 80MHz max |
| BW 실측 < 80MHz | ⚠ AP 측 이슈 | AP가 BW80 광고 안 함 — `iw scan dump`로 AP의 HT/VHT Op IE 확인 |
| `LLDE` 통계 비어있음 | ✅ 정상 (STA) | LLDE는 AP/uAP 전용 기능. STA 모드에서 의도된 N/A |
| `tx_bf_cfg fail` | ✅ 정상 | peer MAC 등 인자 필수 — 단순 GET 미지원 |

---

## MU 직접 증명 한계

본 스크립트는 **STA-only 진단**이며, **MU/OFDMA의 실제 PPDU 발현은 직접 증명 불가**.
9098 SDIO mlan 드라이버는 procfs에 MU/Trigger PPDU 카운터를 노출하지 않음.

직접 증명이 필요한 경우:
- **외부 sniffer 노드** (Intel AX200/AX210 등 monitor mode + Wireshark)
- **AP 측 hostapd_cli / vendor stats** (D-Link DAP-X2850 등 폐쇄 AP는 불가)

본 스크립트는 다음까지 보장:
- ✅ STA가 4기능을 광고하는지 (HE Cap byte 디코드)
- ✅ 2×2 NSS PHY가 실제로 동작하는지 (histogram MCS bucket)
- ⚠ 실측 throughput cap의 환경 요인 추정 (BW/wired/AP)

---

## mgmt_hex_dump — 드라이버 자체 mgmt frame IE 캡처

진단 스크립트 외에 드라이버에 추가된 **mgmt frame 전체 IE byte-level 캡처** 기능.
`mlanutl 11axcfg` GET이 STA 자신의 HE Cap(전송 전 cache)을 보여준다면,
`mgmt_hex_dump`는 **AP가 실제로 advertise/응답하는 raw IE byte**를 받습니다.

### 개요

- **목적**: AssocResp/Probe Resp/Beacon 등 RX management frame의 모든 IE를 byte 단위로 보존
- **출력 위치**: `/proc/mwlan/adapter*/mgmt_dump` (256KB ring buffer, adapter별 분리)
- **포맷**: 헤더 라인 + 각 IE를 `IE[<tag>] len=<n> : <hex bytes>` 형식
- **타임스탬프**: 각 라인에 `[YYYY-MM-DD HH:MM:SS.mmm]` 자동 prefix

### 활성화 (2단계 게이트)

| 단계 | 파라미터 | 값 | 의미 |
|---|---|---|---|
| 1차 | `net_rx` | ≥2 (RX 로깅) 또는 `+4` (TX 로깅) | 어떤 frame을 mgmt_log/mgmt_dump로 보낼지 |
| 2차 | `mgmt_hex_dump` | 1 | IE bytes hex 추가 출력 |

**net_rx 비트 매핑**:
- `0`: legacy `netif_rx_ni`
- `1` (default): `netif_receive_skb`만 (mgmt 로깅 없음)
- `2`: 1 + roaming RX 로그 (Auth/Deauth/Assoc/Reassoc/Disassoc/Action)
- `3`: 1 + all RX 로그 (위 + Probe/Beacon)
- `+4`: TX 로그 (예: `6` = roaming RX + TX, `7` = all RX + TX)

**mgmt_hex_dump 효과**:
- `0` (default): mgmt_log에 메타데이터 한 줄만
- `1`: mgmt_log + mgmt_dump에 메타데이터 + IE bytes hex

### 설정 방법 — wifi_init_conf.json

per-adapter 토글. 모듈 reload 시점에 conf 파싱:

```jsonc
{
  "mlan0": {
    "net_rx": 7,                      // 모든 RX + TX 로깅
    "mgmt_hex_dump_enable": true      // IE bytes 포함
  },
  "mlan1": {
    "net_rx": 0,
    "mgmt_hex_dump_enable": false
  }
}
```

> ⚠ `net_rx`와 `mgmt_hex_dump`는 **module_param permission = 0** → sysfs 미노출, runtime 변경 불가.
> 변경하려면 conf 파일 수정 후 `wifi.sh restart` 또는 보드 reboot.

### 사용 예

```bash
# 1. ring buffer 초기화
echo > /proc/mwlan/adapter0/mgmt_dump

# 2. 트리거 (재접속 또는 자연 mgmt frame 대기)
wpa_cli -i mlan0 reassociate

# 3. 캡처
cat /proc/mwlan/adapter0/mgmt_dump | head -100

# 4. AP의 HE Cap byte 추출 (AssocResp의 IE[255] ext=0x23)
awk '/Assoc Response/,/^\[/' /proc/mwlan/adapter0/mgmt_dump \
  | grep 'IE\[255\] ext=0x23'
```

### 출력 예시

```
[2026-01-17 01:55:03.792] [mlan0] MGMT[RX] Assoc Response( 1) : SA=04:ba:d6:ec:0b:08 DA=90:2c:fb:00:f0:dc RSSI=-66 NF=-95 SNR=29 Retry=0 Seq=3
  IE[  1]         len=8 : 8c 12 98 24 b0 48 60 6c                                  # Supp Rates
  IE[ 45]         len=26: 8f 09 03 ff ff ff ff ...                                 # HT Capabilities
  IE[ 61]         len=22: 28 07 05 ...                                             # HT Operation
  IE[127]         len=10: 04 00 0f 02 00 00 00 40 00 40                            # Extended Cap
  IE[191]         len=12: 92 f9 8b 33 fa ff 00 00 aa ff 00 20                      # VHT Capabilities
  IE[192]         len=5 : 01 2a 00 fc ff                                           # VHT Operation
  IE[255] ext=0x23 len=34: 0d 01 08 9a 40 10 04 60 48 88 1f 43 81 1c 01 08 00 ...  # ★ HE Capabilities
  IE[255] ext=0x24 len=6 : f4 3f 00 06 fc ff                                       # ★ HE Operation
  IE[255] ext=0x26 len=13: 08 03 a4 ff 27 a4 ff 42 43 ff 62 32 ff                  # HE Spatial Reuse
  IE[255] ext=0x27 len=1 : 03                                                      # MU EDCA
```

### diag-9098-11ax.sh의 [3] HE Cap 디코드와 cross-check

mgmt_dump의 `IE[255] ext=0x23 len=34` byte sequence는 diag 스크립트의
[3] 섹션에서 출력되는 HE Cap 55-byte raw의 **MAC Cap (6) + PHY Cap (11) + MCS (4) + val (28)** 부분과
동일한 의미. AP 측을 보면 STA가 협상한 능력 검증 가능.

### diag-9098-11ax.sh [12] 섹션 — AP HE Cap 자동 분석

`mgmt_hex_dump`로 캡처된 mgmt_dump를 **diag 스크립트가 자동 파싱**해
AP 측 HE Cap byte를 4기능 광고 평가 + STA-AP cross-check까지 수행:

```bash
ssh root@<board> /usr/local/scripts/diag-9098-11ax.sh mlan0 | sed -n '/\[12\]/,/\[DONE\]/p'
```

출력 예시:
```
=== [12] AP HE Cap (mgmt_dump의 AssocResp에서 추출, byte-level) ===
  source: [2026-01-16 17:54:00.178] IE[255]
  raw(34 bytes): 0d 01 08 9a 40 10 04 60 48 88 1f 43 81 1c 01 08 00 aa ff aa ff 7b ...

  ── AP 광고 능력 ──
    mac[0]=0x0d  +HTC=1
    mac[3]=0x9a  OM-Ctrl=1
    phy[0]=0x04  40/80_5G=1  160=0
    phy[2]=0x48  FullBW-UL-MU=1
    phy[3]=0x88  Rx-HE-MU-PPDU=0  SU-BFER=1
    phy[4]=0x1f  SU-BFEE=1  **MU-BFER=1**
    phy[5]=0x43  SoundDim<=80=3 (4 streams)
    rx_mcs_80 NSS2=2  tx_mcs_80 NSS2=2

  ── AP 측 4기능 광고 평가 (AP→STA 방향) ──
    [DL-MU-MIMO 송신] OK
    [UL-MU-MIMO 수신] OK
    [DL-OFDMA 송신  ] FAIL (AP Rx-HE-MU-PPDU=0)
    [UL-OFDMA 수신  ] OK

  ── STA-AP cross-check ──
    DL-MU-MIMO : STA-BFEE=1 + AP-MU-BFER=1 → 양쪽 OK
    UL-MU-MIMO : STA-FullBW=1 + AP-FullBW=1 → 양쪽 OK
```

#### 자동 평가 매트릭스

| AP 측 광고 | 판정 비트 | OK 의미 |
|---|---|---|
| DL-MU-MIMO 송신 | `phy[4].1 = MU-BFER` | AP가 STA에 빔포밍 동시 송신 가능 |
| UL-MU-MIMO 수신 | `phy[2].6 = FullBW-UL-MU` | AP가 다중 STA UL trigger 가능 |
| DL-OFDMA 송신 | `phy[3].6 = Rx-HE-MU-PPDU` | (※ 본 비트는 AP가 자신을 STA로 행세할 때만 의미. AP role에서는 default 0이 정상 가능) |
| UL-OFDMA 수신 | `mac[0].0 = +HTC HE` | AP가 Trigger Frame 처리 |

#### STA-AP cross-check 의미

| cross-check 결과 | 의미 |
|---|---|
| **양쪽 OK** | MU/UL-MU 그룹 형성 **이론상 가능** (실 발현은 ≥2 STA + AP 스케줄러) |
| **한쪽 결핍** | 그룹 형성 불가 — 결핍 측 점검 (펌웨어/conf) |

#### 데이터 출처

`/proc/mwlan/adapter0/mgmt_dump`의 가장 최근 `IE[255] ext=0x23` 라인 자동 grep.
여러 AP의 mgmt frame이 섞여 있어도 마지막 = **현재 연결된 AP의 AssocResp**.

#### prerequisites

- `wifi_init_conf.json`에서 `mlan0.net_rx >= 2` + `mgmt_hex_dump_enable = true` 활성
- 최근 1회 이상 assoc/reassoc 발생 (Beacon에는 HE Cap 없음 — AssocResp 필수)
- 없으면 `wpa_cli -i mlan0 reassociate` 트리거 후 재실행

### 알려진 한계

| 항목 | 영향 |
|---|---|
| **TX AssocReq의 HE Cap IE 미포함** | HostMlme 모드에서는 wpa_supplicant + FW가 HE Cap을 추가 → mgmt_dump의 TX AssocReq에 안 보임. STA 자체 HE Cap은 `mlanutl 11axcfg`로 확인 |
| **RX IE에 6 byte vendor prefix** | NXP RX path가 mgmt body 앞에 6 byte 메타를 추가. 드라이버 코드가 이미 보정 (모든 subtype `_ie_off`에 +6 적용) — 사용자 대응 불필요 |
| **Beacon은 100ms 마다 + 다중 AP** | net_rx=3으로 켜면 ring buffer 빠르게 wrap. roaming만 필요하면 net_rx=2 권장 |
| **mgmt_log_dump 옛 이름** | 초기 버전에서 사용. 현재 이름은 `mgmt_hex_dump`. modinfo로 정확한 이름 확인 |

### 트러블슈팅

| 증상 | 점검 |
|---|---|
| `/proc/mwlan/adapter*/mgmt_dump` 없음 | 모듈이 새 빌드인지 확인: `modinfo moal \| grep mgmt_hex_dump` |
| mgmt_dump 비어있음 (0 byte) | `dmesg \| grep mgmt_hex_dump` → 값 확인. 부팅 시 0이면 conf 미반영. `wifi_init_conf.json` 점검 |
| IE 디코드가 어긋남 (tag 17, 8 같은 비표준) | NXP +6 vendor prefix 보정 미적용 버전. 최신 ko로 deploy 필요 |
| Hex 잔존 (다른 라인 데이터가 빈 IE에 보임) | 초기 버전 hex[] 초기화 버그. 최신 ko로 fix 됨 |
| sysfs로 토글 안 됨 | 의도된 동작 (perm=0). conf + reload 필요 |

### 코드 위치 (참고)

| 항목 | 파일 |
|---|---|
| param 정의 | `mlinux/moal_init.c:92, 891-898, 1849-1852, 3170-3173` |
| handle 필드 | `mlinux/moal_main.h:2658, 2870` |
| ring buffer / IE 파서 | `mlinux/moal_proc.c:53-167` |
| proc entry | `mlinux/moal_proc.c:1740-1796` |
| RX path IE dump | `mlinux/moal_shim.c:4520-` (subtype별 `_ie_off`, +6 vendor prefix) |
| TX path IE dump | `mlinux/moal_sta_cfg80211.c:2829, 3410, 5275, 6155, 6214`<br>`mlinux/moal_cfg80211.c:3270` |
| 옛 (legacy join) TX HE Cap dmesg | `mlan/mlan_11ax.c:519` (`DBG_HEXDUMP(MMSG, ...)` — HostMlme에서는 inactive) |

---

## 참고

- 코드 분석 기반 비트 매핑: `mlan/mlan_fw.h:1031-1060`, `mlan/mlan_ieee.h:1396-1500`, `mlan/mlan_11ax.c:533-862`
- HE Cap raw 형식: 55 bytes (band 1B + TLV 2B + len 2B + ext_id 1B + MAC 6B + PHY 11B + MCS 4B + val 28B)
- IEEE 802.11ax-2021 표 9-323/9-324 (HE MAC/PHY Capabilities Element)
- 워크스테이션용 듀얼 STA 검증: `wlan-package/scripts/dual-sta-mu-test.sh` (보드에는 배포 안 함)
- mgmt_hex_dump 신규 추가: 2026-01-17 (`mlinux/moal_proc.c`, `moal_main.h`, `moal_init.c`, `moal_shim.c`, `moal_sta_cfg80211.c`, `moal_cfg80211.c`, `mlan/mlan_11ax.c`)
