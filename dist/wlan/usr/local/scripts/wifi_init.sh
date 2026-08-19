#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
# shellcheck source=./wifi_init_config_lib.sh
. "$SCRIPT_DIR/wifi_init_config_lib.sh"
# shellcheck source=./wifi_fw_config_lib.sh
. "$SCRIPT_DIR/wifi_fw_config_lib.sh"
# shellcheck source=./wlan_link_lib.sh
. "$SCRIPT_DIR/wlan_link_lib.sh"
# shellcheck source=./mac_link_lib.sh
. "$SCRIPT_DIR/mac_link_lib.sh"

tag=$(basename "$0")
MOD_PARA="cts/wifi_mod_para.conf"
TXPWRLIMIT_PATH="/lib/firmware/cts/txpwrlimit_cfg_9098.conf"
# thermal_mgmt FW hostcmd 정의 파일 (per-interface enable/disable_thermal_mgmt 블록, SUBID 0x113)
THERMAL_DEBUG_CONF="/lib/firmware/cts/config/debug.conf"
ANT_TYPE=""
WBRIDGE_ENGINE="pcap"
# moal 엔진 bridge 파라미터 (전역, engine=moal일 때 moal insmod args에 추가)
bridge_debug=0
bridge_wlan_idx=0
bridge_keepalive_ms=1
bridge_keepalive_idle_ms=20  # idle cutoff(ms). 드라이버가 해당 param을 선언한 경우에만 insmod 인자로 전달(아래 moal 블록)
bridge_peer=""               # 빈값=드라이버 기본(eth0). JSON 명시 시에만 insmod 인자 추가
bridge_consume_link_local="" # 빈값=드라이버 기본(0). JSON 명시 시에만 insmod 인자 추가
# local_hairpin: 로컬발 TX(dst==클론 MAC) 유선 divert + ARP tee/inject — BD↔유선peer 통신을
# peer IP 인지(peer_route/ip_discovery) 없이 성립시킴 (AP intra-BSS 무반사 환경 대응).
# 빈값=미전달(드라이버 기본 0=off). 신규 param이므로 parmtype 게이트 후에만 insmod 인자 추가.
bridge_local_hairpin=""
# deliver_rt_prio: RX deliver leg RT 우선순위 (Direction B — threaded NAPI + FIFO fix).
# 0=off(deliver가 CFS→moal 다운스트림 RX jitter), 1-99=FIFO prio. 드라이버가 wq_sched_policy
# param 선언 시에만 전달(아래 moal 블록). 실측: RTT 82ms→9.3ms.
bridge_deliver_rt_prio=45
# tx_work: moal 데이터 TX 제출 방식 module_param (bridge 무관·드라이버 전역, engine과 무관하게 전달).
# 빈값=미전달(드라이버 기본, iMX는 1). 0|1만 수용. 아래 moal insmod 블록에서 param 선언 확인 후 추가.
tx_work=""

WIFI_INIT_CONF_JSON="${WIFI_INIT_CONF_JSON:-/usr/local/etc/wifi_init_conf.json}"

#logger -p local0.info "[$tag:$LINENO] wifi initializing"

if [ -f "$WIFI_INIT_CONF_JSON" ] && command -v jq >/dev/null 2>&1; then
    BOARD_TYPE=$(jq -r '.global.BOARD_TYPE // "imx8mm"' "$WIFI_INIT_CONF_JSON")
    MOD_PARA=$(jq -r '.global.MOD_PARA // "cts/wifi_mod_para.conf"' "$WIFI_INIT_CONF_JSON")
    TXPWRLIMIT_PATH=$(jq -r '.global.TXPWRLIMIT_PATH // "/lib/firmware/cts/txpwrlimit_cfg_9098.conf"' "$WIFI_INIT_CONF_JSON")
    ANT_TYPE=$(jq -r '.global.ANT_TYPE // ""' "$WIFI_INIT_CONF_JSON")
    BRIDGE_IFACE=$(jq -r '.wbridge.bridge_iface // .global.BRIDGE_IFACE // "mlan0"' "$WIFI_INIT_CONF_JSON")
    WBRIDGE_ENABLED=$(jq -r 'if .wbridge.enabled then "true" else "false" end' "$WIFI_INIT_CONF_JSON" 2>/dev/null || echo "true")
    MAC_MODE=$(jq -r '.wbridge.mac_mode // .global.MAC_MODE // "default"' "$WIFI_INIT_CONF_JSON")
    # mac_clone_require_peer: dynamic 클론 MAC을 유선 peer가 실제로 있을 때만 유지한다.
    # true면 peer를 못 찾은 부팅에서 이전 클론이 .link에 남지 않는다(base → 없으면 MAC 미지정).
    MAC_CLONE_REQUIRE_PEER=$(jq -r 'if (.wbridge.mac_clone_require_peer // true) then "true" else "false" end' \
        "$WIFI_INIT_CONF_JSON" 2>/dev/null || echo "true")
    WBRIDGE_ENGINE=$(jq -r '.wbridge.engine // "pcap"' "$WIFI_INIT_CONF_JSON")
    # moal keepalive: 발열↔레이턴시 노브 (engine=moal 전용 insmod 인자).
    # 음이 아닌 정수만 수용 — 그 외(키 없음/잘못된 값)는 스크립트 기본값(1) 유지.
    _ka=$(jq -r '.wbridge.moal.keepalive_ms // empty' "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    case "$_ka" in
        ''|*[!0-9]*) ;;
        *) bridge_keepalive_ms=$_ka ;;
    esac
    unset _ka
    # moal keepalive_idle: idle 구간 keepalive 주기 (engine=moal 전용 insmod 인자).
    # 음이 아닌 정수만 수용 — 그 외(키 없음/잘못된 값)는 스크립트 기본값(20) 유지.
    _kai=$(jq -r '.wbridge.moal.keepalive_idle_ms // empty' "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    case "$_kai" in
        ''|*[!0-9]*) ;;
        *) bridge_keepalive_idle_ms=$_kai ;;
    esac
    unset _kai
    # moal.deliver_rt_prio: RX deliver leg RT 우선순위 (Direction B — threaded NAPI FIFO).
    # 0=off(deliver CFS=jitter), 1-99=FIFO prio. 그 외(키 없음/비정수/>99)=기본(45) 유지.
    _drp=$(jq -r '.wbridge.moal.deliver_rt_prio // empty' "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    case "$_drp" in
        ''|*[!0-9]*) ;;
        *) [ "$_drp" -le 99 ] && bridge_deliver_rt_prio=$_drp ;;
    esac
    unset _drp
    # moal.debug: BR_DBG/[DBG-RXDROP] 진단 로그 (0|1만 수용)
    _bd=$(jq -r '.wbridge.moal.debug // empty' "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    case "$_bd" in
        0|1) bridge_debug=$_bd ;;
    esac
    unset _bd
    # moal.peer: 유선 peer 인터페이스명 (IFNAMSIZ 15자 이내, 인터페이스명 문자만).
    # 유효하게 명시된 경우에만 insmod 인자로 전달 — 빈값/형식위반은 드라이버 기본(eth0).
    _bp=$(jq -r '.wbridge.moal.peer // empty' "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    if [ -n "$_bp" ] && [ "${#_bp}" -le 15 ]; then
        case "$_bp" in
            *[!a-zA-Z0-9._-]*) ;;
            *) bridge_peer=$_bp ;;
        esac
    fi
    unset _bp
    # moal.consume_link_local: [DBG-RXDROP] A/B 진단 토글 (0|1만 수용)
    _cll=$(jq -r '.wbridge.moal.consume_link_local // empty' "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    case "$_cll" in
        0|1) bridge_consume_link_local=$_cll ;;
    esac
    unset _cll
    # moal.local_hairpin: 로컬 hairpin — BD↔유선peer 통신을 peer IP 인지 없이 성립 (0|1만 수용).
    _lhp=$(jq -r '.wbridge.moal.local_hairpin // empty' "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    case "$_lhp" in
        0|1) bridge_local_hairpin=$_lhp ;;
    esac
    unset _lhp
    # global.tx_work: moal 데이터 TX 제출 방식 (0|1만 수용). 빈값/형식위반은 미전달(드라이버 기본).
    _tw=$(jq -r '.global.tx_work // empty' "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    case "$_tw" in
        0|1) tx_work=$_tw ;;
    esac
    unset _tw
fi

# 커널 모듈 (보드별 드라이버 선택)
case "${BOARD_TYPE:-imx8mm}" in
    imx93*)
        MLAN_KO="mlan_imx93.ko"; MOAL_KO="moal_imx93.ko"
        ;;
    *)
        MLAN_KO="mlan_imx8.ko"; MOAL_KO="moal_imx8.ko"
        ;;
esac
MLAN_MOD="mlan"; MOAL_MOD="moal"


if [ "${TXPWRLIMIT_PATH:-}" = "none" ]; then
    TXPWRLIMIT_PATH=""
fi

# moal 파라미터 구성 (dev_cap_mask·cal_data_cfg는 인터페이스별로 wifi_mod_para.conf 블록에 주입됨)
moal_args="mod_para=$MOD_PARA"

# tx_work: moal 데이터 TX 제출 방식 module_param (bridge 무관·드라이버 전역 → engine과 무관하게 전달).
# 미선언 .ko에 전달하면 insmod가 unknown-parameter로 실패(부팅 붕괴)하므로 선언된 경우에만 추가한다.
# 게이트는 .modinfo 의 param 전용 토큰(parmtype=tx_work:)으로 정탐한다 — tx_work 어근 함수 심볼
# (woal_tx_work_handler/woal_pcie_delayed_tx_work)·로그 포맷(tx_work=0x%x)은 param 미선언 .ko 에도
# 존재해 단순 토큰 검색을 위험측으로 오탐시키므로, param 등록 시에만 나타나는 토큰으로 검사한다.
# 추출에 `tr` 를 쓰는 이유: busybox grep -a 는 NUL 이 많은 .ko 바이너리에서 NUL 이후 문자열을
# 놓쳐(BusyBox 1.30.1 실측: 실제 .ko 에 grep -a NOMATCH) 게이트가 항상 실패하는 함정이 있다.
# NUL 을 개행으로 바꿔(tr '\000' '\n') 일반 grep 으로 검사하면 busybox/GNU 가 일치(실측 검증).
# strings 가 아니라 tr 를 쓰는 건 portability — strings 는 binutils/optional applet 이라 minimal
# production fs 에 없을 수 있으나 tr 는 busybox 핵심 applet 이라 항상 존재. tr 부재 시엔
# NOMATCH→skip(안전측: 드라이버 기본값 사용).
if [ -n "$tx_work" ]; then
    # 주의: `tr | grep -q` + `set -o pipefail` 조합은 grep 의 조기 종료가 tr 에 SIGPIPE 를 보내
    # 파이프라인 exit 를 비0(실패)으로 만들어 오판한다(실제 param 있는 .ko 도 skip). grep 이
    # 입력을 끝까지 읽도록 -q 대신 출력만 버려(>/dev/null) pipefail 오판을 피한다.
    if tr '\000' '\n' < "/opt/wlan/driver/$MOAL_KO" 2>/dev/null | grep -F 'parmtype=tx_work:' >/dev/null 2>&1; then
        moal_args="$moal_args tx_work=$tx_work"
        logger -p local0.info "[$tag:$LINENO] moal: tx_work=$tx_work added to insmod args"
    else
        logger -p local0.warn "[$tag:$LINENO] moal: $MOAL_KO lacks tx_work param; skip (driver default)"
    fi
fi

# mfg_mode 판정 (SoT: mod_para.conf의 mfg_mode=). 스크립트 전체에서 단 1회만 판독 —
# fw_name 선택과 MFG 프로파일 분기(bridge 인자/mod_para 운영키/MAC/unload 멱등 가드/
# mfg_loaded flag)가 모두 이 한 값에서 파생되어야 flag("현재 드라이버가 mfg로 로드됨")와
# 실제 로드된 FW가 어긋나는 TOCTOU가 구조적으로 불가능해진다.
MFG_MODE=$(grep -m1 '^[[:space:]]*mfg_mode=' "/lib/firmware/$MOD_PARA" 2>/dev/null | sed 's/.*mfg_mode=//' | tr -d ' ' || echo "0")
MFG_LOADED_FLAG="/run/wifi/mfg_loaded"

