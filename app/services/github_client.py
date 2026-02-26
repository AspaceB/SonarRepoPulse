import re
import logging
import httpx
from typing import Optional

logger = logging.getLogger(__name__)


class GitHubClient:
    """GitHub REST API client."""

    BASE_URL = "https://api.github.com"

    def __init__(self, token: str, proxy: Optional[str] = None,
                 ssl_verify: bool = True):
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self._proxy = proxy
        self._ssl_verify = ssl_verify

    async def list_repos(self, org: str) -> list[dict]:
        """
        List all repositories in a GitHub organization.

        Uses: GET /orgs/{org}/repos?per_page=100&type=all
        Handles pagination via the Link header.
        """
        repos = []
        url = f"{self.BASE_URL}/orgs/{org}/repos"
        params = {"per_page": 100, "type": "all"}

        async with httpx.AsyncClient(
            headers=self._headers, timeout=30.0,
            proxy=self._proxy, verify=self._ssl_verify,
        ) as client:
            while url:
                logger.info(f"Fetching GitHub repos: {url}")
                resp = await client.get(url, params=params)
                resp.raise_for_status()

                for repo in resp.json():
                    repos.append({
                        "slug": repo["name"],
                        "name": repo["name"],
                        "full_name": repo["full_name"],
                        "mainbranch": repo.get("default_branch", "main"),
                        "project_key": org,
                        "project_name": org,
                    })

                url = self._parse_next_link(resp.headers.get("Link", ""))
                params = None  # next URL is fully qualified, don't override query string

        logger.info(f"Found {len(repos)} repos in GitHub org={org}")
        return repos

    @staticmethod
    def _parse_next_link(link_header: str) -> Optional[str]:
        """Extract the 'next' URL from GitHub's Link header."""
        if not link_header:
            return None
        for part in link_header.split(","):
            if 'rel="next"' in part:
                match = re.search(r"<(.+?)>", part)
                if match:
                    return match.group(1)
        return None
