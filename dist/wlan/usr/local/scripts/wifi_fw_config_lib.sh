#!/bin/bash
# FW tuning 설정의 association 전 적용과 연결 후 제한 복구·GET 확인 공용 함수.

: "${WIFI_MLANUTL:=mlanutl}"
: "${WIFI_WPA_CLI:=wpa_cli}"
: "${WIFI_FW_LOGGER:=logger}"
: "${WIFI_MCS_VERIFY_ATTEMPTS:=5}"
: "${WIFI_MCS_VERIFY_DELAY_SEC:=1}"
: "${WIFI_MCS_PENDING_DIR:=/run/wifi}"

wifi_fw_log() {
    local priority="$1"
    shift
    "$WIFI_FW_LOGGER" -p "$priority" "[wifi_fw_config:$LINENO] $*" 2>/dev/null || true
}

_wifi_fw_has_section() {
    local json="$1" iface="$2" section="$3"
    jq -e --arg i "$iface" --arg s "$section" '.[$i][$s] != null' "$json" >/dev/null 2>&1
}

# 0.5.2 이하 wifi CLI는 jq 식에 MCS 값을 숫자로 넣었다. 0.5.3의 스키마와
# 런타임 계약은 문자열이며, mlan0 HE는 Tx/Rx 방향을 명시한다. 업그레이드 시
# 유효한 레거시 값만 정규형으로 바꾸고 알 수 없는 값은 그대로 남겨 검증기가
# 거부하도록 한다.
wifi_fw_normalize_legacy_mcs_json() {
    local json="$1"
    jq '
        def canonical_number($allowed):
            . as $value
            | if (($value | type) == "number" and (($allowed | index($value)) != null))
              then ($value | tostring)
              else $value
              end;
        def normalize_common($iface):
            if (.[$iface].mcs_tier? | type) == "object" then
                (if (.[$iface].mcs_tier | has("ht"))
                 then .[$iface].mcs_tier.ht |= canonical_number([7, 15])
                 else . end)
                | (if (.[$iface].mcs_tier | has("vht"))
                   then .[$iface].mcs_tier.vht |= canonical_number([7, 8, 9])
                   else . end)
            else . end;
        normalize_common("mlan0")
        | normalize_common("mlan1")
        | if ((.mlan0.mcs_tier? | type) == "object"
              and (.mlan0.mcs_tier | has("he"))) then
              .mlan0.mcs_tier.he |= (
                  . as $value
                  | if (($value | type) == "number"
                        and (([7, 9, 11] | index($value)) != null))
                    then "both \($value)"
                    else $value
                    end
              )
          else . end
        | if ((.mlan1.mcs_tier? | type) == "object"
              and (.mlan1.mcs_tier | has("he"))) then
              .mlan1.mcs_tier.he |= (
                  . as $value
                  | if ((($value | type) == "number"
                         and (([7, 9, 11] | index($value)) != null))
                        or (($value | type) == "string"
                            and ((["7", "9", "11", "both 7", "both 9", "both 11"]
                                  | index($value)) != null)))
                    then ""
                    else $value
                    end
              )
          else . end
    ' "$json"
}

wifi_fw_validate_rate_config() {
    local json="$1" iface="$2" values mode low high interval
    _wifi_fw_has_section "$json" "$iface" rate_adapt || return 2

    jq -e --arg i "$iface" '
        .[$i].rate_adapt as $r
        | ($r | type) == "object"
        and ($r | has("mode") and has("low_thresh") and has("high_thresh") and has("interval_ms"))
        and ([$r.mode, $r.low_thresh, $r.high_thresh, $r.interval_ms]
             | all(type == "number" and floor == .))
    ' "$json" >/dev/null 2>&1 || return 1

    values=$(jq -er --arg i "$iface" '
        .[$i].rate_adapt | [.mode,.low_thresh,.high_thresh,.interval_ms] | @tsv
    ' "$json" 2>/dev/null) || return 1
    IFS=$'\t' read -r mode low high interval <<< "$values"

    case "$mode" in 0|1) ;; *) return 1 ;; esac
    [ "$interval" -gt 0 ] 2>/dev/null && [ $((interval % 10)) -eq 0 ] || return 1

    if [ "$low" -eq 255 ] 2>/dev/null || [ "$high" -eq 255 ] 2>/dev/null; then
        [ "$low" -eq 255 ] && [ "$high" -eq 255 ] || return 1
    else
        [ "$low" -ge 0 ] && [ "$low" -le 100 ] \
            && [ "$high" -ge 0 ] && [ "$high" -le 100 ] \
            && [ "$low" -lt "$high" ] || return 1
    fi
}

