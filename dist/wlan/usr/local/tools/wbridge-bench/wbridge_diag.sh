#!/bin/bash
# wbridge_diag.sh — Plan SC: FR-01
# Design Ref: §4.2 (CLI), §3.1 (Diag Result Schema), §11.2 (Implementation Order)
#
# bridge_netfilter / conntrack / nftables flowtable / wbridge engine 진단.
# 사람용 표 + JSON 결과 동시 출력.
#
# Usage: wbridge_diag.sh [--output FILE] [--syslog-only]
set -euo pipefail
TAG=$(basename "$0")
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
. "${SCRIPT_DIR}/lib/_common.sh"

usage() {
    cat <<EOF
Usage: ${TAG} [--output FILE] [--syslog-only] [-h|--help]

Options:
  --output FILE     JSON 결과 저장 경로 (default: /var/log/wbridge-bench/diag-{ts}.json)
  --syslog-only     파일 저장 skip, syslog/stdout만
  -h, --help        도움말

Exit codes:
  0 = OK, 1 = config/dependency missing
EOF
}

# --- 인자 파싱 ---
OUTPUT=""
SYSLOG_ONLY=0
while [ $# -gt 0 ]; do
    case "$1" in
        --output)       shift; OUTPUT="${1:-}" ;;
        --output=*)     OUTPUT="${1#*=}" ;;
        --syslog-only)  SYSLOG_ONLY=1 ;;
        -h|--help)      usage; exit 0 ;;
        *)              die "$EXIT_CONFIG_MISSING" "unknown arg: $1 (--help for usage)" ;;
    esac
    shift
done

require_command jq

if [ "$SYSLOG_ONLY" -eq 0 ] && [ -z "$OUTPUT" ]; then
    OUTPUT=$(_result_path diag)
fi

# ============================================================
# 1) Host / Kernel / HZ
# ============================================================
HOST=$(hostname)
KERNEL_VERSION=$(uname -r)
HZ_VALUE=$(kernel_hz)
SOC_ID=$(cat /sys/devices/soc0/soc_id 2>/dev/null || echo "unknown")

# ============================================================
# 2) Kernel config 옵션 추출 (4개 핵심)
# ============================================================
get_kconfig() {
    local sym="$1"
    local val
    if [ -r /proc/config.gz ]; then
        val=$(zcat /proc/config.gz 2>/dev/null | grep -E "^CONFIG_${sym}=" | cut -d= -f2 | head -1)
    elif [ -r "/boot/config-$(uname -r)" ]; then
        val=$(grep -E "^CONFIG_${sym}=" "/boot/config-$(uname -r)" | cut -d= -f2 | head -1)
    fi
    printf '%s' "${val:-}"
}

CFG_BRIDGE_NETFILTER=$(get_kconfig BRIDGE_NETFILTER)
CFG_NF_CONNTRACK=$(get_kconfig NF_CONNTRACK)
CFG_NF_FLOW_TABLE=$(get_kconfig NF_FLOW_TABLE)
CFG_NF_FLOW_TABLE_INET=$(get_kconfig NF_FLOW_TABLE_INET)

# ============================================================
# 3) bridge_netfilter 동적 상태
# ============================================================
BNF_LOADED="false"
BNF_REFCNT=0
if bridge_netfilter_loaded; then
    BNF_LOADED="true"
    BNF_REFCNT=$(cat /sys/module/br_netfilter/refcnt 2>/dev/null || echo 0)
fi

# ============================================================
# 4) conntrack 카운트
# ============================================================
CT_COUNT=$(conntrack_count)
CT_MAX=$(conntrack_max)

# ============================================================
# 5) nftables 상태
# ============================================================
NFT_RULESET_PRESENT="false"
NFT_FLOWTABLE_PRESENT="false"
NFT_WBRIDGE_LOADED="false"
if command -v nft >/dev/null 2>&1; then
    if nft list ruleset 2>/dev/null | grep -q '^table'; then
        NFT_RULESET_PRESENT="true"
    fi
    if nft list ruleset 2>/dev/null | grep -q 'flowtable'; then
        NFT_FLOWTABLE_PRESENT="true"
    fi
    if nft list table inet wbridge_filter >/dev/null 2>&1; then
        NFT_WBRIDGE_LOADED="true"
    fi
fi

# ============================================================
# 6) wbridge engine
# ============================================================
WBRIDGE_ENGINE="unknown"
if [ -f "$WBRIDGE_CONF_JSON" ]; then
    WBRIDGE_ENGINE=$(jq -r '.wbridge.engine // "unknown"' "$WBRIDGE_CONF_JSON")
fi

