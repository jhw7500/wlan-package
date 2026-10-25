"""#381: Reset Cause 값은 wlan-opc protocol/ids.h 가 정본이다.

통신 사양서 7.6 은 고객에게 "최하위 바이트의 상위 니블로 분류(0x0x 운영자·시스템, 0x1x 무선,
0x2x 환경, 0x3x 초기화, 0x4x 유지보수)하고 한 번 쓴 값은 다른 의미로 다시 쓰지 않는다"고 약속했다.
이 테스트는 그 정본과, 값을 실제로 쓰는 스크립트(wlan_reboot_policy.sh 의 reset_cause_id(),
factory_reset.sh)가 서로 맞는지, 각 값이 이름에 맞는 분류에 속하는지 검사한다.

wlan-opc 서브모듈 체크아웃이 필요하다(`git submodule update --init wlan-opc`). CI source gate 는
공개 wlan-opc 를 받아 이 테스트를 돌리므로, 없으면 건너뛰지 않고 실패한다.
"""
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[6]
IDS_H = REPO / "wlan-opc/protocol/ids.h"
SCRIPTS = REPO / "dist/wlan/usr/local/scripts"
POLICY = SCRIPTS / "wlan_reboot_policy.sh"
FACTORY = SCRIPTS / "factory_reset.sh"

# 이름 → 분류(상위 니블). 새 원인을 ids.h 에 더하면 여기에도 분류를 정해 넣어야 통과한다.
CATEGORY = {
    "USER": 0x0,
    "SYSTEM": 0x0,
    "WLAN_LINK_LOST": 0x1,
    "WLAN_STATION_FAULT": 0x1,
    "WLAN_DRIVER_WEDGE": 0x1,
    "OVERTEMP": 0x2,
    "INIT_FAILURE": 0x3,
    "FACTORY_RESET": 0x4,
}

# wlan_reboot_policy.sh 에 넘기는 (--source, --reason) → 써야 하는 ids.h 이름.
POLICY_CASES = [
    ("wifi_checker", "wifi_checker fatal: link", "WLAN_LINK_LOST"),
    ("wifi_checker", "wifi_checker fatal: fw_crash", "WLAN_LINK_LOST"),
    ("wifi_checker", "wifi_checker fatal: station_dump_fault", "WLAN_STATION_FAULT"),
    ("wlan_fw_watch", "wlan_fw_watch fatal: wifi_status=11 terminal", "WLAN_DRIVER_WEDGE"),
    ("wifi_logger_temp", "overtemp recovered -> reboot", "OVERTEMP"),
    ("wifi_init", "wifi_init persistent failure", "INIT_FAILURE"),
]


def _ids_causes():
    if not IDS_H.is_file():
        pytest.fail(f"{IDS_H} not found: run `git submodule update --init wlan-opc`")
    text = IDS_H.read_text(encoding="utf-8")
    found = re.findall(r"^#define\s+OPC_RESET_CAUSE_([A-Z0-9_]+)\s+(0x[0-9A-Fa-f]+)\b", text, re.M)
    causes = {name: int(value, 16) for name, value in found}
    assert len(causes) == len(found), "an OPC_RESET_CAUSE_ name is defined twice"
    assert causes, "no OPC_RESET_CAUSE_ definitions found in ids.h"
    return causes


def _reset_cause_id_body():
    text = POLICY.read_text(encoding="utf-8")
    match = re.search(r"^reset_cause_id\(\) \{\n.*?^\}\n", text, re.M | re.S)
    assert match, "reset_cause_id() not found in wlan_reboot_policy.sh"
    return match.group(0)


def test_every_reset_cause_has_its_category():
    causes = _ids_causes()
    assert set(causes) == set(CATEGORY), (
        "ids.h Reset Causes and this test's category table differ — give a new cause a new value "
        "in its category and add it here"
    )
    for name, value in causes.items():
        assert value & ~0xFF == 0, f"{name}=0x{value:08x} uses bits above the lowest byte"
        assert value >> 4 == CATEGORY[name], (
            f"{name}=0x{value:08x} is not in category 0x{CATEGORY[name]:x}x"
        )


def test_reset_cause_values_are_unique():
    causes = _ids_causes()
    assert len(set(causes.values())) == len(causes), "two Reset Causes share a value"


@pytest.mark.parametrize("source, reason, name", POLICY_CASES)
def test_policy_writes_the_ids_value(source, reason, name):
    causes = _ids_causes()
    result = subprocess.run(
        ["bash", "-c", _reset_cause_id_body() + 'SOURCE=$1 REASON=$2 reset_cause_id\n',
         "reset_cause_id", source, reason],
        capture_output=True, text=True, timeout=10, check=True,
    )
    assert int(result.stdout.strip(), 16) == causes[name]


def test_policy_emits_only_ids_values():
    values = set(_ids_causes().values())
    emitted = re.findall(r"echo\s+(0x[0-9A-Fa-f]+)", _reset_cause_id_body())
    assert emitted, "reset_cause_id() emits no value"
    for value in emitted:
        assert int(value, 16) in values, f"reset_cause_id() emits {value}, not defined in ids.h"


def test_factory_reset_writes_the_ids_value():
    text = FACTORY.read_text(encoding="utf-8")
    writes = re.findall(r"printf '(0x[0-9A-Fa-f]+)\\n'[^\n]*>\s*/run/opc/reset_cause", text)
    assert len(writes) == 1, f"expected one Reset Cause write in factory_reset.sh, found {writes}"
    assert int(writes[0], 16) == _ids_causes()["FACTORY_RESET"]
