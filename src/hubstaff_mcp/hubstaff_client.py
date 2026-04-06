"""Hubstaff API client with simple JSON token caching."""

import httpx
from .config import config
from .token_cache import get_access_token


class HubstaffClient:
    """Simple Hubstaff API client."""

    def __init__(self):
        self._default_user_id = None
        self._api_client = httpx.AsyncClient(base_url=config.base_url, timeout=30.0)

    async def _get_access_token(self) -> str:
        """Get valid access token."""
        return await get_access_token()

    async def _get_headers(self) -> dict:
        """Get headers with authorization."""
        access_token = await self._get_access_token()
        return {"Authorization": f"Bearer {access_token}"}

    async def get(self, path: str, params: dict = None) -> dict:
        """Make GET request."""
        headers = await self._get_headers()
        response = await self._api_client.get(path, params=params, headers=headers)
        response.raise_for_status()
        return response.json()

    async def post(self, path: str, data: dict = None, json: dict = None) -> dict:
        """Make POST request."""
        headers = await self._get_headers()
        response = await self._api_client.post(
            path, data=data, json=json, headers=headers
        )
        response.raise_for_status()
        return response.json()

    async def close(self):
        """Close client."""
        await self._api_client.aclose()

    async def get_users(self) -> list:
        """Get all users in the organization."""
        data = await self.get(f"/v2/organizations/{config.hubstaff_org_id}/members")
        members = data.get("members", [])
        enriched_members = []
        for member in members:
            user_id = member.get("user_id")
            if user_id:
                try:
                    user_data = await self.get(f"/v2/users/{user_id}")
                    member["user"] = user_data.get("user", {})
                except Exception as e:
                    print(
                        f"Error occurred while fetching user data for ID {user_id}: {e}"
                    )
                    pass
            enriched_members.append(member)
        return enriched_members

    async def get_projects(self, status: str = "active") -> list:
        """Get projects."""
        params = {"status": status}
        data = await self.get(
            f"/v2/organizations/{config.hubstaff_org_id}/projects", params=params
        )
        return data.get("projects", [])

    async def get_time_breakdown(
        self, start_date: str, end_date: str, user_id: int, project_ids: list = None
    ) -> dict:
        """Get daily activities."""
        params = {"date[start]": start_date, "date[stop]": end_date}
        if user_id:
            params["user_ids[]"] = [user_id]
        if project_ids:
            params["project_ids[]"] = project_ids

        data = await self.get(
            f"/v2/organizations/{config.hubstaff_org_id}/activities/daily",
            params=params,
        )
        return data.get("daily_activities", [])

    async def get_project_hours(
        self, project_id: int, user_id: int, start_date: str, end_date: str
    ) -> dict:
        """Get total hours on project."""
        params = {
            "date[start]": start_date,
            "date[stop]": end_date,
            "user_ids": str(user_id),
            "project_ids": str(project_id),
        }

        data = await self.get(f"/v2/organizations/{config.hubstaff_org_id}/activities/daily", params=params)
        data = data.get("daily_activities", [])
        return data

    async def get_team_time_summary(
        self, user_ids: list, start_date: str, end_date: str, project_ids: list = None
    ) -> dict:
        """Get team time summary."""
        params = {
            "date[start]": start_date,
            "date[stop]": end_date,
            "user_ids": ",".join(map(str, user_ids)),
        }
        if project_ids:
            params["project_ids"] = ",".join(map(str, project_ids))

        data = await self.get(f"/v2/organizations/{config.hubstaff_org_id}/activities/daily", params=params)
        data = data.get("daily_activities", [])
        return data

    async def get_tasks(
        self, project_id: int = None, assignee: int = None, status: str = "active"
    ) -> list:
        """Get tasks."""
        if project_id:
            params = {"status": status}
            data = await self.get(f"/v2/projects/{project_id}/tasks", params=params)
        else:
            params = {"status": status}
            if assignee:
                params["assignee"] = str(assignee)
            data = await self.get(
                f"/v2/organizations/{config.hubstaff_org_id}/tasks", params=params
            )

        return data.get("tasks", [])

    async def create_task(
        self, project_id: int, title: str, assignee: int = None
    ) -> dict:
        """Create task."""
        task_data = {"summary": title}
        if assignee:
            task_data["assignee_id"] = assignee

        try:
            data = await self.post(f"/v2/projects/{project_id}/tasks", json=task_data)
            return data.get("task", {})
        except Exception as e:
            print(f"Error creating task: {e}")
            raise

    def set_default_user(self, user_id: int):
        """Set default user ID."""
        self._default_user_id = user_id


class HubstaffTasksClient:
    """Hubstaff Tasks API client."""

    def __init__(self, access_token: str):
        self._access_token = access_token
        self._client = httpx.AsyncClient(
            base_url="https://tasks.hubstaff.com/api", timeout=30.0
        )

    async def _get_headers(self) -> dict:
        return {"Authorization": f"Bearer {self._access_token}"}

    async def get(self, path: str, params: dict = None) -> dict:
        headers = await self._get_headers()
        response = await self._client.get(path, params=params, headers=headers)
        response.raise_for_status()
        return response.json()

    async def post(self, path: str, data: dict) -> dict:
        headers = await self._get_headers()
        response = await self._client.post(path, data=data, headers=headers)
        response.raise_for_status()
        return response.json()

    async def close(self):
        await self._client.aclose()

    async def get_projects(self, status: str = "all") -> list:
        org_id = config.hubstaff_tasks_org_id or config.hubstaff_org_id
        data = await self.get(f"/v1/organizations/{org_id}/projects?status={status}")
        projects = data.get("projects", [])
        return projects

    async def get_lists(self, project_id: int) -> list:
        data = await self.get(f"/v1/projects/{project_id}/lists")
        return data.get("lists", [])

    async def get_members(self) -> list:
        org_id = config.hubstaff_tasks_org_id or config.hubstaff_org_id
        data = await self.get(f"/v1/organizations/{org_id}/members")
        return data.get("members", [])

    async def create_task(
        self,
        list_id: int,
        subject: str,
        assignee_ids: list = None,
        description: str = None,
        due_on: str = None,
    ) -> dict:
        form_data = {"task[subject]": subject}

        if description:
            form_data["task[description]"] = description
        if due_on:
            form_data["task[due_on]"] = due_on
        if assignee_ids:
            form_data["task[assigned_to_id]"] = str(assignee_ids[0]) if len(assignee_ids) == 1 else assignee_ids 

        headers = await self._get_headers()
        headers["Content-Type"] = "application/x-www-form-urlencoded"

        response = await self._client.post(
            f"/v1/lists/{list_id}/tasks", data=form_data, headers=headers
        )
        response.raise_for_status()
        result = response.json()
        return result.get("task", {})
