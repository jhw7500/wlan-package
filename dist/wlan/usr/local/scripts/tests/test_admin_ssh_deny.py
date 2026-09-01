"""admin 계정 SSH 차단(sshd 드롭인)의 회귀 고정.

검증 오라클은 실제 sshd 파서다 — 드롭인이 "글자로 맞게 적혔는가"가 아니라
"sshd 가 읽어서 admin 을 거부하는 설정으로 해석하는가"를 본다. postinst 의
락아웃 가드도 문자열이 아니라 블록을 그대로 떼어 실행해서 확인한다.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

WLAN_ROOT = Path(__file__).resolve().parents[4]
DROPIN_REL = "etc/ssh/sshd_config.d/10-wlan-admin.conf"
DROPIN = WLAN_ROOT / DROPIN_REL
PAYLOAD_MANIFEST = WLAN_ROOT / "DEBIAN/payload-manifest.txt"
POSTINST = WLAN_ROOT / "DEBIAN/postinst"

SSHD = shutil.which("sshd") or "/usr/sbin/sshd"
HAVE_SSHD = Path(SSHD).exists()


def _manifest_entries() -> set[str]:
    return {
        line.strip()
        for line in PAYLOAD_MANIFEST.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def test_dropin_is_shipped():
    assert DROPIN.is_file(), f"{DROPIN_REL} missing from the payload tree"


def test_dropin_is_in_payload_manifest():
    # 매니페스트에 없으면 validate_release.sh 가 트리/매니페스트 불일치로 떨어진다.
    assert DROPIN_REL in _manifest_entries()


@pytest.mark.skipif(not HAVE_SSHD, reason="sshd not available as an oracle")
def test_sshd_resolves_dropin_to_denyusers_admin(tmp_path):
    """실제 sshd 가 드롭인을 읽어 admin 만 거부 목록에 올리는지 본다."""
    hostkey = tmp_path / "hostkey"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-f", str(hostkey), "-N", ""],
        check=True,
    )
    dropin = tmp_path / "10-wlan-admin.conf"
    dropin.write_text(DROPIN.read_text(encoding="utf-8"), encoding="utf-8")
    conf = tmp_path / "sshd_config"
    conf.write_text(f"HostKey {hostkey}\nInclude {dropin}\n", encoding="utf-8")

    proc = subprocess.run(
        [SSHD, "-T", "-f", str(conf)], capture_output=True, text=True
    )
    assert proc.returncode == 0, f"sshd rejected the config: {proc.stderr}"
    resolved = [
        line.strip()
        for line in proc.stdout.splitlines()
        if line.lower().startswith("denyusers")
    ]
    assert resolved == ["denyusers admin"], f"sshd resolved: {resolved!r}"


def _guard_block() -> str:
    """postinst 에서 sshd 락아웃 가드 블록을 원문 그대로 떼어낸다."""
    lines = POSTINST.read_text(encoding="utf-8").splitlines()
    start = next(
        i for i, line in enumerate(lines)
        if line.startswith("  admin_ssh_dropin=")
    )
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "  fi")
    return "\n".join(lines[start:end + 1])


def _run_guard(tmp_path: Path, *, sshd_rcs: list[int]) -> tuple[Path, str]:
    """가드 블록을 실행하고 (드롭인 최종 상태, logger 호출 기록)을 돌려준다.

    ``sshd_rcs`` 는 ``sshd -t`` 호출 순서별 종료코드다. 가드는 첫 검사가 실패하면
    드롭인을 치우고 **한 번 더** 부르며, 그 두 번째 결과가 심각도를 가른다.
    """
    # 실제 설치 경로를 그대로 쓰지 않는다 — 그 경로는 치환 결과의 접미사가 되어
    # "원본 경로가 남아 있지 않은가" 단언을 무의미하게 만든다.
    target = tmp_path / "dropin.conf"
    target.write_text("DenyUsers admin\n", encoding="utf-8")

    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    rcfile = tmp_path / "sshd_rcs"
    rcfile.write_text("".join(f"{rc}\n" for rc in sshd_rcs), encoding="utf-8")
    counter = tmp_path / "sshd_calls"
    logfile = tmp_path / "logger_calls"

    sshd = fakebin / "sshd"
    sshd.write_text(
        "#!/bin/sh\n"
        f"n=$(cat {counter} 2>/dev/null || echo 0); n=$((n+1)); echo $n > {counter}\n"
        f'rc=$(sed -n "${{n}}p" {rcfile})\n'
        "exit ${rc:-0}\n",
        encoding="utf-8",
    )
    sshd.chmod(0o755)

    logger = fakebin / "logger"
    logger.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$*" >> {logfile}\nexit 0\n', encoding="utf-8"
    )
    logger.chmod(0o755)

    block = _guard_block()
    redirected = block.replace(f"/{DROPIN_REL}", str(target))
    # 경로 치환이 실제로 적용됐는지 확인한다 — 치환이 빗나가면 테스트가
    # 시스템 경로를 건드리거나 아무것도 검증하지 못한 채 통과한다.
    assert str(target) in redirected, "path redirect did not apply"
    assert f"/{DROPIN_REL}" not in redirected, "original path still present"

    script = tmp_path / "guard.sh"
    script.write_text(
        f'#!/bin/bash\ntag=test\nKEY=PKG\nPATH="{fakebin}:$PATH"\n{redirected}\n',
        encoding="utf-8",
    )
    subprocess.run(["bash", str(script)], check=True, capture_output=True)
    logged = logfile.read_text(encoding="utf-8") if logfile.exists() else ""
    return target, logged


def test_guard_keeps_dropin_when_sshd_accepts_config(tmp_path):
    target, logged = _run_guard(tmp_path, sshd_rcs=[0])
    assert target.exists(), "guard removed the drop-in even though sshd -t passed"
    assert "-p local0.info" in logged, logged


def test_guard_removes_dropin_when_sshd_rejects_config(tmp_path):
    """드롭인이 sshd 설정을 깨면 그 다음 연결부터 모든 SSH 가 거부된다 —
    원격 복구 경로까지 막히므로 가드는 반드시 물러서야 한다."""
    target, logged = _run_guard(tmp_path, sshd_rcs=[1, 0])
    assert not target.exists(), (
        "guard left a drop-in that sshd rejects — this locks SSH out"
    )
    assert "-p local0.err" in logged, logged


def test_guard_escalates_when_config_is_broken_without_the_dropin(tmp_path):
    """드롭인을 치워도 sshd 설정이 깨져 있으면 SSH 가 전면 불통일 수 있다.

    이쪽이 "드롭인이 원인이었고 제거로 복구된" 경우보다 심각하므로, 심각도가
    그보다 낮으면 안 된다 — err 이상을 감시하는 운영 모니터링에서 진짜 장애가
    묻히고 정상 백아웃만 경보로 뜨는 역전이 생긴다.
    """
    target, logged = _run_guard(tmp_path, sshd_rcs=[1, 1])
    assert not target.exists()
    assert "-p local0.crit" in logged, logged
    assert "-p local0.warn" not in logged, (
        "a broken sshd config must not be reported below the successful back-out"
    )


def test_admin_password_is_stored_as_a_hash_not_plaintext():
    """postinst 는 기기에서 world-readable 이라 평문을 두면 비-root 도 읽는다.

    root 쪽과 같은 계약이다 — `chpasswd -e` 로 SHA-512 해시만 넣는다.
    """
    import re

    body = POSTINST.read_text(encoding="utf-8")
    line = next(
        l for l in body.splitlines()
        if "chpasswd -e" in l and "'admin:" in l
    )
    secret = re.search(r"'admin:([^']+)'", line).group(1)
    assert secret.startswith("$6$"), f"expected a SHA-512 crypt hash, got {secret[:4]!r}"
    assert len(secret) >= 80, "hash looks truncated"


def test_login_shell_is_not_switched_to_nologin():
    """셸 교체 대신 DenyUsers 를 택한 설계 결정을 고정한다.

    nologin 으로 바꾸면 vsftpd 의 pam_shells 가 admin 을 거부해 /etc/shells
    (base-files 소유)까지 함께 고쳐야 하고, postinst 의 계정 생성 가드 때문에
    이미 admin 이 있는 기기에는 적용되지도 않는다.
    """
    body = POSTINST.read_text(encoding="utf-8")
    assert "useradd -m -d /home/admin -s /bin/sh admin" in body
    assert "/etc/shells" not in body, "postinst must not touch /etc/shells"
    assert "nologin" not in body
