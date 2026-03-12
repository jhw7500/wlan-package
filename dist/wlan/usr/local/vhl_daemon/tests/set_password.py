#!/usr/bin/env python3
"""
VHL Set Password Test (0x8001)
패스워드 설정

Usage:
    python3 set_password.py [host] [port]
"""

import socket
import struct
import sys
import getpass

VHL_PROTO_VER = 1
VHL_CMD_REQUEST = 0x01
VHL_REQ_SET_PASSWORD = 0x8001


def set_password(host: str, port: int):
    print(f"=== Set Password (0x8001) ===")
    print(f"Target: {host}:{port}")

    old_pw = getpass.getpass("Current password: ")
    new_pw = getpass.getpass("New password: ")
    confirm_pw = getpass.getpass("Confirm new password: ")

    if new_pw != confirm_pw:
        print("\n[ERROR] Passwords do not match!")
        return

    if len(old_pw) > 63 or len(new_pw) > 63:
        print("\n[ERROR] Password too long (max 63 characters)")
        return

    # 페이로드 빌드: old_pw(64) + new_pw(64)
    payload = old_pw.encode('utf-8').ljust(64, b'\x00')
    payload += new_pw.encode('utf-8').ljust(64, b'\x00')

    seq = 1
    header = struct.pack(">BBHHH", VHL_PROTO_VER, VHL_CMD_REQUEST, VHL_REQ_SET_PASSWORD, seq, len(payload))

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2.0)

    try:
        print(f"\nSending password change request...")
        sock.sendto(header + payload, (host, port))
        data, _ = sock.recvfrom(1500)

        version, cmd_type, req_id, resp_seq, length = struct.unpack(">BBHHH", data[:8])
        payload = data[8:8+length]

        result = struct.unpack(">H", payload[0:2])[0]
        error = struct.unpack(">H", payload[2:4])[0]

        print(f"\nResponse: Result=0x{result:04X}, Error=0x{error:04X}")

        if result == 0:
            print("[SUCCESS] Password changed successfully")
            print("[NOTE] Currently stub only - wpa_supplicant not updated")
        else:
            error_msg = {
                0x0001: "Old password mismatch",
                0x0002: "Old password has invalid characters",
                0x0003: "Old password not NULL terminated",
                0x0004: "New password has invalid characters",
                0x0005: "New password not NULL terminated"
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
    set_password(host, port)
