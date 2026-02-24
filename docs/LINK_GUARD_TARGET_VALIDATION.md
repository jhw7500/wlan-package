# Link Guard Target Validation Checklist

This checklist validates link-guard behavior on the real target device.

## Scope

- Startup guard in `wifi_bridge.sh`
- Runtime supervision behavior for wired/wireless link changes
- Runtime TX short-circuit in `wifi-wbridge` (pcap) and `wifi-wbridge-tpacket`
- Config knobs in `/etc/default/wbridge`
- Runtime snapshots in `/run/wbridge.effective.json` and `/run/wbridge.apply.json`

Current behavior is scoped to `wifi_bridge@mlan0`.

## Preconditions

- Run as `root` on target.
- Service exists: `wifi_bridge@mlan0.service`
- One peer can generate traffic into the active side during link-down tests.

## Quick Baseline Capture

```bash
systemctl status wifi_bridge@mlan0 --no-pager
cat /etc/default/wbridge
cat /run/wbridge.effective.json 2>/dev/null || true
cat /run/wbridge.apply.json 2>/dev/null || true
journalctl -u wifi_bridge@mlan0 -n 120 --no-pager
```

## Temporary Test Profile

Backup config first:

```bash
cp /etc/default/wbridge /tmp/wbridge.before-link-guard
```

Recommended test values:

```bash
sed -i 's/^WBRIDGE_LINK_GUARD=.*/WBRIDGE_LINK_GUARD=1/' /etc/default/wbridge
sed -i 's/^WBRIDGE_LINK_DOWN_DEBOUNCE_SEC=.*/WBRIDGE_LINK_DOWN_DEBOUNCE_SEC=2/' /etc/default/wbridge
sed -i 's/^WBRIDGE_LINK_UP_STABLE_SEC=.*/WBRIDGE_LINK_UP_STABLE_SEC=2/' /etc/default/wbridge
sed -i 's/^WBRIDGE_LINK_IDLE_POLL_SEC=.*/WBRIDGE_LINK_IDLE_POLL_SEC=2/' /etc/default/wbridge
sed -i 's/^WBRIDGE_WAIT_READY_TIMEOUT_SEC=.*/WBRIDGE_WAIT_READY_TIMEOUT_SEC=20/' /etc/default/wbridge
sed -i 's/^WBRIDGE_WLAN_ROAM_GRACE_SEC=.*/WBRIDGE_WLAN_ROAM_GRACE_SEC=15/' /etc/default/wbridge
sed -i 's/^WBRIDGE_WLAN_DOWN_RESTART=.*/WBRIDGE_WLAN_DOWN_RESTART=0/' /etc/default/wbridge
systemctl restart wifi_bridge@mlan0
```

## PASS/FAIL Matrix

| ID | Scenario | PASS Criteria | Evidence |
|---|---|---|---|
| LG1 | Startup with wired down | Bridge child does not start until wired recovers | `ps`, journal |
| LG2 | Startup with wireless down | Startup timeout logs appear, bridge child still starts if wired is up | `ps`, journal |
| LG3 | Runtime wireless down (roaming) | Bridge child stays alive by default (`WBRIDGE_WLAN_DOWN_RESTART=0`) | `ps`, journal |
| LG4 | Runtime wired down | Bridge child stops within debounce and waits for wired recovery | `ps`, journal |
| LG5 | Runtime peer down under traffic (pcap/tpacket) | RX can rise, TX forwarding attempts are curtailed, drop rises without error storm | SIGUSR1 stats + journal |
| LG6 | No restart storm | `NRestarts` does not rapidly increase during short flap | `systemctl show -p NRestarts` |

## Detailed Procedure

### LG1: Startup behavior when wired is down

```bash
ip link set eth0 down
systemctl restart wifi_bridge@mlan0
sleep 5
pid=$(systemctl show -p MainPID --value wifi_bridge@mlan0)
ps -p "$pid" -o pid,args=
journalctl -u wifi_bridge@mlan0 -n 80 --no-pager
```

PASS:
- command line remains `/usr/local/scripts/wifi_bridge.sh mlan0`
- journal includes `Wired link is not ready, delaying bridge start`

### LG2: Startup behavior when wireless is down

Keep wired up and force wireless to disconnected state, then run:

```bash
systemctl restart wifi_bridge@mlan0
sleep 22
pid=$(systemctl show -p MainPID --value wifi_bridge@mlan0)
ps -p "$pid" -o pid,args=
journalctl -u wifi_bridge@mlan0 -n 120 --no-pager
```

PASS:
- journal contains timeout warning
- child process starts (`wifi-wbridge` or `wifi-wbridge-tpacket`)

### LG3: Runtime wireless-down roaming behavior

```bash
pid=$(systemctl show -p MainPID --value wifi_bridge@mlan0)
ps -p "$pid" -o pid,args=
journalctl -u wifi_bridge@mlan0 -n 120 --no-pager
```

PASS:
- after wireless down exceeds `WBRIDGE_WLAN_ROAM_GRACE_SEC`, journal logs hold message
- child process remains alive when `WBRIDGE_WLAN_DOWN_RESTART=0`

### LG4: Runtime wired-down stop/recover behavior

```bash
ip link set eth0 down
sleep 3
journalctl -u wifi_bridge@mlan0 -n 120 --no-pager
ip link set eth0 up
sleep 5
journalctl -u wifi_bridge@mlan0 -n 120 --no-pager
```

PASS:
- wired down logs `Wired link down ... stopping bridge process`
- after wired recovery, child starts again

### LG5: Runtime TX short-circuit under peer-down traffic

Run once with `WBRIDGE_ENGINE=pcap`, then again with `WBRIDGE_ENGINE=tpacket`.

```bash
sed -i 's/^WBRIDGE_ENGINE=.*/WBRIDGE_ENGINE=pcap/' /etc/default/wbridge
systemctl restart wifi_bridge@mlan0
pid=$(systemctl show -p MainPID --value wifi_bridge@mlan0)
kill -USR1 "$pid"
sleep 1
journalctl -u wifi_bridge@mlan0 -n 80 --no-pager

# force peer down while traffic is still fed into active side
kill -USR1 "$pid"
sleep 1
journalctl -u wifi_bridge@mlan0 -n 120 --no-pager
```

PASS:
- drop counters increase in peer-down window
- no sustained flood of `pcap_inject failed` / `sendto failed`
- service stays healthy

### LG6: Restart stability check

```bash
systemctl show -p NRestarts wifi_bridge@mlan0
journalctl -u wifi_bridge@mlan0 -n 200 --no-pager | grep -E "Wired link down|Wireless link down|Bridge process running|Bridge cycle ended"
```

PASS:
- `NRestarts` remains low and does not rapidly climb on short flaps

## Optional Resource Snapshot During Test

```bash
pid=$(systemctl show -p MainPID --value wifi_bridge@mlan0)
ps -p "$pid" -o pid,pcpu,pmem,args=
ip -s link show eth0
ip -s link show mlan0
```

## Cleanup / Rollback

```bash
cp /tmp/wbridge.before-link-guard /etc/default/wbridge
ip link set eth0 up
systemctl restart wifi_bridge@mlan0
```

## Report Template

```text
Date/Target:
Engine tested: pcap / tpacket
LG1: PASS|FAIL (evidence)
LG2: PASS|FAIL (evidence)
LG3: PASS|FAIL (evidence)
LG4: PASS|FAIL (evidence)
LG5: PASS|FAIL (evidence)
LG6: PASS|FAIL (evidence)
Notes:
```
