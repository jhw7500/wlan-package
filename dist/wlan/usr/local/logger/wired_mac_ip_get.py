#!/usr/bin/env python3
import subprocess
import time
import sys
import os
import logging
import ipaddress
import json
from scapy.all import sniff, ARP, Ether, IP, srp, sendp, get_if_hwaddr, conf
from scapy.layers.dhcp import DHCP, BOOTP
from sUTILS import Logger, _EXTRA_

ETH_IFACE = "eth0"          # 유선 1:1
IFACE = "mlan0"             # 무선 (게이트웨이 사용)

# ETH_CLIENT_IP: wifi_init_conf.json의 global.ETH_CLIENT_IP에서 읽음
# 설정이 없거나 빈 문자열이면 빠른 경로(quick_arp_probe) 건너뜀
ETH_CLIENT_IP = None
ETH_LINK_TIMEOUT = 3
try:
    with open("/usr/local/etc/wifi_init_conf.json") as f:
        _cfg = json.load(f)
    ETH_CLIENT_IP = _cfg.get("global", {}).get("ETH_CLIENT_IP") or None
    ETH_LINK_TIMEOUT = int(_cfg.get("global", {}).get("eth_link_wait_sec", 3))
except Exception:
    pass

# ===================== 공통 유틸 =====================

def sh(*cmd, text=True, check=False, **kw):
    return subprocess.run(cmd, text=text, capture_output=True, check=check, **kw)

def get_own_mac(interface):
    with open(f"/sys/class/net/{interface}/address", "r") as f:
        return f.read().strip().lower()

def wait_for_eth_link(timeout=ETH_LINK_TIMEOUT):
    """
    eth0 링크 확인. 이미 up이면 즉시 리턴.
    down 상태일 때만 리셋 후 대기.
    """
    carrier_path   = f"/sys/class/net/{ETH_IFACE}/carrier"
    operstate_path = f"/sys/class/net/{ETH_IFACE}/operstate"

    # 이미 링크가 있는지 확인
    try:
        with open(carrier_path, "r") as f:
            carrier = f.read().strip()
        with open(operstate_path, "r") as f:
            oper = f.read().strip()
        if carrier == "1" and oper == "up":
            print("[+] Ethernet link already up.")
            return True
    except Exception:
        pass

    # 링크 없으면 리셋
    print(f"[+] Reset {ETH_IFACE} link (down/up) and wait for link up...")
    subprocess.run(["ip", "link", "set", ETH_IFACE, "down"], check=False)
    time.sleep(0.2)
    subprocess.run(["ip", "link", "set", ETH_IFACE, "up"], check=False)

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with open(carrier_path, "r") as f:
                carrier = f.read().strip()
            with open(operstate_path, "r") as f:
                oper = f.read().strip()
            if carrier == "1" and oper == "up":
                print("[+] Ethernet link is up.")
                return True
        except Exception:
            pass
        time.sleep(0.3)

    print("[-] Timeout waiting for Ethernet link.")
    logger.message("err", f"[{IFACE}] Timeout waiting for {ETH_IFACE} link", _EXTRA_())
    return False

# ===================== 1) MAC 획득 (원래 흐름 유지) =====================

def passive_mac_sniff(own_mac, timeout=5):
    print("[*] Sniffing for incoming ARP/DHCP/IP packets...")

    # ARP/DHCP/IP만 BPF로 받고, 내 MAC이 아닌 첫 송신자 MAC을 타깃으로 선정
    bpf = "arp or (udp and (port 67 or 68)) or ip"
    target_mac = [None]

    def stop_first(pkt):
        if not pkt.haslayer(Ether):
            return False
        src = pkt[Ether].src.lower()
        if src == own_mac:
            return False
        target_mac[0] = src
        return True

    try:
        sniff(iface=ETH_IFACE, timeout=timeout, stop_filter=stop_first, store=0, filter=bpf)
    except Exception:
        sniff(iface=ETH_IFACE, timeout=timeout, stop_filter=stop_first, store=0)

    return target_mac[0]

def raw_l2_broadcast_probe(iface):    
    print(f"[*] Sending raw L2 broadcast probe on {iface}...")
    logger.message("info", f"[{iface}] Sending raw L2 broadcast probe", _EXTRA_())
    src_mac = get_if_hwaddr(iface)
    # dummy EtherType + 최소 페이로드로 상대를 깨움
    pkt = Ether(dst="ff:ff:ff:ff:ff:ff", src=src_mac, type=0x0800) / (b'\x00' * 46)
    sendp(pkt, iface=iface, count=3, verbose=0)

