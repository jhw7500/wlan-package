#!/bin/bash
set -euo pipefail

REPO=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
ARCHIVE=${1:-"$REPO/release/wlan-package.tar"}
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

validate_archive() {
    SOURCE_ARCHIVE_MANIFEST="$REPO/scripts/source_archive_manifest.txt" \
        python3 - "$1" <<'PY'
import posixpath
import os
import stat
import sys
import tarfile

archive = sys.argv[1]
manifest_path = os.environ["SOURCE_ARCHIVE_MANIFEST"]

# Only files needed to rebuild, validate, document, or configure the product are
# publishable. In particular, allowing "wlan-bridge/**" or "wlan-opc/**" would
# silently reintroduce nested agent state and local test-environment files.
EXACT_PATHS = {
    "CHANGELOG.md",
    "README.md",
    "build.sh",
    "dist",
    "dist/wlan",
    "docs",
    "docs/wifi_init_conf.schema.json",
    "docs/wifi_init_conf_guide.md",
    "docs/wifi_init_conf_webui_handoff.md",
    "scripts",
    "scripts/exec_bit_targets.py",
    "scripts/gen_config_defaults.py",
    "scripts/package_tar_test.sh",
    "scripts/source_archive_manifest.txt",
    "scripts/validate_release.sh",
    "scripts/validate_release_test.sh",
    "wlan-bridge/docs",
    "wlan-bridge/scripts",
    "wlan-bridge/wbridge",
    "wlan-bridge",
    "wlan-opc",
    "wlan-opc/Makefile",
    "wlan-opc/README.md",
    "wlan-opc/opcd",
    "wlan-opc/protocol",
    "wlan-opc/vhlctl",
}
PERMITTED_PREFIXES = (
    "dist/wlan/",
    "wlan-bridge/docs/",
    "wlan-bridge/scripts/",
    "wlan-bridge/wbridge/",
    "wlan-opc/opcd/",
    "wlan-opc/protocol/",
    "wlan-opc/vhlctl/",
)
PROHIBITED_COMPONENTS = {
    ".agents",
    ".base",
    ".bkit",
    ".claude",
    ".code-review-graph",
    ".compound-engineering",
    ".git",
    ".github",
    ".jhw",
    ".omc",
    ".omo",
    ".omx",
    ".paul",
    ".playwright-mcp",
    ".pytest_cache",
    ".ruff_cache",
    ".serena",
    ".superpowers",
    ".worktrees",
    "__pycache__",
    "artifacts",
    "tmp",
}
PROHIBITED_PREFIXES = (
    "wlan-bridge/wbridge/release",
    "wlan-bridge/wbridge/debug",
    "wlan-bridge/wbridge/tests/bin",
)
REQUIRED_PATHS = {
    "build.sh",
    "README.md",
    "CHANGELOG.md",
    "dist/wlan/DEBIAN/control",
    "dist/wlan/usr/lib/firmware/cts/sd9098_wlan_v1.bin",
    # The source archive must remain independently buildable: pre-build gates
    # execute both logger and script suites from dist rather than pruning them.
    "dist/wlan/usr/local/logger/tests/test_config_default_sync.py",
    "dist/wlan/usr/local/scripts/tests/test_wifi_log_extract.py",
    "docs/wifi_init_conf.schema.json",
    "docs/wifi_init_conf_guide.md",
    "docs/wifi_init_conf_webui_handoff.md",
    "scripts/exec_bit_targets.py",
    "scripts/gen_config_defaults.py",
    "scripts/package_tar_test.sh",
    "scripts/source_archive_manifest.txt",
    "scripts/validate_release.sh",
    "scripts/validate_release_test.sh",
    "wlan-bridge/docs/VLAN-SUPPORT.md",
    "wlan-bridge/scripts/optimize-for-udp.sh",
    "wlan-bridge/wbridge/Makefile",
    "wlan-bridge/wbridge/make-for-imx8",
    "wlan-bridge/wbridge/make-for-imx93",
    "wlan-opc/Makefile",
    "wlan-opc/opcd/Makefile",
    "wlan-opc/opcd/opcd.service",
    "wlan-opc/protocol/Makefile",
    "wlan-opc/vhlctl/Makefile",
}
REQUIRED_EXECUTABLES = {
    "build.sh",
    "scripts/package_tar_test.sh",
    "scripts/validate_release.sh",
    "scripts/validate_release_test.sh",
    "wlan-bridge/scripts/optimize-for-udp.sh",
    "wlan-bridge/wbridge/make-for-imx8",
    "wlan-bridge/wbridge/make-for-imx93",
}

try:
    with open(manifest_path, encoding="utf-8") as stream:
        EXPECTED_FILES = {
            line.strip() for line in stream
            if line.strip() and not line.lstrip().startswith("#")
        }
except OSError as exc:
    print(f"FAIL: cannot read source archive manifest: {manifest_path}: {exc}", file=sys.stderr)
    raise SystemExit(1)
if not EXPECTED_FILES:
    print("FAIL: source archive manifest is empty", file=sys.stderr)
    raise SystemExit(1)


def canonical_member_name(raw):
    if raw.startswith("/"):
        raise ValueError("absolute member path")
    while raw.startswith("./"):
        raw = raw[2:]
    raw = raw.rstrip("/")
    if raw in ("", "."):
        return ""
    normalized = posixpath.normpath(raw)
    if normalized == ".." or normalized.startswith("../"):
        raise ValueError("parent-traversal member path")
    if normalized != raw:
        raise ValueError("non-canonical member path")
    return normalized


def permitted(name):
    if not name or name in EXACT_PATHS:
        return True
    return any(name.startswith(root) for root in PERMITTED_PREFIXES)


def resolved_link_target(member_name, linkname, symbolic):
    if not linkname or linkname.startswith("/"):
        raise ValueError("empty or absolute link target")
    base = posixpath.dirname(member_name) if symbolic else ""
    target = posixpath.normpath(posixpath.join(base, linkname))
    if target == ".." or target.startswith("../") or target.startswith("/"):
        raise ValueError("link target escapes archive")
    return target[2:] if target.startswith("./") else target


errors = []
try:
    archive_stat = os.lstat(archive)
except OSError as exc:
    print(f"FAIL: cannot stat source tar: {archive}: {exc}", file=sys.stderr)
    raise SystemExit(1)
if not stat.S_ISREG(archive_stat.st_mode):
    print(f"FAIL: source tar must be a regular non-symlink file: {archive}", file=sys.stderr)
    raise SystemExit(1)
archive_mode = stat.S_IMODE(archive_stat.st_mode)
if archive_mode & (stat.S_IWGRP | stat.S_IWOTH):
    errors.append(
        f"archive file itself is group/world writable: {archive_mode:#o}"
    )
try:
    tf = tarfile.open(archive, mode="r:*")
except (OSError, tarfile.TarError) as exc:
    print(f"FAIL: source tar is unreadable: {archive}: {exc}", file=sys.stderr)
    raise SystemExit(1)

with tf:
    members = tf.getmembers()

    canonical_names = set()
    canonical_counts = {}
    members_by_name = {}
    normalized = []
    for member in members:
        try:
            name = canonical_member_name(member.name)
        except ValueError as exc:
            errors.append(f"{member.name!r}: {exc}")
            continue
        canonical_names.add(name)
        canonical_counts[name] = canonical_counts.get(name, 0) + 1
        members_by_name[name] = member
        normalized.append((member, name))

    for name, count in sorted(canonical_counts.items()):
        if name and count != 1:
            errors.append(f"{name!r}: duplicate canonical member ({count} entries)")

    # Every explicit ancestor of another member must be a real directory. This
    # forbids file/link aliases such as `scripts` followed by `scripts/x`.
    structural_parents = set()
    for name in canonical_names:
        parent = posixpath.dirname(name)
        while parent:
            structural_parents.add(parent)
            parent = posixpath.dirname(parent)
    for parent in sorted(structural_parents & canonical_names):
        if not members_by_name[parent].isdir():
            errors.append(f"{parent!r}: archive ancestor is not a directory")

    # The manifest is self-referential: its member list includes the manifest
    # itself, while the current validator reads the authoritative local copy.
    # Compare its bytes too so an archive cannot carry a weaker declaration.
    if "scripts/source_archive_manifest.txt" in members_by_name:
        manifest_member = members_by_name["scripts/source_archive_manifest.txt"]
        if manifest_member.isfile():
            archived_manifest = tf.extractfile(manifest_member)
            if archived_manifest is None or archived_manifest.read() != open(manifest_path, "rb").read():
                errors.append("'scripts/source_archive_manifest.txt': archived manifest differs from release gate manifest")

    for member, name in normalized:
        if not permitted(name):
            errors.append(f"{name!r}: path is outside permitted source prefixes")

        if any(component in PROHIBITED_COMPONENTS for component in name.split("/")):
            errors.append(f"{name!r}: internal/runtime path component")
        if any(name == root or name.startswith(root + "/")
               for root in PROHIBITED_PREFIXES):
            errors.append(f"{name!r}: generated build-output path")
        if name.endswith(".pyc"):
            errors.append(f"{name!r}: compiled Python cache")

        if member.uid != 0 or member.gid != 0:
            errors.append(
                f"{name!r}: non-root numeric owner {member.uid}:{member.gid}"
            )
        if member.uname or member.gname:
            errors.append(
                f"{name!r}: stored owner names must be empty, got "
                f"{member.uname!r}:{member.gname!r}"
            )

        if member.mode & (stat.S_ISUID | stat.S_ISGID):
            errors.append(f"{name!r}: setuid/setgid mode {member.mode:#o}")
        if member.mode & (stat.S_IWGRP | stat.S_IWOTH):
            errors.append(f"{name!r}: group/world writable mode {member.mode:#o}")

        if member.issym() or member.islnk():
            errors.append(f"{name!r}: archive link entries are not permitted")
        elif not (member.isfile() or member.isdir()):
            errors.append(f"{name!r}: device/FIFO/unsupported archive entry type")

    missing = sorted(REQUIRED_PATHS - canonical_names)
    errors.extend(f"{path!r}: required build/source path is missing" for path in missing)
    for path in sorted(REQUIRED_PATHS & canonical_names):
        member = members_by_name[path]
        if not member.isfile():
            errors.append(f"{path!r}: required build/source path is not a regular file")
        elif member.size == 0:
            errors.append(f"{path!r}: required build/source file is empty")
    for path in sorted(REQUIRED_EXECUTABLES & canonical_names):
        member = members_by_name[path]
        if member.isfile() and not member.mode & 0o111:
            errors.append(f"{path!r}: required entrypoint is not executable")

    actual_files = {name for member, name in normalized if member.isfile()}
    unexpected_files = sorted(actual_files - EXPECTED_FILES)
    missing_manifest_files = sorted(EXPECTED_FILES - actual_files)
    errors.extend(
        f"{path!r}: regular file is not declared in source archive manifest"
        for path in unexpected_files
    )
    errors.extend(
        f"{path!r}: manifest-declared source file is missing"
        for path in missing_manifest_files
    )

    expected_directories = set()
    for path in EXPECTED_FILES:
        parent = posixpath.dirname(path)
        while parent:
            expected_directories.add(parent)
            parent = posixpath.dirname(parent)
    actual_directories = {name for member, name in normalized if member.isdir() and name}
    errors.extend(
        f"{path!r}: directory is not required by the source manifest"
        for path in sorted(actual_directories - expected_directories)
    )
    errors.extend(
        f"{path!r}: required source parent directory is missing"
        for path in sorted(expected_directories - actual_directories)
    )

if errors:
    print(f"FAIL: source tar safety violations in {archive}:", file=sys.stderr)
    for error in errors[:40]:
        print(f"  - {error}", file=sys.stderr)
    if len(errors) > 40:
        print(f"  - ... {len(errors) - 40} additional violation(s)", file=sys.stderr)
    raise SystemExit(1)

print(f"source tar safety test: PASS ({archive})")
PY
}

