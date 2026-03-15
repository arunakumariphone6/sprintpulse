"""
SprintPulse Demo Data Creator
─────────────────────────────
Bulk-creates realistic Jira issues across 3 sprints for screenshot purposes.

Usage:
  1. Fill in the CONFIG block below
  2. Run:  python create_demo_data.py

Requirements: Python 3.8+ (no extra packages — uses only stdlib)

Author: Arunakumar Tavva
"""

import json, base64, urllib.request, urllib.error, time

# ─────────────────────────────────────────────────────────────
#  CONFIG — fill these in before running
# ─────────────────────────────────────────────────────────────
JIRA_URL="YOUR-JIRA-PORTAL-URL"

# Your Jira login email
JIRA_EMAIL="YOUR-JIRA-LOGIN-EMAIL"

# API token from https://id.atlassian.com/manage-profile/security/api-tokens
JIRA_API_TOKEN="YOUR-JIRA-API-TOKEN"

# Comma-separated project keys (leave blank for all projects)
JIRA_PROJECTS="YOUR-JIRA-PROJECT-KEYS"
PROJECT_KEY = "YOUR-JIRA-PROJECT-KEY"                                     # your project key
BOARD_ID    = None   # leave None — script will auto-detect it
# ─────────────────────────────────────────────────────────────

HEADERS = {
    "Authorization": "Basic " + base64.b64encode(f"{EMAIL}:{API_TOKEN}".encode()).decode(),
    "Content-Type":  "application/json",
    "Accept":        "application/json",
}

def api(method, path, body=None, base="rest/api/3"):
    url = f"{JIRA_URL}/{base}/{path.lstrip('/')}"
    data = json.dumps(body).encode() if body else None
    req  = urllib.request.Request(url, data=data, headers=HEADERS, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read()) if r.length != 0 else {}
    except urllib.error.HTTPError as e:
        msg = e.read().decode()
        print(f"  ✗ {method} {path} → {e.code}: {msg[:200]}")
        return None

def agile(method, path, body=None):
    return api(method, path, body, base="rest/agile/1.0")

# ── Issue definitions ─────────────────────────────────────────

SPRINT_1_DONE = [
    ("User authentication module",     "Story", "High",   8, "Done"),
    ("Database schema design",         "Story", "High",   5, "Done"),
    ("Login page UI",                  "Story", "Medium", 3, "Done"),
    ("Fix null pointer on signup",     "Bug",   "High",   2, "Done"),
    ("Set up CI/CD pipeline",          "Task",  "Medium", 3, "Done"),
    ("API rate limiting",              "Story", "Medium", 5, "Done"),
    ("Write auth unit tests",          "Task",  "Low",    2, "Done"),
]

SPRINT_1_PARTIAL = [
    ("Password reset flow",            "Story", "High",   5, "In Progress"),
    ("Fix broken login redirect",      "Bug",   "High",   1, "In Progress"),
    ("Update API documentation",       "Task",  "Low",    1, "To Do"),
]

SPRINT_2_ISSUES = [
    ("Dashboard home screen",          "Story", "High",   8,  "Done"),
    ("User profile page",              "Story", "Medium", 5,  "Done"),
    ("Search functionality",           "Story", "High",   8,  "Done"),
    ("Fix search returning duplicates","Bug",   "High",   3,  "Done"),
    ("Notifications system",           "Story", "High",   13, "In Progress"),
    ("Settings page UI",               "Story", "Medium", 5,  "In Progress"),
    ("Dark mode support",              "Story", "Low",    5,  "In Progress"),
    ("Fix avatar upload crash",        "Bug",   "Critical",2, "In Progress"),
    ("Performance profiling",          "Task",  "Medium", 3,  "In Progress"),
    ("Dashboard analytics charts",     "Story", "High",   8,  "To Do"),
    ("Export data to CSV",             "Story", "Medium", 5,  "To Do"),
    ("Fix timezone display bug",       "Bug",   "Medium", 2,  "To Do"),
    ("Accessibility audit",            "Task",  "Medium", 3,  "To Do"),
]

