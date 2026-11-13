"""boot1 기록 도구(#481 PR-3) — 쓰기 전 검사, 쓰기·되읽기, 반영, 종료 코드.

장치 경로(boot1·force_ro·nvmem·device_info.json·잠금)는 임시 파일로, extcsd 출력·루트FS
디스크·캐시 비우기·opcd 재시작은 가짜로 바꾼다. extcsd 고정값은 i.MX93 보드에서 받은 실제 출력
(`mmc extcsd read /dev/mmcblk0`, mmc-utils 0.1+git0+613495ecac, 2026-11-13)의 일부다.
"""
import datetime
import fcntl
import json
import os
import signal
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))
import devinfo_record as dr  # noqa: E402
import wifi_devinfo as wd  # noqa: E402
import wifi_devinfo_write as ww  # noqa: E402

TEST_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))  # 시험 전용
TEST_PUB = TEST_KEY.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
OTHER_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
KEYS = {1: TEST_PUB}
UID = bytes(range(0x10, 0x20))
BOOT1_SIZE = 8192


def values(serial="SN-TEST-0001"):
    return dr.DeviceValues(
        vendor_code=0x00902CFB, product_code=0x0103, product_subcode=0x4A50,
        ieee_11r=1, ieee_11ai=0, ieee_11k=1, ieee_11v=1,
        manufacture_date=datetime.date(2026, 11, 1), shipment_date=datetime.date(2026, 11, 13),
        hardware_version="HW-1.0.0", serial_number=serial)


def record(serial="SN-TEST-0001", key_id=1, uid=UID, key=TEST_KEY):
    return dr.sign_record(dr.Record(key_id, dr.UID_SOURCE_IMX93_OCOTP, uid, values(serial)), key)


EXTCSD_BOOT0 = """\
Erased memory content [ERASED_MEM_CONT: 0x00]
Boot configuration bytes [PARTITION_CONFIG: 0x08]
 Boot Partition 1 enabled
 No access to boot partition
Boot config protection [BOOT_CONFIG_PROT: 0x00]
Boot bus Conditions [BOOT_BUS_CONDITIONS: 0x00]
"""


class Dev:
    """임시 장치 한 벌과 가짜 동작. calls 에 순서대로 기록한다."""

    def __init__(self, root: Path, *, boot1=None, extcsd=EXTCSD_BOOT0, root_disk="mmcblk0",
                 opcd_rc=0, opcd_active=True):
        self.root = root
        self.boot1 = root / "mmcblk0boot1"
        self.boot1.write_bytes(bytes(BOOT1_SIZE) if boot1 is None else boot1)
        self.force_ro = root / "force_ro"
        self.force_ro.write_text("1\n")
        self.nvmem = root / "nvmem"
        nv = bytearray(256)
        nv[192:208] = UID
        self.nvmem.write_bytes(bytes(nv))
        self.etc = root / "etc"
        self.etc.mkdir()
        self.info = self.etc / "device_info.json"
        self.lock = root / "wifi_devinfo.lock"
        self.calls = []
        self.out = []
        self.log = []
        self.extcsd_text = extcsd
        self.root_disk_name = root_disk
        self.opcd_rc = opcd_rc
        self.opcd_active = opcd_active
        self.corrupt_on_flush = False

    def _extcsd(self):
        self.calls.append("extcsd")
        if isinstance(self.extcsd_text, Exception):
            raise self.extcsd_text
        return self.extcsd_text

    def _root_disk(self):
        self.calls.append("root_disk")
        return self.root_disk_name

    def _flush(self, path):
        self.calls.append(("flush", self.force_ro.read_text().strip()))
        if self.corrupt_on_flush:
            data = bytearray(Path(path).read_bytes())
            data[100] ^= 0xFF
            Path(path).write_bytes(bytes(data))

    def _opcd(self):
        self.calls.append("opcd")
        return self.opcd_active, self.opcd_rc

    def target(self):
        return ww.Target(record_device=str(self.boot1), force_ro=str(self.force_ro),
                         info_path=str(self.info), uid_path=str(self.nvmem), lock_path=str(self.lock),
                         public_keys=KEYS, root_disk=self._root_disk, extcsd=self._extcsd,
                         flush=self._flush, opcd=self._opcd)

    def write(self, blob, *, replace=False):
        rec = self.root / "record.bin"
        rec.write_bytes(blob)
        return ww.cmd_write(str(rec), replace, self.target(), out=self.out.append, log=self.log.append)

    def head(self):
        return self.boot1.read_bytes()[:dr.RECORD_SIZE]

    def text(self):
        return "\n".join(self.out + self.log)


