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
import os
import re
import signal
import subprocess
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

    복구는 `systemctl restart wifi_init.service` 라서 PartOf 면 그 재시작이 이 유닛까지
    stop 시켜 90초 회복 검증창이 사라진다. (쿨다운은 STATE_FILE 에 있어 잃지 않는다.)
    별도 유닛인 더 큰 이유는 재부팅 예산 분리다 — 정책은 --iface 유무로 state 파일을
    가르므로, 보드 전역 wedge 예산이 per-iface 링크 장애 예산과 섞이면 AP 부재로
    소진된 카운터가 wedge 재부팅을 조용히 거부한다.
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


def test_thermal_cooldown_stops_the_watchdog():
    """과열 차단 중에는 감시자도 멈춰야 한다.

    wifi_logger_temp.sh 의 cooldown_until_recover() 가 WIFI_STOP_UNITS 를 stop 하고
    온도 회복을 기다리는데, 그 목록에 이 유닛이 없으면 살아남은 감시자가 wedge 를
    만나 wifi_init 를 재시작시키고, 그 ExecStartPost 가 무선 유닛을 되살린다 —
    열 차단 한복판에서 라디오가 다시 켜진다.
    """
    temp = WLAN_ROOT / "usr/local/scripts/wifi_logger_temp.sh"
    m = re.search(r"WIFI_STOP_UNITS=\$\{WIFI_STOP_UNITS:-\"([^\"]*)\"\}", temp.read_text())
    assert m, "WIFI_STOP_UNITS 를 찾지 못했다"
    assert "wlan_fw_watch" in m.group(1), \
        "과열 차단 목록에 감시자가 빠지면 차단 중 라디오가 재무장된다"


def test_recovery_check_is_netdev_name_agnostic():
    """회복 확인이 특정 netdev 이름에 매이면 안 된다.

    리로드 후 어느 어댑터가 먼저 뜨는지는 구성에 따라 다르다. mlan0 를 박아두면
    mlan1 만 올라온 보드에서 회복을 영원히 놓쳐 불필요한 재부팅으로 에스컬레이션된다.
    """
    text = SCRIPT.read_text()
    assert "have_wlan_netdev" in text
    assert "/sys/class/net/mlan0" not in text, "특정 netdev 이름을 박지 않는다"


def test_conf_load_does_not_use_eval():
    """설정 로드는 eval 없이 한다 — 가드가 나중에 느슨해져도 주입 여지가 없도록."""
    text = SCRIPT.read_text()
    assert "eval " not in text, "eval 대신 printf -v 를 쓴다"
    assert 'printf -v "$k"' in text


def _executable(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o700)


