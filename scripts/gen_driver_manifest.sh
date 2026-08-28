#!/bin/bash
# Board-qualified driver provenance and component lock generator.
#
# Usage:
#   scripts/gen_driver_manifest.sh --write <wlan-driver-v2 repo> <source ref>
#   scripts/gen_driver_manifest.sh --check
#
# `--write` deliberately requires an explicit immutable source ref. Inferring
# source identity from whichever Git repository happens to be nearby previously
# recorded wlan-package HEAD as wlan-driver-v2 provenance. The source layout
# and commit object are now validated before either tracked output is replaced.

set -euo pipefail

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(CDPATH='' cd -- "${SCRIPT_DIR}/.." && pwd)
TREE="${REPO_ROOT}/dist/wlan"
DRIVER_DIR="${TREE}/opt/wlan/driver"
MANIFEST="${DRIVER_DIR}/DRIVER_MANIFEST.md"
LOCK="${DRIVER_DIR}/DRIVER_COMPONENTS.sha256"
CANONICAL_SOURCE_REMOTE="https://github.com/jhw7500/wlan-driver-v2.git"

QUALIFIED_COMPONENTS=(
    opt/wlan/driver/mlan_imx93.ko
    opt/wlan/driver/moal_imx93.ko
    opt/wlan/bin/mlanutl_imx93
    usr/lib/firmware/cts/sd9098_wlan_v1.bin
)

usage() {
    echo "usage: $0 --write <wlan-driver-v2 repo> <source ref> | --check" >&2
    exit 64
}

validate_source_repo() {
    local repo=$1 ref=$2 required source_commit source_type source_remote
    local -a source_remote_refs=()
    git -C "$repo" rev-parse --git-dir >/dev/null 2>&1 || {
        echo "gen_driver_manifest: not a Git repository: $repo" >&2
        return 1
    }
    source_commit=$(git -C "$repo" rev-parse --verify "${ref}^{commit}" 2>/dev/null) || {
        echo "gen_driver_manifest: source ref is not a commit: $ref" >&2
        return 1
    }
    for required in \
        mlan/mlan_main.h \
        mlinux/moal_main.c \
        mapp/mlanutl/mlanutl.c; do
        source_type=$(git -C "$repo" cat-file -t "${source_commit}:${required}" 2>/dev/null || true)
        [ "$source_type" = "blob" ] || {
            echo "gen_driver_manifest: source commit does not track required file: $required" >&2
            return 1
        }
        [ -f "$repo/$required" ] || {
            echo "gen_driver_manifest: not wlan-driver-v2 (missing $required): $repo" >&2
            return 1
        }
    done

    source_remote=$(git -C "$repo" remote get-url origin 2>/dev/null || true)
    case "$source_remote" in
        "$CANONICAL_SOURCE_REMOTE"|"${CANONICAL_SOURCE_REMOTE%.git}"|\
        git@github.com:jhw7500/wlan-driver-v2.git|\
        ssh://git@github.com/jhw7500/wlan-driver-v2.git)
            ;;
        *)
            echo "gen_driver_manifest: origin is not canonical wlan-driver-v2: ${source_remote:-missing}" >&2
            return 1
            ;;
    esac

    mapfile -t source_remote_refs < <(
        git -C "$repo" for-each-ref --format='%(refname)' \
            --contains "$source_commit" refs/remotes/origin/
    )
    [ "${#source_remote_refs[@]}" -gt 0 ] || {
        echo "gen_driver_manifest: source commit is absent from local origin/* refs: $source_commit" >&2
        return 1
    }
}

canonical_source_remote() {
    local repo=$1 source_remote
    source_remote=$(git -C "$repo" remote get-url origin 2>/dev/null || true)
    case "$source_remote" in
        "$CANONICAL_SOURCE_REMOTE"|"${CANONICAL_SOURCE_REMOTE%.git}"|\
        git@github.com:jhw7500/wlan-driver-v2.git|\
        ssh://git@github.com/jhw7500/wlan-driver-v2.git)
            printf '%s\n' "$CANONICAL_SOURCE_REMOTE"
            ;;
        *)
            echo "gen_driver_manifest: origin is not canonical wlan-driver-v2: ${source_remote:-missing}" >&2
            return 1
            ;;
    esac
}

