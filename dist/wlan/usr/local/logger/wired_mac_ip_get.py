#!/usr/bin/env python3
import subprocess
import time
import sys
import os
import logging
import ipaddress
import json
# scapy.all 전체 로드는 임베디드(ARM)에서 import만 수초 소요 → 필요한 서브모듈만 import (부팅 가속)
from scapy.config import conf
from scapy.sendrecv import sniff, srp, sendp
from scapy.layers.l2 import ARP, Ether
from scapy.layers.inet import IP
from scapy.layers.dhcp import DHCP, BOOTP
from scapy.arch import get_if_hwaddr
from sUTILS import Logger, _EXTRA_

ETH_IFACE = "eth0"          # 유선 1:1
IFACE = "mlan0"             # 무선 (게이트웨이 사용)

# ETH_CLIENT_IP: wifi_init_conf.json의 wbridge.eth_client_ip에서 읽음
# 설정이 없거나 빈 문자열이면 빠른 경로(quick_arp_probe) 건너뜀
ETH_CLIENT_IP = None
ETH_LINK_TIMEOUT = 3
IP_DISCOVERY = False        # MAC 확보 후 클라이언트 IP까지 탐색할지 (wbridge.ip_discovery, 기본 false)
# ETH_SWEEP_SUBNET: sweep는 ETH_IFACE(eth0)로 전송되지만, peer는 mlan0-IP 토폴로지에서 mlan0와
# 같은 대역에 있다. 미설정 시 mlan0→eth0 inet 순으로 폴백한다(get_sweep_network) — eth0는 관리
# IP(예: 192.168.1.0/24)를 가질 수 있어 peer 대역과 다를 수 있으므로 mlan0를 우선 참조한다.
# 부팅 race(특히 mlan0 미초기화)를 피하려면 wbridge.eth_sweep_subnet에 정적 CIDR(mlan0와 같은
# 대역, 예: "192.168.0.0/24")을 두는 게 안전하다.
ETH_SWEEP_SUBNET = None
# PEER_ROUTE_ENABLED: 양방향 peer 라우팅(옵션 X) 마스터 토글. false면 host route 등록 skip.
PEER_ROUTE_ENABLED = True
try:
    with open("/usr/local/etc/wifi_init_conf.json") as f:
        _cfg = json.load(f)
    _wb = _cfg.get("wbridge", {})
    _gl = _cfg.get("global", {})
    ETH_CLIENT_IP = _wb.get("eth_client_ip") or _gl.get("ETH_CLIENT_IP") or None
    ETH_LINK_TIMEOUT = int(_wb.get("eth_link_wait_sec", _gl.get("eth_link_wait_sec", 3)))
    IP_DISCOVERY = str(_wb.get("ip_discovery", False)).strip().lower() in ("1", "true", "yes", "on")
    ETH_SWEEP_SUBNET = _wb.get("eth_sweep_subnet") or _gl.get("eth_sweep_subnet") or None
    # null/missing → shell wifi_init.sh와 일관되게 default true.
    # 명시적 None 체크 후 str-based 파싱: bool/string 모두 안전 처리.
    # 기존 .get("enabled", True) + str() 방식은 "enabled": null 케이스에서
    # str(None)="None" → False가 되어 wifi_init.sh의 default=true와 split-brain 발생.
    _pr_v = _wb.get("peer_route", {}).get("enabled")
    PEER_ROUTE_ENABLED = True if _pr_v is None else \
        str(_pr_v).strip().lower() in ("1", "true", "yes", "on")
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
            #logger.message("info", f"[{IFACE}] Ethernet link already up", _EXTRA_())
            return True
    except Exception:
        pass

    # 링크 없으면 리셋
    logger.message("info", f"[{IFACE}] Reset {ETH_IFACE} link (down/up) and wait for link up...", _EXTRA_())
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
                logger.message("info", f"[{IFACE}] Ethernet link is up.", _EXTRA_())
                return True
        except Exception:
            pass
        time.sleep(0.3)

    logger.message("err", f"[{IFACE}] Timeout waiting for {ETH_IFACE} link", _EXTRA_())
    return False

