#!/usr/bin/python3
"""eMMC boot1 에 출하 고정값 서명 레코드를 기록한다(#481 PR-3).

양산 PC 도구가 SSH 로 부른다: `uid` 로 장치 UID 를 읽어 레코드를 만들어 서명하고, 그 파일을
장치에 올려 `write` 로 기록한다. 바이트 배치·판정 규칙·종료 코드의 정본은
docs/devinfo_record_format.md 의 "기록 도구" 절이다.

`write` 는 잠금(부팅 검증 서비스와 공유)을 쥔 채 다음을 한다. 앞 단계에서 거부하면 boot1 과
force_ro 를 건드리지 않는다.
1. 입력 레코드가 이 장치의 공개키·UID 로 유효한지 본다.
2. 루트FS 디스크가 mmcblk0 인지 본다(부팅 서비스가 읽는 boot1 과 같은 eMMC).
3. EXT_CSD PARTITION_CONFIG 가 boot0 부팅(Boot Partition 1)인지 본다.
4. boot1 앞 512 B 가 비었는지(0x00/0xFF), 같은 레코드인지, 다른 레코드(magic)인지 본다.
   다른 레코드는 --replace 가 있어야 덮어쓰고, 이전 512 B 를 hex 로 출력한다.
5. force_ro=0 → O_SYNC 쓰기 → 블록 캐시 비우기 → 되읽어 비교 → force_ro=1(항상).
6. 쓰기를 건너뛰었어도 부팅 서비스와 같은 판정으로 device_info.json 에 반영하고, 파일이 레코드
   값과 같은지 확인한 뒤 opcd 가 active 면 try-restart 한다(opcd 는 시작할 때 한 번 읽는다).
"""
import argparse
import contextlib
import dataclasses
import fcntl
import json
import os
import re
import signal
import subprocess
import sys

import devinfo_record as dr
import wifi_devinfo as wd

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_RECORD_INVALID = 3
EXIT_TARGET_REFUSED = 4
EXIT_REPLACE_REQUIRED = 5
EXIT_WRITE_FAILED = 6
EXIT_APPLY_FAILED = 7
EXIT_BUSY = 8
EXIT_UNDETERMINED = 9

EMMC_DISK = "mmcblk0"
EMMC_DEVICE = "/dev/mmcblk0"
FORCE_RO_PATH = "/sys/block/mmcblk0boot1/force_ro"
BLKFLSBUF = 0x1261  # <linux/fs.h> _IO(0x12, 97): 블록 장치 버퍼 캐시를 비운다
# PARTITION_CONFIG bits[5:3] BOOT_PARTITION_ENABLE: 1 = Boot Partition 1(리눅스 mmcblk0boot0),
# 2 = Boot Partition 2(mmcblk0boot1), 7 = user area. boot0 부팅일 때만 boot1 에 쓴다.
BOOT_PARTITION_1 = 1
_PARTITION_CONFIG = re.compile(r"\[PARTITION_CONFIG: 0x([0-9a-fA-F]{2})\]")
_EMPTY = (bytes(dr.RECORD_SIZE), b"\xff" * dr.RECORD_SIZE)
_TERMINATING = {signal.SIGHUP, signal.SIGTERM}


