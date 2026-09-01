# 드라이버 빌드 manifest

> `scripts/gen_driver_manifest.sh --write` 자동 생성 — 수동 편집 금지.
> board-qualified imx93 4-component exact identity는 `DRIVER_COMPONENTS.sha256`가 강제한다.

- 소스 저장소: `wlan-driver-v2` (required layout tracked-object verified)
- 소스 원격: `https://github.com/jhw7500/wlan-driver-v2.git`
- 소스 설명: `mwifiex-61820-0396-imx93-validated-20260822-29-gdc0be6c`
- 소스 commit: `dc0be6cad6238f7d188a5c21a00af5fc0abd345c`
- 소스 범위: declared commit tracks required layout and is contained by local origin/*; supplied outputs are external
- 소스 검증: supplied metadata matched; no remote/build attestation; exact board-qualified payload bytes locked below
- 대상 디렉토리: `dist/wlan/opt/wlan/driver`

## Kernel modules

| 파일 | SHA-256 | version | srcversion | vermagic |
|------|---------|---------|------------|----------|
| mlan_imx93.ko | `4604ce45ca0672bcea73aa0c3b72a38343962c8ae1261dbb5f8b9014c440d2cc` | 543.p18 | D4BAACF8CD5EE3BE07E77AC | 6.6.3-lts-next-gccf0a99701a7-dirty SMP preempt mod_unload modversions aarch64 |
| moal_imx93.ko | `08a204d466a7ca6737c83fbab2f3b7eba666a9096ba7b775df15cdc98269344d` | 543.p18 | 7EC3F43DE3381F51BF26288 | 6.6.3-lts-next-gccf0a99701a7-dirty SMP preempt mod_unload modversions aarch64 |

## Board-qualified component lock

| 패키지 경로 | SHA-256 |
|-------------|---------|
| opt/wlan/driver/mlan_imx93.ko | `4604ce45ca0672bcea73aa0c3b72a38343962c8ae1261dbb5f8b9014c440d2cc` |
| opt/wlan/driver/moal_imx93.ko | `08a204d466a7ca6737c83fbab2f3b7eba666a9096ba7b775df15cdc98269344d` |
| opt/wlan/bin/mlanutl_imx93 | `43b40f38f20d663d16786b50f3069569f73168b4b237c5ae3bcbd3369c6861cc` |
| usr/lib/firmware/cts/sd9098_wlan_v1.bin | `7c3ef6e12d3cfc9bd638d1571ccf6ddd2e96e0ed179ec70664ccb1df0ba29e57` |
