# Staging deploy runbook — OAuth broker (dev branch)

Deploy of the zero-paste OAuth broker (PR #6, merged to `dev`) to the staging
server behind **https://hubstaff.hbai.dev**. This is a concrete runbook for this
specific deploy; the general reference is `DEPLOYMENT.md`.

**Decisions for this deploy**
- Public origin: `https://hubstaff.hbai.dev` (reused; already HTTPS via nginx).
- Hubstaff OAuth app: **reuse the existing app** — its redirect URI
  `https://hubstaff.hbai.dev/oauth/hubstaff/callback` is already registered, and
  the same `client_id` / `client_secret` are reused.

> ⚠️ **Breaking change:** auth is now **OAuth-only**. The old `X-MCP-API-Key`
> PAT header and the `HUBSTAFF_REFRESH_TOKEN` service-account fallback are gone.
> Any existing client using a PAT will get `401` after this deploy and must
> reconnect via the browser OAuth login. Communicate before rolling out.

---

## 0. Preconditions (verify first)
- Hubstaff OAuth app exists with redirect URI **exactly**
  `https://hubstaff.hbai.dev/oauth/hubstaff/callback` (no trailing slash).
- You have its `client_id` / `client_secret`.
- nginx for `hubstaff.hbai.dev` terminates TLS and can proxy the **whole host**
  (not just `/mcp`).

## 1. Pull the code
```bash
cd int-mcp-hubstaff
git fetch origin
git checkout dev
git pull --ff-only origin dev      # must include the PR #6 merge (ac1f8fa)
```

## 2. `.env` (staging)
```env
HUBSTAFF_ORGANIZATION_ID=542238
HUBSTAFF_TASKS_ORGANIZATION_ID=141872
PORT=8000

# OAuth broker — REQUIRED (reuse the existing Hubstaff app)
HUBSTAFF_CLIENT_ID=<existing client_id>
HUBSTAFF_CLIENT_SECRET=<existing client_secret>
PUBLIC_BASE_URL=https://hubstaff.hbai.dev
```
- No `HUBSTAFF_REFRESH_TOKEN`, no `ALLOW_LEGACY_PAT` — both are removed/ignored.
- `PUBLIC_BASE_URL` must be the external HTTPS origin; every OAuth URL, the
  resource identifier, and the upstream redirect are built from it (never from
  the request), so it must be exact.

## 3. Persistent OAuth store (`./data`)
```bash
mkdir -p data
sudo chown 1000:1000 data      # container runs as uid 1000 (mcp)
```
- Holds `data/oauth.db` (SQLite WAL: `oauth.db`, `-wal`, `-shm`).
- **Directory** bind-mount `./data:/app/data` — never a single-file mount (a
  missing single file bind-mounts as a directory and breaks the DB).
- **Treat `data/` as secret** — it stores live Hubstaff refresh tokens. It is
  git-ignored; keep it on persistent disk. Losing it forces every user to
  re-authorize (no other data loss).
- Migrating from a previous deploy? The old `tokens.json` is obsolete; the
  compose file no longer mounts it. You can delete it.

## 4. Build & start
```bash
docker compose up -d --build
docker compose logs -f --tail=50   # expect "Application startup complete"
```
- Container `hubstaff-mcp-server`, host `25088` → container `8000`,
  `restart: unless-stopped`.
- New dependency `python-multipart` is in `requirements.txt` (needed for the
  `/token` and `/oauth/consent` form posts) — the `--build` picks it up.

## 5. nginx (whole-host proxy)
The reverse proxy must expose **all** OAuth endpoints, unauthenticated at the
proxy layer:
- `/.well-known/oauth-protected-resource`, `/.well-known/oauth-authorization-server`
- `/authorize`, `/token`, `/register`, `/revoke`
- `/oauth/hubstaff/callback`, `/oauth/consent`
- `/mcp`

Requirements:
- Proxy `hubstaff.hbai.dev` → `prod-hbai-kr-s5-1080ti-tools:25088` (whole host).
- Forward the `Authorization` header upstream.
- Set `X-Forwarded-Proto: https` (uvicorn runs with `--proxy-headers`).
- Disable response buffering for SSE.
- Do **not** put any auth (basic auth / IP allow) in front of `/.well-known/*`
  or the OAuth endpoints, or discovery/login breaks.

## 6. Verify
Discovery + the 401 handshake (unauthenticated):
```bash
curl -s https://hubstaff.hbai.dev/.well-known/oauth-protected-resource | jq
# expect: resource=https://hubstaff.hbai.dev/mcp, authorization_servers=[https://hubstaff.hbai.dev]

curl -s -D - -o /dev/null -X POST https://hubstaff.hbai.dev/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","method":"initialize","id":1}'
# expect: HTTP/1.1 401 + WWW-Authenticate: Bearer resource_metadata="https://hubstaff.hbai.dev/.well-known/oauth-protected-resource"
```

Full OAuth login (the real test):
- Connect `https://hubstaff.hbai.dev/mcp` from an MCP client (MCP Inspector or
  Claude custom connector).
- Expected: 401 → client discovers metadata → DCR → browser opens **Hubstaff
  login** → Hubstaff consent (Authorize) → **our consent page** (Approve) →
  token issued → `tools/list` + a `tools/call` (e.g. `list_team_members`) return
  your org's data.

> Tip from local testing: if you hand-drive the flow, open the **`/authorize`**
> URL — never the `/oauth/hubstaff/callback` URL directly (that yields
> "Missing code or state").

## 7. Rollback
```bash
git checkout <previous-sha>    # e.g. 2216827 (pre-OAuth dev tip)
docker compose up -d --build
```
- The old build served the PAT/`X-MCP-API-Key` path. Rolling back does not
  destroy `data/`, but tokens issued by the OAuth build won't be understood by
  the old build (and vice-versa) — expect clients to re-auth on either switch.

## Common gotchas (seen during local testing)
- **Redirect URI mismatch** → Hubstaff shows an error instead of login. The
  registered URI must exactly equal `PUBLIC_BASE_URL` + `/oauth/hubstaff/callback`.
- **`data/` owned by root** → container can't write the DB; `chown 1000:1000`.
- **Proxy strips `/.well-known` or auth-gates it** → clients can't discover;
  keep those paths open and whole-host proxied.
- **`PUBLIC_BASE_URL` wrong/localhost** → metadata advertises the wrong origin
  and the browser round-trip fails.
