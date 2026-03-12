#!/usr/bin/env python3
"""
VHL Get Device Status Test (0x0002)
장치 상태 조회

Usage:
    python3 get_device_status.py [host] [port]
"""

import socket
import struct
import sys

VHL_PROTO_VER = 1
VHL_CMD_REQUEST = 0x01
VHL_REQ_GET_DEV_STATUS = 0x0002

VHL_STATUS_BOOTING = 0x0001
VHL_STATUS_SCANNING = 0x0002
VHL_STATUS_CONNECTED = 0x1000


def get_device_status(host: str, port: int):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2.0)

    seq = 1
    header = struct.pack(">BBHHH", VHL_PROTO_VER, VHL_CMD_REQUEST, VHL_REQ_GET_DEV_STATUS, seq, 0)

    print(f"=== Get Device Status (0x0002) ===")
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
        status = struct.unpack(">H", payload[2:4])[0]
        freq = struct.unpack(">H", payload[4:6])[0]
        snr = payload[6]
        priority = struct.unpack(">H", payload[7:9])[0]

        status_str = {
            VHL_STATUS_BOOTING: "Booting",
            VHL_STATUS_SCANNING: "Scanning (Beacon)",
            VHL_STATUS_CONNECTED: "Connected"
        }.get(status, f"Unknown(0x{status:04X})")

        print(f"\n--- Device Status ---")
        print(f"  Device Type:   0x{dev_type:04X} ({'Single Station' if dev_type == 0x0001 else 'Dual Station'})")
        print(f"  Status:        {status_str} (0x{status:04X})")
        print(f"  Frequency:     {freq} MHz")
        print(f"  SNR:           {snr} dB")
        print(f"  Priority:      {priority} MHz")

        if status == VHL_STATUS_CONNECTED:
            print(f"\n  ✓ WLAN is CONNECTED")
        elif status == VHL_STATUS_SCANNING:
            print(f"\n  ⏳ WLAN is SCANNING")
        else:
            print(f"\n  ◐ WLAN is BOOTING")

    except socket.timeout:
        print("  [ERROR] Response timeout")
    except Exception as e:
        print(f"  [ERROR] {e}")
    finally:
        sock.close()


if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 50000
    get_device_status(host, port)
