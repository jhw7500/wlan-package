# Phase 3 수정 완료 보고서

**수정 일시**: 2026-02-11
**대상 버전**: wlan-proc v0.1.6 → v0.2.0 (예정)
**수정 범위**: P3 (장기 - 기술 부채 정리)

---

## 수정 완료 항목

### ✅ P3-1. ifconfig → ip 명령 마이그레이션

**수정된 파일**: `dist/wlan/usr/local/scripts/wifi_init.sh`

**수정 내용** (50-57행):

```bash
# 수정 전 (deprecated ifconfig 사용)
cmd=$(ifconfig |grep mlan0)
if [ -n "$cmd" ]; then
    ifconfig mlan0 down
fi
cmd=$(ifconfig |grep mlan1)
if [ -n "$cmd" ]; then
    ifconfig mlan1 down
fi

# 수정 후 (최신 ip 명령 사용)
if ip link show mlan0 &>/dev/null; then
    ip link set mlan0 down
fi
if ip link show mlan1 &>/dev/null; then
    ip link set mlan1 down
fi
```

**영향**:
- `ifconfig`는 net-tools 패키지 소속이며 deprecated
- 최신 임베디드 리눅스 배포판에서 미설치 가능성 제거
- iproute2 패키지의 `ip` 명령은 표준 설치
- 더 간결하고 안전한 존재 확인 (`&>/dev/null`)

---

### ✅ P3-2. dumb/archive/ 디렉터리 제거

**삭제된 디렉터리**: `wlan-bridge/dumb/archive/`

**삭제된 파일** (총 9개):
- `dumb.c.orig`
- `dumb-daemon-fix.c`
- `dumb-phase1.1.c`
- `dumb-phase1.2.c`
- `dumb-phase1.3-final.c`
- `dumb-phase1-complete.c`
- `dumb-phase2.3.c`
- `dumb-phase2.3-clean.c`
- `phase1-complete.diff`

**영향**:
- 배포 패키지 크기 감소 (~100KB)
- Git 히스토리에서 이전 버전 추적 가능하므로 중복 불필요
- 소스 트리 단순화

---

### ✅ P3-3. optimize-for-udp.sh sysctl 중복 방지

**수정된 파일**: `wlan-bridge/scripts/optimize-for-udp.sh`

**수정 내용** (95-103행):

```bash
# 수정 전 (중복 append 가능)
echo "net.core.wmem_max=16777216" >> /etc/sysctl.conf
echo "net.core.wmem_default=16777216" >> /etc/sysctl.conf
...

# 수정 후 (중복 방지)
grep -q "^net.core.wmem_max=" /etc/sysctl.conf || echo "net.core.wmem_max=16777216" >> /etc/sysctl.conf
grep -q "^net.core.wmem_default=" /etc/sysctl.conf || echo "net.core.wmem_default=16777216" >> /etc/sysctl.conf
...
```

**영향**:
- 스크립트 반복 실행 시 sysctl.conf 중복 항목 방지
- 기존 항목이 있으면 skip, 없으면 추가하는 idempotent 동작
- 8개 항목 모두에 중복 방지 로직 적용

---

### ✅ P3-4. config 스크립트 expr → 산술 확장 전환

**수정된 파일**: `dist/wlan/DEBIAN/config`

**수정 내용** (44, 48, 52, 86행):

```bash
# 수정 전 (외부 프로세스 호출)
sum_id0=`expr $sum_id0 + 1`
sum_id1=`expr $sum_id1 + 1`
sum_id2=`expr $sum_id2 + 1`
cnt=`expr $cnt + 1`

# 수정 후 (bash 내장 산술 확장)
sum_id0=$((sum_id0 + 1))
sum_id1=$((sum_id1 + 1))
sum_id2=$((sum_id2 + 1))
cnt=$((cnt + 1))
```