wifi_fw_apply_rate() {
    local json="$1" iface="$2" values mode low high interval_ms interval live
    if ! _wifi_fw_has_section "$json" "$iface" rate_adapt; then
        wifi_fw_log local0.info "[$iface] rate_adapt absent; skip"
        return 0
    fi
    if ! wifi_fw_validate_rate_config "$json" "$iface"; then
        wifi_fw_log local0.err "[$iface] invalid/partial rate_adapt section; skip entire section"
        return 0
    fi

    values=$(jq -er --arg i "$iface" '
        .[$i].rate_adapt | [.mode,.low_thresh,.high_thresh,.interval_ms] | @tsv
    ' "$json" 2>/dev/null) || return 0
    IFS=$'\t' read -r mode low high interval_ms <<< "$values"
    interval=$((interval_ms / 10))

    wifi_fw_log local0.info "[$iface] rate_adapt configured: mode=$mode low=$low high=$high interval_ms=$interval_ms"
    if ! "$WIFI_MLANUTL" "$iface" rate_adapt_cfg "$mode" "$low" "$high" "$interval" >/dev/null 2>&1; then
        wifi_fw_log local0.err "[$iface] rate_adapt_cfg SET failed"
        return 0
    fi
    live=$("$WIFI_MLANUTL" "$iface" rate_adapt_cfg 2>&1) || {
        wifi_fw_log local0.warn "[$iface] rate_adapt_cfg GET failed after SET"
        return 0
    }
    wifi_fw_log local0.info "[$iface] rate_adapt live after pre-association SET: $(printf '%s' "$live" | tr '\n' ' ')"
}

wifi_fw_validate_mcs_config() {
    local json="$1" iface="$2" enabled standard ht vht he
    _wifi_fw_has_section "$json" "$iface" mcs_tier || return 2
    enabled=$(jq -r --arg i "$iface" '.[$i].mcs_tier.enabled // false' "$json" 2>/dev/null)
    [ "$enabled" = true ] || return 2

    jq -e --arg i "$iface" '
        (.[$i].mcs_tier | type) == "object"
        and (.[$i].mcs_tier | has("ht") and has("vht") and has("he"))
        and ([.[$i].mcs_tier.ht,.[$i].mcs_tier.vht,.[$i].mcs_tier.he] | all(type == "string"))
    ' "$json" >/dev/null 2>&1 || return 1

    standard=$(jq -r --arg i "$iface" '.[$i].STANDARD // "" | ascii_downcase' "$json" 2>/dev/null)
    ht=$(jq -r --arg i "$iface" '.[$i].mcs_tier.ht' "$json" 2>/dev/null)
    vht=$(jq -r --arg i "$iface" '.[$i].mcs_tier.vht' "$json" 2>/dev/null)
    he=$(jq -r --arg i "$iface" '.[$i].mcs_tier.he' "$json" 2>/dev/null)

    case "$ht" in 7|15) ;; *) return 1 ;; esac
    case "$vht" in 7|8|9) ;; *) return 1 ;; esac
    if [ "$standard" = ax ] || [ "$standard" = 6 ]; then
        case "$he" in 'both 7'|'both 9'|'both 11') ;; *) return 1 ;; esac
    fi
}

_wifi_fw_map_hex() {
    case "$1" in
        7) printf 'FFF0\n' ;;
        8) printf 'FFF5\n' ;;
        9|11) printf 'FFFA\n' ;;
        *) return 1 ;;
    esac
}