check_outputs() {
    TREE="$TREE" LOCK="$LOCK" MANIFEST="$MANIFEST" python3 - <<'PY'
import hashlib
import os
import re
from pathlib import Path, PurePosixPath

tree = Path(os.environ["TREE"])
lock = Path(os.environ["LOCK"])
manifest = Path(os.environ["MANIFEST"])
expected_paths = {
    "opt/wlan/driver/mlan_imx93.ko",
    "opt/wlan/driver/moal_imx93.ko",
    "opt/wlan/bin/mlanutl_imx93",
    "usr/lib/firmware/cts/sd9098_wlan_v1.bin",
}

if not lock.is_file() or lock.is_symlink():
    raise SystemExit(f"gen_driver_manifest: missing/non-regular component lock: {lock}")
if not manifest.is_file() or manifest.is_symlink():
    raise SystemExit(f"gen_driver_manifest: missing/non-regular driver manifest: {manifest}")

headers = {}
entries = {}
for number, raw in enumerate(lock.read_text(encoding="utf-8").splitlines(), 1):
    if not raw:
        continue
    if raw.startswith("# "):
        key, sep, value = raw[2:].partition(": ")
        if not sep or key in headers:
            raise SystemExit(f"gen_driver_manifest: malformed/duplicate lock header at line {number}")
        headers[key] = value
        continue
    match = re.fullmatch(r"([0-9a-f]{64})  ([^\s]+)", raw)
    if not match:
        raise SystemExit(f"gen_driver_manifest: malformed lock entry at line {number}")
    digest, rel = match.groups()
    path = PurePosixPath(rel)
    if path.is_absolute() or ".." in path.parts or rel in entries:
        raise SystemExit(f"gen_driver_manifest: unsafe/duplicate lock path: {rel}")
    entries[rel] = digest

if headers.get("source-repository") != "wlan-driver-v2":
    raise SystemExit("gen_driver_manifest: source-repository must be wlan-driver-v2")
if headers.get("source-remote") != "https://github.com/jhw7500/wlan-driver-v2.git":
    raise SystemExit("gen_driver_manifest: source-remote must be canonical wlan-driver-v2")
commit = headers.get("source-commit", "")
if not re.fullmatch(r"[0-9a-f]{40}", commit):
    raise SystemExit("gen_driver_manifest: source-commit must be a full 40-hex commit")
if headers.get("source-scope") != "declared commit tracks required layout and is contained by local origin/*; supplied outputs are external":
    raise SystemExit("gen_driver_manifest: source-scope is missing or ambiguous")
if headers.get("source-verification") != "supplied metadata matched; no remote/build attestation; exact board-qualified payload bytes locked below":
    raise SystemExit("gen_driver_manifest: source-verification is missing or ambiguous")
if set(entries) != expected_paths:
    missing = sorted(expected_paths - set(entries))
    extra = sorted(set(entries) - expected_paths)
    raise SystemExit(f"gen_driver_manifest: component lock set mismatch; missing={missing}, extra={extra}")

for rel, expected in sorted(entries.items()):
    path = tree / rel
    if not path.is_file() or path.is_symlink() or path.stat().st_size == 0:
        raise SystemExit(f"gen_driver_manifest: missing/empty/non-regular component: {path}")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise SystemExit(
            f"gen_driver_manifest: component sha256 mismatch: {rel}: {actual} != {expected}"
        )

text = manifest.read_text(encoding="utf-8")
if f"- 소스 commit: `{commit}`" not in text:
    raise SystemExit("gen_driver_manifest: manifest source commit differs from component lock")
if "- 소스 원격: `https://github.com/jhw7500/wlan-driver-v2.git`" not in text:
    raise SystemExit("gen_driver_manifest: manifest source remote differs from component lock")
if "supplied outputs are external" not in text:
    raise SystemExit("gen_driver_manifest: manifest source scope differs from component lock")
if "no remote/build attestation" not in text:
    raise SystemExit("gen_driver_manifest: manifest overstates source provenance")
for rel, digest in entries.items():
    if f"| {rel} | `{digest}` |" not in text:
        raise SystemExit(f"gen_driver_manifest: manifest is missing locked component: {rel}")
PY
}

