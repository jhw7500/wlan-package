# SD9098 rate_adapt_cfg 70/90 -> 40/65 조사 결과 (2026-09-12)

2026-09-13 추가 확인: **60초 송신 중 RATE_ADAPT_CFG GET을 한 번도 보내지 않은 두 회차에서도 종료 후 첫 응답이 40/65였다.** 고빈도 조회가 필수 조건이라는 가설은 배제됐다. 상세 결과와 최신 복구 상태는 10절에 있다.

이번 조사는 **FW가 반환하는 Low/High 임계값의 반복 변화를 확인하고, 내부 원인은 미확정 상태로 마무리**했다. 제품 코드나 FW에 원인 수정은 적용하지 않았다. 마지막 타겟 조회에서 70/90/100ms와 운영 설정 복구 상태를 확인했다. 이는 복구 시점의 관측이며 영구 해결을 의미하지 않는다.

## 1. 확정된 사실과 남은 의문

- 연결 전에 70/90을 SET하고 별도 GET으로 확인한 뒤, 같은 association에서 FW 응답이 40/65로 바뀌는 현상을 반복 재현했다. 유효 수집 구간에는 추가 RATE_ADAPT_CFG SET, FW 재초기화 명령, 재연결 및 로그 누락이 없었다.
- 원시 HostCmd 0x0264 요청 / 0x8264 응답에서도 Low=0x28, High=0x41을 확인했다. CLI 표시 변환만의 문제로 설명할 수 없다.
- 70/90/500ms가 **40/65/500ms**로 변했다. SR 모드와 평가 주기는 유지됐다. 적어도 이 회차의 응답은 설정 블록 전체가 40/65/100ms로 초기화된 것이 아니다.
- 실제 전송률 결정에 쓰는 내부 임계값이 변경된 것인지, GET 응답을 만들 때 다른 상태를 선택한 것인지는 아직 구별하지 못했다. 정확한 변경 함수와 조건도 미확정이다.
- TX A-MPDU 동작 및 초기화/운영 이력이 관련된다는 통제 시험 근거가 있다. RTS/CTS OFF나 평가 주기 변경을 영구 해결책으로 제시할 근거는 없다.

| Field | Before | After |
| --- | --- | --- |
| SR enabled | 1 | 1 |
| Low | 70 | 40 |
| High | 90 | 65 |
| Eval timer ms | 500 | 500 |

이 표는 fresh-fw500 회차의 동일 association 안에서 직접 관측한 FW 반환값이다. 성능 저하나 실제 MCS 결정 로직의 변경까지 입증한 표는 아니다.

## 2. 과거 기록과 이번 결과의 관계

기존 노션에는 2026-08-28의 **70/90 -> 30/50 한 차례 관측**과 이후 조합 시험의 미재현이 기록돼 있다. 최초 관측 전 비정상 SDIO 리셋과 잘못된 FW 경로에 의한 로드 실패라는 교란도 뒤에 보강됐다. 2026-08-31의 reassociate, roam 요청, 타 AP/채널 전환 후 관측에서도 70/90이 유지됐다고 기록돼 있다. 따라서 사용자의 "이전에 한 번 발생했지만 이후 재현 시험에서는 발생하지 않았다"는 기억은 당시 기록과 일치한다.

이번 40/65는 정상 FW 다운로드를 확인한 새 부팅과, 같은 FW 부팅에서 재설정한 연결 양쪽에서 재현됐다. 이전 30/50과 이번 40/65를 동일 조건·동일 원인이라고 확정하지 않는다.

이번 FW의 무설정 시작 상태에서 확인한 기본 동작은 SR dynamic, Low/High=0xff/0xff이다. 40/65 또는 과거의 30/50을 고정된 FW 기본값이라고 부를 근거가 없다. 모듈 재적재와 실제 FW 다운로드는 구분해야 하며, 모듈 재적재 후 값이 남았다는 기록은 전원 차단 뒤 영속 저장을 입증하지 않는다.

기존 문서의 "드라이버 재적재가 유일한 복구 경로"라는 설명은 이번 결과로 범위를 정정한다. 같은 부팅에서도 **연결 해제 -> pre-association SET -> 독립 GET -> 재연결**로 70/90 복원을 확인했다. "호스트 원인 전체 배제"라는 표현도 과도하다. 이번 추적에서 추가 RATE SET 등은 배제했지만 GET 관찰 영향과 앞선 명령의 지연 효과까지 전부 배제한 것은 아니다.

관련 노션:

