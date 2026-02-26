import asyncio
import logging
import urllib.parse
from typing import Optional
from pathlib import Path

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

logger = logging.getLogger(__name__)

SESSION_FILE = ".sonar_session.json"


class SonarBrowserClient:
    """SonarQube client that authenticates through SSO using a real browser.

    Uses Playwright to handle the OpenID Connect SSO login flow, then
    makes API calls via fetch() from within the authenticated browser context.
    Session cookies are saved to disk so subsequent runs skip SSO.
    """

    def __init__(
        self,
        base_url: str,
        organization: Optional[str] = None,
        session_path: str = SESSION_FILE,
    ):
        self.base_url = base_url.rstrip("/")
        self.organization = organization
        self.session_path = Path(session_path)
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._authenticated = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Launch browser and restore or create an authenticated session."""
        self._playwright = await async_playwright().start()

        # Try restoring a saved session first
        if self.session_path.exists():
            logger.info("Restoring saved SonarQube browser session ...")
            self._browser = await self._playwright.chromium.launch(headless=True)
            self._context = await self._browser.new_context(
                storage_state=str(self.session_path)
            )
            self._page = await self._context.new_page()

            if await self._is_session_valid():
                self._authenticated = True
                # Navigate to base so fetch() calls use same origin
                await self._page.goto(self.base_url, wait_until="domcontentloaded", timeout=30000)
                logger.info("SonarQube browser session restored successfully")
                return

            logger.info("Saved session expired, re-authenticating ...")
            await self._browser.close()

        # Interactive SSO login
        await self._interactive_login()

    async def start_passive(self) -> None:
        """Launch browser and restore session if available, but do NOT open interactive login.

        Used for remote/headless deployments where interactive login is handled
        via the WebSocket streaming endpoint in the web UI.
        """
        self._playwright = await async_playwright().start()

        if self.session_path.exists():
            logger.info("Restoring saved SonarQube browser session ...")
            self._browser = await self._playwright.chromium.launch(headless=True)
            self._context = await self._browser.new_context(
                storage_state=str(self.session_path)
            )
            self._page = await self._context.new_page()

            if await self._is_session_valid():
                self._authenticated = True
                await self._page.goto(self.base_url, wait_until="domcontentloaded", timeout=30000)
                logger.info("SonarQube browser session restored successfully")
                return

            logger.warning("Saved SonarQube session expired. Use the web UI to re-authenticate.")
            await self._browser.close()
            self._browser = None
            self._context = None
            self._page = None
        else:
            logger.warning("No SonarQube session file found. Use the web UI to authenticate.")

    @property
    def is_authenticated(self) -> bool:
        return self._authenticated

    async def close(self) -> None:
        """Release browser resources."""
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        self._authenticated = False

    # ------------------------------------------------------------------
    # Authentication helpers
    # ------------------------------------------------------------------

    async def _is_session_valid(self) -> bool:
        """Check whether the current browser session is still authenticated."""
        try:
            response = await self._page.goto(
                f"{self.base_url}/api/authentication/validate",
                wait_until="networkidle",
                timeout=15000,
            )
            if response and response.ok:
                data = await response.json()
                return data.get("valid", False)
        except Exception as e:
            logger.debug(f"Session validation failed: {e}")
        return False

    async def _interactive_login(self) -> None:
        """Open a visible browser for the user to complete SSO login."""
        logger.info("Opening browser for SonarQube SSO login ...")
        print("\n" + "=" * 60)
        print("  SONARQUBE SSO LOGIN")
        print("=" * 60)
        print("  A browser window will open.")
        print("  Please log in through your corporate SSO.")
        print("  The window will close automatically once login succeeds.")
        print("=" * 60 + "\n")

        self._browser = await self._playwright.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._context = await self._browser.new_context(
            viewport={"width": 1280, "height": 900},
        )
        self._page = await self._context.new_page()

        # Navigate to SonarQube — SSO plugin will redirect to IdP
        await self._page.goto(self.base_url, wait_until="domcontentloaded", timeout=60000)

        # Wait for the user to complete SSO. DO NOT navigate away from the
        # page (that was causing the constant-refresh bug). Instead, just
        # watch the URL — once it returns to the SonarQube base without
        # "login" or "openid" in the path, the user has authenticated.
        max_wait_seconds = 300  # 5 minutes
        base_host = self.base_url.split("//")[-1].split("/")[0]

        for _ in range(max_wait_seconds):
            current = self._page.url
            # User has landed back on SonarQube (not on the IdP or login page)
            if base_host in current and "login" not in current.lower() and "openid" not in current.lower():
                # Give the page a moment to settle, then validate via JS fetch
                await asyncio.sleep(2)
                try:
                    valid = await self._page.evaluate(
                        """async (url) => {
                            try {
                                const r = await fetch(url, { credentials: 'include' });
                                const j = await r.json();
                                return j.valid === true;
                            } catch { return false; }
                        }""",
                        f"{self.base_url}/api/authentication/validate",
                    )
                    if valid:
                        break
                except Exception:
                    pass
            await asyncio.sleep(1)
        else:
            await self.close()
            raise TimeoutError("SSO login timed out after 5 minutes")

        # Persist session for future runs
        await self._context.storage_state(path=str(self.session_path))
        self._authenticated = True
        logger.info("SSO login successful — session saved to %s", self.session_path)

        # Switch to headless for API work
        await self._browser.close()
        self._browser = await self._playwright.chromium.launch(headless=True)
        self._context = await self._browser.new_context(
            storage_state=str(self.session_path)
        )
        self._page = await self._context.new_page()
        await self._page.goto(self.base_url, wait_until="domcontentloaded", timeout=30000)

    async def _ensure_session(self) -> None:
        """Make sure we have an active authenticated session."""
        if not self._authenticated:
            await self.start()

    # ------------------------------------------------------------------
    # Low-level API helper
    # ------------------------------------------------------------------

    async def _api_get(self, endpoint: str, params: Optional[dict] = None) -> Optional[dict]:
        """Make an authenticated API call using fetch() inside the browser page."""
        await self._ensure_session()

        url = f"{self.base_url}{endpoint}"
        if params:
            qs = urllib.parse.urlencode(
                {k: v for k, v in params.items() if v is not None}
            )
            url = f"{url}?{qs}"

        try:
            data = await self._page.evaluate(
                """async (url) => {
                    const resp = await fetch(url, { credentials: 'include' });
                    if (!resp.ok) {
                        return { __error: true, __status: resp.status, __body: (await resp.text()).slice(0, 500) };
                    }
                    return await resp.json();
                }""",
                url,
            )

            if isinstance(data, dict) and data.get("__error"):
                status = data.get("__status")
                body = data.get("__body", "")
                logger.error(f"Sonar API {endpoint} returned HTTP {status}: {body[:200]}")

                # If unauthorized, invalidate session so next call re-authenticates
                if status in (401, 403):
                    self._authenticated = False
                    self.session_path.unlink(missing_ok=True)
                return None

            return data

        except Exception as e:
            logger.error(f"Browser fetch failed for {endpoint}: {e}")
            return None

    # ------------------------------------------------------------------
    # Public API — mirrors SonarClient interface
    # ------------------------------------------------------------------

    async def search_project(self, repo_name: str) -> Optional[str]:
        """Search SonarQube for a project matching the given repo name.

        Uses /api/components/search (works for regular users) instead of
        /api/projects/search (requires admin rights).
        """
        params = {"q": repo_name, "qualifiers": "TRK", "ps": "100"}
        if self.organization:
            params["organization"] = self.organization

        data = await self._api_get("/api/components/search", params)
        if not data or "components" not in data:
            return None

        components = data["components"]
        if not components:
            return None

        repo_lower = repo_name.lower()
        repo_underscore = repo_name.replace("-", "_").lower()

        # Exact key suffix match
        for c in components:
            key = c.get("key", "").lower()
            name = c.get("name", "").lower()
            if (
                key.endswith(f"_{repo_lower}")
                or key.endswith(f"_{repo_underscore}")
                or name == repo_lower
                or key == repo_lower
                or repo_lower in key
            ):
                matched = c.get("key", "")
                logger.info(f"Sonar match: repo='{repo_name}' -> key='{matched}'")
                return matched

        logger.debug(f"No Sonar match for repo '{repo_name}'")
        return None

    async def list_all_projects(self) -> list:
        """Fetch ALL SonarQube projects via paginated API calls."""
        from app.services.sonar_project_index import SonarProject

        projects = []
        page = 1
        page_size = 500

        while True:
            params = {"qualifiers": "TRK", "ps": str(page_size), "p": str(page)}
            if self.organization:
                params["organization"] = self.organization

            data = await self._api_get("/api/components/search", params)
            if not data or "components" not in data:
                logger.error(f"Failed to list projects (page {page})")
                break

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
        """Fetch code quality measures for a project on a specific branch."""
        params = {
            "component": project_key,
            "branch": branch,
            "metricKeys": "coverage,complexity,duplicated_lines_density,ncloc",
        }

        data = await self._api_get("/api/measures/component", params)
        if not data or "component" not in data:
            return {}

        measures = {}
        for m in data.get("component", {}).get("measures", []):
            measures[m["metric"]] = m["value"]
        return measures

    async def get_high_risk_violations(self, project_key: str, branch: str) -> int:
        """Count CRITICAL + BLOCKER unresolved issues."""
        params = {
            "componentKeys": project_key,
            "branch": branch,
            "severities": "CRITICAL,BLOCKER",
            "resolved": "false",
            "ps": "1",
        }

        data = await self._api_get("/api/issues/search", params)
        if not data:
            return 0
        return data.get("total", 0)

    async def get_last_analysis_date(self, project_key: str) -> Optional[str]:
        """Fetch the date of the most recent analysis for a project."""
        params = {"project": project_key, "ps": "1"}
        data = await self._api_get("/api/project_analyses/search", params)
        if data and data.get("analyses"):
            return data["analyses"][0].get("date")
        return None

    async def fetch_all_metrics(
        self, project_key: str, branches: list[str]
    ) -> Optional[dict]:
        """Try each branch in priority order until one returns data."""
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

        logger.info(f"No analysis found for {project_key} on any branch: {branches}")
        return None
