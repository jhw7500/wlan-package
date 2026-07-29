#!/bin/bash
# wifi-logger.sh — NXP 88Q9098 channel specified sniffer mode 실시간 로깅 도구
# wifi_capture.py 형식 호환, JSON 필터 설정 지원
set -euo pipefail

# --- 상수 ---
IFACE="mlan0"
MON_IFACE="rtap"
FILTER=7
BANDWIDTH=0
CHANNEL=""
BAND=""
OUTPUT_DIR="."
PCAP_FILE=""
LOG_FILE=""
SAVE_PCAP=0
FILTER_CONF=""
TCPDUMP_PID=""
TSHARK_PID=""

# 필터 변수 (JSON에서 파싱)
TSHARK_DISPLAY_FILTER=""
AWK_PROTO_FILTER=""
AWK_MAC_FILTER=""
AWK_IP_FILTER=""

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# --- 유틸리티 ---
die()  { echo "ERROR: $*" >&2; exit 1; }
warn() { echo "WARN: $*" >&2; }
info() { echo "INFO: $*"; }

usage() {
    cat <<'EOF'
Usage: wifi-logger.sh [OPTIONS]

NXP 88Q9098 channel specified sniffer mode 실시간 프레임 로깅 도구.
관리/제어/데이터 프레임을 파싱하여 사람이 읽을 수 있는 형식으로 출력합니다.

Options:
  -c <channel>     모니터 채널 (미지정 시 자동감지)
  -b <band>        밴드 (미지정 시 자동감지 또는 채널에서 추론)
  -f <filter>      filter_flag 비트맵 (기본값: 7 = 관리+제어+데이터)
  -w <bandwidth>   채널 대역폭: 0=20MHz, 1=40MHz↑, 3=40MHz↓, 4=80MHz
  -i <interface>   mlan 인터페이스 (기본값: mlan0)
  -F <json>        필터 설정 파일 (기본값: 스크립트 경로의 filter.json)
  -s               pcap 파일도 동시 저장
  -o <directory>   출력 경로 (기본값: .)
  -l <logfile>     로그 파일 경로 (미지정 시 stdout만)
  -h               도움말

Filter JSON 예제 (filter.json):
  {
    "frame_types":          { "management": true, "control": false, "data": true },
    "management_subtypes":  { "exclude": ["beacon"] },
    "protocols":            { "mode": "include", "list": ["ARP", "EAPOL", "ICMP"] },
    "mac":                  { "mode": "include", "list": ["58:86:94:fc:e0:63"] },
    "ip":                   { "mode": "include", "list": ["192.168.0.101"] }
  }

  mode: "all" = 필터 없음, "include" = list만 표시, "exclude" = list 제외
  management_subtypes.exclude: beacon, probe-req, probe-resp, auth, deauth 등

Examples:
  wifi-logger.sh -c 40                         # 기본 필터
  wifi-logger.sh -c 40 -F filter.json          # JSON 필터 적용
  wifi-logger.sh -c 40 -F filter.json -s       # + pcap 저장
EOF
    exit 0
}

# --- 전제 조건 ---

check_prerequisites() {
    [ "$(id -u)" -eq 0 ] || die "root 권한이 필요합니다"
    for cmd in mlanutl wpa_cli tcpdump tshark python3; do
        command -v "$cmd" >/dev/null 2>&1 || die "'$cmd'이(가) 설치되어 있지 않습니다"
    done
}

cleanup_existing() {
    mlanutl "$IFACE" netmon 0 2>/dev/null || true
}

# --- 밴드 ---

band_to_bitmap() {
    local band="$1"
    case "$band" in [0-9]*) echo "$band"; return ;; esac
    case "$band" in
        B) echo 1 ;; G) echo 2 ;; A) echo 4 ;;
        GN) echo 8 ;; AN) echo 16 ;; GAC) echo 32 ;; AAC) echo 64 ;;
        *) die "알 수 없는 밴드: $band" ;;
    esac
}

bitmap_to_label() {
    case "$1" in
        1) echo "B" ;; 2) echo "G" ;; 4) echo "A" ;;
        8) echo "GN" ;; 11) echo "BGN" ;; 16) echo "AN" ;;
        20) echo "AAN" ;; 32) echo "GAC" ;; 64) echo "AAC" ;;
        *) echo "$1" ;;
    esac
}

# --- 자동감지 ---

