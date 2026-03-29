"""
flows/soc_inventory_flow.py
----------------------------
Master Prefect flow. Entry point for all scheduled and manual runs.

Run manually:
    prefect run flow -p flows/soc_inventory_flow.py:soc_inventory

With filters:
    from flows.soc_inventory_flow import soc_inventory
    await soc_inventory(customer="reduno", site="datacenter", tool="wsus")
"""

from __future__ import annotations
from typing import Optional

from prefect import flow, get_run_logger
from prefect.blocks.system import Secret

from playbook_loader import PlaybookLoader
from runner.run_task import run_inventory_task


@flow(
    name="soc-inventory",
    description="Daily SOC inventory — updates Netbox custom fields from all tools",
    version="1.0",
)
async def soc_inventory(
    playbook_path: str = "config/playbook.yaml",
    field_map_path: str = "config/field_map.yaml",
    customer: Optional[str] = None,     # filter: run only this customer
    site: Optional[str] = None,         # filter: run only this site
    tool: Optional[str] = None,         # filter: run only this tool
    dry_run: bool = False,
):
    logger = get_run_logger()

    # ── Load Netbox credentials from Prefect block ────────────────────────────
    # Store as a single Secret block named "netbox/api"
    # with value: {"url": "https://netbox.example.com", "token": "..."}
    nb_block = await Secret.load("netbox/api")
    nb_creds = nb_block.get()
    netbox_url   = nb_creds["url"]
    netbox_token = nb_creds["token"]

    # ── Load and filter playbook ──────────────────────────────────────────────
    loader = PlaybookLoader(playbook_path)
    tasks = loader.load()

    if customer:
        tasks = [t for t in tasks if t.customer_id == customer]
    if site:
        tasks = [t for t in tasks if t.site_id == site]
    if tool:
        tasks = [t for t in tasks if t.tool == tool]

    if not tasks:
        logger.warning("No tasks matched filters — check playbook and filter values")
        return

    logger.info(
        f"Running {len(tasks)} task(s)"
        + (f" [DRY RUN]" if dry_run else "")
    )
    for t in tasks:
        logger.info(f"  → {t.task_id}  block={t.block_name}")

    # ── Submit all tasks concurrently ─────────────────────────────────────────
    # Prefect will respect the task-runner concurrency settings.
    # For large deployments, set a ConcurrentTaskRunner limit.
    futures = [
        run_inventory_task.submit(
            resolved=t,
            netbox_url=netbox_url,
            netbox_token=netbox_token,
            field_map_path=field_map_path,
            dry_run=dry_run,
        )
        for t in tasks
    ]

    # ── Collect results ───────────────────────────────────────────────────────
    results = []
    failed  = []
    for future in futures:
        try:
            result = await future.result()
            results.append(result)
        except Exception as e:
            failed.append(str(e))
            logger.error(f"Task failed: {e}")

    # ── Summary ───────────────────────────────────────────────────────────────
    total_patched = sum(r.get("patched", 0) for r in results)
    total_matched = sum(r.get("matched", 0) for r in results)
    total_records = sum(r.get("tool_records", 0) for r in results)

    logger.info(
        f"Run complete — "
        f"tasks={len(tasks)} "
        f"succeeded={len(results)} "
        f"failed={len(failed)} | "
        f"tool_records={total_records} "
        f"matched={total_matched} "
        f"patched={total_patched}"
    )

    return {
        "tasks":        len(tasks),
        "succeeded":    len(results),
        "failed":       len(failed),
        "tool_records": total_records,
        "matched":      total_matched,
        "patched":      total_patched,
        "results":      results,
    }
