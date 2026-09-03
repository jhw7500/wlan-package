#!/bin/bash
# wifi_event.sh - iw event(netlink) 기반 AP 연결 변화 감지 + on_connect 커맨드 실행
# Usage: wifi_event.sh [interface]

IFACE="${1:-mlan0}"
tag=$(basename "$0")
WIFI_INIT_CONF_JSON="${WIFI_INIT_CONF_JSON:-/usr/local/etc/wifi_init_conf.json}"
ON_CONNECT_CMDS=""
# 링크 상태 조회 헬퍼(iw link 미사용 — 파일 상단 주석 참조). /bin/sh 에서도 동작한다.
# 이 스크립트는 set -e 가 없어 로드 실패가 조용히 넘어가고, 그러면 wlan_bssid 미정의로
# initial_bssid 가 비어 **catch-up 이 말없이 누락**된다. 실패를 반드시 남긴다.
# shellcheck source=./wlan_link_lib.sh
if ! . /usr/local/scripts/wlan_link_lib.sh 2>/dev/null; then
    logger -p local0.err "[$tag:$LINENO] [$IFACE] wlan_link_lib.sh load failed — catch-up/BSSID lookup unavailable"
    # 로드 실패 후에도 아래에서 wlan_bssid 를 부르므로, stub 이 없으면 매 호출이
    # "command not found" 를 stderr 로 뱉어 서비스 로그를 오염시킨다. 빈 값으로 수렴시켜
    # catch-up 만 조용히 건너뛰게 한다(실패 사실은 위 logger 로 이미 남겼다).
    wlan_bssid() { :; }
fi

# association 전에는 88W9098 HE map이 0x0000으로 보일 수 있다. wifi_init이 남긴
# per-iface pending이 있을 때만 CONNECTED/ROAMED에서 검증하고, 첫 association이 FW
# 기본값으로 되돌리면 connected SET + 1회 reassociate로 다음 association을 확정한다.
# shellcheck source=./wifi_fw_config_lib.sh
if ! . /usr/local/scripts/wifi_fw_config_lib.sh 2>/dev/null; then
    logger -p local0.err "[$tag:$LINENO] [$IFACE] wifi_fw_config_lib.sh load failed — deferred MCS verification unavailable"
    wifi_fw_verify_mcs_connected() { return 0; }
fi

run_mcs_post_connect_verify() {
    # mlanutl 검증/제한 복구가 iw event 소비를 막지 않도록 별도 자식에서 수행한다.
    # 실패는 library가 기록하며 링크/service lifecycle에는 전파하지 않는다.
    ( wifi_fw_verify_mcs_connected "$WIFI_INIT_CONF_JSON" "$IFACE" || true ) &
}
cleanup() {
    logger -p local0.info "[$tag:$LINENO] [$IFACE] stopped"
    exit 0
}

trap cleanup INT TERM

# radio.bw(20/40/80) HE 클램프는 OMI 경로를 폐기함(2026-06-15 실기: NXP FW가
# OMI로 STA 동작 BW를 안 바꿈). BW는 htcapinfo/vhtcfg cap에서 파생되며, cap은
# 호스트 usr_* 필드라 roam/reconnect 시 새 assoc IE에 자동 반영될 것으로 기대
# (실기 확인 항목). cap이 roam 후 풀리는 게 확인되면 여기서 cap 재적용 훅 추가.

