"""
Jira Intelligence Dashboard
──────────────────────────────────────────
Real-time Jira API integration.
Connect once, share the URL with your team.
They hit Refresh to get the latest data.

Author: Arunakumar Tavva
Copyright © 2026 Arunakumar Tavva. All rights reserved.

docker compose up --build
Open: http://localhost:8080
"""

import os, base64, collections, json, time, threading, logging
from datetime import datetime
from functools import wraps
from flask import (Flask, request, render_template_string,
                   redirect, url_for, session, jsonify)
import urllib.request, urllib.error, urllib.parse

# ─────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "jira-intelligence-dashboard-secret-2026-change-me")

# ── In-memory cache ────────────────────────────────────────────
_cache = {
    "data":        None,      # processed data dict
    "raw_issues":  None,      # raw Jira issues list
    "fetched_at":  None,      # datetime of last fetch
    "status":      "idle",    # idle | fetching | ready | error
    "error_msg":   "",
    "config":      {          # persisted connection config
        "jira_url":   os.environ.get("JIRA_URL", ""),
        "email":      os.environ.get("JIRA_EMAIL", ""),
        "api_token":  os.environ.get("JIRA_API_TOKEN", ""),
        "projects":   os.environ.get("JIRA_PROJECTS", ""),   # e.g. "P2,VPD,UAMRQ"
        "max_results": int(os.environ.get("JIRA_MAX_RESULTS", "500")),
        "api_version": "3",   # "3" = Jira Cloud, "2" = Jira Server/Data Center
    }
}
_lock = threading.Lock()


# ─────────────────────────────────────────────────────────────
#  JIRA API FETCHER
# ─────────────────────────────────────────────────────────────

JIRA_FIELDS = [
    "summary", "status", "issuetype", "priority", "assignee",
    "reporter", "project", "created", "updated", "resolutiondate",
    "labels", "customfield_10014",  # Epic Link
    "customfield_10020",            # Sprint
    "customfield_10016",            # Story Points
    "customfield_10031",            # Testing Phase (common)
]

def _auth_header(email, api_token):
    creds = base64.b64encode(f"{email}:{api_token}".encode()).decode()
    return {"Authorization": f"Basic {creds}",
            "Content-Type":  "application/json",
            "Accept":        "application/json"}

def _jira_get(jira_url, path, email, api_token):
    """Single Jira REST API GET call. Returns parsed JSON."""
    url = jira_url.rstrip("/") + path
    req = urllib.request.Request(url, headers=_auth_header(email, api_token))
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())

def _jira_post(jira_url, path, payload, email, api_token):
    """Single Jira REST API POST call with JSON body. Returns parsed JSON."""
    url  = jira_url.rstrip("/") + path
    body = json.dumps(payload).encode()
    req  = urllib.request.Request(url, data=body, headers=_auth_header(email, api_token),
                                   method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())

def detect_api_version(jira_url, email, api_token):
    """
    Auto-detect whether this is Jira Cloud (API v3) or Jira Server/DC (API v2).
    Only tests the /myself endpoint — the search endpoint is probed separately
    in fetch_jira_issues to handle the new POST /search/jql vs GET /search split.
    Returns "3" for Cloud, "2" for Server/Data Center.
    Raises urllib.error.HTTPError if credentials are wrong (401/403).
    """
    for version in ("3", "2"):
        try:
            _jira_get(jira_url, f"/rest/api/{version}/myself", email, api_token)
            log.info(f"Detected Jira API version: {version}")
            return version
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise   # bad credentials — stop immediately
            if version == "2":
                raise   # both versions failed
            log.info(f"/myself v{version} returned {e.code}, trying v2...")
    return "2"

def fetch_jira_issues(config):
    """
    Fetch ALL issues using the best available search endpoint, tried in order:
      1. POST /rest/api/3/search/jql  — new Jira Cloud (cursor pagination)
      2. GET  /rest/api/3/search      — classic Jira Cloud
      3. GET  /rest/api/2/search      — Jira Server / Data Center
    Returns list of raw Jira issue dicts.
    """
    jira_url    = config["jira_url"].rstrip("/")
    email       = config["email"]
    token       = config["api_token"]
    projects    = config["projects"]
    max_total   = config.get("max_results", 500)
    api_version = config.get("api_version", "3")

    if projects.strip():
        proj_list = ", ".join(f'"{p.strip()}"' for p in projects.split(","))
        jql = f"project in ({proj_list}) ORDER BY created DESC"
    else:
        jql = "ORDER BY created DESC"

    # ── Strategy 1: POST /rest/api/3/search/jql (cursor pagination) ──────────
    def _fetch_post_jql():
        issues_out = []
        next_token = None
        while True:
            payload = {
                "jql":        jql,
                "maxResults": min(100, max_total - len(issues_out)),
                "fields":     JIRA_FIELDS,
            }
            if next_token:
                payload["nextPageToken"] = next_token
            data       = _jira_post(jira_url, "/rest/api/3/search/jql", payload, email, token)
            page       = data.get("issues", [])
            issues_out.extend(page)
            next_token = data.get("nextPageToken")
            log.info(f"  POST /search/jql — fetched {len(issues_out)} so far")
            if not next_token or not page or len(issues_out) >= max_total:
                break
            time.sleep(0.1)
        return issues_out

    # ── Strategy 2/3: GET /rest/api/{ver}/search (offset pagination) ─────────
    def _fetch_get(ver):
        issues_out = []
        start_at   = 0
        while True:
            params = urllib.parse.urlencode({
                "jql":        jql,
                "startAt":    start_at,
                "maxResults": min(100, max_total - len(issues_out)),
                "fields":     ",".join(JIRA_FIELDS),
            })
            data   = _jira_get(jira_url, f"/rest/api/{ver}/search?{params}", email, token)
            page   = data.get("issues", [])
            issues_out.extend(page)
            total  = data.get("total", 0)
            log.info(f"  GET /search v{ver} — fetched {len(issues_out)}/{total}")
            if len(issues_out) >= total or len(issues_out) >= max_total or not page:
                break
            start_at += len(page)
            time.sleep(0.1)
        return issues_out

    strategies = [
        ("POST /search/jql v3",  _fetch_post_jql),
        (f"GET  /search v{api_version}", lambda: _fetch_get(api_version)),
        ("GET  /search v2",       lambda: _fetch_get("2")),
        ("GET  /search v3",       lambda: _fetch_get("3")),
    ]
    last_err = None
    for label, fn in strategies:
        try:
            log.info(f"Trying search strategy: {label}")
            result = fn()
            log.info(f"Fetch complete via {label} — {len(result)} issues")
            return result
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise
            log.warning(f"  {label} → HTTP {e.code}, trying next strategy…")
            last_err = e
        except Exception as e:
            log.warning(f"  {label} → {e}, trying next strategy…")
            last_err = e
    raise last_err


def normalize_issues(raw_issues):
    """
    Convert raw Jira API issues → list of dicts with the same
    keys our CSV processor expects, so build_dashboard_html() works identically.
    """
    rows = []
    for i in raw_issues:
        f  = i.get("fields", {})
        # Status
        status_obj  = f.get("status", {})
        status_name = status_obj.get("name", "")
        status_cat  = status_obj.get("statusCategory", {}).get("name", "")
        # Priority
        prio_obj  = f.get("priority") or {}
        prio_name = prio_obj.get("name", "")
        # Assignee
        asgn_obj  = f.get("assignee") or {}
        assignee  = asgn_obj.get("displayName", "")
        # Reporter
        rep_obj  = f.get("reporter") or {}
        reporter = rep_obj.get("displayName", "")
        # Project
        proj_obj   = f.get("project", {})
        proj_key   = proj_obj.get("key", "")
        proj_name  = proj_obj.get("name", "")
        # Issue type
        itype_obj  = f.get("issuetype", {})
        issue_type = itype_obj.get("name", "")
        # Dates
        created    = f.get("created", "")[:10] or ""
        updated    = f.get("updated", "")[:10] or ""
        resolved   = f.get("resolutiondate", "") or ""
        if resolved: resolved = resolved[:10]
        # Testing phase (try common custom field names)
        testing_phase = ""
        for cf in ["customfield_10031","customfield_10050","customfield_10100"]:
            val = f.get(cf)
            if val and isinstance(val, dict):
                testing_phase = val.get("value","")
            elif val and isinstance(val, str):
                testing_phase = val
            if testing_phase:
                break
        # Labels
        labels = " ".join(f.get("labels", []))

        rows.append({
            "Issue key":   i.get("key", ""),
            "Summary":     f.get("summary", ""),
            "Issue Type":  issue_type,
            "Status":      status_name,
            "Status Category": status_cat,
            "Priority":    prio_name,
            "Assignee":    assignee,
            "Reporter":    reporter,
            "Project key": proj_key,
            "Project name": proj_name,
            "Created":     created,
            "Updated":     updated,
            "Resolved":    resolved,
            "Labels":      labels,
            "Custom field (Testing Phase)": testing_phase,
            "Custom field (Epic Name)": "",
            "Custom field (Application Version)": "",
        })
    return rows


def background_fetch(config):
    """Run in a thread: fetch Jira → process → store in cache."""
    with _lock:
        _cache["status"]    = "fetching"
        _cache["error_msg"] = ""

    try:
        # Auto-detect API version (Cloud=v3, Server/DC=v2) if not already known
        if not config.get("api_version"):
            config["api_version"] = "3"
        try:
            detected = detect_api_version(
                config["jira_url"], config["email"], config["api_token"])
            config["api_version"] = detected
        except Exception as ve:
            log.warning(f"API version detection failed, using {config['api_version']}: {ve}")

        log.info(f"Starting Jira fetch (API v{config['api_version']})…")
        raw = fetch_jira_issues(config)
        rows = normalize_issues(raw)
        data = process_data(rows)

        with _lock:
            _cache["raw_issues"] = raw
            _cache["data"]       = data
            _cache["fetched_at"] = datetime.now()
            _cache["status"]     = "ready"
            _cache["config"]     = config
        log.info(f"Fetch complete — {len(rows)} issues loaded")

    except Exception as e:
        msg = str(e)
        log.error(f"Jira fetch failed: {msg}")
        with _lock:
            _cache["status"]    = "error"
            _cache["error_msg"] = msg


# ─────────────────────────────────────────────────────────────
#  DATA PROCESSING  (identical logic to CSV version)
# ─────────────────────────────────────────────────────────────

def parse_date(s):
    s = (s or "").strip()[:10]
    for fmt in ["%Y-%m-%d", "%d/%b/%y", "%d/%m/%Y"]:
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            pass
    return None


def process_data(rows):
    """Core data processing — works for both CSV rows and API-normalized rows."""
    if not rows:
        return None

    lane_map = {
        "To Do":"To Do","New Task":"To Do","Backlog":"Backlog",
        "In Progress":"In Progress","Review":"In Progress",
        "Ready For QA":"Review / QA","In UAT":"Review / QA",
        "Closed":"Done","Issue Closed":"Done","Issue Resolved":"Done",
        "New Bug":"New Bug","On Hold":"On Hold","Open":"In Progress",
        "CTO Approval":"Approval","CMTApproval":"Approval",
        "AWAITING FOR CEO APPROVAL":"Approval",
        "Pending for Implementation":"Approval",
        "Awaiting For Head of Information Security Approval":"Approval",
        "Initiate Visa/Work Permit":"Approval",
        "Defect Triage":"Backlog","Deferred":"Backlog",
    }
    OPEN_STATUSES = {"New Task","New Bug","To Do","In Progress","On Hold",
                     "Backlog","Ready For QA","In UAT","Review","Open"}

    total = len(rows)
    status_cat   = collections.Counter(r.get("Status Category","") for r in rows)
    issue_types  = collections.Counter(r.get("Issue Type","") for r in rows)
    statuses     = collections.Counter(r.get("Status","") for r in rows)
    priorities   = collections.Counter(r.get("Priority","") for r in rows)
    projects     = collections.Counter(r.get("Project name","") for r in rows)

    done_count   = status_cat.get("Done",0)
    inprog_count = status_cat.get("In Progress",0)
    todo_count   = status_cat.get("To Do",0)

    bugs         = [r for r in rows if r.get("Issue Type")=="Bug"]
    open_bugs    = [b for b in bugs if b.get("Status Category")!="Done"]
    closed_bugs  = [b for b in bugs if b.get("Status Category")=="Done"]
    bug_closure  = round(len(closed_bugs)/len(bugs)*100,1) if bugs else 0

    bug_statuses   = collections.Counter(b.get("Status","") for b in bugs)
    bug_priorities = collections.Counter(b.get("Priority","") for b in bugs)

    testing_phases = collections.Counter(
        r.get("Custom field (Testing Phase)","").strip() or "Unassigned"
        for r in rows if r.get("Issue Type")=="Test"
    )

    approval_statuses_set = {
        "CTO Approval","CMTApproval","AWAITING FOR CEO APPROVAL",
        "Pending for Implementation",
        "Awaiting For Head of Information Security Approval",
        "Initiate Visa/Work Permit",
    }
    approvals = [r for r in rows if r.get("Status","") in approval_statuses_set]

    board_lanes = collections.defaultdict(list)
    for r in rows:
        lane = lane_map.get(r.get("Status",""), r.get("Status","Other"))
        board_lanes[lane].append({
            "key":      r.get("Issue key",""),
            "summary":  r.get("Summary","")[:75],
            "type":     r.get("Issue Type",""),
            "priority": r.get("Priority",""),
            "assignee": r.get("Assignee","") or "—",
            "status":   r.get("Status",""),
            "project":  r.get("Project key",""),
        })

    assignee_stats = collections.defaultdict(lambda:{"total":0,"done":0,"open":0,"bugs":0})
    for r in rows:
        a = r.get("Assignee","") or "Unassigned"
        assignee_stats[a]["total"] += 1
        if r.get("Status Category")=="Done": assignee_stats[a]["done"] += 1
        else:                                assignee_stats[a]["open"] += 1
        if r.get("Issue Type")=="Bug":       assignee_stats[a]["bugs"] += 1
    for a in assignee_stats:
        t = assignee_stats[a]["total"]
        assignee_stats[a]["rate"] = round(assignee_stats[a]["done"]/t*100,1) if t else 0.0

    reporters = collections.Counter(r.get("Reporter","") or "Unknown" for r in rows)

    by_date = collections.defaultdict(int)
    for r in rows:
        d = parse_date(r.get("Created",""))
        if d:
            by_date[d.strftime("%d %b")] += 1

    proj_stats = {}
    for proj in projects:
        pr = [r for r in rows if r.get("Project name")==proj]
        pd_done  = sum(1 for r in pr if r.get("Status Category")=="Done")
        pd_total = len(pr)
        proj_stats[proj] = {
            "total":       pd_total,
            "done":        pd_done,
            "in_progress": sum(1 for r in pr if r.get("Status Category")=="In Progress"),
            "todo":        sum(1 for r in pr if r.get("Status Category")=="To Do"),
            "open_bugs":   sum(1 for r in pr if r.get("Issue Type")=="Bug" and r.get("Status Category")!="Done"),
            "pct":         round(pd_done/pd_total*100,1) if pd_total else 0,
            "key":         pr[0].get("Project key","") if pr else "",
        }

    open_issues  = [r for r in rows if r.get("Status","") in OPEN_STATUSES]
    assignee_open = collections.Counter(r.get("Assignee","") or "Unassigned" for r in open_issues)
    on_hold       = [r for r in rows if r.get("Status")=="On Hold"]
    in_progress_items = [r for r in rows if r.get("Status") in {"In Progress","Review","In UAT","Ready For QA"}]
    done_items    = [r for r in rows if r.get("Status Category")=="Done"]
    unassigned    = sum(1 for r in rows if not r.get("Assignee","").strip())

    return {
        "rows": rows, "bugs": bugs, "open_bugs": open_bugs,
        "closed_bugs": closed_bugs, "on_hold": on_hold,
        "in_progress_items": in_progress_items, "done_items": done_items,
        "approvals": approvals,
        "total": total, "done_count": done_count, "open_count": total-done_count,
        "inprog_count": inprog_count, "todo_count": todo_count,
        "bug_count": len(bugs), "open_bug_count": len(open_bugs),
        "closed_bug_count": len(closed_bugs), "bug_closure": bug_closure,
        "approval_count": len(approvals), "unassigned": unassigned,
        "status_cat": dict(status_cat), "issue_types": dict(issue_types),
        "statuses": dict(statuses), "priorities": dict(priorities),
        "projects": dict(projects), "bug_statuses": dict(bug_statuses),
        "bug_priorities": dict(bug_priorities), "testing_phases": dict(testing_phases),
        "by_date": dict(sorted(by_date.items())),
        "proj_stats": proj_stats, "assignee_stats": dict(assignee_stats),
        "assignee_open": dict(assignee_open.most_common(12)),
        "reporters": dict(reporters.most_common(10)),
        "board_lanes": dict(board_lanes),
    }


# ─────────────────────────────────────────────────────────────
#  PEOPLE INTELLIGENCE  — per-person productivity & gamification
# ─────────────────────────────────────────────────────────────

