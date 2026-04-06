"""Data formatting utilities."""


def format_time(seconds: int) -> str:
    """Convert seconds to hours:minutes format."""
    if not seconds:
        return "0m"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours == 0:
        return f"{minutes}m"
    if minutes == 0:
        return f"{hours}h"
    return f"{hours}h {minutes}m"


def format_percent(value: float) -> str:
    """Format percentage."""
    return f"{value:.1f}%"


def format_time_breakdown(activities: list, projects: dict, tasks: dict, user_info: dict = None) -> str:
    """Format time breakdown report."""
    if not activities:
        return "No time tracked for the specified period."
    
    date_activities = {}
    
    for activity in activities:
        date = activity.get("date")
        if not date:
            continue
            
        if date not in date_activities:
            date_activities[date] = []
        date_activities[date].append(activity)
    
    lines = []
    for date in sorted(date_activities.keys()):
        day_activities = date_activities[date]
        lines.append(f"## {date}")
        
        user_project_tasks = {}
        
        for activity in day_activities:
            user_id = activity.get("user_id")
            project_id = activity.get("project_id")
            task_id = activity.get("task_id")
            tracked = activity.get("tracked", 0)
            
            if user_info and user_id:
                user_name = user_info.get(user_id, f"User {user_id}")
            else:
                user_name = None
            
            project_name = projects.get(project_id, f"Project {project_id}")
            
            key = user_name if user_name else project_name
            
            if key not in user_project_tasks:
                user_project_tasks[key] = {}
            
            if project_name not in user_project_tasks[key]:
                user_project_tasks[key][project_name] = []
            
            task_name = tasks.get(task_id) if task_id else None
            user_project_tasks[key][project_name].append({
                "task_name": task_name,
                "tracked": tracked
            })
        
        for key, projects_dict in user_project_tasks.items():
            if user_info:
                lines.append(f"**{key}:**")
            
            for project_name, task_list in projects_dict.items():
                if not user_info:
                    lines.append(f"**{project_name}:**")
                
                for item in task_list:
                    if item["tracked"] > 0:
                        hours = item["tracked"] // 3600
                        minutes = (item["tracked"] % 3600) // 60
                        time_str = f"{hours:02d}:{minutes:02d}"
                        task_desc = item["task_name"] or "No task"
                        lines.append(f"- [{time_str}] {task_desc}")
                    else:
                        task_desc = item["task_name"] or "No task"
                        lines.append(f"- {task_desc}")
                
                lines.append("")
    
    return "\n".join(lines).rstrip()


def format_project_hours(activities: list, project_name: str) -> str:
    """Format project hours report."""
    if not activities:
        return f"No time tracked for project: {project_name}"
    
    total_tracked = sum(a.get("tracked", 0) for a in activities)
    billable = sum(a.get("billable", 0) for a in activities)
    manual = sum(a.get("manual", 0) for a in activities)
    
    lines = [
        f"Project: {project_name}",
        f"Total Tracked: {format_time(total_tracked)}",
        f"Billable Time: {format_time(billable)}",
        f"Manual Entries: {format_time(manual)}"
    ]
    
    return "\n".join(lines)


def format_team_summary(user_data: dict, user_info: dict) -> str:
    """Format team summary report."""
    if not user_data:
        return "No data available for specified users."
    
    lines = ["Team Time Summary", ""]
    
    grand_total = 0
    for user_id, data in user_data.items():
        user_name = user_info.get(user_id, f"User {user_id}")
        total = data["total_tracked"]
        grand_total += total
        
        lines.append(f"{user_name} (ID: {user_id})")
        lines.append(f"  Total: {format_time(total)}")
        
        if data["projects"]:
            top_projects = sorted(data["projects"].items(), key=lambda x: x[1], reverse=True)[:3]
            project_strs = [f"{name} ({format_time(time)})" for name, time in top_projects]
            lines.append(f"  Top Projects: {', '.join(project_strs)}")
        
        lines.append("")
    
    lines.append(f"Team Grand Total: {format_time(grand_total)}")
    
    return "\n".join(lines)


def format_team_members(members: list) -> str:
    """Format team members list."""
    if not members:
        return "No team members found."
    
    lines = ["Available Team Members:", ""]
    
    for member in members:
        user_id = member.get("user_id")
        user = member.get("user", {})
        
        if user:
            name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
            email = user.get("email", "")
        else:
            name = "Unknown"
            email = ""
        
        role = member.get("membership_role", "member")
        
        lines.append(f"ID: {user_id} | Name: {name} | Email: {email} | Role: {role}")
    
    lines.append("")
    lines.append("Use these IDs in other functions to get time tracking data.")
    
    return "\n".join(lines)


def format_todos(todos: list, projects: dict = None) -> str:
    """Format todos list."""
    if not todos:
        return "No todos found."
    
    lines = ["Tasks/Todos:", ""]
    
    for todo in todos:
        todo_id = todo.get("id")
        summary = todo.get("summary", "Untitled")
        status = todo.get("status", "unknown")
        project_id = todo.get("project_id")
        
        project_name = ""
        if projects and project_id:
            project_name = f" [{projects.get(project_id, f'Project {project_id}')}]"
        
        lines.append(f"ID: {todo_id} | {summary}{project_name} | Status: {status}")
    
    return "\n".join(lines)


def format_created_todo(todo: dict, project_name: str) -> str:
    """Format created todo confirmation."""
    todo_id = todo.get("id")
    summary = todo.get("summary", "Untitled")
    
    return f"Created task in {project_name}:\nID: {todo_id} | Summary: {summary}"


def format_tasks_members(members: list) -> str:
    """Format Tasks organization members list."""
    if not members:
        return "No Tasks organization members found."
    
    lines = ["Hubstaff Tasks Organization Members:", ""]
    
    for member in members:
        member_id = member.get("id")
        name = member.get("name", "Unknown")
        email = member.get("email", "")
        
        lines.append(f"Tasks Member ID: {member_id} | Name: {name} | Email: {email}")
    
    lines.append("")
    lines.append("Use these Tasks Member IDs in create_todo for assignee_ids.")
    
    output= "\n".join(lines)

    return output
