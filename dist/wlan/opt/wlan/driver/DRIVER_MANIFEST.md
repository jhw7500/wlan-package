# 드라이버 빌드 manifest

> `scripts/gen_driver_manifest.sh --write` 자동 생성 — 수동 편집 금지.
> board-qualified imx93 4-component exact identity는 `DRIVER_COMPONENTS.sha256`가 강제한다.

- 소스 저장소: `wlan-driver-v2` (required layout tracked-object verified)
- 소스 원격: `https://github.com/jhw7500/wlan-driver-v2.git`
- 소스 설명: `mwifiex-61820-0396-imx93-validated-20260822-34-g3406423`
- 소스 commit: `3406423fc34b393df0a3aa0969d51bd4b80d2906`
- 소스 범위: declared commit tracks required layout and is contained by local origin/*; supplied outputs are external
- 소스 검증: supplied metadata matched; no remote/build attestation; exact board-qualified payload bytes locked below
- 대상 디렉토리: `dist/wlan/opt/wlan/driver`

## Kernel modules

| 파일 | SHA-256 | version | srcversion | vermagic |
|------|---------|---------|------------|----------|
| mlan_imx93.ko | `3bda99eb3ebac7a93ac880bcc21d6b3cf6f069e2476a1dbee3fe0d733af0e761` | 543.p18 | 69FB1CDC4109F4A73C98B59 | 6.6.3-lts-next-gccf0a99701a7-dirty SMP preempt mod_unload modversions aarch64 |
| moal_imx93.ko | `08a204d466a7ca6737c83fbab2f3b7eba666a9096ba7b775df15cdc98269344d` | 543.p18 | 7EC3F43DE3381F51BF26288 | 6.6.3-lts-next-gccf0a99701a7-dirty SMP preempt mod_unload modversions aarch64 |

## Board-qualified component lock

| 패키지 경로 | SHA-256 |
|-------------|---------|
| opt/wlan/driver/mlan_imx93.ko | `3bda99eb3ebac7a93ac880bcc21d6b3cf6f069e2476a1dbee3fe0d733af0e761` |
| opt/wlan/driver/moal_imx93.ko | `08a204d466a7ca6737c83fbab2f3b7eba666a9096ba7b775df15cdc98269344d` |
| opt/wlan/bin/mlanutl_imx93 | `f082df6862a96224c29ae912dc5a406af2a083e8cf3f8127aa2f7590475ad3d9` |
| usr/lib/firmware/cts/sd9098_wlan_v1.bin | `7c3ef6e12d3cfc9bd638d1571ccf6ddd2e96e0ed179ec70664ccb1df0ba29e57` |
