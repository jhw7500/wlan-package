#!/usr/bin/env python3
"""
VHL Get Device Info Test (0x0001)
장치 정보 조회

Usage:
    python3 get_device_info.py [host] [port]
"""

import socket
import struct
import sys

VHL_PROTO_VER = 1
VHL_CMD_REQUEST = 0x01
VHL_REQ_GET_DEV_INFO = 0x0001


def get_device_info(host: str, port: int):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2.0)

    # Request 패킷 빌드 (헤더만, 페이로드 없음)
    seq = 1
    header = struct.pack(">BBHHH", VHL_PROTO_VER, VHL_CMD_REQUEST, VHL_REQ_GET_DEV_INFO, seq, 0)

    print(f"=== Get Device Info (0x0001) ===")
    print(f"Target: {host}:{port}")
    print(f"Sending request...")

    try:
        sock.sendto(header, (host, port))
        data, _ = sock.recvfrom(1500)

        # 헤더 파싱
        version, cmd_type, req_id, resp_seq, length = struct.unpack(">BBHHH", data[:8])
        payload = data[8:8+length]

        print(f"\nResponse received:")
        print(f"  Version: {version}, Type: {cmd_type}, ReqID: 0x{req_id:04X}, Seq: {resp_seq}, Len: {length}")

        # 페이로드 파싱
        offset = 0
        vendor = payload[offset:offset+64].decode('utf-8', errors='ignore').rstrip('\x00'); offset += 64
        model = payload[offset:offset+64].decode('utf-8', errors='ignore').rstrip('\x00'); offset += 64
        fw_ver = payload[offset:offset+64].decode('utf-8', errors='ignore').rstrip('\x00'); offset += 64
        hw_ver = payload[offset:offset+64].decode('utf-8', errors='ignore').rstrip('\x00'); offset += 64
        serial = payload[offset:offset+64].decode('utf-8', errors='ignore').rstrip('\x00'); offset += 64
        dev_type = struct.unpack(">H", payload[offset:offset+2])[0]; offset += 2
        eth_mac = ":".join(f"{payload[offset+i]:02X}" for i in range(6)); offset += 6
        wlan_mac = ":".join(f"{payload[offset+i]:02X}" for i in range(6)); offset += 6
        ip_addr = ".".join(str(payload[offset+i]) for i in range(4)); offset += 4
        subnet = ".".join(str(payload[offset+i]) for i in range(4)); offset += 4
        gateway = ".".join(str(payload[offset+i]) for i in range(4)); offset += 4

        print(f"\n--- Device Information ---")
        print(f"  Vendor:        {vendor}")
        print(f"  Model:         {model}")
        print(f"  FW Version:    {fw_ver}")
        print(f"  HW Version:    {hw_ver}")
        print(f"  Serial Number: {serial}")
        print(f"  Device Type:   0x{dev_type:04X} ({'Single Station' if dev_type == 0x0001 else 'Unknown'})")
        print(f"  ETH MAC:       {eth_mac}")
        print(f"  WLAN MAC:      {wlan_mac}")
        print(f"  IP Address:    {ip_addr}")
        print(f"  Subnet Mask:   {subnet}")
        print(f"  Gateway:       {gateway}")

    except socket.timeout:
        print("  [ERROR] Response timeout")
    except Exception as e:
        print(f"  [ERROR] {e}")
    finally:
        sock.close()


if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 50000
    get_device_info(host, port)
