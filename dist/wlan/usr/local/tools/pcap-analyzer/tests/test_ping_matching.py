import importlib
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

Frame = importlib.import_module("models").Frame
ping_rtt = importlib.import_module("analyzers.ping_rtt")
ping_loss = importlib.import_module("analyzers.ping_loss")
diagnosis = importlib.import_module("analyzers.diagnosis")
roaming_impact = importlib.import_module("analyzers.roaming_impact")


def _frame(**kwargs):
    defaults = dict(
        number=1,
        epoch=0.0,
        timestamp="Feb 26, 2026 13:49:20.000000000 KST",
        retry=False,
        subtype="40",
        protocol="ICMP",
        length=100,
        mcs="",
        rssi="-40",
        ta="00:50:43:18:fe:01",
        ra="00:80:4c:e1:09:cb",
        ip_src="192.168.0.10",
        ip_dst="192.168.0.21",
        icmp_type="",
        arp_opcode="",
        tcp_len="",
        tcp_flags="",
        seq="1",
        icmp_seq="",
        bssid="00:80:4c:e1:09:cb",
    )
    defaults.update(kwargs)
    icmp_ident = defaults.pop("icmp_ident", "")
    frame = Frame(**defaults)
    setattr(frame, "icmp_ident", icmp_ident)
    return frame


def _overlapping_ping_frames():
    return [
        _frame(number=1, epoch=1.0, icmp_type="8", icmp_seq="7", icmp_ident="100"),
        _frame(number=2, epoch=1.1, icmp_type="8", icmp_seq="7", icmp_ident="200"),
        _frame(
            number=3,
            epoch=1.2,
            ta="00:80:4c:e1:09:cb",
            ra="00:50:43:18:fe:01",
            ip_src="192.168.0.21",
            ip_dst="192.168.0.10",
            icmp_type="0",
            icmp_seq="7",
            icmp_ident="100",
        ),
        _frame(
            number=4,
            epoch=1.3,
            ta="00:80:4c:e1:09:cb",
            ra="00:50:43:18:fe:01",
            ip_src="192.168.0.21",
            ip_dst="192.168.0.10",
            icmp_type="0",
            icmp_seq="7",
            icmp_ident="200",
        ),
    ]


class PingMatchingTests(unittest.TestCase):
    def setUp(self):
        self.roles = {
            "00:50:43:18:fe:01": {"role": "STA", "name": "STA1(fe01)"},
            "00:80:4c:e1:09:cb": {"role": "AP", "name": "AP1(09cb)"},
        }

    def test_ping_rtt_matches_two_inflight_requests_with_same_seq(self):
        section = ping_rtt.analyze(_overlapping_ping_frames(), self.roles)
        self.assertIn("ping 2쌍", section.summary)

    def test_ping_loss_does_not_report_loss_when_ident_differs(self):
        section = ping_loss.analyze(_overlapping_ping_frames(), self.roles)
        self.assertEqual(section.summary, "ping loss 없음")

    def test_diagnosis_counts_both_ping_replies(self):
        section = diagnosis.analyze(_overlapping_ping_frames(), self.roles)
        text = "\n".join(section.lines)
        self.assertIn("Ping: 2성공/0손실", text)

    def test_roaming_impact_uses_ident_aware_ping_matching(self):
        frames = [
            _frame(
                number=10,
                epoch=0.0,
                subtype="11",
                protocol="802.11",
                icmp_type="",
                ip_src="",
                ip_dst="",
                icmp_seq="",
                icmp_ident="",
                ta="00:50:43:18:fe:01",
                ra="00:80:4c:e1:09:cb",
            ),
            _frame(
                number=11,
                epoch=0.01,
                subtype="2",
                protocol="802.11",
                icmp_type="",
                ip_src="",
                ip_dst="",
                icmp_seq="",
                icmp_ident="",
                ta="00:50:43:18:fe:01",
                ra="00:80:4c:e1:09:cb",
            ),
            *_overlapping_ping_frames(),
        ]
        section = roaming_impact.analyze(frames, self.roles)
        text = "\n".join(section.lines)
        self.assertIn("Ping: 성공 2, 손실 없음", text)
        self.assertEqual(section.summary, "로밍 1건, 문제 0건")


if __name__ == "__main__":
    unittest.main()
