import sys
import os
from unittest.mock import MagicMock

# wired_mac_ip_get는 import 시 scapy/sUTILS(무거운/부재 deps)를 로드하므로 stub 후 import.
for _m in ("scapy", "scapy.config", "scapy.sendrecv", "scapy.layers", "scapy.layers.l2",
           "scapy.layers.inet", "scapy.layers.dhcp", "scapy.arch", "sUTILS"):
    sys.modules.setdefault(_m, MagicMock())

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wired_mac_ip_get as w

import pytest


def _write(netdir, name, content):
    with open(os.path.join(netdir, name), "w") as f:
        f.write(content)


# ---- get_iface_config_addr ----

def test_normal_address(tmp_path):
    _write(str(tmp_path), "20-mlan0.network", "[Network]\nAddress=192.168.0.1/24\n")
    assert w.get_iface_config_addr("mlan0", netdir=str(tmp_path)) == ("192.168.0.1", "192.168.0.0/24")

def test_missing_file(tmp_path):
    assert w.get_iface_config_addr("mlan0", netdir=str(tmp_path)) == (None, None)

def test_no_address_key(tmp_path):
    _write(str(tmp_path), "20-mlan0.network", "[Network]\nDHCP=no\n")
    assert w.get_iface_config_addr("mlan0", netdir=str(tmp_path)) == (None, None)

def test_case_insensitive_key(tmp_path):
    # systemd 키는 대소문자 무관
    _write(str(tmp_path), "20-mlan0.network", "[Network]\naddress=192.168.0.5/24\n")
    assert w.get_iface_config_addr("mlan0", netdir=str(tmp_path)) == ("192.168.0.5", "192.168.0.0/24")

def test_addressfamily_not_mismatched(tmp_path):
    # AddressFamily= 가 Address= 앞줄에 있어도 오파싱(ValueError→race 재발)하지 않고 Address= 를 잡는다
    _write(str(tmp_path), "20-mlan0.network",
           "[Network]\nAddressFamily=ipv4\nAddress=192.168.0.7/24\n")
    assert w.get_iface_config_addr("mlan0", netdir=str(tmp_path)) == ("192.168.0.7", "192.168.0.0/24")

def test_slash32_when_no_mask(tmp_path):
    # 마스크 없는 Address 는 /32 로 파싱된다
    _write(str(tmp_path), "20-mlan0.network", "[Network]\nAddress=192.168.0.100\n")
    assert w.get_iface_config_addr("mlan0", netdir=str(tmp_path)) == ("192.168.0.100", "192.168.0.100/32")

def test_mlan1_glob_prefix(tmp_path):
    # 파일명 접두 번호가 달라도(21-) iface 로 찾는다
    _write(str(tmp_path), "21-mlan1.network", "[Network]\nAddress=192.168.9.1/24\n")
    assert w.get_iface_config_addr("mlan1", netdir=str(tmp_path)) == ("192.168.9.1", "192.168.9.0/24")

def test_first_address_wins(tmp_path):
    _write(str(tmp_path), "20-mlan0.network",
           "[Network]\nAddress=192.168.0.1/24\nAddress=10.0.0.1/8\n")
    assert w.get_iface_config_addr("mlan0", netdir=str(tmp_path)) == ("192.168.0.1", "192.168.0.0/24")


# ---- get_sweep_network 우선순위 ----

def test_sweep_prefers_config(monkeypatch):
    monkeypatch.setattr(w, "ETH_SWEEP_SUBNET", None)
    monkeypatch.setattr(w, "get_iface_config_addr", lambda i, **k: ("192.168.0.100", "192.168.0.0/24"))
    monkeypatch.setattr(w, "get_iface_network", lambda i: "10.9.9.0/24")  # 쓰이면 안 됨
    assert w.get_sweep_network() == "192.168.0.0/24"

def test_sweep_slash32_falls_back_to_runtime(monkeypatch):
    # config 가 /32면 sweep 대상이 없으므로 런타임 폴백
    monkeypatch.setattr(w, "ETH_SWEEP_SUBNET", None)
    monkeypatch.setattr(w, "get_iface_config_addr", lambda i, **k: ("192.168.0.100", "192.168.0.100/32"))
    monkeypatch.setattr(w, "get_iface_network", lambda i: "10.1.2.0/24" if i == w.IFACE else "172.16.0.0/24")
    assert w.get_sweep_network() == "10.1.2.0/24"

def test_sweep_explicit_subnet_wins(monkeypatch):
    monkeypatch.setattr(w, "ETH_SWEEP_SUBNET", "192.168.5.0/24")
    monkeypatch.setattr(w, "get_iface_config_addr", lambda i, **k: ("1.1.1.1", "1.1.1.0/24"))  # 쓰이면 안 됨
    assert w.get_sweep_network() == "192.168.5.0/24"
