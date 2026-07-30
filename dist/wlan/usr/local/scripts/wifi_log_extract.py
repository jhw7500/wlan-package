#!/usr/bin/env python3
"""Extract timestamped Wi-Fi log records into a cp-style directory."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Optional, Sequence, Tuple


TIME_ONLY_RE = re.compile(r"^\d{2}:\d{2}(?::\d{2})?$")
DATE_TIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?$"
)
LOG_TIMESTAMP_RE = re.compile(
    rb"^(?:\[\s*|=+\s*)?"
    rb"(?P<date>\d{4}-\d{2}-\d{2})[ T]"
    rb"(?P<time>\d{2}:\d{2}:\d{2})(?:\.\d+)?"
)


class RangeError(ValueError):
    """Raised when a requested log time range is invalid."""


def parse_datetime(value: str, today: date) -> datetime:
    """Parse a full datetime or combine a time-only value with ``today``."""
    value = value.strip()
    if TIME_ONLY_RE.fullmatch(value):
        value = f"{today.isoformat()} {value}"
    elif DATE_TIME_RE.fullmatch(value):
        value = value.replace("T", " ", 1)
    else:
        raise RangeError(
            f"invalid datetime '{value}' "
            "(expected HH:MM[:SS] or YYYY-MM-DD HH:MM[:SS])"
        )

    fmt = "%Y-%m-%d %H:%M:%S" if value.count(":") == 2 else "%Y-%m-%d %H:%M"
    try:
        return datetime.strptime(value, fmt)
    except ValueError as exc:
        raise RangeError(f"invalid datetime '{value}': {exc}") from exc


def normalize_range(
    start_value: str,
    end_value: str,
    today: Optional[date] = None,
) -> Tuple[datetime, datetime]:
    """Normalize a start/end pair, using one consistent local date."""
    base_date = today or datetime.now().astimezone().date()
    start = parse_datetime(start_value, base_date)
    end = parse_datetime(end_value, base_date)
    if end < start:
        raise RangeError(
            f"end datetime ({end:%Y-%m-%d %H:%M:%S}) is before "
            f"start datetime ({start:%Y-%m-%d %H:%M:%S})"
        )
    return start, end


def default_output_dir(iface: str, start: datetime, end: datetime) -> str:
    """Return a filesystem-safe default directory name for a time range."""
    return (
        f"{iface}_log_"
        f"{start:%Y%m%d_%H%M%S}-"
        f"{end:%Y%m%d_%H%M%S}"
    )


def extract_file(
    source: Path,
    destination: Path,
    start: datetime,
    end: datetime,
) -> int:
    """Copy records in the inclusive range and return written byte count.

    A timestamped line starts a record. Untimestamped lines following it are
    kept with that record, which preserves ap.log/freq.log/snap.log blocks.
    """
    start_key = start.strftime("%Y-%m-%d %H:%M:%S").encode("ascii")
    end_key = end.strftime("%Y-%m-%d %H:%M:%S").encode("ascii")
    include_record = False
    written = 0

    with source.open("rb") as reader, destination.open("wb") as writer:
        for line in reader:
            match = LOG_TIMESTAMP_RE.match(line)
            if match:
                timestamp = match.group("date") + b" " + match.group("time")
                include_record = start_key <= timestamp <= end_key
            if include_record:
                writer.write(line)
                written += len(line)

    return written


def extract_logs(
    sources: Iterable[Path],
    output_dir: Path,
    start: datetime,
    end: datetime,
) -> Tuple[int, int]:
    """Extract all sources, returning (file count, non-empty file count)."""
    source_list = list(sources)
    names = [source.name for source in source_list]
    if len(names) != len(set(names)):
        raise ValueError("source log basenames must be unique")

    resolved_output = output_dir.resolve()
    for source in source_list:
        destination = resolved_output / source.name
        if source.resolve() == destination.resolve():
            raise ValueError(
                f"output directory would overwrite source log: {source}"
            )

    output_dir.mkdir(parents=True, exist_ok=True)
    nonempty = 0
    for source in source_list:
        written = extract_file(source, output_dir / source.name, start, end)
        if written:
            nonempty += 1
    return len(source_list), nonempty


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Extract timestamped log records. Time-only values use today's "
            "local date; range endpoints are inclusive."
        )
    )
    parser.add_argument("--iface", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output")
    parser.add_argument("sources", nargs="+", type=Path)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        start, end = normalize_range(args.start, args.end)
        output_dir = Path(
            args.output or default_output_dir(args.iface, start, end)
        )
        file_count, nonempty_count = extract_logs(
            args.sources, output_dir, start, end
        )
    except (OSError, RangeError, ValueError) as exc:
        print(f"Error: log extract failed: {exc}", file=sys.stderr)
        return 2

    print(
        f"Extracted {file_count} log(s) for {args.iface} "
        f"from {start:%Y-%m-%d %H:%M:%S} "
        f"to {end:%Y-%m-%d %H:%M:%S} "
        f"into {output_dir.resolve()}"
    )
    if nonempty_count != file_count:
        print(
            f"No matching records in {file_count - nonempty_count} log(s); "
            "empty files were created."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
