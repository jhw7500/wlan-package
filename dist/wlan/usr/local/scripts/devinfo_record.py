"""출하 고정값 서명 레코드(#481).

eMMC boot1 의 512 바이트 레코드를 만들고(양산 도구) 판정한다(장치). 바이트 배치와
판정 순서의 정본은 docs/devinfo_record_format.md 다. 이 모듈은 부팅 검증 서비스,
장치 기록 도구, 양산 PC 도구가 같이 쓴다.

장치는 서명·UID·구조만 검사하고, 사양서 R2 값 규칙(r2_violations)은 레코드를
만드는 쪽이 강제한다. cryptography 는 서명이 필요할 때 불러오므로, 라이브러리가
없으면 check()·sign_record() 가 ImportError 를 낸다(호출자는 "판정 불가"로 처리).
"""
import dataclasses
import datetime
import struct

MAGIC = b"CTSDINFO"
FORMAT_VERSION = 1
RECORD_SIZE = 512
SIGNED_SIZE = 448
SIGNATURE_SIZE = 64
UID_FIELD_SIZE = 16
STR_FIELD_SIZE = 32

UID_SOURCE_IMX93_OCOTP = 1
# uid_source → (nvmem 경로, 오프셋, 길이). 정본은 OCOTP 원시 바이트다(문서 "uid_source").
UID_SOURCES = {
    UID_SOURCE_IMX93_OCOTP: ("/sys/bus/nvmem/devices/fsb_s400_fuse0/nvmem", 192, 16),
}

STATUS_VALID = "valid"
STATUS_ABSENT = "absent"
STATUS_INVALID = "invalid"
STATUS_UNKNOWN_KEY = "unknown-key"
STATUS_UNDETERMINED = "undetermined"  # 판정 불가: 호출자는 기존 파일을 바꾸지 않는다

# opcd 가 그대로 읽는 범위(wlan-opc/opcd/json_util.c copy_quoted_value 는 첫 '"' 에서
# 멈추고 이스케이프를 풀지 않는다, inventory.c parse_date 는 1970~9999 년만 받는다).
STR_FORBIDDEN = frozenset('"\\')
MIN_YEAR = 1970

JSON_SOURCE_MARKER = "boot1-record"

# 장치가 믿는 출하 공개키: key_id → Ed25519 공개키(32 B). 추가만 한다. 이미 쓴 key_id 를 다른 키에
# 쓰지 않는다. 개인키는 사내에서만 보관한다(문서 "키 관리"). 부팅 서비스·기록 도구(장치)와 양산 PC
# 도구가 같이 쓰므로, Windows 에서도 import 할 수 있는 이 모듈이 정본이다.
SHIPPING_PUBLIC_KEYS = {
    1: bytes.fromhex("47c2d7ffaa166db2f8a7c40380fe4765bf26fe0da5817122e470270b10d0bd6d"),
}

# 사양서 R2 6.2·6.3절 값 규칙(양산 도구가 강제한다).
R2_VENDOR_CODE = 0x00902CFB
R2_PRODUCT_CLASSES = frozenset({0x01})          # Bridge A type
R2_WIRELESS_STANDARDS = frozenset({0x01, 0x02, 0x03})  # 802.11n / ac / ax
R2_SUBCODES = frozenset({0x4B52, 0x4A50, 0x434E, 0x5757})  # KR / JP / CN / WW

_HEADER = struct.Struct("<8sHBBB3s")             # 0..15
_FIELDS = struct.Struct("<IHHBBBBHBBHBB")        # 32..51
_IEEE = ("ieee_11r", "ieee_11ai", "ieee_11k", "ieee_11v")


class RecordError(ValueError):
    """레코드로 만들 수 없는 값(검증을 통과하지 못할 레코드를 만들지 않으려고 쓴다)."""


@dataclasses.dataclass(frozen=True)
class DeviceValues:
    vendor_code: int
    product_code: int
    product_subcode: int
    ieee_11r: int
    ieee_11ai: int
    ieee_11k: int
    ieee_11v: int
    manufacture_date: datetime.date
    shipment_date: datetime.date
    hardware_version: str
    serial_number: str


@dataclasses.dataclass(frozen=True)
class Record:
    key_id: int
    uid_source: int
    uid: bytes
    values: DeviceValues


@dataclasses.dataclass(frozen=True)
class CheckResult:
    status: str
    reason: str = ""
    record: "Record | None" = None


# --- 만들기 -----------------------------------------------------------------

