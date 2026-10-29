"""ftpcmd 핸들러의 **첫 stdout 줄** 계약.

vsftpd 디스패치는 핸들러의 첫 stdout 줄을 200 응답 본문으로 쓴다(meta-cts-wlan
의 0001-vsftpd-external-command-dispatch.patch). 그래서 첫 줄이 상위 툴과의
호환 계약이다 — 대체 대상인 FXE3000 카드가 이렇게 답하고 툴이 그것을 파싱한다:

    200 IFCUP command successful.
    200 IFCDOWN command successful.
    200 ath0 UP

핸들러를 셸에서 직접 불러 "출력이 맞다"를 본 것만으로는 부족하다는 것이 실측으로
드러났다 — 디스패치를 태워보니 응답이 `200 OK: getifstate` 로, 값이 실려 나오지
않았다(2026-09-03, vsFTPd 3.0.5). 그래서 여기서는 **첫 줄이 무엇인지**를 실행으로
고정한다. 디스패치 자체는 이 저장소가 소유하지 않으므로 그 축은 실기 FTP 시험에서
본다.
"""
import fcntl
import os
import re
import runpy
import shlex
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

FTPCMD_DIR = Path(__file__).resolve().parents[4] / "opt" / "ftpcmd" / "bin"


def _stub_dir(**commands):
    """PATH 앞에 끼울 스텁 디렉터리. 값은 스텁이 실행할 sh 본문."""
    d = Path(tempfile.mkdtemp())
    for name, body in commands.items():
        p = d / name
        p.write_text("#!/bin/sh\n" + body)
        p.chmod(0o755)
    return d


def run_handler(name, *args, env=None, stubs=None):
    """핸들러를 실제로 실행한다. root 가 필요한 명령은 PATH 스텁으로 대체한다."""
    d = _stub_dir(**(stubs or {}))
    base = {k: v for k, v in os.environ.items() if k != "FTPCMD_LOCAL_ADDR"}
    base["PATH"] = f"{d}:{os.environ['PATH']}"
    base["WIFI_RUN_DIR"] = str(d / "wifi-run")
    base.update(env or {})
    try:
        return subprocess.run(
            [str(FTPCMD_DIR / name), *args],
            capture_output=True, text=True, timeout=30, env=base,
        )
    finally:
        shutil.rmtree(d, ignore_errors=True)


def first_line(text):
    return text.splitlines()[0] if text.splitlines() else ""


def run_wconnect(*args, wifi_rc=0, runtime_stage=False, runtime_active=False,
                 runtime_marker=False):
    """Run the shipped handler with only its absolute wifi path test-patched."""
    root = Path(tempfile.mkdtemp(prefix="wconnect-"))
    try:
        capture = root / "wifi.args"
        timing_log = root / "timing.log"
        wifi = root / "wifi"
        wifi.write_text(textwrap.dedent('''\
            #!/bin/sh
            [ "${FTPCMD_RUNTIME_PSK:-0}" = "${WCONNECT_EXPECT_RUNTIME:-0}" ] || exit 98
            printf '%s\\n' "$@" > "$WCONNECT_CAPTURE"
            echo "wifi connect reply"
            exit "${WCONNECT_WIFI_RC:-0}"
        '''))
        wifi.chmod(0o755)

        logger = root / "logger"
        logger.write_text(textwrap.dedent('''\
            #!/bin/sh
            printf '%s\\n' "$*" >> "$WCONNECT_LOG"
        '''))
        logger.chmod(0o755)

        source = (FTPCMD_DIR / "wconnect").read_text()
        old = "WIFI=/usr/local/bin/wifi\n"
        replacement = f"WIFI={shlex.quote(str(wifi))}\n"
        if source.count(old) != 1:
            raise AssertionError("wconnect WIFI assignment contract changed")
        handler = root / "wconnect"
        handler.write_text(source.replace(old, replacement, 1))
        handler.chmod(0o755)

        run_dir = root / "run"
        run_dir.mkdir()
        if runtime_stage:
            stage = run_dir / "mlan0.runtime-psk"
            stage.write_text("runtime-secret")
            stage.chmod(0o600)
        if runtime_active:
            active = run_dir / "mlan0.runtime-psk.active"
            active.write_text("runtime-secret")
            active.chmod(0o600)
        if runtime_marker:
            marker = run_dir / "mlan0.runtime-switch-in-progress"
            marker.write_text("marker=absent\ncheckpoint=absent\n")
            marker.chmod(0o600)

        env = {k: v for k, v in os.environ.items() if k != "FTPCMD_LOCAL_ADDR"}
        env.update({
            "PATH": f"{root}:{os.environ['PATH']}",
            "WCONNECT_CAPTURE": str(capture),
            "WCONNECT_LOG": str(timing_log),
            "WCONNECT_WIFI_RC": str(wifi_rc),
            "WCONNECT_EXPECT_RUNTIME": (
                "1" if runtime_stage and not runtime_marker else "0"),
            "WIFI_RUN_DIR": str(run_dir),
        })
        result = subprocess.run(
            [str(handler), *args], capture_output=True, text=True, timeout=30, env=env,
        )
        captured_args = capture.read_text().splitlines() if capture.exists() else []
        timing_lines = timing_log.read_text().splitlines() if timing_log.exists() else []
        return result, captured_args, timing_lines
    finally:
        shutil.rmtree(root, ignore_errors=True)


