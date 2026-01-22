# Gemini Review Context Guardrails Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make `gemini-review` stable by default (Flash) and prevent context explosions by blocking binary/large-file reads and reducing workspace exposure.

**Architecture:**
- Default model is controlled by GitHub Actions repo variable `GEMINI_MODEL` (default: `gemini-3-flash-preview`).
- The reusable workflow (`automation` repo) enforces two layers of safety:
  1) `actions/checkout` sparse-checkout allowlist so large/binary artifacts are not present in the workspace.
  2) "safe" shell wrappers (`cat/head/tail/grep`) prepended to `PATH` so even if a file is present, non-text/large/executable files are refused.
- Optional hardening: clear `.gemini/` between retries to avoid cross-attempt context accumulation.

**Tech Stack:** GitHub Actions reusable workflows, `google-github-actions/run-gemini-cli@v0`, Gemini CLI, `gh` CLI.

## Repository Variables (Control Surface)

### Required
- `GEMINI_MODEL` (string): `gemini-3-flash-preview` (default) or `gemini-3-pro-preview`.
- `GEMINI_FALLBACK_MODEL` (string): usually `gemini-3-flash-preview`.

### Optional
- `UPLOAD_ARTIFACTS` (bool string): `true/false` for debugging (should default to `false`).

How to change variables:
- GitHub UI: `Settings -> Secrets and variables -> Actions -> Variables`.
- CLI examples:

```bash
gh variable set GEMINI_MODEL -b gemini-3-flash-preview -R jhw7500/wlan-package
gh variable set GEMINI_MODEL -b gemini-3-pro-preview -R jhw7500/wlan-package
```

## Task 1: Add sparse-checkout allowlist in shared workflow

**Files:**
- Modify: `projects/automation/.github/workflows/gemini-review.yml`

**Step 1: Update checkout step to use sparse-checkout**

Change the `actions/checkout` step to include only text/config/script paths needed for review, excluding large/binary payloads by default.

Recommended allowlist (adjust if needed):

```yaml
with:
  fetch-depth: 1
  lfs: false
  submodules: false
  sparse-checkout-cone-mode: false
  sparse-checkout: |
    build.sh
    config.json
    dist/wlan/DEBIAN/
    dist/wlan/usr/local/scripts/
    dist/wlan/usr/local/bin/
    .github/
    README*
```

Notes:
- `dist/wlan/usr/local/bin/` might contain binaries in some repos. If it does, prefer excluding it and only include scripts directories.
- Explicitly avoid: `release/`, `dist/wlan/opt/`, `dist/wlan/usr/lib/firmware/`.

**Step 2: Verification**

Run a manual review workflow on a test PR and confirm:
- `ls release/` fails (directory missing) in the workspace
- `ls dist/wlan/usr/lib/firmware/` fails (directory missing)

## Task 2: Add safe shell wrappers for cat/head/tail/grep

**Files:**
- Modify: `projects/automation/.github/workflows/gemini-review.yml`

**Goal:** If the model tries to read a binary, executable, extensionless, or very large file, the wrapper refuses.

**Step 1: Add a step to create wrappers**

Add a step before the first `run-gemini-cli` attempt:

