#!/usr/bin/env python3
"""
VHL Set IP Address Test (0x8002)
IP 주소 변경 (리셋 없이 즉시 적용)

Usage:
    python3 set_ip_addr.py [host] [port]
"""

import socket
import struct
import sys

VHL_PROTO_VER = 1
VHL_CMD_REQUEST = 0x01
VHL_REQ_SET_IP_ADDR = 0x8002


def ip_to_bytes(ip_str):
    parts = ip_str.split('.')
    return struct.pack(">BBBB", int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3]))


def set_ip_addr(host: str, port: int):
    print(f"=== Set IP Address (0x8002) ===")
    print(f"Target: {host}:{port}")
    print(f"[CAUTION] This will change IP immediately!")

    ip_addr = input("\nNew IP Address (e.g. 192.168.0.100): ")
    subnet = input("Subnet Mask (e.g. 255.255.255.0): ")
    gateway = input("Gateway (e.g. 192.168.0.1 or 0.0.0.0 for none): ")

    # IP 검증
    try:
        ip_to_bytes(ip_addr)
        ip_to_bytes(subnet)
        ip_to_bytes(gateway)
    except:
        print("\n[ERROR] Invalid IP format!")
        return

    # 페이로드 빌드: ip(4) + mask(4) + gw(4)
    payload = ip_to_bytes(ip_addr)
    payload += ip_to_bytes(subnet)
    payload += ip_to_bytes(gateway)

    seq = 1
    header = struct.pack(">BBHHH", VHL_PROTO_VER, VHL_CMD_REQUEST, VHL_REQ_SET_IP_ADDR, seq, len(payload))

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2.0)

    try:
        print(f"\nSending IP change request...")
        print(f"  IP: {ip_addr}")
        print(f"  Mask: {subnet}")
        print(f"  Gateway: {gateway}")

        sock.sendto(header + payload, (host, port))
        data, _ = sock.recvfrom(1500)

        version, cmd_type, req_id, resp_seq, length = struct.unpack(">BBHHH", data[:8])
        payload = data[8:8+length]

        result = struct.unpack(">H", payload[0:2])[0]
        error = struct.unpack(">H", payload[2:4])[0]

        print(f"\nResponse: Result=0x{result:04X}, Error=0x{error:04X}")

        if result == 0:
            print("[SUCCESS] IP address changed successfully")
            print(f"[INFO] New IP: {ip_addr}")
            print("[WARNING] You may lose connection if accessing via old IP!")
        else:
            error_msg = {
                0x0001: "Invalid netmask value",
                0x0002: "Gateway is in different subnet"
            }.get(error, f"Unknown error (0x{error:04X})")
            print(f"[FAILED] {error_msg}")

    except socket.timeout:
        print("[ERROR] Response timeout")
        print("[NOTE] Connection may be lost if IP was changed")
    except Exception as e:
        print(f"[ERROR] {e}")
    finally:
        sock.close()


if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 50000
    set_ip_addr(host, port)