# ===================== 1) MAC 획득 (원래 흐름 유지) =====================

def passive_mac_sniff(own_mac, timeout=5):
    logger.message("info", f"[{IFACE}] Sniffing for incoming ARP/DHCP/IP packets...", _EXTRA_())

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
    #print(f"[*] Sending raw L2 broadcast probe on {iface}...")
    logger.message("info", f"[{IFACE}] Sending raw L2 broadcast probe on {iface}...", _EXTRA_())
    src_mac = get_if_hwaddr(iface)
    # dummy EtherType + 최소 페이로드로 상대를 깨움
    pkt = Ether(dst="ff:ff:ff:ff:ff:ff", src=src_mac, type=0x0800) / (b'\x00' * 46)
    sendp(pkt, iface=iface, count=3, verbose=0)

def quick_arp_probe(iface, target_ip, own_mac, timeout=1):
    """
    알려진 고정 IP에 ARP 요청 → MAC+IP 동시 확보 (~1초).
    옵션 기능: ETH_CLIENT_IP가 None이면 호출하지 않음.
    """
    logger.message("info", f"[{iface}] Quick ARP probe to {target_ip}...", _EXTRA_())
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
    logger.message("info", f"[{iface}] MAC fixed: {target_mac} ? watching its packets for IP...", _EXTRA_())
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

def get_iface_network(iface):
    """
    지정 인터페이스의 IPv4 네트워크 대역 추출 (예: '192.168.1.1/24' -> '192.168.1.0/24').
    inet가 없으면(미초기화/무IP) None.
    """
    try:
        out = sh("ip", "-4", "addr", "show", "dev", iface).stdout
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("inet "):
                cidr = line.split()[1]  # x.x.x.x/yy
                net = str(ipaddress.ip_interface(cidr).network)
                return net
    except Exception:
        pass
    return None

def get_sweep_network():
    """
    sweep 대역 결정 우선순위 (sweep는 ETH_IFACE로 전송됨):
      1) wbridge.eth_sweep_subnet (정적, 부팅 race condition 없음 — 명시 override)
      2) mlan0(IFACE)의 inet — peer_route/mlan0-IP 토폴로지에서 peer가 실제 위치하는 대역.
         eth0는 관리 IP(예: 192.168.1.0/24)를 가질 수 있어 peer 대역과 다를 수 있으므로 mlan0 우선.
      3) eth0(ETH_IFACE)의 inet — mlan0 무IP(flat-bridge/eth0-IP)일 때 폴백.
    주의: eth0-IP 토폴로지인데 mlan0에도 별도 IP가 있는 dual-IP 구성에서 peer가 eth0 대역에
    있으면, 2순위에서 mlan0 대역이 선택되어 sweep가 빗나간다(폴백은 mlan0 무IP를 eth0-IP의
    신호로 삼는다). 이 구성에서는 eth_sweep_subnet을 명시해 폴백(2·3순위)을 우회하라.
    """
    if ETH_SWEEP_SUBNET:
        return ETH_SWEEP_SUBNET
    return get_iface_network(IFACE) or get_iface_network(ETH_IFACE)

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