write_outputs() {
    local source_repo=$1 source_ref=$2 source_commit source_desc source_remote
    local source_head lock_tmp manifest_tmp ko rel digest v s m source_ko field_name

    validate_source_repo "$source_repo" "$source_ref"
    command -v modinfo >/dev/null 2>&1 || {
        echo "gen_driver_manifest: modinfo is required for --write" >&2
        return 1
    }
    [ -d "$DRIVER_DIR" ] || {
        echo "gen_driver_manifest: driver directory missing: $DRIVER_DIR" >&2
        return 1
    }
    for rel in "${QUALIFIED_COMPONENTS[@]}"; do
        if [ ! -f "$TREE/$rel" ] || [ -L "$TREE/$rel" ] || [ ! -s "$TREE/$rel" ]; then
            echo "gen_driver_manifest: qualified component missing/empty/non-regular: $TREE/$rel" >&2
            return 1
        fi
    done

    source_commit=$(git -C "$source_repo" rev-parse --verify "${source_ref}^{commit}")
    source_head=$(git -C "$source_repo" rev-parse HEAD)
    [ "$source_head" = "$source_commit" ] || {
        echo "gen_driver_manifest: source checkout HEAD=$source_head does not match ref=$source_commit" >&2
        return 1
    }
    if ! git -C "$source_repo" diff --quiet --ignore-submodules -- \
        || ! git -C "$source_repo" diff --cached --quiet --ignore-submodules --; then
        echo "gen_driver_manifest: source checkout has tracked working-tree changes" >&2
        return 1
    fi

    # Bind the declared commit to a clean rebuild, not merely to a repository
    # name. Kernel build-id/debuglink bytes and embedded absolute source paths
    # are not reproducible across build directories, so compare the kernel's
    # source-derived metadata fields instead of pretending exact file identity.
    for ko in mlan_imx93.ko moal_imx93.ko; do
        source_ko="$source_repo/bin_wlan/$ko"
        [ -s "$source_ko" ] || {
            echo "gen_driver_manifest: clean-ref rebuild output missing: $source_ko" >&2
            return 1
        }
        for field_name in version srcversion vermagic; do
            [ "$(modinfo -F "$field_name" "$source_ko" 2>/dev/null)" = \
              "$(modinfo -F "$field_name" "$DRIVER_DIR/$ko" 2>/dev/null)" ] || {
                echo "gen_driver_manifest: clean-ref rebuild metadata mismatch: $ko/$field_name" >&2
                return 1
            }
        done
    done
    [ -s "$source_repo/bin_wlan/mlanutl_imx93" ] || {
        echo "gen_driver_manifest: clean-ref utility rebuild output missing" >&2
        return 1
    }
    PACKAGE_UTILITY="$TREE/opt/wlan/bin/mlanutl_imx93" \
        SOURCE_UTILITY="$source_repo/bin_wlan/mlanutl_imx93" python3 - <<'PY'
import os
import struct
from pathlib import Path

def normalized_elf(path):
    data = bytearray(Path(path).read_bytes())
    if data[:6] != b"\x7fELF\x02\x01":
        raise SystemExit(f"gen_driver_manifest: utility is not ELF64 little-endian: {path}")
    shoff = struct.unpack_from("<Q", data, 40)[0]
    shentsize = struct.unpack_from("<H", data, 58)[0]
    shnum = struct.unpack_from("<H", data, 60)[0]
    shstrndx = struct.unpack_from("<H", data, 62)[0]
    if shentsize < 64 or shnum == 0 or shoff + shentsize * shnum > len(data):
        raise SystemExit(f"gen_driver_manifest: malformed utility ELF sections: {path}")
    shstr = shoff + shentsize * shstrndx
    str_off = struct.unpack_from("<Q", data, shstr + 24)[0]
    str_size = struct.unpack_from("<Q", data, shstr + 32)[0]
    names = bytes(data[str_off:str_off + str_size])
    for index in range(shnum):
        section = shoff + index * shentsize
        name_off = struct.unpack_from("<I", data, section)[0]
        end = names.find(b"\0", name_off)
        name = names[name_off:end if end >= 0 else None]
        if name in {b".note.gnu.build-id", b".gnu_debuglink"}:
            offset = struct.unpack_from("<Q", data, section + 24)[0]
            size = struct.unpack_from("<Q", data, section + 32)[0]
            data[offset:offset + size] = b"\0" * size
    return bytes(data)

if normalized_elf(os.environ["PACKAGE_UTILITY"]) != normalized_elf(os.environ["SOURCE_UTILITY"]):
    raise SystemExit("gen_driver_manifest: clean-ref rebuilt mlanutl differs beyond build-id/debuglink")
PY

    source_desc=$(git -C "$source_repo" describe --tags --always "$source_commit" 2>/dev/null || printf '%s' "$source_commit")
    source_remote=$(canonical_source_remote "$source_repo")

    lock_tmp=$(mktemp "${DRIVER_DIR}/.DRIVER_COMPONENTS.sha256.XXXXXX")
    manifest_tmp=$(mktemp "${DRIVER_DIR}/.DRIVER_MANIFEST.md.XXXXXX")
    trap 'rm -f "$lock_tmp" "$manifest_tmp"' RETURN

    {
        echo "# source-repository: wlan-driver-v2"
        echo "# source-remote: $source_remote"
        echo "# source-commit: $source_commit"
        echo "# source-scope: declared commit tracks required layout and is contained by local origin/*; supplied outputs are external"
        echo "# source-verification: supplied metadata matched; no remote/build attestation; exact board-qualified payload bytes locked below"
        for rel in "${QUALIFIED_COMPONENTS[@]}"; do
            digest=$(sha256sum "$TREE/$rel" | awk '{print $1}')
            printf '%s  %s\n' "$digest" "$rel"
        done
    } > "$lock_tmp"

    mapfile -t KOS < <(find "$DRIVER_DIR" -maxdepth 2 -type f -name '*.ko' -printf '%P\n' | LC_ALL=C sort)
    [ "${#KOS[@]}" -gt 0 ] || {
        echo "gen_driver_manifest: no kernel modules found in $DRIVER_DIR" >&2
        return 1
    }

    field() {
        modinfo -F "$2" "$1" 2>/dev/null | head -1 | tr -d '\r' | sed 's/|/\//g'
    }

    {
        echo "# 드라이버 빌드 manifest"
        echo
        echo "> \`scripts/gen_driver_manifest.sh --write\` 자동 생성 — 수동 편집 금지."
        echo "> board-qualified imx93 4-component exact identity는 \`DRIVER_COMPONENTS.sha256\`가 강제한다."
        echo
        echo "- 소스 저장소: \`wlan-driver-v2\` (required layout tracked-object verified)"
        echo "- 소스 원격: \`$source_remote\`"
        echo "- 소스 설명: \`$source_desc\`"
        echo "- 소스 commit: \`$source_commit\`"
        echo "- 소스 범위: declared commit tracks required layout and is contained by local origin/*; supplied outputs are external"
        echo "- 소스 검증: supplied metadata matched; no remote/build attestation; exact board-qualified payload bytes locked below"
        echo "- 대상 디렉토리: \`dist/wlan/opt/wlan/driver\`"
        echo
        echo "## Kernel modules"
        echo
        echo "| 파일 | SHA-256 | version | srcversion | vermagic |"
        echo "|------|---------|---------|------------|----------|"
        for ko in "${KOS[@]}"; do
            digest=$(sha256sum "$DRIVER_DIR/$ko" | awk '{print $1}')
            v=$(field "$DRIVER_DIR/$ko" version)
            s=$(field "$DRIVER_DIR/$ko" srcversion)
            m=$(field "$DRIVER_DIR/$ko" vermagic)
            echo "| $ko | \`$digest\` | ${v:-?} | ${s:-?} | ${m:-?} |"
        done
        echo
        echo "## Board-qualified component lock"
        echo
        echo "| 패키지 경로 | SHA-256 |"
        echo "|-------------|---------|"
        for rel in "${QUALIFIED_COMPONENTS[@]}"; do
            digest=$(sha256sum "$TREE/$rel" | awk '{print $1}')
            echo "| $rel | \`$digest\` |"
        done
    } > "$manifest_tmp"

    chmod 0644 "$lock_tmp" "$manifest_tmp"
    mv -f "$lock_tmp" "$LOCK"
    mv -f "$manifest_tmp" "$MANIFEST"
    trap - RETURN
    check_outputs
    echo "gen_driver_manifest: wrote ${MANIFEST#"${REPO_ROOT}"/} and ${LOCK#"${REPO_ROOT}"/}"
}

case "${1:-}" in
    --write)
        [ "$#" -eq 3 ] || usage
        write_outputs "$2" "$3"
        ;;
    --check)
        [ "$#" -eq 1 ] || usage
        check_outputs
        ;;
    *) usage ;;
esac
