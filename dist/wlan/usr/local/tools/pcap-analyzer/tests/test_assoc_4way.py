"""연결 응답 → EAPOL 4-way 판정 분석기(#460).

Frame 목록 시험은 시도 묶음·재전송·ACK·판정 규칙을 고정한다. 합성 pcap 시험은
시험 중에 만든 pcap(linktype 105, FCS 없음, 가짜 MAC)을 실제 tshark 로 돌려
추출 필드(상태 코드·EAPOL 메시지 번호)부터 판정까지 확인한다. tshark 가 없으면
건너뛰지 않고 실패한다.
"""
import importlib
import os
import shutil
import struct
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

Frame = importlib.import_module("models").Frame
extractor = importlib.import_module("extractor")
assoc_4way = importlib.import_module("analyzers.assoc_4way")

AP = "02:00:00:00:0a:01"
STA = "02:00:00:00:0b:01"
T0 = 1_700_000_000.0


def _frame(number, at, subtype, ta="", ra="", retry=False, seq="", status="", msgnr="", protocol=""):
    return Frame(
        number=number, epoch=T0 + at, timestamp=f"Nov 14, 2023 22:13:{at:09.6f} UTC",
        retry=retry, subtype=subtype, protocol=protocol, length=100, mcs="", rssi="",
        ta=ta, ra=ra, ip_src="", ip_dst="", icmp_type="", arp_opcode="", tcp_len="",
        tcp_flags="", seq=seq, bssid=AP, status_code=status, eapol_msgnr=msgnr,
    )


def _resp(number, at, retry=False, seq="0", status="0"):
    return _frame(number, at, "1", ta=AP, ra=STA, retry=retry, seq=seq, status=status)


def _ack(number, at, ra=AP):
    return _frame(number, at, "29", ra=ra)


def _eapol(number, at, msg):
    down = msg in ("1", "3")
    return _frame(number, at, "40", ta=AP if down else STA, ra=STA if down else AP,
                  msgnr=msg, protocol="EAPOL")


def _tail(number, at):
    """판정 창(10초)을 다 채우도록 캡처를 잇는 프레임."""
    return _frame(number, at, "8", ta=AP, ra="ff:ff:ff:ff:ff:ff")


def _handshake(start_number, start_at, msgs):
    return [_eapol(start_number + i, start_at + 0.002 * i, m) for i, m in enumerate(msgs)]


def test_responses_with_seq_zero_split_by_retry_bit_seq_change_and_gap():
    frames = [
        _resp(1, 0.000), _ack(2, 0.0001),
        _resp(3, 0.010, retry=True), _ack(4, 0.0101),
        _resp(5, 0.020, retry=True), _ack(6, 0.0201),
        _resp(7, 0.300), _ack(8, 0.3001),                      # 0.5초 안, 같은 seq=0 이지만 Retry=0 → 새 시도
        _resp(9, 0.310, retry=True), _ack(10, 0.3101),
        _resp(11, 0.320, retry=True, seq="1"),                  # seq 변화 → 새 시도
        _ack(12, 0.3201),
        _resp(13, 1.000, retry=True, seq="1"), _ack(14, 1.0001),  # 0.5초 넘는 간격 → 새 시도
        _tail(15, 20.0),
    ]
    attempts = assoc_4way.collect_attempts(frames)
    assert [a.first.number for a in attempts] == [1, 7, 11, 13]
    assert [a.retransmissions for a in attempts] == [2, 1, 1, 1]
    assert [len(a.copies) for a in attempts] == [3, 2, 1, 1]


@pytest.mark.parametrize("msgs,verdict,missing", [
    ("1234", "OK", []),
    ("134", "OK", ["M2"]),
    ("123", "STA_NO_M4", []),
    ("12", "AP_NO_M3", []),
    ("1", "STA_NO_M2", []),
    ("24", "OK", ["M1", "M3"]),
    ("", "AP_NO_M1", []),
])
def test_verdict_follows_highest_message_and_marks_lower_gaps(msgs, verdict, missing):
    frames = [_resp(1, 0.0), _ack(2, 0.0001)] + _handshake(3, 0.005, msgs) + [_tail(20, 20.0)]
    [attempt] = assoc_4way.collect_attempts(frames)
    assert attempt.verdict == verdict
    assert attempt.missing == missing


def test_message_times_are_milliseconds_after_the_first_response_copy():
    frames = [_resp(1, 0.0), _ack(2, 0.0001), _resp(3, 0.004, retry=True), _ack(4, 0.0041)]
    frames += _handshake(5, 0.009, "1234") + [_tail(20, 20.0)]
    [attempt] = assoc_4way.collect_attempts(frames)
    assert attempt.msg_ms["1"] == pytest.approx(9.0, abs=0.01)
    assert attempt.msg_ms["4"] == pytest.approx(15.0, abs=0.01)


