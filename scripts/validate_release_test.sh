#!/bin/bash
set -euo pipefail

REPO=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
VALIDATE="$REPO/scripts/validate_release.sh"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
PKG="$WORK/pkg"
SOURCE_NETWORK="$REPO/dist/wlan/opt/wlan/config/systemd/network/22-eth0.network"

# Git stores only executable vs non-executable and `git archive` materializes a
# non-executable file as 0664.  The source gate must accept that representation;
# the package gate below still requires the shipped payload to be exactly 0644.
if ! (
    original_mode=$(stat -c '%a' "$SOURCE_NETWORK")
    trap 'chmod "$original_mode" "$SOURCE_NETWORK"' EXIT
    chmod 0664 "$SOURCE_NETWORK"
    # shellcheck source=validate_release.sh
    source "$VALIDATE"
    validate_source_product_defaults >/dev/null
); then
    echo "FAIL: non-executable Git source mode 0664 was rejected" >&2
    exit 1
fi

if (
    original_mode=$(stat -c '%a' "$SOURCE_NETWORK")
    trap 'chmod "$original_mode" "$SOURCE_NETWORK"' EXIT
    chmod 0755 "$SOURCE_NETWORK"
    # shellcheck source=validate_release.sh
    source "$VALIDATE"
    validate_source_product_defaults >/dev/null 2>&1
); then
    echo "FAIL: executable factory network source was accepted" >&2
    exit 1
fi

make_tree() {
    rm -rf "$PKG"
    mkdir -p "$PKG/DEBIAN" \
        "$PKG/opt/wlan/config/wpa_supplicant" \
        "$PKG/opt/wlan/config/systemd/network" \
        "$PKG/usr/lib/firmware/cts" \
        "$PKG/usr/share/doc/wlan-proc/nxp-imx-firmware"
    # Package identity is part of the release contract.  Keep the positive
    # fixture version-agnostic by deriving it from the source control file.
    cp "$REPO/dist/wlan/DEBIAN/control" "$PKG/DEBIAN/control"
    for rel in config templates preinst postinst prerm postrm; do
        cp -p "$REPO/dist/wlan/DEBIAN/$rel" "$PKG/DEBIAN/$rel"
    done
    while IFS= read -r rel; do
        [ -n "$rel" ] || continue
        mkdir -p "$PKG/$(dirname "$rel")"
        if [ "$rel" = "DEBIAN/payload-manifest.txt" ]; then
            cp "$REPO/dist/wlan/$rel" "$PKG/$rel"
        else
            printf 'fixture\n' > "$PKG/$rel"
        fi
    done < "$REPO/dist/wlan/DEBIAN/payload-manifest.txt"

    cp "$REPO/dist/wlan/opt/wlan/config/systemd/network/22-eth0.network" \
        "$PKG/opt/wlan/config/systemd/network/22-eth0.network"
    cp "$REPO/dist/wlan/usr/lib/firmware/cts/sd9098_wlan_v1.bin" \
        "$PKG/usr/lib/firmware/cts/sd9098_wlan_v1.bin"
    cp "$REPO/dist/wlan/usr/share/doc/wlan-proc/nxp-imx-firmware/"* \
        "$PKG/usr/share/doc/wlan-proc/nxp-imx-firmware/"
    mkdir -p "$PKG/etc/systemd/system"
    cp "$REPO/dist/wlan/etc/systemd/system/wifi_init.service" \
        "$PKG/etc/systemd/system/wifi_init.service"
    for rel in \
        usr/local/scripts/wifi_config_backup.sh \
        usr/local/scripts/wifi_cal_backup.sh \
        usr/local/scripts/wifi_apply_enabled.sh \
        usr/local/scripts/wifi_init.sh \
        usr/local/scripts/wifi_services.sh; do
        cp "$REPO/dist/wlan/$rel" "$PKG/$rel"
        chmod 0755 "$PKG/$rel"
    done
    for rel in \
        usr/local/opc/bin/opcd \
        usr/local/opc/bin/vhlctl \
        usr/local/vhl_daemon/vhld \
        usr/local/wlan-bridge/wbridge/wbridge_imx8 \
        usr/local/wlan-bridge/wbridge/wbridge_imx93 \
        usr/local/wlan-bridge/wbridge/wbridge-tpacket_imx8 \
        usr/local/wlan-bridge/wbridge/wbridge-tpacket_imx93; do
        mkdir -p "$PKG/$(dirname "$rel")"
        cp "$REPO/dist/wlan/$rel" "$PKG/$rel"
        chmod 0755 "$PKG/$rel"
    done
    chmod -R a-s,go-w "$PKG"
    chmod 0600 "$PKG/opt/wlan/config/wpa_supplicant/"*.conf
    chmod 0644 "$PKG/opt/wlan/config/wifi_init_conf.json" \
        "$PKG/opt/wlan/config/systemd/network/22-eth0.network" \
        "$PKG/usr/lib/firmware/cts/sd9098_wlan_v1.bin"
    find "$PKG" -type d -exec chmod 0755 {} +
}

