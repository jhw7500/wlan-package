"""postinst 의 root 비밀번호 설정 블록 회귀 고정.

출하 이미지의 root 는 비밀번호가 비어 있고(`passwd -S` 가 NP) sshd 가
PermitEmptyPasswords yes 라 그 상태로 인증이 통과한다. postinst 가 값을 채워
그 경로를 닫되, 이미 설정(P)·잠김(L) 상태는 건드리지 않아야 한다.

블록을 원문 그대로 떼어 스텁 passwd/chpasswd 로 실행해 네 갈래를 모두 확인한다.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

WLAN_ROOT = Path(__file__).resolve().parents[4]
POSTINST = WLAN_ROOT / "DEBIAN/postinst"


def _postinst() -> str:
    return POSTINST.read_text(encoding="utf-8")


def test_password_is_stored_as_a_hash_not_plaintext():
    """postinst 는 기기에서 world-readable 이라 평문을 두면 비-root 도 읽는다."""
    line = next(
        l for l in _postinst().splitlines()
        if "chpasswd -e" in l and "'root:" in l
    )
    secret = re.search(r"'root:([^']+)'", line).group(1)
    assert secret.startswith("$6$"), f"expected a SHA-512 crypt hash, got {secret[:4]!r}"
    assert len(secret) >= 80, "hash looks truncated"
    # `chpasswd -e` 없이 평문을 먹이는 형태로 되돌아가지 않았는지 함께 고정한다.
    assert "chpasswd -e" in line


def _guard_block() -> str:
    lines = _postinst().splitlines()
    start = next(
        i for i, l in enumerate(lines) if l.startswith("  root_pw_state=")
    )
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "  fi")
    return "\n".join(lines[start:end + 1])


def _run(tmp_path: Path, *, passwd_out: str, chpasswd_rc: int = 0):
    """블록을 실행하고 (chpasswd 에 전달된 stdin, logger 호출 기록)을 돌려준다."""
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    chpasswd_in = tmp_path / "chpasswd_stdin"
    logged = tmp_path / "logger_calls"

    (fakebin / "passwd").write_text(
        f"#!/bin/sh\nprintf '%s' {passwd_out!r}\nexit 0\n", encoding="utf-8"
    )
    (fakebin / "chpasswd").write_text(
        f"#!/bin/sh\ncat > {chpasswd_in}\nexit {chpasswd_rc}\n", encoding="utf-8"
    )
    (fakebin / "logger").write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$*" >> {logged}\nexit 0\n', encoding="utf-8"
    )
    for n in ("passwd", "chpasswd", "logger"):
        (fakebin / n).chmod(0o755)

    script = tmp_path / "block.sh"
    script.write_text(
        f'#!/bin/bash\ntag=test\nKEY=PKG\nPATH="{fakebin}:$PATH"\n{_guard_block()}\n',
        encoding="utf-8",
    )
    subprocess.run(["bash", str(script)], check=True, capture_output=True)
    return (
        chpasswd_in.read_text(encoding="utf-8") if chpasswd_in.exists() else "",
        logged.read_text(encoding="utf-8") if logged.exists() else "",
    )


def test_sets_password_when_root_has_none(tmp_path):
    fed, logged = _run(tmp_path, passwd_out="root NP 2011-04-05 0 99999 7 -1\n")
    assert fed.startswith("root:$6$"), f"chpasswd was fed {fed[:12]!r}"
    assert "-p local0.info" in logged and "set root password" in logged


def test_leaves_an_already_set_password_alone(tmp_path):
    """현장에서 바꾼 비밀번호를 패키지가 되돌리면 안 된다."""
    fed, logged = _run(tmp_path, passwd_out="root P 2026-08-26 0 99999 7 -1\n")
    assert fed == "", "chpasswd must not run when a password is already set"
    assert "already set" in logged


def test_leaves_a_locked_account_alone(tmp_path):
    """의도적으로 잠근 계정을 패키지가 열면 안 된다."""
    fed, logged = _run(tmp_path, passwd_out="root L 2026-08-26 0 99999 7 -1\n")
    assert fed == "", "chpasswd must not run for a locked account"
    assert "already set" in logged


def test_backs_off_when_state_is_unreadable(tmp_path):
    fed, logged = _run(tmp_path, passwd_out="")
    assert fed == "", "chpasswd must not run when the state could not be read"
    assert "-p local0.warn" in logged


def test_escalates_when_chpasswd_leaves_the_password_empty(tmp_path):
    """실패하면 root 가 빈 비밀번호로 남는다 — 그 상태 자체가 취약하므로
    admin 의 "잠긴 채 남음"(fail-closed)보다 높은 심각도로 알려야 한다."""
    fed, logged = _run(
        tmp_path, passwd_out="root NP 2011-04-05 0 99999 7 -1\n", chpasswd_rc=1
    )
    assert fed.startswith("root:$6$")
    assert "-p local0.crit" in logged, logged
    assert "-p local0.err" not in logged, (
        "an empty root password must not be reported below crit"
    )