def run_wconnectraw(*args, status_id="7", status_rc=0, set_reply="OK", disconnect_reply="OK",
                    reassociate_reply="OK", network_rows="7\tcurrent-ap\tany\t",
                    wpa_state="COMPLETED", capture_request=False,
                    pending_credential=False, require_grace=False, via_wifi=False,
                    change_busy=False, slow_path_resolution=False,
                    profile_available=True, worker_available=True,
                    existing_request=None, require_request_absent=False,
                    capture_state=False):
    """Run FTP/local entrypoints with real dispatch and boundary-only stubs."""
    root = Path(tempfile.mkdtemp(prefix="wconnectraw-"))
    change_guard = None
    try:
        capture = root / "wpa_cli.args"
        timing_log = root / "timing.log"
        wpa_cli = root / "wpa_cli"
        wpa_cli.write_text(textwrap.dedent('''\
            #!/bin/sh
            case "$*" in
                *" set_network "*|*" disconnect"|*" reassociate")
                    if [ "$WCONNECTRAW_REQUIRE_REQUEST_ABSENT" = 1 ] \
                       && [ -e "$WIFI_RUN_DIR/mlan0.raw-commit" ]; then
                        exit 91
                    fi
                    ;;
            esac
            {
                _sep=
                printf '['
                for _arg in "$@"; do
                    printf '%s%s' "$_sep" "$_arg"
                    _sep='|'
                done
                printf ']\\n'
            } >> "$WCONNECTRAW_CAPTURE"
            case "$*" in
                *" status")
                    [ "$WCONNECTRAW_STATUS_RC" -eq 0 ] || exit "$WCONNECTRAW_STATUS_RC"
                    printf 'wpa_state=%s\\nid=%s\\nssid=current-ap\\nbssid=aa:bb:cc:dd:ee:ff\\nfreq=2412\\n' "$WCONNECTRAW_WPA_STATE" "$WCONNECTRAW_STATUS_ID"
                    ;;
                *" list_networks") printf 'network id / ssid / bssid / flags\\n%s\\n' "$WCONNECTRAW_NETWORKS" ;;
                *" set_network "*) printf '%s\\n' "$WCONNECTRAW_SET_REPLY" ;;
                *" disconnect")
                    if [ "$WCONNECTRAW_REQUIRE_GRACE" = 1 ] \
                       && [ ! -f "$WIFI_RUN_DIR/mlan0.reconfigure-grace" ]; then
                        printf 'FAIL\\n'
                    else
                        printf '%s\\n' "$WCONNECTRAW_DISC_REPLY"
                    fi
                    ;;
                *" reassociate") printf '%s\\n' "$WCONNECTRAW_REASSOC_REPLY" ;;
                *) printf 'FAIL\\n' ;;
            esac
        '''))
        wpa_cli.chmod(0o755)

        logger = root / "logger"
        logger.write_text(textwrap.dedent('''\
            #!/bin/sh
            printf '%s\\n' "$*" >> "$WCONNECTRAW_LOG"
        '''))
        logger.chmod(0o755)
        if slow_path_resolution:
            dirname = root / "dirname"
            dirname.write_text('#!/bin/sh\nsleep 0.12\nexec /usr/bin/dirname "$@"\n')
            dirname.chmod(0o755)

        conf_dir = root / "etc" / "wpa_supplicant"
        conf_dir.mkdir(parents=True)
        for iface in ("mlan0", "mlan1"):
            conf = conf_dir / f"wpa_supplicant-{iface}.conf"
            conf.write_text('update_config=0\nnetwork={\n    ssid="current-ap"\n    psk="password123"\n}\n')
            conf.chmod(0o600)
        if not profile_available:
            (conf_dir / "wpa_supplicant-mlan0.conf").unlink()
        run_dir = root / "wifi-run"
        run_dir.mkdir()
        opc_marker = root / "changeip_essid.restore"
        opc_marker.write_bytes(b"opc-marker\n")
        if existing_request is not None:
            (run_dir / "mlan0.raw-commit").write_bytes(existing_request)
        if pending_credential:
            (run_dir / "mlan0.credential-pending").touch()
        if change_busy:
            change_guard = (run_dir / "mlan0.change.lock").open("w")
            fcntl.flock(change_guard, fcntl.LOCK_SH | fcntl.LOCK_NB)

        scripts = FTPCMD_DIR.parents[2] / "usr" / "local" / "scripts"
        source = (scripts / "wifi_connect_raw.sh").read_text()
        old = "WPA_CLI=/usr/sbin/wpa_cli\n"
        replacement = f"WPA_CLI={shlex.quote(str(wpa_cli))}\n"
        if source.count(old) != 1:
            raise AssertionError("raw engine WPA_CLI assignment contract changed")
        engine = root / "wifi_connect_raw.sh"
        engine.write_text(source.replace(old, replacement, 1))
        engine.chmod(0o755)
        wifi = root / "wifi.sh"
        wifi.write_text((scripts / "wifi.sh").read_text())
        wifi.chmod(0o755)
        # Both entrypoints must bypass ordinary initialization and EXIT sync.
        (root / "wifi_init_config_lib.sh").write_text("exit 97\n")

        source = (FTPCMD_DIR / "wconnectraw").read_text()
        old = "WIFI=/usr/local/bin/wifi\n"
        if source.count(old) != 1:
            raise AssertionError("wconnectraw WIFI assignment contract changed")
        handler = root / "wconnectraw"
        handler.write_text(source.replace(old, f"WIFI={shlex.quote(str(wifi))}\n", 1))
        handler.chmod(0o755)

        invocation = [str(handler), *args]
        if via_wifi:
            invocation = [str(wifi), args[0], "connectraw", *args[1:]]

        env = dict(os.environ)
        env.update({
            "PATH": f"{root}:{os.environ['PATH']}",
            "WCONNECTRAW_CAPTURE": str(capture),
            "WIFI_RAW_COMMIT_HELPER": (
                "/bin/true" if worker_available else str(root / "missing-worker")),
            "WPA_CONF_DIR": str(conf_dir),
            "WIFI_RUN_DIR": str(root / "wifi-run"),
            "WCONNECTRAW_LOG": str(timing_log),
            "WCONNECTRAW_STATUS_ID": status_id,
            "WCONNECTRAW_STATUS_RC": str(status_rc),
            "WCONNECTRAW_WPA_STATE": wpa_state,
            "WCONNECTRAW_NETWORKS": network_rows,
            "WCONNECTRAW_SET_REPLY": set_reply,
            "WCONNECTRAW_DISC_REPLY": disconnect_reply,
            "WCONNECTRAW_REASSOC_REPLY": reassociate_reply,
            "WCONNECTRAW_REQUIRE_GRACE": "1" if require_grace else "0",
            "WCONNECTRAW_REQUIRE_REQUEST_ABSENT": (
                "1" if require_request_absent else "0"),
            "OPC_ESSID_MARKER": str(opc_marker),
        })
        result = subprocess.run(
            invocation, capture_output=True, text=True, timeout=10, env=env,
        )
        calls = capture.read_text().splitlines() if capture.exists() else []
        timing_lines = timing_log.read_text().splitlines() if timing_log.exists() else []
        request = root / "wifi-run" / "mlan0.raw-commit"
        request_lines = request.read_text().splitlines() if request.exists() else []
        if capture_state:
            profile = conf_dir / "wpa_supplicant-mlan0.conf"
            return result, calls, timing_lines, {
                "request": request.read_bytes() if request.exists() else None,
                "profile": profile.read_bytes() if profile.exists() else None,
                "opc_marker": opc_marker.read_bytes() if opc_marker.exists() else None,
                "grace": (run_dir / "mlan0.reconfigure-grace").exists(),
            }
        if capture_request:
            return result, calls, timing_lines, request_lines
        return result, calls, timing_lines
    finally:
        if change_guard is not None:
            change_guard.close()
        shutil.rmtree(root, ignore_errors=True)


