# Global Runtime Output Design

## Goal

Make the WLAN package suitable for global deployment by giving package installation a compact progress display, emitting human-readable operational messages in ASCII English, and eliminating the GNU awk multibyte warning seen during boot.

The change must preserve service behavior, machine-readable output, exit status, log severity, and configuration semantics.

## Scope

The implementation covers three related output paths:

1. `dist/wlan/DEBIAN/postinst` displays installation progress in the same in-place style as `factory_reset.sh`: start and finish records surround a single carriage-return-updated progress line, and the final update closes the line with `done`.
2. Human-readable messages emitted by shipped boot, daemon, and package-maintenance paths are written in ASCII English. This includes syslog/journal messages, stderr diagnostics, status descriptions, and exception text that may reach an operator.
3. `wifi_board_config.sh` scans binary kernel-module metadata under a byte-oriented locale so arbitrary `.ko` bytes are never interpreted as UTF-8 text.

Korean comments, documentation, and tests remain source-only and are outside the runtime output policy. The engineer-facing tools `wifi.sh`, `diag-9098-11ax.sh`, and `verify-he-mu-features.sh` retain their current Korean output by explicit product decision.

## Output Contract

Installation has six visible stages. A normal run follows this shape:

```text
[installer] install start
[installer] install  17% (1/6) .\r
...
[installer] install 100% (6/6) ...... done
[installer] install finish
```

Intermediate updates end in a carriage return so the next update replaces the same terminal line. The completed line retains the full dot sequence before `done`, preventing stale characters from a longer previous update. Non-interactive consumers may retain carriage returns in captured output; every stage still contains its numeric index and percentage.

Operational messages use stable ASCII wording. Existing prefixes, identifiers, values, and severity remain intact. Unicode display punctuation such as arrows and long dashes is replaced with ASCII equivalents where it appears in emitted messages.

Examples:

| Existing output | New output |
|---|---|
| `thermal_mgmt: config/jq 부재 → skip` | `thermal_mgmt: config/jq unavailable - skipped` |
| `snmp.trap.dest 미설정 - 트랩 생략` | `snmp.trap.dest is unset - trap skipped` |
| `allowed_hosts 타입 오류` | `allowed_hosts type error` |

Keys and values intended for scripts stay byte-for-byte stable. Examples include `enabled`, `enforcing`, `NOT-enforcing`, interface names, configuration paths, numeric codes, and command options. Translation changes only the explanation around them.

## Components

### Installer progress

`postinst` owns small output helpers for start, stage update, completion, and finish. Each existing installation section advances exactly one of the six stages. The helpers use the package's existing console-print path and preserve stderr fallback behavior when that helper is unavailable.

### Operational message translation

Translation is limited to executable messages in the boot and service graph, Python daemons, and Debian maintainer scripts. The operational set is the entry points referenced by packaged systemd units or Debian maintainer scripts, together with the shell and Python modules they call or import. Known affected areas include:

- `postinst` service lifecycle logging;
- `wifi_init.sh` boot policy diagnostics;
- `wifi_fw_config_lib.sh`, `wifi_peer_net_reapply.sh`, and peer-route/apply helpers;
- ACL and SNMP trap diagnostics;
- `wifi_bgscan.py`, `wifi_roam.py`, and operator-visible exception messages in shared logger code.

The implementation will inventory emitted strings rather than translating every Korean source line. Comments, docstrings that cannot be emitted, and test descriptions are intentionally unchanged.

### Kernel-module metadata parsing

`module_field()` in `wifi_board_config.sh` converts NUL-separated module bytes to lines and extracts `version` and `srcversion`. Only that binary pipeline runs with `LC_ALL=C`. The process and package locale remain `C.UTF-8`, because SSIDs and other user data can validly contain UTF-8.

The localized pipeline keeps the current first-match behavior and return status. It changes byte classification only; extracted ASCII metadata is unchanged.

## Data and Control Flow

```text
dpkg postinst
  -> start message
  -> six existing install sections
  -> in-place progress after each section
  -> completed progress line
  -> finish message

wifi_init.service
  -> wifi_init.sh loads board modules
  -> wifi_board_config.sh --verify-loaded
  -> module_field(module, version/srcversion)
  -> LC_ALL=C tr | awk over binary .ko bytes
  -> compare loaded and packaged metadata
```

The message translation does not add calls, retries, sleeps, service transitions, or configuration writes.

## Error Handling

- Installer output failures follow the existing best-effort logging behavior and do not mask a package installation failure.
- Stage progress is printed only after its associated operation completes under the existing `set -e` policy.
- Module verification continues to fail for missing or mismatched metadata exactly as before. The locale change suppresses only encoding diagnostics caused by scanning binary input.
- Human-readable translations retain the original log severity and exit code so monitoring and callers observe the same failure class.

## Verification

Automated checks will cover the behavior rather than the wording implementation:

1. A post-install progress test asserts the start/finish records, six ordered stage updates, monotonic percentages, carriage-return updates, and final `done` line.
2. A module metadata regression test runs verification under `C.UTF-8` with invalid UTF-8 bytes in deterministic `.ko` fixtures. It asserts successful metadata extraction and no `Invalid multibyte data` warning on stderr.
3. A static runtime-output check rejects Hangul and non-ASCII display punctuation in emitted strings for the defined operational paths. It explicitly excludes comments, tests, and the three approved engineer tools.
4. Existing focused logger and shell suites run after the edits, followed by `scripts/validate_release.sh source`.
5. Shell syntax and error-level shellcheck validation run for changed shell scripts.

## Acceptance Criteria

- Package installation shows the six-stage in-place progress display and terminates the progress line cleanly.
- Boot verification of both packaged WLAN modules emits zero awk multibyte warnings under `C.UTF-8`.
- Shipped boot, daemon, and package-maintenance messages in scope contain no Korean text or Unicode display punctuation.
- `wifi.sh`, `diag-9098-11ax.sh`, and `verify-he-mu-features.sh` retain their engineer-facing Korean output.
- Machine-readable fields, service behavior, configuration behavior, severity, and exit status do not change.
- The focused and release validation suites pass.
