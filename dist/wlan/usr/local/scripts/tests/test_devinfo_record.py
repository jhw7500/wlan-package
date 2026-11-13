"""출하 고정값 서명 레코드(#481) — 형식·판정 시험.

형식의 정본은 docs/devinfo_record_format.md 다. GOLDEN 은 모듈과 별개로
struct.pack 과 cryptography 를 직접 써서 만든 레코드이고(시험 전용 키, 고정 시드),
모듈이 형식을 바꾸면 여기서 실패한다.
"""
import datetime
import json
import struct
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))
import devinfo_record as dr  # noqa: E402

TEST_SEED = bytes(range(32))  # 시험 전용. 장치 패키지의 공개키 목록에 넣지 않는다.
TEST_KEY = Ed25519PrivateKey.from_private_bytes(TEST_SEED)
TEST_PUB = bytes.fromhex("03a107bff3ce10be1d70dd18e74bc09967e4d6309ba50d5f1ddc8664125531b8")
KEYS = {1: TEST_PUB}
UID = bytes(range(0x10, 0x20))
DEVICE_UID = (dr.UID_SOURCE_IMX93_OCOTP, UID)

GOLDEN = bytes.fromhex(
    "43545344494e464f0100010110000000101112131415161718191a1b1c1d1e1f"
    "fb2c90000301504a01000101ea070b01ea070b0d48572d312e302e3000000000"
    "0000000000000000000000000000000000000000534e2d544553542d30303031"
    + "00" * 352
    + "9ca8e212b1709a21b842e40597376c8ee3a6dbf35314790a312014e90fcc55fb"
    "3bc93b925984b7dd03d4b21de3dc8f032c37a5eaf8fe109001f6148c53197b0b"
)

GOLDEN_VALUES = dr.DeviceValues(
    vendor_code=0x00902CFB,
    product_code=0x0103,
    product_subcode=0x4A50,
    ieee_11r=1,
    ieee_11ai=0,
    ieee_11k=1,
    ieee_11v=1,
    manufacture_date=datetime.date(2026, 11, 1),
    shipment_date=datetime.date(2026, 11, 13),
    hardware_version="HW-1.0.0",
    serial_number="SN-TEST-0001",
)
GOLDEN_RECORD = dr.Record(key_id=1, uid_source=dr.UID_SOURCE_IMX93_OCOTP, uid=UID, values=GOLDEN_VALUES)


def resign(payload: bytes) -> bytes:
    """구조를 일부러 깬 448 바이트에 다시 서명한다(서명이 아닌 구조 검사를 시험하려고)."""
    assert len(payload) == 448
    return payload + TEST_KEY.sign(payload)


def patched(offset: int, data: bytes) -> bytes:
    p = bytearray(GOLDEN[:448])
    p[offset:offset + len(data)] = data
    return resign(bytes(p))


# --- 골든 벡터 -------------------------------------------------------------

def test_golden_is_512_bytes_with_documented_layout():
    assert len(GOLDEN) == dr.RECORD_SIZE == 512
    assert GOLDEN[0:8] == b"CTSDINFO"
    assert struct.unpack_from("<HBBB", GOLDEN, 8) == (1, 1, 1, 16)
    assert struct.unpack_from("<IHH", GOLDEN, 32) == (0x00902CFB, 0x0103, 0x4A50)
    assert tuple(GOLDEN[40:44]) == (1, 0, 1, 1)
    assert struct.unpack_from("<HBB", GOLDEN, 44) == (2026, 11, 1)
    assert struct.unpack_from("<HBB", GOLDEN, 48) == (2026, 11, 13)
    assert GOLDEN[52:84].rstrip(b"\0") == b"HW-1.0.0"
    assert GOLDEN[84:116].rstrip(b"\0") == b"SN-TEST-0001"
    assert GOLDEN[116:448] == bytes(332)


def test_sign_record_reproduces_golden_bytes():
    assert dr.sign_record(GOLDEN_RECORD, TEST_KEY) == GOLDEN


def test_golden_verifies_and_round_trips_values():
    result = dr.check(GOLDEN, KEYS, DEVICE_UID)
    assert result.status == dr.STATUS_VALID, result.reason
    assert result.record == GOLDEN_RECORD


# --- 없음 / 무효 / 모르는 키 ------------------------------------------------

@pytest.mark.parametrize("blob", [bytes(512), b"\xff" * 512, b"CTSDINFX" + GOLDEN[8:]])
def test_missing_magic_is_absent(blob):
    assert dr.check(blob, KEYS, DEVICE_UID).status == dr.STATUS_ABSENT


