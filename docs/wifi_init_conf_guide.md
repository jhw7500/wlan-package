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
├── global          # 드라이버 초기화 (펌웨어, 모듈 파라미터)
├── mac             # MAC 주소 설정 (인터페이스별)
├── wbridge         # wifi_bridge 프로세스 설정
│   └── thermal     #   브릿지 thermal 상태 관리
├── checker         # wifi_checker + reboot 정책
├── temperature     # 온도 모니터링 임계값
├── arping          # ARP 연결 감시 + sweep
├── mmc             # eMMC 수명 모니터링
├── mcp             # 전류/전압 센서 모니터링
├── logger          # 각종 로깅 주기 설정
├── mlan0           # mlan0 인터페이스 설정
│   ├── bgscan      #   백그라운드 스캔
│   └── roaming     #   로밍 알고리즘
└── mlan1           # mlan1 인터페이스 설정 (mlan0과 동일 구조)
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
| `PRIMARY_IFACE` | string | `"mlan0"` | 기본 인터페이스. `"mlan0"` 또는 `"mlan1"` |
| `MAC_MODE` | string | `"default"` | MAC 주소 모드. `"default"` (base만), `"dynamic"` (동적→base), `"static"` (target→base) |

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
| `optimize` | int | `1` | 브릿지 최적화 활성화 (0=비활성, 1=활성) |
| `mode` | string | `"normal"` | 동작 모드: `"latency"`, `"normal"`, `"eco"`, `"thermal"` |
| `engine` | string | `"pcap"` | 패킷 캡처 엔진 |
| `thermal_state` | string | `"ok"` | 현재 thermal 상태 |
| `mode_force` | int | `0` | 모드 강제 고정 (1=활성) |
| `link_guard` | int | `1` | 링크 가드 활성화 |
| `link_down_debounce_sec` | int | `2` | 링크 다운 디바운스 시간 (초) |
| `link_up_stable_sec` | int | `2` | 링크 업 안정화 대기 시간 (초) |
| `link_idle_poll_sec` | int | `2` | 링크 유휴 폴링 주기 (초) |
| `wait_ready_timeout_sec` | int | `20` | 인터페이스 준비 대기 타임아웃 (초) |
| `wlan_roam_grace_sec` | int | `15` | 로밍 후 유예 시간 (초) |
| `wlan_down_restart` | int | `0` | WLAN 다운 시 재시작 (0=비활성) |
| `profile_version` | int | `1` | 프로파일 버전 |

### 3.1 wbridge.thermal - 브릿지 Thermal 관리

**사용 스크립트**: `wbridge_thermal_state_update.sh`

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `auto_restart` | int | `1` | 상태 변경 시 자동 재시작 |
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
| `LIMIT_CNT` | int | `3` | 인터페이스 미존재 허용 횟수. 이 값+1회 연속 실패 시 reboot 요청 |
| `MAX_UNSTABLE_DURATION` | int | `10` | WiFi 불안정 허용 시간 (초). 초과 시 wpa_supplicant 재시작 |
| `MAX_REBOOT_COUNT` | int | `3` | 쿨다운 윈도우 내 최대 reboot 횟수. 초과 시 루프 감지 |
| `REBOOT_COOLDOWN_SEC` | int | `300` | reboot 카운트 리셋 윈도우 (초) |
| `MIN_UPTIME_SEC` | int | `120` | 부팅 후 최소 대기 시간 (초). 이전에는 reboot 거부 |

### Reboot 정책 동작 흐름

```
인터페이스 미존재 → ERR_CNT 누적 → ERR_CNT > LIMIT_CNT
→ 로그 수집 (dmesg, journald)
→ wlan_reboot_policy.sh 호출
  ├── uptime < MIN_UPTIME_SEC → 거부 (rc=10)
  ├── 쿨다운 내 count >= MAX_REBOOT_COUNT → 거부 (rc=11, 루프 감지)
  └── 통과 → reboot 실행
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

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `cpu_interval_sec` | int | `60` | CPU/MEM 사용률 로깅 주기 (초) |
| `stat_log_interval_sec` | int | `1` | WiFi 통계 로깅 주기 (초) |
| `stat_check_interval_sec` | int | `1` | WiFi 통계 체크 주기 (초) |
| `stat_reset_interval_sec` | int | `604800` | 통계 누적 리셋 주기 (초, 기본 7일) |
| `bgscan_stale_threshold_sec` | int | `600` | bgscan 로그 stale 판정 시간 (초, 기본 10분) |

> `stat_log_interval_sec`과 `stat_check_interval_sec`의 차이: check는 데이터 수집 주기, log는 실제 파일/syslog 기록 주기이다. log >= check 관계를 유지해야 한다.

---

## 10. mlan0 / mlan1 - 인터페이스별 설정

**사용 스크립트**: `wifi_bgscan.py`, `wifi_roaming.py`

### 10.1 bgscan - 백그라운드 스캔

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `interval` | int | `30` | 백그라운드 스캔 주기 (초) |

### 10.2 roaming - 로밍 알고리즘

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `use_signal_avg` | bool | `false` | 평균 신호 사용 여부 |
| `DEFAULT_TH_2G` | int | `-75` | 2.4GHz 로밍 RSSI 임계값 (dBm) |
| `DEFAULT_TH_5G` | int | `-75` | 5GHz 로밍 RSSI 임계값 (dBm) |
| `DIFF_TH` | int | `10` | 로밍 결정 RSSI 차이 (dB) |
| `CHECK_INTERVAL` | int | `5` | 로밍 체크 주기 (초) |
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

---

## mlan0 vs mlan1 기본값 차이

대부분 동일하지만 다음 값이 다르다:

| 설정 경로 | mlan0 | mlan1 | 이유 |
|-----------|-------|-------|------|
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

> `/etc/default/wbridge` 파일은 wbridge 섹션에서 JSON보다 우선한다.

---

## 설정 로드 패턴

### Shell (bash)

```bash
WIFI_INIT_CONF_JSON="/usr/local/etc/wifi_init_conf.json"

