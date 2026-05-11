#!/bin/bash
# _quick.sh — wbridge-quick 헬퍼 라이브러리
# Plan: wbridge-quick.plan.md FR-Q1~Q8
# Design: wbridge-quick.design.md §2.2

# _common.sh 가 먼저 source 되어 있다고 가정 (log_*, die, EXIT_*).

# ============================================================
# 경로 상수
# ============================================================
QUICK_CONF_FILE="${QUICK_CONF_FILE:-/etc/wbridge/quick.conf}"
QUICK_PHASE_DEFAULT="quick"

# ============================================================
# conf load / save
# ============================================================
quick_load_conf() {
    if [ ! -f "$QUICK_CONF_FILE" ]; then
        die "$EXIT_CONFIG_MISSING" \
            "config not found: $QUICK_CONF_FILE — run 'wbridge_quick.sh setup' first"
    fi

    # shellcheck disable=SC1090
    . "$QUICK_CONF_FILE"

    # default 채우기
    : "${WIRED_IFACE:=eth0}"
    : "${WIRELESS_IFACE:=wlan0}"
    : "${DURATION:=30}"
    : "${DEFAULT_ENGINES:=pcap tpacket moal}"
    : "${ENV_LABEL:=unknown}"
    : "${SETTLE_SEC:=5}"
    : "${PHASE:=$QUICK_PHASE_DEFAULT}"

    # 필수 키 검증
    [ -n "${WIRED_HOST:-}" ]    || die "$EXIT_CONFIG_MISSING" "WIRED_HOST 누락 in $QUICK_CONF_FILE — setup 다시 실행"
    [ -n "${WIRELESS_HOST:-}" ] || die "$EXIT_CONFIG_MISSING" "WIRELESS_HOST 누락 in $QUICK_CONF_FILE — setup 다시 실행"

    export WIRED_HOST WIRED_IFACE WIRELESS_HOST WIRELESS_IFACE \
           DURATION DEFAULT_ENGINES ENV_LABEL SETTLE_SEC PHASE
}

quick_save_conf() {
    local dir
    dir=$(dirname "$QUICK_CONF_FILE")
    mkdir -p "$dir"
    cat > "$QUICK_CONF_FILE" <<EOF
# wbridge-quick 측정 설정 ($(date -Iseconds) setup wizard)
WIRED_HOST=${WIRED_HOST}
WIRED_IFACE=${WIRED_IFACE:-eth0}
WIRELESS_HOST=${WIRELESS_HOST}
WIRELESS_IFACE=${WIRELESS_IFACE:-wlan0}
DURATION=${DURATION:-30}
DEFAULT_ENGINES="${DEFAULT_ENGINES:-pcap tpacket moal}"
ENV_LABEL=${ENV_LABEL:-unknown}
SETTLE_SEC=${SETTLE_SEC:-5}
PHASE=${PHASE:-$QUICK_PHASE_DEFAULT}
EOF
    chmod 600 "$QUICK_CONF_FILE"
    log_info "saved: $QUICK_CONF_FILE (chmod 600)"
}

# ============================================================
# wizard 입력 헬퍼 — prompt + default
# Usage: quick_prompt VARNAME "label" "default"
# ============================================================
quick_prompt() {
    local varname="$1"
    local label="$2"
    local default="$3"
    local current="${!varname:-$default}"
    local input
    if [ -t 0 ]; then
        printf '%s [%s]: ' "$label" "$current" >&2
        IFS= read -r input || true
    else
        input=""
    fi
    [ -z "$input" ] && input="$current"
    printf -v "$varname" '%s' "$input"
    export "${varname?}"
}

