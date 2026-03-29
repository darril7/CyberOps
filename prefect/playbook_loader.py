"""
playbook_loader.py
------------------
Loads playbook.yaml and resolves resource_ref aliases.
Produces a flat list of ResolvedTask objects ready for the runner.
"""

from __future__ import annotations
import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ── Data model ───────────────────────────────────────────────────────────────

@dataclass
class ResolvedTask:
    """A single executable unit: one tool, one site, one customer."""
    customer_id: str
    customer_name: str
    site_id: str
    site_name: str
    tool: str
    block_name: str           # Prefect block to load credentials from
    params: dict[str, Any]    # Merged params (resource-level + site-level override)

    @property
    def task_id(self) -> str:
        return f"{self.customer_id}/{self.site_id}/{self.tool}"


# ── Loader ───────────────────────────────────────────────────────────────────

class PlaybookLoader:
    def __init__(self, playbook_path: str | Path):
        self.path = Path(playbook_path)
        self._raw: dict = {}

    def load(self) -> list[ResolvedTask]:
        with open(self.path) as f:
            self._raw = yaml.safe_load(f)

        tasks: list[ResolvedTask] = []
        customers = self._raw.get("customers", {})

        for customer_id, customer_cfg in customers.items():
            if not customer_cfg.get("enabled", True):
                continue

            customer_name = customer_cfg.get("display_name", customer_id)
            resources = customer_cfg.get("resources", {})
            sites = customer_cfg.get("sites", {})

            for site_id, site_cfg in sites.items():
                if not site_cfg.get("enabled", True):
                    continue

                site_name = site_cfg.get("display_name", site_id)
                inventory_sources = site_cfg.get("inventory_sources", {})

                for tool_key, tool_cfg in inventory_sources.items():
                    task = self._resolve_task(
                        customer_id, customer_name,
                        site_id, site_name,
                        tool_key, tool_cfg,
                        resources
                    )
                    if task:
                        tasks.append(task)

        return tasks

    def _resolve_task(
        self,
        customer_id: str,
        customer_name: str,
        site_id: str,
        site_name: str,
        tool_key: str,
        tool_cfg: dict,
        resources: dict,
    ) -> ResolvedTask | None:
        """
        Resolve a tool config entry.
        - If it has a resource_ref, start from the named resource
          then overlay any site-level params on top.
        - If it has its own tool/block/params, use those directly.
        """
        site_params = tool_cfg.get("params", {})

        if "resource_ref" in tool_cfg:
            ref = tool_cfg["resource_ref"]
            if ref not in resources:
                raise ValueError(
                    f"[{customer_id}/{site_id}/{tool_key}] "
                    f"resource_ref '{ref}' not found in customer resources."
                )
            base = resources[ref]
            tool = base.get("tool", tool_key)
            block_name = base["block"]
            # Merge: resource-level params first, site-level params override
            params = {**base.get("params", {}), **site_params}

        elif "tool" in tool_cfg or "block" in tool_cfg:
            tool = tool_cfg.get("tool", tool_key)
            block_name = tool_cfg["block"]
            params = site_params

        else:
            raise ValueError(
                f"[{customer_id}/{site_id}/{tool_key}] "
                f"Entry must have either 'resource_ref' or 'tool'+'block'."
            )

        return ResolvedTask(
            customer_id=customer_id,
            customer_name=customer_name,
            site_id=site_id,
            site_name=site_name,
            tool=tool,
            block_name=block_name,
            params=params,
        )

    def load_for_customer(self, customer_id: str) -> list[ResolvedTask]:
        return [t for t in self.load() if t.customer_id == customer_id]

    def load_for_tool(self, tool: str) -> list[ResolvedTask]:
        return [t for t in self.load() if t.tool == tool]


# ── CLI convenience ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "playbook.yaml"
    loader = PlaybookLoader(path)
    tasks = loader.load()
    print(f"\nResolved {len(tasks)} task(s):\n")
    for t in tasks:
        print(f"  {t.task_id}")
        print(f"    tool       : {t.tool}")
        print(f"    block      : {t.block_name}")
        print(f"    params     : {t.params}")
        print()
