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
│   ├── ping_monitor    #   ping 모니터 서비스 제어
│   └── fw_watch        #   드라이버 wedge 감시·복구
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
│   ├── periodic_roam   #   DEPRECATED 호환 블록(강제 off)
│   ├── bgscan          #   백그라운드 스캔
│   ├── roaming         #   로밍 알고리즘
│   ├── mcs_tier        #   MCS tier 능력 제한 (mcstiercfg)
│   ├── on_connect      #   AP 연결 후 실행 명령
│   ├── checker         #   wifi_checker + reboot 정책 (구 최상위 checker에서 이동)
│   └── arping          #   ARP 연결 감시 + sweep (구 최상위 arping에서 이동)
└── mlan1               # mlan1 인터페이스 설정 (mlan0과 동일 구조)
    ├── logger, net_rx, rate_adapt, periodic_roam(DEPRECATED), bgscan,
    ├── roaming, mcs_tier, on_connect, checker, arping ...  # mlan0과 동일 구조
    └── (net_rx → PCIE9098_1 / SD9098_1)
```

> **구조 변경 요약** (이전 연동/구 가이드 대비): `checker`·`arping`이 최상위 → **인터페이스별**(`mlan0.checker`, `mlan1.arping` 등)로 이동했다. `global`에서 `FW_NAME`/`MFG_MODE`가 제거되고 펌웨어는 `BOARD_TYPE`+`BUS_TYPE`+`BLUETOOTH.enable`로 자동 선택된다. `global.rate_adapt` 블록은 제거되어 실질적으로 per-iface(`mlanN.rate_adapt`)만 존재한다. `snmp` 섹션이 신규 추가되었다. 인터페이스별 스칼라 키(`connect_threshold`, `mgmt_hex_dump_enable`, `thermal_mgmt`, `enabled`, `Frequency` 등)는 트리 간결성을 위해 생략했다 — §11 참조.

---

## 1. global - 드라이버 초기화

**사용 스크립트**: `wifi_init.sh`

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `BOARD_TYPE` | detected enum (read-only) | N/A (detected) | 실제 SoC에서 `imx93` 또는 `imx8mm`로 정규화된다. 설치·Factory Reset·정상 부팅이 같은 감지 helper를 사용하며 수동 변경은 다음 부팅에 복구된다. 실제 SoC를 확인할 수 없으면 Wi-Fi는 module load 전에 실패한다. |
| `BUS_TYPE` | detected enum (read-only) | N/A (detected) | 버스 종류. `sdio`\|`pcie`. **fw_name 자동선택**, `mod_para` 블록 prefix(`sdio`→`SD9098_N`, `pcie`→`PCIE9098_N`), 검증 패턴 결정에 사용. 하드웨어와 반드시 일치 |
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

### 1.1 rate_adapt 안내

> `rate_adapt`는 인터페이스별 블록만 사용한다. 섹션이 존재하면 값 4개 필드(`mode`/`low_thresh`/`high_thresh`/`interval_ms`)를 모두 제공해야 하며,
> partial 값에 global 또는 코드 기본값을 섞지 않는다. 상세 계약은 [§11.5](#115-rate_adapt---fw-rate-adaptation)를 참고한다.

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
> - **쓸 MAC이 하나도 없으면**(dynamic peer 없음 + base 없음) `wbridge.mac_clone_require_peer=true`(기본)일 때 bridge iface `.link`의 `MACAddress`를 **제거**해 드라이버 기본 MAC으로 되돌린다. 아래 [클론 MAC 잔재](#클론-mac-잔재--mac_clone_require_peer) 참고.

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
| `mac_clone_require_peer` | bool | `true` | `mac_mode=dynamic` 전용. 클론 MAC을 **유선 peer를 실제로 찾은 부팅에서만** 유지한다. 아래 [클론 MAC 잔재](#클론-mac-잔재--mac_clone_require_peer) 참고 |
| `ip_discovery` | bool | `false` | dynamic MAC 모드에서 MAC 확보 후 클라이언트 IP까지 탐색할지. `false`면 MAC만 확보 후 즉시 종료(부팅 가속, host route 미등록). `true`면 IP 탐색(passive→unicast→sweep) + host route(`/32 dev eth0` src=무선IP) + peer neigh(permanent) 자동 등록. 양방향 라우팅 완성에는 `peer_route.enabled=true` **또는 `moal.local_hairpin=1`** 필요 (등록 게이트가 둘 중 하나면 열림) |
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

#### 기배포 기기 마이그레이션 — 공식 경로 (결정, #257)

`DEBIAN/postinst`의 cpchk·json_merge는 **사이트 값 보존**이 목적이라, 위 3종 토글
(`peer_route.enabled`/`ip_discovery`/`arp_ignore_always.enabled`)을 출하 기본
(false/false/true)에서 mlan0-IP(옵션 X) 운용값(true/true/false)으로 바꾸는 변경을
**패키지 업그레이드로는 전파하지 않는다**(의도된 사각 — 업그레이드가 사이트 값을 덮지
않게 하기 위함). 기배포 기기 전환의 공식 경로는 아래 **명시적 opt-in 운영자 명령** 두
가지다 — 둘 다 원자적 기록 + 백업, 다음 부팅부터 적용:

- **온디바이스**: `wifi {0|1} br profile dual apply`(moal 권장) 또는 `peer-route apply`
  (엔진 무관). 3종을 검증된 조합으로 한 번에 json에 기록(개별 편집의 함정 조합 방지 —
  `_bridge_profile`가 백업 후 원자 `mv`). 적용 후 `wifi {0|1} br status`로 정합성 확인.
- **호스트 번들**: `bd_provision.sh --profile b`(wlan-opc#88). 3종 토글에 더해
  `.network`(Address/GW)·`timesyncd.conf`(NTP)·wpa 자격증명까지 한 기기에 원자 적용
  (plan/apply/verify/rollback + 백업). 수백 대 규모 일괄 전환용.

**결정**: 버전 게이트 postinst 자동 마이그레이션 훅은 만들지 않는다 — (a) "사이트 값 보존"
원칙과 충돌(업그레이드가 사용자 값을 덮음), (b) "부팅/업그레이드 시 자동 설정변경 금지"
지침과 충돌. 위 두 opt-in 경로만 두 원칙을 동시에 만족한다. (#91 NTP 결정과 동일 패턴 —
공식 반영 경로 = 프로비저닝 스크립트.)

#### 클론 MAC 잔재 — `mac_clone_require_peer`

`mac_mode=dynamic`은 유선 peer(BD에 연결된 PC/PLC)의 MAC을 bridge 인터페이스 `.link`에 클론한다. 이 `.link`는 **다음 `insmod`까지 남기 때문에**, 같은 PC로 여러 기기를 차례로 설정하면 각 기기에 같은 MAC이 기록되고 PC를 떼도 그대로 유지돼 **여러 기기가 동일 MAC으로 부팅**하는 문제가 있었다.

| `mac_clone_require_peer` | 유선 peer 없는 부팅에서의 동작 |
|--------------------------|--------------------------------|
| `true` (기본) | `base` 있으면 → `base` 기록. `base` 없으면 → `.link`의 `MACAddress` **제거**(드라이버 기본 MAC). 클론 잔재가 남지 않는다 |
| `false` | 종전 동작 — 직전 클론이 `.link`에 남아 다음 부팅에도 재적용된다 |

- **반영 시점은 같은 부팅**이다. `wifi_init.sh`의 MAC 결정 블록은 `try_insmod`보다 **앞**에서 실행되므로, 정정된 `.link`를 udev가 netdev 생성 시 바로 적용한다.
- `true`면 매 탐색 전에 `/tmp/eth0_client_mac`도 지운다. `wired_mac_ip_get.py`는 peer를 찾았을 때만 이 파일을 쓰고 실패해도 지우지 않아, `systemctl restart wifi_init` 시 유선을 뽑았는데도 직전 실행의 peer MAC이 읽히기 때문이다.
- 대상은 **bridge 인터페이스**(`wbridge.bridge_iface`)뿐이다. dynamic 클론은 이 인터페이스에만 적용되므로 secondary/eth0의 `.link`는 건드리지 않는다. 다만 `bridge_iface`를 mlan0↔mlan1로 **바꾼 경우**, 이전 bridge 인터페이스에 남은 클론은 이 옵션의 범위 밖이므로 `wifi mac <iface> base <MAC>` 또는 공장 초기화로 정리해야 한다.
- MFG 프로파일(`mfg_mode=1`)에서는 MAC 설정 전체가 skip되므로 이 정정도 수행하지 않는다.
- 관련 구현: `wifi_init.sh`(`apply_final_mac`), `update_mac.sh --clear`, `mac_link_lib.sh`(`mac_render_link_without_address`). 테스트: `update_mac_test.sh` T26~T30.

**공장 초기화의 `.link` 정리** — `factory_reset.sh`는 2단계로 MAC 오염을 제거한다. 목표는 **reset 내부에서 재부팅하기 직전** 어떤 `.link`도 mlan0/mlan1/eth0에 이전 MAC을 강제하지 않게 만드는 것이며, 2단계가 그 사실을 검증한다. 다음 부팅의 `wifi_init.sh`는 보존된 production base 또는 이번 부팅에 새로 검출한 dynamic peer MAC을 의도적으로 다시 기록할 수 있으므로, 부팅 완료 후 `wifi_link_reset.sh --check`가 non-zero인 것만으로 reset 실패로 판정하면 안 된다. 부팅 후에는 선택된 MAC 입력·managed `.link`·실제 인터페이스 MAC이 일치하는지 검증한다.

| 단계 | 동작 |
|------|------|
| 1 | 패키지 소유 `.link` 3개를 `/opt/wlan/config/systemd/network/`의 템플릿(=`MACAddress` 없음)으로 덮어쓰기 |
| 2 | **`wifi_link_reset.sh`** — ① `*.link.*` 파생 잔재 일소 ② 패키지 소유분에 남은 `MACAddress` 제거(1단계 실패 대비) ③ 외부 `.link`가 우리 인터페이스의 MAC을 강제하면 **파일 삭제** ④ `.link.d` 드롭인 감지 ⑤ 후조건 검증 + `sync` |

- ①은 `.link.bak`·`.link.bak.N`·`.link.bak.<임의 suffix>`·orphan `.link.tmp.*`를 모두 지운다. 공장 초기화에서 이들은 되살릴 이유가 없는 파생 상태다. (구 `update_mac.sh --reset-backups` 루프를 대체 — 그쪽은 비숫자 suffix 백업을 보존했다)
- ③이 필요한 이유: **udev는 파일명 사전순으로 처음 매칭된 `.link`만 적용**하므로 `10-custom.link` 같은 낮은 번호 파일이 `20-mlan0.link`를 완전히 가린다. 이름이 `.link`로 끝나 ①의 `*.link.*` 글롭에는 걸리지 않는다.
- **삭제 대상은 `OriginalName`으로 우리 인터페이스를 지목하면서 `MACAddress`를 설정하는 `.link`뿐**이다(공백 구분 목록·glob 인식). MAC을 설정하지 않는 `.link`(MTU/WoL 등 운영자 튜닝)와 무관한 인터페이스용 `.link`는 보존한다.
- 판정 불가 2종은 **삭제하지 않고** `logger.crit` + 콘솔 경고만 남긴다 — 오삭제보다 사람 판단이 낫다는 판단. 이때 `wifi_link_reset.sh`는 exit 1이지만 공장 초기화는 계속 진행한다.
  - `OriginalName` 없이 `MACAddress`만 설정하는 `.link` (`Path=`/`Driver=` 매칭은 해석하지 않음)
  - `<name>.link.d/*.conf` 드롭인의 `MACAddress` — 드롭인은 부모 `.link`에 **병합**되므로 템플릿 복원과 무관하게 MAC을 강제한다. 디렉터리라 ①의 `rm`으로도 지워지지 않는다(부모 `.link`가 없는 고아 드롭인은 systemd가 무시하므로 대상 아님).
- 진단: `wifi_link_reset.sh --check`는 아무것도 바꾸지 않고 강제 MAC이 남았는지만 검사한다(남으면 exit 1). 이 명령은 Factory Reset 내부의 **재부팅 전 청결성 검사**이며, `wifi_init`이 최종 MAC 계획을 적용한 부팅 후 상태 검사로 사용하지 않는다. 테스트: `wifi_link_reset_test.sh`

**공장 초기화의 필수 파일 복구 게이트** — FW 설정 4종, `wpa_supplicant@.service`, 두 WPA conf, mlan0/mlan1/eth0 `.network`는 best-effort `cp` 대상이 아니다. 각 파일을 destination과 같은 디렉터리의 temp에 root 소유·규정 모드(WPA 0600, 나머지 0644)로 설치하고 원본과 `cmp`한 뒤 atomic rename한다. WPA/network 및 mod_para/txpower의 `.bak`도 같은 factory 원본으로 재시드하므로 reset 이전 자격증명·IP·FW 설정이 self-healing에서 부활하지 않는다. 설치·sync·rename·최종 내용/권한 검증 중 하나라도 실패하면 reset은 non-zero로 끝나며 reboot하지 않는다. 장시간 버튼 호출자도 이 결과를 우회해 별도 강제 reboot하지 않는다.

Factory Reset이 성공하면 장비가 재부팅되며, 공장 기본 유선 주소는 `eth0=192.168.1.1/24`이다. 재부팅 뒤 `root@192.168.1.1`로 다시 접속해 후조건을 확인한다. 일반 패키지 업그레이드는 현재 active 네트워크 설정을 보존하므로 이 주소는 Factory Reset 시 확정 적용된다. 동일 L2에 초기화 장비를 여러 대 동시에 연결하면 주소 충돌이 발생하므로 한 대씩 격리해 초기화한다.


### 1.3 global.fw_watch - 드라이버 wedge 감시자

**사용 스크립트**: `wlan_fw_watch.sh`, `wlan_reboot_policy.sh`

FW 자동복구가 최종 실패하면 드라이버가 `driver_status=MTRUE` 로 latch 되어 모든 IOCTL 이
거부된다. 이때 netdev 는 그대로 등록돼 있고 operstate 도 `up` 이라 기존 복구 경로 셋이
모두 빗나간다 — `wifi_checker` 의 netdev 소멸 분기는 netdev 가 사라져야 하고, station dump
사다리는 `wpa_state=COMPLETED` 를 요구하며, `wifi_init.service` 의 `OnFailure` 는 유닛이
다시 실행돼야 한다. 이 감시자는 `/proc/mwlan/wifi_status` 를 직접 보고 그 공백을 메운다.

판정은 보수적이다: `wifi_status` 가 **정확히 11**(`WIFI_STATUS_FW_RECOVERY_FAIL`)일 때만
후보로 보고(0~10 은 정상 전이값이며 FW 이벤트가 임의 값을 실을 수 있다), 빈 읽기는 0 이
아니라 판정 불가로 취급하며(rmmod 창), 연속 틱 디바운스를 통과한 뒤에야
`hardware_status` 로 확인한다. `hardware_status` 는 정상 teardown 마다 `NotReady` 가 되므로
1차 신호로 쓰지 않는다.

조치는 재부팅이 아니라 **모듈 리로드 우선**이다. `systemctl restart wifi_init.service` 로
드라이버를 다시 올리고 회복을 확인한 뒤, 그래도 낫지 않을 때만 `wlan_reboot_policy.sh` 로
넘긴다. 정책 호출 시 `--iface` 를 주지 않는다 — `wifi_status` 는 보드 전역 신호라
per-iface 링크 장애 예산(`reboot_policy_mlan0.state`)과 섞이면 안 된다.

| 키 | 타입 | 기본값 | 설명 |
|---|---|---|---|
| `enabled` | bool | `true` | `wlan_fw_watch.service` 활성화. `wifi_apply_enabled.sh` 가 systemd enable/disable 로 동기화 |
| `CHECK_INTERVAL_SEC` | int | `5` | `wifi_status` 폴링 주기 (초) |
| `INITIAL_DELAY_SEC` | int | `60` | 기동 후 첫 판정까지 유예 (초). 부팅 중 FW 다운로드 창을 피한다 |
| `TERM_FAULT_CNT` | int | `3` | `wifi_status=11` 연속 관측 횟수 (기본 15초) |
| `ABNORMAL_FAULT_CNT` | int | `36` | 그 외 비정상 값 연속 관측 횟수 (기본 180초). `0` 이면 이 계층 비활성 |
| `CONFIRM_TIMEOUT_SEC` | int | `3` | `hardware_status` 확인 읽기 제한 (초). adapter config 는 FW 커맨드를 유발하므로 반드시 bounded 로 읽는다 |
| `RELOAD_ENABLED` | int | `1` | `1` 이면 재부팅 전에 리로드를 먼저 시도. **`0` 이면 감지·보고만 하고 아무 조치도 하지 않는다**(재부팅하지 않음) |
| `RELOAD_COOLDOWN_SEC` | int | `900` | 리로드 간 최소 간격 (초). `wifi_init.service` 의 `StartLimitIntervalSec` 보다 길다 |
| `VERIFY_TIMEOUT_SEC` | int | `90` | 리로드 후 회복 확인 제한 (초) |
| `VERIFY_INTERVAL_SEC` | int | `3` | 회복 확인 폴링 주기 (초) |
| `MAX_REBOOT_COUNT` / `REBOOT_COOLDOWN_SEC` / `MIN_UPTIME_SEC` | int | `3` / `300` / `60` | 에스컬레이션 시 `wlan_reboot_policy.sh` 에 전달 |

> MFG 프로파일에서는 감시를 보류한다(`mod_para.conf` 의 `mfg_mode=`). 과열 차단 중에도
> `wifi_logger_temp.sh` 의 `WIFI_STOP_UNITS` 에 포함돼 함께 정지한다 — 차단 도중 리로드가
> 무선 유닛을 되살리는 것을 막기 위해서다.

**리로드가 `wifi_checker` 와 겹치지 않는가 (2026-08-18 실측)** — 감시자가 거는
`systemctl restart wifi_init.service` 는 `PartOf` 전파로 `wifi_checker@` 도 재시작시킨다.
그 사이 netdev 가 사라지므로 checker 의 "netdev 부재" 사다리가 먼저 재부팅을 요청할
여지가 있는지 보드에서 측정했다.

| 항목 | 값 |
|---|---|
| checker 발화 임계 | `ERR_CNT > LIMIT_CNT(5)` = 6틱 × `sleep 5` = **30초** 연속 부재 |
| 실측 netdev 부재 구간 | **5.6초** |
| checker ACTIVE 상태로 리로드했을 때 실측 `ERR_CNT` 최대 | **2** |
| 재부팅 요청 | **0회** (`reboot_policy_mlan0.state` 생성 안 됨) |

마진이 5배 이상이고, 방어가 삼중이다 — 부재 구간이 임계보다 훨씬 짧고, `PartOf` 전파로
checker 가 재시작되면서 `ERR_CNT` 가 0 으로 리셋되며, 재시작된 checker 는 루프 진입 전
lsmod 대기(최대 15초)를 먼저 한다. 따라서 별도의 grace 플래그가 필요하지 않다.

부재가 실제로 30초를 넘는다면 그것은 오탐이 아니라 리로드가 실패했다는 뜻이므로,
그때 checker 가 재부팅을 요청하는 것은 의도된 동작이다.

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
| `roam_announce` | int(`0`\|`1`) | `""` | 로밍/링크업 완료 시 클론 MAC 소스 L2 update(802.2 LLC XID) 브로드캐스트를 공중으로 발사해 상단 유선 스위치 FDB를 새 AP 포트로 즉시 재학습 — 로밍 후 하향 ~2–5s 갭 대응 (wlan-driver-v2#47; 메커니즘 실기 검증 완료, end-to-end 갭 검증은 #235 단계). 빈값=미전달(드라이버 기본 `0`, off). 유효할 때만 → insmod 인자 `bridge_roam_announce` (capability-gate 적용, 구버전 .ko 부팅 안전). runtime 변경: `/sys/module/moal/parameters/bridge_roam_announce`. 점검은 `wifi {0\|1} br status`의 `roam_announce(rt)`/`announce counters`. |

> **⚠️ `keepalive_idle_ms=20` 기본 동작 변경 주의** (`mlan1.enabled`와 동급의 behavioral change): 드라이버 자체 기본값은 `0`(free-running)이지만 이 패키지의 출하 config는 `20`(adaptive)이다. 따라서 해당 param을 지원하는 드라이버(예: `moal_imx93.ko`)에서는 부팅 시마다 명시적으로 `20`(adaptive idle cutoff)이 적용되어 **idle 발열↓ ↔ idle 후 첫 패킷 콜드 가능**이라는 동작 변화가 생긴다. 드라이버 기본(free-running)을 원하면 `0`으로 설정한다.

> **capability-gate (부팅 안전)**: `keepalive_idle_ms`는 신규 param이라, `wifi_init.sh`가 로드 대상 `.ko`에 해당 param이 선언돼 있는지 `grep -aq`로 확인한 뒤 선언된 경우에만 insmod 인자로 전달한다. 미선언 드라이버(예: 현재 `moal_imx8.ko`)에는 자동으로 전달하지 않아 `insmod` 실패(=Wi-Fi init 붕괴)를 방지하며, 이 경우 드라이버 기본값(`0`)이 쓰인다. `peer`/`consume_link_local`도 빈값이면 동일하게 미전달(구버전 드라이버 호환). `local_hairpin`/`roam_announce`는 값이 있어도 parmtype 토큰 게이트를 통과한 경우에만 전달(미선언 .ko + JSON=1 조합은 `wifi br status`가 WARN으로 검출).

---

## 4. checker - WiFi 체커 + Reboot 정책 (인터페이스별: `mlanN.checker`)

**사용 스크립트**: `wifi_checker.sh`, `wlan_reboot_policy.sh`

> **⚠️ 동작 변경 (2026-08-19)** — 아래 두 판정이 그동안 **사실상 발화하지 않고 있었고**,
> 고친 뒤로는 정상 발화한다. 임계값(`FAULT_*`, `LIMIT_CNT`)은 그대로지만 체감 민감도가
> 올라가므로 운영 시 참고할 것.
>
> - **station dump 판정**: `iw ... station dump` 의 **exit code** 로 보던 것을 **출력 유무**로
>   바꿨다. nl80211 DUMP 계열은 드라이버 에러를 `NLMSG_DONE` 페이로드로 돌려주고 iw 가
>   exit 0 을 내므로, 드라이버가 죽어 있어도 `FAULT_CNT` 가 오르지 않았다(실측: dump rc=0
>   vs doit `iw <if> info` rc=237). 이제 `FAULT_REASSOC_CNT`(2) → `FAULT_RESTART_CNT`(4) →
>   `FAULT_REBOOT_CNT`(6) 사다리가 실제로 동작한다.
> - **operstate 비교**: `get_state()` 는 오래전부터 `operstate` 를 돌려주는데 비교 문자열은
>   `wpa_state` 시절의 `"DISCONNECTED"`/`"SCANNING"` 이 남아 있어 `"down"` 하나만 유효했다.
>   `dormant`/`lowerlayerdown`/`notpresent` 를 명시적으로 포함했다(`unknown` 은 연결 여부를
>   알 수 없다는 뜻이라 종전대로 개입하지 않는다).
> - **정책 거부 시 백오프**: 재부팅 요청이 `rc=11`(loop) 로 거부되면 `REBOOT_COOLDOWN_SEC + 30`
>   초 물러난다. 정책은 거부하면서도 state 를 먼저 쓰므로, 쿨다운보다 짧은 주기로 재요청하면
>   카운터가 영구히 래칫된다.

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

**사용 스크립트**: `wifi_logger_cpu.sh`, `wifi_apply_enabled.sh`, `wifi_logger_control.sh`

이 섹션은 **인터페이스 개념이 없는 시스템 로거**만 다룬다. 주기 키는 아래 per-interface 절에 있다.

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `enabled` | bool | `true` | 시스템 로거 그룹(CPU/MMC/TEMP/MCP/SUMMARY) 부팅 활성화. `false`면 온도 로그와 과열 보호도 함께 중단. 인터페이스별 로거(`wifi_logger@<iface>`)는 멈추지 않는다 |
| `cpu_interval_sec` | int | `60` | CPU/MEM 사용률 로깅 주기 (초) |

> **인터페이스별로 이관됨**: `link_interval_sec` / `stat_log_interval_sec` / `stat_check_interval_sec` /
> `stat_reset_interval_sec` / `bgscan_stale_threshold_sec` 는 전역 `logger`에서 제거되고
> `<iface>.logger.*` 로 옮겨졌다. 업그레이드 시 `postinst`의
> `migrate_retired_global_logger_keys`가 구 전역값을 per-iface 키가 없는 인터페이스로 승격시킨 뒤
> 전역 키를 지우므로(운영자가 바꾼 값은 보존된다), **정상 경로에서는 전역 잔존 키가 없다.**
> 소비 코드에 남은 `<iface>.logger` → `logger` → 코드 기본값 체인의 전역 항은 그 마이그레이션이
> 돌지 못한 기기(jq 부재/실패/구버전 postinst)용 안전망일 뿐이다.

> **⚠️ `link_retry_count` / `link_retry_delay_sec` 는 JSON 키가 아니다 (유령 키)**: 구 가이드에는 이 두 키가 `logger` 표에 있었으나 **현재 JSON에는 실재하지 않으며, JSON Schema에도 넣지 말 것**. `wifi_logger_link.py`의 순간 끊김 억제 로직(`wpa_cli reconfigure`·`select_network` 직후 100~200ms 동안 `iw station dump`가 비는 것을 곧바로 끊김으로 기록하지 않고 재조회)은 **모듈/CLI 기본값 `4` / `0.05`(초)로만 동작**한다. 조정이 필요하면 JSON이 아니라 CLI `--link-retry-count` / `--link-retry-delay`로만 가능하다.

### per-interface logger (주기 키의 소재지)

| 키 | 타입 | 기본값 | 인터페이스 | 설명 |
|----|------|--------|-----------|------|
| `link_interval_sec` | float | mlanN `0.9` / eth0 `1` | mlan0, mlan1, eth0 | 링크 상태 체크 주기 (초) |
| `stat_log_interval_sec` | int | `1` | mlan0, mlan1 | WiFi 통계 로깅 주기 (초) |
| `stat_check_interval_sec` | int | `1` | mlan0, mlan1 | WiFi 통계 체크 주기 (초) |
| `stat_reset_interval_sec` | int | `604800` | mlan0, mlan1 | 통계 누적 리셋 주기 (초, 기본 7일) |
| `bgscan_stale_threshold_sec` | int | `600` | mlan0, mlan1 | `beacon.json` 스캔 엔트리 stale 프루닝 임계(초, 기본 10분). 소비: `wifi_logger_scan.py`. 양의 정수가 아니면 코드 기본 600 + 경고 |
| `fwcfg_watch_sec` | float | `60` | mlan0, mlan1 | FW 커스텀 설정(`rate_adapt`/`antcfg`/`mcs_tier`) 변화 감시 주기(초). `0`=끔. 한 주기에 하나씩 라운드로빈하므로 3개 기준 전체 순회는 3배 주기. 관측 전용 — 변화 시 syslog `warn`만 남기고 복구하지 않는다. 소비: `wifi_logger_link.py` |
| `enabled` | bool | mlan0 `true` / mlan1 `false` / eth0 `true` | mlan0, mlan1, eth0 | `wifi_logger@<iface>` 부팅 정책. `mlanN.enabled=false`면 강제 disable |

> `stat_log_interval_sec`과 `stat_check_interval_sec`의 차이: check는 데이터 수집 주기, log는 실제 파일/syslog 기록 주기이다. log >= check 관계를 유지해야 한다.
>
> eth0은 `enabled`와 `link_interval_sec`만 소비한다 — stat/scan 로거는 유닛의
> `ConditionPathIsDirectory=/sys/class/net/%i/wireless` 로 무선 인터페이스에서만 동작한다.

```json
"eth0": {
    "logger": { "enabled": true, "link_interval_sec": 1 }
},
"mlan0": {
    "logger": {
        "link_interval_sec": 0.9,
        "stat_log_interval_sec": 1,
        "stat_check_interval_sec": 1,
        "stat_reset_interval_sec": 604800,
        "bgscan_stale_threshold_sec": 600,
        "enabled": true
    }
}
```

로거 그룹 제어 명령은 시스템과 인터페이스에 동일하게
`start|stop|restart|status|enable|disable`을 제공한다. 런타임 동작과 부팅 정책은
분리되며, 인터페이스 일괄 명령은 없다. 시스템 그룹의 `stop|disable`은
`wifi_logger_temp`가 담당하는 온도 체크와 과열 보호도 함께 중단하므로 CLI가 경고한다.

```bash
wifi log system start|stop|restart|status|enable|disable
wifi mlan0 log start|stop|restart|status|enable|disable
wifi mlan1 log start|stop|restart|status|enable|disable
wifi eth0  log start|stop|restart|status|enable|disable
```

- `start|stop|restart`: 현재 부팅의 런타임 상태만 변경
- `enable|disable`: JSON 부팅 정책과 systemd enable 상태만 변경하며 즉시 start/stop하지 않음
- 시스템 자식(CPU/MMC/TEMP/MCP/SUMMARY)은 각각 systemd가 감독한다. TEMP 중지는 과열 보호 중지와 동일하다.
- 외부 명령 제한시간은 stat/snapshot/CPU/MMC/MCP 5초, WLAN 온도 조회 3초다. 실패한 온도는 `0`이 아니라 `unknown`이다.

---

## 11. mlan0 / mlan1 - 인터페이스별 설정

**사용 스크립트**: `wifi_bgscan.py`, `wifi_roam.py`, `wifi_init.sh`, `wifi_apply_enabled.sh`, `wifi_event.sh`

### 11.1 interface defaults - 인터페이스 기본 활성/주파수

**사용 스크립트**: `wifi_init.sh`, `wifi.sh`

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `enabled` | bool | mlan0 `true` / mlan1 `false` | 인터페이스별 초기화/적용 여부. `false`면 `wifi_init.sh`가 해당 인터페이스의 radio setup과 bridge enable을 건너뜀 |
| `wpa_supplicant.enabled` | bool | `true` | supplicant 데몬 관리. `wifi_apply_enabled.sh`가 `wpa_supplicant@<iface>.service`를 enable/disable하고, `false`면 `wifi_init.sh`의 직접 `systemctl start`도 건너뛴다(두 경로를 모두 막아야 실효한다 — `start`는 disable된 유닛도 기동시키기 때문). 외부(`wifi_manager` 등)가 supplicant를 소유하거나 association을 수동 제어할 때 `false`. `<iface>.enabled=false`이면 이 값과 무관하게 disable |
| `Frequency` | string | `"auto"` | 인터페이스별 bandcfg 기본값. `auto`, `2.4GHz`, `5GHz` |
| `connect_threshold` | int | `-100` | 연결 후보 BSS의 신호레벨이 이 값(dBm) 미만이면 연결 후보에서 제외하는 최소 연결 임계값. `-100`=사실상 무필터. **커스텀 패치 wpa_supplicant 바이너리**(`/opt/wlan/bin/wpa_supplicant.imx8`/`.imx93`)가 `/usr/local/etc/wifi_init_conf.json`을 직접 읽어 적용(shell/python 스크립트 경유 아님). 로그: `BSS: … level N < connect threshold M` |
| `net_rx` | int | `0` | MGMT 프레임 로깅 모드. `wifi_init.sh`가 `wifi_mod_para.conf`의 해당 블록(`PCIE9098_N` / `SD9098_N`)에 반영. 0=비활성 |
| `mgmt_hex_dump_enable` | bool | `false` | MGMT 프레임 hex dump 로깅 활성화 |
| `STANDARD` | string | mlan0 `"ax"`, mlan1 `"ac"` | WiFi 표준 제한. `wifi_init.sh`가 `wifi_mod_para.conf`의 해당 블록에 `dev_cap_mask`로 반영. `n`/`ac`/`ax`(또는 `4`/`5`/`6`). **mlan1은 `ax` 불가**. 아래 [매핑](#standard--wifi_mod_paraconf-매핑) 참고 |
| `CAL_DATA_CFG` | string | `""` | 인터페이스별 캘리브레이션 데이터 파일. 비어있으면 `global.CAL_DATA_CFG`로 fallback. `wifi_init.sh`가 `wifi_mod_para.conf` 블록의 `cal_data_cfg=`로 주입. 아래 [매핑](#cal_data_cfg--txpwrlimit_path--인터페이스별-매핑) 참고 |
| `TXPWRLIMIT_PATH` | string | `""` | 인터페이스별 TX 파워 리밋 파일(절대 경로). 비어있으면 `global.TXPWRLIMIT_PATH`로 fallback. `"none"`이면 이 인터페이스만 미적용. 부팅 시 `mlanutl <iface> hostcmd`로 적용 |
| `thermal_mgmt` | bool | `true` | FW thermal management 활성화. `true`(기본)=enable, `false`=disable. 부팅 시 `mlanutl <iface> hostcmd`로 적용. 아래 [§11.8](#118-thermal_mgmt---fw-thermal-관리) 참고 |

> 인터페이스 활성화와 주파수 값은 `/usr/local/etc/wifi_init_conf.json`만 읽는다.
> 별도 외부 설정 파일과의 우선순위 병합은 없다.

> **⚠️ 패키지 기본값 주의**: 출하 `wifi_init_conf.json`은 `mlan0.enabled=true`, **`mlan1.enabled=false`**로 설정되어 있다 — 즉 **mlan1은 기본적으로 초기화되지 않는다**(`wifi_init.sh`가 radio setup·bridge enable을 건너뛰고, `wifi_apply_enabled.sh`가 mlan1 child unit을 disable). 신규 설치·공장초기화로 이 기본 config를 그대로 쓰는 환경에서 mlan1을 사용하려면 본 파일의 `mlan1.enabled=true`로 변경한다.

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
- **선택된 production CAL은 `wifi_cal_backup.sh reset`에서 형식까지 재검증한다.** custom marker가 있으면 정상 active/backup을 유지하고 active 손상 시 backup에서 복구하며, 둘 다 손상되면 reset을 실패시켜 reboot을 막는다. 선택되지 않은 custom CAL(active/backup/marker)은 제거하고, package 이름의 CAL은 독립 `/opt/wlan/config/wlan/` baseline으로 active와 `.bak`을 함께 재시드해 과거 bytes가 부활하지 않게 한다.
- **`.mac`은 `base`만 보존하고 `target`은 초기화된다.** `base`는 유닛의 기준 MAC(`write_mac.sh` / `eth_mac_get.sh`가 기록)이지만 `target`은 `wifi mac <iface> target <MAC>`으로 정하는 런타임 설정이다. 리셋 후 `resolve_mac`은 `dynamic → target → base` 순으로 폴백하므로 보존된 `base`가 쓰인다. 함께 초기화되는 `.link` 파일은 다음 부팅에 `resolve_mac` → `update_mac.sh` 경로로 `base`에서 다시 만들어진다(`.link` 정리 절차는 [공장 초기화의 `.link` 정리](#클론-mac-잔재--mac_clone_require_peer) 참고).
- 위 10개를 제외한 나머지 키(`STANDARD`, `connect_threshold`, `.wbridge.*`, `.mac.*.target` 등)는 종전대로 전부 템플릿 값으로 초기화된다.
- 테스트: `wifi_conf_preserve_test.sh` (하드웨어/root 불필요)

### 11.2 periodic_roam - 주기적 패시브 로밍

> **DEPRECATED / 강제 비활성**: 이 블록은 기존 JSON 호환을 위해서만 남아 있다.
> `wifi_periodic_roam`은 `wifi_roam`/wpa native와 겹치는 제3의 proactive owner이므로
> `wifi_apply_enabled.sh`가 `enabled=true`도 경고 후 무시하고
> `wifi_periodic_roam@<iface>.service`를 항상 disable+stop한다. unit 자체에도 항상
> 실패하는 `ExecCondition`이 있어 이전 release의 symlink/queued job으로도 실행되지 않는다.

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `enabled` | bool | `false` | **미사용**. `true`도 강제 off |
| `interval` | int | `60` | legacy 값 보존용, 현재 동작에 미사용 |
| `scan_before_roam` | bool | `true` | legacy 값 보존용, 현재 동작에 미사용 |

### 11.3 bgscan - 백그라운드 스캔

**사용 스크립트**: `wifi_bgscan.py`

`wifi_bgscan.py`는 별도 backend 설정을 받지 않는다. `wifi_apply_enabled.sh`가 부팅
최초 `/run/wifi/<iface>.roam-policy.json`을 원자 생성하고, bgscan/roam/init/writer가
이 snapshot을 공통으로 사용한다. `/run` 수명 동안 owner/topology는 daemon이
crash-restart되어도 바뀌지 않는다. `/run/.<iface>.roam-policy.latched`는 해당 boot에
snapshot이 이미 생성됐음을 별도로 기억한다. snapshot만 삭제되면 live JSON으로
재생성하지 않고 service/writer가 fail-closed하며, 재부팅 시에만 둘 다 사라진다.

| `roaming.enabled` | proactive roam owner | bgscan requester | Mode A cross-SSID |
|---|---|---|---|
| `true` | `wifi_roam.py` | `iw` | wifi_roam이 목표 network ID/BSSID를 pin·선택·확인 |
| `false` | wpa_supplicant native selection | `wpa_cli scan` (`TYPE=ONLY` 미사용) | wpa 기본 network 선택 정책을 수용 |

owner/backend의 runtime hot switch는 지원하지 않는다. `roaming.enabled` 변경은
**재부팅**으로 반영한다. 반면 `bgscan.enabled=false`는 package 주기 스캔만 끄며,
wpa_supplicant의 장애 복구·재연결 스캔은 계속 동작한다.

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `enabled` | bool | mlan0 `true` / mlan1 `false` | package background scan 서비스 활성화. false여도 wpa 복구 scan은 유지 |
| `interval` | int | `60` | 백그라운드 스캔 주기 (초) |
| `ssid_filter` | bool | `true` | active scan의 **기본 ssid** directed probe 포함 여부. `true`면 conf 기본 ssid를 probe, `false`면 기본 ssid는 probe 없이 스캔. `roaming.extra_ssids`(§11.4)는 이 값과 **무관하게 항상 probe**된다. wifi_roam/iw periodic bgscan은 `passive=true`도 safety override 뒤 active construction을 쓰므로 이 규칙이 적용된다 |
| `freq_filter` | bool | `true` | **DEPRECATED**. 공통 `freq_list`가 있으면 `false`도 무시하고 iw/wpa_cli 모두 같은 목록을 사용. 목록 자체가 없을 때만 전대역 |
| `passive` | bool | `true` | compatibility 값. wifi_roam owner의 periodic iw bgscan은 `bgscan.passive=true`를 수용하지만 supported mlan hardware의 data-plane safety를 위해 프로세스당 한 번 경고하고 active scan으로 강제한다. wpa native owner는 계속 `wpa_cli -i <iface> SCAN passive=1`을 요청한다. `false`는 native backend에서도 active scan을 요청한다 |
| `emit_roam_hint` | bool | `true` | iw/wifi_roam owner에서 스캔 성공 시 roam backoff hint 발행. wpa native owner에서는 소비자가 없어 강제 비활성 |

> **periodic iw safety override (`bgscan.passive`, 기본 `true`)**: wifi_roam owner의
> periodic `iw` backend는 `true`를 config compatibility로만 수용한다. supported mlan
> hardware에서 반복 다채널 passive scan이 data plane을 strand할 수 있으므로, 이 backend는
> 프로세스당 한 번 warning을 남기고 기존 active grammar를 사용한다. 즉 periodic `iw`
> 명령에는 `passive` 토큰이 없고, `ssid_filter`와 `roaming.extra_ssids`의 정상 directed
> probe 순서가 유지된다. 반대로 wpa native owner의 `wpa_cli` backend는 `true`일 때
> `passive=1`을 계속 보내므로 probe 없이 beacon만 수신하며 hidden SSID를 발견하지 못한다.
> `STAGED_SCAN.home_passive`는 wifi_roam.py의 단일 홈 채널 staged-scan 설정으로,
> `bgscan.passive`와 별개이고 이번 periodic safety override로 변경되지 않았다.

> **아래 `ssid_filter` 설명은 active construction에 적용된다.** wifi_roam/iw periodic
> bgscan은 `passive` 값과 무관하게 safety override 뒤 이 construction을 사용한다. wpa
> native backend는 `passive=false`일 때만 아래 directed `ssid` 인자를 사용한다.
>
> `ssid_filter=true`이면 bgscan이 conf 기본 ssid를 directed probe한다. iw backend의 active 문법은 `iw <iface> scan ... ssid <SSID>`이고, native backend는 `wpa_cli -i <iface> SCAN ... ssid <UTF-8 hex>`를 쓴다. native active SSID filters are UTF-8 hex encoded because the control interface accepts bytes rather than a quoted SSID. non-hidden SSID는 probe 없이 beacon으로도 잡히지만 **hidden SSID는 directed probe가 있어야 발견**된다. 따라서 `roaming.extra_ssids`(§11.4)는 명시적 로밍 후보로서 `ssid_filter` 값과 **무관하게 항상 directed probe**에 포함된다 — `ssid_filter=false`로 두어도 extra SSID(hidden 포함)는 누락되지 않는다. ssid_filter on/off의 스캔 시간·통신 영향 차이는 작고, 실제 비용은 `freq_filter`(채널 수)가 좌우한다.

> both backends require the same `wpa_state=COMPLETED` safety gate before bgscan sends either `iw <iface> scan` or the native `wpa_cli ... SCAN` request. 미연결 시엔 wpa_supplicant의 재연결 스캔/association과 라디오 경합을 피하려 skip한다.
>
> package bgscan은 per-network `bgscan=`을 설정·강제하지 않는다. 그 unsupported boundary에 대한 built-in fail-close 동작은 이 구성의 범위 밖이다.

> 스캔 파라미터는 **매 스캔 직전에 다시 읽는다** — 실제 network SSID와 공통
> `freq_list`는 `wpa_supplicant-<iface>.conf`에서, `interval`/filter/passive는 JSON에서
> 읽는다. 반면 **backend/owner/generate/extra_ssids/bgscan.enabled**는 boot snapshot에
> 고정되므로 persisted JSON만 바꾸거나 daemon을 재시작해도 바뀌지 않는다.
> 연결 상태 확인은 매 tick이 아니라 스캔 주기 도래 시에만 수행한다.

공통 주파수 정책의 canonical 형식은 다음과 같다. 목록이 비면 전역/블록
`freq_list`를 모두 생략한다. `scan_freq`는 부팅 canonicalization의 legacy fallback으로만 읽는다.

```ini
update_config=0
freq_list=2412 2437 5180
network={
    # ...
    freq_list=2412 2437 5180
}
```

모든 network 블록은 전역과 **동일한 목록**을 갖는다. 부팅과 `wifi freq`,
`wifi connect`, OPC writer가 이 형식을 유지하므로 Mode A의 SSID마다 서로 다른
주파수 범위를 둘 수 없다. runtime writer는 `/run/wifi/<iface>.wpa-conf.lock`을
공유하고, 같은 directory staging 파일을 sync한 뒤 atomic rename하고 대상 파일과
directory까지 sync하므로 wifi/OPC가 경합하거나 reader가 partial conf를 관찰하지 않는다.
OPC는 원본 backup inode와 그 directory entry도 새 conf 설치 전에 순서대로 sync하며,
설치 직전부터 reconfigure 성공 직전까지 rollback-required 단계로 처리한다. 이 구간의
실패·시그널은 EXIT trap이 원본을
복구한다. staging/설치 파일·directory sync 실패도 성공으로 무시하지 않는다. rollback은
durable backup을 별도 staging으로 복사해 atomic rename하고 복원 파일+directory sync가
모두 성공한 뒤에만 backup을 제거한다. rollback rename/sync가 실패하면 exit 6으로
중단하면서 byte-exact backup 경로를 보존·보고한다. 모든 `wpa_cli` 제어 명령은
**process rc=0과 reply=`OK`를 동시에** 만족해야 성공이다(`OK` 출력 뒤 nonzero 종료도 실패).

### 11.4 roaming - 로밍 알고리즘

| 키 | 타입 | 기본값 (mlan0/mlan1) | 설명 |
|----|------|---------------------|------|
| `use_signal_avg` | bool | `true` | 평균 신호 사용 여부 |
| `DEFAULT_TH_2G` | int | `-75` | 2.4GHz 로밍 RSSI 임계값 (dBm) |
| `DEFAULT_TH_5G` | int | `-75` | 5GHz 로밍 RSSI 임계값 (dBm) |
| `DIFF_TH` | int | `7` | 로밍 결정 RSSI 차이 (dB) |
| `CHECK_INTERVAL` | int | `1` | 로밍 체크 주기 (초). 판정 입력 link.json 이 ~1s 갱신이라 1 미만은 실익 없음 |
| `SCAN_NO_RESULT_SLEEP` | int | `3` | 스캔 결과 없을 때 대기 (초) |
| `ROAM_SUCCESS_SLEEP` | int | `3` | 로밍 성공 후 대기 (초) |
| `enabled` | bool | mlan0 `true` / mlan1 `false` | owner 선택자. `true`=wifi_roam+iw, `false`=wpa native+wpa_cli. boot snapshot에 latch되므로 변경은 **재부팅** |
| `extra_ssids` | array[str] | `[]` | Mode A에서 추가 network 블록으로 만들 SSID. 같은 psk/key_mgmt 전제. 빈 배열이어도 Mode A identity/SSID writer 금지는 유지. 변경은 **재부팅** |
| `generate_network_blocks` | bool | `false` | 부팅 topology 결정자. `false`=Mode B 단일 블록/cross-SSID 자동전환 없음, `true`=Mode A 다중 블록/자동 cross-SSID. 변경은 **재부팅** |
| `ROAM_CROSS_FAIL_RETRY_COUNT` | int | `2` | 모드A cross-SSID(`select_network`) 전환 실패 시 cooldown 없이 즉시 재시도 허용 횟수. 초과 시 지수 backoff로 해당 SSID를 후보에서 제외(진동 차단). 모드B에선 미적용 |

> **Mode B (`generate_network_blocks=false`)**: 단일 network 블록만 유지하고
> 자동 owner/bgscan은 `extra_ssids`를 무시한다. 수동 `wifi <iface> roam`도 같은 SSID
> 안의 BSS 전환 전용이라 `extra_ssids`를 읽지 않으므로, Mode B에서 이 키는 boot
> snapshot에 보존만 되고 읽는 소비자가 없다. 망 전환은 `wifi connect <ssid> [freq...]`
> (conf ssid 교체 → reconfigure → 재연결)로 한다.
> snapshot 생성 시점에는 부팅 base와 중복된 후보를 거부하지만, `wifi connect` 전환
> 후 live base가 그 후보와 같아지는 것은 의도된 단일-블록 동작이다.
> Mode A에서는 자동 owner만 cross-SSID를 수행한다. 수동 `roam`은 두 모드 모두 같은
> SSID 안의 BSS 전환 전용이다.
>
> **Mode A (`generate_network_blocks=true`)**: 부팅 시 base 블록의 자격증명과 공통
> `freq_list`를 상속한 extra SSID 블록을 만든다. 명시적 `wifi connect <ssid>`는 기본
> SSID 소실을 막기 위해 거부한다. `extra_ssids=[]`로 생성 블록이 0개여도 Mode A
> identity는 boot snapshot에 남아 같은 거부가 적용된다. `roaming.enabled=true`이면 wifi_roam이 목표 BSSID를
> 해당 network ID에 임시 pin하고 `id+ssid+bssid+COMPLETED`를 정확히 확인한 뒤 pin을
> 해제하고 모든 블록을 복구한다. `false`이면 wpa_supplicant의 native cross-SSID 선택
> 정책(우선순위·신호·blacklist 등)을 그대로 수용한다.
> BSSID pin 전에 `/run/.<iface>.selection-cleanup-pending` JSON WAL을 atomic write+fsync한다.
> WAL 지속성을 확인하지 못하면 pin 자체를 거부하고 process-local gate도 유지한다.
> pin 해제/all-network 복구와 canonical reconfigure가 모두 실패하면 WAL을 남긴 채
> wifi_roam은 다음 판정보다 해당 cleanup을 먼저 재시도한다. WAL은 `/run/wifi` 정리와
> 독립적이다. daemon 재시작 시에는 boot snapshot 검증/owner 거부보다 cleanup-only
> preflight를 먼저 수행하므로, snapshot이 함께 삭제돼도 supplicant pin을 복구한 뒤
> owner 정책은 계속 fail-closed한다. 디스크 WAL 또는 메모리 gate가 제거될 때까지 새
> 로밍 판정은 차단된다.
>
> Mode A/B와 owner는 부팅 최초 `/run` snapshot으로 결정한다. 설정 파일만 고치거나
> 일부 daemon/wifi_init만 재시작해 topology/owner를 hot switch할 수 없으며 반드시
> 재부팅한다. snapshot이 없거나 손상되면 owner daemon은 추측하지 않고 기동을 거부한다.

> **로밍 판정 스캔**: 현재 링크 RSSI가 임계값 아래로 떨어지면 주파수 구성에 따라 분기한다.
> 1. **단일 freq = 현재 채널**: `home_passive=true`면 홈채널 passive scan을 수행한다. 현재 AP 외 후보 beacon을 못 받았을 때만 같은 채널 directed active scan으로 재확인한다. `home_passive=false`면 처음부터 directed active 1회다.
> 2. **다중 freq**: 홈 passive와 `ap.log` cache 판정을 건너뛰고 `iw scan freq <공통 freq_list 전체> ssid <allowed>` directed active를 1회 수행한다.
> 3. **공통 freq_list 미설정**: 안전장치로 전대역 active scan 1회를 수행한다.
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
>
> **SIGHUP 원자성**: 데몬은 새 JSON을 분리된 설정으로 읽고 타입, schema 수치 범위, SSID identity를 모두 검증한 뒤 한 번에 반영한다. 어느 한 필드라도 잘못되면 전체 변경을 거부하고 기존 설정과 WPA conf mtime을 그대로 유지한다. 따라서 임시 저장 중인 JSON이나 `trend_window_size: 0` 같은 범위 위반이 다른 유효 필드만 부분 반영시키지 않는다.

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
| `detection_time` | int | `10` | A→B→A 왕복 감지 시간 (초) |

> **제거됨 (2026-07-31 노브 감사 D1, `knob_audit_2026-07.md`)**: `LOAD_BASED_ROAM` ·
> `ADAPTIVE_INTERVAL` · `POST_ROAM_ARP_OPTIMIZATION`(+`PEER_WARMUP`) 은 출하 기본 off 로
> 사용 실적이 없어 코드·템플릿·스키마에서 제거됐다. 기존 기기 JSON 에 남은 해당 키는
> 데몬이 읽지 않으므로 무해(stale)하다. `PREDICTIVE_ROAM` 은 2층 판정 계획의 RSSI 이력
> 소스라 보류로 남았다.

### 11.4b antcfg - FW Tx/Rx 안테나 경로 (물리 전용)

**사용 스크립트**: `wifi_init.sh` → `wifi_fw_config_lib.sh`

association 전에 `mlanutl <iface> antcfg <tx> [rx]`로 FW의 **물리** Tx/Rx 경로를 지정한다.
섹션 자체는 opt-in이다. **imx93/543 제품 기본은 mlan0/mlan1 모두 비활성(비움)** — 부팅
경로에서 RF_ANTENNA HostCmd를 발행하지 않고 물리를 FW 기본(2x2)에 둔다(driver#41).
광고 NSS intent는 §11.4c `antcfgnss`가 담당한다. physical 1-path(예: `0x0101/0x0101`)는
p149.115 scan wedge cofactor이므로, 물리 명령 경로 자체를 부팅에서 제거한 것이 새 회피
형태다. 이 섹션을 켜서 SET하면 안테나 마스크로부터 intent(`user_htstream`)가 재계산돼
antcfgnss 값을 **덮어쓴다** — 켜야 한다면 적용 순서는 항상 antcfg → antcfgnss다(런타임
수동 SET도 동일; 연결 중 antcfg SET은 FW가 거부하면서도 intent는 갱신되는 부작용이 실측돼
있으니 피한다).

제품 profile의 보드 판정은 persisted JSON이 아니라 실제 `soc_id`다. JSON의
`BOARD_TYPE`/`BUS_TYPE`/`mcp.iio_device`가 감지값과 다르면 `crit` 로그 후 원자
정규화한다. 선택 KO와 로드된 `mlan`/`moal`의 `version` 또는 `srcversion`이
다르거나 확인되지 않으면 association 전에 fail-closed 한다. i.MX8MM에서는 i.MX93
strict profile을 적용하지 않으며 custom antcfg/MCS는 보존한다.

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `enabled` | bool | mlan0=`false`, mlan1=`false` | 적용 ON/OFF. 제품 기본은 비활성 — SET 자체를 하지 않는다 |
| `tx` | string | mlan0=`""`, mlan1=`""` | Tx 경로 비트맵. 10진 또는 `0x` 16진, `1`~`0xFFFF`. `rx`가 비면 Tx/Rx 공통 |
| `rx` | string | mlan0=`""`, mlan1=`""` | Rx 경로 비트맵. 빈 문자열이면 인자를 생략한다 |
| `verify.physical_tx` | string | (선택) | SET 뒤 기대하는 FW physical Tx path |
| `verify.physical_rx` | string | (선택) | SET 뒤 기대하는 FW physical Rx path |
| `verify.user_htstream` | string | (선택) | SET 뒤 기대하는 host NSS intent. matching 543 `mlanutl` 필요 |

이 섹션을 커스텀으로 켠 경우: `verify`가 없으면 SET 후 GET 결과를 로그로만 남기고,
`verify`가 있으면 세 하위 키가 모두 필수다. 다만 불일치해도 **부팅과 association 은
막지 않는다** — 재시도 후 `local0.err` 로그와 `/run/wifi/fwcfg_unapplied_<iface>` 마커만
남긴다(FW 설정 미반영으로 재부팅하던 경로는 제거됐다).

imx93 의 **부팅 게이트**(`wifi_fw_validate_product_scan_profile`)가 검사하는 것은
**antcfg 축뿐**이다 — `mlan0.antcfg` 와 `mlan1.antcfg` 가 비활성이고 `mlan1.antcfgnss`
가 어댑터 설정을 덮어쓰지 않을 것. 이것만이 p149.115 scan wedge 와 인과가 확인된 축이다
(physical 1-path 가 cofactor). 광고 Rx NSS 1SS 제한 **단독으로는 wedge 가 재현되지
않았다**(`docs/ant_rx_nss_scan_gate_2026-08-25.md:100`).

게이트는 **fail-open** 이다 — 위반해도 부팅은 계속되고 `local0.err` 로그와
`/run/wifi/fwcfg_unapplied_<iface>` 마커만 남는다. 이 마커는 `wifi <iface> info` 의
**[FW Config Unapplied]** 섹션에서 확인한다 — 부팅 로그는 흘러가지만 "지금 이 보드가
의도한 RF 설정으로 도는가" 는 상태로 물을 수 있어야 한다. 종전에는
하드 실패라 설정이 그대로인 채 재부팅만 반복됐다.

`antcfgnss` 와 `mcs_tier` 는 게이트 대상이 아니라 **현장 조정 가능**하다. 다만 제품
기본값에는 이유가 있다 — 역할 분리로 `mcs_tier` 는 순수 MCS 상한만 두고(`ht 15`),
NSS 제한은 `antcfgnss` 니블(`0x1111` = Tx1/Rx1)이 전담한다. `mcstiercfg ht` 를 `7` 로
내리면 HT 뿐 아니라 **VHT·HE 의 TX NSS 까지 1 로 떨어진다**(driver-v2#41 실측).
`antcfgnss` 는 apply 단계에서 FW read-back(`verify.user_htstream`)으로 확인되지만
불일치해도 **막지는 않는다** — 로그와 미반영 마커만 남는다. 즉 잘못된 값은 조용히
FW 기본값으로 남을 수 있으니, 변경 후에는 `mlanutl <iface> antcfgnss` 로 실제 반영을
확인할 것.

따라서 WebUI 는 imx93 에서 **`antcfg` 를 읽기 전용**으로 두고, `antcfgnss`/`mcs_tier`
는 제품 기본값과 위 주의사항을 함께 노출하는 편집 가능 항목으로 다루면 된다.

업그레이드에서는 active JSON 우선 병합 때문에 템플릿 변경만으로 과거 값이 바뀌지 않는다.
`postinst`가 병합 직후 과거 제품 이력 — 구제품 비대칭 계약 `0x0303/0x0101(+verify)`,
그 이전의 `enabled=false, tx="", rx=""`, 알려진 physical 1x1 `0x0101` — 만
**새 계약(antcfg 비움 + antcfgnss `0x1111` 주입)** 으로 멱등 승격한다. 그 밖의 명시적
안테나 경로 값은 운영자 설정으로 보고 보존하며 antcfgnss도 주입하지 않는다(이 경우 제품
불변식 검사에 걸리므로 의도적 커스텀은 불변식까지 이해한 구성이어야 한다). deep merge가
커스텀 값에 템플릿의 제품용 `verify`를 자동 주입한 경우에는 주입된 계약만 제거해 기존
log-only 동작을 유지한다.

이 마이그레이션은 **forward-only safety migration**이다. 새 패키지로 승격된 설정을 구
패키지가 이해하는 값으로 자동 역변환하지 않는다. downgrade가 필요하면 해당 구 릴리스와
함께 저장한 구 버전 설정/backup을 복원해야 한다. imx8 업그레이드와 factory reset은
보드 감지 단계에서 위 strict imx93 profile만 중화하며, 별도 custom log-only antcfg는 보존한다.

비트맵 해석 (9097/9098/IW624):

| | LOW BYTE (2G) | HIGH BYTE (5G) |
|---|---|---|
| bit 0 / bit 8 | path A | path A |
| bit 1 / bit 9 | path B | path B |
| 둘 다 | path A+B | path A+B |

| 예시 | 의미 |
|------|------|
| `"0x303"` | 2G/5G 모두 A+B (2x2) |
| `"0x103"` | 2G A+B, 5G A |
| `"0x202"` | 2G/5G 모두 path B |
| `"3"` | 밴드 구분 없는 칩에서 A+B |
| `"0xFFFF"` | SAD 지원 칩의 안테나 다이버시티 (이때 `rx`는 평가 주기, 기본 `0x1770`=6s) |

> **⚠️ 어댑터 단위 설정**: 키는 인터페이스별이지만 실제로는 라디오(어댑터) 하나의 설정이다.
> `mlan0`/`mlan1`에 서로 다른 값을 켜면 나중에 적용된 쪽(mlan1)이 이기며, 이때 경고 로그가 남는다.
> 두 인터페이스를 함께 쓸 때는 같은 값을 넣거나 한쪽만 켠다.

> **⚠️ `global.ANT_TYPE`과 다르다**: `ANT_TYPE`은 드라이버 로드 전 GPIO mux(`SW_SEL1`/`SW_SEL2`)를
> `internal`/`external`로 바꾸는 하드웨어 경로 선택이고, `antcfg`는 그 뒤 FW의 Tx/Rx chain 선택이다.
> 서로 독립이며 둘 다 필요할 수 있다.

> **`0` 거부**: `tx`/`rx`에 `0`을 넣으면 어떤 경로도 선택되지 않아 RF가 죽는다. `mlanutl`이 성공을
> 보고할 수 있고 이 기기는 무선이 유일한 접속 경로이므로, 설정 검증 단계에서 거부하고 섹션 전체를
> 건너뛴다(FW 기본 경로 유지). 범위를 벗어난 값·비16진 문자열도 같다.

### 11.4c antcfgnss - 광고 NSS intent (user_htstream)

**사용 스크립트**: `wifi_init.sh` → `wifi_fw_config_lib.sh` (antcfg 다음 순서로 적용)

association 전에 `mlanutl <iface> antcfgnss <value>`로 광고 NSS intent(`user_htstream`)를
직접 기록한다(driver#41, ported b75741f+). **RF_ANTENNA HostCmd를 발행하지 않아 물리
안테나는 불변**이며, 니블 배치는 `[15:12]`=5G Tx, `[11:8]`=5G Rx, `[7:4]`=2G Tx,
`[3:0]`=2G Rx다. **imx93 제품값 `0x2121`(양 밴드 Tx2/Rx1)** — OTA 실측으로
**실효 TX NSS = min(`mcstiercfg ht` 티어, Rx니블, Tx니블)** 이 확정됐고(driver#41
`issuecomment-5503431889`), 제품 구성 `ht 7` + Rx니블 1 이므로 TX NSS1이 보장된다.
구 기술 `min(Tx니블, Rx니블)` 은 `ht` 항이 빠져 있어 **다른 값으로 바꿀 때 어긋난다** —
예: `0x2222`(구 규칙 min=2)도 `ht 7` 이 상한이라 실효 TX NSS1 에 머문다. TX NSS2 가
필요하면 `mcstiercfg ht 15` 를 함께 풀어야 한다. 실효 RX NSS 는 Rx니블 단독이며 `ht`
티어에 묶이지 않는다. 반영은 다음 (re)association부터다.

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `enabled` | bool | mlan0=`true`, mlan1=`false` | 적용 ON/OFF. imx93 mlan0 제품 기본은 `true` (부팅 게이트 대상은 아님 — antcfg 축만 검사) |
| `value` | string | mlan0=`"0x1111"` | `0x` 접두 **필수**(드라이버가 강제 — 진수 혼동·파서 fail-open 차단). 지원 밴드 니블은 `1..hw 상한`(0은 드라이버가 거부) |
| `verify.user_htstream` | string | mlan0=`"0x1111"` | SET 후 read-back 기대값. 불일치는 로그+미반영 마커로만 남고 association 은 계속된다 |

구버전 드라이버: `antcfgnss <value>` SET이 실패하거나 read-back(`antcfg`의
`[user_htstream=...]`)에서 값을 읽지 못하면 재시도 후 미반영으로 기록한다(부팅은 계속). 레거시 antcfg로
위임하던 경로는 제거했다 — 위임은 RF_ANTENNA HostCmd를 발행해 "물리 불변" 계약을 깨면서도,
read-back이 불가한 드라이버에서는 같은 검증에 걸려 결국 실패했다(확인할 수 없는 intent는
통과시키지 않는다). 릴리즈 preflight는 staging된 imx93 `mlanutl`에 `antcfgnss` ABI marker가
없으면 패키징을 거부한다.

> **⚠️ 어댑터 단위 상태**: `user_htstream`은 라디오(어댑터) 하나의 상태다. antcfg와 같은
> last-wins 함정이 있어 mlan1은 비활성을 유지한다(제품 불변식이 검사).

> **⚠️ 순서 제약**: 이후의 `antcfg <mask>` SET과 FW reload는 안테나 마스크로부터
> intent를 재계산해 이 값을 덮어쓴다. 적용 순서는 항상 antcfg(물리) → antcfgnss(광고)다.

### 11.5 rate_adapt - FW Rate Adaptation

**사용 스크립트**: `wifi_init.sh`, `wifi.sh`

mlan0 / mlan1에 개별 적용한다. 섹션이 있으면 `mode`/`low_thresh`/`high_thresh`/`interval_ms`
4개 필드가 모두 유효해야 하며, 하나라도 없거나 잘못되면 해당 iface의 rate 설정 전체를 경고 후
건너뛴다. `enabled`는 선택 키로, 없으면 `true`(종전 동작)로 본다.

| 키 | 타입 | 기본값 (현재 JSON) | 설명 |
|----|------|------------------|------|
| `enabled` | bool | `true` | 섹션 적용 ON/OFF. `false`면 `rate_adapt_cfg`를 SET하지 않는다. 이때 남는 값은 FW 기본값이 아니라 **마지막으로 SET된 값**이다 — rate_adapt는 드라이버 재적재를 넘어 유지되며 콜드부팅 후에만 FW 기본값이다(2026-08-28 실측). 값 검증은 `enabled`와 무관하게 수행되므로 꺼둔 채로도 값을 미리 저장해 둘 수 있다 |
| `mode` | int | `1` | `0`=legacy, `1`=SR(Success Rate) |
| `low_thresh` | int | `70` | SR 하한 (%). `255`(0xff)=dynamic |
| `high_thresh` | int | `90` | SR 상한 (%). static은 `low < high`, dynamic은 양쪽 모두 255 |
| `interval_ms` | int | `100` | 양수이며 10ms 배수인 평가 주기 |

현재 `70/90`은 확정 상수가 아니라 실기 결과에 따라 템플릿에서 계속 조정하는 시험값이다.
코드 fallback에는 이 수치를 복제하지 않는다.

```json
"mlan0": {
    "rate_adapt": { "enabled": true, "mode": 1, "low_thresh": 70, "high_thresh": 90, "interval_ms": 100 }
},
"mlan1": {
    "rate_adapt": { "enabled": true, "mode": 1, "low_thresh": 70, "high_thresh": 90, "interval_ms": 100 }
}
```

> **적용 시점과 한계**: 부팅 association 전에만 SET하고 즉시 GET을 로그에 남긴다. NXP 도구 계약상
> connected 상태 SET은 지원되지 않는다(연결 중 SET은 에러 없이 `exit 0`으로 끝나지만 FW가 받지
> 않으므로, 반영 여부는 종료 코드가 아니라 SET 직후 GET으로 판정한다). `wifi <iface> rate`는
> configured/live를 함께 표시하며, 수정은 `wifi <iface> rate <mode> <low> <high> <interval_ms>`로
> JSON에만 저장한다.

> **[정정 2026-08-31] 30/50 복귀는 재현되지 않는다**: 이 절에는 "mlan0 직접 roam의 `COMPLETED`에서
> FW 기본 `30/50`으로 복귀했고, ac 전용 mlan1은 최초 association과 일반 reconnect에서도 복귀했다"고
> 적혀 있었으나 재검증에서 **재현되지 않았다**. wlan-proc 0.5.6 / FW `17.92.1.p149.115`의 mlan0에서
> association 완료를 5회 유발(부팅 1, `reassociate` 2, `wpa_cli roam` 1, 타 벤더 AP·타 채널 전환 1)
> 했고 전 구간 `SR 70/90 iv=10`이 유지됐다. `fwcfg_watch`의 CHANGED도 0건이다. 종전 기재 중
> "mlan0 일반 reconnect에서는 70/90 유지" 부분만 이번 결과와 일치한다.
>
> **미검증 범위**: ① 동일 SSID 내 다른 BSS로 가는 실제 roam — 시험 환경에 해당 ESS의 BSS가
> 하나뿐이라 만들지 못했다(대신 타 AP·타 채널 전환으로 대체 자극을 넣었다). ② mlan1 AC 경로 —
> `mlan1.enabled=false`라 시험하지 못했다. 최초 관측이 0.5.4 무렵이라 그 사이 변경으로 조건이
> 사라졌을 가능성도 배제하지 못한다.
>
> 이후 관측은 **로거가 도는 인터페이스에서만** `fwcfg_watch`가 담당한다. 위 ②의 mlan1 은
> `mlan1.enabled=false`이므로 `wifi_apply_enabled.sh`가 `wifi_logger@mlan1`을 포함한 자식 유닛을
> 모두 disable 한다 — `fwcfg_watch_sec` 값과 무관하게 관측 프로세스 자체가 없다. 즉 미검증
> 항목 ②는 관측으로도 메워지지 않으므로, 확인하려면 mlan1과 그 로거를 켜고 재시험해야 한다.

### 11.6 mcs_tier - MCS Tier 능력 제한

**사용 스크립트**: `wifi_init.sh`, `wifi_event.sh`, `wifi_apply_enabled.sh`

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `enabled` | bool | `true` (양쪽) | mcstiercfg 적용·검증 활성화 |
| `ht` | string | `"7"` (양쪽) | HT(11n). `"7"` 또는 `"15"` |
| `vht` | string | `"7"` (양쪽) | VHT(11ac). `"7"`, `"8"`, `"9"` |
| `he` | string | mlan0 `"both 7"` / mlan1 `""` | HE(11ax) Tx/Rx. mlan0만 지원하며 mlan1은 반드시 빈 문자열 |

> **인터페이스 capability**: mlan0은 ax이므로 HT/VHT/HE를 검증한다. mlan1은 ac까지만 지원하므로
> HT/VHT만 적용하고 HE 설정과 `11axcfg` 판정에서 제외한다.

**적용 시점**: 부팅 시 association 전에 SET한다. SET return code만 신뢰하지 않고
`mcstiercfg` GET을 검증하며 mlan0 HE는 `11axcfg` map도 함께 확인한다. HT/VHT가 다르거나
명확한 비영(非零) HE 값이 설정 의도와 다르면 제한 재시도 후 supplicant 시작을 차단한다.
이 persistent 실패에는 기존 systemd lifecycle 복구가 적용된다.

88W9098 실기에서는 AP가 없는 factory reset 상태에서 HT/VHT는 적용됐지만 association 전
HE Tx/Rx가 `0x0000`으로만 보이는 경우가 확인됐다. 이 경우는 실패가 아니라 per-iface pending으로
기록하고 association을 진행한다. `wifi_event@mlan0`이 INITIAL CONNECTED/CONNECTED/ROAMED에서
먼저 GET을 확인한다. 값이 맞으면 pending을 지운다.

p149.115 실기에서는 첫 association이 HE를 FW 기본 `0xFFFA`로 되돌렸다. 이 경우 connected SET으로
다음 association capability를 저장하고 per-iface 1회 마커를 만든 뒤 MCS lock을 해제하고
`wifi <iface> connect`를 한 번만 요청한다. 이 wrapper가 scan-transition lock, scan quiesce,
fresh association 증명을 함께 소유한다. 다음 CONNECTED의 GET이 JSON 의도와 일치하면 pending과
마커를 지운다. SET이 보이지 않거나
재association 뒤에도 불일치하면 반복 재연결하지 않고 링크·pending·오류를 보존한다. cold boot 실증에서
이 과정 후 HE/VHT `0xFFF0`이 확정됐고, 동일 SSID BSSID roam에서도 추가 SET/reassociate 없이 유지됐다.
AC 전용 mlan1은 이 deferred HE 경로와 `11axcfg` 검증 대상이 아니다.

- `enabled: false`이면 mcstiercfg를 실행하지 않고 FW 기본값을 사용한다.
- `enabled: true`이면 iface capability에 맞는 필드를 모두 문자열로 제공해야 한다.
- mlan0 AX+HE 설정은 deferred 검증과 제한된 1회 복구를 위해 `wifi_event@mlan0`을 활성화한다. mlan1 AC 설정만으로는 활성화하지 않는다.
- CLI 수정은 JSON만 바꾸며 다음 부팅의 association 전 검증 경로에서 적용한다.

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
| `roaming.enabled` | `true` | `false` | mlan0 로밍 기본 활성화 |
| `bgscan.enabled` | `true` | `false` | mlan1 비활성 인터페이스 |
| `checker.enabled` | `true` | `false` | mlan1 비활성 인터페이스 |
| `on_connect.enabled` | `true` | `false` | mlan1 비활성 인터페이스 |
| `logger.enabled` | `true` | `false` | mlan1 비활성 인터페이스 |
| `mcs_tier.ht` / `.vht` / `.he` | `"7"` / `"7"` / `"both 7"` | `"7"` / `"7"` / `""` | mlan1은 ac 전용이므로 HE만 제외 |

> ※ `roaming.CHECK_INTERVAL=1`, `ROAM_SUCCESS_SLEEP=3`, `PING_PONG_PREVENTION`(window=20, detection_time=10)은 mlan0/mlan1이 동일하므로 이 표에 없다.

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