auto_detect() {
    info "현재 STA 연결에서 채널/밴드 자동감지 중..."
    # `iw dev link` 는 moal 에서 연결 중에도 "Not connected." 를 반환할 수 있어(2026-07-29
    # 실측) 멀쩡한 연결을 미연결로 오탐지한다. wlan_link_lib 로 판정·조회한다.
    # 단 이 스크립트는 **단독 배포**를 지원하므로(wifi-capture.sh 와 동일) lib 이 없으면
    # 최소 구현으로 대체해 자체 완결성을 유지한다.
    if [ -r /usr/local/scripts/wlan_link_lib.sh ]; then
        # shellcheck source=/dev/null
        . /usr/local/scripts/wlan_link_lib.sh
    else
        # lib 과 동일하게 **계단식**이어야 한다 — wpa_cli 만 보면 supplicant 가 죽었을 때
        # 물리적으로 연결돼 있어도 캡처가 거부되어, 이 변경이 없애려는 상황이 그대로 남는다.
        wlan_is_connected() {
            local _s _b
            _s=$(wpa_cli -i "$1" status 2>/dev/null)
            _b=$(printf '%s\n' "$_s" | sed -n 's/^bssid=//p' | head -1)
            [ "$(printf '%s\n' "$_s" | sed -n 's/^wpa_state=//p' | head -1)" = "COMPLETED" ] \
                && [ -n "$_b" ] && [ "$_b" != "00:00:00:00:00:00" ] && return 0
            [ -n "$(iw dev "$1" station dump 2>/dev/null | sed -n 's/^Station .*/x/p' | head -1)" ]
        }
        wlan_freq_mhz() {
            local _f
            _f=$(wpa_cli -i "$1" status 2>/dev/null | sed -n 's/^freq=//p' | head -1)
            [ -n "$_f" ] || _f=$(iw dev "$1" info 2>/dev/null \
                                 | sed -n 's/.*(\([0-9]\{4,\}\) MHz).*/\1/p' | head -1)
            printf '%s' "$_f"
        }
    fi
    wlan_is_connected "$IFACE" || die "STA가 연결되어 있지 않습니다. -c와 -b를 직접 지정하세요"

    # 아래 대역폭 자동 감지가 파싱할 원문. 종전에는 `iw link` 출력을 썼는데, station dump 는
    # "tx bitrate: 780.0 MBit/s VHT-MCS 9 80MHz VHT-NSS 2" 처럼 대역폭 표기를 함께 담는다.
    local status
    status=$(iw dev "$IFACE" station dump 2>/dev/null)

    local freq
    freq=$(wlan_freq_mhz "$IFACE")
    [ -n "$freq" ] || die "주파수를 읽을 수 없습니다"

    if [ -z "$CHANNEL" ]; then
        if [ "$freq" -lt 3000 ]; then CHANNEL=$(( (freq - 2407) / 5 ))
        else CHANNEL=$(( (freq - 5000) / 5 )); fi
    fi
    if [ -z "$BAND" ]; then
        if [ "$freq" -lt 3000 ]; then BAND=11; else BAND=20; fi
    fi
    if [ "$BANDWIDTH" -eq 0 ]; then
        local bw_str
        bw_str=$(echo "$status" | grep -oP '\d+MHz' | head -1 || true)
        if echo "$bw_str" | grep -q "80"; then BANDWIDTH=4
        elif echo "$bw_str" | grep -q "40"; then
            if echo "$status" | grep -qi "above\|+"; then BANDWIDTH=1; else BANDWIDTH=3; fi
        fi
    fi
    info "감지됨: 채널=$CHANNEL, 밴드=$(bitmap_to_label "$BAND") ($BAND), 대역폭=$BANDWIDTH"
}

# --- JSON 필터 파싱 ---

