# wired_mac_ip_get.py 속도 최적화 계획

> **For agentic workers:** REQUIRED: Use superpowers:executing-plans to implement this plan.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 유선 클라이언트 MAC/IP 탐지 시간을 최소화하여 부팅 후 무선 드라이버 등록까지의 대기 시간을 단축한다.

**Architecture:** 3단계 탐지 전략 (빠른 경로 → 능동 탐색 → 풀 스윕)으로 재구성한다. 링크 확인을 최적화하고, 알려진 IP에 대한 ARP 프로브를 최우선으로 시도하여 최선의 경우 ~1초 내 완료를 목표로 한다.

**Tech Stack:** Python 3, Scapy, Linux sysfs

---

## 현재 문제 분석

### 호출 흐름
```
wifi_init.sh (MAC_MODE=dynamic)
  → python3 /usr/local/logger/wired_mac_ip_get.py
  → resolve_mac → update_mac.sh → 무선 드라이버 등록
```

### 현재 타이밍 (최악 경로)
| 단계 | 타임아웃 | 누적 |
|------|----------|------|
| eth0 down/up + link wait | 10s | 10s |
| passive_mac_sniff 1차 | 5s | 15s |
| raw_l2_broadcast_probe + 2차 스니핑 | 5s | 20s |
| passive_ip_for_mac | 6s | 26s |
| arp_unicast_probe (후보 IP) | 1.5s | 27.5s |
| /24 유니캐스트 스윕 | ~5s | 32.5s |
| arp_broadcast_sweep | 2s | 34.5s |

### 목표 타이밍
| 시나리오 | 현재 | 목표 |
|----------|------|------|
| 최선 (링크 up + 고정 IP 응답) | ~5s | **~1s** |
| 보통 (링크 up + 패시브 성공) | ~11s | **~3-4s** |
| 최악 (링크 대기 + 풀 스윕) | ~34s | **~15s** |

---

## Chunk 1: 구현

### Task 1: eth0 링크 대기 최적화

**Files:**
- Modify: `projects/wlan-package/dist/wlan/usr/local/logger/wired_mac_ip_get.py:26-63`

현재 문제: 매번 eth0를 down/up 리셋한 후 대기. 이미 링크가 있으면 불필요한 지연.

- [ ] **Step 1: `wait_for_eth_link()` 수정 — 이미 링크가 있으면 즉시 리턴**

```python
def wait_for_eth_link(timeout=10):
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
```

---

### Task 2: 빠른 경로 — ARP 프로브로 MAC+IP 동시 확보

**Files:**
- Modify: `projects/wlan-package/dist/wlan/usr/local/logger/wired_mac_ip_get.py` (새 함수 추가)

알려진 고정 IP가 있으면 ARP 1회로 MAC과 IP를 동시에 얻는 빠른 경로.

- [ ] **Step 2: `quick_arp_probe()` 함수 추가**

```python
def quick_arp_probe(iface, target_ip, own_mac, timeout=1):
    """
    알려진 고정 IP에 ARP 요청 → MAC+IP 동시 확보 (~1초).
    옵션 기능: PRIMARY_CANDIDATE_IP가 None이면 호출하지 않음.
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
```

---

### Task 3: 능동+패시브 통합 탐색

**Files:**
- Modify: `projects/wlan-package/dist/wlan/usr/local/logger/wired_mac_ip_get.py` (새 함수 추가)

현재: 패시브 5초 → 실패 → 브로드캐스트 → 패시브 5초 (순차 최대 10초)
개선: 브로드캐스트 자극 후 짧은 스니핑 1회 (최대 3초)

- [ ] **Step 3: `active_mac_sniff()` 함수 추가**

```python
def active_mac_sniff(iface, own_mac, timeout=3):
    """
    브로드캐스트 프로브 → 짧은 패시브 스니핑 (1회 통합).
    기존 패시브 2회(10초) 대비 3초로 단축.
    """
    raw_l2_broadcast_probe(iface)
    return passive_mac_sniff(own_mac, timeout=timeout)
```

