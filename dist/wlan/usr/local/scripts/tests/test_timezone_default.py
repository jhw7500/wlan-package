"""The time zone set with quote wconfig TIMEZONE survives package upgrades (#464).

The zone lives only in /etc/localtime.  postinst sets the default zone on the first install,
which it tells by the settings JSON that json_merge creates later in the same run, and leaves
the zone alone on every later install.  Factory reset puts the default back.
"""

import subprocess
from pathlib import Path


DIST = Path(__file__).resolve().parents[4]
POSTINST = DIST / "DEBIAN/postinst"
FACTORY_RESET = DIST / "usr/local/scripts/factory_reset.sh"
ACTIVE_JSON = "/usr/local/etc/wifi_init_conf.json"
BEGIN, END = "  # >>> timezone-default\n", "  # <<< timezone-default\n"
SET_DEFAULT = "timedatectl set-timezone Asia/Seoul"


def index_of(lines, predicate, what):
    found = [number for number, line in enumerate(lines) if predicate(line)]
    assert len(found) == 1, f"{what}: lines {found}"
    return found[0]


def postinst_lines():
    return POSTINST.read_text(encoding="utf-8").splitlines(keepends=True)


def test_postinst_sets_the_zone_only_in_the_default_block_before_the_safe_time():
    # set-time reads its argument in the zone in force, so the default zone comes first.
    lines = postinst_lines()
    begin = index_of(lines, lambda line: line == BEGIN, "block start")
    end = index_of(lines, lambda line: line == END, "block end")
    zone_sets = [number for number, line in enumerate(lines) if "set-timezone" in line]
    assert zone_sets and all(begin < number < end for number in zone_sets)
    set_time = index_of(lines, lambda line: "timedatectl set-time " in line, "set-time")
    merge = index_of(lines, lambda line: line.strip().startswith(f"json_merge /opt/wlan/config/wifi_init_conf.json {ACTIVE_JSON}"),
                     "json_merge call")
    assert end < set_time < merge


def run_block(tmp_path, json_exists):
    """Run postinst's timezone-default block with a recording timedatectl; returns its calls."""
    lines = postinst_lines()
    block = "".join(lines[lines.index(BEGIN):lines.index(END) + 1])
    # The path must be in the block, or the replacement below would test nothing.
    assert ACTIVE_JSON in block
    active = tmp_path / "wifi_init_conf.json"
    if json_exists:
        active.write_text("{}\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    record = tmp_path / "calls"
    stub = bin_dir / "timedatectl"
    stub.write_text(f'#!/bin/sh\necho "$*" >> "{record}"\n')
    stub.chmod(0o755)
    script = block.replace(ACTIVE_JSON, str(active))
    result = subprocess.run(["bash", "-c", "logger() { :; }\ntag=postinst\nKEY=PKG\n" + script],
                            capture_output=True, text=True, timeout=10,
                            env={"PATH": f"{bin_dir}:/usr/bin:/bin"})
    assert result.returncode == 0, result.stderr
    return record.read_text().splitlines() if record.exists() else []


def test_the_first_install_sets_the_default_zone(tmp_path):
    assert run_block(tmp_path, json_exists=False) == ["set-timezone Asia/Seoul"]


def test_a_later_install_keeps_the_zone_the_device_has(tmp_path):
    assert run_block(tmp_path, json_exists=True) == []


def test_factory_reset_sets_the_default_zone_before_it_reboots():
    lines = FACTORY_RESET.read_text(encoding="utf-8").splitlines()
    zone = index_of(lines, lambda line: line.strip().startswith(SET_DEFAULT), "set-timezone")
    reboot = index_of(lines, lambda line: line.strip() == "if ! systemctl reboot; then", "reboot")
    assert zone < reboot
