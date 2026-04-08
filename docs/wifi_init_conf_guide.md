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
├── global              # 드라이버 초기화 (펌웨어, 모듈 파라미터)
│   ├── rate_adapt      #   FW rate adaptation 설정
│   └── ping_monitor    #   ping 모니터 서비스 제어
├── mac                 # MAC 주소 설정 (인터페이스별)
├── wbridge             # wifi_bridge 프로세스 설정
│   └── thermal         #   브릿지 thermal 상태 관리
├── checker             # wifi_checker + reboot 정책
├── temperature         # 온도 모니터링 임계값
├── arping              # ARP 연결 감시 + sweep
├── mmc                 # eMMC 수명 모니터링
├── mcp                 # 전류/전압 센서 모니터링
├── logger              # 각종 로깅 주기 설정 (전역 기본값)
├── eth0                # eth0 인터페이스 설정
│   └── logger          #   eth0 전용 로깅 override
├── mlan0               # mlan0 인터페이스 설정
│   ├── logger          #   mlan0 전용 로깅 override
│   ├── net_rx          #   MGMT 프레임 로깅 (→ PCIE9098_0)
│   ├── periodic_roam   #   주기적 패시브 로밍
│   ├── bgscan          #   백그라운드 스캔
│   ├── roaming         #   로밍 알고리즘
│   ├── mcs_tier        #   MCS tier 능력 제한 (mcstiercfg)
│   └── on_connect      #   AP 연결 후 실행 명령
└── mlan1               # mlan1 인터페이스 설정 (mlan0과 동일 구조)
    ├── logger          #   mlan1 전용 로깅 override
    ├── net_rx          #   MGMT 프레임 로깅 (→ PCIE9098_1)
    ├── periodic_roam   #   주기적 패시브 로밍
    ├── bgscan          #   백그라운드 스캔
    ├── roaming         #   로밍 알고리즘
    ├── mcs_tier        #   MCS tier 능력 제한 (mcstiercfg)
    └── on_connect      #   AP 연결 후 실행 명령
