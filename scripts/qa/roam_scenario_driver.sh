#!/bin/bash
# Deterministic roaming-condition driver for wifi_roam@IFACE.
#
# 로밍 판정은 두 게이트를 통과해야 일어난다 — ① 현재 링크 RSSI < TH(진입) ②
# 후보 RSSI - baseline >= DIFF_TH(선택). 이 하네스는 ①을 link.json 주입으로,
# ②를 DIFF_TH 로 통제해 같은 시나리오를 반복 재현한다.
#
# 실기 실측으로 확정된 전제(설계가 여기에 묶여 있다):
#   * 주입은 **진입 게이트만** 통제한다. 후보 비교의 baseline 은 wifi_roam 이 스캔
#     결과에서 재산출하므로(baseline_from_entries), 주입값이 선택을 지배하지 못한다.
#     "현재보다 강한 후보"는 실제 AP 배치로 만들어야 한다.
#   * link.json 은 30초 stale 가드가 있다. 생산자를 멈추고 한 번만 쓰면 판정이
#     보류되므로(`link.json stale (Ns > 30s)`), 주입은 **주기적 재기록**이어야 한다.
#   * 현재 붙은 AP 가 최강이면 전환이 없는 것이 정상이다. 사전조건에서 걸러낸다.
#   * wpa_cli select_network 는 다른 network 를 disable 한다 — 이동 후 반드시
#     enable_network all 로 복구해야 데몬이 그 SSID 를 다시 고를 수 있다.
#
# ── 원격 실행 안전 (2026-08-29 사고 반영) ────────────────────────────────────
# 이 하네스를 ssh 로 돌리다 로컬에서 세션을 끊었더니 **원격 스크립트가 살아남아**
# 주입 루프가 계속 돌고 link logger 가 정지된 채 방치됐다. 실기가 시험 상태로 남았다.
# 원인과 대책은 넷이다.
#   1. 백그라운드 주입 루프가 ssh 의 stdout/stderr 를 물고 있어 세션이 닫히지 않았다
#      → 주입 루프의 stdio 를 분리한다. 그래야 ssh 가 닫히고 sshd 가 SIGHUP 을 보낸다.
#   2. trap 이 EXIT 만 걸려 있어 SIGHUP/SIGTERM 에 복원이 돌지 않았다
#      → INT TERM HUP 까지 건다.
#   3. 부모가 SIGKILL 되면 자식 주입 루프는 고아로 남는다
#      → 주입 루프가 부모 생존(/proc/<pid>)을 매 주기 확인하고 없으면 스스로 끝낸다.
#   4. 신호가 끝내 도착하지 않는 경우가 있다
#      → 스케줄 합계로 **자체 마감시각**을 정하고 초과하면 스스로 복원·종료한다.
# 프로덕션 코드는 건드리지 않는다. 만지는 것은 link.json(로그/캐시)과 DIFF_TH(공식
# `wifi roam diff` CLI) 둘뿐이고 어느 경로로 끝나든 복원한다.
set -Eeuo pipefail

IFACE=""
SCHEDULE=""
ARTIFACT=""
SEED_WEAK=0
ACK=0
DRY_RUN=0
INJECT_PERIOD_SEC=1
STALE_GUARD_SEC=30
DEADLINE_MARGIN_SEC=60
CMD_TIMEOUT_SEC=5

usage() {
    cat >&2 <<'USAGE'
usage: roam_scenario_driver.sh --iface <mlan0|mlan1> --schedule <file> --ack
                               [--seed-weak] [--artifact <dir>] [--dry-run]

  --schedule  TSV: <rssi> <hold_sec> <diff_th> [note...]   ('#' 주석, 빈 줄 무시)
  --ack       실기 상태를 바꾼다는 확인. 없으면 아무것도 하지 않는다.
  --seed-weak 시작 전 가장 약한 후보 AP 로 이동한다(전환 여지를 만든다).
  --dry-run   스케줄 검증과 계획만 출력하고 장치를 건드리지 않는다.
USAGE
    exit 64
}

# ── 순수 함수(테스트 대상) ───────────────────────────────────────────────────
validate_schedule_line() {
    local rssi="${1-}" hold="${2-}" diff="${3-}"

    [[ "$rssi" =~ ^-[0-9]+$ ]] || { echo "rssi must be a negative integer: '$rssi'" >&2; return 1; }
    [ "$rssi" -ge -100 ] && [ "$rssi" -le -10 ] || { echo "rssi out of range (-100..-10): $rssi" >&2; return 1; }
    [[ "$hold" =~ ^[0-9]+$ ]] && [ "$hold" -ge 1 ] || { echo "hold_sec must be a positive integer: '$hold'" >&2; return 1; }
    [ "$INJECT_PERIOD_SEC" -lt "$STALE_GUARD_SEC" ] || { echo "inject period must stay under the stale guard" >&2; return 1; }
    [[ "$diff" =~ ^[0-9]+$ ]] || { echo "diff_th must be a non-negative integer: '$diff'" >&2; return 1; }
    [ "$diff" -le 50 ] || { echo "diff_th out of range (0..50): $diff" >&2; return 1; }

    printf '%s\t%s\t%s\n' "$rssi" "$hold" "$diff"
}

