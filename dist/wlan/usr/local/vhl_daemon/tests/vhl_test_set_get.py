#!/usr/bin/env python3
"""
VHL Protocol Set/Get Individual Test
각 Set 커맨드 실행 후 Get으로 변경 사항을 검증

Usage:
    python3 vhl_test_set_get.py [host] [port]
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

VHL_REQ_GET_DEV_INFO = 0x0001
VHL_REQ_GET_DEV_STATUS = 0x0002
VHL_REQ_GET_WLAN_CONFIG = 0x0003
VHL_REQ_SET_PASSWORD = 0x8001
VHL_REQ_SET_IP_ADDR = 0x8002
VHL_REQ_SET_WLAN_CONFIG = 0x8003
VHL_REQ_SET_INDICATION = 0x8004
VHL_REQ_RESET = 0x80FF

VHL_DEV_SINGLE_STATION = 0x0001
VHL_STATUS_CONNECTED = 0x1000


class VHLClient:
    def __init__(self, host: str, port: int, timeout: float = 2.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock: Optional[socket.socket] = None
        self.seq_num = 1

    def connect(self) -> bool:
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.settimeout(self.timeout)
            return True
        except Exception as e:
            print(f"[ERROR] socket create failed: {e}")
            return False

    def close(self):
        if self.sock:
            self.sock.close()

    def _build_header(self, cmd_type: int, req_id: int, payload: bytes) -> bytes:
        seq = self.seq_num
        self.seq_num += 1
        if self.seq_num > 0xFFFF:
            self.seq_num = 1
        return struct.pack(">BBHHH", VHL_PROTO_VER, cmd_type, req_id, seq, len(payload)) + payload

    def _parse_header(self, data: bytes) -> Tuple[int, int, int, int, int]:
        if len(data) < 8:
            raise ValueError("Packet too short")
        return struct.unpack(">BBHHH", data[:8])

    def send_request(self, req_id: int, payload: bytes = b"") -> Optional[Tuple[bytes, bytes]]:
        """Request 전송, (raw_response, payload) 반환 또는 None"""
        if not self.sock:
            return None

        packet = self._build_header(VHL_CMD_REQUEST, req_id, payload)
        try:
            self.sock.sendto(packet, (self.host, self.port))
        except Exception as e:
            print(f"  [ERROR] sendto: {e}")
            return None

        try:
            data, _ = self.sock.recvfrom(1500)
            _, _, _, _, length = self._parse_header(data)
            payload = data[8:8+length] if length > 0 else b""
            return data, payload
        except socket.timeout:
            print("  [ERROR] Response timeout")
            return None
        except Exception as e:
            print(f"  [ERROR] recvfrom: {e}")
            return None


def print_separator(title: str):
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)


def parse_string(data: bytes, offset: int, max_len: int) -> Tuple[str, int]:
    end = data.find(b'\x00', offset, offset + max_len)
    if end == -1:
        end = offset + max_len
    return data[offset:end].decode('utf-8', errors='ignore').rstrip('\x00'), offset + max_len


def parse_mac(data: bytes, offset: int) -> str:
    mac = data[offset:offset+6]
    return ":".join(f"{b:02X}" for b in mac)


def parse_ip(data: bytes, offset: int) -> str:
    ip = struct.unpack(">I", data[offset:offset+4])[0]
    return f"{(ip>>24)&0xFF}.{(ip>>16)&0xFF}.{(ip>>8)&0xFF}.{ip&0xFF}"


# ========== Get Functions ==========

def get_device_info(client: VHLClient) -> dict:
    """장치 정보 조회"""
    print("\n[GET] Device Info (0x0001)")
    resp = client.send_request(VHL_REQ_GET_DEV_INFO)
    if not resp:
        return None

    _, payload = resp
    offset = 0
    vendor, offset = parse_string(payload, offset, 64)
    model, offset = parse_string(payload, offset, 64)
    fw_ver, offset = parse_string(payload, offset, 64)
    hw_ver, offset = parse_string(payload, offset, 64)
    serial, offset = parse_string(payload, offset, 64)
    dev_type = struct.unpack(">H", payload[offset:offset+2])[0]; offset += 2
    eth_mac = parse_mac(payload, offset); offset += 6
    wlan_mac = parse_mac(payload, offset); offset += 6
    ip_addr = parse_ip(payload, offset); offset += 4
    subnet = parse_ip(payload, offset); offset += 4
    gateway = parse_ip(payload, offset); offset += 4

    info = {
        'vendor': vendor, 'model': model, 'fw_ver': fw_ver, 'hw_ver': hw_ver,
        'serial': serial, 'dev_type': dev_type, 'eth_mac': eth_mac,
        'wlan_mac': wlan_mac, 'ip_addr': ip_addr, 'subnet': subnet, 'gateway': gateway
    }
    print(f"  Vendor: {vendor}")
    print(f"  Model: {model}")
    print(f"  FW: {fw_ver}, HW: {hw_ver}")
    print(f"  Serial: {serial}")
    print(f"  ETH MAC: {eth_mac}")
    print(f"  WLAN MAC: {wlan_mac}")
    print(f"  IP: {ip_addr}/{subnet}")
    print(f"  Gateway: {gateway}")
    return info


def get_device_status(client: VHLClient) -> dict:
    """장치 상태 조회"""
    print("\n[GET] Device Status (0x0002)")
    resp = client.send_request(VHL_REQ_GET_DEV_STATUS)
    if not resp:
        return None

    _, payload = resp
    dev_type = struct.unpack(">H", payload[0:2])[0]
    status = struct.unpack(">H", payload[2:4])[0]
    freq = struct.unpack(">H", payload[4:6])[0]
    snr = payload[6]
    priority = struct.unpack(">H", payload[7:9])[0]

    status_str = {VHL_STATUS_CONNECTED: "Connected"}.get(status, f"0x{status:04X}")

    result = {
        'dev_type': dev_type, 'status': status, 'freq': freq, 'snr': snr, 'priority': priority
    }
    print(f"  Status: {status_str} (0x{status:04X})")
    print(f"  Freq: {freq} MHz, SNR: {snr} dB")
    print(f"  Priority: {priority} MHz")
    return result


def get_wlan_config(client: VHLClient) -> dict:
    """무선 설정 조회"""
    print("\n[GET] WLAN Config (0x0003)")
    resp = client.send_request(VHL_REQ_GET_WLAN_CONFIG)
    if not resp:
        return None

    _, payload = resp
    dev_type = struct.unpack(">H", payload[0:2])[0]
    priority = struct.unpack(">H", payload[2:4])[0]
    freq = struct.unpack(">H", payload[4:6])[0]
    mode = payload[6]
    bw = payload[7]
    ssid, _ = parse_string(payload, 8, 32)

    mode_str = {0x01: "g", 0x02: "gn", 0x03: "ax", 0x05: "an", 0x06: "ac/ax", 0x08: "ax-6G"}.get(mode, f"0x{mode:02X}")
    bw_str = {0x01: "20MHz", 0x02: "HT40+", 0x03: "HT40-", 0x04: "80MHz", 0x05: "160MHz"}.get(bw, f"0x{bw:02X}")

    result = {'priority': priority, 'freq': freq, 'mode': mode, 'bw': bw, 'ssid': ssid}
    print(f"  Priority: {priority} MHz")
    print(f"  Freq: {freq} MHz")
    print(f"  Mode: {mode_str} (0x{mode:02X})")
    print(f"  Bandwidth: {bw_str} (0x{bw:02X})")
    print(f"  SSID: '{ssid}'")
    return result


# ========== Set Functions ==========

def set_password(client: VHLClient, old_pw: str, new_pw: str) -> bool:
    """패스워드 설정 (스텁 - 실제 wpa_supplicant 변경 없음)"""
    print(f"\n[SET] Password (0x8001)")
    print(f"  Old: '{old_pw}' -> New: '{new_pw}'")

    payload = old_pw.encode('utf-8').ljust(64, b'\x00')
    payload += new_pw.encode('utf-8').ljust(64, b'\x00')

    resp = client.send_request(VHL_REQ_SET_PASSWORD, payload)
    if not resp:
        return False

    _, payload = resp
    result = struct.unpack(">H", payload[0:2])[0]
    error = struct.unpack(">H", payload[2:4])[0]

    if result == 0:
        print(f"  [OK] Password changed (stub - wpa_supplicant not updated)")
        return True
    else:
        print(f"  [FAIL] Result: 0x{result:04X}, Error: 0x{error:04X}")
        return False


def set_ip_addr(client: VHLClient, ip: str, mask: str, gw: str) -> bool:
    """IP 주소 설정 (리셋 없이 즉시 적용)"""
    print(f"\n[SET] IP Address (0x8002)")
    print(f"  IP: {ip}, Mask: {mask}, Gateway: {gw}")

    # IP 파싱
    def ip_to_bytes(ip_str):
        parts = ip_str.split('.')
        return struct.pack(">BBBB", int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3]))

    payload = ip_to_bytes(ip)
    payload += ip_to_bytes(mask)
    payload += ip_to_bytes(gw)

    resp = client.send_request(VHL_REQ_SET_IP_ADDR, payload)
    if not resp:
        return False

    _, payload = resp
    result = struct.unpack(">H", payload[0:2])[0]
    error = struct.unpack(">H", payload[2:4])[0]

    if result == 0:
        print(f"  [OK] IP changed (appplied immediately)")
        time.sleep(1)  # 적용 대기
        return True
    else:
        print(f"  [FAIL] Result: 0x{result:04X}, Error: 0x{error:04X}")
        return False


def set_wlan_config(client: VHLClient, ssid: str, freq_mhz: int) -> bool:
    """무선 설정 (SSID, 주파수) - 리셋 없이 적용"""
    print(f"\n[SET] WLAN Config (0x8003)")
    print(f"  SSID: '{ssid}', Freq: {freq_mhz} MHz")

    # Payload: dev_type(2) + priority(2) + freq(2) + mode(1) + bw(1) + ssid(32)
    payload = struct.pack(">HHHBB",
                          VHL_DEV_SINGLE_STATION,
                          freq_mhz,  # priority
                          freq_mhz,  # freq
                          0x06,      # mode: 5GHz ac/ax
                          0x04)      # bw: 80MHz
    payload += ssid.encode('utf-8').ljust(32, b'\x00')

    resp = client.send_request(VHL_REQ_SET_WLAN_CONFIG, payload)
    if not resp:
        return False

    _, payload = resp
    result = struct.unpack(">H", payload[0:2])[0]
    error = struct.unpack(">H", payload[2:4])[0]

    if result == 0:
        print(f"  [OK] WLAN config changed")
        time.sleep(2)  # 재연결 대기
        return True
    else:
        print(f"  [FAIL] Result: 0x{result:04X}, Error: 0x{error:04X}")
        return False


def set_indication(client: VHLClient, udp_port: int, info_mask: int, ka_sec: int, ip: str) -> bool:
    """Indication 설정"""
    print(f"\n[SET] Indication (0x8004)")
    print(f"  Port: {udp_port}, Mask: 0x{info_mask:02X}, KA: {ka_sec}s, IP: {ip}")

    payload = struct.pack(">HBB", udp_port, info_mask, ka_sec)
    payload += struct.pack(">I", int(ip))

    resp = client.send_request(VHL_REQ_SET_INDICATION, payload)
    if not resp:
        return False

    _, payload = resp
    result = struct.unpack(">H", payload[0:2])[0]
    error = struct.unpack(">H", payload[2:4])[0]

    if result == 0:
        print(f"  [OK] Indication configured")
        return True
    else:
        print(f"  [FAIL] Result: 0x{result:04X}, Error: 0x{error:04X}")
        return False


# ========== Test Scenarios ==========

def test_1_get_all(client: VHLClient):
    """테스트 1: 모든 Get 커맨드 실행"""
    print_separator("TEST 1: Get All Commands")
    get_device_info(client)
    get_device_status(client)
    get_wlan_config(client)


def test_2_set_indication(client: VHLClient):
    """테스트 2: Indication 설정"""
    print_separator("TEST 2: Set Indication")
    # 127.0.0.1:60000, mask=0x01(init), period=5
    set_indication(client, 60000, 0x01, 5, "2130706433")  # 127.0.0.1


def test_3_set_wlan_config_verify(client: VHLClient):
    """테스트 3: 무선 설정 변경 후 검증"""
    print_separator("TEST 3: Set WLAN Config & Verify")

    # 변경 전 상태 확인
    print("\n>>> Before Change:")
    before = get_wlan_config(client)
    if not before:
        print("  [SKIP] Cannot get current config")
        return

    # SSID 변경 (현재와 다른 값으로)
    old_ssid = before['ssid']
    test_ssid = "VHL_TEST_SSID" if old_ssid != "VHL_TEST_SSID" else "TEST_SSID_2"

    print(f"\n>>> Changing SSID from '{old_ssid}' to '{test_ssid}'")
    if not set_wlan_config(client, test_ssid, 5200):
        print("  [SKIP] Set failed")
        return

    # 변경 후 상태 확인
    print("\n>>> After Change:")
    time.sleep(1)
    after = get_wlan_config(client)

    if after and after['ssid'] == test_ssid:
        print(f"\n  [VERIFY OK] SSID changed: '{old_ssid}' -> '{test_ssid}'")
    else:
        print(f"\n  [VERIFY FAIL] SSID not changed. Expected: '{test_ssid}', Got: '{after['ssid'] if after else 'N/A'}'")

    # 원래대로 복원
    print(f"\n>>> Restoring original SSID: '{old_ssid}'")
    set_wlan_config(client, old_ssid, 5200)


def test_4_set_ip_addr_verify(client: VHLClient):
    """테스트 4: IP 주소 변경 후 검증"""
    print_separator("TEST 4: Set IP Address & Verify")

    # 현재 IP 확인
    print("\n>>> Before Change:")
    info = get_device_info(client)
    if not info:
        print("  [SKIP] Cannot get device info")
        return

    old_ip = info['ip_addr']
    old_mask = info['subnet']

    # IP를 현재와 다른 값으로 변경 (네트워크 유지)
    # 예: 192.168.0.101 -> 192.168.0.102
    parts = old_ip.split('.')
    new_ip = f"{parts[0]}.{parts[1]}.{parts[2]}.{int(parts[3]) + 1}"

    print(f"\n>>> Changing IP from {old_ip} to {new_ip}")
    if not set_ip_addr(client, new_ip, old_mask, "0.0.0.0"):
        print("  [SKIP] Set failed")
        return

    # 변경 후 IP 확인 (필요시 재접속)
    print("\n>>> After Change:")
    print("  [NOTE] Re-connect with new IP to verify")
    print(f"  New IP: {new_ip}")

    # 원래대로 복원 (사용자가 수동으로 해야 함)
    print(f"\n>>> To restore, run: python3 {sys.argv[0]} {old_ip} 50000")


def main():
    if len(sys.argv) > 1:
        host = sys.argv[1]
    else:
        host = "192.168.0.101"

    if len(sys.argv) > 2:
        port = int(sys.argv[2])
    else:
        port = 50000

    print(f"=== VHL Protocol Set/Get Test ===")
    print(f"Target: {host}:{port}\n")

    client = VHLClient(host, port)
    if not client.connect():
        print("[ERROR] Connection failed")
        sys.exit(1)

    try:
        # 각 테스트를 개별적으로 실행
        test_1_get_all(client)
        test_2_set_indication(client)

        print("\n" + "="*60)
        print("  Continue with WLAN Config change test?")
        print("  This will temporarily change your SSID!")
        print("  Enter 'yes' to continue:")
        print("="*60)

        # WLAN 설정 변경은 확인 후 진행
        # test_3_set_wlan_config_verify(client)

        # IP 변경은 네트워크 연결이 끊기므로 자동으로 하지 않음
        # test_4_set_ip_addr_verify(client)

        print("\n[INFO] Skipping auto WLAN/IP change tests.")
        print("[INFO] Run manually if needed:")
        print(f"  python3 {sys.argv[0]} {host} {port}")

    except KeyboardInterrupt:
        print("\n\n[INFO] Interrupted")
    finally:
        client.close()


if __name__ == "__main__":
    main()
