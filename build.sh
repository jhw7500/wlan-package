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
MAKE_FOR_IMX93="${WBRIDGE_DIR}/make-for-imx93"

cd "${WBRIDGE_DIR}"
make clean || { echo "Warning: make clean failed"; }

HOST_ARCH=$(uname -m)
echo "Host arch: ${HOST_ARCH}"

# 산출물: release/wbridge_<board>, release/wbridge-tpacket_<board> 등.
# postinst가 보드 감지(BOARD_TYPE)해서 적절한 binary로 심볼릭 링크 생성.
build_wbridge_native() {
    if make -n release >/dev/null 2>&1; then
        make release
    else
        make
    fi
}

build_wbridge_debug_native_optional() {
    if make -n debug >/dev/null 2>&1; then
        make debug || echo "Warning: Failed to build debug binaries"
    fi
}

cross_build_board() {
    local board="$1"            # imx8 | imx93
    local script="$2"           # make-for-imx8 | make-for-imx93
    local suffix="_${board}"

    if [ ! -x "${script}" ]; then
        echo "Error: ${script} not found or not executable" >&2
        exit 1
    fi

    echo "[wbridge] cross-build ${board} (BOARD_SUFFIX=${suffix})"
    # NOTE: clean 호출하지 않음. Makefile이 OBJDIR_R/OBJDIR_D를 board별로 분리하므로
    # imx8 산출물(release/wbridge_imx8 등)과 imx93 산출물을 같은 release/에 공존 가능.
    BOARD_SUFFIX="${suffix}" "${script}" release \
        || { echo "Error: cross-build ${board} release failed"; exit 1; }
    BOARD_SUFFIX="${suffix}" "${script}" debug \
        || echo "Warning: cross-build ${board} debug failed (continuing)"
}

if [ "${HOST_ARCH}" = "aarch64" ] || [ "${HOST_ARCH}" = "arm64" ]; then
    echo "Native build (target arch detected) — single board binary (no suffix)"
    build_wbridge_native || { echo "Error: Failed to build wbridge binaries"; exit 1; }
    build_wbridge_debug_native_optional
else
    echo "Cross build (host != aarch64); building imx8 + imx93"
    cross_build_board "imx8"  "${MAKE_FOR_IMX8}"
    cross_build_board "imx93" "${MAKE_FOR_IMX93}"
fi

cd "${BASEDIR}"
echo "Build completed successfully"

# Create wlan-bridge directory structure
mkdir -p "${BASEDIR}/dist/wlan/usr/local/wlan-bridge/"{wbridge,debug,scripts,docs}

# Native vs cross 빌드에 따라 산출물 이름이 다르다.
# - native(aarch64): wbridge, wbridge-tpacket (suffix 없음)
# - cross: wbridge_imx8/_imx93, wbridge-tpacket_imx8/_imx93 (양쪽 다 빌드)
if [ "${HOST_ARCH}" = "aarch64" ] || [ "${HOST_ARCH}" = "arm64" ]; then
    WBRIDGE_BINS=("wbridge" "wbridge-tpacket")
else
    WBRIDGE_BINS=("wbridge_imx8" "wbridge-tpacket_imx8" "wbridge_imx93" "wbridge-tpacket_imx93")
fi

# Verify binaries exist before copying
for bin in "${WBRIDGE_BINS[@]}"; do
    src="${BASEDIR}/wlan-bridge/wbridge/release/${bin}"
    if [ ! -f "${src}" ]; then
        echo "Error: ${bin} binary not found at ${src}"
        exit 1
    fi
done

# Copy release binaries
echo "Copying binaries: ${WBRIDGE_BINS[*]}"
for bin in "${WBRIDGE_BINS[@]}"; do
    cp "${BASEDIR}/wlan-bridge/wbridge/release/${bin}" "${BASEDIR}/dist/wlan/usr/local/wlan-bridge/wbridge/" \
        || { echo "Error: Failed to copy ${bin}"; exit 1; }
done

# Copy debug binaries if present (optional)
for bin in "${WBRIDGE_BINS[@]}"; do
    src_path="${BASEDIR}/wlan-bridge/wbridge/debug/${bin}"
    if [ -f "${src_path}" ]; then
        cp "${src_path}" "${BASEDIR}/dist/wlan/usr/local/wlan-bridge/debug/" \
            || echo "Warning: Failed to copy debug ${bin} binary"
    else
        echo "Warning: debug ${bin} binary not found, skipping (${src_path})"
    fi
done

cp "${BASEDIR}/wlan-bridge/wbridge/README.md" "${BASEDIR}/dist/wlan/usr/local/wlan-bridge/wbridge/" || { echo "Error: Failed to copy README.md"; exit 1; }
cp "${BASEDIR}/wlan-bridge/wbridge/wifi_bridge@.service" "${BASEDIR}/dist/wlan/usr/local/wlan-bridge/wbridge/" || { echo "Error: Failed to copy wifi_bridge@.service"; exit 1; }