make_good_fixture() {
    local root="$WORK/good-root"
    mkdir -p \
        "$root/dist/wlan/DEBIAN" \
        "$root/dist/wlan/usr/lib/firmware/cts" \
        "$root/dist/wlan/usr/local/logger/tests" \
        "$root/dist/wlan/usr/local/scripts/tests" \
        "$root/docs" \
        "$root/scripts" \
        "$root/wlan-bridge/docs" \
        "$root/wlan-bridge/scripts" \
        "$root/wlan-bridge/wbridge" \
        "$root/wlan-opc/opcd" \
        "$root/wlan-opc/protocol" \
        "$root/wlan-opc/vhlctl"

    while IFS= read -r file; do
        [ -n "$file" ] || continue
        mkdir -p "$root/$(dirname "$file")"
        printf 'fixture\n' > "$root/$file"
    done < "$REPO/scripts/source_archive_manifest.txt"
    cp "$REPO/scripts/source_archive_manifest.txt" \
        "$root/scripts/source_archive_manifest.txt"

    find "$root" -type d -exec chmod 0755 {} +
    find "$root" -type f -exec chmod 0644 {} +
    chmod 0755 "$root/build.sh" \
        "$root/scripts/package_tar_test.sh" \
        "$root/scripts/validate_release.sh" \
        "$root/scripts/validate_release_test.sh" \
        "$root/wlan-bridge/scripts/optimize-for-udp.sh" \
        "$root/wlan-bridge/wbridge/make-for-imx8" \
        "$root/wlan-bridge/wbridge/make-for-imx93"

    tar --format=posix --numeric-owner --owner=0 --group=0 \
        -cf "$WORK/good.tar" -C "$root" .
    chmod 0644 "$WORK/good.tar"
}

