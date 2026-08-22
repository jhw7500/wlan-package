"""wifi_roam writer와 wifi_bgscan reader가 공유하는 iface별 PID lease."""

import os
import fcntl
from contextlib import contextmanager


def roam_state_paths(iface):
    return (
        f"/run/wifi/roam_condition_{iface}",
        f"/run/wifi/last_roam_scan_{iface}",
    )


@contextmanager
def scan_transition_lock(iface, run_dir=None):
    """Nonblocking per-interface live scan/association serialization lock.

    The lock path deliberately survives owners; kernel FD lifetime releases the
    advisory flock on normal exit, signals, and SIGKILL.
    """
    run_dir = run_dir or "/run/wifi"
    os.makedirs(run_dir, exist_ok=True)
    path = os.path.join(run_dir, f"{iface}.scan-transition.lock")
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    acquired = False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError:
            pass
        yield acquired
    finally:
        if acquired:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def process_start_time(pid):
    """Linux /proc/<pid>/stat field 22. PID 재사용 판별용."""
    try:
        data = open(f"/proc/{int(pid)}/stat", encoding="ascii").read()
        # comm(field 2)은 공백과 ')'를 포함할 수 있으므로 마지막 ')' 뒤부터 해석한다.
        after_comm = data.rsplit(")", 1)[1].split()
        return after_comm[19]
    except (OSError, ValueError, IndexError):
        return None


def _remove_if_unchanged(path, expected):
    try:
        with open(path, encoding="ascii") as stream:
            if stream.read().strip() != expected:
                return False
        os.unlink(path)
        return True
    except FileNotFoundError:
        return False


def write_flag(on, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp_path = f"{path}.tmp.{os.getpid()}"
    if on is True or on == 1:
        start = process_start_time(os.getpid())
        if start is None:
            raise RuntimeError("cannot read own process start time")
        content = f"1 {os.getpid()} {start}"
    elif on is False or on == 0:
        content = "0"
    else:
        content = ""
    try:
        with open(tmp_path, "w", encoding="ascii") as stream:
            stream.write(content)
        os.replace(tmp_path, path)
    finally:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass


def lease_active(path):
    try:
        with open(path, encoding="ascii") as stream:
            content = stream.read().strip()
    except FileNotFoundError:
        return False

    fields = content.split()
    if len(fields) == 3 and fields[0] == "1":
        pid, expected_start = fields[1], fields[2]
        if process_start_time(pid) == expected_start:
            return True
        _remove_if_unchanged(path, content)
        return False

    # 구버전 정수 플래그는 writer 생존 여부를 증명할 수 없으므로 stale로 폐기한다.
    if content == "1":
        _remove_if_unchanged(path, content)
    return False


def clear_stale_lease(path):
    try:
        with open(path, encoding="ascii") as stream:
            before = stream.read().strip()
    except FileNotFoundError:
        return False
    if lease_active(path):
        return False
    return before == "1" or (before.startswith("1 ") and not os.path.exists(path))


def clear_own_lease(path):
    start = process_start_time(os.getpid())
    if start is None:
        return False
    return _remove_if_unchanged(path, f"1 {os.getpid()} {start}")