@pytest.mark.parametrize("size", [0, 511, 513])
def test_wrong_size_is_undetermined(size):
    """문서 판정 순서 1단계: 512 바이트를 읽지 못하면 판정 불가(파일을 바꾸지 않는다)."""
    blob = (GOLDEN * 2)[:size]
    result = dr.check(blob, KEYS, DEVICE_UID)
    assert (result.status, result.reason) == (dr.STATUS_UNDETERMINED, "size")


def test_unknown_format_version_is_invalid():
    result = dr.check(patched(8, struct.pack("<H", 2)), KEYS, DEVICE_UID)
    assert (result.status, result.reason) == (dr.STATUS_INVALID, "version")


def test_key_id_not_in_list_is_unknown_key():
    result = dr.check(patched(10, b"\x02"), KEYS, DEVICE_UID)
    assert result.status == dr.STATUS_UNKNOWN_KEY
    assert "2" in result.reason


@pytest.mark.parametrize("offset", [20, 33, 41, 60, 90, 200, 447, 448, 511])
def test_any_flipped_byte_breaks_signature(offset):
    blob = bytearray(GOLDEN)
    blob[offset] ^= 0x01
    result = dr.check(bytes(blob), KEYS, DEVICE_UID)
    assert (result.status, result.reason) == (dr.STATUS_INVALID, "signature")


def test_signature_from_other_key_is_invalid():
    other = Ed25519PrivateKey.from_private_bytes(bytes(32))
    blob = GOLDEN[:448] + other.sign(GOLDEN[:448])
    assert (dr.check(blob, KEYS, DEVICE_UID).reason) == "signature"


@pytest.mark.parametrize("offset,data,reason", [
    (13, b"\x01", "reserved"),
    (300, b"\x01", "reserved"),
    (12, b"\x00", "uid"),
    (12, b"\x11", "uid"),
    (12, b"\x08", "uid"),                 # uid_len 8 인데 뒤 8 바이트가 0 이 아님
    (40, b"\x02", "ieee_11r"),
    (43, b"\xff", "ieee_11v"),
    (46, b"\x0d", "manufacture_date"),    # 13월
    (51, b"\x20", "shipment_date"),       # 32일
    (52, b"HW\x001", "hardware_version"),  # 첫 NUL 뒤에 문자가 있다
    (84, b"\xc3\xa9", "serial_number"),    # 비 ASCII
    (84, b"\x01", "serial_number"),        # 제어 문자
    (84, b"A" * 32, "serial_number"),      # NUL 없이 32 자
    (84, b'SN"1', "serial_number"),        # opcd 가 따옴표에서 자른다
    (52, b"HW\\1", "hardware_version"),    # opcd 가 이스케이프를 풀지 않는다
    (44, struct.pack("<H", 1969), "manufacture_date"),  # opcd parse_date 는 1970 미만을 거부
    (48, struct.pack("<H", 1969), "shipment_date"),
])
def test_resigned_structural_violations_are_invalid(offset, data, reason):
    result = dr.check(patched(offset, data), KEYS, DEVICE_UID)
    assert (result.status, result.reason) == (dr.STATUS_INVALID, reason)


def test_uid_mismatch_is_invalid_and_reports_both_values():
    other_uid = bytes(16)
    result = dr.check(GOLDEN, KEYS, (dr.UID_SOURCE_IMX93_OCOTP, other_uid))
    assert result.status == dr.STATUS_INVALID
    assert result.reason.startswith("uid-mismatch")
    assert UID.hex() in result.reason and other_uid.hex() in result.reason


def test_uid_source_mismatch_is_invalid():
    result = dr.check(GOLDEN, KEYS, (99, UID))
    assert result.status == dr.STATUS_INVALID
    assert result.reason.startswith("uid-mismatch")


def test_missing_cryptography_raises_instead_of_judging(monkeypatch):
    for name in [m for m in sys.modules if m == "cryptography" or m.startswith("cryptography.")]:
        monkeypatch.setitem(sys.modules, name, None)
    with pytest.raises(ImportError):
        dr.check(GOLDEN, KEYS, DEVICE_UID)


# --- device_info.json ------------------------------------------------------

def test_device_info_json_matches_opcd_keys_and_formats():
    doc = dr.device_info_json(GOLDEN_VALUES)
    assert doc == {
        "vendor_code": "0x00902CFB",
        "product_code": "0x0103",
        "product_subcode": "0x4A50",
        "hardware_version": "HW-1.0.0",
        "serial_number": "SN-TEST-0001",
        "manufacture_date": "2026-11-01",
        "shipment_date": "2026-11-13",
        "ieee_11r": 1,
        "ieee_11ai": 0,
        "ieee_11k": 1,
        "ieee_11v": 1,
        "source": "boot1-record",
    }