# BUS_TYPE/BLUETOOTH 기반 fw_name 자동 갱신
if [ -f "$WIFI_INIT_CONF_JSON" ] && command -v jq >/dev/null 2>&1; then
    _BUS=$(jq -r '.global.BUS_TYPE // "pcie"' "$WIFI_INIT_CONF_JSON")
    _BT=$(jq -r '.global.BLUETOOTH.enable // false' "$WIFI_INIT_CONF_JSON")
    _MOD_FILE="/lib/firmware/$(jq -r '.global.MOD_PARA // "cts/wifi_mod_para.conf"' "$WIFI_INIT_CONF_JSON")"
    _MFG="$MFG_MODE"

    if [ "$_BUS" == "sdio" ]; then
        if [ "$_BT" == "true" ]; then
            [ "${_MFG:-0}" == "1" ] && _FW="cts/sduart9098_combo.bin" || _FW="cts/sduart9098_combo_v1.bin"
        else
            [ "${_MFG:-0}" == "1" ] && _FW="cts/sd9098_wlan.bin" || _FW="cts/sd9098_wlan_v1.bin"
        fi
    else
        if [ "$_BT" == "true" ]; then
            [ "${_MFG:-0}" == "1" ] && _FW="cts/pcieuart9098_combo.bin" || _FW="cts/pcieuart9098_combo_v1.bin"
        else
            [ "${_MFG:-0}" == "1" ] && _FW="cts/pcie9098_wlan.bin" || _FW="cts/pcie9098_wlan_v1.bin"
        fi
    fi

    # 현재 fw_name과 다르면 갱신
    if [ "$_BUS" == "sdio" ]; then _BLK="SD9098"; else _BLK="PCIE9098"; fi
    _CUR_FW=$(grep -A20 "^${_BLK}_0 " "$_MOD_FILE" 2>/dev/null | grep -m1 'fw_name=' | sed 's/.*fw_name=//' | tr -d ' ')
    if [ "${_CUR_FW:-}" != "$_FW" ]; then
        python3 /usr/local/logger/wifi_config.py 2 fw_name "$_FW"
        logger -p local0.info "[$tag:$LINENO] fw_name auto-update: $_CUR_FW -> $_FW (BUS=$_BUS, BT=$_BT, MFG=${_MFG:-0})"
    fi
fi

# 인터페이스 활성 폴백 기본값 — wifi_apply_enabled.sh 와 같은 규약으로 맞춘다.
# 두 스크립트가 다르면 한쪽은 유닛을 disable 하고 다른 쪽은 radio setup / supplicant
# start 를 진행하는 모순 상태가 된다(wifi_apply_enabled.sh 는 `.mlanN.enabled` 부재를
# "false" 로 본다).
#   - 설정을 읽을 수 있는데 키가 없거나 해석 불가  → false
#   - 설정을 못 읽음(파일/jq 부재, 파싱 실패)       → true
# 후자를 false 로 떨어뜨리면 안 된다: 그건 "인터페이스를 껐다"가 아니라 "설정을 못 읽는다"
# 이고, 여기서 양쪽 인터페이스를 죽이면 config 가 깨진 기기가 무선까지 잃어 원격 복구가
# 끊긴다. wifi_apply_enabled.sh 도 이 경우 apply 자체를 건너뛰거나 중단해 종전 enable
# 상태를 그대로 둔다 — 기동 유지가 양쪽 공통 동작이며, 판정은 wifi_init_conf_status 하나를
# 공유한다(각자 구현하면 파싱 실패 같은 사유가 한쪽에만 반영된다).
IFACE_ENABLED_DEFAULT=false
if ! wifi_init_conf_status "$WIFI_INIT_CONF_JSON"; then
    IFACE_ENABLED_DEFAULT=true
fi

MLAN0_ENABLED=$(wifi_init_get_iface_enabled "mlan0" "$IFACE_ENABLED_DEFAULT")
MLAN1_ENABLED=$(wifi_init_get_iface_enabled "mlan1" "$IFACE_ENABLED_DEFAULT")
MLAN0_FREQ=$(wifi_init_get_iface_frequency "mlan0" "auto")
MLAN1_FREQ=$(wifi_init_get_iface_frequency "mlan1" "auto")

BRIDGE_IFACE="${BRIDGE_IFACE:-mlan0}"
WBRIDGE_ENABLED="${WBRIDGE_ENABLED:-true}"
BRIDGE_NONE=false
if [ "$WBRIDGE_ENABLED" = "false" ]; then
    BRIDGE_NONE=true
    logger -p local0.info "[$tag:$LINENO] wbridge.enabled=false → bridge disabled"
elif [ "$BRIDGE_IFACE" = "none" ]; then
    BRIDGE_NONE=true
    BRIDGE_IFACE="mlan0"
elif [ "$BRIDGE_IFACE" != "mlan0" ] && [ "$BRIDGE_IFACE" != "mlan1" ]; then
    logger -p local0.err "[$tag:$LINENO] BRIDGE_IFACE invalid: $BRIDGE_IFACE, fallback to mlan0"
    BRIDGE_IFACE="mlan0"
fi

MAC_MODE="${MAC_MODE:-default}"
if [ "$MAC_MODE" != "default" ] && [ "$MAC_MODE" != "dynamic" ] && [ "$MAC_MODE" != "static" ]; then
    logger -p local0.err "[$tag:$LINENO] MAC_MODE invalid: $MAC_MODE, fallback to default"
    MAC_MODE="default"
fi
# jq/JSON을 못 읽은 경우에도 템플릿 기본값과 같은 true로 맞춘다.
MAC_CLONE_REQUIRE_PEER="${MAC_CLONE_REQUIRE_PEER:-true}"
logger -p local0.info "[$tag:$LINENO] BOARD_TYPE=$BOARD_TYPE, modules=$MLAN_KO/$MOAL_KO BRIDGE_IFACE=$BRIDGE_IFACE BRIDGE_NONE=$BRIDGE_NONE MAC_MODE=$MAC_MODE mac_clone_require_peer=$MAC_CLONE_REQUIRE_PEER"
logger -p local0.info "[$tag:$LINENO] mlan0: enabled=$MLAN0_ENABLED freq=$MLAN0_FREQ, mlan1: enabled=$MLAN1_ENABLED freq=$MLAN1_FREQ"

# moal 엔진일 때만 bridge 파라미터를 moal insmod args에 추가.
# bridge_iface가 mlan1이면 bridge_wlan_idx=1, 나머지는 기본값 유지.
[ "$BRIDGE_IFACE" = "mlan1" ] && bridge_wlan_idx=1
# MFG 프로파일: moal bridge 인자(bridge_mode=1 등) 미전달 — MFG FW에서 브릿지는
# 무의미하고, moal bridge가 eth0에 attach(promisc)해 labtool 이더넷 링크를 건드릴 수 있다.
if [ "${MFG_MODE:-0}" == "1" ] && [ "$WBRIDGE_ENGINE" = "moal" ]; then
    logger -p local0.info "[$tag:$LINENO] MFG profile: skip moal bridge params (moal_args: $moal_args)"
elif [ "$WBRIDGE_ENGINE" = "moal" ]; then
    moal_args="$moal_args bridge_mode=1 bridge_debug=$bridge_debug bridge_wlan_idx=$bridge_wlan_idx bridge_keepalive_ms=$bridge_keepalive_ms"
    # bridge_keepalive_idle_ms: 신규 param — 미선언 드라이버(예: 현재 moal_imx8.ko)에 전달하면
    # insmod가 unknown-parameter로 실패하므로, 로드 대상 .ko가 선언한 경우에만 전달한다.
    # 게이트는 .modinfo 의 param 전용 토큰(parmtype=...:)으로 정탐한다 — busybox grep -a 는 NUL 이 많은
    # .ko 바이너리에서 NUL 이후 문자열을 놓쳐(BusyBox 1.30.1 실측: 실제 .ko 에 NOMATCH) 게이트가 항상
    # 실패하는 함정이 있어, tr '\000' '\n' 로 NUL 을 개행으로 바꿔 grep 한다(tx_work 게이트와 동일,
    # tr 는 busybox 핵심 applet 이라 strings 보다 portable). -q 대신 출력만 버려(>/dev/null) pipefail
    # 에서 grep 조기종료 SIGPIPE 로 인한 오판을 막는다. 미지원 시 드라이버 기본(0).
    if tr '\000' '\n' < "/opt/wlan/driver/$MOAL_KO" 2>/dev/null | grep -F 'parmtype=bridge_keepalive_idle_ms:' >/dev/null 2>&1; then
        moal_args="$moal_args bridge_keepalive_idle_ms=$bridge_keepalive_idle_ms"
    else
        logger -p local0.warn "[$tag:$LINENO] moal: $MOAL_KO lacks bridge_keepalive_idle_ms param; skip (driver default)"
    fi
    # 선택 파라미터: JSON에 유효하게 명시된 경우에만 추가
    # (해당 param이 없는 구버전 드라이버는 insmod가 실패하므로 기본 미전달)
    [ -n "$bridge_peer" ] && moal_args="$moal_args bridge_peer=$bridge_peer"
    [ -n "$bridge_consume_link_local" ] && moal_args="$moal_args bridge_consume_link_local=$bridge_consume_link_local"
    # bridge_local_hairpin: 신규 param — 미선언 .ko 에 전달하면 insmod 실패(부팅 붕괴)하므로
    # parmtype 게이트(keepalive_idle_ms와 동일 방식) 통과 시에만 추가. runtime 변경도 가능:
    # /sys/module/moal/parameters/bridge_local_hairpin
    if [ -n "$bridge_local_hairpin" ]; then
        if tr '\000' '\n' < "/opt/wlan/driver/$MOAL_KO" 2>/dev/null | grep -F 'parmtype=bridge_local_hairpin:' >/dev/null 2>&1; then
            moal_args="$moal_args bridge_local_hairpin=$bridge_local_hairpin"
        else
            logger -p local0.warn "[$tag:$LINENO] moal: $MOAL_KO lacks bridge_local_hairpin param; skip (driver default)"
        fi
    fi
    # deliver_rt_prio>0: Direction B — RX deliver leg를 RT화(threaded NAPI FIFO:prio)해 moal
    # 다운스트림 RX jitter 해결(실측 RTT 82ms→9.3ms). wq_sched_policy=1(FIFO)+wq_sched_prio 전달;
    # main/tx/bridge kthread도 동일 FIFO:prio로 올라감(pull IRQ FIFO:50 아래 권장). 게이트는
    # bridge_keepalive_idle_ms와 동일 방식(parmtype 토큰) — 미선언 .ko면 skip(insmod 실패 방지).
    if [ "${bridge_deliver_rt_prio:-0}" -gt 0 ] 2>/dev/null && \
       tr '\000' '\n' < "/opt/wlan/driver/$MOAL_KO" 2>/dev/null | grep -F 'parmtype=wq_sched_policy:' >/dev/null 2>&1; then
        moal_args="$moal_args wq_sched_policy=1 wq_sched_prio=$bridge_deliver_rt_prio"
        logger -p local0.info "[$tag:$LINENO] moal: deliver_rt_prio=$bridge_deliver_rt_prio → wq_sched_policy=1 wq_sched_prio=$bridge_deliver_rt_prio added"
    fi
    logger -p local0.info "[$tag:$LINENO] moal engine: bridge params added → $moal_args"
else
    logger -p local0.info "[$tag:$LINENO] moal_args: $moal_args"
fi

# MFG 프로파일 멱등 가드: mfg 모드로 이미 로드된 드라이버는 건드리지 않고 성공 종료.
# 재실행마다 아래 rmmod/insmod + wpa kill/fuser kill이 수행되면 mlan을 점유한
# mfgbridge 제조 테스트가 끊긴다. flag는 mfg 모드 insmod 성공 시에만 생성되므로,
# mfg_mode=1이어도 flag가 없으면(일반 FW로 로드된 상태) 재로드 경로로 MFG FW 전환된다.
if [ "${MFG_MODE:-0}" == "1" ] && [ -f "$MFG_LOADED_FLAG" ] && lsmod | grep "^${MOAL_MOD}\b" >/dev/null 2>&1; then
    logger -p local0.info "[$tag:$LINENO] mfg_mode=1, driver already loaded in mfg mode → no-op (MFG profile)"
    exit 0
fi

