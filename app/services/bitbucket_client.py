import logging
from typing import Optional
import httpx

logger = logging.getLogger(__name__)


class BitbucketClient:
    """Bitbucket Server / Data Center REST API 1.0 client."""

    def __init__(self, base_url: str, token: str,
                 proxy: Optional[str] = None, ssl_verify: bool = True):
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._proxy = proxy
        self._ssl_verify = ssl_verify

    async def list_projects(self) -> list[dict]:
        """List all Bitbucket projects the authenticated user can access."""
        projects = []
        url = f"{self._base_url}/rest/api/1.0/projects"
        start = 0
        limit = 100

        async with httpx.AsyncClient(
            headers=self._headers, timeout=30.0,
            proxy=self._proxy, verify=self._ssl_verify,
        ) as client:
            while True:
                params = {"start": start, "limit": limit}
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()

                for proj in data.get("values", []):
                    projects.append({
                        "key": proj["key"],
                        "name": proj.get("name", proj["key"]),
                    })

                if data.get("isLastPage", True):
                    break
                start = data.get("nextPageStart", start + limit)

        logger.info(f"Found {len(projects)} accessible Bitbucket projects")
        return projects

    async def list_repos(self, project_key: str) -> list[dict]:
        """
        List all repositories in a Bitbucket Server project.

        Uses: GET /rest/api/1.0/projects/{projectKey}/repos
        Handles pagination via start/limit/isLastPage/nextPageStart.
        """
        repos = []
        url = f"{self._base_url}/rest/api/1.0/projects/{project_key}/repos"
        start = 0
        limit = 100

        async with httpx.AsyncClient(
            headers=self._headers, timeout=30.0,
            proxy=self._proxy, verify=self._ssl_verify,
        ) as client:
            while True:
                params = {"start": start, "limit": limit}
                logger.info(f"Fetching Bitbucket Server repos: {url} (start={start})")
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()

                for repo in data.get("values", []):
                    # Resolve default branch name
                    default_branch = "main"
                    if repo.get("defaultBranch"):
                        # defaultBranch can be a string or dict depending on BB version
                        branch = repo["defaultBranch"]
                        if isinstance(branch, dict):
                            default_branch = branch.get("displayId", "main")
                        else:
                            default_branch = str(branch)

                    project = repo.get("project", {})
                    repos.append({
                        "slug": repo["slug"],
                        "name": repo.get("name", repo["slug"]),
                        "full_name": f"{project_key}/{repo['slug']}",
                        "mainbranch": default_branch,
                        "project_key": project_key,
                        "project_name": project.get("name", project_key),
                    })

                if data.get("isLastPage", True):
                    break
                start = data.get("nextPageStart", start + limit)

        logger.info(
            f"Found {len(repos)} repos in Bitbucket Server project={project_key}"
        )
        return repos
