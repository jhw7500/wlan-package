#!/bin/bash
set -euo pipefail

BASEDIR=${PWD}
echo "Script location: ${BASEDIR}"

# 오래 걸리는 cross-build 전에 설정 drift·schema·단위/shell 회귀를 먼저 차단한다.
bash "${BASEDIR}/scripts/validate_release.sh" pre \
    || { echo "Error: pre-build release gate failed" >&2; exit 1; }

# Build wlan-bridge binaries (wbridge)
echo "Building wbridge binaries..."
if [ ! -d "${BASEDIR}/wlan-bridge/wbridge" ]; then
    echo "Error: wlan-bridge/wbridge directory not found. Please verify directory name or submodule."
    exit 1
fi

WBRIDGE_DIR="${BASEDIR}/wlan-bridge/wbridge"
MAKE_FOR_IMX8="${WBRIDGE_DIR}/make-for-imx8"
MAKE_FOR_IMX93="${WBRIDGE_DIR}/make-for-imx93"

cd "${WBRIDGE_DIR}" || { echo "Error: cannot enter ${WBRIDGE_DIR}" >&2; exit 1; }
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

cd "${BASEDIR}" || { echo "Error: cannot return to ${BASEDIR}" >&2; exit 1; }
echo "Build completed successfully"

# Create wlan-bridge directory structure
mkdir -p "${BASEDIR}/dist/wlan/usr/local/wlan-bridge/"{wbridge,debug}
rm -rf "${BASEDIR}/dist/wlan/usr/local/wlan-bridge/scripts" \
       "${BASEDIR}/dist/wlan/usr/local/wlan-bridge/docs"
mkdir -p "${BASEDIR}/dist/wlan/usr/local/wlan-bridge/"{scripts,docs}

# Never mix artifacts from a previous native/cross build. Known boards prefer
# suffixed binaries in postinst, so retaining an old suffix can silently select
# stale code over the binary built in this invocation.
find "${BASEDIR}/dist/wlan/usr/local/wlan-bridge/wbridge" -maxdepth 1 \
    \( -type f -o -type l \) \
    \( -name 'wbridge' -o -name 'wbridge_*' -o -name 'wbridge-tpacket' -o -name 'wbridge-tpacket_*' \) \
    -delete
find "${BASEDIR}/dist/wlan/usr/local/wlan-bridge/debug" -maxdepth 1 \
    \( -type f -o -type l \) \
    \( -name 'wbridge' -o -name 'wbridge_*' -o -name 'wbridge-tpacket' -o -name 'wbridge-tpacket_*' \) \
    -delete

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
# Thermal state handling is WLAN-package-owned because it consumes the JSON
# SSoT; keep it alongside the bridge scripts after rebuilding the clean stage.
cp "${BASEDIR}/dist/wlan/usr/local/scripts/wifi_thermal_state_update.sh" \
   "${BASEDIR}/dist/wlan/usr/local/wlan-bridge/scripts/wifi_thermal_state_update.sh" \
    || { echo "Error: Failed to stage WLAN thermal script"; exit 1; }

# Build wlan-opc (OPC-side control daemon + VHL CLI simulator)
# 산출물: wlan-opc/build/<arch>/{opcd/opcd,vhlctl/vhlctl}, wlan-opc/opcd/opcd.service
# 설치 트리: /usr/local/opc/{bin/opcd, bin/vhlctl, opcd.service}
# postinst가 /etc/systemd/system/opcd.service symlink + enable 처리한다.
WLAN_OPC_DIR="${BASEDIR}/wlan-opc"
if [ -d "${WLAN_OPC_DIR}" ]; then
    echo "Building wlan-opc..."
    cd "${WLAN_OPC_DIR}" || { echo "Error: cannot enter ${WLAN_OPC_DIR}" >&2; exit 1; }
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
    cd "${BASEDIR}" || { echo "Error: cannot return to ${BASEDIR}" >&2; exit 1; }

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
    cd "${VHLD_DIR}" || { echo "Error: cannot enter ${VHLD_DIR}" >&2; exit 1; }
    make clean
    if [ "${HOST_ARCH}" = "aarch64" ] || [ "${HOST_ARCH}" = "arm64" ]; then
        make release || { echo "Error: Failed to build vhld"; exit 1; }
    else
        make cross || { echo "Error: Failed to cross-build vhld"; exit 1; }
    fi
    cd "${BASEDIR}" || { echo "Error: cannot return to ${BASEDIR}" >&2; exit 1; }
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

# source tree를 직접 dpkg에 넘기지 않는다. 임시 stage에서 개발 산출물/테스트를 제거하고
# mode를 정규화한 뒤 root-owner-group package를 만들며, 검증 성공 전 release 파일을 덮지 않는다.
PKG_STAGE=$(mktemp -d)
CANDIDATE_DEB="${BASEDIR}/release/.wlan.deb.candidate.$$"
CANDIDATE_VERSIONED_DEB="${BASEDIR}/release/.${package}-${version}.deb.candidate.$$"
CANDIDATE_TAR="${BASEDIR}/release/.wlan-package.tar.candidate.$$"
CANDIDATE_SUMS="${BASEDIR}/release/.SHA256SUMS.candidate.$$"
cleanup_package_stage() {
    rm -rf "${PKG_STAGE}"
    rm -f "${CANDIDATE_DEB}" "${CANDIDATE_VERSIONED_DEB}" "${CANDIDATE_TAR}" "${CANDIDATE_SUMS}"
}
trap cleanup_package_stage EXIT
cp -a "${BASEDIR}/dist/wlan" "${PKG_STAGE}/wlan"