# Copy wlan-bridge scripts and docs
cp -a "${BASEDIR}/wlan-bridge/scripts/." "${BASEDIR}/dist/wlan/usr/local/wlan-bridge/scripts/" || { echo "Error: Failed to copy scripts"; exit 1; }
cp -a "${BASEDIR}/wlan-bridge/docs/." "${BASEDIR}/dist/wlan/usr/local/wlan-bridge/docs/" || { echo "Error: Failed to copy docs"; exit 1; }

# Build wlan-opc (OPC-side control daemon + VHL CLI simulator)
# 산출물: wlan-opc/build/<arch>/{opcd/opcd,vhlctl/vhlctl}, wlan-opc/opcd/opcd.service
# 설치 트리: /usr/local/opc/{bin/opcd, bin/vhlctl, opcd.service}
# postinst가 /etc/systemd/system/opcd.service symlink + enable 처리한다.
WLAN_OPC_DIR="${BASEDIR}/wlan-opc"
if [ -d "${WLAN_OPC_DIR}" ]; then
    echo "Building wlan-opc..."
    cd "${WLAN_OPC_DIR}"
    make clean || true
    # Device package MUST use the nxp platform backend (real /var/log/cantops JSON
    # link data, timesyncd NTP, dpkg-query firmware, wifi.sh radio apply). The
    # Makefile default PLATFORM=stub is host-test only and would ship an opcd that
    # returns canned/zero device-info and no-ops radio/IP apply. vhlctl is
    # platform-independent, so PLATFORM only affects opcd.
    #
    # Per-arch out-of-tree build: artifacts land in build/<arch>/. On an arm64
    # host the native build already yields target binaries; otherwise cross-build.
    # `make native`/`make arm64` select the toolchain internally (cc vs
    # aarch64-linux-gnu-*), so the old `CC=cc AR=ar` override is no longer needed.
    #
    # Capability probe (cf. build_wbridge_native's `make -n release`): tolerate an
    # un-bumped wlan-opc submodule that still ships the old in-tree Makefile with
    # no native/arm64 targets — fall back to the legacy invocation + source-tree
    # artifact paths so a staged rollout (submodule bumped after this script) works.
    if make -n native >/dev/null 2>&1; then
        if [ "${HOST_ARCH}" = "aarch64" ] || [ "${HOST_ARCH}" = "arm64" ]; then
            make native PLATFORM=nxp || { echo "Error: Failed to build wlan-opc (native)"; exit 1; }
            OPC_BUILD="build/native"
        else
            make arm64 PLATFORM=nxp || { echo "Error: Failed to cross-build wlan-opc"; exit 1; }
            OPC_BUILD="build/arm64"
        fi
        [ -n "${OPC_BUILD}" ] || { echo "Error: OPC_BUILD unset for arch ${HOST_ARCH}"; exit 1; }
    else
        echo "[wlan-opc] legacy Makefile (no per-arch targets) — in-tree build"
        if [ "${HOST_ARCH}" = "aarch64" ] || [ "${HOST_ARCH}" = "arm64" ]; then
            CC=cc AR=ar make PLATFORM=nxp || { echo "Error: Failed to build wlan-opc (native)"; exit 1; }
        else
            make PLATFORM=nxp || { echo "Error: Failed to cross-build wlan-opc"; exit 1; }
        fi
        OPC_BUILD="."
    fi
    cd "${BASEDIR}"

    OPC_DEST="${BASEDIR}/dist/wlan/usr/local/opc"
    mkdir -p "${OPC_DEST}/bin"
    cp "${WLAN_OPC_DIR}/${OPC_BUILD}/opcd/opcd"     "${OPC_DEST}/bin/opcd"     || { echo "Error: copy opcd";        exit 1; }
    cp "${WLAN_OPC_DIR}/${OPC_BUILD}/vhlctl/vhlctl" "${OPC_DEST}/bin/vhlctl"   || { echo "Error: copy vhlctl";      exit 1; }
    cp "${WLAN_OPC_DIR}/opcd/opcd.service"          "${OPC_DEST}/opcd.service" || { echo "Error: copy opcd.service"; exit 1; }
    echo "wlan-opc artifacts staged to ${OPC_DEST}"
else
    echo "Warning: wlan-opc submodule not found, skipping"
fi

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

# README.md Current Version이 control Version과 동기되어 있는지 점검 (경고만, 빌드는 계속).
# 막지 않는다 — 릴리스 담당자에게 README 갱신 누락을 상기시키는 용도.
readme_version=$(grep -oE 'Current Version:\*\* [0-9.]+' "${BASEDIR}/README.md" 2>/dev/null | grep -oE '[0-9.]+$')
if [ -n "${readme_version}" ] && [ "${readme_version}" != "${version}" ]; then
    echo "Warning: README.md Current Version(${readme_version}) != control Version(${version}) — README 버전 갱신 권장" >&2
fi

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
