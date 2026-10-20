"""#371: 정상 재부팅의 셧다운 구간까지 journal 을 내보내는 유닛 계약.

systemd 는 셧다운 때 시작의 역순으로 정지한다. 부팅 완료 유닛(After=multi-user.target)의
ExecStop 은 셧다운 맨 앞에서 돌아 서비스 정지 구간(D-Bus·opcd 정지 등)을 놓쳤다
(랩 보드 실측: 셧다운 시작 2초 뒤에서 내보내기가 끝남). 셧다운 스냅샷은 일반 서비스보다
먼저 시작해(Before=basic.target) 그들이 다 멈춘 뒤 정지하고, journald 와 내보내기
마운트보다는 먼저 정지하는(After=) 유닛이 맡는다.
"""
import re
from pathlib import Path


WLAN_ROOT = Path(__file__).resolve().parents[4]
REPO_ROOT = WLAN_ROOT.parents[1]
UNIT_ROOT = WLAN_ROOT / "etc/systemd/system"
PAYLOAD_MANIFEST = WLAN_ROOT / "DEBIAN/payload-manifest.txt"
SOURCE_MANIFEST = REPO_ROOT / "scripts/source_archive_manifest.txt"
POSTINST = WLAN_ROOT / "DEBIAN/postinst"

SHUTDOWN_UNIT = "journald-snapshot-shutdown.service"
BOUNDARY_UNIT = "journald-snapshot-boundary.service"
SNAPSHOT = "/usr/local/scripts/journald_snapshot.sh"


def directives(name):
    """유닛 파일의 Key=값 을 반복 키까지 모아 {key: [값 토큰...]} 로 돌려준다."""
    out = {}
    for line in (UNIT_ROOT / name).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";", "[")) or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out.setdefault(key.strip(), []).extend(value.split())
    return out


def test_shutdown_unit_stops_after_services_and_before_journald_and_mount():
    d = directives(SHUTDOWN_UNIT)
    assert d["DefaultDependencies"] == ["no"]
    # 일반 서비스는 basic.target 뒤에 시작하므로 셧다운 때 이 유닛보다 먼저 정지한다.
    assert "basic.target" in d["Before"]
    assert "shutdown.target" in d["Conflicts"] and "shutdown.target" in d["Before"]
    # journald 와 내보내기 마운트는 이 유닛 뒤에 정지한다. /var/log 는 심볼릭 링크라
    # 실제 마운트 경로도 함께 적는다(fake-hwclock.service 와 같은 이유).
    assert "systemd-journald.service" in d["After"]
    assert {"/var/volatile/log/cantops", "/var/log/cantops"} <= set(d["RequiresMountsFor"])
    assert d["Type"] == ["oneshot"] and d["RemainAfterExit"] == ["yes"]
    assert d["ExecStop"] == [SNAPSHOT]
    assert d["WantedBy"] == ["basic.target"]


def test_boundary_unit_keeps_only_the_boot_completion_snapshot():
    d = directives(BOUNDARY_UNIT)
    assert d["ExecStart"] == [SNAPSHOT]
    assert "ExecStop" not in d, "셧다운 스냅샷은 셧다운 전용 유닛이 맡는다(셧다운 맨 앞 중복 실행 제거)"


def test_shutdown_unit_is_enabled_and_shipped():
    enable_lines = [l for l in POSTINST.read_text().splitlines()
                    if re.match(r"\s*systemctl enable watchdog switchd ", l)]
    assert len(enable_lines) == 1
    assert f" {SHUTDOWN_UNIT} " in enable_lines[0]
    # enable 만으로는 다음 부팅 전까지 inactive 라 업그레이드 직후 첫 셧다운에 ExecStop 이
    # 돌지 않는다(boundary 의 ExecStop 은 이미 없다). 설치 시점에 한 번 띄운다.
    assert any(re.match(rf"\s*systemctl start --no-block {re.escape(SHUTDOWN_UNIT)} \|\| true$", l)
               for l in POSTINST.read_text().splitlines())
    assert f"etc/systemd/system/{SHUTDOWN_UNIT}" in PAYLOAD_MANIFEST.read_text().splitlines()
    assert f"dist/wlan/etc/systemd/system/{SHUTDOWN_UNIT}" in SOURCE_MANIFEST.read_text().splitlines()
