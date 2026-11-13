# 드라이버 빌드 manifest

> `scripts/gen_driver_manifest.sh --write` 자동 생성 — 수동 편집 금지.
> board-qualified imx93 4-component exact identity는 `DRIVER_COMPONENTS.sha256`가 강제한다.

- 소스 저장소: `wlan-driver-v2` (required layout tracked-object verified)
- 소스 원격: `https://github.com/jhw7500/wlan-driver-v2.git`
- 소스 설명: `mwifiex-61820-0396-imx93-validated-20260822-128-g21427ad`
- 소스 commit: `21427ad0a122858d808a7f5d078d68a74698f09b`
- 소스 범위: declared commit tracks required layout and is contained by local origin/*; supplied outputs are external
- 소스 검증: supplied metadata matched; no remote/build attestation; exact board-qualified payload bytes locked below
- 대상 디렉토리: `dist/wlan/opt/wlan/driver`

## Kernel modules

| 파일 | SHA-256 | version | srcversion | vermagic |
|------|---------|---------|------------|----------|
| debug/mlan.ko | `ea7948c8afddb808da3d8895b1f8236517e4de5be86bcf9107119234ee344460` | 552.p22 | 85407268EE457EA142491CC | 6.6.3-lts-next-g1c0b4db17dce SMP preempt mod_unload modversions aarch64 |
| debug/moal.ko | `4fba25b9df0ac7d27a1342dcd295d8f468005abd018df6b910f5da2bf107cbd5` | 552.p22 | 346707AE542F054C95411FA | 6.6.3-lts-next-g1c0b4db17dce SMP preempt mod_unload modversions aarch64 |
| mlan_imx8.ko | `ce5f2d57e8361fc1fb790a08a0536d4d4cbafd348f9eb8d9180b7e8d3afba644` | 505.p14 | C313A4C1BA94176BBBF91E7 | 6.6.3-lts-next-g9bc88c3c4469-dirty SMP preempt mod_unload modversions aarch64 |
| mlan_imx93.ko | `ab5020157d3674f7c4a2f3cb4265fc71fe19a534a089750c1ed4a428c695b3a6` | 552.p22 | 85407268EE457EA142491CC | 6.6.3-lts-next-g1c0b4db17dce SMP preempt mod_unload modversions aarch64 |
| moal_imx8.ko | `c967d18482ae674de5aa1fae40c11cfb267bfb410b720111e3ed2f1684d665ea` | 505.p14 | 4BA28708A7C5CBCB7C79BF3 | 6.6.3-lts-next-g9bc88c3c4469-dirty SMP preempt mod_unload modversions aarch64 |
| moal_imx93.ko | `bdd519733819b031b8d8a1938d77b3dbf530e91cb1d88bce78394edd88201f72` | 552.p22 | 346707AE542F054C95411FA | 6.6.3-lts-next-g1c0b4db17dce SMP preempt mod_unload modversions aarch64 |

## Board-qualified component lock

| 패키지 경로 | SHA-256 |
|-------------|---------|
| opt/wlan/driver/mlan_imx93.ko | `ab5020157d3674f7c4a2f3cb4265fc71fe19a534a089750c1ed4a428c695b3a6` |
| opt/wlan/driver/moal_imx93.ko | `bdd519733819b031b8d8a1938d77b3dbf530e91cb1d88bce78394edd88201f72` |
| opt/wlan/bin/mlanutl_imx93 | `e272535a5d56b4bd2160a5d6bccdd4eab0664cab44f42495bf7aff43553e962e` |
| usr/lib/firmware/cts/sd9098_wlan_v1.bin | `a05ba158efb0262791a471b90c0e0e616316097ccf6e17f6a170778ee4758813` |
