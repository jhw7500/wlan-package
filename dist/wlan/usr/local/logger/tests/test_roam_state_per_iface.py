"""roam 상태 파일 iface별 분리(DBDC 선행 정비) 테스트.

/run/wifi/roam_condition_<iface> · /run/wifi/last_roam_scan_<iface> 로 분리해
mlan0 roam 조건이 mlan1 bgscan 을 정지시키거나 두 roam 데몬이 플래그를 교차
기록(last-writer-wins)하는 오염을 차단한다. 두 데몬은 서로 import 없이 경로
규칙으로만 결합하므로(reader/writer 쌍) 규칙 일치를 여기서 고정한다."""
import os
import subprocess
import sys
import textwrap
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("sUTILS", MagicMock())
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wifi_bgscan
import wifi_roam
import roam_state

wifi_roam.logger = MagicMock()


# ── live scan/transition lock ──────────────────────────────────────────────

def test_scan_transition_lock_is_per_iface_exclusive_and_sigkill_releases(tmp_path):
    """The new live-operation lock is a kernel flock, not a stale-PID marker.

    A killed owner must release its advisory lock immediately while the durable
    path remains.  A different interface must remain independently lockable.
    ``run_dir`` is an explicit test seam; production retains /run/wifi.
    """
    run_dir = tmp_path / "run-wifi"
    holder = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, {str(tmp_path)!r})
        sys.path.insert(0, {os.path.dirname(roam_state.__file__)!r})
        from roam_state import scan_transition_lock
        with scan_transition_lock('mlan0', run_dir={str(run_dir)!r}) as acquired:
            assert acquired
            print('locked', flush=True)
            import time
            time.sleep(30)
        """
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", holder], stdout=subprocess.PIPE, text=True
    )
    try:
        assert proc.stdout.readline().strip() == "locked"
        with roam_state.scan_transition_lock("mlan0", run_dir=str(run_dir)) as locked:
            assert locked is False
        with roam_state.scan_transition_lock("mlan1", run_dir=str(run_dir)) as locked:
            assert locked is True

        proc.kill()
        assert proc.wait(timeout=3) < 0
        lock_path = run_dir / "mlan0.scan-transition.lock"
        assert lock_path.exists(), "release is by FD/process lifetime, never unlink"
        with roam_state.scan_transition_lock("mlan0", run_dir=str(run_dir)) as locked:
            assert locked is True
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=3)


def test_scan_transition_lock_path_error_is_visible_and_never_uses_tmp(monkeypatch):
    calls = []

    def denied(*args, **kwargs):
        calls.append((args, kwargs))
        raise PermissionError("denied")

    monkeypatch.setattr(roam_state.os, "makedirs", denied)
    with pytest.raises(PermissionError):
        with roam_state.scan_transition_lock("mlan0", run_dir="/run/wifi"):
            pass
    assert calls == [(("/run/wifi",), {"exist_ok": True})]
    assert all("/tmp" not in str(call) for call in calls)


def test_scan_transition_lock_open_error_propagates_without_alternate_namespace(monkeypatch):
    makedirs = MagicMock()
    open_calls = []

    def denied(path, flags, mode):
        open_calls.append((path, flags, mode))
        raise PermissionError("open denied")

    monkeypatch.setattr(roam_state.os, "makedirs", makedirs)
    monkeypatch.setattr(roam_state.os, "open", denied)
    with pytest.raises(PermissionError, match="open denied"):
        with roam_state.scan_transition_lock("mlan0", run_dir="/run/wifi"):
            pass

    makedirs.assert_called_once_with("/run/wifi", exist_ok=True)
    assert [call[0] for call in open_calls] == [
        "/run/wifi/mlan0.scan-transition.lock"
    ]
    assert all("/tmp" not in call[0] for call in open_calls)


# ── 경로 규칙 ──────────────────────────────────────────────────────────────

def test_path_rule_identical_between_daemons():
    """writer(wifi_roam)와 reader(wifi_bgscan)의 경로 규칙이 정확히 일치해야
    신호가 전달된다 — 한쪽만 바뀌면 조용히 끊긴다(import 결합 없음)."""
    for iface in ("mlan0", "mlan1"):
        assert wifi_bgscan.roam_state_paths(iface) == wifi_roam.roam_state_paths(iface)


def test_paths_isolated_per_iface():
    """mlan0/mlan1 상태 파일이 전부 서로 다른 경로여야 교차 오염이 없다."""
    flag0, ts0 = wifi_roam.roam_state_paths("mlan0")
    flag1, ts1 = wifi_roam.roam_state_paths("mlan1")
    assert len({flag0, ts0, flag1, ts1}) == 4
    assert flag0.endswith("mlan0") and ts0.endswith("mlan0")
    assert flag1.endswith("mlan1") and ts1.endswith("mlan1")


def test_module_defaults_follow_rule():
    """정의-사용 일관: 모듈 상수 기본값 == 규칙(mlan0) — 구 /tmp 전역 경로 소멸 고정.
    (__main__ 재대입 전까지의 기본값이 규칙과 어긋나면 mlan0 단독에서도 경로가 갈라진다.)"""
    assert (
        wifi_roam.ROAM_CONDITION_FLAG,
        wifi_roam.LAST_SCAN_TIME_FILE,
    ) == wifi_roam.roam_state_paths("mlan0")
    assert (
        wifi_bgscan.ROAM_CONDITION_FLAG,
        wifi_bgscan.LAST_SCAN_TIME_FILE,
    ) == wifi_bgscan.roam_state_paths("mlan0")


# ── atomic write ──────────────────────────────────────────────────────────

def test_set_flag_atomic_and_creates_dir(tmp_path):
    """set_flag 은 미존재 디렉터리를 만들고(부팅 직후 /run/wifi 부재 대응),
    쓰기 후 값이 정확하며 같은 디렉터리에 .tmp 잔재를 남기지 않는다."""
    path = tmp_path / "wifi" / "roam_condition_mlan0"  # 부모 디렉터리 미존재
    wifi_roam.set_flag(1, str(path))
    fields = path.read_text().split()
    assert fields[0] == "1" and int(fields[1]) == os.getpid() and fields[2]
    assert wifi_bgscan.get_flag(str(path)) is True
    wifi_roam.set_flag(0, str(path))
    assert path.read_text() == "0"
    wifi_roam.set_flag(None, str(path))  # 그 외 값 → 빈 파일(OFF)
    assert path.read_text() == ""
    leftovers = [p.name for p in path.parent.iterdir() if p.name != path.name]
    assert leftovers == [], f"원자 교체 후 tmp 잔재: {leftovers}"


def test_record_roam_scan_time_atomic(tmp_path, monkeypatch):
    """시각 기록도 tmp+os.replace 원자 교체 — bgscan float() 파싱 torn read 제거,
    잔재 없음 + 디렉터리 자동 생성."""
    target = tmp_path / "wifi" / "last_roam_scan_mlan1"
    monkeypatch.setattr(wifi_roam, "LAST_SCAN_TIME_FILE", str(target))
    wifi_roam._record_roam_scan_time()
    assert float(target.read_text()) > 0
    leftovers = [p.name for p in target.parent.iterdir() if p.name != target.name]
    assert leftovers == [], f"원자 교체 후 tmp 잔재: {leftovers}"


# ── 호출 시점 전역 해석(재대입 반영) ──────────────────────────────────────

def test_bgscan_get_flag_follows_reassigned_global(tmp_path, monkeypatch):
    """__main__ 재대입 시뮬레이션: get_flag() 기본 경로는 def 시점 바인딩이 아니라
    호출 시점 전역이어야 mlan1 인스턴스가 자기 플래그를 본다. writer=wifi_roam,
    reader=wifi_bgscan 쌍으로 왕복 검증."""
    flag = tmp_path / "roam_condition_mlan1"
    monkeypatch.setattr(wifi_bgscan, "ROAM_CONDITION_FLAG", str(flag))
    assert wifi_bgscan.get_flag() is False  # 파일 없음 → OFF(오류 내성 유지)
    wifi_roam.set_flag(1, str(flag))
    assert wifi_bgscan.get_flag() is True
    wifi_roam.set_flag(0, str(flag))
    assert wifi_bgscan.get_flag() is False
    wifi_roam.set_flag(None, str(flag))  # 빈 파일 → OFF
    assert wifi_bgscan.get_flag() is False


def test_roam_flag_helpers_follow_reassigned_global(tmp_path, monkeypatch):
    """wifi_roam 쪽 set_flag/get_flag 기본 경로도 호출 시점 전역을 따른다."""
    flag = tmp_path / "roam_condition_mlan1"
    monkeypatch.setattr(wifi_roam, "ROAM_CONDITION_FLAG", str(flag))
    wifi_roam.set_flag(1)
    assert flag.read_text().startswith(f"1 {os.getpid()} ")
    assert wifi_roam.get_flag() is True


def test_flag_isolation_between_ifaces(tmp_path):
    """mlan0 플래그 ON 이 mlan1 reader 에 보이지 않는다(교차 정지 차단)."""
    p0 = tmp_path / "roam_condition_mlan0"
    p1 = tmp_path / "roam_condition_mlan1"
    wifi_roam.set_flag(1, str(p0))
    assert wifi_bgscan.get_flag(str(p0)) is True
    assert wifi_bgscan.get_flag(str(p1)) is False


def test_dead_writer_lease_is_removed(tmp_path):
    flag = tmp_path / "roam_condition_mlan0"
    flag.write_text("1 99999999 1")
    assert wifi_bgscan.get_flag(str(flag)) is False
    assert not flag.exists()


def test_pid_reuse_start_time_mismatch_is_removed(tmp_path):
    flag = tmp_path / "roam_condition_mlan0"
    start = wifi_bgscan.process_start_time(os.getpid())
    assert start is not None
    flag.write_text(f"1 {os.getpid()} {int(start) + 1}")
    assert wifi_bgscan.get_flag(str(flag)) is False
    assert not flag.exists()


def test_legacy_integer_flag_is_stale_and_removed(tmp_path):
    flag = tmp_path / "roam_condition_mlan0"
    flag.write_text("1")
    assert wifi_bgscan.get_flag(str(flag)) is False
    assert not flag.exists()


def test_cleanup_removes_only_own_lease(tmp_path, monkeypatch):
    flag = tmp_path / "roam_condition_mlan0"
    monkeypatch.setattr(wifi_roam, "ROAM_CONDITION_FLAG", str(flag))
    wifi_roam.set_flag(1)
    wifi_roam.cleanup()
    assert not flag.exists()

    proc = subprocess.Popen(["sleep", "10"])
    try:
        start = wifi_roam.process_start_time(proc.pid)
        assert start is not None
        flag.write_text(f"1 {proc.pid} {start}")
        wifi_roam.cleanup()
        assert flag.exists(), "다른 살아 있는 writer lease를 지우면 안 됨"
    finally:
        proc.terminate()
        proc.wait(timeout=3)


def test_sigterm_handler_releases_own_lease(tmp_path, monkeypatch):
    flag = tmp_path / "roam_condition_mlan0"
    monkeypatch.setattr(wifi_roam, "ROAM_CONDITION_FLAG", str(flag))
    wifi_roam.set_flag(1)
    with pytest.raises(SystemExit) as exc:
        wifi_roam.handle_sigterm(15, None)
    assert exc.value.code == 0
    assert not flag.exists()


def test_startup_self_heal_keeps_iface_isolation(tmp_path, monkeypatch):
    p0 = tmp_path / "roam_condition_mlan0"
    p1 = tmp_path / "roam_condition_mlan1"
    p0.write_text("1 99999999 1")
    wifi_roam.set_flag(1, str(p1))
    monkeypatch.setattr(wifi_roam, "ROAM_CONDITION_FLAG", str(p0))
    assert wifi_roam.clear_stale_roam_lease() is True
    assert not p0.exists()
    assert wifi_bgscan.get_flag(str(p1)) is True