# ============================================================
# board check
# ============================================================
quick_check_board() {
    local rc=0
    printf '=== board ===\n'

    local missing=()
    for c in jq bc mpstat ssh; do
        command -v "$c" >/dev/null 2>&1 || missing+=("$c")
    done
    if [ ${#missing[@]} -eq 0 ]; then
        printf '[OK]  jq, bc, mpstat, ssh installed\n'
    else
        printf '[FAIL] missing on board: %s\n' "${missing[*]}"
        printf '       hint: apt install %s\n' "${missing[*]}"
        rc=1
    fi

    if [ -f "$WBRIDGE_CONF_JSON" ] && [ -r "$WBRIDGE_CONF_JSON" ]; then
        local engine
        engine=$(jq -r '.wbridge.engine // "unknown"' "$WBRIDGE_CONF_JSON" 2>/dev/null || echo unknown)
        printf '[OK]  wifi_init_conf.json readable (engine=%s)\n' "$engine"
    else
        printf '[FAIL] wifi_init_conf.json not readable: %s\n' "$WBRIDGE_CONF_JSON"
        rc=1
    fi

    # wbridge engine 동작 여부 (moal은 daemon X, 그 외는 daemon)
    local engine
    engine=$(get_engine 2>/dev/null || echo unknown)
    case "$engine" in
        pcap|tpacket)
            if pgrep -af 'wifi-wbridge' >/dev/null 2>&1; then
                printf '[OK]  wifi-wbridge daemon running (engine=%s)\n' "$engine"
            else
                printf '[WARN] engine=%s but wifi-wbridge daemon not running\n' "$engine"
            fi
            ;;
        moal)
            if pgrep -af 'wifi-wbridge' >/dev/null 2>&1; then
                printf '[WARN] engine=moal but wifi-wbridge daemon detected (kernel-mode 충돌 가능)\n'
            else
                printf '[OK]  engine=moal (kernel driver, no daemon)\n'
            fi
            ;;
        *)
            printf '[WARN] engine=%s (unexpected)\n' "$engine"
            ;;
    esac

    return $rc
}

