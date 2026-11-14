"""양산 PC 레코드 발급 도구(#481 PR-4) — 입력 검사, 키, 원장 순서·중복, ssh 인자, 대조.

장치는 가짜 ssh 실행기(FakeDevice)로 바꾼다. 가짜는 원격 명령 문자열만 보고 기록 도구(uid·write)와
대조 스크립트를 흉내 낸다. 레코드는 시험 전용 키로 서명한다.
"""
import csv
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (BestAvailableEncryption, Encoding, NoEncryption,
                                                          PrivateFormat, PublicFormat)

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "dist/wlan/usr/local/scripts"))
import devinfo_issue as di  # noqa: E402
import devinfo_record as dr  # noqa: E402

TEST_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))  # 시험 전용
TEST_PUB = TEST_KEY.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
OTHER_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
TRUSTED = {1: TEST_PUB}
UID = bytes(range(0x10, 0x20))
UID2 = bytes(range(0x20, 0x30))


class Result:
    def __init__(self, returncode, stdout=b"", stderr=b""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


class FakeDevice:
    """원격 명령 문자열로 분기하는 가짜 ssh. 기록 도구·대조 스크립트의 출력 형식을 흉내 낸다."""

    def __init__(self, uid=UID, writer_rc=0, info_override=None, snapshot_override=None, verify_extra=None):
        self.uid = uid
        self.writer_rc = writer_rc
        self.info_override = info_override
        self.snapshot_override = snapshot_override
        self.verify_extra = verify_extra or {}
        self.boot1 = bytes(512)
        self.info = None
        self.calls = []
        self.on_write = None

    def __call__(self, argv, input_bytes=None, timeout=None):
        remote = argv[-1]
        self.calls.append((list(argv), input_bytes))
        if remote == di.UID_COMMAND:
            return Result(0, f"uid_source=1 uid={self.uid.hex()}\n".encode())
        if remote.startswith(di.WRITE_COMMAND):
            if self.on_write:
                self.on_write()
            if self.writer_rc:
                return Result(self.writer_rc, b"", "refused by device\n".encode())
            res = dr.check(input_bytes, TRUSTED, (1, self.uid))
            if res.status != dr.STATUS_VALID:
                return Result(3, b"", b"invalid\n")
            out = ""
            if input_bytes == self.boot1:
                out = "result=unchanged\n"
            else:
                if self.boot1[:8] == dr.MAGIC:
                    if "--replace" not in remote.split():
                        return Result(5, b"", b"replace required\n")
                    out = f"previous={self.boot1.hex()}\n"
                self.boot1 = input_bytes
                out += "result=written\n"
            self.info = dr.device_info_json(res.record.values)
            return Result(0, out.encode())
        if remote == di.VERIFY_COMMAND:
            assert input_bytes == di.VERIFY_SCRIPT.encode()
            info = self.info_override if self.info_override is not None else self.info
            snap = {"result": 0,
                    "vendor_code": int(self.info["vendor_code"], 16),
                    "product_code": int(self.info["product_code"], 16),
                    "product_subcode": int(self.info["product_subcode"], 16),
                    "manufacture_date": self.info["manufacture_date"],
                    "shipment_date": self.info["shipment_date"],
                    "hardware_version": self.info["hardware_version"],
                    "serial_number": self.info["serial_number"],
                    "ieee_11r": self.info["ieee_11r"], "ieee_11ai": self.info["ieee_11ai"],
                    "ieee_11k": self.info["ieee_11k"], "ieee_11v": self.info["ieee_11v"]}
            if self.snapshot_override:
                snap.update(self.snapshot_override)
            out = {"info": info, "snapshot": snap, "opcd_state": "inactive", "opcd_was_active": False,
                   "opcd_start_attempted": True, "opcd_started": True, "opcd_stop_rc": 0,
                   "opcd_state_after": "inactive", "password_file": False, "device_info_rc": 0,
                   "removed": [], "left": []}
            out.update(self.verify_extra)
            return Result(0, (json.dumps(out) + "\n").encode())
        raise AssertionError(f"unexpected remote command: {remote!r}")

    def remotes(self):
        return [argv[-1] for argv, _ in self.calls]


def write_pem(path, key=TEST_KEY, password=None):
    enc = BestAvailableEncryption(password) if password else NoEncryption()
    path.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, enc))
    return str(path)


ARGS = ["--host", "192.0.2.10", "--serial", "SN-0001", "--hardware-version", "HW-1.0",
        "--product-code", "0x0103", "--subcode", "KR",
        "--manufacture-date", "2026-11-01", "--shipment-date", "2026-11-14", "--yes"]


