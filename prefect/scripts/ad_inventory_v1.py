"""
scripts/ad_inventory.py
-----------------------
Returns a hostname-keyed dict of AD computer data.
No Netbox knowledge. No hardcoded values. No keyring.

Contract:
    async def fetch(credentials: dict, **params) -> dict[str, dict]

    Return value example:
    {
        "PC-OFFICE-042": {
            "ad_last_seen":    "2026-03-22T14:30:00",
            "ad_os":           "Windows 11 Pro 23H2",
            "ad_ou":           "OU=Office,OU=Workstations,DC=corp,DC=customera,DC=local",
            "ad_enabled":      True,
            "ad_description":  "Daf's laptop",
        },
        "SRV-DC-001": { ... },
    }
    Keys are always lowercase hostnames to make matching predictable.
"""

from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import ldap3
from ldap3 import Server, Connection, ALL, NTLM, SUBTREE
from ldap3.utils.conv import format_ad_timestamp

log = logging.getLogger(__name__)

# Fields to pull from AD — add here if you need more, field_map.yaml controls
# which ones actually get written to Netbox
AD_ATTRIBUTES = [
    "cn",
    "dNSHostName",
    "operatingSystem",
    "operatingSystemVersion",
    "lastLogonTimestamp",
    "whenCreated",
    "description",
    "distinguishedName",
    "enabled",           # requires ldap3 extended attributes
    "userAccountControl",
]


# ── Main entry point ──────────────────────────────────────────────────────────

async def fetch(
    credentials: dict,
    *,
    domain: str,
    search_base: str,
    ou_filter: str | None = None,
    ldap_port: int = 636,
    use_ssl: bool = True,
    page_size: int = 500,
    **_extra,                     # absorb unknown playbook params safely
) -> dict[str, dict[str, Any]]:
    """
    Pull computer objects from AD.

    Args:
        credentials:  Prefect block value — expects keys: username, password
        domain:       FQDN e.g. "corp.customera.local"
        search_base:  Base DN e.g. "DC=corp,DC=customera,DC=local"
        ou_filter:    Optional OU DN to narrow scope for a specific site.
                      e.g. "OU=Office,DC=corp,DC=customera,DC=local"
                      Falls back to search_base if not provided.
        ldap_port:    636 (LDAPS) by default
        use_ssl:      True by default
        page_size:    LDAP paging size

    Returns:
        dict keyed by lowercase hostname → dict of raw AD field values
    """
    username = credentials["username"]
    password = credentials["password"]
    effective_base = ou_filter or search_base

    log.info(f"[AD] connecting to {domain} | scope: {effective_base}")

    # Run blocking ldap3 calls in a thread so the Prefect event loop stays free
    return await asyncio.to_thread(
        _fetch_sync,
        domain=domain,
        username=username,
        password=password,
        search_base=effective_base,
        ldap_port=ldap_port,
        use_ssl=use_ssl,
        page_size=page_size,
    )


# ── Sync implementation (runs in thread) ──────────────────────────────────────

def _fetch_sync(
    domain: str,
    username: str,
    password: str,
    search_base: str,
    ldap_port: int,
    use_ssl: bool,
    page_size: int,
) -> dict[str, dict[str, Any]]:

    server = Server(domain, port=ldap_port, use_ssl=use_ssl, get_info=ALL)
    conn = Connection(
        server,
        user=f"{domain}\\{username}",
        password=password,
        authentication=NTLM,
        auto_bind=True,
    )

    # Paged search — handles large directories cleanly
    conn.search(
        search_base=search_base,
        search_filter="(objectClass=computer)",
        search_scope=SUBTREE,
        attributes=AD_ATTRIBUTES,
        paged_size=page_size,
    )

    results: dict[str, dict[str, Any]] = {}

    for entry in conn.entries:
        hostname = _resolve_hostname(entry)
        if not hostname:
            continue

        ip = _str(entry, "dNSHostName") or None
        results[hostname.lower()] = {
            # Keys must match field_map.yaml fields: section exactly
            "last_logon":  _parse_timestamp(entry, "lastLogonTimestamp"),
            "os_name":     _str(entry, "operatingSystem"),
            "status":      _parse_enabled(entry),
            "name":        _str(entry, "dNSHostName"),
            "ip_address":  ip.split(".")[0] if ip else None,
        }

    log.info(f"[AD] fetched {len(results)} computer objects from {search_base}")
    conn.unbind()
    return results


# ── Field parsers ─────────────────────────────────────────────────────────────

def _resolve_hostname(entry) -> str | None:
    """Prefer dNSHostName short name, fall back to cn."""
    dns = _str(entry, "dNSHostName")
    if dns:
        return dns.split(".")[0]          # strip domain suffix
    cn = _str(entry, "cn")
    return cn if cn else None


def _str(entry, attr: str) -> str | None:
    try:
        val = getattr(entry, attr).value
        return str(val).strip() if val else None
    except Exception:
        return None


def _parse_timestamp(entry, attr: str) -> str | None:
    """Convert AD FILETIME integer to ISO-8601 string."""
    try:
        raw = getattr(entry, attr).value
        if not raw:
            return None
        # ldap3 may return a datetime directly or an integer FILETIME
        if isinstance(raw, datetime):
            return raw.astimezone(timezone.utc).isoformat()
        # integer FILETIME (100-nanosecond intervals since 1601-01-01)
        epoch_diff = 116444736000000000
        ts = (int(raw) - epoch_diff) / 10_000_000
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except Exception:
        return None


def _parse_ou(entry) -> str | None:
    """Extract the first OU component from the distinguishedName."""
    try:
        dn = str(entry.distinguishedName.value)
        parts = [p.strip() for p in dn.split(",")]
        ous = [p[3:] for p in parts if p.upper().startswith("OU=")]
        return ous[0] if ous else None
    except Exception:
        return None


def _parse_enabled(entry) -> bool | None:
    """userAccountControl bit 2 = ACCOUNTDISABLE."""
    try:
        uac = getattr(entry, "userAccountControl").value
        if uac is None:
            return None
        return not bool(int(uac) & 0x0002)
    except Exception:
        return None
