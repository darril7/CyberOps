# =============================================================================
# SOC Inventory — Prefect Self-Hosted (On-Premise) Setup Guide
# =============================================================================
# Prerequisites:
#   - Ubuntu 22.04+ server to run Prefect Server + Worker
#   - Python 3.11+
#   - Project copied to /opt/soc_inventory
#   - Netbox already running and reachable
#
# Run each phase manually — do not pipe this whole file to bash.
# =============================================================================


# -----------------------------------------------------------------------------
# PHASE 1 — Python environment
# -----------------------------------------------------------------------------

python3 -m venv /opt/soc_inventory/.venv
source /opt/soc_inventory/.venv/bin/activate

cd /opt/soc_inventory
pip install --upgrade pip
pip install -r requirements.txt

# Verify
prefect version


# -----------------------------------------------------------------------------
# PHASE 2 — Point Prefect at your local server
# Run this on every machine that runs flows or workers
# -----------------------------------------------------------------------------

# Same machine (server + worker together):
prefect config set PREFECT_API_URL="https://prefect.reduno.online/api"

# Different machines (replace with your server IP):
# prefect config set PREFECT_API_URL="http://192.168.1.50:4200/api"

prefect config view
# Confirm: PREFECT_API_URL = https://prefect.reduno.online/api


# -----------------------------------------------------------------------------
# PHASE 3 — Start Prefect Server
# -----------------------------------------------------------------------------

# Option A — foreground (testing only)
prefect server start

# Option B — systemd service (production)
# Replace YOUR_USER with your actual Linux username before running

sudo tee /etc/systemd/system/prefect-server.service > /dev/null << 'UNIT'
[Unit]
Description=Prefect Server
After=network.target

[Service]
User=YOUR_USER
WorkingDirectory=/opt/soc_inventory
ExecStart=/opt/soc_inventory/.venv/bin/prefect server start --host 0.0.0.0
Restart=on-failure
RestartSec=10
Environment=HOME=/home/YOUR_USER

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable prefect-server
sudo systemctl start prefect-server

# Verify — UI should be reachable at https://prefect.reduno.online
curl https://prefect.reduno.online/api/health
# Expected: {"status":"healthy"}


# -----------------------------------------------------------------------------
# PHASE 4 — Create Secret Blocks
# -----------------------------------------------------------------------------
# Blocks store all credentials — nothing sensitive goes in playbook.yaml.
# Block names follow this pattern:
#
#   {client-slug}/{tool}-{scope}
#
#   scope = "main"     → shared across all sites for that client
#   scope = {site}     → specific to one site (e.g. wsus-c1-office1)
#
# Run this script once. Edit ALL placeholder values first.
# Add/remove blocks to match the tools each client actually has deployed.
# -----------------------------------------------------------------------------

source /opt/soc_inventory/.venv/bin/activate

python3 - << 'PYTHON'
import asyncio
from prefect.blocks.system import Secret