class Env:
    def __init__(self, tmp_path, monkeypatch, device=None):
        self.tmp = tmp_path
        self.key = write_pem(tmp_path / "key.pem")
        self.ledger = tmp_path / "ledger.csv"
        self.device = device or FakeDevice()
        self.logs = []
        monkeypatch.setattr(di, "_tool_commit", lambda: "abc1234")

    def run(self, *extra, device=None, args=ARGS):
        argv = list(args) + ["--key", self.key, "--ledger", str(self.ledger)] + list(extra)
        return di.issue(di.parse_args(argv), runner=device or self.device, trusted_keys=TRUSTED,
                        log=self.logs.append, confirm=lambda prompt: "y")

    def rows(self):
        with open(self.ledger, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))


@pytest.fixture
def env(tmp_path, monkeypatch):
    return Env(tmp_path, monkeypatch)


# --- 성공 경로 --------------------------------------------------------------------

def test_issue_writes_and_verifies_with_three_connections(env):
    assert env.run() == di.EXIT_OK
    assert env.device.remotes() == [di.UID_COMMAND, di.WRITE_COMMAND, di.VERIFY_COMMAND]
    record = env.device.calls[1][1]
    assert dr.check(record, TRUSTED, (1, UID)).status == dr.STATUS_VALID
    phases = [r["phase"] for r in env.rows()]
    assert phases == ["signed", "result", "verify"]
    signed = env.rows()[0]
    assert signed["record_hex"] == record.hex() and signed["uid"] == UID.hex() and signed["serial"] == "SN-0001"
    assert signed["key_id"] == "1" and signed["tool_commit"] == "abc1234"
    assert env.rows()[1]["result"] == "written" and env.rows()[2]["verify"] == "ok"


def test_signed_row_is_on_disk_before_the_device_write(env):
    seen = {}
    env.device.on_write = lambda: seen.update(phases=[r["phase"] for r in env.rows()])
    env.run()
    assert seen["phases"] == ["signed"]


def test_remote_commands_never_carry_operator_input(env):
    hostile = ["--serial", "SN;$(reboot)`x`|&'"]
    args = [a for a in ARGS]
    args[args.index("--serial") + 1] = hostile[1]
    assert env.run(args=args) == di.EXIT_OK
    for argv, _ in env.device.calls:
        assert argv[-1] in (di.UID_COMMAND, di.WRITE_COMMAND, di.VERIFY_COMMAND)
        assert all(hostile[1] not in a for a in argv)


def test_ssh_uses_a_fresh_known_hosts_file_per_run(env):
    env.run()
    argvs = [argv for argv, _ in env.device.calls]
    kh = {a.split("=", 1)[1] for argv in argvs for a in argv if a.startswith("UserKnownHostsFile=")}
    assert len(kh) == 1
    path = kh.pop()
    assert " " not in path and not os.path.exists(os.path.dirname(path))  # 실행이 끝나면 디렉터리째 지운다
    for argv in argvs:
        assert "StrictHostKeyChecking=accept-new" in argv and "-T" in argv
        assert argv[argv.index("-p") + 1] == "22" and "root@192.0.2.10" in argv


def test_identity_file_is_passed_only_when_given(env, tmp_path):
    env.run()
    argv = env.device.calls[0][0]
    assert "-i" not in argv and "IdentitiesOnly=yes" not in argv
    env.device.calls.clear()
    env.run("--identity", str(tmp_path / "id_ed25519"))
    argv = env.device.calls[0][0]
    assert argv[argv.index("-i") + 1] == str(tmp_path / "id_ed25519") and "IdentitiesOnly=yes" in argv


def test_system_known_hosts_are_not_consulted(env):
    env.run()
    argv = env.device.calls[0][0]
    user = next(a.split("=", 1)[1] for a in argv if a.startswith("UserKnownHostsFile="))
    glob = next(a.split("=", 1)[1] for a in argv if a.startswith("GlobalKnownHostsFile="))
    assert os.path.dirname(glob) == os.path.dirname(user) and glob != user


@pytest.mark.parametrize("dirname", ["with space", "pct%dir"])  # ssh -o 값은 공백에서 잘리고 % 는 펼친다
def test_known_hosts_dir_with_spaces_is_refused(env, monkeypatch, tmp_path, dirname):
    spaced = tmp_path / dirname
    spaced.mkdir()
    monkeypatch.setattr(di.tempfile, "gettempdir", lambda: str(spaced))
    monkeypatch.setenv("TMP", str(spaced))
    monkeypatch.setenv("TEMP", str(spaced))
    monkeypatch.setenv("TMPDIR", str(spaced))
    monkeypatch.setattr(di.tempfile, "tempdir", None)
    assert env.run() == di.EXIT_INPUT
    assert env.device.calls == []


