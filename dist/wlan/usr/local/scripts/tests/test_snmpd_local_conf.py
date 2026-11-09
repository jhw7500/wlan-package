"""snmpd.local.conf keeps the SNMP values applied over FTP across package upgrades (#464).

postinst rewrites /etc/snmp/snmpd.conf from the template on every install, so the query
community and the sys* strings live in /etc/snmp/snmpd.local.conf, which snmpd reads after it.
"""

import re
import subprocess
from pathlib import Path

import pytest


DIST = Path(__file__).resolve().parents[4]
POSTINST = DIST / "DEBIAN/postinst"
FACTORY_RESET = DIST / "usr/local/scripts/factory_reset.sh"
MAIN_TEMPLATE = DIST / "opt/wlan/config/snmp/snmpd.conf"
LOCAL_TEMPLATE = DIST / "opt/wlan/config/snmp/snmpd.local.conf"
LOCAL = "/etc/snmp/snmpd.local.conf"
LOCAL_SOURCE = "/opt/wlan/config/snmp/snmpd.local.conf"
BEGIN, END = "  # >>> snmpd-local\n", "  # <<< snmpd-local\n"
TEMPLATE_LINE = b"rocommunity public  default\n"


def directives(path):
    """(name, rest) of every snmpd directive line; comments and blank lines are skipped."""
    found = []
    for line in path.read_text(encoding="utf-8").splitlines():
        words = line.split(None, 1)
        if words and not words[0].startswith("#"):
            found.append((words[0].lower(), words[1] if len(words) > 1 else ""))
    return found


def index_of(lines, predicate, what):
    found = [number for number, line in enumerate(lines) if predicate(line)]
    assert len(found) == 1, f"{what}: lines {found}"
    return found[0]


def postinst_lines():
    return POSTINST.read_text(encoding="utf-8").splitlines(keepends=True)


def test_the_query_community_moved_from_the_main_template_to_the_local_one():
    # A community left in snmpd.conf could never change: postinst rewrites that file, and
    # snmpd answers every rocommunity it reads, so the old one would keep working too.
    assert [entry for entry in directives(MAIN_TEMPLATE) if entry[0] == "rocommunity"] == []
    assert directives(LOCAL_TEMPLATE) == [("rocommunity", "public  default")]


def test_postinst_writes_the_local_file_before_it_restarts_snmpd():
    # The new snmpd.conf has no community, so an snmpd restarted before the local file
    # exists answers no query (no access control information) until it restarts again.
    lines = postinst_lines()
    main_copy = index_of(lines, lambda line: line.strip() == "cp /opt/wlan/config/snmp/snmpd.conf /etc/snmp/snmpd.conf",
                         "snmpd.conf copy")
    begin = index_of(lines, lambda line: line == BEGIN, "block start")
    end = index_of(lines, lambda line: line == END, "block end")
    restart = index_of(lines, lambda line: line.strip().startswith("systemctl try-restart snmpd"), "try-restart")
    assert main_copy < begin < end < restart


def test_cpchk_is_never_called_before_it_is_defined():
    # postinst has no set -e: a call before the definition only prints "command not found".
    lines = postinst_lines()
    definition = index_of(lines, lambda line: line.startswith("cpchk() {"), "cpchk definition")
    assert [number + 1 for number, line in enumerate(lines[:definition]) if re.match(r"\s*cpchk\s", line)] == []


def run_block(tmp_path, before):
    """Run postinst's snmpd-local block on a temporary target; BEFORE None means no file."""
    lines = postinst_lines()
    block = "".join(lines[lines.index(BEGIN):lines.index(END) + 1])
    # Both paths must be in the block, or the replacement below would test nothing.
    assert LOCAL in block and LOCAL_SOURCE in block
    target = tmp_path / "snmpd.local.conf"
    if before is not None:
        target.write_bytes(before)
    script = block.replace(LOCAL, str(target)).replace(LOCAL_SOURCE, str(LOCAL_TEMPLATE))
    result = subprocess.run(["bash", "-c", "logger() { :; }\ntag=postinst\nKEY=PKG\n" + script],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    return target.read_bytes() if target.exists() else None


@pytest.mark.parametrize("before", [None, b""])
def test_a_missing_or_empty_local_file_gets_the_template(tmp_path, before):
    assert run_block(tmp_path, before) == LOCAL_TEMPLATE.read_bytes()


@pytest.mark.parametrize("before", ["syslocation 서울\nrocommunity Secret-1 192.0.2.0/24\n".encode(),
                                    b"ROCOMMUNITY x default\n", b"\trocommunity y default"])
def test_a_local_file_with_a_community_is_kept_byte_for_byte(tmp_path, before):
    assert run_block(tmp_path, before) == before


@pytest.mark.parametrize("before", [b"syslocation Room 1\n", b"syslocation Room 1",
                                    b"rocommunity6 public default\n", b"# rocommunity public default\n"])
def test_a_local_file_without_a_community_gets_the_template_line(tmp_path, before):
    # The template told operators to put sys* values in this file; the community that used to
    # come from snmpd.conf is added so such a device keeps answering queries after the upgrade.
    expected = before + (b"" if before.endswith(b"\n") else b"\n") + TEMPLATE_LINE
    assert run_block(tmp_path, before) == expected


def test_factory_reset_restores_the_local_file_before_it_reboots():
    lines = FACTORY_RESET.read_text(encoding="utf-8").splitlines()
    restore = index_of(lines, lambda line: line.strip() == f"safe_cp {LOCAL_SOURCE} {LOCAL}", "restore")
    reboot = index_of(lines, lambda line: line.strip() == "if ! systemctl reboot; then", "reboot")
    assert restore < reboot
