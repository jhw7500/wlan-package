#!/bin/bash
# wbridge fastpath common library
# Design Ref: §9.4 — Infrastructure helpers (logger, JSON, engine helpers)
# Plan SC: FR-01~FR-09의 공통 토대

# ============================================================
# Exit code 규약 (Design §6.1)
# ============================================================
export EXIT_OK=0
export EXIT_CONFIG_MISSING=1
export EXIT_MEASUREMENT_FAILED=2
export EXIT_REGRESSION=3

# ============================================================
# 환경 변수 (default + override)
# ============================================================
WBRIDGE_FACILITY="${WBRIDGE_FACILITY:-local0}"
WBRIDGE_CONF_JSON="${WBRIDGE_CONF_JSON:-/usr/local/etc/wifi_init_conf.json}"
WBRIDGE_BENCH_OUTPUT="${WBRIDGE_BENCH_OUTPUT:-/var/log/wbridge-bench}"
WBRIDGE_BENCH_DURATION="${WBRIDGE_BENCH_DURATION:-60}"
WBRIDGE_BENCH_ENGINES="${WBRIDGE_BENCH_ENGINES:-pcap tpacket moal}"

# ============================================================
# 로그 (syslog local0 + stdout/stderr)
# Design Ref: §10.3
# ============================================================
log_info() {
    logger -p "${WBRIDGE_FACILITY}.info" -t "${TAG:-wbridge}" -- "$*"
    printf '[INFO ] %s\n' "$*"
}

log_warn() {
    logger -p "${WBRIDGE_FACILITY}.warn" -t "${TAG:-wbridge}" -- "$*"
    printf '[WARN ] %s\n' "$*" >&2
}

log_err() {
    logger -p "${WBRIDGE_FACILITY}.err" -t "${TAG:-wbridge}" -- "$*"
    printf '[ERROR] %s\n' "$*" >&2
}

# Usage: die <exit_code> <message>
die() {
    local code="$1"; shift
    log_err "$*"
    exit "$code"
}

# ============================================================
# 권한/의존성 검증
# ============================================================
require_root() {
    if [ "$(id -u)" -ne 0 ]; then
        die "$EXIT_CONFIG_MISSING" "must run as root"
    fi
}

require_command() {
    local cmd="$1"
    command -v "$cmd" >/dev/null 2>&1 \
        || die "$EXIT_CONFIG_MISSING" "required command not found: $cmd"
}

# ============================================================
# JSON 결과 파일 출력
# Plan SC: FR-05 (JSON 결과), Design §3 (Schema)
# Usage: cat <<EOF | _emit_json_raw "/path/to/out.json"
#        { "schema_version": "1.0", ... }
#        EOF
# ============================================================
_emit_json_raw() {
    local out_file="$1"
    mkdir -p "$(dirname "$out_file")"
    cat > "$out_file"
    log_info "result: $out_file"
}

# Usage: out=$(_result_path baseline moal)  →  /var/log/wbridge-bench/baseline/moal/2026-04-27T15-30-00Z.json
#        out=$(_result_path diag)           →  /var/log/wbridge-bench/diag-2026-04-27T15-30-00Z.json
_result_path() {
    local phase="$1"
    local engine="${2:-}"
    local ts
    ts=$(date -u +'%Y-%m-%dT%H-%M-%SZ')
    if [ -n "$engine" ]; then
        printf '%s/%s/%s/%s.json' "$WBRIDGE_BENCH_OUTPUT" "$phase" "$engine" "$ts"
    else
        printf '%s/%s-%s.json' "$WBRIDGE_BENCH_OUTPUT" "$phase" "$ts"
    fi
}

# ============================================================
# wbridge engine helpers
# Plan SC: FR-06 (engine 교차 측정), Design §2.2
# ============================================================

# 현재 engine 조회. Default: pcap (JSON 부재 또는 키 없을 때)
get_engine() {
    if [ ! -f "$WBRIDGE_CONF_JSON" ]; then
        die "$EXIT_CONFIG_MISSING" "config not found: $WBRIDGE_CONF_JSON"
    fi
    require_command jq
    jq -r '.wbridge.engine // "pcap"' "$WBRIDGE_CONF_JSON"
}

