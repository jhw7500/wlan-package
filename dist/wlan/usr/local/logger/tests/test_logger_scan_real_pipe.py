import os
import select
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("sUTILS", MagicMock())
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import wifi_logger_scan

wifi_logger_scan.logger = MagicMock()


class PipeProcess:
    def __init__(self, read_fd):
        self.stdout = os.fdopen(read_fd, "rb", buffering=0)

    def terminate(self):
        pass

    def wait(self, timeout=None):
        self.stdout.close()
        return 0

    def kill(self):
        pass


def test_two_events_in_one_pipe_write_are_drained_immediately():
    read_fd, write_fd = os.pipe()
    proc = PipeProcess(read_fd)
    payload = (
        b"mlan0 (phy #0): scan finished: 5180\n"
        b"mlan0 (phy #0): scan finished: 5240\n"
    )
    os.write(write_fd, payload)
    os.close(write_fd)
    events = []

    consumed = wifi_logger_scan.iw_scan_event(
        "mlan0", events.append,
        _popen=lambda *args, **kwargs: proc,
        _select=select.select,
        idle_timeout=1,
    )

    assert consumed == 2
    assert events == ["mlan0", "mlan0"]
