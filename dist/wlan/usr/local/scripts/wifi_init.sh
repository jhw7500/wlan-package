#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
# shellcheck source=./wifi_init_config_lib.sh
. "$SCRIPT_DIR/wifi_init_config_lib.sh"

tag=$(basename "$0")
JSON_FILE="${JSON_FILE:-/usr/local/etc/config.json}"
MOD_PARA="cts/wifi_mod_para.conf"
CAL_DATA_CFG="cts/WlanCalData_ext_RD.conf"
TXPWRLIMIT_PATH="/lib/firmware/cts/txpwrlimit_cfg_9098.conf"
DEV_CAP_MASK=""

WIFI_INIT_CONF_JSON="${WIFI_INIT_CONF_JSON:-/usr/local/etc/wifi_init_conf.json}"

logger -p local0.info "[$tag:$LINENO] wifi initializing"

if [ -f "$WIFI_INIT_CONF_JSON" ] && command -v jq >/dev/null 2>&1; then
    BOARD_TYPE=$(jq -r '.global.BOARD_TYPE // "imx8mm"' "$WIFI_INIT_CONF_JSON")
    MOD_PARA=$(jq -r '.global.MOD_PARA // "cts/wifi_mod_para.conf"' "$WIFI_INIT_CONF_JSON")
    CAL_DATA_CFG=$(jq -r '.global.CAL_DATA_CFG // "cts/WlanCalData_ext_RD.conf"' "$WIFI_INIT_CONF_JSON")
    TXPWRLIMIT_PATH=$(jq -r '.global.TXPWRLIMIT_PATH // "/lib/firmware/cts/txpwrlimit_cfg_9098.conf"' "$WIFI_INIT_CONF_JSON")
    STANDARD=$(jq -r '.global.STANDARD // ""' "$WIFI_INIT_CONF_JSON")
    DEV_CAP_MASK=$(jq -r '.global.DEV_CAP_MASK // ""' "$WIFI_INIT_CONF_JSON")
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


if [ "${CAL_DATA_CFG:-}" = "none" ]; then
    CAL_DATA_CFG=""
fi
if [ "${TXPWRLIMIT_PATH:-}" = "none" ]; then
    TXPWRLIMIT_PATH=""
fi

STANDARD="${STANDARD:-}"
if [ -n "$STANDARD" ]; then
    standard_lc=$(printf '%s' "$STANDARD" | tr '[:upper:]' '[:lower:]')
    if [ "$standard_lc" = "4" ]; then
        standard_lc="n"
    elif [ "$standard_lc" = "5" ]; then
        standard_lc="ac"
    elif [ "$standard_lc" = "6" ]; then
        standard_lc="ax"
    fi

    #if [ "$standard_lc" = "ax" ]; then
    #    logger -p local0.warn "[$tag:$LINENO] STANDARD=ax requested but mlan1 does not support ax; using ac"
    #    standard_lc="ac"
    #fi

    if [ "$standard_lc" = "n" ]; then
        DEV_CAP_MASK="0xfffcdfff"
    elif [ "$standard_lc" = "ac" ]; then
        DEV_CAP_MASK="0xfffcffff"
    elif [ "$standard_lc" = "ax" ]; then
        DEV_CAP_MASK="0xffffffff"
    else
        logger -p local0.err "[$tag:$LINENO] STANDARD invalid: $STANDARD"
    fi
fi

# moal 파라미터 구성
moal_args="mod_para=$MOD_PARA cal_data_cfg=$CAL_DATA_CFG"
if [ -n "${DEV_CAP_MASK:-}" ]; then
    moal_args="$moal_args dev_cap_mask=$DEV_CAP_MASK"
fi
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

# Backup files with error logging
logger -p local0.info "[$tag:$LINENO] Starting backup..."
/usr/local/scripts/backup_file.sh /lib/firmware/$MOD_PARA PCIE9098_0 || logger -p local0.err "[$tag:$LINENO] backup failed: $MOD_PARA"
/usr/local/scripts/backup_file.sh $TXPWRLIMIT_PATH txpwrlimit_2g_cfg_set || logger -p local0.err "[$tag:$LINENO] backup failed: TXPWRLIMIT"
/usr/local/scripts/backup_file.sh /etc/systemd/network/20-mlan0.network mlan0 || logger -p local0.err "[$tag:$LINENO] backup failed: 20-mlan0.network"
/usr/local/scripts/backup_file.sh /etc/systemd/network/21-mlan1.network mlan1 || logger -p local0.err "[$tag:$LINENO] backup failed: 21-mlan1.network"
/usr/local/scripts/backup_file.sh /etc/systemd/network/22-eth0.network eth0 || logger -p local0.err "[$tag:$LINENO] backup failed: 22-eth0.network"
/usr/local/scripts/backup_file.sh /etc/wpa_supplicant/wpa_supplicant-mlan0.conf network= || logger -p local0.err "[$tag:$LINENO] backup failed: wpa_supplicant-mlan0"
/usr/local/scripts/backup_file.sh /etc/wpa_supplicant/wpa_supplicant-mlan1.conf network= || logger -p local0.err "[$tag:$LINENO] backup failed: wpa_supplicant-mlan1"

