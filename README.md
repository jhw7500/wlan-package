# wlan-package

Debian package for deploying WLAN application infrastructure on ARM64 embedded systems (i.MX8MM/i.MX93 + NXP 88W9098).

## Overview

This package (`wlan-proc`) bundles the wlan-bridge L2 network bridge along with supporting scripts, configuration files, and systemd services for wireless network management on embedded Linux systems.

**Current Version:** 0.5.4

## Prerequisites

### Build Host Requirements

- Cross-compilation toolchain for ARM64 (aarch64-linux-gnu)
- `libpcap-dev` development headers
- `dpkg-deb` (Debian packaging tools)
- `make`
- `tar`
- Python 3 with `pytest` and `jsonschema` (release gates)
- For x86_64 cross-builds, both NXP i.MX8 and i.MX93 SDK environment files;
  set `SDK_LOC`/`SDK_NAME` when they are not installed under `/shared`

Install dependencies on Ubuntu/Debian:
```bash
sudo apt-get update
sudo apt-get install -y gcc-aarch64-linux-gnu libpcap-dev dpkg-dev make tar python3-pytest python3-jsonschema
```

The generic ARM64 toolchain builds `wlan-opc` and `vhld`. The two board-specific
`wbridge` builds additionally source the matching NXP SDK environment. On the
current build host those are:

- `/shared/fsl-imx-xwayland/6.6-nanbield/environment-setup-armv8a-poky-linux` (i.MX8)
- `/shared/fsl-imx-wayland/6.6-nanbield/environment-setup-armv8a-poky-linux` (i.MX93)

### Target System Requirements

- ARM64 Linux kernel 5.4+
- systemd
- libpcap (runtime)
- NXP88W9098 wireless driver

## Build Instructions

### 1. Clone the Repository

```bash
git clone <repository-url>
cd wlan-package
```

### 2. Initialize Submodules

The wlan-bridge is included as a git submodule:
```bash
git submodule update --init --recursive
```

### 3. Build the Package

The build script will:
- Compile wlan-bridge binaries (`wbridge` and `wbridge-tpacket`) for the supported boards
- Copy binaries, scripts, and configuration files to `dist/`
- Create and validate the Debian package and a sanitized source archive in `release/`

```bash
./build.sh
```

**Output:**
- `wlan-bridge/wbridge/release/wbridge_<board>` - libpcap-based bridge binary
- `wlan-bridge/wbridge/release/wbridge-tpacket_<board>` - TPACKET_V3 bridge binary
- `release/wlan.deb` - Latest build
- `release/wlan-proc-0.5.4.deb` - Versioned package
- `release/wlan-package.tar` - Allowlisted source/build archive
- `release/SHA256SUMS` - Release-set hashes, published last

The source archive contains only the files required to rebuild and validate the
package. Local agent state, target-test artifacts, private working documents,
caches, and generated subproject binaries are deliberately excluded.
Verify all release files together with `cd release && sha256sum -c SHA256SUMS`;
the checksum manifest is published last so an interrupted multi-file publish is
detected as a mismatch rather than treated as a coherent release set.

**Note:** The `wlan-bridge/wbridge/release/` and `wlan-bridge/wbridge/debug/` directories are build outputs and should not be committed.

### 4. (Optional) Update Scripts from System

If you need to include scripts/logger from the build host:
```bash
./update.sh
```

**Note:** This requires `/usr/local/scripts/` and `/usr/local/logger/` to exist on the build host.

## Package Structure

```
wlan-package/
├── build.sh             # Package build script
├── update.sh            # Update scripts from system
├── wlan-bridge/         # Git submodule
│   └── wbridge/
│       ├── release/     # Compiled release binaries (stripped)
│       │   ├── wbridge_<board>
│       │   └── wbridge-tpacket_<board>
│       ├── debug/       # Compiled debug binaries (unstripped)
│       ├── bridge.c
│       ├── wbridge-tpacket.c
│       └── Makefile
└── dist/wlan/           # Debian package contents
    ├── DEBIAN/
    │   ├── control          # Package metadata
    │   ├── postinst         # Post-installation script
    │   ├── preinst          # Pre-installation script
    │   ├── postrm           # Post-removal script
    │   └── prerm            # Pre-removal script
    ├── etc/
    │   ├── systemd/         # Systemd service files
    │   ├── rsyslog.d/       # Logging configuration
    │   └── ...
    └── usr/local/
        ├── wlan-bridge/     # L2 bridge binaries and docs
        │   ├── wbridge/
        │   ├── scripts/
        │   └── docs/
        ├── scripts/         # WLAN management scripts
        └── logger/          # Logging utilities
```

## Deployment

### 1. Transfer Package to Target

```bash
scp release/wlan-proc-0.5.4.deb root@<target-ip>:/tmp/
```

### 2. Install on Target System

```bash
ssh root@<target-ip>
dpkg -i /tmp/wlan-proc-0.5.4.deb
```

The `postinst` script will automatically:
- Configure system services
- Set up network interfaces
- Enable required systemd units
- Create symbolic links for binaries

