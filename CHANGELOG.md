# Changelog

wlan-proc 패키지의 상세 변경 이력입니다. 버전당 한 줄 요약과 전체 버전 목록은
`dist/wlan/DEBIAN/control`의 Description 필드를 참조하세요.

## 0.6.1 (2026-08-31)

> SemVer **patch** — 패키지 업그레이드 후 `wifi_init` 자식 유닛이 내려간 채 남던 것을 고친다. 동작 계약 변경 없음.

### 업그레이드 후 로거 미기동 수리 (#218)

- `prerm` 이 `wifi-stack.target` 을 stop 하면 `PartOf=` 로 묶인 자식 유닛(`wifi_logger@mlanN`, `wifi_logger_link@mlanN` 등)이 함께 내려간다. `postinst` 가 부르는 `wifi_apply_enabled.sh` 는 **enable/disable 상태만** 다루고 start 는 하지 않아(`no change (all units already in desired state)`) 내린 주체는 있는데 올리는 주체가 없었다. 결과적으로 **재부팅 전까지 로거가 죽은 채** 남았다.
- 증상이 조용하다. `wpa_supplicant` 는 살아 있어 무선 통신은 계속되고 관측만 죽는다 — `link.json` 이 사라져 로밍 판정 소스가 없어지고, SNMP 는 실제 연결 중인데 `StaLoginState=notConnected(1)` 로 보고하며 walk 객체수가 39에서 30으로 준다.
- `postinst` 가 `wifi_apply_enabled.sh` 직후 `wifi_services.sh start` 를 호출한다. 부팅 경로(`wifi_init.service` 의 `ExecStartPre` → `ExecStartPost`)와 **같은 스크립트를 같은 순서로** 재사용해, 업그레이드 직후 상태가 부팅 직후 상태와 같아지게 한다.
- `wifi_services.sh` 는 `is-enabled` 인 유닛만 `start --no-block` 하므로 멱등하고(이미 떠 있으면 no-op) disable 된 유닛은 건드리지 않는다. MFG 프로파일 가드도 그대로 적용된다.
- `wifi_init` 이 active 일 때만 호출한다. "자식만 내려간 상태"를 복구한다는 뜻이며, `wifi_init` 자체가 정지 중이면 다음 부팅에 함께 올라오는 것이 맞다.
- 0.4.0 배포 기록에도 설치 후 게이트를 수동 실행한 흔적이 있어, 이 갭은 이번에 생긴 것이 아니라 계속 있었다.

## 0.6.0 (2026-08-31)

> SemVer **minor** — SNMP 벤더 OID 트리를 CanTops 자체 PEN(66620)으로 이전하고 CONTEC 호환을 병행한다. 폴링은 두 루트를 모두 서빙하므로 기존 NMS 는 설정 변경 없이 계속 동작하지만, **트랩 OID 는 `.672.65.1.1.x` 에서 `.66620.1.1.1.x` 로 바뀐다**(트랩은 `snmp.trap.enabled` 기본 false 라 opt-in 한 기기에만 영향). 상위 툴용 FTP 계정 `admin` 을 postinst 가 생성한다.

### SNMP — CanTops PEN 66620 정본 루트 (#212)

- IANA PEN **66620 = CanTops Co., LTD.** 아래 `.1.3.6.1.4.1.66620.1`(product 1 = CTS-WLAN)을 정본 루트로 노출한다. 서브OID 구조는 종전과 동일해 루트만 바뀐다(예: FirmwareVersion = `.1.3.6.1.4.1.66620.1.2.2.0`).
- CONTEC FXE3000 `.672.65` 는 레거시 NMS 호환을 위해 **폴링에서 병행 서빙**한다. 같은 데이터를 두 이름으로 준다.
- 담당 루트는 `snmpd.conf` 의 `pass_persist` 가 PROG 인자로 넘기고 백엔드가 `sys.argv[1]` 로 받는다. `pass_persist` 는 등록 루트마다 별도 프로세스를 띄우므로 각 프로세스가 자기 루트만 서빙한다. 두 루트를 한 맵에 담으면 수치정렬상 `.672 < .66620` 이라 `.672.65` 트리 끝의 GETNEXT 가 등록 범위 밖 OID 를 반환하게 되므로, 프로세스 분리가 정확성 요구다.
- 인자가 숫자 OID 가 아니면 하위호환 루트로 폴백한다. 그대로 쓰면 매 요청 예외로 프로세스는 살아있는데 아무것도 서빙하지 않는 조용한 고장이 된다.
- **CONTEC 호환 종료 시**: `snmpd.conf` 의 해당 `pass_persist` 줄 1개만 지우면 된다(백엔드·트랩 무수정). `postinst` 가 `cp` 로 `/etc/snmp/snmpd.conf` 를 덮어쓰므로 업그레이드만으로 전파되며 별도 마이그레이션이 필요 없다.
- 병행 비용은 백엔드 프로세스 2개(온타겟 RSS 36MB x 2, 보드 available 1,551MB 대비 4.6퍼센트).

### SNMP — CONTEC MIB 정합 및 트랩 varbind 갭 (#214)

- MIB 의 `fxe3000TrapChannelChange` 는 `OBJECTS { fxe3000WIFInfoApChannel }` 로 `.3.3.1.10.2` 를 varbind 에 지정하는데, 폴링이 `.3.3.1.10.x`(AP 그룹)를 "AP 모드용이라 STA 에 무의미"로 보고 서빙하지 않았다. 그 결과 **트랩을 받은 NMS 가 varbind 의 OID 를 조회하면 noSuchInstance** 가 났다.
- `.3.3.1.10.1 ApEssId` / `.3.3.1.10.2 ApChannel` 을 접속 중인 AP 의 SSID·채널로 채운다(값은 Sta 계열 `.11.3`/`.11.4` 와 동일). `.10.3 ApLoginNum` 은 AP 가 세는 접속 단말 수라 STA 가 알 수 없어 계속 미노출한다.
- 노출 인스턴스 28 -> 30(연결 상태 기준). 접근가능 MIB leaf 61개 중 **37개 구현**.

### SNMP — CanTops MIB 정의 파일 (#216)

