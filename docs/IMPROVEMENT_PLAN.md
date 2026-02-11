# wlan-package 코드 개선 계획서

**작성일**: 2026-02-11
**대상 버전**: wlan-proc v0.1.4
**분석 기반**: 종합 코드 분석 (Quality / Security / Performance / Architecture)

> sshd_config 관련 항목은 현재 환경에서 수정 불필요하므로 제외함.

---

## 개요

종합 분석 결과 총 **22건**의 개선 항목을 도출하였다.
C 소스 코드의 품질과 성능 최적화는 우수하나, 쉘 스크립트 품질과 패키지 메타데이터에
개선이 필요하다.

| 우선순위 | 건수 | 설명 |
|----------|------|------|
| P0 (즉시) | 2 | 기능 버그, 시그널 안전성 |
| P1 (단기) | 6 | shebang 불일치, 의존성 누락, 하드코딩 |
| P2 (중기) | 9 | 죽은 코드, 스크립트 안전성, 중복 제거 |
| P3 (장기) | 5 | deprecated 명령 전환, 아카이브 정리 |

---

## P0 - 즉시 수정 (기능 버그 / 안정성)

### P0-1. config 스크립트 변수 버그

- **파일**: `dist/wlan/DEBIAN/config`
- **위치**: 57~63행
- **문제**: `$_id` 사용 → `$id`이어야 함. GPIO ID 비트 합산이 항상 0이 됨.
- **영향**: 도터보드 타입 자동 감지 실패 → 모델 자동 선택 오동작 가능

```bash
# 현재 (버그)
id=`expr $_id + 1`
id=`expr $_id + 2`
id=`expr $_id + 4`

# 수정
id=$((id + 1))
id=$((id + 2))
id=$((id + 4))
```

### P0-2. dumb-tpacket.c 시그널 핸들러 안전성

- **파일**: `wlan-bridge/dumb/dumb-tpacket.c`
- **위치**: 95~128행
- **문제**: `print_stats()`가 시그널 핸들러에서 직접 호출됨.
  `fprintf`, `atomic_load`, `syslog`은 async-signal-safe가 아님.
  교착 상태 또는 데이터 손상 가능.
- **참고**: `dumb.c`는 이미 올바른 패턴(플래그 → 메인 루프에서 처리)을 사용 중.

```c
// 현재 (위험)
static void print_stats(int sig) {
    (void)sig;
    // fprintf, atomic_load 등 직접 호출
}
signal(SIGUSR1, print_stats);

// 수정: dumb.c 패턴 적용
static volatile sig_atomic_t print_stats_requested = 0;

static void sigusr1_handler(int sig) {
    (void)sig;
    print_stats_requested = 1;
}

// 메인 루프에서:
while (keep_running) {
    sleep(1);
    if (print_stats_requested) {
        print_stats_requested = 0;
        print_stats_impl();  // 안전한 컨텍스트
    }
}
```

- 추가: `keep_running`을 `volatile sig_atomic_t`로 변경하거나 `atomic_int`로 통일

---

## P1 - 단기 수정 (호환성 / 의존성)

### P1-1. wifi_bridge_stop.sh shebang 불일치

- **파일**: `dist/wlan/usr/local/scripts/wifi_bridge_stop.sh`
- **위치**: 1행, 6행
- **문제**: shebang이 `#!/bin/sh`이나 `[[ ]]` bash 문법 사용. dash 환경에서 실패.
- **수정**: shebang을 `#!/bin/bash`로 변경

### P1-2. Debian control 파일 의존성 누락

- **파일**: `dist/wlan/DEBIAN/control`
- **위치**: 6행
- **문제**: `Depends:` 필드가 비어있음. 런타임 의존성 미명시.
- **수정**:

```
Depends: libpcap0.8, systemd, jq
```

### P1-3. control Description 형식 오류

- **파일**: `dist/wlan/DEBIAN/control`
- **위치**: 13~28행
- **문제**: extended description 행이 공백으로 시작하지 않음. dpkg 경고 발생 가능.
- **수정**: 13행 이후 각 줄 앞에 공백 1칸 추가

```
Description: WLAN Application
 0.0.0 : Test version
 0.0.1 : demo version
 ...
```

### P1-4. postinst samba init.d 이동 시 존재 확인 누락

- **파일**: `dist/wlan/DEBIAN/postinst`
- **위치**: 195행
- **문제**: `/etc/init.d/samba` 파일이 없으면 에러 발생
- **수정**:

```bash
if [ -f /etc/init.d/samba ]; then
    mv /etc/init.d/samba /etc/init.d/samba.disabled
fi
```

### P1-5. postinst sed -i 플래그 중복

- **파일**: `dist/wlan/DEBIAN/postinst`
- **위치**: 317행
- **문제**: `sed -i "2s/.*/..." -i /etc/hosts` → `-i` 플래그 2회. 백업 파일 생성 오동작.
- **수정**:

```bash
sed -i "2s/.*/127.0.1.1 $hostname/" /etc/hosts
```

### P1-6. wifi_init.sh 변수 미인용

