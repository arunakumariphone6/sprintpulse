# Publishing SprintPulse for Jira — Complete First-Timer Guide

**Author:** Arunakumar Tavva
**App Name:** SprintPulse for Jira
**App Key:** `com.arunakumartavva.sprintpulse`
**Version:** 2.0

---

## Your App Identity at a Glance

| Item | Value |
|------|-------|
| **App Name** | SprintPulse for Jira |
| **Tagline** | Real-time Sprint Intelligence. Live for your whole team. |
| **App Key** | `com.arunakumartavva.sprintpulse` |
| **Recommended Domain** | `sprintpulse.dev` *(register at Namecheap/GoDaddy ~$12/yr)* |
| **Alternative Domain** |   `sprintpulse.dev` |
| **Vendor Name** | Arunakumar Tavva |
| **Category** | Reporting & Charts → Jira Software |
| **Pricing** | Free (start free — you can add paid tiers later) |
| **Hosting type** | Self-Hosted (customers deploy Docker on their own server) |

> **Why "SprintPulse for Jira" and not "Jira Intelligence Dashboard"?**
> Atlassian's trademark policy (updated 2019) does **not** allow app names that begin with
> "Jira", "Atlassian", or "Confluence". Putting "for Jira" at the end is the correct and
> approved format. "SprintPulse" is memorable, unique, and domain-available.

---

## Assets Ready in Your Project

| Asset | File | Status |
|-------|------|--------|
| App icon (512×512) | `app-icon-512.png` | ✅ Ready |
| Marketplace banner | `marketplace-banner.png` | ✅ Ready |
| App descriptor | `atlassian-connect.json` | ⚠️ Needs your real URL |
| Privacy policy | `privacy-policy.md` | ⚠️ Needs your email + hosting |
| Main README | `README.md` | ✅ Ready |

---

## PHASE 1 — Before You Touch the Marketplace Portal

Complete these 4 things first. Do not open the portal until these are done.

---

### 1-A. Register Your Domain (10 minutes)

