import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI

from app.config import Settings, load_app_config, load_sonar_mappings
from app.routers import health, scan, reports, discovery, ui, auth
from app.services.bitbucket_client import BitbucketClient
from app.services.github_client import GitHubClient
from app.services.sonarcloud_client import SonarClient
from app.services.report_generator import ReportGenerator
from app.services.scanner import Scanner
from app.database import store
from app.scheduler.setup import configure_scheduler, start_scheduler, shutdown_scheduler
from app.scheduler.jobs import set_scanner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Load settings and config
settings = Settings()
config = load_app_config(settings.config_path)
mappings = load_sonar_mappings(settings.sonar_mappings_path)

# Resolve proxy URL (HTTPS_PROXY takes priority)
proxy_url = settings.https_proxy or settings.http_proxy or None
if proxy_url:
    logger.info(f"Using proxy: {proxy_url}")

# Initialize SCM clients
bb_client = None
_bb_browser_client = None  # track for cleanup

if config.scm.bitbucket:
    if settings.bitbucket_auth_method == "browser":
        from app.services.bitbucket_browser_client import BitbucketBrowserClient
        bb_client = BitbucketBrowserClient(
            base_url=config.scm.bitbucket.base_url,
        )
        _bb_browser_client = bb_client
        logger.info(f"Bitbucket browser client initialized (SSO mode): {config.scm.bitbucket.base_url}")
    elif settings.bitbucket_token:
        bb_client = BitbucketClient(
            base_url=config.scm.bitbucket.base_url,
            token=settings.bitbucket_token,
            proxy=proxy_url, ssl_verify=settings.ssl_verify,
        )
        logger.info(f"Bitbucket Server client initialized: {config.scm.bitbucket.base_url}")

gh_client = None
if config.scm.github and settings.github_token:
    gh_client = GitHubClient(
        settings.github_token,
        proxy=proxy_url, ssl_verify=settings.ssl_verify,
    )
    logger.info("GitHub client initialized")

# Initialize Sonar client
sonar_client = None
_sonar_browser_client = None  # track for cleanup

if settings.sonar_auth_method == "browser":
    from app.services.sonar_browser_client import SonarBrowserClient
    sonar_client = SonarBrowserClient(
        base_url=config.sonar.base_url,
        organization=config.sonar.organization,
    )
    _sonar_browser_client = sonar_client
    logger.info(f"Sonar browser client initialized (SSO mode): {config.sonar.base_url}")
else:
    sonar_client = SonarClient(
        token=settings.sonar_token,
        base_url=config.sonar.base_url,
        organization=config.sonar.organization,
        proxy=proxy_url, ssl_verify=settings.ssl_verify,
    )
    logger.info(f"Sonar token client initialized: {config.sonar.base_url}")

# Initialize report generator
report_gen = ReportGenerator(output_dir=config.reports.output_dir)

# Initialize scanner (orchestrator)
scanner_service = Scanner(
    bitbucket_client=bb_client,
    github_client=gh_client,
    sonar_client=sonar_client,
    sonar_mappings=mappings,
    app_config=config,
    report_generator=report_gen,
    store=store,
)

# Make scanner available to scheduled jobs
set_scanner(scanner_service)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage browser clients and scheduler lifecycle with the FastAPI app."""
    if _bb_browser_client is not None:
        await _bb_browser_client.start_passive()
    if _sonar_browser_client is not None:
        await _sonar_browser_client.start_passive()
    configure_scheduler(config)
    start_scheduler()
    yield
    shutdown_scheduler()
    if _sonar_browser_client is not None:
        await _sonar_browser_client.close()
    if _bb_browser_client is not None:
        await _bb_browser_client.close()


app = FastAPI(
    title="SCM Sonar Metrics API",
    description=(
        "API to fetch repositories from Bitbucket Server/DC and GitHub, "
        "retrieve SonarQube code quality metrics, and generate CSV/Excel reports."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# Store config, scanner, and SCM clients on app state for route handlers
app.state.config = config
app.state.settings = settings
app.state.scanner = scanner_service
app.state.bb_client = bb_client
app.state.gh_client = gh_client
app.state.bb_browser_client = _bb_browser_client
app.state.sonar_browser_client = _sonar_browser_client

# Register routers
app.include_router(ui.router)
app.include_router(health.router)
app.include_router(scan.router)
app.include_router(reports.router)
app.include_router(discovery.router)
app.include_router(auth.router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
