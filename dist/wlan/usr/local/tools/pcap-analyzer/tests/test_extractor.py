import importlib
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

parse_tsv_line = importlib.import_module("extractor").parse_tsv_line


class ParseTsharkLineTests(unittest.TestCase):
    def test_parse_tsv_line_reads_icmp_ident_and_icmp_seq(self):
        line = (
            "3\t1772081362.0\tFeb 26, 2026 13:49:22.006610023 KST\tFalse\t0x0028\tICMP\t222"
            "\t15\t-36\t00:50:43:18:fe:01\t00:80:4c:e1:09:cb\t00:80:4c:e1:09:cb"
            "\t192.168.0.21\t192.168.0.10\t8\t\t0\t\t99\t4660\t7"
        )

        frame = parse_tsv_line(line)

        self.assertIsNotNone(frame)
        assert frame is not None
        self.assertEqual(getattr(frame, "icmp_ident", ""), "4660")
        self.assertEqual(frame.icmp_seq, "7")

    def test_parse_tsv_line_normalizes_new_tshark_retry_and_subtype_formats(self):
        line = (
            "1\t1772081360.0\tFeb 26, 2026 13:49:20.006610023 KST\tTrue\t0x0028\tICMP\t222"
            "\t15\t-36\t00:50:43:18:fe:01\t00:80:4c:e1:09:cb\t00:80:4c:e1:09:cb"
            "\t192.168.0.21\t192.168.0.10\t0\t\t0\t\t99"
        )

        frame = parse_tsv_line(line)

        self.assertIsNotNone(frame)
        assert frame is not None
        self.assertTrue(frame.retry)
        self.assertEqual(frame.subtype, "40")
        self.assertTrue(frame.is_data)
        self.assertEqual(frame.subtype_name, "QoS Data")

    def test_parse_tsv_line_keeps_legacy_retry_and_subtype_formats_compatible(self):
        line = (
            "2\t1772081361.0\tFeb 26, 2026 13:49:21.006610023 KST\t1\t8\tEAPOL\t120"
            "\t\t-45\t00:80:4c:e1:09:cb\tff:ff:ff:ff:ff:ff\t00:80:4c:e1:09:cb"
            "\t\t\t\t\t\t\t100"
        )

        frame = parse_tsv_line(line)

        self.assertIsNotNone(frame)
        assert frame is not None
        self.assertTrue(frame.retry)
        self.assertEqual(frame.subtype, "8")
        self.assertTrue(frame.is_mgmt)
        self.assertEqual(frame.subtype_name, "Beacon")


def _assoc_columns():
    """연결 응답 한 줄의 tshark 열(가짜 MAC). 21~22번이 #460 의 새 열이다."""
    return [
        "4", "1772081363.0", "Feb 26, 2026 13:49:23.006610023 KST", "0", "0x0001", "802.11",
        "120", "", "-40", "02:00:00:00:0a:01", "02:00:00:00:0b:01", "02:00:00:00:0a:01",
        "", "", "", "", "", "", "0", "", "", "0x0000", "",
    ]


class AssocFourWayFieldTests(unittest.TestCase):
    def test_new_fields_are_appended_after_the_legacy_columns(self):
        fields = importlib.import_module("extractor").TSHARK_FIELDS
        self.assertEqual(fields[20], "icmp.seq")
        self.assertEqual(fields[21:], ["wlan.fixed.status_code", "wlan_rsna_eapol.keydes.msgnr"])

    def test_parse_tsv_line_reads_hex_status_code(self):
        frame = parse_tsv_line("\t".join(_assoc_columns()))

        self.assertIsNotNone(frame)
        assert frame is not None
        self.assertEqual(frame.subtype, "1")
        self.assertEqual(frame.status_code, "0")
        self.assertEqual(frame.eapol_msgnr, "")

    def test_parse_tsv_line_reads_eapol_message_number(self):
        cols = _assoc_columns()
        cols[4], cols[5], cols[21], cols[22] = "0x0028", "EAPOL", "", "3"

        frame = parse_tsv_line("\t".join(cols))

        assert frame is not None
        self.assertEqual(frame.eapol_msgnr, "3")
        self.assertEqual(frame.status_code, "")

    def test_parse_tsv_line_without_new_columns_leaves_them_empty(self):
        frame = parse_tsv_line("\t".join(_assoc_columns()[:21]))

        assert frame is not None
        self.assertEqual((frame.status_code, frame.eapol_msgnr), ("", ""))


if __name__ == "__main__":
    unittest.main()
