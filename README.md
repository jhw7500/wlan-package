# wlan-package

Debian package for deploying WLAN application infrastructure on ARM64 embedded systems (i.MX8MM + NXP88W9098).

## Overview

This package (`wlan-proc`) bundles the wlan-bridge L2 network bridge along with supporting scripts, configuration files, and systemd services for wireless network management on embedded Linux systems.

**Current Version:** 0.1.4

## Prerequisites

### Build Host Requirements

- Cross-compilation toolchain for ARM64 (aarch64-linux-gnu)
- `libpcap-dev` development headers
- `dpkg-deb` (Debian packaging tools)
- `make`
- `tar`

Install dependencies on Ubuntu/Debian:
```bash
sudo apt-get update
sudo apt-get install -y gcc-aarch64-linux-gnu libpcap-dev dpkg-dev make tar
```

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
- Compile wlan-bridge binaries (`dumb` and `dumb-tpacket`) into `wlan-bridge/dumb/release/` directory
- Copy binaries, scripts, and configuration files to `dist/`
- Create a Debian package in `release/`

```bash
./build.sh
```

**Output:**
- `wlan-bridge/dumb/release/dumb` - libpcap-based bridge binary
- `wlan-bridge/dumb/release/dumb-tpacket` - TPACKET_V3 bridge binary
- `release/wlan.deb` - Latest build
- `release/wlan-proc-0.1.4.deb` - Versioned package
- `release/wlan-package.tar` - Full package archive

**Note:** The `wlan-bridge/dumb/release/` and `wlan-bridge/dumb/debug/` directories are build outputs and should not be committed.

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
│   └── dumb/
│       ├── release/     # Compiled release binaries (stripped)
│       │   ├── dumb
│       │   └── dumb-tpacket
│       ├── debug/       # Compiled debug binaries (unstripped)
│       │   ├── dumb
│       │   └── dumb-tpacket
│       ├── dumb.c
│       ├── dumb-tpacket.c
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
        │   ├── dumb/
        │   ├── scripts/
        │   └── docs/
        ├── scripts/         # WLAN management scripts
        └── logger/          # Logging utilities
```

## Deployment

### 1. Transfer Package to Target

```bash
scp release/wlan-proc-0.1.4.deb root@<target-ip>:/tmp/
```

### 2. Install on Target System

```bash
ssh root@<target-ip>
dpkg -i /tmp/wlan-proc-0.1.4.deb
```

The `postinst` script will automatically:
- Configure system services
- Set up network interfaces
- Enable required systemd units
- Create symbolic links for binaries

### 3. Verify Installation

```bash
# Check installed version
dpkg -l | grep wlan-proc

# Check service status
systemctl status wifi_init
systemctl status wifi_logger

# Test bridge binary
wifi-dumb --help
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
- `/usr/local/bin/wifi-dumb` - Production bridge (libpcap)
- `/usr/local/bin/wifi-dumb-tpacket` - Experimental high-performance version

**Optional Debug Binaries:**
- `/usr/local/wlan-bridge/debug/dumb` - Debug build (unstripped, may not be included)
- `/usr/local/wlan-bridge/debug/dumb-tpacket` - Debug build (unstripped, may not be included)

### Systemd Services

The package includes multiple systemd services:
- `wifi_init.service` - WLAN initialization
- `wifi_logger@.service` - Per-interface logging
- `wifi_bridge@.service` - L2 bridge service
- `wifi_checker@.service` - Connection monitoring
- `wifi_roam@.service` - Roaming management
- And more...

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
- Check if `wlan-bridge/dumb/release/` directory was created successfully

**Error: "Source directory does not exist" (update.sh)**
- The `update.sh` script requires existing system directories
- Either create the directories or skip running `update.sh`

**Clean Build**
If you need to start fresh:
```bash
cd wlan-bridge/dumb
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