_wifi_fw_verify_mcs_base_get() {
    local ht="$1" vht="$2" mcs_get="$3" vht_map

    printf '%s\n' "$mcs_get" | grep -Eq "HT .*MCS 0~${ht}([)]|$)" || return 1
    vht_map=$(_wifi_fw_map_hex "$vht") || return 1
    [ "$(printf '%s\n' "$mcs_get" | awk '/VHT Tx:/ {sub(/^0x/,"",$3); print toupper($3); exit}')" = "$vht_map" ] || return 1
    [ "$(printf '%s\n' "$mcs_get" | awk '/VHT Rx:/ {sub(/^0x/,"",$3); print toupper($3); exit}')" = "$vht_map" ] || return 1
}

_wifi_fw_verify_mcs_he_get() {
    local he="$1" mcs_get="$2" ax_get="$3" he_max he_map le

    he_max=${he##* }
    he_map=$(_wifi_fw_map_hex "$he_max") || return 1
    [ "$(printf '%s\n' "$mcs_get" | awk '/HE Tx:/ {sub(/^0x/,"",$3); print toupper($3); exit}')" = "$he_map" ] || return 1
    [ "$(printf '%s\n' "$mcs_get" | awk '/HE Rx:/ {sub(/^0x/,"",$3); print toupper($3); exit}')" = "$he_map" ] || return 1

    # 11axcfg raw IE map은 little-endian Rx/Tx 16-bit map 두 개가 연속된다.
    le=$(printf '%s %s %s %s' \
        "$(printf '%s' "$he_map" | cut -c3-4 | tr 'A-F' 'a-f')" \
        "$(printf '%s' "$he_map" | cut -c1-2 | tr 'A-F' 'a-f')" \
        "$(printf '%s' "$he_map" | cut -c3-4 | tr 'A-F' 'a-f')" \
        "$(printf '%s' "$he_map" | cut -c1-2 | tr 'A-F' 'a-f')")
    printf '%s\n' "$ax_get" | tail -n +2 | tr 'A-F' 'a-f' | tr '\n' ' ' | grep -Fq "$le" || return 1
}

_wifi_fw_mcs_he_unobservable() {
    local mcs_get="$1" tx rx

    tx=$(printf '%s\n' "$mcs_get" | awk '/HE Tx:/ {sub(/^0x/,"",$3); print toupper($3); exit}')
    rx=$(printf '%s\n' "$mcs_get" | awk '/HE Rx:/ {sub(/^0x/,"",$3); print toupper($3); exit}')
    [ "$tx" = 0000 ] && [ "$rx" = 0000 ]
}

_wifi_fw_verify_mcs_get() {
    local standard="$1" ht="$2" vht="$3" he="$4" mcs_get="$5" ax_get="$6"

    _wifi_fw_verify_mcs_base_get "$ht" "$vht" "$mcs_get" || return 1
    if [ "$standard" = ax ] || [ "$standard" = 6 ]; then
        _wifi_fw_verify_mcs_he_get "$he" "$mcs_get" "$ax_get" || return 1
    fi
}

_wifi_fw_mcs_pending_path() {
    local iface="$1"
    case "$iface" in ''|*[!A-Za-z0-9_.-]*) return 1 ;; esac
    printf '%s/mcs_verify_pending_%s\n' "$WIFI_MCS_PENDING_DIR" "$iface"
}

_wifi_fw_mcs_reassociate_path() {
    local iface="$1"
    case "$iface" in ''|*[!A-Za-z0-9_.-]*) return 1 ;; esac
    printf '%s/mcs_reassociate_once_%s\n' "$WIFI_MCS_PENDING_DIR" "$iface"
}

wifi_fw_mcs_pending() {
    local path
    path=$(_wifi_fw_mcs_pending_path "$1") || return 1
    [ -e "$path" ]
}

wifi_fw_mcs_defer() {
    local iface="$1" path tmp reassociate_path
    path=$(_wifi_fw_mcs_pending_path "$iface") || return 1
    reassociate_path=$(_wifi_fw_mcs_reassociate_path "$iface") || return 1
    mkdir -p -- "$WIFI_MCS_PENDING_DIR" || return 1
    rm -f -- "$reassociate_path"
    tmp="${path}.tmp.$$"
    : > "$tmp" && mv -f -- "$tmp" "$path" || {
        rm -f -- "$tmp"
        return 1
    }
}

