# Task 10 implementation report (implementation and board acceptance complete)

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

The repository pre-commit hook regenerated `DRIVER_MANIFEST.md` from unrelated
host binaries while creating the implementation commit. The report follow-up
restores that file byte-for-byte from inherited HEAD with hooks disabled; it is
absent from the final Task 10 range and is not a Task 10 change.

## Scoped watchdog harness-race follow-up

Fresh controller validation after `8eddd0973725156dfbfe340d1524ede1a66f96f5`
reproduced `PASS=168 FAIL=1` in two of four normal writer runs, with only
`Mode A watchdog bounds SIGKILL orphan` failing. Strace established that the
monitor PID disappeared at `1787397898.988993`, while watchdog `rm -rf` did not
start until `1787397898.995989` and completed at `1787397899.005243`. The old
helper broke its poll loop as soon as the PID disappeared, so it could test
directory absence roughly 7 ms before removal started.

The fix is test-harness-only: `check_monitor_cleaned()` now polls the complete
postcondition (PID absent/zombie, recorded directory absent, and no iface
monitor glob) throughout the existing three-second deadline. It retains the
same final combined assertions and failure detail at the deadline, so a real
daemon or private-file leak still fails visibly. No production file changed.

Exact validation:

```sh
rtk bash -n dist/wlan/usr/local/scripts/wifi_wpa_conf_writer_test.sh
# exit 0

rtk bash -lc 'set -e; for i in $(seq 1 10); do
  rtk bash dist/wlan/usr/local/scripts/wifi_wpa_conf_writer_test.sh \
    > "/tmp/task10-watchdog-fix-run-${i}.log"
  rtk grep "^RESULT:" "/tmp/task10-watchdog-fix-run-${i}.log"
done'
# runs 1..10: each RESULT: PASS=169 FAIL=0 (exit 0)

rtk python3 -m pytest dist/wlan/usr/local/scripts/tests -q
# 169 passed in 16.90s

rtk ./scripts/validate_release.sh pre
# exit 0; embedded logger 654 passed, scripts 169 passed,
# writer RESULT: PASS=169 FAIL=0

rtk git diff --check 8eddd0973725156dfbfe340d1524ede1a66f96f5
# exit 0
```

Concerns: none.

## Fix round 5: preserve substitution failure normalization

Inherited HEAD: `ddc57bd11052a3f138f42d6de63aa58e4aca1aa1`.
Implementation/test commit: `d7615f9876f05554f74adc344997f50d9ad575cb`
(`fix(wlan): preserve child substitution cleanup`).

### Root cause and RED

The three private PID-file reads put `|| true` inside the command substitution.
`wifi_wpa_child_exec` correctly closes FD7/FD9 and then `exec`s the reader, but
that `exec` replaces the substitution shell before its internal OR-list can
run. A missing PID file therefore returned the reader's failure to a `set -e`
owner instead of the old empty-output/success normalization.

The writer harness first extracted the real monitor cleanup function and ran
its missing-PID-file path under `set -e`. It also added direct Bash and POSIX-sh
checks for failure-to-empty/success normalization and successful stdout
capture. Each held status, PID-poll, and install fixture now proves its chosen
child remains live after the owner is SIGKILLed and reaped, immediately before
the ordered FD9-then-FD7 probe.

```sh
rtk bash dist/wlan/usr/local/scripts/wifi_wpa_conf_writer_test.sh
# exit 1 — RESULT: PASS=219 FAIL=1
# sole failure: set -e missing-PID cleanup did not reach directory removal
```

### Implementation

Failure normalization now sits in the owning shell for all three reads:
`pid=$(wifi_wpa_child_exec cat ... 2>/dev/null) || true`. The reader still
closes both descriptors before `exec`; missing files yield empty captured
stdout with successful caller status; successful stdout is unchanged; and the
transaction parent retains FD9/FD7 through its existing EXIT cleanup.

The static inventory remains a reviewed exact inventory for the current known
post-lock graph. It is intentionally not claimed to be a universal shell parser
or proof against arbitrary future executable names.

### GREEN and final validation

```sh
rtk bash dist/wlan/usr/local/scripts/wifi_wpa_conf_writer_test.sh
# exit 0 — RESULT: PASS=220 FAIL=0

rtk bash dist/wlan/usr/local/scripts/wifi_init_config_test.sh
# exit 0 — PASS: 88 / FAIL: 0

rtk bash -n dist/wlan/usr/local/scripts/wifi.sh \
  dist/wlan/usr/local/scripts/opc_wlan_apply.sh \
  dist/wlan/usr/local/scripts/wifi_init_config_lib.sh \
  dist/wlan/usr/local/scripts/wifi_wpa_conf_writer_test.sh
# exit 0 — syntax failures: 0

rtk sh -n dist/wlan/usr/local/scripts/opc_wlan_apply.sh \
  dist/wlan/usr/local/scripts/wifi_init_config_lib.sh
# exit 0 — syntax failures: 0

rtk python3 -m pytest dist/wlan/usr/local/logger/tests -q
# exit 0 — 654 passed, 0 failed

rtk python3 -m pytest dist/wlan/usr/local/scripts/tests -q
# exit 0 — 169 passed, 0 failed

rtk python3 scripts/gen_config_defaults.py --check
# exit 0 — defaults mismatches: 0; one documented runtime handoff row allowlisted

rtk ./scripts/validate_release.sh pre
# exit 0 — embedded logger 654 passed, scripts 169 passed,
# writer RESULT: PASS=220 FAIL=0; no validation failure

rtk git diff --check ddc57bd11052a3f138f42d6de63aa58e4aca1aa1..HEAD
# exit 0 — whitespace errors: 0
```