async def create_blocks():
    blocks = {

        # ── Netbox — shared across all clients ───────────────────────────────
        # One Netbox instance holds all tenants/sites/devices
        "netbox-api": {
            "url":   "http://YOUR_NETBOX_HOST",   # e.g. http://192.168.1.10 or http://netbox.local
            "token": "YOUR_NETBOX_API_TOKEN",     # Netbox → Admin → API Tokens → Add
        },

        # ════════════════════════════════════════════════════════════════════
        # CLIENT 1  (sites: c1-office1, c1-dc1)
        # ════════════════════════════════════════════════════════════════════

        # AD — shared across all client1 sites (one domain, different OUs)
        "client1-ad-main": {
            "username": "svc_inventory",      # AD service account (read-only)
            "password": "...",
        },

        # GLPI — shared across all client1 sites
        "client1-glpi-main": {
            "app_token":  "...",              # GLPI → Setup → API → App token
            "user_token": "...",              # GLPI → My account → API token
        },

        # Trellix ePO — shared (group_filter scopes per site in playbook)
        "client1-trellix-main": {
            "api_key":    "...",              # ePO → User Mgmt → Users → Generate API Key
            "api_secret": "...",
        },

        # Wazuh — shared (same manager covers all sites)
        "client1-wazuh-main": {
            "username": "wazuh-api-user",
            "password": "...",
            "host":     "wazuh.client1.local",
        },

        # Elastic — shared
        "client1-elastic-main": {
            "api_key": "...",                 # Kibana → Stack Mgmt → API Keys
            "host":    "elastic.client1.local",
        },

        # Nessus — shared scanner (scan_filter scopes per site in playbook)
        "client1-nessus-main": {
            "access_key": "...",              # Nessus → Settings → My Account → API Keys
            "secret_key": "...",
            "host":       "nessus.client1.local",
        },

        # Teramind — shared
        "client1-teramind-main": {
            "client_id":     "...",
            "client_secret": "...",
            "host":          "teramind.client1.local",
        },

        # WSUS — one per site (different server + device scope per site)
        "client1-wsus-c1-office1": {
            "username": "svc_wsus",
            "password": "...",
            "host":     "wsus-office1.client1.local",
        },
        "client1-wsus-c1-dc1": {
            "username": "svc_wsus",
            "password": "...",
            "host":     "wsus-dc1.client1.local",
        },

        # FortiGate — one per site (each site has its own firewall)
        "client1-fortigate-c1-office1": {
            "api_token": "...",               # FortiGate → System → Admin → REST API Admin
            "host":      "fw-office1.client1.local",
        },
        "client1-fortigate-c1-dc1": {
            "api_token": "...",
            "host":      "fw-dc1.client1.local",
        },

        # vCenter — one per DC site
        "client1-vcenter-c1-dc1": {
            "username": "svc_inventory@vsphere.local",
            "password": "...",
            "host":     "vcenter-dc1.client1.local",
        },

        # ════════════════════════════════════════════════════════════════════
        # CLIENT 2  (sites: c2-office1)
        # Simpler stack — no WSUS, no vCenter, no Wazuh/Elastic/Teramind
        # ════════════════════════════════════════════════════════════════════

        "client2-ad-main": {
            "username": "svc_inventory",
            "password": "...",
        },
        "client2-glpi-main": {
            "app_token":  "...",
            "user_token": "...",
        },
        "client2-trellix-main": {
            "api_key":    "...",
            "api_secret": "...",
        },
        "client2-nessus-main": {
            "access_key": "...",
            "secret_key": "...",
            "host":       "nessus.client2.local",
        },
        "client2-fortigate-c2-office1": {
            "api_token": "...",
            "host":      "fw-office1.client2.local",
        },

        # ════════════════════════════════════════════════════════════════════
        # CLIENT 3  (sites: c3-office1, c3-dc1, c3-dc2)
        # Full stack + MDM + separate vCenter and FortiGate per DC
        # ════════════════════════════════════════════════════════════════════

        "client3-ad-main": {
            "username": "svc_inventory",
            "password": "...",
        },
        "client3-glpi-main": {
            "app_token":  "...",
            "user_token": "...",
        },
        "client3-trellix-main": {
            "api_key":    "...",
            "api_secret": "...",
        },
        "client3-mdm-main": {
            "client_id":     "...",           # Azure app registration client ID
            "client_secret": "...",           # Azure app registration secret
            "tenant_id":     "...",           # Azure AD tenant ID
        },
        "client3-teramind-main": {
            "client_id":     "...",
            "client_secret": "...",
            "host":          "teramind.client3.local",
        },

        # WSUS — one per site
        "client3-wsus-c3-office1": {
            "username": "svc_wsus",
            "password": "...",
            "host":     "wsus-office1.client3.local",
        },
        "client3-wsus-c3-dc1": {
            "username": "svc_wsus",
            "password": "...",
            "host":     "wsus-dc1.client3.local",
        },
        "client3-wsus-c3-dc2": {
            "username": "svc_wsus",
            "password": "...",
            "host":     "wsus-dc2.client3.local",
        },

        # vCenter — one per DC
        "client3-vcenter-c3-dc1": {
            "username": "svc_inventory@vsphere.local",
            "password": "...",
            "host":     "vcenter-dc1.client3.local",
        },
        "client3-vcenter-c3-dc2": {
            "username": "svc_inventory@vsphere.local",
            "password": "...",
            "host":     "vcenter-dc2.client3.local",
        },

        # FortiGate — one per site
        "client3-fortigate-c3-office1": {
            "api_token": "...",
            "host":      "fw-office1.client3.local",
        },
        "client3-fortigate-c3-dc1": {
            "api_token": "...",
            "host":      "fw-dc1.client3.local",
        },
        "client3-fortigate-c3-dc2": {
            "api_token": "...",
            "host":      "fw-dc2.client3.local",
        },

        # Nessus — one per DC (office shares dc1 scanner)
        "client3-nessus-c3-dc1": {
            "access_key": "...",
            "secret_key": "...",
            "host":       "nessus-dc1.client3.local",
        },
        "client3-nessus-c3-dc2": {
            "access_key": "...",
            "secret_key": "...",
            "host":       "nessus-dc2.client3.local",
        },

    }

    print(f"Creating {len(blocks)} blocks...\n")
    for name, value in blocks.items():
        await Secret(value=value).save(name, overwrite=True)
        print(f"  ✓  {name}")

    print(f"\nDone — {len(blocks)} blocks registered in Prefect Server.")
    print("Verify at: https://prefect.reduno.online/blocks")

asyncio.run(create_blocks())
PYTHON


# -----------------------------------------------------------------------------
# PHASE 5 — Create Work Pool
# -----------------------------------------------------------------------------

prefect work-pool create soc-pool --type process
prefect work-pool ls


# -----------------------------------------------------------------------------
# PHASE 6 — Start Worker (systemd)
# Replace YOUR_USER with your actual Linux username
# -----------------------------------------------------------------------------

sudo tee /etc/systemd/system/prefect-worker.service > /dev/null << 'UNIT'
[Unit]
Description=Prefect Worker
After=network.target prefect-server.service

