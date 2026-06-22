# 드라이버 빌드 manifest

> `scripts/gen_driver_manifest.sh` 자동 생성 — 수동 편집 금지.
> 바이너리 `.ko` 는 .gitignore 대상이며, 이 파일이 배포된 드라이버의 약식 이력이다.

- 소스(wlan-driver-v2): `lf-6.12.3-1.0.0-95-g8beeb06` (HEAD 8beeb06)
- 대상 디렉토리: `dist/wlan/opt/wlan/driver`

| 파일 | version | srcversion | vermagic |
|------|---------|------------|----------|
| debug/mlan.ko | 405.p61 | 41705D05ED8C1DA0F3483A7 | 6.6.3-lts-next-ge16172170484-dirty SMP preempt mod_unload modversions aarch64 |
| debug/moal.ko | 405.p61 | 7AD84AFC1AE8006735BCAAD | 6.6.3-lts-next-ge16172170484-dirty SMP preempt mod_unload modversions aarch64 |
| mlan_imx8.ko | 505.p14 | 5F4BD26BEBEB8B6CB3747FF | 6.6.3-lts-next-g9bc88c3c4469-dirty SMP preempt mod_unload modversions aarch64 |
| mlan_imx93.ko | 505.p14 | 5F4BD26BEBEB8B6CB3747FF | 6.6.3-lts-next-gccf0a99701a7-dirty SMP preempt mod_unload modversions aarch64 |
| moal_imx8.ko | 505.p14 | 7E3CF26C17676EE7E9E7915 | 6.6.3-lts-next-g9bc88c3c4469-dirty SMP preempt mod_unload modversions aarch64 |
| moal_imx93.ko | 505.p14 | 7E3CF26C17676EE7E9E7915 | 6.6.3-lts-next-gccf0a99701a7-dirty SMP preempt mod_unload modversions aarch64 |
