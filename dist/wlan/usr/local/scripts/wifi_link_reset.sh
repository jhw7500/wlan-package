#!/bin/bash
# wifi_link_reset.sh — 공장 초기화용 systemd .link 정리·검증
#
# factory_reset.sh가 패키지 소유 .link 3개(20-mlan0/21-mlan1/22-eth0)를 템플릿(MACAddress
# 없음)으로 덮은 뒤 호출한다. 목표는 하나다 — 초기화 후 어떤 .link도 mlan0/mlan1/eth0에
# MAC을 강제하지 않게 만들고, 그 사실을 검증한다.
#
#   1) 파생 잔재 — *.link.* (백업 .bak/.bak.N/.bak.<임의>, orphan .tmp.*)를 모두 제거한다.
#      공장 초기화에서 이들은 전부 되살릴 이유가 없는 파생 상태다.
#   2) 후조건 검증 — 템플릿 복사(safe_cp)는 실패해도 로그만 남기고 진행한다. 패키지 소유
#      .link에 [Link] MACAddress가 남아 있으면 제거한다.
#   3) 외부 .link — 패키지 소유가 아닌 .link가 mlan0/mlan1/eth0을 지목하며 MACAddress를
#      설정하면 템플릿 복원이 무의미하다. udev는 파일명 사전순으로 처음 매칭된 .link만
#      적용하므로 20-보다 앞선 이름(예: 10-custom.link)은 패키지 파일을 완전히 가린다.
#      해당 파일은 삭제한다. 이름이 `.link`로 끝나므로 1)의 *.link.* 글롭에는 걸리지 않는다.
#   4) 드롭인 — <name>.link.d/*.conf는 <name>.link에 병합되므로 거기 MACAddress가 있으면
#      템플릿 복원과 무관하게 MAC이 강제된다. 디렉터리라 1)의 rm 대상도 아니다. 감지해
#      보고한다.
#
# 삭제 대상은 "우리 인터페이스를 OriginalName으로 명시하고 MACAddress를 설정하는" .link뿐이다.
# MAC을 설정하지 않는 .link(MTU/WoL 등)는 MAC 오염이 아니므로 건드리지 않는다. 대상 판정이
# 불가능한 두 경우 — OriginalName 없이(Path/Driver 매칭) MAC을 설정하는 .link, 그리고 드롭인 —
# 은 삭제하지 않고 crit 로그만 남긴다. 오삭제보다 사람 판단이 낫다.
#
# 백업을 남기지 않는 이유: 공장 초기화는 되돌리는 동작이 아니고, 여기서 회전 백업을 만들면
# 1)에서 지운 파생 잔재를 다시 만드는 셈이 된다.
#
# Usage:
#   wifi_link_reset.sh            정리 수행 (기본)
#   wifi_link_reset.sh --check    변경 없이 검사만 (진단/테스트). 강제 MAC이 남아 있으면 exit 1
set -u

tag=$(basename "$0")
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
NETWORK_DIR="${SYSTEMD_NETWORK_DIR:-/etc/systemd/network}"
MAC_LINK_LIB="${WIFI_MAC_LINK_LIB:-$SCRIPT_DIR/mac_link_lib.sh}"
LOGGER_BIN="${WIFI_LINK_RESET_LOGGER:-logger}"
MANAGED_IFACES="mlan0 mlan1 eth0"

log_msg() {
    local priority="$1" line="$2"
    shift 2
    "$LOGGER_BIN" -p "$priority" "[$tag:$line] $*" || true
}

if [ ! -r "$MAC_LINK_LIB" ]; then
    log_msg local0.emerg "$LINENO" "missing $MAC_LINK_LIB; cannot inspect .link files"
    exit 1
fi
# shellcheck source=./mac_link_lib.sh
. "$MAC_LINK_LIB"

