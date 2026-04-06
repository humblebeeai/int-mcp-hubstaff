# Hubstaff MCP Server

MCP server for tracking time, activity logs, and Hubstaff Task operations. This document covers setup, token handling, and available tools.

## Prerequisites

- Python 3.10+
- Hubstaff account with API access
- Organization IDs for Hubstaff Core and (optionally) Hubstaff Tasks

## Configuration

Copy the sample env file and fill in the required values:

```bash
cp .env.example .env
```

Update `.env` with the variables managed in `src/hubstaff_mcp/config.py`:

```bash
HUBSTAFF_REFRESH_TOKEN=your_refresh_token
HUBSTAFF_ORGANIZATION_ID=your_org_id
# Optional
HUBSTAFF_TASKS_ORGANIZATION_ID=your_tasks_org_id
PORT=8000
```

## Token Management (`tokens.json`)

- On startup the server imports `src/hubstaff_mcp/token_cache.py`, which ensures a `tokens.json` file exists in the working directory.
- The cache stores the current `access_token`, `refresh_token`, and timestamp. Access tokens are refreshed automatically when older than six days.
- Add `tokens.json` to `.gitignore` (and avoid committing it anywhere) because it contains live credentials.
- When the cache refreshes, Hubstaff may return a new refresh token; the file updates automatically.

### Obtaining a Refresh Token

1. Log into Hubstaff and open the Developer / [Personal Access Tokens page](https://developer.hubstaff.com/personal_access_tokens).
2. Create a new token with the scopes required for your MCP operations.
3. Copy the refresh token value and place it in `HUBSTAFF_REFRESH_TOKEN`. Do **not** share or commit this token.

## Local Setup

```bash
pip install -r requirements.txt
python -m hubstaff_mcp.server
```

The MCP server runs on `PORT` (default `8000`).

## Docker Setup

```bash
docker-compose up -d
```

Mount a volume if you want to persist `tokens.json` across container restarts.

## Available Tools

- `get_time_breakdown` - Get daily time breakdown by project and task
- `get_project_hours` - Get total hours spent on a project
- `get_team_time_summary` - Get time summary for team members
- `list_team_members` - List available team members
- `list_projects` - List accessible projects
- `list_todos` - List tasks/todos
- `create_todo` - Create a new task

## Usage Tips

1. Call `list_team_members` to get available user IDs.
2. Pass those IDs plus specific date ranges into reporting endpoints.
3. Keep `tokens.json` secure; delete it if rotating credentials so the server regenerates it with the new refresh token.
