#!/bin/bash
# wbridge_quick.sh — 보드 측 빠른 측정 wrapper
# Plan: wbridge-quick.plan.md FR-Q1~Q8
# Design: wbridge-quick.design.md §2~4
#
# 보드 SSH 들어간 상태에서 한 명령으로 setup/check/run/report 까지.
# 기존 wbridge_bench.sh / wbridge_report.sh / wbridge_diag.sh 는 변경 없이 재활용.
#
# Usage:
#   wbridge_quick.sh setup [--no-interactive --wired-host=... --wireless-host=... --label=...]
#   wbridge_quick.sh check
#   wbridge_quick.sh run [--engine=ENG] [--matrix] [--duration=N] [--label=ENV]
#   wbridge_quick.sh report [--out=FILE]
#   wbridge_quick.sh help
set -euo pipefail
TAG=$(basename "$0")
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
. "${SCRIPT_DIR}/lib/_common.sh"
. "${SCRIPT_DIR}/lib/_quick.sh"

# ============================================================
# usage
# ============================================================
usage() {
    cat <<EOF
${TAG} — 보드 측 빠른 측정 wrapper

Usage:
  ${TAG} setup [opts]                대화식 wizard로 /etc/wbridge/quick.conf 생성
  ${TAG} check                       보드 + PC1 + PC2 환경 일괄 점검
  ${TAG} run [opts]                  단일 engine 또는 매트릭스 측정
  ${TAG} report [--out=FILE]         최신 결과로 markdown 리포트 (stdout)
  ${TAG} help                        도움말

setup options:
  --no-interactive          비대화식 (모든 키 인자로 받음)
  --wired-host=user@HOST    PC1 SSH 대상
  --wired-iface=eth0        PC1 NIC
  --wireless-host=user@HOST PC2 SSH 대상
  --wireless-iface=mlan0    PC2 무선 NIC
  --duration=30             iperf3 1회 측정 (초)
  --label=wifi6-pure        환경 식별자 (리포트 헤더)
  --settle=5                engine 토글 후 대기 (초)
  --auto-ssh-keys           SSH 키 자동 셋업 (ssh-keygen + ssh-copy-id)
  --no-ssh-keys             SSH 키 자동 셋업 skip (대화식에서도)

run options:
  --engine=pcap|tpacket|moal  단일 engine (생략 시 현재 engine)
  --matrix                    3 engine 자동 토글 (DEFAULT_ENGINES)
  --duration=N                conf의 DURATION override
  --label=ENV                 conf의 ENV_LABEL override

전제조건:
  - root 권한
  - 보드: jq, bc, mpstat, ssh
  - PC1/PC2: iperf3, ip, ping + 보드에서 BatchMode SSH 가능

Exit codes:
  0 = OK, 1 = config-missing, 2 = measurement-failed, 3 = regression
EOF
}

# ============================================================
# subcommand: setup (wizard)
# ============================================================
cmd_setup() {
    require_root

    local NO_INTERACTIVE=0
    local SSH_KEYS_MODE=ask    # ask | auto | skip
    # 기존 conf default 로 미리 로드 (있으면)
    if [ -f "$QUICK_CONF_FILE" ]; then
        # shellcheck disable=SC1090
        . "$QUICK_CONF_FILE" || true
    fi
    : "${WIRED_IFACE:=eth0}"
    : "${WIRELESS_IFACE:=mlan0}"
    : "${DURATION:=30}"
    : "${DEFAULT_ENGINES:=pcap tpacket moal}"
    : "${ENV_LABEL:=unknown}"
    : "${SETTLE_SEC:=5}"
    : "${PHASE:=$QUICK_PHASE_DEFAULT}"

    while [ $# -gt 0 ]; do
        case "$1" in
            --no-interactive)     NO_INTERACTIVE=1 ;;
            --wired-host=*)       WIRED_HOST="${1#*=}" ;;
            --wired-iface=*)      WIRED_IFACE="${1#*=}" ;;
            --wireless-host=*)    WIRELESS_HOST="${1#*=}" ;;
            --wireless-iface=*)   WIRELESS_IFACE="${1#*=}" ;;
            --duration=*)         DURATION="${1#*=}" ;;
            --label=*)            ENV_LABEL="${1#*=}" ;;
            --settle=*)           SETTLE_SEC="${1#*=}" ;;
            --default-engines=*)  DEFAULT_ENGINES="${1#*=}" ;;
            --phase=*)            PHASE="${1#*=}" ;;
            --auto-ssh-keys)      SSH_KEYS_MODE=auto ;;
            --no-ssh-keys)        SSH_KEYS_MODE=skip ;;
            -h|--help)            usage; exit 0 ;;
            *) die "$EXIT_CONFIG_MISSING" "unknown setup arg: $1" ;;
        esac
        shift
    done

    if [ "$NO_INTERACTIVE" -eq 0 ]; then
        printf '=== wbridge-quick setup wizard ===\n' >&2
        quick_prompt WIRED_HOST       'WIRED_HOST    (예: root@192.168.1.10)' "${WIRED_HOST:-}"
        quick_prompt WIRED_IFACE      'WIRED_IFACE   (예: eth0)'              "$WIRED_IFACE"
        quick_prompt WIRELESS_HOST    'WIRELESS_HOST (예: jhw@192.168.0.50)'  "${WIRELESS_HOST:-}"
        quick_prompt WIRELESS_IFACE   'WIRELESS_IFACE(예: mlan0)'             "$WIRELESS_IFACE"
        quick_prompt DURATION         'DURATION sec'                          "$DURATION"
        quick_prompt ENV_LABEL        'ENV_LABEL    (예: wifi6-pure)'         "$ENV_LABEL"
        quick_prompt SETTLE_SEC       'SETTLE_SEC   (engine 토글 후 대기)'    "$SETTLE_SEC"
    fi

    [ -n "${WIRED_HOST:-}" ]    || die "$EXIT_CONFIG_MISSING" "WIRED_HOST 필수 (예: root@192.168.1.10)"
    [ -n "${WIRELESS_HOST:-}" ] || die "$EXIT_CONFIG_MISSING" "WIRELESS_HOST 필수"

    quick_save_conf

    # SSH 키 자동 셋업 결정
    local do_ssh=0
    case "$SSH_KEYS_MODE" in
        auto) do_ssh=1 ;;
        skip) do_ssh=0 ;;
        ask)
            if [ "$NO_INTERACTIVE" -eq 1 ]; then
                # 비대화식 + --auto-ssh-keys/--no-ssh-keys 미지정 → default skip
                do_ssh=0
                log_info "비대화식 + SSH 키 모드 미지정 → skip (필요 시 --auto-ssh-keys)"
            else
                local ans=""
                printf '\nSSH 키 자동 셋업(ssh-keygen + ssh-copy-id)을 진행할까요? [Y/n] ' >&2
                IFS= read -r ans || true
                case "$ans" in
                    n|N|no|No|NO) do_ssh=0 ;;
                    *)            do_ssh=1 ;;
                esac
            fi
            ;;
    esac

    if [ "$do_ssh" -eq 1 ]; then
        quick_setup_ssh_keys || log_warn "SSH 키 셋업 미완 — check 단계에서 재확인하세요"
    fi

    printf '\n다음: %s check\n' "$TAG"
}