def apply_peer_host_route(peer_ip):
    """
    라우팅 비대칭 해소:
      - mlan0와 eth0가 같은 서브넷일 때 connected route 한쪽으로만 잡혀
        반대편 응답이 잘못된 인터페이스로 나가는 문제를 host route로 우회.
      - peer_ip/32 dev eth0 를 main table에 등록하면 longest prefix match로 우선됨.
      - systemd-networkd 20-eth0.network의 iif policy routing과 함께 동작.
    """
    if not peer_ip:
        return
    r = subprocess.run(
        ["ip", "route", "replace", f"{peer_ip}/32", "dev", ETH_IFACE],
        capture_output=True, text=True, check=False
    )
    if r.returncode == 0:
        logger.message("info", f"[{IFACE}] host route applied: {peer_ip}/32 dev {ETH_IFACE}", _EXTRA_())
    else:
        logger.message("err",
            f"[{IFACE}] host route apply FAILED ({peer_ip}/32 dev {ETH_IFACE}): {r.stderr.strip()}",
            _EXTRA_())

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
            #print(f"[+] Quick path: MAC={mac}, IP={ip}")
            logger.message("info", f"[{IFACE}] Quick ARP: MAC={mac} IP={ip}", _EXTRA_())

    # ── 2단계: 능동+패시브 MAC 탐색 (~3초) ──
    if not mac:
        mac = active_mac_sniff(ETH_IFACE, own_mac, timeout=3)

    if not mac:
        #print("[-] Failed to detect any external MAC address.")
        logger.message("err", f"[{IFACE}] Failed to detect any external MAC address.", _EXTRA_())
        return

    logger.message("info", f"[{IFACE}] Wired Client MAC dectected: {mac}", _EXTRA_())

    # MAC 확보 → 즉시 저장 (무선 드라이버 등록에 필요)
    save_data(f"/tmp/{ETH_IFACE}_client_mac", mac)

    # ── 3단계: IP 확보 (MAC은 이미 저장됨) ──
    # wbridge.ip_discovery=false면 IP 탐색을 생략하고 MAC만 확보한 채 즉시 종료(부팅 가속).
    if not IP_DISCOVERY:
        logger.message("info", f"[{IFACE}] ip_discovery=false → skip IP discovery (MAC only)", _EXTRA_())
    else:
        if not ip:
            # 3-1) 패시브 관찰 (타임아웃 축소)
            ip = passive_ip_for_mac(ETH_IFACE, mac, timeout=3)

        if not ip and ETH_CLIENT_IP:
            # 3-2) 알려진 후보 IP에 유니캐스트 ARP
            ip = arp_unicast_probe_for_ip(ETH_IFACE, mac, [ETH_CLIENT_IP], timeout=1)

        if not ip:
            # 3-3) sweep (최후 수단)
            # sweep는 eth0로 전송 → ETH_SWEEP_SUBNET(정적) 우선, 미설정 시 eth0→mlan0 inet 폴백.
            net = get_sweep_network()
            if net:
                logger.message("info", f"[{IFACE}] sweep network: {net}", _EXTRA_())
                ip = arp_broadcast_sweep_for_mac(ETH_IFACE, mac, net, timeout=2)
            else:
                logger.message("err",
                    f"[{IFACE}] no sweep network (set wbridge.eth_sweep_subnet to enable last-resort sweep)",
                    _EXTRA_())

    # 결과 저장
    save_data(f"/tmp/{ETH_IFACE}_client_ip", ip)

    if ip:
        # 라우팅 비대칭 해소: peer로 가는 트래픽을 eth0로 강제 (peer_route 마스터 토글 체크)
        if PEER_ROUTE_ENABLED:
            apply_peer_host_route(ip)
        else:
            logger.message("info", f"[{IFACE}] peer_route=off: skip host route for {ip}", _EXTRA_())
        #print(f"[+] Wired Client IP resolved: {ip}")
        logger.message("info", f"[{IFACE}] result MAC/IP: {mac} {ip}", _EXTRA_())
    else:
        #print("[!] MAC only (no IP yet).")
        logger.message("info", f"[{IFACE}] MAC only saved (no IP)", _EXTRA_())

if __name__ == "__main__":
    logger = Logger(app_name='MAC', facility=logging.handlers.SysLogHandler.LOG_LOCAL0)

    IFACE = sys.argv[1] if len(sys.argv) > 1 else "mlan0"
    if IFACE not in ["mlan0", "mlan1"]:
        logger.message("err", f"[{IFACE}] invalid interface", _EXTRA_())
        sys.exit(1)

    os.makedirs("/opt/wlan/ip", exist_ok=True)

    # Scapy 기본 설정
    conf.sniff_promisc = True
    conf.verb = 0

    #logger.message("info", f"[{IFACE}] {ETH_IFACE} mac/ip discover start...", _EXTRA_())
    main()
