# Hubstaff MCP — Session Notes (2026-08-18)

Working notes for continuing the Hubstaff MCP setup. No secrets are stored in this file.

> **This file now lives in the configured clone** `~/workspaces/agents/int-mcp-hubstaff`
> (branch `dev`, real `.env` — org `542238`, tasks org `141872`, refresh token present).
> `tokens.json` is **not** present here yet, and no Docker container is running from
> this clone. The "What we ran today" section below was captured from the throwaway
> Docker clone `~/workspaces/projects/hbai/hstaff/int-mcp-hubstaff`.
> See `DEPLOYMENT.md` for the central production deploy (`https://hubstaff.hbai.dev/mcp`).

## Goal
Make it easy for each user to connect their own Hubstaff profile to our internal
platform without the painful PAT-copy setup. Explored using the **Hubstaff MCP server**
as a low-risk side project instead of touching the core internal platform.

## Key decisions / findings
- **Hubstaff has an official hosted MCP** (read-only, early access, owners/managers only),
  but no public endpoint URL yet — would need to contact Hubstaff support.
  Docs: https://support.hubstaff.com/hubstaff-mcp-server/
- We have **our own** MCP: `humblebeeai/int-mcp-hubstaff`
  (https://github.com/humblebeeai/int-mcp-hubstaff).
- **Use the `dev` branch, not `main`.** `main` is ~4 months stale.
  `dev` is a superset of the other branches and contains:
  - Per-user PAT auth: each caller sends their own Hubstaff PAT via the
    `X-MCP-API-Key` header; Hubstaff enforces that user's role. **This is the
    solution to the "PAT setup is hard" problem** — no per-user server setup.
  - `X-MCP-API-Key` header security for remote deployment.
  - `update_todo` tool + startup fix (the `feature/update-todo` and
    `fix/mcp-server-startup` branches are already folded into `dev`).

## Architecture (dev branch)
- Python (Starlette + uvicorn), single HTTP JSON-RPC endpoint at `POST /mcp`.
- Auth gate runs before method routing: requests need `X-MCP-API-Key` present.
  Only `tools/call` actually validates the PAT against Hubstaff.
- Only shared server-side config needed: `HUBSTAFF_ORGANIZATION_ID`
  (+ optional `HUBSTAFF_TASKS_ORGANIZATION_ID`). `HUBSTAFF_REFRESH_TOKEN` is
  optional — only a fallback for the reserved "default" key when no header sent.
- Token cache in `tokens.json` (keep out of git; it holds live creds).

## 12 tools exposed
get_time_breakdown, get_project_hours, get_team_time_summary, list_team_members,
list_projects, list_todos, create_task, create_todo, update_todo, get_tasks_lists,
list_tasks_projects, list_tasks_members

## What we ran today (throwaway Docker clone `~/workspaces/projects/hbai/hstaff/int-mcp-hubstaff`)
- Cloned repo, checked out `dev`, created `.env` from example (PLACEHOLDER org id),
  created empty `tokens.json`, built + started via Docker Compose.
- Container `hubstaff-mcp-server` running on **http://localhost:25088/mcp**
  (compose maps host 25088 -> container 8000).
- Verified:
  - `tools/list` -> 12 tools
  - Missing header -> 401 Unauthorized
  - Invalid PAT -> 401 "Invalid or expired Hubstaff PAT"
  - Live data NOT yet tested (needs real org id + PAT).

## Existing installs found on this device
| Location | Branch | State |
|---|---|---|
| `~/workspaces/agents/int-mcp-hubstaff` | `dev` | **Canonical** — real `.env` (refresh token + both org IDs); no `tokens.json`, not running in Docker. This notes file lives here. |
| `~/workspaces/agents/in.hubstaff-scripts-exp` | — | Scripts project; has `HUBSTAFF_PAT` + org id; contains `mcp-hubstaff-linear/` |
| `~/workspaces/projects/hbai/hstaff/int-mcp-hubstaff` | `dev` | Throwaway — ran in Docker with placeholder `.env` for the smoke tests below |

- Real credentials already exist in `~/workspaces/agents/int-mcp-hubstaff/.env`
  (org id `5422…`, tasks org `1418…`, plus a working refresh token / PAT `eyJ0…`).
  All point to the same Hubstaff account.

## Client config example (per-user)
```json
"hubstaff-mcp": {
  "type": "remote",
  "url": "http://localhost:25088/mcp",
  "headers": { "X-MCP-API-Key": "<your-hubstaff-PAT>" }
}
```

## Next steps (pick up here)
1. Canonical clone decided: `~/workspaces/agents/int-mcp-hubstaff` (this one, real creds).
   Before running it in Docker here, create `tokens.json` first (`echo '{}' > tokens.json`)
   so Docker doesn't bind-mount it as a directory — see `DEPLOYMENT.md` §3.
2. Do a live end-to-end test: `tools/call` -> `list_team_members` with a real PAT.
3. Merge `dev` -> `main` (open PR) since `dev` is the intended state. `DEPLOYMENT.md`
   assumes `main` (`git checkout main` "after PR #4 merges").
4. Central production deploy is documented in `DEPLOYMENT.md`:
   target `prod-hbai-kr-s5-1080ti-tools`, URL `https://hubstaff.hbai.dev/mcp`,
   host port `25088` -> container `8000`. Keep only `HUBSTAFF_ORGANIZATION_ID`
   (+ tasks org) server-side; each user supplies their PAT via `X-MCP-API-Key`.

## Get a Hubstaff PAT
Developer / Personal Access Tokens: https://developer.hubstaff.com/personal_access_tokens
OAuth docs: https://developer.hubstaff.com/authentication/

## OAuth / token model (from the auth docs — how it maps to this server)
- **Token endpoint:** `POST https://account.hubstaff.com/access_tokens`. Our server
  calls it with `grant_type=refresh_token` + the PAT as the `refresh_token`
  (`token_cache.py:104`). No `client_id`/`client_secret` needed — **PATs act as
  refresh tokens** that skip client auth. (Full OAuth apps would use HTTP Basic
  with client_id/secret and support `authorization_code` + PKCE.)
- **Access tokens expire after 24h.** Our cache stores the returned `access_token`
  + `cached_at` per PAT in `tokens.json` and re-mints when stale.
- **Scopes:** `openid profile email hubstaff:read hubstaff:write tasks:read
  tasks:write`. Per-user role enforcement happens server-side at Hubstaff based on
  the PAT's owner — request the narrowest scopes when minting a PAT.
- **⚠️ Refresh-token rotation gotcha:** the docs say the token endpoint returns a
  *rotated* `refresh_token` and you must "replace what you have on disk." Our code
  only saves the new `access_token`, **not** the rotated `refresh_token`
  (`token_cache.py:115-119`). This is fine for the per-user PAT path (the user keeps
  sending the same long-lived PAT in the header), but the reserved **`default`
  key** using `HUBSTAFF_REFRESH_TOKEN` from `.env` could go stale if Hubstaff
  rotates it — worth verifying before relying on a service-account fallback in prod.
- Refresh grants for the same PAT are serialized in `token_cache.py` because
  Hubstaff rejects overlapping `refresh_token` grants with a 400.
