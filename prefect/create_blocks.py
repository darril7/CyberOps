"""
create_blocks.py
----------------
Run directly — not as a heredoc — so environment is inherited cleanly.

Usage:
    source /opt/soc_inventory/.venv/bin/activate
    export PREFECT_API_URL="http://127.0.0.1:4200/api"
    cd /opt/soc_inventory
    python create_blocks.py

    # To create only one client at a time:
    python create_blocks.py --client client1
    python create_blocks.py --client netbox
"""

import asyncio
import argparse
import os
import sys

# ── Verify API URL before doing anything ─────────────────────────────────────

api_url = os.environ.get("PREFECT_API_URL") or "https://prefect.reduno.online/api"
print(f"\n  Prefect API URL : {api_url}")

if "prefect.cloud" in api_url:
    print("\n  ERROR: PREFECT_API_URL points to Prefect Cloud, not your local server.")
    print("  Fix: export PREFECT_API_URL=http://127.0.0.1:4200/api")
    sys.exit(1)

# ── Verify server is reachable before creating blocks ────────────────────────

import urllib.request
import urllib.error

def check_server(url: str):
    health = url.rstrip("/api").rstrip("/") + "/api/health"
    try:
        with urllib.request.urlopen(health, timeout=5) as r:
            body = r.read().decode()
            if "healthy" in body:
                print(f"  Server health   : OK ({health})")
                return True
            print(f"  Server health   : unexpected response: {body}")
            return False
    except urllib.error.URLError as e:
        print(f"\n  ERROR: Cannot reach Prefect server at {health}")
        print(f"  Reason: {e}")
        print(f"  Fix: make sure 'prefect server start' is running")
        sys.exit(1)

check_server(api_url)

# ── Set env so Prefect SDK picks it up ───────────────────────────────────────

os.environ["PREFECT_API_URL"] = api_url

from prefect.blocks.system import Secret

# =============================================================================
# BLOCK DEFINITIONS
# Edit credential values ("...") before running.
# =============================================================================