SPRINT_3_ISSUES = [
    ("Mobile responsive layout",       "Story", "High",   13, "To Do"),
    ("Push notifications",             "Story", "High",   8,  "To Do"),
    ("Two-factor authentication",      "Story", "High",   8,  "To Do"),
    ("Onboarding tutorial flow",       "Story", "Medium", 5,  "To Do"),
    ("Fix session timeout bug",        "Bug",   "High",   3,  "To Do"),
    ("Billing and subscription page",  "Story", "High",   13, "To Do"),
    ("Admin user management",          "Story", "Medium", 8,  "To Do"),
    ("API v2 migration",               "Task",  "Medium", 5,  "To Do"),
    ("Fix memory leak on dashboard",   "Bug",   "Critical",3, "To Do"),
    ("Internationalisation (i18n)",    "Story", "Low",    13, "To Do"),
]

STATUS_TRANSITION = {
    "In Progress": "In Progress",
    "Done":        "Done",
}


def get_board_id():
    result = agile("GET", f"board?projectKeyOrId={PROJECT_KEY}")
    if result and result.get("values"):
        bid = result["values"][0]["id"]
        print(f"  ✓ Found board ID: {bid}")
        return bid
    print("  ✗ Could not find board. Check PROJECT_KEY and that the project has a board.")
    return None


def get_or_create_sprint(board_id, name, goal, start_offset_days, length_days):
    """Return existing sprint by name or create a new one."""
    existing = agile("GET", f"board/{board_id}/sprint")
    if existing:
        for s in existing.get("values", []):
            if s["name"] == name:
                print(f"  ✓ Sprint already exists: {name} (id={s['id']})")
                return s["id"]

    from datetime import datetime, timedelta
    now   = datetime.utcnow()
    start = now + timedelta(days=start_offset_days)
    end   = start + timedelta(days=length_days)

    body = {
        "name":          name,
        "goal":          goal,
        "originBoardId": board_id,
        "startDate":     start.strftime("%Y-%m-%dT00:00:00.000Z"),
        "endDate":       end.strftime("%Y-%m-%dT00:00:00.000Z"),
    }
    result = agile("POST", "sprint", body)
    if result and result.get("id"):
        print(f"  ✓ Created sprint: {name} (id={result['id']})")
        return result["id"]
    return None


def get_transitions(issue_key):
    result = api("GET", f"issue/{issue_key}/transitions")
    if result:
        return {t["name"]: t["id"] for t in result.get("transitions", [])}
    return {}


def transition_issue(issue_key, target_status):
    transitions = get_transitions(issue_key)
    tid = None
    for name, tid_val in transitions.items():
        if target_status.lower() in name.lower():
            tid = tid_val
            break
    if not tid:
        # fallback: try exact match
        tid = transitions.get(target_status)
    if tid:
        api("POST", f"issue/{issue_key}/transitions", {"transition": {"id": tid}})
    else:
        print(f"    ⚠ Could not find transition to '{target_status}' for {issue_key}. Available: {list(transitions.keys())}")


def create_issue(summary, issuetype, priority, story_points, sprint_id):
    body = {
        "fields": {
            "project":     {"key": PROJECT_KEY},
            "summary":     summary,
            "issuetype":   {"name": issuetype},
            "priority":    {"name": priority},
            "customfield_10016": story_points,   # Story Points
            "customfield_10020": {"id": str(sprint_id)},  # Sprint (next-gen)
        }
    }
    result = api("POST", "issue", body)
    if result and result.get("key"):
        return result["key"]
    # fallback: try without sprint field (assign sprint separately)
    body["fields"].pop("customfield_10020", None)
    result = api("POST", "issue", body)
    return result.get("key") if result else None


def assign_to_sprint(sprint_id, issue_keys):
    agile("POST", f"sprint/{sprint_id}/issue", {"issues": issue_keys})