def _encode_str(name, text):
    if not isinstance(text, str) or not text.isascii() or not text.isprintable():
        raise RecordError(f"{name}: printable ASCII only")
    if STR_FORBIDDEN & set(text):
        raise RecordError(f"{name}: '\"' and '\\' are not allowed")
    raw = text.encode("ascii")
    if len(raw) > STR_FIELD_SIZE - 1:
        raise RecordError(f"{name}: at most {STR_FIELD_SIZE - 1} characters")
    return raw + bytes(STR_FIELD_SIZE - len(raw))


def _check_uint(name, value, bits):
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value < (1 << bits):
        raise RecordError(f"{name}: not a u{bits}")


def pack_payload(record):
    """서명 범위(앞 448 바이트)를 만든다. 장치가 무효로 볼 값은 RecordError 로 거부한다."""
    if not isinstance(record.key_id, int) or not 1 <= record.key_id <= 255:
        raise RecordError("key_id: 1..255")
    # 장치는 read_device_uid() 로 등록된 출처의 정해진 길이만 읽으므로, 그 밖의 조합은
    # 어느 장치에서도 uid-mismatch 가 된다. 그런 레코드는 만들지 않는다.
    if record.uid_source not in UID_SOURCES:
        raise RecordError(f"uid_source: one of {sorted(UID_SOURCES)}")
    uid_length = UID_SOURCES[record.uid_source][2]
    if not isinstance(record.uid, bytes) or len(record.uid) != uid_length:
        raise RecordError(f"uid: {uid_length} bytes for uid_source {record.uid_source}")
    v = record.values
    _check_uint("vendor_code", v.vendor_code, 32)
    _check_uint("product_code", v.product_code, 16)
    _check_uint("product_subcode", v.product_subcode, 16)
    for name in _IEEE:
        if getattr(v, name) not in (0, 1) or isinstance(getattr(v, name), bool):
            raise RecordError(f"{name}: 0 or 1")
    for name in ("manufacture_date", "shipment_date"):
        if not isinstance(getattr(v, name), datetime.date) or getattr(v, name).year < MIN_YEAR:
            raise RecordError(f"{name}: date from {MIN_YEAR}")
    header = _HEADER.pack(MAGIC, FORMAT_VERSION, record.key_id, record.uid_source, len(record.uid), bytes(3))
    uid = record.uid + bytes(UID_FIELD_SIZE - len(record.uid))
    md, sd = v.manufacture_date, v.shipment_date
    fields = _FIELDS.pack(v.vendor_code, v.product_code, v.product_subcode,
                          v.ieee_11r, v.ieee_11ai, v.ieee_11k, v.ieee_11v,
                          md.year, md.month, md.day, sd.year, sd.month, sd.day)
    payload = (header + uid + fields
               + _encode_str("hardware_version", v.hardware_version)
               + _encode_str("serial_number", v.serial_number))
    payload += bytes(SIGNED_SIZE - len(payload))
    assert len(payload) == SIGNED_SIZE
    return payload


def sign_record(record, private_key):
    """Ed25519 개인키(cryptography 객체)로 512 바이트 레코드를 만든다."""
    payload = pack_payload(record)
    signature = private_key.sign(payload)
    if len(signature) != SIGNATURE_SIZE:
        raise RecordError("signature: unexpected length")
    return payload + signature


# --- 판정 -------------------------------------------------------------------

def _decode_str(raw):
    text, _, rest = raw.partition(b"\0")
    if any(rest) or len(text) >= STR_FIELD_SIZE:
        return None
    if not all(0x20 <= c <= 0x7E and chr(c) not in STR_FORBIDDEN for c in text):
        return None
    return text.decode("ascii")


def _decode_date(year, month, day):
    if year < MIN_YEAR:
        return None
    try:
        return datetime.date(year, month, day)
    except ValueError:
        return None


def _structure(payload):
    """서명이 맞은 448 바이트에서 Record 를 만든다. 규칙 위반이면 (None, 이유)."""
    _magic, _ver, key_id, uid_source, uid_len, reserved = _HEADER.unpack_from(payload, 0)
    if any(reserved) or any(payload[116:SIGNED_SIZE]):
        return None, "reserved"
    uid_field = payload[16:32]
    if not 1 <= uid_len <= UID_FIELD_SIZE or any(uid_field[uid_len:]):
        return None, "uid"
    (vendor, product, subcode, r, ai, k, vv,
     my, mm, md, sy, sm, sd) = _FIELDS.unpack_from(payload, 32)
    for name, flag in zip(_IEEE, (r, ai, k, vv)):
        if flag not in (0, 1):
            return None, name
    mdate = _decode_date(my, mm, md)
    if mdate is None:
        return None, "manufacture_date"
    sdate = _decode_date(sy, sm, sd)
    if sdate is None:
        return None, "shipment_date"
    hw = _decode_str(payload[52:84])
    if hw is None:
        return None, "hardware_version"
    serial = _decode_str(payload[84:116])
    if serial is None:
        return None, "serial_number"
    values = DeviceValues(vendor, product, subcode, r, ai, k, vv, mdate, sdate, hw, serial)
    return Record(key_id, uid_source, bytes(uid_field[:uid_len]), values), ""


