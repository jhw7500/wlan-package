#!/bin/bash
# wifi-capture.sh — NXP 88Q9098 channel specified sniffer mode 캡처 도구
set -euo pipefail

# --- 상수 ---
IFACE="mlan0"
MON_IFACE="rtap"
FILTER=7
BANDWIDTH=0
CHANNEL=""
BAND=""
REMOTE_HOST=""
OUTPUT_DIR="."
TCPDUMP_PID=""
PCAP_FILE=""
MOAL_VERSION=""
NETMON_FAMILY=""
NETMON_BAND=""

# --- 유틸리티 ---
die()  { echo "ERROR: $*" >&2; exit 1; }
warn() { echo "WARN: $*" >&2; }
info() { echo "INFO: $*"; }

usage() {
    cat <<'EOF'
Usage: wifi-capture.sh [OPTIONS]

NXP 88Q9098 channel specified sniffer mode를 이용한 WiFi 패킷 캡처 도구.
STA 연결을 해제하고 지정된 채널에서 모든 802.11 프레임을 캡처합니다.

Options:
  -c <channel>     모니터 채널 (미지정 시 현재 연결 채널 자동감지)
  -b <band>        밴드: B,G,A,GN,AN,GAC,AAC 또는 옛 비트맵 숫자 (미지정 시 자동감지)
  -f <filter>      filter_flag 비트맵 (기본값: 7 = 관리+제어+데이터)
  -w <bandwidth>   채널 대역폭: 0=20MHz, 1=40MHz↑, 3=40MHz↓, 4=80MHz (기본값: 0)
  -i <interface>   mlan 인터페이스 (기본값: mlan0)
  -H <host>        원격 타겟 IP (SSH 키 기반)
  -o <directory>   pcap 저장 경로 (기본값: .)
  -h               도움말

Examples:
  wifi-capture.sh                          # 자동감지
  wifi-capture.sh -c 64 -b AN             # 5GHz 채널 64, 802.11n
  wifi-capture.sh -c 6 -b GN -f 7        # 2.4GHz 채널 6, 모든 프레임
  wifi-capture.sh -H 192.168.1.100 -c 64 -b AN  # 원격 실행

Filter flags:
  bit 0: 관리 프레임 (Beacon, Probe, Auth, Assoc 등)
  bit 1: 제어 프레임 (ACK, RTS/CTS 등)
  bit 2: 데이터 프레임 (EAPOL, IP 패킷 등)

Band values (옛 비트맵, OR 조합 가능):
  B=1, G=2, A=4, GN=8, AN=16, GAC=32, AAC=64
  2.4GHz 권장: 11 (B+G+GN)
  5GHz 권장: 20 (A+AN)
  VHT(AC)는 -w 4 옵션으로 지정

드라이버가 받는 netmon 밴드 값 (로드된 moal 버전으로 정함):
  505.* (i.MX8)         : 위 비트맵을 그대로 넘긴다
  543.*, 552.* (i.MX93) : 2.4GHz 비트만 있으면 0, 5GHz 비트만 있으면 1 (섞이면 거부)
  그 밖의 버전          : NETMON_BAND_MODE=bitmap 또는 enum 환경변수로 지정
EOF
    exit 0
}

# --- 전제 조건 검사 ---

check_prerequisites() {
    [ "$(id -u)" -eq 0 ] || die "root 권한이 필요합니다"
    for cmd in mlanutl wpa_cli tcpdump; do
        command -v "$cmd" >/dev/null 2>&1 || die "'$cmd'이(가) 설치되어 있지 않습니다"
    done
}

cleanup_existing() {
    # netmon 비활성화 시 rtap0도 자동 제거됨
    mlanutl "$IFACE" netmon 0 2>/dev/null || true
}

# --- 밴드 매핑 ---

band_to_bitmap() {
    local band="$1"
    case "$band" in
        [0-9]*) echo "$band"; return ;;
    esac
    case "$band" in
        B)   echo 1 ;;
        G)   echo 2 ;;
        A)   echo 4 ;;
        GN)  echo 8 ;;
        AN)  echo 16 ;;
        GAC) echo 32 ;;
        AAC) echo 64 ;;
        *)   die "알 수 없는 밴드: $band (B,G,A,GN,AN,GAC,AAC 또는 숫자)" ;;
    esac
}

bitmap_to_label() {
    local val="$1"
    case "$val" in
        1)  echo "B" ;;
        2)  echo "G" ;;
        4)  echo "A" ;;
        8)  echo "GN" ;;
        16) echo "AN" ;;
        32) echo "GAC" ;;
        64) echo "AAC" ;;
        *)  echo "$val" ;;
    esac
}

