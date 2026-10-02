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


def run_wconnect(*args, wifi_rc=0):
    """Run the shipped handler with only its absolute wifi path test-patched."""
    root = Path(tempfile.mkdtemp(prefix="wconnect-"))
    try:
        capture = root / "wifi.args"
        timing_log = root / "timing.log"
        wifi = root / "wifi"
        wifi.write_text(textwrap.dedent('''\
            #!/bin/sh
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

        env = {k: v for k, v in os.environ.items() if k != "FTPCMD_LOCAL_ADDR"}
        env.update({
            "PATH": f"{root}:{os.environ['PATH']}",
            "WCONNECT_CAPTURE": str(capture),
            "WCONNECT_LOG": str(timing_log),
            "WCONNECT_WIFI_RC": str(wifi_rc),
        })
        result = subprocess.run(
            [str(handler), *args], capture_output=True, text=True, timeout=30, env=env,
        )
        captured_args = capture.read_text().splitlines() if capture.exists() else []
        timing_lines = timing_log.read_text().splitlines() if timing_log.exists() else []
        return result, captured_args, timing_lines
    finally:
        shutil.rmtree(root, ignore_errors=True)


def run_wconnectraw(*args, status_id="7", set_reply="OK", reassociate_reply="OK",
                    network_rows="7\tcurrent-ap\tany\t"):
    """Run the raw handler with only its absolute wpa_cli path test-patched."""
    root = Path(tempfile.mkdtemp(prefix="wconnectraw-"))
    try:
        capture = root / "wpa_cli.args"
        timing_log = root / "timing.log"
        wpa_cli = root / "wpa_cli"
        wpa_cli.write_text(textwrap.dedent('''\
            #!/bin/sh
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
                    printf 'wpa_state=COMPLETED\\nid=%s\\nssid=current-ap\\nfreq=2412\\n' "$WCONNECTRAW_STATUS_ID"
                    ;;
                *" list_networks") printf 'network id / ssid / bssid / flags\\n%s\\n' "$WCONNECTRAW_NETWORKS" ;;
                *" set_network "*) printf '%s\\n' "$WCONNECTRAW_SET_REPLY" ;;
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

        source = (FTPCMD_DIR / "wconnectraw").read_text()
        old = "WPA_CLI=/usr/sbin/wpa_cli\n"
        replacement = f"WPA_CLI={shlex.quote(str(wpa_cli))}\n"
        if source.count(old) != 1:
            raise AssertionError("wconnectraw WPA_CLI assignment contract changed")
        handler = root / "wconnectraw"
        handler.write_text(source.replace(old, replacement, 1))
        handler.chmod(0o755)

        env = dict(os.environ)
        env.update({
            "PATH": f"{root}:{os.environ['PATH']}",
            "WCONNECTRAW_CAPTURE": str(capture),
            "WCONNECTRAW_LOG": str(timing_log),
            "WCONNECTRAW_STATUS_ID": status_id,
            "WCONNECTRAW_NETWORKS": network_rows,
            "WCONNECTRAW_SET_REPLY": set_reply,
            "WCONNECTRAW_REASSOC_REPLY": reassociate_reply,
        })
        result = subprocess.run(
            [str(handler), *args], capture_output=True, text=True, timeout=10, env=env,
        )
        calls = capture.read_text().splitlines() if capture.exists() else []
        timing_lines = timing_log.read_text().splitlines() if timing_log.exists() else []
        return result, calls, timing_lines
    finally:
        shutil.rmtree(root, ignore_errors=True)


class HandlersExist(unittest.TestCase):
    def test_every_handler_is_executable(self):
        for name in ("getifstate", "ifcup", "ifcdown", "rst", "wconnect", "wconnectraw", "wssid", "wfreq", "wstatus"):
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