parse_filter_json() {
    local conf="$1"
    [ -f "$conf" ] || die "필터 파일 없음: $conf"

    info "필터 로드: $conf"

    # python3로 JSON 파싱 → 셸 변수로 출력
    eval "$(python3 -c "
import json, sys

with open('$conf') as f:
    c = json.load(f)

# --- tshark display filter ---
parts = []

# frame_types
ft = c.get('frame_types', {})
type_conds = []
if ft.get('management', True): type_conds.append('wlan.fc.type == 0')
if ft.get('control', True):    type_conds.append('wlan.fc.type == 1')
if ft.get('data', True):       type_conds.append('wlan.fc.type == 2')
if type_conds and len(type_conds) < 3:
    parts.append('(' + ' || '.join(type_conds) + ')')

# management_subtypes exclude
msub = c.get('management_subtypes', {})
excludes = msub.get('exclude', [])
subtype_map = {
    'assoc-req':0, 'assoc-resp':1, 'reassoc-req':2, 'reassoc-resp':3,
    'probe-req':4, 'probe-resp':5, 'beacon':8, 'atim':9,
    'disassoc':10, 'auth':11, 'deauth':12, 'action':13, 'action-no-ack':14
}
if excludes:
    exc_conds = []
    for e in excludes:
        e_lower = e.lower().replace(' ', '-').replace('_', '-')
        if e_lower in subtype_map:
            exc_conds.append(f'wlan.fc.subtype == {subtype_map[e_lower]}')
    if exc_conds:
        parts.append('!((wlan.fc.type == 0) && (' + ' || '.join(exc_conds) + '))')

# mac filter → tshark
mac = c.get('mac', {})
mac_mode = mac.get('mode', 'all')
mac_list = mac.get('list', [])
if mac_mode == 'include' and mac_list:
    mc = ' || '.join(f'wlan.addr == {m}' for m in mac_list)
    parts.append('(' + mc + ')')
elif mac_mode == 'exclude' and mac_list:
    mc = ' && '.join(f'wlan.addr != {m}' for m in mac_list)
    parts.append('(' + mc + ')')

# ip filter → tshark
ip = c.get('ip', {})
ip_mode = ip.get('mode', 'all')
ip_list = ip.get('list', [])
if ip_mode == 'include' and ip_list:
    ic = ' || '.join(f'ip.addr == {i}' for i in ip_list)
    # IP 필터는 데이터 프레임에만 적용, 관리/제어는 통과
    parts.append('(wlan.fc.type != 2 || ' + ic + ')')
elif ip_mode == 'exclude' and ip_list:
    ic = ' && '.join(f'ip.addr != {i}' for i in ip_list)
    parts.append('(wlan.fc.type != 2 || ' + ic + ')')

display_filter = ' && '.join(parts) if parts else ''
print(f'TSHARK_DISPLAY_FILTER=\"{display_filter}\"')

# --- awk proto filter ---
proto = c.get('protocols', {})
proto_mode = proto.get('mode', 'all')
proto_list = proto.get('list', [])
if proto_mode == 'include' and proto_list:
    pl = ','.join([p.upper() for p in proto_list])
    print('AWK_PROTO_FILTER=\"include:' + pl + '\"')
elif proto_mode == 'exclude' and proto_list:
    pl = ','.join([p.upper() for p in proto_list])
    print('AWK_PROTO_FILTER=\"exclude:' + pl + '\"')
else:
    print('AWK_PROTO_FILTER=\"\"')
" 2>&1)" || die "필터 JSON 파싱 실패"

    if [ -n "$TSHARK_DISPLAY_FILTER" ]; then
        info "tshark 필터: $TSHARK_DISPLAY_FILTER"
    fi
    if [ -n "$AWK_PROTO_FILTER" ]; then
        info "프로토콜 필터: $AWK_PROTO_FILTER"
    fi
}

# --- tshark 파싱 + 로그 포매팅 ---

