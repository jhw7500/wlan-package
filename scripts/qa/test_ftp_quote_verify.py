"""Exercise the verifier over real loopback FTP sockets, without target hardware."""
import contextlib
import importlib.util
import json
import os
import signal
import socketserver
import stat
import string
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).with_name("ftp_quote_verify.py")
SECRET = "test-credential-only"
SSID = "QA 공간"


class FTPHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(2)
        with self.server.lock:
            self.server.connections += 1
        try:
            self.wfile.write(b"220 QA fixture\r\n")
            while raw := self.rfile.readline(2048):
                command = raw.decode("utf-8").rstrip("\r\n")
                verb, _, arguments = command.partition(" ")
                if verb == "USER":
                    reply = "331 Password required"
                elif verb == "PASS":
                    reply = ("530 " + SECRET) if self.server.auth_error else "230 Logged in"
                elif verb == "QUIT":
                    self.wfile.write(b"221 Goodbye\r\n")
                    return
                else:
                    with self.server.lock:
                        self.server.commands.append(command)
                        occurrence = self.server.command_counts.get(command, 0) + 1
                        self.server.command_counts[command] = occurrence
                    paused = (command == self.server.pause_command
                              and occurrence == self.server.pause_occurrence)
                    if paused:
                        self.server.pause_seen.set()
                        self.server.pause_release.wait(3)
                    if self.server.delay_once and verb == "wfreq":
                        with self.server.lock:
                            delay = not self.server.delayed
                            self.server.delayed = True
                        if delay:
                            time.sleep(0.3)
                    reply = self.response(verb, arguments)
                    if reply is None:
                        return
                self.wfile.write((reply + "\r\n").encode("utf-8"))
                if 'paused' in locals() and paused:
                    self.server.pause_reply_sent.set()
        except OSError:
            pass

    def response(self, verb, arguments):
        allowed = string.ascii_letters + string.digits + " _-.:,=+@/"
        if (any(character not in allowed for character in arguments)
                or len(arguments.encode()) > 255 or len(arguments.split()) > 8):
            return "501 Invalid command arguments."
        args = arguments.split()
        if verb == "wstatus" and not args and self.server.legacy:
            return "200 " + SSID + " 5180"
        if verb == "wstatus" and len(args) == 2 and args[1] == "extra" and self.server.wrong_extra:
            return "200 SUCCESS"
        if not args or args[0] not in ("0", "1", "mlan0", "mlan1"):
            return "550 Failed: " + verb + " exited 2"
        iface = "mlan0" if args[0] in ("0", "mlan0") else "mlan1"
        if verb == "wssid" and len(args) > 1:
            ssid = " ".join(args[1:])
            if ssid != self.server.original_ssid and self.server.reject_ssid:
                return "550 Failed: wssid exited 1"
            if self.server.restore_fail and ssid == self.server.original_ssid:
                return "550 Failed: wssid exited 1"
            self.server.profiles[iface][0] = ssid
            if (ssid == self.server.original_ssid and self.server.disconnect_restore_ssid
                    and not self.server.restore_disconnected):
                self.server.restore_disconnected = True
                return None
            if ssid != self.server.original_ssid and self.server.fail_after_ssid_save:
                return "550 Failed: wssid exited 1"
            return "200 SUCCESS"
        if verb == "wfreq" and len(args) > 1:
            self.server.profiles[iface][1] = ",".join(args[1:])
            if (self.server.profiles[iface][1] == self.server.original_frequency and self.server.disconnect_restore_frequency
                    and not self.server.restore_disconnected):
                self.server.restore_disconnected = True
                return None
            return "200 SUCCESS"
        if len(args) != 1:
            return "550 Failed: " + verb + " exited 2"
        if verb == "wconnect":
            ssid, frequency = self.server.profiles[iface]
            if ssid != self.server.original_ssid and self.server.switch_fail:
                if self.server.external_profile_change:
                    self.server.profiles[iface][0] = "Other Owner AP"
                return "550 Failed: wconnect exited 8"
            current_ssid = "Unintended AP" if self.server.wrong_switch_status and ssid != self.server.original_ssid else ssid
            current_freq = "5180" if self.server.wrong_switch_frequency and ssid != self.server.original_ssid else frequency.split(",")[0]
            self.server.states[iface] = [current_ssid, current_freq]
            if ssid != self.server.original_ssid and self.server.external_profile_change:
                self.server.profiles[iface][0] = "Other Owner AP"
            return "200 SUCCESS"
        if (verb == "wstatus" and self.server.disconnect_after_switch and not self.server.disconnected
                and self.server.profiles[iface][0] != self.server.original_ssid):
            self.server.disconnected = True
            return None
        if verb == "wstatus" and self.server.malformed_status:
            return "200 " + SSID
        return {
            "wssid": "200 " + self.server.profiles[iface][0],
            "wfreq": "200 " + self.server.profiles[iface][1],
            "wstatus": "200 " + " ".join(self.server.states[iface]),
            "wgetifstate": "200 " + iface + " UP",
        }.get(verb, "500 Unknown command")


