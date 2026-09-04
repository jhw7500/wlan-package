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


# vsftpd `isolate` 조건 재현 wrapper. 두 가지를 만든다.
#  1. 새 PID 네임스페이스이지만 `/proc` 은 **호스트 것 그대로**다(--mount-proc 를 쓰지 않는다).
#  2. 자식이 받는 ns PID 가 호스트 /proc 에 **없도록** 미리 PID 를 소진한다. 실기(cts-wlan)는
#     프로세스가 163개뿐이라 낮은 ns PID 가 호스트에 없는 것이 정상이지만, 프로세스가 많은
#     빌드 호스트는 낮은 번호가 우연히 존재해 조건이 성립하지 않는다(실측: 소진 없이는 통과).
# `"$@"; exit $?` 로 두 문장을 만들어 bash 의 exec 최적화를 막는다 — wifi.sh 가 ns PID 1(init)이
# 되면 시그널 기본동작과 고아 reaper 규약이 실기와 달라진다.
# 조건을 못 만들면 조용히 통과시키지 않고 99 로 끝내 테스트가 skip 되게 한다.
NS_WRAPPER = r'''
burn="${WIFI_TEST_PIDNS_BURN:-400}"
i=0
while [ "$i" -lt "$burn" ]; do (:); i=$((i + 1)); done
(:) & probe=$!
wait "$probe" 2>/dev/null
if [ -r "/proc/$probe/stat" ]; then
    echo "HARNESS: ns pid $probe still resolves in host /proc" >&2
    exit 99
fi
"$@"
exit $?
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

    def run_connect(self, *args, pid_namespace=False):
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
        argv = ["bash", str(WIFI_SH), "mlan0", "connect", *args]
        if pid_namespace:
            argv = ["unshare", "-Upf", "bash", "-c", NS_WRAPPER, "_", *argv]
        return subprocess.run(
            argv, capture_output=True, text=True, timeout=60, env=env,
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


def _pid_namespace_available():
    """ns PID ≠ 호스트 /proc PID 조건을 만들 수 있는가. sudo 없이 unprivileged userns 로 만든다."""
    if shutil.which("unshare") is None:
        return False
    try:
        probe = subprocess.run(
            ["unshare", "-Upf", "bash", "-c", 'echo "$$ $(cut -d\' \' -f1 /proc/self/stat)"; exit 0'],
            capture_output=True, text=True, timeout=20,
        )
    except Exception:  # noqa: BLE001 - 환경 탐지, 무엇이든 미지원으로 본다
        return False
    if probe.returncode != 0:
        return False
    fields = probe.stdout.split()
    return len(fields) == 2 and fields[0] != fields[1]


@unittest.skipUnless(_pid_namespace_available(), "PID namespace unavailable")
class ConnectInsidePidNamespace(ConnectHarness):
    """#297 — vsftpd 는 커넥션마다 별도 PID 네임스페이스에서 핸들러를 실행하지만 `/proc` 은
    호스트 것이 그대로 보인다. 재연결 이벤트 모니터가 PID 를 `/proc` 으로 판정하면 자기 자식이
    아니라 같은 번호의 호스트 프로세스를 보게 된다. 그 조건에서도 connect 는 동작해야 한다."""

    def _run(self, *args):
        r = self.run_connect(*args, pid_namespace=True)
        if r.returncode == 99:
            self.skipTest(f"PID namespace condition unmet: {r.stderr.strip()}")
        return r

    def test_no_arg_reconnect_attaches_monitor_inside_pid_namespace(self):
        self.write_conf(CONF_MODE_B)
        self.land_on(0, "jhw_wlan_", 5220)
        r = self._run()
        self.assertNotIn("failed to attach reconnect event monitor", r.stderr)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # 성공 판정은 스텁 데몬이 넣은 fresh CONNECTED 이벤트를 거쳐야만 나온다 —
        # 즉 모니터가 남의 호스트 PID 가 아니라 우리 자식에 실제로 붙었다는 증거다.
        self.assertIn('associated: ssid="jhw_wlan_"', r.stdout)

    def test_ssid_switch_inside_pid_namespace(self):
        self.write_conf(CONF_MODE_B)
        self.land_on(0, "jhw_wlan", 5200)
        r = self._run("jhw_wlan")
        self.assertNotIn("failed to attach reconnect event monitor", r.stderr)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