def test_eapol_after_the_next_attempt_belongs_to_the_next_attempt():
    frames = [_resp(1, 0.0), _ack(2, 0.0001), _resp(3, 1.2), _ack(4, 1.2001)]
    frames += _handshake(5, 1.205, "1234") + [_tail(20, 20.0)]
    first, second = assoc_4way.collect_attempts(frames)
    assert first.verdict == "AP_NO_M1"
    assert second.verdict == "OK"
    assert second.msg_ms["1"] == pytest.approx(5.0, abs=0.01)


def test_eapol_in_the_wrong_direction_or_for_another_sta_is_ignored():
    frames = [
        _resp(1, 0.0), _ack(2, 0.0001),
        _frame(3, 0.005, "40", ta=STA, ra=AP, msgnr="1", protocol="EAPOL"),
        _frame(4, 0.006, "40", ta=AP, ra="02:00:00:00:0b:02", msgnr="1", protocol="EAPOL"),
        _tail(5, 20.0),
    ]
    [attempt] = assoc_4way.collect_attempts(frames)
    assert attempt.verdict == "AP_NO_M1"


def test_messages_after_ten_seconds_are_outside_the_window():
    frames = [_resp(1, 0.0), _ack(2, 0.0001), _eapol(3, 10.5, "1"), _tail(4, 20.0)]
    [attempt] = assoc_4way.collect_attempts(frames)
    assert attempt.verdict == "AP_NO_M1"


def test_capture_ending_inside_the_window_defers_the_verdict():
    frames = [_resp(1, 0.0), _ack(2, 0.0001), _eapol(3, 0.005, "1")]
    [attempt] = assoc_4way.collect_attempts(frames)
    assert attempt.verdict == "CAPTURE_END"

    frames = [_resp(1, 0.0), _ack(2, 0.0001)] + _handshake(3, 0.005, "1234")
    [attempt] = assoc_4way.collect_attempts(frames)
    assert attempt.verdict == "OK"


def test_nonzero_status_is_rejected():
    frames = [_resp(1, 0.0, status="17"), _ack(2, 0.0001), _tail(3, 20.0)]
    [attempt] = assoc_4way.collect_attempts(frames)
    assert attempt.verdict == "ASSOC_REJECTED"
    assert attempt.status == "17"


def test_ack_is_the_next_frame_number_addressed_to_the_ap():
    frames = [
        _resp(1, 0.00), _ack(2, 0.0001),                                     # ACK
        _resp(3, 0.01, retry=True), _frame(4, 0.0101, "40", ta=STA, ra=AP),  # 다음 프레임이 ACK 아님
        _resp(5, 0.02, retry=True), _ack(6, 0.0201, ra=STA),                 # 다른 수신자 ACK
        _resp(7, 0.03, retry=True), _ack(9, 0.0301),                         # 다음 번호(8)가 캡처에 없음
        _tail(10, 20.0),
    ]
    [attempt] = assoc_4way.collect_attempts(frames)
    assert (attempt.acked, attempt.ack_checked, attempt.ack_unknown) == (1, 3, 1)


def test_section_title_summary_and_detail_line():
    frames = [_resp(1, 0.0), _ack(2, 0.0001)] + _handshake(3, 0.005, "1234")
    frames += [_resp(7, 1.2), _ack(8, 1.2001), _resp(9, 1.21, retry=True), _ack(10, 1.2101),
               _tail(11, 20.0)]
    section = assoc_4way.analyze(frames, {}, None)
    assert section.title == "13. 연결·4-way 판정"
    assert section.summary == "연결 응답 2건: OK 1, AP_NO_M1 1, 응답 재전송 평균 0.5회"
    assert sum("AP_NO_M1" in line and "#7" in line for line in section.lines) == 1


def test_no_association_response():
    section = assoc_4way.analyze([_tail(1, 0.0)], {}, None)
    assert section.summary == "연결 응답 없음"


# ---------------------------------------------------------------- 합성 pcap

def _mac(text):
    return bytes(int(part, 16) for part in text.split(":"))


def _seq_ctrl(seq):
    return struct.pack("<H", seq << 4)


def _assoc_resp(seq, retry, status=0):
    header = bytes([0x10, 0x08 if retry else 0x00]) + b"\x3a\x01" + _mac(STA) + _mac(AP) + _mac(AP)
    body = struct.pack("<HHH", 0x0431, status, 0xC001)
    body += bytes([0x01, 0x08, 0x82, 0x84, 0x8B, 0x96, 0x0C, 0x12, 0x18, 0x24])
    return header + _seq_ctrl(seq) + body


def _ack_frame(ra):
    return bytes([0xD4, 0x00, 0x00, 0x00]) + _mac(ra)


RSN_IE = bytes([0x30, 0x14, 0x01, 0x00, 0x00, 0x0F, 0xAC, 0x04, 0x01, 0x00, 0x00, 0x0F, 0xAC, 0x04,
                0x01, 0x00, 0x00, 0x0F, 0xAC, 0x02, 0x00, 0x00])
