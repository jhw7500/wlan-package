# 드라이버 빌드 manifest

> `scripts/gen_driver_manifest.sh --write` 자동 생성 — 수동 편집 금지.
> board-qualified imx93 4-component exact identity는 `DRIVER_COMPONENTS.sha256`가 강제한다.

- 소스 저장소: `wlan-driver-v2` (required layout tracked-object verified)
- 소스 원격: `https://github.com/jhw7500/wlan-driver-v2.git`
- 소스 설명: `mwifiex-61820-0396-imx93-validated-20260822`
- 소스 commit: `26400d66cc56e9af0096273b5d25d31d3e001fa6`
- 소스 범위: declared commit tracks required layout and is contained by local origin/*; supplied outputs are external
- 소스 검증: supplied metadata matched; no remote/build attestation; exact board-qualified payload bytes locked below
- 대상 디렉토리: `dist/wlan/opt/wlan/driver`

## Kernel modules

| 파일 | SHA-256 | version | srcversion | vermagic |
|------|---------|---------|------------|----------|
| debug/mlan.ko | `74a9fc0784d8a1d16d938d988d1f7888cef7b730e2878bc9bd7e5616a2e92d3b` | 405.p61 | 41705D05ED8C1DA0F3483A7 | 6.6.3-lts-next-ge16172170484-dirty SMP preempt mod_unload modversions aarch64 |
| debug/moal.ko | `8e9bfcf21d71850912698c4845c955ca9997a298de28b290045a8d115e41d395` | 405.p61 | 7AD84AFC1AE8006735BCAAD | 6.6.3-lts-next-ge16172170484-dirty SMP preempt mod_unload modversions aarch64 |
| mlan_imx8.ko | `ce5f2d57e8361fc1fb790a08a0536d4d4cbafd348f9eb8d9180b7e8d3afba644` | 505.p14 | C313A4C1BA94176BBBF91E7 | 6.6.3-lts-next-g9bc88c3c4469-dirty SMP preempt mod_unload modversions aarch64 |
| mlan_imx93.ko | `c351a0d63f75d53f99ca0b74aba3911d79e9aaf1f4e269dac68cba9b06e4c46a` | 543.p18 | 69CD10BAA7F3A642C954443 | 6.6.3-lts-next-gccf0a99701a7-dirty SMP preempt mod_unload modversions aarch64 |
| moal_imx8.ko | `c967d18482ae674de5aa1fae40c11cfb267bfb410b720111e3ed2f1684d665ea` | 505.p14 | 4BA28708A7C5CBCB7C79BF3 | 6.6.3-lts-next-g9bc88c3c4469-dirty SMP preempt mod_unload modversions aarch64 |
| moal_imx93.ko | `87b9d0dc5b86c4a40560050f4e9c5a2c8662acc97bea6b516f0f094fcfc9b6a0` | 543.p18 | E14FF2EA56EE8DA9F44DC18 | 6.6.3-lts-next-gccf0a99701a7-dirty SMP preempt mod_unload modversions aarch64 |

## Board-qualified component lock

| 패키지 경로 | SHA-256 |
|-------------|---------|
| opt/wlan/driver/mlan_imx93.ko | `c351a0d63f75d53f99ca0b74aba3911d79e9aaf1f4e269dac68cba9b06e4c46a` |
| opt/wlan/driver/moal_imx93.ko | `87b9d0dc5b86c4a40560050f4e9c5a2c8662acc97bea6b516f0f094fcfc9b6a0` |
| opt/wlan/bin/mlanutl_imx93 | `86ea019edd766b2c426026a4ffd86538af1f6ce85060e68cb02bbd8cc81d6f95` |
| usr/lib/firmware/cts/sd9098_wlan_v1.bin | `7c3ef6e12d3cfc9bd638d1571ccf6ddd2e96e0ed179ec70664ccb1df0ba29e57` |
