import os
import subprocess
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("sUTILS", MagicMock())
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import wifi_logger_link
import wifi_logger_scan
import wifi_logger_stat

wifi_logger_link.logger = MagicMock()
wifi_logger_scan.logger = MagicMock()
wifi_logger_stat.logger = MagicMock()


def _timeout(*args, **kwargs):
    raise subprocess.TimeoutExpired(args[0] if args else "cmd", kwargs.get("timeout", 1))


def test_scan_getscantable_timeout_is_bounded(monkeypatch):
    run = MagicMock(side_effect=_timeout)
    monkeypatch.setattr(wifi_logger_scan.subprocess, "run", run)
    monkeypatch.setattr(wifi_logger_scan.time, "sleep", lambda _: None)
    assert wifi_logger_scan.run_getscantable(retries=2, delay=0) == []
    assert run.call_count == 2
    assert all(call.kwargs["timeout"] == wifi_logger_scan.SCAN_COMMAND_TIMEOUT_S for call in run.call_args_list)


def test_scan_iw_dump_timeout_returns_err(monkeypatch):
    run = MagicMock(side_effect=_timeout)
    monkeypatch.setattr(wifi_logger_scan.subprocess, "run", run)
    monkeypatch.setattr(wifi_logger_scan.time, "sleep", lambda _: None)
    assert wifi_logger_scan.run_iwdevscandump(retries=2, delay=0) == "err"
    assert run.call_count == 2


def test_setuserscan_timeout_returns_empty(monkeypatch):
    monkeypatch.setattr(wifi_logger_scan.subprocess, "run", _timeout)
    assert wifi_logger_scan.run_setuserscan() == []


def test_link_run_command_timeout_returns_empty(monkeypatch):
    run = MagicMock(side_effect=_timeout)
    monkeypatch.setattr(wifi_logger_link.subprocess, "run", run)
    assert wifi_logger_link.run_command(["iw", "mlan0", "info"]) == ""
    assert run.call_args.kwargs["timeout"] == wifi_logger_link.LINK_COMMAND_TIMEOUT_S


def test_link_retry_continues_after_timeout(monkeypatch):
    calls = iter(["", "Station aa:bb:cc:dd:ee:ff"])
    monkeypatch.setattr(wifi_logger_link, "run_command", lambda cmd: next(calls))
    monkeypatch.setattr(wifi_logger_link.time, "sleep", lambda _: None)
    out = wifi_logger_link.run_command_with_retry(
        ["iw", "mlan0", "station", "dump"], retries=2,
        validate_fn=wifi_logger_link.validate_station,
    )
    assert out.startswith("Station")


def test_stat_getlog_timeout_is_bounded(monkeypatch):
    run = MagicMock(side_effect=subprocess.TimeoutExpired("mlanutl", 5))
    monkeypatch.setattr(wifi_logger_stat.subprocess, "run", run)

    assert wifi_logger_stat.get_mlanutl_log("mlan0") == ""
    assert (
        run.call_args.kwargs["timeout"]
        == wifi_logger_stat.STAT_COMMAND_TIMEOUT_S
    )


def test_network_stats_reads_proc_without_popen(monkeypatch, tmp_path):
    proc = tmp_path / "netdev"
    proc.write_text("mlan0: 1 2 0 0 0 0 0 0 3 4\n")
    monkeypatch.setattr(wifi_logger_stat, "PROC_NET_DEV", str(proc))
    monkeypatch.setattr(wifi_logger_stat, "IFACE", "mlan0")

    assert wifi_logger_stat.get_network_stats() == {
        "rx_bytes": 1,
        "rx_packets": 2,
        "tx_bytes": 3,
        "tx_packets": 4,
    }