make_bad_fixtures() {
    python3 - "$WORK/good.tar" "$WORK" <<'PY'
import copy
import io
import os
import sys
import tarfile

source, outdir = sys.argv[1:]

fixtures = {
    "path": ("artifacts/private.txt", tarfile.REGTYPE, 0o644, ""),
    "prefix_confusion": ("wlan-opc/opcd-evil/private.txt", tarfile.REGTYPE, 0o644, ""),
    "build_output": ("wlan-bridge/wbridge/release/wbridge", tarfile.REGTYPE, 0o755, ""),
    "symlink": ("wlan-bridge/docs/escape-symlink", tarfile.SYMTYPE, 0o777, "../../../outside"),
    "hardlink": ("wlan-bridge/docs/escape-hardlink", tarfile.LNKTYPE, 0o644, "../outside"),
    "device": ("wlan-bridge/docs/device", tarfile.CHRTYPE, 0o600, ""),
    "fifo": ("wlan-bridge/docs/fifo", tarfile.FIFOTYPE, 0o600, ""),
    "setuid": ("wlan-bridge/docs/setuid", tarfile.REGTYPE, 0o4644, ""),
    "setgid": ("wlan-bridge/docs/setgid", tarfile.REGTYPE, 0o2644, ""),
    "groupwrite": ("wlan-bridge/docs/groupwrite", tarfile.REGTYPE, 0o664, ""),
    "worldwrite": ("wlan-bridge/docs/worldwrite", tarfile.REGTYPE, 0o646, ""),
    "numeric_owner": ("wlan-bridge/docs/nonroot", tarfile.REGTYPE, 0o644, ""),
    "named_owner": ("wlan-bridge/docs/local-owner", tarfile.REGTYPE, 0o644, ""),
    "root_named_owner": ("wlan-bridge/docs/root-named-owner", tarfile.REGTYPE, 0o644, ""),
    "undeclared_nested": ("wlan-opc/opcd/target-credentials.txt", tarfile.REGTYPE, 0o600, ""),
    "internal_symlink": ("wlan-bridge/docs/internal-symlink", tarfile.SYMTYPE, 0o777, "VLAN-SUPPORT.md"),
    "internal_hardlink": ("wlan-bridge/docs/internal-hardlink", tarfile.LNKTYPE, 0o644, "wlan-bridge/docs/VLAN-SUPPORT.md"),
    "empty_directory": ("wlan-opc/opcd/unused-empty", tarfile.DIRTYPE, 0o755, ""),
}

for fixture, (name, entry_type, mode, linkname) in fixtures.items():
    destination = os.path.join(outdir, f"bad-{fixture}.tar")
    with tarfile.open(source, "r:*") as src, tarfile.open(destination, "w") as dst:
        for original in src.getmembers():
            member = copy.copy(original)
            data = src.extractfile(original) if original.isfile() else None
            dst.addfile(member, data)

        member = tarfile.TarInfo(name)
        member.type = entry_type
        member.mode = mode
        member.uid = member.gid = 0
        member.uname = member.gname = ""
        member.linkname = linkname
        if fixture == "device":
            member.devmajor, member.devminor = 1, 3
        elif fixture == "numeric_owner":
            member.uid, member.gid = 123, 456
            member.uname = member.gname = ""
        elif fixture == "named_owner":
            member.uname = member.gname = "jhw"
        elif fixture == "root_named_owner":
            member.uname = member.gname = "root"
        dst.addfile(member)
    os.chmod(destination, 0o644)


def replacement_archive(fixture, replacement, data=None):
    destination = os.path.join(outdir, f"bad-{fixture}.tar")
    with tarfile.open(source, "r:*") as src, tarfile.open(destination, "w") as dst:
        for original in src.getmembers():
            if original.name.lstrip("./").rstrip("/") == "build.sh":
                continue
            member = copy.copy(original)
            payload = src.extractfile(original) if original.isfile() else None
            dst.addfile(member, payload)
        if data is not None:
            replacement.size = len(data)
        dst.addfile(replacement, io.BytesIO(data) if data is not None else None)
    os.chmod(destination, 0o644)


duplicate = os.path.join(outdir, "bad-duplicate.tar")
with tarfile.open(source, "r:*") as src, tarfile.open(duplicate, "w") as dst:
    for original in src.getmembers():
        member = copy.copy(original)
        payload = src.extractfile(original) if original.isfile() else None
        dst.addfile(member, payload)
    member = tarfile.TarInfo("build.sh")
    member.mode = 0o755
    member.uid = member.gid = 0
    payload = b"duplicate\n"
    member.size = len(payload)
    dst.addfile(member, io.BytesIO(payload))
os.chmod(duplicate, 0o644)

wrong_type = tarfile.TarInfo("build.sh")
wrong_type.type = tarfile.DIRTYPE
wrong_type.mode = 0o755
wrong_type.uid = wrong_type.gid = 0
replacement_archive("required-type", wrong_type)

empty = tarfile.TarInfo("build.sh")
empty.mode = 0o755
empty.uid = empty.gid = 0
replacement_archive("required-empty", empty, b"")

nonexec = tarfile.TarInfo("build.sh")
nonexec.mode = 0o644
nonexec.uid = nonexec.gid = 0
replacement_archive("required-nonexec", nonexec, b"fixture\n")

outer_mode = os.path.join(outdir, "bad-outer-mode.tar")
with open(source, "rb") as src, open(outer_mode, "wb") as dst:
    dst.write(src.read())
os.chmod(outer_mode, 0o664)

outer_symlink = os.path.join(outdir, "bad-outer-symlink.tar")
os.symlink(source, outer_symlink)


ancestor = os.path.join(outdir, "bad-ancestor.tar")
with tarfile.open(source, "r:*") as src, tarfile.open(ancestor, "w") as dst:
    for original in src.getmembers():
        if original.name.lstrip("./").rstrip("/") == "scripts":
            continue
        member = copy.copy(original)
        payload = src.extractfile(original) if original.isfile() else None
        dst.addfile(member, payload)
    member = tarfile.TarInfo("scripts")
    member.mode = 0o644
    member.uid = member.gid = 0
    payload = b"not-a-directory\n"
    member.size = len(payload)
    dst.addfile(member, io.BytesIO(payload))
os.chmod(ancestor, 0o644)


ancestor_link = os.path.join(outdir, "bad-ancestor-link.tar")
with tarfile.open(source, "r:*") as src, tarfile.open(ancestor_link, "w") as dst:
    for original in src.getmembers():
        if original.name.lstrip("./").rstrip("/") == "wlan-opc/opcd":
            continue
        member = copy.copy(original)
        payload = src.extractfile(original) if original.isfile() else None
        dst.addfile(member, payload)
    member = tarfile.TarInfo("wlan-opc/opcd")
    member.type = tarfile.SYMTYPE
    member.mode = 0o777
    member.uid = member.gid = 0
    member.linkname = "protocol"
    dst.addfile(member)
os.chmod(ancestor_link, 0o644)
PY
}

