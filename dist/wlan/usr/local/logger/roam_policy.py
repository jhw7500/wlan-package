"""Boot-latched roaming owner/topology policy shared by Wi-Fi daemons.

The snapshot is created atomically under /run by wifi_apply_enabled.sh.  It is
deliberately not regenerated inside a daemon: /run lifetime, rather than a
mutable persisted JSON file, defines the reboot boundary.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Optional


DEFAULT_RUN_DIR = "/run/wifi"


class RoamPolicyError(RuntimeError):
    """The boot owner/topology snapshot is missing or cannot be trusted."""


_WPA_HEX_RE = re.compile(r"(?:[0-9A-Fa-f]{2})+")


def validate_ssid(ssid: Any) -> str:
    """Validate and return one byte-exact IEEE 802.11 SSID identity.

    The package contract is deliberately narrower than arbitrary supplicant
    byte strings: valid UTF-8, 1..32 encoded bytes, with C0 controls and DEL
    rejected.  Printable spaces, backslashes and quotes are identities and are
    therefore never stripped or escaped here.
    """
    if not isinstance(ssid, str):
        raise RoamPolicyError("SSID must be a string")
    try:
        encoded = ssid.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise RoamPolicyError("SSID must be valid UTF-8") from exc
    if not 1 <= len(encoded) <= 32:
        raise RoamPolicyError(
            f"SSID must be 1..32 UTF-8 bytes (got {len(encoded)})"
        )
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in ssid):
        raise RoamPolicyError("SSID must not contain C0 controls or DEL")
    return ssid


def validate_ssid_list(
    ssids: Any, *, base_ssid: Optional[str] = None
) -> list[str]:
    """Validate an ordered SSID list without normalizing any identity."""
    if not isinstance(ssids, list):
        raise RoamPolicyError("extra_ssids must be an array")
    base = validate_ssid(base_ssid) if base_ssid is not None else None
    result: list[str] = []
    seen: set[str] = set()
    for item in ssids:
        ssid = validate_ssid(item)
        if ssid in seen:
            raise RoamPolicyError(f"duplicate SSID identity: {ssid!r}")
        if base is not None and ssid == base:
            raise RoamPolicyError("extra SSID duplicates the base SSID identity")
        seen.add(ssid)
        result.append(ssid)
    return result


def decode_wpa_ssid_text(value: Any) -> str:
    """Decode wpa_supplicant/iw printable SSID text to the exact UTF-8 identity.

    CTRL_IFACE ``status``, ``list_networks`` and ``scan_results`` use
    upstream ``printf_encode``: quotes/backslashes are escaped and every
    non-ASCII byte is rendered as ``\\xNN``.  iw uses the same hexadecimal
    form for non-printable/edge bytes.  Decode only that structural alphabet,
    then apply the package SSID contract.
    """
    if not isinstance(value, str):
        raise RoamPolicyError("printable SSID value must be text")
    decoded = bytearray()
    index = 0
    while index < len(value):
        char = value[index]
        if char != "\\":
            try:
                decoded.extend(char.encode("utf-8"))
            except UnicodeEncodeError as exc:
                raise RoamPolicyError("SSID text is not valid UTF-8") from exc
            index += 1
            continue
        index += 1
        if index >= len(value):
            raise RoamPolicyError("truncated SSID escape")
        escape = value[index]
        if escape == "\\":
            decoded.append(0x5C)
            index += 1
        elif escape == '"':
            decoded.append(0x22)
            index += 1
        elif escape in {"n", "r", "t", "e"}:
            decoded.append({"n": 0x0A, "r": 0x0D, "t": 0x09, "e": 0x1B}[escape])
            index += 1
        elif escape == "x":
            digits = value[index + 1:index + 3]
            if len(digits) != 2 or not re.fullmatch(r"[0-9A-Fa-f]{2}", digits):
                raise RoamPolicyError("invalid hexadecimal SSID escape")
            decoded.append(int(digits, 16))
            index += 3
        else:
            raise RoamPolicyError("unsupported SSID escape")
    try:
        ssid = bytes(decoded).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RoamPolicyError("printable SSID is not valid UTF-8") from exc
    return validate_ssid(ssid)


def parse_wpa_ssid_value(value: str) -> str:
    """Decode a supported quoted or hexadecimal wpa_supplicant SSID value."""
    if not isinstance(value, str):
        raise RoamPolicyError("wpa_supplicant SSID value must be text")
    token = value.strip()
    if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
        ssid = token[1:-1]
    else:
        if not _WPA_HEX_RE.fullmatch(token):
            raise RoamPolicyError("SSID value must be quoted text or even hexadecimal")
        try:
            ssid = bytes.fromhex(token).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise RoamPolicyError("hexadecimal SSID is not valid UTF-8") from exc
    return validate_ssid(ssid)


def roam_policy_path(iface: str, run_dir: Optional[str] = None) -> str:
    root = run_dir or os.environ.get("WIFI_RUN_DIR", DEFAULT_RUN_DIR)
    return os.path.join(root, f"{iface}.roam-policy.json")


def load_boot_roam_policy(
    iface: str, run_dir: Optional[str] = None
) -> Dict[str, Any]:
    path = roam_policy_path(iface, run_dir=run_dir)
    try:
        with open(path, "r", encoding="utf-8") as stream:
            policy = json.load(stream)
    except (OSError, ValueError) as exc:
        raise RoamPolicyError(f"cannot load boot roam policy {path}: {exc}") from exc

    if not isinstance(policy, dict):
        raise RoamPolicyError(f"boot roam policy {path} must be an object")
    if policy.get("version") != 1:
        raise RoamPolicyError(f"unsupported boot roam policy version in {path}")
    if policy.get("iface") != iface:
        raise RoamPolicyError(
            f"boot roam policy iface mismatch in {path}: {policy.get('iface')!r}"
        )
    for key in (
        "roaming_enabled",
        "bgscan_enabled",
        "generate_network_blocks",
    ):
        if not isinstance(policy.get(key), bool):
            raise RoamPolicyError(f"boot roam policy {path}: {key} must be boolean")
    try:
        extras = validate_ssid_list(policy.get("extra_ssids"))
    except RoamPolicyError as exc:
        raise RoamPolicyError(f"boot roam policy {path}: {exc}") from exc

    # Return a detached normalized object so callers cannot mutate shared input.
    return {
        "version": 1,
        "iface": iface,
        "roaming_enabled": policy["roaming_enabled"],
        "bgscan_enabled": policy["bgscan_enabled"],
        "generate_network_blocks": policy["generate_network_blocks"],
        "extra_ssids": list(extras),
    }


def scan_backend_for_policy(policy: Dict[str, Any]) -> str:
    enabled = policy.get("roaming_enabled")
    if not isinstance(enabled, bool):
        raise RoamPolicyError("roaming_enabled must be boolean")
    return "iw" if enabled else "wpa_cli"
