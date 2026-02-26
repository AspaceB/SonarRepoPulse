import logging
from fastapi import APIRouter, HTTPException, Request
from app.models import RepoInfo, BitbucketProjectInfo, GitHubOrgInfo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/discovery", tags=["discovery"])


@router.get("/providers")
async def list_providers(request: Request):
    """Return which SCM providers are configured and available."""
    config = request.app.state.config
    providers = []
    if config.scm.bitbucket and request.app.state.bb_client:
        providers.append("bitbucket")
    if config.scm.github and request.app.state.gh_client:
        providers.append("github")
    sonar_base_url = None
    if config.sonar:
        sonar_base_url = getattr(config.sonar, "base_url", None) or getattr(config.sonar, "url", None)

    return {"providers": providers, "sonar_base_url": sonar_base_url}


@router.get("/bitbucket/projects")
async def list_bitbucket_projects(request: Request):
    """List all Bitbucket projects the user has access to (without repos).

    Merges projects from the API with config-specified projects so that
    projects the user has repo-level (but not project-browse) access to
    still appear.
    Repos are loaded on-demand via /bitbucket/projects/{key}/repos.
    """
    config = request.app.state.config
    bb_client = request.app.state.bb_client

    if not bb_client:
        raise HTTPException(status_code=404, detail="Bitbucket not configured")

    # Start with config-specified projects (always included)
    seen_keys = set()
    projects = []
    if config.scm.bitbucket and config.scm.bitbucket.projects:
        for p in config.scm.bitbucket.projects:
            projects.append(BitbucketProjectInfo(key=p.key, name=p.name))
            seen_keys.add(p.key)

    # Add API-discovered projects that aren't already in config
    try:
        raw_projects = await bb_client.list_projects()
        for p in raw_projects:
            if p["key"] not in seen_keys:
                projects.append(BitbucketProjectInfo(key=p["key"], name=p["name"]))
                seen_keys.add(p["key"])
    except Exception as e:
        logger.error(f"Failed to list Bitbucket projects from API: {e}")
        # Still return config projects even if API fails

    logger.info(f"Returning {len(projects)} Bitbucket projects ({len(seen_keys)} unique)")
    return {"projects": projects}


@router.get("/bitbucket/projects/{project_key}/repos")
async def list_bitbucket_project_repos(project_key: str, request: Request):
    """List repos for a specific Bitbucket project (loaded on-demand)."""
    bb_client = request.app.state.bb_client

    if not bb_client:
        raise HTTPException(status_code=404, detail="Bitbucket not configured")

    try:
        raw_repos = await bb_client.list_repos(project_key)
        repos = [
            RepoInfo(
                slug=r["slug"],
                name=r["name"],
                project_key=r["project_key"],
                project_name=r["project_name"],
            )
            for r in raw_repos
        ]
        return {"repos": repos}
    except Exception as e:
        logger.error(f"Failed to list repos for Bitbucket project {project_key}: {e}")
        raise HTTPException(status_code=502, detail=f"Failed to list repos: {e}")


@router.get("/github/orgs")
async def list_github_orgs(request: Request):
    """List all configured GitHub orgs with their repos."""
    config = request.app.state.config
    gh_client = request.app.state.gh_client

    if not config.scm.github or not gh_client:
        raise HTTPException(status_code=404, detail="GitHub not configured")

    orgs = []
    for org_conf in config.scm.github.organizations:
        try:
            raw_repos = await gh_client.list_repos(org_conf.name)
            repos = [
                RepoInfo(
                    slug=r["slug"],
                    name=r["name"],
                    project_key=r["project_key"],
                    project_name=r["project_name"],
                )
                for r in raw_repos
            ]
            orgs.append(GitHubOrgInfo(name=org_conf.name, repos=repos))
        except Exception as e:
            logger.error(f"Failed to list repos for GitHub org {org_conf.name}: {e}")
            orgs.append(GitHubOrgInfo(name=org_conf.name, repos=[]))

    return {"orgs": orgs}
