#!/usr/bin/python3
"""Persist a raw SSID switch only after its exact association is confirmed."""

import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import syslog
import tempfile
import time
from pathlib import Path


SSID_RE = re.compile(r"[A-Za-z0-9_.:,=+@/ -]{1,32}\Z")
NETWORK_START = re.compile(rb"^[ \t]*network[ \t]*=[ \t]*\{")
NETWORK_END = re.compile(rb"^[ \t]*\}")
SSID_LINE = re.compile(rb"^([ \t]*)ssid[ \t]*=")
COMMENTED_SSID = re.compile(rb"^[ \t]*#[ \t]*ssid[ \t]*=")


def log_result(trace, iface, result, reason=""):
    level = syslog.LOG_INFO if result == "saved" else syslog.LOG_ERR
    syslog.syslog(level, f"ftpcmd_raw_commit trace={trace} iface={iface} status={result} reason={reason}")


def parse_status(output):
    return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)


class WpaCliFailure(Exception):
    """A control-interface failure, distinct from an unmatched association."""


def wpa_call(wpa_cli, iface, *args):
    try:
        result = subprocess.run(
            [wpa_cli, "-i", iface, *args], capture_output=True, text=True,
            timeout=1, check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise WpaCliFailure("wpa_cli_timeout") from error
    except OSError as error:
        raise WpaCliFailure("wpa_cli_error") from error
    output = result.stdout.strip()
    if result.returncode != 0 or output == "FAIL" or output.startswith("Failed to connect"):
        raise WpaCliFailure("wpa_cli_error")
    return output


def target_is_connected(wpa_cli, iface, network_id, ssid, old_bssid,
                        require_new_bssid, check_profile=False):
    status = parse_status(wpa_call(wpa_cli, iface, "status"))
    if "wpa_state" not in status:
        raise WpaCliFailure("wpa_cli_error")
    if not (status.get("wpa_state") == "COMPLETED"
            and status.get("id") == network_id
            and status.get("ssid") == ssid
            and (status.get("freq") or "").isdigit()
            and re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", status.get("bssid", ""))
            and not (require_new_bssid and status.get("bssid") == old_bssid)):
        return False
    if check_profile:
        return wpa_call(wpa_cli, iface, "get_network", network_id, "ssid") == f'"{ssid}"'
    return True


def lock_file(path):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def single_network_profile(content, ssid):
    lines = content.splitlines(keepends=True)
    network_count = 0
    in_network = False
    active = []
    commented = []
    close_index = None
    for index, line in enumerate(lines):
        if line.lstrip().startswith(b"#"):
            if in_network and COMMENTED_SSID.match(line):
                commented.append(index)
            continue
        if NETWORK_START.match(line):
            network_count += 1
            if in_network:
                raise ValueError("nested_network")
            in_network = True
            continue
        if in_network and NETWORK_END.match(line):
            in_network = False
            close_index = index
            continue
        if in_network and SSID_LINE.match(line):
            active.append(index)
    if network_count != 1 or in_network or close_index is None or len(active) > 1:
        raise ValueError("unsupported_topology")
    newline = b"\r\n" if b"\r\n" in content else b"\n"
    if active:
        index = active[0]
        indent = SSID_LINE.match(lines[index]).group(1)
        lines[index] = indent + b'ssid="' + ssid.encode("ascii") + b'"' + newline
    elif commented:
        index = commented[0]
        indent = re.match(rb"^([ \t]*)", lines[index]).group(1)
        lines[index] = indent + b'ssid="' + ssid.encode("ascii") + b'"' + newline
    else:
        lines.insert(close_index, b'    ssid="' + ssid.encode("ascii") + b'"' + newline)
    return b"".join(lines)


def topology_supported(run_dir, iface):
    policy = run_dir / f"{iface}.roam-policy.json"
    latch_dir = Path(os.environ.get("WIFI_ROAM_POLICY_LATCH_DIR", run_dir.parent))
    latch = latch_dir / f".{iface}.roam-policy.latched"
    if policy.is_symlink() or latch.is_symlink():
        return False
    if not policy.is_file() or not latch.is_file():
        return False
    if policy.stat().st_uid != os.geteuid() or latch.stat().st_uid != os.geteuid():
        return False
    try:
        data = json.loads(policy.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    extras = data.get("extra_ssids")
    return (
        type(data.get("version")) is int and data["version"] == 1
        and data.get("iface") == iface
        and type(data.get("roaming_enabled")) is bool
        and type(data.get("bgscan_enabled")) is bool
        and data.get("generate_network_blocks") is False
        and isinstance(extras, list)
        and all(
            isinstance(value, str)
            and 1 <= len(value.encode("utf-8")) <= 32
            and all(ord(char) >= 0x20 and ord(char) != 0x7f for char in value)
            for value in extras
        )
    )


def read_profile(path):
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
            raise ValueError("unsafe_profile")
        if stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError("unsafe_profile_mode")
        with os.fdopen(fd, "rb", closefd=False) as profile:
            return profile.read(), info
    finally:
        os.close(fd)


def atomic_install(path, content, original):
    staged_fd, staged_name = tempfile.mkstemp(prefix=f".{path.name}.raw.", dir=path.parent)
    try:
        os.fchmod(staged_fd, 0o600)
        if (original.st_uid, original.st_gid) != (os.geteuid(), os.getegid()):
            os.fchown(staged_fd, original.st_uid, original.st_gid)
        with os.fdopen(staged_fd, "wb", closefd=False) as staged:
            staged.write(content)
            staged.flush()
            os.fsync(staged.fileno())
        os.replace(staged_name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        os.close(staged_fd)
        if os.path.exists(staged_name):
            os.unlink(staged_name)


def commit(iface, network_id, ssid, expected_sha, trace, old_bssid,
           require_new_bssid, current_request=None):
    run_dir = Path(os.environ.get("WIFI_RUN_DIR", "/run/wifi"))
    conf_dir = Path(os.environ.get("WPA_CONF_DIR", "/etc/wpa_supplicant"))
    conf = conf_dir / f"wpa_supplicant-{iface}.conf"
    wpa_cli = os.environ.get("WIFI_RAW_COMMIT_WPA_CLI", "/usr/sbin/wpa_cli")
    timeout = min(15.0, max(0.1, float(os.environ.get("WIFI_RAW_COMMIT_TIMEOUT_SEC", "15"))))
    interval = min(1.0, max(0.01, float(os.environ.get("WIFI_RAW_COMMIT_POLL_SEC", "0.1"))))
    deadline = time.monotonic() + timeout
    last_control_error = None
    while time.monotonic() < deadline:
        if current_request is not None and not current_request():
            log_result(trace, iface, "skipped", "superseded")
            return 10
        try:
            connected = target_is_connected(
                wpa_cli, iface, network_id, ssid, old_bssid, require_new_bssid)
            last_control_error = None
        except WpaCliFailure as error:
            last_control_error = str(error)
            time.sleep(interval)
            continue
        if not connected:
            time.sleep(interval)
            continue
        change_fd = lock_file(run_dir / f"{iface}.change.lock")
        if change_fd is None:
            time.sleep(interval)
            continue
        try:
            profile_fd = lock_file(run_dir / f"{iface}.wpa-conf.lock")
            if profile_fd is None:
                time.sleep(interval)
                continue
            try:
                if current_request is not None and not current_request():
                    log_result(trace, iface, "skipped", "superseded")
                    return 10
                try:
                    profile_matches = target_is_connected(
                        wpa_cli, iface, network_id, ssid, old_bssid,
                        require_new_bssid, check_profile=True)
                    last_control_error = None
                except WpaCliFailure as error:
                    last_control_error = str(error)
                    continue
                if not profile_matches:
                    continue
                content, info = read_profile(conf)
                if hashlib.sha256(content).hexdigest() != expected_sha:
                    log_result(trace, iface, "skipped", "profile_changed")
                    return 7
                if not topology_supported(run_dir, iface):
                    log_result(trace, iface, "skipped", "mode_or_topology")
                    return 6
                try:
                    updated = single_network_profile(content, ssid)
                except ValueError:
                    log_result(trace, iface, "skipped", "mode_or_topology")
                    return 6
                marker = None
                if iface == "mlan0":
                    marker = Path(os.environ.get(
                        "OPC_ESSID_MARKER", "/usr/local/opc/etc/changeip_essid.restore"))
                    if os.path.lexists(marker):
                        marker_info = marker.lstat()
                        if (not stat.S_ISREG(marker_info.st_mode)
                                or marker_info.st_uid != os.geteuid()):
                            log_result(trace, iface, "skipped", "unsafe_restore_marker")
                            return 7
                    else:
                        marker = None
                if updated != content:
                    atomic_install(conf, updated, info)
                if marker is not None:
                    marker.unlink()
                    directory_fd = os.open(marker.parent, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                log_result(trace, iface, "saved")
                return 0
            finally:
                os.close(profile_fd)
        finally:
            os.close(change_fd)
    if last_control_error is not None:
        log_result(trace, iface, "failed", last_control_error)
        return 1
    log_result(trace, iface, "skipped", "association_timeout")
    return 8


def valid_request_fields(fields):
    if len(fields) != 6:
        return False
    network_id, ssid, expected_sha, trace, old_bssid, require_new = fields
    return (
        network_id.isdigit()
        and SSID_RE.fullmatch(ssid) is not None
        and re.fullmatch(r"[0-9a-f]{64}", expected_sha) is not None
        and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", trace) is not None
        and (old_bssid == "-" or re.fullmatch(
            r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", old_bssid) is not None)
        and require_new in ("0", "1")
    )


def request_identity(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    return (info.st_dev, info.st_ino)


def read_request(path):
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
                or info.st_size > 512):
            raise ValueError("unsafe_request")
        data = os.read(fd, 513)
    finally:
        os.close(fd)
    if len(data) > 512 or not data.endswith(b"\n"):
        raise ValueError("invalid_request")
    try:
        lines = data.decode("ascii").splitlines()
    except UnicodeDecodeError as error:
        raise ValueError("invalid_request") from error
    if len(lines) != 7 or lines[0] != "wifi-raw-commit-v1" or not valid_request_fields(lines[1:]):
        raise ValueError("invalid_request")
    return lines[1:], (info.st_dev, info.st_ino)


def clear_request_if_same(path, identity, run_dir, iface):
    for _ in range(40):
        change_fd = lock_file(run_dir / f"{iface}.change.lock")
        if change_fd is not None:
            try:
                if request_identity(path) != identity:
                    return False
                path.unlink()
                return True
            finally:
                os.close(change_fd)
        time.sleep(0.05)
    raise TimeoutError("change_lock_busy")


def from_request(iface):
    run_dir = Path(os.environ.get("WIFI_RUN_DIR", "/run/wifi"))
    path = run_dir / f"{iface}.raw-commit"
    while True:
        identity = request_identity(path)
        if identity is None:
            return 0
        try:
            fields, read_identity = read_request(path)
        except (OSError, ValueError) as error:
            log_result("invalid", iface, "failed", type(error).__name__)
            if clear_request_if_same(path, identity, run_dir, iface):
                return 1
            continue
        if identity != read_identity:
            continue
        network_id, ssid, expected_sha, trace, old_bssid, require_new = fields
        try:
            result = commit(
                iface, network_id, ssid, expected_sha, trace, old_bssid,
                require_new == "1",
                current_request=lambda: request_identity(path) == identity,
            )
        except (OSError, ValueError, TimeoutError, subprocess.TimeoutExpired) as error:
            log_result(trace, iface, "failed", type(error).__name__)
            result = 1
        if clear_request_if_same(path, identity, run_dir, iface):
            return result


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--from-request":
        iface = sys.argv[2]
        if iface not in ("mlan0", "mlan1"):
            return 2
        try:
            return from_request(iface)
        except (OSError, ValueError, TimeoutError, subprocess.TimeoutExpired) as error:
            log_result("request", iface, "failed", type(error).__name__)
            return 1
    if len(sys.argv) != 8:
        return 2
    iface, network_id, ssid, expected_sha, trace, old_bssid, require_new = sys.argv[1:]
    if iface not in ("mlan0", "mlan1") or not valid_request_fields(sys.argv[2:]):
        return 2
    try:
        return commit(iface, network_id, ssid, expected_sha, trace,
                      old_bssid, require_new == "1")
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        log_result(trace, iface, "failed", type(error).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