def complete_sprint(sprint_id, sprint_name):
    """Mark a sprint as closed (completed)."""
    from datetime import datetime, timedelta
    body = {
        "state":     "closed",
        "startDate": (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00.000Z"),
        "endDate":   (datetime.utcnow() - timedelta(days=16)).strftime("%Y-%m-%dT00:00:00.000Z"),
    }
    agile("POST", f"sprint/{sprint_id}", body)
    print(f"  ✓ Sprint '{sprint_name}' marked as completed")


def start_sprint(sprint_id, sprint_name):
    """Mark a sprint as active."""
    from datetime import datetime, timedelta
    body = {
        "state":     "active",
        "startDate": (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00.000Z"),
        "endDate":   (datetime.utcnow() + timedelta(days=7)).strftime("%Y-%m-%dT00:00:00.000Z"),
    }
    agile("POST", f"sprint/{sprint_id}", body)
    print(f"  ✓ Sprint '{sprint_name}' marked as active")


def run():
    print("\n" + "="*56)
    print("  SprintPulse Demo Data Creator")
    print("  Creating 30 issues across 3 sprints…")
    print("="*56 + "\n")

    # ── Validate config ───────────────────────────────────────
    if "YOUR_EMAIL" in EMAIL or "YOUR_API_TOKEN" in API_TOKEN:
        print("✗ ERROR: Fill in EMAIL and API_TOKEN in the CONFIG block at the top of this script.")
        return

    # ── Get board ID ─────────────────────────────────────────
    board_id = BOARD_ID or get_board_id()
    if not board_id:
        return

    # ── Create 3 sprints ─────────────────────────────────────
    print("\n[ 1/4 ] Creating sprints…")
    s1 = get_or_create_sprint(board_id,
        "Sprint 1 — Foundation",
        "Deliver core auth, database design and CI/CD pipeline",
        start_offset_days=-30, length_days=14)

    s2 = get_or_create_sprint(board_id,
        "Sprint 2 — Core Features",
        "Dashboard, profile, search and notification system",
        start_offset_days=-14, length_days=14)

    s3 = get_or_create_sprint(board_id,
        "Sprint 3 — Scale & Polish",
        "Mobile layout, 2FA, billing and performance improvements",
        start_offset_days=0, length_days=14)

    if not all([s1, s2, s3]):
        print("\n✗ Could not create sprints. Check your API token has 'write:sprint:jira-software' scope.")
        return

    # ── Create Sprint 1 issues ────────────────────────────────
    print("\n[ 2/4 ] Creating Sprint 1 issues (completed sprint)…")
    s1_keys = []
    all_s1 = SPRINT_1_DONE + SPRINT_1_PARTIAL
    for summary, issuetype, priority, pts, status in all_s1:
        key = create_issue(summary, issuetype, priority, pts, s1)
        if key:
            s1_keys.append((key, status))
            print(f"  ✓ {key}: {summary}")
            time.sleep(0.2)

    assign_to_sprint(s1, [k for k, _ in s1_keys])

    # ── Create Sprint 2 issues ────────────────────────────────
    print("\n[ 3/4 ] Creating Sprint 2 issues (active sprint)…")
    s2_keys = []
    for summary, issuetype, priority, pts, status in SPRINT_2_ISSUES:
        key = create_issue(summary, issuetype, priority, pts, s2)
        if key:
            s2_keys.append((key, status))
            print(f"  ✓ {key}: {summary}")
            time.sleep(0.2)

    assign_to_sprint(s2, [k for k, _ in s2_keys])

    # ── Create Sprint 3 issues ────────────────────────────────
    print("\n[ 4/4 ] Creating Sprint 3 issues (upcoming sprint)…")
    s3_keys = []
    for summary, issuetype, priority, pts, status in SPRINT_3_ISSUES:
        key = create_issue(summary, issuetype, priority, pts, s3)
        if key:
            s3_keys.append((key, status))
            print(f"  ✓ {key}: {summary}")
            time.sleep(0.2)

    assign_to_sprint(s3, [k for k, _ in s3_keys])

    # ── Transition statuses ───────────────────────────────────
    print("\n[ ★ ] Transitioning issue statuses…")
    all_issues = s1_keys + s2_keys + s3_keys
    for key, status in all_issues:
        if status != "To Do":
            print(f"  → {key} to '{status}'")
            transition_issue(key, status)
            time.sleep(0.3)

    # ── Set sprint states ─────────────────────────────────────
    print("\n[ ★ ] Setting sprint states…")
    complete_sprint(s1, "Sprint 1 — Foundation")
    start_sprint(s2, "Sprint 2 — Core Features")
    # Sprint 3 stays as future/not started

    # ── Done ─────────────────────────────────────────────────
    total = len(s1_keys) + len(s2_keys) + len(s3_keys)
    print(f"\n{'='*56}")
    print(f"  ✅ Done! Created {total} issues across 3 sprints.")
    print(f"  Now connect SprintPulse to:")
    print(f"    URL:     {JIRA_URL}")
    print(f"    Project: {PROJECT_KEY}")
    print(f"  Then take your screenshots at http://localhost:8085")
    print(f"{'='*56}\n")


if __name__ == "__main__":
    run()
