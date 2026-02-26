import asyncio
import logging
from typing import Optional
import httpx

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
BACKOFF_BASE = 1  # seconds


class SonarClient:
    """SonarQube / SonarCloud API client for project search, metrics, and issue counts.

    Works with both self-hosted SonarQube and SonarCloud.
    For SonarCloud, pass the organization parameter.
    For self-hosted SonarQube, leave organization as None.
    """

    def __init__(self, token: str, base_url: str,
                 organization: Optional[str] = None,
                 proxy: Optional[str] = None, ssl_verify: bool = True):
        self._base_url = base_url.rstrip("/")
        self._organization = organization
        self._headers = {"Authorization": f"Bearer {token}"}
        self._proxy = proxy
        self._ssl_verify = ssl_verify

    async def _request_with_retry(
        self, client: httpx.AsyncClient, url: str, params: dict
    ) -> httpx.Response:
        """Execute GET request with exponential backoff retry on 429."""
        for attempt in range(MAX_RETRIES):
            resp = await client.get(url, params=params)
            if resp.status_code == 429:
                wait = BACKOFF_BASE * (2 ** attempt)
                logger.warning(
                    f"Sonar rate limited (429), retrying in {wait}s "
                    f"(attempt {attempt + 1}/{MAX_RETRIES})"
                )
                await asyncio.sleep(wait)
                continue
            return resp
        return resp  # Return last response even if still 429

    async def search_project(self, repo_name: str) -> Optional[str]:
        """
        Search Sonar for a project matching the given repo name.

        Uses /api/components/search (works for regular users) with
        qualifiers=TRK to find projects.  Falls back to
        /api/projects/search for SonarCloud (where organization is set).

        Returns the project key if a match is found, else None.
        """
        # /api/components/search works for regular users on self-hosted SonarQube
        # /api/projects/search requires admin on SonarQube but works on SonarCloud
        if self._organization:
            url = f"{self._base_url}/api/projects/search"
            params = {"q": repo_name, "ps": 100, "organization": self._organization}
        else:
            url = f"{self._base_url}/api/components/search"
            params = {"q": repo_name, "qualifiers": "TRK", "ps": 100}

        async with httpx.AsyncClient(
            headers=self._headers, timeout=30.0,
            proxy=self._proxy, verify=self._ssl_verify,
        ) as client:
            resp = await self._request_with_retry(client, url, params)
            if resp.status_code != 200:
                logger.error(
                    f"Sonar project search failed for '{repo_name}': "
                    f"HTTP {resp.status_code}"
                )
                return None
            data = resp.json()

        repo_lower = repo_name.lower()
        repo_underscore = repo_name.replace("-", "_").lower()

        for comp in data.get("components", []):
            comp_key = comp.get("key", "").lower()
            comp_name = comp.get("name", "").lower()

            if (
                comp_key.endswith(f"_{repo_lower}")
                or comp_key.endswith(f"_{repo_underscore}")
                or comp_name == repo_lower
                or comp_key == repo_lower
                or repo_lower in comp_key
            ):
                matched_key = comp.get("key", "")
                logger.info(
                    f"Sonar match found: repo='{repo_name}' -> key='{matched_key}'"
                )
                return matched_key

        logger.debug(f"No Sonar match for repo '{repo_name}'")
        return None

    async def list_all_projects(self) -> list:
        """Fetch ALL SonarQube projects via paginated API calls.

        Returns list of SonarProject instances for index building.
        """
        from app.services.sonar_project_index import SonarProject

        projects = []
        page = 1
        page_size = 500

        if self._organization:
            endpoint = f"{self._base_url}/api/projects/search"
            base_params = {"ps": page_size, "organization": self._organization}
        else:
            endpoint = f"{self._base_url}/api/components/search"
            base_params = {"qualifiers": "TRK", "ps": page_size}

        async with httpx.AsyncClient(
            headers=self._headers, timeout=60.0,
            proxy=self._proxy, verify=self._ssl_verify,
        ) as client:
            while True:
                params = {**base_params, "p": page}
                resp = await self._request_with_retry(client, endpoint, params)
                if resp.status_code != 200:
                    logger.error(f"Failed to list projects (page {page}): HTTP {resp.status_code}")
                    break

                data = resp.json()
                for comp in data.get("components", []):
                    projects.append(SonarProject(
                        key=comp.get("key", ""),
                        name=comp.get("name", ""),
                    ))

                total = data.get("paging", {}).get("total", 0)
                logger.info(f"Fetched SonarQube projects page {page}: {len(data.get('components', []))} items (total: {total})")

                if page * page_size >= total or not data.get("components"):
                    break
                page += 1

        logger.info(f"Total SonarQube projects fetched: {len(projects)}")
        return projects

    async def get_metrics(self, project_key: str, branch: str) -> dict:
        """
        Fetch code quality measures for a project on a specific branch.

        Uses: GET /api/measures/component
        Metrics: coverage, complexity, duplicated_lines_density, ncloc
        """
        url = f"{self._base_url}/api/measures/component"
        params = {
            "component": project_key,
            "branch": branch,
            "metricKeys": "coverage,complexity,duplicated_lines_density,ncloc",
        }

        async with httpx.AsyncClient(
            headers=self._headers, timeout=30.0,
            proxy=self._proxy, verify=self._ssl_verify,
        ) as client:
            resp = await self._request_with_retry(client, url, params)
            if resp.status_code == 404:
                logger.debug(
                    f"No Sonar analysis for {project_key} on branch '{branch}'"
                )
                return {}
            if resp.status_code != 200:
                logger.error(
                    f"Sonar metrics fetch failed for {project_key}/{branch}: "
                    f"HTTP {resp.status_code}"
                )
                return {}
            data = resp.json()

        measures = {}
        for m in data.get("component", {}).get("measures", []):
            measures[m["metric"]] = m["value"]
        return measures

    async def get_high_risk_violations(self, project_key: str, branch: str) -> int:
        """
        Count CRITICAL and BLOCKER severity unresolved issues.

        Uses: GET /api/issues/search with severities=CRITICAL,BLOCKER
        Only fetches the total count (ps=1 to minimize payload).
        """
        url = f"{self._base_url}/api/issues/search"
        params = {
            "componentKeys": project_key,
            "branch": branch,
            "severities": "CRITICAL,BLOCKER",
            "resolved": "false",
            "ps": 1,
        }

        async with httpx.AsyncClient(
            headers=self._headers, timeout=30.0,
            proxy=self._proxy, verify=self._ssl_verify,
        ) as client:
            resp = await self._request_with_retry(client, url, params)
            if resp.status_code == 404:
                return 0
            if resp.status_code != 200:
                logger.error(
                    f"Sonar issues fetch failed for {project_key}/{branch}: "
                    f"HTTP {resp.status_code}"
                )
                return 0
            data = resp.json()

        return data.get("total", 0)

    async def get_last_analysis_date(self, project_key: str) -> Optional[str]:
        """Fetch the date of the most recent analysis for a project."""
        url = f"{self._base_url}/api/project_analyses/search"
        params = {"project": project_key, "ps": 1}

        async with httpx.AsyncClient(
            headers=self._headers, timeout=30.0,
            proxy=self._proxy, verify=self._ssl_verify,
        ) as client:
            resp = await self._request_with_retry(client, url, params)
            if resp.status_code != 200:
                return None
            data = resp.json()
            if data.get("analyses"):
                return data["analyses"][0].get("date")
        return None

    async def fetch_all_metrics(
        self, project_key: str, branches: list[str]
    ) -> Optional[dict]:
        """
        Try each branch in priority order until one returns data.
        Combines measures + high-risk violation count.
        Returns dict of metrics or None if no branch has analysis.
        """
        for branch in branches:
            metrics = await self.get_metrics(project_key, branch)
            if metrics:
                violations = await self.get_high_risk_violations(project_key, branch)
                last_analysis = await self.get_last_analysis_date(project_key)
                return {
                    "coverage": (
                        float(metrics["coverage"])
                        if metrics.get("coverage") else None
                    ),
                    "complexity": (
                        int(metrics["complexity"])
                        if metrics.get("complexity") else None
                    ),
                    "duplication": (
                        float(metrics["duplicated_lines_density"])
                        if metrics.get("duplicated_lines_density") else None
                    ),
                    "ncloc": (
                        int(metrics["ncloc"])
                        if metrics.get("ncloc") else None
                    ),
                    "high_risk_violations": violations,
                    "last_analysis_date": last_analysis,
                }

        logger.info(
            f"No analysis found for {project_key} on any branch: {branches}"
        )
        return None
