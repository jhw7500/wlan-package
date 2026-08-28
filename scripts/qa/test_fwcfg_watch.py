#!/usr/bin/env python3
"""Host-side tests for the fwcfg_watch observer in wifi_logger_link.py.

The observer samples FW custom settings (rate_adapt / antcfg / mcs_tier) and
logs only when a value changes. Its whole purpose is to catch a firmware
revert of a pre-association setting, so the tests below pin the properties
that make that possible: the normalizers must distinguish the drifts we have
actually observed, every baseline must be captured on the first tick, and a
command outage must not turn the watcher into a repeated batch of blocking
calls inside the 0.9s link.json loop.

wifi_logger_link.py imports sUTILS/roam_policy at module scope, which are not
importable on a plain host, so the watcher block is exec'd out of the source
rather than imported.
"""

import re
import unittest
from pathlib import Path

LOGGER_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "dist/wlan/usr/local/logger/wifi_logger_link.py"
)

# mlanutl 실측 출력 (SD9098 / iMX93, 2026-08-28)
RATE_ADAPT_STATIC = """Rate Adapt Cfg:
 SR RateAdapt Enabled
Aggregated data Tx success rate static thresholds:
    Low   : 70
    High  : 90
Eval Timer interval  : 10  i.e. 100ms
(in multiples of 10)"""

RATE_ADAPT_DYNAMIC = """Rate Adapt Cfg:
 SR RateAdapt Enabled
Dynamic rate adaptation mode based on noise level active
Eval Timer interval  : 10  i.e. 100ms
(in multiples of 10)"""

RATE_ADAPT_LEGACY = "Rate Adapt Cfg:\n Legacy RateAdapt Enabled\n"

ANTCFG = """Mode of Tx path is 0x101
Mode of Rx path is 0x101
NSS limit (antcfg): 2G rx=1 tx=1, 5G rx=1 tx=1  [user_htstream=0x1111]"""

MCS_TIER = """MCS Tier Capability Configuration (association)
  NSS limit (antcfg): 2G rx=1 tx=1, 5G rx=1 tx=1  [user_htstream=0x1111]
  HT  (11n)  : 1x1 (MCS 0~7)
  VHT Tx: 0xFFF0  ->  advertised 0xFFFC (NSS limit 1)
    NSS1: MCS 0~7
  VHT Rx: 0xFFF0  ->  advertised 0xFFFC (NSS limit 1)
    NSS1: MCS 0~7
  HE Band: 5G (0x02)
  HE Tx: 0xFFF0  ->  advertised 0xFFFC (NSS limit 1)
    NSS1: MCS 0~7
  HE Rx: 0xFFF0  ->  advertised 0xFFFC (NSS limit 1)
    NSS1: MCS 0~7"""

COMMAND_OUTPUT = {
    "rate_adapt_cfg": RATE_ADAPT_STATIC,
    "antcfg": ANTCFG,
    "mcstiercfg": MCS_TIER,
}


def load_watcher(watch_seconds=60):
    """Return a namespace holding the watcher block with stubbed collaborators.

    ``run_command`` and ``logger`` are left for the caller to replace; the
    returned dict also carries ``calls`` and ``logged`` for assertions.
    """
    source = LOGGER_SOURCE.read_text(encoding="utf-8")
    block = source[
        source.index("_RE_RA_LOW = re.compile") : source.index("\n\n_empty_json")
    ]
    calls: list[str] = []
    logged: list[tuple[str, str]] = []

    class _Logger:
        def message(self, level, text, extra=None):
            logged.append((level, text))

    namespace: dict = {"re": re}
    exec(block, namespace)  # noqa: S102 - the block under test is our own source
    namespace.update(
        {
            "logger": _Logger(),
            "_EXTRA_": lambda: None,
            "IFACE": "mlan0",
            "FWCFG_WATCH_SEC": watch_seconds,
            "calls": calls,
            "logged": logged,
        }
    )
    return namespace


def responder(namespace, output=None, missing=()):
    """Install a run_command stub; commands in ``missing`` return ''.

    A caller-supplied ``output`` mapping is used as-is so a test can mutate it
    between ticks to simulate a value drifting.
    """
    table = dict(COMMAND_OUTPUT) if output is None else output

    def run_command(cmd):
        subcommand = cmd[2]
        namespace["calls"].append(subcommand)
        return "" if subcommand in missing else table[subcommand]

    namespace["run_command"] = run_command
    return run_command


class NormalizerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.ns = load_watcher()

    def test_rate_adapt_modes_are_distinguished(self) -> None:
        norm = self.ns["_norm_rate_adapt"]
        self.assertEqual(norm(RATE_ADAPT_STATIC), "SR 70/90 iv=10")
        self.assertEqual(norm(RATE_ADAPT_DYNAMIC), "SR dyn/dyn iv=10")
        self.assertEqual(norm(RATE_ADAPT_LEGACY), "legacy")

    def test_rate_adapt_threshold_drift_is_detected(self) -> None:
        """The 70/90 -> 30/50 change observed on target must not normalize away."""
        norm = self.ns["_norm_rate_adapt"]
        drifted = RATE_ADAPT_STATIC.replace("70", "30").replace("90", "50")
        self.assertNotEqual(norm(drifted), norm(RATE_ADAPT_STATIC))

    def test_antcfg_paths_and_htstream(self) -> None:
        norm = self.ns["_norm_antcfg"]
        self.assertEqual(norm(ANTCFG), "tx=0x101 rx=0x101 hts=0x1111")
        self.assertNotEqual(norm(ANTCFG.replace("0x101", "0x303")), norm(ANTCFG))

    def test_mcs_tier_keeps_the_ht_mcs_ceiling(self) -> None:
        """An HT ceiling drift with an unchanged NSS token must still differ."""
        norm = self.ns["_norm_mcs_tier"]
        self.assertIn("MCS 0~7", norm(MCS_TIER))
        widened = MCS_TIER.replace("1x1 (MCS 0~7)", "1x1 (MCS 0~15)")
        self.assertNotEqual(norm(widened), norm(MCS_TIER))

    def test_mcs_tier_detects_the_documented_he_clamp(self) -> None:
        """The documented first-association clamp 0xFFF0 -> 0xFFFA."""
        norm = self.ns["_norm_mcs_tier"]
        clamped = MCS_TIER.replace("HE Tx: 0xFFF0", "HE Tx: 0xFFFA").replace(
            "HE Rx: 0xFFF0", "HE Rx: 0xFFFA"
        )
        self.assertNotEqual(norm(clamped), norm(MCS_TIER))

    def test_antcfg_line_inside_mcstiercfg_is_ignored(self) -> None:
        """mcstiercfg echoes the antcfg NSS limit; the two signals stay independent."""
        norm = self.ns["_norm_mcs_tier"]
        moved = MCS_TIER.replace(
            "2G rx=1 tx=1, 5G rx=1 tx=1  [user_htstream=0x1111]",
            "2G rx=2 tx=2, 5G rx=2 tx=2  [user_htstream=0x2222]",
        )
        self.assertEqual(norm(moved), norm(MCS_TIER))

    def test_unparseable_output_yields_none(self) -> None:
        for norm_name in ("_norm_rate_adapt", "_norm_antcfg", "_norm_mcs_tier"):
            norm = self.ns[norm_name]
            self.assertIsNone(norm(""), norm_name)
            self.assertIsNone(norm("mlanutl: command failed"), norm_name)


class WatchTickTest(unittest.TestCase):
    def setUp(self) -> None:
        self.ns = load_watcher()
        self.check = self.ns["check_fw_settings"]

    def test_first_tick_captures_every_baseline(self) -> None:
        """mcs_tier must not wait for its round-robin turn (two intervals)."""
        responder(self.ns)
        self.check(0)
        self.assertCountEqual(
            self.ns["calls"], ["rate_adapt_cfg", "antcfg", "mcstiercfg"]
        )
        baselines = [t for _, t in self.ns["logged"] if "baseline" in t]
        self.assertEqual(len(baselines), 3)

    def test_later_ticks_poll_one_setting_each(self) -> None:
        responder(self.ns)
        self.check(0)
        for tick, expected in ((100, "rate_adapt_cfg"), (200, "antcfg")):
            self.ns["calls"].clear()
            self.check(tick)
            self.assertEqual(self.ns["calls"], [expected])

    def test_change_is_reported_once_and_silence_otherwise(self) -> None:
        table = dict(COMMAND_OUTPUT)
        responder(self.ns, output=table)
        self.ns["FWCFG_WATCH_TABLE"] = (
            ("rate_adapt", "rate_adapt_cfg", self.ns["_norm_rate_adapt"]),
        )
        self.check(0)
        self.check(100)
        table["rate_adapt_cfg"] = RATE_ADAPT_STATIC.replace("70", "30").replace(
            "90", "50"
        )
        self.check(200)
        self.check(300)
        levels = [level for level, _ in self.ns["logged"]]
        self.assertEqual(levels, ["info", "warn"])
        self.assertIn("SR 70/90 iv=10 -> SR 30/50 iv=10", self.ns["logged"][1][1])

    def test_total_outage_does_not_repeat_the_full_sweep(self) -> None:
        """Three blocking calls per period would stall the link.json producer."""
        responder(self.ns, missing=set(COMMAND_OUTPUT))
        self.check(0)
        self.assertEqual(len(self.ns["calls"]), 3)
        for tick in (100, 200, 300):
            self.ns["calls"].clear()
            self.check(tick)
            self.assertEqual(len(self.ns["calls"]), 1, f"tick {tick}")

    def test_baseline_that_failed_first_is_captured_after_recovery(self) -> None:
        responder(self.ns, missing={"antcfg", "mcstiercfg"})
        self.check(0)
        responder(self.ns)
        for tick in (100, 200, 300, 400):
            self.check(tick)
        captured = {
            text.split("baseline ")[1].split(":")[0]
            for _, text in self.ns["logged"]
            if "baseline" in text
        }
        self.assertSetEqual(captured, {"rate_adapt", "antcfg", "mcs_tier"})

    def test_watch_disabled_emits_nothing(self) -> None:
        self.ns["FWCFG_WATCH_SEC"] = 0
        responder(self.ns)
        self.check(0)
        self.assertEqual(self.ns["calls"], [])
        self.assertEqual(self.ns["logged"], [])

    def test_non_mlan_interface_is_skipped(self) -> None:
        """eth0 has no mlanutl."""
        self.ns["IFACE"] = "eth0"
        responder(self.ns)
        self.check(0)
        self.assertEqual(self.ns["calls"], [])


if __name__ == "__main__":
    unittest.main()
