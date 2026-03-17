#!/bin/bash
# wifi-decrypt.sh — pcap 복호화 + 요약 도구
set -euo pipefail

# --- 상수 ---
SSID=""
PSK=""
CONF_FILE="$HOME/.wifi-sniffer.conf"
INPUT_FILE=""
OUTPUT_FILE=""
DECRYPT_OPTS=()  # tshark 복호화 옵션 (전역, 요약에서도 재사용)

# --- 유틸리티 ---
die()  { echo "ERROR: $*" >&2; exit 1; }
warn() { echo "WARN: $*" >&2; }
info() { echo "INFO: $*"; }

usage() {
    cat <<'EOF'
Usage: wifi-decrypt.sh [OPTIONS] <pcap-file>

WiFi pcap 파일을 WPA2-PSK로 복호화하고 요약 통계를 출력합니다.
복호화에는 EAPOL 4-Way Handshake가 pcap에 포함되어 있어야 합니다.

Options:
  -s <ssid>    WiFi SSID
  -p <psk>     WiFi PSK (비밀번호)
  -h           도움말

Credentials: CLI 인자 우선, 미지정 시 ~/.wifi-sniffer.conf 참조

Examples:
  wifi-decrypt.sh -s MyWiFi -p mypass123 capture.pcap
  wifi-decrypt.sh capture.pcap                          # ~/.wifi-sniffer.conf 사용
EOF
    exit 0
}

# --- 전제 조건 ---

check_prerequisites() {
    command -v tshark >/dev/null 2>&1 || die "'tshark'이(가) 설치되어 있지 않습니다"
    [ -n "$INPUT_FILE" ] || die "pcap 파일을 지정하세요"
    [ -f "$INPUT_FILE" ] || die "파일 없음: $INPUT_FILE"
    [ -s "$INPUT_FILE" ] || die "빈 파일: $INPUT_FILE"
}

# --- 자격증명 ---

load_credentials() {
    # CLI 인자가 있으면 우선
    if [ -n "$SSID" ] && [ -n "$PSK" ]; then
        return
    fi

    # 설정파일 fallback
    if [ -f "$CONF_FILE" ]; then
        info "설정파일 로드: $CONF_FILE"
        # shellcheck source=/dev/null
        . "$CONF_FILE"
    fi

    [ -n "$SSID" ] || die "SSID가 지정되지 않았습니다 (-s 또는 ~/.wifi-sniffer.conf)"
    [ -n "$PSK" ]  || die "PSK가 지정되지 않았습니다 (-p 또는 ~/.wifi-sniffer.conf)"
}

# --- EAPOL 확인 ---

check_eapol() {
    local count
    count=$(tshark -r "$INPUT_FILE" -Y "eapol" 2>/dev/null | wc -l)
    if [ "$count" -eq 0 ]; then
        warn "EAPOL handshake가 없습니다 — 복호화가 제한될 수 있습니다"
    else
        info "EAPOL handshake 발견: ${count}개 패킷"
    fi
}

# --- 복호화 ---

build_decrypt_opts() {
    DECRYPT_OPTS=(
        -o "wlan.enable_decryption:TRUE"
        -o "uat:80211_keys:\"wpa-pwd\",\"${PSK}:${SSID}\""
    )
}

decrypt_pcap() {
    local basename
    basename="${INPUT_FILE%.pcap}"
    OUTPUT_FILE="${basename}_decrypted.pcap"

    info "복호화 중: $INPUT_FILE → $OUTPUT_FILE"
    # -w는 raw 프레임을 저장하므로, 복호화된 pcap도 키 포함하여 읽어야 함
    tshark -r "$INPUT_FILE" \
        "${DECRYPT_OPTS[@]}" \
        -w "$OUTPUT_FILE" 2>/dev/null

    [ -s "$OUTPUT_FILE" ] || die "복호화된 파일이 비어있습니다"
    info "복호화 완료: $OUTPUT_FILE"
}

# --- 요약 ---

print_summary() {
    echo ""
    info "=== 복호화 결과 요약 ==="

    # 총 패킷 수
    local total
    total=$(tshark -r "$OUTPUT_FILE" "${DECRYPT_OPTS[@]}" 2>/dev/null | wc -l)
    info "총 패킷: $total"

    # EAPOL 수
    local eapol
    eapol=$(tshark -r "$OUTPUT_FILE" "${DECRYPT_OPTS[@]}" -Y "eapol" 2>/dev/null | wc -l)
    info "EAPOL: $eapol"

    # 복호화된 패킷 (상위 프로토콜이 보이는 수)
    local decrypted
    decrypted=$(tshark -r "$OUTPUT_FILE" "${DECRYPT_OPTS[@]}" -Y "ip || ipv6 || arp" 2>/dev/null | wc -l)
    info "복호화된 패킷 (IP/ARP): $decrypted"

    if [ "$decrypted" -eq 0 ] && [ "$total" -gt 0 ]; then
        warn "복호화된 패킷이 0개입니다 — PSK가 올바른지 확인하세요"
    fi

    # 프로토콜 분포
    echo ""
    info "=== 프로토콜 분포 ==="
    tshark -r "$OUTPUT_FILE" "${DECRYPT_OPTS[@]}" -q -z io,phs 2>/dev/null
}

# --- 인자 파싱 + main ---

parse_args() {
    # pcap 파일은 옵션 앞이든 뒤든 위치 무관하게 처리
    local args=()
    for arg in "$@"; do
        if [ -z "$INPUT_FILE" ] && [[ "$arg" == *.pcap ]]; then
            INPUT_FILE="$arg"
        else
            args+=("$arg")
        fi
    done

    # 나머지 인자에서 옵션 파싱
    OPTIND=1
    while getopts "s:p:h" opt "${args[@]}"; do
        case "$opt" in
            s) SSID="$OPTARG" ;;
            p) PSK="$OPTARG" ;;
            h) usage ;;
            *) usage ;;
        esac
    done
}

main() {
    parse_args "$@"
    check_prerequisites
    load_credentials
    build_decrypt_opts
    check_eapol
    decrypt_pcap
    print_summary
}

main "$@"
