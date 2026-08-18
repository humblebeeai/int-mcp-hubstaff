# Zero-paste OAuth for the Hubstaff MCP server

## Context

Today each user connects by manually creating a Hubstaff **Personal Access Token** and pasting it into the `X-MCP-API-Key` header ([server.py:364](src/hubstaff_mcp/server.py#L364), [token_cache.py](src/hubstaff_mcp/token_cache.py) treats the PAT as the refresh token). That PAT setup is exactly the friction we want to remove.

Goal: a **zero-paste** experience. The user adds the remote MCP server, the MCP client (Claude / MCP Inspector) automatically pops a Hubstaff login in the browser, the user consents, and they're connected — nothing to copy.

Per the MCP Authorization spec (2025-06-18), this means our server must become an **OAuth 2.1 Authorization Server + Resource Server that brokers to Hubstaff upstream**. Hubstaff app registration is self-serve (Account → OAuth apps), supports OIDC discovery, but has **no dynamic client registration** — so we hold one static Hubstaff app and federate every user through it.

**Prerequisite (blocking, manual):** an owner registers a Hubstaff OAuth app (Account → OAuth apps) with redirect URI `https://hubstaff.hbai.dev/oauth/hubstaff/callback` and scopes `openid profile email hubstaff:read hubstaff:write tasks:read tasks:write`, yielding `client_id` + `client_secret`. HTTPS is mandatory (already have it at `hubstaff.hbai.dev`).

## Architecture — a brokered flow, two OAuth legs

- **Downstream leg** (MCP client ↔ our AS): client does DCR + PKCE against *our* `/authorize` and `/token`; we issue *our own* opaque tokens.
- **Upstream leg** (our server ↔ Hubstaff): our server is a normal Hubstaff OAuth client using the one static app; its own PKCE + HTTP-Basic client creds against Hubstaff.
- The two legs meet only in storage: our issued token → internal `grant_id` → that user's stored Hubstaff refresh/access token.

Rules we must honor: validate token **audience** (only tokens we issued, for `resource=https://hubstaff.hbai.dev/mcp`); **never** forward our client's token to Hubstaff; **never** accept a raw Hubstaff token at `/mcp`; PKCE + `resource` (RFC8707) on the downstream leg; per-client user consent (confused-deputy, since the upstream client_id is static).

Chained flow: `/mcp` no token → `401 + WWW-Authenticate` → client fetches PRM/ASM → DCR → browser to our `/authorize` → we 302 to Hubstaff login → Hubstaff 302 to our `/oauth/hubstaff/callback` → we store the grant + mint our auth code → 302 back to client → client `POST /token` → our Bearer token → `/mcp` works.

## Token & storage strategy

- **Our issued tokens: opaque random** (`secrets.token_urlsafe(32)`), store only a **SHA-256 hash**. Chosen over JWT for instant revocation (a token is a live proxy for Hubstaff access) and simpler audience checks; keep the token module swappable to JWT if we ever scale horizontally.
- **Refresh rotation** for public clients: rotate on every use, track a `refresh_family_id`, revoke the whole family on reuse (breach detection).
- **Storage: `sqlite3` (stdlib), WAL mode**, one DB file, accessed via `asyncio.to_thread`. Replaces the whole-file-rewrite `tokens.json`. Tables:
  - `clients` (DCR registrations: client_id, redirect_uris, auth method)
  - `pending_auth` (keyed by our upstream `state`; holds downstream client/redirect/state/code_challenge + our upstream PKCE verifier; short TTL, single-use)
  - `auth_codes` (our codes: hash, client_id, redirect_uri, code_challenge, resource, grant_id; ~60s, single-use)
  - `grants` (**replaces PAT-keyed tokens.json**: grant_id, hubstaff_user_id, hubstaff_refresh_token, hubstaff_access_token, expires_at, scope)
  - `access_tokens` (hash, grant_id, client_id, resource, scope, expires_at)
  - `refresh_tokens` (hash, grant_id, refresh_family_id, rotated_from, revoked, expires_at)

## Endpoints to add (new `src/hubstaff_mcp/oauth/` package, wired into `Starlette(routes=[...])` at [server.py:441](src/hubstaff_mcp/server.py#L441))

Unauthenticated discovery/metadata:
- `GET /.well-known/oauth-protected-resource` (RFC9728) — lists our AS; `resource: https://hubstaff.hbai.dev/mcp`.
- `GET /.well-known/oauth-authorization-server` (RFC8414) — authorize/token/register endpoints, `code_challenge_methods_supported: ["S256"]`, grants `authorization_code`+`refresh_token`, `token_endpoint_auth_methods_supported: ["none"]`.

OAuth endpoints:
- `POST /register` (DCR, RFC7591) — accept `redirect_uris` (validate: https or loopback only), issue public `client_id`.
- `GET /authorize` — validate client_id/redirect_uri(exact match)/PKCE/`resource`; create `pending_auth` row + our upstream PKCE+state; 302 to Hubstaff `authorizations/new`. On invalid redirect_uri: render error page, **never** redirect.
- `GET /oauth/hubstaff/callback` — look up pending row by upstream state; exchange Hubstaff code (HTTP Basic client creds + our verifier) at `access_tokens`; persist grant (+ user id from id_token/userinfo); mint our auth code; 302 to downstream redirect_uri.
- `POST /token` — `authorization_code` (verify PKCE, redirect_uri, resource → issue our access+refresh) and `refresh_token` (rotate + family-revoke on reuse).
- Per-client **consent interstitial** before the Hubstaff redirect (confused-deputy mitigation).
- Optional `POST /revoke` (RFC7009).

## Files to modify

- [server.py](src/hubstaff_mcp/server.py) — register OAuth routes; in `handle_mcp` ([server.py:355](src/hubstaff_mcp/server.py#L355)) validate `Authorization: Bearer` first (lookup by hash, check expiry + `resource`), resolve `grant_id`, return `401 + WWW-Authenticate: Bearer resource_metadata="..."` when absent/invalid; pass `grant_id` (not PAT) to `call_tool` ([server.py:160](src/hubstaff_mcp/server.py#L160)).
- [token_cache.py](src/hubstaff_mcp/token_cache.py) — re-key `get_access_token` from PAT to `grant_id`; read/write the `grants` table; **persist the rotated `refresh_token`** Hubstaff returns (fixes the current drop-on-refresh bug at [token_cache.py:115-119](src/hubstaff_mcp/token_cache.py#L115-L119)); use the real `expires_in` (~24h) instead of the hardcoded 6-day heuristic; refresh with **HTTP Basic client creds**; keep the per-key `asyncio.Lock` (now per `grant_id`); on `invalid_grant` mark grant `needs_reauth` → surfaces a fresh 401.
- [hubstaff_client.py:11](src/hubstaff_mcp/hubstaff_client.py#L11) — `HubstaffClient.__init__` `api_key` → `grant_id`. (`HubstaffTasksClient` still takes an access-token string — unchanged; ensure brokered token carries `tasks:*` scope so Tasks tools keep working.)
- [config.py](src/hubstaff_mcp/config.py) — add `HUBSTAFF_CLIENT_ID`, `HUBSTAFF_CLIENT_SECRET`, `PUBLIC_BASE_URL` (=`https://hubstaff.hbai.dev`, used to build all metadata/redirect URLs — do **not** derive from `request.url`), `OAUTH_DB_PATH`, TTLs, `ALLOW_LEGACY_PAT` (default `true`).
- `docker-compose.yml` — replace the single-file mount `./tokens.json:/app/tokens.json` ([docker-compose.yml:12](docker-compose.yml#L12)) with a **directory** mount `./data:/app/data` (SQLite needs WAL sidecar files; a missing single-file bind-mounts as a *directory* — the exact failure `token_cache` already warns about). Pre-create `./data` owned by the container `mcp` user. Add new env; enable uvicorn `--proxy-headers` so metadata advertises the external HTTPS URL. Add `data/` and `oauth.db*` to `.gitignore` (alongside existing `tokens.json`).

New files: `oauth/store.py` (sqlite schema+CRUD+TTL cleanup), `oauth/metadata.py`, `oauth/register.py`, `oauth/authorize.py`, `oauth/callback.py`, `oauth/token.py`, `oauth/tokens.py` (PKCE S256 + random/hash helpers).

## Library choice

**Hand-roll** the OAuth endpoints with stdlib (`sqlite3`, `secrets`, `hashlib`, `base64`) + `httpx` — no new dependency. Rejected: the `mcp` SDK's `mcp.server.auth` (would force rearchitecting the hand-rolled JSON-RPC `/mcp` into the SDK's ASGI transport) and `authlib` (its AS framework assumes we own user login/consent, which we delegate to Hubstaff). Fallback if the team wants a vetted lib: `authlib` for just downstream `/authorize`+`/token`, still hand-rolling the upstream leg + DCR.

## Backward compatibility

Keep the legacy `X-MCP-API-Key` PAT path behind `ALLOW_LEGACY_PAT` (default `true`): in `handle_mcp`, try Bearer first, else PAT (wrapped through a synthetic grant so `call_tool` still gets a `grant_id`), else spec 401. The `HUBSTAFF_REFRESH_TOKEN`/`default` service-account fallback stays under the legacy path. Flip `ALLOW_LEGACY_PAT=false` and delete the PAT branch after users migrate.

## Phased delivery

1. **Foundation** — `oauth/store.py` (sqlite), config fields, directory bind-mount; migrate `token_cache` to grant-id + persist rotation (legacy PAT still works). Unit-test PKCE/hashing/rotation.
2. **Resource server** — PRM endpoint + Bearer validation + `WWW-Authenticate` 401 on `/mcp` (flagged).
3. **AS core** — ASM, `/register`, `/authorize`, `/token`, PKCE, refresh rotation, `/revoke`; test downstream leg with a stubbed upstream.
4. **Upstream federation** — Hubstaff redirect + `/oauth/hubstaff/callback` + grant creation + consent interstitial; wire the real Hubstaff app creds.
5. **Cutover** — flip `ALLOW_LEGACY_PAT=false`, remove PAT branch, drop `tokens.json` mount.

## Verification (end-to-end)

- **MCP Inspector** against `https://hubstaff.hbai.dev/mcp`: expect 401 → PRM/ASM → DCR → browser to `/authorize` → Hubstaff login → consent → back → token → `tools/list` + a `tools/call` succeed.
- **Claude remote connector**: connect the URL; complete the browser handshake as a *second* Hubstaff user to prove per-user grant isolation.
- **Negative tests**: tampered PKCE verifier rejected at `/token`; reused auth code rejected; token with wrong `resource` → 401; reused rotated refresh token → family revoked; expired Hubstaff refresh → grant `needs_reauth` → `/mcp` 401 restarts flow; unregistered `redirect_uri` → `/authorize` error page (no redirect).
- **Concurrency**: N parallel `/mcp` calls for one grant with an expired Hubstaff access token → exactly one upstream refresh (asyncio lock), rotation persisted once.

## Top risks

Audience/token confusion (store+check `resource`, never pass tokens through); open redirect (exact-match redirect_uri before any 302); refresh-rotation races (persist rotated token in one transaction, per-grant lock, `needs_reauth` on `invalid_grant`); state separation (downstream echoed state vs our single-use upstream state); token-at-rest security (hash our tokens, treat Hubstaff refresh tokens + `client_secret` as secrets, never log); proxy/issuer URL correctness (advertise external HTTPS, `--proxy-headers`); discovery endpoints must bypass any proxy auth; brokered token must include `tasks:*` scope.