class WconnectDefaultInterface(unittest.TestCase):
    def test_no_arguments_apply_stored_mlan0_profile(self):
        r, argv, _ = run_wconnect()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(argv, ["mlan0", "connect"])
        self.assertEqual(r.stdout.splitlines(), ["SUCCESS"])

    def test_ssid_without_interface_uses_mlan0(self):
        r, argv, _ = run_wconnect("field-ap")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(argv, ["mlan0", "connect", "field-ap"])
        self.assertEqual(r.stdout.splitlines(), ["SUCCESS"])

    def test_ssid_words_are_joined_into_one_wifi_argument(self):
        r, argv, _ = run_wconnect("Field", "AP")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(argv, ["mlan0", "connect", "Field AP"])

    def test_numeric_words_are_ssid_text_not_frequencies(self):
        r, argv, _ = run_wconnect("field-ap", "36", "5200")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(argv, ["mlan0", "connect", "field-ap 36 5200"])

    def test_explicit_mlan1_is_preserved(self):
        r, argv, _ = run_wconnect("mlan1", "field", "ap")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(argv, ["mlan1", "connect", "field ap"])

    def test_timing_log_uses_one_trace_through_reply_ready(self):
        r, _, lines = run_wconnect("field-ap", wifi_rc=8)
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
    def test_ssid_without_interface_updates_current_mlan0_network(self):
        r, calls, timing = run_wconnectraw("Field", "AP")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.splitlines(), ["SUCCESS"])
        self.assertEqual(calls, [
            "[-i|mlan0|status]",
            '[-i|mlan0|set_network|7|ssid|"Field AP"]',
            "[-i|mlan0|reassociate]",
        ])
        self.assertEqual(len(timing), 1, timing)
        self.assertIn("phase=commands_submitted", timing[0])
        self.assertIn("iface=mlan0", timing[0])

    def test_explicit_mlan1_is_preserved(self):
        r, calls, _ = run_wconnectraw("mlan1", "lab-ap", status_id="3")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(calls[1], '[-i|mlan1|set_network|3|ssid|"lab-ap"]')
        self.assertEqual(calls[2], "[-i|mlan1|reassociate]")
        self.assertEqual(len(calls), 3)

    def test_missing_ssid_fails_before_wpa_cli(self):
        r, calls, _ = run_wconnectraw()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertEqual(calls, [])
        self.assertEqual(r.stdout.splitlines(), ["FAIL code=2"])

    def test_disconnected_uses_only_enabled_network(self):
        r, calls, _ = run_wconnectraw("current-ap", status_id="")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(calls, [
            "[-i|mlan0|status]",
            "[-i|mlan0|list_networks]",
            '[-i|mlan0|set_network|7|ssid|"current-ap"]',
            "[-i|mlan0|reassociate]",
        ])
        self.assertEqual(r.stdout.splitlines(), ["SUCCESS"])

    def test_ambiguous_disconnected_networks_stop_before_mutation(self):
        r, calls, _ = run_wconnectraw(
            "other-ap", status_id="", network_rows="7\tfirst\tany\t\n8\tsecond\tany\t",
        )
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(calls, ["[-i|mlan0|status]", "[-i|mlan0|list_networks]"])
        self.assertEqual(r.stdout.splitlines(), ["FAIL code=1"])

    def test_fail_payload_is_reported_as_wpa_error(self):
        r, calls, _ = run_wconnectraw(
            "lab-ap", set_reply="FAIL", reassociate_reply="FAIL",
        )
        self.assertEqual(r.returncode, 7, r.stdout + r.stderr)
        self.assertEqual(len(calls), 3)
        self.assertEqual(r.stdout.splitlines(), ["FAIL code=7"])


class ProfileHandlerContract(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="ftpcmd-profile-")
        self.root = Path(self.tempdir.name)
        self.conf = self.root / "wpa_supplicant-mlan0.conf"
        self.conf.write_text(
            'freq_list=5180 5200\nnetwork={\n    ssid="Office AP"\n'
            '    freq_list=5180 5200\n}\n'
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
                f"Path({str(self.conf)!r})",
                1,
            )
        elif name == "wfreq":
            source = source.replace(
                'WIFI=/usr/local/bin/wifi',
                f'WIFI={shlex.quote(str(self.wifi))}',
                1,
            ).replace(
                'CONF="/etc/wpa_supplicant/wpa_supplicant-$IFACE.conf"',
                f'CONF={shlex.quote(str(self.conf))}',
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
        query, _ = self.run_profile("wssid")
        self.assertEqual(query.returncode, 0, query.stderr)
        self.assertEqual(query.stdout.splitlines(), ["Office AP"])
        set_result, calls = self.run_profile("wssid", "New", "Office")
        self.assertEqual(set_result.returncode, 0, set_result.stderr)
        self.assertEqual(set_result.stdout.splitlines(), ["SUCCESS"])
        self.assertEqual(calls, ["mlan0", "ssid", "New Office"])

    def test_wssid_failure_has_safe_actionable_cause(self):
        classify = runpy.run_path(str(FTPCMD_DIR / "wssid"))["wifi_ssid_failure_cause"]
        for diagnostic, expected in (
            ("Error: SSID must be valid UTF-8", "cause=invalid_ssid"),
            ("install: permission denied", "cause=persist_failed"),
        ):
            failure, _ = self.run_profile("wssid", "New", "Office", rc=1, diag=diagnostic)
            self.assertEqual(failure.returncode, 1)
            self.assertEqual(failure.stdout, "")
            self.assertEqual("cause=" + classify(diagnostic), expected)

    def test_wfreq_query_and_set(self):
        query, _ = self.run_profile("wfreq")
        self.assertEqual(query.returncode, 0, query.stderr)
        self.assertEqual(query.stdout.splitlines(), ["5180,5200"])
        set_result, calls = self.run_profile("wfreq", "36", "5200")
        self.assertEqual(set_result.returncode, 0, set_result.stderr)
        self.assertEqual(set_result.stdout.splitlines(), ["SUCCESS"])
        self.assertEqual(calls, ["mlan0", "freq", "36", "5200"])

    def test_wstatus_connected_disconnected_and_invalid(self):
        connected, calls = self.run_profile(
            "wstatus", status="wpa_state=COMPLETED\nssid=Office AP\nfreq=5200",
        )
        self.assertEqual(connected.returncode, 0, connected.stderr)
        self.assertEqual(connected.stdout.splitlines(), ["Office AP 5200"])
        self.assertEqual(calls, ["-i", "mlan0", "status"])
        disconnected, _ = self.run_profile(
            "wstatus", status="wpa_state=SCANNING",
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