def quick_arp_probe(iface, target_ip, own_mac, timeout=1):
    """
    알려진 고정 IP에 ARP 요청 → MAC+IP 동시 확보 (~1초).
    옵션 기능: ETH_CLIENT_IP가 None이면 호출하지 않음.
    """
    print(f"[*] Quick ARP probe to {target_ip}...")
    src_mac = get_if_hwaddr(iface)
    pkt = Ether(dst="ff:ff:ff:ff:ff:ff", src=src_mac) / ARP(pdst=target_ip)
    ans, _ = srp(pkt, iface=iface, timeout=timeout, verbose=0)
    for _, r in ans:
        if r.haslayer(ARP):
            a = r[ARP]
            mac = a.hwsrc.lower()
            if mac != own_mac:
                return mac, a.psrc
    return None, None

def active_mac_sniff(iface, own_mac, timeout=3):
    """
    브로드캐스트 프로브 → 짧은 패시브 스니핑 (1회 통합).
    기존 패시브 2회(10초) 대비 3초로 단축.
    """
    raw_l2_broadcast_probe(iface)
    return passive_mac_sniff(own_mac, timeout=timeout)

# ===================== 2) 타깃 MAC의 IP만 정확히 추출 =====================

def extract_ip_from_packet(pkt, target_mac):
    """
    같은 패킷에서 IP를 추출하되, 대상 MAC만 인정:
    우선순위: ARP reply(is-at) -> DHCP ACK -> IPv4 (Ether.src == MAC)
    """
    try:
        # ARP reply
        if pkt.haslayer(ARP):
            a = pkt[ARP]
            # op==2 (is-at) + 응답자 MAC 일치
            if getattr(a, "op", None) == 2 and a.hwsrc and a.hwsrc.lower() == target_mac:
                if a.psrc and a.psrc != "0.0.0.0":
                    return a.psrc

        # DHCP (ACK)
        if pkt.haslayer(BOOTP) and pkt.haslayer(DHCP):
            boot = pkt[BOOTP]
            # chaddr → MAC 매칭
            ch = bytes(boot.chaddr[:6])
            mac = ':'.join(f"{b:02x}" for b in ch).lower()
            if mac == target_mac:
                yi = getattr(boot, "yiaddr", None)
                if yi and yi != "0.0.0.0":
                    return yi

        # IPv4 (그 장치가 송신한 일반 IP 트래픽)
        if pkt.haslayer(IP) and pkt.haslayer(Ether):
            if pkt[Ether].src.lower() == target_mac:
                sip = pkt[IP].src
                if sip and sip != "0.0.0.0":
                    return sip
    except Exception:
        pass
    return None

def passive_ip_for_mac(iface, target_mac, timeout=6):
    """
    target_mac의 트래픽만 잠깐 더 관찰해서 IP를 추출.
    """
    print(f"[*] MAC fixed: {target_mac} ? watching its packets for IP...")
    result_ip = [None]
    bpf = "arp or (udp and (port 67 or 68)) or ip"

    def stop_follow(pkt):
        if not pkt.haslayer(Ether):
            return False
        if pkt[Ether].src.lower() != target_mac:
            return False
        ip = extract_ip_from_packet(pkt, target_mac)
        if ip:
            result_ip[0] = ip
            return True
        return False

    try:
        sniff(iface=iface, timeout=timeout, stop_filter=stop_follow, store=0, filter=bpf)
    except Exception:
        sniff(iface=iface, timeout=timeout, stop_filter=stop_follow, store=0)

    return result_ip[0]

def get_mlan_network():
    """
    mlan0의 네트워크 대역 추출 (예: '192.168.0.35/24' -> '192.168.0.0/24')
    """
    try:
        out = sh("ip", "-4", "addr", "show", "dev", IFACE).stdout
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("inet "):
                cidr = line.split()[1]  # x.x.x.x/yy
                net = str(ipaddress.ip_interface(cidr).network)
                return net
    except Exception:
        pass
    return None

def arp_unicast_probe_for_ip(iface, target_mac, ip_list, timeout=1.5):
    """
    지정한 ip_list 각각에 대해, target_mac으로 **유니캐스트 ARP 요청**을 보내고,
    응답의 hwsrc가 target_mac과 일치할 때만 그 psrc(IP)를 정답으로 채택.
    """
    src_mac = get_if_hwaddr(iface)
    reqs = []
    for ip in ip_list:
        # 유니캐스트 ARP request
        reqs.append(Ether(dst=target_mac, src=src_mac, type=0x0806) / ARP(pdst=ip))
    ans, _ = srp(reqs, iface=iface, timeout=timeout, verbose=0)
    for _, r in ans:
        if r.haslayer(ARP):
            a = r[ARP]
            # 응답자 MAC 일치 + 응답 IP 리턴
            if a.hwsrc and a.hwsrc.lower() == target_mac:
                return a.psrc
    return None