1. Go to **[namecheap.com](https://www.namecheap.com)** (recommended — cheap + free WhoisGuard privacy)
2. Search for `sprintpulse.io` in the search bar
3. If available → Add to cart → Checkout (~$12–15/year)
4. During checkout: enable **WhoisGuard Privacy Protection** (free on Namecheap)
5. Complete purchase — keep the login credentials safe

> If `sprintpulse.io` is taken, try: `sprintpulse.app`, `sprintpulse.dev`, `sprintpulse.co`

---

### 1-B. Host the App on a Public HTTPS Server (30–45 minutes)

The Atlassian Marketplace **requires** your app to be live at a real HTTPS URL before you can submit. The easiest free option is **Railway**.

#### Option 1 — Railway (Free tier, recommended for first-timer)

1. Go to **[railway.app](https://railway.app)** → Sign up (use your GitHub account)
2. Click **New Project** → **Deploy from GitHub repo**
   - If your code isn't on GitHub yet: go to [github.com](https://github.com) → New repository → name it `sprintpulse` → upload all project files
3. Railway detects the `Dockerfile` automatically
4. Set environment variables in Railway dashboard:
   - `SECRET_KEY` → any long random string (e.g. `SprintPulse2026SecureKey!XYZ`)
   - `JIRA_URL` → (leave blank — users enter in UI)
   - `JIRA_EMAIL` → (leave blank)
   - `JIRA_API_TOKEN` → (leave blank)
5. Click **Deploy** → Railway gives you a URL like `https://sprintpulse-production.up.railway.app`
6. ✅ Your app is now live at a public HTTPS URL — copy this URL

#### Option 2 — Render (Free tier)

1. Go to **[render.com](https://render.com)** → Sign up
2. New → Web Service → Connect GitHub repo
3. Runtime: **Docker**
4. Set `SECRET_KEY` environment variable
5. Deploy → Render gives you `https://sprintpulse.onrender.com`

> **Custom domain (optional but professional):** In Railway or Render, go to Settings → Custom Domain → enter `app.sprintpulse.io` → follow DNS instructions (adds a CNAME record in Namecheap dashboard).

---

### 1-C. Update `atlassian-connect.json` with Your Real URL

Open `atlassian-connect.json` and replace the placeholder:

```json
"baseUrl": "https://sprintpulse-production.up.railway.app",
"vendor": {
  "name": "Arunakumar Tavva",
  "url": "https://sprintpulse.io"
},
"links": {
  "self": "https://sprintpulse-production.up.railway.app/atlassian-connect.json",
  "homepage": "https://sprintpulse-production.up.railway.app"
}
```

Then add this route to `app.py` (add it just before the `if __name__ == "__main__":` block):

```python
@app.route('/atlassian-connect.json')
def serve_atlassian_connect():
    """Serve the Atlassian Connect descriptor."""
    import json as _json
    descriptor_path = os.path.join(os.path.dirname(__file__), 'atlassian-connect.json')
    with open(descriptor_path) as f:
        return app.response_class(f.read(), mimetype='application/json')

@app.route('/atlassian/installed', methods=['POST'])
def atlassian_installed():
    """Lifecycle hook — called when app is installed on an Atlassian instance."""
    return '', 204

@app.route('/atlassian/uninstalled', methods=['POST'])
def atlassian_uninstalled():
    """Lifecycle hook — called when app is uninstalled."""
    return '', 204
```

Redeploy after making this change. Then verify it works:
```bash
curl https://YOUR-URL/atlassian-connect.json
```
It must return JSON with HTTP 200. ✅

---

### 1-D. Host Your Privacy Policy at a Public URL (5 minutes)

1. Go to **[gist.github.com](https://gist.github.com)**
2. Log in with your GitHub account
3. Paste the entire contents of `privacy-policy.md`
4. **Important:** Add your email in the Contact section (line that says `*(your contact email*)`)
5. Set to **Public** gist
6. Click **Create public gist**
7. Copy the URL — it will look like `https://gist.github.com/arunakumartavva/abc123`

> Alternatively, upload `privacy-policy.md` to Google Drive → share as "Anyone with link" → right-click → Open in Docs → File → Download as PDF → re-upload and share PDF link.

---

## PHASE 2 — Create Your Atlassian Vendor Account

---

### Step 1 — Sign Into the Marketplace Vendor Portal

1. Go to **[marketplace.atlassian.com](https://marketplace.atlassian.com)**
2. Click **Sign in** (top right)
3. Use your existing Atlassian account (the one you use for Jira) — or create one free at [id.atlassian.com](https://id.atlassian.com)
4. After sign-in, click your avatar → **Manage listings** — OR go directly to:
   **[marketplace.atlassian.com/manage](https://marketplace.atlassian.com/manage)**

---

### Step 2 — Register as a Vendor (Individual)

1. At the Manage portal, you'll see a prompt: **"Become a vendor"** — click it
2. Fill in the vendor registration form:

| Field | What to Enter |
|-------|--------------|
| **Vendor/company name** | `Arunakumar Tavva` |
| **Vendor website** | `https://sprintpulse.io` (or your GitHub profile if domain not ready yet) |
| **Support URL** | `https://github.com/arunakumartavva/sprintpulse/issues` (create this GitHub repo) |
| **Contact email** | Your email address |
| **Country** | Your country |

3. Read and accept the **Atlassian Marketplace Vendor Agreement** — click the checkbox
4. Click **Save** / **Register**
5. You are now a registered vendor ✅

---

### Step 3 — Prepare Your App Icon Files

The icon at `app-icon-512.png` is already in your project folder. You need to resize it:

#### Resize using free online tool (no software needed):
1. Go to **[squoosh.app](https://squoosh.app)** (Google's free image tool)
2. Drag and drop `app-icon-512.png`
3. On the right panel, click **Resize** → set Width: `75`, Height: `75`
4. Set format to **PNG**
5. Click **Save** → save as `app-icon-75.png`
6. Repeat with size `144×144` → save as `app-icon-144.png`

> Atlassian requires:
> - **75 × 75 px PNG** — shown in search results
> - **144 × 144 px PNG** — shown on listing page (Retina displays)

---

### Step 4 — Take Screenshots of Your Running App

You need **at least 3 screenshots**. Recommended size: **1280 × 800 px**.

#### How to take perfect screenshots:
1. Start your app: `docker compose up -d`
2. Open Chrome / Edge → go to `http://localhost:8085/dashboard`
3. Press **F12** to open DevTools
4. Press **Ctrl + Shift + P** → type `screenshot` → select **"Capture full size screenshot"**
5. It saves automatically to your Downloads folder

#### Which screens to capture:
| Screenshot # | URL | What to show |
|-------------|-----|-------------|
| 1 (Hero) | `/dashboard` | Full board with issues loaded — all 5 tabs visible |
| 2 | `/people` | People Intelligence with leaderboard and badges |
| 3 | `/` (Connect page) | Clean connect form UI |
| 4 (bonus) | `/dashboard` → Sprint KPI tab | KPI cards and sprint metrics |
| 5 (bonus) | `/dashboard` → Timeline tab | Release timeline view |

> **Pro tip:** Before screenshotting, connect to a Jira test instance with real data so the dashboard looks full and populated.

---

## PHASE 3 — Create the Marketplace Listing

---

### Step 5 — Create a New App Listing

1. In the Vendor Portal → **My Apps** → click **Create app**
2. Choose: **Connect app** (this is the correct type for our `atlassian-connect.json` descriptor)
3. You'll see a multi-tab form. Fill in each tab:

---

#### TAB: Overview

| Field | Value |
|-------|-------|
| **App name** | `SprintPulse for Jira` |
| **App key** | `com.arunakumartavva.sprintpulse` |
| **Tagline** (80 chars) | `Real-time Sprint Intelligence. Live for your whole team.` |
| **Summary** (160 chars) | `Live Jira dashboard with sprint KPIs, People Intelligence leaderboard & CEO reporting. Deploy with Docker. Share with your team instantly.` |
| **Categories** | Reporting & Charts, Project Management |
| **Hosting** | Cloud (for Connect apps) |
| **Jira product** | Jira Software |

---

#### TAB: Details (Long Description)

Copy-paste this entire block into the long description field:

```
## SprintPulse for Jira — Real-time Sprint Intelligence

SprintPulse is a self-hosted real-time Jira dashboard that gives your team and management instant visibility into sprint progress, team productivity, and project health — with zero per-seat costs.

### 🚀 Core Features

**Live Board Dashboard**
- 5-tab board: Kanban view, Sprint KPIs, Bug Tracker, Assignee breakdown, Release timeline
- Real-time data refresh — any team member clicks ↺ to pull latest Jira data
- Handles 5,000+ issues with background pagination

**People Intelligence Dashboard**
- Individual productivity scores per team member
- Gamified leaderboard with achievement badges: 🏆 MVP, 🐛 Bug Slayer, ⚡ Speed Demon
- Performance tier distribution (Exceptional / Good / Needs Improvement)
- Ready-to-share CEO Action Plan with recommended management actions

**Sprint KPI Engine**
- Velocity, cycle time, completion rate, bug ratio
- Overdue issue tracker with assignee drill-down
- Automated release timeline from issue resolution dates

### ⚙️ How It Works

1. Deploy with one command: `docker compose up --build`
2. Enter your Jira URL + API token on the connect page (or pre-fill via .env)
3. Share the dashboard URL with your entire team — no per-user login required
4. Team members click ↺ Refresh to get the latest data anytime

### ✅ Compatibility
- Jira Cloud (API v3)
- Jira Server / Data Center (API v2)
- Auto-detects endpoint version

### 🔒 Security
- Read-only Jira API access — no data created, modified, or deleted
- API token stored only in server memory — never exposed in browser
- Self-hosted: your data stays on your infrastructure, never sent anywhere

### 🐳 Requirements
- Docker Desktop (for self-hosted deployment)
- Jira API token (free from id.atlassian.com)
- Read access to one or more Jira projects

---
Authored by Arunakumar Tavva
```

---

#### TAB: Vendor

| Field | Value |
|-------|-------|
| **Vendor name** | `Arunakumar Tavva` |
| **Vendor URL** | `https://sprintpulse.io` |
| **Support URL** | Your GitHub Issues URL or email |
| **Privacy policy URL** | Your hosted gist/page URL from Step 1-D |
| **EULA** | Select "Atlassian Standard EULA" (simplest for first-timers) |

---

#### TAB: Pricing

| Field | Value |
|-------|-------|
| **Pricing model** | Free |
| **License type** | Free |

> Start free. Once you have reviews and users, you can add paid tiers from the vendor portal later.

---

### Step 6 — Upload Assets

In the **Media** or **Listing assets** section:

1. **App icon (75×75):** Upload `app-icon-75.png`
2. **App icon (144×144):** Upload `app-icon-144.png`
3. **Screenshots:** Upload 3–5 screenshots in order (most impressive first)
4. **Banner image:** Upload `marketplace-banner.png` if there's a banner slot

---

### Step 7 — Add the App Version / Descriptor

1. Go to the **Versions** tab in your listing
2. Click **Add version**
3. Fill in:

| Field | Value |
|-------|-------|
| **Version number** | `2.0` |
| **Build number** | `200` |
| **Descriptor URL** | `https://YOUR-DEPLOYMENT-URL/atlassian-connect.json` |
| **Release notes** | `Initial release. Real-time Jira dashboard with board view, sprint KPIs, and People Intelligence.` |

4. Click **Validate descriptor** — Atlassian fetches your JSON and checks it
5. Common validation errors and fixes:

| Error | Fix |
|-------|-----|
| `Could not fetch descriptor` | Make sure your app is running and URL is correct |
| `Invalid JSON` | Run `curl YOUR-URL/atlassian-connect.json` and check the response |
| `baseUrl mismatch` | The `baseUrl` in JSON must exactly match the URL you entered |
| `key already taken` | Change the `key` in JSON to something more unique |

6. Once validation passes → click **Save version** ✅

---

### Step 8 — Review Checklist Before Submitting

Go through this checklist. Everything must be ticked before you hit Submit:

- [ ] App is live at a public HTTPS URL
- [ ] `https://YOUR-URL/atlassian-connect.json` returns valid JSON
- [ ] App name does NOT start with "Jira", "Atlassian", or "Confluence"
- [ ] App icon uploaded (75×75 PNG)
- [ ] At least 3 screenshots uploaded
- [ ] Long description filled in (minimum 200 characters)
- [ ] Privacy policy URL is live and accessible
- [ ] Support URL is live (GitHub Issues page or email)
- [ ] Version added and descriptor validated without errors
- [ ] Pricing model set
- [ ] Vendor profile complete (name, URL, contact email)

---

### Step 9 — Submit for Review

1. Click **Submit for review** (or "Submit listing" — button text varies)
2. A confirmation dialog appears — click **Confirm**
3. You will receive a confirmation email from Atlassian
4. **Review timeline:** 1–5 business days (often faster for free apps)

#### What Atlassian's team checks:
| Control | Your App Status |
|---------|----------------|
| No hardcoded credentials in source | ✅ All via env vars |
| Read-only API scopes | ✅ `"scopes": ["READ"]` |
| Privacy policy present | ✅ Created |
| No data sent to third parties | ✅ Self-hosted only |
| HTTPS required | ✅ Deployed on Railway/Render |
| Trademark compliance (no "Jira" at start) | ✅ "SprintPulse for Jira" |
| App descriptor valid | ✅ Validated in Step 7 |
| OCI Docker labels | ✅ In Dockerfile |

---

### Step 10 — After Approval

Once Atlassian approves:

1. Your listing goes live at `https://marketplace.atlassian.com/apps/YOUR-APP-ID`
2. Share it everywhere:
   - LinkedIn post: "Excited to announce SprintPulse for Jira is now live on the Atlassian Marketplace! 🚀 [link]"
   - Atlassian Community forums: [community.atlassian.com](https://community.atlassian.com) → Apps & Integrations
   - Reddit: r/jira, r/agile, r/scrum
3. Respond to reviews promptly (within 24 hours ideally)
4. Monitor installs from your vendor dashboard

---

## Quick Reference — Domain & URL Recommendations

| Purpose | Recommended URL |
|---------|----------------|
| Primary domain | `sprintpulse.io` |
| Alternative | `sprintpulse.app` or `sprintpulse.dev` |
| App hosting (free) | `https://sprintpulse-production.up.railway.app` |
| Privacy policy | GitHub Gist (public) |
| Support | `https://github.com/arunakumartavva/sprintpulse/issues` |
| Vendor profile | `https://sprintpulse.io` |

---

## Useful Links — Bookmark These

| Resource | URL |
|----------|-----|
| Marketplace Vendor Portal | [marketplace.atlassian.com/manage](https://marketplace.atlassian.com/manage) |
| Atlassian Account (sign in) | [id.atlassian.com](https://id.atlassian.com) |
| Connect App Docs | [developer.atlassian.com/cloud/jira/platform](https://developer.atlassian.com/cloud/jira/platform/integrating-with-jira-cloud/) |
| Naming Guidelines | [developer.atlassian.com/platform/marketplace/atlassian-trademark-guidelines](https://developer.atlassian.com/platform/marketplace/atlassian-trademark-guidelines/) |
| Security Requirements | [developer.atlassian.com/platform/marketplace/security-requirements](https://developer.atlassian.com/platform/marketplace/security-requirements/) |
| Free image resizer | [squoosh.app](https://squoosh.app) |
| Free domain registrar | [namecheap.com](https://www.namecheap.com) |
| Free app hosting | [railway.app](https://railway.app) |
| Privacy policy hosting | [gist.github.com](https://gist.github.com) |
| Atlassian Community | [community.atlassian.com](https://community.atlassian.com) |

---

## Files in Your Project — Final State

```
sprintpulse-for-jira/
├── app.py                    ← Flask app (all branding updated)
├── Dockerfile                ← OCI labels, maintainer = Arunakumar Tavva
├── docker-compose.yml        ← Container: jira-intelligence-dashboard
├── requirements.txt          ← flask + gunicorn
├── .env.example              ← Template (no real credentials)
├── .env                      ← Your live config (NEVER commit this)
├── .dockerignore             ← Keeps .env out of image
├── atlassian-connect.json    ← ⚠️ Update baseUrl before submitting
├── privacy-policy.md         ← ⚠️ Add email + host publicly
├── app-icon-512.png          ← ✅ App icon (resize to 75px and 144px)
├── marketplace-banner.png    ← ✅ Marketplace banner image
├── logo.png                  ← App logo used inside the dashboard
├── README.md                 ← Updated documentation
├── runlocal.md               ← Windows local run guide
└── MARKETPLACE-PUBLISH.md    ← This guide
```

---

*© 2026 Arunakumar Tavva. All rights reserved. — Let's make it proud.*
