"""캡처 파일 헤더에서 캡처가 끝난 시각을 읽는다(#474).

연결·4-way 판정은 응답 뒤 10초 창이 캡처 안에 다 들어왔는지로 판정을 미룰지 정한다. 필터
(--mac·--sta·--start·--end)는 tshark -Y 로 걸리므로 분석기가 받은 마지막 프레임은 캡처 끝이
아니다. 그래서 tshark 를 다시 돌리지 않고 파일의 레코드 헤더만 따라가며(페이로드는 건너뜀)
파일 순서상 마지막 패킷 시각을 읽는다. 최대값이 아니라 마지막 레코드다 — 캡처 중 시계가 뒤로
가면 최대값은 실제 캡처 끝보다 늦어 판정을 너무 일찍 확정한다. 일반 파일만 읽는다. 파이프·
/dev/stdin·FIFO 를 미리 읽으면 tshark 가 받을 데이터가 사라지므로 열지 않고 None 이다.

classic pcap(µs·ns, LE·BE)과 pcapng(섹션마다 인터페이스 표를 새로 만들고, IDB 마다
if_tsresol, 패킷이 아닌 블록은 길이만큼 건너뜀)만 읽는다. 그 밖의 형식(gzip 등), 잘린 파일,
선언되지 않은 인터페이스, if_tsoffset≠0 은 None 이다. 호출하는 쪽은 그때 지금처럼 필터를
통과한 마지막 프레임 시각을 쓴다.
"""

import os
import re
import stat
import struct
import time
from typing import BinaryIO, Optional, Tuple

CLASSIC_MAGIC = {
    b"\xd4\xc3\xb2\xa1": ("<", 1_000_000),
    b"\xa1\xb2\xc3\xd4": (">", 1_000_000),
    b"\x4d\x3c\xb2\xa1": ("<", 1_000_000_000),
    b"\xa1\xb2\x3c\x4d": (">", 1_000_000_000),
}
PCAPNG_SHB = 0x0A0D0D0A
PCAPNG_BYTE_ORDER = 0x1A2B3C4D
PCAPNG_IDB, PCAPNG_PB, PCAPNG_EPB = 1, 2, 6
OPT_END, OPT_TSRESOL, OPT_TSOFFSET = 0, 9, 14
END_TIME = re.compile(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})(\.\d+)?")


class _Unreadable(Exception):
    """파일을 끝까지 정확히 읽을 수 없다(→ None)."""


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    data = stream.read(size)
    if len(data) != size:
        raise _Unreadable
    return data


def _classic_end(stream: BinaryIO, file_size: int, order: str, scale: int) -> Optional[float]:
    _read_exact(stream, 20)                          # 나머지 전역 헤더
    latest: Optional[float] = None
    while True:
        header = stream.read(16)
        if not header:
            return latest
        if len(header) != 16:
            raise _Unreadable
        seconds, fraction, captured, _ = struct.unpack(order + "IIII", header)
        if fraction >= scale or stream.tell() + captured > file_size:
            raise _Unreadable
        stream.seek(captured, os.SEEK_CUR)
        latest = seconds + fraction / scale


def _resolution(options: bytes, order: str) -> float:
    """IDB 옵션에서 타임스탬프 단위(초). if_tsoffset≠0 이면 읽지 않는다."""
    unit = 1e-6
    position = 0
    while position + 4 <= len(options):
        code, length = struct.unpack(order + "HH", options[position:position + 4])
        value = options[position + 4:position + 4 + length]
        if code == OPT_END:
            break
        if len(value) != length:
            raise _Unreadable
        if code == OPT_TSRESOL and length == 1:
            raw = value[0]
            unit = 2.0 ** -(raw & 0x7F) if raw & 0x80 else 10.0 ** -raw
        elif code == OPT_TSOFFSET and length == 8 and struct.unpack(order + "q", value)[0] != 0:
            raise _Unreadable
        position += 4 + length + (-length) % 4
    return unit


