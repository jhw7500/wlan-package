"""systemd 유닛이 ExecStart 로 실행하는 리포 내 스크립트는 실행 가능해야 한다.

이 저장소는 `core.fileMode=false` 라 워킹트리에서 `chmod +x` 를 해도 git 인덱스에
반영되지 않는다. 빌드는 신선한 체크아웃에서 이뤄지므로, 인덱스 모드가 100644 이면
패키지에 non-executable 로 실려 systemd 가 `203/EXEC` 로 유닛을 못 띄운다.
에러가 유닛 로그에만 남고 기능은 조용히 사라지므로 온타겟에서 시간을 쓰기 쉽다.

실제로 이 클래스의 결함이 두 번 나왔다 — PR #174 의 `wlan_wpa_reattach.sh`(udev RUN),
그리고 `wifi_peer_net_reapply.sh`(wlan-peer-net.service ExecStart). 전자는 개별
테스트로 막았지만 후자는 그 테스트가 커버하지 않아 다시 통과했다. 여기서는 개별
파일이 아니라 **유닛의 ExecStart 대상 전체**를 불변식으로 고정한다.

검증: 유닛 42개 중 리포 내부를 가리키는 ExecStart 28개, 위반 0건 (2026-08-19).
파일시스템 모드가 아니라 `git ls-files -s` 의 인덱스 모드를 봐야 한다 —
워킹트리는 항상 실행 가능해 보이므로 `os.access(X_OK)` 는 이 결함을 통과시킨다.
"""

import re
import subprocess
from pathlib import Path

WLAN_ROOT = Path(__file__).resolve().parents[4]  # dist/wlan
REPO_ROOT = WLAN_ROOT.parents[1]

_EXECSTART = re.compile(r"^\s*ExecStart[^=]*=\s*[-+!@]*(\S+)")
_UNIT_SUFFIXES = (".service", ".timer", ".target", ".socket", ".path", ".mount")


def _index_modes():
    """git 인덱스 모드 맵 {repo상대경로: mode}."""
    out = subprocess.check_output(
        ["git", "ls-files", "-s", "dist/"], cwd=REPO_ROOT, text=True
    )
    modes = {}
    for line in out.splitlines():
        mode, _sha, _stage, path = line.split(None, 3)
        modes[path] = mode
    return modes


def _execstart_targets(modes):
    """(유닛경로, ExecStart 절대경로, 리포상대경로) — 리포 안을 가리키는 것만."""
    targets = []
    for path, _mode in modes.items():
        if not path.endswith(_UNIT_SUFFIXES):
            continue
        text = (REPO_ROOT / path).read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            m = _EXECSTART.match(line)
            if not m:
                continue
            exe = m.group(1)
            if not exe.startswith("/"):
                continue  # 상대경로/변수 — 판정 불가
            repo_path = "dist/wlan" + exe
            if repo_path in modes:  # 리포 밖(배포판 제공 바이너리)은 대상 아님
                targets.append((path, exe, repo_path))
    return targets


def test_unit_execstart_targets_are_executable_in_git_index():
    modes = _index_modes()
    targets = _execstart_targets(modes)

    assert targets, "ExecStart 대상을 하나도 못 찾았다 — 파서가 깨졌을 가능성"

    violations = [
        f"{unit} → {exe} (git index mode={modes[repo_path]}, 기대 100755)"
        for unit, exe, repo_path in targets
        if modes[repo_path] != "100755"
    ]
    assert violations == [], (
        "유닛이 실행하는 스크립트가 git 인덱스에서 non-executable 이다. "
        "신선한 체크아웃 빌드에서 systemd 203/EXEC 로 실패한다. "
        "`git update-index --chmod=+x <path>` 로 고칠 것:\n  "
        + "\n  ".join(violations)
    )


def test_new_peer_net_reapply_is_wired_and_executable():
    """이번 회귀의 구체적 고정 — 유닛·스크립트 존재와 연결까지 함께 본다."""
    modes = _index_modes()
    script = "dist/wlan/usr/local/scripts/wifi_peer_net_reapply.sh"
    unit = "dist/wlan/etc/systemd/system/wlan-peer-net.service"

    assert script in modes, f"{script} 가 git 에 없다"
    assert unit in modes, f"{unit} 이 git 에 없다"
    assert modes[script] == "100755", (
        f"{script} 인덱스 모드가 {modes[script]} — wlan-peer-net.service 의 "
        "ExecStart 와 wifi_init.sh 의 직접 호출이 모두 실패한다"
    )

    unit_text = (REPO_ROOT / unit).read_text(encoding="utf-8")
    assert "/usr/local/scripts/wifi_peer_net_reapply.sh" in unit_text, (
        "유닛의 ExecStart 가 reapply 스크립트를 가리키지 않는다"
    )
