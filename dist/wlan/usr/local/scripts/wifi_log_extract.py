#!/usr/bin/env python3
"""Extract timestamped Wi-Fi log records.

The default CLI copies a time range into a cp-style directory
(`wifi <iface> log extract`). `today-archive` writes one day's records of the
given logs into a tar.gz that keeps their paths (`wifi log dump today`, #462).
"""

from __future__ import annotations

import argparse
import gzip
import os
import re
import sys
import tarfile
import tempfile
import zlib
from datetime import date, datetime, time as dtime
from pathlib import Path
from typing import BinaryIO, Iterable, Iterator, List, Optional, Sequence, Tuple


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


ROTATED_NAME_RE = re.compile(r"-(?P<ymd>\d{8})(?:\.gz)?$")


class SourceReadError(Exception):
    """Raised when a log source cannot be read to the end."""


def is_day_source(path: Path, mtime: float, day: date, day_start: float) -> bool:
    """Return whether ``path`` can hold records of ``day``.

    logrotate ``dateext`` names a rotated copy with the day it was rotated, so a
    copy named before ``day`` holds older records only, unless a writer kept
    appending to it after rotation (failed HUP). Its mtime then reaches ``day``.
    """
    match = ROTATED_NAME_RE.search(path.name)
    if not match:
        return True
    return match.group("ymd") >= day.strftime("%Y%m%d") or mtime >= day_start


def _checked_lines(reader: Iterable[bytes]) -> Iterator[bytes]:
    try:
        yield from reader
    except (OSError, EOFError, zlib.error) as exc:
        raise SourceReadError(str(exc)) from exc


def copy_day_records(reader: Iterable[bytes], writer: BinaryIO, day: date) -> int:
    """Copy records dated ``day`` and return the written byte count.

    As in ``extract_file``, an untimestamped line belongs to the record above it.
    """
    day_key = day.isoformat().encode("ascii")
    include = False
    written = 0
    for line in reader:
        match = LOG_TIMESTAMP_RE.match(line)
        if match:
            include = match.group("date") == day_key
        if include:
            writer.write(line)
            written += len(line)
    return written


def write_day_archive(
    sources: Iterable[Path], base: Path, day: date, archive: Path
) -> Tuple[int, List[str]]:
    """Archive ``day``'s records of each source as ``<base name>/<relative path>``.

    Return (archived log count, warnings). A ``.gz`` source is read decompressed
    and stored without the suffix. A log without a record of ``day`` is left out.
    An unreadable source is skipped with a warning. The archive appears only when
    it is complete, with the mode a plain file would get under the umask.
    """
    day_start = datetime.combine(day, dtime()).timestamp()
    archive_dir = archive.parent
    archived = set()
    warnings: List[str] = []
    fd, tmp_name = tempfile.mkstemp(prefix=f".{archive.name}.", dir=archive_dir)
    os.close(fd)
    try:
        with tarfile.open(tmp_name, "w:gz") as tar:
            for source in sorted(set(sources)):
                rel = source.relative_to(base).as_posix()
                name = rel[:-3] if rel.endswith(".gz") else rel
                try:
                    mtime = source.stat().st_mtime
                    if not is_day_source(source, mtime, day, day_start):
                        continue
                    reader = (gzip.open if source.name.endswith(".gz") else open)(source, "rb")
                except OSError as exc:
                    warnings.append(f"unreadable log skipped: {rel} ({exc})")
                    continue
                if name in archived:
                    reader.close()
                    warnings.append(f"duplicate log name skipped: {rel}")
                    continue
                with reader, tempfile.TemporaryFile(dir=archive_dir) as spool:
                    try:
                        size = copy_day_records(_checked_lines(reader), spool, day)
                    except SourceReadError as exc:
                        warnings.append(f"unreadable log skipped: {rel} ({exc})")
                        continue
                    if not size:
                        continue
                    spool.seek(0)
                    info = tarfile.TarInfo(f"{base.name}/{name}")
                    info.size = size
                    info.mtime = int(mtime)
                    info.mode = 0o644
                    tar.addfile(info, spool)
                archived.add(name)
        umask = os.umask(0)
        os.umask(umask)
        os.chmod(tmp_name, 0o666 & ~umask)
        os.replace(tmp_name, archive)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return len(archived), warnings


def build_day_archive_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wifi_log_extract.py today-archive",
        description="Archive one day's records of the given logs into a tar.gz.",
    )
    parser.add_argument("--base", required=True, type=Path,
                        help="log root; members keep paths relative to it")
    parser.add_argument("--date", required=True, type=date.fromisoformat,
                        help="day to keep, YYYY-MM-DD")
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("sources", nargs="*", type=Path)
    return parser


def day_archive_main(argv: Sequence[str]) -> int:
    args = build_day_archive_parser().parse_args(argv)
    try:
        archived, warnings = write_day_archive(
            args.sources, args.base, args.date, args.archive
        )
    except (OSError, ValueError, tarfile.TarError) as exc:
        print(f"Error: log dump today failed: {exc}", file=sys.stderr)
        return 2
    for warning in warnings:
        print(f"Warning: {warning}", file=sys.stderr)
    print(
        f"Archived {args.date.isoformat()} records from {archived} log(s) "
        f"into {args.archive}"
    )
    return 0


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
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["today-archive"]:
        return day_archive_main(argv[1:])
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
