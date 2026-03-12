#!/usr/bin/env python3
"""
VHL Set Indication Test (0x8004)
Indication (이벤트 통지) 설정

Usage:
    python3 set_indication.py [host] [port]
"""

import socket
import struct
import sys

VHL_PROTO_VER = 1
VHL_CMD_REQUEST = 0x01
VHL_REQ_SET_INDICATION = 0x8004


def set_indication(host: str, port: int):
    print(f"=== Set Indication (0x8004) ===")
    print(f"Target: {host}:{port}")

    print("\nIndication Info Mask (bit flags, sum for multiple):")
    print("  0x01 - Device init complete")
    print("  0x02 - WLAN state change (connect/disconnect)")
    print("  0x04 - Roaming")
    print("  0x08 - AP disconnect (Deauth/Disassoc)")
    print("  0x10 - Device fault detection")
    print("  0x20 - Device reset")
    print("  0x80 - Keep Alive")

    udp_port = input("\nIndication UDP Port (e.g. 50001): ")
    info_mask = input("Info Mask (hex, e.g. 85 for init+roam+keepalive): ")
    ka_period = input("Keep Alive Period in seconds (1-255, 0 to disable): ")
    ip_addr = input("Notification IP Address (e.g. 192.168.0.100 or 127.0.0.1): ")

    # IP 변환
    try:
        ip_parts = ip_addr.split('.')
        ip_bytes = struct.pack(">BBBB", int(ip_parts[0]), int(ip_parts[1]), int(ip_parts[2]), int(ip_parts[3]))
    except:
        print("\n[ERROR] Invalid IP address format")
        return

    try:
        udp_port = int(udp_port)
        info_mask = int(info_mask, 16)
        ka_period = int(ka_period)
    except:
        print("\n[ERROR] Invalid input format")
        return

    # 페이로드: udp_port(2) + info_mask(1) + ka_period(1) + ip_addr(4)
    payload = struct.pack(">HBB", udp_port, info_mask, ka_period)
    payload += ip_bytes

    seq = 1
    header = struct.pack(">BBHHH", VHL_PROTO_VER, VHL_CMD_REQUEST, VHL_REQ_SET_INDICATION, seq, len(payload))

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2.0)

    try:
        print(f"\nSending Indication config request...")
        print(f"  UDP Port: {udp_port}")
        print(f"  Info Mask: 0x{info_mask:02X}")
        print(f"  Keep Alive: {ka_period}s" if ka_period > 0 else "  Keep Alive: disabled")
        print(f"  Notify IP: {ip_addr}")

        sock.sendto(header + payload, (host, port))
        data, _ = sock.recvfrom(1500)

        version, cmd_type, req_id, resp_seq, length = struct.unpack(">BBHHH", data[:8])
        payload = data[8:8+length]

        result = struct.unpack(">H", payload[0:2])[0]
        error = struct.unpack(">H", payload[2:4])[0]

        print(f"\nResponse: Result=0x{result:04X}, Error=0x{error:04X}")

        if result == 0:
            print("[SUCCESS] Indication configured successfully")

            # 설정된 내용 해설
            enabled_flags = []
            if info_mask & 0x01: enabled_flags.append("Init Complete")
            if info_mask & 0x02: enabled_flags.append("WLAN State")
            if info_mask & 0x04: enabled_flags.append("Roaming")
            if info_mask & 0x08: enabled_flags.append("AP Disconnect")
            if info_mask & 0x10: enabled_flags.append("Fault Detection")
            if info_mask & 0x20: enabled_flags.append("Device Reset")
            if info_mask & 0x80: enabled_flags.append(f"Keep Alive ({ka_period}s)")

            if enabled_flags:
                print(f"[INFO] Enabled indications: {', '.join(enabled_flags)}")

            # UDP 리스너 시작 안내
            print(f"\n[INFO] To receive indications, start a listener on {ip_addr}:{udp_port}")
            print(f"[INFO] Example: nc -ulk {udp_port}")
        else:
            error_msg = {
                0x0001: "Invalid bit in info_mask (unassigned bit set)",
                0x0002: "Unsupported indication (not implemented)",
                0x0003: "Notification IP in different subnet"
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
    set_indication(host, port)
