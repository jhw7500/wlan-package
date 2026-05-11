# iMX8MM CPU 온도 저감 설계서

**작성일:** 2026-03-10
**플랫폼:** iMX8MM (4x Cortex-A53) + NXP 88W9098 (PCIe)
**대상 프로젝트:** wlan-package, wlan-bridge, wlan-driver
**Status:** Implemented (eco 모드로 구현 완료 — 참조: `docs/archive/01-plan/2026-03-10-eco-mode-implementation.md`)

---

## 1. 문제 정의

### 현상
- 유무선 브릿지 상태에서 단순 ping 통신만으로 CPU 온도 70도 후반 도달
- optimize 스크립트 미적용 상태, normal 모드 기준
- 고부하(throughput 테스트) 시 thermal throttling 위험

### 근본 원인 분석
트래픽 수준이 1~10Mbps로 낮음에도 온도가 높은 이유:

1. **폴링 오버헤드**: wbridge의 pcap_dispatch가 timeout_ms=1ms로 초당 ~1000회 시스템콜 발생
2. **인터럽트 빈도**: normal 모드 rx-usecs=50, rx-frames=4로 소량 패킷에도 잦은 인터럽트
3. **CPU idle 진입 불가**: RT 스케줄링(SCHED_FIFO, priority=50)이 CPU idle state 진입을 방해
4. **cpufreq governor**: 기본값이 performance일 경우 풀클럭(1.6GHz) 고정
5. **4코어 모두 활성**: IRQ와 워크로드가 분산 없이 모든 코어에서 처리

### 운영 조건
| 항목 | 값 |
|------|-----|
| 평상시 처리량 | ~1Mbps (~83 pps, 1500B 기준) |
| 피크 처리량 | 5~10Mbps (~830 pps) |
| 레이턴시 요구 | 중요 (ping RTT 증가 최소화) |
| 성능 타협 조건 | 온도 저감 효과가 클 경우 RTT +5ms까지 허용 가능 (근거 필요) |
| 하드웨어 변경 | 불가 |

---

## 2. 설계 방침

- **기존 모드 불변**: latency, normal, thermal 3개 모드는 수정하지 않음
  - latency: 최소 지연에 집중하는 모드 → 변경 없음
  - normal: 기본 균형 모드 → 변경 없음
  - thermal: 최대 온도 저감 모드 → 변경 없음
- **신규 `eco` 모드 추가**: normal과 thermal 사이에 위치, 레이턴시를 크게 희생하지 않으면서 온도를 낮추는 목적
- **코드 수정 최소화**: 기존 파라미터, 스크립트, sysfs 튜닝 우선
- **단계적 적용**: 각 단계별 독립 적용 및 온도 측정 가능
- **롤백 용이**: 모든 변경은 파라미터 복원으로 원복 가능
- **근거 기반**: 각 항목의 예상 효과와 레이턴시 영향을 명시

### 모드 비교표

| 항목 | latency | normal | **eco (신규)** | thermal |
|------|---------|--------|--------------|---------|
| rx-usecs | 0 | 50 | **100** | 150 |
| rx-frames | 1 | 4 | **6** | 10 |
| timeout_ms | 1 | 1 | **5** | 10 |
| dispatch_budget | 64 | 64 | **96** | 128 |
| rt_priority | 80 | 50 | **40** | 30 |
| GRO | off | on | **on** | on |
| cpufreq | (미지정) | (미지정) | **conservative** | powersave |
| IRQ 분배 | 분산 | 분산 | **CPU2,3 집중** | CPU2,3 집중 |
| ps_mode | 2 (off) | 2 (off) | **2 (off)** | 2 (off) |

> ps_mode는 모든 모드에서 기본 off 유지. AP 호환성 리스크가 있으므로 별도 옵션(`--ps-mode`)으로 분리.

---

## 3. 최적화 항목 상세

### 3.1 단계 1: cpufreq governor 변경 (예상 -3~5°C)

