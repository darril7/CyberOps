"""
netbox_schema_pull.py
=====================
Connects to NetBox, reads mapping.yaml, and generates:

  device_onboarding_form.html  — complete self-contained form, open in any browser
  netbox_schema.yaml           — schema snapshot used by netbox_import.py

Usage:
    python netbox_schema_pull.py

Config via environment variables:
    export NETBOX_URL=http://your-netbox-instance
    export NETBOX_TOKEN=your-api-token

To add a new device role, product, or appliance type:
    1. Add the choice to the relevant choice set in NetBox
    2. Add the mapping entry in mapping.yaml
    3. Re-run this script — new HTML is generated automatically
"""

import os
import sys
import json
import yaml
import requests
from datetime import datetime, timezone

# ── Config ─────────────────────────────────────────────────────────
NETBOX_URL   = os.getenv("NETBOX_URL",   "http://your-netbox-instance")
NETBOX_TOKEN = os.getenv("NETBOX_TOKEN", "your-api-token")

OUTPUT_HTML  = "device_onboarding_form.html"
OUTPUT_YAML  = "netbox_schema.yaml"
MAPPING_FILE = "mapping.yaml"

SOC_PREFIX        = "SOC_"
TARGET_OBJECT_TYPE = "dcim.device"

HEADERS = {
    "Authorization": f"Token {NETBOX_TOKEN}",
    "Content-Type":  "application/json",
    "Accept":        "application/json",
}

# ══════════════════════════════════════════════════════════════════
# NetBox pull functions
# ══════════════════════════════════════════════════════════════════

def nb_get(endpoint):
    url = f"{NETBOX_URL.rstrip('/')}/api/{endpoint}"
    r = requests.get(url, headers=HEADERS, verify=False, timeout=15)
    r.raise_for_status()
    return r.json()


def nb_paginate(endpoint):
    results = []
    url = f"{NETBOX_URL.rstrip('/')}/api/{endpoint}"
    while url:
        r = requests.get(url, headers=HEADERS, verify=False, timeout=15)
        r.raise_for_status()
        data = r.json()
        results.extend(data.get("results", []))
        url = data.get("next")
    return results


def pull_choice_sets():
    print("  Pulling choice sets...", end=" ", flush=True)
    raw = nb_paginate("extras/custom-field-choice-sets/?limit=200")
    sets = {}
    for cs in raw:
        name = cs.get("name", "")
        if not name.startswith(SOC_PREFIX):
            continue
        choices = []
        for ch in cs.get("extra_choices", []):
            if isinstance(ch, list) and len(ch) == 2:
                choices.append({"value": ch[0], "label": ch[1]})
            elif isinstance(ch, dict):
                choices.append({"value": ch.get("value",""), "label": ch.get("label","")})
        sets[name] = {"name": name, "description": cs.get("description",""), "choices": choices}
    print(f"{len(sets)} SOC_* sets found")
    return sets


def pull_custom_fields(choice_sets):
    print("  Pulling custom fields...", end=" ", flush=True)
    raw = nb_paginate(f"extras/custom-fields/?object_type={TARGET_OBJECT_TYPE}&limit=200")
    fields = []
    for cf in raw:
        cs_obj  = cf.get("choice_set") or {}
        cs_name = cs_obj.get("name","") if isinstance(cs_obj, dict) else ""
        fields.append({
            "name":        cf.get("name",""),
            "label":       cf.get("label", cf.get("name","")),
            "type":        (cf.get("type") or {}).get("value","text"),
            "required":    cf.get("required", False),
            "description": cf.get("description",""),
            "group":       cf.get("group_name",""),
            "choice_set":  cs_name,
            "choices":     choice_sets.get(cs_name, {}).get("choices", []),
        })
    fields.sort(key=lambda f: (f["group"], f["name"]))
    print(f"{len(fields)} fields found")
    return fields


def pull_sites():
    print("  Pulling sites...", end=" ", flush=True)
    raw = nb_paginate("dcim/sites/?limit=200")
    sites = [{"slug": s["slug"], "name": s["name"]} for s in raw]
    print(f"{len(sites)} sites found")
    return sites


