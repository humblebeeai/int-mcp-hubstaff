"""OAuth 2.1 authorization-server + resource-server broker for the Hubstaff MCP.

This package turns the MCP server into a spec-compliant (MCP Authorization
2025-06-18) OAuth broker: downstream MCP clients authenticate against *our*
authorization server via Dynamic Client Registration + PKCE, and we federate
the actual login to Hubstaff upstream. See ``docs``/plan for the full design.
"""
