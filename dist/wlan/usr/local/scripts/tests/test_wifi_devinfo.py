"""부팅 검증 서비스(#481 PR-2) — boot1 레코드 판정을 device_info.json 에 반영하는 규칙.

판정과 형식은 devinfo_record 가 맡고(test_devinfo_record.py), 여기서는 판정마다
파일을 어떻게 다루는지 고정한다. 레코드는 시험 전용 키로 서명한다.
"""
import datetime
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))
import devinfo_record as dr  # noqa: E402
import wifi_devinfo as wd  # noqa: E402

TEST_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))  # 시험 전용
TEST_PUB = TEST_KEY.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
KEYS = {1: TEST_PUB}
UID = bytes(range(0x10, 0x20))
VALUES = dr.DeviceValues(
    vendor_code=0x00902CFB, product_code=0x0103, product_subcode=0x4A50,
    ieee_11r=1, ieee_11ai=0, ieee_11k=1, ieee_11v=1,
    manufacture_date=datetime.date(2026, 11, 1),
    shipment_date=datetime.date(2026, 11, 13),
    hardware_version="HW-1.0.0", serial_number="SN-TEST-0001",
)
EXPECTED = dr.device_info_json(VALUES)
MANUAL = '{"vendor_code": "0x00000001", "serial_number": "MANUAL"}\n'

# 출하 공개키(key_id 1). 사내에서 만든 키쌍의 공개 부분이며 개인키는 저장소에 없다.
SHIPPING_KEY_1 = bytes.fromhex("47c2d7ffaa166db2f8a7c40380fe4765bf26fe0da5817122e470270b10d0bd6d")


def record(key_id=1, uid=UID):
    return dr.sign_record(dr.Record(key_id, dr.UID_SOURCE_IMX93_OCOTP, uid, VALUES), TEST_KEY)


class Board:
    """boot1·nvmem·device_info.json 자리를 임시 디렉터리에 둔다."""

    def __init__(self, root: Path):
        self.boot1 = root / "mmcblk0boot1"
        self.nvmem = root / "nvmem"
        self.etc = root / "etc"
        self.etc.mkdir()
        self.info = self.etc / "device_info.json"
        self.logs = []
        nvmem = bytearray(256)
        nvmem[192:208] = UID
        self.nvmem.write_bytes(bytes(nvmem))

    def run(self, keys=None):
        return wd.run(str(self.boot1), str(self.info), uid_path=str(self.nvmem),
                      public_keys=KEYS if keys is None else keys, log=self.logs.append)

    def log_text(self):
        return "\n".join(self.logs)


@pytest.fixture
def board(tmp_path):
    return Board(tmp_path)


def marked_file(board):
    data = dict(EXPECTED, serial_number="OLD-SERIAL")
    board.info.write_text(json.dumps(data))
    return data


# --- 출하 공개키 ---------------------------------------------------------------

def test_shipping_key_list_is_exactly_key_id_1():
    assert wd.SHIPPING_PUBLIC_KEYS == {1: SHIPPING_KEY_1}
    Ed25519PublicKey.from_public_bytes(wd.SHIPPING_PUBLIC_KEYS[1])  # 올바른 Ed25519 공개키


def test_test_key_is_not_trusted_by_the_package():
    assert TEST_PUB not in wd.SHIPPING_PUBLIC_KEYS.values()


def test_default_paths_match_opcd_and_boot1():
    assert wd.RECORD_DEVICE == "/dev/mmcblk0boot1"
    assert wd.DEVICE_INFO_PATH == "/usr/local/opc/etc/device_info.json"


# --- 유효 -----------------------------------------------------------------------

def test_valid_record_writes_marked_json(board):
    board.boot1.write_bytes(record())
    assert board.run() == 0
    assert json.loads(board.info.read_text()) == EXPECTED
    assert EXPECTED["source"] == dr.JSON_SOURCE_MARKER
    assert "유효" in board.log_text()


def test_valid_record_written_file_is_0644_and_leaves_no_temp(board):
    board.boot1.write_bytes(record())
    board.run()
    assert stat.S_IMODE(board.info.stat().st_mode) == 0o644
    assert sorted(p.name for p in board.etc.iterdir()) == ["device_info.json"]


