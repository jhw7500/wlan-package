"""netdev 재생성 시 wpa_supplicant 재부착 보장 — 2계층 방어의 계약 고정.

드라이버 리셋(SDIO FLR, `fw_reload=5`)이나 재로드로 mlan* netdev 가 파괴·재생성될 때
`wpa_supplicant -i<iface>` 의 거동이 라운드마다 갈린다(2026-08-18 cts-wlan 실기 관측):

  A. 프로세스가 **죽는** 라운드 → 유닛의 `Restart=` 가 복구한다.
  B. 프로세스가 **살아남는** 라운드 → 파괴된 구 ifindex 에 좀비 부착된 채 동명의 재생성
     iface 에 재부착하지 않아 영구 "Not connected". 프로세스가 죽지 않으므로 `Restart=`
     로는 잡히지 않고, udev `try-restart` 만이 덮는다.

두 계층 중 하나라도 빠지면 한쪽 라운드가 조용히 열린다. 이 파일은 각 계층의 **성립
조건**을 고정한다 — 특히 `try-restart`/`--no-block` 은 대체 가능한 표현이 아니라 아래
근거로 선택된 것이라, 무심코 `restart`/블로킹 호출로 바뀌면 다른 결함이 된다.
"""
import os
import re
import subprocess
from pathlib import Path

import pytest

WLAN_ROOT = Path(__file__).resolve().parents[4]
REPO_ROOT = WLAN_ROOT.parents[1]

UDEV_RULE = WLAN_ROOT / "etc/udev/rules.d/99-wlan-wpa-reattach.rules"
ARP_SEAL_RULE = WLAN_ROOT / "etc/udev/rules.d/99-wlan-arp-seal.rules"
WPA_UNIT = WLAN_ROOT / "opt/wlan/config/wpa_supplicant/wpa_supplicant@.service"
WIFI_INIT = WLAN_ROOT / "usr/local/scripts/wifi_init.sh"
HELPER = WLAN_ROOT / "usr/local/scripts/wlan_wpa_reattach.sh"
POSTINST = WLAN_ROOT / "DEBIAN/postinst"
PAYLOAD_MANIFEST = WLAN_ROOT / "DEBIAN/payload-manifest.txt"
SOURCE_MANIFEST = REPO_ROOT / "scripts/source_archive_manifest.txt"


def _logical_line(text, idx):
    """idx 가 속한 셸 논리 행(백슬래시 연결 포함)을 돌려준다."""
    start = text.rfind("\n", 0, idx) + 1
    out = []
    while True:
        end = text.find("\n", start)
        if end == -1:
            out.append(text[start:])
            break
        line = text[start:end]
        out.append(line)
        if not line.rstrip().endswith("\\"):
            break
        start = end + 1
    return "\n".join(out)


