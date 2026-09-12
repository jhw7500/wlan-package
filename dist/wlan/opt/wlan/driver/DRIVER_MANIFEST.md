# 드라이버 빌드 manifest

> `scripts/gen_driver_manifest.sh --write` 자동 생성 — 수동 편집 금지.
> board-qualified imx93 4-component exact identity는 `DRIVER_COMPONENTS.sha256`가 강제한다.

- 소스 저장소: `wlan-driver-v2` (required layout tracked-object verified)
- 소스 원격: `https://github.com/jhw7500/wlan-driver-v2.git`
- 소스 설명: `mwifiex-61820-0396-imx93-validated-20260822-84-g53cfcf3`
- 소스 commit: `53cfcf35cef80d93f422a76f81323776ae78742e`
- 소스 범위: declared commit tracks required layout and is contained by local origin/*; supplied outputs are external
- 소스 검증: supplied metadata matched; no remote/build attestation; exact board-qualified payload bytes locked below
- 대상 디렉토리: `dist/wlan/opt/wlan/driver`

## Kernel modules

| 파일 | SHA-256 | version | srcversion | vermagic |
|------|---------|---------|------------|----------|
| debug/mlan.ko | `74a9fc0784d8a1d16d938d988d1f7888cef7b730e2878bc9bd7e5616a2e92d3b` | 405.p61 | 41705D05ED8C1DA0F3483A7 | 6.6.3-lts-next-ge16172170484-dirty SMP preempt mod_unload modversions aarch64 |
| debug/moal.ko | `8e9bfcf21d71850912698c4845c955ca9997a298de28b290045a8d115e41d395` | 405.p61 | 7AD84AFC1AE8006735BCAAD | 6.6.3-lts-next-ge16172170484-dirty SMP preempt mod_unload modversions aarch64 |
| mlan_imx8.ko | `ce5f2d57e8361fc1fb790a08a0536d4d4cbafd348f9eb8d9180b7e8d3afba644` | 505.p14 | C313A4C1BA94176BBBF91E7 | 6.6.3-lts-next-g9bc88c3c4469-dirty SMP preempt mod_unload modversions aarch64 |
| mlan_imx93.ko | `772e433b2ccba132a50e27190299b2cb887a2bed0fea5c8f234c30264d962a33` | 543.p18 | 69FB1CDC4109F4A73C98B59 | 6.6.3-lts-next-g1c0b4db17dce SMP preempt mod_unload modversions aarch64 |
| moal_imx8.ko | `c967d18482ae674de5aa1fae40c11cfb267bfb410b720111e3ed2f1684d665ea` | 505.p14 | 4BA28708A7C5CBCB7C79BF3 | 6.6.3-lts-next-g9bc88c3c4469-dirty SMP preempt mod_unload modversions aarch64 |
| moal_imx93.ko | `7f0a901eeea36093950bbcd84fd4942d35613b45580983c4539b683cc5003706` | 543.p18 | 7EC3F43DE3381F51BF26288 | 6.6.3-lts-next-g1c0b4db17dce SMP preempt mod_unload modversions aarch64 |

## Board-qualified component lock

| 패키지 경로 | SHA-256 |
|-------------|---------|
| opt/wlan/driver/mlan_imx93.ko | `772e433b2ccba132a50e27190299b2cb887a2bed0fea5c8f234c30264d962a33` |
| opt/wlan/driver/moal_imx93.ko | `7f0a901eeea36093950bbcd84fd4942d35613b45580983c4539b683cc5003706` |
| opt/wlan/bin/mlanutl_imx93 | `d1cd869b7d75118fad5ca936e9919dafbe99831fba215ad35a1c2d67de1a914b` |
| usr/lib/firmware/cts/sd9098_wlan_v1.bin | `7c3ef6e12d3cfc9bd638d1571ccf6ddd2e96e0ed179ec70664ccb1df0ba29e57` |
