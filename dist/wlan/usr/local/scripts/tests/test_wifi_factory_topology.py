import fcntl
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
HELPER = SCRIPTS_DIR / "wifi_factory_topology.py"
WIFI = SCRIPTS_DIR / "wifi.sh"
sys.path.insert(0, str(SCRIPTS_DIR))
import wifi_factory_topology as topology_helper  # noqa: E402


def run_helper(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python3", str(HELPER), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def run_helper_with_fds(
    args: list[str], pass_fds: tuple[int, ...]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python3", str(HELPER), *args],
        pass_fds=pass_fds,
        check=False,
        capture_output=True,
        text=True,
    )


def owner_args() -> list[str]:
    return ["--owner-uid", str(os.getuid()), "--owner-gid", str(os.getgid())]


def write_selector(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)


def test_missing_selector_reads_legacy_eth0_ip_without_creating_file(tmp_path: Path) -> None:
    selector = tmp_path / "wifi_factory_topology.json"

    result = run_helper("read", "--path", str(selector), *owner_args())

    assert result.returncode == 0, result.stderr
    assert result.stdout == "eth0-ip\n"
    assert result.stderr == ""
    assert not selector.exists()


@pytest.mark.parametrize("topology", ["eth0-ip", "peer-route"])
def test_set_persists_exact_schema_and_read_returns_only_topology(
    tmp_path: Path, topology: str
) -> None:
    selector = tmp_path / "wifi_factory_topology.json"

    written = run_helper(
        "set", "--path", str(selector), "--topology", topology, *owner_args()
    )
    read_back = run_helper("read", "--path", str(selector), *owner_args())

    assert written.returncode == 0, written.stderr
    assert written.stdout == f"{topology}\n"
    assert written.stderr == ""
    assert json.loads(selector.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "topology": topology,
    }
    assert stat.S_IMODE(selector.stat().st_mode) == 0o600
    assert selector.stat().st_uid == os.getuid()
    assert selector.stat().st_gid == os.getgid()
    assert selector.stat().st_nlink == 1
    assert read_back.returncode == 0, read_back.stderr
    assert read_back.stdout == f"{topology}\n"
    assert read_back.stderr == ""


@pytest.mark.parametrize(
    "content",
    [
        '{"schema_version":1,"topology":"eth0-ip","topology":"peer-route"}',
        '{"schema_version":true,"topology":"eth0-ip"}',
        '{"schema_version":1,"topology":1}',
        '{"schema_version":1,"topology":"other"}',
        '{"schema_version":1,"topology":"eth0-ip","extra":false}',
        '[{"schema_version":1,"topology":"eth0-ip"}]',
        '{"schema_version":1,"topology":"eth0-ip"} trailing',
    ],
)
def test_read_rejects_noncanonical_json(tmp_path: Path, content: str) -> None:
    selector = tmp_path / "wifi_factory_topology.json"
    write_selector(selector, content)

    result = run_helper("read", "--path", str(selector), *owner_args())

    assert result.returncode != 0
    assert result.stdout == ""
    assert "Error:" in result.stderr


def test_read_rejects_oversized_json(tmp_path: Path) -> None:
    selector = tmp_path / "wifi_factory_topology.json"
    write_selector(
        selector,
        '{"schema_version":1,"topology":"eth0-ip","padding":"'
        + ("x" * 5000)
        + '"}',
    )

    result = run_helper("read", "--path", str(selector), *owner_args())

    assert result.returncode != 0
    assert result.stdout == ""
    assert "too large" in result.stderr


@pytest.mark.parametrize("unsafe", ["mode", "hardlink", "symlink"])
def test_read_rejects_unsafe_selector_path(tmp_path: Path, unsafe: str) -> None:
    selector = tmp_path / "wifi_factory_topology.json"
    if unsafe == "symlink":
        target = tmp_path / "target.json"
        write_selector(target, '{"schema_version":1,"topology":"eth0-ip"}\n')
        selector.symlink_to(target)
    else:
        write_selector(selector, '{"schema_version":1,"topology":"eth0-ip"}\n')
        if unsafe == "mode":
            selector.chmod(0o644)
        else:
            os.link(selector, tmp_path / "second-link.json")

    result = run_helper("read", "--path", str(selector), *owner_args())

    assert result.returncode != 0
    assert result.stdout == ""
    assert "Error:" in result.stderr


