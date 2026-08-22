# SDD ledger — plan: docs/superpowers/plans/2026-08-21-wpa-roam-scan-policy.md

## Recovery checkpoint

- Worktree: `/home/jhw/ai/opencode/projects/wlan-package/.worktrees/wpa-roam-scan-policy`
- Branch: `feat/wpa-roam-scan-policy`
- Recovered HEAD before Task 8: `90097ec7763ff9c1fac699858e6176c4df0c8a6a`
- Binding spec: `docs/superpowers/specs/2026-08-21-wpa-roam-scan-policy-design.md` (Approved 2026-08-21)
- Primary-checkout constraint: never modify the user's existing `dist/wlan/opt/wlan/bin/README_MLAN` change.

## Plan preflight scan

| Tasks / surface | Producer → consumer or internal check | Result |
|---|---|---|
| Task 1 internal | resolver precedence, renderer, normalization tests, commit files | Consistent; renderer is the shared pure boundary. |
| Task 2 internal | Task 1 renderer → boot normalization/templates | Consistent with canonical global plus derived per-block `freq_list`. |
| Task 3 internal | owner latch → one scan backend and command grammar tests | Consistent; no cross-backend fallback. |
| Task 4 internal | pin/select/confirm/cleanup tests → external Mode A transition | Conflict found: historical BSSID clear token `any` is not accepted by the deployed control interface. Amended to all-zero address; see Ruling 1. |
| Task 5 internal | runtime writer tests → `wifi.sh`/OPC implementation | Conflict found: original Mode A reconnect used `select_network` plus process-local cleanup even though the binding spec only requires no false success. Amended; see Ruling 2. |
| Task 6 internal | owner service policy → schema/templates/operator docs | Consistent; built-in per-network `bgscan=` remains unsupported. |
| Task 7 internal | focused/full gates → invariant audit/review | Follow-up findings exist; completion workflow is deferred to Task 8. |
| Task 8 internal | RED reconnect/guidance/doc tests → minimal runtime/doc changes | Consistent after Rulings 2–4; built-in `bgscan=` enforcement is explicitly excluded. |
| Task 10 internal | live-operation lock/abort/fresh proof tests → daemon and writer changes | Consistent after the new scan-transition section; the controlled manual native scan is deliberately outside the package lock and is quiesced by `ABORT_SCAN`. |
| Tasks 1 → 2 | renderer/library → boot writer/templates | Compatible; Task 2 consumes Task 1's canonical output. |
| Tasks 1 → 5 | renderer/library → runtime writers | Compatible; runtime writers must reuse the same transform. |
| Tasks 1 → 7 | canonical invariants → integration audit | Compatible; Task 7 directly audits Task 1 output. |
| Tasks 2 → 3 | normalized common frequencies → background-scan parser | Compatible; global list is the requester source. |
| Tasks 2 → 4 | normalized common frequencies → roam parser | Compatible; migration-only block fallback remains bounded. |
| Tasks 2 → 5 | packaged/boot layout → runtime write preservation | Compatible; Task 5 preserves rather than invents layout. |
| Tasks 2 → 7 | boot/templates → integration audit | Compatible. |
| Tasks 3 ↔ 4 | owner/backend selection ↔ transition owner | Compatible; iw scans feed external owner, wpa_cli scans permit native selection. |
| Tasks 3 → 6 | latched owner/backend → service and documentation contract | Compatible; one boot decision drives both. |
| Tasks 3 → 7 | backend grammar/behavior → integration audit | Compatible. |
| Tasks 4 → 7 | exact target and cleanup → integration audit | Compatible after Ruling 1. |
| Tasks 5 → 6 | runtime behavior → published configuration contract | Compatible, except raw `select_network` guidance bypassed the supported boundary; Task 8 removes it. |
| Tasks 5 → 7 | writer guarantees → integration audit | Compatible after Task 8 remediation. |
| Tasks 5 ↔ 8 | `wifi.sh`, OPC, real-CLI harness | Intentional follow-up: replace temporary selection mutation with owner-neutral reassociation and exact-id proof. |
| Tasks 6 → 7 | service/docs contract → integration audit | Compatible; docs gap for native scan grammar is assigned to Task 8. |
| Tasks 6 ↔ 8 | configuration guide and unsupported boundary | Intentional follow-up: document both requesters without adding built-in `bgscan=` enforcement. |
| Tasks 7 → 8 | branch review → remediation | Compatible after deferring Task 7's finishing step until Task 8 and repeated review complete. |
| Tasks 3 ↔ 10 | fixed scan backend/request grammar ↔ scan-transition serialization | Compatible; each daemon keeps its latched backend, acquires one nonblocking per-interface lock, and never falls back on contention. |
| Tasks 4 ↔ 10 | external exact-target transition/cleanup ↔ transition serialization | Compatible; the lock surrounds the live transition but never replaces BSSID pin, exact status proof, or WAL cleanup. |
| Tasks 5/9 ↔ 10 | conf writer lock/durable install/fresh monitor → live-operation lock and generalized event proof | Compatible with fixed order FD9 then FD7; lock/abort failure occurs before mutation, and explicit reconfigure/reassociate phases share the existing association budget. |

