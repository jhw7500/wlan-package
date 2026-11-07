"""wifi-sniff 캡처 도구가 로드된 moal 계열에 맞는 netmon 밴드 값을 넘기는지 고정한다(#456).

`mlanutl <iface> netmon 1 <filter> <band> <channel> <bw>` 의 밴드 인자는 드라이버마다
뜻이 다르다(드라이버 원문 기준).

* 505.p14(i.MX8 payload, wlan-driver-v2 `main`): 옛 비트맵(B=1, G=2, A=4, GN=8, AN=16,
  GAC=32, AAC=64). `SupportedInfraBand` 검사가 원소 수로 돌고 바이트 수와 비교해 어떤
  값도 거부하지 않는다. 그래서 넣어 보고 실패하면 바꾸는 식으로는 계열을 가를 수 없다.
* 543.p18(i.MX93 payload, `ported`): `BAND_2GHZ=0`·`BAND_5GHZ=1`·`BAND_6GHZ=2` 만 받고
  나머지는 `NET_MON: Invalid band`(-EINVAL)로 거부한다. 채널도 그 밴드에 속해야 한다.

가짜 mlanutl 이 두 계열의 검사를 흉내 내고, 두 스크립트가 실제로 넘긴 인자를 기록한다.
계열은 `$WIFI_SYS_MODULE_ROOT/moal/version`(기본 /sys/module)의 로드된 모듈 버전으로 정한다.
"""

from __future__ import annotations

import getopt
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
SNIFF_DIR = TESTS_DIR.parents[1] / "tools" / "wifi-sniff"
SCRIPTS = {
    "capture": SNIFF_DIR / "wifi-capture.sh",
    "logger": SNIFF_DIR / "wifi-logger.sh",
}
README = SNIFF_DIR / "README.md"

ENUM_VERSION = "543.p18"
BITMAP_VERSION = "505.p14"

LEGACY_BITS = {"B": 1, "G": 2, "A": 4, "GN": 8, "AN": 16, "GAC": 32, "AAC": 64}
BITS_24 = 1 | 2 | 8 | 32
BITS_5 = 4 | 16 | 64

# 자동 감지 경로에서 "pcap 이 생긴다"를 보려면 로거는 -s 를 줘야 한다.
AUTO_ARGS = {"capture": (), "logger": ("-s",)}

_REJECT = 'echo "mlanutl: Invalid argument" >&2; echo "mlanutl: netmon fail" >&2; exit 1'

FAKES = {
    "mlanutl": f"""#!/bin/bash
printf 'mlanutl %s\\n' "$*" >> "$FAKE_LOG"
if [ "$2" = netmon ] && [ "$3" = 1 ]; then
    band=$5 ch=$6
    if [ "$FAKE_NETMON_FAMILY" = enum ]; then
        case "$band" in
            0|1|2) ;;
            *) {_REJECT} ;;
        esac
        # woal_is_channel_valid(): 채널이 그 밴드에 속해야 한다
        if {{ [ "$band" = 0 ] && [ "$ch" -gt 14 ]; }} || {{ [ "$band" = 1 ] && [ "$ch" -le 14 ]; }}; then
            {_REJECT}
        fi
    fi
    : > "$FAKE_STATE/rtap"
elif [ "$2" = netmon ] && [ "$3" = 0 ]; then
    rm -f "$FAKE_STATE/rtap"
fi
exit 0
""",
    "wpa_cli": """#!/bin/bash
printf 'wpa_cli %s\\n' "$*" >> "$FAKE_LOG"
case " $* " in
    *" status "*) printf 'bssid=02:00:00:00:00:01\\nfreq=%s\\nssid=lab\\nwpa_state=COMPLETED\\n' "$FAKE_FREQ" ;;
esac
exit 0
""",
    "iw": """#!/bin/bash
case "$*" in
    *"station dump"*) printf 'Station 02:00:00:00:00:01 (on %s)\\n\\ttx bitrate:\\t72.2 MBit/s MCS 7 short GI\\n' "$2" ;;
esac
exit 0
""",
    "ip": """#!/bin/bash
if [ "$1" = link ] && [ "$2" = show ]; then
    [ -e "$FAKE_STATE/$3" ]
    exit
fi
exit 0
""",
    "tcpdump": """#!/bin/bash
printf 'tcpdump %s\\n' "$*" >> "$FAKE_LOG"
while [ $# -gt 0 ]; do
    case "$1" in
        -w) printf 'pcap\\n' > "$2"; exit 0 ;;
        -r) printf 'frame\\n'; exit 0 ;;
    esac
    shift
done
exit 0
""",
    "ssh": """#!/bin/bash
printf 'ssh %s\\n' "$*" >> "$FAKE_LOG"
exit 0
""",
    "scp": """#!/bin/bash
printf 'scp %s\\n' "$*" >> "$FAKE_LOG"
exit 0
""",
    "id": "#!/bin/bash\necho 0\n",
    "sleep": "#!/bin/bash\nexit 0\n",
    "tshark": "#!/bin/bash\nexit 0\n",
    # stdbuf -oL <cmd> ... → <cmd> ...
    "stdbuf": '#!/bin/bash\nwhile [ "${1#-}" != "$1" ]; do shift; done\nexec "$@"\n',
}


