#!/usr/bin/env python3
"""Read or atomically update the permanent factory-reset topology selector."""

import argparse
import errno
import fcntl
import hashlib
import json
import os
import secrets
import stat
import sys
from typing import Any, Dict, Iterable, List, Optional, Tuple


DEFAULT_TOPOLOGY = "eth0-ip"
VALID_TOPOLOGIES = frozenset(("eth0-ip", "peer-route"))
MAX_SELECTOR_BYTES = 4096


class SelectorError(Exception):
    """The selector could not be safely read or durably updated."""


def _strict_object(pairs: Iterable[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SelectorError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _parse_selector(raw: bytes) -> str:
    if len(raw) > MAX_SELECTOR_BYTES:
        raise SelectorError("selector is too large")
    try:
        text = raw.decode("utf-8")
        document = json.loads(text, object_pairs_hook=_strict_object)
    except UnicodeDecodeError as exc:
        raise SelectorError("selector is not valid UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise SelectorError("selector is not valid JSON") from exc

    if not isinstance(document, dict):
        raise SelectorError("selector must be a JSON object")
    if set(document) != {"schema_version", "topology"}:
        raise SelectorError("selector must contain exactly schema_version and topology")
    schema_version = document["schema_version"]
    if type(schema_version) is not int or schema_version != 1:
        raise SelectorError("schema_version must be integer 1")
    topology = document["topology"]
    if type(topology) is not str or topology not in VALID_TOPOLOGIES:
        raise SelectorError("topology must be eth0-ip or peer-route")
    return topology


def _validate_regular_file(metadata: os.stat_result, owner_uid: int, owner_gid: int) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise SelectorError("selector path is not a regular file")
    if metadata.st_nlink != 1:
        raise SelectorError("selector must have exactly one hard link")
    if metadata.st_uid != owner_uid or metadata.st_gid != owner_gid:
        raise SelectorError("selector owner does not match the required owner")
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise SelectorError("selector mode must be 0600")


def _read_fd(fd: int, owner_uid: int, owner_gid: int) -> str:
    metadata = os.fstat(fd)
    _validate_regular_file(metadata, owner_uid, owner_gid)
    if metadata.st_size > MAX_SELECTOR_BYTES:
        raise SelectorError("selector is too large")
    raw = os.read(fd, MAX_SELECTOR_BYTES + 1)
    if len(raw) > MAX_SELECTOR_BYTES:
        raise SelectorError("selector is too large")
    return _parse_selector(raw)


def _snapshot_identity(metadata: os.stat_result) -> Tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _read_snapshot_fd(
    fd: int, owner_uid: int, owner_gid: int
) -> Tuple[str, str]:
    before = os.fstat(fd)
    _validate_regular_file(before, owner_uid, owner_gid)
    if before.st_size > MAX_SELECTOR_BYTES:
        raise SelectorError("selector is too large")

    os.lseek(fd, 0, os.SEEK_SET)
    chunks: List[bytes] = []
    remaining = MAX_SELECTOR_BYTES + 1
    while remaining > 0:
        chunk = os.read(fd, remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    raw = b"".join(chunks)
    after = os.fstat(fd)
    if _snapshot_identity(before) != _snapshot_identity(after) or len(raw) != before.st_size:
        raise SelectorError("selector changed during snapshot")
    topology = _parse_selector(raw)
    identity = json.dumps(_snapshot_identity(before), separators=(",", ":")).encode("ascii")
    digest = hashlib.sha256(b"wifi-factory-topology-v1\0" + identity + b"\0" + raw)
    return topology, "v1-" + digest.hexdigest()


def _split_path(path: str) -> Tuple[str, str]:
    absolute = os.path.abspath(path)
    parent = os.path.dirname(absolute)
    name = os.path.basename(absolute)
    if not name or name in (".", ".."):
        raise SelectorError("selector path must name a file")
    return parent, name


def _open_parent(parent: str, owner_uid: int, owner_gid: int) -> int:
    if os.path.realpath(parent) != parent:
        raise SelectorError("selector parent must not contain symlinks")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(parent, flags)
    except OSError as exc:
        raise SelectorError(f"cannot open selector parent: {exc.strerror}") from exc
    metadata = os.fstat(fd)
    if not stat.S_ISDIR(metadata.st_mode):
        os.close(fd)
        raise SelectorError("selector parent is not a directory")
    if metadata.st_uid != owner_uid or metadata.st_gid != owner_gid:
        os.close(fd)
        raise SelectorError("selector parent owner does not match the required owner")
    if stat.S_IMODE(metadata.st_mode) & 0o022:
        os.close(fd)
        raise SelectorError("selector parent must not be group/world writable")
    return fd


def _open_existing_at(parent_fd: int, name: str) -> int:
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        return os.open(name, flags, dir_fd=parent_fd)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise SelectorError(f"cannot open selector: {exc.strerror}") from exc


def _parent_is_genuinely_missing(parent: str) -> bool:
    current = os.path.sep
    for component in parent.split(os.path.sep):
        if not component:
            continue
        current = os.path.join(current, component)
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            return True
        except OSError:
            return False
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            return False
    return False


def read_topology(path: str, owner_uid: int = 0, owner_gid: int = 0) -> str:
    parent, name = _split_path(path)
    try:
        parent_fd = _open_parent(parent, owner_uid, owner_gid)
    except SelectorError as parent_error:
        if _parent_is_genuinely_missing(parent):
            return DEFAULT_TOPOLOGY
        raise parent_error

    try:
        try:
            fd = _open_existing_at(parent_fd, name)
        except FileNotFoundError:
            return DEFAULT_TOPOLOGY
        try:
            return _read_fd(fd, owner_uid, owner_gid)
        finally:
            os.close(fd)
    finally:
        os.close(parent_fd)


def snapshot_topology(
    path: str, owner_uid: int = 0, owner_gid: int = 0
) -> Dict[str, str]:
    parent, name = _split_path(path)
    try:
        parent_fd = _open_parent(parent, owner_uid, owner_gid)
    except SelectorError as parent_error:
        if _parent_is_genuinely_missing(parent):
            return {"topology": DEFAULT_TOPOLOGY, "generation": "absent"}
        raise parent_error

    try:
        try:
            fd = _open_existing_at(parent_fd, name)
        except FileNotFoundError:
            return {"topology": DEFAULT_TOPOLOGY, "generation": "absent"}
        try:
            topology, generation = _read_snapshot_fd(fd, owner_uid, owner_gid)
            return {"topology": topology, "generation": generation}
        finally:
            os.close(fd)
    finally:
        os.close(parent_fd)


def _open_lock(parent_fd: int, name: str, owner_uid: int, owner_gid: int) -> int:
    lock_name = f".{name}.lock"
    flags = os.O_RDWR | os.O_CLOEXEC | os.O_NONBLOCK
    flags |= getattr(os, "O_NOFOLLOW", 0)
    created = False
    try:
        fd = os.open(lock_name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent_fd)
        created = True
    except FileExistsError:
        try:
            fd = os.open(lock_name, flags, dir_fd=parent_fd)
        except OSError as exc:
            raise SelectorError(f"cannot open selector lock: {exc.strerror}") from exc
    except OSError as exc:
        raise SelectorError(f"cannot create selector lock: {exc.strerror}") from exc

    try:
        if created:
            os.fchmod(fd, 0o600)
            if os.fstat(fd).st_uid != owner_uid or os.fstat(fd).st_gid != owner_gid:
                os.fchown(fd, owner_uid, owner_gid)
        try:
            _validate_regular_file(os.fstat(fd), owner_uid, owner_gid)
        except SelectorError as exc:
            raise SelectorError(f"unsafe selector lock: {exc}") from exc
        return fd
    except BaseException:
        os.close(fd)
        raise


def _same_inode(first: os.stat_result, second: os.stat_result) -> bool:
    return first.st_dev == second.st_dev and first.st_ino == second.st_ino


def _validate_borrowed_lock(
    parent_fd: int,
    name: str,
    borrowed_fd: int,
    owner_uid: int,
    owner_gid: int,
) -> None:
    lock_name = f".{name}.lock"
    try:
        borrowed_metadata = os.fstat(borrowed_fd)
    except OSError as exc:
        raise SelectorError(f"cannot inspect borrowed selector lock: {exc.strerror}") from exc
    try:
        _validate_regular_file(borrowed_metadata, owner_uid, owner_gid)
    except SelectorError as exc:
        raise SelectorError(f"unsafe borrowed selector lock: {exc}") from exc

    try:
        path_metadata = os.stat(lock_name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as exc:
        raise SelectorError(f"cannot inspect selector lock path: {exc.strerror}") from exc
    try:
        _validate_regular_file(path_metadata, owner_uid, owner_gid)
    except SelectorError as exc:
        raise SelectorError(f"unsafe selector lock path: {exc}") from exc
    if not _same_inode(borrowed_metadata, path_metadata):
        raise SelectorError("borrowed FD is not the selector lock file")

    flags = os.O_RDWR | os.O_CLOEXEC | os.O_NONBLOCK
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        probe_fd = os.open(lock_name, flags, dir_fd=parent_fd)
    except OSError as exc:
        raise SelectorError(f"cannot open selector lock probe: {exc.strerror}") from exc
    try:
        probe_metadata = os.fstat(probe_fd)
        if not _same_inode(borrowed_metadata, probe_metadata):
            raise SelectorError("selector lock path changed during validation")

        try:
            fcntl.flock(probe_fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN):
                raise SelectorError(f"cannot probe selector lock: {exc.strerror}") from exc
        else:
            fcntl.flock(probe_fd, fcntl.LOCK_UN)
            raise SelectorError("borrowed selector lock is not already held exclusively")

        try:
            fcntl.flock(borrowed_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                raise SelectorError("selector lock is held by an unrelated file description") from exc
            raise SelectorError(f"cannot verify borrowed selector lock: {exc.strerror}") from exc
    finally:
        os.close(probe_fd)


def _read_existing_for_set(
    parent_fd: int, name: str, owner_uid: int, owner_gid: int
) -> None:
    try:
        fd = _open_existing_at(parent_fd, name)
    except FileNotFoundError:
        return
    try:
        _read_fd(fd, owner_uid, owner_gid)
    finally:
        os.close(fd)


def _create_temp(
    parent_fd: int, name: str, owner_uid: int, owner_gid: int
) -> Tuple[int, str]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    for _ in range(32):
        temp_name = f".{name}.tmp.{os.getpid()}.{secrets.token_hex(8)}"
        try:
            fd = os.open(temp_name, flags, 0o600, dir_fd=parent_fd)
        except FileExistsError:
            continue
        try:
            os.fchmod(fd, 0o600)
            if os.fstat(fd).st_uid != owner_uid or os.fstat(fd).st_gid != owner_gid:
                os.fchown(fd, owner_uid, owner_gid)
            _validate_regular_file(os.fstat(fd), owner_uid, owner_gid)
            return fd, temp_name
        except BaseException:
            os.close(fd)
            try:
                os.unlink(temp_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
            raise
    raise SelectorError("cannot allocate an exclusive selector temp file")


def _write_all(fd: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(fd, content[offset:])
        if written <= 0:
            raise SelectorError("selector temp write made no progress")
        offset += written


def set_topology(
    path: str,
    topology: str,
    owner_uid: int = 0,
    owner_gid: int = 0,
    borrowed_lock_fd: Optional[int] = None,
) -> str:
    if topology not in VALID_TOPOLOGIES:
        raise SelectorError("topology must be eth0-ip or peer-route")
    parent, name = _split_path(path)
    parent_fd = _open_parent(parent, owner_uid, owner_gid)
    lock_fd = -1
    owns_lock_fd = False
    temp_fd = -1
    temp_name = ""
    replaced = False
    try:
        if borrowed_lock_fd is None:
            lock_fd = _open_lock(parent_fd, name, owner_uid, owner_gid)
            owns_lock_fd = True
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
        else:
            lock_fd = borrowed_lock_fd
            _validate_borrowed_lock(
                parent_fd, name, lock_fd, owner_uid, owner_gid
            )
        _read_existing_for_set(parent_fd, name, owner_uid, owner_gid)

        temp_fd, temp_name = _create_temp(parent_fd, name, owner_uid, owner_gid)
        content = json.dumps(
            {"schema_version": 1, "topology": topology},
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("ascii") + b"\n"
        _write_all(temp_fd, content)
        os.fsync(temp_fd)
        os.close(temp_fd)
        temp_fd = -1
        os.replace(temp_name, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        replaced = True
        try:
            os.fsync(parent_fd)
        except OSError as exc:
            raise SelectorError(
                "selector is visible but directory fsync failed; durability is unconfirmed"
            ) from exc
        return topology
    finally:
        if temp_fd >= 0:
            os.close(temp_fd)
        if temp_name and not replaced:
            try:
                os.unlink(temp_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
        if owns_lock_fd and lock_fd >= 0:
            os.close(lock_fd)
        os.close(parent_fd)


def _nonnegative_id(value: str) -> int:
    try:
        parsed = int(value, 10)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a decimal integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("read", "snapshot", "set"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--path", required=True)
        subparser.add_argument("--owner-uid", type=_nonnegative_id, default=0)
        subparser.add_argument("--owner-gid", type=_nonnegative_id, default=0)
        if command == "set":
            subparser.add_argument("--topology", choices=sorted(VALID_TOPOLOGIES), required=True)
            subparser.add_argument("--lock-fd", type=_nonnegative_id)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "read":
            topology = read_topology(args.path, args.owner_uid, args.owner_gid)
        elif args.command == "snapshot":
            snapshot = snapshot_topology(args.path, args.owner_uid, args.owner_gid)
            print(json.dumps(snapshot, separators=(",", ":")))
            return 0
        else:
            topology = set_topology(
                args.path,
                args.topology,
                args.owner_uid,
                args.owner_gid,
                args.lock_fd,
            )
    except (SelectorError, OSError) as exc:
        detail = exc.strerror if isinstance(exc, OSError) else str(exc)
        print(f"Error: {detail}", file=sys.stderr)
        return 1
    print(topology)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
