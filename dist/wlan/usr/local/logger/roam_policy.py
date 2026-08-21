"""Boot-latched roaming owner/topology policy shared by Wi-Fi daemons.

The snapshot is created atomically under /run by wifi_apply_enabled.sh.  It is
deliberately not regenerated inside a daemon: /run lifetime, rather than a
mutable persisted JSON file, defines the reboot boundary.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional


DEFAULT_RUN_DIR = "/run/wifi"


class RoamPolicyError(RuntimeError):
    """The boot owner/topology snapshot is missing or cannot be trusted."""


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
    extras = policy.get("extra_ssids")
    if not isinstance(extras, list) or any(not isinstance(item, str) for item in extras):
        raise RoamPolicyError(
            f"boot roam policy {path}: extra_ssids must be a string array"
        )

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