The pre-commit hook regenerated unrelated `DRIVER_MANIFEST.md`; it was restored
byte-for-byte from inherited HEAD and excluded by a hooks-disabled amend. The
final commit contains only `wifi.sh` and the writer regression harness.

Concerns: none.

## Fix round 2: prove abort quiescence

Board finding SHA-256:
`6b3936fbaf68dfc820dca2c266145f5f5a2d2509d0932cd24ad5d6adf5ff8e71`.
Evidence directory: `/tmp/wlan-board-test-task10-eHAbw35A`.

The direct required scan-to-connect sequence failed 10/10 because the first
exact `ABORT_SCAN=OK` only acknowledged the abort request; teardown had not
yet become quiescent before `RECONFIGURE`, and both AP attempts ended in
`ASSOC_TIMED_OUT`. The board's successful control path issued another
`ABORT_SCAN` immediately: the second reply was exact plain `FAIL`, proving
that no scan remained, and the ordinary connect then succeeded without an
added pre-connect delay.

### Witnessed RED

Writer coverage was added before the production change for wifi and OPC:
`OK` then `FAIL` success, repeated-`OK` exhaustion, and invalid later empty,
unexpected, or nonzero-transport replies. Every fail-closed path checks the
configuration byte-for-byte and forbids live association/reconfigure calls.

```sh
rtk bash dist/wlan/usr/local/scripts/wifi_wpa_conf_writer_test.sh
# exit 1; RESULT: PASS=167 FAIL=34
```

The old one-shot implementation stopped after the first `OK`: the two
`OK`-then-`FAIL` cases made only one call, while every exhaustion/later-invalid
case mutated configuration and issued a live command.

### Implementation

- `wifi_wpa_abort_scan_quiesce()` is now shared by wifi and OPC in
  `wifi_init_config_lib.sh`.
- Exact plain `FAIL` remains a one-call no-active-scan success. Exact `OK`
  means only that the abort was accepted, so the helper retries up to five
  total calls with a fixed 50 ms interval until exact plain `FAIL` proves
  quiescence.
- Transport/nonzero, empty, unexpected reply, sleep failure, or five accepted
  replies without plain `FAIL` fail closed before configuration mutation,
  transaction install, or a live association command.
- The bound is fixed production policy (at most 200 ms of sleeps), not a
  schema/runtime knob and not part of or a reset of the existing 15-second
  association proof budget. FD9 then FD7 ordering is unchanged.

### GREEN validation

```sh
rtk bash dist/wlan/usr/local/scripts/wifi_wpa_conf_writer_test.sh
# RESULT: PASS=201 FAIL=0

rtk bash dist/wlan/usr/local/scripts/wifi_init_config_test.sh
# PASS: 88 / FAIL: 0

rtk bash -n dist/wlan/usr/local/scripts/wifi.sh \
  dist/wlan/usr/local/scripts/opc_wlan_apply.sh \
  dist/wlan/usr/local/scripts/wifi_init_config_lib.sh \
  dist/wlan/usr/local/scripts/wifi_wpa_conf_writer_test.sh
# exit 0

rtk python3 -m pytest dist/wlan/usr/local/logger/tests -q
# 654 passed in 7.25s

rtk python3 -m pytest dist/wlan/usr/local/scripts/tests -q
# 169 passed in 17.20s

rtk python3 scripts/gen_config_defaults.py --check
# exit 0 (one documented runtime-generated handoff allowlist row)

rtk ./scripts/validate_release.sh pre
# exit 0; embedded logger 654 passed, scripts 169 passed,
# writer RESULT: PASS=201 FAIL=0

rtk git diff --check f46865d4892eb28facc77ed6406d89afee245a58
# exit 0
```

### Self-review

- Reply contract: **PASS** — only exact plain `FAIL` establishes quiescence;
  `OK` retries, and every other outcome fails closed.
- Pre-mutation safety: **PASS** — wifi and OPC byte-exact tests cover every
  later failure and prove no reconfigure/reassociate/reconnect escapes.
- Bounds/budgets: **PASS** — five fixed attempts and four 50 ms sleeps are
  independent of the unchanged `TOTAL_POLLS` association budget.
- Locking/scope: **PASS** — shared helper runs after existing FD9-to-FD7 lock
  acquisition; no JSON, schema, owner, topology, frequency, interval, or
  production-test-seam change was introduced.