def test_device_info_json_hex_codes_are_upper_case_and_zero_padded():
    values = dr.DeviceValues(**{**GOLDEN_VALUES.__dict__,
                                "vendor_code": 0xABCDEF, "product_code": 0xAB, "product_subcode": 0x0ADE})
    doc = dr.device_info_json(values)
    assert (doc["vendor_code"], doc["product_code"], doc["product_subcode"]) == ("0x00ABCDEF", "0x00AB", "0x0ADE")


# --- 레코드 만들기 ---------------------------------------------------------

@pytest.mark.parametrize("change", [
    {"hardware_version": "x" * 32},
    {"serial_number": "시리얼"},
    {"serial_number": "a\tb"},
    {"ieee_11k": 2},
    {"product_code": 0x10000},
    {"vendor_code": -1},
    {"serial_number": 'SN"0001'},
    {"hardware_version": "HW\\1.0"},
    {"manufacture_date": datetime.date(1969, 12, 31)},
    {"shipment_date": datetime.date(1, 1, 1)},
])
def test_pack_rejects_values_that_would_not_verify(change):
    values = dr.DeviceValues(**{**GOLDEN_VALUES.__dict__, **change})
    with pytest.raises(dr.RecordError):
        dr.pack_payload(dr.Record(1, dr.UID_SOURCE_IMX93_OCOTP, UID, values))


@pytest.mark.parametrize("record", [
    dr.Record(0, dr.UID_SOURCE_IMX93_OCOTP, UID, GOLDEN_VALUES),
    dr.Record(256, dr.UID_SOURCE_IMX93_OCOTP, UID, GOLDEN_VALUES),
    dr.Record(1, dr.UID_SOURCE_IMX93_OCOTP, b"", GOLDEN_VALUES),
    dr.Record(1, dr.UID_SOURCE_IMX93_OCOTP, bytes(17), GOLDEN_VALUES),
    dr.Record(1, 0, UID, GOLDEN_VALUES),                              # 등록되지 않은 출처
    dr.Record(1, 2, UID, GOLDEN_VALUES),
    dr.Record(1, dr.UID_SOURCE_IMX93_OCOTP, UID[:8], GOLDEN_VALUES),  # 출처의 길이(16)와 다름
])
def test_pack_rejects_bad_header(record):
    with pytest.raises(dr.RecordError):
        dr.pack_payload(record)


# --- 사양서 R2 값 규칙(양산 도구가 강제) ------------------------------------

def test_r2_rules_accept_golden_values():
    assert dr.r2_violations(GOLDEN_VALUES) == []


@pytest.mark.parametrize("change,field", [
    ({"vendor_code": 0x00902CFC}, "vendor_code"),
    ({"product_code": 0x0203}, "product_code"),
    ({"product_code": 0x0104}, "product_code"),
    ({"product_code": 0x0100}, "product_code"),
    ({"product_subcode": 0x5553}, "product_subcode"),  # US
    ({"ieee_11ai": 2}, "ieee_11ai"),
])
def test_r2_rules_reject_values_outside_spec(change, field):
    values = dr.DeviceValues(**{**GOLDEN_VALUES.__dict__, **change})
    assert field in dr.r2_violations(values)


@pytest.mark.parametrize("subcode", [0x4B52, 0x4A50, 0x434E, 0x5757])
@pytest.mark.parametrize("product", [0x0101, 0x0102, 0x0103])
def test_r2_rules_accept_every_listed_code(subcode, product):
    values = dr.DeviceValues(**{**GOLDEN_VALUES.__dict__, "product_code": product, "product_subcode": subcode})
    assert dr.r2_violations(values) == []


# --- UID 읽기 --------------------------------------------------------------

def test_read_imx93_uid_takes_16_bytes_at_offset_192(tmp_path):
    nvmem = tmp_path / "nvmem"
    nvmem.write_bytes(bytes(192) + UID + bytes(2048 - 208))
    assert dr.read_device_uid(dr.UID_SOURCE_IMX93_OCOTP, nvmem) == DEVICE_UID


def test_read_imx93_uid_rejects_short_nvmem(tmp_path):
    nvmem = tmp_path / "nvmem"
    nvmem.write_bytes(bytes(200))
    with pytest.raises(OSError):
        dr.read_device_uid(dr.UID_SOURCE_IMX93_OCOTP, nvmem)


# --- 소비자 오라클: 실제 opcd 로더 ------------------------------------------
# v1 이 유효로 보는 값은 opcd(wlan-opc/opcd/inventory.c)가 그대로 읽어야 한다.
# 서브모듈 소스를 작은 main 과 함께 컴파일해 device_info_json 출력을 먹인다.
# wlan-opc 서브모듈과 cc 가 없으면 건너뛰지 않고 실패한다(test_reset_cause_ids.py 와 같다).