- **파일**: `dist/wlan/usr/local/scripts/wifi_init.sh`
- **위치**: 91행, 107행
- **문제**: `$MLAN0_MAC`, `$MLAN1_MAC` 변수가 인용 부호 없이 사용.
  MAC 주소에 공백이 들어오면 word splitting 발생.
- **수정**:

```bash
/usr/local/scripts/update_mac.sh mlan0 "$MLAN0_MAC"
/usr/local/scripts/update_mac.sh mlan1 "$MLAN1_MAC"
```

---

## P2 - 중기 수정 (코드 품질 / 유지보수성)

### P2-1. 주요 스크립트에 방어적 옵션 추가

- **대상 파일**:
  - `wifi_init.sh`
  - `wifi_checker.sh`
  - `wifi_bridge.sh`
  - `wifi_bridge_start.sh`
- **수정**: 스크립트 상단에 추가:

```bash
set -euo pipefail
```

> 단, `wifi_checker.sh`는 무한 루프 데몬이므로 `set -e`가 조기 종료를
> 유발하지 않도록 에러 핸들링 코드를 함께 점검해야 함.

### P2-2. 주석 처리된 죽은 코드 정리

- **대상 파일 및 예상 제거량**:

| 파일 | 죽은 코드 행 (약) | 비고 |
|------|-------------------|------|
| `wifi_bridge.sh` | ~30행 | 주석 처리된 relayd, sysctl, insmod 등 |
| `wifi_init.sh` | ~40행 | 주석 처리된 scan, ifconfig, ip link 등 |
| `postinst` | ~40행 | END 블록 내 주석, 패스워드 하드코딩 등 |
| `wifi_checker.sh` | ~15행 | END 블록 내 iw link 로직 |

- **주의**: `postinst:301-303`의 주석 처리된 비밀번호 하드코딩(`root:root`, `semes:semes`)은
  보안 상 우선 제거

### P2-3. wifi_bridge.sh와 dumb-wrapper.sh 통합

- **파일**: `dist/wlan/usr/local/scripts/wifi_bridge.sh`, `dumb-wrapper.sh`
- **문제**: 두 스크립트가 거의 동일한 로직 수행
  (both_up 체크 → exec wifi-dumb). 유지보수 부담.
- **수정 방안**:
  - `wifi_bridge.sh`를 메인으로 유지
  - `dumb-wrapper.sh` 삭제 또는 `wifi_bridge.sh`로 심볼릭 링크
  - `wifi_bridge@.service`의 ExecStart가 `wifi_bridge.sh`를 가리키므로 영향 없음

### P2-4. wifi_dumb@.service와 wifi_bridge@.service 역할 명확화

- **파일**: `etc/systemd/system/wifi_dumb@.service`, `wifi_bridge@.service`
- **문제**: 두 서비스 모두 dumb bridge를 실행하는 유사 기능.
  `wifi_dumb@`은 Capability 제한이 없고, `wifi_bridge@`은 올바르게 제한됨.
- **수정 방안**:
  - `wifi_dumb@.service`를 deprecated로 표시하거나 삭제
  - 또는 `wifi_dumb@.service`에도 동일한 Capability/리소스 제한 적용

### P2-5. wifi_init.service 부팅 순서 정리

- **파일**: `etc/systemd/system/wifi_init.service`
- **문제**: `Before=sysinit.target` + `DefaultDependencies=no`이면서
  `WantedBy=multi-user.target`. 부팅 순서 모순.
  sysinit 이전에 실행하려면 `WantedBy=sysinit.target`이어야 함.
- **수정**:

```ini
[Unit]
Description=WiFi init Service
After=local-fs.target
Wants=local-fs.target

[Service]
Type=oneshot
ExecStart=/usr/local/scripts/wifi_init.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
```

### P2-6. wifi_checker.sh 재부팅 안전장치 강화

- **파일**: `dist/wlan/usr/local/scripts/wifi_checker.sh`
- **위치**: 98~108행, 161~165행
- **문제**: ERR_CNT > LIMIT_CNT(3)이면 `reboot` 직접 호출.
  빠른 반복 실패 시 부팅 루프 진입 가능.
- **수정 방안**:
  - 재부팅 쿨다운 타이머 추가 (예: 마지막 재부팅 후 5분 이내면 재부팅 억제)
  - 재부팅 횟수를 persistent 파일에 기록하여 부팅 루프 감지

```bash
REBOOT_COUNT_FILE="/var/log/cantops/reboot_count"
MAX_REBOOT_COUNT=3
# 재부팅 전 카운트 확인
```

### P2-7. postinst에서 하드코딩된 호스트네임 제거

- **파일**: `dist/wlan/DEBIAN/postinst`
- **위치**: 12행
- **문제**: `hostname=Cantops` 하드코딩. 다른 고객/프로젝트 재사용 어려움.
- **수정**: config.json 또는 debconf 변수에서 읽기

### P2-8. 하드코딩된 인터페이스명/IP 통합