`/usr/local/etc/config.json`과 nginx는 별도 `wifi_manager` 패키지가
설치·관리한다. `wlan-proc`는 이 파일과 서비스를 설치, 삭제, Factory Reset,
release 검증 대상으로 취급하지 않는다. Factory Reset의 `eth0` 공장 기본값은
`192.168.1.1/24`이며, 일반 패키지 업그레이드에서는 현재 active 네트워크 설정을
보존한다.

### 3. Verify Installation

```bash
# Check installed version
dpkg -l | grep wlan-proc

# Check service status
systemctl status wifi_init
systemctl status wifi_logger

# Test bridge binary
wifi-wbridge --help
```

## Components

### wlan-bridge

High-performance userspace L2 bridge for wired/wireless interfaces. See `wlan-bridge/` submodule for detailed documentation.

**Key Features:**
- libpcap-based packet forwarding
- VLAN 802.1Q support
- Low-latency design for real-time applications
- Systemd integration

**Binaries:**
- `/usr/local/bin/wifi-wbridge` - Production bridge (libpcap)
- `/usr/local/bin/wifi-wbridge-tpacket` - Experimental high-performance version

**Optional Debug Binaries:**
- `/usr/local/wlan-bridge/debug/wbridge_<board>` - Debug bridge build
- `/usr/local/wlan-bridge/debug/wbridge-tpacket_<board>` - Debug TPACKET build

### Systemd Services

The package includes multiple systemd services:
- `wifi_init.service` - WLAN initialization
- `wifi_logger@.service` - Per-interface logging
- `wifi_bridge@.service` - L2 bridge service
- `wifi_checker@.service` - Connection monitoring
- `wifi_roam@.service` - Roaming management
- And more...

### Logger Control

System and per-interface logger groups expose the same six lifecycle actions:

```bash
wifi log system start|stop|restart|status|enable|disable
wifi mlan0 log start|stop|restart|status|enable|disable
wifi mlan1 log start|stop|restart|status|enable|disable
wifi eth0  log start|stop|restart|status|enable|disable
```

There is intentionally no aggregate interface command. `start`, `stop`, and
`restart` affect only the current runtime. `enable` and `disable` update only
the persistent boot policy; they do not implicitly start or stop the group.

The system group controls independently supervised CPU, MMC, temperature, MCP,
and summary children. The temperature child also owns overtemperature
protection, so stopping or disabling the system group stops both temperature
logging and thermal protection; the CLI prints a warning before doing so.
Interface logger children are supervised per interface. Child restart bursts
are limited to 10 failures per 300 seconds with a 3-second retry delay.
Potentially blocking stat/snapshot/CPU/MMC/MCP operations time out after 5
seconds; each WLAN temperature query times out after 3 seconds and is recorded
as `unknown` rather than `0`.

## Configuration

### Bridge Configuration

Edit `/usr/local/mfg/bridge_init.conf` to configure bridge parameters.

### Network Interfaces

Systemd network configuration files are located in `/etc/systemd/network/`:
- `20-mlan0.network` - Wireless interface (mlan0)
- `21-mlan1.network` - Wireless interface (mlan1)
- `22-eth0.network` - Wired interface (eth0)

## Troubleshooting

### Build Failures

**Error: "Failed to build wlan-bridge binaries"**
- Ensure `libpcap-dev` is installed
- Check cross-compiler is in PATH
- Verify submodule is initialized: `git submodule status`
- Check if `wlan-bridge/wbridge/release/` directory was created successfully

**Error: "Source directory does not exist" (update.sh)**
- The `update.sh` script requires existing system directories
- Either create the directories or skip running `update.sh`

**Clean Build**
If you need to start fresh:
```bash
cd wlan-bridge/wbridge
make clean
cd ../..
rm -rf release/
./build.sh
```

### Runtime Issues

Check logs:
```bash
journalctl -u wifi_init
journalctl -u wifi_bridge@mlan0
```

Enable debug logging:
```bash
wifi-dumb -i mlan0 -o eth0 -v
```

## Version History

- **0.4.0** - wifi mode/bw/connect/radio-apply 명령 재설계, extra_ssids 다중 SSID 로밍, wpa 런타임 적용(reconfigure), OPC 프로토콜 사양 정합·입력검증(비호환), nl80211 indication·device-info publish·FaultDetect, EAPOL/802.1D 차단. 상세: [`CHANGELOG.md`](CHANGELOG.md)
- **0.3.1** - opcd nxp 백엔드 패키징, 공통 header 64B/Length, dpkg purge 정리
- **0.3.0** - wbridge 설정 구조화(optimize/link_guard/thermal), engine=moal 드라이버 bridge, 드라이버/conf 통일, imx93 SDIO IRQ 대응
- **0.2.0** - 서비스 wifi_ prefix 통일, cron→timer 이관, 주기 로밍/ping 모니터, 스캔 필터링
- **0.1.3** - Integrated wlan-bridge as submodule
- **0.1.2** - dumb bridge improvements
- **0.1.1** - Added SNMP, capture, roam features
- **0.1.0** - Initial release with dumb bridge
- See `dist/wlan/DEBIAN/control` for full version history

## License

See individual component licenses in their respective directories.

## Support

For issues related to:
- **wlan-bridge**: See `wlan-bridge/` submodule documentation
- **Package build/deployment**: Open an issue in this repository
- **Target system integration**: Contact maintainer (hwjo@cantops.biz)
