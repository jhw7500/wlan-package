#!/usr/bin/env python3
"""
VHL Protocol Test Client
VHL 장치에서 무선기판 제어 데몬(vhld)을 테스트하는 클라이언트

Usage:
    python3 vhl_test_client.py [host] [port]
    python3 vhl_test_client.py 192.168.0.100 50000
"""

import socket
import struct
import sys
import time
from typing import Optional, Tuple

# VHL Protocol Constants
VHL_PROTO_VER = 1
VHL_CMD_REQUEST = 0x01
VHL_CMD_ACK = 0x02
VHL_CMD_INDICATION = 0x03

# Request IDs
VHL_REQ_GET_DEV_INFO = 0x0001
VHL_REQ_GET_DEV_STATUS = 0x0002
VHL_REQ_GET_WLAN_CONFIG = 0x0003
VHL_REQ_SET_PASSWORD = 0x8001
VHL_REQ_SET_IP_ADDR = 0x8002
VHL_REQ_SET_WLAN_CONFIG = 0x8003
VHL_REQ_SET_INDICATION = 0x8004
VHL_REQ_RESET = 0x80FF

# Device Type
VHL_DEV_SINGLE_STATION = 0x0001

# Device Status
VHL_STATUS_BOOTING = 0x0001
VHL_STATUS_SCANNING = 0x0002
VHL_STATUS_CONNECTED = 0x1000