expect_rejected() {
    local fixture="$1"
    local expected="$2"
    local output="$WORK/$fixture.output"
    if validate_archive "$WORK/bad-$fixture.tar" >"$output" 2>&1; then
        echo "FAIL: unsafe $fixture fixture was accepted" >&2
        return 1
    fi
    if ! grep -Fq "$expected" "$output"; then
        echo "FAIL: $fixture fixture failed for the wrong reason; expected: $expected" >&2
        cat "$output" >&2
        return 1
    fi
}

make_good_fixture
validate_archive "$WORK/good.tar" >/dev/null
make_bad_fixtures
expect_rejected path "outside permitted source prefixes"
expect_rejected prefix_confusion "outside permitted source prefixes"
expect_rejected build_output "generated build-output path"
expect_rejected symlink "archive link entries are not permitted"
expect_rejected hardlink "archive link entries are not permitted"
expect_rejected device "device/FIFO/unsupported archive entry type"
expect_rejected fifo "device/FIFO/unsupported archive entry type"
expect_rejected setuid "setuid/setgid mode"
expect_rejected setgid "setuid/setgid mode"
expect_rejected groupwrite "group/world writable mode"
expect_rejected worldwrite "group/world writable mode"
expect_rejected numeric_owner "non-root numeric owner"
expect_rejected named_owner "stored owner names must be empty"
expect_rejected root_named_owner "stored owner names must be empty"
expect_rejected undeclared_nested "regular file is not declared in source archive manifest"
expect_rejected internal_symlink "archive link entries are not permitted"
expect_rejected internal_hardlink "archive link entries are not permitted"
expect_rejected empty_directory "directory is not required by the source manifest"
expect_rejected duplicate "duplicate canonical member"
expect_rejected required-type "required build/source path is not a regular file"
expect_rejected required-empty "required build/source file is empty"
expect_rejected required-nonexec "required entrypoint is not executable"
expect_rejected outer-mode "archive file itself is group/world writable"
expect_rejected outer-symlink "source tar must be a regular non-symlink file"
expect_rejected ancestor "archive ancestor is not a directory"
expect_rejected ancestor-link "archive ancestor is not a directory"
echo "source tar safety fixture tests: PASS"

[ -s "$ARCHIVE" ] || {
    echo "FAIL: source tar missing or empty: $ARCHIVE" >&2
    exit 1
}
validate_archive "$ARCHIVE"