def _pcapng_end(stream: BinaryIO, file_size: int) -> Optional[float]:
    latest: Optional[float] = None
    order = "<"
    units: list = []
    while True:
        start = stream.tell()
        head = stream.read(8)
        if not head:
            return latest
        if len(head) != 8:
            raise _Unreadable
        block_type = struct.unpack("<I", head[:4])[0]
        if block_type == PCAPNG_SHB:
            magic = _read_exact(stream, 4)
            if struct.unpack("<I", magic)[0] == PCAPNG_BYTE_ORDER:
                order = "<"
            elif struct.unpack(">I", magic)[0] == PCAPNG_BYTE_ORDER:
                order = ">"
            else:
                raise _Unreadable
            units = []                               # 섹션마다 인터페이스 번호가 새로 시작한다
        else:
            block_type = struct.unpack(order + "I", head[:4])[0]
        total = struct.unpack(order + "I", head[4:])[0]
        if total < 12 or total % 4 or start + total > file_size:
            raise _Unreadable
        if block_type == PCAPNG_IDB:
            stream.seek(start + 8)
            body = _read_exact(stream, total - 12)
            units.append(_resolution(body[8:], order))
        elif block_type in (PCAPNG_EPB, PCAPNG_PB):
            stream.seek(start + 8)
            if block_type == PCAPNG_EPB:
                interface, high, low = struct.unpack(order + "III", _read_exact(stream, 12))
            else:
                interface, _, high, low = struct.unpack(order + "HHII", _read_exact(stream, 12))
            if interface >= len(units):
                raise _Unreadable
            latest = ((high << 32) | low) * units[interface]
        stream.seek(start + total)                   # ISB·NRB·DSB·사용자 정의 블록도 길이만큼 건너뛴다


def capture_end_epoch(path: str) -> Optional[float]:
    """캡처의 파일 순서상 마지막 패킷 시각(epoch 초). 일반 파일이 아니거나 정확히 읽을 수 없으면 None."""
    try:
        if not stat.S_ISREG(os.stat(path).st_mode):
            return None
        with open(path, "rb") as stream:
            file_size = os.fstat(stream.fileno()).st_size
            magic = stream.read(4)
            if magic in CLASSIC_MAGIC:
                order, scale = CLASSIC_MAGIC[magic]
                return _classic_end(stream, file_size, order, scale)
            if len(magic) == 4 and struct.unpack("<I", magic)[0] == PCAPNG_SHB:
                stream.seek(0)
                return _pcapng_end(stream, file_size)
            return None
    except (OSError, _Unreadable, struct.error):
        return None


def parse_end_time(text: str) -> Optional[float]:
    """--end 를 tshark frame.time 필터처럼 지역 시각으로 읽는다. 'YYYY-MM-DD[ T]HH:MM:SS(.frac)' 만 받는다."""
    match = END_TIME.fullmatch(text.strip()) if text else None
    if not match:
        return None
    try:
        base = time.mktime(time.strptime(f"{match.group(1)} {match.group(2)}", "%Y-%m-%d %H:%M:%S"))
    except (ValueError, OverflowError):
        return None
    return base + (float(match.group(3)) if match.group(3) else 0.0)


def capture_end(path: str, time_end: str = "") -> Tuple[Optional[float], bool]:
    """(창 판정에 쓸 캡처 끝 시각, 그 시각을 포함하지 않는지). 모르면 (None, False).

    --end 는 tshark 필터에서 `<`(그 시각 미포함)이므로, 파일 끝보다 이르면 그 시각이 끝이고 배타다.
    """
    file_end = capture_end_epoch(path)
    if file_end is None:
        return None, False
    if not time_end:
        return file_end, False
    end = parse_end_time(time_end)
    if end is None:
        return None, False
    if end <= file_end:
        return end, True
    return file_end, False
