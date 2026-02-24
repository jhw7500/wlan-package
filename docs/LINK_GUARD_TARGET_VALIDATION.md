# Link Guard Target Validation Checklist

This checklist validates the new link-guard behavior on the real target device.

## Scope

- Startup guard in `wifi_bridge.sh`
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
sed -i 's/^WBRIDGE_LINK_DOWN_DEBOUNCE_SEC=.*/WBRIDGE_LINK_DOWN_DEBOUNCE_SEC=3/' /etc/default/wbridge
sed -i 's/^WBRIDGE_LINK_UP_STABLE_SEC=.*/WBRIDGE_LINK_UP_STABLE_SEC=8/' /etc/default/wbridge
sed -i 's/^WBRIDGE_LINK_IDLE_POLL_SEC=.*/WBRIDGE_LINK_IDLE_POLL_SEC=10/' /etc/default/wbridge
sed -i 's/^WBRIDGE_WAIT_READY_TIMEOUT_SEC=.*/WBRIDGE_WAIT_READY_TIMEOUT_SEC=40/' /etc/default/wbridge
systemctl restart wifi_bridge@mlan0
```

## PASS/FAIL Matrix

| ID | Scenario | PASS Criteria | Evidence |
|---|---|---|---|
| LG1 | Startup with one link down, guard on | Service process stays in startup wait mode (no `wifi-wbridge*` exec yet) | `ps`, journal |
| LG2 | Link recovers and stays up | After stable window, bridge binary starts | `ps`, journal, `/run/wbridge.apply.json` |
| LG3 | Guard off compatibility | After timeout, startup continues even if one link still down | journal contains timeout-continue message |
| LG4 | Runtime peer down under traffic (pcap) | RX can rise, TX forwarding attempts are curtailed, drop rises without error storm | SIGUSR1 stats + journal |
| LG5 | Runtime peer down under traffic (tpacket) | Same as LG4 on tpacket engine | SIGUSR1 stats + journal |
| LG6 | No restart storm | `NRestarts` does not rapidly increase during short flap | `systemctl show -p NRestarts` |

## Detailed Procedure

### LG1: Startup wait when one link is down

1. Bring one side down before restart (example uses eth0):

```bash
ip link set eth0 down
systemctl restart wifi_bridge@mlan0
sleep 5
```

2. Verify process is still startup script, not bridge binary:

```bash
pid=$(systemctl show -p MainPID --value wifi_bridge@mlan0)
ps -p "$pid" -o pid,args=
journalctl -u wifi_bridge@mlan0 -n 60 --no-pager
```

PASS:
- command line shows `/usr/local/scripts/wifi_bridge.sh mlan0`
- journal includes `Link incomplete ... waiting ...`

### LG2: Recover link and verify stable-start

```bash
ip link set eth0 up
sleep 10
pid=$(systemctl show -p MainPID --value wifi_bridge@mlan0)
ps -p "$pid" -o pid,args=
cat /run/wbridge.apply.json
journalctl -u wifi_bridge@mlan0 -n 80 --no-pager
```

PASS:
- command line switches to `/usr/local/bin/wifi-wbridge` or `/usr/local/bin/wifi-wbridge-tpacket`
- journal includes `Links are stable` then bridge start log

### LG3: Guard off compatibility path

```bash
sed -i 's/^WBRIDGE_LINK_GUARD=.*/WBRIDGE_LINK_GUARD=0/' /etc/default/wbridge
sed -i 's/^WBRIDGE_WAIT_READY_TIMEOUT_SEC=.*/WBRIDGE_WAIT_READY_TIMEOUT_SEC=5/' /etc/default/wbridge
ip link set eth0 down
systemctl restart wifi_bridge@mlan0
sleep 7
pid=$(systemctl show -p MainPID --value wifi_bridge@mlan0)
ps -p "$pid" -o pid,args=
journalctl -u wifi_bridge@mlan0 -n 80 --no-pager
```

PASS:
- journal contains timeout-continue message
- bridge binary started even though one side is still down

Restore guard-on profile after this case:

```bash
sed -i 's/^WBRIDGE_LINK_GUARD=.*/WBRIDGE_LINK_GUARD=1/' /etc/default/wbridge
sed -i 's/^WBRIDGE_WAIT_READY_TIMEOUT_SEC=.*/WBRIDGE_WAIT_READY_TIMEOUT_SEC=40/' /etc/default/wbridge
ip link set eth0 up
systemctl restart wifi_bridge@mlan0
```

### LG4/LG5: Runtime TX short-circuit under peer-down traffic

Run once with `WBRIDGE_ENGINE=pcap`, then again with `WBRIDGE_ENGINE=tpacket`.

1. Select engine and restart:

```bash
sed -i 's/^WBRIDGE_ENGINE=.*/WBRIDGE_ENGINE=pcap/' /etc/default/wbridge
systemctl restart wifi_bridge@mlan0
```

2. Collect baseline stats:

```bash
pid=$(systemctl show -p MainPID --value wifi_bridge@mlan0)
kill -USR1 "$pid"
sleep 1
journalctl -u wifi_bridge@mlan0 -n 80 --no-pager
```

3. Force peer link down while feeding traffic into the still-up side.
4. Collect stats again:

```bash
kill -USR1 "$pid"
sleep 1
journalctl -u wifi_bridge@mlan0 -n 120 --no-pager
```

PASS:
- drop counters increase during peer-down window
- no sustained flood of `pcap_inject failed` / `sendto failed` logs
- process remains healthy (no crash/restart loop)

### LG6: Restart stability check

```bash
systemctl show -p NRestarts wifi_bridge@mlan0
journalctl -u wifi_bridge@mlan0 -n 200 --no-pager | grep -E "Starting wbridge|Link incomplete|Links are stable"
```

PASS:
- `NRestarts` remains low and does not climb rapidly during short link flaps

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