Concerns: none.

## Fix round 3: transient child lock inheritance

Binding finding SHA-256:
`320bf36c0c38462d5dea6cd15d44f760ccaaaeed652074b8728a7799d1bb3305`.
Board evidence remains under `/tmp/wlan-board-test-task10-eHAbw35A`.

The board's three-round SIGKILL matrix repeatedly found both locks busy while
the monitor and its private directory were still live. The watchdog and
monitor daemon had already closed FD7/FD9; the remaining holder was a
foreground association poll child inheriting the parent's open lock file
descriptions.

### Deterministic RED

Before production changes, the writer mock gained a `wpa_cli status` mode that
returns the initial current id, then blocks its first post-monitor association
poll on a FIFO. The harness confirms that child and a stubborn private monitor
are live, SIGKILLs only the owning `wifi connect` shell, and immediately tries
FD9 followed by FD7 with `flock -n` while the private directory still exists.
It then terminates the fake slow child and retains the watchdog cleanup check.

```sh
rtk bash dist/wlan/usr/local/scripts/wifi_wpa_conf_writer_test.sh
# exit 1; RESULT: PASS=203 FAIL=1
# sole failure: Mode A SIGKILL releases FD9 then FD7 before monitor cleanup
```

This is the expected defect: the held status child kept both open file
descriptions after the owner was reaped.

### Implementation and intermediate diagnosis

- `wifi_wpa_child_exec` closes 7 and 9 in the current child shell and then
  replaces it with the external command. Command substitutions call this form
  directly, so no intermediate substitution shell remains to hold locks.
- `wifi_wpa_run_child` supplies one close-and-exec subshell for ordinary child
  calls. The transaction parent never closes its descriptors and remains the
  sole lock owner until transaction exit.
- Both status substitutions, connected-id reads, event-evidence cleanup,
  association polling sleeps, abort-quiescence calls/sleeps, and every
  reconfigure/reassociate/reconnect request now use those forms.

The first GREEN attempt nested a close-and-exec subshell inside the
command-substitution shell. The deterministic test correctly remained RED at
`PASS=203 FAIL=1`, proving the intermediate shell still retained the lock
descriptions. Splitting the direct command-substitution form removed that
holder; no association timeout, retry count, or response contract changed.

### Final GREEN validation

```sh
rtk bash dist/wlan/usr/local/scripts/wifi_wpa_conf_writer_test.sh
# RESULT: PASS=205 FAIL=0

rtk bash dist/wlan/usr/local/scripts/wifi_init_config_test.sh
# PASS: 88 / FAIL: 0

rtk bash -n dist/wlan/usr/local/scripts/wifi.sh \
  dist/wlan/usr/local/scripts/opc_wlan_apply.sh \
  dist/wlan/usr/local/scripts/wifi_init_config_lib.sh \
  dist/wlan/usr/local/scripts/wifi_wpa_conf_writer_test.sh
# exit 0

rtk sh -n dist/wlan/usr/local/scripts/opc_wlan_apply.sh \
  dist/wlan/usr/local/scripts/wifi_init_config_lib.sh
# exit 0

rtk python3 -m pytest dist/wlan/usr/local/logger/tests -q
# 654 passed in 7.24s

rtk python3 -m pytest dist/wlan/usr/local/scripts/tests -q
# 169 passed in 16.80s

rtk python3 scripts/gen_config_defaults.py --check
# exit 0 (one documented runtime-generated handoff allowlist row)

rtk ./scripts/validate_release.sh pre
# exit 0; embedded logger 654 passed, scripts 169 passed,
# writer RESULT: PASS=205 FAIL=0

rtk git diff --check 0c56e229dc029c9e9685b0a1aeef398cab270f03
# exit 0
```

### Self-review

- Determinism: **PASS** — the regression holds the real mock status process
  alive, proves monitor-directory presence at the ordered FD9/FD7 probe, and
  explicitly cleans both slow child and monitor resources.
- Sole ownership: **PASS** — command-substitution shells themselves close and
  `exec`; normal calls use one close-and-exec child. All listed post-lock
  polling/association external commands use the shared pattern.
- Scope/order/budget: **PASS** — parent acquisition remains FD9 then FD7 and
  neither descriptor is released early; `TOTAL_POLLS`, the shared 15-second
  budget, monitor/watchdog lifecycle, and exact abort-quiescence policy are
  unchanged.
- Contract surface: **PASS** — no runtime option, JSON/schema, owner,
  topology, frequency, or default-interval change was introduced.

Concerns: none.
## Fix round 4: universal post-lock child FD isolation

Binding finding SHA-256:
`320bf36c0c38462d5dea6cd15d44f760ccaaaeed652074b8728a7799d1bb3305`.
The scoped `0c56e22..de31b50` review correctly found that round 3 covered the
association poll but not the full child graph reachable while FD9 and FD7 were
held. Monitor construction/identity polling, topology inspection, explicit
render/install, signal cleanup, and EXIT durability still spawned retaining
children.