@pytest.fixture
def dev(tmp_path):
    return Dev(tmp_path)


def make_dev(tmp_path, **kw):
    return Dev(tmp_path, **kw)


# --- 성공 경로 ------------------------------------------------------------------

def test_write_to_empty_boot1_records_verifies_and_applies(dev):
    blob = record()
    assert dev.write(blob) == ww.EXIT_OK
    data = dev.boot1.read_bytes()
    assert data[:512] == blob and data[512:] == bytes(BOOT1_SIZE - 512)
    assert dev.force_ro.read_text().strip() == "1"
    assert ("flush", "0") in dev.calls          # 쓰기·캐시 비우기는 force_ro=0 동안
    assert json.loads(dev.info.read_text()) == dr.device_info_json(values())
    assert dev.calls[-1] == "opcd"
    assert "result=written" in dev.out


def test_write_to_erased_ff_boot1(tmp_path):
    dev = make_dev(tmp_path, boot1=b"\xff" * BOOT1_SIZE)
    blob = record()
    assert dev.write(blob) == ww.EXIT_OK
    assert dev.head() == blob


def test_same_record_skips_the_write_but_still_applies(dev):
    blob = record()
    dev.write(blob)
    dev.info.unlink()
    dev.calls.clear()
    dev.out.clear()
    assert dev.write(blob) == ww.EXIT_OK
    assert not any(isinstance(c, tuple) and c[0] == "flush" for c in dev.calls)   # 쓰지 않았다
    assert json.loads(dev.info.read_text()) == dr.device_info_json(values())       # 그래도 반영
    assert "opcd" in dev.calls
    assert "result=unchanged" in dev.out


def test_existing_different_record_needs_replace(dev):
    first, second = record("SN-A"), record("SN-B")
    dev.write(first)
    assert dev.write(second) == ww.EXIT_REPLACE_REQUIRED
    assert dev.head() == first
    assert dev.write(second, replace=True) == ww.EXIT_OK
    assert dev.head() == second
    assert f"previous={first.hex()}" in dev.out
    assert json.loads(dev.info.read_text())["serial_number"] == "SN-B"


def test_replace_works_over_a_record_the_device_no_longer_trusts(dev):
    # 키 교체 뒤 재기록(형식 문서 "키 관리"): 기존 레코드는 magic 만 있으면 --replace 대상이다.
    old = record("SN-OLD", key_id=7, key=OTHER_KEY)
    dev.boot1.write_bytes(old + bytes(BOOT1_SIZE - 512))
    assert dev.write(record("SN-NEW"), replace=True) == ww.EXIT_OK
    assert dev.head() == record("SN-NEW")
    assert "unknown-key" in dev.text()


# --- 쓰기 전 거부: boot1·force_ro 를 건드리지 않는다 ---------------------------------

def _untouched(dev, before):
    assert dev.boot1.read_bytes() == before
    assert dev.force_ro.read_text().strip() == "1"
    assert not any(isinstance(c, tuple) and c[0] == "flush" for c in dev.calls)


@pytest.mark.parametrize("blob", [
    pytest.param(lambda: record(key_id=2), id="unknown-key"),
    pytest.param(lambda: record(uid=bytes(16)), id="other-device-uid"),
    pytest.param(lambda: record(key=OTHER_KEY), id="bad-signature"),
    pytest.param(lambda: bytes(512), id="no-magic"),
    pytest.param(lambda: record()[:500], id="short"),
    pytest.param(lambda: record() + b"\0", id="long"),
])
def test_record_not_valid_for_this_device_is_refused(dev, blob):
    before = dev.boot1.read_bytes()
    assert dev.write(blob()) == ww.EXIT_RECORD_INVALID
    _untouched(dev, before)


def test_root_filesystem_not_on_mmcblk0_is_refused(tmp_path):
    dev = make_dev(tmp_path, root_disk="mmcblk1")
    before = dev.boot1.read_bytes()
    assert dev.write(record()) == ww.EXIT_TARGET_REFUSED
    _untouched(dev, before)


