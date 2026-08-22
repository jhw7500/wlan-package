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

## Target evidence status

- Mode B external owner: same-SSID roaming observed for 5 round trips / 10 transitions on the replacement AP.
- Mode B native owner: owner exclusivity and wpa_cli scan requester observed; automatic BSSID handoff not yet directly observed.
- Mode A external owner: superseded by Task 10 board acceptance — five round
  trips/ten real cross-SSID transitions passed against the two intended APs.
- Mode A native owner: superseded by Task 10 board acceptance — one real
  cross-SSID transition followed the supported wpa_cli scan path with no custom
  selection command.

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
- Task 10 fix round 4: the scoped review found round 3 incomplete outside the
  association poll. Deterministic held monitor-PID-poll and explicit-install
  children plus the preserved held-status child were RED at writer
  `PASS=211 FAIL=3`; universal close-first boundaries and a static exact child
  inventory are GREEN at writer `PASS=214 FAIL=0`, init `88/0`, logger pytest
  `654`, scripts pytest `169`, and syntax/defaults/release-pre/exact-base
  diff-check exit 0.
- Ruling: Require universal post-lock child FD isolation: direct substitutions
  close in their own shell, ordinary external calls close-and-exec once, and
  transitive helper calls cross a close-first boundary while the transaction
  parent alone retains FD9/FD7 — cost if wrong: a hidden connect helper that
  intentionally consumes private descriptors 7 or 9 would lose an unsupported
  channel, whereas failing to isolate any child can strand both locks after
  SIGKILL and block immediate recovery for that child's lifetime.
- Task 10: fix round 4/5 (0 addressed, 1 open — command-substitution `exec`
  bypasses the inner `|| true`, regressing missing-PID cleanup semantics;
  commits de31b50..ddc57bd).
- Task 10: minor (deferred): the held-child fixtures prove pre-kill liveness
  but must also prove the child remains alive after parent SIGKILL and before
  the lock probes; carried into fix round 5 because it strengthens the board
  finding's causal evidence.
- Task 10: minor (deferred): the static post-lock inventory is exact for the
  known command graph but cannot detect an arbitrary future executable name;
  final review must treat it as a reviewed inventory, not a shell-parser proof.
- Task 10: fix round 5/5 (2 addressed, 0 open — PID-read substitution
  normalization and post-SIGKILL child-liveness proof; commits
  ddc57bd..d7615f9). Scoped re-review CLEAN. Fresh controller gates at
  `d7615f9`: writer `220/0`, init `88/0`, logger pytest `654`, scripts pytest
  `169`, Bash/POSIX syntax, defaults, release-pre, and exact-range diff check
  all exit 0. The subsequently completed board acceptance is recorded below.
- Task 10 final package: built from `d7615f9` as `wlan-proc 0.5.5`, package
  SHA-256 `5ffd3cf8...38ac9e0`; five installed runtime files matched source.
- Task 10 controlled board matrix: SIGKILL recovery passed 3/3 with the held
  child live through ordered immediate FD9/FD7 probes; Mode B manual
  scan/connect concurrency passed 10/10 with fresh exact target proof and no
  rejected/timeout/temporary-disable errors, leak, or ping failure.
- Task 10 Mode A/external board matrix: five round trips/ten real cross-SSID
  transitions passed with exact SSID/BSSID/frequency/id, fresh event, cleared
  pin, all networks enabled, no WAL/monitor/lock leak, and healthy ping.
- Task 10 Mode A/native board matrix: reboot-latched native ownership proved
  `wifi_roam=inactive`, wpa_cli scan backend, common `5180 5200` policy, and one
  real cross-SSID transition ordered `SCAN_STARTED -> SCAN_RESULTS -> selected
  BSS`, with zero custom `SELECT_NETWORK`/`ROAM` commands and `CORE_OK=1`.
- Task 10 harness diagnostics: Mode B v5 failed only because greedy `.*id=`
  parsed the suffix of `bssid=`; external-owner v1 exposed the intentional
  persist-only/runtime-frequency distinction, v2 correctly rejected a weaker
  target against real current-AP scan RSSI, and v3 sampled asynchronous cleanup
  too early. Corrected causal harnesses passed without hiding these behaviors.
- Task 10 exact board restoration: saved Mode B/external JSON and supplicant
  conf were restored byte-for-byte before and after reboot, services/status,
  package, locks, monitor/WAL absence, and ping passed; final assertion is
  `FINAL_RESTORE_OK=1`. Evidence and verified 1,436-file SHA manifest:
  `/tmp/wlan-board-test-task10-eHAbw35A`.
- Task 10: complete (commits `f94dd2c..d7615f9`, scoped fix re-review CLEAN,
  local gates and all required board acceptance/restore gates PASS). Required
  merge-base whole-branch review remains in progress.

## Final whole-branch review at `d7615f9`

