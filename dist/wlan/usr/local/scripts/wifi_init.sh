#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
# shellcheck source=./wifi_init_config_lib.sh
. "$SCRIPT_DIR/wifi_init_config_lib.sh"

tag=$(basename "$0")
JSON_FILE="${JSON_FILE:-/usr/local/etc/config.json}"
MOD_PARA="cts/wifi_mod_para.conf"
TXPWRLIMIT_PATH="/lib/firmware/cts/txpwrlimit_cfg_9098.conf"
ANT_TYPE=""

WIFI_INIT_CONF_JSON="${WIFI_INIT_CONF_JSON:-/usr/local/etc/wifi_init_conf.json}"

logger -p local0.info "[$tag:$LINENO] wifi initializing"

if [ -f "$WIFI_INIT_CONF_JSON" ] && command -v jq >/dev/null 2>&1; then
    BOARD_TYPE=$(jq -r '.global.BOARD_TYPE // "imx8mm"' "$WIFI_INIT_CONF_JSON")
    MOD_PARA=$(jq -r '.global.MOD_PARA // "cts/wifi_mod_para.conf"' "$WIFI_INIT_CONF_JSON")
    TXPWRLIMIT_PATH=$(jq -r '.global.TXPWRLIMIT_PATH // "/lib/firmware/cts/txpwrlimit_cfg_9098.conf"' "$WIFI_INIT_CONF_JSON")
    ANT_TYPE=$(jq -r '.global.ANT_TYPE // ""' "$WIFI_INIT_CONF_JSON")
    BRIDGE_IFACE=$(jq -r '.wbridge.bridge_iface // .global.BRIDGE_IFACE // "mlan0"' "$WIFI_INIT_CONF_JSON")
    WBRIDGE_ENABLED=$(jq -r 'if .wbridge.enabled then "true" else "false" end' "$WIFI_INIT_CONF_JSON" 2>/dev/null || echo "true")
    MAC_MODE=$(jq -r '.wbridge.mac_mode // .global.MAC_MODE // "default"' "$WIFI_INIT_CONF_JSON")
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
logger -p local0.info "[$tag:$LINENO] moal_args: $moal_args"
logger -p local0.info "[$tag:$LINENO] BOARD_TYPE=$BOARD_TYPE, modules=$MLAN_KO/$MOAL_KO"

# BUS_TYPE/BLUETOOTH 기반 fw_name 자동 갱신
if [ -f "$WIFI_INIT_CONF_JSON" ] && command -v jq >/dev/null 2>&1; then
    _BUS=$(jq -r '.global.BUS_TYPE // "pcie"' "$WIFI_INIT_CONF_JSON")
    _BT=$(jq -r '.global.BLUETOOTH.enable // false' "$WIFI_INIT_CONF_JSON")
    _MOD_FILE="/lib/firmware/$(jq -r '.global.MOD_PARA // "cts/wifi_mod_para.conf"' "$WIFI_INIT_CONF_JSON")"
    _MFG=$(grep -m1 'mfg_mode=' "$_MOD_FILE" 2>/dev/null | sed 's/.*mfg_mode=//' | tr -d ' ' || echo "0")

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

MLAN0_ENABLED=$(wifi_init_get_iface_enabled "mlan0" "true")
MLAN1_ENABLED=$(wifi_init_get_iface_enabled "mlan1" "true")
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
logger -p local0.info "[$tag:$LINENO] BRIDGE_IFACE=$BRIDGE_IFACE BRIDGE_NONE=$BRIDGE_NONE MAC_MODE=$MAC_MODE"
logger -p local0.info "[$tag:$LINENO] mlan0 enabled=$MLAN0_ENABLED freq=$MLAN0_FREQ"
logger -p local0.info "[$tag:$LINENO] mlan1 enabled=$MLAN1_ENABLED freq=$MLAN1_FREQ"

# 이미 로드된 모듈이 있으면 사용 프로세스 종료 후 제거
if lsmod | grep -q "^${MOAL_MOD}\b" || lsmod | grep -q "^${MLAN_MOD}\b"; then
    logger -p local0.info "[$tag:$LINENO] $MOAL_MOD/$MLAN_MOD already loaded → unloading"

    # wpa 관련 프로세스 종료
    wpa_pids=$(pgrep -f 'wpa_supplicant.*mlan' 2>/dev/null | tr '\n' ' ') || true
    if [ -n "$wpa_pids" ]; then
        logger -p local0.info "[$tag:$LINENO] killing wpa processes: $wpa_pids"
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
logger -p local0.info "[$tag:$LINENO] Starting backup..."
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