def check(blob, public_keys, device_uid):
    """레코드를 판정한다. public_keys: {key_id: 32바이트 공개키}.

    device_uid 는 (uid_source, uid) 이거나, 그것을 돌려주는 인자 없는 함수다. 함수는 7단계(UID
    비교)에 이르렀을 때만 부른다. 그래서 장치 UID 를 읽지 못해도 앞 단계에서 결론이 나는 레코드는
    판정하고, 함수가 낸 예외(OSError 등)는 그대로 올린다(호출자는 "판정 불가"로 처리).
    판정 순서는 문서의 "판정 순서" 1~8 단계다. 512 바이트가 아니면(짧게 읽힘) 판정 불가다.
    cryptography 를 불러오지 못하면 ImportError 를 그대로 낸다(이것도 판정 불가).
    """
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if len(blob) != RECORD_SIZE:
        return CheckResult(STATUS_UNDETERMINED, "size")
    if blob[:8] != MAGIC:
        return CheckResult(STATUS_ABSENT)
    _magic, version, key_id, _src, _len, _res = _HEADER.unpack_from(blob, 0)
    if version != FORMAT_VERSION:
        return CheckResult(STATUS_INVALID, "version")
    if key_id not in public_keys:
        return CheckResult(STATUS_UNKNOWN_KEY, f"key_id {key_id}")
    payload, signature = blob[:SIGNED_SIZE], blob[SIGNED_SIZE:]
    try:
        Ed25519PublicKey.from_public_bytes(public_keys[key_id]).verify(signature, payload)
    except InvalidSignature:
        return CheckResult(STATUS_INVALID, "signature")
    record, reason = _structure(payload)
    if record is None:
        return CheckResult(STATUS_INVALID, reason)
    if callable(device_uid):
        device_uid = device_uid()
    want_source, want_uid = device_uid
    if record.uid_source != want_source or record.uid != want_uid:
        return CheckResult(
            STATUS_INVALID,
            f"uid-mismatch record={record.uid_source}:{record.uid.hex()} device={want_source}:{want_uid.hex()}")
    return CheckResult(STATUS_VALID, "", record)


# --- 출력·규칙·UID -----------------------------------------------------------

def device_info_json(values):
    """opcd(inventory.c)·SNMP(wifi_snmp_pp.py)가 읽는 device_info.json 내용."""
    return {
        "vendor_code": f"0x{values.vendor_code:08X}",
        "product_code": f"0x{values.product_code:04X}",
        "product_subcode": f"0x{values.product_subcode:04X}",
        "hardware_version": values.hardware_version,
        "serial_number": values.serial_number,
        "manufacture_date": values.manufacture_date.isoformat(),
        "shipment_date": values.shipment_date.isoformat(),
        "ieee_11r": values.ieee_11r,
        "ieee_11ai": values.ieee_11ai,
        "ieee_11k": values.ieee_11k,
        "ieee_11v": values.ieee_11v,
        "source": JSON_SOURCE_MARKER,
    }


def r2_violations(values):
    """사양서 R2 6.2·6.3절 값 규칙을 어긴 필드 이름 목록(양산 도구용)."""
    bad = []
    if values.vendor_code != R2_VENDOR_CODE:
        bad.append("vendor_code")
    if (values.product_code >> 8 not in R2_PRODUCT_CLASSES
            or values.product_code & 0xFF not in R2_WIRELESS_STANDARDS):
        bad.append("product_code")
    if values.product_subcode not in R2_SUBCODES:
        bad.append("product_subcode")
    bad.extend(name for name in _IEEE if getattr(values, name) not in (0, 1))
    return bad


def read_device_uid(uid_source, path=None):
    """장치 UID 를 (uid_source, 원시 바이트)로 읽는다. 짧게 읽히면 OSError."""
    default_path, offset, length = UID_SOURCES[uid_source]
    with open(path or default_path, "rb") as f:
        f.seek(offset)
        raw = f.read(length)
    if len(raw) != length:
        raise OSError(f"uid: short read from {path or default_path}")
    return uid_source, raw
