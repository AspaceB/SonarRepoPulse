import uuid
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from app.models import ScanRequest, ScanResult, ScanStatus, RepoMetrics
from app.database import ScanStore
from app.services.bitbucket_client import BitbucketClient
from app.services.github_client import GitHubClient
from app.services.sonarcloud_client import SonarClient
from app.services.report_generator import ReportGenerator
from app.services.sonar_project_index import SonarProjectIndex
from app.config import AppConfig

logger = logging.getLogger(__name__)

# Max concurrent Sonar requests to avoid rate limiting
SONAR_CONCURRENCY = 5


class Scanner:
    """Orchestrator: fetches repos from SCM providers, resolves Sonar
    project keys, collects metrics, and generates reports."""

    def __init__(
        self,
        bitbucket_client: Optional[BitbucketClient],
        github_client: Optional[GitHubClient],
        sonar_client: SonarClient,
        sonar_mappings: dict[str, str],
        app_config: AppConfig,
        report_generator: ReportGenerator,
        store: ScanStore,
    ):
        self.bb = bitbucket_client
        self.gh = github_client
        self.sonar = sonar_client
        self.mappings = sonar_mappings
        self.config = app_config
        self.report_gen = report_generator
        self.store = store

    async def run_scan(self, request: ScanRequest) -> str:
        """Create a scan and execute it asynchronously. Returns scan_id."""
        scan_id = str(uuid.uuid4())
        scan = ScanResult(
            scan_id=scan_id,
            status=ScanStatus.PENDING,
            started_at=datetime.now(timezone.utc),
        )
        self.store.save_scan(scan)

        asyncio.create_task(self._execute_scan(scan_id, request))
        return scan_id

    async def _execute_scan(self, scan_id: str, request: ScanRequest) -> None:
        scan = self.store.get_scan(scan_id)
        scan.status = ScanStatus.RUNNING
        self.store.save_scan(scan)

        try:
            # Step 1: Collect repos from requested providers
            all_repos = await self._collect_repos(request)

            # Optional: filter to specific repository names
            if request.repositories:
                filter_set = {r.lower() for r in request.repositories}
                all_repos = [
                    r for r in all_repos if r["slug"].lower() in filter_set
                ]

            scan.total_repos = len(all_repos)
            self.store.save_scan(scan)
            logger.info(f"Scan {scan_id}: found {len(all_repos)} total repos")

            # Step 1.5: Build SonarQube project index for smart matching
            sonar_index = None
            try:
                sonar_index = SonarProjectIndex()
                await sonar_index.build(self.sonar)
                logger.info(f"Scan {scan_id}: SonarQube index ready ({sonar_index.project_count} projects)")
            except Exception as e:
                logger.warning(f"Scan {scan_id}: failed to build Sonar index, falling back to per-repo search: {e}")
                sonar_index = None

            # Step 2: Resolve sonar keys and fetch metrics (with concurrency limit)
            branches = self.config.sonar.branches
            sem = asyncio.Semaphore(SONAR_CONCURRENCY)

            async def process_with_semaphore(repo: dict) -> RepoMetrics:
                async with sem:
                    result = await self._process_repo(repo, branches, sonar_index)
                    scan.processed_repos += 1
                    self.store.save_scan(scan)
                    return result

            metrics_list = await asyncio.gather(
                *[process_with_semaphore(r) for r in all_repos]
            )

            scan.metrics = list(metrics_list)

            # Step 3: Generate reports
            report_ids = self.report_gen.generate(
                scan_id, scan.metrics, request.formats
            )
            scan.report_ids = report_ids
            scan.status = ScanStatus.COMPLETED
            scan.completed_at = datetime.now(timezone.utc)
            logger.info(f"Scan {scan_id}: completed, {len(report_ids)} reports generated")

        except Exception as e:
            logger.exception(f"Scan {scan_id} failed: {e}")
            scan.status = ScanStatus.FAILED
            scan.error = str(e)
            scan.completed_at = datetime.now(timezone.utc)

        self.store.save_scan(scan)

    async def _collect_repos(self, request: ScanRequest) -> list[dict]:
        """Collect repositories from all requested SCM providers."""
        all_repos = []

        if "bitbucket" in request.providers:
            if not self.bb:
                logger.warning("Bitbucket requested but client not configured")
            else:
                bb_config = self.config.scm.bitbucket
                project_keys = request.bitbucket_projects or [
                    p.key for p in bb_config.projects
                ]
                for pk in project_keys:
                    try:
                        repos = await self.bb.list_repos(pk)
                        all_repos.extend(repos)
                    except Exception as e:
                        logger.error(f"Failed to fetch Bitbucket repos for project {pk}: {e}")

        if "github" in request.providers:
            if not self.gh:
                logger.warning("GitHub requested but client not configured")
            else:
                gh_config = self.config.scm.github
                org_names = request.github_orgs or [
                    o.name for o in gh_config.organizations
                ]
                for org in org_names:
                    try:
                        repos = await self.gh.list_repos(org)
                        all_repos.extend(repos)
                    except Exception as e:
                        logger.error(f"Failed to fetch GitHub repos for org {org}: {e}")

        return all_repos

    async def _process_repo(
        self, repo: dict, branches: list[str], sonar_index: Optional[SonarProjectIndex] = None
    ) -> RepoMetrics:
        """Resolve Sonar key and fetch metrics for a single repo."""
        slug = repo["slug"]
        project_name = repo["project_name"]
        lookup_key = f"{repo['project_key']}/{slug}"

        try:
            sonar_key = None

            # Strategy 1: Index-based matching (fast, no API call)
            if sonar_index:
                match = sonar_index.find_match(slug)
                if match:
                    sonar_key = match.sonar_key
                    logger.info(
                        f"Index match: '{slug}' -> '{sonar_key}' "
                        f"(strategy={match.strategy}, confidence={match.confidence})"
                    )

            # Strategy 2: Manual YAML mapping fallback
            if not sonar_key:
                sonar_key = self.mappings.get(lookup_key)
                if sonar_key:
                    logger.info(
                        f"Mapping fallback: '{lookup_key}' -> '{sonar_key}'"
                    )

            # Strategy 3: Per-repo API search (last resort)
            if not sonar_key:
                sonar_key = await self.sonar.search_project(slug)
                if sonar_key:
                    logger.info(
                        f"API search fallback: '{slug}' -> '{sonar_key}'"
                    )

            if not sonar_key:
                return RepoMetrics(
                    project=project_name,
                    repository=slug,
                    error=f"No Sonar project found for '{lookup_key}'",
                )

            # Fetch metrics from Sonar
            metrics = await self.sonar.fetch_all_metrics(sonar_key, branches)

            if metrics is None:
                return RepoMetrics(
                    project=project_name,
                    repository=slug,
                    sonar_project_key=sonar_key,
                    error=f"No analysis found on branches {branches}",
                )

            return RepoMetrics(
                project=project_name,
                repository=slug,
                sonar_project_key=sonar_key,
                coverage=metrics.get("coverage"),
                complexity=metrics.get("complexity"),
                duplication=metrics.get("duplication"),
                high_risk_violations=metrics.get("high_risk_violations"),
                ncloc=metrics.get("ncloc"),
                last_analysis_date=metrics.get("last_analysis_date"),
            )

        except Exception as e:
            logger.error(f"Error processing repo {lookup_key}: {e}")
            return RepoMetrics(
                project=project_name,
                repository=slug,
                error=str(e),
            )

    async def rescan_single_repo(
        self,
        provider: str,
        project_key: str,
        repo_slug: str,
        sonar_project_key: str,
    ) -> RepoMetrics:
        """Fetch Sonar metrics for a single repo using a manually provided key."""
        branches = self.config.sonar.branches
        try:
            metrics = await self.sonar.fetch_all_metrics(sonar_project_key, branches)
            if metrics is None:
                return RepoMetrics(
                    project=project_key,
                    repository=repo_slug,
                    sonar_project_key=sonar_project_key,
                    error=f"No analysis found on branches {branches}",
                )
            return RepoMetrics(
                project=project_key,
                repository=repo_slug,
                sonar_project_key=sonar_project_key,
                coverage=metrics.get("coverage"),
                complexity=metrics.get("complexity"),
                duplication=metrics.get("duplication"),
                high_risk_violations=metrics.get("high_risk_violations"),
                ncloc=metrics.get("ncloc"),
                last_analysis_date=metrics.get("last_analysis_date"),
            )
        except Exception as e:
            logger.error(f"Rescan failed for {repo_slug} with key {sonar_project_key}: {e}")
            return RepoMetrics(
                project=project_key,
                repository=repo_slug,
                sonar_project_key=sonar_project_key,
                error=str(e),
            )
