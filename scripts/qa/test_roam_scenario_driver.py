"""roam_scenario_driver.sh — 스케줄 검증·사전조건·복원 계약.

이 하네스는 실기 상태를 바꾸므로(link.json 주입, DIFF_TH 변경, 로거 정지) 잘못된
입력이나 복원 누락이 곧 장비 상태 오염이 된다. 장치 없이 검증 가능한 축을 여기서
고정한다 — 스케줄 파서, 최강/최약 판정, 유닛 원상복원, ack/dry-run 게이트.

설계 전제는 실기 실측에서 나왔다(스크립트 헤더 주석 참조). 특히 "현재 AP 가 최강이면
전환이 없는 것이 정상"이라 사전조건에서 걸러야 하고, link.json 은 30초 stale 가드가
있어 주입이 주기적이어야 한다.
"""
import os
import shlex
import subprocess
import unittest
from pathlib import Path

QA_DIR = Path(__file__).resolve().parent
DRIVER = QA_DIR / "roam_scenario_driver.sh"


def call(func: str, *args: str, stdin: str = "") -> subprocess.CompletedProcess:
    """스크립트를 source 해 순수 함수 하나만 호출한다(main 은 실행 가드로 막혀 있다)."""
    quoted = " ".join(shlex.quote(a) for a in args)
    cmd = f'source {shlex.quote(str(DRIVER))}; {func} {quoted}'
    return subprocess.run(
        ["bash", "-c", cmd], input=stdin, capture_output=True, text=True, timeout=30
    )


def run_driver(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(DRIVER), *args],
        capture_output=True, text=True, timeout=30,
        env={**os.environ, **(env or {})},
    )


class ScheduleParsing(unittest.TestCase):
    def test_accepts_a_well_formed_line(self):
        r = call("validate_schedule_line", "-85", "20", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip().split("\t"), ["-85", "20", "1"])

    def test_rejects_a_non_negative_rssi(self):
        # 양수 RSSI 는 오타다. 통과시키면 진입 게이트가 절대 안 열려 조용히 무의미해진다.
        self.assertNotEqual(call("validate_schedule_line", "85", "20", "1").returncode, 0)

    def test_rejects_rssi_outside_the_plausible_range(self):
        self.assertNotEqual(call("validate_schedule_line", "-5", "20", "1").returncode, 0)
        self.assertNotEqual(call("validate_schedule_line", "-140", "20", "1").returncode, 0)

    def test_rejects_zero_hold(self):
        self.assertNotEqual(call("validate_schedule_line", "-85", "0", "1").returncode, 0)

    def test_rejects_non_numeric_diff(self):
        self.assertNotEqual(call("validate_schedule_line", "-85", "20", "x").returncode, 0)

    def test_parse_schedule_ignores_comments_and_blanks(self):
        with self.subTest("mixed file"):
            path = Path(self._tmp("sched.tsv", """
# rssi hold diff  note
-85  20  1   진입 게이트 개방

-40  15  7   정상 복귀
"""))
            r = call("parse_schedule", str(path))
            self.assertEqual(r.returncode, 0, r.stderr)
            rows = [l.split("\t") for l in r.stdout.strip().splitlines()]
            self.assertEqual(rows, [["-85", "20", "1"], ["-40", "15", "7"]])

    def test_parse_schedule_rejects_the_whole_file_on_one_bad_line(self):
        path = Path(self._tmp("bad.tsv", "-85 20 1\n-85 0 1\n"))
        r = call("parse_schedule", str(path))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("schedule line 2", r.stderr)

    def _tmp(self, name: str, body: str) -> str:
        import tempfile
        d = tempfile.mkdtemp()
        p = os.path.join(d, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
        self.addCleanup(lambda: os.path.exists(p) and os.remove(p))
        return p


class Preconditions(unittest.TestCase):
    CANDS = "aa:aa:aa:aa:aa:aa -45\nbb:bb:bb:bb:bb:bb -50\ncc:cc:cc:cc:cc:cc -52\n"

    def test_current_is_strongest_detects_no_room_to_roam(self):
        # 최강 AP 에 붙어 있으면 어떤 주입으로도 전환이 안 난다(baseline 이 스캔값이므로).
        r = call("current_is_strongest", "aa:aa:aa:aa:aa:aa", stdin=self.CANDS)
        self.assertEqual(r.returncode, 0)

    def test_current_is_strongest_is_false_when_a_better_ap_exists(self):
        r = call("current_is_strongest", "cc:cc:cc:cc:cc:cc", stdin=self.CANDS)
        self.assertNotEqual(r.returncode, 0)

    def test_weakest_other_skips_the_current_bssid(self):
        r = call("weakest_other_bssid", "cc:cc:cc:cc:cc:cc", stdin=self.CANDS)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "bb:bb:bb:bb:bb:bb")

    def test_weakest_other_fails_when_there_is_no_other_candidate(self):
        r = call("weakest_other_bssid", "aa:aa:aa:aa:aa:aa", stdin="aa:aa:aa:aa:aa:aa -45\n")
        self.assertNotEqual(r.returncode, 0)


