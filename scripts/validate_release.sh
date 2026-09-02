#!/bin/bash
set -euo pipefail

REPO=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
SDIO_FW_REL="usr/lib/firmware/cts/sd9098_wlan_v1.bin"
SDIO_FW_SHA256="7c3ef6e12d3cfc9bd638d1571ccf6ddd2e96e0ed179ec70664ccb1df0ba29e57"
FACTORY_ETH0_REL="opt/wlan/config/systemd/network/22-eth0.network"
FACTORY_ETH0_ADDRESS="192.168.1.1/24"
MLANUTL_IMX93_REL="opt/wlan/bin/mlanutl_imx93"
FW_DOC_DIR_REL="usr/share/doc/wlan-proc/nxp-imx-firmware"
FW_LICENSE_SHA256="3001cf84018c5cb10d183a678f6ec8a928c797616ba06b398d7ca93c0779aaa2"
FW_SCR_SHA256="a05d7e1bb43bd7f3a955f3ff5c4dba3c61a5515df6f4fc5bf150a370e413289e"
FW_SOURCE_SHA256="4dbbbeebe006653040a28669433e6d8fbf596291e77d8e6ecb2efe7010e82745"
PAYLOAD_MANIFEST_REL="DEBIAN/payload-manifest.txt"
DRIVER_COMPONENT_LOCK_REL="opt/wlan/driver/DRIVER_COMPONENTS.sha256"
WBRIDGE_RELEASE_DIR="usr/local/wlan-bridge/wbridge"
WBRIDGE_DEBUG_DIR="usr/local/wlan-bridge/debug"

is_generated_wbridge_path() {
    case "$1" in
        "$WBRIDGE_RELEASE_DIR"/wbridge|"$WBRIDGE_RELEASE_DIR"/wbridge-tpacket|\
        "$WBRIDGE_RELEASE_DIR"/wbridge_imx8|"$WBRIDGE_RELEASE_DIR"/wbridge_imx93|\
        "$WBRIDGE_RELEASE_DIR"/wbridge-tpacket_imx8|"$WBRIDGE_RELEASE_DIR"/wbridge-tpacket_imx93|\
        "$WBRIDGE_DEBUG_DIR"/wbridge|"$WBRIDGE_DEBUG_DIR"/wbridge-tpacket|\
        "$WBRIDGE_DEBUG_DIR"/wbridge_imx8|"$WBRIDGE_DEBUG_DIR"/wbridge_imx93|\
        "$WBRIDGE_DEBUG_DIR"/wbridge-tpacket_imx8|"$WBRIDGE_DEBUG_DIR"/wbridge-tpacket_imx93) return 0 ;;
        *) return 1 ;;
    esac
}

