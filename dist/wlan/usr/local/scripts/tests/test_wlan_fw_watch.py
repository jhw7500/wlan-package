"""드라이버 wedge 감시자(wlan_fw_watch)의 계약 고정.

잡는 장애는 "FW 자동복구가 최종 실패해 드라이버가 driver_status=MTRUE 로 latch 된"
상태다. netdev 는 살아 있고 operstate 도 up 이라 기존 세 경로가 전부 빗나간다
(2026-08-18 cts-wlan 실기: wifi_status=11 / hardware_status=5 가 156초 이상 유지되는
동안 wifi_checker@mlan0 이 active 였는데도 아무 조치가 없었다).

여기서 고정하는 것은 값이 아니라 **판정 규칙**이다:
  - 1차 신호는 wifi_status 여야 한다. hardware_status 는 정상 teardown 마다 NotReady 가
    되므로(rmmod / drv_mode 변경 / 성공한 FLR) 1차로 쓰면 오탐한다.
  - "wifi_status != 0" 로 판정하면 안 된다. 0~10 에는 정상 전이값이 있고, FW 이벤트가
    임의 값을 실어 보낼 수 있다.
  - 빈 값/파일 부재를 0 으로 취급하면 안 된다(rmmod 창에서 빈 읽기가 나온다).
  - 복구는 재부팅이 아니라 모듈 리로드를 먼저 시도해야 하고, 그 리로드 호출은
    --no-block 이어야 한다(블로킹하면 감시 루프가 눈이 먼다).
"""
import re
from pathlib import Path

WLAN_ROOT = Path(__file__).resolve().parents[4]
REPO_ROOT = WLAN_ROOT.parents[1]

SCRIPT = WLAN_ROOT / "usr/local/scripts/wlan_fw_watch.sh"
UNIT = WLAN_ROOT / "etc/systemd/system/wlan_fw_watch.service"
CONF = WLAN_ROOT / "opt/wlan/config/wifi_init_conf.json"
APPLY = WLAN_ROOT / "usr/local/scripts/wifi_apply_enabled.sh"
SERVICES_LIB = WLAN_ROOT / "usr/local/scripts/wifi_services_lib.sh"
CHECKER = WLAN_ROOT / "usr/local/scripts/wifi_checker.sh"
PAYLOAD_MANIFEST = WLAN_ROOT / "DEBIAN/payload-manifest.txt"
SOURCE_MANIFEST = REPO_ROOT / "scripts/source_archive_manifest.txt"


def test_script_and_unit_exist():
    assert SCRIPT.is_file(), "감시 스크립트가 없다"
    assert UNIT.is_file(), "유닛 파일이 없다"


def test_unit_is_not_partof_wifi_init():
    """PartOf 면 감시자가 자기 복구 액션에 스스로 stop 된다.

    복구는 `systemctl restart wifi_init.service` 인데, PartOf=wifi_init.service 면
    그 재시작이 이 유닛까지 stop 시켜 회복 검증도 쿨다운 상태도 사라진다.
    패키지 내 유일한 lifecycle 예외이며 이것이 별도 유닛으로 만든 이유다.
    """
    text = UNIT.read_text()
    assert not re.search(r"^\s*PartOf\s*=", text, re.M), \
        "PartOf 를 걸면 리로드 중 감시자가 죽는다"
    assert re.search(r"^\s*Restart\s*=\s*always", text, re.M)


def test_primary_signal_is_wifi_status_not_hardware_status():
    text = SCRIPT.read_text()
    # 폴링 루프가 읽는 것은 wifi_status 다
    assert "WIFI_STATUS_PROC" in text
    assert "read_wifi_status" in text
    # hardware_status 는 확인용으로만, 그리고 bounded 로만 읽는다
    assert "logger_run_bounded" in text, "adapter config 는 반드시 bounded 로 읽어야 한다"
    m = re.search(r"confirm_hw_not_ready\(\)\s*\{(.*?)\n\}", text, re.S)
    assert m, "confirm_hw_not_ready 가 없다"
    assert "logger_run_bounded" in m.group(1)


def test_terminal_class_matches_exact_value_not_nonzero():
    """'11 이 아니면 정상' 이 아니라 '11 만 TERMINAL' 이어야 한다."""
    text = SCRIPT.read_text()
    assert re.search(r'"11"\)\s*CLASS="TERMINAL"', text), \
        "wifi_status=11(FW_RECOVERY_FAIL) 을 정확히 매칭해야 한다"
    # 빈 값과 0 은 같은 등급(정상/판정불가)으로 접힌다
    assert re.search(r'""\|"0"\)\s*CLASS="OK"', text), \
        "빈 값(파일 부재/rmmod 창)을 0 과 함께 OK 로 접어야 오탐하지 않는다"


def test_debounce_before_acting():
    text = SCRIPT.read_text()
    assert "TERM_FAULT_CNT" in text and "ABNORMAL_FAULT_CNT" in text
    # 임계 미달이면 액션 없이 다음 틱으로
    assert re.search(r'FAULT_CNT"?\s*-lt\s*"?\$THRESH', text), "디바운스 비교가 없다"


def test_reload_is_tried_before_reboot_and_is_nonblocking():
    text = SCRIPT.read_text()
    i_reload = text.find("systemctl --no-block restart wifi_init.service")
    assert i_reload > 0, "리로드는 --no-block 이어야 한다 (블로킹하면 감시가 멈춘다)"
    i_policy = text.find("wlan_reboot_policy")
    assert i_policy > 0
    assert "verify_recovered" in text, "리로드 후 회복을 검증해야 한다"