[Service]
User=YOUR_USER
WorkingDirectory=/opt/soc_inventory
ExecStart=/opt/soc_inventory/.venv/bin/prefect worker start --pool soc-pool
Restart=on-failure
RestartSec=10
Environment=HOME=/home/YOUR_USER
Environment=PREFECT_API_URL=https://prefect.reduno.online/api

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable prefect-worker
sudo systemctl start prefect-worker
sudo systemctl status prefect-worker


# -----------------------------------------------------------------------------
# PHASE 7 — Deploy flows
# Run this every time you change prefect.yaml or flow code
# -----------------------------------------------------------------------------

source /opt/soc_inventory/.venv/bin/activate
cd /opt/soc_inventory

prefect deploy --all
prefect deployment ls
# Expected:
#   soc-inventory/daily-full
#   soc-inventory/on-demand-customer
#   soc-inventory/dry-run


# -----------------------------------------------------------------------------
# PHASE 8 — Test: dry run (no writes to Netbox)
# -----------------------------------------------------------------------------

# Single client, single tool — safest first test
prefect deployment run 'soc-inventory/dry-run' \
  --param customer=client1 \
  --param tool=ad \
  --param dry_run=true

# Watch logs
prefect flow-run ls
prefect flow-run logs <FLOW_RUN_ID>

# Full client dry run
prefect deployment run 'soc-inventory/dry-run' \
  --param customer=client1 \
  --param dry_run=true


# -----------------------------------------------------------------------------
# PHASE 9 — First real run
# -----------------------------------------------------------------------------

# Single client
prefect deployment run 'soc-inventory/on-demand-customer' \
  --param customer=client1

# Single site
prefect deployment run 'soc-inventory/on-demand-customer' \
  --param customer=client1 \
  --param site=c1-dc1

# Single tool across all clients
prefect deployment run 'soc-inventory/on-demand-customer' \
  --param tool=trellix


# -----------------------------------------------------------------------------
# PHASE 10 — Enable daily schedule
# -----------------------------------------------------------------------------

# In the UI: Deployments → soc-inventory/daily-full → toggle Scheduled ON
# Or CLI:
prefect deployment set-schedule 'soc-inventory/daily-full' \
  --cron "0 6 * * *" \
  --timezone "America/Costa_Rica"

prefect deployment inspect 'soc-inventory/daily-full'


# =============================================================================
# REFERENCE — Block naming convention
# =============================================================================
#
# Pattern: {client-slug}/{tool}-{scope}
#
#   scope = "main"     → one instance shared across all sites for that client
#   scope = {site}     → instance is specific to one site
#
# Shared tools (main):          Site-specific tools:
#   client1/ad-main               client1/wsus-c1-office1
#   client1/glpi-main             client1/wsus-c1-dc1
#   client1/trellix-main          client1/fortigate-c1-office1
#   client1/wazuh-main            client1/fortigate-c1-dc1
#   client1/elastic-main          client1/vcenter-c1-dc1
#   client1/nessus-main           client1/nessus-c1-dc2  ← if separate scanner
#   client1/teramind-main
#
# Rule of thumb:
#   If the tool has ONE instance that covers all sites → use "main"
#   If the tool has a SEPARATE instance per site      → use the site slug
#
# =============================================================================
# DAY-TO-DAY COMMANDS
# =============================================================================

# Run one client
prefect deployment run 'soc-inventory/on-demand-customer' --param customer=client1

# Run one site
prefect deployment run 'soc-inventory/on-demand-customer' \
  --param customer=client1 --param site=c1-dc1

# Run one tool for all clients
prefect deployment run 'soc-inventory/on-demand-customer' --param tool=wsus

# Check running flows
prefect flow-run ls

# Cancel a run
prefect flow-run cancel <FLOW_RUN_ID>

# Restart worker after code change
sudo systemctl restart prefect-worker

# Redeploy after changing prefect.yaml
cd /opt/soc_inventory && prefect deploy --all

# Rotate a credential (e.g. AD password changed)
python3 - << 'PYTHON'
import asyncio
from prefect.blocks.system import Secret

async def update():
    await Secret(value={"username": "svc_inventory", "password": "NEW_PASSWORD"}) \
        .save("client1-ad-main", overwrite=True)
    print("Block updated.")

asyncio.run(update())
PYTHON

# =============================================================================
# ADDING A NEW CLIENT
# =============================================================================
# 1. Add tenant in Netbox  → slug: client4
# 2. Add sites in Netbox   → slugs: c4-office1, c4-dc1, etc.
# 3. Add devices to Netbox under the correct tenant + site
# 4. Add client4 entry to config/playbook.yaml (copy client4 template block)
# 5. Add blocks to Phase 4 script and run it for the new blocks only
# 6. prefect deploy --all
# → Next scheduled run picks it up automatically

# ADDING A NEW TOOL
# =============================================================================
# 1. Write scripts/{tool}_inventory.py → async def fetch(credentials, **params)
# 2. Add tool entry to config/field_map.yaml
# 3. Add to SCRIPT_REGISTRY in runner/run_task.py
# 4. Add to relevant sites in config/playbook.yaml
# 5. Create credential blocks for each client that has the tool
# 6. prefect deploy --all
