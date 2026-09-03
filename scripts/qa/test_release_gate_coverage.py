"""릴리스 게이트의 커버리지가 조용히 새지 않도록 고정한다.

게이트는 두 층이다:

* ``validate_release.sh source`` — 산출물 비의존. **CI 가 돌린다.**
* ``validate_release.sh pre``    — 위에 산출물 의존 검사를 더한 것. 릴리스 빌드 전용.

이 분리에는 실패 모드가 둘 있고, 둘 다 **이미 일어난 적이 있다**:

1. 새 검사를 ``run_prebuild`` 에 직접 붙이면 CI 가 못 본다. #279 의 블로커 3건이
   그렇게 머지됐다(게이트 자체가 CI 에서 안 돌던 시절).
2. 새 테스트 스크립트를 만들고 목록에 안 넣으면 아무도 안 돌린다. 실제로
   ``wifi_radio_test.sh`` 는 mock 기반이라 어디서든 도는데 목록에 없었고,
   ``validate_release_test.sh`` 는 게이트 자신의 테스트인데 호출처가 없었다.

exit code 만 보는 검사로는 둘 다 안 잡힌다 — 빠진 검사는 실패하지 않고 그냥 없다.
그래서 게이트 스크립트의 **모양**을 단언한다(이 저장소가 이미 쓰는 관용구:
``wifi_init_config_test.sh`` 의 "has one update_mac write point").
"""
import re
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GATE = REPO / "scripts" / "validate_release.sh"

# run_prebuild 가 불러도 되는 것. 산출물이 필요한 검사와 소스 검사 위임뿐이다.
PREBUILD_ALLOWED = {
    'cd "$REPO"',
    "validate_source_product_defaults",
    "validate_payload_manifest",
    "bash scripts/validate_release_test.sh",
    "run_source",
}

# 게이트에서 제외되는 테스트 스크립트 — 전부 실기 자원을 요구한다.
# 새로 넣으려면 여기에 이유와 함께 적어야 한다(빈칸으로 두면 이 테스트가 막는다).
ON_TARGET_ONLY = {
    "dist/wlan/usr/local/scripts/emmc_test.sh":
        "/dev/mmcblk2 에 직접 접근한다 (EMMC_DEV 기본값)",
    "dist/wlan/usr/local/scripts/wbridge_smoke_test.sh":
        "systemctl daemon-reload 와 /usr/local/etc/wifi_init_conf.json 을 건드린다",
    "dist/wlan/usr/local/vhl_daemon/tests/test_vhld.sh":
        "동작 중인 vhld 데몬(127.0.0.1:50000)에 접속한다",
}


def _function_body(name):
    text = GATE.read_text(encoding="utf-8")
    m = re.search(rf"^{name}\(\) \{{\n(.*?)^\}}$", text, re.M | re.S)
    assert m, f"{name}() 를 {GATE} 에서 찾지 못했다"
    return m.group(1)


def _source_test_list():
    """run_source 의 셸 스위트 목록(경로들)."""
    body = _function_body("run_source")
    m = re.search(r"for test in \\\n(.*?); do$", body, re.M | re.S)
    assert m, "run_source 의 셸 스위트 목록을 찾지 못했다"
    return {
        line.strip().rstrip("\\").strip()
        for line in m.group(1).splitlines()
        if line.strip().rstrip("\\").strip()
    }


class PrebuildStaysThin(unittest.TestCase):
    """`pre` 에 검사를 직접 붙이면 CI 가 놓친다 — 붙이지 못하게 한다."""

    def test_run_prebuild_only_delegates(self):
        stray = []
        for raw in _function_body("run_prebuild").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line not in PREBUILD_ALLOWED:
                stray.append(line)
        self.assertEqual(
            stray, [],
            "run_prebuild 에 직접 붙은 검사가 있다. 산출물이 필요 없으면 run_source 로 "
            "옮겨라 — 그래야 CI 가 본다.",
        )

    def test_run_prebuild_delegates_to_run_source(self):
        self.assertIn("run_source", _function_body("run_prebuild"))


class EveryTestScriptIsWiredOrExcluded(unittest.TestCase):
    """만들어만 두고 아무도 안 돌리는 테스트가 생기지 않게 한다."""

    def test_no_test_script_is_orphaned(self):
        tracked = subprocess.run(
            ["git", "-C", str(REPO), "ls-files",
             "dist/wlan/**_test.sh", "dist/wlan/**/test_*.sh"],
            capture_output=True, text=True, check=True,
        ).stdout.split()
        self.assertTrue(tracked, "테스트 스크립트를 하나도 못 찾았다 — 탐색이 깨졌다")

        wired = _source_test_list()
        orphans = [
            path for path in tracked
            if path not in wired and path not in ON_TARGET_ONLY
        ]
        self.assertEqual(
            orphans, [],
            "게이트에서 아무도 안 돌리는 테스트 스크립트다. run_source 목록에 넣거나, "
            "실기 전용이면 ON_TARGET_ONLY 에 이유와 함께 등록하라.",
        )

    def test_exclusions_carry_a_reason(self):
        for path, reason in ON_TARGET_ONLY.items():
            self.assertTrue(reason.strip(), f"{path} 의 제외 사유가 비어 있다")
            self.assertTrue(
                (REPO / path).exists(),
                f"{path} 가 없다 — 제외 목록이 낡았다",
            )


class SourceGateIsReachableFromCI(unittest.TestCase):
    """게이트를 CI 에서 부를 수 있어야 한다 — 서브커맨드가 살아 있는지 본다."""

    def test_source_subcommand_is_dispatched(self):
        text = GATE.read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^\s*source\)\s*run_source\s*;;")

    def test_usage_mentions_source(self):
        self.assertIn("<source|pre|package", GATE.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
