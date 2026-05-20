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
│   ├── optimize        #   커널 레벨 네트워크 튜닝 (UDP/IRQ/오프로드)
│   ├── link_guard      #   유/무선 링크 상태 감시
│   └── thermal         #   브릿지 thermal 상태 관리
│       └── thresholds  #     온도 임계값 (히스테리시스)
├── checker             # wifi_checker + reboot 정책
├── temperature         # 온도 모니터링 임계값
├── arping              # ARP 연결 감시 + sweep
├── mmc                 # eMMC 수명 모니터링
├── mcp                 # 전류/전압 센서 모니터링
├── monitor             # wifi_link_monitor.py 표시 설정
├── logger              # 각종 로깅 주기 설정 (전역 기본값)
├── eth0                # eth0 인터페이스 설정
│   └── logger          #   eth0 전용 로깅 override
├── mlan0               # mlan0 인터페이스 설정
│   ├── logger          #   mlan0 전용 로깅 override
│   ├── net_rx          #   MGMT 프레임 로깅 (→ PCIE9098_0)
│   ├── rate_adapt      #   FW rate adaptation override
│   ├── periodic_roam   #   주기적 패시브 로밍
│   ├── bgscan          #   백그라운드 스캔
│   ├── roaming         #   로밍 알고리즘
│   ├── mcs_tier        #   MCS tier 능력 제한 (mcstiercfg)
│   └── on_connect      #   AP 연결 후 실행 명령
└── mlan1               # mlan1 인터페이스 설정 (mlan0과 동일 구조)
    ├── logger          #   mlan1 전용 로깅 override
    ├── net_rx          #   MGMT 프레임 로깅 (→ PCIE9098_1)
    ├── rate_adapt      #   FW rate adaptation override
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
| `STANDARD` | string | `""` | WiFi 표준 제한 **fallback**. 인터페이스별 `mlanN.STANDARD`가 우선하며, 비어있을 때만 이 값 사용. `n`/`ac`/`ax`(또는 `4`/`5`/`6`). 자세한 내용은 [11.1 STANDARD → dev_cap_mask 매핑](#standard--wifi_mod_paraconf-매핑) 참고 |
| `DEV_CAP_MASK` | string | `""` | dev_cap_mask raw fallback. 인터페이스/global `STANDARD`가 모두 비었을 때만 사용 |

> **참고**: `BRIDGE_IFACE`, `MAC_MODE`, `ETH_CLIENT_IP`, `eth_link_wait_sec`는 `wbridge` 섹션으로 이동되었습니다. 하위 호환을 위해 `global`에 있어도 동작하지만, 새 설정에서는 `wbridge` 섹션을 사용하세요.

### 1.1 global.rate_adapt - FW Rate Adaptation

**사용 스크립트**: `wifi_init.sh`

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `mode` | int | `1` | rate adaptation 모드. `0`=legacy, `1`=SR(Success Rate) |
| `low_thresh` | int | `50` | SR 모드 하한 임계값 (%). `0xff`=dynamic(noise-based) |
| `high_thresh` | int | `80` | SR 모드 상한 임계값 (%) |
| `interval_ms` | int | `100` | 평가 주기 (ms). association 전에 설정해야 함 |

> **Per-iface override**: `mlan0.rate_adapt`, `mlan1.rate_adapt`에 동일 키를 두면 해당 인터페이스에만 override 적용된다. 우선순위: `mlanN.rate_adapt.<key>` > `global.rate_adapt.<key>` > 내장 기본값. 자세한 내용은 `11.x rate_adapt` 섹션 참조.

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

**사용 스크립트**: `wifi_bridge.sh`, `wifi_init.sh`, `wired_mac_ip_get.py`, `/etc/default/wbridge`

> **우선순위**: `wifi_init_conf.json` (SSoT) > `/etc/default/wbridge` (fallback) > 스크립트 기본값
>
> JSON이 정상 파싱되면 JSON 값이 최종 사용된다. JSON이 없거나 파싱에 실패할 때만
> `/etc/default/wbridge`가 폴백 소스로 사용된다.

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `enabled` | bool | `true` | bridge 기능 마스터 스위치. `false`이면 bridge 서비스 전체 비활성 |
| `bridge_iface` | string | `"mlan0"` | bridge에 사용할 인터페이스. `"mlan0"` 또는 `"mlan1"` |
| `mac_mode` | string | `"dynamic"` | MAC 주소 모드. `"default"` (base만), `"dynamic"` (동적→base), `"static"` (target→base) |
| `eth_client_ip` | string | `""` | 유선 클라이언트 고정 IP. 설정 시 `wired_mac_ip_get.py`의 quick ARP probe 활성화. 빈 문자열이면 비활성 |
| `eth_link_wait_sec` | int | `3` | 유선 링크 준비 대기 시간 (초). `wired_mac_ip_get.py`에서 사용 |
| `engine` | string | `"pcap"` | 패킷 캡처 엔진. `"pcap"` 또는 `"tpacket"` |

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
| `mode_force` | bool | `true` | `true`이면 thermal 클램핑 무시 (요청 모드 강제 적용) |
| `auto_restart` | bool | `false` | thermal 상태 변경 시 bridge 자동 재시작 |
| `timer_enable` | bool | `false` | 타이머 기반 주기적 thermal 체크 |
| `restart_cooldown_sec` | int | `60` | 재시작 쿨다운 (초) |
| `bridge_units` | string | `"wifi_bridge@mlan0.service wifi_bridge@mlan1.service"` | 관리 대상 systemd 유닛 |

### 3.4 wbridge.thermal.thresholds - 온도 임계값

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `warm_cpu_enter` | int | `80` | CPU warm 진입 온도 (°C) |
| `hot_cpu_enter` | int | `90` | CPU hot 진입 온도 (°C) |
| `warm_cpu_exit` | int | `75` | CPU warm 해제 온도 (°C) |
| `hot_cpu_exit` | int | `85` | CPU hot 해제 온도 (°C) |
| `warm_wifi_enter` | int | `70` | WiFi warm 진입 온도 (°C) |
| `hot_wifi_enter` | int | `80` | WiFi hot 진입 온도 (°C) |
| `warm_wifi_exit` | int | `65` | WiFi warm 해제 온도 (°C) |
| `hot_wifi_exit` | int | `75` | WiFi hot 해제 온도 (°C) |

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
| `MIN_UPTIME_SEC` | int | `30` | **커널 부팅**(`/proc/uptime`) 후 최소 대기 시간 (초). 이전에는 reboot 거부. 데몬 uptime이 아님 — boot loop 방지용 |
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

## 11. mlan0 / mlan1 - 인터페이스별 설정

**사용 스크립트**: `wifi_bgscan.py`, `wifi_roam.py`, `wifi_periodic_roam.sh`, `wifi_event.sh`

### 11.1 interface defaults - 인터페이스 기본 활성/주파수

**사용 스크립트**: `wifi_init.sh`, `wifi.sh`

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `enabled` | bool | `true` | 인터페이스별 초기화/적용 여부. `false`면 `wifi_init.sh`가 해당 인터페이스의 radio setup과 bridge enable을 건너뜀 |
| `Frequency` | string | `"auto"` | 인터페이스별 bandcfg 기본값. `auto`, `2.4GHz`, `5GHz` |
| `net_rx` | int | `0` | MGMT 프레임 로깅 모드. `wifi_init.sh`가 `wifi_mod_para.conf`의 해당 PCIE9098 블록에 반영. 0=비활성 |
| `STANDARD` | string | mlan0 `"ax"`, mlan1 `"ac"` | WiFi 표준 제한. `wifi_init.sh`가 `wifi_mod_para.conf`의 해당 블록에 `dev_cap_mask`로 반영. `n`/`ac`/`ax`(또는 `4`/`5`/`6`). **mlan1은 `ax` 불가**. 아래 [매핑](#standard--wifi_mod_paraconf-매핑) 참고 |

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

### 11.2 periodic_roam - 주기적 패시브 로밍

**사용 스크립트**: `wifi_periodic_roam.sh` (service: `wifi_periodic_roam@.service`)

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `enabled` | bool | `false` | 주기적 패시브 로밍 활성화 |
| `interval` | int | `60` | 로밍 시도 주기 (초) |
| `scan_before_roam` | bool | `true` | `true`=roam 전 스캔 수행(최신 RSSI 기반 판단), `false`=기존 ap.log 스캔 데이터 사용 |

### 11.3 bgscan - 백그라운드 스캔

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `interval` | int | `60` | 백그라운드 스캔 주기 (초) |

### 11.4 roaming - 로밍 알고리즘

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

### 11.5 rate_adapt - FW Rate Adaptation (per-iface override)

**사용 스크립트**: `wifi_init.sh`

mlan0 / mlan1에 개별 적용. 블록이 없거나 특정 키가 없으면 `global.rate_adapt`로 fallback.

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

**사용 스크립트**: `wifi_init.sh`, `wifi_event.sh`

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `enabled` | bool | `false` | mcstiercfg 적용 활성화 |
| `ht` | string | `""` | HT(11n) 인자. 예: `"7"`(1x1), `"15"`(2x2). `""`이면 건너뜀 |
| `vht` | string | `""` | VHT(11ac) 인자. 예: `"7"`, `"8"`, `"9"`. `""`이면 건너뜀 |
| `he` | string | `""` | HE(11ax) 인자. 예: `"7"`, `"9"`, `"11"`, 또는 `"both 7"`. `""`이면 건너뜀 |

**적용 시점**:
1. **부팅 시** (`wifi_init.sh` → `apply_iface_radio_defaults`): association 전에 1회 적용
2. **연결 이벤트 시** (`wifi_event.sh`): `iw event`의 `connected to` 감지 시마다 재적용 (로밍/재연결 포함)


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
| `enabled` | bool | `false` | on_connect 기능 활성화 |
| `commands` | array | `[]` | AP 연결/로밍 후 순서대로 실행할 명령 목록. 실패해도 중단하지 않고 로깅만 수행 |

---

## mlan0 vs mlan1 기본값 차이

대부분 동일하지만 다음 값이 다르다:

| 설정 경로 | mlan0 | mlan1 | 이유 |
|-----------|-------|-------|------|
| `STANDARD` | `ax` | `ac` | mlan1은 11ax 미지원 |
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
