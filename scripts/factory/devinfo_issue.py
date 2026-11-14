#!/usr/bin/env python3
"""양산 PC 에서 장치 하나에 출하 고정값 서명 레코드를 발급한다(#481 PR-4). Windows·Linux 공용.

순서(장치 연결 3 번, 원격 명령은 고정 문자열만):
1. 입력값을 사양서 R2 규칙과 레코드 형식으로 검사한다. 날짜·시리얼에는 기본값이 없다.
2. 개인키(PKCS8 PEM)의 공개키로 장치가 믿는 공개키 표에서 key_id 를 찾는다.
3. 원장 잠금을 잡고 쓸 수 있는지 본다.
4. [ssh 1] 장치 기록 도구의 `uid` 로 UID 를 받아 서명하고, 스스로 판정한다. 원장의 중복을 본 뒤
   `signed` 행을 먼저 적는다(서명이 곧 발급이다).
5. [ssh 2] 레코드를 표준입력으로 보내 `write /dev/stdin [--replace]` 를 부르고 `result` 행을 적는다.
6. [ssh 3] 고정 대조 스크립트로 device_info.json 과 opcd GetDeviceInfo 응답(스냅샷)을 레코드 값과
   대조하고 `verify` 행을 적는다. opcd 가 꺼져 있으면 잠시 띄우고, 그동안 새로 생긴 파일만 지운다.

같은 시리얼·UID·레코드로 다시 실행하면 이어서 한다. 시리얼이나 UID 가운데 하나라도 원장에 있으면
`--reissue --reason` 이 있어야 한다. 장치에 다른 레코드가 있으면 `--replace` 도 필요하다.
CLI·원장 열·종료 코드의 정본은 docs/devinfo_record_format.md 의 "양산 발급 도구" 절이다.
"""
import argparse
import csv
import datetime
import getpass
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile

_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                        "dist", "wlan", "usr", "local", "scripts")
sys.path.insert(0, _SCRIPTS)
import devinfo_record as dr  # noqa: E402

EXIT_OK = 0
EXIT_INPUT = 2
EXIT_LEDGER = 3
EXIT_KEY = 4
EXIT_DEVICE = 5
EXIT_VERIFY = 6
EXIT_ABORTED = 7

UID_COMMAND = "/usr/local/scripts/wifi_devinfo_write.py uid"
WRITE_COMMAND = "/usr/local/scripts/wifi_devinfo_write.py write /dev/stdin"
VERIFY_COMMAND = "python3 -"
SUBCODES = {"KR": 0x4B52, "JP": 0x4A50, "CN": 0x434E, "WW": 0x5757}
LEDGER_FIELDS = ["time_utc", "phase", "serial", "uid_source", "uid", "key_id", "pubkey_sha256",
                 "vendor_code", "product_code", "product_subcode", "ieee_11r", "ieee_11ai", "ieee_11k",
                 "ieee_11v", "manufacture_date", "shipment_date", "hardware_version", "record_hex",
                 "device_rc", "result", "previous_hex", "verify", "reissue_reason", "tool_commit",
                 "operator", "pc", "host"]
_UID_LINE = re.compile(r"^uid_source=(\d+) uid=([0-9a-f]+)$")
_PREVIOUS_HEX = re.compile(r"[0-9a-f]{%d}" % (2 * dr.RECORD_SIZE))
_SNAPSHOT_INT = ("vendor_code", "product_code", "product_subcode", "ieee_11r", "ieee_11ai", "ieee_11k",
                 "ieee_11v")
_SNAPSHOT_STR = ("manufacture_date", "shipment_date", "hardware_version", "serial_number")