**영향**:
- 외부 프로세스 호출 제거 → 성능 향상
- bash 내장 기능 사용 → 의존성 감소
- POSIX 표준 산술 확장 (`$(( ))`)
- 57, 61, 65행은 이미 Phase 1 (P0-1)에서 수정 완료

---

### ⚠️ P3-5. backup 디렉터리 배포 전략 재검토 (보류)

**대상 디렉터리**: `dist/wlan/opt/wlan/backup/`

**현재 상태**:
- 13개 디렉터리 및 파일 포함 (iptables, logrotate, samba, ntp.conf, rsyslog.conf, rc.local 등)
- 시스템 설정 백업 파일이 패키지에 포함되어 배포됨
- 타겟 시스템의 기존 설정과 충돌 가능성

**권장 사항**:
1. postinst에서 필요한 설정만 조건부 복사하도록 변경
2. 별도 `wlan-config` 패키지로 분리 검토
3. 설정 파일을 `/etc/` 직접 배치 대신 `/usr/share/wlan/templates/`로 이동 후 필요 시 복사

**보류 이유**:
- 프로젝트의 배포 전략과 관련된 구조적 변경
- 타겟 환경 특성 및 고객 요구사항 파악 필요
- postinst 스크립트 전면 재작성 필요
- v0.2.0에서 별도 이슈로 진행 권장

---

## 수정 통계

| 카테고리 | 파일 수 | 변경 유형 | 영향 |
|----------|---------|-----------|------|
| 명령 전환 | 1 | ifconfig → ip | 호환성 ↑ |
| 아카이브 정리 | 9개 파일 | archive 디렉터리 삭제 | 크기 감소 |
| 스크립트 개선 | 1 | 중복 방지 로직 추가 | 안정성 ↑ |
| 성능 최적화 | 1 | expr → 산술 확장 | 성능 ↑ |
| 전략 검토 | 1 | 보류 (구조적 변경) | - |
| **합계** | **4** | **~30행 수정, 9개 파일 삭제** | **효율성 향상** |

---

## 변경 파일 목록

```
dist/wlan/usr/local/scripts/
  └── wifi_init.sh                               (ifconfig → ip 전환)
dist/wlan/DEBIAN/
  └── config                                     (expr → 산술 확장 완료)
wlan-bridge/dumb/
  └── archive/                                   (디렉터리 삭제)
wlan-bridge/scripts/
  └── optimize-for-udp.sh                        (sysctl 중복 방지)
dist/wlan/opt/wlan/backup/                       (배포 전략 재검토 보류)
```

---

## 검증 포인트

### 1. 스크립트 문법 검증

```bash
# shellcheck 검증
shellcheck dist/wlan/usr/local/scripts/wifi_init.sh
shellcheck wlan-bridge/scripts/optimize-for-udp.sh
shellcheck dist/wlan/DEBIAN/config
```

### 2. 명령 호환성 테스트

```bash
# ip 명령 사용 가능 확인
which ip
ip link show

# ifconfig 명령이 더 이상 사용되지 않는지 확인
grep -r "ifconfig" dist/wlan/usr/local/scripts/
```

### 3. 산술 확장 동작 확인

```bash
# config 스크립트 테스트 (dry-run)
bash -n dist/wlan/DEBIAN/config
```

### 4. sysctl 중복 방지 테스트

```bash
# 타겟 시스템에서
sudo wlan-bridge/scripts/optimize-for-udp.sh eth0 mlan0
# 'y' 입력하여 영구 설정
# 스크립트 재실행
sudo wlan-bridge/scripts/optimize-for-udp.sh eth0 mlan0
# /etc/sysctl.conf에 중복 항목 없는지 확인
grep "net.core.wmem_max" /etc/sysctl.conf | wc -l  # 결과: 1
```

### 5. 기능 회귀 테스트

```bash
# 정상 부팅 테스트
systemctl start wifi_init
systemctl status wifi_init

# 인터페이스 상태 확인
ip link show mlan0
ip link show mlan1

# 모듈 로드 확인
lsmod | grep moal
```

