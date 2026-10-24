"""#400: postinst installs the opcd unit as a regular file in /etc/systemd/system.

`systemctl disable` (the `.opc.enabled` true -> false path of wifi_apply_enabled.sh,
factory reset included) removes symlinks, so a symlinked unit disappeared and could
not be enabled again. These tests run postinst's install_opcd_unit against a
scratch root and drive the result with `systemctl --root`, as the gate does.
"""

import shutil
import stat
import subprocess
from pathlib import Path

import pytest


POSTINST = Path(__file__).resolve().parents[4] / "DEBIAN" / "postinst"
SYSTEMCTL = shutil.which("systemctl")
UNIT_TEXT = (
    "[Unit]\nDescription=opcd test unit\n\n"
    "[Service]\nExecStart=/usr/local/opc/bin/opcd\n\n"
    "[Install]\nWantedBy=multi-user.target\n"
)


def make_root(tmp_path: Path, state: str) -> tuple[Path, Path, Path]:
    root = tmp_path / "root"
    src = root / "usr/local/opc/opcd.service"
    src.parent.mkdir(parents=True)
    src.write_text(UNIT_TEXT)
    unit_dir = root / "etc/systemd/system"
    unit_dir.mkdir(parents=True)
    if state == "link-enabled":
        # what postinst's old `ln -sf` plus `systemctl enable` left on devices
        (unit_dir / "opcd.service").symlink_to("/usr/local/opc/opcd.service")
        wants = unit_dir / "multi-user.target.wants"
        wants.mkdir()
        (wants / "opcd.service").symlink_to("/usr/local/opc/opcd.service")
    return root, src, unit_dir / "opcd.service"


def install_unit(src: Path, dest: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", '. "$1"; install_opcd_unit "$2" "$3"',
         "postinst-test", str(POSTINST), str(src), str(dest)],
        check=False, capture_output=True, text=True,
    )


def systemctl(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([SYSTEMCTL, f"--root={root}", *args],
                          check=False, capture_output=True, text=True)


@pytest.mark.skipif(SYSTEMCTL is None, reason="systemctl is required")
@pytest.mark.parametrize("state", ["link-enabled", "link-gone", "fresh"])
def test_unit_survives_disable_and_can_be_enabled_again(tmp_path: Path, state: str) -> None:
    root, src, dest = make_root(tmp_path, state)

    result = install_unit(src, dest)
    assert result.returncode == 0, result.stderr

    result = systemctl(root, "disable", "opcd.service")
    assert result.returncode == 0, result.stderr
    assert dest.exists(), "systemctl disable deleted the opcd unit"
    result = systemctl(root, "enable", "opcd.service")
    assert result.returncode == 0, result.stderr
    assert systemctl(root, "is-enabled", "opcd.service").stdout.strip() == "enabled"

    assert dest.is_file() and not dest.is_symlink()
    assert stat.S_IMODE(dest.stat().st_mode) == 0o644
    assert dest.read_text() == UNIT_TEXT
    assert src.read_text() == UNIT_TEXT
    assert not list(dest.parent.glob(".opcd.service.*")), "temporary file left behind"


def test_missing_source_fails_without_touching_the_installed_unit(tmp_path: Path) -> None:
    root, src, dest = make_root(tmp_path, "fresh")
    dest.write_text("previous unit\n")
    src.unlink()

    result = install_unit(src, dest)

    assert result.returncode != 0
    assert dest.read_text() == "previous unit\n"
    assert not list(dest.parent.glob(".opcd.service.*")), "temporary file left behind"


def test_postinst_installs_the_opcd_unit_through_the_helper() -> None:
    text = POSTINST.read_text(encoding="utf-8")
    assert "install_opcd_unit /usr/local/opc/opcd.service /etc/systemd/system/opcd.service" in text
    assert "ln -sf /usr/local/opc/opcd.service" not in text