## Rulings

- Ruling: Clear a temporary wpa network BSSID constraint with `00:00:00:00:00:00`, not `any` — direct deployed control-interface/board evidence showed `any` is rejected, and HEAD `90097ec` already implements/tests the accepted token — cost if wrong: cleanup can fail and leave the target network pinned.
- Ruling: For Mode A no-argument reconnect, use `REASSOCIATE` and prove the initially current network id instead of using `select_network` — the approved spec binds success to avoiding unintended transitions, while removing enable-state mutation eliminates a SIGKILL cleanup hole — cost if wrong: native selection may complete on another enabled block, so the CLI times out rather than forcing the original id.
- Ruling: Do not add fail-close enforcement for built-in per-network `bgscan=` — the approved spec explicitly places that configuration out of scope; only make the unsupported boundary clear — cost if wrong: an externally injected unsupported setting remains warning-only and can compete with package scan policy.
- Ruling: Task 8 supersedes Task 7's branch-finishing step until its task review and the repeated whole-branch review finish — post-review findings mean the prior completion claim is withdrawn — cost if wrong: integration is delayed by one remediation/review cycle.
- Ruling: Treat exact plain `FAIL` from `ABORT_SCAN` as the deployed no-scan quiescent result, while rejecting transport failure, nonzero status, empty output, and every other response — direct board evidence distinguishes active-scan `OK` from idle `FAIL`, but the control protocol does not provide a richer idle code — cost if wrong: a future supplicant that reuses plain `FAIL` for a real abort failure could let a writer continue despite an active scan.
- Ruling: Keep disconnected no-id `wifi connect` as broad automatic recovery, but require the fresh event id and subsequent `COMPLETED` id to match — this preserves the chosen automatic-recovery policy without accepting stale status — cost if wrong: recovery may connect a different enabled Mode A block when no pre-request current id exists.
- Ruling: An explicit `RECONFIGURE` gets only a short grace slice and any forced reassociation uses the remainder of the same 15-second association budget — this avoids normal-path duplicate association while retaining bounded fallback — cost if wrong: unusually slow reconfigure-only associations may receive a redundant forced request after the grace slice.
- Ruling: In the watchdog harness, poll PID termination, recorded-directory removal, and iface monitor-glob removal as one combined postcondition through the existing three-second deadline; PID exit alone is not cleanup completion because strace proved `rm -rf` starts milliseconds later — cost if wrong: each genuine cleanup failure can consume the full three-second test deadline before failing, but the unchanged final combined assertions still expose every daemon or private-file leak rather than accepting it.

## Recovered completed work

- Task 1: complete (commit `2ee50d4`, recovered from git history)
- Task 2: complete (commit `2ee50d4`, recovered from git history; boot/templates were batched with Task 1)
- Task 3: complete (commit `9e431c1`, recovered from git history)
- Task 4: complete (commits `51989ed` and cleanup hardening through `90097ec`, recovered from git history)
- Task 5: complete (commit `34eb35d` plus durability hardening commits, recovered from git history)
- Task 6: complete (commit `aebb107` plus boot-policy hardening commits, recovered from git history)
- Task 7: complete (integration/review evidence through `90097ec`; review-driven remediation moved to Task 8 and branch finishing deferred)

## Deferred target evidence (not completion claims)

- Mode B external owner: same-SSID roaming observed for 5 round trips / 10 transitions on the replacement AP.
- Mode B native owner: owner exclusivity and wpa_cli scan requester observed; automatic BSSID handoff not yet directly observed.
- Mode A external/native owner: structural, writer, and cleanup recovery evidence exists; real cross-SSID transition still needs a second SSID with usable shared credentials.

