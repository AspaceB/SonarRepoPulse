import threading
from typing import Optional
from app.models import ScanResult, ReportInfo


class ScanStore:
    """Thread-safe in-memory store for scans and reports."""

    def __init__(self):
        self._scans: dict[str, ScanResult] = {}
        self._reports: dict[str, ReportInfo] = {}
        self._lock = threading.Lock()

    def save_scan(self, scan: ScanResult) -> None:
        with self._lock:
            self._scans[scan.scan_id] = scan

    def get_scan(self, scan_id: str) -> Optional[ScanResult]:
        with self._lock:
            return self._scans.get(scan_id)

    def list_scans(self) -> list[ScanResult]:
        with self._lock:
            return list(self._scans.values())

    def save_report(self, report: ReportInfo) -> None:
        with self._lock:
            self._reports[report.report_id] = report

    def get_report(self, report_id: str) -> Optional[ReportInfo]:
        with self._lock:
            return self._reports.get(report_id)

    def list_reports(self) -> list[ReportInfo]:
        with self._lock:
            return sorted(
                self._reports.values(),
                key=lambda r: r.created_at,
                reverse=True,
            )


store = ScanStore()
