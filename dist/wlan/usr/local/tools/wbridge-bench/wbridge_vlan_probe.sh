#!/bin/bash
# wbridge_vlan_probe.sh — VLAN passthrough 시나리오 실측 검증 (standalone)
#
# 4-addr STA 환경에서 AP가 VLAN tagged frame을 wbridge mlan0까지 forwarding하는지 확인.
# 시나리오 A (1-addr, AP strip) vs B (4-addr, transparent passthrough) 자동 판정.
#
# 검증 절차:
#   1) driver/iface 정보 수집 (iw, ip -d, /sys/module/moal/*)
#   2) mlan0 + eth0 동시 capture (duration N초)
#   3) tagged frame 수 / total frame 수 / VLAN ID 분포 분석
#   4) verdict: scenario A/B + 양쪽 NIC passthrough 여부
#
# Usage:
#   wbridge_vlan_probe.sh --duration=30 [--out=FILE] [--text|--json]
#   wbridge_vlan_probe.sh --info-only                  # driver 정보만 빠르게
#
# 의존성: tcpdump, iw, ip, awk, (text 모드: jq)
#
# Plan SC: 보강 — wbridge VLAN 시나리오 실측
# Design Ref: docs/04-report/baseline/wbridge-engine-modes-explained.md §5.1 §D

set -euo pipefail
TAG=$(basename "$0")
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
. "${SCRIPT_DIR}/lib/_common.sh"

usage() {
    cat <<EOF
Usage: ${TAG} (--duration=N | --info-only) [options]

Modes:
  --duration=N    N초간 mlan0/eth0 capture → VLAN frame 비율 + VID 분포 + verdict
  --info-only     driver/iface 정보만 빠르게 (capture skip)

Options:
  --wireless=IFACE  무선 NIC (default mlan0)
  --wired=IFACE     유선 NIC (default eth0)
  --out=FILE        결과 파일
  --json            JSON 출력 (default)
  --text            사람용 표 출력
  -h, --help

Verdict 판정:
  scenario_A : mlan0 vlan_frames == 0
               → AP가 1-addr STA로 보고 VLAN tag strip
               → wbridge에서 VLAN 정책 적용 불가
  scenario_B : mlan0 vlan_frames > 0
               → AP가 4-addr 또는 VLAN-aware transparent forwarding
               → wbridge에서 VLAN passthrough/매핑/정책 적용 가능

전제: AP에서 VLAN tagged 트래픽이 발생 중이어야 의미 있는 결과.
      검증 중 AP에서 multi-VLAN 트래픽을 흘리세요 (예: VLAN 10/20에서 ping/iperf).
EOF
}

DURATION=""
INFO_ONLY=0
WIRELESS_IFACE="${WBRIDGE_WIRELESS_IFACE:-mlan0}"
WIRED_IFACE="${WBRIDGE_WIRED_IFACE:-eth0}"
OUT_FILE=""
FORMAT="json"

while [ $# -gt 0 ]; do
    case "$1" in
        --duration=*) DURATION="${1#*=}" ;;
        --duration)   shift; DURATION="${1:-}" ;;
        --info-only)  INFO_ONLY=1 ;;
        --wireless=*) WIRELESS_IFACE="${1#*=}" ;;
        --wireless)   shift; WIRELESS_IFACE="${1:-}" ;;
        --wired=*)    WIRED_IFACE="${1#*=}" ;;
        --wired)      shift; WIRED_IFACE="${1:-}" ;;
        --out=*)      OUT_FILE="${1#*=}" ;;
        --out)        shift; OUT_FILE="${1:-}" ;;
        --json)       FORMAT="json" ;;
        --text)       FORMAT="text" ;;
        -h|--help)    usage; exit 0 ;;
        *) die "$EXIT_CONFIG_MISSING" "unknown arg: $1 (--help for usage)" ;;
    esac
    shift
done

# 모드 검증
if [ -z "$DURATION" ] && [ "$INFO_ONLY" -eq 0 ]; then
    die "$EXIT_CONFIG_MISSING" "--duration=N or --info-only required"