- `/opt/wlan/config/snmp/CANTOPS-CTS-WLAN-MIB.txt` 신설. NMS 가 우리 트리를 숫자 OID 로만 보던 문제를 없앤다. OID 트리는 CONTEC FXE3000 을 arc 단위로 미러링해, FXE3000 을 폴링하던 매니저가 enterprise arc 두 개만 바꾸면 같은 객체에 닿는다.
- **실제로 서빙하는 것만 선언한다** — 37객체 + 트랩 2종. FXE3000 에 있으나 우리가 주지 않는 24개는 파일 끝에 사유와 함께 주석으로 남겼다(ConnectedNode·WIFCert·DipSwitch·LoaderVersion·드라이버 미제공 통계 등). MIB 는 계약이므로 미지원 객체를 선언하면 매니저가 noSuchInstance 를 받는다.
- **MacAddress 4종은 `DisplayString` 으로 선언한다.** FXE3000 은 이들을 `OCTET STRING (SIZE (6))` 로 타이핑하지만 구현은 콜론헥사 텍스트를 반환하므로, "선언한 대로"가 아니라 "주는 대로" 적었다. 규격 정합은 AgentX 전환이 필요하며 별도 과제로 둔다.
- DESCRIPTION 에 값의 성격을 명시했다 — 고정값(LedPower 상수 on / WLM `managed` / UnitType `Station`), 유도값(LED 는 링크 상태 파생, WirelessMode 는 tx bitrate 파생), 근사값(octet 카운터는 멀티캐스트 포함·재연결 시 0 리셋으로 가짜 wrap 가능), 결측 시 0 대신 noSuchInstance 를 주는 이유.
- MIB 와 구현이 따로 놀지 않도록 회귀 테스트 4종을 둔다(선언 집합 == 서빙 집합 양방향 / MIB 트랩 == 트랩 스크립트 / MacAddress SYNTAX / SMIv1 `ACCESS` 잔존 금지).

### FTP 계정 정비 (#210, #211)

- **상위 툴용 FTP 계정 `admin` 을 postinst 가 생성한다.** ftpcmd 디스패치(`quote rst`/`ifcup`/`ifcdown`)를 호출하려면 전용 계정으로 FTP 로그인해야 하는데 그 계정을 만드는 곳이 아무 데도 없었다. 이미지 레시피의 `extrausers` 는 rootfs 조립 시점에만 도는 클래스라 .deb 배포 경로에서는 계정이 생기지 않고, `ftpcmd-handlers` 패키지에는 postinst 가 없다. 이 제품은 이미지 교체 없이 .deb 로 배포하므로 종전 상태로는 필드에서 상위 툴이 로그인할 수 없었다.
- 비밀번호는 **SHA-512 해시로 넣는다**(`chpasswd -e`). postinst 는 기기에 `/var/lib/dpkg/info/wlan-proc.postinst` 로 0755 권한으로 설치되어 비-root 사용자가 읽을 수 있으므로, 평문을 두면 그대로 노출된다. 주석에도 값을 적지 않는다 — 규칙은 "해시를 쓴다"가 아니라 "기기에 실리는 산출물에 평문을 두지 않는다"다.
- **소비자 없던 root FTP 개방을 제거한다.** postinst 가 `/etc/vsftpd.ftpusers` 와 `/etc/vsftpd.user_list` 의 root 줄을 주석 처리해 왔으나, 두 파일은 vsftpd 패키지 소유이면서 conffile 이 아니라 vsftpd 설치·업그레이드 시 무조건 덮어써진다. 실기에서 이미 효과가 사라져 있었고(USER root 는 530 반환), 우리 툴링은 root FTP 를 쓰지 않는다.

### 문서 정정 (#209)

- `wifi rate` 출력과 노트에서 미검증 단정을 조건부·정본 안내로 고치고, mlan1 은 관측으로도 메워지지 않는 범위임을 명시한다.

## 0.5.5 (2026-08-14)

> SemVer **patch** — Factory Reset의 nginx enable을 유닛 존재 가드로 감싼다. 동작 계약은 변하지 않는다.

### Factory Reset nginx 처리 정리

- `customctl enable nginx`를 `systemctl cat nginx.service` 성공 시에만 실행한다. nginx 미탑재 이미지에서 남던 `systemctl enable failed: nginx` err 로그가 사라진다. 유닛이 있는데 enable이 실패하면 종전대로 err만 남기고 Factory Reset은 계속한다.
- `FACTORY_OPTIONAL_UNITS`를 쓰지 않은 이유를 코드 주석에 남겼다. 그 경로는 유닛이 존재하는데 enable이나 후조건이 실패하면 `critical_failures`로 이어져 `reboot inhibited` + `exit 1`로 Factory Reset을 중단시킨다. 0.5.4가 `FACTORY_REQUIRED_UNITS`에서 nginx를 뺀 취지 — "nginx가 없거나 비정상이어도 Factory Reset은 실패하지 않는다" — 와 어긋나므로 채택하지 않았다.

## 0.5.4 (2026-08-14)

> SemVer **patch** — nginx를 Factory Reset 필수 유닛에서 분리하고, Factory Reset 유선 공장 주소를 제품 기본값으로 되돌린다. `config.json` 완전 제거 계약은 유지한다.

### Factory Reset 소유권·기본값 정정

- Factory Reset의 `eth0` 공장 기본 주소를 임시 타겟 검증값 `192.168.214.5/24`에서 제품값 `192.168.1.1/24`로 복원한다. 이 주소는 **Factory Reset 후 복구 경로**다. Factory Reset은 WPA 설정까지 기본값으로 되돌리므로 기본 SSID가 현장에 없으면 무선이 붙지 않는 것이 정상이며, 이때 `192.168.1.0/24` 호스트를 `eth0`에 연결해 `192.168.1.1`로 접속하여 재설정한다. 0.5.2가 이 값을 시험용 `192.168.214.5/24`로 승격시켜 복구 경로가 사이트 시험값으로 바뀌어 있었다.
- 일반 패키지 업그레이드는 active 네트워크 설정을 계속 보존하므로(`postinst`의 `cpchk`), 이 값은 신규 설치와 Factory Reset에만 적용된다.
- nginx는 표준 제품 이미지가 제공하는 선행조건이므로 `FACTORY_REQUIRED_UNITS`에서 제외한다. nginx가 없거나 비정상이어도 Factory Reset은 실패하지 않는다.
- 다만 0.5.0 이하의 Factory Reset이 `customctl disable nginx`로 영속 disable 시킨 기기는 스스로 복구되지 않으므로, `customctl enable nginx`는 그대로 유지한다. `wlan-proc`이 만든 피해만 되돌리는 범위다.
- `/usr/local/etc/config.json`은 호환 유지 대상이 아니라 완전 제거 대상이라는 기존 계약을 유지한다. 업그레이드 시 active 잔재를 삭제하고, 패키지·CI·release gate의 재유입 금지 검사도 유지한다.

## 0.5.3 (2026-08-14)

