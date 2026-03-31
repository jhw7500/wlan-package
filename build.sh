#!/bin/bash

BASEDIR=${PWD}
echo "Script location: ${BASEDIR}"

# Build wlan-bridge binaries (wbridge)
echo "Building wbridge binaries..."
if [ ! -d "${BASEDIR}/wlan-bridge/wbridge" ]; then
    echo "Error: wlan-bridge/wbridge directory not found. Please verify directory name or submodule."
    exit 1
fi

WBRIDGE_DIR="${BASEDIR}/wlan-bridge/wbridge"
MAKE_FOR_IMX8="${WBRIDGE_DIR}/make-for-imx8"

cd "${WBRIDGE_DIR}"
make clean || { echo "Warning: make clean failed"; }

HOST_ARCH=$(uname -m)
echo "Host arch: ${HOST_ARCH}"

build_wbridge_release() {
    if make -n release >/dev/null 2>&1; then
        make release
    else
        make
    fi
}

build_wbridge_debug_optional() {
    if make -n debug >/dev/null 2>&1; then
        make debug || echo "Warning: Failed to build debug binaries"
    fi
}

if [ "${HOST_ARCH}" = "aarch64" ] || [ "${HOST_ARCH}" = "arm64" ]; then
    echo "Native build (target arch detected)"
    build_wbridge_release || { echo "Error: Failed to build wbridge binaries"; exit 1; }
    build_wbridge_debug_optional
else
    echo "Cross build (host != aarch64); using make-for-imx8"
    if [ ! -x "${MAKE_FOR_IMX8}" ]; then
        echo "Error: make-for-imx8 not found or not executable at ${MAKE_FOR_IMX8}" >&2
        exit 1
    fi

    "${MAKE_FOR_IMX8}" release || { echo "Error: Failed to cross-build wbridge binaries"; exit 1; }
    "${MAKE_FOR_IMX8}" debug || echo "Warning: Failed to cross-build debug binaries"
fi

cd "${BASEDIR}"
echo "Build completed successfully"

# Create wlan-bridge directory structure
mkdir -p "${BASEDIR}/dist/wlan/usr/local/wlan-bridge/"{wbridge,debug,scripts,docs}

# Verify binaries exist before copying
if [ ! -f "${BASEDIR}/wlan-bridge/wbridge/release/wbridge" ]; then
    echo "Error: wbridge binary not found at ${BASEDIR}/wlan-bridge/wbridge/release/wbridge"
    exit 1
fi
if [ ! -f "${BASEDIR}/wlan-bridge/wbridge/release/wbridge-tpacket" ]; then
    echo "Error: wbridge-tpacket binary not found at ${BASEDIR}/wlan-bridge/wbridge/release/wbridge-tpacket"
    exit 1
fi

# Copy wbridge binaries
echo "Copying binaries..."
cp "${BASEDIR}/wlan-bridge/wbridge/release/wbridge" "${BASEDIR}/dist/wlan/usr/local/wlan-bridge/wbridge/" || { echo "Error: Failed to copy wbridge binary"; exit 1; }
cp "${BASEDIR}/wlan-bridge/wbridge/release/wbridge-tpacket" "${BASEDIR}/dist/wlan/usr/local/wlan-bridge/wbridge/" || { echo "Error: Failed to copy wbridge-tpacket binary"; exit 1; }

# Copy debug binaries if present (optional)
for bin in wbridge wbridge-tpacket; do
    src_path="${BASEDIR}/wlan-bridge/wbridge/debug/${bin}"
    if [ -f "${src_path}" ]; then
        cp "${src_path}" "${BASEDIR}/dist/wlan/usr/local/wlan-bridge/debug/" || echo "Warning: Failed to copy debug ${bin} binary"
    else
        echo "Warning: debug ${bin} binary not found, skipping (${src_path})"
    fi
done

cp "${BASEDIR}/wlan-bridge/wbridge/README.md" "${BASEDIR}/dist/wlan/usr/local/wlan-bridge/wbridge/" || { echo "Error: Failed to copy README.md"; exit 1; }
cp "${BASEDIR}/wlan-bridge/wbridge/wifi_bridge@.service" "${BASEDIR}/dist/wlan/usr/local/wlan-bridge/wbridge/" || { echo "Error: Failed to copy wifi_bridge@.service"; exit 1; }

# Copy wlan-bridge scripts and docs
cp -a "${BASEDIR}/wlan-bridge/scripts/." "${BASEDIR}/dist/wlan/usr/local/wlan-bridge/scripts/" || { echo "Error: Failed to copy scripts"; exit 1; }
cp -a "${BASEDIR}/wlan-bridge/docs/." "${BASEDIR}/dist/wlan/usr/local/wlan-bridge/docs/" || { echo "Error: Failed to copy docs"; exit 1; }

# Build vhld (VHL Protocol Daemon)
echo "Building vhld..."
VHLD_DIR="${BASEDIR}/dist/wlan/usr/local/vhl_daemon"
if [ -f "${VHLD_DIR}/Makefile" ]; then
    cd "${VHLD_DIR}"
    make clean
    if [ "${HOST_ARCH}" = "aarch64" ] || [ "${HOST_ARCH}" = "arm64" ]; then
        make release || { echo "Error: Failed to build vhld"; exit 1; }
    else
        make cross || { echo "Error: Failed to cross-build vhld"; exit 1; }
    fi
    cd "${BASEDIR}"
    echo "vhld build completed"
else
    echo "Warning: vhld Makefile not found, skipping"
fi

mkdir -p "${BASEDIR}/release"
cd "${BASEDIR}/dist" || exit 1

CONTROL_FILE="${BASEDIR}/dist/wlan/DEBIAN/control"
version=$(awk -F': *' '$1 == "Version" {print $2; exit}' "${CONTROL_FILE}" | tr -d '"\r\n ')
package=$(awk -F': *' '$1 == "Package" {print $2; exit}' "${CONTROL_FILE}" | tr -d '"\r\n ')

if [ -z "${version}" ] || [ -z "${package}" ]; then
    echo "Error: Failed to parse Package/Version from ${CONTROL_FILE}" >&2
    exit 1
fi

echo "version:${version}"

# Temporarily move tmp directories out of dpkg build tree
STASH_DIR="${BASEDIR}/dist/.tmp-stash"
rm -rf "${STASH_DIR}"
TMP_DIRS=()
restore_tmp() {
    for rel in "${TMP_DIRS[@]}"; do
        [ -d "${STASH_DIR}/${rel}" ] && mv "${STASH_DIR}/${rel}" "${BASEDIR}/dist/wlan/${rel}"
    done
    rm -rf "${STASH_DIR}"
}
trap restore_tmp EXIT
while IFS= read -r -d '' d; do
    rel="${d#${BASEDIR}/dist/wlan/}"
    stash="${STASH_DIR}/${rel}"
    mkdir -p "$(dirname "${stash}")"
    mv "$d" "${stash}"
    TMP_DIRS+=("${rel}")
done < <(find "${BASEDIR}/dist/wlan" -type d -name tmp -print0)

dpkg -b wlan "${BASEDIR}/release/wlan.deb"

cp "${BASEDIR}/release/wlan.deb" "${BASEDIR}/release/${package}-${version}.deb"

echo "Creating package tarball: ${BASEDIR}/release/wlan-package.tar"
tar --exclude-vcs \
  --exclude="./release" \
  --exclude="./.worktrees" \
  --exclude="./tmp" \
  --exclude="*/tmp" \
  -cf "${BASEDIR}/release/wlan-package.tar" -C "${BASEDIR}" .
echo "Created: ${BASEDIR}/release/wlan-package.tar"
