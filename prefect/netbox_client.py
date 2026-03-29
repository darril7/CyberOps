"""
runner/netbox_client.py
-----------------------
Netbox query + patch module.

Responsibilities:
  1. Query dcim/devices and virtualization/virtual-machines
     filtered by tenant + site
  2. Match tool output records to Netbox objects using
     ordered match keys (hostname → ip → mac → vm_name → serial)
  3. PATCH only the custom fields owned by the current tool

Nothing here knows about tools or business rules — it only
knows how to talk to Netbox and how to match/update records.
"""

from __future__ import annotations
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger(__name__)


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class NetboxDevice:
    """Unified representation of a Netbox device or VM."""
    id: int
    name: str                           # display name / hostname
    object_type: str                    # "device" or "vm"
    device_role: str                    # server | workstation | appliance
    tenant: str | None
    site: str | None
    primary_ip: str | None              # primary_ip4 address without prefix
    all_ips: list[str] = field(default_factory=list)
    mac_addresses: list[str] = field(default_factory=list)
    vm_name: str | None = None          # custom field: guest OS hostname
    serial: str | None = None
    platform: str | None = None         # OS platform (windows/linux)
    raw: dict = field(default_factory=dict)

    @property
    def url_path(self) -> str:
        if self.object_type == "device":
            return f"dcim/devices/{self.id}"
        return f"virtualization/virtual-machines/{self.id}"


@dataclass
class MatchResult:
    device: NetboxDevice
    matched_by: str                     # which key made the match
    tool_record: dict                   # raw record from tool script


# ── Netbox client ─────────────────────────────────────────────────────────────