class RestoreContract(unittest.TestCase):
    """원래 active 였으면 active 로, inactive 였으면 inactive 로 되돌려야 한다."""

    def _with_stub_systemctl(self, func: str, *args: str) -> tuple[subprocess.CompletedProcess, str]:
        import tempfile
        d = tempfile.mkdtemp()
        stub = Path(d) / "systemctl"
        stub.write_text(
            "#!/bin/sh\n"
            'echo "$@" >> "$SYSTEMCTL_LOG"\n'
            'case "$1" in is-active) [ "$STUB_ACTIVE" = 1 ] && exit 0 || exit 3 ;; esac\n'
            "exit 0\n"
        )
        stub.chmod(0o755)
        log = Path(d) / "calls"
        log.write_text("")
        cmd = f'source {shlex.quote(str(DRIVER))}; {func} ' + " ".join(shlex.quote(a) for a in args)
        r = subprocess.run(
            ["bash", "-c", cmd], capture_output=True, text=True, timeout=30,
            env={**os.environ, "PATH": f"{d}:{os.environ['PATH']}",
                 "SYSTEMCTL_LOG": str(log), "STUB_ACTIVE": self.stub_active},
        )
        return r, log.read_text()

    def test_originally_active_unit_is_started_again(self):
        self.stub_active = "1"
        r, calls = self._with_stub_systemctl("restore_unit_active_state", "u.service", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("start u.service", calls)

    def test_originally_inactive_unit_is_stopped_again(self):
        self.stub_active = "0"
        r, calls = self._with_stub_systemctl("restore_unit_active_state", "u.service", "0")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("stop u.service", calls)


class Gates(unittest.TestCase):
    def _sched(self) -> str:
        import tempfile
        d = tempfile.mkdtemp()
        p = os.path.join(d, "s.tsv")
        with open(p, "w", encoding="utf-8") as f:
            f.write("-85 5 1\n")
        return p

    def test_dry_run_prints_the_plan_without_requiring_ack_or_root(self):
        r = run_driver("--iface", "mlan0", "--schedule", self._sched(), "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("-85", r.stdout)
        self.assertIn("dry-run", r.stdout)

    def test_refuses_to_touch_the_device_without_ack(self):
        r = run_driver("--iface", "mlan0", "--schedule", self._sched())
        self.assertEqual(r.returncode, 66)
        self.assertIn("--ack", r.stderr)

    def test_rejects_an_unknown_interface(self):
        r = run_driver("--iface", "wlan9", "--schedule", self._sched(), "--dry-run")
        self.assertEqual(r.returncode, 64)

    def test_rejects_a_bad_schedule_before_anything_else(self):
        import tempfile
        d = tempfile.mkdtemp()
        p = os.path.join(d, "bad.tsv")
        with open(p, "w", encoding="utf-8") as f:
            f.write("-85 0 1\n")
        r = run_driver("--iface", "mlan0", "--schedule", p, "--ack")
        self.assertEqual(r.returncode, 65)



class RemoteSafety(unittest.TestCase):
    """2026-08-29 사고 회귀 — ssh 세션이 끊겨도 실기에 하네스가 남으면 안 된다.

    당시 로컬 ssh 가 timeout 으로 죽었는데 원격 스크립트가 살아남아 주입 루프가 계속
    돌고 link logger 는 정지된 채 방치됐다. 아래 축이 그때 비어 있던 것들이다.
    """

    def test_inject_loop_exits_when_its_parent_disappears(self):
        """부모가 SIGKILL 돼도 고아 주입 루프가 남지 않아야 한다(사고의 실제 메커니즘)."""
        import json
        import signal
        import tempfile
        import time

        d = tempfile.mkdtemp()
        link = Path(d) / "link.json"
        link.write_text(json.dumps({"link": {"signal": "-40 dBm", "signal_avg": "-40 dBm"}}))

        # 죽일 수 있는 가짜 부모.
        parent = subprocess.Popen(["sleep", "60"])
        loop = subprocess.Popen(
            ["bash", "-c",
             f'source {shlex.quote(str(DRIVER))}; '
             f'inject_loop {shlex.quote(str(link))} -85 {parent.pid}'],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            time.sleep(2)
            self.assertIsNone(loop.poll(), "주입 루프가 부모 생존 중에 죽으면 안 된다")
            self.assertIn("-85", link.read_text(), "주입이 실제로 파일을 바꿔야 한다")

            parent.send_signal(signal.SIGKILL)
            parent.wait(timeout=5)

            # 부모가 사라지면 다음 주기에 스스로 끝나야 한다.
            loop.wait(timeout=10)
            self.assertIsNotNone(loop.poll(), "부모가 사라졌는데 주입 루프가 남았다")
        finally:
            for p in (loop, parent):
                if p.poll() is None:
                    p.kill()

    def test_inject_loop_keeps_the_file_fresh(self):
        """얼려두면 30초 stale 가드에 걸려 판정이 보류된다 — 주기적 재기록이어야 한다."""
        import json
        import tempfile
        import time

        d = tempfile.mkdtemp()
        link = Path(d) / "link.json"
        link.write_text(json.dumps({"link": {"signal": "-40 dBm", "signal_avg": "-40 dBm"}}))
        parent = subprocess.Popen(["sleep", "60"])
        loop = subprocess.Popen(
            ["bash", "-c",
             f'source {shlex.quote(str(DRIVER))}; '
             f'inject_loop {shlex.quote(str(link))} -85 {parent.pid}'],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            time.sleep(1.5)
            first = link.stat().st_mtime
            time.sleep(2.5)
            self.assertGreater(link.stat().st_mtime, first,
                               "재기록이 멈추면 stale 가드에 걸린다")
        finally:
            for p in (loop, parent):
                if p.poll() is None:
                    p.kill()

    def test_restore_is_wired_to_the_disconnect_signals(self):
        """EXIT 만으로는 ssh 절단(HUP)·외부 종료(TERM)에서 복원이 돌지 않는다."""
        body = DRIVER.read_text(encoding="utf-8")
        # assertIn 은 실패 시 스크립트 전문을 덤프해 로그를 못 쓰게 만든다 — 짧게 말한다.
        self.assertTrue(
            "trap restore EXIT INT TERM HUP" in body,
            "trap 이 EXIT 만 잡으면 ssh 절단(HUP)·외부 종료(TERM)에서 복원이 돌지 않는다",
        )

    def test_injector_is_launched_with_detached_stdio(self):
        """주입 루프가 ssh 의 stdout 을 물고 있으면 세션이 닫히지 않아 하네스가 남는다."""
        body = DRIVER.read_text(encoding="utf-8")
        self.assertTrue(
            'inject_loop "$link" "$rssi" "$$" </dev/null >/dev/null 2>&1 &' in body,
            "주입 루프가 ssh 의 stdio 를 물면 세션이 안 닫혀 원격에 하네스가 남는다",
        )

    def test_schedule_total_seconds_sums_the_holds(self):
        """자체 마감시각의 근거 — 신호가 끝내 안 와도 하네스가 무한히 남지 않는다."""
        r = call("schedule_total_seconds", stdin="-85\t20\t1\n-40\t15\t7\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "35")

class RestoreScope(unittest.TestCase):
    """복원 경로가 **최상위 스코프**에서 돌아야 한다.

    EXIT 트랩은 main 이 반환한 **뒤** 최상위에서 실행된다. 복원 함수를 main 안에
    중첩해 두고 main 의 local 을 참조하면, `set -u` 가 그 시점에 unbound variable 로
    복원을 중단시킨다 — 정상 완주 경로만 깨지고 SIGHUP/SIGTERM 경로는 멀쩡해서
    신호 테스트로는 잡히지 않는다. 실기에서 주입 루프·DIFF_TH·로거가 복원되지 않은
    채 남았던 실제 사고 모드다(2026-09-02).
    """

    def _stubs(self):
        import tempfile
        d = Path(tempfile.mkdtemp())
        log = d / "calls"
        log.write_text("")
        for name in ("systemctl", "wpa_cli"):
            stub = d / name
            stub.write_text(
                "#!/bin/sh\n"
                f'echo "{name} $@" >> "$STUB_LOG"\n'
                'case "$1" in is-active) exit 0 ;; esac\n'
                "exit 0\n"
            )
            stub.chmod(0o755)
        return d, log

    def _run(self, body: str) -> tuple[subprocess.CompletedProcess, str]:
        d, log = self._stubs()
        script = d / "harness.sh"
        script.write_text(
            f"source {shlex.quote(str(DRIVER))}\n"
            "IFACE=mlan0\n"
            'RESTORE_UNIT="u.service"\n'
            'RESTORE_ORIG_DIFF="7"\n'
            "RESTORE_WAS_ACTIVE=1\n"
            + body
        )
        r = subprocess.run(
            ["bash", str(script)], capture_output=True, text=True, timeout=30,
            env={**os.environ, "PATH": f"{d}:{os.environ['PATH']}", "STUB_LOG": str(log)},
        )
        return r, log.read_text()

    def test_restore_is_callable_from_top_level_scope(self):
        r, calls = self._run("restore\n")
        self.assertEqual(r.returncode, 0, f"stdout={r.stdout} stderr={r.stderr}")
        self.assertIn("DIFF_TH=7", r.stdout)
        self.assertIn("u.service restored", r.stdout)
        self.assertIn("start u.service", calls)

    def test_exit_trap_armed_inside_a_function_still_restores_after_it_returns(self):
        """실기 순서 그대로 — 함수 안에서 트랩을 걸고, 함수가 반환한 뒤 셸이 끝난다."""
        r, calls = self._run(
            "arm() { trap restore EXIT INT TERM HUP; return 0; }\n"
            "arm\n"
        )
        self.assertEqual(r.returncode, 0, f"stdout={r.stdout} stderr={r.stderr}")
        self.assertNotIn("unbound variable", r.stderr)
        self.assertIn("== restore ==", r.stdout)
        self.assertIn("start u.service", calls)

    def test_restore_reports_failure_when_the_unit_cannot_be_restored(self):
        """복원 실패를 조용히 삼키지 않는다(rc=70)."""
        import tempfile
        d = Path(tempfile.mkdtemp())
        stub = d / "systemctl"
        stub.write_text('#!/bin/sh\ncase "$1" in is-active) exit 3 ;; esac\nexit 0\n')
        stub.chmod(0o755)
        (d / "wpa_cli").write_text("#!/bin/sh\nexit 0\n")
        (d / "wpa_cli").chmod(0o755)
        script = d / "harness.sh"
        script.write_text(
            f"source {shlex.quote(str(DRIVER))}\n"
            "IFACE=mlan0\n"
            'RESTORE_UNIT="u.service"\n'
            'RESTORE_ORIG_DIFF="7"\n'
            "RESTORE_WAS_ACTIVE=1\n"
            "restore\n"
        )
        r = subprocess.run(
            ["bash", str(script)], capture_output=True, text=True, timeout=30,
            env={**os.environ, "PATH": f"{d}:{os.environ['PATH']}"},
        )
        self.assertEqual(r.returncode, 70, f"stdout={r.stdout} stderr={r.stderr}")
        self.assertIn("restore failed for u.service", r.stderr)


class ModuleWiring(unittest.TestCase):
    """직접 실행(`python3 <file>`)이 pytest 와 같은 수의 테스트를 돌려야 한다.

    `unittest.main()` 뒤에 클래스를 덧붙이면 그 클래스는 직접 실행에서 **조용히
    누락**된다. 실제로 RemoteSafety(사고 회귀 5건)가 그렇게 빠져 있었고 pytest 로만
    돌아 22 passed 로 보였다. 실행 경로가 달라 커버리지가 갈리는 것을 여기서 막는다.
    """

    @unittest.skipIf(os.environ.get("_ROAM_QA_SELFTEST") == "1",
                     "재귀 방지 — 하위 실행에서는 건너뛴다(집계에는 포함)")
    def test_direct_execution_runs_every_test_in_this_module(self):
        import re as _re

        expected = sum(
            1
            for obj in list(globals().values())
            if isinstance(obj, type) and issubclass(obj, unittest.TestCase)
            for name in dir(obj)
            if name.startswith("test_")
        )
        r = subprocess.run(
            ["python3", str(Path(__file__).resolve())],
            capture_output=True, text=True, timeout=120,
            env={**os.environ, "_ROAM_QA_SELFTEST": "1"},
        )
        m = _re.search(r"^Ran (\d+) tests?", r.stderr, _re.M)
        self.assertIsNotNone(m, f"직접 실행 요약을 못 읽었다:\n{r.stderr[-500:]}")
        self.assertEqual(
            int(m.group(1)), expected,
            "직접 실행이 일부 테스트를 건너뛴다 — unittest.main() 뒤에 클래스가 있는지 확인하라",
        )


if __name__ == "__main__":
    unittest.main()