# --- 입력 검사: 장치에 닿기 전에 멈춘다 ----------------------------------------------

@pytest.mark.parametrize("flag, value", [
    ("--product-code", "0x0203"),    # 제품 분류 밖
    ("--product-code", "0x0104"),    # 무선 규격 밖
    ("--subcode", "US"),
    ("--subcode", "0x1234"),
    ("--ieee", "1,0,1,2"),
    ("--serial", 'SN"1'),            # opcd 가 못 읽는 문자
    ("--serial", ""),                # 배치 파일의 빈 변수
    ("--serial", " SN-0001"),
    ("--hardware-version", " "),
    ("--hardware-version", "HW-1.0 "),
    ("--hardware-version", "H" * 32),
    ("--manufacture-date", "1969-12-31"),
    ("--shipment-date", "2026-02-30"),
])
def test_values_outside_the_rules_are_refused_before_any_connection(env, flag, value):
    args = list(ARGS)
    if flag in args:
        args[args.index(flag) + 1] = value
    else:
        args += [flag, value]
    assert env.run(args=args) == di.EXIT_INPUT
    assert env.device.calls == [] and not env.ledger.exists()


@pytest.mark.parametrize("missing", ["--manufacture-date", "--shipment-date", "--serial"])
def test_dates_and_serial_have_no_defaults(env, missing):
    args = list(ARGS)
    i = args.index(missing)
    del args[i:i + 2]
    with pytest.raises(SystemExit) as exc:
        env.run(args=args)
    assert exc.value.code == 2


def test_subcode_accepts_country_letters_and_hex(env):
    args = list(ARGS)
    args[args.index("--subcode") + 1] = "0x5757"
    assert env.run(args=args) == di.EXIT_OK
    assert env.rows()[0]["product_subcode"] == "0x5757"


def test_operator_can_decline_the_confirmation(env):
    argv = [a for a in ARGS if a != "--yes"] + ["--key", env.key, "--ledger", str(env.ledger)]
    rc = di.issue(di.parse_args(argv), runner=env.device, trusted_keys=TRUSTED, log=env.logs.append,
                  confirm=lambda prompt: "n")
    assert rc == di.EXIT_ABORTED and env.device.calls == [] and not env.ledger.exists()


# --- 키 ---------------------------------------------------------------------------

def test_key_not_trusted_by_devices_is_refused(env, tmp_path):
    env.key = write_pem(tmp_path / "other.pem", OTHER_KEY)
    assert env.run() == di.EXIT_KEY
    assert env.device.calls == []


def test_encrypted_key_prompts_for_its_password(env, tmp_path, monkeypatch):
    env.key = write_pem(tmp_path / "enc.pem", password=b"pw-1234")
    monkeypatch.setattr(di.getpass, "getpass", lambda prompt="": "pw-1234")
    assert env.run() == di.EXIT_OK


def test_key_id_comes_from_the_trusted_table(env, tmp_path):
    rc = di.issue(di.parse_args(ARGS + ["--key", env.key, "--ledger", str(env.ledger)]),
                  runner=env.device, trusted_keys={7: TEST_PUB}, log=env.logs.append, confirm=lambda p: "y")
    assert rc == di.EXIT_DEVICE                     # 가짜 장치는 key_id 1 만 믿는다
    assert env.rows()[0]["key_id"] == "7"


# --- 원장: 중복·이어서 하기·잠금 ------------------------------------------------------

def test_rerun_with_same_serial_uid_and_values_resumes(env):
    assert env.run() == di.EXIT_OK
    assert env.run() == di.EXIT_OK
    phases = [r["phase"] for r in env.rows()]
    assert phases.count("signed") == 1               # 같은 레코드는 다시 적지 않는다
    assert env.rows()[-2]["result"] == "unchanged"


