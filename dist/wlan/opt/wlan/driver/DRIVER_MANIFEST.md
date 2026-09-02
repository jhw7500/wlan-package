# 드라이버 빌드 manifest

> `scripts/gen_driver_manifest.sh --write` 자동 생성 — 수동 편집 금지.
> board-qualified imx93 4-component exact identity는 `DRIVER_COMPONENTS.sha256`가 강제한다.

- 소스 저장소: `wlan-driver-v2` (required layout tracked-object verified)
- 소스 원격: `https://github.com/jhw7500/wlan-driver-v2.git`
- 소스 설명: `mwifiex-61820-0396-imx93-validated-20260822-35-g567b836`
- 소스 commit: `567b83613752c179236c03fcca047d8bcc128f7b`
- 소스 범위: declared commit tracks required layout and is contained by local origin/*; supplied outputs are external
- 소스 검증: supplied metadata matched; no remote/build attestation; exact board-qualified payload bytes locked below
- 대상 디렉토리: `dist/wlan/opt/wlan/driver`

## Kernel modules

| 파일 | SHA-256 | version | srcversion | vermagic |
|------|---------|---------|------------|----------|
| mlan_imx93.ko | `28e6a2b81f583f33516c06614df45b3ce6d02e26640a8236221b1868ac682c8a` | 543.p18 | 69FB1CDC4109F4A73C98B59 | 6.6.3-lts-next-gccf0a99701a7-dirty SMP preempt mod_unload modversions aarch64 |
| moal_imx93.ko | `ad91350619e5bb5ca6e21a627a2ccc7b20385e4a4f3a0223de0a77cb6343bcd2` | 543.p18 | 7EC3F43DE3381F51BF26288 | 6.6.3-lts-next-gccf0a99701a7-dirty SMP preempt mod_unload modversions aarch64 |

## Board-qualified component lock

| 패키지 경로 | SHA-256 |
|-------------|---------|
| opt/wlan/driver/mlan_imx93.ko | `28e6a2b81f583f33516c06614df45b3ce6d02e26640a8236221b1868ac682c8a` |
| opt/wlan/driver/moal_imx93.ko | `ad91350619e5bb5ca6e21a627a2ccc7b20385e4a4f3a0223de0a77cb6343bcd2` |
| opt/wlan/bin/mlanutl_imx93 | `d1cd869b7d75118fad5ca936e9919dafbe99831fba215ad35a1c2d67de1a914b` |
| usr/lib/firmware/cts/sd9098_wlan_v1.bin | `7c3ef6e12d3cfc9bd638d1571ccf6ddd2e96e0ed179ec70664ccb1df0ba29e57` |
