"""
playbook_runner.py
------------------
Master Prefect flow that:
  1. Loads the playbook
  2. Optionally filters by customer / site / tool
  3. Resolves credentials from Prefect blocks
  4. Dispatches each task to its registered flow

Run manually:
    python playbook_runner.py
    python playbook_runner.py --customer customer_a
    python playbook_runner.py --customer customer_a --site dc
    python playbook_runner.py --tool wsus

Or via Prefect deployment with parameters.
"""

from __future__ import annotations
import asyncio
import argparse
from typing import Optional

from prefect import flow, task, get_run_logger

from playbook_loader import PlaybookLoader, ResolvedTask
from flow_registry import get_flow, load_block_credentials


PLAYBOOK_PATH = "playbook.yaml"


# ── Per-task dispatcher ───────────────────────────────────────────────────────

@task(name="dispatch-task", retries=1, retry_delay_seconds=30)
async def dispatch_task(resolved: ResolvedTask):
    """Load credentials and run the appropriate tool flow for one resolved task."""
    logger = get_run_logger()
    logger.info(
        f"Dispatching: {resolved.task_id} "
        f"(block={resolved.block_name})"
    )

    credentials = await load_block_credentials(resolved.block_name)
    flow_fn = get_flow(resolved.tool)

    await flow_fn(
        credentials=credentials,
        params=resolved.params,
        task_id=resolved.task_id,
    )


# ── Master flow ───────────────────────────────────────────────────────────────

@flow(
    name="soc-inventory-playbook-runner",
    description="Runs inventory scripts for all customers/sites defined in the playbook.",
)
async def run_playbook(
    playbook_path: str = PLAYBOOK_PATH,
    customer: Optional[str] = None,     # filter by customer_id
    site: Optional[str] = None,         # filter by site_id
    tool: Optional[str] = None,         # filter by tool name
    dry_run: bool = False,              # print tasks without executing
):
    logger = get_run_logger()
    loader = PlaybookLoader(playbook_path)
    tasks = loader.load()

    # ── Apply filters ──────────────────────────────────────────────────────
    if customer:
        tasks = [t for t in tasks if t.customer_id == customer]
    if site:
        tasks = [t for t in tasks if t.site_id == site]
    if tool:
        tasks = [t for t in tasks if t.tool == tool]

    if not tasks:
        logger.warning("No tasks matched the given filters. Check playbook and filters.")
        return

    logger.info(f"Tasks to run ({len(tasks)}):")
    for t in tasks:
        logger.info(f"  → {t.task_id}  block={t.block_name}  params={t.params}")

    if dry_run:
        logger.info("DRY RUN — no tasks executed.")
        return

    # ── Dispatch ───────────────────────────────────────────────────────────
    # Tasks within a site run sequentially; sites and customers run concurrently.
    # Adjust concurrency via Prefect task runner settings if needed.
    dispatch_futures = [dispatch_task.submit(t) for t in tasks]

    results = []
    for future in dispatch_futures:
        try:
            results.append(await future.result())
        except Exception as exc:
            logger.error(f"Task failed: {exc}")

    logger.info(f"Run complete. {len(results)}/{len(tasks)} tasks succeeded.")


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SOC Playbook Runner")
    parser.add_argument("--playbook", default=PLAYBOOK_PATH)
    parser.add_argument("--customer", default=None)
    parser.add_argument("--site", default=None)
    parser.add_argument("--tool", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    asyncio.run(
        run_playbook(
            playbook_path=args.playbook,
            customer=args.customer,
            site=args.site,
            tool=args.tool,
            dry_run=args.dry_run,
        )
    )
