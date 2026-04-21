# Thermal Optimization Design - Interrupt Coalescing + sysctl Tuning

**Date:** 2026-02-20
**Status:** Implemented
**Approach:** 3-mode optimization (latency / normal / thermal)

## Background

i.MX8MM + NXP 88W9098 WiFi bridge device experiencing high temperatures despite low CPU usage (15%).
Root cause: Interrupt Storm - excessive context switching from per-packet interrupts during 100Mbps traffic.

## Current State

- wbridge: userspace L2 bridge (pcap-based, NOT Linux br0)
- Interrupt coalescing: OFF (rx-usecs=0, rx-frames=1) - latency optimized
- GRO/GSO/TSO: OFF
- IRQ affinity: ETH->CPU2, WLAN->CPU3 (already distributed)
- CPU affinity: bridge threads on CPU0/1
- RT scheduling: SCHED_FIFO priority 50
- pcap immediate mode: ON

## Design

### 1. setup-irq-affinity.sh - 3-mode support

Single script with `--mode latency|normal|thermal` argument:

```bash
sudo ./setup-irq-affinity.sh --mode thermal eth0 mlan0   # 발열 우선
sudo ./setup-irq-affinity.sh --mode latency eth0 mlan0   # 레이턴시 우선
sudo ./setup-irq-affinity.sh eth0 mlan0                  # 일반 (기본)
```

Mode parameters defined in script. See `wlan-bridge/docs/optimization-modes.md`.

### 2. sysctl.conf - Network Stack Tuning

Added to backup/sysctl.conf:

```conf
net.core.netdev_budget = 600
net.core.netdev_max_backlog = 2000
```

### 3. wbridge - Environment Variable Control

Default config.c unchanged (normal mode). Mode-specific via env vars:

```bash
# thermal
WBRIDGE_DISPATCH_BUDGET=128 WBRIDGE_IMMEDIATE=0 WBRIDGE_TIMEOUT_MS=10 wbridge eth0 mlan0

# latency
WBRIDGE_DISPATCH_BUDGET=64 WBRIDGE_IMMEDIATE=1 WBRIDGE_TIMEOUT_MS=1 wbridge eth0 mlan0
```

### 4. wifi_mod_para.conf - Driver Parameters

Added to PCIE9098_0 and PCIE9098_1:

```conf
pcie_int_mode=1    # MSI interrupt mode
napi=1             # Enable NAPI polling
```

See `wlan-bridge/docs/driver-options.md` for details.

## Items NOT Applied (from email review)

| Item | Reason |
|---|---|
| bridge-nf-call-iptables=0 | N/A - wbridge is userspace, not Linux br0 |
| nftables Flow Offload | N/A - no iptables/nftables in packet path |
| NAPI weight tuning | Requires kernel rebuild, out of scope |
| DVFS clock limiting | Deferred to Plan B if Plan A insufficient |
| PCIe ASPM | Deferred to Plan B - stability risk |

## Email Errata

1. `smp_offinity` typo -> correct: `smp_affinity`
2. ethtool coalescing may not work on moal WiFi driver - use wifi_mod_para.conf instead
3. 88Q9098 (automotive) vs 88W9098 (this project) - different chips

## Expected Impact

- Interrupt frequency: ~80-90% reduction
- CPU idle opportunity: significantly increased (deeper C-states)
- Cache efficiency: improved (batch processing)
- Latency increase: ~100-200us (acceptable for 100Mbps bridge)
- Temperature: meaningful reduction expected

## Rollback

All changes are reversible:
1. setup-irq-affinity.sh: revert ethtool values
2. sysctl.conf: remove added lines
3. wbridge: override with environment variables
4. wifi_mod_para.conf: remove added parameters
