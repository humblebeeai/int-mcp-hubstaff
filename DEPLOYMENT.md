# Deploy: Hubstaff MCP Server (central deployment)

**Target server:** `prod-hbai-kr-s5-1080ti-tools`
**Service URL:** `https://hubstaff.hbai.dev/mcp`

## 1. Pull & build

```bash
git clone git@github.com:humblebeeai/int-mcp-hubstaff.git
cd int-mcp-hubstaff
git checkout main   # after PR #4 merges
```

## 1b. Register the Hubstaff OAuth app (one-time, REQUIRED)

Auth is **OAuth-only** — users log into Hubstaff in the browser (no PAT).
Register **one** Hubstaff OAuth app that every user is federated through:

1. Hubstaff **Account → OAuth apps** → create app.
2. Redirect URI (exact match): `https://hubstaff.hbai.dev/oauth/hubstaff/callback`
3. Scopes: `openid profile email hubstaff:read hubstaff:write tasks:read tasks:write`
4. Copy the `client_id` / `client_secret` into `.env` below.

> These credentials are **required** — without them the server has no working
> auth method and every request returns 401.

## 2. Create `.env`

```env
HUBSTAFF_ORGANIZATION_ID=542238
HUBSTAFF_TASKS_ORGANIZATION_ID=141872
PORT=8000

# OAuth broker (zero-paste per-user login) — REQUIRED
HUBSTAFF_CLIENT_ID=<from step 1b>
HUBSTAFF_CLIENT_SECRET=<from step 1b>
PUBLIC_BASE_URL=https://hubstaff.hbai.dev
```

> `PUBLIC_BASE_URL` **must** be the externally-visible HTTPS origin — all OAuth
> metadata URLs, the resource identifier, and the upstream redirect URI are
> built from it (never from the incoming request). HTTPS is mandatory for OAuth.

## 3. Create the `data/` volume (OAuth store, persistent)

```bash
mkdir -p data
sudo chown 1000:1000 data
```

> The OAuth broker stores its state (DCR clients, grants, issued tokens, per-user
> Hubstaff **refresh tokens**) in `data/oauth.db` (SQLite, WAL mode). It is
> bind-mounted as a **directory** (`./data:/app/data`) — not a single file —
> because SQLite WAL needs sidecar files (`oauth.db-wal`, `oauth.db-shm`).
>
> The container runs as the `mcp` user (uid 1000). If `data/` is owned by root
> the container can't write the DB. **Treat `data/` as secret** (it holds live
> refresh tokens); it is git-ignored.
>
> Legacy note: the old single-file `tokens.json` mount is gone. If `data/` is
> missing before `docker compose up`, Docker bind
> mounts it as a **directory** (Linux). Create `data/` *before* starting the
> container.

## 4. Start the container

```bash
docker compose up -d --build
```

- Container `hubstaff-mcp-server`
- Host port `25088` → container `8000`
- `./data` bind-mounted (`./data:/app/data`) so the OAuth store (grants, tokens)
  persists across restarts
- `restart: unless-stopped`

## 5. Verify locally

Discovery + the 401 handshake (no auth) must work:

```bash
curl -s http://localhost:25088/.well-known/oauth-protected-resource | jq
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://localhost:25088/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","method":"initialize","id":1}'   # expect 401 + WWW-Authenticate
```

Full OAuth login: connect the server URL from an MCP client (Claude / MCP
Inspector). It should hit 401 → discover metadata → DCR → open the Hubstaff
login → consent → obtain a token → `tools/list` succeeds.

## 6. Nginx (already staged in `in.server-nginx`)

- Config: `03.hbai.dev/hubstaff.hbai.dev.conf` (PR #32 → `prod`)
- Reverse-proxies `hubstaff.hbai.dev` → `prod-hbai-kr-s5-1080ti-tools:25088`.
  **Proxy the whole host**, not just `/mcp` — the OAuth endpoints
  (`/.well-known/*`, `/authorize`, `/token`, `/register`, `/revoke`,
  `/oauth/hubstaff/callback`, `/oauth/consent`) must all be reachable and
  **unauthenticated at the proxy** (no auth in front of them).
- Forwards the `Authorization` header upstream
- Sets `X-Forwarded-Proto: https` (the app trusts proxy headers)
- SSE buffering off

## Health check

```bash
curl -s https://hubstaff.hbai.dev/.well-known/oauth-authorization-server | jq
```

## Notes

- Auth is per-user via an OAuth Bearer token issued by this server (users log
  into Hubstaff in the browser — there is no PAT / API-key path).
- Invalid or missing credential → HTTP `401` with `WWW-Authenticate` pointing at
  the protected-resource metadata (this is what triggers the client's OAuth flow).
- Keep `data/` on persistent disk and **treat it as secret** — it holds live
  Hubstaff refresh tokens. Losing it forces every user to re-authorize.