REPO = Path(__file__).resolve().parents[6]
OPCD = REPO / "wlan-opc/opcd"
_OPCD_MAIN = r"""
#include <stdio.h>
#include "inventory.h"
int main(int argc, char **argv) {
    (void)argc;
    int rc = opcd_inventory_load(argv[1]);
    const opcd_inventory_t *i = opcd_inventory();
    printf("rc=%d\nvendor=%u\nproduct=%u\nsubcode=%u\nhw=%s\nsn=%s\n"
           "mfg=%u-%u-%u\nship=%u-%u-%u\nieee=%u%u%u%u\n",
           rc, (unsigned)i->vendor_code, (unsigned)i->product_code, (unsigned)i->product_subcode,
           i->hardware_version, i->serial_number,
           i->manufacture_date.year, i->manufacture_date.month, i->manufacture_date.day,
           i->shipment_date.year, i->shipment_date.month, i->shipment_date.day,
           i->ieee_11r, i->ieee_11ai, i->ieee_11k, i->ieee_11v);
    return 0;
}
"""


@pytest.fixture(scope="module")
def opcd_loader(tmp_path_factory):
    if not (OPCD / "inventory.c").is_file():
        pytest.fail(f"{OPCD}/inventory.c not found: run `git submodule update --init wlan-opc`")
    d = tmp_path_factory.mktemp("opcd")
    (d / "main.c").write_text(_OPCD_MAIN, encoding="ascii")
    exe = d / "loader"
    subprocess.run(["cc", "-std=c11", "-I", str(OPCD), "-o", str(exe), str(d / "main.c"),
                    str(OPCD / "inventory.c"), str(OPCD / "json_util.c")],
                   check=True, capture_output=True)

    def load(values):
        path = d / "device_info.json"
        path.write_text(json.dumps(dr.device_info_json(values)), encoding="ascii")
        out = subprocess.run([str(exe), str(path)], check=True, capture_output=True, text=True).stdout
        return dict(line.split("=", 1) for line in out.splitlines())
    return load


def _expected(values):
    m, s = values.manufacture_date, values.shipment_date
    return {
        "rc": "0", "vendor": str(values.vendor_code), "product": str(values.product_code),
        "subcode": str(values.product_subcode), "hw": values.hardware_version, "sn": values.serial_number,
        "mfg": f"{m.year}-{m.month}-{m.day}", "ship": f"{s.year}-{s.month}-{s.day}",
        "ieee": f"{values.ieee_11r}{values.ieee_11ai}{values.ieee_11k}{values.ieee_11v}",
    }


# v1 이 문자열에 허용하는 문자 전부(출력 가능한 ASCII 에서 '"'·'\' 를 뺀 93 자)를 31 자씩 나눈다.
_ALLOWED = "".join(chr(c) for c in range(0x20, 0x7F) if chr(c) not in "\"\\")
_CHUNKS = [_ALLOWED[i:i + 31] for i in range(0, len(_ALLOWED), 31)]


@pytest.mark.parametrize("change", [
    {},
    *[{"hardware_version": chunk, "serial_number": chunk[::-1]} for chunk in _CHUNKS],
    {"manufacture_date": datetime.date(1970, 1, 1), "shipment_date": datetime.date(9999, 12, 31)},
    {"vendor_code": 0xFFFFFFFF, "product_code": 0xFFFF, "product_subcode": 0,
     "ieee_11r": 0, "ieee_11ai": 1, "ieee_11k": 0, "ieee_11v": 0},
    {"hardware_version": "", "serial_number": "x"},
])
def test_every_value_v1_accepts_round_trips_through_opcd(opcd_loader, change):
    values = dr.DeviceValues(**{**GOLDEN_VALUES.__dict__, **change})
    record = dr.Record(1, dr.UID_SOURCE_IMX93_OCOTP, UID, values)
    blob = dr.sign_record(record, TEST_KEY)            # v1 이 만들 수 있고
    assert dr.check(blob, KEYS, DEVICE_UID).status == dr.STATUS_VALID   # 장치가 유효로 보는 값
    assert opcd_loader(values) == _expected(values)    # 은 opcd 가 그대로 읽는다


@pytest.mark.parametrize("change,field", [
    ({"serial_number": 'SN"0001'}, "sn"),
    ({"hardware_version": "HW\\1.0"}, "hw"),
    ({"manufacture_date": datetime.date(1969, 12, 31)}, "mfg"),
])
def test_opcd_oracle_detects_what_v1_rejects(opcd_loader, change, field):
    """양성 대조: v1 이 거부하는 값은 opcd 에서 실제로 깨진다(오라클이 차이를 잡는다)."""
    values = dr.DeviceValues(**{**GOLDEN_VALUES.__dict__, **change})
    assert opcd_loader(values)[field] != _expected(values)[field]
