#!/bin/bash

BASEDIR=${PWD}
echo "Script location: ${BASEDIR}"

# Build wlan-bridge binaries
echo "Building wlan-bridge binaries..."
if [ ! -d "${BASEDIR}/wlan-bridge/dumb" ]; then
    echo "Error: wlan-bridge/dumb directory not found. Please initialize submodule with: git submodule update --init --recursive"
    exit 1
fi

DUMB_DIR="${BASEDIR}/wlan-bridge/dumb"
MAKE_FOR_IMX8="${DUMB_DIR}/make-for-imx8"

cd "${DUMB_DIR}"
make clean || { echo "Warning: make clean failed"; }

HOST_ARCH=$(uname -m)
echo "Host arch: ${HOST_ARCH}"

build_dumb_release() {
    if make -n release >/dev/null 2>&1; then
        make release
    else
        make
    fi
}

build_dumb_debug_optional() {
    if make -n debug >/dev/null 2>&1; then
        make debug || echo "Warning: Failed to build debug binaries"
    fi
}

if [ "${HOST_ARCH}" = "aarch64" ] || [ "${HOST_ARCH}" = "arm64" ]; then
    echo "Native build (target arch detected)"
    build_dumb_release || { echo "Error: Failed to build wlan-bridge binaries"; exit 1; }
    build_dumb_debug_optional
else
    echo "Cross build (host != aarch64); using make-for-imx8"
    if [ ! -x "${MAKE_FOR_IMX8}" ]; then
        echo "Error: make-for-imx8 not found or not executable at ${MAKE_FOR_IMX8}" >&2
        echo "Hint: update submodule (git submodule update --init --recursive)" >&2
        echo "Hint: or build natively on target (aarch64)" >&2
        exit 1
    fi

    "${MAKE_FOR_IMX8}" release || { echo "Error: Failed to cross-build wlan-bridge binaries"; exit 1; }
    "${MAKE_FOR_IMX8}" debug || echo "Warning: Failed to cross-build debug binaries"
fi

cd "${BASEDIR}"
echo "Build completed successfully"

# Create wlan-bridge directory structure
mkdir -p "${BASEDIR}/dist/wlan/usr/local/wlan-bridge/"{dumb,debug,scripts,docs}

# Verify binaries exist before copying
if [ ! -f "${BASEDIR}/wlan-bridge/dumb/release/dumb" ]; then
    echo "Error: dumb binary not found at ${BASEDIR}/wlan-bridge/dumb/release/dumb"
    exit 1
fi
if [ ! -f "${BASEDIR}/wlan-bridge/dumb/release/dumb-tpacket" ]; then
    echo "Error: dumb-tpacket binary not found at ${BASEDIR}/wlan-bridge/dumb/release/dumb-tpacket"
    exit 1
fi

# Copy wlan-bridge binaries
echo "Copying binaries..."
cp "${BASEDIR}/wlan-bridge/dumb/release/dumb" "${BASEDIR}/dist/wlan/usr/local/wlan-bridge/dumb/" || { echo "Error: Failed to copy dumb binary"; exit 1; }
cp "${BASEDIR}/wlan-bridge/dumb/release/dumb-tpacket" "${BASEDIR}/dist/wlan/usr/local/wlan-bridge/dumb/" || { echo "Error: Failed to copy dumb-tpacket binary"; exit 1; }

# Copy debug binaries if present (optional)
for bin in dumb dumb-tpacket; do
    src_path="${BASEDIR}/wlan-bridge/dumb/debug/${bin}"
    if [ -f "${src_path}" ]; then
        cp "${src_path}" "${BASEDIR}/dist/wlan/usr/local/wlan-bridge/debug/" || echo "Warning: Failed to copy debug ${bin} binary"
    else
        echo "Warning: debug ${bin} binary not found, skipping (${src_path})"
    fi
done

cp "${BASEDIR}/wlan-bridge/dumb/README.md" "${BASEDIR}/dist/wlan/usr/local/wlan-bridge/dumb/" || { echo "Error: Failed to copy README.md"; exit 1; }
cp "${BASEDIR}/wlan-bridge/dumb/wifi_bridge@.service" "${BASEDIR}/dist/wlan/usr/local/wlan-bridge/dumb/" || { echo "Error: Failed to copy wifi_bridge@.service"; exit 1; }