@pytest.mark.parametrize("change", ["uid", "serial", "values"])
def test_reissue_is_required_when_serial_or_uid_was_used(env, change):
    assert env.run() == di.EXIT_OK
    args = list(ARGS)
    device = env.device
    if change == "uid":
        device = FakeDevice(uid=UID2)
    elif change == "serial":
        args[args.index("--serial") + 1] = "SN-0002"
    else:
        args[args.index("--hardware-version") + 1] = "HW-2.0"
    before = len(env.rows())
    assert env.run(args=args, device=device) == di.EXIT_LEDGER
    assert len(env.rows()) == before
    replace = ["--replace"] if change in ("serial", "values") else []
    assert env.run("--reissue", "--reason", "보드 교체", *replace, args=args, device=device) == di.EXIT_OK
    assert env.rows()[before]["reissue_reason"] == "보드 교체"


def test_going_back_to_a_superseded_record_needs_reissue(env):
    assert env.run() == di.EXIT_OK                                           # R1
    newer = list(ARGS)
    newer[newer.index("--hardware-version") + 1] = "HW-2.0"
    assert env.run("--reissue", "--reason", "HW 정정", "--replace", args=newer) == di.EXIT_OK  # R2
    before = len(env.rows())
    assert env.run("--replace") == di.EXIT_LEDGER                            # R1 로 되돌리기
    assert len(env.rows()) == before and env.device.boot1.hex() == env.rows()[-3]["record_hex"]
    assert env.run("--reissue", "--reason", "되돌림", "--replace") == di.EXIT_OK
    assert env.rows()[before]["phase"] == "signed" and env.rows()[before]["reissue_reason"] == "되돌림"


def test_reissue_needs_a_reason(env):
    with pytest.raises(SystemExit):
        env.run("--reissue")


def test_ledger_lock_refuses_a_second_session(env):
    lock = Path(str(env.ledger) + ".lock")
    lock.write_text("other session\n")
    assert env.run() == di.EXIT_LEDGER
    assert env.device.calls == []
    assert lock.read_text() == "other session\n"                 # 남의 잠금은 지우지 않는다


