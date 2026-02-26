from fastapi import APIRouter
from fastapi.responses import FileResponse
from pathlib import Path

router = APIRouter(tags=["ui"])

STATIC_DIR = Path(__file__).parent.parent / "static"


@router.get("/", include_in_schema=False)
async def serve_ui():
    """Serve the single-page application."""
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")
