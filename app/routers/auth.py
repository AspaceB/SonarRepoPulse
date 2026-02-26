import logging
from pathlib import Path

from fastapi import APIRouter, Request, HTTPException
from app.services.browser_streaming import auth_session_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.get("/status")
async def auth_status(request: Request):
    """Return current authentication status for browser-auth services."""
    settings = request.app.state.settings
    config = request.app.state.config
    services = []

    for service_name, auth_attr, client_attr, session_file, base_url_fn in [
        (
            "bitbucket",
            "bitbucket_auth_method",
            "bb_browser_client",
            ".bitbucket_session.json",
            lambda c: c.scm.bitbucket.base_url if c.scm.bitbucket else None,
        ),
        (
            "sonarqube",
            "sonar_auth_method",
            "sonar_browser_client",
            ".sonar_session.json",
            lambda c: c.sonar.base_url if c.sonar else None,
        ),
    ]:
        auth_method = getattr(settings, auth_attr, "token")
        if auth_method != "browser":
            services.append({
                "name": service_name,
                "auth_method": "token",
                "connected": True,
                "authenticating": False,
            })
            continue

        client = getattr(request.app.state, client_attr, None)
        connected = client is not None and getattr(client, "is_authenticated", False)
        session_exists = Path(session_file).exists()
        authenticating = auth_session_manager.has_active_session(service_name)
        base_url = base_url_fn(config)

        services.append({
            "name": service_name,
            "auth_method": "browser",
            "connected": connected,
            "session_exists": session_exists,
            "base_url": base_url,
            "authenticating": authenticating,
        })

    return {"services": services}


@router.post("/connect/{service}")
async def connect_service(service: str, request: Request):
    """Open a browser window for SSO login, poll until complete, save session.

    The browser opens as a visible window on the server. The user completes
    SSO login (including MFA/fingerprint/PIN) in that window. Once auth is
    detected, the session is saved and the browser closes automatically.
    """
    if service not in ("bitbucket", "sonarqube"):
        raise HTTPException(status_code=400, detail="Invalid service. Use 'bitbucket' or 'sonarqube'.")

    if auth_session_manager.has_active_session(service):
        raise HTTPException(status_code=409, detail=f"{service} authentication already in progress.")

    config = request.app.state.config

    if service == "bitbucket":
        if not config.scm.bitbucket:
            raise HTTPException(status_code=404, detail="Bitbucket not configured")
        base_url = config.scm.bitbucket.base_url
        session_path = Path(".bitbucket_session.json")
    else:
        if not config.sonar:
            raise HTTPException(status_code=404, detail="SonarQube not configured")
        base_url = config.sonar.base_url
        session_path = Path(".sonar_session.json")

    session = None
    try:
        # Launch visible browser
        session = await auth_session_manager.start_session(service, base_url, session_path)

        # Poll until SSO login completes (user interacts with browser window)
        success, username = await session.poll_until_authenticated()

        if success:
            await session.save_session()
            await session.close()
            await auth_session_manager.close_session(service)

            # Reload the main browser client so it uses the new session
            await _reload_browser_client(request.app, service)

            label = "Bitbucket" if service == "bitbucket" else "SonarQube"
            return {
                "status": "connected",
                "service": service,
                "user": username,
                "message": f"{label} authentication successful. Session saved.",
            }
        else:
            raise HTTPException(status_code=408, detail="Authentication timed out after 5 minutes.")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Auth connect error for {service}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await auth_session_manager.close_session(service)


async def _reload_browser_client(app, service: str) -> None:
    """After successful auth, reload the browser client on app.state."""
    try:
        if service == "bitbucket":
            client = getattr(app.state, "bb_browser_client", None)
            if client:
                await client.close()
                await client.start_passive()
                logger.info("Bitbucket browser client reloaded with new session")
        elif service == "sonarqube":
            client = getattr(app.state, "sonar_browser_client", None)
            if client:
                await client.close()
                await client.start_passive()
                logger.info("SonarQube browser client reloaded with new session")
    except Exception as e:
        logger.error(f"Failed to reload {service} browser client: {e}")
