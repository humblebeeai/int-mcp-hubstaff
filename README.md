# Hubstaff MCP Server

A remote MCP server for Hubstaff time tracking, reporting, and Tasks — with
**zero‑paste OAuth**: each user logs into their own Hubstaff account in the
browser (no PATs or shared secrets), and Hubstaff enforces their role.

- **Live:** `https://hubstaff.hbai.dev/mcp`
- **Connect a client:** see [docs/USAGE.md](docs/USAGE.md)

## How it works

The server is an OAuth 2.1 Authorization Server + Resource Server that brokers
login to Hubstaff (one upstream Hubstaff OAuth app; per‑user grants). MCP clients
discover it, register dynamically (DCR), run PKCE, and get a Bearer token scoped
to this server. Full design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Docs

| Doc | What |
|---|---|
| [docs/USAGE.md](docs/USAGE.md) | Connect Claude Code / Cursor / Claude Desktop / mcp‑remote / Inspector |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Deploy the server (Docker, nginx, Cloudflare, env) |
| [docs/STAGING_DEPLOY.md](docs/STAGING_DEPLOY.md) | Staging runbook for `hubstaff.hbai.dev` |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | OAuth broker design & rationale |

## Local development

```bash
pip install -r requirements.txt

# Register a Hubstaff OAuth app (Account → OAuth apps), then:
cp .env.example .env      # set HUBSTAFF_CLIENT_ID/SECRET, org IDs, PUBLIC_BASE_URL

python -m hubstaff_mcp.server      # serves on PORT (default 8000)
python -m pytest tests/ -q         # offline end-to-end OAuth tests
```

`scripts/manual_oauth_test.py` runs the full browser OAuth flow against a
deployment and calls a real tool (see the header for usage).

Health probe: `GET /health` → `{"status":"ok", ...}` (used by Uptime Kuma).

## Tools

Reporting: `get_time_breakdown`, `get_project_hours`, `get_team_time_summary`,
`list_team_members`, `list_projects`, `list_todos`.
Tasks: `create_task`, `create_todo`, `update_todo`, `get_tasks_lists`,
`list_tasks_projects`, `list_tasks_members`.

Tip: call `list_team_members` first to get valid user IDs for the reporting tools.