build() {
    dpkg-deb --build --root-owner-group "$PKG" "$1" >/dev/null
}

make_tree
build "$WORK/good.deb"
bash "$VALIDATE" package "$WORK/good.deb" >/dev/null

expect_metadata_rejected() {
    local field="$1" deb="$2" err
    err="$WORK/metadata-${field}.err"
    if bash "$VALIDATE" package "$deb" >/dev/null 2>"$err"; then
        echo "FAIL: mismatched package metadata accepted: $field" >&2
        exit 1
    fi
    if ! grep -Fq "package metadata mismatch: $field=" "$err"; then
        echo "FAIL: $field rejection did not identify the metadata mismatch" >&2
        cat "$err" >&2
        exit 1
    fi
}

make_tree
sed -i 's/^Package: .*/&-identity-mismatch/' "$PKG/DEBIAN/control"
build "$WORK/wrong-package.deb"
expect_metadata_rejected Package "$WORK/wrong-package.deb"

make_tree
sed -i 's/^Version: .*/&+identity-mismatch/' "$PKG/DEBIAN/control"
build "$WORK/wrong-version.deb"
expect_metadata_rejected Version "$WORK/wrong-version.deb"

make_tree
sed -i 's/^Architecture: .*/Architecture: all/' "$PKG/DEBIAN/control"
build "$WORK/wrong-architecture.deb"
expect_metadata_rejected Architecture "$WORK/wrong-architecture.deb"

mkdir -p "$PKG/opt/wlan/config"
printf '{}\n' > "$PKG/opt/wlan/config/config.json"
build "$WORK/config.deb"
if bash "$VALIDATE" package "$WORK/config.deb" >/dev/null 2>&1; then
    echo "FAIL: retired config.json accepted" >&2; exit 1
fi

make_tree
ln -s /etc/passwd "$PKG/opt/wlan/config/config.json"
build "$WORK/config-symlink.deb"
if bash "$VALIDATE" package "$WORK/config-symlink.deb" >/dev/null 2>&1; then
    echo "FAIL: retired config.json symlink accepted" >&2; exit 1
fi

make_tree
rm "$PKG/usr/local/opc/bin/opcd"
ln -s /bin/true "$PKG/usr/local/opc/bin/opcd"
build "$WORK/manifest-symlink.deb"
if bash "$VALIDATE" package "$WORK/manifest-symlink.deb" >/dev/null 2>&1; then
    echo "FAIL: manifest payload symlink accepted" >&2; exit 1
fi

make_tree
rm "$PKG/usr/local/opc/bin/opcd"
mkfifo "$PKG/usr/local/opc/bin/opcd"
build "$WORK/manifest-fifo.deb"
if bash "$VALIDATE" package "$WORK/manifest-fifo.deb" >/dev/null 2>&1; then
    echo "FAIL: manifest payload FIFO accepted" >&2; exit 1
fi

make_tree
: > "$PKG/usr/local/opc/bin/opcd"
chmod 0755 "$PKG/usr/local/opc/bin/opcd"
build "$WORK/empty-runtime-binary.deb"
if bash "$VALIDATE" package "$WORK/empty-runtime-binary.deb" >/dev/null 2>&1; then
    echo "FAIL: empty runtime binary accepted" >&2; exit 1
fi

