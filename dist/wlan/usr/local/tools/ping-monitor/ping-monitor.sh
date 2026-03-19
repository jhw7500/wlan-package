#!/bin/bash
# ping-monitor.sh — 유무선 브릿지 ICMP 지연/손실 분석 도구
# 클라이언트 모드에서 동작, 브릿지에 영향 없음
set -euo pipefail

# --- 설정 ---
IF_WLAN="mlan0"
IF_ETH="eth0"
OUTPUT_DIR="/tmp/ping-monitor"
DURATION=0          # 0 = Ctrl+C까지
TARGET_IP=""        # 특정 IP 필터 (빈 값 = 전체 ICMP)
REMOTE_HOST=""
TCPDUMP_PID_WLAN=""
TCPDUMP_PID_ETH=""
PCAP_WLAN=""
PCAP_ETH=""

# --- 유틸리티 ---
die()  { echo "ERROR: $*" >&2; exit 1; }
info() { echo "INFO: $*"; }
warn() { echo "WARN: $*" >&2; }

usage() {
    cat <<'EOF'
Usage: ping-monitor.sh [OPTIONS]

유무선 브릿지 ICMP 지연/손실 분석 도구.
eth0과 mlan0에서 동시에 ICMP 패킷을 캡처하여 브릿지 통과 지연과 손실을 분석합니다.
클라이언트 모드에서 동작하며 브릿지에 영향을 주지 않습니다.

Options:
  -t <ip>          특정 IP만 필터 (기본: 전체 ICMP)
  -d <seconds>     캡처 시간 (기본: 0 = Ctrl+C까지)
  -e <interface>   유선 인터페이스 (기본: eth0)
  -w <interface>   무선 인터페이스 (기본: mlan0)
  -o <directory>   출력 경로 (기본: /tmp/ping-monitor)
  -H <host>        원격 타겟 IP (SSH 키 기반)
  -h               도움말

Examples:
  ping-monitor.sh                              # 전체 ICMP 캡처
  ping-monitor.sh -t 192.168.1.1               # 특정 IP만
  ping-monitor.sh -t 192.168.1.1 -d 60         # 60초간 캡처
  ping-monitor.sh -H 10.0.0.100 -t 192.168.1.1 # 원격 실행

Output:
  캡처 종료 후 자동으로 분석 결과를 출력합니다:
  - 패킷 매칭 (ICMP seq 기반)
  - 브릿지 통과 지연 (eth→wlan, wlan→eth)
  - 손실 패킷 목록
  - 통계 (평균/최대/최소 지연, 지터, 손실률)
EOF
    exit 0
}

# --- 전제 조건 ---

check_prerequisites() {
    [ "$(id -u)" -eq 0 ] || die "root 권한이 필요합니다"
    for cmd in tcpdump tshark; do
        command -v "$cmd" >/dev/null 2>&1 || die "'$cmd'이(가) 설치되어 있지 않습니다"
    done
    ip link show "$IF_WLAN" >/dev/null 2>&1 || die "인터페이스 없음: $IF_WLAN"
    ip link show "$IF_ETH" >/dev/null 2>&1 || die "인터페이스 없음: $IF_ETH"
}

# --- 캡처 ---

start_capture() {
    mkdir -p "$OUTPUT_DIR"

    local timestamp
    timestamp=$(date +%Y%m%d_%H%M%S)
    PCAP_ETH="${OUTPUT_DIR}/icmp_${IF_ETH}_${timestamp}.pcap"
    PCAP_WLAN="${OUTPUT_DIR}/icmp_${IF_WLAN}_${timestamp}.pcap"

    local bpf="icmp"
    if [ -n "$TARGET_IP" ]; then
        bpf="icmp and host $TARGET_IP"
    fi

    info "캡처 시작: $IF_ETH + $IF_WLAN"
    [ -n "$TARGET_IP" ] && info "필터: $TARGET_IP"
    [ "$DURATION" -gt 0 ] && info "시간: ${DURATION}초"
    info "종료: Ctrl+C"
    echo ""

    # 마이크로초 타임스탬프로 캡처 (--time-stamp-precision micro)
    tcpdump -i "$IF_ETH" -n -tt --time-stamp-precision=micro \
        -w "$PCAP_ETH" "$bpf" 2>/dev/null &
    TCPDUMP_PID_ETH=$!

    tcpdump -i "$IF_WLAN" -n -tt --time-stamp-precision=micro \
        -w "$PCAP_WLAN" "$bpf" 2>/dev/null &
    TCPDUMP_PID_WLAN=$!

    if [ "$DURATION" -gt 0 ]; then
        sleep "$DURATION"
        cleanup
    else
        wait "$TCPDUMP_PID_ETH" 2>/dev/null || true
    fi
}