# ============================================================
# subcommand: check
# ============================================================
cmd_check() {
    require_root
    quick_load_conf

    local rc=0
    quick_check_board || rc=$?
    quick_check_remote "$WIRED_HOST"    "$WIRED_IFACE"    'wired'    || rc=$?
    quick_check_remote "$WIRELESS_HOST" "$WIRELESS_IFACE" 'wireless' || rc=$?

    printf '\n=== verdict ===\n'
    if [ "$rc" -eq 0 ]; then
        printf 'ALL OK — %s run 실행 가능\n' "$TAG"
    else
        printf 'FAIL — 위 [FAIL] 항목 해결 후 %s check 재실행\n' "$TAG"
    fi
    return "$rc"
}

# ============================================================
# subcommand: run
# ============================================================
cmd_run() {
    require_root
    quick_load_conf

    local ENGINE=""
    local MATRIX=0
    while [ $# -gt 0 ]; do
        case "$1" in
            --engine=*)     ENGINE="${1#*=}" ;;
            --matrix)       MATRIX=1 ;;
            --duration=*)   DURATION="${1#*=}" ;;
            --label=*)      ENV_LABEL="${1#*=}" ;;
            -h|--help)      usage; exit 0 ;;
            *) die "$EXIT_CONFIG_MISSING" "unknown run arg: $1" ;;
        esac
        shift
    done

    if [ "$MATRIX" -eq 1 ] && [ -n "$ENGINE" ]; then
        die "$EXIT_CONFIG_MISSING" "--matrix 와 --engine 동시 사용 불가"
    fi

    if [ "$MATRIX" -eq 1 ]; then
        log_info "quick: matrix mode engines=[$DEFAULT_ENGINES] duration=${DURATION}s label=$ENV_LABEL"
        quick_run_matrix "$DURATION" "$ENV_LABEL"
    else
        if [ -z "$ENGINE" ]; then
            ENGINE=$(get_engine)
            log_info "quick: --engine 생략, 현재 engine 사용 = $ENGINE"
        fi
        quick_run_one "$ENGINE" "$DURATION" "$ENV_LABEL"
    fi

    printf '\n다음: %s report\n' "$TAG"
}

# ============================================================
# subcommand: report
# ============================================================
cmd_report() {
    quick_load_conf

    local OUT=""
    while [ $# -gt 0 ]; do
        case "$1" in
            --out=*)    OUT="${1#*=}" ;;
            --out)      shift; OUT="${1:-}" ;;
            -h|--help)  usage; exit 0 ;;
            *) die "$EXIT_CONFIG_MISSING" "unknown report arg: $1" ;;
        esac
        shift
    done

    quick_report "$OUT"
}

# ============================================================
# conf 자동 보강 — conf 없으면 wizard 자동 시작 (대화식 tty 한정)
#   - 시리얼/터미널: wizard 자동 → 저장 → 본 sub 진행
#   - 비대화식 SSH/script: hang 방지 위해 친절 에러
# ============================================================
ensure_conf_or_wizard() {
    [ -f "$QUICK_CONF_FILE" ] && return 0
    if [ -t 0 ] && [ -t 1 ]; then
        log_info "conf 없음 → setup wizard 자동 시작 (한 번만)"
        cmd_setup
        printf '\n--- wizard 완료, 본 명령 이어서 진행 ---\n\n' >&2
    else
        die "$EXIT_CONFIG_MISSING" \
            "conf 없음: $QUICK_CONF_FILE — 시리얼/터미널에서 '$TAG setup' 한 번 실행 또는 '$TAG setup --no-interactive --wired-host=... --wireless-host=... --label=...'"
    fi
}

# ============================================================
# dispatcher
# ============================================================
SUB="${1:-help}"
[ $# -gt 0 ] && shift || true

case "$SUB" in
    setup)   cmd_setup  "$@" ;;
    check)   ensure_conf_or_wizard; cmd_check  "$@" ;;
    run)     ensure_conf_or_wizard; cmd_run    "$@" ;;
    report)  ensure_conf_or_wizard; cmd_report "$@" ;;
    help|-h|--help) usage; exit 0 ;;
    *) usage; die "$EXIT_CONFIG_MISSING" "unknown subcommand: $SUB" ;;
esac