class FTPServer(socketserver.ThreadingTCPServer):
    daemon_threads = False
    block_on_close = True


@contextlib.contextmanager
def ftp_server(**options):
    server = FTPServer(("127.0.0.1", 0), FTPHandler)
    server.commands = []
    server.command_counts = {}
    server.connections = 0
    server.lock = threading.Lock()
    server.delayed = False
    server.disconnected = False
    server.restore_disconnected = False
    server.pause_command = options.get("pause_command")
    server.pause_occurrence = options.get("pause_occurrence", 1)
    server.pause_seen = threading.Event()
    server.pause_release = threading.Event()
    server.pause_reply_sent = threading.Event()
    server.original_ssid = options.get("initial_ssid", SSID)
    server.original_frequency = options.get("initial_frequency", "5180,5200")
    server.profiles = {iface: [server.original_ssid, server.original_frequency] for iface in ("mlan0", "mlan1")}
    server.states = {iface: [options.get("current_ssid", server.original_ssid), "5180"] for iface in ("mlan0", "mlan1")}
    for name in ("legacy", "wrong_extra", "delay_once", "auth_error", "malformed_status",
                 "switch_fail", "wrong_switch_status", "wrong_switch_frequency", "restore_fail", "disconnect_after_switch",
                 "reject_ssid", "fail_after_ssid_save", "disconnect_restore_ssid", "disconnect_restore_frequency",
                 "external_profile_change"):
        setattr(server, name, options.get(name, False))
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
        assert not thread.is_alive()


def invoke(tmp_path, server, *, iface="0", timeout=1, repeat=1, password_mode=0o600, report=None, extra=()):
    password = tmp_path / "password"
    password.write_text(SECRET + "\n")
    password.chmod(password_mode)
    output = report if report is not None else tmp_path / "report.json"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--host", "127.0.0.1", "--port", str(server.server_address[1]),
         "--user", "qa", "--password-file", str(password), "--iface", iface,
         "--timeout", str(timeout), "--repeat", str(repeat), "--report", str(output), *extra],
        capture_output=True, text=True, timeout=8,
    )
    return result, output


