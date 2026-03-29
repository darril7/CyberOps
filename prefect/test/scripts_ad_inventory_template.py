"""
scripts/ad_inventory.py
-----------------------
BEFORE → hardcoded domain, keyring for creds
AFTER  → accepts credentials dict + params kwargs, no hardcoding

Pattern applies to ALL scripts:
  - No hardcoded hostnames, domains, OUs, URLs
  - No keyring / os.environ credential access
  - Accept `credentials` dict (loaded from Prefect block by runner)
  - Accept **params (everything else from playbook)
  - Return structured result dict for logging/reporting
"""

from __future__ import annotations
import asyncio
import ldap3                        # or whatever your AD library is
from typing import Any


# ── BEFORE (what NOT to do anymore) ──────────────────────────────────────────
#
# import keyring
# DOMAIN = "corp.customera.local"
# SEARCH_BASE = "DC=corp,DC=customera,DC=local"
#
# def run():
#     password = keyring.get_password("ad", "svc_inventory")
#     conn = ldap3.Connection(DOMAIN, user="svc_inventory", password=password)
#     ...
#
# ── AFTER ─────────────────────────────────────────────────────────────────────


async def run_ad_inventory(
    credentials: dict,
    *,
    # These come from playbook params — all optional with sensible defaults
    domain: str,
    search_base: str,
    ou_filter: str | None = None,
    page_size: int = 500,
    **extra_params,               # absorb any future params without breaking
) -> dict[str, Any]:
    """
    Pull computer objects from AD and return structured inventory data.

    Args:
        credentials:  {'username': ..., 'password': ...}  loaded from Prefect block
        domain:       FQDN of the AD domain
        search_base:  LDAP base DN
        ou_filter:    Optional OU DN to scope the search (site-specific override)
        page_size:    LDAP paging size
    """
    username = credentials["username"]
    password = credentials["password"]

    # Use ou_filter if provided, otherwise fall back to full search_base
    effective_base = ou_filter if ou_filter else search_base

    server = ldap3.Server(domain, get_info=ldap3.ALL)
    conn = ldap3.Connection(
        server,
        user=f"{domain}\\{username}",
        password=password,
        authentication=ldap3.NTLM,
        auto_bind=True,
    )

    conn.search(
        search_base=effective_base,
        search_filter="(objectClass=computer)",
        attributes=["cn", "operatingSystem", "lastLogonTimestamp", "distinguishedName"],
        paged_size=page_size,
    )

    computers = [
        {
            "name": entry.cn.value,
            "os": entry.operatingSystem.value if entry.operatingSystem else None,
            "last_logon": entry.lastLogonTimestamp.value,
            "dn": entry.distinguishedName.value,
        }
        for entry in conn.entries
    ]

    return {
        "source": "ad",
        "domain": domain,
        "search_base": effective_base,
        "count": len(computers),
        "computers": computers,
    }


# ── Same pattern for WSUS ─────────────────────────────────────────────────────
# scripts/wsus_inventory.py would look like:
#
# async def run_wsus_inventory(
#     credentials: dict,
#     *,
#     server_url: str,
#     target_type: str,    # "server" or "workstation"
#     **extra_params,
# ) -> dict:
#     ...
#
# ── Same pattern for GLPI ─────────────────────────────────────────────────────
# async def run_glpi_sync(
#     credentials: dict,
#     *,
#     base_url: str,
#     verify_ssl: bool = True,
#     **extra_params,
# ) -> dict:
#     ...
#
# ── Same pattern for Trellix ──────────────────────────────────────────────────
# async def run_trellix_inventory(
#     credentials: dict,
#     *,
#     api_version: str,
#     tenant_id: str,
#     **extra_params,
# ) -> dict:
#     ...