def _rewrite_ledger(env, edit, encoding="utf-8"):
    with open(env.ledger, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    edit(rows)
    with open(env.ledger, "w", newline="", encoding=encoding) as f:
        w = csv.DictWriter(f, fieldnames=di.LEDGER_FIELDS)
        w.writeheader()
        w.writerows(rows)


def test_ledger_saved_with_a_bom_still_works(env):
    assert env.run() == di.EXIT_OK
    _rewrite_ledger(env, lambda rows: None, encoding="utf-8-sig")             # Excel "CSV UTF-8"
    assert env.run() == di.EXIT_OK                                            # 이어서 하기
    other = list(ARGS)
    other[other.index("--hardware-version") + 1] = "HW-2.0"
    assert env.run(args=other) == di.EXIT_LEDGER                              # 중복도 그대로 잡는다


@pytest.mark.parametrize("damage", ["leading_zero", "cp949", "header"])
def test_a_ledger_changed_by_another_program_stops_the_tool(env, damage):
    args = list(ARGS)
    args[args.index("--serial") + 1] = "000123"
    assert env.run(args=args) == di.EXIT_OK
    if damage == "leading_zero":                                              # Excel 이 숫자로 바꾼 시리얼
        _rewrite_ledger(env, lambda rows: [r.update(serial="123") for r in rows])
    elif damage == "cp949":
        _rewrite_ledger(env, lambda rows: [r.update(operator="작업자") for r in rows], encoding="cp949")
    else:
        env.ledger.write_text(env.ledger.read_text(encoding="utf-8").replace("time_utc,", "time,", 1),
                              encoding="utf-8")
    second = FakeDevice(uid=UID2)                                             # 같은 시리얼, 다른 장치
    assert env.run(args=args, device=second) == di.EXIT_LEDGER
    assert second.remotes() == []                                             # 장치에 연결하지 않는다


def test_unwritable_ledger_is_refused_before_any_connection(env, tmp_path):
    env.ledger = tmp_path / "missing-dir" / "ledger.csv"
    assert env.run() == di.EXIT_LEDGER
    assert env.device.calls == []


def test_ledger_has_no_blank_lines_and_keeps_the_header_once(env):
    env.run()
    env.run()
    raw = env.ledger.read_bytes()
    assert b"\r\r\n" not in raw and b"\n\n" not in raw
    assert raw.count(b"time_utc,phase,") == 1


def test_lock_is_released_after_the_run(env):
    env.run()
    assert not Path(str(env.ledger) + ".lock").exists()


# --- 장치 기록·대조 실패 ------------------------------------------------------------

def test_device_text_never_reaches_the_ledger_verbatim(env):
    def runner(argv, input_bytes=None, timeout=None):
        r = env.device(argv, input_bytes, timeout)
        if argv[-1].startswith(di.WRITE_COMMAND):                             # 응답한 호스트가 돌려준 글자
            return Result(r.returncode, b"previous==1+2\nresult=@SUM(1+2)\n", b"")
        return r
    env.run(device=runner)
    row = [r for r in env.rows() if r["phase"] == "result"][0]
    assert row["result"] == "invalid" and row["previous_hex"] == "invalid"
    assert not any("@SUM" in line or "=1+2" in line for line in env.logs)


def test_device_refusal_is_recorded_and_reported(env):
    env.device.writer_rc = 5
    assert env.run() == di.EXIT_DEVICE
    result = env.rows()[1]
    assert result["phase"] == "result" and result["device_rc"] == "5"
    assert any("--replace" in line for line in env.logs)


def test_replace_records_the_previous_record(env):
    env.run()
    args = list(ARGS)
    args[args.index("--hardware-version") + 1] = "HW-2.0"
    assert env.run("--reissue", "--reason", "값 정정", "--replace", args=args) == di.EXIT_OK
    result = [r for r in env.rows() if r["phase"] == "result"][-1]
    assert result["previous_hex"] == env.rows()[0]["record_hex"]


@pytest.mark.parametrize("exc", [FileNotFoundError(2, "ssh"), subprocess.TimeoutExpired("ssh", 120)])
def test_ssh_that_cannot_run_or_times_out_is_a_device_error(env, exc):
    def runner(argv, input_bytes=None, timeout=None):
        raise exc
    assert env.run(device=runner) == di.EXIT_DEVICE
    assert not os.path.exists(str(env.ledger) + ".lock")


@pytest.mark.parametrize("line", [b"uid_source=1 uid=0011223344556677\n",            # 길이가 다르다
                                  b"uid_source=9 uid=" + UID.hex().encode() + b"\n"])  # 모르는 출처
def test_unusable_uid_from_the_device_is_a_device_error(env, line):
    def runner(argv, input_bytes=None, timeout=None):
        assert argv[-1] == di.UID_COMMAND, "UID 를 쓸 수 없으면 더 연결하지 않는다"
        return Result(0, line)
    assert env.run(device=runner) == di.EXIT_DEVICE
    assert env.rows() == []


@pytest.mark.parametrize("rc, keep_output", [(255, False), (1, True)])  # 출력이 맞아도 rc 가 0 이 아니면 실패
def test_lost_verify_connection_is_recorded(env, rc, keep_output):
    def runner(argv, input_bytes=None, timeout=None):
        r = env.device(argv, input_bytes, timeout)
        if argv[-1] == di.VERIFY_COMMAND:
            return Result(rc, r.stdout if keep_output else b"", b"Connection reset\n")
        return r
    assert env.run(device=runner) == di.EXIT_VERIFY
    assert env.rows()[-1]["phase"] == "verify" and env.rows()[-1]["verify"] == f"error:rc {rc}"


def test_self_check_failure_stops_before_the_ledger_and_the_device(env, monkeypatch):
    monkeypatch.setattr(di.dr, "check", lambda *a, **k: dr.CheckResult(dr.STATUS_INVALID, "forced"))
    assert env.run() == di.EXIT_KEY
    assert env.device.remotes() == [di.UID_COMMAND] and env.rows() == []


@pytest.mark.parametrize("phase", ["signed", "result", "verify"])
def test_ledger_write_failure_mid_run_is_a_ledger_error(env, monkeypatch, phase):
    real = di.Ledger._write

    def write(self, row):
        if row["phase"] == phase:
            raise PermissionError(13, "locked by another program")
        return real(self, row)
    monkeypatch.setattr(di.Ledger, "_write", write)
    assert env.run() == di.EXIT_LEDGER
    if phase == "signed":
        assert env.device.remotes() == [di.UID_COMMAND]      # 서명 행을 못 적으면 장치에 쓰지 않는다
    assert not os.path.exists(str(env.ledger) + ".lock")


def test_ssh_failure_is_a_device_error(env):
    def broken(argv, input_bytes=None, timeout=None):
        return Result(255, b"", b"ssh: connect to host failed\n")
    assert env.run(device=broken) == di.EXIT_DEVICE
    assert not env.ledger.exists() or [r["phase"] for r in env.rows()] == []


def test_files_left_on_the_device_are_shown_to_the_operator(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch, device=FakeDevice(verify_extra={"left": ["/usr/local/opc/etc/iplist.txt"]}))
    assert env.run() == di.EXIT_OK
    assert any("/usr/local/opc/etc/iplist.txt" in line for line in env.logs)


@pytest.mark.parametrize("override", [
    {"info_override": {"serial_number": "OTHER"}},
    {"snapshot_override": {"shipment_date": "2026-01-01"}},
    {"snapshot_override": {"ieee_11k": 0}},
    {"snapshot_override": {"result": 1}},
    {"verify_extra": {"password_file": True}},
    {"verify_extra": {"snapshot": {"_error": "FileNotFoundError"}}},
    {"verify_extra": {"opcd_stop_rc": 1, "opcd_state_after": "active"}},      # 띄운 opcd 를 멈추지 못했다
    {"verify_extra": {"opcd_state_after": "active"}},
    {"verify_extra": {"opcd_state": "activating", "opcd_start_attempted": False, "opcd_started": False}},
])
def test_verification_mismatch_fails_and_is_recorded(tmp_path, monkeypatch, override):
    env = Env(tmp_path, monkeypatch, device=FakeDevice(**override))
    assert env.run() == di.EXIT_VERIFY
    assert env.rows()[-1]["phase"] == "verify" and env.rows()[-1]["verify"] != "ok"


# --- 이식성 ------------------------------------------------------------------------

def test_tool_imports_without_fcntl_like_on_windows():
    code = ("import sys; sys.modules['fcntl'] = None; "
            f"sys.path.insert(0, {str(HERE)!r}); import devinfo_issue; print('ok')")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert r.returncode == 0 and r.stdout.strip() == "ok", r.stderr


def test_shipping_keys_live_in_the_portable_module():
    import wifi_devinfo as wd  # 장치 쪽(리눅스) 모듈
    assert wd.SHIPPING_PUBLIC_KEYS is dr.SHIPPING_PUBLIC_KEYS
    assert set(dr.SHIPPING_PUBLIC_KEYS) == {1}


def test_verify_script_is_valid_python():
    compile(di.VERIFY_SCRIPT, "verify", "exec")


# --- 대조 스크립트를 실제로 실행한다: 장치 경로를 tmp 로 바꾸고 systemctl·vhlctl 을 가짜로 둔다 ----------

FAKE_SYSTEMCTL = """#!/bin/sh
echo "systemctl $*" >> "$FAKE_LOG"
case "$1" in
  is-active) s=$(cat "$FAKE_STATE"); echo "$s"; [ "$s" = active ] && exit 0; exit 3 ;;
  start) [ -n "$FAKE_START_RC" ] && { echo failed > "$FAKE_STATE"; mkdir -p "$FAKE_ETC/temp"; exit "$FAKE_START_RC"; }
         echo active > "$FAKE_STATE"; mkdir -p "$FAKE_ETC/temp"; echo x > "$FAKE_ETC/temp/f"
         if [ -f "$FAKE_ADMIN/iplist.txt" ] && [ -n "$FAKE_REJECT" ]; then
             mv "$FAKE_ADMIN/iplist.txt" "$FAKE_ADMIN/iplist.txt.rejected"; echo default > "$FAKE_ADMIN/iplist.txt"
             echo default > "$FAKE_ETC/iplist.user.last"
         elif [ ! -f "$FAKE_ADMIN/iplist.txt" ]; then
             echo default > "$FAKE_ADMIN/iplist.txt"; echo default > "$FAKE_ETC/iplist.user.last"; fi
         exit 0 ;;
  stop) [ -n "$FAKE_STOP_RC" ] && exit "$FAKE_STOP_RC"; echo inactive > "$FAKE_STATE"; exit 0 ;;
esac
exit 1
"""
FAKE_VHLCTL = """#!/bin/sh
echo "vhlctl $*" >> "$FAKE_LOG"
case " $* " in
  *" device-info "*) [ -n "$FAKE_HUP" ] && kill -HUP "$PPID" && sleep 5
                     [ -n "$FAKE_NO_SNAP" ] && exit 2
                     mkdir -p "$FAKE_SHM"; printf '%s' "$FAKE_SNAP_JSON" > "$FAKE_SHM/device_info.json" ;;
esac
exit 0
"""


class LocalDevice:
    def __init__(self, root, opcd_state="inactive"):
        self.etc, self.admin, self.shm, self.bin = (root / "opc/etc", root / "home/admin", root / "shm/opcd",
                                                    root / "bin")
        for d in (self.etc, self.admin, self.bin):
            d.mkdir(parents=True)
        for name, body in (("systemctl", FAKE_SYSTEMCTL), ("vhlctl", FAKE_VHLCTL)):
            (self.bin / name).write_text(body)
            (self.bin / name).chmod(0o755)
        self.log, self.state = root / "calls.log", root / "state"
        self.state.write_text(opcd_state + "\n")
        self.env = dict(os.environ, PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}", FAKE_LOG=str(self.log),
                        FAKE_STATE=str(self.state), FAKE_ETC=str(self.etc), FAKE_ADMIN=str(self.admin),
                        FAKE_SHM=str(self.shm), FAKE_SNAP_JSON=json.dumps({"result": 0, "serial_number": "SN-0001"}))

    def script(self):
        text = di.VERIFY_SCRIPT
        subs = {'"/usr/local/opc/etc/device_info.json"': self.etc / "device_info.json",
                '"/usr/local/opc/etc/password"': self.etc / "password",
                '"/usr/local/opc/etc/temp"': self.etc / "temp",
                '"/home/admin/iplist.txt"': self.admin / "iplist.txt",
                '"/usr/local/opc/etc/iplist.user.last"': self.etc / "iplist.user.last",
                '"/dev/shm/opcd/device_info.json"': self.shm / "device_info.json",
                '"/usr/local/opc/etc"': self.etc, '"/home/admin"': self.admin,
                '"/usr/local/opc/bin/vhlctl"': self.bin / "vhlctl"}
        for old, new in subs.items():
            assert text.count(old) == 1, old          # 바꿀 자리가 정확히 하나여야 한다(조용한 무효 방지)
            text = text.replace(old, json.dumps(str(new)))
        return text

    def run(self, expect_rc=0, **env):
        r = subprocess.run([sys.executable, "-"], input=self.script(), capture_output=True, text=True,
                           env=dict(self.env, **env), check=False, timeout=30)
        assert r.returncode == expect_rc, r.stderr
        return json.loads(r.stdout.strip().splitlines()[-1])

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def opcd(self):
        return self.state.read_text().strip()


