# Gitea + Woodpecker CI — On-Premise Change Management Setup Guide

## Overview

This guide covers the full setup of a self-hosted Git + CI/CD stack using **Gitea** (Git server with web UI) and **Woodpecker CI** (pipeline runner). The goal is to manage code changes for multiple projects and automatically deploy them to their target servers via SSH after each commit.

**Stack:**
- **Gitea** — Git hosting, web-based file editor, OAuth provider
- **Woodpecker CI** — Pipeline runner, triggered on push, executes deploy commands via SSH

---

## Architecture

```
Developer (browser or git push)
        │
        ▼
  ┌─────────────┐     webhook trigger
  │   Gitea     │ ──────────────────────────▶ Woodpecker CI
  │  :3000      │                                   │
  └─────────────┘                    ┌──────────────┼──────────────┐
                                     ▼              ▼              ▼
                               10.0.10.10      10.0.10.x      10.0.10.x
                             (FastAPI/Docker) (cron script) (cron script)
                              SSH + rebuild    SSH + pull      SSH + pull
```

---

## Part 1 — Installation

### Prerequisites

- A server to host Gitea + Woodpecker (this machine: `192.168.50.176`)
- Docker and Docker Compose installed
- Network access to all target project servers

### 1.1 — docker-compose.yml

Create a working directory and place this file:

```yaml
services:
  gitea:
    image: gitea/gitea:latest
    ports:
      - "3000:3000"
      - "222:22"
    volumes:
      - gitea_data:/data
    environment:
      - GITEA__server__ROOT_URL=http://192.168.50.176:3000

  woodpecker-server:
    image: woodpeckerci/woodpecker-server:v3
    ports:
      - "8000:8000"
      - "9000:9000"
    environment:
      - WOODPECKER_HOST=http://192.168.50.176:8000
      - WOODPECKER_OPEN=false
      - WOODPECKER_ADMIN=your_gitea_username
      - WOODPECKER_GITEA=true
      - WOODPECKER_GITEA_URL=http://192.168.50.176:3000
      - WOODPECKER_GITEA_CLIENT=YOUR_GITEA_OAUTH_CLIENT
      - WOODPECKER_GITEA_SECRET=YOUR_GITEA_OAUTH_SECRET
      - WOODPECKER_AGENT_SECRET=a_strong_shared_secret
    depends_on:
      - gitea

  woodpecker-agent:
    image: woodpeckerci/woodpecker-agent:v3
    environment:
      - WOODPECKER_SERVER=woodpecker-server:9000
      - WOODPECKER_AGENT_SECRET=a_strong_shared_secret
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    depends_on:
      - woodpecker-server

volumes:
  gitea_data:
```

**Key variables to fill in:**
| Variable | Value |
|---|---|
| `WOODPECKER_HOST` | Full URL of this server on port 8000 |
| `WOODPECKER_ADMIN` | Your Gitea admin username |
| `WOODPECKER_GITEA_CLIENT` | OAuth Client ID from Gitea (see Part 2) |
| `WOODPECKER_GITEA_SECRET` | OAuth Client Secret from Gitea (see Part 2) |
| `WOODPECKER_AGENT_SECRET` | Any strong shared string (same in both server and agent) |

### 1.2 — First Boot (Gitea only)

Start only Gitea first to complete its setup wizard:

```bash
docker compose up -d gitea
```

Open `http://192.168.50.176:3000` in your browser.

---

## Part 2 — Gitea Initial Configuration

### 2.1 — Setup Wizard

Fill in the form at `http://192.168.50.176:3000`:

| Field | Value |
|---|---|
| Database Type | SQLite3 (fine for this use case) |
| Server Domain | `192.168.50.176` |
| SSH Server Port | `222` (matches docker-compose port mapping) |
| Gitea HTTP Listen Port | `3000` |
| Gitea Base URL | `http://192.168.50.176:3000/` |
| Enable Update Checker | Unchecked |

Scroll down to **Administrator Account Settings** and create your admin user before submitting.

### 2.2 — Create OAuth2 App for Woodpecker

1. Log in as admin
2. Go to **top-right menu → Settings → Applications**
3. Scroll to **Manage OAuth2 Applications → Create OAuth2 Application**
4. Fill in:
   - **Name:** `Woodpecker`
   - **Redirect URI:** `http://192.168.50.176:8000/authorize`
5. Click **Create**
6. **Copy the Client ID and Client Secret** — you will not see the secret again

Paste both values into `docker-compose.yml` under `WOODPECKER_GITEA_CLIENT` and `WOODPECKER_GITEA_SECRET`.

---

## Part 3 — Start Full Stack

```bash
docker compose down
docker compose up -d
```

### 3.1 — Log into Woodpecker

Open `http://192.168.50.176:8000` and click the **Login with Gitea** button. It will redirect to Gitea for authorization and return you to Woodpecker.

> **Troubleshooting:** If you see `error=registration_closed`, make sure `WOODPECKER_ADMIN` in docker-compose matches your exact Gitea username, then restart the stack.

---

## Part 4 — SSH Key Setup (One-Time Per Target Server)

Woodpecker deploys to remote servers via SSH. You need to set up a key pair so Woodpecker can authenticate without a password.

### 4.1 — Generate the deploy key

Run this on the Gitea/Woodpecker host (`192.168.50.176`):

```bash
ssh-keygen -t ed25519 -f ~/.ssh/woodpecker_deploy -N ""
```

This creates two files:
- `~/.ssh/woodpecker_deploy` — private key (goes into Woodpecker)
- `~/.ssh/woodpecker_deploy.pub` — public key (goes onto each target server)