fi
if [ -n "$DURATION" ]; then
    case "$DURATION" in
        ''|*[!0-9]*) die "$EXIT_CONFIG_MISSING" "--duration must be positive integer" ;;
    esac
    [ "$DURATION" -gt 0 ] || die "$EXIT_CONFIG_MISSING" "--duration must be > 0"
fi

require_command awk
require_command ip
# capture mode만 root + tcpdump 필요 (info-only는 일반 사용자 가능)
if [ "$INFO_ONLY" -eq 0 ]; then
    require_root
    require_command tcpdump
fi

# ============================================================
# 1) driver / iface 정보 수집 (JSON 조각)
# ============================================================
gather_driver_info() {
    local iface="$1"
    local iftype="unknown"
    local addr_mode="unknown"
    local link_state="unknown"
    local mtu="0"

    # iw 정보 (wireless 우선)
    if command -v iw >/dev/null 2>&1 && iw "$iface" info >/dev/null 2>&1; then
        iftype=$(iw "$iface" info 2>/dev/null | awk '/^[[:space:]]*type / {print $2; exit}')
        addr_mode=$(iw "$iface" info 2>/dev/null | awk '/4addr/ {print $0; exit}' | sed 's/^[[:space:]]*//')
    fi

    # ip link 정보
    if ip -d link show dev "$iface" >/dev/null 2>&1; then
        link_state=$(ip link show dev "$iface" 2>/dev/null | awk '/state/ {for(i=1;i<=NF;i++) if($i=="state") print $(i+1); exit}')
        mtu=$(ip link show dev "$iface" 2>/dev/null | awk '/mtu/ {for(i=1;i<=NF;i++) if($i=="mtu") print $(i+1); exit}')
    fi

    # JSON-safe escape (단순)
    iftype=$(printf '%s' "$iftype" | sed 's/[\\"]/\\&/g')
    addr_mode=$(printf '%s' "$addr_mode" | sed 's/[\\"]/\\&/g')

    cat <<EOF
{
  "iface": "${iface}",
  "iftype": "${iftype:-unknown}",
  "addr_mode": "${addr_mode:-unknown}",
  "link_state": "${link_state:-unknown}",
  "mtu": ${mtu:-0}
}
EOF
}

# moal driver 모듈 정보
gather_moal_info() {
    local loaded=false bridge_mode=null
    if [ -d /sys/module/moal ]; then
        loaded=true
        if [ -r /sys/module/moal/parameters/bridge_mode ] 2>/dev/null; then
            bridge_mode=$(cat /sys/module/moal/parameters/bridge_mode 2>/dev/null || echo null)
        fi
    fi
    cat <<EOF
{
  "moal_loaded": ${loaded},
  "bridge_mode": ${bridge_mode}
}
EOF
}

WL_INFO=$(gather_driver_info "$WIRELESS_IFACE")
WD_INFO=$(gather_driver_info "$WIRED_IFACE")
MOAL_INFO=$(gather_moal_info)

# ============================================================
# 2) info-only 모드: capture skip
# ============================================================
if [ "$INFO_ONLY" -eq 1 ]; then
    JSON=$(cat <<EOF
{
  "schema_version": "1.0",
  "mode": "info-only",
  "timestamp": "$(date -Iseconds)",
  "wireless": ${WL_INFO},
  "wired": ${WD_INFO},
  "moal": ${MOAL_INFO}
}
EOF
)
    if [ -n "$OUT_FILE" ]; then
        mkdir -p "$(dirname "$OUT_FILE")"
        printf '%s\n' "$JSON" > "$OUT_FILE"
        log_info "result: $OUT_FILE"
    else
        if [ "$FORMAT" = "text" ]; then
            require_command jq
            printf '%s' "$JSON" | jq -r '
                "## driver info",
                "  wireless: \(.wireless.iface) iftype=\(.wireless.iftype) addr_mode=\(.wireless.addr_mode) state=\(.wireless.link_state)",
                "  wired   : \(.wired.iface) state=\(.wired.link_state) mtu=\(.wired.mtu)",
                "  moal    : loaded=\(.moal.moal_loaded) bridge_mode=\(.moal.bridge_mode)"
            '
        else
            printf '%s\n' "$JSON"
        fi
    fi
    exit "$EXIT_OK"
