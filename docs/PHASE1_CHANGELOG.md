# Phase 1 수정 완료 보고서

**수정 일시**: 2026-02-11
**대상 버전**: wlan-proc v0.1.4 → v0.1.5 (예정)
**수정 범위**: P0 (즉시) + P1 (단기)

---

## 수정 완료 항목

### P0 - 즉시 수정 (기능 버그 / 안정성)

#### ✅ P0-1. config 스크립트 변수 버그

- **파일**: `dist/wlan/DEBIAN/config`
- **수정 내용**:
  - 57~65행: `$_id` → `$id` 변경
  - `` `expr` `` → `$(( ))` 산술 확장으로 전환 (성능 개선)
- **영향**: GPIO ID 비트 합산 오류 수정, 도터보드 타입 자동 감지 정상화

```bash
# 수정 전
id=`expr $_id + 1`

# 수정 후
id=$((id + 1))
```

#### ✅ P0-2. dumb-tpacket.c 시그널 안전성

- **파일**: `wlan-bridge/dumb/dumb-tpacket.c`
- **수정 내용**:
  1. `keep_running`을 `atomic_int`로 변경 (31~32행)
  2. `print_stats_requested` 플래그 추가 (34행)
  3. `print_stats()` → `print_stats_impl()` 이름 변경 (105행)
  4. `sigusr1_handler()` 새로 추가 (시그널 핸들러, 100~103행)
  5. 메인 루프에서 플래그 확인 후 `print_stats_impl()` 호출 (638~643행)
  6. `atomic_load(&keep_running)` 패턴으로 변경 (376행, 638행)
- **영향**: 시그널 핸들러에서 비안전 함수 호출 제거, 경쟁 조건 해결

```c
// 수정 전 (위험)
static void print_stats(int sig) {
    fprintf(stderr, ...);  // async-signal-unsafe
}
signal(SIGUSR1, print_stats);

// 수정 후 (안전)
static void sigusr1_handler(int sig) {
    print_stats_requested = 1;
}
signal(SIGUSR1, sigusr1_handler);

// 메인 루프
while (atomic_load(&keep_running)) {
    if (print_stats_requested) {
        print_stats_requested = 0;
        print_stats_impl();
    }
}
```

---

### P1 - 단기 수정 (호환성 / 의존성)

#### ✅ P1-1. wifi_bridge_stop.sh shebang 불일치

- **파일**: `dist/wlan/usr/local/scripts/wifi_bridge_stop.sh`
- **수정**: 1행 `#!/bin/sh` → `#!/bin/bash`
- **영향**: dash 환경에서 `[[ ]]` 문법 오류 해결

#### ✅ P1-2. control Depends 추가

- **파일**: `dist/wlan/DEBIAN/control`
- **수정**: 6행 `Depends:` → `Depends: libpcap0.8, systemd, jq`
- **영향**: 패키지 설치 시 런타임 의존성 자동 검증

#### ✅ P1-3. control Description 형식 수정

- **파일**: `dist/wlan/DEBIAN/control`
- **수정**: 13~28행 각 줄 앞에 공백 1칸 추가
- **영향**: Debian 패키지 형식 준수, dpkg 경고 제거

#### ✅ P1-4. postinst samba 존재 확인

- **파일**: `dist/wlan/DEBIAN/postinst`
- **수정**: 195행 앞에 `if [ -f /etc/init.d/samba ]; then` 조건 추가
- **영향**: samba 파일 부재 시 에러 방지

```bash
# 수정 전
mv /etc/init.d/samba /etc/init.d/samba.disabled

# 수정 후
if [ -f /etc/init.d/samba ]; then
  mv /etc/init.d/samba /etc/init.d/samba.disabled
fi
```

#### ✅ P1-5. postinst sed -i 중복 제거

- **파일**: `dist/wlan/DEBIAN/postinst`
- **수정**: 317행 `sed -i "..." -i` → `sed -i "..."`
- **영향**: sed 플래그 중복 제거, 백업 파일 오동작 방지

```bash
# 수정 전
sed -i "2s/.*/127.0.1.1 $hostname/g" -i /etc/hosts

# 수정 후
sed -i "2s/.*/127.0.1.1 $hostname/" /etc/hosts
```

#### ✅ P1-6. wifi_init.sh 변수 인용 추가

- **파일**: `dist/wlan/usr/local/scripts/wifi_init.sh`
- **수정**: 91행, 107행 `$MLAN0_MAC`, `$MLAN1_MAC` → `"$MLAN0_MAC"`, `"$MLAN1_MAC"`
- **영향**: MAC 주소 변수에 공백 포함 시 word splitting 방지

---

## 수정 통계

| 카테고리 | 파일 수 | 총 변경 행 수 |
|----------|---------|---------------|
| C 소스 | 1 | ~20행 |
| 쉘 스크립트 | 2 | ~5행 |
| Debian 메타데이터 | 2 | ~20행 |
| **합계** | **5** | **~45행** |

---

## 빌드 & 테스트 권장사항

### 1. wlan-bridge 빌드

```bash
cd wlan-bridge/dumb
make clean
make release
make debug
```

**검증 포인트**:
- 컴파일 경고 없음
- `dumb-tpacket` 실행 후 `kill -USR1 <pid>` 시 통계 정상 출력
- 메인 루프 안정성 (시그널 처리)

### 2. Debian 패키지 빌드

```bash
cd /home/jhw/ai/opencode/projects/wlan-package
./build.sh
```

**검증 포인트**:
- 빌드 에러 없음
- `dpkg -I release/wlan-proc-0.1.5.deb` 출력 확인:
  - `Depends: libpcap0.8, systemd, jq` 정상
  - Description 형식 정상 (들여쓰기)

### 3. 설치 테스트 (타겟 시스템)

```bash
dpkg -i wlan-proc-0.1.5.deb
```

**검증 포인트**:
- postinst 에러 없음 (samba 파일 부재 시에도 정상 진행)
- `/etc/hosts` 2행 정상 수정
- `/usr/local/bin/wifi-dumb` 심볼릭 링크 정상

### 4. 런타임 테스트

```bash
# dumb-tpacket 실행
systemctl start wifi_bridge@mlan0

# 통계 확인 (SIGUSR1)
systemctl status wifi_bridge@mlan0
kill -USR1 <pid>

# 로그 확인
journalctl -u wifi_bridge@mlan0 -f
```

---

## 다음 단계

Phase 2 (P2 - 중기 수정) 진행:
- P2-1: 주요 스크립트에 방어적 옵션 추가 (`set -euo pipefail`)
- P2-2: 주석 처리된 죽은 코드 정리 (~125행)
- P2-3: `wifi_bridge.sh`와 `dumb-wrapper.sh` 통합
- ... (총 9개 항목)

또는 v0.1.5 버전 릴리스 후 Phase 2 진행 권장.