**변경 내용:**
```bash
# performance → conservative
for cpu in /sys/devices/system/cpu/cpu[0-3]/cpufreq/scaling_governor; do
    echo conservative > $cpu
done

# conservative 파라미터 조정
echo 80 > /sys/devices/system/cpu/cpufreq/conservative/up_threshold
echo 20 > /sys/devices/system/cpu/cpufreq/conservative/down_threshold
echo 2  > /sys/devices/system/cpu/cpufreq/conservative/freq_step
```

**근거:**
- iMX8MM Cortex-A53은 600MHz~1.6GHz 범위에서 DVFS(Dynamic Voltage and Frequency Scaling) 지원
- 1Mbps 트래픽 처리에 1.6GHz 풀클럭 불필요
- conservative governor는 부하에 따라 점진적으로 클럭을 올려 순간 부하 대응 가능
- performance 대비 idle 전력 소비 40~60% 감소 (ARM Cortex-A53 TRM 기준)

**레이턴시 영향:** 없음~무시 가능 (클럭 전환 시간 ~100µs, 패킷 간격 ~12ms)

**적용 위치:** `setup-irq-affinity.sh` 또는 별도 systemd service

---

### 3.2 단계 2: Interrupt Coalescing 강화 (예상 -2~3°C)

**변경 내용:**
```bash
# eco 모드 전용 설정
ethtool -C mlan0 rx-usecs 100 rx-frames 6
ethtool -C eth0  rx-usecs 100 rx-frames 6
```

**근거:**
- rx-usecs=50 → 100: 인터럽트 최대 빈도 10,000/s → 5,000/s (50% 감소)
- rx-frames=4 → 6: 저트래픽에서 대부분 타이머 만료로 처리되므로 실질 영향 미미
- 각 인터럽트마다 context switch + cache flush 발생, 빈도 감소가 직접적 온도 저감

**레이턴시 영향:** 최악의 경우 +50µs (100µs - 50µs), 실측 ping RTT +0.1~1ms

**적용 위치:** `setup-irq-affinity.sh`의 eco 모드 프로파일

---

### 3.3 단계 3: wbridge 폴링 간격 조정 (예상 -2~3°C)

**변경 내용:**
```bash
# 환경변수 또는 CLI
export WBRIDGE_TIMEOUT_MS=5          # 1ms → 5ms
export WBRIDGE_DISPATCH_BUDGET=96    # 64 → 96
```

**근거:**
- timeout_ms=1: pcap_dispatch가 매 1ms마다 반환 → 초당 ~1000회 시스템콜
- timeout_ms=5: 초당 ~200회로 감소 (80% 감소)
- 1Mbps 기준 83 pps이므로, 5ms 창에 ~0.4 패킷 → 대부분 빈 반환이 줄어듦
- dispatch_budget=96: 버스트 시(10Mbps) 한 번에 더 많이 처리하여 wake-up 횟수 감소

**레이턴시 영향:**
- 평균: +2~3ms (패킷이 도착 후 다음 dispatch까지 대기)
- 최악: +5ms
- ping RTT 기존 ~1ms → ~4~6ms

**적용 위치:** `/run/wbridge.env` 또는 systemd 환경변수

---

### 3.4 단계 4: IRQ Affinity 집중 (예상 -1~2°C)

**변경 내용:**
```bash
# eth0, mlan0 IRQ를 CPU2, CPU3에 집중
echo 4 > /proc/irq/<eth0_irq>/smp_affinity    # CPU2 (0b0100)
echo 8 > /proc/irq/<mlan0_irq>/smp_affinity   # CPU3 (0b1000)

# RPS도 동일하게 설정
echo 4 > /sys/class/net/eth0/queues/rx-0/rps_cpus
echo 8 > /sys/class/net/mlan0/queues/rx-0/rps_cpus

# wbridge affinity: CPU2=eth0 스레드, CPU3=mlan0 스레드 (기존 동작 유지)
```

**근거:**
- CPU0, CPU1이 네트워크 처리에서 완전 해방 → deep idle(WFI/retention) 진입 가능
- Cortex-A53 WFI 상태 전력: ~10mW (active ~300mW 대비 97% 감소)
- 2코어 idle = 전체 SoC 열 발생 ~15~20% 감소

