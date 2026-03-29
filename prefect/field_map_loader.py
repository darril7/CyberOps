"""
runner/field_map_loader.py
--------------------------
Loads field_map.yaml and answers:
  - Is this tool applicable to this device?
  - What match keys should be used?
  - What fields should be patched?
  - Are there OS conditions to check first?
"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import yaml

from runner.netbox_client import NetboxDevice


@dataclass
class ToolMap:
    """Resolved config for one tool from field_map.yaml."""
    tool: str
    match_keys: list[str]
    vm_match_keys: list[str]           # vCenter has different VM match strategy
    applicable_device_roles: list[str]
    applicable_vms: bool
    vm_condition: dict                 # {device_role: [...], os_family: "windows"}
    device_condition: dict             # {os_family: "windows"}
    fields: dict[str, str]            # script_key → cf_netbox_field
    vm_fields: dict[str, str] | None  # vCenter has separate VM fields
    not_applicable: list[str]


class FieldMapLoader:
    def __init__(self, path: str | Path = "config/field_map.yaml"):
        self.path = Path(path)
        self._data: dict = {}

    def load(self) -> dict[str, ToolMap]:
        with open(self.path) as f:
            raw = yaml.safe_load(f)
        return {
            tool: self._parse(tool, cfg)
            for tool, cfg in raw.items()
            if not tool.startswith("_")   # skip comment keys
        }

    def get(self, tool: str) -> ToolMap:
        maps = self.load()
        if tool not in maps:
            raise KeyError(
                f"Tool '{tool}' not found in field_map.yaml. "
                f"Available: {list(maps.keys())}"
            )
        return maps[tool]

    def _parse(self, tool: str, cfg: dict) -> ToolMap:
        return ToolMap(
            tool=tool,
            match_keys=cfg.get("match_keys", ["hostname", "ip_address"]),
            vm_match_keys=cfg.get("vm_match_keys", cfg.get("match_keys", ["hostname", "ip_address"])),
            applicable_device_roles=cfg.get("applicable_device_roles", []),
            applicable_vms=cfg.get("applicable_vms", False),
            vm_condition=cfg.get("vm_condition", {}),
            device_condition=cfg.get("device_condition", {}),
            fields=cfg.get("fields", {}),
            vm_fields=cfg.get("vm_fields"),
            not_applicable=cfg.get("not_applicable", []),
        )


# ── Applicability checks ──────────────────────────────────────────────────────

def is_applicable(device: NetboxDevice, tool_map: ToolMap) -> tuple[bool, str]:
    """
    Returns (applicable: bool, reason: str).
    Checks device_role + object_type + OS conditions.
    """
    role = device.device_role

    if device.object_type == "device":
        if role in tool_map.not_applicable:
            return False, f"role '{role}' in not_applicable"

        if role not in tool_map.applicable_device_roles:
            return False, f"role '{role}' not in applicable_device_roles"

        # OS condition for physical devices (e.g. WSUS/AD Windows-only)
        if tool_map.device_condition:
            os_req = tool_map.device_condition.get("os_family")
            if os_req and not _os_matches(device, os_req):
                return False, f"os_family condition not met (requires {os_req})"

        return True, "ok"

    elif device.object_type == "vm":
        if not tool_map.applicable_vms:
            return False, "applicable_vms=false"

        # VM role condition (e.g. Trellix only for server/workstation VMs)
        if tool_map.vm_condition:
            allowed_roles = tool_map.vm_condition.get("device_role", [])
            if allowed_roles and role not in allowed_roles:
                return False, f"VM role '{role}' not in vm_condition.device_role"

            os_req = tool_map.vm_condition.get("os_family")
            if os_req and not _os_matches(device, os_req):
                return False, f"VM os_family condition not met (requires {os_req})"

        return True, "ok"

    return False, f"unknown object_type '{device.object_type}'"


def _os_matches(device: NetboxDevice, required: str) -> bool:
    """
    Check device OS against requirement.
    Uses Netbox platform slug — e.g. "windows-server-2022" contains "windows".
    Returns True if platform is unknown (don't skip devices with no OS set).
    """
    if not device.platform:
        return True                     # no platform set — don't exclude
    return required.lower() in device.platform.lower()


def get_match_keys(device: NetboxDevice, tool_map: ToolMap) -> list[str]:
    """Return the correct ordered match key list for this device type."""
    if device.object_type == "vm":
        return tool_map.vm_match_keys
    return tool_map.match_keys


def get_fields(device: NetboxDevice, tool_map: ToolMap) -> dict[str, str]:
    """Return the correct field mapping for this device type."""
    if device.object_type == "vm" and tool_map.vm_fields:
        return tool_map.vm_fields
    return tool_map.fields
