"""MCP Server for Hubstaff using Starlette HTTP endpoint for JSON-RPC routing."""
from mcp.types import TextContent, Tool
import mcp.types as types
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.responses import JSONResponse
import uvicorn

from .hubstaff_client import HubstaffClient, HubstaffTasksClient
from .config import config
from . import formatters
from .oauth import store
from .oauth.bearer import build_www_authenticate, extract_bearer, resolve_bearer
from .oauth import metadata as oauth_metadata
from .oauth.register import register as oauth_register
from .oauth.authorize import authorize as oauth_authorize
from .oauth.callback import hubstaff_callback, consent as oauth_consent
from .oauth.token import token as oauth_token, revoke as oauth_revoke


async def list_tools() -> list[types.Tool]:
    return [
        Tool(
            name="get_time_breakdown",
            description="Get daily time breakdown by project and task for specified user and date. Tip: call list_team_members first to find valid user IDs.",
            inputSchema={
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "Start date (YYYY-MM-DD) - REQUIRED"},
                    "end_date": {"type": "string", "description": "End date (YYYY-MM-DD) - REQUIRED"},
                    "user_id": {"type": "integer", "description": "User ID - REQUIRED"},
                    "project_ids": {"type": "array", "items": {"type": "integer"}, "description": "Filter by projects (optional)"}
                },
                "required": ["start_date", "end_date", "user_id"]
            }
        ),
        Tool(
            name="get_project_hours",
            description="Get total hours spent on a specific project over a date range for a user. Tip: call list_team_members to discover user IDs.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer", "description": "Project ID - REQUIRED"},
                    "user_id": {"type": "integer", "description": "User ID - REQUIRED"},
                    "start_date": {"type": "string", "description": "Start date (YYYY-MM-DD) - REQUIRED"},
                    "end_date": {"type": "string", "description": "End date (YYYY-MM-DD) - REQUIRED"}
                },
                "required": ["project_id", "user_id", "start_date", "end_date"]
            }
        ),
        Tool(
            name="get_team_time_summary",
            description="Get time summary for specified team members and date range. Tip: use list_team_members to build the user_ids array.",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_ids": {"type": "array", "items": {"type": "integer"}, "description": "List of user IDs - REQUIRED"},
                    "start_date": {"type": "string", "description": "Start date (YYYY-MM-DD) - REQUIRED"},
                    "end_date": {"type": "string", "description": "End date (YYYY-MM-DD) - REQUIRED"},
                    "project_ids": {"type": "array", "items": {"type": "integer"}, "description": "Filter by projects (optional)"}
                },
                "required": ["user_ids", "start_date", "end_date"]
            }
        ),
        Tool(
            name="list_team_members",
            description="List all organization members to help user select personas to track",
            inputSchema={"type": "object", "properties": {}}
        ),
        Tool(
            name="list_projects",
            description="List all accessible projects",
            inputSchema={
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["active", "archived", "all"], "description": "Filter by status"}
                }
            }
        ),
        Tool(
            name="list_todos",
            description="List tasks/todos in project or organization",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer", "description": "Filter by project"},
                    "status": {"type": "string", "enum": ["active", "completed", "all"]},
                    "assignee_id": {"type": "integer", "description": "Filter by assignee"}
                }
            }
        ),
        Tool(
            name="create_task",
            description="Create a new task in Hubstaff (for projects WITHOUT task integration like Jira/Asana)",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer", "description": "Project ID - REQUIRED"},
                    "title": {"type": "string", "description": "Task title - REQUIRED"},
                    "assignee_id": {"type": "integer", "description": "User ID to assign to"}
                },
                "required": ["project_id", "title"]
            }
        ),
        Tool(
            name="create_todo",
            description="Create a todo in Hubstaff Tasks. Pass `project` (a project name like 'INT: LumioHub', or a project id from list_projects/list_tasks_projects) and it resolves the Tasks project + default list automatically. Use `list_id` only to target a specific list/column.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Project name (e.g. 'INT: LumioHub') or project id. Resolves to the Tasks project and its default list. Provide this OR list_id."},
                    "list_id": {"type": "integer", "description": "Explicit Hubstaff Tasks list/column id (optional; overrides `project`)."},
                    "title": {"type": "string", "description": "Todo title - REQUIRED"},
                    "assignee_ids": {"type": "array", "items": {"type": "integer"}, "description": "User IDs to assign. Accepts list_team_members or list_tasks_members ids (mapped automatically)."},
                    "description": {"type": "string", "description": "Todo description (optional)"},
                    "due_on": {"type": "string", "description": "Due date YYYY-MM-DD (optional)"}
                },
                "required": ["title"]
            }
        ),
        Tool(
            name="update_todo",
            description="Update an existing todo in Hubstaff Tasks (title/description/due date/assignees). To mark done, use complete_todo. Moving a todo between lists/columns is not supported by the Tasks API.",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "Task ID in Hubstaff Tasks - REQUIRED"},
                    "title": {"type": "string", "description": "New title for the todo (optional)"},
                    "assignee_ids": {"type": "array", "items": {"type": "integer"}, "description": "User IDs to assign. Accepts list_team_members or list_tasks_members ids (mapped automatically)."},
                    "description": {"type": "string", "description": "New todo description (optional)"},
                    "due_on": {"type": "string", "description": "New due date YYYY-MM-DD (optional)"}
                },
                "required": ["task_id"],
                "additionalProperties": False,
                "minProperties": 2
            }
        ),
        Tool(
            name="complete_todo",
            description="Mark a Hubstaff Tasks todo as complete (moves it to the Done list).",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "Task ID in Hubstaff Tasks - REQUIRED"}
                },
                "required": ["task_id"]
            }
        ),
        Tool(
            name="get_tasks_lists",
            description="Get lists from a Hubstaff Tasks project (use project_id to find lists for creating todos)",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer", "description": "Hubstaff Tasks project ID - REQUIRED"}
                },
                "required": ["project_id"]
            }
        ),
        Tool(
            name="list_tasks_projects",
            description="List all Hubstaff Tasks projects (for use with get_tasks_lists and create_todo)",
            inputSchema={
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["active", "archived"], "description": "Filter by status"}
                }
            }
        ),
        Tool(
            name="list_tasks_members",
            description="List all Hubstaff Tasks organization members (get Tasks-specific member IDs for create_todo)",
            inputSchema={"type": "object", "properties": {}}
        )
    ]


