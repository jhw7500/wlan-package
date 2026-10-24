"""#406: AP 미연결이어도 link.json 에 자기 MAC(info.address)이 실려 GetDeviceInfo·SNMP 의
무선 MAC 이 비지 않는다. 미연결 판정(link.address 없음)은 그대로다."""
import json
import os
import sys
from unittest.mock import MagicMock

# sUTILS pulls heavy runtime deps (paho, serial); stub before importing the module.
sys.modules.setdefault("sUTILS", MagicMock())

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wifi_logger_link  # noqa: E402
import wifi_snmp_pp as pp  # noqa: E402
import passive_roam  # noqa: E402
import roam_notify  # noqa: E402

import pytest  # noqa: E402

MAC = "02:11:22:33:44:55"


@pytest.fixture
def link(tmp_path, monkeypatch):
    """IFACE·sysfs·LOG_DIR 를 임시 경로로 돌린 로거 모듈. sysfs 값은 대문자로 둬 정규화도 본다."""
    sysfs = tmp_path / "net"
    (sysfs / "mlan0").mkdir(parents=True)
    (sysfs / "mlan0" / "address").write_text(MAC.upper() + "\n")
    logdir = tmp_path / "json"
    logdir.mkdir()
    monkeypatch.setattr(wifi_logger_link, "IFACE", "mlan0")
    monkeypatch.setattr(wifi_logger_link, "SYSFS_NET", str(sysfs))
    monkeypatch.setattr(wifi_logger_link, "LOG_DIR", str(logdir))
    return wifi_logger_link, sysfs / "mlan0" / "address", logdir / "link.json"


def _write_disconnected(link):
    mod, _, path = link
    mod._write_disconnected_link()
    return path


def test_disconnected_link_has_own_mac_and_no_link_section(link):
    data = json.loads(_write_disconnected(link).read_text())
    assert data["info"] == {"address": MAC}
    assert "link" not in data
    assert "date" in data


@pytest.mark.parametrize("content", [None, "garbage\n", ""])
def test_unreadable_or_invalid_mac_keeps_empty_object(link, content):
    _, address, _ = link
    if content is None:
        address.unlink()
    else:
        address.write_text(content)
    assert _write_disconnected(link).read_text() == "{}\n"


def test_snmp_reports_own_mac_while_not_connected(link):
    data = json.loads(_write_disconnected(link).read_text())
    om = pp.build_oid_map(mlan=data, base=pp.FXE3000)
    want = ("octet", MAC.replace(":", " ").upper())
    assert om[pp.FXE3000 + ".2.5.0"] == want         # WLMacAddress
    assert om[pp.FXE3000 + ".3.3.1.4.0"] == want     # WIFInfoWLMacAddress
    assert om[pp.FXE3000 + ".3.3.1.11.1.0"] == ("integer", "1")   # StaLoginState notConnected
    assert om[pp.FXE3000 + ".3.3.1.11.2.0"] == ("octet", "00 00 00 00 00 00")  # BSSID
    assert om[pp.FXE3000 + ".3.3.1.11.3.0"] == ("string", "")     # ESSID
    assert om[pp.FXE3000 + ".3.3.1.11.4.0"] == ("integer", "0")   # Channel


def test_roam_consumers_still_see_disconnected(link, monkeypatch):
    path = _write_disconnected(link)
    assert passive_roam.read_current_bssid(str(path)) == ""
    monkeypatch.setattr(roam_notify, "LINK_JSON_FMT", str(path))
    data = roam_notify._read_link_json("mlan0")
    assert roam_notify.build_payload(data, "mlan0", "", "aa:bb:cc:dd:ee:ff") is None
    assert roam_notify.notify_roam("mlan0", "", "aa:bb:cc:dd:ee:ff") is False
    assert roam_notify.notify_roam("mlan0", "", "aa:bb:cc:dd:ee:ff",
                                   require_link_match=True) is False


def test_connected_info_without_address_is_filled(link):
    mod, _, _ = link
    assert mod._ensure_own_mac({"ssid": "x"}) == {"ssid": "x", "address": MAC}
    # iw info 가 준 값은 그대로 둔다.
    assert mod._ensure_own_mac({"address": "aa:bb:cc:dd:ee:ff"}) == {"address": "aa:bb:cc:dd:ee:ff"}


def test_connected_fill_skipped_when_sysfs_unreadable(link):
    mod, address, _ = link
    address.unlink()
    assert mod._ensure_own_mac({}) == {}


class _StopLoop(Exception):
    pass


def _run_one_cycle(link, monkeypatch, wpa_running):
    """main() 을 한 주기만 돌린다. 인터페이스 대기 분기는 고정 경로(/sys/class/net/<if>)를
    보므로 호스트에 늘 있는 lo 를 IFACE 로 쓰고, 자기 MAC 은 임시 sysfs 에서 읽는다."""
    mod, address, path = link
    lo = address.parent.parent / "lo"
    lo.mkdir()
    (lo / "address").write_text(MAC + "\n")

    def stop(_seconds):
        raise _StopLoop

    monkeypatch.setattr(mod, "IFACE", "lo")
    # logger 는 __main__ 에서만 만들어지므로 없을 수 있다.
    monkeypatch.setattr(mod, "logger", MagicMock(), raising=False)
    monkeypatch.setattr(mod, "time", type("T", (), {
        "monotonic": staticmethod(lambda: 0.0), "sleep": staticmethod(stop)}))
    monkeypatch.setattr(mod, "check_fw_settings", lambda *_: None)
    monkeypatch.setattr(mod, "is_wpa_running", lambda _iface: wpa_running)
    monkeypatch.setattr(mod, "poll_supplicant",
                        lambda _iface: {"wpa_state": "SCANNING", "temp_disabled": False})
    monkeypatch.setattr(mod, "run_command", lambda _cmd: "")
    monkeypatch.setattr(mod, "fast_retry_station_dump", lambda *_: None)
    with pytest.raises(_StopLoop):
        mod.main()
    return json.loads(path.read_text())


@pytest.mark.parametrize("wpa_running", [False, True], ids=["wpa-down", "no-station"])
def test_main_disconnected_branches_write_own_mac(link, monkeypatch, wpa_running):
    data = _run_one_cycle(link, monkeypatch, wpa_running)
    assert data["info"] == {"address": MAC}
    assert "link" not in data