# Apply net_rx from wifi_init_conf.json to wifi_mod_para.conf
# PCIE9098_0 ← mlan0.net_rx, PCIE9098_1 ← mlan1.net_rx
apply_net_rx_to_mod_para() {
    local conf="/lib/firmware/$MOD_PARA"
    [ -f "$conf" ] || return 0

    local mlan0_net_rx mlan1_net_rx
    mlan0_net_rx=$(jq -r '.mlan0.net_rx // 0' "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    mlan1_net_rx=$(jq -r '.mlan1.net_rx // 0' "$WIFI_INIT_CONF_JSON" 2>/dev/null)

    _set_net_rx_in_block() {
        local block="$1" value="$2"
        # Remove existing net_rx line in block, then always write value
        sed -i "/^[[:space:]]*${block}[[:space:]]*=/{
            :loop
            n
            /^[[:space:]]*net_rx=/d
            /^[[:space:]]*}/!b loop
        }" "$conf"

        sed -i "/^[[:space:]]*${block}[[:space:]]*=/{
            :loop
            n
            /^[[:space:]]*}/{
                i\\	net_rx=${value}
                b done
            }
            b loop
            :done
        }" "$conf"
        logger -p local0.info "[$tag:$LINENO] ${block}: net_rx=${value}"
    }

    local _bus
    _bus=$(jq -r '.global.BUS_TYPE // "pcie"' "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    local blk_prefix
    if [ "$_bus" = "sdio" ]; then blk_prefix="SD9098"; else blk_prefix="PCIE9098"; fi

    _set_net_rx_in_block "${blk_prefix}_0" "$mlan0_net_rx"
    _set_net_rx_in_block "${blk_prefix}_1" "$mlan1_net_rx"
}
apply_net_rx_to_mod_para

# Apply bridge_mode from wbridge.engine to wifi_mod_para.conf
# engine=moal → bridge_iface 블록에 bridge_mode=1, 나머지 0
# engine=pcap|tpacket → 모든 블록 bridge_mode=0 (유저스페이스 bridge)
apply_bridge_mode_to_mod_para() {
    local conf="/lib/firmware/$MOD_PARA"
    [ -f "$conf" ] || return 0

    local engine bridge_iface
    engine=$(jq -r '.wbridge.engine // "pcap"' "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    bridge_iface=$(jq -r '.wbridge.bridge_iface // .global.BRIDGE_IFACE // "mlan0"' "$WIFI_INIT_CONF_JSON" 2>/dev/null)

    local _bus
    _bus=$(jq -r '.global.BUS_TYPE // "pcie"' "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    local blk_prefix
    if [ "$_bus" = "sdio" ]; then blk_prefix="SD9098"; else blk_prefix="PCIE9098"; fi

    _set_bridge_mode_in_block() {
        local block="$1" value="$2"
        # Remove existing bridge_mode line in block, then add
        sed -i "/^[[:space:]]*${block}[[:space:]]*=/{
            :loop
            n
            /^[[:space:]]*bridge_mode=/d
            /^[[:space:]]*}/!b loop
        }" "$conf"

        sed -i "/^[[:space:]]*${block}[[:space:]]*=/{
            :loop
            n
            /^[[:space:]]*}/{
                i\\	bridge_mode=${value}
                b done
            }
            b loop
            :done
        }" "$conf"
        logger -p local0.info "[$tag:$LINENO] ${block}: bridge_mode=${value}"
    }

    local mode_0=0 mode_1=0
    if [ "$engine" = "moal" ]; then
        if [ "$bridge_iface" = "mlan1" ]; then
            mode_1=1
        else
            mode_0=1
        fi
    fi

    _set_bridge_mode_in_block "${blk_prefix}_0" "$mode_0"
    _set_bridge_mode_in_block "${blk_prefix}_1" "$mode_1"
}
apply_bridge_mode_to_mod_para

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

    if [ "$enabled" != "true" ]; then
        logger -p local0.info "[$tag:$LINENO] [$iface] disabled; skip txpwrlimit"
        return 0
    fi

    if [ -z "${TXPWRLIMIT_PATH:-}" ]; then
        logger -p local0.warn "[$tag:$LINENO] [$iface] TXPWRLIMIT_PATH empty; skip txpwrlimit hostcmd"
        return 0
    fi

    logger -p local0.info "[$tag:$LINENO] [$iface] TXPWRLIMIT_PATH : $TXPWRLIMIT_PATH"
    mlanutl "$iface" hostcmd "$TXPWRLIMIT_PATH" txpwrlimit_2g_cfg_set > /dev/null 2>&1 || logger -p local0.err "[$tag:$LINENO] [$iface] txpwrlimit_2g_cfg_set failed"
    mlanutl "$iface" hostcmd "$TXPWRLIMIT_PATH" txpwrlimit_5g_cfg_set_sub0 > /dev/null 2>&1 || logger -p local0.err "[$tag:$LINENO] [$iface] txpwrlimit_5g_cfg_set_sub0 failed"
    mlanutl "$iface" hostcmd "$TXPWRLIMIT_PATH" txpwrlimit_5g_cfg_set_sub1 > /dev/null 2>&1 || logger -p local0.err "[$tag:$LINENO] [$iface] txpwrlimit_5g_cfg_set_sub1 failed"
    mlanutl "$iface" hostcmd "$TXPWRLIMIT_PATH" txpwrlimit_5g_cfg_set_sub2 > /dev/null 2>&1 || logger -p local0.err "[$tag:$LINENO] [$iface] txpwrlimit_5g_cfg_set_sub2 failed"
    mlanutl "$iface" hostcmd "$TXPWRLIMIT_PATH" txpwrlimit_5g_cfg_set_sub3 > /dev/null 2>&1 || logger -p local0.err "[$tag:$LINENO] [$iface] txpwrlimit_5g_cfg_set_sub3 failed"
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
    systemctl restart systemd-networkd

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
