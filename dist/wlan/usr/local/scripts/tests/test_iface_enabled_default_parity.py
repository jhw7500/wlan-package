"""`.mlanN.enabled` 폴백 해석을 wifi_init.sh 와 wifi_apply_enabled.sh 가 동일하게 볼 것.

두 스크립트는 같은 키로 서로 다른 일을 한다 — wifi_apply_enabled.sh 는 systemd unit 을
enable/disable 하고, wifi_init.sh 는 radio setup 과 supplicant 직접 start 를 결정한다.
해석이 갈리면 "유닛은 disable 인데 wifi_init 이 start 하는" 모순 상태가 되므로 규약을
맞춘다:

  - 설정을 읽을 수 있는데 키가 없거나 해석 불가  → false
  - 설정 파일 자체가 없거나 jq 부재              → 기동 유지(양쪽 공통)

두 번째 줄이 중요하다. 그건 "인터페이스를 껐다"가 아니라 "설정이 없다"이고, 여기서
false 로 떨어뜨리면 config 를 잃은 기기가 무선까지 잃어 원격 복구가 끊긴다.
wifi_apply_enabled.sh 는 이 경우 apply 자체를 건너뛰어(파일 부재/jq 부재/파싱 실패)
종전 enable 상태를 그대로 두므로, wifi_init.sh 도 기동을 유지해야 짝이 맞는다.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WLAN_ROOT = Path(__file__).resolve().parents[4]
INIT_SH = WLAN_ROOT / "usr/local/scripts/wifi_init.sh"
APPLY_SH = WLAN_ROOT / "usr/local/scripts/wifi_apply_enabled.sh"
CONFIG_LIB = WLAN_ROOT / "usr/local/scripts/wifi_init_config_lib.sh"
TEMPLATE = WLAN_ROOT / "opt/wlan/config/wifi_init_conf.json"

needs_jq = pytest.mark.skipif(shutil.which("jq") is None, reason="jq 미설치")


def test_wifi_init_has_no_hardcoded_iface_enabled_default():
    """wifi_init.sh 의 모든 호출이 공통 변수를 쓰는지 — 한 곳이라도 리터럴로 되돌아가면
    그 경로만 조용히 옛 규약(부재=true)으로 갈라진다."""
    src = INIT_SH.read_text()
    calls = re.findall(
        r"wifi_init_(?:get_iface_enabled|iface_is_enabled)\s+\"[^\"]+\"\s+\"([^\"]*)\"",
        src,
    )
    assert calls, "호출을 찾지 못함(호출 형식 변경 시 테스트 갱신)"
    bad = [c for c in calls if c != "$IFACE_ENABLED_DEFAULT"]
    assert not bad, (
        f"wifi_init.sh 에 리터럴 기본값이 남았다: {bad} — "
        "$IFACE_ENABLED_DEFAULT 를 쓸 것"
    )


def test_iface_enabled_default_is_false_unless_config_unreadable():
    """공통 변수의 기본은 false 이고, 설정을 못 읽을 때만 true 로 올라간다."""
    src = INIT_SH.read_text()
    assert "IFACE_ENABLED_DEFAULT=false\n" in src, "기본값이 false 가 아니다"
    guard = re.search(
        r"IFACE_ENABLED_DEFAULT=false\n"
        r"if ! wifi_init_conf_status \"\$WIFI_INIT_CONF_JSON\"; then\n"
        r"\s+IFACE_ENABLED_DEFAULT=true\n",
        src,
    )
    assert guard, (
        "설정 가용성 가드가 사라졌거나 공유 판정을 쓰지 않는다 — 설정을 못 읽는 기기가 "
        "양쪽 인터페이스를 모두 잃어 원격 복구가 끊긴다"
    )


def test_both_scripts_use_shared_conf_status():
    """가용성 판정은 한 곳(wifi_init_conf_status)만 존재해야 한다.

    각자 인라인으로 구현하면 사유 하나가 한쪽에만 반영된다 — 실제로 wifi_init.sh 의
    인라인 가드가 파일/jq 만 보고 **파싱 실패**를 놓쳐, 손상된 config 에서 양쪽
    인터페이스가 죽는 상태가 있었다.
    """
    assert "wifi_init_conf_status()" in CONFIG_LIB.read_text(), "공유 판정 함수가 없다"
    for sh in (INIT_SH, APPLY_SH):
        src = sh.read_text()
        assert "wifi_init_conf_status" in src, f"{sh.name} 이 공유 판정을 쓰지 않는다"
        assert 'jq empty "$JSON"' not in src, (
            f"{sh.name} 에 인라인 파싱 검사가 되살아났다 — wifi_init_conf_status 를 쓸 것"
        )


@needs_jq
@pytest.mark.parametrize(
    "kind,expected",
    [("ok", 0), ("missing", 1), ("corrupt", 3)],
)
def test_conf_status_codes(tmp_path, kind, expected):
    """호출자가 사유별 정책(warn/crit, skip/abort, 폴백값)을 정할 수 있어야 한다."""
    if kind == "ok":
        target = tmp_path / "c.json"
        target.write_text(TEMPLATE.read_text())
    elif kind == "missing":
        target = tmp_path / "nope.json"
    else:
        target = tmp_path / "c.json"
        target.write_text("{broken json")
    r = subprocess.run(
        ["bash", "-c", f'. "{CONFIG_LIB}"; wifi_init_conf_status "{target}"'],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == expected, f"{kind}: {r.returncode} (기대 {expected})"


@needs_jq
def test_corrupt_config_keeps_interfaces_up(tmp_path):
    """[락아웃 방지] 파싱이 깨진 config 에서도 인터페이스를 죽이지 않는다.

    손상 JSON 은 모든 키가 null 로 보이므로 '키 부재'와 구분되지 않는다. 여기서 기본값
    false 를 적용하면 mlan0/mlan1 이 함께 죽어 무선 복구 경로가 끊긴다 — 인라인 가드가
    파일/jq 만 보던 시절 실제로 발생하던 상태다.
    """
    p = tmp_path / "wifi_init_conf.json"
    p.write_text("{broken json")
    assert _init_side(p) == "true"


def test_apply_enabled_still_defaults_iface_to_false():
    """상대편이 바뀌면 통일이 깨진다 — 규약의 반대쪽도 고정한다."""
    src = APPLY_SH.read_text()
    assert 'get_bool ".${iface}.enabled" "false"' in src, (
        "wifi_apply_enabled.sh 의 .mlanN.enabled 기본값이 false 가 아니다 — "
        "wifi_init.sh 의 IFACE_ENABLED_DEFAULT 와 함께 맞출 것"
    )


def _init_guard_snippet():
    """wifi_init.sh 의 폴백 결정 블록을 **원문 그대로** 꺼내온다.

    재구현하면 스크립트가 바뀌어도 테스트는 자기 사본을 검증해 통과한다 — 실제로
    기본값을 true 로 뒤집는 변형에서 동작 테스트가 통과하는 것을 실측했다. 실물을
    실행해야 회귀를 잡는다.
    """
    src = INIT_SH.read_text()
    # 값이 아니라 변수명에 앵커한다 — 값에 앵커하면 기본값을 뒤집는 변형이 '동작 불일치'가
    # 아니라 '추출 실패'로 터져, 정작 무엇이 깨졌는지 알려주지 못한다.
    marker = "\nIFACE_ENABLED_DEFAULT="
    assert marker in src, "IFACE_ENABLED_DEFAULT 정의를 찾지 못함(형식 변경 시 갱신)"
    start = src.index(marker) + 1
    end = src.index("\nMLAN0_ENABLED=", start)
    return src[start:end]


def _init_side(config_path):
    """wifi_init.sh 의 폴백 결정(원문) + lib 리더로 mlan0 판정을 돌려준다."""
    script = f'''
        set -euo pipefail
        . "{CONFIG_LIB}"
        WIFI_INIT_CONF_JSON="{config_path}"
{_init_guard_snippet()}
        wifi_init_get_iface_enabled "mlan0" "$IFACE_ENABLED_DEFAULT"
    '''
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _shell_fn(src, name):
    """셸 소스에서 함수 정의를 원문 그대로 꺼낸다(끝은 열 0 의 단독 `}` 행)."""
    head = f"{name}() {{\n"
    assert head in src, f"{name} 정의를 찾지 못함(형식 변경 시 테스트 갱신)"
    rest = src.split(head, 1)[1]
    end = rest.find("\n}\n")
    assert end != -1, f"{name} 의 종료 행을 찾지 못함"
    return head + rest[:end] + "\n}\n"


def _apply_helpers():
    """wifi_apply_enabled.sh 의 normalize_bool/get_bool 을 **실물 그대로** 꺼내온다.

    재현 사본을 실행하면 실제 스크립트의 판정이 바뀌어도 테스트는 자기 사본을 검증해
    통과한다 — 이 파일이 고정하려는 것이 바로 두 스크립트의 판정 일치이므로, 상대편을
    재구현하면 게이트가 무력해진다.
    """
    src = APPLY_SH.read_text()
    return _shell_fn(src, "normalize_bool") + _shell_fn(src, "get_bool")


def _apply_side(config_path):
    """wifi_apply_enabled.sh 의 get_bool(원문)으로 mlan0 판정을 돌려준다."""
    script = f'''
        set -u
        JSON="{config_path}"
{_apply_helpers()}
        get_bool ".mlan0.enabled" "false"
    '''
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


@needs_jq
@pytest.mark.parametrize(
    "value,expected",
    [
        (True, "true"),
        (False, "false"),
        ("__ABSENT__", "false"),
        (None, "false"),
        ("maybe", "false"),
        (1, "true"),
        ("off", "false"),
        (0, "false"),
    ],
)
def test_both_scripts_agree_on_iface_enabled(tmp_path, value, expected):
    conf = json.loads(TEMPLATE.read_text())
    if value == "__ABSENT__":
        conf["mlan0"].pop("enabled", None)
    else:
        conf["mlan0"]["enabled"] = value
    p = tmp_path / "wifi_init_conf.json"
    p.write_text(json.dumps(conf))

    init = _init_side(p)
    apply_ = _apply_side(p)
    assert init == apply_, f"두 스크립트 해석 불일치: wifi_init={init} wifi_apply={apply_}"
    assert init == expected


@needs_jq
def test_missing_config_keeps_interfaces_up(tmp_path):
    """[락아웃 방지] 설정 파일이 없으면 인터페이스를 죽이지 않는다.

    wifi_apply_enabled.sh 는 이 경우 apply 를 건너뛰어 종전 enable 상태를 두므로,
    wifi_init.sh 가 false 로 떨어지면 혼자만 인터페이스를 죽여 무선 복구 경로가 끊긴다.
    """
    assert _init_side(tmp_path / "does_not_exist.json") == "true"
