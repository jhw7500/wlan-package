#!/usr/bin/env python3
"""
VHL Set WLAN Config Test (0x8003)
무선 설정 변경 (SSID, 주파수)

Usage:
    python3 set_wlan_config.py [host] [port]
"""

import socket
import struct
import sys
import time

VHL_PROTO_VER = 1
VHL_CMD_REQUEST = 0x01
VHL_REQ_SET_WLAN_CONFIG = 0x8003
VHL_DEV_SINGLE_STATION = 0x0001


def set_wlan_config(host: str, port: int):
    print(f"=== Set WLAN Config (0x8003) ===")
    print(f"Target: {host}:{port}")

    ssid = input("\nNew SSID (leave empty to skip): ")

    if not ssid:
        print("[SKIP] No SSID provided")
        return

    if len(ssid) > 31:
        print("[ERROR] SSID too long (max 31 characters)")
        return

    freq_input = input("Frequency in MHz (e.g. 2412, 5180, 5200, leave empty to keep): ")

    try:
        freq = int(freq_input) if freq_input else 0
    except:
        print("[ERROR] Invalid frequency format")
        return

    # 페이로드 빌드: dev_type(2) + priority(2) + freq(2) + mode(1) + bw(1) + ssid(32)
    payload = struct.pack(">HHHBB",
                          VHL_DEV_SINGLE_STATION,
                          freq,   # priority
                          freq,   # wlan1 freq
                          0x06,   # mode: 5GHz ac/ax
                          0x04)   # bw: 80MHz

    payload += ssid.encode('utf-8').ljust(32, b'\x00')

    seq = 1
    header = struct.pack(">BBHHH", VHL_PROTO_VER, VHL_CMD_REQUEST, VHL_REQ_SET_WLAN_CONFIG, seq, len(payload))

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(5.0)

    try:
        print(f"\nSending WLAN config request...")
        print(f"  SSID: '{ssid}'")
        print(f"  Frequency: {freq} MHz")

        sock.sendto(header + payload, (host, port))
        data, _ = sock.recvfrom(1500)

        version, cmd_type, req_id, resp_seq, length = struct.unpack(">BBHHH", data[:8])
        payload = data[8:8+length]

        result = struct.unpack(">H", payload[0:2])[0]
        error = struct.unpack(">H", payload[2:4])[0]

        print(f"\nResponse: Result=0x{result:04X}, Error=0x{error:04X}")

        if result == 0:
            print("[SUCCESS] WLAN config changed successfully")
            print("[INFO] Reconnecting to new SSID...")
            print("[NOTE] Wait 2-3 seconds for connection to establish")
        else:
            error_msg = {
                0x0001: "Invalid frequency",
                0x0002: "Invalid device type (Dual Station on Single Station device)",
                0x0003: "SSID has invalid characters",
                0x0004: "SSID string length error"
            }.get(error, f"Unknown error (0x{error:04X})")
            print(f"[FAILED] {error_msg}")

    except socket.timeout:
        print("[ERROR] Response timeout")
    except Exception as e:
        print(f"[ERROR] {e}")
    finally:
        sock.close()


if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 50000
    set_wlan_config(host, port)
