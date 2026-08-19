"""systemd 유닛·udev 규칙이 직접 실행하는 리포 내 스크립트는 실행 가능해야 한다.

이 저장소는 `core.fileMode=false` 라 워킹트리에서 `chmod +x` 를 해도 git 인덱스에
반영되지 않는다. 빌드는 신선한 체크아웃에서 이뤄지므로, 인덱스 모드가 100644 이면
패키지에 non-executable 로 실려 systemd 는 `203/EXEC` 로 유닛을 못 띄우고 udev 는
RUN 을 조용히 건너뛴다. 어느 쪽도 에러가 눈에 띄지 않는다.

이 클래스가 실제로 세 번 나왔다:
  PR #174  wlan_wpa_reattach.sh      (udev RUN)
  PR #184  wifi_peer_net_reapply.sh  (ExecStart)
  PR #182  wlan_fw_watch.sh          (ExecStart) ← 앞의 둘을 막은 뒤에도 통과했다

세 번째가 통과한 이유는 이 테스트의 초판이 **systemd 만** 봤기 때문이 아니라(그건
잡았다), PR #182 가 이 테스트보다 먼저 머지됐기 때문이다. 다만 초판은 udev 를
보지 않아 PR #174 부류를 놓쳤을 것이므로 판정 범위를 넓혔다.

판정 규칙은 `scripts/exec_bit_targets.py` 한 곳에 둔다 — pre-commit 훅이 같은 규칙으로
자동 보정하므로, 정규식을 여기에 복제하면 둘이 드리프트한다.

파일시스템 모드가 아니라 `git ls-files -s` 의 인덱스 모드를 본다. 워킹트리는 항상
실행 가능해 보이므로 `os.access(X_OK)` 로는 이 결함이 통과한다.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

WLAN_ROOT = Path(__file__).resolve().parents[4]  # dist/wlan
REPO_ROOT = WLAN_ROOT.parents[1]
_HELPER = REPO_ROOT / "scripts" / "exec_bit_targets.py"


def _load_helper():
    """scripts/ 는 패키지가 아니라 import 경로에 없다 — 파일에서 직접 로드한다."""
    assert _HELPER.is_file(), f"판정 규칙 단일 소스가 없다: {_HELPER}"
    spec = importlib.util.spec_from_file_location("exec_bit_targets", _HELPER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_unit_and_udev_exec_targets_are_executable_in_git_index():
    helper = _load_helper()
    modes = helper.index_modes(REPO_ROOT)
    targets = helper.exec_targets(REPO_ROOT, modes)

    assert targets, "실행 대상을 하나도 못 찾았다 — 파서가 깨졌을 가능성"

    bad = [
        f"[{kind}] {decl} → {exe} (git index mode={modes[repo_path]}, 기대 100755)"
        for decl, kind, exe, repo_path in targets
        if modes[repo_path] != "100755"
    ]
    assert bad == [], (
        "유닛/udev 가 실행하는 스크립트가 git 인덱스에서 non-executable 이다. "
        "신선한 체크아웃 빌드에서 203/EXEC(systemd) 또는 조용한 skip(udev) 이 된다. "
        "`git update-index --chmod=+x <path>` 로 고치거나, pre-commit 훅을 켜면 "
        "(`git config core.hooksPath scripts/git-hooks`) 자동 보정된다:\n  "
        + "\n  ".join(bad)
    )


def test_rule_covers_both_systemd_and_udev():
    """판정 범위가 한쪽으로 쪼그라드는 회귀를 막는다.

    과거 사고가 systemd·udev 양쪽에서 났으므로, 어느 한쪽 탐지가 0 이 되면
    규칙이 반쪽이 된 것이다.
    """
    helper = _load_helper()
    modes = helper.index_modes(REPO_ROOT)
    kinds = {kind for _decl, kind, _exe, _rp in helper.exec_targets(REPO_ROOT, modes)}
    assert "systemd" in kinds, "systemd ExecStart 대상을 하나도 못 찾았다"
    assert "udev" in kinds, "udev RUN/PROGRAM 대상을 하나도 못 찾았다"


def test_helper_cli_reports_violations_and_exit_code():
    """훅이 의존하는 계약 — stdout 한 줄당 위반 하나, 위반 있으면 exit 1."""
    r = subprocess.run(
        [sys.executable, str(_HELPER)], cwd=REPO_ROOT, capture_output=True, text=True
    )
    lines = [l for l in r.stdout.splitlines() if l.strip()]
    if lines:
        assert r.returncode == 1, "위반이 있는데 exit 0 이면 훅이 조용히 지나친다"
        for l in lines:
            assert len(l.split("\t")) == 3, f"훅이 파싱하는 3-필드 형식이 아니다: {l!r}"
    else:
        assert r.returncode == 0, "위반이 없는데 exit 1 이면 훅이 매번 실패한다"