/usr/local/scripts/backup_file.sh /lib/firmware/$MOD_PARA "$_MOD_PARA_PATTERN" "$_DEFAULT_DIR/wifi_mod_para__.conf" \
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

# Apply wifi_init_conf.json values to wifi_mod_para.conf
# Mappings (PCIE9098_0/SD9098_0 ← mlan0, PCIE9098_1/SD9098_1 ← mlan1):
#   - net_rx        ← mlanN.net_rx (int)
#   - mgmt_hex_dump ← mlanN.mgmt_hex_dump_enable (bool → 1/0)
#   - bridge_mode   ← wbridge.engine="moal" → bridge_iface 블록=1, 나머지=0 (else 둘 다 0)
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
    local m0_net_rx m1_net_rx m0_mgmt m1_mgmt engine bridge_iface m0_bridge m1_bridge
    m0_net_rx=$(jq -r '.mlan0.net_rx // 0' "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    m1_net_rx=$(jq -r '.mlan1.net_rx // 0' "$WIFI_INIT_CONF_JSON" 2>/dev/null)

    [ "$(jq -r '.mlan0.mgmt_hex_dump_enable // false' "$WIFI_INIT_CONF_JSON" 2>/dev/null)" = "true" ] && m0_mgmt=1 || m0_mgmt=0
    [ "$(jq -r '.mlan1.mgmt_hex_dump_enable // false' "$WIFI_INIT_CONF_JSON" 2>/dev/null)" = "true" ] && m1_mgmt=1 || m1_mgmt=0

    engine=$(jq -r '.wbridge.engine // "pcap"' "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    bridge_iface=$(jq -r '.wbridge.bridge_iface // .global.BRIDGE_IFACE // "mlan0"' "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    m0_bridge=0; m1_bridge=0
    if [ "$engine" = "moal" ]; then
        if [ "$bridge_iface" = "mlan1" ]; then m1_bridge=1; else m0_bridge=1; fi
    fi

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
        logger -p local0.info "[$tag:$LINENO] ${block}: ${key}=${value}"
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
        logger -p local0.info "[$tag:$LINENO] ${block}: ${key} removed"
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

    _set_kv_in_block "${blk_prefix}_0" "net_rx"        "$m0_net_rx"
    _set_kv_in_block "${blk_prefix}_1" "net_rx"        "$m1_net_rx"
    _set_kv_in_block "${blk_prefix}_0" "mgmt_hex_dump" "$m0_mgmt"
    _set_kv_in_block "${blk_prefix}_1" "mgmt_hex_dump" "$m1_mgmt"
    _set_kv_in_block "${blk_prefix}_0" "bridge_mode"   "$m0_bridge"
    _set_kv_in_block "${blk_prefix}_1" "bridge_mode"   "$m1_bridge"
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

MAC_REGEX='^([a-fA-F0-9]{2}:){5}[a-fA-F0-9]{2}$'

try_read_mac() {
    local label=$1
    local file=$2
    local iface=$3

    if [ ! -f "$file" ]; then
        return 1
    fi

    local val
    val=$(cat "$file")
    if [[ "$val" =~ $MAC_REGEX ]]; then
        logger -p local0.info "[$tag:$LINENO] [$iface] $label mac: $val"
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

    if [[ "$val" =~ $MAC_REGEX ]]; then
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

    logger -p local0.info "[$tag:$LINENO] [$iface] MAC_MODE=$mode"

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

    logger -p local0.info "[$tag:$LINENO] [$iface] mac_source=$source"
    echo "$mac $source"
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

apply_mcs_tier() {
    local iface="$1"
    local enabled ht vht he args=""

    [ -f "$WIFI_INIT_CONF_JSON" ] && command -v jq >/dev/null 2>&1 || return 0

    enabled=$(jq -r ".${iface}.mcs_tier.enabled // false" "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    [ "$enabled" = "true" ] || return 0

    ht=$(jq -r ".${iface}.mcs_tier.ht // \"\"" "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    vht=$(jq -r ".${iface}.mcs_tier.vht // \"\"" "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    he=$(jq -r ".${iface}.mcs_tier.he // \"\"" "$WIFI_INIT_CONF_JSON" 2>/dev/null)

    # Empty string skips; non-empty passes through verbatim (e.g. "both 7" → "he both 7").
    [ -n "$ht" ] && args="$args ht $ht"
    [ -n "$vht" ] && args="$args vht $vht"
    [ -n "$he" ] && args="$args he $he"

    if [ -z "$args" ]; then
        logger -p local0.warn "[$tag:$LINENO] [$iface] mcs_tier: enabled but no tier specified"
        return 0
    fi

    logger -p local0.info "[$tag:$LINENO] [$iface] mcstiercfg$args"
    mlanutl "$iface" mcstiercfg $args > /dev/null 2>&1 || \
        logger -p local0.err "[$tag:$LINENO] [$iface] mcstiercfg failed"
}

apply_iface_radio_defaults() {
    local iface="$1"
    local enabled="$2"

    if [ "$enabled" != "true" ]; then
        logger -p local0.info "[$tag:$LINENO] [$iface] disabled; skip radio defaults"
        return 0
    fi

    sleep 0.2
    logger -p local0.info "[$tag:$LINENO] [$iface] macctrl 0x00010e13"
    mlanutl "$iface" macctrl 0x00010e13 > /dev/null 2>&1 || logger -p local0.err "[$tag:$LINENO] [$iface] macctrl failed"

    sleep 0.2
    logger -p local0.info "[$tag:$LINENO] [$iface] httxcfg 0x00000063"
    mlanutl "$iface" httxcfg 0x00000063 > /dev/null 2>&1 || logger -p local0.err "[$tag:$LINENO] [$iface] httxcfg failed"

    sleep 0.2
    logger -p local0.info "[$tag:$LINENO] [$iface] htcapinfo 0x05c20000"
    mlanutl "$iface" htcapinfo 0x05c20000 > /dev/null 2>&1 || logger -p local0.err "[$tag:$LINENO] [$iface] htcapinfo failed"

    sleep 0.2
    logger -p local0.info "[$tag:$LINENO] [$iface] reassoctrl enable"
    mlanutl "$iface" reassoctrl 1 > /dev/null 2>&1 || logger -p local0.err "[$tag:$LINENO] [$iface] reassoctrl failed"

    # Apply rate_adapt_cfg (per-iface override > global, must be set before association)
    local ra_mode ra_low ra_high ra_interval ra_interval_ms
    ra_mode=$(jq -r ".${iface}.rate_adapt.mode // .global.rate_adapt.mode // empty" "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    if [ -n "$ra_mode" ]; then
        ra_low=$(jq -r ".${iface}.rate_adapt.low_thresh // .global.rate_adapt.low_thresh // 255" "$WIFI_INIT_CONF_JSON" 2>/dev/null)
        ra_high=$(jq -r ".${iface}.rate_adapt.high_thresh // .global.rate_adapt.high_thresh // 255" "$WIFI_INIT_CONF_JSON" 2>/dev/null)
        ra_interval_ms=$(jq -r ".${iface}.rate_adapt.interval_ms // .global.rate_adapt.interval_ms // 100" "$WIFI_INIT_CONF_JSON" 2>/dev/null)
        ra_interval=$((ra_interval_ms / 10))
        sleep 0.2
        logger -p local0.info "[$tag:$LINENO] [$iface] rate_adapt_cfg $ra_mode $ra_low $ra_high $ra_interval (${ra_interval_ms}ms)"
        mlanutl "$iface" rate_adapt_cfg "$ra_mode" "$ra_low" "$ra_high" "$ra_interval" > /dev/null 2>&1 || \
            logger -p local0.err "[$tag:$LINENO] [$iface] rate_adapt_cfg failed"
    fi

    # Apply MCS tier capability limit from per-interface config
    apply_mcs_tier "$iface" || \
        logger -p local0.err "[$tag:$LINENO] [$iface] apply_mcs_tier failed (continuing)"
}

# BRIDGE: MAC_MODE에 따라 resolve
# SECONDARY: base만
if [ "$BRIDGE_IFACE" = "mlan0" ]; then
    SECONDARY_IFACE="mlan1"
else
    SECONDARY_IFACE="mlan0"
fi

# 동적 MAC이 필요한 경우 wired_mac_ip_get.py 먼저 실행
if [ "$MAC_MODE" = "dynamic" ] && wifi_init_iface_is_enabled "$BRIDGE_IFACE" "true"; then
    logger -p local0.info "[$tag:$LINENO] [$BRIDGE_IFACE] running wired_mac_ip_get.py"
    python3 /usr/local/logger/wired_mac_ip_get.py || true
fi

# resolve_mac 결과: "mac source"
BRIDGE_ENABLED=$(iface_enabled_value "$BRIDGE_IFACE")
if [ "$BRIDGE_ENABLED" = "true" ]; then
    BRIDGE_RESULT=$(resolve_mac "$BRIDGE_IFACE" "$MAC_MODE")
    BRIDGE_MAC="${BRIDGE_RESULT% *}"
    BRIDGE_MAC_SOURCE="${BRIDGE_RESULT##* }"
    if [ -n "$BRIDGE_MAC" ]; then
        /usr/local/scripts/update_mac.sh "$BRIDGE_IFACE" "$BRIDGE_MAC" || logger -p local0.err "[$tag:$LINENO] update_mac.sh $BRIDGE_IFACE failed"
    else
        logger -p local0.info "[$tag:$LINENO] [$BRIDGE_IFACE] no MAC resolved; skip update_mac"
        BRIDGE_MAC_SOURCE="none"
    fi
else
    BRIDGE_MAC_SOURCE="disabled"
    logger -p local0.info "[$tag:$LINENO] [$BRIDGE_IFACE] disabled; skip primary MAC update"
fi

SECONDARY_ENABLED=$(iface_enabled_value "$SECONDARY_IFACE")
if [ "$SECONDARY_ENABLED" = "true" ]; then
    SECONDARY_MAC=$(read_mac_from_json "base" "$SECONDARY_IFACE" "base") || SECONDARY_MAC=""
    SECONDARY_MAC_SOURCE="base"
    if [ -n "$SECONDARY_MAC" ]; then
        /usr/local/scripts/update_mac.sh "$SECONDARY_IFACE" "$SECONDARY_MAC" || logger -p local0.err "[$tag:$LINENO] update_mac.sh $SECONDARY_IFACE failed"
    else
        logger -p local0.info "[$tag:$LINENO] [$SECONDARY_IFACE] no base MAC configured; skip update_mac"
        SECONDARY_MAC_SOURCE="none"
    fi
else
    SECONDARY_MAC_SOURCE="disabled"
    logger -p local0.info "[$tag:$LINENO] [$SECONDARY_IFACE] disabled; skip secondary MAC update"
fi

# wifi_bridge / wifi_event / wifi_periodic_roam / wifi_ping_monitor / wifi_mgmt_log.timer 등
# 자식 unit의 enable/disable은 운영자의 systemctl 결정이 진실이며, wifi_init.service의
# ExecStartPost=/usr/local/scripts/wifi_services.sh start 가 enable된 것만 일괄 start한다.

# --- eth0: base ---
ETH0_MAC=$(read_mac_from_json "base" "eth0" "base") || ETH0_MAC=""
if [ -n "$ETH0_MAC" ]; then
    /usr/local/scripts/update_mac.sh eth0 "$ETH0_MAC" || logger -p local0.err "[$tag:$LINENO] update_mac.sh eth0 failed"
else
    logger -p local0.info "[$tag:$LINENO] [eth0] no base MAC configured; skip update_mac"
fi

# 무선 드라이버 로드 전 안테나 경로(GPIO mux) 설정. ANT_TYPE 비어있으면 건드리지 않음.
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

# mfg_mode 체크: mod_para.conf에서 읽기
MFG_MODE=$(grep -m1 'mfg_mode=' "/lib/firmware/$MOD_PARA" 2>/dev/null | sed 's/.*mfg_mode=//' | tr -d ' ' || echo "0")
if [ "${MFG_MODE:-0}" == "1" ]; then
    logger -p local0.info "[$tag:$LINENO] mfg_mode=1 detected, skipping post-insmod setup"
    exit 1
fi

apply_iface_txpwrlimit "mlan0" "$MLAN0_ENABLED"
apply_iface_txpwrlimit "mlan1" "$MLAN1_ENABLED"

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

    # 모듈 로드 + networkd가 mlan 인터페이스를 생성한 직후, association 전에 라디오 기본값 적용.
    # 그 외 자식 데몬은 ExecStartPost(/usr/local/scripts/wifi_services.sh)가 systemctl enable
    # 상태에 따라 일괄 start한다.
    apply_iface_radio_defaults "mlan0" "$MLAN0_ENABLED"
    apply_iface_radio_defaults "mlan1" "$MLAN1_ENABLED"

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
    if [ "$wifi_manager_active" = "false" ]; then
        logger -p local0.info "[$tag:$LINENO] No wifi_manager service; starting wpa_supplicant@$BRIDGE_IFACE"
        systemctl start --no-block "wpa_supplicant@${BRIDGE_IFACE}" 2>/dev/null || \
            logger -p local0.err "[$tag:$LINENO] Failed to start wpa_supplicant@$BRIDGE_IFACE"
    fi
fi