async def _resolve_tasks_project(main_client, tasks_client, proj: str):
    """Resolve a project name or id to a Hubstaff Tasks project dict {id, name}.

    Handles the id-space trap: a numeric value is tried first as a Tasks project
    id, then as a main-API project id (mapped to the Tasks project by name).
    """
    proj = proj.strip()
    if proj.isdigit():
        pid = int(proj)
        # (a) maybe it's already a Tasks project id
        try:
            await tasks_client.get_lists(pid)
            return {"id": pid, "name": proj}
        except Exception:
            pass
        # (b) treat as a main-API project id -> map to Tasks project by name
        try:
            main_projects = await main_client.get_projects("all")
        except Exception:
            main_projects = []
        name = next((p.get("name") for p in main_projects if str(p.get("id")) == proj), None)
        return await tasks_client.find_project_by_name(name) if name else None
    # by name
    return await tasks_client.find_project_by_name(proj)


async def _resolve_assignee_ids(main_client, tasks_client, ids: list) -> list:
    """Map assignee ids to Hubstaff **Tasks** member ids.

    Accepts either Tasks member ids (from list_tasks_members) or main-API user
    ids (from list_team_members) and normalizes to Tasks member ids, which is
    what the Tasks API's task[assignee_ids] expects. Mapping is by email, since
    the two id spaces differ and Tasks members don't expose the account user id.
    Unresolvable ids are dropped.
    """
    if not ids:
        return []
    tasks_members = await tasks_client.get_members()
    tasks_ids = {m.get("id") for m in tasks_members}
    email_to_tasks = {(m.get("email") or "").lower(): m.get("id") for m in tasks_members}

    unknown = [i for i in ids if i not in tasks_ids]
    main_id_to_email = {}
    if unknown:
        for m in await main_client.get_users():
            u = m.get("user", {})
            uid, em = u.get("id"), (u.get("email") or "").lower()
            if uid and em:
                main_id_to_email[uid] = em

    resolved = []
    for i in ids:
        if i in tasks_ids:
            resolved.append(i)
        else:
            tid = email_to_tasks.get(main_id_to_email.get(i, ""))
            if tid:
                resolved.append(tid)
    return resolved


def _pick_default_list(lists: list):
    """Pick a sensible default list for new todos: prefer 'To Do', else the
    first non-'done' list, else the first list."""
    if not lists:
        return None
    for lst in lists:
        if (lst.get("name") or "").strip().lower() in ("to do", "todo", "to-do"):
            return lst.get("id")
    for lst in lists:
        if (lst.get("type") or "").lower() != "done":
            return lst.get("id")
    return lists[0].get("id")