ALL_BLOCKS = {

    # ── Netbox ────────────────────────────────────────────────────────────────
    "netbox": {
        "netbox-api": {
            "url":   "https://netbox.reduno.online",
            "token": "YOUR_NETBOX_API_TOKEN",
        },
    },

    # ── Client 1 (sites: c1-office1, c1-dc1) ─────────────────────────────────
    "client1": {

        # Shared across all client1 sites
        "client1-ad-main": {
            "username": "svc_inventory",
            "password": "...",
        },
        "client1-glpi-main": {
            "app_token":  "...",
            "user_token": "...",
        },
        "client1-trellix-main": {
            "api_key":    "...",
            "api_secret": "...",
        },
        "client1-wazuh-main": {
            "username": "wazuh-api-user",
            "password": "...",
            "host":     "wazuh.client1.local",
        },
        "client1-elastic-main": {
            "api_key": "...",
            "host":    "elastic.client1.local",
        },
        "client1-nessus-main": {
            "access_key": "...",
            "secret_key": "...",
            "host":       "nessus.client1.local",
        },
        "client1-teramind-main": {
            "client_id":     "...",
            "client_secret": "...",
            "host":          "teramind.client1.local",
        },

        # Site-specific — one per site
        "client1-wsus-c1-office1": {
            "username": "svc_wsus",
            "password": "...",
            "host":     "wsus-office1.client1.local",
        },
        "client1-wsus-c1-dc1": {
            "username": "svc_wsus",
            "password": "...",
            "host":     "wsus-dc1.client1.local",
        },
        "client1-fortigate-c1-office1": {
            "api_token": "...",
            "host":      "fw-office1.client1.local",
        },
        "client1-fortigate-c1-dc1": {
            "api_token": "...",
            "host":      "fw-dc1.client1.local",
        },
        "client1-vcenter-c1-dc1": {
            "username": "svc_inventory@vsphere.local",
            "password": "...",
            "host":     "vcenter-dc1.client1.local",
        },
    },

    # ── Client 2 (sites: c2-office1) ─────────────────────────────────────────
    "client2": {
        "client2-ad-main": {
            "username": "svc_inventory",
            "password": "...",
        },
        "client2-glpi-main": {
            "app_token":  "...",
            "user_token": "...",
        },
        "client2-trellix-main": {
            "api_key":    "...",
            "api_secret": "...",
        },
        "client2-nessus-main": {
            "access_key": "...",
            "secret_key": "...",
            "host":       "nessus.client2.local",
        },
        "client2-fortigate-c2-office1": {
            "api_token": "...",
            "host":      "fw-office1.client2.local",
        },
    },

    # ── Client 3 (sites: c3-office1, c3-dc1, c3-dc2) ─────────────────────────
    "client3": {
        "client3-ad-main": {
            "username": "svc_inventory",
            "password": "...",
        },
        "client3-glpi-main": {
            "app_token":  "...",
            "user_token": "...",
        },
        "client3-trellix-main": {
            "api_key":    "...",
            "api_secret": "...",
        },
        "client3-mdm-main": {
            "client_id":     "...",
            "client_secret": "...",
            "tenant_id":     "...",
        },
        "client3-teramind-main": {
            "client_id":     "...",
            "client_secret": "...",
            "host":          "teramind.client3.local",
        },
        "client3-wsus-c3-office1": {
            "username": "svc_wsus",
            "password": "...",
            "host":     "wsus-office1.client3.local",
        },
        "client3-wsus-c3-dc1": {
            "username": "svc_wsus",
            "password": "...",
            "host":     "wsus-dc1.client3.local",
        },
        "client3-wsus-c3-dc2": {
            "username": "svc_wsus",
            "password": "...",
            "host":     "wsus-dc2.client3.local",
        },
        "client3-vcenter-c3-dc1": {
            "username": "svc_inventory@vsphere.local",
            "password": "...",
            "host":     "vcenter-dc1.client3.local",
        },
        "client3-vcenter-c3-dc2": {
            "username": "svc_inventory@vsphere.local",
            "password": "...",
            "host":     "vcenter-dc2.client3.local",
        },
        "client3-fortigate-c3-office1": {
            "api_token": "...",
            "host":      "fw-office1.client3.local",
        },
        "client3-fortigate-c3-dc1": {
            "api_token": "...",
            "host":      "fw-dc1.client3.local",
        },
        "client3-fortigate-c3-dc2": {
            "api_token": "...",
            "host":      "fw-dc2.client3.local",
        },
        "client3-nessus-c3-dc1": {
            "access_key": "...",
            "secret_key": "...",
            "host":       "nessus-dc1.client3.local",
        },
        "client3-nessus-c3-dc2": {
            "access_key": "...",
            "secret_key": "...",
            "host":       "nessus-dc2.client3.local",
        },
    },
}


# =============================================================================
# Runner
# =============================================================================

async def create_blocks(target_client: str | None = None):
    groups = (
        {target_client: ALL_BLOCKS[target_client]}
        if target_client
        else ALL_BLOCKS
    )

    all_blocks = {}
    for group in groups.values():
        all_blocks.update(group)

    print(f"\n  Creating {len(all_blocks)} block(s)...\n")

    ok = 0
    failed = 0
    for name, value in all_blocks.items():
        try:
            await Secret(value=value).save(name, overwrite=True)
            print(f"  ✓  {name}")
            ok += 1
        except Exception as e:
            print(f"  ✗  {name}  →  {e}")
            failed += 1

    print(f"\n  Done — {ok} created, {failed} failed")
    print(f"  Verify at: http://127.0.0.1:4200/blocks\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--client",
        choices=list(ALL_BLOCKS.keys()),
        default=None,
        help="Create blocks for one client only (default: all)",
    )
    args = parser.parse_args()

    if args.client and args.client not in ALL_BLOCKS:
        print(f"Unknown client '{args.client}'. Choose from: {list(ALL_BLOCKS.keys())}")
        sys.exit(1)

    asyncio.run(create_blocks(args.client))