> SemVer **patch** — 시스템/인터페이스 로거 제어를 대칭화하고, 시스템 로거 자식을 개별 감독하며 외부 명령 hang을 제한한다. 로그 포맷·무선 설정·와이어 프로토콜 변경 없음.

### 로거 그룹 제어·장애 격리

- `wifi log system ...`과 `wifi {mlan0|mlan1|eth0} log ...`에 `start|stop|restart|status|enable|disable` 6개 동작을 제공한다. 런타임 동작과 부팅 정책을 분리했으며 인터페이스 일괄 명령은 두지 않는다. 기존 `logctl.sh`는 system 명령 호환 래퍼로 유지한다.
- 시스템 로거를 CPU/MMC/TEMP/MCP/SUMMARY 5개 systemd 자식으로 분리하고 `wifi_logger.service`를 중앙 controller로 전환했다. 각 자식 장애는 독립적으로 관측·복구되며, 인터페이스 link/scan/stat/snapshot을 포함한 재시작은 300초당 10회·3초 간격으로 제한한다.
- `.logger.enabled=true`, `.eth0.logger.enabled=true`를 명시하고 mlan0=true/mlan1=false 기존 정책과 함께 `wifi_apply_enabled.sh`에서 단일 동기화한다. 시스템 `stop|disable`은 TEMP와 과열 보호를 함께 중단하므로 CLI가 명시적으로 경고한다.

### Hang 제한·온도 안전성

- stat/snapshot/CPU/MMC/MCP 외부 명령과 sysfs 읽기를 5초로 제한하고, WLAN 온도 조회는 인터페이스당 3초로 제한했다. `bc`와 `sysstat`을 명시적 런타임 의존성으로 승격했다.
- 온도 timeout·비정상 응답을 `0°C`로 변환하지 않고 유효성 상태와 `unknown`으로 기록한다. 임계값 비교는 유효 샘플에만 수행하며, cooldown 복구는 CPU와 존재하는 WLAN 센서가 모두 유효하고 복구 임계 미만일 때만 기존 snapshot→reboot 경로를 실행한다.
- 로컬 단위·release gate 검증까지 수행하며, 실제 타겟 설치·재부팅 검증은 별도 배포 단계로 유보한다.

## 0.5.2 (2026-08-13)

> SemVer **patch** — Factory Reset 복구 트랜잭션·CAL/backup 정책·재부팅 게이트를 양산 수준으로 강화하고, 공장 유선 관리 주소와 i.MX93 SDIO/WLAN-only 펌웨어를 타겟 검증값으로 고정한다. 설정 키·와이어 포맷 변경 없음.

### Factory Reset 복구 강화

- **유선 관리 경로 유지** — Factory Reset 기본 `eth0` 주소를 `192.168.214.5/24`로 통일해 reset/reboot 뒤에도 동일 유선 경로로 재접속한다. 일반 업그레이드는 active 주소를 보존하며 reset 시 공장값이 적용된다.
- **필수 WLAN payload 원자 복원·후조건 검증** — FW 설정, WPA service/conf, 3개 `.network`를 staged install·sync·atomic rename·내용/owner/mode 후조건으로 복구한다. WPA/network/mod_para/txpower `.bak`도 공장값으로 재시드해 이전 자격증명·IP·FW 설정 부활을 막는다. 중간 실패는 전체 rollback하고 reboot을 금지한다.
- **생산 CAL 보존 정합화** — 선택된 custom CAL은 포맷 검증 후 active/backup/marker를 보존·복구하고, package CAL 및 선택되지 않은 잔재는 baseline으로 재시드하거나 제거한다. CAL helper/sync/후조건 실패는 reset 실패로 처리한다.
- **재부팅·서비스 계약** — 장시간 버튼 경로가 reset 실패 rc를 보존하며 `factory_reset.sh`만 reboot을 소유한다. 표준 이미지 선행조건인 `nginx.service`와 핵심 WLAN 유닛을 preflight·enable·후조건에서 검증한다.
- WPA active/backup을 `root:root 0600`, 나머지 관리 payload를 `root:root 0644`로 정규화하고, MOD_PARA 최종 self-healing source를 canonical `/opt/wlan/config/wlan/wifi_mod_para.conf`로 수정했다.

### SDIO WLAN firmware·출하 게이트

- i.MX93 **SDIO/WLAN-only** `sd9098_wlan_v1.bin`을 NXP `lf-6.18.20_2.0.0/FwImage_9098_SD`의 17.92.1.p149.115로 갱신했다(SHA256 `7c3ef6e12d3cfc9bd638d1571ccf6ddd2e96e0ed179ec70664ccb1df0ba29e57`). PCIe, combo/BT, MFG firmware는 이번 변경 범위가 아니며 p149.115로 주장하지 않는다.
- NXP branch-matched `LICENSE.txt`, `SCR-imx-firmware.txt`, immutable source/commit/blob/hash manifest를 패키지에 포함하고 빌드·패키지 게이트에서 검증한다. 펌웨어는 NXP 기반 Authorized System의 일부로만 배포해야 한다.
- release gate가 FW SHA256/size, factory eth0 주소, 네트워크 템플릿 0644, firmware notice/provenance를 검증한다.

## 0.5.1 (2026-08-10)

> SemVer **patch** — DBDC 선행 정비(다중 iface 격리) + factory reset 위생 + 실기 튜닝 기본값 승격 + link.json 마감 스케줄링. 설정 키 추가/제거 없음(값·템플릿 변경만), 와이어 포맷 변경 없음.

### DBDC 선행 정비 (#157~#159)