def test_set_rejects_corrupt_existing_selector_without_replacing_it(tmp_path: Path) -> None:
    selector = tmp_path / "wifi_factory_topology.json"
    original = b'{"schema_version":1,"topology":"broken"}\n'
    selector.write_bytes(original)
    selector.chmod(0o600)

    result = run_helper(
        "set", "--path", str(selector), "--topology", "peer-route", *owner_args()
    )

    assert result.returncode != 0
    assert result.stdout == ""
    assert selector.read_bytes() == original


def test_set_rejects_group_writable_parent(tmp_path: Path) -> None:
    unsafe_parent = tmp_path / "unsafe"
    unsafe_parent.mkdir()
    unsafe_parent.chmod(0o770)
    selector = unsafe_parent / "wifi_factory_topology.json"

    result = run_helper(
        "set", "--path", str(selector), "--topology", "peer-route", *owner_args()
    )

    assert result.returncode != 0
    assert result.stdout == ""
    assert not selector.exists()
    assert "parent" in result.stderr


@pytest.mark.parametrize("unsafe", ["writable", "symlink"])
def test_missing_selector_does_not_hide_an_unsafe_existing_parent(
    tmp_path: Path, unsafe: str
) -> None:
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    if unsafe == "writable":
        real_parent.chmod(0o770)
        parent = real_parent
    else:
        parent = tmp_path / "linked"
        parent.symlink_to(real_parent, target_is_directory=True)

    result = run_helper(
        "read", "--path", str(parent / "wifi_factory_topology.json"), *owner_args()
    )

    assert result.returncode != 0
    assert result.stdout == ""
    assert "parent" in result.stderr


def test_missing_descendant_does_not_hide_a_symlinked_parent_ancestor(
    tmp_path: Path,
) -> None:
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real_parent, target_is_directory=True)
    selector = alias / "not-created" / "wifi_factory_topology.json"

    result = run_helper("read", "--path", str(selector), *owner_args())

    assert result.returncode != 0
    assert result.stdout == ""
    assert "parent" in result.stderr


def test_file_fsync_failure_keeps_old_selector_and_removes_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selector = tmp_path / "wifi_factory_topology.json"
    write_selector(selector, '{"schema_version":1,"topology":"eth0-ip"}\n')
    original = selector.read_bytes()
    real_fsync = topology_helper.os.fsync

    def fail_regular_file_fsync(fd: int) -> None:
        if stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(5, "injected file fsync failure")
        real_fsync(fd)

    monkeypatch.setattr(topology_helper.os, "fsync", fail_regular_file_fsync)

    with pytest.raises(OSError, match="injected file fsync failure"):
        topology_helper.set_topology(
            str(selector), "peer-route", os.getuid(), os.getgid()
        )

    assert selector.read_bytes() == original
    assert not list(tmp_path.glob(".wifi_factory_topology.json.tmp.*"))