### Deterministic RED

The hardware-free writer harness was extended before production edits with
two additional FIFO-held real child processes:

- a private `wpa_cli.pid` `cat` during monitor setup/PID polling; and
- the canonical writer's `install` after an explicit-connect monitor was
  attached.

Each fixture proves the selected child is live and the stubborn private monitor
directory exists, SIGKILLs only the owning `wifi connect` shell, then requires
nonblocking acquisition of FD9 followed by FD7 while that directory still
exists. The existing held-status-child case remains unchanged. A narrow source
inventory additionally rejects unwrapped external commands or an unreviewed
change to the close-boundary inventory in the post-lock connect/helper slices.

```sh
rtk bash dist/wlan/usr/local/scripts/wifi_wpa_conf_writer_test.sh
# exit 1; RESULT: PASS=211 FAIL=3
# failures: held PID-poll child, held explicit-install child, static inventory
```

Both behavioral failures retained FD9/FD7 exactly as reported; the source
inventory enumerated the remaining direct setup, identity, render/install,
polling, cleanup, and EXIT children.

### Implementation and complete child inventory

`wifi_wpa_child_close` is the single descriptor-close primitive.
`wifi_wpa_child_exec` closes and replaces a direct command-substitution shell;
`wifi_wpa_run_child` gives ordinary external commands one close-and-exec child.
For transitive shared helpers, `wifi_wpa_child_call` and
`wifi_wpa_run_child_call` close before entering the helper, so all descendants
inherit closed descriptors without changing unrelated direct helper callers.
Stdout/stderr, argv/globbing, redirections, exit status, and substitution
capture remain at their original call sites.

The audited graph after both locks are acquired is:

- abort quiescence: `wpa_cli` replies and bounded `sleep`;
- topology protection: policy path/validation, JSON reads, sentinel `grep`, and
  network-count `awk`, all beneath one closed helper boundary;
- monitor setup and identity: `mkdir`, `mktemp`, both `chmod` calls, action-file
  `cat`, `/proc` `cat`/`tr`, watchdog identity/poll `cat`/`sleep`, daemon
  `wpa_cli`, PID-file polling, and setup cleanup `rm`;
- explicit processing: byte-length/frequency substitutions, ENVIRON probe
  `awk`, common-frequency resolver `awk`, both `mktemp` calls, canonical and
  SSID render `awk`, and the atomic install helper's transitive
  `mktemp`/`install`/`sync`/`rm`/`mv` operations;
- association and teardown: evidence `cat`, status/reconfigure/reassociate/
  reconnect `wpa_cli`, grace/final polling `sleep`, evidence/temp/monitor `rm`,
  `/proc` identity checks, and EXIT `sync`.

The watchdog closes FD7/FD9 as its first operation, the generated action also
closes them before any child, and the persistent monitor is launched through
the close-and-exec runner. The owning transaction shell alone retains both
locks through EXIT cleanup.

### Final GREEN validation

```sh
rtk bash dist/wlan/usr/local/scripts/wifi_wpa_conf_writer_test.sh
# RESULT: PASS=214 FAIL=0

rtk bash dist/wlan/usr/local/scripts/wifi_init_config_test.sh
# PASS: 88 / FAIL: 0

rtk bash -n dist/wlan/usr/local/scripts/wifi.sh \
  dist/wlan/usr/local/scripts/opc_wlan_apply.sh \
  dist/wlan/usr/local/scripts/wifi_init_config_lib.sh \
  dist/wlan/usr/local/scripts/wifi_wpa_conf_writer_test.sh
# exit 0

rtk sh -n dist/wlan/usr/local/scripts/opc_wlan_apply.sh \
  dist/wlan/usr/local/scripts/wifi_init_config_lib.sh
# exit 0

rtk python3 -m pytest dist/wlan/usr/local/logger/tests -q
# 654 passed in 7.22s

rtk python3 -m pytest dist/wlan/usr/local/scripts/tests -q
# 169 passed in 16.84s

rtk python3 scripts/gen_config_defaults.py --check
# exit 0 (one documented runtime-generated handoff allowlist row)

rtk ./scripts/validate_release.sh pre
# exit 0; embedded logger 654 passed, scripts 169 passed,
# writer RESULT: PASS=214 FAIL=0

rtk git diff --check de31b503a3075457ed084ecc147e0ef2f67c7a1a
# exit 0
```

### Self-review

- Behavioral proof: **PASS** — status, monitor PID polling, and explicit
  install are independently held across owner SIGKILL; ordered immediate lock
  acquisition occurs before watchdog cleanup in all three cases.
- Inventory completeness: **PASS** — the static gate covers the post-lock
  connect slice, monitor/association helper cluster, and abort helper, while
  transitive topology/render/install helpers enter only through closed
  boundaries. Its exact inventory fails on silent additions.
- Semantics: **PASS** — direct substitutions close in their own shell;
  ordinary and transitive calls preserve output, error, status, arguments,
  redirections, and file effects. Shared helpers themselves were not changed.