class HandlersExist(unittest.TestCase):
    def test_every_handler_is_executable(self):
        for name in (
            "getifstate", "ifcup", "ifcdown", "rst", "wconnect", "wconnectraw",
            "wssid", "wfreq", "wstatus", "wscan", "wpsk", "wgetifstate", "wifcup", "wifcdown", "wrst",
        ):
            path = FTPCMD_DIR / name
            self.assertTrue(path.is_file(), f"{name} 이 없다")
            self.assertTrue(os.access(path, os.X_OK), f"{name} 에 실행비트가 없다")


class FirstLineContract(unittest.TestCase):
    """성공 응답이 될 첫 줄을 실행으로 고정한다."""

    # ip 는 root 를 요구하므로 스텁으로 대체한다. 스텁이 아무 것도 출력하지 않으므로
    # 핸들러가 스스로 찍는 줄만 남는다 — 첫 줄 계약을 그대로 볼 수 있다.
    IP_STUB = {"ip": "exit 0\n"}

    def test_ifcup_first_line_is_the_canned_reply(self):
        r = run_handler("ifcup", "lo", stubs=self.IP_STUB)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(first_line(r.stdout), "IFCUP command successful.")

    def test_ifcdown_first_line_is_the_canned_reply(self):
        r = run_handler("ifcdown", "lo", stubs=self.IP_STUB)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(first_line(r.stdout), "IFCDOWN command successful.")

    def test_getifstate_first_line_carries_the_state(self):
        """조회 명령은 정형 문구가 아니라 **값**이 첫 줄이어야 한다."""
        r = run_handler("getifstate", "lo")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(first_line(r.stdout), r"^lo (UP|DOWN)$")

    def test_getifstate_defaults_to_mlan0_shape(self):
        """인자 없이도 같은 모양이어야 한다(빌드호스트엔 mlan0 이 없어 실패 경로)."""
        r = run_handler("getifstate")
        if r.returncode == 0:
            self.assertRegex(first_line(r.stdout), r"^mlan0 (UP|DOWN)$")
        else:
            self.assertEqual(r.returncode, 2)
            self.assertIn("no such interface: mlan0", r.stdout)