## Active task

- Task 8: implementation report recorded at `task-8-report.md`; witnessed RED
  `PASS=62 FAIL=8`, GREEN `PASS=70 FAIL=0`, corrected focused pytest `95 passed`,
  and release-pre gate reported passing.
- Task 8: pre-review correction — removed pre-commit-hook-generated
  `DRIVER_MANIFEST.md` from the task commit and reverified the four-file range.
- Task 8: complete (commits `90097ec..fe0c904`, task review clean: spec compliant,
  quality approved, zero Critical/Important/Minor findings).
- Plan correction: Task 8's defaults consistency pytest path is
  `dist/wlan/usr/local/logger/tests/test_defaults_template_consistency.py`;
  the originally written `scripts/tests` path does not exist.
- Fresh controller validation at `fe0c904`: focused writer harness
  `PASS=70 FAIL=0`; task-focused pytest `95 passed`; release-pre logger
  `642 passed`; script pytest `169 passed`; registered shell suites, syntax,
  generated-defaults, diff check, and clean worktree all exited zero.

## Final whole-branch review at `fe0c904`

- Important: Mode A reconnect can accept capture-time/stale `COMPLETED` before
  the asynchronous reassociation has produced a fresh connection epoch.
- Important: `wifi.sh:safe_install_sync` masks paired targeted/global staging
  and installed-directory sync failure despite the approved fatal-durability
  contract.
- Minor: extra-SSID block atomic replacements preserve mode but not UID/GID.
- Board design probe: deployed `wpa_cli` supports `-a`, `-B`, and `-P`; with an
  action monitor attached first, `REASSOCIATE` emitted fresh `CONNECTED id=0`
  about 0.33 seconds after the request while ending `COMPLETED id=0`.

- Ruling: Require a fresh post-request `CONNECTED` event id plus matching
  `COMPLETED` status for Mode A capture-id reconnect — direct source and board
  evidence show plain status can be stale while the action interface provides
  an epoch signal without mutating network state — cost if wrong: a target
  whose supplicant never emits the event will return a reconnect timeout rather
  than claim success.
- Ruling: Accept the durability finding and make paired targeted/global sync
  failure fatal exactly as the approved spec already requires — cost if wrong:
  transient sync implementations that reject both forms will now fail the CLI
  instead of proceeding best-effort.
- Ruling: Include ownership preservation in the single final fix wave — the
  approved transform says permission-preserving and the canonical path already
  attempts ownership preservation — cost if wrong: an exotic target lacking
  working `chown --reference` will fail the replacement; the deployed board
  advertises support for the option.

## Active final fix

- Task 9: implementation report recorded at `task-9-report.md`; witnessed
  writer RED `PASS=86 FAIL=14`, init RED `PASS=86 FAIL=1`, then writer GREEN
  `PASS=100 FAIL=0`, init GREEN `PASS=88 FAIL=0`, focused pytest `95 passed`,
  and release-pre gate reported passing.
- Task 9: complete (commits `fe0c904..f94dd2c`, independent scoped re-review:
  reconnect freshness ADDRESSED, durability ADDRESSED, ownership ADDRESSED,
  no new Critical/Important breakage).

## Active Task 10

- BASE: `f94dd2c39dcd0b0fbdb740185cf6968873d76690`
- Root-cause evidence: `/tmp/wlan-board-test-f94dd2c-20260822-181903/`
  (`mode-a-native-multi-rootcause-events.txt` and
  `scan-abort-reassociate-probe-3rounds.txt`).
- Board invariant: retain `bgscan.interval=60`; induce concurrency with the
  explicit manual `wpa_cli ... scan ... passive=1` request.
- AP fixture update: base `jhw_wlan_`/`00:80:4c:c7:7d:dd`/5180 and renamed
  extra `jhw_wlan`/`58:86:94:d2:73:e8`/5200; ignore `jhw_wlan__` and stale
  cache entries.
- Task 10: preflight complete; implementation not yet started. RED evidence
  must be recorded before production edits.
- Task 10: controller-witnessed RED at BASE `f94dd2c`: focused Python
  `4 failed, 73 passed` (exit 1) and writer harness `PASS=112 FAIL=31`
  (exit 1). Evidence:
  `task-10-controller-red-pytest.log` and
  `task-10-controller-red-writer.log`. Production files were unchanged;
  `git diff --name-only` contained exactly four test files.
