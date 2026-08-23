"""Hubstaff API client with simple JSON token caching."""

import httpx
from .config import config
from .token_cache import get_access_token


def _raise_with_body(response: httpx.Response) -> None:
    """Raise including Hubstaff's error body so callers see the real reason.

    ``httpx.raise_for_status()`` only reports the status code; Hubstaff puts the
    actionable message (e.g. "project is integrated", wrong org) in the body.
    """
    if response.is_error:
        try:
            body = response.text[:800]
        except Exception:
            body = "<unreadable body>"
        raise httpx.HTTPStatusError(
            f"{response.status_code} {response.reason_phrase} for {response.request.url} :: {body}",
            request=response.request,
            response=response,
        )


class HubstaffClient:
    """Simple Hubstaff API client."""

    def __init__(self, grant_id: str):
        self.grant_id = grant_id
        self._default_user_id = None
        self._api_client = httpx.AsyncClient(base_url=config.base_url, timeout=30.0)

    async def _get_access_token(self) -> str:
        """Get valid access token."""
        return await get_access_token(self.grant_id)

    async def _get_headers(self) -> dict:
        """Get headers with authorization."""
        access_token = await self._get_access_token()
        return {"Authorization": f"Bearer {access_token}"}

    async def get(self, path: str, params: dict = None) -> dict:
        """Make GET request."""
        headers = await self._get_headers()
        response = await self._api_client.get(path, params=params, headers=headers)
        _raise_with_body(response)
        return response.json()

    async def post(self, path: str, data: dict = None, json: dict = None) -> dict:
        """Make POST request."""
        headers = await self._get_headers()
        response = await self._api_client.post(
            path, data=data, json=json, headers=headers
        )
        _raise_with_body(response)
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
        _raise_with_body(response)
        return response.json()

    async def post(self, path: str, data: dict) -> dict:
        headers = await self._get_headers()
        response = await self._client.post(path, data=data, headers=headers)
        _raise_with_body(response)
        return response.json()

    async def close(self):
        await self._client.aclose()

    async def get_projects(self, status: str = "all") -> list:
        # Use the org-less discovery endpoint ("all projects accessible to the
        # current user") instead of /organizations/{id}/projects — the latter
        # 403s when the configured tasks org id is wrong or the user isn't a
        # member of it. Each project carries its own organization_id.
        params = {} if status == "all" else {"status": status}
        data = await self.get("/v1/projects", params=params)
        return data.get("projects", [])

    async def find_project_by_name(self, name: str) -> dict | None:
        """Resolve a Tasks project by (case-insensitive) name.

        Lets callers pass a human project name (or the name from the main-API
        `list_projects`) instead of a Tasks-specific project id.
        """
        projects = await self.get_projects(status="active")
        target = name.strip().lower()
        for p in projects:
            if (p.get("name") or "").strip().lower() == target:
                return p
        return None

    async def get_lists(self, project_id: int) -> list:
        data = await self.get(f"/v1/projects/{project_id}/lists")
        return data.get("lists", [])

    async def resolve_org_id(self) -> int | None:
        """Discover the current user's Tasks org id.

        The configured HUBSTAFF_TASKS_ORGANIZATION_ID is unreliable (it 403s
        with "not authorized" when the user isn't a member), so ask the Tasks
        API which orgs this user actually belongs to.
        """
        data = await self.get("/v1/organizations")
        orgs = data.get("organizations", [])
        return orgs[0]["id"] if orgs else None

    async def get_members(self) -> list:
        org_id = await self.resolve_org_id()
        if not org_id:
            return []
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
            # The Tasks API expects an array: task[assignee_ids][]=<id>&...
            form_data["task[assignee_ids][]"] = [str(a) for a in assignee_ids]

        headers = await self._get_headers()
        headers["Content-Type"] = "application/x-www-form-urlencoded"

        response = await self._client.post(
            f"/v1/lists/{list_id}/tasks", data=form_data, headers=headers
        )
        _raise_with_body(response)
        result = response.json()
        return result.get("task", {})

    async def update_task(
        self,
        task_id: int,
        subject: str = None,
        description: str = None,
        due_on: str = None,
        assignee_ids: list = None,
    ) -> dict:
        """Update an existing task in Hubstaff Tasks.

        Note: PATCH /v1/tasks/{id} accepts subject/description/due_on/
        assignee_ids/label_ids/external_id — NOT list_id (you cannot move a task
        between lists via this endpoint). Use complete_task() to mark done.
        """
        form_data = {}
        if subject is not None:
            form_data["task[subject]"] = subject
        if description is not None:
            form_data["task[description]"] = description
        if due_on is not None:
            form_data["task[due_on]"] = due_on
        if assignee_ids is not None:
            form_data["task[assignee_ids][]"] = [str(a) for a in assignee_ids]

        if not form_data:
            raise ValueError("No update fields provided")

        headers = await self._get_headers()
        headers["Content-Type"] = "application/x-www-form-urlencoded"

        response = await self._client.patch(
            f"/v1/tasks/{task_id}", data=form_data, headers=headers
        )
        _raise_with_body(response)
        result = response.json()
        return result.get("task", {})

    async def complete_task(self, task_id: int) -> dict:
        """Mark a task complete (moves it to the Done list)."""
        headers = await self._get_headers()
        response = await self._client.patch(
            f"/v1/tasks/{task_id}/complete", headers=headers
        )
        _raise_with_body(response)
        return response.json().get("task", {})
