#!/usr/bin/env python3
import subprocess
import time
import sys
import os
import logging
from scapy.all import sniff, ARP, Ether, srp, sendp, get_if_hwaddr
from sUTILS import Logger, _EXTRA_

ETH_IFACE = "eth0"
WLAN_IFACE = "mlan0"
PROBE_IP = "192.168.3.100"  # 예상 IP 주소 (ARP 유도 목적)
MAC_FILE = "/var/log/cantops/target_mac"
CONF_FILE = "/lib/firmware/nxp/wifi_mod_para__.conf"
BLOCK = "PCIE9098_0"

def get_own_mac(interface):
    with open(f"/sys/class/net/{interface}/address", "r") as f:
        return f.read().strip().lower()

def wait_for_eth_link(timeout=10):
    print(f"[+] Waiting for {ETH_IFACE} link up...")
    path = f"/sys/class/net/{ETH_IFACE}/operstate"
    deadline = time.time() + timeout
    while time.time() < deadline:
        with open(path, "r") as f:
            if f.read().strip() == "up":
                print("[+] Ethernet link is up.")
                return True
        time.sleep(0.5)
    print("[-] Timeout waiting for Ethernet link.")
    return False

def passive_mac_sniff(timeout=5):
    print("[*] Sniffing for incoming ARP/DHCP packets...")
    own_mac = get_own_mac(ETH_IFACE)
    def pkt_filter(pkt):
        return pkt.haslayer(Ether) and pkt.src != own_mac
    pkts = sniff(iface=ETH_IFACE, timeout=timeout, lfilter=pkt_filter)
    for pkt in pkts:
        return pkt.src
    return None

def active_arp_probe(ip, iface):
    print(f"[*] Sending ARP probe to {ip}...")
    pkt = Ether(dst="ff:ff:ff:ff:ff:ff", src=get_if_hwaddr(iface), type=0x0806) / ARP(pdst=ip)
    ans, _ = srp(pkt, iface=iface, timeout=2, verbose=0)
    if ans:
        return ans[0][1].hwsrc
    return None

def raw_l2_broadcast_probe(iface):
    print(f"[*] Sending raw L2 broadcast probe on {iface}...")
    src_mac = get_if_hwaddr(iface)
    pkt = Ether(dst="ff:ff:ff:ff:ff:ff", src=src_mac, type=0x0800) / b'\x00' * 46
    sendp(pkt, iface=iface, count=3, verbose=0)

def set_mac_address(interface, mac):
    print(f"[+] Setting {interface} MAC to {mac}")
    subprocess.run(["ip", "link", "set", interface, "down"])
    subprocess.run(["ip", "link", "set", interface, "address", mac])
    subprocess.run(["ip", "link", "set", interface, "up"])

def save_mac_address(FILE, mac):
    with open(FILE, "w") as log:
        log.write(mac)
        log.write("\n")

def connect_to_ap():
    print("[*] (Placeholder) Connecting to AP...")
    # 실제 환경에 맞게 수정:
    # subprocess.run(["wpa_supplicant", "-i", WLAN_IFACE, "-c", "/etc/wpa_supplicant.conf", "-B"])
    pass

def insert_mac_addr(conf_path, mac_path, target_block="PCIE9098_0"):
    try:
        with open(mac_path, "r") as f:
            mac = f.read().strip()

        with open(conf_path, "r") as f:
            lines = f.readlines()

        updated_lines = []
        inside_target = False
        already_present = False
        block_indent = ""
        block_end_idx = None

        for idx, line in enumerate(lines):
            stripped = line.strip()

            # 타겟 블록 진입
            if stripped.startswith(f"{target_block} ="):
                inside_target = True
                block_indent = line[:line.index(stripped)]
                updated_lines.append(line)
                continue

            # 블록 내 처리
            if inside_target:
                if "mac_addr=" in stripped:
                    already_present = True
                if stripped == "}":
                    block_end_idx = idx
                    if not already_present:
                        updated_lines.append(f"{block_indent}    mac_addr={mac}\n")
                    inside_target = False

            updated_lines.append(line)

        with open(conf_path, "w") as f:
            f.writelines(updated_lines)

        print(f"[INFO] mac_addr={mac} successfully inserted into block {target_block}.")
        logger.message("info", f"[{WLAN_IFACE}] mac_addr={mac} successfully inserted into block {target_block}", _EXTRA_())

    except Exception as e:
        print(f"[ERROR] {e}")

