"""Execute the init MAC/driver phase with real JSON and .link writes.

Only kernel, udev, logging and board-module verification cross fake OS
boundaries. The fake driver uses cached .link settings until udev is reloaded,
matching the stale-setting hypothesis; a separate override models apply failure.
"""

import json
import os
from pathlib import Path
import shlex
import subprocess

import pytest


SCRIPTS = Path(__file__).resolve().parents[1]
WLAN = SCRIPTS.parents[2]
INIT_UNIT = WLAN / "etc/systemd/system/wifi_init.service"
EMERGENCY_UNIT = WLAN / "etc/systemd/system/wlan_emergency_reboot.service"
WPA_UNIT = WLAN / "opt/wlan/config/wpa_supplicant/wpa_supplicant@.service"
PEER = "00:e0:4c:68:2b:1f"


def run_mac_phase(tmp_path, *, mode="static", override=None, reload_rc=0,
                  wait_rc=0, secondary_enabled=False, secondary_mac="",
                  mfg=False, blocked_write=False, help_rc=0):
    network = tmp_path / "network"
    sysfs = tmp_path / "net"
    bin_dir = tmp_path / "bin"
    for directory in (network, sysfs, bin_dir):
        directory.mkdir()
    calls = tmp_path / "calls"
    calls.write_text("")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"mac": {
        "mlan0": {"base": "", "target": PEER},
        "mlan1": {"base": secondary_mac}, "eth0": {"base": ""},
    }}))
    (network / "20-mlan0.link").write_text(
        "[Match]\nOriginalName=mlan0\n[Link]\nMACAddress=00:50:43:02:fe:01\n")
    if blocked_write:
        (network / "10-operator.link").write_text(
            f"[Match]\nOriginalName=other0\n[Link]\nMACAddress={PEER}\n")
    udev = bin_dir / "udevadm"
    udev.write_text("""#!/bin/bash
printf 'udev %s\\n' "$*" >> "$CALLS"
if [ "$*" = 'wait --help' ]; then exit "$HELP_RC"; fi
case "$1" in
    control)
        [ "$*" = 'control --timeout=5 --reload' ] || exit 90
        [ "$RELOAD_RC" = 0 ] || exit "$RELOAD_RC"
        touch "$SYSFS/reloaded" ;;
    wait)
        [ "$2" = '--timeout=5' ] || exit 91
        [ -d "$3" ] || exit 92
        exit "$WAIT_RC" ;;
    *) exit 93 ;;
esac
""")
    insmod = bin_dir / "insmod"
    insmod.write_text("""#!/bin/bash
printf 'insmod %s\\n' "$*" >> "$CALLS"
case "$1" in
    *moal*)
        for iface in mlan0 mlan1; do
            mkdir "$SYSFS/$iface"
            value=00:50:43:02:fe:01
            [ "$iface" != mlan1 ] || value=00:50:43:02:fe:02
            if [ -e "$SYSFS/reloaded" ]; then
                case "$iface" in
                    mlan0) link=20-mlan0.link ;;
                    mlan1) link=21-mlan1.link ;;
                esac
                configured=$(awk -F= '$1 == "MACAddress" {print $2}' "$SYSTEMD_NETWORK_DIR/$link" 2>/dev/null)
                [ -z "$configured" ] || value="$configured"
            fi
            if [ "$iface" = mlan0 ] && [ -n "$OVERRIDE" ]; then value="$OVERRIDE"; fi
            printf '%s\\n' "$value" > "$SYSFS/$iface/address"
        done ;;
esac
""")
    logger = bin_dir / "logger"
    logger.write_text('#!/bin/bash\nprintf "log %s\\n" "$*" >> "$CALLS"\n')
    board = bin_dir / "board"
    board.write_text('#!/bin/bash\nexit 0\n')
    ip = bin_dir / "ip"
    ip.write_text('#!/bin/bash\nprintf "ip %s\\n" "$*" >> "$CALLS"\n')
    for executable in bin_dir.iterdir():
        executable.chmod(0o755)
    block = tmp_path / "wifi/mac-contract-blocked"
    block.parent.mkdir()
    block.touch()
    source = (SCRIPTS / "wifi_init.sh").read_text()
    functions = source[source.index("try_insmod() {"):
                       source.index("apply_iface_txpwrlimit() {")]
    phase = source[source.index("# BRIDGE: MAC_MODE"):
                   source.index('apply_iface_txpwrlimit "mlan0" "$MLAN0_ENABLED"')]
    # Redirect OS paths, leaving the real decisions and update helper intact.
    prepare = source[source.index("# MAC 초기화 중이거나 실패한 동안"):
                     source.index("# 이미 로드된 모듈이 있으면")]
    body = (prepare + functions + phase).replace(
        "/usr/local/scripts/update_mac.sh", str(SCRIPTS / "update_mac.sh")
    ).replace("/sys/class/net", str(sysfs)).replace("/run/wifi", str(tmp_path / "wifi"))
    library = tmp_path / "mac_link_lib.sh"
    library.write_text((SCRIPTS / library.name).read_text().replace("/sys/class/net", str(sysfs)))
    harness = f"""set -euo pipefail
. {shlex.quote(str(library))}
tag=wifi_init-test
BRIDGE_IFACE=mlan0
BRIDGE_NONE=false
MAC_MODE={shlex.quote(mode)}
MAC_CLONE_REQUIRE_PEER=true
IFACE_ENABLED_DEFAULT=false
MLAN0_ENABLED=true
MLAN1_ENABLED={str(secondary_enabled).lower()}
MFG_MODE={int(mfg)}
MFG_LOADED_FLAG={shlex.quote(str(tmp_path / 'mfg'))}
ANT_TYPE=''
MLAN_KO=mlan_imx93.ko
MOAL_KO=moal_imx93.ko
BOARD_TYPE=imx93
ACTUAL_SOC_ID=iMX93
BOARD_CONFIG_SH={shlex.quote(str(board))}
moal_args=''
wifi_init_iface_is_enabled() {{ [ "$1" = mlan0 ]; }}
{body}
printf 'association-ready\\n' >> "$CALLS"
"""
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
           "WIFI_INIT_CONF_JSON": str(config), "SYSTEMD_NETWORK_DIR": str(network),
           "SYSFS": str(sysfs), "CALLS": str(calls), "OVERRIDE": override or "",
           "RELOAD_RC": str(reload_rc), "WAIT_RC": str(wait_rc), "HELP_RC": str(help_rc)}
    result = subprocess.run(["bash", "-c", harness], env=env, text=True,
                            capture_output=True, timeout=10)
    return result, calls.read_text(), sysfs


