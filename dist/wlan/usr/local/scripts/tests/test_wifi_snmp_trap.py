import os, subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "wifi_snmp_trap.sh"

def _run(args, conf_text, tmp_path, dryrun=True):
    conf = tmp_path / "wifi_init_conf.json"
    conf.write_text(conf_text)
    env = {**os.environ, "WIFI_INIT_CONF": str(conf),
           "WIFI_SNMP_TRAP_DRYRUN": "1" if dryrun else "0"}
    return subprocess.run(["sh", str(SCRIPT), *args],
                          env=env, capture_output=True, text=True)

def test_disabled_is_noop(tmp_path):
    r = _run(["link", "up"], '{"snmp":{"trap":{"enabled":false,"dest":"10.0.0.1"}}}', tmp_path)
    assert r.returncode == 0
    assert r.stdout.strip() == ""

def test_enabled_but_no_dest_is_noop(tmp_path):
    r = _run(["link", "up"], '{"snmp":{"trap":{"enabled":true,"dest":""}}}', tmp_path)
    assert r.returncode == 0
    assert "snmptrap" not in r.stdout

def test_missing_conf_is_noop(tmp_path):
    env = {**os.environ, "WIFI_INIT_CONF": str(tmp_path / "nope.json"),
           "WIFI_SNMP_TRAP_DRYRUN": "1"}
    r = subprocess.run(["sh", str(SCRIPT), "link", "up"], env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0
    assert r.stdout.strip() == ""