CHECK_ONLY=0
case "${1:-}" in
    "") ;;
    --check) CHECK_ONLY=1 ;;
    *)
        log_msg local0.err "$LINENO" "usage: $tag [--check]"
        exit 64
        ;;
esac

# 이 .link가 관리 대상 인터페이스를 명시적으로 지목하는지. 지목하면 iface 이름을 출력한다.
matched_managed_iface() {
    local link_file="$1" iface
    for iface in $MANAGED_IFACES; do
        if mac_link_matches_iface "$link_file" "$iface"; then
            printf '%s\n' "$iface"
            return 0
        fi
    done
    return 1
}

# *.link.* 파생 잔재(백업 세대·orphan tmp) 일소. 디렉터리(.link.d)는 여기서 걸러지고
# 아래 scan_dropins가 따로 본다 — rm -f는 디렉터리를 지우지 못하고 에러만 낸다.
purge_link_artifacts() {
    local artifact cleaned=0 rc=0
    for artifact in "$NETWORK_DIR"/*.link.*; do
        [ -f "$artifact" ] || continue
        if [ "$CHECK_ONLY" -eq 1 ]; then
            log_msg local0.info "$LINENO" "derived link artifact present: ${artifact##*/}"
            continue
        fi
        if rm -f -- "$artifact"; then
            cleaned=$((cleaned + 1))
        else
            log_msg local0.err "$LINENO" "failed to remove link artifact: ${artifact##*/}"
            rc=1
        fi
    done
    [ "$cleaned" -eq 0 ] \
        || log_msg local0.info "$LINENO" "removed $cleaned derived link artifact(s) (*.link.*)"
    return "$rc"
}

# <name>.link.d/*.conf는 <name>.link에 병합된다. 우리 인터페이스용 부모에 MACAddress를
# 넣는 드롭인이 있으면 템플릿 복원이 무력화되므로 보고한다(삭제는 사람 판단).
scan_dropins() {
    local dropin_dir parent conf mac rc=0
    for dropin_dir in "$NETWORK_DIR"/*.link.d; do
        [ -d "$dropin_dir" ] || continue
        parent="${dropin_dir%.d}"
        # 부모 .link가 없으면 systemd가 드롭인을 적용하지 않는다.
        [ -f "$parent" ] || continue
        # 판정 불가 부모도 포함한다 — 부모 자신이 MAC을 안 쓰면 remove_foreign_link가
        # 조기 반환해 아무도 보고하지 않으므로, 드롭인의 MAC이 조용히 남는다.
        mac_owned_link_iface "$parent" >/dev/null \
            || matched_managed_iface "$parent" >/dev/null \
            || mac_link_match_undecidable "$parent" \
            || continue
        for conf in "$dropin_dir"/*.conf; do
            [ -f "$conf" ] || continue
            mac=$(mac_read_link_address "$conf")
            [ -n "$mac" ] || continue
            log_msg local0.crit "$LINENO" \
                "drop-in forces MAC on ${parent##*/}; left in place for manual review: ${dropin_dir##*/}/${conf##*/}=$mac"
            rc=1
        done
    done
    return "$rc"
}

strip_package_link() {
    local link_file="$1" owner="$2" cur_mac tmp
    cur_mac=$(mac_read_link_address "$link_file")
    [ -n "$cur_mac" ] || return 0

    if [ "$CHECK_ONLY" -eq 1 ]; then
        log_msg local0.err "$LINENO" "[$owner] package link still forces MAC after reset: ${link_file##*/}=$cur_mac"
        return 1
    fi

    # 템플릿 복사가 실패했다는 뜻이므로 err로 남긴다 — 정상 초기화에서는 도달하지 않는다.
    log_msg local0.err "$LINENO" \
        "[$owner] package link still had MACAddress after template restore ($cur_mac); stripping"
    tmp=$(mktemp "${link_file}.tmp.XXXXXX") || return 1
    if ! mac_render_link_without_address "$link_file" > "$tmp"; then
        rm -f -- "$tmp"
        log_msg local0.emerg "$LINENO" "[$owner] failed to render ${link_file##*/} without MACAddress"
        return 1
    fi
    if ! install -m 0644 "$tmp" "$link_file"; then
        rm -f -- "$tmp"
        log_msg local0.emerg "$LINENO" "[$owner] failed to install ${link_file##*/}"
        return 1
    fi
    rm -f -- "$tmp"
}