# 장치에서 root 로 도는 대조 스크립트(고정 문자열, 입력값을 넣지 않는다). 결과를 JSON 한 줄로 낸다.
VERIFY_SCRIPT = r'''
import json, os, shutil, signal, subprocess
INFO = "/usr/local/opc/etc/device_info.json"
PASSWORD = "/usr/local/opc/etc/password"
SNAP = "/dev/shm/opcd/device_info.json"
# opcd 가 시작하며 만드는 것 가운데 이 스크립트가 지울 수 있는 것(전에 없던 경우만). iplist.user.last 는
# opcd 가 고객 파일(/home/admin/iplist.txt)을 쓸 때 한 쌍으로 쓰는 기록이다(wlan-opc/opcd/handler.c).
RESIDUE = ("/usr/local/opc/etc/temp", "/home/admin/iplist.txt", "/usr/local/opc/etc/iplist.user.last")
WATCH = ("/usr/local/opc/etc", "/home/admin")
VHL = ["/usr/local/opc/bin/vhlctl", "--host", "127.0.0.1", "--port", "50607", "--timeout", "10000"]


class Interrupted(Exception):
    pass


def on_signal(signum, frame):
    raise Interrupted("signal %d" % signum)


def opcd_state():
    r = subprocess.run(["systemctl", "is-active", "opcd.service"], capture_output=True, text=True)
    return r.stdout.strip() or "unknown"


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        return {"_error": "%s: %s" % (type(exc).__name__, exc)}


def names(d):
    try:
        return set(os.listdir(d))
    except OSError:
        return None


for s in (signal.SIGHUP, signal.SIGTERM):
    signal.signal(s, on_signal)
out = {"info": load(INFO), "password_file": os.path.exists(PASSWORD), "snapshot": {"_error": "not queried"},
       "device_info_rc": None, "removed": [], "left": []}
state = opcd_state()
out["opcd_state"] = state
out["opcd_was_active"] = state == "active"
attempt = state in ("inactive", "failed")   # 그 밖(activating 등)은 건드리지 않고 대조 실패로 둔다
out["opcd_start_attempted"] = attempt
out["opcd_started"] = False
before = {p: os.path.lexists(p) for p in RESIDUE}
seen = {d: names(d) for d in WATCH}
try:
    if attempt:
        out["opcd_started"] = subprocess.run(["systemctl", "start", "opcd.service"]).returncode == 0
    if state == "active" or out["opcd_started"]:
        try:
            os.unlink(SNAP)
        except FileNotFoundError:
            pass
        subprocess.run(VHL + ["login", "--password", "MyPassword"], capture_output=True)
        out["device_info_rc"] = subprocess.run(VHL + ["device-info"], capture_output=True).returncode
        out["snapshot"] = load(SNAP)
        subprocess.run(VHL + ["logout"], capture_output=True)
finally:
    for s in (signal.SIGHUP, signal.SIGTERM):
        signal.signal(s, signal.SIG_IGN)    # 복원 중에는 멈추지 않는다
    if attempt:
        out["opcd_stop_rc"] = subprocess.run(["systemctl", "stop", "opcd.service"]).returncode
        out["opcd_state_after"] = opcd_state()
        if out["opcd_stop_rc"] == 0 and out["opcd_state_after"] in ("inactive", "failed"):
            for p in RESIDUE:
                if before[p] or not os.path.lexists(p):
                    continue
                if os.path.isdir(p) and not os.path.islink(p):
                    shutil.rmtree(p)
                else:
                    os.unlink(p)
                out["removed"].append(p)
        for d, was in seen.items():
            now = names(d) or set()
            out["left"] += [os.path.join(d, n) for n in sorted(now - (was or set()))
                            if os.path.join(d, n) not in out["removed"]]
    try:
        print(json.dumps(out), flush=True)
    except OSError:
        pass
'''


