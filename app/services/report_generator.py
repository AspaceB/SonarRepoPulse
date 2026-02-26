import uuid
import logging
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd
from app.models import RepoMetrics, ReportInfo
from app.database import store

logger = logging.getLogger(__name__)


class ReportGenerator:
    """Generates CSV and Excel reports from scan metrics."""

    def __init__(self, output_dir: str):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate(
        self, scan_id: str, metrics: list[RepoMetrics], formats: list[str]
    ) -> list[str]:
        """Generate reports in requested formats. Returns list of report IDs."""
        report_ids = []
        df = self._to_dataframe(metrics)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

        if "csv" in formats:
            rid = str(uuid.uuid4())
            filename = f"report_{scan_id[:8]}_{timestamp}.csv"
            filepath = self.output_dir / filename
            df.to_csv(filepath, index=False)
            store.save_report(ReportInfo(
                report_id=rid,
                scan_id=scan_id,
                filename=filename,
                format="csv",
                created_at=datetime.now(timezone.utc),
                row_count=len(df),
            ))
            report_ids.append(rid)
            logger.info(f"CSV report generated: {filename} ({len(df)} rows)")

        if "excel" in formats:
            rid = str(uuid.uuid4())
            filename = f"report_{scan_id[:8]}_{timestamp}.xlsx"
            filepath = self.output_dir / filename
            df.to_excel(filepath, index=False, engine="openpyxl")
            store.save_report(ReportInfo(
                report_id=rid,
                scan_id=scan_id,
                filename=filename,
                format="excel",
                created_at=datetime.now(timezone.utc),
                row_count=len(df),
            ))
            report_ids.append(rid)
            logger.info(f"Excel report generated: {filename} ({len(df)} rows)")

        return report_ids

    @staticmethod
    def _to_dataframe(metrics: list[RepoMetrics]) -> pd.DataFrame:
        rows = []
        for m in metrics:
            rows.append({
                "Project": m.project,
                "Repository": m.repository,
                "Coverage (%)": m.coverage,
                "Complexity": m.complexity,
                "Duplication (%)": m.duplication,
                "High Risk Violations": m.high_risk_violations,
                "NCLOC": m.ncloc,
                "Sonar Project Key": m.sonar_project_key or "",
                "Notes": m.error or "",
            })
        return pd.DataFrame(rows)
