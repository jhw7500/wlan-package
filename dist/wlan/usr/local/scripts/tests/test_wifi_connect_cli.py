"""`wifi <iface> connect` 의 stdout 계약과 모드 A 재연결 판정 (#292, #293).

핸들러를 실제로 실행한다. `wpa_cli` 는 PATH 스텁으로 대체하되 실기 동작을 흉내 낸다:
`-a <action> -B -P <pidfile>` 데몬 모드는 백그라운드로 남아 pidfile 을 쓰고, 뒤이은
`reassociate`/`reconfigure` 가 "착지" 를 지정하면 `status` 를 그 network 로 바꾸고
action 스크립트에 `CONNECTED` 이벤트(`WPA_ID`)를 넣는다. 그래서 fresh-event + COMPLETED
증명 경로가 그대로 돈다. root 가 필요한 `install -o root` 는 소유권 옵션만 떼는 스텁으로
대체한다(ftpcmd 테스트가 `ip` 를 스텁하는 것과 같은 이유).

#292: 첫 stdout 줄은 vsftpd `wconnect` 의 200 응답 본문이 되므로 ASCII 여야 한다
      (비ASCII 는 vsftpd 가 `?` 로 살균한다 — 2026-09-04 실기).
#293: 모드 A(다중 network 블록)에서 인자 없는 connect 는 REASSOCIATE 뒤 supplicant 가
      enable 된 다른 network 를 고를 수 있다. 그 착지도 fresh CONNECTED + COMPLETED 면
      성공이다. 이벤트 없는 stale COMPLETED 는 여전히 실패(exit 8)여야 한다.
"""
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
WIFI_SH = SCRIPTS_DIR / "wifi.sh"

CONF_MODE_B = textwrap.dedent('''\
    ctrl_interface=/var/run/wpa_supplicant
    ctrl_interface_group=0
    country=KR
    ap_scan=1
    update_config=0
    freq_list=5180 5200 5220 5240
    network={
        ssid="jhw_wlan_"
        key_mgmt=WPA-PSK
        psk="stub-psk-not-a-secret"
        proto=RSN
        pairwise=CCMP
        group=CCMP
        scan_ssid=1
        freq_list=5180 5200 5220 5240
    }
''')

CONF_MODE_A = CONF_MODE_B + textwrap.dedent('''\
    # >>> wifi_extra_ssid auto-generated (do not edit) >>>
    network={
        ssid="jhw_wlan"
        key_mgmt=WPA-PSK
        psk="stub-psk-not-a-secret"
        proto=RSN
        pairwise=CCMP
        group=CCMP
        scan_ssid=1
        freq_list=5180 5200 5220 5240
        priority=0
    }
    # <<< wifi_extra_ssid auto-generated <<<
''')

STATUS_INITIAL = "bssid=04:ba:d6:ec:0b:08\nfreq=5220\nssid=jhw_wlan_\nid=0\nwpa_state=COMPLETED\n"

# 스텁 wpa_cli. 상태 디렉터리는 WPA_STUB_DIR.
#  - abort_scan      -> FAIL (활성 스캔 없음 = quiesce 완료)
#  - status          -> $WPA_STUB_DIR/status 그대로
#  - -a ... -B -P    -> 자기 자신을 --daemon 으로 백그라운드 기동(pidfile 기록),
#                       $WPA_STUB_DIR/fire 가 생기면 action 에 CONNECTED(WPA_ID) 전달
#  - reassociate/reconnect/reconfigure
#                    -> OK. $WPA_STUB_DIR/landing 이 "id ssid freq" 면 status 를 그 값으로
#                       바꾸고 fire 를 남긴다(착지). 없으면 status 불변, 이벤트 없음.
WPA_CLI_STUB = r'''#!/bin/bash
D="${WPA_STUB_DIR:?}"
if [ "${1:-}" = "--daemon" ]; then
    action="$2"; iface="$3"; pidfile="$4"
    echo "$$" > "$pidfile"
    while :; do
        if [ -f "$D/fire" ]; then
            id=$(cat "$D/fire"); rm -f "$D/fire"
            WPA_ID="$id" "$action" "$iface" CONNECTED
        fi
        sleep 0.05
    done
fi
echo "wpa_cli $*" >> "$D/calls.log"
iface=""; action=""; pidfile=""; cmd=""
while [ $# -gt 0 ]; do
    case "$1" in
        -i) iface="$2"; shift ;;
        -a) action="$2"; shift ;;
        -P) pidfile="$2"; shift ;;
        -B) ;;
        *) [ -n "$cmd" ] || cmd="$1" ;;
    esac
    shift
done
if [ -n "$action" ]; then
    nohup "$0" --daemon "$action" "$iface" "$pidfile" >/dev/null 2>&1 &
    exit 0
fi
case "$cmd" in
    abort_scan) echo FAIL ;;
    status) cat "$D/status" ;;
    reassociate|reconnect|reconfigure)
        if [ -f "$D/landing" ]; then
            read -r lid lssid lfreq < "$D/landing"
            printf 'bssid=00:00:00:00:00:%02d\nfreq=%s\nssid=%s\nid=%s\nwpa_state=COMPLETED\n' \
                "$lid" "$lfreq" "$lssid" "$lid" > "$D/status.new"
            mv -f "$D/status.new" "$D/status"
            echo "$lid" > "$D/fire.tmp" && mv -f "$D/fire.tmp" "$D/fire"
        fi
        echo OK ;;
    *) echo OK ;;
esac
'''