**레이턴시 영향:** 없음 (워크로드 집중일 뿐, 처리 자체는 동일)

**적용 위치:** `setup-irq-affinity.sh` (이미 유사 로직 존재)

**주의:** iMX8MM은 4코어 Cortex-A53으로 빅-리틀 구성이 아님. 코어 간 성능 차이 없음.

---

### 3.5 단계 5: WiFi 칩 전력 관리 (예상 -3~5°C, 선택적)

**변경 내용:**
```bash
# 모듈 파라미터 (insmod 시 또는 wifi_mod_para__.conf)
ps_mode=1        # IEEE Power Save 활성화 (현재 ps_mode=2, 비활성)
auto_ds=1        # Deep Sleep 활성화 (현재 auto_ds=2, 비활성)
```

**근거:**
- 88W9098 칩 자체 발열이 PCIe 버스를 통해 SoC에 전도
- ps_mode=1: 유휴 시 WiFi 칩이 doze 상태 진입, beacon interval마다 wake-up
- 1Mbps 기준 패킷 간격 ~12ms, beacon interval 100ms → 대부분 시간 doze 가능
- auto_ds=1: 장기 유휴 시 deep sleep, 더 높은 전력 절감

**레이턴시 영향:**
- ps_mode=1: wake-up latency +5~20ms (첫 패킷만, 이후 active 유지)
- 연속 트래픽 중에는 영향 없음 (AP가 buffered frame 즉시 전달)
- ping 테스트 시: 간헐적 ping에서 +10~15ms, 연속 ping에서 +1~3ms

**리스크:**
- 일부 AP에서 PS-Poll 응답 지연 가능 → AP 호환성 테스트 필요
- 브릿지 특성상 양방향 트래픽이 빈번하면 doze/wake 전환 오버헤드 발생
- **권장: 단계 1~4 적용 후 온도가 목표에 미달할 때만 적용**

**적용 위치:** `/opt/wlan/config/wifi_mod_para__.conf` (PCIE9098 섹션)

---

### 3.6 보조 항목: cpuidle 활성화 확인

```bash
# idle state가 비활성화되어 있는지 확인
for state in /sys/devices/system/cpu/cpu*/cpuidle/state*/disable; do
    cat $state
done

# 비활성화된 경우 활성화
for state in /sys/devices/system/cpu/cpu*/cpuidle/state*/disable; do
    echo 0 > $state
done
```

**근거:** 일부 임베디드 BSP에서 안정성을 위해 cpuidle을 비활성화하는 경우가 있음. 활성화 시 idle 전력 소비가 크게 감소.

---

## 4. 예상 효과 종합

### 누적 온도 변화 예측

| 단계 | 항목 | 온도 감소 | 누적 RTT 증가 | 누적 온도 |
|------|------|----------|-------------|----------|
| 기준 | normal 모드 (미적용) | - | ~1ms | ~78°C |
| 1 | cpufreq conservative | -3~5°C | +0ms | ~74°C |
| 2 | interrupt coalescing 100µs | -2~3°C | +0.5ms | ~71°C |
| 3 | wbridge timeout_ms=5 | -2~3°C | +3ms | ~69°C |
| 4 | IRQ affinity 집중 | -1~2°C | +0ms | ~67°C |
| 5 | ps_mode=1 (선택) | -3~5°C | +5~10ms | ~63°C |

**단계 1~4 적용 시:** ~67°C (RTT ~4.5ms) — 레이턴시 영향 최소
**단계 1~5 적용 시:** ~63°C (RTT ~10ms) — 최대 온도 저감

> 주의: 예상값은 일반적인 Cortex-A53 + PCIe WiFi 환경 기준 추정치이며, 실제 효과는 방열 구조, 주변 온도, 펌웨어 버전에 따라 다를 수 있음. 반드시 단계별 실측 필요.

---

## 5. 검증 계획

