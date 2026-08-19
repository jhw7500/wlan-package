#!/usr/bin/env python3
"""systemd 유닛·udev 규칙이 **직접 실행**하는 리포 내 파일 목록 — 실행비트 규칙의 단일 소스.

이 저장소는 `core.fileMode=false` 라 워킹트리에서 `chmod +x` 를 해도 git 인덱스에
반영되지 않는다. 빌드는 신선한 체크아웃에서 이뤄지므로 인덱스 모드가 100644 이면
패키지에 non-executable 로 실려, systemd 는 `203/EXEC` 로 유닛을 못 띄우고 udev 는
RUN 을 조용히 건너뛴다. 어느 쪽도 에러가 눈에 띄지 않아 온타겟에서 시간을 쓰게 된다.

실제로 이 클래스가 세 번 나왔다 — PR #174(`wlan_wpa_reattach.sh`, udev RUN),
PR #184(`wifi_peer_net_reapply.sh`, ExecStart), PR #182(`wlan_fw_watch.sh`, ExecStart).

pre-commit 훅과 회귀 테스트가 **같은 규칙**을 봐야 하므로 판정 로직을 여기 한 곳에 둔다.
(정규식을 bash·python 양쪽에 복제하면 드리프트가 생긴다.)

단독 실행하면 위반 목록을 stdout 으로 출력하고, 위반이 있으면 exit 1.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

# dist/wlan 아래가 타겟의 / 에 대응한다
PAYLOAD_PREFIX = "dist/wlan"

_UNIT_SUFFIXES = (".service", ".timer", ".target", ".socket", ".path", ".mount")

# systemd: ExecStart/ExecStop/... = [-+!@]prefix 뒤의 첫 토큰이 실행 파일
_SYSTEMD_EXEC = re.compile(
    r"^\s*Exec(?:Start|StartPre|StartPost|Stop|StopPost|Reload)\s*=\s*[-+!@]*(\S+)"
)
# udev: RUN{program}+="..." / PROGRAM="..." / IMPORT{program}="..."
_UDEV_EXEC = re.compile(
    r'(?:RUN\{program\}|RUN|PROGRAM|IMPORT\{program\})\s*\+?=\s*"([^" ]+)'
)


def repo_root() -> Path:
    out = subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True)
    return Path(out.strip())


def index_modes(root: Path) -> dict[str, str]:
    """{repo상대경로: git 인덱스 모드}. 워킹트리 모드가 아니라 인덱스를 본다."""
    out = subprocess.check_output(
        ["git", "ls-files", "-s", PAYLOAD_PREFIX], cwd=root, text=True
    )
    modes: dict[str, str] = {}
    for line in out.splitlines():
        mode, _sha, _stage, path = line.split(None, 3)
        modes[path] = mode
    return modes


def exec_targets(root: Path, modes: dict[str, str]) -> list[tuple[str, str, str, str]]:
    """(선언 파일, 종류, 실행 절대경로, 리포상대경로) — 리포 안을 가리키는 것만.

    배포판이 제공하는 바이너리(/bin/systemctl 등)는 리포에 없으므로 자연히 제외된다.
    """
    found: list[tuple[str, str, str, str]] = []
    for path in modes:
        if path.endswith(_UNIT_SUFFIXES):
            kind, pattern = "systemd", _SYSTEMD_EXEC
        elif path.endswith(".rules"):
            kind, pattern = "udev", _UDEV_EXEC
        else:
            continue
        text = (root / path).read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            # 주석 처리된 규칙은 실행되지 않는다. systemd 는 '#'/';', udev 는 '#'.
            if line.lstrip()[:1] in ("#", ";"):
                continue
            if kind == "systemd":
                m = pattern.match(line)
                hits = [m.group(1)] if m else []
            else:
                hits = pattern.findall(line)
            for exe in hits:
                if not exe.startswith("/"):
                    continue  # 상대경로·변수 — 정적 판정 불가
                repo_path = PAYLOAD_PREFIX + exe
                if repo_path in modes:
                    found.append((path, kind, exe, repo_path))
    return found


def dangling_targets(root: Path, modes: dict[str, str]) -> list[tuple[str, str, str]]:
    """선언은 있는데 **대상 파일이 리포에 없는** 실행 경로 — 끊긴 배선.

    실행비트 검사만으로는 "스크립트가 지워졌다"나 "ExecStart 경로 오타"를 못 잡는다
    (대상이 없으면 애초에 검사 목록에 안 들어오기 때문). 패키지가 소유하는 디렉터리를
    가리키는데 그 파일만 없는 경우를 배선 끊김으로 본다 — 배포판 제공 바이너리
    (/bin/systemctl 등)는 그 디렉터리 자체가 리포에 없으므로 자연히 제외된다.
    """
    owned_dirs = {os.path.dirname(p) for p in modes}
    out: list[tuple[str, str, str]] = []
    ignored_cache: dict[str, bool] = {}

    def is_generated(repo_path: str) -> bool:
        """`.gitignore` 로 명시 제외된 경로 = 빌드 산출물 (예: vhld.c → vhld).

        git 에 없는 것이 정상이므로 배선 끊김이 아니다. 저장소가 스스로 '생성물'
        이라고 선언한 것만 예외로 삼는다 — 디스크 존재 여부로 판정하면 신선한
        체크아웃(빌드 전)에서 오탐이 난다.
        """
        if repo_path not in ignored_cache:
            rc = subprocess.run(
                ["git", "check-ignore", "-q", "--", repo_path], cwd=root
            ).returncode
            ignored_cache[repo_path] = rc == 0
        return ignored_cache[repo_path]

    for path in modes:
        if path.endswith(_UNIT_SUFFIXES):
            kind, pattern = "systemd", _SYSTEMD_EXEC
        elif path.endswith(".rules"):
            kind, pattern = "udev", _UDEV_EXEC
        else:
            continue
        text = (root / path).read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            if line.lstrip()[:1] in ("#", ";"):
                continue
            if kind == "systemd":
                m = pattern.match(line)
                hits = [m.group(1)] if m else []
            else:
                hits = pattern.findall(line)
            for exe in hits:
                if not exe.startswith("/"):
                    continue
                repo_path = PAYLOAD_PREFIX + exe
                if repo_path in modes:
                    continue  # 정상
                if os.path.dirname(repo_path) in owned_dirs and not is_generated(repo_path):
                    out.append((path, kind, exe))
    return out


def violations(root: Path | None = None) -> list[tuple[str, str, str, str]]:
    """실행 대상인데 인덱스 모드가 100755 가 아닌 것들."""
    root = root or repo_root()
    modes = index_modes(root)
    return [t for t in exec_targets(root, modes) if modes[t[3]] != "100755"]


def main() -> int:
    root = repo_root()
    bad = violations(root)
    for decl, kind, exe, repo_path in bad:
        print(f"{repo_path}\t{kind}\t{decl} → {exe}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
