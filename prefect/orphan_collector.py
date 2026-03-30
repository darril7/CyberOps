"""
runner/orphan_collector.py
--------------------------
Captures tool records that had NO match in Netbox during a run.
These are "orphan" devices — known to the tool but not registered in Netbox.

An orphan record means one of:
  A) Device exists physically but was never added to Netbox
  B) Device was decommissioned in Netbox but still active in the tool
  C) Hostname/IP mismatch — device exists in Netbox under a different name

The report groups orphans by client → site → tool so the team can triage.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class OrphanRecord:
    """One device returned by a tool that had no Netbox match."""
    customer_id:    str
    customer_name:  str
    site_id:        str
    site_name:      str
    tool:           str
    record_key:     str             # the hostname key the script returned
    tried_keys:     list[str]       # match keys attempted (hostname, IP, MAC...)
    data:           dict[str, Any]  # raw record from script for context

    @property
    def ip(self) -> str | None:
        return (
            self.data.get("ip_address")
            or self.data.get("ip")
            or None
        )

    @property
    def mac(self) -> str | None:
        return self.data.get("mac_address") or None

    @property
    def hostname(self) -> str:
        return self.record_key


class OrphanCollector:
    """
    Accumulates orphan records across all tasks in a run.
    Thread-safe for concurrent Prefect tasks via append-only list.
    """

    def __init__(self):
        self._records: list[OrphanRecord] = []

    def add(
        self,
        customer_id:   str,
        customer_name: str,
        site_id:       str,
        site_name:     str,
        tool:          str,
        unmatched:     list[str],
        tool_records:  dict[str, dict],
        match_keys:    list[str],
    ):
        """
        Called by run_task after match() completes.
        unmatched = list of hostname keys that had no Netbox match.
        """
        for key in unmatched:
            self._records.append(OrphanRecord(
                customer_id=customer_id,
                customer_name=customer_name,
                site_id=site_id,
                site_name=site_name,
                tool=tool,
                record_key=key,
                tried_keys=match_keys,
                data=tool_records.get(key, {}),
            ))

    @property
    def records(self) -> list[OrphanRecord]:
        return list(self._records)

    @property
    def count(self) -> int:
        return len(self._records)

    def by_customer(self) -> dict[str, list[OrphanRecord]]:
        out: dict[str, list[OrphanRecord]] = {}
        for r in self._records:
            out.setdefault(r.customer_id, []).append(r)
        return out

    def by_tool(self) -> dict[str, list[OrphanRecord]]:
        out: dict[str, list[OrphanRecord]] = {}
        for r in self._records:
            out.setdefault(r.tool, []).append(r)
        return out

    def summary(self) -> dict:
        """Counts for logging."""
        by_c = self.by_customer()
        by_t = self.by_tool()
        return {
            "total":     self.count,
            "by_client": {k: len(v) for k, v in by_c.items()},
            "by_tool":   {k: len(v) for k, v in by_t.items()},
        }
