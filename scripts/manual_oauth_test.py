#!/usr/bin/env python3
"""Manual end-to-end OAuth + MCP test against a public deployment.

Runs the full client side of the flow with YOUR browser/IP:
  DCR -> /authorize (browser: Hubstaff login + consent) -> /token ->
  authenticated POST /mcp (initialize, tools/list, tools/call).

Usage:
  python3 manual_oauth_test.py            # defaults to https://hubstaff.hbai.dev
  BASE=https://hubstaff.hbai.dev python3 manual_oauth_test.py
"""
import base64, hashlib, http.server, json, os, secrets, sys, urllib.parse, urllib.request

BASE = os.environ.get("BASE", "https://hubstaff.hbai.dev").rstrip("/")
CAPTURE_PORT = 9999
REDIRECT = f"http://localhost:{CAPTURE_PORT}/callback"

def b64url(b): return base64.urlsafe_b64encode(b).rstrip(b"=").decode()
verifier = secrets.token_urlsafe(64)
challenge = b64url(hashlib.sha256(verifier.encode()).digest())
state = secrets.token_urlsafe(16)

def req(path, data=None, form=False, bearer=None, method=None):
    url = BASE + path
    headers = {}
    body = None
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    r = urllib.request.Request(url, data=body, headers=headers, method=method or ("POST" if data is not None else "GET"))
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()

# 1. Dynamic Client Registration
st, txt = req("/register", {"redirect_uris": [REDIRECT], "client_name": "Manual Test"})
print(f"[1] POST /register -> {st}")
client_id = json.loads(txt)["client_id"]

# 2. Build authorize URL
authz = BASE + "/authorize?" + urllib.parse.urlencode({
    "response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT,
    "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
    "resource": f"{BASE}/mcp",
    "scope": "openid profile email hubstaff:read hubstaff:write tasks:read tasks:write",
})
print("\n" + "=" * 72 + "\nOPEN THIS IN YOUR BROWSER, log in to Hubstaff, click Authorize + Approve:\n")
print(authz + "\n" + "=" * 72 + "\n")

# 3. Capture the redirect
cap = {}
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        cap.update({k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).items()})
        self.send_response(200); self.end_headers()
        self.wfile.write(b"<h1>Captured. Close this tab.</h1>")
    def log_message(self, *a): pass
srv = http.server.HTTPServer(("localhost", CAPTURE_PORT), H)
print(f"[2] waiting for redirect on {REDIRECT} ...")
while "code" not in cap and "error" not in cap:
    srv.handle_request()
if "error" in cap:
    print(f"[!] authorize error: {cap}"); sys.exit(1)
print(f"[3] captured code (state ok: {cap.get('state') == state})")

# 4. Token exchange
st, txt = req("/token", {"grant_type": "authorization_code", "code": cap["code"],
    "client_id": client_id, "redirect_uri": REDIRECT, "code_verifier": verifier,
    "resource": f"{BASE}/mcp"}, form=True)
print(f"[4] POST /token -> {st}")
if st != 200:
    print(txt); sys.exit(1)
access = json.loads(txt)["access_token"]

# 5. Authenticated MCP calls through the public URL
st, txt = req("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "manual", "version": "0"}}}, bearer=access)
print(f"[5] POST /mcp initialize -> {st}: {txt[:200]}")

st, txt = req("/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, bearer=access)
print(f"[6] POST /mcp tools/list -> {st}: {txt[:200]}")

st, txt = req("/mcp", {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
    "params": {"name": "list_team_members", "arguments": {}}}, bearer=access)
print(f"[7] POST /mcp tools/call list_team_members -> {st}:\n{txt[:800]}")
print("\nDONE. If [5]-[7] are 200 with data, the server + proxy + auth all work end-to-end.")
