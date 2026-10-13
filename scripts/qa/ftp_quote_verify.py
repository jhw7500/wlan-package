#!/usr/bin/env python3
"""FTP QUOTE checks and optional network switching for wlan-proc 0.7.0."""
import argparse
import contextlib
import copy
import datetime
import ftplib
import getpass
import hashlib
import json
import math
import os
import platform
import re
import signal
import stat
import sys
import time


class TransportError(Exception):
    """The control stream can no longer be reused safely."""


class CheckFailed(Exception):
    """A recorded reply did not satisfy the test's expected result."""


class StopRequested(Exception):
    """Finish restoring the original network before returning an interruption."""


@contextlib.contextmanager
def defer_termination(report):
    previous = {}
    report["_signal_generation"] = 0

    def request_stop(signum, frame):
        report["_signal_generation"] += 1
        report["interrupted"] = report.get("interrupted") or signal.Signals(signum).name
        report["stopped"] = report["stopped"] or "interrupted"
        report["passed"] = False

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, request_stop)
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def close(client):
    if client is not None:
        try:
            client.close()
        except OSError:
            pass


def connect(args, password):
    client = ftplib.FTP(timeout=args.timeout, encoding="utf-8")
    try:
        client.connect(args.host, args.port)
        client.login(args.user, password)
    except BaseException:
        close(client)
        raise
    return client