async def call_tool(name: str, arguments: dict, grant_id: str) -> list[types.TextContent]:
    client = HubstaffClient(grant_id=grant_id)
    client_created = True
    try:
        if name == "get_time_breakdown":
            # Use the get_time_breakdown method which matches the tool purpose
            activities = await client.get_time_breakdown(
                start_date=arguments["start_date"], 
                end_date=arguments["end_date"],
                user_id=arguments["user_id"],
                project_ids=arguments.get("project_ids")
            )
            projects_data = await client.get_projects()
            projects = {p["id"]: p["name"] for p in projects_data}
            tasks_data = await client.get_tasks(project_id=arguments.get("project_ids", [None])[0] if arguments.get("project_ids") else None)
            tasks = {t["id"]: t["summary"] for t in tasks_data}
            members = await client.get_users()
            user_info = {m["user"]["id"]: f"{m['user'].get('first_name', '')} {m['user'].get('last_name', '')}".strip() for m in members}
            return [TextContent(type="text", text=formatters.format_time_breakdown(activities, projects, tasks, user_info))]
        
        elif name == "get_project_hours":
            # Fix parameter order: project_id, user_id, start_date, end_date
            activities = await client.get_project_hours(
                project_id=arguments["project_id"],
                user_id=arguments["user_id"], 
                start_date=arguments["start_date"],
                end_date=arguments["end_date"]
            )
            projects = await client.get_projects()
            project_name = next((p["name"] for p in projects if p["id"] == arguments["project_id"]), f"Project {arguments['project_id']}")
            return [TextContent(type="text", text=formatters.format_project_hours(activities, project_name))]
        
        elif name == "get_team_time_summary":
            # Use get_team_time_summary with correct parameters
            activities = await client.get_team_time_summary(
                user_ids=arguments["user_ids"],
                start_date=arguments["start_date"],
                end_date=arguments["end_date"],
                project_ids=arguments.get("project_ids")
            )
            user_data = {}
            for activity in activities:
                user_id = activity.get("user_id")
                project_id = activity.get("project_id")
                tracked = activity.get("tracked", 0)
                if user_id not in user_data:
                    user_data[user_id] = {"total_tracked": 0, "projects": {}}
                user_data[user_id]["total_tracked"] += tracked
                projects = await client.get_projects()
                project_name = next((p["name"] for p in projects if p["id"] == project_id), f"Project {project_id}")
                if project_name not in user_data[user_id]["projects"]:
                    user_data[user_id]["projects"][project_name] = 0
                user_data[user_id]["projects"][project_name] += tracked
            members = await client.get_users()
            user_info = {m["user"]["id"]: f"{m['user'].get('first_name', '')} {m['user'].get('last_name', '')}".strip() for m in members}
            return [TextContent(type="text", text=formatters.format_team_summary(user_data, user_info))]
        
        elif name == "list_team_members":
            members = await client.get_users()
            return [TextContent(type="text", text=formatters.format_team_members(members))]
        
        elif name == "list_projects":
            status = arguments.get("status", "active")
            projects = await client.get_projects(status)
            lines = ["Available Projects:", ""]
            for project in projects:
                lines.append(f"ID: {project['id']} | Name: {project['name']} | Status: {project.get('status', 'unknown')}")
            return [TextContent(type="text", text="\n".join(lines))]
        
        elif name == "list_todos":
            status = arguments.get("status", "active")
            if status == "all":
                status = None
            tasks = await client.get_tasks(project_id=arguments.get("project_id"), status=status)
            projects_data = await client.get_projects()
            projects = {p["id"]: p["name"] for p in projects_data}
            return [TextContent(type="text", text=formatters.format_todos(tasks, projects))]
        
        elif name == "create_task":
            try:
                assignee = arguments.get("assignee_id", arguments.get("user_id"))
                todo = await client.create_task(arguments["project_id"], arguments["title"], assignee)
                projects = await client.get_projects()
                project_name = next((p["name"] for p in projects if p["id"] == arguments["project_id"]), f"Project {arguments['project_id']}")
                return [TextContent(type="text", text=formatters.format_created_todo(todo, project_name))]
            except Exception as e:
                return [TextContent(type="text", text=(
                    "Error creating task via the main Hubstaff API.\n"
                    "If this project's to-dos are managed by Hubstaff Tasks (or a "
                    "3rd-party tool), the main API rejects direct creation — use "
                    "create_todo instead.\n"
                    f"Hubstaff response: {str(e)}"
                ))]

        elif name == "create_todo":
            token = await client._get_access_token()
            tasks_client = HubstaffTasksClient(access_token=token)
            try:
                list_id = arguments.get("list_id")
                # Resolve `project` (name or id) -> Tasks project -> default list,
                # so callers don't have to chain list_tasks_projects/get_tasks_lists
                # or hand-copy a Tasks list_id.
                if not list_id:
                    proj = arguments.get("project")
                    if proj is None:
                        return [TextContent(type="text", text="Provide either `project` (name or id) or `list_id`.")]
                    tasks_project = await _resolve_tasks_project(client, tasks_client, str(proj).strip())
                    if not tasks_project:
                        return [TextContent(type="text", text=(
                            f"Could not resolve project '{proj}' to a Hubstaff Tasks project. "
                            "Call list_tasks_projects to see available names/ids."
                        ))]
                    lists = await tasks_client.get_lists(tasks_project["id"])
                    list_id = _pick_default_list(lists)
                    if not list_id:
                        return [TextContent(type="text", text=f"Tasks project '{tasks_project.get('name', proj)}' has no lists to add a todo to.")]

                assignee_ids = await _resolve_assignee_ids(
                    client, tasks_client, arguments.get("assignee_ids") or []
                )
                todo = await tasks_client.create_task(
                    list_id=list_id,
                    subject=arguments["title"],
                    description=arguments.get("description"),
                    due_on=arguments.get("due_on"),
                    assignee_ids=assignee_ids or None
                )
                task_id = todo.get("id")
                subject = todo.get("subject", "Untitled")
                return [TextContent(type="text", text=f"Created todo in Hubstaff Tasks:\nID: {task_id} | Subject: {subject} | List: {list_id}")]
            finally:
                await tasks_client.close()

        elif name == "update_todo":
            # Reject calls that provide no fields to update (fail fast).
            # Note: list_id (moving between lists) is NOT supported by the Tasks
            # API's PATCH endpoint; use complete_todo to mark done instead.
            valid_keys = {"title", "description", "due_on", "assignee_ids"}
            update_fields = {}
            for k, v in arguments.items():
                if k in valid_keys and v is not None:
                    if k == "assignee_ids" and not v:  # skip empty list
                        continue
                    update_fields[k] = v

            if not update_fields:
                return [TextContent(type="text", text="Error: No update fields provided. Specify at least one of: title, description, due_on, assignee_ids.")]

            token = await client._get_access_token()
            tasks_client = HubstaffTasksClient(access_token=token)
            try:
                assignee_ids = update_fields.get("assignee_ids")
                if assignee_ids:
                    assignee_ids = await _resolve_assignee_ids(client, tasks_client, assignee_ids)
                todo = await tasks_client.update_task(
                    task_id=arguments["task_id"],
                    subject=update_fields.get("title"),
                    description=update_fields.get("description"),
                    due_on=update_fields.get("due_on"),
                    assignee_ids=assignee_ids,
                )
                task_id = todo.get("id")
                subject = todo.get("subject", "Untitled")
                return [TextContent(type="text", text=f"Updated todo in Hubstaff Tasks:\nID: {task_id} | Subject: {subject}")]
            finally:
                await tasks_client.close()

        elif name == "complete_todo":
            token = await client._get_access_token()
            tasks_client = HubstaffTasksClient(access_token=token)
            try:
                todo = await tasks_client.complete_task(arguments["task_id"])
                task_id = todo.get("id", arguments["task_id"])
                subject = todo.get("subject", "")
                return [TextContent(type="text", text=f"Completed todo in Hubstaff Tasks:\nID: {task_id} | Subject: {subject}")]
            finally:
                await tasks_client.close()
        
        elif name == "get_tasks_lists":
            token = await client._get_access_token()
            tasks_client = HubstaffTasksClient(access_token=token)
            try:
                lists = await tasks_client.get_lists(arguments["project_id"])
                if not lists:
                    return [TextContent(type="text", text=f"No lists found for project {arguments['project_id']}")]
                lines = [f"Project {arguments['project_id']} Lists:", ""]
                for lst in lists:
                    lst_id = lst.get("id")
                    lst_name = lst.get("name", "Unnamed")
                    lst_type = lst.get("type", "normal")
                    lines.append(f"ID: {lst_id} | Name: {lst_name} | Type: {lst_type}")
                return [TextContent(type="text", text="\n".join(lines))]
            finally:
                await tasks_client.close()
        
        elif name == "list_tasks_members":
            token = await client._get_access_token()
            tasks_client = HubstaffTasksClient(access_token=token)
            try:
                members = await tasks_client.get_members()
                return [TextContent(type="text", text=formatters.format_tasks_members(members))]
            except Exception as e:
                import traceback
                error_msg = str(e)
                tb = traceback.format_exc()
                print(f"Error fetching Tasks members: {error_msg}\n{tb}")
                return [TextContent(type="text", text=f"Error fetching Tasks members: {error_msg}\n\nDetails: {tb}")]
            finally:
                await tasks_client.close()
        
        elif name == "list_tasks_projects":
            token = await client._get_access_token()
            tasks_client = HubstaffTasksClient(access_token=token)
            try:
                status = arguments.get("status", "active")
                projects = await tasks_client.get_projects(status)
                lines = ["Hubstaff Tasks Projects:", ""]
                for project in projects:
                    proj_id = project.get("id")
                    proj_name = project.get("name", "Unnamed")
                    proj_status = "Archived" if project.get("archived") else "Active"
                    lines.append(f"ID: {proj_id} | Name: {proj_name} | Status: {proj_status}")
                return [TextContent(type="text", text="\n".join(lines))]
            finally:
                await tasks_client.close()
        
        return [TextContent(type="text", text=f"Unknown tool: {name}")]
    except Exception as e:
        import traceback
        error_msg = str(e)
        tb = traceback.format_exc()
        print(f"Error calling tool {name}: {error_msg}\n{tb}")
        return [TextContent(type="text", text=f"Error: {error_msg}\n\nDetails: {tb}")]
    finally:
        if client_created:
            await client.close()


