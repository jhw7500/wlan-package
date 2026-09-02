#!/bin/bash
# FW tuning 설정의 association 전 적용과 연결 후 제한 복구·GET 확인 공용 함수.

: "${WIFI_MLANUTL:=mlanutl}"
: "${WIFI_WPA_CLI:=wpa_cli}"
: "${WIFI_COMMAND:=/usr/local/bin/wifi}"
: "${WIFI_FW_LOGGER:=logger}"
: "${WIFI_MCS_VERIFY_ATTEMPTS:=5}"
: "${WIFI_MCS_VERIFY_DELAY_SEC:=1}"
: "${WIFI_MCS_PENDING_DIR:=/run/wifi}"
# antcfg/antcfgnss 의 SET→GET 재시도. mcs_tier 가 쓰던 것과 같은 축이며 기본값도 맞춘다.
: "${WIFI_FW_VERIFY_ATTEMPTS:=5}"
: "${WIFI_FW_VERIFY_DELAY_SEC:=1}"
# 미반영 축을 남기는 곳. 로그는 흘러가지만 "지금 이 보드가 의도한 RF 설정인가" 는
# 상태로 물을 수 있어야 한다. tmpfs 라 부팅마다 초기화된다.
: "${WIFI_FW_UNAPPLIED_DIR:=/run/wifi}"

# 미반영 축 상태. 파일 한 줄 = 한 축, "축=사유" 형식.
# 부팅을 막지 않는 대신 이 파일이 관측 지점이 된다(wifi info / SNMP / fwcfg_watch).
_wifi_fw_unapplied_file() {
    printf '%s/fwcfg_unapplied_%s\n' "$WIFI_FW_UNAPPLIED_DIR" "$1"
}

_wifi_fw_unapplied_mark() {
    local iface="$1" axis="$2" reason="$3" f
    f=$(_wifi_fw_unapplied_file "$iface")
    mkdir -p "$WIFI_FW_UNAPPLIED_DIR" 2>/dev/null || return 0
    # 같은 축의 이전 기록은 덮어쓴다(재시도 결과가 최신이다).
    if [ -f "$f" ]; then
        grep -v "^${axis}=" "$f" > "${f}.tmp" 2>/dev/null || : > "${f}.tmp"
        mv -f "${f}.tmp" "$f" 2>/dev/null || true
    fi
    printf '%s=%s\n' "$axis" "$reason" >> "$f" 2>/dev/null || true
}

_wifi_fw_unapplied_clear() {
    local iface="$1" axis="$2" f
    f=$(_wifi_fw_unapplied_file "$iface")
    [ -f "$f" ] || return 0
    grep -v "^${axis}=" "$f" > "${f}.tmp" 2>/dev/null || : > "${f}.tmp"
    if [ -s "${f}.tmp" ]; then
        mv -f "${f}.tmp" "$f" 2>/dev/null || true
    else
        rm -f "${f}.tmp" "$f" 2>/dev/null || true
    fi
}

# 라이브러리 외부(wifi_init 등)에서 미반영 축을 남길 때 쓰는 공개 래퍼.
wifi_fw_mark_unapplied() {
    _wifi_fw_unapplied_mark "$@"
}

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