wifi_fw_mcs_clear_pending() {
    local path reassociate_path
    path=$(_wifi_fw_mcs_pending_path "$1") || return 1
    reassociate_path=$(_wifi_fw_mcs_reassociate_path "$1") || return 1
    rm -f -- "$path" "${path}.tmp.$$" "$reassociate_path" "${reassociate_path}.tmp.$$"
}

wifi_fw_mcs_reassociate_attempted() {
    local path
    path=$(_wifi_fw_mcs_reassociate_path "$1") || return 1
    [ -e "$path" ]
}

wifi_fw_mcs_mark_reassociate() {
    local iface="$1" path tmp
    path=$(_wifi_fw_mcs_reassociate_path "$iface") || return 1
    mkdir -p -- "$WIFI_MCS_PENDING_DIR" || return 1
    tmp="${path}.tmp.$$"
    : > "$tmp" && mv -f -- "$tmp" "$path" || {
        rm -f -- "$tmp"
        return 1
    }
}

wifi_fw_mcs_clear_reassociate() {
    local path
    path=$(_wifi_fw_mcs_reassociate_path "$1") || return 1
    rm -f -- "$path" "${path}.tmp.$$"
}

wifi_fw_apply_mcs_verified() {
    local json="$1" iface="$2" standard ht vht he attempt mcs_get ax_get="" validation_rc
    local -a args
    if ! _wifi_fw_has_section "$json" "$iface" mcs_tier; then
        wifi_fw_mcs_clear_pending "$iface" 2>/dev/null || true
        return 0
    fi
    if wifi_fw_validate_mcs_config "$json" "$iface"; then
        :
    else
        validation_rc=$?
        case "$validation_rc" in
            2) wifi_fw_mcs_clear_pending "$iface" 2>/dev/null || true; return 0 ;;
            *) wifi_fw_mcs_clear_pending "$iface" 2>/dev/null || true
               wifi_fw_log local0.err "[$iface] invalid/partial mcs_tier section; skip"; return 0 ;;
        esac
    fi

    standard=$(jq -r --arg i "$iface" '.[$i].STANDARD // "" | ascii_downcase' "$json")
    ht=$(jq -r --arg i "$iface" '.[$i].mcs_tier.ht' "$json")
    vht=$(jq -r --arg i "$iface" '.[$i].mcs_tier.vht' "$json")
    he=$(jq -r --arg i "$iface" '.[$i].mcs_tier.he' "$json")
    args=(ht "$ht" vht "$vht")
    if [ "$standard" = ax ] || [ "$standard" = 6 ]; then
        args+=(he both "${he##* }")
    elif [ -n "$he" ]; then
        wifi_fw_log local0.warn "[$iface] STANDARD=$standard has no HE support; ignoring configured he='$he'"
    fi

    case "$WIFI_MCS_VERIFY_ATTEMPTS" in ''|*[!0-9]*) WIFI_MCS_VERIFY_ATTEMPTS=5 ;; esac
    [ "$WIFI_MCS_VERIFY_ATTEMPTS" -gt 0 ] || WIFI_MCS_VERIFY_ATTEMPTS=5
    for ((attempt=1; attempt<=WIFI_MCS_VERIFY_ATTEMPTS; attempt++)); do
        mcs_get=""
        ax_get=""
        wifi_fw_log local0.info "[$iface] mcstiercfg SET attempt $attempt/$WIFI_MCS_VERIFY_ATTEMPTS: ${args[*]}"
        if "$WIFI_MLANUTL" "$iface" mcstiercfg "${args[@]}" >/dev/null 2>&1; then
            mcs_get=$("$WIFI_MLANUTL" "$iface" mcstiercfg 2>&1 || true)
            if [ "$standard" = ax ] || [ "$standard" = 6 ]; then
                ax_get=$("$WIFI_MLANUTL" "$iface" 11axcfg 2>&1 || true)
            else
                ax_get=""
            fi
            if _wifi_fw_verify_mcs_get "$standard" "$ht" "$vht" "$he" "$mcs_get" "$ax_get"; then
                wifi_fw_mcs_clear_pending "$iface" 2>/dev/null || true
                wifi_fw_log local0.info "[$iface] mcstiercfg verified before association on attempt $attempt"
                return 0
            fi
        fi
        [ "$attempt" -lt "$WIFI_MCS_VERIFY_ATTEMPTS" ] && sleep "$WIFI_MCS_VERIFY_DELAY_SEC"
    done

    # 88W9098은 association 전 GET에서 HT/VHT 적용값은 보이지만 HE map만 0x0000으로
    # 보고할 수 있다. 이 명시적인 미가시 상태는 SET 실패가 아니라
    # association-dependent visibility다. 비영(非零) HE 오설정은 완화하지 않는다.
    # supplicant 시작을 막지 않고 per-iface pending을 남겨 첫 CONNECTED/ROAMED 이벤트에서
    # 검증하고, 필요 시 connected SET+reassociate 1회로 확정한다. HT/VHT도 다르거나
    # SET/GET 자체가 실패한 경우는 기존처럼 fatal.
    if { [ "$standard" = ax ] || [ "$standard" = 6 ]; } \
       && _wifi_fw_verify_mcs_base_get "$ht" "$vht" "$mcs_get" \
       && _wifi_fw_mcs_he_unobservable "$mcs_get"; then
        if wifi_fw_mcs_defer "$iface"; then
            wifi_fw_log local0.warn "[$iface] mcstiercfg HT/VHT applied but HE not observable before association; deferred connected verification"
            return 0
        fi
        wifi_fw_log local0.emerg "[$iface] cannot persist deferred MCS verification marker"
    fi

    wifi_fw_log local0.emerg "[$iface] mcstiercfg GET mismatch after $WIFI_MCS_VERIFY_ATTEMPTS attempts; association must not start"
    return 1
}