# Usage: set_engine <pcap|tpacket|moal>
# 효과: JSON 토글 + wifi_init.service 재시작 (driver 모듈 reload + 자식 unit 재시작)
# moal ↔ user-space 전환 시 driver mod_para (bridge_mode) 가 달라지므로
# daemon SIGHUP/restart 만으로 부족 — wifi_init.service 가 정답.
set_engine() {
    local new_engine="$1"
    case "$new_engine" in
        pcap|tpacket|moal) ;;
        *) die "$EXIT_CONFIG_MISSING" "invalid engine: $new_engine (allowed: pcap|tpacket|moal)" ;;
    esac

    require_command jq
    local old_engine
    old_engine=$(jq -r '.wbridge.engine // "pcap"' "$WBRIDGE_CONF_JSON" 2>/dev/null || echo "")

    local tmp="${WBRIDGE_CONF_JSON}.tmp.$$"
    if ! jq --arg e "$new_engine" '.wbridge.engine = $e' "$WBRIDGE_CONF_JSON" > "$tmp"; then
        rm -f "$tmp"
        die "$EXIT_MEASUREMENT_FAILED" "jq failed to update engine to $new_engine"
    fi
    mv "$tmp" "$WBRIDGE_CONF_JSON"
    log_info "engine set in JSON: $new_engine (was=${old_engine:-?})"

    # 같은 engine 이면 noop
    if [ "$new_engine" = "$old_engine" ]; then
        log_info "engine unchanged — skip restart"
        return 0
    fi

    _restart_wbridge_service
}

# wifi_init.service: oneshot — moal 관련 전환에서 driver 모듈 reload 필수
# StartLimitBurst 회피 위해 reset-failed 선행.
_restart_wbridge_service() {
    local settle="${WBRIDGE_DRIVER_SETTLE_SEC:-7}"

    # 1) wifi_init.service 우선 — driver reload + 자식 unit (wifi_bridge@... 등) 재시작
    if systemctl list-unit-files 'wifi_init.service' >/dev/null 2>&1; then
        systemctl reset-failed wifi_init.service 2>/dev/null || true
        if systemctl restart wifi_init.service 2>/dev/null; then
            log_info "wifi_init restarted (driver reload). settle ${settle}s"
            sleep "$settle"
            return 0
        else
            log_warn "wifi_init restart 실패 — fallback service 시도"
        fi
    fi

    # 2) fallback: instance unit / 단일 unit
    local svc
    for svc in 'wifi_bridge@mlan0' wifi_bridge wbridge; do
        if systemctl list-unit-files "${svc}.service" >/dev/null 2>&1 \
           || systemctl status "$svc" >/dev/null 2>&1; then
            if systemctl restart "$svc" 2>/dev/null; then
                log_info "$svc restarted via systemctl. settle ${settle}s"
                sleep "$settle"
                return 0
            fi
        fi
    done
    log_warn "no wbridge service available; engine change may not be picked up"
}

# Trap helper: ORIGINAL_ENGINE 변수가 설정되어 있으면 복원
# Plan SC: FR-09 (regression 0% — engine 자동 복원 보장)
# 사용 패턴:
#   ORIGINAL_ENGINE=$(get_engine)
#   trap '_restore_engine_on_exit' EXIT INT TERM
_restore_engine_on_exit() {
    if [ -n "${ORIGINAL_ENGINE:-}" ]; then
        local current
        current=$(get_engine 2>/dev/null || echo "")
        if [ -n "$current" ] && [ "$current" != "$ORIGINAL_ENGINE" ]; then
            log_info "restoring engine: $current -> $ORIGINAL_ENGINE"
            set_engine "$ORIGINAL_ENGINE" || log_err "engine restore failed (manual recovery needed)"
        fi
    fi
}

# ============================================================
# bridge_netfilter / conntrack 진단 헬퍼
# Plan SC: FR-01
# ============================================================
bridge_netfilter_loaded() {
    [ -d /sys/module/br_netfilter ]
}

conntrack_count() {
    cat /proc/sys/net/netfilter/nf_conntrack_count 2>/dev/null || echo "0"
}

conntrack_max() {
    cat /proc/sys/net/netfilter/nf_conntrack_max 2>/dev/null || echo "0"
}

# 현재 커널 HZ (CONFIG_HZ)
kernel_hz() {
    if [ -r /proc/config.gz ]; then
        zcat /proc/config.gz 2>/dev/null | grep -E '^CONFIG_HZ=' | cut -d= -f2
    elif [ -r "/boot/config-$(uname -r)" ]; then
        grep -E '^CONFIG_HZ=' "/boot/config-$(uname -r)" | cut -d= -f2
    else
        echo "unknown"
    fi
}