find "${PKG_STAGE}/wlan" -type d \
    \( -name .omc -o -name .pytest_cache -o -name __pycache__ -o -name test -o -name tests \) \
    -prune -exec rm -rf {} +
find "${PKG_STAGE}/wlan" -type f \
    \( -name '*.pyc' -o -name '*_test.sh' -o -name '*_test.py' -o -name '.gitignore' \) -delete
find "${PKG_STAGE}/wlan" -type d -name tmp -prune -exec rm -rf {} +
chmod -R a-s,go-w "${PKG_STAGE}/wlan"
chmod 0600 "${PKG_STAGE}/wlan/opt/wlan/config/wpa_supplicant/wpa_supplicant-mlan0.conf" \
           "${PKG_STAGE}/wlan/opt/wlan/config/wpa_supplicant/wpa_supplicant-mlan1.conf"
chmod 0644 "${PKG_STAGE}/wlan/opt/wlan/config/wifi_init_conf.json"

dpkg-deb --build --root-owner-group "${PKG_STAGE}/wlan" "${CANDIDATE_DEB}" || {
    echo "Error: failed to build candidate package" >&2
    exit 1
}
chmod 0644 "${CANDIDATE_DEB}" || {
    echo "Error: failed to normalize candidate package mode" >&2
    exit 1
}
bash "${BASEDIR}/scripts/validate_release.sh" package "${CANDIDATE_DEB}" || {
    echo "Error: candidate package failed the release gate" >&2
    exit 1
}
cp "${CANDIDATE_DEB}" "${CANDIDATE_VERSIONED_DEB}" || {
    echo "Error: failed to stage versioned package candidate" >&2
    exit 1
}

echo "Creating sanitized source tarball: ${BASEDIR}/release/wlan-package.tar"

# This is a source/build handoff, not a checkout snapshot.  A positive
# allowlist prevents local agent state, target evidence, private working docs,
# and newly-created top-level paths from silently becoming release artifacts.
SOURCE_ARCHIVE_MEMBERS=()
while IFS= read -r member; do
    SOURCE_ARCHIVE_MEMBERS+=("$member")
done < <(python3 - "${BASEDIR}/scripts/source_archive_manifest.txt" <<'PY'
import posixpath
import sys

files = []
parents = set()
for raw in open(sys.argv[1], encoding="utf-8"):
    path = raw.strip()
    if not path or path.startswith("#"):
        continue
    files.append(path)
    parent = posixpath.dirname(path)
    while parent:
        parents.add(parent)
        parent = posixpath.dirname(parent)
for member in sorted(parents | set(files)):
    print(member)
PY
)

tar --format=posix \
    --sort=name \
    --mtime="@${SOURCE_DATE_EPOCH:-0}" \
    --pax-option=delete=atime,delete=ctime \
    --numeric-owner \
    --owner=0 \
    --group=0 \
    --mode='u-s,g-s,go-w' \
    --no-recursion \
    -cf "${CANDIDATE_TAR}" -C "${BASEDIR}" "${SOURCE_ARCHIVE_MEMBERS[@]}" || {
        echo "Error: failed to create candidate source tarball" >&2
        exit 1
    }
chmod 0644 "${CANDIDATE_TAR}" || {
    echo "Error: failed to normalize candidate source tarball mode" >&2
    exit 1
}

bash "${BASEDIR}/scripts/package_tar_test.sh" "${CANDIDATE_TAR}" || {
    echo "Error: candidate source tarball failed the release gate" >&2
    exit 1
}

(
    cd "${BASEDIR}/release" || exit 1
    sha256sum \
        "$(basename "${CANDIDATE_DEB}")" \
        "$(basename "${CANDIDATE_VERSIONED_DEB}")" \
        "$(basename "${CANDIDATE_TAR}")" \
        | sed \
            -e "s#$(basename "${CANDIDATE_DEB}")#wlan.deb#" \
            -e "s#$(basename "${CANDIDATE_VERSIONED_DEB}")#${package}-${version}.deb#" \
            -e "s#$(basename "${CANDIDATE_TAR}")#wlan-package.tar#" \
        > "${CANDIDATE_SUMS}"
) || {
    echo "Error: failed to stage release checksum manifest" >&2
    exit 1
}
if [ "$(wc -l < "${CANDIDATE_SUMS}")" -ne 3 ] \
    || [ "$(awk '{print $2}' "${CANDIDATE_SUMS}" | LC_ALL=C sort -u | wc -l)" -ne 3 ]; then
    echo "Error: staged release checksum manifest is incomplete" >&2
    exit 1
fi
chmod 0644 "${CANDIDATE_SUMS}" || {
    echo "Error: failed to normalize release checksum manifest mode" >&2
    exit 1
}

# Publish only after the complete release set has passed its gates. Every
# destination is replaced by rename, so readers never observe a truncated file.
# Consumers that require set-level atomicity should pin the published hashes;
# POSIX has no multi-file atomic rename primitive.
mv -f "${CANDIDATE_DEB}" "${BASEDIR}/release/wlan.deb" || {
    echo "Error: failed to publish validated package" >&2
    exit 1
}
mv -f "${CANDIDATE_VERSIONED_DEB}" "${BASEDIR}/release/${package}-${version}.deb" || {
    echo "Error: failed to publish versioned package" >&2
    exit 1
}
mv -f "${CANDIDATE_TAR}" "${BASEDIR}/release/wlan-package.tar" || {
    echo "Error: failed to publish validated source tarball" >&2
    exit 1
}
mv -f "${CANDIDATE_SUMS}" "${BASEDIR}/release/SHA256SUMS" || {
    echo "Error: failed to publish release checksum manifest" >&2
    exit 1
}
echo "Created: ${BASEDIR}/release/wlan-package.tar"
