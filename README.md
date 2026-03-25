# AI Pipeline Log Analyzer

AI-powered GitHub Actions log analyzer. Automatically fetches the latest failed pipeline run, extracts the relevant failure lines, and sends them to Claude for analysis — returning a structured plain-English report with what failed, root cause, and suggested fix.

---

## How It Works

```
docker run -e GITHUB_TOKEN=xxx -e REPO=owner/repo wyethmontana/pipeline-log-analyzer

→ Fetches latest failed GitHub Actions run via API
→ Downloads and extracts the log zip
→ Filters relevant failure lines
→ Sends to Claude for AI analysis
→ Prints structured report + saves pipeline_report_run<N>.md
```

---

## Quick Start

### Prerequisites

- Docker installed ([Docker Desktop](https://www.docker.com/products/docker-desktop) or [Docker Portable — no admin required](#docker-portable-no-admin-required))
- GitHub Personal Access Token (PAT) with `repo` and `workflow` scopes

### Generate a GitHub PAT

1. Go to `github.com` → profile icon → **Settings**
2. Left menu → **Developer settings** → **Personal access tokens** → **Tokens (classic)**
3. Click **Generate new token (classic)**
4. Name it, select `repo` and `workflow` scopes
5. Click **Generate token** — copy it immediately

### Run

```bash
docker run \
  -e GITHUB_TOKEN=your_pat_here \
  -e REPO=owner/repo-name \
  wyethmontana/pipeline-log-analyzer
```

**Optional — analyze a specific run by ID:**
```bash
docker run \
  -e GITHUB_TOKEN=your_pat_here \
  -e REPO=owner/repo-name \
  -e RUN_ID=12345678 \
  wyethmontana/pipeline-log-analyzer
```

### Save the report locally

```bash
docker run \
  -e GITHUB_TOKEN=your_pat_here \
  -e REPO=owner/repo-name \
  -v $(pwd):/app \
  wyethmontana/pipeline-log-analyzer
```

The report is saved as `pipeline_report_run<N>.md` in the current directory.

---

## Sample Output

```
============================================================
  AI PIPELINE LOG ANALYZER
============================================================
  Repo    : montanawyethm03-max/fuel-forecast-bot-py
  Run     : #2 — Build and Push Docker Image
  Branch  : main
  Status  : FAILURE
  URL     : https://github.com/...
============================================================

--- AI ANALYSIS ---

## What Failed
The "Log in to Docker Hub" step failed during the Build and Push Docker Image pipeline.

## Root Cause
The error `Username and password required` indicates that Docker Hub credentials
were not passed to the docker/login-action — secrets were not yet configured
when this run was triggered.

## Suggested Fix
1. Go to Settings → Secrets and variables → Actions
2. Add DOCKERHUB_USERNAME and DOCKERHUB_TOKEN secrets
3. Re-run the pipeline

## Severity
Critical — pipeline cannot complete any image push without Docker Hub authentication.
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `GITHUB_TOKEN` | Yes | GitHub PAT with `repo` + `workflow` scopes |
| `REPO` | Yes | Target repo e.g. `montanawyethm03-max/fuel-forecast-bot-py` |
| `RUN_ID` | No | Specific run ID — defaults to latest failed run |

---

## Docker Image

Available on Docker Hub: [wyethmontana/pipeline-log-analyzer](https://hub.docker.com/r/wyethmontana/pipeline-log-analyzer)

Automatically rebuilt and republished on every push to `main`.

---

## Docker Portable (No Admin Required)

If you can't install Docker Desktop, use DockerPortable + QEMU — no admin, no WSL2, no Hyper-V.

**Pre-requisites:** Git for Windows and 7-Zip installed. Use Windows PowerShell 64-bit.

**Step 1 — Allow PowerShell scripts**
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

**Step 2 — Download and extract QEMU**
```powershell
Invoke-WebRequest -Uri "https://qemu.weilnetz.de/w64/qemu-w64-setup-20241112.exe" -OutFile "$env:USERPROFILE\Downloads\qemu-setup.exe"
```
Right-click `qemu-setup.exe` → 7-Zip → Extract to `qemu-setup\`, then:
```powershell
Move-Item "$env:USERPROFILE\Downloads\qemu-setup" "$env:USERPROFILE\QEMU"
```

**Step 3 — Add QEMU to PATH**
```powershell
[Environment]::SetEnvironmentVariable("Path", [Environment]::GetEnvironmentVariable("Path","User") + ";$env:USERPROFILE\QEMU", "User")
```
Verify: `qemu-system-x86_64 --version`

**Step 4 — Download DockerPortable**
```powershell
git clone https://github.com/knockshore/dockerportable.git "$env:USERPROFILE\dockerportable"
```

**Step 5 — Start the Docker VM**
```powershell
cd "$env:USERPROFILE\dockerportable"
.\boot.bat
```
Wait for login prompt (30–90 seconds). Leave this window open.

**Step 6 — Connect and run**
```powershell
cd "$env:USERPROFILE\dockerportable"
.\connect.bat
```
Log in as `root` (no password), then run the `docker run` command above.

---

## Built With

- Python 3.11
- GitHub Actions API
- Claude (via Claude Code CLI)
- Docker
