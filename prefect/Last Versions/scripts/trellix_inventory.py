"""
scripts/trellix_inventory.py
-----------------------------
Connects to Trellix ePO REST API and returns a hostname-keyed dict
of managed endpoint data.

Contract:
    async def fetch(credentials: dict, **params) -> dict[str, dict]

Credentials block (reduno/trellix-main):
    {
        "api_key":    "...",    # ePO API key  (preferred auth)
        "api_secret": "..."     # ePO API secret
    }
    -- OR --
    {
        "username": "...",      # ePO local/domain user
        "password": "..."
    }

Params from playbook.yaml:
    api_base     : "https://trellix.reduno.local/epo/api"
    api_version  : "v2"                     (default: "v2")
    group_filter : "Reduno/Office"          (optional — filter by ePO group path)
    verify_ssl   : false                    (default: true)

Return value example:
    {
        "pc-office-042": {
            "trellix_last_seen":  "2026-03-23T10:15:00",
            "trellix_av_status":  "managed",
            "trellix_dat_date":   "2026-03-22",
            "trellix_agent_ver":  "5.7.9.246",
            "ip_address":         "192.168.1.42",
            "all_ips":            ["192.168.1.42"],
        },
        ...
    }
"""

from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

log = logging.getLogger(__name__)

# ePO REST API endpoints
EPO_SYSTEM_FIND  = "/system.find"
EPO_SYSTEM_QUERY = "/system.find"   # v2 uses same endpoint with filters

# Fields to request from ePO — map to output keys below
EPO_FIELDS = [
    "EPOComputerProperties.ComputerName",
    "EPOComputerProperties.IPAddress",
    "EPOComputerProperties.OSType",
    "EPOComputerProperties.OSVersion",
    "EPOComputerProperties.LastUpdate",         # last ePO communication
    "EPOComputerProperties.AgentVersion",
    "EPOComputerProperties.DATVersion",
    "EPOComputerProperties.DATDate",
    "EPOComputerProperties.IsPortable",
    "EPOLeafNode.AgentGUID",
    "EPOLeafNode.ManagedState",
    "EPOBranchNode.AutoID",                     # group node (for group_filter)
    "EPOBranchNode.NodeName",                   # group name
]


# ── Main entry point ──────────────────────────────────────────────────────────

async def fetch(
    credentials: dict,
    *,
    api_base: str,
    api_version: str = "v2",
    group_filter: str | None = None,    # ePO group path e.g. "Reduno/Office"
    verify_ssl: bool = True,
    page_size: int = 500,
    **_extra,
) -> dict[str, dict[str, Any]]:
    """
    Pull managed system records from Trellix ePO.

    Args:
        credentials : Prefect block — api_key+api_secret OR username+password
        api_base    : ePO REST API base e.g. "https://trellix.reduno.local/epo/api"
        api_version : API version string (default "v2")
        group_filter: ePO group path to scope results to a specific site
        verify_ssl  : set False for self-signed certs (common on-prem)
        page_size   : records per page
    """
    auth    = _build_auth(credentials)
    base    = api_base.rstrip("/")
    headers = {
        "Accept":       "application/json",
        "Content-Type": "application/json",
    }

    log.info(
        f"[Trellix] connecting to {base} "
        f"| group={group_filter or 'all'}"
    )

    records = await asyncio.to_thread(
        _fetch_sync,
        base=base,
        auth=auth,
        headers=headers,
        version=api_version,
        group_filter=group_filter,
        verify_ssl=verify_ssl,
        page_size=page_size,
    )

    log.info(f"[Trellix] fetched {len(records)} managed systems")
    return records


# ── Sync implementation ───────────────────────────────────────────────────────

def _fetch_sync(
    base: str,
    auth: tuple | None,
    headers: dict,
    version: str,
    group_filter: str | None,
    verify_ssl: bool,
    page_size: int,
) -> dict[str, dict[str, Any]]:

    results: dict[str, dict[str, Any]] = {}
    offset = 0

    with httpx.Client(
        base_url=base,
        auth=auth,
        headers=headers,
        verify=verify_ssl,
        timeout=60,
    ) as client:

        while True:
            params = _build_query_params(
                version=version,
                group_filter=group_filter,
                fields=EPO_FIELDS,
                offset=offset,
                limit=page_size,
            )

            resp = client.get(f"/{version}/system.find", params=params)

            # ePO returns 200 even for auth errors — check body
            _raise_for_epo_error(resp)

            data    = resp.json()
            systems = _extract_systems(data, version)

            if not systems:
                break

            for sys in systems:
                hostname, record = _parse_system(sys)
                if hostname:
                    results[hostname.lower()] = record

            offset += len(systems)

            # ePO v2 pagination: stop when fewer results than page_size
            if len(systems) < page_size:
                break

    return results


# ── Query builder ─────────────────────────────────────────────────────────────

