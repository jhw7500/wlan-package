import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from models import Frame


def _frame(**kwargs):
    defaults = dict(
        number=1, epoch=0, timestamp="", retry=False, subtype="40",
        protocol="", length=0, mcs="", rssi="", ta="", ra="",
        ip_src="", ip_dst="", icmp_type="", arp_opcode="",
        tcp_len="", tcp_flags="", seq="",
    )
    defaults.update(kwargs)
    return Frame(**defaults)


def test_frame_creation():
    f = _frame(number=100, retry=True, subtype="40")
    assert f.number == 100
    assert f.retry is True
    assert f.is_data is True


def test_frame_is_arp():
    f = _frame(arp_opcode="1")
    assert f.is_arp is True
    assert f.is_control_traffic is True


def test_frame_is_icmp():
    f = _frame(icmp_type="8")
    assert f.is_icmp_request is True
    assert f.is_control_traffic is True


def test_frame_is_pure_tcp_ack():
    f = _frame(tcp_len="0", tcp_flags="0x00000010")
    assert f.is_pure_tcp_ack is True
    assert f.is_control_traffic is True


def test_frame_rssi_first():
    assert _frame(rssi="-36,-37,-43").rssi_first == -36
    assert _frame(rssi="").rssi_first is None


def test_frame_mcs_int():
    assert _frame(mcs="15").mcs_int == 15
    assert _frame(mcs="").mcs_int is None


def test_frame_time_short():
    f = _frame(timestamp="Feb 26, 2026 13:49:20.006610023 KST")
    assert "13:49:20" in f.time_short


def test_frame_roaming_related():
    assert _frame(subtype="11").is_roaming_related is True  # Auth
    assert _frame(subtype="2").is_roaming_related is True   # ReassocReq
    assert _frame(protocol="EAPOL").is_roaming_related is True
    assert _frame(subtype="40").is_roaming_related is False
