# wifi_init_conf.json 설정 가이드

## 개요

`wifi_init_conf.json`은 wlan-package의 **중앙 설정 파일**로, WiFi 드라이버 초기화부터 모니터링, 로밍, reboot 정책까지 모든 런타임 설정을 관리한다.

- **설치 경로**: `/opt/wlan/config/wifi_init_conf.json` (패키지 원본)
- **런타임 경로**: `/usr/local/etc/wifi_init_conf.json` (postinst에서 복사)
- **의존성**: `jq` (shell), `json` 모듈 (python)
- **fallback**: JSON이 없거나 `jq`가 없으면 모든 스크립트가 내장 기본값으로 동작

---

## 섹션 구조

```
wifi_init_conf.json
├── global              # 드라이버 초기화 (보드/버스/펌웨어 자동선택, 모듈 파라미터)
│   ├── BLUETOOTH       #   BT combo 펌웨어 사용 여부 (enable, fw 자동선택 반영)
│   └── ping_monitor    #   ping 모니터 서비스 제어
├── mac                 # MAC 주소 설정 (인터페이스별)
├── wbridge             # wifi_bridge 프로세스 설정
│   ├── peer_route      #   양방향 peer 라우팅 마스터 스위치 (enabled)
│   ├── arp_ignore_always #  ARP 정책 (enabled, IP 배치 종속: eth0-IP/동일서브넷=true)
│   ├── optimize        #   커널 레벨 네트워크 튜닝 (UDP/IRQ/오프로드)
│   ├── link_guard      #   유/무선 링크 상태 감시 (engine=pcap|tpacket 전용)
│   ├── moal            #   moal 엔진 파라미터 (engine=moal 전용 insmod 인자)
│   └── thermal         #   브릿지 thermal 상태 관리
│       └── thresholds  #     온도 임계값 (히스테리시스)
├── temperature         # 온도 모니터링 임계값
├── mmc                 # eMMC 수명 모니터링
├── mcp                 # 전류/전압 센서 모니터링
├── monitor             # wifi_link_monitor.py 표시 설정
├── logger              # 각종 로깅 주기 설정 (전역 기본값)
├── snmp                # snmpd 조건부 기동 + 트랩
│   └── trap            #   SNMP 트랩 송신 설정
├── eth0                # eth0 인터페이스 설정
│   └── logger          #   eth0 전용 로깅 override
├── mlan0               # mlan0 인터페이스 설정
│   ├── logger          #   mlan0 전용 로깅 override (enabled 포함)
│   ├── net_rx          #   MGMT 프레임 로깅 (→ PCIE9098_0 / SD9098_0)
│   ├── rate_adapt      #   FW rate adaptation
│   ├── periodic_roam   #   주기적 패시브 로밍
│   ├── bgscan          #   백그라운드 스캔
│   ├── roaming         #   로밍 알고리즘
│   ├── mcs_tier        #   MCS tier 능력 제한 (mcstiercfg)
│   ├── on_connect      #   AP 연결 후 실행 명령
│   ├── checker         #   wifi_checker + reboot 정책 (구 최상위 checker에서 이동)
│   └── arping          #   ARP 연결 감시 + sweep (구 최상위 arping에서 이동)
└── mlan1               # mlan1 인터페이스 설정 (mlan0과 동일 구조)
    ├── logger, net_rx, rate_adapt, periodic_roam, bgscan,
    ├── roaming, mcs_tier, on_connect, checker, arping ...  # mlan0과 동일 구조
    └── (net_rx → PCIE9098_1 / SD9098_1)
```

> **구조 변경 요약** (이전 연동/구 가이드 대비): `checker`·`arping`이 최상위 → **인터페이스별**(`mlan0.checker`, `mlan1.arping` 등)로 이동했다. `global`에서 `FW_NAME`/`MFG_MODE`가 제거되고 펌웨어는 `BOARD_TYPE`+`BUS_TYPE`+`BLUETOOTH.enable`로 자동 선택된다. `global.rate_adapt` 블록은 제거되어 실질적으로 per-iface(`mlanN.rate_adapt`)만 존재한다. `snmp` 섹션이 신규 추가되었다. 인터페이스별 스칼라 키(`connect_threshold`, `mgmt_hex_dump_enable`, `thermal_mgmt`, `enabled`, `Frequency` 등)는 트리 간결성을 위해 생략했다 — §11 참조.

---

## 1. global - 드라이버 초기화

