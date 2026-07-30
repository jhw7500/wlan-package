import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import pytest


SCRIPT_DIR = Path(__file__).resolve().parents[1]
SCRIPT = SCRIPT_DIR / "wifi_log_extract.py"
sys.path.insert(0, str(SCRIPT_DIR))

import wifi_log_extract


def test_time_only_uses_supplied_current_date():
    start, end = wifi_log_extract.normalize_range(
        "13:03", "13:23:45", today=date(2026, 7, 30)
    )

    assert start == datetime(2026, 7, 30, 13, 3, 0)
    assert end == datetime(2026, 7, 30, 13, 23, 45)


def test_full_datetime_does_not_use_current_date():
    start, end = wifi_log_extract.normalize_range(
        "2026-07-21 23:59", "2026-07-22 00:01:00",
        today=date(2030, 1, 1),
    )

    assert start == datetime(2026, 7, 21, 23, 59)
    assert end == datetime(2026, 7, 22, 0, 1)


@pytest.mark.parametrize(
    "start,end",
    [
        ("25:00", "26:00"),
        ("2026-02-30 10:00", "2026-02-30 11:00"),
        ("2026/07/30 10:00", "2026-07-30 11:00"),
        ("14:00", "13:00"),
    ],
)
def test_invalid_ranges_are_rejected(start, end):
    with pytest.raises(wifi_log_extract.RangeError):
        wifi_log_extract.normalize_range(
            start, end, today=date(2026, 7, 30)
        )


def test_extract_preserves_plain_and_legacy_multiline_records(tmp_path):
    source = tmp_path / "ap.log"
    source.write_text(
        "[2026-07-30 12:59:59]\n"
        "old body\n"
        "2026-07-30 13:00:00\n"
        "new body 1\n"
        "new body 2\n"
        "===== 2026-07-30 13:10:00 =====\n"
        "legacy snap body\n"
        "[2026-07-30 13:20:01]\n"
        "late body\n"
    )
    destination = tmp_path / "out.log"

    written = wifi_log_extract.extract_file(
        source,
        destination,
        datetime(2026, 7, 30, 13, 0),
        datetime(2026, 7, 30, 13, 20),
    )

    expected = (
        "2026-07-30 13:00:00\n"
        "new body 1\n"
        "new body 2\n"
        "===== 2026-07-30 13:10:00 =====\n"
        "legacy snap body\n"
    )
    assert destination.read_text() == expected
    assert written == len(expected.encode())


def test_extract_includes_millisecond_lines_at_end_second(tmp_path):
    source = tmp_path / "logger.log"
    source.write_text(
        "2026-07-30 13:19:59.999 before\n"
        "2026-07-30 13:20:00.999 included\n"
        "2026-07-30 13:20:01.000 excluded\n"
    )
    destination = tmp_path / "out.log"

    wifi_log_extract.extract_file(
        source,
        destination,
        datetime(2026, 7, 30, 13, 20),
        datetime(2026, 7, 30, 13, 20),
    )

    assert destination.read_text() == (
        "2026-07-30 13:20:00.999 included\n"
    )


def test_extract_logs_creates_empty_files_for_no_match(tmp_path):
    source = tmp_path / "stat.log"
    source.write_text("2026-07-30 12:00:00 MAC:aa:bb:cc:dd:ee:ff\n")
    output = tmp_path / "result"

    count, nonempty = wifi_log_extract.extract_logs(
        [source],
        output,
        datetime(2026, 7, 30, 13, 0),
        datetime(2026, 7, 30, 14, 0),
    )

    assert (count, nonempty) == (1, 0)
    assert (output / "stat.log").read_bytes() == b""


def test_extract_logs_refuses_to_overwrite_source(tmp_path):
    source = tmp_path / "stat.log"
    source.write_text("2026-07-30 13:00:00 data\n")

    with pytest.raises(ValueError, match="overwrite source"):
        wifi_log_extract.extract_logs(
            [source],
            tmp_path,
            datetime(2026, 7, 30, 13, 0),
            datetime(2026, 7, 30, 14, 0),
        )

    assert source.read_text() == "2026-07-30 13:00:00 data\n"


def test_cli_time_only_uses_local_today(tmp_path):
    source = tmp_path / "logger.log"
    today = datetime.now().astimezone().date().isoformat()
    source.write_text(
        f"{today} 13:00:00 first\n"
        f"{today} 13:01:00 second\n"
    )
    output = tmp_path / "extracted"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--iface",
            "mlan0",
            "--start",
            "13:00",
            "--end",
            "13:00",
            "--output",
            str(output),
            str(source),
        ],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert (output / "logger.log").read_text() == (
        f"{today} 13:00:00 first\n"
    )
