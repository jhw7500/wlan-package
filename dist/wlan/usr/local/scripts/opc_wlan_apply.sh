#!/bin/sh
# opc_wlan_apply.sh — OPC 무선설정 적용 (wpa_supplicant conf 직접편집 + reconfigure)
#
# 사용: opc_wlan_apply.sh <iface> [--netid N] [freq "<mhz ...>"] [ssid <name>]
#   --netid N : (하위호환 인자) 단일 network 블록 전제 — 현재는 모든 블록에 적용하므로 무시.
#   freq/ssid : 하나 이상 지정.
#
# 책임: wpa_supplicant conf 파일을 직접 수정(ssid/scan_freq/freq_list)하고
#       `wpa_cli reconfigure` 로 프로세스 종료 없이 동적 적용 + 영속을 한 번에 처리.
#       재연결 성공 확인/롤백은 안 함 (결과는 WlanStatusChange indication 이 통지).
#
# 왜 save_config 가 아니라 conf 직접편집인가:
#   wpa_supplicant v2.10 의 save_config 는 freq_list 를 직렬화하지 않아(verified
#   on-target) 영속이 깨지고, update_config=1 conf 를 통째로 재생성하며 주석/포맷도
#   손실된다. conf 를 우리가 직접 쓰면 freq_list/scan_freq/ssid 가 그대로 영속되고,
#   reconfigure 가 conf 를 재로드하면서 freq_list(하드 밴드 락)까지 즉시 반영된다.
#   (set_network 런타임 변경은 reconfigure 시 conf 값으로 덮어써지므로 쓰지 않는다.)
#
# 적용 트리거: wpa_cli reconfigure — 전체 conf 재로드라 재연결(끊김)이 발생할 수 있다.
#   끊김이 문제가 되면 freq_list 를 런타임 전용(set_network)으로 관리하는 방식으로
#   후속 전환을 검토한다.
#
# exit: 0=ok / 2=usage / 3=ctrl_interface 부재 / 4=conf 편집 실패(awk ENVIRON 미지원 포함) / 5=reconfigure 실패
set -u

IFACE="${1:-}"
[ -n "$IFACE" ] || { echo "usage: $0 <iface> [--netid N] [freq \"<mhz ...>\"] [ssid <name>]" >&2; exit 2; }
shift

FREQS=""
SSID=""
HAVE_SSID=0
while [ $# -gt 0 ]; do
    case "$1" in
        --netid) [ $# -gt 1 ] || { echo "opc_wlan_apply: --netid requires an argument" >&2; exit 2; }
                 shift 2 ;;   # 단일 network 블록 전제 — netid 무시(모든 블록에 적용)
        freq)    [ $# -gt 1 ] || { echo "opc_wlan_apply: freq requires an argument" >&2; exit 2; }
                 FREQS="$2"; shift 2 ;;
        ssid)    [ $# -gt 1 ] || { echo "opc_wlan_apply: ssid requires an argument" >&2; exit 2; }
                 SSID="$2"; HAVE_SSID=1; shift 2 ;;
        *)       echo "opc_wlan_apply: unknown arg '$1'" >&2; exit 2 ;;
    esac
done
[ -n "$FREQS" ] || [ "$HAVE_SSID" = 1 ] || { echo "opc_wlan_apply: nothing to apply (need freq and/or ssid)" >&2; exit 2; }

CONF="${WPA_CONF_DIR:-/etc/wpa_supplicant}/wpa_supplicant-${IFACE}.conf"
[ -f "$CONF" ] || { echo "opc_wlan_apply: conf not found: $CONF" >&2; exit 4; }

wcli() { wpa_cli -i "$IFACE" "$@"; }

# ctrl interface 가용 확인 (wpa_supplicant 미동작이면 reconfigure 불가 → 3).
wcli ping >/dev/null 2>&1 || { echo "opc_wlan_apply: wpa_cli ctrl unavailable for $IFACE" >&2; exit 3; }

# --- conf 직접 편집 (atomic: 같은 fs 임시파일 → chmod → rename, 원본은 롤백용 백업) -
# freq → 모든 network={} 블록에 scan_freq=/freq_list= 설정(단일 블록 전제).
# ssid → network 블록의 ssid= 치환(없으면 블록 끝에 추가). 한 awk 패스로 처리.
# SSID 의 \ 와 " 는 wpa_supplicant conf 문법(C-style escape)에 맞게 이스케이프하여
# conf 라인 인젝션/따옴표 조기종료를 막는다(신뢰 불가 입력 대비).
DO_FREQ=0; [ -n "$FREQS" ] && DO_FREQ=1
# FREQS 는 숫자/공백만 허용 — 직접 호출 시 awk -v 로 들어가는 값에 개행 등이 섞여
# conf 라인이 인젝션되는 것을 차단(데몬 경로는 정수만 전달하나 방어적으로 검증).
case "$FREQS" in *[!0-9\ ]*) echo "opc_wlan_apply: invalid freq '$FREQS' (digits/spaces only)" >&2; exit 2 ;; esac
# busybox awk 가 ENVIRON 을 미지원하면 아래 SSID 전달이 "" 로 조용히 덮어써져 conf 의
# ssid 가 빈 값으로 손상된다(awk 는 exit 0 → opcd 는 성공 오인하는 silent failure).
# SSID 적용 시에만 ENVIRON 지원을 사전 검증하고, 미지원이면 편집 전에 비-0(exit 4)로
# 실패를 알린다. (freq 경로는 -v 전달이라 ENVIRON 과 무관 — 밴드락은 영향받지 않는다.)
if [ "$HAVE_SSID" = 1 ]; then
    OPC_ENVIRON_PROBE=ok awk 'BEGIN { exit(ENVIRON["OPC_ENVIRON_PROBE"] == "ok" ? 0 : 1) }' </dev/null \
        || { echo "opc_wlan_apply: awk lacks ENVIRON support — cannot apply ssid safely" >&2; exit 4; }