KEY_INFO = {"1": 0x008A, "2": 0x010A, "3": 0x13CA, "4": 0x030A}  # 실제 캡처의 네 조합


def _eapol_frame(msg, seq):
    down = msg in ("1", "3")
    if down:   # Data, FromDS: DA=STA, BSSID=AP, SA=AP
        header = bytes([0x08, 0x02, 0x00, 0x00]) + _mac(STA) + _mac(AP) + _mac(AP)
    else:      # Data, ToDS: BSSID=AP, SA=STA, DA=AP
        header = bytes([0x08, 0x01, 0x00, 0x00]) + _mac(AP) + _mac(STA) + _mac(AP)
    nonce = {"1": bytes(range(1, 33)), "2": bytes([0x11] * 32),
             "3": bytes(range(1, 33)), "4": bytes(32)}[msg]
    key_data = {"1": b"", "2": RSN_IE, "3": bytes([0x55] * 56), "4": b""}[msg]
    descriptor = (bytes([0x02])
                  + struct.pack(">HHQ", KEY_INFO[msg], 16 if down else 0, 1 if msg in "12" else 2)
                  + nonce + bytes(16) + bytes(8) + bytes(8)
                  + (bytes(16) if msg == "1" else bytes([0x22] * 16))
                  + struct.pack(">H", len(key_data)) + key_data)
    eapol = bytes([0x02, 0x03]) + struct.pack(">H", len(descriptor)) + descriptor
    llc = bytes([0xAA, 0xAA, 0x03, 0x00, 0x00, 0x00, 0x88, 0x8E])
    return header + _seq_ctrl(seq) + llc + eapol


def _write_pcap(path, records):
    with open(path, "wb") as out:
        out.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 105))
        for at, data in records:
            micros = round((T0 + at) * 1_000_000)
            out.write(struct.pack("<IIII", micros // 1_000_000, micros % 1_000_000, len(data), len(data)))
            out.write(data)


def _synthetic_records():
    return [
        (0.000, _assoc_resp(0, False)), (0.0001, _ack_frame(AP)),
        (0.010, _assoc_resp(0, True)), (0.0101, _ack_frame(AP)),
        (0.015, _eapol_frame("1", 1)), (0.0151, _ack_frame(AP)),
        (0.017, _eapol_frame("2", 1)), (0.0171, _ack_frame(STA)),
        (0.019, _eapol_frame("3", 2)), (0.0191, _ack_frame(AP)),
        (0.021, _eapol_frame("4", 2)), (0.0211, _ack_frame(STA)),
        # 두 번째 시도: seq 도 0, 응답 재전송 2회, M1 없음
        (1.200, _assoc_resp(0, False)), (1.2001, _ack_frame(AP)),
        (1.210, _assoc_resp(0, True)), (1.2101, _ack_frame(AP)),
        (1.220, _assoc_resp(0, True)), (1.2201, _ack_frame(AP)),
        (12.000, _ack_frame(AP)),  # 판정 창(10초)을 다 채우도록 캡처를 잇는다
    ]


@pytest.fixture
def synthetic_pcap(tmp_path, monkeypatch):
    assert shutil.which("tshark"), "tshark 가 없다 — 이 시험은 실제 tshark 출력으로만 판정을 확인한다"
    config_dir = tmp_path / "wireshark-config"
    config_dir.mkdir()
    monkeypatch.setenv("WIRESHARK_CONFIG_DIR", str(config_dir))
    pcap = tmp_path / "synthetic.pcap"
    _write_pcap(pcap, _synthetic_records())
    return pcap


def test_synthetic_pcap_through_real_tshark(synthetic_pcap):
    records = _synthetic_records()

    frames = extractor.extract_frames(str(synthetic_pcap))

    assert len(frames) == len(records)
    assert [f.status_code for f in frames if f.subtype == "1"] == ["0"] * 5
    assert [f.eapol_msgnr for f in frames if f.eapol_msgnr] == ["1", "2", "3", "4"]
    first, second = assoc_4way.collect_attempts(frames)
    assert (first.verdict, first.retransmissions, first.acked, first.ack_checked) == ("OK", 1, 2, 2)
    assert (second.verdict, second.retransmissions, second.acked, second.ack_checked) == ("AP_NO_M1", 2, 3, 3)


def test_cli_brief_run_reports_the_new_section(synthetic_pcap, tmp_path):
    tool = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "pcap_analyzer.py"))
    report = tmp_path / "report.txt"

    run = subprocess.run(
        [sys.executable, tool, str(synthetic_pcap), "--brief", "-o", str(report)],
        cwd=tmp_path, capture_output=True, text=True, timeout=120,
    )

    assert run.returncode == 0, run.stderr
    summary = "[13. 연결·4-way 판정] 연결 응답 2건: OK 1, AP_NO_M1 1, 응답 재전송 평균 1.5회"
    assert summary in run.stdout
    assert summary.split("] ")[1] in report.read_text(encoding="utf-8")
