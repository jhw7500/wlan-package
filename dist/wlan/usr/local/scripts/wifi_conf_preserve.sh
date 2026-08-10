#!/bin/bash
# wifi_conf_preserve.sh — 공장 초기화가 지워선 안 되는 하드웨어/생산 설정 보존
#
# factory_reset.sh는 wifi_init_conf.json을 템플릿으로 통째로 덮어써 사용자 런타임 설정을 지운다.
# 그러나 아래 키는 사용자 취향이 아니라 유닛마다 생산 단계에서 정해지는 하드웨어·규제 값이라,
# 템플릿으로 되돌리면 그 유닛의 캘리브레이션과 TX 파워 리밋이 다른 값으로 바뀐다.
#
#   .global.MOD_PARA         모듈 파라미터 conf (/lib/firmware/ 기준 상대경로)
#   .global.CAL_DATA_CFG     캘리브레이션 파일 fallback
#   .global.TXPWRLIMIT_PATH  TX 파워 리밋 파일 fallback
#   .mlanN.CAL_DATA_CFG      인터페이스별 캘리브레이션 (global보다 우선)
#   .mlanN.TXPWRLIMIT_PATH   인터페이스별 TX 파워 리밋 (global보다 우선)
#   .mac.<iface>.base        인터페이스 기준 MAC (write_mac.sh/eth_mac_get.sh가 기록)
#
# .mac은 base만 보존한다. base는 그 유닛의 기준 MAC이지만 target은 `wifi mac <iface> target`로
# 정하는 런타임 설정이라 초기화 대상이다 (wifi_init.sh resolve_mac: dynamic → target → base).
#
# Usage:
#   wifi_conf_preserve.sh save <snapshot>    템플릿 덮어쓰기 전, active에서 보존 대상만 추출
#   wifi_conf_preserve.sh apply <snapshot>   덮어쓰기 후, active에 되쓰기
#   wifi_conf_preserve.sh keys               보존 대상 키 목록 출력 (문서/테스트용)
#
# apply는 반드시 wifi_board_config.sh 뒤에 호출해야 한다. 보드 감지 헬퍼가 .global.MOD_PARA를
# 상수로 다시 쓰기 때문에(v0.3.0 wifi_mod_para_.conf 통합 마이그레이션 잔재) 그 전에 되쓰면
# 곧바로 덮인다.
#
# 되살려도 안전한 값만 되쓴다 — 파일 경로는 그 파일이 실제로 있을 때, MAC은 할당 가능한
# unicast일 때. 빈 문자열과 "none"은 "사용 안 함"이라는 유효한 설정이라 그대로 보존한다.
# 못 쓰는 값까지 되살리면 공장 초기화가 복구 수단이 아니라 고장을 이월하는 동작이 된다 —
# 특히 MOD_PARA가 없는 파일을 가리키면 moal insmod가 실패해 무선이 아예 안 올라온다.
set -u

tag=$(basename "$0")
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
MAC_LINK_LIB="${WIFI_MAC_LINK_LIB:-$SCRIPT_DIR/mac_link_lib.sh}"
ACTIVE="${WIFI_INIT_CONF_JSON:-/usr/local/etc/wifi_init_conf.json}"
FIRMWARE_ROOT="${WIFI_FIRMWARE_ROOT:-/lib/firmware}"
LOGGER_BIN="${WIFI_PRESERVE_LOGGER:-logger}"

# 보존 대상 키의 단일 출처. save/apply/keys가 모두 이 목록만 본다.
# .mac 하위는 2단계 경로라 섹션 이름을 "mac.<iface>"로 잡는다 (jq getpath/setpath는
# 중간 객체를 알아서 만들고 찾는다).
PRESERVE_PATHS='[
    ["global","MOD_PARA"],
    ["global","CAL_DATA_CFG"],
    ["global","TXPWRLIMIT_PATH"],
    ["mlan0","CAL_DATA_CFG"],
    ["mlan0","TXPWRLIMIT_PATH"],
    ["mlan1","CAL_DATA_CFG"],
    ["mlan1","TXPWRLIMIT_PATH"],
    ["mac","mlan0","base"],
    ["mac","mlan1","base"],
    ["mac","eth0","base"]
]'

