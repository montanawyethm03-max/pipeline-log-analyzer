import os
import sys
import json
import zipfile
import io
import subprocess
import requests

# ── Config ─────────────────────────────────────────────────────────────────────
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
REPO         = os.environ.get("REPO", "")
RUN_ID       = os.environ.get("RUN_ID", "")

HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28"
}

MAX_LOG_CHARS = 8000  # keep prompt manageable


# ── GitHub API helpers ──────────────────────────────────────────────────────────

def get_latest_failed_run(repo):
    url = f"https://api.github.com/repos/{repo}/actions/runs"
    params = {"status": "failure", "per_page": 5}
    r = requests.get(url, headers=HEADERS, params=params, verify=False)
    r.raise_for_status()
    runs = r.json().get("workflow_runs", [])
    if not runs:
        print("No failed runs found.")
        sys.exit(0)
    return runs[0]


def get_run_by_id(repo, run_id):
    url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}"
    r = requests.get(url, headers=HEADERS, verify=False)
    r.raise_for_status()
    return r.json()


def download_logs(repo, run_id):
    url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/logs"
    r = requests.get(url, headers=HEADERS, verify=False, allow_redirects=True)
    if r.status_code == 404:
        print("Logs not available (may have expired or run is still in progress).")
        sys.exit(1)
    r.raise_for_status()
    return r.content


def extract_log_text(zip_bytes):
    """Extract and concatenate all log files from the zip."""
    lines = []
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            for name in z.namelist():
                if name.endswith(".txt"):
                    with z.open(name) as f:
                        content = f.read().decode("utf-8", errors="ignore")
                        lines.append(f"=== {name} ===\n{content}")
    except zipfile.BadZipFile:
        print("Could not read log zip file.")
        sys.exit(1)
    return "\n".join(lines)


def extract_failure_lines(log_text):
    """Keep only lines likely related to the failure."""
    keywords = ["error", "failed", "failure", "exception", "traceback", "exit code", "killed", "fatal", "denied", "not found", "timeout"]
    lines = log_text.splitlines()
    relevant = []
    for i, line in enumerate(lines):
        if any(k in line.lower() for k in keywords):
            # include surrounding context (2 lines before, 2 after)
            start = max(0, i - 2)
            end = min(len(lines), i + 3)
            relevant.extend(lines[start:end])
            relevant.append("---")
    # deduplicate while preserving order
    seen = set()
    deduped = []
    for line in relevant:
        if line not in seen:
            seen.add(line)
            deduped.append(line)
    return "\n".join(deduped)


# ── Claude analysis ─────────────────────────────────────────────────────────────

def analyze_with_claude(log_excerpt, run_info):
    prompt = f"""You are a CI/CD pipeline expert. Analyze this GitHub Actions failure log and provide a structured report.

Pipeline: {run_info.get('name', 'Unknown')}
Repo: {run_info.get('repository', {}).get('full_name', REPO)}
Branch: {run_info.get('head_branch', 'Unknown')}
Triggered by: {run_info.get('event', 'Unknown')}
Run URL: {run_info.get('html_url', '')}

FAILURE LOG:
{log_excerpt}

Provide your analysis in this exact format:

## What Failed
[1-2 sentences describing what step or job failed]

## Root Cause
[1-3 sentences explaining why it failed based on the log]

## Suggested Fix
[Concrete, actionable steps to fix the issue]

## Severity
[Critical / High / Medium / Low — and why]
"""

    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "json"],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return data.get("result", result.stdout)
        else:
            return result.stdout or result.stderr
    except FileNotFoundError:
        return "[Claude Code CLI not found — install Claude Code to enable AI analysis]"
    except subprocess.TimeoutExpired:
        return "[Claude analysis timed out]"
    except Exception as e:
        return f"[Analysis error: {e}]"


# ── Report output ───────────────────────────────────────────────────────────────

def print_report(run_info, analysis, log_excerpt):
    print("\n" + "="*60)
    print("  AI PIPELINE LOG ANALYZER")
    print("="*60)
    print(f"  Repo    : {REPO}")
    print(f"  Run     : #{run_info.get('run_number', '?')} — {run_info.get('name', '')}")
    print(f"  Branch  : {run_info.get('head_branch', '')}")
    print(f"  Status  : {run_info.get('conclusion', '').upper()}")
    print(f"  URL     : {run_info.get('html_url', '')}")
    print("="*60)
    print("\n--- AI ANALYSIS ---\n")
    print(analysis)
    print("\n--- RAW LOG EXCERPT ---\n")
    print(log_excerpt[:2000] + ("..." if len(log_excerpt) > 2000 else ""))
    print("\n" + "="*60)

    # Save report to file
    report_path = f"pipeline_report_run{run_info.get('run_number', 'unknown')}.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"# Pipeline Failure Report — Run #{run_info.get('run_number', '?')}\n\n")
        f.write(f"**Repo:** {REPO}  \n")
        f.write(f"**Branch:** {run_info.get('head_branch', '')}  \n")
        f.write(f"**Status:** {run_info.get('conclusion', '').upper()}  \n")
        f.write(f"**URL:** {run_info.get('html_url', '')}  \n\n")
        f.write(analysis)
        f.write("\n\n## Raw Log Excerpt\n\n```\n")
        f.write(log_excerpt[:3000])
        f.write("\n```\n")
    print(f"  Report saved: {report_path}")


# ── Main ────────────────────────────────────────────────────────────────────────

def main():
    if not GITHUB_TOKEN:
        print("ERROR: GITHUB_TOKEN not set.")
        sys.exit(1)
    if not REPO:
        print("ERROR: REPO not set (e.g. montanawyethm03-max/fuel-forecast-bot-py)")
        sys.exit(1)

    print(f"Fetching pipeline data for: {REPO}")

    if RUN_ID:
        run_info = get_run_by_id(REPO, RUN_ID)
    else:
        print("Looking for latest failed run...")
        run_info = get_latest_failed_run(REPO)

    print(f"Found: Run #{run_info.get('run_number')} — {run_info.get('name')} ({run_info.get('conclusion')})")
    print("Downloading logs...")

    zip_bytes = download_logs(REPO, run_info["id"])
    full_log  = extract_log_text(zip_bytes)
    excerpt   = extract_failure_lines(full_log)

    if not excerpt.strip():
        excerpt = full_log[:MAX_LOG_CHARS]

    excerpt = excerpt[:MAX_LOG_CHARS]

    print("Analyzing with Claude...")
    analysis = analyze_with_claude(excerpt, run_info)

    print_report(run_info, analysis, excerpt)


if __name__ == "__main__":
    main()