def _unauthorized(msg_id, message: str) -> JSONResponse:
    """401 with the RFC 9728 WWW-Authenticate header so clients start OAuth."""
    return JSONResponse(
        {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32001, "message": f"Unauthorized: {message}"},
        },
        status_code=401,
        headers={"WWW-Authenticate": build_www_authenticate()},
    )


async def _resolve_grant_id(request):
    """Resolve the caller to an internal grant id from their OAuth Bearer token.

    Returns ``None`` when the caller is unauthenticated or the token is
    invalid/expired, which the caller turns into a 401 + WWW-Authenticate.
    """
    bearer = extract_bearer(request.headers.get("authorization"))
    if not bearer:
        return None
    return await resolve_bearer(bearer)


async def handle_mcp(request):
    """Handle MCP JSON-RPC requests.

    Authentication resolves the caller's OAuth Bearer token to an internal
    ``grant_id`` that maps to that user's stored Hubstaff credential. Hubstaff's
    own API then enforces project-level roles, so callers only see/manage what
    their role allows.
    """
    await store.init()  # idempotent; ensures schema exists before any DB access
    grant_id = await _resolve_grant_id(request)

    body = await request.body()
    import json
    try:
        data = json.loads(body)
    except Exception:
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32700, "message": "Parse error: invalid JSON"}
        }, status_code=400)
    if not isinstance(data, dict):
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32600, "message": "Invalid Request: JSON body must be an object"}
        }, status_code=400)

    method = data.get("method")
    msg_id = data.get("id")

    # Auth gate: every method requires a resolved grant. A missing/invalid
    # credential returns 401 + WWW-Authenticate, which is exactly what triggers
    # a spec-compliant MCP client to begin the OAuth discovery + login flow.
    if grant_id is None:
        return _unauthorized(
            msg_id,
            "authentication required. Complete the OAuth login flow to connect "
            "your Hubstaff account.",
        )

    if method == "initialize":
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "hubstaff-mcp", "version": "1.0.0"}
            }
        })
    elif method == "tools/list":
        tools = await list_tools()
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"tools": [t.model_dump(by_alias=True, exclude_none=True) for t in tools]}
        })
    elif method == "tools/call":
        # Lazy validation: mint/refresh the Hubstaff access token for this
        # grant. If the underlying credential is invalid/expired we fail fast
        # with a 401 (WWW-Authenticate) so the client restarts the login flow.
        from .token_cache import get_access_token, NeedsReauth
        try:
            await get_access_token(grant_id)
        except NeedsReauth:
            return _unauthorized(
                msg_id, "Hubstaff credential invalid or expired; re-authorize."
            )
        except Exception as e:
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32603, "message": f"Internal error acquiring token: {e}"}
            }, status_code=500)

        result = await call_tool(data["params"]["name"], data["params"].get("arguments", {}), grant_id=grant_id)
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"content": [{"type": "text", "text": result[0].text}]}
        })
    
    return JSONResponse({"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": "Method not found"}})