class Stop(Exception):
    """종료 코드와 함께 기록을 멈춘다."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _root_disk(sys_root="/sys", st_dev=None):
    """루트FS 가 있는 디스크 이름(예: mmcblk0). sysfs 노드가 없으면 OSError(확인 불가)."""
    if st_dev is None:
        st_dev = os.stat("/").st_dev
    node = os.path.realpath(os.path.join(sys_root, "dev/block", f"{os.major(st_dev)}:{os.minor(st_dev)}"))
    if not os.path.isdir(node):
        raise OSError(f"루트FS 블록 장치의 sysfs 노드가 없다({node})")
    if os.path.exists(os.path.join(node, "partition")):
        node = os.path.dirname(node)
    return os.path.basename(node)


def _read_extcsd():
    return subprocess.run(["mmc", "extcsd", "read", EMMC_DEVICE], capture_output=True, text=True,
                          timeout=30, check=True).stdout


def _flush_block_cache(path):
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    try:
        fcntl.ioctl(fd, BLKFLSBUF)
    finally:
        os.close(fd)


def _restart_opcd_if_active():
    """(active 였는지, try-restart 종료 코드)."""
    if subprocess.run(["systemctl", "is-active", "--quiet", "opcd.service"], check=False).returncode != 0:
        return False, 0
    return True, subprocess.run(["systemctl", "try-restart", "opcd.service"], check=False).returncode


@dataclasses.dataclass
class Target:
    record_device: str = wd.RECORD_DEVICE
    force_ro: str = FORCE_RO_PATH
    info_path: str = wd.DEVICE_INFO_PATH
    uid_path: "str | None" = None
    lock_path: str = wd.LOCK_PATH
    public_keys: dict = dataclasses.field(default_factory=lambda: wd.SHIPPING_PUBLIC_KEYS)
    root_disk: object = _root_disk
    extcsd: object = _read_extcsd
    flush: object = _flush_block_cache
    opcd: object = _restart_opcd_if_active


def boot_partition_enable(extcsd_text):
    """PARTITION_CONFIG 의 BOOT_PARTITION_ENABLE 값. 줄이 정확히 한 번 나오지 않으면 None."""
    found = _PARTITION_CONFIG.findall(extcsd_text)
    if len(found) != 1:
        return None
    return (int(found[0], 16) >> 3) & 0x7


def _read_force_ro(path):
    with open(path, encoding="ascii") as f:
        return f.read().strip()


def _set_force_ro(path, value):
    with open(path, "w", encoding="ascii") as f:
        f.write(value)
    got = _read_force_ro(path)
    if got != value:
        raise OSError(f"force_ro 에 {value} 를 썼지만 {got} 로 읽힌다({path})")


def _read_head(path):
    with open(path, "rb") as f:
        head = f.read(dr.RECORD_SIZE)
    if len(head) != dr.RECORD_SIZE:
        raise OSError(f"{path} 에서 {dr.RECORD_SIZE} 바이트를 읽지 못했다")
    return head


def _write_record(target, blob):
    # force_ro 를 푼 동안에는 SIGHUP·SIGTERM 을 막아 두었다가, 1 로 되돌린 뒤에 처리한다(SSH 단절,
    # 두 번째 시그널에도 복원을 건너뛰지 않는다). 해제도 try 안에서 해 확인이 실패해도 되돌린다.
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, _TERMINATING)
    try:
        try:
            _set_force_ro(target.force_ro, "0")
            fd = os.open(target.record_device, os.O_WRONLY | os.O_SYNC | os.O_CLOEXEC)
            try:
                written = os.pwrite(fd, blob, 0)
                if written != len(blob):
                    raise OSError(f"짧게 썼다({written} 바이트)")
                os.fsync(fd)
            finally:
                os.close(fd)
            target.flush(target.record_device)
            if _read_head(target.record_device) != blob:
                raise OSError("되읽은 512 바이트가 쓴 내용과 다르다")
        finally:
            _set_force_ro(target.force_ro, "1")
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


def _ensure_force_ro(target, log):
    """앞 실행이 비정상 종료로 force_ro=0 을 남겼으면 1 로 되돌린다(같은 레코드라 쓰지 않은 경우 포함)."""
    try:
        if _read_force_ro(target.force_ro) != "1":
            log("force_ro 가 0 으로 남아 있어 1 로 되돌린다")
            _set_force_ro(target.force_ro, "1")
    except OSError as exc:
        raise Stop(EXIT_WRITE_FAILED, f"force_ro 를 1 로 되돌리지 못했다: {exc}") from exc


def _uid_reader(target):
    return lambda: dr.read_device_uid(wd.DEVICE_UID_SOURCE, target.uid_path)


def _check_target(target):
    try:
        disk = target.root_disk()
    except OSError as exc:
        raise Stop(EXIT_UNDETERMINED, f"루트FS 디스크를 알 수 없다: {exc}") from exc
    if disk != EMMC_DISK:
        raise Stop(EXIT_TARGET_REFUSED, f"루트FS 디스크가 {disk} 이다({EMMC_DISK} 만 받는다)")
    try:
        text = target.extcsd()
    except FileNotFoundError as exc:
        raise Stop(EXIT_UNDETERMINED, "mmc-utils(mmc) 가 없어 PARTITION_CONFIG 를 확인할 수 없다") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise Stop(EXIT_UNDETERMINED, f"EXT_CSD 를 읽지 못했다: {exc}") from exc
    enable = boot_partition_enable(text)
    if enable != BOOT_PARTITION_1:
        raise Stop(EXIT_TARGET_REFUSED,
                   f"PARTITION_CONFIG 부트 파티션이 boot0 이 아니거나 확인할 수 없다(값 {enable})")


def _write_steps(blob, replace, target, out, log):
    try:
        result = dr.check(blob, target.public_keys, _uid_reader(target))
    except (ImportError, OSError) as exc:
        raise Stop(EXIT_UNDETERMINED, f"레코드를 판정하지 못했다({type(exc).__name__}: {exc})") from exc
    if result.status != dr.STATUS_VALID:
        reason = f"({result.reason})" if result.reason else ""
        raise Stop(EXIT_RECORD_INVALID, f"이 장치에서 유효한 레코드가 아니다: {result.status}{reason}")
    _check_target(target)
    try:
        # 앞 실행의 실패한 쓰기가 페이지 캐시에 남았을 수 있다: 캐시를 비우고 매체를 읽는다.
        target.flush(target.record_device)
        current = _read_head(target.record_device)
    except OSError as exc:
        raise Stop(EXIT_UNDETERMINED, f"boot1 을 읽지 못했다: {exc}") from exc

    if current == blob:
        action = "unchanged"
    elif current in _EMPTY:
        action = "written"
    elif current[:len(dr.MAGIC)] == dr.MAGIC:
        try:
            existing = dr.check(current, target.public_keys, _uid_reader(target))
            log(f"기존 레코드: {existing.status}" + (f"({existing.reason})" if existing.reason else ""))
        except (ImportError, OSError) as exc:
            log(f"기존 레코드: 판정 불가({type(exc).__name__}: {exc})")
        if not replace:
            raise Stop(EXIT_REPLACE_REQUIRED, "boot1 에 다른 레코드가 있다. 덮어쓰려면 --replace 를 준다")
        out(f"previous={current.hex()}")
        action = "written"
    else:
        raise Stop(EXIT_TARGET_REFUSED, "boot1 앞 512 바이트가 비어 있지 않고 레코드도 아니다")

    if action == "written":
        try:
            _write_record(target, blob)
        except OSError as exc:
            raise Stop(EXIT_WRITE_FAILED, f"boot1 기록 실패: {exc}") from exc
        log(f"boot1 에 기록했다({target.record_device})")
    _ensure_force_ro(target, log)

    if wd.run(target.record_device, target.info_path, uid_path=target.uid_path,
              public_keys=target.public_keys, log=log) != 0:
        raise Stop(EXIT_APPLY_FAILED, "device_info.json 반영이 실패했다")
    try:
        with open(target.info_path, encoding="utf-8") as f:
            applied = json.load(f)
    except (OSError, ValueError) as exc:
        raise Stop(EXIT_APPLY_FAILED, f"반영한 파일을 읽지 못했다: {exc}") from exc
    if applied != dr.device_info_json(result.record.values):
        raise Stop(EXIT_APPLY_FAILED, f"{target.info_path} 가 레코드 값과 다르다")
    return action


def _quiet(fn):
    def call(line):
        try:
            fn(line)
        except OSError:
            pass  # SSH 가 끊겨 출력이 실패해도(EPIPE) 기록·반영은 끝까지 한다
    return call


def cmd_write(record_path, replace, target, *, out, log):
    out, log = _quiet(out), _quiet(log)
    try:
        with open(record_path, "rb") as f:
            blob = f.read(dr.RECORD_SIZE + 1)
    except OSError as exc:
        log(f"레코드 파일을 읽지 못했다: {exc}")
        return EXIT_USAGE
    try:
        if len(blob) != dr.RECORD_SIZE:
            raise Stop(EXIT_RECORD_INVALID, f"레코드는 {dr.RECORD_SIZE} 바이트여야 한다")
        with contextlib.ExitStack() as stack:
            try:
                stack.enter_context(wd.locked(target.lock_path, blocking=False))
            except BlockingIOError as exc:
                raise Stop(EXIT_BUSY, f"다른 기록·판정이 진행 중이다({target.lock_path})") from exc
            except OSError as exc:
                raise Stop(EXIT_UNDETERMINED, f"잠금 파일을 쓸 수 없다({target.lock_path}: {exc})") from exc
            action = _write_steps(blob, replace, target, out, log)
        # opcd 재시작은 잠금을 푼 뒤에 한다. wifi_devinfo.service 가 Before=opcd.service 라, 잠금을
        # 기다리는 서비스 시작 작업이 큐에 있으면 opcd 시작이 그 작업을 기다려 서로 멈출 수 있다.
        try:
            active, rc = target.opcd()
        except (OSError, subprocess.SubprocessError) as exc:
            raise Stop(EXIT_APPLY_FAILED, f"opcd 재시작을 확인하지 못했다: {exc}") from exc
        if active and rc != 0:
            raise Stop(EXIT_APPLY_FAILED, f"opcd 재시작 실패(rc {rc}): GetDeviceInfo 가 이전 값일 수 있다")
        out(f"result={action}")
        return EXIT_OK
    except Stop as stop:
        log(str(stop))
        return stop.code


def cmd_uid(target, *, out, log):
    try:
        source, uid = dr.read_device_uid(wd.DEVICE_UID_SOURCE, target.uid_path)
    except OSError as exc:
        log(f"UID 를 읽지 못했다: {exc}")
        return EXIT_UNDETERMINED
    out(f"uid_source={source} uid={uid.hex()}")
    return EXIT_OK


def _on_signal(signum, frame):
    # SSH 가 끊겨 SIGHUP 이 와도 finally(force_ro=1 복원)가 돌게 한다.
    raise SystemExit(128 + signum)


def main(argv=None):
    parser = argparse.ArgumentParser(description="eMMC boot1 에 출하 고정값 서명 레코드를 기록한다(#481).")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("uid", help="장치 UID 를 출력한다(uid_source=N uid=HEX)")
    write = sub.add_parser("write", help="서명 레코드 파일을 boot1 에 기록하고 반영한다")
    write.add_argument("record", help="512 바이트 서명 레코드 파일")
    write.add_argument("--replace", action="store_true", help="boot1 의 다른 레코드를 덮어쓴다")
    args = parser.parse_args(argv)

    def out(line):
        print(line, flush=True)

    if args.command == "uid":
        return cmd_uid(Target(), out=out, log=wd._log)
    previous = {s: signal.signal(s, _on_signal) for s in (signal.SIGHUP, signal.SIGTERM)}
    try:
        return cmd_write(args.record, args.replace, Target(), out=out, log=wd._log)
    finally:
        for s, handler in previous.items():
            signal.signal(s, handler)


if __name__ == "__main__":
    sys.exit(main())
