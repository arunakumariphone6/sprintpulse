# Jira Intelligence Dashboard — Run Locally & Share with Your Team

**Author:** Arunakumar Tavva
**© 2026 Arunakumar Tavva. All rights reserved.**

Real-time Jira dashboard. Connects via **Jira API** using your credentials from `.env` — auto-loads on startup. Share the dashboard URL with your team; they hit **↺ Refresh** to pull live data anytime.

---

## Prerequisites

| Requirement | Download |
|---|---|
| Docker Desktop | [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop) |
| Your Jira API Token | [id.atlassian.com → Security → API Tokens](https://id.atlassian.com/manage-profile/security/api-tokens) |

---

## Step 1 — Get Your Jira API Token

1. Go to **[https://id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens)**
2. Click **Create API token**
3. Name it `Jira Intelligence Dashboard`
4. Click **Create** → copy the token immediately (shown only once)

---

## Step 2 — Set Up the Project

Open a terminal in the project folder (`jira-intelligence-dashboard/`) and run:

```powershell
# Copy the environment template (only needed once)
copy .env.example .env
```

Open `.env` and fill in your credentials:

```env
JIRA_URL=https://yourcompany.atlassian.net        # your Jira base URL — no trailing slash
JIRA_EMAIL=your.email@company.com                 # your Jira login email
JIRA_API_TOKEN=your_api_token_here                # token from Step 1
JIRA_PROJECTS=P2,VPD,UAMRQ                        # comma-separated project keys (leave blank = all)
JIRA_MAX_RESULTS=500                              # increase for large projects
```

> **Auto-connect:** When all three (URL, email, token) are set in `.env`, the dashboard connects to Jira automatically on startup — no manual login needed. You can go straight to `/dashboard`.

---

## Step 3 — Start the Dashboard

```powershell
docker compose up --build -d
```

> First run downloads the Python image (~50 MB) and installs packages — takes ~2 minutes.
> Subsequent starts are instant: `docker compose up -d`

---

## Step 4 — Connect to Jira

**If `.env` is configured:** Open **[http://localhost:8085](http://localhost:8085)** — you'll be redirected straight to the dashboard once data is loaded (usually within 10–30 seconds).

**If `.env` is empty or you prefer the UI:** Open **[http://localhost:8085](http://localhost:8085)** and fill in:
- **Jira URL** — e.g. `https://yourcompany.atlassian.net` (no trailing slash, no `/jira` suffix)
- **Email** — your Jira login email
- **API Token** — from Step 1

Click **Test Connection** → then **Connect & Load Dashboard**.

> Supports **Jira Cloud** and **Jira Server / Data Center** — the app auto-detects the correct API endpoint.

---

## Step 5 — Access the Dashboard

| URL | Purpose |
|---|---|
| `http://localhost:8085` | Connect page (or redirects to dashboard if already connected) |
| `http://localhost:8085/dashboard` | Live board dashboard — share this with your team |
| `http://localhost:8085/people` | **People Intelligence** — team productivity, leaderboard & CEO report |
| `http://localhost:8085/refresh` | Force re-fetch from Jira |
| `http://localhost:8085/status` | Health / status check (JSON) |

> **People Intelligence** (`/people`) is a separate CEO-grade dashboard showing individual productivity scores, a gamified leaderboard with achievement badges (🏆 MVP, 🐛 Bug Slayer, ⚡ Speed Demon), performance tier distribution, and a ready-to-share Action Plan. Accessible via the **👥 People** button in the board dashboard navbar.

---

## Step 6 — Share with Your Team (Same Company Network)

Your colleagues can access the dashboard **directly from their own browsers** as long as:
- Your computer is **on** and Docker is running
- You are on the **same office Wi-Fi / LAN**

Share this URL with your team:

```
http://192.168.1.52:8085/dashboard
```

> If your IP changes (e.g. after a reboot), run `ipconfig` in PowerShell and look for
> the **IPv4 Address** under **Wi-Fi** — use that number instead.

### What your colleagues need to do

They just open the URL above in any browser — **no install, no login, no Docker required**.
They click **↺ Refresh** in the top nav to pull the latest data from Jira at any time.

---

## Firewall — Allow Port 8085 (one-time setup)

Windows Firewall may block incoming connections on port 8085.
Run this **once** in PowerShell as Administrator to open it:

```powershell
New-NetFirewallRule -DisplayName "Jira Intelligence Dashboard" `
  -Direction Inbound -Protocol TCP -LocalPort 8085 -Action Allow
```

To remove the rule later:
```powershell
Remove-NetFirewallRule -DisplayName "Jira Intelligence Dashboard"
```

---

## Daily Use — Docker Commands

| Task | Command |
|---|---|
| Start | `docker compose up -d` |
| Stop | `docker compose down` |
| Restart | `docker compose restart` |
| View logs | `docker compose logs -f` |
| Rebuild after code changes | `docker compose up --build -d` |

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `Port already in use` | Change `"8085:8080"` to `"8086:8080"` in `docker-compose.yml` |
| `Connection refused` | Make sure Docker Desktop is running |
| `401 Unauthorized` | Wrong email or API token |
| `404 Not Found` | Check Jira URL — no trailing slash, no extra path |
| `410 Gone` | Remove `/jira` or any extra path from the Jira URL (app auto-tries multiple endpoints) |
| Team can't access URL | Run the firewall rule above; check you're on the same network |
| Dashboard blank | Click ↺ Refresh — data may still be loading (first load takes 10–30 sec) |
| Auto-connect not working | Ensure `JIRA_URL`, `JIRA_EMAIL`, and `JIRA_API_TOKEN` are all set in `.env` |

---

## Jira URL Format — Common Mistakes

| Type | Correct Format | Wrong |
|---|---|---|
| Jira Cloud | `https://yourcompany.atlassian.net` | `https://yourcompany.atlassian.net/jira` |
| Jira Server/DC | `https://jira.yourcompany.com` | `https://jira.yourcompany.com/` |

---

*© 2026 Arunakumar Tavva. All rights reserved.*