start_logger() {
    info "STA 연결 해제 중..."
    wpa_cli -i "$IFACE" disconnect >/dev/null 2>&1 || true
    sleep 1

    info "netmon 활성화: 채널=$CHANNEL, 밴드=$BAND, 필터=$FILTER, 대역폭=$BANDWIDTH"
    if ! mlanutl "$IFACE" netmon 1 "$FILTER" "$BAND" "$CHANNEL" "$BANDWIDTH"; then
        die "netmon 활성화 실패"
    fi

    local retry=0
    while ! ip link show "$MON_IFACE" >/dev/null 2>&1; do
        retry=$((retry + 1))
        [ "$retry" -gt 10 ] && die "$MON_IFACE 인터페이스가 생성되지 않았습니다"
        sleep 0.5
    done
    ip link set "$MON_IFACE" up || die "$MON_IFACE UP 실패"
    info "모니터 인터페이스 준비: $MON_IFACE"

    if [ "$SAVE_PCAP" -eq 1 ]; then
        local band_label timestamp
        band_label=$(bitmap_to_label "$BAND")
        timestamp=$(date +%Y%m%d_%H%M%S)
        PCAP_FILE="${OUTPUT_DIR}/capture_ch${CHANNEL}_${band_label}_${timestamp}.pcap"
        tcpdump -i "$MON_IFACE" -w "$PCAP_FILE" >/dev/null 2>&1 &
        TCPDUMP_PID=$!
        info "pcap 저장: $PCAP_FILE"
    fi

    info "로깅 시작 (Ctrl+C로 종료)"
    echo "# wifi-logger: 채널=$CHANNEL 밴드=$(bitmap_to_label "$BAND") 필터=$FILTER 대역폭=$BANDWIDTH"
    [ -n "$FILTER_CONF" ] && echo "# 필터: $FILTER_CONF"
    echo "# $(date '+%Y-%m-%d %H:%M:%S') 캡처 시작"
    echo ""

    # tshark 명령 구성
    local tshark_args=(
        stdbuf -oL tshark -l -i "$MON_IFACE" -n
        -T fields
        -e frame.time_relative
        -e wlan.sa -e wlan.da -e wlan.ta -e wlan.ra
        -e wlan.fc.type -e wlan.fc.subtype -e wlan.fc.retry -e wlan.seq
        -e radiotap.dbm_antsignal -e radiotap.dbm_antnoise
        -e frame.len -e wlan.fc.protected
        -e _ws.col.Protocol -e _ws.col.Info
        -E "separator=|"
    )
    if [ -n "$TSHARK_DISPLAY_FILTER" ]; then
        tshark_args+=(-Y "$TSHARK_DISPLAY_FILTER")
    fi

    "${tshark_args[@]}" 2>/dev/null | stdbuf -oL awk -F'|' \
        -v logfile="$LOG_FILE" \
        -v proto_filter="$AWK_PROTO_FILTER" \
    'BEGIN {
        mgmt[0]="Assoc Request";   mgmt[1]="Assoc Response"
        mgmt[2]="Reassoc Req";     mgmt[3]="Reassoc Resp"
        mgmt[4]="Probe Request";   mgmt[5]="Probe Response"
        mgmt[8]="Beacon";          mgmt[9]="ATIM"
        mgmt[10]="Disassoc";       mgmt[11]="Auth"
        mgmt[12]="Deauth";         mgmt[13]="Action"
        mgmt[14]="Action NoAck"
        ctrl[8]="Block Ack";       ctrl[9]="Block Ack Req"
        ctrl[11]="RTS";            ctrl[12]="CTS";  ctrl[13]="ACK"
        data[0]="Data";            data[4]="Null"
        data[8]="QoS Data";        data[12]="QoS Null"

        # 프로토콜 필터 파싱
        pf_mode = ""
        delete pf_list
        if (proto_filter != "") {
            split(proto_filter, pf_parts, ":")
            pf_mode = pf_parts[1]
            n = split(pf_parts[2], pf_items, ",")
            for (i = 1; i <= n; i++) pf_list[pf_items[i]] = 1
        }
    }
    function check_proto(proto) {
        if (pf_mode == "") return 1
        up = toupper(proto)
        if (pf_mode == "include") return (up in pf_list)
        if (pf_mode == "exclude") return !(up in pf_list)
        return 1
    }
    {
        ts=$1; sa=$2; da=$3; ta=$4; ra=$5
        ftype=$6; fsub=$7; retry=$8; seq=$9
        rssi=$10; nf=$11; flen=$12; prot=$13
        proto=$14; info=$15

        if (ftype == "") next

        t = sprintf("%10.3f", ts+0)
        snr = "N/A"
        if (rssi != "" && nf != "") snr = sprintf("%3d", rssi - nf)

        line = ""

        if (ftype == 0) {
            name = (fsub in mgmt) ? mgmt[fsub] : "Mgmt(" fsub ")"
            if (sa == "") sa = "---"
            if (da == "") da = "---"
            line = sprintf("%-16s(%2d) : SA=%-17s DA=%-17s RSSI=%4s NF=%4s SNR=%3s Retry=%-3s Seq=%s", \
                name, fsub, sa, da, rssi, nf, snr, retry, seq)
        }
        else if (ftype == 1) {
            name = (fsub in ctrl) ? ctrl[fsub] : "Ctrl(" fsub ")"
            if (ta == "") ta = "---"
            if (ra == "") ra = "---"
            if (fsub == 12 || fsub == 13) {
                line = sprintf("%-16s(%2d) : RA=%-17s %19s RSSI=%4s NF=%4s SNR=%3s", \
                    name, fsub, ra, "", rssi, nf, snr)
            } else {
                line = sprintf("%-16s(%2d) : TA=%-17s RA=%-17s RSSI=%4s NF=%4s SNR=%3s", \
                    name, fsub, ta, ra, rssi, nf, snr)
            }
        }
        else if (ftype == 2) {
            # 프로토콜 필터 적용 (비암호화 데이터)
            if (pf_mode != "" && proto != "" && proto != "802.11") {
                if (!check_proto(proto)) next
            }

            if (prot != 1 && proto != "" && proto != "802.11") {
                name = proto
            } else {
                name = (fsub in data) ? data[fsub] : "Data(" fsub ")"
            }
            if (sa == "") sa = "---"
            if (da == "") da = "---"
            enc = (prot == 1) ? "Enc=Y" : "Enc=N"
            if (fsub == 4 || fsub == 12) {
                line = sprintf("%-16s(%2d) : SA=%-17s DA=%-17s RSSI=%4s NF=%4s SNR=%3s Retry=%-3s Seq=%s", \
                    name, fsub, sa, da, rssi, nf, snr, retry, seq)
            } else if (prot != 1 && info != "") {
                truncinfo = substr(info, 1, 60)
                line = sprintf("%-16s(%2d) : SA=%-17s DA=%-17s RSSI=%4s NF=%4s SNR=%3s Retry=%-3s Seq=%-5s Len=%-5s %s", \
                    name, fsub, sa, da, rssi, nf, snr, retry, seq, flen, truncinfo)
            } else {
                line = sprintf("%-16s(%2d) : SA=%-17s DA=%-17s RSSI=%4s NF=%4s SNR=%3s Retry=%-3s Seq=%-5s Len=%-5s %s", \
                    name, fsub, sa, da, rssi, nf, snr, retry, seq, flen, enc)
            }
        }
        else next

        out = t " " line
        print out
        fflush()
        if (logfile != "") print out >> logfile
    }' &
    TSHARK_PID=$!
    wait "$TSHARK_PID"
}