def _rule_line():
    """주석을 제외한 실제 rule 행(단일)."""
    lines = [
        ln.strip()
        for ln in UDEV_RULE.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    assert len(lines) == 1, f"rule 행이 1개가 아니다: {lines}"
    return lines[0]


# ── 계층 B: udev try-restart ────────────────────────────────────────────────

def test_rule_triggers_on_mlan_netdev_add():
    """arp-seal rule 과 같은 add 트리거를 써야 재생성 시점에 걸린다."""
    line = _rule_line()
    for token in ('ACTION=="add"', 'SUBSYSTEM=="net"', 'KERNEL=="mlan[0-9]*"'):
        assert token in line, f"{token} 없음 — 트리거가 netdev 재생성에 걸리지 않는다"


def test_rule_delegates_to_helper_with_absolute_path():
    """RUN 은 셸을 거치지 않으므로 절대 경로가 필요하고, 인스턴스는 %k 로 넘긴다."""
    line = _rule_line()
    m = re.search(r'RUN\+="(/[^ "]+)([^"]*)"', line)
    assert m, "RUN+= 에 절대 경로 명령이 없다"
    assert m.group(1) == "/usr/local/scripts/wlan_wpa_reattach.sh", (
        f"헬퍼가 아니다: {m.group(1)}"
    )
    assert m.group(2).strip() == "%k", "인터페이스를 %k 로 넘기지 않는다"


def test_helper_is_executable():
    """udev RUN 은 프로그램을 직접 exec 한다 — 실행 비트가 없으면 조용히 아무 일도 없다.

    **git 인덱스 모드**를 본다. 이 저장소는 core.fileMode=false 라 로컬 chmod 가 git 에
    반영되지 않으며, 빌드는 신선한 체크아웃에서 이뤄지므로 인덱스 모드(100755)가
    패키지에 실리는 실제 권한을 결정한다. 파일시스템 모드만 확인하면 로컬에서만
    통과하고 CI 빌드 산출물은 non-executable 이 되는 상태를 놓친다.
    """
    assert HELPER.exists(), "헬퍼가 없다"
    r = subprocess.run(["git", "ls-files", "-s", "--", str(HELPER)],
                       capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=30)
    assert r.returncode == 0 and r.stdout.strip(), f"git 인덱스에 없다: {r.stderr}"
    mode = r.stdout.split()[0]
    assert mode == "100755", (
        f"git 인덱스 모드가 {mode} 다 — 신선한 체크아웃에서 non-executable 이 되어 "
        "udev RUN 이 조용히 실패한다. `git update-index --chmod=+x` 필요"
    )


def test_helper_uses_no_block_for_job_calls():
    """job 을 거는 호출은 --no-block 이어야 한다.

    man systemctl: 미지정 시 "systemctl will wait until the unit's start-up is
    completed". 이 유닛은 지금 생성 중인 sys-subsystem-net-devices-%i.device 를
    Requires/After 로 걸어 블로킹 호출은 그 device 유닛을 기다린다. man 7 udev 는 RUN
    파생 프로세스가 이벤트 처리 후 "unconditionally killed" 된다고 명시하므로 그 대기는
    작업을 반쯤 남기고 끊긴다. (reset-failed / is-* 는 job 을 만들지 않아 대상이 아니다.)
    """
    text = HELPER.read_text(encoding="utf-8")
    for verb in ("restart", "start"):
        for m in re.finditer(rf'\$SYSTEMCTL"?\s+([^\n]*\b{verb}\b[^\n]*)', text):
            assert "--no-block" in m.group(1), f"job 호출에 --no-block 없음: {m.group(1)}"


def test_rule_sorts_after_arp_seal():
    """udev 는 파일명 사전순으로 처리한다 — 기존 netdev 훅 뒤에 오도록 유지한다."""
    assert ARP_SEAL_RULE.exists(), "전례 rule 이 사라졌다(경로 변경 시 테스트 갱신)"
    assert ARP_SEAL_RULE.name < UDEV_RULE.name


# ── 헬퍼 동작 (가짜 systemctl 로 실행 검증) ────────────────────────────────
#
# 문자열 단언은 분기를 지워도 통과하기 쉽다. 여기서는 헬퍼를 **실제로 실행**해
# 어떤 systemctl 하위명령이 나가는지 본다.

def _run_helper(tmp_path, state, enabled="yes", iface="mlan0"):
    """가짜 systemctl 을 물려 헬퍼를 돌리고, 발행된 job 호출 목록을 돌려준다."""
    fake = tmp_path / "systemctl"
    fake.write_text(
        "#!/bin/bash\n"
        "case \"$1\" in\n"
        "  is-active) echo \"$FAKE_STATE\"; [ \"$FAKE_STATE\" = active ] && exit 0 || exit 3 ;;\n"
        "  is-enabled) [ \"$FAKE_ENABLED\" = yes ] && exit 0 || exit 1 ;;\n"
        "  *) echo \"$*\" >> \"$CALLS\"; exit 0 ;;\n"
        "esac\n"
    )
    fake.chmod(0o755)
    noop = tmp_path / "logger"
    noop.write_text("#!/bin/bash\nexit 0\n")
    noop.chmod(0o755)

    src = HELPER.read_text(encoding="utf-8")
    patched = (src.replace("SYSTEMCTL=/usr/bin/systemctl", f"SYSTEMCTL={fake}")
                  .replace("LOGGER=/usr/bin/logger", f"LOGGER={noop}"))
    assert f"SYSTEMCTL={fake}" in patched, "SYSTEMCTL 상수 라인을 찾지 못함(형식 변경 시 갱신)"
    script = tmp_path / "helper.sh"
    script.write_text(patched)
    script.chmod(0o755)

    calls = tmp_path / "calls.txt"
    calls.write_text("")
    env = dict(os.environ, CALLS=str(calls), FAKE_STATE=state, FAKE_ENABLED=enabled)
    r = subprocess.run(["bash", str(script), iface], capture_output=True, text=True,
                       timeout=30, env=env)
    return r.returncode, [ln for ln in calls.read_text().splitlines() if ln.strip()]


@pytest.mark.parametrize("state", ["active", "activating"])
def test_helper_restarts_zombie(tmp_path, state):
    """좀비 라운드 — 프로세스가 구 ifindex 에 붙은 채 살아 있으면 restart 로 재부착."""
    rc, calls = _run_helper(tmp_path, state)
    assert rc == 0
    assert any("restart" in c and "--no-block" in c for c in calls), calls
    assert not any("reset-failed" in c for c in calls), "살아있는 유닛에 reset-failed 불필요"


def test_helper_revives_failed_unit(tmp_path):
    """[핵심] StartLimit 을 소진해 failed 로 굳은 유닛을 되살린다.

    failed 는 systemd 기준 "not running" 이라 try-restart/restart 가 모두 no-op 이고
    (실측), reset-failed 만으로도 inactive 가 될 뿐이라 start 가 필요하다. 이 분기가
    없으면 netdev 복구가 RestartSec x StartLimitBurst 를 넘길 때 supplicant 가 영구
    failed 에 갇힌다.
    """
    rc, calls = _run_helper(tmp_path, "failed")
    assert rc == 0
    joined = " ; ".join(calls)
    assert "reset-failed" in joined, f"reset-failed 없음: {calls}"
    assert any("start" in c and "--no-block" in c for c in calls), f"start 없음: {calls}"
    reset_i = next(i for i, c in enumerate(calls) if "reset-failed" in c)
    start_i = next(i for i, c in enumerate(calls) if "start" in c)
    assert reset_i < start_i, (
        f"reset-failed 가 start 보다 뒤에 있다 — StartLimit 이 안 지워진 채 start 한다: {calls}"
    )


def test_helper_does_not_revive_disabled_unit(tmp_path):
    """failed 분기만 start 를 쓰므로 여기서만 운영자 의도를 되살릴 위험이 있다.

    `.mlanN.wpa_supplicant.enabled=false` 로 disable 된 유닛은 failed 여도 그대로 둔다.
    """
    rc, calls = _run_helper(tmp_path, "failed", enabled="no")
    assert rc == 0
    assert calls == [], f"꺼둔 유닛을 되살렸다: {calls}"


@pytest.mark.parametrize("state", ["inactive", "deactivating"])
def test_helper_noop_when_not_running(tmp_path, state):
    """부팅 첫 add(아직 미기동), 운영자 정지, 재로드 중 의도적 stop — 모두 건드리지 않는다."""
    rc, calls = _run_helper(tmp_path, state)
    assert rc == 0
    assert calls == [], f"{state} 상태를 건드렸다: {calls}"


@pytest.mark.parametrize("bad", ["eth0", "", "mlan0; rm -rf /", "../etc"])
def test_helper_rejects_non_mlan_interface(tmp_path, bad):
    """udev 가 넘기는 %k 외 값으로 호출돼도 엉뚱한 유닛을 건드리지 않는다."""
    rc, calls = _run_helper(tmp_path, "active", iface=bad)
    assert rc == 1, f"거부되지 않았다: rc={rc}"
    assert calls == [], f"거부해야 하는데 호출이 나갔다: {calls}"


# ── 계층 A: 유닛 Restart 정책 ───────────────────────────────────────────────

def test_wpa_unit_has_supervision_contract():
    """죽는 라운드를 덮는다. 값은 이 리포의 감독 계약(wifi_link_snapshot@ 등)과 동일."""
    text = WPA_UNIT.read_text(encoding="utf-8")
    for token in ("Restart=always", "RestartSec=3",
                  "StartLimitIntervalSec=300", "StartLimitBurst=10"):
        assert token in text, f"{token} 없음"
    assert "StartLimitIntervalSec=0" not in text, "StartLimit 무력화됨"


def test_wpa_unit_keeps_requires_not_bindsto():
    """`BindsTo=` 를 붙이면 두 계층이 함께 무너진다.

    BindsTo 는 device 제거 시 유닛을 **정지**시킨다. 정지는 명시적 중단이라 `Restart=`
    가 발동하지 않고, 이어지는 add 에서 `try-restart` 는 비활성 유닛을 만나 no-op 이
    된다 — supplicant 가 영영 돌아오지 않는다. 형제 유닛들이 BindsTo 를 쓴다고 해서
    여기에 따라 붙이면 안 된다.
    """
    text = WPA_UNIT.read_text(encoding="utf-8")
    assert "Requires=sys-subsystem-net-devices-%i.device" in text
    assert "BindsTo=" not in text, (
        "BindsTo= 가 추가됐다 — try-restart 계층이 무력해진다(주석 참조)"
    )


# ── 전제조건: 모듈 재로드 경로가 Restart= 와 충돌하지 않을 것 ────────────────

def test_module_reload_stops_wpa_via_systemd_before_kill():
    """`Restart=always` 의 전제조건.

    wifi_init.sh 의 모듈 재로드 경로는 wpa 를 죽인 뒤 rmmod 한다. `kill -9` 만 하면
    systemd 가 실패로 보고 RestartSec 뒤 재기동하고, 그 프로세스가 rmmod 창에서 mlan 을
    다시 점유하면 rmmod 실패 → `exit 1` → wifi_init.service 의
    `OnFailure=wlan_emergency_reboot.service` 로 이어진다. systemctl stop 은 명시적
    정지라 systemd 가 재기동하지 않는다.
    """
    text = WIFI_INIT.read_text(encoding="utf-8")
    stop_idx = text.find("systemctl stop wpa_supplicant@mlan0.service")
    kill_idx = text.find("kill -9 $wpa_pids")
    assert stop_idx != -1, "모듈 재로드 전 systemctl stop 이 없다 — rmmod 창에서 되살아난다"
    assert kill_idx != -1, "kill 폴백이 사라졌다(형식 변경 시 테스트 갱신)"
    assert stop_idx < kill_idx, "systemctl stop 이 kill -9 보다 뒤에 있다 — 순서가 무의미"
    # mlan0 만 확인하면 mlan1 을 빼도 통과한다. DBDC 보조 STA 는 checker/event 커버리지가
    # 없어(핸드오프 §1) 이 stop 이 빠지면 rmmod 창에서 되살아나는 경로가 그대로 열린다.
    # 고정 바이트 창은 명령이 재포맷되면 false negative 를 낸다 — 논리 행(백슬래시 연결
    # 포함) 전체를 본다.
    assert "wpa_supplicant@mlan1.service" in _logical_line(text, stop_idx), (
        "systemctl stop 이 mlan1 을 포함하지 않는다"
    )


def test_module_reload_stop_keeps_stderr():
    """실패 "이유"를 지우지 않는다.

    `2>/dev/null` 을 붙이면 logger 가 "failed" 사실만 남기고 원인(DBus 불통/권한/유닛
    로드 실패)은 영구 소실된다. 이 스크립트는 wifi_init.service 하에서 돌아 stderr 가
    그대로 journald 에 수집되므로 버릴 이유가 없다.
    """
    text = WIFI_INIT.read_text(encoding="utf-8")
    stop_idx = text.find("systemctl stop wpa_supplicant@mlan0.service")
    assert stop_idx != -1
    assert "2>/dev/null" not in text[stop_idx:stop_idx + 200], (
        "systemctl stop 의 stderr 를 버린다 — 실패 이유가 사라진다"
    )


def test_stop_wait_is_bounded():
    """`systemctl stop` 은 동기이고 systemctl 에는 --timeout 옵션이 없다.

    모듈 재로드 경로가 rmmod 전에 stop 으로 대기하므로, 유닛에 상한이 없으면
    DefaultTimeoutStopSec(통상 90s)까지 재로드 전체가 멈춘다. 상한은 유닛에 둘 수밖에
    없다 — `systemctl stop --timeout=N` 은 존재하지 않는 옵션이라 그대로 쓰면 명령이
    통째로 실패하고 `|| true` 가 삼켜, wpa 가 아예 정지되지 않는 회귀가 된다.
    """
    text = WPA_UNIT.read_text(encoding="utf-8")
    m = re.search(r"^TimeoutStopSec=(\d+)", text, re.M)
    assert m, "TimeoutStopSec 미설정 — 재로드가 기본 90초까지 지연될 수 있다"
    assert 1 <= int(m.group(1)) <= 30, f"상한이 비현실적이다: {m.group(1)}s"
    assert "--timeout" not in WIFI_INIT.read_text(encoding="utf-8"), (
        "systemctl 에 없는 --timeout 옵션이 쓰였다 — 명령 전체가 실패한다"
    )


def test_critical_path_loggers_cannot_abort_script():
    """`set -euo pipefail` + 맨 logger = 임계 구간이 통째로 건너뛰어질 수 있다.

    stop/kill 앞 첫 문장에서 logger 가 실패하면(syslog 미기동 등) 스크립트가 거기서
    끊겨 systemctl stop 과 kill -9 폴백이 **둘 다** 실행되지 않고, wpa 가 살아있는 채로
    rmmod 창에 진입한다 → rmmod 실패 → exit 1 → OnFailure=wlan_emergency_reboot.
    파일 전체는 맨 logger 가 관례지만 이 구간만 예외로 `|| true` 를 요구한다.
    """
    text = WIFI_INIT.read_text(encoding="utf-8")
    assert "set -euo pipefail" in text, "set -e 전제가 바뀌었다(테스트 갱신 필요)"
    # 실패 폴백(`cmd || logger ...`)이 특히 위험하다 — cmd 가 실패한 뒤 logger 도 실패하면
    # 표현식 전체가 non-zero 라 set -e 가 발동해 kill -9 폴백까지 건너뛴다.
    for marker in ("stopping wpa_supplicant@mlan0/mlan1 via systemctl",
                   "systemctl not found; relying on kill fallback before rmmod",
                   "systemctl stop wpa_supplicant@ failed; relying on kill fallback"):
        line = next((ln for ln in text.splitlines() if marker in ln), None)
        assert line is not None, f"로그 문구가 사라졌다: {marker}"
        assert line.rstrip().endswith("|| true"), (
            f"임계 구간 logger 에 `|| true` 가 없다 — set -e 로 stop/kill 이 건너뛰어진다: {marker}"
        )


# ── 배포 즉시 적용 ──────────────────────────────────────────────────────────

def test_postinst_reloads_udev_rules():
    """재로드가 없으면 신규 rule 이 다음 재부팅까지 적용되지 않는다.

    옵션은 canonical form 인 `--reload` 를 쓴다 — `--reload-rules` 는 동작하는
    하위호환 별칭이지만 `udevadm control --help` 와 man page 어디에도 문서화돼 있지 않다.
    """
    text = POSTINST.read_text(encoding="utf-8")
    assert "udevadm control --reload" in text, (
        "postinst 가 udev rule 을 재로드하지 않는다 — 배포해도 재부팅 전까지 무효"
    )
    assert "--reload-rules" not in text, (
        "문서화되지 않은 별칭 --reload-rules 를 쓴다 — canonical form 은 --reload"
    )


def test_postinst_does_not_swallow_reload_failure():
    """재로드 실패는 조용히 넘기면 안 된다.

    실패하면 rule 이 재부팅까지 무효인데, 그 사실이 어디에도 남지 않으면 "배포했는데 왜
    안 고쳐졌나"를 추적할 수 없다. 설치 자체는 막지 않되(재부팅하면 적용) 로그는 남긴다.
    """
    text = POSTINST.read_text(encoding="utf-8")
    idx = text.find("udevadm control --reload")
    assert idx != -1
    window = text[idx:idx + 500]
    assert "logger" in window, "재로드 실패가 로그에 남지 않는다"
    assert "udevadm control --reload 2>/dev/null" not in text, (
        "stderr 를 버린다 — 패키지 관리자와 로그 양쪽에서 실패가 보이지 않는다"
    )
    # 재로드 실패 분기와 udevadm 부재 분기 **양쪽** 모두 stderr 폴백을 가져야 한다.
    # syslog 미기동 환경(컨테이너 등)에서는 logger 가 조용히 실패해 한쪽만 있으면
    # 그 분기의 경고가 통째로 사라진다.
    block = text[max(0, idx - 800):idx + 800]
    for msg in ("udevadm control --reload failed", "udevadm not found"):
        lines = [ln for ln in block.splitlines() if msg in ln]
        assert any("logger" in ln for ln in lines), f"{msg}: logger 경고가 없다"
        assert any("printf" in ln and ">&2" in ln for ln in lines), (
            f"{msg}: stderr 폴백(printf >&2)이 없다 — syslog 미기동 환경에서 경고가 사라진다"
        )


def test_module_reload_logs_stop_attempt():
    """kill 폴백 앞의 graceful stop 시도가 무기록이면 사후 추적이 끊긴다.

    주변 `logger` 개수를 세면 kill 블록의 로그에 가려 통과하므로, 이 단계에서만 나오는
    문구를 직접 확인한다.
    """
    text = WIFI_INIT.read_text(encoding="utf-8")
    assert "stopping wpa_supplicant@mlan0/mlan1 via systemctl" in text, (
        "graceful stop 시도 로그가 없다"
    )
    assert "systemctl stop wpa_supplicant@ failed" in text, (
        "stop 실패 로그가 없다 — 왜 kill 폴백까지 갔는지 알 수 없다"
    )


def test_postinst_does_not_trigger_udev():
    """`udevadm trigger` 는 금지다.

    add 이벤트를 재생하면 이 rule 이 **현재 붙어 있는** mlan* 에 try-restart 를 걸어,
    패키지 설치만으로 무선이 끊긴다. 재로드는 규칙만 갱신하고 다음 실제 netdev
    이벤트부터 적용된다.
    """
    text = POSTINST.read_text(encoding="utf-8")
    assert "udevadm trigger" not in text, (
        "udevadm trigger 가 추가됐다 — 설치 중 무선이 끊긴다"
    )


# ── 패키징 게이트 ───────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "manifest,entry",
    [
        (PAYLOAD_MANIFEST, "etc/udev/rules.d/99-wlan-wpa-reattach.rules"),
        (SOURCE_MANIFEST, "dist/wlan/etc/udev/rules.d/99-wlan-wpa-reattach.rules"),
        (PAYLOAD_MANIFEST, "usr/local/scripts/wlan_wpa_reattach.sh"),
        (SOURCE_MANIFEST, "dist/wlan/usr/local/scripts/wlan_wpa_reattach.sh"),
    ],
)
def test_rule_is_registered_in_manifests(manifest, entry):
    """payload 는 미등록 시 빌드가 깨지고, source 는 조용히 누락된다 — 둘 다 고정한다."""
    lines = manifest.read_text(encoding="utf-8").splitlines()
    assert entry in lines, f"{manifest.name} 에 {entry} 미등록"
    assert lines == sorted(lines), f"{manifest.name} 가 LC_ALL=C 정렬이 아니다"
