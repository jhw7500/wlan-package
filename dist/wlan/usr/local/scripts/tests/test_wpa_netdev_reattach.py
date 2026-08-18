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
import re
from pathlib import Path

import pytest

WLAN_ROOT = Path(__file__).resolve().parents[4]
REPO_ROOT = WLAN_ROOT.parents[1]

UDEV_RULE = WLAN_ROOT / "etc/udev/rules.d/99-wlan-wpa-reattach.rules"
ARP_SEAL_RULE = WLAN_ROOT / "etc/udev/rules.d/99-wlan-arp-seal.rules"
WPA_UNIT = WLAN_ROOT / "opt/wlan/config/wpa_supplicant/wpa_supplicant@.service"
WIFI_INIT = WLAN_ROOT / "usr/local/scripts/wifi_init.sh"
PAYLOAD_MANIFEST = WLAN_ROOT / "DEBIAN/payload-manifest.txt"
SOURCE_MANIFEST = REPO_ROOT / "scripts/source_archive_manifest.txt"


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


def test_rule_uses_try_restart_not_restart_or_start():
    """`try-restart` 는 대체 가능한 표현이 아니다.

    man systemctl: "Stop and then start ... **if the units are running**. This does
    nothing if units are not running."
      - 부팅 첫 add 에서 wpa 는 아직 미기동 → no-op(부팅 순서 무간섭)
      - 운영자가 `.mlanN.wpa_supplicant.enabled=false` 로 끈 경우 → 비활성 → no-op
    `restart`/`start` 로 바꾸면 두 경우 모두 유닛을 강제로 띄워 운영자 의도와 부팅
    순서를 덮어쓴다.
    """
    line = _rule_line()
    assert "try-restart" in line, "try-restart 가 아니다"
    assert not re.search(r"systemctl[^\"]*\brestart\b(?!-)", line.replace("try-restart", "")), (
        "try-restart 외의 restart 동사가 섞였다"
    )
    assert not re.search(r"systemctl[^\"]*\bstart\b", line.replace("try-restart", "")), (
        "start 동사가 섞였다 — 꺼둔 supplicant 를 되살린다"
    )


def test_rule_is_non_blocking():
    """`--no-block` 이 없으면 udev 워커가 멈춘다.

    man systemctl: 미지정 시 "systemctl will wait until the unit's start-up is
    completed". 게다가 wpa_supplicant@.service 는 지금 생성 중인
    sys-subsystem-net-devices-%i.device 를 Requires/After 로 걸어, 블로킹 호출은 그
    device 유닛을 기다린다. man 7 udev 는 RUN 파생 프로세스가 이벤트 처리 후
    "unconditionally killed" 된다고 명시하므로 그 대기는 작업을 반쯤 남기고 끊긴다.
    """
    assert "--no-block" in _rule_line(), "--no-block 없음 — udev 워커 블로킹"


def test_rule_uses_absolute_path_and_instance_name():
    """RUN 은 셸을 거치지 않으므로 절대 경로가 필요하고, 인스턴스는 %k 로 받는다."""
    line = _rule_line()
    m = re.search(r'RUN\+="(/[^ "]+)', line)
    assert m, "RUN+= 에 절대 경로 명령이 없다"
    assert m.group(1).endswith("/systemctl"), f"systemctl 이 아니다: {m.group(1)}"
    assert "wpa_supplicant@%k.service" in line, "인스턴스가 %k 로 확장되지 않는다"


def test_rule_sorts_after_arp_seal():
    """udev 는 파일명 사전순으로 처리한다 — 기존 netdev 훅 뒤에 오도록 유지한다."""
    assert ARP_SEAL_RULE.exists(), "전례 rule 이 사라졌다(경로 변경 시 테스트 갱신)"
    assert ARP_SEAL_RULE.name < UDEV_RULE.name


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


# ── 패키징 게이트 ───────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "manifest,entry",
    [
        (PAYLOAD_MANIFEST, "etc/udev/rules.d/99-wlan-wpa-reattach.rules"),
        (SOURCE_MANIFEST, "dist/wlan/etc/udev/rules.d/99-wlan-wpa-reattach.rules"),
    ],
)
def test_rule_is_registered_in_manifests(manifest, entry):
    """payload 는 미등록 시 빌드가 깨지고, source 는 조용히 누락된다 — 둘 다 고정한다."""
    lines = manifest.read_text(encoding="utf-8").splitlines()
    assert entry in lines, f"{manifest.name} 에 {entry} 미등록"
    assert lines == sorted(lines), f"{manifest.name} 가 LC_ALL=C 정렬이 아니다"