# ============================================================
# Background 자식 프로세스 cleanup helper
# 사용 패턴:
#   register_pid "$bg_pid"
#   trap 'cleanup_pids' EXIT INT TERM
# ============================================================
declare -a WBRIDGE_PID_LIST=()

register_pid() {
    WBRIDGE_PID_LIST+=("$1")
}

cleanup_pids() {
    local pid
    for pid in "${WBRIDGE_PID_LIST[@]:-}"; do
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            kill -TERM "$pid" 2>/dev/null || true
            sleep 0.2
            kill -KILL "$pid" 2>/dev/null || true
        fi
    done
}

# ============================================================
# IRQ / softirq snapshot 및 차분
# Design Ref: §3.2 board_irq schema (v1.2)
# Plan SC: FR-05 보강 — NIC IRQ 부하 정량화
#
# 사용 패턴:
#   snapshot_irq /tmp/snap-before
#   ... 측정 작업 ...
#   snapshot_irq /tmp/snap-after
#   irq_diff_to_json /tmp/snap-before /tmp/snap-after $duration_sec
# ============================================================

# /proc/interrupts + /proc/softirqs 캡처
# Usage: snapshot_irq <prefix>  → ${prefix}.interrupts, ${prefix}.softirqs
snapshot_irq() {
    local prefix="$1"
    [ -n "$prefix" ] || { log_err "snapshot_irq: prefix required"; return 1; }
    cp /proc/interrupts "${prefix}.interrupts" 2>/dev/null \
        || { log_warn "snapshot_irq: /proc/interrupts read failed"; return 1; }
    cp /proc/softirqs   "${prefix}.softirqs"   2>/dev/null \
        || { log_warn "snapshot_irq: /proc/softirqs read failed"; return 1; }
    return 0
}

# /proc/interrupts 파싱 → "<label>\t<descriptor>\t<sum>" per line
# label: IRQ 번호 또는 NMI/LOC/RES 등 system label
# descriptor: 마지막 토큰들 (예: "GIC 26 Level mlan0_msi" 또는 "ENETC enetc-0:rx")
# sum: 모든 CPU 합계
parse_interrupts() {
    local f="$1"
    [ -f "$f" ] || return 1
    awk '
        FNR == 1 {
            ncpu = NF
            next
        }
        {
            label = $1; sub(/:$/, "", label)
            sum = 0
            n_max = ncpu + 1
            if (n_max > NF) n_max = NF
            for (i = 2; i <= n_max; i++) {
                if ($i ~ /^[0-9]+$/) sum += ($i + 0)
            }
            desc = ""
            for (j = ncpu + 2; j <= NF; j++) {
                desc = desc (j == ncpu+2 ? "" : " ") $j
            }
            if (desc == "") desc = label
            printf "%s\t%s\t%d\n", label, desc, sum
        }
    ' "$f"
}

# /proc/softirqs 파싱 → "<type>\t<sum>" per line
parse_softirqs() {
    local f="$1"
    [ -f "$f" ] || return 1
    awk '
        FNR == 1 {
            ncpu = NF
            next
        }
        {
            label = $1; sub(/:$/, "", label)
            sum = 0
            n_max = ncpu + 1
            if (n_max > NF) n_max = NF
            for (i = 2; i <= n_max; i++) {
                if ($i ~ /^[0-9]+$/) sum += ($i + 0)
            }
            printf "%s\t%d\n", label, sum
        }
    ' "$f"
}

# 두 interrupts 파일 차분 → "<label>\t<descriptor>\t<delta>" (delta>0만)
diff_interrupts() {
    local b="$1" a="$2"
    [ -f "$b" ] && [ -f "$a" ] || return 1
    local before_table; before_table=$(mktemp -t wbirq-bt.XXXXXX)
    parse_interrupts "$b" > "$before_table"
    parse_interrupts "$a" | awk -F'\t' -v BT="$before_table" '
        BEGIN {
            while ((getline L < BT) > 0) {
                n = split(L, p, "\t")
                if (n >= 3) B[p[1] FS p[2]] = p[3] + 0
            }
            close(BT)
        }
        {
            key = $1 FS $2
            d = ($3 + 0) - (key in B ? B[key] : 0)
            if (d > 0) printf "%s\t%s\t%d\n", $1, $2, d
        }
    '
    rm -f "$before_table"
}