```bash
mkdir -p .gemini/safe-bin
cat > .gemini/safe-bin/cat <<'SH'
#!/usr/bin/env sh
set -eu

max_bytes=200000

is_blocked_ext() {
  case "$1" in
    *.bin|*.ko|*.deb|*.tar|*.tar.gz|*.tgz|*.zip|*.gz|*.xz|*.7z|*.exe|*.so|*.a|*.o)
      return 0
      ;;
  esac
  return 1
}

is_text_file() {
  # file(1) is available on ubuntu runners
  mime=$(file -b --mime-type "$1" 2>/dev/null || true)
  case "$mime" in
    text/*|application/json|application/xml)
      return 0
      ;;
  esac
  return 1
}

for f in "$@"; do
  # Block stdin usage
  if [ "$f" = "-" ]; then
    echo "ERROR: refusing to read from stdin" >&2
    exit 2
  fi

  # Block extension-based binaries
  if is_blocked_ext "$f"; then
    echo "ERROR: refusing to read blocked file type: $f" >&2
    exit 2
  fi

  # Block missing or non-regular files
  if [ ! -f "$f" ]; then
    echo "ERROR: file not found or not a regular file: $f" >&2
    exit 2
  fi

  # Block executable files (covers extensionless executables)
  if [ -x "$f" ]; then
    echo "ERROR: refusing to read executable file: $f" >&2
    exit 2
  fi

  # Block large files
  size=$(wc -c < "$f" | tr -d ' ')
  if [ "$size" -gt "$max_bytes" ]; then
    echo "ERROR: refusing to read large file ($size bytes): $f" >&2
    exit 2
  fi

  # Block non-text files
  if ! is_text_file "$f"; then
    echo "ERROR: refusing to read non-text file: $f" >&2
    exit 2
  fi
done

exec /bin/cat "$@"
SH

chmod +x .gemini/safe-bin/cat

# Option: wrappers for head/tail/grep that delegate to /usr/bin/head etc but validate file args similarly.
ln -sf cat .gemini/safe-bin/head
ln -sf cat .gemini/safe-bin/tail
ln -sf cat .gemini/safe-bin/grep
```

Notes:
- The `ln -sf` trick is a simplification. If `grep/head/tail` need flags parsing, implement separate wrappers that validate only path-like args and then exec the real binary.
- Start with `cat` wrapper first if you want minimal change, then iterate.

**Step 2: Ensure run-gemini-cli uses these wrappers**

Set `PATH` in each `run-gemini-cli` step env so the wrappers take precedence:

```yaml
env:
  PATH: ${{ github.workspace }}/.gemini/safe-bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
```

**Step 3: Verification**

In a test run, confirm telemetry contains attempted reads of text scripts only, and add an explicit self-test step (once) if needed:

```bash
dd if=/dev/urandom of=/tmp/blob.bin bs=1024 count=2
! cat /tmp/blob.bin
```

## Task 3: Avoid cross-attempt accumulation (retry hardening)

**Files:**
- Modify: `projects/automation/.github/workflows/gemini-review.yml`

**Step 1: Before attempt 2 and fallback, reset gemini state**

Add a step executed when attempt 1 fails:

```bash
rm -rf .gemini
```

Then re-run the "create wrappers" step (and re-create `.gemini/settings.json` if needed) before attempt 2.

Rationale:
- We observed failures that could be consistent with retry attempts inheriting state.
- Resetting `.gemini/` ensures each attempt starts fresh.

**Step 2: Verification**

Force a failure on attempt 1 (e.g., temporarily set invalid model in a test branch) and confirm attempt 2 still runs with a clean `.gemini/`.

## Task 4: Release shared workflow + bump consumers

**Files:**
- Modify: `projects/automation/.github/workflows/gemini-review.yml`
- Modify: `projects/wlan-package/.github/workflow-config.yml` (and/or workflow ref rewrites if needed)

**Step 1: automation repo**
- Create branch
- Apply Tasks 1-3
- Open PR, merge
- Tag release (e.g. `v1.12`)

**Step 2: wlan-package repo**
- Bump `automation_ref` to `v1.12`
- Rewrite `uses: jhw7500/automation/...@v*` refs to `v1.12` if required
- Run `🧪 Gemini Manual PR Review` on a small test PR

## Task 5: Verification Matrix (evidence-based)

Run these and save artifacts:

1) Flash default (expected stable)
- `GEMINI_MODEL=gemini-3-flash-preview`
- Run manual PR review on `PR #16`.

2) Pro override
- `GEMINI_MODEL=gemini-3-pro-preview`
- Run manual PR review on `PR #16`.

3) Large file safety
- Ensure sparse-checkout excludes `release/` and firmware directories.
- Ensure `cat` wrapper refuses `/tmp/blob.bin` in a workflow self-test.

Success criteria:
- No `input token count exceeds 1048576` on normal PRs.
- No `The action has timed out` due to infinite tool loops.
- Model cannot read binaries/large files even if attempted.