def invoke_with_signal(tmp_path, server, sent_signal, *, repeat=2, extra=None):
    """Signal the real verifier process while one FTP command awaits its reply."""
    password = tmp_path / "password"
    password.write_text(SECRET + "\n")
    password.chmod(0o600)
    output = tmp_path / "report.json"
    launcher = (
        "import importlib.util,sys,time; from types import SimpleNamespace; "
        "spec=importlib.util.spec_from_file_location('ftp_quote_verify',sys.argv[1]); "
        "module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); "
        "module.time=SimpleNamespace(monotonic=time.monotonic,sleep=lambda seconds:None); "
        "raise SystemExit(module.main(sys.argv[2:]))"
    )
    switch_args = SWITCH_ARGS if extra is None else extra
    process = subprocess.Popen(
        [sys.executable, "-c", launcher, str(SCRIPT),
         "--host", "127.0.0.1", "--port", str(server.server_address[1]),
         "--user", "qa", "--password-file", str(password), "--iface", "0",
         "--timeout", "40", "--repeat", str(repeat), "--report", str(output), *switch_args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        assert server.pause_seen.wait(3), "verifier did not reach the requested FTP boundary"
        os.kill(process.pid, sent_signal)
        time.sleep(0.05)
        alive_before_reply = process.poll() is None
        commands_before_reply = list(server.commands)
        server.pause_release.set()
        stdout, stderr = process.communicate(timeout=5)
    finally:
        server.pause_release.set()
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=2)
    result = subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
    return result, output, alive_before_reply, commands_before_reply


def invoke_with_signal_during_settle(tmp_path, server, sent_signal):
    """Deliver a real signal from the child when handler settling begins."""
    password = tmp_path / "password"
    password.write_text(SECRET + "\n")
    password.chmod(0o600)
    output = tmp_path / "report.json"
    launcher = (
        "import importlib.util,os,sys,time; from types import SimpleNamespace; "
        "spec=importlib.util.spec_from_file_location('ftp_quote_verify',sys.argv[1]); "
        "module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); "
        "sent=[False]; "
        "interrupt=lambda seconds:(os.kill(os.getpid(),int(sys.argv[2])),sent.__setitem__(0,True)) "
        "if not sent[0] else None; "
        "module.time=SimpleNamespace(monotonic=time.monotonic,sleep=interrupt); "
        "raise SystemExit(module.main(sys.argv[3:]))"
    )
    result = subprocess.run(
        [sys.executable, "-c", launcher, str(SCRIPT), str(int(sent_signal)),
         "--host", "127.0.0.1", "--port", str(server.server_address[1]),
         "--user", "qa", "--password-file", str(password), "--iface", "0",
         "--timeout", "40", "--repeat", "2", "--report", str(output), *SWITCH_ARGS],
        capture_output=True, text=True, timeout=5,
    )
    return result, output


def invoke_with_signal_during_report(tmp_path, server, sent_signal):
    """Deliver a real signal after the result is calculated but before JSON is written."""
    password = tmp_path / "password"
    password.write_text(SECRET + "\n")
    password.chmod(0o600)
    output = tmp_path / "report.json"
    launcher = (
        "import importlib.util,os,sys; "
        "spec=importlib.util.spec_from_file_location('ftp_quote_verify',sys.argv[1]); "
        "module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); "
        "dump=module.json.dump; signum=int(sys.argv[2]); sent=[False]; "
        "module.json.dump=lambda *args,**kwargs:"
        "(os.kill(os.getpid(),signum),sent.__setitem__(0,True),dump(*args,**kwargs))[2] "
        "if not sent[0] else dump(*args,**kwargs); "
        "raise SystemExit(module.main(sys.argv[3:]))"
    )
    result = subprocess.run(
        [sys.executable, "-c", launcher, str(SCRIPT), str(int(sent_signal)),
         "--host", "127.0.0.1", "--port", str(server.server_address[1]),
         "--user", "qa", "--password-file", str(password), "--iface", "0",
         "--timeout", "40", "--repeat", "1", "--report", str(output), *SWITCH_ARGS],
        capture_output=True, text=True, timeout=5,
    )
    return result, output


def invoke_with_signal_during_safe_copy(tmp_path, server, sent_signal):
    """Signal after deepcopy copies passed but before it copies interrupted."""
    password = tmp_path / "password"
    password.write_text(SECRET + "\n")
    password.chmod(0o600)
    output = tmp_path / "report.json"
    launcher = (
        "import importlib.util,os,sys; "
        "spec=importlib.util.spec_from_file_location('ftp_quote_verify',sys.argv[1]); "
        "module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); "
        "namespace={'real':module.copy.deepcopy,'os':os,'signum':int(sys.argv[2]),'sent':[False]}; "
        "exec(\"def split(value):\\n"
        "    if isinstance(value, dict) and 'checks' in value and 'interrupted' in value:\\n"
        "        copied={}\\n"
        "        for key,item in value.items():\\n"
        "            copied[key]=real(item)\\n"
        "            if key=='passed' and not sent[0]:\\n"
        "                sent[0]=True\\n"
        "                os.kill(os.getpid(),signum)\\n"
        "        return copied\\n"
        "    return real(value)\",namespace); "
        "module.copy.deepcopy=namespace['split']; "
        "raise SystemExit(module.main(sys.argv[3:]))"
    )
    result = subprocess.run(
        [sys.executable, "-c", launcher, str(SCRIPT), str(int(sent_signal)),
         "--host", "127.0.0.1", "--port", str(server.server_address[1]),
         "--user", "qa", "--password-file", str(password), "--iface", "0",
         "--timeout", "40", "--repeat", "1", "--report", str(output), *SWITCH_ARGS],
        capture_output=True, text=True, timeout=5,
    )
    return result, output


def read_report(path):
    assert path.is_file(), "verifier did not create a report"
    return json.loads(path.read_text())


@pytest.mark.parametrize("iface", ["0", "mlan0", "1", "mlan1"])
def test_documented_replies_pass_without_profile_writes(tmp_path, iface):
    # A mutation adding wconnect/wrst or using numeric profile setters fails this.
    with ftp_server() as server:
        result, output = invoke(tmp_path, server, iface=iface, repeat=2)
    assert result.returncode == 0, result.stderr
    report = read_report(output)
    assert report["passed"]
    assert report["checks"] and all(check["passed"] for check in report["checks"])
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    canonical = "mlan0" if iface in ("0", "mlan0") else "mlan1"
    assert server.commands.count("wssid " + canonical) == 2
    assert server.commands.count("wfreq " + canonical) == 2
    for command in server.commands:
        verb, _, arguments = command.partition(" ")
        assert verb in {"wssid", "wfreq", "wstatus", "wgetifstate"}
        if verb in {"wssid", "wfreq"}:
            assert arguments in {"mlan0", "mlan1"}, "profile-changing request emitted"
    assert SECRET not in output.read_text() + result.stdout + result.stderr
    assert SSID not in output.read_text() + result.stdout


def test_legacy_interface_contract_stops_before_profile_queries(tmp_path):
    # A mutation ignoring the guard would risk setters on a pre-0.7.0 image.
    with ftp_server(legacy=True) as server:
        result, output = invoke(tmp_path, server)
    assert result.returncode == 1
    report = read_report(output)
    assert not report["passed"]
    assert report["stopped"] == "interface_contract_mismatch"
    assert not any(command.startswith(("wssid ", "wfreq ")) for command in server.commands)


def test_unexpected_success_for_invalid_arguments_fails_verification(tmp_path):
    # A mutation accepting any 2xx result would conceal broken argument rejection.
    with ftp_server(wrong_extra=True) as server:
        result, output = invoke(tmp_path, server)
    assert result.returncode == 1
    report = read_report(output)
    assert not report["passed"]
    assert any(not check["passed"] and check["command"].endswith(" extra") for check in report["checks"])


def test_timeout_discards_session_and_probes_with_a_fresh_connection(tmp_path):
    # A mutation reusing a timed-out socket risks interpreting its late reply.
    with ftp_server(delay_once=True) as server:
        result, output = invoke(tmp_path, server, timeout=0.05)
    assert result.returncode == 1
    report = read_report(output)
    assert not report["passed"]
    assert report["stopped"] == "transport_error"
    assert any(check.get("error_type") == "TimeoutError" for check in report["checks"])
    recovered = next(check for check in report["checks"] if check["name"] == "reconnect_after_transport_error")
    assert recovered["passed"]
    assert server.connections == 2


def test_authentication_failure_does_not_expose_credentials(tmp_path):
    # A mutation persisting the raw authentication exception leaks its password.
    with ftp_server(auth_error=True) as server:
        result, output = invoke(tmp_path, server)
    assert result.returncode == 1
    report = read_report(output)
    assert not report["passed"]
    assert report["fatal_error"] == "error_perm"
    assert SECRET not in output.read_text() + result.stdout + result.stderr


def test_malformed_status_masks_the_entire_payload(tmp_path):
    # Preserving a supposed frequency before validation exposes the last SSID word.
    with ftp_server(malformed_status=True) as server:
        result, output = invoke(tmp_path, server)
    assert result.returncode == 1
    report = read_report(output)
    assert not report["passed"]
    assert "공간" not in output.read_text() + result.stdout + result.stderr


def test_readable_by_others_password_file_is_rejected_before_connect(tmp_path):
    # A mutation accepting mode 0644 loses the credential-file privacy boundary.
    with ftp_server() as server:
        result, output = invoke(tmp_path, server, password_mode=0o644)
    assert result.returncode == 2
    report = read_report(output)
    assert report["fatal_error"] == "PermissionError"
    assert server.connections == 0


def test_report_symlink_does_not_overwrite_existing_evidence(tmp_path):
    # A mutation using open(..., 'w') would truncate the symlink target.
    existing = tmp_path / "existing"
    existing.write_text("keep this evidence")
    symlink = tmp_path / "report-link"
    symlink.symlink_to(existing)
    with ftp_server() as server:
        result, _ = invoke(tmp_path, server, report=symlink)
    assert result.returncode == 2
    assert "report" in result.stderr.lower()
    assert existing.read_text() == "keep this evidence"
    assert server.connections == 0


SWITCH_ARGS = ("--mode", "switch", "--switch-ssid", "Alternate AP", "--switch-freq", "44")

SIGNAL_BOUNDARIES = [
    pytest.param("wssid mlan0 Alternate AP", 1, False, "interrupted", id="target-ssid-setter"),
    pytest.param("wfreq mlan0 5220", 1, False, "interrupted", id="target-frequency-setter"),
    pytest.param("wconnect mlan0", 1, False, "interrupted", id="target-connect"),
    pytest.param("wssid mlan0", 5, True, "switch_check_failed", id="restore-current-ssid-query"),
    pytest.param("wfreq mlan0", 5, True, "switch_check_failed", id="restore-current-frequency-query"),
    pytest.param("wssid mlan0 Original AP", 1, True, "switch_check_failed", id="restore-ssid-setter"),
    pytest.param("wfreq mlan0 5180 5200", 1, True, "switch_check_failed", id="restore-frequency-setter"),
    pytest.param("wstatus mlan0", 4, True, "switch_check_failed", id="restore-current-status"),
    pytest.param("wconnect mlan0", 2, True, "switch_check_failed", id="restore-connect"),
    pytest.param("wstatus mlan0", 5, True, "switch_check_failed", id="restore-connected-status"),
]


@pytest.mark.parametrize("sent_signal", [signal.SIGINT, signal.SIGTERM], ids=("SIGINT", "SIGTERM"))
@pytest.mark.parametrize("pause_command,pause_occurrence,switch_fail,expected_stopped", SIGNAL_BOUNDARIES)
def test_signal_during_switch_waits_for_reply_and_restores_before_exit(
        tmp_path, sent_signal, pause_command, pause_occurrence, switch_fail, expected_stopped):
    # Removing deferred signal handling would terminate mid-command and strand
    # the target profile, or begin cleanup before the in-flight handler settles.
    with ftp_server(initial_ssid="Original AP", switch_fail=switch_fail,
                    pause_command=pause_command, pause_occurrence=pause_occurrence) as server:
        result, output, alive_before_reply, commands_before_reply = invoke_with_signal(
            tmp_path, server, sent_signal)
    signal_name = signal.Signals(sent_signal).name
    assert alive_before_reply, f"verifier exited on {signal_name} before the current FTP reply"
    assert commands_before_reply[-1] == pause_command, "cleanup started before the current FTP reply"
    assert server.pause_reply_sent.is_set(), "the in-flight handler reply did not complete"
    assert result.returncode == 1, result.stderr
    report = read_report(output)
    assert report["interrupted"] == signal_name
    assert report["stopped"] == expected_stopped
    assert report["restoration"] == {"attempted": True, "passed": True}
    assert server.profiles["mlan0"] == ["Original AP", "5180,5200"]
    assert server.states["mlan0"] == ["Original AP", "5180"]
    assert server.commands.count("wssid mlan0 Alternate AP") == 1


@pytest.mark.parametrize("sent_signal", [signal.SIGINT, signal.SIGTERM], ids=("SIGINT", "SIGTERM"))
def test_signal_while_settling_lost_restore_reply_does_not_cancel_retry(tmp_path, sent_signal):
    # Restoring the default signal handler before the settle/retry sequence
    # finishes would leave the frequency policy or association on the test AP.
    with ftp_server(initial_ssid="Original AP", disconnect_restore_ssid=True) as server:
        result, output = invoke_with_signal_during_settle(tmp_path, server, sent_signal)
    report = read_report(output)
    assert result.returncode == 1, result.stderr
    assert report["interrupted"] == signal.Signals(sent_signal).name
    assert report["stopped"] == "interrupted"
    assert report["restoration"] == {"attempted": True, "passed": True}
    assert any(row.get("error_type") == "EOFError" and row["name"].endswith("restore_ssid")
               for row in report["checks"])
    assert any(row["name"].startswith("switch1/retry/") for row in report["checks"])
    assert server.profiles["mlan0"] == ["Original AP", "5180,5200"]
    assert server.states["mlan0"] == ["Original AP", "5180"]
    assert server.commands.count("wssid mlan0 Alternate AP") == 1


@pytest.mark.parametrize("sent_signal", [signal.SIGINT, signal.SIGTERM], ids=("SIGINT", "SIGTERM"))
def test_signal_during_report_write_is_persisted_as_an_interrupted_failure(tmp_path, sent_signal):
    # Computing exit status before the report write must not let a late stop
    # request produce a successful exit or an evidence file that omits it.
    with ftp_server(initial_ssid="Original AP") as server:
        result, output = invoke_with_signal_during_report(tmp_path, server, sent_signal)
    report = read_report(output)
    assert result.returncode == 1, result.stdout + result.stderr
    assert not report["passed"]
    assert report["interrupted"] == signal.Signals(sent_signal).name
    assert report["stopped"] == "interrupted"
    assert report["restoration"] == {"attempted": True, "passed": True}
    assert report["switch_target"]["ssid_sha256"] == (
        "704a56b48b44d778c5b6c1007aa6575c492535e772c1c70b74b2418dab533ff7")
    assert report["original_network"]["ssid_sha256"] == (
        "a24274cbc8c7475690d5ee3cba1c5687af7f9be74f67b33233bd574a04cd313d")
    assert all(value not in output.read_text() + result.stdout + result.stderr
               for value in ("Alternate AP", "Original AP", SECRET))
    assert server.profiles["mlan0"] == ["Original AP", "5180,5200"]
    assert server.states["mlan0"] == ["Original AP", "5180"]


@pytest.mark.parametrize("sent_signal", [signal.SIGINT, signal.SIGTERM], ids=("SIGINT", "SIGTERM"))
def test_signal_during_safe_report_copy_cannot_publish_a_mixed_success_snapshot(tmp_path, sent_signal):
    # Comparing only the copied/live interrupted value misses a signal that
    # arrives after passed/stopped were copied but before interrupted was copied.
    with ftp_server(initial_ssid="Original AP") as server:
        result, output = invoke_with_signal_during_safe_copy(tmp_path, server, sent_signal)
    report = read_report(output)
    assert result.returncode == 1, result.stdout + result.stderr
    assert not report["passed"]
    assert report["stopped"] == "interrupted"
    assert report["interrupted"] == signal.Signals(sent_signal).name
    assert report["restoration"] == {"attempted": True, "passed": True}
    assert report["switch_target"]["ssid_sha256"] == (
        "704a56b48b44d778c5b6c1007aa6575c492535e772c1c70b74b2418dab533ff7")
    assert report["original_network"]["ssid_sha256"] == (
        "a24274cbc8c7475690d5ee3cba1c5687af7f9be74f67b33233bd574a04cd313d")
    assert all(value not in output.read_text() + result.stdout + result.stderr
               for value in ("Alternate AP", "Original AP", SECRET))
    assert server.profiles["mlan0"] == ["Original AP", "5180,5200"]
    assert server.states["mlan0"] == ["Original AP", "5180"]


@pytest.mark.parametrize("iface", ["0", "mlan1"])
def test_switch_uses_configured_network_and_restores_original_between_rounds(tmp_path, iface):
    with ftp_server(initial_ssid="Original AP") as server:
        result, output = invoke(tmp_path, server, iface=iface, timeout=40, repeat=2, extra=SWITCH_ARGS)
    assert result.returncode == 0, result.stderr
    report = read_report(output)
    assert report["mode"] == "switch" and report["passed"]
    assert report["restoration"] == {"attempted": True, "passed": True}
    canonical = "mlan0" if iface == "0" else "mlan1"
    writes = [command for command in server.commands if command.startswith(("wssid " + canonical + " ", "wfreq " + canonical + " ", "wconnect "))]
    assert writes == [
        "wssid " + canonical + " Alternate AP", "wfreq " + canonical + " 5220", "wconnect " + canonical,
        "wssid " + canonical + " Original AP", "wfreq " + canonical + " 5180 5200", "wconnect " + canonical,
    ] * 2
    assert server.profiles[canonical] == ["Original AP", "5180,5200"]
    assert server.states[canonical] == ["Original AP", "5180"]
    assert "Alternate AP" not in output.read_text() + result.stdout + result.stderr
    assert "Original AP" not in output.read_text() + result.stdout + result.stderr
    assert SECRET not in output.read_text() + result.stdout + result.stderr


@pytest.mark.parametrize("failure", ["switch_fail", "wrong_switch_status", "wrong_switch_frequency", "disconnect_after_switch"])
def test_failed_switch_keeps_failure_and_restores_original_with_a_fresh_session(tmp_path, failure):
    with ftp_server(initial_ssid="Original AP", **{failure: True}) as server:
        result, output = invoke(tmp_path, server, timeout=40, extra=SWITCH_ARGS)
    assert result.returncode == 1, result.stderr
    report = read_report(output)
    assert not report["passed"]
    assert report["restoration"] == {"attempted": True, "passed": True}
    assert server.profiles["mlan0"] == ["Original AP", "5180,5200"]
    assert server.states["mlan0"] == ["Original AP", "5180"]
    assert server.connections >= 3  # contract checks, switch, then independent cleanup


def test_restore_failure_is_visible_and_does_not_apply_an_incomplete_profile(tmp_path):
    with ftp_server(initial_ssid="Original AP", restore_fail=True) as server:
        result, output = invoke(tmp_path, server, timeout=40, extra=SWITCH_ARGS)
    assert result.returncode == 1, result.stderr
    report = read_report(output)
    assert report["restoration"] == {"attempted": True, "passed": False}
    assert any(not row["passed"] and "/restore_" in row["name"] for row in report["checks"])
    assert server.commands.count("wconnect mlan0") == 1


@pytest.mark.parametrize("options", [
    {"initial_frequency": "ANY"}, {"initial_ssid": "Original  AP"},
    {"initial_frequency": "5180,5200,5220,5240,5260,5280,5300,5320"},
    {"current_ssid": "Different AP"},
])
def test_unrestorable_or_unapplied_initial_profile_stops_before_writes(tmp_path, options):
    with ftp_server(**{"initial_ssid": "Original AP", **options}) as server:
        result, output = invoke(tmp_path, server, timeout=40, extra=SWITCH_ARGS)
    assert result.returncode == 1, result.stderr
    assert not read_report(output)["restoration"]["attempted"]
    assert not any(command.startswith(("wssid mlan0 ", "wfreq mlan0 ", "wconnect ")) for command in server.commands)


@pytest.mark.parametrize("extra", [
    ("--mode", "switch"),
    ("--switch-ssid", "Alternate AP", "--switch-freq", "44"),
    ("--mode", "switch", "--switch-ssid", "Bad;SSID", "--switch-freq", "44"),
    ("--mode", "switch", "--switch-ssid", "한글망", "--switch-freq", "44"),
    ("--mode", "switch", "--switch-ssid", "A" * 33, "--switch-freq", "44"),
    ("--mode", "switch", "--switch-ssid", " A ", "--switch-freq", "44"),
    ("--mode", "switch", "--switch-ssid", "a b c d e f g h", "--switch-freq", "44"),
    ("--mode", "switch", "--switch-ssid", "Alternate AP", "--switch-freq", "200"),
])
def test_invalid_switch_configuration_is_rejected_before_any_connection(tmp_path, extra):
    with ftp_server(initial_ssid="Original AP") as server:
        result, _ = invoke(tmp_path, server, timeout=40, extra=extra)
    assert result.returncode == 2
    assert server.connections == 0


def test_switch_to_already_connected_pair_does_not_claim_a_network_transition(tmp_path):
    extra = ("--mode", "switch", "--switch-ssid", "Original AP", "--switch-freq", "5180")
    with ftp_server(initial_ssid="Original AP") as server:
        result, output = invoke(tmp_path, server, timeout=40, extra=extra)
    assert result.returncode == 1, result.stderr
    assert not read_report(output)["restoration"]["attempted"]
    assert not any(command.startswith("wconnect ") for command in server.commands)


@pytest.mark.parametrize("failure", ["reject_ssid", "fail_after_ssid_save"])
def test_first_ssid_failure_does_not_rewrite_an_unchanged_frequency_policy(tmp_path, failure):
    with ftp_server(initial_ssid="Original AP", **{failure: True}) as server:
        result, output = invoke(tmp_path, server, timeout=40, extra=SWITCH_ARGS)
    assert result.returncode == 1
    assert read_report(output)["restoration"] == {"attempted": True, "passed": True}
    assert server.profiles["mlan0"] == ["Original AP", "5180,5200"]
    assert not any(command.startswith("wfreq mlan0 ") for command in server.commands)
    if failure == "reject_ssid":
        assert "wssid mlan0 Original AP" not in server.commands
        assert not any(command.startswith("wconnect ") for command in server.commands)


@pytest.mark.parametrize("failure", ["disconnect_restore_ssid", "disconnect_restore_frequency"])
def test_restore_lost_reply_settles_handler_and_recovers_with_one_fresh_retry(tmp_path, failure, monkeypatch):
    spec = importlib.util.spec_from_file_location("ftp_quote_verify", SCRIPT)
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    waits = []
    # The fixture finishes its write immediately; observe the real-world
    # handler grace period without making the socket regression take 31s.
    monkeypatch.setattr(verifier, "time", SimpleNamespace(monotonic=time.monotonic, sleep=waits.append))
    password = tmp_path / "password"
    password.write_text(SECRET + "\n")
    password.chmod(0o600)
    output = tmp_path / "report.json"
    with ftp_server(initial_ssid="Original AP", **{failure: True}) as server:
        code = verifier.main(["--host", "127.0.0.1", "--port", str(server.server_address[1]),
                              "--user", "qa", "--password-file", str(password), "--report", str(output), *SWITCH_ARGS])
    assert code == 1  # A recovered transport error remains a failed test.
    assert read_report(output)["restoration"] == {"attempted": True, "passed": True}
    assert server.profiles["mlan0"] == ["Original AP", "5180,5200"]
    assert server.states["mlan0"] == ["Original AP", "5180"]
    assert len(waits) == 1 and 30 <= waits[0] <= 31
    assert server.connections >= 4


def test_restore_does_not_overwrite_an_unrelated_profile_change(tmp_path):
    with ftp_server(initial_ssid="Original AP", external_profile_change=True) as server:
        result, output = invoke(tmp_path, server, timeout=40, extra=SWITCH_ARGS)
    assert result.returncode == 1
    report = read_report(output)
    assert report["restoration"] == {"attempted": True, "passed": False}
    assert report["stopped"] == "restoration_conflict"
    assert server.profiles["mlan0"][0] == "Other Owner AP"
    assert "wssid mlan0 Original AP" not in server.commands
    assert "wfreq mlan0 5180 5200" not in server.commands


def test_restoration_conflict_preserves_the_original_switch_failure_reason(tmp_path):
    with ftp_server(initial_ssid="Original AP", switch_fail=True, external_profile_change=True) as server:
        result, output = invoke(tmp_path, server, timeout=40, extra=SWITCH_ARGS)
    assert result.returncode == 1
    report = read_report(output)
    assert report["stopped"] == "switch_check_failed"
    assert report["restoration_error"] == "conflict"
    assert not report["restoration"]["passed"]
    assert server.profiles["mlan0"][0] == "Other Owner AP"
