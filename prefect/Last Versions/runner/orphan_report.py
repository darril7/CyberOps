"""
runner/orphan_report.py
-----------------------
Generates the orphan device report from a completed run.

Two outputs:
  - HTML report  → human-readable, grouped by client → site → tool
  - CSV          → for ticketing, filtering, bulk review

An orphan = a device returned by a tool that had NO match in Netbox.
Triage categories:
  ADD    → Device is real, should be added to Netbox
  RENAME → Device exists in Netbox under a different name/IP
  STALE  → Device is decommissioned but still reporting to the tool
  REVIEW → Unknown — needs manual investigation
"""

from __future__ import annotations
import csv
import io
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runner.orphan_collector import OrphanCollector, OrphanRecord


# ── HTML Report ───────────────────────────────────────────────────────────────

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Orphan Device Report — {run_date}</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap');
  :root {{
    --bg:#f5f6f8;--surface:#fff;--border:#e3e7ee;--text:#1c2030;--muted:#68758f;
    --red-bg:#fff0f0;--red-bd:#f5c0c0;--red-tx:#8b1a1a;
    --amber-bg:#fff8e8;--amber-bd:#f0d080;--amber-tx:#7a5200;
    --blue-bg:#e8f2ff;--blue-bd:#b3d0f5;--blue-tx:#1a4b8a;
    --gray-bg:#f5f6f8;--gray-bd:#d0d5dd;--gray-tx:#4a5568;
  }}
  *{{box-sizing:border-box;margin:0;padding:0;}}
  body{{background:var(--bg);font-family:'IBM Plex Sans',sans-serif;font-size:14px;color:var(--text);padding:32px 24px 64px;}}
  h1{{font-size:22px;font-weight:600;letter-spacing:-.02em;margin-bottom:4px;}}
  .meta{{font-size:12px;color:var(--muted);font-family:'IBM Plex Mono',monospace;margin-bottom:28px;}}
  .summary-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px;margin-bottom:32px;}}
  .sc{{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:12px 16px;}}
  .sc .sl{{font-size:11px;color:var(--muted);margin-bottom:3px;}}
  .sc .sv{{font-size:22px;font-weight:500;}}
  .sc .sv.red{{color:#c0392b;}}
  .client-block{{margin-bottom:32px;}}
  .client-title{{font-size:17px;font-weight:600;margin-bottom:4px;}}
  .client-meta{{font-size:12px;color:var(--muted);font-family:'IBM Plex Mono',monospace;margin-bottom:16px;}}
  .site-block{{margin-bottom:20px;margin-left:16px;}}
  .site-title{{font-size:13px;font-weight:500;color:var(--muted);letter-spacing:.06em;text-transform:uppercase;margin-bottom:10px;border-bottom:1px solid var(--border);padding-bottom:6px;}}
  table{{width:100%;border-collapse:collapse;background:var(--surface);border:1px solid var(--border);border-radius:8px;overflow:hidden;margin-bottom:16px;}}
  th{{background:var(--bg);font-size:11px;font-weight:500;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);padding:8px 12px;text-align:left;border-bottom:1px solid var(--border);}}
  td{{padding:9px 12px;border-bottom:1px solid #f0f2f5;font-size:13px;vertical-align:top;}}
  tr:last-child td{{border-bottom:none;}}
  tr:hover td{{background:#fafbfd;}}
  .tag{{display:inline-block;font-size:10px;font-weight:500;padding:2px 7px;border-radius:4px;font-family:'IBM Plex Mono',monospace;}}
  .tag-tool{{background:var(--blue-bg);color:var(--blue-tx);border:1px solid var(--blue-bd);}}
  .mono{{font-family:'IBM Plex Mono',monospace;font-size:12px;}}
  .triage-add{{background:var(--blue-bg);color:var(--blue-tx);}}
  .triage-stale{{background:var(--amber-bg);color:var(--amber-tx);}}
  .triage-review{{background:var(--gray-bg);color:var(--gray-tx);}}
  select{{font-size:12px;padding:4px 8px;border:1px solid var(--border);border-radius:4px;background:var(--surface);color:var(--text);margin-left:8px;}}
  .filter-bar{{display:flex;align-items:center;gap:12px;margin-bottom:20px;font-size:13px;color:var(--muted);flex-wrap:wrap;}}
  .count-badge{{background:var(--red-bg);color:var(--red-tx);border:1px solid var(--red-bd);border-radius:12px;padding:1px 8px;font-size:11px;font-family:'IBM Plex Mono',monospace;margin-left:6px;}}
</style>
</head>
<body>
<h1>Orphan device report</h1>
<p class="meta">Generated: {run_date} &nbsp;·&nbsp; Run ID: {run_id} &nbsp;·&nbsp; Total orphans: {total}</p>

<div class="summary-grid">
  <div class="sc"><div class="sl">Total orphans</div><div class="sv red">{total}</div></div>
  {client_summary_cards}
  {tool_summary_cards}
</div>

<div class="filter-bar">
  Filter by triage:
  <select onchange="filterTriage(this.value)">
    <option value="">All</option>
    <option value="ADD">ADD — not in Netbox</option>
    <option value="STALE">STALE — decommissioned</option>
    <option value="REVIEW">REVIEW — unknown</option>
  </select>
</div>

{client_blocks}

<script>
function filterTriage(val) {{
  document.querySelectorAll('tr[data-triage]').forEach(tr => {{
    tr.style.display = (!val || tr.dataset.triage === val) ? '' : 'none';
  }});
}}
</script>
</body>
</html>
"""


def _triage(record: OrphanRecord) -> str:
    """
    Heuristic triage classification.
    The team will override these manually — this is a starting suggestion.
    """
    hostname = record.hostname.lower()
    # Likely stale: common decommission patterns
    stale_patterns = ["old", "decom", "retired", "archive", "bak", "backup", "test", "temp"]
    if any(p in hostname for p in stale_patterns):
        return "STALE"
    # Has an IP → probably a real device worth adding
    if record.ip:
        return "ADD"
    return "REVIEW"


def generate_html(
    collector: OrphanCollector,
    run_id: str = "manual",
) -> str:
    run_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    records = collector.records
    total = len(records)

    if total == 0:
        return f"""<!DOCTYPE html><html><body style="font-family:sans-serif;padding:40px">
            <h2>Orphan report — {run_date}</h2>
            <p style="color:green;margin-top:16px">&#10003; No orphan devices found. All tool records matched Netbox.</p>
            </body></html>"""

    # Summary cards
    by_client = collector.by_customer()
    by_tool   = collector.by_tool()

    client_cards = "\n  ".join(
        f'<div class="sc"><div class="sl">{cid}</div><div class="sv">{len(recs)}</div></div>'
        for cid, recs in sorted(by_client.items())
    )
    tool_cards = "\n  ".join(
        f'<div class="sc"><div class="sl">{tool}</div><div class="sv">{len(recs)}</div></div>'
        for tool, recs in sorted(by_tool.items())
    )

    # Client blocks
    client_html_parts = []
    for cid, crecs in sorted(by_client.items()):
        cname = crecs[0].customer_name
        # Group by site
        by_site: dict[str, list[OrphanRecord]] = {}
        for r in crecs:
            by_site.setdefault(r.site_id, []).append(r)

        site_html_parts = []
        for site_id, srecs in sorted(by_site.items()):
            # Group by tool within site
            by_tool_site: dict[str, list[OrphanRecord]] = {}
            for r in srecs:
                by_tool_site.setdefault(r.tool, []).append(r)

            rows = []
            for tool, trecs in sorted(by_tool_site.items()):
                for rec in sorted(trecs, key=lambda r: r.hostname):
                    triage = _triage(rec)
                    triage_cls = {
                        "ADD": "triage-add",
                        "STALE": "triage-stale",
                        "REVIEW": "triage-review",
                    }.get(triage, "triage-review")
                    rows.append(
                        f'<tr data-triage="{triage}">'
                        f'<td><span class="tag tag-tool">{tool}</span></td>'
                        f'<td class="mono">{rec.hostname}</td>'
                        f'<td class="mono">{rec.ip or "—"}</td>'
                        f'<td class="mono">{rec.mac or "—"}</td>'
                        f'<td><span class="tag {triage_cls}">{triage}</span></td>'
                        f'<td class="mono" style="font-size:11px;color:#8b949e">'
                        f'{", ".join(rec.tried_keys)}</td>'
                        f'</tr>'
                    )

            site_html = f"""
            <div class="site-block">
              <div class="site-title">{site_id} <span class="count-badge">{len(srecs)}</span></div>
              <table>
                <tr>
                  <th>Tool</th><th>Hostname / Key</th><th>IP address</th>
                  <th>MAC address</th><th>Triage</th><th>Tried match keys</th>
                </tr>
                {"".join(rows)}
              </table>
            </div>"""
            site_html_parts.append(site_html)

        client_html_parts.append(f"""
        <div class="client-block">
          <div class="client-title">{cname} <span class="count-badge">{len(crecs)}</span></div>
          <div class="client-meta">{cid} &nbsp;·&nbsp; {len(by_site)} site(s)</div>
          {"".join(site_html_parts)}
        </div>""")

    return HTML_TEMPLATE.format(
        run_date=run_date,
        run_id=run_id,
        total=total,
        client_summary_cards=client_cards,
        tool_summary_cards=tool_cards,
        client_blocks="\n".join(client_html_parts),
    )


def generate_csv(collector: OrphanCollector) -> str:
    """Returns CSV string — write to file or attach to ticket."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "customer_id", "customer_name", "site_id", "tool",
        "hostname", "ip_address", "mac_address",
        "triage", "tried_match_keys",
    ])
    for rec in sorted(
        collector.records,
        key=lambda r: (r.customer_id, r.site_id, r.tool, r.hostname)
    ):
        writer.writerow([
            rec.customer_id,
            rec.customer_name,
            rec.site_id,
            rec.tool,
            rec.hostname,
            rec.ip or "",
            rec.mac or "",
            _triage(rec),
            " → ".join(rec.tried_keys),
        ])
    return output.getvalue()


def save_reports(
    collector: OrphanCollector,
    output_dir: str | Path = "reports",
    run_id: str = "manual",
) -> tuple[Path, Path]:
    """Save HTML and CSV reports. Returns (html_path, csv_path)."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    html_path = out / f"orphan_report_{date_str}.html"
    csv_path  = out / f"orphan_report_{date_str}.csv"

    html_path.write_text(generate_html(collector, run_id), encoding="utf-8")
    csv_path.write_text(generate_csv(collector), encoding="utf-8")

    return html_path, csv_path
