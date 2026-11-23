"""postinst 가 업그레이드 때 옛 stat 로거의 AP별 파일만 지운다(#494).

`cleanup_retired_stat_ap_logs` 본문을 postinst 에서 원문 그대로 꺼내 임시 트리에 돌리고,
호출 블록(`# >>> stat-ap-log-cleanup`)은 systemctl 스텁으로 돌려 정리만 하는지 본다. 블록이 로거를
재시작하지 않는 근거(preinst upgrade 의 wifi_logger@ 정지, stat 유닛의 PartOf, 앞선 자식 유닛 복구)도
원문에서 확인한다.
"""
import os
import re
import stat
import subprocess
from pathlib import Path

POSTINST = Path(__file__).resolve().parents[4] / "DEBIAN" / "postinst"
FUNC = "cleanup_retired_stat_ap_logs"

REMOVED = [
    "mlan0/02_00_00_00_00_0a.log",
    "mlan0/02_00_00_00_00_0a.log-20261122",
    "mlan0/02_00_00_00_00_0b.log-20261121.gz",
    "mlan0/00_00_00_00_00_00.log",
    "mlan0/0A_BC_DE_F0_12_34.log-20261120.gz",
    "mlan1/02_00_00_00_00_0c.log",
    "mlan1/02_00_00_00_00_0c.log-20261122.gz",
]
KEPT = [
    "mlan0/stat.log", "mlan0/stat.log-20261122", "mlan0/stat.log-20261121.gz",
    "mlan0/snap.log", "mlan0/snap.log-20261122.gz",
    "mlan1/stat.log", "mlan1/snap.log-20261121.gz",
    # MAC 형식이 아닌 이름
    "mlan0/02_00_00_00_00_0a.log.bak", "mlan0/02_00_00_00_00_0a.txt",
    "mlan0/02_00_00_00_00_0g.log", "mlan0/02_00_00_00_00.log",
    "mlan0/02_00_00_00_00_0a.log-2026112", "mlan0/x02_00_00_00_00_0a.log",
    "mlan0/02_00_00_00_00_0a.log-20261122.gz.1",
    # 다른 디렉터리
    "eth0/02_00_00_00_00_0a.log", "02_00_00_00_00_0a.log",
]


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _function() -> str:
    text = POSTINST.read_text(encoding="utf-8")
    head = f"{FUNC}() {{\n"
    assert head in text, f"postinst 에 {FUNC} 이 없다"
    body = text.split(head, 1)[1]
    end = body.find("\n}\n")
    assert end != -1
    body = body[:end]
    assert "return 0" in body, "함수 본문 추출이 잘렸다"
    return head + body + "\n}\n"


def _block() -> str:
    text = POSTINST.read_text(encoding="utf-8")
    start, end = "# >>> stat-ap-log-cleanup", "# <<< stat-ap-log-cleanup"
    assert start in text and end in text, "postinst stat-ap-log-cleanup 블록이 없다"
    assert text.index(f"{FUNC}() {{") < text.index(start), "호출 블록이 함수 정의보다 앞에 있다"
    return text[text.index(start): text.index(end)]


def _env(tmp_path: Path) -> dict:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    calls.touch()
    _write_executable(bin_dir / "logger", '#!/bin/sh\nprintf "logger %s\\n" "$*" >> "$CALLS"\n')
    _write_executable(bin_dir / "systemctl", '#!/bin/sh\nprintf "systemctl %s\\n" "$*" >> "$CALLS"\nexit 0\n')
    return {**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin", "CALLS": str(calls)}


def _files(root: Path) -> set:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def test_removes_only_mac_named_files_and_their_rotations(tmp_path):
    base = tmp_path / "stat"
    for rel in REMOVED + KEPT:
        (base / rel).parent.mkdir(parents=True, exist_ok=True)
        (base / rel).write_text(rel + "\n")
    (base / "mlan0/02_00_00_00_00_0d.log").mkdir()  # 이름만 맞는 디렉터리는 남는다
    env = _env(tmp_path)

    result = subprocess.run(
        ["bash", "-c", f'tag=postinst\nKEY=PKG\n{_function()}{FUNC} "$1"\necho "rc=$?"\n',
         "postinst-test", str(base)],
        env=env, capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "rc=0\n"
    assert _files(base) == set(KEPT)
    assert (base / "mlan0/02_00_00_00_00_0d.log").is_dir()
    assert f"removed {len(REMOVED)} retired per-AP stat file(s) under {base}" in Path(env["CALLS"]).read_text()


def test_missing_stat_directories_are_not_an_error(tmp_path):
    env = _env(tmp_path)

    result = subprocess.run(
        ["bash", "-c", f'tag=postinst\nKEY=PKG\n{_function()}{FUNC} "$1"\necho "rc=$?"\n',
         "postinst-test", str(tmp_path / "absent")],
        env=env, capture_output=True, text=True, check=False)

    assert (result.returncode, result.stdout, result.stderr) == (0, "rc=0\n", "")
    assert Path(env["CALLS"]).read_text() == ""


def _run_block(tmp_path: Path, *args: str) -> list:
    env = _env(tmp_path)
    script = (f'tag=postinst\nKEY=PKG\nset -- {" ".join(args)}\n'
              f'{FUNC}() {{ printf "cleanup %s\\n" "$*" >> "$CALLS"; }}\n' + _block())
    result = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return Path(env["CALLS"]).read_text().splitlines()


def test_upgrade_only_cleans(tmp_path):
    assert _run_block(tmp_path, "configure", "0.8.8") == ["cleanup /var/log/cantops/stat"]


def test_old_stat_logger_is_already_replaced_before_cleanup():
    # 정리 블록이 재시작하지 않는 근거: preinst upgrade 가 wifi_logger@mlanN 을 멈추면 stat 로거도
    # PartOf 로 멈추고, postinst 가 정리보다 먼저 자식 유닛을 새 코드로 올린다.
    preinst = (POSTINST.parent / "preinst").read_text(encoding="utf-8")
    upgrade = re.search(r"^upgrade\)\n(.*?)^\s*;;", preinst, re.M | re.S)
    assert upgrade, "preinst 에 upgrade) 분기가 없다"
    assert "wifi_logger@mlan0" in upgrade.group(1) and "wifi_logger@mlan1" in upgrade.group(1)
    unit = (POSTINST.parents[1] / "etc/systemd/system/wifi_logger_stat@.service").read_text(encoding="utf-8")
    assert re.search(r"^PartOf=.*\bwifi_logger@%i\.service\b", unit, re.M)
    postinst = POSTINST.read_text(encoding="utf-8")
    assert postinst.index("/usr/local/scripts/wifi_services.sh start") < postinst.index("# >>> stat-ap-log-cleanup")


def test_fresh_install_only_cleans(tmp_path):
    assert _run_block(tmp_path, "configure") == ["cleanup /var/log/cantops/stat"]