# 이미 로드된 모듈이 있으면 사용 프로세스 종료 후 제거
if lsmod | grep -q "^${MOAL_MOD}\b" || lsmod | grep -q "^${MLAN_MOD}\b"; then
    logger -p local0.info "[$tag:$LINENO] $MOAL_MOD/$MLAN_MOD already loaded → unloading"

    # wpa 관련 프로세스 종료 — 반드시 systemctl stop 을 먼저 한다.
    # wpa_supplicant@ 에 Restart=always 가 붙어 있으므로 kill -9 만 하면 systemd 가 이를
    # 실패로 보고 RestartSec 뒤 재기동한다. 그 프로세스가 아래 rmmod 창에서 mlan 을 다시
    # 점유하면 rmmod 가 실패하고, 실패는 exit 1 → wifi_init.service 의
    # OnFailure=wlan_emergency_reboot.service 로 이어진다. systemctl stop 은 명시적 정지라
    # systemd 가 재기동하지 않는다. kill -9 는 systemd 밖에서 뜬 잔존 프로세스용 폴백으로만
    # 남긴다(수동 실행분 등).
    # graceful stop 시도와 실패를 남긴다. 아래 kill -9 는 PID 를 로깅하는데 그 앞 단계가
    # 무기록이면, "왜 kill 폴백까지 갔는지"를 사후에 알 수 없다.
    # 이 구간의 logger 에는 모두 `|| true` 를 붙인다. 스크립트가 set -euo pipefail 이라
    # logger 실패(syslog 미기동 등)가 여기서 스크립트를 끊으면 kill -9 폴백까지 건너뛰어진
    # 채 rmmod 창에 진입한다. 특히 `cmd || logger ...` 형태는 cmd 가 실패한 뒤 logger 도
    # 실패하면 표현식 전체가 non-zero 라 set -e 가 발동한다 — 정확히 이 PR 이 막으려는
    # 경로다. 파일 전체는 맨 logger 가 관례지만 이 임계 구간만 예외로 둔다.
    if command -v systemctl >/dev/null 2>&1; then
        logger -p local0.info "[$tag:$LINENO] stopping wpa_supplicant@mlan0/mlan1 via systemctl before rmmod" || true
        # stderr 를 버리지 않는다 — "failed" 사실만 남기고 이유(DBus 불통/권한/유닛 로드
        # 실패)를 지우면 사후 추적이 끊긴다. 이 스크립트는 wifi_init.service 하에서 돌아
        # stderr 가 그대로 journald 에 수집된다.
        systemctl stop wpa_supplicant@mlan0.service wpa_supplicant@mlan1.service \
            || logger -p local0.warn "[$tag:$LINENO] systemctl stop wpa_supplicant@ failed; relying on kill fallback" || true
    else
        logger -p local0.warn "[$tag:$LINENO] systemctl not found; relying on kill fallback before rmmod" || true
    fi
    wpa_pids=$(pgrep -f 'wpa_supplicant.*mlan' 2>/dev/null | tr '\n' ' ') || true
    if [ -n "$wpa_pids" ]; then
        logger -p local0.info "[$tag:$LINENO] killing leftover wpa processes: $wpa_pids"
        kill -9 $wpa_pids 2>/dev/null || true
    fi

    # 무선 인터페이스 체크 및 link down
    for iface in mlan0 mlan1; do
        if [ -d "/sys/class/net/$iface" ]; then
            logger -p local0.info "[$tag:$LINENO] [$iface] found → link down"
            ip link set "$iface" down 2>/dev/null || true
        fi
    done

    # mlan0/mlan1 인터페이스를 사용하는 나머지 프로세스 종료
    for iface in mlan0 mlan1; do
        if [ -d "/sys/class/net/$iface" ]; then
            pids=$(fuser /sys/class/net/"$iface" 2>/dev/null | grep -oE '[0-9]+' | tr '\n' ' ') || true
            if [ -n "$pids" ]; then
                logger -p local0.info "[$tag:$LINENO] killing processes on $iface: $pids"
                kill $pids 2>/dev/null || true
                sleep 0.5
                kill -9 $pids 2>/dev/null || true
            fi
        fi
    done

    # 모듈 제거 (moal → mlan 순서)
    if lsmod | grep -q "^${MOAL_MOD}\b"; then
        rmmod "$MOAL_MOD" 2>/dev/null || logger -p local0.err "[$tag:$LINENO] rmmod $MOAL_MOD failed"
    fi
    if lsmod | grep -q "^${MLAN_MOD}\b"; then
        rmmod "$MLAN_MOD" 2>/dev/null || logger -p local0.err "[$tag:$LINENO] rmmod $MLAN_MOD failed"
    fi

    # 제거 확인
    if lsmod | grep -q "^${MOAL_MOD}\b\|^${MLAN_MOD}\b"; then
        logger -p local0.emerg "[$tag:$LINENO] failed to unload $MOAL_MOD/$MLAN_MOD modules"
        exit 1
    fi
    logger -p local0.info "[$tag:$LINENO] $MOAL_MOD/$MLAN_MOD modules unloaded successfully"
    # rmmod 직후 버스 재초기화 등 하드웨어 settling 대기
    sleep 0.5
fi

# Backup files with self-healing recovery (size>0 + pattern + default fallback)
#logger -p local0.info "[$tag:$LINENO] Starting backup..."
_DEFAULT_DIR="/opt/wlan/config"

# BUS_TYPE에 따라 mod_para의 검증 패턴 동적 결정
if [ -f "$WIFI_INIT_CONF_JSON" ] && command -v jq >/dev/null 2>&1; then
    _BUS_BAK=$(jq -r '.global.BUS_TYPE // "pcie"' "$WIFI_INIT_CONF_JSON" 2>/dev/null)
else
    _BUS_BAK="pcie"
fi
if [ "${_BUS_BAK}" = "sdio" ]; then
    _MOD_PARA_PATTERN="SD9098_0"
else
    _MOD_PARA_PATTERN="PCIE9098_0"
fi

/usr/local/scripts/backup_file.sh /lib/firmware/$MOD_PARA "$_MOD_PARA_PATTERN" "$_DEFAULT_DIR/wlan/wifi_mod_para.conf" \
    || logger -p local0.err "[$tag:$LINENO] backup failed: $MOD_PARA"
# TXPWRLIMIT는 변형이 5개+이고 사용자 정책에 따라 바뀌므로 default 매핑 없이 .bak에만 의존.
# 인터페이스별 경로(.mlanN.TXPWRLIMIT_PATH // 전역)를 각각 백업하되 동일 경로는 한 번만.
_txpwr_bak_seen=""
for _ti in mlan0 mlan1; do
    if command -v jq >/dev/null 2>&1 && [ -f "$WIFI_INIT_CONF_JSON" ]; then
        _tp=$(jq -r --arg i "$_ti" '.[$i].TXPWRLIMIT_PATH // ""' "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    else
        _tp=""
    fi
    [ -z "$_tp" ] && _tp="${TXPWRLIMIT_PATH:-}"
    [ "$_tp" = "none" ] && _tp=""
    [ -z "$_tp" ] && continue
    case " $_txpwr_bak_seen " in *" $_tp "*) continue ;; esac
    _txpwr_bak_seen="$_txpwr_bak_seen $_tp"
    /usr/local/scripts/backup_file.sh "$_tp" txpwrlimit_2g_cfg_set "" \
        || logger -p local0.err "[$tag:$LINENO] backup failed: TXPWRLIMIT ($_tp)"
done
/usr/local/scripts/backup_file.sh /etc/systemd/network/20-mlan0.network mlan0 "$_DEFAULT_DIR/systemd/network/20-mlan0.network" \
    || logger -p local0.err "[$tag:$LINENO] backup failed: 20-mlan0.network"
/usr/local/scripts/backup_file.sh /etc/systemd/network/21-mlan1.network mlan1 "$_DEFAULT_DIR/systemd/network/21-mlan1.network" \
    || logger -p local0.err "[$tag:$LINENO] backup failed: 21-mlan1.network"
/usr/local/scripts/backup_file.sh /etc/systemd/network/22-eth0.network eth0 "$_DEFAULT_DIR/systemd/network/22-eth0.network" \
    || logger -p local0.err "[$tag:$LINENO] backup failed: 22-eth0.network"
/usr/local/scripts/backup_file.sh /etc/wpa_supplicant/wpa_supplicant-mlan0.conf network= "$_DEFAULT_DIR/wpa_supplicant/wpa_supplicant-mlan0.conf" \
    || logger -p local0.err "[$tag:$LINENO] backup failed: wpa_supplicant-mlan0"
/usr/local/scripts/backup_file.sh /etc/wpa_supplicant/wpa_supplicant-mlan1.conf network= "$_DEFAULT_DIR/wpa_supplicant/wpa_supplicant-mlan1.conf" \
    || logger -p local0.err "[$tag:$LINENO] backup failed: wpa_supplicant-mlan1"

# backup_file 복원(단일블록 원본 가능) 직후 모드 A extra_ssid 자동 블록을 멱등 재생성.
# 복원-then-확장 순서 의존: backup_file이 default(단일블록)로 복원해도 여기서 자가 복원.
# 모드 B/빈 배열은 자동 블록만 제거(무회귀). 함수 부재 시(lib 미source) 조용히 skip.
if command -v wifi_init_sync_extra_ssid_blocks >/dev/null 2>&1; then
    wifi_init_sync_extra_ssid_blocks mlan0 /etc/wpa_supplicant/wpa_supplicant-mlan0.conf \
        || logger -p local0.err "[$tag:$LINENO] extra_ssid block sync failed: mlan0"
    wifi_init_sync_extra_ssid_blocks mlan1 /etc/wpa_supplicant/wpa_supplicant-mlan1.conf \
        || logger -p local0.err "[$tag:$LINENO] extra_ssid block sync failed: mlan1"
fi

# bgscan 가드(경고-only): conf의 비주석 bgscan=는 wpa_supplicant 자율 로밍을 켜
# Roaming(0x04) notify(3훅)를 우회한다 — 운영 전제 위반 감시
# (wlan-opc docs/implementation/design-roam-indication-notify.md §8.3/§8.4).
# mlan0/mlan1 conf를 순회 검사(파일 존재 시) — DBDC 재평가 완료(2026-08-07)로 mlan1 포함.
# 런타임 wpa_cli set 경로는 wifi_checker.sh의 주기 가드가 보조한다.
# (set -e: grep 무매치는 || true로 흡수)
for _bg_iface in mlan0 mlan1; do
    _bg_conf="/etc/wpa_supplicant/wpa_supplicant-${_bg_iface}.conf"
    [ -f "$_bg_conf" ] || continue
    _bgscan_line=$(grep -E '^[[:space:]]*bgscan[[:space:]]*=' "$_bg_conf" 2>/dev/null | head -1) || true
    if [ -n "${_bgscan_line:-}" ]; then
        logger -p local0.warning "[$tag:$LINENO] [$_bg_iface] active wpa_supplicant bgscan in conf ('${_bgscan_line}') — autonomous roaming bypasses Roaming(0x04) notify (design §8.4)"
    fi
done
unset _bgscan_line _bg_iface _bg_conf

