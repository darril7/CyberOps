"""
flow_registry.py
----------------
Maps tool identifiers (from the playbook) to their Prefect flow functions.
Add a new script here when onboarding a new tool.
"""

from prefect import flow, task, get_run_logger
from prefect.blocks.system import Secret
from prefect_aws import AwsSecret          # swap for whatever vault you use
# from prefect.blocks.core import Block    # generic fallback

from typing import Any, Callable


# ── Block helper ─────────────────────────────────────────────────────────────

async def load_block_credentials(block_name: str) -> dict:
    """
    Load credentials from a Prefect block.
    Block name format: "customer-a/ad-main"
    Assumes the block stores a JSON dict (username, password, token, etc.)
    Adjust the block type to match what you registered in Prefect Cloud.
    """
    logger = get_run_logger()
    logger.info(f"Loading credentials from block: {block_name}")
    block = await Secret.load(block_name)
    return block.get()          # returns the stored secret value


# ── Individual tool flows ─────────────────────────────────────────────────────
# Each flow accepts (credentials: dict, params: dict, task_id: str)
# and is responsible for its own logic.
# Keep imports lazy inside the flow to avoid loading unused dependencies.

@flow(name="ad-inventory")
async def ad_inventory_flow(credentials: dict, params: dict, task_id: str):
    from scripts.ad_inventory import run_ad_inventory
    logger = get_run_logger()
    logger.info(f"[{task_id}] Starting AD inventory")
    await run_ad_inventory(credentials=credentials, **params)


@flow(name="wsus-inventory")
async def wsus_inventory_flow(credentials: dict, params: dict, task_id: str):
    from scripts.wsus_inventory import run_wsus_inventory
    logger = get_run_logger()
    logger.info(f"[{task_id}] Starting WSUS inventory — type={params.get('target_type')}")
    await run_wsus_inventory(credentials=credentials, **params)


@flow(name="glpi-sync")
async def glpi_sync_flow(credentials: dict, params: dict, task_id: str):
    from scripts.glpi_sync import run_glpi_sync
    logger = get_run_logger()
    logger.info(f"[{task_id}] Starting GLPI sync")
    await run_glpi_sync(credentials=credentials, **params)


@flow(name="trellix-inventory")
async def trellix_inventory_flow(credentials: dict, params: dict, task_id: str):
    from scripts.trellix_inventory import run_trellix_inventory
    logger = get_run_logger()
    logger.info(f"[{task_id}] Starting Trellix inventory")
    await run_trellix_inventory(credentials=credentials, **params)


# ── Registry ─────────────────────────────────────────────────────────────────

FLOW_REGISTRY: dict[str, Callable] = {
    "ad":       ad_inventory_flow,
    "wsus":     wsus_inventory_flow,
    "glpi":     glpi_sync_flow,
    "trellix":  trellix_inventory_flow,
    # Add new tools here:
    # "netbox":   netbox_flow,
    # "fortimail": fortimail_flow,
}


def get_flow(tool: str) -> Callable:
    if tool not in FLOW_REGISTRY:
        raise KeyError(
            f"No flow registered for tool '{tool}'. "
            f"Available: {list(FLOW_REGISTRY.keys())}"
        )
    return FLOW_REGISTRY[tool]