- Review package: `review-27c30a6..d7615f9.diff` (26 commits, 48 files).
- Independent final review SHA-256:
  `42649f75bb3fa36fff3753f0e23998cfb272e93ba291454bf53c0922a27ef820`.
- Verdict: not ready to merge; Critical 0, Important 6, Minor 2.
- Important findings: WAL recovery mutates supplicant outside the transition
  lock; FD9/FD7 child isolation omits lock acquisition, OPC, and FD9-only
  writers; policy publication precedes the tombstone; boot extra-block
  install/sync failures are masked; passive_roam advertises a Mode A
  cross-SSID path rejected by the supported writer; SSID validation and
  serialization are lossy/incomplete.
- Minors: active-policy wording still calls the common `freq_list`
  `scan_freq`; final ledger/board records are modified but not in nominated
  HEAD. The known-command static inventory limitation remains acceptable, but
  cannot excuse the concretely omitted supported writer graphs.
- Ruling: In Mode A, keep manual cross-SSID selection unsupported in
  `passive_roam`; read the immutable boot policy, suppress/reject extra-SSID
  manual targets explicitly, and preserve same-SSID BSSID roam — this matches
  the established automatic-Mode-A/manual-Mode-B product boundary without
  adding a second external transition API — cost if wrong: operators lose a
  manual Mode A cross-SSID action they may have expected, although the exposed
  path was already deterministically rejected by `wifi connect`.
- Ruling: Define the shared SSID contract as valid nonempty UTF-8 of 1..32
  encoded bytes, preserving leading/trailing spaces and printable
  backslash/quote bytes via byte-exact hex supplicant serialization; reject
  C0/DEL controls, duplicates, and extra/base identity duplication at schema
  where expressible and at every runtime boundary — cost if wrong: previously
  schema-accepted control/duplicate values become invalid and generated
  `ssid=` values are less human-readable, but accepted identities round-trip
  exactly instead of silently changing.
- Ruling: Preserve the already tracked SDD ledger/report by including their
  final delta as an intentional, separate evidence-only commit inside the
  single final-fix review range rather than deleting or silently omitting them
  — the normal SDD cleanup rule assumes ignored scratch, but these two files
  are already branch history and the reviewer treated them as binding input —
  cost if wrong: the branch retains process/audit documentation maintainers may
  prefer outside product history; separating the commit makes it removable.
- Final review fix wave: one implementer, base `d7615f9`; no second fix wave.


## 2026-08-23 — Task 10 final whole-branch fix wave (all review findings)

- Fix base remained `d7615f9876f05554f74adc344997f50d9ad575cb`; binding review SHA-256 was `42649f75bb3fa36fff3753f0e23998cfb272e93ba291454bf53c0922a27ef820`.
- Production/tests/operator-doc/schema commit: `a0ec07e` (`fix(wifi): close final roam policy review gaps`). The pre-commit hook's unrelated local driver-manifest regeneration was explicitly removed before the final commit.
- TDD RED was witnessed before production edits:
  - logger focused set: `39 failed, 203 passed` (`/tmp/task10-final-fix-red-logger.log`) — real WAL-lock contention/startup, Mode A manual CLI, SSID Python/parser/schema boundaries;
  - snapshot owner-policy set: `18 failed, 12 passed` (`/tmp/task10-final-fix-red-scripts-policy.log`) — tombstone order/fault boundaries and SSID snapshot validation;
  - init harness: `PASS=94 FAIL=33` (`/tmp/task10-final-fix-red-init.log`) — checked install boundaries, fail-closed boot topology, SSID generation;
  - writer harness: `PASS=241 FAIL=14` (`/tmp/task10-final-fix-red-writers.log`) — FD inventories/orphan-child lock release and exact SSID writers;
  - CTRL_IFACE/iw consumer supplement: `5 failed` (`/tmp/task10-final-fix-red-ssid-ctrl-consumers.log`).
- Resolutions:
  1. Pending selection recovery now detects unlocked, acquires the exact nonblocking per-interface transition lock, rechecks WAL/gate, and mutates only in the lock-held helper. Main contention defers one cycle; startup contention/failure exits with zero control mutation.
  2. FD7 acquisition closes FD9 in its waiter; all supported `wifi connect`, OPC, and FD9-only writer child/substitution/transitive paths use close-first wrappers. Exact inventories and live-child SIGKILL probes prove ordered FD9/FD7 release.
  3. Snapshot creation fully stages/validates policy, durably publishes the tombstone first, then policy; tombstone-only and policy-without-tombstone states fail closed and never reconstruct from changed live JSON.
  4. One checked same-directory boot install primitive preserves metadata and requires staged sync, rename, installed-file sync, and directory sync for normalize/generate/remove paths. Every injected boundary propagates and `wifi_init` exits before affected supplicant continuation.
  5. `passive_roam` uses immutable boot policy. Mode A filters/rejects manual cross-SSID and never starts `wifi connect`; same-SSID BSSID roam remains. Mode B manual cross-SSID remains integrated and supported.
  6. Shared SSID contract is valid nonempty UTF-8, 1..32 encoded bytes, no C0/DEL, unique extras, and no boot base/extra identity duplicate. Spaces/quotes/backslashes are preserved and all supported writers/generators emit hexadecimal `ssid=`. Consumers decode exact CTRL_IFACE/iw identities; real upstream wpa_supplicant config parsing is exercised.
  7. Active-policy terminology now says common/configured `freq_list`; `scan_freq` remains only in explicitly legacy fallback/removal contexts. This evidence is intentionally isolated to the two tracked SDD records.
