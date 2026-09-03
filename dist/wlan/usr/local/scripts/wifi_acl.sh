#!/bin/bash
# wifi_acl.sh — 무선측 관리 접점 인바운드 화이트리스트 (#256, 기본 OFF/opt-in)
#
# 위협 모델: 관리 IP를 무선에 노출하는 운용(옵션 X 등)에서, 라우팅 도달 가능한
# 전 구간이 기기의 관리 리스너에 접근 가능해지는 문제. 게이트 대상 포트는
# 실측 인벤토리(2026-09-03, 접근 리뷰 C1) 기반:
#   tcp 22(ssh) / 21(vsftpd — root 명령 채널 ftpcmd) / 80(nginx webui)
#   udp 50607(opcd) / 50000(vhld, shipped-예비) / 161(snmpd) / 162(snmptrapd)
#
# 핵심 안전 설계:
#  - **무선 인터페이스(iifname mlan0/mlan1) 한정** — eth0(유선 1:1, OPC/VHL
#    반송제어 제어평면)은 구조적으로 제외. allowed_hosts를 오구성해도 유선
#    복구 경로가 남는다 (접근 리뷰 V1·V2: weak-host 케이스도 iif 매치가 커버).
#  - **전용 테이블(inet wlan_mgmt_acl)만 소유** — 타 룰 불간섭. 적용은
#    declare+delete+create 를 단일 `nft -f` 트랜잭션으로 원자 수행(M3):
#    반쪽 룰셋(드롭만 남는 자기차단)이 구조적으로 불가능. allowed4 set 은
#    `auto-merge` — 중복/겹침 항목(UI 중복행·host∈CIDR)을 거부 대신 합집합으로
#    병합한다. 없으면 interval set 이 겹침 원소에 트랜잭션 전체를 거부→최초
#    enable 시 보존할 이전 테이블이 없어 fail-open(운영자 오구성 트리거).
#  - **accept-then-drop** (M2): 부정 매치(`!=`)+interval set 조합을 회피.
#  - **IPv6 동반 차단** (C2): 게이트 포트는 v6 무조건 drop — #255(IPv6 비활성)
#    미배포 기기에서 sshd/nginx가 dual-stack이라 v4 화이트리스트만으로는
#    link-local 경유 우회가 가능하기 때문. allowed_hosts는 IPv4 전용.
#  - **관측 가능한 fail-open** (M4): nft 부재/적용 실패 시 패킷 경로는 열어
#    두되(원격 복구 불가 기기 — 벽돌 방지) exit 1 로 유닛을 failed 로 남기고
#    logger 경고를 찍는다. status 는 3-상태(disabled/enforcing/NOT-enforcing).
#
# 한계(문서화된 비목표): UDP(50607 등)는 소스 IP 스푸핑에 방어되지 않음(M1) —
# 본 ACL은 스캐닝·우발 접근 차단이며 인증·기밀성 대체재가 아니다. 직렬 콘솔
# (ttyGS0)·유선 경로는 범위 밖. 바인드 스코핑(SO_BINDTODEVICE)은 인터페이스
# 단위라 "무선이되 이 호스트들만"을 표현하지 못해 상보 관계 — 유선 전용
# 사이트는 opcd device_ip_iface=eth0(기본)로 이미 무선 표면이 없다.
#
# 적용 타이밍: boot(wifi_acl.service oneshot). allowed_hosts 변경 후
# `wifi_acl.sh apply` 재실행 또는 재부팅.
# usage: wifi_acl.sh apply|clear|status|gen   (gen: 룰셋 텍스트 출력 — 테스트용)

set -u

JSON="${WIFI_INIT_CONF_JSON:-/usr/local/etc/wifi_init_conf.json}"
NFT="${WIFI_ACL_NFT:-nft}"
LOGTAG="wifi_acl"

# 무선 인터페이스 한정 — DBDC(mlan1) 재개/인터페이스 추가 시 이 목록 갱신 필요
IFACES='"mlan0", "mlan1"'
TCP_PORTS='22, 21, 80'
UDP_PORTS='50607, 50000, 161, 162'

# 함수 내부의 $LINENO 는 함수 정의줄로 고정되므로, 호출자 라인은 ${BASH_LINENO[0]} 로 남긴다
# (wifi_eth_peer_find.sh:30 관례). [file:line] 은 메시지의 첫 필드여야 한다.
log() { logger -p "local0.$1" -t "$LOGTAG" "[$LOGTAG:${BASH_LINENO[0]}] $2" 2>/dev/null; echo "[$LOGTAG] $2" >&2; }