# ============================================================
# 7) Verdict (Flowtable 도입 효과 자동 판정)
# ============================================================
VERDICT=""
if [ "$BNF_LOADED" = "true" ] && [ "$CT_COUNT" -gt 0 ]; then
    if [ "$NFT_WBRIDGE_LOADED" = "true" ]; then
        VERDICT="Flowtable 이미 적용됨 (wbridge_filter loaded). 측정 가능."
    else
        VERDICT="Flowtable 도입 효과 큼 — bridge_netfilter ON + conntrack 활성. 0005 cfg + nft 룰 적용 권장."
    fi
else
    if [ "$BNF_LOADED" = "false" ]; then
        VERDICT="bridge_netfilter OFF — Flowtable 효과 미미. HZ_1000/측정 인프라만 의미. Plan scope 재검토 필요."
    else
        VERDICT="conntrack 비활성 — Flowtable 효과 미미. 일반 transparent bridge 모드."
    fi
fi

# ============================================================
# 8) 사람용 표 출력
# ============================================================
printf '\n=== wbridge environment diagnosis ===\n'
printf 'host:                %s\n' "$HOST"
printf 'soc_id:              %s\n' "$SOC_ID"
printf 'kernel:              %s  (HZ=%s)\n' "$KERNEL_VERSION" "${HZ_VALUE:-unknown}"
printf 'CONFIG:\n'
printf '  BRIDGE_NETFILTER:  %s\n' "${CFG_BRIDGE_NETFILTER:-(absent)}"
printf '  NF_CONNTRACK:      %s\n' "${CFG_NF_CONNTRACK:-(absent)}"
printf '  NF_FLOW_TABLE:     %s\n' "${CFG_NF_FLOW_TABLE:-(absent)}"
printf '  NF_FLOW_TABLE_INET:%s\n' "${CFG_NF_FLOW_TABLE_INET:-(absent)}"
printf 'bridge_netfilter:    LOADED=%s (refcnt=%s)\n' "$BNF_LOADED" "$BNF_REFCNT"
printf 'nf_conntrack:        count=%s / max=%s\n' "$CT_COUNT" "$CT_MAX"
printf 'nftables:\n'
printf '  ruleset_present:        %s\n' "$NFT_RULESET_PRESENT"
printf '  flowtable_anywhere:     %s\n' "$NFT_FLOWTABLE_PRESENT"
printf '  wbridge_filter_loaded:  %s\n' "$NFT_WBRIDGE_LOADED"
printf 'wbridge engine:      %s\n' "$WBRIDGE_ENGINE"
printf '\n=== verdict ===\n'
printf '%s\n\n' "$VERDICT"

# ============================================================
# 9) syslog 핵심 사실
# ============================================================
log_info "diag: bnf=$BNF_LOADED ct=$CT_COUNT/$CT_MAX nft_wbridge=$NFT_WBRIDGE_LOADED engine=$WBRIDGE_ENGINE hz=${HZ_VALUE:-?}"
log_info "diag verdict: $VERDICT"

# ============================================================
# 10) JSON 출력 (Design §3.1 schema)
# ============================================================
if [ "$SYSLOG_ONLY" -eq 0 ]; then
    cat <<EOF | _emit_json_raw "$OUTPUT"
{
  "schema_version": "1.0",
  "timestamp": "$(date -Iseconds)",
  "host": "${HOST}",
  "soc_id": "${SOC_ID}",
  "kernel": {
    "version": "${KERNEL_VERSION}",
    "hz": ${HZ_VALUE:-null},
    "config": {
      "BRIDGE_NETFILTER": $(jq -Rn --arg v "$CFG_BRIDGE_NETFILTER" '$v | select(. != "") // null'),
      "NF_CONNTRACK": $(jq -Rn --arg v "$CFG_NF_CONNTRACK" '$v | select(. != "") // null'),
      "NF_FLOW_TABLE": $(jq -Rn --arg v "$CFG_NF_FLOW_TABLE" '$v | select(. != "") // null'),
      "NF_FLOW_TABLE_INET": $(jq -Rn --arg v "$CFG_NF_FLOW_TABLE_INET" '$v | select(. != "") // null')
    }
  },
  "bridge_netfilter": {
    "module_loaded": ${BNF_LOADED},
    "module_used_by_count": ${BNF_REFCNT}
  },
  "conntrack": {
    "count": ${CT_COUNT},
    "max": ${CT_MAX}
  },
  "nftables": {
    "ruleset_present": ${NFT_RULESET_PRESENT},
    "flowtable_present": ${NFT_FLOWTABLE_PRESENT},
    "wbridge_flowtable_loaded": ${NFT_WBRIDGE_LOADED}
  },
  "wbridge": {
    "engine": "${WBRIDGE_ENGINE}",
    "config_path": "${WBRIDGE_CONF_JSON}"
  },
  "verdict": $(printf '%s' "$VERDICT" | jq -R -s '.')
}
EOF
fi

exit "$EXIT_OK"