class VHLClient:
    """VHL Protocol Client"""

    def __init__(self, host: str, port: int, timeout: float = 2.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock: Optional[socket.socket] = None
        self.seq_num = 1

    def connect(self) -> bool:
        """UDP 소켓 생성"""
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.settimeout(self.timeout)
            print(f"[INFO] UDP client ready: {self.host}:{self.port}")
            return True
        except Exception as e:
            print(f"[ERROR] socket creation failed: {e}")
            return False

    def close(self):
        """소켓 닫기"""
        if self.sock:
            self.sock.close()
            self.sock = None

    def _build_header(self, cmd_type: int, req_id: int, payload: bytes) -> bytes:
        """VHL 헤더 생성 (Big Endian)"""
        seq = self.seq_num
        self.seq_num += 1
        if self.seq_num > 0xFFFF:
            self.seq_num = 1

        # version(1) + cmd_type(1) + req_id(2) + seq_num(2) + length(2)
        header = struct.pack(
            ">BBHHH",
            VHL_PROTO_VER,
            cmd_type,
            req_id,
            seq,
            len(payload)
        )
        return header

    def _parse_header(self, data: bytes) -> Tuple[int, int, int, int, int]:
        """헤더 파싱 (version, cmd_type, req_id, seq_num, length)"""
        if len(data) < 8:
            raise ValueError("Packet too short for header")
        return struct.unpack(">BBHHH", data[:8])

    def send_request(self, req_id: int, payload: bytes = b"") -> Optional[bytes]:
        """Request 전송 및 응답 수신"""
        if not self.sock:
            print("[ERROR] Not connected")
            return None

        # 패킷 빌드
        header = self._build_header(VHL_CMD_REQUEST, req_id, payload)
        packet = header + payload

        # 전송
        try:
            self.sock.sendto(packet, (self.host, self.port))
            print(f"[SEND] ReqID=0x{req_id:04X}, Seq={self.seq_num-1}, Payload={len(payload)}B")
        except Exception as e:
            print(f"[ERROR] sendto failed: {e}")
            return None

        # 수신
        try:
            data, _ = self.sock.recvfrom(1500)
            version, cmd_type, resp_id, seq, length = self._parse_header(data)
            payload = data[8:8+length] if length > 0 else b""

            if cmd_type == VHL_CMD_ACK:
                result = struct.unpack(">H", payload[:2])[0] if len(payload) >= 2 else 0
                error = struct.unpack(">H", payload[2:4])[0] if len(payload) >= 4 else 0
                print(f"[RECV] Ack, ReqID=0x{resp_id:04X}, Seq={seq}, Result=0x{result:04X}, Error=0x{error:04X}")
                return data
            else:
                print(f"[RECV] Unknown cmd_type: {cmd_type}")
                return None
        except socket.timeout:
            print("[ERROR] Response timeout")
            return None
        except Exception as e:
            print(f"[ERROR] recvfrom failed: {e}")
            return None

    def parse_string(self, data: bytes, offset: int, max_len: int) -> Tuple[str, int]:
        """NULL 종료 문자열 파싱"""
        end = data.find(b'\x00', offset, offset + max_len)
        if end == -1:
            end = offset + max_len
        return data[offset:end].decode('utf-8', errors='ignore').rstrip('\x00'), offset + max_len

    def parse_mac(self, data: bytes, offset: int) -> str:
        """MAC 주소 파싱"""
        mac = data[offset:offset+6]
        return ":".join(f"{b:02X}" for b in mac)

    def parse_ip(self, data: bytes, offset: int) -> str:
        """IP 주소 파싱 (Big Endian)"""
        ip = struct.unpack(">I", data[offset:offset+4])[0]
        return f"{(ip>>24)&0xFF}.{(ip>>16)&0xFF}.{(ip>>8)&0xFF}.{ip&0xFF}"


# ========== Test Functions ==========

def test_get_device_info(client: VHLClient):
    """장치 정보 취득 테스트"""
    print("\n=== Test: Get Device Info (0x0001) ===")
    resp = client.send_request(VHL_REQ_GET_DEV_INFO)
    if not resp:
        return False

    try:
        _, _, _, _, length = client._parse_header(resp)
        payload = resp[8:8+length]

        offset = 0
        vendor, offset = client.parse_string(payload, offset, 64)
        model, offset = client.parse_string(payload, offset, 64)
        fw_ver, offset = client.parse_string(payload, offset, 64)
        hw_ver, offset = client.parse_string(payload, offset, 64)
        serial, offset = client.parse_string(payload, offset, 64)

        dev_type = struct.unpack(">H", payload[offset:offset+2])[0]; offset += 2
        eth_mac = client.parse_mac(payload, offset); offset += 6
        wlan_mac = client.parse_mac(payload, offset); offset += 6
        ip_addr = client.parse_ip(payload, offset); offset += 4
        subnet = client.parse_ip(payload, offset); offset += 4
        gateway = client.parse_ip(payload, offset); offset += 4

        print(f"  Vendor: {vendor}")
        print(f"  Model: {model}")
        print(f"  FW Version: {fw_ver}")
        print(f"  HW Version: {hw_ver}")
        print(f"  Serial: {serial}")
        print(f"  Device Type: 0x{dev_type:04X}")
        print(f"  ETH MAC: {eth_mac}")
        print(f"  WLAN MAC: {wlan_mac}")
        print(f"  IP: {ip_addr}")
        print(f"  Subnet: {subnet}")
        print(f"  Gateway: {gateway}")

        if dev_type == VHL_DEV_SINGLE_STATION:
            print("  [OK] Single Station device detected")
            return True
        else:
            print(f"  [WARN] Unknown device type: 0x{dev_type:04X}")
            return True
    except Exception as e:
        print(f"  [FAIL] Parse error: {e}")
        return False


def test_get_device_status(client: VHLClient):
    """장치 상태 취득 테스트"""
    print("\n=== Test: Get Device Status (0x0002) ===")
    resp = client.send_request(VHL_REQ_GET_DEV_STATUS)
    if not resp:
        return False

    try:
        _, _, _, _, length = client._parse_header(resp)
        payload = resp[8:8+length]

        dev_type = struct.unpack(">H", payload[0:2])[0]
        status = struct.unpack(">H", payload[2:4])[0]
        freq = struct.unpack(">H", payload[4:6])[0]
        snr = payload[6] if len(payload) > 6 else 0
        priority = struct.unpack(">H", payload[7:9])[0] if len(payload) > 7 else 0

        status_str = {VHL_STATUS_BOOTING: "Booting", VHL_STATUS_SCANNING: "Scanning", VHL_STATUS_CONNECTED: "Connected"}.get(status, f"0x{status:04X}")

        print(f"  Device Type: 0x{dev_type:04X}")
        print(f"  Status: {status_str} (0x{status:04X})")
        print(f"  Freq: {freq} MHz")
        print(f"  SNR: {snr} dB")
        print(f"  Priority: {priority} MHz")
        print("  [OK] Status received")
        return True
    except Exception as e:
        print(f"  [FAIL] Parse error: {e}")
        return False


def test_get_wlan_config(client: VHLClient):
    """무선 설정 취득 테스트"""
    print("\n=== Test: Get WLAN Config (0x0003) ===")
    resp = client.send_request(VHL_REQ_GET_WLAN_CONFIG)
    if not resp:
        return False

    try:
        _, _, _, _, length = client._parse_header(resp)
        payload = resp[8:8+length]

        dev_type = struct.unpack(">H", payload[0:2])[0]
        priority = struct.unpack(">H", payload[2:4])[0]
        freq = struct.unpack(">H", payload[4:6])[0]
        mode = payload[6]
        bw = payload[7]
        ssid, _ = client.parse_string(payload, 8, 32)

        mode_str = {0x01: "g", 0x02: "gn", 0x03: "ax", 0x05: "an", 0x06: "ac/ax", 0x08: "ax-6G"}.get(mode, f"0x{mode:02X}")
        bw_str = {0x01: "20MHz", 0x02: "HT40+", 0x03: "HT40-", 0x04: "80MHz", 0x05: "160MHz"}.get(bw, f"0x{bw:02X}")

        print(f"  Device Type: 0x{dev_type:04X}")
        print(f"  Priority: {priority} MHz")
        print(f"  Freq: {freq} MHz")
        print(f"  Mode: {mode_str} (0x{mode:02X})")
        print(f"  Bandwidth: {bw_str} (0x{bw:02X})")
        print(f"  SSID: {ssid}")
        print("  [OK] WLAN config received")
        return True
    except Exception as e:
        print(f"  [FAIL] Parse error: {e}")
        return False


def test_set_indication(client: VHLClient):
    """Indication 설정 테스트"""
    print("\n=== Test: Set Indication (0x8004) ===")

    # port=60000, mask=0x01 (초기화완료), period=10, IP=127.0.0.1
    payload = struct.pack(">HBB",
                          60000,  # udp_port
                          0x01,   # info_mask (초기화완료만)
                          10)     # keepalive_sec
    payload += struct.pack(">I", 0x7F000001)  # 127.0.0.1

    resp = client.send_request(VHL_REQ_SET_INDICATION, payload)
    if not resp:
        return False

    try:
        _, _, _, _, length = client._parse_header(resp)
        payload = resp[8:8+length]
        result = struct.unpack(">H", payload[0:2])[0]
        error = struct.unpack(">H", payload[2:4])[0]

        if result == 0:
            print(f"  [OK] Indication configured successfully")
            return True
        else:
            print(f"  [FAIL] Result: 0x{result:04X}, Error: 0x{error:04X}")
            return False
    except Exception as e:
        print(f"  [FAIL] Parse error: {e}")
        return False


def test_sequence_echo(client: VHLClient):
    """시퀀스 번호 에코 테스트"""
    print("\n=== Test: Sequence Number Echo ===")

    # 시퀀스 번호를 0xABCD로 설정하여 전송
    client.seq_num = 0xABCD
    resp = client.send_request(VHL_REQ_GET_DEV_INFO)

    if resp:
        _, _, _, seq, _ = client._parse_header(resp)
        if seq == 0xABCD:
            print(f"  [OK] Sequence number echoed correctly: 0x{seq:04X}")
            return True
        else:
            print(f"  [FAIL] Sequence mismatch: expected 0xABCD, got 0x{seq:04X}")
            return False
    return False


def main():
    if len(sys.argv) > 1:
        host = sys.argv[1]
    else:
        host = "127.0.0.1"

    if len(sys.argv) > 2:
        port = int(sys.argv[2])
    else:
        port = 50000

    print(f"=== VHL Protocol Test Client ===")
    print(f"Target: {host}:{port}")
    print(f"Press Ctrl+C to exit\n")

    client = VHLClient(host, port)
    if not client.connect():
        sys.exit(1)

    results = []

    try:
        # 테스트 실행
        results.append(("Sequence Echo", test_sequence_echo(client)))
        results.append(("Get Device Info", test_get_device_info(client)))
        results.append(("Get Device Status", test_get_device_status(client)))
        results.append(("Get WLAN Config", test_get_wlan_config(client)))
        results.append(("Set Indication", test_set_indication(client)))

        # 결과 요약
        print("\n" + "="*50)
        print("=== Test Results ===")
        passed = sum(1 for _, r in results if r)
        failed = len(results) - passed

        for name, result in results:
            status = "[PASS]" if result else "[FAIL]"
            print(f"  {status} {name}")

        print(f"\nTotal: {passed} passed, {failed} failed")
        print("="*50)

        sys.exit(0 if failed == 0 else 1)

    except KeyboardInterrupt:
        print("\n\n[INFO] Interrupted by user")
    finally:
        client.close()


if __name__ == "__main__":
    main()