- **roam 상태 파일 iface별 분리** — `/run/wifi/{roam_condition,last_roam_scan}_<iface>` + atomic write. 기본 인자 def-시점 바인딩 탓에 재대입이 bgscan `get_flag()`에 반영되지 않던 실버그를 센티널로 수정, reader 데몬의 미호출 writer 제거(#157).
- **dmesg 스캔 소유 시간창(30s) 판정** — COMPLETED에 iface가 없는 실측 포맷에서 동시 스캔 오귀속을 먼저/나중 관찰자 양방향 차단(즉시-리셋 방식의 사각지대 교체)(#158).
- **autonomous-bgscan 가드 mlan1 확장** + 보조 탐지를 현재 supplicant 실행(InvocationID) journal로 한정 — wpa.log 누적에 의한 구 릴리스 잔재 오경고 제거(#159).

### factory reset 위생 (#160)

- 공장 초기화가 하드웨어·생산 설정(보드 감지, MAC base 등)을 보존하고, 클론 MAC 잔재로 여러 기기가 같은 MAC으로 부팅하던 문제 수정. `.link` 대상 판정 견고화(다중 OriginalName·판정불가 드롭인).

### 기본값 승격·설정 (#161, #162)

- **실기 튜닝값 승격**: rate_adapt 70/90, DIFF_TH 7, ping_pong detection_time 10, mcs_tier 기본 적용(ht/vht "7", he "both 7"), mlan1 로밍 키를 mlan0과 정렬. 코드 상수·wifi.sh fallback·스키마·handoff 4축 동기화(#161).
- **config.json overlay 템플릿 신설** + factory_reset 배포 + nginx 명시 enable(과거 리셋으로 disabled된 기기 복구)(#161).
- **link.json 생산 0.95→0.9s + 마감(deadline) 스케줄링** — 고정 sleep의 실주기 초과(작업시간+interval)를 수정해 roam tick(1s) 대비 신선도를 실제로 보장. `Device.Country` KR(#162).

### MCS association 전 검증 안정화

- AP가 없는 factory reset 부팅에서 88W9098 mlan0의 HE GET이 `0x0000`으로만 보여 `wifi_init` 재시작·비상 reboot가 반복되던 문제 수정. HT/VHT가 일치하고 HE Tx/Rx만 0x0000인 경우에 한해 association을 허용하고 per-iface pending을 남긴다.
- `wifi_event@mlan0`이 INITIAL CONNECTED/CONNECTED/ROAMED에서 검증한다. 첫 association이 HE를 FW 기본값으로 되돌리면 connected SET으로 다음 association 값을 저장하고 1회만 reassociate한 뒤 GET으로 확정한다. 성공 시 pending/마커 제거, 실패 시 반복 재연결·reboot 없이 링크와 관측 상태를 보존한다. 실제 로드된 SDIO p149.115 cold boot와 동일 SSID BSSID roam에서 HE/VHT `0xFFF0` 유지 실증. 비영 HE 오설정과 HT/VHT 불일치는 기존 fail-closed 정책을 유지하며, AC 전용 mlan1은 deferred HE 서비스 대상에서 제외한다.

## 0.5.0 (2026-08-07)

> SemVer **minor** — 로밍 엔진 개편 + 설정키 정리(제거 키는 postinst가 자동 마이그레이션) + 로거 systemd 감독화 + `wifi br route`·MAC 반영 정비. 와이어 포맷 변경 없음. 제거된 설정 키(LOAD_BASED_ROAM·ADAPTIVE_INTERVAL·POST_ROAM_ARP_OPTIMIZATION 등)는 업그레이드 시 자동 삭제되며 수동 조치 불필요.

### 로밍 엔진 개편 (#102~#137, #149~#152)

- **판정 스캔을 iw scan 기반 fresh 결과로 전환** — mlanutl setuserscan이 wpa_supplicant BSS 테이블을 채우지 않아 `wpa_cli roam`이 FAIL하던 근본원인 수정(#102~#106). 스캔 단위 BSS freshness 게이트(`last seen` 대조)로 stale BSS 오판 차단, Stage 2 bgscan 캐시 판정 제거 — 매 tick 직접 fresh 스캔이 1차 설계(#152). 스캔 로그 src(scan/cache) 라벨(#137), LAST_SCAN_TIME 기록을 bgscan 동등 커버리지로 게이트(#130).
- **cross-SSID 모드 A(select_network)** — extra_ssids 다중 SSID를 conf 교체 없이 select_network로 전환(2AP same-SSID + cross-SSID 양방향 온타겟 실증), cross ping-pong cooldown(#149), same-SSID ping-pong은 후보 선정 단계에서 BSSID 단위 제외.
- **backoff·게이트** — 후보없음 플래토 backoff 곡선(3,3,3,6,…,30 상한)(#150), good-signal 리셋 게이트(Δ2dB, 임계 진동 오리셋 차단) 신설(#138) 후 기본 on + mlan0 CHECK_INTERVAL=1(#151).
- **SIGHUP 런타임 reload** — 폴링 제거, self-pipe interruptible sleep 기반 즉시 반영(`wifi roam th` 연동, 프로덕션 유휴 비용 0)(#112).

### 설정 체계 정리 (#146~#148)

- 실험 노브 3종 제거(197→175키, −627줄)(#147). retired key는 postinst `cleanup_retired_roaming_keys`가 업그레이드 장비에서 자동 삭제. 설정 기본값 생성기 `gen_config_defaults.py --check/--write` + 역방향 커버리지 게이트로 코드/템플릿/스키마/handoff 4축 drift 해소(#148), 스키마 일관성 테스트 축(#146).

### 로거 systemd 감독화 (#118, #156)

- link(#118)에 이어 scan/stat/snapshot 로거를 Restart=always 감독 템플릿 유닛으로 분리 — `wifi_logger@` Wants pull-in, eth0 인스턴스 Condition 스킵, flock-loss exit 3(RestartPreventExitStatus 연동), start.sh `&` 기동/stop.sh pkill 제거(#156). passive_roam에 150s stale 가드 — 죽은 scan 로거의 옛 데이터로 수동 roam 하는 것을 차단.
- 온타겟 실증 완료(cts-wlan): 유닛 기동·kill 자동재시작·중복 exit3·stop 전파·stale 가드 양방향.

### 이슈 트리아지 안정성 픽스 (#153~#155)

- **select_network 성공 판정 강화**(#154) — wpa_cli "FAIL"+exit 0 오수락 차단(응답 "OK" 게이트) + 폴링을 wpa_state/ssid/id 3중 대조로 강화(구 AP COMPLETED 잔존 오판 → 핑퐁 카운터 오염·허위 opcd 통지 차단). FAIL 경로 방어적 블록 복원.
- **roam 실패 backoff**(#155) — 실패 tick도 후보없음 곡선을 전진(성공 확정 시에만 리셋), 후보 발견 시 good-signal baseline 재앵커로 임계 진동에 의한 에스컬레이션 무효화 차단.
- **mlan1 supplicant bgscan 제거**(#153) — 자율 로밍이 외부 roam 데몬·Roaming notify를 우회하던 지뢰 제거(템플릿 + 기존 기기 postinst 마이그레이션), `wifi status` 표시 fallback 템플릿 정렬.

### 브릿지 라우팅·MAC (`wifi br route`·update_mac)

- **`wifi {0|1} br route {find|set|auto}` 신설** — peer_route=on 토폴로지에서 **이더넷 미연결로 부팅**하면 `wired_mac_ip_get.py`가 `wait_for_eth_link()`에서 early-return 해 **peer host route(`<peer>/32 dev eth0`)만 누락**된다(나머지 인프라는 링크 무관하게 세팅됨). 이후 eth 연결 시 이 라우트를 사후 등록하는 수동 커맨드. 독립 bash 2개(`wifi_eth_peer_find.sh` 탐색기, `wifi_eth_peer_route.sh` 등록기)로 구현하고 부팅크리티컬 `wired_mac_ip_get.py`는 무수정. `find [<subnet>]`=peer sweep 탐색(읽기 전용, self·GW 제외), `set <ip>`=`ip route replace <ip>/32 dev eth0` 등록, `auto [<subnet>]`=정확히 1건 발견 시 등록(0/2+는 에러). 서브넷 생략 시 `eth_client_ip`→`eth_sweep_subnet`→mlanN CIDR 순 결정. `peer_route.enabled=false`면 경고 후 진행. 스텁 기반 단위/통합 테스트 추가. (자동 링크업 트리거는 후속 Phase 2)
- **`br route find`/`auto` 실기 무출력 버그 픽스** — 탐색기가 arping sweep 후 `ip neigh show`로 응답자를 읽었으나, `arping`(iputils)은 raw PF_PACKET 소켓이라 **응답을 받아도 커널 neigh 테이블을 채우지 않는다**(온타겟 실측 2026-07-20: `arping` exit=0인데 `ip neigh show`엔 없음, `ping`은 채움). 그 결과 유선 peer가 실제로 응답해도 항상 무출력이었다. `ip neigh show` 읽기·`ip neigh flush`를 폐기하고 **arping exit code로 응답자를 직접 수집**하도록 교체(온타겟 `find`가 유선 peer 192.168.0.21/122 발견 검증). 부수 효과: 스윕 범위 내 IP만 probe하므로 대역 필터가 불필요해지고, live 응답만 잡으므로 STALE 잔존 오등록도 원천 차단(직전 `neigh flush` 가드 제거). 스텁 테스트를 arping-응답자 모델로 재작성 — 기존 테스트는 arping과 neigh 스텁을 분리해 이 버그를 가렸다.
- **MAC 반영 로직 재설계 + `update_mac.sh` 견고성** (#110) — mlan0/mlan1 모두 `enabled` 여부와 무관하게 `mac.<iface>.base`를 항상 반영(baseline)하고, 실제 bridge 활성(`wbridge.enabled` & bridge iface enable) + dynamic/static 변환 성공 시에만 bridge 인터페이스 MAC을 base 대신 override(기존: iface `enabled=false` 시 MAC 설정 전체 skip → mlan1 비활성에서 base 미반영). `update_mac.sh`는 `.link`가 없을 때 `[Match]/[Link]/MACAddress`를 갖춘 파일을 자동 생성하고(기존: 빈 0바이트 파일 생성 후 성공 로그만 → MAC 미적용), 백업을 인터페이스당 최대 5개 회전(`.bak.1`~`.bak.5`) + 동일 MAC 중복 방지로 전환.
- **`update_mac.sh` 리뷰 후속 + 테스트** (#111) — 빈(0바이트)·섹션 없는 `.link`도 재생성(구버전 버그로 남은 0바이트 파일이 그대로 방치돼 MAC 미적용되던 회귀 차단), `mktemp` 실패 시 에러 로그 후 종료, 잘못된 MAC 입력 시 현재 `.link`가 이미 유효하면 오래된 백업 복구 skip. `SYSTEMD_NETWORK_DIR` 오버라이드로 테스트 가능화(기본 동작 불변), `update_mac_test.sh` 신설(21 케이스).
- **`br route find`/`auto` — eth0 타서브넷 peer 발견 수정** (#113) — 탐색기 `wifi_eth_peer_find.sh`가 `arping`을 `-s`(source) 없이 호출해 eth0 IP를 sender로 써, eth0가 sweep 대역과 다른 서브넷이면(예: eth0=192.168.1.1/24, peer=192.168.0.220) same-subnet source에만 응답하는 peer를 못 찾던 문제(서브넷 인자는 sweep 범위만 바꾸고 source는 안 바꿈). eth0가 대역 밖이면 대역 내 우리 IP(mlanN)를 `-s`로 지정하도록 보정. 온타겟(192.168.0.20/peer .220) 발견 검증, `wifi_eth_peer_find_test.sh` 신설(8 케이스).

## 0.4.4 (2026-07-17)

> SemVer **patch** — peer_route 진단·안정화 + 부팅 race 수리 + tpacket 배리어 + 로그 명령. 와이어 포맷·설정키 호환 변경 없음.

### wlan-package (메인)

- **peer_route 진단·안정화** (#100) — `wifi <0|1> br status` 진단 명령 신설(3종 토글 `peer_route.enabled`/`ip_discovery`/`arp_ignore_always` + 런타임 실측 + 정합성 판정, 읽기 전용). `eth_sweep_subnet` 미설정 시 sweep 폴백을 mlan0 대역 우선으로 정정(기존 eth0 관리대역 오판). `br status`의 `wbridge.enabled=false`가 jq `//`에 흡수돼 `true`로 오표시되던 버그 픽스.
- **부팅 race OHT 미발견 수리** (#101) — `mac_mode=dynamic` 부팅 경로에서 `wired_mac_ip_get.py`가 mlan0 IP 부여 **전**에 sweep를 실행하면 런타임 폴백이 `None`→eth0 관리대역(`192.168.1.0/24`)을 sweep해 OHT(유선 peer)를 못 찾았다. sweep 대역/source를 `mlanN.network`의 `Address` **설정값** 기반(`get_iface_config_addr`, glob·case-insensitive)으로 바꿔 부팅 타이밍과 무관하게 만들고, arp `psrc`를 mlan0 IP로 고정(eth0 primary/secondary 순서 의존 제거). 파싱 실패는 로깅. 온타겟 실측으로 OHT 발견 확인, 단위테스트 11개 추가.
- **moal deliver_rt_prio 노브** — RX deliver leg(NAPI `woal_netdev_poll_rx`)를 전용 kthread `SCHED_FIFO`로 올려 sparse/idle 시 다운스트림 RX jitter를 저감(Direction B). `wq_sched_policy`/`wq_sched_prio`로 전달(capability-gate).
- **로그 명령 정비** (#98) — 로그 수집 tar.gz 전환, `wifi log reset`(iface/전역 truncate + rsyslog HUP) 신설.
- **stat.log 손상 수리** (#99) — 로거 단일 인스턴스 락 + stop 종료로 라인 겹침 제거.

### wlan-bridge (서브모듈)

- **tpacket TX/RX 링버퍼 메모리 배리어** (#29) — ARM(weak-memory) + `-O3 -flto`에서 TPACKET v2 공유 링버퍼 `status`에 atomic acquire/release 적용(4곳). user↔kernel 레이스로 미완성 프레임/블록을 읽거나 덮어쓰는 것을 차단. `tx_frame_is_available`은 `const` 계약을 `(__u32 *)` 캐스트로 유지.

## 0.4.3 (2026-07-13)

> SemVer **patch** — factory_reset 버그픽스. 신규 설정 키 1개(`.mcp.max_probe_fail`), 와이어 포맷 변경 없음.

### wlan-package (메인)

- **factory_reset이 보드 감지 결과를 날리던 버그 수정** — `.mcp.iio_device`는 postinst가 SoC를 감지해 활성 설정에 주입하는 값인데(iMX93=`iio:device1`, iMX8MM=`iio:device0`), `factory_reset.sh`가 템플릿을 `json_merge` 없이 통째로 덮어써 이 키가 사라졌다. 템플릿에는 해당 키가 아예 없다. 그 결과 iMX93에서 `wifi_logger_mcp.sh`가 `iio:device0`으로 fallback해 ADC를 못 읽고, 빈 값이 `printf '%.3f'`를 거쳐 `0.000`으로 찍히면서 전원 규격(5V/24V) 판별 루프를 영영 빠져나오지 못한 채 `Invalid Voltage!!`를 **emerg로 5초마다 무한 로깅**했다(emerg는 journald가 모든 콘솔에 wall broadcast). 온타겟 실측 결과 설치본과 리셋본의 유일한 내용 차이가 `.mcp.iio_device`였음을 확인. 부수적으로 템플릿의 `.global.BOARD_TYPE`이 `imx93` 고정값이라 **iMX8MM에서 factory_reset 시 BOARD_TYPE이 뒤바뀌어 드라이버 선택이 어긋나는** 잠복 버그도 함께 해소.
  - SoC 감지 + JSON 주입을 신규 `wifi_board_config.sh`로 분리해 postinst와 `factory_reset.sh`가 공유. 이로써 **factory_reset 결과 == 신규 설치 결과**(템플릿 + 보드 감지)가 된다.
  - **iio 디바이스를 인덱스가 아니라 capability로 탐색** — `in_voltage0/1_raw`와 `in_voltage0/1_scale` 4개를 모두 가진 디바이스를 찾는다. iMX93 실기기 실측 결과 SoC 내장 `imx93-adc`가 `device0`, 외장 ADC(I2C `0-0068`)가 `device1`이며, 내장 ADC는 채널별 scale 없이 공용 `in_voltage_scale`만 있어 이 검사에서 걸러진다. 인덱스는 프로브 순서·IMU 유무에 밀리므로(i.MX93 EVK에서는 `device1`이 `lsm6dso_gyro`) 하드코딩은 취약하다. 후보가 유일하지 않으면 기존 보드별 인덱스로 폴백.
  - 원 버그의 기전도 실기기에서 확정: fallback한 `device0`(내장 ADC)은 `in_voltage0_scale`이 없어 `cat`이 실패 → `bc`가 빈 값을 받아 계산 실패 → `printf '%.3f' ""`가 `0.000` 출력 → 전원 규격 판별 실패 → 무한 emerg.
  - `.mac.*`(mlan0/mlan1/eth0)은 factory_reset이 템플릿의 빈 값으로 되돌린다. 의도된 동작이며, 필요 시 `wifi mac <iface> <base|target> <MAC>`으로 재설정한다(자동 복구 경로 없음).
- **wifi_logger_mcp.sh 방어** — `read_adc()`로 sysfs/bc 실패를 감지해 빈 값이 `0.000`으로 흘러 "0V 이상전압"으로 오인되던 경로를 차단. `Invalid Voltage`/읽기 실패 로그를 emerg→err로 강등(콘솔 wall broadcast 방지)하고 backoff 적용. 전원 규격 판별 루프는 `max_probe_fail`(기본 12회, ≈5.5분) 후 종료하고, **과전류 감시 루프는 종료하지 않는다** — `wifi_logger.service`가 `Type=oneshot`이라 exit하면 재부팅 전까지 재시작되지 않아 emerg 감시를 영구히 잃기 때문. ADC 복구 시 감시가 자동 재개된다.

## 0.4.2 (2026-07-10)

> SemVer **patch** — `wifi info` 관측성 개선 (런타임 동작 변경 없음).

### wlan-package (메인)

- **wifi info에 [MFG] 섹션·부가 데몬 표시** — `wifi info` 출력에 MFG 상태 섹션(mfg_mode SoT 판독, 활성 버스 블록의 fw_name, `/run/wifi/mfg_loaded` 멱등 flag, 프로파일 요약)과 [Services]의 snmpd/opcd 상태 표시를 추가. mfg 판정은 정책 스크립트 5종과 동일한 라인앵커 grep(`^[[:space:]]*mfg_mode=`)·동일 SoT(mod_para.conf)를 사용해 표시와 정책 간 판정 불일치를 방지.

## 0.4.1 (2026-07-10)

> SemVer **patch** — MFG(제조) 모드 정책 전환 버그픽스. 신규 설정 키/와이어 포맷 없음.

### wlan-package (메인)

- **MFG 모드 정책 전환 (드라이버 반복 리로드 루프 수정)** — mfg_mode=1에서 wifi_init.sh가 exit 1로 종료 → Restart=on-failure가 10초마다 재실행 → 매회 moal/mlan rmmod/insmod + mlan 점유 프로세스 kill(fuser)로 mfgbridge 제조 테스트가 간헐 실패하고, StartLimit 소진 시 wlan_emergency_reboot 요청까지 이어지던 체인을 차단. MFG 프로파일 동작: wifi_init.sh는 mfg 로드 완료 시 성공 종료(exit 0)하고 재실행 시 이미 mfg로 로드된 드라이버를 건드리지 않으며(멱등 가드 flag `/run/wifi/mfg_loaded` — mfg insmod 성공 시에만 생성, 일반 FW 로드 상태에서 mfg 전환 시에는 재로드 정상 수행), wifi_apply_enabled.sh가 STA/FW 접촉 유닛(per-iface 9종 wpa_supplicant@·wifi_logger@·wifi_checker@·wifi_event@·wifi_bridge@·wifi_bgscan@·wifi_roam@·wifi_periodic_roam@·wifi_arping@ + wifi_ping_monitor + thermal/mgmt_log 타이머 + snmpd/opcd)을 disable+stop, wifi_services.sh는 start를 skip(이중 안전장치), wifi_checker.sh는 idle 대기(Restart=always 재스폰 방지 위해 내부 sleep), wlan_reboot_policy.sh는 mfg 중 재부팅 요청을 거부(신규 exit 12; overtemp/`--force`/wifi_init emergency는 통과 — 과열 보호와 실제 드라이버 로드 실패 복구 유지). mfg_mode=0 복귀 후 wifi_init 재실행 시 JSON 기준으로 자동 재-enable(자기치유). mfg 판정 SoT는 기존과 동일하게 `mod_para.conf`의 `mfg_mode=`.
- **MFG 로드 경로 최소화** — mfg_mode=1일 때 wifi_init.sh의 로드 절차에서 운영용 설정을 배제: moal bridge insmod 인자(bridge_mode=1 등) 미전달(moal bridge의 eth0 attach/promisc가 labtool 이더넷 링크를 건드리는 것 방지), MAC 설정 전체 skip(동적/정적 spoofing·wired_mac_ip_get.py IP discovery·eth0 base MAC — eth0 MAC 변경은 진행 중 labtool 연결을 끊을 수 있음), mod_para 블록의 운영키 제거(net_rx/mgmt_hex_dump/dev_cap_mask ← STANDARD — 드라이버/FW 기본값으로 로드), `cal_data_cfg=none` 강제(제조에서는 labtool이 cal data를 직접 로드/OTP 기록하므로 드라이버 선주입은 OTP 검증을 가리고 캘 기록과 충돌). 안테나 mux(ANT_TYPE)는 제조 RF 측정 경로에 영향이 있어 유지. txpwrlimit/thermal_mgmt/peer_route/radio defaults 등 post-insmod 설정은 기존대로 skip.

## 0.4.0 (2026-06-22)

> 0.3.1 이후 **메인 105 + wlan-opc 53 + wlan-bridge 6 = 164 커밋**.
> SemVer **minor** — 운영자 체감 신기능 대거 추가 + wlan-opc OPC 와이어 포맷·입력검증·에러코드 사양 정합(비호환).

### wlan-package (메인)

- **무선 대역폭(BW)·모드 변경 명령 재설계** — wifi mode/bw/radio-apply/ip apply 명령을 신설하고 HE 80↔40↔20 전환을 OMI(tx_omi) 대신 htcapinfo/vhtcfg cap 적용 후 reassociate(무중단)로 재설계. mode 변경 시에만 disconnect+bandcfg+reconnect를 수행하고 bw default(AP 최대로 cap 개방)·라이브 불일치 시 재적용 복구 경로를 추가.
- **wpa 무선설정 런타임 적용 파이프라인 전환** — opcd의 freq/essid 적용을 set_network+save_config 대신 wpa conf 직접편집(awk)+wpa_cli reconfigure 방식(opc_wlan_apply.sh 신설)으로 전환해 v2.10에서 freq_list가 영속되지 않던 문제 해소. conf 권한 보존·SSID 이스케이프·reconfigure 실패 롤백·busybox awk ENVIRON 미지원 시 SSID 무음손상 차단 추가.
- **wifi connect 명령 + association 폴링 개선** — ssid/scan_freq/freq_list를 한 번에 기록하고 reconfigure→reassociate까지 일원화하는 wifi connect 명령 추가. connect·radio-apply의 assoc 대기 폴링을 1s→0.1s 그리드로 통일, 타임아웃 상수화·8진수 산술오류 방지.
- **extra_ssids 다중 SSID 스캔·로밍** — roaming.extra_ssids 배열(기본 [])로 추가 로밍 후보를 지정해 같은 SSID는 wpa_cli roam(무중단)·다른 SSID는 wifi connect(재연결)로 전환. bgscan이 ssid_filter와 무관하게 extra_ssids를 항상 directed probe(hidden 포함)하도록 하여 스캔↔로밍 후보를 일치.
- **reconfigure 재연결 과도기 안정화** — wpa_cli reconfigure가 유발하는 disconnect→reconnect 과도기를 wifi_checker가 오판하지 않도록 grace flag(/run/wifi/<iface>.reconfigure-grace)를 도입. wifi_roam이 conf mtime 변화 시 재파싱해 stale SSID 로밍을 방지하고, disconnect 복구를 handshake hold→reassociate→restart로 단계화(reboot 미진입).
- **bgscan 동작 개선** — 미연결(wpa_state!=COMPLETED) 시 iw scan을 skip해 재연결 스캔과의 라디오 경합을 차단. mlanN.bgscan.ssid_filter/freq_filter 옵션과 스캔 직전 wpa conf+JSON 재로드를 추가해 런타임 무선설정 변경을 다음 스캔부터 반영.
- **moal 드라이버 파라미터·thermal 설정 추가** — mlanN.thermal_mgmt(per-iface)·wbridge.moal.keepalive_idle_ms·global.tx_work 설정을 추가해 moal insmod 인자로 배선하되, 미선언 .ko에 인자가 전달돼 부팅이 깨지지 않도록 capability-gate를 strings|grep parmtype= / tr 기반으로 통일. 발열 임계(CPU/WiFi) 상향, mlan1 기본 비활성화.
- **모니터·로깅 관찰성 개선** — wifi mon c(compact)에 현재 채널 점유율(ChUtil busy%/noise) 표시 추가, NXP moal survey가 누적이 아닌 스캔 구간값임을 반영해 순간 점유율로 정정. link.json의 ssid stale 캐시(같은 BSSID에서 SSID 변경 시 영구 소실)를 매주기 iw info 호출로 제거하고 extra_ssids 로드 여부를 로그로 노출.
- **부팅 안전 가드·CI·드라이버 이력 관리** — arp_ignore_always+peer_route 위험 조합을 부팅 시 경고로 가시화, .ko 드라이버 버전을 DRIVER_MANIFEST.md+pre-commit hook으로 약식 추적. claude/gemini 워크플로의 pull_request_review 트리거 제거(봇 리뷰 루프 차단), 서브모듈 포인터 최신화.

### wlan-opc

- **OPC 프로토콜 와이어 포맷 사양 정합 정정** *(비호환)* — List Boundary Flag(START/CONTINUE swap·START_END 제거), Set Radio Config WLAN#2 필드순서(CH→FREQ를 WLAN#1과 대칭인 FREQ→CH로), 공통 헤더 크기·Length 정의(원본 docx 도면 교차확인으로 64B/전체−8 최종 확정)를 바로잡아 VHL 측과 정합화.
- **입력값 검증·에러코드 사양 적합성 구현** *(비호환)* — 누락돼 있던 입력 검증(IP/넷마스크/GW/NTP/ESSID NULL종단·list-size·loopback/네트워크/브로드캐스트 거부, 주파수 범위·밴드 검증으로 6G/미지 밴드 거부)과 에러코드 정정(0x0018 LIST_SEQUENCE 신설, A14 타-IP 통지 0x0013, D9 0x0011, D10 0x0012) 추가. SetIPConfigList를 전체 교체에서 지정 슬롯 merge로 바꿔 미지정 슬롯 소실 차단, 1424B 초과/9~63B 부정 길이 프레임 처리 정정.
- **무선/IP 설정 런타임 실적용** — OK만 응답하고 반영되지 않던 ChangeIpAddress의 eth0 IP를 ip addr로 실제 교체. SetRadioConfig freq·ChangeIp essid를 conf rewrite에서 wpa_cli 런타임 적용(opc_wlan_apply.sh 위임)으로 전환하고, ChangeIp가 ACK 직후 조기 전환되던 A12 위반을 명시적 Logout 시점 write-once 스냅샷으로 확정. deferred apply를 UDP drain 내부로 옮겨 same-drain 윈도우 제거.
- **nl80211 기반 indication 이벤트 발행** — no-op이던 WlanStatusChange/Roaming/ApDisconnect 등을 커널 nl80211 mlme 멀티캐스트를 raw netlink로 구독해 실제 발행. CONNECT 시 WIPHY_FREQ 누락으로 채널이 0으로 나가던 문제를 link.json/GET_INTERFACE 폴백·커널 직접조회로 보강, AP 절단을 deauth(39)/disassoc(40)로 구분.
- **device-info 인벤토리 분리·tmpfs publish·출처 토글** — device-info Ack의 정적 ID를 device_info.json 인벤토리로 분리하고 firmware(dpkg-query)·NTP(timesyncd.conf) 동적 조회 추가. GetDeviceInfo 응답을 /dev/shm/opcd/device_info.json으로 원자적 publish해 외부 도구가 프로토콜 없이 관찰 가능, FREQ/CH 출처를 config|live|auto로 고르는 device_info_freq_source 토글(기본 config) 추가.
- **보안 하드닝 — 빈 비밀번호 차단·세션 수명 결합** — 빈 저장 비밀번호 로그인 통과 실구멍을 fail-closed 차단하고 빈 새 비밀번호 설정 거부. ErrorCause를 의미별 명명상수로 정리(와이어 값 불변)하고 SECURITY.md 추가, indication 리플렉터를 인증 세션 수명에 묶어 세션 종료 시 통지 중지(teardown 단일화)·수신처 검증 추가.
- **NVRAM 비동기 쓰기·재송 응답 정합** — 동기 fsync가 최대 120s 전 제어평면을 정지시키던 문제를 pthread 워커 1개+FIFO 큐+eventfd 기반 비동기 쓰기로 전환하고 NVRAM 결과를 deferred ack로 송신. A19에 따라 처리 중 동일 커맨드 재송(새 SN) 도착 시 기존 대기 응답을 폐기하고 새 SN으로만 응답.
- **FaultDetect 장애검출 통지 구현** — 사양 §3.4 장치 장애 검출 통지의 폭주 판별을 구현해 CPU(/proc/stat)·Disk(io_ticks)·Network(rx+tx vs 링크속도) 사용률을 indication 보고 주기마다 샘플링, 임계(기본 80%, congestion_* 키 가변) 초과 리소스 통지·지속 시 재통지(운영값은 발주처 확인 전 임시 정책).
- **내부 구조·빌드·CI 정비** — 명령 디스패치 정적 테이블화(ARCH-004)·station_type 권위 중앙화, T7 와이어 응답 소요시간 실측 로그, Makefile 헤더 의존성 추적(-MMD -MP)·native/arm64 빌드 분리·platform_nxp 리팩토링, automation v1.31 핀(PR 자동리뷰 RCE 차단)·봇 리뷰 루프 차단.

### wlan-bridge

- **EAPOL·802.1D link-local 프레임 무조건 차단 (moal 가드 패리티)** — pcap 엔진(wifi-wbridge)이 브릿징하던 802.1X EAPOL(0x888E, unicast 4-way handshake 포함)과 IEEE 802.1D link-local 목적지(01:80:C2:00:00:00~0F, STP/LACP/LLDP/PAE group)를 enable_* 플래그와 무관하게 filter_should_drop에서 무조건 드롭하도록 추가해 토폴로지 루프·STP 혼란을 차단하고 moal_bridge 가드와 의미론 일치.
- **봇 리뷰 자동화 무한 루프 차단** — claude.yml·gemini-dispatch.yml·gemini-chat.yml에서 pull_request_review:submitted 트리거를 제거해 봇 리뷰가 다시 봇을 트리거하던 무한 반복 차단.
- **automation 워크플로우 v1.31 핀 + gemini-auto-review 활성화** — 모든 uses 참조·automation_ref를 v1.31로 올리고(PR 자동리뷰 셸 인젝션 RCE 차단) gemini-auto-review를 enabled/auto로 켜 Gemini 자동 리뷰 파이프라인 가동.

### ⚠️ 호환성 주의 (Breaking changes)

- **[wlan-opc] OPC 와이어 포맷 비호환**: List Boundary Flag 값 swap(START/CONTINUE)·START_END 제거, Set Radio Config WLAN#2 필드순서를 FREQ→CH로 변경, 공통 헤더 64B/Length(전체−8) 재확정. 구버전 클라이언트/VHL과 프레임 해석이 어긋나므로 양측 동시 갱신 필요.
- **[wlan-opc] 에러코드 의미 변경**: 0x0018 LIST_SEQUENCE 신설, A14 타-IP 통지 0x0013, D9 0x0011, D10 0x0012로 정정. 에러코드를 하드코딩한 상위 시스템은 매핑 갱신 필요.
- **[wlan-opc] SetIPConfigList 동작 변경**: 전체 교체 → 지정 슬롯 merge. 기존에 전체 교체를 전제로 빈 리스트로 초기화하던 운용 절차는 더 이상 미지정 슬롯을 비우지 않음.
- **[wlan-opc] 입력 검증 강화**: 기존에 통과되던 일부 요청(loopback/네트워크/브로드캐스트 주소, 6G/미지 밴드, 부정 길이 프레임)이 NG로 거부됨.
- **[wlan-opc] 빈 비밀번호 로그인 차단**: fail-closed로 거부됨. 빈 비밀번호로 운용하던 장치는 사전에 비밀번호 설정 필요.
- **[wlan-package] mlan1 기본 비활성화**: mlan1을 사용하던 구성은 명시적 enable 필요.

---

이전 버전(0.3.1 이하) 요약은 `dist/wlan/DEBIAN/control`을 참조하세요.