# --- netmon 밴드 형식 (#456) ---
# netmon 의 밴드 인자는 드라이버마다 뜻이 다르다(드라이버 원문 기준).
#   bitmap: moal 505.* (i.MX8 payload) — 위 옛 비트맵을 그대로 받는다. 밴드 값을 사실상 검사하지
#           않아 틀린 값도 받아들이므로, 넣어 보고 실패하면 바꾸는 식으로는 형식을 가를 수 없다.
#   enum:   moal 543.* (i.MX93 payload) — 0=2.4GHz, 1=5GHz, 2=6GHz 만 받고 나머지는 -EINVAL.
#           moal 552.* 도 같다(i.MX93 보드 실측 2026-11-07: 1·0 으로 캡처, 옛 값 20 은 Invalid band).
# 형식은 로드된 모듈 버전으로 정하고, 표에 없는 버전은 추측하지 않는다.
# NETMON_BAND_MODE=bitmap|enum 을 주면 버전 표 대신 그 형식을 쓴다.

check_band_mode_env() {
    case "${NETMON_BAND_MODE:-}" in
        ""|bitmap|enum) ;;
        *) die "NETMON_BAND_MODE=$NETMON_BAND_MODE 은(는) 지원하지 않습니다 (bitmap 또는 enum)" ;;
    esac
}

detect_netmon_family() {
    local vfile="${WIFI_SYS_MODULE_ROOT:-/sys/module}/moal/version"
    check_band_mode_env
    MOAL_VERSION=""
    if [ -r "$vfile" ]; then
        IFS= read -r MOAL_VERSION < "$vfile" || true
    fi
    if [ -n "${NETMON_BAND_MODE:-}" ]; then
        NETMON_FAMILY="$NETMON_BAND_MODE"
        return 0
    fi
    case "$MOAL_VERSION" in
        505.*) NETMON_FAMILY=bitmap ;;
        543.*|552.*) NETMON_FAMILY=enum ;;
        "") die "moal 드라이버 버전을 읽지 못했습니다($vfile). 드라이버가 로드됐는지 확인하거나 NETMON_BAND_MODE=bitmap|enum 으로 netmon 밴드 형식을 지정하세요" ;;
        *) die "netmon 밴드 형식을 모르는 moal 버전입니다: $MOAL_VERSION. NETMON_BAND_MODE=bitmap(옛 비트맵) 또는 enum(0=2.4GHz, 1=5GHz)으로 지정하세요" ;;
    esac
}