---

## 주요 개선 효과

| 영역 | Phase 2 대비 개선 |
|------|-------------------|
| **호환성** | deprecated 명령 제거 → 최신 배포판 지원 |
| **성능** | 외부 프로세스 호출 제거 → 스크립트 실행 속도 향상 |
| **안정성** | 중복 방지 로직 → idempotent 동작 보장 |
| **유지보수성** | 아카이브 정리 → 소스 트리 단순화 |
| **크기** | ~100KB 감소 → 배포 패키지 최적화 |

---

## Phase 1-3 누적 개선 요약

| Phase | 항목 수 | 주요 개선 영역 |
|-------|---------|---------------|
| Phase 1 (P0 + P1) | 8 | 기능 버그 수정, 호환성 확보 |
| Phase 2 (P2) | 9 | 코드 품질, 유지보수성, 안정성 |
| Phase 3 (P3) | 4 | 기술 부채 정리, 성능 최적화 |
| **총합** | **21** | **프로덕션 품질 달성** |

### 전체 변경 통계
- **수정 파일**: 18개
- **삭제 파일**: 10개 (dumb-wrapper.sh + archive 9개)
- **변경 행**: ~230행 수정 + ~125행 삭제
- **품질 개선**: 7.5/10 → 9.5/10 (예상)

---

## 다음 단계

### v0.2.0 릴리스 준비

1. **버전 업데이트**:
   ```bash
   # dist/wlan/DEBIAN/control 수정
   Version: 0.2.0
   ```

2. **빌드 & 테스트**:
   ```bash
   ./build.sh
   sudo dpkg -i wlan-proc_0.2.0_arm64.deb
   ```

3. **타겟 시스템 검증**:
   - 부팅 시퀀스 정상 동작
   - 네트워크 브리지 정상 동작
   - 재부팅 루프 방지 메커니즘 동작
   - 시스템 안정성 장기 테스트

### P3-5 후속 작업 (선택)

backup 디렉터리 배포 전략 재검토가 필요한 경우:
1. postinst 스크립트 리팩토링
2. 설정 파일 템플릿화
3. 조건부 복사 로직 구현
4. 별도 wlan-config 패키지 분리 검토

---

## 릴리스 노트 초안 (v0.2.0)

### 개선 사항

**호환성**:
- deprecated ifconfig 명령을 최신 ip 명령으로 전환
- 최신 임베디드 리눅스 배포판 지원 강화

**성능**:
- bash 산술 확장으로 스크립트 실행 속도 향상
- 외부 프로세스 호출 최소화

**안정성**:
- 재부팅 루프 방지 메커니즘 추가 (5분 쿨다운, 3회 제한)
- sysctl 설정 중복 방지 로직 추가
- 방어적 스크립트 옵션 (set -euo pipefail) 적용

**코드 품질**:
- 죽은 코드 ~125행 제거
- 아카이브 파일 9개 정리
- 스크립트 통합 (dumb-wrapper.sh 제거)
- 하드코딩 제거 (호스트네임, 인터페이스명 일부)

**보안**:
- systemd 서비스 Capability 제한 강화
- 비밀번호 주석 완전 제거

### 버그 수정

- GPIO ID 계산 버그 수정 (도터보드 타입 감지 오류)
- dumb-tpacket.c 시그널 핸들러 안전성 수정
- postinst sed 플래그 중복 수정
- wifi_init.service 부팅 순서 수정

### 알려진 이슈

- P3-5 (backup 디렉터리 배포 전략) 미완료: 별도 작업 필요
- P2-8 (하드코딩 인터페이스명) 부분 완료: wifi_bridge.sh만 변수화

---

**작업 완료**: Phase 3 (P3-1 ~ P3-4)
**다음 단계**: v0.2.0 버전 업데이트 및 릴리스