def process_people_data(rows):
    """Compute per-person productivity, effectiveness and gamification metrics."""
    if not rows:
        return None

    people = {}
    for r in rows:
        name = (r.get("Assignee","") or "").strip() or "Unassigned"
        if name not in people:
            people[name] = {
                "name": name, "total": 0, "done": 0, "in_progress": 0,
                "todo": 0, "on_hold": 0, "bugs_total": 0, "bugs_done": 0,
                "critical_total": 0, "critical_done": 0, "resolve_times": [],
                "issue_types": {}, "projects": set(), "recent_activity": 0,
            }
        p  = people[name]
        sc = r.get("Status Category","")
        st = r.get("Status","")
        it = r.get("Issue Type","")
        pr = r.get("Priority","")
        p["total"] += 1
        if sc == "Done":
            p["done"] += 1
            cr = parse_date(r.get("Created",""))
            rs = parse_date(r.get("Resolved",""))
            if cr and rs and rs >= cr:
                p["resolve_times"].append((rs - cr).days)
        elif st == "In Progress": p["in_progress"] += 1
        elif sc == "To Do":       p["todo"] += 1
        if st == "On Hold":       p["on_hold"] += 1
        p["issue_types"][it] = p["issue_types"].get(it, 0) + 1
        if it == "Bug":
            p["bugs_total"] += 1
            if sc == "Done": p["bugs_done"] += 1
        if any(x in (pr or "") for x in ("Critical","P1","P2","High","Highest","Blocker")):
            p["critical_total"] += 1
            if sc == "Done": p["critical_done"] += 1
        proj = r.get("Project key","")
        if proj: p["projects"].add(proj)
        upd = parse_date(r.get("Updated",""))
        if upd and (datetime.now() - upd).days <= 30:
            p["recent_activity"] += 1

    PC = ["#1e6ef5","#0891b2","#7c3aed","#38a169","#d97706","#e53e3e",
          "#f97316","#ec4899","#14b8a6","#8b5cf6","#84cc16","#94a3b8"]

    for nm, p in people.items():
        t  = p["total"]
        p["resolution_rate"]       = round(p["done"]/t*100,1)                          if t else 0.0
        p["bug_closure_rate"]      = round(p["bugs_done"]/p["bugs_total"]*100,1)        if p["bugs_total"]   else 0.0
        p["critical_closure_rate"] = round(p["critical_done"]/p["critical_total"]*100,1) if p["critical_total"] else 0.0
        p["on_hold_rate"]          = round(p["on_hold"]/t*100,1)                        if t else 0.0
        p["avg_resolve_days"]      = round(sum(p["resolve_times"])/len(p["resolve_times"]),1) if p["resolve_times"] else None
        p["projects"]              = sorted(list(p["projects"]))
        p["color"]                 = PC[hash(nm) % len(PC)]
        rs_s  = p["resolution_rate"]  * 0.40
        sp_s  = max(0, 25*(1 - min(p["avg_resolve_days"],60)/60)) if p["avg_resolve_days"] is not None else 12.0
        bs_s  = p["bug_closure_rate"] * 0.20
        cs_s  = p["critical_closure_rate"] * 0.15
        p["productivity_score"] = round(min(100, rs_s + sp_s + bs_s + cs_s), 1)

    active   = sorted([p for p in people.values() if p["total"] >= 3],
                      key=lambda x: (-x["productivity_score"], -x["total"]))
    inactive = sorted([p for p in people.values() if p["total"] < 3],
                      key=lambda x: -x["total"])
    all_sorted = active + inactive

    for i, p in enumerate(all_sorted):
        p["rank"]  = i + 1
        p["medal"] = (["🥇","🥈","🥉"][i] if i < 3 else "") if p["total"] >= 3 else ""
        p["badges"] = []
        if p["total"] >= 5 and p["productivity_score"] >= 75: p["badges"].append(("🏆","MVP"))
        if p["bugs_done"] >= 3:                                p["badges"].append(("🐛","Bug Slayer"))
        if p["avg_resolve_days"] is not None and p["avg_resolve_days"] <= 5:
                                                               p["badges"].append(("⚡","Speed Demon"))
        if p["critical_done"] >= 2:                           p["badges"].append(("💎","Guardian"))
        if p["total"] >= 20:                                   p["badges"].append(("💪","Heavy Lifter"))
        if p["recent_activity"] >= 10:                        p["badges"].append(("🔥","On Fire"))
        if   p["total"] < 3:
            p["tier"]="limited";  p["tier_label"]="Limited Data";      p["tier_color"]="#94a3b8"
        elif p["productivity_score"] >= 70:
            p["tier"]="excellent";p["tier_label"]="Excellent";          p["tier_color"]="#38a169"
        elif p["productivity_score"] >= 45:
            p["tier"]="good";     p["tier_label"]="Good";               p["tier_color"]="#d97706"
        elif p["resolution_rate"] == 0 and p["total"] >= 5:
            p["tier"]="critical"; p["tier_label"]="Needs Attention";    p["tier_color"]="#e53e3e"
        else:
            p["tier"]="improve";  p["tier_label"]="Needs Improvement";  p["tier_color"]="#f97316"

    av  = list(people.values())
    tt  = sum(p["total"]       for p in av)
    td  = sum(p["done"]        for p in av)
    tb  = sum(p["bugs_total"]  for p in av)
    tbd = sum(p["bugs_done"]   for p in av)
    rt  = [d for p in av for d in p["resolve_times"]]
    return {
        "people":           all_sorted,
        "active":           active,
        "top3":             active[:3],
        "needs_help":       [p for p in active if p["tier"] in ("critical","improve")],
        "team_total":       tt,
        "team_done":        td,
        "team_res_rate":    round(td/tt*100,1) if tt else 0,
        "team_bugs":        tb,
        "team_bugs_done":   tbd,
        "team_bug_closure": round(tbd/tb*100,1) if tb else 0,
        "team_avg_resolve": round(sum(rt)/len(rt),1) if rt else None,
        "total_members":    len(all_sorted),
        "active_members":   len(active),
        "exc_count":        sum(1 for p in active if p["tier"]=="excellent"),
        "good_count":       sum(1 for p in active if p["tier"]=="good"),
        "imp_count":        sum(1 for p in active if p["tier"] in ("critical","improve")),
    }


# ─────────────────────────────────────────────────────────────
#  LOGO  (embedded once at startup)
# ─────────────────────────────────────────────────────────────

def get_logo_src():
    path = os.path.join(os.path.dirname(__file__), "logo.png")
    if os.path.exists(path):
        with open(path, "rb") as f:
            return "data:image/png;base64," + base64.b64encode(f.read()).decode()
    return ""

LOGO_SRC = get_logo_src()


def _auto_connect_from_env():
    """
    If all Jira credentials are present in .env / environment variables,
    kick off an initial fetch automatically so the dashboard is ready
    without any manual interaction.
    """
    cfg = _cache["config"]
    if cfg.get("jira_url") and cfg.get("email") and cfg.get("api_token"):
        log.info("Auto-connecting from environment / .env credentials…")
        t = threading.Thread(target=background_fetch, args=(cfg,), daemon=True)
        t.start()
    else:
        log.info("No .env credentials found — manual connection required.")

_auto_connect_from_env()


# ─────────────────────────────────────────────────────────────
#  CONNECT PAGE
# ─────────────────────────────────────────────────────────────