# Apply wifi_init_conf.json values to wifi_mod_para.conf
# Mappings (PCIE9098_0/SD9098_0 ← mlan0, PCIE9098_1/SD9098_1 ← mlan1):
#   - net_rx        ← mlanN.net_rx (int)
#   - mgmt_hex_dump ← mlanN.mgmt_hex_dump_enable (bool → 1/0)
#   - bridge_mode   ← (블록에서 제거) engine="moal"이면 moal insmod 인자 bridge_mode=1로 전달
#   - dev_cap_mask  ← mlanN.STANDARD (없으면 global.STANDARD) → n/ac만 set, ax/빈값은 라인 삭제(칩 기본값)
#   - cal_data_cfg  ← mlanN.CAL_DATA_CFG (없으면 global.CAL_DATA_CFG) → 경로 set, 빈값/none은 cal_data_cfg=none
apply_mod_para_from_json() {
    local conf="/lib/firmware/$MOD_PARA"
    [ -f "$conf" ] || return 0

    local _bus blk_prefix
    _bus=$(jq -r '.global.BUS_TYPE // "pcie"' "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    if [ "$_bus" = "sdio" ]; then blk_prefix="SD9098"; else blk_prefix="PCIE9098"; fi

    # dev_cap_mask 산출용 global fallback (인터페이스별 STANDARD가 비었을 때 사용)
    local g_standard g_devcap g_caldata
    g_standard=$(jq -r '.global.STANDARD // ""' "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    g_devcap=$(jq -r '.global.DEV_CAP_MASK // ""' "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    # cal_data_cfg 산출용 global fallback (인터페이스별 CAL_DATA_CFG가 비었을 때 사용)
    g_caldata=$(jq -r '.global.CAL_DATA_CFG // ""' "$WIFI_INIT_CONF_JSON" 2>/dev/null)

    # JSON → mod_para 값 산출
    local m0_net_rx m1_net_rx m0_mgmt m1_mgmt
    m0_net_rx=$(jq -r '.mlan0.net_rx // 0' "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    m1_net_rx=$(jq -r '.mlan1.net_rx // 0' "$WIFI_INIT_CONF_JSON" 2>/dev/null)

    [ "$(jq -r '.mlan0.mgmt_hex_dump_enable // false' "$WIFI_INIT_CONF_JSON" 2>/dev/null)" = "true" ] && m0_mgmt=1 || m0_mgmt=0
    [ "$(jq -r '.mlan1.mgmt_hex_dump_enable // false' "$WIFI_INIT_CONF_JSON" 2>/dev/null)" = "true" ] && m1_mgmt=1 || m1_mgmt=0

    # 블록 내 key=value 1줄을 idempotent하게 반영 (기존 라인 제거 후 닫는 `}` 직전 삽입)
    _set_kv_in_block() {
        local block="$1" key="$2" value="$3"
        sed -i "/^[[:space:]]*${block}[[:space:]]*=/{
            :loop
            n
            /^[[:space:]]*${key}=/d
            /^[[:space:]]*}/!b loop
        }" "$conf"

        sed -i "/^[[:space:]]*${block}[[:space:]]*=/{
            :loop
            n
            /^[[:space:]]*}/{
                i\\	${key}=${value}
                b done
            }
            b loop
            :done
        }" "$conf"
        #logger -p local0.info "[$tag:$LINENO] ${block}: ${key}=${value}"
    }

    # 블록에서 key 라인을 제거 (없으면 no-op)
    _del_kv_in_block() {
        local block="$1" key="$2"
        sed -i "/^[[:space:]]*${block}[[:space:]]*=/{
            :loop
            n
            /^[[:space:]]*${key}=/d
            /^[[:space:]]*}/!b loop
        }" "$conf"
    }

    # 표준 레벨: n < ac < ax
    _std_level() {
        case "$1" in n) echo 1 ;; ac) echo 2 ;; ax) echo 3 ;; *) echo 0 ;; esac
    }

    # 인터페이스 STANDARD(없으면 global)를 dev_cap_mask로 변환해 블록에 반영.
    #   인터페이스 native max 이상이면 라인 삭제(제한 불필요=칩 기본값): mlan0 max=ax, mlan1 max=ac.
    #   그보다 낮은 표준만 dev_cap_mask 설정. n→0xfffcdfff, ac→0xfffcffff. mlan1 ax는 경고.
    _apply_dev_cap_mask() {
        local iface="$1" block="$2"
        local s mask iface_max
        s=$(jq -r --arg i "$iface" '.[$i].STANDARD // ""' "$WIFI_INIT_CONF_JSON" 2>/dev/null)
        [ -z "$s" ] && s="$g_standard"
        s=$(printf '%s' "$s" | tr '[:upper:]' '[:lower:]')
        case "$s" in
            4) s="n" ;;
            5) s="ac" ;;
            6) s="ax" ;;
        esac

        # STANDARD 미지정 → raw fallback 또는 라인 삭제
        if [ -z "$s" ]; then
            if [ -n "$g_devcap" ]; then
                _set_kv_in_block "$block" "dev_cap_mask" "$g_devcap"
            else
                _del_kv_in_block "$block" "dev_cap_mask"
            fi
            return
        fi

        if [ "$(_std_level "$s")" = "0" ]; then
            logger -p local0.err "[$tag:$LINENO] ${iface} STANDARD invalid: $s"
            _del_kv_in_block "$block" "dev_cap_mask"
            return
        fi

        # 인터페이스 native 최대 표준 (mlan1은 ax 미지원)
        if [ "$iface" = "mlan1" ]; then iface_max="ac"; else iface_max="ax"; fi

        if [ "$(_std_level "$s")" -ge "$(_std_level "$iface_max")" ]; then
            # native max 이상 → 제한 불필요 = 라인 삭제(칩 기본값)
            if [ "$iface" = "mlan1" ] && [ "$s" = "ax" ]; then
                logger -p local0.warn "[$tag:$LINENO] ${iface} STANDARD=ax 비권장 — dev_cap_mask 미설정(기본값)"
            fi
            _del_kv_in_block "$block" "dev_cap_mask"
            return
        fi

        case "$s" in
            n)  mask="0xfffcdfff" ;;
            ac) mask="0xfffcffff" ;;
        esac
        _set_kv_in_block "$block" "dev_cap_mask" "$mask"
    }

    # 인터페이스 CAL_DATA_CFG(없으면 global)를 블록의 cal_data_cfg로 반영.
    #   경로가 있으면 그대로 set, 비었거나 none이면 cal_data_cfg=none (외부 cal 파일 미사용).
    _apply_cal_data_cfg() {
        local iface="$1" block="$2" v
        v=$(jq -r --arg i "$iface" '.[$i].CAL_DATA_CFG // ""' "$WIFI_INIT_CONF_JSON" 2>/dev/null)
        [ -z "$v" ] && v="$g_caldata"
        if [ -z "$v" ] || [ "$v" = "none" ]; then
            _set_kv_in_block "$block" "cal_data_cfg" "none"
        else
            _set_kv_in_block "$block" "cal_data_cfg" "$v"
        fi
    }

    # MFG 프로파일: 운영용 키 주입을 건너뛰고 기존 라인을 제거(드라이버/FW 기본값 사용),
    # cal_data_cfg=none 강제. 제조에서는 labtool/mfgbridge가 cal data를 직접 관리(로드/OTP
    # 기록)하므로 드라이버의 호스트 cal 선주입은 OTP 실제 상태 검증을 가리고 캘 기록과
    # 충돌할 수 있다. dev_cap_mask/net_rx 등 STA 운영값은 MFG 계측에 불필요.
    if [ "${MFG_MODE:-0}" == "1" ]; then
        local _blk
        for _blk in "${blk_prefix}_0" "${blk_prefix}_1"; do
            _del_kv_in_block "$_blk" "bridge_mode"
            _del_kv_in_block "$_blk" "net_rx"
            _del_kv_in_block "$_blk" "mgmt_hex_dump"
            _del_kv_in_block "$_blk" "dev_cap_mask"
            _set_kv_in_block "$_blk" "cal_data_cfg" "none"
        done
        logger -p local0.info "[$tag:$LINENO] MFG profile: mod_para operational keys removed (net_rx/mgmt_hex_dump/dev_cap_mask), cal_data_cfg=none"
        return 0
    fi

    _set_kv_in_block "${blk_prefix}_0" "net_rx"        "$m0_net_rx"
    _set_kv_in_block "${blk_prefix}_1" "net_rx"        "$m1_net_rx"
    _set_kv_in_block "${blk_prefix}_0" "mgmt_hex_dump" "$m0_mgmt"
    _set_kv_in_block "${blk_prefix}_1" "mgmt_hex_dump" "$m1_mgmt"
    # bridge_mode는 moal insmod 인자로 전달 → 블록의 기존 라인은 제거(전역 인자 override 방지)
    _del_kv_in_block "${blk_prefix}_0" "bridge_mode"
    _del_kv_in_block "${blk_prefix}_1" "bridge_mode"
    _apply_dev_cap_mask "mlan0" "${blk_prefix}_0"
    _apply_dev_cap_mask "mlan1" "${blk_prefix}_1"
    _apply_cal_data_cfg "mlan0" "${blk_prefix}_0"
    _apply_cal_data_cfg "mlan1" "${blk_prefix}_1"
}
apply_mod_para_from_json

try_insmod() {
    local module_path=$1
    local args=$2
    local output
    local ret

    output=$(insmod "$module_path" $args 2>&1)
    ret=$?

    if [ $ret -eq 0 ]; then
        logger -p local0.info "[$tag:$LINENO] insmod $(basename $module_path) success"
    else
        logger -p local0.emerg "[$tag:$LINENO] insmod $(basename $module_path) fail"
        logger -p local0.emerg "[$tag:$LINENO] $output"
    fi

    return $ret
}

try_read_mac() {
    local label=$1
    local file=$2
    local iface=$3

    if [ ! -f "$file" ]; then
        return 1
    fi

    local val
    val=$(cat "$file")
    if mac_is_assignable "$val"; then
        #logger -p local0.info "[$tag:$LINENO] [$iface] $label mac: $val"
        echo "$val"
        return 0
    fi

    logger -p local0.warn "[$tag:$LINENO] [$iface] invalid $label mac: $val"
    return 1
}

read_mac_from_json() {
    local label=$1
    local iface=$2
    local key=$3

    [ -f "$WIFI_INIT_CONF_JSON" ] && command -v jq >/dev/null 2>&1 || return 1

    local val
    val=$(jq -r ".mac.${iface}.${key} // empty" "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    [ -z "$val" ] && return 1

    if mac_is_assignable "$val"; then
        logger -p local0.info "[$tag:$LINENO] [$iface] $label mac (json): $val"
        echo "$val"
        return 0
    fi

    logger -p local0.warn "[$tag:$LINENO] [$iface] invalid $label mac in json: $val"
    return 1
}

try_dynamic_mac() {
    local iface=$1
    local mac

    mac=$(try_read_mac "dynamic" /tmp/eth0_client_mac "$iface") || return 1
    echo "$mac"
}

resolve_mac() {
    local iface=$1
    local mode=$2
    local mac=""
    local source="none"

    #logger -p local0.info "[$tag:$LINENO] [$iface] MAC_MODE=$mode"

    if [ -z "$mac" ] && [ "$mode" = "dynamic" ]; then
        mac=$(try_dynamic_mac "$iface") || mac=""
        [ -n "$mac" ] && source="dynamic"
    fi

    if [ -z "$mac" ] && [ "$mode" = "static" ]; then
        mac=$(read_mac_from_json "target" "$iface" "target") || mac=""
        [ -n "$mac" ] && source="target"
    fi

    if [ -z "$mac" ]; then
        mac=$(read_mac_from_json "base" "$iface" "base") || mac=""
        [ -n "$mac" ] && source="base"
    fi

    #logger -p local0.info "[$tag:$LINENO] [$iface] mac_source=$source"
    echo "$mac $source"
}

validate_final_mac_plan() {
    local entry iface mac normalized
    declare -A owner_by_mac=()

    for entry in "$@"; do
        iface="${entry%%=*}"
        mac="${entry#*=}"
        [ -n "$mac" ] || continue
        if ! mac_is_assignable "$mac"; then
            logger -p local0.err "[$tag:$LINENO] [$iface] final MAC is invalid: $mac"
            return 1
        fi
        normalized=$(mac_normalize "$mac")
        if [ -n "${owner_by_mac[$normalized]:-}" ]; then
            logger -p local0.err \
                "[$tag:$LINENO] MAC conflict in final plan: $normalized ($iface, ${owner_by_mac[$normalized]})"
            return 1
        fi
        owner_by_mac[$normalized]="$iface"
    done
}

apply_final_mac() {
    local iface="$1" mac="$2" source="$3"
    shift 3
    if [ -z "$mac" ]; then
        # dynamic 클론 잔재 폐기(mac_clone_require_peer=true).
        # 쓸 MAC이 하나도 없는데 .link를 그대로 두면 직전 부팅에 클론한 유선 peer MAC이
        # 남아 바로 아래 insmod에서 udev가 다시 적용한다. 그 결과 같은 PC로 설정한 여러
        # 기기가 전부 같은 MAC을 갖게 된다. MACAddress를 지워 드라이버 기본 MAC으로 되돌린다.
        if [ "$iface" = "$BRIDGE_IFACE" ] && [ "$MAC_MODE" = "dynamic" ] \
            && [ "${MAC_CLONE_REQUIRE_PEER:-true}" = "true" ]; then
            if /usr/local/scripts/update_mac.sh "$iface" --clear; then
                logger -p local0.info \
                    "[$tag:$LINENO] [$iface] no usable MAC (wired peer/base absent); discarded stale clone MAC → driver default"
            else
                logger -p local0.err "[$tag:$LINENO] [$iface] failed to discard stale clone MAC"
            fi
            return 0
        fi
        logger -p local0.info "[$tag:$LINENO] [$iface] no final MAC configured; skip update_mac"
        return 0
    fi
    if /usr/local/scripts/update_mac.sh "$iface" "$mac" "$@"; then
        logger -p local0.info "[$tag:$LINENO] [$iface] final MAC applied: $mac (source=$source)"
        return 0
    fi
    logger -p local0.err "[$tag:$LINENO] update_mac.sh $iface ($source) failed"
    return 1
}

iface_enabled_value() {
    local iface="$1"

    case "$iface" in
        mlan0)
            printf '%s\n' "$MLAN0_ENABLED"
            ;;
        mlan1)
            printf '%s\n' "$MLAN1_ENABLED"
            ;;
        *)
            printf 'true\n'
            ;;
    esac
}

