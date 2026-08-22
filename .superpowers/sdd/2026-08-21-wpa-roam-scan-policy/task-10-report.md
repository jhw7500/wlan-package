# Task 10 implementation report (fix round 1 complete)

## Scope and stop condition

Only focused tests/harness assertions were changed. No production file was edited and no commit was made. The next step is GREEN implementation against the witnessed failures below.

## Changed test files

- `dist/wlan/usr/local/logger/tests/test_roam_state_per_iface.py`
  - asserts a real per-interface `fcntl.flock` context manager, `mlan0`/`mlan1` independence, durable lock path, and SIGKILL release.
- `dist/wlan/usr/local/logger/tests/test_bgscan_run_scan.py`
  - asserts bgscan lock contention runs no scan subprocess and advances the next attempt by the normal interval.
- `dist/wlan/usr/local/logger/tests/test_iw_scan_roam.py`
  - asserts external-owner scan and selected transition contention emit neither `iw` nor `wpa_cli roam` and report a neutral skip.
- `dist/wlan/usr/local/scripts/wifi_wpa_conf_writer_test.sh`
  - adds hardware-free FD7/FD9 ordering and 15-second bound checks; held-lock fail-closed tests; exact `ABORT_SCAN` reply matrix; Mode A/Mode B fresh-event tests; reconfigure grace/no-redundant-reassociate tests; and normal/TERM/SIGKILL lock-release assertions.

## RED commands and results

Full command output: `task-10-red.log`.

```sh
python3 -m pytest \\
  dist/wlan/usr/local/logger/tests/test_roam_state_per_iface.py \\
  dist/wlan/usr/local/logger/tests/test_bgscan_run_scan.py \\
  dist/wlan/usr/local/logger/tests/test_iw_scan_roam.py \\
  dist/wlan/usr/local/logger/tests/test_roam_select_network.py -q
# exit 1 — 4 failed, 73 passed

bash dist/wlan/usr/local/scripts/wifi_wpa_conf_writer_test.sh
# exit 1 — PASS=112 FAIL=31
```

## Failure attribution

- Python failures are intentional: `scan_transition_lock` does not exist; bgscan still executes a scan when the injected lock says busy; roaming still invokes `iw`/`wpa_cli roam` instead of returning a neutral deferred cycle. The deterministic clock and denied-lock seams run successfully; each assertion fails only after the absent behavior is exercised.
- Writer harness failures are intentional: scripts have no FD7 scan-transition lock or bounded wait, do not issue/fail-close on `ABORT_SCAN`, mutate/reconfigure while the lock is held or abort reply is invalid, accept stale Mode B/disconnected Mode A status, never attach an event monitor for explicit Mode B, and always reassociate after `RECONFIGURE` despite a supplied fresh proof.
- Existing non-Task-10 writer assertions remained passing in the same harness run. The added monitor, watchdog, and held-lock fixtures are deterministic; no test infrastructure failure was observed.

## GREEN handoff notes

The tests expect a `scan_transition_lock(iface, run_dir=...)` nonblocking Python context manager yielding a boolean and a shell `wifi_scan_transition_lock_acquire` helper using FD 7, defaulting to a 15-second bounded wait while honoring `WIFI_SCAN_TRANSITION_LOCK_TIMEOUT=0` in the harness. These are test seams/contracts, not production edits made in this phase.

## GREEN implementation and validation

Implemented the nonblocking Python `fcntl.flock` helper and daemon deferral; Mode A same/cross transition locking without wrapping the Mode B child `wifi connect`; FD9→FD7 shell ordering with a production 15-second bounded wait; strict pre-mutation `ABORT_SCAN`; and all-path fresh-event proof. Explicit Mode B arms before `RECONFIGURE`, accepts its fresh target proof during grace without reassociation, then clears/rearms for one fallback reassociation.

Monitor watchdog and daemon-launch subprocesses explicitly close FD7 and FD9 so SIGKILL cannot strand either lock; the SIGKILL harness probes the lock before watchdog cleanup.

GREEN results:

- Focused Task 10 Python: `78 passed` (exit 0).
- Writer harness: `PASS=143 FAIL=0` (exit 0).
- `wifi_init_config_test.sh`: `PASS: 88 FAIL: 0` (exit 0).
- Logger tests: `647 passed` (exit 0).
- Script tests: `169 passed` (exit 0).
- `bash -n`, defaults check, release-pre, and `git diff --check`: exit 0.

## Self-review

Reviewed the full BASE..working-tree diff. Confirmed no JSON/schema/owner/topology/frequency changes; lock ordering is FD9 then FD7; abort failures precede writer staging; Python contention is neutral; Mode B child writers own their own shell lock; and monitor children close inherited locks. The only host-test concession is a PermissionError fallback for an unprivileged `/run/wifi`; production uses the specified `/run/wifi` path.

Commit: `c5344a90569d506042130f82d81cdefc2e961012` (`feat(wlan): serialize scans and association transitions`).

## Review round 1 follow-up

Added a distinct `SCAN_TRANSITION_BUSY` result and main-loop handling so scan, same-SSID, and Mode A cross-SSID contention take one normal `CHECK_INTERVAL` path without failure/cooldown/settle side effects. No-argument connect now captures a current id before topology handling, and explicit proof requires a numeric fresh event id equal to status id. Lock timeout normalizes all nonzero/invalid overrides to 15 seconds (only test override `0` is retained).

Focused `test_iw_scan_roam.py`: `26 passed`; writer harness rerun: `PASS=143 FAIL=0`.