# 기본값 선언
MY_VALUE=10

# JSON에서 로드 (jq 사용)
if [ -f "$WIFI_INIT_CONF_JSON" ] && command -v jq >/dev/null 2>&1; then
    MY_VALUE=$(jq -r '.section.key // 10' "$WIFI_INIT_CONF_JSON")
fi
```

- `//` 연산자: 값이 null이면 fallback 값 사용
- `jq -r`: raw 출력 (따옴표 제거)

### Python

```python
WIFI_INIT_CONF_JSON = "/usr/local/etc/wifi_init_conf.json"
MY_VALUE = 10  # 기본값

try:
    with open(WIFI_INIT_CONF_JSON) as f:
        conf = json.load(f)
    MY_VALUE = conf.get("section", {}).get("key", MY_VALUE)
except Exception:
    pass
```

---

## 설정값별 사용 스크립트 맵

| 섹션 | 스크립트 | 언어 |
|------|---------|------|
| `global` | `wifi_init.sh` | bash |
| `mac` | `wifi_init.sh`, `wifi_mac_set.py`, `wifi_mac_save.py` | bash, python |
| `wbridge` | `wifi_bridge.sh` | bash |
| `wbridge.thermal` | `wbridge_thermal_state_update.sh` | bash |
| `checker` | `wifi_checker.sh` → `wlan_reboot_policy.sh` | bash |
| `temperature` | `wifi_logger_temp.sh` | bash |
| `arping` | `wifi_arping.sh`, `arping_sweep.sh` | bash |
| `mmc` | `wifi_logger_mmc.sh` | bash |
| `mcp` | `wifi_logger_mcp.sh` | bash |
| `logger` | `wifi_logger_cpu.sh`, `wifi_logger_stat.py`, `wifi_bgscan.py` | bash, python |
| `mlan0.bgscan` | `wifi_bgscan.py` | python |
| `mlan0.roaming` | `wifi_roaming.py` | python |
| `mlan1.bgscan` | `wifi_bgscan.py` | python |
| `mlan1.roaming` | `wifi_roaming.py` | python |

---

## 현장 튜닝 가이드

### 고온 환경 (40C+ 외부 온도)

```json
"temperature": {
    "emerg_cpu": 98,
    "crit_cpu": 95,
    "error_cpu": 90,
    "warn_cpu": 85,
    "emerg_mlan": 90,
    "crit_mlan": 85,
    "error_mlan": 80,
    "warn_mlan": 75
}
```

### 불안정한 네트워크 환경

```json
"checker": {
    "LIMIT_CNT": 5,
    "MAX_UNSTABLE_DURATION": 20,
    "MAX_REBOOT_COUNT": 5,
    "REBOOT_COOLDOWN_SEC": 600
},
"arping": {
    "threshold": 20,
    "loop_delay_sec": 5
}
```

### 디버깅 (로깅 빈도 증가)

```json
"logger": {
    "cpu_interval_sec": 10,
    "stat_log_interval_sec": 1,
    "stat_check_interval_sec": 1
},
"temperature": {
    "check_interval_sec": 2
},
"mcp": {
    "check_interval_sec": 2
}
```

### 로깅 부하 감소 (저사양/배터리 환경)

```json
"logger": {
    "cpu_interval_sec": 300,
    "stat_log_interval_sec": 10,
    "stat_check_interval_sec": 5
},
"temperature": {
    "check_interval_sec": 15
},
"mmc": {
    "check_interval_sec": 3600
}
```

---

## JSON 검증

설정 파일 수정 후 반드시 문법을 검증한다:

```bash
jq . /usr/local/etc/wifi_init_conf.json > /dev/null && echo "OK" || echo "FAIL"
```

런타임에서 값 확인:

```bash
# 특정 값 조회
jq '.temperature.emerg_cpu' /usr/local/etc/wifi_init_conf.json

# 섹션 전체 조회
jq '.checker' /usr/local/etc/wifi_init_conf.json

# 모든 섹션 키 목록
jq 'keys' /usr/local/etc/wifi_init_conf.json
```