class Rig:
    """가짜 도구와 가짜 /sys/module 로 스크립트를 실행하고 호출 기록을 돌려준다."""

    def __init__(self, tmp_path: Path) -> None:
        self.bin = tmp_path / "bin"
        self.state = tmp_path / "state"
        self.out = tmp_path / "out"
        self.work = tmp_path / "work"
        self.mods = tmp_path / "sys_module"
        self.log = tmp_path / "calls.log"
        for d in (self.bin, self.state, self.out, self.work, self.mods):
            d.mkdir()
        for name, body in FAKES.items():
            path = self.bin / name
            path.write_text(body, encoding="utf-8")
            path.chmod(0o755)
        # 로거 예시의 `-F filter.json` 은 현재 디렉터리 기준이다.
        shutil.copy(SNIFF_DIR / "filter.json", self.work / "filter.json")

    def run(self, script, *args, version=ENUM_VERSION, family="enum", freq="5180", env=None):
        moal = self.mods / "moal"
        shutil.rmtree(moal, ignore_errors=True)
        if version is not None:
            moal.mkdir()
            (moal / "version").write_text(version + "\n", encoding="utf-8")
        for path in list(self.state.iterdir()) + list(self.out.iterdir()):
            path.unlink()
        self.log.write_text("", encoding="utf-8")
        run_env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "LC_ALL": "C.UTF-8",
            "FAKE_LOG": str(self.log),
            "FAKE_STATE": str(self.state),
            "FAKE_NETMON_FAMILY": family,
            "FAKE_FREQ": freq,
            "WIFI_SYS_MODULE_ROOT": str(self.mods),
        }
        run_env.update(env or {})
        proc = subprocess.run(
            ["bash", str(SCRIPTS[script]), *args, "-o", str(self.out)],
            cwd=self.work,
            env=run_env,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
        return proc, self.log.read_text(encoding="utf-8").splitlines()

    def pcaps(self):
        return sorted(p.name for p in self.out.iterdir() if p.suffix == ".pcap")


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


def netmon_starts(calls):
    """`mlanutl <iface> netmon 1 ...` 호출마다 [filter, band, channel, bw] 를 돌려준다."""
    starts = []
    for line in calls:
        parts = line.split()
        if parts[:1] == ["mlanutl"] and parts[2:4] == ["netmon", "1"]:
            starts.append(parts[4:])
    return starts


def touched_link(calls):
    return [c for c in calls if c.startswith("wpa_cli ") and "disconnect" in c]


def remote_runs(calls):
    """원격 실행 줄(`ssh -t ...`)만 돌려준다. 끝의 정리용 `ssh ... rm -f` 는 뺀다."""
    return [c for c in calls if c.startswith("ssh -t ")]


@pytest.mark.parametrize("script", SCRIPTS)
@pytest.mark.parametrize("freq, channel, band", [("5180", "36", "1"), ("2437", "6", "0")])
def test_enum_driver_auto_detect_passes_the_driver_band(rig, script, freq, channel, band):
    proc, calls = rig.run(script, *AUTO_ARGS[script], freq=freq)
    assert proc.returncode == 0, proc.stderr
    assert netmon_starts(calls) == [["7", band, channel, "0"]]
    assert rig.pcaps(), "pcap 이 만들어지지 않았다"


@pytest.mark.parametrize("script", SCRIPTS)
@pytest.mark.parametrize(
    "args, expected",
    [
        (("-c", "64", "-b", "AN"), ["7", "1", "64", "0"]),
        (("-c", "6", "-b", "GN"), ["7", "0", "6", "0"]),
        (("-c", "149", "-b", "AAC", "-w", "4"), ["7", "1", "149", "4"]),
        (("-c", "1", "-b", "G"), ["7", "0", "1", "0"]),
        (("-c", "36", "-b", "20"), ["7", "1", "36", "0"]),
        (("-c", "6", "-b", "11"), ["7", "0", "6", "0"]),
        (("-c", "6", "-b", "1"), ["7", "0", "6", "0"]),
        (("-c", "36", "-b", "4"), ["7", "1", "36", "0"]),
        (("-c", "40"), ["7", "1", "40", "0"]),
        (("-c", "6"), ["7", "0", "6", "0"]),
    ],
)
def test_enum_driver_converts_legacy_band_values(rig, script, args, expected):
    proc, calls = rig.run(script, *args)
    assert proc.returncode == 0, proc.stderr
    assert netmon_starts(calls) == [expected]


@pytest.mark.parametrize("script", SCRIPTS)
@pytest.mark.parametrize(
    "args, freq, expected",
    [
        ((), "5180", ["7", "20", "36", "0"]),
        ((), "2437", ["7", "11", "6", "0"]),
        (("-c", "64", "-b", "AN"), "5180", ["7", "16", "64", "0"]),
        (("-c", "6", "-b", "GN"), "5180", ["7", "8", "6", "0"]),
        (("-c", "36", "-b", "20"), "5180", ["7", "20", "36", "0"]),
        (("-c", "6"), "5180", ["7", "11", "6", "0"]),
    ],
)
def test_bitmap_driver_keeps_the_legacy_bitmap(rig, script, args, freq, expected):
    proc, calls = rig.run(script, *args, version=BITMAP_VERSION, family="bitmap", freq=freq)
    assert proc.returncode == 0, proc.stderr
    assert netmon_starts(calls) == [expected]


@pytest.mark.parametrize("script", SCRIPTS)
@pytest.mark.parametrize("band", ["13", "0", "128"])
def test_enum_driver_rejects_a_band_it_cannot_express(rig, script, band):
    # 13 = B+A+GN(2.4·5 GHz 혼합), 0 = 비트 없음, 128 = 정의되지 않은 비트
    proc, calls = rig.run(script, "-c", "36", "-b", band)
    assert proc.returncode != 0
    assert f"밴드 값 {band} " in proc.stderr
    assert netmon_starts(calls) == []
    assert touched_link(calls) == []


@pytest.mark.parametrize("script", SCRIPTS)
def test_unknown_driver_version_stops_before_touching_the_link(rig, script):
    proc, calls = rig.run(script, version="552.p22")
    assert proc.returncode != 0
    assert "552.p22" in proc.stderr
    assert "NETMON_BAND_MODE" in proc.stderr
    assert netmon_starts(calls) == []
    assert touched_link(calls) == []


@pytest.mark.parametrize("script", SCRIPTS)
def test_missing_driver_version_stops_before_touching_the_link(rig, script):
    proc, calls = rig.run(script, version=None)
    assert proc.returncode != 0
    assert "moal/version" in proc.stderr
    assert "NETMON_BAND_MODE" in proc.stderr
    assert netmon_starts(calls) == []
    assert touched_link(calls) == []


@pytest.mark.parametrize("script", SCRIPTS)
@pytest.mark.parametrize(
    "mode, version, family, band",
    [("enum", "552.p22", "enum", "1"), ("bitmap", ENUM_VERSION, "bitmap", "20")],
)
def test_band_mode_override_wins_over_the_version_table(rig, script, mode, version, family, band):
    proc, calls = rig.run(script, version=version, family=family, env={"NETMON_BAND_MODE": mode})
    assert proc.returncode == 0, proc.stderr
    assert netmon_starts(calls) == [["7", band, "36", "0"]]


@pytest.mark.parametrize("script", SCRIPTS)
def test_invalid_band_mode_override_is_rejected(rig, script):
    proc, calls = rig.run(script, env={"NETMON_BAND_MODE": "auto"})
    assert proc.returncode != 0
    assert "NETMON_BAND_MODE" in proc.stderr
    assert netmon_starts(calls) == []
    assert touched_link(calls) == []


@pytest.mark.parametrize("script", SCRIPTS)
def test_netmon_failure_reports_the_arguments_it_passed(rig, script):
    # 5 GHz 채널에 2.4 GHz 밴드를 주면 543.p18 은 채널 검사에서 거부한다.
    proc, calls = rig.run(script, "-c", "36", "-b", "GN", "-w", "4")
    assert proc.returncode != 0
    assert netmon_starts(calls) == [["7", "0", "36", "4"]]
    assert "netmon 1 7 0 36 4" in proc.stderr


def test_remote_run_needs_no_local_driver(rig):
    # -H 는 같은 스크립트를 타겟에서 다시 돌린다. 판별은 타겟에서 한다.
    proc, calls = rig.run("capture", "-H", "192.0.2.1", "-c", "36", version=None)
    assert proc.returncode == 0, proc.stderr
    ssh = remote_runs(calls)
    assert len(ssh) == 1
    assert "NETMON_BAND_MODE" not in ssh[0]


def test_remote_run_forwards_the_band_mode_override(rig):
    proc, calls = rig.run(
        "capture", "-H", "192.0.2.1", "-c", "36", version=None, env={"NETMON_BAND_MODE": "enum"}
    )
    assert proc.returncode == 0, proc.stderr
    ssh = remote_runs(calls)
    assert len(ssh) == 1
    assert "NETMON_BAND_MODE=enum /tmp/wifi-capture.sh" in ssh[0]


# --- 문서의 예시가 두 계열에서 그대로 동작한다 ---


def _help_examples(script):
    out = subprocess.run(
        ["bash", str(SCRIPTS[script]), "-h"],
        capture_output=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    ).stdout
    block = out.split("Examples:", 1)[1].split("\n\n", 1)[0]
    name = SCRIPTS[script].name
    examples = []
    for line in block.splitlines():
        cmd = line.split("#", 1)[0].strip()
        if cmd == name or cmd.startswith(name + " "):
            examples.append(shlex.split(cmd)[1:])
    return examples


def _readme_examples():
    examples = []
    for line in README.read_text(encoding="utf-8").splitlines():
        cmd = line.split("#", 1)[0].strip()
        if re.match(r"wifi-capture\.sh(\s|$)", cmd) and not re.search(r"[\[<]", cmd):
            examples.append(shlex.split(cmd)[1:])
    return examples


def _expected(args, family, auto_channel=36):
    opts = dict(getopt.getopt(list(args), "c:b:f:w:i:H:o:F:l:sh")[0])
    channel = int(opts.get("-c", auto_channel))
    band = opts.get("-b")
    if band is None:
        legacy = 11 if channel <= 14 else 20
    else:
        legacy = LEGACY_BITS.get(band, None)
        legacy = int(band) if legacy is None else legacy
    if family == "bitmap":
        return str(legacy), str(channel)
    return ("0" if legacy & BITS_24 else "1"), str(channel)


@pytest.mark.parametrize("family, version", [("enum", ENUM_VERSION), ("bitmap", BITMAP_VERSION)])
@pytest.mark.parametrize("source", ["capture-help", "logger-help", "readme"])
def test_documented_examples_start_netmon(rig, source, family, version):
    script = "logger" if source == "logger-help" else "capture"
    examples = _readme_examples() if source == "readme" else _help_examples(script)
    assert len(examples) >= 3, f"{source} 에서 예시를 읽지 못했다: {examples}"
    failures = []
    for args in examples:
        if "-H" in args:  # 원격 실행은 같은 스크립트를 타겟에서 다시 돈다
            continue
        proc, calls = rig.run(script, *args, version=version, family=family)
        starts = netmon_starts(calls)
        band, channel = _expected(args, family)
        if proc.returncode != 0 or len(starts) != 1 or starts[0][1:3] != [band, channel]:
            failures.append((args, proc.returncode, starts, proc.stderr[-300:]))
    assert failures == []