async def health(request):
    """Unauthenticated liveness probe for uptime monitoring (Uptime Kuma).

    Returns 200 as long as the process is serving. Reports whether the OAuth
    broker is configured so a misconfigured deploy is visible in the monitor.
    """
    return JSONResponse(
        {
            "status": "ok",
            "service": "hubstaff-mcp",
            "oauth_configured": config.oauth_configured,
        }
    )


routes = [
    Route("/health", health, methods=["GET"]),
    Route("/mcp", handle_mcp, methods=["POST"]),
    # OAuth discovery metadata (unauthenticated).
    Route(
        "/.well-known/oauth-protected-resource",
        oauth_metadata.protected_resource_metadata,
        methods=["GET"],
    ),
    Route(
        "/.well-known/oauth-authorization-server",
        oauth_metadata.authorization_server_metadata,
        methods=["GET"],
    ),
    # OAuth authorization server endpoints (broker to Hubstaff upstream).
    Route("/register", oauth_register, methods=["POST"]),
    Route("/authorize", oauth_authorize, methods=["GET"]),
    Route("/token", oauth_token, methods=["POST"]),
    Route("/revoke", oauth_revoke, methods=["POST"]),
    Route("/oauth/hubstaff/callback", hubstaff_callback, methods=["GET"]),
    Route("/oauth/consent", oauth_consent, methods=["POST"]),
]