def _build_query_params(
    version: str,
    group_filter: str | None,
    fields: list[str],
    offset: int,
    limit: int,
) -> dict:
    params: dict[str, Any] = {
        "select":   ",".join(fields),
        "offset":   offset,
        "limit":    limit,
        "orion.user.session.timeout": 600,
    }
    if group_filter:
        # ePO searches by group path using a LIKE filter on NodeName chain
        params["where"] = (
            f'(EPOBranchNode.NodeName like "%{group_filter}%")'
        )
    return params


# ── Response parsers ──────────────────────────────────────────────────────────

def _extract_systems(data: dict, version: str) -> list[dict]:
    """
    ePO v2 wraps results differently depending on endpoint.
    Try known response shapes in order.
    """
    # v2: {"data": [...]}  or  {"data": {"devices": [...]}}
    if "data" in data:
        inner = data["data"]
        if isinstance(inner, list):
            return inner
        if isinstance(inner, dict):
            return inner.get("devices", inner.get("systems", []))
    # older ePO: top-level list
    if isinstance(data, list):
        return data
    return []


def _parse_system(sys: dict) -> tuple[str | None, dict[str, Any]]:
    """
    Parse one ePO system record into (hostname, record_dict).
    ePO returns fields either as flat keys or nested under table names.
    Handle both shapes.
    """
    props = sys.get("EPOComputerProperties", sys)    # flat or nested
    leaf  = sys.get("EPOLeafNode", {})

    hostname = (
        props.get("ComputerName")
        or props.get("computerName")
        or sys.get("ComputerName")
    )
    if not hostname:
        return None, {}

    ip = (
        props.get("IPAddress")
        or props.get("ipAddress")
        or sys.get("IPAddress")
    )

    last_seen = _parse_date(
        props.get("LastUpdate") or props.get("lastUpdate")
    )

    dat_date  = _parse_date(
        props.get("DATDate") or props.get("datDate")
    )

    managed_state = (
        leaf.get("ManagedState")
        or leaf.get("managedState")
        or props.get("ManagedState")
    )

    mac = (
        props.get("MACAddress") or props.get("macAddress")
        or sys.get("MACAddress")
    )
    os_ver = (
        props.get("OSVersion") or props.get("osVersion")
        or props.get("OSType")
    )
    malware = (
        props.get("MalwareStatus") or props.get("malwareStatus")
    )

    record: dict[str, Any] = {
        # Keys must match field_map.yaml fields: section exactly
        "last_poll":       last_seen,
        "mac_address":     mac,
        "os_version":      os_ver,
        "agent_version":   (props.get("AgentVersion") or props.get("agentVersion")),
        "malware_status":  _parse_managed_state(malware or managed_state),
        "ip_address":      ip,
        "hostname":        hostname,
        # Match key helpers
        "all_ips":         [ip] if ip else [],
    }

    return hostname, record


# ── Field parsers ─────────────────────────────────────────────────────────────

def _parse_date(raw: Any) -> str | None:
    """Normalize ePO date strings to ISO-8601."""
    if not raw:
        return None
    # ePO typically returns: "2026/03/22 10:15:00"  or ISO already
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(str(raw).strip(), fmt)
            return dt.replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue
    return str(raw)                 # return as-is if unparseable


def _parse_managed_state(state: Any) -> str | None:
    """
    ePO ManagedState values:
      1 = managed   2 = unmanaged   0 = unknown
    """
    mapping = {1: "managed", "1": "managed", True: "managed",
               2: "unmanaged", "2": "unmanaged",
               0: "unknown", "0": "unknown"}
    return mapping.get(state, str(state) if state is not None else None)


# ── Auth helper ───────────────────────────────────────────────────────────────

def _build_auth(credentials: dict) -> tuple[str, str] | None:
    """
    Return httpx Basic auth tuple from credentials block.
    ePO supports both API key/secret and username/password as Basic auth.
    API key auth: username = api_key, password = api_secret
    """
    if "api_key" in credentials:
        return (credentials["api_key"], credentials["api_secret"])
    if "username" in credentials:
        return (credentials["username"], credentials["password"])
    raise ValueError(
        "Trellix credentials block must contain either "
        "'api_key'+'api_secret' or 'username'+'password'"
    )


def _raise_for_epo_error(resp: httpx.Response):
    """
    ePO returns HTTP 200 even for auth failures, with an error in the body.
    Raise explicitly so the runner retries correctly.
    """
    resp.raise_for_status()         # catch real HTTP errors first

    try:
        body = resp.json()
    except Exception:
        return                      # non-JSON response — let caller handle

    # ePO error shape: {"status": "error", "error": "..."}
    if isinstance(body, dict):
        if body.get("status") == "error":
            raise httpx.HTTPStatusError(
                message=f"ePO API error: {body.get('error', body)}",
                request=resp.request,
                response=resp,
            )