# 기존 설치의 active JSON은 postinst deep merge에서 템플릿보다 우선한다. 따라서
# 새 제품 기본만 바꿔서는 과거 false/empty 값이나 p149.115 scan wedge를 재현한 physical
# 1x1 값이 남는다. 알려진 제품 이력과 이미 선택된 새 요청만 승격하고, 그 밖의 명시적
# 운영자 안테나 설정은 보존한다.
wifi_fw_migrate_product_antcfg_json() {
    local json="$1" board_type="${2:-imx93}"
    jq --arg board "$board_type" '
        def one_of($values): . as $v | ($values | index($v)) != null;
        def is_legacy_disabled($a):
            ($a.enabled == false and ($a.tx // "") == "" and ($a.rx // "") == "");
        def is_legacy_physical_1x1($a):
            ($a.enabled == true
             and (($a.tx // "") | one_of(["0x101", "0x0101", "0X101", "0X0101"]))
             and (($a.rx // "") | one_of(["", "0x101", "0x0101", "0X101", "0X0101"])));
        def is_product_request($a):
            ($a.enabled == true
             and (($a.tx // "") | one_of(["0x303", "0x0303", "0X303", "0X0303"]))
             and (($a.rx // "") | one_of(["0x101", "0x0101", "0X101", "0X0101"])));
        def is_product_verify($v):
            (($v | type) == "object"
             and $v.physical_tx == "0x0303"
             and $v.physical_rx == "0x0303"
             and $v.user_htstream == "0x2121");
        def is_empty_verify($v):
            (($v | type) == "object"
             and ($v.physical_tx // "") == ""
             and ($v.physical_rx // "") == ""
             and ($v.user_htstream // "") == "");
        # 제품 계약값. mcs_tier 는 순수 MCS 상한만 담당하고 NSS 제한은 이 니블이
        # 전담한다 — 0x1111 = 양 밴드 Tx1/Rx1. 종전 0x2121(Tx2/Rx1)은 mcs_tier
        # ht 7 과 함께 NSS1 을 만들던 조합이라, ht 를 15 로 올린 지금은 니블만으로
        # NSS1 을 세워야 한다.
        def product_nss:
            {
                enabled: true,
                value: "0x1111",
                verify: { user_htstream: "0x1111" }
            };
        (.mlan0.antcfg // {}) as $a
        | (.mlan0.antcfgnss // {}) as $n
        | if $board == "imx93" then
            # driver#41 이후 제품 계약: antcfg 는 비워서(RF_ANTENNA 미발행) 물리를 FW
            # 기본(2x2)에 두고, 광고 Rx NSS1 intent 는 antcfgnss 로 건다. 종전 제품
            # profile(0x0303/0x0101+verify)과 그 이전 legacy 형태를 모두 새 계약으로
            # 승격한다. 새 계약의 antcfg 는 legacy_disabled 와 같은 모양이므로 이
            # 분기는 재실행에도 같은 결과를 낸다(멱등).
            if is_legacy_disabled($a) or is_legacy_physical_1x1($a) or is_product_request($a)
            then .mlan0.antcfg = (($a + {enabled: false, tx: "", rx: ""}) | del(.verify))
                 | .mlan0.antcfgnss = ($n + product_nss)
            elif is_product_verify($a.verify)
            then .mlan0.antcfg |= del(.verify)
            else .
            end
          else
            # 505.p14/imx8 utility에는 antcfgnss(user_htstream) ABI가 없다.
            # 후보 패키지가 주입한 exact 제품 profile만 안전하게 중화하고, 기존 custom
            # SET(log-only)은 보존한다. deep merge로 verify만 주입된 경우도 제거한다.
            (if is_product_request($a) and is_product_verify($a.verify)
             then .mlan0.antcfg = (($a + {enabled:false, tx:"", rx:""}) | del(.verify))
             elif is_product_verify($a.verify)
             then .mlan0.antcfg |= del(.verify)
             else .
             end)
            # 템플릿 merge 로 들어온 제품 antcfgnss 도 중화한다(적용 시도 자체를 차단).
            | if ($n.enabled == true
                  and (($n.value // "") | one_of(["0x1111", "0x2121"])))
              then .mlan0.antcfgnss |= (. + {enabled: false})
              else .
              end
          end
        # 퇴역 키 정리: fallback_antcfg 는 레거시 antcfg 위임 경로와 함께 제거됐다.
        # 소비 코드가 없어 남아 있어도 무해하지만, 죽은 키를 설정에 남기지 않는다.
        | if (.mlan0.antcfgnss | type) == "object"
          then .mlan0.antcfgnss |= del(.fallback_antcfg) else . end
        | if (.mlan1.antcfgnss | type) == "object"
          then .mlan1.antcfgnss |= del(.fallback_antcfg) else . end
        | (.mlan1.antcfg // {}) as $b
        | if is_empty_verify($b.verify)
          then .mlan1.antcfg |= del(.verify)
          else .
          end
    ' "$json"
}

# p149.115 scan wedge 회피 게이트. imx93/543 계열에서 association 전에 한 번 본다.
#
# 검사 범위는 **wedge 와 인과가 확인된 축으로 한정**한다 — physical 1-path 를 만들 수
# 있는 antcfg 경로다. antcfg 를 비워두면(RF_ANTENNA 미발행) 물리 Tx/Rx 가 FW 기본 2x2 로
# 남아 cofactor 자체가 생기지 않는다. mlan1 의 adapter-level 설정이 뒤에서 덮어쓰는 것도
# 같은 이유로 막는다.
#
# 여기서 검사하지 않는 것과 그 이유:
#   - mlan0.antcfgnss 값: 광고 Rx NSS 1SS 제한 단독으로는 wedge 가 재현되지 않았다
#     (docs/ant_rx_nss_scan_gate_2026-08-25.md:100). 게다가 이 축은 apply 단계에서
#     FW read-back(verify.user_htstream)으로 이미 fail-closed 검증되므로, JSON 값을
#     여기서 재확인하는 것은 중복이다.
#   - mlan0.mcs_tier.*: MCS/NSS 튜닝 값이고 wedge 와 인과가 없다.
#
# wifi_init_conf.json 은 현장에서 조정 가능한 파일이다. 인과 없는 축까지 정확 일치를
# 요구하면 정당한 튜닝이 매 부팅 err 로그를 만들어, 진짜 위반이 그 안에 묻힌다.
# 이 게이트는 "다른 어떤 경로도 검사하지 않는 것" 만 남긴다.
#
# imx8/505 계열은 이 ABI로 qualification되지 않았으므로 적용 대상이 아니다.
wifi_fw_validate_product_scan_profile() {
    local json="$1" board_type="${2:-imx93}"
    case "$board_type" in imx93*) ;; *) return 0 ;; esac
    jq -e '
        (.mlan0.antcfg.enabled != true)
        and (.mlan1.antcfg.enabled != true)
        and (.mlan1.antcfgnss.enabled != true)
    ' "$json" >/dev/null 2>&1
}

# 안테나 경로 비트맵 유효성 — 10진 또는 0x 16진, 1..0xFFFF.
# 0 은 거부한다: 어떤 경로도 선택하지 않는 값이라 RF 가 죽는데 mlanutl 은 성공으로 보고할
# 수 있고, 이 기기는 무선이 유일한 접속 경로다.
_wifi_fw_is_ant_path() {
    local v="$1" n
    case "$v" in
        0x*|0X*)
            case "${v#0[xX]}" in
                ''|*[!0-9a-fA-F]*) return 1 ;;
                # 유효 범위가 1..0xFFFF 라 16진 5자리 이상은 무조건 초과다. bash 산술
                # 오버플로가 음수를 만들어 아래 범위 검사에서 걸리는 우연에 기대지 않고
                # 여기서 명시적으로 거부한다(선행 0 거부와 같은 취지 — 모호한 입력 배제).
                ?????*) return 1 ;;
            esac
            ;;
        # 선행 0 10진수 거부: bash 산술은 "010" 을 8진수 8 로 읽는데 mlanutl 에는 문자열
        # "010" 이 그대로 전달돼, 범위 검증한 값과 실제 적용값이 갈린다.
        ''|0[0-9]*|*[!0-9]*) return 1 ;;
    esac
    n=$((v)) 2>/dev/null || return 1
    [ "$n" -ge 1 ] && [ "$n" -le 65535 ]
}

# antcfg 는 mcs_tier 와 같은 opt-in 이다(기본 false) — 지금까지 적용하지 않던 설정이라
# 기본으로 켜면 출하 기기의 RF 경로가 통째로 바뀐다.
wifi_fw_validate_antcfg_config() {
    local json="$1" iface="$2" enabled tx rx verify_values physical_tx physical_rx user_htstream
    _wifi_fw_has_section "$json" "$iface" antcfg || return 2
    enabled=$(jq -r --arg i "$iface" '.[$i].antcfg.enabled // false' "$json" 2>/dev/null)
    [ "$enabled" = true ] || return 2

    jq -e --arg i "$iface" '
        (.[$i].antcfg | type) == "object"
        and (.[$i].antcfg | has("tx") and has("rx"))
        and ([.[$i].antcfg.tx, .[$i].antcfg.rx] | all(type == "string"))
    ' "$json" >/dev/null 2>&1 || return 1

    tx=$(jq -r --arg i "$iface" '.[$i].antcfg.tx' "$json" 2>/dev/null) || return 1
    rx=$(jq -r --arg i "$iface" '.[$i].antcfg.rx' "$json" 2>/dev/null) || return 1

    _wifi_fw_is_ant_path "$tx" || return 1
    # rx 는 선택 — 비면 인자를 생략해 tx 가 Tx/Rx 양쪽에 적용된다.
    [ -z "$rx" ] || _wifi_fw_is_ant_path "$rx" || return 1

    # verify가 없으면 기존의 SET+관측 로그 동작을 유지한다. 있으면 요청값과 별개인
    # FW physical path 및 host NSS intent의 기대값 세 개를 모두 명시해야 한다.
    if jq -e --arg i "$iface" '.[$i].antcfg | has("verify")' "$json" >/dev/null 2>&1; then
        jq -e --arg i "$iface" '
            .[$i].antcfg.verify as $v
            | ($v | type) == "object"
            and ($v | has("physical_tx") and has("physical_rx") and has("user_htstream"))
            and ([$v.physical_tx, $v.physical_rx, $v.user_htstream] | all(type == "string"))
        ' "$json" >/dev/null 2>&1 || return 1
        verify_values=$(jq -er --arg i "$iface" '
            .[$i].antcfg.verify
            | [.physical_tx,.physical_rx,.user_htstream]
            | @tsv
        ' "$json" 2>/dev/null) || return 1
        IFS=$'\t' read -r physical_tx physical_rx user_htstream <<< "$verify_values"
        _wifi_fw_is_ant_path "$physical_tx" || return 1
        _wifi_fw_is_ant_path "$physical_rx" || return 1
        _wifi_fw_is_ant_path "$user_htstream" || return 1
    fi

    return 0
}

wifi_fw_apply_antcfg() {
    local json="$1" iface="$2" tx rx live rc other differs verify_enabled
    local expected_tx expected_rx expected_user_htstream actual_tx actual_rx actual_user_htstream
    local attempt set_rc=0 fail_reason=""
    if ! _wifi_fw_has_section "$json" "$iface" antcfg; then
        wifi_fw_log local0.info "[$iface] antcfg absent; skip"
        return 0
    fi
    if wifi_fw_validate_antcfg_config "$json" "$iface"; then
        :
    else
        rc=$?
        case "$rc" in
            2) wifi_fw_log local0.info "[$iface] antcfg disabled; skip (FW/board default path)" ;;
            *) wifi_fw_log local0.err "[$iface] invalid antcfg section; skip (FW/board default path)" ;;
        esac
        return 0
    fi

    # antcfg 는 어댑터(라디오) 단위 설정인데 키는 인터페이스별이라 오설정을 부르기 쉽다 —
    # 두 인터페이스에 서로 다른 값을 켜면 나중에 적용된 쪽이 조용히 이긴다. 명시적으로 경고한다.
    # 비교는 jq 한 번으로 끝낸다 — 두 번 호출하면 둘 다 실패했을 때 ""!="" 가 되어 경고가
    # 조용히 누락된다. 판정 불가(빈 결과)도 침묵하지 않고 별도 경고로 남긴다.
    case "$iface" in mlan0) other=mlan1 ;; mlan1) other=mlan0 ;; *) other="" ;; esac
    if [ -n "$other" ] && wifi_fw_validate_antcfg_config "$json" "$other"; then
        differs=$(jq -r --arg i "$iface" --arg o "$other" '
            [.[$i].antcfg.tx, .[$i].antcfg.rx] != [.[$o].antcfg.tx, .[$o].antcfg.rx]
        ' "$json" 2>/dev/null) || differs=""
        case "$differs" in
            true)
                wifi_fw_log local0.warn "[$iface] antcfg differs from $other; adapter-level setting — last applied wins"
                ;;
            false) ;;
            *)
                wifi_fw_log local0.warn "[$iface] antcfg cross-check vs $other failed; cannot confirm agreement"
                ;;
        esac
    fi

    tx=$(jq -r --arg i "$iface" '.[$i].antcfg.tx' "$json" 2>/dev/null) || tx=""
    rx=$(jq -r --arg i "$iface" '.[$i].antcfg.rx' "$json" 2>/dev/null) || rx=""
    verify_enabled=$(jq -r --arg i "$iface" '.[$i].antcfg | has("verify")' "$json" 2>/dev/null) \
        || verify_enabled=false
    if [ "$verify_enabled" = true ]; then
        IFS=$'\t' read -r expected_tx expected_rx expected_user_htstream < <(
            jq -r --arg i "$iface" '
                .[$i].antcfg.verify
                | [.physical_tx,.physical_rx,.user_htstream]
                | @tsv
            ' "$json"
        )
    fi
    # 검증 통과 후라 빈 tx 는 나올 수 없지만, 나온다면 mlanutl 에 빈 인자를 넘기는 대신
    # 사유를 남기고 FW 기본 경로를 유지한다(같은 함수의 다른 jq 호출과 동일 규약).
    if [ -z "$tx" ]; then
        wifi_fw_log local0.err "[$iface] antcfg tx read failed after validation; skip (FW/board default path)"
        return 0
    fi

    # SET → GET → 비교를 재시도한다. 최종 실패해도 부팅은 막지 않는다 — 안테나 설정
    # 미반영은 링크를 세울 수 없는 조건이 아니고, 재부팅으로 나아지지도 않는다.
    # 미반영 사실은 err 로그와 마커 파일로 남긴다.
    for ((attempt = 1; attempt <= WIFI_FW_VERIFY_ATTEMPTS; attempt++)); do
        if [ -n "$rx" ]; then
            wifi_fw_log local0.info "[$iface] antcfg configured (attempt $attempt/$WIFI_FW_VERIFY_ATTEMPTS): tx=$tx rx=$rx"
            "$WIFI_MLANUTL" "$iface" antcfg "$tx" "$rx" >/dev/null 2>&1 || set_rc=1
        else
            wifi_fw_log local0.info "[$iface] antcfg configured (attempt $attempt/$WIFI_FW_VERIFY_ATTEMPTS): tx=$tx (rx 생략 — tx가 Tx/Rx 공통)"
            "$WIFI_MLANUTL" "$iface" antcfg "$tx" >/dev/null 2>&1 || set_rc=1
        fi
        if [ "${set_rc:-0}" = 1 ]; then
            set_rc=0
            fail_reason="SET failed"
            wifi_fw_log local0.warn "[$iface] antcfg SET failed on attempt $attempt/$WIFI_FW_VERIFY_ATTEMPTS"
            [ "$attempt" -lt "$WIFI_FW_VERIFY_ATTEMPTS" ] && sleep "$WIFI_FW_VERIFY_DELAY_SEC"
            continue
        fi

        live=$("$WIFI_MLANUTL" "$iface" antcfg 2>&1) || live=""
        if [ -z "$live" ]; then
            fail_reason="GET failed"
            wifi_fw_log local0.warn "[$iface] antcfg GET failed after SET (attempt $attempt/$WIFI_FW_VERIFY_ATTEMPTS)"
            [ "$attempt" -lt "$WIFI_FW_VERIFY_ATTEMPTS" ] && sleep "$WIFI_FW_VERIFY_DELAY_SEC"
            continue
        fi
        wifi_fw_log local0.info "[$iface] antcfg live after pre-association SET: $(printf '%s' "$live" | tr '\n' ' ')"

        if [ "$verify_enabled" != true ]; then
            _wifi_fw_unapplied_clear "$iface" antcfg
            return 0
        fi

        # 9098 비대칭 NSS 설정은 요청 Rx mask를 physical GET에 그대로 돌려주지 않는다.
        # physical path와 host intent를 각각 파싱해 명시된 계약과 수치로 비교한다.
        actual_tx=$(printf '%s\n' "$live" \
            | sed -n 's/^Mode of Tx path is[[:space:]:]*\([^[:space:]]*\).*$/\1/p' | tail -1)
        actual_rx=$(printf '%s\n' "$live" \
            | sed -n 's/^Mode of Rx path is[[:space:]:]*\([^[:space:]]*\).*$/\1/p' | tail -1)
        actual_user_htstream=$(printf '%s\n' "$live" \
            | sed -n 's/.*\[user_htstream=\(0[xX][0-9A-Fa-f][0-9A-Fa-f]*\)\].*/\1/p' | tail -1)

        fail_reason=""
        if ! _wifi_fw_is_ant_path "$actual_tx" \
           || [ "$((actual_tx))" -ne "$((expected_tx))" ]; then
            fail_reason="physical_tx expected=$expected_tx actual=${actual_tx:-<missing>}"
        elif ! _wifi_fw_is_ant_path "$actual_rx" \
           || [ "$((actual_rx))" -ne "$((expected_rx))" ]; then
            fail_reason="physical_rx expected=$expected_rx actual=${actual_rx:-<missing>}"
        elif ! _wifi_fw_is_user_htstream_value "$actual_user_htstream" \
           || [ "$((actual_user_htstream))" -ne "$((expected_user_htstream))" ]; then
            fail_reason="user_htstream expected=$expected_user_htstream actual=${actual_user_htstream:-<missing>}"
        fi

        if [ -z "$fail_reason" ]; then
            wifi_fw_log local0.info "[$iface] antcfg verified on attempt $attempt: physical_tx=$actual_tx physical_rx=$actual_rx user_htstream=$actual_user_htstream"
            _wifi_fw_unapplied_clear "$iface" antcfg
            return 0
        fi
        wifi_fw_log local0.warn "[$iface] antcfg not yet applied (attempt $attempt/$WIFI_FW_VERIFY_ATTEMPTS): $fail_reason"
        [ "$attempt" -lt "$WIFI_FW_VERIFY_ATTEMPTS" ] && sleep "$WIFI_FW_VERIFY_DELAY_SEC"
    done

    wifi_fw_log local0.err "[$iface] antcfg NOT applied after $WIFI_FW_VERIFY_ATTEMPTS attempts: ${fail_reason:-unknown} (boot continues; antenna paths may stay at FW default)"
    _wifi_fw_unapplied_mark "$iface" antcfg "${fail_reason:-unknown}"
    return 0
}

# antcfgnss SET 값 유효성 — 드라이버가 0x 접두를 요구한다(진수 혼동·파서 fail-open 차단,
# driver#41). 1..0xFFFF 의 0x 16진만 허용하고 0 은 거부한다. 니블 단위 의미 검증(지원
# 밴드 니블 1..hw 상한)은 드라이버가 수행하므로 여기서는 형식만 본다.
_wifi_fw_is_user_htstream_value() {
    local v="$1"
    case "$v" in
        0x*|0X*)
            case "${v#0[xX]}" in
                ''|*[!0-9a-fA-F]*) return 1 ;;
                ?????*) return 1 ;;
            esac
            ;;
        *) return 1 ;;
    esac
    [ "$((v))" -ge 1 ] && [ "$((v))" -le 65535 ]
}

# antcfgnss 는 광고 NSS intent(user_htstream) 전용 host 경로다(driver#41) — RF_ANTENNA
# HostCmd 를 발행하지 않아 물리 안테나를 건드리지 않는다. antcfg 와 같은 opt-in(기본 false).
wifi_fw_validate_antcfgnss_config() {
    local json="$1" iface="$2" enabled value expected
    _wifi_fw_has_section "$json" "$iface" antcfgnss || return 2
    enabled=$(jq -r --arg i "$iface" '.[$i].antcfgnss.enabled // false' "$json" 2>/dev/null)
    [ "$enabled" = true ] || return 2

    jq -e --arg i "$iface" '
        (.[$i].antcfgnss | type) == "object"
        and (.[$i].antcfgnss | has("value"))
        and (.[$i].antcfgnss.value | type == "string")
    ' "$json" >/dev/null 2>&1 || return 1

    value=$(jq -r --arg i "$iface" '.[$i].antcfgnss.value' "$json" 2>/dev/null) || return 1
    _wifi_fw_is_user_htstream_value "$value" || return 1

    if jq -e --arg i "$iface" '.[$i].antcfgnss | has("verify")' "$json" >/dev/null 2>&1; then
        expected=$(jq -er --arg i "$iface" '
            .[$i].antcfgnss.verify
            | select(type == "object")
            | .user_htstream
            | select(type == "string")
        ' "$json" 2>/dev/null) || return 1
        _wifi_fw_is_user_htstream_value "$expected" || return 1
    fi

    return 0
}

wifi_fw_apply_antcfgnss() {
    local json="$1" iface="$2" value live other differs verify_enabled expected actual attempt
    if ! _wifi_fw_has_section "$json" "$iface" antcfgnss; then
        wifi_fw_log local0.info "[$iface] antcfgnss absent; skip"
        return 0
    fi
    if wifi_fw_validate_antcfgnss_config "$json" "$iface"; then
        :
    else
        rc=$?
        case "$rc" in
            2) wifi_fw_log local0.info "[$iface] antcfgnss disabled; skip (host NSS intent 기본값 유지)" ;;
            *) wifi_fw_log local0.err "[$iface] invalid antcfgnss section; skip (host NSS intent 기본값 유지)" ;;
        esac
        return 0
    fi

    # user_htstream 은 어댑터(라디오) 단위 상태라 antcfg 와 같은 last-wins 함정이 있다.
    case "$iface" in mlan0) other=mlan1 ;; mlan1) other=mlan0 ;; *) other="" ;; esac
    if [ -n "$other" ] && wifi_fw_validate_antcfgnss_config "$json" "$other"; then
        differs=$(jq -r --arg i "$iface" --arg o "$other" '
            .[$i].antcfgnss.value != .[$o].antcfgnss.value
        ' "$json" 2>/dev/null) || differs=""
        case "$differs" in
            true)
                wifi_fw_log local0.warn "[$iface] antcfgnss differs from $other; adapter-level setting — last applied wins"
                ;;
            false) ;;
            *)
                wifi_fw_log local0.warn "[$iface] antcfgnss cross-check vs $other failed; cannot confirm agreement"
                ;;
        esac
    fi

    value=$(jq -r --arg i "$iface" '.[$i].antcfgnss.value' "$json" 2>/dev/null) || value=""
    verify_enabled=$(jq -r --arg i "$iface" '.[$i].antcfgnss | has("verify")' "$json" 2>/dev/null) \
        || verify_enabled=false
    if [ "$verify_enabled" = true ]; then
        expected=$(jq -r --arg i "$iface" '.[$i].antcfgnss.verify.user_htstream' "$json" 2>/dev/null) \
            || expected=""
    fi
    if [ -z "$value" ]; then
        wifi_fw_log local0.err "[$iface] antcfgnss value read failed after validation; skip"
        return 0
    fi

    wifi_fw_log local0.info "[$iface] antcfgnss configured: value=$value"

    # SET → read-back → 비교를 재시도한다. 콜드부팅 직후 FW 가 SET 을 rc=0 으로 받고도
    # 실제 반영이 한 박자 늦는 경우가 있어(mcs_tier 에서 관측된 것과 같은 축) 1-shot 은
    # 취약하다. read-back 은 antcfg 가 제공한다 — mlanutl 출력이
    # "NSS limit (antcfg): ...  [user_htstream=0x....]" 이고, antcfgnss 는 SET 전용이라
    # 인자 없이 부르면 값이 아닌 응답만 돌아온다(실측: "...response received: !!").
    # 인자 없는 antcfg 는 읽기 전용이라 RF_ANTENNA HostCmd 를 발행하지 않는다.
    for ((attempt = 1; attempt <= WIFI_FW_VERIFY_ATTEMPTS; attempt++)); do
        if ! "$WIFI_MLANUTL" "$iface" antcfgnss "$value" >/dev/null 2>&1; then
            wifi_fw_log local0.warn "[$iface] antcfgnss SET failed on attempt $attempt/$WIFI_FW_VERIFY_ATTEMPTS"
            [ "$attempt" -lt "$WIFI_FW_VERIFY_ATTEMPTS" ] && sleep "$WIFI_FW_VERIFY_DELAY_SEC"
            continue
        fi
        if [ "$verify_enabled" != true ]; then
            live=$("$WIFI_MLANUTL" "$iface" antcfg 2>&1) || live=""
            wifi_fw_log local0.info "[$iface] antcfgnss SET ok (verify 미설정; read-back: $(printf '%s' "$live" | tr '\n' ' '))"
            _wifi_fw_unapplied_clear "$iface" antcfgnss
            return 0
        fi
        live=$("$WIFI_MLANUTL" "$iface" antcfg 2>&1) || live=""
        actual=$(printf '%s\n' "$live" \
            | sed -n 's/.*user_htstream=\(0[xX][0-9A-Fa-f][0-9A-Fa-f]*\).*/\1/p' | tail -1)
        if _wifi_fw_is_user_htstream_value "$actual" \
           && [ "$((actual))" -eq "$((expected))" ]; then
            wifi_fw_log local0.info "[$iface] antcfgnss verified on attempt $attempt: user_htstream=$actual"
            _wifi_fw_unapplied_clear "$iface" antcfgnss
            return 0
        fi
        wifi_fw_log local0.warn "[$iface] antcfgnss not yet applied (attempt $attempt/$WIFI_FW_VERIFY_ATTEMPTS): expected=$expected actual=${actual:-<missing>}"
        [ "$attempt" -lt "$WIFI_FW_VERIFY_ATTEMPTS" ] && sleep "$WIFI_FW_VERIFY_DELAY_SEC"
    done

    # 재시도를 소진해도 반영되지 않았다. 부팅을 막지 않는다 — 광고 NSS 미반영은
    # 링크를 세울 수 없는 조건이 아니고, 재부팅으로 나아지지도 않는다(실기에서
    # 부팅 루프만 만들었다). 대신 err 로 남기고 미반영 상태를 파일로 노출한다.
    wifi_fw_log local0.err "[$iface] antcfgnss NOT applied after $WIFI_FW_VERIFY_ATTEMPTS attempts: expected=$expected actual=${actual:-<missing>} (boot continues; advertised NSS may stay at FW default)"
    _wifi_fw_unapplied_mark "$iface" antcfgnss "expected=$expected actual=${actual:-<missing>}"
    return 0
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
    local json="$1" iface="$2" values mode low high interval_ms interval live enabled
    if ! _wifi_fw_has_section "$json" "$iface" rate_adapt; then
        wifi_fw_log local0.info "[$iface] rate_adapt absent; skip"
        return 0
    fi
    # enabled 기본 true — 섹션이 있으면 적용하던 종전 동작을 그대로 둔다(mcs_tier 는
    # opt-in 이라 기본 false, 이쪽은 이미 켜져 있던 기능이라 기본 true).
    # jq 의 // 는 false 를 falsey 로 취급해 기본값으로 덮으므로 null 만 기본값으로 본다.
    # 판정은 mcs_tier 와 같은 strict 비교 — 스키마상 boolean 이며 같은 파일의 형제 키와
    # 해석이 갈리면 안 된다.
    enabled=$(jq -r --arg i "$iface" '
        if (.[$i].rate_adapt.enabled == null) then "true"
        else (.[$i].rate_adapt.enabled | tostring) end
    ' "$json" 2>/dev/null) || enabled=""
    # 읽기 실패(빈 결과)를 disabled 와 같은 info 로 묻으면, 설정이 켜져 있는데 적용되지
    # 않은 상태가 조용히 지나간다 — 사유를 구분해 warn 으로 남긴다.
    if [ -z "$enabled" ]; then
        wifi_fw_log local0.warn "[$iface] rate_adapt.enabled read failed; skip (마지막 SET값 유지; 콜드부팅 후에만 FW 기본값)"
        return 0
    fi
    if [ "$enabled" != true ]; then
        wifi_fw_log local0.info "[$iface] rate_adapt disabled; skip (마지막 SET값 유지; 콜드부팅 후에만 FW 기본값)"
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
                _wifi_fw_unapplied_clear "$iface" mcs_tier
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
        wifi_fw_log local0.err "[$iface] cannot persist deferred MCS verification marker; falling through to unapplied"
    fi

    wifi_fw_log local0.err "[$iface] mcstiercfg NOT applied after $WIFI_MCS_VERIFY_ATTEMPTS attempts (boot continues; MCS tier may stay at FW default)"
    _wifi_fw_unapplied_mark "$iface" mcs_tier "not verified after $WIFI_MCS_VERIFY_ATTEMPTS attempts"
    return 0
}

# Deferred marker가 있을 때만 연결 후 확인한다. GET이 맞으면 종료하고, 첫 association이
# FW 기본값으로 되돌린 경우 connected SET으로 다음 association 값을 저장한다. 실제
# association 변경은 MCS lock을 푼 outer 함수가 transition lock+fresh proof를 소유하는
# `wifi <iface> connect`로 한 번만 수행한다. 실패는 wifi_init/reboot로 전파하지 않고
# 링크와 pending을 보존한다.
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
    # 저장하므로, 한 번 SET 검증 후 outer 함수에 serialized reconnect를 요청한다. attempt marker는
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
    wifi_fw_log local0.info "[$iface] connected MCS tier staged; serialized reconnect required, pending GET verification"
    return 10
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
    if _wifi_fw_verify_mcs_connected_locked "$json" "$iface"; then
        rc=0
    else
        rc=$?
    fi
    flock -u "$lock_fd" 2>/dev/null || true
    exec {lock_fd}>&-
    if [ "$rc" -eq 10 ]; then
        # wifi connect 내부가 conf(FD9) -> transition(FD7), ABORT_SCAN quiesce,
        # fresh CONNECTED+status proof를 일괄 소유한다. MCS lock을 먼저 해제했으므로
        # CONNECTED event의 후속 GET verifier와 교착하지 않는다.
        if "$WIFI_COMMAND" "$iface" connect >/dev/null 2>&1; then
            wifi_fw_log local0.info "[$iface] one-time MCS reconnect completed through serialized wifi command"
            return 0
        fi
        wifi_fw_mcs_clear_reassociate "$iface" 2>/dev/null || true
        wifi_fw_log local0.err "[$iface] one-time serialized MCS reconnect failed; pending retained"
        return 1
    fi
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