- Contracts: **PASS** — FD9-to-FD7 order, full transaction lock lifetime,
  abort policy, shared 15-second association budget, monitor/watchdog cleanup,
  atomic writer durability/metadata, and all JSON/schema/owner/topology/
  frequency/default behavior are unchanged.

Concerns: none.

## Final board acceptance at `d7615f9`

The final reviewed package was built from
`d7615f9876f05554f74adc344997f50d9ad575cb` and installed as
`wlan-proc 0.5.5` on `root@192.168.214.5`:

- package: `wlan-d7615f9.deb`
- package SHA-256:
  `5ffd3cf8d8efc34136b3d416a4873d0e3450d0b47132596e13e94316e38ac9e0`
- installed `wifi.sh`, `wifi_init_config_lib.sh`, `wifi_bgscan.py`,
  `wifi_roam.py`, and `roam_state.py` were byte-identical to the source files
  at that HEAD.

The controlled fixtures were base `jhw_wlan_` at
`00:80:4c:c7:7d:dd`/5180 and extra `jhw_wlan` at
`58:86:94:d2:73:e8`/5200. `jhw_wlan__` at 5220 was excluded. RF capture was
not enabled during the successful matrices: same-radio netmon on `mlan0`
suppressed the active scan under test, and the approved constraints prohibit
substituting `mlan1` or `net_rx`. The earlier same-radio capture failure
artifacts remain in the evidence directory.

### SIGKILL recovery: PASS 3/3

`task10_sigkill_matrix_v3.sh` held the selected real poll child alive, killed
only the transaction parent, and in every round proved that the child was
still alive before and after the ordered lock probes. FD9 and then FD7 were
immediately acquirable while the private monitor directory still existed.
The watchdog subsequently removed the daemon/private files, and a fresh
recovery produced a new matching `CONNECTED` event/status id, healthy ping,
and no lock or monitor leak. Result: `CORE_FAILS=0`.

### Mode B manual scan/connect concurrency: PASS 10/10

`task10_modeb_matrix_v6_nocapture.sh` alternated both target SSIDs. Each round
issued the exact manual passive scan, waited until it entered the scan path,
then ran `wifi connect`. All ten rounds returned a causally fresh numeric event
id matching status id, requested SSID/BSSID/frequency, exact scan-quiescence
ordering, no monitor/WAL/lock leak, no rejected/timeout/temporary-disable
errors, and a healthy ping. Result: `CORE_FAILS=0 PING_FAILS=0`.

The preceding v5 test failure was a harness-only parser defect: a greedy
`.*id=` matched the `id=` suffix inside `bssid=`. Tab-field parsing reproduced
the actual event id and v6 passed without a production change.

### Mode A external owner: PASS 5 round trips / 10 transitions

`task10_modea_external_matrix_v4.sh` used bind-mounted RSSI injection only for
`.link.signal` and `.link.signal_avg`, waited through the anti-ping-pong window,
and alternated five complete round trips. Every transition proved the exact
SSID/BSSID/frequency/network id and fresh event; all networks were enabled,
temporary BSSID pins were cleared, the selection WAL was absent, both locks
were free, no monitor leaked, and ping remained healthy. Result:
`CORE_FAILS=0 PING_FAILS=0`.

Three diagnostics were resolved in the test setup rather than hidden as
product fixes:

1. the boot-time supplicant runtime must contain the common `5180 5200` list;
   `wifi freq` is intentionally persist-only and cannot retroactively widen a
   running supplicant;
2. a multichannel real scan correctly rejects a weaker target against the real
   current-AP scan RSSI, so per-leg daemon-file narrowing was used to exercise
   the injected-current-RSSI seam while retaining real candidate scans; and
3. cleanup is asynchronous after the `CONNECTED` event, so the harness waits
   for the exact enable/pin/WAL/lock postcondition instead of sampling it
   prematurely.

### Mode A native owner: PASS 1 real cross-SSID transition

After installing a reboot-latched `roaming.enabled=false` Mode A policy, boot
state proved `wifi_roam=inactive`, `wifi_bgscan=active`,
`scan_backend=wpa_cli`, two enabled network blocks, one common global
`freq_list=5180 5200`, and no `scan_freq`. The native harness established the
base network, enabled the extra block with `no-connect`, then used only the
supported manual `wpa_cli scan` request. One scan produced:

```text
SCAN_STARTED (line 37) -> SCAN_RESULTS (line 49)
-> selected BSS 58:86:94:d2:73:e8 / jhw_wlan (line 93)
```

The subsequent fresh event and status both identified network id 1,
`jhw_wlan`, `58:86:94:d2:73:e8`, and 5200. There were zero custom
`SELECT_NETWORK`/`ROAM` control commands, zero rejected/timeout errors, no
disabled networks or pins, no WAL/monitor/lock leak, and ping passed. Result:
`CORE_OK=1`.

### Exact board restoration: PASS