Review follow-up commit: `38de8ba0d3c2e209dff528e160ee08465591b14c`. Removed the `/tmp` fallback, added explicit test lock seams and visible PermissionError coverage, and restored iw TimeoutExpired to ordinary scan failure (`None`) rather than busy sentinel. Writer regression: `PASS=143 FAIL=0`.

## Fix round 1 completion

Inherited HEAD: `75112a58de1924a7a30fe4e6b52fa09cdc7a4354`.

Implementation/test commit: `db0950c1aaad4453f1fd90579af80b83441da067`
(`fix(wlan): preserve transition contention budget`).

### RED evidence added in this round

The direct production-main contention tests failed before the fix with the
expected gate mutation and missing same-SSID BUSY propagation:

```sh
python3 -m pytest \
  dist/wlan/usr/local/logger/tests/test_roam_backoff.py \
  dist/wlan/usr/local/logger/tests/test_roam_state_per_iface.py \
  dist/wlan/usr/local/logger/tests/test_iw_scan_roam.py -q
# exit 1 — 3 failed, 72 passed
```

The completed writer harness was also overlaid on inherited HEAD in a detached
temporary worktree. It produced `PASS=166 FAIL=3`: delayed grace incorrectly
issued one reassociation, grace plus fallback made 11 status polls instead of
the 10-poll total budget, and the inherited SIGKILL watchdog could lose cleanup
to job-control HUP. The temporary worktree was removed after capture.

### GREEN implementation

- Main-loop candidate/gate resets and the attempt log now occur only after a
  non-BUSY transition result. Scan, same-SSID, and cross-SSID BUSY each take
  exactly one `CHECK_INTERVAL` without active fallback, failure backoff,
  cooldown recording, or settle sleep. Same-SSID lock contention propagates
  the distinct BUSY sentinel; `TimeoutExpired` remains ordinary scan failure.
- `wifi connect` establishes `TOTAL_POLLS=CONNECT_TIMEOUT*10` once. Explicit
  reconfigure grace uses the bounded one-third share, delayed fresh proof is
  accepted, and fallback uses only the remaining counter after a clear/re-arm.
  No-argument reconnect skips grace and retains the full post-reassociate
  budget. Event evidence is read before every status snapshot.
- Duplicate Mode-A current-id capture and the no-op `if true` monitor wrapper
  were removed. The SIGKILL watchdog ignores HUP and tightly bounds orphan
  daemon termination while retaining PID/start-time identity checks.
- Writer coverage grew from 143 to 169 passes, including delayed grace,
  combined-poll cap, remaining-budget fallback proof, id 0→1 rejection,
  missing/nonnumeric status ids, absent event, and the complete FD7 timeout
  normalization matrix.

### Final validation evidence

```sh
python3 -m pytest \
  dist/wlan/usr/local/logger/tests/test_roam_state_per_iface.py \
  dist/wlan/usr/local/logger/tests/test_bgscan_run_scan.py \
  dist/wlan/usr/local/logger/tests/test_iw_scan_roam.py \
  dist/wlan/usr/local/logger/tests/test_roam_select_network.py \
  dist/wlan/usr/local/logger/tests/test_roam_backoff.py -q
# 112 passed

bash dist/wlan/usr/local/scripts/wifi_wpa_conf_writer_test.sh
# RESULT: PASS=169 FAIL=0

bash dist/wlan/usr/local/scripts/wifi_init_config_test.sh
# PASS: 88 / FAIL: 0

python3 -m pytest dist/wlan/usr/local/logger/tests -q
# 654 passed

python3 -m pytest dist/wlan/usr/local/scripts/tests -q
# 169 passed

bash -n dist/wlan/usr/local/scripts/wifi.sh \
  dist/wlan/usr/local/scripts/opc_wlan_apply.sh \
  dist/wlan/usr/local/scripts/wifi_init_config_lib.sh \
  dist/wlan/usr/local/scripts/wifi_wpa_conf_writer_test.sh
# exit 0

python3 scripts/gen_config_defaults.py --check
# exit 0 (one documented runtime-generated handoff allowlist row)

./scripts/validate_release.sh pre
# exit 0; embedded logger 654 passed, scripts 169 passed, writer PASS=169 FAIL=0

git diff --check 75112a58de1924a7a30fe4e6b52fa09cdc7a4354
# exit 0
```

### Self-review verdict

- Daemon deferral: **PASS** — direct `main()` tests cover staged scan,
  same-SSID, and cross-SSID BUSY without scheduler/gate/cooldown side effects.
- Lock ordering/release: **PASS** — FD9→FD7 remains fixed; normal, TERM, and
  SIGKILL paths reacquire immediately and the watchdog removes private state.
- Pre-mutation abort failure: **PASS** — exact `OK`/plain `FAIL` acceptance and
  all fail-closed replies remain before conf/transaction mutation.
- Causally fresh association proof: **PASS** — numeric event id precedes a
  matching subsequent status id, plus explicit SSID/frequency matching.
- Redundant reassociate avoidance: **PASS** — delayed reconfigure grace proof
  returns with zero reassociate/reconnect; fallback issues exactly one accepted
  reassociate and reconnect only on rejection.
- Owner/topology/frequency/default contracts: **PASS** — no JSON/schema, owner,
  topology, canonical frequency, or default interval changes are present.

The obsolete `/tmp` host-test concession is explicitly superseded: production
attempts only the requested `/run/wifi` namespace and propagates both mkdir and
open permission errors. Host tests use explicit temporary `run_dir` seams;
there is no alternate production namespace. Concerns: none.