- [SD9098 rate_adapt_cfg 동작 종합](https://app.notion.com/p/SD9098-rate_adapt_cfg-FW-3ca8a230a04e816b8a8afde294b7738b)
- [wlan-package rate_adapt 설정값 이력과 배포 실태](https://app.notion.com/p/wlan-package-rate_adapt-50-80-70-90-3cd8a230a04e81a0bf20c7692e88b3a3)

## 3. 시험 대상과 방법

| Item | Value |
| --- | --- |
| Target | cts-wlan / NXP i.MX93 11X11 EVK |
| Management SSH | root@192.168.1.1 |
| WLAN | mlan0 / 192.168.0.100 |
| AP | 00:80:4c:c7:7d:dd / 5180 MHz / HT20 |
| Firmware | SD9098----17.92.1.p149.115--MM6X17543.p18-GPL-(FP92) |
| Firmware file | /lib/firmware/cts/sd9098_wlan_v1.bin |
| Firmware SHA256 | 7c3ef6e12d3cfc9bd638d1571ccf6ddd2e96e0ed179ec70664ccb1df0ba29e57 |
| Deployed driver commit | 53cfcf35cef80d93f422a76f81323776ae78742e |
| Source repository | wlan-driver-v2 |
| Inspected source HEAD | d877d7036f59ec539d02c8affe1a3113b419ac08 |
| Utility | /usr/local/bin/mlanutl |
| Config SHA256 | 1525cb04c4b5bfb17dfb149bf72e91160130f95ac766b162b979f70f1d915238 |

원인 분석에 인용한 드라이버 경로는 배포 커밋과 비교한 wlan-driver-v2 소스다. wlan-driver-v3 분석으로 대체하지 않았다. 초기 기록의 관리 주소 192.168.214.5와 이번 접속 주소는 다르다.

설정은 README_MLAN의 pre-association 조건을 지켰다. 연결이 끊긴 것을 확인하고 SET한 뒤 독립 GET으로 검증하고 재연결했다. 연결 중 SET 시도는 원인 판정의 유효 증거에서 제외했다.

주요 시험은 같은 AP에서 10 packets/s 송신과 GET 수집을 수행했다. 후속 pause 시험은 TX BA 협상 성공 확인 후 같은 ping 프로세스를 3초 정지하고, BA 유지와 송신 카운터를 확인한 뒤 재개했다. 카운터는 같은 association 안의 snapshot 차분만 사용했다. 수집 시점의 명령 sequence 연속성, 추가 SET, 실패 응답, association 유지 및 설정 hash를 별도 audit으로 검증했다.

## 4. 재현 및 대조 결과

첫 캠페인은 각 회차마다 실제 새 부팅 후 연결 전에 70/90/100ms를 설정했다. 180초 시험의 모든 유효 회차에서 ping은 1800/1800이었다.

| Trial | AMSDU feedback | TID0 TX AMPDU | Rate after 180 s |
| --- | --- | --- | --- |
| A1 | ON | ON | 40/65 |
| B1 | OFF | ON | 40/65 |
| A2 | ON | ON | 40/65 |
| C3 | OFF | OFF | 70/90 |
| D1 | OFF | ON | 40/65 |

feedback OFF에서도 재현되므로 **AMSDU feedback 이벤트 수신은 필수 조건이 아니다.** C3/D1에서는 feedback OFF를 유지한 채 TID0 TX A-MPDU 설정 하나를 차단/원복했다. C3의 실제 TX A-MPDU/MPDU/octet 증가량은 0이고, D1은 각각 +5525/+5714/+255596이었다. 이 비교는 TX A-MPDU 경로와의 관련성을 지지하지만 모든 환경의 필요조건을 입증하지는 않는다.

아래 Seconds는 송신 재개부터 첫 40/65 관측 또는 관찰 종료까지다. AMPDU delta는 송신 재개 전 snapshot부터 첫 변화 직후 또는 관찰 종료 snapshot까지다.

| Trial | RTS/CTS | Timer ms | Seconds | Rate | AMPDU delta |
| --- | --- | ---: | ---: | --- | ---: |
| cold-pause-on | ON | 100 | 0.613 | 40/65 | 5 |
| warm-pause-on | ON | 100 | 1.208 | 40/65 | 13 |
| on-pause-repeat | ON | 100 | 5.637 | 40/65 | 109 |
| off-pause-repeat | OFF | 100 | 20 | 70/90 | 407 |
| on-pause-reversal | ON | 100 | 5.730 | 40/65 | 109 |
| off-pause-long-retry | OFF | 100 | 180 | 70/90 | 3741 |
| timer500-on | ON | 500 | 180 | 70/90 | 2980 |
| timer100-reversal | ON | 100 | 180 | 70/90 | 3673 |
| fresh-fw100 | ON | 100 | 0.858 | 40/65 | 33 |
| fresh-fw500 | ON | 500 | 1.615 | 40/65 | 10 |

BA를 유지한 3초 정지 중 송신 카운터 증가 없이 70/90을 반환하고 송신 재개 후 변하는 양성 사례를 반복 확보했다. BA 협상 자체를 즉시 임계값을 바꾸는 명령으로 특정할 수 없다. 변화까지의 AMPDU 개수가 서로 다르므로 5개나 109개를 고정 임계 패킷 수로 해석하지 않는다.

같은 pause 절차에서 RTS/CTS ON 재현 -> OFF 비재현 -> ON 재현을 확보했다. 다만 별도 RTS/CTS ON 연속 송신과 ON 180초 회차에서도 비재현이었다. RTS 실패가 1488회 늘고도 70/90을 유지한 사례가 있으므로 RTS 실패만으로 항상 변한다고 설명할 수 없다. OFF 180초의 비재현도 영구 예방 증거는 아니다.

Warm 500ms와 뒤이은 warm 100ms 모두 180초 비재현이었지만, 새 FW 부팅에서는 양쪽 모두 재현됐다. 500ms로 늘리면 방지된다는 가설은 반박됐다. 초기화/운영 이력이 영향을 주는 것으로 보이지만 재부팅은 호스트 상태도 초기화하며, warm 양성도 존재하므로 cold boot가 필수 조건은 아니다.

## 5. 드라이버 로그로 좁힌 범위

| Logging item | Value |
| --- | --- |
| Baseline drvdbg | 0x80207 / 524807 |
| Collection drvdbg | 0x80237 / 524855 |
| Added command log | MCMND / 0x10 |
| Added event log | MEVENT / 0x20 |
| Raw GET capture | MCMD_D / 0x20000, bounded capture |
| Existing baseline bit | MFW_D / 0x80000 |

MCMND/MEVENT로 HostCmd ID/action/sequence와 이벤트 순서를 수집했다. MCMD_D는 연결 이후의 제한된 원시 GET 확인에 사용했다. MFW_D가 기본값에 포함돼 있다는 사실은 FW 내부 rate-control 변수와 변경 사유가 출력된다는 뜻이 아니다. 광범위한 연결 과정 hexdump는 사용하지 않았다.

feedback ON 양성 회차에서는 다음 순서가 관측됐다.

```text
BA accepted -> TX paused 3 s -> TX resumed -> AMSDU event -> threshold change
               70/90                         t = 0          40/65

timer 100 ms: last 70/90 < t + 100 ms < first 40/65
timer 500 ms: last 70/90 at t + 469.127 ms
             first 40/65 at t + 521.971 ms
```

평가 주기에 따른 FW rate/aggregation 상태 처리와 일치하는 시간 단서다. 이벤트 뒤에도 70/90 응답이 존재하고 feedback OFF에서도 재현되므로 이벤트 자체의 인과관계나 내부 writer를 확정하지 않는다.

여러 양성 회차의 약 50ms 변화 구간에는 RATE_ADAPT_CFG GET/응답만 있었다. fresh-fw500의 약 53ms 변화 구간에는 RSSI_INFO, GET_LOG, SNMP_MIB, TX_RATE_QUERY, STA_CONFIGURE, TXPWR_CFG 조회도 있었다. 앞선 양성 구간에 없었던 명령이므로 이들이 공통 필수 트리거라고 볼 수 없다. 20Hz GET의 관찰 영향이나 더 이전 명령의 지연 효과는 완전히 배제하지 않았다.

로그의 command/event 시각은 드라이버 monotonic timestamp이며 FW 내부 시계가 아니다. 보드 벽시계도 호스트와 어긋나므로 boot ID와 uptime을 기준으로 비교했다. 서로 다른 시계의 timestamp를 빼지 않았다.

소스에서 별도로 발견한 `ba_packet_threshold`의 t_u8 저장과 `1024 + R` 대입에 따른 절단은 별도 조사 메모에 보존했다. 이것이 40/65 변화의 직접 원인이라는 증거는 없고 수정도 적용하지 않았다.

## 6. 해석의 제한

- BA 유지 실패, 수집 시작 전 조건 불충족, 송신 도구 실패 회차는 유효 비재현으로 계산하지 않았다. 세부 제외 사유는 각 캠페인 보고서에 있다.
- off-pause-long-retry의 정지 구간에는 배경/대기 송신 +20 A-MPDU/+47 MPDU가 있었다. 완전 무송신 대조군이 아니다. ping 1813/1812의 한 미응답 원인도 확정하지 않았다.
- on-pause-reversal에는 정지 첫 snapshot 이전 A-MPDU 6개가 있었다. 3초 정지 중 카운터가 고정됐다는 사실과 구분한다.
- fresh-fw100/500 사이 WLAN MAC이 기존 유선 단말 동적 복제 기능으로 달라졌다. 두 부팅의 시간 비교는 완벽한 단일 변수 비교가 아니다. fresh-fw500 한 association 안에서의 임계값 선택적 변화 관측은 유효하다.
- FW 응답값 변화는 통신 장애 발생이나 성능 회귀 자체를 뜻하지 않는다. 실제 전송률 결정 임계값과 GET 값의 일치 여부가 남아 있다.

## 7. 원인을 확정하려면

동일 FW 소스 또는 NXP 계측 FW에서 **실제 MCS 결정에 쓰는 임계값과 GET 응답값을 같은 시점·동일 peer/TID 문맥으로 비교**해야 한다. 다음 정보를 함께 기록해야 정확한 변경 주체를 특정할 수 있다.

- 실제 Low/High, static/dynamic 플래그, 평가 주기.
- old/new 값, 변경 함수 또는 PC, 변경 사유.
- BSS/peer/TID와 association generation, 공통 monotonic timestamp.
- AMSDU/aggregate 상태 처리 및 평가 주기 실행 시점.
- RATE_ADAPT_CFG GET 응답에 넣은 값과 그 값의 출처.

| Actual decision state | GET response | Interpretation |
| --- | --- | --- |
| 40/65 | 40/65 | Actual threshold changed |
| 70/90 | 40/65 | GET readback/state-selection mismatch |

계측 FW가 없으면 제어된 RF 조건에서 전송률 변화를 관찰해 실제 동작에 미치는 영향을 추가 검증할 수 있다. 단 ICMP ping 손실률을 FW가 사용하는 aggregated TX 성공률로 대체할 수 없고, 동작 비교만으로 변경 함수까지 확정할 수는 없다. 이번 마무리에서는 해당 추가 시험을 실행하지 않았다.

## 8. 2026-09-12 종료 시점 상태와 작업 범위

마무리용 읽기 전용 조회: boot ID `45531b5c-5eab-4309-b285-0934bfbd8806`, uptime **1133.74초**.

| Item | Closing observation |
| --- | --- |
| Rate | SR enabled / 70/90 / 100ms |
| RTS/CTS | mode 0 |
| AMSDU feedback | ON / buffer 3367 |
| Aggregation priority table | Original table retained |
| drvdbg | 524807 / 0x80207 |
| WPA state | COMPLETED / same AP |
| Expected units | 26/26 active |
| Association success/failure | 2 / 0 |
| Command timeout / command H2C / TX H2C failures | 0 / 0 / 0 |
| Config and FW hashes | Match recorded baseline |
| Owned boot drop-in | Absent |
| Owned target temporary directories | Absent |
| Current-boot trial ping PIDs | Absent |

현재 WLAN MAC `70:5d:cc:08:20:94`는 wifi_init의 기존 동적 MAC 복제 결과다. 이전 MAC으로 강제 변경하지 않았다. 이번 종료 확인은 설정 조회만 수행했고 재부팅이나 신규 재현 시험을 하지 않았다.

26개 unit의 active 상태는 FTP admin 인증과 전체 운영 명령의 기능 검증 완료를 뜻하지 않는다. 자격증명이 필요한 검증까지 완료했다고 기록하지 않는다. 이 문서의 완료 범위는 임계값 조사 정리와 타겟 진단 항목 정리 확인이다.

원인 수정, 드라이버/FW 재빌드, Yocto/BitBake 실행은 없었다. 후속 내부 원인 확정은 미해결 항목으로 남긴다.

## 9. 증거 위치

원시 로그와 audit은 wlan-package 작업 공간의 아래 release 디렉터리에 있다. 2026-09-12 마무리 시 기존 세 manifest의 **53 + 40 + 72 = 165개 항목**을 크기와 SHA256으로 재검증했으며 불일치는 없었다. 기존 캠페인 파일은 수정하지 않았다. release 자료는 로컬 보관본이며 Git 추적 대상이 아니다.

- [feedback 및 A-MPDU 차단/원복 보고서](../release/rate-feedback-aba-20260912-9ur9e_lj/FINDINGS.md)
- [BA 정지/재개 및 드라이버 경로 보고서](../release/rate-trigger-isolation-20260912-nqejfbxt/FINDINGS.md)
- [RTS/CTS 및 평가 주기 후속 보고서](../release/rate-trigger-followup-20260912-sgz6btnk/FINDINGS.md)
- [임계값 선택적 변화 검증](../release/rate-trigger-followup-20260912-sgz6btnk/selective-threshold-change-report.json)
- [별도 BA 임계값 폭 조사](../release/rate-trigger-isolation-20260912-nqejfbxt/ba-threshold-width-finding.md)
- [마무리 타겟 상태 원본](../release/rate-investigation-close-20260912-2rhbhcs2/closing-state.json)

후속 분석에서는 각 캠페인의 `*-audit-v2.json`과 원시 JSONL을 함께 사용한다. 실패 회차를 유효 대조군에 섞거나, 보고값 변화와 실제 FW 내부 writer 확정을 같은 결론으로 취급하지 않는다.

## 10. 2026-09-13 추가 확인: 송신 중 RATE GET 없이도 재현

남아 있던 관찰 영향 중 **송신 중 20Hz RATE_ADAPT_CFG GET이 있어야만 40/65가 반환되는지**를 확인했다. 두 번의 실제 새 FW 부팅에서 같은 FW hash, AP, 주파수와 MAC을 확인하고, pre-association 70/90/100ms, RTS/CTS ON, AMSDU feedback ON을 적용했다. 기존 설정 감시를 수행하는 `wifi_logger_link@mlan0.service`를 회차 동안 정지했다.

송신 직전 독립 GET으로 70/90을 확인한 뒤 약 60초간 10 packets/s 연속 송신하고, 그동안 RATE_ADAPT_CFG GET을 보내지 않았다. 종료 시 첫 GET은 **송신 프로세스가 아직 실행 중일 때** 수행했다. 같은 명령 sequence의 요청/응답과 원시 CLI 출력을 대응시켜 확인했다.

| Trial | Gap to endpoint GET s | RATE GETs in gap | First response | Ping Tx/Rx | TX AMPDU delta |
| --- | ---: | ---: | --- | --- | ---: |
| endpoint-continuous1 | 60.056459 | 0 | 40/65/100ms | 597/578 | 1667 |
| endpoint-continuous2 | 60.097369 | 0 | 40/65/100ms | 598/597 | 1704 |

Gap은 마지막 70/90 응답부터 종료 시 첫 GET 요청까지의 드라이버 monotonic 시간 차이다. 송신 중 60초 구간을 포함한다. 첫 회차의 마지막 70/90 응답 sequence는 2477, 첫 종료 GET 요청은 3361이고, 두 번째는 각 회차 audit의 exchange 항목에 보존했다. 전체 수집 로그 1125개/1136개 record의 watermark와 sequence 연속성을 검증했다. 추가 RATE SET, FW lifecycle reset, association 변경 및 명령 실패는 없었다.

```text
GET 70/90 -> TX for 60 s, RATE GET count = 0 -> first GET 40/65/100ms
             BA accepted; AMSDU events
```

따라서 **송신 중 지속적·고빈도 RATE GET은 현상의 필수 조건이 아니다.** 20Hz 반복 조회 때문에만 나타난 현상이라고 설명할 수 없다. 이 반례를 두 회차 확보했으므로 이번 추가 조사에서 고빈도 조회 대조군을 다시 실행하지 않았다.

이 결론은 모든 호스트 명령의 영향을 배제하지 않는다. 두 회차 모두 GET_LOG 등 다른 운영 조회와 BA 명령이 있었으며, MGMT_IE_LIST SET 및 SCAN_EXT도 각각 한 번 있었다. 정확한 임계값 변경 시점은 60초의 관측 공백 안에서 특정할 수 없다. AMSDU 이벤트가 종료 GET 전에 발생한 것은 확인했지만, 이 시험으로 이벤트와 임계값 변경 사이의 지연을 측정한 것은 아니다.

연결 전 설정 검증과 송신 전 baseline 조회는 수행했다. 단일 GET의 응답 구성 문제, 더 이전 명령의 지연 효과, FW가 실제 MCS 결정에 쓰는 값과 반환값의 차이는 여전히 미확정이다. 7절의 동일 FW 내부 계측이 정확한 writer/사유를 확정하는 다음 단계다.

두 회차의 ping 집계에는 각각 19개/1개 미응답이 있었다. 손실 원인이나 임계값 변화와의 인과관계를 확정하지 않았으며, 무손실 통신 시험으로 기록하지 않는다. 카운터 증가는 같은 association 안에서 계산했다. TX AMPDU 증가는 실제 전송 참여 근거이며, 고유 ping 패킷 개수나 임계 패킷 수가 아니다.

시험 전 두 부팅은 유효 비교에서 제외했다. `endpoint-cold1`은 기존 동적 MAC 복제 결과가 직전 운영 부팅과 달라 사전 검사에서 중단됐고 송신/수집 시험을 시작하지 않았다. `endpoint-cold2`는 TX BA 성공 후 약 0.228초에 peer DELBA가 발생해 3초 BA 유지 조건에 실패했다. 이를 비재현으로 계산하지 않았다. 이후 연속 송신 절차와 회차 내 GET 0회 판정 기준을 실행 전에 기록했다.

유효 두 회차의 MAC은 모두 `00:e0:4c:68:2b:1f`로 같았다. 직전 운영 부팅의 `70:5d:cc:08:20:94`와는 다르므로 과거 부팅과의 단일 변수 비교로 해석하지 않는다. MAC과 영구 설정을 강제로 변경하지 않았다.

추가 시험 후 연결을 해제하고 70/90/100ms를 SET 및 독립 GET으로 복원한 다음 재연결했다. 최종 확인은 boot ID `af478bde-e996-414f-8f14-07bbd120c8d7`, uptime **144.19초**다. 70/90/100ms, RTS/CTS mode 0, AMSDU feedback ON, 기존 aggregation 표, drvdbg 524807, 26개 unit active, 설정/FW hash 일치, 현 부팅 시험 ping 종료를 확인했다. 임시 부팅 drop-in과 타겟 시험 디렉터리도 제거했다. 이는 최신 복구 시점의 상태이며 원인 수정이나 전체 운영 기능 검증 완료가 아니다.

추가 증거는 `release/rate-get-observer-20260912-hgg51v6v/`에 보관했다. 디렉터리 날짜는 UTC 기준으로 시작한 작업명이고 이 보강의 날짜는 한국 시간 기준이다. 기존 캠페인의 165개 증거 항목은 해시가 일치했고, 보강 전 문서도 `document-before-followup.md`로 보존했다.

- [추가 조사 보고서](../release/rate-get-observer-20260912-hgg51v6v/FINDINGS.md)
- [첫 회차 GET 공백 검증](../release/rate-get-observer-20260912-hgg51v6v/endpoint-continuous1-observer-audit.json)
- [반복 회차 GET 공백 검증](../release/rate-get-observer-20260912-hgg51v6v/endpoint-continuous2-observer-audit.json)
- [최신 복구 상태](../release/rate-get-observer-20260912-hgg51v6v/final-state.json)

## 11. 2026-09-14 추가 확인: 반복 HostCmd 제거 후에도 재현

20Hz `RATE_ADAPT_CFG` 조회가 필요하지 않다는 10절의 결론에 이어, 다른 주기적 HostCmd도 필수 조건인지 확인했다. 실제 새 FW 다운로드가 확인된 부팅에서 알려진 WLAN 백그라운드 서비스 9개를 정지하고, 연결 후 독립 GET으로 70/90/100ms를 확인한 뒤 약 60초간 10 packets/s 송신했다. 이 구간에는 `RATE_ADAPT_CFG` GET을 보내지 않았고, 송신 중 첫 종료 GET이 **40/65/100ms**를 반환했다. ping은 597/597이었다.

| Trial | Known background units stopped | Commands between rate observations | Result | Verdict |
| --- | ---: | --- | --- | --- |
| quiet-hostcmd1 | 8 | BA 2, sensor temperature 24, STA_CONFIGURE 1 | 70/90 -> 40/65 | 온도 로거가 남아 최소 HostCmd 판정에서 제외 |
| quiet-hostcmd2 | 9 | BA response 1, BA request 1, STA_CONFIGURE GET 1 | 70/90 -> 40/65 | 유효 양성 |

유효 회차 `quiet-hostcmd2`의 boot ID는 `df52cbcd-f5af-4df2-ab2a-179515bd7a67`이고 FW hash는 기존과 동일하다. 마지막 70/90 응답부터 첫 40/65 요청까지의 드라이버 monotonic 간격은 **60.014376초**다. 원시 trace 59개 record, sequence 2295..2353의 연속성을 확인했다. 구간 내 세 HostCmd 응답은 모두 result 0이었고, 추가 RATE SET, FW lifecycle 명령, association 변경, command timeout/H2C 실패는 없었다.

남은 `STA_CONFIGURE` action-0 요청은 드라이버 uptime 99.295091초에 한 번 발생했다. 소스상 `wlan_bss_ioctl_get_chan_info()`가 `MLAN_OID_BSS_CHAN_INFO`를 `HostCmd_CMD_STA_CONFIGURE` GET으로 준비하는 채널/밴드 정보 조회다(`wlan-driver-v2/mlan/mlan_sta_ioctl.c:218-246`, `mlan_sta_cmd.c:3370-3373`, `mlan_sta_cmdresp.c:2695`). cfg80211의 station channel 조회 및 active-interface channel 조회에서 이 경로를 호출할 수 있다(`mlinux/moal_sta_cfg80211.c:7450`, `mlinux/moal_ioctl.c:2406-2418`). 두 quiet 부팅 모두 거의 같은 FW uptime 99.295초에 나타나 예약된 조회로 보이지만, 정확한 userspace caller는 확인하지 못했다.

따라서 **반복 RATE GET과 알려진 백그라운드 서비스의 주기적 HostCmd는 40/65 응답의 필수 조건이 아니다.** 단일 `STA_CONFIGURE` 채널 정보 조회는 아직 트리거 후보에서 제외할 수 없고, BA 명령만으로 변했다고 단정하지 않는다. FW 내부 writer와 실제 MCS 결정 상태 대 GET 응답 상태의 일치 여부는 여전히 7절의 FW 계측이 필요하다.

시험 뒤 소유한 부팅 drop-in과 타겟 임시 디렉터리를 제거하고 26개 운영 unit을 재시작했다. 최신 읽기 검증은 같은 boot ID, uptime **117385.49초**, 70/90/100ms, RTS/CTS, AMSDU feedback ON/buffer 3367, drvdbg 524807, association success/failure 2/0, command timeout/H2C/TX H2C failure 0/0/0, 설정/FW hash 일치, drop-in 없음, 임시 경로 없음으로 통과했다. FTP 자격증명 기반 기능 검증이나 전체 운영 인수 시험을 완료했다는 뜻은 아니다.

추가 증거는 `release/rate-hostcmd-quiet-20260913-qr5bkf7v/`에 보관했다.

- [추가 조사 보고서](../release/rate-hostcmd-quiet-20260913-qr5bkf7v/FINDINGS.md)
- [유효 회차 감사 결과](../release/rate-hostcmd-quiet-20260913-qr5bkf7v/quiet-hostcmd2-quiet-audit.json)
- [두 회차 비교 보고서](../release/rate-hostcmd-quiet-20260913-qr5bkf7v/comparison-report.json)
- [최종 복구 상태](../release/rate-hostcmd-quiet-20260913-qr5bkf7v/final-state.json)

## 12. 2026-09-14 캠페인 3건: 지배 변수·실사용 여부·출하 기본값 결정

캠페인 3건, 총 17 arm. 부팅마다 fresh FW 다운로드, 배경 WLAN 서비스 9종 정지(제품 경로 확인 arm 은
예외), 60초 트래픽, `rate_adapt_cfg` 50ms 조밀 폴링. 각 arm 은 실행 **전에** 예측을 계약 파일에
기록했다. 원시 trace·arm 별 audit·계약은 빌드 호스트의 `release/rate-static-pair-20260914-0e769997`,
`release/rate-load-boundary-20260914-0a2484b3`, `release/rate-effective-20260914-3e9d7ee7` 에 있다
(`release/` 는 gitignore 이므로 저장소에는 없다). 각 디렉터리의 `FINDINGS.md` 가 정본이다.

### 12.1 지배 변수는 설정 LOW 이고 HIGH 는 무관

| LOW | 결과 (가벼운 트래픽) |
| ---: | --- |
| 50 | 무전환 (3 arm) |
| 55 | 무전환 |
| 58 | 무전환 |
| 60 | 전환 |
| 70 | 전환 |

`HIGH=90` 이 전환 arm(`70/90`)과 무전환 arm(`58/90`·`55/90`·`50/90`) 양쪽에 나타나므로 `HIGH` 는
지배 변수가 아니다. `dynamic`(`1 0xff 0xff 10`)은 완화되지 않는다.

### 12.2 트리거는 association 이 아니라 집계 Tx 트래픽이다

전환은 송신 개시 **2~3초** 후 **50~60ms** 창 안에서 단일 계단으로 일어난다. 배경 서비스를 정지한
조건에서 전환 전후 구간의 HostCmd 는 ADDBA 교환 2건과 `STA_CONFIGURE` 1건뿐이었고, 그
`STA_CONFIGURE` 는 전환보다 **22.3초·18.9초 늦게** 발생했다 — 즉 트리거가 아니다. 8절의 "호스트
원인" 후보 중 남아 있던 이 항목은 이로써 배제된다.

### 12.3 경계는 링크 상태에 따라 움직인다 — 고정 상수가 아니다

| 트래픽 프로파일 | MPDU당 재시도 | 경계 |
| --- | ---: | --- |
| 10 pps / 56 B | 0.676 | `58 < LOW <= 60` (수 시간 뒤 더 나쁜 링크에서 58 이하) |
| 100 pps / 56 B | 0.512~0.534 | `LOW <= 65` |
| 1000 pps / 1400 B | 0.392~0.410 | `75 < LOW <= 80` |

집계 **깊이**는 관여하지 않는다(전 arm `MPDU/AMPDU` 1.0005~1.026). 재시도율 기반 정량 모델 2개를
사전 등록해 시험했고 **둘 다 반증**됐다. 프록시 자체가 같은 프로파일에서 4% 변동하므로 이 계측으로는
몇 점 단위 분해가 불가하다. 비교 대상 물리량 식별은 벤더 질문이다.

### 12.4 도착값은 대개 `40/65` 다 — 다만 불변은 아니다

**[정정 2026-09-14]** 이 절은 원래 "도착값은 `40/65` 로 불변"이라고 적었다. 그 주장은 §12.10 에서
반증됐다. 이 절의 측정 자체는 유효하다: 이 시점까지 출발값 5종에서 관측된 전환 **18회는 전부**
정확히 `[40, 65]` 로 착지했다(`70/90`×14, `58/90`, `60/80`, `65/90`, `80/90`). 그러나 후속 시험에서
`60/90` 이 `[30,50]` 으로 착지하는 반례가 나왔으므로 **`[40,65]` 는 지배적 착지값이지 유일한 값이
아니다**. 2절의 과거 `30/50` 관측을 "교란·미재현 단일 관측"으로 배제한 종전 판단도 §12.10 에서
철회한다.

### 12.5 덮어쓴 값은 실사용 상태다 (표시 문제가 아니다)

`mlanutl getdatarate` 를 200ms 로 표본화해 5초 정착 이후 분포를 비교했다.

| arm | 설정 → 보고 | 평균 TX rate | MCS 질량 |
| --- | --- | ---: | --- |
| `reflow4065` | 40/65 유지 | **58.11 Mbps** | MCS6/7 |
| `test7090` | 70/90 → **40/65** | **58.66 Mbps** | MCS6/7 |
| `refhigh4090` | 40/90 유지 | **53.95 Mbps** | MCS5 |

사전 선언한 계측 유효성 게이트(두 진짜 참조가 3.0 Mbps 이상 차이) 통과 — 실제 `d=4.16`. 허용오차
`d/3=1.39` 기준으로 `|test7090 − 40/65| = 0.55`(내부), `|test7090 − 40/90| = 4.71`(외부) →
**live**. MCS 히스토그램이 평균과 독립적으로 같은 결론을 준다. 6절의 "GET 이 실사용 임계값인지
불명" 항목은 이로써 해소된다.

### 12.6 연결 중 재설정은 불가 — 모니터링 기반 교정은 성립하지 않는다

연결 상태 SET 은 출력만 바뀌고 즉시 GET 은 덮어쓴 값을 그대로 반환한다(벤더 계약도 pre-association
한정). 유일한 재설정 경로는 연결해제 → SET → 재연결이며, 트래픽 2~3초 후 다시 드리프트하므로 자동
교정 루프는 플래핑만 만든다. `fwcfg_watch` 는 **관측·경고 전용**으로 쓴다.

### 12.7 FW 기본값(dynamic)은 면제이고, 제품 opt-out 으로 도달 가능하다

`rate_adapt.enabled=false` 로 두고 재부팅하면 `wifi_init` 이 SET 을 건너뛰고
(`[mlan0] rate_adapt disabled; skip`) 인터페이스는 dynamic 을 보고한다. 60초 트래픽에서 967/967
표본이 dynamic 을 유지했다. 재부팅은 매번 FW 를 재다운로드하므로 "콜드부팅 후에만 FW 기본값"
조건을 충족한다 — 단 **드라이버 모듈만 재적재하면 도달하지 않고** 마지막 SET 값이 남는다.

### 12.8 결론과 출하 기본값 결정 (0.6.7)

제품은 부팅마다 `70/90` 을 pre-association 에 SET 했으나 그 값은 트래픽 2~3초 후 `40/65` 로
덮어써지고 그것이 실사용 상태였다 — 즉 **`70/90` 은 현장 동작을 한 번도 지배한 적이 없다**. 그래서
0.6.7 에서 출하 기본값을 `40/65` 로 옮겼다. 설정을 실제 동작에 맞추는 변경이며,
혹시 더 나쁜 링크에서 경계가 40 아래로 내려가도 도착값이 같아 무해하다.

**신규 설치·factory reset 한정**이다. `postinst` 의 JSON deep merge 가 기존 키를 보존하므로 기존
기기는 `70/90` 을 유지한다(동작은 이미 `40/65`). 전환하려면 `wifi <iface> rate 1 40 65 100` 후
**재부팅**한다.

온타겟 확인은 제품 경로로 했다 — drop-in 없이, 서비스 정지 없이(활성 `wifi*`/`wpa*` 26 유닛),
`wifi_init` 이 `rate_adapt configured: mode=1 low=40 high=65 interval_ms=100` 을 남기고, association
후 baseline `[40, 65]`, 60초 트래픽에서 **1106 표본 전환 0회**, ping 597/597 로 유지됐다.

근본 수정은 FW 몫이며 벤더 요청서는 `docs/rate_adapt_cfg_nxp_request.md` 다.

### 12.9 무거운 트래픽에서의 효과 크기 — 두 설정이 구분되지 않는다

12.8 의 "동작 중립" 논거에는 구멍이 있었다. 경계가 `75<LOW<=80` 인 무거운 프로파일에서는 `70/90` 이
**덮어써지지 않고 살아남으므로**(heavy arm 0/2 전환), 그 regime 의 기기에서는 기본값 변경이 실제
동작 변경이 된다. PR #332 의 Codex P1 이 이 점을 지적했고, 지적이 맞았다. 그래서 그 regime 에서
두 설정을 직접 측정했다.

| arm | 설정 | 전환 | 평균 TX rate | MCS 분포 | 재시도/MPDU |
| --- | --- | ---: | ---: | --- | ---: |
| `heavycfg7090` | 70/90 | 0 | **45.5300 Mbps** | MCS4:108, MCS5:109 | 0.3928 |
| `heavycfg4065` | 40/65 | 0 | **45.6498 Mbps** | MCS4:106, MCS5:111 | 0.3978 |

차이는 **0.1198 Mbps** 로, 사전 선언한 허용오차 **1.39 Mbps**(가벼운 프로파일에서 측정한 계측
분리력 `d=4.16` 의 1/3)의 약 1/12 다. MCS 히스토그램도 사실상 동일하며 어느 쪽도 MCS6/MCS7 에
도달하지 않았다. 측정 regime 도 일치한다(옥텟/MPDU 900.3 vs 892.7, ACK 실패/MPDU 0.0025 vs
0.0027). 두 arm 모두 전환 0 회이므로 각 설정이 창 전체에서 live 였고 비교가 유효하다.

따라서 동작 중립은 두 regime 에서 **각각 다른 이유로** 성립한다 — 가벼운 쪽은 귀결이 같아서,
무거운 쪽은 살아남은 두 설정의 선택 rate 가 구분되지 않아서다. 사전 등록한 방향 예측
(`mean(40/65) >= mean(70/90)`)은 수치상 성립하지만 차이가 run-to-run 잡음 안이므로 방향 자체는
이 측정으로 **검정되지 않았다** — 방향 주장은 하지 않는다.

한계: 설정당 1 회, 한 링크 품질, 한 무거운 프로파일(1000 pps / 1442 B). 다른 고부하 형태는
포함하지 않으며, 측정 대상은 선택 TX rate 이고 응용 처리율·지연은 아니다.

### 12.10 2026-09-14 4번째 캠페인: 링크 품질 -> 경계 인과, 그리고 착지값 불변 반증

`release/rate-txpwr-inverse-20260914-c38b243a` — 4 arm, 부팅마다 fresh FW, 배경 서비스 9종 정지, 연결 전 부트 드롭인 게이트,
light 프로파일(`-i 0.1`, 56B, 60s 창, 50ms 폴링). 레버는 STA Tx 전력이다.

#### 설계

§12.3 의 "경계는 링크 상태에 따라 움직인다"는 **기회적 관측**(몇 시간 뒤 더 나쁜 링크에서 58 이하)
이었고 통제 개입이 아니었다. 이 캠페인은 그 인과를 통제 시험으로 바꾼다. 예측·반증 조건·계기
게이트를 전부 실행 전에 등록했다(`experiment-contract.md`, `amendment_1.md`, `amendment_2.md`).

선행 시험(`release/rate-txpwr-20260914`)에서 확정한 사실이 설계를 바꿨다: **이 근거리 링크
(-48 dBm)에서 Tx 전력 저감은 링크를 악화시키지 못하고 개선한다.** 기본 20 dBm 이 가장 나쁘고
(재시도/MPDU 0.677~0.707), 2 dBm 이 가장 좋다(0.0057~0.0085). 0 dBm 까지 패킷 손실 0 이다.
20/2/20/2 교대 실행으로 순서 교란을 배제했다. 따라서 "저감으로 경계를 내리는" 시험은 불가능하고
**"개선으로 경계를 올리는"** 역방향만 가능하다.

부수적으로 `iw dev mlan0 set txpower fixed` 가 이 드라이버에서 FW 까지 도달함을 확인했다 —
근거는 readback 이 아니라 행동 오라클이다(설정에 맞춰 효과가 나타나고 사라지고 다시 나타났다).
`mlanutl txpowercfg` 그룹 최대값도 전 그룹 단일값으로 평탄화된다. 런타임 수동 조정에는 제품
스크립트 `dist/wlan/usr/local/scripts/txpower_family_set.sh` 를 쓴다.

#### 결과

| arm | pair | txpower | 전력표 | 재시도/MPDU | 전환 | 착지값 | 지연 |
|---|---|---|---|---|---|---|---|
| `fullL70` | 70/90 | auto | `[15,17,20]` | 0.6655 | YES | `[40,65]` | 2.90s |
| `lowL70` | 70/90 | 2 dBm | `[2]` | 0.0132 | YES | `[40,65]` | 2.60s |
| `fullL60` | 60/90 | auto | `[15,17,20]` | 0.7225 | YES | **`[30,50]`** | **0.24s** |
| `lowL60` | 60/90 | 2 dBm | `[2]` | 0.0050 | **NO** | `[60,90]` 유지 | - |

전 arm 계기 게이트 4/4 통과(전력표 평탄화 확인, 재시도 분리, 트래픽 597~598/598, 연결 전 증명),
gapless 트레이스(`gaps: []`), fresh FW 다운로드 확인.

#### 판정 1 — 경계는 링크 품질을 따른다 (첫 통제 인과 증거)

같은 `LOW=60` 에서 기본 전력은 전환했고 2 dBm 은 전환하지 않았다. 즉 경계가 기본 전력의 `<60` 에서
2 dBm 의 `60<LOW<=70` 으로 **올라갔다**. 재시도/MPDU 0.7225 -> 0.0050 (약 145배) 개선이 원인이다.

`LOW=70` 쌍(arm1·arm2)은 **판별력이 없었다** — 70 이 두 조건 모두에서 경계 위였으므로 양쪽 전환이
당연하다. 계약서의 반증 조건이 단일 LOW 로 경계 이동을 반증하도록 잘못 적혀 있었고, 그에 따라
`amendment_1` 이 내린 "H1' 반증" 판정은 `amendment_2` 에서 철회했다. 경계 이동 가설은 **경계를
사이에 두는 LOW 값**으로만 시험할 수 있다.

#### 판정 2 — 착지값 불변 반증

`60/90` 이 단일 계단으로 `[30,50]` 에 착지했다(`[40,65]` 경유 없음, 50ms 폴링 1009 표본).
전 캠페인 11개 디렉터리의 표본 시퀀스를 전수 재계수한 결과:

- rate 표본을 가진 arm **48개**, 전환 **21회**
- 착지값 `[40,65]` **20회** + `[30,50]` **1회**
- 출발값 `[70,90]`x16, `[58,90]`, `[60,80]`, `[65,90]`, `[80,90]`, `[60,90]`
- **21회 전부 단일 계단**(중간 프로파일 경유 없음)

따라서 2절의 2026-08-31 `30/50` 관측은 **실재였다**. 당시 자극(association 5회)이 트래픽 트리거를
만들지 못했을 뿐이며, 그 관측을 "교란·미재현"으로 설명해 배제한 §12.4 의 종전 판단은 철회한다.

또한 같은 `LOW=60` 이 `HIGH=80` 에서는 `[40,65]`(`pair6080`), `HIGH=90` 에서는 `[30,50]` 로 갔다.
§12.1 의 "`HIGH` 는 무관"은 **전환 여부**에 대한 주장이며 **착지값에는 적용되지 않는다**.

#### 설명되지 않은 사실 (모델 세우지 않음)

1. `LOW=60` 이 `LOW=70` 보다 **더 빨리** 전환했다(0.24s vs 2.90s). 낮은 임계는 만족하기 쉬우므로
   더 늦거나 전환하지 않아야 직관에 맞는다.
2. 착지 프로파일 선택 규칙 — `HIGH` 가 관여하지만 `60/80`->`[40,65]` 와 `60/90`->`[30,50]` 을
   함께 설명하는 규칙을 찾지 못했다.
3. 커널 트레이스로는 무엇이 `30/50` 을 썼는지 디코드할 수 없다. 이 drvdbg 마스크(524855)는 hex
   페이로드를 찍지 않고, 보이는 `RATE_ADAPT_CFG act 0x0` 는 전부 우리 폴링 GET 이다.

자체 모델은 이미 2건 반증됐으므로(§12.3) 수정 모델을 제안하지 않고 벤더 질문으로 넘긴다.

#### 타겟 복원

캠페인 후 드롭인 제거·상태 복원·재부팅·독립 검증 완료: `validation: passed`, 활성 유닛 26개,
`drvdbg=524807`, `DropInPaths=` 비어 있음, 임시 경로 부재, `config_sha256`·`firmware_sha256` 일치.

### 12.11 2026-09-14 5번째 캠페인: 현장 튜닝 가능값과 계기 분해한계

`release/rate-tune-4560-20260914-384f81e5` — 4 설정(`40/65`, `40/90`, `45/60`, `50/70`) x 2 반복 = 8 arm.
arm 마다 재부팅으로 fresh FW, 배경 서비스 9종 정지, 연결 전 부트 드롭인 SET,
light 프로파일(`-i 0.1`, 56B, 60s 창, 50ms 폴링, `getdatarate` 200ms 표본화, settle 5s).

#### 왜 했는가
사용자가 출하 기본값을 `45/60` 으로 하고 현장에서 `50/70` 으로 튜닝하는 안을 제시했다.
두 값 모두 **성능을 측정한 적이 없었고**, `45` 는 전환 여부조차 시험되지 않은 값이었다.
0.6.7 의 `40/65` 는 "동작 중립"을 측정해 정당화했는데 그 근거는 `40/65` 가 FW 착지값이라는
사실에 의존하므로 `45/60` 으로 옮겨가지 않는다. 측정 없이 출하하면 PR #332 에서 지적받은
것과 같은 실수(측정하지 않은 중립성 주장)를 반복한다.

#### 결과
| 설정 | 1회차 | 2회차 | 평균 | 설정 내 산포 | 전환 |
|---|---|---|---|---|---|
| `40/65` | 57.1795 | 57.6171 | 57.3983 | 0.4376 | 0회 x2 |
| `40/90` | 54.5000 | 53.3597 | 53.9299 | 1.1403 | 0회 x2 |
| `45/60` | 58.4638 | 54.6312 | 56.5475 | **3.8326** | 0회 x2 |
| `50/70` | 52.4005 | 54.4523 | 53.4264 | 2.0518 | 0회 x2 |
전 8 arm 게이트 통과(연결 전 SET readback, ping 597~598 전량 수신, gaps [], fresh FW).

#### 확정 1 — 전환 여부
**`45/60` 과 `50/70` 은 이 링크에서 덮어써지지 않는다.** 각 2 회 독립 부팅에서 재현됐다.
누적 `LOW<=55` 무전환 관측은 **12 arm** 이다(`50/70` x4, `50/90`, `55/90`, `45/60` x2,
`40/65` x3, `40/90` x2 중 light 프로파일 기준). `45` 는 이번에 처음 시험됐다.

#### 확정 2 — 성능 비교는 VOID (계기 분해한계)
```
s = max|1회차 - 2회차| = 3.8326 Mbps   (45/60)
d = |40/65 평균 - 40/90 평균| = 3.4684 Mbps
사전 등록 조건: d >= 3.0 (통과) AND d > s (실패, 3.4684 < 3.8326)
```
**같은 설정의 두 측정이 기준 설정쌍의 간격보다 더 벌어진다.** 60 초 창에서 이 계기는 설정 간
차이를 분해하지 못한다. `amendment_1` 의 규정대로 비교를 진행하지 않으며, 1회차에서 기록한
"`50/70` 이 4.78 Mbps 낮다"는 신호도 **철회**했다 — 비교를 authorize 하는 게이트가 실패한
상태에서 그 수치로 판정하는 것은 결과를 보고 해석을 맞추는 것이다.

#### 출하 기본값을 `40/65` 로 유지한 근거
성능으로 후보를 가릴 수 없으므로 남는 근거는 셋이다.
1. 관측 최저 경계(58) 대비 여유가 가장 크다 — 18 포인트(`45/60` 13, `50/70` 8).
2. FW 자신의 착지값이어서 설정 표기와 실제 동작이 일치한다. `45/60` 은 이 성질을 잃는다.
3. 설정 내 산포가 가장 작다(0.4376). `45/60` 은 4 설정 중 최악(3.8326)으로,
   성능이 나쁘다는 뜻이 아니라 **이 계기로 특성화가 안 된다**는 뜻이다.

#### 기존 주장에 대한 영향
§12.9 의 heavy regime 비교(허용오차 `d/3`=1.39)는 **설정 내 산포를 측정하지 않았다** —
동일 프로파일 반복 arm 이 없었다. 그 허용오차는 잡음에 대해 검증되지 않았다. 다만 결론이
`indistinguishable` 이고 잡음이 크면 그 결론은 더 쉬워지는 방향이므로 **결론은 뒤집히지
않는다**. 무너지는 것은 함의된 정밀도이므로, 그 절의 서술에서 정밀도를 주장하지 않는다.

#### 한계
- **경계 하한 미측정**. 이 랩 링크의 무전환이 더 나쁜 현장 링크의 무전환을 보장하지 않는다.
  4 번째 캠페인에서 링크 품질이 경계를 움직인다는 것이 통제 시험으로 확인됐고, Tx 전력으로는
  악화 방향 레버가 없어 하한을 재지 못했다.
- light 프로파일만 측정했다. heavy regime 의 이 네 설정은 측정하지 않았다.