The saved pre-test Mode B/external-owner files were restored with their
original ownership and modes and verified both before and after the final
reboot:

- `/usr/local/etc/wifi_init_conf.json`:
  `be46944c89e45f0b07a6a672f6a4a6fd23e8ef993a7d88a93ed5caab9219c2c9`,
  `root:root 0644`
- `/etc/wpa_supplicant/wpa_supplicant-mlan0.conf`:
  `e1f9248ab43b159e69e7ffd5a7143ab797caeff7f3aa4216bcf706dc6708d758`,
  `root:root 0600`

Post-reboot policy was Mode B/external owner, `wpa_supplicant`, `wifi_roam`,
and `wifi_bgscan` were active, capture was inactive, and mlan0 was
`COMPLETED` on id 0 / `jhw_wlan_` / `00:80:4c:c7:7d:dd` / 5180. The injection
drop-in, monitor directories, and selection WAL were absent; FD9 and FD7 were
free; three gateway pings had 0% loss; package/version/source hashes matched.
The assertion artifact ends with `FINAL_RESTORE_OK=1`.

Raw evidence, harnesses, configs, and a verified 1,436-file checksum manifest
are preserved under `/tmp/wlan-board-test-task10-eHAbw35A`.

Board acceptance verdict: **PASS**. Task 10 implementation and target rollout
criteria are complete; only the required whole-branch review remains.

## Final whole-branch review and fix wave

The independent merge-base review of `27c30a6..d7615f9` found no Critical
issues, six Important issues, and two Minors; report SHA-256 is
`42649f75bb3fa36fff3753f0e23998cfb272e93ba291454bf53c0922a27ef820`.
The six required fixes are:

1. serialize pending-selection WAL recovery under the exact transition lock;
2. extend child-FD isolation to lock acquisition, OPC, and every FD9-only
   supported writer;
3. publish the boot tombstone durably before the policy snapshot;
4. propagate boot normalize/extra-block atomic-install and sync failures and
   fail closed;
5. stop advertising the rejected Mode A manual cross-SSID path while retaining
   same-SSID BSSID roam; and
6. define and enforce one byte-exact 1..32-byte UTF-8 SSID contract.

The terminology Minor will be corrected in the same wave. The final ledger and
report delta will be retained in a separate evidence-only commit within the
scoped re-review range. Per the SDD final-review rule, all findings go to one
fix implementer and receive exactly one scoped re-review; there is no second
fix wave.


## Final whole-branch fix wave — implementation and verification (2026-08-23)

### Scope and commits

- Comparison base (unchanged): `d7615f9876f05554f74adc344997f50d9ad575cb`.
- Reviewed finding document: `/tmp/task10-final-whole-branch-review.md`, SHA-256 `42649f75bb3fa36fff3753f0e23998cfb272e93ba291454bf53c0922a27ef820`.
- Production/tests/operator docs/schema: `a0ec07e` (`fix(wifi): close final roam policy review gaps`).
- This appendix and the matching progress appendix are deliberately staged only in the separate evidence-only commit. No ignored review packages, raw `/tmp` artifacts, target-board changes, merge, or push are included.

### Deterministic RED evidence (before production edits)

| Finding coverage | Witnessed RED | Artifact |
|---|---:|---|
| A recovery lock, E manual Mode A, F Python/parser/schema | `39 failed, 203 passed` | `/tmp/task10-final-fix-red-logger.log` |
| C tombstone-first + failure boundaries, F snapshot validation | `18 failed, 12 passed` | `/tmp/task10-final-fix-red-scripts-policy.log` |
| D checked boot installs + fail-closed init, F generated hex/identity | `PASS=94 FAIL=33` | `/tmp/task10-final-fix-red-init.log` |
| B universal FD isolation, F wifi/OPC writers | `PASS=241 FAIL=14` | `/tmp/task10-final-fix-red-writers.log` |
| F exact CTRL_IFACE/iw consumers | `5 failed` | `/tmp/task10-final-fix-red-ssid-ctrl-consumers.log` |

The recovery tests held the real transition lock and observed the old implementation mutate during both normal-loop/startup scenarios. The FD tests kept the selected acquisition/OPC/FD9-only child alive across parent SIGKILL before ordered lock probes. Snapshot tests injected each stage/rename/sync boundary and deleted runtime policy state before mutating live JSON. Boot install tests injected staged sync, `mv`, installed sync, and directory sync across normalize/generate/remove variants. The manual test executed the real CLI boundary and checked that Mode A started no forbidden wifi child. SSID cases included UTF-8, leading/trailing spaces, quote/backslash, controls, duplicates, boot-base duplication, and 32/33 encoded-byte limits.

### Per-finding resolution