def test_valid_record_same_content_is_not_rewritten(board):
    board.boot1.write_bytes(record())
    board.run()
    before = board.info.stat()
    board.logs.clear()
    assert board.run() == 0
    after = board.info.stat()
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)
    assert "변경 없음" in board.log_text()


def test_valid_record_replaces_stale_marked_and_manual_files(board):
    board.boot1.write_bytes(record())
    for existing in (json.dumps(dict(EXPECTED, serial_number="OLD")), MANUAL, "not json"):
        board.info.write_text(existing)
        assert board.run() == 0
        assert json.loads(board.info.read_text()) == EXPECTED


def test_write_failure_keeps_existing_file_and_fails(board, monkeypatch):
    board.boot1.write_bytes(record())
    board.info.write_text(MANUAL)

    def refuse(src, dst):
        raise PermissionError("read-only")

    monkeypatch.setattr(os, "replace", refuse)
    assert board.run() == 1
    assert board.info.read_text() == MANUAL
    assert sorted(p.name for p in board.etc.iterdir()) == ["device_info.json"]
    assert "기록 실패" in board.log_text()


# --- 없음·무효·모르는 키: 표식 파일만 지운다 -------------------------------------

def _invalid_signature():
    blob = bytearray(record())
    blob[500] ^= 0x01
    return bytes(blob)


NOT_VALID = [
    pytest.param(bytes(512), 0, "레코드 없음", id="absent-zero"),
    pytest.param(b"\xff" * 512, 0, "레코드 없음", id="absent-ff"),
    pytest.param(_invalid_signature(), 1, "레코드 무효", id="invalid-signature"),
    pytest.param("uid", 1, "레코드 무효", id="invalid-uid"),
    pytest.param("key2", 1, "모르는 서명 키", id="unknown-key"),
]


def _blob(spec):
    if spec == "uid":
        return record(uid=bytes(16))
    if spec == "key2":
        return record(key_id=2)
    return spec


@pytest.mark.parametrize("spec, rc, label", NOT_VALID)
def test_not_valid_removes_only_marked_file(board, spec, rc, label):
    board.boot1.write_bytes(_blob(spec))
    marked_file(board)
    assert board.run() == rc
    assert not board.info.exists()
    assert label in board.log_text()


@pytest.mark.parametrize("spec, rc, label", NOT_VALID)
@pytest.mark.parametrize("manual", [MANUAL, "not json", "[]"])
def test_not_valid_keeps_unmarked_manual_file_and_logs_it(board, spec, rc, label, manual):
    board.boot1.write_bytes(_blob(spec))
    board.info.write_text(manual)
    assert board.run() == rc
    assert board.info.read_text() == manual
    assert label in board.log_text()
    assert "unsigned 수동 파일 사용" in board.log_text()


@pytest.mark.parametrize("spec, rc, label", NOT_VALID)
def test_not_valid_without_file_creates_nothing(board, spec, rc, label):
    board.boot1.write_bytes(_blob(spec))
    assert board.run() == rc
    assert not board.info.exists()
    assert label in board.log_text()


def test_unknown_key_log_names_the_key_id(board):
    board.boot1.write_bytes(record(key_id=2))
    board.run()
    assert "key_id 2" in board.log_text()


def test_uid_mismatch_log_shows_both_values(board):
    board.boot1.write_bytes(record(uid=bytes(16)))
    board.run()
    assert UID.hex() in board.log_text() and bytes(16).hex() in board.log_text()


def test_absent_record_does_not_need_device_uid(board):
    board.boot1.write_bytes(bytes(512))
    board.nvmem.unlink()  # UID 출처가 없는 SoC
    assert board.run() == 0
    assert "레코드 없음" in board.log_text()


# --- 판정 불가: 아무것도 바꾸지 않는다 --------------------------------------------

def _short(board):
    board.boot1.write_bytes(record()[:100])


def _missing_device(board):
    pass  # boot1 파일을 만들지 않는다


def _uid_unreadable(board):
    board.boot1.write_bytes(record())
    board.nvmem.unlink()


def _no_cryptography(board, monkeypatch):
    board.boot1.write_bytes(record())
    monkeypatch.setitem(sys.modules, "cryptography.exceptions", None)