```

---

## 1. global - 드라이버 초기화

**사용 스크립트**: `wifi_init.sh`

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `FW_NAME` | string | `"cts/pcieuart9098_combo_v1.bin"` | WiFi 펌웨어 파일 경로 (`/lib/firmware/` 기준) |
| `MOD_PARA` | string | `"cts/wifi_mod_para.conf"` | 모듈 파라미터 설정 파일 |
| `CAL_DATA_CFG` | string | `"cts/WlanCalData_ext_RD.conf"` | 캘리브레이션 데이터 파일 |
| `TXPWRLIMIT_PATH` | string | `"/lib/firmware/cts/txpwrlimit_cfg_9098.conf"` | TX 파워 리밋 설정 파일 (절대 경로) |
| `MFG_MODE` | string | `"0"` | 제조 모드. `"1"` = MFG 모드 활성화 |
| `STANDARD` | string | `""` | WiFi 표준 제한 (비어있으면 자동) |
| `DEV_CAP_MASK` | string | `""` | 디바이스 capability 마스크 |
| `BRIDGE_IFACE` | string | `"mlan0"` | 브릿지 인터페이스. `"mlan0"`, `"mlan1"`, 또는 `"none"` (bridge 비활성) |
| `MAC_MODE` | string | `"dynamic"` | MAC 주소 모드. `"default"` (base만), `"dynamic"` (동적→base), `"static"` (target→base) |
| `ETH_CLIENT_IP` | string | `""` | 유선 클라이언트 고정 IP. 설정 시 `wired_mac_ip_get.py`의 quick ARP probe 활성화. 빈 문자열이면 비활성 |
| `eth_link_wait_sec` | int | `3` | 유선 링크 준비 대기 시간 (초) |

### 1.1 global.rate_adapt - FW Rate Adaptation

**사용 스크립트**: `wifi_init.sh`

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `mode` | int | `1` | rate adaptation 모드. `0`=legacy, `1`=SR(Success Rate) |
| `low_thresh` | int | `50` | SR 모드 하한 임계값 (%). `0xff`=dynamic(noise-based) |
| `high_thresh` | int | `80` | SR 모드 상한 임계값 (%) |
| `interval_ms` | int | `100` | 평가 주기 (ms). association 전에 설정해야 함 |

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
| `base` | 참조 MAC 소스 (빈 문자열이면 생략) |
| `target` | 적용할 대상 MAC (빈 문자열이면 생략) |

> 빈 문자열(`""`)이면 해당 소스를 무시하고 기존 `/opt/wlan/mac` 디렉토리 방식으로 fallback한다.

---

## 3. wbridge - WiFi 브릿지 프로세스

**사용 스크립트**: `wifi_bridge.sh`, `/etc/default/wbridge`

> `/etc/default/wbridge` 파일이 존재하면 해당 값이 JSON보다 **우선**한다.

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `optimize` | int | `0` | 브릿지 최적화 활성화 (0=비활성, 1=활성) |
| `mode` | string | `"normal"` | 동작 모드: `"latency"`, `"normal"`, `"eco"`, `"thermal"` |
| `engine` | string | `"pcap"` | 패킷 캡처 엔진 |
| `thermal_state` | string | `"ok"` | 현재 thermal 상태 |
| `mode_force` | int | `1` | 모드 강제 고정 (1=활성) |
| `link_guard` | int | `1` | 링크 가드 활성화 |
| `link_down_debounce_sec` | int | `2` | 링크 다운 디바운스 시간 (초) |
| `link_up_stable_sec` | int | `2` | 링크 업 안정화 대기 시간 (초) |
| `link_idle_poll_sec` | int | `5` | 링크 유휴 폴링 주기 (초) |
| `wait_ready_timeout_sec` | int | `10` | 인터페이스 준비 대기 타임아웃 (초) |
| `wlan_roam_grace_sec` | int | `15` | 로밍 후 유예 시간 (초) |
| `wlan_down_restart` | int | `0` | WLAN 다운 시 재시작 (0=비활성) |
| `profile_version` | int | `1` | 프로파일 버전 |

### 3.1 wbridge.thermal - 브릿지 Thermal 관리

**사용 스크립트**: `wbridge_thermal_state_update.sh`, `wifi_thermal_state_update.sh`

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `auto_restart` | int | `0` | 상태 변경 시 자동 재시작 (0=비활성) |
| `timer_enable` | int | `0` | 타이머 기반 thermal 체크 |
| `restart_cooldown_sec` | int | `60` | 재시작 쿨다운 (초) |
| `bridge_units` | string | `"wifi_bridge@mlan0.service wifi_bridge@mlan1.service"` | 관리 대상 systemd 유닛 |
| `warm_cpu_enter` | int | `80` | CPU warm 진입 온도 (C) |
| `hot_cpu_enter` | int | `90` | CPU hot 진입 온도 (C) |
| `warm_cpu_exit` | int | `75` | CPU warm 해제 온도 (C) |
| `hot_cpu_exit` | int | `85` | CPU hot 해제 온도 (C) |
| `warm_wifi_enter` | int | `70` | WiFi warm 진입 온도 (C) |
| `hot_wifi_enter` | int | `80` | WiFi hot 진입 온도 (C) |
| `warm_wifi_exit` | int | `65` | WiFi warm 해제 온도 (C) |
| `hot_wifi_exit` | int | `75` | WiFi hot 해제 온도 (C) |

**히스테리시스 설계**: enter와 exit 사이에 5도 갭을 두어 상태 플리핑을 방지한다.

---

## 4. checker - WiFi 체커 + Reboot 정책

**사용 스크립트**: `wifi_checker.sh`, `wlan_reboot_policy.sh`

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `LIMIT_CNT` | int | `5` | 인터페이스 미존재 허용 횟수. 이 값+1회 연속 실패 시 reboot 요청 |
| `MAX_UNSTABLE_DURATION` | int | `10` | WiFi 불안정 허용 시간 (초). 초과 시 wpa_supplicant 재시작 |
| `MAX_REBOOT_COUNT` | int | `3` | 쿨다운 윈도우 내 최대 reboot 횟수. 초과 시 루프 감지 |
| `REBOOT_COOLDOWN_SEC` | int | `300` | reboot 카운트 리셋 윈도우 (초) |
| `MIN_UPTIME_SEC` | int | `30` | 부팅 후 최소 대기 시간 (초). 이전에는 reboot 거부 |
| `FAULT_REASSOC_CNT` | int | `2` | fault 누적 시 재연결(reassoc) 시도 횟수 임계값 |
| `FAULT_RESTART_CNT` | int | `4` | fault 누적 시 wpa_supplicant 재시작 횟수 임계값 |
| `FAULT_REBOOT_CNT` | int | `6` | fault 누적 시 reboot 실행 횟수 임계값 |

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
| `emerg_cpu` | int | `93` | CPU 긴급 온도 (C). `emerg_count_threshold`+1회 연속 초과 시 cooldown+reboot |
| `crit_cpu` | int | `90` | CPU 심각 온도 (C) |
| `error_cpu` | int | `85` | CPU 에러 온도 (C) |
| `warn_cpu` | int | `80` | CPU 경고 온도 (C) |
| `emerg_mlan` | int | `85` | WiFi 칩 긴급 온도 (C) |
| `crit_mlan` | int | `80` | WiFi 칩 심각 온도 (C) |
| `error_mlan` | int | `75` | WiFi 칩 에러 온도 (C) |
| `warn_mlan` | int | `70` | WiFi 칩 경고 온도 (C) |
| `cooldown_sec` | int | `60` | 과열 시 서비스 중지 후 대기 시간 (초) |
| `recover_cpu` | int | `90` | CPU 복구 판정 온도 (C). 이하로 내려가면 reboot |
| `recover_mlan` | int | `80` | WiFi 칩 복구 판정 온도 (C) |
| `check_interval_sec` | int | `5` | 온도 체크 주기 (초) |
| `emerg_count_threshold` | int | `2` | emerg 연속 횟수 임계값. 초과 시 cooldown 진입 |

### 온도 레벨과 동작

| 레벨 | CPU 임계값 | MLAN 임계값 | syslog 레벨 | 동작 |
|------|-----------|------------|-------------|------|
| debug | < 80 | < 70 | local3.debug | 정상 |
| warn | >= 80 | >= 70 | local3.warn | 경고 로깅 |
| error | >= 85 | >= 75 | local3.err | 에러 로깅 |
| crit | >= 90 | >= 80 | local3.crit | 심각 로깅 |
| emerg | >= 93 (연속 3회) | - | local0.emerg | WiFi 서비스 중지 → cooldown → reboot |

### 과열 복구 시퀀스

```
CPU >= 93C (연속 emerg_count_threshold+1회)
→ WiFi/Bridge 전체 서비스 중지
→ cooldown_sec 동안 대기
→ 온도 폴링 (5초 간격)
→ CPU < recover_cpu AND MLAN < recover_mlan
→ journald 스냅샷 → reboot (--force)
```

---

## 6. arping - ARP 연결 감시

**사용 스크립트**: `wifi_arping.sh`, `arping_sweep.sh`

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
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

## 9. logger - 로깅 주기 설정

**사용 스크립트**: `wifi_logger_cpu.sh`, `wifi_logger_stat.py`, `wifi_bgscan.py`

이 섹션은 **전역 기본값**이다. `eth0.logger`, `mlan0.logger`, `mlan1.logger`에서 인터페이스별로 override할 수 있다.

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `cpu_interval_sec` | int | `60` | CPU/MEM 사용률 로깅 주기 (초) |
| `link_interval_sec` | float | `0.95` | 링크 상태 체크 주기 (초) |
| `stat_log_interval_sec` | int | `1` | WiFi 통계 로깅 주기 (초) |
| `stat_check_interval_sec` | int | `1` | WiFi 통계 체크 주기 (초) |
| `stat_reset_interval_sec` | int | `604800` | 통계 누적 리셋 주기 (초, 기본 7일) |
| `bgscan_stale_threshold_sec` | int | `600` | bgscan 로그 stale 판정 시간 (초, 기본 10분) |

> `stat_log_interval_sec`과 `stat_check_interval_sec`의 차이: check는 데이터 수집 주기, log는 실제 파일/syslog 기록 주기이다. log >= check 관계를 유지해야 한다.

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

## 10. mlan0 / mlan1 - 인터페이스별 설정

**사용 스크립트**: `wifi_bgscan.py`, `wifi_roaming.py`

### 10.1 interface defaults - 인터페이스 기본 활성/주파수

**사용 스크립트**: `wifi_init.sh`, `wifi.sh`

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `enabled` | bool | `true` | 인터페이스별 초기화/적용 여부. `false`면 `wifi_init.sh`가 해당 인터페이스의 radio setup과 bridge enable을 건너뜀 |
| `Frequency` | string | `"auto"` | 인터페이스별 bandcfg 기본값. `auto`, `2.4GHz`, `5GHz` |
| `net_rx` | int | `0` | MGMT 프레임 로깅 모드. `wifi_init.sh`가 `wifi_mod_para.conf`의 해당 PCIE9098 블록에 반영. 0=비활성 |

> `config.json`이 별도로 설치된 환경에서는 `.mlan0.enabled`, `.mlan1.enabled`, `.mlan0.Frequency`, `.mlan1.Frequency`가 존재할 때만 이 값을 override한다.

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

### 10.2 periodic_roam - 주기적 패시브 로밍

**사용 스크립트**: `wifi_roaming.py`

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `enabled` | bool | `false` | 주기적 패시브 로밍 활성화 |
| `interval` | int | `60` | 로밍 시도 주기 (초) |
| `scan_before_roam` | bool | `true` | `true`=roam 전 스캔 수행(최신 RSSI 기반 판단), `false`=기존 ap.log 스캔 데이터 사용 |

### 10.3 bgscan - 백그라운드 스캔

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `interval` | int | `60` | 백그라운드 스캔 주기 (초) |

### 10.4 roaming - 로밍 알고리즘

| 키 | 타입 | 기본값 (mlan0/mlan1) | 설명 |
|----|------|---------------------|------|
| `use_signal_avg` | bool | `true` | 평균 신호 사용 여부 |
| `DEFAULT_TH_2G` | int | `-75` | 2.4GHz 로밍 RSSI 임계값 (dBm) |
| `DEFAULT_TH_5G` | int | `-75` | 5GHz 로밍 RSSI 임계값 (dBm) |
| `DIFF_TH` | int | `10` | 로밍 결정 RSSI 차이 (dB) |
| `CHECK_INTERVAL` | int | `3` / `5` | 로밍 체크 주기 (초) |
| `SCAN_NO_RESULT_SLEEP` | int | `3` | 스캔 결과 없을 때 대기 (초) |
| `ROAM_SUCCESS_SLEEP` | int | `5` | 로밍 성공 후 대기 (초) |

#### PREDICTIVE_ROAM - 예측 로밍

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `enable` | bool | `true` | 예측 로밍 활성화 |
| `threshold_boost` | int | `5` | 신호 하락 추세 시 임계값 상향 (dB) |
| `trend_window_size` | int | `5` | 추세 분석 윈도우 크기 (샘플 수) |
| `trend_history_max_age` | int | `30` | 추세 기록 최대 보존 시간 (초) |

#### LOAD_BASED_ROAM - 부하 기반 로밍

| 키 | 타입 | 기본값 (mlan0/mlan1) | 설명 |
|----|------|---------------------|------|
| `enable` | bool | `false` / `true` | 부하 기반 로밍 활성화 |
| `max_roam_load` | int | `80` | 채널 부하 임계값 (%) |
| `load_diff_threshold` | int | `20` | 채널 간 부하 차이 임계값 (%) |

#### PING_PONG_PREVENTION - 핑퐁 방지

| 키 | 타입 | 기본값 (mlan0/mlan1) | 설명 |
|----|------|---------------------|------|
| `enable` | bool | `true` | 핑퐁 방지 활성화 |
| `window` | int | `30` / `60` | 감시 윈도우 (초) |
| `max_roams_in_window` | int | `3` | 윈도우 내 최대 로밍 횟수 |
| `detection_time` | int | `10` / `30` | 핑퐁 감지 시 대기 시간 (초) |

#### ADAPTIVE_INTERVAL - 적응형 체크 주기

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `enable` | bool | `true` | 적응형 주기 활성화 |
| `min_check_interval` | int | `1` | 최소 체크 주기 (초) |
| `max_check_interval` | int | `10` | 최대 체크 주기 (초) |
| `rssi_drop_threshold` | int | `-5` | 신호 하락 감지 임계값 (dB) |
| `rssi_rise_threshold` | int | `2` | 신호 상승 감지 임계값 (dB) |
| `near_threshold_offset` | int | `5` | 임계값 근처 판정 오프셋 (dB) |
| `near_threshold_interval` | int | `2` | 임계값 근처 체크 주기 (초) |
| `good_signal_offset` | int | `15` | 양호 신호 판정 오프셋 (dB) |
| `consecutive_drop_count` | int | `2` | 연속 하락 카운트 임계값 |

#### POST_ROAM_ARP_OPTIMIZATION - 로밍 후 ARP 최적화

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `enable` | bool | `true` | 활성화 |
| `garp_count` | int | `2` | Gratuitous ARP 전송 횟수 |
| `garp_wait` | int | `1` | GARP 전송 간 대기 (초) |

##### PEER_WARMUP - 피어 워밍업

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `enable` | bool | `true` | 활성화 |
| `peer_count` | int | `5` | 워밍업 대상 피어 수 |
| `peer_wait` | int | `1` | 피어 간 대기 (초) |

### 10.5 mcs_tier - MCS Tier 능력 제한

**사용 스크립트**: `wifi_init.sh`

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `enabled` | bool | `false` | mcstiercfg 적용 활성화 |
| `ht` | int | — | HT(11n) 최대 MCS. `7`(1x1) 또는 `15`(2x2) |
| `vht` | int | — | VHT(11ac) 최대 MCS. `7`, `8`, `9` |
| `he` | int | — | HE(11ax) 최대 MCS. `7`, `9`, `11` |

**적용 시점**: 부팅 시 wpa_supplicant 시작 전 (association 전). 로밍해도 유지됨.

- `enabled: false`(기본)이면 mcstiercfg를 실행하지 않음 (FW 기본값 사용)
- 개별 키(ht/vht/he)를 생략하면 해당 표준은 건너뜀
- 인터페이스별 독립 설정 가능 (mlan0과 mlan1에 다른 tier)

> **주의**: VHT는 FW 내부에 MCS 7 하한(floor)이 있어 tier 7이 사실상 최소값.
> 상세 비교: `wlan-driver/docs/mcs-rate-control-comparison.md` 참조.

```json
"mcs_tier": {
    "enabled": true,
    "ht": 7,
    "vht": 7,
    "he": 7
}
```

### 10.6 on_connect - AP 연결 후 명령 실행

**사용 스크립트**: `wifi_event`

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `enabled` | bool | `false` | on_connect 기능 활성화 |
| `commands` | array | `[]` | AP 연결/로밍 후 순서대로 실행할 명령 목록. 실패해도 중단하지 않고 로깅만 수행 |

---

## mlan0 vs mlan1 기본값 차이

대부분 동일하지만 다음 값이 다르다:

| 설정 경로 | mlan0 | mlan1 | 이유 |
|-----------|-------|-------|------|
| `roaming.CHECK_INTERVAL` | `3` | `5` | mlan0은 주 채널로 더 빠른 로밍 감지 |
| `LOAD_BASED_ROAM.enable` | `false` | `true` | mlan1은 보조 채널로 부하 분산 활용 |
| `PING_PONG_PREVENTION.window` | `30` | `60` | mlan1은 더 넓은 윈도우로 보수적 판단 |
| `PING_PONG_PREVENTION.detection_time` | `10` | `30` | mlan1은 핑퐁 감지 시 더 오래 대기 |

---

## 설정 로드 우선순위

```
1. 환경변수 (일부 스크립트, 예: wifi_arping.sh의 THRESHOLD)
2. wifi_init_conf.json 값
3. 스크립트 내장 기본값 (JSON 없거나 jq 미설치 시)
```