make_tree
printf '#!/bin/sh\nexit 0\n' > "$PKG/usr/local/opc/bin/opcd"
chmod 0755 "$PKG/usr/local/opc/bin/opcd"
build "$WORK/non-elf-runtime-binary.deb"
if bash "$VALIDATE" package "$WORK/non-elf-runtime-binary.deb" >/dev/null 2>&1; then
    echo "FAIL: non-AArch64 runtime binary accepted" >&2; exit 1
fi

make_tree
python3 - <<'PY2' > "$PKG/usr/local/opc/bin/opcd"
import struct, sys
h = bytearray(64)
h[:4] = b'\x7fELF'
h[4:6] = b'\x02\x01'
struct.pack_into('<H', h, 16, 3)
struct.pack_into('<H', h, 18, 183)
struct.pack_into('<Q', h, 32, 64)
struct.pack_into('<H', h, 52, 64)
struct.pack_into('<H', h, 54, 56)
struct.pack_into('<H', h, 56, 1)
sys.stdout.buffer.write(h)
PY2
chmod 0644 "$PKG/usr/local/opc/bin/opcd"
build "$WORK/nonexec-runtime-binary.deb"
if bash "$VALIDATE" package "$WORK/nonexec-runtime-binary.deb" >/dev/null 2>&1; then
    echo "FAIL: non-executable runtime binary accepted" >&2; exit 1
fi

make_tree
python3 - <<'PY2' > "$PKG/usr/local/opc/bin/opcd"
import struct, sys
h = bytearray(64)
h[:4] = b'\x7fELF'
h[4:6] = b'\x02\x01'
struct.pack_into('<H', h, 16, 3)
struct.pack_into('<H', h, 18, 183)
struct.pack_into('<Q', h, 32, 64)
struct.pack_into('<H', h, 52, 64)
struct.pack_into('<H', h, 54, 56)
struct.pack_into('<H', h, 56, 1)
sys.stdout.buffer.write(h)
PY2
chmod 0755 "$PKG/usr/local/opc/bin/opcd"
build "$WORK/truncated-elf-runtime-binary.deb"
if bash "$VALIDATE" package "$WORK/truncated-elf-runtime-binary.deb" >/dev/null 2>&1; then
    echo "FAIL: truncated AArch64 ELF runtime binary accepted" >&2; exit 1
fi

make_tree
chmod 0644 "$PKG/usr/local/scripts/wifi_init.sh"
build "$WORK/nonexec-service-command.deb"
if bash "$VALIDATE" package "$WORK/nonexec-service-command.deb" >/dev/null 2>&1; then
    echo "FAIL: non-executable systemd service command accepted" >&2; exit 1
fi

make_tree
mkdir -p "$PKG/usr/local/wlan-bridge/wbridge"
cat > "$PKG/usr/local/wlan-bridge/wbridge/wifi_bridge@.service" <<'EOF'
[Service]
ExecStop=/usr/local/scripts/wifi_bridge_stop.sh
EOF
printf '#!/bin/sh\nexit 0\n' > "$PKG/usr/local/scripts/wifi_bridge_stop.sh"
chmod 0644 "$PKG/usr/local/scripts/wifi_bridge_stop.sh"
build "$WORK/nonexec-installed-unit-command.deb"
if bash "$VALIDATE" package "$WORK/nonexec-installed-unit-command.deb" >/dev/null 2>&1; then
    echo "FAIL: non-executable installed unit command accepted" >&2; exit 1
fi

make_tree
mkdir -p "$PKG/usr/local/wlan-bridge/wbridge"
cat > "$PKG/usr/local/wlan-bridge/wbridge/wifi_bridge@.service" <<'EOF'
[Service]
ExecStop=/usr/local/scripts/does-not-exist.sh
EOF
build "$WORK/missing-installed-unit-command.deb"
if bash "$VALIDATE" package "$WORK/missing-installed-unit-command.deb" >/dev/null 2>&1; then
    echo "FAIL: missing package-owned installed unit command accepted" >&2; exit 1
fi

make_tree
printf '\nprintf injected-control-script-marker\\n' >> "$PKG/DEBIAN/postinst"
build "$WORK/control-drift.deb"
if bash "$VALIDATE" package "$WORK/control-drift.deb" >/dev/null 2>&1; then
    echo "FAIL: maintainer-script drift accepted" >&2; exit 1
fi

