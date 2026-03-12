#!/usr/bin/env python3
"""
VHL Get WLAN Config Test (0x0003)
무선 설정 조회

Usage:
    python3 get_wlan_config.py [host] [port]
"""

import socket
import struct
import sys

VHL_PROTO_VER = 1
VHL_CMD_REQUEST = 0x01
VHL_REQ_GET_WLAN_CONFIG = 0x0003


def get_wlan_config(host: str, port: int):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2.0)

    seq = 1
    header = struct.pack(">BBHHH", VHL_PROTO_VER, VHL_CMD_REQUEST, VHL_REQ_GET_WLAN_CONFIG, seq, 0)

    print(f"=== Get WLAN Config (0x0003) ===")
    print(f"Target: {host}:{port}")
    print(f"Sending request...")

    try:
        sock.sendto(header, (host, port))
        data, _ = sock.recvfrom(1500)

        version, cmd_type, req_id, resp_seq, length = struct.unpack(">BBHHH", data[:8])
        payload = data[8:8+length]

        print(f"\nResponse received:")
        print(f"  Version: {version}, Type: {cmd_type}, ReqID: 0x{req_id:04X}, Seq: {resp_seq}, Len: {length}")

        dev_type = struct.unpack(">H", payload[0:2])[0]
        priority = struct.unpack(">H", payload[2:4])[0]
        freq = struct.unpack(">H", payload[4:6])[0]
        mode = payload[6]
        bw = payload[7]
        ssid = payload[8:40].decode('utf-8', errors='ignore').rstrip('\x00')

        mode_str = {
            0x01: "802.11g", 0x02: "802.11gn", 0x03: "802.11ax",
            0x11: "802.11a", 0x05: "802.11an", 0x06: "802.11ac/ax",
            0x08: "802.11ax-6G"
        }.get(mode, f"Unknown(0x{mode:02X})")

        bw_str = {
            0x01: "20MHz", 0x02: "HT40+", 0x03: "HT40-",
            0x04: "80MHz", 0x05: "160MHz",
            0x10: "HE20", 0x20: "HE40/80", 0x30: "HE160"
        }.get(bw, f"Unknown(0x{bw:02X})")

        band = "2.4GHz" if freq < 3000 else "5GHz" if freq < 6000 else "6GHz"

        print(f"\n--- WLAN Configuration ---")
        print(f"  Device Type:   0x{dev_type:04X}")
        print(f"  Priority:      {priority} MHz")
        print(f"  Frequency:     {freq} MHz ({band})")
        print(f"  Mode:          {mode_str} (0x{mode:02X})")
        print(f"  Bandwidth:     {bw_str} (0x{bw:02X})")
        print(f"  SSID:          '{ssid}'")

        if ssid:
            print(f"\n  ✓ Connected to SSID: {ssid}")
        else:
            print(f"\n  ⚠ Not connected to any SSID")

    except socket.timeout:
        print("  [ERROR] Response timeout")
    except Exception as e:
        print(f"  [ERROR] {e}")
    finally:
        sock.close()


if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 50000
    get_wlan_config(host, port)
