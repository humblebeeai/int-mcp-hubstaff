"""MCP Server for Hubstaff using low-level Server with HTTP."""
from mcp.server import Server
from mcp.types import TextContent, Tool
import mcp.types as types
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.responses import JSONResponse
import uvicorn

from .hubstaff_client import HubstaffClient, HubstaffTasksClient
from .config import config
from . import formatters


server = Server("hubstaff-mcp")


@server.list_tools()
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
            description="Create a new todo in Hubstaff Tasks (for projects WITH task integration like Jira/Asana)",
            inputSchema={
                "type": "object",
                "properties": {
                    "list_id": {"type": "integer", "description": "List ID in Hubstaff Tasks - REQUIRED"},
                    "title": {"type": "string", "description": "Todo title - REQUIRED"},
                    "assignee_ids": {"type": "array", "items": {"type": "integer"}, "description": "User IDs to assign - REQUIRED"},
                    "description": {"type": "string", "description": "Todo description (optional)"},
                    "due_on": {"type": "string", "description": "Due date YYYY-MM-DD (optional)"}
                },
                "required": ["list_id", "title"]
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


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    client = HubstaffClient()
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
                return [TextContent(type="text", text=f"Error creating task: most likely this project does not have task integration enabled. Details: {str(e)}")]

        elif name == "create_todo":
            token = await HubstaffClient()._get_access_token()
            tasks_client = HubstaffTasksClient(access_token=token)
            try:
                todo = await tasks_client.create_task(
                    list_id=arguments["list_id"],
                    subject=arguments["title"],
                    description=arguments.get("description"),
                    due_on=arguments.get("due_on"),
                    assignee_ids=arguments.get("assignee_ids")
                )
                task_id = todo.get("id")
                subject = todo.get("subject", "Untitled")
                return [TextContent(type="text", text=f"Created todo in Hubstaff Tasks:\nID: {task_id} | Subject: {subject}")]
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


async def handle_mcp(request):
    """Handle MCP requests."""
    body = await request.body()
    import json
    data = json.loads(body)
    
    method = data.get("method")
    msg_id = data.get("id")
    
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
            "result": {"tools": [{"name": t.name, "inputSchema": t.inputSchema, "description": t.description} for t in tools]}
        })
    elif method == "tools/call":
        result = await call_tool(data["params"]["name"], data["params"].get("arguments", {}))
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"content": [{"type": "text", "text": result[0].text}]}
        })
    
    return JSONResponse({"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": "Method not found"}})


app = Starlette(routes=[Route("/mcp", handle_mcp, methods=["POST"])])


if __name__ == "__main__":
    import os
    
    debug_mode = os.getenv("DEBUG", "false").lower() == "true"
    
    if debug_mode:
        from .token_cache import load_tokens
        import time
        
        print("🚀 Starting Hubstaff MCP Server in DEBUG mode")
        print("=" * 60)
        print(f"📊 Config:")
        print(f"   Base URL: {config.base_url}")
        print(f"   Hubstaff Org ID: {config.hubstaff_org_id}")
        print(f"   Tasks Org ID: {config.hubstaff_tasks_org_id}")
        print(f"   Port: {config.port}")
        print(f"   Debug: {debug_mode}")
        
        # Show token information
        from .token_cache import load_tokens
        import time
        
        tokens = load_tokens()
        print("=" * 60)
        print("🔐 Token Status:")
        print(f"   Token file exists: {os.path.exists('tokens.json')}")
        print(f"   Has access token: {bool(tokens.get('access_token'))}")
        print(f"   Has refresh token: {bool(tokens.get('refresh_token'))}")
        if tokens.get("cached_at"):
            age_hours = (time.time() - tokens["cached_at"]) / 3600
            print(f"   Access token age: {age_hours:.1f}h")
            print(f"   Token expired: {age_hours >= 144}")  # 6 days
        
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
    
    uvicorn.run(app, host="0.0.0.0", port=config.port)