validate_source_product_defaults() {
    local fw="$REPO/dist/wlan/$SDIO_FW_REL"
    local network="$REPO/dist/wlan/$FACTORY_ETH0_REL"
    local mlanutl_imx93="$REPO/dist/wlan/$MLANUTL_IMX93_REL"
    local actual_hash actual_address actual_mode

    # The qualified modules, matching private-command utility, and firmware are
    # an atomic board-tested set.  Module binaries are gitignored, so version
    # strings alone cannot prevent an accidental local replacement.
    if ! bash "$REPO/scripts/gen_driver_manifest.sh" --check; then
        echo "release gate: driver provenance/component lock validation failed" >&2
        return 1
    fi

    [ -s "$fw" ] || { echo "release gate: missing/empty SDIO firmware: $fw" >&2; return 1; }
    actual_hash=$(sha256sum "$fw" | awk '{print $1}')
    [ "$actual_hash" = "$SDIO_FW_SHA256" ] || {
        echo "release gate: unexpected SDIO firmware sha256=$actual_hash" >&2
        return 1
    }

    [ -s "$network" ] || { echo "release gate: missing/empty factory eth0 template: $network" >&2; return 1; }
    actual_address=$(awk -F= '$1 == "Address" { print $2 }' "$network")
    [ "$actual_address" = "$FACTORY_ETH0_ADDRESS" ] || {
        echo "release gate: factory eth0 address=$actual_address, expected $FACTORY_ETH0_ADDRESS" >&2
        return 1
    }
    actual_mode=$(stat -c '%a' "$network")
    if (( (8#$actual_mode & 07111) != 0 )); then
        echo "release gate: factory eth0 source must not be executable or set-id: mode=$actual_mode" >&2
        return 1
    fi

    [ -f "$mlanutl_imx93" ] && [ ! -L "$mlanutl_imx93" ] && [ -s "$mlanutl_imx93" ] \
        && [ -x "$mlanutl_imx93" ] || {
        echo "release gate: missing/non-executable matching imx93 mlanutl: $mlanutl_imx93" >&2
        return 1
    }
    # 543 driver는 antcfg physical path와 host NSS intent를 별도 private command로
    # 제공한다. 구 utility를 staging하면 제품 verify가 user_htstream을 읽지 못해
    # wifi_init이 fail-closed 하므로 패키징 전에 ABI marker를 강제한다.
    LC_ALL=C grep -aFq 'antcfgnss' "$mlanutl_imx93" || {
        echo "release gate: imx93 mlanutl lacks required antcfgnss support: $mlanutl_imx93" >&2
        return 1
    }

    local rel expected
    for rel in \
        "$FW_DOC_DIR_REL/LICENSE.txt|$FW_LICENSE_SHA256" \
        "$FW_DOC_DIR_REL/SCR-imx-firmware.txt|$FW_SCR_SHA256" \
        "$FW_DOC_DIR_REL/firmware-source.json|$FW_SOURCE_SHA256"; do
        expected=${rel#*|}
        rel=${rel%%|*}
        [ -s "$REPO/dist/wlan/$rel" ] || {
            echo "release gate: missing/empty firmware notice: $rel" >&2
            return 1
        }
        actual_hash=$(sha256sum "$REPO/dist/wlan/$rel" | awk '{print $1}')
        [ "$actual_hash" = "$expected" ] || {
            echo "release gate: firmware notice hash mismatch: $rel" >&2
            return 1
        }
    done
}

validate_packaged_component_lock() {
    local deb=$1
    if ! bash "$REPO/scripts/gen_driver_manifest.sh" --check; then
        echo "release gate: source component lock is invalid before package comparison" >&2
        return 1
    fi
    REPO_ROOT="$REPO" PACKAGE_DEB="$deb" LOCK_REL="$DRIVER_COMPONENT_LOCK_REL" python3 - <<'PY'
import hashlib
import io
import os
import re
import subprocess
import tarfile
from pathlib import Path

repo = Path(os.environ["REPO_ROOT"])
lock_rel = os.environ["LOCK_REL"]
source_lock = repo / "dist/wlan" / lock_rel
expected_lock = source_lock.read_bytes()
payload = subprocess.check_output(["dpkg-deb", "--fsys-tarfile", os.environ["PACKAGE_DEB"]])

with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
    members = {m.name.removeprefix("./").rstrip("/"): m for m in archive.getmembers()}
    lock_member = members.get(lock_rel)
    if lock_member is None or not lock_member.isfile():
        raise SystemExit(f"release gate: packaged component lock missing/non-regular: {lock_rel}")
    stream = archive.extractfile(lock_member)
    packaged_lock = stream.read() if stream else b""
    if packaged_lock != expected_lock:
        raise SystemExit("release gate: packaged component lock differs from source")

    entries = {}
    for number, raw in enumerate(expected_lock.decode("utf-8").splitlines(), 1):
        if not raw or raw.startswith("# "):
            continue
        match = re.fullmatch(r"([0-9a-f]{64})  ([^\s]+)", raw)
        if not match:
            raise SystemExit(f"release gate: malformed source component lock line {number}")
        digest, rel = match.groups()
        entries[rel] = digest

    errors = []
    for rel, expected in sorted(entries.items()):
        member = members.get(rel)
        if member is None or not member.isfile() or member.size == 0:
            errors.append(f"qualified component missing/empty/non-regular: {rel}")
            continue
        stream = archive.extractfile(member)
        actual = hashlib.sha256(stream.read() if stream else b"").hexdigest()
        if actual != expected:
            errors.append(f"qualified component sha256 mismatch: {rel}: {actual} != {expected}")
    if errors:
        raise SystemExit("\n".join(f"release gate: {error}" for error in errors))
PY
}

validate_payload_manifest() {
    local tree="$REPO/dist/wlan"
    local manifest="$tree/$PAYLOAD_MANIFEST_REL"

    [ -s "$manifest" ] || {
        echo "release gate: missing/empty payload manifest: $manifest" >&2
        return 1
    }
    REPO_ROOT="$REPO" PAYLOAD_MANIFEST="$manifest" python3 - <<'PY'
import os
from pathlib import Path

root = Path(os.environ["REPO_ROOT"])
tree = root / "dist/wlan"
manifest = Path(os.environ["PAYLOAD_MANIFEST"])
expected = {
    line.strip() for line in manifest.read_text(encoding="utf-8").splitlines()
    if line.strip() and not line.lstrip().startswith("#")
}

excluded_components = {".omc", ".pytest_cache", "__pycache__", "tmp", "test", "tests"}
generated_wbridge_names = {
    "usr/local/wlan-bridge/wbridge/wbridge",
    "usr/local/wlan-bridge/wbridge/wbridge-tpacket",
    "usr/local/wlan-bridge/wbridge/wbridge_imx8",
    "usr/local/wlan-bridge/wbridge/wbridge_imx93",
    "usr/local/wlan-bridge/wbridge/wbridge-tpacket_imx8",
    "usr/local/wlan-bridge/wbridge/wbridge-tpacket_imx93",
    "usr/local/wlan-bridge/debug/wbridge",
    "usr/local/wlan-bridge/debug/wbridge-tpacket",
    "usr/local/wlan-bridge/debug/wbridge_imx8",
    "usr/local/wlan-bridge/debug/wbridge_imx93",
    "usr/local/wlan-bridge/debug/wbridge-tpacket_imx8",
    "usr/local/wlan-bridge/debug/wbridge-tpacket_imx93",
}
generated_runtime_names = generated_wbridge_names | {
    "usr/local/opc/bin/opcd",
    "usr/local/opc/bin/vhlctl",
    "usr/local/vhl_daemon/vhld",
}
actual = set()
wrong_type = []
for path in tree.rglob("*"):
    if not path.is_file() and not path.is_symlink():
        continue
    rel = path.relative_to(tree).as_posix()
    parts = path.relative_to(tree).parts
    if any(part in excluded_components for part in parts):
        continue
    if rel.startswith("DEBIAN/") and rel != "DEBIAN/payload-manifest.txt":
        continue
    if path.name == ".gitignore":
        continue
    if rel in generated_runtime_names:
        if path.is_symlink() or not path.is_file():
            wrong_type.append(rel)
        continue
    if path.suffix == ".pyc" or path.name.endswith(("_test.sh", "_test.py")):
        continue
    actual.add(rel)
    if rel in expected and (path.is_symlink() or not path.is_file()):
        wrong_type.append(rel)

missing = sorted(expected - actual)
unexpected = sorted(actual - expected)
if missing or unexpected or wrong_type:
    for item in missing[:20]:
        print(f"release gate: payload manifest missing source file: {item}", file=os.sys.stderr)
    for item in unexpected[:20]:
        print(f"release gate: unapproved payload source file: {item}", file=os.sys.stderr)
    for item in wrong_type[:20]:
        print(f"release gate: payload manifest source is not a regular file: {item}", file=os.sys.stderr)
    raise SystemExit(1)
PY
}

validate_control_archive() {
    local deb="$1" expected_dir actual_dir expected_names actual_names rel
    expected_dir=$(mktemp -d)
    actual_dir=$(mktemp -d)
    rmdir "$actual_dir"
    expected_names=$(mktemp)
    actual_names=$(mktemp)
    trap 'rm -rf "$expected_dir" "$actual_dir"; rm -f "$expected_names" "$actual_names"' RETURN

    for rel in control config templates preinst postinst prerm postrm payload-manifest.txt; do
        [ -f "$REPO/dist/wlan/DEBIAN/$rel" ] && [ ! -L "$REPO/dist/wlan/DEBIAN/$rel" ] || {
            echo "release gate: missing/non-regular source control member: $rel" >&2
            return 1
        }
        cp -p "$REPO/dist/wlan/DEBIAN/$rel" "$expected_dir/$rel"
        printf '%s\n' "$rel" >> "$expected_names"
    done

    dpkg-deb -e "$deb" "$actual_dir" || {
        echo "release gate: cannot extract package control archive" >&2
        return 1
    }
    find "$actual_dir" -mindepth 1 -maxdepth 1 -printf '%f\n' | LC_ALL=C sort > "$actual_names"
    LC_ALL=C sort -o "$expected_names" "$expected_names"
    cmp -s "$expected_names" "$actual_names" || {
        echo "release gate: expected control members:" >&2; cat "$expected_names" >&2
        echo "release gate: actual control members:" >&2; cat "$actual_names" >&2
        echo "release gate: package control member set differs from source" >&2
        return 1
    }
    while IFS= read -r rel; do
        [ -f "$actual_dir/$rel" ] && [ ! -L "$actual_dir/$rel" ] || {
            echo "release gate: package control member is not regular: $rel" >&2
            return 1
        }
        cmp -s "$expected_dir/$rel" "$actual_dir/$rel" || {
            echo "release gate: package control member differs from source: $rel" >&2
            return 1
        }
        case "$rel" in
            config|templates|preinst|postinst|prerm|postrm) expected_mode=755 ;;
            control) expected_mode=755 ;;
            *) expected_mode=644 ;;
        esac
        [ "$(stat -c '%a' "$actual_dir/$rel")" = "$expected_mode" ] || {
            echo "release gate: package control member mode differs from contract: $rel" >&2
            return 1
        }
    done < "$expected_names"
}

run_prebuild() {
    cd "$REPO"
    validate_source_product_defaults
    validate_payload_manifest
    python3 scripts/gen_config_defaults.py --check
    python3 - <<'PY'
import json
from jsonschema import Draft7Validator
schema = json.load(open("docs/wifi_init_conf.schema.json"))
conf = json.load(open("dist/wlan/opt/wlan/config/wifi_init_conf.json"))
Draft7Validator.check_schema(schema)
errors = list(Draft7Validator(schema).iter_errors(conf))
if errors:
    raise SystemExit("template/schema validation failed: " + "; ".join(e.message for e in errors[:10]))
PY
    python3 -m pytest dist/wlan/usr/local/logger/tests -q
    python3 -m pytest dist/wlan/usr/local/scripts/tests -q
    # QA 하네스도 게이트에 포함한다. 이 스위트는 실기 상태를 바꾸는 도구의
    # 안전 계약(복원·원격 절단 대응·스케줄 검증)을 고정하는데, 여기 없으면
    # 아무도 실행하지 않아 그 계약이 조용히 썩는다.
    python3 -m pytest scripts/qa -q

    local test
    for test in \
        dist/wlan/usr/local/scripts/update_mac_test.sh \
        dist/wlan/usr/local/scripts/wifi_cal_backup_test.sh \
        dist/wlan/usr/local/scripts/wifi_conf_preserve_test.sh \
        dist/wlan/usr/local/scripts/wifi_config_backup_test.sh \
        dist/wlan/usr/local/scripts/wifi_eth_peer_find_test.sh \
        dist/wlan/usr/local/scripts/wifi_factory_reset_test.sh \
        dist/wlan/usr/local/scripts/wifi_fw_config_test.sh \
        dist/wlan/usr/local/scripts/wifi_init_config_test.sh \
        dist/wlan/usr/local/scripts/wifi_link_reset_test.sh \
        dist/wlan/usr/local/scripts/wifi_secret_test.sh \
        dist/wlan/usr/local/scripts/wifi_wpa_conf_writer_test.sh \
        dist/wlan/usr/local/scripts/tests/test_fake_hwclock.sh \
        dist/wlan/usr/local/scripts/tests/test_wifi_eth_peer.sh \
        dist/wlan/usr/local/scripts/tests/test_wlan_link_lib.sh; do
        bash "$test"
    done

    while IFS= read -r -d '' test; do
        bash -n "$test"
    done < <(find dist/wlan -type f -name '*.sh' -print0)
}

validate_package() {
    local deb="$1" listing names actual_hash actual_address rel expected
    local source_control="$REPO/dist/wlan/DEBIAN/control"
    local field actual
    [ -f "$deb" ] && [ ! -L "$deb" ] && [ -s "$deb" ] || {
        echo "release gate: package must be a nonempty regular file: $deb" >&2
        return 1
    }
    local deb_mode
    deb_mode=$(stat -c '%a' "$deb")
    [ "$deb_mode" = "644" ] || {
        echo "release gate: package file mode=$deb_mode, expected 644" >&2
        return 1
    }
    [ -s "$source_control" ] || {
        echo "release gate: missing source control: $source_control" >&2
        return 1
    }

    # Artifact identity must match the source control file exactly.  Version
    # ordering is intentionally not used: an epoch/revision difference denotes
    # a different release artifact even when dpkg would consider it upgradeable.
    for field in Package Version Architecture; do
        expected=$(awk -v key="$field" '
            index($0, key ":") == 1 {
                sub(/^[^:]*:[[:space:]]*/, "")
                sub(/[[:space:]]+$/, "")
                print
                exit
            }
        ' "$source_control")
        [ -n "$expected" ] || {
            echo "release gate: source control missing $field" >&2
            return 1
        }
        if ! actual=$(dpkg-deb -f "$deb" "$field"); then
            echo "release gate: cannot read package field $field" >&2
            return 1
        fi
        [ "$actual" = "$expected" ] || {
            echo "release gate: package metadata mismatch: $field='$actual', expected '$expected'" >&2
            return 1
        }
    done
    validate_control_archive "$deb"
    validate_packaged_component_lock "$deb"

    listing=$(mktemp)
    names=$(mktemp)
    trap 'rm -f "$listing" "$names"' RETURN
    dpkg-deb -c "$deb" > "$listing"
    dpkg-deb --fsys-tarfile "$deb" | tar -tf - > "$names"

    if ! REPO_ROOT="$REPO" PACKAGE_DEB="$deb" python3 - <<'PY'
import io
import os
import posixpath
import subprocess
import tarfile
from pathlib import Path

repo = Path(os.environ["REPO_ROOT"])
expected = {
    line.strip() for line in (repo / "dist/wlan/DEBIAN/payload-manifest.txt")
        .read_text(encoding="utf-8").splitlines()
    if line.strip() and not line.lstrip().startswith("#")
}
expected.discard("DEBIAN/payload-manifest.txt")
generated_names = {
    "usr/local/wlan-bridge/wbridge/wbridge",
    "usr/local/wlan-bridge/wbridge/wbridge-tpacket",
    "usr/local/wlan-bridge/wbridge/wbridge_imx8",
    "usr/local/wlan-bridge/wbridge/wbridge_imx93",
    "usr/local/wlan-bridge/wbridge/wbridge-tpacket_imx8",
    "usr/local/wlan-bridge/wbridge/wbridge-tpacket_imx93",
    "usr/local/wlan-bridge/debug/wbridge",
    "usr/local/wlan-bridge/debug/wbridge-tpacket",
    "usr/local/wlan-bridge/debug/wbridge_imx8",
    "usr/local/wlan-bridge/debug/wbridge_imx93",
    "usr/local/wlan-bridge/debug/wbridge-tpacket_imx8",
    "usr/local/wlan-bridge/debug/wbridge-tpacket_imx93",
    "usr/local/opc/bin/opcd",
    "usr/local/opc/bin/vhlctl",
    "usr/local/vhl_daemon/vhld",
}
factory_reset_implementation = {
    "usr/local/scripts/factory_reset.sh",
    "usr/local/scripts/wifi_factory_reset_lib.sh",
}
payload = subprocess.check_output(["dpkg-deb", "--fsys-tarfile", os.environ["PACKAGE_DEB"]])
actual = set()
errors = []
counts = {}
with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
    for member in archive.getmembers():
        raw = member.name
        if raw.startswith("/"):
            errors.append(f"absolute packaged payload path: {raw!r}")
            continue
        while raw.startswith("./"):
            raw = raw[2:]
        name = raw.rstrip("/")
        if not name:
            continue
        canonical = posixpath.normpath(name)
        if canonical != name or canonical == ".." or canonical.startswith("../"):
            errors.append(f"non-canonical packaged payload path: {member.name!r}")
            continue
        counts[name] = counts.get(name, 0) + 1
        if member.isfile() and name not in generated_names:
            actual.add(name)
            if name in factory_reset_implementation:
                factory_reset = archive.extractfile(member)
                if factory_reset is not None and b"nginx" in factory_reset.read().lower():
                    errors.append("package factory reset must not manage nginx")
        elif not member.isfile() and not member.isdir():
            errors.append(f"packaged payload is not a regular file: {name}")
for name, count in sorted(counts.items()):
    if count != 1:
        errors.append(f"duplicate packaged payload member: {name} ({count})")
missing = sorted(expected - actual)
unexpected = sorted(actual - expected)
if missing or unexpected or errors:
    for item in missing[:20]:
        print(f"release gate: packaged payload missing manifest file: {item}", file=os.sys.stderr)
    for item in unexpected[:20]:
        print(f"release gate: unapproved packaged payload file: {item}", file=os.sys.stderr)
    for item in errors[:20]:
        print(f"release gate: {item}", file=os.sys.stderr)
    raise SystemExit(1)
PY
    then
        return 1
    fi


    local generated release_variant release_count path
    generated=$(mktemp)
    trap 'rm -f "$listing" "$names" "$generated"' RETURN
    : > "$generated"
    while IFS= read -r path; do
        path=${path#./}
        path=${path%/}
        is_generated_wbridge_path "$path" && printf '%s\n' "$path" >> "$generated"
    done < "$names"
    LC_ALL=C sort -u -o "$generated" "$generated"
    release_count=$(awk -v p="$WBRIDGE_RELEASE_DIR/" 'index($0,p)==1 {n++} END{print n+0}' "$generated")
    if grep -Fxq "$WBRIDGE_RELEASE_DIR/wbridge" "$generated"; then
        release_variant=native
        [ "$release_count" -eq 2 ] \
            && grep -Fxq "$WBRIDGE_RELEASE_DIR/wbridge-tpacket" "$generated" || {
                echo "release gate: incomplete/mixed native wbridge release set" >&2
                return 1
            }
    else
        release_variant=cross
        [ "$release_count" -eq 4 ] \
            && grep -Fxq "$WBRIDGE_RELEASE_DIR/wbridge_imx8" "$generated" \
            && grep -Fxq "$WBRIDGE_RELEASE_DIR/wbridge_imx93" "$generated" \
            && grep -Fxq "$WBRIDGE_RELEASE_DIR/wbridge-tpacket_imx8" "$generated" \
            && grep -Fxq "$WBRIDGE_RELEASE_DIR/wbridge-tpacket_imx93" "$generated" || {
                echo "release gate: incomplete/mixed cross wbridge release set" >&2
                return 1
            }
    fi

    if ! PACKAGE_DEB="$deb" GENERATED_NAMES="$generated" python3 - <<'PY'
import io
import os
import struct
import subprocess
import tarfile
from pathlib import Path

runtime = {
    "usr/local/opc/bin/opcd",
    "usr/local/opc/bin/vhlctl",
    "usr/local/vhl_daemon/vhld",
}
runtime.update(Path(os.environ["GENERATED_NAMES"]).read_text().splitlines())
payload = subprocess.check_output(["dpkg-deb", "--fsys-tarfile", os.environ["PACKAGE_DEB"]])
errors = []
with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
    members = {m.name.removeprefix("./").rstrip("/"): m for m in archive.getmembers()}
    for name in sorted(runtime):
        member = members.get(name)
        if member is None or not member.isfile() or member.size < 64:
            errors.append(f"runtime binary is missing/empty/non-regular: {name}")
            continue
        if not member.mode & 0o111:
            errors.append(f"runtime binary is not executable: {name}")
        stream = archive.extractfile(member)
        data = stream.read() if stream else b""
        header = data[:64]
        if len(header) < 64 or header[:4] != b"\x7fELF" or header[4] != 2 or header[5] != 1:
            errors.append(f"runtime binary is not 64-bit little-endian ELF: {name}")
            continue
        if struct.unpack_from("<H", header, 18)[0] != 183:
            errors.append(f"runtime binary is not AArch64: {name}")
            continue
        elf_type = struct.unpack_from("<H", header, 16)[0]
        phoff = struct.unpack_from("<Q", header, 32)[0]
        ehsize = struct.unpack_from("<H", header, 52)[0]
        phentsize = struct.unpack_from("<H", header, 54)[0]
        phnum = struct.unpack_from("<H", header, 56)[0]
        if elf_type not in (2, 3) or ehsize != 64 or phentsize < 56 or phnum < 1 \
                or phoff < 64 or phoff + phentsize * phnum > len(data):
            errors.append(f"runtime binary has invalid/truncated ELF program headers: {name}")
            continue
        has_load = any(
            struct.unpack_from("<I", data, phoff + i * phentsize)[0] == 1
            for i in range(phnum)
        )
        if not has_load:
            errors.append(f"runtime binary has no loadable ELF segment: {name}")
if errors:
    for error in errors:
        print(f"release gate: {error}", file=os.sys.stderr)
    raise SystemExit(1)
PY
    then
        return 1
    fi

    if awk '$2 != "root/root" { found=1 } END { exit found ? 0 : 1 }' "$listing"; then
        echo "release gate: non-root payload ownership" >&2
        awk '$2 != "root/root" { print }' "$listing" >&2
        return 1
    fi
    if awk '$1 !~ /^l/ && (substr($1,6,1)=="w" || substr($1,9,1)=="w") { found=1 } END { exit found ? 0 : 1 }' "$listing"; then
        echo "release gate: group/world-writable payload" >&2
        awk '$1 !~ /^l/ && (substr($1,6,1)=="w" || substr($1,9,1)=="w") { print }' "$listing" >&2
        return 1
    fi
    if awk '$1 !~ /^l/ && (substr($1,4,1) ~ /[sS]/ || substr($1,7,1) ~ /[sS]/) { found=1 } END { exit found ? 0 : 1 }' "$listing"; then
        echo "release gate: setuid/setgid payload" >&2
        awk '$1 !~ /^l/ && (substr($1,4,1) ~ /[sS]/ || substr($1,7,1) ~ /[sS]/) { print }' "$listing" >&2
        return 1
    fi
    if ! PACKAGE_DEB="$deb" python3 - <<'PY'
import io
import os
import re
import subprocess
import tarfile

payload = subprocess.check_output(["dpkg-deb", "--fsys-tarfile", os.environ["PACKAGE_DEB"]])
errors = []
with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
    members = {m.name.removeprefix("./").rstrip("/"): m for m in archive.getmembers()}
    required_cli_executables = {
        "usr/local/scripts/wifi_logger_control.sh",
    }
    for name in sorted(required_cli_executables):
        member = members.get(name)
        if member is None or not member.isfile() or not member.mode & 0o111:
            errors.append(f"CLI helper is missing or not executable: {name}")
    installed_unit_paths = {
        "opt/wlan/config/wpa_supplicant/wpa_supplicant@.service",
        "usr/local/opc/opcd.service",
        "usr/local/vhl_daemon/vhld.service",
        "usr/local/wlan-bridge/wbridge/wifi_bridge@.service",
    }
    units = [
        m for n, m in members.items()
        if m.isfile() and (n.startswith("etc/systemd/system/") or n in installed_unit_paths)
    ]
    for unit in units:
        stream = archive.extractfile(unit)
        text = stream.read().decode("utf-8", "replace") if stream else ""
        for line in text.splitlines():
            if not re.match(r"^(ExecStart|ExecStop|ExecReload|ExecStartPre|ExecStartPost)=", line):
                continue
            command = line.split("=", 1)[1].strip().lstrip("-+!:@").strip()
            if not command:
                continue
            token = command.split()[0]
            if not token.startswith("/"):
                continue
            name = token.lstrip("/")
            member = members.get(name)
            package_owned = token.startswith(("/usr/local/", "/opt/wlan/"))
            if package_owned and member is None:
                errors.append(f"systemd command is missing from package payload: {unit.name} -> {token}")
            elif member is not None and (not member.isfile() or not member.mode & 0o111):
                errors.append(f"systemd command is not an executable regular payload file: {unit.name} -> {token}")
if errors:
    for error in errors:
        print(f"release gate: {error}", file=os.sys.stderr)
    raise SystemExit(1)
PY
    then
        return 1
    fi
    if grep -Eq '/(\.omc|\.pytest_cache|__pycache__)(/|$)|\.pyc$|/tests?/|_test\.(sh|py)$' "$names"; then
        echo "release gate: development/test artifact packaged" >&2
        grep -E '/(\.omc|\.pytest_cache|__pycache__)(/|$)|\.pyc$|/tests?/|_test\.(sh|py)$' "$names" >&2
        return 1
    fi
    if grep -Eq '(usr/local/etc|opt/wlan/config)/config\.json/?$' "$names"; then
        echo "release gate: retired config.json packaged" >&2
        return 1
    fi

    local conf entry mode
    for conf in \
        ./opt/wlan/config/wpa_supplicant/wpa_supplicant-mlan0.conf \
        ./opt/wlan/config/wpa_supplicant/wpa_supplicant-mlan1.conf; do
        entry=$(awk -v p="$conf" '$NF == p {print; exit}' "$listing")
        [ -n "$entry" ] || { echo "release gate: missing $conf" >&2; return 1; }
        mode=${entry%% *}
        [ "$mode" = "-rw-------" ] || { echo "release gate: $conf mode=$mode, expected 0600" >&2; return 1; }
    done
    entry=$(awk '$NF == "./opt/wlan/config/wifi_init_conf.json" {print; exit}' "$listing")
    [ "${entry%% *}" = "-rw-r--r--" ] || {
        echo "release gate: wifi_init_conf.json must be 0644" >&2
        return 1
    }

    if ! actual_hash=$(dpkg-deb --fsys-tarfile "$deb" \
        | tar -xOf - "./$SDIO_FW_REL" \
        | sha256sum | awk '{print $1}'); then
        echo "release gate: missing/unreadable ./$SDIO_FW_REL" >&2
        return 1
    fi
    [ "$actual_hash" = "$SDIO_FW_SHA256" ] || {
        echo "release gate: unexpected packaged SDIO firmware sha256=$actual_hash" >&2
        return 1
    }

    if ! actual_address=$(dpkg-deb --fsys-tarfile "$deb" \
        | tar -xOf - "./$FACTORY_ETH0_REL" \
        | awk -F= '$1 == "Address" { print $2 }'); then
        echo "release gate: missing/unreadable ./$FACTORY_ETH0_REL" >&2
        return 1
    fi
    [ "$actual_address" = "$FACTORY_ETH0_ADDRESS" ] || {
        echo "release gate: packaged factory eth0 address=$actual_address, expected $FACTORY_ETH0_ADDRESS" >&2
        return 1
    }
    entry=$(awk -v p="./$FACTORY_ETH0_REL" '$NF == p {print; exit}' "$listing")
    [ "${entry%% *}" = "-rw-r--r--" ] || {
        echo "release gate: ./$FACTORY_ETH0_REL must be 0644" >&2
        return 1
    }

    for rel in \
        "$FW_DOC_DIR_REL/LICENSE.txt|$FW_LICENSE_SHA256" \
        "$FW_DOC_DIR_REL/SCR-imx-firmware.txt|$FW_SCR_SHA256" \
        "$FW_DOC_DIR_REL/firmware-source.json|$FW_SOURCE_SHA256"; do
        expected=${rel#*|}
        rel=${rel%%|*}
        if ! actual_hash=$(dpkg-deb --fsys-tarfile "$deb" \
            | tar -xOf - "./$rel" \
            | sha256sum | awk '{print $1}'); then
            echo "release gate: missing/unreadable firmware notice: ./$rel" >&2
            return 1
        fi
        [ "$actual_hash" = "$expected" ] || {
            echo "release gate: packaged firmware notice hash mismatch: ./$rel" >&2
            return 1
        }
    done

    dpkg-deb --fsys-tarfile "$deb" | tar -tf - >/dev/null
    echo "release package gate: PASS ($deb)"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    case "${1:-}" in
        pre) run_prebuild ;;
        package) [ $# -eq 2 ] || { echo "usage: $0 package <deb>" >&2; exit 64; }; validate_package "$2" ;;
        *) echo "usage: $0 <pre|package <deb>>" >&2; exit 64 ;;
    esac
fi
