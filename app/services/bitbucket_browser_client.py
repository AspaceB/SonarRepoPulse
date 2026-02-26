import asyncio
import logging
import urllib.parse
from typing import Optional
from pathlib import Path

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

logger = logging.getLogger(__name__)

SESSION_FILE = ".bitbucket_session.json"


class BitbucketBrowserClient:
    """Bitbucket Server/DC client that authenticates through SSO using a real browser.

    Uses Playwright to handle the OpenID Connect SSO login flow, then
    makes REST API calls via fetch() from within the authenticated browser context.
    Session cookies are saved to disk so subsequent runs skip SSO.
    """

    def __init__(self, base_url: str, session_path: str = SESSION_FILE):
        self.base_url = base_url.rstrip("/")
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

        if self.session_path.exists():
            logger.info("Restoring saved Bitbucket browser session ...")
            self._browser = await self._playwright.chromium.launch(headless=True)
            self._context = await self._browser.new_context(
                storage_state=str(self.session_path)
            )
            self._page = await self._context.new_page()

            if await self._is_session_valid():
                self._authenticated = True
                await self._page.goto(self.base_url, wait_until="domcontentloaded", timeout=30000)
                user = await self._get_current_user()
                logger.info(f"Bitbucket browser session restored successfully (user: {user})")
                return

            logger.info("Saved session expired, re-authenticating ...")
            await self._browser.close()

        await self._interactive_login()

    async def start_passive(self) -> None:
        """Launch browser and restore session if available, but do NOT open interactive login.

        Used for remote/headless deployments where interactive login is handled
        via the WebSocket streaming endpoint in the web UI.
        """
        self._playwright = await async_playwright().start()

        if self.session_path.exists():
            logger.info("Restoring saved Bitbucket browser session ...")
            self._browser = await self._playwright.chromium.launch(headless=True)
            self._context = await self._browser.new_context(
                storage_state=str(self.session_path)
            )
            self._page = await self._context.new_page()

            if await self._is_session_valid():
                self._authenticated = True
                await self._page.goto(self.base_url, wait_until="domcontentloaded", timeout=30000)
                user = await self._get_current_user()
                logger.info(f"Bitbucket browser session restored successfully (user: {user})")
                return

            logger.warning("Saved Bitbucket session expired. Use the web UI to re-authenticate.")
            await self._browser.close()
            self._browser = None
            self._context = None
            self._page = None
        else:
            logger.warning("No Bitbucket session file found. Use the web UI to authenticate.")

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
            # Bitbucket Server REST API - a lightweight call to verify auth
            response = await self._page.goto(
                f"{self.base_url}/rest/api/1.0/application-properties",
                wait_until="networkidle",
                timeout=15000,
            )
            if response and response.ok:
                data = await response.json()
                return "version" in data
        except Exception as e:
            logger.debug(f"Session validation failed: {e}")
        return False

    async def _interactive_login(self) -> None:
        """Open a visible browser for the user to complete SSO login."""
        logger.info("Opening browser for Bitbucket SSO login ...")
        print("\n" + "=" * 60)
        print("  BITBUCKET SSO LOGIN")
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

        await self._page.goto(self.base_url, wait_until="domcontentloaded", timeout=60000)

        # Wait for the user to complete SSO without navigating away
        max_wait_seconds = 300
        base_host = self.base_url.split("//")[-1].split("/")[0]

        for _ in range(max_wait_seconds):
            current = self._page.url
            if base_host in current and "login" not in current.lower() and "openid" not in current.lower():
                await asyncio.sleep(2)
                try:
                    valid = await self._page.evaluate(
                        """async (url) => {
                            try {
                                const r = await fetch(url, { credentials: 'include' });
                                const j = await r.json();
                                return 'version' in j;
                            } catch { return false; }
                        }""",
                        f"{self.base_url}/rest/api/1.0/application-properties",
                    )
                    if valid:
                        break
                except Exception:
                    pass
            await asyncio.sleep(1)
        else:
            await self.close()
            raise TimeoutError("SSO login timed out after 5 minutes")

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

    async def _get_current_user(self) -> Optional[str]:
        """Return the username of the currently authenticated user."""
        try:
            data = await self._page.evaluate(
                """async (url) => {
                    try {
                        const resp = await fetch(url, {
                            credentials: 'include',
                            headers: { 'Accept': 'application/json', 'X-Atlassian-Token': 'no-check' }
                        });
                        if (!resp.ok) return null;
                        const j = await resp.json();
                        return j.name || j.slug || null;
                    } catch { return null; }
                }""",
                f"{self.base_url}/rest/api/1.0/users?limit=1&filter=",
            )
            # Try /plugins/servlet/applinks/whoami as fallback
            if not data:
                data = await self._page.evaluate(
                    """async (url) => {
                        try {
                            const resp = await fetch(url, { credentials: 'include' });
                            const text = await resp.text();
                            return text.trim() || null;
                        } catch { return null; }
                    }""",
                    f"{self.base_url}/plugins/servlet/applinks/whoami",
                )
            return data
        except Exception as e:
            logger.debug(f"Failed to get current user: {e}")
            return None

    async def _ensure_session(self) -> None:
        """Make sure we have an active authenticated session."""
        if not self._authenticated:
            await self.start()

    # ------------------------------------------------------------------
    # Low-level API helper
    # ------------------------------------------------------------------

    async def _api_get(
        self, endpoint: str, params: Optional[dict] = None, *, _retried: bool = False,
    ) -> Optional[dict]:
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
                    const resp = await fetch(url, {
                        credentials: 'include',
                        headers: {
                            'Accept': 'application/json',
                            'X-Atlassian-Token': 'no-check'
                        }
                    });
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
                logger.error(f"Bitbucket API {endpoint} returned HTTP {status}: {body[:200]}")

                # On auth failure, re-authenticate once and retry
                if status in (401, 403) and not _retried:
                    logger.info("Auth failure — re-authenticating and retrying ...")
                    self._authenticated = False
                    self.session_path.unlink(missing_ok=True)
                    await self._browser.close()
                    await self._interactive_login()
                    return await self._api_get(endpoint, params, _retried=True)

                return None

            return data

        except Exception as e:
            logger.error(f"Browser fetch failed for {endpoint}: {e}")
            return None

    # ------------------------------------------------------------------
    # Public API — mirrors BitbucketClient interface
    # ------------------------------------------------------------------

    async def list_projects(self) -> list[dict]:
        """List all Bitbucket projects the authenticated user can access."""
        projects = []
        start = 0
        limit = 100

        while True:
            params = {"start": str(start), "limit": str(limit)}
            data = await self._api_get("/rest/api/1.0/projects", params)

            if not data or "values" not in data:
                logger.error("Failed to fetch Bitbucket projects")
                break

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
        """List all repositories in a Bitbucket Server project."""
        repos = []
        start = 0
        limit = 100

        # Navigate to the project page first to ensure any project-level
        # auth cookies / tokens are set (Bitbucket sometimes requires this)
        try:
            await self._page.goto(
                f"{self.base_url}/projects/{project_key}",
                wait_until="domcontentloaded",
                timeout=15000,
            )
            await asyncio.sleep(1)
        except Exception as e:
            logger.debug(f"Pre-navigation to project {project_key} page: {e}")

        while True:
            params = {"start": str(start), "limit": str(limit)}
            data = await self._api_get(
                f"/rest/api/1.0/projects/{project_key}/repos", params
            )

            if not data or "values" not in data:
                logger.error(f"Failed to fetch repos for project {project_key}")
                break

            for repo in data.get("values", []):
                default_branch = "main"
                if repo.get("defaultBranch"):
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

        logger.info(f"Found {len(repos)} repos in Bitbucket project={project_key}")
        return repos
