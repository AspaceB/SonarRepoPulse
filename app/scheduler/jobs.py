import logging
from app.models import ScanRequest

logger = logging.getLogger(__name__)

# Will be set by setup.py during app startup
_scanner = None


def set_scanner(scanner):
    global _scanner
    _scanner = scanner


async def scheduled_scan(providers: list[str]):
    """Invoked by APScheduler. Creates a ScanRequest and runs the scanner."""
    if _scanner is None:
        logger.error("Scheduled scan failed: scanner not initialized")
        return

    request = ScanRequest(providers=providers)
    scan_id = await _scanner.run_scan(request)
    logger.info(f"Scheduled scan started: scan_id={scan_id}, providers={providers}")