def test_reboot_request_shape():
    """정책 호출 관례: rc 직접 수신, --force 금지, overtemp 금지, --iface 생략."""
    text = SCRIPT.read_text()
    m = re.search(r"request_reboot\(\)\s*\{(.*?)\n\}", text, re.S)
    assert m, "request_reboot 가 없다"
    body = m.group(1)
    assert "--source wlan_fw_watch" in body
    assert "--force" not in body, "--force 는 정책 게이트를 무력화한다"
    assert "overtemp" not in body, "reason 에 overtemp 가 들어가면 MIN_UPTIME 이 0 으로 강제된다"
    assert "--iface" not in body, \
        "wifi_status 는 보드 전역 신호다. --iface 를 주면 per-iface state 로 재부팅 예산이 갈린다"
    assert re.search(r"rc=\$\?", body), "`if ! cmd` 는 부정된 상태를 잡는다 — $? 를 직접 받아야 한다"


def test_policy_refusal_backoff_longer_than_cooldown():
    """rc=11 은 쿨다운보다 길게 물러나야 한다.

    정책은 거부하면서도 state 를 먼저 쓰므로, 쿨다운보다 짧은 주기로 재요청하면
    last_ts 가 계속 밀려 count 가 영구히 래칫된다.
    """
    for path in (SCRIPT, CHECKER):
        text = path.read_text()
        assert re.search(r'rc"?\s*-eq\s*11.*REBOOT_COOLDOWN_SEC\s*\+', text, re.S), \
            f"{path.name}: rc=11 백오프가 쿨다운보다 짧다"


def test_mfg_mode_suspends_watching():
    text = SCRIPT.read_text()
    assert "is_mfg_mode" in text
    assert re.search(r"if is_mfg_mode; then", text)


def test_wired_into_config_apply_and_services():
    conf = CONF.read_text()
    assert '"fw_watch"' in conf, "설정 템플릿에 .global.fw_watch 가 없다"
    assert 'wlan_fw_watch.service' in APPLY.read_text(), "apply_enabled 배선 누락"
    assert 'wlan_fw_watch.service' in SERVICES_LIB.read_text(), "services 목록 배선 누락"


def test_registered_in_both_manifests():
    payload = PAYLOAD_MANIFEST.read_text().splitlines()
    source = SOURCE_MANIFEST.read_text().splitlines()
    assert "usr/local/scripts/wlan_fw_watch.sh" in payload
    assert "etc/systemd/system/wlan_fw_watch.service" in payload
    assert "dist/wlan/usr/local/scripts/wlan_fw_watch.sh" in source
    assert "dist/wlan/etc/systemd/system/wlan_fw_watch.service" in source


def test_checker_station_dump_judges_output_not_exit_code():
    """nl80211 DUMP 는 드라이버 에러를 NLMSG_DONE 페이로드로 돌려주고 iw 는 exit 0 을 낸다.

    그래서 rc 로 판정하던 기존 코드는 wedge 를 영원히 감지하지 못했다
    (대조 실측: `iw <if> station dump` rc=0 vs `iw <if> info` rc=237).
    """
    m = re.search(r"check_station_dump\(\)\s*\{(.*?)\n\}", CHECKER.read_text(), re.S)
    assert m, "check_station_dump 가 없다"
    body = m.group(1)
    assert "-n " in body or "-z " in body, "출력으로 판정해야 한다"
    assert not re.search(r"iw .*station dump\s*>/dev/null 2>&1\s*$", body.strip()), \
        "exit code 만 보면 드라이버 에러를 놓친다"


def test_checker_state_compare_uses_operstate_values():
    """get_state 는 operstate 를 돌려준다. wpa_state 시절 문자열과 비교하면 죽은 조건이다."""
    text = CHECKER.read_text()
    assert '"$STATE" == "DISCONNECTED"' not in text, \
        "operstate 는 DISCONNECTED 를 반환하지 않는다 (죽은 비교)"
    assert '"$STATE" == "SCANNING"' not in text, \
        "operstate 는 SCANNING 을 반환하지 않는다 (죽은 비교)"
    assert '"$STATE" == "down"' in text


def test_reload_disabled_never_reboots():
    """RELOAD_ENABLED=0 은 "감지만" 이어야 한다.

    실기(2026-08-18)에서 RELOAD_ENABLED=0 으로 시험하다 보드가 재부팅됐다. 설정 이름이
    약속하는 것은 "리로드를 하지 않는다" 이지 "대신 재부팅한다" 가 아니다. 재부팅은
    리로드를 실제로 시도했고 낫지 않았을 때만 정당하다.
    """
    text = SCRIPT.read_text()
    m = re.search(r'if \[ "\$RELOAD_ENABLED" != "1" \]; then(.*?)elif', text, re.S)
    assert m, "RELOAD_ENABLED 비활성 분기가 없다"
    branch = m.group(1)
    assert "request_reboot" not in branch, \
        "리로드가 꺼져 있는데 재부팅하면 설정 이름이 거짓말을 한다"
    assert "reporting only" in branch


def test_cooldown_recurrence_does_escalate():
    """쿨다운 중 재발은 '리로드로도 안 나았다'는 뜻이므로 에스컬레이션이 맞다."""
    text = SCRIPT.read_text()
    assert "recurred within reload cooldown" in text