def _run_marker_race(tmp_path: Path, scenario: str) -> tuple[list[str], Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    events = tmp_path / "events"
    sleep_count = tmp_path / "sleep-count"
    marker = tmp_path / "run/mac-contract-blocked"
    parent_metadata = tmp_path / "parent-metadata"
    marker.parent.mkdir()
    if scenario in {
        "loop-start",
        "stale-marker-non78",
        "marker-active-transition",
        "marker-metadata-unavailable",
        "marker-malformed-metadata",
    }:
        marker.touch()
    if scenario == "parent-78-start":
        parent_metadata.write_text(
            "ActiveState=failed\nExecMainStatus=78\n", encoding="utf-8"
        )
    elif scenario == "stale-marker-non78":
        parent_metadata.write_text(
            "ActiveState=failed\nExecMainStatus=1\n", encoding="utf-8"
        )
    elif scenario == "marker-active-transition":
        parent_metadata.write_text(
            "ActiveState=activating\nExecMainStatus=1\n", encoding="utf-8"
        )
    elif scenario == "marker-malformed-metadata":
        parent_metadata.write_text(
            "ActiveState=failed\nExecMainStatus=not-a-number\n", encoding="utf-8"
        )

    _executable(
        fake_bin / "logger",
        """printf 'logger %s\\n' "$*" >> "$EVENTS"
if [ "$SCENARIO" = before-restart ]; then
  case "$*" in
    *"reloading driver via"*)
        printf 'marker-created-before-restart\\n' >> "$EVENTS"
        touch "$MAC_BLOCK_MARKER" ;;
  esac
fi
if [ "$SCENARIO" = parent-78-before-restart ]; then
  case "$*" in
    *"reloading driver via"*)
        printf 'parent-status-78-before-restart\n' >> "$EVENTS"
        printf 'ActiveState=failed\nExecMainStatus=78\n' > "$PARENT_METADATA" ;;
  esac
fi
""",
    )
    _executable(
        fake_bin / "jq",
        """case "$*" in
    *CHECK_INTERVAL_SEC*) echo 1 ;;
    *INITIAL_DELAY_SEC*) echo 0 ;;
    *TERM_FAULT_CNT*|*ABNORMAL_FAULT_CNT*) echo 1 ;;
    *CONFIRM_TIMEOUT_SEC*) echo 1 ;;
    *RELOAD_ENABLED*) echo 1 ;;
    *RELOAD_COOLDOWN_SEC*) echo 0 ;;
    *VERIFY_TIMEOUT_SEC*) echo 0 ;;
    *VERIFY_INTERVAL_SEC*) echo 1 ;;
    *MAX_REBOOT_COUNT*) echo 3 ;;
    *REBOOT_COOLDOWN_SEC*) echo 1 ;;
    *MIN_UPTIME_SEC*) echo 0 ;;
    *) echo ;;
esac
""",
    )
    _executable(
        fake_bin / "sleep",
        """n=0
[ ! -f "$SLEEP_COUNT" ] || n=$(cat "$SLEEP_COUNT")
n=$((n + 1))
printf '%s\\n' "$n" > "$SLEEP_COUNT"
printf 'sleep %s\\n' "$n" >> "$EVENTS"
if [ "$SCENARIO" = loop-start ] && [ "$n" -ge 3 ]; then kill -TERM "$PPID"; fi
if [ "$SCENARIO" = before-restart ] && [ "$n" -ge 3 ]; then kill -TERM "$PPID"; fi
if [ "$SCENARIO" = restart-blocks-escalation ] && [ "$n" -ge 3 ]; then kill -TERM "$PPID"; fi
if [ "$SCENARIO" = restart-rejected ] && [ "$n" -ge 2 ]; then kill -TERM "$PPID"; fi
if [ "$SCENARIO" = parent-78-start ] && [ "$n" -ge 3 ]; then kill -TERM "$PPID"; fi
if [ "$SCENARIO" = parent-78-before-restart ] && [ "$n" -ge 3 ]; then kill -TERM "$PPID"; fi
if [ "$SCENARIO" = marker-active-transition ] && [ "$n" -ge 3 ]; then kill -TERM "$PPID"; fi
if [ "$SCENARIO" = marker-metadata-unavailable ] && [ "$n" -ge 3 ]; then kill -TERM "$PPID"; fi
if [ "$SCENARIO" = marker-malformed-metadata ] && [ "$n" -ge 3 ]; then kill -TERM "$PPID"; fi
if [ "$SCENARIO" = stale-marker-non78 ] && [ "$n" -ge 5 ]; then kill -TERM "$PPID"; fi
if [ -f "$POLICY_MARK" ]; then kill -TERM "$PPID"; fi
exit 0
""",
    )
    _executable(
        fake_bin / "systemctl",
        """printf 'systemctl %s\\n' "$*" >> "$EVENTS"
if [ "$*" = "show wifi_init.service -p ActiveState -p ExecMainStatus" ]; then
    [ "$SCENARIO" != marker-metadata-unavailable ] || exit 1
    if [ -f "$PARENT_METADATA" ]; then
        cat "$PARENT_METADATA"
    else
        printf 'ActiveState=active\\nExecMainStatus=0\\n'
    fi
    exit 0
fi
if [ "$SCENARIO" = restart-blocks-escalation ]; then
    printf 'marker-created-by-restart\\n' >> "$EVENTS"
    touch "$MAC_BLOCK_MARKER"
elif [ "$SCENARIO" = restart-rejected ]; then
    exit 7
elif [ "$SCENARIO" = stale-marker-non78 ]; then
    exit 0
else
    kill -TERM "$PPID"
fi
exit 0
""",
    )
    for name in ("sync", "snapshot"):
        _executable(fake_bin / name, "exit 0\n")
    policy = tmp_path / "policy"
    _executable(
        policy,
        'printf "policy %s\\n" "$*" >> "$EVENTS"\ntouch "$POLICY_MARK"\nexit 42\n',
    )
    logger_lib = tmp_path / "logger-lib.sh"
    logger_lib.write_text(
        "logger_run_bounded() { shift; \"$@\"; }\n"
        "logger_read_bounded() { return 1; }\n",
        encoding="utf-8",
    )
    config = tmp_path / "config.json"
    config.write_text("{}\n", encoding="utf-8")
    wifi_status = tmp_path / "wifi_status"
    wifi_status.write_text("11\n", encoding="utf-8")
    adapter = tmp_path / "adapter-config"
    adapter.write_text("hardware_status=5\n", encoding="utf-8")

    source = SCRIPT.read_text(encoding="utf-8")
    replacements = {
        "/lib/firmware/": str(tmp_path / "firmware") + "/",
        "/sys/class/net/": str(tmp_path / "net") + "/",
        "/run/wifi/": str(tmp_path / "run") + "/",
    }
    for old, new in replacements.items():
        source = source.replace(old, new)
    for boundary in replacements:
        assert boundary not in source, f"unsafe test path: {boundary}"
    runnable = tmp_path / "wlan_fw_watch.sh"
    runnable.write_text(source, encoding="utf-8")

    policy_mark = tmp_path / "policy.mark"
    state_file = tmp_path / "reload.state"
    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "EVENTS": str(events),
        "SLEEP_COUNT": str(sleep_count),
        "MAC_BLOCK_MARKER": str(marker),
        "PARENT_METADATA": str(parent_metadata),
        "POLICY_MARK": str(policy_mark),
        "SCENARIO": scenario,
        "WIFI_LOGGER_COMMAND_LIB": str(logger_lib),
        "WIFI_INIT_CONF_JSON": str(config),
        "WIFI_STATUS_PROC": str(wifi_status),
        "ADAPTER_CONFIG_GLOB": str(adapter),
        "STATE_FILE": str(state_file),
        "POLICY_BIN": str(policy),
        "SNAPSHOT_BIN": str(fake_bin / "snapshot"),
    }
    proc = subprocess.Popen(
        ["/bin/bash", str(runnable)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=8)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        stdout, stderr = proc.communicate(timeout=3)
        raise AssertionError(f"watcher did not reach bounded stop\n{stdout}\n{stderr}")
    assert proc.returncode == 0, (stdout, stderr)
    return events.read_text(encoding="utf-8").splitlines(), policy_mark, state_file


def _recovery_actions(events: list[str]) -> list[str]:
    return [
        line
        for line in events
        if line.startswith(("systemctl --no-block restart ", "policy "))
    ]


def test_mac_contract_marker_at_loop_start_suppresses_recovery(tmp_path):
    events, policy_mark, _ = _run_marker_race(tmp_path, "loop-start")
    assert any(
        line == "systemctl show wifi_init.service -p ActiveState -p ExecMainStatus"
        for line in events
    ), events
    assert not _recovery_actions(events), events
    assert not policy_mark.exists()


def test_marker_created_before_reload_suppresses_wifi_init_restart(tmp_path):
    events, policy_mark, state_file = _run_marker_race(tmp_path, "before-restart")
    created_at = events.index("marker-created-before-restart")
    assert not _recovery_actions(events[created_at:]), events
    assert not policy_mark.exists()
    assert not state_file.exists(), "aborted restart must not consume reload cooldown"


def test_restart_created_marker_suppresses_reboot_escalation(tmp_path):
    events, policy_mark, _ = _run_marker_race(tmp_path, "restart-blocks-escalation")
    created_at = events.index("marker-created-by-restart")
    assert any(line.startswith("systemctl --no-block restart wifi_init.service") for line in events)
    assert not any(line.startswith("policy ") for line in events[created_at:]), events
    assert not policy_mark.exists()


def test_rejected_restart_does_not_mark_cooldown_or_escalate(tmp_path):
    events, policy_mark, state_file = _run_marker_race(tmp_path, "restart-rejected")
    assert any(line.startswith("systemctl --no-block restart wifi_init.service") for line in events)
    assert not state_file.exists(), "rejected restart must not consume reload cooldown"
    assert not any(line.startswith("policy ") for line in events), events
    assert not policy_mark.exists()


def test_parent_status_78_at_start_suppresses_reload_and_reboot(tmp_path):
    events, policy_mark, state_file = _run_marker_race(tmp_path, "parent-78-start")
    assert not any(
        line.startswith(("systemctl --no-block restart ", "policy "))
        for line in events
    ), events
    assert not policy_mark.exists()
    assert not state_file.exists(), "suppressed reload must not consume cooldown"


def test_parent_status_becoming_78_before_reload_suppresses_actions(tmp_path):
    events, policy_mark, state_file = _run_marker_race(
        tmp_path, "parent-78-before-restart"
    )
    changed_at = events.index("parent-status-78-before-restart")
    assert not any(
        line.startswith(("systemctl --no-block restart ", "policy "))
        for line in events[changed_at:]
    ), events
    assert not policy_mark.exists()
    assert not state_file.exists(), "aborted reload must not consume cooldown"


def test_failed_non78_parent_with_stale_marker_resumes_recovery(tmp_path):
    events, policy_mark, state_file = _run_marker_race(tmp_path, "stale-marker-non78")
    assert any(
        line.startswith("systemctl --no-block restart wifi_init.service")
        for line in events
    ), events
    assert state_file.exists(), "accepted reload must consume reload cooldown"
    assert policy_mark.exists(), "failed reload did not resume ordinary escalation"


def test_marker_during_parent_activation_remains_blocked(tmp_path):
    events, policy_mark, state_file = _run_marker_race(
        tmp_path, "marker-active-transition"
    )
    assert not _recovery_actions(events), events
    assert not policy_mark.exists()
    assert not state_file.exists()


def test_marker_with_unavailable_parent_metadata_remains_blocked(tmp_path):
    events, policy_mark, state_file = _run_marker_race(
        tmp_path, "marker-metadata-unavailable"
    )
    assert not _recovery_actions(events), events
    assert not policy_mark.exists()
    assert not state_file.exists()


def test_marker_with_malformed_parent_metadata_remains_blocked(tmp_path):
    events, policy_mark, state_file = _run_marker_race(
        tmp_path, "marker-malformed-metadata"
    )
    assert not _recovery_actions(events), events
    assert not policy_mark.exists()
    assert not state_file.exists()