def check(client, report, name, command, accepts):
    started = time.monotonic()
    row = {"name": name, "command": command, "passed": False}
    try:
        try:
            reply = client.sendcmd(command)
        except (ftplib.error_perm, ftplib.error_temp) as error:
            reply = str(error)
        row.update(reply=reply, ftp_code=int(reply[:3]), passed=bool(accepts(reply)))
    except (OSError, EOFError, ftplib.error_proto, UnicodeError, ValueError) as error:
        row["error_type"] = type(error).__name__
        raise TransportError from None
    except KeyboardInterrupt:
        row["error_type"] = "KeyboardInterrupt"
        raise
    finally:
        row["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)
        report["checks"].append(row)
    return reply


def state_reply(reply):
    if not reply.startswith("200 "):
        return False
    ssid, separator, frequency = reply[4:].rpartition(" ")
    return bool(separator and ssid and frequency.isdigit() and int(frequency) > 0)


def valid_ssid(ssid):
    # The FTP dispatcher and handlers join tokens with one ASCII space.
    return (bool(re.fullmatch(r"[A-Za-z0-9 _.:,=+@/-]+", ssid))
            and 1 <= len(ssid.encode()) <= 32
            and ssid == " ".join(ssid.split()) and len(ssid.split()) <= 7)


def frequency_mhz(value):
    if not re.fullmatch(r"[0-9]+", value):
        raise ValueError("Expected a channel number or frequency in MHz")
    frequency = int(value)
    if frequency < 1000:
        frequency = (2407 + 5 * frequency if 1 <= frequency <= 13
                     else 2484 if frequency == 14 else 5000 + 5 * frequency)
    if not (2412 <= frequency <= 2484 or 5180 <= frequency <= 5825):
        raise ValueError("Frequency is outside the target's supported bands")
    return str(frequency)


def require(client, report, name, command, accepts):
    reply = check(client, report, name, command, accepts)
    if not report["checks"][-1]["passed"]:
        raise CheckFailed
    return reply


def settle_pending_handler(report):
    last = report["checks"][-1]
    if ("error_type" in last and last["command"].split(" ", 1)[0] in ("wssid", "wfreq", "wconnect")):
        # A disconnected client does not cancel the server handler.
        time.sleep(max(0, 31 - last["elapsed_ms"] / 1000))


def restore_network(args, password, report, prefix, canonical, original_ssid, frequency_reply, original_status):
    for attempt in range(2):
        client = None
        label = prefix if attempt == 0 else prefix + "retry/"
        try:
            client = connect(args, password)
            current_ssid = check(client, report, label + "restore_current_ssid", "wssid " + canonical,
                                 lambda reply: reply in ("200 " + original_ssid, "200 " + args.switch_ssid))
            current_freq = check(client, report, label + "restore_current_frequency", "wfreq " + canonical,
                                 lambda reply: reply in (frequency_reply, "200 " + args.switch_freq))
            if (current_ssid not in ("200 " + original_ssid, "200 " + args.switch_ssid)
                    or current_freq not in (frequency_reply, "200 " + args.switch_freq)):
                report["restoration_error"] = "conflict"
                report["stopped"] = report["stopped"] or "restoration_conflict"
                return False
            changed = False
            restored = True
            for name, command, current, wanted in (
                ("restore_ssid", "wssid " + canonical + " " + original_ssid, current_ssid, "200 " + original_ssid),
                ("restore_frequency", "wfreq " + canonical + " " + frequency_reply[4:].replace(",", " "), current_freq, frequency_reply),
            ):
                if current != wanted:
                    changed = True
                    reply = check(client, report, label + name, command, lambda reply: reply == "200 SUCCESS")
                    restored = restored and reply == "200 SUCCESS"
            for name, command, wanted in (
                ("restore_stored_ssid", "wssid " + canonical, "200 " + original_ssid),
                ("restore_stored_frequency", "wfreq " + canonical, frequency_reply),
            ):
                reply = check(client, report, label + name, command, lambda reply, wanted=wanted: reply == wanted)
                restored = restored and reply == wanted
            if not restored:
                return False
            state = check(client, report, label + "restore_current_status", "wstatus " + canonical,
                          lambda reply: state_reply(reply) or reply == "550 Failed: wstatus exited 8")
            if changed or state != original_status:
                require(client, report, label + "restore_connect", "wconnect " + canonical,
                        lambda reply: reply == "200 SUCCESS")
            require(client, report, label + "restore_connected_network", "wstatus " + canonical,
                    lambda reply: reply == original_status)
            return True
        except TransportError:
            close(client)
            client = None
            settle_pending_handler(report)
            report["stopped"] = report["stopped"] or "restoration_transport_error"
        except CheckFailed:
            return False
        except Exception as error:
            report["fatal_error"] = type(error).__name__
            return False
        finally:
            close(client)
    return False


def run_switch_checks(args, password, report):
    client = None
    canonical = "mlan0" if args.iface in ("0", "mlan0") else "mlan1"
    success = lambda reply: reply == "200 SUCCESS"

    def transition(name, command, accepts):
        if report.get("interrupted"):
            raise StopRequested
        return require(client, report, name, command, accepts)

    try:
        client = connect(args, password)
        original_ssid = require(client, report, "switch/snapshot_ssid", "wssid " + canonical,
                                lambda reply: reply.startswith("200 ") and valid_ssid(reply[4:]))[4:]
        frequency_reply = require(client, report, "switch/snapshot_frequency", "wfreq " + canonical,
                                  lambda reply: bool(re.fullmatch(r"200 [0-9]+(?:,[0-9]+){0,6}", reply)))
        original_frequencies = frequency_reply[4:].split(",")
        # Unlike a query, a setter interprets small numbers as channels.
        if any(frequency_mhz(value) != value for value in original_frequencies):
            report["stopped"] = "profile_not_restorable"
            return
        original_status = require(client, report, "switch/snapshot_status", "wstatus " + canonical,
                                  lambda reply: state_reply(reply) and reply[4:].rpartition(" ")[0] == original_ssid
                                  and reply[4:].rpartition(" ")[2] in original_frequencies)
        require(client, report, "switch/initial_interface_up", "wgetifstate " + canonical,
                lambda reply: reply == "200 " + canonical + " UP")
        target_status = "200 " + args.switch_ssid + " " + args.switch_freq
        if target_status == original_status:
            report["stopped"] = "target_already_connected"
            return
        report["original_network"] = {"ssid_sha256": hashlib.sha256(original_ssid.encode()).hexdigest(),
                                      "frequency_policy_mhz": original_frequencies,
                                      "connected_frequency_mhz": original_status.rpartition(" ")[2]}
        for iteration in range(1, args.repeat + 1):
            if report.get("interrupted"):
                return
            prefix = "switch" + str(iteration) + "/"
            if client is None:
                client = connect(args, password)
            try:
                # Even a lost setter reply can mean that the profile changed.
                report["restoration"]["attempted"] = True
                transition(prefix + "set_ssid", "wssid " + canonical + " " + args.switch_ssid, success)
                transition(prefix + "set_frequency", "wfreq " + canonical + " " + args.switch_freq, success)
                transition(prefix + "stored_ssid", "wssid " + canonical,
                        lambda reply: reply == "200 " + args.switch_ssid)
                transition(prefix + "stored_frequency", "wfreq " + canonical,
                        lambda reply: reply == "200 " + args.switch_freq)
                transition(prefix + "connect", "wconnect " + canonical, success)
                transition(prefix + "connected_network", "wstatus " + canonical,
                        lambda reply: reply == target_status)
            except StopRequested:
                report["stopped"] = report["stopped"] or "interrupted"
            except CheckFailed:
                report["stopped"] = report["stopped"] or "switch_check_failed"
            except TransportError:
                report["stopped"] = report["stopped"] or "transport_error"
            except KeyboardInterrupt:
                report["stopped"] = "interrupted"
            finally:
                close(client)
                client = None
                settle_pending_handler(report)
                restored = restore_network(args, password, report, prefix, canonical, original_ssid,
                                           frequency_reply, original_status)
                report["restoration"]["passed"] = restored
                if not restored:
                    report["stopped"] = report["stopped"] or "restoration_failed"
            if report["stopped"] or report["fatal_error"]:
                return
    except CheckFailed:
        report["stopped"] = "profile_not_restorable"
    except TransportError:
        report["stopped"] = "transport_error"
    except Exception as error:
        report["fatal_error"] = type(error).__name__
    finally:
        close(client)


def run_checks(args, password, report):
    client = None
    canonical = "mlan0" if args.iface in ("0", "mlan0") else "mlan1"
    numeric = canonical[-1]
    link_ok = lambda reply: reply in ("200 " + canonical + " UP", "200 " + canonical + " DOWN")
    try:
        client = connect(args, password)
        guard = check(client, report, "required_interface_guard", "wstatus",
                      lambda reply: reply == "550 Failed: wstatus exited 2")
        if guard != "550 Failed: wstatus exited 2":
            report["stopped"] = "interface_contract_mismatch"
            return
        for iteration in range(1, args.repeat + 1):
            if report.get("interrupted"):
                return
            prefix = "round" + str(iteration) + "/"
            check(client, report, prefix + "stored_ssid", "wssid " + canonical,
                  lambda reply: reply.startswith("200 ") and 1 <= len(reply[4:].encode()) <= 32)
            check(client, report, prefix + "stored_frequency", "wfreq " + canonical,
                  lambda reply: bool(re.fullmatch(r"200 (ANY|[0-9]+(?:,[0-9]+)*)", reply)))
            states = [
                check(client, report, prefix + "status_" + selector, "wstatus " + selector, state_reply)
                for selector in (numeric, canonical)
            ]
            links = [
                check(client, report, prefix + "interface_" + selector, "wgetifstate " + selector, link_ok)
                for selector in (numeric, canonical)
            ]
            for label, replies in (("status_aliases_match", states), ("interface_aliases_match", links)):
                report["checks"].append({"name": prefix + label, "command": "", "passed":
                                         all(reply.startswith("200 ") for reply in replies) and replies[0] == replies[1]})
            arguments_255 = canonical + " " + "a" * (254 - len(canonical))
            negatives = [
                ("invalid_interface", "wstatus invalid-interface", "550 Failed: wstatus exited 2"),
                ("extra_argument", "wstatus " + canonical + " extra", "550 Failed: wstatus exited 2"),
                ("eight_tokens_reach_handler", "wstatus " + canonical + " x" * 7, "550 Failed: wstatus exited 2"),
                ("nine_tokens_rejected", "wstatus " + canonical + " x" * 8, "501 Invalid command arguments."),
                ("255_argument_bytes_reach_handler", "wstatus " + arguments_255, "550 Failed: wstatus exited 2"),
                ("256_argument_bytes_rejected", "wstatus " + arguments_255 + "a", "501 Invalid command arguments."),
            ]
            for label, value in (("double_quote", '"x"'), ("single_quote", "'x'"),
                                 ("backslash", "\\x"), ("semicolon", ";"), ("non_ascii", "한글")):
                negatives.append((label + "_rejected", "wstatus " + canonical + " " + value,
                                  "501 Invalid command arguments."))
            for label, command, expected in negatives:
                if report.get("interrupted"):
                    return
                check(client, report, prefix + label, command, lambda reply, wanted=expected: reply == wanted)
                check(client, report, prefix + "session_after_" + label, "wgetifstate " + canonical, link_ok)
    except TransportError:
        report["stopped"] = "transport_error"
        close(client)
        client = None
        try:
            client = connect(args, password)
            check(client, report, "reconnect_after_transport_error", "wfreq " + canonical,
                  lambda reply: bool(re.fullmatch(r"200 (ANY|[0-9]+(?:,[0-9]+)*)", reply)))
        except Exception as error:
            report["fatal_error"] = type(error).__name__
    except Exception as error:
        # Authentication exceptions may contain secrets echoed by a server.
        report["fatal_error"] = type(error).__name__
    finally:
        close(client)


def read_password(args):
    if args.password_file is None:
        if not sys.stdin.isatty():
            raise ValueError("A private password file is required without a terminal")
        return getpass.getpass("FTP password: ")
    fd = os.open(args.password_file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) not in (0o400, 0o600)):
            raise PermissionError("Password file must be a private regular file owned by the caller")
        password = stream.read(4097)
        if len(password) > 4096:
            raise ValueError("Password file is too large")
        return password.rstrip("\r\n")