# 두 softirqs 파일 차분 → "<type>\t<delta>"
diff_softirqs() {
    local b="$1" a="$2"
    [ -f "$b" ] && [ -f "$a" ] || return 1
    local before_table; before_table=$(mktemp -t wbsoft-bt.XXXXXX)
    parse_softirqs "$b" > "$before_table"
    parse_softirqs "$a" | awk -F'\t' -v BT="$before_table" '
        BEGIN {
            while ((getline L < BT) > 0) {
                split(L, p, "\t")
                B[p[1]] = p[2] + 0
            }
            close(BT)
        }
        {
            d = ($2 + 0) - ($1 in B ? B[$1] : 0)
            printf "%s\t%d\n", $1, d
        }
    '
    rm -f "$before_table"
}

# Usage: irq_diff_to_json <before_prefix> <after_prefix> <duration_sec>
# stdout: JSON object  (board_irq schema v1.2)
irq_diff_to_json() {
    local b_pfx="$1" a_pfx="$2" dur="$3"
    [ "$dur" -gt 0 ] 2>/dev/null || dur=1

    local irq_diff sirq_diff
    irq_diff=$(mktemp -t wbirq-d.XXXXXX)
    sirq_diff=$(mktemp -t wbsoft-d.XXXXXX)

    diff_interrupts "${b_pfx}.interrupts" "${a_pfx}.interrupts" > "$irq_diff" 2>/dev/null || true
    diff_softirqs   "${b_pfx}.softirqs"   "${a_pfx}.softirqs"   > "$sirq_diff" 2>/dev/null || true

    # hardirq 합계
    local hi_total
    hi_total=$(awk -F'\t' '{ s += $3 } END { print s+0 }' "$irq_diff")

    # hardirq by iface (descriptor 끝 토큰이 NIC 이름인 경우만 — mlan/eth/enetc/wlan/sdhci 등)
    local hi_by_iface_json
    hi_by_iface_json=$(awk -F'\t' '
        BEGIN { ifsep = "" }
        {
            n = split($2, words, " ")
            iface = words[n]
            # 관심 NIC/스토리지 NIC 패턴
            if (iface !~ /^(mlan|eth|enetc|wlan|enp|wlp|sdhci|mmc)/) next
            gsub(/[^A-Za-z0-9_:.-]/, "_", iface)
            cnt[iface] += $3
        }
        END {
            printf "{"
            for (k in cnt) { printf "%s\"%s\":%d", ifsep, k, cnt[k]; ifsep = "," }
            printf "}"
        }
    ' "$irq_diff")

    # 전체 hardirq breakdown (descriptor 통째)
    local hi_by_desc_json
    hi_by_desc_json=$(awk -F'\t' '
        BEGIN { ifsep = "" }
        {
            d = $2
            gsub(/\\/, "\\\\", d)
            gsub(/"/, "\\\"", d)
            printf "%s\"%s\":%d", ifsep, d, $3
            ifsep = ","
        }
        END { }
    ' "$irq_diff" | awk 'BEGIN{printf "{"} {printf "%s",$0} END{printf "}"}')

    # softirq breakdown
    local sirq_by_type_json sirq_total net_rx net_tx
    sirq_by_type_json=$(awk -F'\t' '
        BEGIN { ifsep = ""; printf "{" }
        { printf "%s\"%s\":%d", ifsep, $1, $2; ifsep = "," }
        END { printf "}" }
    ' "$sirq_diff")

    sirq_total=$(awk -F'\t' '{ s += $2 } END { print s+0 }' "$sirq_diff")
    net_rx=$(awk -F'\t' '$1 == "NET_RX" { print $2+0; found=1 } END { if (!found) print 0 }' "$sirq_diff")
    net_tx=$(awk -F'\t' '$1 == "NET_TX" { print $2+0; found=1 } END { if (!found) print 0 }' "$sirq_diff")

    local hi_per_sec sirq_net_per_sec
    hi_per_sec=$(awk "BEGIN{ printf \"%.2f\", ${hi_total}/${dur} }")
    sirq_net_per_sec=$(awk "BEGIN{ printf \"%.2f\", (${net_rx}+${net_tx})/${dur} }")

    cat <<EOF
{
  "duration_sec": ${dur},
  "hardirq_total": ${hi_total},
  "hardirq_per_sec": ${hi_per_sec},
  "hardirq_by_iface": ${hi_by_iface_json},
  "hardirq_by_desc": ${hi_by_desc_json},
  "softirq_total": ${sirq_total},
  "softirq_by_type": ${sirq_by_type_json},
  "softirq_net_rx": ${net_rx},
  "softirq_net_tx": ${net_tx},
  "softirq_net_per_sec": ${sirq_net_per_sec}
}
EOF
    rm -f "$irq_diff" "$sirq_diff"
}
