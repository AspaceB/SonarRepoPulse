from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pathlib import Path
from app.models import ReportInfo
from app.database import store

router = APIRouter(prefix="/api/v1", tags=["reports"])


@router.get("/reports", response_model=list[ReportInfo])
async def list_reports():
    """List all generated reports."""
    return store.list_reports()


@router.get("/reports/{report_id}/download")
async def download_report(report_id: str, request: Request):
    """Download a report as CSV or Excel."""
    report = store.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail=f"Report '{report_id}' not found")

    output_dir = request.app.state.config.reports.output_dir
    filepath = Path(output_dir) / report.filename
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="Report file not found on disk")

    media_types = {
        "csv": "text/csv",
        "excel": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    media_type = media_types.get(report.format, "application/octet-stream")

    return FileResponse(
        path=str(filepath),
        filename=report.filename,
        media_type=media_type,
    )
