#!/bin/bash

BASEDIR=${PWD}
echo "Script location: ${BASEDIR}"

# Build wlan-bridge binaries
echo "Building wlan-bridge binaries..."
if [ ! -d "${BASEDIR}/wlan-bridge/dumb" ]; then
    echo "Error: wlan-bridge/dumb directory not found. Please initialize submodule with: git submodule update --init --recursive"
    exit 1
fi
cd ${BASEDIR}/wlan-bridge/dumb
make clean
make
if [ $? -ne 0 ]; then
    echo "Error: Failed to build wlan-bridge binaries"
    exit 1
fi
cd ${BASEDIR}
echo "Build completed successfully"

# Create wlan-bridge directory structure
mkdir -p ${BASEDIR}/dist/wlan/usr/local/wlan-bridge/dumb
mkdir -p ${BASEDIR}/dist/wlan/usr/local/wlan-bridge/scripts
mkdir -p ${BASEDIR}/dist/wlan/usr/local/wlan-bridge/docs

# Verify binaries exist before copying
if [ ! -f "${BASEDIR}/wlan-bridge/dumb/bin/dumb" ]; then
    echo "Error: dumb binary not found at ${BASEDIR}/wlan-bridge/dumb/bin/dumb"
    exit 1
fi
if [ ! -f "${BASEDIR}/wlan-bridge/dumb/bin/dumb-tpacket" ]; then
    echo "Error: dumb-tpacket binary not found at ${BASEDIR}/wlan-bridge/dumb/bin/dumb-tpacket"
    exit 1
fi

# Copy wlan-bridge binaries
echo "Copying binaries..."
cp ${BASEDIR}/wlan-bridge/dumb/bin/dumb ${BASEDIR}/dist/wlan/usr/local/wlan-bridge/dumb/ || { echo "Error: Failed to copy dumb binary"; exit 1; }
cp ${BASEDIR}/wlan-bridge/dumb/bin/dumb-tpacket ${BASEDIR}/dist/wlan/usr/local/wlan-bridge/dumb/ || { echo "Error: Failed to copy dumb-tpacket binary"; exit 1; }
cp ${BASEDIR}/wlan-bridge/dumb/README.md ${BASEDIR}/dist/wlan/usr/local/wlan-bridge/dumb/ || { echo "Error: Failed to copy README.md"; exit 1; }
cp ${BASEDIR}/wlan-bridge/dumb/wifi_bridge@.service ${BASEDIR}/dist/wlan/usr/local/wlan-bridge/dumb/ || { echo "Error: Failed to copy wifi_bridge@.service"; exit 1; }

# Copy wlan-bridge scripts and docs
cp -a ${BASEDIR}/wlan-bridge/scripts/. ${BASEDIR}/dist/wlan/usr/local/wlan-bridge/scripts/ || { echo "Error: Failed to copy scripts"; exit 1; }
cp -a ${BASEDIR}/wlan-bridge/docs/. ${BASEDIR}/dist/wlan/usr/local/wlan-bridge/docs/ || { echo "Error: Failed to copy docs"; exit 1; }

# Also update usr/local/bin with latest binaries
cp ${BASEDIR}/wlan-bridge/dumb/bin/dumb ${BASEDIR}/dist/wlan/usr/local/bin/ || { echo "Error: Failed to copy dumb to /usr/local/bin"; exit 1; }
cp ${BASEDIR}/wlan-bridge/dumb/bin/dumb-tpacket ${BASEDIR}/dist/wlan/usr/local/bin/ || { echo "Error: Failed to copy dumb-tpacket to /usr/local/bin"; exit 1; }

# Create config directory and default config.json
# This config is used by wifi_init.sh to configure network interfaces
mkdir -p ${BASEDIR}/dist/wlan/usr/local/etc
if [ ! -f ${BASEDIR}/dist/wlan/usr/local/etc/config.json ]; then
    cat > ${BASEDIR}/dist/wlan/usr/local/etc/config.json << 'CONFIGEOF'
{
    "mlan0": {
        "Frequency": "5GHz",
        "enabled": true
    },
    "mlan1": {
        "Frequency": "2.4GHz",
        "enabled": false
    },
    "eth0": {
        "enabled": true
    }
}
CONFIGEOF

    # Validate generated JSON
    if command -v jq >/dev/null 2>&1; then
        if ! jq empty ${BASEDIR}/dist/wlan/usr/local/etc/config.json 2>/dev/null; then
            echo "Error: Generated config.json is invalid JSON"
            exit 1
        fi
        echo "config.json validated successfully"
    else
        echo "Warning: jq not found, skipping JSON validation"
    fi
fi
mkdir -p ${BASEDIR}/release

cd ${BASEDIR}/dist
#rm -rf ${BASEDIR}/release
#cp -R ${BASEDIR}/dist ${BASEDIR}/release

#cd ${BASEDIR}/release
version=$(cat ../dist/wlan/DEBIAN/control| grep Version |grep -v ^$#| cut -d':' -f2 | cut -d',' -f1 | tr -d '"' | tr -d '\r\n' | tr -d ' ')
package=$(cat ../dist/wlan/DEBIAN/control| grep Package |grep -v ^$#| cut -d':' -f2 | cut -d',' -f1 | tr -d '"' | tr -d '\r\n' | tr -d ' ')
echo version:$version
dpkg -b wlan ${BASEDIR}/release/wlan.deb
cp ${BASEDIR}/release/wlan.deb ${BASEDIR}/release/$package-$version.deb
tar --exclude="release" -cvf ${BASEDIR}/release/wlan-package.tar -C ${BASEDIR} .
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