fi
# trap 을 mktemp 보다 먼저 등록 — 임시파일 생성과 trap 등록 사이에 시그널이 와도
# 파일이 남지 않도록(누출 창 제거). 실패 경로는 exit 으로 trap 정리에 위임한다.
BAK=""; TMP=""
trap 'rm -f "$TMP" "$BAK"' EXIT
BAK="$(mktemp "${CONF}.bak.XXXXXX")" || { echo "opc_wlan_apply: mktemp(bak) failed" >&2; exit 4; }
cp -p "$CONF" "$BAK" || { echo "opc_wlan_apply: backup failed" >&2; exit 4; }
TMP="$(mktemp "${CONF}.XXXXXX")" || { echo "opc_wlan_apply: mktemp failed" >&2; exit 4; }

# SSID 는 ENVIRON 으로 전달한다 — awk -v 는 값의 \X 를 C-escape 로 해석해(예: \b→
# 백스페이스) 백슬래시가 든 SSID 를 손상시키므로, raw 보존되는 ENVIRON 을 쓴다.
# (ENVIRON 미지원 awk 는 위 probe 에서 걸러져 이 경로에 도달하지 않는다.)
OPC_SSID="$SSID" awk -v freqs="$FREQS" -v do_freq="$DO_FREQ" -v do_ssid="$HAVE_SSID" '
    function esc(s) { gsub(/\\/, "\\\\", s); gsub(/"/, "\\\"", s); return s }
    BEGIN { in_net = 0; blocks = 0; ssid_done = 0; new_ssid = ENVIRON["OPC_SSID"] }
    /^[[:space:]]*#/ { print; next }
    /^[[:space:]]*network[[:space:]]*=[[:space:]]*\{/ {
        in_net = 1; blocks++; done_scan = 0; done_list = 0; ssid_done = 0; print; next
    }
    in_net && /^[[:space:]]*\}/ {
        if (do_freq && !done_scan) { print "    scan_freq=" freqs; done_scan = 1 }
        if (do_freq && !done_list) { print "    freq_list=" freqs; done_list = 1 }
        if (do_ssid && !ssid_done)  { print "    ssid=\"" esc(new_ssid) "\""; ssid_done = 1 }
        in_net = 0; print; next
    }
    do_freq && in_net && /^[[:space:]]*scan_freq[[:space:]]*=/ {
        if (!done_scan) { print "    scan_freq=" freqs; done_scan = 1 } next
    }
    do_freq && in_net && /^[[:space:]]*freq_list[[:space:]]*=/ {
        if (!done_list) { print "    freq_list=" freqs; done_list = 1 } next
    }
    do_ssid && in_net && /^[[:space:]]*ssid[[:space:]]*=/ {
        if (!ssid_done) { print "    ssid=\"" esc(new_ssid) "\""; ssid_done = 1 } next
    }
    { print }
    END {
        if (blocks == 0) { print "error: no network={ block in " FILENAME > "/dev/stderr"; exit 1 }
        if (blocks > 1) { print "warn: " blocks " network blocks present — all modified (single-block assumed)" > "/dev/stderr" }
    }
' "$CONF" > "$TMP" || { echo "opc_wlan_apply: conf edit failed" >&2; exit 4; }

# 원본 conf 권한을 보존한다 — psk= 평문이 0644 로 월드리더블 노출되지 않도록.
# --reference 미지원 환경(busybox 등)은 0600 으로 폴백(노출 최소).
chmod --reference="$CONF" "$TMP" 2>/dev/null || chmod 0600 "$TMP" 2>/dev/null || true
mv -f "$TMP" "$CONF" || { echo "opc_wlan_apply: conf install failed" >&2; exit 4; }
# mv 로 TMP 소진. reconfigure 검증 전까지 BAK(롤백본)는 보존해야 하므로, 이 구간에
# 시그널이 와도 백업이 지워지지 않도록 trap 에서 BAK 제거를 뺀다.
trap 'rm -f "$TMP"' EXIT
sync 2>/dev/null || true

# --- 적용 트리거: 전체 conf 재로드 → freq_list/scan_freq/ssid 모두 반영 -----------
# reconfigure 실패 시 깨진 conf 가 영속되어 다음 reboot 기동을 막을 수 있으므로,
# 원본 백업으로 롤백한 뒤 재적용한다(무선 전체 다운 방지).
# wpa_cli 는 제어요청 전달만 성공하면 데몬 응답(OK/FAIL)과 무관하게 exit 0 이므로,
# exit code 가 아니라 출력이 "OK" 인지로 실패를 판정한다(wifi.sh 의 wpa_cli_ok 와 동일).
# reconfigure 는 재연결(끊김)을 유발 — wifi_checker 가 과도기를 '불안정'으로 오판해
# reassociate/restart 하지 않도록 grace flag 를 세운다(TTL 은 checker 의 RECONFIGURE_GRACE_SEC).
mkdir -p /run/wifi 2>/dev/null || true
: > "/run/wifi/${IFACE}.reconfigure-grace" 2>/dev/null || true
if [ "$(wcli reconfigure 2>/dev/null)" != "OK" ]; then
    mv -f "$BAK" "$CONF"; sync 2>/dev/null || true
    trap - EXIT   # TMP 는 위 mv 로 이미 소진(rename)됨 — 정리할 임시파일 없음
    wcli reconfigure >/dev/null 2>&1 || true
    echo "opc_wlan_apply: reconfigure failed for $IFACE — conf rolled back" >&2
    exit 5
fi
trap - EXIT
rm -f "$BAK"
sync 2>/dev/null || true

exit 0
