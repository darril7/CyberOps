"""
tests/test_trellix.py
---------------------
Standalone test — run directly on the server to verify Trellix connectivity.
Does NOT require Prefect to be running.

Usage:
    cd /opt/soc_inventory
    source .venv/bin/activate
    python tests/test_trellix.py
"""

import asyncio
import json
import sys
import os

# Make sure the project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.trellix_inventory import fetch


# ── Edit these before running ─────────────────────────────────────────────────

CREDENTIALS = {
    # Use ONE of these two auth methods:

    # Option A — API key (preferred)
    "api_key":    "YOUR_EPO_API_KEY",
    "api_secret": "YOUR_EPO_API_SECRET",

    # Option B — username/password
    # "username": "svc_inventory",
    # "password": "...",
}

PARAMS = {
    "api_base":     "https://YOUR_TRELLIX_HOST/epo/api",
    "api_version":  "v2",
    "group_filter": None,           # set to e.g. "Reduno/Office" to scope
    "verify_ssl":   False,          # set True if you have a valid cert
}

# ─────────────────────────────────────────────────────────────────────────────


async def main():
    print(f"\n[test] connecting to {PARAMS['api_base']}")
    print(f"[test] group_filter = {PARAMS.get('group_filter') or 'all'}\n")

    try:
        results = await fetch(credentials=CREDENTIALS, **PARAMS)
    except Exception as e:
        print(f"[FAIL] fetch() raised: {e}")
        sys.exit(1)

    if not results:
        print("[WARN] fetch() returned empty dict — check credentials and group_filter")
        sys.exit(1)

    print(f"[OK] {len(results)} devices returned\n")

    # Show first 5 records
    shown = 0
    for hostname, data in results.items():
        if shown >= 5:
            print(f"  ... and {len(results) - 5} more\n")
            break
        print(f"  {hostname}")
        for k, v in data.items():
            print(f"    {k:30s} = {v}")
        print()
        shown += 1

    # Verify expected keys are present
    sample = next(iter(results.values()))
    expected_keys = [
        "trellix_last_seen",
        "trellix_av_status",
        "trellix_dat_date",
        "trellix_agent_ver",
        "ip_address",
    ]
    missing = [k for k in expected_keys if k not in sample]
    if missing:
        print(f"[WARN] missing expected keys in output: {missing}")
    else:
        print(f"[OK] all expected output keys present")
        print(f"[OK] ready to register in Prefect and run via playbook\n")


if __name__ == "__main__":
    asyncio.run(main())
