#!/usr/bin/python3
"""부팅 때 eMMC boot1 의 출하 고정값 서명 레코드를 판정해 device_info.json 에 반영한다(#481).

바이트 배치와 판정 순서의 정본은 docs/devinfo_record_format.md, 판정 구현은
devinfo_record.py 다. 이 스크립트(wifi_devinfo.service)가 device_info.json 의 유일한
기록 주체이고, 판정이 확정됐을 때만 파일을 바꾼다.

- 유효: 레코드 값으로 파일을 쓴다("source": "boot1-record" 표식). 내용이 같으면 쓰지 않는다.
- 없음·무효·모르는 키: 표식 있는 파일만 지운다. 표식 없는 수동 파일은 그대로 두고
  "unsigned 수동 파일 사용" 로그를 남긴다.
- 판정 불가(읽기 오류, 검증 라이브러리 없음 등): 아무것도 바꾸지 않는다.

종료 코드는 유효·없음이면 0, 그 밖(무효·모르는 키·판정 불가·기록 실패)이면 1 이다.
"""
import argparse
import json
import os
import sys
import tempfile

import devinfo_record as dr

RECORD_DEVICE = "/dev/mmcblk0boot1"
DEVICE_INFO_PATH = "/usr/local/opc/etc/device_info.json"  # opcd OPC_PATH_DEVICE_INFO
DEVICE_UID_SOURCE = dr.UID_SOURCE_IMX93_OCOTP

# key_id → Ed25519 공개키(32 B). 추가만 한다. 이미 쓴 key_id 를 다른 키에 쓰지 않는다.
# 개인키는 사내에서만 보관한다(문서 "키 관리").
SHIPPING_PUBLIC_KEYS = {
    1: bytes.fromhex("47c2d7ffaa166db2f8a7c40380fe4765bf26fe0da5817122e470270b10d0bd6d"),
}

_NOT_VALID_LABEL = {
    dr.STATUS_ABSENT: "레코드 없음",
    dr.STATUS_INVALID: "레코드 무효",
    dr.STATUS_UNKNOWN_KEY: "모르는 서명 키",
}


def _log(message):
    print(message, file=sys.stderr, flush=True)


def _judge(record_path, public_keys, uid_path):
    with open(record_path, "rb") as f:
        blob = f.read(dr.RECORD_SIZE)
    # UID 는 판정 7단계(UID 비교)에 이르렀을 때만 읽는다. UID 를 읽지 못하는 장치도 없음·무효·
    # 모르는 키는 그대로 판정하고, 서명과 구조가 맞는 레코드만 판정 불가가 된다.
    return dr.check(blob, public_keys, lambda: dr.read_device_uid(DEVICE_UID_SOURCE, uid_path))


def _current(info_path):
    """(파일 있음, JSON 내용 또는 None). 읽을 수 없거나 JSON 이 아니면 내용은 None."""
    try:
        with open(info_path, encoding="utf-8") as f:
            return True, json.load(f)
    except FileNotFoundError:
        return False, None
    except (OSError, ValueError):
        return True, None


def _is_marked(data):
    return isinstance(data, dict) and data.get("source") == dr.JSON_SOURCE_MARKER


def _write_atomic(path, data):
    directory = os.path.dirname(path) or "."
    # 패키지는 /usr/local/opc/etc 를 만들지 않는다(opcd 가 처음 뜰 때 0755 로 만든다). OPC 를 켠 적이
    # 없거나 purge 뒤 재설치한 장치도 SNMP 가 읽을 파일을 쓰도록 같은 모드로 만든다.
    os.makedirs(directory, mode=0o755, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".device_info.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(json.dumps(data) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def run(record_path, info_path, *, uid_path=None, public_keys=None, log=None):
    keys = SHIPPING_PUBLIC_KEYS if public_keys is None else public_keys
    log = log or _log
    try:
        result = _judge(record_path, keys, uid_path)
    except Exception as exc:  # 어떤 예외든 파일을 바꾸지 않는다(판정 불가)
        log(f"판정 불가({type(exc).__name__}: {exc}): {info_path} 를 바꾸지 않는다")
        return 1
    if result.status == dr.STATUS_UNDETERMINED:
        log(f"판정 불가({result.reason}): {info_path} 를 바꾸지 않는다")
        return 1

    exists, current = _current(info_path)
    if result.status == dr.STATUS_VALID:
        data = dr.device_info_json(result.record.values)
        if current == data:
            log(f"레코드 유효: {info_path} 변경 없음")
            return 0
        try:
            _write_atomic(info_path, data)
        except OSError as exc:
            log(f"레코드 유효, 기록 실패({exc}): {info_path} 를 바꾸지 않았다")
            return 1
        log(f"레코드 유효: {info_path} 를 기록했다")
        return 0

    label = _NOT_VALID_LABEL[result.status]
    if result.reason:
        label = f"{label}({result.reason})"
    if _is_marked(current):
        try:
            os.unlink(info_path)
        except OSError as exc:
            log(f"{label}, 삭제 실패({exc}): {info_path}")
            return 1
        log(f"{label}: 레코드에서 만든 {info_path} 를 지웠다")
    elif exists:
        log(f"{label}: unsigned 수동 파일 사용({info_path})")
    else:
        log(f"{label}: {info_path} 없음")
    return 0 if result.status == dr.STATUS_ABSENT else 1


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="eMMC boot1 출하 고정값 서명 레코드를 판정해 device_info.json 에 반영한다(#481).")
    parser.parse_args(argv)
    return run(RECORD_DEVICE, DEVICE_INFO_PATH, public_keys=SHIPPING_PUBLIC_KEYS)


if __name__ == "__main__":
    sys.exit(main())