def get_mac_from_file(mac_file_path):
    """
    /var/log/cantops/target_mac 파일에서 MAC 주소를 읽는다.
    유효하면 문자열을 반환, 없으면 None.
    """
    try:
        with open(mac_file_path, "r") as f:
            mac = f.read().strip()
            if mac:
                return mac.lower()
    except Exception as e:
        print(f"[ERROR] Failed to read MAC from {mac_file_path}: {e}")
    return None

def is_mac_in_config_block(config_path, block_name, target_mac):
    """
    /lib/firmware/nxp/wifi_mod_para.conf 파일에서
    지정된 block_name (예: 'PCIE9098_0') 내에 mac_addr=target_mac 항목이 있는지 확인
    """
    try:
        with open(config_path, "r") as f:
            in_block = False
            for line in f:
                stripped = line.strip()

                if stripped.startswith(f"{block_name} ="):
                    in_block = True
                    continue

                if in_block:
                    if stripped == "}":
                        break
                    if stripped.startswith("mac_addr="):
                        current_mac = stripped.split("=", 1)[1].strip().lower()
                        if current_mac == target_mac.lower():
                            return True
    except Exception as e:
        print(f"[ERROR] Failed to parse config {config_path}: {e}")

    return False

def main():
    if not wait_for_eth_link():
        return

    mac = get_mac_from_file(MAC_FILE)
    if mac:
        if is_mac_in_config_block(CONF_FILE, BLOCK, mac):
            print(f"[INFO] MAC {mac} already configured in {BLOCK}")
            logger.message("info", f"[{WLAN_IFACE}] MAC {mac} already configured in {BLOCK} at {CONF_FILE}", _EXTRA_())
            sys.exit(0)
        else:
            print(f"[INFO] MAC {mac} not found in {BLOCK}")
            logger.message("info", f"[{WLAN_IFACE}] MAC {mac} not found in {BLOCK} at {CONF_FILE}", _EXTRA_())
            insert_mac_addr(CONF_FILE, MAC_FILE, BLOCK)
            sys.exit(0)

    mac = passive_mac_sniff()
    if not mac:
        raw_l2_broadcast_probe(ETH_IFACE)
        mac = passive_mac_sniff()
    if not mac:
        mac = active_arp_probe(PROBE_IP, ETH_IFACE)

    if mac:
        print(f"[+] Target MAC detected: {mac}")
        logger.message("info", f"Target MAC detected: {mac}", _EXTRA_())
        save_mac_address(MAC_FILE, mac)

        insert_mac_addr(CONF_FILE, MAC_FILE, BLOCK)

        #set_mac_address(WLAN_IFACE, mac)
        #connect_to_ap()
    else:
        print("[-] Failed to detect any external MAC address.")

if __name__ == "__main__":
    logger = Logger(app_name='MAC', facility=logging.handlers.SysLogHandler.LOG_LOCAL0)

    WLAN_IFACE = sys.argv[1] if len(sys.argv) > 1 else "mlan0"
    if WLAN_IFACE not in ["mlan0", "mlan1"]:
        logger.message("info", f"[{WLAN_IFACE}] Invalid interface {WLAN_IFACE}", _EXTRA_())
        sys.exit(1)

    if WLAN_IFACE == "mlan0" :
        BLOCK = "PCIE9098_0"
        MAC_FILE = "/opt/wlan/mac/target0"
    elif WLAN_IFACE == "mlan1" :
        BLOCK = "PCIE9098_1"
        MAC_FILE = "/opt/wlan/mac/target1"

    main()