# Copy wlan-bridge scripts and docs
cp -a "${BASEDIR}/wlan-bridge/scripts/." "${BASEDIR}/dist/wlan/usr/local/wlan-bridge/scripts/" || { echo "Error: Failed to copy scripts"; exit 1; }
cp -a "${BASEDIR}/wlan-bridge/docs/." "${BASEDIR}/dist/wlan/usr/local/wlan-bridge/docs/" || { echo "Error: Failed to copy docs"; exit 1; }

# Create config directory and default config.json
# This config is used by wifi_init.sh to configure network interfaces
mkdir -p "${BASEDIR}/dist/wlan/usr/local/etc"
if [ ! -f "${BASEDIR}/dist/wlan/usr/local/etc/config.json" ]; then
    cat > "${BASEDIR}/dist/wlan/usr/local/etc/config.json" << 'CONFIGEOF'
{
    "mlan0": {
        "Frequency": "auto",
        "enabled": true
    },
    "mlan1": {
        "Frequency": "auto",
        "enabled": false
    },
    "eth0": {
        "enabled": true
    }
}
CONFIGEOF
fi

# Always validate config.json (whether newly created or existing)
if command -v jq >/dev/null 2>&1; then
    if ! jq empty "${BASEDIR}/dist/wlan/usr/local/etc/config.json" 2>/dev/null; then
        echo "Error: config.json is invalid JSON"
        exit 1
    fi
    echo "config.json validated successfully"
else
    echo "Warning: jq not found, skipping JSON validation"
fi
mkdir -p "${BASEDIR}/release"

cd "${BASEDIR}/dist" || exit 1
#rm -rf ${BASEDIR}/release
#cp -R ${BASEDIR}/dist ${BASEDIR}/release

#cd ${BASEDIR}/release
CONTROL_FILE="${BASEDIR}/dist/wlan/DEBIAN/control"
version=$(awk -F': *' '$1 == "Version" {print $2; exit}' "${CONTROL_FILE}" | tr -d '"\r\n ')
package=$(awk -F': *' '$1 == "Package" {print $2; exit}' "${CONTROL_FILE}" | tr -d '"\r\n ')

if [ -z "${version}" ] || [ -z "${package}" ]; then
    echo "Error: Failed to parse Package/Version from ${CONTROL_FILE}" >&2
    exit 1
fi

echo "version:${version}"
dpkg -b wlan "${BASEDIR}/release/wlan.deb"
cp "${BASEDIR}/release/wlan.deb" "${BASEDIR}/release/${package}-${version}.deb"

echo "Creating package tarball: ${BASEDIR}/release/wlan-package.tar"
tar --exclude="release" -cf "${BASEDIR}/release/wlan-package.tar" -C "${BASEDIR}" .
echo "Created: ${BASEDIR}/release/wlan-package.tar"
#tar -cvf ${BASEDIR}/release/wlan-package.tar ${BASEDIR}/*

:<<'END'
#### create_upgrade_file ######################
if [ -d ${BASEDIR}/release/upgrade_file ]; then
    rm -rf ${BASEDIR}/release/upgrade_file
fi
cp -R ${BASEDIR}/upgrade_file ${BASEDIR}/release
cp ${BASEDIR}/release/$package-$version.deb ${BASEDIR}/release/upgrade_file/

${BASEDIR}/dist/pim//opt/pim/bin/change_line.sh 'PIM_DEB_FILE="'"$package-$version.deb"'"' "PIM_DEB_FILE=" ${BASEDIR}/release/upgrade_file/setup.sh > /dev/null
${BASEDIR}/dist/pim/opt/cis/bin/release_tool.sh create ${BASEDIR}/release/upgrade_file

if [ ! -f ${BASEDIR}/release/upgrade_file.zip ]; then
    echo "upgrade_file.zip not exist."
    exit 1
fi
ugrade_zip_file=$(echo ${package} | tr [a-z] [A-Z] | tr '-' '_')"_release_"$(echo ${version} | tr '.' '_')".zip"
mv ${BASEDIR}/release/upgrade_file.zip ${BASEDIR}/release/${ugrade_zip_file}

echo "create ${ugrade_zip_file}"

#### create old upgrade file #################
cd ${BASEDIR}/release/upgrade_file/
mv setup.sh pim_update.sh
ugrade_old_zip_file="pim_update_"$(echo ${version})".tar"
tar cvf "../${ugrade_old_zip_file}" ./

echo "create ${ugrade_old_zip_file}"
END