@pytest.mark.parametrize("extcsd", [
    EXTCSD_BOOT0.replace("0x08", "0x10"),                     # Boot Partition 2 = mmcblk0boot1
    EXTCSD_BOOT0.replace("0x08", "0x38"),                     # user area
    EXTCSD_BOOT0.replace("0x08", "0x00"),                     # 부트 비활성
    EXTCSD_BOOT0.replace("PARTITION_CONFIG", "PARTITION_CFG"),  # 줄 없음
    EXTCSD_BOOT0 + "Boot configuration bytes [PARTITION_CONFIG: 0x08]\n",  # 두 번
    "",
], ids=["boot2", "user", "disabled", "missing", "duplicate", "empty"])
def test_partition_config_other_than_boot0_is_refused(tmp_path, extcsd):
    dev = make_dev(tmp_path, extcsd=extcsd)
    before = dev.boot1.read_bytes()
    assert dev.write(record()) == ww.EXIT_TARGET_REFUSED
    _untouched(dev, before)


def test_partition_config_reads_the_board_output():
    assert ww.boot_partition_enable(EXTCSD_BOOT0) == 1
    # 문구가 아니라 값으로 판정한다: Boot Partition 2 라는 글자가 있어도 값이 0x08 이면 boot0 이다.
    assert ww.boot_partition_enable(EXTCSD_BOOT0 + " R/W Boot Partition 2\n") == 1


def test_missing_mmc_utils_is_undetermined(tmp_path):
    dev = make_dev(tmp_path, extcsd=FileNotFoundError("mmc"))
    before = dev.boot1.read_bytes()
    assert dev.write(record()) == ww.EXIT_UNDETERMINED
    _untouched(dev, before)


def test_non_empty_boot1_without_magic_is_refused(tmp_path):
    junk = bytearray(BOOT1_SIZE)
    junk[0:4] = b"\x7fELF"
    dev = make_dev(tmp_path, boot1=bytes(junk))
    assert dev.write(record(), replace=True) == ww.EXIT_TARGET_REFUSED
    _untouched(dev, bytes(junk))


def test_unreadable_device_uid_is_undetermined(dev):
    dev.nvmem.unlink()
    before = dev.boot1.read_bytes()
    assert dev.write(record()) == ww.EXIT_UNDETERMINED
    _untouched(dev, before)