---

### Task 4: main() 흐름 재구성 — 3단계 탐지 전략

**Files:**
- Modify: `projects/wlan-package/dist/wlan/usr/local/logger/wired_mac_ip_get.py:228-296` (main 함수 전체)

핵심 변경: 탐지 순서를 "빠른 경로 → 능동 탐색 → 풀 스윕"으로 재구성.

- [ ] **Step 4: main() 재작성**

```python
def main():
    if not wait_for_eth_link():
        return

    own_mac = get_own_mac(ETH_IFACE)
    save_data(f"/tmp/{ETH_IFACE}_mac", own_mac)

    mac = None
    ip = None

    # ── 1단계: 빠른 경로 (옵션, ~1초) ──
    # PRIMARY_CANDIDATE_IP가 설정되어 있을 때만 시도
    if PRIMARY_CANDIDATE_IP:
        mac, ip = quick_arp_probe(ETH_IFACE, PRIMARY_CANDIDATE_IP, own_mac, timeout=1)
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

    if not ip and PRIMARY_CANDIDATE_IP:
        # 3-2) 알려진 후보 IP에 유니캐스트 ARP
        ip = arp_unicast_probe_for_ip(ETH_IFACE, mac, [PRIMARY_CANDIDATE_IP], timeout=1)

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
```

**주요 변경 사항:**
1. `quick_arp_probe` 최우선 시도 (PRIMARY_CANDIDATE_IP 있을 때만)
2. 패시브 2회(10초) → `active_mac_sniff` 1회(3초)로 통합
3. `passive_ip_for_mac` 타임아웃 6초 → 3초
4. /24 유니캐스트 배치 스윕 제거 → `arp_broadcast_sweep` 1회로 단순화
5. `save_data(client_mac)` 시점을 IP 탐색 전으로 이동 (MAC 확보 즉시 저장)

---

### Task 5: 불필요해진 코드 정리

**Files:**
- Modify: `projects/wlan-package/dist/wlan/usr/local/logger/wired_mac_ip_get.py`

main()에서 더 이상 사용하지 않는 /24 유니캐스트 배치 스윕 로직 제거 (main 내부의 262-279행 부분).
`passive_mac_sniff`, `raw_l2_broadcast_probe` 등 기존 함수는 `active_mac_sniff`에서 재사용하므로 유지.

- [ ] **Step 5: main() 내부 유니캐스트 배치 스윕 로직 제거**

이 부분은 Task 4에서 main()을 재작성할 때 자연스럽게 제거됨.

---

## 최종 결과 비교

| 시나리오 | 현재 경로 | 개선 경로 | 시간 |
|----------|-----------|-----------|------|
| 링크 up + 고정 IP 응답 | link(0s) → passive(5s) → ... | link(0s) → quick_arp(1s) | **~1초** |
| 링크 up + 패시브 성공 | link(0s) → passive(5s) → ip(6s) | link(0s) → quick_arp 실패(1s) → active(3s) → ip(3s) | **~7초** |
| 링크 대기 + 풀 스윕 | link(10s) → passive×2(10s) → ip(6s) → sweep(~8s) | link(10s) → quick_arp 실패(1s) → active(3s) → sweep(2s) | **~16초** |

---

## 호환성 확인

- **wifi_init.sh (line 238):** `python3 /usr/local/logger/wired_mac_ip_get.py` — 변경 없음
- **wifi_arping.sh (line 135):** 동일 호출 — 변경 없음
- **출력 파일:** `/tmp/eth0_client_mac`, `/tmp/eth0_client_ip` — 경로/형식 동일
- **인자:** `sys.argv[1]` (mlan0/mlan1) — 변경 없음
- **PRIMARY_CANDIDATE_IP = None** 설정 시 기존 동작과 동일
