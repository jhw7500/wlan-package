#!/bin/bash
# PR #134 온타겟 검증 — wifi_roam 데몬 감독화 + 설정값 하한 강제
#
# 검증 항목
#   V1. Restart=always 실동작 — 데몬을 kill 해도 자동 복구되는가
#   V2. StartLimitIntervalSec=0 — 연속 크래시에도 systemd 가 포기하지 않는가
#   V3. 설정값 하한 — 0/음수를 넣으면 거부 로그 + 기본값 유지인가
#   V4. 정상값 무회귀 — 유효한 값은 그대로 반영되는가
#
# 안전성: 무선 연결(wpa_supplicant)은 건드리지 않는다. wifi_roam 은 로밍 판정 전용이라
# 재시작해도 링크가 끊기지 않는다. 원본을 백업하고 종료 시 항상 복원한다.
# cts-wlan 은 유선 폴백이 없으므로 드라이버 reload/factory_reset/dpkg 는 절대 하지 않는다.
set -u

DEV="${DEV:-root@192.168.0.100}"
IFACE="${IFACE:-mlan0}"
SRC_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_SRC="$SRC_ROOT/dist/wlan/etc/systemd/system/wifi_roam@.service"
PY_SRC="$SRC_ROOT/dist/wlan/usr/local/logger/wifi_roam.py"
STAMP="$(date +%Y%m%d_%H%M%S)"
REMOTE_BAK="/tmp/pr134_bak_$STAMP"

pass=0; fail=0
ok()   { echo "  [PASS] $*"; pass=$((pass+1)); }
ng()   { echo "  [FAIL] $*"; fail=$((fail+1)); }
step() { echo; echo "=== $* ==="; }

r() { ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=no "$DEV" "$@"; }

step "0. 접속 및 사전 상태"
if ! r true 2>/dev/null; then
    echo "  장치에 접속할 수 없습니다: $DEV"
    exit 2
fi
r "hostname; uptime | tr -s ' '"
BEFORE_ACTIVE=$(r "systemctl is-active wifi_roam@$IFACE" 2>/dev/null)
echo "  wifi_roam@$IFACE = $BEFORE_ACTIVE"
LINK_BEFORE=$(r "iw dev $IFACE link 2>/dev/null | head -1")
echo "  link: $LINK_BEFORE"

step "1. 백업"
r "mkdir -p $REMOTE_BAK && \
   cp -a /etc/systemd/system/wifi_roam@.service $REMOTE_BAK/ 2>/dev/null; \
   cp -a /usr/local/logger/wifi_roam.py $REMOTE_BAK/ 2>/dev/null; \
   cp -a /usr/local/etc/wifi_init_conf.json $REMOTE_BAK/ 2>/dev/null; ls $REMOTE_BAK"

restore() {
    step "복원"
    r "cp -a $REMOTE_BAK/wifi_roam@.service /etc/systemd/system/ 2>/dev/null; \
       cp -a $REMOTE_BAK/wifi_roam.py /usr/local/logger/ 2>/dev/null; \
       cp -a $REMOTE_BAK/wifi_init_conf.json /usr/local/etc/ 2>/dev/null; \
       systemctl daemon-reload; systemctl restart wifi_roam@$IFACE; \
       sleep 2; systemctl is-active wifi_roam@$IFACE"
    echo "  백업 보관: $DEV:$REMOTE_BAK"
}
trap restore EXIT

step "2. 배포"
scp -o StrictHostKeyChecking=no "$UNIT_SRC" "$DEV:/etc/systemd/system/wifi_roam@.service" >/dev/null || { echo "  scp 실패"; exit 1; }
scp -o StrictHostKeyChecking=no "$PY_SRC" "$DEV:/usr/local/logger/wifi_roam.py" >/dev/null || { echo "  scp 실패"; exit 1; }
r "systemctl daemon-reload && systemctl restart wifi_roam@$IFACE && sleep 3 && systemctl is-active wifi_roam@$IFACE"

