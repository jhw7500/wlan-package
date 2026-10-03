"""소스 아카이브 허용 목록이 출하 패키지와 회귀 테스트를 빠뜨리지 않는다.

`release/wlan-package.tar` 는 `scripts/source_archive_manifest.txt` 에 적힌 파일만 담는다.
목록에서 빠진 파일은 실패하지 않고 조용히 사라진다 — ftpcmd 핸들러 9종과 sshd 드롭인이
패키지에는 들어가는데 아카이브에는 없어서, 아카이브만으로는 같은 패키지를 다시 만들 수
없었다(payload-manifest 가 가리키는 파일이 없음). 회귀 테스트도 같은 경로로 빠져 있었다.

그래서 두 가지를 고정한다.

* payload-manifest 의 모든 항목은 아카이브 목록에 있어야 한다. 파일 시스템만 보므로
  압축을 푼 아카이브 안에서도 돈다. bridge 소스(`wlan-bridge/wbridge`)가 없으면 build.sh
  처럼 bridge payload 를 기대 목록에서 뺀다 — bridge-less 아카이브의 목록은 그렇게 걸러진다.
* git 이 추적하는 `dist/wlan/**/tests/` 파일은 아카이브 목록에 있어야 한다. `.git` 이
  없는 아카이브 안에서는 이 검사를 건너뛴다(아카이브는 정의상 목록과 같다).
"""
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[6]
SOURCE_MANIFEST = REPO / "scripts/source_archive_manifest.txt"
PAYLOAD_MANIFEST = REPO / "dist/wlan/DEBIAN/payload-manifest.txt"


def manifest_entries(path: Path) -> set:
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }


class SourceArchiveManifestCoverage(unittest.TestCase):
    def test_every_payload_file_is_archived(self):
        archived = manifest_entries(SOURCE_MANIFEST)
        expected = manifest_entries(PAYLOAD_MANIFEST)
        if not (REPO / "wlan-bridge/wbridge").is_dir():
            # build.sh 와 같은 규칙: bridge 소스가 없으면 bridge payload 는 패키지에서도,
            # 아카이브 목록에서도 빠진다(bridge-less 아카이브의 목록은 그렇게 걸러져 있다).
            expected = {e for e in expected if not e.startswith("usr/local/wlan-bridge/")}
        missing = sorted(entry for entry in expected if f"dist/wlan/{entry}" not in archived)
        self.assertEqual(missing, [], "payload files missing from source_archive_manifest.txt")

    def test_every_tracked_dist_test_is_archived(self):
        if not (REPO / ".git").exists():
            self.skipTest("not a git checkout (extracted source archive)")
        tracked = subprocess.run(
            ["git", "-C", str(REPO), "ls-files", "dist/wlan"],
            capture_output=True, text=True, check=True,
        ).stdout.splitlines()
        tests = [path for path in tracked if "/tests/" in path]
        self.assertTrue(tests, "no tracked dist tests found; the matcher is broken")
        archived = manifest_entries(SOURCE_MANIFEST)
        missing = sorted(path for path in tests if path not in archived)
        self.assertEqual(missing, [], "tracked dist tests missing from source_archive_manifest.txt")


if __name__ == "__main__":
    unittest.main()