# --- Cleanup ---

cleanup() {
    echo ""
    info "로깅 종료 중..."

    if [ -n "$TSHARK_PID" ] && kill -0 "$TSHARK_PID" 2>/dev/null; then
        kill "$TSHARK_PID" 2>/dev/null || true
        wait "$TSHARK_PID" 2>/dev/null || true
    fi
    if [ -n "$TCPDUMP_PID" ] && kill -0 "$TCPDUMP_PID" 2>/dev/null; then
        kill "$TCPDUMP_PID" 2>/dev/null || true
        wait "$TCPDUMP_PID" 2>/dev/null || true
    fi

    mlanutl "$IFACE" netmon 0 2>/dev/null || true

    if [ -n "$PCAP_FILE" ] && [ -f "$PCAP_FILE" ]; then
        info "pcap 저장됨: $PCAP_FILE ($(du -h "$PCAP_FILE" | cut -f1))"
    fi
    if [ -n "$LOG_FILE" ] && [ -f "$LOG_FILE" ]; then
        info "로그 저장됨: $LOG_FILE ($(wc -l < "$LOG_FILE") 줄)"
    fi
    exit 0
}

# --- 인자 파싱 + main ---

parse_args() {
    while getopts "c:b:f:w:i:F:o:l:sh" opt; do
        case "$opt" in
            c) CHANNEL="$OPTARG" ;;
            b) BAND=$(band_to_bitmap "$OPTARG") ;;
            f) FILTER="$OPTARG" ;;
            w) BANDWIDTH="$OPTARG" ;;
            i) IFACE="$OPTARG" ;;
            F) FILTER_CONF="$OPTARG" ;;
            o) OUTPUT_DIR="$OPTARG" ;;
            l) LOG_FILE="$OPTARG" ;;
            s) SAVE_PCAP=1 ;;
            h) usage ;;
            *) usage ;;
        esac
    done
}

main() {
    parse_args "$@"
    check_prerequisites
    cleanup_existing

    if [ -z "$CHANNEL" ] && [ -z "$BAND" ]; then
        auto_detect
    elif [ -n "$CHANNEL" ] && [ -z "$BAND" ]; then
        if [ "$CHANNEL" -le 14 ]; then BAND=11
        else BAND=20; fi
        info "밴드 자동 추론: 채널 $CHANNEL → $(bitmap_to_label $BAND) ($BAND)"
    elif [ -z "$CHANNEL" ] && [ -n "$BAND" ]; then
        die "밴드(-b)만 지정됨. 채널(-c)도 지정하세요"
    fi

    # 필터 설정 로드
    if [ -z "$FILTER_CONF" ] && [ -f "$SCRIPT_DIR/filter.json" ]; then
        FILTER_CONF="$SCRIPT_DIR/filter.json"
    fi
    if [ -n "$FILTER_CONF" ]; then
        parse_filter_json "$FILTER_CONF"
    fi

    [ -d "$OUTPUT_DIR" ] || die "출력 디렉토리 없음: $OUTPUT_DIR"

    trap cleanup INT TERM
    start_logger
}

main "$@"