- Task 10 board evidence directory reserved:
  `/tmp/wlan-board-test-task10-eHAbw35A`. Exact pre-test Mode B/external
  backups are saved there with SHA-256
  `be46944c...9219c2c9` (JSON) and `e1f9248a...708d758` (supplicant conf).
- Task 10 implementation commit: `c5344a90569d506042130f82d81cdefc2e961012`.
  Fresh controller GREEN at that HEAD: writer `PASS=143 FAIL=0`, init
  `PASS=88 FAIL=0`, focused Python `78 passed`, all logger `647 passed`,
  script pytest `169 passed`, shell syntax/defaults/release-pre/range
  `git diff --check` all exit 0. Independent task review is in progress.
- Task 10 independent review at `c5344a9`: spec FAIL / quality FAIL. Open
  Important findings: daemon contention is not scheduler-neutral; Mode B
  no-arg does not capture current id; explicit target can accept empty event
  and status ids; reconfigure grace is outside the single timeout budget;
  Python PermissionError changes lock namespace. Minor: timeout override is
  not capped and coverage misses the main scheduling/causality cases. Fix
  round 1/5 started from `c5344a9`.
- Task 10 fix round 1 intermediate commits: `9e690ad`, `38de8ba`, and
  `75112a5`. They address sentinel propagation, numeric-id checks, exact lock
  namespace failure, and staged home-scan sentinel ordering, but the same
  implementer returned three consecutive `DONE_WITH_CONCERNS` reports with
  the shared association budget and direct main-loop scheduler regressions
  still open. No scoped re-review was dispatched, so this remains round 1.
- Ruling: treat the repeated incomplete correctness reports as an effective
  implementation blocker and transfer the still-open fix round to a fresh,
  more capable implementer rather than submitting knowingly incomplete code
  to review — cost if wrong: the handoff spends an extra implementation seat
  and context rebuild, but preserves the review gate and does not discard any
  committed work.
- Task 10 scoped harness follow-up after `8eddd09`: controller validation saw
  two of four writer runs fail only the SIGKILL orphan assertion. Strace proved
  the PID became absent about 7 ms before watchdog directory removal began.
  `check_monitor_cleaned()` now polls the complete postcondition to the same
  three-second deadline; ten consecutive writer runs each passed `169/0`,
  scripts pytest passed `169`, and release-pre passed with embedded writer
  `169/0`. Production code is unchanged.
- Task 10 fix round 2 board finding: direct scan-to-connect failed 10/10 after
  first `ABORT_SCAN=OK`; the successful control path immediately issued a
  second abort, received exact plain `FAIL`, and then connected. Writer RED was
  `PASS=167 FAIL=34`. Shared bounded quiescence polling for wifi and OPC is now
  GREEN at writer `PASS=201 FAIL=0`, init `88/0`, logger pytest `654`, scripts
  pytest `169`, syntax/defaults/release-pre/range diff-check exit 0.
- Ruling: Treat exact `ABORT_SCAN=OK` only as an accepted request and require a
  bounded retry to exact plain `FAIL` before mutation; use five fixed attempts
  50 ms apart for wifi and OPC — board evidence proves the immediate second
  abort returns `FAIL` and restores connect success — cost if wrong: on a
  slower target the writer fails closed after about 200 ms instead of applying,
  requiring operator retry while never racing mutation into an active scan.
- Task 10 fix round 3: board evidence identified a foreground association poll
  child retaining FD9/FD7 after its `wifi connect` parent was SIGKILLed. The
  deterministic held-status-child RED was `PASS=203 FAIL=1`; direct child-shell
  FD closure is now GREEN at writer `PASS=205 FAIL=0`, init `88/0`, logger
  pytest `654`, scripts pytest `169`, and syntax/defaults/release-pre/range
  diff-check exit 0.
- Ruling: Require immediate ordered FD9-then-FD7 reacquisition before watchdog
  cleanup and make every transient connect polling/association child explicitly
  close inherited descriptors while the parent remains sole lock owner — cost
  if wrong: an undocumented external helper that intentionally depended on
  private FDs 7 or 9 would lose that accidental channel, but supported command
  inputs, outputs, retry timing, and transaction lock scope remain unchanged.