### 측정 방법
```bash
# CPU 온도 (1초 간격, 10분)
while true; do
    echo "$(date +%H:%M:%S) $(cat /sys/devices/virtual/thermal/thermal_zone0/temp)"
    sleep 1
done > /tmp/cpu_temp.log

# WiFi 칩 온도
mlanutl mlan0 get_sensor_temp

# ping RTT 기준선
ping -c 100 -i 0.1 <gateway_ip> | tee /tmp/ping_baseline.log
```

### 단계별 검증 절차
1. 기준선 측정 (normal 모드, 최적화 없음) — 10분 안정화 후 온도/RTT 기록
2. 각 단계 적용 후 동일 조건에서 10분 측정
3. 온도 안정화 확인 (3분 이상 변화 <1°C)
4. RTT 평균, P95, P99 비교
5. 단계 간 누적 효과 확인

### 안정성 검증
- 각 단계에서 24시간 연속 운영 테스트
- 처리량 10Mbps 부하 테스트 (iperf3)
- 로밍 동작 확인 (ps_mode 적용 시)
- thermal throttling 미발생 확인

---

## 6. 적용 가이드

### 원칙: 기존 모드 불변

기존 latency, normal, thermal 모드의 파라미터는 일절 변경하지 않는다.
eco 모드를 신규로 추가하여 `--mode eco`로 선택 가능하게 한다.

### setup-irq-affinity.sh 수정 범위

**추가만 수행 (기존 코드 수정 없음):**

```bash
# eco 모드 프로파일 추가 (latency/normal/thermal 사이에 삽입)
eco)
    RX_USECS=100
    RX_FRAMES=6
    GRO=on
    GSO=off
    TSO=off
    DISPATCH_BUDGET=96
    TIMEOUT_MS=5
    RT_PRIORITY=40
    TPACKET_RETIRE_TOV=5
    CPUFREQ_GOVERNOR=conservative
    CPUFREQ_UP_THRESHOLD=80
    CPUFREQ_DOWN_THRESHOLD=20
    IRQ_AFFINITY_MODE=concentrated    # CPU2,3 집중
    ;;
```

**cpufreq 적용 로직 추가:**
- eco 모드일 때만 governor 변경
- 다른 모드에서는 governor를 건드리지 않음

**IRQ affinity 집중 로직 추가:**
- eco/thermal 모드: CPU2,3 집중
- latency/normal 모드: 기존 분산 유지

### wifi_init_conf.json 수정 방향
```json
"wbridge": {
    "mode": "eco"    // 선택지 추가: latency | normal | eco | thermal
}
```

### ps_mode 별도 옵션 (선택적, 단계 5)

wifi_mod_para__.conf는 모드와 무관하게 적용되므로, ps_mode 변경은 별도 판단:
```
# 기본: 변경 없음 (ps_mode=2, auto_ds=2)
# 필요 시: ps_mode=1, auto_ds=1 로 변경 후 AP 호환성 테스트 필수
```

---

## 7. 리스크 및 완화

| 리스크 | 영향 | 완화 |
|--------|------|------|
| conservative governor의 클럭 전환 지연 | 버스트 트래픽 시 순간 처리 지연 | up_threshold=80으로 빠른 scale-up |
| ps_mode AP 호환성 | 특정 AP에서 지연 증가 | 단계 5는 선택적 적용, AP별 테스트 |
| cpuidle 활성화 시 wake-up 지연 | 드물게 첫 패킷 지연 | retention state만 사용, deeper state 비활성 |
| 온도 예측 오차 | 실제 효과가 예상보다 적을 수 있음 | 단계별 실측 후 판단 |

---

## 8. 결론

iMX8MM + 88W9098 유무선 브릿지 환경에서 **1~10Mbps 저트래픽 조건**의 핵심 발열 원인은 트래픽 자체가 아닌 **폴링/인터럽트 오버헤드와 CPU idle 미진입**이다.

단계 1~4(cpufreq + coalescing + polling + IRQ affinity)만으로 **약 10°C 저감, RTT +3.5ms** 수준의 최적화가 가능하며, 이는 코드 수정 없이 파라미터/스크립트 변경만으로 달성할 수 있다.