def test_static_mac_is_live_before_association_without_operator_reload(tmp_path):
    result, calls, sysfs = run_mac_phase(tmp_path)
    assert result.returncode == 0, result.stderr
    assert (sysfs / "mlan0/address").read_text().strip() == PEER
    assert calls.index("udev control") < calls.index("insmod")
    assert calls.index("udev wait --timeout=5") < calls.index("association-ready")
    assert not (tmp_path / "wifi/mac-contract-blocked").exists()


@pytest.mark.parametrize("fault", ["reload", "wait", "mismatch", "write", "duplicate"])
def test_mac_failure_blocks_association_with_non_reboot_status(tmp_path, fault):
    kwargs = {"reload": {"reload_rc": 1}, "wait": {"wait_rc": 1},
              "mismatch": {"override": "00:50:43:02:fe:01"},
              "write": {"blocked_write": True},
              "duplicate": {"secondary_mac": PEER, "secondary_enabled": True}}[fault]
    result, calls, _ = run_mac_phase(tmp_path, **kwargs)
    assert result.returncode == 78, (result.stderr, calls)
    assert "association-ready" not in calls
    assert (tmp_path / "wifi/mac-contract-blocked").exists()
    if fault in {"reload", "write", "duplicate"}:
        assert "insmod" not in calls