# 옛 비트맵 밴드 값을 드라이버가 받는 netmon 밴드 값으로 바꾼다.
# enum 형식: 2.4GHz 비트(B,G,GN,GAC)만 있으면 0, 5GHz 비트(A,AN,AAC)만 있으면 1.
netmon_band_arg() {
    local bm="$1"
    if [ "$NETMON_FAMILY" != enum ]; then
        echo "$bm"
        return 0
    fi
    case "$bm" in
        ""|*[!0-9]*) die "밴드 값 $bm 은(는) 숫자가 아닙니다" ;;
    esac
    local v=$((10#$bm))
    local g24=$((v & 43)) g5=$((v & 84)) extra=$((v & ~127))
    if [ "$extra" -eq 0 ] && [ "$g24" -ne 0 ] && [ "$g5" -eq 0 ]; then
        echo 0
    elif [ "$extra" -eq 0 ] && [ "$g5" -ne 0 ] && [ "$g24" -eq 0 ]; then
        echo 1
    else
        die "밴드 값 $bm 은(는) moal ${MOAL_VERSION:-?} 의 netmon 밴드(0=2.4GHz, 1=5GHz)로 바꿀 수 없습니다. 2.4GHz(B,G,GN) 또는 5GHz(A,AN,AAC) 한쪽만 지정하세요"
    fi
}

# --- 자동감지 ---

auto_detect() {
    info "현재 STA 연결에서 채널/밴드 자동감지 중..."

    # `iw dev link` 는 moal 에서 연결 중에도 "Not connected." 를 반환할 수 있어(2026-07-29
    # 실측) 멀쩡한 연결을 미연결로 오탐지한다. wlan_link_lib 로 판정·조회한다.
    # 단 이 스크립트는 **단독 배포**를 지원한다(README 는 wifi-capture.sh/wifi-decrypt.sh 만
    # scp 하고, -H 원격 실행도 /tmp 로 이 파일 하나만 전송한다). lib 이 없을 수 있으므로
    # 없으면 최소 구현으로 대체해 자체 완결성을 유지한다.
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

    # 아래 VHT/대역폭 자동 감지가 파싱할 원문. 종전에는 `iw link` 출력을 썼는데,
    # station dump 는 "tx bitrate: 780.0 MBit/s VHT-MCS 9 80MHz VHT-NSS 2" 처럼 VHT 표기와
    # 대역폭을 함께 담아 같은 정보를 더 풍부하게 준다.
    local status
    status=$(iw dev "$IFACE" station dump 2>/dev/null)

    # 주파수 추출
    local freq
    freq=$(wlan_freq_mhz "$IFACE")
    [ -n "$freq" ] || die "주파수를 읽을 수 없습니다"

    # 주파수 → 채널
    if [ -z "$CHANNEL" ]; then
        if [ "$freq" -lt 3000 ]; then
            CHANNEL=$(( (freq - 2407) / 5 ))
        else
            CHANNEL=$(( (freq - 5000) / 5 ))
        fi
    fi

    # 밴드 결정
    # mlanutl netmon에서 밴드는 B/G/A/GN/AN 조합만 사용
    # VHT(AC)는 밴드가 아니라 -w 옵션(bandwidth)으로 지정
    if [ -z "$BAND" ]; then
        if [ "$freq" -lt 3000 ]; then
            BAND=11  # B+G+GN (2.4GHz 통합)
        else
            BAND=20  # A+AN (5GHz 통합)
        fi
    fi

    # VHT 감지 시 대역폭도 자동 설정
    if [ "$BANDWIDTH" -eq 0 ]; then
        local vht_cap bw_str
        vht_cap=$(echo "$status" | grep -ci "VHT" || true)
        bw_str=$(echo "$status" | grep -oP '\d+MHz' | head -1 || true)

        if echo "$bw_str" | grep -q "80"; then
            BANDWIDTH=4
        elif echo "$bw_str" | grep -q "40"; then
            # secondary channel 방향 결정
            if echo "$status" | grep -qi "above\|+"; then
                BANDWIDTH=1
            else
                BANDWIDTH=3
            fi
        fi
    fi

    info "감지됨: 채널=$CHANNEL, 밴드=$(bitmap_to_label "$BAND") ($BAND), 주파수=${freq}MHz, 대역폭=$BANDWIDTH"
}

# --- Cleanup ---

cleanup() {
    echo ""
    info "캡처 종료 중..."

    # 1. tcpdump 종료
    if [ -n "$TCPDUMP_PID" ] && kill -0 "$TCPDUMP_PID" 2>/dev/null; then
        kill "$TCPDUMP_PID" 2>/dev/null || true
        wait "$TCPDUMP_PID" 2>/dev/null || true
    fi

    # 2. netmon 비활성화 (rtap0도 자동 제거됨)
    mlanutl "$IFACE" netmon 0 2>/dev/null || true

    # 4. 결과 요약
    if [ -n "$PCAP_FILE" ] && [ -f "$PCAP_FILE" ]; then
        local size packets
        size=$(du -h "$PCAP_FILE" | cut -f1)
        packets=$(tcpdump -r "$PCAP_FILE" 2>/dev/null | wc -l || echo "?")
        echo ""
        info "=== 캡처 결과 ==="
        info "파일: $PCAP_FILE"
        info "크기: $size"
        info "패킷: $packets"
    fi

    exit 0
}

# --- 캡처 ---

start_capture() {
    # STA 연결 해제
    info "STA 연결 해제 중..."
    wpa_cli -i "$IFACE" disconnect >/dev/null 2>&1 || true
    sleep 1

    # netmon 활성화
    info "netmon 활성화: 채널=$CHANNEL, 밴드=$NETMON_BAND, 필터=$FILTER, 대역폭=$BANDWIDTH (moal ${MOAL_VERSION:-?}, 밴드 형식 $NETMON_FAMILY)"
    if ! mlanutl "$IFACE" netmon 1 "$FILTER" "$NETMON_BAND" "$CHANNEL" "$BANDWIDTH"; then
        die "netmon 활성화 실패: mlanutl $IFACE netmon 1 $FILTER $NETMON_BAND $CHANNEL $BANDWIDTH (밴드=$NETMON_BAND, 채널=$CHANNEL, 대역폭=$BANDWIDTH, moal ${MOAL_VERSION:-?}, 밴드 형식 $NETMON_FAMILY)"
    fi

    # rtap0은 netmon 활성화 시 드라이버가 자동 생성
    # UP 대기
    local retry=0
    while ! ip link show "$MON_IFACE" >/dev/null 2>&1; do
        retry=$((retry + 1))
        [ "$retry" -gt 10 ] && die "$MON_IFACE 인터페이스가 생성되지 않았습니다"
        sleep 0.5
    done
    ip link set "$MON_IFACE" up || die "$MON_IFACE UP 실패"
    info "모니터 인터페이스 준비: $MON_IFACE"

    # 파일명 생성
    local band_label timestamp
    band_label=$(bitmap_to_label "$BAND")
    timestamp=$(date +%Y%m%d_%H%M%S)
    PCAP_FILE="${OUTPUT_DIR}/capture_ch${CHANNEL}_${band_label}_${timestamp}.pcap"

    # tcpdump 시작
    info "캡처 시작: $PCAP_FILE"
    info "종료하려면 Ctrl+C를 누르세요"
    echo ""
    tcpdump -i "$MON_IFACE" -w "$PCAP_FILE" &
    TCPDUMP_PID=$!
    wait "$TCPDUMP_PID"
}

# --- 원격 실행 ---

exec_remote() {
    local host="$REMOTE_HOST"

    info "원격 실행: $host"

    # 스크립트 전송
    local script_path
    script_path=$(readlink -f "$0")
    scp -q "$script_path" "root@${host}:/tmp/wifi-capture.sh" || die "scp 실패: $host"

    # -H 제거한 인자 재구성
    local remote_args=()
    local skip_next=0
    for arg in "$@"; do
        if [ "$skip_next" -eq 1 ]; then
            skip_next=0
            continue
        fi
        if [ "$arg" = "-H" ]; then
            skip_next=1
            continue
        fi
        remote_args+=("$arg")
    done
    remote_args+=("-o" "/tmp")

    # NETMON_BAND_MODE(선택)는 타겟의 버전 판별을 대신하므로 함께 넘긴다(값은 main 에서 검사함)
    local env_prefix=""
    if [ -n "${NETMON_BAND_MODE:-}" ]; then
        env_prefix="NETMON_BAND_MODE=$NETMON_BAND_MODE "
    fi

    # SSH로 실행 (-t로 Ctrl+C 전파)
    ssh -t "root@${host}" "${env_prefix}/tmp/wifi-capture.sh ${remote_args[*]}"
    local exit_code=$?

    # pcap 파일 복사
    info "pcap 파일 복사 중..."
    scp -q "root@${host}:/tmp/capture_*.pcap" "${OUTPUT_DIR}/" 2>/dev/null || warn "pcap 파일 복사 실패 (파일 없음?)"

    # 원격 임시 파일 정리
    ssh "root@${host}" "rm -f /tmp/wifi-capture.sh /tmp/capture_*.pcap" 2>/dev/null || true

    exit "$exit_code"
}

# --- 인자 파싱 + main ---

parse_args() {
    while getopts "c:b:f:w:i:H:o:h" opt; do
        case "$opt" in
            c) CHANNEL="$OPTARG" ;;
            b) BAND=$(band_to_bitmap "$OPTARG") ;;
            f) FILTER="$OPTARG" ;;
            w) BANDWIDTH="$OPTARG" ;;
            i) IFACE="$OPTARG" ;;
            H) REMOTE_HOST="$OPTARG" ;;
            o) OUTPUT_DIR="$OPTARG" ;;
            h) usage ;;
            *) usage ;;
        esac
    done
}

