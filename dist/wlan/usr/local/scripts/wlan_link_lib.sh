# shellcheck shell=bash
# 무선 링크 상태 조회 공용 헬퍼. **source 전용** — 직접 실행하지 않으므로 shebang 을 두지
# 않는다. `#!/bin/sh` 스크립트(wifi_event.sh 등)도 source 하며, 쓰이는 문법(local, printf,
# sed)은 dash/busybox ash 에서 모두 동작한다.
#
# ── `iw dev <if> link` 를 쓰지 않는 이유 ──────────────────────────────────────
# 이 명령은 cfg80211 의 current_bss 를 읽는데, moal 드라이버가 이를 신뢰성 있게
# 갱신하지 않아 **연결이 살아 있는데도 "Not connected." 를 반환**하는 사례가 실측됐다.
#
#   2026-07-29 cts-wlan 관측:
#     iw dev mlan0 link  → Not connected.          ← 이것만 틀림
#     iw dev mlan0 info  → ssid jhw_wlan_, ch48    (연결 인지)
#     iw ... station dump→ peer 00:80:4c:c7:7d:dd, -49dBm, 트래픽 흐름
#     wpa_cli status     → wpa_state=COMPLETED
#     ping 게이트웨이     → 0% loss
#
# 완전한 auth→connect 시퀀스를 다시 타면(재연결) 정상으로 돌아오지만 **최초에 깨지는
# 재현 조건이 규명되지 않았다**. 즉 언제든 다시 발생할 수 있고, 이 명령에 링크 판정을
# 의존하는 코드는 조용히 오동작한다(연결됐는데 미연결로 처리).
#
# 실제 피해 경로였던 곳:
#   - 10-set-gateway.sh : BSSID 를 못 얻어 default route 를 fallback 으로 오설정
#   - wifi_init.sh      : 연결 중인데 미연결로 보고 bandcfg 재적용 → mode/bw split-brain
#   - wifi_event.sh     : 첫 부팅 catch-up 미실행 → apply_mcs_tier/run_on_connect 누락
#
# ── 대체 소스 (계단식) ───────────────────────────────────────────────────────
#   1) wpa_cli status   — supplicant SME 의 사실. 연결 판정·BSSID 에 가장 정확하다.
#   2) iw station dump  — supplicant 부재/사망 시 폴백이자, signal 등 물리 지표의 원천.
# 둘 중 하나가 죽어도 동작하도록 순서대로 시도한다. (wifi_logger_link.py 가 같은 이유로
# 이미 station dump 로 전환해 둔 선례가 있다 — "station dump로 연결 판별 (iw link 대체)")

# 연결된 AP 의 BSSID 를 출력한다. 미연결이면 빈 문자열(exit 0).
#
# ⚠️ wpa_state=COMPLETED 일 때만 bssid 를 수락한다. supplicant 는 결합/4-way 진행 중에도
# bssid= 줄을 내며(구현에 따라 00:00:00:00:00:00), 그걸 그대로 믿으면 **미연결인데 BSSID 가
# 있는 것처럼 보여** 이 파일이 고치려는 버그와 똑같은 증상이 된다(catch-up 허위 실행,
# fallback GW 오설정). 이는 리포의 기존 관용과 동일하다 —
# roam_notify.py:228 `_bssid_from_status()`: "wpa_state=COMPLETED 이고 bssid 줄이 있을 때만
# 그 값을 반환 … 결합 미완료 시점의 bssid 줄은 신뢰하지 않는다".
wlan_bssid() {
    local iface="${1:?wlan_bssid: iface required}" bssid="" _status="" _state=""

    _status=$(wpa_cli -i "$iface" status 2>/dev/null)
    _state=$(printf '%s\n' "$_status" | sed -n 's/^wpa_state=//p' | head -1)
    if [ "$_state" = "COMPLETED" ]; then
        bssid=$(printf '%s\n' "$_status" | sed -n 's/^bssid=//p' | head -1)
    fi
    if [ -z "$bssid" ]; then
        # station dump 첫 줄: "Station 00:80:4c:c7:7d:dd (on mlan0)"
        bssid=$(iw dev "$iface" station dump 2>/dev/null \
                | sed -n 's/^Station[[:space:]]\{1,\}\([0-9a-fA-F:]\{17\}\).*/\1/p' | head -1)
    fi
    printf '%s' "$bssid"
}

# 연결 여부. 연결이면 exit 0, 아니면 비0.
wlan_is_connected() {
    local iface="${1:?wlan_is_connected: iface required}"

    if [ "$(wpa_cli -i "$iface" status 2>/dev/null | sed -n 's/^wpa_state=//p' | head -1)" = "COMPLETED" ]; then
        return 0
    fi
    # 폴백: station 엔트리가 하나라도 있으면 연결로 본다.
    [ -n "$(iw dev "$iface" station dump 2>/dev/null | sed -n 's/^Station .*/x/p' | head -1)" ]
}

# 현재 연결 주파수(MHz)를 출력한다. 못 얻으면 빈 문자열.
wlan_freq_mhz() {
    local iface="${1:?wlan_freq_mhz: iface required}" freq=""

    freq=$(wpa_cli -i "$iface" status 2>/dev/null | sed -n 's/^freq=//p' | head -1)
    if [ -z "$freq" ]; then
        # "channel 48 (5240 MHz), width: 20 MHz" — 4자리 이상만 잡아 width(20/40/80)를 배제
        freq=$(iw dev "$iface" info 2>/dev/null \
               | sed -n 's/.*(\([0-9]\{4,\}\) MHz).*/\1/p' | head -1)
    fi
    printf '%s' "$freq"
}

# 현재 RSSI(dBm, 부호 포함 정수)를 출력한다. 못 얻으면 빈 문자열.
# 물리 지표라 station dump 가 원천이다(wpa_cli status 에는 signal 이 없다).
wlan_signal_dbm() {
    local iface="${1:?wlan_signal_dbm: iface required}"

    # "	signal:  	-49 dBm" — 탭/공백 혼재. 'signal avg:' 는 패턴이 달라 매칭되지 않는다.
    iw dev "$iface" station dump 2>/dev/null \
        | sed -n 's/^[[:space:]]*signal:[[:space:]]*\(-\{0,1\}[0-9]\{1,\}\).*/\1/p' | head -1
}