def test_directory_fsync_failure_reports_visible_but_unconfirmed_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selector = tmp_path / "wifi_factory_topology.json"
    write_selector(selector, '{"schema_version":1,"topology":"eth0-ip"}\n')
    real_fsync = topology_helper.os.fsync

    def fail_directory_fsync(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(5, "injected directory fsync failure")
        real_fsync(fd)

    monkeypatch.setattr(topology_helper.os, "fsync", fail_directory_fsync)

    with pytest.raises(
        topology_helper.SelectorError,
        match="visible.*durability is unconfirmed",
    ):
        topology_helper.set_topology(
            str(selector), "peer-route", os.getuid(), os.getgid()
        )

    assert json.loads(selector.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "topology": "peer-route",
    }
    assert not list(tmp_path.glob(".wifi_factory_topology.json.tmp.*"))


def test_unsafe_existing_lock_blocks_set(tmp_path: Path) -> None:
    selector = tmp_path / "wifi_factory_topology.json"
    lock = tmp_path / ".wifi_factory_topology.json.lock"
    lock.write_text("unsafe", encoding="utf-8")
    lock.chmod(0o644)

    result = run_helper(
        "set", "--path", str(selector), "--topology", "peer-route", *owner_args()
    )

    assert result.returncode != 0
    assert result.stdout == ""
    assert "unsafe selector lock" in result.stderr
    assert not selector.exists()


def test_concurrent_set_calls_serialize_without_partial_json(tmp_path: Path) -> None:
    selector = tmp_path / "wifi_factory_topology.json"
    commands = [
        [
            "python3",
            str(HELPER),
            "set",
            "--path",
            str(selector),
            "--topology",
            "eth0-ip" if index % 2 == 0 else "peer-route",
            *owner_args(),
        ]
        for index in range(8)
    ]

    processes = [
        subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for command in commands
    ]
    results = [process.communicate(timeout=10) + (process.returncode,) for process in processes]

    assert all(returncode == 0 for _, _, returncode in results), results
    assert json.loads(selector.read_text(encoding="utf-8")) in (
        {"schema_version": 1, "topology": "eth0-ip"},
        {"schema_version": 1, "topology": "peer-route"},
    )
    assert not list(tmp_path.glob(".wifi_factory_topology.json.tmp.*"))


def test_set_with_borrowed_lock_inherited_succeeds_and_keeps_parent_lock_held(
    tmp_path: Path,
) -> None:
    selector = tmp_path / "wifi_factory_topology.json"
    initial = run_helper(
        "set", "--path", str(selector), "--topology", "eth0-ip", *owner_args()
    )
    assert initial.returncode == 0, initial.stderr
    lock_path = tmp_path / ".wifi_factory_topology.json.lock"
    lock_fd = os.open(lock_path, os.O_RDWR)
    fcntl.flock(lock_fd, fcntl.LOCK_EX)
    try:
        result = run_helper_with_fds(
            [
                "set",
                "--path",
                str(selector),
                "--topology",
                "peer-route",
                "--lock-fd",
                str(lock_fd),
                *owner_args(),
            ],
            (lock_fd,),
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout == "peer-route\n"
        probe_fd = os.open(lock_path, os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(probe_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(probe_fd)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)

    assert json.loads(selector.read_text(encoding="utf-8"))["topology"] == "peer-route"


@pytest.mark.parametrize(
    "invalid_fd", ["wrong-file", "same-file-unlocked", "shared", "contended"]
)
def test_set_with_unproven_borrowed_lock_fails_without_change(
    tmp_path: Path, invalid_fd: str
) -> None:
    selector = tmp_path / "wifi_factory_topology.json"
    initial = run_helper(
        "set", "--path", str(selector), "--topology", "eth0-ip", *owner_args()
    )
    assert initial.returncode == 0, initial.stderr
    before = selector.read_bytes()
    lock_path = tmp_path / ".wifi_factory_topology.json.lock"
    holder_fd = -1
    if invalid_fd == "wrong-file":
        wrong_path = tmp_path / "unrelated.lock"
        wrong_path.write_bytes(b"")
        wrong_path.chmod(0o600)
        borrowed_fd = os.open(wrong_path, os.O_RDWR)
        fcntl.flock(borrowed_fd, fcntl.LOCK_EX)
    else:
        if invalid_fd == "contended":
            holder_fd = os.open(lock_path, os.O_RDWR)
            fcntl.flock(holder_fd, fcntl.LOCK_EX)
        borrowed_fd = os.open(lock_path, os.O_RDWR)
        if invalid_fd == "shared":
            fcntl.flock(borrowed_fd, fcntl.LOCK_SH)
    try:
        result = run_helper_with_fds(
            [
                "set",
                "--path",
                str(selector),
                "--topology",
                "peer-route",
                "--lock-fd",
                str(borrowed_fd),
                *owner_args(),
            ],
            (borrowed_fd,),
        )
    finally:
        if invalid_fd in ("wrong-file", "shared"):
            fcntl.flock(borrowed_fd, fcntl.LOCK_UN)
        os.close(borrowed_fd)
        if holder_fd >= 0:
            fcntl.flock(holder_fd, fcntl.LOCK_UN)
            os.close(holder_fd)

    assert result.returncode != 0
    assert result.stdout == ""
    assert "Error:" in result.stderr
    assert selector.read_bytes() == before


@pytest.mark.parametrize("missing_parent", [False, True])
def test_snapshot_missing_safe_path_returns_absent_generation_without_writing(
    tmp_path: Path, missing_parent: bool
) -> None:
    parent = tmp_path / "not-created" if missing_parent else tmp_path
    selector = parent / "wifi_factory_topology.json"

    result = run_helper("snapshot", "--path", str(selector), *owner_args())

    assert result.returncode == 0, result.stderr
    assert result.stdout == '{"topology":"eth0-ip","generation":"absent"}\n'
    assert result.stderr == ""
    assert not selector.exists()


@pytest.mark.parametrize("topology", ["eth0-ip", "peer-route"])
def test_snapshot_is_stable_compact_and_does_not_mutate_selector(
    tmp_path: Path, topology: str
) -> None:
    selector = tmp_path / "wifi_factory_topology.json"
    set_result = run_helper(
        "set", "--path", str(selector), "--topology", topology, *owner_args()
    )
    assert set_result.returncode == 0, set_result.stderr
    before_bytes = selector.read_bytes()
    before = selector.stat()

    first = run_helper("snapshot", "--path", str(selector), *owner_args())
    second = run_helper("snapshot", "--path", str(selector), *owner_args())

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first.stdout == second.stdout
    snapshot = json.loads(first.stdout)
    assert list(snapshot) == ["topology", "generation"]
    assert snapshot["topology"] == topology
    assert snapshot["generation"].startswith("v1-")
    assert len(snapshot["generation"]) == 67
    assert first.stdout == json.dumps(snapshot, separators=(",", ":")) + "\n"
    after = selector.stat()
    assert selector.read_bytes() == before_bytes
    assert (
        after.st_dev,
        after.st_ino,
        after.st_mtime_ns,
        after.st_ctime_ns,
        after.st_size,
        stat.S_IMODE(after.st_mode),
    ) == (
        before.st_dev,
        before.st_ino,
        before.st_mtime_ns,
        before.st_ctime_ns,
        before.st_size,
        0o600,
    )


def test_snapshot_generation_changes_after_same_value_atomic_replacement(
    tmp_path: Path,
) -> None:
    selector = tmp_path / "wifi_factory_topology.json"
    first_set = run_helper(
        "set", "--path", str(selector), "--topology", "peer-route", *owner_args()
    )
    assert first_set.returncode == 0, first_set.stderr
    before = run_helper("snapshot", "--path", str(selector), *owner_args())
    second_set = run_helper(
        "set", "--path", str(selector), "--topology", "peer-route", *owner_args()
    )
    after = run_helper("snapshot", "--path", str(selector), *owner_args())

    assert before.returncode == 0, before.stderr
    assert second_set.returncode == 0, second_set.stderr
    assert after.returncode == 0, after.stderr
    assert json.loads(before.stdout)["topology"] == "peer-route"
    assert json.loads(after.stdout)["topology"] == "peer-route"
    assert json.loads(before.stdout)["generation"] != json.loads(after.stdout)["generation"]


@pytest.mark.parametrize("unsafe", ["corrupt", "mode", "hardlink", "symlink"])
def test_snapshot_rejects_corrupt_or_unsafe_selector(tmp_path: Path, unsafe: str) -> None:
    selector = tmp_path / "wifi_factory_topology.json"
    if unsafe == "symlink":
        target = tmp_path / "target.json"
        write_selector(target, '{"schema_version":1,"topology":"eth0-ip"}\n')
        selector.symlink_to(target)
    else:
        content = (
            '{"schema_version":1,"topology":"broken"}\n'
            if unsafe == "corrupt"
            else '{"schema_version":1,"topology":"eth0-ip"}\n'
        )
        write_selector(selector, content)
        if unsafe == "mode":
            selector.chmod(0o644)
        elif unsafe == "hardlink":
            os.link(selector, tmp_path / "second-link.json")
    before = target.read_bytes() if unsafe == "symlink" else selector.read_bytes()

    result = run_helper("snapshot", "--path", str(selector), *owner_args())

    assert result.returncode != 0
    assert result.stdout == ""
    assert "Error:" in result.stderr
    observed = target.read_bytes() if unsafe == "symlink" else selector.read_bytes()
    assert observed == before


def test_snapshot_rejects_metadata_change_during_same_fd_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selector = tmp_path / "wifi_factory_topology.json"
    write_selector(selector, '{"schema_version":1,"topology":"eth0-ip"}\n')
    real_fstat = topology_helper.os.fstat
    regular_calls = 0

    def changing_fstat(fd: int) -> os.stat_result:
        nonlocal regular_calls
        metadata = real_fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            return metadata
        regular_calls += 1
        if regular_calls < 2:
            return metadata
        fields = list(metadata)
        fields[8] += 1
        return os.stat_result(fields)

    monkeypatch.setattr(topology_helper.os, "fstat", changing_fstat)

    with pytest.raises(topology_helper.SelectorError, match="changed during snapshot"):
        topology_helper.snapshot_topology(
            str(selector), os.getuid(), os.getgid()
        )


def run_wifi(selector: Path, active_json: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "WIFI_FACTORY_TOPOLOGY_SCRIPT": str(HELPER),
            "WIFI_FACTORY_TOPOLOGY_PATH": str(selector),
            "WIFI_FACTORY_TOPOLOGY_OWNER_UID": str(os.getuid()),
            "WIFI_FACTORY_TOPOLOGY_OWNER_GID": str(os.getgid()),
            "WIFI_INIT_CONF_JSON": str(active_json),
        }
    )
    return subprocess.run(
        ["bash", str(WIFI), "0", "br", "factory-profile", *args],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def test_wifi_factory_profile_show_uses_missing_legacy_default(tmp_path: Path) -> None:
    selector = tmp_path / "wifi_factory_topology.json"
    active_json = tmp_path / "wifi_init_conf.json"
    active_json.write_text('{"keep":"unchanged"}\n', encoding="utf-8")
    before = active_json.read_bytes()

    result = run_wifi(selector, active_json, "show")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "eth0-ip\n"
    assert not selector.exists()
    assert active_json.read_bytes() == before


def test_wifi_factory_profile_without_name_is_show(tmp_path: Path) -> None:
    selector = tmp_path / "wifi_factory_topology.json"
    active_json = tmp_path / "wifi_init_conf.json"
    active_json.write_text('{"keep":"unchanged"}\n', encoding="utf-8")

    result = run_wifi(selector, active_json)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "eth0-ip\n"
    assert not selector.exists()


def test_wifi_factory_profile_dry_run_never_writes_selector_or_active_json(
    tmp_path: Path,
) -> None:
    selector = tmp_path / "wifi_factory_topology.json"
    active_json = tmp_path / "wifi_init_conf.json"
    active_json.write_text('{"keep":"unchanged"}\n', encoding="utf-8")
    before = active_json.read_bytes()

    result = run_wifi(selector, active_json, "peer-route")

    assert result.returncode == 0, result.stderr
    assert "peer-route" in result.stdout
    assert "dry-run" in result.stdout
    assert not selector.exists()
    assert active_json.read_bytes() == before


def test_wifi_factory_profile_apply_writes_only_selector(tmp_path: Path) -> None:
    selector = tmp_path / "wifi_factory_topology.json"
    active_json = tmp_path / "wifi_init_conf.json"
    active_json.write_text('{"keep":"unchanged"}\n', encoding="utf-8")
    before = active_json.read_bytes()

    result = run_wifi(selector, active_json, "peer-route", "apply")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "peer-route\n"
    assert json.loads(selector.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "topology": "peer-route",
    }
    assert active_json.read_bytes() == before


def test_runtime_bridge_profile_does_not_change_factory_selector(tmp_path: Path) -> None:
    selector = tmp_path / "wifi_factory_topology.json"
    active_json = tmp_path / "wifi_init_conf.json"
    active_json.write_text(
        json.dumps(
            {
                "wbridge": {
                    "engine": "pcap",
                    "peer_route": {"enabled": True},
                    "ip_discovery": True,
                    "arp_ignore_always": {"enabled": False},
                    "moal": {"local_hairpin": ""},
                    "eth_fallback": {"enabled": False},
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    set_result = run_helper(
        "set", "--path", str(selector), "--topology", "peer-route", *owner_args()
    )
    assert set_result.returncode == 0, set_result.stderr
    before = selector.read_bytes()
    before_stat = selector.stat()
    env = os.environ.copy()
    env["WIFI_INIT_CONF_JSON"] = str(active_json)

    result = subprocess.run(
        ["bash", str(WIFI), "0", "br", "profile", "eth0-ip", "apply"],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    after_stat = selector.stat()
    assert selector.read_bytes() == before
    assert (after_stat.st_ino, after_stat.st_mtime_ns, stat.S_IMODE(after_stat.st_mode)) == (
        before_stat.st_ino,
        before_stat.st_mtime_ns,
        0o600,
    )


@pytest.mark.parametrize(
    "args",
    [
        ("unknown",),
        ("show", "apply"),
        ("peer-route", "unexpected"),
        ("peer-route", "apply", "extra"),
    ],
)
def test_wifi_factory_profile_rejects_invalid_forms(tmp_path: Path, args: tuple[str, ...]) -> None:
    selector = tmp_path / "wifi_factory_topology.json"
    active_json = tmp_path / "wifi_init_conf.json"
    active_json.write_text('{"keep":"unchanged"}\n', encoding="utf-8")

    result = run_wifi(selector, active_json, *args)

    assert result.returncode != 0
    assert not selector.exists()
    assert active_json.read_text(encoding="utf-8") == '{"keep":"unchanged"}\n'