main() {
    parse_args "$@"
    # 원격 실행에도 넘기므로 분기 전에 검사한다
    check_band_mode_env

    # 원격 실행 분기 (netmon 밴드 형식은 타겟에서 판별한다)
    if [ -n "$REMOTE_HOST" ]; then
        exec_remote "$@"
        return
    fi

    check_prerequisites
    # 드라이버를 건드리기 전에 netmon 밴드 형식부터 정한다(모르면 여기서 멈춘다)
    detect_netmon_family
    cleanup_existing

    # 자동감지 필요 시
    if [ -z "$CHANNEL" ] && [ -z "$BAND" ]; then
        auto_detect
    elif [ -n "$CHANNEL" ] && [ -z "$BAND" ]; then
        # 채널만 지정된 경우 밴드를 채널 번호에서 추론
        if [ "$CHANNEL" -le 14 ]; then
            BAND=11  # B+G+GN (2.4GHz)
            info "밴드 자동 추론: 채널 $CHANNEL → B+G+GN (11, 2.4GHz)"
        else
            BAND=20  # A+AN (5GHz)
            info "밴드 자동 추론: 채널 $CHANNEL → A+AN (20, 5GHz)"
        fi
    elif [ -z "$CHANNEL" ] && [ -n "$BAND" ]; then
        die "밴드(-b)만 지정됨. 채널(-c)도 지정하세요"
    fi

    # 드라이버가 받는 밴드 값으로 바꾼다(바꿀 수 없으면 STA 연결을 끊기 전에 멈춘다)
    NETMON_BAND=$(netmon_band_arg "$BAND") || exit 1

    # 출력 디렉토리 확인
    [ -d "$OUTPUT_DIR" ] || die "출력 디렉토리 없음: $OUTPUT_DIR"

    # trap 설정 후 캡처 시작
    trap cleanup INT TERM
    start_capture
}

main "$@"
