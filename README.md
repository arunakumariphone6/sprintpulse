# Jira Intelligence Dashboard

**Author:** Arunakumar Tavva
**Version:** 2.0
**License:** Proprietary — © 2026 Arunakumar Tavva. All rights reserved.

Real-time Jira dashboard. Connect once with your API token → share the URL with your entire team → they hit **↺ Refresh** to get the latest data anytime.

---

## Quick Start (Docker — Recommended)

### Prerequisites
- Docker Desktop installed → [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop)
- Your Jira API token → [id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens)

---

### Step 1 — Unzip the project
```bash
unzip jira-intelligence-dashboard.zip
cd jira-intelligence-dashboard
```

### Step 2 — Create your `.env` file
```bash
cp .env.example .env
```

Open `.env` and fill in your details:
```env
JIRA_URL=https://yourcompany.atlassian.net
JIRA_EMAIL=your.email@company.com
JIRA_API_TOKEN=your_api_token_here
JIRA_PROJECTS=P2,VPD,UAMRQ
JIRA_MAX_RESULTS=500
```
> **Leave `.env` values blank** if you'd rather enter them in the browser UI.

### Step 3 — Build and start
```bash
docker compose up --build
```

You'll see:
```
 ✔ Container jira-intelligence-dashboard  Started
```

### Step 4 — Open in browser
```
http://localhost:8085
```

### Step 5 — Connect to Jira
Fill in your Jira URL, email and API token → click **Connect & Load Dashboard →**

### Step 6 — Share with your team
Share this URL with everyone:
```
http://YOUR-SERVER-IP:8085/dashboard
```
They click **↺ Refresh** in the top nav to pull the latest data from Jira anytime.

---

## Docker Commands

| Task | Command |
|------|---------|
| Start | `docker compose up -d` |
| Stop | `docker compose down` |
| Restart | `docker compose restart` |
| Rebuild after changes | `docker compose up --build` |
| View logs | `docker compose logs -f` |
| Check health | `curl http://localhost:8085/status` |

---

## URLs

| URL | Purpose |
|-----|---------|
| `http://localhost:8085` | Connect / Settings page |
| `http://localhost:8085/dashboard` | Live dashboard (share this) |
| `http://localhost:8085/people` | People Intelligence dashboard |
| `http://localhost:8085/refresh` | Force-refresh data from Jira |
| `http://localhost:8085/status` | JSON health/status check |

---

## Project Files

```
jira-intelligence-dashboard/
├── app.py                ← Flask app — all logic, Jira API, dashboard HTML
├── Dockerfile            ← Container definition
├── docker-compose.yml    ← Single-command deployment
├── requirements.txt      ← Flask + Gunicorn
├── .env.example          ← Copy to .env and fill in credentials
├── .dockerignore         ← Keeps .env out of the image
├── logo.png              ← App logo (embedded in all pages)
├── atlassian-connect.json ← Atlassian Marketplace app descriptor
├── privacy-policy.md     ← Privacy policy (required for Marketplace)
└── README.md             ← This file
```

---

## Getting a Jira API Token

1. Go to **[id.atlassian.com → Security → API Tokens](https://id.atlassian.com/manage-profile/security/api-tokens)**
2. Click **Create API token**
3. Name it e.g. `Jira Intelligence Dashboard`
4. Copy the token — paste it into the `.env` file or the UI

---

## Security Notes

- Your API token is stored **only in server memory** and your `.env` file
- It is **never** sent to or visible in the browser
- The `.dockerignore` ensures `.env` is never baked into the Docker image
- Change `SECRET_KEY` in `.env` to a unique random string before deploying

---

## Deploy to a Server (so your team can always access it)

### Option A — Any Linux Server / VM
```bash
# Install Docker on Ubuntu
curl -fsSL https://get.docker.com | sh

# Upload your project files
scp -r jira-intelligence-dashboard/ user@your-server:/opt/jira-dashboard/

# SSH in and start
ssh user@your-server
cd /opt/jira-dashboard
docker compose up -d

# Share this URL with your team
echo "http://$(curl -s ifconfig.me):8085/dashboard"
```

### Option B — Run behind nginx (for HTTPS / custom domain)
Add this to your nginx config:
```nginx
server {
    listen 80;
    server_name dashboard.yourcompany.com;
    location / {
        proxy_pass http://localhost:8085;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `Port 8085 already in use` | Change `"8085:8080"` to `"8086:8080"` in docker-compose.yml |
| `Connection refused` | Make sure Docker Desktop is running |
| `401 Unauthorized` | Check your email and API token |
| `404 Not Found` | Check your Jira URL (no trailing slash) |
| Team can't access URL | Open port 8085 in your firewall / security group |
| Dashboard blank | Click ↺ Refresh — data may still be loading |

---

## How It Works

1. **Connect page** — enter Jira credentials once
2. **Jira API** — fetches all issues with pagination (handles 5000+ issues)
3. **Processing** — computes KPIs, lanes, bugs, assignees, timelines
4. **Dashboard** — 5-tab HTML rendered server-side, served to all team members
5. **Refresh** — any team member clicks ↺ to re-fetch fresh data from Jira

---

*© 2026 Arunakumar Tavva. All rights reserved.*