# jq // 연산자는 false 도 falsey 로 취급하므로 null 검사 방식 사용
# (wifi_apply_enabled.sh get_bool 관례 — jq 는 패키지 하드 의존)
get_enabled() {
    local v
    v=$(jq -r 'if .mgmt_acl.enabled == null then "false" else (.mgmt_acl.enabled|tostring) end' \
        "$JSON" 2>/dev/null)
    [ "$v" = "true" ] && echo true || echo false
}

# allowed_hosts 각 항목 검증: IPv4 또는 IPv4/CIDR 만 허용. 무효 항목이 하나라도
# 있으면 전체 실패(exit 1) — 항목을 조용히 버리면 의도보다 넓게 잠기거나
# 열리는 양방향 사고가 되므로 원자적으로 거부한다.
# allowed_hosts의 JSON 타입 반환("array"/"null"/"string"/"number"/... 또는 파싱 실패 시 빈 문자열)
allowed_hosts_type() {
    jq -r '.mgmt_acl.allowed_hosts | type' "$JSON" 2>/dev/null
}

read_allowed_hosts() {
    # 배열 원소만 방출. 스칼라 등 비배열은 여기서 걸러지지 않으므로(=`.[]` 에러)
    # 호출부(collect_hosts)가 allowed_hosts_type으로 타입을 먼저 검증한다.
    jq -r '.mgmt_acl.allowed_hosts // [] | .[]' "$JSON" 2>/dev/null
}

valid_host() {
    local h="$1" ip="${1%%/*}" pfx=""
    case "$h" in */*) pfx="${h#*/}" ;; esac
    [[ "$ip" =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$ ]] || return 1
    local o
    for o in "${BASH_REMATCH[@]:1}"; do [ "$o" -le 255 ] || return 1; done
    if [ -n "$pfx" ]; then
        [[ "$pfx" =~ ^[0-9]{1,2}$ ]] && [ "$pfx" -ge 0 ] && [ "$pfx" -le 32 ] || return 1
    fi
    return 0
}

# 룰셋 생성 (stdout). declare+delete+create = 원자·멱등 nftables 관용구.
emit_ruleset() {
    local hosts=("$@") elements=""
    if [ "${#hosts[@]}" -gt 0 ]; then
        elements=$(printf '%s, ' "${hosts[@]}"); elements="${elements%, }"
    fi
    cat <<EOF
table inet wlan_mgmt_acl {}
delete table inet wlan_mgmt_acl
table inet wlan_mgmt_acl {
    set allowed4 {
        type ipv4_addr
        flags interval
        auto-merge
EOF
    [ -n "$elements" ] && printf '        elements = { %s }\n' "$elements"
    cat <<EOF
    }
    chain ingress {
        type filter hook input priority filter; policy accept;
        iifname { $IFACES } meta nfproto ipv6 tcp dport { $TCP_PORTS } drop
        iifname { $IFACES } meta nfproto ipv6 udp dport { $UDP_PORTS } drop
        iifname { $IFACES } tcp dport { $TCP_PORTS } ip saddr @allowed4 accept
        iifname { $IFACES } tcp dport { $TCP_PORTS } drop
        iifname { $IFACES } udp dport { $UDP_PORTS } ip saddr @allowed4 accept
        iifname { $IFACES } udp dport { $UDP_PORTS } drop
    }
}
EOF
}

emit_clear() {
    cat <<'EOF'
table inet wlan_mgmt_acl {}
delete table inet wlan_mgmt_acl
EOF
}

table_present() { "$NFT" list table inet wlan_mgmt_acl >/dev/null 2>&1; }