def arp_broadcast_sweep_for_mac(iface, target_mac, network_cidr, timeout=2):
    """
    /24 같은 대역에 대해 브로드캐스트 ARP sweep.
    응답자의 MAC이 target_mac일 때만 해당 IP 승인.
    """
    try:
        net = ipaddress.ip_network(network_cidr, strict=False)
    except ValueError:
        return None

    src_mac = get_if_hwaddr(iface)
    reqs = []
    for ip in net.hosts():
        reqs.append(Ether(dst="ff:ff:ff:ff:ff:ff", src=src_mac, type=0x0806) / ARP(pdst=str(ip)))
    ans, _ = srp(reqs, iface=iface, timeout=timeout, verbose=0)
    for _, r in ans:
        if r.haslayer(ARP):
            a = r[ARP]
            if a.hwsrc and a.hwsrc.lower() == target_mac:
                return a.psrc
    return None

# ===================== 저장/부가 =====================

def save_data(file_path, data):
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "w") as f:
        if data:
            f.write(f"{data}\n")

# ===================== 메인 =====================

def main():
    if not wait_for_eth_link():
        return

    own_mac = get_own_mac(ETH_IFACE)
    save_data(f"/tmp/{ETH_IFACE}_mac", own_mac)

    mac = None
    ip = None

    # ── 1단계: 빠른 경로 (옵션, ~1초) ──
    # ETH_CLIENT_IP가 설정되어 있을 때만 시도
    if ETH_CLIENT_IP:
        mac, ip = quick_arp_probe(ETH_IFACE, ETH_CLIENT_IP, own_mac, timeout=1)
        if mac:
            print(f"[+] Quick path: MAC={mac}, IP={ip}")
            logger.message("info", f"[{IFACE}] Quick ARP: MAC={mac} IP={ip}", _EXTRA_())

    # ── 2단계: 능동+패시브 MAC 탐색 (~3초) ──
    if not mac:
        mac = active_mac_sniff(ETH_IFACE, own_mac, timeout=3)

    if not mac:
        print("[-] Failed to detect any external MAC address.")
        logger.message("err", f"[{IFACE}] no external MAC", _EXTRA_())
        return

    print(f"[+] Wired Client MAC detected: {mac}")
    logger.message("info", f"[{IFACE}] Wired Client MAC: {mac}", _EXTRA_())

    # MAC 확보 → 즉시 저장 (무선 드라이버 등록에 필요)
    save_data(f"/tmp/{ETH_IFACE}_client_mac", mac)

    # ── 3단계: IP 확보 (MAC은 이미 저장됨) ──
    if not ip:
        # 3-1) 패시브 관찰 (타임아웃 축소)
        ip = passive_ip_for_mac(ETH_IFACE, mac, timeout=3)

    if not ip and ETH_CLIENT_IP:
        # 3-2) 알려진 후보 IP에 유니캐스트 ARP
        ip = arp_unicast_probe_for_ip(ETH_IFACE, mac, [ETH_CLIENT_IP], timeout=1)

    if not ip:
        # 3-3) mlan0 대역 스윕 (최후 수단)
        net = get_mlan_network()
        if net:
            ip = arp_broadcast_sweep_for_mac(ETH_IFACE, mac, net, timeout=2)

    # 결과 저장
    save_data(f"/tmp/{ETH_IFACE}_client_ip", ip)

    if ip:
        print(f"[+] Wired Client IP resolved: {ip}")
        logger.message("info", f"[{IFACE}] result MAC/IP: {mac} {ip}", _EXTRA_())
    else:
        print("[!] MAC only (no IP yet).")
        logger.message("info", f"[{IFACE}] MAC only saved (no IP)", _EXTRA_())

if __name__ == "__main__":
    logger = Logger(app_name='MAC', facility=logging.handlers.SysLogHandler.LOG_LOCAL0)

    IFACE = sys.argv[1] if len(sys.argv) > 1 else "mlan0"
    if IFACE not in ["mlan0", "mlan1"]:
        logger.message("info", f"[{IFACE}] invalid interface", _EXTRA_())
        sys.exit(1)

    os.makedirs("/opt/wlan/ip", exist_ok=True)

    # Scapy 기본 설정
    conf.sniff_promisc = True
    conf.verb = 0

    logger.message("info", f"[{IFACE}] {ETH_IFACE} mac/ip discover start...", _EXTRA_())
    main()