@pytest.mark.parametrize("setup", [_short, _missing_device, _uid_unreadable, "crypto"])
@pytest.mark.parametrize("existing", ["marked", "manual", None])
def test_undetermined_changes_nothing(board, monkeypatch, setup, existing):
    if setup == "crypto":
        _no_cryptography(board, monkeypatch)
    else:
        setup(board)
    if existing == "marked":
        marked_file(board)
    elif existing == "manual":
        board.info.write_text(MANUAL)
    before = board.info.read_bytes() if existing else None
    assert board.run() == 1
    assert (board.info.read_bytes() if board.info.exists() else None) == before
    assert "판정 불가" in board.log_text()


def test_main_uses_shipping_keys_and_default_paths(monkeypatch):
    seen = {}

    def fake_run(record_path, info_path, *, uid_path=None, public_keys=None, log=None):
        seen.update(record_path=record_path, info_path=info_path, keys=public_keys)
        return 7

    monkeypatch.setattr(wd, "run", fake_run)
    assert wd.main([]) == 7
    assert seen == {"record_path": wd.RECORD_DEVICE, "info_path": wd.DEVICE_INFO_PATH,
                    "keys": wd.SHIPPING_PUBLIC_KEYS}


def test_script_runs_as_a_file_like_execstart(tmp_path):
    # ExecStart 처럼 파일 경로로 실행한다. 형제 모듈(devinfo_record)은 스크립트 디렉터리에서 찾는다.
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONSAFEPATH")}
    out = subprocess.run([sys.executable, str(SCRIPTS_DIR / "wifi_devinfo.py"), "--help"],
                         capture_output=True, text=True, cwd=tmp_path, env=env, check=False)
    assert out.returncode == 0, out.stderr


def test_shebang_is_system_python3():
    assert (SCRIPTS_DIR / "wifi_devinfo.py").read_text().splitlines()[0] == "#!/usr/bin/python3"


# --- 소비자 오라클: 서비스가 쓴 파일을 opcd 로더가 그대로 읽는다 --------------------
# 오라클 소스(main)와 기대값은 test_devinfo_record.py 의 것을 그대로 쓴다.
from .test_devinfo_record import _OPCD_MAIN, OPCD, _expected  # noqa: E402


@pytest.fixture(scope="module")
def opcd_read(tmp_path_factory):
    if not (OPCD / "inventory.c").is_file():
        pytest.fail(f"{OPCD}/inventory.c not found: run `git submodule update --init wlan-opc`")
    d = tmp_path_factory.mktemp("opcd")
    (d / "main.c").write_text(_OPCD_MAIN, encoding="ascii")
    exe = d / "loader"
    subprocess.run(["cc", "-std=c11", "-I", str(OPCD), "-o", str(exe), str(d / "main.c"),
                    str(OPCD / "inventory.c"), str(OPCD / "json_util.c")],
                   check=True, capture_output=True)

    def read(path):
        out = subprocess.run([str(exe), str(path)], check=True, capture_output=True, text=True).stdout
        return dict(line.split("=", 1) for line in out.splitlines())
    return read


def test_file_written_by_service_is_read_by_opcd(board, opcd_read):
    board.boot1.write_bytes(record())
    assert board.run() == 0
    assert opcd_read(board.info) == _expected(VALUES)


# --- 패키지 연결: 유닛·manifest·postinst·prerm·control --------------------------------

WLAN_ROOT = SCRIPTS_DIR.parents[2]          # dist/wlan
REPO_ROOT = WLAN_ROOT.parents[1]
UNIT_PATH = "etc/systemd/system/wifi_devinfo.service"
SCRIPT_PATH = "usr/local/scripts/wifi_devinfo.py"
POSTINST = WLAN_ROOT / "DEBIAN/postinst"


def _unit_directives():
    out = {}
    for line in (WLAN_ROOT / UNIT_PATH).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "[")):
            continue
        key, _, value = line.partition("=")
        out.setdefault(key, []).append(value)
    return out


