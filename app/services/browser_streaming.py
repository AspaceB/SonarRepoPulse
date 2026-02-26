import asyncio
import logging
from typing import Optional
from pathlib import Path

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

logger = logging.getLogger(__name__)

MAX_DURATION_SECONDS = 300  # 5 minutes
POLL_INTERVAL = 2  # seconds between auth checks


class BrowserAuthSession:
    """Opens a visible (headed) browser for SSO login and polls for completion.

    WebAuthn/FIDO2 (fingerprint, PIN, security key) requires a real browser
    window with OS-level prompts, so headless mode won't work for MFA.
    The user interacts directly with the browser window while we poll
    for auth completion in the background.
    """

    def __init__(
        self,
        service: str,
        base_url: str,
        session_path: Path,
    ):
        self.service = service  # "bitbucket" or "sonarqube"
        self.base_url = base_url.rstrip("/")
        self.session_path = session_path
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._active = False

    async def start(self) -> None:
        """Launch a visible browser and navigate to the SSO entry point."""
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._context = await self._browser.new_context(
            viewport={"width": 1280, "height": 900},
        )
        self._page = await self._context.new_page()
        self._active = True

        logger.info(f"Auth browser opened for {self.service}: {self.base_url}")
        await self._page.goto(self.base_url, wait_until="domcontentloaded", timeout=60000)

    async def poll_until_authenticated(self) -> tuple[bool, Optional[str]]:
        """Poll until SSO login completes or timeout. Returns (success, username)."""
        max_checks = MAX_DURATION_SECONDS // POLL_INTERVAL

        for i in range(max_checks):
            await asyncio.sleep(POLL_INTERVAL)
            try:
                is_done, username = await self.check_auth_complete()
                if is_done:
                    # Wait for cookies to fully settle, then navigate to base URL
                    # to trigger any deferred cookie writes from the SSO flow
                    logger.info(f"{self.service} SSO detected — waiting for session to settle...")
                    await asyncio.sleep(3)
                    await self._page.goto(self.base_url, wait_until="networkidle", timeout=30000)
                    await asyncio.sleep(2)

                    # Re-validate to confirm session is stable
                    is_done2, username2 = await self.check_auth_complete()
                    if is_done2:
                        logger.info(f"{self.service} SSO login confirmed (user: {username2})")
                        return True, username2
                    else:
                        logger.warning(f"{self.service} SSO validation passed initially but failed on re-check, continuing poll...")
            except Exception as e:
                logger.debug(f"Auth check #{i} failed: {e}")

        logger.warning(f"{self.service} SSO login timed out after {MAX_DURATION_SECONDS}s")
        return False, None

    async def check_auth_complete(self) -> tuple[bool, Optional[str]]:
        """Check whether the SSO login has completed successfully."""
        if self.service == "bitbucket":
            return await self._validate_bitbucket()
        elif self.service == "sonarqube":
            return await self._validate_sonarqube()
        return False, None

    async def save_session(self) -> None:
        """Persist the authenticated browser session to disk."""
        await self._context.storage_state(path=str(self.session_path))
        logger.info(f"Session saved to {self.session_path}")

    async def close(self) -> None:
        """Clean up browser resources."""
        self._active = False
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        self._page = None
        self._context = None

    @property
    def is_active(self) -> bool:
        return self._active

    # ------------------------------------------------------------------
    # Service-specific auth validation
    # ------------------------------------------------------------------

    async def _validate_bitbucket(self) -> tuple[bool, Optional[str]]:
        """Check if Bitbucket SSO login is complete."""
        base_host = self.base_url.split("//")[-1].split("/")[0]
        current = self._page.url

        if base_host not in current or "login" in current.lower() or "openid" in current.lower():
            return False, None

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
            if not valid:
                return False, None

            user = await self._page.evaluate(
                """async (url) => {
                    try {
                        const resp = await fetch(url, { credentials: 'include' });
                        const text = await resp.text();
                        return text.trim() || null;
                    } catch { return null; }
                }""",
                f"{self.base_url}/plugins/servlet/applinks/whoami",
            )
            return True, user
        except Exception as e:
            logger.debug(f"Bitbucket auth check failed: {e}")
            return False, None

    async def _validate_sonarqube(self) -> tuple[bool, Optional[str]]:
        """Check if SonarQube SSO login is complete."""
        base_host = self.base_url.split("//")[-1].split("/")[0]
        current = self._page.url

        if base_host not in current or "login" in current.lower() or "openid" in current.lower():
            return False, None

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
            return valid, None
        except Exception as e:
            logger.debug(f"SonarQube auth check failed: {e}")
            return False, None


class AuthSessionManager:
    """Manages active auth sessions. One session per service at a time."""

    def __init__(self):
        self._sessions: dict[str, BrowserAuthSession] = {}
        self._lock = asyncio.Lock()

    async def start_session(
        self, service: str, base_url: str, session_path: Path
    ) -> BrowserAuthSession:
        async with self._lock:
            if service in self._sessions:
                await self._sessions[service].close()
            session = BrowserAuthSession(service, base_url, session_path)
            await session.start()
            self._sessions[service] = session
            return session

    async def close_session(self, service: str) -> None:
        async with self._lock:
            if service in self._sessions:
                await self._sessions[service].close()
                del self._sessions[service]

    def has_active_session(self, service: str) -> bool:
        return service in self._sessions and self._sessions[service].is_active


# Module-level singleton
auth_session_manager = AuthSessionManager()