def test_disabled_secondary_mac_is_not_required_on_live_device(tmp_path):
    result, calls, _ = run_mac_phase(tmp_path, secondary_mac="02:aa:bb:cc:dd:02")
    assert result.returncode == 0, result.stderr
    assert "udev wait --timeout=5" in calls
    assert not any("udev wait --timeout=5" in line and "mlan1" in line for line in calls.splitlines())


def test_mfg_skips_udev_mac_gate(tmp_path):
    result, calls, _ = run_mac_phase(tmp_path, mfg=True, reload_rc=1, wait_rc=1)
    assert result.returncode == 0, result.stderr
    assert "udev " not in calls


def test_empty_mac_plan_needs_no_live_address_override(tmp_path):
    result, calls, _ = run_mac_phase(tmp_path, mode="default")
    assert result.returncode == 0, result.stderr
    assert "udev wait --timeout=5" not in calls


def run_unit_condition(tmp_path, unit, status, *, blocked=False, actual=PEER):
    # Execute the actual shell condition with a harmless systemctl boundary.
    condition = next((line.split("=", 1)[1] for line in unit.read_text().splitlines()
                      if line.startswith("ExecCondition=")), None)
    if condition is None:
        return 0  # With no condition the unit proceeds to ExecStart.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    systemctl = bin_dir / "systemctl"
    systemctl.write_text(f"#!/bin/sh\nprintf '%s\\n' {status}\n")
    systemctl.chmod(0o755)
    sysfs = tmp_path / "net/mlan0"
    sysfs.mkdir(parents=True)
    (sysfs / "address").write_text(actual + "\n")
    network = tmp_path / "network"
    network.mkdir()
    (network / "20-mlan0.link").write_text(
        f"[Match]\nOriginalName=mlan0\n[Link]\nMACAddress={PEER}\n")
    marker = tmp_path / "mac-contract-blocked"
    if blocked:
        marker.touch()
    condition = condition.replace("/run/wifi/mac-contract-blocked", str(marker))
    if "/usr/local/scripts/wlan_mac_ready.sh" in condition:
        helper = tmp_path / "wlan_mac_ready.sh"
        helper.write_text((SCRIPTS / helper.name).read_text().replace(
            "/run/wifi/mac-contract-blocked", str(marker)))
        helper.chmod(0o755)
        (tmp_path / "mac_link_lib.sh").write_text(
            (SCRIPTS / "mac_link_lib.sh").read_text().replace(
                "/sys/class/net", str(sysfs.parent)))
        condition = condition.replace("/usr/local/scripts/wlan_mac_ready.sh", str(helper))
    udev = bin_dir / "udevadm"
    udev.write_text('#!/bin/sh\nexit 0\n')
    udev.chmod(0o755)
    return subprocess.run(shlex.split(condition.replace("%i", "mlan0")),
                          env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
                               "SYSTEMD_NETWORK_DIR": str(network)},
                          capture_output=True, timeout=5).returncode


@pytest.mark.parametrize("status,expected", [(78, 1), (75, 1), (1, 0)])
def test_emergency_condition_excludes_permanent_mac_failure(tmp_path, status, expected):
    assert run_unit_condition(tmp_path, EMERGENCY_UNIT, status) == expected


@pytest.mark.parametrize("status,expected", [(78, 1), (75, 0), (0, 0)])
def test_independent_supplicant_start_is_blocked_after_mac_failure(tmp_path, status, expected):
    assert run_unit_condition(tmp_path, WPA_UNIT, status) == expected


@pytest.mark.parametrize("fault", ["blocked", "recreated-with-wrong-mac"])
def test_supplicant_condition_rejects_unverified_current_device(tmp_path, fault):
    assert run_unit_condition(tmp_path, WPA_UNIT, 0,
                              blocked=fault == "blocked",
                              actual=PEER if fault == "blocked" else "00:50:43:02:fe:01") == 1