apply_iface_txpwrlimit() {
    local iface="$1"
    local enabled="$2"
    local path

    if [ "$enabled" != "true" ]; then
        logger -p local0.info "[$tag:$LINENO] [$iface] disabled; skip txpwrlimit"
        return 0
    fi

    # 인터페이스별 경로 우선, 비었으면 전역 fallback. none/빈값이면 skip.
    path=$(jq -r --arg i "$iface" '.[$i].TXPWRLIMIT_PATH // ""' "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    [ -z "$path" ] && path="${TXPWRLIMIT_PATH:-}"
    [ "$path" = "none" ] && path=""

    if [ -z "$path" ]; then
        logger -p local0.warn "[$tag:$LINENO] [$iface] TXPWRLIMIT_PATH empty; skip txpwrlimit hostcmd"
        return 0
    fi

    logger -p local0.info "[$tag:$LINENO] [$iface] TXPWRLIMIT_PATH : $path"
    mlanutl "$iface" hostcmd "$path" txpwrlimit_2g_cfg_set > /dev/null 2>&1 || logger -p local0.err "[$tag:$LINENO] [$iface] txpwrlimit_2g_cfg_set failed"
    mlanutl "$iface" hostcmd "$path" txpwrlimit_5g_cfg_set_sub0 > /dev/null 2>&1 || logger -p local0.err "[$tag:$LINENO] [$iface] txpwrlimit_5g_cfg_set_sub0 failed"
    mlanutl "$iface" hostcmd "$path" txpwrlimit_5g_cfg_set_sub1 > /dev/null 2>&1 || logger -p local0.err "[$tag:$LINENO] [$iface] txpwrlimit_5g_cfg_set_sub1 failed"
    mlanutl "$iface" hostcmd "$path" txpwrlimit_5g_cfg_set_sub2 > /dev/null 2>&1 || logger -p local0.err "[$tag:$LINENO] [$iface] txpwrlimit_5g_cfg_set_sub2 failed"
    mlanutl "$iface" hostcmd "$path" txpwrlimit_5g_cfg_set_sub3 > /dev/null 2>&1 || logger -p local0.err "[$tag:$LINENO] [$iface] txpwrlimit_5g_cfg_set_sub3 failed"
}

# thermal_mgmt: FW thermal management 제어 (debug.conf의 SUBID 0x113 hostcmd).
# .mlanN.thermal_mgmt 기본 true(=enable). 명시적 false일 때만 disable.
# TXPWRLIMIT처럼 insmod 직후(인터페이스 생성 후) per-interface 1회 적용.
apply_iface_thermal_mgmt() {
    local iface="$1"
    local enabled="$2"
    local tm block

    if [ "$enabled" != "true" ]; then
        logger -p local0.info "[$tag:$LINENO] [$iface] disabled; skip thermal_mgmt"
        return 0
    fi

    # config/jq 부재(degraded)면 thermal_mgmt 값을 읽을 수 없어 skip — FW는 power-on
    # 기본 상태(통상 enable)를 유지한다(명시 enable/disable은 config가 있을 때만 송신).
    # 다른 per-iface 설정과 동일하게 config 없으면 미적용이되, silent가 아니라 로그로 남긴다.
    if [ ! -f "$WIFI_INIT_CONF_JSON" ] || ! command -v jq >/dev/null 2>&1; then
        logger -p local0.warn "[$tag:$LINENO] [$iface] thermal_mgmt: config/jq 부재 → skip (FW power-on 기본 유지)"
        return 0
    fi

    # jq의 `//`는 false도 alternative 대상이라 raw 값을 가져와 shell case로 분기.
    # true/누락/invalid → factory default(enable), 명시적 false만 disable.
    tm=$(jq -r --arg i "$iface" '.[$i].thermal_mgmt' "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    case "$tm" in
        false) block="disable_thermal_mgmt" ;;
        *)     block="enable_thermal_mgmt" ;;
    esac

    if [ ! -f "$THERMAL_DEBUG_CONF" ]; then
        logger -p local0.warn "[$tag:$LINENO] [$iface] thermal_mgmt: $THERMAL_DEBUG_CONF not found; skip"
        return 0
    fi

    logger -p local0.info "[$tag:$LINENO] [$iface] thermal_mgmt: $block"
    mlanutl "$iface" hostcmd "$THERMAL_DEBUG_CONF" "$block" > /dev/null 2>&1 || \
        logger -p local0.err "[$tag:$LINENO] [$iface] thermal_mgmt $block failed"
}

# .{iface}.radio.{mode,bw} 부팅 재적용 (wifi.sh의 mode/bw/radio-apply와 동기 유지).
# bandcfg/htcapinfo/vhtcfg는 드라이버 RAM 전용이라 insmod마다 FW 기본값으로
# 복원되므로 여기서 다시 적용한다. 부팅 경로이므로 실패해도 항상 0을 반환
# (wifi_init.service OnFailure=emergency reboot 방지) — 실패는 logger로만 남긴다.
apply_radio_mode_bw() {
    local iface="$1"
    local mode bw mask htcap vhtbw vhtcap ok i freq_bands

    [ -f "$WIFI_INIT_CONF_JSON" ] && command -v jq >/dev/null 2>&1 || return 0

    mode=$(jq -r ".${iface}.radio.mode // empty" "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    bw=$(jq -r ".${iface}.radio.bw // empty" "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    [ -z "$mode" ] && [ -z "$bw" ] && return 0

    # 이미 연결된 상태면 bandcfg는 무조건 실패(-EOPNOTSUPP)하고 htcapinfo/vhtcfg만
    # 적용돼 mode/bw split-brain이 됨 → 둘 다 건너뛰고 radio-apply 안내만 남긴다.
    # 연결 판정에 `iw dev link` 를 쓰지 않는다 — moal 이 연결 중에도 "Not connected." 를
    # 반환하면 이 skip 이 통과돼 bandcfg 가 실행되고, 그 결과가 바로 위 주석이 경고하는
    # mode/bw split-brain 이다.
    if wlan_is_connected "$iface"; then
        logger -p local0.info "[$tag:$LINENO] [$iface] connected; skip radio mode/bw re-apply (use 'wifi N radio-apply')"
        return 0
    fi

    # freq↔mode 교차 검증 (wifi.sh radio-apply exit 11 가드와 동기 유지):
    # b/g(2.4G 전용 마스크) + 5G 전용 freq_list는 연결 불가 조합 — mode 적용을
    # 건너뛰어 연결성을 보존한다 (부팅 비치명 원칙, skip+log).
    if { [ "$mode" = "b" ] || [ "$mode" = "g" ]; }; then
        freq_bands=$(wifi_init_conf_freq_bands "${WPA_CONF_DIR:-/etc/wpa_supplicant}/wpa_supplicant-${iface}.conf")
        if [ "$freq_bands" = "5G" ]; then
            logger -p local0.err "[$tag:$LINENO] [$iface] radio.mode=$mode with 5G-only freq_list — dead combo; skip mode (fix freq or mode, then 'wifi N radio-apply')"
            mode=""
            # bw는 모드와 독립이라 계속 적용 — 모드 미변경 상태(기존 5G 연결)에서도
            # HT/VHT BW 제한은 유효하다 (의도된 동작).
            [ -z "$bw" ] && return 0
        fi
    fi

    # bw는 htcapinfo/vhtcfg cap으로 적용한다(아래). 실기 검증(2026-06-15):
    # HE 연결도 이 cap에서 BW가 파생되며(20=0x05c00000/bwcfg0, 40=0x05c20000/bwcfg0,
    # 80=0x05c20000/bwcfg1), 부팅 disconnected 상태에서 cap 설정 후 wpa_supplicant
    # 첫 assoc이 해당 폭으로 협상된다. OMI 경로는 폐기(NXP FW가 STA BW 미반영).

    if [ -n "$mode" ]; then
        # 마스크 테이블은 wifi_init_config_lib.sh 단일 정의 사용
        mask=$(wifi_init_mode_to_bandcfg_mask "$mode")
        if [ -z "$mask" ]; then
            logger -p local0.err "[$tag:$LINENO] [$iface] radio.mode invalid: $mode (skip)"
            mask=""
        fi
        if [ "$iface" = "mlan1" ] && [ "$mode" = "ax" ]; then
            logger -p local0.err "[$tag:$LINENO] [$iface] radio.mode=ax not supported on mlan1 (skip)"
            mask=""
        fi
        if [ -n "$mask" ]; then
            # 부팅 직후 첫 assoc과 경합 가능(-EOPNOTSUPP) → 0.2s×10 재시도
            ok=""
            for i in 1 2 3 4 5 6 7 8 9 10; do
                if mlanutl "$iface" bandcfg "$mask" > /dev/null 2>&1; then
                    ok=1
                    break
                fi
                sleep 0.2
            done
            if [ -n "$ok" ]; then
                logger -p local0.info "[$tag:$LINENO] [$iface] bandcfg $mask (mode=$mode)"
            else
                logger -p local0.err "[$tag:$LINENO] [$iface] bandcfg $mask failed (mode=$mode; run 'wifi N radio-apply' to retry)"
            fi
        fi
    fi

    if [ -n "$bw" ]; then
        # BW 매핑 테이블은 wifi_init_config_lib.sh 단일 정의 사용
        htcap=$(wifi_init_bw_to_htcap "$bw")
        vhtbw=$(wifi_init_bw_to_vhtbw "$bw")
        if [ -z "$htcap" ] || [ -z "$vhtbw" ]; then
            logger -p local0.err "[$tag:$LINENO] [$iface] radio.bw invalid: $bw (skip)"
            return 0
        fi
        # apply_iface_radio_defaults의 mlanutl 연속 호출 간격 관례와 동일
        sleep 0.2
        mlanutl "$iface" htcapinfo "$htcap" > /dev/null 2>&1 || \
            logger -p local0.err "[$tag:$LINENO] [$iface] htcapinfo $htcap failed (bw=$bw)"
        vhtcap=$(mlanutl "$iface" vhtcfg 2 2 2>/dev/null | awk '/VHT Capabilities Info/ {print $NF; exit}')
        if [ -n "$vhtcap" ]; then
            mlanutl "$iface" vhtcfg 2 2 "$vhtbw" "$vhtcap" > /dev/null 2>&1 || \
                logger -p local0.err "[$tag:$LINENO] [$iface] vhtcfg 2 2 $vhtbw $vhtcap failed (bw=$bw)"
        else
            logger -p local0.err "[$tag:$LINENO] [$iface] vhtcfg GET failed; skip vht bw (bw=$bw)"
        fi
        logger -p local0.info "[$tag:$LINENO] [$iface] radio bw=$bw (htcapinfo $htcap, vht bwcfg=$vhtbw)"
    fi

    return 0
}

apply_iface_radio_defaults() {
    local iface="$1"
    local enabled="$2"

    if [ "$enabled" != "true" ]; then
        logger -p local0.info "[$tag:$LINENO] [$iface] disabled; skip radio defaults"
        return 0
    fi

    sleep 0.2
    #logger -p local0.info "[$tag:$LINENO] [$iface] macctrl 0x00010e13"
    mlanutl "$iface" macctrl 0x00010e13 > /dev/null 2>&1 || logger -p local0.err "[$tag:$LINENO] [$iface] macctrl failed"

    sleep 0.2
    #logger -p local0.info "[$tag:$LINENO] [$iface] httxcfg 0x00000063"
    mlanutl "$iface" httxcfg 0x00000063 > /dev/null 2>&1 || logger -p local0.err "[$tag:$LINENO] [$iface] httxcfg failed"

    sleep 0.2
    #logger -p local0.info "[$tag:$LINENO] [$iface] htcapinfo 0x05c20000"
    mlanutl "$iface" htcapinfo 0x05c20000 > /dev/null 2>&1 || logger -p local0.err "[$tag:$LINENO] [$iface] htcapinfo failed"

    sleep 0.2
    logger -p local0.info "[$tag:$LINENO] [$iface] macctrl: 0x00010e13, httxcfg: 0x00000063, htcapinfo: 0x05c20000, reassoctrl: enable"
    mlanutl "$iface" reassoctrl 1 > /dev/null 2>&1 || logger -p local0.err "[$tag:$LINENO] [$iface] reassoctrl failed"

    # Re-apply persisted radio mode/bw (위 htcapinfo 기본값을 의도적으로 덮어씀).
    # 함수는 부팅 보호를 위해 항상 0을 반환하고 실패는 내부에서 logger로 남긴다.
    apply_radio_mode_bw "$iface"

    # 안테나 경로(FW Tx/Rx path)는 rate/MCS보다 근본이라 먼저 적용한다. opt-in이며
    # 꺼져 있으면 FW/보드 기본 경로를 그대로 둔다. global.ANT_TYPE(GPIO mux)과는 별개다.
    wifi_fw_apply_antcfg "$WIFI_INIT_CONF_JSON" "$iface"

    # rate는 association 전에만 설정 가능하며 partial/default 혼합을 금지한다.
    wifi_fw_apply_rate "$WIFI_INIT_CONF_JSON" "$iface"

    # MCS는 SET 성공 코드만 믿지 않고 GET(mcstiercfg + mlan0 11axcfg)을 확인한다.
    # HT/VHT까지 불일치하면 supplicant 시작을 막는다. association 전 HE만 0x0000으로
    # 보이면 pending으로 defer하고 wifi_event가 연결 후 검증/1회 제한 복구한다.
    wifi_fw_apply_mcs_verified "$WIFI_INIT_CONF_JSON" "$iface"
}

# BRIDGE: MAC_MODE에 따라 resolve
# SECONDARY: base만
if [ "$BRIDGE_IFACE" = "mlan0" ]; then
    SECONDARY_IFACE="mlan1"
else
    SECONDARY_IFACE="mlan0"
fi

# MAC 설정 유무/MFG 모드와 무관하게 패키지가 소유한 고아 tmp와 회전 상한 초과분을 정리한다.
# 운영자가 만든 다른 *.link 파일이나 고정 legacy .bak은 삭제하지 않는다.
for _mac_cleanup_if in mlan0 mlan1 eth0; do
    /usr/local/scripts/update_mac.sh "$_mac_cleanup_if" --cleanup \
        || logger -p local0.err "[$tag:$LINENO] [$_mac_cleanup_if] stale MAC artifact cleanup failed"
done

# MFG 프로파일: MAC 설정 전체 skip — 동적/정적 MAC spoofing(update_mac), 유선 IP/MAC
# discovery(wired_mac_ip_get.py), eth0 base MAC까지. 제조 테스트에서 MAC 변경은 불필요하고
# eth0 MAC 변경은 진행 중인 labtool 이더넷 연결을 끊을 수 있다.
if [ "${MFG_MODE:-0}" == "1" ]; then
    logger -p local0.info "[$tag:$LINENO] MFG profile: skip MAC setup (wired_mac_ip_get/update_mac)"
else

# 먼저 세 인터페이스의 최종 MAC을 모두 계산한다. bridge iface는
# dynamic → target → base 우선순위, secondary/eth0는 base를 사용한다.
# 계산이 끝난 뒤 인터페이스별 한 번만 update_mac을 호출하므로 base→override 이중 쓰기와
# 불필요한 백업 회전/flash write가 발생하지 않는다.
BRIDGE_BASE_MAC=$(read_mac_from_json "base" "$BRIDGE_IFACE" "base") || BRIDGE_BASE_MAC=""
SECONDARY_MAC=$(read_mac_from_json "base" "$SECONDARY_IFACE" "base") || SECONDARY_MAC=""
ETH0_MAC=$(read_mac_from_json "base" "eth0" "base") || ETH0_MAC=""
BRIDGE_MAC="$BRIDGE_BASE_MAC"
BRIDGE_MAC_SOURCE="base"

if [ "$BRIDGE_NONE" != "true" ] && wifi_init_iface_is_enabled "$BRIDGE_IFACE" "$IFACE_ENABLED_DEFAULT"; then
    # dynamic 모드면 유선 peer MAC/IP 확보 먼저 (resolve_mac의 try_dynamic_mac가 /tmp/eth0_client_mac를 읽음)
    if [ "$MAC_MODE" = "dynamic" ]; then
        # wired_mac_ip_get.py는 peer를 찾았을 때만 파일을 쓰고 실패 시 기존 파일을 지우지 않는다.
        # 부팅 직후엔 tmpfs라 비어 있지만 wifi_init 재실행에서는 유선을 뽑아도 직전 실행이 남긴
        # peer MAC이 그대로 읽혀 클론이 유지된다. require_peer면 매 탐색을 빈 상태에서 시작한다.
        if [ "${MAC_CLONE_REQUIRE_PEER:-true}" = "true" ]; then
            rm -f /tmp/eth0_client_mac
        fi
        #logger -p local0.info "[$tag:$LINENO] [$BRIDGE_IFACE] running wired_mac_ip_get.py"
        python3 /usr/local/logger/wired_mac_ip_get.py || true
    fi
    # resolve_mac 결과: "mac source" (dynamic → target → base 순 폴백)
    BRIDGE_RESULT=$(resolve_mac "$BRIDGE_IFACE" "$MAC_MODE")
    BRIDGE_MAC="${BRIDGE_RESULT% *}"
    BRIDGE_MAC_SOURCE="${BRIDGE_RESULT##* }"
    if [ -n "$BRIDGE_MAC" ]; then
        logger -p local0.info \
            "[$tag:$LINENO] [$BRIDGE_IFACE] final MAC selected: $BRIDGE_MAC (source=$BRIDGE_MAC_SOURCE)"
    else
        BRIDGE_MAC_SOURCE="none"
        logger -p local0.info "[$tag:$LINENO] [$BRIDGE_IFACE] dynamic/static/base MAC not resolved"
    fi
else
    logger -p local0.info "[$tag:$LINENO] [$BRIDGE_IFACE] bridge inactive (BRIDGE_NONE=$BRIDGE_NONE); keep base MAC"
fi

FINAL_MAC_PLAN=(
    "$BRIDGE_IFACE=$BRIDGE_MAC"
    "$SECONDARY_IFACE=$SECONDARY_MAC"
    "eth0=$ETH0_MAC"
)

# 최종 계획 자체에 같은 MAC이 두 인터페이스에 있으면 부분 적용하지 않고 전체 MAC 쓰기를
# 건너뛴다. 전체 적용 동안 전역 락을 유지해 다른 update_mac/write_mac 호출이 중간에
# 끼어들지 못하게 하고, update_mac.sh에는 이동 예정인 패키지 소유 .link를 판별할
# 전체 계획을 전달한다. 외부/legacy 파일 충돌은 계속 거부한다.
if validate_final_mac_plan "${FINAL_MAC_PLAN[@]}"; then
    if ! mac_acquire_global_lock "${SYSTEMD_NETWORK_DIR:-/etc/systemd/network}"; then
        logger -p local0.emerg "[$tag:$LINENO] failed to acquire global MAC plan lock"
    else
        apply_final_mac "$BRIDGE_IFACE" "$BRIDGE_MAC" "$BRIDGE_MAC_SOURCE" \
            "${FINAL_MAC_PLAN[@]}" \
            || logger -p local0.err "[$tag:$LINENO] [$BRIDGE_IFACE] final MAC apply failed"
        apply_final_mac "$SECONDARY_IFACE" "$SECONDARY_MAC" "base" \
            "${FINAL_MAC_PLAN[@]}" \
            || logger -p local0.err "[$tag:$LINENO] [$SECONDARY_IFACE] final MAC apply failed"
        apply_final_mac "eth0" "$ETH0_MAC" "base" \
            "${FINAL_MAC_PLAN[@]}" \
            || logger -p local0.err "[$tag:$LINENO] [eth0] final MAC apply failed"
        mac_release_global_lock \
            || logger -p local0.err "[$tag:$LINENO] failed to release global MAC plan lock"
    fi
else
    logger -p local0.emerg "[$tag:$LINENO] invalid/duplicate final MAC plan; skip all MAC writes"
fi

# wifi_bridge / wifi_event / wifi_periodic_roam / wifi_ping_monitor / wifi_mgmt_log.timer 등
# 자식 unit의 enable/disable은 운영자의 systemctl 결정이 진실이며, wifi_init.service의
# ExecStartPost=/usr/local/scripts/wifi_services.sh start 가 enable된 것만 일괄 start한다.

fi  # MFG profile: MAC setup skip 끝

# 무선 드라이버 로드 전 안테나 경로(GPIO mux) 설정. ANT_TYPE 비어있으면 건드리지 않음.
# (MFG 프로파일에서도 유지 — 안테나 mux는 제조 RF 측정 경로에 영향)
# GPIO 매핑은 wifi.sh의 ant 명령과 동일 (SW_SEL1/SW_SEL2 LED).
if [ -n "${ANT_TYPE:-}" ]; then
    _ant_sel1=""; _ant_sel2=""
    case "$ANT_TYPE" in
        internal|0) _ant_sel1=0; _ant_sel2=1 ;;
        external|1) _ant_sel1=1; _ant_sel2=0 ;;
        *) logger -p local0.err "[$tag:$LINENO] ANT_TYPE invalid: $ANT_TYPE (expected internal|external)" ;;
    esac
    if [ -n "$_ant_sel1" ]; then
        if [ -e /sys/class/leds/SW_SEL1/brightness ] && [ -e /sys/class/leds/SW_SEL2/brightness ]; then
            echo "$_ant_sel1" > /sys/class/leds/SW_SEL1/brightness 2>/dev/null || logger -p local0.err "[$tag:$LINENO] antenna SW_SEL1 write failed"
            echo "$_ant_sel2" > /sys/class/leds/SW_SEL2/brightness 2>/dev/null || logger -p local0.err "[$tag:$LINENO] antenna SW_SEL2 write failed"
            logger -p local0.info "[$tag:$LINENO] antenna set: ANT_TYPE=$ANT_TYPE (SW_SEL1=$_ant_sel1 SW_SEL2=$_ant_sel2)"
        else
            logger -p local0.warn "[$tag:$LINENO] SW_SEL leds not present; skip antenna set (ANT_TYPE=$ANT_TYPE)"
        fi
    fi
