"""#457: the net_rx section of docs/wifi_init_conf_guide.md matches the units and scripts
it describes -- the flush timer and its output file, the bus-dependent mod_para block and
the handling of a zero value.  Each sentence is checked against the source line it states.
"""

from __future__ import annotations

from pathlib import Path

WLAN_ROOT = Path(__file__).resolve().parents[4]
REPO_ROOT = WLAN_ROOT.parents[1]
GUIDE = REPO_ROOT / "docs/wifi_init_conf_guide.md"
SYSTEMD = WLAN_ROOT / "etc/systemd/system"
INIT = WLAN_ROOT / "usr/local/scripts/wifi_init.sh"
MGMT_LOG = WLAN_ROOT / "usr/local/scripts/wifi_mgmt_log.sh"


def _net_rx_section() -> str:
    text = GUIDE.read_text()
    start = text.index("#### net_rx 비트맵")
    end = text.index("#### STANDARD → wifi_mod_para.conf 매핑", start)
    return text[start:end]


def test_flush_unit_and_output_file_match_the_shipped_timer_and_script() -> None:
    section = _net_rx_section()
    timer = (SYSTEMD / "wifi_mgmt_log.timer").read_text().splitlines()
    service = (SYSTEMD / "wifi_mgmt_log.service").read_text().splitlines()
    script = MGMT_LOG.read_text()

    assert "`wifi_mgmt_log.timer`" in section
    assert "Unit=wifi_mgmt_log.service" in timer
    assert "ExecStart=/usr/local/scripts/wifi_mgmt_log.sh" in service
    assert "`/var/log/cantops/mgmt/mlanX/mgmt.log`" in section
    assert 'LOG_BASE="/var/log/cantops/mgmt"' in script
    assert 'local log_file="${log_dir}/mgmt.log"' in script
    for stale in ("mgmt-log-flush.timer", "gmgmt.log"):
        assert stale not in section


def test_block_mapping_lists_both_bus_prefixes_the_init_script_selects() -> None:
    section = _net_rx_section()
    init = INIT.read_text()

    assert "_bus=$(jq -r '.global.BUS_TYPE // \"pcie\"'" in init
    assert 'if [ "$_bus" = "sdio" ]; then blk_prefix="SD9098"; else blk_prefix="PCIE9098"; fi' in init
    assert "`.global.BUS_TYPE`" in section
    assert "| `.mlan0.net_rx` | `PCIE9098_0` | `SD9098_0` |" in section
    assert "| `.mlan1.net_rx` | `PCIE9098_1` | `SD9098_1` |" in section


def test_zero_value_is_written_and_only_the_mfg_profile_removes_the_key() -> None:
    section = _net_rx_section()
    init = INIT.read_text()

    assert '_set_kv_in_block "${blk_prefix}_0" "net_rx"' in init
    assert '_del_kv_in_block "$_blk" "net_rx"' in init
    assert "`net_rx=0`" in section
    assert "MFG 프로파일" in section
    assert "값이 0이면 conf에서 `net_rx=` 줄이 제거되고" not in section