# install(1) 스텁: safe_install_sync 의 `-o root -g root` 만 떼고 진짜 install 로 넘긴다.
INSTALL_STUB = r'''#!/bin/bash
args=()
while [ $# -gt 0 ]; do
    case "$1" in
        -o|-g) shift ;;
        *) args+=("$1") ;;
    esac
    shift
done
exec /usr/bin/install "${args[@]}"
'''


class ConnectHarness(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="wifi-connect-"))
        self.stub = self.root / "bin"
        self.stub.mkdir()
        self.state = self.root / "state"
        self.state.mkdir()
        self.run_dir = self.root / "run"
        self.conf_dir = self.root / "wpa"
        self.conf_dir.mkdir()
        (self.stub / "wpa_cli").write_text(WPA_CLI_STUB)
        (self.stub / "install").write_text(INSTALL_STUB)
        for noop in ("sync", "logger", "systemctl"):
            (self.stub / noop).write_text("#!/bin/sh\nexit 0\n")
        for p in self.stub.iterdir():
            p.chmod(0o755)
        (self.state / "status").write_text(STATUS_INITIAL)
        self.conf = self.conf_dir / "wpa_supplicant-mlan0.conf"

    def tearDown(self):
        # 스텁 데몬은 핸들러 cleanup 이 TERM 으로 거둔다. 남은 것이 있으면 여기서 정리.
        subprocess.run(["pkill", "-f", f"{self.stub}/wpa_cli --daemon"], check=False)
        shutil.rmtree(self.root, ignore_errors=True)

    def write_conf(self, text):
        self.conf.write_text(text)

    def land_on(self, net_id, ssid, freq):
        (self.state / "landing").write_text(f"{net_id} {ssid} {freq}\n")

    def run_connect(self, *args):
        env = dict(os.environ)
        env.update({
            "PATH": f"{self.stub}:{os.environ['PATH']}",
            "WPA_STUB_DIR": str(self.state),
            "WPA_CONF_DIR": str(self.conf_dir),
            "WIFI_RUN_DIR": str(self.run_dir),
            "WIFI_INIT_CONF_JSON": str(self.root / "absent.json"),
            "WIFI_SCAN_TRANSITION_LOCK_TIMEOUT": "0",
            "ASSOC_TIMEOUT_DEFAULT": "2",
        })
        return subprocess.run(
            ["bash", str(WIFI_SH), "mlan0", "connect", *args],
            capture_output=True, text=True, timeout=60, env=env,
        )

    @staticmethod
    def first_line(text):
        lines = text.splitlines()
        return lines[0] if lines else ""


class FirstLineIsAscii(ConnectHarness):
    """#292 — wconnect 200 응답 본문이 되는 첫 stdout 줄은 ASCII 여야 한다."""

    def test_ssid_switch_keeps_freq_list_first_line_is_ascii(self):
        self.write_conf(CONF_MODE_B)
        self.land_on(0, "jhw_wlan", 5200)
        r = self.run_connect("jhw_wlan")
        line = self.first_line(r.stdout)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(line.isascii(), f"non-ASCII first line: {line!r}")
        self.assertEqual(
            line,
            f'conf updated: ssid="jhw_wlan" (global/block freq_list kept: '
            f'5180 5200 5220 5240) in {self.conf}',
        )
        self.assertIn('ssid="jhw_wlan"', self.conf.read_text())

    def test_ssid_switch_without_freq_list_first_line_is_ascii(self):
        self.write_conf(CONF_MODE_B.replace("freq_list=5180 5200 5220 5240\n", ""))
        self.land_on(0, "jhw_wlan", 5200)
        r = self.run_connect("jhw_wlan")
        line = self.first_line(r.stdout)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(line.isascii(), f"non-ASCII first line: {line!r}")
        self.assertEqual(
            line, f'conf updated: ssid="jhw_wlan" (no frequency restriction) in {self.conf}',
        )

    def test_no_arg_reconnect_first_line_is_ascii(self):
        self.write_conf(CONF_MODE_B)
        self.land_on(0, "jhw_wlan_", 5220)
        r = self.run_connect()
        line = self.first_line(r.stdout)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(line.isascii(), f"non-ASCII first line: {line!r}")
        self.assertEqual(line, "no ssid given - reassociating current network id=0 on mlan0...")


class ModeANoArgReconnect(ConnectHarness):
    """#293 — 다중 network 블록에서 인자 없는 connect 의 착지 판정."""

    def test_landing_on_other_enabled_network_is_success(self):
        self.write_conf(CONF_MODE_A)
        self.land_on(1, "jhw_wlan", 5200)          # supplicant 가 id=1 을 고른다
        r = self.run_connect()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('associated: ssid="jhw_wlan" freq=5200 id=1 (wpa_state=COMPLETED)', r.stdout)
        line = self.first_line(r.stdout)
        self.assertTrue(line.isascii(), f"non-ASCII first line: {line!r}")
        self.assertIn("multi-network topology", line)

    def test_stale_completed_without_fresh_event_still_fails(self):
        """양성 대조: 위와 같은 conf 에서 이벤트가 없으면 실패가 유지돼야 한다."""
        self.write_conf(CONF_MODE_A)
        # landing 없음 -> reassociate OK 지만 status 는 옛 COMPLETED(id=0), CONNECTED 이벤트 없음
        r = self.run_connect()
        self.assertEqual(r.returncode, 8, r.stdout + r.stderr)
        self.assertIn("requested association not completed", r.stderr)

    def test_ssid_argument_is_still_refused(self):
        self.write_conf(CONF_MODE_A)
        r = self.run_connect("jhw_wlan")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("Mode A", r.stderr)
        self.assertIn('ssid="jhw_wlan_"', self.conf.read_text())   # conf 는 그대로


if __name__ == "__main__":
    unittest.main()
