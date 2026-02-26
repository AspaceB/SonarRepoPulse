import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings
from typing import Optional
from pathlib import Path


class BitbucketProject(BaseModel):
    key: str
    name: str


class BitbucketConfig(BaseModel):
    base_url: str
    projects: list[BitbucketProject]


class GitHubOrg(BaseModel):
    name: str


class GitHubConfig(BaseModel):
    organizations: list[GitHubOrg]


class SCMConfig(BaseModel):
    bitbucket: Optional[BitbucketConfig] = None
    github: Optional[GitHubConfig] = None


class SonarConfig(BaseModel):
    base_url: str
    organization: Optional[str] = None  # Required for SonarCloud, not needed for self-hosted SonarQube
    branches: list[str] = ["main", "master"]


class SchedulerJob(BaseModel):
    name: str
    cron: str
    providers: list[str]


class SchedulerConfig(BaseModel):
    enabled: bool = False
    jobs: list[SchedulerJob] = []


class ReportsConfig(BaseModel):
    output_dir: str = "./reports"
    formats: list[str] = ["csv", "excel"]


class AppConfig(BaseModel):
    scm: SCMConfig
    sonar: SonarConfig
    reports: ReportsConfig = ReportsConfig()
    scheduler: SchedulerConfig = SchedulerConfig()


class Settings(BaseSettings):
    bitbucket_token: str = ""
    github_token: str = ""
    sonar_token: str = ""
    # Set to "browser" to authenticate via SSO using Playwright
    bitbucket_auth_method: str = "token"
    sonar_auth_method: str = "token"
    config_path: str = "./config.yaml"
    sonar_mappings_path: str = "./sonar_key_mappings.yaml"

    # Corporate proxy support
    https_proxy: str = ""
    http_proxy: str = ""
    no_proxy: str = ""
    # Set to false to skip SSL verification (e.g. proxy with custom CA)
    ssl_verify: bool = True

    class Config:
        env_file = ".env"


def load_app_config(path: str) -> AppConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(config_path) as f:
        data = yaml.safe_load(f)
    return AppConfig(**data)


def load_sonar_mappings(path: str) -> dict[str, str]:
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
        if data is None:
            return {}
        return data.get("mappings", {}) or {}
    except FileNotFoundError:
        return {}
