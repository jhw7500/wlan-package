# wifi_init_conf.json 웹 UI 핸드오프 필드 스펙

## 1. 문서 개요

- **목적**: 웹 UI "설정 프로그램"이 `wifi_init_conf.json`을 폼(form)으로 노출/편집할 때 참조하는 **완전한 필드 명세**. 각 필드의 경로·타입·기본값·허용값·UI 편집 가능 여부·적용 시점·소비 스크립트를 정리한다.
- **대상**: 웹 UI 설정 편집기 담당자.
- **단일 소스오브트루스(SSoT)**: 값/구조의 최종 기준은 항상 아래 실제 파일이다. 본 문서와 값이 다르면 JSON이 옳다.
  - `/home/jhw/ai/opencode/projects/wlan-package/dist/wlan/opt/wlan/config/wifi_init_conf.json`

### 파일 경로

| 상황 | 경로 |
|---|---|
| 설치(패키지 내) | `/opt/wlan/config/wifi_init_conf.json` |
| 런타임(기기) | `/usr/local/etc/wifi_init_conf.json` (설치 시 위 파일과 동일 내용으로 연결) |

> MAC 관련 파이썬(`wifi_mac_set.py`/`wifi_mac_save.py`)은 `/usr/local/etc/wifi_init_conf.json`을 읽는다.

### 설정 로드 우선순위

```
환경변수(스크립트별 override)  >  JSON(wifi_init_conf.json)  >  스크립트 내장 기본값
```

- 일부 인터페이스별 필드(`mlanN.Frequency`, `mlanN.enabled`)는 별도 오버레이 `config.json`이 `wifi_init_conf.json`보다 우선한다(해당 필드 비고 참조). `config.json`은 wlan-package가 배포·백업하지 않는 외부 호환 overlay이므로, WebUI 등 공급 측에서 백업·복구 수명주기를 함께 관리해야 한다.
- 인터페이스별 값(`.mlanN.*`)이 있으면 전역(`.global.*`)보다 우선하며, 전역은 fallback이다.

### ⚠️ 업그레이드 시 기본값 미반영 주의

postinst의 `json_merge`는 **기존 값 보존** 방식이다. 따라서 이 문서의 "기본값"이 바뀌어도 **기존 기기 업그레이드에는 반영되지 않고**, 신규 설치 또는 factory reset에만 적용된다. 기존 기기에 새 기본값을 밀어넣으려면 별도 마이그레이션이 필요하다.

### 범례

> **기본값 컬럼은 생성된다**: §3 표의 기본값 셀은 `scripts/gen_config_defaults.py` 가
> 배포 템플릿(`dist/wlan/opt/wlan/config/wifi_init_conf.json`)에서 동기화한다.
> 기본값이 바뀌면 이 문서를 손으로 고치지 말고 **템플릿을 고친 뒤 `--write` 를 실행**할 것.
> CI 테스트(`test_config_default_sync.py`)가 불일치를 차단한다.

**apply_timing (적용 시점)**

| 값 | 의미 |
|---|---|
| `runtime` | 데몬이 매 주기/이벤트마다 JSON 재로드 → 재시작 없이 반영 |
| `daemon-restart` | 해당 데몬 재시작 시 반영(데몬 기동 시 1회 로드) |
| `boot` | 부팅 시(또는 wifi_init 재실행 시) 반영 |
| `reboot` | insmod 인자 등 드라이버 리로드/재부팅 필요 |
| `na` | 동작에 영향 없음(메타데이터/미구현) |

**ui_editable (UI 편집)**

| 값 | 의미 |
|---|---|
| `yes` | 일반 사용자 편집 가능 |
| `caution` | 고급/위험 — 하드웨어·안전·보안·재부팅 정책에 영향, 검증·경고 필요 |
| `no` | 읽기전용/자동설정 — UI에서 편집 금지 |

---

## 2. 최근 변경점 (웹 UI 반영 필요)

이전 연동/구 가이드 대비 달라진 부분. **UI 폼 구조와 기본값을 아래에 맞춰 갱신해야 한다.**

### 2.1 구조 변경

- **`checker`, `arping`가 최상위 → `mlan0`/`mlan1` 하위(인터페이스별)로 이동**. 이제 `mlan0.checker.*`, `mlan1.arping.*` 형태다.
- **`global` 개편**:
  - `FW_NAME`, `MFG_MODE` **제거**. 펌웨어는 `global.BOARD_TYPE` + `global.BUS_TYPE` + `global.BLUETOOTH.enable`로 **자동 선택**된다. `MFG_MODE`는 JSON이 아니라 `mod_para.conf`의 `mfg_mode=`에서 읽는다.
  - `global.rate_adapt` 블록 **제거**. 코드에 fallback 경로만 남아있고 데이터는 없음 → 실질적으로 per-iface(`mlanN.rate_adapt`)만 유효.

### 2.2 신규 섹션/키

- **`snmp` 섹션 신규**: `snmp.enabled` + `snmp.trap.{enabled, dest, community, version}`.
- **`global`**: `BOARD_TYPE`, `BUS_TYPE`, `BLUETOOTH.enable`, `tx_work`, `ping_monitor.enabled`.
- **`wbridge`**: `ip_discovery`, `eth_client_ip`, `eth_sweep_subnet`, `peer_route.enabled`, `arp_ignore_always.enabled`, `engine`(pcap|tpacket|moal), `moal.{keepalive_ms, keepalive_idle_ms, debug, peer, consume_link_local}`, `optimize.*`, `thermal.*` 재구성.
- **`mlanN`**: `connect_threshold`(-100), `mgmt_hex_dump_enable`(false), `thermal_mgmt`(true), `rate_adapt`, `logger.enabled`.
- **`mlanN.roaming`**: `generate_network_blocks`(false), `ROAM_CROSS_FAIL_RETRY_COUNT`(2), `extra_ssids`([]), `enabled`.
- **`mlanN.bgscan`**: `ssid_filter`(true), `freq_filter`(true), `enabled`.
- **`mlanN.periodic_roam`**: `scan_before_roam`(true).
- **`mlanN.checker`**: `RECONFIGURE_GRACE_SEC`(20), `enabled`; **`mlanN.arping`**: `enabled`.

### 2.3 기본값 변동

- **`temperature.*` 임계 전반 상향**:

  | 키 | 이전 | 현재 |
  |---|---|---|
  | emerg_cpu | 93 | **100** |
  | crit_cpu | 90 | **95** |
  | error_cpu | 85 | **90** |
  | warn_cpu | 80 | **85** |
  | emerg_mlan | 85 | **95** |
  | crit_mlan | 80 | **90** |
  | error_mlan | 75 | **85** |
  | warn_mlan | 70 | **80** |

- **`wbridge.thermal.thresholds.*` 상향**:

  | 키 | 이전 | 현재 |
  |---|---|---|
  | warm_cpu_enter | 80 | **90** |
  | hot_cpu_enter | 90 | **95** |
  | warm_cpu_exit | 75 | **85** |
  | hot_cpu_exit | 85 | **90** |
  | warm_wifi_enter | 70 | **85** |
  | hot_wifi_enter | 80 | **90** |
  | warm_wifi_exit | 65 | **80** |
  | hot_wifi_exit | 75 | **85** |

- **`wbridge.eth_link_wait_sec`**: 3 → **5**.
- **로밍 고급기능 정리**:
  - `roaming.PREDICTIVE_ROAM.enable` → false (보류 — 2층 판정 이력 소스)
  - `roaming.ADAPTIVE_INTERVAL` / `POST_ROAM_ARP_OPTIMIZATION`(+`PEER_WARMUP`) /
    `LOAD_BASED_ROAM` → **제거됨** (감사 D1 2026-07-31, `knob_audit_2026-07.md`)
  - `mlan0.roaming.enabled` = **true** (mlan0 로밍 기본 활성화), `mlan1.roaming.enabled` = false

### 2.4 제거/유령(phantom)

- `global.rate_adapt` — 데이터 없음(코드 fallback 경로만 존재).
- `logger.link_retry_count` / `logger.link_retry_delay_sec` — **JSON 키가 아님**. 스키마에 넣지 말 것(아래 ⚠️ 주의 박스 및 각주 참조).

---

## 3. 필드 스펙 (섹션별)

표 컬럼: 경로(key) | 라벨 | 타입 | 기본값 | 허용값/범위 | UI편집 | 적용시점 | 설명

> 인터페이스별 값이 다르면 기본값 칸에 `mlan0=X / mlan1=Y`로 분리 표기. 우선순위/override/capability-gate/히스테리시스 등 세부는 각 섹션 아래 **비고 각주**에 정리.

### 3.1 global (드라이버 초기화 전역)

