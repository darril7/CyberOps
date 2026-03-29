"""
flows/ad_flow.py
----------------
Prefect flow wrapper for AD inventory.
Thin by design — all logic lives in scripts/ad_inventory.py.
"""

from prefect import flow, get_run_logger
from prefect.blocks.system import Secret

from scripts.ad_inventory import fetch


@flow(name="ad-inventory")
async def ad_inventory_flow(
    block_name: str,
    params: dict,
    task_id: str,
) -> dict:
    """
    Load credentials from Prefect block, run AD fetch, return hostname-keyed dict.
    The runner receives this dict and handles the Netbox PATCH.
    """
    logger = get_run_logger()
    logger.info(f"[{task_id}] loading credentials from block: {block_name}")

    block = await Secret.load(block_name)
    credentials = block.get()             # {"username": ..., "password": ...}

    logger.info(f"[{task_id}] starting AD fetch — domain={params.get('domain')}")
    result = await fetch(credentials=credentials, **params)

    logger.info(f"[{task_id}] AD fetch complete — {len(result)} devices returned")
    return result
