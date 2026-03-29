"""
runner/run_task.py
------------------
Executes one resolved task end-to-end:

  1. Load field map for this tool
  2. Query Netbox: devices + VMs for this tenant/site
  3. Filter by applicability (device_role + OS conditions)
  4. Call the tool's fetch() function
  5. Match tool output to Netbox objects
  6. PATCH custom fields

This is what Prefect calls — one task = one tool × one site × one client.
"""

from __future__ import annotations
import logging
from typing import Any

from prefect import task, get_run_logger
from prefect.blocks.system import Secret

from runner.netbox_client import NetboxClient, NetboxDevice
from runner.field_map_loader import (
    FieldMapLoader,
    ToolMap,
    is_applicable,
    get_match_keys,
    get_fields,
)
from playbook_loader import ResolvedTask

log = logging.getLogger(__name__)

# Lazy import registry — avoids loading all tool dependencies upfront
SCRIPT_REGISTRY: dict[str, str] = {
    "ad":         "scripts.ad_inventory",
    "trellix":    "scripts.trellix_inventory",
    "teramind":   "scripts.teramind_inventory",
    "mdm":        "scripts.mdm_inventory",
    "wsus":       "scripts.wsus_inventory",
    "wazuh":      "scripts.wazuh_inventory",
    "elastic":    "scripts.elastic_inventory",
    "nessus":     "scripts.nessus_inventory",
    "fortigate":  "scripts.fortigate_inventory",
    "vcenter":    "scripts.vcenter_inventory",
    "glpi":       "scripts.glpi_inventory",
}


@task(
    name="run-inventory-task",
    retries=2,
    retry_delay_seconds=60,
    tags=["inventory"],
)
async def run_inventory_task(
    resolved: ResolvedTask,
    netbox_url: str,
    netbox_token: str,
    field_map_path: str = "config/field_map.yaml",
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    One Prefect task = one tool × one site × one client.

    Args:
        resolved:       ResolvedTask from playbook_loader
        netbox_url:     Netbox base URL
        netbox_token:   Netbox API token
        field_map_path: Path to field_map.yaml
        dry_run:        If True, log what would be patched without writing

    Returns:
        Summary dict with counts for logging/reporting.
    """
    logger = get_run_logger()
    logger.info(
        f"[{resolved.task_id}] starting — "
        f"tool={resolved.tool} site={resolved.site_id} "
        f"customer={resolved.customer_id}"
    )

    # ── 1. Load field map for this tool ───────────────────────────────────────
    loader = FieldMapLoader(field_map_path)
    tool_map: ToolMap = loader.get(resolved.tool)

    # ── 2. Load tool credentials from Prefect block ───────────────────────────
    logger.info(f"[{resolved.task_id}] loading credentials: {resolved.block_name}")
    block = await Secret.load(resolved.block_name)
    credentials = block.get()

    # ── 3. Fetch tool data ────────────────────────────────────────────────────
    logger.info(f"[{resolved.task_id}] fetching from {resolved.tool}")
    tool_data: dict[str, dict] = await _call_script(
        resolved.tool, credentials, resolved.params
    )
    logger.info(
        f"[{resolved.task_id}] tool returned {len(tool_data)} records"
    )

    if not tool_data:
        logger.warning(f"[{resolved.task_id}] no data returned from tool — skipping")
        return {"task_id": resolved.task_id, "tool_records": 0, "patched": 0}

    # ── 4. Query Netbox ───────────────────────────────────────────────────────
    nb = NetboxClient(
        base_url=netbox_url,
        token=netbox_token,
        verify_ssl=True,
    )

    netbox_objects: list[NetboxDevice] = []

    # Physical devices — query only roles this tool covers
    if tool_map.applicable_device_roles:
        devices = await nb.get_devices(
            tenant_slug=resolved.customer_id,
            site_slug=resolved.site_id,
            device_roles=tool_map.applicable_device_roles,
        )
        netbox_objects.extend(devices)

    # Virtual machines — always all roles (filtered by applicability below)
    if tool_map.applicable_vms:
        vms = await nb.get_vms(
            tenant_slug=resolved.customer_id,
            site_slug=resolved.site_id,
        )
        netbox_objects.extend(vms)

    logger.info(
        f"[{resolved.task_id}] Netbox returned "
        f"{len(netbox_objects)} objects (devices + VMs)"
    )

    # ── 5. Filter by applicability ─────────────────────────────────────────────
    applicable: list[NetboxDevice] = []
    for obj in netbox_objects:
        ok, reason = is_applicable(obj, tool_map)
        if ok:
            applicable.append(obj)
        else:
            log.debug(
                f"[{resolved.task_id}] skipping {obj.name} "
                f"({obj.object_type}/{obj.device_role}): {reason}"
            )

    logger.info(
        f"[{resolved.task_id}] {len(applicable)}/{len(netbox_objects)} "
        f"objects pass applicability"
    )

    if not applicable:
        logger.warning(
            f"[{resolved.task_id}] no applicable Netbox objects — "
            f"check device_role and OS conditions"
        )
        return {
            "task_id": resolved.task_id,
            "tool_records": len(tool_data),
            "netbox_objects": len(netbox_objects),
            "applicable": 0,
            "patched": 0,
        }

    # ── 6. Match tool records to Netbox objects ───────────────────────────────
    # Use the first applicable device to determine match key strategy
    # (devices and VMs may use different match keys per field_map)
    sample = applicable[0]
    match_keys = get_match_keys(sample, tool_map)

    match_results = nb.match(
        tool_records=tool_data,
        netbox_objects=applicable,
        match_keys=match_keys,
    )

    # ── 7. Patch Netbox ───────────────────────────────────────────────────────
    # Group match results by object_type to use correct field mapping
    device_results = [mr for mr in match_results if mr.device.object_type == "device"]
    vm_results     = [mr for mr in match_results if mr.device.object_type == "vm"]

    summary = {"patched": 0, "skipped": 0, "failed": 0}

    if device_results:
        device_fields = get_fields(device_results[0].device, tool_map)
        s = await nb.patch_custom_fields(device_results, device_fields, dry_run)
        _merge_summary(summary, s)

    if vm_results:
        vm_fields = get_fields(vm_results[0].device, tool_map)
        s = await nb.patch_custom_fields(vm_results, vm_fields, dry_run)
        _merge_summary(summary, s)

    logger.info(
        f"[{resolved.task_id}] complete — "
        f"matched={len(match_results)} "
        f"patched={summary['patched']} "
        f"skipped={summary['skipped']} "
        f"failed={summary['failed']}"
    )

    return {
        "task_id":        resolved.task_id,
        "customer":       resolved.customer_id,
        "site":           resolved.site_id,
        "tool":           resolved.tool,
        "tool_records":   len(tool_data),
        "netbox_objects": len(netbox_objects),
        "applicable":     len(applicable),
        "matched":        len(match_results),
        **summary,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _call_script(
    tool: str,
    credentials: dict,
    params: dict,
) -> dict[str, dict]:
    """Dynamically import and call the tool's fetch() function."""
    import importlib
    module_path = SCRIPT_REGISTRY.get(tool)
    if not module_path:
        raise KeyError(
            f"No script registered for tool '{tool}'. "
            f"Add it to SCRIPT_REGISTRY in run_task.py"
        )
    module = importlib.import_module(module_path)
    return await module.fetch(credentials=credentials, **params)


def _merge_summary(target: dict, source: dict):
    for k in ("patched", "skipped", "failed"):
        target[k] = target.get(k, 0) + source.get(k, 0)