- Self-review caught and rejected an overbroad interpretation that would have prohibited a valid Mode B manual candidate after it became the live single-block base. New RED (`2 failed, 10 passed` plus four writer assertions) and GREEN (`12 passed`; full writer `259/0`) preserve Mode B while retaining boot snapshot base/extra duplicate rejection.
- Final GREEN: logger `699 passed`; scripts pytest `190 passed`; init `127/0`; writer `259/0`; Bash/POSIX/Python/JSON static checks `FINAL_STATIC_OK`; defaults/schema `FINAL_DEFAULTS_SCHEMA_OK`; full `validate_release.sh pre` returned `0`; base and working diff checks passed.
- Final concern: none. The writer harness cleanup poll was widened only as test scheduler margin; production watchdog timing and lock lifetime were not changed.


## 2026-08-23 — Final-fix continuation: deterministic watchdog gate closure

- Controller reran the clean committed tree before scoped re-review and found the writer cleanup mismatch that the implementer run had missed: full release preflight exit `1` with `PASS=256 FAIL=3` (`/tmp/task10-controller-final-fix-release-pre.log`, SHA-256 `03eb7865da1cf802888bd9f9ce9259f78ab2eb73b967aba5fdcb74c5b9182429`); isolated writer reproduced the same (`/tmp/task10-controller-final-fix-writer-rerun-1.log`, SHA-256 `5b0d330ac96a8f315aa72a9d62213aa0e00b929a7482797bde112cb1558d890b`). The two causal failures were monitor PID-poll and explicit-install orphan bounds; the later Mode B monitor failure was their stale-directory contamination.
- Root cause: the SIGKILL watchdog's critical owner-liveness path spawned nested schedulable processes on every 100 ms tick (`command substitution -> wifi_wpa_child_call -> connect_monitor_proc_start -> cat /proc/<pid>/stat`) and used external `cat`/`tr` again for pidfile/argv identity. If the liveness child was delayed or held, the watchdog itself could not observe owner death, so the nominal cleanup limit was not structurally bounded. Earlier `259/0` runs merely scheduled those normally short processes promptly.
- Timing instrumentation over 20 SIGKILL cleanup graphs from five concurrent runs measured normal owner-death detection at `0.105394..0.117046 s`, death-to-directory-removal at `0.023506..0.401153 s`, and total watcher-start-to-removal at `0.137805..0.516887 s` (`/tmp/task10-watchdog-trace.log`, SHA-256 `b56758b76bf110ec4274bd9ba36ed1a02965264eb5eb0bbd7d2bffbcb12e7344`). This explains the pass/fail split: prompt scheduling was fast, but no bound existed when the poll child stalled.
- Deterministic TDD RED armed an external `/proc/*/stat` trap only after monitor setup and association polling, then kept that exact watchdog child alive across owner SIGKILL. It produced `RESULT: PASS=257 FAIL=2`: external-child dependency and failure to remove the orphan within the original three-second bound (`/tmp/task10-final-fix-continuation-red-watchdog.log`, SHA-256 `7d006b6b4414be7a7df0476b89a0fc31823b0b2976bf8ea83c767db0231e22ce`).
- Scoped fix commit `32c42508b6869664439e36db8a6acde8788f5a67` makes `/proc` stat, NUL-delimited cmdline, and monitor pidfile reads direct shell builtins and assigns start tokens in caller variables. The watchdog owner-death/identity path now has no child or command substitution to stall; PID/start-token reuse protection, FD9->FD7 order, child FD isolation, and the production watchdog policy are unchanged. The harness was restored from the temporary 5-second margin to the intended 3-second combined PID+directory postcondition.
- Required clean-commit GREEN: two consecutive isolated writer runs both returned `RESULT: PASS=259 FAIL=0` with byte-identical logs (SHA-256 `ffb523ec6ff976fb934801c49672b179ce5976fc5e4b18d728d53cfbfae65b1a`), and full `validate_release.sh pre` returned `0` with logger `699`, scripts `190`, init `127/0`, and writer `259/0` (`/tmp/task10-final-fix-continuation-green-release-pre-final.log`, SHA-256 `706b0bc7423e52af447c6de39242ce863c39e382e0e8bb43955c32be038c8d10`).
- Concern: none. This is continuation of the single final fix wave, not a deadline-only workaround or a second wave.