remove_foreign_link() {
    local link_file="$1" cur_mac owner
    cur_mac=$(mac_read_link_address "$link_file")
    # MAC을 설정하지 않는 외부 .link는 MAC 오염이 아니다 — 운영자 설정으로 두고 넘어간다.
    [ -n "$cur_mac" ] || return 0

    if ! owner=$(matched_managed_iface "$link_file"); then
        # 우리 인터페이스를 지목하지 않거나(무해) 판정이 불가능하다. 후자는 Path/Driver
        # 매칭이나 부정(!) 패턴으로 우리 인터페이스에 걸릴 수 있어 사람이 봐야 한다.
        if mac_link_match_undecidable "$link_file"; then
            log_msg local0.crit "$LINENO" \
                "foreign link sets MAC but its OriginalName cannot decide the target (absent or negated); left in place for manual review: ${link_file##*/}=$cur_mac"
            return 1
        fi
        return 0
    fi

    if [ "$CHECK_ONLY" -eq 1 ]; then
        log_msg local0.err "$LINENO" "[$owner] foreign link still forces MAC: ${link_file##*/}=$cur_mac"
        return 1
    fi

    log_msg local0.crit "$LINENO" \
        "[$owner] removing foreign link that forces MAC $cur_mac: ${link_file##*/}"
    if ! rm -f -- "$link_file"; then
        log_msg local0.emerg "$LINENO" "[$owner] failed to remove ${link_file##*/}"
        return 1
    fi
}

if [ ! -d "$NETWORK_DIR" ]; then
    log_msg local0.err "$LINENO" "missing $NETWORK_DIR; nothing to reset"
    exit 1
fi

if ! mac_acquire_global_lock "$NETWORK_DIR"; then
    log_msg local0.err "$LINENO" "failed to acquire global MAC update lock"
    exit 1
fi

failed=0
purge_link_artifacts || failed=1

for _link in "$NETWORK_DIR"/*.link; do
    [ -f "$_link" ] || continue
    if _owner=$(mac_owned_link_iface "$_link"); then
        strip_package_link "$_link" "$_owner" || failed=1
    else
        remove_foreign_link "$_link" || failed=1
    fi
done

scan_dropins || failed=1

# 최종 후조건: 남아 있는 어떤 .link도 관리 인터페이스에 MAC을 강제하지 않아야 한다.
remaining=""
for _link in "$NETWORK_DIR"/*.link; do
    [ -f "$_link" ] || continue
    _mac=$(mac_read_link_address "$_link")
    [ -n "$_mac" ] || continue
    if mac_owned_link_iface "$_link" >/dev/null || matched_managed_iface "$_link" >/dev/null \
        || mac_link_match_undecidable "$_link"; then
        remaining="$remaining ${_link##*/}=$_mac"
    fi
done

# factory_reset은 곧바로 reboot하므로 전원이 끊겨도 삭제/수정이 유실되지 않게 동기화한다.
if [ "$CHECK_ONLY" -eq 0 ]; then
    sync "$NETWORK_DIR" 2>/dev/null || sync
fi

mac_release_global_lock || log_msg local0.err "$LINENO" "failed to release global MAC update lock"

if [ -n "$remaining" ]; then
    log_msg local0.emerg "$LINENO" "MAC still forced after link reset:$remaining"
    exit 1
fi
if [ "$failed" -ne 0 ]; then
    exit 1
fi
log_msg local0.info "$LINENO" "link reset verified: no .link forces a MAC on [$MANAGED_IFACES]"