fi

# ============================================================
# 3) capture mode (duration)
# ============================================================
log_info "vlan-probe: capture ${DURATION}s on ${WIRELESS_IFACE} + ${WIRED_IFACE}"
log_info "vlan-probe: AP에서 VLAN tagged 트래픽이 흐르고 있는지 확인하세요 (예: VID 10/20에서 ping/iperf)"

TMPDIR=$(mktemp -d -t wbvlan.XXXXXX)
trap 'rm -rf "$TMPDIR"' EXIT INT TERM

WL_CAP="${TMPDIR}/wireless.txt"
WD_CAP="${TMPDIR}/wired.txt"

# tcpdump -nn -e: numerical, ethernet header 표시 → "vlan N" 토큰 보임
# 종료: -G N -W 1 = N초 1번 rotate 후 종료
# -i: snaplen 64 sufficient (header만 필요)
capture_iface() {
    local iface="$1"
    local out="$2"
    tcpdump -nn -e -s 64 -i "$iface" -l -G "$DURATION" -W 1 \
        -w "${out}.pcap" 2>/dev/null \
        || true
    # pcap에서 텍스트 추출
    tcpdump -nn -e -r "${out}.pcap" 2>/dev/null > "$out" || true
    rm -f "${out}.pcap"
}

capture_iface "$WIRELESS_IFACE" "$WL_CAP" &
WL_PID=$!
register_pid "$WL_PID"
capture_iface "$WIRED_IFACE" "$WD_CAP" &
WD_PID=$!
register_pid "$WD_PID"

wait "$WL_PID" 2>/dev/null || true
wait "$WD_PID" 2>/dev/null || true