# --- 분석 ---

analyze() {
    local pcap_eth="$1"
    local pcap_wlan="$2"

    # pcap 파일 존재 확인
    [ -f "$pcap_eth" ] || { info "eth pcap 없음, 분석 건너뜀"; return; }
    [ -f "$pcap_wlan" ] || { info "wlan pcap 없음, 분석 건너뜀"; return; }

    local eth_count wlan_count
    eth_count=$(tshark -r "$pcap_eth" 2>/dev/null | wc -l)
    wlan_count=$(tshark -r "$pcap_wlan" 2>/dev/null | wc -l)

    if [ "$eth_count" -eq 0 ] && [ "$wlan_count" -eq 0 ]; then
        info "캡처된 ICMP 패킷이 없습니다"
        return
    fi

    info "분석 중... (eth=$eth_count, wlan=$wlan_count 패킷)"
    echo ""

    # tshark로 패킷 추출: 시간, src, dst, type, id, seq, data_len
    local eth_csv="${OUTPUT_DIR}/.eth_packets.csv"
    local wlan_csv="${OUTPUT_DIR}/.wlan_packets.csv"

    tshark -r "$pcap_eth" -n -T fields \
        -e frame.time_epoch -e ip.src -e ip.dst \
        -e icmp.type -e icmp.ident -e icmp.seq -e frame.len \
        -E separator=, 2>/dev/null > "$eth_csv"

    tshark -r "$pcap_wlan" -n -T fields \
        -e frame.time_epoch -e ip.src -e ip.dst \
        -e icmp.type -e icmp.ident -e icmp.seq -e frame.len \
        -E separator=, 2>/dev/null > "$wlan_csv"

    # awk로 매칭 및 분석
    awk -F, -v eth_file="$eth_csv" -v wlan_file="$wlan_csv" \
        -v if_eth="$IF_ETH" -v if_wlan="$IF_WLAN" '
    BEGIN {
        # eth 패킷 로드
        eth_n = 0
        while ((getline line < eth_file) > 0) {
            eth_n++
            split(line, f, ",")
            eth_time[eth_n]  = f[1]
            eth_src[eth_n]   = f[2]
            eth_dst[eth_n]   = f[3]
            eth_type[eth_n]  = f[4]
            eth_id[eth_n]    = f[5]
            eth_seq[eth_n]   = f[6]
            eth_len[eth_n]   = f[7]
            # 키: type_id_seq
            key = f[4] "_" f[5] "_" f[6]
            eth_key_time[key] = f[1]
            eth_key_idx[key]  = eth_n
        }
        close(eth_file)

        # wlan 패킷 로드
        wlan_n = 0
        while ((getline line < wlan_file) > 0) {
            wlan_n++
            split(line, f, ",")
            wlan_time[wlan_n]  = f[1]
            wlan_src[wlan_n]   = f[2]
            wlan_dst[wlan_n]   = f[3]
            wlan_type[wlan_n]  = f[4]
            wlan_id[wlan_n]    = f[5]
            wlan_seq[wlan_n]   = f[6]
            wlan_len[wlan_n]   = f[7]
            key = f[4] "_" f[5] "_" f[6]
            wlan_key_time[key] = f[1]
            wlan_key_idx[key]  = wlan_n
        }
        close(wlan_file)

        # 매칭 및 지연 계산
        matched = 0
        eth_only = 0
        wlan_only = 0
        sum_delay = 0
        max_delay = 0
        min_delay = 999999
        prev_delay = -1
        sum_jitter = 0
        jitter_n = 0

        printf "=== ICMP 패킷 매칭 결과 ===\n"
        printf "%-6s  %-8s  %-15s → %-15s  %-5s  %-6s  %-12s  %-12s  %s\n", \
            "Type", "ID/Seq", "Src", "Dst", "Len", "Seq", if_eth, if_wlan, "Delay(ms)"
        printf "%-6s  %-8s  %-15s   %-15s  %-5s  %-6s  %-12s  %-12s  %s\n", \
            "------", "--------", "---------------", "---------------", "-----", "------", "------------", "------------", "---------"

        # eth 기준 매칭
        for (i = 1; i <= eth_n; i++) {
            key = eth_type[i] "_" eth_id[i] "_" eth_seq[i]
            type_str = (eth_type[i] == 8) ? "Echo" : (eth_type[i] == 0) ? "Reply" : eth_type[i]
            id_seq = eth_id[i] "/" eth_seq[i]

            if (key in wlan_key_time) {
                matched++
                et = eth_time[i] + 0
                wt = wlan_key_time[key] + 0
                delay_us = (wt - et) * 1000000
                if (delay_us < 0) delay_us = -delay_us
                delay_ms = delay_us / 1000
                dir = (wt > et) ? "→" : "←"

                sum_delay += delay_ms
                if (delay_ms > max_delay) max_delay = delay_ms
                if (delay_ms < min_delay) min_delay = delay_ms

                if (prev_delay >= 0) {
                    j = delay_ms - prev_delay
                    if (j < 0) j = -j
                    sum_jitter += j
                    jitter_n++
                }
                prev_delay = delay_ms

                printf "%-6s  %-8s  %-15s → %-15s  %-5s  %-6s  %12.6f  %12.6f  %7.3f %s\n", \
                    type_str, id_seq, eth_src[i], eth_dst[i], eth_len[i], eth_seq[i], \
                    et, wt, delay_ms, dir
                eth_matched[key] = 1
            }
        }

        # eth에만 있는 패킷
        for (i = 1; i <= eth_n; i++) {
            key = eth_type[i] "_" eth_id[i] "_" eth_seq[i]
            if (!(key in wlan_key_time)) {
                eth_only++
                type_str = (eth_type[i] == 8) ? "Echo" : (eth_type[i] == 0) ? "Reply" : eth_type[i]
            }
        }

        # wlan에만 있는 패킷
        for (i = 1; i <= wlan_n; i++) {
            key = wlan_type[i] "_" wlan_id[i] "_" wlan_seq[i]
            if (!(key in eth_key_time)) {
                wlan_only++
            }
        }

        # 통계
        printf "\n=== 통계 ===\n"
        printf "캡처: %s=%d, %s=%d 패킷\n", if_eth, eth_n, if_wlan, wlan_n
        printf "매칭: %d 패킷\n", matched
        printf "손실: %s에만=%d, %s에만=%d\n", if_eth, eth_only, if_wlan, wlan_only

        total = eth_n
        if (wlan_n > total) total = wlan_n
        if (total > 0) {
            loss = eth_only + wlan_only
            loss_pct = (loss / (matched + loss)) * 100
            printf "손실률: %.1f%%\n", loss_pct
        }

        if (matched > 0) {
            avg = sum_delay / matched
            printf "\n=== 브릿지 통과 지연 ===\n"
            printf "평균: %.3f ms\n", avg
            printf "최소: %.3f ms\n", min_delay
            printf "최대: %.3f ms\n", max_delay
            if (jitter_n > 0)
                printf "지터: %.3f ms (평균 연속 편차)\n", sum_jitter / jitter_n
        }

        # 손실 패킷 상세
        if (eth_only > 0 || wlan_only > 0) {
            printf "\n=== 손실 패킷 상세 ===\n"
            for (i = 1; i <= eth_n; i++) {
                key = eth_type[i] "_" eth_id[i] "_" eth_seq[i]
                if (!(key in wlan_key_time)) {
                    type_str = (eth_type[i] == 8) ? "Echo" : (eth_type[i] == 0) ? "Reply" : eth_type[i]
                    printf "  [%s에서 사라짐] %s id=%s seq=%s %s→%s t=%s\n", \
                        if_wlan, type_str, eth_id[i], eth_seq[i], eth_src[i], eth_dst[i], eth_time[i]
                }
            }
            for (i = 1; i <= wlan_n; i++) {
                key = wlan_type[i] "_" wlan_id[i] "_" wlan_seq[i]
                if (!(key in eth_key_time)) {
                    type_str = (wlan_type[i] == 8) ? "Echo" : (wlan_type[i] == 0) ? "Reply" : wlan_type[i]
                    printf "  [%s에서 사라짐] %s id=%s seq=%s %s→%s t=%s\n", \
                        if_eth, type_str, wlan_id[i], wlan_seq[i], wlan_src[i], wlan_dst[i], wlan_time[i]
                }
            }
        }
    }' /dev/null

    # 임시 파일 정리
    rm -f "$eth_csv" "$wlan_csv"

    # 결과 파일 안내
    echo ""
    info "pcap 파일:"
    info "  $pcap_eth ($(du -h "$pcap_eth" | cut -f1))"
    info "  $pcap_wlan ($(du -h "$pcap_wlan" | cut -f1))"
    info "추가 분석: tshark -r $pcap_eth / tshark -r $pcap_wlan"
}