class WconnectRequiredInterface(unittest.TestCase):
    def test_missing_interface_stops_before_wifi(self):
        r, argv, _ = run_wconnect()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertEqual(argv, [])
        self.assertEqual(r.stdout.splitlines(), ["FAIL code=2"])

    def test_invalid_interface_is_not_interpreted_as_ssid(self):
        for interface in ("", "field-ap", "2", "mlan2", "eth0"):
            with self.subTest(interface=interface):
                r, argv, _ = run_wconnect(interface, "AP")
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertEqual(argv, [])
                self.assertEqual(r.stdout.splitlines(), ["FAIL code=2"])

    def test_interface_names_and_numbers_apply_the_selected_profile(self):
        for interface, expected in (
            ("mlan0", "mlan0"), ("0", "mlan0"),
            ("mlan1", "mlan1"), ("1", "mlan1"),
        ):
            with self.subTest(interface=interface):
                r, argv, _ = run_wconnect(interface)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertEqual(argv, [expected, "connect"])
                self.assertEqual(r.stdout.splitlines(), ["SUCCESS"])

    def test_ssid_words_are_joined_into_one_wifi_argument(self):
        r, argv, _ = run_wconnect("0", "Field", "AP")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(argv, ["mlan0", "connect", "Field AP"])

    def test_save_policy_is_forwarded_between_connect_and_joined_ssid(self):
        for policy in ("--save", "--no-save"):
            with self.subTest(policy=policy):
                r, argv, _ = run_wconnect("0", policy, "Field", "AP")
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertEqual(argv, ["mlan0", "connect", policy, "Field AP"])

    def test_option_separator_preserves_save_word_as_ssid_text(self):
        r, argv, _ = run_wconnect("0", "--", "--save")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(argv, ["mlan0", "connect", "--", "--save"])

    def test_staged_runtime_psk_is_forwarded_as_a_secret_free_environment_flag(self):
        r, argv, _ = run_wconnect("0", "Field", "AP", runtime_stage=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(argv, ["mlan0", "connect", "Field AP"])

    def test_active_runtime_psk_does_not_turn_no_ssid_recovery_into_a_new_switch(self):
        r, argv, _ = run_wconnect("0", runtime_active=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(argv, ["mlan0", "connect"])

    def test_marker_owns_a_pending_stage_during_no_ssid_recovery(self):
        r, argv, _ = run_wconnect(
            "0", runtime_stage=True, runtime_marker=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(argv, ["mlan0", "connect"])

    def test_numeric_words_are_ssid_text_not_frequencies(self):
        r, argv, _ = run_wconnect("0", "field-ap", "36", "5200")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(argv, ["mlan0", "connect", "field-ap 36 5200"])

    def test_explicit_mlan1_is_preserved(self):
        r, argv, _ = run_wconnect("mlan1", "field", "ap")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(argv, ["mlan1", "connect", "field ap"])

    def test_timing_log_uses_one_trace_through_reply_ready(self):
        r, _, lines = run_wconnect("0", "field-ap", wifi_rc=8)
        self.assertEqual(r.returncode, 8, r.stdout + r.stderr)
        self.assertEqual(r.stdout.splitlines(), ["FAIL code=8"])
        phases = ("dispatch_received", "arguments_parsed", "connect_invoked", "reply_ready")
        trace_ids = set()
        for phase in phases:
            matches = [line for line in lines if f"phase={phase}" in line]
            self.assertEqual(len(matches), 1, lines)
            match = re.search(r"trace=([0-9]+-[0-9]+).*elapsed_ms=([0-9]+)", matches[0])
            self.assertIsNotNone(match, matches[0])
            trace_ids.add(match.group(1))
        self.assertEqual(len(trace_ids), 1, lines)
        self.assertIn("rc=8", next(line for line in lines if "phase=reply_ready" in line))
        failure = [line for line in lines if "ftpcmd_result" in line]
        self.assertEqual(len(failure), 1, lines)
        self.assertIn("status=fail", failure[0])
        self.assertIn("code=8", failure[0])
        self.assertIn("detail=wifi connect reply", failure[0])


class WconnectRawRuntimeSwitch(unittest.TestCase):
    def test_received_command_is_logged_before_radio_submission(self):
        result, _, lines = run_wconnectraw("0", "Field", "AP")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        received = [line for line in lines if "phase=dispatch_received" in line]
        self.assertEqual(len(received), 1, lines)
        self.assertIn("iface=mlan0", received[0])
        self.assertIn('ssid="Field AP"', received[0])
        self.assertLess(lines.index(received[0]), next(
            i for i, line in enumerate(lines) if "phase=commands_submitted" in line))

    def test_checker_grace_is_published_before_disconnect(self):
        result, _, _ = run_wconnectraw("0", "Field", "AP", require_grace=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_wifi_connectraw_bypasses_regular_initialization(self):
        result, calls, _ = run_wconnectraw("0", "Field", "AP", via_wifi=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["SUCCESS"])
        self.assertEqual(calls[-2:], ["[-i|mlan0|disconnect]", "[-i|mlan0|reassociate]"])

    def test_local_and_ftp_submit_the_same_commands_and_save_request(self):
        ftp, ftp_calls, _, ftp_request = run_wconnectraw(
            "0", "Field", "AP", "42", capture_request=True)
        cli, cli_calls, lines, cli_request = run_wconnectraw(
            "0", "Field", "AP", "42", capture_request=True, via_wifi=True)
        self.assertEqual(ftp.returncode, 0, ftp.stdout + ftp.stderr)
        self.assertEqual(cli.returncode, 0, cli.stdout + cli.stderr)
        self.assertEqual(cli.stdout, ftp.stdout)
        self.assertEqual(cli_calls, ftp_calls)
        self.assertEqual(cli_request[:4], ftp_request[:4])
        self.assertEqual(cli_request[5:], ftp_request[5:])
        self.assertIn("command=connectraw origin=cli", lines[0])

    def test_local_and_ftp_no_save_submit_the_same_runtime_commands(self):
        ftp, ftp_calls, ftp_lines, ftp_state = run_wconnectraw(
            "0", "--no-save", "Field", "AP", capture_state=True)
        cli, cli_calls, cli_lines, cli_state = run_wconnectraw(
            "0", "--no-save", "Field", "AP", capture_state=True, via_wifi=True)
        self.assertEqual(ftp.returncode, 0, ftp.stdout + ftp.stderr)
        self.assertEqual(cli.returncode, 0, cli.stdout + cli.stderr)
        self.assertEqual(cli.stdout, ftp.stdout)
        self.assertEqual(cli_calls, ftp_calls)
        self.assertIsNone(ftp_state["request"])
        self.assertIsNone(cli_state["request"])
        self.assertTrue(any("save_policy=no-save" in line for line in ftp_lines))
        self.assertTrue(any("save_policy=no-save" in line for line in cli_lines))

    def test_no_save_needs_neither_profile_nor_persistence_worker(self):
        result, calls, _, state = run_wconnectraw(
            "0", "--no-save", "Field", "AP", profile_available=False,
            worker_available=False, capture_state=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls, [
            "[-i|mlan0|status]",
            '[-i|mlan0|set_network|7|ssid|"Field AP"]',
            "[-i|mlan0|disconnect]",
            "[-i|mlan0|reassociate]",
        ])
        self.assertIsNone(state["profile"])
        self.assertIsNone(state["request"])

    def test_no_save_cancels_older_persistence_request_before_switch(self):
        result, _, _, state = run_wconnectraw(
            "0", "--no-save", "Field", "AP", existing_request=b"older-request\n",
            require_request_absent=True, capture_state=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIsNone(state["request"])
        self.assertTrue(state["grace"])

    def test_no_save_keeps_older_request_when_preflight_rejects_switch(self):
        prior = b"older-request\n"
        cases = (
            ({"status_rc": 4}, ["[-i|mlan0|status]"]),
            ({"wpa_state": "INTERFACE_DISABLED"}, ["[-i|mlan0|status]"]),
            ({
                "status_id": "",
                "network_rows": "7\tfirst\tany\t\n8\tsecond\tany\t",
            }, ["[-i|mlan0|status]", "[-i|mlan0|list_networks]"]),
        )
        for kwargs, expected_calls in cases:
            with self.subTest(kwargs=kwargs):
                result, calls, _, state = run_wconnectraw(
                    "0", "--no-save", "Field", "AP",
                    existing_request=prior, capture_state=True, **kwargs)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(calls, expected_calls)
                self.assertEqual(state["request"], prior)
                self.assertFalse(state["grace"])

    def test_no_save_preserves_profile_bytes(self):
        before = b'update_config=0\nnetwork={\n    ssid="current-ap"\n    psk="password123"\n}\n'
        result, _, _, state = run_wconnectraw(
            "0", "--no-save", "Field", "AP", capture_state=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(state["profile"], before)
        self.assertEqual(state["opc_marker"], b"opc-marker\n")
        self.assertIsNone(state["request"])

    def test_save_option_keeps_the_default_persistence_contract(self):
        implicit, implicit_calls, _, implicit_request = run_wconnectraw(
            "0", "Field", "AP", capture_request=True)
        explicit, explicit_calls, _, explicit_request = run_wconnectraw(
            "0", "--save", "Field", "AP", capture_request=True)
        self.assertEqual(implicit.returncode, 0, implicit.stdout + implicit.stderr)
        self.assertEqual(explicit.returncode, 0, explicit.stdout + explicit.stderr)
        self.assertEqual(explicit_calls, implicit_calls)
        self.assertEqual(explicit_request[:4], implicit_request[:4])
        self.assertEqual(explicit_request[5:], implicit_request[5:])

    def test_option_separator_allows_save_word_as_ssid(self):
        result, calls, _, request = run_wconnectraw(
            "0", "--", "--save", capture_request=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls[1], '[-i|mlan0|set_network|7|ssid|"--save"]')
        self.assertEqual(request[2], "--save")

    def test_malformed_save_options_stop_before_mutation(self):
        cases = (
            ("--save", "--no-save", "Field AP"),
            ("--no-save", "--save", "Field AP"),
            ("--no-save", "--no-save", "Field AP"),
            ("--bogus", "Field AP"),
            ("--",),
        )
        for args in cases:
            with self.subTest(args=args):
                result, calls, _, state = run_wconnectraw(
                    "0", *args, existing_request=b"older-request\n", capture_state=True)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertEqual(calls, [])
                self.assertEqual(state["request"], b"older-request\n")

    def test_save_words_after_ssid_start_are_plain_ssid_text(self):
        result, calls, _, request = run_wconnectraw(
            "0", "Field", "--no-save", capture_request=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls[1], '[-i|mlan0|set_network|7|ssid|"Field --no-save"]')
        self.assertEqual(request[2], "Field --no-save")

    def test_busy_request_is_logged_before_rejection_without_radio_changes(self):
        result, calls, lines = run_wconnectraw("0", "Field", "AP", change_busy=True)
        self.assertEqual(result.returncode, 6, result.stdout + result.stderr)
        self.assertEqual(calls, [])
        self.assertIn("phase=dispatch_received", lines[0])
        self.assertIn("command=wconnectraw origin=ftp", lines[0])
        self.assertIn("status=fail code=6", lines[-1])

    def test_timing_includes_entrypoint_startup_before_shared_engine(self):
        for via_wifi in (False, True):
            with self.subTest(via_wifi=via_wifi):
                result, _, lines = run_wconnectraw(
                    "0", "Field", "AP", via_wifi=via_wifi, slow_path_resolution=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                submitted = next(line for line in lines if "phase=commands_submitted" in line)
                elapsed = int(re.search(r"elapsed_ms=([0-9]+)", submitted).group(1))
                self.assertGreaterEqual(elapsed, 100, submitted)

    def test_pending_credential_stops_before_runtime_switch(self):
        for policy in ((), ("--no-save",)):
            with self.subTest(policy=policy):
                result, calls, _, request = run_wconnectraw(
                    "0", *policy, "Field", "AP", pending_credential=True,
                    capture_request=True)
                self.assertEqual(result.returncode, 6, result.stdout + result.stderr)
                self.assertEqual(calls, [])
                self.assertEqual(request, [])

    def test_success_queues_independent_persistence_after_radio_commands(self):
        r, calls, _, request = run_wconnectraw("0", "Field", "AP", capture_request=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.splitlines(), ["SUCCESS"])
        self.assertEqual(calls[-2:], ["[-i|mlan0|disconnect]", "[-i|mlan0|reassociate]"])
        self.assertEqual(request[0], "wifi-raw-commit-v1")
        self.assertEqual(request[1:3], ["7", "Field AP"])
        self.assertRegex(request[3], r"^[0-9a-f]{64}$")
        self.assertRegex(request[4], r"^[0-9]+-[0-9]+$")
        self.assertEqual(request[5:], ["aa:bb:cc:dd:ee:ff", "1"])

    def test_failed_radio_command_does_not_queue_persistence(self):
        r, _, _, request = run_wconnectraw(
            "0", "Field", "AP", reassociate_reply="FAIL", capture_request=True)
        self.assertEqual(r.returncode, 7)
        self.assertEqual(request, [])

    def test_explicit_interface_updates_current_mlan0_network(self):
        r, calls, timing = run_wconnectraw("0", "Field", "AP")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.splitlines(), ["SUCCESS"])
        self.assertEqual(calls, [
            "[-i|mlan0|status]",
            '[-i|mlan0|set_network|7|ssid|"Field AP"]',
            "[-i|mlan0|disconnect]",
            "[-i|mlan0|reassociate]",
        ])
        submitted = [line for line in timing if "phase=commands_submitted" in line]
        self.assertEqual(len(submitted), 1, timing)
        self.assertIn("iface=mlan0", submitted[0])
        self.assertRegex(submitted[0], r" disconnect_ms=\d+ reassociate_ms=\d+ ")
        self.assertIn("set_rc=0 disconnect_rc=0 reassociate_rc=0", submitted[0])

    def test_explicit_mlan1_is_preserved(self):
        r, calls, _ = run_wconnectraw("mlan1", "lab-ap", status_id="3")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(calls[1], '[-i|mlan1|set_network|3|ssid|"lab-ap"]')
        self.assertEqual(calls[2], "[-i|mlan1|disconnect]")
        self.assertEqual(calls[3], "[-i|mlan1|reassociate]")
        self.assertEqual(len(calls), 4)

    def test_interface_names_and_numbers_select_the_same_network(self):
        for interface, expected in (
            ("mlan0", "mlan0"), ("0", "mlan0"),
            ("mlan1", "mlan1"), ("1", "mlan1"),
        ):
            with self.subTest(interface=interface):
                r, calls, _ = run_wconnectraw(interface, "1", status_id="3")
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertEqual(calls, [
                    f"[-i|{expected}|status]",
                    f'[-i|{expected}|set_network|3|ssid|"1"]',
                    f"[-i|{expected}|disconnect]",
                    f"[-i|{expected}|reassociate]",
                ])

    def test_invalid_interface_stops_before_wpa_cli(self):
        for interface in ("", "Field", "2", "mlan2", "eth0"):
            with self.subTest(interface=interface):
                r, calls, _ = run_wconnectraw(interface, "AP")
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertEqual(calls, [])
                self.assertEqual(r.stdout.splitlines(), ["FAIL code=2"])

    def test_missing_ssid_fails_before_wpa_cli(self):
        for args in ((), ("0",), ("mlan1",)):
            with self.subTest(args=args):
                r, calls, _ = run_wconnectraw(*args)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertEqual(calls, [])
                self.assertEqual(r.stdout.splitlines(), ["FAIL code=2"])

    def test_disconnected_uses_only_enabled_network(self):
        r, calls, _ = run_wconnectraw("0", "current-ap", status_id="")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(calls, [
            "[-i|mlan0|status]",
            "[-i|mlan0|list_networks]",
            '[-i|mlan0|set_network|7|ssid|"current-ap"]',
            "[-i|mlan0|disconnect]",
            "[-i|mlan0|reassociate]",
        ])
        self.assertEqual(r.stdout.splitlines(), ["SUCCESS"])

    def test_ambiguous_disconnected_networks_stop_before_mutation(self):
        r, calls, _ = run_wconnectraw(
            "0", "other-ap", status_id="", network_rows="7\tfirst\tany\t\n8\tsecond\tany\t",
        )
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(calls, ["[-i|mlan0|status]", "[-i|mlan0|list_networks]"])
        self.assertEqual(r.stdout.splitlines(), ["FAIL code=1"])

    def test_disabled_interface_stops_before_any_wpa_change(self):
        for status_id in ("7", ""):
            with self.subTest(status_id=status_id):
                r, calls, timing = run_wconnectraw(
                    "0", "lab-ap", status_id=status_id, wpa_state="INTERFACE_DISABLED",
                )
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                self.assertEqual(calls, ["[-i|mlan0|status]"])
                self.assertEqual(r.stdout.splitlines(), ["FAIL code=1"])
                failure = [line for line in timing if "status=fail" in line]
                self.assertEqual(len(failure), 1, timing)
                self.assertIn("status=fail code=1", failure[0])
                self.assertIn("wpa_state=INTERFACE_DISABLED", failure[0])

    def test_set_network_failure_leaves_the_link_alone(self):
        r, calls, timing = run_wconnectraw(
            "0", "lab-ap", set_reply="FAIL", reassociate_reply="FAIL",
        )
        self.assertEqual(r.returncode, 7, r.stdout + r.stderr)
        self.assertEqual(calls, [
            "[-i|mlan0|status]",
            '[-i|mlan0|set_network|7|ssid|"lab-ap"]',
        ])
        self.assertEqual(r.stdout.splitlines(), ["FAIL code=7"])
        failure = [line for line in timing if "status=fail" in line]
        self.assertEqual(len(failure), 1, timing)
        self.assertIn("status=fail code=7", failure[0])
        self.assertIn("set_rc=0 set_reply=FAIL", failure[0])

    def test_disconnect_failure_still_requests_reconnection(self):
        r, calls, timing = run_wconnectraw("0", "lab-ap", disconnect_reply="FAIL")
        self.assertEqual(r.returncode, 7, r.stdout + r.stderr)
        self.assertEqual(calls[2:], ["[-i|mlan0|disconnect]", "[-i|mlan0|reassociate]"])
        self.assertEqual(r.stdout.splitlines(), ["FAIL code=7"])
        failure = [line for line in timing if "status=fail" in line]
        self.assertEqual(len(failure), 1, timing)
        self.assertIn("disconnect_rc=0 disconnect_reply=FAIL", failure[0])
        self.assertIn("reassociate_rc=0 reassociate_reply=OK", failure[0])

    def test_reassociate_failure_after_disconnect_is_reported(self):
        r, calls, timing = run_wconnectraw("0", "lab-ap", reassociate_reply="FAIL")
        self.assertEqual(r.returncode, 7, r.stdout + r.stderr)
        self.assertEqual(calls[2:], ["[-i|mlan0|disconnect]", "[-i|mlan0|reassociate]"])
        self.assertEqual(r.stdout.splitlines(), ["FAIL code=7"])
        failure = [line for line in timing if "status=fail" in line]
        self.assertEqual(len(failure), 1, timing)
        self.assertIn("reassociate_rc=0 reassociate_reply=FAIL", failure[0])


class ProfileHandlerContract(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="ftpcmd-profile-")
        self.root = Path(self.tempdir.name)
        self.conf = self.root / "wpa_supplicant-mlan0.conf"
        self.conf.write_text(
            'freq_list=5180 5200\nnetwork={\n    ssid="Office AP"\n'
            '    freq_list=5180 5200\n}\n'
        )
        (self.root / "wpa_supplicant-mlan1.conf").write_text(
            'freq_list=2412\nnetwork={\n    ssid="Warehouse AP"\n'
            '    freq_list=2412\n}\n'
        )
        self.capture = self.root / "calls"
        self.wifi = self.root / "wifi"
        self.wifi.write_text(
            '#!/bin/sh\nprintf "%s\\n" "$@" > "$PROFILE_CAPTURE"\n'
            'if [ -n "$PROFILE_DIAG" ]; then printf "%s\\n" "$PROFILE_DIAG" >&2; fi\n'
            'exit "$PROFILE_RC"\n'
        )
        self.wifi.chmod(0o755)
        self.wpa_cli = self.root / "wpa_cli"
        self.wpa_cli.write_text(
            '#!/bin/sh\nprintf "%s\\n" "$@" > "$PROFILE_CAPTURE"\n'
            'printf "%s\\n" "$PROFILE_STATUS"\n'
            'exit "$PROFILE_RC"\n'
        )
        self.wpa_cli.chmod(0o755)
        self.log = self.root / "logger.log"
        logger = self.root / "logger"
        logger.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$PROFILE_LOG"\n')
        logger.chmod(0o755)

    def tearDown(self):
        self.tempdir.cleanup()

    def run_profile(self, name, *args, status="", rc=0, diag=""):
        source = (FTPCMD_DIR / name).read_text()
        if name == "wssid":
            source = source.replace(
                '"/usr/local/bin/wifi"',
                repr(str(self.wifi)),
                1,
            ).replace(
                'Path(f"/etc/wpa_supplicant/wpa_supplicant-{iface}.conf")',
                f'Path({str(self.root)!r}) / f"wpa_supplicant-{{iface}}.conf"',
                1,
            )
        elif name == "wfreq":
            source = source.replace(
                'WIFI=/usr/local/bin/wifi',
                f'WIFI={shlex.quote(str(self.wifi))}',
                1,
            ).replace(
                'CONF="/etc/wpa_supplicant/wpa_supplicant-$IFACE.conf"',
                f'CONF={shlex.quote(str(self.root))}/wpa_supplicant-$IFACE.conf',
                1,
            ).replace(
                '/usr/local/scripts/wifi_init_config_lib.sh',
                str(FTPCMD_DIR.parents[2] / "usr/local/scripts/wifi_init_config_lib.sh"),
                1,
            )
        elif name == "wstatus":
            source = source.replace(
                'WPA_CLI=/usr/sbin/wpa_cli',
                f'WPA_CLI={shlex.quote(str(self.wpa_cli))}',
                1,
            ).replace(
                'PYTHONPATH=/usr/local/logger',
                f'PYTHONPATH={shlex.quote(str(FTPCMD_DIR.parents[2] / "usr/local/logger"))}',
                1,
            )
        handler = self.root / name
        handler.write_text(source)
        handler.chmod(0o755)
        self.capture.unlink(missing_ok=True)
        env = dict(os.environ)
        env.update({
            "PATH": f"{self.root}:{os.environ['PATH']}",
            "PROFILE_CAPTURE": str(self.capture),
            "PROFILE_STATUS": status,
            "PROFILE_RC": str(rc),
            "PROFILE_DIAG": diag,
            "PROFILE_LOG": str(self.log),
        })
        result = subprocess.run(
            [str(handler), *args], capture_output=True, text=True,
            timeout=30, env=env,
        )
        calls = self.capture.read_text().splitlines() if self.capture.exists() else []
        return result, calls

    def test_wssid_query_and_set(self):
        query, _ = self.run_profile("wssid", "0")
        self.assertEqual(query.returncode, 0, query.stderr)
        self.assertEqual(query.stdout.splitlines(), ["Office AP"])
        set_result, calls = self.run_profile("wssid", "0", "New", "Office")
        self.assertEqual(set_result.returncode, 0, set_result.stderr)
        self.assertEqual(set_result.stdout.splitlines(), ["SUCCESS"])
        self.assertEqual(calls, ["mlan0", "ssid", "New Office"])

    def test_wssid_failure_has_safe_actionable_cause(self):
        classify = runpy.run_path(str(FTPCMD_DIR / "wssid"))["wifi_ssid_failure_cause"]
        for diagnostic, expected in (
            ("Error: SSID must be valid UTF-8", "cause=invalid_ssid"),
            ("install: permission denied", "cause=persist_failed"),
        ):
            failure, _ = self.run_profile("wssid", "0", "New", "Office", rc=1, diag=diagnostic)
            self.assertEqual(failure.returncode, 1)
            self.assertEqual(failure.stdout, "")
            self.assertEqual("cause=" + classify(diagnostic), expected)

    def test_wfreq_query_and_set(self):
        query, _ = self.run_profile("wfreq", "0")
        self.assertEqual(query.returncode, 0, query.stderr)
        self.assertEqual(query.stdout.splitlines(), ["5180,5200"])
        set_result, calls = self.run_profile("wfreq", "0", "36", "5200")
        self.assertEqual(set_result.returncode, 0, set_result.stderr)
        self.assertEqual(set_result.stdout.splitlines(), ["SUCCESS"])
        self.assertEqual(calls, ["mlan0", "freq", "36", "5200"])

    def test_profile_queries_normalize_interface_and_read_its_file(self):
        for interface, ssid, frequencies in (
            ("mlan0", "Office AP", "5180,5200"), ("0", "Office AP", "5180,5200"),
            ("mlan1", "Warehouse AP", "2412"), ("1", "Warehouse AP", "2412"),
        ):
            for name, expected in (("wssid", ssid), ("wfreq", frequencies)):
                with self.subTest(name=name, interface=interface):
                    result, calls = self.run_profile(name, interface)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(result.stdout.splitlines(), [expected])
                    self.assertEqual(calls, [])

    def test_profile_writes_normalize_only_the_interface_argument(self):
        for interface, expected in (
            ("mlan0", "mlan0"), ("0", "mlan0"),
            ("mlan1", "mlan1"), ("1", "mlan1"),
        ):
            for name, operation, values in (
                ("wssid", "ssid", ["1"]), ("wfreq", "freq", ["1", "5200"]),
            ):
                with self.subTest(name=name, interface=interface):
                    result, calls = self.run_profile(name, interface, *values)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(result.stdout.splitlines(), ["SUCCESS"])
                    self.assertEqual(calls, [expected, operation, *values])

    def test_profiles_require_a_valid_interface_before_access(self):
        for name in ("wssid", "wfreq", "wstatus"):
            for args in ((), ("",), ("2",), ("mlan2",), ("eth0",), ("Office", "AP")):
                with self.subTest(name=name, args=args):
                    result, calls = self.run_profile(
                        name, *args, status="wpa_state=COMPLETED\nssid=Office AP\nfreq=5200",
                    )
                    self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                    self.assertEqual(result.stdout, "")
                    self.assertEqual(calls, [])

    def test_wstatus_normalizes_interface_and_rejects_extra_arguments(self):
        for interface, expected in (
            ("mlan0", "mlan0"), ("0", "mlan0"),
            ("mlan1", "mlan1"), ("1", "mlan1"),
        ):
            with self.subTest(interface=interface):
                result, calls = self.run_profile(
                    "wstatus", interface, status="wpa_state=COMPLETED\nssid=Office AP\nfreq=5200",
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(result.stdout.splitlines(), ["Office AP 5200"])
                self.assertEqual(calls, ["-i", expected, "status"])
        result, calls = self.run_profile("wstatus", "0", "extra")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(calls, [])

    def test_wstatus_connected_disconnected_and_invalid(self):
        connected, calls = self.run_profile(
            "wstatus", "0", status="wpa_state=COMPLETED\nssid=Office AP\nfreq=5200",
        )
        self.assertEqual(connected.returncode, 0, connected.stderr)
        self.assertEqual(connected.stdout.splitlines(), ["Office AP 5200"])
        self.assertEqual(calls, ["-i", "mlan0", "status"])
        disconnected, _ = self.run_profile(
            "wstatus", "0", status="wpa_state=SCANNING",
        )
        self.assertEqual(disconnected.returncode, 8)
        invalid, calls = self.run_profile("wstatus", "invalid")
        self.assertEqual(invalid.returncode, 2)
        self.assertEqual(calls, [])

    def test_rst_submits_nonblocking_systemd_request(self):
        marker = self.root / "systemctl.args"
        r = run_handler(
            "rst", env={"RST_CAPTURE": str(marker)},
            stubs={"systemctl": 'printf "%s\\n" "$@" > "$RST_CAPTURE"\n'},
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.splitlines(), ["reboot requested"])
        self.assertEqual(marker.read_text().splitlines(), ["--no-block", "reboot"])


class WirelessInterfaceHandlerContract(unittest.TestCase):
    """Run the aliases and legacy handlers with a private sysfs and command doubles."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="ftpcmd-interface-")
        self.root = Path(self.tempdir.name)
        self.bin = self.root / "bin"
        shutil.copytree(FTPCMD_DIR, self.bin)
        net = self.root / "net"
        for interface, flags in (("mlan0", "0x1"), ("mlan1", "0x0"), ("eth0", "0x1")):
            (net / interface).mkdir(parents=True)
            (net / interface / "flags").write_text(flags + "\n")
        for name in ("getifstate", "ifcup", "ifcdown"):
            handler = self.bin / name
            handler.write_text(handler.read_text().replace("/sys/class/net/", str(net) + "/"))
        self.capture = self.root / "commands"
        for name, body in {
            "ip": '''printf '%s\\n' "$*" >> "$IFACE_CAPTURE"
if [ "$*" = "-o addr show" ] && [ -n "$CONTROL_IFACE" ]; then
    printf '3: %s inet 192.0.2.1/24 scope global %s\\n' "$CONTROL_IFACE" "$CONTROL_IFACE"
fi
if [ "$1" = link ]; then exit "$COMMAND_RC"; fi
''',
            "systemctl": '''printf '%s\\n' "$*" >> "$IFACE_CAPTURE"
exit "$COMMAND_RC"
''',
        }.items():
            command = self.bin / name
            command.write_text("#!/bin/sh\n" + body)
            command.chmod(0o755)

    def tearDown(self):
        self.tempdir.cleanup()

    def run_interface(self, name, *args, control="", rc=0):
        handler = self.bin / name
        self.assertTrue(handler.is_file(), f"missing handler: {name}")
        self.capture.unlink(missing_ok=True)
        env = {key: value for key, value in os.environ.items() if key != "FTPCMD_LOCAL_ADDR"}
        env.update({
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "IFACE_CAPTURE": str(self.capture), "CONTROL_IFACE": control,
            "COMMAND_RC": str(rc),
            "WIFI_RUN_DIR": str(self.root / "wifi-run"),
        })
        if control:
            env["FTPCMD_LOCAL_ADDR"] = "192.0.2.1"
        result = subprocess.run(
            [str(handler), *args], capture_output=True, text=True, timeout=10, env=env,
        )
        calls = self.capture.read_text().splitlines() if self.capture.exists() else []
        return result, calls

    def test_wgetifstate_reports_normalized_interface_and_admin_state(self):
        for interface, expected in (
            ("mlan0", "mlan0 UP"), ("0", "mlan0 UP"),
            ("mlan1", "mlan1 DOWN"), ("1", "mlan1 DOWN"),
        ):
            with self.subTest(interface=interface):
                result, calls = self.run_interface("wgetifstate", interface)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(result.stdout.splitlines(), [expected])
                self.assertEqual(calls, [])

    def test_wifcup_and_wifcdown_act_on_the_normalized_interface(self):
        for interface, expected in (
            ("mlan0", "mlan0"), ("0", "mlan0"),
            ("mlan1", "mlan1"), ("1", "mlan1"),
        ):
            for name, operation, reply in (
                ("wifcup", "up", "IFCUP command successful."),
                ("wifcdown", "down", "IFCDOWN command successful."),
            ):
                with self.subTest(name=name, interface=interface):
                    result, calls = self.run_interface(name, interface)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(first_line(result.stdout), reply)
                    self.assertEqual(calls[0], f"link set {expected} {operation}")

    def test_wrst_preserves_the_board_reboot_operation(self):
        for interface in ("mlan0", "0", "mlan1", "1"):
            with self.subTest(interface=interface):
                result, calls = self.run_interface("wrst", interface)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(result.stdout.splitlines(), ["reboot requested"])
                self.assertEqual(calls, ["--no-block reboot"])

    def test_wrst_without_an_interface_requests_board_reboot(self):
        result, calls = self.run_interface("wrst")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["reboot requested"])
        self.assertEqual(calls, ["--no-block reboot"])

    def test_wrst_without_an_interface_propagates_reboot_failure(self):
        result, calls = self.run_interface("wrst", rc=9)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(calls, ["--no-block reboot"])

    def test_wrst_rejects_invalid_or_extra_arguments_before_reboot(self):
        for args in (("",), ("2",), ("mlan2",), ("eth0",), ("0", "extra")):
            with self.subTest(args=args):
                result, calls = self.run_interface("wrst", *args)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertEqual(calls, [])

    def test_wireless_aliases_require_exactly_one_valid_interface(self):
        for name in ("wgetifstate", "wifcup", "wifcdown"):
            for args in ((), ("",), ("2",), ("mlan2",), ("eth0",), ("0", "extra")):
                with self.subTest(name=name, args=args):
                    result, calls = self.run_interface(name, *args)
                    self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                    self.assertEqual(calls, [])

    def test_wifcdown_preserves_control_connection_guard(self):
        for interface, control in (("0", "mlan0"), ("1", "mlan1")):
            with self.subTest(interface=interface):
                result, calls = self.run_interface("wifcdown", interface, control=control)
                self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
                self.assertIn("refusing", result.stdout)
                self.assertEqual(calls, ["-o addr show"])

    def test_wireless_aliases_propagate_operation_failure(self):
        for name in ("wifcup", "wifcdown", "wrst"):
            with self.subTest(name=name):
                result, _ = self.run_interface(name, "0", rc=9)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)

    def test_legacy_interface_defaults_and_other_names_are_preserved(self):
        result, _ = self.run_interface("getifstate")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["mlan0 UP"])
        for name, operation in (("ifcup", "up"), ("ifcdown", "down")):
            result, calls = self.run_interface(name)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(calls[0], f"link set mlan0 {operation}")
            result, calls = self.run_interface(name, "eth0")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(calls[0], f"link set eth0 {operation}")

    def test_legacy_rst_still_takes_no_arguments(self):
        result, calls = self.run_interface("rst", "0")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(calls, [])


class FailurePaths(unittest.TestCase):
    """실패는 exit code 로 알린다 — 디스패치가 550 으로 매핑한다."""

    def test_unknown_interface_exits_2(self):
        for name in ("getifstate", "ifcup", "ifcdown"):
            r = run_handler(name, "nosuchif9", stubs={"ip": "exit 0\n"})
            self.assertEqual(r.returncode, 2, f"{name}: {r.stdout}{r.stderr}")

    def test_invalid_interface_name_exits_2(self):
        for name in ("getifstate", "ifcup", "ifcdown"):
            r = run_handler(name, "a;b", stubs={"ip": "exit 0\n"})
            self.assertEqual(r.returncode, 2, f"{name}: {r.stdout}{r.stderr}")

    def test_ifcdown_refuses_the_control_interface(self):
        """응답은 핸들러 종료 뒤에 쓰이므로, 세션이 탄 인터페이스를 내리면 조작자가 갇힌다."""
        r = run_handler(
            "ifcdown", "lo",
            env={"FTPCMD_LOCAL_ADDR": "127.0.0.1"},
            stubs={"ip": 'echo "1: lo    inet 127.0.0.1/8 scope host lo"\n'},
        )
        self.assertEqual(r.returncode, 3, f"{r.stdout}{r.stderr}")
        self.assertIn("refusing", r.stdout)


if __name__ == "__main__":
    unittest.main()