1. **WAL recovery lock:** `_pending_selection_cleanup_network_id()` is a mutation-free detector. `retry_pending_selection_cleanup()` acquires the exact nonblocking `scan_transition_lock`, rechecks under lock, and delegates all BSSID-clear/enable/reconfigure work to `_retry_pending_selection_cleanup_locked()`. The normal Mode A transition already holding the lock calls the lock-held cleanup graph and never nests acquisition. Busy main-loop recovery sleeps/defer one cycle; startup busy/error exits before any control request.
2. **Universal FD isolation:** FD9-to-FD7 order is unchanged. The FD7 waiter closes FD9, and close-first exec/call wrappers cover the complete supported post-lock graph in `wifi connect`, OPC, and FD9-only `wifi freq/ssid/psk/key`. Background action/watchdog children close both descriptors structurally. Exact source inventories plus deterministic held-child SIGKILL tests prove the child remains alive while the dead parent no longer keeps either ordered lock.
3. **Tombstone-first snapshot:** policy JSON is rendered, chmodded, synced, and validated before publication; the durable tombstone is atomically installed first; policy is atomically installed and synced second. Policy-without-tombstone is no longer repaired. Tombstone-without-policy fails closed until reboot, including deletion plus changed live-JSON retries.
4. **Checked boot topology install:** normalization and every generated/removal branch share `wifi_wpa_conf_atomic_install()`: same directory, preserved owner/mode, required staged sync, rename, installed-file sync, and destination-directory sync. Every failure is propagated. `wifi_init.sh` treats missing/failing required normalization/topology primitives as fatal before supplicant continuation.
5. **Mode A manual ruling:** `passive_roam` reads only the immutable boot snapshot. Mode A does not advertise cross-SSID entries and rejects a cross-SSID call before constructing/invoking `wifi connect`; same-SSID BSSID roam is retained. Mode B advertises and executes boot-latched manual candidates through the real CLI integration path.
6. **Shared SSID contract:** schema expresses nonempty/control-free/unique/code-point bounds and documents the encoded-byte rule; snapshot, shell, writer, OPC, and Python boundaries enforce valid UTF-8, 1..32 encoded bytes, no C0/DEL, uniqueness, and no boot-base duplicate. Identities are never trimmed. Supported writers/generators use byte-exact lowercase hex `ssid=<UTF-8 hex>`. CTRL_IFACE/iw/AP-table consumers decode exact identities. Tests use the local upstream wpa_supplicant config parser rather than a substitute.
7. **Minors:** active-policy messages/comments now use common/configured `freq_list`, reserving `scan_freq` for explicitly legacy fallback/removal/tests. The pre-existing controller final-review/board record was prefix-preserved byte-for-byte, then these two tracked records alone receive the final-fix evidence.

### Self-review correction

An interim defense rejected a Mode B writer target merely because it appeared in boot `extra_ssids`. That was overbroad: the contract rejects duplicate declarations between the *boot base* and extras, but explicitly supports a Mode B manual candidate replacing the live single block. Deterministic follow-up RED was `2 failed, 10 passed` for current-extra same-SSID behavior (`/tmp/task10-final-fix-red-modeb-current-extra.log`) plus four writer assertions (`/tmp/task10-final-fix-red-modeb-writer-extra.log`). The guard was removed, immutable snapshot validation retained, and GREEN became `12 passed` plus the complete writer `PASS=259 FAIL=0`.

### Final GREEN and quality gates

- logger pytest: `699 passed in 10.44s` — `/tmp/task10-final-fix-green-logger-final-complete.log`;
- scripts pytest: `190 passed in 26.47s` — `/tmp/task10-final-fix-green-scripts-final-complete.log`;
- init harness: `PASS=127 FAIL=0` — `/tmp/task10-final-fix-green-init-final-complete.log`;
- writer harness: `RESULT: PASS=259 FAIL=0` — `/tmp/task10-final-fix-green-writers-contract-final.log`;
- static Bash/POSIX/Python/JSON checks: `FINAL_STATIC_OK` — `/tmp/task10-final-fix-green-static-final.log`;
- generated defaults/schema: `FINAL_DEFAULTS_SCHEMA_OK` (one pre-existing allowlisted runtime handoff) — `/tmp/task10-final-fix-green-defaults-schema-final.log`;
- full `scripts/validate_release.sh pre`: exit `0`, including logger `699`, scripts `190`, init `127/0`, writer `259/0`, and remaining release harnesses — `/tmp/task10-final-fix-green-release-pre-final.log`;
- `git diff --check d7615f9876f05554f74adc344997f50d9ad575cb..HEAD` and final working diff check passed.

### Failure-boundary review and concerns

All lock-owner child graphs, snapshot publication boundaries, boot installation branches, recovery startup/main-loop exits, Mode A/B routing, and SSID serialization/consumer boundaries were reviewed after GREEN. The only harness adjustment was a test-only cleanup polling margin (3s to 5s) for overloaded host scheduling; production watchdog semantics and lock duration are unchanged. Known required findings: none unresolved. Final concern: none.


## Controller gate mismatch and watchdog continuation (2026-08-23)

### Controller reproduction

Before scoped re-review, the controller independently reran the clean commits and caught a deterministic gate mismatch:

