# Reboot Policy / Reboot Loop Prevention Plan

## Goal
Reduce device reboot loops by ensuring there is a single authority for reboots and by removing systemd-triggered machine reboots.

## Current Problem (Observed)
- `wifi_bridge@.service` can reboot the whole device via `StartLimitAction=reboot` when the service repeatedly fails.
- Multiple scripts can independently reboot the device (`wifi_checker.sh`, `arping.sh`, `wifi_logger_temp.sh`, `switchd.sh`).
- Some scripts have their own loop detection, but it is local and does not prevent concurrent or cross-trigger reboot storms.

## Scope
In-scope:
- Remove `StartLimitAction=reboot` from `dist/wlan/etc/systemd/system/wifi_bridge@.service`.
- Introduce a single reboot policy entrypoint script: `/usr/local/scripts/wlan_reboot_policy.sh`.
- Replace direct `reboot` calls in the following scripts with policy invocation:
  - `dist/wlan/usr/local/scripts/wifi_checker.sh`
  - `dist/wlan/usr/local/scripts/arping.sh`
  - `dist/wlan/usr/local/scripts/wifi_logger_temp.sh`
  - `dist/wlan/usr/local/scripts/switchd.sh`

Out-of-scope (for this phase):
- Refactoring detection logic (e.g., how instability is detected) beyond replacing reboot execution.
- Adding new dependencies or services.

## Policy Rules (Single Authority)
`/usr/local/scripts/wlan_reboot_policy.sh` is the only place allowed to execute a reboot.

Default gating (tunable via env vars):
- MIN_UPTIME_SEC: refuse reboot if uptime is below this threshold (default: 120s).
- REBOOT_COOLDOWN_SEC: if reboot was requested recently, treat as a repeated attempt (default: 300s).
- MAX_REBOOT_COUNT: after this many attempts within the cooldown window, refuse further reboots (default: 3).

Concurrency control:
- Use a lock directory under `/run/cantops/wlan-policy/` to dedupe concurrent reboot triggers.

Special cases:
- Manual user long-press reboot uses `--force` (bypasses gating).
- Over-temperature: stop wifi/bridge services, cooldown, then reboot via `--force` once temperature recovers.

State storage:
- Store reboot attempt counters under `/var/log/cantops/` to match existing conventions.

## Verification Checklist
1) systemd
- `systemctl daemon-reload`
- Confirm `wifi_bridge@.service` no longer contains `StartLimitAction=reboot`.

2) Reboot triggers
- Grep `dist/wlan/usr/local/scripts/*.sh` to ensure no direct `reboot` remains (except inside policy script).
- Trigger paths:
  - wifi_checker fatal path -> policy called and logs reason
  - arping unrecoverable path -> policy called
  - overtemp path -> policy called
  - switchd long press -> policy called with --force

3) Loop prevention
- Repeated triggers within 5 minutes should stop rebooting after MAX_REBOOT_COUNT and log refusal.

## Rollback
- Revert the service file change and replace policy invocations with direct `reboot` (or revert commit range).