- **대상**: `wifi_bridge.sh:115`, `dumb-wrapper.sh:29`, `wifi_bridge_start.sh:21-32`
- **문제**: `eth0`, `mlan0`, `192.168.4.0/24` 등이 스크립트 곳곳에 하드코딩.
- **수정 방안**: `config.json`에서 인터페이스명과 네트워크 설정을 읽도록 통합.
  현재 `wifi_init.sh`는 이미 `config.json`을 사용하고 있으므로
  동일 패턴을 다른 스크립트에 확장.

### P2-9. postinst 주석 처리된 비밀번호 제거

- **파일**: `dist/wlan/DEBIAN/postinst`
- **위치**: 301~304행
- **문제**: 주석이지만 `root:root`, `semes:semes` 비밀번호 노출.
  코드 리뷰나 감사에서 문제 제기 가능.
- **수정**: 해당 주석 블록 완전 삭제

---

## P3 - 장기 수정 (기술 부채 정리)

### P3-1. ifconfig → ip 명령 마이그레이션

- **대상**: `wifi_init.sh:49-56`, 기타 스크립트
- **이유**: `ifconfig`는 net-tools 패키지 소속이며 deprecated.
  최신 임베디드 리눅스에서 미설치 가능.
- **수정 예시**:

```bash
# 현재
cmd=$(ifconfig | grep mlan0)
if [ -n "$cmd" ]; then
    ifconfig mlan0 down
fi

# 변경
if ip link show mlan0 &>/dev/null; then
    ip link set mlan0 down
fi
```

### P3-2. dumb/archive/ 디렉터리 제거

- **파일**: `wlan-bridge/dumb/archive/` (파일 8개)
- **이유**: Git 히스토리에서 이전 버전 추적 가능. 배포 패키지 크기 증가.
- **수정**: 디렉터리 삭제, `.gitignore`에 추가 불필요 (Git에서 추적 중단)

### P3-3. optimize-for-udp.sh sysctl.conf 중복 append 방지

- **파일**: `wlan-bridge/scripts/optimize-for-udp.sh`
- **위치**: 94~113행
- **문제**: 스크립트 반복 실행 시 `/etc/sysctl.conf`에 동일 설정 중복 추가
- **수정**: append 전 기존 항목 확인

```bash
grep -q "^net.core.wmem_max=" /etc/sysctl.conf || \
    echo "net.core.wmem_max=16777216" >> /etc/sysctl.conf
```

### P3-4. config 스크립트 expr → 산술 확장 전환

- **파일**: `dist/wlan/DEBIAN/config`
- **위치**: 43~53행
- **이유**: `` `expr` `` 은 외부 프로세스 호출. `$(( ))` bash 내장이 효율적.
- **수정 예시**:

```bash
# 현재
sum_id0=`expr $sum_id0 + 1`

# 변경
sum_id0=$((sum_id0 + 1))
```

### P3-5. dist/wlan/opt/wlan/backup/ 배포 전략 재검토

- **디렉터리**: `dist/wlan/opt/wlan/backup/`
- **문제**: 시스템 설정 백업 파일(ssh, samba, logrotate 등)이 패키지에 포함.
  타겟 시스템 설정과 충돌 가능.
- **수정 방안**: postinst에서 필요한 설정만 조건부 복사하는 방식으로 전환하거나,
  별도 `wlan-config` 패키지로 분리.

---

## 작업 순서 요약

```
Phase 1 (P0 + P1) ─── 기능 버그 수정 + 호환성 확보
  ├── P0-1: config $_id 버그
  ├── P0-2: dumb-tpacket 시그널 안전성
  ├── P1-1: wifi_bridge_stop.sh shebang
  ├── P1-2: control Depends 추가
  ├── P1-3: control Description 형식
  ├── P1-4: postinst samba 존재 확인
  ├── P1-5: postinst sed -i 중복
  └── P1-6: wifi_init.sh 변수 인용

Phase 2 (P2) ─── 코드 품질 + 유지보수성
  ├── P2-1: 방어적 스크립트 옵션
  ├── P2-2: 죽은 코드 정리
  ├── P2-3: 스크립트 통합
  ├── P2-4: 서비스 역할 명확화
  ├── P2-5: 부팅 순서 정리
  ├── P2-6: 재부팅 안전장치
  ├── P2-7: 하드코딩 호스트네임
  ├── P2-8: 인터페이스/IP 통합
  └── P2-9: 비밀번호 주석 제거

Phase 3 (P3) ─── 기술 부채
  ├── P3-1: ifconfig → ip
  ├── P3-2: archive 디렉터리
  ├── P3-3: sysctl 중복 방지
  ├── P3-4: expr → 산술 확장
  └── P3-5: backup 배포 전략
```

---

## 버전 계획

| 버전 | 포함 항목 | 비고 |
|------|-----------|------|
| v0.1.5 | Phase 1 (P0 + P1) | 버그 수정 + 호환성 |
| v0.1.6 | Phase 2 (P2) | 코드 품질 개선 |
| v0.2.0 | Phase 3 (P3) | 기술 부채 정리 |
