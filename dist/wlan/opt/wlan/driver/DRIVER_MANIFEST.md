# 드라이버 빌드 manifest

> `scripts/gen_driver_manifest.sh --write` 자동 생성 — 수동 편집 금지.
> board-qualified imx93 4-component exact identity는 `DRIVER_COMPONENTS.sha256`가 강제한다.

- 소스 저장소: `wlan-driver-v2` (required layout tracked-object verified)
- 소스 원격: `https://github.com/jhw7500/wlan-driver-v2.git`
- 소스 설명: `mwifiex-61820-0396-imx93-validated-20260822-131-g7ed1efd`
- 소스 commit: `7ed1efd3eb8a9098a9ec99cbe11f9abe30ddf031`
- 소스 범위: declared commit tracks required layout and is contained by local origin/*; supplied outputs are external
- 소스 검증: supplied metadata matched; no remote/build attestation; exact board-qualified payload bytes locked below
- 대상 디렉토리: `dist/wlan/opt/wlan/driver`

## Kernel modules

| 파일 | SHA-256 | version | srcversion | vermagic |
|------|---------|---------|------------|----------|
| debug/mlan.ko | `9aa0436f26a60ecb984380e1b3322e40444cce0f67393500386bb72a3f2be87c` | 552.p22 | 3B7FDEE33C334D7B1F9B7C0 | 6.6.3-lts-next-g1c0b4db17dce SMP preempt mod_unload modversions aarch64 |
| debug/moal.ko | `80f0c80b0bbc2ca62bd36ca5b39dcc2315fe61b0a77916d86cfce2b081378ca0` | 552.p22 | 17CD6D439FEFE620FE635D9 | 6.6.3-lts-next-g1c0b4db17dce SMP preempt mod_unload modversions aarch64 |
| mlan_imx8.ko | `ce5f2d57e8361fc1fb790a08a0536d4d4cbafd348f9eb8d9180b7e8d3afba644` | 505.p14 | C313A4C1BA94176BBBF91E7 | 6.6.3-lts-next-g9bc88c3c4469-dirty SMP preempt mod_unload modversions aarch64 |
| mlan_imx93.ko | `7252d41f49f0ed7d2c15b27aa7f6d39cdf7adde413ae04ff309427a65fa92458` | 552.p22 | 3B7FDEE33C334D7B1F9B7C0 | 6.6.3-lts-next-g1c0b4db17dce SMP preempt mod_unload modversions aarch64 |
| moal_imx8.ko | `c967d18482ae674de5aa1fae40c11cfb267bfb410b720111e3ed2f1684d665ea` | 505.p14 | 4BA28708A7C5CBCB7C79BF3 | 6.6.3-lts-next-g9bc88c3c4469-dirty SMP preempt mod_unload modversions aarch64 |
| moal_imx93.ko | `635cf03032cd921bc189ff8658535866cf72dc82b3a55a7ca68067154a9c7f4a` | 552.p22 | 17CD6D439FEFE620FE635D9 | 6.6.3-lts-next-g1c0b4db17dce SMP preempt mod_unload modversions aarch64 |

## Board-qualified component lock

| 패키지 경로 | SHA-256 |
|-------------|---------|
| opt/wlan/driver/mlan_imx93.ko | `7252d41f49f0ed7d2c15b27aa7f6d39cdf7adde413ae04ff309427a65fa92458` |
| opt/wlan/driver/moal_imx93.ko | `635cf03032cd921bc189ff8658535866cf72dc82b3a55a7ca68067154a9c7f4a` |
| opt/wlan/bin/mlanutl_imx93 | `e272535a5d56b4bd2160a5d6bccdd4eab0664cab44f42495bf7aff43553e962e` |
| usr/lib/firmware/cts/sd9098_wlan_v1.bin | `a05ba158efb0262791a471b90c0e0e616316097ccf6e17f6a170778ee4758813` |
