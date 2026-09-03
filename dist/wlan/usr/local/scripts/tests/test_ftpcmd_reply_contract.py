"""ftpcmd 핸들러의 **첫 stdout 줄** 계약.

vsftpd 디스패치는 핸들러의 첫 stdout 줄을 200 응답 본문으로 쓴다(meta-cts-wlan
의 0001-vsftpd-external-command-dispatch.patch). 그래서 첫 줄이 상위 툴과의
호환 계약이다 — 대체 대상인 FXE3000 카드가 이렇게 답하고 툴이 그것을 파싱한다:

    200 IFCUP command successful.
    200 IFCDOWN command successful.
    200 ath0 UP

핸들러를 셸에서 직접 불러 "출력이 맞다"를 본 것만으로는 부족하다는 것이 실측으로
드러났다 — 디스패치를 태워보니 응답이 `200 OK: getifstate` 로, 값이 실려 나오지
않았다(2026-09-03, vsFTPd 3.0.5). 그래서 여기서는 **첫 줄이 무엇인지**를 실행으로
고정한다. 디스패치 자체는 이 저장소가 소유하지 않으므로 그 축은 실기 FTP 시험에서
본다.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

FTPCMD_DIR = Path(__file__).resolve().parents[4] / "opt" / "ftpcmd" / "bin"


def _stub_dir(**commands):
    """PATH 앞에 끼울 스텁 디렉터리. 값은 스텁이 실행할 sh 본문."""
    d = Path(tempfile.mkdtemp())
    for name, body in commands.items():
        p = d / name
        p.write_text("#!/bin/sh\n" + body)
        p.chmod(0o755)
    return d


def run_handler(name, *args, env=None, stubs=None):
    """핸들러를 실제로 실행한다. root 가 필요한 명령은 PATH 스텁으로 대체한다."""
    d = _stub_dir(**(stubs or {}))
    base = {k: v for k, v in os.environ.items() if k != "FTPCMD_LOCAL_ADDR"}
    base["PATH"] = f"{d}:{os.environ['PATH']}"
    base.update(env or {})
    try:
        return subprocess.run(
            [str(FTPCMD_DIR / name), *args],
            capture_output=True, text=True, timeout=30, env=base,
        )
    finally:
        shutil.rmtree(d, ignore_errors=True)


def first_line(text):
    return text.splitlines()[0] if text.splitlines() else ""


class HandlersExist(unittest.TestCase):
    def test_every_handler_is_executable(self):
        for name in ("getifstate", "ifcup", "ifcdown", "rst", "wconnect"):
            path = FTPCMD_DIR / name
            self.assertTrue(path.is_file(), f"{name} 이 없다")
            self.assertTrue(os.access(path, os.X_OK), f"{name} 에 실행비트가 없다")


class FirstLineContract(unittest.TestCase):
    """성공 응답이 될 첫 줄을 실행으로 고정한다."""

    # ip 는 root 를 요구하므로 스텁으로 대체한다. 스텁이 아무 것도 출력하지 않으므로
    # 핸들러가 스스로 찍는 줄만 남는다 — 첫 줄 계약을 그대로 볼 수 있다.
    IP_STUB = {"ip": "exit 0\n"}

    def test_ifcup_first_line_is_the_canned_reply(self):
        r = run_handler("ifcup", "lo", stubs=self.IP_STUB)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(first_line(r.stdout), "IFCUP command successful.")

    def test_ifcdown_first_line_is_the_canned_reply(self):
        r = run_handler("ifcdown", "lo", stubs=self.IP_STUB)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(first_line(r.stdout), "IFCDOWN command successful.")

    def test_getifstate_first_line_carries_the_state(self):
        """조회 명령은 정형 문구가 아니라 **값**이 첫 줄이어야 한다."""
        r = run_handler("getifstate", "lo")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(first_line(r.stdout), r"^lo (UP|DOWN)$")

    def test_getifstate_defaults_to_mlan0_shape(self):
        """인자 없이도 같은 모양이어야 한다(빌드호스트엔 mlan0 이 없어 실패 경로)."""
        r = run_handler("getifstate")
        if r.returncode == 0:
            self.assertRegex(first_line(r.stdout), r"^mlan0 (UP|DOWN)$")
        else:
            self.assertEqual(r.returncode, 2)
            self.assertIn("no such interface: mlan0", r.stdout)


class FailurePaths(unittest.TestCase):
    """실패는 exit code 로 알린다 — 디스패치가 550 으로 매핑한다."""

    def test_unknown_interface_exits_2(self):
        for name in ("getifstate", "ifcup", "ifcdown"):
            r = run_handler(name, "nosuchif9", stubs={"ip": "exit 0\n"})
            self.assertEqual(r.returncode, 2, f"{name}: {r.stdout}{r.stderr}")

    def test_invalid_interface_name_exits_2(self):
        for name in ("getifstate", "ifcup", "ifcdown"):
            r = run_handler(name, "a;b", stubs={"ip": "exit 0\n"})
            self.assertEqual(r.returncode, 2, f"{name}: {r.stdout}{r.stderr}")

    def test_ifcdown_refuses_the_control_interface(self):
        """응답은 핸들러 종료 뒤에 쓰이므로, 세션이 탄 인터페이스를 내리면 조작자가 갇힌다."""
        r = run_handler(
            "ifcdown", "lo",
            env={"FTPCMD_LOCAL_ADDR": "127.0.0.1"},
            stubs={"ip": 'echo "1: lo    inet 127.0.0.1/8 scope host lo"\n'},
        )
        self.assertEqual(r.returncode, 3, f"{r.stdout}{r.stderr}")
        self.assertIn("refusing", r.stdout)


if __name__ == "__main__":
    unittest.main()