# ============================================================
# 4) 분석 — total / vlan / VID 분포
# ============================================================
analyze_capture() {
    local f="$1"
    local total vlan
    if [ ! -f "$f" ] || [ ! -s "$f" ]; then
        printf '{"total_frames":0,"vlan_frames":0,"vlan_ratio":0,"vlan_ids":{}}\n'
        return
    fi
    total=$(wc -l < "$f")
    # tcpdump output에 "vlan N" 토큰이 있는 line 수
    vlan=$(grep -c -E "vlan [0-9]+" "$f" 2>/dev/null || echo 0)
    [ -n "$vlan" ] || vlan=0

    # VID 분포 (정렬된 JSON)
    local vid_json
    vid_json=$(awk '
        {
            for (i = 1; i <= NF; i++) {
                if ($i == "vlan" && (i+1) <= NF) {
                    vid = $(i+1)
                    gsub(/,/, "", vid)
                    if (vid ~ /^[0-9]+$/) cnt[vid]++
                }
            }
        }
        END {
            printf "{"
            sep = ""
            for (v in cnt) {
                printf "%s\"%s\":%d", sep, v, cnt[v]
                sep = ","
            }
            printf "}"
        }
    ' "$f")

    local ratio
    if [ "$total" -gt 0 ]; then
        ratio=$(awk "BEGIN{printf \"%.4f\", ${vlan}/${total}}")
    else
        ratio="0"
    fi

    cat <<EOF
{
  "total_frames": ${total},
  "vlan_frames": ${vlan},
  "vlan_ratio": ${ratio},
  "vlan_ids": ${vid_json:-{}}
}
EOF
}

WL_STATS=$(analyze_capture "$WL_CAP")
WD_STATS=$(analyze_capture "$WD_CAP")

# ============================================================
# 5) verdict — 시나리오 A/B 판정
# ============================================================
WL_VLAN=$(printf '%s' "$WL_STATS" | awk -F'[:,]' '/vlan_frames/ {gsub(/[^0-9]/,"",$2); print $2; exit}')
WD_VLAN=$(printf '%s' "$WD_STATS" | awk -F'[:,]' '/vlan_frames/ {gsub(/[^0-9]/,"",$2); print $2; exit}')
: "${WL_VLAN:=0}" "${WD_VLAN:=0}"

SCENARIO="A"
PASSTHROUGH=false
REASON="mlan0 vlan_frames == 0 → AP가 VLAN tag strip (1-addr STA 시나리오) 또는 AP가 VLAN tagged 트래픽 미발생"
if [ "$WL_VLAN" -gt 0 ]; then
    SCENARIO="B"
    if [ "$WD_VLAN" -gt 0 ]; then
        PASSTHROUGH=true
        REASON="mlan0/eth0 양쪽 vlan_frames > 0 → AP가 4-addr/transparent forwarding + wbridge가 양방향 passthrough"
    else
        PASSTHROUGH=false
        REASON="mlan0 vlan_frames > 0 이지만 eth0 vlan_frames == 0 → wbridge가 strip하거나 bridge 미구성"
    fi
fi

# ============================================================
# 6) JSON 결합
# ============================================================
JSON=$(cat <<EOF
{
  "schema_version": "1.0",
  "mode": "capture",
  "timestamp": "$(date -Iseconds)",
  "duration_sec": ${DURATION},
  "wireless": ${WL_INFO},
  "wired": ${WD_INFO},
  "moal": ${MOAL_INFO},
  "wireless_capture": ${WL_STATS},
  "wired_capture": ${WD_STATS},
  "verdict": {
    "scenario": "${SCENARIO}",
    "passthrough_works": ${PASSTHROUGH},
    "reason": "${REASON}"
  }
}
EOF
)

# ============================================================
# 7) 출력
# ============================================================
emit() {
    local payload="$1"
    if [ -n "$OUT_FILE" ]; then
        mkdir -p "$(dirname "$OUT_FILE")"
        printf '%s\n' "$payload" > "$OUT_FILE"
        log_info "result: $OUT_FILE"
    else
        printf '%s\n' "$payload"
    fi
}

if [ "$FORMAT" = "json" ]; then
    emit "$JSON"
else
    require_command jq
    text=$(printf '%s' "$JSON" | jq -r '
        "duration_sec: \(.duration_sec)",
        "",
        "## driver info",
        "  wireless: \(.wireless.iface) iftype=\(.wireless.iftype) addr_mode=\(.wireless.addr_mode) state=\(.wireless.link_state) mtu=\(.wireless.mtu)",
        "  wired   : \(.wired.iface) state=\(.wired.link_state) mtu=\(.wired.mtu)",
        "  moal    : loaded=\(.moal.moal_loaded) bridge_mode=\(.moal.bridge_mode)",
        "",
        "## wireless capture (\(.wireless.iface))",
        "  total_frames : \(.wireless_capture.total_frames)",
        "  vlan_frames  : \(.wireless_capture.vlan_frames)",
        "  vlan_ratio   : \(.wireless_capture.vlan_ratio)",
        "  vlan_ids     :",
        ((.wireless_capture.vlan_ids // {}) | to_entries | sort_by(-.value)
          | map("    - VID \(.key): \(.value)") | .[]),
        "",
        "## wired capture (\(.wired.iface))",
        "  total_frames : \(.wired_capture.total_frames)",
        "  vlan_frames  : \(.wired_capture.vlan_frames)",
        "  vlan_ratio   : \(.wired_capture.vlan_ratio)",
        "  vlan_ids     :",
        ((.wired_capture.vlan_ids // {}) | to_entries | sort_by(-.value)
          | map("    - VID \(.key): \(.value)") | .[]),
        "",
        "## verdict",
        "  scenario          : \(.verdict.scenario)",
        "  passthrough_works : \(.verdict.passthrough_works)",
        "  reason            : \(.verdict.reason)"
    ')
    emit "$text"
fi

exit "$EXIT_OK"