fi

if ! try_insmod "/opt/wlan/driver/$MLAN_KO" ""; then
    echo "$MLAN_KO module load failed"
    exit 1
fi

if ! try_insmod "/opt/wlan/driver/$MOAL_KO" "$moal_args"; then
    echo "$MOAL_KO module load failed"
    exit 1
fi

# mfg_mode는 여기서 재판독하지 않는다 — insmod 인자/fw_name을 결정한 판정값(MFG_MODE)과
# 플래그("현재 드라이버가 mfg로 로드됨")가 항상 일치해야 한다(재판독 시 FW 다운로드 수 초
# 사이의 토글로 플래그-실제 FW 불일치가 고착되는 TOCTOU). 실행 중 conf 토글은 다음
# wifi_init 재실행에서 올바른 재로드로 수렴한다.
if [ "${MFG_MODE:-0}" == "1" ]; then
    # MFG 프로파일: post-insmod 설정을 건너뛰고 성공 종료. exit 1이면 유닛의
    # Restart=on-failure가 10초마다 재실행해 rmmod/insmod 루프가 되고 StartLimit
    # 소진 시 OnFailure=wlan_emergency_reboot까지 이어진다. exit 0이면
    # ExecStartPost(wifi_services.sh start)가 실행되지만, MFG 프로파일에서는
    # wifi_apply_enabled.sh(ExecStartPre)가 STA 유닛을 disable+stop해 두고
    # wifi_services.sh도 start를 skip하므로 STA 데몬은 기동되지 않는다.
    mkdir -p /run/wifi
    : > "$MFG_LOADED_FLAG"
    logger -p local0.info "[$tag:$LINENO] mfg_mode=1 detected, skipping post-insmod setup (MFG profile, exit 0)"
    exit 0
fi
rm -f "$MFG_LOADED_FLAG" 2>/dev/null || true

apply_iface_txpwrlimit "mlan0" "$MLAN0_ENABLED"
apply_iface_txpwrlimit "mlan1" "$MLAN1_ENABLED"

apply_iface_thermal_mgmt "mlan0" "$MLAN0_ENABLED"
apply_iface_thermal_mgmt "mlan1" "$MLAN1_ENABLED"