def build_schema(choice_sets, custom_fields, sites):
    return {
        "meta": {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "netbox_url":   NETBOX_URL,
            "object_type":  TARGET_OBJECT_TYPE,
        },
        "choice_sets":   choice_sets,
        "custom_fields": custom_fields,
        "sites":         sites,
    }


# ══════════════════════════════════════════════════════════════════
# MAPPING — loaded from mapping.yaml
# Edit mapping.yaml to add new roles, products, or appliance types.
# Never edit this section directly.
# ══════════════════════════════════════════════════════════════════

def load_mapping() -> dict:
    if not os.path.exists(MAPPING_FILE):
        print(f"[ERROR] {MAPPING_FILE} not found.")
        print(f"        This file defines how form answers map to NetBox fields.")
        print(f"        It must exist in the same directory as this script.")
        sys.exit(1)
    with open(MAPPING_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ══════════════════════════════════════════════════════════════════
# HTML generator
# ══════════════════════════════════════════════════════════════════

def build_html(schema: dict, mapping: dict) -> str:
    schema_json  = json.dumps(schema,  indent=2, ensure_ascii=False)
    mapping_json = json.dumps(mapping, indent=2, ensure_ascii=False)
    generated_at = schema["meta"]["generated_at"]
    netbox_url   = schema["meta"]["netbox_url"]

    # Build site options from live NetBox data
    site_options = "\n".join(
        f'          <option value="{s["slug"]}">{s["name"]}</option>'
        for s in schema.get("sites", [])
    )
    if not site_options:
        site_options = '<option value="">No sites found — add sites in NetBox first</option>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SOC Device Onboarding Form</title>
<!-- Generated by netbox_schema_pull.py on {generated_at} from {netbox_url} -->
<!-- DO NOT EDIT MANUALLY — re-run netbox_schema_pull.py to regenerate -->
<script src="https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js"></script>
<script>
// ── NetBox Schema (live pull from {netbox_url}) ──
const NETBOX_SCHEMA = {schema_json};

// ── Mapping logic ──
const MAPPING = {mapping_json};
</script>
<style>
:root {{
  --blue:#1F4E79;--med-blue:#2E75B6;--lt-blue:#D6E4F0;
  --green:#375623;--lt-green:#EBF1DE;
  --orange:#C55A11;--lt-orange:#FFF2CC;
  --gray:#F4F7FB;--border:#CCCCCC;--dark:#1A1A1A;
}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:Arial,sans-serif;background:var(--gray);color:var(--dark);font-size:14px}}
header{{background:var(--blue);color:white;padding:16px 28px;display:flex;align-items:center;justify-content:space-between}}
header h1{{font-size:17px;font-weight:bold}}
header span{{font-size:11px;opacity:.6}}
.schema-badge{{font-size:10px;background:rgba(255,255,255,.18);padding:3px 10px;border-radius:10px;margin-left:12px}}
.container{{max-width:920px;margin:22px auto;padding:0 14px 80px}}
.section{{background:white;border-radius:8px;margin-bottom:18px;overflow:hidden;border:1px solid var(--border)}}
.section-header{{background:var(--med-blue);color:white;padding:9px 18px;font-weight:bold;font-size:12px;letter-spacing:.5px;text-transform:uppercase}}
.section-header.green{{background:var(--green)}}.section-header.orange{{background:var(--orange)}}
.section-body{{padding:18px}}
.field-row{{display:grid;grid-template-columns:210px 1fr;align-items:start;gap:10px;margin-bottom:13px}}
.field-row:last-child{{margin-bottom:0}}
label{{font-weight:bold;font-size:12px;padding-top:7px;color:var(--blue)}}
label .req{{color:var(--orange);margin-left:2px}}
label .hint{{display:block;font-weight:normal;font-size:10px;color:#888;margin-top:2px;line-height:1.4}}
input[type=text],select,textarea{{width:100%;padding:7px 10px;border:1px solid var(--border);border-radius:4px;font-family:Arial,sans-serif;font-size:13px;color:var(--dark);background:white;transition:border-color .2s}}
input[type=text]:focus,select:focus,textarea:focus{{outline:none;border-color:var(--med-blue);box-shadow:0 0 0 3px rgba(46,117,182,.13)}}
textarea{{resize:vertical;min-height:65px}}
.radio-group{{display:flex;flex-wrap:wrap;gap:7px;padding-top:3px}}
.radio-group label{{display:flex;align-items:center;gap:5px;font-weight:normal;color:var(--dark);cursor:pointer;padding:5px 12px;border:1px solid var(--border);border-radius:18px;font-size:13px;transition:all .15s;padding-top:5px}}
.radio-group input{{display:none}}
.radio-group label:has(input:checked){{background:var(--med-blue);color:white;border-color:var(--med-blue)}}
.conditional{{display:none}}.conditional.visible{{display:block}}
.note{{background:var(--lt-orange);border-left:4px solid var(--orange);padding:9px 13px;border-radius:0 4px 4px 0;font-size:12px;color:var(--dark);margin-bottom:14px}}
.role-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(195px,1fr));gap:7px;padding-top:3px}}
.role-card{{border:2px solid var(--border);border-radius:6px;padding:9px 11px;cursor:pointer;transition:all .15s;display:flex;align-items:center;gap:7px;font-size:13px}}
.role-card:hover{{border-color:var(--med-blue);background:var(--lt-blue)}}
.role-card.selected{{border-color:var(--med-blue);background:var(--med-blue);color:white}}
.role-card input{{display:none}}
.role-code{{font-size:10px;font-weight:bold;opacity:.7;white-space:nowrap}}
.action-bar{{position:sticky;bottom:0;background:white;border-top:1px solid var(--border);padding:13px 28px;display:flex;align-items:center;gap:11px;box-shadow:0 -4px 12px rgba(0,0,0,.08)}}
.btn{{padding:9px 22px;border:none;border-radius:6px;cursor:pointer;font-family:Arial,sans-serif;font-size:13px;font-weight:bold;transition:all .15s}}
.btn-primary{{background:var(--med-blue);color:white}}.btn-primary:hover{{background:var(--blue)}}
.btn-success{{background:var(--green);color:white}}.btn-success:hover{{background:#2a4119}}
.btn-outline{{background:white;border:2px solid var(--border);color:var(--dark)}}.btn-outline:hover{{border-color:var(--med-blue)}}
.queue-section{{background:white;border-radius:8px;border:1px solid var(--border);margin-bottom:18px;overflow:hidden}}
.queue-header{{background:var(--blue);color:white;padding:9px 18px;font-weight:bold;font-size:12px;display:flex;align-items:center;justify-content:space-between}}
.queue-count{{background:var(--orange);padding:2px 10px;border-radius:18px;font-size:11px}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th{{background:var(--lt-blue);color:var(--blue);padding:7px 9px;text-align:left;font-size:10px;text-transform:uppercase;letter-spacing:.4px}}
td{{padding:6px 9px;border-bottom:1px solid #eee}}
tr:last-child td{{border-bottom:none}}
tr:hover td{{background:#fafafa}}
.badge{{display:inline-block;padding:1px 7px;border-radius:9px;font-size:10px;font-weight:bold}}
.badge-server{{background:var(--lt-blue);color:var(--blue)}}
.badge-workstation{{background:var(--lt-green);color:var(--green)}}
.badge-appliance{{background:var(--lt-orange);color:var(--orange)}}
.badge-review{{background:#eee;color:#666}}
.remove-btn{{background:none;border:none;cursor:pointer;color:#c0392b;font-size:14px}}
.empty-queue{{text-align:center;padding:28px;color:#aaa;font-size:12px}}
</style>
</head>
<body>

<header>
  <h1>SOC Program — Device Onboarding Form
    <span class="schema-badge">schema: {generated_at[:10]} &nbsp;|&nbsp; {netbox_url}</span>
  </h1>
  <span>Inventory &amp; Security Baseline Intake</span>
</header>

<div class="container">

  <div class="queue-section">
    <div class="queue-header">
      <span>Device Queue — Pending Export</span>
      <span class="queue-count" id="queue-count">0 devices</span>
    </div>
    <div id="queue-body">
      <div class="empty-queue">No devices added yet. Fill the form below and click "Add to Queue".</div>
    </div>
  </div>

  <!-- SECTION 1 — BASIC -->
  <div class="section">
    <div class="section-header">Section 1 — Basic Device Information</div>
    <div class="section-body">
      <div class="field-row">
        <label>Hostname <span class="req">*</span></label>
        <input type="text" id="hostname" placeholder="e.g. SRV-DC-01, WRK-JSMITH-01">
      </div>
      <div class="field-row">
        <label>IP Address</label>
        <input type="text" id="ip_address" placeholder="e.g. 192.168.1.10">
      </div>
      <div class="field-row">
        <label>Asset Type <span class="req">*</span></label>
        <div class="radio-group">
          <label><input type="radio" name="asset_type" value="Server"      onchange="onAssetTypeChange()"> Server</label>
          <label><input type="radio" name="asset_type" value="Workstation" onchange="onAssetTypeChange()"> Workstation</label>
          <label><input type="radio" name="asset_type" value="Appliance"   onchange="onAssetTypeChange()"> Appliance / Network Device</label>
        </div>
      </div>
      <div class="field-row">
        <label>Operating System</label>
        <select id="os_select">
          <option value="">— Select OS —</option>
          <optgroup label="Windows Server">
            <option>Windows Server 2022</option>
            <option>Windows Server 2019</option>
            <option>Windows Server 2016</option>
          </optgroup>
          <optgroup label="Windows Workstation">
            <option>Windows 11</option>
            <option>Windows 10</option>
          </optgroup>
          <optgroup label="Linux">
            <option>Ubuntu 22.04</option>
            <option>Ubuntu 20.04</option>
            <option>RHEL 9</option>
            <option>RHEL 8</option>
            <option>Debian 12</option>
            <option>Debian 11</option>
            <option>Amazon Linux 2</option>
          </optgroup>
          <option>N/A (Appliance)</option>
          <option>Other</option>
        </select>
      </div>
      <div class="field-row">
        <label>Environment</label>
        <div class="radio-group">
          <label><input type="radio" name="environment" value="Production"> Production</label>
          <label><input type="radio" name="environment" value="Development"> Development</label>
          <label><input type="radio" name="environment" value="Test"> Test</label>
          <label><input type="radio" name="environment" value="DMZ"> DMZ</label>
        </div>
      </div>
      <div class="field-row">
        <label>Handles Sensitive Data?</label>
        <div class="radio-group">
          <label><input type="radio" name="sensitive_data" value="Yes"> Yes</label>
          <label><input type="radio" name="sensitive_data" value="No"> No</label>
        </div>
      </div>
      <div class="field-row">
        <label>Site <span class="req">*</span></label>
        <select id="site_select">
          <option value="">— Select Site —</option>
          {site_options}
        </select>
      </div>
      <div class="field-row">
        <label>Department / Owner
          <span class="hint">For workstations</span>
        </label>
        <input type="text" id="department" placeholder="e.g. Finance, IT, HR">
      </div>
    </div>
  </div>

  <!-- SECTION 2 — SERVER -->
  <div class="section conditional" id="section-server">
    <div class="section-header">Section 2 — Server Role</div>
    <div class="section-body">
      <div class="note">This section drives all hardening and log collection decisions. A wrong role means the wrong CIS benchmark and Sigma rules get applied.</div>
      <div class="field-row" style="grid-template-columns:1fr">
        <label style="padding-top:0;margin-bottom:8px">Q1 — Primary server role <span class="req">*</span></label>
        <div class="role-grid" id="role-grid"></div>
      </div>
      <div class="conditional" id="section-product" style="margin-top:14px">
        <div class="field-row">
          <label>Q2 — Specific product
            <span class="hint">Web: Apache / Nginx / IIS<br>DB: MySQL / MSSQL / PostgreSQL / Oracle<br>Mail: Exchange / Postfix<br>App: free text below</span>
          </label>
          <select id="specific_product_select">
            <option value="">— Select if listed —</option>
            <optgroup label="Web Server">
              <option>Apache</option><option>Nginx</option><option>IIS</option>
            </optgroup>
            <optgroup label="Database">
              <option>MySQL</option><option>MSSQL</option><option>PostgreSQL</option><option>Oracle</option>
            </optgroup>
            <optgroup label="Mail">
              <option>Exchange</option><option>Postfix</option>
            </optgroup>
            <option value="Other">Other — specify below</option>
          </select>
        </div>
        <div class="field-row">
          <label>If not listed / Business app</label>
          <input type="text" id="specific_product_text" placeholder="e.g. SAP ERP, Custom CRM, Internal portal">
        </div>
      </div>
      <div class="field-row" style="margin-top:14px">
        <label>Q3 — Internet facing?
          <span class="hint">DMZ, public IP, or exposed port</span>
        </label>
        <div class="radio-group">
          <label><input type="radio" name="internet_facing" value="Yes"> Yes</label>
          <label><input type="radio" name="internet_facing" value="No"> No</label>
        </div>
      </div>
    </div>
  </div>

  <!-- SECTION 3 — WORKSTATION -->
  <div class="section conditional" id="section-workstation">
    <div class="section-header green">Section 3 — Workstation Type</div>
    <div class="section-body">
      <div class="field-row">
        <label>Workstation Type</label>
        <div class="radio-group" id="workstation-type-group"></div>
      </div>
    </div>
  </div>

  <!-- SECTION 4 — APPLIANCE -->
  <div class="section conditional" id="section-appliance">
    <div class="section-header orange">Section 4 — Appliance / Network Device</div>
    <div class="section-body">
      <div class="field-row">
        <label>Appliance Type <span class="req">*</span></label>
        <div class="radio-group" id="appliance-type-group"></div>
      </div>
      <div class="field-row">
        <label>Vendor &amp; Model</label>
        <input type="text" id="appliance_vendor" placeholder="e.g. Fortinet FortiGate 100F, Cisco Catalyst 9200">
      </div>
      <div class="field-row">
        <label>Syslog Forwarding?</label>
        <div class="radio-group">
          <label><input type="radio" name="syslog_configured" value="Yes"> Yes</label>
          <label><input type="radio" name="syslog_configured" value="No"> No</label>
          <label><input type="radio" name="syslog_configured" value="Pending"> Pending</label>
        </div>
      </div>
    </div>
  </div>

  <!-- SECTION 5 — NOTES -->
  <div class="section">
    <div class="section-header green">Section 5 — Additional Notes</div>
    <div class="section-body">
      <div class="field-row">
        <label>Notes / Exceptions</label>
        <textarea id="notes" placeholder="Known constraints, exceptions, or anything the security team should know..."></textarea>
      </div>
    </div>
  </div>

</div>

<div class="action-bar">
  <button class="btn btn-primary" onclick="addToQueue()">＋ Add to Queue</button>
  <button class="btn btn-outline"  onclick="clearForm()">Clear Form</button>
  <div style="flex:1"></div>
  <span id="export-status" style="font-size:11px;color:#888;margin-right:10px"></span>
  <button class="btn btn-success" onclick="exportExcel()">⬇ Export to Excel</button>
</div>

<script>
// ── Dynamic UI build ────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', () => {{
  // Role cards
  const grid = document.getElementById('role-grid');
  Object.entries(MAPPING.server_roles).forEach(([code, role]) => {{
    const card = document.createElement('div');
    card.className = 'role-card';
    card.innerHTML = `<input type="radio" name="server_role" value="${{code}}">` +
                     `<span class="role-code">${{code}}</span>${{role.label}}`;
    card.onclick = () => selectRole(card, code);
    grid.appendChild(card);
  }});

  // Workstation types
  const wg = document.getElementById('workstation-type-group');
  Object.entries(MAPPING.workstation_roles).forEach(([code, role]) => {{
    const lbl = document.createElement('label');
    lbl.innerHTML = `<input type="radio" name="workstation_type" value="${{code}}"> ${{role.label}}`;
    wg.appendChild(lbl);
  }});
  const defW = wg.querySelector(`input[value="${{MAPPING.workstation_default}}"]`);
  if (defW) defW.checked = true;

  // Appliance types
  const ag = document.getElementById('appliance-type-group');
  Object.entries(MAPPING.appliance_roles).forEach(([code, role]) => {{
    const lbl = document.createElement('label');
    lbl.innerHTML = `<input type="radio" name="appliance_type" value="${{code}}"> ${{role.label}}`;
    ag.appendChild(lbl);
  }});
}});

// ── UI interactions ─────────────────────────────────────────────
function onAssetTypeChange() {{
  const v = getRadio('asset_type');
  document.getElementById('section-server').classList.toggle('visible', v === 'Server');
  document.getElementById('section-workstation').classList.toggle('visible', v === 'Workstation');
  document.getElementById('section-appliance').classList.toggle('visible', v === 'Appliance');
}}

function selectRole(card, code) {{
  document.querySelectorAll('.role-card').forEach(c => c.classList.remove('selected'));
  card.classList.add('selected');
  card.querySelector('input').checked = true;
  const role = MAPPING.server_roles[code] || {{}};
  document.getElementById('section-product').classList.toggle('visible', !!role.product_overrides);
}}

function getRadio(name) {{
  return document.querySelector(`input[name="${{name}}"]:checked`)?.value || '';
}}

// ── Mapping engine ──────────────────────────────────────────────
function applyMapping(fd) {{
  const m = {{ ...MAPPING.defaults }};
  m.cis_os_benchmark = MAPPING.os_to_cis_benchmark[fd.os] || '';

  if (fd.asset_type === 'Server') {{
    const role = MAPPING.server_roles[fd.server_role] || MAPPING.server_roles['OTHER'];
    m.device_function      = role.device_function;
    m.cis_app_benchmark    = role.cis_app_benchmark;
    m.sigma_product        = [...(role.sigma_product || [])];
    m.sigma_log_categories = [...(role.sigma_log_categories || [])];
    if (role.flag_log_path) m.business_app_log_path = 'PENDING — Blue Team to define';
    if (role.import_status)  m.import_status = role.import_status;
    if (role.product_overrides && fd.specific_product) {{
      const k = fd.specific_product.toLowerCase();
      if (MAPPING.product_to_cis_app_benchmark[k]) m.cis_app_benchmark = MAPPING.product_to_cis_app_benchmark[k];
      if (MAPPING.product_to_sigma[k])             m.sigma_product = MAPPING.product_to_sigma[k];
    }}
  }} else if (fd.asset_type === 'Workstation') {{
    const wt   = fd.workstation_type || MAPPING.workstation_default;
    const role = MAPPING.workstation_roles[wt] || MAPPING.workstation_roles[MAPPING.workstation_default];
    m.device_function      = role.device_function;
    m.cis_app_benchmark    = role.cis_app_benchmark;
    m.sigma_product        = [...role.sigma_product];
    m.sigma_log_categories = [...role.sigma_log_categories];
  }} else if (fd.asset_type === 'Appliance') {{
    const role = MAPPING.appliance_roles[fd.appliance_type] || MAPPING.appliance_roles['OTHER'];
    m.device_function      = role.device_function;
    m.cis_os_benchmark     = role.cis_os_benchmark;
    m.cis_app_benchmark    = role.cis_app_benchmark;
    m.sigma_product        = [...role.sigma_product];
    m.sigma_log_categories = [...role.sigma_log_categories];
    m.log_collector        = role.log_collector;
    if (role.import_status) m.import_status = role.import_status;
  }}

  // Escalation
  const esc = MAPPING.escalation;
  if (esc.fields.some(f => fd[f] === esc.values[0])) {{
    Object.assign(m, esc.overrides);
    esc.append_sigma_categories.forEach(cat => {{
      if (!m.sigma_log_categories.includes(cat)) m.sigma_log_categories.push(cat);
    }});
  }}

  m.sigma_product        = m.sigma_product.join(',');
  m.sigma_log_categories = m.sigma_log_categories.join(',');
  if (!m.import_status) m.import_status = 'Ready';
  return m;
}}

// ── Queue ───────────────────────────────────────────────────────
let queue = [];

function addToQueue() {{
  const hostname = document.getElementById('hostname').value.trim();
  if (!hostname) {{ alert('Hostname is required.'); return; }}
  const asset_type = getRadio('asset_type');
  if (!asset_type) {{ alert('Asset Type is required.'); return; }}

  const pSel  = document.getElementById('specific_product_select').value;
  const pText = document.getElementById('specific_product_text').value.trim();
  const specific_product = (pSel === 'Other' || !pSel) ? pText : pSel;

  const fd = {{
    hostname, asset_type,
    ip_address:       document.getElementById('ip_address').value.trim(),
    os:               document.getElementById('os_select').value,
    environment:      getRadio('environment'),
    sensitive_data:   getRadio('sensitive_data'),
    site:             document.getElementById('site_select').value,
    department:       document.getElementById('department').value.trim(),
    server_role:      getRadio('server_role'),
    workstation_type: getRadio('workstation_type'),
    specific_product,
    internet_facing:  getRadio('internet_facing'),
    appliance_type:   getRadio('appliance_type'),
    appliance_vendor: document.getElementById('appliance_vendor').value.trim(),
    syslog_configured:getRadio('syslog_configured'),
    notes:            document.getElementById('notes').value.trim(),
  }};

  queue.push({{ ...fd, ...applyMapping(fd) }});
  renderQueue();
  clearForm();
  document.getElementById('export-status').textContent = `${{queue.length}} device(s) in queue`;
}}

function removeDevice(i) {{
  queue.splice(i, 1);
  renderQueue();
  document.getElementById('export-status').textContent = queue.length ? `${{queue.length}} device(s) in queue` : '';
}}

function renderQueue() {{
  document.getElementById('queue-count').textContent = `${{queue.length}} device${{queue.length !== 1 ? 's' : ''}}`;
  const body = document.getElementById('queue-body');
  if (!queue.length) {{ body.innerHTML = '<div class="empty-queue">No devices added yet.</div>'; return; }}
  const bc = t => t==='Server'?'badge-server':t==='Workstation'?'badge-workstation':t==='Appliance'?'badge-appliance':'badge-review';
  body.innerHTML = `<table><thead><tr>
    <th>#</th><th>Hostname</th><th>Type</th><th>OS</th>
    <th>Device Function</th><th>CIS OS</th><th>Profile</th><th>Sigma Product</th><th>Status</th><th></th>
  </tr></thead><tbody>
  ${{queue.map((d,i) => `<tr>
    <td>${{i+1}}</td>
    <td><strong>${{d.hostname}}</strong></td>
    <td><span class="badge ${{bc(d.asset_type)}}">${{d.asset_type}}</span></td>
    <td style="font-size:11px">${{d.os||'—'}}</td>
    <td><strong style="color:var(--blue)">${{d.device_function||'—'}}</strong></td>
    <td style="font-size:11px">${{d.cis_os_benchmark||'—'}}</td>
    <td><span class="badge badge-server">${{d.cis_os_profile||'—'}}</span></td>
    <td style="font-size:10px">${{d.sigma_product||'—'}}</td>
    <td><span class="badge ${{d.import_status==='Ready'?'badge-server':'badge-review'}}">${{d.import_status}}</span></td>
    <td><button class="remove-btn" onclick="removeDevice(${{i}})">✕</button></td>
  </tr>`).join('')}}
  </tbody></table>`;
}}

function clearForm() {{
  ['hostname','ip_address','department','appliance_vendor','specific_product_text','notes']
    .forEach(id => {{ const el = document.getElementById(id); if (el) el.value = ''; }});
  ['os_select','site_select','specific_product_select']
    .forEach(id => {{ const el = document.getElementById(id); if (el) el.selectedIndex = 0; }});
  document.querySelectorAll('input[type=radio]').forEach(r => r.checked = false);
  document.querySelectorAll('.role-card').forEach(c => c.classList.remove('selected'));
  ['section-server','section-workstation','section-appliance','section-product']
    .forEach(id => document.getElementById(id)?.classList.remove('visible'));
  const defW = document.querySelector(`input[name="workstation_type"][value="${{MAPPING.workstation_default}}"]`);
  if (defW) defW.checked = true;
}}

// ── Excel export ────────────────────────────────────────────────
function exportExcel() {{
  if (!queue.length) {{ alert('Add at least one device before exporting.'); return; }}
  const COLS = [
    'hostname','ip_address','asset_type','os','environment','sensitive_data','site','department',
    'server_role','workstation_type','specific_product','internet_facing',
    'appliance_type','appliance_vendor','syslog_configured',
    'device_function','cis_os_benchmark','cis_os_profile','cis_app_benchmark',
    'sigma_product','sigma_log_categories','business_app_log_path',
    'cis_os_status','cis_app_status','log_collection_status','log_collector',
    'hardening_exception_notes','notes','import_status'
  ];
  const wb = XLSX.utils.book_new();
  const ws = XLSX.utils.aoa_to_sheet([COLS, ...queue.map(d => COLS.map(c => d[c] ?? ''))]);
  ws['!cols'] = COLS.map(c => ({{ wch: Math.max(c.length + 2, 20) }}));
  XLSX.utils.book_append_sheet(wb, ws, 'Device Onboarding');
  const readme = [
    ['SOC Device Onboarding Export'],
    ['Generated: ' + new Date().toISOString().slice(0,19)],
    ['Schema from NetBox: {generated_at}'],
    [''],
    ['COLUMN','TYPE','DESCRIPTION'],
    ['hostname','Form','Device hostname — primary key for NetBox match/create'],
    ['ip_address','Form','Management IP'],
    ['asset_type','Form','Server / Workstation / Appliance'],
    ['os','Form','Operating system'],
    ['environment','Form','Production / Development / Test / DMZ'],
    ['sensitive_data','Form','Yes/No — escalates CIS profile to L2'],
    ['site','Form','NetBox site slug'],
    ['department','Form','Owner team (workstations)'],
    ['server_role','Form','SRV-* code from Q1'],
    ['workstation_type','Form','WRK-* code'],
    ['specific_product','Form','Software product from Q2'],
    ['internet_facing','Form','Yes/No — escalates CIS to L2 + adds network_connection'],
    ['appliance_type','Form','APL-* code'],
    ['device_function','MAPPED','NetBox CF: device_function'],
    ['cis_os_benchmark','MAPPED','NetBox CF: cis_os_benchmark'],
    ['cis_os_profile','MAPPED','NetBox CF: cis_os_profile — L1 or L2'],
    ['cis_app_benchmark','MAPPED','NetBox CF: cis_app_benchmark'],
    ['sigma_product','MAPPED','NetBox CF: sigma_product (comma-separated)'],
    ['sigma_log_categories','MAPPED','NetBox CF: sigma_log_categories (comma-separated)'],
    ['business_app_log_path','MAPPED','NetBox CF: for SRV-APP — Blue Team fills log path'],
    ['cis_os_status','DEFAULT','NOT-STARTED — updated by Ansible'],
    ['cis_app_status','DEFAULT','NOT-STARTED — updated by Ansible'],
    ['log_collection_status','DEFAULT','NOT-CONFIGURED — updated by Blue Team'],
    ['log_collector','DEFAULT','NONE — updated after agent deployment'],
    ['import_status','META','Ready = import now | REVIEW NEEDED = fix device_function first'],
  ];
  const wsR = XLSX.utils.aoa_to_sheet(readme);
  wsR['!cols'] = [{{wch:28}},{{wch:10}},{{wch:70}}];
  XLSX.utils.book_append_sheet(wb, wsR, 'README');
  const date = new Date().toISOString().slice(0,10);
  XLSX.writeFile(wb, `device_onboarding_${{date}}.xlsx`);
  document.getElementById('export-status').textContent = `Exported ${{queue.length}} device(s) — ${{date}}`;
}}
</script>
</body>
</html>"""


# ══════════════════════════════════════════════════════════════════
# Writers
# ══════════════════════════════════════════════════════════════════

def write_html(schema: dict, mapping: dict):
    html = build_html(schema, mapping)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  ✓ Written: {OUTPUT_HTML}  ← open this in any browser")


def write_yaml(schema: dict):
    with open(OUTPUT_YAML, "w", encoding="utf-8") as f:
        yaml.dump(schema, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    print(f"  ✓ Written: {OUTPUT_YAML}")


# ══════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════

def main():
    print("=" * 55)
    print("  NetBox Schema Pull — SOC Onboarding")
    print(f"  Target: {NETBOX_URL}")
    print("=" * 55)

    try:
        info = nb_get("status/")
        print(f"  Connected — NetBox {info.get('netbox-version', '?')}\n")
    except Exception as e:
        print(f"\n[ERROR] Cannot connect to NetBox: {e}")
        sys.exit(1)

    choice_sets   = pull_choice_sets()
    custom_fields = pull_custom_fields(choice_sets)
    sites         = pull_sites()

    schema = build_schema(choice_sets, custom_fields, sites)

    print()
    mapping = load_mapping()
    print(f"  Mapping: {MAPPING_FILE} loaded")

    write_yaml(schema)
    write_html(schema, mapping)

    print(f"\n  Done.")
    print(f"  → Open {OUTPUT_HTML} directly in any browser — no server needed.")
    print(f"  → Re-run this script whenever choice sets or sites change in NetBox.")
    print("=" * 55)


if __name__ == "__main__":
    main()