needs_sh = pytest.mark.skipif(os.name == "nt", reason="장치(리눅스)에서 도는 스크립트")


@needs_sh
def test_verify_starts_a_stopped_opcd_and_removes_only_its_known_residue(tmp_path):
    dev = LocalDevice(tmp_path)
    (dev.etc / "device_info.json").write_text('{"serial_number": "SN-0001"}')
    (dev.etc / "keep.conf").write_text("x")
    (dev.admin / "keep.txt").write_text("x")
    out = dev.run()
    assert out["opcd_state"] == "inactive" and out["opcd_start_attempted"] is True and out["opcd_started"] is True
    assert out["opcd_stop_rc"] == 0 and out["opcd_state_after"] == "inactive" and dev.opcd() == "inactive"
    assert sorted(os.listdir(dev.etc)) == ["device_info.json", "keep.conf"]
    assert os.listdir(dev.admin) == ["keep.txt"]
    assert sorted(out["removed"]) == sorted([str(dev.etc / "temp"), str(dev.admin / "iplist.txt"),
                                             str(dev.etc / "iplist.user.last")])
    assert out["left"] == []
    assert out["info"] == {"serial_number": "SN-0001"} and out["snapshot"]["serial_number"] == "SN-0001"
    assert out["password_file"] is False and out["device_info_rc"] == 0
    calls = dev.calls()
    assert [c for c in calls if c.startswith("systemctl")] == [
        "systemctl is-active opcd.service", "systemctl start opcd.service", "systemctl stop opcd.service",
        "systemctl is-active opcd.service"]
    vhl = [c.split()[-1] if "login" not in c else "login" for c in calls if c.startswith("vhlctl")]
    assert vhl == ["login", "device-info", "logout"]


