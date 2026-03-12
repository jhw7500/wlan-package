#!/usr/bin/env python3
import subprocess
import time
import sys
import os
import logging
import ipaddress
from scapy.all import sniff, ARP, Ether, IP, srp, sendp, get_if_hwaddr, conf
from scapy.layers.dhcp import DHCP, BOOTP
from sUTILS import Logger, _EXTRA_

ETH_IFACE = "eth0"          # 유선 1:1
IFACE = "mlan0"             # 무선 (게이트웨이 사용)
# 환경 상 eth0_client가 사실상 고정(192.168.0.10)이라고 했으니, 먼저 이 IP에 유니캐스트 ARP로 확인
PRIMARY_CANDIDATE_IP = "192.168.0.10"   # 필요시 None 로 두면 생략

# ===================== 공통 유틸 =====================

def sh(*cmd, text=True, check=False, **kw):
    return subprocess.run(cmd, text=text, capture_output=True, check=check, **kw)

def get_own_mac(interface):
    with open(f"/sys/class/net/{interface}/address", "r") as f:
        return f.read().strip().lower()

def wait_for_eth_link(timeout=10):
    """
    요청대로 eth0을 먼저 down/up 리셋하고, carrier/operstate 둘 다 확인.
    """
    print(f"[+] Reset {ETH_IFACE} link (down/up) and wait for link up...")
    # down
    subprocess.run(["ip", "link", "set", ETH_IFACE, "down"], check=False)
    time.sleep(0.2)
    # up
    subprocess.run(["ip", "link", "set", ETH_IFACE, "up"], check=False)

    carrier_path   = f"/sys/class/net/{ETH_IFACE}/carrier"     # '1'이면 링크
    operstate_path = f"/sys/class/net/{ETH_IFACE}/operstate"   # 'up'이면 활성
    deadline = time.time() + timeout

    while time.time() < deadline:
        carrier = None
        oper    = None
        try:
            with open(carrier_path, "r") as f:
                carrier = f.read().strip()
        except Exception:
            pass
        try:
            with open(operstate_path, "r") as f:
                oper = f.read().strip()
        except Exception:
            pass

        if carrier == "1" and oper == "up":
            print("[+] Ethernet link is up.")
            return True

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

    # 1) MAC 먼저 확보 (원래 흐름)
    mac = passive_mac_sniff(own_mac, timeout=5)
    if not mac:
        raw_l2_broadcast_probe(ETH_IFACE)
        mac = passive_mac_sniff(own_mac, timeout=5)

    if not mac:
        print("[-] Failed to detect any external MAC address.")
        logger.message("err", f"[{IFACE}] no external MAC", _EXTRA_())
        return

    print(f"[+] Wired Client MAC detected: {mac}")
    logger.message("info", f"[{IFACE}] Wired Client MAC: {mac}", _EXTRA_())

    # 2) 해당 MAC만 대상으로 IP 확정
    ip = passive_ip_for_mac(ETH_IFACE, mac, timeout=6)

    # 3) 패시브 실패 시, 우선적으로 '알려진 후보 IP(192.168.0.10)'에 유니캐스트 ARP
    if not ip and PRIMARY_CANDIDATE_IP:
        ip = arp_unicast_probe_for_ip(ETH_IFACE, mac, [PRIMARY_CANDIDATE_IP], timeout=1.5)

    # 4) 그래도 없으면, mlan0 대역(/24 등) 전체를 대상으로 유니캐스트→브로드캐스트 순 시도
    if not ip:
        net = get_mlan_network()  # 예: '192.168.0.0/24'
        if net:
            # 유니캐스트로 /24 한 바퀴: NIC/스택에 따라 응답률이 더 좋은 경우가 있음
            # 성능상 모두 유니캐스트로 돌리기 부담되면 브로드캐스트만으로도 충분
            try:
                network = ipaddress.ip_network(net, strict=False)
                # 유니캐스트 스윕 (속도/부하 조절 가능)
                batch = []
                cnt = 0
                for host_ip in network.hosts():
                    batch.append(str(host_ip))
                    cnt += 1
                    if cnt >= 64:  # 배치 크기 조절
                        ip = arp_unicast_probe_for_ip(ETH_IFACE, mac, batch, timeout=1.2)
                        if ip:
                            break
                        batch = []
                        cnt = 0
                if not ip and batch:
                    ip = arp_unicast_probe_for_ip(ETH_IFACE, mac, batch, timeout=1.2)
            except Exception:
                pass

            # 브로드캐스트 스윕 (최후 수단)
            if not ip:
                ip = arp_broadcast_sweep_for_mac(ETH_IFACE, mac, net, timeout=2)

    # 결과 저장
    save_data(f"/tmp/{ETH_IFACE}_client_mac", mac)
    save_data(f"/tmp/{ETH_IFACE}_client_ip", ip)

    if ip:
        print(f"[+] Wired Client IP resolved: {ip}")
        logger.message("info", f"[{IFACE}] result MAC/IP: {mac} {ip}", _EXTRA_())
        save_data(f"/tmp/{ETH_IFACE}_client_ip", ip)
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
