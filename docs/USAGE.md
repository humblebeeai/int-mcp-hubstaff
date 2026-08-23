# Using the Hubstaff MCP (`hubstaff.hbai.dev`)

Connect your AI client to Hubstaff over MCP. **You log in with your own Hubstaff
account in the browser — no tokens or API keys to copy.** You only see and manage
what your Hubstaff role allows.

**Server URL:** `https://hubstaff.hbai.dev/mcp`

## How auth works (one-time, per client)
1. Add the server URL in your MCP client.
2. The client opens a browser → **log in to Hubstaff** → **Authorize** → **Approve**.
3. Done. The client stores its own token and reconnects automatically after that.

There is no shared secret and no PAT. Auth is per-user via OAuth.

## Connect from…

### Claude Code (CLI)
```bash
claude mcp add --transport http hubstaff https://hubstaff.hbai.dev/mcp
```
Then run `/mcp` in Claude Code and complete the browser login. ✅ verified

### Cursor
`~/.cursor/mcp.json` (or Settings → MCP → Add):
```json
{ "mcpServers": { "hubstaff": { "url": "https://hubstaff.hbai.dev/mcp" } } }
```

### Claude Desktop
`claude_desktop_config.json` (uses the `mcp-remote` bridge):
```json
{ "mcpServers": { "hubstaff": { "command": "npx", "args": ["mcp-remote", "https://hubstaff.hbai.dev/mcp"] } } }
```

### Any stdio MCP client (Cline, Roo, Windsurf, Goose, …)
Bridge it with `mcp-remote`:
```bash
npx mcp-remote https://hubstaff.hbai.dev/mcp
```

### Quick manual test (MCP Inspector)
```bash
npx @modelcontextprotocol/inspector
```
Transport **Streamable HTTP** → URL `https://hubstaff.hbai.dev/mcp` → Connect → do the OAuth → **List Tools**.

> **Note:** the **claude.ai web** "custom connector" is currently stricter and may
> not connect; use **Claude Code** or **Claude Desktop (via mcp-remote)** instead.

## Available tools
Time & reporting: `get_time_breakdown`, `get_project_hours`, `get_team_time_summary`,
`list_team_members`, `list_projects`, `list_todos`.
Tasks (Hubstaff Tasks): `create_task`, `create_todo`, `update_todo`,
`get_tasks_lists`, `list_tasks_projects`, `list_tasks_members`.

Tip: call `list_team_members` first to get valid user IDs for the reporting tools.

## Health / status
- `https://hubstaff.hbai.dev/health` → `{"status":"ok", ...}` (used by Uptime Kuma).
- Discovery: `https://hubstaff.hbai.dev/.well-known/oauth-protected-resource`.

## Troubleshooting
- **Stuck / "connection issue"**: remove and re-add the connector; the browser
  login must complete both the Hubstaff **Authorize** and our **Approve** page.
- **Empty results / 403 from tools**: your Hubstaff account may not be a member of
  the configured organization, or your role limits what you can see.
- **Need to disconnect**: revoke access in Hubstaff → Account → Connected apps
  (the "hubstaff.hbai.dev integration"), and remove the connector in your client.
