#!/usr/bin/env python3
import subprocess
import time
import sys
import os
import logging
from scapy.all import sniff, ARP, Ether, srp, sendp, get_if_hwaddr
from sUTILS import Logger, _EXTRA_

ETH_IFACE = "eth0"
IFACE = "mlan0"
PROBE_IP = "192.168.1.100"  # 예상 IP 주소 (ARP 유도 목적)
FILE_PATH = "/opt/wlan/mac/target0"

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
    logger.message("err", f"[{IFACE}] Timeout waiting for Ethernet link", _EXTRA_())
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
    # subprocess.run(["wpa_supplicant", "-i", IFACE, "-c", "/etc/wpa_supplicant.conf", "-B"])
    pass

def main():
    if not wait_for_eth_link():
        return

    mac = passive_mac_sniff()
    if not mac:
        raw_l2_broadcast_probe(ETH_IFACE)
        mac = passive_mac_sniff()
    if not mac:
        mac = active_arp_probe(PROBE_IP, ETH_IFACE)

    if mac:
        print(f"[+] Target MAC detected: {mac}")
        logger.message("info", f"[{IFACE}] Target MAC detected: {mac}", _EXTRA_())
        save_mac_address(FILE_PATH, mac)
        #set_mac_address(IFACE, mac)
        #connect_to_ap()
    else:
        print("[-] Failed to detect any external MAC address.")
        logger.message("err", f"[{IFACE}] Failed to detect any extrernal MAC address", _EXTRA_())

if __name__ == "__main__":
    logger = Logger(app_name='MAC', facility=logging.handlers.SysLogHandler.LOG_LOCAL0)

    IFACE = sys.argv[1] if len(sys.argv) > 1 else "mlan0"
    if IFACE not in ["mlan0", "mlan1"]:
        logger.message("info", f"[{IFACE}] Invalid interface", _EXTRA_())
        sys.exit(1)

    if IFACE == "mlan0" :
        FILE_PATH = "/opt/wlan/mac/wired_client"
    elif IFACE == "mlan1" :
        FILE_PATH = "/opt/wlan/mac/wired_client"
    else:
        logger.message("info", f"[{IFACE}] interface is wrong", _EXTRA_())
        sys.exit(1)

    os.makedirs("/opt/wlan/mac", exist_ok=True)

    logger.message("info", f"[{IFACE}] {ETH_IFACE} mac sniff...", _EXTRA_())

    main()
