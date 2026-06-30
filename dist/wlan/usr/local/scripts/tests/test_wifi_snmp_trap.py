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

def test_link_up_varbinds(tmp_path):
    r = _run(["link", "up"], '{"snmp":{"trap":{"enabled":true,"dest":"10.0.0.9","community":"public","version":"2c"}}}', tmp_path)
    out = r.stdout.strip()
    assert "snmptrap -v2c -c public 10.0.0.9" in out
    assert ".1.3.6.1.4.1.672.65.1.1.1" in out              # trap-OID
    assert ".1.3.6.1.4.1.672.65.3.2.1.1.2 i 2" in out       # IfIndex=2(무선)
    assert ".1.3.6.1.4.1.672.65.3.2.1.7.2 i 1" in out       # IfLinkStatus=up

def test_link_down_status_2(tmp_path):
    r = _run(["link", "down"], '{"snmp":{"trap":{"enabled":true,"dest":"10.0.0.9"}}}', tmp_path)
    assert ".1.3.6.1.4.1.672.65.3.2.1.7.2 i 2" in r.stdout  # down

def test_link_bad_arg_noop(tmp_path):
    r = _run(["link", "bogus"], '{"snmp":{"trap":{"enabled":true,"dest":"10.0.0.9"}}}', tmp_path)
    assert "snmptrap" not in r.stdout