# Deferred marker가 있을 때만 연결 후 확인한다. GET이 맞으면 종료하고, 첫 association이
# FW 기본값으로 되돌린 경우 connected SET으로 다음 association 값을 저장한 뒤 한 번만
# reassociate한다. 실패는 wifi_init/reboot로 전파하지 않고 링크와 pending을 보존한다.
_wifi_fw_verify_mcs_connected_locked() {
    local json="$1" iface="$2" standard ht vht he mcs_get ax_get="" validation_rc
    local -a args

    wifi_fw_mcs_pending "$iface" || return 0
    if wifi_fw_validate_mcs_config "$json" "$iface"; then
        :
    else
        validation_rc=$?
        if [ "$validation_rc" -eq 2 ]; then
            wifi_fw_mcs_clear_pending "$iface" 2>/dev/null || true
            return 0
        fi
        wifi_fw_log local0.err "[$iface] deferred MCS verification skipped: invalid/partial config"
        return 1
    fi

    standard=$(jq -r --arg i "$iface" '.[$i].STANDARD // "" | ascii_downcase' "$json")
    ht=$(jq -r --arg i "$iface" '.[$i].mcs_tier.ht' "$json")
    vht=$(jq -r --arg i "$iface" '.[$i].mcs_tier.vht' "$json")
    he=$(jq -r --arg i "$iface" '.[$i].mcs_tier.he' "$json")
    mcs_get=$("$WIFI_MLANUTL" "$iface" mcstiercfg 2>&1 || true)
    if [ "$standard" = ax ] || [ "$standard" = 6 ]; then
        ax_get=$("$WIFI_MLANUTL" "$iface" 11axcfg 2>&1 || true)
    fi

    if _wifi_fw_verify_mcs_get "$standard" "$ht" "$vht" "$he" "$mcs_get" "$ax_get"; then
        wifi_fw_mcs_clear_pending "$iface" 2>/dev/null || true
        wifi_fw_log local0.info "[$iface] deferred mcstiercfg verified after association (GET-only)"
        return 0
    fi

    # 실제 로드된 SDIO p149.115 실기에서는 첫 association이 pre-association HE tier를 FW 기본값으로
    # 되돌린다. connected SET은 현재 링크를 끊지 않고 다음 association capability를
    # 저장하므로, 한 번 SET 검증 후 wpa_supplicant reassociate를 요청한다. attempt marker는
    # 이벤트 중복/실패 시 재연결 루프를 막고 다음 CONNECTED GET 성공 때 pending과 함께 지운다.
    if wifi_fw_mcs_reassociate_attempted "$iface"; then
        wifi_fw_log local0.err "[$iface] deferred mcstiercfg still mismatched after one-time reassociation; link preserved"
        return 1
    fi

    args=(ht "$ht" vht "$vht")
    if [ "$standard" = ax ] || [ "$standard" = 6 ]; then
        args+=(he both "${he##* }")
    fi
    wifi_fw_log local0.warn "[$iface] association reset MCS tier; staging connected SET for one-time reassociation: ${args[*]}"
    if ! "$WIFI_MLANUTL" "$iface" mcstiercfg "${args[@]}" >/dev/null 2>&1; then
        wifi_fw_log local0.err "[$iface] connected mcstiercfg recovery SET failed; link preserved"
        return 1
    fi

    mcs_get=$("$WIFI_MLANUTL" "$iface" mcstiercfg 2>&1 || true)
    ax_get=""
    if [ "$standard" = ax ] || [ "$standard" = 6 ]; then
        ax_get=$("$WIFI_MLANUTL" "$iface" 11axcfg 2>&1 || true)
    fi
    if ! _wifi_fw_verify_mcs_get "$standard" "$ht" "$vht" "$he" "$mcs_get" "$ax_get"; then
        wifi_fw_log local0.err "[$iface] connected mcstiercfg recovery SET not observable; no reassociation, pending retained"
        return 1
    fi
    if ! wifi_fw_mcs_mark_reassociate "$iface"; then
        wifi_fw_log local0.err "[$iface] cannot persist one-time MCS reassociation marker; link preserved"
        return 1
    fi
    if ! "$WIFI_WPA_CLI" -i "$iface" reassociate >/dev/null 2>&1; then
        wifi_fw_mcs_clear_reassociate "$iface" 2>/dev/null || true
        wifi_fw_log local0.err "[$iface] one-time MCS reassociation request failed; pending retained"
        return 1
    fi
    wifi_fw_log local0.info "[$iface] connected MCS tier staged; one-time reassociation requested, pending GET verification"
    return 0
}