**사용 스크립트**: `wifi_init.sh`

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `BOARD_TYPE` | enum | `"imx93"` | 보드/드라이버 선택. `imx93*`이면 `mlan_imx93.ko`/`moal_imx93.ko`, 그 외(`imx8mm` 등)면 `mlan_imx8.ko`/`moal_imx8.ko`. insmod할 `.ko` 파일 선택에 사용 (변경 시 드라이버 리로드/재부팅 필요) |
| `BUS_TYPE` | enum | `"sdio"` | 버스 종류. `sdio`\|`pcie`. **fw_name 자동선택**, `mod_para` 블록 prefix(`sdio`→`SD9098_N`, `pcie`→`PCIE9098_N`), 검증 패턴 결정에 사용. 하드웨어와 반드시 일치 |
| `BLUETOOTH.enable` | bool | `false` | BT combo 펌웨어 사용 여부. **fw_name 자동선택에 반영**(`true`면 `*_combo*.bin`). fw_name이 바뀌면 `wifi_config.py`가 `mod_para.conf`에 자동 기입 |
| `MOD_PARA` | string | `"cts/wifi_mod_para.conf"` | 모듈 파라미터 설정 파일 (`/lib/firmware/` 기준). moal insmod 인자 `mod_para=`로 전달. dev_cap_mask/cal_data_cfg/mac_addr/net_rx/fw_name 주입 대상 파일 |
| `CAL_DATA_CFG` | string | `"cts/WlanCalData_ext_RD.conf"` | 캘리브레이션 데이터 파일 **fallback**. 인터페이스별 `mlanN.CAL_DATA_CFG`가 우선하며, 비어있을 때만 이 값 사용. `wifi_init.sh`가 `wifi_mod_para.conf` 블록의 `cal_data_cfg=`로 주입. 자세한 내용은 [CAL_DATA_CFG 매핑](#cal_data_cfg--txpwrlimit_path--인터페이스별-매핑) 참고 |
| `TXPWRLIMIT_PATH` | string | `"/lib/firmware/cts/txpwrlimit_cfg_9098.conf"` | TX 파워 리밋 설정 파일 (절대 경로) **fallback**. 인터페이스별 `mlanN.TXPWRLIMIT_PATH`가 우선하며, 비어있을 때만 이 값 사용 |
| `STANDARD` | string | `""` | WiFi 표준 제한 **fallback**. 인터페이스별 `mlanN.STANDARD`가 우선하며, 비어있을 때만 이 값 사용. `n`/`ac`/`ax`(또는 `4`/`5`/`6`). 자세한 내용은 [11.1 STANDARD → dev_cap_mask 매핑](#standard--wifi_mod_paraconf-매핑) 참고 |
| `DEV_CAP_MASK` | string | `""` | dev_cap_mask raw fallback. 인터페이스/global `STANDARD`가 모두 비었을 때만 사용 |
| `ANT_TYPE` | string | `""` | 안테나 경로(GPIO mux) **부팅 시 설정**. `internal`/`external`(또는 `0`/`1`). `wifi_init.sh`가 무선 드라이버 로드 **직전**에 `SW_SEL1`/`SW_SEL2` GPIO로 적용. **빈값이면 설정하지 않음**(하드웨어/이전 상태 유지). 런타임 변경은 기존 `wifi ant` 명령 사용(persist 안 함) |
| `tx_work` | int | `0` | moal 데이터 TX 제출 방식 module_param. `0`=호출자 컨텍스트 동기 제출(홉1), `1`=`tx_workqueue` 비동기(홉2, NXP iMX 기본). moal insmod 인자로 전달(bridge engine 무관·드라이버 전역). **capability-gate**: `.ko`가 `tx_work` param을 선언한 경우에만 전달, 미선언 `.ko`면 skip. 런타임 sysfs는 init 때 latch라 무효 → 모듈 reload 필요 |

> **펌웨어 자동 선택 (FW_NAME/MFG_MODE 제거됨)**: 구 가이드의 `FW_NAME`·`MFG_MODE`는 **더 이상 JSON 필드가 아니다**.
> - **fw**: `wifi_init.sh`가 `BOARD_TYPE`+`BUS_TYPE`+`BLUETOOTH.enable`(+ mod_para 파일의 `mfg_mode=` 값)을 조합해 `fw_name`을 자동 산출하고, 현재 `mod_para.conf`의 `fw_name`과 다르면 `wifi_config.py`로 자동 기입한다.
> - **MFG_MODE**: JSON이 아니라 `mod_para.conf`의 `mfg_mode=` 라인에서 읽는다.

> **참고**: `BRIDGE_IFACE`, `MAC_MODE`, `ETH_CLIENT_IP`, `eth_link_wait_sec`는 `wbridge` 섹션으로 이동되었습니다. 하위 호환을 위해 `global`에 있어도 동작하지만(레거시 fallback 키), 새 설정에서는 `wbridge` 섹션을 사용하세요.

### 1.1 rate_adapt 안내 (global.rate_adapt는 제거됨)

> **⚠️ `global.rate_adapt` 블록은 현재 JSON에 존재하지 않는다.** `wifi_init.sh`는 `.mlanN.rate_adapt.<key> // .global.rate_adapt.<key> // empty` 형태로 global을 fallback으로 **읽도록 코딩되어 있으나, JSON에 데이터가 없으므로** 실질적으로는 항상 **인터페이스별 값(`mlanN.rate_adapt`)** 또는 내장 기본값으로 귀결된다. 즉 rate_adapt는 per-iface 설정만 유효하다 — [§11.5 rate_adapt](#115-rate_adapt---fw-rate-adaptation-per-iface-override) 참조.

### 1.2 global.ping_monitor - Ping 모니터 서비스

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `enabled` | bool | `false` | `wifi_ping_monitor.service` 활성화 여부 |

---

## 2. mac - MAC 주소 설정

**사용 스크립트**: `wifi_init.sh`, `wifi_mac_set.py`, `wifi_mac_save.py`

```json
"mac": {
    "mlan0": { "base": "", "target": "" },
    "mlan1": { "base": "", "target": "" },
    "eth0":  { "base": "" }
}
```

| 키 | 설명 |
|----|------|
| `base` | 인터페이스 기본(baseline) MAC. **`enabled` 여부와 무관하게 항상 반영**된다(#110). 빈 문자열이면 생략 |
| `target` | static 모드 소스 + 드라이버 `mod_para`(`wifi_mod_para__.conf`)의 `mac_addr=` 주입값(펌웨어/어댑터 MAC). 빈 문자열이면 생략 |

> **적용 규칙 (#110~)**
> - mlan0/mlan1 모두 `enabled` 여부와 무관하게 `base`를 먼저 반영(baseline). (기존: iface `enabled=false`면 MAC 설정 전체 skip → mlan1 비활성 시 base 미반영)
> - **실제 bridge 기능이 켜져 있고**(`wbridge.enabled=true` & bridge iface `enabled`) **`mac_mode`의 dynamic(유선 peer 클론)/static(target) 변환에 성공한 경우에만** bridge 인터페이스 MAC을 `base` 대신 그 값으로 override한다. 변환 실패/`default` 모드면 base 유지.
> - resolve_mac 우선순위(bridge iface): dynamic(`/tmp/eth0_client_mac`) → static이면 target → base. 형식 위반은 warn 로그 후 무시.
> - 빈 문자열이면 해당 소스 생략(기존 `/opt/wlan/mac` 디렉토리 대체).

> **적용 계층·시점**: `update_mac.sh`가 systemd `.link`(`/etc/systemd/network/2X-<iface>.link`)의 `MACAddress=`를 기록하며, **udev가 netdev 생성 시(=부팅/드라이버 리로드)에만 적용**한다 — JSON만 고치고 재부팅하지 않으면 live 인터페이스 MAC은 바뀌지 않는다. `target`은 추가로 `mod_para`의 `mac_addr=`로 주입돼 insmod 시 어댑터 MAC이 된다. `.link`가 없거나 비어 있으면(구버전 잔재) `update_mac.sh`가 `[Match]/[Link]`를 갖춘 파일을 재생성하고(#111), 변경 시 인터페이스당 최대 5개 회전 백업(`.bak.1`~`.bak.5`, 동일 MAC 중복 제외)한다.

---

## 3. wbridge - WiFi 브릿지 프로세스

**사용 스크립트**: `wifi_bridge.sh`, `wifi_init.sh`, `wired_mac_ip_get.py`, `/etc/default/wbridge`

> **우선순위**: `wifi_init_conf.json` (SSoT) > `/etc/default/wbridge` (fallback) > 스크립트 기본값
>
> JSON이 정상 파싱되면 JSON 값이 최종 사용된다. JSON이 없거나 파싱에 실패할 때만
> `/etc/default/wbridge`가 폴백 소스로 사용된다.

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `enabled` | bool | `true` | bridge 기능 마스터 스위치. `false`이면 bridge 서비스 전체 stop+disable |
| `bridge_iface` | string | `"mlan0"` | bridge에 사용할 인터페이스. `"mlan0"` 또는 `"mlan1"`. `mlan1`이면 moal insmod `bridge_wlan_idx=1`로 전달(engine=moal 시). pcap 트랙은 `wifi_bridge@mlan0`에 하드코딩 |
| `mac_mode` | string | `"dynamic"` | MAC 주소 모드. `"default"` (base만), `"dynamic"` (유선측 MAC 동적 획득→base), `"static"` (target→base) |
| `ip_discovery` | bool | `false` | dynamic MAC 모드에서 MAC 확보 후 클라이언트 IP까지 탐색할지. `false`면 MAC만 확보 후 즉시 종료(부팅 가속, host route 미등록). `true`면 IP 탐색(passive→unicast→sweep) + host route(`/32 dev eth0`) 자동 등록. 양방향 라우팅 완성에는 `peer_route.enabled=true` 필요 |
| `eth_client_ip` | string | `""` | 유선 클라이언트 고정 IP. 빈 문자열이면 quick ARP probe 비활성. 네트워크 토폴로지 종속 |
| `eth_link_wait_sec` | int | `5` | dynamic MAC 모드에서 유선 링크 준비 대기 시간 (초). `wired_mac_ip_get.py`에서 사용 (구 기본값 3 → **5**) |
| `eth_sweep_subnet` | string | `""` | peer 발견 최후 sweep 대역 (CIDR). sweep는 eth0로 전송되지만 **peer는 mlan0-IP 토폴로지에서 mlan0와 같은 대역**에 있다. 빈값이면 `mlanN.network의 Address 설정값 → mlanN 런타임 inet → eth0 런타임 inet` 순 폴백 — **설정값을 우선 읽어 부팅 race(mlanN IP 미부여 시점에 eth0 관리대역으로 오판)를 차단**한다. ⚠️ 순수 eth0-IP 토폴로지에서 `mlanN.network`에 이전 구성의 `Address`가 남아 있으면 mlanN 대역이 잡히므로, 그 구성에서는 이 값을 명시해 우회, 예: `"192.168.0.0/24"` |
| `peer_route.enabled` | bool | `false` | 양방향 BD↔유선peer 라우팅(eth0 host scope IP/table 100 + host route + ARP/RPF sysctl) **마스터 스위치**(옵션 X). `false`면 부팅 시 모든 변경을 revert(기본 투명 브릿지 — 토폴로지 무관 안전). BD가 유선 peer와 **직접** 통신해야 하는 mlan0-IP 토폴로지에서만 `true`(+ `ip_discovery=true`, `arp_ignore_always.enabled=false` 권장). **토폴로지(IP 배치)는 이 JSON이 아니라 `wifi <iface> ip`/webui로 결정.** invalid/missing이면 factory default=`true` |
| `arp_ignore_always.enabled` | bool | `true` | `peer_route`와 **독립**으로 `arp_ignore=1`/`arp_announce=2`를 적용해 클론 MAC 이중 ARP 응답 레이스를 차단. **IP 배치에 종속**: eth0-IP(또는 eth0/mlan0 동일 서브넷) 구성에서 `true`, 순수 mlan0-IP에서 `false`. ⚠️ 아래 주의 박스 참고 |
| `engine` | string | `"pcap"` | 브릿지 엔진. `pcap`\|`tpacket`(유저스페이스) \| `moal`(드라이버 레벨, `mod_para` `bridge_mode=1`). invalid면 pcap 폴백. moal↔그 외 전환은 드라이버 리로드/재부팅 필요. engine=moal이면 `link_guard` 무시 |

> **⚠️ `arp_ignore_always.enabled`는 IP 배치(토폴로지)에 종속**: 출하 기본값 **`true`**는 eth0에 IP를 두거나 eth0/mlan0가 동일 서브넷이라 클론 MAC 이중 ARP 응답이 문제되는 구성을 전제로 한다. **토폴로지(IP 배치)는 이 JSON이 아니라 `wifi <iface> ip`/webui로 결정**되므로, 배치를 바꾸면 이 값도 함께 점검해야 한다. 순수 mlan0-IP에서 BD↔유선peer 직접통신이 필요하면 `peer_route.enabled=true` + `ip_discovery=true` + `arp_ignore_always.enabled=false` 3종 세트로 설정한다(이때 mlan0 IP 배치도 `wifi mlan0 ip`/webui로 수행). `arp_ignore_always=true` + `peer_route=off` 조합에서 mlan0-IP + 유선↔BD가 필요하면 `wifi_init.sh`가 `[GUARD]` 경고를 남긴다.
>
> **진단 명령**: 현재 3종 토글·런타임 상태·정합성을 한눈에 보려면 **`wifi {0|1} br status`** (읽기 전용, 값 변경 없음). `peer_route↔ip_discovery`, `arp_ignore_always↔토폴로지(추정)`, 설정↔런타임(`/32` 미러 등)의 불일치를 `[WARN]`/`[INFO]`로 표시한다. 매번 3종 세트를 외우지 않아도 이 명령으로 현재 구성이 맞는지 확인할 수 있다.
>
> **eth 지연연결 라우트 등록**: `peer_route=on`인데 이더넷을 **부팅 후 나중에** 연결한 경우, 부팅 시점엔 `wired_mac_ip_get.py`가 링크 대기(`wait_for_eth_link`)에서 빠져나와 **peer host route(`<peer>/32 dev eth0`)가 누락**된다(나머지 인프라 — eth0 `/32` 미러·table 100·sysctl — 은 링크 무관하게 이미 세팅됨). 이후 eth 연결 시 **`wifi {0|1} br route auto`**로 유선 peer를 sweep 탐색해 그 라우트를 사후 등록한다(읽기 전용 아님). 하위 명령: `find [<subnet>]`=탐색만(읽기 전용), `set <ip>`=IP 직접 지정 등록, `auto [<subnet>]`=정확히 1건 발견 시 자동 등록(0건/2건+는 에러 — 후자는 `set <ip>`로 지정). 서브넷 생략 시 `eth_client_ip`(단일 IP quick ARP) → `eth_sweep_subnet` → mlanN 대역 순으로 대상 결정. (자동 트리거는 후속 확장 예정)
>
> **⚠️ `find`/`auto`가 peer를 못 찾을 때 (eth0 타서브넷)**: `eth0`의 IP가 sweep 대역과 다른 서브넷이면(예: `eth0=192.168.1.1/24`, peer=`192.168.0.220`), same-subnet source에만 ARP 응답하는 peer는 발견에 실패한다. #113에서 sweep arping의 source를 **대역 내 우리 IP(mlanN)**로 지정하도록 보정했다(⚠️ **서브넷 인자는 sweep 범위만** 바꾸고 arping source IP는 안 바꾸므로 대역 지정만으로는 안 풀림). 그래도 안 되면 `wifi {0|1} br route set <peer-ip>`로 직접 등록한다.

### 3.1 wbridge.optimize - 커널 레벨 네트워크 튜닝

**사용 스크립트**: `wifi_bridge.sh` → `optimize-for-udp.sh`, `setup-irq-affinity.sh`

> `enabled=false`이면 하위 설정(`mode`, `irq_affinity` 등) 모두 무효. 커널 튜닝 없이 wbridge 바이너리 기본값으로 동작한다.

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `enabled` | bool | `false` | 커널 레벨 최적화 활성화 (UDP 버퍼, IRQ, 오프로드 등) |
| `mode` | string | `"normal"` | 동작 모드: `"latency"`, `"normal"`, `"eco"`, `"thermal"` |
| `irq_affinity` | string | `"auto"` | IRQ affinity 정책: `"auto"` (코어수 자동판단), `"pinned"` (명시적 CPU 핀), `"none"` (커널 기본) |
| `profile_version` | int | `1` | 프로파일 스키마 버전 (로깅/메타데이터용) |

### 3.2 wbridge.link_guard - 링크 상태 감시

**사용 스크립트**: `wifi_bridge.sh`

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `enabled` | bool | `true` | 링크 감시 활성화. `false`이면 wbridge 프로세스를 wait 없이 방치 |
| `link_down_debounce_sec` | int | `2` | 유선 링크 다운 디바운스 시간 (초) |
| `link_up_stable_sec` | int | `2` | 유선 링크 업 안정화 대기 시간 (초) |
| `link_idle_poll_sec` | int | `5` | 링크 유휴 폴링 주기 (초) |
| `wait_ready_timeout_sec` | int | `10` | 인터페이스 준비 대기 타임아웃 (초) |
| `wlan_roam_grace_sec` | int | `15` | 무선 링크 다운 시 로밍 유예 시간 (초) |
| `wlan_down_restart` | bool | `false` | 무선 링크 다운 유예 초과 시 bridge 재시작 여부 |

### 3.3 wbridge.thermal - 브릿지 Thermal 관리

**사용 스크립트**: `wifi_thermal_state_update.sh`

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `enabled` | bool | `false` | thermal 주기 모니터링(`wifi_thermal_state.timer`) ON/OFF. false면 상태 갱신이 멈춰 clamp 비활성 |
| `mode_force` | bool | `true` | `true`이면 thermal 반응 억제 — 클램핑 무시(요청 모드 강제) + 상태 변경 시 bridge 재시작 skip |
| `auto_restart` | bool | `false` | thermal 상태 변경 시 bridge 자동 재시작 |
| `restart_cooldown_sec` | int | `60` | 재시작 쿨다운 (초) |
| `bridge_units` | string | `"wifi_bridge@mlan0.service wifi_bridge@mlan1.service"` | 관리 대상 systemd 유닛 |

### 3.4 wbridge.thermal.thresholds - 온도 임계값

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `warm_cpu_enter` | int | `90` | CPU warm 진입 온도 (°C) |
| `hot_cpu_enter` | int | `95` | CPU hot 진입 온도 (°C) |
| `warm_cpu_exit` | int | `85` | CPU warm 해제 온도 (°C) |
| `hot_cpu_exit` | int | `90` | CPU hot 해제 온도 (°C) |
| `warm_wifi_enter` | int | `85` | WiFi warm 진입 온도 (°C) |
| `hot_wifi_enter` | int | `90` | WiFi hot 진입 온도 (°C) |
| `warm_wifi_exit` | int | `80` | WiFi warm 해제 온도 (°C) |
| `hot_wifi_exit` | int | `85` | WiFi hot 해제 온도 (°C) |

**히스테리시스 설계**: enter와 exit 사이에 5도 갭을 두어 상태 플리핑을 방지한다.

### 3.5 wbridge.moal - moal 엔진 파라미터

**사용 스크립트**: `wifi_init.sh` (moal 모듈 insmod 인자)

`wbridge.engine="moal"`(드라이버 레벨 bridge)일 때만 적용된다. 모두 **insmod 인자**로 전달되므로 변경 반영에는 재부팅/드라이버 리로드가 필요하다. `engine=pcap|tpacket`이면 이 블록은 무시된다.

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `keepalive_ms` | int | `1` | `main_work` keepalive hrtimer 주기(ms). `0`=off(트래픽 워밍 의존, idle 후 첫 패킷 콜드 스파이크), `1`=기본(실측 7ms 레이턴시 전제). 클수록 idle wakeup↓(발열↓)·레이턴시↑. → insmod 인자 `bridge_keepalive_ms`. |
| `keepalive_idle_ms` | int | `20` | keepalive **idle cutoff**(ms). `0`=free-running(타이머 상시 가동, **드라이버 기본**), `>0`=adaptive(트래픽으로 타이머 arm 후 이 시간만큼 idle 지속 시 자동 정지 → idle 시 wakeup 0). → insmod 인자 `bridge_keepalive_idle_ms`. 아래 ⚠️/capability-gate 참고. |
| `debug` | int(`0`\|`1`) | `0` | bridge 디버그 로그(`BR_DBG`/`[DBG-RXDROP]`) — dmesg로 패킷 경로·가드 진단. 런타임 변경 가능: `/sys/module/moal/parameters/bridge_debug`. → insmod 인자 `bridge_debug`. |
| `peer` | string | `""` | 브릿지 유선 peer 인터페이스명. 빈값=드라이버 기본(`eth0`) + 인자 미전달. 유효(IFNAMSIZ 15자 이내)할 때만 → insmod 인자 `bridge_peer`. moal 엔진에만 적용(pcap 트랙·ARP 스크립트는 eth0 하드코딩). |
| `consume_link_local` | int(`0`\|`1`) | `""` | 차단된 link-local(STP/LLDP) 프레임을 드라이버 내에서 폐기(`1`)하여 mlan `rx_nohandler` 증가를 막는 A/B 진단 토글. 빈값/미설정=드라이버 기본(`0`, 스택 전달 후 폐기). 유효할 때만 → insmod 인자 `bridge_consume_link_local`. |
| `local_hairpin` | int(`0`\|`1`) | `""` | 로컬 hairpin — 로컬발 TX(dst==클론 MAC) 유선 divert + ARP tee/inject로 BD↔유선peer IP 통신을 peer IP 인지(peer_route/ip_discovery) **없이** 성립. AP intra-BSS 무반사 환경의 유일 해법 (2026-07-17 실기 검증). 빈값=드라이버 기본(`0`, off). 유효할 때만 → insmod 인자 `bridge_local_hairpin` (capability-gate 적용, 구버전 .ko 부팅 안전). runtime 변경: `/sys/module/moal/parameters/bridge_local_hairpin`. 개별 수정 대신 `wifi {0\|1} br profile {hairpin\|dual} apply` 묶음 적용 권장(연계 키 함정 방지), 점검은 `wifi {0\|1} br status`. |

> **⚠️ `keepalive_idle_ms=20` 기본 동작 변경 주의** (`mlan1.enabled`와 동급의 behavioral change): 드라이버 자체 기본값은 `0`(free-running)이지만 이 패키지의 출하 config는 `20`(adaptive)이다. 따라서 해당 param을 지원하는 드라이버(예: `moal_imx93.ko`)에서는 부팅 시마다 명시적으로 `20`(adaptive idle cutoff)이 적용되어 **idle 발열↓ ↔ idle 후 첫 패킷 콜드 가능**이라는 동작 변화가 생긴다. 드라이버 기본(free-running)을 원하면 `0`으로 설정한다.

> **capability-gate (부팅 안전)**: `keepalive_idle_ms`는 신규 param이라, `wifi_init.sh`가 로드 대상 `.ko`에 해당 param이 선언돼 있는지 `grep -aq`로 확인한 뒤 선언된 경우에만 insmod 인자로 전달한다. 미선언 드라이버(예: 현재 `moal_imx8.ko`)에는 자동으로 전달하지 않아 `insmod` 실패(=Wi-Fi init 붕괴)를 방지하며, 이 경우 드라이버 기본값(`0`)이 쓰인다. `peer`/`consume_link_local`도 빈값이면 동일하게 미전달(구버전 드라이버 호환). `local_hairpin`은 값이 있어도 parmtype 토큰 게이트를 통과한 경우에만 전달(미선언 .ko + JSON=1 조합은 `wifi br status`가 WARN으로 검출).

---

## 4. checker - WiFi 체커 + Reboot 정책 (인터페이스별: `mlanN.checker`)

**사용 스크립트**: `wifi_checker.sh`, `wlan_reboot_policy.sh`

> **⚠️ 구조 변경**: 이 설정은 최상위 `checker`에서 **인터페이스별**(`mlan0.checker`, `mlan1.checker`)로 이동했다. 각 인터페이스에 동일 키가 존재하며 `enabled`로 데몬(`wifi_checker@<iface>`) 활성화를 제어한다.

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `enabled` | bool | mlan0 `true` / mlan1 `false` | `wifi_checker@<iface>` 데몬 활성화. `wifi_apply_enabled.sh`가 systemd enable/disable로 동기화 |
| `LIMIT_CNT` | int | `5` | 인터페이스 미존재 허용 횟수. 이 값+1회 연속 실패 시 reboot 요청 |
| `MAX_UNSTABLE_DURATION` | int | `10` | 미연결 허용 시간 (초). **단계적 복구**: 초과 시 1차 `wpa_cli reassociate`(가벼움), 3배(기본 30s) 초과 시에도 무진행이면 `wpa_supplicant` 재시작. 능동 연결 진행 중(auth/assoc/handshake)이면 개입 보류. AP 부재 시 재시작 루프를 피하려 disconnect 경로는 reboot까지 가지 않음 |
| `MAX_REBOOT_COUNT` | int | `3` | 쿨다운 윈도우 내 최대 reboot 횟수. 초과 시 루프 감지 |
| `REBOOT_COOLDOWN_SEC` | int | `300` | reboot 카운트 리셋 윈도우 (초) |
| `MIN_UPTIME_SEC` | int | `30` | **커널 부팅**(`/proc/uptime`) 후 최소 대기 시간 (초). 이전에는 reboot 거부. 데몬 uptime이 아님 — boot loop 방지용 |
| `FAULT_REASSOC_CNT` | int | `2` | fault 누적 시 재연결(reassoc) 시도 횟수 임계값 |
| `FAULT_RESTART_CNT` | int | `4` | fault 누적 시 wpa_supplicant 재시작 횟수 임계값 |
| `FAULT_REBOOT_CNT` | int | `6` | fault 누적 시 reboot 실행 횟수 임계값 |
| `RECONFIGURE_GRACE_SEC` | int | `20` | reconfigure 재연결 과도기 동안 불안정 사다리(reassoc/restart/reboot) 억제 시간 (초, flag mtime 기준) |

### Reboot 정책 동작 흐름

```
인터페이스 미존재 → ERR_CNT 누적 → ERR_CNT > LIMIT_CNT
→ 로그 수집 (dmesg, journald)
→ wlan_reboot_policy.sh 호출
  ├── uptime < MIN_UPTIME_SEC → 거부 (rc=10)
  ├── 쿨다운 내 count >= MAX_REBOOT_COUNT → 거부 (rc=11, 루프 감지)
  └── 통과 → reboot 실행
```

### Fault 단계적 복구

```
fault 누적 → FAULT_REASSOC_CNT 도달 → reassoc 시도
           → FAULT_RESTART_CNT 도달 → wpa_supplicant 재시작
           → FAULT_REBOOT_CNT 도달 → reboot 실행
```

> **주의**: `REBOOT_COOLDOWN_SEC`는 reboot 사이 강제 대기가 아니라 카운트 리셋 윈도우이다. 윈도우 내 요청이 누적되어 MAX_REBOOT_COUNT에 도달하면 차단된다.

---

## 5. temperature - 온도 모니터링

**사용 스크립트**: `wifi_logger_temp.sh`

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `emerg_cpu` | int | `100` | CPU 긴급 온도 (C). `emerg_count_threshold`+1회 연속 초과 시 cooldown+reboot |
| `crit_cpu` | int | `95` | CPU 심각 온도 (C) |
| `error_cpu` | int | `90` | CPU 에러 온도 (C) |
| `warn_cpu` | int | `85` | CPU 경고 온도 (C) |
| `emerg_mlan` | int | `95` | WiFi 칩 긴급 온도 (C) |
| `crit_mlan` | int | `90` | WiFi 칩 심각 온도 (C) |
| `error_mlan` | int | `85` | WiFi 칩 에러 온도 (C) |
| `warn_mlan` | int | `80` | WiFi 칩 경고 온도 (C) |
| `cooldown_sec` | int | `60` | 과열 시 서비스 중지 후 대기 시간 (초) |
| `recover_cpu` | int | `90` | CPU 복구 판정 온도 (C). 이하로 내려가면 reboot |
| `recover_mlan` | int | `85` | WiFi 칩 복구 판정 온도 (C) |
| `check_interval_sec` | int | `5` | 온도 체크 주기 (초) |
| `emerg_count_threshold` | int | `2` | emerg 연속 횟수 임계값. 초과 시 cooldown 진입 |

### 온도 레벨과 동작

| 레벨 | CPU 임계값 | MLAN 임계값 | syslog 레벨 | 동작 |
|------|-----------|------------|-------------|------|
| debug | < 85 | < 80 | local3.debug | 정상 |
| warn | >= 85 | >= 80 | local3.warn | 경고 로깅 |
| error | >= 90 | >= 85 | local3.err | 에러 로깅 |
| crit | >= 95 | >= 90 | local3.crit | 심각 로깅 |
| emerg | >= 100 (연속 3회) | - | local0.emerg | WiFi 서비스 중지 → cooldown → reboot |

> **참고**: emerg 판정 분기는 **CPU 온도만** 사용하며, `emerg_mlan`은 crit/err/warn 로깅 분류에서만 참조된다(emerg 분기 미사용).

### 과열 복구 시퀀스

```
CPU >= 100C (연속 emerg_count_threshold+1회)
→ WiFi/Bridge 전체 서비스 중지
→ cooldown_sec 동안 대기
→ 온도 폴링 (5초 간격)
→ CPU < recover_cpu AND MLAN < recover_mlan
→ journald 스냅샷 → reboot (--force)
```

---

## 6. arping - ARP 연결 감시 (인터페이스별: `mlanN.arping`)

**사용 스크립트**: `wifi_arping.sh`, `arping_sweep.sh`

> **⚠️ 구조 변경**: 이 설정은 최상위 `arping`에서 **인터페이스별**(`mlan0.arping`, `mlan1.arping`)로 이동했다. `enabled`로 데몬(`wifi_arping@<iface>`) 활성화를 제어한다(양쪽 기본 `false`).

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `enabled` | bool | `false` | `wifi_arping@<iface>` 데몬 활성화 (mlan0/mlan1 모두 기본 `false`). `wifi_apply_enabled.sh`가 systemd enable/disable로 동기화 |
| `threshold` | int | `10` | ARP 연속 실패 허용 횟수 |
| `cooldown_sec` | int | `10` | 임계값 도달 후 대기 시간 (초) |
| `loop_delay_sec` | int | `10` | ARP 체크 주기 (초) |
| `timeout_sec` | int | `3` | 단일 arping 명령 타임아웃 (초) |
| `sweep_timeout_sec` | int | `1` | ARP sweep 시 개별 호스트 타임아웃 (초) |
| `sweep_parallel_limit` | int | `50` | ARP sweep 동시 실행 프로세스 수 |

### 환경변수 override

`wifi_arping.sh`는 환경변수가 JSON보다 우선한다 (하위 호환):
```bash
# systemd unit에서 환경변수로 override 가능
Environment="THRESHOLD=20"
Environment="LOOPDELAY=5"
```

### ARP 감시 동작

```
arping → 성공 → FAILS=0, loop_delay_sec 대기
       → 실패 → FAILS++
                → FAILS >= threshold
                  → eth0: wired_mac_ip_get.py 재실행
                  → mlan: (예약됨)
                  → FAILS=0, cooldown_sec 대기
```

---

## 7. mmc - eMMC 수명 모니터링

**사용 스크립트**: `wifi_logger_mmc.sh`

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `check_interval_sec` | int | `300` | eMMC 상태 체크 주기 (초, 기본 5분) |

### 수명 판정 기준

eMMC 수명은 JEDEC 표준 EXT_CSD 레지스터에서 읽으며, hex 값 기반으로 판정한다:

| hex 값 | 수명 범위 | syslog 레벨 |
|---------|----------|-------------|
| 01-06 | 0~60% | info |
| 07 | 60~70% | warning |
| 08 | 70~80% | error |
| 09 | 80~90% | crit |
| 0A | 90~100% | emerg |
| 0B | 수명 초과 | emerg |

> 이 매핑은 JEDEC 표준이므로 JSON 설정 대상이 아니다.

---

## 8. mcp - 전류/전압 센서 모니터링

**사용 스크립트**: `wifi_logger_mcp.sh`

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `gain_current` | float | `0.5203` | CH0 전류 ADC 변환 계수 |
| `gain_voltage` | float | `15.6552` | CH1 전압 ADC 변환 계수 |
| `check_interval_sec` | int | `5` | 센서 읽기 주기 (초) |

### 8.1 system_5v - 5V 시스템 전류 임계값

부팅 시 전압이 4.0~6.0V 범위이면 5V 시스템으로 판정한다.

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `warn_a` | float | `1.0` | 경고 전류 (A) |
| `error_a` | float | `1.5` | 에러 전류 (A) |
| `crit_a` | float | `2.0` | 심각 전류 (A) |
| `emerg_a` | float | `2.5` | 긴급 전류 (A) |

### 8.2 system_24v - 24V 시스템 전류 임계값

부팅 시 전압이 20.0~30.0V 범위이면 24V 시스템으로 판정한다.

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `warn_a` | float | `0.2` | 경고 전류 (A) |
| `error_a` | float | `0.3` | 에러 전류 (A) |
| `crit_a` | float | `0.4` | 심각 전류 (A) |
| `emerg_a` | float | `0.5` | 긴급 전류 (A) |

### 전압 자동 감지

```
부팅 → 전압 읽기
  ├── 4.0~6.0V → 5V 시스템 → system_5v 임계값 적용
  ├── 20.0~30.0V → 24V 시스템 → system_24v 임계값 적용
  └── 그 외 → emerg 로그 + check_interval_sec 후 재시도
```

> **gain 값 변경 주의**: ADC raw 값 × scale × gain = 실제 물리값. gain은 하드웨어 회로에 의존하므로 보드 변경 시에만 수정한다.

---

## 9. monitor - 링크 모니터 표시 설정

**사용 스크립트**: `wifi_link_monitor.py`

`wifi_link_monitor.py`의 화면 갱신 주기와 표시 옵션을 제어한다. CLI 인자가 이 설정보다 우선한다.

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `interval_sec` | float | `1.0` | 데이터 수집 및 화면 갱신 주기 (초) |
| `summary_lines` | int | `5` | summary.log 표시 줄 수 |
| `ping_lines` | int | `5` | ping.log 표시 줄 수 |
| `roam_display_sec` | int | `5` | 로밍 이벤트 화면 유지 시간 (초) |

> **우선순위**: CLI 인자(`--interval`, `--summary-lines`, `--ping-lines`, `--roam-display`) > `wifi_init_conf.json` > 하드코딩 기본값

---

## 10. logger - 로깅 주기 설정

**사용 스크립트**: `wifi_logger_cpu.sh`, `wifi_logger_stat.py`, `wifi_bgscan.py`

이 섹션은 **전역 기본값**이다. `eth0.logger`, `mlan0.logger`, `mlan1.logger`에서 인터페이스별로 override할 수 있다.

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `cpu_interval_sec` | int | `60` | CPU/MEM 사용률 로깅 주기 (초) |
| `link_interval_sec` | float | `0.95` | 링크 상태 체크 주기 (초) |
| `stat_log_interval_sec` | int | `1` | WiFi 통계 로깅 주기 (초) |
| `stat_check_interval_sec` | int | `1` | WiFi 통계 체크 주기 (초) |
| `stat_reset_interval_sec` | int | `604800` | 통계 누적 리셋 주기 (초, 기본 7일) |
| `bgscan_stale_threshold_sec` | int | `600` | `beacon.json` 스캔 엔트리 stale 프루닝 임계(초, 기본 10분). 소비: `wifi_logger_scan.py`(마지막 관측 후 이 시간이 지난 BSSID 제거, 반영은 데몬 재시작) |

> `stat_log_interval_sec`과 `stat_check_interval_sec`의 차이: check는 데이터 수집 주기, log는 실제 파일/syslog 기록 주기이다. log >= check 관계를 유지해야 한다.

> **⚠️ `link_retry_count` / `link_retry_delay_sec` 는 JSON 키가 아니다 (유령 키)**: 구 가이드에는 이 두 키가 `logger` 표에 있었으나 **현재 JSON에는 실재하지 않으며, JSON Schema에도 넣지 말 것**. `wifi_logger_link.py`의 순간 끊김 억제 로직(`wpa_cli reconfigure`·`select_network` 직후 100~200ms 동안 `iw station dump`가 비는 것을 곧바로 끊김으로 기록하지 않고 재조회)은 **모듈/CLI 기본값 `4` / `0.05`(초)로만 동작**한다. 조정이 필요하면 JSON이 아니라 CLI `--link-retry-count` / `--link-retry-delay`로만 가능하다.

### per-interface logger override

인터페이스별 `logger` 블록이 존재하면 해당 키만 override된다. 미지정 키는 전역 기본값을 사용한다.

```json
"eth0": {
    "logger": { "link_interval_sec": 1.0 }
},
"mlan0": {
    "logger": {
        "link_interval_sec": 0.95,
        "stat_log_interval_sec": 1,
        "stat_check_interval_sec": 1,
        "stat_reset_interval_sec": 604800
    }
}
```

---

## 11. mlan0 / mlan1 - 인터페이스별 설정

**사용 스크립트**: `wifi_bgscan.py`, `wifi_roam.py`, `wifi_periodic_roam.sh`, `wifi_event.sh`

### 11.1 interface defaults - 인터페이스 기본 활성/주파수

**사용 스크립트**: `wifi_init.sh`, `wifi.sh`

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `enabled` | bool | mlan0 `true` / mlan1 `false` | 인터페이스별 초기화/적용 여부. `false`면 `wifi_init.sh`가 해당 인터페이스의 radio setup과 bridge enable을 건너뜀 |
| `Frequency` | string | `"auto"` | 인터페이스별 bandcfg 기본값. `auto`, `2.4GHz`, `5GHz` |
| `connect_threshold` | int | `-100` | 연결 후보 BSS의 신호레벨이 이 값(dBm) 미만이면 연결 후보에서 제외하는 최소 연결 임계값. `-100`=사실상 무필터. **커스텀 패치 wpa_supplicant 바이너리**(`/opt/wlan/bin/wpa_supplicant.imx8`/`.imx93`)가 `/usr/local/etc/wifi_init_conf.json`을 직접 읽어 적용(shell/python 스크립트 경유 아님). 로그: `BSS: … level N < connect threshold M` |
| `net_rx` | int | `0` | MGMT 프레임 로깅 모드. `wifi_init.sh`가 `wifi_mod_para.conf`의 해당 블록(`PCIE9098_N` / `SD9098_N`)에 반영. 0=비활성 |
| `mgmt_hex_dump_enable` | bool | `false` | MGMT 프레임 hex dump 로깅 활성화 |
| `STANDARD` | string | mlan0 `"ax"`, mlan1 `"ac"` | WiFi 표준 제한. `wifi_init.sh`가 `wifi_mod_para.conf`의 해당 블록에 `dev_cap_mask`로 반영. `n`/`ac`/`ax`(또는 `4`/`5`/`6`). **mlan1은 `ax` 불가**. 아래 [매핑](#standard--wifi_mod_paraconf-매핑) 참고 |
| `CAL_DATA_CFG` | string | `""` | 인터페이스별 캘리브레이션 데이터 파일. 비어있으면 `global.CAL_DATA_CFG`로 fallback. `wifi_init.sh`가 `wifi_mod_para.conf` 블록의 `cal_data_cfg=`로 주입. 아래 [매핑](#cal_data_cfg--txpwrlimit_path--인터페이스별-매핑) 참고 |
| `TXPWRLIMIT_PATH` | string | `""` | 인터페이스별 TX 파워 리밋 파일(절대 경로). 비어있으면 `global.TXPWRLIMIT_PATH`로 fallback. `"none"`이면 이 인터페이스만 미적용. 부팅 시 `mlanutl <iface> hostcmd`로 적용 |
| `thermal_mgmt` | bool | `true` | FW thermal management 활성화. `true`(기본)=enable, `false`=disable. 부팅 시 `mlanutl <iface> hostcmd`로 적용. 아래 [§11.8](#118-thermal_mgmt---fw-thermal-관리) 참고 |

> `config.json`이 별도로 설치된 환경에서는 `.mlan0.enabled`, `.mlan1.enabled`, `.mlan0.Frequency`, `.mlan1.Frequency`가 존재할 때만 이 값을 override한다.
> `config.json`은 wlan-package가 배포하거나 갱신하는 파일이 아닌 외부 호환 overlay다. 따라서
> wlan-package의 정상본 백업 대상에도 포함하지 않으며, 파일을 공급하는 외부 구성 관리자가
> 백업·복구 수명주기를 함께 소유해야 한다.

> **⚠️ 패키지 기본값 주의**: 출하 `wifi_init_conf.json`은 `mlan0.enabled=true`, **`mlan1.enabled=false`**로 설정되어 있다 — 즉 **mlan1은 기본적으로 초기화되지 않는다**(`wifi_init.sh`가 radio setup·bridge enable을 건너뛰고, `wifi_apply_enabled.sh`가 mlan1 child unit을 disable). 신규 설치·공장초기화로 이 기본 config를 그대로 쓰는 환경에서 mlan1을 사용하려면 `mlan1.enabled=true`로 변경한다(override `config.json` 또는 본 파일).

#### net_rx 비트맵

| 값 | bit[1:0] RX 모드 | bit[2] TX 로그 | 설명 |
|----|-----------------|---------------|------|
| `0` | 비활성 | 비활성 | MGMT 로깅 없음 |
| `2` | 로밍 프레임만 | 비활성 | Auth/Assoc/Deauth/Disassoc/Action (Beacon/Probe 제외) |
| `3` | 전체 프레임 | 비활성 | 모든 MGMT 프레임 (Beacon/Probe 포함) |
| `6` | 로밍 프레임만 | 활성 | RX 로밍 + TX 로깅 |
| `7` | 전체 프레임 | 활성 | RX 전체 + TX 로깅 |

> **로그 출력 위치**: dmesg가 아닌 `/proc/mwlan/adapterX/mgmt_log` 커널 링버퍼에 기록됨. `mgmt-log-flush.timer`가 10초 주기로 `/var/log/cantops/mgmt/mlanX/gmgmt.log`에 flush.

#### net_rx → wifi_mod_para.conf 매핑

| JSON 경로 | conf 블록 |
|-----------|----------|
| `.mlan0.net_rx` | `PCIE9098_0` |
| `.mlan1.net_rx` | `PCIE9098_1` |

값이 0이면 conf에서 `net_rx=` 줄이 제거되고, 0보다 크면 `net_rx=값`이 블록 내에 추가/갱신된다.

#### STANDARD → wifi_mod_para.conf 매핑

인터페이스별 `STANDARD`를 `wifi_init.sh`(`apply_mod_para_from_json`)가 `wifi_mod_para.conf`의 해당 블록 `dev_cap_mask=`로 변환한다. 모듈 로드 시 전역 `dev_cap_mask=` 파라미터는 더 이상 전달하지 않고, 블록별 주입으로 대체되었다.

**핵심 규칙**: 인터페이스의 native max 표준(mlan0=`ax`, mlan1=`ac`)과 **같거나 높으면** 제한이 불필요하므로 `dev_cap_mask` 줄을 **삭제**(칩 기본값)한다. 그보다 **낮은** 표준만 마스크를 설정한다.

| STANDARD | dev_cap_mask | mlan0 (max=ax) | mlan1 (max=ac) |
|----------|--------------|----------------|----------------|
| `n` (=`4`) | `0xfffcdfff` | set | set |
| `ac` (=`5`) | `0xfffcffff` | set | **삭제**(=기본값) |
| `ax` (=`6`) | — | **삭제**(=기본값) | **삭제** + 경고 |
| `""` (빈값) | fallback | 아래 우선순위로 결정 | 아래 우선순위로 결정 |

| JSON 경로 | conf 블록 (BUS_TYPE에 따라) |
|-----------|----------|
| `.mlan0.STANDARD` | `PCIE9098_0` / `SD9098_0` |
| `.mlan1.STANDARD` | `PCIE9098_1` / `SD9098_1` |

**우선순위**: `mlanN.STANDARD` → (빈값) `global.STANDARD` → (빈값) `global.DEV_CAP_MASK`(raw) → 그래도 없으면 줄 제거(제한 없음)

**제약**: `mlan1`은 `ax`(11ax)를 지원하지 않는다.
- `wifi mlan1 standard ax` → 거부(미적용)
- JSON에 `.mlan1.STANDARD="ax"`를 직접 넣으면 `wifi_init.sh`가 경고 로그 후 `dev_cap_mask` 미설정(기본값) 처리

매 부팅 시 idempotent하게 반영된다(기존 줄 삭제 후 재삽입).

#### CAL_DATA_CFG / TXPWRLIMIT_PATH — 인터페이스별 매핑

`STANDARD`와 동일하게 인터페이스별 값이 global보다 우선하며, global은 fallback으로 유지된다. 단 두 키는 적용 메커니즘이 서로 다르다.

**CAL_DATA_CFG — `wifi_mod_para.conf` 블록 주입**

`wifi_init.sh`(`apply_mod_para_from_json`)가 인터페이스별 `CAL_DATA_CFG`(없으면 `global.CAL_DATA_CFG`)를 해당 블록의 `cal_data_cfg=`로 주입한다. 모듈 로드 시 전역 `cal_data_cfg=` 파라미터는 더 이상 전달하지 않고 블록별 주입으로 대체되었다(`dev_cap_mask`와 동일).

| JSON 경로 | conf 블록 (BUS_TYPE에 따라) |
|-----------|----------|
| `.mlan0.CAL_DATA_CFG` | `PCIE9098_0` / `SD9098_0` |
| `.mlan1.CAL_DATA_CFG` | `PCIE9098_1` / `SD9098_1` |

- **우선순위**: `mlanN.CAL_DATA_CFG` → (빈값) `global.CAL_DATA_CFG` → (빈값/`none`) `cal_data_cfg=none` (외부 cal 파일 미사용)
- 경로가 있으면 `cal_data_cfg=<경로>`, 빈값이거나 `none`이면 `cal_data_cfg=none`을 블록에 기록한다.
- 매 부팅 시 idempotent하게 반영된다.

**TXPWRLIMIT_PATH — 런타임 `mlanutl hostcmd`**

`wifi_mod_para.conf`와 무관하다. `wifi_init.sh`의 `apply_iface_txpwrlimit`가 인터페이스별로 `mlanutl <iface> hostcmd <경로> txpwrlimit_*_cfg_set`을 실행한다.

- **우선순위**: `mlanN.TXPWRLIMIT_PATH` → (빈값) `global.TXPWRLIMIT_PATH` → (빈값/`none`) 적용 skip
- `"none"`은 해당 인터페이스만 명시적 미적용(전역으로 fallback하지 않음).
- 백업(self-healing)은 인터페이스별 경로를 각각 수행하되 동일 경로는 한 번만 백업한다.

**`wifi` 명령** (둘 다 persist + global fallback 유지):
- `wifi {0|1} cal {0|1|2|none|*.conf}` → `.mlanN.CAL_DATA_CFG` 저장 (다음 부팅에 블록 반영). `wifi cal ...`(인자 없는 최상위)는 `global.CAL_DATA_CFG` 저장.
- `wifi {0|1} txpwr {0|1|2|3|no|default|low|org|*.conf}` → live 적용 + `.mlanN.TXPWRLIMIT_PATH` 저장. `no/0`은 `"none"`으로 저장(이 인터페이스만 미적용). `wifi txpwr ...`(최상위)는 `global.TXPWRLIMIT_PATH` 저장.

#### 공장 초기화 예외 — MOD_PARA / CAL_DATA_CFG / TXPWRLIMIT_PATH / mac.base

`factory_reset.sh`는 활성 설정(`/usr/local/etc/wifi_init_conf.json`)을 템플릿으로 **통째로** 덮어쓰지만, 아래 10개 경로만은 리셋 대상에서 제외한다. 사용자 취향이 아니라 유닛마다 생산 단계에서 정해지는 하드웨어·규제 값이라, 템플릿으로 되돌리면 그 유닛의 캘리브레이션·TX 파워 리밋·기준 MAC이 다른 값으로 바뀌기 때문이다.

| 보존 경로 | 유효성 게이트 |
|-----------|---------------|
| `.global.MOD_PARA` | 파일 존재 |
| `.global.CAL_DATA_CFG`, `.global.TXPWRLIMIT_PATH` | 파일 존재 |
| `.mlan0.CAL_DATA_CFG`, `.mlan0.TXPWRLIMIT_PATH` | 파일 존재 |
| `.mlan1.CAL_DATA_CFG`, `.mlan1.TXPWRLIMIT_PATH` | 파일 존재 |
| `.mac.mlan0.base`, `.mac.mlan1.base`, `.mac.eth0.base` | 할당 가능한 unicast MAC (`mac_link_lib.sh`의 `mac_is_assignable`) |

- 담당 스크립트: `wifi_conf_preserve.sh` (`save <snapshot>` → 덮어쓰기 → `apply <snapshot>`). 보존 목록의 단일 출처는 이 스크립트이며 `wifi_conf_preserve.sh keys`로 확인한다.
- **`apply`는 `wifi_board_config.sh` 뒤에 실행된다.** 보드 감지 헬퍼가 `.global.MOD_PARA`를 상수로 다시 쓰므로(v0.3.0 `wifi_mod_para_.conf` 통합 마이그레이션 잔재) 그 전에 되쓰면 곧바로 덮인다.
- 빈 문자열과 `"none"`은 "사용 안 함"이라는 유효한 설정이므로 게이트를 거치지 않고 그대로 보존한다.
- **되살릴 수 없는 값(없는 파일, 할당 불가 MAC)은 보존하지 않고 템플릿 기본값을 남긴다** (`logger.warn`). 공장 초기화가 고장을 이월하지 않게 하기 위함이며, 특히 `MOD_PARA`가 없는 파일을 가리키면 `moal` insmod가 실패해 무선이 아예 올라오지 않는다.
- **`.mac`은 `base`만 보존하고 `target`은 초기화된다.** `base`는 유닛의 기준 MAC(`write_mac.sh` / `eth_mac_get.sh`가 기록)이지만 `target`은 `wifi mac <iface> target <MAC>`으로 정하는 런타임 설정이다. 리셋 후 `resolve_mac`은 `dynamic → target → base` 순으로 폴백하므로 보존된 `base`가 쓰인다. 함께 초기화되는 `.link` 파일은 다음 부팅에 `resolve_mac` → `update_mac.sh` 경로로 `base`에서 다시 만들어진다.
- 위 10개를 제외한 나머지 키(`STANDARD`, `connect_threshold`, `.wbridge.*`, `.mac.*.target` 등)는 종전대로 전부 템플릿 값으로 초기화된다.
- 테스트: `wifi_conf_preserve_test.sh` (하드웨어/root 불필요)

### 11.2 periodic_roam - 주기적 패시브 로밍

**사용 스크립트**: `wifi_periodic_roam.sh` (service: `wifi_periodic_roam@.service`)

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `enabled` | bool | `false` | 주기적 패시브 로밍 활성화 |
| `interval` | int | `60` | 로밍 시도 주기 (초) |
| `scan_before_roam` | bool | `true` | `true`=roam 전 스캔 수행(최신 RSSI 기반 판단), `false`=기존 ap.log 스캔 데이터 사용 |

### 11.3 bgscan - 백그라운드 스캔

**사용 스크립트**: `wifi_bgscan.py`

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `enabled` | bool | mlan0 `true` / mlan1 `false` | bgscan 데몬 활성화 |
| `interval` | int | `60` | 백그라운드 스캔 주기 (초) |
| `ssid_filter` | bool | `true` | `iw scan`의 **기본 ssid** directed probe 포함 여부. `true`면 conf 기본 ssid를 probe, `false`면 기본 ssid는 probe 없이 스캔. `roaming.extra_ssids`(§11.4)는 이 값과 **무관하게 항상 probe**된다. **`passive=true`(기본)면 directed probe 자체가 없어 이 키는 무효** |
| `freq_filter` | bool | `true` | `iw scan`에 `freq <wpa_supplicant conf의 scan_freq>` 필터 포함 여부. `false`면 freq 제한 없이 전체 대역 스캔(스캔 시간·airtime↑) |
| `passive` | bool | `true` | `true`=패시브 스캔(`iw scan passive`, probe request 미송신·beacon 수신만). `false`=종전 액티브 스캔 |
| `emit_roam_hint` | bool | `true` | 스캔 성공 시 `/tmp/wifi_roam_hint_<iface>` touch — `wifi_roam`의 후보없음 지수 backoff를 즉시 해제하는 신호(§11.4). `false`면 hint 미발행. 메커니즘: 파일은 삭제되지 않고 남으며, `wifi_roam` 메인루프가 매 tick **mtime 변화**를 감지해 backoff 카운터를 리셋한다(단방향 write/read라 race-free). **주의 — 단일 iface 운용에서는 실효가 없다**: backoff가 쌓이는 구간은 로밍 컨디션 ON인데, 그 플래그가 켜져 있으면 bgscan은 스캔 자체를 건너뛰므로 hint가 발행될 수 없다. `/tmp/roam_condition`이 iface 구분 없는 전역 파일이라 **듀얼 iface에서만 우발적으로 발화**할 수 있다(감사 2026-07, `knob_audit_2026-07.md` D3) |

> **패시브 스캔 (`passive`, 기본 `true`)**: bgscan은 probe request를 쏘지 않고 beacon만 수신한다. probe를 안 보내므로 **directed `ssid` 토큰은 명령에서 전부 생략**되며(`ssid_filter`/`extra_ssids`의 directed probe는 패시브에서 무의미), airtime 오염이 줄고 beacon 기반 RSSI라 현재 링크의 `signal_avg`와 측정 스케일이 가까워진다(로밍 판정 정합성↑, §11.4). **한계**: beacon을 보내지 않는 **hidden SSID는 패시브로 발견되지 않는다** — hidden extra SSID가 로밍 후보라면 `passive=false`로 액티브 bgscan을 쓰거나, 로밍 트리거 시의 액티브 폴백(§11.4)에 의존해야 한다.

> **아래 `ssid_filter` 설명은 `passive=false`(액티브 bgscan)일 때만 적용된다.** `passive=true`(기본)면 probe request를 보내지 않으므로 `ssid` 인자 자체가 명령에서 빠진다.
>
> `ssid_filter=true`이면 bgscan이 conf 기본 ssid를 directed probe한다. `iw scan`의 `ssid` 인자는 directed probe 대상이라, non-hidden SSID는 probe 없이 beacon으로도 잡히지만 **hidden SSID는 directed probe가 있어야 발견**된다. 따라서 `roaming.extra_ssids`(§11.4)는 명시적 로밍 후보로서 `ssid_filter` 값과 **무관하게 항상 directed probe**에 포함된다 — `ssid_filter=false`로 두어도 extra SSID(hidden 포함)는 누락되지 않는다. ssid_filter on/off의 스캔 시간·통신 영향 차이는 작고, 실제 비용은 `freq_filter`(채널 수)가 좌우한다.

> bgscan은 `wpa_state==COMPLETED`(연결됨)일 때만 `iw <iface> scan`을 수행한다 — 미연결 시엔 wpa_supplicant의 재연결 스캔/association과 라디오 경합을 피하려 skip.

> 스캔 파라미터는 **매 스캔 직전에 다시 읽는다** — `ssid`/`freq`는 `wpa_supplicant-<iface>.conf`에서, `interval`/`ssid_filter`/`freq_filter`는 이 JSON `bgscan` 블록에서. 런타임에 무선설정을 재적용하거나 JSON을 바꾸면 bgscan 재시작 없이 **다음 스캔부터 새 값으로 스캔**한다(읽기 실패 시 직전 값 유지). 연결 상태 확인(`wpa_cli`)도 매 tick이 아니라 스캔 주기 도래 시에만 수행한다.

### 11.4 roaming - 로밍 알고리즘

| 키 | 타입 | 기본값 (mlan0/mlan1) | 설명 |
|----|------|---------------------|------|
| `use_signal_avg` | bool | `true` | 평균 신호 사용 여부 |
| `DEFAULT_TH_2G` | int | `-75` | 2.4GHz 로밍 RSSI 임계값 (dBm) |
| `DEFAULT_TH_5G` | int | `-75` | 5GHz 로밍 RSSI 임계값 (dBm) |
| `DIFF_TH` | int | `8` | 로밍 결정 RSSI 차이 (dB) |
| `CHECK_INTERVAL` | int | `1` / `3` | 로밍 체크 주기 (초). 판정 입력 link.json 이 ~1s 갱신이라 1 미만은 실익 없음 |
| `SCAN_NO_RESULT_SLEEP` | int | `3` | 스캔 결과 없을 때 대기 (초) |
| `ROAM_SUCCESS_SLEEP` | int | `3` / `2` | 로밍 성공 후 대기 (초) |
| `enabled` | bool | mlan0 `true` / mlan1 `false` | 로밍 데몬(`wifi_roam@<iface>`) 활성화. `wifi_apply_enabled.sh`가 systemd enable/disable로 동기화. **mlan0 로밍이 기본 활성화됨** |
| `extra_ssids` | array[str] | `[]` | conf 기본 ssid 외 추가 로밍 후보 SSID 목록. 빈 배열=기존 단일 SSID 동작(무회귀) |
| `generate_network_blocks` | bool | `false` | 모드 결정자. `false`=모드B(단일 블록, cross-SSID는 외부 `wifi connect`만, `extra_ssids` 무시), `true`=모드A(다중 network 블록 + `select_network` cross-SSID). 기본 `false`로 기존 단일 SSID 동작 무회귀 |
| `ROAM_CROSS_FAIL_RETRY_COUNT` | int | `2` | 모드A cross-SSID(`select_network`) 전환 실패 시 cooldown 없이 즉시 재시도 허용 횟수. 초과 시 지수 backoff로 해당 SSID를 후보에서 제외(진동 차단). 모드B에선 미적용 |

> **다중 SSID 로밍 (`extra_ssids`)**: wpa_supplicant conf는 단일 `network` 블록을 유지하고, `extra_ssids`에 적은 SSID도 스캔·로밍 후보에 포함한다. `wifi_roam.py`(자동 데몬)와 `wifi <iface> roam`(`passive_roam.py`) 모두 적용된다.
> - **전제**: `extra_ssids`는 현재 network와 **같은 `psk`/`key_mgmt`를 공유**해야 한다. 전환은 conf의 `ssid=`만 교체(`wifi <iface> connect`)하므로 자격증명이 다르면 인증에 실패한다.
> - **목록 작성**: `extra_ssids`에는 로밍 대상 SSID를 **모두** 나열한다. `wifi connect` 전환 시 conf의 `ssid=`가 바뀌므로, 현재 라이브 SSID는 자동으로 후보에 유지되지만 그 외 대상(원래 기본 SSID 포함)으로 복귀하려면 그 SSID도 `extra_ssids`에 있어야 한다.
> - **전환 방식**: 후보 SSID가 현재 연결 SSID와 같으면 `wpa_cli roam <bssid>`(무중단), 다르면 `wifi <iface> connect <ssid>`(conf `ssid=` 교체 → `reconfigure` → `reassociate`, **짧은 재연결 끊김** 발생. freq는 넘기지 않아 `scan_freq` 보존).
> - **스캔 발견**: `bgscan.passive=false`면 hidden SSID도 평시에 directed probe한다. 기본 `passive=true`에서는 hidden SSID를 못 보지만, 다중 freq 로밍 조건에서는 `wifi_roam.py`가 설정 채널 전체를 directed active scan하므로 이를 보완한다. 단일 freq hidden 배포는 `STAGED_SCAN.home_passive=false`를 사용한다.
> - **채널 전제**: `extra_ssids` AP는 현재 `scan_freq` 대역 안에 있어야 한다. `wifi_roam.py`는 conf의 `scan_freq`로 스캔·필터하므로 다른 채널의 SSID는 후보가 되지 않는다. (SSID 전환 시 `wifi connect`에 freq를 넘기지 않아 `scan_freq`는 보존된다.)
> - **AP 선택 (v1 한계)**: 다른 SSID 전환은 `wifi connect <ssid>`로 **BSSID를 지정하지 않는다**. 같은 extra SSID에 AP가 여럿이면 로밍 알고리즘이 고른 best AP가 아닌 다른 AP에 붙을 수 있다. (같은 SSID 로밍은 `wpa_cli roam <bssid>`로 BSSID를 지정하므로 해당 없음.)

> **로밍 판정 스캔**: 현재 링크 RSSI가 임계값 아래로 떨어지면 주파수 구성에 따라 분기한다.
> 1. **단일 freq = 현재 채널**: `home_passive=true`면 홈채널 passive scan을 수행한다. 현재 AP 외 후보 beacon을 못 받았을 때만 같은 채널 directed active scan으로 재확인한다. `home_passive=false`면 처음부터 directed active 1회다.
> 2. **다중 freq**: 홈 passive와 `ap.log` cache 판정을 건너뛰고 `iw scan freq <scan_freq 전체> ssid <allowed>` directed active를 1회 수행한다.
> 3. **scan_freq 미설정**: 안전장치로 전대역 active scan 1회를 수행한다.
>
> `ap.log`/bgscan RSSI는 최대 bgscan interval만큼 과거 값일 수 있으므로 **최종 로밍 후보 판정에는 사용하지 않는다.** bgscan은 평상시 BSS 테이블 충전과 roam backoff hint 용도로 유지된다.
>
> **active/passive scan 결과 freshness**: `iw scan`과 이어지는 `wpa_cli scan_results`는 둘 다 누적 BSS cache를 출력할 수 있다. 로밍 데몬은 `iw` 출력의 `last seen` age가 이번 scan 실행시간+1초 이내인 BSSID 집합을 만든 뒤, `scan_results`에서도 그 BSSID만 사용한다. freshness를 증명할 수 없으면 누적 테이블로 판단하지 않고 해당 tick을 실패 처리한다.
>
> **RSSI baseline 통일**: 로밍 판정의 `DIFF_TH` 비교에서 현재 AP RSSI는 위 스캔 결과에서 **자기 BSSID 항목을 찾아** 사용한다. 종전에는 현재 AP는 `iw station dump`의 `signal_avg`(평활값), 후보는 `wpa_cli scan_results`(순간값)로 **서로 다른 소스를 직접 뺐기 때문에** diff에 편향이 있었다. 스캔에서 자기 BSSID를 못 찾은 경우에만 `signal_avg`로 폴백한다.
>
> ⚠️ `wifi_roam.py`의 판정 결과는 메모리에서 처리하고 후보 내역은 syslog에 남긴다. `wifi_logger_scan`이 scan-completed 이벤트에 반응해 `ap.log` 블록을 남길 수 있지만, 이 블록은 자동 로밍 의사결정 입력이 아니다.

#### STAGED_SCAN - 단계형 스캔

`.<iface>.roaming.STAGED_SCAN` (SIGHUP 런타임 반영 — 재배포/재시작 없이 적용)

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `enable` | bool | `true` | 단계형 스캔 ON/OFF. **`false`면 종전 단일 액티브 스캔 경로로 회귀**(무회귀 폴백) |
| `skip_redundant_active` | bool | `true` | 단일 freq에서 `home_passive=true`이고 홈 passive scan이 **현재 AP 외 후보를 실제로 봤으면** 추가 active fallback을 스킵. `home_passive=false`는 첫 scan 자체가 active이므로 이 값과 무관하게 1회만 수행 |
| `home_passive` | bool | `true` | 단일 freq 홈채널 모드. `true`=passive, `false`=directed active. 다중 freq에서는 무시되고 전체 directed active 수행 |

> 필드에서 단계형 스캔이 문제를 일으키면 `enable: false` + SIGHUP(`systemctl kill --kill-who=main -s SIGHUP wifi_roam@mlan0`)으로 **재배포 없이 즉시 종전 동작으로 되돌릴 수 있다.**
>
> `skip_redundant_active`는 단일 채널 passive 모드에서만 사용된다. 홈 passive scan이 현재 AP 외 같은 SSID 후보를 실제로 관측했다면 active 재확인을 생략한다. `home_passive=false`와 다중 freq에서는 이미 directed active scan 1회로 완결되므로 이 옵션을 평가하지 않는다.

#### GOOD_SIGNAL_RESET_GATE - good-signal 리셋 게이트

`.<iface>.roaming.GOOD_SIGNAL_RESET_GATE` (SIGHUP 런타임 반영 — 재배포/재시작 없이 적용)

메인루프의 **신호 양호 분기**(`rssi >= 임계`)는 후보없음 지수 backoff의 streak를 **무조건 리셋**한다. 그런데 정체 로그 18.85h 재생 실측에서 **오탐 리셋 662건 중 624건(94%)이 이 분기**였고 그중 **623건이 Δ0dB** — 임계 바로 위에서 진동할 뿐 위치는 안 변한 경우다. 리셋되면 다음 악화에 backoff가 시작값(3초)부터 다시 올라가 스캔이 폭증한다. 이 게이트는 **위치가 실제로 변했을 때만** 리셋을 허용한다.

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `enable` | bool | `true` | `true`면 직전 리셋 시점 대비 `|Δrssi| >= 2dB`일 때만 리셋하고, 결합 후 40초 동안은 게이트를 우회한다. `false`면 종전대로 무조건 리셋 |

`delta_db=2`, `post_roam_grace_sec=40`은 실측에 근거한 **코드 고정 정책**이며 JSON 옵션이 아니다. 임계값 후보(1/2/3/5dB)의 재생 결과가 사실상 같고, 결합 직후 RSSI ramp가 약 25초 관측되어 현장 튜닝 이득 없이 오설정 위험만 만드는 두 키를 제거했다.

**시뮬레이션 결과**(로그 재생):

| 대상 | 스캔 | 변화 | 추가 지연 |
|---|---|---|---|
| 정체 로그 (18.85h) | 3377 → 1417 | **−58.0%** | 136건 중 2건, 최대 6.8초 |
| 이동 로그 (71개, 90.1h) | 8276 → 8269 | **−0.0%** | **0건** |
| 정체 구간 오탐 리셋 | 662 → 1 | −99.8% | — |

스캔 airtime duty 5.44% → 2.28%. **다중 단말 환경에서 효과가 커진다** — 액티브 스캔 1회의 probe airtime이 온타겟 실측 약 4.6ms(ProbeReq 4 + ProbeResp 8, 6Mbps 가정)이므로, 스캔 주기가 3초에서 30초로 늘면 채널 점유가 10배 줄어든다(단말 50대 기준 7.7% → 0.77%).

> **고정 2dB 근거**: 재생 실측상 1/2/3/5가 전부 −58.0~−58.1%로 동일하다(리셋 지점의 Δ가 0dB에 몰려 있어 어떤 임계든 같이 걸린다). RSSI 1dB 양자화의 노이즈 여유를 위해 2dB로 고정했다.
>
> **`post_roam_grace_sec`가 필요한 이유**: 결합 직후 RSSI는 25초에 걸쳐 **12~14dB 하강**한다(attach ramp — 같은 구간 TX rate가 불변이므로 실제 링크 열화가 아니라 측정 램프다). 그 큰 Δ를 "이동"으로 읽으면 게이트가 사실상 무력화되므로 그 창에서는 우회한다.
>
> **런타임 A/B**: `wifi <if> roam gate on|off`로 즉시 토글되고(SIGHUP), `wifi <if> roam gate`로 현재 enable과 고정 정책값을 볼 수 있다. 켠 뒤 로그의 `good-signal streak reset (...) — 이전 N회 억제` 라인으로 억제가 실제로 일어나는지 확인한다.
>
> **한계**: 위 시뮬레이션은 1초 raw RSSI 로그 재생 결과다. 실기 메인루프의 RSSI 샘플 간격은 상황에 따라 2~30초(good-signal이면 `CHECK_INTERVAL`, 로밍 컨디션이면 backoff)로 흔들리므로 **이동 시 재탐색 지연은 실기에서 재확인이 필요**하다. 설계의 2층 판정(60초 peak-to-peak ≥ 5dB)은 이 파일의 RSSI 이력이 `PREDICTIVE_ROAM.enable` 게이트 안에서만 쌓여(출하 기본에서 비어 있음) 후속 범위로 미뤘다 — 1층(Δ)만으로 위 효과의 거의 전부를 얻는다.

#### PREDICTIVE_ROAM - 예측 로밍

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `enable` | bool | `false` | 예측 로밍 활성화 (**plain-mode화로 기본 off**, 구 기본 true) |
| `threshold_boost` | int | `5` | 신호 하락 추세 시 **로밍 컨디션 진입 임계** 상향 (dB) — 더 일찍 후보 탐색 시작 |

> **falling trend의 두 가지 효과**: ① 컨디션 진입 임계가 `threshold_boost`(기본 5dB)만큼 상향되어 더 일찍 탐색을 시작하고, ② 후보 판정의 `DIFF_TH`가 **3dB 완화**된다(`effective_diff_th = max(1, DIFF_TH - 3)` — 하한 1dB 클램프로 음수/0 diff 후보 통과를 차단).
| `trend_window_size` | int | `5` | 추세 분석 윈도우 크기 (샘플 수) |
| `trend_history_max_age` | int | `30` | 추세 기록 최대 보존 시간 (초) |

#### PING_PONG_PREVENTION - 핑퐁 방지

| 키 | 타입 | 기본값 | 설명 |
|----|------|---------------------|------|
| `enable` | bool | `true` | 핑퐁 방지 활성화 |
| `window` | int | `20` | 감시 윈도우 (초) |
| `max_roams_in_window` | int | `3` | 윈도우 내 최대 로밍 횟수 |
| `detection_time` | int | `5` | A→B→A 왕복 감지 시간 (초) |

> **제거됨 (2026-07-31 노브 감사 D1, `knob_audit_2026-07.md`)**: `LOAD_BASED_ROAM` ·
> `ADAPTIVE_INTERVAL` · `POST_ROAM_ARP_OPTIMIZATION`(+`PEER_WARMUP`) 은 출하 기본 off 로
> 사용 실적이 없어 코드·템플릿·스키마에서 제거됐다. 기존 기기 JSON 에 남은 해당 키는
> 데몬이 읽지 않으므로 무해(stale)하다. `PREDICTIVE_ROAM` 은 2층 판정 계획의 RSSI 이력
> 소스라 보류로 남았다.

### 11.5 rate_adapt - FW Rate Adaptation (per-iface override)

**사용 스크립트**: `wifi_init.sh`

mlan0 / mlan1에 개별 적용. 코드상 `global.rate_adapt`로 fallback하도록 되어 있으나 **현재 JSON global에는 `rate_adapt` 블록이 없으므로**(§1.1 참조) 실질적으로는 인터페이스별 값 또는 내장 기본값만 유효하다.

| 키 | 타입 | 기본값 (현재 JSON) | 설명 |
|----|------|------------------|------|
| `mode` | int | `1` | `0`=legacy, `1`=SR(Success Rate) |
| `low_thresh` | int | `50` | SR 하한 (%). `255`(0xff)=dynamic |
| `high_thresh` | int | `80` | SR 상한 (%) |
| `interval_ms` | int | `100` | 평가 주기 (ms) |

**우선순위**: `.mlanN.rate_adapt.<key>` > `.global.rate_adapt.<key>` > 내장 기본값

```json
"mlan0": {
    "rate_adapt": { "mode": 1, "low_thresh": 50, "high_thresh": 80, "interval_ms": 100 }
},
"mlan1": {
    "rate_adapt": { "mode": 1, "low_thresh": 40, "high_thresh": 70, "interval_ms": 200 }
}
```

> **적용 시점**: `apply_iface_radio_defaults()` 안에서 association 전에 호출. 각 iface 초기화 시 mlanutl rate_adapt_cfg로 전달.

### 11.6 mcs_tier - MCS Tier 능력 제한

**사용 스크립트**: `wifi_init.sh`

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `enabled` | bool | `false` (양쪽) | mcstiercfg 적용 활성화 |
| `ht` | string | mlan0 `"7"` / mlan1 `""` | HT(11n) 인자. 예: `"7"`(1x1), `"15"`(2x2). `""`이면 건너뜀 |
| `vht` | string | mlan0 `"7"` / mlan1 `""` | VHT(11ac) 인자. 예: `"7"`, `"8"`, `"9"`. `""`이면 건너뜀 |
| `he` | string | mlan0 `"both 7"` / mlan1 `""` | HE(11ax) 인자. 예: `"7"`, `"9"`, `"11"`, 또는 `"both 7"`. `""`이면 건너뜀 |

> **인터페이스별 기본값 주의**: 출하 JSON은 mlan0에만 tier 값(`ht="7"`, `vht="7"`, `he="both 7"`)이 설정돼 있고 mlan1은 모두 빈 문자열(`""`)이다. 단 `mcs_tier.enabled`는 **양쪽 모두 `false`**이므로 기본 상태에서는 실제로 적용되지 않는다(값만 preset).

**적용 시점**: 부팅 시 `wifi_init.sh`의 `apply_iface_radio_defaults`에서 association 전에 1회 적용

- `enabled: false`(기본)이면 mcstiercfg를 실행하지 않음 (FW 기본값 사용)
- 각 키는 **문자열**로 관리. 빈 문자열(`""`)이면 해당 prefix를 명령에서 제외
- 값 안의 토큰은 공백으로 구분되어 mcstiercfg 인자로 **그대로 전달**됨
  - `he: "7"` → `mlanutl <iface> mcstiercfg he 7`
  - `he: "both 7"` → `mlanutl <iface> mcstiercfg he both 7`
- 유효성 검사를 하지 않으므로 mcstiercfg가 지원하는 문법은 자유롭게 사용 가능 (실패 시 에러 로깅만 수행)
- 인터페이스별 독립 설정 가능 (mlan0과 mlan1에 다른 tier)
- 하위호환: 기존 int 값(`"ht": 7`)도 문자열로 읽혀 동일하게 동작

> **주의**: VHT는 FW 내부에 MCS 7 하한(floor)이 있어 tier 7이 사실상 최소값.

```json
"mcs_tier": {
    "enabled": true,
    "ht": "7",
    "vht": "7",
    "he": "both 7"
}
```

### 11.7 on_connect - AP 연결 후 명령 실행

**사용 스크립트**: `wifi_event`

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `enabled` | bool | mlan0 `true` / mlan1 `false` | on_connect 기능 활성화 |
| `commands` | array | `[]` (양쪽) | AP 연결/로밍 후 순서대로 실행할 명령 목록. 실패해도 중단하지 않고 로깅만 수행 |

> `enabled`와 `commands`는 `wifi_event@<iface>` 시작 시 한 번 읽어 캐시한다. 실행 중 JSON을 변경했다면 `systemctl restart wifi_event@<iface>` 후 반영된다.

### 11.8 thermal_mgmt - FW Thermal 관리

**사용 스크립트**: `wifi_init.sh`

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `thermal_mgmt` | bool | `true` | `.mlanN.thermal_mgmt`. FW thermal management 제어. `true`(기본)=enable, `false`=disable |

**적용 방식**:
- 부팅 시 `wifi_init.sh` → `apply_iface_thermal_mgmt()`가 insmod 직후(인터페이스 생성 후) per-interface 1회 적용
- `mlanutl <iface> hostcmd /lib/firmware/cts/config/debug.conf {enable|disable}_thermal_mgmt` 호출
- hostcmd 정의(`debug.conf`): `CmdCode 0x008b`, `SUBID 0x113`(THERMAL_MANAGEMENT), `Value 1=enable / 0=disable`
- 키 누락/invalid → factory default(`enable`). 명시적 `false`일 때만 `disable`
- `.mlanN.enabled=false`이면 해당 인터페이스는 건너뜀 (TXPWRLIMIT과 동일 동작)

### 11.9 checker - WiFi 체커 + Reboot 정책 (per-iface)

`mlan0.checker` / `mlan1.checker`. 최상위에서 인터페이스별로 이동했다. 키·기본값·동작 흐름은 [§4 checker](#4-checker---wifi-체커--reboot-정책-인터페이스별-mlannchecker) 참조. `enabled`(mlan0 `true` / mlan1 `false`)로 데몬 활성화, `RECONFIGURE_GRACE_SEC`(20)로 재연결 과도기 사다리 억제.

### 11.10 arping - ARP 연결 감시 (per-iface)

`mlan0.arping` / `mlan1.arping`. 최상위에서 인터페이스별로 이동했다. 키·기본값은 [§6 arping](#6-arping---arp-연결-감시-인터페이스별-mlannarping) 참조. `enabled`는 양쪽 기본 `false`.

---

## 12. snmp - SNMP 조건부 기동 + 트랩

**사용 스크립트**: `wifi_apply_enabled.sh`(snmpd enable/disable 동기화), `wifi_services.sh`(start), `wifi_event.sh`(트랩 송신)

SNMP는 **기본 off(opt-in)**이다. `snmp.enabled=true`일 때만 `wifi_apply_enabled.sh`가 `snmpd.service`를 systemd enable/disable로 동기화하고 `wifi_services.sh`가 기동한다(UDP 161 노출 → 보안상 opt-in). 트랩은 별도로 `trap.enabled=true`일 때 `wifi_event.sh`가 무선 링크/채널 이벤트 시 `snmptrap`을 송신한다.

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `enabled` | bool | `false` | `snmpd.service` enable/disable + 기동 마스터 스위치. `true`면 UDP 161 노출 |
| `trap.enabled` | bool | `false` | SNMP 트랩 송신 활성화. `true`면 링크/채널 이벤트 시 `wifi_event.sh`가 `snmptrap` 송신 |
| `trap.dest` | string | `""` | 트랩 수신지. `host` 또는 `host:port` 형식. 빈 문자열이면 트랩 미송신 |
| `trap.community` | string | `"public"` | 트랩 community 문자열 |
| `trap.version` | enum | `"2c"` | SNMP 트랩 버전. `1` \| `2c` |

```json
"snmp": {
    "enabled": false,
    "trap": { "enabled": false, "dest": "", "community": "public", "version": "2c" }
}
```

---

## mlan0 vs mlan1 기본값 차이

대부분 동일하지만 다음 값이 다르다 (현재 출하 JSON 기준):

| 설정 경로 | mlan0 | mlan1 | 이유 |
|-----------|-------|-------|------|
| `enabled` | `true` | `false` | **mlan1은 기본적으로 초기화되지 않음** (radio setup·bridge enable skip) |
| `STANDARD` | `ax` | `ac` | mlan1은 11ax 미지원 |
| `roaming.CHECK_INTERVAL` | `1` | `3` | mlan0은 주 채널이라 1초 주기로 빠르게 감지(입력 link.json 갱신 한계), mlan1은 3초 |
| `roaming.ROAM_SUCCESS_SLEEP` | `3` | `2` | 로밍 성공 후 정착 대기 — mlan0=3초, mlan1=2초 |
| `roaming.enabled` | `true` | `false` | mlan0 로밍 기본 활성화 |
| `bgscan.enabled` | `true` | `false` | mlan1 비활성 인터페이스 |

> ※ `PING_PONG_PREVENTION` 파라미터는 mlan0/mlan1 **동일**(window=20, detection_time=5)이라 이 표에 없다 — 과거 가이드의 30/60·10/30 병기는 템플릿과 불일치했던 오기.
| `checker.enabled` | `true` | `false` | mlan1 비활성 인터페이스 |
| `on_connect.enabled` | `true` | `false` | mlan1 비활성 인터페이스 |
| `logger.enabled` | `true` | `false` | mlan1 비활성 인터페이스 |
| `mcs_tier.ht` / `.vht` / `.he` | `"7"` / `"7"` / `"both 7"` | `""` / `""` / `""` | mlan0만 MCS tier 제한값 설정(단 `mcs_tier.enabled`는 양쪽 `false`) |

> **더 이상 차이 아님**: `PREDICTIVE_ROAM.enable` 은 양쪽 `false` 로 동일하다. `LOAD_BASED_ROAM`/`ADAPTIVE_INTERVAL`/`POST_ROAM_ARP_OPTIMIZATION` 은 감사 D1(2026-07-31)로 **제거**됐다(§11.4 제거됨 참조).

---

## 설정 로드 우선순위

```
1. 환경변수 (일부 스크립트, 예: wifi_arping.sh의 THRESHOLD)
2. wifi_init_conf.json 값
3. 스크립트 내장 기본값 (JSON 없거나 jq 미설치 시)
```

---

## 부록: 서비스 라이프사이클 및 Reboot 경로

### 부팅 흐름

```
sysinit.target
    ↓
multi-user.target ──────→ 🟢 로그인 가능 (getty, sshd, serial-getty)
    ↓ (After=, 비블로킹)
wifi-stack.target  ────→ WiFi 스택 기동
    ├─ wifi_init.service           (Type=oneshot, 모듈 로드)
    ├─ wifi_apply_enabled.service  (JSON ↔ systemd enable 동기화)
    └─ 자식 unit들 (wifi_checker@, wifi_logger@, wifi_bridge@ 등)
```

### 설계 원칙

**1. 로그인 경로와 WiFi 스택의 분리**
- `wifi-stack.target`에 `DefaultDependencies=no` + `After=multi-user.target` 설정
- `multi-user.target` 도달(로그인 가능)이 WiFi 스택 기동을 기다리지 않음
- `wifi_init` hang 중에도 시리얼/SSH 로그인 가능

**2. wifi_checker 독립 실행**
- `wifi_checker@.service`는 `After=sysinit.target`만 보유 (wifi_init 의존성 제거)
- `wifi_init`이 모듈 로드 실패/D state hang이어도 `wifi_checker`가 독립적으로 실행되어 상태 감시
- `PartOf=wifi_init.service`는 유지 → 수동 `systemctl stop wifi_init` 시 함께 stop (관리 편의)

**3. 다중 reboot 경로 (Defense in depth)**

| 경로 | 트리거 | 감지 시간 | 비고 |
|------|--------|-----------|------|
| **wifi_checker@mlan** (primary) | `/sys/class/net/mlanN` 부재 × `LIMIT_CNT` 초과 | 30~60초 | 가장 빠름 |
| **wifi_init.service OnFailure** (backup) | `StartLimit` 소진 (180s × 3회) | 약 9.5분 | `wlan_emergency_reboot.service` 호출 |
| **temperature 초과** | emerg 온도 연속 감지 | 설정에 따라 | §5 참조 |
| **HW watchdog** (최종 안전망) | watchdog 데몬 kick 불가 | 30초 | iMX SoC 내장 |

### wlan_reboot_policy.sh 3단계 폴백

모든 reboot 요청은 `wlan_reboot_policy.sh`를 거쳐 uptime/cooldown 정책 검증 후 실행된다:

```
do_reboot():
  1차: /sbin/reboot                    → 10초 대기 (graceful, systemd shutdown.target)
  2차: /sbin/reboot -f                  → 5초 대기 (forced, reboot(2) 직접 호출)
  3차: echo b > /proc/sysrq-trigger    (kernel emergency_restart)
  → HW watchdog 30초가 최종 안전망
```

- **3차 sysrq 경로**는 `device_shutdown()`을 건너뛰어 D state 드라이버 락과 무관하게 HW reset 수행
- 각 단계는 background 실행으로 block 시에도 다음 단계로 진행

### 연관 JSON 설정 (요약)

reboot 동작을 조정하는 주요 키들 (상세는 §4 checker 참조):

| 키 | 기본값 | 역할 |
|----|--------|------|
| `mlanN.checker.LIMIT_CNT` | `5` | 인터페이스 부재 허용 횟수 |
| `mlanN.checker.MIN_UPTIME_SEC` | `30` | **커널 부팅** 후 reboot 거부 구간 (초, `/proc/uptime` 기준). 데몬 uptime이 아님 |
| `mlanN.checker.MAX_REBOOT_COUNT` | `3` | cooldown 윈도우 내 최대 reboot |
| `mlanN.checker.REBOOT_COOLDOWN_SEC` | `300` | reboot 카운트 리셋 윈도우 (초) |

### 업그레이드/제거 시 유닛 상태 동기화

- `postinst`: 구 `multi-user.target.wants/wifi_*` 링크 정리 후 `wifi-stack.target`으로 재등록
- `prerm`: `wifi-stack.target`부터 stop (PartOf 전파로 자식 일괄 정지)
- `postrm`: `wifi-stack.target.wants/` 고아 링크 정리, `wlan_emergency_reboot.service` disable