def test_mac_failure_prevents_parent_auto_restart_without_becoming_success():
    service = INIT_UNIT.read_text().splitlines()
    prevent = [line.split("=", 1)[1].split() for line in service
               if line.startswith("RestartPreventExitStatus=")]
    success = [line.split("=", 1)[1].split() for line in service
               if line.startswith("SuccessExitStatus=")]
    assert any("78" in values for values in prevent)
    assert not any("78" in values for values in success)


def test_missing_udev_wait_capability_stops_before_driver_operations(tmp_path):
    result, calls, _ = run_mac_phase(tmp_path, help_rc=1)
    assert result.returncode == 78, result.stderr
    assert "insmod" not in calls
    assert "association-ready" not in calls
    assert (tmp_path / "wifi/mac-contract-blocked").exists()


def run_moal_args(tmp_path, *, enabled=True, engine="moal", bridge_iface="mlan0"):
    """Run the bridge-switch and moal-argument section of wifi_init.sh."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    calls.write_text("")
    logger = bin_dir / "logger"
    logger.write_text('#!/bin/bash\nprintf "log %s\\n" "$*" >> "$CALLS"\n')
    logger.chmod(0o755)
    # A module that declares every gated parameter, so the gates add them.
    module = tmp_path / "moal_imx93.ko"
    module.write_bytes(b"\0".join(
        b"parmtype=%s:int" % name
        for name in (b"bridge_keepalive_idle_ms", b"wq_sched_policy")))
    source = (SCRIPTS / "wifi_init.sh").read_text()
    section = source[source.index('BRIDGE_IFACE="${BRIDGE_IFACE:-mlan0}"'):
                     source.index("# MFG 프로파일 멱등 가드")]
    section = section.replace("/opt/wlan/driver/$MOAL_KO", str(module))
    harness = f"""set -euo pipefail
tag=wifi_init-test
WBRIDGE_ENABLED={'true' if enabled else 'false'}
BRIDGE_IFACE={shlex.quote(bridge_iface)}
WBRIDGE_ENGINE={shlex.quote(engine)}
MAC_MODE=default
MAC_CLONE_REQUIRE_PEER=true
BOARD_TYPE=imx93 MLAN_KO=mlan_imx93.ko MOAL_KO=moal_imx93.ko
MLAN0_ENABLED=true MLAN0_FREQ=auto MLAN1_ENABLED=false MLAN1_FREQ=auto
MFG_MODE=0
moal_args='mod_para=cts/wifi_mod_para.conf'
bridge_debug=0 bridge_wlan_idx=0 bridge_keepalive_ms=1 bridge_keepalive_idle_ms=20
bridge_peer='' bridge_consume_link_local='' bridge_local_hairpin='' bridge_roam_announce=''
bridge_deliver_rt_prio=45
{section}
printf 'moal_args=%s\\n' "$moal_args"
"""
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "CALLS": str(calls)}
    result = subprocess.run(["bash", "-c", harness], env=env, text=True,
                            capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    args = next(line for line in result.stdout.splitlines() if line.startswith("moal_args="))
    return args.split("=", 1)[1].split(), calls.read_text()


def test_enabled_moal_bridge_passes_its_driver_arguments(tmp_path):
    args, _ = run_moal_args(tmp_path)
    assert "bridge_mode=1" in args
    assert "bridge_keepalive_idle_ms=20" in args
    assert "wq_sched_policy=1" in args


@pytest.mark.parametrize("setting", [{"enabled": False}, {"bridge_iface": "none"}])
def test_disabled_bridge_passes_no_moal_bridge_arguments(tmp_path, setting):
    # Stopping only the services leaves the moal driver bridging (#402).
    args, calls = run_moal_args(tmp_path, **setting)
    assert args == ["mod_para=cts/wifi_mod_para.conf"]
    assert "bridge disabled: skip moal bridge params" in calls


def test_userspace_engine_never_passes_moal_bridge_arguments(tmp_path):
    args, _ = run_moal_args(tmp_path, engine="pcap")
    assert args == ["mod_para=cts/wifi_mod_para.conf"]