wifi_fw_verify_mcs_connected() {
    local json="$1" iface="$2" lock_path lock_fd rc

    wifi_fw_mcs_pending "$iface" || return 0
    mkdir -p -- "$WIFI_MCS_PENDING_DIR" || return 1
    lock_path="$WIFI_MCS_PENDING_DIR/mcs_verify_${iface}.lock"
    exec {lock_fd}>"$lock_path" || return 1
    if ! flock -n "$lock_fd"; then
        exec {lock_fd}>&-
        return 0
    fi
    _wifi_fw_verify_mcs_connected_locked "$json" "$iface"
    rc=$?
    flock -u "$lock_fd" 2>/dev/null || true
    exec {lock_fd}>&-
    return "$rc"
}

# 냉부팅 첫 FW/module 인스턴스의 mlan0 HE map 적용 실패는 실기에서 1회 module
# lifecycle 재시작 후 회복된다. 첫 실패만 status 75로 구분하고, 같은 boot에서
# 두 번째도 실패하면 일반 실패(1)로 승격해 기존 비상 복구 정책을 다시 적용한다.
wifi_fw_mcs_cold_failure_code() {
    local marker="${1:-/run/wifi/mcs_cold_retry_once}"
    if [ -e "$marker" ]; then
        printf '1\n'
        return 0
    fi
    mkdir -p "$(dirname -- "$marker")" 2>/dev/null || {
        printf '1\n'
        return 0
    }
    : > "${marker}.tmp.$$" 2>/dev/null && mv -f "${marker}.tmp.$$" "$marker" 2>/dev/null || {
        rm -f "${marker}.tmp.$$"
        printf '1\n'
        return 0
    }
    printf '75\n'
}

wifi_fw_mcs_cold_success() {
    local marker="${1:-/run/wifi/mcs_cold_retry_once}"
    rm -f "$marker" "${marker}.tmp.$$"
}