class Stop(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _log(message):
    print(message, flush=True)


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _tool_commit():
    try:
        r = subprocess.run(["git", "-C", os.path.dirname(os.path.abspath(__file__)), "rev-parse", "--short",
                            "HEAD"], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else "unknown"


def _operator():
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 — 원장 메모용, 실패해도 발급은 계속한다
        return "unknown"


def _run(argv, input_bytes=None, timeout=None):
    return subprocess.run(argv, input=input_bytes, capture_output=True, timeout=timeout, check=False)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="장치 하나에 출하 고정값 서명 레코드를 발급한다(#481).")
    p.add_argument("--host", required=True, help="장치 주소")
    p.add_argument("--port", type=int, default=22)
    p.add_argument("--user", default="root")
    p.add_argument("--ssh", default="ssh", help="ssh 실행 파일(Windows 는 기본 OpenSSH ssh.exe)")
    p.add_argument("--identity", help="SSH 개인키 파일(없으면 ssh 가 비밀번호를 묻는다)")
    p.add_argument("--key", required=True, help="레코드 서명 개인키(PKCS8 PEM)")
    p.add_argument("--ledger", required=True, help="발급 원장 CSV(덧붙이기만 한다)")
    p.add_argument("--serial", required=True)
    p.add_argument("--hardware-version", required=True)
    p.add_argument("--product-code", required=True, help="예: 0x0103")
    p.add_argument("--subcode", required=True, help="KR·JP·CN·WW 또는 16진 값")
    p.add_argument("--manufacture-date", required=True, help="YYYY-MM-DD")
    p.add_argument("--shipment-date", required=True, help="YYYY-MM-DD")
    p.add_argument("--ieee", default="1,0,1,1", help="802.11r,ai,k,v (0/1), 기본 1,0,1,1")
    p.add_argument("--replace", action="store_true", help="장치 boot1 의 다른 레코드를 덮어쓴다")
    p.add_argument("--reissue", action="store_true", help="원장에 이미 있는 시리얼·UID 로 다시 발급한다")
    p.add_argument("--reason", help="재발급 사유(--reissue 와 함께)")
    p.add_argument("--yes", action="store_true", help="입력값 확인 질문을 건너뛴다")
    p.add_argument("--timeout", type=int, default=120, help="ssh 한 번의 제한 시간(초)")
    args = p.parse_args(argv)
    if args.reissue and not (args.reason or "").strip():
        p.error("--reissue 에는 --reason 이 필요하다")
    return args


def _int(text):
    return int(text, 0)


def build_values(args):
    sub = args.subcode.upper()
    subcode = SUBCODES[sub] if sub in SUBCODES else _int(args.subcode)
    flags = [int(x) for x in args.ieee.split(",")]
    if len(flags) != 4:
        raise ValueError("--ieee 는 네 값(11r,ai,k,v)이다")
    values = dr.DeviceValues(
        vendor_code=dr.R2_VENDOR_CODE, product_code=_int(args.product_code), product_subcode=subcode,
        ieee_11r=flags[0], ieee_11ai=flags[1], ieee_11k=flags[2], ieee_11v=flags[3],
        manufacture_date=datetime.date.fromisoformat(args.manufacture_date),
        shipment_date=datetime.date.fromisoformat(args.shipment_date),
        hardware_version=args.hardware_version, serial_number=args.serial)
    for name in ("serial_number", "hardware_version"):
        text = getattr(values, name)
        if not text.strip() or text != text.strip():
            raise ValueError(f"{name}: 비어 있거나 앞뒤에 공백이 있다({text!r})")
    bad = dr.r2_violations(values)
    if bad:
        raise ValueError("사양서 R2 규칙 밖: " + ", ".join(bad))
    # 문자열·날짜·폭 규칙은 레코드 형식이 정한다(장치에서 무효가 될 값으로 만들지 않는다).
    dr.pack_payload(dr.Record(1, dr.UID_SOURCE_IMX93_OCOTP, bytes(16), values))
    return values


def load_key(path, trusted):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, load_pem_private_key
    with open(path, "rb") as f:
        data = f.read()
    try:
        key = load_pem_private_key(data, password=None)
    except TypeError:
        key = load_pem_private_key(data, password=getpass.getpass("개인키 비밀번호: ").encode())
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("Ed25519 개인키가 아니다")
    pub = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    ids = [kid for kid, value in trusted.items() if value == pub]
    if not ids:
        raise ValueError("장치가 믿는 공개키 목록에 이 키의 짝이 없다")
    return key, ids[0], hashlib.sha256(pub).hexdigest()


def _signed_row_problem(row):
    """signed 행의 열(serial·uid_source·uid)이 record_hex 의 내용과 다르면 그 이유, 같으면 ""."""
    try:
        blob = bytes.fromhex(row.get("record_hex") or "")
    except ValueError:
        return "record_hex"
    if len(blob) != dr.RECORD_SIZE or blob[:len(dr.MAGIC)] != dr.MAGIC:
        return "record_hex"
    record, _ = dr._structure(blob[:dr.SIGNED_SIZE])  # 서명 검증 없이 내용만 읽는다(키는 이미 바뀌었을 수 있다)
    if record is None:
        return "record_hex"
    if row.get("serial") != record.values.serial_number:
        return "serial"
    if row.get("uid") != record.uid.hex() or row.get("uid_source") != str(record.uid_source):
        return "uid"
    return ""


class Ledger:
    def __init__(self, path):
        self.path = path
        self.lock = path + ".lock"
        self._locked = False

    def acquire(self):
        try:
            fd = os.open(self.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise Stop(EXIT_LEDGER, f"원장 잠금 파일이 있다({self.lock}): 다른 발급이 진행 중이거나, "
                                    "앞 실행이 비정상 종료했다면 확인한 뒤 지운다") from exc
        except OSError as exc:
            raise Stop(EXIT_LEDGER, f"원장 잠금 파일을 만들 수 없다({self.lock}: {exc})") from exc
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"{os.getpid()} {platform.node()} {_now()}\n")
        self._locked = True
        try:
            with open(self.path, "a", newline="", encoding="utf-8"):
                pass
        except OSError as exc:
            self.release()
            raise Stop(EXIT_LEDGER, f"원장을 쓸 수 없다({self.path}: {exc})") from exc

    def release(self):
        if self._locked:
            try:
                os.unlink(self.lock)
            except FileNotFoundError:
                pass
            self._locked = False

    def rows(self):
        """원장 행. BOM(Excel "CSV UTF-8")은 받는다. 다른 프로그램이 바꾼 원장은 Stop(EXIT_LEDGER)."""
        try:
            with open(self.path, newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
                header = reader.fieldnames
        except FileNotFoundError:
            return []
        except (UnicodeDecodeError, csv.Error) as exc:
            raise Stop(EXIT_LEDGER, f"원장을 읽을 수 없다({self.path}: {exc}). UTF-8 이 아닌 형식으로 저장된 것 "
                                    "같다. 원장은 Excel 등에서 저장하지 않는다") from exc
        if header is None and not rows:
            return []
        if header != LEDGER_FIELDS:
            raise Stop(EXIT_LEDGER, f"원장 열이 이 도구의 열과 다르다({self.path})")
        for n, row in enumerate(rows, start=2):
            if row.get("phase") == "signed" and _signed_row_problem(row):
                raise Stop(EXIT_LEDGER, f"원장 {n}행의 시리얼·UID 가 레코드 내용과 다르다"
                                        f"({_signed_row_problem(row)}). 원장을 다른 프로그램으로 저장했는지 확인한다")
        return rows

    def append(self, row):
        try:
            self._write(row)
        except OSError as exc:
            raise Stop(EXIT_LEDGER, f"원장에 {row.get('phase')} 행을 쓰지 못했다({self.path}: {exc}). 원장을 "
                                    "연 프로그램(Excel 등)을 닫고 같은 값으로 다시 실행한다") from exc

    def _write(self, row):
        with open(self.path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=LEDGER_FIELDS)
            if f.tell() == 0:
                writer.writeheader()
            writer.writerow({k: row.get(k, "") for k in LEDGER_FIELDS})
            f.flush()
            os.fsync(f.fileno())


def _ssh_argv(args, known_hosts, remote):
    argv = [args.ssh, "-T", "-p", str(args.port),
            "-o", f"UserKnownHostsFile={known_hosts}",
            # 시스템 known_hosts 와 섞지 않는다(만들지 않는 파일을 가리킨다).
            "-o", f"GlobalKnownHostsFile={known_hosts}.global",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=3"]
    if args.identity:
        argv += ["-i", args.identity, "-o", "IdentitiesOnly=yes"]
    return argv + [f"{args.user}@{args.host}", remote]


def _text(data):
    return (data or b"").decode("utf-8", "replace")


def _verify(out, values):
    """대조 스크립트 결과에서 어긋난 항목 이름 목록."""
    bad = []
    if out.get("opcd_state") not in ("active", "inactive", "failed"):
        bad.append("opcd_state")
    if out.get("opcd_start_attempted") and (out.get("opcd_stop_rc") != 0
                                            or out.get("opcd_state_after") not in ("inactive", "failed")):
        bad.append("opcd_restore")
    if out.get("password_file"):
        bad.append("password_file")
    if out.get("info") != dr.device_info_json(values):
        bad.append("device_info.json")
    snap = out.get("snapshot") or {}
    if "_error" in snap or snap.get("result") != 0:
        bad.append("getdeviceinfo")
        return bad
    expect = {"vendor_code": values.vendor_code, "product_code": values.product_code,
              "product_subcode": values.product_subcode, "ieee_11r": values.ieee_11r,
              "ieee_11ai": values.ieee_11ai, "ieee_11k": values.ieee_11k, "ieee_11v": values.ieee_11v,
              "manufacture_date": values.manufacture_date.isoformat(),
              "shipment_date": values.shipment_date.isoformat(),
              "hardware_version": values.hardware_version, "serial_number": values.serial_number}
    bad += [f"getdeviceinfo.{k}" for k in _SNAPSHOT_INT + _SNAPSHOT_STR if snap.get(k) != expect[k]]
    return bad


def _issue(args, ledger, runner, trusted, log, values, key, key_id, pub_sha):
    tmp = tempfile.mkdtemp(prefix="devinfo-kh-")
    try:
        if " " in tmp or "%" in tmp:
            raise Stop(EXIT_INPUT, f"임시 디렉터리 경로에 공백이나 % 가 있다({tmp}). TMP·TEMP 를 그런 문자가 없는 "
                                   "경로로 바꾼다")
        kh = os.path.join(tmp, "known_hosts").replace("\\", "/")  # ssh -o 값의 역슬래시 해석을 피한다

        def ssh(remote, data=None):
            try:
                return runner(_ssh_argv(args, kh, remote), input_bytes=data, timeout=args.timeout)
            except (OSError, subprocess.SubprocessError) as exc:
                raise Stop(EXIT_DEVICE, f"ssh 를 실행하지 못했다({remote}): {exc}") from exc

        r = ssh(UID_COMMAND)
        m = None
        for line in _text(r.stdout).splitlines():
            m = _UID_LINE.match(line.strip()) or m
        if r.returncode != 0 or not m:
            raise Stop(EXIT_DEVICE, f"장치 UID 를 받지 못했다(rc {r.returncode}): {_text(r.stderr).strip()}")
        uid_source, uid = int(m.group(1)), bytes.fromhex(m.group(2))
        try:
            blob = dr.sign_record(dr.Record(key_id, uid_source, uid, values), key)
        except dr.RecordError as exc:
            raise Stop(EXIT_DEVICE, f"장치가 알려 준 UID 로 레코드를 만들 수 없다({exc})") from exc
        if dr.check(blob, trusted, (uid_source, uid)).status != dr.STATUS_VALID:
            raise Stop(EXIT_KEY, "만든 레코드가 스스로 판정에서 유효하지 않다")

        base = {"serial": values.serial_number, "uid_source": uid_source, "uid": uid.hex(), "key_id": key_id,
                "pubkey_sha256": pub_sha, "vendor_code": f"0x{values.vendor_code:08X}",
                "product_code": f"0x{values.product_code:04X}",
                "product_subcode": f"0x{values.product_subcode:04X}",
                "ieee_11r": values.ieee_11r, "ieee_11ai": values.ieee_11ai, "ieee_11k": values.ieee_11k,
                "ieee_11v": values.ieee_11v, "manufacture_date": values.manufacture_date.isoformat(),
                "shipment_date": values.shipment_date.isoformat(),
                "hardware_version": values.hardware_version, "record_hex": blob.hex(),
                "tool_commit": _tool_commit(), "operator": _operator(), "pc": platform.node(), "host": args.host}
        signed = [row for row in ledger.rows() if row.get("phase") == "signed"]
        used = [row for row in signed if row["serial"] == values.serial_number or row["uid"] == uid.hex()]
        # 이어서 하기는 이 시리얼·UID 의 가장 최근 발급과 같을 때만이다. 대체된 옛 레코드로 되돌리는 것은 재발급이다.
        latest = used[-1] if used else None
        same = (latest is not None and latest["serial"] == values.serial_number and latest["uid"] == uid.hex()
                and latest["record_hex"] == blob.hex())
        if same:
            log("원장에 같은 시리얼·UID·레코드가 있다: 이어서 한다")
        elif used and not args.reissue:
            prev = "; ".join(f"{row['time_utc']} serial={row['serial']} uid={row['uid']}" for row in used[-3:])
            raise Stop(EXIT_LEDGER, f"이 시리얼이나 UID 로 발급한 기록이 있다({prev}). 다시 발급하려면 "
                                    "--reissue --reason 을 준다")
        else:
            ledger.append(dict(base, time_utc=_now(), phase="signed",
                               reissue_reason=(args.reason or "") if used else ""))

        r = ssh(WRITE_COMMAND + (" --replace" if args.replace else ""), blob)
        out = _text(r.stdout)
        result = next((ln.split("=", 1)[1] for ln in out.splitlines() if ln.startswith("result=")), "")
        previous = next((ln.split("=", 1)[1] for ln in out.splitlines() if ln.startswith("previous=")), "")
        # 장치(응답한 호스트)가 돌려준 글자는 기록 도구의 형식일 때만 원장·로그에 그대로 쓴다.
        result = result if result in ("", "written", "unchanged") else "invalid"
        previous = previous if previous == "" or _PREVIOUS_HEX.fullmatch(previous) else "invalid"
        ledger.append(dict(base, time_utc=_now(), phase="result", device_rc=r.returncode, result=result,
                           previous_hex=previous))
        if r.returncode != 0:
            hint = (" 장치 boot1 에 다른 레코드가 있다. 재발급이면 --reissue --reason … --replace 를 준다."
                    if r.returncode == 5 else "")
            raise Stop(EXIT_DEVICE, f"장치 기록이 실패했다(rc {r.returncode}).{hint} {_text(r.stderr).strip()}")
        log(f"장치 기록: result={result}")

        r = ssh(VERIFY_COMMAND, VERIFY_SCRIPT.encode())
        try:
            verdict = json.loads(_text(r.stdout).strip().splitlines()[-1]) if r.returncode == 0 else None
        except (ValueError, IndexError):
            verdict = None
        if verdict is None:
            ledger.append(dict(base, time_utc=_now(), phase="verify", verify=f"error:rc {r.returncode}"))
            raise Stop(EXIT_VERIFY, f"대조를 하지 못했다(rc {r.returncode}): {_text(r.stderr).strip()}")
        bad = _verify(verdict, values)
        if verdict.get("opcd_started"):
            log(f"opcd 를 잠시 띄웠다가 멈췄다(새로 생겨 지운 것: {verdict.get('removed')})")
        if verdict.get("left"):
            log(f"대조 중 장치에 새로 생겼지만 지우지 않은 것: {verdict.get('left')} — 장치에서 확인한다")
        ledger.append(dict(base, time_utc=_now(), phase="verify", verify="ok" if not bad else
                           "mismatch:" + "+".join(bad)))
        if bad:
            raise Stop(EXIT_VERIFY, "장치 응답이 레코드 값과 다르다: " + ", ".join(bad))
        log(f"발급 완료: serial={values.serial_number} uid={uid.hex()} record_sha256="
            f"{hashlib.sha256(blob).hexdigest()}")
        return EXIT_OK
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def issue(args, *, runner=None, trusted_keys=None, log=None, confirm=input):
    runner = runner or _run
    trusted = dr.SHIPPING_PUBLIC_KEYS if trusted_keys is None else trusted_keys
    log = log or _log
    try:
        values = build_values(args)
    except (ValueError, KeyError, dr.RecordError) as exc:
        log(f"입력값 오류: {exc}")
        return EXIT_INPUT
    try:
        key, key_id, pub_sha = load_key(args.key, trusted)
    except (OSError, ValueError, TypeError) as exc:
        log(f"서명 키 오류: {exc}")
        return EXIT_KEY
    log(f"발급 대상 {args.user}@{args.host}: serial={values.serial_number} hw={values.hardware_version} "
        f"product=0x{values.product_code:04X} subcode=0x{values.product_subcode:04X} "
        f"mfg={values.manufacture_date} ship={values.shipment_date} "
        f"ieee={values.ieee_11r},{values.ieee_11ai},{values.ieee_11k},{values.ieee_11v} key_id={key_id}")
    if not args.yes and confirm("발급할까요? [y/N] ").strip().lower() not in ("y", "yes"):
        log("취소했다")
        return EXIT_ABORTED
    ledger = Ledger(os.path.abspath(args.ledger))
    try:
        ledger.acquire()
        ledger.rows()  # 다른 프로그램이 바꾼 원장은 장치에 연결하기 전에 멈춘다(Stop EXIT_LEDGER)
        return _issue(args, ledger, runner, trusted, log, values, key, key_id, pub_sha)
    except Stop as stop:
        log(str(stop))
        return stop.code
    finally:
        ledger.release()


def main(argv=None):
    return issue(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