# --- 원격 실행 ---

exec_remote() {
    local host="$REMOTE_HOST"
    info "원격 실행: $host"

    local script_path
    script_path=$(readlink -f "$0")
    scp -q "$script_path" "root@${host}:/tmp/ping-monitor.sh" || die "scp 실패"

    # -H 제거한 인자 재구성
    local remote_args=()
    local skip_next=0
    for arg in "$@"; do
        if [ "$skip_next" -eq 1 ]; then skip_next=0; continue; fi
        if [ "$arg" = "-H" ]; then skip_next=1; continue; fi
        remote_args+=("$arg")
    done

    ssh -t "root@${host}" bash /tmp/ping-monitor.sh "${remote_args[@]}"
    local exit_code=$?

    # pcap 복사
    info "pcap 파일 복사 중..."
    mkdir -p "$OUTPUT_DIR"
    scp -q "root@${host}:${OUTPUT_DIR}/icmp_*.pcap" "${OUTPUT_DIR}/" 2>/dev/null || warn "pcap 복사 실패"
    ssh "root@${host}" "rm -rf /tmp/ping-monitor.sh ${OUTPUT_DIR}" 2>/dev/null || true

    exit "$exit_code"
}

# --- Cleanup ---

cleanup() {
    echo ""
    info "캡처 종료..."

    for pid_var in TCPDUMP_PID_ETH TCPDUMP_PID_WLAN; do
        local pid="${!pid_var}"
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            wait "$pid" 2>/dev/null || true
        fi
    done

    # 약간 대기 (pcap 플러시)
    sleep 0.5

    # 분석 실행
    if [ -n "$PCAP_ETH" ] && [ -n "$PCAP_WLAN" ]; then
        analyze "$PCAP_ETH" "$PCAP_WLAN"
    fi

    exit 0
}

# --- main ---

parse_args() {
    while getopts "t:d:e:w:o:H:h" opt; do
        case "$opt" in
            t) TARGET_IP="$OPTARG" ;;
            d) DURATION="$OPTARG" ;;
            e) IF_ETH="$OPTARG" ;;
            w) IF_WLAN="$OPTARG" ;;
            o) OUTPUT_DIR="$OPTARG" ;;
            H) REMOTE_HOST="$OPTARG" ;;
            h) usage ;;
            *) usage ;;
        esac
    done
}

main() {
    parse_args "$@"

    if [ -n "$REMOTE_HOST" ]; then
        exec_remote "$@"
        return
    fi

    check_prerequisites
    trap cleanup INT TERM

    start_capture
}

main "$@"