def test_busy_lock_is_refused_without_touching(dev):
    holder = os.open(dev.lock, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(holder, fcntl.LOCK_EX)
    try:
        before = dev.boot1.read_bytes()
        assert dev.write(record()) == ww.EXIT_BUSY
        _untouched(dev, before)
    finally:
        os.close(holder)


# --- 쓰기 실패: force_ro 는 항상 되돌린다 ----------------------------------------------

def test_readback_mismatch_fails_and_restores_force_ro(dev):
    dev.corrupt_on_flush = True
    assert dev.write(record()) == ww.EXIT_WRITE_FAILED
    assert dev.force_ro.read_text().strip() == "1"
    assert not dev.info.exists()          # 반영 단계로 가지 않는다


def test_write_error_fails_and_restores_force_ro(dev, monkeypatch):
    def refuse(fd, data, offset):
        raise OSError(5, "I/O error")

    monkeypatch.setattr(os, "pwrite", refuse)
    assert dev.write(record()) == ww.EXIT_WRITE_FAILED
    assert dev.force_ro.read_text().strip() == "1"


def test_force_ro_that_does_not_return_to_1_fails(dev, monkeypatch):
    # 커널이 force_ro=1 을 받아들이지 않으면(되읽으면 0) 쓰기 보호가 풀린 채 남으므로 실패로 알린다.
    monkeypatch.setattr(ww, "_read_force_ro", lambda path: "0")
    assert dev.write(record()) == ww.EXIT_WRITE_FAILED
    assert "force_ro" in dev.text()


# --- 반영 실패 ---------------------------------------------------------------------

def test_service_failure_after_write_is_apply_failure(dev, monkeypatch):
    # 이전 실행이 이미 같은 값의 파일을 써 두었어도, 이번 판정이 실패(rc 1, 예: UID 읽기 실패)면
    # 기록 도구는 성공으로 끝내지 않는다. JSON 대조만으로는 이 경우를 가리지 못한다.
    dev.info.write_text(json.dumps(dr.device_info_json(values())))
    monkeypatch.setattr(wd, "run", lambda *a, **k: 1)
    assert dev.write(record()) == ww.EXIT_APPLY_FAILED
    assert dev.head() == record()


def test_json_not_matching_the_record_is_apply_failure(dev, monkeypatch):
    def wrong(record_path, info_path, **kw):
        Path(info_path).write_text(json.dumps({"serial_number": "OTHER"}))
        return 0

    monkeypatch.setattr(wd, "run", wrong)
    assert dev.write(record()) == ww.EXIT_APPLY_FAILED


def test_opcd_restart_failure_is_apply_failure(tmp_path):
    dev = make_dev(tmp_path, opcd_rc=1)
    assert dev.write(record()) == ww.EXIT_APPLY_FAILED


def test_inactive_opcd_is_not_a_failure(tmp_path):
    dev = make_dev(tmp_path, opcd_active=False, opcd_rc=0)
    assert dev.write(record()) == ww.EXIT_OK


# --- uid ---------------------------------------------------------------------------

def test_uid_prints_source_and_hex(dev):
    assert ww.cmd_uid(dev.target(), out=dev.out.append, log=dev.log.append) == ww.EXIT_OK
    assert dev.out == [f"uid_source=1 uid={UID.hex()}"]


def test_uid_unreadable_is_undetermined(dev):
    dev.nvmem.unlink()
    assert ww.cmd_uid(dev.target(), out=dev.out.append, log=dev.log.append) == ww.EXIT_UNDETERMINED
    assert dev.out == []


# --- 진입점 ------------------------------------------------------------------------

def test_exit_codes_are_distinct_and_documented():
    codes = {name: getattr(ww, name) for name in dir(ww) if name.startswith("EXIT_")}
    assert len(set(codes.values())) == len(codes)
    doc = (SCRIPTS_DIR.parents[4] / "docs/devinfo_record_format.md").read_text()
    for name, value in codes.items():
        assert f"| {value} | `{name}` |" in doc, name


def test_terminating_signals_raise_system_exit():
    with pytest.raises(SystemExit):
        ww._on_signal(signal.SIGTERM, None)


def test_main_installs_signal_handlers_for_write(monkeypatch, tmp_path):
    seen = {}

    def fake_write(record_path, replace, target, *, out, log):
        seen["hup"] = signal.getsignal(signal.SIGHUP)
        seen["term"] = signal.getsignal(signal.SIGTERM)
        seen["replace"] = replace
        return 0

    monkeypatch.setattr(ww, "cmd_write", fake_write)
    assert ww.main(["write", str(tmp_path / "r.bin"), "--replace"]) == 0
    assert seen == {"hup": ww._on_signal, "term": ww._on_signal, "replace": True}


def test_default_target_uses_service_paths():
    t = ww.Target()
    assert (t.record_device, t.info_path, t.lock_path) == (wd.RECORD_DEVICE, wd.DEVICE_INFO_PATH, wd.LOCK_PATH)
    assert t.force_ro == "/sys/block/mmcblk0boot1/force_ro"
    assert t.public_keys is wd.SHIPPING_PUBLIC_KEYS


def test_script_runs_as_a_file(tmp_path):
    import subprocess
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONSAFEPATH")}
    r = subprocess.run([sys.executable, str(SCRIPTS_DIR / "wifi_devinfo_write.py"), "--help"],
                       capture_output=True, text=True, cwd=tmp_path, env=env, check=False)
    assert r.returncode == 0, r.stderr
    assert (SCRIPTS_DIR / "wifi_devinfo_write.py").read_text().splitlines()[0] == "#!/usr/bin/python3"


# --- 패키지 연결 -----------------------------------------------------------------

WLAN_ROOT = SCRIPTS_DIR.parents[2]
REPO_ROOT = WLAN_ROOT.parents[1]


def _control_field(name):
    line = next(l for l in (WLAN_ROOT / "DEBIAN/control").read_text().splitlines() if l.startswith(f"{name}:"))
    return [d.strip() for d in line.split(":", 1)[1].split(",")]


def test_control_recommends_mmc_utils_without_depending_on_it():
    # python3-cryptography 와 같은 이유(#481 완료 조건 개정, PR #490 A-R1-001): 없는 이미지에서
    # 업그레이드가 configure 에서 멈추지 않게 하고, 기록 도구는 mmc 가 없으면 9 로 끝난다.
    assert "mmc-utils" in _control_field("Recommends")
    assert not any("mmc-utils" in d for d in _control_field("Depends"))


def test_writer_script_and_test_are_in_the_manifests():
    payload = (WLAN_ROOT / "DEBIAN/payload-manifest.txt").read_text().splitlines()
    source = (REPO_ROOT / "scripts/source_archive_manifest.txt").read_text().splitlines()
    assert "usr/local/scripts/wifi_devinfo_write.py" in payload
    assert "dist/wlan/usr/local/scripts/wifi_devinfo_write.py" in source
    assert "dist/wlan/usr/local/scripts/tests/test_wifi_devinfo_write.py" in source
