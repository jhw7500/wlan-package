"""`wifi_arping.sh` 의 유선 링크 판정 계약.

`wifi_arping@eth0` 는 ARP 를 쏘기 전에 유선 링크가 올라오길 기다린다. 판정 근거가
`link.json`(`wifi_logger_link@eth0` 이 만드는 파생물) 하나뿐이면 **파일이 없는 것과 링크가
정말 내려간 것을 구분하지 못한다**. 실측(cts-wlan 2026-08-31 04:09:37): arping 과 링크 로거가
같은 초에 떠서 arping 이 먼저 읽었고, 파일 부재가 `|| echo "down"` 으로 "링크 다운" 이 됐다.
케이블은 그때도 꽂혀 있었다(`/sys/class/net/eth0/carrier`=1). 로거가 꺼진 구성에서는 파일이
영영 없으므로 같은 조건이 무한 대기가 된다.

그래서 판정은 `link.json` 이 유효한 값을 줄 때 그것을 쓰고, 없거나 못 읽으면 커널 carrier 로
내려간다. 둘 다 없으면 `down` 이 아니라 `unknown` 이다 — 모르는 것을 아는 것처럼 기록하지 않는다.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
ARPING_SH = SCRIPTS_DIR / "wifi_arping.sh"


class EthLinkState(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="wifi-arping-"))
        cls.helpers = cls.tmp / "helpers.sh"
        lines = ARPING_SH.read_text(encoding="utf-8").splitlines(keepends=True)
        start = next((i for i, l in enumerate(lines) if l.startswith("eth_link_state() {")), None)
        if start is None:
            cls.helpers.write_text("", encoding="utf-8")
            return
        depth_end = next(i for i in range(start, len(lines)) if lines[i].rstrip() == "}")
        cls.helpers.write_text("".join(lines[start:depth_end + 1]), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="wifi-arping-case-"))
        self.sysfs = self.root / "sys"
        (self.sysfs / "eth0").mkdir(parents=True)
        self.json = self.root / "link.json"

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def state(self):
        env = dict(os.environ)
        env["NET_SYSFS_ROOT"] = str(self.sysfs)
        r = subprocess.run(
            ["bash", "-c",
             f'. "{self.helpers}"\neth_link_state eth0 "{self.json}"'],
            capture_output=True, text=True, timeout=30, env=env,
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout.strip()

    def write_json(self, link):
        self.json.write_text('{"eth_stats": {"phy": {"link": "%s"}}}' % link, encoding="utf-8")

    def write_carrier(self, value):
        (self.sysfs / "eth0" / "carrier").write_text(f"{value}\n", encoding="utf-8")

    def test_link_json_up_wins(self):
        self.write_json("up")
        self.write_carrier(0)          # link.json 이 유효하면 그것이 판정 근거다
        self.assertEqual(self.state(), "up")

    def test_link_json_down_wins(self):
        self.write_json("down")
        self.write_carrier(1)
        self.assertEqual(self.state(), "down")

    def test_missing_link_json_falls_back_to_carrier_up(self):
        # 실측 회귀: 파일 부재가 "링크 다운" 이 되어 케이블이 꽂혀 있는데도 기다렸다.
        self.write_carrier(1)
        self.assertEqual(self.state(), "up")

    def test_missing_link_json_falls_back_to_carrier_down(self):
        self.write_carrier(0)
        self.assertEqual(self.state(), "down")

    def test_malformed_link_json_falls_back_to_carrier(self):
        self.json.write_text("{ this is not json", encoding="utf-8")
        self.write_carrier(1)
        self.assertEqual(self.state(), "up")

    def test_link_json_without_the_key_falls_back_to_carrier(self):
        self.json.write_text('{"eth_stats": {}}', encoding="utf-8")
        self.write_carrier(1)
        self.assertEqual(self.state(), "up")

    def test_no_source_at_all_is_unknown_not_down(self):
        # carrier 는 admin-down 인터페이스에서 EINVAL 이라 읽기 자체가 실패한다.
        self.assertEqual(self.state(), "unknown")


if __name__ == "__main__":
    unittest.main()