def safe_report(report, user, password):
    report = copy.deepcopy(report)
    report.pop("_signal_generation", None)
    for row in report["checks"]:
        reply = row.get("reply", "")
        command = row.get("command", "")
        verb, _, arguments = command.partition(" ")
        setter = verb == "wssid" and len(arguments.split()) > 1
        if setter:
            row["ssid_sha256"] = hashlib.sha256(arguments.partition(" ")[2].encode()).hexdigest()
            row["command"] = "wssid " + arguments.split()[0] + " [ssid]"
            if reply != "200 SUCCESS" and len(reply) > 3:
                row["reply"] = reply[:3] + " [payload]"
        if reply.startswith("200 ") and verb in ("wssid", "wstatus") and not setter:
            valid_status = verb == "wstatus" and state_reply(reply)
            ssid = reply[4:].rpartition(" ")[0] if valid_status else reply[4:]
            row["ssid_sha256"] = hashlib.sha256(ssid.encode()).hexdigest()
            suffix = " " + reply[4:].rpartition(" ")[2] if valid_status else ""
            row["reply"] = "200 [ssid]" + suffix

    def redact(value):
        if isinstance(value, str):
            for secret in (password, user):
                if secret:
                    value = value.replace(secret, "[credential]")
            return value
        if isinstance(value, list):
            return [redact(item) for item in value]
        if isinstance(value, dict):
            return {key: redact(item) for key, item in value.items()}
        return value

    return redact(report)