CONNECT_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Jira Intelligence Dashboard — Connect</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap');
*{box-sizing:border-box;margin:0;padding:0}
body{background:linear-gradient(135deg,#0f2d5e 0%,#1a3f7a 50%,#0f2d5e 100%);min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;font-family:'Plus Jakarta Sans',sans-serif;padding:20px}
.card{background:#fff;border-radius:16px;padding:40px 48px;max-width:560px;width:100%;box-shadow:0 32px 80px rgba(0,0,0,.4)}
.logo{max-width:160px;height:auto;margin-bottom:24px;display:block}
h1{font-size:20px;font-weight:800;color:#0f2d5e;margin-bottom:4px}
.sub{font-size:12.5px;color:#64748b;margin-bottom:28px;line-height:1.6}
.tabs{display:flex;gap:0;border:1px solid #e2e8f2;border-radius:8px;overflow:hidden;margin-bottom:24px}
.tab{flex:1;padding:9px 0;text-align:center;font-size:12px;font-weight:700;cursor:pointer;color:#64748b;background:#f8fafc;transition:all .15s;border:none}
.tab.active{background:#0f2d5e;color:#fff}
.tab-content{display:none}.tab-content.active{display:block}
.field{margin-bottom:16px}
label{display:block;font-size:11.5px;font-weight:700;color:#374151;margin-bottom:5px;letter-spacing:.3px}
.hint{font-size:10px;color:#94a3b8;margin-top:3px;font-family:'JetBrains Mono',monospace}
input,textarea{width:100%;padding:9px 12px;border:1.5px solid #e2e8f2;border-radius:7px;font-size:13px;font-family:'Plus Jakarta Sans',sans-serif;color:#1e293b;background:#fff;outline:none;transition:border-color .15s}
input:focus,textarea:focus{border-color:#1e6ef5;box-shadow:0 0 0 3px rgba(30,110,245,.1)}
input[type=password]{font-family:'JetBrains Mono',monospace;letter-spacing:2px}
.row2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.btn{width:100%;padding:13px;background:linear-gradient(135deg,#1e6ef5,#0891b2);color:#fff;border:none;border-radius:9px;font-size:14px;font-weight:700;cursor:pointer;font-family:'Plus Jakarta Sans',sans-serif;transition:opacity .2s;margin-top:4px}
.btn:hover{opacity:.9}
.btn-sec{background:linear-gradient(135deg,#38a169,#0891b2)}
.btn-test{background:#f8fafc;color:#1e6ef5;border:1.5px solid #e2e8f2;font-size:12px;padding:9px;margin-top:0;margin-bottom:12px}
.btn-test:hover{background:#eff6ff;border-color:#1e6ef5}
.alert{padding:10px 14px;border-radius:7px;font-size:12px;margin-bottom:14px;display:none;line-height:1.5}
.alert-ok{background:#f0fff4;border:1px solid rgba(56,161,105,.3);color:#14532d}
.alert-err{background:#fff5f5;border:1px solid rgba(229,62,62,.3);color:#7f1d1d}
.steps{display:flex;flex-direction:column;gap:8px;margin-bottom:20px}
.step{display:flex;align-items:flex-start;gap:10px;padding:10px 12px;background:#f8fafc;border-radius:7px;border:1px solid #e2e8f2}
.sn{width:22px;height:22px;border-radius:50%;background:#1e6ef5;color:#fff;font-size:10px;font-weight:700;display:flex;align-items:center;justify-content:center;flex-shrink:0;margin-top:1px}
.st{font-size:11.5px;color:#374151;line-height:1.5}
.st strong{color:#1e293b}
.st a{color:#1e6ef5;text-decoration:none}
.spinner{display:none;width:18px;height:18px;border:2px solid #e2e8f2;border-top-color:#1e6ef5;border-radius:50%;animation:spin .7s linear infinite;margin:0 auto}
@keyframes spin{to{transform:rotate(360deg)}}
.foot{margin-top:20px;padding-top:14px;border-top:1px solid #f1f5f9;font-size:10px;color:#94a3b8;text-align:center}
.status-banner{background:#eff6ff;border:1px solid rgba(30,110,245,.2);border-radius:8px;padding:12px 16px;margin-bottom:16px;display:flex;align-items:center;gap:10px;font-size:12px;color:#1e3a8a}
.ldot{width:8px;height:8px;border-radius:50%;background:#38a169;flex-shrink:0;animation:blink 2s infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.3}}
</style>
</head>
<body>
<div class="card">
  <img src="{{ logo_src }}" alt="Jira Intelligence Dashboard" class="logo"/>
  <h1>Connect to Jira</h1>
  <p class="sub">Link your Jira workspace once. Your team hits <strong>Refresh</strong> on the dashboard URL to get live data anytime.</p>

  {% if connected %}
  <div class="status-banner">
    <div class="ldot"></div>
    <div>Connected to <strong>{{ config.jira_url }}</strong> &nbsp;·&nbsp;
    Last synced: <strong>{{ fetched_at }}</strong> &nbsp;·&nbsp;
    <strong>{{ total }} issues</strong> loaded
    <br><a href="/dashboard" style="color:#1e6ef5;font-weight:700;text-decoration:none">→ Open Dashboard</a>
    &nbsp;&nbsp;
    <a href="/refresh" style="color:#38a169;font-weight:700;text-decoration:none">↺ Refresh Data</a>
    </div>
  </div>
  {% endif %}

  <div class="tabs">
    <button class="tab active" onclick="showTab('api',this)">🔗 Jira API</button>
    <button class="tab" onclick="showTab('help',this)">❓ Help</button>
  </div>

  <!-- ── API TAB ── -->
  <div id="tab-api" class="tab-content active">
    <div id="alertBox" class="alert"></div>
    <form id="apiForm" onsubmit="connectApi(event)">
      <div class="field">
        <label>JIRA BASE URL</label>
        <input type="url" name="jira_url" placeholder="https://yourcompany.atlassian.net"
               value="{{ config.jira_url }}" required/>
        <div class="hint">Your Atlassian Cloud URL — no trailing slash</div>
      </div>
      <div class="row2">
        <div class="field">
          <label>EMAIL</label>
          <input type="email" name="email" placeholder="you@company.com"
                 value="{{ config.email }}" required/>
        </div>
        <div class="field">
          <label>API TOKEN</label>
          <input type="password" name="api_token" placeholder="••••••••••••••••"
                 value="{{ config.api_token }}" required/>
          <div class="hint"><a href="https://id.atlassian.com/manage-profile/security/api-tokens" target="_blank" style="color:#1e6ef5">Generate token ↗</a></div>
        </div>
      </div>
      <div class="row2">
        <div class="field">
          <label>PROJECT KEYS  <span style="color:#94a3b8;font-weight:400">(comma-separated)</span></label>
          <input type="text" name="projects" placeholder="P2, VPD, UAMRQ"
                 value="{{ config.projects }}"/>
          <div class="hint">Leave blank = all projects you can access</div>
        </div>
        <div class="field">
          <label>MAX ISSUES</label>
          <input type="number" name="max_results" placeholder="500" min="50" max="5000"
                 value="{{ config.max_results }}"/>
          <div class="hint">Increase for large projects</div>
        </div>
      </div>
      <button type="button" class="btn btn-test" onclick="testConnection()">
        🔍 Test Connection
      </button>
      <div class="spinner" id="spinner"></div>
      <button type="submit" class="btn">Connect &amp; Load Dashboard →</button>
    </form>
  </div>

  <!-- ── HELP TAB ── -->
  <div id="tab-help" class="tab-content">
    <div class="steps">
      <div class="step"><div class="sn">1</div><div class="st">Go to <a href="https://id.atlassian.com/manage-profile/security/api-tokens" target="_blank">id.atlassian.com → Security → API Tokens</a> and click <strong>Create API token</strong>.</div></div>
      <div class="step"><div class="sn">2</div><div class="st">Give it a name like <strong>"Jira Intelligence Dashboard"</strong> and copy the token — you won't see it again.</div></div>
      <div class="step"><div class="sn">3</div><div class="st">Paste your <strong>Jira URL</strong> (e.g. <code style="font-family:'JetBrains Mono',monospace;background:#f1f5f9;padding:1px 5px;border-radius:3px">https://acme.atlassian.net</code>), your <strong>email</strong>, and the token into the fields above. <strong>No trailing slash</strong> in the URL.</div></div>
      <div class="step"><div class="sn">4</div><div class="st">Enter your <strong>project keys</strong> (e.g. <code style="font-family:'JetBrains Mono',monospace;background:#f1f5f9;padding:1px 5px;border-radius:3px">P2, VPD, UAMRQ</code>) — or leave blank for all projects you have access to.</div></div>
      <div class="step"><div class="sn">5</div><div class="st">Click <strong>Connect &amp; Load Dashboard</strong>. Supports both <strong>Jira Cloud</strong> and <strong>Jira Server / Data Center</strong> — the API version is detected automatically.</div></div>
      <div class="step"><div class="sn">6</div><div class="st">Share <code style="font-family:'JetBrains Mono',monospace;background:#f1f5f9;padding:1px 5px;border-radius:3px">http://your-server:8085/dashboard</code> with your team. They click <strong>↺ Refresh</strong> to pull live data from Jira anytime.</div></div>
      <div class="step"><div class="sn">7</div><div class="st"><strong>Auto-connect tip:</strong> Fill in your <code style="font-family:'JetBrains Mono',monospace;background:#f1f5f9;padding:1px 5px;border-radius:3px">.env</code> file with your credentials and the dashboard will connect automatically every time the container starts — no manual login needed.</div></div>
    </div>
    <div style="background:#fffbeb;border:1px solid rgba(217,119,6,.2);border-radius:7px;padding:10px 14px;font-size:11px;color:#78350f">
      🔒 <strong>Security:</strong> Your API token is stored only in server memory and the <code>.env</code> file — never exposed to the browser or team members viewing the dashboard.
    </div>
  </div>

  <div class="foot" id="footClock"></div>
</div>

<script>
function showTab(id, el) {
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('tab-'+id).classList.add('active');
  el.classList.add('active');
}

function showAlert(msg, ok) {
  const el = document.getElementById('alertBox');
  el.className = 'alert ' + (ok ? 'alert-ok' : 'alert-err');
  el.textContent = msg;
  el.style.display = 'block';
}

async function testConnection() {
  const f = document.getElementById('apiForm');
  const body = new FormData(f);
  showAlert('Testing connection…', true);
  document.getElementById('spinner').style.display='block';
  try {
    const r = await fetch('/test-connection', {method:'POST', body});
    const j = await r.json();
    showAlert(j.message, j.ok);
  } catch(e) {
    showAlert('Network error: ' + e, false);
  }
  document.getElementById('spinner').style.display='none';
}

async function connectApi(e) {
  e.preventDefault();
  const body = new FormData(document.getElementById('apiForm'));
  document.querySelector('.btn[type=submit]').textContent = 'Connecting…';
  document.getElementById('spinner').style.display='block';
  try {
    const r = await fetch('/connect', {method:'POST', body});
    const j = await r.json();
    if (j.ok) {
      showAlert('✅ Connected! Redirecting to dashboard…', true);
      setTimeout(() => { window.location.href = '/dashboard'; }, 1200);
    } else {
      showAlert('❌ ' + j.message, false);
    }
  } catch(e) {
    showAlert('Network error: ' + e, false);
  }
  document.querySelector('.btn[type=submit]').textContent = 'Connect & Load Dashboard →';
  document.getElementById('spinner').style.display='none';
}

// Live clock
(function() {
  const M=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  function pad(n){return String(n).padStart(2,'0');}
  function tick() {
    const n=new Date();
    const d = pad(n.getDate())+' '+M[n.getMonth()]+' '+n.getFullYear()+'  '+pad(n.getHours())+':'+pad(n.getMinutes())+':'+pad(n.getSeconds());
    const el=document.getElementById('footClock');
    if(el) el.textContent='© '+n.getFullYear()+' Arunakumar Tavva. All rights reserved.  ·  '+d;
  }
  tick(); setInterval(tick,1000);
})();
</script>
</body>
</html>"""


# ─────────────────────────────────────────────────────────────
#  DASHBOARD HTML BUILDER  (re-used from original, with refresh)
# ─────────────────────────────────────────────────────────────

def build_dashboard_html(d, logo_src, fetched_at=None, jira_url=""):
    """Full 5-tab dashboard — identical to original plus refresh button."""

    def pct_bar(pct, color="var(--blue)", height=7):
        return (f'<div style="height:{height}px;background:var(--bg);border-radius:4px;'
                f'overflow:hidden;border:1px solid var(--border)">'
                f'<div style="height:100%;width:{min(pct,100):.1f}%;'
                f'background:{color};border-radius:3px"></div></div>')

    def badge(text, cls):
        return f'<span class="badge b-{cls}">{text}</span>'

    def prio_dot(p):
        col = "#dc2626" if "Critical" in (p or "") or "P1" in (p or "") or "P2" in (p or "") else "#f97316"
        return f'<span class="prio"><span class="pd" style="background:{col}"></span>{p or "—"}</span>'

    def av_initials(name):
        parts = (name or "?").split()
        return ("".join(p[0] for p in parts[:2])).upper() if parts else "?"

    COLORS = ["#1e6ef5","#0891b2","#7c3aed","#38a169","#d97706","#e53e3e",
              "#94a3b8","#f97316","#ec4899","#14b8a6","#8b5cf6","#84cc16"]

    def av_color(name):
        return COLORS[hash(name or "") % len(COLORS)]

    def status_badge(s):
        m = {"Closed":"done","Issue Closed":"done","Issue Resolved":"done",
             "In Progress":"ip","Review":"ip","In UAT":"test","Ready For QA":"rev",
             "New Bug":"bug","Backlog":"todo","On Hold":"hold",
             "New Task":"todo","To Do":"todo"}
        return badge(s or "—", m.get(s,"todo"))

    def type_badge(t):
        cls = {"Bug":"bug","Test":"test","Task":"ip","Sub-task":"rev","Story":"ip"}.get(t,"todo")
        return badge(t or "Issue", cls)

    # KPIs
    approval_count = len(d["approvals"])
    active_count   = d["inprog_count"] + sum(1 for r in d["rows"] if r.get("Status") in {"Ready For QA","In UAT"})
    kpis = [
        (str(d["total"]),        "Total Issues",      f"↑ {max(d['by_date'].values(),default=0)} added in one day", "var(--blue)"),
        (str(d["open_count"]),   "Open / To Do",      f"{round(d['open_count']/d['total']*100,1) if d['total'] else 0}% of total", "var(--red)"),
        (str(d["done_count"]),   "Done / Closed",     f"{round(d['done_count']/d['total']*100,1) if d['total'] else 0}% closure", "var(--green)"),
        (str(d["bug_count"]),    "Total Bugs",        f"{d['open_bug_count']} open · {d['closed_bug_count']} resolved", "var(--red)"),
        (str(active_count),      "Active Work",       "In progress + QA/UAT", "var(--teal)"),
        (str(approval_count),    "Pending Approvals", "Awaiting sign-off", "var(--amber)"),
    ]
    kpi_html = "".join(
        f'<div class="kpi" style="--ka:{c}"><div class="kpi-val" style="color:{c}">{v}</div>'
        f'<div class="kpi-lbl">{l}</div><div class="kpi-sub" style="color:{c}">{s}</div></div>'
        for v,l,s,c in kpis
    )

    # Insights
    as_ = d["assignee_stats"]
    zero_res   = [(a,as_[a]) for a in as_ if as_[a]["rate"]==0 and as_[a]["total"]>5]
    bug_owners = sorted([(a,as_[a]) for a in as_ if as_[a]["bugs"]>0], key=lambda x:x[1]["bugs"], reverse=True)
    top_perf   = sorted([(a,as_[a]) for a in as_ if as_[a]["total"]>=5], key=lambda x:x[1]["rate"], reverse=True)
    insight_html = ""
    if zero_res:
        a,s = zero_res[0]
        insight_html += f'<div class="insight ins-r"><span class="ins-icon">🚨</span><div class="ins-text"><strong>{a} — 0% resolution on {s["total"]} issues.</strong> All items in unopened status. Risk of sprint bottleneck.</div></div>'
    if bug_owners:
        a,s = bug_owners[0]
        insight_html += f'<div class="insight ins-r"><span class="ins-icon">🚨</span><div class="ins-text"><strong>{a} owns {s["bugs"]} bugs</strong> with {s["rate"]}% closure rate. Bug backlog growing faster than resolution.</div></div>'
    if top_perf:
        a,s = top_perf[0]
        insight_html += f'<div class="insight ins-g"><span class="ins-icon">⭐</span><div class="ins-text"><strong>{a}</strong> leads at <strong>{s["rate"]}%</strong> resolution ({s["done"]}/{s["total"]} issues).</div></div>'
    insight_html += f'<div class="insight ins-b"><span class="ins-icon">📋</span><div class="ins-text"><strong>{d["issue_types"].get("Test",0)} test cases</strong> · <strong>{d["open_bug_count"]} open bugs</strong> · <strong>{d["todo_count"]} items</strong> in To Do.</div></div>'

    # Status donut
    sc = d["status_cat"]
    circ  = 301.59
    td_pct = round(sc.get("To Do",0)/d["total"]*100,1) if d["total"] else 0
    dn_pct = round(sc.get("Done",0)/d["total"]*100,1)  if d["total"] else 0
    ip_pct = round(sc.get("In Progress",0)/d["total"]*100,1) if d["total"] else 0
    td_da, dn_da, ip_da = round(td_pct/100*circ,1), round(dn_pct/100*circ,1), round(ip_pct/100*circ,1)

    # Project bars
    proj_bar_html = ""
    sorted_projs = sorted(d["proj_stats"].items(), key=lambda x:x[1]["total"], reverse=True)
    proj_colors = ["linear-gradient(90deg,#1e6ef5,#0891b2)","linear-gradient(90deg,#7c3aed,#a855f7)",
                   "linear-gradient(90deg,#38a169,#10b981)","linear-gradient(90deg,#d97706,#fbbf24)",
                   "linear-gradient(90deg,#e53e3e,#f87171)"]
    for i,(proj,ps) in enumerate(sorted_projs[:5]):
        cc = "#38a169" if ps["pct"]>50 else ("#d97706" if ps["pct"]>20 else "#e53e3e")
        proj_bar_html += f"""<div class="prog-item" style="margin-top:4px">
          <div class="prog-head"><div class="prog-lbl" style="font-family:'JetBrains Mono',monospace;font-size:10px">{proj[:35]}</div><div class="prog-val" style="color:{cc}">{ps['pct']}%</div></div>
          {pct_bar(ps['pct'],proj_colors[i%len(proj_colors)],10)}
          <div style="display:flex;justify-content:space-between;font-size:9px;color:var(--muted);margin-top:2px"><span>{ps['done']} done / {ps['total']}</span><span>{ps['in_progress']} in prog · {ps['open_bugs']} bugs</span></div>
        </div>"""

    # Issue type bars
    it = d["issue_types"]
    tot_it = sum(it.values()) or 1
    type_bar_html = ""
    type_colors_map = {"Test":"linear-gradient(90deg,#0891b2,#22d3ee)","Task":"linear-gradient(90deg,#1e6ef5,#60a5fa)",
                       "Bug":"linear-gradient(90deg,#e53e3e,#f87171)","Sub-task":"linear-gradient(90deg,#7c3aed,#a78bfa)"}
    for typ,cnt in sorted(it.items(),key=lambda x:x[1],reverse=True)[:6]:
        col = type_colors_map.get(typ,"linear-gradient(90deg,#d97706,#fbbf24)")
        icon = {"Test":"🧪","Task":"📋","Bug":"🐛","Sub-task":"🔧"}.get(typ,"📝")
        type_bar_html += f"""<div class="prog-item">
          <div class="prog-head"><div class="prog-lbl">{icon} {typ}</div><div class="prog-val">{cnt}</div></div>
          {pct_bar(cnt/tot_it*100,col)}
        </div>"""

    # Bug boxes
    bug_new    = d["bug_statuses"].get("New Bug",0)
    bug_closed = d["bug_statuses"].get("Issue Closed",0)
    bug_res    = d["bug_statuses"].get("Issue Resolved",0)
    bug_bl     = d["bug_statuses"].get("Backlog",0)
    bp = d["bug_priorities"]
    bug_p_crit   = sum(v for k,v in bp.items() if "Critical" in k or "P1" in k or "P2" in k)
    bug_p_major  = sum(v for k,v in bp.items() if "Major" in k or "P3" in k)
    bug_p_medium = sum(v for k,v in bp.items() if "Medium" in k)
    bug_p_minor  = sum(v for k,v in bp.items() if "Minor" in k or "Low" in k or "P4" in k)

    # Workload rows
    workload_html = ""
    max_open = max(d["assignee_open"].values()) if d["assignee_open"] else 1
    for a,cnt in list(d["assignee_open"].items())[:8]:
        if a=="Unassigned": continue
        av   = av_initials(a)
        col  = av_color(a)
        pct  = round(cnt/max_open*100)
        rate = as_.get(a,{}).get("rate",0)
        star = " ⭐" if rate>=80 else ""
        workload_html += f"""<div class="pi-row">
          <div class="av" style="background:{col}">{av}</div>
          <div style="flex:1"><div class="pname">{a}</div><div class="prole">{rate}% resolution{star}</div></div>
          <div style="width:70px;height:5px;background:var(--bg);border-radius:3px;overflow:hidden;border:1px solid var(--border);margin-right:8px"><div style="height:100%;width:{pct}%;background:{col};border-radius:2px"></div></div>
          <div style="font-family:'JetBrains Mono',monospace;font-weight:700;font-size:12px">{cnt}</div>
        </div>"""
    unassigned_cnt = d["assignee_open"].get("Unassigned", d["unassigned"])
    workload_html += f'<div style="margin-top:10px;padding:8px 12px;background:#f8fafc;border:1px solid var(--border);border-radius:6px;display:flex;justify-content:space-between;align-items:center"><span style="font-size:10px;color:var(--muted)">⚠ Unassigned issues</span><span style="font-family:\'JetBrains Mono\',monospace;font-size:13px;font-weight:700;color:var(--muted)">{unassigned_cnt}</span></div>'

    # Activity bars
    activity_html = ""
    max_cnt = max(d["by_date"].values()) if d["by_date"] else 1
    for date_lbl,cnt in sorted(d["by_date"].items()):
        h      = max(2, round(cnt/max_cnt*110))
        spike  = cnt==max_cnt
        col    = "linear-gradient(180deg,#e53e3e,#f97316)" if spike else ("#1e6ef5" if cnt>max_cnt*0.3 else "#bfdbfe")
        vc     = "#e53e3e" if spike else "var(--muted)"
        parts  = date_lbl.split()
        activity_html += f"""<div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:3px">
          <div style="font-size:9px;font-family:'JetBrains Mono',monospace;color:{vc};font-weight:{'600' if spike else '400'}">{cnt}</div>
          <div style="width:100%;background:{col};border-radius:3px 3px 0 0;height:{h}px"></div>
          <div style="font-size:7px;color:var(--muted);text-align:center">{parts[0] if parts else ''}<br>{parts[1][:3] if len(parts)>1 else ''}</div>
        </div>"""
    spike_date = max(d["by_date"],key=d["by_date"].get) if d["by_date"] else "N/A"
    spike_cnt  = max(d["by_date"].values()) if d["by_date"] else 0

    # Board
    lane_order  = ["Backlog","To Do","In Progress","Review / QA","On Hold","Approval","Done","New Bug"]
    lane_colors = {"Backlog":"#94a3b8","To Do":"#64748b","In Progress":"#1e6ef5",
                   "Review / QA":"#7c3aed","On Hold":"#d97706","Approval":"#d97706",
                   "Done":"#38a169","New Bug":"#e53e3e"}
    lane_border = {"In Progress":"#1e6ef5","Review / QA":"#7c3aed","On Hold":"#d97706",
                   "Done":"#38a169","New Bug":"#e53e3e","Approval":"#d97706"}
    board_html = ""
    bl = d["board_lanes"]
    for lane in lane_order + [l for l in bl if l not in lane_order]:
        items = bl.get(lane,[])
        if not items: continue
        shown = items[:10]
        extra = len(items)-len(shown)
        def _card(c, lane=lane):
            blc = lane_border.get(lane,"transparent")
            return (f'<div class="board-card" style="border-left:2px solid {blc}">'
                    f'<div class="bc-key">{c["key"]}</div>'
                    f'<div class="bc-ttl">{c["summary"][:65]}</div>'
                    f'<div class="bc-meta">{type_badge(c["type"])}'
                    f'<div class="bc-av" style="background:{av_color(c["assignee"])}">{av_initials(c["assignee"])}</div>'
                    f'</div></div>')
        cards_html = "".join(_card(c) for c in shown)
        if extra>0: cards_html += f'<div class="lane-more">+ {extra} more</div>'
        board_html += f"""<div class="lane">
  <div class="lane-header"><div style="width:8px;height:8px;border-radius:50%;background:{lane_colors.get(lane,'#94a3b8')};flex-shrink:0"></div><div class="lane-ttl">{lane}</div><div class="lane-cnt">{len(items)}</div></div>
  <div class="lane-body">{cards_html}</div>
</div>"""

    sprint_done_pct = round(d["done_count"]/d["total"]*100,1) if d["total"] else 0
    sprint_ip_pct   = round(d["inprog_count"]/d["total"]*100,1) if d["total"] else 0

    # Backlog tables
    def backlog_row(r):
        tp      = r.get("Custom field (Testing Phase)","").strip() or ""
        phase   = badge(tp[:15],"test") if tp else ""
        return (f'<tr><td class="ik">{r.get("Issue key","")}</td>'
                f'<td class="smry">{r.get("Summary","")[:70]}</td>'
                f'<td>{prio_dot(r.get("Priority",""))}</td>'
                f'<td>{phase}</td>'
                f'<td>{(r.get("Assignee","") or "—")[:20]}</td>'
                f'<td style="font-family:\'JetBrains Mono\',monospace;font-size:10px">{r.get("Created","")[:10]}</td>'
                f'<td>{status_badge(r.get("Status",""))}</td></tr>')

    def task_row(r):
        return (f'<tr><td class="ik">{r.get("Issue key","")}</td>'
                f'<td class="smry">{r.get("Summary","")[:70]}</td>'
                f'<td>{type_badge(r.get("Issue Type",""))}</td>'
                f'<td>{r.get("Assignee","") or "—"}</td>'
                f'<td>{status_badge(r.get("Status",""))}</td></tr>')

    def approval_row(r):
        return (f'<tr><td class="ik">{r.get("Issue key","")}</td>'
                f'<td class="smry">{r.get("Summary","")[:70]}</td>'
                f'<td><span class="ptag" style="background:#fef3c7;color:#b45309">{r.get("Project key","")}</span></td>'
                f'<td>{r.get("Assignee","") or "—"}</td>'
                f'<td>{status_badge(r.get("Status",""))}</td></tr>')

    bug_rows_html  = "".join(backlog_row(r) for r in d["open_bugs"][:30])
    task_rows_html = "".join(task_row(r) for r in d["on_hold"]+[r for r in d["rows"] if r.get("Status")=="To Do"][:20])
    appr_rows_html = "".join(approval_row(r) for r in d["approvals"][:15])

    # Reports: resolution bars
    sorted_assignees = sorted([(a,s) for a,s in as_.items() if s["total"]>=3],
                               key=lambda x:x[1]["rate"], reverse=True)[:12]
    res_bar_html = ""
    for a,s in sorted_assignees:
        rate = s["rate"]
        col  = "#38a169" if rate>=70 else ("#d97706" if rate>=40 else "#e53e3e")
        res_bar_html += f"""<div class="chart-bar-h">
          <div class="bar-lbl-l">{a[:22]}</div>
          <div class="bar-bg"><div class="bar-fill" style="width:{rate}%;background:linear-gradient(90deg,{col},{col}aa)"><span class="bar-label">{rate}%</span></div></div>
          <div class="bar-val">{s['done']}/{s['total']}</div>
        </div>"""

    # Reporter bars
    reporter_max = max(d["reporters"].values()) if d["reporters"] else 1
    rep_bar_html = ""
    rep_colors   = ["#0891b2","#1e6ef5","#e53e3e","#7c3aed","#38a169","#d97706","#94a3b8"]
    for i,(rep,cnt) in enumerate(list(d["reporters"].items())[:8]):
        pct_r = round(cnt/reporter_max*100)
        col   = rep_colors[i%len(rep_colors)]
        rep_bar_html += f"""<div class="chart-bar-h">
          <div class="bar-lbl-l">{rep[:22]}</div>
          <div class="bar-bg"><div class="bar-fill" style="width:{pct_r}%;background:linear-gradient(90deg,{col},{col}bb)"><span class="bar-label">{cnt} ({round(cnt/d['total']*100,1)}%)</span></div></div>
          <div class="bar-val">{cnt}</div>
        </div>"""

    # Risk insights
    risk_html = ""
    for a,s in sorted_assignees:
        if s["rate"]==0 and s["total"]>=5:
            risk_html += f'<div class="insight ins-r"><span class="ins-icon">🚨</span><div class="ins-text"><strong>{a}</strong> — 0% resolution on {s["total"]} issues. Needs attention.</div></div>'
        elif s["bugs"]>=5:
            risk_html += f'<div class="insight ins-a"><span class="ins-icon">⚠️</span><div class="ins-text"><strong>{a}</strong> owns {s["bugs"]} bugs with {s["rate"]}% closure rate.</div></div>'
    if d["reporters"]:
        top_rep,top_cnt = list(d["reporters"].items())[0]
        risk_html += f'<div class="insight ins-b"><span class="ins-icon">📌</span><div class="ins-text"><strong>{top_rep}</strong> reported {top_cnt} of {d["total"]} issues ({round(top_cnt/d["total"]*100,1)}%).</div></div>'
    if not risk_html:
        risk_html = '<div class="insight ins-g"><span class="ins-icon">✅</span><div class="ins-text">No critical bottlenecks detected. Team is performing well.</div></div>'

    # Testing phase donut
    tp        = d["testing_phases"]
    tp_total  = sum(tp.values()) or 1
    tp_sorted = sorted(tp.items(),key=lambda x:x[1],reverse=True)
    tp_cols   = ["#0891b2","#7c3aed","#d97706","#38a169","#e53e3e"]
    dc2       = 263.9
    tp_offset = 0
    tp_circles = tp_legend = ""
    for i,(phase,cnt) in enumerate(tp_sorted[:5]):
        p  = cnt/tp_total*100
        da = round(p/100*dc2,1)
        col = tp_cols[i%len(tp_cols)]
        tp_circles += f'<circle cx="55" cy="55" r="42" fill="none" stroke="{col}" stroke-width="18" stroke-dasharray="{da} {dc2-da:.1f}" stroke-dashoffset="-{tp_offset:.1f}" transform="rotate(-90 55 55)"/>'
        tp_offset  += da
        tp_legend  += f'<div class="li"><div class="ld" style="background:{col}"></div><div class="ll">{phase[:20]}</div><div class="ln">{cnt}</div><div class="lp">{p:.1f}%</div></div>'

    # Releases
    rel_html = ""
    for proj,ps in sorted(d["proj_stats"].items(),key=lambda x:x[1]["pct"],reverse=True):
        pct_v = ps["pct"]
        if pct_v>=50:
            sl,sc2,pcc,tc,emoji = '✅ On Track','#dcfce7;color:#15803d','var(--green)','linear-gradient(90deg,#38a169,#10b981)','🚀'
        elif pct_v>=15:
            sl,sc2,pcc,tc,emoji = '⚠️ In Progress','#fef3c7;color:#b45309','var(--amber)','linear-gradient(90deg,#e53e3e,#f97316)','🏦'
        else:
            sl,sc2,pcc,tc,emoji = '🔴 Early Stage','#fee2e2;color:#b91c1c','var(--red)','#e2e8f2','⏳'
        in_prog = [r for r in d["rows"] if r.get("Project name")==proj and r.get("Status Category")=="In Progress"]
        chips = "".join(f'<span style="background:var(--blue-l);color:var(--navy);padding:3px 8px;border-radius:4px;font-size:10px;font-weight:500;border:1px solid rgba(30,110,245,.15)">{r.get("Issue key","")} {r.get("Summary","")[:35]}</span>' for r in in_prog[:6])
        rel_html += f"""<div class="rel-card">
  <div class="rel-header">
    <div style="font-size:22px">{emoji}</div>
    <div style="flex:1"><div class="rel-name">{proj}</div>
    <div style="font-size:11px;color:var(--muted);margin-top:1px">{ps['key']} · {ps['total']} issues · {ps['done']} done · {ps['open_bugs']} bugs</div></div>
    <div style="text-align:right"><div class="rel-pct" style="color:{pcc}">{pct_v}%</div><div style="font-size:10px;color:var(--muted)">Complete</div></div>
    <span style="background:{sc2};padding:4px 12px;border-radius:20px;font-size:11px;font-weight:700">{sl}</span>
  </div>
  <div class="rel-body">
    <div class="rel-track"><div class="rel-fill" style="width:{pct_v}%;background:{tc}"></div></div>
    <div class="rel-stats">
      <div class="rel-stat"><div class="rel-stat-n" style="color:var(--green)">{ps['done']}</div><div class="rel-stat-l">Done</div></div>
      <div class="rel-stat"><div class="rel-stat-n" style="color:var(--blue)">{ps['in_progress']}</div><div class="rel-stat-l">In Progress</div></div>
      <div class="rel-stat"><div class="rel-stat-n" style="color:var(--muted)">{ps['todo']}</div><div class="rel-stat-l">To Do</div></div>
      <div class="rel-stat"><div class="rel-stat-n" style="color:var(--red)">{ps['open_bugs']}</div><div class="rel-stat-l">Open Bugs</div></div>
      <div class="rel-stat"><div class="rel-stat-n" style="color:var(--text)">{ps['total']}</div><div class="rel-stat-l">Total</div></div>
    </div>
    {'<div style="margin-top:10px"><div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:1px;margin-bottom:6px">Active Work Items</div><div style="display:flex;flex-wrap:wrap;gap:6px">'+chips+'</div></div>' if chips else ''}
  </div>
</div>"""

    # Project tags
    proj_tags = " ".join(
        f'<span class="ptag" style="background:{["#e8f0fe","#e0f2fe","#f5f3ff","#fef3c7","#fee2e2"][i%5]};color:{["#1e6ef5","#0369a1","#7c3aed","#b45309","#b91c1c"][i%5]}">{k}</span>'
        for i,(k,v) in enumerate(sorted(d["projects"].items(),key=lambda x:x[1],reverse=True)[:6])
    )

    fetched_str = fetched_at.strftime("%d %b %Y %H:%M:%S") if fetched_at else "—"
    jira_short  = jira_url.replace("https://","").replace("http://","")[:40] if jira_url else "CSV Upload"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>Jira Intelligence Dashboard — Live Board</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap');
:root{{
  --bg:#f0f4f9;--surface:#fff;--border:#e2e8f2;--border2:#d0d9e8;
  --navy:#0f2d5e;--blue:#1e6ef5;--blue-l:#e8f0fe;--teal:#0891b2;--teal-l:#e0f7fb;
  --red:#e53e3e;--red-l:#fff5f5;--orange:#dd6b20;--orange-l:#fffaf0;
  --green:#38a169;--green-l:#f0fff4;--amber:#d97706;--amber-l:#fffbeb;
  --purple:#7c3aed;--purple-l:#f5f3ff;--text:#1e293b;--text2:#475569;--muted:#94a3b8;
  --r:8px;--sh:0 1px 3px rgba(0,0,0,.06),0 1px 2px rgba(0,0,0,.04);
  --shm:0 4px 12px rgba(0,0,0,.08);
}}
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
html{{font-size:13px}}
body{{background:var(--bg);color:var(--text);font-family:'Plus Jakarta Sans',sans-serif;min-height:100vh}}
/* ── NAV ── */
.topnav{{background:var(--navy);padding:0 28px;display:flex;align-items:center;height:54px;box-shadow:0 2px 8px rgba(0,0,0,.2);position:sticky;top:0;z-index:100}}
.nav-logo{{display:flex;align-items:center;gap:10px;margin-right:28px;cursor:pointer;text-decoration:none;flex-shrink:0}}
.nav-logo img{{height:30px;width:auto;object-fit:contain}}
.nav-items{{display:flex;gap:2px}}
.nav-item{{font-size:12px;font-weight:600;color:rgba(255,255,255,.55);padding:7px 14px;border-radius:5px;cursor:pointer;transition:all .15s;white-space:nowrap;user-select:none}}
.nav-item:hover{{background:rgba(255,255,255,.08);color:#fff}}
.nav-item.active{{background:rgba(30,110,245,.4);color:#fff}}
.nav-right{{margin-left:auto;display:flex;align-items:center;gap:10px;flex-shrink:0}}
.nav-clock{{font-family:'JetBrains Mono',monospace;font-size:10px;color:rgba(255,255,255,.5);white-space:nowrap}}
.btn-refresh{{display:flex;align-items:center;gap:5px;background:rgba(56,161,105,.2);border:1px solid rgba(56,161,105,.4);color:#6ee7b7;padding:5px 12px;border-radius:6px;font-size:11px;font-weight:700;cursor:pointer;text-decoration:none;transition:all .15s;white-space:nowrap}}
.btn-refresh:hover{{background:rgba(56,161,105,.35);color:#fff}}
.btn-settings{{background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.2);color:rgba(255,255,255,.8);padding:5px 10px;border-radius:6px;font-size:11px;font-weight:600;cursor:pointer;text-decoration:none;transition:all .15s}}
.btn-settings:hover{{background:rgba(255,255,255,.18);color:#fff}}
/* ── SUBBAR ── */
.subbar{{background:#fff;border-bottom:1px solid var(--border);padding:8px 28px;display:flex;align-items:center;gap:10px;flex-wrap:wrap}}
.subbar-title{{font-size:14px;font-weight:800;color:var(--navy)}}
.ptag{{font-size:10px;font-weight:600;padding:2px 8px;border-radius:4px;font-family:'JetBrains Mono',monospace}}
.subbar-right{{margin-left:auto;display:flex;align-items:center;gap:8px;font-size:11px;color:var(--muted);font-family:'JetBrains Mono',monospace;flex-wrap:wrap}}
.live-badge{{display:flex;align-items:center;gap:5px;background:var(--green-l);color:var(--green);border:1px solid rgba(56,161,105,.2);border-radius:12px;padding:3px 10px;font-size:10px;font-weight:600}}
.ldot{{width:5px;height:5px;background:var(--green);border-radius:50%;animation:blink 2s infinite}}
@keyframes blink{{0%,100%{{opacity:1}}50%{{opacity:.3}}}}
/* ── CONTENT ── */
.content{{padding:20px 28px 40px}}
.tab-content{{display:none}}.tab-content.active{{display:block}}
/* ── GRID ── */
.row{{display:grid;gap:12px;margin-bottom:12px}}
.g2{{grid-template-columns:1fr 1fr}}.g3{{grid-template-columns:1fr 1fr 1fr}}
.g4{{grid-template-columns:1fr 1fr 1fr 1fr}}.g43{{grid-template-columns:4fr 3fr}}
/* ── KPI ── */
.kpi-grid{{display:grid;grid-template-columns:repeat(6,1fr);gap:10px;margin-bottom:16px}}
.kpi{{background:var(--surface);border:1px solid var(--border);border-radius:var(--r);padding:13px 15px;box-shadow:var(--sh);position:relative;overflow:hidden;transition:box-shadow .2s}}
.kpi:hover{{box-shadow:var(--shm)}}
.kpi::before{{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:var(--ka,var(--blue))}}
.kpi-val{{font-size:28px;font-weight:800;line-height:1}}
.kpi-lbl{{font-size:11px;color:var(--muted);font-weight:500;margin-top:3px}}
.kpi-sub{{font-size:10px;color:var(--muted);font-family:'JetBrains Mono',monospace;margin-top:5px}}
/* ── CARD ── */
.card{{background:var(--surface);border:1px solid var(--border);border-radius:var(--r);box-shadow:var(--sh);overflow:hidden}}
.card-header{{padding:11px 16px 9px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:8px}}
.card-title{{font-size:12px;font-weight:700;color:var(--text)}}
.ccount{{margin-left:auto;font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--muted);background:var(--bg);padding:2px 7px;border-radius:10px;border:1px solid var(--border)}}
.card-body{{padding:14px 16px}}
.stl{{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1.5px;color:var(--muted);margin:16px 0 8px;display:flex;align-items:center;gap:8px}}
.stl::after{{content:'';flex:1;height:1px;background:var(--border)}}
/* ── BADGE ── */
.badge{{display:inline-flex;align-items:center;font-size:10px;font-weight:600;padding:2px 8px;border-radius:4px;white-space:nowrap}}
.b-todo{{background:#f1f5f9;color:#64748b}}.b-ip{{background:#dbeafe;color:#1d4ed8}}
.b-done{{background:#dcfce7;color:#15803d}}.b-rev{{background:#ede9fe;color:#6d28d9}}
.b-hold{{background:#fef9c3;color:#a16207}}.b-bug{{background:#fee2e2;color:#b91c1c}}
.b-test{{background:#e0f2fe;color:#0369a1}}.b-appr{{background:#fef3c7;color:#b45309}}
.prio{{display:inline-flex;align-items:center;gap:4px;font-size:10px;font-weight:600}}
.pd{{width:8px;height:8px;border-radius:50%}}
/* ── PROG ── */
.prog-wrap{{display:flex;flex-direction:column;gap:9px}}
.prog-item{{display:flex;flex-direction:column;gap:4px}}
.prog-head{{display:flex;align-items:center;justify-content:space-between}}
.prog-lbl{{font-size:11px;font-weight:600;color:var(--text2)}}
.prog-val{{font-size:11px;font-weight:700;font-family:'JetBrains Mono',monospace}}
/* ── TABLE ── */
.tbl{{width:100%;border-collapse:collapse;font-size:11.5px}}
.tbl thead th{{text-align:left;padding:7px 10px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--muted);border-bottom:1px solid var(--border);background:var(--bg);white-space:nowrap}}
.tbl tbody td{{padding:6px 10px;border-bottom:1px solid var(--border);color:var(--text2)}}
.tbl tbody tr:last-child td{{border-bottom:none}}
.tbl tbody tr:hover td{{background:#f8fafc}}
td.ik{{font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--blue);font-weight:500;white-space:nowrap}}
td.smry{{max-width:240px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--text);font-weight:500}}
/* ── DONUT ── */
.donut-wrap{{display:flex;align-items:center;gap:18px;padding:6px 0}}
.leg{{display:flex;flex-direction:column;gap:7px;flex:1}}
.li{{display:flex;align-items:center;gap:8px;font-size:11px}}
.ld{{width:10px;height:10px;border-radius:3px;flex-shrink:0}}
.ll{{color:var(--text2);flex:1;font-weight:500}}
.ln{{font-weight:700;color:var(--text);font-family:'JetBrains Mono',monospace;font-size:11px}}
.lp{{color:var(--muted);font-size:10px;font-family:'JetBrains Mono',monospace;min-width:34px;text-align:right}}
/* ── PERSON ── */
.pi-row{{display:flex;align-items:center;gap:10px;padding:7px 0;border-bottom:1px solid var(--border)}}
.pi-row:last-child{{border-bottom:none}}
.av{{width:30px;height:30px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;color:#fff;flex-shrink:0}}
.pname{{font-size:12px;font-weight:600;color:var(--text);flex:1}}
.prole{{font-size:10px;color:var(--muted)}}
/* ── INSIGHT ── */
.insight{{display:flex;align-items:flex-start;gap:10px;padding:10px 14px;border-radius:6px;border:1px solid transparent;margin-bottom:8px;font-size:11px;line-height:1.6}}
.ins-r{{background:var(--red-l);border-color:rgba(229,62,62,.2);color:#7f1d1d}}
.ins-a{{background:var(--amber-l);border-color:rgba(217,119,6,.2);color:#78350f}}
.ins-g{{background:var(--green-l);border-color:rgba(56,161,105,.2);color:#14532d}}
.ins-b{{background:var(--blue-l);border-color:rgba(30,110,245,.2);color:#1e3a8a}}
.ins-icon{{font-size:14px;flex-shrink:0;margin-top:1px}}
.ins-text strong{{font-weight:700}}
/* ── BOARD ── */
.board-wrap{{display:flex;gap:10px;overflow-x:auto;padding-bottom:8px;min-height:500px}}
.board-wrap::-webkit-scrollbar{{height:5px}}
.board-wrap::-webkit-scrollbar-thumb{{background:var(--border2);border-radius:3px}}
.lane{{min-width:200px;width:200px;display:flex;flex-direction:column;background:var(--bg);border-radius:var(--r);border:1px solid var(--border);flex-shrink:0}}
.lane-header{{padding:10px 12px 8px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:7px;background:var(--surface);border-radius:var(--r) var(--r) 0 0}}
.lane-ttl{{font-size:11px;font-weight:700;color:var(--text);flex:1}}
.lane-cnt{{background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:1px 7px;font-size:10px;font-weight:700;font-family:'JetBrains Mono',monospace;color:var(--muted)}}
.lane-body{{padding:8px;display:flex;flex-direction:column;gap:6px;flex:1;overflow-y:auto;max-height:580px}}
.lane-body::-webkit-scrollbar{{width:3px}}
.lane-body::-webkit-scrollbar-thumb{{background:var(--border2);border-radius:2px}}
.board-card{{background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:9px 10px;cursor:pointer;transition:box-shadow .15s}}
.board-card:hover{{box-shadow:var(--shm)}}
.bc-key{{font-family:'JetBrains Mono',monospace;font-size:9px;color:var(--blue);font-weight:500;margin-bottom:4px}}
.bc-ttl{{font-size:11px;font-weight:600;color:var(--text);line-height:1.35;margin-bottom:6px}}
.bc-meta{{display:flex;align-items:center;gap:5px;flex-wrap:wrap}}
.bc-av{{width:18px;height:18px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:7px;font-weight:700;color:#fff;margin-left:auto}}
.lane-more{{font-size:10px;color:var(--muted);text-align:center;padding:6px 0;font-family:'JetBrains Mono',monospace}}
.sh-bar{{display:flex;height:28px;border-radius:6px;overflow:hidden;border:1px solid var(--border);margin:8px 0}}
/* ── REPORTS ── */
.chart-bar-h{{display:flex;align-items:center;gap:8px;padding:5px 0}}
.bar-bg{{flex:1;height:22px;background:var(--bg);border-radius:4px;overflow:hidden;border:1px solid var(--border)}}
.bar-fill{{height:100%;border-radius:3px;display:flex;align-items:center;padding-left:8px}}
.bar-label{{font-size:10px;font-weight:700;color:#fff;white-space:nowrap}}
.bar-lbl-l{{font-size:11px;font-weight:600;color:var(--text2);min-width:130px;text-align:right}}
.bar-val{{font-size:11px;font-weight:700;font-family:'JetBrains Mono',monospace;min-width:38px;text-align:right}}
/* ── RELEASES ── */
.rel-card{{background:var(--surface);border:1px solid var(--border);border-radius:var(--r);box-shadow:var(--sh);overflow:hidden;margin-bottom:10px}}
.rel-header{{padding:14px 18px;display:flex;align-items:center;gap:12px}}
.rel-name{{font-size:14px;font-weight:800;color:var(--text);flex:1}}
.rel-pct{{font-size:22px;font-weight:800;font-family:'JetBrains Mono',monospace}}
.rel-body{{padding:14px 18px;border-top:1px solid var(--border)}}
.rel-track{{height:12px;background:var(--bg);border-radius:6px;overflow:hidden;border:1px solid var(--border);margin-bottom:12px}}
.rel-fill{{height:100%;border-radius:5px}}
.rel-stats{{display:grid;grid-template-columns:repeat(5,1fr);gap:8px}}
.rel-stat{{text-align:center;padding:8px;background:var(--bg);border-radius:6px;border:1px solid var(--border)}}
.rel-stat-n{{font-size:18px;font-weight:800}}
.rel-stat-l{{font-size:9px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-top:2px}}
/* ── FOOTER ── */
.footer{{margin-top:24px;display:flex;align-items:center;justify-content:space-between;padding:14px 0;border-top:1px solid var(--border2);font-size:10px;color:var(--muted)}}
.footer-brand{{font-weight:800;color:var(--navy);font-size:12px}}
/* ── REFRESH OVERLAY ── */
.refresh-overlay{{display:none;position:fixed;inset:0;background:rgba(15,45,94,.6);z-index:999;align-items:center;justify-content:center;backdrop-filter:blur(3px)}}
.refresh-overlay.show{{display:flex}}
.refresh-box{{background:#fff;border-radius:14px;padding:32px 40px;text-align:center;box-shadow:0 24px 60px rgba(0,0,0,.3)}}
.ref-spinner{{width:44px;height:44px;border:4px solid #e2e8f2;border-top-color:#1e6ef5;border-radius:50%;animation:spin .8s linear infinite;margin:0 auto 16px}}
@keyframes spin{{to{{transform:rotate(360deg)}}}}
.bl-table-wrap{{background:var(--surface);border:1px solid var(--border);border-radius:var(--r);overflow:hidden;box-shadow:var(--sh)}}
</style>
</head>
<body>

<!-- Refresh overlay -->
<div class="refresh-overlay" id="refreshOverlay">
  <div class="refresh-box">
    <div class="ref-spinner"></div>
    <div style="font-size:16px;font-weight:800;color:#0f2d5e;margin-bottom:6px">Fetching Latest Data</div>
    <div style="font-size:12px;color:#64748b">Connecting to Jira… please wait</div>
  </div>
</div>

<!-- ── TOP NAV ── -->
<nav class="topnav">
  <a href="/" class="nav-logo"><img src="{logo_src}" alt="Jira Intelligence Dashboard"/></a>
  <div class="nav-items">
    <div class="nav-item active" onclick="switchTab('dashboard',this)">Dashboard</div>
    <div class="nav-item" onclick="switchTab('board',this)">Board</div>
    <div class="nav-item" onclick="switchTab('backlog',this)">Backlog</div>
    <div class="nav-item" onclick="switchTab('reports',this)">Reports</div>
    <div class="nav-item" onclick="switchTab('releases',this)">Releases</div>
  </div>
  <div class="nav-right">
    <div class="nav-clock" id="liveClock"></div>
    <a href="/people" class="btn-settings" style="background:#38a169;color:#fff;border-color:#38a169">👥 People</a>
    <a href="/refresh" class="btn-refresh" onclick="showRefresh(event)">↺ Refresh</a>
    <a href="/" class="btn-settings">⚙ Settings</a>
  </div>
</nav>

<!-- ── SUBBAR ── -->
<div class="subbar">
  <div class="subbar-title">SprintPulse for Jira</div>
  {proj_tags}
  <div class="subbar-right">
    <div class="live-badge"><span class="ldot"></span>Live · {jira_short}</div>
    <span>Synced: {fetched_str}</span>
    <span>{d['total']} issues · {len(d['assignee_stats'])} members · {len(d['projects'])} projects</span>
  </div>
</div>

<div class="content">

<!-- ════ DASHBOARD ════ -->
<div id="tab-dashboard" class="tab-content active">
  <div class="kpi-grid">{kpi_html}</div>
  <div class="stl">🔎 Key Insights</div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:14px">{insight_html}</div>
  <div class="row g3">
    <div class="card">
      <div class="card-header"><span>📊</span><div class="card-title">Status Distribution</div><div class="ccount">{d['total']}</div></div>
      <div class="card-body">
        <div class="donut-wrap">
          <svg width="120" height="120" viewBox="0 0 120 120">
            <circle cx="60" cy="60" r="48" fill="none" stroke="#f1f5f9" stroke-width="22"/>
            <circle cx="60" cy="60" r="48" fill="none" stroke="#94a3b8" stroke-width="22" stroke-dasharray="{td_da} {circ-td_da:.1f}" stroke-dashoffset="0" transform="rotate(-90 60 60)"/>
            <circle cx="60" cy="60" r="48" fill="none" stroke="#38a169" stroke-width="22" stroke-dasharray="{dn_da} {circ-dn_da:.1f}" stroke-dashoffset="-{td_da:.1f}" transform="rotate(-90 60 60)"/>
            <circle cx="60" cy="60" r="48" fill="none" stroke="#1e6ef5" stroke-width="22" stroke-dasharray="{ip_da} {circ-ip_da:.1f}" stroke-dashoffset="-{td_da+dn_da:.1f}" transform="rotate(-90 60 60)"/>
            <text x="60" y="56" text-anchor="middle" fill="#1e293b" font-size="16" font-family="Plus Jakarta Sans,sans-serif" font-weight="800">{d['total']}</text>
            <text x="60" y="69" text-anchor="middle" fill="#94a3b8" font-size="8">issues</text>
          </svg>
          <div class="leg">
            <div class="li"><div class="ld" style="background:#94a3b8"></div><div class="ll">To Do</div><div class="ln">{d['todo_count']}</div><div class="lp">{td_pct}%</div></div>
            <div class="li"><div class="ld" style="background:#38a169"></div><div class="ll">Done</div><div class="ln">{d['done_count']}</div><div class="lp">{dn_pct}%</div></div>
            <div class="li"><div class="ld" style="background:#1e6ef5"></div><div class="ll">In Progress</div><div class="ln">{d['inprog_count']}</div><div class="lp">{ip_pct}%</div></div>
          </div>
        </div>
        <div style="margin-top:12px;display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px">
          <div style="background:#f8fafc;border:1px solid var(--border);border-radius:5px;padding:7px;text-align:center"><div style="font-size:15px;font-weight:800;color:var(--red)">{d['bug_statuses'].get('New Bug',0)}</div><div style="font-size:9px;color:var(--muted)">NEW BUGS</div></div>
          <div style="background:#f8fafc;border:1px solid var(--border);border-radius:5px;padding:7px;text-align:center"><div style="font-size:15px;font-weight:800;color:var(--amber)">{d['statuses'].get('On Hold',0)}</div><div style="font-size:9px;color:var(--muted)">ON HOLD</div></div>
          <div style="background:#f8fafc;border:1px solid var(--border);border-radius:5px;padding:7px;text-align:center"><div style="font-size:15px;font-weight:800;color:var(--purple)">{approval_count}</div><div style="font-size:9px;color:var(--muted)">APPROVALS</div></div>
        </div>
      </div>
    </div>
    <div class="card"><div class="card-header"><span>🏷️</span><div class="card-title">Issue Types</div></div><div class="card-body"><div class="prog-wrap">{type_bar_html}</div></div></div>
    <div class="card"><div class="card-header"><span>📁</span><div class="card-title">Completion by Project</div></div><div class="card-body"><div class="prog-wrap">{proj_bar_html}</div></div></div>
  </div>
  <div class="row g43">
    <div class="card">
      <div class="card-header"><span>🐛</span><div class="card-title">Bug Health — {d['bug_count']} Bugs</div><div class="ccount">{d['bug_count']} total</div></div>
      <div class="card-body">
        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:14px">
          <div style="background:var(--red-l);border:1px solid rgba(229,62,62,.2);border-radius:6px;padding:9px;text-align:center"><div style="font-size:22px;font-weight:800;color:var(--red)">{bug_new}</div><div style="font-size:9px;font-weight:700;color:var(--muted)">NEW BUG</div></div>
          <div style="background:var(--green-l);border:1px solid rgba(56,161,105,.2);border-radius:6px;padding:9px;text-align:center"><div style="font-size:22px;font-weight:800;color:var(--green)">{bug_closed}</div><div style="font-size:9px;font-weight:700;color:var(--muted)">CLOSED</div></div>
          <div style="background:#f0fdf4;border:1px solid rgba(56,161,105,.2);border-radius:6px;padding:9px;text-align:center"><div style="font-size:22px;font-weight:800;color:#22c55e">{bug_res}</div><div style="font-size:9px;font-weight:700;color:var(--muted)">RESOLVED</div></div>
          <div style="background:var(--amber-l);border:1px solid rgba(217,119,6,.2);border-radius:6px;padding:9px;text-align:center"><div style="font-size:22px;font-weight:800;color:var(--amber)">{bug_bl}</div><div style="font-size:9px;font-weight:700;color:var(--muted)">BACKLOG</div></div>
        </div>
        <div style="background:#f8fafc;border-radius:6px;padding:10px 12px;border:1px solid var(--border)">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px"><span style="font-size:11px;font-weight:700">Bug Closure Rate</span><span style="font-family:'JetBrains Mono',monospace;font-size:13px;font-weight:700">{d['bug_closure']}%</span></div>
          <div style="height:10px;background:var(--bg);border-radius:4px;overflow:hidden;border:1px solid var(--border)"><div style="height:100%;width:{d['bug_closure']}%;background:linear-gradient(90deg,#38a169,#34d399);border-radius:3px"></div></div>
        </div>
        <div style="margin-top:10px;display:flex;gap:6px">
          <div style="flex:1;background:var(--red-l);border:1px solid rgba(229,62,62,.15);border-radius:5px;padding:6px;text-align:center"><div style="font-size:14px;font-weight:800;color:var(--red)">{bug_p_crit}</div><div style="font-size:9px;color:var(--muted)">CRITICAL</div></div>
          <div style="flex:1;background:var(--orange-l);border:1px solid rgba(221,107,32,.15);border-radius:5px;padding:6px;text-align:center"><div style="font-size:14px;font-weight:800;color:var(--orange)">{bug_p_major}</div><div style="font-size:9px;color:var(--muted)">MAJOR</div></div>
          <div style="flex:1;background:var(--amber-l);border:1px solid rgba(217,119,6,.15);border-radius:5px;padding:6px;text-align:center"><div style="font-size:14px;font-weight:800;color:var(--amber)">{bug_p_medium}</div><div style="font-size:9px;color:var(--muted)">MEDIUM</div></div>
          <div style="flex:1;background:var(--green-l);border:1px solid rgba(56,161,105,.15);border-radius:5px;padding:6px;text-align:center"><div style="font-size:14px;font-weight:800;color:var(--green)">{bug_p_minor}</div><div style="font-size:9px;color:var(--muted)">LOW</div></div>
        </div>
      </div>
    </div>
    <div class="card"><div class="card-header"><span>👥</span><div class="card-title">Team Workload</div></div><div class="card-body">{workload_html}</div></div>
  </div>
  <div class="stl">📅 Issue Creation Timeline</div>
  <div class="card">
    <div class="card-header"><span>📈</span><div class="card-title">Daily Issue Volume</div><div class="ccount">{d['total']} total</div></div>
    <div class="card-body">
      <div style="display:flex;align-items:flex-end;gap:3px;height:130px;padding:0 4px">{activity_html}</div>
      <div style="display:flex;gap:8px;margin-top:10px">
        <div style="flex:1;padding:7px 12px;background:var(--blue-l);border:1px solid rgba(30,110,245,.2);border-radius:5px;font-size:10px;color:#1e3a8a">📦 Spikes often indicate bulk imports from sprint planning sessions.</div>
        <div style="flex:1;padding:7px 12px;background:var(--red-l);border:1px solid rgba(229,62,62,.2);border-radius:5px;font-size:10px;color:#7f1d1d">🚨 <strong>Largest day:</strong> {spike_cnt} issues on {spike_date}</div>
      </div>
    </div>
  </div>
</div><!-- /dashboard -->

<!-- ════ BOARD ════ -->
<div id="tab-board" class="tab-content">
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">
    <div style="font-size:15px;font-weight:800;color:var(--navy)">Active Sprint Board</div>
    {proj_tags}
    <div style="margin-left:auto"><div class="live-badge"><span class="ldot"></span>{d['total']} issues</div></div>
  </div>
  <div style="background:var(--surface);border:1px solid var(--border);border-radius:var(--r);padding:14px 18px;margin-bottom:12px;box-shadow:var(--sh)">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
      <div style="font-size:12px;font-weight:700">Sprint Completion</div>
      <div style="font-size:11px;color:var(--muted);font-family:'JetBrains Mono',monospace">{d['done_count']} done of {d['total']} = {sprint_done_pct}%</div>
    </div>
    <div class="sh-bar">
      <div style="width:{sprint_done_pct}%;background:linear-gradient(90deg,#38a169,#10b981);display:flex;align-items:center;justify-content:center"><span style="font-size:9px;font-weight:700;color:#fff">Done {d['done_count']}</span></div>
      <div style="width:{sprint_ip_pct}%;background:linear-gradient(90deg,#1e6ef5,#3b82f6)"></div>
      <div style="flex:1;background:#e2e8f2;display:flex;align-items:center;justify-content:center"><span style="font-size:9px;color:var(--muted)">To Do / Backlog</span></div>
    </div>
    <div style="display:flex;gap:12px;margin-top:6px;font-size:10px;font-family:'JetBrains Mono',monospace">
      <span style="color:#38a169">■ Done {d['done_count']}</span>
      <span style="color:#1e6ef5">■ In Progress {d['inprog_count']}</span>
      <span style="color:#e53e3e">■ Bugs {d['bug_statuses'].get('New Bug',0)}</span>
      <span style="color:#94a3b8">■ To Do {d['todo_count']}</span>
    </div>
  </div>
  <div class="board-wrap">{board_html}</div>
</div><!-- /board -->

<!-- ════ BACKLOG ════ -->
<div id="tab-backlog" class="tab-content">
  <div style="font-size:15px;font-weight:800;color:var(--navy);margin-bottom:12px">Product Backlog</div>
  <div class="row g4" style="margin-bottom:14px">
    <div style="background:var(--surface);border:1px solid var(--border);border-radius:var(--r);padding:12px 16px;box-shadow:var(--sh);border-top:3px solid var(--red)"><div style="font-size:22px;font-weight:800;color:var(--red)">{d['open_bug_count']}</div><div style="font-size:11px;font-weight:600;color:var(--muted);margin-top:2px">OPEN BUGS</div></div>
    <div style="background:var(--surface);border:1px solid var(--border);border-radius:var(--r);padding:12px 16px;box-shadow:var(--sh);border-top:3px solid #0891b2"><div style="font-size:22px;font-weight:800;color:#0891b2">{d['issue_types'].get('Test',0)}</div><div style="font-size:11px;font-weight:600;color:var(--muted);margin-top:2px">TEST CASES</div></div>
    <div style="background:var(--surface);border:1px solid var(--border);border-radius:var(--r);padding:12px 16px;box-shadow:var(--sh);border-top:3px solid #94a3b8"><div style="font-size:22px;font-weight:800;color:#94a3b8">{d['todo_count']}</div><div style="font-size:11px;font-weight:600;color:var(--muted);margin-top:2px">TO DO</div></div>
    <div style="background:var(--surface);border:1px solid var(--border);border-radius:var(--r);padding:12px 16px;box-shadow:var(--sh);border-top:3px solid var(--amber)"><div style="font-size:22px;font-weight:800;color:var(--amber)">{approval_count}</div><div style="font-size:11px;font-weight:600;color:var(--muted);margin-top:2px">APPROVALS</div></div>
  </div>
  <div class="stl">🐛 Open Bugs</div>
  <div class="bl-table-wrap" style="margin-bottom:14px">
    <table class="tbl"><thead><tr><th>Key</th><th>Summary</th><th>Priority</th><th>Phase</th><th>Assignee</th><th>Created</th><th>Status</th></tr></thead>
    <tbody>{bug_rows_html}</tbody></table>
  </div>
  <div class="stl">📋 Tasks — On Hold &amp; To Do</div>
  <div class="bl-table-wrap" style="margin-bottom:14px">
    <table class="tbl"><thead><tr><th>Key</th><th>Summary</th><th>Type</th><th>Assignee</th><th>Status</th></tr></thead>
    <tbody>{task_rows_html}</tbody></table>
  </div>
  <div class="stl">🔐 Pending Approvals</div>
  <div class="bl-table-wrap">
    <table class="tbl"><thead><tr><th>Key</th><th>Summary</th><th>Project</th><th>Assignee</th><th>Stage</th></tr></thead>
    <tbody>{appr_rows_html}</tbody></table>
  </div>
</div><!-- /backlog -->

<!-- ════ REPORTS ════ -->
<div id="tab-reports" class="tab-content">
  <div style="font-size:15px;font-weight:800;color:var(--navy);margin-bottom:14px">Analytics &amp; Reports</div>
  <div class="row g2">
    <div class="card" style="grid-column:span 2"><div class="card-header"><span>🏆</span><div class="card-title">Resolution Rate by Assignee</div><div class="ccount">{len(sorted_assignees)} members</div></div><div class="card-body">{res_bar_html}</div></div>
  </div>
  <div class="row g2">
    <div class="card"><div class="card-header"><span>📢</span><div class="card-title">Issues by Reporter</div></div><div class="card-body">{rep_bar_html}</div></div>
    <div class="card"><div class="card-header"><span>🧪</span><div class="card-title">Test Cases by Phase</div></div>
      <div class="card-body">
        <div class="donut-wrap">
          <svg width="110" height="110" viewBox="0 0 110 110">
            <circle cx="55" cy="55" r="42" fill="none" stroke="#f1f5f9" stroke-width="18"/>
            {tp_circles}
            <text x="55" y="51" text-anchor="middle" fill="#1e293b" font-size="13" font-family="Plus Jakarta Sans,sans-serif" font-weight="800">{sum(tp.values())}</text>
            <text x="55" y="63" text-anchor="middle" fill="#94a3b8" font-size="7">tests</text>
          </svg>
          <div class="leg">{tp_legend}</div>
        </div>
      </div>
    </div>
  </div>
  <div class="stl">💡 Risk &amp; Performance Insights</div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:14px">{risk_html}</div>
  <div class="row g3">
    <div style="background:var(--surface);border:1px solid var(--border);border-radius:var(--r);padding:14px;box-shadow:var(--sh);text-align:center;border-top:3px solid var(--green)"><div style="font-size:28px;font-weight:800;color:var(--green)">{d['bug_closure']}%</div><div style="font-size:11px;font-weight:600;color:var(--muted);margin-top:4px">BUG CLOSURE RATE</div></div>
    <div style="background:var(--surface);border:1px solid var(--border);border-radius:var(--r);padding:14px;box-shadow:var(--sh);text-align:center;border-top:3px solid var(--blue)"><div style="font-size:28px;font-weight:800;color:var(--blue)">{round(d['done_count']/d['total']*100,1) if d['total'] else 0}%</div><div style="font-size:11px;font-weight:600;color:var(--muted);margin-top:4px">ISSUE CLOSURE RATE</div></div>
    <div style="background:var(--surface);border:1px solid var(--border);border-radius:var(--r);padding:14px;box-shadow:var(--sh);text-align:center;border-top:3px solid var(--amber)"><div style="font-size:28px;font-weight:800;color:var(--amber)">{d['unassigned']}</div><div style="font-size:11px;font-weight:600;color:var(--muted);margin-top:4px">UNASSIGNED ISSUES</div></div>
  </div>
</div><!-- /reports -->

<!-- ════ RELEASES ════ -->
<div id="tab-releases" class="tab-content">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">
    <div style="font-size:15px;font-weight:800;color:var(--navy)">Release Readiness</div>
    <div style="font-size:11px;color:var(--muted);font-family:'JetBrains Mono',monospace" id="relDate">Snapshot: {fetched_str}</div>
  </div>
  {rel_html}
</div><!-- /releases -->

<!-- ════ FOOTER ════ -->
<div class="footer">
  <div style="display:flex;align-items:center;gap:10px">
    <img src="{logo_src}" alt="Jira Intelligence Dashboard" style="height:22px;width:auto;object-fit:contain;opacity:.7"/>
    <div>
      <div class="footer-brand">Jira Intelligence Dashboard</div>
      <div style="font-size:10px;color:var(--muted);margin-top:1px" id="footerCopy"></div>
    </div>
  </div>
  <div style="display:flex;gap:16px;font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--muted)">
    <span>{d['total']} issues</span><span>{len(d['projects'])} projects</span>
    <span>{len(d['assignee_stats'])} members</span><span>5 tabs</span>
  </div>
  <div style="text-align:right">
    <div style="font-size:10px;color:var(--muted);font-family:'JetBrains Mono',monospace" id="footerDate"></div>
    <div style="font-size:10px;color:var(--muted);margin-top:1px">Last synced: {fetched_str}</div>
  </div>
</div>

</div><!-- /content -->

<script>
function switchTab(id, el) {{
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById('tab-' + id).classList.add('active');
  el.classList.add('active');
}}

function showRefresh(e) {{
  e.preventDefault();
  document.getElementById('refreshOverlay').classList.add('show');
  window.location.href = '/refresh';
}}

// ── Live clock ─────────────────────────────
function pad(n) {{ return String(n).padStart(2,'0'); }}
const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const DAYS   = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];

function updateClock() {{
  const now  = new Date();
  const day  = DAYS[now.getDay()];
  const date = pad(now.getDate()) + ' ' + MONTHS[now.getMonth()] + ' ' + now.getFullYear();
  const time = pad(now.getHours()) + ':' + pad(now.getMinutes()) + ':' + pad(now.getSeconds());
  const full = day + ' · ' + date + '  ' + time;

  const c = document.getElementById('liveClock');
  if (c) c.textContent = full;

  const fd = document.getElementById('footerDate');
  if (fd) fd.textContent = day + ', ' + date + ' · ' + time;

  const fc = document.getElementById('footerCopy');
  if (fc) fc.textContent = '© ' + now.getFullYear() + ' Arunakumar Tavva. All rights reserved.';
}}
updateClock();
setInterval(updateClock, 1000);
</script>
</body>
</html>"""


# ─────────────────────────────────────────────────────────────
#  PEOPLE INTELLIGENCE HTML
# ─────────────────────────────────────────────────────────────

def build_people_html(pd, logo_src, fetched_at=None, jira_url=""):
    """People Intelligence Dashboard — 4-tab CEO-grade productivity & gamification report."""

    PCOLORS = ["#1e6ef5","#0891b2","#7c3aed","#38a169","#d97706","#e53e3e",
               "#f97316","#ec4899","#14b8a6","#8b5cf6","#84cc16","#94a3b8"]

    def av_init(nm):
        parts = (nm or "?").split()
        return ("".join(x[0] for x in parts[:2])).upper() if parts else "?"

    def score_ring(score, size=60):
        r    = size // 2 - 7
        circ = round(2 * 3.14159 * r, 2)
        fill = round(min(score, 100) / 100 * circ, 2)
        gap  = round(circ - fill, 2)
        col  = "#38a169" if score >= 70 else ("#d97706" if score >= 45 else "#e53e3e")
        cx = cy = size // 2
        fs   = "14" if size >= 60 else "11"
        return (f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}" style="flex-shrink:0">'
                f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="#e2e8f2" stroke-width="6"/>'
                f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{col}" stroke-width="6" '
                f'stroke-dasharray="{fill} {gap}" stroke-linecap="round" transform="rotate(-90 {cx} {cy})"/>'
                f'<text x="{cx}" y="{cy+5}" text-anchor="middle" font-size="{fs}" font-weight="800" fill="{col}">'
                f'{score:.0f}</text></svg>')

    def pct_bar(pct, color="#1e6ef5"):
        return (f'<div style="height:6px;background:#e8edf5;border-radius:3px;overflow:hidden;margin-top:3px">'
                f'<div style="height:100%;width:{min(pct,100):.1f}%;background:{color};border-radius:3px"></div></div>')

    def badge_chip(emoji, label, color):
        return (f'<span style="display:inline-flex;align-items:center;gap:3px;background:{color}1a;'
                f'color:{color};border:1px solid {color}44;border-radius:20px;padding:2px 8px;'
                f'font-size:10px;font-weight:700;white-space:nowrap">{emoji} {label}</span>')

    def tier_tag(label, color):
        return (f'<span style="background:{color}1a;color:{color};border:1px solid {color}44;'
                f'border-radius:6px;padding:2px 8px;font-size:10px;font-weight:700;'
                f'white-space:nowrap">{label}</span>')

    ts      = fetched_at.strftime("%d %b %Y  %H:%M") if fetched_at else "—"
    tm_res  = pd["team_res_rate"]
    tm_col  = "#38a169" if tm_res >= 60 else ("#d97706" if tm_res >= 30 else "#e53e3e")
    avg_res = pd["team_avg_resolve"]
    avg_str = f'{avg_res}d' if avg_res else "N/A"
    avg_col = "#38a169" if avg_res and avg_res <= 7 else ("#d97706" if avg_res and avg_res <= 21 else "#94a3b8")
    bc_col  = "#38a169" if pd["team_bug_closure"] >= 60 else ("#d97706" if pd["team_bug_closure"] >= 30 else "#e53e3e")

    # ── KPI cards ──────────────────────────────────────────────────────────────
    kpis = [
        (str(pd["active_members"]),       "Active Team Members",     f'{pd["total_members"]} total tracked',                   "#1e6ef5"),
        (f'{tm_res}%',                     "Team Resolution Rate",    f'{pd["team_done"]} of {pd["team_total"]} issues closed',  tm_col),
        (avg_str,                          "Avg Days to Close",       "Mean issue resolution velocity",                         avg_col),
        (f'{pd["team_bug_closure"]}%',     "Bug Closure Rate",        f'{pd["team_bugs_done"]}/{pd["team_bugs"]} bugs fixed',    bc_col),
        (str(pd["exc_count"]),             "Excellent Performers",    "Operating at top tier",                                  "#38a169"),
        (str(pd["imp_count"]),             "Needs Attention",         "Below performance threshold",                            "#e53e3e"),
    ]
    kpi_html = "".join(
        f'<div style="background:#fff;border:1.5px solid #e2e8f2;border-top:3px solid {c};border-radius:14px;padding:20px 18px">'
        f'<div style="font-size:30px;font-weight:800;color:{c};line-height:1.1">{v}</div>'
        f'<div style="font-size:12px;font-weight:700;color:#0f2d5e;margin-top:5px">{l}</div>'
        f'<div style="font-size:11px;color:#94a3b8;margin-top:2px">{s}</div></div>'
        for v, l, s, c in kpis
    )

    # ── Top 3 podium ────────────────────────────────────────────────────────────
    PODIUM = [
        ("#d97706", "#fffbeb", "border:2px solid #fde68a"),
        ("#64748b", "#f8fafc", "border:2px solid #e2e8f2"),
        ("#b45309", "#fff7ed", "border:2px solid #fed7aa"),
    ]
    podium_html = ""
    for i, p in enumerate(pd["top3"]):
        mc, bg, brd = PODIUM[i]
        bdg_s   = " ".join(badge_chip(e, l, mc) for e, l in p["badges"])
        spd_str = f'{p["avg_resolve_days"]}d avg' if p["avg_resolve_days"] is not None else "—"
        podium_html += (
            f'<div style="background:{bg};{brd};border-radius:16px;padding:22px;flex:1;min-width:220px;position:relative">'
            f'  <div style="position:absolute;top:14px;right:16px;font-size:28px">{p["medal"]}</div>'
            f'  <div style="display:flex;align-items:center;gap:12px;margin-bottom:16px">'
            f'    <div style="width:50px;height:50px;border-radius:50%;background:{p["color"]};display:flex;'
            f'align-items:center;justify-content:center;font-size:17px;font-weight:800;color:#fff;flex-shrink:0">'
            f'{av_init(p["name"])}</div>'
            f'    <div style="flex:1;padding-right:40px">'
            f'      <div style="font-size:14px;font-weight:800;color:#0f2d5e">{p["name"]}</div>'
            f'      <div style="margin-top:5px">{tier_tag(p["tier_label"], p["tier_color"])}</div>'
            f'    </div>'
            f'  </div>'
            f'  <div style="display:flex;align-items:center;gap:14px;margin-bottom:16px">'
            f'    {score_ring(p["productivity_score"], 72)}'
            f'    <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;flex:1">'
            f'      <div style="background:rgba(255,255,255,.75);border-radius:8px;padding:8px;text-align:center">'
            f'        <div style="font-size:20px;font-weight:800;color:#0f2d5e">{p["total"]}</div>'
            f'        <div style="font-size:9px;color:#64748b;font-weight:700;text-transform:uppercase">Assigned</div></div>'
            f'      <div style="background:rgba(255,255,255,.75);border-radius:8px;padding:8px;text-align:center">'
            f'        <div style="font-size:20px;font-weight:800;color:#38a169">{p["done"]}</div>'
            f'        <div style="font-size:9px;color:#64748b;font-weight:700;text-transform:uppercase">Done</div></div>'
            f'      <div style="background:rgba(255,255,255,.75);border-radius:8px;padding:8px;text-align:center">'
            f'        <div style="font-size:20px;font-weight:800;color:{mc}">{p["resolution_rate"]}%</div>'
            f'        <div style="font-size:9px;color:#64748b;font-weight:700;text-transform:uppercase">Rate</div></div>'
            f'      <div style="background:rgba(255,255,255,.75);border-radius:8px;padding:8px;text-align:center">'
            f'        <div style="font-size:20px;font-weight:800;color:{avg_col}">{spd_str}</div>'
            f'        <div style="font-size:9px;color:#64748b;font-weight:700;text-transform:uppercase">Speed</div></div>'
            f'    </div>'
            f'  </div>'
            f'  <div style="display:flex;flex-wrap:wrap;gap:4px">'
            + (f'    {bdg_s}</div>' if bdg_s else '    <span style="font-size:11px;color:#94a3b8">No badges yet</span></div>')
            + f'</div>'
        )

    # ── Performance distribution donut ─────────────────────────────────────────
    ac       = max(pd["active_members"], 1)
    exc_pct  = round(pd["exc_count"]  / ac * 100)
    good_pct = round(pd["good_count"] / ac * 100)
    imp_pct  = round(pd["imp_count"]  / ac * 100)
    circ_d   = 251.33
    exc_da   = round(exc_pct  / 100 * circ_d, 1)
    good_da  = round(good_pct / 100 * circ_d, 1)
    imp_da   = round(imp_pct  / 100 * circ_d, 1)
    good_off = round(exc_da, 1)
    imp_off  = round(exc_da + good_da, 1)
    dist_svg = (
        f'<svg width="130" height="130" viewBox="0 0 130 130">'
        f'<circle cx="65" cy="65" r="40" fill="none" stroke="#e8edf5" stroke-width="20"/>'
        f'<circle cx="65" cy="65" r="40" fill="none" stroke="#38a169" stroke-width="20" '
        f'stroke-dasharray="{exc_da} {circ_d-exc_da}" stroke-dashoffset="0" transform="rotate(-90 65 65)"/>'
        f'<circle cx="65" cy="65" r="40" fill="none" stroke="#d97706" stroke-width="20" '
        f'stroke-dasharray="{good_da} {circ_d-good_da}" stroke-dashoffset="-{good_off}" transform="rotate(-90 65 65)"/>'
        f'<circle cx="65" cy="65" r="40" fill="none" stroke="#e53e3e" stroke-width="20" '
        f'stroke-dasharray="{imp_da} {circ_d-imp_da}" stroke-dashoffset="-{imp_off}" transform="rotate(-90 65 65)"/>'
        f'<text x="65" y="61" text-anchor="middle" font-size="24" font-weight="800" fill="#0f2d5e">{ac}</text>'
        f'<text x="65" y="76" text-anchor="middle" font-size="10" fill="#64748b">Active</text>'
        f'</svg>'
    )

    # ── CEO Insights ───────────────────────────────────────────────────────────
    ins_items = []
    if pd["top3"]:
        best = pd["top3"][0]
        ins_items.append(f'🏆 <strong>{best["name"]}</strong> leads the team — '
                         f'<strong>{best["productivity_score"]:.0f}/100 score</strong>, '
                         f'<strong>{best["resolution_rate"]}%</strong> resolution across '
                         f'<strong>{best["total"]}</strong> issues. A true team champion.')
    if pd["needs_help"]:
        nh = pd["needs_help"][0]
        ins_items.append(f'⚠️ <strong>{nh["name"]}</strong> has <strong>{nh["total"]} assigned issues</strong> '
                         f'with only <strong>{nh["resolution_rate"]}%</strong> resolved. '
                         f'Immediate coaching or workload review recommended.')
    if pd["team_avg_resolve"]:
        qual = ("Excellent velocity." if pd["team_avg_resolve"] <= 7
                else "Room to improve resolution speed." if pd["team_avg_resolve"] > 14 else "Solid pace.")
        ins_items.append(f'⏱️ Team resolves issues in <strong>{pd["team_avg_resolve"]} days</strong> on average. {qual}')
    ins_items.append(
        f'📊 Overall team resolution rate: <strong>{pd["team_res_rate"]}%</strong> '
        f'across <strong>{pd["team_total"]}</strong> issues. '
        f'{"Strong execution overall." if pd["team_res_rate"] >= 60 else "Focused backlog clearance will drive visible improvement."}'
    )
    if pd["exc_count"] > 0:
        ins_items.append(
            f'⭐ <strong>{pd["exc_count"]} member{"s" if pd["exc_count"]>1 else ""}</strong> at Excellent tier — '
            f'consider public recognition to reinforce high-performance culture.'
        )
    ceo_ins_html = "".join(
        f'<div style="padding:10px 0;border-bottom:1px solid #fde68a;font-size:12.5px;color:#374151;line-height:1.6">{s}</div>'
        for s in ins_items
    )

    # ── Leaderboard rows ──────────────────────────────────────────────────────
    lb_rows = ""
    for p in pd["active"]:
        spd   = f'{p["avg_resolve_days"]}d' if p["avg_resolve_days"] is not None else "—"
        spc   = ("#38a169" if p["avg_resolve_days"] and p["avg_resolve_days"] <= 7
                 else "#d97706" if p["avg_resolve_days"] and p["avg_resolve_days"] <= 14
                 else "#94a3b8")
        bug_s = f'{p["bugs_done"]}/{p["bugs_total"]}' if p["bugs_total"] else "—"
        bdg_s = " ".join(f'<span title="{l}" style="font-size:16px;cursor:help">{e}</span>' for e, l in p["badges"])
        rnk   = p["medal"] or str(p["rank"])
        rnk_fs = "20" if p["medal"] else "13"
        lb_rows += (
            f'<tr style="border-bottom:1px solid #f1f5f9" onmouseover="this.style.background=\'#f8fafc\'" onmouseout="this.style.background=\'\'">'
            f'<td style="padding:12px 10px;text-align:center;font-size:{rnk_fs}px;font-weight:700;color:#94a3b8">{rnk}</td>'
            f'<td style="padding:12px 10px">'
            f'  <div style="display:flex;align-items:center;gap:10px">'
            f'    <div style="width:36px;height:36px;border-radius:50%;background:{p["color"]};display:flex;align-items:center;'
            f'justify-content:center;font-size:12px;font-weight:800;color:#fff;flex-shrink:0">{av_init(p["name"])}</div>'
            f'    <div>'
            f'      <div style="font-size:13px;font-weight:700;color:#0f2d5e">{p["name"]}</div>'
            f'      <div style="font-size:10px;color:#94a3b8">{", ".join(p["projects"][:2]) or "—"}</div>'
            f'    </div>'
            f'  </div>'
            f'</td>'
            f'<td style="padding:12px 10px;text-align:center">{score_ring(p["productivity_score"], 46)}</td>'
            f'<td style="padding:12px 10px;text-align:center;font-size:15px;font-weight:800;color:#0f2d5e">{p["total"]}</td>'
            f'<td style="padding:12px 10px;text-align:center;font-size:15px;font-weight:800;color:#38a169">{p["done"]}</td>'
            f'<td style="padding:12px 10px;min-width:110px">'
            f'  <div style="font-size:12px;font-weight:700;color:{p["tier_color"]}">{p["resolution_rate"]}%</div>'
            f'  {pct_bar(p["resolution_rate"], p["tier_color"])}'
            f'</td>'
            f'<td style="padding:12px 10px;text-align:center;font-size:13px;font-weight:700;color:{spc}">{spd}</td>'
            f'<td style="padding:12px 10px;text-align:center;font-size:12px;color:#64748b">{bug_s}</td>'
            f'<td style="padding:12px 10px">{tier_tag(p["tier_label"], p["tier_color"])}</td>'
            f'<td style="padding:12px 10px;font-size:16px">{bdg_s}</td>'
            f'</tr>'
        )

    # ── Individual scorecards ─────────────────────────────────────────────────
    scorecards_html = ""
    for p in pd["people"]:
        bdg_s      = " ".join(badge_chip(e, l, p["color"]) for e, l in p["badges"])
        spd_str    = f'{p["avg_resolve_days"]} days avg' if p["avg_resolve_days"] is not None else "No resolved data"
        proj_s     = ", ".join(p["projects"][:3]) or "—"
        it_top     = sorted(p["issue_types"].items(), key=lambda x: -x[1])[:3]
        it_str     = " · ".join(f'{v} {k}' for k, v in it_top) or "—"
        bug_detail = f'{p["bugs_done"]}/{p["bugs_total"]} bugs closed' if p["bugs_total"] else "No bugs"
        crit_det   = f'{p["critical_done"]}/{p["critical_total"]} critical resolved' if p["critical_total"] else "No critical"
        scorecards_html += (
            f'<div data-tier="{p["tier"]}" style="background:#fff;border:1.5px solid #e8edf5;'
            f'border-top:3px solid {p["tier_color"]};border-radius:14px;padding:20px">'
            f'  <div style="display:flex;align-items:flex-start;gap:12px;margin-bottom:16px">'
            f'    <div style="width:46px;height:46px;border-radius:50%;background:{p["color"]};display:flex;align-items:center;'
            f'justify-content:center;font-size:16px;font-weight:800;color:#fff;flex-shrink:0">{av_init(p["name"])}</div>'
            f'    <div style="flex:1;min-width:0">'
            f'      <div style="font-size:13px;font-weight:800;color:#0f2d5e">{p["medal"]} {p["name"]}</div>'
            f'      <div style="margin-top:4px">{tier_tag(p["tier_label"], p["tier_color"])}</div>'
            f'      <div style="font-size:10px;color:#94a3b8;margin-top:4px">📁 {proj_s}</div>'
            f'    </div>'
            f'    {score_ring(p["productivity_score"], 54)}'
            f'  </div>'
            f'  <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px;margin-bottom:14px">'
            f'    <div style="background:#f8fafc;border-radius:8px;padding:9px;text-align:center">'
            f'      <div style="font-size:20px;font-weight:800;color:#0f2d5e">{p["total"]}</div>'
            f'      <div style="font-size:9px;color:#94a3b8;font-weight:700;text-transform:uppercase">Assigned</div></div>'
            f'    <div style="background:#f0fff4;border-radius:8px;padding:9px;text-align:center">'
            f'      <div style="font-size:20px;font-weight:800;color:#38a169">{p["done"]}</div>'
            f'      <div style="font-size:9px;color:#94a3b8;font-weight:700;text-transform:uppercase">Done</div></div>'
            f'    <div style="background:#f8fafc;border-radius:8px;padding:9px;text-align:center">'
            f'      <div style="font-size:20px;font-weight:800;color:{p["tier_color"]}">{p["resolution_rate"]}%</div>'
            f'      <div style="font-size:9px;color:#94a3b8;font-weight:700;text-transform:uppercase">Rate</div></div>'
            f'  </div>'
            f'  <div style="display:flex;flex-direction:column;gap:8px;margin-bottom:12px">'
            f'    <div>'
            f'      <div style="display:flex;justify-content:space-between;font-size:10px;font-weight:600;color:#64748b;margin-bottom:2px">'
            f'        <span>Resolution Rate</span><span style="color:{p["tier_color"]}">{p["resolution_rate"]}%</span></div>'
            f'      {pct_bar(p["resolution_rate"], p["tier_color"])}'
            f'    </div>'
            f'    <div>'
            f'      <div style="display:flex;justify-content:space-between;font-size:10px;font-weight:600;color:#64748b;margin-bottom:2px">'
            f'        <span>Bug Closure</span><span style="color:#e53e3e">{p["bug_closure_rate"]}%</span></div>'
            f'      {pct_bar(p["bug_closure_rate"], "#e53e3e")}'
            f'    </div>'
            f'    <div>'
            f'      <div style="display:flex;justify-content:space-between;font-size:10px;font-weight:600;color:#64748b;margin-bottom:2px">'
            f'        <span>Critical Issues</span><span style="color:#7c3aed">{p["critical_closure_rate"]}%</span></div>'
            f'      {pct_bar(p["critical_closure_rate"], "#7c3aed")}'
            f'    </div>'
            f'  </div>'
            f'  <div style="font-size:10px;color:#64748b;padding:8px;background:#f8fafc;border-radius:7px;margin-bottom:8px">'
            f'    ⏱️ {spd_str} &nbsp;·&nbsp; 🐛 {bug_detail} &nbsp;·&nbsp; 💎 {crit_det}</div>'
            f'  <div style="font-size:10px;color:#94a3b8;margin-bottom:8px">{it_str}</div>'
            f'  <div style="display:flex;flex-wrap:wrap;gap:4px">{bdg_s}</div>'
            f'</div>'
        )

    # ── Needs attention cards ─────────────────────────────────────────────────
    needs_html = ""
    for p in pd["needs_help"]:
        reasons = []
        if p["resolution_rate"] < 20:    reasons.append(f'Only {p["resolution_rate"]}% resolved')
        if p["on_hold_rate"] > 30:        reasons.append(f'{p["on_hold_rate"]}% on hold')
        if p["bugs_total"] > 0 and p["bug_closure_rate"] < 25:
            reasons.append(f'{p["bugs_total"]-p["bugs_done"]} open bugs')
        reason_text = " · ".join(reasons) if reasons else "Low productivity score"
        needs_html += (
            f'<div style="background:#fff5f5;border:1.5px solid #fecaca;border-radius:12px;padding:16px;'
            f'display:flex;align-items:center;gap:14px">'
            f'  <div style="width:44px;height:44px;border-radius:50%;background:{p["color"]};display:flex;align-items:center;'
            f'justify-content:center;font-size:15px;font-weight:800;color:#fff;flex-shrink:0">{av_init(p["name"])}</div>'
            f'  <div style="flex:1">'
            f'    <div style="font-size:13px;font-weight:700;color:#7f1d1d">{p["name"]}</div>'
            f'    <div style="font-size:11px;color:#dc2626;margin-top:2px">{reason_text}</div>'
            f'    <div style="font-size:11px;color:#64748b;margin-top:3px">'
            f'      {p["total"]} assigned · {p["done"]} done · {p["in_progress"]} in progress · {p["on_hold"]} on hold</div>'
            f'    <div style="font-size:10px;color:#94a3b8;margin-top:4px;font-style:italic">'
            f'      💡 Recommended: Schedule 1-on-1, review blockers, consider workload redistribution</div>'
            f'  </div>'
            f'  {score_ring(p["productivity_score"], 46)}'
            f'</div>'
        )
    if not needs_html:
        needs_html = (
            '<div style="text-align:center;padding:40px;background:#f0fff4;border-radius:12px;border:1.5px solid #bbf7d0">'
            '<div style="font-size:36px;margin-bottom:10px">🎉</div>'
            '<div style="font-size:15px;font-weight:700;color:#15803d">All active team members are performing well!</div>'
            '<div style="font-size:12px;color:#16a34a;margin-top:6px">Keep up the great work team.</div>'
            '</div>'
        )

    # ── Champions spotlight ───────────────────────────────────────────────────
    champs_html = ""
    for p in pd["top3"]:
        bdg_s = " ".join(badge_chip(e, l, p["color"]) for e, l in p["badges"])
        champs_html += (
            f'<div style="background:#f0fff4;border:1.5px solid #bbf7d0;border-radius:12px;padding:16px;'
            f'display:flex;align-items:center;gap:14px">'
            f'  <div style="font-size:28px;flex-shrink:0">{p["medal"]}</div>'
            f'  <div style="width:42px;height:42px;border-radius:50%;background:{p["color"]};display:flex;align-items:center;'
            f'justify-content:center;font-size:14px;font-weight:800;color:#fff;flex-shrink:0">{av_init(p["name"])}</div>'
            f'  <div style="flex:1">'
            f'    <div style="font-size:13px;font-weight:700;color:#14532d">{p["name"]}</div>'
            f'    <div style="font-size:11px;color:#16a34a;margin-top:2px">'
            f'      {p["productivity_score"]:.0f}/100 score · {p["resolution_rate"]}% resolution · {p["done"]} issues closed</div>'
            f'    <div style="display:flex;flex-wrap:wrap;gap:3px;margin-top:6px">{bdg_s}</div>'
            f'  </div>'
            f'  {score_ring(p["productivity_score"], 46)}'
            f'</div>'
        )

    # ── Recommendations ───────────────────────────────────────────────────────
    recs = []
    if pd["team_res_rate"] < 50:
        recs.append(("📋", "Schedule a backlog grooming session — over half of issues remain unresolved."))
    if pd["imp_count"] > 0:
        recs.append(("👥", f'Initiate 1-on-1 coaching for the {pd["imp_count"]} member{"s" if pd["imp_count"]>1 else ""} in the Needs Improvement tier.'))
    if pd["team_avg_resolve"] and pd["team_avg_resolve"] > 14:
        recs.append(("⚡", "Resolution time exceeds 2 weeks. Implement daily blockers tracking to accelerate."))
    if pd["exc_count"] > 0:
        recs.append(("🏅", f'Publicly recognize your top {pd["exc_count"]} performer{"s" if pd["exc_count"]>1 else ""} in the next all-hands meeting.'))
    recs.append(("📊", "Share this People Intelligence report with the team to celebrate wins and set improvement goals."))
    recs.append(("🎯", "Set individual OKRs based on these metrics to drive focused improvement next sprint."))
    recs_html = "".join(
        f'<div style="display:flex;align-items:flex-start;gap:12px;padding:12px 14px;background:#f8fafc;'
        f'border-radius:10px;border:1px solid #e8edf5">'
        f'<span style="font-size:20px;flex-shrink:0;margin-top:1px">{em}</span>'
        f'<span style="font-size:12px;color:#374151;line-height:1.6">{txt}</span></div>'
        for em, txt in recs
    )

    jurl_html = (f'<a href="{jira_url}" target="_blank" style="color:#1e6ef5;text-decoration:none">'
                 f'{jira_url.replace("https://","")}</a>') if jira_url else "Jira"

    # ── CSS (regular string — no f-string so no brace escaping) ────────────────
    CSS = """@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap');
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Plus Jakarta Sans',sans-serif;background:#f0f4f8;min-height:100vh;color:#1e293b}
.pi-nav{background:#0f2d5e;color:#fff;padding:0 24px;display:flex;align-items:center;gap:0;height:56px;position:sticky;top:0;z-index:100;box-shadow:0 2px 16px rgba(0,0,0,.35)}
.pi-logo{display:flex;align-items:center;gap:10px;text-decoration:none;color:#fff;flex-shrink:0;margin-right:20px}
.pi-logo img{height:28px;width:auto}
.pi-logo-txt{display:flex;flex-direction:column}
.pi-logo-name{font-size:13px;font-weight:800;letter-spacing:.3px;line-height:1.2}
.pi-logo-sub{font-size:9px;font-weight:500;color:rgba(255,255,255,.5);text-transform:uppercase;letter-spacing:1px}
.pi-tabs{display:flex;align-items:center;gap:2px;flex:1}
.pi-tab{padding:8px 16px;border-radius:7px;font-size:12px;font-weight:700;color:rgba(255,255,255,.6);cursor:pointer;border:none;background:transparent;transition:all .15s;letter-spacing:.3px;font-family:'Plus Jakarta Sans',sans-serif}
.pi-tab:hover{color:#fff;background:rgba(255,255,255,.12)}
.pi-tab.active{color:#fff;background:rgba(255,255,255,.2)}
.pi-nav-r{display:flex;align-items:center;gap:8px;flex-shrink:0}
.pi-clock{font-size:10.5px;color:rgba(255,255,255,.6);font-family:'JetBrains Mono',monospace}
.pi-btn{padding:6px 13px;border-radius:7px;font-size:11px;font-weight:700;cursor:pointer;border:none;font-family:'Plus Jakarta Sans',sans-serif;transition:opacity .2s;text-decoration:none;display:inline-flex;align-items:center;gap:5px}
.pi-btn-s{background:rgba(255,255,255,.12);color:#fff}.pi-btn-s:hover{background:rgba(255,255,255,.2)}
.pi-btn-p{background:#1e6ef5;color:#fff}.pi-btn-p:hover{opacity:.88}
.pi-tc{display:none}.pi-tc.active{display:block}
.pi-pg{max-width:1400px;margin:0 auto;padding:24px}
.pi-kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(175px,1fr));gap:14px;margin-bottom:28px}
.pi-g2{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:28px}
.pi-card{background:#fff;border:1.5px solid #e8edf5;border-radius:14px;padding:22px}
.pi-sec{font-size:15px;font-weight:800;color:#0f2d5e;margin-bottom:16px;display:flex;align-items:center;gap:8px}
.pi-pod{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:28px}
.pi-lb{width:100%;border-collapse:collapse}
.pi-lb th{padding:10px 10px;text-align:left;font-size:10px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.5px;border-bottom:2px solid #e8edf5;background:#f8fafc;white-space:nowrap}
.pi-lb th:first-child{text-align:center}
.pi-cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(265px,1fr));gap:16px}
.pi-fbar{display:flex;gap:8px;margin-bottom:20px;flex-wrap:wrap}
.pi-fbtn{padding:6px 16px;border-radius:20px;font-size:11px;font-weight:700;cursor:pointer;border:1.5px solid #e2e8f2;background:#fff;color:#64748b;transition:all .15s;font-family:'Plus Jakarta Sans',sans-serif}
.pi-fbtn.active,.pi-fbtn:hover{border-color:#1e6ef5;color:#1e6ef5;background:#eff6ff}
footer{background:#0f2d5e;color:rgba(255,255,255,.45);text-align:center;padding:20px;font-size:11px;margin-top:40px}
"""

    nh_count  = len(pd["needs_help"])
    top_count = len(pd["top3"])
    tot_m     = pd["total_members"]
    exc_c     = pd["exc_count"]
    good_c    = pd["good_count"]
    imp_c     = pd["imp_count"]

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Jira Intelligence Dashboard — People Intelligence</title>
<style>{CSS}</style>
</head>
<body>
<nav class="pi-nav">
  <a href="/dashboard" class="pi-logo">
    {'<img src="' + logo_src + '" alt="Jira Intelligence Dashboard"/>' if logo_src else '<span style="font-size:22px">📊</span>'}
    <div class="pi-logo-txt">
      <span class="pi-logo-name">Jira Intelligence</span>
      <span class="pi-logo-sub">People Intelligence</span>
    </div>
  </a>
  <div class="pi-tabs">
    <button class="pi-tab active" onclick="piTab('exec',this)">Executive Summary</button>
    <button class="pi-tab" onclick="piTab('lb',this)">🏅 Leaderboard</button>
    <button class="pi-tab" onclick="piTab('cards',this)">📋 Scorecards</button>
    <button class="pi-tab" onclick="piTab('action',this)">🎯 Action Plan</button>
  </div>
  <div class="pi-nav-r">
    <span class="pi-clock" id="piClock"></span>
    <a href="/dashboard" class="pi-btn pi-btn-s">← Board</a>
    <a href="/refresh" class="pi-btn pi-btn-p">⟳ Refresh</a>
  </div>
</nav>

<!-- ════ EXECUTIVE SUMMARY ════ -->
<div class="pi-tc active" id="tab-exec"><div class="pi-pg">
  <div style="display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:24px;gap:20px;flex-wrap:wrap">
    <div>
      <h1 style="font-size:24px;font-weight:800;color:#0f2d5e;line-height:1.2">People Intelligence</h1>
      <p style="font-size:12px;color:#94a3b8;margin-top:5px">Productivity · Effectiveness · Gamification &nbsp;·&nbsp; {ts} &nbsp;·&nbsp; {jurl_html}</p>
    </div>
    <div style="background:linear-gradient(135deg,#0f2d5e,#1a4f9e);color:#fff;border-radius:12px;padding:14px 20px;text-align:right;flex-shrink:0">
      <div style="font-size:10px;font-weight:700;opacity:.65;text-transform:uppercase;letter-spacing:1.2px">CEO Report</div>
      <div style="font-size:20px;font-weight:800;margin-top:2px">Team Performance</div>
      <div style="font-size:11px;opacity:.65;margin-top:2px">{pd["active_members"]} active members · {pd["team_total"]} issues tracked</div>
    </div>
  </div>
  <div class="pi-kpi">{kpi_html}</div>
  <div class="pi-sec">🏆 Top Performers Spotlight</div>
  <div class="pi-pod">
    {podium_html or '<div style="padding:32px;color:#94a3b8;font-size:13px">Need at least 3 issues per person for rankings.</div>'}
  </div>
  <div class="pi-g2">
    <div class="pi-card">
      <div class="pi-sec">📊 Performance Distribution</div>
      <div style="display:flex;align-items:center;gap:28px;flex-wrap:wrap">
        {dist_svg}
        <div style="display:flex;flex-direction:column;gap:16px">
          <div style="display:flex;align-items:center;gap:12px">
            <div style="width:14px;height:14px;border-radius:4px;background:#38a169;flex-shrink:0"></div>
            <div><span style="font-size:26px;font-weight:800;color:#38a169">{exc_pct}</span>
            <span style="font-size:11px;font-weight:700;color:#64748b;margin-left:4px">Excellent — {pd["exc_count"]} members</span></div>
          </div>
          <div style="display:flex;align-items:center;gap:12px">
            <div style="width:14px;height:14px;border-radius:4px;background:#d97706;flex-shrink:0"></div>
            <div><span style="font-size:26px;font-weight:800;color:#d97706">{good_pct}</span>
            <span style="font-size:11px;font-weight:700;color:#64748b;margin-left:4px">Good — {pd["good_count"]} members</span></div>
          </div>
          <div style="display:flex;align-items:center;gap:12px">
            <div style="width:14px;height:14px;border-radius:4px;background:#e53e3e;flex-shrink:0"></div>
            <div><span style="font-size:26px;font-weight:800;color:#e53e3e">{imp_pct}</span>
            <span style="font-size:11px;font-weight:700;color:#64748b;margin-left:4px">Needs Improvement — {pd["imp_count"]} members</span></div>
          </div>
        </div>
      </div>
      <div style="margin-top:20px;padding:12px;background:#f8fafc;border-radius:10px;border:1px solid #e8edf5">
        <div style="font-size:10px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px">📐 Scoring Formula</div>
        <div style="font-size:11.5px;color:#64748b;line-height:1.8">
          Resolution Rate <strong style="color:#1e6ef5">×40%</strong> &nbsp;+&nbsp;
          Speed <strong style="color:#0891b2">×25%</strong> &nbsp;+&nbsp;
          Bug Closure <strong style="color:#e53e3e">×20%</strong> &nbsp;+&nbsp;
          Critical Handling <strong style="color:#7c3aed">×15%</strong>
        </div>
      </div>
    </div>
    <div class="pi-card">
      <div class="pi-sec">💡 CEO Insights</div>
      <div style="background:linear-gradient(135deg,#fffbeb,#fef3c7);border:1.5px solid #fde68a;border-radius:10px;padding:16px">
        <div style="font-size:10px;font-weight:700;color:#92400e;text-transform:uppercase;letter-spacing:.5px;margin-bottom:10px">🎯 Executive Narrative</div>
        {ceo_ins_html}
      </div>
      <div style="margin-top:16px;display:grid;grid-template-columns:1fr 1fr;gap:8px">
        <div style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:10px;padding:12px;text-align:center">
          <div style="font-size:24px;font-weight:800;color:#1e6ef5">{pd["team_res_rate"]}%</div>
          <div style="font-size:10px;font-weight:700;color:#64748b;text-transform:uppercase;margin-top:2px">Team Resolution</div>
        </div>
        <div style="background:#f0fff4;border:1px solid #bbf7d0;border-radius:10px;padding:12px;text-align:center">
          <div style="font-size:24px;font-weight:800;color:#38a169">{avg_str}</div>
          <div style="font-size:10px;font-weight:700;color:#64748b;text-transform:uppercase;margin-top:2px">Avg Close Time</div>
        </div>
      </div>
    </div>
  </div>
</div></div>

<!-- ════ LEADERBOARD ════ -->
<div class="pi-tc" id="tab-lb"><div class="pi-pg">
  <div class="pi-sec" style="font-size:18px">🏅 Team Performance Leaderboard</div>
  <div style="background:#fff;border:1.5px solid #e8edf5;border-radius:14px;overflow:hidden">
    <table class="pi-lb">
      <thead><tr>
        <th style="width:50px">Rank</th>
        <th>Team Member</th>
        <th style="text-align:center">Score</th>
        <th style="text-align:center">Assigned</th>
        <th style="text-align:center">Done</th>
        <th style="min-width:120px">Resolution</th>
        <th style="text-align:center">Speed</th>
        <th style="text-align:center">Bugs</th>
        <th>Tier</th>
        <th>Badges</th>
      </tr></thead>
      <tbody>{lb_rows}</tbody>
    </table>
  </div>
  <div style="margin-top:10px;font-size:11px;color:#94a3b8;text-align:right">
    Only members with 3+ issues are ranked &nbsp;·&nbsp; Score = Resolution(40%) + Speed(25%) + Bug Closure(20%) + Critical Handling(15%)
  </div>
</div></div>

<!-- ════ SCORECARDS ════ -->
<div class="pi-tc" id="tab-cards"><div class="pi-pg">
  <div class="pi-sec">📋 Individual Performance Scorecards</div>
  <div class="pi-fbar">
    <button class="pi-fbtn active" onclick="piFilter('all',this)">All ({tot_m})</button>
    <button class="pi-fbtn" onclick="piFilter('excellent',this)">🟢 Excellent ({exc_c})</button>
    <button class="pi-fbtn" onclick="piFilter('good',this)">🟡 Good ({good_c})</button>
    <button class="pi-fbtn" onclick="piFilter('improve',this)">🔴 Needs Improvement ({imp_c})</button>
    <button class="pi-fbtn" onclick="piFilter('critical',this)">🚨 Needs Attention</button>
  </div>
  <div class="pi-cards" id="piCardsGrid">{scorecards_html}</div>
</div></div>

<!-- ════ ACTION PLAN ════ -->
<div class="pi-tc" id="tab-action"><div class="pi-pg">
  <div class="pi-g2" style="align-items:start">
    <div>
      <div class="pi-sec">⚠️ Needs Attention ({nh_count} members)</div>
      <div style="display:flex;flex-direction:column;gap:12px">{needs_html}</div>
    </div>
    <div>
      <div class="pi-sec">🌟 Champions to Spotlight ({top_count})</div>
      <div style="display:flex;flex-direction:column;gap:12px;margin-bottom:24px">{champs_html}</div>
      <div class="pi-sec">💡 Recommended Actions for Management</div>
      <div style="display:flex;flex-direction:column;gap:10px">{recs_html}</div>
    </div>
  </div>
</div></div>

<footer>
  <div id="piFooterDate"></div>
  <div style="margin-top:6px">Jira Intelligence Dashboard — People Intelligence · Powered by Jira API · <a href="/dashboard" style="color:rgba(255,255,255,.5);text-decoration:none">← Back to Board Dashboard</a></div>
</footer>

<script>
function piTab(id, btn) {{
  document.querySelectorAll('.pi-tc').forEach(function(t) {{ t.classList.remove('active'); }});
  document.querySelectorAll('.pi-tab').forEach(function(b) {{ b.classList.remove('active'); }});
  document.getElementById('tab-' + id).classList.add('active');
  btn.classList.add('active');
}}
function piFilter(tier, btn) {{
  document.querySelectorAll('.pi-fbtn').forEach(function(b) {{ b.classList.remove('active'); }});
  btn.classList.add('active');
  document.querySelectorAll('#piCardsGrid > div').forEach(function(card) {{
    card.style.display = (tier === 'all' || card.dataset.tier === tier) ? '' : 'none';
  }});
}}
var PI_DAYS   = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];
var PI_MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
function piPad(n) {{ return n < 10 ? '0' + n : n; }}
function piClock() {{
  var now = new Date();
  var s = PI_DAYS[now.getDay()] + ' · ' + piPad(now.getDate()) + ' ' + PI_MONTHS[now.getMonth()] + ' ' + now.getFullYear() + '  ' + piPad(now.getHours()) + ':' + piPad(now.getMinutes()) + ':' + piPad(now.getSeconds());
  var c = document.getElementById('piClock');     if (c) c.textContent = s;
  var f = document.getElementById('piFooterDate'); if (f) f.textContent = s;
}}
piClock();
setInterval(piClock, 1000);
</script>
</body>
</html>"""


# ─────────────────────────────────────────────────────────────
#  FLASK ROUTES
# ─────────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def index():
    cfg = _cache["config"]
    status = _cache["status"]
    # If auto-connected from .env and data is ready, go straight to the dashboard
    if status == "ready" and cfg.get("jira_url") and cfg.get("email") and cfg.get("api_token"):
        return redirect(url_for("dashboard"))
    connected  = status == "ready"
    fetched_at = _cache["fetched_at"]
    total = _cache["data"]["total"] if connected and _cache["data"] else 0
    return render_template_string(
        CONNECT_HTML,
        logo_src=LOGO_SRC,
        config=cfg,
        connected=connected,
        fetched_at=fetched_at.strftime("%d %b %Y %H:%M:%S") if fetched_at else "—",
        total=total,
    )


@app.route("/test-connection", methods=["POST"])
def test_connection():
    jira_url  = request.form.get("jira_url","").strip().rstrip("/")
    email     = request.form.get("email","").strip()
    api_token = request.form.get("api_token","").strip()
    if not all([jira_url, email, api_token]):
        return jsonify({"ok": False, "message": "Please fill in all three fields."})
    try:
        api_version = detect_api_version(jira_url, email, api_token)
        data = _jira_get(jira_url, f"/rest/api/{api_version}/myself", email, api_token)
        name = data.get("displayName", data.get("emailAddress","Unknown"))
        # Persist detected api_version into the cache config so Connect uses it too
        with _lock:
            _cache["config"]["api_version"] = api_version
        ver_label = "Cloud" if api_version == "3" else "Server/DC"
        return jsonify({"ok": True, "message": f"✅ Connected as {name} (Jira {ver_label}). Credentials are valid!"})
    except urllib.error.HTTPError as e:
        msgs = {401:"Invalid credentials — check your email and API token.",
                403:"Access denied — your account may lack permissions.",
                404:"URL not found — check your Jira base URL (no trailing slash).",
                410:"URL not found — check your Jira base URL is correct (no extra path like /jira)."}
        return jsonify({"ok": False, "message": msgs.get(e.code, f"HTTP {e.code}: {e.reason}")})
    except Exception as e:
        return jsonify({"ok": False, "message": f"Connection failed: {str(e)}"})


@app.route("/connect", methods=["POST"])
def connect():
    jira_url  = request.form.get("jira_url","").strip().rstrip("/")
    email     = request.form.get("email","").strip()
    api_token = request.form.get("api_token","").strip()
    if not all([jira_url, email, api_token]):
        return jsonify({"ok": False, "message": "Jira URL, email and API token are required."})
    # Auto-detect API version (Cloud=3, Server/DC=2)
    try:
        api_version = detect_api_version(jira_url, email, api_token)
    except urllib.error.HTTPError as e:
        msgs = {401:"Invalid credentials — check your email and API token.",
                403:"Access denied — your account may lack permissions.",
                404:"URL not found — check your Jira base URL.",
                410:"URL not found — check your Jira base URL is correct."}
        return jsonify({"ok": False, "message": msgs.get(e.code, f"HTTP {e.code}: {e.reason}")})
    except Exception as e:
        return jsonify({"ok": False, "message": f"Connection failed: {str(e)}"})
    config = {
        "jira_url":    jira_url,
        "email":       email,
        "api_token":   api_token,
        "projects":    request.form.get("projects","").strip(),
        "max_results": int(request.form.get("max_results", 500) or 500),
        "api_version": api_version,
    }
    if not all([config["jira_url"], config["email"], config["api_token"]]):
        return jsonify({"ok": False, "message": "Jira URL, email and API token are required."})
    # Start background fetch
    t = threading.Thread(target=background_fetch, args=(config,), daemon=True)
    t.start()
    t.join(timeout=120)   # wait up to 2 min in foreground for initial load
    with _lock:
        status = _cache["status"]
        err    = _cache["error_msg"]
    if status == "ready":
        return jsonify({"ok": True, "message": "Connected and data loaded."})
    elif status == "error":
        return jsonify({"ok": False, "message": err})
    else:
        return jsonify({"ok": True, "message": "Fetch in progress — redirecting…"})


@app.route("/refresh")
def refresh():
    cfg = _cache["config"]
    if not cfg.get("jira_url") or not cfg.get("email") or not cfg.get("api_token"):
        return redirect(url_for("index"))
    if _cache["status"] != "fetching":
        t = threading.Thread(target=background_fetch, args=(cfg,), daemon=True)
        t.start()
        t.join(timeout=120)
    return redirect(url_for("dashboard"))


@app.route("/dashboard")
def dashboard():
    with _lock:
        status = _cache["status"]
        data   = _cache["data"]
        fetched_at = _cache["fetched_at"]
        cfg    = _cache["config"]
        err    = _cache["error_msg"]

    if status == "fetching":
        return """<html><head><meta http-equiv="refresh" content="3;url=/dashboard">
        <style>body{{display:flex;align-items:center;justify-content:center;height:100vh;
        font-family:sans-serif;background:#0f2d5e;color:#fff;flex-direction:column;gap:16px}}
        .sp{{width:40px;height:40px;border:4px solid rgba(255,255,255,.2);border-top-color:#fff;
        border-radius:50%;animation:s .8s linear infinite}}@keyframes s{{to{{transform:rotate(360deg)}}}}</style>
        </head><body><div class="sp"></div><div style="font-size:16px;font-weight:700">
        Fetching data from Jira…</div><div style="font-size:12px;opacity:.6">
        Refreshing automatically…</div></body></html>"""

    if status == "error":
        return f"""<html><head><style>body{{display:flex;align-items:center;justify-content:center;
        height:100vh;font-family:sans-serif;background:#fff5f5;color:#7f1d1d;flex-direction:column;gap:12px}}
        </style></head><body><div style="font-size:24px">❌</div>
        <div style="font-size:16px;font-weight:700">Jira fetch failed</div>
        <div style="font-size:13px;max-width:480px;text-align:center">{err}</div>
        <a href="/" style="color:#1e6ef5;font-weight:600">← Back to Settings</a></body></html>"""

    if status == "idle" or data is None:
        return redirect(url_for("index"))

    return build_dashboard_html(data, LOGO_SRC, fetched_at, cfg.get("jira_url",""))


@app.route("/people")
def people():
    with _lock:
        status     = _cache["status"]
        data       = _cache["data"]
        fetched_at = _cache["fetched_at"]
        cfg        = _cache["config"]
        err        = _cache["error_msg"]

    if status == "fetching":
        return """<html><head><meta http-equiv="refresh" content="3;url=/people">
        <style>body{display:flex;align-items:center;justify-content:center;height:100vh;
        font-family:sans-serif;background:#0f2d5e;color:#fff;flex-direction:column;gap:16px}
        .sp{width:40px;height:40px;border:4px solid rgba(255,255,255,.2);border-top-color:#fff;
        border-radius:50%;animation:s .8s linear infinite}@keyframes s{to{transform:rotate(360deg)}}</style>
        </head><body><div class="sp"></div>
        <div style="font-size:16px;font-weight:700">Loading People Intelligence…</div>
        <div style="font-size:12px;opacity:.6">Refreshing automatically…</div></body></html>"""

    if status == "error":
        return (f'<html><head><style>body{{display:flex;align-items:center;justify-content:center;'
                f'height:100vh;font-family:sans-serif;background:#fff5f5;color:#7f1d1d;'
                f'flex-direction:column;gap:12px}}</style></head><body>'
                f'<div style="font-size:24px">❌</div>'
                f'<div style="font-size:16px;font-weight:700">Data fetch failed</div>'
                f'<div style="font-size:13px;max-width:480px;text-align:center">{err}</div>'
                f'<a href="/" style="color:#1e6ef5;font-weight:600">← Back to Settings</a>'
                f'</body></html>')

    if status == "idle" or data is None:
        return redirect(url_for("index"))

    pd_data = process_people_data(data["rows"])
    if not pd_data:
        return redirect(url_for("dashboard"))
    return build_people_html(pd_data, LOGO_SRC, fetched_at, cfg.get("jira_url",""))


@app.route("/status")
def status_api():
    with _lock:
        return jsonify({
            "status":     _cache["status"],
            "fetched_at": _cache["fetched_at"].isoformat() if _cache["fetched_at"] else None,
            "total":      _cache["data"]["total"] if _cache["data"] else 0,
            "error":      _cache["error_msg"],
        })


# ─────────────────────────────────────────────────────────────
#  ATLASSIAN MARKETPLACE — CONNECT LIFECYCLE ROUTES
# ─────────────────────────────────────────────────────────────

@app.route("/atlassian-connect.json")
def serve_atlassian_connect():
    """Serve the Atlassian Connect app descriptor."""
    descriptor_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "atlassian-connect.json")
    try:
        with open(descriptor_path) as f:
            return app.response_class(f.read(), mimetype="application/json")
    except FileNotFoundError:
        return jsonify({"error": "atlassian-connect.json not found"}), 404

@app.route("/atlassian/installed", methods=["POST"])
def atlassian_installed():
    """Lifecycle hook — called by Atlassian when app is installed on an instance."""
    log.info("Atlassian lifecycle: app installed")
    return "", 204

@app.route("/atlassian/uninstalled", methods=["POST"])
def atlassian_uninstalled():
    """Lifecycle hook — called by Atlassian when app is uninstalled from an instance."""
    log.info("Atlassian lifecycle: app uninstalled")
    return "", 204


@app.route("/privacy")
def privacy_policy():
    """Serve the Privacy Policy page."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Privacy Policy — SprintPulse for Jira</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 800px; margin: 40px auto; padding: 0 24px; color: #172B4D; line-height: 1.7; }
  h1 { color: #0052CC; border-bottom: 2px solid #0052CC; padding-bottom: 12px; }
  h2 { color: #0052CC; margin-top: 32px; }
  table { width: 100%; border-collapse: collapse; margin: 16px 0; }
  th, td { border: 1px solid #DFE1E6; padding: 10px 14px; text-align: left; }
  th { background: #F4F5F7; font-weight: 600; }
  a { color: #0052CC; }
  .meta { color: #6B778C; font-size: 0.9em; }
  footer { margin-top: 48px; padding-top: 16px; border-top: 1px solid #DFE1E6; color: #6B778C; font-size: 0.85em; }
</style>
</head>
<body>
<h1>Privacy Policy — SprintPulse for Jira</h1>
<p class="meta"><strong>Author / Data Controller:</strong> Arunakumar Tavva<br>
<strong>Effective Date:</strong> 14 March 2026 &nbsp;|&nbsp; <strong>Last Updated:</strong> 14 March 2026</p>

<h2>1. Overview</h2>
<p>SprintPulse for Jira ("the App") is a self-hosted web application that connects to your Atlassian Jira instance via the Jira REST API. This Privacy Policy explains what data is accessed, how it is used, and the responsibilities of the person deploying and using the App.</p>

<h2>2. Data Accessed</h2>
<table>
<tr><th>Data Type</th><th>Purpose</th></tr>
<tr><td>Jira issue metadata (summary, status, type, priority, assignee, reporter, labels, sprint, story points, dates)</td><td>Display on dashboard, KPI calculations</td></tr>
<tr><td>Jira project keys and names</td><td>Project-level filtering and grouping</td></tr>
<tr><td>Jira user display names</td><td>Assignee / reporter identification on dashboard</td></tr>
</table>
<p>The App uses <strong>read-only</strong> Jira API scopes (<code>READ</code>). It does <strong>not</strong> create, modify, or delete any Jira data.</p>

<h2>3. Data Storage</h2>
<ul>
<li><strong>No external database.</strong> All Jira data is held in server memory only during the running session.</li>
<li><strong>No data is persisted</strong> to disk beyond the <code>.env</code> file (which stores your Jira credentials locally).</li>
<li><strong>No data is sent</strong> to any third-party service, analytics platform, or external server.</li>
<li>When the Docker container is stopped, all cached data is permanently cleared from memory.</li>
</ul>

<h2>4. Credentials Handling</h2>
<ul>
<li>Your Jira email address and API token are stored in your local <code>.env</code> file, excluded from Docker images via <code>.dockerignore</code>.</li>
<li>Credentials are transmitted over HTTPS to Atlassian's API endpoints only.</li>
<li>Credentials are <strong>never logged</strong>, displayed in the browser, or sent to any party other than Atlassian's own API servers.</li>
</ul>

<h2>5. Cookies and Sessions</h2>
<p>The App uses a single server-side Flask session cookie to maintain your connection configuration within your browser session. No persistent tracking cookies, third-party cookies, or analytics cookies are used.</p>

<h2>6. Third-Party Services</h2>
<p>The App loads fonts from Google Fonts CDN for visual display purposes only. No Jira data is transmitted to Google Fonts.</p>

<h2>7. Data Sharing</h2>
<p>The App does not share, sell, rent, or otherwise transmit any Jira data or personal information to any third party. All data processing occurs within your own infrastructure.</p>

<h2>8. Self-Hosted Deployment</h2>
<p>Because the App is self-hosted, <strong>you</strong> (the deploying organisation or individual) are the data controller. You are responsible for securing the server, network access, TLS certificates, and access controls.</p>

<h2>9. Atlassian API Usage</h2>
<p>This App uses the Atlassian Jira REST API in accordance with <a href="https://developer.atlassian.com/platform/marketplace/atlassian-developer-terms/">Atlassian's Developer Terms of Service</a> and the <a href="https://developer.atlassian.com/platform/marketplace/atlassian-marketplace-vendor-agreement/">Atlassian Marketplace Partner Agreement</a>.</p>

<h2>10. Contact</h2>
<p>For privacy-related questions or data deletion requests, contact:<br>
<strong>Arunakumar Tavva</strong><br>
Email: <a href="mailto:support@sprintpulse.dev">support@sprintpulse.dev</a><br>
Website: <a href="https://sprintpulse.dev">https://sprintpulse.dev</a></p>

<h2>11. Changes to This Policy</h2>
<p>This policy may be updated to reflect changes in the App or legal requirements. The "Last Updated" date at the top will be revised accordingly.</p>

<footer>&copy; 2026 Arunakumar Tavva. All rights reserved.</footer>
</body>
</html>"""


@app.route("/security")
def security_policy():
    """Serve the Security Policy page."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Security Policy — SprintPulse for Jira</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 800px; margin: 40px auto; padding: 0 24px; color: #172B4D; line-height: 1.7; }
  h1 { color: #0052CC; border-bottom: 2px solid #0052CC; padding-bottom: 12px; }
  h2 { color: #0052CC; margin-top: 32px; }
  ul { padding-left: 20px; }
  li { margin-bottom: 6px; }
  .meta { color: #6B778C; font-size: 0.9em; }
  a { color: #0052CC; }
  .highlight { background: #F4F5F7; border-left: 4px solid #0052CC; padding: 12px 16px; margin: 16px 0; border-radius: 0 4px 4px 0; }
  footer { margin-top: 48px; padding-top: 16px; border-top: 1px solid #DFE1E6; color: #6B778C; font-size: 0.85em; }
</style>
</head>
<body>
<h1>Security Policy — SprintPulse for Jira</h1>
<p class="meta"><strong>Vendor:</strong> Arunakumar Tavva &nbsp;|&nbsp;
<strong>Effective Date:</strong> 14 March 2026 &nbsp;|&nbsp;
<strong>Last Updated:</strong> 14 March 2026</p>

<div class="highlight">
  SprintPulse for Jira is a self-hosted application. The customer deploys and operates it within their own infrastructure. This policy describes the security controls, practices, and incident response procedures maintained by the vendor.
</div>

<h2>1. Application Security</h2>
<ul>
  <li>The app is built with Python/Flask and uses only well-maintained, minimal dependencies (Flask, Gunicorn, Requests).</li>
  <li>All dependencies are pinned in <code>requirements.txt</code> and reviewed for known vulnerabilities before each release.</li>
  <li>The app uses read-only Jira API access (<code>READ</code> scope only) — it cannot create, modify, or delete any Jira data.</li>
  <li>No user passwords are stored by the application. Jira API tokens are held in the customer's own <code>.env</code> file, never transmitted to the vendor.</li>
  <li>Session data is managed using Flask's server-side session with a configurable <code>SECRET_KEY</code>.</li>
  <li>All Jira API communications are made over HTTPS to Atlassian's endpoints only.</li>
  <li>The application has been reviewed against the <a href="https://owasp.org/www-project-top-ten/">OWASP Top 10</a> security risks.</li>
</ul>

<h2>2. Infrastructure Security</h2>
<ul>
  <li>The vendor's production deployment (sprintpulse.dev) is hosted on Railway, which provides encrypted storage, automatic HTTPS/TLS, and infrastructure-level security.</li>
  <li>All traffic to sprintpulse.dev is served over HTTPS (TLS 1.2+).</li>
  <li>The Docker image is built from the official Python base image and kept up to date.</li>
  <li>The <code>.env</code> file containing credentials is excluded from Docker images via <code>.dockerignore</code> and from source control via <code>.gitignore</code>.</li>
</ul>

<h2>3. Access Control and Authentication</h2>
<ul>
  <li>Source code is hosted on GitHub with access restricted to the vendor only.</li>
  <li>Multi-factor authentication (MFA) is enabled on the GitHub account used for development.</li>
  <li>The vendor's Atlassian Marketplace account is protected with MFA.</li>
  <li>Strong, unique passwords are used for all vendor systems.</li>
</ul>

<h2>4. Vulnerability Management</h2>
<ul>
  <li>The vendor monitors dependencies for known CVEs and applies patches promptly.</li>
  <li>Critical security vulnerabilities will be patched within 14 days of discovery, in accordance with the <a href="https://developer.atlassian.com/platform/marketplace/security-bugfix-policy/">Atlassian Marketplace Security Bug Fix Policy</a>.</li>
  <li>Dependency scanning (SCA) is performed on open-source libraries used in the application.</li>
</ul>

<h2>5. Incident Response</h2>
<ul>
  <li>In the event of a confirmed security incident or critical vulnerability, affected customers and Atlassian will be notified within 72 hours.</li>
  <li>Notifications will be sent via the security contact email and posted on the Marketplace listing.</li>
  <li>The vendor follows <a href="https://developer.atlassian.com/platform/marketplace/app-security-incident-management-guidelines/">Atlassian's Security Incident Management Guidelines</a>.</li>
  <li>A fix or mitigation will be provided as quickly as possible, with a target of 14 days for critical issues.</li>
</ul>

<h2>6. Vulnerability Reporting</h2>
<ul>
  <li>To report a security vulnerability, contact: <a href="mailto:support@sprintpulse.dev">support@sprintpulse.dev</a></li>
  <li>Please include a description of the vulnerability, steps to reproduce, and potential impact.</li>
  <li>The vendor commits to acknowledging all valid security reports within 5 business days.</li>
  <li>Responsible disclosure is appreciated — please allow the vendor reasonable time to address the issue before public disclosure.</li>
</ul>

<h2>7. Customer Responsibilities</h2>
<p>Because SprintPulse for Jira is self-hosted, customers are responsible for:</p>
<ul>
  <li>Securing their own server, network, and access controls</li>
  <li>Keeping their Docker host and OS patched and up to date</li>
  <li>Setting a strong, unique <code>SECRET_KEY</code> in their <code>.env</code> file</li>
  <li>Restricting access to the dashboard port (8085) to authorised users only</li>
  <li>Rotating their Jira API token periodically</li>
</ul>

<h2>8. Data Security</h2>
<ul>
  <li>No customer Jira data is stored or processed by the vendor.</li>
  <li>All data processing occurs entirely within the customer's own deployment.</li>
  <li>The vendor has no access to customer Jira instances or data at any time.</li>
</ul>

<h2>9. Security Contact</h2>
<p>For all security-related enquiries, vulnerability reports, or incident notifications:<br>
<strong>Email:</strong> <a href="mailto:support@sprintpulse.dev">support@sprintpulse.dev</a><br>
<strong>Vendor:</strong> Arunakumar Tavva<br>
<strong>Website:</strong> <a href="https://sprintpulse.dev">https://sprintpulse.dev</a></p>

<footer>&copy; 2026 Arunakumar Tavva. All rights reserved. &nbsp;|&nbsp;
<a href="/privacy">Privacy Policy</a></footer>
</body>
</html>"""


if __name__ == "__main__":
    print("\n" + "="*60)
    print("  🚀  SprintPulse for Jira — by Arunakumar Tavva")
    print("="*60)
    print("  ➜  Browser:   http://localhost:8080")
    print("  ➜  Enter Jira URL + email + API token to connect")
    print("  ➜  Share http://your-server:8080/dashboard")
    print("  ➜  Descriptor: http://localhost:8080/atlassian-connect.json")
    print("  ➜  Press Ctrl+C to stop")
    print("="*60 + "\n")
    app.run(debug=False, host="0.0.0.0", port=8080)