step "V1. Restart=always 실동작"
PID1=$(r "systemctl show -p MainPID --value wifi_roam@$IFACE")
echo "  kill 전 MainPID=$PID1"
r "kill -9 $PID1" 2>/dev/null
sleep 6
STATE=$(r "systemctl is-active wifi_roam@$IFACE")
PID2=$(r "systemctl show -p MainPID --value wifi_roam@$IFACE")
echo "  kill 후 state=$STATE MainPID=$PID2"
if [ "$STATE" = "active" ] && [ -n "$PID2" ] && [ "$PID2" != "0" ] && [ "$PID2" != "$PID1" ]; then
    ok "SIGKILL 후 자동 재시작 (PID $PID1 -> $PID2)"
else
    ng "자동 재시작 실패 (state=$STATE, PID=$PID2)"
fi

step "V2. StartLimitIntervalSec=0 — 연속 크래시 내성"
for i in 1 2 3 4 5 6; do
    P=$(r "systemctl show -p MainPID --value wifi_roam@$IFACE")
    [ -n "$P" ] && [ "$P" != "0" ] && r "kill -9 $P" 2>/dev/null
    sleep 4
done
STATE=$(r "systemctl is-active wifi_roam@$IFACE")
if [ "$STATE" = "active" ]; then
    ok "6회 연속 SIGKILL 후에도 active (rate-limit 미포기)"
else
    ng "연속 크래시 후 $STATE — StartLimit 에 걸렸을 수 있음"
    r "systemctl status wifi_roam@$IFACE --no-pager | tail -5"
fi

step "V3. 설정값 하한 — 0 주입"
r "cp /usr/local/etc/wifi_init_conf.json /tmp/pr134_conf_test.json && \
   jq '.$IFACE.roaming.SCAN_NO_RESULT_SLEEP = 0 | .$IFACE.roaming.ROAM_NO_RESULT_MAX_SLEEP = 0' \
      /tmp/pr134_conf_test.json > /usr/local/etc/wifi_init_conf.json"
r "systemctl kill --kill-who=main -s SIGHUP wifi_roam@$IFACE"
sleep 4
REJECT=$(r "grep -a 'rejected' /var/log/cantops/logger.log 2>/dev/null | tail -3")
if [ -n "$REJECT" ]; then
    ok "거부 로그 확인"
    echo "$REJECT" | sed 's/^/      /'
else
    ng "거부 로그 없음 — 하한 가드가 동작하지 않았을 수 있음"
fi
STATE=$(r "systemctl is-active wifi_roam@$IFACE")
[ "$STATE" = "active" ] && ok "0 주입 후에도 데몬 생존 (바쁜 루프/크래시 없음)" || ng "데몬 상태=$STATE"
CPU=$(r "top -bn1 -p \$(systemctl show -p MainPID --value wifi_roam@$IFACE) 2>/dev/null | tail -1 | awk '{print \$9}'")
echo "  데몬 CPU%: ${CPU:-측정불가} (바쁜 루프면 높게 나온다)"

step "V4. 정상값 무회귀"
r "jq '.$IFACE.roaming.SCAN_NO_RESULT_SLEEP = 4' /tmp/pr134_conf_test.json > /usr/local/etc/wifi_init_conf.json"
r "systemctl kill --kill-who=main -s SIGHUP wifi_roam@$IFACE"
sleep 4
APPLIED=$(r "grep -a 'no-candidate backoff=' /var/log/cantops/logger.log 2>/dev/null | tail -2")
echo "  최근 backoff 로그:"; echo "${APPLIED:-  (없음 — 로밍컨디션 미발생)}" | sed 's/^/      /'
STATE=$(r "systemctl is-active wifi_roam@$IFACE")
[ "$STATE" = "active" ] && ok "정상값 반영 후 데몬 생존" || ng "데몬 상태=$STATE"

step "링크 무영향 확인"
LINK_AFTER=$(r "iw dev $IFACE link 2>/dev/null | head -1")
echo "  before: $LINK_BEFORE"
echo "  after : $LINK_AFTER"
[ -n "$LINK_AFTER" ] && [ "$LINK_AFTER" != "Not connected." ] && ok "무선 링크 유지" || ng "링크 상태 확인 필요"

step "결과"
echo "  PASS=$pass  FAIL=$fail"
[ "$fail" -eq 0 ] && echo "  => 온타겟 검증 통과" || echo "  => 실패 항목 있음 (머지 보류)"
exit "$fail"
