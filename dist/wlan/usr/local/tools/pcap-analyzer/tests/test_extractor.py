import importlib
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

parse_tsv_line = importlib.import_module("extractor").parse_tsv_line


class ParseTsharkLineTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