app = Starlette(routes=routes)


if __name__ == "__main__":
    import os
    
    debug_mode = os.getenv("DEBUG", "false").lower() == "true"
    
    if debug_mode:
        print("🚀 Starting Hubstaff MCP Server in DEBUG mode")
        print("=" * 60)
        print(f"📊 Config:")
        print(f"   Base URL: {config.base_url}")
        print(f"   Hubstaff Org ID: {config.hubstaff_org_id}")
        print(f"   Port: {config.port}")
        print(f"   Debug: {debug_mode}")

        print("=" * 60)
        print("🔐 Auth Status:")
        print(f"   OAuth broker configured: {config.oauth_configured}")
        print(f"   Public base URL: {config.public_base_url}")
        print(f"   Resource URI: {config.resource_uri}")
        print(f"   OAuth DB path: {config.oauth_db_path}")

        print("=" * 60)
        print("🔧 Available MCP Tools:")
        
        # List all available tools
        tools_info = [
            ("get_time_breakdown", "📊 Daily time breakdown by project/task"),
            ("get_project_hours", "⏱️  Total hours on project over date range"),
            ("get_team_time_summary", "👥 Team time summary for date range"),
            ("list_team_members", "👨‍💼 List organization members"),
            ("list_projects", "📁 List all accessible projects"),
            ("create_task", "➕ Create tasks in Hubstaff v2 (non-integrated)"),
            ("create_todo", "✅ Create todos in Hubstaff Tasks (integrated)"),
            ("list_todos", "📋 List tasks/todos in projects"),
            ("list_tasks_members", "👥 Get Tasks-specific member IDs"),
            ("list_tasks_projects", "📁 List Tasks-integrated projects"),
            ("get_tasks_lists", "📝 Get lists within Tasks projects")
        ]
        
        for tool_name, description in tools_info:
            print(f"   {description}")
        
        print("=" * 60)
        print("🌐 Server will be available at:")
        print(f"   HTTP: http://localhost:{config.port}/mcp")
        print("=" * 60)
        
        # Add some test endpoints info
        print("🧪 Quick Test Examples:")
        print(f'   curl -X POST "http://localhost:{config.port}/mcp" \\')
        print('     -H "Content-Type: application/json" \\')
        print('     -d \'{"method": "tools/list"}\'')
        print("")
        print(f'   curl -X POST "http://localhost:{config.port}/mcp" \\')
        print('     -H "Content-Type: application/json" \\')
        print('     -d \'{"method": "tools/call", "params": {"name": "list_team_members"}}\'')
        print("=" * 60)
    
    # proxy_headers + forwarded_allow_ips: trust the reverse proxy's
    # X-Forwarded-* so the app sees the real client IP/scheme. OAuth metadata
    # URLs are built from config.public_base_url (not the request), so they stay
    # correct regardless, but this keeps logging/security context accurate.
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=config.port,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