parse_schedule() {
    local file="$1" line no=0 rc=0
    [ -f "$file" ] || { echo "schedule not found: $file" >&2; return 1; }
    while IFS= read -r line || [ -n "$line" ]; do
        no=$((no + 1))
        line="${line%%#*}"
        # shellcheck disable=SC2086  # 공백 분리가 목적
        set -- $line
        [ "$#" -eq 0 ] && continue
        if ! validate_schedule_line "${1-}" "${2-}" "${3-}"; then
            echo "  ↳ schedule line $no" >&2
            rc=1
        fi
    done < "$file"
    [ "$rc" -eq 0 ] || return 1
}

# 정규화된 스케줄(rssi\thold\tdiff 줄들)의 총 hold 초. 자체 마감시각의 근거다.
schedule_total_seconds() {
    local total=0 rssi hold diff
    while IFS=$'\t' read -r rssi hold diff; do
        [ -n "${hold:-}" ] || continue
        total=$((total + hold))
    done
    printf '%s\n' "$total"
}

current_is_strongest() {
    local cur="$1" best_b="" best_r=-999 b r
    while read -r b r; do
        [ -n "$b" ] || continue
        if [ "$r" -gt "$best_r" ]; then best_r="$r"; best_b="$b"; fi
    done
    [ "$best_b" = "$cur" ]
}

weakest_other_bssid() {
    local cur="$1" worst_b="" worst_r=999 b r
    while read -r b r; do
        [ -n "$b" ] || continue
        [ "$b" = "$cur" ] && continue
        if [ "$r" -lt "$worst_r" ]; then worst_r="$r"; worst_b="$b"; fi
    done
    [ -n "$worst_b" ] || return 1
    printf '%s\n' "$worst_b"
}

restore_unit_active_state() {
    local unit="$1" was_active="$2"
    if [ "$was_active" = 1 ]; then
        systemctl start "$unit" && systemctl is-active --quiet "$unit"
    else
        systemctl stop "$unit" && ! systemctl is-active --quiet "$unit"
    fi
}

# 주입 루프. **부모가 사라지면 스스로 끝낸다** — 부모가 SIGKILL 돼도 고아로 남지 않는다.
# 호출부는 stdio 를 분리해 띄운다(그래야 ssh 세션이 닫히고 sshd 가 SIGHUP 을 보낸다).
inject_loop() {
    local link="$1" rssi="$2" parent="$3"
    while [ -e "/proc/$parent" ]; do
        if jq --arg v "${rssi} dBm" '.link.signal = $v | .link.signal_avg = $v' \
              "$link" > "${link}.inj" 2>/dev/null; then
            mv "${link}.inj" "$link"
        else
            rm -f "${link}.inj"
        fi
        sleep "$INJECT_PERIOD_SEC"
    done
}

# ── 실기 경로 ────────────────────────────────────────────────────────────────
# 외부 명령이 멈춰도 마감시각 검사가 돌 수 있도록 전부 시간 상한을 씌운다.
wpa() { timeout "$CMD_TIMEOUT_SEC" wpa_cli -i "$IFACE" "$@"; }
status_field() { wpa status 2>/dev/null | sed -n "s/^$1=//p"; }
scan_candidates() { wpa scan_results 2>/dev/null | awk 'NR>1 && NF>=5 {print $1, $3}'; }

main() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --iface) IFACE="${2-}"; shift 2 ;;
            --schedule) SCHEDULE="${2-}"; shift 2 ;;
            --artifact) ARTIFACT="${2-}"; shift 2 ;;
            --seed-weak) SEED_WEAK=1; shift ;;
            --ack) ACK=1; shift ;;
            --dry-run) DRY_RUN=1; shift ;;
            *) usage ;;
        esac
    done
    case "$IFACE" in mlan0|mlan1) ;; *) usage ;; esac
    [ -n "$SCHEDULE" ] || usage

    local plan total deadline
    plan="$(parse_schedule "$SCHEDULE")" || { echo "schedule rejected" >&2; exit 65; }
    total="$(printf '%s\n' "$plan" | schedule_total_seconds)"
    echo "== schedule (rssi / hold_sec / diff_th) — total ${total}s =="
    printf '%s\n' "$plan"

    if [ "$DRY_RUN" = 1 ]; then
        echo "dry-run: 장치를 건드리지 않고 종료한다."
        exit 0
    fi
    [ "$ACK" = 1 ] || { echo "refusing to touch the device without --ack" >&2; exit 66; }
    [ "$(id -u)" = 0 ] || { echo "must run as root on the target" >&2; exit 67; }

    local link="/var/log/cantops/json/${IFACE}/link.json"
    local conf="/usr/local/etc/wifi_init_conf.json"
    local unit="wifi_logger_link@${IFACE}"
    [ -f "$link" ] || { echo "missing $link" >&2; exit 68; }

    local orig_diff was_active inj_pid=""
    orig_diff="$(jq -r --arg i "$IFACE" '.[$i].roaming.DIFF_TH' "$conf")"
    was_active=0; systemctl is-active --quiet "$unit" && was_active=1

    restore() {
        local rc=$?
        trap - EXIT INT TERM HUP
        echo "== restore =="
        if [ -n "$inj_pid" ]; then kill "$inj_pid" 2>/dev/null || true; fi
        timeout 20 /usr/local/bin/wifi "$IFACE" roam diff "$orig_diff" >/dev/null 2>&1 || true
        wpa enable_network all >/dev/null 2>&1 || true
        restore_unit_active_state "$unit" "$was_active" \
            || { echo "restore failed for $unit" >&2; rc=70; }
        echo "DIFF_TH=$orig_diff, all networks enabled, $unit restored"
        exit "$rc"
    }
    # EXIT 만으로는 ssh 절단(SIGHUP)·외부 종료(SIGTERM)에서 복원이 돌지 않는다.
    trap restore EXIT INT TERM HUP

    # 신호가 끝내 오지 않아도 하네스가 무한히 남지 않도록 자체 마감시각을 둔다.
    deadline=$(( $(date +%s) + total + DEADLINE_MARGIN_SEC ))

    wpa scan >/dev/null 2>&1 || true; sleep 3
    local cur; cur="$(status_field bssid)"
    echo "== start: bssid=$cur ssid=$(status_field ssid) DIFF_TH=$orig_diff deadline=+$((total + DEADLINE_MARGIN_SEC))s =="

    if scan_candidates | current_is_strongest "$cur"; then
        if [ "$SEED_WEAK" = 1 ]; then
            local weak; weak="$(scan_candidates | weakest_other_bssid "$cur")" || {
                echo "no other candidate to seed onto" >&2; exit 69; }
            echo "seed-weak: $cur -> $weak"
            wpa roam "$weak" >/dev/null 2>&1 || true
            sleep 5
            wpa enable_network all >/dev/null 2>&1 || true
            cur="$(status_field bssid)"
            echo "seeded: bssid=$cur"
        else
            echo "!! 현재 AP 가 최강이라 전환이 일어나지 않는 것이 정상이다." >&2
            echo "   --seed-weak 로 약한 AP 로 이동한 뒤 실행하라." >&2
            exit 69
        fi
    fi

    systemctl stop "$unit"
    local rssi hold diff from_b to_b i
    while IFS=$'\t' read -r rssi hold diff; do
        [ -n "$rssi" ] || continue
        from_b="$(status_field bssid)"
        timeout 20 /usr/local/bin/wifi "$IFACE" roam diff "$diff" >/dev/null 2>&1 || true
        if [ -n "$inj_pid" ]; then kill "$inj_pid" 2>/dev/null || true; fi
        # stdio 분리 — 이게 없으면 ssh 세션이 닫히지 않아 원격에 하네스가 남는다.
        inject_loop "$link" "$rssi" "$$" </dev/null >/dev/null 2>&1 &
        inj_pid=$!

        to_b="$from_b"
        for ((i = 1; i <= hold; i++)); do
            if [ "$(date +%s)" -ge "$deadline" ]; then
                echo "!! deadline exceeded — 남은 구간을 중단하고 복원한다" >&2
                return 0
            fi
            sleep 1
            to_b="$(status_field bssid)"
            [ "$to_b" != "$from_b" ] && break
        done
        if [ "$to_b" != "$from_b" ]; then
            echo "rssi=$rssi diff_th=$diff hold=${hold}s -> ROAMED @${i}s $from_b -> $to_b (ssid=$(status_field ssid))"
        else
            echo "rssi=$rssi diff_th=$diff hold=${hold}s -> no transition (still $from_b)"
        fi
    done <<< "$plan"

    if [ -n "$ARTIFACT" ]; then
        # 자격증명·원본 설정은 남기지 않는다 — 판정에 필요한 사실만.
        mkdir -p "$ARTIFACT"
        grep -a ROAM /var/log/cantops/logger.log 2>/dev/null | tail -200 > "$ARTIFACT/roam.log" || true
        echo "artifact: $ARTIFACT/roam.log"
    fi
}

# 테스트가 순수 함수만 가져다 쓸 수 있도록 실행을 가드한다.
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
    main "$@"
fi