| 경로 | 라벨 | 타입 | 기본값 | 허용값/범위 | UI편집 | 적용시점 | 설명 |
|---|---|---|---|---|---|---|---|
| `global.BOARD_TYPE` | 보드 타입 | enum | `imx93` | `imx8mm`\|`imx93` | caution | reboot | 보드/드라이버(.ko) 선택. imx93*→imx93 드라이버, 그 외→imx8 기본 |
| `global.BUS_TYPE` | 버스 타입 | enum | `sdio` | `sdio`\|`pcie` | caution | reboot | fw_name 자동선택·mod_para 블록 prefix(SD9098/PCIE9098) 결정 |
| `global.BLUETOOTH.enable` | 블루투스 콤보 FW | bool | `false` | true\|false | caution | reboot | BT combo 펌웨어 사용 여부(fw_name 자동선택에 반영) |
| `global.MOD_PARA` | 모듈 파라미터 파일 | string | `cts/wifi_mod_para.conf` | `/lib/firmware/` 기준 상대경로 | caution | reboot | moal insmod 인자 `mod_para=`로 전달. dev_cap/cal/mac/net_rx/fw_name 주입 대상 파일 |
| `global.CAL_DATA_CFG` | 캘리브레이션 데이터 파일(fallback) | string | `cts/WlanCalData_ext_RD.conf` | 상대경로, 빈값/`none`→`cal_data_cfg=none` | caution | reboot | `mlanN.CAL_DATA_CFG`가 우선, 비었을 때 fallback |
| `global.TXPWRLIMIT_PATH` | TX 파워 리밋 파일(fallback) | string | `/lib/firmware/cts/txpwrlimit_cfg_9098.conf` | 절대경로, `none`/빈값→skip | caution | boot | `mlanN.TXPWRLIMIT_PATH`가 우선. 부팅 시 mlanutl hostcmd로 적용 |
| `global.STANDARD` | WiFi 표준 제한(fallback) | enum | `""` | `""`\|`n`\|`ac`\|`ax` (또는 4\|5\|6). 빈값=칩 기본 | caution | reboot | dev_cap_mask로 변환되어 mod_para에 주입. `mlanN.STANDARD`가 우선 |
| `global.DEV_CAP_MASK` | dev_cap_mask raw(fallback) | string | `""` | raw hex(예: `0xfffcffff`), 빈값=미설정 | caution | reboot | 최하위 fallback: iface/global STANDARD가 모두 비어야 사용 |
| `global.ANT_TYPE` | 안테나 경로 | enum | `""` | `""`\|`internal`\|`external` (또는 0\|1). 빈값=설정 안 함 | caution | boot | 부팅 시 GPIO(SW_SEL1/2)로 적용. 런타임 변경은 `wifi ant` |
| `global.tx_work` | moal TX 제출 방식 | int | `0` | `0`\|`1` (빈값/형식위반=미전달) | caution | reboot | 0=동기 제출(홉1), 1=tx_workqueue 비동기(홉2, NXP iMX 기본). capability-gate |
| `global.ping_monitor.enabled` | ping 모니터 서비스 | bool | `false` | true\|false | yes | daemon-restart | `wifi_ping_monitor.service` enable/disable |

**비고 (global)** — 소비: 대부분 `wifi_init.sh`(부팅). `ping_monitor.enabled`는 `wifi_apply_enabled.sh`.
- `BUS_TYPE`/`BLUETOOTH.enable`/`BOARD_TYPE`는 하드웨어와 일치해야 함(fw_name·mod_para 블록·.ko 선택 결정). fw_name이 바뀌면 `wifi_config.py`가 `mod_para.conf`에 자동 기입.
- `tx_work`: .ko의 `.modinfo`에 `tx_work` param이 선언된 경우에만 insmod 인자로 전달(미선언 .ko면 skip해 붕괴 방지). 런타임 sysfs는 init 때 latch라 무효.
- `STANDARD`: n→`0xfffcdfff`, ac→`0xfffcffff`. native max 이상이면 dev_cap_mask 라인 삭제(제한 없음). mlan1의 ax는 미지원(경고).

### 3.2 mac (MAC 주소 설정)