# on_connect 설정은 시작 시 한 번만 읽어 연결 이벤트마다 JSON/jq를 다시 열지 않는다.
# 런타임 설정 변경은 wifi_event@<iface> 재시작 후 반영된다.
if [ -f "$WIFI_INIT_CONF_JSON" ] && command -v jq >/dev/null 2>&1; then
    # group/world-writable이면 임의 명령 삽입 위험
    _json_perm=$(stat -c '%a' "$WIFI_INIT_CONF_JSON" 2>/dev/null || echo "000")
    if [ $(( 0${_json_perm} & 022 )) -ne 0 ]; then
        logger -p local0.crit "[$tag:$LINENO] [$IFACE] CRITICAL: $WIFI_INIT_CONF_JSON is group/world-writable (perm=${_json_perm}). Disabling on_connect to prevent command injection."
    else
        ON_CONNECT_CMDS=$(jq -r --arg iface "$IFACE" '
            if ((.[$iface].on_connect.enabled // false) | tostring) == "true" then
                (.[$iface].on_connect.commands[]? // empty)
            else
                empty
            end
        ' "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    fi
    unset _json_perm
fi

run_on_connect() {
    local cmd

    [ -n "$ON_CONNECT_CMDS" ] || return 0

    printf '%s\n' "$ON_CONNECT_CMDS" | while IFS= read -r cmd; do
        [ -z "$cmd" ] && continue
        logger -p local0.info "[$tag:$LINENO] [$IFACE] on_connect: $cmd"
        if bash -c "$cmd" > /dev/null 2>&1; then
            logger -p local0.info "[$tag:$LINENO] [$IFACE] on_connect: OK"
        else
            logger -p local0.err "[$tag:$LINENO] [$IFACE] on_connect: FAILED ($?)"
        fi
    done
}

#logger -p local0.info "[$tag:$LINENO] [$IFACE] started"

# 초기 상태 점검: wifi_event 시작 전에 이미 연결된 경우(첫 부팅 race) 1회 처리.
# iw event는 구독 이후의 이벤트만 전달하므로, 이미 CONNECTED 상태이면 run_on_connect가 실행되지 않음.
initial_bssid=$(wlan_bssid "$IFACE")
if [ -n "$initial_bssid" ]; then
    logger -p local0.info "[$tag:$LINENO] [$IFACE] INITIAL CONNECTED bssid=$initial_bssid (catch-up)"
    run_mcs_post_connect_verify
    run_on_connect
    # catch-up 에서는 LinkUp 트랩을 송신하지 않는다 — 이는 '상태 변화'가 아니라 기존 연결의
    # 복구이고, 데몬 재시작마다 중복 up 트랩을 유발한다. NMS 는 SNMP 폴링(IfLinkStatus
    # .3.2.1.7.2)으로 현재 링크 상태를 조회할 수 있다(트랩은 변화 통지 전용).
fi

# 로밍 직후 스위치 FDB 재학습 강제 — 무선 iface IP로 gratuitous ARP(-U) 즉시 발사(#234).
# 배경: 로밍 후 유선망이 STA 새 위치를 재학습하기까지 ~2-5s 상향 갭이 관측됐고(무선 핸드오프·
# 하향은 정상), 현재 STA/AP 어느 쪽도 로밍 시 재공지를 하지 않는다(tshark 재검증, 2026-09-01).
# 스코프 = 무선전용/관리-IP: iface에 IPv4가 있을 때만 발사한다. 순수 MAC클론 투명 브리지는
# mlan0에 L3 IP가 없어 자동 no-op이며, 그 최종 제품의 근본책은 드라이버측 클론MAC L2 announce
# (wlan-driver-v2#47, 머지됨)다. 비블로킹 백그라운드(&) — iw event 루프를 막지 않는다(트랩 패턴 동일).
# 한계: 잔여 갭이 AP측 상향 포워딩 지연이면 이 GARP도 같은 갭에 걸릴 수 있어 갭 소멸을 보장하진
# 않는다(듀얼AP A/B 판별은 #235). 다만 "아무도 재공지 안 함"은 확정이라 이 발사 자체는 유효.
# 게이트: `.<iface>.roam_garp.enabled`(bool, 기본 false — opt-in). 효과가 #235에서 확증되기
# 전까지 default off로 두고, 확증 후 릴리스에서 default 승격을 재검토한다. null/부재/파싱실패는
# 모두 off로 처리(안전측 — get_bool/wifi_acl get_enabled 관례와 동일).
roam_gratuitous_arp() {
    local en ip
    en=$(jq -r --arg i "$IFACE" 'if (.[$i].roam_garp.enabled) == true then "true" else "false" end' \
        "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    [ "$en" = "true" ] || return 0
    ip=$(ip -4 -o addr show "$IFACE" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1)
    [ -n "$ip" ] && arping -U -c 3 -I "$IFACE" "$ip" >/dev/null 2>&1 &
}

iw event -t 2>&1 | while IFS= read -r line; do
    case "$line" in
        *"$IFACE"*"connected to"*)
            bssid=$(echo "$line" | grep -oE '([0-9a-f]{2}:){5}[0-9a-f]{2}' | head -1)
            logger -p local0.info "[$tag:$LINENO] [$IFACE] CONNECTED bssid=$bssid"
            roam_gratuitous_arp
            run_mcs_post_connect_verify
            run_on_connect
            # 트랩은 &(백그라운드) — dest 가 hostname 이면 DNS 지연이 iw event 루프를 블로킹해
            # 후속 이벤트를 누락시킬 수 있어 분리(Gemini/Claude 리뷰 합의). UDP fire-forget 라
            # 송신 자체는 즉시, 이벤트 빈도가 낮아 순서 역전 영향 미미.
            /usr/local/scripts/wifi_snmp_trap.sh link up &
            ;;
        *"$IFACE"*"roamed to"*)
            # FW 주도 로밍 전용 케이스 — cfg80211_roamed 경로는 "roamed to"로
            # 표면화된다. 주력인 수동 로밍(wpa_cli roam — wifi_roam.py:1242)은
            # nl80211 connect 경로(cfg80211_connect_result)라 "connected to"로
            # 표면화되어 위 케이스가 커버한다.
            bssid=$(echo "$line" | grep -oE '([0-9a-f]{2}:){5}[0-9a-f]{2}' | head -1)
            logger -p local0.info "[$tag:$LINENO] [$IFACE] ROAMED bssid=$bssid"
            roam_gratuitous_arp
            run_mcs_post_connect_verify
            ;;
        *"$IFACE"*"channel switch started"*)
            # STARTED 는 전환 개시 알림 — iw dev info 가 아직 구채널을 반환하므로 트랩 생략.
            # NOTIFY("channel switch", started 없음)에서만 신채널 트랩(stale·이중 방지, 리뷰 합의).
            ;;
        *"$IFACE"*"channel switch"*)
            ch=$(iw dev "$IFACE" info 2>/dev/null | sed -n 's/.*channel \([0-9]*\).*/\1/p' | head -1)
            logger -p local0.info "[$tag:$LINENO] [$IFACE] CH_SWITCH channel=$ch"
            [ -n "$ch" ] && /usr/local/scripts/wifi_snmp_trap.sh channel "$ch" &
            ;;
        *"$IFACE"*"disconnected"*)
            reason=$(echo "$line" | sed -n 's/.*reason: \([0-9]*\).*/\1/p')
            logger -p local0.info "[$tag:$LINENO] [$IFACE] DISCONNECTED reason=$reason"
            /usr/local/scripts/wifi_snmp_trap.sh link down &
            ;;
        *"$IFACE"*"deauth"*)
            # deauth 는 직후 disconnected 이벤트를 동반하므로 LinkDown 트랩은 disconnected
            # case 에서만 송신(이중 트랩 방지 — Gemini/Claude 리뷰 합의).
            bssid=$(echo "$line" | grep -oE '([0-9a-f]{2}:){5}[0-9a-f]{2}' | head -1)
            logger -p local0.info "[$tag:$LINENO] [$IFACE] DEAUTH bssid=$bssid"
            ;;
    esac
done
