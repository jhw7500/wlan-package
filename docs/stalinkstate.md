# `stalinkstate` 사용법과 출력 필드

STA(클라이언트)에서 **어떤 규격으로 AP에 연결됐는지** 보려면 `association.standard`, **현재 채널과 폭**을 보려면 `current.channel`과 `current.width`를 확인한다.

## 패키지 설치와 적용

이 패키지는 i.MX93의 `mlan`·`moal` 모듈과 `mlanutl_imx93`에 이 명령을 제공한다. 보드에 시험용 파일을 `/tmp/wlan-stalinkstate.deb`로 복사한 뒤 유선 관리 연결에서 실행한다. 드라이버를 다시 로드하는 동안 Wi-Fi가 잠시 끊긴다.

```sh
dpkg -i /tmp/wlan-stalinkstate.deb
systemctl restart wifi_init.service
systemctl is-active wifi_init.service
```

## 호스트에서 시험 패키지 다시 만들기

같은 빌드의 `wlan-package.tar`에는 i.MX93과 i.MX8 입력 파일이 들어 있다. 빈 디렉터리에 풀고 그 디렉터리에서 다음 명령을 실행한다. 호스트에는 패키지 빌드용 SDK와 ARM64 크로스 컴파일러가 필요하다.

```sh
bash scripts/gen_driver_manifest.sh --check
bash build.sh
```

결과는 `release/wlan.deb`이다. Git 체크아웃에서 시작하는 경우 드라이버 모듈과 `mlanutl_*`는 외부 빌드 입력이다. i.MX93의 소스 커밋과 필요한 파일의 SHA-256은 [DRIVER_COMPONENTS.sha256](../dist/wlan/opt/wlan/driver/DRIVER_COMPONENTS.sha256)에 있다.

## 보드에서 실행

i.MX93 보드의 `mlan0` 인터페이스에서는 다음 명령을 사용한다.

```sh
/opt/wlan/bin/mlanutl_imx93 mlan0 stalinkstate
```

읽기 전용 명령이며 추가 인자는 없다. 보드에는 이 명령을 지원하는 `mlanutl`과 해당 보드용 `mlan`·`moal` 모듈이 함께 설치돼 있어야 한다. `Invalid command specified!`가 나오면 보드에서 실행한 바이너리와 로드된 모듈의 버전을 확인한다. 이 기능은 `ported` 브랜치에 반영됐다. [PR #128](https://github.com/jhw7500/wlan-driver-v2/pull/128)

## 연결 상태의 출력 예시

88W9098 보드가 5 GHz AP에 802.11ac로 연결된 직후의 출력이다. BSSID는 예시 주소로 바꿨다.

```text
association.state=connected
association.standard=ac
association.band=5
association.bssid=02:00:00:00:00:36
association.connected_sec=0
current.channel=36
current.width=80
current.opermode_bw=0
current.opermode_nss=0
current.tx_format=vht
current.tx_bw=40
current.rx_format=legacy
current.rx_bw=20
```

이 연결은 채널 36에서 80 MHz 폭으로 동작했고 마지막 송신 프레임은 40 MHz를 사용했다. 같은 시점에 `iw dev mlan0 info`는 채널 폭을 80 MHz로, `mlanutl_imx93 mlan0 getdatarate`는 마지막 TX 폭을 40 MHz로 보고했다.

## 연결 필드

규격·대역·BSSID는 연결이 성립할 때 기록한다. 재연결이나 FT 로밍에 성공하면 새 연결 정보로 갱신되고 경과 시간도 다시 시작한다.

| 필드 | 예시 | 값의 의미 |
|---|---|---|
| `association.state` | `connected` | `connected`는 AP에 연결됨, `disconnected`는 미연결이다. |
| `association.standard` | `ac` | 드라이버가 요청한 능력과 AP의 연결 응답을 대조해 판정한 802.11 규격. `a`, `b`, `g`, `n`, `ac`, `ax` 중 하나이며 판정할 수 없으면 `unknown`이다. |
| `association.band` | `5` | 연결 주파수 대역. `2.4`와 `5`의 단위는 GHz이며, 대역을 판정할 수 없으면 `unknown`이다. |
| `association.bssid` | `02:00:00:00:00:36` | 연결된 AP의 무선 MAC 주소. 같은 SSID 안에서 로밍했는지는 이 주소의 변화로 확인할 수 있다. |
| `association.connected_sec` | `0` | 마지막 연결 완료 후 경과한 초. `0`은 1초 미만이다. |

## 현재 상태 필드

TX는 보드가 송신하는 방향, RX는 보드가 수신하는 방향이다.

| 필드 | 예시 | 값의 의미 |
|---|---|---|
| `current.channel` | `36` | 펌웨어가 보고한 현재 주 채널 번호. 5 GHz의 채널 36은 5180 MHz다. |
| `current.width` | `80` | 현재 운용 채널 폭(MHz). 표시값은 `20`, `40`, `80`, `160`, `80+80`이다. `80+80`은 떨어져 있는 두 80 MHz 구간을 뜻한다. |
| `current.opermode_bw` | `0` | 802.11ac 동작 모드 통지에 쓸 폭 설정 코드. `0`은 기본값, `1`은 20 MHz, `2`는 40 MHz, `3`은 80 MHz, `4`는 160 또는 80+80 MHz다. |
| `current.opermode_nss` | `0` | 동작 모드 통지에서 보드가 수신 가능하다고 알릴 공간 스트림 수(NSS). `0`은 기본 설정, `1`~`8`은 지정한 스트림 수다. |
| `current.tx_format` | `vht` | 마지막 송신 프레임 형식. `legacy`는 a/b/g 계열, `ht`는 11n, `vht`는 11ac, `he`는 11ax다. |
| `current.tx_bw` | `40` | 마지막 송신 프레임의 폭(MHz). 표시값은 `20`, `40`, `80`, `160`이다. |
| `current.rx_format` | `legacy` | 마지막 수신 프레임 형식. 표시값은 송신과 같은 `legacy`, `ht`, `vht`, `he`다. |
| `current.rx_bw` | `20` | 마지막 수신 프레임의 폭(MHz). 표시값은 `20`, `40`, `80`, `160`이다. |

## 미연결과 예외 값

- 미연결이면 연결 필드에는 `association.state=disconnected` 한 줄만 나오고, 여덟 개의 `current.*` 필드는 모두 `unavailable`이다.
- 연결 중에도 특정 조회가 실패하거나 드라이버가 값을 표시할 수 없으면 해당 현재 상태 필드에 `unavailable`이 나온다. 이 버전에서는 5 MHz 및 10 MHz 채널 폭도 여기에 해당한다.
- 연결 직후에는 첫 데이터 프레임을 받기 전에도 RX 초기값이 `legacy` / `20`으로 표시될 수 있다. 이 값만으로는 데이터 수신 여부를 판단할 수 없다.
- 조회 도중 연결이 바뀌면 CLI가 한 번 재시도한다. 재시도까지 연결 전환과 겹쳐 `EAGAIN`이 반환되면 연결이 안정된 뒤 다시 실행한다.

원문 사양은 드라이버 사용 설명서의 `stalinkstate` 및 `opermodecfg` 항목에 있다. [README_MLAN](https://github.com/jhw7500/wlan-driver-v2/blob/e76f50b9133eaab7ab17053c350caa8bb8176675/docs/README_MLAN)