# ============================================================
# remote check (PC1 wired / PC2 wireless)
# Usage: quick_check_remote "$host" "$iface" "wired|wireless"
# ============================================================
quick_check_remote() {
    local host="$1"
    local iface="$2"
    local label="$3"
    local rc=0

    printf '=== %s (%s %s) ===\n' "$label" "$host" "$iface"

    local ssh_opts=(-o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new)

    if ! ssh "${ssh_opts[@]}" "$host" 'true' >/dev/null 2>&1; then
        printf '[FAIL] ssh unreachable (BatchMode=yes)\n'
        printf '       hint: ssh-copy-id %s 또는 ssh %s 수동 확인\n' "$host" "$host"
        return 1
    fi
    printf '[OK]  ssh reachable\n'

    local missing
    missing=$(ssh "${ssh_opts[@]}" "$host" '
        m=""
        for c in iperf3 ip ping; do
            command -v "$c" >/dev/null 2>&1 || m="$m $c"
        done
        echo "$m" | xargs
    ' 2>/dev/null || echo "?")

    if [ -z "$missing" ] || [ "$missing" = "" ]; then
        printf '[OK]  iperf3, ip, ping installed\n'
    else
        printf '[FAIL] missing on %s:%s\n' "$label" "$missing"
        printf '       hint: ssh %s sudo apt install -y%s\n' "$host" "$missing"
        rc=1
    fi

    local ip
    ip=$(ssh "${ssh_opts[@]}" "$host" "ip -4 -o addr show dev $iface | awk '{print \$4}' | cut -d/ -f1 | head -1" 2>/dev/null || echo "")
    if [ -n "$ip" ]; then
        printf '[OK]  ip %s (iface=%s)\n' "$ip" "$iface"
    else
        printf '[FAIL] cannot resolve IP on iface=%s\n' "$iface"
        rc=1
    fi

    return $rc
}

# ============================================================
# SSH 키 자동 셋업
#   1) 보드 ~/.ssh/id_ed25519 없으면 ssh-keygen (passphrase 없음)
#   2) WIRED_HOST / WIRELESS_HOST 에 BatchMode 도달 안 되면 ssh-copy-id
#      → ssh-copy-id 가 비밀번호 prompt 직접 처리 (wrapper는 비밀번호 미보관)
# ============================================================
quick_setup_ssh_keys() {
    local home_dir="${HOME:-/root}"
    local key="${home_dir}/.ssh/id_ed25519"
    local pub="${key}.pub"

    require_command ssh-keygen
    require_command ssh-copy-id

    if [ ! -f "$key" ]; then
        log_info "보드에 SSH 키가 없어 새로 생성합니다: $key"
        mkdir -p "${home_dir}/.ssh"
        chmod 700 "${home_dir}/.ssh"
        ssh-keygen -t ed25519 -N '' -f "$key" -C "wbridge-quick@$(hostname)" >&2 \
            || die "$EXIT_CONFIG_MISSING" "ssh-keygen 실패"
    else
        log_info "기존 SSH 키 사용: $key"
    fi
    [ -f "$pub" ] || die "$EXIT_CONFIG_MISSING" "공개키 부재: $pub"

    local fail=()
    local entry
    for entry in "wired:${WIRED_HOST}" "wireless:${WIRELESS_HOST}"; do
        local label="${entry%%:*}"
        local host="${entry#*:}"
        [ -n "$host" ] || continue

        local opts=(-o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new)
        if ssh "${opts[@]}" "$host" 'true' >/dev/null 2>&1; then
            log_info "[SKIP] ${label} (${host}) 이미 키 인증 가능"
            continue
        fi

        printf '\n=== %s (%s) SSH 키 등록 ===\n' "$label" "$host" >&2
        printf '비밀번호 prompt가 뜨면 %s 계정 비밀번호를 입력하세요.\n' "$host" >&2
        if ssh-copy-id -i "$pub" -o StrictHostKeyChecking=accept-new "$host" >&2; then
            # 등록 후 BatchMode 재검증
            if ssh "${opts[@]}" "$host" 'true' >/dev/null 2>&1; then
                log_info "[OK] ${label} 키 등록 완료 (BatchMode 통과)"
            else
                log_warn "[WARN] ${label} ssh-copy-id 끝났는데 BatchMode 실패 — sshd 설정 확인"
                fail+=("$label")
            fi
        else
            log_warn "[FAIL] ${label} ssh-copy-id 실패 — 수동으로 ssh-copy-id ${host}"
            fail+=("$label")
        fi
    done

    if [ ${#fail[@]} -gt 0 ]; then
        log_warn "ssh setup 미완: ${fail[*]} — 'wbridge_quick.sh check' 로 재확인"
        return 1
    fi
    log_info "SSH 풀자동 셋업 완료"
    return 0
}

# ============================================================
# 결과 JSON 에 env_label inject
# ============================================================
quick_inject_label() {
    local json="$1"
    local label="$2"
    [ -f "$json" ] || return 0
    [ -n "$label" ] || return 0
    local tmp="${json}.tmp.$$"
    if jq --arg l "$label" '.topology.env_label = $l' "$json" > "$tmp" 2>/dev/null; then
        mv "$tmp" "$json"
    else
        rm -f "$tmp"
        log_warn "env_label inject 실패: $json"
    fi
}

# ============================================================
# 단일 engine 측정
# Usage: quick_run_one <engine> <duration> <label>
# 외부 의존: wbridge_bench.sh 와 같은 디렉토리에 있음 (SCRIPT_DIR)
# ============================================================
quick_run_one() {
    local engine="$1"
    local duration="$2"
    local label="$3"

    local bench="${SCRIPT_DIR}/wbridge_bench.sh"
    [ -x "$bench" ] || die "$EXIT_CONFIG_MISSING" "wbridge_bench.sh not found: $bench"

    log_info "quick: engine=$engine duration=${duration}s label=$label"

    # 결과 디렉토리 결정 (env override로 phase=quick)
    local out_dir="${WBRIDGE_BENCH_OUTPUT}/${PHASE:-$QUICK_PHASE_DEFAULT}"
    mkdir -p "$out_dir/$engine"

    # wbridge_bench.sh 의 _result_path 는 phase=baseline|after 만 인식하므로
    # WBRIDGE_BENCH_OUTPUT 을 임시로 바꿔서 phase 디렉토리를 quick 으로 강제.
    # 단순 방식: WBRIDGE_BENCH_OUTPUT을 quick 위로 redirect.
    #   - 기존 OUTPUT/baseline/<engine>/ 대신 OUTPUT/quick-baseline/<engine>/ 같은 식 회피
    #   - 단순화: 그냥 PHASE=baseline 으로 호출하되 OUTPUT을 OUTPUT/quick 로 둔다.
    local saved_output="$WBRIDGE_BENCH_OUTPUT"
    WBRIDGE_BENCH_OUTPUT="$out_dir"
    export WBRIDGE_BENCH_OUTPUT

    # PC1 iperf3 server pre-spawn (ssh -f)
    # 일부 임베디드 PC에서 wbridge_bench.sh 의 nohup&  spawn 이 detach 안 되는 문제 우회.
    local ssh_opts=(-o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new)
    log_info "quick: PC1 iperf3 server pre-spawn (ssh -f)"
    ssh "${ssh_opts[@]}" "$WIRED_HOST" 'pkill -f "iperf3 -s" 2>/dev/null; true' >/dev/null 2>&1 || true
    sleep 0.3
    if ! ssh -f "${ssh_opts[@]}" "$WIRED_HOST" 'iperf3 -s -p 5201 >/tmp/iperf3-server.log 2>&1'; then
        log_warn "quick: PC1 iperf3 server pre-spawn 실패 — wbridge_bench.sh 자체 spawn 시도"
    else
        sleep 0.5
    fi

    set +e
    "$bench" \
        --phase=baseline \
        --engine="$engine" \
        --wired-host="$WIRED_HOST" \
        --wireless-host="$WIRELESS_HOST" \
        --wired-iface="$WIRED_IFACE" \
        --wireless-iface="$WIRELESS_IFACE" \
        --duration="$duration" \
        --no-server-spawn
    local rc=$?
    set -e

    # cleanup PC1 iperf3 server
    ssh "${ssh_opts[@]}" "$WIRED_HOST" 'pkill -f "iperf3 -s" 2>/dev/null; true' >/dev/null 2>&1 || true

    WBRIDGE_BENCH_OUTPUT="$saved_output"
    export WBRIDGE_BENCH_OUTPUT

    # 가장 최신 JSON에 env_label inject
    local latest
    latest=$(ls -1t "$out_dir/baseline/$engine"/*.json 2>/dev/null | head -1 || true)
    if [ -n "$latest" ]; then
        quick_inject_label "$latest" "$label"
        log_info "quick: result -> $latest"
    fi

    return $rc
}

# ============================================================
# matrix 측정 — 3 engine 자동 토글
# trap 으로 ORIGINAL_ENGINE 원복은 wbridge_bench.sh 가 자체 처리
# ============================================================
quick_run_matrix() {
    local duration="$1"
    local label="$2"

    # backup_engine 은 trap 콜백에서도 참조되므로 export 로 노출
    QUICK_MATRIX_BACKUP_ENGINE=$(get_engine)
    export QUICK_MATRIX_BACKUP_ENGINE
    log_info "quick: matrix backup engine=$QUICK_MATRIX_BACKUP_ENGINE"

    _quick_matrix_restore() {
        local target="${QUICK_MATRIX_BACKUP_ENGINE:-}"
        [ -n "$target" ] || return 0
        local cur
        cur=$(get_engine 2>/dev/null || echo "")
        if [ -n "$cur" ] && [ "$cur" != "$target" ]; then
            log_info "quick: restore engine -> $target"
            set_engine "$target" || log_err "engine restore failed"
        fi
    }
    trap '_quick_matrix_restore' EXIT INT TERM

    local engines
    read -ra engines <<<"$DEFAULT_ENGINES"
    local total="${#engines[@]}"
    local idx=0
    local fails=()

    for eng in "${engines[@]}"; do
        idx=$((idx+1))
        log_info "quick: [${idx}/${total}] engine=$eng"
        if ! quick_run_one "$eng" "$duration" "$label"; then
            fails+=("$eng")
        fi
        if [ "$idx" -lt "$total" ]; then
            log_info "quick: settle ${SETTLE_SEC}s before next engine"
            sleep "$SETTLE_SEC"
        fi
    done

    if [ ${#fails[@]} -gt 0 ]; then
        log_warn "quick: matrix failures: ${fails[*]}"
        return "$EXIT_MEASUREMENT_FAILED"
    fi
    return "$EXIT_OK"
}

# ============================================================
# report 호출 — wbridge_report.sh wrapper
# ============================================================
quick_report() {
    local out_file="$1"   # 빈값이면 stdout
    local report="${SCRIPT_DIR}/wbridge_report.sh"
    [ -x "$report" ] || die "$EXIT_CONFIG_MISSING" "wbridge_report.sh not found: $report"

    local base_dir="${WBRIDGE_BENCH_OUTPUT}/${PHASE:-$QUICK_PHASE_DEFAULT}/baseline"
    if [ ! -d "$base_dir" ]; then
        die "$EXIT_CONFIG_MISSING" "no quick results yet: $base_dir — run 'wbridge_quick.sh run' first"
    fi

    local label
    label="wbridge-quick (${ENV_LABEL:-unknown})"

    if [ -n "$out_file" ]; then
        "$report" --baseline-dir="$base_dir" --engines="$DEFAULT_ENGINES" --label="$label" --out="$out_file"
    else
        "$report" --baseline-dir="$base_dir" --engines="$DEFAULT_ENGINES" --label="$label"
    fi
}