def emit_report(args, password, report, fd, exit_code):
    try:
        with os.fdopen(fd, "w") as stream:
            while True:
                generation = report.get("_signal_generation", 0)
                if report.get("interrupted"):
                    report["passed"] = False
                safe = safe_report(report, args.user, password)
                if generation != report.get("_signal_generation", 0):
                    continue
                stream.seek(0)
                json.dump(safe, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.truncate()
                if generation == report.get("_signal_generation", 0):
                    break
    except OSError:
        print("Cannot write report.", file=sys.stderr)
        return 2
    # The finalized file is the result boundary. Keep stdout and the exit code
    # consistent with it even if another signal arrives after that boundary.
    print(json.dumps({"passed": safe["passed"], "checks": len(safe["checks"]),
                      "failed_checks": [row["name"] for row in safe["checks"] if not row["passed"]],
                      "stopped": safe["stopped"], "fatal_error": safe["fatal_error"],
                      "interrupted": safe.get("interrupted")}))
    return 1 if safe.get("interrupted") else exit_code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=21)
    parser.add_argument("--user", required=True)
    parser.add_argument("--iface", choices=("0", "1", "mlan0", "mlan1"), default="0")
    parser.add_argument("--timeout", type=float, default=40, help="Socket response timeout in seconds (default: 40)")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--mode", choices=("read-only", "switch"), default="read-only")
    parser.add_argument("--switch-ssid", help="Target AP SSID; uses the target's stored Wi-Fi authentication")
    parser.add_argument("--switch-freq", help="Target AP frequency in MHz or channel number; required in switch mode")
    parser.add_argument("--password-file", help="Caller-owned regular file, mode 0600 or 0400; otherwise prompt privately")
    parser.add_argument("--report", required=True, help="New JSON report path; existing files are never overwritten")
    args = parser.parse_args(argv)
    if not (1 <= args.port <= 65535 and math.isfinite(args.timeout) and args.timeout > 0 and args.repeat > 0):
        parser.error("port, timeout and repeat must be positive and within their valid ranges")
    if args.mode == "switch":
        if not args.switch_ssid or not valid_ssid(args.switch_ssid) or args.switch_freq is None:
            parser.error("switch mode requires a restorable FTP-compatible SSID and --switch-freq")
        if args.timeout < 35:
            parser.error("switch mode requires --timeout >= 35 for the server's 30-second handler budget")
        try:
            args.switch_freq = frequency_mhz(args.switch_freq)
        except ValueError:
            parser.error("--switch-freq must be a supported channel or frequency in MHz")
    elif args.switch_ssid is not None or args.switch_freq is not None:
        parser.error("--switch-ssid and --switch-freq require --mode switch")
    try:
        fd = os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
    except OSError:
        print("Cannot create report: choose a new writable report path.", file=sys.stderr)
        return 2
    report = {
        "schema_version": 1, "mode": args.mode, "host": args.host, "port": args.port,
        "iface": args.iface, "timeout_seconds": args.timeout, "iterations": args.repeat,
        "client": "Python ftplib", "python_version": platform.python_version(),
        "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "checks": [], "stopped": None, "fatal_error": None, "passed": False, "interrupted": None,
        "scope": "QUOTE responses, argument rejection and control-session reuse; release readiness is not assessed.",
    }
    password = None
    exit_code = 2
    if args.mode == "switch":
        report["switch_target"] = {"ssid_sha256": hashlib.sha256(args.switch_ssid.encode()).hexdigest(),
                                   "frequency_mhz": args.switch_freq, "authentication": "existing-profile"}
        report["restoration"] = {"attempted": False, "passed": None}
        report["scope"] = "QUOTE contract, configured SSID/frequency transition and FTP restoration; release readiness is not assessed."
    try:
        password = read_password(args)
        if not password or "\r" in password or "\n" in password:
            raise ValueError("A nonempty single-line password is required")
    except (OSError, ValueError, EOFError) as error:
        report["fatal_error"] = type(error).__name__
        return emit_report(args, password, report, fd, exit_code)
    else:
        guard = defer_termination(report) if args.mode == "switch" else contextlib.nullcontext()
        with guard:
            run_checks(args, password, report)
            if (args.mode == "switch" and not report["stopped"] and not report["fatal_error"]
                    and all(row["passed"] for row in report["checks"])):
                run_switch_checks(args, password, report)
            report["passed"] = bool(report["checks"]) and all(row["passed"] for row in report["checks"]) and not report["fatal_error"] and not report["stopped"]
            exit_code = 0 if report["passed"] else 1
            return emit_report(args, password, report, fd, exit_code)


if __name__ == "__main__":
    sys.exit(main())