@needs_sh
def test_verify_also_starts_an_opcd_that_failed_before(tmp_path):
    dev = LocalDevice(tmp_path, opcd_state="failed")
    out = dev.run()
    assert out["opcd_start_attempted"] is True and out["opcd_started"] is True
    assert out["snapshot"]["serial_number"] == "SN-0001" and out["opcd_state_after"] == "inactive"


@needs_sh
def test_verify_keeps_a_file_opcd_set_aside_and_reports_it(tmp_path):
    dev = LocalDevice(tmp_path)
    (dev.admin / "iplist.txt").write_text("customer list")                  # opcd 가 거부해 .rejected 로 옮긴다
    out = dev.run(FAKE_REJECT="1")
    assert (dev.admin / "iplist.txt.rejected").read_text() == "customer list"
    assert out["left"] == [str(dev.admin / "iplist.txt.rejected")]
    assert str(dev.admin / "iplist.txt") not in out["removed"]              # 전부터 있던 이름은 지우지 않는다
    assert str(dev.etc / "iplist.user.last") in out["removed"]


@needs_sh
def test_verify_leaves_a_running_opcd_and_its_files_alone(tmp_path):
    dev = LocalDevice(tmp_path, opcd_state="active")
    (dev.admin / "iplist.txt").write_text("ip")
    out = dev.run()
    assert out["opcd_was_active"] is True and out["opcd_start_attempted"] is False and out["removed"] == []
    assert not any(c.startswith(("systemctl start", "systemctl stop")) for c in dev.calls())
    assert os.listdir(dev.admin) == ["iplist.txt"] and dev.opcd() == "active"


