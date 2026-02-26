# SonarRepoPulse

API-driven utility with a **built-in web UI** that pulls repositories from **Bitbucket Server/DC** and **GitHub**, resolves each repo's **SonarQube** project key using smart multi-strategy matching, fetches code quality metrics from the main/master branch, and outputs results as **CSV/Excel** reports. Supports interactive scanning via the web dashboard, on-demand execution via REST endpoints, and automated scheduled runs.

---

## Table of Contents

- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Web UI](#web-ui)
- [Prerequisites](#prerequisites)
- [Token Generation Guide](#token-generation-guide)
  - [Bitbucket Server/DC — HTTP Access Token](#bitbucket-serverdc--http-access-token)
  - [GitHub — Personal Access Token](#github--personal-access-token-classic)
  - [SonarQube — User Token](#sonarqube--user-token)
- [Setup](#setup)
- [Configuration](#configuration)
  - [Environment Variables](#environment-variables)
  - [config.yaml](#configyaml)
  - [SonarQube Key Mappings](#sonar-key-mappings)
- [Docker](#docker)
- [Running the Server](#running-the-server)
- [API Reference](#api-reference)
  - [Health Check](#health-check)
  - [Trigger Scan](#trigger-scan)
  - [Get Scan Status](#get-scan-status)
  - [Rescan Single Repo](#rescan-single-repo)
  - [Discovery — Providers](#discovery--providers)
  - [Discovery — Bitbucket Projects](#discovery--bitbucket-projects)
  - [Discovery — Bitbucket Project Repos](#discovery--bitbucket-project-repos)
  - [Discovery — GitHub Orgs](#discovery--github-orgs)
  - [List Reports](#list-reports)
  - [Download Report](#download-report)
- [How It Works](#how-it-works)
  - [Scan Flow](#scan-flow)
  - [SonarQube Key Resolution](#sonar-key-resolution)
  - [Metrics Collected](#metrics-collected)
  - [Report Output](#report-output)
- [Scheduling](#scheduling)
- [Corporate Proxy & SSO](#corporate-proxy--sso)
  - [Playwright SSO Browser Authentication](#playwright-sso-browser-authentication)
- [Error Handling](#error-handling)
- [Project Structure](#project-structure)

---

## Architecture

```
                         +------------------+
                         |   FastAPI Server  |
                         |   (main.py)       |
                         +--------+---------+
                                  |
       +------------+-------------+-------------+-------------+
       |            |             |             |             |
+------v-----+ +---v--------+ +-v----------+ +v----------+ +v-----------+
| UI Router  | | Discovery  | | Scan Router| | Reports   | | Health     |
| GET /      | | Router     | | POST /scan | | Router    | | Router     |
| (SPA)      | | /discovery | | GET /scan/ | | GET /rpts | | GET /health|
+------+-----+ +---+--------+ +-----+------+ +-----+-----+ +-----------+
       |            |                |              |
       |            v                v              |
       |    +-------+-------+  +----+-------+      |
       |    | SCM Clients   |  |  Scanner   |      |
       |    | (Bitbucket,   |  | (Orchestr) |      |
       |    |  GitHub)       |  +--+---+----+      |
       |    +---------------+     |   |            |
       |                          v   v            |
       |            +-----------+ +---+--------+   |
       |            |SonarProject| |Sonar      |   |
       |            |Index       | |Client     |   |
       |            |(6-strategy | |(token/SSO)|   |
       |            | matching)  | +-----------+   |
       |            +------------+                 |
       |                                  +--------v---------+
       |                                  | Report Generator  |
       +-- Serves index.html (SPA) ----→ | (CSV / Excel)     |
                                          +------------------+
```

---

## Tech Stack

### Backend

| Technology | Version | Purpose |
|------------|---------|---------|
| **Python** | 3.11+ | Runtime |
| **FastAPI** | 0.115+ | Async web framework, REST API, static file serving |
| **Uvicorn** | 0.30+ | ASGI server |
| **Pydantic** | 2.0+ | Data validation, settings management, request/response models |
| **httpx** | 0.27+ | Async HTTP client for Bitbucket, GitHub, and SonarQube APIs |
| **Playwright** | 1.40+ | Headless browser automation for SSO authentication |
| **pandas** | 2.2+ | DataFrame processing for CSV/Excel report generation |
| **openpyxl** | 3.1+ | Excel (.xlsx) file writing |
| **APScheduler** | 3.10+ | Cron-based scheduled scan jobs |
| **PyYAML** | 6.0+ | YAML config file parsing |
| **python-dotenv** | 1.0+ | `.env` file loading for credentials |

### Frontend

| Technology | Purpose |
|------------|---------|
| **Bootstrap 5.3** (CDN) | UI layout, components, responsive grid, tables, progress bars |
| **Bootstrap Icons** (CDN) | Icon set for UI elements |
| **Vanilla JavaScript (ES6+)** | Application logic — no framework, no build step |
| **HTML5 / CSS3** | Single-file SPA served by FastAPI |

### Infrastructure

| Technology | Purpose |
|------------|---------|
| **Docker** | Container image for deployment |
| **Docker Compose** | Multi-service orchestration |

---

## Web UI

SonarRepoPulse includes a built-in web dashboard accessible at `http://localhost:8000/` — no separate frontend build step required. The UI is a single-page application (SPA) served directly by FastAPI using Bootstrap 5 and vanilla JavaScript.

### Features

- **Provider Selection** — Toggle between Bitbucket and GitHub providers based on your configuration
- **Repository Browser** — Collapsible tree view for Bitbucket projects (lazy-loaded repos per project) and grouped list for GitHub orgs
- **Search & Multi-Select** — Filter repos by name, select/deselect individually or in bulk
- **Real-time Scan Progress** — Live progress bar with repo count updates during scanning
- **Results Table** — Color-coded metrics (coverage, complexity, duplication, violations) with direct links to SonarQube dashboards
- **Report Downloads** — Download CSV/Excel reports directly from the results view
- **Inline Re-scan** — For repos where automatic Sonar key matching fails, manually enter the SonarQube project key and re-scan without restarting the full scan
- **Last Analysis Date** — Shows when each repo was last analyzed in SonarQube

### Accessing the UI

Start the server and navigate to:

```
http://localhost:8000/
```

The Swagger API docs remain available at `http://localhost:8000/docs`.

---

## Prerequisites

- **Python 3.11+**
- API credentials for the services you want to scan (see [Token Generation Guide](#token-generation-guide) below)

---

## Token Generation Guide

### Bitbucket Server/DC — HTTP Access Token

1. Log in to your Bitbucket Server instance
2. Click your **profile avatar** (top-right corner)
3. Click **"Manage account"**
4. In the left sidebar, click **"HTTP access tokens"**
5. Click **"Create token"**
6. Fill in:
   - **Token name**: `SonarRepoPulse`
   - **Project permissions**: **Project read**
   - **Repository permissions**: **Repository read** (inherited)
7. Click **"Save"**
8. **Copy the token immediately** — it starts with `BBDC-` and won't be shown again

> **SSO Note**: If your Bitbucket Server uses OpenID Connect SSO, the SSO plugin may block REST API calls even with a valid token. In this case, ask your Bitbucket admin to **whitelist `/rest/api/**` paths from SSO redirection** in the OpenID Connect plugin settings.

### GitHub — Personal Access Token (Classic)

1. Go to [github.com/settings/tokens](https://github.com/settings/tokens)
2. Click **"Generate new token"** > **"Generate new token (classic)"**
3. Fill in:
   - **Note**: `SonarRepoPulse`
   - **Expiration**: choose an appropriate duration (e.g. 90 days)
   - **Scopes**: check **`repo`** (Full control of private repositories — includes read access)
4. Click **"Generate token"**
5. **Copy the token immediately** — it starts with `ghp_` and won't be shown again

**If your GitHub organization uses SSO** (required extra step):

1. Go back to [github.com/settings/tokens](https://github.com/settings/tokens)
2. Find your newly created token
3. Click **"Configure SSO"** next to the token
4. Click **"Authorize"** next to your organization name
5. Complete the SSO authentication prompt if shown

> Without this SSO authorization step, API calls to your org's repositories will return `403 Forbidden`.

### SonarQube — User Token

1. Log in to your SonarQube instance
2. Click your **profile avatar** (top-right corner)
3. Click **"My Account"**
4. Go to the **"Security"** tab
5. Under **"Generate Tokens"**:
   - **Name**: `SonarRepoPulse`
   - **Type**: **User Token**
   - **Expires in**: choose an appropriate duration
6. Click **"Generate"**
7. **Copy the token immediately** — it starts with `squ_` and won't be shown again

> **SSO Note**: If your SonarQube instance uses OpenID Connect SSO, the SSO layer may block REST API calls even with a valid token. In this case, ask your SonarQube admin to **whitelist `/api/**` paths from SSO redirection**.

### Token Summary

| Service | Env Variable | Token Format | Permissions Needed |
|---------|-------------|-------------|-------------------|
| Bitbucket Server/DC | `BITBUCKET_TOKEN` | `BBDC-...` | Project Read, Repository Read |
| GitHub | `GITHUB_TOKEN` | `ghp_...` | `repo` scope + SSO authorize per org |
| SonarQube | `SONAR_TOKEN` | `squ_...` | User Token (read access) |

You only need tokens for the providers you plan to scan. For example, if you only scan GitHub repos, `BITBUCKET_TOKEN` can be left empty.

---

## Setup

```bash
# Clone the repository
git clone https://github.com/AspaceB/SonarRepoPulse.git
cd SonarRepoPulse

# Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate        # macOS/Linux
# venv\Scripts\activate         # Windows

# Install dependencies
pip install -r requirements.txt

# Install Playwright browser (only needed if using SSO browser auth)
playwright install chromium

# Create your .env file from the template
cp .env.example .env
```

Edit `.env` with your credentials (see below).

---

## Configuration

### Environment Variables

Create a `.env` file in the project root (never commit this file):

```env
# Bitbucket Server/DC (HTTP Access Token)
BITBUCKET_TOKEN=your-http-access-token

# GitHub
GITHUB_TOKEN=ghp_your-personal-access-token

# SonarQube (User Token)
SONAR_TOKEN=squ_your-sonar-token

# Auth method: "token" (default) or "browser" (SSO via Playwright)
# Use "browser" when services are behind OpenID Connect SSO that blocks API tokens
# BITBUCKET_AUTH_METHOD=browser
# SONAR_AUTH_METHOD=browser

# Optional: custom paths for config files
CONFIG_PATH=./config.yaml
SONAR_MAPPINGS_PATH=./sonar_key_mappings.yaml
```

You only need credentials for the providers you plan to scan. For example, if you only scan GitHub repos, `BITBUCKET_TOKEN` can be left empty.

> **SSO Users**: If Bitbucket or SonarQube is behind OpenID Connect SSO that blocks API token access, set `BITBUCKET_AUTH_METHOD=browser` and/or `SONAR_AUTH_METHOD=browser`. See [Playwright SSO Browser Authentication](#playwright-sso-browser-authentication) for details.

### config.yaml

This file defines which projects/orgs to scan, SonarQube settings, report output, and schedule:

```yaml
scm:
  bitbucket:
    base_url: "https://bitbucket.your-company.com"     # Your Bitbucket Server URL
    projects:
      - key: "PROJ1"                   # Bitbucket project key
        name: "Project One"            # Display name for reports
      - key: "PROJ2"
        name: "Project Two"

  github:
    organizations:
      - name: "my-github-org"          # GitHub organization name
      - name: "another-org"

sonar:
  base_url: "https://sonar.your-company.com"  # Your SonarQube Server URL
  # organization: "my-org"                    # Only needed for SonarCloud, omit for self-hosted
  branches:                            # Branches to check (in priority order)
    - "main"
    - "master"

reports:
  output_dir: "./reports"              # Where generated files are saved
  formats:
    - "csv"
    - "excel"

scheduler:
  enabled: false                       # Set to true to enable cron jobs
  jobs:
    - name: "full_scan"
      cron: "0 2 * * 1"               # Every Monday at 2:00 AM
      providers: ["bitbucket", "github"]
```

### SonarQube Key Mappings

SonarQube project keys don't always match repository names. The file `sonar_key_mappings.yaml` provides a manual fallback mapping:

```yaml
mappings:
  # Format: "project_or_org/repo-slug": "sonar-project-key"
  "my-workspace/legacy-app": "my-sonar-org_legacy-app-v2"
  "my-github-org/old-service": "custom-sonar-key-123"
```

This file is only consulted when the automatic SonarQube API search fails to find a match. See [SonarQube Key Resolution](#sonar-key-resolution) for details.

---

## Docker

### Using Docker Compose (recommended)

```bash
# 1. Create your .env file
cp .env.example .env
# Edit .env with your credentials

# 2. Edit config.yaml with your workspace/org/project values

# 3. Build and start
docker compose up -d

# View logs
docker compose logs -f

# Stop
docker compose down
```

### Using Docker directly

```bash
# Build the image
docker build -t sonarrepopulse .

# Run the container
docker run -d \
  --name sonarrepopulse \
  -p 8000:8000 \
  --env-file .env \
  -v $(pwd)/config.yaml:/app/config.yaml:ro \
  -v $(pwd)/sonar_key_mappings.yaml:/app/sonar_key_mappings.yaml:ro \
  -v $(pwd)/reports:/app/reports \
  sonarrepopulse
```

Config files (`config.yaml`, `sonar_key_mappings.yaml`) are mounted as read-only volumes so you can edit them without rebuilding. The `reports/` directory is mounted as a volume so generated files persist on your host.

---

## Running the Server

```bash
# Activate virtual environment
source venv/bin/activate

# Start the server
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

The web UI is available at `http://localhost:8000/`. API endpoints are under `http://localhost:8000/api/v1/`. Interactive Swagger docs are at `http://localhost:8000/docs`.

---

## API Reference

### Health Check

```
GET /api/v1/health
```

**Response** `200`:
```json
{
  "status": "healthy",
  "timestamp": "2026-02-20T05:46:06.161181+00:00",
  "version": "1.0.0"
}
```

---

### Trigger Scan

```
POST /api/v1/scan
Content-Type: application/json
```

**Request body**:
```json
{
  "providers": ["bitbucket", "github"],
  "bitbucket_projects": ["PROJ1"],
  "github_orgs": ["my-org"],
  "repositories": ["my-service", "my-api"],
  "formats": ["csv", "excel"]
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `providers` | `string[]` | Yes | Which SCM providers to scan: `"bitbucket"`, `"github"`, or both |
| `bitbucket_projects` | `string[]` | No | Override: specific Bitbucket project keys. If omitted, uses `config.yaml` values |
| `github_orgs` | `string[]` | No | Override: specific GitHub org names. If omitted, uses `config.yaml` values |
| `repositories` | `string[]` | No | Filter: only process repos whose name matches one of these. If omitted, processes all repos |
| `formats` | `string[]` | No | Output formats: `"csv"`, `"excel"`, or both. Default: `["csv", "excel"]` |

**Response** `202 Accepted`:
```json
{
  "scan_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "pending"
}
```

The scan runs asynchronously. Use the `scan_id` to poll for results.

**Examples**:

```bash
# Scan all configured Bitbucket projects
curl -X POST http://localhost:8000/api/v1/scan \
  -H "Content-Type: application/json" \
  -d '{"providers": ["bitbucket"]}'

# Scan specific GitHub orgs with CSV output only
curl -X POST http://localhost:8000/api/v1/scan \
  -H "Content-Type: application/json" \
  -d '{"providers": ["github"], "github_orgs": ["my-org"], "formats": ["csv"]}'

# Scan both providers
curl -X POST http://localhost:8000/api/v1/scan \
  -H "Content-Type: application/json" \
  -d '{"providers": ["bitbucket", "github"]}'

# Scan a single specific repository
curl -X POST http://localhost:8000/api/v1/scan \
  -H "Content-Type: application/json" \
  -d '{"providers": ["github"], "github_orgs": ["my-org"], "repositories": ["my-service"]}'
```

---

### Get Scan Status

```
GET /api/v1/scan/{scan_id}
```

**Response** `200`:
```json
{
  "scan_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "completed",
  "started_at": "2026-02-20T06:00:00.000000+00:00",
  "completed_at": "2026-02-20T06:01:30.000000+00:00",
  "total_repos": 25,
  "processed_repos": 25,
  "metrics": [
    {
      "project": "Project One",
      "repository": "my-service",
      "sonar_project_key": "my-org_my-service",
      "coverage": 82.5,
      "complexity": 1234,
      "duplication": 3.2,
      "high_risk_violations": 5,
      "ncloc": 45000,
      "last_analysis_date": "2026-02-15T10:30:00+0000",
      "error": null
    }
  ],
  "report_ids": [
    "r1b2c3d4-csv-uuid",
    "r1b2c3d4-xlsx-uuid"
  ],
  "error": null
}
```

| Status | Meaning |
|--------|---------|
| `pending` | Scan created, not yet started |
| `running` | Currently fetching repos and metrics |
| `completed` | All repos processed, reports generated |
| `failed` | Scan failed (see `error` field) |

---

### Rescan Single Repo

```
POST /api/v1/scan/rescan-repo
Content-Type: application/json
```

Re-scan a single repository using a manually provided SonarQube project key. Useful when automatic key resolution fails and you know the correct key.

**Request body**:
```json
{
  "provider": "bitbucket",
  "project_key": "PROJ1",
  "repo_slug": "my-service",
  "sonar_project_key": "com.example_my-service"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `provider` | `string` | Yes | SCM provider: `"bitbucket"` or `"github"` |
| `project_key` | `string` | Yes | Bitbucket project key or GitHub org name |
| `repo_slug` | `string` | Yes | Repository slug/name |
| `sonar_project_key` | `string` | Yes | The SonarQube project key to use |

**Response** `200`:
```json
{
  "project": "PROJ1",
  "repository": "my-service",
  "sonar_project_key": "com.example_my-service",
  "coverage": 82.5,
  "complexity": 1234,
  "duplication": 3.2,
  "high_risk_violations": 5,
  "ncloc": 45000,
  "last_analysis_date": "2026-02-15T10:30:00+0000",
  "error": null
}
```

---

### Discovery — Providers

```
GET /api/v1/discovery/providers
```

Returns which SCM providers are configured and available, along with the SonarQube base URL.

**Response** `200`:
```json
{
  "providers": ["bitbucket", "github"],
  "sonar_base_url": "https://sonar.your-company.com"
}
```

---

### Discovery — Bitbucket Projects

```
GET /api/v1/discovery/bitbucket/projects
```

Lists all Bitbucket projects the user has access to. Merges projects from the Bitbucket API with config-specified projects so that projects with repo-level (but not project-browse) access still appear.

**Response** `200`:
```json
{
  "projects": [
    {"key": "PROJ1", "name": "Project One"},
    {"key": "PROJ2", "name": "Project Two"}
  ]
}
```

---

### Discovery — Bitbucket Project Repos

```
GET /api/v1/discovery/bitbucket/projects/{project_key}/repos
```

Lists repositories for a specific Bitbucket project (loaded on-demand when expanding a project in the UI).

**Response** `200`:
```json
{
  "repos": [
    {"slug": "my-service", "name": "my-service", "project_key": "PROJ1", "project_name": "Project One"},
    {"slug": "my-api", "name": "my-api", "project_key": "PROJ1", "project_name": "Project One"}
  ]
}
```

---

### Discovery — GitHub Orgs

```
GET /api/v1/discovery/github/orgs
```

Lists all configured GitHub organizations with their repositories.

**Response** `200`:
```json
{
  "orgs": [
    {
      "name": "my-github-org",
      "repos": [
        {"slug": "api-service", "name": "api-service", "project_key": "my-github-org", "project_name": "my-github-org"}
      ]
    }
  ]
}
```

---

### List Reports

```
GET /api/v1/reports
```

**Response** `200`:
```json
[
  {
    "report_id": "r1b2c3d4-csv-uuid",
    "scan_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "filename": "report_a1b2c3d4_20260220_060130.csv",
    "format": "csv",
    "created_at": "2026-02-20T06:01:30.000000+00:00",
    "row_count": 25
  }
]
```

---

### Download Report

```
GET /api/v1/reports/{report_id}/download
```

Returns the file directly as a download. Content type is `text/csv` for CSV files or `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` for Excel files.

```bash
# Download as CSV
curl -o report.csv http://localhost:8000/api/v1/reports/{report_id}/download

# Download as Excel
curl -o report.xlsx http://localhost:8000/api/v1/reports/{report_id}/download
```

---

## How It Works

### Scan Flow

When a scan is triggered (via the web UI, API call, or scheduler), the following steps execute:

1. **Collect Repositories** — The scanner queries the requested SCM providers:
   - **Bitbucket Server/DC**: `GET /rest/api/1.0/projects/{projectKey}/repos` with pagination via `start`/`isLastPage`/`nextPageStart`
   - **GitHub**: `GET /orgs/{org}/repos?per_page=100` with pagination via the `Link` header

2. **Build SonarQube Project Index** — All SonarQube projects are fetched once and indexed in memory. This avoids per-repo API calls during key resolution.

3. **Resolve SonarQube Project Keys** — For each repository, the scanner determines its SonarQube project key using the 6-strategy index matching, YAML mapping fallback, and per-repo API search as a last resort (see [Key Resolution](#sonar-key-resolution) below)

4. **Fetch Metrics** — For each resolved project key, the scanner queries SonarQube for measures, issue counts, and last analysis date on the configured branches (tries `main` first, then `master`)

5. **Generate Reports** — All collected metrics are compiled into a pandas DataFrame and written as CSV and/or Excel files

6. **Store Results** — Scan status, metrics, and report metadata are stored in memory and accessible via the API

Steps 3-4 run concurrently (up to 5 repos at a time) to optimize throughput while respecting SonarQube rate limits. Progress is updated in real-time as each repo completes, visible via the API and the web UI progress bar.

### SonarQube Key Resolution

SonarQube project keys often differ from repository names. The resolution uses a multi-strategy approach with a pre-built index for fast matching:

```
Step 1: SonarQube Project Index (automatic, fast)
    On scan start, all SonarQube projects are fetched once and indexed.
    Each repo is matched against the index using 6 strategies (in order):

    1. exact_key    — Repo slug matches Sonar project key exactly
    2. exact_name   — Repo slug matches Sonar project name exactly
    3. key_suffix   — Sonar key ends with the repo slug (e.g. "org_my-service" matches "my-service")
    4. normalized   — After removing separators (-_.), slugs match (e.g. "my-service" ↔ "myservice")
    5. collapsed    — After collapsing common prefixes/org names, keys match
    6. token_overlap — Significant word overlap between repo slug and Sonar key tokens

    Each match includes a confidence score. Higher-confidence matches are preferred.

Step 2: Manual YAML Mapping (fallback)
    If Step 1 finds no match, looks up sonar_key_mappings.yaml
    using the key "{project_or_org}/{repo_slug}"
    → Example: "my-workspace/legacy-app" → "my-org_legacy-app-v2"

Step 3: Per-repo API Search (last resort)
    GET /api/components/search?qualifiers=TRK&q={repo_name}  (SonarQube)
    GET /api/projects/search?organization={org}&q={repo_name} (SonarCloud)
    → Matches by key suffix, name, or substring

If no step resolves a key:
    → The repo is included in the report with a "Notes" column
      explaining that no SonarQube project was found
    → In the Web UI, an "Enter Sonar Key" button allows manual resolution
```

### Metrics Collected

| Column | SonarQube Metric | API Source | Description |
|--------|-------------------|------------|-------------|
| **Coverage (%)** | `coverage` | `/api/measures/component` | Line coverage percentage |
| **Complexity** | `complexity` | `/api/measures/component` | Total cyclomatic complexity |
| **Duplication (%)** | `duplicated_lines_density` | `/api/measures/component` | Percentage of duplicated lines |
| **High Risk Violations** | CRITICAL + BLOCKER | `/api/issues/search` | Count of unresolved CRITICAL and BLOCKER severity issues |
| **NCLOC** | `ncloc` | `/api/measures/component` | Non-comment lines of code |
| **Last Analysis Date** | — | `/api/project_analyses/search` | Date of the most recent SonarQube analysis |

### Report Output

Generated files are saved to the `reports/` directory (configurable) with filenames like:

```
report_a1b2c3d4_20260220_060130.csv
report_a1b2c3d4_20260220_060130.xlsx
```

CSV/Excel columns:

| Project | Repository | Coverage (%) | Complexity | Duplication (%) | High Risk Violations | NCLOC | Last Analysis Date | Sonar Project Key | Notes |
|---------|------------|-------------|------------|----------------|---------------------|-------|--------------------|-------------------|-------|
| Project One | my-service | 82.5 | 1234 | 3.2 | 5 | 45000 | 2026-02-15 | my-org_my-service | |
| Project One | old-app | | | | | | | | No SonarQube project found |

---

## Scheduling

Automated scans can be configured in `config.yaml`:

```yaml
scheduler:
  enabled: true
  jobs:
    - name: "full_scan"
      cron: "0 2 * * 1"               # Every Monday at 2 AM
      providers: ["bitbucket", "github"]
    - name: "daily_bitbucket"
      cron: "0 6 * * *"               # Daily at 6 AM
      providers: ["bitbucket"]
```

The cron format follows the standard 5-field syntax: `minute hour day month day_of_week`.

| Field | Values |
|-------|--------|
| Minute | 0-59 |
| Hour | 0-23 |
| Day of month | 1-31 |
| Month | 1-12 |
| Day of week | 0-6 (0 = Monday) |

Scheduled scans use the default projects/orgs from `config.yaml` and output in all configured formats. Scan results are accessible via the same API endpoints.

---

## Corporate Proxy & SSO

### Proxy Setup

If your network routes traffic through a corporate proxy (e.g. Zscaler, Netskope, BlueCoat), add the following to your `.env` file:

```env
HTTPS_PROXY=http://proxy.corp.example.com:8080
HTTP_PROXY=http://proxy.corp.example.com:8080
NO_PROXY=localhost,127.0.0.1
```

If the proxy intercepts HTTPS traffic with its own CA certificate (common with Zscaler/Netskope), you may need to disable SSL verification:

```env
SSL_VERIFY=false
```

> **Note:** Disabling SSL verification reduces security. Only use this when connecting through a trusted corporate proxy that performs TLS inspection.

All three API clients (Bitbucket, GitHub, SonarQube) automatically pick up these proxy settings. No code changes are needed.

**Docker with proxy:**

When running in Docker, pass proxy variables in your `.env` file (loaded automatically by `docker-compose.yml`), or pass them directly:

```bash
docker run -d \
  --name sonarrepopulse \
  -p 8000:8000 \
  -e HTTPS_PROXY=http://proxy.corp.example.com:8080 \
  -e SSL_VERIFY=false \
  --env-file .env \
  -v $(pwd)/config.yaml:/app/config.yaml:ro \
  -v $(pwd)/sonar_key_mappings.yaml:/app/sonar_key_mappings.yaml:ro \
  -v $(pwd)/reports:/app/reports \
  sonarrepopulse
```

### SSO Authentication

All three services support corporate SSO. The API tokens used by this utility work alongside SSO without any additional configuration in most environments:

| Service | Auth Method | SSO Notes |
|---------|-------------|-----------|
| **Bitbucket Server/DC** | HTTP Access Token | HTTP access tokens bypass SSO for API access. Generate one from your Bitbucket profile > **HTTP access tokens**. Your Bitbucket admin must allow token creation. |
| **GitHub** | Personal Access Token | PATs work with SSO, but must be **SSO-authorized** per organization. Go to [GitHub Settings > Tokens](https://github.com/settings/tokens), click **"Configure SSO"** next to your token, and **"Authorize"** each SSO-protected org. Without this step, API calls return `403`. |
| **SonarQube** | User Token | Tokens are generated after SSO login and work independently. Generate yours from your SonarQube instance > **My Account > Security**. |

> **Important**: If your Bitbucket Server or SonarQube instance uses an **OpenID Connect (OIDC) SSO plugin** that intercepts ALL requests (including REST API calls), standard API tokens will fail with a `302` redirect to the login page. In this case, use the **Playwright SSO Browser Authentication** method described below.

### Playwright SSO Browser Authentication

When Bitbucket Server or SonarQube is behind an OpenID Connect SSO plugin (e.g., Azure AD, Okta, Keycloak) that blocks REST API token access, this utility can authenticate through the actual SSO login flow using a real browser powered by [Playwright](https://playwright.dev/python/).

**How it works:**

1. On first run, a Chromium browser window opens automatically
2. The browser navigates to Bitbucket/SonarQube, which redirects to your corporate Identity Provider (Azure AD, Okta, etc.)
3. You log in with your corporate credentials and complete MFA if required
4. Once authenticated, the session cookies are saved to disk (`.bitbucket_session.json` / `.sonar_session.json`)
5. The browser window closes and switches to headless mode
6. All subsequent API calls are made via `fetch()` inside the authenticated browser context
7. On future runs, the saved session is reused automatically — no login needed until the session expires

**Setup:**

```bash
# 1. Install Playwright (included in requirements.txt)
pip install playwright

# 2. Install Chromium browser binary
playwright install chromium
```

**Enable browser auth in `.env`:**

```env
# For Bitbucket Server behind SSO:
BITBUCKET_AUTH_METHOD=browser

# For SonarQube behind SSO:
SONAR_AUTH_METHOD=browser

# You can enable both independently:
BITBUCKET_AUTH_METHOD=browser
SONAR_AUTH_METHOD=browser
```

> **Note**: `BITBUCKET_TOKEN` and `SONAR_TOKEN` are not needed when using browser auth mode. GitHub uses standard PAT authentication (GitHub SSO works fine with tokens after SSO authorization).

**First run — interactive login:**

```bash
# Start the server
uvicorn main:app --host 0.0.0.0 --port 8000
```

On startup, a browser window will open for each service configured with `browser` auth:

```
============================================================
  BITBUCKET SSO LOGIN
============================================================
  A browser window will open.
  Please log in through your corporate SSO.
  The window will close automatically once login succeeds.
============================================================
```

1. The browser opens your Bitbucket/SonarQube URL
2. SSO redirects you to your Identity Provider (Azure AD, Okta, etc.)
3. Enter your corporate credentials and complete MFA
4. Once you land back on the Bitbucket/SonarQube dashboard, the browser detects the successful login
5. Session is saved and the browser closes automatically
6. Server startup continues normally

**Subsequent runs — automatic session reuse:**

```
Restoring saved Bitbucket browser session ...
Bitbucket browser session restored successfully
Restoring saved SonarQube browser session ...
SonarQube browser session restored successfully
```

The saved sessions are reused until they expire. When a session expires, the browser window will open again for re-authentication.

**Session files:**

| File | Purpose |
|------|---------|
| `.bitbucket_session.json` | Saved Bitbucket SSO session (gitignored) |
| `.sonar_session.json` | Saved SonarQube SSO session (gitignored) |

These files contain session cookies and are automatically added to `.gitignore`. Delete them to force a fresh login.

**Auth method comparison:**

| | Token Auth (`token`) | Browser Auth (`browser`) |
|---|---|---|
| **Setup** | Generate API token from web UI | Install Playwright + Chromium |
| **First run** | Immediate, no interaction | Browser opens for SSO login |
| **Subsequent runs** | Immediate | Automatic (session reused) |
| **SSO compatibility** | Works if SSO doesn't block API paths | Works with any SSO provider |
| **Admin access needed** | No | No |
| **Dependencies** | httpx only | Playwright + Chromium (~250 MB) |
| **Headless/CI** | Yes | Requires initial interactive login, then headless |
| **Docker** | Yes | Requires display for initial login (use token auth in Docker) |

**Troubleshooting:**

| Issue | Solution |
|-------|----------|
| Browser doesn't open | Ensure `playwright install chromium` was run |
| Login page keeps refreshing | Close any other Playwright sessions and retry |
| Session expires quickly | Delete the `.json` session file and re-login |
| `TimeoutError: SSO login timed out` | You have 5 minutes to complete login — ensure MFA is ready |
| Headless mode fails after login | Delete the session file and re-authenticate |

### Proxy + SSO Checklist

1. Get your corporate proxy URL (ask your network/IT team)
2. Add `HTTPS_PROXY` to `.env`
3. If you get SSL errors, set `SSL_VERIFY=false`
4. Generate API tokens for each service (see table above)
5. For GitHub with SSO: authorize the PAT for each org
6. If tokens are blocked by OpenID Connect SSO: set `BITBUCKET_AUTH_METHOD=browser` and/or `SONAR_AUTH_METHOD=browser`
7. Test connectivity: `curl -x http://your-proxy:8080 https://api.github.com`

---

## Error Handling

The utility handles errors gracefully at the per-repository level so that one failure doesn't stop the entire scan:

| Scenario | Behavior |
|----------|----------|
| SCM auth failure (401/403) | Scan fails immediately with a clear error message |
| SonarQube project not found | Repo included in report with explanation in Notes column |
| No analysis on any branch | Repo included with "No analysis found" in Notes |
| SonarQube rate limit (429) | Automatic retry with exponential backoff (1s, 2s, 4s) up to 3 attempts |
| Network timeout | 30-second timeout per request; error recorded for that repo, scan continues |
| Empty project (no repos) | Empty result, no error |

---

## Project Structure

```
SonarRepoPulse/
├── main.py                              # FastAPI app entrypoint, dependency wiring
├── requirements.txt                     # Python dependencies
├── config.yaml                          # SCM projects, SonarQube config, schedule
├── sonar_key_mappings.yaml              # Fallback repo → Sonar project key mappings
├── .env.example                         # Credentials template
├── .gitignore
├── Dockerfile                           # Container image definition
├── docker-compose.yml                   # Docker Compose config
├── app/
│   ├── config.py                        # Pydantic Settings + YAML config loader
│   ├── models.py                        # Request/response Pydantic models
│   ├── database.py                      # Thread-safe in-memory scan/report store
│   ├── static/
│   │   └── index.html                   # Web UI — single-page app (Bootstrap 5 + vanilla JS)
│   ├── routers/
│   │   ├── ui.py                        # GET / — serves the web UI
│   │   ├── discovery.py                 # Repository discovery API (providers, projects, repos)
│   │   ├── health.py                    # GET /api/v1/health
│   │   ├── scan.py                      # POST /api/v1/scan, GET /scan/{id}, POST /rescan-repo
│   │   └── reports.py                   # GET /api/v1/reports, download endpoint
│   ├── services/
│   │   ├── bitbucket_client.py          # Bitbucket Server/DC REST API 1.0 (token auth)
│   │   ├── bitbucket_browser_client.py  # Bitbucket Server/DC via Playwright (SSO auth)
│   │   ├── github_client.py             # GitHub REST API client
│   │   ├── sonarcloud_client.py         # SonarQube/SonarCloud (token auth)
│   │   ├── sonar_browser_client.py      # SonarQube via Playwright (SSO auth)
│   │   ├── sonar_project_index.py       # 6-strategy Sonar key matching index
│   │   ├── scanner.py                   # Orchestrator: repos → keys → metrics → report
│   │   └── report_generator.py          # pandas → CSV/Excel generation
│   └── scheduler/
│       ├── setup.py                     # APScheduler init with FastAPI lifespan
│       └── jobs.py                      # Scheduled job definitions
├── reports/                             # Generated CSV/Excel output directory
├── .sonar_session.json                  # (gitignored) Saved SonarQube SSO session
└── .bitbucket_session.json              # (gitignored) Saved Bitbucket SSO session
```