| 경로 | 라벨 | 타입 | 기본값 | 허용값/범위 | UI편집 | 적용시점 | 설명 |
|---|---|---|---|---|---|---|---|
| `mac.mlan0.base` | mlan0 base MAC | string | `""` | `^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$`, 빈값=생략 | caution | boot | enable 무관 항상 반영 baseline (#110); bridge 활성+dynamic/static 성공 시에만 override |
| `mac.mlan0.target` | mlan0 target MAC | string | `""` | 위 MAC 포맷, 빈값=생략 | caution | reboot | static 모드 소스 + mod_para 블록 `mac_addr=`로 주입 |
| `mac.mlan1.base` | mlan1 base MAC | string | `""` | 위 MAC 포맷, 빈값=생략 | caution | boot | enable 무관 항상 반영 baseline (#110); bridge 활성+dynamic/static 성공 시에만 override |
| `mac.mlan1.target` | mlan1 target MAC | string | `""` | 위 MAC 포맷, 빈값=생략 | caution | reboot | static 모드 소스 + mod_para 블록 `mac_addr=`로 주입 |
| `mac.eth0.base` | eth0 base MAC | string | `""` | 위 MAC 포맷, 빈값=생략 | caution | boot | 유효 시 `update_mac.sh eth0`로 설정 |

**비고 (mac)** — 소비: `wifi_init.sh`, (target은 추가로) `wifi_mac_set.py`/`wifi_mac_save.py`.
- `base`는 mlan0/mlan1 모두 **enable 여부와 무관하게 항상 반영**(baseline, #110). **실제 bridge 활성**(`wbridge.enabled` & bridge iface enable) **+ dynamic/static 변환 성공 시에만** bridge 인터페이스 MAC을 base 대신 override.
- resolve_mac 우선순위(bridge iface): dynamic(`/tmp/eth0_client_mac`) → static이면 target → base. 형식 위반은 warn 로그 후 무시.
- 적용: `update_mac.sh`가 systemd `.link`의 `MACAddress=` 기록 → udev가 **netdev 생성(부팅/드라이버 리로드) 시에만** 적용(런타임 즉시 반영 아님). `.link` 부재/빈 파일이면 재생성, 변경 시 최대 5개 회전 백업(#111).

### 3.3 wbridge (WiFi Bridge)

#### 3.3.1 wbridge 기본

| 경로 | 라벨 | 타입 | 기본값 | 허용값/범위 | UI편집 | 적용시점 | 설명 |
|---|---|---|---|---|---|---|---|
| `wbridge.enabled` | 브릿지 전체 활성화 | bool | `true` | true\|false | yes | boot | false면 bridge 서비스 전부 stop+disable |
| `wbridge.bridge_iface` | 브릿지 무선 인터페이스 | enum | `mlan0` | `mlan0`\|`mlan1` | caution | boot | mlan1이면 moal `bridge_wlan_idx=1`. pcap 트랙은 mlan0 하드코딩 |
| `wbridge.mac_mode` | 브릿지 MAC 결정 방식 | enum | `dynamic` | `default`\|`dynamic`\|`static` | caution | boot | dynamic=유선측 MAC 동적 획득 |
| `wbridge.ip_discovery` | 클라이언트 IP 탐색 | bool | `false` | true\|false | yes | boot | dynamic MAC 확보 후 클라이언트 IP 탐색+host route(/32 dev eth0) 등록. false=MAC만 확보(부팅 가속) |
| `wbridge.eth_client_ip` | 유선 클라이언트 고정 IP | string | `""` | IPv4 또는 `""`(=비활성) | caution | boot | 빈값이면 quick ARP probe 비활성 |
| `wbridge.eth_link_wait_sec` | 유선 링크 대기(초) | int | `5` | 양의 정수 | yes | boot | dynamic MAC 모드에서 유선 링크 up 대기 |
| `wbridge.eth_sweep_subnet` | peer sweep 대역(CIDR) | string | `""` | CIDR(예: `192.168.1.0/24`) 또는 `""` | caution | boot | 빈값이면 eth0→mlan0 inet 순 폴백. 정적 CIDR 권장 |
| `wbridge.peer_route.enabled` | 양방향 peer 라우팅 마스터 | bool | `false` | true\|false | caution | boot | 옵션 X. false=기본 투명 브릿지(토폴로지 무관 안전). BD가 유선 peer와 직접 통신하는 mlan0-IP 토폴로지에서만 true(+ip_discovery=true, arp_ignore_always=false). **토폴로지=IP 배치는 `wifi <iface> ip`/webui로 별도 결정** |
| `wbridge.arp_ignore_always.enabled` | ARP 정책(토폴로지 종속) | bool | `false` | true\|false | caution | boot | `peer_route`와 독립. 클론 MAC 이중 ARP 레이스 차단(`arp_ignore=1`/`arp_announce=2`). eth0-IP/동일서브넷=true, 순수 mlan0-IP=false. **아래 주의** |
| `wbridge.eth_fallback.enabled` | 무선 down 시 eth0 절체 | bool | `false` | true\|false | caution | boot | mlan0 IP의 eth0 /32 미러 + fallback route(metric 200), 무선 복구 시 환원. mlan0-IP 토폴로지 전용(hairpin/dual 프로파일에 기본 포함) |
| `wbridge.engine` | 브릿지 엔진 | enum | `moal` | `pcap`\|`tpacket`\|`moal` | caution | daemon-restart | pcap/tpacket=유저스페이스, moal=드라이버 레벨. moal↔전환은 reboot |

**비고 (wbridge 기본)** — 소비: `wifi_bridge.sh`, `wifi_init.sh`, `wired_mac_ip_get.py`, `wifi_apply_enabled.sh`.
- SSoT는 이 JSON. JSON 파싱 실패 시에만 `/etc/default/wbridge`가 폴백.
- `ip_discovery`=true는 `peer_route.enabled`=true와 조합해야 양방향 라우팅 완성.
- `engine=moal`이면 `link_guard`는 무시된다.
- **⚠️ `arp_ignore_always.enabled`**: IP 배치(토폴로지)에 종속된 값. 출하 기본 **false**는 순수 mlan0-IP(기본 토폴로지) 전제 — eth0-IP(또는 eth0/mlan0 동일 서브넷) 구성에서는 true 로 변경. **토폴로지는 이 JSON이 아니라 `wifi <iface> ip`/webui로 결정**하므로 배치 변경 시 함께 점검. 순수 mlan0-IP + 유선↔BD 직접통신이 필요하면 `peer_route=true`+`ip_discovery=true`+`arp_ignore_always=false` 3종 세트. 아래 5장 주의 박스 참조.

#### 3.3.2 wbridge.moal (engine=moal 전용)

| 경로 | 라벨 | 타입 | 기본값 | 허용값/범위 | UI편집 | 적용시점 | 설명 |
|---|---|---|---|---|---|---|---|
| `wbridge.moal.keepalive_ms` | keepalive 주기(ms) | int | `1` | 음이 아닌 정수, 0=off | caution | reboot | 발열↔레이턴시 노브(클수록 발열↓/레이턴시↑) |
| `wbridge.moal.keepalive_idle_ms` | keepalive idle cutoff(ms) | int | `20` | 음이 아닌 정수, 0=free-running | caution | reboot | >0=idle 지속 시 타이머 자동 정지(발열↓) |
| `wbridge.moal.debug` | 브릿지 디버그 | int | `0` | `0`\|`1` | caution | reboot | BR_DBG/[DBG-RXDROP] dmesg 로그. 런타임도 sysfs로 가능 |
| `wbridge.moal.peer` | 유선 peer 인터페이스 | string | `""` | 인터페이스명(≤15자) 또는 `""` | caution | reboot | 빈값=드라이버 기본(eth0), insmod 인자 미전달 |
| `wbridge.moal.consume_link_local` | link-local 폐기 토글 | string | `""` | `0`\|`1` 또는 `""`(=기본 0) | caution | reboot | 차단된 link-local 프레임 드라이버 내 폐기(A/B 진단) |
| `wbridge.moal.deliver_rt_prio` | RX deliver RT 우선순위 | int | `45` | 0~99, 0=RT 미적용 | caution | reboot | threaded NAPI deliver leg 의 SCHED_FIFO 우선순위 |
| `wbridge.moal.local_hairpin` | 로컬발 hairpin | string | `0` | `0`\|`1` 또는 `""`(=드라이버 기본) | caution | reboot | 로컬발 TX(dst==클론 MAC) 유선 divert + ARP tee/inject(BD↔유선 peer). CLI(`br profile apply`)는 숫자로 기록 — 문자열·숫자 모두 유효 |

**비고 (moal)** — 소비: `wifi_init.sh`. `engine=moal`일 때만 insmod 인자로 전달.
- `keepalive_idle_ms`는 대상 .ko가 `bridge_keepalive_idle_ms` param을 선언한 경우에만 전달(현 `moal_imx8.ko` 등 미선언 드라이버엔 자동 미전달).
- `peer`/`consume_link_local`은 빈 문자열이면 insmod 인자 미전달(구버전 드라이버 호환 센티널).

#### 3.3.3 wbridge.optimize (커널 튜닝)

| 경로 | 라벨 | 타입 | 기본값 | 허용값/범위 | UI편집 | 적용시점 | 설명 |
|---|---|---|---|---|---|---|---|
| `wbridge.optimize.enabled` | 커널 튜닝 활성화 | bool | `true` | true\|false | yes | daemon-restart | false면 하위 무효, wbridge 바이너리 기본값만 |
| `wbridge.optimize.mode` | 최적화 프로파일 | enum | `normal` | `latency`\|`normal`\|`eco`\|`thermal` | yes | daemon-restart | latency=최소지연/최대발열, normal=균형(권장), eco=저전력, thermal=최소발열 |
| `wbridge.optimize.irq_affinity` | IRQ/RPS 핀 정책 | enum | `auto` | `auto`\|`pinned`\|`none` | caution | daemon-restart | auto=코어수 자동판단, pinned=ETH/WLAN 코어 고정 |
| `wbridge.optimize.profile_version` | 프로파일 스키마 버전 | int | `1` | 정수 | no | na | 로깅/메타데이터용(동작 영향 없음) |

**비고 (optimize)** — 소비: `wifi_bridge.sh`(→optimize-for-udp.sh, setup-irq-affinity.sh).
- thermal 상태(warm/hot)+`mode_force=0`이면 `mode`가 EFFECTIVE_MODE로 클램프될 수 있음.

#### 3.3.4 wbridge.link_guard (링크 상태 감시, engine=pcap|tpacket 전용)

| 경로 | 라벨 | 타입 | 기본값 | 허용값/범위 | UI편집 | 적용시점 | 설명 |
|---|---|---|---|---|---|---|---|
| `wbridge.link_guard.enabled` | 링크 가드 활성화 | bool | `true` | true\|false | yes | daemon-restart | false면 링크 상태 무관하게 wbridge 실행 |
| `wbridge.link_guard.link_down_debounce_sec` | 유선 down 디바운스(초) | int | `2` | 양의 정수 | yes | daemon-restart | 유선 down 감지 후 bridge 중지까지 대기 |
| `wbridge.link_guard.link_up_stable_sec` | 유선 up 안정 확인(초) | int | `2` | 양의 정수 | yes | daemon-restart | up이 이 시간 유지되면 bridge 시작 |
| `wbridge.link_guard.link_idle_poll_sec` | 링크 미준비 폴링(초) | int | `5` | 양의 정수 | yes | daemon-restart | 링크 미준비 시 폴링 주기 |
| `wbridge.link_guard.wait_ready_timeout_sec` | 인터페이스 준비 대기(초) | int | `10` | 양의 정수 | yes | daemon-restart | 초과 시 guard 없이 진행 |
| `wbridge.link_guard.wlan_roam_grace_sec` | 무선 down 로밍 유예(초) | int | `15` | 양의 정수 | yes | daemon-restart | 무선 down 시 로밍 복구 대기 |
| `wbridge.link_guard.wlan_down_restart` | 무선 down 시 재시작 | bool | `false` | true\|false | yes | daemon-restart | true면 roam grace 초과 시 bridge 재시작 |

**비고 (link_guard)** — 소비: `wifi_bridge.sh`. `engine=moal`이면 전체 무시.

#### 3.3.5 wbridge.thermal (Thermal 상태 관리)

| 경로 | 라벨 | 타입 | 기본값 | 허용값/범위 | UI편집 | 적용시점 | 설명 |
|---|---|---|---|---|---|---|---|
| `wbridge.thermal.enabled` | thermal 모니터링 활성화 | bool | `false` | true\|false | yes | boot | false면 상태 갱신 정지→thermal clamp 사실상 비활성 |
| `wbridge.thermal.mode_force` | thermal 반응 억제 | bool | `true` | true\|false | caution | daemon-restart | true면 클램핑 무시(hot에서도 요청 모드 강제)+재시작 skip |
| `wbridge.thermal.auto_restart` | thermal 자동 재시작 | bool | `false` | true\|false | yes | runtime | 상태 변경 시 bridge 자동 재시작 여부 |
| `wbridge.thermal.restart_cooldown_sec` | 재시작 쿨다운(초) | int | `60` | 양의 정수 | yes | runtime | 재시작 루프 방지 쿨다운 |
| `wbridge.thermal.bridge_units` | 재시작 대상 유닛 | string | `wifi_bridge@mlan0.service wifi_bridge@mlan1.service` | 유닛명 공백 구분 | caution | runtime | 상태 변경 시 재시작할 systemd 유닛 목록 |
| `wbridge.thermal.thresholds.warm_cpu_enter` | CPU warm 진입(°C) | int | `90` | 정수 °C (enter>exit, 5도 갭) | caution | runtime | CPU warm 진입 임계 |
| `wbridge.thermal.thresholds.hot_cpu_enter` | CPU hot 진입(°C) | int | `95` | 정수 °C | caution | runtime | CPU hot 진입 임계 |
| `wbridge.thermal.thresholds.warm_cpu_exit` | CPU warm 해제(°C) | int | `85` | 정수 °C | caution | runtime | CPU warm 해제 임계 |
| `wbridge.thermal.thresholds.hot_cpu_exit` | CPU hot 해제(°C) | int | `90` | 정수 °C | caution | runtime | CPU hot 해제 임계 |
| `wbridge.thermal.thresholds.warm_wifi_enter` | WiFi warm 진입(°C) | int | `85` | 정수 °C | caution | runtime | WiFi(mlan) warm 진입 임계 |
| `wbridge.thermal.thresholds.hot_wifi_enter` | WiFi hot 진입(°C) | int | `90` | 정수 °C | caution | runtime | WiFi(mlan) hot 진입 임계 |
| `wbridge.thermal.thresholds.warm_wifi_exit` | WiFi warm 해제(°C) | int | `80` | 정수 °C | caution | runtime | WiFi(mlan) warm 해제 임계 |
| `wbridge.thermal.thresholds.hot_wifi_exit` | WiFi hot 해제(°C) | int | `85` | 정수 °C | caution | runtime | WiFi(mlan) hot 해제 임계 |

**비고 (thermal)** — 소비: `wifi_thermal_state_update.sh`(매 틱 JSON 재파싱→runtime), `wifi_bridge.sh`(mode_force 클램프 바이패스는 서비스 재시작), `wifi_apply_enabled.sh`(`thermal.enabled`→timer enable/disable).
- 히스테리시스: enter/exit 사이 5도 갭. `mode_force`는 발열 안전 클램프를 무력화하므로 편집 신중.
- `thermal.enabled=false`(출하 기본)면 thresholds/bridge_units 등 하위값은 채워져 있어도 실질 무효(dead config).

### 3.4 temperature (wifi_logger_temp.sh 온도 임계)

| 경로 | 라벨 | 타입 | 기본값 | 허용값/범위 | UI편집 | 적용시점 | 설명 |
|---|---|---|---|---|---|---|---|
| `temperature.emerg_cpu` | CPU 긴급 온도 | int | `100` | °C 정수 | caution | daemon-restart | 이상이면 emerg 카운트↑, 임계 초과 시 wifi/bridge 정지+강제 재부팅 |
| `temperature.crit_cpu` | CPU 위험 온도 | int | `95` | °C 정수 | caution | daemon-restart | crit 로그(동작 없음). recover_cpu 미지정 시 복구 임계 fallback |
| `temperature.error_cpu` | CPU 에러 온도 | int | `90` | °C 정수 | caution | daemon-restart | err 로그(로깅만) |
| `temperature.warn_cpu` | CPU 경고 온도 | int | `85` | °C 정수 | caution | daemon-restart | warn 로그(로깅만) |
| `temperature.emerg_mlan` | mlan 긴급 온도 | int | `95` | °C 정수 | caution | daemon-restart | JSON에 존재하나 emerg 분기는 CPU만 사용(mlan은 crit/err/warn만 참조) |
| `temperature.crit_mlan` | mlan 위험 온도 | int | `90` | °C 정수 | caution | daemon-restart | crit 로그. recover_mlan 미지정 시 복구 임계 fallback |
| `temperature.error_mlan` | mlan 에러 온도 | int | `85` | °C 정수 | caution | daemon-restart | err 로그(로깅만) |
| `temperature.warn_mlan` | mlan 경고 온도 | int | `80` | °C 정수 | caution | daemon-restart | warn 로그(로깅만) |
| `temperature.cooldown_sec` | 과열 쿨다운(초) | int | `60` | 초 정수 | caution | daemon-restart | 과열 정지 후 대기 시간. 재부팅 루프에 직접 영향 |
| `temperature.recover_cpu` | CPU 복구 온도 | int | `90` | °C 정수 | caution | daemon-restart | 쿨다운 후 이 값 미만이면 복구 판정 후 강제 재부팅 |
| `temperature.recover_mlan` | mlan 복구 온도 | int | `85` | °C 정수 | caution | daemon-restart | 쿨다운 후 mlan 모두 이 값 미만이면 복구 판정 |
| `temperature.check_interval_sec` | 온도 점검 주기(초) | int | `5` | 양의 정수 | yes | daemon-restart | 온도 폴링/로그 주기. 클수록 감지 지연 |
| `temperature.emerg_count_threshold` | 긴급 연속 카운트 임계 | int | `2` | 정수 | caution | daemon-restart | 엄격 초과 비교(기본 2면 3회째 발동). 낮추면 재부팅 쉽게 트리거 |

**비고 (temperature)** — 소비: `wifi_logger_temp.sh`. 환경변수 `RECOVER_CPU_TEMP`/`RECOVER_MLAN_TEMP`/`WIFI_STOP_UNITS`가 우선. emerg 발동 시 `wlan_reboot_policy.sh --force`로 재부팅되므로 신중.

### 3.5 mmc (eMMC 헬스)

| 경로 | 라벨 | 타입 | 기본값 | 허용값/범위 | UI편집 | 적용시점 | 설명 |
|---|---|---|---|---|---|---|---|
| `mmc.check_interval_sec` | eMMC 점검 주기(초) | int | `300` | 양의 정수 | yes | daemon-restart | eMMC 수명(PRE_EOL/LifeTime) 점검·로그 주기(로깅만) |

**비고 (mmc)** — 소비: `wifi_logger_mmc.sh`. BOARD_TYPE에 따라 ext_csd 경로 자동 분기.

### 3.6 mcp (전류/전압 센서)

| 경로 | 라벨 | 타입 | 기본값 | 허용값/범위 | UI편집 | 적용시점 | 설명 |
|---|---|---|---|---|---|---|---|
| `mcp.gain_current` | 전류 채널 게인 | float | `0.5203` | 실수(HW 캘리브레이션) | caution | daemon-restart | iio CH0 전류(A) 환산 게인 |
| `mcp.gain_voltage` | 전압 채널 게인 | float | `15.6552` | 실수(HW 캘리브레이션) | caution | daemon-restart | iio CH1 전압(V) 환산 게인. 5V/24V 자동 판별에 사용 |
| `mcp.check_interval_sec` | 전류/전압 점검 주기(초) | int | `5` | 양의 정수 | yes | daemon-restart | 센서 폴링/로그 주기 |
| `mcp.max_probe_fail` | probe 연속 실패 한도 | int | `12` | >=1 | yes | daemon-restart | ADC probe 연속 실패 초과 시 데몬 종료(센서 비장착 보드 무한 재시도 방지) |
| `mcp.iio_device` | iio 디바이스 경로 | string | (JSON 미존재; 자동설정) | sysfs 경로 | no | boot | iMX8MM=device0, iMX93=device1. wifi_init.sh가 자동 주입 — **편집 금지** |
| `mcp.system_5v.warn_a` | 5V계 경고 전류(A) | float | `1` | A 실수 | yes | daemon-restart | 이상이면 warn 로그(분류만) |
| `mcp.system_5v.error_a` | 5V계 에러 전류(A) | float | `1.5` | A 실수 | yes | daemon-restart | 이상이면 err 로그 |
| `mcp.system_5v.crit_a` | 5V계 위험 전류(A) | float | `2` | A 실수 | yes | daemon-restart | 이상이면 crit 로그 |
| `mcp.system_5v.emerg_a` | 5V계 긴급 전류(A) | float | `2.5` | A 실수 | yes | daemon-restart | 이상이면 emerg 로그(동작 없음) |
| `mcp.system_24v.warn_a` | 24V계 경고 전류(A) | float | `0.2` | A 실수 | yes | daemon-restart | 이상이면 warn 로그 |
| `mcp.system_24v.error_a` | 24V계 에러 전류(A) | float | `0.3` | A 실수 | yes | daemon-restart | 이상이면 err 로그 |
| `mcp.system_24v.crit_a` | 24V계 위험 전류(A) | float | `0.4` | A 실수 | yes | daemon-restart | 이상이면 crit 로그 |
| `mcp.system_24v.emerg_a` | 24V계 긴급 전류(A) | float | `0.5` | A 실수 | yes | daemon-restart | 이상이면 emerg 로그(동작 없음) |

**비고 (mcp)** — 소비: `wifi_logger_mcp.sh`. `gain_*`는 HW 캘리브레이션 상수라 임의 변경 금지(오설정 시 전류 오판정·임계셋 오선택). system_*는 로그 레벨 분류만(차단/재부팅 없음). `iio_device`는 JSON 기본엔 없고 자동 주입되므로 UI에 노출하지 않음(읽기전용).

### 3.7 monitor (wifi_link_monitor.py 표시 설정)

| 경로 | 라벨 | 타입 | 기본값 | 허용값/범위 | UI편집 | 적용시점 | 설명 |
|---|---|---|---|---|---|---|---|
| `monitor.interval_sec` | 모니터 갱신 주기(초) | float | `1` | 양의 실수 | yes | daemon-restart | 화면 갱신 주기 |
| `monitor.summary_lines` | 요약 표시 줄수 | int | `5` | 양의 정수 | yes | daemon-restart | summary.log 최근 줄 수 |
| `monitor.ping_lines` | ping 표시 줄수 | int | `5` | 양의 정수 | yes | daemon-restart | ping 로그 최근 줄 수 |
| `monitor.roam_display_sec` | 로밍 표시 유지(초) | int | `5` | 정수 | yes | daemon-restart | 로밍 이벤트 강조 유지 시간 |

**비고 (monitor)** — 소비: `wifi_link_monitor.py`. **CLI 인자가 JSON보다 우선**. 온디맨드 curses 도구라 다음 실행 시 반영.

### 3.8 logger (전역 로거 기본값)

| 경로 | 라벨 | 타입 | 기본값 | 허용값/범위 | UI편집 | 적용시점 | 설명 |
|---|---|---|---|---|---|---|---|
| `logger.cpu_interval_sec` | CPU 로그 주기(전역) | int | `60` | 양의 정수 | yes | daemon-restart | CPU/메모리/클럭 로그 주기(per-iface override 없음) |
| `logger.link_interval_sec` | 링크 폴링 주기(전역) | float | `0.95` | 양의 실수 | yes | daemon-restart | 링크 상태 폴링 주기. eth0/mlanN.logger가 override |
| `logger.stat_log_interval_sec` | 통계 로그 주기(전역) | int | `1` | 양의 정수 | yes | daemon-restart | 통계 기록 주기 |
| `logger.stat_check_interval_sec` | 통계 체크 주기(전역) | int | `1` | 양의 정수 | yes | daemon-restart | 통계 수집 루프 체크 주기 |
| `logger.stat_reset_interval_sec` | 통계 리셋 주기(전역) | int | `604800` | 정수(기본 7일) | yes | daemon-restart | AP별 누적 통계 리셋 주기 |
| `logger.bgscan_stale_threshold_sec` | bgscan stale 임계 | int | `600` | 정수 | yes | daemon-restart | 스캔 결과 stale 판정 임계(global-only) |

**비고 (logger)** — 소비: `wifi_logger_cpu.sh`/`wifi_logger_link.py`/`wifi_logger_stat.py`/`wifi_bgscan.py`.
- 우선순위: `{iface}.logger.*` > `logger.*` (단 `cpu_interval_sec`·`bgscan_stale_threshold_sec`는 global-only).
- **① 유령 키 주의**: `logger.link_retry_count` / `logger.link_retry_delay_sec`는 **JSON 키가 아니다.** 스키마에 넣지 말 것. `wifi_logger_link.py`의 모듈/CLI 기본값(각각 `4` / `0.05`)이며, 필요 시 CLI `--link-retry-count` / `--link-retry-delay`로만 조정한다.

### 3.9 eth0 (eth0 인터페이스)

| 경로 | 라벨 | 타입 | 기본값 | 허용값/범위 | UI편집 | 적용시점 | 설명 |
|---|---|---|---|---|---|---|---|
| `eth0.logger.link_interval_sec` | eth0 링크 폴링 주기(초) | int | `1` | 양의 실수/정수 | yes | daemon-restart | 전역 0.95를 eth0에 한해 1로 override |

**비고 (eth0)** — 소비: `wifi_logger_link.py`. `wifi_logger@eth0` 재시작 시 반영.

### 3.10 mlan0 / mlan1 (무선 인터페이스)

> 아래 표의 기본값이 인터페이스별로 다르면 `mlan0=X / mlan1=Y`로 표기한다. 표기가 단일값이면 두 인터페이스 동일.

#### 3.10.1 인터페이스 기본 + radio

| 경로(`mlanN.`) | 라벨 | 타입 | 기본값 (mlan0 / mlan1) | 허용값/범위 | UI편집 | 적용시점 | 설명 |
|---|---|---|---|---|---|---|---|
| `STANDARD` | WiFi 표준 제한 | enum | `mlan0="ax" / mlan1="ac"` | `n`\|`ac`\|`ax` (또는 4\|5\|6), 빈값=칩 기본. mlan1은 ax 미지원 | caution | reboot | dev_cap_mask로 변환. mlanN>global 우선 |
| `CAL_DATA_CFG` | 캘리브레이션 파일(per-iface) | string | `""` | 경로\|`none`\|빈값 | caution | reboot | mlanN>global 우선. 빈값/none→`cal_data_cfg=none` |
| `TXPWRLIMIT_PATH` | TX 파워 리밋(per-iface) | string | `""` | 절대경로\|`none`\|빈값 | caution | boot | mlanN>global 우선. 부팅 시 mlanutl hostcmd |
| `connect_threshold` | 연결 임계값(RSSI) | int | `-100` | 음의 정수 dBm(예 -100~-40). -100=사실상 무필터 | caution | daemon-restart | **커스텀 wpa_supplicant 바이너리**가 `/usr/local/etc/wifi_init_conf.json`을 직접 읽어, 신호레벨이 이 값 미만인 BSS를 연결 후보에서 제외(로그 `BSS: … level N < connect threshold M`). 과설정 시 미연결 위험 |
| `enabled` | 인터페이스 활성화 | bool | `mlan0=true / mlan1=false` | true\|false | yes | boot | false면 부팅 초기화·모든 자식 데몬 disable. **overlay config.json이 우선** |
| `Frequency` | 주파수 대역 | enum | `auto` | `auto`\|`2.4GHz`\|`5GHz` (검증 없음) | caution | boot | 현재는 로그/상태표시에만 사용(HW 밴드제한 미적용). **overlay config.json이 우선** |
| `net_rx` | MGMT 프레임 로깅 비트맵 | int | `0` | `0`\|`2`\|`3`\|`6`\|`7` (bit[1:0]=RX모드, bit[2]=TX로그) | caution | reboot | mod_para 블록 `net_rx=`. 로그는 커널 링버퍼→10초마다 flush |
| `mgmt_hex_dump_enable` | MGMT hex dump 로깅 | bool | `false` | true\|false | caution | reboot | 디버그용. mod_para 블록 `mgmt_hex_dump=1/0` |
| `thermal_mgmt` | FW 열관리 | bool | `true` | true\|false | caution | boot | FW thermal management(SUBID 0x113). 명시적 false만 disable |
| `rate_adapt.mode` | 레이트 적응 모드 | int | `1` | `0`=legacy\|`1`=SR | caution | boot | 비었으면 rate_adapt 블록 전체 skip |
| `rate_adapt.low_thresh` | 레이트 적응 low 임계 | int | `50` | 0..100(%) 또는 255(0xff=dynamic) | caution | boot | SR 모드 하한 성공률(%) |
| `rate_adapt.high_thresh` | 레이트 적응 high 임계 | int | `80` | 0..100(%) 또는 255(0xff) | caution | boot | SR 모드 상한 성공률(%) |
| `rate_adapt.interval_ms` | 레이트 적응 평가주기(ms) | int | `100` | ms 정수 | caution | boot | 평가 주기 |

#### 3.10.2 mlanN.logger (per-iface 로거)

| 경로(`mlanN.logger.`) | 라벨 | 타입 | 기본값 (mlan0 / mlan1) | 허용값/범위 | UI편집 | 적용시점 | 설명 |
|---|---|---|---|---|---|---|---|
| `link_interval_sec` | 링크 로거 주기 | float | `0.95` | 초(>0) | yes | daemon-restart | mlanN>global 우선 |
| `stat_log_interval_sec` | 통계 로그 기록 주기 | int | `1` | 초(>0) | yes | daemon-restart | mlanN>global 우선 |
| `stat_check_interval_sec` | 통계 체크 주기 | int | `1` | 초(>0) | yes | daemon-restart | mlanN>global 우선 |
| `stat_reset_interval_sec` | 통계 리셋 주기 | int | `604800` | 초(7일) | yes | daemon-restart | mlanN>global 우선 |
| `logger.enabled` | 로거 데몬 활성화 | bool | `mlan0=true / mlan1=false` | true\|false | yes | daemon-restart | `wifi_logger@mlanN` enable/disable. `mlanN.enabled=false`면 강제 disable |

#### 3.10.3 mlanN.periodic_roam / bgscan

| 경로(`mlanN.`) | 라벨 | 타입 | 기본값 (mlan0 / mlan1) | 허용값/범위 | UI편집 | 적용시점 | 설명 |
|---|---|---|---|---|---|---|---|
| `periodic_roam.interval` | 주기적 패시브 로밍 주기 | int | `60` | >=10 (10 미만은 10으로 강제) | yes | daemon-restart | passive roam 실행 주기 |
| `periodic_roam.scan_before_roam` | 로밍 전 스캔 | bool | `true` | true\|false | yes | daemon-restart | true=스캔 후 판단, false=기존 ap.log 사용 |
| `periodic_roam.enabled` | 주기적 로밍 데몬 | bool | `false` | true\|false | yes | daemon-restart | `wifi_periodic_roam@mlanN` enable/disable |
| `bgscan.interval` | 백그라운드 스캔 주기 | int | `60` | >0 | yes | runtime | 매 스캔 직전 재로드(무재시작) |
| `bgscan.ssid_filter` | SSID directed probe | bool | `true` | true\|false | yes | runtime | false면 광범위 undirected 스캔 |
| `bgscan.freq_filter` | 주파수 필터 | bool | `true` | true\|false | yes | runtime | false면 전 대역 스캔(airtime↑) |
| `bgscan.passive` | 패시브 스캔 | bool | `true` | true\|false | yes | runtime | true=probe 미송신(beacon 수신만, 공유매체 probe airtime 0). hidden SSID 미발견 — 판단 기준은 로밍 가이드 §2.3 |
| `bgscan.emit_roam_hint` | roam hint 발행 | bool | `true` | true\|false | yes | runtime | 스캔 성공 시 hint touch → wifi_roam backoff 조기 해제. **단일 iface 에서는 실효 없음**(스키마 주의 참조) |
| `bgscan.enabled` | 백그라운드 스캔 데몬 | bool | `mlan0=true / mlan1=false` | true\|false | yes | daemon-restart | `wifi_bgscan@mlanN` enable/disable |

#### 3.10.4 mlanN.roaming (wifi_roam.py 로밍)

| 경로(`mlanN.roaming.`) | 라벨 | 타입 | 기본값 (mlan0 / mlan1) | 허용값/범위 | UI편집 | 적용시점 | 설명 |
|---|---|---|---|---|---|---|---|
| `use_signal_avg` | 평균 신호 사용 | bool | `true` | true\|false | yes | daemon-restart | true=평균(안정), false=순간값 |
| `DEFAULT_TH_2G` | 2.4GHz 로밍 임계값 | int | `-75` | 음수 dBm | yes | daemon-restart | 이 값 이하이면 로밍 시도 (JSON 단일 소스, conf `#!TH_2G=` 마커 미사용) |
| `DEFAULT_TH_5G` | 5GHz 로밍 임계값 | int | `-75` | 음수 dBm | yes | daemon-restart | 이 값 이하이면 로밍 시도 (JSON 단일 소스, conf `#!TH_5G=` 마커 미사용) |
| `DIFF_TH` | 후보 AP 최소 RSSI 차 | int | `8` | >=0 dB | yes | daemon-restart | 클수록 보수적 |
| `CHECK_INTERVAL` | 로밍 체크 주기 | int | `mlan0=2 / mlan1=3` | >=1 초 | yes | daemon-restart | 고정 체크 주기(ADAPTIVE_INTERVAL 은 감사 D1로 제거됨) |
| `extra_ssids` | 추가 로밍 후보 SSID | array | `[]` | 문자열 배열(같은 psk/key_mgmt) | caution | daemon-restart | 모드B(generate_network_blocks=false)면 강제 무시 |
| `generate_network_blocks` | 모드 결정자 | bool | `false` | true\|false | caution | daemon-restart | false=모드B(단일 블록), true=모드A(다중+select_network) |
| `ROAM_CROSS_FAIL_RETRY_COUNT` | cross-SSID 재시도 횟수 | int | `2` | >=0 (모드A 전용) | yes | daemon-restart | 초과 시 지수 backoff로 후보 제외 |
| `ROAM_NO_RESULT_FAST_COUNT` | 후보없음 고속 재시도 횟수 | int | `3` | >=1 | yes | daemon-restart | 처음 N회는 backoff 없이 `SCAN_NO_RESULT_SLEEP` 주기 유지, 초과분부터 지수 backoff |
| `SCAN_NO_RESULT_SLEEP` | 스캔 무결과 대기 | int | `3` | >=1 초 | yes | daemon-restart | 지수 backoff 시작값 |
| `ROAM_SUCCESS_SLEEP` | 로밍 성공 후 대기 | int | `mlan0=3 / mlan1=2` | >=1 초 | yes | daemon-restart | 성공 후 재체크 대기 |
| `enabled` | 로밍 데몬 활성화 | bool | `mlan0=true / mlan1=false` | true\|false | yes | daemon-restart | `wifi_roam@mlanN` enable/disable |
| `PREDICTIVE_ROAM.enable` | 예측 로밍 | bool | `false` | true\|false | yes | daemon-restart | RSSI 하락 추세 시 조기 로밍 |
| `PREDICTIVE_ROAM.threshold_boost` | 예측 임계 부스트 | int | `5` | >=0 dB | yes | daemon-restart | 하락 추세 시 임계값에 더하는 부스트 |
| `PREDICTIVE_ROAM.trend_window_size` | 추세 샘플 수 | int | `5` | >=1 | yes | daemon-restart | 추세 계산 RSSI 샘플 수 |
| `PREDICTIVE_ROAM.trend_history_max_age` | 추세 샘플 최대 수명 | int | `30` | >=1 초 | yes | daemon-restart | 이 시간 지난 샘플 폐기 |

> `LOAD_BASED_ROAM`·`ADAPTIVE_INTERVAL`·`POST_ROAM_ARP_OPTIMIZATION`(+`PEER_WARMUP`) 키는 감사 D1(2026-07-31)로 제거됨 — WebUI 에 노출하지 말 것.
| `PING_PONG_PREVENTION.enable` | 핑퐁 방지 | bool | `true` | true\|false | yes | daemon-restart | AP 간 반복 로밍 방지 |
| `PING_PONG_PREVENTION.window` | 핑퐁 감시 구간 | int | `20` | >=1 초 | yes | daemon-restart | 로밍 횟수 감시 구간 |
| `PING_PONG_PREVENTION.max_roams_in_window` | 구간 내 최대 로밍 | int | `3` | >=1 | yes | daemon-restart | 초과 시 detection_time 동안 억제 |
| `PING_PONG_PREVENTION.detection_time` | 핑퐁 억제 시간 | int | `5` | >=1 초 | yes | daemon-restart | 감지 후 로밍 억제 시간 |
| `STAGED_SCAN.enable` | 단계형 스캔 | bool | `true` | true\|false | yes | runtime | 홈채널→캐시→전채널 3단계 스캔(로밍 가이드 §1). false=종전 단일 액티브 스캔 |
| `STAGED_SCAN.skip_redundant_active` | Stage3 중복 스킵 | bool | `true` | true\|false | yes | runtime | 단일채널+홈스캔 성공 시 전채널 액티브 생략(스킵 조건 3개 AND, 가이드 §1) |
| `STAGED_SCAN.home_passive` | Stage1 홈채널 패시브 | bool | `true` | true\|false | yes | runtime | false=directed 액티브(홈채널 hidden 타깃용, 가이드 §2) |
| `STAGED_SCAN.cache_fresh_sec` | Stage2 캐시 신선도 | int | `70` | >=1 초 | yes | runtime | ap.log 캐시 블록 유효 시간. `bgscan.interval`+여유 유지(가이드 §5 함정 #1) |
| `STAGED_SCAN.self_induced_tail_sec` | 자기유발 블록 제외 여유 | int | `10` | >=1 초 | yes | runtime | 내 로밍 스캔이 남긴 ap.log 블록을 Stage2 판정에서 제외하는 시간 여유 |
| `GOOD_SIGNAL_RESET_GATE.enable` | good-signal 리셋 게이트 | bool | `false` | true\|false | yes | runtime | 정체 시 backoff streak 유지로 스캔 폭증 차단(PR #138, 현장 A/B 대기). CLI `wifi <n> roam gate` |
| `GOOD_SIGNAL_RESET_GATE.delta_db` | 게이트 이동 판정 임계 | int | `2` | >=1 dB | yes | runtime | 직전 리셋 시점 대비 \|Δrssi\| 가 이 값 이상이면 리셋 허용 |
| `GOOD_SIGNAL_RESET_GATE.post_roam_grace_sec` | 결합 후 유예 | int | `40` | >=1 초 | yes | runtime | attach ramp(결합 직후 RSSI 하강)를 이동으로 오독하지 않는 유예 |

**비고 (roaming)** — 소비: `wifi_roam.py`(데몬 시작 시 1회 로드 → daemon-restart), `enabled`류는 `wifi_apply_enabled.sh`.
- `mlanN.enabled=false`면 하위 로밍/스캔/logger/checker/arping 데몬은 상위 게이트로 강제 disable.
- `extra_ssids`는 같은 psk/key_mgmt 전제(오설정 시 인증 실패). `generate_network_blocks=true`(모드A)는 wpa_supplicant.conf에 다중 network 블록을 생성하므로 기존 연결 동작이 바뀔 수 있음.

#### 3.10.5 mlanN.mcs_tier

| 경로(`mlanN.mcs_tier.`) | 라벨 | 타입 | 기본값 (mlan0 / mlan1) | 허용값/범위 | UI편집 | 적용시점 | 설명 |
|---|---|---|---|---|---|---|---|
| `enabled` | MCS tier 제한 활성화 | bool | `false` | true\|false | caution | boot | 부팅 시 + 매 연결 이벤트마다 재적용 |
| `ht` | MCS HT tier 값 | string | `mlan0="7" / mlan1=""` | 자유 문자열(예 `"7"`,`"15"`), 빈값=skip | caution | boot | `mlanutl mcstiercfg ht <값>` verbatim |
| `vht` | MCS VHT tier 값 | string | `mlan0="7" / mlan1=""` | 자유 문자열, 빈값=skip | caution | boot | vht prefix verbatim |
| `he` | MCS HE tier 값 | string | `mlan0="both 7" / mlan1=""` | 자유 문자열(예 `"both 7"`), 빈값=skip | caution | boot | he prefix verbatim(공백 포함 문자열 그대로) |

**비고 (mcs_tier)** — 소비: `wifi_init.sh`(부팅), `wifi_event.sh`(매 connected 재적용). 타입은 **문자열**이며 빈 문자열은 "해당 prefix skip" 센티널. `enabled=true`인데 ht/vht/he 전부 비면 no-op. `on_connect` 또는 `mcs_tier` 중 하나라도 true면 `wifi_event@mlanN` 데몬 enable.

#### 3.10.6 mlanN.on_connect

| 경로(`mlanN.on_connect.`) | 라벨 | 타입 | 기본값 (mlan0 / mlan1) | 허용값/범위 | UI편집 | 적용시점 | 설명 |
|---|---|---|---|---|---|---|---|
| `enabled` | 연결 후 명령 실행 활성화 | bool | `mlan0=true / mlan1=false` | true\|false | yes | runtime | AP 연결/로밍 후 commands 실행 여부 |
| `commands` | 연결 후 실행 명령 목록 | array | `[]` | shell 명령 문자열 배열 | caution | runtime | 순서대로 실행(실패는 로그만). **command injection 위험** |

**비고 (on_connect)** — 소비: `wifi_event.sh`(매 connected 이벤트마다 JSON 재읽기). JSON이 group/world-writable이면 `on_connect` 무력화(권한 가드). UI 편집 시 명령 내용 검증 필요.

#### 3.10.7 mlanN.checker (연결 감시·복구·재부팅 정책)

| 경로(`mlanN.checker.`) | 라벨 | 타입 | 기본값 (mlan0 / mlan1) | 허용값/범위 | UI편집 | 적용시점 | 설명 |
|---|---|---|---|---|---|---|---|
| `LIMIT_CNT` | F/W 부재 감내 카운트 | int | `5` | 양의 정수 | caution | daemon-restart | 초과 시 재부팅 정책 트리거 |
| `MAX_UNSTABLE_DURATION` | 불안정 감내 시간(초) | int | `10` | 양의 정수 | yes | daemon-restart | 초과 시 reassociate, 3배 초과 시 wpa 재시작 |
| `MAX_REBOOT_COUNT` | 재부팅 루프 상한 | int | `3` | 양의 정수 | caution | daemon-restart | 쿨다운 창 내 재부팅 초과 시 거부(exit 11) |
| `REBOOT_COOLDOWN_SEC` | 재부팅 쿨다운(초) | int | `300` | 양의 정수 | caution | daemon-restart | 이 창 내 재시도는 루프 카운트↑ |
| `MIN_UPTIME_SEC` | 최소 부팅 후 시간(초) | int | `30` | 0 이상 정수 | caution | daemon-restart | 미만이면 재부팅 거부(exit 10). 커널 uptime 기준 |
| `FAULT_REASSOC_CNT` | 결함 reassoc 임계 | int | `2` | 양의 정수(<RESTART) | yes | daemon-restart | station dump 결함 사다리 1단계 |
| `FAULT_RESTART_CNT` | 결함 재시작 임계 | int | `4` | 양의 정수(<REBOOT) | yes | daemon-restart | 사다리 2단계(wpa 재시작) |
| `FAULT_REBOOT_CNT` | 결함 재부팅 임계 | int | `6` | 양의 정수 | caution | daemon-restart | 사다리 최종 단계(재부팅) |
| `RECONFIGURE_GRACE_SEC` | reconfigure 유예(초) | int | `20` | 양의 정수 | yes | daemon-restart | conf 재로드 직후 불안정 사다리 억제 |
| `enabled` | checker 데몬 활성화 | bool | `mlan0=true / mlan1=false` | true\|false | yes | boot | `wifi_checker@mlanN` enable/disable |

**비고 (checker)** — 소비: `wifi_checker.sh`, `MAX_REBOOT_COUNT`/`REBOOT_COOLDOWN_SEC`/`MIN_UPTIME_SEC`는 `wlan_reboot_policy.sh`(env로 전달), `enabled`는 `wifi_apply_enabled.sh`. 재부팅 정책에 직접 영향하는 항목은 caution.

#### 3.10.8 mlanN.arping (게이트웨이 도달성 감시)

| 경로(`mlanN.arping.`) | 라벨 | 타입 | 기본값 (mlan0 / mlan1) | 허용값/범위 | UI편집 | 적용시점 | 설명 |
|---|---|---|---|---|---|---|---|
| `threshold` | ARP 무응답 임계 | int | `10` | 양의 정수 | yes | daemon-restart | 연속 무응답 도달 시 임계 처리(현재 로그만) |
| `cooldown_sec` | 임계 도달 후 쿨다운(초) | int | `10` | 양의 정수 | yes | daemon-restart | 다음 시도 전 대기 |
| `loop_delay_sec` | 메인 루프 주기(초) | int | `10` | 양의 정수 | yes | daemon-restart | 성공 시/대기 폴링 주기 |
| `timeout_sec` | arping 응답 타임아웃(초) | int | `3` | 양의 정수 | yes | daemon-restart | 단건 arping `-w` 값 |
| `sweep_timeout_sec` | sweep arping 타임아웃(초) | int | `1` | 양의 정수 | yes | daemon-restart | 서브넷 스윕 개별 대상 타임아웃 |
| `sweep_parallel_limit` | sweep 병렬 상한 | int | `50` | 양의 정수 | yes | daemon-restart | 동시 arping 백그라운드 프로세스 최대 |
| `enabled` | arping 데몬 활성화 | bool | `false` | true\|false | yes | boot | `wifi_arping@mlanN` enable/disable |

**비고 (arping)** — 소비: `wifi_arping.sh`, sweep은 `wifi_arping_sweep.sh`, `enabled`는 `wifi_apply_enabled.sh`.
- (참고) 코드가 JSON 값을 무조건 대입하므로 `THRESHOLD`/`LOOPDELAY` 등 **환경변수 override는 사실상 무시**되고 JSON이 우선한다(구 주석의 "하위 호환"은 stale).
- `wifi_arping_sweep.sh`는 CIDR을 항상 mlan0 기준으로 산출하므로, mlan1에서도 mlan0 서브넷을 스윕한다.

### 3.11 snmp (snmpd 조건부 기동 + 트랩)

| 경로 | 라벨 | 타입 | 기본값 | 허용값/범위 | UI편집 | 적용시점 | 설명 |
|---|---|---|---|---|---|---|---|
| `snmp.enabled` | snmpd 서비스 활성화 | bool | `false` | true\|false | caution | boot | `wifi_apply_enabled.sh`가 `snmpd.service` enable/disable 동기화 + `wifi_services.sh` start. **기본 off(opt-in, UDP161 보안)** |
| `snmp.trap.enabled` | SNMP 트랩 송신 활성화 | bool | `false` | true\|false | yes | runtime | 무선 링크/채널 이벤트 시 snmptrap 송신 |
| `snmp.trap.dest` | 트랩 수신지 | string | `""` | `host` 또는 `host:port` | yes | runtime | 빈값이면 트랩 미송신 |
| `snmp.trap.community` | 트랩 community | string | `public` | 문자열 | yes | runtime | SNMP community 문자열 |
| `snmp.trap.version` | 트랩 SNMP 버전 | enum | `2c` | `1`\|`2c` | yes | runtime | SNMP 프로토콜 버전 |

**비고 (snmp)** — 소비: `snmp.enabled`는 `wifi_apply_enabled.sh`(snmpd enable/disable) + `wifi_services.sh`(start), `snmp.trap.*`는 `wifi_event.sh`(링크/채널 이벤트 시 snmptrap 송신). `snmp.enabled`는 UDP 161 포트를 여므로 caution.

---

### 3.12 opc (OPC 제어 데몬)

| 경로 | 라벨 | 타입 | 기본값 | 허용값/범위 | UI편집 | 적용시점 | 설명 |
|---|---|---|---|---|---|---|---|
| `opc.enabled` | opcd 서비스 | bool | `false` | true\|false | caution | boot | wlan-opc OPC 제어 데몬 조건부 기동. `wifi_apply_enabled.sh`가 systemctl 상태 동기화. 기본 opt-in off |


## 4. 웹 UI 그룹핑 권장

폼을 아래 화면 그룹으로 나눌 것을 권장한다. `caution`/`no` 항목은 각 그룹 내 "고급/읽기전용" 하위 섹션으로 분리한다.

| UI 그룹 | 포함 경로(요약) | 비고 |
|---|---|---|
| **기본/드라이버** | `global.*`(BOARD_TYPE, BUS_TYPE, BLUETOOTH.enable, MOD_PARA, CAL/TXPWR, STANDARD, DEV_CAP_MASK, ANT_TYPE, tx_work), `mac.*`, `mlanN.{STANDARD, CAL_DATA_CFG, TXPWRLIMIT_PATH, Frequency, net_rx, mgmt_hex_dump_enable, rate_adapt.*}` | 대부분 caution/reboot. MAC·펌웨어·표준은 고급 |
| **인터페이스 활성화** | `mlanN.enabled`, `global.ping_monitor.enabled`, 각 `*.enabled` 토글 | mlan1 기본 off |
| **브릿지** | `wbridge.{enabled, bridge_iface, mac_mode, ip_discovery, eth_client_ip, eth_link_wait_sec, eth_sweep_subnet, peer_route.enabled, arp_ignore_always.enabled, engine}`, `wbridge.moal.*`(고급), `wbridge.optimize.*`, `wbridge.link_guard.*` | moal.*/arp_ignore_always는 고급 |
| **로밍** | `mlanN.roaming.*`, `mlanN.periodic_roam.*`, `mlanN.bgscan.*`, `mlanN.mcs_tier.*`, `mlanN.connect_threshold` | 고급기능(PREDICTIVE_ROAM)은 "고급 로밍" 하위로(ADAPTIVE/LOAD/POST_ROAM 은 감사 D1로 제거). `connect_threshold`=연결 최소 RSSI(wpa_supplicant) |
| **모니터링·발열** | `temperature.*`, `wbridge.thermal.*`, `mmc.*`, `mcp.*`, `monitor.*`, `logger.*`, `mlanN.logger.*`, `eth0.logger.*`, `mlanN.thermal_mgmt` | 온도 임계·게인은 고급/읽기전용 |
| **헬스·리부트** | `mlanN.checker.*`, `mlanN.arping.*` | 재부팅 정책 항목(MAX_REBOOT_COUNT 등)은 고급 |
| **SNMP** | `snmp.enabled`, `snmp.trap.*` | snmp.enabled는 고급(포트 오픈) |
| **읽기전용/자동** | `mcp.iio_device`, `wbridge.optimize.profile_version` | UI 노출하되 편집 잠금 권장 |

---

## 5. ⚠️ 주의 박스

> **① `wbridge.arp_ignore_always.enabled`는 IP 배치(토폴로지) 종속**
> 출하 기본값은 **`false`**(순수 mlan0-IP 기본 토폴로지 전제)다. eth0에 IP를 두거나 eth0/mlan0 동일 서브넷이라 클론 MAC 이중 ARP 응답이 문제되는 구성에서는 `true` 로 바꾼다. **토폴로지(IP 배치)는 이 JSON이 아니라 `wifi <iface> ip`/webui로 결정**되므로, 배치를 바꾸면 이 값도 함께 점검해야 한다. 순수 mlan0-IP에서 BD↔유선peer 직접통신이 필요하면 `peer_route.enabled=true` + `ip_discovery=true` + `arp_ignore_always.enabled=false` 3종 세트로 설정한다(`arp_ignore_always=true` + `peer_route=off` + mlan0-IP + 유선↔BD 필요 조합에서는 `wifi_init.sh`가 `[GUARD]` 경고).

> **② mlan1 기본 비활성 (`mlan1.enabled=false`)**
> mlan1은 출하 시 비활성이다. mlan1의 하위 데몬(logger/roaming/bgscan/checker/on_connect 등)은 `mlan1.enabled=false`인 동안 모두 강제 disable된다. UI에서 mlan1 설정을 노출할 때 "인터페이스 비활성" 상태를 명확히 표시할 것.

> **③ caution 항목 편집 위험 (온도·게인·펌웨어경로 등)**
> `temperature.*`/`wbridge.thermal.thresholds.*`(과열 시 강제 재부팅), `mcp.gain_current`/`gain_voltage`(HW 캘리브레이션 상수 — 오설정 시 전류 오판정), `global.CAL_DATA_CFG`/`TXPWRLIMIT_PATH`/`BOARD_TYPE`/`BUS_TYPE`(펌웨어·RF·드라이버 경로), `mlanN.checker.{MAX_REBOOT_COUNT, MIN_UPTIME_SEC, ...}`(재부팅 정책), `mlanN.on_connect.commands`(command injection)은 잘못 편집 시 부팅 불가·과열 미보호·재부팅 루프·보안 사고로 이어질 수 있다. UI에서 경고·범위 검증을 반드시 둘 것.

> **④ 유령 키 (`logger.link_retry_count` / `logger.link_retry_delay_sec`)**
> 이 두 키는 **JSON에 존재하지 않는다.** 스키마/폼에 넣지 말 것. `wifi_logger_link.py`의 모듈/CLI 기본값(각각 `4` / `0.05`)이며, 조정이 필요하면 CLI `--link-retry-count` / `--link-retry-delay`로만 가능하다.

> **⑤ `mcs_tier.ht/vht/he`는 문자열**
> 정수처럼 보이지만 타입은 **문자열**이다(`"7"`, `"both 7"`, `""`). 빈 문자열은 "해당 prefix skip" 센티널이며 값은 `mlanutl mcstiercfg`에 verbatim 전달된다. 숫자 입력 컨트롤이 아니라 문자열 입력으로 다룰 것.

---

## 부록: 소비 스크립트 요약

- 부팅/초기화: `wifi_init.sh`, `wifi_init_config_lib.sh`, `wifi_apply_enabled.sh`
- 브릿지: `wifi_bridge.sh`, `wired_mac_ip_get.py`, `optimize-for-udp.sh`, `setup-irq-affinity.sh`, `wifi_thermal_state_update.sh`
- 로깅: `wifi_logger_temp.sh`, `wifi_logger_mmc.sh`, `wifi_logger_mcp.sh`, `wifi_logger_cpu.sh`, `wifi_logger_link.py`, `wifi_logger_stat.py`, `wifi_link_monitor.py`
- 로밍/스캔: `wifi_roam.py`, `wifi_bgscan.py`, `wifi_periodic_roam.sh`, `passive_roam.py`
- 헬스: `wifi_checker.sh`, `wlan_reboot_policy.sh`, `wifi_arping.sh`, `wifi_arping_sweep.sh`
- 이벤트/MAC/SNMP: `wifi_event.sh`, `wifi_mac_set.py`, `wifi_mac_save.py`, `wifi_services.sh`, `snmpd`
