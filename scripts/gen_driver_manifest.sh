#!/bin/bash
# dist/wlan/opt/wlan/driver 의 *.ko 메타데이터(modinfo)와 wlan-driver-v2 소스 버전을
# DRIVER_MANIFEST.md 로 스냅샷 생성한다(약식 이력 관리).
#
# 바이너리 .ko 는 .gitignore 대상이라 git 으로 추적하지 않는다. 대신 이 manifest 만 추적해
# "배포 패키지에 어떤 드라이버가 들어갔는지"를 약식으로 남긴다.
#
# 사용: scripts/gen_driver_manifest.sh [wlan-driver-v2_경로]
#   기본 wlan-driver-v2 경로: ../wlan-driver-v2 (repo 형제 디렉토리)
#
# 멱등: 매 실행 시 manifest 를 덮어쓴다. 생성 날짜/시간은 일부러 기록하지 않는다 —
#       .ko 가 그대로면 manifest 도 동일해야(불필요한 매 커밋 diff 방지) 하기 때문.
# 종료코드: 항상 0. 약식 도구이므로 환경 미비(.ko/modinfo/소스 repo 부재) 시
#           경고만 남기고 가능한 범위까지만 기록한다(커밋을 막지 않기 위함).

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DRIVER_DIR="${REPO_ROOT}/dist/wlan/opt/wlan/driver"
MANIFEST="${DRIVER_DIR}/DRIVER_MANIFEST.md"
DRIVER_V2="${1:-${REPO_ROOT}/../wlan-driver-v2}"

if [ ! -d "${DRIVER_DIR}" ]; then
    echo "gen_driver_manifest: driver 디렉토리 없음 (${DRIVER_DIR}) — skip" >&2
    exit 0
fi

if ! command -v modinfo >/dev/null 2>&1; then
    echo "gen_driver_manifest: modinfo 없음 — manifest 갱신 skip" >&2
    exit 0
fi

# .ko 목록 (driver 직하 + debug/), driver 디렉토리 기준 상대경로로
cd "${DRIVER_DIR}" || exit 0
mapfile -t KOS < <(find . -maxdepth 2 -name '*.ko' | sed 's|^\./||' | sort)
if [ "${#KOS[@]}" -eq 0 ]; then
    echo "gen_driver_manifest: .ko 없음 — skip" >&2
    exit 0
fi

# wlan-driver-v2 소스 버전 (없으면 unknown)
SRC_DESC="unknown"
SRC_HEAD="unknown"
if git -C "${DRIVER_V2}" rev-parse --git-dir >/dev/null 2>&1; then
    SRC_DESC="$(git -C "${DRIVER_V2}" describe --tags --always --dirty 2>/dev/null || echo unknown)"
    SRC_HEAD="$(git -C "${DRIVER_V2}" rev-parse --short HEAD 2>/dev/null || echo unknown)"
else
    echo "gen_driver_manifest: wlan-driver-v2 repo 없음 (${DRIVER_V2}) — 소스 버전 unknown" >&2
fi

# modinfo 단일 필드 추출 (markdown 표 보호: 파이프/CR 정리)
field() { modinfo -F "$2" "$1" 2>/dev/null | head -1 | tr -d '\r' | sed 's/|/\//g'; }

{
    echo "# 드라이버 빌드 manifest"
    echo
    echo "> \`scripts/gen_driver_manifest.sh\` 자동 생성 — 수동 편집 금지."
    echo "> 바이너리 \`.ko\` 는 .gitignore 대상이며, 이 파일이 배포된 드라이버의 약식 이력이다."
    echo
    echo "- 소스(wlan-driver-v2): \`${SRC_DESC}\` (HEAD ${SRC_HEAD})"
    echo "- 대상 디렉토리: \`dist/wlan/opt/wlan/driver\`"
    echo
    echo "| 파일 | version | srcversion | vermagic |"
    echo "|------|---------|------------|----------|"
    for ko in "${KOS[@]}"; do
        v="$(field "${ko}" version)"
        s="$(field "${ko}" srcversion)"
        m="$(field "${ko}" vermagic)"
        echo "| ${ko} | ${v:-?} | ${s:-?} | ${m:-?} |"
    done
} > "${MANIFEST}"

echo "gen_driver_manifest: ${MANIFEST#${REPO_ROOT}/} 갱신 (소스 ${SRC_DESC})"
exit 0