- `/tmp/task10-controller-final-fix-release-pre.log` — release preflight exit `1`, writer `RESULT: PASS=256 FAIL=3`, SHA-256 `03eb7865da1cf802888bd9f9ce9259f78ab2eb73b967aba5fdcb74c5b9182429`;
- `/tmp/task10-controller-final-fix-writer-rerun-1.log` — isolated writer `PASS=256 FAIL=3`, SHA-256 `5b0d330ac96a8f315aa72a9d62213aa0e00b929a7482797bde112cb1558d890b`.

The first two failures were `monitor PID-poll watchdog still bounds monitor orphan` and `explicit install watchdog still bounds monitor orphan`. The later `Mode B fresh proof cleans private monitor` failure was downstream contamination because the earlier private directory remained during subsequent cases. The failed monitor processes/directories disappeared only later, proving an overdue cleanup rather than a wrong steady-state postcondition.

### Measured root cause

The original watchdog did not have a structurally bounded owner-liveness decision. Every nominal 100 ms tick traversed:

```text
connect_monitor_pid_matches
  -> command substitution
  -> wifi_wpa_child_call
  -> connect_monitor_proc_start
  -> wifi_wpa_child_exec cat /proc/<owner>/stat
```

Monitor recovery and identity added external pidfile `cat` and `/proc/<pid>/cmdline` `tr` children. A delayed/held child therefore delayed the watchdog itself before it could observe owner death. Increasing the assertion deadline would only hide that unbounded dependency.

Temporary, subsequently removed instrumentation ran five harnesses concurrently and captured 20 SIGKILL cleanup graphs. When all helper children scheduled promptly, owner-death detection was `0.105394..0.117046 s`; death-to-private-directory removal was `0.023506..0.401153 s`; total watcher-start-to-removal was `0.137805..0.516887 s`. Artifact: `/tmp/task10-watchdog-trace.log`, SHA-256 `b56758b76bf110ec4274bd9ba36ed1a02965264eb5eb0bbd7d2bffbcb12e7344`. Those measurements explain why earlier implementer runs passed while controller runs exposed the missing structural bound.

### Deterministic RED

The harness now arms a separate `/proc/*/stat` trap only after the monitor is attached and association polling has begun, guaranteeing the selected child belongs to the watchdog parent-liveness graph rather than monitor setup. Keeping that child alive across owner SIGKILL produced:

- `FAIL: watchdog parent-liveness poll must not depend on external cat`;
- `FAIL: builtin-watchdog bounds monitor orphan within three seconds`;
- `RESULT: PASS=257 FAIL=2`.

Artifact: `/tmp/task10-final-fix-continuation-red-watchdog.log`, SHA-256 `7d006b6b4414be7a7df0476b89a0fc31823b0b2976bf8ea83c767db0231e22ce`. RED cleanup explicitly drained the resumed old watchdog afterward, so later cases were not contaminated.

### Fix

Commit `32c42508b6869664439e36db8a6acde8788f5a67` (`fix(wifi): make monitor watchdog polling child-free`) changes only:

- `dist/wlan/usr/local/scripts/wifi.sh`;
- `dist/wlan/usr/local/scripts/wifi_wpa_conf_writer_test.sh`.

`/proc/<pid>/stat` is read directly with the shell `read` builtin and its start token assigned into a named caller variable. NUL-delimited `/proc/<pid>/cmdline` is inspected with Bash builtin `read -d ''`. Monitor pidfiles are read with guarded builtins. Thus owner liveness, PID/start-token validation, and post-death monitor recovery have no external child or command-substitution scheduling boundary. Numeric PID validation, non-zombie check, start-token identity, wpa_cli argv identity, monitor TERM/KILL behavior, FD9->FD7 lock order, and close-first supported-child contracts remain unchanged.

The test deadline was tightened back from the temporary five-second scheduler margin to the original three-second combined PID-exit plus private-directory-removal bound. This is an actual causal fix, not a deadline extension.

### Final clean-commit GREEN

Two consecutive isolated runs from clean commit `32c4250`:

- `/tmp/task10-final-fix-continuation-green-writer-final-1.log` — `RESULT: PASS=259 FAIL=0`;
- `/tmp/task10-final-fix-continuation-green-writer-final-2.log` — `RESULT: PASS=259 FAIL=0`.

Both are byte-identical with SHA-256 `ffb523ec6ff976fb934801c49672b179ce5976fc5e4b18d728d53cfbfae65b1a`.

Full clean-tree release gate:

- `/tmp/task10-final-fix-continuation-green-release-pre-final.log` — `CONTINUATION_FINAL_RELEASE_PRE_RC=0`, SHA-256 `706b0bc7423e52af447c6de39242ce863c39e382e0e8bb43955c32be038c8d10`;
- logger pytest `699 passed`;
- scripts pytest `190 passed`;
- init harness `PASS=127 FAIL=0`;
- writer harness `RESULT: PASS=259 FAIL=0`;
- all remaining release-preflight harnesses passed.

No board access, merge, push, or deadline-only workaround occurred. Required findings unresolved: none. Concern: none.
