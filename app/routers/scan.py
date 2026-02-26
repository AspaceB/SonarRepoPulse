from fastapi import APIRouter, HTTPException, Request
from app.models import ScanRequest, ScanResult, RepoMetrics, RescanRequest
from app.database import store

router = APIRouter(prefix="/api/v1", tags=["scan"])

VALID_PROVIDERS = {"bitbucket", "github"}


@router.post("/scan", status_code=202)
async def trigger_scan(request: Request, scan_request: ScanRequest):
    """
    Trigger an asynchronous scan.
    Returns scan_id immediately; poll GET /api/v1/scan/{scan_id} for results.
    """
    for p in scan_request.providers:
        if p not in VALID_PROVIDERS:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown provider: '{p}'. Valid: {sorted(VALID_PROVIDERS)}",
            )

    scanner = request.app.state.scanner
    scan_id = await scanner.run_scan(scan_request)
    return {"scan_id": scan_id, "status": "pending"}


@router.get("/scan/{scan_id}", response_model=ScanResult)
async def get_scan_status(scan_id: str):
    """Get scan status and results."""
    scan = store.get_scan(scan_id)
    if not scan:
        raise HTTPException(status_code=404, detail=f"Scan '{scan_id}' not found")
    return scan


@router.post("/scan/rescan-repo", response_model=RepoMetrics)
async def rescan_single_repo(request: Request, rescan: RescanRequest):
    """Re-scan a single repo with a user-provided Sonar project key."""
    scanner = request.app.state.scanner
    result = await scanner.rescan_single_repo(
        provider=rescan.provider,
        project_key=rescan.project_key,
        repo_slug=rescan.repo_slug,
        sonar_project_key=rescan.sonar_project_key,
    )
    return result