# .network 파일의 Address=에 서브넷이 없으면 /24 보정
for nf in /etc/systemd/network/*.network; do
    [ -f "$nf" ] || continue
    if grep -qE '^Address=[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' "$nf" 2>/dev/null; then
        sed -i 's|^\(Address=[0-9]\+\.[0-9]\+\.[0-9]\+\.[0-9]\+\)$|\1/24|' "$nf"
        logger -p local0.warn "[$tag:$LINENO] Added missing /24 subnet to Address in $nf"
    fi
done

if command -v systemctl >/dev/null 2>&1; then
    # .network 파일 변경 적용 — networkctl reload 가 지원되면 이를 사용하여
    # eth0/시리얼 네트워크 재초기화를 회피한다 (로그인 경로 보호 목적).
    # reload 실패 시에만 fallback 으로 systemd-networkd 전체 재시작.
    if command -v networkctl >/dev/null 2>&1 && networkctl reload 2>/dev/null; then
        logger -p local0.info "[$tag:$LINENO] networkctl reload ok (eth0 not disrupted)"
        for _nif in mlan0 mlan1; do
            [ -d "/sys/class/net/$_nif" ] && networkctl reconfigure "$_nif" 2>/dev/null || true
        done
    else
        logger -p local0.warn "[$tag:$LINENO] networkctl reload unavailable → restart systemd-networkd"
        systemctl restart systemd-networkd
    fi

    # mlan 인터페이스가 udev로 생성될 때까지 대기 (insmod 직후 race 방지).
    # /sys/class/net/mlanN가 나타나야 mlanutl 호출이 정상 작동.
    wait_iface() {
        local iface="$1" enabled="$2" deadline=$((SECONDS + 5))
        [ "$enabled" = "true" ] || return 0
        while [ $SECONDS -lt $deadline ]; do
            [ -d "/sys/class/net/$iface" ] && return 0
            sleep 0.2
        done
        logger -p local0.warn "[$tag:$LINENO] [$iface] device not present after 5s"
        return 1
    }
    wait_iface "mlan0" "$MLAN0_ENABLED" || true
    wait_iface "mlan1" "$MLAN1_ENABLED" || true

    # === peer_route (옵션 X) 토글 — wbridge.peer_route.enabled ===
    # true(기본): eth0 host scope IP / table 100 / fallback route 부여 + sysctl ARP/RPF 정책 활성
    # false: 모든 변경 사항 revert + sysctl default reset → 변경 전 동작과 동일 (A/B 테스트용)
    # 별도 토글 wbridge.arp_ignore_always.enabled(기본 false)가 true면 peer_route와 무관하게
    # arp_ignore=1/arp_announce=2를 분기 이후 공통 블록에서 강제한다 — IP를 eth0에 두는
    # 토폴로지(mlan0 무IP/타서브넷) 전용. 기본 false인 이유: mlan0-IP 배포 토폴로지에서
    # peer_route=off(A/B·degraded 부팅 fallback) 시 eth0에 /32 미러가 없으므로 arp_ignore=1을
    # 강제하면 유선→BD ARP가 무응답이 된다 (eth0은 .100 미소유 + 브릿지 필터가 공중 유출 차단).
    # Degraded 환경(jq 부재 / config 부재 / config 파싱 실패) 시 보수적으로 false:
    # 사용자가 enabled=false로 운영 중인 시스템에서 jq 깨짐으로 의도 반대 동작(=true) 발동 방지.
    # 정상 환경에서만 jq 결과 신뢰, key가 invalid/missing이면 그때만 factory default(=true) 적용.
    _peer_route_enabled=false
    if command -v jq >/dev/null 2>&1 && [ -f /usr/local/etc/wifi_init_conf.json ]; then
        # jq의 `//` operator는 null뿐 아니라 false도 alternative 대상이라 (`false // true → true`)
        # default를 jq 안에서 처리하면 안 됨. raw 값을 가져와서 shell case로 분기.
        _val=$(jq -r '.wbridge.peer_route.enabled' /usr/local/etc/wifi_init_conf.json 2>/dev/null)
        case "$_val" in
            true|false) _peer_route_enabled="$_val" ;;
            *)          _peer_route_enabled=true ;;  # config 정상 + key invalid/missing → factory default
        esac
        unset _val
    else
        logger -p local0.warn "[$tag:$LINENO] peer_route degraded fallback: no jq or no /usr/local/etc/wifi_init_conf.json → peer_route=off"
    fi

    # === 헬퍼: sysctl -w wrapper (양쪽 분기에서 공유) ===
    # 실패 시 logger.warn으로 가시화. 기존엔 silent fail이라 mlan1 미로드 등 진단 불가.
    _safe_sysctl() {
        if ! sysctl -w "$@" >/dev/null 2>&1; then
            logger -p local0.warn "[$tag:$LINENO] sysctl -w failed: $*"
        fi
    }

    if [ "$_peer_route_enabled" = "true" ]; then
        # === ENABLED: eth0 ↔ mlan0 IP/라우팅 동기화 (옵션 X) ===
        # 20-mlan0.network의 mlan0 Address를 기준으로 다음을 모두 동적 부여 (정적 부여 시
        # enabled=false에서 부여 후 제거 패턴이 발생하므로 의도적으로 wifi_init.sh가 일원 관리):
        #   1) eth0에 <mlan0_ip>/32 (host scope) — connected route 안 만듦, ARP responder 역할만
        #   2) policy rule iif=eth0 → table 100 — peer 응답을 eth0로 강제
        #   3) table 100에 <mlan0_subnet> dev eth0 scope link — iif=eth0 응답 전용 link route
        #   4) sysctl ARP/RPF/Neighbour 정책 (arp_ignore, arp_announce, ignore_routes_with_linkdown 등)
        # NOTE: 이전 설계의 main table fallback route(<subnet>/24 metric 200)는 carrier flap 시
        # 전체 subnet 트래픽이 eth0로 redirect되는 부작용 때문에 제거됨. BD→peer 송신은
        # wired_mac_ip_get.py의 peer host route(<peer>/32 dev eth0)에만 의존.
        # subshell + set +e + || true로 어떤 ip 명령 실패도 wifi_init.sh를 죽이지 않게 격리.
        (
            set +e
            if [ -r /etc/systemd/network/20-mlan0.network ] && [ -d /sys/class/net/eth0 ]; then
                _mlan_addr=$(awk -F= '/^[[:space:]]*Address[[:space:]]*=/{gsub(/[[:space:]]/,"",$2); print $2; exit}' \
                             /etc/systemd/network/20-mlan0.network)
                _mlan_ip=${_mlan_addr%/*}
                _mlan_subnet=$(python3 -c "import ipaddress,sys; print(ipaddress.ip_interface(sys.argv[1]).network)" \
                               "$_mlan_addr" 2>/dev/null)
                if echo "$_mlan_ip" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' && [ -n "$_mlan_subnet" ]; then
                    # 1) eth0 host scope IP 갱신 (기존 /32 잔재 제거 후)
                    ip -4 addr show dev eth0 2>/dev/null | awk '/inet .*\/32/{print $2}' | while read -r _old; do
                        ip addr del "$_old" dev eth0 2>/dev/null
                    done
                    ip addr replace "${_mlan_ip}/32" dev eth0 2>/dev/null \
                        || logger -p local0.warn "[$tag:$LINENO] eth0 sync: addr replace failed (${_mlan_ip}/32)"

                    # 2) policy rule iif=eth0 → table 100 (22-eth0.network에서 이관)
                    #    enabled=false에서 부여 후 제거 패턴 회피를 위해 동적 부여
                    ip rule del iif eth0 table 100 2>/dev/null  # 잔재 정리 후
                    ip rule add iif eth0 table 100 priority 100 2>/dev/null \
                        || logger -p local0.warn "[$tag:$LINENO] eth0 sync: ip rule add failed (iif eth0 table 100)"

                    # 3) table 100 link route (iif=eth0 응답 전용)
                    ip route flush table 100 2>/dev/null
                    ip route add "$_mlan_subnet" dev eth0 scope link table 100 2>/dev/null \
                        || logger -p local0.warn "[$tag:$LINENO] eth0 sync: table 100 route add failed ($_mlan_subnet)"

                    # main table fallback route 부여는 의도적으로 제거됨:
                    # 이전 설계: <subnet>/24 dev eth0 metric 200 부여 → mlan0 driver 미로드 시 fallback 활성.
                    # 문제: ignore_routes_with_linkdown=1과 결합 시 mlan0 carrier 일시 flap에서
                    # 전체 subnet 트래픽이 eth0로 redirect됨 (peer 외 무선 클라 응답까지 영향).
                    # 새 설계: BD→peer 통신은 wired_mac_ip_get.py의 peer host route(<peer>/32 dev eth0)에만
                    # 의존. peer 발견 실패 시 BD→peer 송신 불가 — [정정 2026-07-17 실측] "peer
                    # initiated는 iif 룰로 OK"는 사실 아님: iif rule/table 100은 인바운드
                    # rp_filter 검증용이고 로컬 생성 응답은 조향하지 않아, host route 부재 시
                    # peer발신 응답도 main 라우트(mlan0)로 가서 neigh 미해소로 전멸한다.
                    # 대안: moal.local_hairpin=1 (드라이버 로컬 hairpin — peer IP 인지 불요).
                    # 잔재 정리(이전 부팅의 fallback route)는 enabled=false 분기에서만 수행.

                    # peer host route/neigh 재적용은 아래 "eth0 대표주소" 블록(게이트:
                    # peer_route=on ∥ local_hairpin=1)으로 이동 — hairpin 단독 구성에서도
                    # BD↔유선peer 채널이 성립해야 하므로 peer_route 분기 밖에서 수행한다.

                    logger -p local0.info "[$tag:$LINENO] peer_route=on: eth0 sync addr=${_mlan_ip}/32 subnet=${_mlan_subnet}"
                else
                    logger -p local0.warn "[$tag:$LINENO] eth0 sync skipped: invalid mlan0 Address ($_mlan_addr)"
                fi
            fi

            # 4) sysctl ARP/RPF/Neighbour 정책 활성 (mlan0 Address 유효성과 무관하게 항상 적용)
            _safe_sysctl net.ipv4.conf.all.arp_ignore=1
            _safe_sysctl net.ipv4.conf.all.arp_announce=2
            _safe_sysctl net.ipv4.conf.eth0.accept_local=1
            _safe_sysctl net.ipv4.conf.eth0.arp_filter=0
            _safe_sysctl net.ipv4.neigh.eth0.gc_stale_time=30
            _safe_sysctl net.ipv4.neigh.eth0.base_reachable_time_ms=15000
            _safe_sysctl net.ipv4.conf.all.ignore_routes_with_linkdown=1
            _safe_sysctl net.ipv4.conf.mlan0.ignore_routes_with_linkdown=1
            _safe_sysctl net.ipv4.conf.mlan1.ignore_routes_with_linkdown=1
            _safe_sysctl net.ipv4.conf.eth0.ignore_routes_with_linkdown=1
            logger -p local0.info "[$tag:$LINENO] peer_route=on: sysctl ARP/RPF policies applied"
        ) || logger -p local0.warn "[$tag:$LINENO] eth0 sync block exited with errors (non-fatal)"
    else
        # === DISABLED: 모든 변경 사항 revert (변경 전 동작 복원) ===
        # 99-bd-arp.conf는 부팅 시 이미 적용됐을 수 있으므로 sysctl을 default 값으로 reset.
        # (arp_ignore_always 토글이 true면 아래 공통 블록이 ARP 정책만 다시 1/2로 덮어쓴다.)
        # wifi_init.sh가 이전 부팅에서 부여한 라우팅도 모두 제거.
        # 22-eth0.network의 policy rule도 명시적으로 제거 (system이 적용했어도 비활성화).
        (
            set +e
            logger -p local0.info "[$tag:$LINENO] peer_route=off: reverting all changes (eth0 routing + sysctl)"

            # 1) eth0의 host scope IP(/32) 모두 제거
            ip -4 addr show dev eth0 2>/dev/null | awk '/inet .*\/32/{print $2}' | while read -r _old; do
                ip addr del "$_old" dev eth0 2>/dev/null
            done

            # 2) main table의 fallback route(metric 200) 제거
            ip route show dev eth0 2>/dev/null | awk '/^[0-9].*metric 200/{print $1}' | while read -r _old_r; do
                ip route del "$_old_r" dev eth0 metric 200 2>/dev/null
            done

            # 3) policy rule + table 100 cleanup (22-eth0.network가 적용한 것 무력화)
            ip rule del iif eth0 table 100 2>/dev/null
            ip route flush table 100 2>/dev/null

            # 4) sysctl을 default 값으로 reset (99-bd-arp.conf 효과 무력화)
            _safe_sysctl net.ipv4.conf.all.arp_ignore=0
            _safe_sysctl net.ipv4.conf.all.arp_announce=0
            _safe_sysctl net.ipv4.conf.eth0.accept_local=0
            _safe_sysctl net.ipv4.conf.eth0.arp_filter=0
            _safe_sysctl net.ipv4.conf.all.ignore_routes_with_linkdown=0
            _safe_sysctl net.ipv4.conf.mlan0.ignore_routes_with_linkdown=0
            _safe_sysctl net.ipv4.conf.mlan1.ignore_routes_with_linkdown=0
            _safe_sysctl net.ipv4.conf.eth0.ignore_routes_with_linkdown=0
            _safe_sysctl net.ipv4.neigh.eth0.gc_stale_time=60
            _safe_sysctl net.ipv4.neigh.eth0.base_reachable_time_ms=30000

            logger -p local0.info "[$tag:$LINENO] peer_route=off: revert done (legacy behavior restored)"
        ) || true
    fi

    # === eth_fallback (B-2) — wbridge.eth_fallback.enabled, 기본 false ===
    # mlan0 IP를 eth0에 /32 미러 + 공유 서브넷 fallback route(metric 200)로 병행 부여.
    # 평시엔 mlan0 connected route(metric 0)가 우선해 잠복, 무선 down 시 networkd의
    # mlan0 주소/라우트 철회가 절체 트리거가 되어 BD↔유선peer 통신이 eth0 직결로
    # 자동 인계·복귀 시 자동 환원 (OHT IP 인지 불요 — G2 무선단절 유선 VHL 해소).
    # 2026-07-17 실기: 절체 후 BD↔OHT 0.44ms(hairpin 미경유), 복귀 자동 환원 확인.
    # 절체 트리거 2계층: ① networkd의 주소/라우트 철회(기본), ② S1 구성
    # (KeepConfiguration/ConfigureWithoutCarrier — 철회 없음)에서는 아래
    # mlan0.ignore_routes_with_linkdown=1 이 linkdown 라우트를 FIB에서 제외해
    # 동일하게 절체(2026-07-17 실기: S1 병용 상태에서 절체 0.41ms 확인).
    # peer_route=off 분기의 /32 일괄 제거보다 뒤에 실행되어 재부여가 유효하다.
    _ef_enabled=false
    if command -v jq >/dev/null 2>&1 && [ -f "$WIFI_INIT_CONF_JSON" ]; then
        _val=$(jq -r '.wbridge.eth_fallback.enabled' "$WIFI_INIT_CONF_JSON" 2>/dev/null)
        [ "$_val" = "true" ] && _ef_enabled=true
        unset _val
    fi
    if [ "$_ef_enabled" = "true" ]; then
        (
            set +e
            if [ -r /etc/systemd/network/20-mlan0.network ] && [ -d /sys/class/net/eth0 ]; then
                _m_addr=$(awk -F= '/^[[:space:]]*Address[[:space:]]*=/{gsub(/[[:space:]]/,"",$2); print $2; exit}' \
                          /etc/systemd/network/20-mlan0.network)
                _m_ip=${_m_addr%/*}
                _m_net=$(python3 -c "import ipaddress,sys; print(ipaddress.ip_interface(sys.argv[1]).network)" \
                         "$_m_addr" 2>/dev/null)
                if echo "$_m_ip" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' && [ -n "$_m_net" ]; then
                    ip addr replace "${_m_ip}/32" dev eth0 2>/dev/null
                    ip route replace "$_m_net" dev eth0 metric 200 src "$_m_ip" 2>/dev/null
                    # S1 병용 대응: 철회가 없는 구성에서도 linkdown 라우트 제외로 절체 성립
                    _safe_sysctl net.ipv4.conf.mlan0.ignore_routes_with_linkdown=1
                    logger -p local0.info "[$tag:$LINENO] eth_fallback=on: ${_m_ip}/32 + $_m_net metric 200 + mlan0 linkdown-skip → eth0"
                else
                    logger -p local0.warn "[$tag:$LINENO] eth_fallback skipped: invalid mlan0 Address ($_m_addr)"
                fi
            fi
        ) || true
    else
        # 잔재 정리: 이전 부팅의 fallback route(metric 200 한정)만 제거 —
        # peer_route 산출물(/32·host route)은 건드리지 않는다.
        ip route show dev eth0 2>/dev/null | awk '/metric 200/{print $1}' | while read -r _r; do
            ip route del "$_r" dev eth0 metric 200 2>/dev/null
        done
    fi
    unset _ef_enabled

    # === eth0 대표주소 = 무선 IP 미러 + peer host route/neigh 재적용 ===
    # 로직 전체는 wifi_peer_net_reapply.sh로 위임 (게이트: peer_route=on ∥
    # moal.local_hairpin=1 — 판정도 스크립트 내부). 같은 스크립트를
    # wlan-peer-net.service(PartOf=systemd-networkd.service)가 networkd restart
    # 직후에도 실행해, restart의 foreign 주소/라우트 flush(실측 2026-07-18)를
    # 자동 복구한다 — 단일 소스·멱등. 상세 주석은 해당 스크립트 참조.
    /usr/local/scripts/wifi_peer_net_reapply.sh \
        || logger -p local0.warn "[$tag:$LINENO] wifi_peer_net_reapply.sh failed (non-fatal)"

    # === 무선 인터페이스 weak-host ARP 봉인 (per-interface, 무조건 적용) ===
    # 커널 실효값 = max(conf.all, conf.dev)이므로, mlan0/mlan1에 arp_ignore=1을
    # 인터페이스 단위로 고정하면 peer_route/arp_ignore_always 토글과 무관하게
    # "무선발 who-has <eth0-IP>에 클론 MAC으로 weak-host 응답"하는 구멍이 항상 닫힌다.
    # 플릿(전 BD 공통 eth0 관리IP + 플랫 L2)에서 이 구멍은 중복 IP/DAI 위반 →
    # exclusion 제재로 이어질 수 있다 (2026-07-17 리뷰 지적, 근본해소).
    # - mlan0 자신의 IP에 대한 공중 ARP 응답은 유지된다 (target이 수신 iface에
    #   설정된 경우만 응답하는 것이 arp_ignore=1의 정의 — 무선↔BD 통신 무영향).
    # - eth0의 weak-host 응답(유선→BD ARP, peer_route=off 구성의 전제)은 conf.all/
    #   conf.eth0 지배라 영향 없음.
    # - arp_announce=2 짝: mlan0발 ARP 요청의 sender IP가 eth0-IP로 광고되는
    #   corner case 차단 (정상 플로우는 어차피 mlan0 IP 선택 — 방어적).
    _safe_sysctl net.ipv4.conf.mlan0.arp_ignore=1
    _safe_sysctl net.ipv4.conf.mlan0.arp_announce=2
    _safe_sysctl net.ipv4.conf.mlan1.arp_ignore=1
    _safe_sysctl net.ipv4.conf.mlan1.arp_announce=2
    # netdev 재생성(FW 복구 remove/add, 드라이버 재로드) 시 재적용은
    # /etc/udev/rules.d/99-wlan-arp-seal.rules 가 담당 (이중화).

    # eth0 rp_filter=loose 고정 (프로비저닝 명시): hairpin/peer_route=off 계열은
    # 유선 peer발 패킷이 eth0으로 들어오지만 peer IP의 라우팅 경로는 mlan0을
    # 가리켜(공유 서브넷) strict(1)면 역경로 불일치로 martian drop 된다.
    # 이미지 기본이 loose였더라도 여기서 고정해 이미지 드리프트를 차단.
    # peer_route=on/eth0-IP 토폴로지에는 무해(iif rule/직접 소유가 각각 커버).
    _safe_sysctl net.ipv4.conf.eth0.rp_filter=2

    # === arp_ignore_always (옵션) — wbridge.arp_ignore_always.enabled, 기본 false ===
    # IP를 eth0에 두는 토폴로지(mlan0 무IP 또는 타서브넷) 전용 opt-in. 이 토폴로지에서는
    # mlan0 커널 스택이 weak host model로 eth0 IP에 대한 ARP 요청에도 클론 MAC으로 응답
    # → eth0 스택의 정상 응답과 이중 응답 레이스 → peer 클라이언트 ARP 캐시 오염/간헐 단절.
    # moal driver bridge / pcap(wbridge) 어느 구현이든 커널 단에서 공통 차단해야 하므로
    # 브릿지별 가드가 아닌 sysctl로 잡는다.
    #   arp_ignore=1: 요청이 들어온 인터페이스에 설정된 IP일 때만 응답 (레이스 원천 차단)
    #   arp_announce=2: ARP sender 주소를 출구 인터페이스 기준으로 선택 (짝 정책)
    # 기본 false 유지 필수: mlan0-IP 배포 토폴로지에서 peer_route=off(A/B·degraded fallback)와
    # 조합되면 eth0이 mlan0 IP를 미소유한 채 arp_ignore=1이 되어 유선→BD ARP 무응답 회귀 발생.
    # peer_route=on은 자체적으로 arp_ignore=1을 이미 적용하므로 이 토글이 불필요하다.
    _arp_ignore_always=false
    if command -v jq >/dev/null 2>&1 && [ -f /usr/local/etc/wifi_init_conf.json ]; then
        _val=$(jq -r '.wbridge.arp_ignore_always.enabled' /usr/local/etc/wifi_init_conf.json 2>/dev/null)
        [ "$_val" = "true" ] && _arp_ignore_always=true
        unset _val
    fi
    if [ "$_arp_ignore_always" = "true" ]; then
        # [GUARD] 조용한 "유선→BD ARP 무응답" 회귀를 가시화한다 (동작은 바꾸지 않음).
        # arp_ignore_always=on + peer_route=off 조합은, 유선↔BD(박스) 통신이 필요한 mlan0-IP
        # 토폴로지에서 eth0이 mlan0 IP 미러(/32)를 갖지 못한 채 arp_ignore=1이 되어 유선→BD ARP가
        # 무응답이 된다 (위 988-997 주석 참조). eth0-IP 토폴로지(유선↔BD 불필요)에서는 정상이므로
        # 토폴로지를 추정해 동작을 바꾸지 않고 경고만 남긴다. 유선↔BD가 필요하면 3종 세트로 설정:
        #   wbridge.peer_route.enabled=true + ip_discovery=true + arp_ignore_always.enabled=false
        if [ "$_peer_route_enabled" = "false" ]; then
            logger -p local0.warn "[$tag:$LINENO] [GUARD] arp_ignore_always=on + peer_route=off -> wired->BD ARP UNANSWERED on mlan0-IP topology. If wired<->BD is required, set wbridge.peer_route.enabled=true + ip_discovery=true + arp_ignore_always.enabled=false. (Safe to ignore on eth0-IP topology where wired<->BD is not needed.)" || true
        fi
        _safe_sysctl net.ipv4.conf.all.arp_ignore=1
        _safe_sysctl net.ipv4.conf.all.arp_announce=2
        logger -p local0.info "[$tag:$LINENO] arp_ignore_always=on: ARP policy forced (eth0-IP topology mode)"
    fi
    unset _arp_ignore_always

    unset _peer_route_enabled

    # 모듈 로드 + networkd가 mlan 인터페이스를 생성한 직후, association 전에 라디오 기본값 적용.
    # 그 외 자식 데몬은 ExecStartPost(/usr/local/scripts/wifi_services.sh)가 systemctl enable
    # 상태에 따라 일괄 start한다.
    fw_config_failed=0
    apply_iface_radio_defaults "mlan0" "$MLAN0_ENABLED" || fw_config_failed=1
    apply_iface_radio_defaults "mlan1" "$MLAN1_ENABLED" || fw_config_failed=1
    if [ "$fw_config_failed" -ne 0 ]; then
        mcs_failure_code=$(wifi_fw_mcs_cold_failure_code)
        logger -p local0.warn "[$tag:$LINENO] MCS verification failed before association; exit=$mcs_failure_code (75=one cold lifecycle retry, 1=persistent failure)"
        exit "$mcs_failure_code"
    fi
    wifi_fw_mcs_cold_success

    # supplicant 데몬 게이트 — .<iface>.enabled(인터페이스 전체)와
    # .<iface>.wpa_supplicant.enabled(데몬 개별, 기본 true)가 모두 참일 때만 직접 start한다.
    # wifi_apply_enabled.sh가 같은 두 키로 유닛을 enable/disable하지만 systemctl start는
    # disable된 유닛도 기동시키므로, 이 경로를 막지 않으면 두 키 모두 실효하지 않는다
    # (인터페이스를 끈 운영자에게 supplicant가 붙는 상태 — 스키마의 "false면 모든 자식
    # 데몬 disable" 계약 위반).
    # 진리값 해석은 인라인 문자열 비교가 아니라 이 스크립트의 정규 리더를 쓴다 —
    # 0/no/off 같은 값에서 wifi_apply_enabled.sh와 어긋나면 한쪽은 disable하고 다른 쪽은
    # start하는 모순이 생긴다. jq의 //는 false를 falsey로 취급하므로 쓰지 않는다.
    _wpa_enabled=true
    if [ "$(wifi_init_get_iface_enabled "$BRIDGE_IFACE" "$IFACE_ENABLED_DEFAULT")" != "true" ]; then
        _wpa_enabled=false
    elif wifi_init_json_key_exists "$WIFI_INIT_CONF_JSON" ".${BRIDGE_IFACE}.wpa_supplicant.enabled"; then
        _wpa_enabled=$(wifi_init_normalize_bool \
            "$(wifi_init_json_read_raw "$WIFI_INIT_CONF_JSON" ".${BRIDGE_IFACE}.wpa_supplicant.enabled")" \
            "true")
    fi

    # wifi_manager 계열이 enable되어 있으면 wpa_supplicant는 wifi_manager가 자체 관리하므로
    # wifi_init이 별도로 시작하지 않는다. 그렇지 않으면 BRIDGE_IFACE의 wpa_supplicant를
    # 직접 start하여 association이 ExecStartPost 시점 이전에 시작되도록 한다.
    wifi_manager_active=false
    for svc in wifi_manager wifi-manager; do
        if systemctl is-enabled "$svc" >/dev/null 2>&1; then
            wifi_manager_active=true
            logger -p local0.info "[$tag:$LINENO] $svc is enabled; skip manual wpa_supplicant start"
            break
        fi
    done
    if [ "$_wpa_enabled" = "false" ]; then
        logger -p local0.info "[$tag:$LINENO] [$BRIDGE_IFACE] disabled by .enabled or .wpa_supplicant.enabled; skip wpa_supplicant@$BRIDGE_IFACE start"
    elif [ "$wifi_manager_active" = "false" ]; then
        logger -p local0.info "[$tag:$LINENO] No wifi_manager service; starting wpa_supplicant@$BRIDGE_IFACE"
        systemctl start --no-block "wpa_supplicant@${BRIDGE_IFACE}" 2>/dev/null || \
            logger -p local0.err "[$tag:$LINENO] Failed to start wpa_supplicant@$BRIDGE_IFACE"
    fi
fi
