import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest


POSTINST = Path(__file__).resolve().parents[4] / "DEBIAN" / "postinst"
FAKEROOT = shutil.which("fakeroot")


def run_in_fakeroot(script: str, *args: Path, env: dict[str, str] | None = None):
    if FAKEROOT is None:
        pytest.skip("fakeroot is required for postinst ownership tests")
    command_env = os.environ.copy()
    command_env["DEBIAN_HAS_FRONTEND"] = "1"
    if env:
        command_env.update(env)
    return subprocess.run(
        [FAKEROOT, "--", "bash", "-c", script, "postinst-test", *map(str, args)],
        check=False,
        capture_output=True,
        text=True,
        env=command_env,
    )


def factory_parent_install_call() -> str:
    calls = [
        line.strip()
        for line in POSTINST.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith(
            "prepare_factory_selector_parent /usr/local/etc /"
        )
    ]
    assert len(calls) == 1
    return calls[0]


def test_configure_stops_before_later_mutation_when_parent_prep_fails(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "later-mutation"
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'''prepare_factory_selector_parent() {{ return 73; }}
{factory_parent_install_call()}
printf changed > "$1"
''',
            "postinst-call-test",
            str(marker),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert not marker.exists()


def test_hardens_legacy_owner_and_mode_without_touching_children(tmp_path: Path) -> None:
    install_root = tmp_path / "root"
    selector = install_root / "usr/local/etc/wifi_factory_topology.json"
    selector.parent.mkdir(parents=True)
    original = b'{"schema_version":1,"topology":"peer-route"}\n'
    selector.write_bytes(original)

    result = run_in_fakeroot(
        r'''
set -e
root=$1
postinst=$2
chown -R 0:0 "$root"
find "$root" -type d -exec chmod 0755 {} +
chown 1003:1003 "$root/usr/local/etc"
chmod 0775 "$root/usr/local/etc"
. "$postinst"
prepare_factory_selector_parent "$root/usr/local/etc" "$root"
stat -c '%u:%g %a' "$root/usr/local/etc"
''',
        install_root,
        POSTINST,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "0:0 755\n"
    assert selector.read_bytes() == original


def test_preserves_existing_selector_metadata_and_bytes(tmp_path: Path) -> None:
    install_root = tmp_path / "root"
    selector = install_root / "usr/local/etc/wifi_factory_topology.json"
    selector.parent.mkdir(parents=True)
    original = b'{ "schema_version": 1, "topology": "eth0-ip" }\n'
    selector.write_bytes(original)
    selector.chmod(0o640)

    result = run_in_fakeroot(
        r'''
set -e
root=$1
postinst=$2
chown -R 0:0 "$root"
find "$root" -type d -exec chmod 0755 {} +
chown 1003:1003 "$root/usr/local/etc"
chmod 0775 "$root/usr/local/etc"
chown 0:0 "$root/usr/local/etc/wifi_factory_topology.json"
chmod 0640 "$root/usr/local/etc/wifi_factory_topology.json"
. "$postinst"
prepare_factory_selector_parent "$root/usr/local/etc" "$root"
stat -c '%u:%g %a' "$root/usr/local/etc/wifi_factory_topology.json"
''',
        install_root,
        POSTINST,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "0:0 640\n"
    assert selector.read_bytes() == original


def test_preserves_parent_permissions_that_are_already_more_restrictive(
    tmp_path: Path,
) -> None:
    install_root = tmp_path / "root"
    target = install_root / "usr/local/etc"
    target.mkdir(parents=True)

    result = run_in_fakeroot(
        r'''
set -e
root=$1
postinst=$2
chown -R 0:0 "$root"
find "$root" -type d -exec chmod 0755 {} +
chown 1003:1003 "$root/usr/local/etc"
chmod 0700 "$root/usr/local/etc"
. "$postinst"
prepare_factory_selector_parent "$root/usr/local/etc" "$root"
stat -c '%u:%g %a' "$root/usr/local/etc"
''',
        install_root,
        POSTINST,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "0:0 700\n"


@pytest.mark.parametrize("unsafe", ["ancestor-symlink", "leaf-symlink", "non-directory"])
def test_rejects_unsafe_path_without_changing_referent(
    tmp_path: Path, unsafe: str
) -> None:
    install_root = tmp_path / "root"
    referent = tmp_path / "referent"
    referent.mkdir()
    referent.chmod(0o777)
    marker = referent / "marker"
    marker.write_bytes(b"unchanged\n")
    (install_root / "usr").mkdir(parents=True)

    if unsafe == "ancestor-symlink":
        (install_root / "usr/local").symlink_to(referent, target_is_directory=True)
        target = install_root / "usr/local/etc"
    elif unsafe == "leaf-symlink":
        (install_root / "usr/local").mkdir()
        (install_root / "usr/local/etc").symlink_to(referent, target_is_directory=True)
        target = install_root / "usr/local/etc"
    else:
        (install_root / "usr/local").mkdir()
        target = install_root / "usr/local/etc"
        target.write_bytes(b"not a directory\n")

    result = run_in_fakeroot(
        r'''
set -e
root=$1
postinst=$2
target=$3
chown -R 0:0 "$root"
find "$root" -type d -exec chmod 0755 {} +
. "$postinst"
prepare_factory_selector_parent "$target" "$root"
''',
        install_root,
        POSTINST,
        target,
    )

    assert result.returncode != 0
    assert "unsafe factory selector path" in result.stderr
    assert marker.read_bytes() == b"unchanged\n"
    assert stat.S_IMODE(referent.stat().st_mode) == 0o777
    if unsafe == "non-directory":
        assert target.read_bytes() == b"not a directory\n"


@pytest.mark.parametrize("failed_command", ["chown", "chmod", "sync"])
def test_metadata_and_sync_failures_propagate(
    tmp_path: Path, failed_command: str
) -> None:
    install_root = tmp_path / "root"
    target = install_root / "usr/local/etc"
    target.mkdir(parents=True)
    fail_bin = tmp_path / "fail-bin"
    fail_bin.mkdir()
    failing_tool = fail_bin / failed_command
    failing_tool.write_text("#!/bin/sh\nexit 73\n", encoding="utf-8")
    failing_tool.chmod(0o755)

    result = run_in_fakeroot(
        r'''
set -e
root=$1
postinst=$2
fail_bin=$3
chown -R 0:0 "$root"
find "$root" -type d -exec chmod 0755 {} +
chown 1003:1003 "$root/usr/local/etc"
chmod 0775 "$root/usr/local/etc"
. "$postinst"
PATH="$fail_bin:$PATH"
export PATH
prepare_factory_selector_parent "$root/usr/local/etc" "$root"
''',
        install_root,
        POSTINST,
        fail_bin,
    )

    assert result.returncode != 0
    assert f"failed to {failed_command}" in result.stderr
