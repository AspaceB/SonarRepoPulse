from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum
from datetime import datetime


class ScanStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ScanRequest(BaseModel):
    """Request body for POST /api/v1/scan"""
    providers: list[str] = Field(
        ...,
        description="Providers to scan: 'bitbucket', 'github', or both",
        examples=[["bitbucket", "github"]],
    )
    bitbucket_projects: Optional[list[str]] = Field(
        None,
        description="Override: specific Bitbucket project keys to scan",
    )
    github_orgs: Optional[list[str]] = Field(
        None,
        description="Override: specific GitHub org names to scan",
    )
    repositories: Optional[list[str]] = Field(
        None,
        description="Filter: only process repos whose slug matches one of these names",
    )
    formats: list[str] = Field(
        default=["csv", "excel"],
        description="Output formats: 'csv', 'excel', or both",
    )


class RepoMetrics(BaseModel):
    """One row of the output report."""
    project: str
    repository: str
    sonar_project_key: Optional[str] = None
    coverage: Optional[float] = None
    complexity: Optional[int] = None
    duplication: Optional[float] = None
    high_risk_violations: Optional[int] = None
    ncloc: Optional[int] = None
    last_analysis_date: Optional[str] = None
    error: Optional[str] = None


class ScanResult(BaseModel):
    scan_id: str
    status: ScanStatus
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    total_repos: int = 0
    processed_repos: int = 0
    metrics: list[RepoMetrics] = []
    report_ids: list[str] = []
    error: Optional[str] = None


class ReportInfo(BaseModel):
    report_id: str
    scan_id: str
    filename: str
    format: str
    created_at: datetime
    row_count: int


# --- Discovery models (for the UI) ---

class RepoInfo(BaseModel):
    slug: str
    name: str
    project_key: str
    project_name: str


class BitbucketProjectInfo(BaseModel):
    key: str
    name: str
    repos: list[RepoInfo] = []


class GitHubOrgInfo(BaseModel):
    name: str
    repos: list[RepoInfo] = []


class RescanRequest(BaseModel):
    """Re-scan a single repo with a manually provided Sonar project key."""
    provider: str
    project_key: str
    repo_slug: str
    sonar_project_key: str