def test_unit_runs_once_before_opcd_and_snmpd_read_the_file():
    d = _unit_directives()
    assert d["DefaultDependencies"] == ["no"]
    assert {"local-fs.target", "sysinit.target"} <= set(d["After"][0].split())
    assert {"opcd.service", "snmpd.service"} <= set(d["Before"][0].split())
    assert d["Type"] == ["oneshot"]
    assert d["ExecStart"] == [f"/{SCRIPT_PATH}"]
    assert d["ConditionPathExists"] == [wd.RECORD_DEVICE]
    assert d["WantedBy"] == ["multi-user.target"]
    assert "Requires" not in d and "RemainAfterExit" not in d


def test_unit_and_script_are_in_both_manifests():
    payload = (WLAN_ROOT / "DEBIAN/payload-manifest.txt").read_text().splitlines()
    source = (REPO_ROOT / "scripts/source_archive_manifest.txt").read_text().splitlines()
    for path in (UNIT_PATH, SCRIPT_PATH):
        assert path in payload
        assert f"dist/wlan/{path}" in source


def test_postinst_enables_the_unit_unconditionally():
    enable = next(l for l in POSTINST.read_text().splitlines() if l.strip().startswith("systemctl enable watchdog"))
    assert "wifi_devinfo.service" in enable.split()
    assert "wifi_devinfo" not in (WLAN_ROOT / "usr/local/scripts/wifi_apply_enabled.sh").read_text()


def test_prerm_tears_the_unit_down():
    text = (WLAN_ROOT / "DEBIAN/prerm").read_text()
    assert "stop_service_if_active wifi_devinfo\n" in text
    assert "disable_service_if_enabled wifi_devinfo\n" in text


def test_control_depends_on_python3_cryptography():
    line = next(l for l in (WLAN_ROOT / "DEBIAN/control").read_text().splitlines() if l.startswith("Depends:"))
    assert "python3-cryptography" in [d.strip() for d in line.split(":", 1)[1].split(",")]


def _block(name):
    text = POSTINST.read_text()
    start, end = f"  # >>> {name}\n", f"  # <<< {name}\n"
    assert text.count(start) == 1 and text.count(end) == 1, name
    return text.split(start, 1)[1].split(end, 1)[0]


def test_postinst_applies_the_record_after_config_and_before_opcd_restart():
    text = POSTINST.read_text()
    block = text.index("  # >>> devinfo-apply\n")
    assert text.index("  # <<< timesyncd-start\n") < block < text.index("  # >>> opcd-upgrade-restart\n")


def _run_block(tmp_path, *, device, script_rc):
    block = _block("devinfo-apply")
    assert block.count("/dev/mmcblk0boot1") == 1 and block.count(f"/{SCRIPT_PATH}") == 1
    dev = tmp_path / "boot1"
    if device:
        dev.write_bytes(b"")
    calls, logs = tmp_path / "calls", tmp_path / "logs"
    script = tmp_path / "wifi_devinfo.py"
    script.write_text(f"#!/bin/sh\necho run >> '{calls}'\necho '판정 메시지'\nexit {script_rc}\n")
    script.chmod(0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "logger").write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{logs}'\n")
    (bin_dir / "logger").chmod(0o755)
    body = block.replace("/dev/mmcblk0boot1", str(dev)).replace(f"/{SCRIPT_PATH}", str(script))
    r = subprocess.run(["bash", "-c", f"tag=postinst; KEY=k\n{body}\necho BLOCK_DONE"],
                       env=dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}"),
                       capture_output=True, text=True, timeout=10)
    read = lambda p: p.read_text().splitlines() if p.exists() else []  # noqa: E731
    return r, read(calls), read(logs)


@pytest.mark.parametrize("script_rc, priority", [(0, "local0.info"), (1, "local0.warn")])
def test_postinst_block_runs_the_script_and_never_stops_the_install(tmp_path, script_rc, priority):
    r, calls, logs = _run_block(tmp_path, device=True, script_rc=script_rc)
    assert r.returncode == 0 and r.stdout.strip().endswith("BLOCK_DONE"), r.stderr
    assert calls == ["run"]
    assert len(logs) == 1 and logs[0].startswith(f"-p {priority} ")
    assert f"wifi_devinfo rc={script_rc}: 판정 메시지" in logs[0]


def test_postinst_block_skips_boards_without_boot1(tmp_path):
    r, calls, logs = _run_block(tmp_path, device=False, script_rc=0)
    assert r.returncode == 0 and r.stdout.strip().endswith("BLOCK_DONE")
    assert calls == [] and logs == []