### 4.2 — Copy public key to each target server

```bash
# For the dashboard project server
ssh-copy-id -i ~/.ssh/woodpecker_deploy.pub cyberops@10.0.10.10

# Repeat for any additional servers
# ssh-copy-id -i ~/.ssh/woodpecker_deploy.pub cyberops@10.0.10.x
```

Test it works:

```bash
ssh -i ~/.ssh/woodpecker_deploy cyberops@10.0.10.10 "echo OK"
```

### 4.3 — Add private key as a Woodpecker Secret

1. In Woodpecker, go to **Settings (gear icon) → Secrets**
2. Click **Add Secret**
3. Name: `SSH_DEPLOY_KEY`
4. Value: paste the contents of `~/.ssh/woodpecker_deploy` (the private key file)
5. Save

> You can also set secrets per-repository in the repo settings instead of globally.

---

## Part 5 — Project Setup: FastAPI Dashboard

**Server:** `10.0.10.10`  
**Path:** `/opt/dash/`  
**User:** `cyberops`  
**Stack:** FastAPI + Docker Compose + Nginx  
**Deploy logic:** HTML changes → just `git pull` (files are in a volume). `main.py` changes → full `docker compose up --build --no-cache`

### 5.1 — Create the repo in Gitea

1. Go to `http://192.168.50.176:3000`
2. Click **+** → **New Repository**
3. Name: `dashboard-app`
4. Set to **Private**
5. Check **Initialize this repository**
6. Click **Create Repository**

### 5.2 — Push your project files

On your local machine or directly on `10.0.10.10`:

```bash
cd /opt/dash
git init
git remote add origin http://192.168.50.176:3000/YOUR_USERNAME/dashboard-app.git
git add .
git commit -m "Initial commit"
git push -u origin main
```

### 5.3 — Add the pipeline file

Create `.woodpecker.yml` in the root of the repo:

```yaml
steps:
  - name: deploy
    image: appleboy/drone-ssh
    settings:
      host: 10.0.10.10
      username: cyberops
      key:
        from_secret: SSH_DEPLOY_KEY
      port: 22
      script:
        - cd /opt/dash
        - git pull origin main
        - |
          if git diff HEAD~1 --name-only | grep -q "main.py"; then
            echo "main.py changed — rebuilding container..."
            docker compose up -d --no-cache --build
          else
            echo "HTML/static change only — no rebuild needed"
          fi
```

Commit and push `.woodpecker.yml`.

### 5.4 — Activate repo in Woodpecker

1. Go to `http://192.168.50.176:8000`
2. Click **+ Add repository**
3. Find `dashboard-app` and click **Enable**

From now on, every push to this repo triggers the pipeline automatically.

---

## Part 6 — Project Setup: Python Cron Scripts

For Python scripts that run via cron — no Docker rebuild needed, just pull the latest code.

**Example for a ticket collector or HTML dashboard generator:**

```yaml
# .woodpecker.yml
steps:
  - name: deploy
    image: appleboy/drone-ssh
    settings:
      host: 10.0.10.x        # replace with actual server IP
      username: cyberops
      key:
        from_secret: SSH_DEPLOY_KEY
      port: 22
      script:
        - cd /opt/your-script-folder
        - git pull origin main
        # Cron will pick up the updated script on next run automatically
```

Same process as above — create repo in Gitea, push files with `.woodpecker.yml`, activate in Woodpecker.

---

## Part 7 — Day-to-Day Usage

### Making a change via browser (no git client needed)

1. Open `http://192.168.50.176:3000`
2. Navigate to the repo → find the file
3. Click the **pencil (✏️) icon** to edit
4. Make your changes
5. Click **Commit Changes**
6. Woodpecker picks up the webhook automatically and runs the pipeline

### Watching the pipeline run

1. Open `http://192.168.50.176:8000`
2. Click the repo → see the running pipeline with live logs

### Manually triggering a pipeline

In Woodpecker, open the repo → click **Trigger** (or re-run last build).

---

## Part 8 — Adding New Projects

For each new project:

1. Create a new repo in Gitea
2. Push your code with a `.woodpecker.yml` at the root
3. Copy the public SSH deploy key to the new target server (`ssh-copy-id`)
4. Enable the repo in Woodpecker UI
5. Done — pushes auto-deploy from that point on

The SSH_DEPLOY_KEY secret is already global, so no need to re-add it per project.

---

## Reference — Common Issues

| Error | Cause | Fix |
|---|---|---|
| `WOODPECKER_HOST is not properly configured` | Missing env var | Add `WOODPECKER_HOST=http://IP:8000` to compose |
| `error=registration_closed` | Admin user not set | Add `WOODPECKER_ADMIN=username` to compose |
| Agent can't connect to server | Port 9000 not exposed | Add `9000:9000` to woodpecker-server ports |
| SSH deploy fails | Key not on target server | Run `ssh-copy-id` to the target |
| Pipeline not triggering | Repo not activated | Click Enable in Woodpecker → Add repository |

---

## Infrastructure Summary

| Service | URL | Purpose |
|---|---|---|
| Gitea | `http://192.168.50.176:3000` | Git hosting + web editor |
| Woodpecker | `http://192.168.50.176:8000` | Pipeline runner + logs |

| Project | Server | Path | Trigger |
|---|---|---|---|
| FastAPI Dashboard | `10.0.10.10` | `/opt/dash` | git pull + conditional rebuild |
| Cron Script 1 | TBD | TBD | git pull only |
| Cron Script 2 | TBD | TBD | git pull only |
