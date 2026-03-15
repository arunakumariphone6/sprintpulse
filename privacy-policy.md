# Privacy Policy — SprintPulse for Jira

**Author / Data Controller:** Arunakumar Tavva
**Effective Date:** 14 March 2026
**Last Updated:** 14 March 2026

---

## 1. Overview

SprintPulse for Jira ("the App") is a self-hosted web application that connects to your Atlassian Jira instance via the Jira REST API. This Privacy Policy explains what data is accessed, how it is used, and the responsibilities of the person deploying and using the App.

---

## 2. Data Accessed

The App accesses the following data from your Jira instance via the official Jira REST API:

| Data Type | Purpose |
|-----------|---------|
| Jira issue metadata (summary, status, type, priority, assignee, reporter, labels, sprint, story points, dates) | Display on dashboard, KPI calculations |
| Jira project keys and names | Project-level filtering and grouping |
| Jira user display names | Assignee / reporter identification on dashboard |

The App uses **read-only** Jira API scopes (`READ`). It does **not** create, modify, or delete any Jira data.

---

## 3. Data Storage

- **No external database.** All Jira data is held in **server memory only** (in-process cache) during the running session.
- **No data is persisted** to disk beyond the `.env` file (which stores your Jira credentials locally).
- **No data is sent** to any third-party service, analytics platform, or external server.
- When the Docker container is stopped, all cached data is permanently cleared from memory.

---

## 4. Credentials Handling

- Your Jira email address and API token are stored in your local `.env` file, which is excluded from Docker images via `.dockerignore`.
- Credentials are transmitted over HTTPS (when your deployment uses TLS) to Atlassian's API endpoints only.
- Credentials are **never logged**, displayed in the browser, or sent to any party other than Atlassian's own API servers.

---

## 5. Cookies and Sessions

- The App uses a single server-side Flask session cookie to maintain your connection configuration within your browser session.
- No persistent tracking cookies, third-party cookies, or analytics cookies are used.

---

## 6. Third-Party Services

The App loads fonts from **Google Fonts CDN** (`fonts.googleapis.com`) for visual display purposes. This is a cosmetic resource; no Jira data is transmitted to Google Fonts. If you require fully offline operation, replace the `@import url(...)` directive in `app.py` with locally served fonts.

---

## 7. Data Sharing

The App does not share, sell, rent, or otherwise transmit any Jira data or personal information to any third party. All data processing occurs within your own infrastructure.

---

## 8. Self-Hosted Deployment

Because the App is self-hosted:
- **You** (the deploying organisation or individual) are the data controller.
- You are responsible for securing the server, network access, TLS certificates, and access controls.
- You should change the `SECRET_KEY` environment variable to a unique random string before production deployment.
- You should restrict access to port 8085 to authorised users only.

---

## 9. Atlassian API Usage

This App uses the Atlassian Jira REST API in accordance with [Atlassian's Developer Terms of Service](https://developer.atlassian.com/platform/marketplace/atlassian-developer-terms/) and the [Atlassian Marketplace Partner Agreement](https://developer.atlassian.com/platform/marketplace/atlassian-marketplace-vendor-agreement/).

---

## 10. Contact

For privacy-related questions or data deletion requests, contact:

**Arunakumar Tavva**
Email: *(your contact email — add before publishing)*

---

## 11. Changes to This Policy

This policy may be updated to reflect changes in the App or legal requirements. The "Last Updated" date at the top of this document will be revised accordingly.

---

*© 2026 Arunakumar Tavva. All rights reserved.*