log_msg() {
    local priority="$1" line="$2"
    shift 2
    "$LOGGER_BIN" -p "$priority" "[$tag:$line] $*" || true
}

is_valid_json() {
    local path="$1"
    [ -s "$path" ] || return 1
    jq -e 'type == "object"' "$path" >/dev/null 2>&1
}

# 값이 가리키는 파일이 실제로 있는지. MOD_PARA와 CAL_DATA_CFG는 /lib/firmware 기준
# 상대경로, TXPWRLIMIT_PATH는 절대경로가 관례지만 셋 다 양쪽을 허용하므로 앞글자로 판별한다.
path_value_usable() {
    local value="$1" candidate
    case "$value" in
        /*) candidate="$value" ;;
        *)  candidate="${FIRMWARE_ROOT}/${value}" ;;
    esac
    [ -f "$candidate" ]
}

# 인터페이스에 실제로 할당 가능한 MAC인지. 판정 규칙은 write_mac.sh/update_mac.sh와 같은
# mac_link_lib.sh를 그대로 쓴다 — 규칙을 복제하면 드리프트만 늘어난다.
mac_value_usable() {
    if ! command -v mac_is_assignable >/dev/null 2>&1; then
        log_msg local0.err "$LINENO" "missing $MAC_LINK_LIB; cannot validate preserved MAC"
        return 1
    fi
    mac_is_assignable "$1"
}

# 되살려도 안전한 값인지. 못 쓰는 값이면 사유를 출력하고 1을 반환한다.
# 빈값/none은 "사용 안 함"이라는 유효한 설정이라 검사 없이 보존한다.
value_reject_reason() {
    local section="$1" value="$2"
    case "$value" in
        ""|none|None) return 1 ;;
    esac
    if [ "$section" = "mac" ]; then
        mac_value_usable "$value" && return 1
        printf 'not an assignable unicast MAC'
    else
        path_value_usable "$value" && return 1
        printf 'path missing on disk'
    fi
}

preserve_keys() {
    printf '%s' "$PRESERVE_PATHS" | jq -r '.[] | join(".")'
}

save_snapshot() {
    local snapshot="$1" count

    if [ -z "$snapshot" ]; then
        log_msg local0.err "$LINENO" "usage: $tag save <snapshot>"
        return 64
    fi
    if ! is_valid_json "$ACTIVE"; then
        log_msg local0.err "$LINENO" "cannot snapshot preserved keys from invalid JSON: $ACTIVE"
        return 1
    fi

    if ! jq --argjson paths "$PRESERVE_PATHS" '
            . as $src
            | reduce ($paths[]) as $p ({};
                ($src | getpath($p)) as $v
                | if $v == null then . else setpath($p; $v) end)
        ' "$ACTIVE" > "$snapshot"; then
        rm -f -- "$snapshot"
        log_msg local0.err "$LINENO" "failed to extract preserved keys from $ACTIVE"
        return 1
    fi

    count=$(jq -r '[paths(scalars)] | length' "$snapshot" 2>/dev/null || echo 0)
    log_msg local0.info "$LINENO" "preserved keys captured ($count): $(snapshot_pairs "$snapshot" | tr '\n' ' ')"
}

# 스냅샷에 실제로 담긴 보존 대상을 "<점표기 키>\t<jq 경로 JSON>\t<값>"으로 펼친다.
# 순회 기준은 스냅샷 구조가 아니라 PRESERVE_PATHS다 — 목록 밖의 키가 스냅샷에 섞여 있어도
# 아예 읽지 않으므로 되쓰기 대상이 되지 않는다.
snapshot_entries() {
    jq -r --argjson paths "$PRESERVE_PATHS" '
        . as $snap
        | $paths[]
        | . as $p
        | ($snap | getpath($p)) as $v
        | select($v != null)
        | [($p | join(".")), ($p | tojson), ($v | tostring)] | @tsv
    ' "$1" 2>/dev/null
}

# 로그용 "global.MOD_PARA=cts/wifi_mod_para.conf ..." 한 줄.
snapshot_pairs() {
    snapshot_entries "$1" | while IFS=$'\t' read -r dotted _ value; do
        printf '%s=%s\n' "$dotted" "$value"
    done
}

apply_snapshot() {
    local snapshot="$1" keep='{}' applied="" skipped="" dotted path value reason tmp

    if [ -z "$snapshot" ]; then
        log_msg local0.err "$LINENO" "usage: $tag apply <snapshot>"
        return 64
    fi
    if ! is_valid_json "$snapshot"; then
        log_msg local0.err "$LINENO" "invalid preserve snapshot; keep template values: $snapshot"
        return 1
    fi
    if ! is_valid_json "$ACTIVE"; then
        log_msg local0.err "$LINENO" "invalid target JSON; cannot restore preserved keys: $ACTIVE"
        return 1
    fi

    while IFS=$'\t' read -r dotted path value; do
        [ -n "$dotted" ] || continue
        if reason=$(value_reject_reason "${dotted%%.*}" "$value"); then
            skipped="$skipped $dotted=$value"
            log_msg local0.warn "$LINENO" "preserved value unusable ($reason); fall back to template: $dotted=$value"
            continue
        fi
        keep=$(printf '%s' "$keep" | jq -c --argjson p "$path" --arg v "$value" \
                   'setpath($p; $v)') || {
            log_msg local0.err "$LINENO" "failed to stage preserved key: $dotted"
            return 1
        }
        applied="$applied $dotted=$value"
    done < <(snapshot_entries "$snapshot")

    if [ "$keep" = '{}' ]; then
        log_msg local0.warn "$LINENO" "no preserved key restored;${skipped:- nothing to restore}"
        return 0
    fi

    # wifi_board_config.sh와 같은 방식: 같은 디렉터리에 쓰고 rename. jq의 `*`는 객체를 재귀
    # 병합하므로 $keep에 담긴 잎 노드만 덮어쓰고 나머지 템플릿 값은 그대로 둔다.
    tmp="${ACTIVE}.preserve.tmp"
    if jq --argjson keep "$keep" '. * $keep' "$ACTIVE" > "$tmp" && [ -s "$tmp" ]; then
        mv -f -- "$tmp" "$ACTIVE"
        # factory_reset은 곧바로 reboot하므로 전원이 끊겨도 유실되지 않게 동기화한다.
        sync "$ACTIVE" 2>/dev/null || sync
        log_msg local0.info "$LINENO" "preserved keys restored:${applied}${skipped:+ (skipped:$skipped)}"
    else
        rm -f -- "$tmp"
        log_msg local0.err "$LINENO" "failed to write preserved keys; keep template values: $ACTIVE"
        return 1
    fi
}

if ! command -v jq >/dev/null 2>&1; then
    log_msg local0.err "$LINENO" "jq not available; cannot preserve hardware keys"
    exit 1
fi

# MAC 판정 규칙 공용 라이브러리. 없으면 MAC 키만 보존 대상에서 빠지고(경로 키는 그대로)
# mac_value_usable가 사유를 남긴다 — 같은 .deb로 배포되므로 정상 설치에선 발생하지 않는다.
if [ -r "$MAC_LINK_LIB" ]; then
    # shellcheck source=./mac_link_lib.sh
    . "$MAC_LINK_LIB"
fi

case "${1:-}" in
    save)
        save_snapshot "${2:-}"
        ;;
    apply)
        apply_snapshot "${2:-}"
        ;;
    keys)
        preserve_keys
        ;;
    *)
        log_msg local0.err "$LINENO" "usage: $tag <save <snapshot>|apply <snapshot>|keys>"
        exit 64
        ;;
esac