make_tree
mkdir -p "$PKG/usr/local/__pycache__"
printf x > "$PKG/usr/local/__pycache__/x.pyc"
build "$WORK/dev.deb"
if bash "$VALIDATE" package "$WORK/dev.deb" >/dev/null 2>&1; then
    echo "FAIL: dev artifact accepted" >&2; exit 1
fi

make_tree
mkdir -p "$PKG/usr/local/runtime/.git" "$PKG/usr/local/runtime/.claude"
printf secret > "$PKG/usr/local/runtime/.git/config"
printf secret > "$PKG/usr/local/runtime/.claude/session.json"
printf secret > "$PKG/usr/local/runtime/target-credentials.txt"
chmod 0600 "$PKG/usr/local/runtime/.git/config" \
    "$PKG/usr/local/runtime/.claude/session.json" \
    "$PKG/usr/local/runtime/target-credentials.txt"
build "$WORK/unapproved-payload.deb"
if bash "$VALIDATE" package "$WORK/unapproved-payload.deb" >/dev/null 2>&1; then
    echo "FAIL: unapproved payload files accepted" >&2; exit 1
fi

make_tree
chmod 0664 "$PKG/opt/wlan/config/wifi_init_conf.json"
build "$WORK/writable.deb"
if bash "$VALIDATE" package "$WORK/writable.deb" >/dev/null 2>&1; then
    echo "FAIL: group-writable payload accepted" >&2; exit 1
fi

# A large listing must not turn the writable-file rejection into a false PASS
# through an early-exiting pipeline/SIGPIPE interaction.
make_tree
mkdir -p "$PKG/usr/share/wlan-release-gate-volume"
for i in $(seq 1 12000); do
    printf x > "$PKG/usr/share/wlan-release-gate-volume/$i"
done
chmod 0664 "$PKG/usr/share/wlan-release-gate-volume/"*
build "$WORK/many-writable.deb"
if bash "$VALIDATE" package "$WORK/many-writable.deb" >/dev/null 2>&1; then
    echo "FAIL: high-volume group-writable payload accepted" >&2; exit 1
fi

make_tree
printf x > "$PKG/usr/lib/setuid-helper"
chmod 4755 "$PKG/usr/lib/setuid-helper"
build "$WORK/setuid.deb"
if bash "$VALIDATE" package "$WORK/setuid.deb" >/dev/null 2>&1; then
    echo "FAIL: setuid payload accepted" >&2; exit 1
fi

make_tree
printf x > "$PKG/usr/lib/setgid-helper"
chmod 2755 "$PKG/usr/lib/setgid-helper"
build "$WORK/setgid.deb"
if bash "$VALIDATE" package "$WORK/setgid.deb" >/dev/null 2>&1; then
    echo "FAIL: setgid payload accepted" >&2; exit 1
fi

make_tree
printf 'not-p149.115\n' > "$PKG/usr/lib/firmware/cts/sd9098_wlan_v1.bin"
build "$WORK/wrong-fw.deb"
if bash "$VALIDATE" package "$WORK/wrong-fw.deb" >/dev/null 2>&1; then
    echo "FAIL: unexpected SDIO firmware accepted" >&2; exit 1
fi

make_tree
sed -i 's/Address=192\.168\.214\.5\/24/Address=192.168.1.1\/24/' \
    "$PKG/opt/wlan/config/systemd/network/22-eth0.network"
build "$WORK/wrong-factory-ip.deb"
if bash "$VALIDATE" package "$WORK/wrong-factory-ip.deb" >/dev/null 2>&1; then
    echo "FAIL: wrong factory eth0 address accepted" >&2; exit 1
fi

make_tree
chmod 0755 "$PKG/opt/wlan/config/systemd/network/22-eth0.network"
build "$WORK/executable-factory-network.deb"
if bash "$VALIDATE" package "$WORK/executable-factory-network.deb" >/dev/null 2>&1; then
    echo "FAIL: executable factory network template accepted" >&2; exit 1
fi

make_tree
rm -rf "$PKG/usr/share/doc/wlan-proc/nxp-imx-firmware"
build "$WORK/missing-fw-provenance.deb"
if bash "$VALIDATE" package "$WORK/missing-fw-provenance.deb" >/dev/null 2>&1; then
    echo "FAIL: package without NXP firmware license/provenance accepted" >&2; exit 1
fi

echo "release gate self-test: PASS"
