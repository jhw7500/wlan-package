# 드라이버 빌드 manifest

> `scripts/gen_driver_manifest.sh --write` 자동 생성 — 수동 편집 금지.
> board-qualified imx93 4-component exact identity는 `DRIVER_COMPONENTS.sha256`가 강제한다.

- 소스 저장소: `wlan-driver-v2` (required layout tracked-object verified)
- 소스 원격: `https://github.com/jhw7500/wlan-driver-v2.git`
- 소스 설명: `mwifiex-61820-0396-imx93-validated-20260822-135-ge76f50b`
- 소스 commit: `e76f50b9133eaab7ab17053c350caa8bb8176675`
- 소스 범위: declared commit tracks required layout and is contained by local origin/*; supplied outputs are external
- 소스 검증: supplied metadata matched; no remote/build attestation; exact board-qualified payload bytes locked below
- 대상 디렉토리: `dist/wlan/opt/wlan/driver`

## Kernel modules

| 파일 | SHA-256 | version | srcversion | vermagic |
|------|---------|---------|------------|----------|
| debug/mlan.ko | `cbe07111614a3d9bd4f84bd91835335b10c7c90d1ad912b0630bb64589c41eac` | 552.p22 | 14DDBC44ED72E32C7C109DF | 6.6.3-lts-next-g1c0b4db17dce SMP preempt mod_unload modversions aarch64 |
| debug/moal.ko | `c4331a78f287a75422e806a3f83ffd4db53b58ca52d28b9acdc3ac47bf384d32` | 552.p22 | 6E1F62844C9EBD2C56A2195 | 6.6.3-lts-next-g1c0b4db17dce SMP preempt mod_unload modversions aarch64 |
| mlan_imx8.ko | `ce5f2d57e8361fc1fb790a08a0536d4d4cbafd348f9eb8d9180b7e8d3afba644` | 505.p14 | C313A4C1BA94176BBBF91E7 | 6.6.3-lts-next-g9bc88c3c4469-dirty SMP preempt mod_unload modversions aarch64 |
| mlan_imx93.ko | `d7b1c9251bda7008f942ef6214d9f87b2aa136494d1365b146bca91499ece0af` | 552.p22 | 14DDBC44ED72E32C7C109DF | 6.6.3-lts-next-g1c0b4db17dce SMP preempt mod_unload modversions aarch64 |
| moal_imx8.ko | `c967d18482ae674de5aa1fae40c11cfb267bfb410b720111e3ed2f1684d665ea` | 505.p14 | 4BA28708A7C5CBCB7C79BF3 | 6.6.3-lts-next-g9bc88c3c4469-dirty SMP preempt mod_unload modversions aarch64 |
| moal_imx93.ko | `5b85ab33f39ae569b627ae742318df6551a69793244e715fd858d519603be0cb` | 552.p22 | 6E1F62844C9EBD2C56A2195 | 6.6.3-lts-next-g1c0b4db17dce SMP preempt mod_unload modversions aarch64 |

## Board-qualified component lock

| 패키지 경로 | SHA-256 |
|-------------|---------|
| opt/wlan/driver/mlan_imx93.ko | `d7b1c9251bda7008f942ef6214d9f87b2aa136494d1365b146bca91499ece0af` |
| opt/wlan/driver/moal_imx93.ko | `5b85ab33f39ae569b627ae742318df6551a69793244e715fd858d519603be0cb` |
| opt/wlan/bin/mlanutl_imx93 | `cd1dee300da0f9ba625bec9922cf7b75624c548d7e9c3cd863b6dfe8cc9cb92d` |
| usr/lib/firmware/cts/sd9098_wlan_v1.bin | `a05ba158efb0262791a471b90c0e0e616316097ccf6e17f6a170778ee4758813` |