class NetboxClient:
    def __init__(self, base_url: str, token: str, verify_ssl: bool = True):
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "Authorization": f"Token {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        self.verify_ssl = verify_ssl

    # ── Fetch devices for a tenant + site ─────────────────────────────────────

    async def get_devices(
        self,
        tenant_slug: str,
        site_slug: str,
        device_roles: list[str],
    ) -> list[NetboxDevice]:
        """
        Query dcim/devices filtered by tenant + site + device_role slugs.
        Returns unified NetboxDevice list.
        """
        params = {
            "tenant": tenant_slug,
            "site": site_slug,
            "role": device_roles,       # httpx sends repeated params as list
            "limit": 1000,
        }
        records = await self._paginate("dcim/devices", params)
        devices = [self._parse_device(r) for r in records]
        # Enrich with interface IPs and MACs in one batch
        await self._enrich_interfaces(devices, "dcim/interfaces", "device_id")
        log.info(
            f"[Netbox] {tenant_slug}/{site_slug} devices: "
            f"{len(devices)} ({', '.join(device_roles)})"
        )
        return devices

    async def get_vms(
        self,
        tenant_slug: str,
        site_slug: str,
        device_roles: list[str] | None = None,
    ) -> list[NetboxDevice]:
        """
        Query virtualization/virtual-machines filtered by tenant + site.
        Optionally filter by device_role slugs.
        site maps to cluster__site in Netbox VM API.
        """
        params = {
            "tenant": tenant_slug,
            "cluster__site": site_slug,
            "limit": 1000,
        }
        if device_roles:
            params["role"] = device_roles
        records = await self._paginate("virtualization/virtual-machines", params)
        vms = [self._parse_vm(r) for r in records]
        await self._enrich_interfaces(vms, "virtualization/interfaces", "virtual_machine_id")
        log.info(
            f"[Netbox] {tenant_slug}/{site_slug} VMs: {len(vms)}"
        )
        return vms

    # ── Match tool output to Netbox objects ───────────────────────────────────

    def match(
        self,
        tool_records: dict[str, dict],      # hostname-keyed dict from script
        netbox_objects: list[NetboxDevice],
        match_keys: list[str],              # ordered: e.g. ["hostname","ip_address","mac_address"]
        tool_ip_field: str = "ip_address",
        tool_mac_field: str = "mac_address",
        tool_vmname_field: str = "vm_name",
    ) -> list[MatchResult]:
        """
        Match each tool record to a Netbox object using ordered match keys.
        First key that produces a hit wins.

        match_keys can contain:
          hostname     — tool record key (lowercased) vs netbox device name
          ip_address   — tool_ip_field vs netbox primary_ip + all_ips
          mac_address  — tool_mac_field vs netbox mac_addresses
          vm_name      — tool_vmname_field vs netbox vm_name custom field
          serial       — tool record "serial" key vs netbox serial
        """
        # Build lookup indexes for O(1) matching
        by_hostname: dict[str, NetboxDevice] = {}
        by_ip:       dict[str, NetboxDevice] = {}
        by_mac:      dict[str, NetboxDevice] = {}
        by_vmname:   dict[str, NetboxDevice] = {}
        by_serial:   dict[str, NetboxDevice] = {}

        for dev in netbox_objects:
            # hostname index — strip domain suffix, lowercase
            short = dev.name.split(".")[0].lower()
            by_hostname[short] = dev
            by_hostname[dev.name.lower()] = dev

            # IP index
            for ip in [dev.primary_ip] + dev.all_ips:
                if ip:
                    by_ip[ip] = dev

            # MAC index
            for mac in dev.mac_addresses:
                by_mac[_norm_mac(mac)] = dev

            # vm_name index
            if dev.vm_name:
                short_vm = dev.vm_name.split(".")[0].lower()
                by_vmname[short_vm] = dev
                by_vmname[dev.vm_name.lower()] = dev

            # serial index
            if dev.serial:
                by_serial[dev.serial.upper()] = dev

        results: list[MatchResult] = []
        unmatched: list[str] = []

        for record_key, record in tool_records.items():
            matched = None
            matched_by = None

            for key in match_keys:
                if key == "hostname":
                    lookup = record_key.split(".")[0].lower()
                    if lookup in by_hostname:
                        matched = by_hostname[lookup]
                        matched_by = "hostname"
                        break

                elif key == "ip_address":
                    ip = record.get(tool_ip_field)
                    if ip and ip in by_ip:
                        matched = by_ip[ip]
                        matched_by = "ip_address"
                        break
                    # also try all IPs the tool returned for this record
                    for extra_ip in record.get("all_ips", []):
                        if extra_ip in by_ip:
                            matched = by_ip[extra_ip]
                            matched_by = f"ip_address({extra_ip})"
                            break
                    if matched:
                        break

                elif key == "mac_address":
                    mac = record.get(tool_mac_field)
                    if not mac:
                        continue      # tool didn't return a MAC — try next key
                    if not by_mac:
                        continue      # no Netbox devices have MACs registered — skip
                    norm = _norm_mac(mac)
                    if norm in by_mac:
                        matched = by_mac[norm]
                        matched_by = "mac_address"
                        break

                elif key == "vm_name":
                    vmn = record.get(tool_vmname_field, "")
                    if vmn:
                        lookup = vmn.split(".")[0].lower()
                        if lookup in by_vmname:
                            matched = by_vmname[lookup]
                            matched_by = "vm_name"
                            break

                elif key == "serial_number":
                    serial = record.get("serial", "")
                    if serial and serial.upper() in by_serial:
                        matched = by_serial[serial.upper()]
                        matched_by = "serial_number"
                        break

            if matched:
                results.append(MatchResult(
                    device=matched,
                    matched_by=matched_by,
                    tool_record=record,
                ))
            else:
                unmatched.append(record_key)

        if unmatched:
            log.warning(
                f"[Netbox] {len(unmatched)} records unmatched: "
                f"{unmatched[:10]}{'...' if len(unmatched) > 10 else ''}"
            )

        log.info(
            f"[Netbox] matched {len(results)}/{len(tool_records)} records"
        )
        return results

    # ── Patch custom fields ───────────────────────────────────────────────────

    async def patch_custom_fields(
        self,
        match_results: list[MatchResult],
        field_mapping: dict[str, str],      # script_key: cf_netbox_field
        dry_run: bool = False,
    ) -> dict[str, int]:
        """
        PATCH custom_fields on each matched Netbox object.
        Only writes fields defined in field_mapping — never touches
        fields owned by other tools.

        Returns summary: {"patched": N, "skipped": N, "failed": N}
        """
        summary = {"patched": 0, "skipped": 0, "failed": 0}

        async with httpx.AsyncClient(
            base_url=self.base_url,
            headers=self.headers,
            verify=self.verify_ssl,
            timeout=30,
        ) as client:
            tasks = [
                self._patch_one(
                    client, mr, field_mapping, dry_run, summary
                )
                for mr in match_results
            ]
            await asyncio.gather(*tasks)

        log.info(
            f"[Netbox] patch complete — "
            f"patched={summary['patched']} "
            f"skipped={summary['skipped']} "
            f"failed={summary['failed']}"
        )
        return summary

    async def _patch_one(
        self,
        client: httpx.AsyncClient,
        mr: MatchResult,
        field_mapping: dict[str, str],
        dry_run: bool,
        summary: dict,
    ):
        # Build the custom_fields payload from tool record
        custom_fields = {}
        for script_key, cf_name in field_mapping.items():
            value = mr.tool_record.get(script_key)
            if value is not None:
                custom_fields[cf_name] = value

        if not custom_fields:
            summary["skipped"] += 1
            return

        url = f"/api/{mr.device.url_path}/"
        payload = {"custom_fields": custom_fields}

        if dry_run:
            log.info(
                f"[DRY RUN] PATCH {url} "
                f"matched_by={mr.matched_by} "
                f"fields={list(custom_fields.keys())}"
            )
            summary["patched"] += 1
            return

        try:
            resp = await client.patch(url, json=payload)
            resp.raise_for_status()
            summary["patched"] += 1
            log.debug(
                f"[Netbox] PATCH {mr.device.name} "
                f"({mr.matched_by}) → {list(custom_fields.keys())}"
            )
        except httpx.HTTPStatusError as e:
            summary["failed"] += 1
            log.error(
                f"[Netbox] PATCH failed {mr.device.name}: "
                f"{e.response.status_code} {e.response.text[:200]}"
            )

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _paginate(self, endpoint: str, params: dict) -> list[dict]:
        """Follow Netbox pagination and return all results."""
        results = []
        url = f"{self.base_url}/api/{endpoint}/"
        async with httpx.AsyncClient(
            headers=self.headers,
            verify=self.verify_ssl,
            timeout=30,
        ) as client:
            while url:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
                results.extend(data.get("results", []))
                url = data.get("next")
                params = {}  # next URL already has params encoded
        return results

    async def _enrich_interfaces(
        self,
        devices: list[NetboxDevice],
        endpoint: str,
        id_field: str,
    ):
        """
        Batch-fetch interfaces for all devices and attach IPs + MACs.
        Runs concurrently for all devices.

        Not all devices have interfaces registered in Netbox — that is normal.
        Devices with no interfaces simply get empty mac_addresses / all_ips.
        MAC matching is opportunistic: skipped silently when not available
        on either side (tool record or Netbox device).
        """
        with_mac = 0
        with_ip  = 0

        async def fetch_one(dev: NetboxDevice):
            nonlocal with_mac, with_ip
            params = {id_field: dev.id, "limit": 100}
            try:
                records = await self._paginate(endpoint, params)
                for iface in records:
                    mac = iface.get("mac_address")
                    if mac:
                        dev.mac_addresses.append(_norm_mac(mac))
                        with_mac += 1
                    for ip_obj in iface.get("ip_addresses", []):
                        addr = ip_obj.get("address", "").split("/")[0]
                        if addr and addr not in dev.all_ips:
                            dev.all_ips.append(addr)
                            with_ip += 1
            except Exception as e:
                log.debug(f"[Netbox] interface fetch failed for {dev.name}: {e}")

        await asyncio.gather(*[fetch_one(d) for d in devices])
        log.debug(
            f"[Netbox] interfaces enriched — "
            f"{with_mac} MACs, {with_ip} extra IPs across {len(devices)} devices "
            f"({len(devices) - with_mac} devices have no MAC registered — normal)"
        )

    def _parse_device(self, r: dict) -> NetboxDevice:
        return NetboxDevice(
            id=r["id"],
            name=r.get("name", ""),
            object_type="device",
            device_role=_slug(r, "role"),
            tenant=_slug(r, "tenant"),
            site=_slug(r, "site"),
            primary_ip=_ip(r.get("primary_ip4")),
            serial=r.get("serial") or None,
            platform=_slug(r, "platform"),
            vm_name=r.get("custom_fields", {}).get("vm_name"),
            raw=r,
        )

    def _parse_vm(self, r: dict) -> NetboxDevice:
        return NetboxDevice(
            id=r["id"],
            name=r.get("name", ""),
            object_type="vm",
            device_role=_slug(r, "role"),
            tenant=_slug(r, "tenant"),
            site=_slug(r.get("cluster", {}), "site") if r.get("cluster") else None,
            primary_ip=_ip(r.get("primary_ip4")),
            platform=_slug(r, "platform"),
            vm_name=r.get("custom_fields", {}).get("vm_name"),
            raw=r,
        )


# ── Utilities ─────────────────────────────────────────────────────────────────

def _slug(obj: dict | None, key: str) -> str | None:
    if not obj:
        return None
    val = obj.get(key)
    if isinstance(val, dict):
        return val.get("slug")
    return val or None


def _ip(obj: dict | None) -> str | None:
    if not obj:
        return None
    addr = obj.get("address", "")
    return addr.split("/")[0] if addr else None


def _norm_mac(mac: str) -> str:
    """Normalize MAC to lowercase colon-separated: aa:bb:cc:dd:ee:ff"""
    return mac.lower().replace("-", ":").replace(".", ":")