collect_hosts() {
    HOSTS=()
    local h bad=0 t
    # allowed_hosts는 배열이어야 한다. null/부재 → 빈 목록(정상). 스칼라(false/42 등)·객체는
    # 타입 오류로 원자 거부 — 이 검사가 없으면 `.[]`가 에러(2>/dev/null로 삼켜짐)→빈 목록으로
    # 오인되어 "전면 차단"이 조용히 적용된다(Claude 리뷰 RVW-9d514af340ba). 파싱 실패(빈 문자열)도
    # 거부해 무효 conf에 열림/닫힘을 임의로 적용하지 않는다.
    t=$(allowed_hosts_type)
    if [ "$t" != "array" ] && [ "$t" != "null" ]; then
        log err "allowed_hosts 타입 오류(배열이어야 함, 실제='${t:-parse-error}') — 적용 거부(원자 실패)"
        return 1
    fi
    # 원소에 제어문자(C0: NUL/LF/CR/TAB 등, 코드포인트<32)가 들면 jq -r가 여러 줄로 흩거나
    # (LF/CR) bash read가 조용히 버려(NUL) 원소 경계를 잃는다. 예: "192.0.2.5\n0.0.0.0/0"가
    # 전체 허용으로 통과, "0.0.0.0 /0"가 유효 host로 둔갑(Codex P2). 원소 단위로
    # 제어문자 포함을 먼저 걸러 원자 거부한다(개행·CR·NUL 일괄).
    if jq -e '(.mgmt_acl.allowed_hosts // []) | any(.[]?; (type=="string") and (explode | any(. < 32)))' \
        "$JSON" >/dev/null 2>&1; then
        log err "allowed_hosts 원소에 제어문자(개행/CR/NUL 등) 포함 — 적용 거부(원자 실패)"
        return 1
    fi
    while IFS= read -r h; do
        # 빈 원소("")는 UI가 저장한 빈 행 등 — 조용히 건너뛰면 의도보다 넓게 열릴 수 있어
        # 무효 IPv4로 원자 거부한다(Codex). jq -r는 정상 배열에서 빈 줄을 만들지 않으므로
        # 여기 도달하는 빈 h는 실제 "" 원소뿐이다.
        if valid_host "$h"; then HOSTS+=("$h"); else
            log err "allowed_hosts 무효 항목 '$h' — 적용 거부(전체 원자 실패)"; bad=1
        fi
    done < <(read_allowed_hosts)
    return $bad
}

do_apply() {
    # conf 파일이 존재하나 파싱 불가(malformed/손상)면 의도를 알 수 없다. 이때 disabled로
    # 오인해 기존 enforcing 테이블을 삭제하면 마지막으로 적용된 보호가 조용히 사라진다
    # (Codex P1). 룰을 건드리지 않고 실패해 마지막 상태를 보존한다. (파일 부재는 opt-in
    # 기본 off 상태이므로 아래 get_enabled=false 경로로 정상 처리 — 다른 .enabled 토글과 일관)
    if [ -f "$JSON" ] && ! jq -e . "$JSON" >/dev/null 2>&1; then
        log err "$JSON 파싱 불가(malformed) — 룰 미변경, 마지막 상태 보존"
        return 1
    fi
    local enabled; enabled=$(get_enabled)
    if [ "$enabled" != "true" ]; then
        # off: 자기 테이블만 정리(멱등). nft 부재/실패는 off 상태에선 무해.
        if command -v "$NFT" >/dev/null 2>&1; then
            emit_clear | "$NFT" -f - 2>/dev/null || true
        fi
        log info "mgmt_acl disabled — 규칙 미적용(전용 테이블 정리)"
        return 0
    fi
    if ! command -v "$NFT" >/dev/null 2>&1; then
        log err "enabled=true 이나 nft 부재 — NOT enforcing (fail-open)"
        return 1
    fi
    collect_hosts || return 1
    if [ "${#HOSTS[@]}" -eq 0 ]; then
        log warning "enabled=true + allowed_hosts 빈 목록 — 무선측 관리 포트 전면 차단(유선 경로 잔존)"
    fi
    if emit_ruleset "${HOSTS[@]+"${HOSTS[@]}"}" | "$NFT" -f -; then
        log info "mgmt_acl enforcing — allowed=${#HOSTS[@]}건, iif={mlan0,mlan1}, tcp{$TCP_PORTS} udp{$UDP_PORTS}"
        return 0
    fi
    log err "nft 적용 실패 — NOT enforcing (fail-open, 유닛 failed 로 표면화)"
    return 1
}

do_clear() {
    if command -v "$NFT" >/dev/null 2>&1; then
        emit_clear | "$NFT" -f - 2>/dev/null || true
    fi
    log info "mgmt_acl 규칙 제거"
    return 0
}

do_status() {
    local enabled; enabled=$(get_enabled)
    if [ "$enabled" != "true" ]; then
        echo "disabled"
        return 0
    fi
    if table_present; then
        echo "enabled·enforcing"
        "$NFT" list table inet wlan_mgmt_acl 2>/dev/null
        return 0
    fi
    echo "enabled·NOT-enforcing (규칙 부재 — 적용 실패 또는 미적용)"
    return 2
}

case "${1:-}" in
    apply)  do_apply ;;
    clear)  do_clear ;;
    status) do_status ;;
    gen)    collect_hosts || exit 1; emit_ruleset "${HOSTS[@]+"${HOSTS[@]}"}" ;;
    *) echo "usage: $0 apply|clear|status|gen" >&2; exit 2 ;;
esac