@needs_sh
@pytest.mark.parametrize("state", ["activating", "deactivating", "reloading"])
def test_verify_does_not_touch_an_opcd_in_transition(tmp_path, state):
    dev = LocalDevice(tmp_path, opcd_state=state)
    out = dev.run()
    assert out["opcd_state"] == state and out["opcd_start_attempted"] is False
    assert not any(c.startswith(("systemctl start", "systemctl stop")) for c in dev.calls())
    assert not any(c.startswith("vhlctl") for c in dev.calls()) and "_error" in out["snapshot"]


@needs_sh
def test_verify_keeps_files_when_opcd_could_not_be_stopped(tmp_path):
    dev = LocalDevice(tmp_path)
    out = dev.run(FAKE_STOP_RC="1")
    assert out["opcd_stop_rc"] == 1 and out["opcd_state_after"] == "active" and out["removed"] == []
    assert (dev.etc / "temp").exists() and (dev.admin / "iplist.txt").exists()
    assert (dev.etc / "iplist.user.last").exists()


@needs_sh
def test_verify_restores_opcd_when_the_start_fails(tmp_path):
    dev = LocalDevice(tmp_path)
    out = dev.run(FAKE_START_RC="1")
    assert out["opcd_start_attempted"] is True and out["opcd_started"] is False
    assert "systemctl stop opcd.service" in dev.calls() and dev.opcd() == "inactive"
    assert out["removed"] == [str(dev.etc / "temp")]


@needs_sh
@pytest.mark.parametrize("how", ["exception", "sighup"])
def test_verify_restores_opcd_when_interrupted(tmp_path, how):
    dev = LocalDevice(tmp_path)
    if how == "exception":
        (dev.bin / "vhlctl").unlink()                                       # vhlctl 실행이 예외를 낸다
        out = dev.run(expect_rc=1)
    else:
        out = dev.run(expect_rc=1, FAKE_HUP="1")
    assert dev.opcd() == "inactive" and out["opcd_stop_rc"] == 0
    assert not (dev.etc / "temp").exists() and not (dev.admin / "iplist.txt").exists()
    assert not (dev.etc / "iplist.user.last").exists()


@needs_sh
def test_verify_never_reads_a_stale_snapshot(tmp_path):
    dev = LocalDevice(tmp_path, opcd_state="active")
    dev.shm.mkdir(parents=True)
    (dev.shm / "device_info.json").write_text(json.dumps({"result": 0, "serial_number": "OLD"}))
    out = dev.run(FAKE_NO_SNAP="1")
    assert out["device_info_rc"] == 2 and "_error" in out["snapshot"]


@needs_sh
def test_verify_reports_a_password_file_and_a_missing_info_file(tmp_path):
    dev = LocalDevice(tmp_path, opcd_state="active")
    (dev.etc / "password").write_text("secret")
    out = dev.run()
    assert out["password_file"] is True and "_error" in out["info"]


def test_exit_codes_are_distinct_and_documented():
    codes = {name: getattr(di, name) for name in dir(di) if name.startswith("EXIT_")}
    assert len(set(codes.values())) == len(codes)
    doc = (REPO / "docs/devinfo_record_format.md").read_text(encoding="utf-8")
    for name, value in codes.items():
        assert f"| {value} | `{name}` |" in doc, name


def test_script_runs_as_a_file(tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONSAFEPATH")}
    r = subprocess.run([sys.executable, str(HERE / "devinfo_issue.py"), "--help"], capture_output=True,
                       text=True, cwd=tmp_path, env=env, check=False)
    assert r.returncode == 0, r.stderr


def test_now_is_utc_iso():
    stamp = di._now()
    assert datetime.datetime.fromisoformat(stamp).utcoffset() == datetime.timedelta(0)
